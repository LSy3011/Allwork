#!/usr/bin/env python3
"""ADR-055 D4(b-1) 独立事件检查器：展开形（unroll）发射契约的逐行对照。

对 A1 白名单算子的 e2e 产物目录，从 **solver 调度 + 硬件布局** 独立重建「本应发射什么」，
再与产物 `physical_instructions.json` 逐行比对（次数 / 顺序 / abs_t / 相位编码 / BANK / ADDR）。

独立性（hco 2026-09-24 提醒，ADR-055 D4 b-1）
--------------------------------------------
* **不 import assembler 任何内部函数 / 常量** —— 本文件只 import 布局规格读取器
  （`PhysicalMemoryLayout`，它读 `hw_config` 的地址布局，不是发射器逻辑）与 `HardwareSpec`。
* 地址刻意写成 **`(orig_offset_k + stride_k·(j − r_k)) mod bank_depth`**（「张量布局 + 梯形图」
  形式）；assembler 内部等价但写成 `A_k + stride_k·j`。**两边不同式**才抓得住 M1 类
  「两边同抄 `iq_max_stride`」的错误。
* 每队列的 `iq_II / l_init / R_q / 槽序` 全部由该队列自身的 t 集合独立重算——**不读**产物的
  INIT 行、**不读** assembler 的 `loops_val`。

契约（ADR-055 D2；§D.3：下列常数公式的**机器唯一来源是注册表**
`verification_v2/tools/protocol_registry_v2.yaml`（v2，2026-09-29 PROJ_ISSUE_UNROLL
勘误版；v1 文件保留未动），本文件一律经 `ProtocolRegistry.evaluate(...)` 求值，
**禁止在本文件重写公式**——注册表与 trace 不符走 PER_BEAT_PROTOCOL_MISMATCH 诊断，
不得回改常数拟合）
------------------------------------------------------------------------------------------
    skip    = t_min // iq_II               # 本队列 IQ 局部
    l_init  = INIT_PAD_FORMULA(skip, II)   # = skip·II + 1（注册表；+1 来源 ADR-055 M7）
    R_q     = (t_max // iq_II + N_eff) − skip
    L_q     = iq_II·R_q + 2                # INIT + iq_II·R_q 展开行 + TERM
    r_k     = t_k // iq_II − skip          # 该 op 的调度迭代偏移
    有效行(j,k): abs_t = l_init+1+k+j·iq_II, vld=1, level=0, loops=0, inc=0, dly=op_dly,
                 embed_end=0, inc_embed=0, trace_id=op.trace_id,
                 BANK/ADDR = resolve(logical_addr) / (orig_offset + stride·(j−r_k)) mod depth
    气泡行     : 同位 abs_t, vld=0 全零域, payload={}, trace_id=nop_{iq}_{j}_{k}
    TERM       : abs_t = TERM_PHASE_FORMULA(l_init, II, R_q)   # = l_init+1+II·R_q（注册表）

期望事件投影（ADR-056 §D.4，`--emit-events` 生成，仿真前落盘）
-------------------------------------------------------------
    FETCH: INIT 1 次(rel 0) + BODY(有效/气泡)各 1 次严格连续(rel=abs_t) + TERM 1 次；
           行数 = L_q（PROJ_FETCH_UNROLL）
    ISSUE: 每拍 = 对应执行拍 +1（ISSUE_PHASE_OFFSET，寄存器边界+posedge 采样）；
           INIT 回放 l_init+1 拍(cycle 1..l_init+1)；每展开行 1 拍(cycle=abs_t+1)；
           TERM 经 embed-0 路径产 1 拍（IQ 内部事件，≠算术操作）；总拍数 =
           l_init+1+II·R_q+1 = ISSUE_TOTAL_FORMULA（PROJ_ISSUE_UNROLL v2；flat 形态
           为实证缺口，见注册表 proven_boundary——本工具仅 unroll 分支）
    事件 cycle 为队列局部 rel_cycle；跨队列相位须以共同启动锚对齐（GLOBAL_MONITOR_RULE），
    消费侧（步④-2 探针裁决）不允许逐队列独立平移后宣布一致。

CLI
---
    python verification_v2/tools/emit_expected_fire_sequence.py \
        --build build/verification_v2/add --num-iterations 2
    # 附望事件生成（§D.3 期望先行：统一入口在打包/VCS 前调用）：
    ... --emit-events <out_dir>/expected_events.json
退出码 0 = 全绿；1 = 有违例（逐条打印；--emit-events 时重建违例则期望文件拒绝落盘）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from verification_v2.infra.protocol_registry import (  # noqa: E402
    ProtocolRegistry, load_default as _load_protocol_registry)

# 注册表模块级单例（derive_queue_rows/audit 缺省共用；测试可显式注入 reg 隔离）
_REG_SINGLETON: Optional[ProtocolRegistry] = None


def _registry(reg: Optional[ProtocolRegistry]) -> ProtocolRegistry:
    global _REG_SINGLETON
    if reg is not None:
        return reg
    if _REG_SINGLETON is None:
        _REG_SINGLETON = _load_protocol_registry()
    return _REG_SINGLETON


# 相位编码字段一律以 packer.py 的 ISA_V5_HEADER 键名为准（ADR-055 E7：
#   `packer.py` 无 EMBED 位；level 的 hex 侧键名是 EMBED_LOOPS，读 "EMBED" 恒真假绿）。
_ZERO_HDR = {"VLD": 0, "JUMP": 0, "INC_EMBED": 0, "EMBED_END": 0, "LOOPS": 0,
             "COND": 0, "DLY": 0, "INC": 0, "EMBED_LOOPS": 0}


def _norm(name: Any) -> str:
    """队列名归一化（与 g14b 的 `iq_name` 口径一致：小写 + '_'→'-'）。"""
    return str(name).strip().lower().replace("_", "-")


def _resolve_hw_config(hw_config: str) -> str:
    """相对路径优先按当前 cwd 解析；不存在则回退到仓库根（工具常被从任意 cwd 调用）。"""
    if Path(hw_config).exists():
        return hw_config
    cand = _REPO / hw_config
    return str(cand) if cand.exists() else hw_config


def load_schedule(build_root: Path) -> Dict[str, Any]:
    p = Path(build_root) / "compiler_schedule.json"
    if not p.exists():
        raise FileNotFoundError(f"缺 solver 调度（检查器唯一调度来源）: {p}")
    sch = json.loads(p.read_text())
    if "ops" not in sch:
        raise ValueError(f"{p} 无 ops 桶——调度体不完整，拒绝静默通过")
    for tid, info in sch["ops"].items():
        for key in ("t", "iq"):
            if key not in info:
                raise ValueError(f"{p} 中 op={tid} 缺 {key} 字段——无法独立重建")
    return sch


def _queue_meta(ii: int, ops_of_iq: List[Dict[str, Any]], num_iterations: int,
                iq_name: str, reg: ProtocolRegistry) -> Dict[str, int]:
    """单队列常数：skip 由 t 集合独立重算；l_init / TERM 相位一律注册表求值（§D.3）。"""
    if ii < 2:
        raise ValueError(f"{iq_name}: iq_II={ii}<2 不属 A1 展开包络（检查器拒绝越界推断）")
    ts = [int(o["t"]) for o in ops_of_iq]
    t_min, t_max = min(ts), max(ts)
    skip = t_min // ii
    return {"skip": skip,
            "l_init": reg.evaluate("INIT_PAD_FORMULA", skip=skip, II=ii),
            "r_q": (t_max // ii + num_iterations) - skip}


def derive_queue_rows(ii: int, ops_of_iq: List[Dict[str, Any]], num_iterations: int,
                      layout: Any, iq_name: str,
                      reg: Optional[ProtocolRegistry] = None) -> List[Dict[str, Any]]:
    """独立重建单队列的全部待发射行（顺序 = 发射序）。

    §D.3 铁律：INIT 垫拍 / TERM 相位常数**只**经注册表 `evaluate` 求得
    （唯一来源 `protocol_registry_v2.yaml`）；本函数不得内联公式，亦不得从
    trace/产物回读常数当期望（读出即比对失效）。
    """
    reg = _registry(reg)
    meta = _queue_meta(ii, ops_of_iq, num_iterations, iq_name, reg)
    skip, l_init, r_q = meta["skip"], meta["l_init"], meta["r_q"]
    if r_q < 1:
        raise ValueError(f"{iq_name}: R_q={r_q} < 1——调度体非法")

    slots: Dict[int, Dict[str, Any]] = {}
    for o in ops_of_iq:
        k = int(o["t"]) % ii
        if k in slots:
            raise ValueError(f"{iq_name}: 槽冲突 k={k} 被 {slots[k]['trace_id']} 与 "
                             f"{o['trace_id']} 同时占用")
        slots[k] = o

    is_ur = "ur-" in iq_name
    rows: List[Dict[str, Any]] = [{
        "iq_name": iq_name, "stage": "INIT", "abs_t": 0,
        "trace_id": f"init_{iq_name}",
        "header": {**_ZERO_HDR, "LOOPS": l_init}, "payload": {},
    }]

    for j in range(r_q):
        for k in range(ii):
            abs_t = l_init + 1 + k + j * ii
            op = slots.get(k)
            if op is None:
                rows.append({
                    "iq_name": iq_name, "stage": "BODY", "abs_t": abs_t,
                    "trace_id": f"nop_{iq_name}_{j}_{k}",
                    "header": dict(_ZERO_HDR), "payload": {},
                })
                continue
            stride = int(op.get("stride", 0) or 0)
            r_k = int(op["t"]) // ii - skip
            payload: Dict[str, Any] = {}
            if is_ur:
                bank, orig_offset = layout.resolve(int(op.get("logical_addr", 0) or 0))
                # ⚠ 与 assembler 的 `A_k + stride_k·j` 刻意不同式（见模块 docstring）：
                #   逻辑数据迭代号 i = j − r_k，地址 = 起点 + stride·i（负值由 Python % 归一到
                #   [0, bank_depth) —— 即 RTL `w_operate_micro_addr` 的 bank 内环绕语义）。
                payload = {"BANK": int(bank),
                           "ADDR": int((orig_offset + stride * (j - r_k)) % layout.bank_depth)}
            rows.append({
                "iq_name": iq_name, "stage": "BODY", "abs_t": abs_t,
                "trace_id": str(op["trace_id"]),
                "header": {**_ZERO_HDR, "VLD": 1, "DLY": int(op.get("dly", 0) or 0)},
                "payload": payload,
            })

    rows.append({
        "iq_name": iq_name, "stage": "TERM",
        "abs_t": reg.evaluate("TERM_PHASE_FORMULA", l_init=l_init, II=ii, R_q=r_q),
        "trace_id": f"term_{iq_name}",
        "header": {**_ZERO_HDR, "VLD": 1, "JUMP": 1}, "payload": {},
    })
    return rows


def compare_rows(actual: List[Dict[str, Any]], expected: List[Dict[str, Any]],
                 iq_name: str) -> List[str]:
    """逐行比对（expected 为契约侧；payload 取子集语义——产物多出的执行字段不算违例）。"""
    bad: List[str] = []
    if len(actual) != len(expected):
        bad.append(f"{iq_name}: 行数 {len(actual)} != 契约 {len(expected)}"
                   f"（INIT+II·R_q+TERM；多/少行即发射层漂移）")
    for idx in range(min(len(actual), len(expected))):
        a, e = actual[idx], expected[idx]
        tag = f"{iq_name}[{idx}] abs_t={e['abs_t']}"
        if int(a.get("abs_t", -1)) != e["abs_t"]:
            bad.append(f"{tag}: 产物 abs_t={a.get('abs_t')} != {e['abs_t']}（顺序/相位漂移）")
        if str(a.get("trace_id")) != e["trace_id"]:
            bad.append(f"{tag}: trace_id {a.get('trace_id')!r} != {e['trace_id']!r}"
                       f"（槽序或气泡/有效错位）")
        if str(a.get("stage")) != e["stage"]:
            bad.append(f"{tag}: stage {a.get('stage')!r} != {e['stage']!r}")
        ah = a.get("header") or {}
        for fld, ev in e["header"].items():
            av = ah.get(fld)
            if av is None:
                bad.append(f"{tag}: 产物缺字段 {fld}（hex 键名口径漂移，ADR-055 E7）")
            elif int(av) != int(ev):
                bad.append(f"{tag}: {fld}={av} != {ev}（相位编码）")
        ap = a.get("payload") or {}
        for fld, ev in e["payload"].items():
            if fld not in ap:
                bad.append(f"{tag}: 产物缺 payload.{fld}（地址未烘焙？M1）")
            elif int(ap[fld]) != int(ev):
                bad.append(f"{tag}: payload.{fld}={ap[fld]} != {ev}"
                           f"（地址契约 orig_offset+stride·(j−r_k)）")
    return bad


def _rebuild_all(sch: Dict[str, Any], branches: Dict[str, str],
                 extra_q_keys: List[str], num_iterations: int, layout: Any,
                 reg: ProtocolRegistry, require_unroll: bool):
    """重建侧（不触产物）：分支/桶断裂与独立重建违例子集，天秤原语返回 expected rows。

    循环集合 = branches ∪ extra_q_keys（产物侧键名，供「产物多出的队列」断裂诊断）∪
    schedule 桶。返回 (ii, expected_by_q, metas, bad)。
    """
    ii = int(sch["II"])
    ops_by_q: Dict[str, List[Dict[str, Any]]] = {}
    for tid, info in sch["ops"].items():
        ops_by_q.setdefault(_norm(info["iq"]), []).append({"trace_id": str(tid), **info})

    expected: Dict[str, List[Dict[str, Any]]] = {}
    metas: Dict[str, Dict[str, int]] = {}
    bad: List[str] = []
    for q in sorted(set(_norm(b) for b in branches) | set(extra_q_keys) | set(ops_by_q)):
        branch = branches.get(q)
        if branch is None:  # 产物/schedule 有该队列但分支表无 → 名称映射断裂
            bad.append(f"{q}: branch_report 无此队列（调度/分支/产物名称映射断裂）")
            continue
        if branch != "unroll":
            if require_unroll:
                bad.append(f"{q}: 分支={branch!r} != unroll——A1 白名单算子必须展开，"
                           f"非展开即 M4 静默回落")
            continue
        if q not in ops_by_q:
            bad.append(f"{q}: schedule 无对应桶（名称映射断裂）")
            continue
        try:
            metas[q] = _queue_meta(ii, ops_by_q[q], num_iterations, q, reg)
            expected[q] = derive_queue_rows(ii, ops_by_q[q], num_iterations, layout, q,
                                            reg=reg)
        except ValueError as exc:
            metas.pop(q, None)
            bad.append(f"{q}: 独立重建失败——{exc}")
    return ii, expected, metas, bad


def _rows_to_events(q: str, rows: List[Dict[str, Any]], ii: int,
                    meta: Dict[str, int],
                    reg: Optional[ProtocolRegistry] = None) -> Dict[str, Any]:
    """§D.4 事件投影：单队列 rows → (FETCH, ISSUE) 事件列表（协议模型期望侧）。

    事件格式 (cycle, iq, kind, payload)：cycle = 队列局部 rel_cycle（INIT FETCH=0，
    其余 = 行 abs_t）；锚定与跨队列相位规则见 payload 文件 semantics 段与消费者
    （GLOBAL_MONITOR_RULE）。ISSUE payload.vld = 该行/拍指令字 VLD 位；⚠ TERM 补拍
    的 vld=1 是 IQ **内部发射事件**（指令字 VLD），不构成一次有效算术操作/写回
    （注册表 PROJ_ISSUE_UNROLL v2 限定②）。

    ISSUE 模型（注册表 v2，勘误详见 arbitrate_add_n4_20260929 提案）：
      * 相位偏移 off = evaluate("ISSUE_PHASE_OFFSET")=1——dly_data_valid 为
        output reg 且全文件无时钟块外赋值（smi_iq_iic.v:96 等），TB posedge
        采样，解码 cycle t → 采样可见 t+off；
      * INIT 回放 l_init+1 拍（cycle = off+rep，rep=0..l_init）；
      * 每展开行 1 拍（cycle = abs_t+off）；
      * TERM（VLD=1·JUMP=1·EMBED=0·LOOPS=0）经 embed-0 路径在 abs_t+off 产 1 拍；
      * 总拍数 = evaluate("ISSUE_TOTAL_FORMULA")（常数一律注册表求值，§D.3 唯一来源）。
    flat 形态不在本投影（注册表 proven_boundary：实证缺口）。
    """
    reg = _registry(reg)
    l_init, r_q = meta["l_init"], meta["r_q"]
    off = reg.evaluate("ISSUE_PHASE_OFFSET")
    events: List[Dict[str, Any]] = []
    for r in rows:
        stage = str(r["stage"])
        abs_t = int(r["abs_t"])
        ep = {"stage": stage, "abs_t": abs_t, "trace_id": str(r["trace_id"]),
              "vld": int(r["header"]["VLD"]), **r["payload"]}
        events.append({"cycle": 0 if stage == "INIT" else abs_t,
                       "iq": q, "kind": "FETCH", "payload": ep})
    for r in rows:
        stage = str(r["stage"])
        abs_t = int(r["abs_t"])
        if stage == "INIT":
            for rep in range(l_init + 1):
                events.append({"cycle": off + rep, "iq": q, "kind": "ISSUE",
                               "payload": {"stage": "INIT", "abs_t": 0,
                                           "trace_id": str(r["trace_id"]),
                                           "vld": 0, "issue_rep": rep}})
        elif stage == "TERM":
            # v2 勘误：TERM 经 embed-0 路径（smi_iq_iic.v:662→:671→:693）产 1 拍
            # w_dly_data_valid。internal=True 标记该拍为 IQ 内部发射事件：
            # 有效写集/算术不变量不得对这一拍下断言（用户限定②）。
            events.append({"cycle": abs_t + off, "iq": q, "kind": "ISSUE",
                           "payload": {"stage": "TERM", "abs_t": abs_t,
                                       "trace_id": str(r["trace_id"]),
                                       "vld": 1, "issue_rep": 0,
                                       "internal": True}})
        else:
            events.append({"cycle": abs_t + off, "iq": q, "kind": "ISSUE",
                           "payload": {"stage": "BODY", "abs_t": abs_t,
                                       "trace_id": str(r["trace_id"]),
                                       "vld": int(r["header"]["VLD"]),
                                       "issue_rep": 0}})
    events.sort(key=lambda e: (int(e["cycle"]), e["kind"]))
    n_fetch = sum(1 for e in events if e["kind"] == "FETCH")
    n_issue = sum(1 for e in events if e["kind"] == "ISSUE")
    l_q = ii * r_q + 2                       # PROJ_FETCH_UNROLL 行数恒等式
    issue_total = reg.evaluate("ISSUE_TOTAL_FORMULA", l_init=l_init, II=ii, R_q=r_q)
    # 内部一致性（工具漂移即硬失败，非违例字符串——协议模型自洽是全局前提）
    assert len(rows) == l_q, (q, len(rows), l_q)
    assert n_fetch == l_q, (q, n_fetch, l_q)
    assert n_issue == issue_total, (q, n_issue, issue_total)
    return {"skip": int(meta["skip"]), "l_init": l_init, "R_q": r_q,
            "L_q": l_q, "issue_beats": issue_total, "events": events}


def build_events_payload(build_root: Path, sch: Dict[str, Any],
                         expected: Dict[str, List[Dict[str, Any]]],
                         metas: Dict[str, Dict[str, int]],
                         num_iterations: int, ii: int,
                         reg: ProtocolRegistry) -> Dict[str, Any]:
    """§D.3「期望事件在仿真前生成」落盘体（schema adr056_d4_expected_events_v1）。"""
    queues = {q: _rows_to_events(q, expected[q], ii, metas[q], reg=reg)
              for q in sorted(expected)}
    return {
        "schema": "adr056_d4_expected_events_v1",
        "generated_by": "verification_v2/tools/emit_expected_fire_sequence.py --emit-events",
        "frozen_rule": ("ADR-056 §D.3 用户修约③：本文件由「调度 + 注册表延迟模型」在仿真前生成；"
                        "trace 与本文件不符 → PER_BEAT_PROTOCOL_MISMATCH，"
                        "禁止回改注册表常数或 schedule 侧输入拟合"),
        "registry": {"path": str(reg.path), "version": reg.version,
                     "records_sha256": reg.records_digest},
        "build": str(build_root),
        "num_iterations": int(num_iterations),
        "II": int(ii),
        "semantics": {
            "cycle_base": ("队列局部 rel_cycle（INIT FETCH = 0）；跨队列相位以共同启动锚 "
                           "（TB iq_probe_cycle）对齐检查，不允许逐队列独立平移后宣布一致 "
                           "（GLOBAL_MONITOR_RULE）"),
            "fetch": ("INIT 1 次(rel 0) + BODY（有效/气泡）各 1 次严格连续(rel=abs_t) + "
                      "TERM 1 次；行数 = L_q；TERM/JUMP 后残尾取指不计违例，裁决只覆盖至 "
                      "TERM 含（PROJ_FETCH_UNROLL + PROBES_TAIL_SEMANTICS，残尾仅限并集内队列）"),
            "issue": ("每 ISSUE 拍 = 对应执行拍 +1（结构性寄存器边界：dly_data_valid "
                      "为 output reg，时钟块 <= 赋值 + TB posedge 采样，注册表 v2 "
                      "ISSUE_PHASE_OFFSET）：INIT 回放 l_init+1 拍(cycle 1..l_init+1)；"
                      "每展开行 1 拍(cycle=abs_t+1)；TERM（VLD=1·JUMP=1·EMBED=0·LOOPS=0）"
                      "经 embed-0 路径在 TERM fetch+1 产 1 拍（IQ 内部发射事件，"
                      "≠有效算术操作/写回）；总拍数 = ISSUE_TOTAL_FORMULA = "
                      "l_init+1+II·R_q+1（PROJ_ISSUE_UNROLL，unroll 分支；flat 形态"
                      "为实证缺口不得引用）"),
        },
        "queues": queues,
        "union_queues": sorted(expected),
    }


def audit(build_root: Path, num_iterations: int, hw_config: str = "hw_config",
          require_unroll: bool = True,
          emit_events_path: Optional[Path] = None) -> List[str]:
    """返回违例字符串列表（空 = 全绿）。build_root = `build/verification_v2/<op>/`。

    emit_events_path 给出时先落盘 §D.4 期望事件 JSON（重建侧有违例则拒绝落盘——
    假期望不得入库）；产物 compare 违例不阻断落盘（发射器审计与仿真期望各自携带
    独立判决，统一入口按任一非空熔断）。
    """
    from src.api.hw_spec import HardwareSpec
    from src.core.memory_layout import PhysicalMemoryLayout

    build_root = Path(build_root)
    sch = load_schedule(build_root)
    reg = _registry(None)

    br_path = build_root / "branch_report.json"
    if not br_path.exists():
        return [f"缺 branch_report.json（无法判定分支，拒绝静默通过）: {br_path}"]
    branches = json.loads(br_path.read_text()).get("branches", {})

    pi_path = build_root / "physical_instructions.json"
    if not pi_path.exists():
        return [f"缺 physical_instructions.json: {pi_path}"]
    per_q: Dict[str, List[Dict[str, Any]]] = {}
    for r in json.loads(pi_path.read_text()):
        per_q.setdefault(_norm(r["iq_name"]), []).append(r)

    layout = PhysicalMemoryLayout.from_spec(HardwareSpec.load(_resolve_hw_config(hw_config)))
    ii, expected, metas, bad = _rebuild_all(
        sch, branches, list(per_q), num_iterations, layout, reg, require_unroll)

    if emit_events_path is not None:
        if bad:
            bad.append("[emit-events] 独立重建存在违例，期望事件拒绝落盘（假期望禁入库）")
        else:
            payload = build_events_payload(build_root, sch, expected, metas,
                                           num_iterations, ii, reg)
            out = Path(emit_events_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2, sort_keys=True,
                                      ensure_ascii=False), encoding="utf-8")
            print(f"[EMIT] 期望事件已落盘（仿真前沿 §D.3 冻结）: {out} "
                  f"（{len(payload['union_queues'])} 队列，"
                  f"{sum(len(qq['events']) for qq in payload['queues'].values())} 事件）")

    for q in sorted(set(expected) | set(per_q)):
        if q not in expected:
            continue  # rebuild 侧已报该队列违例
        if q not in per_q:
            bad.append(f"{q}: 产物无该队列行")
            continue
        actual = sorted(per_q[q], key=lambda r: int(r["abs_t"]))
        bad += compare_rows(actual, expected[q], q)
    return bad


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ADR-055 D4(b-1) 独立发射事件检查器 + §D.4 期望事件生成")
    ap.add_argument("--build", required=True, help="构建目录（含 compiler_schedule.json 等）")
    ap.add_argument("--num-iterations", type=int, required=True,
                    help="N_eff（= num_iterations；仅 SMI_DS_T 路径钉 2，A1 不含该路径）")
    ap.add_argument("--hw-config", default="hw_config")
    ap.add_argument("--allow-non-unroll", action="store_true",
                    help="不要求全队列展开（默认要求，A1 验收用）")
    ap.add_argument("--emit-events", default=None, metavar="PATH",
                    help="§D.3 期望先行：仿真前落盘 FETCH/ISSUE 期望事件 JSON"
                         "（重建侧有违例则拒绝落盘）")
    args = ap.parse_args(argv)

    bad = audit(args.build, args.num_iterations, args.hw_config,
                require_unroll=not args.allow_non_unroll,
                emit_events_path=Path(args.emit_events) if args.emit_events else None)
    if bad:
        print(f"[FAIL] 独立事件检查：{len(bad)} 条违例")
        for b in bad:
            print(f"  - {b}")
        return 1
    print(f"[OK] 独立事件检查全绿：{args.build}（II/abs_t/相位编码/BANK/ADDR 逐行一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
