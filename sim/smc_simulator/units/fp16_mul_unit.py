from typing import Dict, Any, List
import sys
import os

# 确保导入路径
sys.path.append("/home/zhangjun/2_code/NPU/AIACC/smi_iq/aiacc_refactor")

from smc_simulator.core.base import Component
from smc_simulator.core.decoder import InstructionDecoder
from aiacc_hardware.src.spec_loader import SpecLoader

# 导入硬件精确计算函数
sys.path.append("/home/zhangjun/2_code/NPU/AIACC/smi_iq")
from smc_simulator.utils.fp_utils import fp16_mul_to_fp32
from smc_simulator.core.behavioral_engine import BehavioralEngine

class FP16MulUnit(Component):
    """
    FP16 Floating Point Multiplier 单元（v0.4.19 表512-514，功能 5.3.3）。

    真结构 = S0-S7 八源 → D0-D7 八输出：
      - 输入 S0-S7：各 128bit = 8×FP16 lane。
      - 输出 D0-D7：各 128bit = 4×FP32 lane（FP16×FP16→FP32，精确）。
      - 4 对源 ×（低 4 lane + 高 4 lane）= 32 个 FP16 乘 / 指令：
          D0[x]=S0[x]*S4[x],   D1[x]=S0[x+4]*S4[x+4]   (x=0..3)
          D2[x]=S1[x]*S5[x],   D3[x]=S1[x+4]*S5[x+4]
          D4[x]=S2[x]*S6[x],   D5[x]=S2[x+4]*S6[x+4]
          D6[x]=S3[x]*S7[x],   D7[x]=S3[x+4]*S7[x+4]
    latency=4。支持“数据先到、指令后发”的同步锁存。
    """
    def __init__(self, name: str, latency: int = None):
        super().__init__(name)

        loader = SpecLoader()
        spec = loader.get_unit("FP16MUL")
        self.decoder = InstructionDecoder(spec)
        self.behavior_engine = BehavioralEngine(spec)

        if latency is None:
            latency = spec.latency or 4
        self.latency = latency

        # pipeline 每级保存 8 个 D 输出（D0..D7）
        self.pipeline = [{'results': [0] * 8, 'valid': False} for _ in range(latency)]
        self.dr = [0] * 8        # D0..D7 锁存
        self.regs = [0] * 8      # S0..S7 输入寄存器
        self._next_pipeline = []
        self._next_dr = [0] * 8

    def reset(self):
        self.pipeline = [{'results': [0] * 8, 'valid': False} for _ in range(self.latency)]
        self.dr = [0] * 8
        self.regs = [0] * 8

    def evaluate(self, inputs: Dict[str, Any], cycle: int = 0):
        ctrl_code = inputs.get('ctrl', 0)

        # 1. 同步锁存：支持“数据先到、指令后发”模式，逐源 S0-S7
        for i in range(8):
            v = inputs.get(f's{i}', 0)
            if inputs.get(f's{i}_valid', False) or v != 0:
                self.regs[i] = v

        instr = self.decoder.decode(ctrl_code)
        valid_trigger = (instr.VALID == "VALID")

        results = [0] * 8
        if valid_trigger:
            results = self._compute()

        self._next_pipeline = [{'results': results, 'valid': valid_trigger}] + self.pipeline[:-1]

        # 写回逻辑：保持模式
        last_stage = self._next_pipeline[-1]
        if last_stage['valid']:
            self._next_dr = list(last_stage['results'])
        else:
            self._next_dr = list(self.dr)

    def _compute(self) -> List[int]:
        """4 对源 → 8 个 D 输出，每 D = 4×FP32（128bit）。"""
        layout = self.behavior_engine.get_layout("FP16")
        width = layout.get('width', 16)     # FP16 lane 宽
        mask16 = (1 << width) - 1
        fp32w = 32                          # 输出 FP32 lane 宽

        out = [0] * 8
        for k in range(4):
            lo_src = self.regs[k]           # S_k   （低源）
            hi_src = self.regs[k + 4]       # S_{k+4}（高源）

            # D(2k)[x]   = lo[x]   * hi[x]   , x=0..3
            d_lo = 0
            for x in range(4):
                a = (lo_src >> (x * width)) & mask16
                b = (hi_src >> (x * width)) & mask16
                d_lo |= (fp16_mul_to_fp32(a, b) << (x * fp32w))
            out[2 * k] = d_lo

            # D(2k+1)[x] = lo[x+4] * hi[x+4] , x=0..3
            d_hi = 0
            for x in range(4):
                a = (lo_src >> ((x + 4) * width)) & mask16
                b = (hi_src >> ((x + 4) * width)) & mask16
                d_hi |= (fp16_mul_to_fp32(a, b) << (x * fp32w))
            out[2 * k + 1] = d_hi

        return out

    def tick(self):
        self.pipeline = self._next_pipeline
        self.dr = self._next_dr

    def get_outputs(self) -> Dict[str, int]:
        return {f'D{i}': self.dr[i] for i in range(8)}
