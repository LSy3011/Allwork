#!/usr/bin/env python3
"""a2b_preflight —— A2b 半天预检（2026-10-06 用户指令：产出候选公式的 II、槽结构与
容量预测即可；不立项、不实现、不承诺）。

口径声明（诚实边界）：
    - II 与槽结构：复用 census 同路径（get_dfg → rtovr_bypass → solve_with_units）实测。
    - 容量预测：**DFG 静态预测**，n_max_pred[q] = floor((128 − HDR_OVERHEAD) / ops_per_iter[q])，
      n_max = min_q。**未跑 unroll 准入链**（候选公式无 experimental_unroll 布局声明，
      构造 admission 超预算）；预测与实测的偏差用 scale_sum 校准点量化（实测 n_max=240，
      rtovr-14 恰 128 行顶格，ADR-056 步⑤）。
      ⚠ 2026-10-06 用户裁定：scale_sum 实测 240 vs 预测 63 —— 一次偏保守不证明算法恒保守，
      且疑混用**元素数**与**逻辑迭代数**两种单位（240/60≈4，恰为量级线索，待证）。
      本脚本 n_max_pred 列 = **未校准估计，不得用于容量准入**；正确口径：先统一单位，
      再按逐队列 II_q·R_q+2 计算（待实现，实现前本列仅作求解器 sanity 参考）。
    - 容量墙：IQ 深度 128 行/stage（smi_top.v:22，ADR-057 实证口径）。

输出：artifacts/a2b_preflight_20261006/preflight.md + 控制台表。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path("/home/juno1023/aiacc_compiler")
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "src"))

from api.hw_spec import HardwareSpec                       # noqa: E402
from verification_v2.ops.configurable import ConfigurableOperator  # noqa: E402
from synthesis.ucode_solver import UCodeSynthesizer        # noqa: E402
from frontend.rtovr_bypass import apply_rtovr_sparse_bypass  # noqa: E402

IQ_DEPTH = 128
HDR_OVERHEAD = 2          # INIT + TERM（平坦/展开流的头尾开销，近似）

# 候选 = census 已解出 II>2 的 SAT 算子 + scale_sum（校准点，II=2 实测 n_max=240）
CANDIDATES = ["leaky_relu", "relu6", "relu_offset", "horner_v3", "sigmoid_taylor",
              "silu_taylor", "sinh5", "div", "log", "rsqrt", "sqrt", "tan7", "tanh7",
              "exp5", "expm1", "hardsigmoid", "log1p", "mish", "reciprocal", "silu_approx"]
CALIBRATION = ["scale_sum"]
CALIBRATION_MEASURED = {"scale_sum": 240}   # ADR-056 步⑤实测


def analyze(spec, syn, name: str) -> dict:
    yp = ROOT / "verification_v2" / "ops" / f"{name}.yaml"
    op = ConfigurableOperator(str(yp))
    ops = op.get_dfg(spec)
    ops = apply_rtovr_sparse_bypass(ops, spec)
    res = syn.solve_with_units(ops, MII=1, MAX_II=64)
    if res is None:
        return {"op": name, "II": None}
    ii = res["II"]
    per_q: dict[str, int] = {}
    for o in ops:
        q = res.get("unit_assignments", {}).get(o.trace_id)
        if q:
            per_q[q] = per_q.get(q, 0) + 1
    # 容量预测：束缚队列 = 每迭代 op 数最多者
    if per_q:
        bind_q = max(per_q, key=per_q.get)
        n_max = (IQ_DEPTH - HDR_OVERHEAD) // per_q[bind_q]
    else:
        bind_q, n_max = "-", 0
    return {"op": name, "II": ii, "bind_queue": bind_q,
            "ops_per_iter": per_q.get(bind_q, 0), "n_max_pred": n_max,
            "queues": sorted(per_q)}


def main() -> int:
    spec = HardwareSpec.load(str(ROOT / "hw_config"))
    syn = UCodeSynthesizer(spec)
    rows = []
    for name in CALIBRATION + CANDIDATES:
        try:
            r = analyze(spec, syn, name)
        except Exception as e:
            r = {"op": name, "II": None, "err": f"{type(e).__name__}: {str(e).splitlines()[0][:60]}"}
        rows.append(r)
        print(r, flush=True)

    out_dir = ROOT / "verification_v2" / "artifacts" / "a2b_preflight_20261006"
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "preflight.md"
    with md.open("w", encoding="utf-8") as f:
        f.write("# A2b 预检（II>2 候选公式：II / 槽结构 / 容量预测）\n\n")
        f.write("> 静态 DFG 预测，未跑准入链；IQ 墙 128 行。\n")
        f.write("> **n_max 列 = 未校准估计，不得用于容量准入**（用户 2026-10-06 裁定：scale_sum\n")
        f.write("> 实测 240 vs 预测 63，一次偏保守不证明恒保守；疑混用元素数/逻辑迭代数单位，\n")
        f.write("> 正确口径 = 统一单位后按逐队列 II_q·R_q+2 计算，待实现）。\n\n")
        f.write("| op | II | 束缚队列 | ops/iter | n_max 未校准估计 | 队列数 |\n|---|---|---|---|---|---|\n")
        for r in rows:
            if r.get("II") is None:
                f.write(f"| {r['op']} | UNSAT/ERR | — | — | — | — |\n")
                continue
            tag = ""
            if r["op"] in CALIBRATION_MEASURED:
                meas = CALIBRATION_MEASURED[r["op"]]
                tag = f"（校准冲突：实测 {meas} / 估计 {r['n_max_pred']} → 单位疑混，未校准）"
            f.write(f"| {r['op']} | {r['II']} | {r['bind_queue']} | {r['ops_per_iter']} "
                    f"| {r['n_max_pred']}{tag} | {len(r['queues'])} |\n")
    print(f"\nwrote {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
