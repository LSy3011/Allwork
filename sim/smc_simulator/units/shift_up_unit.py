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
    SHIFTUP 单元（ADR-023 D2 方案 B：单源同拍 fan-out；supersede ADR-019 逐级 chain）。
    - 功能：broadcast / P2P 暴露 DR_SHIFTUP_D
    - ADR-023 D2(B)：广播 `a` 是流，SMC_0 bank0 单源经 ur-r0 读出 `a[i]`；broadcast 时由
      cluster 同拍 fan-out 到所有使能 SMC 的 DR_SHIFTUP_D（见 cluster._broadcast_shiftup_fanout），
      **abolish ADR-019 `_propagate_chain_inputs` 的逐级 1-cycle 相对延迟**（与 D1 删 stagger 绑定，
      否则 lockstep FPMUL 读到错位 stale `a`）。本单元 evaluate 记录广播状态供 cluster fan-out。
    - 旧 OQ-1「数据源固定 SMC_0 + 逐级 chain 传播」语义已被 D2(B) 取代（HCO 三审 a7de85055575f69d7）。
    - N=1 退化兼容：cru_shiftup_i 缺省 0；data 取本地 R0；smc_id_en=0 时等价旧 "本地直通"。
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
        # ADR-023 D2(B)：本拍广播状态，供 cluster._broadcast_shiftup_fanout 读 SMC_0 单源
        self.bcast_active = False    # 本拍是否在 broadcast（VALID && broadcast==1）
        self.bcast_smc_id_en = 0     # 本拍使能上界

    def reset(self):
        self.cru_o = 0
        self.dr_d = 0
        self._next_cru_o = 0
        self._next_dr_d = 0
        self.bcast_active = False
        self.bcast_smc_id_en = 0

    def evaluate(self, inputs: Dict[str, Any], cycle: int = 0):
        ctrl_code = inputs.get('ctrl', 0)
        smc_id = inputs.get('smc_id', 0)             # cluster 注入；N=1 默认 0
        chain_in = inputs.get('cru_shiftup_i', 0)    # 来自 SMC_{smc_id-1}；N=1 默认 0
        local_data = inputs.get('data', 0)           # SMC_0 chain head 本地数据源

        instr = self.decoder.decode(ctrl_code)

        if instr.VALID != "VALID":
            self._next_cru_o = self.cru_o            # Hold
            self._next_dr_d = self.dr_d
            self.bcast_active = False
            return

        smc_id_en = instr.smc_id_en                  # 小写
        broadcast = instr.broadcast                  # 小写：0=P2P, 1=BROADCAST（int 或字符串）
        is_broadcast = (broadcast == 1) or (broadcast == "BROADCAST")

        # ADR-023 D2(B)：记录本拍广播状态供 cluster fan-out（读 SMC_0 单源）
        self.bcast_active = is_broadcast
        self.bcast_smc_id_en = int(smc_id_en) if isinstance(smc_id_en, int) else smc_id_en

        # cru_o：SMC_0 取本地单源 data（ur-r0 读出的 a[i]）；其他 SMC 的 cru_o 在 broadcast 下
        # 由 cluster fan-out 覆盖（D2(B)），此处仅给非 broadcast / 退化路径用。
        if smc_id <= smc_id_en:
            if smc_id == 0:
                self._next_cru_o = local_data & ((1 << 128) - 1)
            else:
                self._next_cru_o = chain_in & ((1 << 128) - 1)
        else:
            self._next_cru_o = self.cru_o            # 未使能，保持

        # Endpoint / broadcast 暴露 DR_SHIFTUP_D（broadcast follower 的 dr_d 同样由 cluster fan-out 覆盖）
        is_endpoint = (smc_id == smc_id_en)
        if (not is_broadcast and is_endpoint) or (is_broadcast and smc_id <= smc_id_en):
            self._next_dr_d = self._next_cru_o
        else:
            self._next_dr_d = self.dr_d              # 未使能/旁路：保持上一拍

    def tick(self):
        self.cru_o = self._next_cru_o
        self.dr_d = self._next_dr_d

    def get_outputs(self) -> Dict[str, int]:
        return {'D': self.dr_d, 'CRU_SHIFTUP_O': self.cru_o}

    # 跨 SMC chain wire 接口（cluster.py D5 调用）
    def get_chain_output(self) -> int:
        return self.cru_o
