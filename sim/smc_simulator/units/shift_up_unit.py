from typing import Dict, Any, List
import sys
import os

# 确保导入路径
sys.path.append("/home/zhangjun/2_code/NPU/AIACC/smi_iq/aiacc_refactor")

from smc_simulator.core.base import Component
from smc_simulator.core.decoder import InstructionDecoder
from aiacc_hardware.src.spec_loader import SpecLoader

class ShiftUpUnit(Component):
    """
    SHIFTUP 单元（ADR-019 v6 重构，跨 SMC chain）。
    - 功能：链路上行（SMC_0 → ... → SMC_{smc_id_en}）+ broadcast / P2P 暴露 DR_SHIFTUP_D
    - OQ-1：数据源固定 SMC_0；其他 SMC 接上游 CRU_SHIFTUP_I
    - OQ-2 + OQ-8 (i)：沿途每 SMC 在 t=t0 同步发射；硬件层 IQ slot 占用由编译器侧 axiom 守
    - N=1 退化兼容：cru_shiftup_i 缺省 0；data 仍取本地 R0；smc_id_en=0 时等价旧 "本地直通"
    """
    def __init__(self, name: str, latency: int = 1):
        super().__init__(name)
        self.latency = latency

        loader = SpecLoader()
        spec = loader.get_unit("SHIFTUP")
        self.decoder = InstructionDecoder(spec)

        self.cru_o = 0       # CRU_SHIFTUP_O（链输出寄存器）
        self.dr_d = 0        # DR_SHIFTUP_D（RtoVR 入口寄存器）
        self._next_cru_o = 0
        self._next_dr_d = 0

    def reset(self):
        self.cru_o = 0
        self.dr_d = 0
        self._next_cru_o = 0
        self._next_dr_d = 0

    def evaluate(self, inputs: Dict[str, Any], cycle: int = 0):
        ctrl_code = inputs.get('ctrl', 0)
        smc_id = inputs.get('smc_id', 0)             # cluster 注入；N=1 默认 0
        chain_in = inputs.get('cru_shiftup_i', 0)    # 来自 SMC_{smc_id-1}；N=1 默认 0
        local_data = inputs.get('data', 0)           # SMC_0 chain head 本地数据源

        instr = self.decoder.decode(ctrl_code)

        if instr.VALID != "VALID":
            self._next_cru_o = self.cru_o            # Hold
            self._next_dr_d = self.dr_d
            return

        smc_id_en = instr.smc_id_en                  # 小写
        broadcast = instr.broadcast                  # 小写：0=P2P, 1=BROADCAST（int 或字符串）

        # Chain propagation：OQ-1 数据源 SMC_0
        if smc_id <= smc_id_en:
            if smc_id == 0:
                self._next_cru_o = local_data & ((1 << 128) - 1)
            else:
                self._next_cru_o = chain_in & ((1 << 128) - 1)
        else:
            self._next_cru_o = self.cru_o            # 不在链上，保持

        # Endpoint / broadcast 暴露 DR_SHIFTUP_D
        # broadcast 字段可能解出 int (0/1) 或 str ("P2P"/"BROADCAST")
        is_broadcast = (broadcast == 1) or (broadcast == "BROADCAST")
        is_endpoint = (smc_id == smc_id_en)
        if (not is_broadcast and is_endpoint) or (is_broadcast and smc_id <= smc_id_en):
            self._next_dr_d = self._next_cru_o
        else:
            self._next_dr_d = self.dr_d              # 保持上一拍

    def tick(self):
        self.cru_o = self._next_cru_o
        self.dr_d = self._next_dr_d

    def get_outputs(self) -> Dict[str, int]:
        return {'D': self.dr_d, 'CRU_SHIFTUP_O': self.cru_o}

    # 跨 SMC chain wire 接口（cluster.py D5 调用）
    def get_chain_output(self) -> int:
        return self.cru_o
