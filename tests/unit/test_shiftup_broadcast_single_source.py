"""
ADR-023 D4.2 可证伪单测（ERRATA 取代 test_shiftup_data_from_instr_field）：
SHIFTUP 广播 data 来自单一 SMI 源（SMC_0），不是各 SMC 各读各的本地 bank。

广播 `a` 是流（64 字），装不进单条指令 128-bit immediate，故初稿「data 来自指令字段」
被推翻（HCO 三审 a7de85055575f69d7）。改守真正契约：方案 (B) 单源同拍 fan-out。

falsifiable：
- 若退化成方案 (C)（各 SMC 读自身本地 bank）→ follower local_data=0 会让 follower dr_d=0
  ≠ SMC_0 单源值 → fail。
- 若 fan-out 引入 per-SMC 相对延迟（非同拍）→ 同一拍采样 follower 拿不到 a_val → fail。
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT / "sim"))
sys.path.append(str(_ROOT / "src"))

from smc_simulator.system.cluster import SystemCluster
from smc_simulator.core.decoder import InstructionDecoder
from aiacc_hardware.src.spec_loader import SpecLoader


def _encode_shiftup(data: int, broadcast: int, smc_id_en: int) -> int:
    dec = InstructionDecoder(SpecLoader().get_unit("SHIFTUP"))
    lay = dec._layout
    raw = (1 << lay["VALID"]["offset"])
    raw |= (data & lay["data"]["mask"]) << lay["data"]["offset"]
    raw |= (broadcast & 1) << lay["broadcast"]["offset"]
    raw |= (smc_id_en & lay["smc_id_en"]["mask"]) << lay["smc_id_en"]["offset"]
    return raw


class TestShiftupBroadcastSingleSource(unittest.TestCase):
    SMC_NUM = 8

    def test_all_enabled_smc_same_value_from_single_source(self):
        """
        N=8，smc_id_en=7（全使能），SMC_0 单源 local_data=a_val，**follower local_data=0**
        （模拟 follower bank0 全 0）。断言：
          (1) 所有使能 SMC 同拍拿到相同 DR_SHIFTUP_D；
          (2) 该值 = SMC_0 单源值，follower bank0=0 不影响（专杀方案 C 各读本地源）。
        """
        cluster = SystemCluster(num_kernels=self.SMC_NUM)
        a_val = 0x1234_5678_9ABC_DEF0
        ctrl = _encode_shiftup(data=a_val, broadcast=1, smc_id_en=self.SMC_NUM - 1)

        for k in range(self.SMC_NUM):
            su = cluster.kernels[k].units["shiftup"]
            # follower local_data=0：若实现退化成「各读本地 bank」，follower 只会拿到 0
            su.evaluate({"ctrl": ctrl, "smc_id": k,
                         "cru_shiftup_i": 0,
                         "data": a_val if k == 0 else 0})
            su.tick()
        cluster._broadcast_shiftup_fanout()

        vals = [cluster.kernels[k].units["shiftup"].dr_d for k in range(self.SMC_NUM)]
        # (1) 所有使能 SMC 同拍同值
        self.assertEqual(len(set(vals)), 1,
            f"所有使能 SMC 应同拍拿到相同广播值，实际: {[hex(v) for v in vals]}")
        # (2) 该值 = SMC_0 单源值（follower local_data=0 不影响 → 证明单源 fan-out 非各读本地）
        self.assertEqual(vals[0], a_val, "SMC_0 单源值应 = a_val")
        for k in range(1, self.SMC_NUM):
            self.assertEqual(vals[k], a_val,
                f"SMC_{k} 应拿到 SMC_0 单源值 {hex(a_val)}（其本地 data=0 不影响）")


if __name__ == "__main__":
    unittest.main()
