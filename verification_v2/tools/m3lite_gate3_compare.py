#!/usr/bin/env python3
"""m3lite_gate3_compare —— ADR-060 / M3-lite 门③步骤③：LOAD→COMPUTE 最小实验比对器。

消费 <case_dir>（m3lite_gate3_case_gen 物化 + VCS 跑后），断言：

  C0 证据前提  sim.log 存在且按模式具备标记：
               positive=恰 1 组 [GATE3][BARRIER] enter/release（计数==g3_expect.barrier）、
                        无 BARRIER ERR / 无 smi_wait_and_clear_intr timeout（末段假绿通道
                        TB:830-834 必须显式排除）/ INTR_ASSERT 恰 2 条（两段各一）；
               negative=并段形态（expect.negative_control=="merged_single_stage"）：
                        无任何 G3 标记、INTR_ASSERT 恰 1 条（单 batch）、
                        GEN stage_0 清单==2+N 行且无 stage_1 目录；
                        legacy BYPASS 形态（第 4 轮档案复现用）：恰 1 条
                        [GATE3][BYPASS] raw intr edge 且无 BARRIER 标记。
               GEN 完整性：stage_0 data_list_inst_subq0.txt==2 行、stage_1==12 行
               （GEN :188 未匹配静默跳过——以清单行数为正面取证，不查 stdout）。
               ur_write_trace.log / ur_read_trace.log 存在且非空。
  C1 数据      smc_0 bank2 dump 窗口 == 独立期望乘积（从 case 内冻结
               memory_init_snapshot/memory_init1.hex 重算，struct fp32）。
  C2 哨兵      bank0/1 窗口 == HBM 源行（LOAD 落位）；bank0/1/2 窗外 == sentinel；
               bank3..7 全 sentinel；smc_1..7 全 sentinel′（修正② lineage）。
  C3 时序      min(读拍[smc0,port*,bank∈{0,1},addr∈[0,words)])
               > max(写拍[smc0,bank∈{0,1},addr∈[0,words)])（严格不等）；
               positive 模式追加：min(读拍) > barrier release 拍。
  C4 写账      bank0/1 窗口每址恰 1 写且 wdata==源行；bank2 窗口每址 ≥1 写且
               末写 wdata==期望乘积；窗外/旁 bank/smc≠0 零写（bank2 窗外写=RED，
               窗口内多余写=WARN 留痕——ur-w0 预热形态属结构已知）。
  C5 源无损    hbm_result_smc_0.hex 前 len(快照) 行与 memory_init_snapshot/
               memory_init1.hex 逐行一致（步骤③无 STORE，预载区任何变化=RED）。
               快照仅覆盖 v3 生成区（如 528 行）；dump 更长尾部=从未 $readmemh
               加载区（X→dump 置 0），不可判读，仅 WARN 留痕不判。

negative（负向对照）语义反转：整体 PASS 当且仅当
  (a) C0 负控形态证据在；且 (b) C1 或 C3 至少其一 RED（证伪路径确实被踩中）。
  若负控全绿 → 报「负控未证活」整体 RED（不确定 ≠ 通过）。
  2026-10-07 第 4 轮实证 + trace 定量（m3lite_g3_lc_w16 实物）：
  - BYPASS（裸 intr 沿放行）太弱：裸沿 ~@300 反在末写@259 之后；段间 IQ_EN
    同步 ~588 拍恒在（stage_1 BEGIN@338→取指@926），任何"改放行时机"的负控
    首读都 ≥~700，结构性撞不上写窗。trigger→读仅 5 拍 vs trigger→首写 21 拍，
    快慢差在取数路径本身（寄存器读 vs AXI 取 HBM），是物理的。
  - 现行负控 = 并段单 trigger（expect.negative_control=="merged_single_stage"，
    无需 TB plusarg）：compute 首读 ≈T+5 ≺ LDB 首写 ≈T+21、末写 ≈T+62，
    读恒先于写 → C1/C3/C4(乘积账) 必红；C2/C5 保持绿——LOAD 本身无恙，
    红纯因竞态，正是负控应有的证伪剖面。

退出码：0=PASS（按模式语义），1=RED，2=用法/产物缺失。

── 步骤④ STORE 扩展（ADR-060 §6.6；expect 含 "store" 键时启用）────────────
  C3s 时序    STB URA 首读拍 > bank2 末写拍（strict）；positive 追加
              > WBARRIER release 拍。
  C5'         STORE 开启时 HBM 输出窗豁免「==快照」（窗外仍逐行一致）。
  C6 写账     hbm_write_trace.log（RAM 写口落实级，8 SMC 全探）：窗口址集恰全、
              每址恰 1 写、dina==独立期望乘积、wea==FFFF、零非目标写、
              总数==窗口行数；重复/遗漏/非目标三类单列明细。
  C7 时序     四事件时间轴入报告（issue q24/q25、CRD_STB、CRD_STBAXI、窗口
              首/末写、B 计数、S4 release、DUMP）；RED：release/DUMP 不严格
              晚于末写、DUMP 早于末 B、B 计数≠期望、信用缺失；信用≺写落位
              gap 记 WARN 不判 RED（结构性预期，ADR-060 §6.2）。
  C8 数据     hbm_result 输出窗 == 独立期望乘积（完整链路逐位比对）。
  负控（merged-3）：白名单扩 ("C3s","C6 ","C8 ")；证活判据扩为
              C1/C3/C3s/C6/C8 任一 RED。
  --mode fatal_expected（超时注入验收，用户要求⑤）：仅读 sim.log——
              --expect-timeout 指定的 [GATE4][*][ERR] TIMEOUT 标记存在，
              且其后无 [GATE4][DUMP]（未 dump/比对/结束）= PASS。
  故障注入（expect.fault_injection）：positive 全断言 + BP 标记在 +
              S4 waited ≥ min_waited + 精确一致性（waited∈[bp_release
              −s4_enter−2, +50]，证明 waited 确由注入造成）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, "src"))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import ldb_review_case_gen as g2  # noqa: E402
import m3lite_gate3_case_gen as g3  # noqa: E402

BARRIER_ENTER_RE = re.compile(
    r"\[GATE3\]\[BARRIER\] enter @probe_cycle (\d+): expect ldb_done=(\d+) ldbaxi_done=(\d+)")
BARRIER_RELEASE_RE = re.compile(
    r"\[GATE3\]\[BARRIER\] release @probe_cycle (\d+): ldb_done=(\d+) ldbaxi_done=(\d+)"
    r" \(waited (\d+) cycles\)")
BYPASS_RE = re.compile(r"\[GATE3\]\[BYPASS\] raw intr edge @probe_cycle (\d+)")
INTR_ASSERT_RE = re.compile(r"\[TB_MULTI_STAGE\] INTR_ASSERT @probe_cycle (\d+)")
# [GATE4] 步骤④ STORE（ADR-060 §6.6）
WBARRIER_ENTER_RE = re.compile(
    r"\[GATE4\]\[WBARRIER\] enter @probe_cycle (\d+): expect bank=(\d+) writes=(\d+)")
WBARRIER_RELEASE_RE = re.compile(
    r"\[GATE4\]\[WBARRIER\] release @probe_cycle (\d+): landed (\d+) \(waited (\d+) cycles\)")
S4_ENTER_RE = re.compile(
    r"\[GATE4\]\[S4BARRIER\] enter @probe_cycle (\d+): expect hbm_writes=(\d+) bresp=(\d+)")
S4_RELEASE_RE = re.compile(
    r"\[GATE4\]\[S4BARRIER\] release @probe_cycle (\d+): hbm_writes=(\d+) bresp=(\d+)"
    r" \(waited (\d+) cycles; crd_stb=(\d+) crd_stbaxi=(\d+) record-only\)")
DUMP_RE = re.compile(r"\[GATE4\]\[DUMP\] begin @probe_cycle (\d+)")
CRD_STB_RE = re.compile(r"\[GATE4\]\[CRD_STB\] @probe_cycle (\d+)")
CRD_STBAXI_RE = re.compile(r"\[GATE4\]\[CRD_STBAXI\] @probe_cycle (\d+)")
BRESP_RE = re.compile(r"\[GATE4\]\[HBM_BRESP\] @probe_cycle (\d+)")
BP_INJECT_RE = re.compile(r"\[GATE4\]\[BP\] inject @probe_cycle (\d+)")
BP_RELEASE_RE = re.compile(r"\[GATE4\]\[BP\] release @probe_cycle (\d+)")


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.red = 0
        self.warn = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        tag = "PASS" if ok else "RED "
        self.lines.append(f"  [{tag}] {name}" + (f" —— {detail}" if detail else ""))
        if not ok:
            self.red += 1
        return ok

    def note(self, name: str, detail: str) -> None:
        self.warn += 1
        self.lines.append(f"  [WARN] {name} —— {detail}")


def tb_provenance(sandbox: str, sim_sha: "str | None", rep: Report) -> dict:
    """任务 1（用户 2026-10-07）：明示「测试时 TB」与「当前 TB」的差异。
    sim_sha = 驱动在 sim 时刻记录的 TB sha256；此处现场重算比对时刻指纹。
    差异记 WARN 如实披露（不判 RED——归档复比对场景 TB 可合法前进，
    解读旧报告以 sim 时刻指纹为准）。"""
    tb = os.path.join(sandbox, "sim_multi_stage", "tb_aiacc_top_v04_f_multi_stage.v")
    now = None
    if os.path.isfile(tb):
        h = hashlib.sha256()
        with open(tb, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        now = h.hexdigest()
    prov = {"tb_path": tb, "tb_sha256_sim": sim_sha,
            "tb_sha256_compare_time": now,
            "tb_match": (sim_sha == now) if (sim_sha and now) else None}
    if sim_sha and now and sim_sha != now:
        rep.note("TB 指纹差异（测试时≠比对时，如实披露不判 RED）",
                 f"sim={sim_sha[:16]}… now={now[:16]}…——解读结果以 sim 时刻指纹为准")
    return prov


def read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return [ln.strip() for ln in f if ln.strip()]


def load_trace(path: str, nfields: int):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != nfields:
                raise ValueError(f"{os.path.basename(path)}:{ln} 字段数 {len(parts)} != {nfields}")
            rows.append(parts)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case_dir")
    ap.add_argument("--mode", choices=["positive", "negative", "fatal_expected"],
                    default="positive")
    ap.add_argument("--expect-timeout", choices=["wbarrier", "s4barrier"], default=None,
                    help="fatal_expected 模式必填：哪道屏障应超时")
    ap.add_argument("--sandbox", default=os.path.expanduser("~/u280_sim_v422"))
    ap.add_argument("--tb-sha256", default=None,
                    help="测试时刻 TB sha256（驱动在 sim 前记录）；与比对时刻现场"
                         "重算值并列写入报告 provenance，差异记 WARN 如实披露")
    args = ap.parse_args()
    case_dir = os.path.abspath(args.case_dir)
    case = os.path.basename(case_dir)
    rep = Report()

    # ── fatal_expected：仅读 sim.log，断言「超时会失败退出」（用户要求⑤）───
    if args.mode == "fatal_expected":
        if args.expect_timeout is None:
            print("[G3-CMP] fatal_expected 模式须指定 --expect-timeout wbarrier|s4barrier")
            return 2
        sim_log_fe = os.path.join(args.sandbox, "arith_regression", "test_log",
                                  "multi_stage", "multi_inst_stage", case, "sim.log")
        if not os.path.isfile(sim_log_fe):
            print(f"[G3-CMP] fatal_expected：缺 sim.log：{sim_log_fe}")
            return 2
        with open(sim_log_fe, encoding="utf-8", errors="replace") as f:
            log_fe = f.read()
        tag = {"wbarrier": "[GATE4][WBARRIER][ERR] TIMEOUT",
               "s4barrier": "[GATE4][S4BARRIER][ERR] TIMEOUT"}[args.expect_timeout]
        pos = log_fe.find(tag)
        rep.check(f"fatal_expected {args.expect_timeout} TIMEOUT 标记存在", pos >= 0)
        dump_after = ("[GATE4][DUMP]" in log_fe[pos:]) if pos >= 0 else ("[GATE4][DUMP]" in log_fe)
        rep.check("fatal_expected TIMEOUT 后无 [GATE4][DUMP]（未 dump/比对/结束）",
                  not dump_after)
        prov = tb_provenance(args.sandbox, args.tb_sha256, rep)
        print(f"[G3-CMP] case={case} mode=fatal_expected expect={args.expect_timeout}")
        for ln in rep.lines:
            print(ln)
        verdict = rep.red == 0
        print(f"[G3-CMP] 总判：{'PASS（超时确实失败退出）' if verdict else 'RED'}"
              f"（red={rep.red} warn={rep.warn}）")
        with open(os.path.join(case_dir, "g3_report_fatal_expected.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"case": case, "mode": "fatal_expected",
                       "expect_timeout": args.expect_timeout,
                       "verdict": "PASS" if verdict else "RED",
                       "red": rep.red, "warn": rep.warn, "lines": rep.lines,
                       "provenance": prov},
                      f, indent=2, ensure_ascii=False)
        return 0 if verdict else 1

    # ── 期望重算（独立链：冻结快照 → struct fp32）──────────────────────────
    exp_path = os.path.join(case_dir, "g3_expect.json")
    snap1 = os.path.join(case_dir, "memory_init_snapshot", "memory_init1.hex")
    if not (os.path.isfile(exp_path) and os.path.isfile(snap1)):
        print(f"[G3-CMP] 缺 g3_expect.json 或冻结快照：{case_dir}")
        return 2
    with open(exp_path, encoding="utf-8") as f:
        expect = json.load(f)
    if expect.get("schema") != "m3lite.gate3_expect/v1":
        print(f"[G3-CMP] g3_expect schema 不识：{expect.get('schema')}")
        return 2
    words = int(expect["words"])
    exp = g3.compute_expectations(snap1, words)
    win = expect["ur_windows"]
    ib, sb, ob = win["input"]["bank"], win["scalar"]["bank"], win["output"]["bank"]
    store = expect.get("store")  # 步骤④：含 "store" 键 = 3 段形态（ADR-060 §6.6）
    fi = expect.get("fault_injection")

    # ── C0 证据前提 ────────────────────────────────────────────────────────
    neg_kind = expect.get("negative_control")  # "merged_single_stage" | None(legacy bypass)
    merged_neg = (args.mode == "negative" and neg_kind == "merged_single_stage")
    sim_log = os.path.join(args.sandbox, "arith_regression", "test_log",
                           "multi_stage", "multi_inst_stage", case, "sim.log")
    log = ""
    if rep.check("C0 sim.log 存在", os.path.isfile(sim_log), sim_log):
        with open(sim_log, encoding="utf-8", errors="replace") as f:
            log = f.read()
    barrier_release_cycle = None
    if log:
        rep.check("C0 无 smi_wait_and_clear_intr timeout（末段假绿通道）",
                  "smi_wait_and_clear_intr timeout" not in log)
        rep.check("C0 无 G3 BARRIER/BYPASS ERR",
                  "[GATE3][BARRIER][ERR]" not in log and "[GATE3][BYPASS][ERR]" not in log)
        rep.check("C0 无 GATE4 WBARRIER/S4BARRIER ERR",
                  "[GATE4][WBARRIER][ERR]" not in log and "[GATE4][S4BARRIER][ERR]" not in log)
        intr = INTR_ASSERT_RE.findall(log)
        exp_intr = 1 if merged_neg else (3 if store else 2)
        rep.check(f"C0 INTR_ASSERT 恰 {exp_intr} 条"
                  + ("（并段单 batch）" if merged_neg
                     else ("（三段各一）" if store else "（两段各一）")),
                  len(intr) == exp_intr, f"实际 {len(intr)} 条")
        enters = BARRIER_ENTER_RE.findall(log)
        releases = BARRIER_RELEASE_RE.findall(log)
        bypasses = BYPASS_RE.findall(log)
        if args.mode == "positive":
            # 屏障挂点 = 每段间转移各一次（TB 主循环逐转移调用）：3 段计划
            # → 2 对 enter/release（stage_0→1 真门 + stage_1→2 直穿），
            # 2 段计划 → 1 对。2026-10-07 首轮实测 enter=2/release=2
            # （@338 与 @1021 分属两个转移）为确定性 TB 行为；此前
            # "恰各 1" 是步骤③两段世界的遗留 spec bug——按转移数断言。
            exp_pairs = 2 if store else 1
            ok = (len(enters) == exp_pairs and len(releases) == exp_pairs
                  and not bypasses)
            rep.check(f"C0 屏障 enter/release 恰各 {exp_pairs}（=段间转移数）"
                      "且无负控标记", ok,
                      f"enter={len(enters)} release={len(releases)} bypass={len(bypasses)}")
            if releases:
                barrier_release_cycle = int(releases[0][0])  # 首次 release=LOAD 门（C3 用）
                b = expect["barrier"]
                all_cnt = all(int(r[1]) == b["exp_ldb_done"]
                              and int(r[2]) == b["exp_ldbaxi_done"] for r in releases)
                rep.check("C0 屏障计数==g3_expect（全部 release）", all_cnt,
                          " | ".join(f"#{i} ldb={r[1]}/{b['exp_ldb_done']} "
                                     f"ldbaxi={r[2]}/{b['exp_ldbaxi_done']}"
                                     for i, r in enumerate(releases)))
                # 第 2+ 次 release 必须 waited==0：信用计数器跨段不清零，
                # 重入即满足直穿；waited>0 = 计数被意外复位或出现新信用源（异常）。
                for i, r in enumerate(releases[1:], start=2):
                    rep.check(f"C0 屏障第 {i} 次为直穿（waited==0，计数单调不清零）",
                              int(r[3]) == 0,
                              f"release#{i} @{r[0]} waited={r[3]}")
        elif merged_neg:
            ok = (not enters and not releases and not bypasses)
            rep.check("C0 并段负控：无任何 G3 标记（单 trigger 天然竞态）", ok,
                      f"enter={len(enters)} release={len(releases)} bypass={len(bypasses)}")
        else:
            ok = (len(bypasses) == 1 and not enters and not releases)
            rep.check("C0 BYPASS 标记恰 1 且无屏障标记（legacy 形态）", ok,
                      f"bypass={len(bypasses)} enter={len(enters)} release={len(releases)}")
        # [GATE4] 步骤④ 标记对（store 形态）：positive 恰各 1 组 + 计数回核；
        # merged-3 负控无任何 GATE4 标记（屏障 plusarg 不传）。
        wb_e = WBARRIER_ENTER_RE.findall(log)
        wb_r = WBARRIER_RELEASE_RE.findall(log)
        s4_e = S4_ENTER_RE.findall(log)
        s4_r = S4_RELEASE_RE.findall(log)
        dumps = DUMP_RE.findall(log)
        if store and args.mode == "positive":
            wbx, s4x = expect["wbarrier"], expect["s4barrier"]
            rep.check("C0 WBARRIER enter/release 恰各 1", len(wb_e) == 1 and len(wb_r) == 1,
                      f"enter={len(wb_e)} release={len(wb_r)}")
            rep.check("C0 S4BARRIER enter/release 恰各 1", len(s4_e) == 1 and len(s4_r) == 1,
                      f"enter={len(s4_e)} release={len(s4_r)}")
            rep.check("C0 [GATE4][DUMP] 恰 1（S4 门控后才开始 dump）",
                      len(dumps) == 1, f"实际 {len(dumps)}")
            if wb_r:
                cyc, landed, waited = int(wb_r[0][0]), int(wb_r[0][1]), int(wb_r[0][2])
                rep.check("C0 WBARRIER 落位数==g3_expect",
                          landed == wbx["writes"] == words,
                          f"landed={landed} expect={wbx['writes']}")
                # enter 在该段 trigger 时（写必未落位，掩码随 rst_n 清零）——
                # waited==0 意味着挂点/掩码失效（先验断言，非按输出调期望：
                # round5 实测 trigger→末写 ~31 拍，round6 实测 waited=30）。
                rep.check("C0 WBARRIER 实际托住（waited>0；==0=挂点失效或写早于 trigger）",
                          waited > 0, f"waited={waited}")
                rep.note("C0 WBARRIER 正向等待实测",
                         f"waited={waited} 拍 @release {cyc}（>0 = 屏障确实托住了"
                         f" intr 提前量——保持力正向证据）")
            if s4_r:
                cyc, hw, br, waited = (int(s4_r[0][0]), int(s4_r[0][1]),
                                       int(s4_r[0][2]), int(s4_r[0][3]))
                rep.check("C0 S4BARRIER 计数==g3_expect",
                          hw == s4x["writes"] == words and br == s4x["bresp"],
                          f"release hbm_writes={hw}/{s4x['writes']} bresp={br}/{s4x['bresp']}")
                rep.note("C0 S4BARRIER 正向等待实测", f"waited={waited} 拍 @release {cyc}")
                if fi is not None:
                    rep.check("C0 BP 保持力：S4 waited >= fault_injection.min_waited"
                              "（ADR-060 §6.5）",
                              waited >= fi["min_waited"],
                              f"waited={waited} min_waited={fi['min_waited']}")
            elif fi is not None:
                rep.check("C0 BP 保持力：S4 release 标记存在（否则 waited 无从核）", False)
            if fi is not None:
                bp_i = BP_INJECT_RE.findall(log)
                bp_r = BP_RELEASE_RE.findall(log)
                rep.check("C0 BP 注入标记 inject/release 恰各 1",
                          len(bp_i) == 1 and len(bp_r) == 1,
                          f"inject={len(bp_i)} release={len(bp_r)}")
                # 精确一致性（预注册公式，非按输出调期望）：B 通道反压下
                # bresp 握手必不早于 BP release；故 waited 必落在
                # [R-E-2, R-E+50]（R=bp release 拍，E=S4 enter 拍；
                # -2 = probe_cycle 显示粒度，+50 = 模型重驱动+轮询余量）。
                # 这一断言证明 waited 确实由注入造成，而非别的偶然延迟。
                if bp_r and s4_e and s4_r:
                    r_minus_e = int(bp_r[0]) - int(s4_e[0][0])
                    lo, hi = r_minus_e - 2, r_minus_e + 50
                    rep.check("C0 BP 精确性：waited∈[bp_release-s4_enter-2,+50]"
                              "（waited 确由注入造成）",
                              lo <= waited <= hi,
                              f"waited={waited} bp_release={bp_r[0]} "
                              f"s4_enter={s4_e[0][0]} → 区间[{lo},{hi}]")
        elif store and merged_neg:
            rep.check("C0 merged-3 负控：无任何 GATE4 屏障/DUMP 标记",
                      not wb_e and not wb_r and not s4_e and not s4_r and not dumps,
                      f"wb={len(wb_e)}/{len(wb_r)} s4={len(s4_e)}/{len(s4_r)} dump={len(dumps)}")
    # GEN 完整性（正面取证：清单行数，非"没看到跳过"；stage_1 期望数取自
    # g3_expect.compute_files——2026-10-07 教训：硬编码 12 曾漏 rtovr-16）
    n1 = len(expect.get("compute_files", []))
    if merged_neg:
        n0 = 2 + n1 + (2 if store else 0)  # 并段：LOAD 2 + compute N（+ STORE 2）
        plan = [(0, n0)]
        s1 = os.path.join(case_dir, "smi_iq_inst", "stage_1")
        rep.check("C0 并段 case 无 stage_1 目录（搜：smi_iq_inst/stage_1）",
                  not os.path.isdir(s1), f"检查路径 {s1}")
        if store:
            s2 = os.path.join(case_dir, "smi_iq_inst", "stage_2")
            rep.check("C0 merged-3 case 无 stage_2 目录（搜：smi_iq_inst/stage_2）",
                      not os.path.isdir(s2), f"检查路径 {s2}")
    else:
        plan = [(0, 2), (1, n1)] + ([(2, 2)] if store else [])
    for stage, n in plan:
        p = os.path.join(case_dir, "smi_iq_inst", f"stage_{stage}",
                         "data_list_inst_subq0.txt")
        cnt = len(read_lines(p)) if os.path.isfile(p) else -1
        rep.check(f"C0 GEN 清单 stage_{stage} 条目=={n}", cnt == n,
                  f"实际 {cnt}" + ("（文件缺失）" if cnt < 0 else ""))

    wr_path = os.path.join(case_dir, "ur_write_trace.log")
    rd_path = os.path.join(case_dir, "ur_read_trace.log")
    writes, reads = [], []
    if rep.check("C0 ur_write_trace.log 非空",
                 os.path.isfile(wr_path) and os.path.getsize(wr_path) > 40):
        writes = load_trace(wr_path, 7)
    if rep.check("C0 ur_read_trace.log 非空",
                 os.path.isfile(rd_path) and os.path.getsize(rd_path) > 40):
        reads = load_trace(rd_path, 5)

    # ── C1 数据：bank2 窗口 == 独立期望乘积 ────────────────────────────────
    def dump_rows(smc: int, bank: int):
        p = os.path.join(case_dir, f"smc_{smc}", f"ur_result_smc_{smc}_bank{bank}.hex")
        if not os.path.isfile(p):
            return None
        rows = read_lines(p)
        return [r.lower() for r in rows]

    c1_ok = True
    d = dump_rows(0, ob)
    if d is None:
        rep.check("C1 bank2 dump 存在", False)
        c1_ok = False
    else:
        if len(d) != g2.UR_BANK_DEPTH:
            rep.check("C1 bank2 dump 行数==256", False, f"实际 {len(d)}")
            c1_ok = False
        else:
            bad = [k for k in range(words) if d[k] != exp["product_lines"][k].lower()]
            c1_ok = not bad
            rep.check("C1 bank2 窗口==独立期望乘积", not bad,
                      "" if not bad else f"首失配行 {bad[0]}（共 {len(bad)} 行）"
                      f"：dump={d[bad[0]]} exp={exp['product_lines'][bad[0]].lower()}")

    # ── C2 哨兵与 LOAD 落位 ────────────────────────────────────────────────
    c2_ok = True
    for bank, src_lines, tag in ((ib, exp["input_lines"], "bank0/input"),
                                 (sb, exp["scalar_lines"], "bank1/scalar")):
        d = dump_rows(0, bank)
        if d is None:
            rep.check(f"C2 {tag} dump 存在", False)
            c2_ok = False
            continue
        bad_win = [k for k in range(words) if d[k] != src_lines[k].lower()]
        rep.check(f"C2 {tag} 窗口==HBM 源（LOAD 落位）", not bad_win,
                  "" if not bad_win else f"失配 {len(bad_win)} 行，首 {bad_win[0]}")
        bad_out = [k for k in range(words, g2.UR_BANK_DEPTH)
                   if d[k] != g2.sentinel_line(bank, k).lower()]
        rep.check(f"C2 {tag} 窗外==sentinel", not bad_out,
                  "" if not bad_out else f"失配 {len(bad_out)} 行，首 {bad_out[0]}")
        c2_ok = c2_ok and not bad_win and not bad_out
    d = dump_rows(0, ob)
    if d is not None:
        bad = [k for k in range(words, g2.UR_BANK_DEPTH)
               if d[k] != g2.sentinel_line(ob, k).lower()]
        rep.check("C2 bank2 窗外==sentinel", not bad,
                  "" if not bad else f"失配 {len(bad)} 行，首 {bad[0]}")
        c2_ok = c2_ok and not bad
    for bank in range(g2.UR_BANKS):
        if bank in (ib, sb, ob):
            continue
        d = dump_rows(0, bank)
        if d is None:
            rep.check(f"C2 bank{bank} dump 存在", False)
            c2_ok = False
            continue
        bad = [k for k in range(g2.UR_BANK_DEPTH)
               if d[k] != g2.sentinel_line(bank, k).lower()]
        rep.check(f"C2 smc_0 bank{bank} 全 sentinel", not bad,
                  "" if not bad else f"失配 {len(bad)} 行，首 {bad[0]}")
        c2_ok = c2_ok and not bad
    for smc in range(1, 8):
        d0 = dump_rows(smc, 0)
        if d0 is None:
            rep.check(f"C2 smc_{smc} dump 存在", False)
            c2_ok = False
            continue
        bad_all = 0
        first = None
        for bank in range(g2.UR_BANKS):
            dd = dump_rows(smc, bank)
            if dd is None:
                bad_all += 1
                continue
            for k in range(g2.UR_BANK_DEPTH):
                if dd[k] != g2.sentinel_aux_line(smc, bank, k).lower():
                    bad_all += 1
                    if first is None:
                        first = (bank, k)
        rep.check(f"C2 smc_{smc} 全 sentinel′", bad_all == 0,
                  "" if bad_all == 0 else f"失配 {bad_all} 处，首 bank{first[0]} 行{first[1]}")
        c2_ok = c2_ok and bad_all == 0

    # ── C3 时序：首有效读 > 末写（严格）────────────────────────────────────
    c3_ok = None
    win_writes = [w for w in writes
                  if int(w[1]) == 0 and int(w[2]) in (ib, sb) and int(w[4]) < words]
    win_reads = [r for r in reads
                 if int(r[1]) == 0 and int(r[3]) in (ib, sb) and int(r[4]) < words]
    if not win_writes:
        rep.check("C3 窗口写 trace 非空", False, "bank0/1 窗口无写记录——探针失效？")
        c3_ok = False
    if not win_reads:
        rep.check("C3 窗口读 trace 非空", False, "bank0/1 窗口无读记录——COMPUTE 未读源？")
        c3_ok = False
    if win_writes and win_reads:
        max_wr = max(int(w[0]) for w in win_writes)
        min_rd = min(int(r[0]) for r in win_reads)
        c3_ok = min_rd > max_wr
        rep.check("C3 min(窗口读拍) > max(窗口写拍)", c3_ok,
                  f"min_read={min_rd} max_write={max_wr} gap={min_rd - max_wr}")
        if args.mode == "positive" and barrier_release_cycle is not None:
            ok = min_rd > barrier_release_cycle
            rep.check("C3 min(窗口读拍) > barrier release 拍", ok,
                      f"min_read={min_rd} release={barrier_release_cycle}")
            c3_ok = c3_ok and ok

    # ── C4 写账 ────────────────────────────────────────────────────────────
    src_by_bank = {ib: exp["input_lines"], sb: exp["scalar_lines"], ob: exp["product_lines"]}
    per_addr: dict[tuple[int, int], list[tuple[int, str, str]]] = {}
    viol = []
    for w in writes:
        smc, bank, addr = int(w[1]), int(w[2]), int(w[4])
        if smc != 0:
            viol.append(f"smc{smc} bank{bank} addr{addr} @cyc{w[0]}")
            continue
        if bank not in (ib, sb, ob) or addr >= words:
            viol.append(f"窗外写 smc0 bank{bank} addr{addr} @cyc{w[0]}")
            continue
        per_addr.setdefault((bank, addr), []).append((int(w[0]), w[6].lower(), w[5].lower()))
    rep.check("C4 零越界写（smc≠0 / 旁 bank / 窗外）", not viol,
              "" if not viol else f"{len(viol)} 处，首：{viol[0]}")
    for bank in (ib, sb):
        miss = [a for a in range(words) if (bank, a) not in per_addr]
        multi = [(a, len(v)) for (b, a), v in per_addr.items() if b == bank and len(v) != 1]
        rep.check(f"C4 bank{bank} 窗口每址恰 1 写", not miss and not multi,
                  "" if not (miss or multi) else f"缺 {len(miss)} 址 / 非单写 {multi[:3]}")
        bad = [(a, per_addr[(bank, a)][0][1], src_by_bank[bank][a].lower())
               for a in range(words)
               if (bank, a) in per_addr and per_addr[(bank, a)][0][1] != src_by_bank[bank][a].lower()]
        rep.check(f"C4 bank{bank} 写数据==HBM 源行", not bad,
                  "" if not bad else f"失配 {len(bad)} 址，首 addr{bad[0][0]}")
    miss = [a for a in range(words) if (ob, a) not in per_addr]
    rep.check("C4 bank2 窗口每址有写", not miss, "" if not miss else f"缺 {len(miss)} 址")
    bad_last, extra = [], 0
    for a in range(words):
        if (ob, a) not in per_addr:
            continue
        v = sorted(per_addr[(ob, a)])
        extra += len(v) - 1
        if v[-1][1] != exp["product_lines"][a].lower():
            bad_last.append(a)
    rep.check("C4 bank2 末写==期望乘积", not bad_last,
              "" if not bad_last else f"失配 {len(bad_last)} 址，首 addr{bad_last[0]}")
    if extra:
        rep.note("C4 bank2 窗口内重复写", f"{extra} 次（ur-w0 预热形态属结构已知，留痕不判）")

    # ── C5 HBM 源无损：快照覆盖区逐行一致；STORE 开启时输出窗豁免（ADR-060
    #    §6.6：窗口是 STORE 的合法写目标，其值由 C8 独立验收）────────────────
    hbm_res = os.path.join(case_dir, "smc_0", "hbm_result_smc_0.hex")
    if rep.check("C5 hbm_result_smc_0.hex 存在", os.path.isfile(hbm_res)):
        a = read_lines(hbm_res)
        b = read_lines(snap1)
        if len(a) < len(b):
            rep.check("C5 HBM dump 覆盖快照区", False,
                      f"result {len(a)} 行 < snapshot {len(b)} 行")
        else:
            if len(a) > len(b):
                rep.note("C5 dump 尾部超出快照",
                         f"{len(a) - len(b)} 行=未加载区（X→0），不可判读不判")
            bad = [k for k in range(len(b)) if a[k].lower() != b[k].lower()]
            if store:
                w0, wl = store["hbm_window"]["addr0"], store["hbm_window"]["lines"]
                bad = [k for k in bad if not (w0 <= k < w0 + wl)]
            rep.check("C5 HBM 预载区==快照" + ("（输出窗豁免，STORE 开启）" if store
                                               else "（无 STORE 纪律）"),
                      not bad,
                      "" if not bad else f"失配 {len(bad)} 行，首 {bad[0]}")

    # ── 步骤④ STORE 断言（ADR-060 §6.6；expect 含 "store" 键才启用）────────
    c3s_ok = c6_ok = c8_ok = None
    timeline = None
    if store:
        addr0 = int(store["hbm_window"]["addr0"])
        wlines = int(store["hbm_window"]["lines"])
        prod = exp["product_lines"]

        # C3s：STB URA 首读 > bank2 末写（store 侧读后写顺序；positive 追加
        # > WBARRIER release——屏障确实挡在读之前）
        stb_rd_path = os.path.join(case_dir, "stb_ur_read_trace.log")
        ob_writes = [w for w in writes
                     if int(w[1]) == 0 and int(w[2]) == ob and int(w[4]) < words]
        stb_reads = []
        if rep.check("C3s stb_ur_read_trace.log 非空",
                     os.path.isfile(stb_rd_path) and os.path.getsize(stb_rd_path) > 20):
            stb_reads = load_trace(stb_rd_path, 2)
        if not ob_writes:
            rep.check("C3s bank2 窗口写 trace 非空", False, "无 bank2 写记录——探针失效？")
            c3s_ok = False
        if stb_reads and ob_writes:
            max_ob_wr = max(int(w[0]) for w in ob_writes)
            min_stb_rd = min(int(r[0]) for r in stb_reads)
            c3s_ok = min_stb_rd > max_ob_wr
            rep.check("C3s min(STB URA 读拍) > max(bank2 窗口写拍)", c3s_ok,
                      f"min_stb_read={min_stb_rd} max_bank2_write={max_ob_wr} "
                      f"gap={min_stb_rd - max_ob_wr}")
            if args.mode == "positive":
                wb_rel = WBARRIER_RELEASE_RE.findall(log)
                if wb_rel:
                    ok = min_stb_rd > int(wb_rel[0][0])
                    rep.check("C3s min(STB URA 读拍) > WBARRIER release 拍", ok,
                              f"min_stb_read={min_stb_rd} release={wb_rel[0][0]}")
                    c3s_ok = c3s_ok and ok
                else:
                    rep.check("C3s WBARRIER release 标记存在（positive 必备）", False)
                    c3s_ok = False
        elif c3s_ok is None:
            c3s_ok = False

        # C6：精确写账（hbm_write_trace = RAM 写口落实级，8 SMC 全探；
        # 用户要求②：地址/数据/字节使能/数量 + 重复/遗漏/非目标三类单列）
        hb_path = os.path.join(case_dir, "hbm_write_trace.log")
        hbm_rows = []
        if rep.check("C6 hbm_write_trace.log 非空",
                     os.path.isfile(hb_path) and os.path.getsize(hb_path) > 30):
            hbm_rows = load_trace(hb_path, 5)
        tgt: dict[int, list] = {}
        nontarget = []
        for r in hbm_rows:
            cyc, smc, addr = int(r[0]), int(r[1]), int(r[2], 16)
            wea, dina = r[3].lower(), r[4].lower()
            if smc == 0 and addr0 <= addr < addr0 + wlines:
                tgt.setdefault(addr, []).append((cyc, dina, wea))
            else:
                nontarget.append((cyc, smc, addr))
        miss = [a for a in range(addr0, addr0 + wlines) if a not in tgt]
        dup = {a: len(v) for a, v in tgt.items() if len(v) != 1}
        bad_dina = [a for a, v in sorted(tgt.items())
                    if v[-1][1] != prod[a - addr0].lower()]
        bad_wea = [a for a, v in sorted(tgt.items())
                   if any(w != "ffff" for _, _, w in v)]
        total_wr = sum(len(v) for v in tgt.values())
        c6_ok = bool(hbm_rows)
        c6_ok = rep.check("C6 窗口址集恰全（零遗漏）", not miss,
                          "" if not miss else f"缺 {len(miss)} 址，首 addra "
                          f"{hex(miss[0])}") and c6_ok
        c6_ok = rep.check("C6 窗口每址恰 1 写（零重复）", not dup,
                          "" if not dup else f"重复 {list(dup.items())[:4]}") and c6_ok
        c6_ok = rep.check("C6 写数据==独立期望乘积", not bad_dina,
                          "" if not bad_dina else f"失配 {len(bad_dina)} 址，首 addra "
                          f"{hex(bad_dina[0])}") and c6_ok
        c6_ok = rep.check("C6 写字节使能==FFFF", not bad_wea,
                          "" if not bad_wea else f"异常 {len(bad_wea)} 址，首 addra "
                          f"{hex(bad_wea[0])}") and c6_ok
        c6_ok = rep.check("C6 零非目标写（窗外/SMC1-7）", not nontarget,
                          "" if not nontarget else f"{len(nontarget)} 笔，首 "
                          f"cyc{nontarget[0][0]} smc{nontarget[0][1]} addra "
                          f"{hex(nontarget[0][2])}") and c6_ok
        c6_ok = rep.check("C6 窗口写总数==窗口行数", total_wr == wlines,
                          f"实际 {total_wr} / 期望 {wlines}") and c6_ok

        # C7：四事件时间轴 + 结束顺序（用户要求①③；positive 才判序，
        # negative 无 GATE4 标记跳过）
        if args.mode == "positive":
            iq_path = os.path.join(case_dir, "iq_issue_trace.log")
            iq_rows = load_trace(iq_path, 4) if os.path.isfile(iq_path) else []
            iss24 = [int(r[0]) for r in iq_rows if r[1] == "24" and r[2] == "1"]
            iss25 = [int(r[0]) for r in iq_rows if r[1] == "25" and r[2] == "1"]
            crd_stb = [int(x) for x in CRD_STB_RE.findall(log)]
            crd_stbaxi = [int(x) for x in CRD_STBAXI_RE.findall(log)]
            bresps = [int(x) for x in BRESP_RE.findall(log)]
            s4r = S4_RELEASE_RE.findall(log)
            dumpc = DUMP_RE.findall(log)
            win_cyc = sorted(c for v in tgt.values() for c, _, _ in v)
            timeline = {
                "issue_q24_first": min(iss24) if iss24 else None,
                "issue_q25_first": min(iss25) if iss25 else None,
                "crd_stb_pulses": crd_stb,
                "crd_stbaxi_pulses": crd_stbaxi,
                "hbm_win_first_write": win_cyc[0] if win_cyc else None,
                "hbm_win_last_write": win_cyc[-1] if win_cyc else None,
                "bresp_cycles": bresps,
                "s4_release": int(s4r[0][0]) if s4r else None,
                "s4_release_credit_snapshot": {"crd_stb": int(s4r[0][4]),
                                               "crd_stbaxi": int(s4r[0][5])} if s4r else None,
                "dump_begin": int(dumpc[0]) if dumpc else None,
            }
            rep.check("C7 指令发出事件在（q24/q25 首拍非空）",
                      bool(iss24) and bool(iss25),
                      f"q24={timeline['issue_q24_first']} q25={timeline['issue_q25_first']}")
            if s4r:
                cs, csx = int(s4r[0][4]), int(s4r[0][5])
                rep.check("C7 信用计数==期望（缺失=RED；record-only 不门控）",
                          cs == store["exp_stb_done"] and csx == store["exp_stbaxi_done"],
                          f"crd_stb={cs}/{store['exp_stb_done']} "
                          f"crd_stbaxi={csx}/{store['exp_stbaxi_done']}")
            rep.check("C7 B 响应计数==期望", len(bresps) == store["exp_bresp"],
                      f"实际 {len(bresps)} / 期望 {store['exp_bresp']}")
            last_wr = timeline["hbm_win_last_write"]
            if s4r and last_wr is not None:
                rep.check("C7 S4 release 严格晚于末写落实",
                          int(s4r[0][0]) > last_wr,
                          f"release={s4r[0][0]} last_write={last_wr}")
            if dumpc and last_wr is not None:
                rep.check("C7 DUMP 严格晚于末写落实", int(dumpc[0]) > last_wr,
                          f"dump={dumpc[0]} last_write={last_wr}")
                if bresps:
                    rep.check("C7 DUMP 晚于末 B 响应", int(dumpc[0]) > bresps[-1],
                              f"dump={dumpc[0]} last_bresp={bresps[-1]}")
            # 信用≺写落位 gap：结构性预期（ADR-060 §6.2），记录不判 RED
            if last_wr is not None and crd_stb and crd_stbaxi:
                rep.note("C7 信用-写落位 gap（结构性预期，记录用）",
                         f"CRD_STB 首脉冲 @{crd_stb[0]} / CRD_STBAXI 首脉冲 "
                         f"@{crd_stbaxi[0]} vs 末写 @{last_wr}：gap_stb="
                         f"{crd_stb[0] - last_wr} gap_stbaxi={crd_stbaxi[0] - last_wr}"
                         f"（负=信用先于写落实，信用不对称实证）")

        # C8：完整链路——hbm_result 输出窗 == 独立期望乘积（用户要求④）
        if os.path.isfile(hbm_res):
            a8 = read_lines(hbm_res)
            if len(a8) < addr0 + wlines:
                rep.check("C8 HBM dump 覆盖输出窗", False,
                          f"result {len(a8)} 行 < 窗末 {addr0 + wlines} 行")
                c8_ok = False
            else:
                bad8 = [k for k in range(wlines)
                        if a8[addr0 + k].lower() != prod[k].lower()]
                c8_ok = not bad8
                rep.check("C8 HBM 输出窗==独立期望乘积（完整链路逐位比对）", not bad8,
                          "" if not bad8 else f"失配 {len(bad8)} 行，首 addra "
                          f"{hex(addr0 + bad8[0])}：dump={a8[addr0 + bad8[0]].lower()} "
                          f"exp={prod[bad8[0]].lower()}")

    # ── 汇总（模式语义）────────────────────────────────────────────────────
    prov = tb_provenance(args.sandbox, args.tb_sha256, rep)
    print(f"[G3-CMP] case={case} mode={args.mode} words={words}")
    for ln in rep.lines:
        print(ln)
    if args.mode == "positive":
        verdict = rep.red == 0
        summary = "GREEN" if verdict else "RED"
    else:
        falsified = (not c1_ok) or (c3_ok is False)
        if store:
            falsified = falsified or (c3s_ok is False) or (c6_ok is False) \
                or (c8_ok is False)
        # 允许的红线白名单：竞态的直接后果。并段形态追加 C4 bank2 末写≠期望乘积
        # （垃圾输入的乘积账必然不符，是证伪的一部分）；merged-3 再追加
        # C3s/C6/C8（STORE 侧读后写竞态的直接后果）；C2/C5 必须保持绿——
        # 证明 LOAD 无恙、红纯因竞态。
        allow = ("C1 ", "C3 ", "C4 bank2 末写") if merged_neg else ("C1 ", "C3 ")
        if merged_neg and store:
            allow = allow + ("C3s", "C6 ", "C8 ")
        base_ok = rep.red == 0 or all(
            any(p in ln for p in allow)
            for ln in rep.lines if ln.startswith("  [RED"))
        verdict = base_ok and falsified
        fals_detail = (f"C1_red={not c1_ok} C3_red={c3_ok is False}"
                       + (f" C3s_red={c3s_ok is False} C6_red={c6_ok is False}"
                          f" C8_red={c8_ok is False}" if store else ""))
        summary = (f"NEGATIVE-CONTROL LIVE（证伪路径踩中：{fals_detail}）") if verdict else \
                  "RED（负控未证活或基础断言失败——不确定≠通过）"
    print(f"[G3-CMP] 总判：{summary}（red={rep.red} warn={rep.warn}）")
    with open(os.path.join(case_dir, f"g3_report_{args.mode}.json"), "w",
              encoding="utf-8") as f:
        json.dump({"case": case, "mode": args.mode, "verdict": summary,
                   "red": rep.red, "warn": rep.warn, "lines": rep.lines,
                   "timeline": timeline, "provenance": prov},
                  f, indent=2, ensure_ascii=False)
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
