#!/usr/bin/env python3
"""路径 A：逐目标（逐 SMC / 逐通道）时序裁决器（2026-10-14）。

设计稿 docs/8smc_pathA_full_target_monitoring_20261014.md §2。

输入（全部来自**该运行自身**的 evidence 目录，禁止跨 run 移植时序）：
  n{N}_sim_log.log        阶段边界 + 屏障 enter/release + [PA][ORD] 汇总 + [PA][CRED]
  n{N}_pa_ur_wr_trace.log 逐 SMC UR 写（cls=ura 装载落位 / cru 计算提交落位）
  n{N}_pa_ur_rd_trace.log 逐 SMC COMPUTE 读（CRU 读口 eff）
  n{N}_pa_stb_rd_trace.log 逐 SMC STORE 读（STB URA 读口）
  n{N}_hbm_write_trace.log 逐 SMC HBM 写落位
  n{N}_pa_bresp_trace.log 逐通道写响应（B 握手）

判据（覆盖**全部活动目标**，不只 SMC0）：
  C1 每个活动 SMC k：first_compute_read(k) > last_load_write(k)
  C2 每个活动 SMC k：first_store_read(k)   > last_compute_write(k)
  C3 最终 dump：> 全部活动目标的 HBM 写落位与所需 B 响应完成

「活动目标」= 运行中真实出现事件的 SMC 集合 ∩ 期望集合；期望目标缺记录 ⇒ RED
（缺目标不得静默通过，用户 2026-10-14 验收点⑤）。

CLI：pathA_target_ordering.py --evidence DIR --n 16 [--expect-smcs 8]
                               [--stage-load 0] [--stage-compute 1] [--stage-store 2]
                               [--json OUT]
退出码：0 = 全部判据成立；2 = 至少一项违例或目标缺记录；3 = 输入缺失/无法解析。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_STAGE_BEGIN_RE = re.compile(
    r"===== stage_(\d+)/(\d+) BEGIN ===== .*@probe_cycle (\d+)")
_STAGE_DONE_RE = re.compile(
    r"stage_(\d+) done \(intr cleared\).*@probe_cycle (\d+)")
_DUMP_RE = re.compile(r"\[GATE4\]\[DUMP\] begin @probe_cycle (\d+)")


def _cycles(rows: List[Tuple[int, int]]) -> Tuple[Optional[int], Optional[int]]:
    """(first, last) cycles of (cycle, payload) rows."""
    if not rows:
        return None, None
    cs = [c for c, _ in rows]
    return min(cs), max(cs)


def parse_stage_bounds(sim_log: str) -> Tuple[Dict[int, int], Dict[int, int],
                                              Optional[int]]:
    begins: Dict[int, int] = {}
    ends: Dict[int, int] = {}
    dump: Optional[int] = None
    for line in sim_log.splitlines():
        m = _STAGE_BEGIN_RE.search(line)
        if m:
            begins[int(m.group(1))] = int(m.group(3))
        m = _STAGE_DONE_RE.search(line)
        if m:
            ends[int(m.group(1))] = int(m.group(2))
        m = _DUMP_RE.search(line)
        if m:
            dump = int(m.group(1))
    return begins, ends, dump


def _read_ur_writes(path: Path) -> Dict[int, Dict[str, List[Tuple[int, int]]]]:
    """pa_ur_wr_trace.log → {smc: {cls: [(cycle, addr)]}}；cls ∈ {ura, cru}。"""
    out: Dict[int, Dict[str, List[Tuple[int, int]]]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        cyc, smc, cls = int(parts[0]), int(parts[1]), parts[2]
        bank, addr = int(parts[3]), int(parts[4])
        out.setdefault(smc, {}).setdefault(cls, []).append(
            (cyc, bank * 4096 + addr))
    return out


def _read_ur_rd(path: Path) -> Dict[int, List[Tuple[int, int]]]:
    """pa_ur_rd_trace.log → {smc: [(cyc, bank*4096+raddr)]}
    格式（TB 实写，逐字）：`%0d %0d %0d %0d %0d` = cycle smc port bank addr（全十进制）。"""
    out: Dict[int, List[Tuple[int, int]]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            cyc, smc, _port, bank, addr = (int(x) for x in parts[:5])
        except ValueError:
            continue  # x/z 态等非数行：跳过（不猜测）
        out.setdefault(smc, []).append((cyc, bank * 4096 + addr))
    return out


def _read_stb_rd(path: Path) -> Dict[int, List[Tuple[int, int]]]:
    """pa_stb_rd_trace.log → {smc: [(cyc, addr)]}
    格式（TB 实写，逐字）：`%0d %0d %04h` = cycle smc raddr（地址为**十六进制无前缀**）。"""
    out: Dict[int, List[Tuple[int, int]]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            cyc, smc = int(parts[0]), int(parts[1])
            addr = int(parts[2], 16)
        except ValueError:
            continue
        out.setdefault(smc, []).append((cyc, addr))
    return out


def _read_crd(path: Path) -> Dict[str, Dict[int, List[int]]]:
    """pa_crd_trace.log → {sig: {pos: [cyc,...]}}
    格式：`%0d %s %0d %b %b %0d %b` = cycle sig pos vld done inst err。"""
    out: Dict[str, Dict[int, List[int]]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            cyc, pos = int(parts[0]), int(parts[2])
        except ValueError:
            continue
        out.setdefault(parts[1], {}).setdefault(pos, []).append(cyc)
    return out


def _read_hbm_writes(path: Path) -> Dict[int, List[Tuple[int, int]]]:
    """hbm_write_trace.log（原树既有，5 列：cycle smc addra wea dina）"""
    out: Dict[int, List[Tuple[int, int]]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        out.setdefault(int(parts[1]), []).append(
            (int(parts[0]), int(parts[2], 16)))
    return out


def _read_bresp(path: Path) -> Dict[int, List[int]]:
    out: Dict[int, List[int]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        out.setdefault(int(parts[1]), []).append(int(parts[0]))
    return out


def _in_stage(cyc: int, bounds: Dict[int, int], ends: Dict[int, int],
              stage: int) -> bool:
    if stage not in bounds:
        return False
    lo = bounds[stage]
    hi = ends.get(stage, 10 ** 12)
    return lo <= cyc <= hi


def analyse(evidence: Path, n: int, expect_smcs: int,
            stage_load: int, stage_compute: int, stage_store: int,
            expect_bresp: int = 1) -> dict:
    sim = evidence / f"n{n}_sim_log.log"
    if not sim.is_file():
        raise FileNotFoundError(f"缺少运行自身日志: {sim}")
    begins, ends, dump = parse_stage_bounds(sim.read_text(errors="replace"))

    urw = _read_ur_writes(evidence / f"n{n}_pa_ur_wr_trace.log")
    urr = _read_ur_rd(evidence / f"n{n}_pa_ur_rd_trace.log")
    stbr = _read_stb_rd(evidence / f"n{n}_pa_stb_rd_trace.log")
    hbmw = _read_hbm_writes(evidence / f"n{n}_hbm_write_trace.log")
    bresp = _read_bresp(evidence / f"n{n}_pa_bresp_trace.log")
    crd = _read_crd(evidence / f"n{n}_pa_crd_trace.log")

    observed = set(urw) | set(urr) | set(stbr) | set(hbmw) | set(bresp)
    per_target: Dict[str, dict] = {}
    bad: List[str] = []

    for k in range(expect_smcs):
        load_w = [(c, a) for (c, a) in urw.get(k, {}).get("ura", [])
                  if _in_stage(c, begins, ends, stage_load)]
        comp_w = [(c, a) for (c, a) in urw.get(k, {}).get("cru", [])
                  if _in_stage(c, begins, ends, stage_compute)]
        comp_r = [(c, a) for (c, a) in urr.get(k, [])
                  if _in_stage(c, begins, ends, stage_compute)]
        store_r = [(c, a) for (c, a) in stbr.get(k, [])
                   if _in_stage(c, begins, ends, stage_store)]
        hbm_k = hbmw.get(k, [])
        ent = {
            "load_writes": len(load_w),
            "compute_writes": len(comp_w),
            "compute_reads": len(comp_r),
            "store_reads": len(store_r),
            "hbm_writes": len(hbm_k),
            "bresp": len(bresp.get(k, [])),
            "last_load_write": _cycles(load_w)[1],
            "first_compute_read": _cycles(comp_r)[0],
            "last_compute_write": _cycles(comp_w)[1],
            "first_store_read": _cycles(store_r)[0],
            "hbm_last": _cycles(hbm_k)[1],
            "bresp_last": max(bresp.get(k, [])) if bresp.get(k) else None,
        }
        per_target[f"smc_{k}"] = ent
        if k not in observed:
            bad.append(f"(TARGET-MISSING/smc_{k}) 该活动目标在全部探针日志中零事件"
                       f"——缺目标记录，不得静默通过")
            continue
        if ent["last_load_write"] is None:
            bad.append(f"(C1/smc_{k}) LOAD 装载落位缺失（无 ura 类写）")
        elif ent["first_compute_read"] is None:
            bad.append(f"(C1/smc_{k}) COMPUTE 首读缺失（无 CRU 读口事件）")
        elif ent["first_compute_read"] <= ent["last_load_write"]:
            bad.append(f"(C1/smc_{k}) COMPUTE 首读 {ent['first_compute_read']} "
                       f"≤ 本目标 LOAD 末写 {ent['last_load_write']}")
        if ent["last_compute_write"] is None:
            bad.append(f"(C2/smc_{k}) 计算落位缺失（无 cru 类写）")
        elif ent["first_store_read"] is None:
            bad.append(f"(C2/smc_{k}) STORE 首读缺失（无 STB URA 读事件）")
        elif ent["first_store_read"] <= ent["last_compute_write"]:
            bad.append(f"(C2/smc_{k}) STORE 首读 {ent['first_store_read']} "
                       f"≤ 本目标计算末写 {ent['last_compute_write']}")
        if ent["hbm_writes"] == 0:
            bad.append(f"(C3/smc_{k}) 该通道 HBM 写落位缺失")
        if ent["bresp"] < expect_bresp:
            bad.append(f"(C3/smc_{k}) 该通道写响应 {ent['bresp']} < 期望 "
                       f"{expect_bresp}——外部写完成未达")

    # C3 dump 晚于全部目标写入与响应
    last_all = [v for k in range(expect_smcs)
                for v in (per_target[f"smc_{k}"]["hbm_last"],
                          per_target[f"smc_{k}"]["bresp_last"]) if v is not None]
    dump_ok = None
    if dump is not None and last_all:
        dump_ok = dump >= max(last_all)
        if not dump_ok:
            bad.append(f"(C3/dump) dump @{dump} 早于末次目标写入/响应 "
                       f"@{max(last_all)}")

    return {
        "schema": "pathA_target_ordering_v1",
        "evidence_dir": str(evidence),
        "n_rows_per_smc": n,
        "expect_smcs": expect_smcs,
        "stage_bounds": {"load": begins.get(stage_load),
                         "compute": begins.get(stage_compute),
                         "store": begins.get(stage_store)},
        "stage_ends": {str(k): v for k, v in sorted(ends.items())},
        "dump_cycle": dump,
        "dump_after_all_targets": dump_ok,
        "observed_smcs": sorted(observed),
        "per_target": per_target,
        # 信用链逐位置（pos 0..7）：聚合上报点 = pos 0。差分延迟签名用：
        # 延迟 SMC k 应恰使 pos 0..k 后移、pos k+1..7 不变。
        "credit_positions": {
            sig: {str(p): {"count": len(c), "first": min(c), "last": max(c)}
                  for p, c in sorted(poss.items())}
            for sig, poss in sorted(crd.items())},
        "violations": bad,
        "verdict": "GREEN" if not bad else "RED",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evidence", required=True, type=Path)
    ap.add_argument("--n", required=True, type=int)
    ap.add_argument("--expect-smcs", type=int, default=8)
    ap.add_argument("--stage-load", type=int, default=0)
    ap.add_argument("--stage-compute", type=int, default=1)
    ap.add_argument("--stage-store", type=int, default=2)
    ap.add_argument("--bresp", type=int, default=1)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    try:
        rep = analyse(a.evidence, a.n, a.expect_smcs, a.stage_load,
                      a.stage_compute, a.stage_store, a.bresp)
    except FileNotFoundError as e:
        print(f"[PATH-A][ORDER][ERR] 输入缺失: {e}", file=sys.stderr)
        return 3
    for k, v in rep["per_target"].items():
        print(f"[PATH-A][ORDER] {k} load_w={v['load_writes']} "
              f"last_load={v['last_load_write']} "
              f"first_comp_rd={v['first_compute_read']} "
              f"last_comp_wr={v['last_compute_write']} "
              f"first_store_rd={v['first_store_read']} "
              f"hbm_w={v['hbm_writes']} bresp={v['bresp']}")
    print(f"[PATH-A][ORDER] dump={rep['dump_cycle']} "
          f"dump_after_all_targets={rep['dump_after_all_targets']}")
    for sig, poss in rep["credit_positions"].items():
        print(f"[PATH-A][CRED] sig={sig} "
              + " ".join(f"pos{p}:n={v['count']}@{v['first']}-{v['last']}"
                         for p, v in poss.items()))
    if rep["violations"]:
        print(f"[PATH-A][ORDER] RED（{len(rep['violations'])} 项）")
        for v in rep["violations"]:
            print(f"  - {v}")
    else:
        print("[PATH-A][ORDER] GREEN：全部活动目标的三条放行条件成立")
    if a.json:
        a.json.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    return 0 if rep["verdict"] == "GREEN" else 2


if __name__ == "__main__":
    sys.exit(main())
