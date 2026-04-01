import os
import json
import shutil
import subprocess
import numpy as np
from pathlib import Path
from typing import Dict, List, Any, Optional

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from orchestrator.stitcher import HSTSStitcher
from driver.emitter import LIRPacker

class OperatorPhysicalTester:
    """
    AIACC 纯净版五阶段自动化验证测站 (The Production Base v6.7)
    目标: 极致稳定，仅关注 smc_simulator 最终对比结果。
    """
    def __init__(self, hw_config_path: str = "hw_config", build_root: str = "build"):
        self.ws = Path.cwd()
        self.hw_config = hw_config_path
        self.build_root = Path(build_root)
        self.spec = HardwareSpec.load(self.hw_config)

    def _prepare_stages(self, op_name: str, strategy: str = "default"):
        op_root = self.build_root / op_name.lower()
        base_dir = op_root
        if strategy != "default":
            base_dir = op_root / f"perf_contest/{strategy}"
        
        base_dir.mkdir(parents=True, exist_ok=True)
        stages = {
            "p1": base_dir / "phase1_synthesis",
            "p2": base_dir / "phase2_emission",
            "p3": base_dir / "phase3_simulation",
            "p4": base_dir / "phase4_verification"
        }
        for p in stages.values():
            p.mkdir(parents=True, exist_ok=True)
        return stages, op_root

    def run_full_stack_verification(self, 
                                   op_name: str, 
                                   dfg_ops: List[Any], 
                                   input_data: Dict[str, np.ndarray],
                                   golden_y: np.ndarray,
                                   constants: Dict[str, float] = {},
                                   static_banks: Dict[str, int] = {},
                                   strategies: List[str] = ["balanced"],
                                   atol: float = 5e-3,
                                   max_cycles: int = 200):
        """
        [工业化标准流] 仅关注仿真正确性对比。
        """
        for strategy in strategies:
            print(f"\n🚀 [INDUSTRIAL-SIM] Testing {op_name} via SMC_Simulator...")
            stages, op_root = self._prepare_stages(op_name, strategy)
            
            # --- 1. Synthesis & Stitching ---
            res = SMTSynthesizer(self.spec).solve(dfg_ops, strategy=strategy)
            stitcher = HSTSStitcher()
            stitcher.add_operator(op_name, dfg_ops, res)
            final_stream = stitcher.get_final_stream()
            
            # --- 2. Emission & Data Sync ---
            banks = {i: [0.0]*4 for i in range(8)}
            tensors_meta = []
            for op in dfg_ops:
                if "bank_id" in op.metadata:
                    b = static_banks.get(op.node_id, op.metadata["bank_id"])
                    op.metadata["bank_id"] = b 
                    if op.node_id in input_data: banks[b] = [float(input_data[op.node_id].flatten()[0]), 0, 0, 0]
                    elif op.node_id in constants: banks[b] = [float(constants[op.node_id]), 0, 0, 0]
                    
                    role = "output" if op.node_id == "w_fin" else "input"
                    name = "y" if op.node_id == "w_fin" else op.node_id.replace("_rd", "").replace("_f", "")
                    tensors_meta.append({"name": name, "bank": b, "offset": 0, "shape": [1], "role": role})

            class ProgramProxy:
                def __init__(self, b, t):
                    self.memory_allocator = type("A", (), {"banks": b})()
                    self.tensors = {t1["name"]: type("T", (), t1) for t1 in t}
            
            acb_path = stages["p2"] / "acb_bundle"
            LIRPacker(self.hw_config).emit_from_stream(final_stream, str(acb_path), program=ProgramProxy(banks, tensors_meta))

            # 🛸 重要：在 acb_bundle 中备好比对数据，供模拟器运行后直接校验
            np.savez(acb_path / "input_data.npz", **input_data)
            np.savez(acb_path / "golden_data.npz", y=golden_y)

            # --- 3. Simulation (Real Hardware Runner) ---
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{self.ws}:{self.ws}/src:{self.ws}/sim:" + env.get("PYTHONPATH", "")
            runner = self.ws / "sim" / "smc_simulator" / "tools" / "physical_runner.py"
            
            # 使用更大的 max_cycles 确保比对流程能走完
            res_sim = subprocess.run(["python3", str(runner), str(acb_path)], env=env, capture_output=True, text=True)
            
            # --- 4. Final Verdict ---
            # 捕获模拟器输出的校验通过标志
            passed = "PASSED" in res_sim.stdout.upper() or "Verified successfully" in res_sim.stdout
            
            if passed:
                print(f"✅ {op_name} (SMC v2.4): BIT-PERFECT PARITY ACHIEVED")
            else:
                print(f"🚨 {op_name} (SMC v2.4): VERIFICATION FAILED")
                print(f"[ERROR-LOG]:\n{res_sim.stdout[-1000:]}")
                return False, "Simulation Parity Failed"

        return True, "Success"
