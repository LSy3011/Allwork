"""
ADR-023 D4.1 可证伪单测：SHIFTUP broadcast 的 smc_id_en 门控真生效。

守 ADR-022:135 契约「未被 smc_id_en 使能的 SMC 不执行该指令」。模型 A（lockstep 复制）
若把 smc_id_en 门控偷偷绕过，本测试失败。

falsifiable：
- 若 cluster._broadcast_shiftup_fanout 去掉 `k <= smc_id_en` 门控 → SMC 4..7 会拿到广播值 → fail。
- 若 fan-out 整体缺失 → 使能 follower(SMC 1..3) 的 dr_d 停在自身 broadcast 路径的 0 → fail。
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
    """用 SHIFTUP decoder layout 反向编码一条 VALID 微指令 ctrl_code。"""
    dec = InstructionDecoder(SpecLoader().get_unit("SHIFTUP"))
    lay = dec._layout
    raw = (1 << lay["VALID"]["offset"])  # VALID=1 → "VALID"
    raw |= (data & lay["data"]["mask"]) << lay["data"]["offset"]
    raw |= (broadcast & 1) << lay["broadcast"]["offset"]
    raw |= (smc_id_en & lay["smc_id_en"]["mask"]) << lay["smc_id_en"]["offset"]
    return raw


def _drive_one_broadcast_cycle(cluster, ctrl, a_val, smc_num):
    """模拟 cluster.step 的一拍：各 SMC shiftup.evaluate+tick，再 broadcast fan-out。
    SMC_0 单源 local_data=a_val；follower local_data=0（模拟 follower bank0 全 0）。"""
    for k in range(smc_num):
        su = cluster.kernels[k].units["shiftup"]
        su.evaluate({"ctrl": ctrl, "smc_id": k,
                     "cru_shiftup_i": 0,
                     "data": a_val if k == 0 else 0})
        su.tick()
    cluster._broadcast_shiftup_fanout()


class TestSmcIdEnGating(unittest.TestCase):
    SMC_NUM = 8

    def test_broadcast_smc_id_en_3_gates_followers(self):
        """smc_id_en=3 @ N=8：SMC 0..3 拿到广播值；SMC 4..7 不被使能，dr_d 保持 0。"""
        cluster = SystemCluster(num_kernels=self.SMC_NUM)
        a_val = 0xCAFE
        ctrl = _encode_shiftup(data=a_val, broadcast=1, smc_id_en=3)

        _drive_one_broadcast_cycle(cluster, ctrl, a_val, self.SMC_NUM)

        # 使能 SMC 0..3 同拍拿到单源广播值
        for k in range(4):
            self.assertEqual(cluster.kernels[k].units["shiftup"].dr_d, a_val,
                f"SMC_{k}（<=smc_id_en=3）应拿到广播值 {hex(a_val)}")
        # 未使能 SMC 4..7 保持 0（门控）
        for k in range(4, self.SMC_NUM):
            self.assertEqual(cluster.kernels[k].units["shiftup"].dr_d, 0,
                f"SMC_{k}（>smc_id_en=3）未使能，dr_d 应保持 0（门控真生效）")


if __name__ == "__main__":
    unittest.main()
