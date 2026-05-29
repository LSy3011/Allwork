import os, json, struct, numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, TYPE_CHECKING

from api.hw_spec import HardwareSpec
from driver.packer import BitPacker
from ir.lir.lir_op import PhysicalInstruction, SMIHeader

if TYPE_CHECKING:
    from ir.mir.mir_op import MIROp

# 从 driver/hw_constants.py inline 的唯一存活常量（其余符号 driver-wide 零使用，已随 hw_constants 一并删除）
DEFAULT_MAX_CYCLES = 120

class LIRPacker:
    """
    AIACC AI-COMPILER EMITTER (V5.1: Fully Decoupled Serialization).
    """
    def __init__(self, config_dir: str = "hw_config", verbosity: int = 2):
        self.hw_spec = HardwareSpec.load(config_dir)
        self.verbosity = verbosity

    def emit_from_instructions(
        self,
        instructions: List[PhysicalInstruction],
        case_root: str,
        program=None,
        smc_num: int = None,
        *,
        lir_stream: Optional[List["MIROp"]] = None,
    ):
        """
        ADR-023 单 SMI 全局指令流入口（supersede ADR-018/020 per-SMC 多流）。

        Args:
            smc_num: None / 1 → 单 SMC 路径（smc_0/ layout）；>=2 → 多 SMC 单全局流路径。
            lir_stream: 已废。ADR-020 v6 per-SMC stagger 已随 ADR-023 D1 删除；
                        参数保留仅为不破调用方签名（pipeline_runner 仍传 lir_stream=），忽略其值。
        """
        if smc_num is None or smc_num == 1:
            return self._emit_single_smc(instructions, case_root, program)
        return self._emit_multi_smc(instructions, case_root, program, smc_num)

    def _emit_single_smc(self, instructions: List[PhysicalInstruction], case_root: str, program=None):
        """N=1 单 SMC emit 路径（ADR-018 v7 D5.a 向后兼容）。"""
        root = Path(case_root)
        smc0 = root / "smc_0"
        ur_dir, bank_dir, analysis = smc0/"ur_data", smc0/"output_bank", smc0/"analysis"
        for d in [smc0, ur_dir, bank_dir, analysis]: os.makedirs(d, exist_ok=True)

        for old_hex in smc0.glob("iq_*.hex"):
            old_hex.unlink()

        (root / "sim_trace.log").touch()

        iq_buckets: Dict[str, List[PhysicalInstruction]] = {}
        for instr in instructions:
            iq_buckets.setdefault(instr.iq_name, []).append(instr)

        iq_init = {}
        for iq_name, stream in iq_buckets.items():
            stream.sort(key=lambda x: x.abs_t)
            lookup_type = self._get_lookup_type(iq_name)
            total_bits = BitPacker.get_unit_bits(self.hw_spec, lookup_type)
            hex_chars = (total_bits + 3) // 4

            hex_lines = []
            for instr in stream:
                is_body_stage = (instr.stage == "BODY")
                val = BitPacker.pack_capsule(
                    spec=self.hw_spec,
                    unit_type=lookup_type,
                    payload_dict=instr.payload,
                    is_body=is_body_stage,
                    **instr.header.to_dict()
                )
                hex_lines.append(f"{val:0{hex_chars}x}")

            hex_file_name = f"iq_{iq_name}.hex"
            with open(smc0 / hex_file_name, "w") as f:
                f.write("\n".join(hex_lines) + "\n")

            iq_init[iq_name.replace("_", "-")] = f"smc_0/{hex_file_name}"

        # 🛸 [V5.1 FIX] 动态获取 Bank 数量，不再死磕 range(8)
        bank_num = getattr(self.hw_spec.params, "PARAM_UR_BANK_NUM", 8)
        sim_conf = {
            "smc_id": 0,
            "iq_init": iq_init,
            "ur_init": {str(b): f"smc_0/ur_data/bank{b}.hex" for b in range(bank_num)},
            "max_cycles": DEFAULT_MAX_CYCLES,
            "tensors": []
        }

        if program:
            self._handle_program_data(program, ur_dir, sim_conf)

        with open(root / "sim_config.json", "w") as f:
            json.dump(sim_conf, f, indent=2)

    def _emit_multi_smc(
        self,
        instructions: List[PhysicalInstruction],
        case_root: str,
        program,
        smc_num: int,
    ):
        """
        ADR-023 D1：单 SMI 全局指令流 emit（supersede ADR-018/020 per-SMC 多流）。
        - **一套** iq_*.hex 写在 case_root 根级（所有 SMC 共享同一份全局指令流）；指令带
          smc_id_en（SHIFTUP 广播由 smc_id_en×broadcast 表达）。SMC_ID/smc_id_en 寻址在
          硬件/sim 执行层按各 SMC 自身 SMC_ID 匹配（ADR-022 单 SMI 模型）。
        - **删** ADR-020 per-SMC INIT stagger + ADR-018 per-SMC SMC_ID 注入 / SHIFTUP follower
          data 清零 / chain skip —— 单流下无 per-SMC 复制，这些遗留机制失去前提（ADR-023 D1）。
        - compute 指令 SMC_ID code_field 走 modules.yaml default 0（HCO 选项 ii，agentId
          a45dedc230c151316）；硬件按自身 SMC_ID 寄存器分发，所有使能 SMC 广播式本地计算。
        - UR 数据仍 per-SMC：smc_k/ur_data/bank*.hex。
        - sim_config.json: 单 iq_init + per_smc_ur_init（list, index=smc_id）。
        """
        root = Path(case_root)
        bank_num = getattr(self.hw_spec.params, "PARAM_UR_BANK_NUM", 8)

        (root / "sim_trace.log").touch()

        # 清迁移期残留的旧 per-SMC iq hex（root 根级 + smc_k/）避免误导
        for old_hex in root.glob("iq_*.hex"):
            old_hex.unlink()

        # 1) 写单套全局指令流 iq_*.hex（case_root 根级）
        iq_buckets: Dict[str, List[PhysicalInstruction]] = {}
        for instr in instructions:
            iq_buckets.setdefault(instr.iq_name, []).append(instr)

        iq_init = {}
        for iq_name, stream in iq_buckets.items():
            stream.sort(key=lambda x: x.abs_t)
            lookup_type = self._get_lookup_type(iq_name)
            total_bits = BitPacker.get_unit_bits(self.hw_spec, lookup_type)
            hex_chars = (total_bits + 3) // 4

            hex_lines = []
            for instr in stream:
                is_body_stage = (instr.stage == "BODY")
                val = BitPacker.pack_capsule(
                    spec=self.hw_spec,
                    unit_type=lookup_type,
                    payload_dict=instr.payload,
                    is_body=is_body_stage,
                    **instr.header.to_dict()
                )
                hex_lines.append(f"{val:0{hex_chars}x}")

            hex_file_name = f"iq_{iq_name}.hex"
            with open(root / hex_file_name, "w") as f:
                f.write("\n".join(hex_lines) + "\n")

            iq_init[iq_name.replace("_", "-")] = hex_file_name

        # 2) per-SMC sim_config 元数据 + per-SMC UR 数据
        sim_conf = {
            "smc_num": smc_num,
            "smc_id_list": list(range(smc_num)),
            "iq_init": iq_init,           # ADR-023: 单一全局指令流（所有 SMC 共享）
            "per_smc_ur_init": [],        # List[Dict[bank_id, hex_path]]，index = smc_id
            "max_cycles": DEFAULT_MAX_CYCLES,
            "tensors": []
        }

        for smc_id in range(smc_num):
            smc_dir = root / f"smc_{smc_id}"
            ur_dir = smc_dir / "ur_data"
            for d in [smc_dir, ur_dir, smc_dir/"output_bank", smc_dir/"analysis"]:
                os.makedirs(d, exist_ok=True)
            # 清迁移期残留的旧 per-SMC iq hex
            for old_hex in smc_dir.glob("iq_*.hex"):
                old_hex.unlink()

            sim_conf["per_smc_ur_init"].append(
                {str(b): f"smc_{smc_id}/ur_data/bank{b}.hex" for b in range(bank_num)}
            )

            if program:
                # per-SMC 数据注入：program 可为 (a) 单 MockProgram（legacy，仅 SMC_0 注入；
                # 其余空 bank）；(b) List[MockProgram] 长度 = smc_num，每 SMC 独立 inject。
                if isinstance(program, list):
                    per_smc_program = program[smc_id]
                    # tensors 只填一次（共享 metadata：layout 各 SMC 同 logical_addr）
                    target_conf = sim_conf if smc_id == 0 else {"tensors": []}
                    self._handle_program_data(per_smc_program, ur_dir, target_conf)
                elif smc_id == 0:
                    self._handle_program_data(program, ur_dir, sim_conf)
                else:
                    # legacy 单 program 模式：其他 SMC 给空 bank.hex（默认 0），保持 layout 一致
                    for b_id in range(bank_num):
                        (ur_dir / f"bank{b_id}.hex").touch()

        with open(root / "sim_config.json", "w") as f:
            json.dump(sim_conf, f, indent=2)

    def _get_lookup_type(self, iq_name: str) -> str:
        """
        [Phase 3] 代理至 UnitPayloadFactory 处理，摒弃硬编码字典。
        """
        from src.driver.payload_factory import UnitPayloadFactory
        return UnitPayloadFactory.resolve_iq_type(iq_name, self.hw_spec)

    def _handle_program_data(self, program, ur_dir, sim_conf):
        """处理 Bank 数据初始化与 Tensor 元数据映射。"""
        v_lanes = program.vector_lanes
        element_bits = getattr(program, "element_bits", 32)
        
        # 🛸 [V6.7 FIX] 动态选择打包格式与移位参数，支持 FP32/FP16/INT8 等全精度类型，彻底拆除硬件打包硬编码
        if element_bits == 32:
            pack_fmt, unpack_fmt, bit_shift, hex_len_factor = '<f', '<I', 32, 8
        elif element_bits == 16:
            # Python 3.6+ 支持 '<e' 表达 16-bit 浮点数，'<H' 表达 16-bit 无符号短整型
            pack_fmt, unpack_fmt, bit_shift, hex_len_factor = '<e', '<H', 16, 4
        elif element_bits == 8:
            pack_fmt, unpack_fmt, bit_shift, hex_len_factor = '<b', '<B', 8, 2
        else:
            pack_fmt, unpack_fmt, bit_shift, hex_len_factor = '<f', '<I', 32, 8 # fallback

        for b_id, storage in program.memory_allocator.banks.items():
            with open(ur_dir / f"bank{b_id}.hex", "w") as f:
                for entry in storage:
                    # 假定每个 word 是 v_lanes 个元素
                    # 🛸 动态打包各精度向量
                    hex_val = 0
                    for i in range(v_lanes):
                        val_bits = struct.unpack(unpack_fmt, struct.pack(pack_fmt, entry[v_lanes-1-i]))[0]
                        hex_val = (hex_val << bit_shift) | val_bits
                    f.write(f"{hex_val:0{v_lanes*hex_len_factor}x}\n")
        
        for name, obj in program.tensors.items():
            sim_conf["tensors"].append({
                "name": name,
                "bank": obj.bank,
                "addr": obj.offset,
                "shape": list(obj.shape),
                "role": obj.role
            })
