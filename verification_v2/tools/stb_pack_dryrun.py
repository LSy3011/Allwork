#!/usr/bin/env python3
"""stb_pack_dryrun —— ADR-052 D1 的内存 dry-run：把拟写进 modules.yaml 的 STB/STB_AXI 字段表
   喂给**真 BitPacker**，对拍手写 golden。不写任何仓库文件。"""
import os, sys, types, yaml
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
from api.hw_spec import ModuleSpec, ModuleField
from driver.packer import BitPacker

# ── 拟写入 modules.yaml 的字段表（单一事实源，落盘时逐字复制）──
STB_FIELDS = [
    ("VALID", 1), ("ur_addr", 15), ("ur_rd_line_cnt", 8), ("line_byte_cnt", 9),
    ("line_stride", 6), ("inst_id", 4), ("smc_id_en", 5), ("tid", 2),
]
STBAXI_FIELDS = [
    ("VALID", 1), ("first_byte_strb", 4), ("last_byte_strb", 4), ("brst", 4),
    ("gr_base_addr", 40), ("gr_intlv_addr", 40), ("inst_id", 4), ("smc_id_en", 5),
]
def mkmod(name, fields):
    return ModuleSpec(type="transfer", latency=1,
                      code_bits=sum(w for _, w in fields),
                      code_fields=[ModuleField(name=n, bits=w) for n, w in fields])

mods = dict(yaml.safe_load(open(os.path.join(ROOT, "hw_config", "modules.yaml"), encoding="utf-8"))["modules"])
mods["STB"] = mkmod("STB", STB_FIELDS)
mods["STB_AXI"] = mkmod("STB_AXI", STBAXI_FIELDS)
spec = types.SimpleNamespace(modules=mods)

# ── 附加守卫：真实 modules.yaml 的 STB/STB_AXI 条目是否 == 上面这张已验证的表 ──
#   实现前：yaml 还是 7b stub → 报「未实现」（非失败）；实现后：必须逐字段相同 → 报一致。
#   作用：防止 yaml 转录漂移（dry-run 验的是这张表，落盘若抄错就白验了）。
print("─" * 68)
live = yaml.safe_load(open(os.path.join(ROOT, "hw_config", "modules.yaml"), encoding="utf-8"))["modules"]
# D1 落地前的已知 stub（用于区分「尚未实现」与「实现后转录漂移」两种红灯）
STUBS = {"STB": ([("VLD", 1), ("SLOT_ID", 6)], 7), "STB_AXI": (None, None)}
_drift = False
for u, want in (("STB", STB_FIELDS), ("STB_AXI", STBAXI_FIELDS)):
    want_bits = sum(w for _, w in want)
    if u not in live:
        print(f"  [yaml 守卫] {u}: 条目不存在 → 未实现（D1 待做，非失败）")
        continue
    got = [(f["name"].upper(), int(f["bits"])) for f in live[u]["code_fields"]]
    got_bits = int(live[u]["code_bits"])
    want_u = [(n.upper(), w) for n, w in want]          # 大小写归一后再比（yaml 混用大小写）
    stub_fields, stub_bits = STUBS[u]
    if got == want_u and got_bits == want_bits:
        print(f"  [yaml 守卫] {u}: ✅ 与已验证字段表逐字段一致（code_bits={got_bits}）")
    elif stub_fields is not None and got == stub_fields and got_bits == stub_bits:
        print(f"  [yaml 守卫] {u}: 仍是 {stub_bits}b stub → 未实现（D1 待做，非失败）")
    else:
        print(f"  [yaml 守卫] {u}: ❌ 与已验证字段表不一致 —— 转录漂移！")
        print(f"        yaml = {got} (code_bits={got_bits})")
        print(f"        期望 = {want} (code_bits={want_bits})")
        _drift = True
print("─" * 68)

print("字段宽度自洽校验：")
print(f"  STB     求和={sum(w for _,w in STB_FIELDS):3d}  期望 50  {'✓' if sum(w for _,w in STB_FIELDS)==50 else '✗'}")
print(f"  STB_AXI 求和={sum(w for _,w in STBAXI_FIELDS):3d}  期望 102 {'✓' if sum(w for _,w in STBAXI_FIELDS)==102 else '✗'}")
print(f"  get_unit_bits(STB)={BitPacker.get_unit_bits(spec,'STB')} (期望 100)  "
      f"get_unit_bits(STB_AXI)={BitPacker.get_unit_bits(spec,'STB_AXI')} (期望 152)")

# 手写 golden（可被 --golden 覆盖）
G = os.environ.get("STB_GOLDEN_DIR",
    os.path.expanduser("~/u280_sim_v422/test_atomic_ops/multi_inst_stage/sub_8smc_v3/smi_iq_inst/stage_2"))
gold_stb = [l.strip() for l in open(f"{G}/iq_stb.hex") if l.strip()]
gold_sx  = [l.strip() for l in open(f"{G}/iq_stb_axi.hex") if l.strip()]

CASES = [
  ("STB",     "iq_stb.hex",     4, {"INC":16, "VLD":1},
   {"VALID":1,"ur_addr":12288,"ur_rd_line_cnt":32,"line_byte_cnt":15,
    "line_stride":1,"inst_id":0,"smc_id_en":7,"tid":2}),
  ("STB_AXI", "iq_stb_axi.hex", 4, {"INC":256, "LOOPS":1, "VLD":1},
   {"VALID":1,"first_byte_strb":0,"last_byte_strb":0,"brst":15,"gr_base_addr":12288,
    "gr_intlv_addr":0x10000000,"inst_id":0,"smc_id_en":7}),
]
print("\n真 packer 对拍 golden（比整数，不比字符串）：")
allok = True
for unit, fn, row, hdr, body in CASES:
    got = BitPacker.pack_capsule(spec, unit, body, is_body=True, **hdr)
    exp = int(gold_stb[row] if unit == "STB" else gold_sx[row], 16)
    ok = got == exp
    allok &= ok
    print(f"  {unit:8} row{row+1}: got=0x{got:0{len(gold_stb[row])}x}  exp=0x{exp:0{len(gold_stb[row])}x}  "
          f"{'✅ 整数全等' if ok else '❌ 差 0x%x' % (got ^ exp)}")
if _drift:
    print("\n❌ yaml 守卫失败：modules.yaml 的 STB/STB_AXI 条目与已验证字段表不一致")
elif allok:
    print("\n✅ DRY-RUN 通过：D1 字段表可被真 packer 还原 golden")
else:
    print("\n❌ DRY-RUN 失败：字段表无法还原 golden")
sys.exit(0 if (allok and not _drift) else 1)
