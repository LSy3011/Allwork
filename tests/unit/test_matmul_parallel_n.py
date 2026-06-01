"""
ADR-025 D5 可证伪单测：8-SMC N 维广播 matmul 的两条核心契约。

matmul = 广播激活 a（单源 SHIFTUP）+ 每 SMC 与本地权重列 b_n 点积 → 每 SMC 输出 y_n=Σ_i a[i]·b_n[i]。

falsifiable：
- test_per_smc_output_distinct：若退化成「所有 SMC 算同一份」（漏 partitioned b）→ golden 全相同 → fail。
- test_single_broadcast_source：若激活退化成多广播流（重蹈 configurable multi-broadcast sim 限制，
  D2 首要风险）→ 注入 >1 个 broadcast SHIFTUP → fail。守「单广播流」契约。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))

from verification_v2.ops.matmul_op import ManualMatmulParallelNOperator


class TestMatmulParallelN(unittest.TestCase):
    def setUp(self):
        self.op = ManualMatmulParallelNOperator()
        self.smc_num = 8
        self.n_rows = 256

    def test_per_smc_output_distinct(self):
        """每 SMC 算自己的 dot(a, b_n)，输出各不相同（output-stationary by N）。"""
        data = self.op.prepare_input(self.n_rows, smc_num=self.smc_num)
        golden = self.op.get_golden(data, vector_lanes=4)["y"]

        # shape (smc_num, 1)：per-SMC 单标量，触发 verifier 的 per-SMC 8/8 比对（非 SMC_0-only fallback）
        self.assertEqual(golden.shape, (self.smc_num, 1))

        a, b = data["a"], data["b"]
        for n in range(self.smc_num):
            expected = float(np.dot(a, b[n]))
            self.assertAlmostEqual(float(golden[n, 0]), expected, places=2,
                                   msg=f"SMC_{n} golden 应 = dot(a, b_{n})")

        # 8 份输出必须各不相同（若漏 partitioned b → 全等 → fail）
        vals = [float(golden[n, 0]) for n in range(self.smc_num)]
        self.assertEqual(len(set(round(v, 3) for v in vals)), self.smc_num,
                         "8 个 SMC 输出应各不相同（partitioned 权重）")

    def test_single_broadcast_source(self):
        """激活是单广播流：get_dfg 只注入 1 个 broadcast SHIFTUP（避开 multi-broadcast sim 限制）。"""
        ops = self.op.get_dfg(spec=None)
        shiftups = [o for o in ops
                    if o.op_type.upper() == "SHIFTUP" and o.metadata.get("broadcast") == 1]
        self.assertEqual(len(shiftups), 1,
                         f"应只有 1 个 broadcast SHIFTUP（单广播源），实际 {len(shiftups)}")

        # 广播源 UR_READ 必须 pin 到 ur-r0（kernel.py SHIFTUP 本地 data 硬连 ur-r0 读端口）
        a_reads = [o for o in ops if o.op_type.upper() == "UR_READ"
                   and o.metadata.get("meta_name") == "a"]
        self.assertEqual(len(a_reads), 1, "应只有 1 个激活 UR_READ")
        self.assertEqual(a_reads[0].metadata.get("iq_id"), 30,
                         "广播激活 read 应 pin 到 ur-r0 (iq_id=30)")

    def test_cumulative_recurrence_modeled(self):
        """cumulative addtree 须有 loop-carried 自环（强制 II>=4，否则跨迭代累加被 pipeline 打断）。"""
        ops = self.op.get_dfg(spec=None)
        addtrees = [o for o in ops if o.op_type.upper() == "FP32ADDTREE5TO1"]
        self.assertEqual(len(addtrees), 1)
        lc = addtrees[0].metadata.get("loop_carried_depends_on", {})
        self.assertIn("y_0", lc, "cumulative addtree 须有自环 loop_carried 边")
        self.assertGreaterEqual(lc["y_0"], 4, "自环延迟须 >= addtree latency(4)")


if __name__ == "__main__":
    unittest.main()
