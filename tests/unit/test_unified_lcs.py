"""ADR-061 统一入口四件套的红绿契约测试（全部 hermetic，不依赖 e2e/VCS 产物）。

覆盖：
  * chunk_descs 分块与 brst=0 禁区响亮拒绝（DEBT-050）；
  * parse_layout D1 守卫（n%4 / addr / shape / bank 交叠 / role / 段链）；
  * check_program_manifest：合成最小合法 case 必须 GREEN；四类单点破坏
    （指令字节 / manifest 声明 / canary / 屏障参数）必须各自 RED；
  * arbitrate_program：合成日志绿用例必须 GREEN；五类单点破坏（LOAD 覆盖
    缺失 / STORE UR 写 / C-L 读先于屏障 / 缺 S4 release / LOAD 窗口越界
    取指）必须各自 RED；
  * unified_lcs_compare：合成完美终态必须 GREEN；HBM 窗篡改 / golden 残留
    预载哨兵（LOAD 未送达伪装）/ dump≠golden 必须各自 RED。

对抗自验即验收本身——「能检测」须双向证明，与 test_arbitrate_probe_logs.py
同一纪律。
"""
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "verification_v2" / "tools"))

import arbitrate_probe_logs as arb                              # noqa: E402
import ldb_review_case_gen as g2                                # noqa: E402
import stb_solo_case_gen as s1                                  # noqa: E402
from check_program_manifest import Checker as ManifestChecker   # noqa: E402
from unified_lcs_case_gen import chunk_descs, parse_layout      # noqa: E402
from unified_lcs_compare import Compare as LcsCompare           # noqa: E402


# ----------------------------------------------------------------------
# chunk_descs / parse_layout
# ----------------------------------------------------------------------
class ChunkDescsTest(unittest.TestCase):
    def test_full_burst(self):
        self.assertEqual(chunk_descs(16), [{"lines": 16, "brst": 15}])

    def test_chunked_with_tail(self):
        self.assertEqual(chunk_descs(61),
                         [{"lines": 16, "brst": 15}] * 3
                         + [{"lines": 13, "brst": 12}])

    def test_min_tail(self):
        self.assertEqual(chunk_descs(2), [{"lines": 2, "brst": 1}])

    def test_brst0_tail_rejected(self):
        # n_lines ≡ 1 (mod 16) → 尾描述符 1 行 → brst=0 禁区，响亮拒绝
        for bad in (1, 17, 33):
            with self.assertRaises(SystemExit, msg=f"n_lines={bad} 须拒"):
                chunk_descs(bad)


def _write_sim_config(build: Path, tensors, stages=None) -> Path:
    cfg = {"tensors": tensors}
    if stages is not None:
        cfg["stages"] = stages
    build.mkdir(parents=True, exist_ok=True)
    (build / "sim_config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return build


def _ok_tensors(n_lines=2):
    return [
        {"name": "x", "bank": 0, "role": "input", "addr": 0,
         "shape": [n_lines, 4]},
        {"name": "y", "bank": 2, "role": "output", "addr": 0,
         "shape": [n_lines, 4]},
    ]


class ParseLayoutGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.build = Path(self.tmp.name) / "build"

    def tearDown(self):
        self.tmp.cleanup()

    def test_happy(self):
        _write_sim_config(self.build, _ok_tensors())
        lay = parse_layout(self.build, 8)
        self.assertEqual(lay["n_lines"], 2)
        self.assertFalse(lay["multi"])
        self.assertEqual([e["direction"] for e in lay["hbm_layout"]],
                         ["h2d", "d2h"])
        self.assertEqual(lay["hbm_layout"][0]["byte_base"], 0x1000)
        self.assertEqual(lay["hbm_layout"][1]["byte_base"], 0x2000)

    def test_n_not_multiple_of_4(self):
        _write_sim_config(self.build, _ok_tensors())
        with self.assertRaises(SystemExit):
            parse_layout(self.build, 7)

    def test_addr_nonzero(self):
        t = _ok_tensors()
        t[0]["addr"] = 16
        _write_sim_config(self.build, t)
        with self.assertRaises(SystemExit):
            parse_layout(self.build, 8)

    def test_shape_mismatch(self):
        t = _ok_tensors()
        t[0]["shape"] = [3, 4]
        _write_sim_config(self.build, t)
        with self.assertRaises(SystemExit):
            parse_layout(self.build, 8)

    def test_bank_overlap(self):
        t = _ok_tensors()
        t[1]["bank"] = 0
        _write_sim_config(self.build, t)
        with self.assertRaises(SystemExit):
            parse_layout(self.build, 8)

    def test_bad_role(self):
        t = _ok_tensors()
        t[0]["role"] = "scratch"
        _write_sim_config(self.build, t)
        with self.assertRaises(SystemExit):
            parse_layout(self.build, 8)

    def test_two_outputs(self):
        t = _ok_tensors() + [{"name": "z", "bank": 3, "role": "output",
                              "addr": 0, "shape": [2, 4]}]
        _write_sim_config(self.build, t)
        with self.assertRaises(SystemExit):
            parse_layout(self.build, 8)

    def test_n_lines_too_small(self):
        _write_sim_config(self.build, _ok_tensors(n_lines=1))
        with self.assertRaises(SystemExit):
            parse_layout(self.build, 4)

    def test_stage_chain_broken(self):
        _write_sim_config(self.build, _ok_tensors(),
                          stages=[{"stage_id": 0, "start": 0, "m": 1},
                                  {"stage_id": 1, "start": 2, "m": 1}])
        with self.assertRaises(SystemExit):
            parse_layout(self.build, 8)

    def test_stage_end_mismatch(self):
        _write_sim_config(self.build, _ok_tensors(),
                          stages=[{"stage_id": 0, "start": 0, "m": 3}])
        with self.assertRaises(SystemExit):
            parse_layout(self.build, 8)


# ----------------------------------------------------------------------
# 合成最小合法 case（manifest checker + compare 共用 fixture）
# ----------------------------------------------------------------------
N_LINES = 2                       # n=8
X_VALS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
O_VALS = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
ALPHA = 2.5


def _f32(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _pack(lanes) -> str:
    v = 0
    for i, w in enumerate(lanes):
        v |= (w & 0xFFFFFFFF) << (32 * i)
    return f"{v:032x}"


def _data_lines(vals):
    return [_pack([_f32(v) for v in vals[4 * k:4 * k + 4]])
            for k in range(N_LINES)]


def build_min_case(case: Path) -> dict:
    """装配最小合法统一 case（n=8：x@bank0 other@bank1 alpha@bank3 y@bank2）。

    返回 (manifest, golden_y_lines)。指令文件经真发射器生成（与检查器再发射
    同码路径——篡改测试在生成之后动手，不受影响）。
    """
    import numpy as np
    lay_entries = [
        {"name": "x", "role": "input", "bank": 0, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x1000,
         "bytes": N_LINES * 16, "direction": "h2d"},
        {"name": "other", "role": "input", "bank": 1, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x2000,
         "bytes": N_LINES * 16, "direction": "h2d"},
        {"name": "alpha", "role": "param", "bank": 3, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x3000,
         "bytes": N_LINES * 16, "direction": "h2d"},
        {"name": "y", "role": "output", "bank": 2, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x4000,
         "bytes": N_LINES * 16, "direction": "d2h"},
    ]
    # 数据行
    x_lines = _data_lines(X_VALS)
    o_lines = _data_lines(O_VALS)
    a_lines = [_pack([_f32(ALPHA)] * 4), g2.ZERO_LINE]
    y_ref = (np.float32(ALPHA)
             * (np.asarray(X_VALS, dtype=np.float32)
                + np.asarray(O_VALS, dtype=np.float32))).astype(np.float32)
    y_lines = _data_lines([float(v) for v in y_ref])

    # 指令（真发射器；chdir 纪律与 checker 一致）
    descs = chunk_descs(N_LINES)
    cwd = os.getcwd()
    os.chdir(_ROOT)
    try:
        ldb_all, axi_all = [], []
        for e in lay_entries[:3]:
            ls, xs = g2.build_and_emit(descs, e["bank"], 0, e["byte_base"], 0)
            ldb_all += ls
            axi_all += xs
        stb, stbaxi = s1.build_and_emit(descs, 2, 0, 0x4000, 0)
    finally:
        os.chdir(cwd)

    import hashlib

    def _w(p: Path, lines):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"file": p.name, "lines": len(lines),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}

    stages_meta, iq_meta = [], {}
    for sid, files in ((0, (("iq_ldb.hex", ldb_all),
                            ("iq_ldb_axi.hex", axi_all))),
                       (1, (("iq_ur_r0.hex", ["deadbeef"] * 3),)),
                       (2, (("iq_stb.hex", stb),
                            ("iq_stb_axi.hex", stbaxi)))):
        iq_meta[sid] = [_w(case / "smi_iq_inst" / f"stage_{sid}" / nm, ls)
                        for nm, ls in files]

    # UR 预载 / 终态 / dump
    ur = case / "smc_0" / "ur_data"
    outb = case / "smc_0" / "output_bank"
    src_of = {0: x_lines, 1: o_lines, 3: a_lines}
    for b in range(8):
        if b in src_of:
            init = [g2.sentinel_line(b, k) for k in range(256)]
            fin = list(init)
            fin[:N_LINES] = src_of[b]
        elif b == 2:
            init = [g2.ZERO_LINE] * 256
            fin = y_lines + [g2.ZERO_LINE] * (256 - N_LINES)
        else:
            init = [g2.ZERO_LINE] * 256
            fin = list(init)
        _w(ur / f"bank{b}.hex", init)
        _w(outb / f"bank{b}_final.hex", fin)
        _w(case / "smc_0" / f"ur_result_smc_0_bank{b}.hex", fin)
    for i in range(1, 8):
        _w(case / f"smc_{i}" / "ur_data" / "bank0.hex",
           [g2.sentinel_aux_line(i, 0, k) for k in range(256)])

    # HBM 镜像：x@256 other@512 alpha@768 y@1024（行号 = byte_base/16）
    init1 = [g2.preload_pattern_line(k) for k in range(1056)]
    for e, lines in ((lay_entries[0], x_lines), (lay_entries[1], o_lines),
                     (lay_entries[2], a_lines)):
        l0 = e["byte_base"] // 16
        init1[l0:l0 + N_LINES] = lines
    init1[1024:1024 + N_LINES] = [g2.ZERO_LINE] * N_LINES   # d2h 区预填全零
    snap = case / "memory_init_snapshot"
    _w(snap / "memory_init1.hex", init1)
    hp_files = {"memory_init1.hex": hashlib.sha256(
        (snap / "memory_init1.hex").read_bytes()).hexdigest()}
    for i in range(2, 9):
        _w(snap / f"memory_init{i}.hex",
           [g2.mark_pattern_line(k) for k in range(64)])
        hp_files[f"memory_init{i}.hex"] = hashlib.sha256(
            (snap / f"memory_init{i}.hex").read_bytes()).hexdigest()

    # HBM dump：slice0 = init1 但输出窗 = golden；slice1..7 = 标记
    hbm0 = list(init1)
    hbm0[1024:1024 + N_LINES] = y_lines
    _w(case / "smc_0" / "hbm_result_smc_0.hex", hbm0)
    for i in range(1, 8):
        _w(case / f"smc_{i}" / f"hbm_result_smc_{i}.hex",
           [g2.mark_pattern_line(k) for k in range(64)])

    region_sha = {}
    for e in lay_entries:
        l0 = e["byte_base"] // 16
        region = init1[l0:l0 + N_LINES]
        region_sha[e["name"]] = hashlib.sha256(
            ("\n".join(region) + "\n").encode()).hexdigest()

    manifest = {
        "schema_version": "adr061_program_manifest_v1",
        "op": "scale_sum", "formula": "alpha * (x + other)",
        "n": 8, "n_lines": N_LINES,
        "hbm_layout": lay_entries,
        "hbm_region_sha256": region_sha,
        "stages": [
            {"stage_id": 0, "kind": "LOAD", "predecessor": None,
             "covers": [{"tensor": e["name"], "bank": e["bank"],
                         "line0": 0, "lines": N_LINES}
                        for e in lay_entries[:3]],
             "expected_ur_write_windows": [
                 {"bank": e["bank"], "line0": 0, "lines": N_LINES}
                 for e in lay_entries[:3]],
             "iq_files": iq_meta[0]},
            {"stage_id": 1, "kind": "COMPUTE", "predecessor": 0,
             "iteration_range": [0, N_LINES],
             "expected_ur_write_windows": [
                 {"bank": 2, "line0": 0, "lines": N_LINES}],
             "iq_files": iq_meta[1]},
            {"stage_id": 2, "kind": "STORE", "predecessor": 1,
             "covers": [{"tensor": "y", "hbm_byte_base": 0x4000}],
             "expected_ur_write_windows": [],
             "expected_hbm_write_window": {
                 "byte_base": 0x4000, "line0": 1024, "lines": N_LINES},
             "iq_files": iq_meta[2]},
        ],
    }
    (case / "program_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    (case / "g3_expect.json").write_text(json.dumps({
        "schema": "adr061.unified_expect/v1",
        "stage_count": 3,
        "barrier": {"exp_ldb_done": len(ldb_all),
                    "exp_ldbaxi_done": len(axi_all),
                    "timeout_cycles": 5000},
        "wbarrier": {"bank": 2, "writes": N_LINES, "stage": 1,
                     "timeout_cycles": 5000},
        "s4barrier": {"addr0": 1024, "writes": N_LINES,
                      "bresp": len(stbaxi), "timeout_cycles": 5000},
        "hbm_preload": {"files": hp_files},
    }), encoding="utf-8")
    return manifest, y_lines


# ----------------------------------------------------------------------
# horner 形态合成 case（C-H 公式驱动独立参考的鉴别性 fixture）
# ----------------------------------------------------------------------
C_VALS = [0.125, -0.25, 0.375, -0.5]          # c0..c3（fp32 恰可表）
HORNER_FORMULA = "((c0 * x + c1) * x + c2) * x + c3"


def _horner_y_ref():
    """有序 AST 同序 fp32：mul→add→mul→add→mul→add 逐 op 舍入。"""
    import numpy as np
    xf = np.asarray(X_VALS, dtype=np.float32)
    t = np.float32(C_VALS[0]) * xf
    t = t + np.float32(C_VALS[1])
    t = t * xf
    t = t + np.float32(C_VALS[2])
    t = t * xf
    return (t + np.float32(C_VALS[3])).astype(np.float32)


def build_horner_case(case: Path) -> dict:
    """装配 horner 形态最小统一 case：x@bank0 + c0..c3 分置 bank1/3/4/5
    + constants@bank7 + y@bank2——布局无 'other'/'alpha'，专鉴 C-H 独立
    参考的算子名/张量名特判（2026-10-12 horner_v3u 步④失败形态回归钉）。"""
    lay_entries = [
        {"name": "x", "role": "input", "bank": 0, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x1000,
         "bytes": N_LINES * 16, "direction": "h2d"},
        {"name": "c0", "role": "param", "bank": 1, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x2000,
         "bytes": N_LINES * 16, "direction": "h2d"},
        {"name": "c1", "role": "param", "bank": 3, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x3000,
         "bytes": N_LINES * 16, "direction": "h2d"},
        {"name": "c2", "role": "param", "bank": 4, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x4000,
         "bytes": N_LINES * 16, "direction": "h2d"},
        {"name": "c3", "role": "param", "bank": 5, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x5000,
         "bytes": N_LINES * 16, "direction": "h2d"},
        {"name": "constants", "role": "constant", "bank": 7, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x6000,
         "bytes": N_LINES * 16, "direction": "h2d"},
        {"name": "y", "role": "output", "bank": 2, "ur_line0": 0,
         "lines": N_LINES, "byte_base": 0x7000,
         "bytes": N_LINES * 16, "direction": "d2h"},
    ]
    x_lines = _data_lines(X_VALS)
    c_lines = [[_pack([_f32(c)] * 4), g2.ZERO_LINE] for c in C_VALS]
    k_lines = [g2.ZERO_LINE] * N_LINES
    y_lines = _data_lines([float(v) for v in _horner_y_ref()])

    descs = chunk_descs(N_LINES)
    cwd = os.getcwd()
    os.chdir(_ROOT)
    try:
        ldb_all, axi_all = [], []
        for e in lay_entries[:6]:
            ls, xs = g2.build_and_emit(descs, e["bank"], 0, e["byte_base"], 0)
            ldb_all += ls
            axi_all += xs
        stb, stbaxi = s1.build_and_emit(descs, 2, 0, 0x7000, 0)
    finally:
        os.chdir(cwd)

    import hashlib

    def _w(p: Path, lines):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"file": p.name, "lines": len(lines),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}

    iq_meta = {}
    for sid, files in ((0, (("iq_ldb.hex", ldb_all),
                            ("iq_ldb_axi.hex", axi_all))),
                       (1, (("iq_ur_r0.hex", ["deadbeef"] * 3),)),
                       (2, (("iq_stb.hex", stb),
                            ("iq_stb_axi.hex", stbaxi)))):
        iq_meta[sid] = [_w(case / "smi_iq_inst" / f"stage_{sid}" / nm, ls)
                        for nm, ls in files]

    ur = case / "smc_0" / "ur_data"
    outb = case / "smc_0" / "output_bank"
    src_of = {0: x_lines, 1: c_lines[0], 3: c_lines[1],
              4: c_lines[2], 5: c_lines[3], 7: k_lines}
    for b in range(8):
        if b in src_of:
            init = [g2.sentinel_line(b, k) for k in range(256)]
            fin = list(init)
            fin[:N_LINES] = src_of[b]
        elif b == 2:
            init = [g2.ZERO_LINE] * 256
            fin = y_lines + [g2.ZERO_LINE] * (256 - N_LINES)
        else:
            init = [g2.ZERO_LINE] * 256
            fin = list(init)
        _w(ur / f"bank{b}.hex", init)
        _w(outb / f"bank{b}_final.hex", fin)
        _w(case / "smc_0" / f"ur_result_smc_0_bank{b}.hex", fin)
    for i in range(1, 8):
        _w(case / f"smc_{i}" / "ur_data" / "bank0.hex",
           [g2.sentinel_aux_line(i, 0, k) for k in range(256)])

    y_l0 = 0x7000 // 16                            # 1792
    init1 = [g2.preload_pattern_line(k) for k in range(1856)]
    for e, lines in ((lay_entries[0], x_lines), (lay_entries[1], c_lines[0]),
                     (lay_entries[2], c_lines[1]), (lay_entries[3], c_lines[2]),
                     (lay_entries[4], c_lines[3]), (lay_entries[5], k_lines)):
        l0 = e["byte_base"] // 16
        init1[l0:l0 + N_LINES] = lines
    init1[y_l0:y_l0 + N_LINES] = [g2.ZERO_LINE] * N_LINES
    snap = case / "memory_init_snapshot"
    _w(snap / "memory_init1.hex", init1)
    hp_files = {"memory_init1.hex": hashlib.sha256(
        (snap / "memory_init1.hex").read_bytes()).hexdigest()}
    for i in range(2, 9):
        _w(snap / f"memory_init{i}.hex",
           [g2.mark_pattern_line(k) for k in range(64)])
        hp_files[f"memory_init{i}.hex"] = hashlib.sha256(
            (snap / f"memory_init{i}.hex").read_bytes()).hexdigest()

    hbm0 = list(init1)
    hbm0[y_l0:y_l0 + N_LINES] = y_lines
    _w(case / "smc_0" / "hbm_result_smc_0.hex", hbm0)
    for i in range(1, 8):
        _w(case / f"smc_{i}" / f"hbm_result_smc_{i}.hex",
           [g2.mark_pattern_line(k) for k in range(64)])

    region_sha = {}
    for e in lay_entries:
        l0 = e["byte_base"] // 16
        region = init1[l0:l0 + N_LINES]
        region_sha[e["name"]] = hashlib.sha256(
            ("\n".join(region) + "\n").encode()).hexdigest()

    manifest = {
        "schema_version": "adr061_program_manifest_v1",
        "op": "horner_v3u", "formula": HORNER_FORMULA,
        "n": 8, "n_lines": N_LINES,
        "hbm_layout": lay_entries,
        "hbm_region_sha256": region_sha,
        "stages": [
            {"stage_id": 0, "kind": "LOAD", "predecessor": None,
             "covers": [{"tensor": e["name"], "bank": e["bank"],
                         "line0": 0, "lines": N_LINES}
                        for e in lay_entries[:6]],
             "expected_ur_write_windows": [
                 {"bank": e["bank"], "line0": 0, "lines": N_LINES}
                 for e in lay_entries[:6]],
             "iq_files": iq_meta[0]},
            {"stage_id": 1, "kind": "COMPUTE", "predecessor": 0,
             "iteration_range": [0, N_LINES],
             "expected_ur_write_windows": [
                 {"bank": 2, "line0": 0, "lines": N_LINES}],
             "iq_files": iq_meta[1]},
            {"stage_id": 2, "kind": "STORE", "predecessor": 1,
             "covers": [{"tensor": "y", "hbm_byte_base": 0x7000}],
             "expected_ur_write_windows": [],
             "expected_hbm_write_window": {
                 "byte_base": 0x7000, "line0": y_l0, "lines": N_LINES},
             "iq_files": iq_meta[2]},
        ],
    }
    (case / "program_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    (case / "g3_expect.json").write_text(json.dumps({
        "schema": "adr061.unified_expect/v1",
        "stage_count": 3,
        "barrier": {"exp_ldb_done": len(ldb_all),
                    "exp_ldbaxi_done": len(axi_all),
                    "timeout_cycles": 5000},
        "wbarrier": {"bank": 2, "writes": N_LINES, "stage": 1,
                     "timeout_cycles": 5000},
        "s4barrier": {"addr0": y_l0, "writes": N_LINES,
                      "bresp": len(stbaxi), "timeout_cycles": 5000},
        "hbm_preload": {"files": hp_files},
    }), encoding="utf-8")
    return manifest, y_lines


class ManifestCheckerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case = Path(self.tmp.name) / "case"
        build_min_case(self.case)

    def tearDown(self):
        self.tmp.cleanup()

    def test_green(self):
        self.assertEqual(ManifestChecker(self.case).run(), 0,
                         msg="合成最小合法 case 必须 GREEN")

    def test_iq_byte_tamper_red(self):
        p = self.case / "smi_iq_inst" / "stage_0" / "iq_ldb.hex"
        lines = p.read_text().splitlines()
        lines[0] = ("0" if lines[0][0] != "0" else "1") + lines[0][1:]
        p.write_text("\n".join(lines) + "\n")
        self.assertEqual(ManifestChecker(self.case).run(), 1)

    def test_manifest_layout_tamper_red(self):
        p = self.case / "program_manifest.json"
        man = json.loads(p.read_text())
        man["hbm_layout"][0]["byte_base"] = 0x5000
        p.write_text(json.dumps(man, indent=2))
        self.assertEqual(ManifestChecker(self.case).run(), 1)

    def test_canary_tamper_red(self):
        p = self.case / "memory_init_snapshot" / "memory_init1.hex"
        lines = p.read_text().splitlines()
        lines[100] = "f" * 32           # 窗口外行（span 外）
        p.write_text("\n".join(lines) + "\n")
        self.assertEqual(ManifestChecker(self.case).run(), 1)

    def test_barrier_param_tamper_red(self):
        p = self.case / "g3_expect.json"
        exp = json.loads(p.read_text())
        exp["barrier"]["exp_ldb_done"] += 1
        p.write_text(json.dumps(exp))
        self.assertEqual(ManifestChecker(self.case).run(), 1)


class UnifiedLcsCompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case = Path(self.tmp.name) / "case"
        _man, self.y_lines = build_min_case(self.case)
        self.instr = Path(self.tmp.name) / "physical_instructions.json"
        self.instr.write_text(json.dumps(
            [{"iq_name": "vout0", "stage": "BODY", "header": {"VLD": 1}}
             for _ in range(N_LINES)]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_green(self):
        self.assertEqual(LcsCompare(self.case, [self.instr]).run(), 0,
                         msg="合成完美终态必须 GREEN")

    def test_hbm_window_tamper_red(self):
        p = self.case / "smc_0" / "hbm_result_smc_0.hex"
        lines = p.read_text().splitlines()
        lines[1024] = "deadbeef" * 4
        p.write_text("\n".join(lines) + "\n")
        self.assertEqual(LcsCompare(self.case, [self.instr]).run(), 1)

    def test_sentinel_leftover_in_golden_red(self):
        # golden 的 LOAD bank 窗残留哨兵（LOAD 未送达伪装成送达）→ |T| 缩水
        p = self.case / "smc_0" / "output_bank" / "bank0_final.hex"
        lines = p.read_text().splitlines()
        lines[0] = g2.sentinel_line(0, 0)
        p.write_text("\n".join(lines) + "\n")
        rc = LcsCompare(self.case, [self.instr]).run()
        self.assertEqual(rc, 1)

    def test_dump_ne_golden_red(self):
        p = self.case / "smc_0" / "ur_result_smc_0_bank2.hex"
        lines = p.read_text().splitlines()
        lines[0] = "0" * 32
        p.write_text("\n".join(lines) + "\n")
        self.assertEqual(LcsCompare(self.case, [self.instr]).run(), 1)


# ----------------------------------------------------------------------
# arbitrate_program 合成日志红绿
# ----------------------------------------------------------------------
C_LOAD, C_COMP, C_STORE = 100, 160, 210     # 三段 BEGIN 拍
ACK_LOAD, ACK_COMP, ACK_STORE = 150, 205, 250
CREDIT_REL, WB_REL, S4_REL = 155, 190, 260


def _compute_expected() -> dict:
    """ur-r0→iq16 / ur-w0→iq20（与 test_arbitrate_probe_logs 同款合成形状；
    期望拍号为**相对** c0——c0 由仲裁核心从观测首拍锚定，不可绝对化）。"""
    c0 = 0
    ev_r0 = ([{"cycle": c0 + c, "iq": "ur-r0", "kind": "FETCH",
               "payload": {"vld": 1 if c else 0}} for c in (0, 3, 4, 5, 6)]
             + [{"cycle": c0 + c, "iq": "ur-r0", "kind": "ISSUE",
                 "payload": {"vld": 0}} for c in range(6)])
    ev_w0 = ([{"cycle": c0 + 0, "iq": "ur-w0", "kind": "FETCH",
               "payload": {"vld": 0}},
              {"cycle": c0 + 5, "iq": "ur-w0", "kind": "FETCH",
               "payload": {"vld": 1, "BANK": 2, "ADDR": 10}},
              {"cycle": c0 + 6, "iq": "ur-w0", "kind": "FETCH",
               "payload": {"vld": 1, "BANK": 2, "ADDR": 11}},
              {"cycle": c0 + 7, "iq": "ur-w0", "kind": "FETCH",
               "payload": {"vld": 0}}]
             + [{"cycle": c0 + c, "iq": "ur-w0", "kind": "ISSUE",
                 "payload": {"vld": 0}} for c in range(7)])
    return {"schema": "adr056_d4_expected_events_v1",
            "registry": {"records_sha256": "synthetic-test"},
            "union_queues": ["ur-r0", "ur-w0"],
            "queues": {"ur-r0": {"events": ev_r0},
                       "ur-w0": {"events": ev_w0}}}


def _prog_manifest() -> dict:
    return {"schema_version": "adr061_program_manifest_v1",
            "stages": [
                {"stage_id": 0, "kind": "LOAD",
                 "covers": [{"tensor": "x", "bank": 0, "line0": 0, "lines": 2},
                            {"tensor": "w", "bank": 1, "line0": 0, "lines": 2}],
                 "expected_ur_write_windows": [
                     {"bank": 0, "line0": 0, "lines": 2},
                     {"bank": 1, "line0": 0, "lines": 2}]},
                {"stage_id": 1, "kind": "COMPUTE"},
                {"stage_id": 2, "kind": "STORE",
                 "expected_ur_write_windows": [],
                 "expected_hbm_write_window": {"byte_base": 1600,
                                               "line0": 100, "lines": 2}}]}


class ArbitrateProgramTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._write_green()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_green(self):
        r = self.root
        fetch = ["# cycle iq addr_valid addr data_valid data"]
        for k, rel in enumerate((0, 3, 4, 5, 6)):
            fetch.append(f"{C_COMP + rel} 16 1 {k} 0 0")
        for k, rel in enumerate((0, 5, 6, 7)):
            fetch.append(f"{C_COMP + rel} 20 1 {k} 0 0")
        fetch.append(f"{C_LOAD + 10} 26 1 0 0 0")      # LOAD 取指
        fetch.append(f"{C_LOAD + 11} 27 1 0 0 0")
        fetch.append(f"{C_STORE + 10} 24 1 0 0 0")     # STORE 取指
        fetch.append(f"{C_STORE + 11} 25 1 0 0 0")
        (r / "iq_probe_trace.log").write_text("\n".join(fetch) + "\n")

        issue = ["# cycle iq dly_data_valid dly_data"]
        for rel in range(6):
            issue.append(f"{C_COMP + rel} 16 1 00aa")
        for rel in range(7):
            issue.append(f"{C_COMP + rel} 20 1 00bb")
        (r / "iq_issue_trace.log").write_text("\n".join(issue) + "\n")

        wdata = "ab" * 16
        write = ["# cycle smc bank we addr wbe wdata"]
        # LOAD：声明窗 4 行全送达
        write.append(f"{C_LOAD + 20} 0 0 1 0 ffff {wdata}")
        write.append(f"{C_LOAD + 21} 0 0 1 1 ffff {wdata}")
        write.append(f"{C_LOAD + 22} 0 1 1 0 ffff {wdata}")
        write.append(f"{C_LOAD + 23} 0 1 1 1 ffff {wdata}")
        # COMPUTE：期望写集 (2,10),(2,11)，早于 ack
        write.append(f"{C_COMP + 40} 0 2 1 10 ffff {wdata}")
        write.append(f"{C_COMP + 41} 0 2 1 11 ffff {wdata}")
        (r / "ur_write_trace.log").write_text("\n".join(write) + "\n")

        read = ["# cycle smc port bank addr",
                f"{C_COMP + 10} 0 0 0 0"]      # COMPUTE 首读 LOAD bank
        (r / "ur_read_trace.log").write_text("\n".join(read) + "\n")

        hbm = ["# cycle smc addra wea dina",
               f"{C_STORE + 20} 0 {100:05x} ffff {'cd' * 16}",
               f"{C_STORE + 21} 0 {101:05x} ffff {'cd' * 16}"]
        (r / "hbm_write_trace.log").write_text("\n".join(hbm) + "\n")

        sim = [
            f"[TB_MULTI_STAGE] ===== stage_0/2 BEGIN ===== @time 1000 "
            f"@probe_cycle {C_LOAD}",
            f"[TB_MULTI_STAGE] stage_0 done (intr cleared) @time 1500 "
            f"@probe_cycle {ACK_LOAD}",
            f"[GATE3][BARRIER] release @probe_cycle {CREDIT_REL}",
            f"[TB_MULTI_STAGE] ===== stage_1/2 BEGIN ===== @time 1600 "
            f"@probe_cycle {C_COMP}",
            f"[GATE4][WBARRIER] release @probe_cycle {WB_REL}",
            f"[TB_MULTI_STAGE] stage_1 done (intr cleared) @time 2050 "
            f"@probe_cycle {ACK_COMP}",
            f"[TB_MULTI_STAGE] ===== stage_2/2 BEGIN ===== @time 2100 "
            f"@probe_cycle {C_STORE}",
            f"[TB_MULTI_STAGE] stage_2 done (intr cleared) @time 2500 "
            f"@probe_cycle {ACK_STORE}",
            f"[GATE4][S4BARRIER] release @probe_cycle {S4_REL}",
            f"[TB_MULTI_STAGE] all stages complete @probe_cycle {S4_REL + 5}",
        ]
        (r / "sim.log").write_text("\n".join(sim) + "\n")

    def _run(self) -> dict:
        return arb.arbitrate_program(self.root, _prog_manifest(),
                                     {1: _compute_expected()},
                                     self.root / "sim.log")

    def test_green(self):
        rep = self._run()
        self.assertEqual(rep["verdict"], "GREEN",
                         msg=f"绿用例必须 GREEN: {rep['violations']}")
        self.assertTrue(rep["barriers"]["g3_credit"])
        self.assertEqual(len(rep["stages"]), 3)

    def test_load_coverage_miss_red(self):
        lines = (self.root / "ur_write_trace.log").read_text().splitlines()
        lines = [ln for ln in lines if not ln.startswith(f"{C_LOAD + 23} ")]
        (self.root / "ur_write_trace.log").write_text("\n".join(lines) + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(LOAD/C-S)" in v for v in rep["violations"]),
                        msg=rep["violations"])

    def test_store_ur_write_red(self):
        with open(self.root / "ur_write_trace.log", "a") as fh:
            fh.write(f"{C_STORE + 5} 0 4 1 7 ffff {'ab' * 16}\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(STORE/C-S)" in v for v in rep["violations"]),
                        msg=rep["violations"])

    def test_c_l_read_before_barrier_red(self):
        (self.root / "ur_read_trace.log").write_text(
            f"# cycle smc port bank addr\n{CREDIT_REL - 1} 0 0 0 0\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(C-L)" in v for v in rep["violations"]),
                        msg=rep["violations"])

    def test_missing_s4_release_red(self):
        lines = [ln for ln in (self.root / "sim.log").read_text().splitlines()
                 if "S4BARRIER" not in ln]
        (self.root / "sim.log").write_text("\n".join(lines) + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("S4BARRIER" in v for v in rep["violations"]),
                        msg=rep["violations"])

    def test_credit_release_same_cycle_as_begin_green_with_limit(self):
        # n=16 实测形态：release == BEGIN（TB 零延迟段推进构造）→ 不 RED，
        # 但须落 limitation（不据此声称独立屏障证据）
        lines = (self.root / "sim.log").read_text().splitlines()
        lines = [ln.replace(f"@probe_cycle {CREDIT_REL}",
                            f"@probe_cycle {C_COMP}")
                 if "GATE3" in ln else ln for ln in lines]
        # 首读 @C_COMP+10 > release @C_COMP 仍成立
        (self.root / "sim.log").write_text("\n".join(lines) + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "GREEN", msg=rep["violations"])
        self.assertTrue(any("(C-L)" in lim for lim in rep["limitations"]),
                        msg=rep["limitations"])

    def test_credit_release_after_begin_red(self):
        lines = (self.root / "sim.log").read_text().splitlines()
        lines = [ln.replace(f"@probe_cycle {CREDIT_REL}",
                            f"@probe_cycle {C_COMP + 1}")
                 if "GATE3" in ln else ln for ln in lines]
        (self.root / "sim.log").write_text("\n".join(lines) + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(C-L)" in v for v in rep["violations"]),
                        msg=rep["violations"])

    def test_first_read_same_cycle_as_release_red(self):
        # 用户裁定钉死：release == 首读 不享受同拍降级——首读 <= release 恒 RED
        # （从严；同拍降级仅适用于 release==BEGIN，且前提是首读严格晚于两者）
        sim = (self.root / "sim.log").read_text()
        sim = sim.replace(f"@probe_cycle {CREDIT_REL}",
                          f"@probe_cycle {C_COMP}", 1)
        (self.root / "sim.log").write_text(sim + "\n")
        (self.root / "ur_read_trace.log").write_text(
            f"# cycle smc port bank addr\n{C_COMP} 0 0 0 0\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(C-L) COMPUTE 首读" in v and "信用屏障" in v
                            for v in rep["violations"]), msg=rep["violations"])

    def test_multi_release_first_read_at_later_boundary_red(self):
        # 2026-10-12 用户有界核查钉死：多 release 按段匹配——首读落在后段
        # 入口且与该入口 release 同拍时，min 规则（仅查最早 release）会漏放；
        # 须晚于前置集最大值（含同拍从严）。本形态在旧实现下 GREEN、新实现 RED。
        sim = (self.root / "sim.log").read_text()
        sim = sim.replace(
            f"[GATE3][BARRIER] release @probe_cycle {CREDIT_REL}",
            f"[GATE3][BARRIER] release @probe_cycle {CREDIT_REL}\n"
            f"[GATE3][BARRIER] release @probe_cycle {C_STORE}", 1)
        (self.root / "sim.log").write_text(sim + "\n")
        (self.root / "ur_read_trace.log").write_text(
            f"# cycle smc port bank addr\n{C_STORE} 0 0 0 0\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(C-L) COMPUTE 首读" in v and "最大值" in v
                            for v in rep["violations"]), msg=rep["violations"])

    def test_multi_release_first_read_after_max_green(self):
        # 同双 release 形态，首读严格晚于前置集最大值 → GREEN；
        # release==BEGIN 同拍仅落 limitation（按归属段入口逐条记录）
        sim = (self.root / "sim.log").read_text()
        sim = sim.replace(
            f"[GATE3][BARRIER] release @probe_cycle {CREDIT_REL}",
            f"[GATE3][BARRIER] release @probe_cycle {CREDIT_REL}\n"
            f"[GATE3][BARRIER] release @probe_cycle {C_STORE}", 1)
        (self.root / "sim.log").write_text(sim + "\n")
        (self.root / "ur_read_trace.log").write_text(
            f"# cycle smc port bank addr\n{C_STORE + 5} 0 0 0 0\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "GREEN", msg=rep["violations"])
        self.assertTrue(any("(C-L)" in lim and "stage_2" in lim
                            for lim in rep["limitations"]), msg=rep["limitations"])

    def test_release_beyond_last_begin_red(self):
        # release 晚于末段 BEGIN：不归于任何段入口，匹配失败 → RED
        sim = (self.root / "sim.log").read_text()
        sim = sim.replace(
            f"[GATE3][BARRIER] release @probe_cycle {CREDIT_REL}",
            f"[GATE3][BARRIER] release @probe_cycle {CREDIT_REL}\n"
            f"[GATE3][BARRIER] release @probe_cycle {C_STORE + 40}", 1)
        (self.root / "sim.log").write_text(sim + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(C-L)" in v and "不归于任何段入口" in v
                            for v in rep["violations"]), msg=rep["violations"])

    def test_late_load_write_cannot_hide_red(self):
        # 用户裁定钉死：迟到 LOAD 写（落在 STORE 窗）必须 RED——
        # 由 LOAD 覆盖缺失 + STORE 段 UR 写恒空两条独立捕获
        lines = (self.root / "ur_write_trace.log").read_text().splitlines()
        lines = [ln for ln in lines if not ln.startswith(f"{C_LOAD + 23} ")]
        lines.append(f"{C_STORE + 6} 0 1 1 1 ffff {'ab' * 16}")
        (self.root / "ur_write_trace.log").write_text("\n".join(lines) + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(LOAD/C-S)" in v for v in rep["violations"]),
                        msg=rep["violations"])
        self.assertTrue(any("(STORE/C-S)" in v for v in rep["violations"]),
                        msg=rep["violations"])

    def test_load_window_outside_iq_red(self):
        with open(self.root / "iq_probe_trace.log", "a") as fh:
            fh.write(f"{C_LOAD + 12} 30 1 0 0 0\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(LOAD/G1)" in v for v in rep["violations"]),
                        msg=rep["violations"])


if __name__ == "__main__":
    unittest.main()


class UnifiedLcsCompareHornerTest(unittest.TestCase):
    """C-H 独立参考公式化的鉴别性测试（2026-10-12 horner_v3u 步④失败形态
    回归钉）：旧实现硬编码 y=alpha*(x+other)，对 horner 布局响拒
    「布局缺张量 'other'」——本类证明泛化后非 scale_sum 形态可 GREEN，
    且各篡改/形态破坏仍各自 RED。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case = Path(self.tmp.name) / "case"
        build_horner_case(self.case)
        self.instr = Path(self.tmp.name) / "physical_instructions.json"
        self.instr.write_text(json.dumps(
            [{"iq_name": "vout0", "stage": "BODY", "header": {"VLD": 1}}
             for _ in range(N_LINES)]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_horner_green(self):
        cmp_ = LcsCompare(self.case, [self.instr])
        self.assertEqual(cmp_.run(), 0,
                         msg=f"horner 形态合成完美终态必须 GREEN: {cmp_.bad}")
        self.assertIn("input_modes", cmp_.report["c_h"])
        self.assertEqual(cmp_.report["c_h"]["input_modes"].get("c0"),
                         "stride0 标量驻留（line0 四 lane 核验）")

    def test_horner_param_region_swap_red(self):
        # c0 与 c3 区内容互换（LOAD 映射错位）→ 独立参考随之改变 ≠ golden/HBM
        p = self.case / "memory_init_snapshot" / "memory_init1.hex"
        lines = p.read_text().splitlines()
        c0_l0, c3_l0 = 0x2000 // 16, 0x5000 // 16
        c0r = lines[c0_l0:c0_l0 + N_LINES]
        lines[c0_l0:c0_l0 + N_LINES] = lines[c3_l0:c3_l0 + N_LINES]
        lines[c3_l0:c3_l0 + N_LINES] = c0r
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmp_ = LcsCompare(self.case, [self.instr])
        self.assertEqual(cmp_.run(), 1)
        self.assertTrue(any("(C-H)" in m for m in cmp_.bad),
                        msg=f"参数区错位须由 C-H 捕获: {cmp_.bad}")

    def test_horner_param_form_violation_red(self):
        # c1 line0 仅改一个 lane（非标量驻留形态）→ 响亮 RED
        p = self.case / "memory_init_snapshot" / "memory_init1.hex"
        lines = p.read_text().splitlines()
        v = int(lines[0x3000 // 16], 16) ^ (1 << 64)     # lane2 翻转 1 bit
        lines[0x3000 // 16] = f"{v:032x}"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmp_ = LcsCompare(self.case, [self.instr])
        self.assertEqual(cmp_.run(), 1)
        self.assertTrue(any("非标量驻留形态" in m for m in cmp_.bad),
                        msg=f"参数形态破坏须正面取证: {cmp_.bad}")

    def test_formula_missing_red(self):
        p = self.case / "program_manifest.json"
        man = json.loads(p.read_text())
        del man["formula"]
        p.write_text(json.dumps(man, indent=2), encoding="utf-8")
        cmp_ = LcsCompare(self.case, [self.instr])
        self.assertEqual(cmp_.run(), 1)
        self.assertTrue(any("缺 formula" in m for m in cmp_.bad),
                        msg=f"缺 formula 须响亮 RED（不静默跳过）: {cmp_.bad}")

    def test_formula_undeclared_leaf_red(self):
        p = self.case / "program_manifest.json"
        man = json.loads(p.read_text())
        man["formula"] = man["formula"].replace("c3", "c9")
        p.write_text(json.dumps(man, indent=2), encoding="utf-8")
        cmp_ = LcsCompare(self.case, [self.instr])
        self.assertEqual(cmp_.run(), 1)
        self.assertTrue(any("公式解析失败" in m for m in cmp_.bad),
                        msg=f"未声明叶须解析期响拒: {cmp_.bad}")

    def test_extra_input_not_referenced_red(self):
        # 布局装载了公式不消费的输入（ghost）→ 不静默放过（Z3 与 C-H 双闸）
        p = self.case / "program_manifest.json"
        man = json.loads(p.read_text())
        man["hbm_layout"].insert(
            1, {"name": "ghost", "role": "input", "bank": 6, "ur_line0": 0,
                "lines": N_LINES, "byte_base": 0x8000,
                "bytes": N_LINES * 16, "direction": "h2d"})
        p.write_text(json.dumps(man, indent=2), encoding="utf-8")
        cmp_ = LcsCompare(self.case, [self.instr])
        self.assertEqual(cmp_.run(), 1)
        self.assertTrue(any("未被公式" in m for m in cmp_.bad),
                        msg=f"装载未消费输入须由 C-H 点名: {cmp_.bad}")


# ----------------------------------------------------------------------
# arbitrate_program v2（ulcs_program_manifest_v2_8smc）：8-SMC 等长分片的
# 逐 SMC LOAD 写账 / 逐通道 STORE 写账（2026-10-14 A′，用户执行范围③④）。
#
# 纪律（对抗自验）：v2 只许**增强**——绿用例必须 GREEN 且报告含逐 SMC /
# 逐通道账目；任何一颗 SMC 少写/写偏、任一通道漏搬运或写越界，都必须
# 响亮 RED 并点名到 smc_k / chk。v1 路径逐字节不变（同日志跑 v1 manifest
# 仍 GREEN 且无 v2 账目块）。
# ----------------------------------------------------------------------
def _prog_manifest_v2(smc_count: int = 8) -> dict:
    man = _prog_manifest()
    man["schema_version"] = "ulcs_program_manifest_v2_8smc"
    man["partition"] = {"scheme": "equal_8smc_v1", "smc_count": smc_count,
                        "n_total": 8 * 2, "n_per": 2,
                        "channel_stride": 0x10000000}
    return man


class ArbitrateProgramV2Test(unittest.TestCase):
    SMC_N = 8

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._write_green_v2()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_green_v2(self):
        """与 v1 绿用例同形态，但 LOAD UR 写与 STORE HBM 写覆盖 8 颗目标。"""
        r = self.root
        fetch = ["# cycle iq addr_valid addr data_valid data"]
        for k, rel in enumerate((0, 3, 4, 5, 6)):
            fetch.append(f"{C_COMP + rel} 16 1 {k} 0 0")
        for k, rel in enumerate((0, 5, 6, 7)):
            fetch.append(f"{C_COMP + rel} 20 1 {k} 0 0")
        fetch.append(f"{C_LOAD + 10} 26 1 0 0 0")
        fetch.append(f"{C_LOAD + 11} 27 1 0 0 0")
        fetch.append(f"{C_STORE + 10} 24 1 0 0 0")
        fetch.append(f"{C_STORE + 11} 25 1 0 0 0")
        (r / "iq_probe_trace.log").write_text("\n".join(fetch) + "\n")

        issue = ["# cycle iq dly_data_valid dly_data"]
        for rel in range(6):
            issue.append(f"{C_COMP + rel} 16 1 00aa")
        for rel in range(7):
            issue.append(f"{C_COMP + rel} 20 1 00bb")
        (r / "iq_issue_trace.log").write_text("\n".join(issue) + "\n")

        wdata = "ab" * 16
        write = ["# cycle smc bank we addr wbe wdata"]
        # LOAD：八颗各写满声明窗 4 行（bank0/1 各 2 行）
        for s in range(self.SMC_N):
            for i, (bank, addr) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
                write.append(f"{C_LOAD + 20 + i} {s} {bank} 1 {addr} "
                             f"ffff {wdata}")
        # COMPUTE（SMC0 代表）：期望写集 (2,10),(2,11)，早于 ack
        write.append(f"{C_COMP + 40} 0 2 1 10 ffff {wdata}")
        write.append(f"{C_COMP + 41} 0 2 1 11 ffff {wdata}")
        (r / "ur_write_trace.log").write_text("\n".join(write) + "\n")

        read = ["# cycle smc port bank addr",
                f"{C_COMP + 10} 0 0 0 0"]      # 读探针实测仅 SMC0
        (r / "ur_read_trace.log").write_text("\n".join(read) + "\n")

        hbm = ["# cycle smc addra wea dina"]
        for s in range(self.SMC_N):
            hbm.append(f"{C_STORE + 20} {s} {100:05x} ffff {'cd' * 16}")
            hbm.append(f"{C_STORE + 21} {s} {101:05x} ffff {'cd' * 16}")
        (r / "hbm_write_trace.log").write_text("\n".join(hbm) + "\n")

        sim = [
            f"[TB_MULTI_STAGE] ===== stage_0/2 BEGIN ===== @time 1000 "
            f"@probe_cycle {C_LOAD}",
            f"[TB_MULTI_STAGE] stage_0 done (intr cleared) @time 1500 "
            f"@probe_cycle {ACK_LOAD}",
            f"[GATE3][BARRIER] release @probe_cycle {CREDIT_REL}",
            f"[TB_MULTI_STAGE] ===== stage_1/2 BEGIN ===== @time 1600 "
            f"@probe_cycle {C_COMP}",
            f"[GATE4][WBARRIER] release @probe_cycle {WB_REL}",
            f"[TB_MULTI_STAGE] stage_1 done (intr cleared) @time 2050 "
            f"@probe_cycle {ACK_COMP}",
            f"[TB_MULTI_STAGE] ===== stage_2/2 BEGIN ===== @time 2100 "
            f"@probe_cycle {C_STORE}",
            f"[TB_MULTI_STAGE] stage_2 done (intr cleared) @time 2500 "
            f"@probe_cycle {ACK_STORE}",
            f"[GATE4][S4BARRIER] release @probe_cycle {S4_REL}",
            f"[TB_MULTI_STAGE] all stages complete @probe_cycle {S4_REL + 5}",
        ]
        (r / "sim.log").write_text("\n".join(sim) + "\n")

    def _run(self, man: dict | None = None) -> dict:
        return arb.arbitrate_program(self.root, man or _prog_manifest_v2(),
                                     {1: _compute_expected()},
                                     self.root / "sim.log")

    # ---- 绿 + 账目块 ----
    def test_v2_green_with_per_target_accounting(self):
        rep = self._run()
        self.assertEqual(rep["verdict"], "GREEN", msg=rep["violations"])
        self.assertEqual(rep["schema_version"], "ulcs_program_manifest_v2_8smc")
        self.assertEqual(rep["partition"]["smc_count"], self.SMC_N)
        load_rep = rep["stages"][0]
        self.assertEqual(sorted(load_rep["per_smc_ur_writes"]),
                         [f"smc_{k}" for k in range(self.SMC_N)])
        for k in range(self.SMC_N):
            acc = load_rep["per_smc_ur_writes"][f"smc_{k}"]
            self.assertEqual((acc["observed"], acc["outside"], acc["missing"]),
                             (4, 0, 0), msg=f"smc_{k}: {acc}")
        store_rep = rep["stages"][-1]
        self.assertEqual(sorted(store_rep["per_channel_hbm_writes"]),
                         [f"ch{k}" for k in range(self.SMC_N)])
        for k in range(self.SMC_N):
            acc = store_rep["per_channel_hbm_writes"][f"ch{k}"]
            self.assertEqual((acc["observed_lines"], acc["outside"],
                              acc["missing"]), (2, 0, 0), msg=f"ch{k}: {acc}")
        # 读探针仅覆盖 SMC0 ⇒ 必须落 limitation，不得声称逐 SMC 消费证据
        self.assertTrue(any("读探针" in lim and "SMC0" in lim
                            for lim in rep["limitations"]), msg=rep["limitations"])

    # ---- v1 路径不变（同日志：v1 只以 SMC0/ch0 代表，无 v2 账目块） ----
    def test_v1_manifest_on_v2_logs_still_green_without_v2_blocks(self):
        rep = self._run(_prog_manifest())
        self.assertEqual(rep["verdict"], "GREEN", msg=rep["violations"])
        self.assertIsNone(rep["partition"])
        self.assertNotIn("per_smc_ur_writes", rep["stages"][0])
        self.assertNotIn("per_channel_hbm_writes", rep["stages"][-1])
        self.assertFalse(any("读探针" in lim for lim in rep["limitations"]))

    # ---- 负控：逐 SMC LOAD ----
    def test_v2_load_missing_one_smc_row_red(self):
        p = self.root / "ur_write_trace.log"
        lines = p.read_text().splitlines()
        # 摘掉 smc_5 的 bank1/addr1 那一笔（第 4 笔）→ 该目标覆盖不足
        victim = [i for i, ln in enumerate(lines)
                  if ln.startswith(f"{C_LOAD + 23} 5 1 1 ")]
        self.assertEqual(len(victim), 1, msg=lines)
        del lines[victim[0]]
        p.write_text("\n".join(lines) + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(LOAD/C-S/smc_5)" in v and "覆盖不足" in v
                            for v in rep["violations"]), msg=rep["violations"])
        self.assertEqual(rep["stages"][0]["per_smc_ur_writes"]["smc_5"]["missing"],
                         1)
        # 其余目标仍应为满额（不因一颗失配而污染全部账目）
        self.assertEqual(rep["stages"][0]["per_smc_ur_writes"]["smc_4"]["missing"],
                         0)

    def test_v2_load_out_of_window_on_one_smc_red(self):
        with open(self.root / "ur_write_trace.log", "a") as fh:
            fh.write(f"{C_LOAD + 24} 6 3 1 9 ffff {'ab' * 16}\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(LOAD/C-S/smc_6)" in v and "越出声明窗" in v
                            for v in rep["violations"]), msg=rep["violations"])

    def test_v2_load_smc7_entirely_absent_red(self):
        p = self.root / "ur_write_trace.log"
        lines = [ln for ln in p.read_text().splitlines()
                 if not ln.startswith(f"{C_LOAD + 20} 7 ")
                 and not ln.startswith(f"{C_LOAD + 21} 7 ")
                 and not ln.startswith(f"{C_LOAD + 22} 7 ")
                 and not ln.startswith(f"{C_LOAD + 23} 7 ")]
        p.write_text("\n".join(lines) + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(LOAD/C-S/smc_7)" in v for v in rep["violations"]),
                        msg=rep["violations"])
        self.assertEqual(rep["stages"][0]["per_smc_ur_writes"]["smc_7"]["observed"],
                         0)

    # ---- 负控：逐通道 STORE ----
    def test_v2_store_missing_one_channel_line_red(self):
        p = self.root / "hbm_write_trace.log"
        lines = p.read_text().splitlines()
        victim = [i for i, ln in enumerate(lines) if ln.startswith(f"{C_STORE + 20} 3 ")]
        self.assertEqual(len(victim), 1, msg=lines)
        del lines[victim[0]]
        p.write_text("\n".join(lines) + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(STORE/C-S/ch3)" in v and "覆盖不足" in v
                            for v in rep["violations"]), msg=rep["violations"])
        self.assertEqual(
            rep["stages"][-1]["per_channel_hbm_writes"]["ch3"]["missing"], 1)

    def test_v2_store_cross_channel_write_red(self):
        # 通道 2 多写一行（越出声明窗）→ 逐通道越界捕获
        with open(self.root / "hbm_write_trace.log", "a") as fh:
            fh.write(f"{C_STORE + 22} 2 {102:05x} ffff {'cd' * 16}\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(STORE/C-S/ch2)" in v and "越出声明窗" in v
                            for v in rep["violations"]), msg=rep["violations"])

    def test_v2_store_channel7_absent_red(self):
        p = self.root / "hbm_write_trace.log"
        lines = [ln for ln in p.read_text().splitlines()
                 if not ln.startswith(f"{C_STORE + 20} 7 ")
                 and not ln.startswith(f"{C_STORE + 21} 7 ")]
        p.write_text("\n".join(lines) + "\n")
        rep = self._run()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("(STORE/C-S/ch7)" in v for v in rep["violations"]),
                        msg=rep["violations"])
        self.assertEqual(
            rep["stages"][-1]["per_channel_hbm_writes"]["ch7"]["observed_lines"],
            0)

    # ---- 负控：schema / 目标数 ----
    def test_v2_smc_count_tamper_red(self):
        man = _prog_manifest_v2(smc_count=7)
        rep = self._run(man)
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("smc_count" in v for v in rep["violations"]),
                        msg=rep["violations"])

    def test_unknown_schema_red(self):
        man = _prog_manifest()
        man["schema_version"] = "ulcs_program_manifest_v9"
        rep = self._run(man)
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("schema" in v for v in rep["violations"]),
                        msg=rep["violations"])
