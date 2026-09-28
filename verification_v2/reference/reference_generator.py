# -*- coding: utf-8 -*-
"""ADR-056 §C.2/§C.4：逐步 fp32 参考发生器 + 输入数值域双道检查。

§C.2 契约
---------
由 §A.2-1 有序语法树**机械展开**为逐步 fp32 语句序列——每个内部节点一条
``tmp_i = fp32(op(lhs, rhs))``（IEEE-754 binary32，RTNE，每步独立舍入，**禁止 FMA**；
numpy 逐元素 fp32 标量语义天然不融合）。不要求、也不允许为新公式手写参考程序。

§C.4 契约（双道 AND，两道都过才算过；失败诊断码 TYPE_LAYOUT_UNSUPPORTED）
------------------------------------------------------------------------
(a) **静态区间传播**：输入区间为声明采样域（linspace 端点的精确 f32 值），逐节点
    向外包络。拒止：任一节点区间越出 ±FLT_MAX → 溢出拒；任一节点（**含输入叶子，
    对整个声明区间作此承诺，不止两个端点**——结果恒零如 ``a*0.0`` 不豁免输入域
    ⊆ D 的要求）区间与 ``(0, 2^-126) ∪ (-2^-126, 0)`` 相交且不能静态证明恰为单点
    {0} → 亚正规风险拒。声明域本身病态（端点 NaN、lo > hi、非有限 f32 端点）→
    MALFORMED_DOMAIN / INPUT_DOMAIN_OUT_OF_D 诊断并置**无效状态**下传（规格第 5
    条）。**保守拒绝语义写明：静态拒 ≠ 实际输入必错**，诊断建议缩小采样域。
(b) **装载数据逐节点核验**：在实际装载数据上逐步执行（与 golden 产生同工序），
    逐节点精确判定 ∈ D；并核验输入逐点 ∈ 声明域（§C.4-5）。无区间保守性。

区间传播实现规格（§C.4-4 五条的落字）
------------------------------------
1. **端点承载精度与运算**：端点为 Python float（f64）。输入节点端点 = 声明端点的
   精确 f32 值转 f64（输入本身无舍入，不加包络）；单例域（如参数 α=2.5）保持
   ``lo == hi`` 精确单例，不展宽。
2. **向外舍入包络**：内部节点先以 f64 计算端点组合（add：lo+lo / hi+hi；mul：四组合
   取 min/max），每个 f64 端点再向区间外侧 ``math.nextafter`` **一 ulp（f64）**
   （覆盖 f64 自身半 ulp 舍入误差）；随后 RTNE 压到 f32，再向外
   ``np.nextafter`` **一 ulp（f32）**。即「每节点向外 1 ulp（f32）+ 端点运算 1 ulp
   （f64）」。本规格**不自称**完整 IEEE 区间算法（§C.4-4 明文要求）。
3. **精确零处理**：端点恰为 ±0 时**不做任何 ulp 展宽**（否则单点 {0} 的可证性
   会被 f64 亚正规展宽销毁）；节点两圆点恰为 0（符号不限）即「可证单点 {0}」，
   亚正规规则例外放行。±0 端点数值上 ``-0.0 == 0.0``，语义按「零即零」处理。
4. **溢出**：包络端点 ``lo < -FLT_MAX`` 或 ``hi > FLT_MAX``（含 ±inf）→ 溢出拒。
   RTNE 超越 f32 有限域时端点钳为 ±inf 表示「定有溢出」。
5. **无效状态传播**：声明域病态（NaN/倒置端点）或声明端点 RTNE 后非有限（±inf）
   时，该叶子后续端点算术无定义（如 ``inf × 0 = NaN``）——置 ``valid=False`` 的
   无效状态只下传：消费节点直接继承、不再计算、不再重复诊断（诊断只在源头一处）。
   亚正规端点 / 带相交**不置无效**——区间有限成义，仅记诊断（``ok=False`` 由诊断
   保证）。

保守性（由 tests/unit/test_ordered_ast_reference.py 随机化测试举证）：RTNE 单调
非减，故任意采样点的逐步 fp32 结果必落在其节点包络内。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .ordered_ast import Node, iter_nodes

F32 = np.float32
MIN_NORMAL_F32: float = float(np.finfo(F32).tiny)   # 2^-126
FLT_MAX_F32: float = float(np.finfo(F32).max)       # (2-2^-23)·2^127
NEG_INF = float("-inf")
POS_INF = float("inf")

DIAG_NODE_DOMAIN = "TYPE_LAYOUT_UNSUPPORTED"


# ---------------------------------------------------------------------------
# 求值（§C.2）
# ---------------------------------------------------------------------------

@dataclass
class ConstConversion:
    node_path: str
    const_text: str
    py_value: float
    f32_value: float     # float(np.float32(py_value))，RTNE
    f32_bits: int
    in_domain_D: bool


@dataclass
class Evaluation:
    y: np.ndarray                          # f32 终值
    node_values: List[Tuple[str, np.ndarray]]   # 后序：叶子在前、tmp 随后；末项即 root
    const_conversions: List[ConstConversion]


def to_f32(value: float) -> np.float32:
    """Python float → binary32（RTNE；numpy 标量转换）。"""
    return F32(value)


def value_in_D(v: np.float32) -> bool:
    """v ∈ D ⇔ 有限 且（v == 0 或 |v| ≥ 2^-126）。±0 允许；亚正规/inf/NaN 拒绝。"""
    if not np.isfinite(v):
        return False
    a = abs(float(v))
    return a == 0.0 or a >= MIN_NORMAL_F32


def _binary(op: str, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # 发生器照算 IEEE 语义（含溢出到 inf），越 D 由裁决器独立判定后响亮报告，非异常路径
    with np.errstate(over="ignore"):
        if op == "add":
            return np.add(a, b, dtype=F32)
        if op == "mul":
            return np.multiply(a, b, dtype=F32)
    raise AssertionError(f"未知 op {op!r}")  # 语法树工序已排除其他运算符


def evaluate(root: Node, inputs: Dict[str, np.ndarray]) -> Evaluation:
    """按有序树逐步 fp32 求值。

    inputs: 叶子名 → f32 数组（标量参数可为 shape=(1,) 或 ()）。不检查 D/声明域——
    检查是 dynamic_data_check/static_domain_check 的独立职责（§C.4 双道正好靠
    「发生器本身照算、裁决器独立判定」分离）。
    """
    arrays: Dict[str, np.ndarray] = {
        k: np.asarray(v, dtype=F32) for k, v in inputs.items()
    }
    env: Dict[str, np.ndarray] = {}        # node.path -> f32 值
    node_values: List[Tuple[str, np.ndarray]] = []
    conversions: List[ConstConversion] = []

    for n in iter_nodes(root):
        if n.kind == "tensor":
            env[n.path] = arrays[n.name]
        elif n.kind == "const":
            f32v = to_f32(n.value)
            conversions.append(ConstConversion(
                node_path=n.path,
                const_text=str(n.const_text),
                py_value=float(n.value),
                f32_value=float(f32v),
                f32_bits=int(np.float32(f32v).view(np.uint32)),
                in_domain_D=value_in_D(f32v),
            ))
            env[n.path] = f32v             # 0 维 fp32 标量广播
        else:
            env[n.path] = _binary(n.op, env[n.left.path], env[n.right.path])
        node_values.append((n.path, np.asarray(env[n.path], dtype=F32)))

    y = np.asarray(env[root.path], dtype=F32)
    if y.ndim == 0:
        y = y.reshape(1)
    return Evaluation(y=y, node_values=node_values, const_conversions=conversions)


# ---------------------------------------------------------------------------
# 诊断载体
# ---------------------------------------------------------------------------

@dataclass
class Diagnostic:
    code: str
    node_path: str
    kind: str            # INPUT_DOMAIN_OUT_OF_D | CONSTANT_OUT_OF_DOMAIN | NONFINITE
                         # | SUBNORMAL | SUBNORMAL_BAND | OVERFLOW | OUT_OF_DECLARED_RANGE
                         # | MALFORMED_DOMAIN
    detail: str

    def __str__(self) -> str:  # 报告美观
        return f"[{self.code}/{self.kind}] 节点 {self.node_path}: {self.detail}"


# ---------------------------------------------------------------------------
# (b) 装载数据逐节点核验（实际数据，无保守性）
# ---------------------------------------------------------------------------

def dynamic_data_check(
    evaluation: Evaluation,
    declared_domains: Dict[str, Tuple[float, float]],
    leaf_names: Dict[str, str],           # node.path -> 张量名（construct_fn 见下）
) -> List[Diagnostic]:
    """对一次求值的逐节点 f32 值做完备判定；越声明域/越 D 立即列诊断（§C.4-5 响亮失败）。"""
    diags: List[Diagnostic] = []
    for path, arr in evaluation.node_values:
        flat = np.asarray(arr, dtype=F32).reshape(-1)
        name = leaf_names.get(path)
        if name is not None:
            lo, hi = declared_domains[name]
            f64 = flat.astype(np.float64)   # 声明端点是实数界，按 f64 比较，勿压回 f32
            bad = np.nonzero((f64 < float(lo)) | (f64 > float(hi)))[0]
            if bad.size:
                i = int(bad[0])
                diags.append(Diagnostic(
                    DIAG_NODE_DOMAIN, path, "OUT_OF_DECLARED_RANGE",
                    f"输入 {name!r} 第 {i} 点 {float(flat[i])!r} 越声明域 [{lo}, {hi}]，立即停止"))
        nonfinite = np.nonzero(~np.isfinite(flat))[0]
        if nonfinite.size:
            i = int(nonfinite[0])
            diags.append(Diagnostic(
                DIAG_NODE_DOMAIN, path, "NONFINITE",
                f"第 {i} 点 {float(flat[i])!r} 非有限（inf/NaN），D 外"))
            continue
        sub = np.nonzero((flat != 0) & (np.abs(flat) < F32(MIN_NORMAL_F32)))[0]
        if sub.size:
            i = int(sub[0])
            diags.append(Diagnostic(
                DIAG_NODE_DOMAIN, path, "SUBNORMAL",
                f"第 {i} 点 {float(flat[i])!r} 为亚正规（D 外；RTL FTZ 行为未验证故全拒）"))
    return diags


def leaf_paths(root: Node) -> Dict[str, str]:
    """node.path → 张量名的映射（供 dynamic_data_check 标定输入叶子）。"""
    return {n.path: n.name for n in iter_nodes(root) if n.kind == "tensor"}


# ---------------------------------------------------------------------------
# (a) 静态区间传播
# ---------------------------------------------------------------------------

@dataclass
class Interval:
    """f64 端点区间；lo <= hi（``-0.0 == 0.0`` 数值相等即视为同一端点）。

    ``valid=False`` = 上游已诊断的**无效状态**（声明域 NaN/倒置/非有限 f32 端点）：
    该区间的端点算术无定义（``inf × 0 → NaN``），只做下传、不再计算、不再重复诊断。
    亚正规端点/带相交不置无效——区间本身有限成义，仅记诊断（ok=False 由诊断保证）。
    """
    lo: float
    hi: float
    valid: bool = True


def _invalid_interval() -> Interval:
    nan = float("nan")
    return Interval(nan, nan, valid=False)


def _subnormal_band_hit(iv: Interval) -> bool:
    """区间与亚正规带 (0,2^-126)∪(-2^-126,0) 相交且不能静态证明恰为单点 {0}。"""
    if iv.lo == 0.0 and iv.hi == 0.0:
        return False                               # 可证单点 {0} 例外
    hit_pos = iv.hi > 0.0 and iv.lo < MIN_NORMAL_F32
    hit_neg = iv.lo < 0.0 and iv.hi > -MIN_NORMAL_F32
    return hit_pos or hit_neg


def _widen(v: float, toward: float) -> float:
    """f64 端点向外 1 ulp；端点恰为 0 不展宽（保 {0} 可证性）。"""
    if v == 0.0:
        return 0.0
    return math.nextafter(v, toward)


def _envelope(v: float) -> Tuple[float, float]:
    """f64 恰值 → RTNE f32 后向外 1 ulp（f32）；0 保持精确；越有限域钳 ±inf。"""
    if v == 0.0:
        return 0.0, 0.0
    with np.errstate(over="ignore"):        # 越 f32 有限域 → ±inf，紧随其后的钳制即语义本身
        r = F32(v)
    if np.isnan(r):
        raise AssertionError("内部错误：端点 NaN")
    if r == F32(POS_INF):
        return FLT_MAX_F32, POS_INF
    if r == F32(NEG_INF):
        return NEG_INF, -FLT_MAX_F32
    lo = float(np.nextafter(r, F32(NEG_INF)))
    hi = float(np.nextafter(r, F32(POS_INF)))
    return lo, hi


def _combine(op: str, a: Interval, b: Interval) -> Interval:
    if not (a.valid and b.valid):
        return _invalid_interval()                  # 无效状态下传，不做端点算术（inf×0=NaN）
    if op == "add":
        vlo = _widen(a.lo + b.lo, NEG_INF)
        vhi = _widen(a.hi + b.hi, POS_INF)
    else:  # mul：四组合取 min/max（f64 乘积非恰，min 向 -inf、max 向 +inf 各让 1 ulp）
        combos = (a.lo * b.lo, a.lo * b.hi, a.hi * b.lo, a.hi * b.hi)
        vlo = _widen(min(combos), NEG_INF)
        vhi = _widen(max(combos), POS_INF)
    lo1, hi1 = _envelope(vlo)
    lo2, hi2 = _envelope(vhi)
    return Interval(lo=min(lo1, lo2), hi=max(hi1, hi2))


@dataclass
class StaticResult:
    ok: bool
    intervals: Dict[str, Interval]        # node.path -> 区间（含输入叶子；valid=False 表无效）
    diagnostics: List[Diagnostic]


_SUBNORMAL_REJECT_NOTE = (
    "静态保守拒绝：区间与亚正规带相交且无法证明恰为单点 {0}——"
    "这不代表实际输入必错；建议缩小采样域后重验（ADR-056 §C.4）"
)


def static_domain_check(
    root: Node,
    declared_domains: Dict[str, Tuple[float, float]],
) -> StaticResult:
    """逐节点静态区间传播 + §C.4(a) 拒止规则。declared_domains 端点以 yaml 声明原值传入。

    输入叶子：**整个声明区间**须 ⊆ D（不止两个端点）——区间跨亚正规带且非单点 {0}
    即保守拒绝（SUBNORMAL_BAND）；NaN/倒置声明域以 MALFORMED_DOMAIN 诊断响亮失败
    （不抛异常）；非有限 f32 端点 → INPUT_DOMAIN_OUT_OF_D + 子树无效下传。"""
    intervals: Dict[str, Interval] = {}
    diags: List[Diagnostic] = []

    for n in iter_nodes(root):
        if n.kind == "tensor":
            if n.name not in declared_domains:
                raise ValueError(f"缺少张量 {n.name!r} 的声明域")
            lo_d, hi_d = float(declared_domains[n.name][0]), float(declared_domains[n.name][1])
            # 整形诊断：声明域本身须成区间（响亮失败码 TYPE_LAYOUT_UNSUPPORTED，非异常）
            if math.isnan(lo_d) or math.isnan(hi_d):
                diags.append(Diagnostic(
                    DIAG_NODE_DOMAIN, n.path, "MALFORMED_DOMAIN",
                    f"输入 {n.name!r} 声明域端点含 NaN：[{lo_d}, {hi_d}]，立即停止静态判定"))
                intervals[n.path] = _invalid_interval()
                continue
            if lo_d > hi_d:
                diags.append(Diagnostic(
                    DIAG_NODE_DOMAIN, n.path, "MALFORMED_DOMAIN",
                    f"输入 {n.name!r} 声明域端点倒置：[{lo_d}, {hi_d}]（lo > hi），无法成区间"))
                intervals[n.path] = _invalid_interval()
                continue
            flo, fhi = F32(lo_d), F32(hi_d)
            # 声明端点自身须在 D：端点是实际可装载值
            eps_finite = True
            for label, e in (("下界", flo), ("上界", fhi)):
                if not value_in_D(e):
                    diags.append(Diagnostic(
                        DIAG_NODE_DOMAIN, n.path, "INPUT_DOMAIN_OUT_OF_D",
                        f"输入 {n.name!r} 声明域{label} {float(e)!r} 在 D 外（亚正规/非有限域端点不允许）"))
                    if not np.isfinite(e):
                        eps_finite = False
            if not eps_finite:
                # 非有限端点的端点算术无定义（如 inf×0→NaN）：毒化该叶子，子树无效下传
                intervals[n.path] = _invalid_interval()
                continue
            iv = Interval(float(flo), float(fhi))
            intervals[n.path] = iv
            # §C.4 静态承诺覆盖整个声明区间（含内部采样点），结果恒零（如 a*0.0）
            # 不豁免输入域 ⊆ D 的要求：跨带且非单点 {0} → 保守拒绝
            if _subnormal_band_hit(iv):
                diags.append(Diagnostic(
                    DIAG_NODE_DOMAIN, n.path, "SUBNORMAL_BAND",
                    f"输入 {n.name!r} 声明域 [{iv.lo!r}, {iv.hi!r}] 与亚正规带 "
                    f"(-2^-126,0)∪(0,2^-126) 相交。" + _SUBNORMAL_REJECT_NOTE))
            continue
        if n.kind == "const":
            f32v = to_f32(n.value)
            if not value_in_D(f32v):
                diags.append(Diagnostic(
                    DIAG_NODE_DOMAIN, n.path, "CONSTANT_OUT_OF_DOMAIN",
                    f"常量 {n.const_text!r} RTNE 后 {float(f32v)!r} 在 D 外"))
            if np.isfinite(f32v):
                intervals[n.path] = Interval(float(f32v), float(f32v))
            else:
                intervals[n.path] = _invalid_interval()      # 非有限常量同样毒化子树
            continue
        a, b = intervals[n.left.path], intervals[n.right.path]
        iv = _combine(n.op, a, b)
        intervals[n.path] = iv
        if not iv.valid:
            continue                                  # 无效状态只下传，不重复诊断

        if _subnormal_band_hit(iv):
            diags.append(Diagnostic(
                DIAG_NODE_DOMAIN, n.path, "SUBNORMAL_BAND",
                f"区间 [{iv.lo!r}, {iv.hi!r}] 与亚正规带 (-2^-126,0)∪(0,2^-126) 相交。"
                + _SUBNORMAL_REJECT_NOTE))
        if iv.lo < -FLT_MAX_F32 or iv.hi > FLT_MAX_F32:
            diags.append(Diagnostic(
                DIAG_NODE_DOMAIN, n.path, "OVERFLOW",
                f"区间 [{iv.lo!r}, {iv.hi!r}] 越出 ±FLT_MAX：溢出拒"))
    return StaticResult(ok=not diags, intervals=intervals, diagnostics=diags)
