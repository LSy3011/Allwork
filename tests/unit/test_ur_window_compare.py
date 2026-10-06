"""ADR-059 v2 / M3-lite 门②：ur_window_compare 对抗单测（防假绿）。

方法：合成「全绿」的 TB 产物现场（dump/trace/sim.log），先证 GREEN 可过；
再逐项注入故障，断言对应断言名进入 fails 且退出码 1（RED 按预期翻转）。
覆盖修正②③新增断言：B4c 独立 sentinel′、B5b 实际写次数、B5c 完成时序锚点。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "verification_v2", "tools"))

import ldb_review_case_gen as G  # noqa: E402
import ur_window_compare as C  # noqa: E402

BANK, TOTAL, UR_OFF, GR_BASE = 1, 16, 0, 0x1000
CASE = "t_ldb_green"
DESCS = [dict(lines=16, brst=15)]
WIN0 = GR_BASE // 16
# trace 写落在 cycle 100..100+TOTAL-1；INTR_ASSERT 精确锚点在其后（gap=14>0）；
# stage_0 done 清除点再后（轮询+ack 开销），二者同 iq_probe_cycle 计数域。
NOTIFY_CYCLE = 100 + TOTAL + 14
ANCHOR_CYCLE = NOTIFY_CYCLE + 1970

# 1M 行 HBM dump 内容模块级只建一次（写盘在每个用例的 tmp_path 各一份）
_HBM_ROWS = None


def _hbm_rows() -> list[str]:
    global _HBM_ROWS
    if _HBM_ROWS is None:
        rows = [C.ZERO_LINE] * C.HBM_DUMP_LINES
        for k in range(G.HBM_PRELOAD_LINES):
            rows[k] = G.preload_pattern_line(k)
        for k in range(TOTAL):
            rows[WIN0 + k] = G.src_pattern_line(k)
        _HBM_ROWS = rows
    return _HBM_ROWS


def _write(path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_green(tmp_path) -> "tuple[object, object]":
    """生成 case 本体 + 全绿 TB 产物。返回 (sbx, case_dir)。"""
    sbx = tmp_path / "sbx"
    case_dir = sbx / "test_atomic_ops" / "multi_inst_stage" / CASE
    ldb, axi = G.build_and_emit(DESCS, BANK, UR_OFF, GR_BASE, 0)
    G.materialize(str(case_dir), DESCS, BANK, UR_OFF, GR_BASE, 0, ldb, axi,
                  write_hbm_init=False)
    # TB dump：smc_0 目标 bank = 期望终态，其余 sentinel；smc_1..7 = sentinel′
    for b in range(8):
        rows = [G.sentinel_line(b, k) for k in range(G.UR_BANK_DEPTH)]
        if b == BANK:
            for k in range(TOTAL):
                rows[k] = G.src_pattern_line(k)
        _write(case_dir / "smc_0" / f"ur_result_smc_0_bank{b}.hex", rows)
    for i in range(1, 8):
        for b in range(8):
            _write(case_dir / f"smc_{i}" / f"ur_result_smc_{i}_bank{b}.hex",
                   [G.sentinel_aux_line(i, b, k) for k in range(G.UR_BANK_DEPTH)])
    _write(case_dir / "smc_0" / "hbm_result_smc_0.hex", _hbm_rows())
    # trace：恰 total 条 commit 写，(addr,wdata) 与窗口一一对应
    trace = ["# cycle smc bank we addr wbe wdata"]
    for k in range(TOTAL):
        trace.append(f"{100 + k} 0 {BANK} 1 {k} ffff {G.src_pattern_line(k)}")
    _write(case_dir / "ur_write_trace.log", trace)
    # sim.log：完成标记 + INTR_ASSERT 精确锚点 + stage_0 done 清除点
    sim = (tmp_path / "sbx" / "arith_regression" / "test_log" / "multi_stage"
           / "multi_inst_stage" / CASE / "sim.log")
    _write(sim, [f"[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle {NOTIFY_CYCLE}",
                 "[TB_MULTI_STAGE] batch interrupt @ cycle 5 @time 100 @probe_cycle 2000 (INT=0x1)",
                 f"[TB_MULTI_STAGE] stage_0 done (intr cleared) @time 200 @probe_cycle {ANCHOR_CYCLE}"])
    return sbx, case_dir


def cmp_fails(case_dir, capsys) -> "tuple[int, list[str]]":
    argv = sys.argv
    try:
        sys.argv = ["ur_window_compare.py", str(case_dir)]
        rc = C.main()
    finally:
        sys.argv = argv
    out = capsys.readouterr().out
    fails = [l.split("] ", 1)[1].split("：", 1)[0].split(" (")[0]
             for l in out.splitlines() if "[FAIL]" in l]
    return rc, fails


def _flip_line(path, idx, val) -> None:
    lines = path.read_text().split()
    lines[idx] = val
    _write(path, lines)


class TestGreen:
    def test_green_passes(self, tmp_path, capsys):
        _, case_dir = build_green(tmp_path)
        rc, fails = cmp_fails(case_dir, capsys)
        assert rc == 0 and fails == []


class TestRedInjections:
    def test_b1_window_data_wrong(self, tmp_path, capsys):
        _, cd = build_green(tmp_path)
        _flip_line(cd / "smc_0" / f"ur_result_smc_0_bank{BANK}.hex", 3, G.ZERO_LINE)
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and "B1 窗口数据==源图案" in fails

    def test_b2_neighbor_stained(self, tmp_path, capsys):
        _, cd = build_green(tmp_path)
        _flip_line(cd / "smc_0" / f"ur_result_smc_0_bank{BANK}.hex", TOTAL, G.src_pattern_line(99))
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B2") for f in fails)

    def test_b3_count_short(self, tmp_path, capsys):
        _, cd = build_green(tmp_path)
        _flip_line(cd / "smc_0" / f"ur_result_smc_0_bank{BANK}.hex",
                   TOTAL - 1, G.sentinel_line(BANK, TOTAL - 1))
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B3 异变行集合") for f in fails)

    def test_b4b_other_bank_stained(self, tmp_path, capsys):
        _, cd = build_green(tmp_path)
        _flip_line(cd / "smc_0" / "ur_result_smc_0_bank3.hex", 10, G.src_pattern_line(0))
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and "B4b smc_0 bank3==sentinel" in fails

    def test_b4c_aux_deviation(self, tmp_path, capsys):
        """修正②：smc_1..7 偏离各自 sentinel′（装载失败或泄漏写）必 RED。"""
        _, cd = build_green(tmp_path)
        _flip_line(cd / "smc_3" / "ur_result_smc_3_bank5.hex", 7, G.ZERO_LINE)
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and "B4c smc_3 bank5==sentinel′" in fails

    def test_b4c_trace_aux_write(self, tmp_path, capsys):
        _, cd = build_green(tmp_path)
        trace = (cd / "ur_write_trace.log").read_text()
        _write(cd / "ur_write_trace.log",
               trace.splitlines() + [f"150 2 0 1 0 ffff {G.src_pattern_line(0)}"])
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B4c'") for f in fails)

    def test_b5a_marker_missing(self, tmp_path, capsys):
        sbx, cd = build_green(tmp_path)
        sim = (sbx / "arith_regression" / "test_log" / "multi_stage"
               / "multi_inst_stage" / CASE / "sim.log")
        _write(sim, [f"[TB_MULTI_STAGE] stage_0 done (intr cleared) @time 1 @probe_cycle {ANCHOR_CYCLE}"])
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and "B5a 完成通知到达" in fails

    def test_b5a_timeout(self, tmp_path, capsys):
        sbx, cd = build_green(tmp_path)
        sim = (sbx / "arith_regression" / "test_log" / "multi_stage"
               / "multi_inst_stage" / CASE / "sim.log")
        lines = sim.read_text().splitlines()
        _write(sim, lines + ["[ERR] smi_wait_and_clear_intr timeout after 500000 cycles"])
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and "B5a 无阶段超时" in fails

    def test_b5b_count_short(self, tmp_path, capsys):
        """修正③：实际写次数独立于终态行数——少一条 commit 写必 RED。"""
        _, cd = build_green(tmp_path)
        lines = (cd / "ur_write_trace.log").read_text().splitlines()
        _write(cd / "ur_write_trace.log", lines[:-1])
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B5b ") for f in fails)

    def test_b5b_wdata_wrong(self, tmp_path, capsys):
        _, cd = build_green(tmp_path)
        lines = (cd / "ur_write_trace.log").read_text().splitlines()
        parts = lines[5].split()
        parts[6] = G.ZERO_LINE
        lines[5] = " ".join(parts)
        _write(cd / "ur_write_trace.log", lines)
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B5b'") for f in fails)

    def test_b5c_write_not_before_notify(self, tmp_path, capsys):
        """修正③（精确锚点）：末次写与 INTR_ASSERT 同沿 = 歧义 → 响亮 RED。"""
        _, cd = build_green(tmp_path)
        lines = (cd / "ur_write_trace.log").read_text().splitlines()
        parts = lines[TOTAL].split()          # 末条数据写（行 0 是注释头）
        parts[0] = str(NOTIFY_CYCLE)          # max_wr == N
        lines[TOTAL] = " ".join(parts)
        _write(cd / "ur_write_trace.log", lines)
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B5c 末次写严格先于通知") for f in fails)

    def test_b5c_residual_write_after_notify(self, tmp_path, capsys):
        """修正③：通知沿之后仍有 UR 写 → RED。"""
        _, cd = build_green(tmp_path)
        lines = (cd / "ur_write_trace.log").read_text().splitlines()
        lines.append(f"{NOTIFY_CYCLE + 5} 0 {BANK} 1 0 ffff {G.src_pattern_line(0)}")
        _write(cd / "ur_write_trace.log", lines)
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and "B5c 通知后零残留写" in fails

    def test_b5c_anchor_missing(self, tmp_path, capsys):
        sbx, cd = build_green(tmp_path)
        sim = (sbx / "arith_regression" / "test_log" / "multi_stage"
               / "multi_inst_stage" / CASE / "sim.log")
        _write(sim, ["[TB_MULTI_STAGE] batch interrupt @ cycle 5 @time 1 @probe_cycle 9 (INT=0x1)"])
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B5c 精确通知锚点") for f in fails)

    def test_b5c_anchor_duplicated(self, tmp_path, capsys):
        """单 stage 用例出现两条 INTR_ASSERT → 锚点不唯一，响亮 RED。"""
        sbx, cd = build_green(tmp_path)
        sim = (sbx / "arith_regression" / "test_log" / "multi_stage"
               / "multi_inst_stage" / CASE / "sim.log")
        lines = sim.read_text().splitlines()
        _write(sim, lines + [f"[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle {NOTIFY_CYCLE + 4000}"])
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B5c 精确通知锚点") for f in fails)

    def test_b5c_anchor_after_clear(self, tmp_path, capsys):
        """INTR_ASSERT 晚于 stage_0 done 清除点 → 锚点自相矛盾，RED。"""
        sbx, cd = build_green(tmp_path)
        sim = (sbx / "arith_regression" / "test_log" / "multi_stage"
               / "multi_inst_stage" / CASE / "sim.log")
        lines = sim.read_text().splitlines()
        lines[0] = f"[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle {ANCHOR_CYCLE + 1}"
        _write(sim, lines)
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B5c 锚点一致性") for f in fails)

    def test_b6_hbm_corrupted(self, tmp_path, capsys):
        _, cd = build_green(tmp_path)
        _flip_line(cd / "smc_0" / "hbm_result_smc_0.hex", 2000, G.ZERO_LINE)
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B6 HBM 全域") for f in fails)

    def test_missing_dump(self, tmp_path, capsys):
        _, cd = build_green(tmp_path)
        os.remove(cd / "smc_0" / f"ur_result_smc_0_bank{BANK}.hex")
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and any(f.startswith("B0") for f in fails)

    def test_missing_trace(self, tmp_path, capsys):
        _, cd = build_green(tmp_path)
        os.remove(cd / "ur_write_trace.log")
        rc, fails = cmp_fails(cd, capsys)
        assert rc == 1 and "B5 ur_write_trace.log 存在" in fails
