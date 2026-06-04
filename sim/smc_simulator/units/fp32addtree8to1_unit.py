from typing import Dict, Any, List
import sys
import os
import struct

# 确保导入路径
sys.path.append("/home/zhangjun/2_code/NPU/AIACC/smi_iq/aiacc_refactor")

from smc_simulator.core.base import Component
from smc_simulator.core.decoder import InstructionDecoder
from aiacc_hardware.src.spec_loader import SpecLoader


# FP32 <-> uint32_t bit-cast helpers（与 fp32_add_tree_unit.py 一致）
def u2f(u: int) -> float:
    return struct.unpack('<f', struct.pack('<I', u & 0xFFFFFFFF))[0]

def f2u(f: float) -> int:
    return struct.unpack('<I', struct.pack('<f', f))[0]

def to_f32(f: float) -> float:
    return struct.unpack('<f', struct.pack('<f', f))[0]


class FP32AddTree8To1Unit(Component):
    """
    FP32 加法树 8-to-1 单元（v0.4.19 表520）。

    功能：S0、S1 各 4×FP32（128bit），将 **8 个 FP32 求和 → 1 标量**，
    写到 D 的第 dst_id 个 32-bit lane（dst_id 2bit，0..3）。

    与 FP16MUL 接线：S0=D0(4×FP32)、S1=D1(4×FP32) → 得 K=8 点积。

    求和顺序 = 平衡加法树（“AddTree” 语义）：
        l0=S0[0]+S0[1]  l1=S0[2]+S0[3]  l2=S1[0]+S1[1]  l3=S1[2]+S1[3]
        m0=l0+l1        m1=l2+l3
        sum=m0+m1
    每步按 FP32 round-to-nearest（to_f32）取整，bit-exact。
    ⚠ 树形顺序为依 “AddTree” 命名的合理假设；RTL 实际拓扑待 VCS 复核
      （ADR-032 still_holds: proposed）。golden 端镜像同序保证 bit-perfect。
    """
    def __init__(self, name: str, latency: int = None):
        super().__init__(name)

        loader = SpecLoader()
        spec = loader.get_unit("FP32ADDTREE8TO1")
        if latency is None:
            if isinstance(spec, dict):
                latency = spec.get("latency", 3)
            else:
                latency = getattr(spec, "latency", 3) or 3
        self.latency = latency

        self.decoder = InstructionDecoder(spec)

        self.dr_d = 0
        # Pipeline stores assembled 128-bit value + mask for partial-lane update
        self.pipeline = [{'val': 0, 'mask': 0, 'valid': False} for _ in range(self.latency)]
        self._next_pipeline = []
        self._next_dr_d = 0

    def reset(self):
        self.dr_d = 0
        self.pipeline = [{'val': 0, 'mask': 0, 'valid': False} for _ in range(self.latency)]
        self._next_pipeline = []
        self._next_dr_d = 0

    def _reduce8(self, s0: int, s1: int) -> int:
        """S0、S1 各 4×FP32 → 平衡树求和 → 1 个 FP32（uint32 bits）。"""
        v = [(s0 >> (y * 32)) & 0xFFFFFFFF for y in range(4)] + \
            [(s1 >> (y * 32)) & 0xFFFFFFFF for y in range(4)]
        l0 = to_f32(u2f(v[0]) + u2f(v[1]))
        l1 = to_f32(u2f(v[2]) + u2f(v[3]))
        l2 = to_f32(u2f(v[4]) + u2f(v[5]))
        l3 = to_f32(u2f(v[6]) + u2f(v[7]))
        m0 = to_f32(l0 + l1)
        m1 = to_f32(l2 + l3)
        s = to_f32(m0 + m1)
        return f2u(s)

    def evaluate(self, inputs: Dict[str, Any], cycle: int = 0):
        ctrl = inputs.get('ctrl', 0)
        instr = self.decoder.decode(ctrl)

        pipeline_item = {'val': 0, 'mask': 0, 'valid': False}
        mask128 = (1 << 128) - 1

        if instr.VALID == "VALID":
            s0 = inputs.get('s0', 0) & mask128
            s1 = inputs.get('s1', 0) & mask128
            # Decoder returns int value for unmapped fields like DST_REG
            x = int(instr.DST_REG)

            res_bits = self._reduce8(s0, s1) & 0xFFFFFFFF
            val_shifted = (res_bits << (x * 32)) & mask128
            mask_shifted = (0xFFFFFFFF << (x * 32)) & mask128
            pipeline_item = {'val': val_shifted, 'mask': mask_shifted, 'valid': True}

        self._next_pipeline = [pipeline_item] + self.pipeline[:-1]

        # Writeback Logic（部分 lane 更新，保持其余 lane）
        last = self.pipeline[-1]
        if last['valid']:
            old = self.dr_d & mask128
            mask = last['mask'] & mask128
            new_p = last['val'] & mask128
            self._next_dr_d = (old & ~mask) | (new_p & mask)
        else:
            self._next_dr_d = self.dr_d

    def tick(self):
        self.pipeline = self._next_pipeline
        self.dr_d = self._next_dr_d

    def get_outputs(self):
        last = self.pipeline[-1]
        mask128 = (1 << 128) - 1
        if last['valid']:
            old = self.dr_d & mask128
            m = last['mask'] & mask128
            n = last['val'] & mask128
            return {'D': (old & ~m) | (n & m)}
        return {'D': self.dr_d}
