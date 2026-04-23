from dataclasses import dataclass
from typing import Dict, List

@dataclass
class SMIHeader:
    """SMI_IQ 共有 Header"""
    vld: int = 1
    jump: int = 0
    embed: int = 0
    embed_end: int = 0
    loops: int = 0
    cond: int = 0
    dly: int = 0
    inc: int = 0
    inc_embed: int = 0

    def to_string(self) -> str:
        fields = [
            f"VALID:{self.vld}", f"JUMP:{self.jump}", f"EMBED:{self.embed}",
            f"EMBED_END:{self.embed_end}", f"LOOPS:{self.loops}",
            f"COND:{self.cond}", f"DLY:{self.dly}", f"INC:{self.inc}",
            f"INC_EMBED:{self.inc_embed}"
        ]
        return "HEAD:[" + "|".join(fields) + "]"

class SMIEncoderTruth:
    """
    真相对齐编码器：按硬件单元 Dump 所有指令及 DLY 时序。
    """
    def __init__(self, ii: int):
        self.ii = ii

    def get_body_string(self, op_type: str, trace_id: str) -> str:
        """根据算子类型生成 Mock 的 BODY 字段"""
        op_type = op_type.upper()
        if "FPADD" in op_type:
            return "BODY:[SRC_PREC:1|DST_PREC:1|UP_STAT_EN:0|ADD_OR_SUB:0|SMC_ID_EN:31]"
        if "FPMUL" in op_type:
            return "BODY:[SRC_PREC:1|DST_PREC:1|SMC_ID_EN:31]"
        if "RTOVR" in op_type:
            return "BODY:[SEL:3]" # 默认 Mock SEL:3
        if "UR_READ" in op_type:
            return f"BODY:[BANK:0|ADDR:0|SMC_ID_EN:31]"
        if "UR_WRITE" in op_type:
            return f"BODY:[BANK:3|ADDR:0|WBE:0|SMC_ID_EN:31]"
        return "BODY:[RAW_CODE:0]"

    def dump_truth_aligned(self, ops: List, schedule: Dict[str, int], 
                           unit_map: Dict[str, str], loop_count: int, base_stride: int = 1) -> str:
        lines = ["TRUTH-ALIGNED MICRO-INSTRUCTION DUMP", "="*60]
        
        # 1. 收集每个单元的所有指令
        unit_instructions = {}
        for op in ops:
            u_name = unit_map[op.trace_id]
            unit_instructions.setdefault(u_name, []).append(op)
        
        # 2. 计算 INC (Stride * II)
        # 如果 II=6, Stride=1, 则 INC=6
        dynamic_inc = base_stride * self.ii
        
        # 3. 排序并输出
        for u_name in sorted(unit_instructions.keys()):
            sorted_ops = sorted(unit_instructions[u_name], key=lambda x: schedule[x.trace_id])
            
            for i, op in enumerate(sorted_ops):
                h = SMIHeader()
                h.vld = 1
                h.loops = loop_count - 1
                h.dly = schedule[op.trace_id]  # 来自 SMT 调度的绝对时刻
                h.inc = dynamic_inc if not op.metadata.get("is_invariant") else 0
                
                # 标记物理终结点
                if op.op_type == "UR_WRITE" or (u_name == "rtovr_1" and i == len(sorted_ops)-1):
                    h.embed_end = 1
                
                body = self.get_body_string(op.op_type, op.trace_id)
                display_name = u_name.replace("-", "_")
                idx_str = f"[{i}]" if len(sorted_ops) > 1 else ""
                
                lines.append(f"{display_name}{idx_str:<5}: {h.to_string()} | {body}")
        
        return "\n".join(lines)
