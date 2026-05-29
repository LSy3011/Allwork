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
    SHIFTDN 单元（ADR-019 errata task #19，严格按新硬件 spec 表 545-547）。
    - microinstruction = vld(1) + data(128) = 129 bit
    - 寄存器名 CRD_SHIFTDN_O/I（下行，区别于 SHIFTUP 的 CRU）
    - **spec gap**：功能公式用 smc_id_en 但 spec 表均未列；本 sim fallback PARAM_SMC_NUM-1
      （ADR-023 D6：SystemCluster 按实际 num_kernels-1 注入 self.smc_id_en_fallback，替代硬编码 31；
      避免 8-SMC 下 31 越界死常量。完整修正 = 从指令字段解码 smc_id_en，defer 到 ADR-022 待办 #3）
    - N=1 退化兼容：cru_shiftdn_i / crd_shiftdn_i 缺省 0；dvr_shiftdn_s 仍从本 SMC RtoVR 出口取
    """
    SMC_ID_EN_FALLBACK = 31  # 类默认 (PARAM_SMC_NUM-1, 32-era)；cluster 多 SMC 路径会按实际规模覆盖

    def __init__(self, name: str, latency: int = 1):
        super().__init__(name)
        self.latency = latency

        loader = SpecLoader()
        spec = loader.get_unit("SHIFTDN")
        self.decoder = InstructionDecoder(spec)

        self.cru_o = 0       # CRD_SHIFTDN_O（输出寄存器；属性名保留 cru_o 兼容历史调用）
        self.regs_s0 = 0     # 保留旧 latch 行为（兼容 behavioral_semantics: pre_load_on_valid_or_nonzero）
        self._next_cru_o = 0
        # ADR-023 D6：实例级 fallback，SystemCluster 按 num_kernels-1 覆盖（派生自真实 SMC 规模）
        self.smc_id_en_fallback = self.SMC_ID_EN_FALLBACK

    def reset(self):
        self.cru_o = 0
        self.regs_s0 = 0
        self._next_cru_o = 0

    def evaluate(self, inputs: Dict[str, Any], cycle: int = 0):
        ctrl_code = inputs.get('ctrl', 0)
        smc_id = inputs.get('smc_id', 0)
        # 兼容新命名 crd_shiftdn_i + 旧命名 cru_shiftdn_i（向后兼容期）
        chain_in = inputs.get('crd_shiftdn_i', inputs.get('cru_shiftdn_i', 0))
        # 兼容旧调用：'s' / 's_valid' 是 kernel.py 历史命名；新字段 'dvr_shiftdn_s' 是 ADR-019 D2 标准
        dvr_s = inputs.get('dvr_shiftdn_s', inputs.get('s', 0))
        dvr_s_valid = inputs.get('dvr_shiftdn_s_valid', inputs.get('s_valid', False))

        # 同步锁存（保旧 behavioral_semantics: pre_load_on_valid_or_nonzero）
        if dvr_s_valid or dvr_s != 0:
            self.regs_s0 = dvr_s

        instr = self.decoder.decode(ctrl_code)

        if instr.VALID != "VALID":
            self._next_cru_o = self.cru_o
            return

        # spec gap：SHIFTDN microinstruction 未列 smc_id_en；fallback 到 PARAM_SMC_NUM-1
        # （ADR-023 D6：实例级 fallback 由 cluster 按真实 num_kernels-1 注入；完整修正 = 解码指令字段）
        smc_id_en = self.smc_id_en_fallback

        # 功能公式（spec 完整描述）
        if smc_id == smc_id_en:
            self._next_cru_o = self.regs_s0 & ((1 << 128) - 1)
        elif smc_id < smc_id_en:
            self._next_cru_o = chain_in & ((1 << 128) - 1)
        else:
            self._next_cru_o = self.cru_o

    def tick(self):
        self.cru_o = self._next_cru_o

    def get_outputs(self) -> Dict[str, int]:
        return {'O': self.cru_o, 'CRD_SHIFTDN_O': self.cru_o, 'CRU_SHIFTDN_O': self.cru_o}

    # 跨 SMC chain wire 接口（cluster.py D5 调用）
    def get_chain_output(self) -> int:
        return self.cru_o
