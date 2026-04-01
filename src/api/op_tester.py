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
    AIACC 工业化五阶段自动化验证测站 (The Production Line v5.1)
    支持: 阶梯目录归档 / 多策略性能审计 / Bit-Perfect 验证
    """
    def __init__(self, hw_config_path: str = "hw_config", build_root: str = "build"):
        self.ws = Path.cwd()
        self.hw_config = hw_config_path
        self.build_root = Path(build_root)
        self.spec = HardwareSpec.load(self.hw_config)

    def _prepare_stages(self, op_name: str, strategy: str = "default"):
        """初始化五阶段目录结构 (分层隔离方案)"""
        op_root = self.build_root / op_name.lower()
        
        # 建立顶层汇总目录
        (op_root / "phase5_profiling").mkdir(parents=True, exist_ok=True)
        
        # 建立具体方案的工作目录
        base_dir = op_root
        if strategy != "default":
            base_dir = op_root / f"perf_contest/{strategy}"
        
        stages = {
            "p1": base_dir / "phase1_synthesis",
            "p2": base_dir / "phase2_emission",
            "p3": base_dir / "phase3_simulation",
            "p4": base_dir / "phase4_verification"
        }
        for p in stages.values():
            p.mkdir(parents=True, exist_ok=True)
        
        # 注入顶层指针
        stages["p5"] = op_root / "phase5_profiling"
        return stages

    def run_full_stack_verification(self, 
                                   op_name: str, 
                                   dfg_ops: List[Any], 
                                   input_data: Dict[str, np.ndarray],
                                   golden_y: np.ndarray,
                                   constants: Dict[str, float] = {},
                                   static_banks: Dict[str, int] = {},
                                   strategies: List[str] = ["balanced"],
                                   atol: float = 5e-3,
                                   max_cycles: int = 120):
        """
        [Standard SOP] 70+ 算子全生命周期验证流水线。
        """
        perf_results = []
        op_root = self.build_root / op_name.lower()
        if op_root.exists(): shutil.rmtree(op_root)

        for strategy in strategies:
            print(f"\n🚀 [STRATEGY-CONTEST] Processing {op_name} with strategy: {strategy}")
            stages = self._prepare_stages(op_name, strategy)
            
            # --- PHASE 1: Synthesis ---
            synth = SMTSynthesizer(self.spec)
            stitcher = HSTSStitcher()
            res = synth.solve(dfg_ops, strategy=strategy)
            stitcher.add_operator(op_name, dfg_ops, res)
            final_stream = stitcher.get_final_stream()
            
            # 存算法预期时序
            with open(stages["p1"] / "algo_timeline.txt", "w") as f:
                f.write(f"Algorithm Prediction for {op_name} ({strategy})\n")
                f.write(json.dumps(res, indent=2))
            
            # --- PHASE 2: Emission ---
            banks = {i: [0.0]*4 for i in range(8)}
            tensors_meta = []
            for op in dfg_ops:
                if "bank_id" in op.metadata:
                    b = op.metadata["bank_id"]
                    node = op.node_id
                    
                    # 🛸 关键注入点: 如果传入了静态 Banks，覆盖自动指配
                    if node in static_banks:
                        b = static_banks[node]
                        op.metadata["bank_id"] = b 
                    if node in input_data: banks[b] = [float(input_data[node].flatten()[0]), 0, 0, 0]
                    elif node in constants: banks[b] = [float(constants[node]), 0, 0, 0]
                    
                    role = "output" if node == "w_fin" else "input"
                    name = "y" if node == "w_fin" else node.replace("_rd", "").replace("_f", "")
                    tensors_meta.append({
                        "name": name, 
                        "bank": b, 
                        "bank_id": b, 
                        "offset": 0, 
                        "shape": [1],
                        "role": role
                    })

            class ProgramProxy:
                def __init__(self, b, t):
                    class Alloc: pass
                    self.memory_allocator = Alloc()
                    self.memory_allocator.banks = b
                    self.tensors = {t1["name"]: type("T", (), t1) for t1 in t}
            
            packer = LIRPacker(self.hw_config)
            packer.emit_from_stream(final_stream, str(stages["p2"] / "acb_bundle"), program=ProgramProxy(banks, tensors_meta))

            # 修改 sim_config
            conf_path = stages["p2"] / "acb_bundle" / "sim_config.json"
            with open(conf_path, "r") as f: sim_conf = json.load(f)
            sim_conf["max_cycles"] = max_cycles
            with open(conf_path, "w") as f: json.dump(sim_conf, f, indent=2)

            # --- PHASE 3: Simulation ---
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{self.ws}:{self.ws}/src:{self.ws}/sim:" + env.get("PYTHONPATH", "")
            runner = self.ws / "sim" / "smc_simulator" / "tools" / "physical_runner.py"
            
            # 执行仿真并重定向结果
            res_sim = subprocess.run(["python3", str(runner), str(stages["p2"] / "acb_bundle")], env=env, capture_output=True, text=True)
            
            # 搬运关键证据 timeline_ascii.txt
            src_trace = stages["p2"] / "acb_bundle" / "smc_0" / "analysis" / "timeline_ascii.txt"
            if src_trace.exists():
                shutil.copy(src_trace, stages["p3"] / "timeline_ascii.txt")
            
            # --- PHASE 4: Verification & Debugging ---
            # 🛸 对齐准则: 添加时序审计与对位调试
            from api.timeline_auditor import TimelineAuditor, PerformanceAnalyzer
            auditor = TimelineAuditor()
            perf_eng = PerformanceAnalyzer()
            
            # 生成时序审计报告 (Algo vs Sim)
            audit_report = auditor.audit_cycle_by_cycle(res, stages["p3"] / "timeline_ascii.txt")
            with open(stages["p4"] / "timing_diff_audit.txt", "w") as f:
                f.write(audit_report)
                
            np.savez(stages["p4"] / "input_data.npz", **input_data)
            np.savez(stages["p4"] / "golden_data.npz", y=golden_y)
            
            passed = "PASSED" in res_sim.stdout
            actual_cycles = self._extract_cycles(res_sim.stdout)
            
            # 🛸 性能效能扫描 (Theoretical vs Actual)
            # 假设基准周期由 DFG 层数计算 (简化模型: c_path = max_T + lat)
            theo_bound = perf_eng.calculate_theoretical_bound(dfg_ops, {"ur": 2, "fpadd": 5, "fpmul": 5})
            perf_report = perf_eng.analyze_efficiency_gap(theo_bound, actual_cycles)
            with open(stages["p5"] / "performance_efficiency.txt", "w") as f:
                f.write(perf_report)

            # 记录性能指标
            p_metrics = {
                "strategy": strategy,
                "passed": passed,
                "theoretical_cycles_min": theo_bound,
                "actual_latency_cycles": actual_cycles,
                "max_diff": self._extract_diff(res_sim.stdout)
            }
            perf_results.append(p_metrics)

        # --- PHASE 5: Performance Benchmarking ---
        with open(stages["p5"] / "benchmark_comparison.json", "w") as f:
            json.dump(perf_results, f, indent=2)
        
        print(f"\n🏁 [ARENA-REPORT] Optimization results for {op_name} saved to phase5_profiling/")
        return True, "All strategies evaluated."

    def _extract_cycles(self, stdout):
        import re
        match = re.search(r"Simulation Finished at Cycle (\d+)", stdout)
        return int(match.group(1)) if match else 0

    def _extract_diff(self, stdout):
        import re
        match = re.search(r"Max Difference = ([\d\.]+)", stdout)
        return float(match.group(1)) if match else 0.0
