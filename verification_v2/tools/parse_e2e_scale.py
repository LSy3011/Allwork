#!/usr/bin/env python3
"""ADR-055 A1 三规模 e2e 归档解析器：从**展开形**产物实测逐队列行数（json + hex 双方）。

与 `artifacts/preflight_add_20260924/parse_preflight.py` 的区别（口径必须分清）：
  * 预检解析器读的是 **embed 主线**产物（INIT + **HDR** + II·BODY，无 TERM），
    R_q 的载体是 HDR 行的 LOOPS；用途是「构造预测」。
  * 本解析器读的是 **A1 展开形**产物（INIT + **II·R_q 个 BODY** + 恰 1 TERM，无 HDR），
    R_q 由 `compiler_schedule.json`（solver 侧独立来源）反算：
        R_q = (t_max // II + N_eff) − (t_min // II)
    用途是「**实测**行数」——ADR-055 §3 表的 `hex 实际行数` 列即由本脚本产出。

用法：
    python parse_e2e_scale.py <build_dir> --num-iterations 2 [--label n8]
产物依赖：`compiler_schedule.json`（扩展后含 stride/logical_addr）、
          `physical_instructions.json`、`smc_0/iq_*.hex`、`branch_report.json`。
"""
from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path


def parse(build_dir: Path, num_iterations: int, label: str = "") -> int:
    sch = json.loads((build_dir / "compiler_schedule.json").read_text())
    ii = int(sch["II"])
    t_by_q: dict = {}
    for tid, info in sch["ops"].items():
        t_by_q.setdefault(str(info["iq"]).lower().replace("_", "-"), []).append(int(info["t"]))

    rows = json.loads((build_dir / "physical_instructions.json").read_text())
    queues: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        queues.setdefault(r["iq_name"], []).append(r)

    hex_dir = build_dir / "smc_0"
    hex_rows = {}
    for p in sorted(hex_dir.glob("iq_*.hex")):
        hex_rows[p.stem[3:]] = len([l for l in p.read_text().splitlines() if l.strip()])

    br = {}
    brp = build_dir / "branch_report.json"
    if brp.exists():
        br = json.loads(brp.read_text()).get("branches", {})

    print(f"# {label or build_dir.name}  build={build_dir}  II={ii}  N_eff={num_iterations}  "
          f"queues={len(queues)}  total_json_rows={len(rows)}")
    hdr = (f"{'iq':<12}{'branch':>8}{'json':>6}{'hex':>5}{'II':>4}{'vld':>5}{'bub':>5}"
           f"{'R_q':>5}{'L_q_pred':>9}{'INIT':>5}{'TERM':>5}{'ok':>4}")
    print(hdr)
    worst = ("", 0)
    n_ok = 0
    for q, rs in queues.items():
        body = [r for r in rs if r["stage"] == "BODY"]
        vld = sum(1 for r in body if int(r["header"]["VLD"]) == 1)
        bub = len(body) - vld
        ts = t_by_q.get(q, [])
        rq = (max(ts) // ii + num_iterations) - (min(ts) // ii) if ts else None
        lq = (ii * rq + 2) if rq is not None else None
        n_hex = hex_rows.get(q, -1)
        has_init = any(r["stage"] == "INIT" for r in rs)
        n_term = sum(1 for r in rs if r["stage"] == "TERM")
        # ⚠ len(body) 是 II·R_q（全槽序列），iq_II 本体来自 schedule 的 II——
        #   初版误以 len(body) 当 iq_II 会把 L_q_pred 算成 II·R_q²+2。
        ok = (lq is not None and len(rs) == lq and n_hex == lq and n_term == 1
              and has_init and rq is not None and len(body) == ii * rq
              and not any(r["stage"] == "HDR" for r in rs))
        n_ok += int(ok)
        if len(rs) > worst[1]:
            worst = (q, len(rs))
        print(f"{q:<12}{br.get(q, '-'):>8}{len(rs):>6}{n_hex:>5}{ii:>4}{vld:>5}{bub:>5}"
              f"{rq if rq is not None else '-':>5}{lq if lq is not None else '-':>9}"
              f"{str(has_init)[:1]:>5}{n_term:>5}{'Y' if ok else 'N':>4}")
    n_bad = len(queues) - n_ok
    print(f"# 全队列 unroll: {all(v == 'unroll' for v in br.values()) if br else '无 branch_report'}"
          f"  三方一致: {n_ok}/{len(queues)}  最大队列: {worst[0]}={worst[1]} 行  "
          f">128 超容: {'有' if worst[1] > 128 else '无'}")
    return 0 if n_bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("build_dir")
    ap.add_argument("--num-iterations", type=int, required=True)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    return parse(Path(a.build_dir), a.num_iterations, a.label)


if __name__ == "__main__":
    raise SystemExit(main())
