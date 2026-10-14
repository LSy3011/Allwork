#!/usr/bin/env python3
"""8-SMC 等长分片（equal_8smc_v1）鉴别性测试（五步令步③，2026-10-14）。

立法序锚点：
- 失败形态（改规则前实录）：g2/s1 守卫对 smc_id_en=7 响拒
  「[LDB-GEN-GUARD] M3-lite 单 SMC 复核要求 smc_id_en=0，收到 7」/STB 同型；
- 新规则：smc_id_en ∈ {0,7}（0=M3-lite 单 SMC 既有形态，7=equal_8smc_v1
  八目标广播），其余值（1..6, 8..31=未复核部分使能形态）一律响拒；
- 回归：既有 v1 单 SMC 路径行为零改动（test_unified_lcs.py 全套不动）。

规格：docs/8smc_equal_partition_spec_20261014.md（M1-M5 / C1-C5 / N1-N3）。
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, "src"))
sys.path.append(os.path.join(ROOT, "verification_v2", "tools"))

import ldb_review_case_gen as g2            # noqa: E402
import stb_solo_case_gen as s1              # noqa: E402
import decode_case_smc_enablement as dec    # noqa: E402
from check_program_manifest import Checker as PMChecker  # noqa: E402
from unified_lcs_case_gen import (          # noqa: E402
    PROGRAM_MANIFEST_SCHEMA_V2, build_unified_case)
from unified_lcs_compare import Compare as LcsCompare  # noqa: E402

N_PER = 8                       # 每 SMC 8 元素（n_lines=2，最小合法）
N_LINES = N_PER // 4
SMC_NUM = 8
X_VALS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
O_VALS = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
ALPHA = 2.5
FORMULA = "alpha * (x + other)"


def _f32(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _pack(lanes) -> str:
    v = 0
    for i, w in enumerate(lanes):
        v |= (w & 0xFFFFFFFF) << (32 * i)
    return f"{v:032x}"


def _lines(vals):
    return [_pack([_f32(v) for v in vals[4 * k:4 * k + 4]])
            for k in range(len(vals) // 4)]


def _w(p: Path, lines) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------
# 守卫规则（立法序：先记失败形态 → 本测试鉴别 → 改规则 → 回归）
# ----------------------------------------------------------------------
class TestGuards:
    DESCS = [{"lines": 2, "brst": 1}]

    @pytest.mark.parametrize("en", [0, 7])
    def test_ldb_guard_accepts(self, en):
        g2.guard("t", self.DESCS, 0, 0, 0x1000, en)   # 不响拒即通过

    @pytest.mark.parametrize("en", [1, 3, 6, 8, 31])
    def test_ldb_guard_rejects_partial(self, en):
        with pytest.raises(SystemExit):
            g2.guard("t", self.DESCS, 0, 0, 0x1000, en)

    @pytest.mark.parametrize("en", [0, 7])
    def test_stb_guard_accepts(self, en):
        s1.guard("t", self.DESCS, 2, 0, 0x4000, en)

    @pytest.mark.parametrize("en", [1, 3, 6, 8, 31])
    def test_stb_guard_rejects_partial(self, en):
        with pytest.raises(SystemExit):
            s1.guard("t", self.DESCS, 2, 0, 0x4000, en)


# ----------------------------------------------------------------------
# 合成 build_copy（scale_sum 形态：x@bank0 other@bank1 alpha@bank3 y@bank2）
# ----------------------------------------------------------------------
def _make_build_copy(build: Path) -> None:
    import numpy as np
    sim_cfg = {"tensors": [
        {"name": "x", "role": "input", "bank": 0, "addr": 0,
         "shape": [N_LINES, 4]},
        {"name": "other", "role": "input", "bank": 1, "addr": 0,
         "shape": [N_LINES, 4]},
        {"name": "alpha", "role": "param", "bank": 3, "addr": 0,
         "shape": [N_LINES, 4]},
        {"name": "y", "role": "output", "bank": 2, "addr": 0,
         "shape": [N_LINES, 4]},
    ]}
    _w(build / "sim_config.json", [json.dumps(sim_cfg)])
    y_ref = (np.float32(ALPHA)
             * (np.asarray(X_VALS, dtype=np.float32)
                + np.asarray(O_VALS, dtype=np.float32))).astype(np.float32)
    src = {0: _lines(X_VALS), 1: _lines(O_VALS),
           3: [_pack([_f32(ALPHA)] * 4)] + [g2.ZERO_LINE] * (N_LINES - 1)}
    for b in range(8):
        pre = [g2.ZERO_LINE] * 256
        fin = [g2.ZERO_LINE] * 256
        if b in src:
            pre[:N_LINES] = src[b]
            fin[:N_LINES] = src[b]
        elif b == 2:
            fin[:N_LINES] = _lines([float(v) for v in y_ref])
        _w(build / "smc_0" / "ur_data" / f"bank{b}.hex", pre)
        _w(build / "smc_0" / "output_bank" / f"bank{b}_final.hex", fin)
    _w(build / "smc_0" / "iq_fpmul.hex", ["00000000000000000000000000"])


def _gen_case(tmp_path: Path) -> Path:
    build = tmp_path / "build"
    _make_build_copy(build)
    case = tmp_path / "case"
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        build_unified_case(build, case, N_PER, tmp_path / "tb.v",
                           op_name="scale_sum", formula=FORMULA,
                           partition="equal_8smc")
    finally:
        os.chdir(cwd)
    return case


class TestCaseGenPartition:
    def test_manifest_v2_fields(self, tmp_path):
        case = _gen_case(tmp_path)
        man = json.loads((case / "program_manifest.json").read_text())
        assert man["schema_version"] == PROGRAM_MANIFEST_SCHEMA_V2
        part = man["partition"]
        assert part["scheme"] == "equal_8smc_v1"
        assert part["n_total"] == SMC_NUM * N_PER
        assert part["n_per"] == N_PER
        assert part["smc_count"] == SMC_NUM
        assert man["n"] == SMC_NUM * N_PER
        assert man["n_lines"] == N_LINES
        for st in man["stages"]:
            assert st["active_smcs"] == list(range(SMC_NUM)), st["kind"]
            assert "active_smc" not in st
        # M2 结构：hbm_region_sha256 每 tensor per-SMC 8 值
        for name, per in man["hbm_region_sha256"].items():
            assert sorted(per) == [f"smc_{k}" for k in range(SMC_NUM)], name
        # 输入张量 8 份哈希两两可区分（数据可区分性的结构面）
        x_shas = set(man["hbm_region_sha256"]["x"].values())
        assert len(x_shas) == SMC_NUM
        # M3：golden_files 枚举齐 8×8 bank
        assert len(man["golden_files"]) == SMC_NUM * 8

    def test_load_store_decode_en7(self, tmp_path):
        case = _gen_case(tmp_path)
        rep = dec.decode_case(str(case))
        for st in rep["stages"]:
            if st["stage"] in ("stage_0", f"stage_{len(rep['stages']) - 1}"):
                for q in st["queues"]:
                    assert q["smc_id_en_vld1"] == [7], (st["stage"], q["file"])

    def test_golden_windows_pairwise_distinct(self, tmp_path):
        case = _gen_case(tmp_path)
        wins = []
        for k in range(SMC_NUM):
            fin = (case / f"smc_{k}" / "output_bank"
                   / "bank2_final.hex").read_text().split()
            wins.append(tuple(fin[:N_LINES]))
        for i in range(SMC_NUM):
            for j in range(i + 1, SMC_NUM):
                assert wins[i] != wins[j], f"smc_{i} 与 smc_{j} golden 窗全等"

    def test_checker_v2_green(self, tmp_path):
        case = _gen_case(tmp_path)
        chk = PMChecker(case)
        assert chk.run() == 0, chk.bad

    def test_wrong_partition_rejected(self, tmp_path):
        build = tmp_path / "build"
        _make_build_copy(build)
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            with pytest.raises(SystemExit):
                build_unified_case(build, tmp_path / "c2", N_PER,
                                   tmp_path / "tb.v", op_name="scale_sum",
                                   formula=FORMULA, partition="ragged_8smc")
        finally:
            os.chdir(cwd)


class TestManifestCheckerV2Reds:
    def test_missing_golden_file_red(self, tmp_path):
        case = _gen_case(tmp_path)
        (case / "smc_3" / "output_bank" / "bank2_final.hex").unlink()
        chk = PMChecker(case)
        assert chk.run() == 1
        assert any("smc_3" in m for m in chk.bad)

    def test_forced_equal_goldens_red(self, tmp_path):
        case = _gen_case(tmp_path)
        src = case / "smc_0" / "output_bank" / "bank2_final.hex"
        (case / "smc_5" / "output_bank" / "bank2_final.hex").write_text(
            src.read_text(), encoding="utf-8")
        # golden 文件被改后 sha256 锚也会不符；两案任一命中即 RED，
        # 但 M4 可区分性必须独立在违例清单中（不只哈希锚）
        chk = PMChecker(case)
        assert chk.run() == 1
        assert any("可区分" in m or "全等" in m for m in chk.bad), chk.bad

    def test_load_en0_tamper_red(self, tmp_path):
        case = _gen_case(tmp_path)
        # 把 LOAD 重发射回 en=0 形态（语义篡改）→ M1 解码一致性必须 RED
        from unified_lcs_case_gen import chunk_descs
        descs = chunk_descs(N_LINES)
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            ls, xs = [], []
            for bank, base in ((0, 0x1000), (1, 0x2000), (3, 0x3000)):
                a, b = g2.build_and_emit(descs, bank, 0, base, 0)
                ls += a
                xs += b
        finally:
            os.chdir(cwd)
        _w(case / "smi_iq_inst" / "stage_0" / "iq_ldb.hex", ls)
        chk = PMChecker(case)
        assert chk.run() == 1
        assert any("M1" in m or "smc_id_en" in m for m in chk.bad), chk.bad


# ----------------------------------------------------------------------
# compare v2（C1 缺席即 RED / C2 可区分 / 逐 SMC 数据面）
# ----------------------------------------------------------------------
def _simulate_hw_dumps(case: Path) -> None:
    """模拟硬件完美执行：dump=golden、HBM 窗=golden 输出（compare 的 GREEN 前提）。"""
    man = json.loads((case / "program_manifest.json").read_text())
    out_e = next(e for e in man["hbm_layout"] if e["direction"] == "d2h")
    out_l0 = out_e["byte_base"] // 16
    for k in range(SMC_NUM):
        init = (case / "memory_init_snapshot"
                / f"memory_init{k + 1}.hex").read_text().split()
        for b in range(8):
            fin = (case / f"smc_{k}" / "output_bank"
                   / f"bank{b}_final.hex").read_text().split()
            _w(case / f"smc_{k}" / f"ur_result_smc_{k}_bank{b}.hex", fin)
        hbm = list(init)
        gold = (case / f"smc_{k}" / "output_bank"
                / f"bank{out_e['bank']}_final.hex").read_text().split()
        hbm[out_l0:out_l0 + N_LINES] = gold[:N_LINES]
        _w(case / f"smc_{k}" / f"hbm_result_smc_{k}.hex", hbm)


def _run_compare(case: Path, tmp_path: Path) -> LcsCompare:
    instr = tmp_path / "physical_instructions.json"
    if not instr.is_file():
        # 最小 COMPUTE 计数 fixture：vout0 BODY vld=1 × n_lines
        # （live_bodily_writes 读 JSON 行列表）
        rows = [{"iq_name": "vout0", "stage": "BODY", "header": {"VLD": 1}}
                ] * N_LINES
        instr.write_text(json.dumps(rows), encoding="utf-8")
    return LcsCompare(case, [instr])


class TestCompareV2:
    def test_green(self, tmp_path):
        case = _gen_case(tmp_path)
        _simulate_hw_dumps(case)
        c = _run_compare(case, tmp_path)
        assert c.run() == 0, c.bad

    def test_missing_result_file_red(self, tmp_path):
        """N2：缺一个目标结果文件 → RED（非 SKIP）。"""
        case = _gen_case(tmp_path)
        _simulate_hw_dumps(case)
        (case / "smc_6" / "hbm_result_smc_6.hex").unlink()
        c = _run_compare(case, tmp_path)
        assert c.run() == 1
        assert any("smc_6" in m for m in c.bad), c.bad

    def test_misplaced_slice_red(self, tmp_path):
        """N1：错置一个 SMC 的结果（两 SMC 输出对调）→ RED 且报对 SMC 号。"""
        case = _gen_case(tmp_path)
        _simulate_hw_dumps(case)
        p3 = case / "smc_3" / "hbm_result_smc_3.hex"
        p5 = case / "smc_5" / "hbm_result_smc_5.hex"
        h3, h5 = p3.read_text(), p5.read_text()
        p3.write_text(h5, encoding="utf-8")
        p5.write_text(h3, encoding="utf-8")
        c = _run_compare(case, tmp_path)
        assert c.run() == 1
        assert any("smc_3" in m for m in c.bad), c.bad

    def test_eightfold_copy_red(self, tmp_path):
        """N3：8 路复制同一份输出（可区分性破坏）→ RED。"""
        case = _gen_case(tmp_path)
        _simulate_hw_dumps(case)
        src = (case / "smc_0" / "output_bank" / "bank2_final.hex").read_text()
        for k in range(1, SMC_NUM):
            (case / f"smc_{k}" / "output_bank"
             / "bank2_final.hex").write_text(src, encoding="utf-8")
            fin = src.split()
            _w(case / f"smc_{k}" / "ur_result_smc_{k}_bank2.hex", fin)
            man = json.loads((case / "program_manifest.json").read_text())
            out_e = next(e for e in man["hbm_layout"]
                         if e["direction"] == "d2h")
            hbm = (case / f"smc_{k}"
                   / f"hbm_result_smc_{k}.hex").read_text().split()
            hbm[out_e["byte_base"] // 16:
                out_e["byte_base"] // 16 + N_LINES] = fin[:N_LINES]
            _w(case / f"smc_{k}" / f"hbm_result_smc_{k}.hex", hbm)
        c = _run_compare(case, tmp_path)
        assert c.run() == 1


# ----------------------------------------------------------------------
# A′（2026-10-14 用户裁定）：equal_8smc_v1 ⇒ en=7 且 intlv=通道步长 绑定；
# 解码产物字段无截断 + 逐 SMC 地址窗核验；单 SMC 路径逐字节不变。
# 机制锚：addr_k=gr_base+k*intlv（ldb_ur_axi_conv.v:392-425 /
# stb_ur_axi_conv.v:360-377）；atcbmc300 按地址选通道
# （noc/rtl/include/atcbmc300_config.vh:172-201，SLV2..9=ch0..7 各 256MB）。
# ----------------------------------------------------------------------
class TestIntlvBinding:
    DESCS = [{"lines": 2, "brst": 1}]

    def _mods(self):
        import iq_field_diff as IFD
        return IFD.load_modules_yaml(os.path.join(ROOT, "hw_config",
                                                  "modules.yaml"))

    def test_partition_transport_binding(self):
        from unified_lcs_case_gen import (_partition_transport, CHANNEL_STRIDE,
                                          SMC_COUNT)
        assert _partition_transport(None) == (0, 0)
        assert _partition_transport("equal_8smc") == (SMC_COUNT - 1,
                                                      CHANNEL_STRIDE)
        with pytest.raises(SystemExit):
            _partition_transport("bogus_scheme")

    def test_intlv_field_roundtrip_no_truncation(self):
        """发射 intlv=通道步长 → 解码逐字段读回 == 原值（40bit 无截断），双侧。"""
        from unified_lcs_case_gen import CHANNEL_STRIDE
        import iq_field_diff as IFD
        mods = self._mods()
        _, axi = g2.build_and_emit(self.DESCS, 0, 0, 0x1000, 7, CHANNEL_STRIDE)
        d = IFD.decode(axi[0], "LDB_AXI", mods)
        assert d["body"]["GR_INTLV_ADDR"] == CHANNEL_STRIDE
        assert d["body"]["GR_BASE_ADDR"] == 0x1000
        _, axi = s1.build_and_emit(self.DESCS, 2, 0, 0x4000, 7, CHANNEL_STRIDE)
        d = IFD.decode(axi[0], "STB_AXI", mods)
        assert d["body"]["GR_INTLV_ADDR"] == CHANNEL_STRIDE
        assert d["body"]["GR_BASE_ADDR"] == 0x4000

    def test_single_smc_default_byte_identical(self):
        """单 SMC 回归：缺省 intlv 与显式 0 逐字节一致，且解码字段==0。"""
        import iq_field_diff as IFD
        mods = self._mods()
        a_l, a_x = g2.build_and_emit(self.DESCS, 0, 0, 0x1000, 0)
        b_l, b_x = g2.build_and_emit(self.DESCS, 0, 0, 0x1000, 0, 0)
        assert (a_l, a_x) == (b_l, b_x)
        assert IFD.decode(a_x[0], "LDB_AXI", mods)["body"]["GR_INTLV_ADDR"] == 0
        a_s, a_ax = s1.build_and_emit(self.DESCS, 2, 0, 0x4000, 0)
        b_s, b_ax = s1.build_and_emit(self.DESCS, 2, 0, 0x4000, 0, 0)
        assert (a_s, a_ax) == (b_s, b_ax)
        assert IFD.decode(a_ax[0], "STB_AXI", mods)["body"]["GR_INTLV_ADDR"] == 0

    @pytest.mark.parametrize("bad", [-16, 1 << 40, 8])
    def test_guard_intlv_field_sanity(self, bad):
        with pytest.raises(SystemExit):
            g2.guard("t", self.DESCS, 0, 0, 0x1000, 7, bad)
        with pytest.raises(SystemExit):
            s1.guard("t", self.DESCS, 2, 0, 0x4000, 7, bad)

    def test_window_checker_accepts_valid(self):
        from unified_lcs_case_gen import (_check_v2_axi_windows, CHANNEL_STRIDE)
        _, axi = g2.build_and_emit(self.DESCS, 0, 0, 0x1000, 7, CHANNEL_STRIDE)
        _check_v2_axi_windows(axi, "LDB_AXI", CHANNEL_STRIDE)   # 不响拒即通过
        _, axi = s1.build_and_emit(self.DESCS, 2, 0, 0x5000, 7, CHANNEL_STRIDE)
        _check_v2_axi_windows(axi, "STB_AXI", CHANNEL_STRIDE)

    def test_window_checker_rejects_wrong_stride(self):
        """错误步长被拒绝：intlv=半步长 → 解码值 != 绑定步长 → 响拒（双侧）。"""
        from unified_lcs_case_gen import (_check_v2_axi_windows, CHANNEL_STRIDE)
        _, axi = g2.build_and_emit(self.DESCS, 0, 0, 0x1000, 7,
                                   CHANNEL_STRIDE // 2)
        with pytest.raises(SystemExit, match="GR_INTLV_ADDR"):
            _check_v2_axi_windows(axi, "LDB_AXI", CHANNEL_STRIDE)
        _, axi = s1.build_and_emit(self.DESCS, 2, 0, 0x5000, 7,
                                   CHANNEL_STRIDE // 2)
        with pytest.raises(SystemExit, match="GR_INTLV_ADDR"):
            _check_v2_axi_windows(axi, "STB_AXI", CHANNEL_STRIDE)

    def test_window_checker_rejects_cross_channel(self):
        """burst 末地址跨入下一通道窗 → 响拒（通道0 顶界正面构造）。"""
        from unified_lcs_case_gen import (_check_v2_axi_windows, CHANNEL_STRIDE)
        _, axi = g2.build_and_emit([{"lines": 16, "brst": 15}], 0, 0,
                                   CHANNEL_STRIDE - 0xF0, 7, CHANNEL_STRIDE)
        with pytest.raises(SystemExit, match="地址窗越界"):
            _check_v2_axi_windows(axi, "LDB_AXI", CHANNEL_STRIDE)

    def test_case_v2_intlv_in_manifest_and_instructions(self, tmp_path):
        """端到端：v2 case manifest 记步长 + 指令解码逐 SMC 窗全合法 + M6 绿。"""
        import iq_field_diff as IFD
        from unified_lcs_case_gen import CHANNEL_STRIDE
        mods = self._mods()
        case = _gen_case(tmp_path)
        man = json.loads((case / "program_manifest.json").read_text())
        assert man["partition"]["channel_stride"] == CHANNEL_STRIDE
        assert "addr_rule" in man["partition"]
        n_st = len(man["stages"])
        for sid, unit, fn in ((0, "LDB_AXI", "iq_ldb_axi.hex"),
                              (n_st - 1, "STB_AXI", "iq_stb_axi.hex")):
            for row in (case / "smi_iq_inst" / f"stage_{sid}" / fn
                        ).read_text().split():
                d = IFD.decode(row, unit, mods)
                assert d["body"]["GR_INTLV_ADDR"] == CHANNEL_STRIDE
        chk = PMChecker(case)
        assert chk.run() == 0, chk.bad

    def test_channel_stride_tamper_red(self, tmp_path):
        """manifest 步长篡改 → M5 绑定检查 RED。"""
        case = _gen_case(tmp_path)
        man_p = case / "program_manifest.json"
        man = json.loads(man_p.read_text())
        man["partition"]["channel_stride"] = 0x08000000
        man_p.write_text(json.dumps(man, indent=2), encoding="utf-8")
        chk = PMChecker(case)
        assert chk.run() == 1
        assert any("channel_stride" in m for m in chk.bad), chk.bad
