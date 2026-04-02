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
                     hw_spec: Any = None) -> int:
        header = 0
        
        # 🛸 物理对齐：位宽决议中心
        # 优先级：1. 明确传入的 iq_conf.BITS_CODE (最权威) > 2. 默认 16
        bits_code = getattr(iq_conf, "BITS_CODE", DEFAULT_BITS_CODE)
        
        # 🛸 动态 Header 映射 (基于硬件规格书)
        if hw_spec and hasattr(hw_spec, "system_core") and hw_spec.system_core:
            iq_header = hw_spec.system_core.get("iq_header", [])
            curr = 0
            # 这里的输入变量需要映射到 Header 字段
            val_map = {
                "INC_EMBED": embed, "INC": inc, "DLY": dly, 
                "COND": 0, "LOOPS": loops, "EMBED_END": embed_end, 
                "EMBED_LOOPS": 0, "JUMP": jump, "VLD": 1
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
            # EMBED_LOOPS Placeholder [36:38]
            header |= ((jump & 1) << 39)           # [39]
            header |= (1 << 40)                    # [40] VLD

        # 🛰️ 物理封裝：Header 在 Payload 的高位 (LSB-First Logic)
        return (header << bits_code) | (payload & ((1 << bits_code) - 1))

    @staticmethod
    def pack_to_bytes(val: int, total_bits: int) -> bytes:
        byte_len = (total_bits + 7) // 8
        return val.to_bytes(byte_len, byteorder='little')
