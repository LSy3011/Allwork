#!/usr/bin/env python3
"""addcmul 静态确认探针（2026-10-12 用户令：addcmul 第二复杂案例·五步①静态确认）。

只读探针：不改任何现有文件、不跑发射链与 VCS；全部结论为静态/求解器实测，
输出落盘到 verification_v2/artifacts/addcmul_static_20261012/probe_output.txt。

确认项（沿 horner_v3 步①同型清单，加 addcmul 特有的三因子乘积考点）：
  A. 公式原文 + 有序 AST（两变体：遗留文本 x + value*t1*t2 与对齐文本
     x + t1*t2*value）——有序浮点语义的锚；
  B. SymPy 规范化树 ↔ 有序 AST 逐变体比对——addcmul 的 value*t1*t2 是三因子
     乘积，SymPy 按 args 规范序（经验证 = (t1,t2,value)）平坦化后由
     standard_kernels.lower_mul 左折叠 → DFG 结合序 = ((t1·t2)·value)，与遗留
     文本序 ((value·t1)·t2) 不同（fp32 乘结合不安全）——本层即鉴别该分歧；
  C. DFG 计算链（post-bypass MIR，调度对象本身）签名 ↔ 两变体 AST 签名；
  D. 调度解：II（census 归档值 3）、队列数、逐队列槽占用/span——**U9 正面
     取证：fpmul 双计算槽判定由【推断】升级为求解器实测**；方法学对照 =
     scale_sum 同路径重解须复现已校准点（II=2、rtovr-14 span=3、L(240)=128）；
  E. 域闸：遗留 test_data（x[-1,1]/t1[-2,2]/t2[0,5] 均跨 0 → 预期
     SUBNORMAL_BAND ×3，precheck 已实测一次）+ 两组候选安全域预检
     （U8 同型；正式值归步②定案）；
  F. 容量构造预测（ADR-057 D1 通式 L_q(m)=II·(m+span_q)+2 ≤ 128）——
     **未校准**，不得用于准入。
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

OUT = ROOT / "verification_v2" / "artifacts" / "addcmul_static_20261012"
IQ_DEPTH = 128
COMPUTE_OPS = {"FPMUL": "mul", "FPADD": "add"}

LEGACY_FORMULA = "x + value * t1 * t2"     # 现存 addcmul.yaml 原文
ALIGNED_FORMULA = "x + t1 * t2 * value"    # 对齐变体：文本序 == SymPy 折叠序

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
    """SymPy 树的同口径规范化签名；n 元节点左折叠并标记（args 已按规范序）。"""
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
    yaml_path = ROOT / "verification_v2" / "ops" / "addcmul.yaml"
    spec_yaml = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    formula = spec_yaml["formula"]
    tensor_names = [n for n, c in spec_yaml["tensors"].items()
                    if c.get("role") in ("input", "param")]
    assert formula == LEGACY_FORMULA, f"addcmul.yaml formula 漂移: {formula!r}"

    emit("=" * 72)
    emit("addcmul 静态确认探针（2026-10-12）——只读，无发射/VCS")
    emit("=" * 72)

    # ---------------- A. 公式与有序 AST（两变体） ----------------
    emit("\n=== A. 公式与有序 AST（有序浮点语义锚）===")
    emit(f"遗留文本（addcmul.yaml）: {formula}")
    emit(f"对齐文本（候选变体）     : {ALIGNED_FORMULA}")
    roots = {}
    for tag, text in (("legacy", LEGACY_FORMULA), ("aligned", ALIGNED_FORMULA)):
        root = parse_ordered_formula(text, tensor_names)
        roots[tag] = root
        emit(f"\n[{tag}] tree_signature: {tree_signature(root)}")
        seq = []
        for nd in iter_nodes(root):
            if nd.kind == "binop":
                seq.append(f"{nd.op}({canon_sig_ast(nd.left)},{canon_sig_ast(nd.right)})")
        emit(f"[{tag}] 求值拓扑序（后序左先，binop {len(seq)} 个）:")
        for i, s in enumerate(seq):
            emit(f"  [{i}] {s}")
        emit(f"[{tag}] 交换律规范化签名: {canon_sig_ast(root)}")

    # ---------------- B. SymPy 规范化树比对（两变体） ----------------
    emit("\n=== B. SymPy 规范化树 ↔ 有序 AST（逐变体）===")
    sigs = {}
    for tag, text in (("legacy", LEGACY_FORMULA), ("aligned", ALIGNED_FORMULA)):
        expr = sp.sympify(text)
        emit(f"\n[{tag}] srepr: {sp.srepr(expr)}")
        muls = [a for a in expr.args if isinstance(a, sp.Mul)]
        if muls:
            emit(f"[{tag}] Mul.args 规范序: {[str(a) for a in muls[0].args]}")
        sig_sym = canon_sig_sympy(expr)
        sig_ast = canon_sig_ast(roots[tag])
        sigs[tag] = (sig_sym, sig_ast)
        emit(f"[{tag}] SymPy 签名: {sig_sym}")
        emit(f"[{tag}] AST   签名: {sig_ast}")
        emit(f"[{tag}] MATCH_ORDERED_MODULO_COMM: {sig_sym == sig_ast}"
             f"  （False = 结合性/结构被改写）")
        # NARY 标记是 3+ 因子平坦化的机械标记；剥掉后再比，区分
        # 「仅标记差异（折叠序与 AST 一致）」与「结合序实质分歧」。
        folded = sig_sym.replace("NARY3:", "").replace("NARY2:", "")
        emit(f"[{tag}] 剥 NARY 标记后 MATCH: {folded == sig_ast}"
             f"（False = 折叠结合序本身与文本序分歧——fp32 乘结合不安全，"
             f"名义语义与编译语义不一致）")
    same_expr = sp.simplify(sp.sympify(LEGACY_FORMULA)
                            - sp.sympify(ALIGNED_FORMULA)) == 0
    emit(f"\n两变体 sympify 后同一 SymPy 树（simplify 差 == 0）: {same_expr}"
         f"——为真则两文本共享同一 DFG，对齐纯为文本层选择")

    # ---------------- C/D. DFG + 调度解 ----------------
    hw = HardwareSpec.load(str(ROOT / "hw_config"))

    def solve(name: str):
        # 每算子新求解器实例：2026-10-12 实测 Z3 模型挑选（槽位/时刻）随进程
        # 状态漂移（scale_sum rtovr-14 时刻三进程 [3,8]、函数路径 [1,6]、
        # 本探针首跑 span=2 实例），最优 II 值稳定；span 属观察量非可复现常量。
        syn = UCodeSynthesizer(hw)
        op = ConfigurableOperator(str(ROOT / "verification_v2" / "ops" / f"{name}.yaml"))
        ops = op.get_dfg(hw)
        ops_b = apply_rtovr_sparse_bypass(ops, hw)
        res = syn.solve_with_units(ops_b, MII=1, MAX_II=64)
        return op, ops_b, res

    emit("\n=== C. DFG 计算链（post-bypass MIR，调度对象本身）===")
    op_h, ops_h, res_h = solve("addcmul")
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
    roots_dfg = [o for o in compute if o.trace_id not in consumed]
    emit(f"计算树根（不被其他计算节点消费）: {len(roots_dfg)} 个")
    linear = all(v == 1 for v in consumed.values())
    emit(f"链线性（每个非根计算节点恰 1 个计算消费者）: {linear}")
    if len(roots_dfg) == 1:
        sig_dfg = sig_of(roots_dfg[0])
        emit(f"DFG 计算树交换律规范化签名: {sig_dfg}")
        emit(f"  ↔ legacy AST  : {sig_dfg == sigs['legacy'][1]}")
        emit(f"  ↔ aligned AST : {sig_dfg == sigs['aligned'][1]}")
        emit("计算链（叶→根，depends_on 声明序）:")
        chain = []
        cur = roots_dfg[0]
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
        emit("addcmul: UNSAT/UNKNOWN（solve 返回 None）——静态确认失败")
    else:
        ii = res_h["II"]
        sched = res_h["schedule"]
        umap = res_h["unit_assignments"]
        emit(f"addcmul: II = {ii}（census 归档值 3）")
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
        emit(f"U9 取证: fpmul 双计算槽 → {bool(multi_slot_compute)} "
             f"{multi_slot_compute}（【推断】→ 求解器实测）")
        full = [q for q in per_q
                if len({t % ii for _, t in per_q[q]}) == ii]
        emit(f"满槽队列（{ii}/{ii} 无气泡）: {full}")

        # ---------------- F. 容量构造预测（未校准） ----------------
        emit("\n=== F. 容量构造预测（ADR-057 D1 通式；**未校准，禁用于准入**）===")
        emit("L_q(m) = II·(m + span_q) + 2 ≤ 128，m = n/4 逻辑迭代数；"
             "校准锚 = scale_sum 实测 n_max=240（见 D-control）")

        def l_of(q, m):
            return ii * (m + spans[q]) + 2
        m_max = max(m for m in range(0, 4096)
                    if all(l_of(q, m) <= IQ_DEPTH for q in per_q))
        bind_at = [q for q in per_q if l_of(q, m_max + 1) > IQ_DEPTH]
        ns = (4, 8, 32) + tuple(sorted({4 * m_max - 4, 4 * m_max, 4 * m_max + 4}))
        header = f"{'queue':<14}{'span':>5}" + "".join(f"{f'n={n}':>9}" for n in ns)
        emit(header)
        for q in sorted(per_q, key=lambda q: -spans[q]):
            row = f"{q:<14}{spans[q]:>5}"
            for n in ns:
                row += f"{ii * (n // 4 + spans[q]) + 2:>9}"
            emit(row)
        emit(f"束缚队列（n=4·(m_max+1) 时首破墙者）= {bind_at} → m_max = {m_max} → "
             f"n_max 构造预测 = {4 * m_max}"
             f"（L({bind_at[0]})={l_of(bind_at[0], m_max)} 恰顶格内）；"
             f"n={4 * (m_max + 1)} 应响拒（L={l_of(bind_at[0], m_max + 1)} > 128）")

    # ---------------- D-control. scale_sum 方法学对照 ----------------
    emit("\n=== D-control. scale_sum 同路径重解（方法学锚定）===")
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
        t14 = sorted(per_qs.get("rtovr-14", [0]))
        span_rtovr14 = max(t14) // ii_s - min(t14) // ii_s
        l_240 = ii_s * (240 // 4 + span_rtovr14) + 2
        emit(f"scale_sum: II={ii_s}（断言 ==2：最优 II 为约束系统属性，跨进程稳定）")
        emit(f"  本进程重解 rtovr-14 times={t14} span={span_rtovr14} → L(240)={l_240}"
             f"（观察量：Z3 模型挑选随进程状态漂移——2026-10-12 三实例实录 "
             f"[3,8]/span3、[1,6]/span3、span2 各一；**容量校准锚不取自重解**，"
             f"取自已发射产物实测：ADR-056 步⑤ n=240 rtovr-14 恰 128 行 ⇒ "
             f"发射调度 span=(128-2)/2-60=3，VCS 校准 n_max=240）")
        emit(f"方法学对照: {'PASS' if ii_s == 2 else 'FAIL——II 复现失败，容量口径作废'}"
             f"（口径：II 复现为对照项；span/L 仅观察，构造预测一律未校准、禁用于准入）")

    # ---------------- E. 域闸 ----------------
    emit("\n=== E. 域闸（声明域 = yaml test_data，同 _declared_domains 约定）===")

    def domains_of(td: dict) -> dict:
        d = {}
        for name, entry in (td or {}).items():
            if isinstance(entry, dict) and "range" in entry:
                d[name] = (float(entry["range"][0]), float(entry["range"][1]))
            elif isinstance(entry, list) and len(entry) == 1:
                d[name] = (float(entry[0]), float(entry[0]))
        return d

    legacy_domains = domains_of(spec_yaml.get("test_data"))
    st = rg.static_domain_check(roots["legacy"], legacy_domains)
    emit(f"遗留域 {legacy_domains}")
    emit(f"static_domain_check(legacy): ok={st.ok}；诊断 = "
         f"{[(d.kind, d.node_path) for d in st.diagnostics] or '无'}"
         f"（precheck 实测 = SUBNORMAL_BAND 拒绝，本轮复核）")

    emit("\n--- 候选安全域预检（U8 同型；正式值归步②定案，此处仅证可行性）---")
    cands = {
        "cand-A（全正）": {"x": (0.25, 1.75), "t1": (0.5, 2.5),
                          "t2": (0.75, 3.0), "value": (0.5, 0.5)},
        "cand-B（含负）": {"x": (-1.75, -0.25), "t1": (0.5, 2.5),
                           "t2": (-3.0, -0.75), "value": (0.5, 0.5)},
    }
    for tag, doms in cands.items():
        stc = rg.static_domain_check(roots["aligned"], doms)
        emit(f"{tag} {doms} → ok={stc.ok}；诊断 = "
             f"{[(d.kind, d.node_path) for d in stc.diagnostics] or '无'}")

    emit("\n=== E2. 有序参考点检（n=4，动态道 + 求值锚，遗留 yaml 实数据）===")
    import numpy as np
    op4 = ConfigurableOperator(str(yaml_path))
    inputs = op4.prepare_input(4)
    ev = rg.evaluate(roots["legacy"], {k: np.asarray(v, dtype=rg.F32)
                                       for k, v in inputs.items() if k in tensor_names})
    dyn = rg.dynamic_data_check(ev, legacy_domains, rg.leaf_paths(roots["legacy"]))
    emit(f"dynamic_data_check: 诊断 {len(dyn)} 项"
         f"{('——' + str(dyn[0])) if dyn else '（空 = 通过）'}")
    emit(f"evaluate 终值（有序 AST 逐节点 fp32）: "
         f"{[float(v) for v in np.asarray(ev.y, dtype=rg.F32).ravel()[:4]]}")
    for leaf in ("x", "t1", "t2", "value"):
        if leaf in inputs:
            emit(f"  输入 {leaf} = "
                 f"{[float(v) for v in np.asarray(inputs[leaf]).ravel()[:4]]}")

    (OUT / "probe_output.txt").write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT / 'probe_output.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
