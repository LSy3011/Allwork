"""ADR-032 S2 可证伪单测：FP16 matmul 满 32 乘（4 列稠密）数值正确性。

覆盖（每条可证伪——实现错则断言挂）：
  1. FP16MulUnit._compute 满 32 乘：4 权重(S0-S3) × 广播激活(S4-S7) → D0..D7 = 4 对点积分量。
  2. 端到端单元链（FP16MUL 32 乘 → 4 条 reduce8）== ManualFp16MatmulQOperator.get_golden
     == 4 个独立手算 K=8 点积。
  3. golden 可证伪：改某一列权重 → 仅对应输出 lane 变（不串扰）。
"""
import sys
import struct
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent.parent
for _p in (str(_ROOT), str(_ROOT / "src"), str(_ROOT / "sim"), str(_ROOT / "sim" / "aiacc_hardware")):
    if _p not in sys.path:
        sys.path.append(_p)


def _f16bits(x):
    return int(np.float16(x).view(np.uint16))


def _f32val(bits):
    return struct.unpack('<f', struct.pack('<I', bits & 0xFFFFFFFF))[0]


def _pack8_fp16(vals):
    r = 0
    for i, v in enumerate(vals):
        r |= (_f16bits(v) << (i * 16))
    return r


class TestFp16MatmulQCompute(unittest.TestCase):
    def test_full_32_mul_4col(self):
        """4 权重列(S0-S3) × 广播激活(S4=S5=S6=S7=act) → 满 32 乘 → 4 对 D。"""
        from smc_simulator.units.fp16_mul_unit import FP16MulUnit
        from smc_simulator.units.fp32addtree8to1_unit import FP32AddTree8To1Unit

        rng = np.random.default_rng(1)
        act = rng.uniform(1.0, 5.0, 8).astype(np.float16)
        cols = [rng.uniform(1.0, 5.0, 8).astype(np.float16) for _ in range(4)]

        mul = FP16MulUnit("m")
        for c in range(4):
            mul.regs[c] = _pack8_fp16(cols[c].tolist())     # 权重列 c → S0-S3
        bcast = _pack8_fp16(act.tolist())
        for s in range(4, 8):
            mul.regs[s] = bcast                              # 广播激活 → S4-S7
        d = mul._compute()

        at8 = FP32AddTree8To1Unit("a")

        def dot8_ref(w):
            p = (w.astype(np.float32) * act.astype(np.float32))
            f32 = np.float32
            l0 = f32(p[0] + p[1]); l1 = f32(p[2] + p[3])
            l2 = f32(p[4] + p[5]); l3 = f32(p[6] + p[7])
            return float(f32(f32(l0 + l1) + f32(l2 + l3)))

        for c in range(4):
            got = _f32val(at8._reduce8(d[2 * c], d[2 * c + 1]))   # D{2c}/D{2c+1} → 列 c 点积
            self.assertEqual(got, dot8_ref(cols[c]),
                             f"列 {c} 点积链结果须 == 手算（D{2*c}/D{2*c+1} 配对）")


class TestFp16MatmulQGolden(unittest.TestCase):
    def _op(self):
        from verification_v2.ops.matmul_op import ManualFp16MatmulQOperator
        return ManualFp16MatmulQOperator()

    def test_golden_matches_units_and_manual(self):
        """operator.get_golden == 单元链 == 4 个独立 numpy 点积。"""
        from smc_simulator.units.fp16_mul_unit import FP16MulUnit
        from smc_simulator.units.fp32addtree8to1_unit import FP32AddTree8To1Unit
        op = self._op()
        data = op.prepare_input(0)
        a = data["a"]
        golden = op.get_golden(data, vector_lanes=8)["y"]

        mul = FP16MulUnit("m")
        for c in range(op.COLS):
            mul.regs[c] = _pack8_fp16(data[f"b{c}"].tolist())
        for s in range(4, 8):
            mul.regs[s] = _pack8_fp16(a.tolist())
        d = mul._compute()
        at8 = FP32AddTree8To1Unit("a")
        for c in range(op.COLS):
            chain = _f32val(at8._reduce8(d[2 * c], d[2 * c + 1]))
            self.assertEqual(chain, float(golden[c]), f"列 {c}: 单元链 != golden")
            manual = float(np.dot(a.astype(np.float32), data[f"b{c}"].astype(np.float32)))
            self.assertAlmostEqual(chain, manual, places=2, msg=f"列 {c}: 链 != numpy 点积")

    def test_columns_distinct_no_crosstalk(self):
        """可证伪：改 b1 一个 lane → 仅 golden[1] 变，其余列不变（防 lane 串扰/退化）。"""
        op = self._op()
        data = op.prepare_input(0)
        g0 = op.get_golden(data, vector_lanes=8)["y"].copy()
        data2 = {k: v.copy() for k, v in data.items()}
        data2["b1"][0] = np.float16(float(data2["b1"][0]) + 1.0)
        g1 = op.get_golden(data2, vector_lanes=8)["y"]
        self.assertNotEqual(float(g0[1]), float(g1[1]), "改 b1 → golden[1] 必变")
        for c in (0, 2, 3):
            self.assertEqual(float(g0[c]), float(g1[c]), f"改 b1 不应影响列 {c}")


class TestFp16MatmulParallelN(unittest.TestCase):
    """ADR-032 S3：8-SMC N 维广播 golden 可证伪。"""
    def _op(self):
        from verification_v2.ops.matmul_op import ManualFp16MatmulParallelNOperator
        return ManualFp16MatmulParallelNOperator()

    def test_per_smc_golden_shape_and_correctness(self):
        """golden (smc_num, COLS)，每 SMC[s,c] = dot(广播 a, 本地 b_c[s])。"""
        op = self._op()
        smc_num = 8
        data = op.prepare_input(8, smc_num=smc_num)
        self.assertEqual(data["a"].shape, (op.K,), "广播 a 是 1D（K,）")
        self.assertEqual(data["b0"].shape, (smc_num, op.K), "权重 b0 partitioned 2D")
        g = op.get_golden(data, vector_lanes=8)["y"]
        self.assertEqual(g.shape, (smc_num, op.COLS), "golden (smc_num, COLS)")
        a = data["a"]
        for s in range(smc_num):
            for c in range(op.COLS):
                manual = float(np.dot(a.astype(np.float32), data[f"b{c}"][s].astype(np.float32)))
                self.assertAlmostEqual(float(g[s, c]), manual, places=2,
                                       msg=f"SMC{s} 列{c} golden != 独立点积")

    def test_smc_outputs_distinct(self):
        """可证伪：各 SMC 权重不同 → 各 SMC golden 不同（防 partitioned 退化成广播）。"""
        op = self._op()
        g = op.get_golden(op.prepare_input(8, smc_num=8), vector_lanes=8)["y"]
        # SMC_0 与 SMC_1 的输出行不应全等（不同 partitioned 权重）
        self.assertFalse(np.allclose(g[0], g[1]),
                         "不同 SMC 的 partitioned 权重应产不同输出")


if __name__ == "__main__":
    unittest.main()
