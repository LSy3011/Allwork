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
from smc_simulator.utils.fp_utils import fp32_add_hw

from smc_simulator.core.behavioral_engine import BehavioralEngine

class FP32AddTree5To1Unit(Component):
    """
    重构后的 FP32 加法树 5-to-1 单元。
    支持同步锁存和集成行为语义引擎。
    """
    def __init__(self, name: str, latency: int = 4):
        super().__init__(name)
        self.latency = latency
        
        loader = SpecLoader()
        spec = loader.get_unit("FP32ADDTREE5TO1")
        self.decoder = InstructionDecoder(spec)
        self.behavior_engine = BehavioralEngine(spec)
        
        # 内部输入锁存寄存器 (8个 128-bit)
        self.regs_s = [0] * 8
        # 架构状态：8 个 FP32 累加器，D0 存低 16bit，D1 存高 16bit
        self.dr_d0 = 0
        self.dr_d1 = 0
        
        # 流水线：latency 级，每级 {d0, d1, valid}
        self.pipeline = [{'d0': 0, 'd1': 0, 'valid': False} for _ in range(latency)]
        self._next_pipeline = []
        self._next_dr_d0 = 0
        self._next_dr_d1 = 0

    def reset(self):
        self.regs_s = [0] * 8
        self.dr_d0 = 0
        self.dr_d1 = 0
        self.pipeline = [{'d0': 0, 'd1': 0, 'valid': False} for _ in range(self.latency)]

    def _get_val_bits_at(self, sources: List[int], y: int) -> int:
        val_bits = 0
        for i in range(8):
            slice_4b = (sources[i] >> (y * 4)) & 0xF
            val_bits |= (slice_4b << (i * 4))
        return val_bits & 0xFFFFFFFF

    def evaluate(self, inputs: Dict[str, Any], cycle: int = 0):
        ctrl_code = inputs.get('ctrl', 0)
        instr = self.decoder.decode(ctrl_code)
        valid_trigger = (instr.VALID == "VALID")
        
        # 1. 同步锁存：解耦数据锁存与指令执行 (对齐旧版逻辑)
        # 优先检查 valid 标志 (RtoVR 显式路由)，否则回退到 != 0
        for i in range(8):
            s_key = f's{i}'
            valid_key = f's{i}_valid'
            val = inputs.get(s_key, 0)
            
            should_latch = False
            if valid_key in inputs:
                if inputs[valid_key]: should_latch = True
            elif val != 0:
                should_latch = True
                
            if should_latch:
                self.regs_s[i] = val

        # 2. 旁路累加基值 (对齐旧版逻辑)
        base_d0 = self.dr_d0
        base_d1 = self.dr_d1
        for stage in self.pipeline:
            if stage['valid']:
                base_d0 = stage['d0']
                base_d1 = stage['d1']
                break
        
        accs_bits = []
        for x in range(8):
            d0_p = (base_d0 >> (x * 16)) & 0xFFFF
            d1_p = (base_d1 >> (x * 16)) & 0xFFFF
            accs_bits.append((d1_p << 16) | d0_p)

        res_d0 = base_d0
        res_d1 = base_d1
        
        if valid_trigger:
            temp_d0 = 0
            temp_d1 = 0
            for x in range(8):
                current_acc = accs_bits[x]
                for offset in range(4):
                    y = 4 * x + offset
                    in_val = self._get_val_bits_at(self.regs_s, y)
                    current_acc = fp32_add_hw(current_acc, in_val)
                
                temp_d0 |= (current_acc & 0xFFFF) << (x * 16)
                temp_d1 |= ((current_acc >> 16) & 0xFFFF) << (x * 16)
            
            res_d0 = temp_d0 & ((1 << 128) - 1)
            res_d1 = temp_d1 & ((1 << 128) - 1)

        # 3. 流水线压入
        self._next_pipeline = [{'d0': res_d0, 'd1': res_d1, 'valid': valid_trigger}] + self.pipeline[:-1]
        
        # 4. 写回级 (T + Latency - 1)
        last_forward = self._next_pipeline[-1]
        if last_forward['valid']:
            self._next_dr_d0 = last_forward['d0']
            self._next_dr_d1 = last_forward['d1']
        else:
            self._next_dr_d0 = self.dr_d0
            self._next_dr_d1 = self.dr_d1

    def tick(self):
        self.pipeline = self._next_pipeline
        self.dr_d0 = self._next_dr_d0
        self.dr_d1 = self._next_dr_d1

    def get_outputs(self) -> Dict[str, int]:
        return {'D0': self.dr_d0, 'D1': self.dr_d1}
