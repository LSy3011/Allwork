"""
ADR-053 D6：VIN/VOUT 绕行流水寄存器的模拟器模型（IPR/OPR，1 拍 vld 门控寄存器）。

RTL 语义取证：
- IPR.sv:87 / OPR.sv:87 "One cycle delay"——1 拍流水寄存器；
- IPR.sv:74-76 per-SMC 门控：`vld_eff = vld && (smc_id_en >= SMC_ID)`（阈值语义，
  与 ADR-031 全单元 smc_id_en 约定一致；kernel 的 smc_id 由 cluster 注入）；
- 数据入口 = RtoVR 输出端口（VIN0_S=14 / VIN1_S=15 / VOUT0_S=16，固定直连）；
- 数据出口 = RtoVR 输入源（VIN0_D=11 / VIN1_D=12 / VOUT0_D=13），由 kernel
  `_collect_sources` 的通用 `DR_<NAME>_D` 分支自动拾取（get_outputs()['D']）。

时序契约（与 SMT 的 rtovr latch+1 / delays[VIN]=1 对齐）：
  拍 C：rtovr-<S> 指令路由 UR→端口 S（C+1 拍端口可见，OUTPUT_PIPE_STAGE=1）
  拍 C+1：本单元 vld 点火，锁存端口 S 的值
  拍 C+2：dr_d 可被 RtoVR 当源（sel 11/12/13）路由给下游
"""

from typing import Dict, Any

from smc_simulator.core.base import Component
from smc_simulator.core.decoder import InstructionDecoder
from aiacc_hardware.src.spec_loader import SpecLoader


class RegPipeUnit(Component):
    """VIN/VOUT 通用模型：vld 门控 1 拍寄存器 + per-SMC smc_id_en 阈值门控。"""

    def __init__(self, name: str):
        super().__init__(name)
        self.latency = 1
        loader = SpecLoader()
        spec = loader.get_unit(name.upper())
        self.decoder = InstructionDecoder(spec) if spec else None
        self.dr_d = 0
        self._next_dr_d = 0
        self.smc_id = 0  # kernel 构造后统一注入（kernel.py:118-121）

    def reset(self):
        self.dr_d = 0
        self._next_dr_d = 0

    def evaluate(self, inputs: Dict[str, Any], cycle: int = 0):
        ctrl_code = inputs.get('ctrl', 0)
        instr = self.decoder.decode(ctrl_code) if (self.decoder and ctrl_code) else None

        valid = False
        smc_id_en = 0
        if instr is not None:
            valid = (getattr(instr, 'VALID', None) in ("VALID", "VLD", 1, "1", True))
            smc_id_en = getattr(instr, 'smc_id_en', None)
            if smc_id_en is None:
                smc_id_en = getattr(instr, 'SMC_ID_EN', 0)
            smc_id_en = int(smc_id_en)

        # IPR.sv:74-76：vld_eff = vld && (smc_id_en >= SMC_ID)
        vld_eff = valid and (smc_id_en >= self.smc_id)

        if vld_eff:
            self._next_dr_d = inputs.get('s', 0)
            print(f">>> [AUDIT-DATA] SMC_ID:{self.smc_id} Cyc:{cycle} "
                  f"Unit:{self.name.upper()} Tag:REGPIPE D0:{self._next_dr_d:032x}")
        # vld_eff=0 时保持（寄存器语义，非清零）
        else:
            self._next_dr_d = self.dr_d

    def tick(self):
        self.dr_d = self._next_dr_d

    def get_outputs(self) -> Dict[str, int]:
        return {'D': self.dr_d}

    def get_regs(self) -> Dict[str, int]:
        return {'D': self.dr_d} if self.dr_d != 0 else {}
