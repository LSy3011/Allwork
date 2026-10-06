"""ADR-058 / M3-lite 门①：hbm_window_compare 对抗单测。

模式：合成最小 case 目录（生成器 materialize + 手工伪造 TB dump/sim.log），
先自检 GREEN 基线，再逐项注入故障断言 RED —— RED 必须来自注入而非夹具漂移。
1048576 行全量 dump 在单测里缩小：monkeypatch HBM_DUMP_LINES。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "verification_v2", "tools"))

import hbm_window_compare as C  # noqa: E402
import stb_solo_case_gen as G  # noqa: E402

_S1 = [dict(lines=16, brst=15)]
WIN0 = 0x1000 // 16          # 256
PRE = 4096
DUMP_N = 5120                # 单测缩小的 dump 行数（> PRE，覆盖 X 区）


def _write(path: str, rows: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(rows) + "\n")


@pytest.fixture()
def green_case(tmp_path, monkeypatch):
    """物化 GREEN 基线 case：dump = 「窗口已写 UR 图案 + 其余预载/零」的正确终态。"""
    monkeypatch.setattr(G, "HBM_INIT_DIR", str(tmp_path / "hbm_init"))
    monkeypatch.setattr(C, "HBM_DUMP_LINES", DUMP_N)
    case = str(tmp_path / "sandbox" / "test_atomic_ops" / "multi_inst_stage" / "c1")
    stb, axi = G.build_and_emit(_S1, 1, 0, 0x1000, 0)
    G.materialize(case, _S1, 1, 0, 0x1000, 0, stb, axi, write_hbm_init=False)

    # 正确终态：目标颗窗口=UR 图案，[0,PRE) 其余=预载，[PRE,DUMP_N)=0
    rows0 = [G.preload_pattern_line(k) for k in range(PRE)] + [G.ZERO_LINE] * (DUMP_N - PRE)
    for k in range(16):
        rows0[WIN0 + k] = G.ur_pattern_line(k)
    _write(os.path.join(case, "smc_0", "hbm_result_smc_0.hex"), rows0)
    for s in range(1, 8):
        rows = [G.mark_pattern_line(k) for k in range(PRE)] + [G.ZERO_LINE] * (DUMP_N - PRE)
        _write(os.path.join(case, f"smc_{s}", f"hbm_result_smc_{s}.hex"), rows)
    for b in range(8):
        ur = open(os.path.join(case, "smc_0", "ur_data", f"bank{b}.hex")).read().split()
        _write(os.path.join(case, "smc_0", f"ur_result_smc_0_bank{b}.hex"), ur)
    log_dir = tmp_path / "sandbox" / "arith_regression" / "test_log" / "multi_stage" \
        / "multi_inst_stage" / "c1"
    log_dir.mkdir(parents=True)
    (log_dir / "sim.log").write_text(
        "[TB_MULTI_STAGE] batch interrupt @ cycle 0 (INT=0x00000101 intid_hw=1 intid_sw=0)\n"
        "[TB_MULTI_STAGE] stage_0 done (intr cleared)\n")
    return case


def _invoke(case: str) -> int:
    argv = sys.argv
    sys.argv = ["hbm_window_compare.py", case]
    try:
        return C.main()
    finally:
        sys.argv = argv


def test_green_baseline(green_case, capsys):
    assert _invoke(green_case) == 0
    assert "✅" in capsys.readouterr().out


class TestRed:
    def test_a1_window_data_corrupt(self, green_case):
        p = os.path.join(green_case, "smc_0", "hbm_result_smc_0.hex")
        rows = open(p).read().split()
        rows[WIN0 + 3] = G.ZERO_LINE                     # 窗口内一行被毁
        _write(p, rows)
        assert _invoke(green_case) == 1

    def test_a2_address_shift(self, green_case):
        """窗口整体平移一行（地址错）→ A4a 预载区窗口外不变 必 RED。"""
        p = os.path.join(green_case, "smc_0", "hbm_result_smc_0.hex")
        rows = open(p).read().split()
        rows[WIN0 - 1] = G.ur_pattern_line(0)            # 数据写到窗口前一行
        rows[WIN0] = G.preload_pattern_line(WIN0)        # 窗口首行反而没被写
        _write(p, rows)
        assert _invoke(green_case) == 1

    def test_a3_count_short(self, green_case):
        p = os.path.join(green_case, "smc_0", "hbm_result_smc_0.hex")
        rows = open(p).read().split()
        rows[WIN0 + 15] = G.preload_pattern_line(WIN0 + 15)  # 末行没写到（数量少一）
        _write(p, rows)
        assert _invoke(green_case) == 1

    def test_a4b_tail_nonzero(self, green_case):
        p = os.path.join(green_case, "smc_0", "hbm_result_smc_0.hex")
        rows = open(p).read().split()
        rows[PRE + 100] = "deadbeef" + "0" * 24          # X 区被写
        _write(p, rows)
        assert _invoke(green_case) == 1

    def test_a4c_en_leak(self, green_case):
        """smc_id_en 泄漏：smc_3 窗口被写 X（dump 0）→ 标记图案被破坏 → RED。"""
        p = os.path.join(green_case, "smc_3", "hbm_result_smc_3.hex")
        rows = open(p).read().split()
        for k in range(16):
            rows[WIN0 + k] = G.ZERO_LINE
        _write(p, rows)
        assert _invoke(green_case) == 1

    def test_a5_timeout_marker(self, green_case):
        log = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(green_case))),
                           "arith_regression", "test_log", "multi_stage",
                           "multi_inst_stage", "c1", "sim.log")
        with open(log, "a") as f:
            f.write("[ERR] smi_wait_and_clear_intr timeout after 500000 cycles\n")
        assert _invoke(green_case) == 1

    def test_a5_no_intr_marker(self, green_case):
        log = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(green_case))),
                           "arith_regression", "test_log", "multi_stage",
                           "multi_inst_stage", "c1", "sim.log")
        with open(log, "w") as f:
            f.write("[TB_MULTI_STAGE] nothing happened\n")
        assert _invoke(green_case) == 1

    def test_a6_ur_corrupt(self, green_case):
        p = os.path.join(green_case, "smc_0", "ur_result_smc_0_bank1.hex")
        rows = open(p).read().split()
        rows[5] = G.ZERO_LINE                            # STB 把源 bank 读穿了
        _write(p, rows)
        assert _invoke(green_case) == 1

    def test_missing_dump_is_red(self, green_case):
        os.remove(os.path.join(green_case, "smc_0", "hbm_result_smc_0.hex"))
        assert _invoke(green_case) == 1
