"""IQUnit DLY 语义回归测试（2026-10-19，INIT→DLY 合并验收引入）。

锁定 sim 与 RTL 一致的 dly 语义（smi_iq_dly_queen.v:479-511 +
smi_iq_iic.v:695-699：dly=k ⇒ 首 fire 一次性延迟 k 拍，后续 fire 不再延迟）：
  [INIT(vld=0, loops=L)] + [BODY(dly=0)]  ≡  [BODY(dly=L+1)]
两形 fire 时刻序列必须逐拍相同。

历史：该路径曾带潜伏 bug（计数器减到 0 后被 evaluate 重新初始化，marker 永不
置位 → dly≥1 的行永不 fire），因编译器从未发射 dly≠0 而零演练（公平对照
INIT→DLY 合并可行性检查点 ① 暴露，docs/fair_pair_init_fold_feasibility_20261019.md）。
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
# Only expose the package parent. Adding sim/smc_simulator itself shadows
# compiler core.* imports when this test is collected before synthesis tests.
sys.path.insert(0, str(_ROOT / "sim"))

from smc_simulator.control.iq_unit import IQUnit  # noqa: E402


class _FakeDecoder:
    payload_width = 8
    _layout = {"VLD": {"offset": 40}, "LOOPS": {"offset": 20}, "JUMP": {"offset": 39},
               "INC": {"offset": 6}, "DLY": {"offset": 10}, "EMBED_LOOPS": {"offset": 36},
               "EMBED_END": {"offset": 30}, "INC_EMBED": {"offset": 0}}

    def decode(self, raw):
        return {}


def _mkrow(vld, dly, loops, jump=0):
    return (vld << 40) | (jump << 39) | (loops << 20) | (dly << 10)


def _fire_schedule(rows, ncyc):
    """逐拍 evaluate+tick，返回 exec_active 为真的拍号（止于行耗尽）。"""
    iq = IQUnit("probe", decoder=_FakeDecoder())
    iq.memory = list(rows)
    fires = []
    for cyc in range(ncyc):
        iq.evaluate({}, cycle=cyc)
        if iq.exec_active and not iq.terminated:
            fires.append(cyc)
        iq.tick()
    return fires


class TestIQUnitDly(unittest.TestCase):
    def test_nop_row_occupies_loops_plus_1(self):
        # [NOP L2] + [BODY d0 L1]：NOP 占 3 拍，BODY 首 fire 在拍 3，共 2 fires
        fires = _fire_schedule([_mkrow(0, 0, 2), _mkrow(1, 0, 1)], 8)
        self.assertEqual(fires, [3, 4])

    def test_fold_equivalence_typical(self):
        # INIT(L2)+BODY(d0,L1) ≡ BODY(d3,L1)
        a = _fire_schedule([_mkrow(0, 0, 2), _mkrow(1, 0, 1)], 8)
        b = _fire_schedule([_mkrow(1, 3, 1)], 8)
        self.assertEqual(a, b)

    def test_fold_equivalence_boundary_dly1(self):
        # INIT(L0)+BODY(d0) ≡ BODY(d1)（l_init=0 假想下界）
        a = _fire_schedule([_mkrow(0, 0, 0), _mkrow(1, 0, 0)], 6)
        b = _fire_schedule([_mkrow(1, 1, 0)], 6)
        self.assertEqual(a, b)

    def test_fold_equivalence_max_dly7(self):
        # INIT(L6)+BODY(d0,L1) ≡ BODY(d7,L1)（BITS_DLY=3 上界）
        a = _fire_schedule([_mkrow(0, 0, 6), _mkrow(1, 0, 1)], 12)
        b = _fire_schedule([_mkrow(1, 7, 1)], 12)
        self.assertEqual(a, b)

    def test_dly_applies_once_not_per_iteration(self):
        # BODY(d2, L3)：首 fire 在拍 2，随后连续 3 fires（dly 不逐迭代重复）
        fires = _fire_schedule([_mkrow(1, 2, 3)], 8)
        self.assertEqual(fires, [2, 3, 4, 5])

    def test_dly_zero_unaffected(self):
        # 基线不漂移：dly=0 单行立即 fire
        fires = _fire_schedule([_mkrow(1, 0, 2)], 6)
        self.assertEqual(fires, [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
