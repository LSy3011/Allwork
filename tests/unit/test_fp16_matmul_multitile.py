"""fp16 大 K 多-tile 累加（DEBT-025-MULTITILE 首步，K=16 两-tile）结构 + 可证伪守门。

hco APPROVE 候选①（2026-06-30，据 spec 表517/520/§1193）：FP32ADDTREE8TO1 无 accumulate 位 →
fp16 大 K 累加必须两级 reduce：8to1 片内（cumulative=False）→ 5to1 accumulate=1 片间（§1193
「累加到 RESULT」）。本测守这个钦定结构 + 累加非平凡（专杀「只剩单 tile」回归——即 naive 8to1
cumulative 的失败模式 t1-only）。"""
import sys
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TestFp16MatmulK16(unittest.TestCase):
    def _op(self):
        from verification_v2.ops.matmul_op import ManualFp16MatmulK16Operator
        return ManualFp16MatmulK16Operator()

    def test_two_stage_reduce_structure(self):
        """钦定结构（hco）：片内 FP32ADDTREE8TO1 cumulative=False + 片间 FP32ADDTREE5TO1 accumulate=1
        （loop_carried II≥4）。8to1 无累加位 → 必须借 5to1 累加，非在 8to1 内 cumulative。"""
        ops = self._op().get_dfg(spec=None)
        by_type = {}
        for o in ops:
            by_type.setdefault(o.op_type.upper(), []).append(o)
        self.assertIn("FP32ADDTREE8TO1", by_type, "须有片内 8to1 reduce")
        self.assertIn("FP32ADDTREE5TO1", by_type, "须有片间 5to1 累加")
        p_tile = by_type["FP32ADDTREE8TO1"][0]
        acc = by_type["FP32ADDTREE5TO1"][0]
        self.assertFalse(p_tile.metadata.get("cumulative", False),
                         "8to1 须 cumulative=False（无 accumulate 位，纯片内 8→1）")
        self.assertTrue(acc.metadata.get("cumulative", False),
                        "5to1 须 cumulative=True（accumulate 位承载片间累加 = §1193）")
        self.assertEqual(acc.metadata.get("loop_carried_depends_on"), {"y_0": 4},
                         "5to1 累加自环 loop_carried latency=4 → II≥4")

    def test_golden_is_two_tile_accumulation_nontrivial(self):
        """可证伪：golden = reduce8(tile0)+reduce8(tile1)（K=16 真累加），且 **≠ 任一单 tile**
        —— 专杀 naive 8to1-cumulative 的失败模式（sim 当时给 t1-only）。"""
        op = self._op()
        data = op.prepare_input(n_rows=16)
        a, b = data["a"].astype(np.float16), data["b"].astype(np.float16)
        self.assertEqual(a.shape, (16,), "K=16 = 2 tile")
        f32 = np.float32

        def reduce8(x, w):
            p = x.astype(np.float32) * w.astype(np.float32)
            l0 = f32(p[0] + p[1]); l1 = f32(p[2] + p[3]); l2 = f32(p[4] + p[5]); l3 = f32(p[6] + p[7])
            return f32(f32(l0 + l1) + f32(l2 + l3))

        t0 = reduce8(a[0:8], b[0:8]); t1 = reduce8(a[8:16], b[8:16])
        golden = op.get_golden(data, vector_lanes=8)["y"]
        self.assertAlmostEqual(float(golden[0]), float(f32(t0 + t1)), places=3,
                               msg="golden 须 = 2-tile 累加 t0+t1")
        # 非平凡：累加值 != 任一单 tile（杀「只剩 t1」/「只剩 t0」回归）
        self.assertNotAlmostEqual(float(golden[0]), float(t0), places=2)
        self.assertNotAlmostEqual(float(golden[0]), float(t1), places=2)


if __name__ == "__main__":
    unittest.main()
