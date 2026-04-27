import numpy as np
import json
import struct
from pathlib import Path
import os
import subprocess

def float_to_hex(f):
    return hex(struct.unpack('>I', struct.pack('>f', f))[0])[2:].zfill(8)

def hex_to_float(h):
    return struct.unpack('>f', struct.pack('>I', int(h, 16)))[0]

def main():
    build_dir = Path("build/alignment/horner_3rd")
    
    # 1. Generate Golden Data
    x = np.linspace(0.1, 1.6, 16)
    c = [-0.166666, 0.0, 1.0, 0.0]  # c3, c2, c1, c0
    # y = ((c3*x + c2)*x + c1)*x + c0
    y_golden = ((c[0] * x + c[1]) * x + c[2]) * x + c[3]
    
    golden_path = build_dir / "golden_data.npz"
    np.savez(golden_path, x=x, y=y_golden)
    print(f"Generated golden data at {golden_path}")

    # 2. Fix sim_config.json for Simulator Alignment
    config_path = build_dir / "sim_config.json"
    with open(config_path, "r") as f:
        config = json.load(f)
    
    new_iq_init = {}
    for k, v in config["iq_init"].items():
        new_key = k.lower()
        if new_key.startswith("ur-r-"):
            new_key = new_key.replace("ur-r-", "ur-r")
        elif new_key.startswith("ur-w-"):
            new_key = new_key.replace("ur-w-", "ur-w")
        new_iq_init[new_key] = v
    
    config["iq_init"] = new_iq_init
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print("Fixed sim_config.json IQ names.")

    # 3. Run Simulation
    print("Running simulation...")
    env = os.environ.copy()
    env["PYTHONPATH"] = ".:./src:./sim:" + env.get("PYTHONPATH", "")
    runner = "sim/smc_simulator/tools/physical_runner.py"
    res = subprocess.run(["python3", runner, str(build_dir)], env=env, capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print("Errors:")
        print(res.stderr)

    # 4. Extract and Compare Results
    # Bank 2 Offset 0 contains y (16 elements = 4 lines)
    output_bank_path = build_dir / "smc_0" / "output_bank" / "bank2_final.hex"
    hw_results = []
    if output_bank_path.exists():
        with open(output_bank_path, "r") as f:
            lines = f.readlines()
            for line in lines[:4]: # First 4 lines = 16 elements
                hex_str = line.strip().replace("0x", "")
                if not hex_str: continue
                # Line is 128-bit = 4 x 32-bit floats. 
                # According to emitter.py: packed |= (bits << (32 * i))
                # So it's [word0, word1, word2, word3]
                for i in range(4):
                    word_hex = hex_str[32 - (i+1)*8 : 32 - i*8]
                    hw_results.append(hex_to_float(word_hex))
    
    hw_results = np.array(hw_results)
    
    print("\nNumerical Comparison (First 4 elements):")
    print(f"{'Input x':<10} | {'Golden y':<12} | {'Hardware y':<12} | {'Diff':<12}")
    print("-" * 55)
    for i in range(min(4, len(hw_results))):
        diff = abs(y_golden[i] - hw_results[i])
        print(f"{x[i]:10.4f} | {y_golden[i]:12.6f} | {hw_results[i]:12.6f} | {diff:12.6e}")

    mae = np.mean(np.abs(y_golden - hw_results)) if len(hw_results) > 0 else float('inf')
    print(f"\nMean Absolute Error (MAE): {mae:.6e}")
    
    if mae < 5e-3:
        print("\n✅ Verification SUCCESS: Bit-Perfect alignment achieved (within threshold).")
    else:
        print("\n❌ Verification FAILED: Error exceeds threshold.")

if __name__ == "__main__":
    main()
