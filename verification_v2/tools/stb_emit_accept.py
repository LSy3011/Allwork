#!/usr/bin/env python3
"""stb_emit_accept —— ADR-052 D5 验收：走**真 LIRPacker** emit iq_stb.hex / iq_stb-axi.hex，
   与 `sub_8smc_v3/stage_2` 的手写 golden 做**整数逐位**比对。

为什么比整数不比字符串（ADR-052 §4）：
    emitter.py 用 `hex_chars=(total_bits+3)//4`（半字节对齐），权威/手写线用 `(header+code+7)//8`
    （字节对齐）。STB(100b) 差 1 字符、STB_AXI(152b) 恰好相等 —— **数值等价，纯 padding 差异**。
    文本 diff 会把等价物报成差异。

覆盖（ADR-052 D5 四步）：
    ① 真 emitter 产出 iq_stb.hex / iq_stb-axi.hex
    ② 与 golden row5 逐整数相等
    ③ 字段级解码对拍（复用 iq_field_diff.py 的位表，人可读）
    ④ **负向断言**：扰动源 bank / gr_base_addr → 输出必须随之变化（防假绿）

用法：
    python verification_v2/tools/stb_emit_accept.py
    STB_GOLDEN_DIR=<dir> python verification_v2/tools/stb_emit_accept.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 与 main_verify.py:5-7 同约定：仓库根（支持 `from src.driver...`）+ src（支持 `from driver...`）
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, "src"))

from driver.emitter import LIRPacker                    # noqa: E402
from ir.lir.lir_op import PhysicalInstruction, SMIHeader  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import iq_field_diff as IFD                             # noqa: E402

GOLDEN = os.environ.get("STB_GOLDEN_DIR", os.path.expanduser(
    "~/u280_sim_v422/test_atomic_ops/multi_inst_stage/sub_8smc_v3/smi_iq_inst/stage_2"))

# ── golden row5 的参数（语义参数，非位串；位串一律从磁盘读）──────────────────
# 对应 meta_v3.json {out_bank:3, out_words:32, store_byte_base:12288}
STB_PARAMS = dict(
    iq_name="stb", abs_t=0,
    header=dict(inc=16, vld=1),
    payload={"VALID": 1, "ur_addr": 12288, "ur_rd_line_cnt": 32, "line_byte_cnt": 15,
             "line_stride": 1, "inst_id": 0, "smc_id_en": 7, "tid": 2},
)
STBAXI_PARAMS = dict(
    iq_name="stb-axi", abs_t=0,
    header=dict(inc=256, loops=1, vld=1),
    payload={"VALID": 1, "first_byte_strb": 0, "last_byte_strb": 0, "brst": 15,
             "gr_base_addr": 12288, "gr_intlv_addr": 0x10000000,
             "inst_id": 0, "smc_id_en": 7},
)
GOLDEN_ROW = 4          # 0-based → 第 5 行（最终输出写回）


def emit_one(params: dict, case_root: str):
    """用真 LIRPacker emit 一条指令，返回产出的 hex 行。"""
    packer = LIRPacker(config_dir="hw_config", verbosity=0)
    hdr = SMIHeader(**params["header"])
    instr = PhysicalInstruction(iq_name=params["iq_name"], header=hdr,
                                payload=dict(params["payload"]), abs_t=params["abs_t"],
                                stage="BODY")
    packer.emit_from_instructions([instr], case_root, smc_num=1)
    out = os.path.join(case_root, "smc_0", f"iq_{params['iq_name']}.hex")
    if not os.path.exists(out):
        raise FileNotFoundError(f"emitter 未产出 {out}（目录内容：{os.listdir(os.path.join(case_root,'smc_0'))}）")
    with open(out, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    return lines[0]


def main() -> int:
    if not os.path.isdir(GOLDEN):
        print(f"❌ golden 目录不存在：{GOLDEN}\n   （可用 STB_GOLDEN_DIR 覆盖）")
        return 2

    ok = True
    for p, gold_file in ((STB_PARAMS, "iq_stb.hex"), (STBAXI_PARAMS, "iq_stb_axi.hex")):
        unit = p["iq_name"].replace("-", "_").upper()
        with open(os.path.join(GOLDEN, gold_file), encoding="utf-8") as f:
            gold = [l.strip() for l in f if l.strip()][GOLDEN_ROW]

        tmp = tempfile.mkdtemp(prefix=f"stb_accept_{unit}_")
        try:
            got = emit_one(p, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        g_i, m_i = int(gold, 16), int(got, 16)
        same = (g_i == m_i)
        ok &= same
        print(f"[{unit}] emitter 产出 iq_{p['iq_name']}.hex")
        print(f"   golden({len(gold)}字) = {gold}")
        print(f"   emitted({len(got)}字) = {got}")
        print(f"   {'✅ 整数全等' if same else '❌ 整数差异 = 0x%x' % (g_i ^ m_i)}"
              f"   （行宽差 {len(got)-len(gold)} 字符 = 半字节对齐 padding，非差异）")

        # ③ 字段级解码对拍（共用 iq_field_diff 的位表，人可读）
        cbits, fields = IFD.body_layout(IFD.load_modules_yaml(
            os.path.join(ROOT, "hw_config", "modules.yaml")), unit)
        dg = IFD.decode_raw(gold, cbits, IFD.HDR_LSU, fields)
        dm = IFD.decode_raw(got, cbits, IFD.HDR_LSU, fields)
        diffs = [(s, k, dm[s][k], dg[s][k]) for s in ("header", "body")
                 for k in dm[s] if dm[s][k] != dg[s][k]]
        print(f"   字段级：{IFD.fmt(dm)}")
        if diffs:
            ok = False
            for s, k, mv, gv in diffs:
                print(f"     ✗ {s}.{k}: 我={mv} 参考={gv}")
        print()

    # ④ 负向断言：扰动必须翻转输出（防假绿）
    print("[负向断言] 扰动源 bank / gr_base_addr → 输出必须变化")
    for p, label, key, delta in ((STB_PARAMS, "STB ur_addr(bank)", "ur_addr", 1 << 12),
                                 (STBAXI_PARAMS, "STB_AXI gr_base_addr", "gr_base_addr", 16)):
        tmp = tempfile.mkdtemp(prefix="stb_neg_")
        try:
            base = emit_one(p, tmp)
            p2 = dict(p, payload=dict(p["payload"], **{key: p["payload"][key] + delta}))
            pert = emit_one(p2, tmp + "_b")
            changed = int(base, 16) != int(pert, 16)
            ok &= changed
            print(f"   {label:24} {p['payload'][key]} → {p['payload'][key]+delta}: "
                  f"{'✅ 输出翻转' if changed else '❌ 输出未变（假绿！）'}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(tmp + "_b", ignore_errors=True)

    print(f"\n{'✅ STB EMIT 验收通过（ADR-052 D5）' if ok else '❌ STB EMIT 验收失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
