from typing import Dict, Any, List
import sys
import os

# 确保导入路径
sys.path.append("/home/zhangjun/2_code/NPU/AIACC/smi_iq/aiacc_refactor")

from smc_simulator.core.base import Component
from smc_simulator.core.decoder import InstructionDecoder
from aiacc_hardware.src.spec_loader import SpecLoader

class ShiftDnUnit(Component):
    """
    SHIFTDN 单元（ADR-019 v6 重构，跨 SMC chain）。
    - 功能：链路下行（SMC_{smc_id_en} → ... → SMC_0）；OQ-5 确认无 broadcast 字段
    - 起点 SMC = SMC_{smc_id_en}（动态，由 SMT 解出 smc_id_en 决定）从本地 DVR_SHIFTDN_S 取数
    - 链中 SMC（SMC_ID < smc_id_en）从上游 CRU_SHIFTDN_I 取数
    - OQ-2 + OQ-8 (i)：沿途每 SMC 在 t=t0 同步发射
    - N=1 退化兼容：cru_shiftdn_i 缺省 0；dvr_shiftdn_s 仍从本 SMC RtoVR 出口取
    """
    def __init__(self, name: str, latency: int = 1):
        super().__init__(name)
        self.latency = latency

        loader = SpecLoader()
        spec = loader.get_unit("SHIFTDN")
        self.decoder = InstructionDecoder(spec)

        self.cru_o = 0       # CRU_SHIFTDN_O（链输出寄存器）
        self.regs_s0 = 0     # 保留旧 latch 行为（兼容 behavioral_semantics: pre_load_on_valid_or_nonzero）
        self._next_cru_o = 0

    def reset(self):
        self.cru_o = 0
        self.regs_s0 = 0
        self._next_cru_o = 0

    def evaluate(self, inputs: Dict[str, Any], cycle: int = 0):
        ctrl_code = inputs.get('ctrl', 0)
        smc_id = inputs.get('smc_id', 0)              # cluster 注入；N=1 默认 0
        chain_in = inputs.get('cru_shiftdn_i', 0)     # 来自 SMC_{smc_id+1}；N=1 默认 0
        # 兼容旧调用：'s' / 's_valid' 字段是 kernel.py 历史命名；新字段 'dvr_shiftdn_s' 是 ADR-019 D2 标准
        dvr_s = inputs.get('dvr_shiftdn_s', inputs.get('s', 0))
        dvr_s_valid = inputs.get('dvr_shiftdn_s_valid', inputs.get('s_valid', False))

        # 同步锁存（保旧 behavioral_semantics: pre_load_on_valid_or_nonzero）
        if dvr_s_valid or dvr_s != 0:
            self.regs_s0 = dvr_s

        instr = self.decoder.decode(ctrl_code)

        if instr.VALID != "VALID":
            self._next_cru_o = self.cru_o             # Hold
            return

        smc_id_en = instr.smc_id_en                   # 小写

        # 起点 SMC：取本地 DVR_SHIFTDN_S
        # 链中 SMC：取上游 CRU_SHIFTDN_I
        if smc_id == smc_id_en:
            self._next_cru_o = self.regs_s0 & ((1 << 128) - 1)
        elif smc_id < smc_id_en:
            self._next_cru_o = chain_in & ((1 << 128) - 1)
        else:
            self._next_cru_o = self.cru_o             # 不在链上

    def tick(self):
        self.cru_o = self._next_cru_o

    def get_outputs(self) -> Dict[str, int]:
        return {'O': self.cru_o, 'CRU_SHIFTDN_O': self.cru_o}

    # 跨 SMC chain wire 接口（cluster.py D5 调用）
    def get_chain_output(self) -> int:
        return self.cru_o
