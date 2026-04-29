import os, json, struct, numpy as np, copy
from pathlib import Path
from typing import List, Dict, Tuple, Any

from api.hw_spec import HardwareSpec
from driver.field_mapper import FieldMapper
from driver.compactor import Compactor
from driver.packer import BitPacker
from driver.hw_constants import (
    IQ_BASE_UR_READ, IQ_BASE_UR_WRITE, 
    PORT_OFFSET_UR_RDATA, PORT_ID_FPMUL_D, PORT_ID_FPADD_D, PORT_ID_INTADD_D0,
    OFFSET_VLD, DEFAULT_BITS_CODE, DEFAULT_MAX_CYCLES
)
from ir.lir.lir_op import PhysicalInstruction, SMIHeader

class LIRPacker:
    """
    AIACC AI-COMPILER EMITTER (V5.0: Pure Serialization).
    负责将已经物理定稿的 PhysicalInstruction 序列化为 Hex 文件。
    不再承担任何路由推导或时序逻辑。
    """
    def __init__(self, config_dir: str = "hw_config", verbosity: int = 2):
        self.hw_spec = HardwareSpec.load(config_dir)
        self.verbosity = verbosity

    def emit_from_instructions(self, instructions: List[PhysicalInstruction], case_root: str, program=None):
        root = Path(case_root)
        smc0 = root / "smc_0"
        ur_dir, bank_dir, analysis = smc0/"ur_data", smc0/"output_bank", smc0/"analysis"
        for d in [smc0, ur_dir, bank_dir, analysis]: os.makedirs(d, exist_ok=True)
        (root / "sim_trace.log").touch()
        
        # 1. 按物理 IQ 分桶
        iq_buckets: Dict[str, List[PhysicalInstruction]] = {}
        for instr in instructions:
            iq_buckets.setdefault(instr.iq_name, []).append(instr)

        # 2. 二进制打包与 Hex 生成
        iq_init = {}
        for iq_name, stream in iq_buckets.items():
            # 严格按时间排序
            stream.sort(key=lambda x: x.abs_t)
            # 1. 确定单元基础类型 (用于静态打包)
            lookup_type = self._get_lookup_type(iq_name)
            total_bits = BitPacker.get_unit_bits(self.hw_spec, lookup_type)
            hex_chars = (total_bits + 3) // 4
            
            hex_lines = []
            for instr in stream:
                # 🛸 明确阶段判定：只有 BODY 阶段才允许携带 Body 载荷
                is_body_stage = (instr.stage == "BODY")
                
                # 🛸 调用动态规格打包器
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
            if self.verbosity >= 2:
                print(f"[EMITTER] IQ: {iq_name:<10} | Type: {lookup_type:<8} | Total: {total_bits:3} | Instrs: {len(hex_lines)}")

        # 3. 导出仿真配置
        actual_max_t = max((instr.abs_t for instr in instructions), default=0)
        effective_max_cycles = max(DEFAULT_MAX_CYCLES, actual_max_t + 10)
        sim_conf = {
            "smc_id": 0,
            "iq_init": iq_init,
            "ur_init": {str(b): f"smc_0/ur_data/bank{b}.hex" for b in range(8)},
            "max_cycles": effective_max_cycles,
            "tensors": []
        }
        
        if program:
            self._handle_program_data(program, ur_dir, sim_conf)

        with open(root / "sim_config.json", "w") as f:
            json.dump(sim_conf, f, indent=2)

        if self.verbosity >= 2:
            print(f"[EMIT DONE] case emitted to {case_root}")

    def _handle_program_data(self, program, ur_dir, sim_conf):
        """处理 Bank 数据初始化与 Tensor 元数据映射。"""
        v_lanes = program.vector_lanes
        for b_id, storage in program.memory_allocator.banks.items():
            with open(ur_dir / f"bank{b_id}.hex", "w") as f:
                for entry in storage:
                    if not isinstance(entry, (list, np.ndarray, tuple)):
                        # 🛸 标量广播扩展: 根据实际 vector_lanes 填满物理行
                        entry = [entry] * v_lanes
                    
                    # 🛰️ 128-bit 矢量物理通道对位 (动态适配 lanes)
                    packed = 0
                    for i, x in enumerate(entry[:v_lanes]):
                        if isinstance(x, int):
                            bits = x & 0xFFFFFFFF
                        else:
                            bits = int(struct.unpack(">I", struct.pack(">f", float(x)))[0]) & 0xFFFFFFFF
                        packed |= (bits << (32 * i))
                    f.write(f"{packed:032x}\n")
        
        for t_name, t_obj in program.tensors.items():
            sim_conf["tensors"].append({
                "name": t_name, "bank": t_obj.bank, "addr": t_obj.offset,
                "shape": t_obj.shape, "role": t_obj.role
            })

    def _resolve_iq_conf(self, iq_name: str):
        """将物理单元名映射到 iq_config.yaml 中的 IQConfig。"""
        # 1. 提取基础类型名 (UR-R-0 -> UR_READ, logic_0 -> LOGIC)
        base_type = self._get_lookup_type(iq_name)
        
        # 2. 尝试匹配 IQ_XXX
        iq_keys = [f"IQ_{base_type}", f"IQ_{base_type.replace('_', '-')}", base_type]
        for key in iq_keys:
            if key in self.hw_spec.iqs:
                return self.hw_spec.iqs[key]
        return None

    def _get_lookup_type(self, iq_name: str) -> str:
        """剥离序号和特殊字符，还原为模块基类名。"""
        name = iq_name.upper().replace("-", "_")
        
        # 特殊映射：UR_R0 -> UR_READ, UR_W0 -> UR_WRITE
        if name.startswith("UR_R"): return "UR_READ"
        if name.startswith("UR_W"): return "UR_WRITE"
        
        # 移除末尾的序号 (如 _0, _1, 或直接跟随字母的数字)
        import re
        name = re.sub(r'_\d+$', '', name)
        name = re.sub(r'\d+$', '', name)
        
        return name

    def dump_verbose(self, instructions: List[PhysicalInstruction]):
        print("\n" + "="*40 + " TRUTH-ALIGNED MICRO-INSTRUCTION DUMP " + "="*40)
        
        # 1. 确保按单元和绝对时间排序
        instructions.sort(key=lambda x: (x.iq_name, x.abs_t))
        
        last_iq = None
        for p_instr in instructions:
            if p_instr.iq_name != last_iq:
                print(f"\n--- Unit: {p_instr.iq_name.upper()} ---")
                last_iq = p_instr.iq_name
            
            # A. 构造标签
            if p_instr.stage == "INIT":
                tag = "[INIT]"
            elif p_instr.stage == "HDR":
                tag = "[HDR] "
            else:
                tag = f"[{p_instr.local_id}]  "
            
            # B. 格式化 Header
            h = p_instr.header
            header_str = f"VLD:{h.vld}|EMB:{h.embed}|E_END:{h.embed_end}|LOOP:{h.loops}|INC:{h.inc}"
            
            # C. 格式化 Body
            if p_instr.header.vld == 0 and p_instr.stage != "INIT":
                body_str = "NOP"
            else:
                body_parts = []
                for k, v in p_instr.payload.items():
                    if k.isupper() and k not in ["IQ_ID", "GLOBAL_T"]:
                        body_parts.append(f"{k}:{v}")
                body_str = "|".join(body_parts) if body_parts else "NOP"
            
            # D. 打印整行
            comment = f"/* {p_instr.comment} */" if p_instr.comment else ""
            trace = f"/* Op: {p_instr.trace_id} */" if p_instr.trace_id else ""
            
            print(f"{p_instr.iq_name:<15}{tag} : HEAD:[{header_str}] | BODY:[{body_str}] {trace} {comment}")
        
        print("\n" + "="*118)
