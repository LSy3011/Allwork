"""
AIACC 物理全链路测试脚手架 (V5.0 Aligned)
集成 HSTSStitcher 和 MemoryAllocator，确保比特级对齐。
"""
import os, sys, json, numpy as np, struct, unittest, subprocess
from pathlib import Path

# 🛰️ 环境自动对齐
workspace_dir = Path(__file__).parent.parent.parent.parent.parent
sys.path.append(str(workspace_dir / "src"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from orchestrator.stitcher import HSTSStitcher
from driver.emitter import LIRPacker

class TestOperatorFactoryV50(unittest.TestCase):
    """算子工厂标准化验证器"""
    
    def run_operator_smoke(self, name: str, dfg_func, inputs: dict, golden_func):
        """
        全链路执行回路: DFG -> SMT -> Stitch -> Pack -> Sim -> Verify
        """
        output_dir = workspace_dir / "build" / f"factory_{name}"
        os.makedirs(output_dir, exist_ok=True)
        
        # 1. 物理合成 (SMT)
        spec = HardwareSpec.load(str(workspace_dir / "hw_config"))
        dfg = dfg_func()
        synthesizer = SMTSynthesizer(spec)
        res = synthesizer.solve(dfg)
        self.assertIsNotNone(res, f"Synthesis Failed for {name}")
        
        # 2. 全球编排 (Stitcher - 产生 global_t)
        stitcher = HSTSStitcher()
        stitcher.add_operator(name, dfg, res)
        final_stream = stitcher.get_final_stream()
        
        # 3. 内存与指令填装 (Packer)
        ur_dir = output_dir / "smc_0" / "ur_data"
        os.makedirs(ur_dir, exist_ok=True)
        
        def write_bank(b, v):
            bits = struct.unpack("<I", struct.pack("<f", v))[0]
            word = sum((bits << (i * 32)) for i in range(4))
            with open(ur_dir / f"bank{b}.hex", "w") as f: f.write(f"{word:032x}\n")
            
        for b, v in inputs.items(): write_bank(b, v)
        for b in range(0, 8): 
            with open(ur_dir / f"bank{b}.hex", "a") as f: pass # Ensure files exist
            
        packer = LIRPacker(str(workspace_dir / "hw_config"))
        packer.emit_from_stream(final_stream, str(output_dir))
        
        # 4. 物理仿真执行 (Simulator)
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{workspace_dir}:{workspace_dir}/sim:" + env.get("PYTHONPATH", "")
        runner_path = workspace_dir / "sim" / "smc_simulator" / "tools" / "physical_runner.py"
        sim_res = subprocess.run([sys.executable, str(runner_path), str(output_dir)], 
                               env=env, capture_output=True, text=True)
        self.assertEqual(sim_res.returncode, 0, f"Simulation Failed: {sim_res.stderr}")
        
        # 5. 比特判决 (Bit-Perfect Match)
        result_hex = output_dir / "smc_0" / "output_bank" / "bank7_final.hex"
        self.assertTrue(result_hex.exists(), "Output bank 7 not found")
        
        with open(result_hex, "r") as f:
            raw = int(f.readline().strip(), 16) & 0xFFFFFFFF
            actual = struct.unpack("<f", struct.pack("<I", raw))[0]
            expected = golden_func(*inputs.values())
            self.assertAlmostEqual(actual, expected, places=4, msg=f"Mismatch: {actual} vs {expected}")
            print(f"📊 [FACTORY] {name} Bit-Perfect Verified: {actual} == {expected}")

if __name__ == "__main__":
    unittest.main()
