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
                                   ur_vec_tran_succ, ur_vec_tran_inte,
                                   inject_distributed, gather_from_programs)


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


class TestMat2DLayout(unittest.TestCase):
    """2D 矩阵分散（§1096-1099 UR_Mat_*）：matmul 权重/激活矩阵跨-SMC 分散。"""

    def test_col_distribute_matches_spec_1098(self):
        """§1098 转置连续：W[K=8,N=16] 列展开，chunk=2 → SMC_s 持列 [2s,2s+1]，每列 K=8 元素。"""
        from core.cross_smc_layout import ur_mat_tran_succ
        W = np.arange(8 * 16, dtype=np.float16).reshape(8, 16)   # [K, N]
        per = ur_mat_tran_succ(8).distribute(W)
        for s in range(8):
            self.assertEqual(per[s].shape, (2, 8), f"SMC_{s} 应持 2 列×K=8")
            np.testing.assert_array_equal(per[s][0], W[:, 2 * s], "第 1 列应 = W[:,2s]")
            np.testing.assert_array_equal(per[s][1], W[:, 2 * s + 1])

    def test_roundtrip_2d_all_formats(self):
        """distribute → gather 复原 [H,W]（行/列展开 + 连续/间插）。"""
        from core.cross_smc_layout import (ur_mat_norm, ur_mat_norm_inte,
                                           ur_mat_tran_succ, ur_mat_tran_inte)
        rng = np.random.default_rng(0)
        for H, W in ((8, 16), (16, 32), (8, 8), (10, 24)):
            M = rng.uniform(1, 5, (H, W)).astype(np.float16)
            for fmt in (ur_mat_norm(8), ur_mat_norm_inte(8), ur_mat_tran_succ(8), ur_mat_tran_inte(8)):
                got = fmt.gather(fmt.distribute(M), H, W)
                np.testing.assert_array_equal(got, M, err_msg=f"2D roundtrip {H}x{W} axis={fmt.axis}")

    def test_reproduces_matmul_n_weight_layout(self):
        """一致性：matmul_n 手搓「列 (s,c)=全局列 4s+c → SMC s」= 转置连续 chunk=4 分散。
        把 ad-hoc 权重分散收编到文档 §1098 模型下。"""
        from core.cross_smc_layout import ur_mat_tran_succ
        K, COLS, SMC = 8, 4, 8
        N = SMC * COLS                                            # 32 列
        W = np.arange(K * N, dtype=np.float16).reshape(K, N)
        per = ur_mat_tran_succ(SMC).distribute(W)                 # chunk=ceil(32/8)=4
        for s in range(SMC):
            self.assertEqual(per[s].shape, (COLS, K), f"SMC_{s} 应持 {COLS} 连续列")
            for c in range(COLS):
                np.testing.assert_array_equal(per[s][c], W[:, s * COLS + c],
                                              err_msg=f"SMC_{s} 第 {c} 列 = 全局列 {s*COLS+c}（matmul_n 布局）")


class TestInjectDistributedSimIntegration(unittest.TestCase):
    """sim/emitter 整合：layout 驱动的跨-SMC 分散注入落到真 MockProgram（sim 消费的 per-SMC bank）。"""

    def _programs(self, n=8):
        import os
        for _p in (str(_ROOT), str(_ROOT / "src"), str(_ROOT / "verification_v2")):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        os.chdir(_ROOT)
        from api.hw_spec import HardwareSpec
        from verification_v2.infra.mock_memory import MockProgram
        spec = HardwareSpec.load("hw_config")
        return [MockProgram(spec, dtype=np.float16) for _ in range(n)], spec

    def test_distributed_inject_populates_per_smc_banks(self):
        """64 fp16 连续展开 → 每 SMC 独立 bank0 持自己 8 fp16 切片（= layout.distribute）。"""
        programs, _ = self._programs()
        vec = np.arange(1, 65, dtype=np.float16)            # 64 fp16
        lay = CrossSmcLayout(8, Unroll.CONTIGUOUS)          # chunk=8
        slices = inject_distributed(programs, vec, lay, logical_addr=0)  # bank0 offset0
        for smc in range(8):
            bank0_row0 = programs[smc].memory_allocator.banks[0][0]   # 8 fp16 = 1 行
            np.testing.assert_array_equal(bank0_row0, slices[smc],
                                          err_msg=f"SMC_{smc} bank0 应持连续切片 {slices[smc].tolist()}")
            np.testing.assert_array_equal(slices[smc], np.arange(8 * smc + 1, 8 * smc + 9))

    def test_sim_level_roundtrip_inject_then_gather(self):
        """sim 级 roundtrip：分散注入 → 从 per-SMC bank gather 回 == 原向量（连续/间插都验）。"""
        for lay in (CrossSmcLayout(8, Unroll.CONTIGUOUS),
                    CrossSmcLayout(8, Unroll.INTERLEAVE, B=2)):
            programs, _ = self._programs()
            vec = np.arange(1, 65, dtype=np.float16)
            inject_distributed(programs, vec, lay, logical_addr=0)
            got = gather_from_programs(programs, lay, logical_addr=0, D3=64, vector_lanes=8)
            np.testing.assert_array_equal(got, vec, err_msg=f"sim roundtrip mode={lay.mode} B={lay.B}")


if __name__ == "__main__":
    unittest.main()
