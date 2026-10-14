"""ADR-058 / M3-lite 门①：stb_solo_case_gen 守卫与发射单测。

覆盖：
    - 守卫矩阵：brst=0 禁区 / 配对恒等式 / 越界 / 非对齐 / smc_id_en≠0 → 响亮 SystemExit
    - 发射正路：S1 参数 → 字段级解码 == 期望（镜像 golden row5 形态的最小改编）
    - 负向断言：扰动 ur_bank / gr_base → hex 必变（防假绿）
    - 物化：case 目录结构 + UR golden==预置 + 期望清单自洽
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
import stb_solo_case_gen as G  # noqa: E402


def _guard(*, descs, ur_bank=1, ur_offset=0, gr_base=0x1000, en=0):
    G.guard("t", descs, ur_bank, ur_offset, gr_base, en)


class TestGuards:
    def test_brst0_rejected(self):
        with pytest.raises(SystemExit, match="brst=0"):
            _guard(descs=[{"lines": 1, "brst": 0}])

    def test_pairing_identity_rejected(self):
        with pytest.raises(SystemExit, match="配对恒等式"):
            _guard(descs=[{"lines": 17, "brst": 15}])   # 17 != 1*16

    def test_pairing_identity_loops_ok(self):
        _guard(descs=[{"lines": 32, "brst": 15, "loops": 1}])   # golden row5 形态

    def test_brst_range(self):
        with pytest.raises(SystemExit, match="brst=16 越界"):
            _guard(descs=[{"lines": 17, "brst": 16}])

    def test_lines_range(self):
        with pytest.raises(SystemExit, match="越界"):
            _guard(descs=[{"lines": 256, "brst": 15, "loops": 15}])  # 256=(15+1)*(15+1) 但 >255

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
        with pytest.raises(SystemExit, match="UR 读越界"):
            _guard(descs=[{"lines": 16, "brst": 15}], ur_offset=250 * 16)

    def test_window_beyond_preload_rejected(self):
        with pytest.raises(SystemExit, match="越出预载区"):
            _guard(descs=[{"lines": 16, "brst": 15}], gr_base=0xFF10)  # 行 4081+16=4097 > 4096

    def test_multi_desc_ok(self):
        _guard(descs=[{"lines": 16, "brst": 15}, {"lines": 8, "brst": 7}])

    def test_desc_parse_bad_key(self):
        with pytest.raises(SystemExit, match="键非法"):
            G.parse_desc("lines=16,foo=1")


_S1 = [dict(lines=16, brst=15)]


class TestEmit:
    """S1 发射：字段级对照（镜像 golden row5 的单迭代改编）。"""

    def test_s1_fields(self):
        stb, axi = G.build_and_emit(_S1, ur_bank=1, ur_offset=0, gr_base=0x1000, smc_id_en=0)
        assert len(stb) == len(axi) == 1
        cbits, fields = IFD.body_layout(IFD.load_modules_yaml(
            os.path.join(ROOT, "hw_config", "modules.yaml")), "STB")
        d = IFD.decode_raw(stb[0], cbits, IFD.HDR_LSU, fields)
        body, hdr = d["body"], d["header"]
        assert body["UR_ADDR"] == (1 << 12) and body["UR_RD_LINE_CNT"] == 16
        assert body["LINE_BYTE_CNT"] == 15 and body["LINE_STRIDE"] == 1
        assert body["SMC_ID_EN"] == 0 and body["TID"] == 2
        assert hdr["VLD"] == 1 and hdr.get("LOOPS", 0) == 0 and hdr.get("EMBED", 0) == 0

        cbits, fields = IFD.body_layout(IFD.load_modules_yaml(
            os.path.join(ROOT, "hw_config", "modules.yaml")), "STB_AXI")
        d = IFD.decode_raw(axi[0], cbits, IFD.HDR_LSU, fields)
        body, hdr = d["body"], d["header"]
        assert body["BRST"] == 15 and body["GR_BASE_ADDR"] == 0x1000
        assert body["GR_INTLV_ADDR"] == 0 and body["SMC_ID_EN"] == 0
        assert hdr["VLD"] == 1 and hdr.get("LOOPS", 0) == 0

    def test_s2_loops_form(self):
        """S2 循环形态（镜像 golden row5：lines=32, loops=1）——
        LOOPS 只在 STBAXI 侧；STB 恒 LOOPS=0（golden row5 实证，回归钉）。"""
        stb, axi = G.build_and_emit([dict(lines=32, brst=15, loops=1)],
                                    ur_bank=3, ur_offset=0, gr_base=12288, smc_id_en=0)
        cbits, fields = IFD.body_layout(IFD.load_modules_yaml(
            os.path.join(ROOT, "hw_config", "modules.yaml")), "STB_AXI")
        d = IFD.decode_raw(axi[0], cbits, IFD.HDR_LSU, fields)
        assert d["header"]["LOOPS"] == 1 and d["header"]["INC"] == 256
        assert d["body"]["BRST"] == 15 and d["body"]["GR_BASE_ADDR"] == 12288
        cbits, fields = IFD.body_layout(IFD.load_modules_yaml(
            os.path.join(ROOT, "hw_config", "modules.yaml")), "STB")
        d = IFD.decode_raw(stb[0], cbits, IFD.HDR_LSU, fields)
        assert d["header"].get("LOOPS", 0) == 0, "STB 侧不得带 LOOPS（golden row5 形态）"
        assert d["body"]["UR_RD_LINE_CNT"] == 32

    def test_multi_desc_address_progression(self):
        """S3 形态：两条描述符地址累进（+256B）。"""
        stb, axi = G.build_and_emit([dict(lines=16, brst=15), dict(lines=8, brst=7)],
                                    ur_bank=1, ur_offset=0, gr_base=0x1000, smc_id_en=0)
        assert len(stb) == len(axi) == 2
        cbits, fields = IFD.body_layout(IFD.load_modules_yaml(
            os.path.join(ROOT, "hw_config", "modules.yaml")), "STB_AXI")
        d0 = IFD.decode_raw(axi[0], cbits, IFD.HDR_LSU, fields)["body"]
        d1 = IFD.decode_raw(axi[1], cbits, IFD.HDR_LSU, fields)["body"]
        assert d1["GR_BASE_ADDR"] - d0["GR_BASE_ADDR"] == 16 * 16
        assert d1["BRST"] == 7

    @pytest.mark.parametrize("key,delta", [("ur_bank", 1), ("gr_base", 16)])
    def test_perturbation_flips_hex(self, key, delta):
        kw = dict(ur_bank=1, ur_offset=0, gr_base=0x1000, smc_id_en=0)
        a = G.build_and_emit(_S1, **kw)
        kw[key] += delta
        b = G.build_and_emit(_S1, **kw)
        assert a != b, f"扰动 {key} 输出未变（假绿！）"


class TestMaterialize:
    def test_case_layout(self, tmp_path, monkeypatch):
        monkeypatch.setattr(G, "HBM_INIT_DIR", str(tmp_path / "hbm_init"))
        case = str(tmp_path / "case")
        stb, axi = G.build_and_emit(_S1, 1, 0, 0x1000, 0)
        exp = G.materialize(case, _S1, 1, 0, 0x1000, 0, stb, axi, write_hbm_init=True)

        # 结构：8 bank ur_data + 8 golden + 2 iq + expect + 快照
        for b in range(8):
            ur = open(os.path.join(case, "smc_0", "ur_data", f"bank{b}.hex")).read().split()
            gold = open(os.path.join(case, "smc_0", "output_bank",
                                     f"bank{b}_final.hex")).read().split()
            assert ur == gold and len(ur) == 256
            if b == 1:
                assert ur[0] == G.ur_pattern_line(0) and ur[15] == G.ur_pattern_line(15)
                assert ur[16] == G.ZERO_LINE
            else:
                assert set(ur) == {G.ZERO_LINE}
        assert os.path.exists(os.path.join(case, "smi_iq_inst", "stage_0", "iq_stb.hex"))
        # smc_1..7 空目录：TB 探针命中 → dump 全 8 颗 HBM（A4c en 泄漏证据载体）
        for i in range(1, 8):
            d = os.path.join(case, f"smc_{i}")
            assert os.path.isdir(d) and os.listdir(d) == []
        # 全局预载 + 副本
        pre = open(os.path.join(str(tmp_path / "hbm_init"), "memory_init1.hex")).read().split()
        assert len(pre) == G.HBM_PRELOAD_LINES and pre[0] == G.preload_pattern_line(0)
        snap = open(os.path.join(case, "memory_init_snapshot", "memory_init1.hex")).read().split()
        assert snap == pre
        # 期望清单自洽
        on_disk = json.load(open(os.path.join(case, "hbm_expect.json")))
        assert on_disk["schema"] == "m3lite.stb_solo_expect/v1"
        assert on_disk["window"] == {"byte_base": 0x1000, "lines": 16}
        assert on_disk["instructions"]["iq_stb"] == stb
