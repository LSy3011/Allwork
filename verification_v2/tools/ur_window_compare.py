#!/usr/bin/env python3
"""ur_window_compare —— ADR-059 v2 / M3-lite 门②：LDB 落位 UR 的六项断言比对。

输入：case 目录（含 ldb_expect.json）+ TB dump（ur_result_smc_*_bank*.hex /
hbm_result_smc_0.hex）+ ur_write_trace.log + sim.log。任一断言失败即 RED、退出码 1。

断言（对应用户门②判据逐字映射；B4c/B5 按 2026-10-06 修正②③重写）：
    B1 落位数据  ur_result_smc_0 目标 bank 窗口行 == 源图案（bit-exact）
    B2 落位地址  窗口两端紧邻 UR 行保持 sentinel
    B3 数量      目标 bank 异变行集合 == 窗口集合（恰好 total 行，位置+数量同证）
    B4 非目标区  B4a 目标 bank 窗口外 == sentinel；B4b 其余 7 bank == sentinel；
                 B4c smc_1..7 全 8 bank == 生成器预置 sentinel′（先验期望，非首跑反钉；
                 装载生效与无泄漏同证）+ ur_write_trace 中 smc≠0 条目数 == 0
    B5 完成时序  B5a sim.log 双通道标记存在且无 timeout；
                 B5b trace 实际写次数：条目恰 total 条，(addr,wdata) 与窗口期望一一对应
                 （独立于 B3 终态行数）；
                 B5c 精确锚点 sim.log `[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle N`
                 （TB 对 DUT 内部 intr_ro 的上升沿探针，smi_ctrl_regs.v:121——
                 batch_done 同拍事件，双完成通道同源；L1 复跑实测轮询锚点不确定窗
                 达 29 拍以上，MARGIN 方案不可区分，按 ADR-059 R5 预案改为精确锚点）：
                 单 stage 用例要求恰 1 条；max(写 cycle) < N（严格，同沿歧义响亮 RED）；
                 全 trace 无 cycle ≥ N 的写；与 stage_0 done 清除点交叉一致；gap 入档
    B6 源区无损  hbm_result_smc_0 全域 == 预载成分（窗口=src 图案，其余=底图/零）

用法：
    python3 verification_v2/tools/ur_window_compare.py <case_dir>
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldb_review_case_gen import (HBM_MARK_LINES, HBM_PRELOAD_LINES, UR_BANK_DEPTH,  # noqa: E402
                                 ZERO_LINE, mark_pattern_line, preload_pattern_line,
                                 sentinel_aux_line, sentinel_line, src_pattern_line)

HBM_DUMP_LINES = 1048576     # ram65536x128 mem[0:1048575]，TB 全量 dump（X→0）

# TB 精确通知沿探针（2026-10-06 新增，additive 纯观测）：intr_ro 上升沿
# = intid_hw_r 自增生效 = batch_done 同拍事件（smi_ctrl_regs.v:121/:281-285，
# smi_iq_done_r 同源 :292-293 → 单锚点覆盖双完成通道）。打印于检测到沿的
# 那个 posedge，与 ur_write_trace 共用 iq_probe_cycle 计数域。
INTR_ASSERT_RE = re.compile(r"\[TB_MULTI_STAGE\] INTR_ASSERT @probe_cycle (\d+)")
# TB :2393「stage_0 done (intr cleared) @time %0d @probe_cycle %0d」（清除点，作交叉一致性锚）
STAGE_DONE_RE = re.compile(r"stage_0 done \(intr cleared\) @time \d+ @probe_cycle (\d+)")


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


def parse_ur_write_trace(path: str) -> list[dict]:
    """ur_write_trace.log → [{cycle, smc, bank, we, addr, wbe, wdata}]。

    TB 格式（:2460 注释 / :2640 附近 fdisplay）：
    `# cycle smc bank we addr wbe wdata`，addr 为 %0d 十进制行号，wbe %04h，wdata %032h。
    """
    entries = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 7:
                raise ValueError(f"ur_write_trace.log:{ln} 字段数 {len(parts)} != 7：{line!r}")
            entries.append({
                "cycle": int(parts[0]), "smc": int(parts[1]), "bank": int(parts[2]),
                "we": int(parts[3]), "addr": int(parts[4]),
                "wbe": parts[5].lower(), "wdata": parts[6].lower(),
            })
    return entries


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    case_dir = os.path.abspath(sys.argv[1])
    with open(os.path.join(case_dir, "ldb_expect.json"), encoding="utf-8") as f:
        exp = json.load(f)
    if exp.get("schema") != "m3lite.ldb_review_expect/v1":
        print(f"❌ ldb_expect.json schema 不识别：{exp.get('schema')}")
        return 2

    bank = exp["ur_window"]["bank"]
    ur_line0 = exp["ur_window"]["byte_offset"] // 16
    total = exp["ur_window"]["lines"]
    win0 = exp["hbm_source"]["byte_base"] // 16     # HBM 源窗口起始行
    preload_n = exp["preload"]["lines"]
    mark_n = exp["mark"]["lines"]
    smc_count = exp["smc_count"]
    case = exp["case"]
    sandbox = os.path.dirname(os.path.dirname(os.path.dirname(case_dir)))
    sim_log = os.path.join(sandbox, "arith_regression", "test_log", "multi_stage",
                           "multi_inst_stage", case, "sim.log")
    trace_path = os.path.join(case_dir, "ur_write_trace.log")

    rep = Reporter()
    print(f"[UR-CMP] case={case} 目标 bank{bank} 窗口行 [{ur_line0},{ur_line0 + total}) "
          f"共 {total} 行（源 HBM 行 [{win0},{win0 + total})）")

    # ── B1/B2/B3/B4a/B4b：smc_0 UR dump ─────────────────────────────────────
    dump_banks: list[list[str] | None] = []
    for b in range(8):
        p = os.path.join(case_dir, "smc_0", f"ur_result_smc_0_bank{b}.hex")
        if not os.path.exists(p):
            rep.check(f"B0 bank{b} ur_result 存在", False, p)
            dump_banks.append(None)
            continue
        rows = _read_lines(p)
        rep.check(f"B0 bank{b} dump 行数", len(rows) == UR_BANK_DEPTH, f"{len(rows)} 行")
        dump_banks.append(rows)

    tgt = dump_banks[bank]
    if tgt is not None and len(tgt) == UR_BANK_DEPTH:
        bad_in = [k for k in range(total) if tgt[ur_line0 + k] != src_pattern_line(k)]
        rep.check("B1 窗口数据==源图案", not bad_in,
                  "全对" if not bad_in else f"{len(bad_in)}/{total} 行错，首错行 {ur_line0 + bad_in[0]}")

        left_ok = ur_line0 == 0 or tgt[ur_line0 - 1] == sentinel_line(bank, ur_line0 - 1)
        right_ok = (ur_line0 + total >= UR_BANK_DEPTH
                    or tgt[ur_line0 + total] == sentinel_line(bank, ur_line0 + total))
        rep.check("B2 窗口两端紧邻行保持 sentinel", left_ok and right_ok,
                  f"左{'无邻居' if ur_line0 == 0 else ('OK' if left_ok else '被染')}"
                  f"/右{'无邻居' if ur_line0 + total >= UR_BANK_DEPTH else ('OK' if right_ok else '被染')}")

        changed = [k for k in range(UR_BANK_DEPTH) if tgt[k] != sentinel_line(bank, k)]
        want_changed = list(range(ur_line0, ur_line0 + total))
        rep.check("B3 异变行集合==窗口（数量+位置）", changed == want_changed,
                  f"恰 {total} 行" if changed == want_changed
                  else f"异变 {len(changed)} 行（期望 {total}），首异 {changed[:3]} vs {want_changed[:3]}")

        bad_out = [k for k in range(UR_BANK_DEPTH)
                   if not (ur_line0 <= k < ur_line0 + total)
                   and tgt[k] != sentinel_line(bank, k)]
        rep.check("B4a 目标 bank 窗口外==sentinel", not bad_out,
                  "全对" if not bad_out else f"{len(bad_out)} 行被染，首行 {bad_out[0]}")

    for b in range(8):
        if b == bank or dump_banks[b] is None or len(dump_banks[b]) != UR_BANK_DEPTH:
            continue
        bad = next((k for k in range(UR_BANK_DEPTH)
                    if dump_banks[b][k] != sentinel_line(b, k)), None)
        rep.check(f"B4b smc_0 bank{b}==sentinel", bad is None,
                  "全对" if bad is None else f"首异行 {bad}")

    # ── B4c：smc_1..7 独立 sentinel′（修正②）────────────────────────────────
    for i in range(1, smc_count):
        for b in range(8):
            p = os.path.join(case_dir, f"smc_{i}", f"ur_result_smc_{i}_bank{b}.hex")
            if not os.path.exists(p):
                rep.check(f"B4c smc_{i} bank{b} dump 存在", False, p)
                continue
            rows = _read_lines(p)
            bad = next((k for k in range(min(UR_BANK_DEPTH, len(rows)))
                        if rows[k] != sentinel_aux_line(i, b, k)), None)
            rep.check(f"B4c smc_{i} bank{b}==sentinel′", bad is None and len(rows) == UR_BANK_DEPTH,
                      "装载生效且未被写" if bad is None else f"首异行 {bad}（装载失败或泄漏写）")

    # ── B5：完成通知与时序（修正③）──────────────────────────────────────────
    log = ""
    if not os.path.exists(sim_log):
        rep.check("B5a sim.log 存在", False, sim_log)
    else:
        with open(sim_log, encoding="utf-8", errors="replace") as f:
            log = f.read()
        has_intr = ("[TB_MULTI_STAGE] batch interrupt" in log
                    or "[TB_MULTI_STAGE] smi_iq_done" in log)
        has_timeout = "smi_wait_and_clear_intr timeout" in log
        rep.check("B5a 完成通知到达", has_intr)
        rep.check("B5a 无阶段超时", not has_timeout)

    entries: list[dict] = []
    if not os.path.exists(trace_path):
        rep.check("B5 ur_write_trace.log 存在", False, trace_path)
    else:
        try:
            entries = parse_ur_write_trace(trace_path)
        except ValueError as e:
            rep.check("B5 trace 解析", False, str(e))

    if entries or os.path.exists(trace_path):
        aux = [e for e in entries if e["smc"] != 0]
        rep.check("B4c' trace 零 smc≠0 写（en 泄漏写级佐证）", not aux,
                  "零" if not aux else f"{len(aux)} 条，首条 {aux[0]}")

        tgt_entries = [e for e in entries if e["smc"] == 0 and e["bank"] == bank]
        addr_map: dict[int, list[dict]] = {}
        for e in tgt_entries:
            addr_map.setdefault(e["addr"], []).append(e)
        want_addrs = {ur_line0 + k: src_pattern_line(k) for k in range(total)}
        count_ok = len(tgt_entries) == total
        set_ok = set(addr_map) == set(want_addrs)
        once_ok = all(len(v) == 1 for v in addr_map.values())
        data_ok = all(addr_map[a][0]["wdata"] == w for a, w in want_addrs.items()
                      if a in addr_map and len(addr_map[a]) == 1)
        rep.check("B5b 实际写次数==total 且地址集合==窗口", count_ok and set_ok and once_ok,
                  f"{len(tgt_entries)} 条 / {total}" if count_ok and set_ok and once_ok
                  else f"条目 {len(tgt_entries)}（期望 {total}），地址集吻合={set_ok}，单次写={once_ok}")
        rep.check("B5b' 每条写数据==源图案（commit 级）", data_ok and set_ok and once_ok,
                  "全对" if data_ok and set_ok and once_ok else "存在 wdata 与源图案不符的写")
        off = [e for e in entries if e["smc"] == 0 and e["bank"] != bank]
        rep.check("B4b' trace 零 smc_0 非目标 bank 写", not off,
                  "零" if not off else f"{len(off)} 条，首条 {off[0]}")

        intr_hits = INTR_ASSERT_RE.findall(log)
        if len(intr_hits) != 1:
            rep.check("B5c 精确通知锚点（INTR_ASSERT 唯一）", False,
                      f"sim.log 中 INTR_ASSERT 行数={len(intr_hits)}（单 stage 用例期望恰 1）"
                      "——缺证据即 RED")
        elif entries:
            n_assert = int(intr_hits[0])
            max_wr = max(e["cycle"] for e in entries)
            late = [e for e in entries if e["cycle"] >= n_assert]
            gap = n_assert - max_wr
            # 精确锚点（见 INTR_ASSERT_RE 注释）：写计数域与锚点同域。严格不等——
            # max_wr == n_assert 为同沿歧义，无法排除「通知不晚于末写」→ 响亮 RED；
            # 残留写判据同步收紧为 cycle >= n_assert。
            rep.check("B5c 末次写严格先于通知（精确锚点）", max_wr < n_assert,
                      f"max写@{max_wr} INTR_ASSERT@{n_assert} gap={gap}"
                      + ("" if max_wr < n_assert else " → 通知未晚于末次写"))
            rep.check("B5c 通知后零残留写", not late,
                      "零" if not late else f"{len(late)} 条不早于通知沿，首条 {late[0]}")
            m = STAGE_DONE_RE.search(log)
            rep.check("B5c 锚点一致性（INTR_ASSERT ≤ stage_0 done 清除点）",
                      m is not None and n_assert <= int(m.group(1)),
                      f"INTR_ASSERT@{n_assert} vs done@{m.group(1) if m else '缺失'}")
        else:
            rep.check("B5c trace 非空", False, "trace 无任何写条目（LDB 没落写？）")

    # ── B6 源区无损（HBM 全域 == 预载成分）──────────────────────────────────
    hbm0 = os.path.join(case_dir, "smc_0", "hbm_result_smc_0.hex")
    if not os.path.exists(hbm0):
        rep.check("B6 hbm_result_smc_0 存在", False, hbm0)
    else:
        rows = _read_lines(hbm0)
        rep.check("B6 dump 行数", len(rows) == HBM_DUMP_LINES, f"{len(rows)} 行")
        bad = None
        for k in range(min(preload_n, len(rows))):
            want = src_pattern_line(k - win0) if win0 <= k < win0 + total else preload_pattern_line(k)
            if rows[k] != want:
                bad = k
                break
        tail = next((k for k in range(preload_n, len(rows)) if rows[k] != ZERO_LINE), None)
        rep.check("B6 HBM 全域==预载成分（源区无损）", bad is None and tail is None,
                  "全对" if bad is None and tail is None
                  else f"首异行 {bad if bad is not None else tail}")

    n = len(rep.fails)
    print(f"\n{'✅ UR 窗口比对全过' if n == 0 else f'❌ {n} 项断言失败：{rep.fails}'}")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
