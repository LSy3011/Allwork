import struct
from typing import Dict

class BitPacker:
    """
    AIACC V5.0 位封装器 (ISA 统合版)。
    处理 41-bit 微码中的“静态锚点区”与“30 位动态货物区”的竞争。
    """
    @staticmethod
    def pack_41bit(iq_name: str, iq_conf, payload: int, 
                   loops: int = 0, dly: int = 0, inc: int = 0, 
                   cond: int = 0, embed: int = 0, jump: int = 0,
                   embed_end: int = 0, inc_embed: int = 0) -> int:
        res = 0
        
        # 1. 静态锚点 (Bit 30-40)
        res |= (1 << 40) # VLD
        res |= ((jump & 1) << 39)
        res |= ((embed & 0x7) << 36)
        res |= ((embed_end & 0x3F) << 30)
        
        # 2. 动态货物区 (Bit 0-29) - 30 位共享池
        # A. 业务载荷 (CODE/INC/INC_EMBED) 从 Bit 0 向上生长
        # 注意：这里的逻辑是 INC 可能会复用部分 CODE 的空间
        # 我们这里采用“叠加模式”：CODE 占位 0..N, INC_EMBED 占 0..5, INC 占 6..9
        res |= (payload & 0x3FFFFFFF) # 原始覆盖，取决于 iq_conf.BITS_CODE
        
        # B. 控制工具 (LOOPS, COND, DLY) 从 Bit 29 向下压入
        # LOOPS (10b): 29-20
        if iq_conf.EN_LOOPS:
            res |= ((loops & 0x3FF) << 20)
            
        # COND (7b): 19-13
        if getattr(iq_conf, "EN_COND", False):
            res |= ((cond & 0x7F) << 13)
            
        # DLY (3b): 12-10
        if iq_conf.EN_DLY:
            res |= ((dly & 0x7) << 10)
            
        # 特殊：INC (4b): 9-6  (覆盖 Payload 低位)
        if getattr(iq_conf, "EN_INC", False):
            res |= ((inc & 0xF) << 6)
            
        # 特殊：INC_EMBED (6b): 5-0 (覆盖 Payload 最低 6 位)
        if getattr(iq_conf, "EN_INC", False):
            res |= (inc_embed & 0x3F)

        return res

    @staticmethod
    def uint64_to_bin_bytes(val: int) -> bytes:
        return struct.pack("<Q", val)
