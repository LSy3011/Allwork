#!/usr/bin/env python3
"""ADR-055 A1 三区比对：用**编译器不可见**的初始/终值对照把「全 2048 行 PASS」拆成三个区。

三区定义（D5 契约，逐字对应验收三点）：
  ① 有效区 T  = { (bank,i) | golden[i] != init[i] } —— golden 与 TB 预载初值的差分得出，
     不经过编译器产物，因此天然独立于 assembler；本区 dump 必须逐位等于 golden，且行数点名。
  ② 全区     = 8 bank × 256 行的 dump==golden 总计（与原生 compare_ur_to_golden 结论互校）。
  ③ 非目标区 = ∀i∉T：dump[i]==golden[i]==init[i]——防「大片未写相等掩盖覆盖不足」。

反空洞断言：
  * |T| > 0；
  * 若提供 --instructions（归档的 physical_instructions.json），|T| 必须等于其中
    vout0（指定 --write-queue）BODY 行 vld=1 的行数——simulator（golden 来源）与
    assembler（行来源）双源计数互校，不一致即 RED；
  * 数值一律比整数（int(line,16)），禁止字符串相等。

用法：
  python3 three_zone_compare.py <case_smc0_dir> --label n8 \
      --instructions <archive>/n8/physical_instructions.json [--write-queue vout0]
退出：0=全绿，1=任一区违例，2=输入缺失/坏。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

N_BANKS = 8


def read_hex_ints(path: Path) -> list[int]:
    vals = []
    for ln, raw in enumerate(path.read_text().splitlines(), 1):
        s = raw.strip()
        if not s:
            continue
        s = s[2:] if s.lower().startswith("0x") else s
        try:
            vals.append(int(s, 16))
        except ValueError:
            raise SystemExit(f"2: 非 hex 行 {path}:{ln}: {raw!r}")
    return vals


def live_bodily_writes(instr_path: Path, queue: str) -> int:
    rows = json.loads(instr_path.read_text())
    n = 0
    for r in rows:
        if r.get("iq_name") == queue and r.get("stage") == "BODY" \
                and int(r["header"]["VLD"]) == 1:
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_smc0_dir", type=Path)
    ap.add_argument("--label", default="")
    ap.add_argument("--instructions", type=Path, default=None)
    ap.add_argument("--write-queue", default="vout0")
    a = ap.parse_args()
    d = a.case_smc0_dir

    init_b, gold_b, dump_b = [], [], []
    for b in range(N_BANKS):
        p_init = d / "ur_data" / f"bank{b}.hex"
        p_gold = d / "output_bank" / f"bank{b}_final.hex"
        p_dump = d / f"ur_result_smc_0_bank{b}.hex"
        missing = [str(p) for p in (p_init, p_gold, p_dump) if not p.is_file()]
        if missing:
            print(f"[三区比对] {a.label}: 输入缺失: {missing}", file=sys.stderr)
            return 2
        init_b.append(read_hex_ints(p_init))
        gold_b.append(read_hex_ints(p_gold))
        dump_b.append(read_hex_ints(p_dump))

    per_bank = []
    t_total = z1_bad = z3_bad = z2_bad = 0
    for b in range(N_BANKS):
        init, gold, dump = init_b[b], gold_b[b], dump_b[b]
        if not (len(init) == len(gold) == len(dump)):
            print(f"[三区比对] bank{b}: 行数不一致 init={len(init)} golden={len(gold)} "
                  f"dump={len(dump)}", file=sys.stderr)
            return 2
        target = [i for i in range(len(gold)) if gold[i] != init[i]]
        t_set = set(target)
        bad1 = [i for i in target if dump[i] != gold[i]]
        bad3 = [i for i in range(len(gold)) if i not in t_set and dump[i] != gold[i]]
        bad2 = [i for i in range(len(gold)) if dump[i] != gold[i]]
        t_total += len(target)
        z1_bad += len(bad1)
        z3_bad += len(bad3)
        z2_bad += len(bad2)
        per_bank.append({"bank": b, "rows": len(gold), "target_rows": len(target),
                         "zone1_bad": len(bad1), "zone3_bad": len(bad3),
                         "target_idx": target[:40] + (["..."] if len(target) > 40 else []),
                         "zone1_bad_idx": bad1[:20], "zone3_bad_idx": bad3[:20]})

    expect = None
    if a.instructions is not None:
        if not a.instructions.is_file():
            print(f"[三区比对] 缺 {a.instructions}", file=sys.stderr)
            return 2
        expect = live_bodily_writes(a.instructions, a.write_queue)

    green = (z1_bad == 0 and z3_bad == 0 and t_total > 0
             and (expect is None or t_total == expect))
    report = {
        "label": a.label,
        "case": str(d),
        "target_lines_total": t_total,
        "target_lines_expected_from_instructions": expect,
        "zone1_valid_region_bitexact_bad": z1_bad,
        "zone2_full_region_bad": z2_bad,
        "zone3_nontarget_region_bad": z3_bad,
        "verdict": "GREEN" if green else "RED",
        "per_bank": per_bank,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if green else 1


if __name__ == "__main__":
    raise SystemExit(main())
