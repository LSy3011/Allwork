# -*- coding: utf-8 -*-
"""ADR-056 实现步②出口证据 1/2/3：有序 AST + 逐步 fp32 参考发生器 + §C.4 双道数值域。

四块（与步②出口四项证据一一对应）：

* **TestOrderedAst**（证据 1）：有序性（a+b ≠ b+a；(a+b)+c ≠ a+(b+c)；×/+ 树形），
  以及 §A.2-1 非法语法响亮拒绝表（表达式取负 / 减除幂 / 调用 / 下标 / 比较 / 布尔 /
  未知名 / 保留名 / bool·复数·非有限字面量）；负字面量与一元正号的允许路径。
* **TestBitPatternSelfTest**（证据 2，§C.3 发生器舍入自测四件套，**全部比固定位模式**）：
  ① RTNE 平局进位 + 不同量级相加 + 乘法舍入；② 重关联改变 binary32 结果；
  ③ 有符号零（±0 位级区分，禁止 ``==`` 比较）；④ 亚正规/溢出/非有限的响亮拒绝。
  固定位模式的推导见各断言注释（IEEE-754 binary32 手册值，已用 numpy 交叉核）。
* **TestNumericDomain**（证据 3，§C.4 双道 AND）：
  安全域静态通过（scale_sum [1,2]/α=2.5）、危险域静态保守拒绝（add 遗留域）、
  保守拒绝诊断落成（节点路径 + 区间记录 + 「保守≠必错」措辞）、
  {0} 单点例外、动态道完备判定（实际数据全过/越声明域即拒）、
  以及「静态拒而动态过 = 设计内保守」的并存断言。
* **TestWholeDomainStaticCommitment**（步②复核封堵两处静态道漏洞的针对性用例）：
  输入叶子对**整个声明区间**承诺 ⊆ D（结果恒零如 a*0.0 不豁免）；病态声明域
  （[inf,inf] / NaN / 倒置）诊断响亮、置无效状态下传、永不抛断言；全负正规域
  放行；毒素经复合树传播且诊断只在源头。
* **TestIntervalConservatism**（§C.4-4 包络保守性的**随机化属性测试举证**，非数学
  证明）：随机有序树 × 随机声明域 × 随机采样点，断言逐节点 fp32 结果被静态包络
  包住。

**全部比整数位模式或区间端点，禁止比字符串**（诊断措辞断言仅限「保守」两字）。
"""
import random
import sys
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))

from verification_v2.reference.ordered_ast import (                       # noqa: E402
    FormulaRejection, Node, iter_nodes, parse_ordered_formula, tree_signature,
)
from verification_v2.reference import reference_generator as rg           # noqa: E402

F32 = np.float32


def bits(v) -> int:
    return int(F32(v).view(np.uint32))


def eval1(formula: str, inputs: dict) -> int:
    """单点求值 → 终值位模式。"""
    root = parse_ordered_formula(formula, list(inputs))
    ev = rg.evaluate(root, {k: F32(v) for k, v in inputs.items()})
    return int(F32(ev.y[0]).view(np.uint32))


class TestOrderedAst(unittest.TestCase):
    def test_order_preserved(self):
        t1 = parse_ordered_formula("x + alpha * other", ["x", "other", "alpha"])
        self.assertEqual(tree_signature(t1), "(add x (mul alpha other))")
        t2 = parse_ordered_formula("alpha * (x + other)", ["x", "other", "alpha"])
        self.assertEqual(tree_signature(t2), "(mul alpha (add x other))")
        # 交换律不得被应用：叶子顺序保序
        self.assertNotEqual(
            tree_signature(parse_ordered_formula("a + b", ["a", "b"])),
            tree_signature(parse_ordered_formula("b + a", ["a", "b"])),
        )
        # 结合性不得被重关联
        self.assertNotEqual(
            tree_signature(parse_ordered_formula("(a + b) + c", ["a", "b", "c"])),
            tree_signature(parse_ordered_formula("a + (b + c)", ["a", "b", "c"])),
        )
        # 节点路径保序：后序枚举的根是末项
        self.assertEqual(iter_nodes(t1)[-1].path, "root")

    def test_negative_literal_and_unary_plus_allowed(self):
        t = parse_ordered_formula("x + (-2.5)", ["x"])
        self.assertEqual(tree_signature(t), "(add x const(-2.5))")
        self.assertEqual(t.right.value, -2.5)
        t3 = parse_ordered_formula("-3.0 * x", ["x"])
        self.assertEqual(tree_signature(t3), "(mul const(-3.0) x)")
        tp = parse_ordered_formula("+x", ["x"])           # UAdd 允许且恒等
        self.assertEqual(tree_signature(tp), "x")
        self.assertEqual(eval1("+x", {"x": 1.5}), eval1("x", {"x": 1.5}))

    def test_illegal_syntax_rejected(self):
        cases = [
            "-x", "-alpha", "-(x+y)", "-(x)",            # 表达式取负
            "x - other", "x / other", "x ** 2", "x // 2",
            "x % 2", "x @ other",
            "sin(x)", "abs(x)", "max(x, other)",         # 函数调用
            "x[0]", "x.a",                               # 下标/属性
            "x > 1", "x == other",                       # 比较
            "x and other", "not x",                      # 布尔
            "z + x",                                     # 未声明名
            "y + x", "constants + x",                    # 保留名
            "x + True",                                  # bool
            "x + 1j",                                    # 复数
            "x + 1e999",                                 # 非有限字面量
            "x *",                                       # 语法错误
        ]
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(FormulaRejection) as cm:
                    parse_ordered_formula(text, ["x", "other", "alpha"])
                self.assertEqual(cm.exception.code, "SYNTAX_UNSUPPORTED")


class TestBitPatternSelfTest(unittest.TestCase):
    """§C.3 用户裁示③四件套。期望值均为 IEEE-754 binary32 手册推导值。"""

    def test_rounding_rtne(self):
        # ①a 平局到偶（下）：1.0 + 2^-24（恰好半个 ulp）→ 1.0
        self.assertEqual(eval1("a + b", {"a": 1.0, "b": 2.0 ** -24}), 0x3F800000)
        # ①b 平局到偶（上）：(1+2^-23) + 2^-24 → 尾数奇 → 进到 1+2^-22
        self.assertEqual(eval1("a + b", {"a": F32(1.0) + F32(2.0 ** -23),
                                         "b": 2.0 ** -24}), 0x3F800002)
        # ①c 不同量级：2^24 + 1（ulp=2，恰半）→ 平局到偶 → 2^24
        self.assertEqual(eval1("a + b", {"a": 2.0 ** 24, "b": 1.0}), 0x4B800000)
        # ①d 乘法舍入：3.0 × 0x3DCCCCCD(0.1f) → 0x3E99999A
        self.assertEqual(eval1("a * b", {"a": 3.0, "b": F32(0.1)}), 0x3E99999A)

    def test_reassociation_changes_bits(self):
        # ② (2^24 + 1) + (-2^24)：先舍 2^24+1→2^24，再抵消 → +0.0
        self.assertEqual(
            eval1("(a + b) + c", {"a": 2.0 ** 24, "b": 1.0, "c": -(2.0 ** 24)}),
            0x00000000)
        # ② 2^24 + (1 + (-2^24))：支括内恰为 -16777215，恰可表 → +1.0
        self.assertEqual(
            eval1("a + (b + c)", {"a": 2.0 ** 24, "b": 1.0, "c": -(2.0 ** 24)}),
            0x3F800000)

    def test_signed_zero_bits(self):
        nz = -0.0
        # ③ -0 + -0 = -0（位级 0x80000000；与 +0 判等但位不同——故全部比位模式）
        self.assertEqual(eval1("a + b", {"a": nz, "b": nz}), 0x80000000)
        # ③ +0 + -0 = +0（RTNE 规则）
        self.assertEqual(eval1("a + b", {"a": 0.0, "b": nz}), 0x00000000)
        # ③ 乘法符号传播
        self.assertEqual(eval1("a * b", {"a": -1.0, "b": 0.0}), 0x80000000)
        self.assertEqual(eval1("a * (0.0)", {"a": 1.0}), 0x00000000)
        self.assertEqual(eval1("a * (-0.0)", {"a": 1.0}), 0x80000000)
        # ③ 字面量 -0.0 经常量通道保持位模式
        self.assertEqual(eval1("(-0.0) + a", {"a": -0.0}), 0x80000000)

    def test_out_of_domain_loud_rejection(self):
        names = ["a", "b"]
        # ④ 亚正规输入 → dynamic SUBNORMAL
        root = parse_ordered_formula("a + b", names)
        ev = rg.evaluate(root, {"a": F32(1e-45), "b": F32(1.0)})
        diags = rg.dynamic_data_check(ev, {"a": (-1, 1), "b": (0, 2)}, rg.leaf_paths(root))
        self.assertTrue(any(d.kind == "SUBNORMAL" and d.node_path == "root.l"
                            for d in diags))
        self.assertTrue(all(d.code == "TYPE_LAYOUT_UNSUPPORTED" for d in diags))
        # ④ inf 输入 → NONFINITE
        ev = rg.evaluate(root, {"a": F32(np.inf), "b": F32(1.0)})
        diags = rg.dynamic_data_check(ev, {"a": (-1, 1), "b": (0, 2)}, rg.leaf_paths(root))
        self.assertTrue(any(d.kind == "NONFINITE" for d in diags))
        # ④ 节点溢出：3e38 × 2 → inf，拒绝在内部节点（root）
        root_ov = parse_ordered_formula("a * b", names)
        ev = rg.evaluate(root_ov, {"a": F32(3e38), "b": F32(2.0)})
        self.assertTrue(any(d.kind == "NONFINITE" and d.node_path == "root"
                            for d in rg.dynamic_data_check(ev, {"a": (0, 3e38), "b": (0, 2)},
                                                           rg.leaf_paths(root_ov))))
        # ④ 常量字面量 RTNE 后为亚正规 → 静态 CONSTANT_OUT_OF_DOMAIN
        root_c = parse_ordered_formula("a + 1e-45", names[:1])
        st = rg.static_domain_check(root_c, {"a": (1.0, 2.0)})
        self.assertTrue(any(d.kind == "CONSTANT_OUT_OF_DOMAIN" for d in st.diagnostics))
        # ④ 静态溢出拒：[1e30,1e30]×[1e30,1e30] 越出 ±FLT_MAX
        root_m = parse_ordered_formula("a * b", names)
        st = rg.static_domain_check(root_m, {"a": (1e30, 1e30), "b": (1e30, 1e30)})
        self.assertFalse(st.ok)
        self.assertTrue(any(d.kind == "OVERFLOW" and d.node_path == "root"
                            for d in st.diagnostics))


_SCALE_SUM = "alpha * (x + other)"
_ADD = "x + alpha * other"
_ADD_DOMAINS = {"x": (-10.0, 10.0), "other": (-5.0, 15.0), "alpha": (2.5, 2.5)}
_SAFE_DOMAINS = {"x": (1.0, 2.0), "other": (1.0, 2.0), "alpha": (2.5, 2.5)}


class TestNumericDomain(unittest.TestCase):
    def test_static_safe_domain_passes(self):
        # 安全域：x+other ∈ [2,4]，α·(x+other) ∈ [5,10]，全正规不相交亚正规带
        root = parse_ordered_formula(_SCALE_SUM, ["x", "other", "alpha"])
        st = rg.static_domain_check(root, _SAFE_DOMAINS)
        self.assertTrue(st.ok, [str(d) for d in st.diagnostics])
        lo, hi = st.intervals["root.r"].lo, st.intervals["root.r"].hi
        self.assertTrue(1.9 < lo < 2.0 and 4.0 < hi < 4.1)      # 外扩 1 ulp 而非更宽
        lo, hi = st.intervals["root"].lo, st.intervals["root"].hi
        self.assertTrue(4.9 < lo < 5.0 and 10.0 < hi < 11.0)

    def test_legacy_domain_conservative_reject(self):
        # 危险域：输入叶子声明域 x∈[-10,10]、other∈[-5,15] 各自跨亚正规带（整区间规则），
        # 且 α·other = 2.5×[−5,15] = [−12.5,37.5] 也跨带 → 静态拒（保守≠必错）
        root = parse_ordered_formula(_ADD, ["x", "other", "alpha"])
        st = rg.static_domain_check(root, _ADD_DOMAINS)
        self.assertFalse(st.ok)
        hit = [d for d in st.diagnostics if d.kind == "SUBNORMAL_BAND"]
        self.assertTrue(hit)
        band_paths = {d.node_path for d in hit}
        # 后序首发 = x 叶子（root.l）；α 是 2.5 精确单例、不跨带，不得被点名
        self.assertEqual(hit[0].node_path, "root.l")
        self.assertTrue({"root.l", "root.r.r", "root.r", "root"}.issubset(band_paths))
        self.assertNotIn("root.r.l", band_paths)
        self.assertIn("保守", hit[0].detail)
        self.assertIn("[", hit[0].detail)                       # 区间记录成文
        # scale_sum 公式在遗留域上同样应拒（印证 §C.4-2 换域的必要性）
        root2 = parse_ordered_formula(_SCALE_SUM, ["x", "other", "alpha"])
        st2 = rg.static_domain_check(root2, _ADD_DOMAINS)
        self.assertFalse(st2.ok)
        hit2 = {d.node_path for d in st2.diagnostics if d.kind == "SUBNORMAL_BAND"}
        self.assertTrue({"root.r.l", "root.r.r", "root.r", "root"}.issubset(hit2))
        self.assertNotIn("root.l", hit2)                        # α 单例在 root.l，干净

    def test_provable_zero_exception(self):
        # {0} 单点例外：a × 0.0 全区间恰为 0，亚正规规则不拒
        root = parse_ordered_formula("a * 0.0", ["a"])
        st = rg.static_domain_check(root, {"a": (1.0, 2.0)})
        self.assertTrue(st.ok, [str(d) for d in st.diagnostics])
        iv = st.intervals["root"]
        self.assertEqual((iv.lo, iv.hi), (0.0, 0.0))

    def test_dynamic_check_on_actual_data(self):
        # 动态道（n=4 缩影）：add 遗留数据逐节点全正规 → 通过（静态拒 ≠ 动态拒）
        root = parse_ordered_formula(_ADD, ["x", "other", "alpha"])
        ev = rg.evaluate(root, {
            "x": np.linspace(-10.0, 10.0, 4).astype(F32),
            "other": np.linspace(-5.0, 15.0, 4).astype(F32),
            "alpha": np.array([2.5], dtype=F32),
        })
        diags = rg.dynamic_data_check(ev, _ADD_DOMAINS, rg.leaf_paths(root))
        self.assertEqual(diags, [])
        # 越声明域样本 → 响亮失败而非裁断（§C.4-5）
        ev2 = rg.evaluate(root, {
            "x": np.array([99.0, 0.0, 0.0, 0.0], dtype=F32),
            "other": np.zeros(4, dtype=F32),
            "alpha": np.array([2.5], dtype=F32),
        })
        diags2 = rg.dynamic_data_check(ev2, _ADD_DOMAINS, rg.leaf_paths(root))
        self.assertTrue(any(d.kind == "OUT_OF_DECLARED_RANGE" and d.node_path == "root.l"
                            for d in diags2))


class TestWholeDomainStaticCommitment(unittest.TestCase):
    """步②复核封堵两处静态道漏洞的针对性用例（结果恒零不豁免输入域；病态域不抛断言）。"""

    def test_constant_zero_result_does_not_exempt_input_domain(self):
        # 复核阻断问题①：a*0.0 结果恒零，但声明域 a∈[-1,1] 跨亚正规带 → 静态保守拒绝
        root = parse_ordered_formula("a * 0.0", ["a"])
        st = rg.static_domain_check(root, {"a": (-1.0, 1.0)})
        self.assertFalse(st.ok)
        bands = [d for d in st.diagnostics if d.kind == "SUBNORMAL_BAND"]
        self.assertTrue(bands)
        self.assertEqual(bands[0].node_path, "root.l")        # 拒在输入叶子
        self.assertTrue(all(d.code == "TYPE_LAYOUT_UNSUPPORTED" for d in bands))
        # mul 节点区间恰为 {0}（可证单点）——结果节点不得再叠一条亚正规诊断
        self.assertEqual((st.intervals["root"].lo, st.intervals["root"].hi), (0.0, 0.0))
        self.assertFalse(any(d.kind == "SUBNORMAL_BAND" and d.node_path == "root"
                             for d in st.diagnostics))

    def test_nonfinite_declared_endpoint_no_crash_poisoned(self):
        # 复核阻断问题②：[inf,inf] 声明域：诊断 + 无效状态下传，不得抛 AssertionError(NaN)
        root = parse_ordered_formula("a * 0.0", ["a"])
        st = rg.static_domain_check(root, {"a": (float("inf"), float("inf"))})
        self.assertFalse(st.ok)
        self.assertTrue(any(d.kind == "INPUT_DOMAIN_OUT_OF_D" and d.node_path == "root.l"
                            for d in st.diagnostics))
        self.assertFalse(st.intervals["root.l"].valid)
        self.assertFalse(st.intervals["root"].valid)          # inf×0 未被执行并毒化下传
        # 诊断只在源头声明，后代不重复诊断
        self.assertTrue(all(d.node_path == "root.l" for d in st.diagnostics))
        self.assertTrue(all(d.code == "TYPE_LAYOUT_UNSUPPORTED" for d in st.diagnostics))

    def test_malformed_domain_diagnostics(self):
        # NaN 端点 → MALFORMED_DOMAIN 响亮失败码（不抛异常），子树无效
        root = parse_ordered_formula("a + b", ["a", "b"])
        st = rg.static_domain_check(root, {"a": (float("nan"), 1.0), "b": (1.0, 2.0)})
        self.assertFalse(st.ok)
        self.assertTrue(any(d.kind == "MALFORMED_DOMAIN" and d.node_path == "root.l"
                            for d in st.diagnostics))
        self.assertFalse(st.intervals["root"].valid)
        # 倒置区间 → MALFORMED_DOMAIN，同样无效下传
        st2 = rg.static_domain_check(root, {"a": (2.0, 1.0), "b": (1.0, 2.0)})
        self.assertFalse(st2.ok)
        self.assertTrue(any(d.kind == "MALFORMED_DOMAIN" and "倒置" in d.detail
                            for d in st2.diagnostics))
        self.assertFalse(st2.intervals["root"].valid)

    def test_all_negative_normal_domain_passes(self):
        # 全负正规域（从下方逼近亚正规带但不相交）→ 通过：跨带判定 ≠ 跨零判定
        root = parse_ordered_formula("a + b", ["a", "b"])
        st = rg.static_domain_check(root, {"a": (-1.0, -1e-30), "b": (-1.0, -1e-30)})
        self.assertTrue(st.ok, [str(d) for d in st.diagnostics])

    def test_poison_propagation_through_tree(self):
        # 复合公式 (a * 0.0) + b：a=[inf,inf] 毒化左支一路到根，未涉支 b 保持有效
        root = parse_ordered_formula("(a * 0.0) + b", ["a", "b"])
        st = rg.static_domain_check(root, {"a": (float("inf"), float("inf")),
                                           "b": (1.0, 2.0)})
        self.assertFalse(st.ok)
        self.assertFalse(st.intervals["root.l.l"].valid)      # a 叶子（源头）
        self.assertFalse(st.intervals["root.l"].valid)        # mul 继承
        self.assertFalse(st.intervals["root"].valid)          # add 继承
        self.assertTrue(st.intervals["root.r"].valid)         # b 叶子未涉
        self.assertTrue(all(d.node_path == "root.l.l" for d in st.diagnostics))


class TestIntervalConservatism(unittest.TestCase):
    """§C.4-4：任意采样点的逐节点 fp32 结果必被静态区间包住（随机化属性测试举证，非数学证明）。"""

    def test_random_trees_contained(self):
        rng = random.Random(20260926)

        def rand_tree(depth: int):
            names = ["a", "b"]
            if depth == 0 or rng.random() < 0.4:
                return rng.choice(names)
            op = rng.choice(["+", "*"])
            return f"({rand_tree(depth - 1)}) {op} ({rand_tree(depth - 1)})"

        for trial in range(120):
            with self.subTest(trial=trial):
                formula = rand_tree(3)
                domains = {}
                for name in ("a", "b"):
                    lo = rng.uniform(-8, 0)
                    domains[name] = (lo, lo + rng.uniform(0.01, 16))
                root = parse_ordered_formula(formula, ["a", "b"])
                st = rg.static_domain_check(root, domains)
                pts = {n: np.random.default_rng(trial).uniform(*d, 256).astype(F32)
                       for n, d in domains.items()}
                ev = rg.evaluate(root, pts)
                for path, arr in ev.node_values:
                    iv = st.intervals[path]
                    vals = np.asarray(arr, dtype=F32).reshape(-1).astype(np.float64)
                    self.assertTrue(bool((vals >= iv.lo).all()),
                                    f"{formula} 节点 {path}: {vals.min()} < {iv.lo}")
                    self.assertTrue(bool((vals <= iv.hi).all()),
                                    f"{formula} 节点 {path}: {vals.max()} > {iv.hi}")


if __name__ == "__main__":
    unittest.main()
