import struct
from typing import Dict
from typing import Dict, Any

DEFAULT_BITS_CODE = 16

class BitPacker:
    """
    AIACC V9.1 位封装器 (鲁棒规格驱动版)。
    """
    @staticmethod
    def pack_capsule(iq_name: str, iq_conf: Any, payload: int, loops: int = 0, dly: int = 0, 
                     inc: int = 0, embed: int = 0, jump: int = 0, embed_end: int = 0, 
                     hw_spec: Any = None, valid: int = 1, level: int = 0, 
                     cond: int = 0, inc_embed: int = 0) -> int:
        header = 0
        
        # 🛸 物理对齐：位宽决议中心
        # 优先级：1. 明确传入的 iq_conf.BITS_CODE (最权威) > 2. 默认 16
        bits_code = getattr(iq_conf, "BITS_CODE", DEFAULT_BITS_CODE)
        
        # 🛸 动态 Header 映射 (基于硬件规格书)
        if hw_spec and hasattr(hw_spec, "system_core") and hw_spec.system_core:
            iq_header = hw_spec.system_core.get("iq_header", [])
            curr = 0
            # 这里的输入变量需要映射到 Header 字段
            # embed 映射为 INC_EMBED (Level 1 增量), level 映射为 EMBED_LOOPS (层级标签)
            val_map = {
                "INC_EMBED": inc_embed or embed, # 兼容性处理
                "INC": inc, "DLY": dly, 
                "COND": cond, "LOOPS": loops, "EMBED_END": embed_end, 
                "EMBED_LOOPS": level, "JUMP": jump, "VLD": valid
            }
            for item in iq_header:
                f_name = item.get("field")
                f_width = item.get("width")
                f_val = val_map.get(f_name, 0)
                header |= ((f_val & ((1 << f_width) - 1)) << curr)
                curr += f_width
        else:
            # 🛸 安全回退基准 (isa_v5 对齐)
            header |= (embed & 0x3F)               # [0:5]
            header |= ((inc & 0xF) << 6)           # [6:9]
            header |= ((dly & 0x7) << 10)          # [10:12]
            # COND Placeholder [13:19]
            header |= ((loops & 0x3FF) << 20)      # [20:29]
            header |= ((embed_end & 0x3F) << 30)   # [30:35]
            # EMBED_LOOPS [36:38]
            header |= ((level & 0x7) << 36)
            header |= ((jump & 1) << 39)           # [39]
            header |= (valid << 40)                 # [40] VLD

        # 🛰️ 物理封装：统一 ISA 空间合并 (Unified ISA Space Merging)
        # 修正 V5.0 严重 Bug：不再级联 (Concat)，而是根据 Offset 覆盖/合并。
        # Header 已经在 bits [0:40] 中根据规格对齐，Payload (CODE) 默认在 LSB [0:bits_code-1]。
        
        # 1. 提取 Payload 掩码，防止溢出干扰 Header 高位字段 (如 VLD/JUMP)
        mask = (1 << bits_code) - 1
        safe_payload = payload & mask
        
        # 2. 合并逻辑：由于 V5 ISA 的 Payload 区域与 INC/DLY/LOOPS 等字段存在物理复用 (Reuse)，
        # 我们采用 OR 逻辑。在编译器上层，如果启用了 CODE，通常会保证 INC/DLY 字段为 0 以免干扰。
        # 最终指令宽度保持在 41-bit (或由硬件参数定义的 BITS_HEADER 宽度)。
        return (header | safe_payload)

    @staticmethod
    def pack_to_bytes(val: int, total_bits: int) -> bytes:
        byte_len = (total_bits + 7) // 8
        return val.to_bytes(byte_len, byteorder='little')
