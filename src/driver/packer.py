import struct
from typing import Dict

class BitPacker:
    """
    AIACC V7.0 位封装器 (动态字节对齐版)。
    硬件架构：[Header(41)] [CODE(N)]
    对齐策略：指令按字节对齐，不补齐到 128-bit，高位补零至字节边界。
    """
    @staticmethod
    def pack_capsule(iq_name: str, iq_conf, payload: int, 
                    loops: int = 0, dly: int = 0, inc: int = 0, 
                    cond: int = 0, embed: int = 0, jump: int = 0,
                    embed_end: int = 0, inc_embed: int = 0) -> int:
        
        # 1. 封装 41-bit 头部 (Header)
        header = 0
        header |= (1 << 40)                    # VLD
        header |= ((jump & 1) << 39)
        header |= ((embed & 0x7) << 36)
        header |= ((embed_end & 0x3F) << 30)
        header |= ((loops & 0x3FF) << 20)
        header |= ((cond & 0x7F) << 13)
        header |= ((dly & 0x7) << 10)
        header |= ((inc & 0xF) << 6)
        header |= (inc_embed & 0x3F)

        # 2. 纵向级联 (Header 是 MSB, CODE 是 LSB)
        bits_code = iq_conf.BITS_CODE
        full_instr = (header << bits_code) | (payload & ((1 << bits_code) - 1))
        
        return full_instr

    @staticmethod
    def pack_to_bytes(val: int, total_bits: int) -> bytes:
        """
        核心物理逻辑：
        根据总位长计算字节数，并以 Little-endian 方式导出。
        如果总长不是 8 的倍数，int.to_bytes 会在最高位字节的高侧自动补零。
        """
        byte_len = (total_bits + 7) // 8
        return val.to_bytes(byte_len, byteorder='little')
