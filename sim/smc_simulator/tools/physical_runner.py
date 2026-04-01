import sys
import json
import struct
import numpy as np
import argparse
import traceback
from pathlib import Path
import csv

def load_sim_config(case_dir: Path):
    with open(case_dir / "sim_config.json") as f:
        return json.load(f)

def _float32_to_ur128(float_array: np.ndarray) -> dict:
    # Converts float32 array to 128-bit words (4 floats per word)
    flat_data = float_array.flatten()
    words = {}
    for i in range(0, len(flat_data), 4):
        chunk = flat_data[i:i+4]
        word_val = 0
        for j, val in enumerate(chunk):
            bits = struct.unpack(">I", struct.pack(">f", val))[0]
            word_val |= (bits << (j * 32))
        words[i // 4] = word_val
    return words

def poison_memory(kernel, config: dict, case_dir: Path):
    """
    Load data from UR config and input_data.npz.
    Poison unallocated memory with NaNs.
    """
    import math
    
    # 1. Fill entire UR memory with NaN equivalent pattern (e.g. 0x7FC00000 -> IEEE-754 NaN)
    nan_bits = struct.unpack(">I", struct.pack(">f", float('nan')))[0]
    nan_word = (nan_bits) | (nan_bits << 32) | (nan_bits << 64) | (nan_bits << 96)
    
    for bank_id in range(len(kernel.ur_bank.storage)):
        for addr in range(len(kernel.ur_bank.storage[bank_id])):
            kernel.ur_bank.storage[bank_id][addr] = nan_word

    # 2. Load actual initial data
    ur_init_map = config.get("ur_init", {})
    for bank_str, rel_path in ur_init_map.items():
        bank_id = int(bank_str)
        file_path = case_dir / rel_path
        if file_path.exists():
            with open(file_path, "r") as f:
                lines = f.read().splitlines()
                for i, line in enumerate(lines):
                    if line.strip():
                        if i >= len(kernel.ur_bank.storage[bank_id]):
                            break
                        val = int(line.strip(), 16)
                        kernel.ur_bank.storage[bank_id][i] = val

def run_physical_verification(case_dir: str):
    case_path = Path(case_dir)
    if not case_path.exists():
         print(f"Error: {case_dir} does not exist.")
         return False

    # Force import from root
    root = Path.cwd()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    import smc_simulator.core.kernel as kernel_mod
    ParallelKernel = kernel_mod.ParallelKernel

    config = load_sim_config(case_path)
    kernel = ParallelKernel()

    # Enable simulation tracing
    trace_file = case_path / "sim_trace.log"
    kernel.enable_trace()

    # Phase 1: Decoupled Cold Start & Instruction Fetch
    iq_map = {n: str(case_path / h) for n, h in config.get("iq_init", {}).items()}
    # We load programs directly using paths
    kernel.load_program(iq_map, {}) # Only load iq, UR is done via poisoning manually

    # Phase 1.3: Memory Poisoning
    poison_memory(kernel, config, case_path)

    # Phase 2.1: Execution
    print(f"Starting Physical Simulation for {case_dir}...")
    try:
        max_cycles = config.get("max_cycles", 1000)
        kernel.run(max_cycles=max_cycles)
        
        # [Standard Alignment] Phase 2.1.2: Analysis and Traces
        analysis_dir = case_path / "smc_0" / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        
        # Dump timeline and events
        kernel.trace_recorder.save_to_timeline_ascii(str(analysis_dir / "timeline_ascii.txt"))
        if hasattr(kernel.trace_recorder, 'save_to_chrome_tracing'):
             kernel.trace_recorder.save_to_chrome_tracing(str(analysis_dir / "sim_events.json"))
        
        # Save main simulation trace
        with open(case_path / "sim_trace.log", "w") as f_log:
             f_log.write(f"Simulation completed at cycle {kernel.cycle_count}\n")
             
        # [Standard Alignment] Phase 2.1.3: Output Bank State
        output_bank_dir = case_path / "smc_0" / "output_bank"
        kernel.dump_banks(str(output_bank_dir))
        
    except Exception as e:
        print(f"Simulation Halted: {e}")
        traceback.print_exc()
        return False
        
    print(f"Simulation Finished at Cycle {kernel.cycle_count}. Verifying Data...")

    # Phase 2.2 & 2.3: Fetching results and Golden Check
    golden_file = case_path / "golden_data.npz"
    if not golden_file.exists():
         print(f"Golden data not found at {golden_file}")
         return False
         
    goldens = np.load(golden_file)
    all_passed = True
    
    for t_info in config.get("tensors", []):
        if t_info.get("role") != "output":
            continue
            
        name = t_info["name"]
        bank = int(t_info["bank"])
        addr = int(t_info["addr"])
        shape = tuple(t_info["shape"])
        dtype = t_info.get("dtype", "float32")
        
        size = int(np.prod(shape))
        if name not in goldens:
            continue
            
        ref = goldens[name]
        
        sim_words = []
        for i in range((size + 3) // 4):
            val = kernel.ur_bank.storage[bank][addr + i]
            sim_words.append(val)
            
        sim_flat = []
        for i in range(size):
            bits = (sim_words[i // 4] >> ((i % 4) * 32)) & 0xFFFFFFFF
            # unpack handles NaNs safely
            f_val = struct.unpack(">f", struct.pack(">I", bits))[0]
            sim_flat.append(f_val)
            
        sim_np = np.array(sim_flat, dtype=np.float32).reshape(shape)
        
        # Checking for NaNs which indicate reading uninitialized poisoned memory
        if np.isnan(sim_np).any():
             print(f"FAILED {name}: Output contains NaN values! Detected Memory Corruption / Poisoned Read.")
             all_passed = False
             continue

        # Mathematical tolerance check
        atol = 5e-3 # Global tolerance for taylor expansion
        passed = True
        if not np.allclose(sim_np, ref, atol=atol):
            max_err = np.max(np.abs(sim_np - ref))
            print(f"FAILED {name}: Max Difference = {max_err}")
            all_passed = False
            passed = False
        else:
            print(f"PASSED {name}: Verified successfully against Golden Data.")
            
        print(f"      [Sample View] Golden Data Vs. SMC Output (first 5 elements):")
        sample_len = min(5, size)
        sim_sample = sim_np.flatten()[:sample_len]
        ref_sample = ref.flatten()[:sample_len]
        for idx in range(sample_len):
            print(f"       [{idx}]: Golden={ref_sample[idx]:.5f}, SMC={sim_sample[idx]:.5f} | Diff={abs(ref_sample[idx] - sim_sample[idx]):.6f}")
        print("-" * 50)
        
        # 核心追加：导出 128-bit HEX 按行文件，用于跟硬件 RTL / FPGA 强行拉平比对 (Hardware Diff)
        dump_file = case_path / f"ur_bank_{bank}_out_{name}.hex"
        with open(dump_file, "w") as f_hex:
            for w_val in sim_words:
                f_hex.write(f"{w_val:032x}\n")
        print(f"      [Hardware Diff] Exported RTL-compatible SMC output to: {dump_file.name}")
            
    return all_passed

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AIACC Physical Runner Testbench")
    parser.add_argument("case_dir", type=str, help="Path to the test output directory (e.g. test_output/e2e_sin_v3)")
    args = parser.parse_args()
    
    success = run_physical_verification(args.case_dir)
    sys.exit(0 if success else 1)
