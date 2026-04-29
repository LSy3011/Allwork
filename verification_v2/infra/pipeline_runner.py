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
        self.spec = HardwareSpec.load(hw_config_name)
        self.show_review = show_review
        self.show_model = show_model
        self.output_root = "build/verification_v2"
        os.makedirs(self.output_root, exist_ok=True)

    def run_full_stack(self, operator: Any, input_data: Dict[str, np.ndarray]):
        if self.show_review:
            print(f"\n>>> Starting Pipeline Runner...")
        
        # 0. 精度校准 (Precision Calibration)
        vector_width_bits = self.spec.params.PARAM_SMC_VECTOR_WIDTH
        element_bits = operator.element_bits
        vector_lanes = vector_width_bits // element_bits
        
        from verification_v2.infra.mock_memory import MockProgram
        program = MockProgram(vector_lanes=vector_lanes)
        ops = operator.get_dfg(spec=self.spec)
        synth = UCodeSynthesizer(self.spec)
        result = synth.solve_with_units(ops, MII=1, MAX_II=32)
        if not result:
            raise RuntimeError("Synthesis failed.")
        
        schedule, II, unit_map = result["schedule"], result["II"], result.get("unit_assignments", {})
        for op in ops:
            op.metadata["global_t"] = schedule[op.trace_id]
            op.metadata["unit_id"] = unit_map.get(op.trace_id)

        # 2. Assembly
        v_level = 3 if self.show_model else (2 if self.show_review else 1)
        assembler = PhysicalAssembler(self.spec, verbosity=v_level)
        
        # 确保数据长度是通道数的整数倍
        input_key = next(iter(input_data))
        num_loop_iters = len(input_data[input_key]) // vector_lanes
        
        physical_instrs = assembler.assemble(ops, II=II, num_iterations=num_loop_iters)

        # 3. Data Injection & Golden Calculation
        operator.inject_data(program, input_data, vector_lanes=vector_lanes, spec=self.spec)
        golden_res = operator.get_golden(input_data, vector_lanes=vector_lanes)
        
        # 🛸 铲除硬编码：基于算子自描述自动注册 Tensor
        for name, config in operator.tensor_configs.items():
            # 支持显式指定的 shape，并处理动态 lanes (-1)
            raw_shape = config.get("shape", (num_loop_iters, vector_lanes))
            t_shape = tuple((vector_lanes if s == -1 else s) for s in raw_shape)
            
            program.tensors[name] = TensorObj(
                bank=config["bank"], 
                offset=config.get("offset", 0), 
                shape=t_shape,
                role=config["role"]
            )

        # 4. Emission
        op_name = operator.__class__.__name__.lower()
        output_dir = os.path.join(self.output_root, op_name)
        os.makedirs(output_dir, exist_ok=True)
        
        # 🛸 核心修复：动态计算仿真最大周期，防止长指令链+多迭代导致的超时
        # 公式：静态最大时间 + (II * 剩余迭代) + 50 周期排空宽放
        actual_max_t = max((instr.abs_t for instr in physical_instrs), default=0)
        safe_max_cycles = actual_max_t + (II * num_loop_iters) + 50
        if self.show_review:
            print(f"[RUNNER] Dynamic Max Cycles set to: {safe_max_cycles}")

        v_level = 3 if self.show_model else (2 if self.show_review else 1)
        packer = LIRPacker("hw_config", verbosity=v_level)
        # 传递动态计算的 max_cycles (如果 packer 支持，否则后续手动修改 sim_config)
        packer.emit_from_instructions(physical_instrs, output_dir, program=program)
        
        # 覆盖 sim_config.json 中的硬编码值
        self._patch_max_cycles(output_dir, safe_max_cycles)
        
        # 保存辅助文件
        np.savez(os.path.join(output_dir, "golden_data.npz"), y=golden_res["y"])
        with open(os.path.join(output_dir, "expected_values.json"), "w") as f:
            json.dump(golden_res["intermediates"], f, indent=2)
        
        schedule_dump = {
            "II": II,
            "ops": {op.trace_id: {"t": int(op.metadata["global_t"]), "iq": op.metadata["unit_id"]} for op in ops}
        }
        with open(os.path.join(output_dir, "compiler_schedule.json"), "w") as f:
            json.dump(schedule_dump, f, indent=2)

        # 5. Simulation
        if self.show_review:
            print(f"[STEP] Running Physical Simulation...")
        log_path = "v2_test_output.log"
        with open(log_path, "w") as log_f:
            sim_cmd = ["python3", "sim/smc_simulator/tools/physical_runner.py", output_dir]
            res = subprocess.run(sim_cmd, stdout=log_f, stderr=log_f)
            if res.returncode != 0:
                print(f"❌ [ERROR] Physical simulation failed. Check {log_path}")
                # CONTINUE so auditor can print timing mismatch

        return {
            "output_dir": output_dir,
            "log_path": log_path,
            "schedule": schedule,
            "II": II,
            "physical_instrs": physical_instrs,
            "num_iterations": num_loop_iters
        }

    def _patch_max_cycles(self, output_dir: str, max_cycles: int):
        """覆盖 LIRPacker 默认生成的仿真周期配置。"""
        # sim_config.json 在 output_dir 的父目录 (case_root)
        conf_path = os.path.join(output_dir, "..", "sim_config.json")
        if not os.path.exists(conf_path):
            # 兼容性处理：有时可能在 output_dir 下
            conf_path = os.path.join(output_dir, "sim_config.json")
            if not os.path.exists(conf_path): return
        
        with open(conf_path, "r") as f:
            data = json.load(f)
        
        data["max_cycles"] = max_cycles
        
        with open(conf_path, "w") as f:
            json.dump(data, f, indent=2)
