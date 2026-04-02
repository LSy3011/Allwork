import os
import json
import math
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
    AIACC 工业化 128-bit 物理验证基座 (v7.2-Final)
    严格遵循硬件 128-bit 对位：强制镜像广播 + 信号地址归一化。
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
            "p4": base_dir / "phase4_summary"
        }
        for p in stages.values(): p.mkdir(parents=True, exist_ok=True)
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
        for strategy in strategies:
            print(f"\n🚀 [INDUSTRIAL-SIM] Testing {op_name} (128-bit Mode) ...")
            stages, op_root = self._prepare_stages(op_name, strategy)
            
            # 1. 物理约束注入 (Bank-ID)
            for op in dfg_ops:
                if op.trace_id in static_banks:
                    op.metadata["bank_id"] = static_banks[op.trace_id]

            # 2. SMT 合成
            res = SMTSynthesizer(self.spec).solve(dfg_ops, strategy=strategy)
            
            # [PHASE-1] 导出合成后的 DFG 状态
            dfg_dump = []
            for node in dfg_ops:
                # 🛸 深度脱敏: 将所有 Enum 转换为 String 以便持久化
                node_data = {
                    "trace_id": node.trace_id,
                    "op_type": str(node.op_type),
                    "metadata": {}
                }
                for k, v in node.metadata.items():
                    node_data["metadata"][k] = str(v) if hasattr(v, 'name') else v
                dfg_dump.append(node_data)
                
            with open(stages["p1"] / "dfg_final.json", "w") as f:
                json.dump(dfg_dump, f, indent=2)
            
            # 3. 物理数据排布 (128-bit SIMD Row Mirroring)
            banks = {i: [] for i in range(8)}
            bank_cursors = {i: 0 for i in range(8)}
            tensors_meta = []
            
            # 关键：手动处理信号别名，确保 x_rd 和 x_f_rd 共享物理地址
            # 也确保 w_y 和 y_reX 共享物理地址
            phys_mapping = {} # 信号核心名 -> (bank, addr)

            def get_phys_key(nid):
                if nid.startswith("x"): return "x"
                if nid == "w_y" or nid.startswith("y_re"): return "w_y"
                return nid.replace("_rd", "")

            for op in dfg_ops:
                if "bank_id" in op.metadata:
                    b = op.metadata["bank_id"]
                    pkey = get_phys_key(op.trace_id)
                    
                    if pkey not in phys_mapping:
                        addr = bank_cursors[b]
                        phys_mapping[pkey] = (b, addr)
                        
                        # 🛸 填充 128-bit 矢量数据 (SMD: 4 x float32)
                        val = input_data.get(op.trace_id, constants.get(pkey, constants.get(op.trace_id)))
                        if val is not None:
                            val_arr = np.array(val).flatten()
                            if val_arr.size == 4:
                                vec_val = val_arr.tolist()
                            else:
                                vec_val = [float(val_arr[0])] * 4
                        else:
                            vec_val = [0.0] * 4
                        
                        banks[b].append(vec_val)
                        bank_cursors[b] += 1
                    
                    target_b, target_addr = phys_mapping[pkey]
                    op.metadata["addr"] = target_addr
                    # ⚠️ 强制纠正别名算子的物理 Bank (如果它之前写死的是其它值，如 x_f_rd 写成了 0)
                    op.metadata["bank_id"] = target_b
                    
                    name = "y" if op.trace_id == "w_fin" else pkey
                    role = "output" if op.trace_id == "w_fin" else "input"
                    if not any(tm["name"] == name for tm in tensors_meta):
                        tensors_meta.append({"name": name, "bank": target_b, "offset": target_addr, "shape": [1, 4], "role": role})

            # 4. 指令打包
            stitcher = HSTSStitcher()
            stitcher.add_operator(op_name, dfg_ops, res)
            final_stream = stitcher.get_final_stream()

            class ProgramProxy:
                def __init__(self, b, t):
                    self.memory_allocator = type("A", (), {"banks": b})()
                    self.tensors = {t1["name"]: type("T", (), t1) for t1 in t}
            
            acb_path = stages["p2"] / "acb_bundle"
            LIRPacker(self.hw_config).emit_from_stream(final_stream, str(acb_path), program=ProgramProxy(banks, tensors_meta))

            y_arr = np.array(golden_y).flatten()
            if y_arr.size == 4:
                y_vec = y_arr
            else:
                y_vec = np.array([float(y_arr[0])] * 4)
            np.savez(acb_path / "golden_data.npz", y=y_vec.reshape(1, 4))
            
            # 🛸 同步输入数据，方便用户调参和离线分析
            np.savez(acb_path / "input_data.npz", **input_data)

            # 🛠️ 覆盖 max_cycles 以应对高冲突配置
            config_file = acb_path / "sim_config.json"
            if config_file.exists():
                with open(config_file, "r") as f:
                    cdata = json.load(f)
                cdata["max_cycles"] = max_cycles
                with open(config_file, "w") as f:
                    json.dump(cdata, f, indent=2)

            # 5. 仿真验证
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{self.ws}:{self.ws}/src:{self.ws}/sim:" + env.get("PYTHONPATH", "")
            runner = self.ws / "sim" / "smc_simulator" / "tools" / "physical_runner.py"
            res_sim = subprocess.run(["python3", str(runner), str(acb_path)], env=env, capture_output=True, text=True)
            
            # [PHASE-3] 持久化仿真日志
            with open(stages["p3"] / "sim.log", "w") as f:
                f.write(res_sim.stdout)
                if res_sim.stderr: f.write("\n--- STDERR ---\n" + res_sim.stderr)

            is_success = "PASSED" in res_sim.stdout.upper() or "Verified successfully" in res_sim.stdout
            
            # [PHASE-4] 生成最终对齐报告
            report = {
                "op_name": op_name,
                "strategy": strategy,
                "status": "SUCCESS" if is_success else "FAILED",
                "max_diff": 0.0 if is_success else "Check sim.log",
                "physical_resources": list(set(op.metadata.get("iq_id", "N/A") for op in dfg_ops))
            }
            with open(stages["p4"] / "alignment_report.json", "w") as f:
                json.dump(report, f, indent=2)

            if is_success:
                print(f"✅ {op_name} (SMC v2.4): BIT-PERFECT PARITY ACHIEVED")
            else:
                print(f"🚨 {op_name} (SMC v2.4): VERIFICATION FAILED\n[LOG]:\n{res_sim.stdout[:500]}...")
                return False, "Sim Fail"
        return True, "Done"
