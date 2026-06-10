import os, sys, json, subprocess
import numpy as np
from typing import List, Dict, Any
from api.hw_spec import HardwareSpec
from synthesis.ucode_solver import UCodeSynthesizer
from driver.assembler import PhysicalAssembler
from driver.emitter import LIRPacker
from collections import namedtuple

TensorObj = namedtuple("TensorObj", ["bank", "offset", "shape", "role"])

class PipelineRunner:
    def __init__(self, hw_config_name: str = "hw_config", show_review: bool = True, show_model: bool = False):
        self.hw_config_name = hw_config_name
        self.spec = HardwareSpec.load(hw_config_name)
        self.show_review = show_review
        self.show_model = show_model
        self.output_root = "build/verification_v2"
        os.makedirs(self.output_root, exist_ok=True)

    def run_full_stack(self, operator: Any, input_data: Dict[str, np.ndarray], op_name: str = None):
        if self.show_review:
            print(f"\n>>> Starting Pipeline Runner...")
        
        # 🛸 [V5.4 FIX] 注入硬件 Spec 和数据类型到模拟内存层
        from verification_v2.infra.mock_memory import MockProgram

        vector_width_bits = self.spec.params.PARAM_SMC_VECTOR_WIDTH
        element_bits = operator.element_bits
        vector_lanes = vector_width_bits // element_bits

        # ADR-018 v7 D5.d task #18 sub-7: parallel_dim 算子需 per-SMC programs
        # + 多 SMC 数据 reshape（broadcast 1D / partitioned 2D shape=(smc_num,n_rows)）。
        smc_num_eff = self._compute_smc_num(operator)
        is_multi_smc = smc_num_eff is not None and smc_num_eff >= 2
        if is_multi_smc:
            first_val = next(iter(input_data.values()))
            n_rows_actual = first_val.shape[-1]
            input_data = operator.prepare_input(n_rows_actual, smc_num=smc_num_eff)
            programs = [MockProgram(spec=self.spec, dtype=operator.dtype)
                        for _ in range(smc_num_eff)]
            program = programs[0]  # for tensor metadata / 兼容
        else:
            program = MockProgram(spec=self.spec, dtype=operator.dtype)
            programs = None

        ops = operator.get_dfg(spec=self.spec)
        if self.show_model:
            print("\n[DEBUG] DFG Nodes Metadata:")
            for op in ops:
                print(f"  Node: {op.trace_id:<15} | Type: {op.op_type:<10} | Meta: {op.metadata}")
                
        synth = UCodeSynthesizer(self.spec)
        result = synth.solve_with_units(ops, MII=1, MAX_II=64)
        if not result:
            raise RuntimeError("Synthesis failed.")
        
        schedule, II, unit_map = result["schedule"], result["II"], result.get("unit_assignments", {})
        for op in ops:
            op.metadata["global_t"] = schedule[op.trace_id]
            op.metadata["unit_id"] = unit_map.get(op.trace_id)
 
        v_level = 3 if self.show_model else (2 if self.show_review else 1)
        assembler = PhysicalAssembler(self.spec, verbosity=v_level)
        
        input_tensor_names = [n for n, c in operator.tensor_configs.items() if c.get("role") == "input"]
        if not input_tensor_names:
             input_key = next(iter(input_data))
        else:
             input_key = input_tensor_names[0]
             
        num_loop_iters = len(input_data[input_key]) // vector_lanes
        # ADR-036 S1：structural_only 节点（如 ds_src_read 列对齐读）只作 SMT 排程锚点，
        # 不进 emit/sim —— S1/S2 期间真实数值由 model-A apply_smi_ds_t backdoor 提供。
        # solve 已对全部 ops（含 structural）排程并设 global_t；此处仅从 emit/assemble 路径剔除。
        emit_ops = [op for op in ops if not op.metadata.get("structural_only")]
        physical_instrs = assembler.assemble(emit_ops, II=II, num_iterations=num_loop_iters)
 
        if is_multi_smc and hasattr(operator, "inject_data_per_smc"):
            operator.inject_data_per_smc(
                programs, input_data, vector_lanes=vector_lanes, spec=self.spec
            )
        else:
            operator.inject_data(program, input_data, vector_lanes=vector_lanes, spec=self.spec)
        golden_res = operator.get_golden(input_data, vector_lanes=vector_lanes)
        
        for name, config in operator.tensor_configs.items():
            raw_shape = config.get("shape", (num_loop_iters, vector_lanes))
            t_shape = tuple((vector_lanes if s == -1 else s) for s in raw_shape)
            program.tensors[name] = TensorObj(
                bank=config["bank"], 
                offset=config.get("offset", 0), 
                shape=t_shape,
                role=config["role"]
            )
 
        if op_name is None:
            op_name = operator.__class__.__name__.lower()
        output_dir = os.path.join(self.output_root, op_name)
        os.makedirs(output_dir, exist_ok=True)
        
        actual_max_t = max((instr.abs_t for instr in physical_instrs), default=0)
        safe_max_cycles = actual_max_t + (II * num_loop_iters) + 50
        if self.show_review:
            print(f"[RUNNER] Dynamic Max Cycles set to: {safe_max_cycles}")

        v_level = 3 if self.show_model else (2 if self.show_review else 1)
        packer = LIRPacker(self.hw_config_name, verbosity=v_level)
        if self.show_review and is_multi_smc:
            print(f"[RUNNER] parallel_dim={getattr(operator, 'parallel_dim', None)} "
                  f"→ multi-SMC emit smc_num={smc_num_eff}")
        # 多 SMC 模式：传 programs list；emitter._emit_multi_smc 据此 per-SMC inject_data
        emit_program = programs if is_multi_smc else program
        # ADR-020 v6: 多 SMC 路径必传 lir_stream (DFG 拓扑) 用于 per-SMC stagger
        packer.emit_from_instructions(
            physical_instrs, output_dir, program=emit_program, smc_num=smc_num_eff,
            lir_stream=emit_ops if is_multi_smc else None,
        )
        self._patch_max_cycles(output_dir, safe_max_cycles)

        # ADR-033 Stage-A：算子提供 SMI_DS_T 跨-SMC 转置 config → patch 进 sim_config.json
        # （照搬 _patch_max_cycles 的 post-emit patch 模式，零 emitter 签名改动）。
        if is_multi_smc and hasattr(operator, "smi_ds_t_config"):
            cfg = operator.smi_ds_t_config(spec=self.spec)
            if cfg:
                self._patch_sim_config(output_dir, {"smi_ds_t": cfg})

        # 🛸 [V5.4 FIX] 保存物理指令供审计员使用
        with open(os.path.join(output_dir, "physical_instructions.json"), "w") as f:
            json.dump([instr.to_dict() for instr in physical_instrs], f, indent=2)
        
        np.savez(os.path.join(output_dir, "golden_data.npz"), y=golden_res["y"])
        with open(os.path.join(output_dir, "expected_values.json"), "w") as f:
            json.dump(golden_res["intermediates"], f, indent=2)
        
        schedule_dump = {
            "II": II,
            "ops": {op.trace_id: {"t": int(op.metadata["global_t"]), "iq": op.metadata["unit_id"]} for op in ops}
        }
        with open(os.path.join(output_dir, "compiler_schedule.json"), "w") as f:
            json.dump(schedule_dump, f, indent=2)

        if self.show_review:
            print(f"[STEP] Running Physical Simulation...")
        
        log_path = os.path.join(output_dir, f"{op_name}_sim.log")
        with open(log_path, "w") as log_f:
            sim_cmd = [sys.executable, "sim/smc_simulator/tools/physical_runner.py", output_dir]
            res = subprocess.run(sim_cmd, stdout=log_f, stderr=log_f)
            if res.returncode != 0:
                print(f"[ERROR] Physical simulation failed. Check {log_path}")

        # 🛸 [V6.4] 解析并控制台实时回显 SMC 仿真比对结果
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                lines = f.readlines()
            start_idx = -1
            for idx, line in enumerate(lines):
                if "Simulation Finished" in line and "Verifying Data..." in line:
                    start_idx = idx
                    break
            if start_idx != -1:
                print("\n>>> SMC Physical Simulation Numerical Verification:")
                for line in lines[start_idx:]:
                    print("  " + line.strip("\n"))
            else:
                # Fallback: Just print last 10 lines if "Simulation Finished" is not matched
                print("\n>>> SMC Physical Simulation Numerical Verification:")
                for line in lines[-10:]:
                    print("  " + line.strip("\n"))

        return {
            "output_dir": output_dir,
            "log_path": log_path,
            "schedule": schedule,
            "II": II,
            "physical_instrs": physical_instrs,
            "num_iterations": num_loop_iters,
            "sim_success": (res.returncode == 0),
            "ops": ops,
            # ADR-020 v3：multi-SMC 路径在本函数内 line 39 重生成 input_data（_prepare_input_parallel_n,
            # rng.uniform(1,5) + default_rng(0)），与 main_verify.run_test 入口前 prepare_input(n_rows)
            # 的 randn 输出不一致。Auditor 须用 sim 实际吃到的这份算 expected，否则系统性 mismatch。
            "input_data": input_data,
        }

    def _compute_smc_num(self, operator: Any):
        """
        ADR-018 v7 D5.d (task #18 sub-5): parallel_dim 算子触发多 SMC emit 路径。

        - parallel_dim ∈ {"N", "K"} → smc_num = PARAM_SMC_NUM (=32)
        - parallel_dim = None / 算子无此属性 → smc_num = None (单 SMC 向后兼容)

        emitter.emit_from_instructions 看到 smc_num=None 走 _emit_single_smc
        (旧 smc_0/ layout)；smc_num>=2 走 _emit_multi_smc (smc_{k}/ layout)。
        """
        parallel_dim = getattr(operator, "parallel_dim", None)
        if parallel_dim is None:
            return None
        return self.spec.params.PARAM_SMC_NUM

    def _patch_max_cycles(self, output_dir: str, max_cycles: int):
        conf_path = os.path.join(output_dir, "..", "sim_config.json")
        if not os.path.exists(conf_path):
            conf_path = os.path.join(output_dir, "sim_config.json")
            if not os.path.exists(conf_path): return
        
        with open(conf_path, "r") as f:
            data = json.load(f)
        data["max_cycles"] = max_cycles
        with open(conf_path, "w") as f:
            json.dump(data, f, indent=2)

    def _patch_sim_config(self, output_dir: str, extra: dict):
        """把额外键值 patch 进 sim_config.json（同 _patch_max_cycles 的 post-emit 模式）。"""
        conf_path = os.path.join(output_dir, "..", "sim_config.json")
        if not os.path.exists(conf_path):
            conf_path = os.path.join(output_dir, "sim_config.json")
            if not os.path.exists(conf_path):
                return
        with open(conf_path, "r") as f:
            data = json.load(f)
        data.update(extra)
        with open(conf_path, "w") as f:
            json.dump(data, f, indent=2)

    def parse_simulation_timeline(self, output_dir: str) -> Dict[str, List[str]]:
        timeline_path = os.path.join(output_dir, "smc_0", "analysis", "timeline_ascii.txt")
        actual_activity = {}
        if os.path.exists(timeline_path):
            with open(timeline_path, "r") as f:
                for line in f:
                    if "|" in line and "iq_name" not in line and "occupancy" not in line:
                        parts = line.split("|")
                        if len(parts) >= 2:
                            name = parts[0].strip()
                            trace = parts[1].strip().split()
                            actual_activity[name] = trace
        return actual_activity
