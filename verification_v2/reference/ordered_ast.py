# -*- coding: utf-8 -*-
"""ADR-056 §A.2-1 / §C.2：V1 公式**有序语法树**（文本级 AST 工序）。

职责
----
1. 从**原始公式文本**建有序语法树：保留用户书写的运算顺序与结合性
   （Python ``ast`` 保序：``a+b`` 与 ``b+a`` 不同；``(a+b)+c`` 与 ``a+(b+c)`` 不同）。
   判定时机在 SymPy 之前——SymPy 会把 ``-x`` 规整为 ``Mul(-1, x)``，改写后
   字面量与取负不可区分（§A.2-1 明文）。
2. 非法语法/结构的响亮拒绝：诊断码 ``SYNTAX_UNSUPPORTED``，信息带
   语法树路径与病灶节点摘要；**不静默、不回落、不猜测**。

语法合同（§A.2-1 逐条）
----------------------
* 运算符 ⊆ {``+``, ``*``}；函数调用、幂、除法、二元减法、比较/选择全部拒绝。
* 一元取负二分：**负的 fp32 常量字面量允许**（``x + (-2.5)``、``-3.0 * x`` 中的
  字面量 ``-3.0``）；**对表达式取负拒绝**（``-x``、``-(x+y)``、``-alpha``）。
* 一元正号 ``+x``（``UnaryOp(UAdd)``）**允许**：对 fp32 位模式恒等（hco notes-3）。
* 名字只允许声明过的输入张量（role=input/param）；未知的、输出名、常驻
  ``constants`` bank 名一律拒绝。
* 常量：int/float 字面量；bool / 复数拒绝；非有限字面量（``1e999``）拒绝。
  常量的 fp32(RTNE) 舍入发生在求值器，其 D 域判定也在求值器
  （reference_generator，§C.4(a)）。

本模块只依赖标准库（编译端与参考端共用本树，§C.2）。
"""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple


class FormulaRejection(ValueError):
    """公式解析/结构拒绝的诊断载体。code 走 §D.2 的 SYNTAX_UNSUPPORTED 族。"""

    def __init__(self, code: str, message: str, node_path: Optional[str] = None):
        self.code = code
        self.node_path = node_path
        super().__init__(f"[{code}] {message}" + (f"（节点 {node_path}）" if node_path else ""))


@dataclass
class Node:
    """有序语法树节点。path 为诊断定位：root / root.l / root.r / root.l.r ……"""

    path: str
    kind: str                              # "tensor" | "const" | "binop"
    name: Optional[str] = None             # kind == "tensor"
    const_text: Optional[str] = None       # kind == "const"（原文，如 "-2.5"）
    value: Optional[float] = None          # kind == "const"（Python float；f32 舍入在求值器）
    op: Optional[str] = None               # kind == "binop"："add" | "mul"
    left: Optional["Node"] = None
    right: Optional["Node"] = None


_BIN_OPS = {ast.Add: "add", ast.Mult: "mul"}


def _excerpt(node: ast.AST, limit: int = 120) -> str:
    return ast.dump(node)[:limit]


def _literal(node: ast.Constant, path: str) -> Tuple[float, str]:
    v = node.value
    if isinstance(v, bool):
        raise FormulaRejection("SYNTAX_UNSUPPORTED", f"布尔字面量 {v!r} 非 fp32 常量", path)
    if not isinstance(v, (int, float)):
        raise FormulaRejection("SYNTAX_UNSUPPORTED", f"非常量数值字面量 {v!r}", path)
    fv = float(v)
    if not math.isfinite(fv):
        raise FormulaRejection("SYNTAX_UNSUPPORTED", f"非有限字面量 {v!r}（RTNE 前值已非有限）", path)
    return fv, repr(v)


def _build(node: ast.AST, path: str, allowed: frozenset, reserved: Sequence[str]) -> Node:
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise FormulaRejection(
                "SYNTAX_UNSUPPORTED",
                f"二元运算符 {type(node.op).__name__} 范围外（V1 仅允许 +、*）：{_excerpt(node)}",
                path,
            )
        return Node(
            path=path, kind="binop", op=op,
            left=_build(node.left, path + ".l", allowed, reserved),
            right=_build(node.right, path + ".r", allowed, reserved),
        )
    if isinstance(node, ast.Name):
        name = node.id
        if name in reserved:
            raise FormulaRejection(
                "SYNTAX_UNSUPPORTED",
                f"名字 {name!r} 为保留名（输出/常驻 bank），不可在公式中引用；"
                f"允许名字：{sorted(allowed)}",
                path,
            )
        if name not in allowed:
            raise FormulaRejection(
                "SYNTAX_UNSUPPORTED",
                f"未声明名字 {name!r}；允许名字：{sorted(allowed)}",
                path,
            )
        return Node(path=path, kind="tensor", name=name)
    if isinstance(node, ast.Constant):
        fv, text = _literal(node, path)
        return Node(path=path, kind="const", const_text=text, value=fv)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            if isinstance(node.operand, ast.Constant):
                fv, text = _literal(node.operand, path)
                return Node(path=path, kind="const", const_text="-" + text, value=-fv)
            raise FormulaRejection(
                "SYNTAX_UNSUPPORTED",
                f"对表达式取负范围外（仅允许负的数值字面量，如 (-2.5)）：{_excerpt(node)}",
                path,
            )
        if isinstance(node.op, ast.UAdd):
            # §A.2-1：一元正号允许（对 fp32 位模式恒等），结构层直接穿透
            return _build(node.operand, path, allowed, reserved)
        raise FormulaRejection(
            "SYNTAX_UNSUPPORTED",
            f"一元运算符 {type(node.op).__name__} 范围外：{_excerpt(node)}",
            path,
        )
    raise FormulaRejection(
        "SYNTAX_UNSUPPORTED",
        f"语法节点 {type(node).__name__} 范围外（禁止函数调用/下标/属性/比较/布尔运算等）："
        f"{_excerpt(node)}",
        path,
    )


def parse_ordered_formula(
    formula_text: str,
    tensor_names: Sequence[str],
    reserved_names: Sequence[str] = ("y", "constants"),
) -> Node:
    """原始公式文本 → 有序语法树根。

    参数
    ----
    formula_text : YAML ``formula:`` 字段原文。
    tensor_names : 允许引用的输入张量名（role=input/param 的声明名）。
    reserved_names : 禁止引用名（输出名 / 常驻 constants bank）。

    返回根 Node；一切非法输入以 FormulaRejection(SYNTAX_UNSUPPORTED) 响亮拒绝。
    """
    if not isinstance(formula_text, str) or not formula_text.strip():
        raise FormulaRejection("SYNTAX_UNSUPPORTED", "公式文本为空")
    allowed = frozenset(tensor_names)
    try:
        tree = ast.parse(formula_text, mode="eval")
    except SyntaxError as e:
        raise FormulaRejection("SYNTAX_UNSUPPORTED", f"公式文本无法解析：{e.msg}") from e
    if not isinstance(tree, ast.Expression) or not isinstance(tree.body, ast.AST):
        raise FormulaRejection("SYNTAX_UNSUPPORTED", "公式必须是单个表达式（禁止语句）")
    return _build(tree.body, "root", allowed, tuple(reserved_names))


def iter_nodes(root: Node) -> List[Node]:
    """后序（左先右后）枚举全部节点：每个 binop 的孩子先于自身。求值拓扑序。"""
    out: List[Node] = []

    def rec(n: Node) -> None:
        if n.kind == "binop":
            rec(n.left)
            rec(n.right)
        out.append(n)

    rec(root)
    return out


def tree_signature(node: Node) -> str:
    """结构签名（保序）：用于保序性测试与 §E.2 规范化记录。"""
    if node.kind == "tensor":
        return str(node.name)
    if node.kind == "const":
        return f"const({node.const_text})"
    return f"({node.op} {tree_signature(node.left)} {tree_signature(node.right)})"
