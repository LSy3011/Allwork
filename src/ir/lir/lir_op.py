from typing import List, Dict, Tuple, Optional, Any
import struct
from ..base import IRNode

class LIROp(IRNode):
    """
    LIR (Low-level IR): 物理指令层。
    1 LIR 节点 = 1条 41-bit 物理微指令包。
    """
    def __init__(self, abs_cycle: int, iq_id: int, 
                 vld: bool = True, loops: int = 1, dly: int = 0, inc: int = 0, 
                 code: int = 0, trace_id: str = ""):
        super().__init__(trace_id=trace_id)
        self.abs_cycle = abs_cycle
        self.iq_id = iq_id
        self.vld = vld
        self.loops = loops
        self.dly = dly
        self.inc = inc
        self.code = code  # 模块特定的 micro-code bits
        
    def encode_41bit(self) -> bytes:
        """
        暂时返回 6 字节二进制（48 bits 补 0），后续对齐 41-bit 精确布局。
        
        [Preamble(vld, loops, dly, inc)][Micro-code bits]
        """
        raw = 0
        raw |= (1 if self.vld else 0) << 40
        raw |= (self.loops & 0xFF) << 32
        raw |= (self.dly & 0xF) << 28
        raw |= (self.inc & 0xF) << 24
        raw |= (self.code & 0xFFFFFF) # 暂时占位 24-bit
        
        # 使用 big-endian 导出 6 字节 (需精确对齐硬件字节序)
        return struct.pack(">Q", raw)[-6:] # 取最后 6 字节

    def __repr__(self):
        return (f"LIROp(T={self.abs_cycle} IQ={self.iq_id} "
                f"vld={self.vld} loops={self.loops} dly={self.dly} inc={self.inc} code=0x{self.code:0X})")
