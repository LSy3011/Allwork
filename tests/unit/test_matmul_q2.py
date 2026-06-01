"""
ADR-026 Stage-2a 可证伪单测：Matmul_Q cols_per_smc=4 密集 4/词打包（解 DEBT-026-PACK）。

覆盖：
  - 密集 4-col 打包（每词 COLS 个结果，非 Stage-1 的 1/词稀疏）。
  - 单 addtree 4 通道 S0..S3 机制（hco 2026-06-01 重裁，推翻原 B1：单 addtree 多通道，
    非 COLS 个独立 addtree op）。
  - addtree 8 通道 old_i per-lane 独立性（直驱 sim 单元；DEBT-026-ADDTREE-8CH-RTL 占位）。
  - 交付路径 = model-A 预分发激活（BROADCAST=False，无 SHIFTUP）；广播通路忠实留 ADR-027。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))
sys.path.append(str(_ROOT / "sim"))

from verification_v2.ops.matmul_op import ManualMatmulQ2Operator


class TestMatmulQ2DensePacking(unittest.TestCase):
    def setUp(self):
        self.op = ManualMatmulQ2Operator()
        self.smc_num = 8

    def test_dense_4col_packing(self):
        """每 SMC 一词装 COLS 个点积（dense），非 Stage-1 的 1/词稀疏。"""
        data = self.op.prepare_input(256, smc_num=self.smc_num)
        golden = self.op.get_golden(data, vector_lanes=4)["y"]
        M, K, COLS = self.op.M, self.op.K, self.op.COLS
        self.assertEqual(golden.shape, (self.smc_num, 4 * M))
        a = data["a"].reshape(M, K)
        for s in range(self.smc_num):
            W = data["b"][s].reshape(COLS, K)
            for i in range(M):
                # word i 的 lane 0..COLS-1 = COLS 个独立点积（密集）
                for c in range(COLS):
                    self.assertAlmostEqual(float(golden[s, 4 * i + c]),
                                           float(np.dot(a[i], W[c])), places=2)
                # COLS=4 填满整词：无 0 padding（区别 Stage-1 lane1-3=0 稀疏）
                if COLS == 4:
                    self.assertNotEqual(float(golden[s, 4 * i + 1]), 0.0,
                                        "dense 打包：lane1 应是真实结果非 0")

    def test_multi_col_golden_independent(self):
        """B1-errata 验收 oracle 层：golden 把 4 列建模为 4 个**独立**点积（验收基准本身正确）。
        构造 4 列各占一个极不同量级的签名 → 若 golden 串扰，量级会可检测地混进来。
        注：本测验的是 golden 参考的列独立性；**真 sim datapath 串扰保护**由 e2e（sim vs
        golden 4 列 distinct 全对）+ test_8channel_no_crosstalk（直驱 sim addtree）覆盖。"""
        M, K, COLS = self.op.M, self.op.K, self.op.COLS
        self.assertEqual((M, K, COLS), (1, 4, 4), "本测针对 Stage-2a M=1/K=4/COLS=4")
        a = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        # 列 c = 仅第 c lane 非 0，量级 10^(c+1) → dot(a,col_c)=10^(c+1)，4 个数量级隔开
        cols = np.array([[10, 0, 0, 0], [0, 100, 0, 0],
                         [0, 0, 1000, 0], [0, 0, 0, 10000]], dtype=np.float32)
        b = np.tile(cols.reshape(1, COLS * K), (self.smc_num, 1)).astype(np.float32)
        data = {"a": a, "b": b}
        golden = self.op.get_golden(data, vector_lanes=4)["y"]
        for s in range(self.smc_num):
            for c in range(COLS):
                exp = 10.0 ** (c + 1)
                self.assertAlmostEqual(float(golden[s, c]), exp, places=1,
                                       msg=f"列{c} 应=独立点积 {exp}，串扰会污染量级")

    def test_single_addtree_multi_channel_mechanism(self):
        """hco 重裁 B1：用**单个** FP32ADDTREE5TO1 op + COLS 通道 S0..S{COLS-1}，
        **非** COLS 个独立 addtree op（后者在单物理单元会互相覆盖 D0/D1）。"""
        ops = self.op.get_dfg(spec=None)
        addtrees = [o for o in ops if o.op_type.upper() == "FP32ADDTREE5TO1"]
        self.assertEqual(len(addtrees), 1,
                         f"应只有 1 个 addtree op（单 op 多通道），实际 {len(addtrees)}")
        # COLS 个通道 RTOVR 各路由到 FP32ADDTREE5TO1_S0..S{COLS-1}，全喂同一 addtree op
        addtree_id = addtrees[0].trace_id
        chan_rtovrs = [o for o in ops if o.op_type.upper() == "RTOVR"
                       and "FP32ADDTREE5TO1_S" in o.trace_id]
        self.assertEqual(len(chan_rtovrs), self.op.COLS,
                         f"应有 COLS={self.op.COLS} 个通道 RTOVR(S0..S{self.op.COLS-1})")
        for rt in chan_rtovrs:
            self.assertIn(rt.trace_id, addtrees[0].metadata.get("depends_on", []),
                          f"通道 RTOVR {rt.trace_id} 应喂唯一 addtree op {addtree_id}")

    def test_shipped_path_is_model_a_predistribute(self):
        """交付路径 BROADCAST=False：model-A 预分发激活，DFG 无 SHIFTUP（广播通路留 ADR-027）。"""
        self.assertFalse(self.op.BROADCAST, "交付路径应 BROADCAST=False（model-A 预分发）")
        ops = self.op.get_dfg(spec=None)
        shiftups = [o for o in ops if o.op_type.upper() == "SHIFTUP"]
        self.assertEqual(len(shiftups), 0,
                         "model-A 预分发路径不应有 SHIFTUP（广播通路忠实 = ADR-027 DEBT-026-BCAST-MULTI）")


class TestAddtree8ChannelIndependence(unittest.TestCase):
    """直驱 sim FP32AddTree5To1Unit 验 8 通道 old_i per-lane 独立（hco B1-errata 依据 (a)）。
    DEBT-026-ADDTREE-8CH-RTL：本测仅 sim 背书，8 通道独立性的 RTL 逐字确认仍缺，升 Accepted 需 VCS。"""

    def _pack4(self, vals, f2u):
        w = 0
        for j, v in enumerate(vals):
            w |= (f2u(float(v)) & 0xFFFFFFFF) << (j * 32)
        return w

    def test_8channel_no_crosstalk(self):
        from smc_simulator.units.fp32_add_tree_unit import FP32AddTree5To1Unit, f2u, u2f
        u = FP32AddTree5To1Unit("ADDTREE")
        # 8 通道各喂 distinct 4-lane（sum = 通道签名）；old_i 初始 0（dr_d0/d1=0）
        sums = [4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0]  # 各通道目标 sum
        inputs = {"ctrl": 0x1}  # VALID
        for i in range(8):
            inputs[f"s{i}"] = self._pack4([sums[i] / 4.0] * 4, f2u)  # 4 lane 各 sum/4
            inputs[f"s{i}_valid"] = True
        u.evaluate(inputs, cycle=0)
        u.tick()
        # flush pipeline（latency 级）→ 结果到 dr_d0/d1
        for cyc in range(u.latency):
            u.evaluate({"ctrl": 0x0}, cycle=1 + cyc)
            u.tick()
        out = u.get_outputs()
        d0, d1 = out["D0"], out["D1"]
        got = []
        for i in range(8):
            base = d0 if i < 4 else d1
            bits = (base >> ((i % 4) * 32)) & 0xFFFFFFFF
            got.append(round(u2f(bits), 3))
        # 每通道输出 = 自己的 sum，互不污染（若共用 old_i / 串扰则量级错位）
        for i in range(8):
            self.assertAlmostEqual(got[i], sums[i], places=2,
                                   msg=f"通道{i} 应={sums[i]}（per-lane 独立 old_i），实得 {got[i]}；"
                                       f"全通道 {got}")


if __name__ == "__main__":
    unittest.main()
