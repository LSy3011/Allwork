#!/usr/bin/env python3
"""hbm_window_compare —— ADR-058 / M3-lite 门①：STB 写回 HBM 的六项断言比对。

输入：case 目录（含 hbm_expect.json）+ TB dump（hbm_result_smc_*.hex /
ur_result_smc_0_bank*.hex）+ sim.log。任一断言失败即 RED、退出码 1。

断言（对应用户门①判据逐字映射）：
    A1 数据正确  窗口 [byte_base, +16*lines) 逐行 == UR 预置图案（bit-exact hex 行比对）
    A2 地址正确  窗口两端紧邻行仍为预载值（窗口位置错→紧邻行先变）+ A3 联合锚定
    A3 数量正确  窗口内恰好 lines 行异于预载，其余预载行不变
    A4 非目标区  目标颗窗口外全域 == 预载（[0,preload)）或全零（[preload,end)，X→dump 0）；
                 其余 7 颗全域 == 标记图案/全零（smc_id_en 泄漏检查：en≠0 时其他 SMC
                 会以 X 数据写窗口 → dump 0 ≠ 标记 → RED）
    A5 完成通知  sim.log 有 `batch interrupt`/`smi_iq_done` 正向标记 且
                 无 `smi_wait_and_clear_intr timeout`（双保险：RUN 的 ^[ERR] 判定之外）
    A6 UR 无损   ur_result_smc_0_bank{0..7}.hex 逐行 == ur_data/bank{0..7}.hex
                 （STB 只读 UR；同时堵 compare_ur_to_golden 无 golden 判 SKIP 的假绿通道）

用法：
    python3 verification_v2/tools/hbm_window_compare.py <case_dir>
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stb_solo_case_gen import (ZERO_LINE, mark_pattern_line,  # noqa: E402
                               preload_pattern_line, ur_pattern_line)

HBM_DUMP_LINES = 1048576     # ram65536x128 mem[0:1048575]，TB 全量 dump（X→0）


def _read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


class Reporter:
    def __init__(self) -> None:
        self.fails: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{('：' + detail) if detail else ''}")
        if not ok:
            self.fails.append(name)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    case_dir = os.path.abspath(sys.argv[1])
    with open(os.path.join(case_dir, "hbm_expect.json"), encoding="utf-8") as f:
        exp = json.load(f)
    if exp.get("schema") != "m3lite.stb_solo_expect/v1":
        print(f"❌ hbm_expect.json schema 不识别：{exp.get('schema')}")
        return 2

    win0 = exp["window"]["byte_base"] // 16          # 窗口起始行（mem 行 = 字节地址>>4）
    win_lines = exp["window"]["lines"]
    preload_n = exp["preload"]["lines"]
    mark_n = exp["mark"]["lines"]
    smc_count = exp["smc_count"]
    case = exp["case"]
    sandbox = os.path.dirname(os.path.dirname(os.path.dirname(case_dir)))
    sim_log = os.path.join(sandbox, "arith_regression", "test_log", "multi_stage",
                           "multi_inst_stage", case, "sim.log")

    rep = Reporter()
    print(f"[HBM-CMP] case={case} 窗口行 [{win0},{win0 + win_lines}) 共 {win_lines} 行")

    # ── A1/A2/A3/A4 目标颗 HBM dump ─────────────────────────────────────────
    dump0 = os.path.join(case_dir, "smc_0", "hbm_result_smc_0.hex")
    if not os.path.exists(dump0):
        print(f"❌ 缺证据：{dump0} 不存在（TB 未 dump？case 没跑？）")
        return 1
    rows = _read_lines(dump0)
    rep.check("A0 dump 行数", len(rows) == HBM_DUMP_LINES, f"{len(rows)} 行")

    bad_in = [k for k in range(win_lines) if rows[win0 + k] != ur_pattern_line(k)]
    rep.check("A1 窗口数据==UR 图案", not bad_in,
              "全对" if not bad_in else f"{len(bad_in)}/{win_lines} 行错，首错行 {win0 + bad_in[0]}")

    bad_pre = [k for k in range(preload_n)
               if not (win0 <= k < win0 + win_lines) and rows[k] != preload_pattern_line(k)]
    rep.check("A4a 预载区窗口外不变", not bad_pre,
              "全对" if not bad_pre else f"{len(bad_pre)} 行被改，首行 {bad_pre[0]}")

    bad_tail = None
    for k in range(preload_n, len(rows)):
        if rows[k] != ZERO_LINE:
            bad_tail = k
            break
    rep.check("A4b 未预载区全零（X→dump 0）", bad_tail is None,
              "全零" if bad_tail is None else f"行 {bad_tail} 非零：{rows[bad_tail][:16]}…")

    # ── A4c 其余颗 en 泄漏检查 ───────────────────────────────────────────────
    for smc in range(1, smc_count):
        p = os.path.join(case_dir, f"smc_{smc}", f"hbm_result_smc_{smc}.hex")
        if not os.path.exists(p):
            rep.check(f"A4c smc_{smc} dump 存在", False, f"{p} 缺失")
            continue
        r = _read_lines(p)
        leak = next((k for k in range(min(mark_n, len(r)))
                     if r[k] != mark_pattern_line(k)), None)
        tail = next((k for k in range(mark_n, len(r)) if r[k] != ZERO_LINE), None)
        rep.check(f"A4c smc_{smc} 未被写（en=0 门控）", leak is None and tail is None,
                  "未被写" if leak is None and tail is None
                  else f"疑似泄漏：标记区首异行 {leak}，尾区首异行 {tail}")

    # ── A5 完成通知 ─────────────────────────────────────────────────────────
    if not os.path.exists(sim_log):
        rep.check("A5 sim.log 存在", False, sim_log)
    else:
        with open(sim_log, encoding="utf-8", errors="replace") as f:
            log = f.read()
        has_intr = ("[TB_MULTI_STAGE] batch interrupt" in log
                    or "[TB_MULTI_STAGE] smi_iq_done" in log)
        has_timeout = "smi_wait_and_clear_intr timeout" in log
        rep.check("A5a 完成中断到达", has_intr)
        rep.check("A5b 无阶段超时", not has_timeout)

    # ── A6 UR 无损（按位）────────────────────────────────────────────────────
    for b in range(8):
        want_p = os.path.join(case_dir, "smc_0", "ur_data", f"bank{b}.hex")
        got_p = os.path.join(case_dir, "smc_0", f"ur_result_smc_0_bank{b}.hex")
        if not os.path.exists(got_p):
            rep.check(f"A6 bank{b} ur_result 存在", False, got_p)
            continue
        want, got = _read_lines(want_p), _read_lines(got_p)
        diff = next((k for k in range(min(len(want), len(got))) if want[k] != got[k]), None)
        rep.check(f"A6 bank{b} UR 无损", diff is None and len(want) == len(got),
                  "逐行一致" if diff is None else f"首异行 {diff}")

    n = len(rep.fails)
    print(f"\n{'✅ HBM 窗口比对全过' if n == 0 else f'❌ {n} 项断言失败：{rep.fails}'}")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
