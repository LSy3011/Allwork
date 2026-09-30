#!/usr/bin/env python3
"""ADR-056 步④-2 全局仲裁器：三类探针日志 × 仿真前期望事件的逐拍裁决。

输入（全部**仿真前已冻结**的产物与日志，仲裁器自身不推导任何常数）：
  * --expected   adr056_d4_expected_events_v1（emit_expected_fire_sequence.py
                 --emit-events；调度 + 注册表延迟模型在仿真前生成，注册表
                 records_sha256 内嵌）。
  * --case-root  TB 探针三日志：iq_probe_trace.log（FETCH，六段格式）、
                 iq_issue_trace.log（ISSUE）、ur_write_trace.log（写回）。

裁决集（注册表为准，逐一对应）：
  (G1) GLOBAL_MONITOR_RULE：FETCH 行覆盖集外的 iq（映射断裂）→ RED；
       **并集外队列任何取指脉冲 → RED**。ISSUE 层并集外活动同样 RED。
  (G2) PROJ_FETCH_UNROLL：每 union 队列 FETCH 事件与期望**逐序列相等**
       （abs 拍 = 本队列 c0 + rel；c0 = 该队列首个 fetch 的启动锚）。
       漏发（少了）、错位（相位移）、多发（G5 残尾容忍之外）→ RED。
       另有 e2 相承：collapsed 序列地址逐拍 +1（ADR-055 D4(b-2) 既有断言）。
       **不允许逐队列独立平移后整体判绿**——每队列只许各自锚定一次 c0，
       之后的全部相位在公共 iq_probe_cycle 基准下逐拍相等。
  (G3) PROJ_ISSUE_UNROLL（注册表 v2）：每 union 队列 ISSUE 事件与期望逐序列相等
       （INIT 回放 l_init+1 拍——**每 valid 拍计一次，不得按值折叠**，
       回放拍指令字逐拍全同；每展开行 1 拍；TERM 1 拍——v2 勘误：经 embed-0
       路径产 IQ 内部发射拍，smi_iq_iic.v:662→:671→:693）。本断言仅
       适用 unroll 形态——flat 形 ISSUE 为注册表明文实证缺口，期望文件
       由 emit 工具保证只含 unroll；若未来消费 flat，本工具拒绝裁决。
  (G4) 实际写回：ur_write_trace.log 每条提交的 (bank, addr) 必须 ∈ 期望
       写集（union 队列中 ur-w* 队列全部 live FETCH 行的 BANK/ADDR 对，来自
       期望文件 payload）——**非法写检测**；期望写集非空时，SMC0 上必须
       覆盖全部期望对——**漏写检测**。写回计数精确模型未冻结：不写常量、
       不拟合 trace，仅报告 observed vs expected 计数差供人工看。
  (G5) PROBES_TAIL_SEMANTICS：TERM 之后的残尾取指（仅并集内队列）计数
       入报告，不计违例；TERM 之前任何错序/多发即 (G2) RED。

跨队列 c0 相等为**未冻结经验命题**（注册表 v1 未含）：仅入报告
（cross_queue.c0_spread），不设硬断言。

数值一律比整数（验收铁律）。任一 RED → exit 1。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from verification_v2.infra.rtl_probe_plan import (  # noqa: E402
    STUB_IDS, monitored_ids, rtl_id_for_queue, ProbePlanError)

FETCH_SCHEMA = "adr056_d4_expected_events_v1"


class ArbiterError(ValueError):
    """输入产物自身断裂（期望文件 schema / 探针日志缺失）——直接 RED。"""


def _int_or_x(tok: str, base: int):
    lo = tok.lower()
    if "x" in lo or "z" in lo:
        return None
    return int(tok, base)


def _collapse_runs(items: List[Tuple[int, Any]]) -> List[Tuple[int, Any]]:
    """相邻周期同值 = 同一次请求的反压多采样 → 取首拍（与 check_iq_probe_trace 同语义）。"""
    out: List[Tuple[int, Any]] = []
    last_cyc = None
    last_val = None
    for cyc, val in items:
        if last_cyc is not None and cyc == last_cyc + 1 and val == last_val:
            last_cyc = cyc
            continue
        out.append((cyc, val))
        last_cyc, last_val = cyc, val
    return out


def load_fetch_log(path: Path) -> Dict[int, List[Tuple[int, int, Any]]]:
    """iq -> [(cycle, addr_valid, addr)]；只关心取指脉冲（GLOBAL_MONITOR 语义）。"""
    evs: Dict[int, List[Tuple[int, int, Any]]] = {}
    for ln, raw in enumerate(path.read_text().splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 6:
            raise ArbiterError(f"{path.name}@{ln} 行格式坏: {raw!r}")
        cyc, iq, av = int(parts[0]), int(parts[1]), int(parts[2])
        addr = _int_or_x(parts[3], 10)
        evs.setdefault(iq, []).append((cyc, av, addr))
    return evs


def load_issue_log(path: Path) -> Dict[int, List[Tuple[int, Any]]]:
    evs: Dict[int, List[Tuple[int, Any]]] = {}
    for ln, raw in enumerate(path.read_text().splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 4:
            raise ArbiterError(f"{path.name}@{ln} 行格式坏: {raw!r}")
        cyc, iq, dv = int(parts[0]), int(parts[1]), int(parts[2])
        dly = _int_or_x(parts[3], 16)
        if dv == 1:
            evs.setdefault(iq, []).append((cyc, dly))
    return evs


def load_write_log(path: Path) -> List[Tuple[int, int, int, int, int, str]]:
    """[(cycle, smc, bank, waddr, wbe, wdata_hex)]。"""
    rows = []
    for ln, raw in enumerate(path.read_text().splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 7:
            raise ArbiterError(f"{path.name}@{ln} 行格式坏: {raw!r}")
        rows.append((int(parts[0]), int(parts[1]), int(parts[2]),
                     int(parts[4]), int(parts[5], 16), parts[6]))
    return rows


def arbitrate(case_root: Path, expected: Dict[str, Any]) -> Dict[str, Any]:
    bad: List[str] = []
    if expected.get("schema") != FETCH_SCHEMA:
        bad.append(f"期望文件 schema={expected.get('schema')!r} != {FETCH_SCHEMA!r}")
        return {"violations": bad, "verdict": "RED"}

    fetch_path = case_root / "iq_probe_trace.log"
    issue_path = case_root / "iq_issue_trace.log"
    write_path = case_root / "ur_write_trace.log"
    for p in (fetch_path, issue_path, write_path):
        if not p.is_file():
            bad.append(f"探针日志缺失: {p}（TB 探针断裂，不得静默降级）")
    if bad:
        return {"violations": bad, "verdict": "RED"}

    fetch_evs = load_fetch_log(fetch_path)
    issue_evs = load_issue_log(issue_path)
    write_rows = load_write_log(write_path)

    probes = set(monitored_ids())
    stub = set(STUB_IDS)

    # ---- (G1) 全局监测：覆盖集外/并集外 ----
    union_qs = list(expected.get("union_queues", []))
    try:
        union_ids = {rtl_id_for_queue(q) for q in union_qs}
    except ProbePlanError as exc:
        return {"violations": [f"并集映射断裂: {exc}"], "verdict": "RED"}
    q_by_id: Dict[int, str] = {}  # iq -> 队列名（留给 (G1) 之后排错信息用）
    for q in union_qs:
        iq_q = rtl_id_for_queue(q)
        q_by_id[iq_q] = q
        if iq_q not in probes:
            return {"violations": [f"{q}: 映射到未铺针/插槽（probe plan 断裂）"],
                    "verdict": "RED"}

    for iq, evs in sorted(fetch_evs.items()):
        if iq not in probes:
            bad.append((f"(G1) FETCH 行落在探针覆盖集外 iq{iq}：映射/TB 断裂"
                        f"（{'stub 槽 ' + str(iq) + ' 不应有 smi_iq' if iq in stub else '未知槽'}）"))
        addr_runs = _collapse_runs([(c, a) for (c, av, a) in evs if av == 1])
        if iq not in union_ids and addr_runs:
            bad.append(f"(G1) 并集外队列取指脉冲 ×{len(addr_runs)} @iq{iq}"
                       f"（首拍 cycle={addr_runs[0][0]}）——GLOBAL_MONITOR_RULE RED")
    for iq, evs in sorted(issue_evs.items()):
        if iq not in probes:
            bad.append(f"(G1) ISSUE 行落在探针覆盖集外 iq{iq}")
        if iq not in union_ids and evs:
            bad.append(f"(G1) 并集外队列 ISSUE 活动 ×{len(evs)} @iq{iq}（无取指不该有发射）")

    # ---- (G2)/(G3) 每 union 队列 FETCH & ISSUE 逐序列裁决 ----
    queues = expected.get("queues", {})
    if sorted(queues) != sorted(union_qs):
        bad.append("期望文件 queues 键集 != union_queues（期望本体断裂）")
    per_q: Dict[str, Any] = {}
    for q in union_qs:
        qd = queues.get(q) or {}
        iq = rtl_id_for_queue(q)   # 映射有效性已在 (G1) 段闸住（断裂即提前 RED）
        exp_fetch = sorted(int(e["cycle"]) for e in qd.get("events", [])
                           if e["kind"] == "FETCH")
        exp_issue = sorted(int(e["cycle"]) for e in qd.get("events", [])
                           if e["kind"] == "ISSUE")
        if not exp_fetch:
            bad.append(f"{q}: 期望文件无 FETCH 事件（期望本体断裂）")
            continue

        raw = [(c, a) for (c, av, a) in fetch_evs.get(iq, []) if av == 1]
        runs = _collapse_runs(raw)
        n_x = sum(1 for (_c, a) in runs if a is None)
        if n_x:
            bad.append(f"{q}(IQ{iq}): fetch 含 X 地址 ×{n_x}（valid=1 而地址未知）")
        runs = [(c, a) for (c, a) in runs if a is not None]
        if not runs:
            bad.append(f"{q}(IQ{iq}): 无取指事件（探针未覆盖 / 队列未启动——漏发）")
            per_q[q] = {"iq": iq, "status": "RED-no-fetch"}
            continue
        c0 = runs[0][0]
        actual = [c for (c, _a) in runs]
        exp_abs = [c0 + r for r in exp_fetch]
        head = actual[:len(exp_abs)]
        entry: Dict[str, Any] = {"iq": iq, "c0": c0,
                                 "fetch_expected": len(exp_abs),
                                 "fetch_observed": len(actual),
                                 "tail_fetches": 0}
        if len(actual) < len(exp_abs):
            bad.append(f"{q}(IQ{iq}): (G2) 漏发：fetch {len(actual)}/{len(exp_abs)}")
        elif head != exp_abs:
            for k, (got, want) in enumerate(zip(head, exp_abs)):
                if got != want:
                    bad.append(f"{q}(IQ{iq}): (G2) FETCH 第{k}拍错拍："
                               f"cycle={got} != c0+rel={want}（rel={exp_fetch[k]}）")
                    break
        else:
            tail = actual[len(exp_abs):]
            entry["tail_fetches"] = len(tail)   # (G5) 残尾：计数不违例
        # e2 相承：collapsed 地址链逐拍 +1（INIT=base，不假定 0）
        addrs = [a for (_c, a) in runs]
        for k in range(1, min(len(exp_abs), len(addrs))):
            if addrs[k] != addrs[k - 1] + 1:
                bad.append(f"{q}(IQ{iq}): (G2/e2) 地址链断裂: 第{k}拍 addr={addrs[k]}"
                           f" != 前一拍+1={addrs[k - 1] + 1}")
                break

        # (G3) ISSUE 逐序列。**不按值折叠**：INIT 回放 l_init+1 拍的指令字逐拍全同，
        # 折叠会把回放压成 1 拍（真硬件假 RED）；PROJ_ISSUE_UNROLL 里每个 valid 拍
        # 都是一次独立发射事件（FETCH 层相反——同址多采样是反压，须折叠）。
        i_cycles = sorted({c for (c, _w) in issue_evs.get(iq, [])})
        i_exp_abs = [c0 + r for r in exp_issue]
        entry["issue_expected"] = len(exp_issue)
        entry["issue_observed"] = len(i_cycles)
        if i_cycles != i_exp_abs:
            if len(i_cycles) != len(i_exp_abs):
                why = "漏发" if len(i_cycles) < len(i_exp_abs) else "多发"
                bad.append(f"{q}(IQ{iq}): (G3) ISSUE 拍数 {len(i_cycles)}"
                           f" != 期望 {len(i_exp_abs)}（{why}）")
            else:
                for k, (got, want) in enumerate(zip(i_cycles, i_exp_abs)):
                    if got != want:
                        bad.append(f"{q}(IQ{iq}): (G3) ISSUE 第{k}拍错拍："
                                   f"cycle={got} != c0+rel={want}（rel={exp_issue[k]}）")
                        break
        per_q[q] = entry
        per_q[q]["status"] = "ok"

    # ---- (G4) 写回集合裁决 ----
    exp_pairs: Counter = Counter()
    for q in union_qs:
        if not q.startswith("ur-w"):
            continue
        for e in (queues.get(q) or {}).get("events", []):
            p = e.get("payload") or {}
            if e["kind"] == "FETCH" and p.get("vld") == 1 and "BANK" in p and "ADDR" in p:
                exp_pairs[(int(p["BANK"]), int(p["ADDR"]))] += 1
    illegal = [f"cycle={c} smc={s} bank={b} addr={a}"
               for (c, s, b, a, _wbe, _wd) in write_rows if (b, a) not in exp_pairs]
    for w in illegal[:20]:
        bad.append(f"(G4) 非法写: {w} ∉ 期望写集")
    if len(illegal) > 20:
        bad.append(f"(G4) 非法写合计 {len(illegal)} 条（仅列前 20）")
    smc0_seen = {(b, a) for (c, s, b, a, _wbe, _wd) in write_rows if s == 0}
    missing = sorted(set(exp_pairs) - smc0_seen)
    if exp_pairs and not write_rows:
        bad.append("(G4) 期望写集非空但 ur_write_trace.log 无写（漏写/探针断裂）")
    for pair in missing[:20]:
        bad.append(f"(G4) 漏写: SMC0 未见 bank={pair[0]} addr={pair[1]}")
    if len(missing) > 20:
        bad.append(f"(G4) 漏写合计 {len(missing)} 对（仅列前 20）")

    report = {
        "case_root": str(case_root),
        "registry_digest": (expected.get("registry") or {}).get("records_sha256"),
        "c0_by_queue": per_q,
        "cross_queue": {
            "c0_spread": (max((e["c0"] for e in per_q.values() if "c0" in e), default=None),
                          ),
            "note": ("跨队列 c0 相等是未冻结经验命题（注册表 v1 未含，步④-2 仅记录，"
                     "不设硬断言；禁止逐队列平移后宣布一致——以上裁决均以各自 c0 "
                     "仅锚定一次）"),
        },
        "writes": {
            "expected_pairs": {f"{b},{a}": n for (b, a), n in sorted(exp_pairs.items())},
            "observed_total": len(write_rows),
            "smc_seen": sorted({s for (_c, s, _b, _a, _we, _wd) in write_rows}),
            "wbe_histogram": dict(Counter(f"{we:#06x}" for (_c, _s, _b, _a, we, _wd)
                                          in write_rows)),
            "illegal_count": len(illegal),
            "missing_on_smc0": len(missing),
        },
        "violations": bad,
        "verdict": "RED" if bad else "GREEN",
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="ADR-056 步④-2 三日志 × 期望事件全局仲裁")
    ap.add_argument("--case-root", type=Path, required=True,
                    help="case 根（含 iq_probe_trace/iq_issue_trace/ur_write_trace 三日志）")
    ap.add_argument("--expected", type=Path, required=True,
                    help="adr056_d4_expected_events_v1（仿真前冻结）")
    ap.add_argument("--report", type=Path, default=None, help="JSON 报告落盘路径")
    args = ap.parse_args()

    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    report = arbitrate(args.case_root, expected)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if report["verdict"] == "RED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
