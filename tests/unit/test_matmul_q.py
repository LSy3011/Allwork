"""
ADR-026 Stage-1 可证伪单测：Matmul_Q（matrix×matrix）多左行 + 广播左 + 每 SMC 列点积。

Stage-1：K=4 单 tile、cols_per_smc=1、M=4 行（≤ addtree pipeline 深度，每行独立）。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))

from verification_v2.ops.matmul_op import ManualMatmulQOperator


class TestMatmulQ(unittest.TestCase):
    def setUp(self):
        self.op = ManualMatmulQOperator()
        self.smc_num = 8

    def test_multi_row_dot(self):
        """每左行 i 在每 SMC s 产 Q[s,i]=dot(X[i], W[:,s])；golden 反映 1/词稀疏布局。"""
        data = self.op.prepare_input(256, smc_num=self.smc_num)
        golden = self.op.get_golden(data, vector_lanes=4)["y"]
        M, K = self.op.M, self.op.K
        # 稀疏布局：shape (smc_num, 4*M)，Q 落 4*i，其余 0
        self.assertEqual(golden.shape, (self.smc_num, 4 * M))
        X = data["a"].reshape(M, K)
        bcol = data["b"][:, :K]
        for s in range(self.smc_num):
            for i in range(M):
                self.assertAlmostEqual(float(golden[s, 4 * i]),
                                       float(np.dot(X[i], bcol[s])), places=2)
                # 非 lane0 位置应为 0（稀疏布局）
                self.assertEqual(float(golden[s, 4 * i + 1]), 0.0)

    def test_per_smc_distinct(self):
        """不同 SMC 权重列不同 → 同一左行在各 SMC 输出不同（output-stationary）。"""
        data = self.op.prepare_input(256, smc_num=self.smc_num)
        golden = self.op.get_golden(data, vector_lanes=4)["y"]
        row0 = [round(float(golden[s, 0]), 3) for s in range(self.smc_num)]  # 各 SMC row0
        self.assertEqual(len(set(row0)), self.smc_num, "各 SMC row0 输出应不同（per-SMC 权重）")

    def test_single_broadcast_source(self):
        """激活单广播流：get_dfg 只注入 1 个 broadcast SHIFTUP（避 multi-broadcast 限制）。"""
        ops = self.op.get_dfg(spec=None)
        shiftups = [o for o in ops
                    if o.op_type.upper() == "SHIFTUP" and o.metadata.get("broadcast") == 1]
        self.assertEqual(len(shiftups), 1, f"应只有 1 个 broadcast SHIFTUP，实际 {len(shiftups)}")
        a_reads = [o for o in ops if o.op_type.upper() == "UR_READ"
                   and o.metadata.get("meta_name") == "a"]
        self.assertEqual(a_reads[0].metadata.get("iq_id"), 30, "激活 read pin ur-r0")

    def test_m_within_pipeline_depth(self):
        """M ≤ addtree latency(4)：保证每行 old_i=0 独立（M>4 会跨行累加 row(i-4)）。"""
        self.assertLessEqual(self.op.M, 4,
                             "Stage-1 M 必须 ≤ pipeline 深度 4，否则跨行累加；M>4 需 8 通道/UR 部分和(Stage-2)")


if __name__ == "__main__":
    unittest.main()
