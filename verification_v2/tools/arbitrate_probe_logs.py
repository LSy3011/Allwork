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
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from verification_v2.infra.rtl_probe_plan import (  # noqa: E402
    STUB_IDS, monitored_ids, rtl_id_for_queue, ProbePlanError)

FETCH_SCHEMA = "adr056_d4_expected_events_v1"
# ADR-057 D4：分段程序的期望事件 schema（带段身份块；段内事件仍为队列局部 rel 拍）
STAGE_SCHEMA = "adr057_d4_expected_events_v1"


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
    """单段仲裁（ADR-056 步④-2 原始入口，行为保持不变）。"""
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

    report, _aux = _arbitrate_core(fetch_evs, issue_evs, write_rows, expected)
    report["case_root"] = str(case_root)
    return report


def _arbitrate_core(fetch_evs: Dict[int, List[Tuple[int, int, Any]]],
                    issue_evs: Dict[int, List[Tuple[int, Any]]],
                    write_rows: List[Tuple[int, int, int, int, int, str]],
                    expected: Dict[str, Any],
                    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """事件级仲裁核心（G1–G5）。输入为已装载（多段时：已按窗过滤）的事件。

    返回 (report, aux)：report 即单段报告体（键集不变）；aux 携带结构化中间量
    （exp_pairs / smc0_seen）供多段封装的段间裁决（C3）复用，不进 JSON。
    """
    bad: List[str] = []
    probes = set(monitored_ids())
    stub = set(STUB_IDS)

    # ---- (G1) 全局监测：覆盖集外/并集外 ----
    union_qs = list(expected.get("union_queues", []))
    try:
        union_ids = {rtl_id_for_queue(q) for q in union_qs}
    except ProbePlanError as exc:
        return ({"violations": [f"并集映射断裂: {exc}"], "verdict": "RED"},
                {"exp_pairs": Counter(), "smc0_seen": set()})
    q_by_id: Dict[int, str] = {}  # iq -> 队列名（留给 (G1) 之后排错信息用）
    for q in union_qs:
        iq_q = rtl_id_for_queue(q)
        q_by_id[iq_q] = q
        if iq_q not in probes:
            return ({"violations": [f"{q}: 映射到未铺针/插槽（probe plan 断裂）"],
                     "verdict": "RED"},
                    {"exp_pairs": Counter(), "smc0_seen": set()})

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
    aux = {"exp_pairs": exp_pairs, "smc0_seen": smc0_seen}
    return report, aux


# ----------------------------------------------------------------------
# ADR-057 D4：多段仲裁——按 TB 标记切窗，逐段复用单段核心，段间 C1/C2/C3 正式化
# ----------------------------------------------------------------------

# TB 标记文法（tb_aiacc_top_v04_f_multi_stage.v :2386/:2393/:959 的 $display 原文；
# BEGIN 行第二数是 tb_stage_count-1 = 最大段号）。
_MARK_BEGIN = re.compile(
    r"\[TB_MULTI_STAGE\] ===== stage_(\d+)/(\d+) BEGIN ===== "
    r"@time (\d+) @probe_cycle (\d+)")
_MARK_DONE = re.compile(
    r"\[TB_MULTI_STAGE\] stage_(\d+) done \(intr cleared\) "
    r"@time (\d+) @probe_cycle (\d+)")
_MARK_COMPLETE = re.compile(
    r"\[TB_MULTI_STAGE\] all stages complete.*?@probe_cycle (\d+)")


def parse_stage_markers(sim_log_path: Path) -> Dict[str, Any]:
    """从 VCS sim.log 解析段标记 → {begins: {sid: cyc}, acks: {sid: cyc},
    complete: cyc|None, max_idx: int|None}。重复标记即抛 ArbiterError（TB 断裂）。"""
    begins: Dict[int, int] = {}
    acks: Dict[int, int] = {}
    complete: Optional[int] = None
    max_idx: Optional[int] = None
    text = Path(sim_log_path).read_text(errors="replace")
    for ln in text.splitlines():
        m = _MARK_BEGIN.search(ln)
        if m:
            sid, mx, _t, cyc = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
            if sid in begins:
                raise ArbiterError(f"stage_{sid} BEGIN 标记重复（TB 断裂）: {ln.strip()}")
            begins[sid] = cyc
            max_idx = mx if max_idx is None else max_idx
            if mx != max_idx:
                raise ArbiterError(f"BEGIN 标记总数字段不一致: {ln.strip()}")
            continue
        m = _MARK_DONE.search(ln)
        if m:
            sid, cyc = int(m.group(1)), int(m.group(3))
            if sid in acks:
                raise ArbiterError(f"stage_{sid} done 标记重复（TB 断裂）: {ln.strip()}")
            acks[sid] = cyc
            continue
        m = _MARK_COMPLETE.search(ln)
        if m:
            complete = int(m.group(1))
    return {"begins": begins, "acks": acks, "complete": complete,
            "max_idx": max_idx}


def _window_filter(fetch_evs, issue_evs, write_rows,
                   lo: int, hi: Optional[int]):
    """半开窗 [lo, hi)（hi=None → ∞）过滤三类事件。"""
    def _in(c: int) -> bool:
        return lo <= c and (hi is None or c < hi)
    f = {iq: [e for e in evs if _in(e[0])] for iq, evs in fetch_evs.items()}
    f = {iq: evs for iq, evs in f.items() if evs}
    i = {iq: [e for e in evs if _in(e[0])] for iq, evs in issue_evs.items()}
    i = {iq: evs for iq, evs in i.items() if evs}
    w = [r for r in write_rows if _in(r[0])]
    return f, i, w


def arbitrate_multi(case_root: Path, expected_stages: List[Dict[str, Any]],
                    sim_log_path: Path) -> Dict[str, Any]:
    """ADR-057 D4 多段仲裁。

    段间裁决（在公共 iq_probe_cycle 轴上，以 TB 标记为唯一段界来源）：
      C1 次序：BEGIN_0 < … < BEGIN_{K-1}；段 k 的完成应答 ack_k ∈ (BEGIN_k,
         BEGIN_{k+1}]；段 k 全部写回均早于 ack_k（写回未完成即应答 = 契约破坏）。
      C2 无残留写：段间静默窗 [ack_k, BEGIN_{k+1}) 内**零写回**；末段 ack 之后
         同样零写回。**应答≡BEGIN 同拍（空窗）不称排干证明**——空窗记入
         `limitations`（ADR-057 复核注记 + errata-2026-10-01：现役 TB 段间
         零延迟顺序推进，tb_aiacc_top_v04_f_multi_stage.v:2382-2394 同一
         $time 打印 done_k 与 BEGIN_{k+1}，空窗是 testbench 构造性产物而非
         硬件时序事实；残留写之排除改由 C1 次序 + C3 期望写集不相交前提下的
         全时线写归因正向支撑，不依赖该窗）。空窗不单独致 RED，但也绝不
         据此声称独立排干证明。
         （gap 内的 FETCH/ISSUE 残尾计为观察量：G5 残尾语义的上界未冻结，
         侦察未取证，不为它发明硬闸。）
      C3 地址接续：各段期望写集（段内期望文件 payload 的 (BANK,ADDR)）两两
         不相交（相交 = 段间归属不可判定，超 ADR-057 当前范围 → RED）；
         各段 SMC0 观察写集 == 本段期望写集（段内 G4 已隐含，此处显式复核）；
         全体段期望写集按段号链式连续（max_k + 1 == min_{k+1}，同 bank）。

    已知盲点（evaluator N3 披露，继承自 ADR-056 集合语义，非 ADR-057 引入）：
      G4/C3 均为集合语义——段窗内、应答前、对本段期望地址的**重复写**不做
      多重性仲裁（exp_pairs 的多重数记入报告但从不断言）。部分后挡：G3 发射
      计数抓真重复发射；golden 终值比对抓数据不同的覆盖写。「单 ISSUE 双写
      strobe 且数据相同」在仲裁层与数值层均不可见。是否收口（段窗内核对
      观察写多重数 == 期望多重数，数据已在手）留待专项决定，不在本 ADR 范围。
    """
    bad: List[str] = []
    limits: List[str] = []   # 限制声明：在场即记录、不单独致 RED（ADR-057 errata-2026-10-01）
    K = len(expected_stages)

    # ---- 期望侧自洽：schema + 段身份块 + 区间链式覆盖 ----
    stages_meta: List[Dict[str, Any]] = []
    for k, exp in enumerate(expected_stages):
        if exp.get("schema") != STAGE_SCHEMA:
            bad.append(f"[stage_?] 期望 schema={exp.get('schema')!r} != {STAGE_SCHEMA!r}")
            continue
        st = exp.get("stage") or {}
        if st.get("stage_id") != k:
            bad.append(f"[stage_{k}] 期望段号乱序/缺失: stage.stage_id={st.get('stage_id')!r}")
        lo_hi = st.get("iter_range")
        if not (isinstance(lo_hi, list) and len(lo_hi) == 2
                and lo_hi[0] == st.get("stage_start")
                and lo_hi[1] == st.get("stage_start", 0) + st.get("m", -1)):
            bad.append(f"[stage_{k}] 段身份块 iter_range={lo_hi} 与 "
                       f"stage_start/m={st.get('stage_start')}/{st.get('m')} 不符")
        stages_meta.append(st)
    if stages_meta and stages_meta[0].get("stage_start") != 0:
        bad.append("首段 stage_start != 0——覆盖链不从 0 起")
    for k in range(len(stages_meta) - 1):
        hi_k = stages_meta[k].get("stage_start", 0) + stages_meta[k].get("m", 0)
        if stages_meta[k + 1].get("stage_start") != hi_k:
            bad.append(f"段间覆盖断裂: stage_{k} end={hi_k} != "
                       f"stage_{k+1} start={stages_meta[k + 1].get('stage_start')}")
    if bad:
        return {"mode": "multi_stage", "violations": bad, "limitations": [],
            "verdict": "RED"}

    # ---- 日志装载（三探针 + sim.log 标记） ----
    fetch_path = case_root / "iq_probe_trace.log"
    issue_path = case_root / "iq_issue_trace.log"
    write_path = case_root / "ur_write_trace.log"
    for p in (fetch_path, issue_path, write_path, sim_log_path):
        if not Path(p).is_file():
            bad.append(f"日志缺失: {p}（TB 探针/标记断裂，不得静默降级）")
    if bad:
        return {"mode": "multi_stage", "violations": bad, "limitations": [],
            "verdict": "RED"}
    fetch_evs = load_fetch_log(fetch_path)
    issue_evs = load_issue_log(issue_path)
    write_rows = load_write_log(write_path)
    marks = parse_stage_markers(sim_log_path)
    begins, acks = marks["begins"], marks["acks"]
    if sorted(begins) != list(range(K)) or sorted(acks) != list(range(K)):
        bad.append(f"TB 段标记不齐: begins={sorted(begins)} acks={sorted(acks)} "
                   f"!= 期望段集 0..{K - 1}")
        return {"mode": "multi_stage", "violations": bad, "limitations": [],
            "verdict": "RED"}
    if marks["max_idx"] is not None and marks["max_idx"] != K - 1:
        bad.append(f"TB 侧段数上界 {marks['max_idx']} != 期望段数-1 {K - 1}")
    if marks["complete"] is None:
        bad.append("缺 'all stages complete' 标记——TB 未走完段序列")
    for k in range(K - 1):
        if begins[k + 1] <= begins[k]:
            bad.append(f"(C1) BEGIN 乱序: stage_{k}@{begins[k]} 不早于 "
                       f"stage_{k + 1}@{begins[k + 1]}")

    # ---- 逐段窗口 + 复用单段核心（c0 逐段重锚） ----
    per_stage_reports: List[Dict[str, Any]] = []
    per_stage_aux: List[Dict[str, Any]] = []
    for k in range(K):
        lo = begins[k]
        hi = begins[k + 1] if k + 1 < K else None
        f_w, i_w, w_w = _window_filter(fetch_evs, issue_evs, write_rows, lo, hi)
        rep_k, aux_k = _arbitrate_core(f_w, i_w, w_w, expected_stages[k])
        rep_k["stage"] = dict(stages_meta[k])
        rep_k["window"] = {"begin": lo, "end": hi}
        rep_k["violations"] = [f"[stage_{k}] {v}" for v in rep_k["violations"]]
        per_stage_reports.append(rep_k)
        per_stage_aux.append(aux_k)
        bad.extend(rep_k["violations"])

    # ---- C1 次序/应答窗 + C2 静默窗 + C3 地址接续 ----
    c1: Dict[str, Any] = {"per_stage": []}
    for k in range(K):
        ack_k = acks[k]
        hi = begins[k + 1] if k + 1 < K else None
        in_window = ack_k > begins[k] and (hi is None or ack_k <= hi)
        w_cycles = [c for (c, _s, _b, _a, _we, _wd)
                    in _window_filter(fetch_evs, issue_evs, write_rows,
                                      begins[k], hi)[2]]
        last_w = max(w_cycles, default=None)
        if not in_window:
            bad.append(f"(C1) stage_{k} 应答 @{ack_k} 不在窗口 ({begins[k]}, "
                       f"{hi if hi is not None else '∞'}] 内")
        if last_w is not None and last_w >= ack_k:
            bad.append(f"(C1) stage_{k} 末笔写 @{last_w} 不早于应答 @{ack_k}"
                       f"——写回未完成即应答")
        c1["per_stage"].append({"stage_id": k, "begin": begins[k], "ack": ack_k,
                                "last_write": last_w, "order_ok": in_window})
    c2: Dict[str, Any] = {"gaps": []}
    pre = [r for r in write_rows if r[0] < begins[0]]
    if pre:
        bad.append(f"(C2) 首段 BEGIN 之前存在写回 ×{len(pre)}（窗口外残留）")
    for k in range(K):
        ack_k = acks[k]
        hi = begins[k + 1] if k + 1 < K else None
        resid = [r for r in write_rows if ack_k <= r[0] and (hi is None or r[0] < hi)]
        gap = {"stage_id": k, "ack": ack_k, "next_begin": hi,
               "residual_writes": len(resid)}
        if hi is not None and ack_k == hi:
            gap["vacuous_window"] = True
            limits.append(
                f"(C2) stage_{k} 应答与 stage_{k + 1} BEGIN 同拍 @{ack_k}"
                f"——空窗不称排干证明（ADR-057 复核注记/errata-2026-10-01："
                f"现役 TB 段间零延迟顺序推进，同一 $time 打印两标记，空窗系"
                f"testbench 构造性产物；残留写排除由 C1 次序 + C3 写集不相交"
                f"前提下的全时线写归因正向支撑，不依赖本窗）")
        if resid:
            bad.append(f"(C2) stage_{k} 应答后静默窗内残留写 ×{len(resid)}"
                       f"（首笔 cycle={resid[0][0]} bank={resid[0][2]} addr={resid[0][3]}）")
        c2["gaps"].append(gap)

    c3: Dict[str, Any] = {"per_stage_pairs": []}
    exp_sets = [set(aux["exp_pairs"]) for aux in per_stage_aux]
    for k in range(K):
        obs_k = {(b, a) for (b, a) in per_stage_aux[k]["smc0_seen"]}
        eq = obs_k == exp_sets[k]
        c3["per_stage_pairs"].append({
            "stage_id": k, "expected": len(exp_sets[k]), "observed": len(obs_k),
            "set_equal": eq})
        if not eq:
            bad.append(f"(C3) stage_{k} 观察写集({len(obs_k)}) != 期望写集"
                       f"({len(exp_sets[k])})——对称差 {len(obs_k ^ exp_sets[k])} 对")
    for k in range(K - 1):
        inter = exp_sets[k] & exp_sets[k + 1]
        if inter:
            bad.append(f"(C3) stage_{k} 与 stage_{k + 1} 期望写集相交 ×{len(inter)}"
                       f"——段间归属不可判定（超 ADR-057 范围）")
        bk = {b for (b, _a) in exp_sets[k]}
        bk1 = {b for (b, _a) in exp_sets[k + 1]}
        if len(bk) == 1 and bk == bk1:
            b0 = next(iter(bk))
            a_max = max(a for (b, a) in exp_sets[k] if b == b0)
            a_min = min(a for (b, a) in exp_sets[k + 1] if b == b0)
            if a_min != a_max + 1:
                bad.append(f"(C3) 地址接续断裂: bank{b0} stage_{k} max={a_max}，"
                           f"stage_{k + 1} min={a_min}（期望 {a_max + 1}）")
        elif bk or bk1:
            bad.append(f"(C3) stage_{k}/stage_{k + 1} 写集跨 bank {sorted(bk | bk1)}"
                       f"——连续性判据不适用（超 ADR-057 范围）")

    inter_stage = {"C1_stage_order": c1, "C2_no_residual_write": c2,
                   "C3_addr_continuity": c3}
    return {
        "mode": "multi_stage",
        "case_root": str(case_root),
        "stage_count": K,
        "markers": {"begins": begins, "acks": acks,
                    "complete": marks["complete"], "max_idx": marks["max_idx"]},
        "stages": per_stage_reports,
        "inter_stage": inter_stage,
        "violations": bad,
        "limitations": limits,
        "verdict": "RED" if bad else "GREEN",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ADR-056 步④-2 / ADR-057 D4 三日志 × 期望事件全局仲裁")
    ap.add_argument("--case-root", type=Path, required=True,
                    help="case 根（含 iq_probe_trace/iq_issue_trace/ur_write_trace 三日志）")
    ap.add_argument("--expected", type=Path, default=None,
                    help="单段：adr056_d4_expected_events_v1（仿真前冻结）")
    ap.add_argument("--expected-stage", type=Path, action="append", default=None,
                    help="多段（ADR-057 D4）：逐段 adr057_d4_expected_events_v1，"
                         "按段号顺序重复传入")
    ap.add_argument("--sim-log", type=Path, default=None,
                    help="多段必给：VCS sim.log（TB_MULTI_STAGE 标记来源，段界唯一依据）")
    ap.add_argument("--report", type=Path, default=None, help="JSON 报告落盘路径")
    args = ap.parse_args()

    if args.expected_stage:
        if not args.sim_log:
            ap.error("多段模式须给 --sim-log（段界标记来源）")
        expected_stages = [json.loads(p.read_text(encoding="utf-8"))
                           for p in args.expected_stage]
        report = arbitrate_multi(args.case_root, expected_stages, args.sim_log)
    elif args.expected:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        report = arbitrate(args.case_root, expected)
    else:
        ap.error("须给 --expected（单段）或 --expected-stage ×K（多段）")
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if report["verdict"] == "RED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
