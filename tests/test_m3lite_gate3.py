# tests/test_m3lite_gate3.py —— ADR-060 门③步骤③工具链单测（纯 Python，不触 VCS）。
# 纪律：不按实际输出调期望；RED 注入用例证明比对器能红（负向可证活）。
import json
import os
import struct
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(ROOT, "verification_v2", "tools"))

import ldb_review_case_gen as g2  # noqa: E402
import m3lite_gate3_case_gen as g3  # noqa: E402
import m3lite_gate3_compare as g3c  # noqa: E402
import stb_solo_case_gen as s1  # noqa: E402

WORDS = 4


def _f32le(v: float) -> bytes:
    return struct.pack("<f", v)


def _line(vals4) -> str:
    return g3.bytes_to_hex_line_le_as_msb(b"".join(_f32le(v) for v in vals4))


# 输入行：1.0/0.5/-1.0/2.0 ×4 → 乘 1.25 = 1.25/0.625/-1.25/2.5（全规格化）
INPUT_VALS = [1.0, 0.5, -1.0, 2.0]
INPUT_LINE = _line(INPUT_VALS)
SCALAR_LINE = g3.bytes_to_hex_line_le_as_msb(struct.pack("<IIII", *([0x3FA00000] * 4)))
PRODUCT_LINE = _line([v * 1.25 for v in INPUT_VALS])


def _hbm_lines() -> list[str]:
    lines = [g2.src_pattern_line(k) for k in range(0x2000 // 16 + WORDS + 8)]
    for k in range(WORDS):
        lines[k] = INPUT_LINE
        lines[0x1000 // 16 + k] = SCALAR_LINE
    return lines


def _write(path: str, lines) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


def build_case(tmp_path, *, mode="positive", plant=None, merged=False,
               store=False, fi=None, fi_us3=None, fatal=None, add_respacct=False):
    """合成最小 case；plant∈{None,'c1','c2aux','c3','c4_smc','c0_nobarrier',
    'c0_barrier_count','c0_barrier_waited2','c6_dup','c6_omit','c6_nontarget',
    'c6_data','c6_strb','c7_dump_early','c8_bad','c3s_early','wb_zero','bp_short',
    'us3_nodrain','us3_leak','us3_risen_late'}。
    merged=True：并段负控形态（仅 mode="negative"）——单 stage_0 清单、
    无 stage_1(/stage_2)、无 G3 标记、INTR_ASSERT 恰 1。
    store=True：步骤④三段形态（expect 增 store/wbarrier/s4barrier 段，
    增 hbm_write_trace/stb_ur_read_trace/iq_issue_trace 与 GATE4 标记）。
    fi=N：故障注入标签（expect.fault_injection.min_waited=N）+ BP 标记。
    fi_us3=N：master 侧合法注入形态（ADR-060 errata 8，2026-10-08）——
    expect 同样带 fault_injection，但写 [GATE4][US3BP] 标记族
    （inject<bvalid_risen<release<=drained 为绿基线）；
    plant='us3_nodrain' 缺 drained / 'us3_leak' drained<release /
    'us3_risen_late' risen>release。
    fatal='wbarrier'|'s4barrier'|'respacct'：fatal_expected 形态 sim.log
    （超时标记 / 任务2收口响应账目异常专属标记，errata 10）。
    add_respacct=True：在任意形态 sim.log 末尾追加 [G3_RESPACCT][FATAL]
    标记（测防吸收/防误报）。"""
    assert not (merged and mode != "negative")
    assert not (fi is not None and fi_us3 is not None)
    case = tmp_path / "case"
    case.mkdir()
    exp = {
        "schema": "m3lite.gate3_expect/v1", "case": "case", "words": WORDS,
        "ur_windows": {"input": {"bank": 0, "line0": 0, "lines": WORDS},
                       "scalar": {"bank": 1, "line0": 0, "lines": WORDS},
                       "output": {"bank": 2, "line0": 0, "lines": WORDS}},
        "barrier": {"exp_ldb_done": 2, "exp_ldbaxi_done": 2, "timeout_cycles": 5000},
        # 13 = 源级枚举（mul_scalar_op.cpp:505-510 六路由 + 七队）
        "compute_files": [f"iq_fake_{i}.hex" for i in range(13)],
    }
    if store:
        exp["store"] = {
            "enabled": True,
            "hbm_window": {"byte_base": 0x2000, "addr0": 0x2000 // 16,
                           "lines": WORDS, "line_bytes": 16},
            "exp_stb_done": 1, "exp_stbaxi_done": 1, "exp_bresp": 1,
            "crosscheck": "byte_exact_vs_gate1_emit",
        }
        exp["wbarrier"] = {"bank": 2, "writes": WORDS, "stage": 1,
                           "timeout_cycles": 5000}
        exp["s4barrier"] = {"addr0": 0x2000 // 16, "writes": WORDS, "bresp": 1,
                            "timeout_cycles": 5000}
        if fi is not None or fi_us3 is not None:
            n_bp = fi if fi is not None else fi_us3
            exp["fault_injection"] = {"type": "axi_w_backpressure", "cycles": n_bp,
                                      "min_waited": n_bp}
    if merged:
        exp["negative_control"] = "merged_single_stage"
        exp["barrier"]["unused"] = True
    (case / "g3_expect.json").write_text(json.dumps(exp), encoding="utf-8")
    hbm = _hbm_lines()
    _write(str(case / "memory_init_snapshot" / "memory_init1.hex"), hbm)
    hbm_res = list(hbm)
    if store:
        # STORE 写输出窗（C5 豁免、C8 验收）；'c8_bad'：窗口首行错
        w0 = 0x2000 // 16
        for k in range(WORDS):
            hbm_res[w0 + k] = PRODUCT_LINE
        if plant == "c8_bad":
            hbm_res[w0] = INPUT_LINE
    _write(str(case / "smc_0" / "hbm_result_smc_0.hex"), hbm_res)

    for b in range(8):
        rows = [g2.sentinel_line(b, k) for k in range(256)]
        if b == 0:
            rows[0:WORDS] = [INPUT_LINE] * WORDS
        elif b == 1:
            rows[0:WORDS] = [SCALAR_LINE] * WORDS
        elif b == 2:
            rows[0:WORDS] = ([g2.sentinel_line(2, k) for k in range(WORDS)]
                             if plant == "c1" else [PRODUCT_LINE] * WORDS)
        _write(str(case / "smc_0" / f"ur_result_smc_0_bank{b}.hex"), rows)
    for s in range(1, 8):
        for b in range(8):
            rows = [g2.sentinel_aux_line(s, b, k) for k in range(256)]
            if plant == "c2aux" and s == 3 and b == 5:
                rows[100] = g2.sentinel_line(5, 100)  # 错用主 sentinel = 污染
            _write(str(case / f"smc_{s}" / f"ur_result_smc_{s}_bank{b}.hex"), rows)

    ob_wr0 = 310 if store else 220  # store 形态：bank2 写在 stage_1（WBARRIER 段）
    wr = ["# cycle smc bank we addr wbe wdata"]
    for a in range(WORDS):
        wr.append(f"{110 + a} 0 0 1 {a} ffff {INPUT_LINE}")
    for a in range(WORDS):
        wr.append(f"{120 + a} 0 1 1 {a} ffff {SCALAR_LINE}")
    if plant == "c4_smc":
        wr.append(f"130 2 0 1 0 ffff {INPUT_LINE}")
    for a in range(WORDS):
        wr.append(f"{ob_wr0 + a} 0 2 1 {a} ffff {PRODUCT_LINE}")
    _write(str(case / "ur_write_trace.log"), wr)

    rd = ["# cycle smc port bank addr"]
    first_rd = 112 if plant == "c3" else 210  # c3: 读早于末写(123) → 违例
    for a in range(WORDS):
        rd.append(f"{first_rd + a} 0 0 0 {a}")
        rd.append(f"{first_rd + a} 0 1 1 0")
    _write(str(case / "ur_read_trace.log"), rd)

    n_stage0 = 2 + 13 + (2 if store else 0)
    stages = [(0, n_stage0)] if merged else ([(0, 2), (1, 13), (2, 2)] if store
                                             else [(0, 2), (1, 13)])
    for stage, n in stages:
        _write(str(case / "smi_iq_inst" / f"stage_{stage}" / "data_list_inst_subq0.txt"),
               [f"dut.iq[{i}] /p/iq_{i}.hex" for i in range(n)])

    # ── 步骤④ trace/标记 fixtures（store 形态）────────────────────────────
    wb_waited = 15 if plant != "wb_zero" else 0
    s4_waited = 6
    if fi is not None:
        s4_waited = 250 if plant == "bp_short" else fi + 10
    if store:
        w0 = 0x2000 // 16
        hb = ["# cycle smc addra wea dina"]
        for a in range(WORDS):
            dina = INPUT_LINE if (plant == "c6_data" and a == 1) else PRODUCT_LINE
            wea = "00ff" if (plant == "c6_strb" and a == 1) else "ffff"
            hb.append(f"{335 + a} 0 {w0 + a:05x} {wea} {dina}")
        if plant == "c6_dup":
            hb.append(f"338 0 {w0 + 3:05x} ffff {PRODUCT_LINE}")   # 重复写
        if plant == "c6_omit":
            hb.pop()                                               # 遗漏末行
        if plant == "c6_nontarget":
            hb.append(f"336 1 {w0:05x} ffff {PRODUCT_LINE}")       # 非目标（smc1）
        _write(str(case / "hbm_write_trace.log"), hb)
        # STB URA 读：'c3s_early' 早于 bank2 末写（ob_wr0+3=313）；正常在 WBARRIER 后
        stb0 = 311 if plant == "c3s_early" else 322
        sr = ["# cycle ur_addr"]
        for a in range(WORDS):
            sr.append(f"{stb0 + a} {0x2000 + a * 16:04x}")
        _write(str(case / "stb_ur_read_trace.log"), sr)
        _write(str(case / "iq_issue_trace.log"),
               ["# cycle iq dly_data_valid dly_data",
                f"{322} 24 1 deadbeef", f"{323} 25 1 deadbeef"])

    sim_dir = (tmp_path / "sbx" / "arith_regression" / "test_log" / "multi_stage"
               / "multi_inst_stage" / "case")
    sim_dir.mkdir(parents=True)
    if fatal is not None:
        if fatal == "respacct":
            # 真实形态 = TB $display 诊断行 + VCS $fatal 回显（两处专属标记）
            log = ("[G3_RESPACCT][FATAL] response accounting anomaly: 16 consecutive "
                   "hits @probe_cycle 1208 pending=1 bresp_cnt=0 bmc_ds2_bvalid=1 "
                   "bmc_ds2_bready=0 pending_bid=0x0\n"
                   "\"tb_aiacc_top_v04_f_multi_stage.v\", 3230: [G3_RESPACCT][FATAL] "
                   "matrix ds2 response accounting anomaly sustained 16 consecutive "
                   "cycles: fatal\n")
        else:
            tag = {"wbarrier": "[GATE4][WBARRIER][ERR] TIMEOUT after 1 cycles: "
                               "landed 0/4 bank=2",
                   "s4barrier": "[GATE4][S4BARRIER][ERR] TIMEOUT after 50 cycles: "
                                "hbm_writes=0/4 bresp=0/1 (crd_stb=1 crd_stbaxi=1 "
                                "record-only)"}[fatal]
            log = tag + "\n"
        if plant == "dump_after":
            log += "[GATE4][DUMP] begin @probe_cycle 999 (gated by S4BARRIER)\n"
    elif mode == "positive":
        log = "" if plant == "c0_nobarrier" else (
            "[GATE3][BARRIER] enter @probe_cycle 100: expect ldb_done=2 ldbaxi_done=2 (have 0/0)\n"
            "[GATE3][BARRIER] release @probe_cycle 200: ldb_done=2 ldbaxi_done=2 (waited 100 cycles)\n")
        # 三段形态：G3 屏障逐转移触发 → 恰 2 对（@stage_0→1 真门 + @stage_1→2
        # 直穿，计数已满足故 waited==0）。'c0_barrier_count'：只给 1 对（数错）；
        # 'c0_barrier_waited2'：第 2 次 waited=5（直穿不变量破）。
        if store and plant not in ("c0_nobarrier", "c0_barrier_count"):
            w2 = 5 if plant == "c0_barrier_waited2" else 0
            log += ("[GATE3][BARRIER] enter @probe_cycle 250: expect ldb_done=2 ldbaxi_done=2 (have 2/2)\n"
                    f"[GATE3][BARRIER] release @probe_cycle 250: ldb_done=2 ldbaxi_done=2 (waited {w2} cycles)\n")
        if store:
            dump_cyc = 337 if plant == "c7_dump_early" else 356
            log += (
                "[GATE4][WBARRIER] enter @probe_cycle 305: expect bank=2 writes=4 (landed 0)\n"
                f"[GATE4][WBARRIER] release @probe_cycle 320: landed 4 (waited {wb_waited} cycles)\n"
                "[GATE4][CRD_STB] @probe_cycle 330 crd=0000011\n"
                "[GATE4][CRD_STBAXI] @probe_cycle 332 crd=0000011\n"
                "[GATE4][HBM_BRESP] @probe_cycle 339 bid=0 bresp=00\n"
                "[GATE4][S4BARRIER] enter @probe_cycle 349: expect hbm_writes=4 bresp=1 "
                "(have 4/1; crd_stb=1 crd_stbaxi=1 record-only)\n"
                f"[GATE4][S4BARRIER] release @probe_cycle 355: hbm_writes=4 bresp=1 "
                f"(waited {s4_waited} cycles; crd_stb=1 crd_stbaxi=1 record-only)\n"
                f"[GATE4][DUMP] begin @probe_cycle {dump_cyc} (gated by S4BARRIER)\n")
            if fi_us3 is not None:
                u_inj, u_rel = 331, 331 + fi_us3
                u_risen, u_drain = 340, u_rel
                if plant == "us3_risen_late":
                    u_risen = u_rel + 1
                if plant == "us3_leak":
                    u_drain = u_rel - 1
                log += ("[GATE4][US3BP] fault injection armed: de_stb_bready[0] "
                        f"stall {fi_us3} cycles (synthetic fixture)\n"
                        f"[GATE4][US3BP] inject @probe_cycle {u_inj}: force "
                        f"de_stb_bready[0]=0 for {fi_us3} cycles\n"
                        f"[GATE4][US3BP] bvalid risen @probe_cycle {u_risen} "
                        "(B pending at us3 during stall; delay half of evidence)\n"
                        f"[GATE4][US3BP] release @probe_cycle {u_rel}\n")
                if plant != "us3_nodrain":
                    log += (f"[GATE4][US3BP] drained @probe_cycle {u_drain} "
                            "(engine-side us3 B handshake completed after release)\n")
            elif fi is not None:
                log += ("[GATE4][BP] inject @probe_cycle 331: force bmc_ds2_bready=0 "
                        f"for {fi} cycles\n"
                        f"[GATE4][BP] release @probe_cycle {331 + fi}\n")
        log += ("[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle 90\n"
                "[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle 300\n")
        if store:
            log += "[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle 348\n"
    elif merged:
        log = "[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle 400\n"
    else:
        log = ("[GATE3][BYPASS] raw intr edge @probe_cycle 95 — releasing next stage "
               "WITHOUT credit barrier (negative control)\n"
               "[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle 95\n"
               "[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle 300\n")
    if add_respacct:
        log += ("[G3_RESPACCT][FATAL] response accounting anomaly: 16 consecutive "
                "hits @probe_cycle 1208 pending=1 bresp_cnt=0 bmc_ds2_bvalid=1 "
                "bmc_ds2_bready=0 pending_bid=0x0\n")
    (sim_dir / "sim.log").write_text(log, encoding="utf-8")
    return str(case), str(tmp_path / "sbx")


def _run(case, sbx, mode, expect_timeout=None):
    argv = sys.argv
    sys.argv = ["m3lite_gate3_compare.py", case, "--mode", mode, "--sandbox", sbx]
    if expect_timeout:
        sys.argv += ["--expect-timeout", expect_timeout]
    try:
        return g3c.main()
    finally:
        sys.argv = argv


def test_g3_positive_green(tmp_path):
    case, sbx = build_case(tmp_path)
    assert _run(case, sbx, "positive") == 0


def test_g3_red_c1_wrong_product(tmp_path):
    case, sbx = build_case(tmp_path, plant="c1")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c2_aux_contamination(tmp_path):
    case, sbx = build_case(tmp_path, plant="c2aux")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c3_read_before_write(tmp_path):
    case, sbx = build_case(tmp_path, plant="c3")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c4_cross_smc_write(tmp_path):
    case, sbx = build_case(tmp_path, plant="c4_smc")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c0_missing_barrier_evidence(tmp_path):
    case, sbx = build_case(tmp_path, plant="c0_nobarrier")
    assert _run(case, sbx, "positive") == 1


def test_g3_negative_control_live(tmp_path):
    """负控：bypass 标记在 + C3 被证伪（读 112 < 末写 123）→ 证活，exit 0。"""
    case, sbx = build_case(tmp_path, mode="negative", plant="c3")
    assert _run(case, sbx, "negative") == 0


def test_g3_negative_control_not_live_is_red(tmp_path):
    """负控全绿（时序居然成立）→ 不确定 ≠ 通过，exit 1。"""
    case, sbx = build_case(tmp_path, mode="negative")
    assert _run(case, sbx, "negative") == 1


def test_g3_negative_merged_live(tmp_path):
    """并段负控：无 G3 标记 + 清单 15 行 + C3 被证伪（读 112 < 末写 123）→ LIVE。"""
    case, sbx = build_case(tmp_path, mode="negative", plant="c3", merged=True)
    assert _run(case, sbx, "negative") == 0


def test_g3_negative_merged_not_live_is_red(tmp_path):
    """并段负控全绿（时序居然成立）→ 不确定 ≠ 通过，exit 1。"""
    case, sbx = build_case(tmp_path, mode="negative", merged=True)
    assert _run(case, sbx, "negative") == 1


def test_g3_hex_line_roundtrip():
    b = bytes(range(16))
    assert g3.hex_line_to_bytes_be(g3.bytes_to_hex_line_le_as_msb(b)) == b


def test_g3_compute_expectations_rejects_subnormal(tmp_path):
    """乘积落非规格化区（FTZ 分歧区）→ 装配器响亮拒绝，不静默出期望。"""
    tiny = struct.unpack("<f", struct.pack("<I", 1))[0]  # 最小非规格化数
    lines = _hbm_lines()
    lines[0] = _line([tiny, tiny, tiny, tiny])
    p = tmp_path / "hbm.hex"
    _write(str(p), lines)
    with pytest.raises(SystemExit):
        g3.compute_expectations(str(p), WORDS)


def test_g3_compute_expectations_matches_scalar_layout(tmp_path):
    p = tmp_path / "hbm.hex"
    _write(str(p), _hbm_lines())
    exp = g3.compute_expectations(str(p), WORDS)
    assert exp["product_lines"][0] == PRODUCT_LINE
    assert exp["scalar_lines"][0] == SCALAR_LINE


def _ref_load_lines():
    ldb, axi = [], []
    for bank, gr in ((0, 0x0), (1, 0x1000)):
        lb, ax = g2.build_and_emit([{"lines": 16, "brst": 15}], ur_bank=bank,
                                   ur_offset=0, gr_base=gr, smc_id_en=0)
        ldb += lb
        axi += ax
    return {"ldb": ldb, "axi": axi}


def test_g3_crosscheck_value_semantics_padding_ok():
    """前导零填充差异（v3 定宽 vs 门②去零）值等价 → 互校通过（2026-10-07 实证形态）。"""
    ref = _ref_load_lines()
    padded = {"ldb": [x.zfill(len(x) + 1) for x in ref["ldb"]],
              "axi": [x.zfill(len(x) + 1) for x in ref["axi"]]}
    g3.cross_check_load_stage(padded, 16)  # 不抛即过


def test_g3_crosscheck_true_bitdiff_rejected():
    """真比特差异（非前导零）仍必须响亮拒绝——防归一化吞掉真分歧。"""
    ref = _ref_load_lines()
    bad = dict(ref)
    bad["ldb"] = list(ref["ldb"])
    bad["ldb"][0] = format(int(ref["ldb"][0], 16) ^ 0x10, "x")
    with pytest.raises(SystemExit):
        g3.cross_check_load_stage(bad, 16)


# ── 步骤④ STORE 用例（ADR-060 §6.6；fixtures 见 build_case store=True）───────
def test_g3_store_positive_green(tmp_path):
    """3 段全屏障正向：C0-C8 全绿（含 C5 输出窗豁免、C7 时间轴、信用 gap note）。"""
    case, sbx = build_case(tmp_path, store=True)
    assert _run(case, sbx, "positive") == 0


def test_g3_red_c6_duplicate_write(tmp_path):
    case, sbx = build_case(tmp_path, store=True, plant="c6_dup")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c6_omitted_write(tmp_path):
    case, sbx = build_case(tmp_path, store=True, plant="c6_omit")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c6_nontarget_write(tmp_path):
    case, sbx = build_case(tmp_path, store=True, plant="c6_nontarget")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c6_wrong_data(tmp_path):
    case, sbx = build_case(tmp_path, store=True, plant="c6_data")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c6_wrong_strb(tmp_path):
    case, sbx = build_case(tmp_path, store=True, plant="c6_strb")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c7_dump_before_last_write(tmp_path):
    """DUMP 早于 HBM 末写落实 = 结束顺序违例（用户要求③）→ RED。"""
    case, sbx = build_case(tmp_path, store=True, plant="c7_dump_early")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c8_wrong_hbm_window(tmp_path):
    """完整链路：HBM 输出窗 ≠ 独立期望乘积 → RED（用户要求④）。"""
    case, sbx = build_case(tmp_path, store=True, plant="c8_bad")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c3s_stb_read_before_landing(tmp_path):
    """STB URA 首读早于 bank2 末写落实（store 侧读后写竞态）→ RED。"""
    case, sbx = build_case(tmp_path, store=True, plant="c3s_early")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_wb_zero_waited(tmp_path):
    """WBARRIER waited==0 = 挂点失效证据（enter 在 trigger、写必未落位）→ RED。"""
    case, sbx = build_case(tmp_path, store=True, plant="wb_zero")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c0_barrier_pair_count(tmp_path):
    """三段形态 G3 屏障须恰 2 对（=段间转移数，首轮实测 2026-10-07 实证）；
    只 1 对 = TB 挂点行为异常 → RED。"""
    case, sbx = build_case(tmp_path, store=True, plant="c0_barrier_count")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_c0_barrier_passthrough_waited(tmp_path):
    """第 2 次 release waited>0 = 信用计数被复位/出现新信用源
    （直穿不变量破：计数跨段单调不清零，重入必须 waited==0）→ RED。"""
    case, sbx = build_case(tmp_path, store=True, plant="c0_barrier_waited2")
    assert _run(case, sbx, "positive") == 1


def test_g3_negative_merged3_live_c1(tmp_path):
    """merged-3 负控：C1 证伪（乘积垃圾）→ LIVE；白名单含 C1。"""
    case, sbx = build_case(tmp_path, mode="negative", plant="c1",
                           merged=True, store=True)
    assert _run(case, sbx, "negative") == 0


def test_g3_negative_merged3_live_c3s(tmp_path):
    """merged-3 负控：C3s 证伪（STB 读撞上未落位窗口）→ LIVE；白名单含 C3s。"""
    case, sbx = build_case(tmp_path, mode="negative", plant="c3s_early",
                           merged=True, store=True)
    assert _run(case, sbx, "negative") == 0


def test_g3_negative_merged3_not_live_is_red(tmp_path):
    """merged-3 负控全绿 → 不确定 ≠ 通过，exit 1。"""
    case, sbx = build_case(tmp_path, mode="negative", merged=True, store=True)
    assert _run(case, sbx, "negative") == 1


def test_g3_store_bp_hold_green(tmp_path):
    """反压保持力：BP=300 注入 + S4 waited=310 ≥ min_waited 且落在精确区间
    [bp_release−s4_enter−2,+50]=[280,332] → GREEN。"""
    case, sbx = build_case(tmp_path, store=True, fi=300)
    assert _run(case, sbx, "positive") == 0


def test_g3_red_bp_waited_short(tmp_path):
    """BP=300 但 S4 waited=250 < min_waited 且低于精确区间下限（280）
    → RED（双断言同红）。"""
    case, sbx = build_case(tmp_path, store=True, fi=300, plant="bp_short")
    assert _run(case, sbx, "positive") == 1


def test_g3_store_us3bp_delay_recover_green(tmp_path):
    """master 侧合法注入（ADR-060 errata 8）：inject<risen<release<=drained
    且 delay>0 → US3BP 语义全过；S4-waited 判据不适用（note 不门控）→ GREEN。"""
    case, sbx = build_case(tmp_path, store=True, fi_us3=150)
    assert _run(case, sbx, "positive") == 0


def test_g3_red_us3bp_nodrain(tmp_path):
    """缺 drained 标记（释放后 B 未交付=未恢复）→ RED。"""
    case, sbx = build_case(tmp_path, store=True, fi_us3=150, plant="us3_nodrain")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_us3bp_leak(tmp_path):
    """drained<release（force 期间泄漏交付，违反零泄漏）→ RED。"""
    case, sbx = build_case(tmp_path, store=True, fi_us3=150, plant="us3_leak")
    assert _run(case, sbx, "positive") == 1


def test_g3_red_us3bp_risen_late(tmp_path):
    """risen>release（B 在释放后才到达，延迟半段不成立）→ RED。"""
    case, sbx = build_case(tmp_path, store=True, fi_us3=150, plant="us3_risen_late")
    assert _run(case, sbx, "positive") == 1


def test_g3_fatal_expected_wbarrier_pass(tmp_path):
    """超时注入：WBARRIER TIMEOUT 标记在且其后无 DUMP → PASS（失败退出实证）。"""
    case, sbx = build_case(tmp_path, fatal="wbarrier")
    assert _run(case, sbx, "fatal_expected", expect_timeout="wbarrier") == 0


def test_g3_fatal_expected_s4barrier_pass(tmp_path):
    case, sbx = build_case(tmp_path, fatal="s4barrier")
    assert _run(case, sbx, "fatal_expected", expect_timeout="s4barrier") == 0


def test_g3_fatal_expected_dump_after_is_red(tmp_path):
    """超时后仍出现 DUMP = 结束顺序被突破 → RED。"""
    case, sbx = build_case(tmp_path, fatal="s4barrier", plant="dump_after")
    assert _run(case, sbx, "fatal_expected", expect_timeout="s4barrier") == 1


# ── 任务2收口（2026-10-08 用户裁决，ADR-060 errata 10）：响应账目异常持续检查 ──

def test_g3_fatal_expected_respacct_pass(tmp_path):
    """边界注入负控新期望：RESPACCT 专属标记在（$display+$fatal 回显两处）
    且其后无 DUMP → PASS（负控准确命中新报警）。"""
    case, sbx = build_case(tmp_path, fatal="respacct")
    assert _run(case, sbx, "fatal_expected", expect_timeout="respacct") == 0


def test_g3_fatal_expected_respacct_dump_after_is_red(tmp_path):
    """RESPACCT 后仍出现 DUMP = 结束顺序被突破 → RED。"""
    case, sbx = build_case(tmp_path, fatal="respacct", plant="dump_after")
    assert _run(case, sbx, "fatal_expected", expect_timeout="respacct") == 1


def test_g3_fatal_expected_respacct_missing_is_red(tmp_path):
    """期望 respacct 但日志只有旧 S4 超时形态 = 未命中新报警 → RED
    （不得沿用旧死锁持到原超时的期望）。"""
    case, sbx = build_case(tmp_path, fatal="s4barrier")
    assert _run(case, sbx, "fatal_expected", expect_timeout="respacct") == 1


def test_g3_fatal_expected_barrier_absorbs_respacct_is_red(tmp_path):
    """裁决③：屏障超时腿出现 RESPACCT 专属标记不得被「预期屏障超时」通道
    吸收放行 → RED。"""
    case, sbx = build_case(tmp_path, fatal="s4barrier", add_respacct=True)
    assert _run(case, sbx, "fatal_expected", expect_timeout="s4barrier") == 1


def test_g3_red_positive_respacct_marker(tmp_path):
    """合法运行（positive store 绿基线 + RESPACCT 标记）= 误报/真异常 → RED
    且不发布。"""
    case, sbx = build_case(tmp_path, store=True, add_respacct=True)
    assert _run(case, sbx, "positive") == 1


def _ref_store_lines():
    stb, axi = s1.build_and_emit([{"lines": 16, "brst": 15}], ur_bank=2,
                                 ur_offset=0, gr_base=0x2000, smc_id_en=0)
    return {"stb": stb, "stb_axi": axi}


def test_g3_store_crosscheck_value_semantics_padding_ok():
    """STORE 互校：前导零填充差异值等价 → 通过（与 LOAD 互校同纪律）。"""
    ref = _ref_store_lines()
    padded = {"stb": [x.zfill(len(x) + 1) for x in ref["stb"]],
              "stb_axi": [x.zfill(len(x) + 1) for x in ref["stb_axi"]]}
    g3.cross_check_store_stage(padded, 16)  # 不抛即过


def test_g3_store_crosscheck_true_bitdiff_rejected():
    """STORE 互校：真比特差异必须响亮拒绝。"""
    ref = _ref_store_lines()
    bad = dict(ref)
    bad["stb"] = list(ref["stb"])
    bad["stb"][0] = format(int(ref["stb"][0], 16) ^ 0x10, "x")
    with pytest.raises(SystemExit):
        g3.cross_check_store_stage(bad, 16)


# ── 任务 1（用户 2026-10-07）：hbm_preload 自动恢复/校验 + TB 指纹 provenance ──
import hashlib  # noqa: E402

import hbm_preload as hp  # noqa: E402


def _mk_preload_case(tmp_path, *, tamper_expect=False, drop_snap=None):
    """合成最小预载场景：case/memory_init_snapshot/8 文件 + g3_expect 冻结
    哈希 + 陈旧 hbm_dir（a5a5 旧图案，drive-3 污染形态）。"""
    case = tmp_path / "case"
    snap = case / "memory_init_snapshot"
    hbm = tmp_path / "hbm"
    snap.mkdir(parents=True)
    hbm.mkdir()
    for i in range(1, 9):
        if drop_snap == i:
            continue
        (snap / f"memory_init{i}.hex").write_text(f"deadbeef{i:02x}\n")
        (hbm / f"memory_init{i}.hex").write_text("a5a50000\n")  # 陈旧预载
    files = {}
    for i in range(1, 9):
        p = snap / f"memory_init{i}.hex"
        if p.exists():
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            files[f"memory_init{i}.hex"] = ("00" * 32 if tamper_expect and i == 3 else h)
    (case / "g3_expect.json").write_text(json.dumps(
        {"schema": "m3lite.gate3_expect/v1", "hbm_preload": {"files": files}}))
    return str(case), str(hbm)


def test_hbm_check_detects_stale_preload(tmp_path):
    """drive-3 形态：全局预载陈旧（a5a5）必须被 check 检出（rc=1）。"""
    case, hbm = _mk_preload_case(tmp_path)
    assert hp.check(case, hbm, out=lambda *_: None) == 1


def test_hbm_restore_happy_then_check_clean(tmp_path):
    """restore 后 8/8 == 冻结快照；再 check 转 CLEAN（rc=0）。"""
    case, hbm = _mk_preload_case(tmp_path)
    assert hp.restore(case, hbm, out=lambda *_: None) == 0
    for i in range(1, 9):
        assert (tmp_path / "hbm" / f"memory_init{i}.hex").read_text() == f"deadbeef{i:02x}\n"
    assert hp.check(case, hbm, out=lambda *_: None) == 0


def test_hbm_restore_refuses_snapshot_expect_mismatch(tmp_path):
    """快照与 expect 冻结哈希不符 = 冻结证据不可信 → 拒跑且全局目录不被写。"""
    case, hbm = _mk_preload_case(tmp_path, tamper_expect=True)
    assert hp.restore(case, hbm, out=lambda *_: None) == 1
    assert (tmp_path / "hbm" / "memory_init1.hex").read_text() == "a5a50000\n"


def test_hbm_restore_refuses_incomplete_snapshot(tmp_path):
    """快照缺文件 = 不半恢复，拒跑。"""
    case, hbm = _mk_preload_case(tmp_path, drop_snap=5)
    assert hp.restore(case, hbm, out=lambda *_: None) == 1
    assert (tmp_path / "hbm" / "memory_init1.hex").read_text() == "a5a50000\n"


def test_hbm_check_missing_current_file(tmp_path):
    """全局目录缺文件 = MISSING-CURRENT 检出。"""
    case, hbm = _mk_preload_case(tmp_path)
    assert hp.restore(case, hbm, out=lambda *_: None) == 0
    os.remove(os.path.join(hbm, "memory_init8.hex"))
    assert hp.check(case, hbm, out=lambda *_: None) == 1


def test_tb_provenance_match_mismatch_none(tmp_path):
    """TB 指纹：一致=不 WARN；差异=WARN 且 tb_match=False；TB 缺席=None。"""
    sbx = tmp_path / "sbx"
    tb = sbx / "sim_multi_stage" / "tb_aiacc_top_v04_f_multi_stage.v"
    tb.parent.mkdir(parents=True)
    tb.write_text("module tb; endmodule\n")
    import hashlib as _hl
    now = _hl.sha256(tb.read_bytes()).hexdigest()
    rep = g3c.Report()
    prov = g3c.tb_provenance(str(sbx), now, rep)
    assert prov["tb_match"] is True and rep.warn == 0
    rep2 = g3c.Report()
    prov2 = g3c.tb_provenance(str(sbx), "00" * 32, rep2)
    assert prov2["tb_match"] is False and rep2.warn == 1
    rep3 = g3c.Report()
    prov3 = g3c.tb_provenance(str(tmp_path / "empty_sbx"), "00" * 32, rep3)
    assert prov3["tb_match"] is None and rep3.warn == 0


# ── 接力轮漏洞修补（2026-10-07，用户验收发现）静态锚定：run.sh 纪律 ──────────

RUN_SH = os.path.join(ROOT, "verification_v2", "tools", "m3lite_gate3_run.sh")


def _run_sh_text():
    with open(RUN_SH, encoding="utf-8") as f:
        return f.read()


def test_run_sh_gen_failfast_covers_all_five_legs():
    """修补①：五腿 gen 非零退出即 fail-fast——禁沿用旧产物（目录已存在时
    旧 g3_expect.json 会通过纯存在性检查，接力轮实测发现）。"""
    t = _run_sh_text()
    for var in ["GEN_POS", "GEN_NEG", "GEN_BP", "GEN_TOWB", "GEN_TOS4"]:
        assert "%s=$?" % var in t, "%s 退出码未捕获" % var
        assert '[ "$%s" -ne 0 ]' % var in t, "%s 无非零 fail-fast 检查" % var
    assert t.count("fail-fast 拒跑，禁沿用旧产物") == 5


def test_run_sh_aggregated_exit_code():
    """修补②：脚本最终退出码=五腿期望形态汇总（exit=0 ⇔ 全部 AS-EXPECTED；
    任何方向偏差含意外变绿均 exit=1 交人工复核）。"""
    t = _run_sh_text()
    assert 'LEG_FAILS=""' in t
    for leg in ["positive", "negative", "bp", "to_wb", "to_s4"]:
        assert 'LEG_FAILS="$LEG_FAILS %s"' % leg in t, "%s 腿未入汇总" % leg
        assert 'LEG_FAILS="$LEG_FAILS %s(preload-gate)"' % leg in t
    tail = t[t.rindex("=== 汇总"):]
    assert "ALL-LEGS-AS-EXPECTED" in tail
    assert 'if [ -z "$LEG_FAILS" ]; then' in tail
    assert "exit 0" in tail and "exit 1" in tail


def test_run_sh_leg_verdict_marks_complete():
    """每腿必须打印 AS-EXPECTED / UNEXPECTED 腿判（汇总依据，留痕可审计）。"""
    t = _run_sh_text()
    assert t.count("腿判") >= 10  # 5 腿 × (AS-EXPECTED + UNEXPECTED)
    # bp 腿信息性 positive 比对不得参与汇总判定（持死形态下必 RED）
    assert "信息性，留痕不判" in t
