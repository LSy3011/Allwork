"""跨-SMC 分散布局模型守门（按架构文档 v0.4.19 §1071/1073 验证）。

守：① 连续/间插展开 placement 精确匹配 spec 公式；② distribute→gather roundtrip 复原原张量；
③ 大 K 均匀分散到 8 SMC（每 SMC K/8，= 大 K 放进独立 32KB 的布局基础）；④ 连续≠间插（可证伪）。"""
import sys
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.cross_smc_layout import (CrossSmcLayout, Unroll,  # noqa: E402
                                   ur_vec_tran_succ, ur_vec_tran_inte)


class TestCrossSmcLayout(unittest.TestCase):
    def test_contiguous_matches_spec_1071(self):
        """§1071 连续展开：D3=16,N=8 → chunk=2，SMC_x 持 [2x,2x+1]。"""
        lay = CrossSmcLayout(smc_num=8, mode=Unroll.CONTIGUOUS)
        t = np.arange(16, dtype=np.float16)
        per = lay.distribute(t)
        for x in range(8):
            np.testing.assert_array_equal(per[x], [2 * x, 2 * x + 1],
                                          err_msg=f"SMC_{x} 连续展开应持 [{2*x},{2*x+1}]")
        self.assertEqual(lay.placement(5, 16), (2, 1), "d3=5 → SMC_2 片内序 1")

    def test_interleave_matches_spec_1073(self):
        """§1073 间插展开 B=1：SMC_x 持 round-robin {x, x+8}；B=2：SMC_x 持 [2x,2x+1]。"""
        t = np.arange(16, dtype=np.float16)
        lay1 = CrossSmcLayout(smc_num=8, mode=Unroll.INTERLEAVE, B=1)
        for x in range(8):
            np.testing.assert_array_equal(lay1.distribute(t)[x], [x, x + 8],
                                          err_msg=f"B=1 SMC_{x} 应 round-robin [{x},{x+8}]")
        lay2 = CrossSmcLayout(smc_num=8, mode=Unroll.INTERLEAVE, B=2)
        for x in range(8):
            np.testing.assert_array_equal(lay2.distribute(t)[x], [2 * x, 2 * x + 1],
                                          err_msg=f"B=2 SMC_{x} 应持块 [{2*x},{2*x+1}]")

    def test_roundtrip_all_modes(self):
        """distribute → gather 复原原张量（多 D3/模式/B；含不整除 D3）。"""
        rng = np.random.default_rng(0)
        for D3 in (8, 10, 16, 64, 100, 512):
            t = rng.uniform(1, 5, D3).astype(np.float16)
            layouts = [CrossSmcLayout(8, Unroll.CONTIGUOUS),
                       CrossSmcLayout(8, Unroll.INTERLEAVE, B=1),
                       CrossSmcLayout(8, Unroll.INTERLEAVE, B=2),
                       ur_vec_tran_succ(8), ur_vec_tran_inte(8)]
            for lay in layouts:
                got = lay.gather(lay.distribute(t), D3)
                np.testing.assert_array_equal(got, t,
                                              err_msg=f"roundtrip D3={D3} mode={lay.mode} B={lay.B}")

    def test_large_k_distributes_evenly(self):
        """大 K=4096 均匀分散 8 SMC：每 SMC 512（= 放进独立 32KB 的布局基础）。"""
        for lay in (CrossSmcLayout(8, Unroll.CONTIGUOUS),
                    CrossSmcLayout(8, Unroll.INTERLEAVE, B=2)):
            self.assertEqual(lay.per_smc_len(4096), [512] * 8,
                             f"K=4096 应每 SMC 512（mode={lay.mode}）")

    def test_contiguous_differs_from_interleave(self):
        """可证伪：非对称分散下连续 != 间插（杀「分散模式无所谓」退化）。"""
        t = np.arange(16, dtype=np.float16)
        cont = CrossSmcLayout(8, Unroll.CONTIGUOUS).distribute(t)
        inte = CrossSmcLayout(8, Unroll.INTERLEAVE, B=1).distribute(t)
        self.assertFalse(all(np.array_equal(cont[x], inte[x]) for x in range(8)),
                         "连续与间插展开（B=1）布局应不同")


if __name__ == "__main__":
    unittest.main()
