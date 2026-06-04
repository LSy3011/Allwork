"""ADR-032 S1 可证伪单测：FP16 matmul 最小通路数值正确性。

覆盖（每条都「可证伪」——实现错则断言挂）：
  1. fp16_mul_to_fp32：FP16×FP16→FP32 与 numpy float16→float32 精确乘逐 bit 一致。
  2. FP16MulUnit._compute：32 乘真结构路由 —— D0=S0[0..3]·S4[0..3]、D1=S0[4..7]·S4[4..7]。
  3. FP32AddTree8To1Unit._reduce8：8 个 FP32 平衡树求和。
  4. 端到端单元链（FP16MulUnit → reduce8）== ManualFp16MatmulOperator.get_golden
     == 独立手算 K=8 点积（fp16 乘 / fp32 累加）。
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

from smc_simulator.utils.fp_utils import fp16_mul_to_fp32


def _f16bits(x):
    return int(np.float16(x).view(np.uint16))


def _f32val(bits):
    return struct.unpack('<f', struct.pack('<I', bits & 0xFFFFFFFF))[0]


def _f32bits(x):
    return struct.unpack('<I', struct.pack('<f', np.float32(x)))[0]


def _pack8_fp16(vals):
    r = 0
    for i, v in enumerate(vals):
        r |= (_f16bits(v) << (i * 16))
    return r


def _unpack4_fp32(word):
    return [_f32val((word >> (x * 32)) & 0xFFFFFFFF) for x in range(4)]


class TestFp16MulToFp32(unittest.TestCase):
    def test_exact_vs_numpy(self):
        """fp16_mul_to_fp32 == numpy float16→float32 乘（精确，22bit 乘积 ≤ fp32 尾数）。"""
        cases = [(1.5, 2.0), (0.1, 0.2), (-3.25, 4.5), (100.0, 0.03125),
                 (0.0, 5.0), (3.14159, -2.71828), (255.0, 255.0)]
        for a, b in cases:
            ba, bb = _f16bits(a), _f16bits(b)
            got = _f32val(fp16_mul_to_fp32(ba, bb))
            exp = float(np.float16(a).astype(np.float32) * np.float16(b).astype(np.float32))
            self.assertEqual(got, exp, f"{a}*{b}: got {got} != numpy {exp}")

    def test_output_is_fp32_not_fp16(self):
        """关键回归：输出是 FP32（32bit）非 FP16。0.1*0.2 的 fp32 结果含 fp16 装不下的尾数位。"""
        bits = fp16_mul_to_fp32(_f16bits(0.1), _f16bits(0.2))
        self.assertEqual(bits, _f32bits(np.float16(0.1).astype(np.float32) *
                                        np.float16(0.2).astype(np.float32)))
        # 若误用 fp16 乘（→fp16 再 widen），低位尾数会丢 → 与上面 fp32-exact 不同
        self.assertNotEqual(bits & 0xFFFF, 0, "fp32 结果应有非零低 16 位尾数（证明非 fp16 截断）")


class TestFP16MulUnitCompute(unittest.TestCase):
    def _unit(self):
        from smc_simulator.units.fp16_mul_unit import FP16MulUnit
        return FP16MulUnit("t")

    def test_32_mul_routing_D0_D1(self):
        """D0=S0[0..3]·S4[0..3]、D1=S0[4..7]·S4[4..7]（4 对×8 lane 的第 0 对）。"""
        u = self._unit()
        S0 = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        S4 = [0.5, 0.25, 2.0, 4.0, 1.0, 3.0, 0.5, 2.0]
        u.regs[0] = _pack8_fp16(S0)
        u.regs[4] = _pack8_fp16(S4)
        out = u._compute()
        d0 = _unpack4_fp32(out[0])
        d1 = _unpack4_fp32(out[1])
        for x in range(4):
            self.assertEqual(d0[x], float(np.float16(S0[x]).astype(np.float32) *
                                          np.float16(S4[x]).astype(np.float32)))
            self.assertEqual(d1[x], float(np.float16(S0[x + 4]).astype(np.float32) *
                                          np.float16(S4[x + 4]).astype(np.float32)))

    def test_unused_pairs_zero(self):
        """只喂第 0 对（S0/S4）→ D2..D7（S1×S5 等）全 0。"""
        u = self._unit()
        u.regs[0] = _pack8_fp16([1.0] * 8)
        u.regs[4] = _pack8_fp16([2.0] * 8)
        out = u._compute()
        for d in range(2, 8):
            self.assertEqual(out[d], 0, f"D{d} 应为 0（对应源寄存器未喂）")


class TestAddTree8To1Reduce(unittest.TestCase):
    def _unit(self):
        from smc_simulator.units.fp32addtree8to1_unit import FP32AddTree8To1Unit
        return FP32AddTree8To1Unit("t")

    def test_reduce8_balanced_tree(self):
        """S0/S1 各 4×FP32 → 8 个 FP32 平衡树求和。"""
        u = self._unit()
        S0 = [1.0, 2.0, 3.0, 4.0]
        S1 = [0.5, 1.5, 2.5, 3.5]

        def pack(vals):
            r = 0
            for i, v in enumerate(vals):
                r |= (_f32bits(v) << (i * 32))
            return r

        got = _f32val(u._reduce8(pack(S0), pack(S1)))
        f32 = np.float32
        l0 = f32(S0[0] + S0[1]); l1 = f32(S0[2] + S0[3])
        l2 = f32(S1[0] + S1[1]); l3 = f32(S1[2] + S1[3])
        exp = float(f32(f32(l0 + l1) + f32(l2 + l3)))
        self.assertEqual(got, exp)
        self.assertAlmostEqual(got, sum(S0 + S1), places=5)


class TestEndToEndChainVsGolden(unittest.TestCase):
    def test_units_chain_equals_golden_and_manual_dot(self):
        """FP16MulUnit → reduce8 == operator.get_golden == 独立手算 K=8 点积。"""
        from smc_simulator.units.fp16_mul_unit import FP16MulUnit
        from smc_simulator.units.fp32addtree8to1_unit import FP32AddTree8To1Unit
        from verification_v2.ops.matmul_op import ManualFp16MatmulOperator

        op = ManualFp16MatmulOperator()
        data = op.prepare_input(0)
        a, b = data["a"], data["b"]

        # 1. 单元链：FP16MUL D0/D1 → addtree8to1 reduce8
        mul = FP16MulUnit("m")
        mul.regs[0] = _pack8_fp16(a.tolist())
        mul.regs[4] = _pack8_fp16(b.tolist())
        d = mul._compute()
        at8 = FP32AddTree8To1Unit("a")
        chain = _f32val(at8._reduce8(d[0], d[1]))

        # 2. golden（operator）
        golden = float(op.get_golden(data, vector_lanes=8)["y"][0])

        # 3. 独立手算：fp16 乘 / fp32 累加（顺序无关，atol 容忍舍入差）
        manual = float(np.dot(a.astype(np.float32), b.astype(np.float32)))

        self.assertEqual(chain, golden, "单元链结果须与 operator.get_golden 逐 bit 一致")
        self.assertAlmostEqual(chain, manual, places=2,
                               msg="链结果须 ≈ 独立 K=8 点积")

    def test_golden_falsifiable_wrong_weight(self):
        """可证伪性自检：改一个权重 lane → golden 必变（防 golden 退化成常数/忽略输入）。"""
        from verification_v2.ops.matmul_op import ManualFp16MatmulOperator
        op = ManualFp16MatmulOperator()
        data = op.prepare_input(0)
        g0 = float(op.get_golden(data, vector_lanes=8)["y"][0])
        data2 = {"a": data["a"].copy(), "b": data["b"].copy()}
        data2["b"][0] = np.float16(float(data2["b"][0]) + 1.0)
        g1 = float(op.get_golden(data2, vector_lanes=8)["y"][0])
        self.assertNotEqual(g0, g1, "改权重 lane0 后 golden 必变")


if __name__ == "__main__":
    unittest.main()
