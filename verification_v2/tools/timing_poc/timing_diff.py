#!/usr/bin/env python3
"""方案 C 时序 diff（PoC）—— compiler 期望时间线 vs VCS 实测波形。

对 UR bank 写事件（compute 输出锚点）做两维比对：
  ① 值：vcs_expected/smc_K_bankB_expected.hex[addr]  vs  VCD 实测 dina
  ② 时序：predicted_rel(smc=k, addr=i) = k + i*II        vs  VCD 实测 cycle - anchor
     - anchor = 所有实测写里最早的 cycle（= SMC_0 addr0），即 issue→write 绝对
       偏移，属实测量不预测（compiler 无法预测 RTL 复位/配置启动时延）。
     - SMC 偏移 +k 来自硬件时序模型（串行 SMC，1 latency/hop）。
     - addr 步进 i*II 来自 compiler_schedule II + UR_WRITE 循环 INC。

输出：第一处发散 (smc/addr/期望-vs-实测/kind)，或 ALL GREEN + 结构摘要。
退出码：0 全绿，1 有发散，2 数据缺失。

用法：
  python timing_diff.py --build <compiler_build_dir> --vcd <vcd> [--bank 2]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from extract_ur_writes import extract


def load_expected_hex(build_dir: str, smc: int, bank: int):
    p = os.path.join(build_dir, "vcs_expected",
                     f"smc_{smc}_bank{bank}_expected.hex")
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        return [ln.strip().lower() for ln in f if ln.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True, help="compiler build 目录（含 vcs_expected/ + compiler_schedule.json）")
    ap.add_argument("--vcd", required=True)
    ap.add_argument("--bank", type=int, default=2)
    ap.add_argument("--smcs", type=int, default=8)
    args = ap.parse_args()

    bank = args.bank
    smcs = list(range(args.smcs))

    # II（addr 步进）
    sched_p = os.path.join(args.build, "compiler_schedule.json")
    II = 1
    if os.path.isfile(sched_p):
        II = json.load(open(sched_p)).get("II", 1)

    # 期望值
    expected = {}   # smc -> [hex per addr]
    for k in smcs:
        ex = load_expected_hex(args.build, k, bank)
        if ex is None:
            print(f"[缺失] vcs_expected/smc_{k}_bank{bank}_expected.hex 不存在", file=sys.stderr)
            return 2
        expected[k] = ex

    # 实测
    print(f"解析 VCD（{args.vcd}）抽 bank{bank} 写事件 ...", file=sys.stderr)
    actual = extract(args.vcd, banks=[bank], smcs=smcs)

    # anchor：最早实测写 cycle
    all_first = [actual[(k, bank)][0].cycle for k in smcs if actual[(k, bank)]]
    if not all_first:
        print("[缺失] VCD 中未发现任何 bank 写事件", file=sys.stderr)
        return 2
    anchor = min(all_first)

    # 逐 (smc, addr) 比对；按实测 cycle 排序找第一处发散
    divergences = []   # (sort_cycle, smc, addr, kind, exp, act)
    n_checked = 0
    for k in smcs:
        ev = {w.addr: w for w in actual[(k, bank)]}
        exp_lines = expected[k]
        for i, exp_hex in enumerate(exp_lines):
            n_checked += 1
            pred_rel = k + i * II
            w = ev.get(i)
            if w is None:
                divergences.append((10**9 + i, k, i, "MISSING_WRITE",
                                    f"rel={pred_rel} val={exp_hex}", "无写事件"))
                continue
            act_rel = w.cycle - anchor
            # 值
            if w.data_hex != exp_hex:
                divergences.append((w.cycle, k, i, "VALUE",
                                    exp_hex, w.data_hex))
            # 时序
            if act_rel != pred_rel:
                divergences.append((w.cycle, k, i, "TIMING",
                                    f"rel={pred_rel}", f"rel={act_rel} (cyc={w.cycle})"))

    print(f"\n{'='*64}")
    print(f"方案 C 时序 diff —— bank{bank}  (II={II}, anchor_cycle={anchor})")
    print(f"检查点 {n_checked} 个 (= {len(smcs)} SMC × {len(expected[smcs[0]])} addr)")
    print(f"{'='*64}")

    if not divergences:
        # 结构摘要
        for k in smcs:
            e = actual[(k, bank)]
            rels = [w.cycle - anchor for w in e]
            print(f"SMC_{k}: {len(e)} 写, rel_cycles={rels[0]}..{rels[-1]}, "
                  f"SMC偏移={e[0].cycle - anchor}")
        print("\n✅ ALL GREEN —— compiler 期望（值+时序）与 VCS 实测逐事件一致。")
        return 0

    divergences.sort()
    print(f"\n❌ 发现 {len(divergences)} 处发散，第一处：")
    sc, k, i, kind, exp, act = divergences[0]
    print(f"  SMC_{k}  addr={i}  类型={kind}")
    print(f"    期望: {exp}")
    print(f"    实测: {act}")
    if len(divergences) > 1:
        print(f"\n  其余 {len(divergences)-1} 处（前 10）：")
        for sc, k, i, kind, exp, act in divergences[1:11]:
            print(f"    SMC_{k} addr={i} {kind}: 期望[{exp}] 实测[{act}]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
