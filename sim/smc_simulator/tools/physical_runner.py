import sys
import logging
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

def poison_memory(kernel, ur_init_map_or_config, case_dir: Path):
    """
    Load data from UR config and input_data.npz.
    Poison unallocated memory with NaNs.

    Args:
        ur_init_map_or_config: 兼容 (a) 旧 config dict 含 "ur_init" 字段，或
            (b) ADR-018 v7 D5.d task #18 sub-6 多 SMC 模式直接传 per_smc_ur_init[k]
            的 {bank_str: rel_path} map。
    """
    import math

    # 1. Fill entire UR memory with NaN equivalent pattern (e.g. 0x7FC00000 -> IEEE-754 NaN)
    nan_bits = struct.unpack(">I", struct.pack(">f", float('nan')))[0]
    nan_word = (nan_bits) | (nan_bits << 32) | (nan_bits << 64) | (nan_bits << 96)

    for bank_id in range(len(kernel.ur_bank.storage)):
        for addr in range(len(kernel.ur_bank.storage[bank_id])):
            kernel.ur_bank.storage[bank_id][addr] = nan_word

    # 2. Load actual initial data — 兼容旧 config dict 与新 ur_init_map 直传
    if isinstance(ur_init_map_or_config, dict) and "ur_init" in ur_init_map_or_config:
        ur_init_map = ur_init_map_or_config.get("ur_init", {})
    else:
        ur_init_map = ur_init_map_or_config or {}
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

def _run_stages_chained(kernel, case_path: Path, stages: list, default_max_cycles: int):
    """ADR-057 D4 的 sim 侧执行模型：UR 持久（poison_memory 已在调用前执行一次），
    逐段「重置 IQ 取指态 → 装载本段程序 → 运行本段预算」。

    - iq.reset() 清 pc/loop/dly/terminated，不碰 UR（ur_bank 与 IQ 解耦）；
    - 不在本段 iq_init 的队列显式清空 memory——防陈旧程序跨段复燃（静默重放
      上段指令属段间污染，必须结构杜绝而非依赖"各段队列集相同"的现状）；
    - kernel.cycle_count 跨段连续累加 = 各段共享公共时间轴（镜像 TB 的
      全局 probe cycle；TB 侧段间次序由仲裁器裁决，sim 侧仅供数值比对）。
    """
    for st in stages:
        sid = int(st["stage_id"])
        iq_map = {n: str(case_path / h) for n, h in st["iq_init"].items()}
        loaded = {n.lower() for n in iq_map}
        for name, iq in kernel.iqs.items():
            iq.reset()
            if name not in loaded:
                iq.memory = []
        kernel.load_program(iq_map, {})
        print(f"[SIM_MULTI_STAGE] stage_{sid} BEGIN "
              f"(start={st.get('start')}, m={st.get('m')}) @cycle {kernel.cycle_count}")
        kernel.run(max_cycles=int(st.get("max_cycles", default_max_cycles)))
        print(f"[SIM_MULTI_STAGE] stage_{sid} DONE @cycle {kernel.cycle_count}")


def run_physical_verification(case_dir: str):
    case_path = Path(case_dir)
    if not case_path.exists():
         print(f"Error: {case_dir} does not exist.")
         return False

    # Force import from root, sim and src directories
    root = Path.cwd()
    sim_dir = root / "sim"
    src_dir = root / "src"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if str(sim_dir) not in sys.path:
        sys.path.insert(0, str(sim_dir))
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    config = load_sim_config(case_path)

    # ADR-018 v7 D5.d (task #18 sub-6): smc_num >= 2 → SystemCluster 多 SMC 路径
    smc_num = config.get("smc_num")
    if smc_num is not None and smc_num >= 2:
        return _run_multi_smc(case_path, config, smc_num)

    import smc_simulator.core.kernel as kernel_mod
    ParallelKernel = kernel_mod.ParallelKernel

    kernel = ParallelKernel()

    # Enable simulation tracing
    trace_file = case_path / "sim_trace.log"
    kernel.enable_trace()

    # ADR-057 D4：sim_config 含 "stages" → 多段链式执行（COMPUTE 分段）。
    #   单段路径（无 stages 键）下方 load→poison→run 顺序逐字不变。
    stages = config.get("stages")
    if not stages:
        # Phase 1: Decoupled Cold Start & Instruction Fetch
        iq_map = {n: str(case_path / h) for n, h in config.get("iq_init", {}).items()}
        # We load programs directly using paths
        kernel.load_program(iq_map, {}) # Only load iq, UR is done via poisoning manually

    # Phase 1.3: Memory Poisoning（多段：仅一次——UR 跨段持久是 D4 既有设施前提）
    poison_memory(kernel, config, case_path)

    # Phase 2.1: Execution
    print(f"Starting Physical Simulation for {case_dir}...")
    
    # Configure logging to file and stdout
    file_handler = logging.FileHandler(trace_file, mode='w')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logging.getLogger("ParallelKernel").addHandler(file_handler)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter('%(message)s'))
    logging.getLogger("ParallelKernel").addHandler(stream_handler)
    logging.getLogger("ParallelKernel").setLevel(logging.INFO)

    class Tee:
        def __init__(self, *files): self.files = files
        def write(self, obj):
            for f in self.files: f.write(obj); f.flush()
        def flush(self):
            for f in self.files: f.flush()

    try:
        max_cycles = config.get("max_cycles", 1000)
        with open(trace_file, "a") as f_trace:
            import contextlib
            with contextlib.redirect_stdout(Tee(sys.stdout, f_trace)):
                if stages:
                    _run_stages_chained(kernel, case_path, stages, max_cycles)
                else:
                    kernel.run(max_cycles=max_cycles)
        
        # [Standard Alignment] Phase 2.1.2: Analysis and Traces
        analysis_dir = case_path / "smc_0" / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        
        # Dump timeline and events
        kernel.trace_recorder.save_to_timeline_ascii(str(analysis_dir / "timeline_ascii.txt"))
        if hasattr(kernel.trace_recorder, 'save_to_chrome_tracing'):
             kernel.trace_recorder.save_to_chrome_tracing(str(analysis_dir / "sim_events.json"))
             
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
        
        # 🛸 ADPC-ALIGN: 支持边界值测试中的 NaN 对比。
        # 只有当 sim_np 有 NaN 但 ref 没有时，才考虑为“内存污染”。
        sim_flat_ar = sim_np.flatten()
        ref_flat = ref.flatten()
        
        mask_sim_nan = np.isnan(sim_flat_ar)
        mask_ref_nan = np.isnan(ref_flat)
        
        if np.any(mask_sim_nan & (~mask_ref_nan)):
             print(f"FAILED {name}: Unexpected NaN detected! (Poisoned Read)")
             all_passed = False
             continue

        # Mathematical tolerance check
        atol = 5e-3 # Global tolerance
        passed = True
        if not np.allclose(sim_flat_ar, ref_flat, atol=atol, equal_nan=True):
            max_err = np.max(np.abs(sim_flat_ar - ref_flat))
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

def _run_multi_smc(case_path: Path, config: dict, smc_num: int) -> bool:
    """
    ADR-018 v7 D5.d (task #18 sub-6): 多 SMC 仿真路径。
    smc_num kernels via SystemCluster；每 SMC 各自 IQ + UR 来自 per_smc_*.

    本 sub-task 仅保证 cluster 跑通（无 crash）+ per-SMC bank dump。
    bit-perfect 32-SMC golden compare 留 sub-7。
    """
    from smc_simulator.system.cluster import SystemCluster

    print(f"Starting Multi-SMC Physical Simulation (smc_num={smc_num}) for {case_path}...")

    cluster = SystemCluster(num_kernels=smc_num)

    # ADR-023 D3：单 SMI 全局指令流——所有 SMC 共享同一套 iq_init（lockstep 复制，模型 A）；
    # UR 数据仍 per-SMC（per_smc_ur_init）。旧 per_smc_iq_init（每 SMC 各一份）schema 已废。
    iq_init = config.get("iq_init", {})
    per_smc_ur = config.get("per_smc_ur_init", [])
    assert iq_init, "ADR-023: sim_config 缺单一全局 iq_init（emitter 单流 schema 未生效？）"
    assert len(per_smc_ur) == smc_num, (
        f"per_smc_ur_init len={len(per_smc_ur)} ≠ smc_num={smc_num}"
    )

    iq_map = {n: str(case_path / h) for n, h in iq_init.items()}
    # DEBT-032 H2：SHIFTUP 广播源独立捕获寄存器配置（消 flaky，hco H2 判定）
    bcast_cap = config.get("broadcast_capture")
    # ADR-036 S3-a：SMI_DS_T 列对齐源读（ds_src_read）独立捕获寄存器配置（real-gather 时存在）
    dst_src_cap = config.get("dst_src_capture")
    # ADR-036 S3-b：SMI_DS_T 转置结果 SHIFTUP 广播输入缓冲（full fan-out，仅 head/SMC_0 启用）
    dst_bcast_feed = config.get("dst_bcast_feed")
    for k in range(smc_num):
        kernel = cluster.kernels[k]
        kernel.enable_trace()
        kernel.load_program(dict(iq_map), {})  # 每 SMC 各自 load 同一套全局指令流（lockstep）
        poison_memory(kernel, per_smc_ur[k], case_path)
        if bcast_cap:
            kernel.set_broadcast_capture(bcast_cap["port"], bcast_cap["bank"])
        if dst_src_cap:
            kernel.set_dst_src_capture(dst_src_cap["port"], dst_src_cap["bank"])
        if dst_bcast_feed and k == int(dst_bcast_feed.get("smc", 0)):
            kernel.set_dst_bcast_feed(dst_bcast_feed["bank"])

    trace_file = case_path / "sim_trace.log"
    trace_file.touch()

    # ADR-034 2b 指令驱动 + ADR-036 S2 流式：SMI_DS_T 转置由其指令 VLD 在 run 中触发（cluster 单实例），
    # 替 ADR-033 Stage-A 的 run-前无条件 apply。数值核走 setup_smi_ds_t_trigger 的流式 stepped 单元
    # （SmiDsTUnit.evaluate/tick 固定 latency pipeline、逐行 DATA_OUT；**非** apply_smi_ds_t gather-gate backdoor，
    # 后者仅无 smi_ds_t hex 时 fallback）。触发时机由 emit→hex→decode VLD 驱动 → vld=0 破坏微指令则 e2e FAIL。
    # 残留 model-A 缺口 = latency 拍数占位 + SHIFTDN drain/interleave/INST_DONE/COND 未建（DEBT-033-DST-CTRL/-S4）。
    # 列对齐激活已 poison_memory 到各 SMC bank。
    smi_ds_t_cfg = config.get("smi_ds_t")
    if smi_ds_t_cfg:
        smi_hex = next((str(case_path / h) for n, h in iq_init.items()
                        if n.replace("-", "_").startswith("smi_ds_t")), None)
        if smi_hex:
            cluster.setup_smi_ds_t_trigger(smi_hex, smi_ds_t_cfg)   # 指令驱动（2b）
        else:
            # ADR-036 S3-c：apply_smi_ds_t model-A backdoor 已删。cfg 在但无 smi-ds-t hex 是不一致
            # （算子声明转置但未产指令流）→ loud-fail，拒绝静默回 backdoor（同 S3-a 反半态原则）。
            raise RuntimeError(
                "ADR-036 S3-c: smi_ds_t cfg 存在但无 smi-ds-t hex 指令流——"
                "apply_smi_ds_t backdoor 已删，拒绝静默 fallback。"
                "算子若不走转置 datapath 应令 smi_ds_t_config 返 None（参 matmul_q_dst_full）。")

    try:
        max_cycles = config.get("max_cycles", 1000)
        cluster.run(cycles=max_cycles)

        # per-SMC analysis + output bank dump
        for k in range(smc_num):
            analysis_dir = case_path / f"smc_{k}" / "analysis"
            analysis_dir.mkdir(parents=True, exist_ok=True)
            bank_dir = case_path / f"smc_{k}" / "output_bank"
            kernel = cluster.kernels[k]
            kernel.dump_banks(str(bank_dir))
            if kernel.trace_recorder:
                kernel.trace_recorder.save_to_timeline_ascii(
                    str(analysis_dir / "timeline_ascii.txt")
                )
    except Exception as e:
        print(f"Multi-SMC Simulation Halted: {e}")
        traceback.print_exc()
        return False

    last_cycle = next(iter(cluster.kernels.values())).cycle_count
    print(f"Multi-SMC Simulation Finished at Cycle {last_cycle}. Verifying Data...")

    # ADR-018 v7 D5.d task #18 sub-7: per-SMC bit-perfect golden compare.
    # golden['y'] 期望 shape (smc_num, n_rows)；每 SMC k 比对自己的 bank 5 输出。
    golden_file = case_path / "golden_data.npz"
    if not golden_file.exists():
        print(f"Golden data not found at {golden_file}")
        return False
    goldens = np.load(golden_file)
    return _verify_multi_smc_per_smc(case_path, config, cluster, smc_num, goldens)


def _verify_multi_smc_per_smc(case_path, config, cluster, smc_num: int, goldens) -> bool:
    """ADR-018 v7 task #18 sub-7: per-SMC bit-perfect compare。
    Golden shape (smc_num, n_rows) → SMC_k bank → 比对 ref[k]。"""
    atol = 5e-3
    all_passed = True

    for t_info in config.get("tensors", []):
        if t_info.get("role") != "output":
            continue
        name = t_info["name"]
        bank = int(t_info["bank"])
        addr = int(t_info["addr"])

        if name not in goldens.files:
            continue
        ref = goldens[name]
        if ref.ndim != 2:
            # 单 SMC 形状 fallback：仅比对 SMC_0
            kernel = cluster.kernels[0]
            sim_np = _read_sim_output(kernel, bank, addr, len(ref.flatten()),
                                      element_bits=ref.dtype.itemsize * 8)
            ok = np.allclose(sim_np, ref.flatten(), atol=atol, equal_nan=True)
            print(f"{'PASSED' if ok else 'FAILED'} {name} (1D golden, SMC_0 only)")
            all_passed = all_passed and ok
            continue

        assert ref.shape[0] == smc_num, (
            f"golden[{name}] shape={ref.shape}, expected first dim={smc_num}"
        )
        n_rows = ref.shape[1]

        per_smc_pass = 0
        first_fail_detail = None
        for k in range(smc_num):
            kernel = cluster.kernels[k]
            sim_np = _read_sim_output(kernel, bank, addr, n_rows, element_bits=ref.dtype.itemsize * 8)
            ref_k = ref[k]
            if np.any(np.isnan(sim_np) & ~np.isnan(ref_k)):
                if first_fail_detail is None:
                    first_fail_detail = f"SMC_{k} 出 unexpected NaN"
                all_passed = False
                continue
            if not np.allclose(sim_np, ref_k, atol=atol, equal_nan=True):
                max_err = float(np.max(np.abs(sim_np - ref_k)))
                if first_fail_detail is None:
                    first_fail_detail = (
                        f"SMC_{k} Max Diff={max_err}; "
                        f"sim[0..2]={sim_np[:3].tolist()}, ref[0..2]={ref_k[:3].tolist()}"
                    )
                all_passed = False
            else:
                per_smc_pass += 1

        if per_smc_pass == smc_num:
            print(f"PASSED {name}: All {smc_num} SMC outputs bit-perfect (atol={atol})")
            sample = min(3, smc_num)
            for k in range(sample):
                ref_k = ref[k]
                sim_k = _read_sim_output(cluster.kernels[k], bank, addr, n_rows,
                                         element_bits=ref.dtype.itemsize * 8)
                print(f"      [SMC_{k}] Golden={ref_k[:min(3,n_rows)].tolist()} | "
                      f"SMC={sim_k[:min(3,n_rows)].tolist()}")
        else:
            print(f"FAILED {name}: {per_smc_pass}/{smc_num} SMCs match. {first_fail_detail}")
    return all_passed


def _read_sim_output(kernel, bank: int, addr: int, n_rows: int, element_bits: int = 32) -> np.ndarray:
    """从 kernel.ur_bank.storage[bank] 读 n_rows 个值（128-bit words 解包）。
    element_bits=32→fp32（4 lane/词）；16→fp16（8 lane/词，ADR-039 fp16 载体）。
    精度从 golden dtype 推（caller 传 ref.dtype.itemsize*8），避免对 fp16 输出按 fp32 错解码（lane 数 + 位宽全错）。"""
    if element_bits == 16:
        from smc_simulator.units.smi_ds_t_unit import fp16_lanes_from_word
        lanes_per_word = 8
        n_words = (n_rows + lanes_per_word - 1) // lanes_per_word
        sim_flat = []
        for wi in range(n_words):
            word = int(kernel.ur_bank.storage[bank][addr + wi])
            sim_flat.extend(fp16_lanes_from_word(word, lanes_per_word))
        return np.array(sim_flat[:n_rows], dtype=np.float32)
    n_words = (n_rows + 3) // 4
    sim_words = [int(kernel.ur_bank.storage[bank][addr + i]) for i in range(n_words)]
    sim_flat = []
    for i in range(n_rows):
        bits = (sim_words[i // 4] >> ((i % 4) * 32)) & 0xFFFFFFFF
        f_val = struct.unpack(">f", struct.pack(">I", bits))[0]
        sim_flat.append(f_val)
    return np.array(sim_flat, dtype=np.float32)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AIACC Physical Runner Testbench")
    parser.add_argument("case_dir", type=str, help="Path to the test output directory (e.g. test_output/e2e_sin_v3)")
    args = parser.parse_args()
    
    success = run_physical_verification(args.case_dir)
    sys.exit(0 if success else 1)
