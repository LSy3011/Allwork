"""ADR-052 回归守门：STB / STB_AXI（Store-to-HBM）规格注册 + 位布局 + 归位链路。

hco 2026-09-10 审查建议的 5 条守门，逐条对应一个**已发生**或**已实测复现**的失效模式：

  ① iq_registry 撞号 —— 改动前 `STB: 7` 与 `INTTOINT: 7` 同 id。hco 复核确认当时无功能依赖
     （`get_iq_ids("STB")` 为空、反向寻址不可见），但同 id 是 latent 冲突：任何按 id 建表 /
     反查的工具都会静默取错单元。
     ⚠ 必须查**两份** specs —— `hw_spec.py:186` 优先取 `master.yaml`，`system_core.yaml` 只是
     回退；只查生效的那份会漏掉回退文件里的冲突（改 master 后 system_core 就长期无人看）。
  ② `stb-axi` 前缀吞噬 —— modules.yaml 无 STB_AXI 条目时，`resolve_iq_type` 精确匹配落空
     → 最长前缀兜底命中 7-bit 的 STB stub → 需 102 位处打 7 位，**静默产错固件**
     （ADR-052 D4 实测：`resolve_iq_type('stb-axi') -> STB code_bits=7`）。
  ③ get_iq_name 的**两份**清单 —— `hw_spec.py:322` `unit_type` 快路径 + `:341` 全局扫描兜底。
     只改一份时另一条路径返回 `stb_axi-0` → `resolve_iq_type` 抛 ValueError（ADR-052 D2）。
     本测**显式走两条路径**并断言同值。
  ④ 编译器逻辑 id ↔ RTL 槽号是**两个命名空间** —— 编译器 52/53（清晰空档，见 D2），
     RTL 是 iq_inst[24]/[25]。二者靠 `iq_{name}.hex` **文件名**桥接。
     桥的 TB 侧在编译器仓库**外**（`~/u280_sim_v422/generate_data_list_coverage.py`），
     故用 AST 读表 + 不在场则 skip —— 不在无 TB 的环境里假红。
  ⑤ 镜像关系（ADR-052 §2 的核心依据，doc-first 下最该被钉死的一条）：
     `STB = LDB − {const(6), pre_fill(1), post_fill(1)} + {tid(2)}`，后段**整体下移 8 位**；
     `STB_AXI = LDB_AXI − {tid(2)}`。此式若被后续改动悄悄破坏，位布局即错位。

KAT（最后一条）走**真 hw_config 加载的 spec** + 真 `BitPacker`，还原手写 golden 第 5 行。
与 `verification_v2/tools/stb_pack_dryrun.py` 的分工：dryrun 验「代码里那张拟写表」并独立守
yaml 转录漂移；本测验「**已落盘的 spec** 在真实加载路径下」的行为，覆盖 master 覆盖逻辑
（`_load_fresh` 会用 master `functional_units` 覆盖 code_fields）。
"""
import ast
import collections
import os
import sys
import unittest
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))

from api.hw_spec import HardwareSpec                      # noqa: E402
from driver.packer import BitPacker                       # noqa: E402
from driver.payload_factory import UnitPayloadFactory     # noqa: E402

# 两份 specs 都必须查（见模块 docstring ①）。
_SPEC_FILES = {
    "master": _ROOT / "hw_config" / "specs" / "aiacc_v2.4_master.yaml",
    "system_core": _ROOT / "hw_config" / "specs" / "system" / "system_core.yaml",
}
# TB 侧归位表（编译器仓库外，见 docstring ④）
_TB_PATH = Path(os.path.expanduser("~/u280_sim_v422/generate_data_list_coverage.py"))

# 手写 golden：sub_8smc_v3（8-SMC 已通过 case）的 Store 阶段
_GOLDEN_DIR = Path(os.environ.get(
    "STB_GOLDEN_DIR",
    os.path.expanduser("~/u280_sim_v422/test_atomic_ops/multi_inst_stage/"
                       "sub_8smc_v3/smi_iq_inst/stage_2")))
# golden 第 5 行（0-based 4）= 最终输出写回，四重交叉验证见 ADR-052 §3
_GOLDEN_ROW = 4
_KAT_CASES = (
    # (单元, golden 文件, header 关键字, body)
    ("STB", "iq_stb.hex", {"INC": 16, "VLD": 1},
     {"VALID": 1, "ur_addr": 12288, "ur_rd_line_cnt": 32, "line_byte_cnt": 15,
      "line_stride": 1, "inst_id": 0, "smc_id_en": 7, "tid": 2}),
    ("STB_AXI", "iq_stb_axi.hex", {"INC": 256, "LOOPS": 1, "VLD": 1},
     {"VALID": 1, "first_byte_strb": 0, "last_byte_strb": 0, "brst": 15,
      "gr_base_addr": 12288, "gr_intlv_addr": 0x10000000,
      "inst_id": 0, "smc_id_en": 7}),
)


def _fields(mod):
    """[(字段名, 位宽)]，保持声明序 == 物理位序（packer 按声明序累加 curr_pos）。"""
    return [(f.name, int(f.bits)) for f in (mod.code_fields or [])]


def _offset_of(fields, name):
    """字段起始位（低→高累加）。"""
    pos = 0
    for n, w in fields:
        if n == name:
            return pos
        pos += w
    raise KeyError(f"字段 {name!r} 不在 {[n for n, _ in fields]} 中")


class TestStbSchemaRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 绕开 ADR-015 pickle 缓存，保证读的是磁盘现状（避免测到旧缓存而假绿）
        os.environ["AIACC_NO_HW_SPEC_CACHE"] = "1"
        # ⚠ 必须传**绝对路径**：`HardwareSpec.load` 按字面 config_dir 找文件，相对路径依赖 cwd
        #   （pytest 从仓库根跑才碰巧能过，换目录即 KeyError: 'hardware_params'）。
        cls.spec = HardwareSpec.load(str(_ROOT / "hw_config"))

    # ── ① iq_registry 唯一性（两份 specs）────────────────────────────────
    def test_iq_registry_no_duplicate_id_in_either_spec(self):
        bad = []
        for label, path in _SPEC_FILES.items():
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            reg = doc.get("iq_registry") or doc.get("system_core", {}).get("iq_registry", {})
            self.assertTrue(reg, f"{label} 里找不到 iq_registry")
            by_id = collections.defaultdict(list)
            for unit, iq_id in reg.items():
                by_id[iq_id].append(unit)
            for iq_id, units in sorted(by_id.items()):
                if len(units) > 1:
                    bad.append(f"{label}: iq_id={iq_id} 被 {units} 共用（改动前 STB:7 / INTTOINT:7 即此病）")
        self.assertEqual(bad, [], "iq_registry 同 id 冲突（按 id 反查会静默取错单元）：\n  "
                         + "\n  ".join(bad))

    def test_two_specs_agree_on_store_and_load_family(self):
        """master 与 system_core 是同一份事实的两个副本，对 STB/LDB 族必须逐值一致。"""
        regs = {}
        for label, path in _SPEC_FILES.items():
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            regs[label] = doc.get("iq_registry") or doc.get("system_core", {}).get("iq_registry", {})
        for unit in ("LDB", "LDB_AXI", "STB", "STB_AXI"):
            vals = {lbl: reg.get(unit) for lbl, reg in regs.items()}
            self.assertIsNotNone(vals["master"],
                                 f"{unit} 不在 master iq_registry（emit 时 _get_registry_val 会抛异常）")
            self.assertEqual(
                vals["master"], vals["system_core"],
                f"{unit} 在两份 specs 里 id 不一致：{vals}（ADR-052 D2 的 clear-gap 范式要求一致）")

    # ── ② 前缀吞噬 ────────────────────────────────────────────────────────
    def test_stb_axi_not_swallowed_by_stb_prefix(self):
        """modules.yaml 缺 STB_AXI 条目时，'stb-axi' 会被 'stb-' 前缀吞成 7-bit stub。"""
        self.assertIn("STB_AXI", self.spec.modules,
                      "modules.yaml 缺 STB_AXI → resolve_iq_type('stb-axi') 会静默返回 7-bit STB stub")
        self.assertEqual(self.spec.modules["STB_AXI"].code_bits, 102)
        self.assertEqual(self.spec.modules["STB"].code_bits, 50)

    def test_resolve_iq_type_store_and_load_family(self):
        for name, want, bits in (("stb", "STB", 50), ("stb-axi", "STB_AXI", 102),
                                 ("ldb", "LDB", 56), ("ldb-axi", "LDB_AXI", 104)):
            got = UnitPayloadFactory.resolve_iq_type(name, self.spec)
            self.assertEqual(got, want, f"resolve_iq_type({name!r}) 归位错（前缀吞噬？）")
            self.assertEqual(self.spec.modules[got].code_bits, bits, f"{got} 位宽不符")

    # ── ③ get_iq_name 两条路径 ────────────────────────────────────────────
    def test_get_iq_name_both_paths_and_no_idx_suffix(self):
        """断言两条路径同值 —— 只改一份清单时另一条会带出 '-0' 后缀（ADR-052 D2）。"""
        for iq_id, want in ((52, "stb"), (53, "stb_axi"), (50, "ldb"), (51, "ldb_axi")):
            fast = self.spec.get_iq_name(iq_id, want.upper())          # unit_type 快路径
            scan = self.spec.get_iq_name(iq_id)                        # 全局扫描兜底
            self.assertEqual(fast, want, f"快路径 get_iq_name({iq_id}, {want.upper()!r}) 带 idx 后缀")
            self.assertEqual(scan, want, f"全局扫描 get_iq_name({iq_id}) 带 idx 后缀")
            self.assertEqual(fast, scan, f"两条路径不一致：{fast!r} vs {scan!r}")

    # ── ⑤ 镜像关系（ADR-052 §2 核心依据）──────────────────────────────────
    def test_mirror_relation_stb_from_ldb(self):
        """STB = LDB − {const,pre_fill,post_fill} + {tid}；后段整体下移 8 位。"""
        ldb, stb = _fields(self.spec.modules["LDB"]), _fields(self.spec.modules["STB"])
        dropped = [n for n in ("const", "pre_fill", "post_fill")]

        # 共享前缀（VALID..line_stride）：宽度逐项相同，名字仅 wr/rd 一字之差
        self.assertEqual([w for _, w in ldb[:5]], [w for _, w in stb[:5]],
                         "STB 与 LDB 的共享前缀宽度不再一致")
        for (ln, lw), (sn, sw) in zip(ldb[:5], stb[:5]):
            self.assertEqual(ln.replace("wr_", "").replace("rd_", ""),
                             sn.replace("wr_", "").replace("rd_", ""),
                             f"前缀字段名漂移：LDB {ln!r} vs STB {sn!r}")

        # 删/增的字段集（用集合比，避免声明序之争）
        # ⚠ 先做 wr/rd 归一再比 —— `ur_wr_line_cnt`(LDB) → `ur_rd_line_cnt`(STB) 是**改名**而非删增，
        #   不归一会把它误算成「LDB 多出」。改名本身由上面的前缀循环（含宽度）单独守着。
        def norm(fs):
            return {n.replace("wr_", "").replace("rd_", "") for n, _ in fs}

        self.assertEqual(norm(ldb) - norm(stb), set(dropped),
                         "LDB 相对 STB 多出的字段集不再是 {const, pre_fill, post_fill}")
        self.assertEqual(norm(stb) - norm(ldb), {"tid"},
                         "STB 相对 LDB 多出的字段集不再是 {tid}")

        # 位宽自洽 + 后段下移 8 位（不是 6 —— 见 ADR-052 §2 的 ⚠ 订正）
        removed = sum(dict(ldb)[n] for n in dropped)
        ldb_total, stb_total = sum(w for _, w in ldb), sum(w for _, w in stb)
        self.assertEqual(ldb_total - removed + dict(stb)["tid"], stb_total,
                         f"宽度不闭合：LDB={ldb_total} − 删{removed} + 增{dict(stb)['tid']} ≠ STB={stb_total}")
        for f in ("inst_id", "smc_id_en"):
            shift = _offset_of(ldb, f) - _offset_of(stb, f)
            self.assertEqual(shift, 8, f"{f} 在 STB 里相对 LDB 下移了 {shift} 位（应为 8，非 6）")

    def test_mirror_relation_stbaxi_from_ldbaxi(self):
        """STB_AXI = LDB_AXI − {tid}；前 102 位逐字段同址同宽。"""
        lax, sax = _fields(self.spec.modules["LDB_AXI"]), _fields(self.spec.modules["STB_AXI"])
        self.assertEqual(sax, lax[:-1],
                         "STB_AXI 不再等于「LDB_AXI 去掉末尾 tid(2b)」")
        self.assertEqual(lax[-1], ("tid", 2), f"LDB_AXI 末尾字段不再是 tid(2b)：{lax[-1]}")
        self.assertEqual(sum(w for _, w in lax), sum(w for _, w in sax) + 2)

    # ── KAT：真 spec + 真 packer 还原手写 golden ──────────────────────────
    def test_kat_reproduces_golden_row5(self):
        for unit, fname, hdr, body in _KAT_CASES:
            with self.subTest(unit=unit):
                self.assertEqual(BitPacker.get_unit_bits(self.spec, unit),
                                 {"STB": 100, "STB_AXI": 152}[unit],
                                 f"{unit} 未走 50b LSU header（packer._LSU_UNITS）")
                gold = _GOLDEN_DIR / fname
                if not gold.exists():
                    self.skipTest(f"手写 golden 不在场（{_GOLDEN_DIR}）—— 用 STB_GOLDEN_DIR 覆盖")
                exp_line = [l.strip() for l in gold.read_text(encoding="utf-8").splitlines()
                            if l.strip()][_GOLDEN_ROW]
                got = BitPacker.pack_capsule(self.spec, unit, dict(body), is_body=True, **hdr)
                # ⚠ 比整数不比字符串：emitter 半字节对齐 vs 权威字节对齐，差 1 字符是等价 padding
                self.assertEqual(
                    got, int(exp_line, 16),
                    f"{unit} KAT 不符：got=0x{got:x} exp={exp_line}（ADR-052 §4 的宽度约定）")

    # ── ④ 归位桥：编译器逻辑 id ↔ RTL 槽号 ───────────────────────────────
    @unittest.skipUnless(_TB_PATH.exists(), f"TB 不在场（{_TB_PATH}）—— 跨仓库守卫跳过")
    def test_tb_slot_map_bridges_compiler_names_to_rtl_slots(self):
        src = _TB_PATH.read_text(encoding="utf-8")
        table = None
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "IQ_NAME_TO_ID" for t in node.targets):
                table = ast.literal_eval(node.value)
        self.assertIsNotNone(table, "TB 里找不到 IQ_NAME_TO_ID 表")

        # RTL 槽号（与编译器逻辑 id 52/53 是**两个**命名空间，靠文件名桥接）
        for token, rtl_slot, iq_id in (("stb", 24, 52), ("stb_axi", 25, 53),
                                       ("ldb", 26, 50), ("ldb_axi", 27, 51)):
            compiler_name = self.spec.get_iq_name(iq_id)          # 如 "stb_axi"（无 idx 后缀）
            self.assertEqual(compiler_name, token,
                             f"编译器 id {iq_id} 产出的名 {compiler_name!r} 与 TB token {token!r} 不符")
            self.assertEqual(table.get(token), rtl_slot,
                             f"TB 表 {token} → {table.get(token)}，期望 RTL 槽 {rtl_slot}")

        # 桥的另一半：编译器的 '_' 会变成文件名里的 '-'，TB 必须做 '-'→'_' 归一才认得出
        self.assertRegex(src, r"replace\('-',\s*'_'\)",
                         "TB 不再做 '-'→'_' 归一 → 'iq_stb-axi.hex' 无法归位（ADR-052 D2 的静默失败）")


if __name__ == "__main__":
    unittest.main()
