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

契约（ADR-055 D2）
------------------
    skip    = t_min // iq_II               # 本队列 IQ 局部
    l_init  = skip·iq_II + 1               # INIT 垫拍 loops（无 HDR 占位拍，多垫 1 拍）
    R_q     = (t_max // iq_II + N_eff) − skip
    L_q     = iq_II·R_q + 2                # INIT + iq_II·R_q 展开行 + TERM
    r_k     = t_k // iq_II − skip          # 该 op 的调度迭代偏移
    有效行(j,k): abs_t = l_init+1+k+j·iq_II, vld=1, level=0, loops=0, inc=0, dly=op_dly,
                 embed_end=0, inc_embed=0, trace_id=op.trace_id,
                 BANK/ADDR = resolve(logical_addr) / (orig_offset + stride·(j−r_k)) mod depth
    气泡行     : 同位 abs_t, vld=0 全零域, payload={}, trace_id=nop_{iq}_{j}_{k}
    TERM       : abs_t = l_init+1+iq_II·R_q, vld=1, level=0, loops=0, jump=1, payload={}

CLI
---
    python verification_v2/tools/emit_expected_fire_sequence.py \
        --build build/verification_v2/add --num-iterations 2
退出码 0 = 全绿；1 = 有违例（逐条打印）。
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


def derive_queue_rows(ii: int, ops_of_iq: List[Dict[str, Any]], num_iterations: int,
                      layout: Any, iq_name: str) -> List[Dict[str, Any]]:
    """独立重建单队列的全部待发射行（顺序 = 发射序）。"""
    if ii < 2:
        raise ValueError(f"{iq_name}: iq_II={ii}<2 不属 A1 展开包络（检查器拒绝越界推断）")
    ts = [int(o["t"]) for o in ops_of_iq]
    t_min, t_max = min(ts), max(ts)
    skip = t_min // ii
    l_init = skip * ii + 1
    r_q = (t_max // ii + num_iterations) - skip
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
        "iq_name": iq_name, "stage": "TERM", "abs_t": l_init + 1 + ii * r_q,
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


def audit(build_root: Path, num_iterations: int, hw_config: str = "hw_config",
          require_unroll: bool = True) -> List[str]:
    """返回违例字符串列表（空 = 全绿）。build_root = `build/verification_v2/<op>/`。"""
    from src.api.hw_spec import HardwareSpec
    from src.core.memory_layout import PhysicalMemoryLayout

    build_root = Path(build_root)
    sch = load_schedule(build_root)
    ii = int(sch["II"])

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

    ops_by_q: Dict[str, List[Dict[str, Any]]] = {}
    for tid, info in sch["ops"].items():
        ops_by_q.setdefault(_norm(info["iq"]), []).append({"trace_id": str(tid), **info})

    layout = PhysicalMemoryLayout.from_spec(HardwareSpec.load(_resolve_hw_config(hw_config)))
    bad: List[str] = []
    for q in sorted(set(_norm(b) for b in branches) | set(per_q) | set(ops_by_q)):
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
        if q not in per_q:
            bad.append(f"{q}: 产物无该队列行")
            continue
        try:
            expected = derive_queue_rows(ii, ops_by_q[q], num_iterations, layout, q)
        except ValueError as exc:
            bad.append(f"{q}: 独立重建失败——{exc}")
            continue
        actual = sorted(per_q[q], key=lambda r: int(r["abs_t"]))
        bad += compare_rows(actual, expected, q)
    return bad


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ADR-055 D4(b-1) 独立发射事件检查器")
    ap.add_argument("--build", required=True, help="构建目录（含 compiler_schedule.json 等）")
    ap.add_argument("--num-iterations", type=int, required=True,
                    help="N_eff（= num_iterations；仅 SMI_DS_T 路径钉 2，A1 不含该路径）")
    ap.add_argument("--hw-config", default="hw_config")
    ap.add_argument("--allow-non-unroll", action="store_true",
                    help="不要求全队列展开（默认要求，A1 验收用）")
    args = ap.parse_args(argv)

    bad = audit(args.build, args.num_iterations, args.hw_config,
                require_unroll=not args.allow_non_unroll)
    if bad:
        print(f"[FAIL] 独立事件检查：{len(bad)} 条违例")
        for b in bad:
            print(f"  - {b}")
        return 1
    print(f"[OK] 独立事件检查全绿：{args.build}（II/abs_t/相位编码/BANK/ADDR 逐行一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
