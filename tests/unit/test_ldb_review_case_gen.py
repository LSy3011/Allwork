"""ADR-059 v2 / M3-lite 门②：ldb_review_case_gen 守卫与发射单测。

覆盖：
    - 守卫矩阵：brst=0 响拒 / 聚合配对恒等 / total>255 / 描述符数>8 / 越界 /
      非对齐 / smc_id_en≠0 → 响亮 SystemExit
    - 发射正路：L1 参数 → 字段级解码 == 期望（镜像 golden w033 的单颗改编：
      en 7→0、intlv→0；CNT/line_byte_cnt=15/stride=1/tid=0 照抄）
    - L2 形态：三描述符 16+9+8 → LDB CNT=33 + LDBAXI 3 行 gr_base 累进
    - 物化：case 结构 + 修正② smc_1..7 独立 sentinel′ + 期望清单自洽
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "verification_v2", "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import iq_field_diff as IFD  # noqa: E402
import ldb_review_case_gen as G  # noqa: E402

MODS = IFD.load_modules_yaml(os.path.join(ROOT, "hw_config", "modules.yaml"))


def _guard(*, descs, ur_bank=1, ur_offset=0, gr_base=0x1000, en=0):
    G.guard("t", descs, ur_bank, ur_offset, gr_base, en)


class TestGuards:
    def test_brst0_rejected(self):
        with pytest.raises(SystemExit, match="brst=0"):
            _guard(descs=[{"lines": 1, "brst": 0}])

    def test_pairing_identity_rejected(self):
        with pytest.raises(SystemExit, match="配对恒等式"):
            _guard(descs=[{"lines": 17, "brst": 15}])   # 17 != 1*16

    def test_brst_range(self):
        with pytest.raises(SystemExit, match="brst=16 越界"):
            _guard(descs=[{"lines": 17, "brst": 16}])

    def test_total_over_255_rejected(self):
        with pytest.raises(SystemExit, match="total=256"):
            _guard(descs=[{"lines": 128, "brst": 15, "loops": 7},
                          {"lines": 128, "brst": 15, "loops": 7}])

    def test_too_many_descs_rejected(self):
        with pytest.raises(SystemExit, match="描述符数 9"):
            _guard(descs=[{"lines": 16, "brst": 15}] * 9)

    def test_gr_base_zero_rejected(self):
        with pytest.raises(SystemExit, match="gr_base"):
            _guard(descs=[{"lines": 16, "brst": 15}], gr_base=0)

    def test_gr_base_misaligned_rejected(self):
        with pytest.raises(SystemExit, match="16B 对齐"):
            _guard(descs=[{"lines": 16, "brst": 15}], gr_base=0x1008)

    def test_smc_id_en_partial_rejected(self):
        # 2026-10-14 规则变更（立法序，tests/unit/test_8smc_equal_partition.py
        # TestGuards）：{0,7} 已复核放行；部分使能/越界仍响拒
        _guard(descs=[{"lines": 16, "brst": 15}], en=7)   # equal_8smc_v1 广播，放行
        with pytest.raises(SystemExit, match="未复核形态"):
            _guard(descs=[{"lines": 16, "brst": 15}], en=3)

    def test_ur_overflow_rejected(self):
        with pytest.raises(SystemExit, match="UR 写越界"):
            _guard(descs=[{"lines": 16, "brst": 15}], ur_offset=250 * 16)

    def test_window_beyond_preload_rejected(self):
        with pytest.raises(SystemExit, match="越出预载区"):
            _guard(descs=[{"lines": 16, "brst": 15}], gr_base=0xFF10)

    def test_desc_parse_bad_key(self):
        with pytest.raises(SystemExit, match="键非法"):
            G.parse_desc("lines=16,foo=1")


_L1 = [dict(lines=16, brst=15)]
_L2 = [dict(lines=16, brst=15), dict(lines=9, brst=8), dict(lines=8, brst=7)]


class TestEmit:
    def test_l1_ldb_fields(self):
        ldb, axi = G.build_and_emit(_L1, ur_bank=1, ur_offset=0, gr_base=0x1000, smc_id_en=0)
        assert len(ldb) == 1 and len(axi) == 1
        d = IFD.decode(ldb[0], "LDB", MODS)
        assert d["body"]["UR_WR_LINE_CNT"] == 16
        assert d["body"]["LINE_BYTE_CNT"] == 15      # 照抄 w033（ADR-059 R3）
        assert d["body"]["LINE_STRIDE"] == 1
        assert d["body"]["UR_ADDR"] == (1 << 12)     # bank1 off0
        assert d["body"]["SMC_ID_EN"] == 0
        assert d["body"]["CONST"] == 0 and d["body"]["PRE_FILL"] == 0
        assert d["header"]["LOOPS"] == 0 and d["header"]["VLD"] == 1
        assert d["header"]["INC"] == 16              # w033 惰性常数

    def test_l1_axi_fields(self):
        _, axi = G.build_and_emit(_L1, ur_bank=1, ur_offset=0, gr_base=0x1000, smc_id_en=0)
        d = IFD.decode(axi[0], "LDB_AXI", MODS)
        assert d["body"]["BRST"] == 15
        assert d["body"]["GR_BASE_ADDR"] == 0x1000
        assert d["body"]["GR_INTLV_ADDR"] == 0       # w033=0x10000000，门②单 SMC 改编为 0
        assert d["body"]["TID"] == 0                 # LDB 侧照抄 w033（≠STB 侧 2）
        assert d["body"]["SMC_ID_EN"] == 0
        assert d["header"]["INC"] == 256 and d["header"]["LOOPS"] == 0

    def test_l2_aggregate_shape(self):
        """L2 = 镜像 w033 单颗改编：CNT=33 ↔ 3 描述符 brst 15/8/7，gr_base 累进。"""
        ldb, axi = G.build_and_emit(_L2, ur_bank=1, ur_offset=0, gr_base=0x1000, smc_id_en=0)
        assert len(ldb) == 1 and len(axi) == 3
        d = IFD.decode(ldb[0], "LDB", MODS)
        assert d["body"]["UR_WR_LINE_CNT"] == 33     # 聚合恒等 33=16+9+8
        bases, brsts = [], []
        for row in axi:
            da = IFD.decode(row, "LDB_AXI", MODS)
            bases.append(da["body"]["GR_BASE_ADDR"])
            brsts.append(da["body"]["BRST"])
        assert brsts == [15, 8, 7]
        assert bases == [0x1000, 0x1000 + 16 * 16, 0x1000 + 16 * 25]

    def test_emit_deterministic(self):
        a = G.build_and_emit(_L1, 1, 0, 0x1000, 0)
        b = G.build_and_emit(_L1, 1, 0, 0x1000, 0)
        assert a == b

    def test_perturbation_changes_hex(self):
        """负向断言：扰动 ur_bank / gr_base → hex 必变（防假绿）。"""
        base = G.build_and_emit(_L1, 1, 0, 0x1000, 0)
        assert G.build_and_emit(_L1, 2, 0, 0x1000, 0)[0] != base[0]
        assert G.build_and_emit(_L1, 1, 0, 0x2000, 0)[1] != base[1]


class TestMaterialize:
    def _make(self, tmp_path, descs=_L1):
        case_dir = tmp_path / "sbx" / "test_atomic_ops" / "multi_inst_stage" / "t_ldb"
        ldb, axi = G.build_and_emit(descs, 1, 0, 0x1000, 0)
        exp = G.materialize(str(case_dir), descs, 1, 0, 0x1000, 0, ldb, axi,
                            write_hbm_init=False)
        return case_dir, exp

    def test_structure_and_sentinels(self, tmp_path):
        case_dir, exp = self._make(tmp_path)
        assert exp["schema"] == "m3lite.ldb_review_expect/v1"
        # smc_0：全 sentinel；目标 bank 期望终态 = 窗口 src + 其余 sentinel
        rows = (case_dir / "smc_0" / "ur_data" / "bank1.hex").read_text().split()
        assert len(rows) == G.UR_BANK_DEPTH
        assert rows[0] == G.sentinel_line(1, 0)
        fin = (case_dir / "smc_0" / "output_bank" / "bank1_final.hex").read_text().split()
        assert fin[0] == G.src_pattern_line(0)
        assert fin[15] == G.src_pattern_line(15)
        assert fin[16] == G.sentinel_line(1, 16)
        # 修正②：smc_1..7 独立 sentinel′ 显式物化（非空目录、非首跑反钉）
        for i in range(1, 8):
            p = case_dir / f"smc_{i}" / "ur_data" / "bank0.hex"
            assert p.exists(), f"smc_{i} ur_data 缺失"
            r = p.read_text().split()
            assert r[0] == G.sentinel_aux_line(i, 0, 0)
            assert r[255] == G.sentinel_aux_line(i, 0, 255)
        # 三种图案互异（防同值串通）
        assert G.sentinel_line(1, 0) != G.src_pattern_line(0)
        assert G.sentinel_aux_line(1, 0, 0) != G.sentinel_line(0, 0)

    def test_hbm_snapshot_window(self, tmp_path):
        case_dir, exp = self._make(tmp_path, _L2)
        pre = (case_dir / "memory_init_snapshot" / "memory_init1.hex").read_text().split()
        assert len(pre) == G.HBM_PRELOAD_LINES
        win0 = 0x1000 // 16
        assert pre[win0] == G.src_pattern_line(0)
        assert pre[win0 + 32] == G.src_pattern_line(32)
        assert pre[win0 + 33] == G.preload_pattern_line(win0 + 33)  # 窗口外底图
        assert (case_dir / "memory_init_snapshot" / "memory_init8.hex").exists()
        # 全局目录未被写（write_hbm_init=False）
        assert exp["hbm_init_written"] is False

    def test_iq_files_written(self, tmp_path):
        case_dir, exp = self._make(tmp_path, _L2)
        ldb = (case_dir / "smi_iq_inst" / "stage_0" / "iq_ldb.hex").read_text().split()
        axi = (case_dir / "smi_iq_inst" / "stage_0" / "iq_ldb_axi.hex").read_text().split()
        assert len(ldb) == 1 and len(axi) == 3
        assert exp["instructions"]["iq_ldb"] == ldb
