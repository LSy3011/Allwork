#!/usr/bin/env python3
"""horner_v3 静态确认探针（2026-10-12 用户令五步①：静态确认）。

只读探针：不改任何现有文件、不跑发射链与 VCS；全部结论为静态/求解器实测，
输出落盘到 verification_v2/artifacts/horner_v3_static_20261012/probe_output.txt。

确认项（对应用户令五步①的交付清单）：
  A. 公式原文 + 有序 AST（tree_signature / 求值拓扑序）——有序浮点语义的锚；
  B. SymPy 规范化树（编译器前端实际消费）与有序 AST 的逐节点对应
     ——交换律层规范化签名相等性机器判定（结合性改变会判 MISMATCH）；
  C. DFG 计算链：post-bypass MIR 中提取 FPMUL/FPADD 依赖树，与有序 AST
     同一规范化签名比对——证明「调度对象 = 文本有序公式」（模逐位安全交换）；
  D. 调度解（get_dfg → apply_rtovr_sparse_bypass → solve_with_units(1..64)）：
     II、队列数、逐队列槽占用（slot = t mod II）、span；正面确认
     「II>2 且同计算队列（fpadd/fpmul）多有效槽」；
     方法学对照 = scale_sum 同路径重解须复现已知结论（II=2、rtovr-14 span=3、
     L(60)=128 恰顶格 ↔ ADR-056 步⑤实测 n_max=240 校准点）；
  E. 域闸：static_domain_check（声明域 = yaml test_data，同 run_v1_verify
     _declared_domains 约定）+ n=4 动态道点检；
  F. 容量构造预测（ADR-057 D1 通式 L_q(m)=II·(m+span_q)+2 ≤ 128，m=n/4
     逻辑迭代数）——**未校准**，不得用于准入；给出逐队列 L_q 表与束缚队列。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/home/juno1023/aiacc_compiler")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import sympy as sp  # noqa: E402
import yaml  # noqa: E402

from api.hw_spec import HardwareSpec  # noqa: E402
from verification_v2.ops.configurable import ConfigurableOperator  # noqa: E402
from synthesis.ucode_solver import UCodeSynthesizer  # noqa: E402
from frontend.rtovr_bypass import apply_rtovr_sparse_bypass  # noqa: E402
from verification_v2.reference.ordered_ast import (  # noqa: E402
    iter_nodes, parse_ordered_formula, tree_signature)
from verification_v2.reference import reference_generator as rg  # noqa: E402

OUT = ROOT / "verification_v2" / "artifacts" / "horner_v3_static_20261012"
IQ_DEPTH = 128
COMPUTE_OPS = {"FPMUL": "mul", "FPADD": "add"}

_lines: list[str] = []


def emit(s: str = "") -> None:
    print(s, flush=True)
    _lines.append(s)


# ---------------------------------------------------------------- 签名工具
def canon_sig_ast(node) -> str:
    """有序 AST 的交换律层规范化签名（孩子排序——fp32 加/乘逐位交换安全）。"""
    if node.kind == "tensor":
        return str(node.name)
    if node.kind == "const":
        return f"const({node.const_text})"
    kids = sorted([canon_sig_ast(node.left), canon_sig_ast(node.right)])
    return f"({node.op} {kids[0]} {kids[1]})"


def canon_sig_sympy(e) -> str:
    """SymPy 树的同口径规范化签名；n 元节点左折叠并标记（horner 预期全二元）。"""
    if e.is_Symbol:
        return str(e)
    if e.is_Number:
        return f"const({e})"
    if isinstance(e, sp.Add):
        op = "add"
    elif isinstance(e, sp.Mul):
        op = "mul"
    else:
        return f"OTHER({type(e).__name__}:{e})"
    kids = sorted(canon_sig_sympy(a) for a in e.args)
    sig = kids[0]
    for k in kids[1:]:
        sig = f"({op} {sig} {k})"
    if len(e.args) != 2:
        sig = f"NARY{len(e.args)}:{sig}"
    return sig


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    yaml_path = ROOT / "verification_v2" / "ops" / "horner_v3.yaml"
    spec_yaml = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    formula = spec_yaml["formula"]
    tensor_names = [n for n, c in spec_yaml["tensors"].items()
                    if c.get("role") in ("input", "param")]

    emit("=" * 72)
    emit("horner_v3 静态确认探针（2026-10-12）——只读，无发射/VCS")
    emit("=" * 72)

    # ---------------- A. 公式与有序 AST ----------------
    emit("\n=== A. 公式与有序 AST（有序浮点语义锚）===")
    emit(f"formula: {formula}")
    root = parse_ordered_formula(formula, tensor_names)
    emit(f"tree_signature: {tree_signature(root)}")
    seq = []
    for nd in iter_nodes(root):
        if nd.kind == "binop":
            seq.append(f"{nd.op}({canon_sig_ast(nd.left)},{canon_sig_ast(nd.right)})")
    emit(f"求值拓扑序（后序左先，binop {len(seq)} 个）:")
    for i, s in enumerate(seq):
        emit(f"  [{i}] {s}")
    sig_ast = canon_sig_ast(root)
    emit(f"交换律规范化签名: {sig_ast}")

    # ---------------- B. SymPy 规范化树比对 ----------------
    emit("\n=== B. SymPy 规范化树 ↔ 有序 AST（逐节点保序判定）===")
    expr = sp.sympify(formula)
    emit(f"srepr: {sp.srepr(expr)}")
    sig_sym = canon_sig_sympy(expr)
    emit(f"交换律规范化签名: {sig_sym}")
    match_b = (sig_sym == sig_ast)
    emit(f"MATCH_ORDERED_MODULO_COMM: {match_b}"
         f"  （False = 结合性/结构被改写，有序语义不成立，须停用）")

    # ---------------- C/D. DFG + 调度解 ----------------
    hw = HardwareSpec.load(str(ROOT / "hw_config"))
    syn = UCodeSynthesizer(hw)

    def solve(name: str):
        op = ConfigurableOperator(str(ROOT / "verification_v2" / "ops" / f"{name}.yaml"))
        ops = op.get_dfg(hw)
        ops_b = apply_rtovr_sparse_bypass(ops, hw)
        res = syn.solve_with_units(ops_b, MII=1, MAX_II=64)
        return op, ops_b, res

    emit("\n=== C. DFG 计算链（post-bypass MIR，调度对象本身）===")
    op_h, ops_h, res_h = solve("horner_v3")
    by_tid = {o.trace_id: o for o in ops_h}
    compute = [o for o in ops_h if o.op_type in COMPUTE_OPS]
    emit(f"MIR 节点总数 {len(ops_h)}；计算节点 {len(compute)} 个: "
         + ", ".join(f"{o.op_type}" for o in compute))

    import re

    def leaf_name(tid: str) -> str:
        m = re.match(r"rt_(.+?)_read_", tid)
        return m.group(1) if m else f"?({tid})"

    def sig_of(o, depth: int = 0) -> str:
        """沿 rtovr_deps（旁路后生产者直指）回溯计算树；叶 = 张量名。"""
        if depth > 16:
            return "DEPTH!"
        kids = []
        for tid in (o.metadata.get("rtovr_deps") or {}):
            prod = by_tid.get(tid)
            if prod is not None and prod.op_type in COMPUTE_OPS:
                kids.append(sig_of(prod, depth + 1))
            else:
                kids.append(leaf_name(tid))
        kids.sort()
        opn = COMPUTE_OPS[o.op_type]
        return f"({opn} {kids[0]} {kids[1]})" if len(kids) == 2 \
            else f"ARITY{len(kids)}({opn}:{','.join(kids)})"

    consumed: dict[str, int] = {}
    for o in compute:
        for tid in (o.metadata.get("rtovr_deps") or {}):
            prod = by_tid.get(tid)
            if prod is not None and prod.op_type in COMPUTE_OPS:
                consumed[tid] = consumed.get(tid, 0) + 1
    roots = [o for o in compute if o.trace_id not in consumed]
    emit(f"计算树根（不被其他计算节点消费者）: {len(roots)} 个")
    linear = all(v == 1 for v in consumed.values())
    emit(f"链线性（每个非根计算节点恰 1 个计算消费者）: {linear}")
    if len(roots) == 1:
        sig_dfg = sig_of(roots[0])
        emit(f"DFG 计算树交换律规范化签名: {sig_dfg}")
        emit(f"MATCH_ORDERED_MODULO_COMM: {sig_dfg == sig_ast}")
        # 逐节点展开（从叶到根，展示真实求值链序）
        emit("计算链（叶→根，S0/S1 端口序 = depends_on 声明序）:")
        chain = []
        cur = roots[0]
        while True:
            chain.append(cur)
            nxt = None
            for tid in (cur.metadata.get("rtovr_deps") or {}):
                prod = by_tid.get(tid)
                if prod is not None and prod.op_type in COMPUTE_OPS:
                    nxt = prod
            if nxt is None:
                break
            cur = nxt
        comp_ids = {o.trace_id for o in compute}
        def dep_label(d: str) -> str:
            base = d[3:] if d.startswith("rt_") else d
            for cid in comp_ids:
                if base.startswith(cid):
                    return f"COMPUTE:{cid}"
            return leaf_name(d)
        for o in reversed(chain):
            deps = list(o.metadata.get("depends_on") or [])
            emit(f"  {o.op_type} {o.trace_id}: deps={[dep_label(d) for d in deps]}")
    else:
        emit("  ⚠ 根不唯一——签名比对不适用，需人工核查")

    emit("\n=== D. 调度解（solve_with_units MII=1 MAX_II=64）===")
    if res_h is None:
        emit("horner_v3: UNSAT/UNKNOWN（solve 返回 None）——静态确认失败")
    else:
        ii = res_h["II"]
        sched = res_h["schedule"]
        umap = res_h["unit_assignments"]
        emit(f"horner_v3: II = {ii}（census 归档值 6）")
        per_q: dict[str, list] = {}
        for o in ops_h:
            q = umap.get(o.trace_id)
            if q:
                per_q.setdefault(q, []).append((o.op_type, sched[o.trace_id]))
        emit(f"队列数: {len(per_q)}")
        emit("span_q 定义 = max_t//II − t_min//II（assembler.py:218/395-396 "
             "total_iterations_needed/skip_iterations 原式，ADR-057 D1 同记号）")
        emit(f"{'queue':<14}{'ops':>4}  {'slots(mod II)':<22}{'span_q':>7}  times")
        spans = {}
        multi_slot_compute = []
        for q in sorted(per_q):
            times = sorted(t for _, t in per_q[q])
            slots = sorted({t % ii for t in times})
            span = max(times) // ii - min(times) // ii
            spans[q] = span
            names = "/".join(sorted({n for n, _ in per_q[q]}))
            emit(f"{q:<14}{len(times):>4}  {str(slots):<22}{span:>7}  "
                 f"{times}  [{names}]")
            if q in ("fpadd", "fpmul") and len(slots) > 1:
                multi_slot_compute.append((q, slots))
        emit(f"\n正面确认: II={ii} > 2 → {ii > 2}")
        emit(f"正面确认: 同计算队列多有效槽 → {bool(multi_slot_compute)} "
             f"{multi_slot_compute}")
        full = [q for q in per_q
                if len({t % ii for _, t in per_q[q]}) == ii]
        emit(f"满槽队列（{ii}/{ii} 无气泡）: {full}")

        # ---------------- F. 容量构造预测（未校准） ----------------
        emit("\n=== F. 容量构造预测（ADR-057 D1 通式；**未校准，禁用于准入**）===")
        emit("L_q(m) = II·(m + span_q) + 2 ≤ 128，m = n/4 逻辑迭代数；"
             "校准锚 = scale_sum 实测 n_max=240（见下）")
        header = f"{'queue':<14}{'span':>5}" + "".join(f"{f'n={n}':>9}" for n in
                                                       (4, 8, 60, 64, 68, 72))
        emit(header)
        for q in sorted(per_q, key=lambda q: -spans[q]):
            row = f"{q:<14}{spans[q]:>5}"
            for n in (4, 8, 60, 64, 68, 72):
                m = n // 4
                row += f"{ii * (m + spans[q]) + 2:>9}"
            emit(row)
        def l_of(q, m):
            return ii * (m + spans[q]) + 2
        bind = max(per_q, key=lambda q: l_of(q, 0))
        m_max = max(m for m in range(0, 4096)
                    if all(l_of(q, m) <= IQ_DEPTH for q in per_q))
        bind_at = [q for q in per_q if l_of(q, m_max + 1) > IQ_DEPTH]
        emit(f"束缚队列（n=4·(m_max+1) 时首破墙者）= {bind_at} → m_max = {m_max} → "
             f"n_max 构造预测 = {4 * m_max}"
             f"（L({bind_at[0]})={l_of(bind_at[0], m_max)} 恰顶格内）；"
             f"n={4 * (m_max + 1)} 应响拒（L={l_of(bind_at[0], m_max + 1)} > 128）")

    # ---------------- D-control. scale_sum 方法学对照 ----------------
    emit("\n=== D-control. scale_sum 同路径重解（方法学锚定，对照已校准点）===")
    _op_s, ops_s, res_s = solve("scale_sum")
    if res_s is None:
        emit("scale_sum: UNSAT——方法学对照失败，本探针全部容量结论作废")
    else:
        ii_s = res_s["II"]
        sched_s = res_s["schedule"]
        umap_s = res_s["unit_assignments"]
        per_qs: dict[str, list] = {}
        for o in ops_s:
            q = umap_s.get(o.trace_id)
            if q:
                per_qs.setdefault(q, []).append(sched_s[o.trace_id])
        t14 = per_qs.get("rtovr-14", [0])
        span_rtovr14 = max(t14) // ii_s - min(t14) // ii_s
        l_240 = ii_s * (240 // 4 + span_rtovr14) + 2
        emit(f"scale_sum: II={ii_s}（期望 2）；rtovr-14 span={span_rtovr14}（期望 3）；"
             f"L(n=240, m=60) = {l_240}（期望 128 = 恰顶格 ↔ 实测 n_max=240）")
        emit(f"方法学对照: {'PASS' if (ii_s == 2 and span_rtovr14 == 3 and l_240 == 128) else 'FAIL——容量预测口径作废'}")

    # ---------------- E. 域闸 ----------------
    emit("\n=== E. 域闸（声明域 = yaml test_data，同 _declared_domains 约定）===")
    domains = {}
    for name, entry in (spec_yaml.get("test_data") or {}).items():
        if isinstance(entry, dict) and "range" in entry:
            domains[name] = (float(entry["range"][0]), float(entry["range"][1]))
        elif isinstance(entry, list) and len(entry) == 1:
            domains[name] = (float(entry[0]), float(entry[0]))
    st = rg.static_domain_check(root, domains)
    emit(f"static_domain_check: ok={st.ok}；诊断 = "
         f"{[(d.kind, d.node_path) for d in st.diagnostics] or '无'}")

    emit("\n=== E2. 有序参考点检（n=4，动态道 + 求值锚）===")
    import numpy as np
    op4 = ConfigurableOperator(str(yaml_path))
    inputs = op4.prepare_input(4)
    ev = rg.evaluate(root, {k: np.asarray(v, dtype=rg.F32)
                            for k, v in inputs.items() if k in tensor_names})
    dyn = rg.dynamic_data_check(ev, domains, rg.leaf_paths(root))
    emit(f"dynamic_data_check: 诊断 {len(dyn)} 项"
         f"{('——' + str(dyn[0])) if dyn else '（空 = 通过）'}")
    xs = np.asarray(inputs["x"], dtype=rg.F32)
    emit(f"evaluate 终值（有序 AST 逐节点 fp32）: "
         f"{[float(v) for v in np.asarray(ev.y, dtype=rg.F32).ravel()[:4]]}")
    emit(f"  输入 x = {[float(v) for v in xs.ravel()[:4]]}；c0..c3 = "
         f"{[float(np.asarray(inputs[c]).ravel()[0]) for c in ('c0','c1','c2','c3')]}")

    (OUT / "probe_output.txt").write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT / 'probe_output.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
