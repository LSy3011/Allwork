#!/usr/bin/env python3
"""case 指令使能与地址解码器（8-SMC 预检订正步①工具，2026-10-14）。

对一个 multi_inst_stage case 目录，逐 stage 逐 iq_*.hex 解码：
- body 字段（modules.yaml code_fields，LSB 递增，与 BitPacker 同序动态推导，
  禁硬编码位偏移）——重点提取 smc_id_en（RTOVR 无此字段，ADR-031 明文）；
- header 字段（ISA_V5 41bit / LSU 50bit，按单元族选变体，与 packer 同一
  _iq_header_for）——提取 vld/loops；
- 地址字段（ur_addr / gr_base_addr / BANK / ADDR / 行计数）原值列出。

输出：stdout 人读表 + 可选 --json 供 manifest 一致性校验（步②）复用。
缺席纪律：未知文件名/未知单元/带 micro_groups 单元一律记入 undecoded，
不静默跳过、不猜布局。

用法：
    python3 verification_v2/tools/decode_case_smc_enablement.py <case_dir> [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, "src"))

from driver.emitter import LIRPacker                      # noqa: E402
from driver.packer import ISA_V5_HEADER, ISA_V5_HEADER_LSU, _LSU_UNITS  # noqa: E402

# 文件名（小写、去 iq_ 前缀与 .hex）→ 单元类型。ur-r0/ur-w2 等带槽号名按前缀归族。
_NAME_MAP = {
    "ldb": "LDB", "ldb_axi": "LDB_AXI", "ldb-axi": "LDB_AXI",
    "stb": "STB", "stb_axi": "STB_AXI", "stb-axi": "STB_AXI",
    "fpmul": "FPMUL", "fpadd": "FPADD",
    "vin0": "VIN0", "vin1": "VIN1", "vout0": "VOUT0",
}
_PREFIX_MAP = (("rtovr", "RTOVR"), ("ur_r", "UR_READ"), ("ur-r", "UR_READ"),
               ("ur_w", "UR_WRITE"), ("ur-w", "UR_WRITE"))


def _unit_of(fname: str) -> str | None:
    stem = fname.lower()
    if stem.startswith("iq_"):
        stem = stem[3:]
    if stem.endswith(".hex"):
        stem = stem[:-4]
    if stem in _NAME_MAP:
        return _NAME_MAP[stem]
    for pre, unit in _PREFIX_MAP:
        if stem.startswith(pre):
            return unit
    return None


def _decode_fields(val: int, fields) -> dict:
    out, pos = {}, 0
    for f in fields:
        out[f.name] = (val >> pos) & ((1 << f.bits) - 1)
        pos += f.bits
    return out


def decode_case(case_dir: str) -> dict:
    packer = LIRPacker(config_dir=os.path.join(ROOT, "hw_config"), verbosity=0)
    spec = packer.hw_spec
    inst_root = os.path.join(case_dir, "smi_iq_inst")
    if not os.path.isdir(inst_root):
        raise SystemExit(f"[DECODE] 无 smi_iq_inst 目录: {inst_root}")

    stages, undecoded = [], []
    for sname in sorted(os.listdir(inst_root)):
        sdir = os.path.join(inst_root, sname)
        if not (os.path.isdir(sdir) and sname.startswith("stage_")):
            continue
        files = sorted(f for f in os.listdir(sdir) if f.startswith("iq_") and f.endswith(".hex"))
        recs = []
        for fn in files:
            unit = _unit_of(fn)
            path = os.path.join(sdir, fn)
            lines = [l.strip() for l in open(path, encoding="utf-8") if l.strip()]
            if unit is None or unit not in spec.modules:
                undecoded.append({"stage": sname, "file": fn,
                                  "reason": f"未知单元或 spec 缺模块: {unit}"})
                continue
            module = spec.modules[unit]
            if getattr(module, "micro_groups", None):
                undecoded.append({"stage": sname, "file": fn,
                                  "reason": f"{unit} 带 micro_groups，未指定 variant 不猜布局"})
                continue
            hmap = ISA_V5_HEADER_LSU if unit in _LSU_UNITS else ISA_V5_HEADER
            body_bits = module.code_bits
            smc_vals, smc_vld1, addrs, loops, vlds = set(), set(), {}, set(), set()
            for ln in lines:
                val = int(ln, 16)
                body = val & ((1 << body_bits) - 1)
                hdr = (val >> body_bits)
                bf = _decode_fields(body, module.code_fields)
                hf = {k: (hdr >> s) & ((1 << w) - 1) for k, (s, w) in hmap.items()}
                vlds.add(hf.get("VLD", 0))
                # 决定性口径 = body VALID=1（单元真正动作）。
                # header vld=1 + body VALID=0 = 队列终止项（JUMP），en 为无关值。
                body_valid = bf.get("VALID", hf.get("VLD", 0))
                if hf.get("VLD", 0):
                    loops.add(hf.get("LOOPS", 0))
                if body_valid:
                    if "smc_id_en" in bf:
                        smc_vld1.add(bf["smc_id_en"])
                if "smc_id_en" in bf:
                    smc_vals.add(bf["smc_id_en"])
                for a in ("ur_addr", "gr_base_addr", "BANK", "ADDR",
                          "ur_wr_line_cnt", "ur_rd_line_cnt"):
                    if a in bf:
                        addrs.setdefault(a, set()).add(bf[a])
            recs.append({
                "file": fn, "unit": unit, "lines": len(lines),
                "smc_id_en": sorted(smc_vals) if smc_vals else None,  # None=RTOVR 类无字段
                "smc_id_en_vld1": sorted(smc_vld1) if smc_vld1 else None,  # 决定性口径
                "vld_values": sorted(vlds), "loops_vld": sorted(loops),
                "addr_fields": {k: sorted(v) for k, v in addrs.items()},
            })
        stages.append({"stage": sname, "queues": recs})
    return {"case": os.path.basename(os.path.abspath(case_dir)), "stages": stages,
            "undecoded": undecoded}


def _fmt_scope(recs) -> str:
    """段使能范围：以 body VALID=1 行的 smc_id_en 为准（header vld=1+body VALID=0 的 JUMP 终止项不计）。"""
    vals = set()
    for r in recs:
        if r["smc_id_en_vld1"]:
            vals.update(r["smc_id_en_vld1"])
    if not vals:
        return "（本段 body VALID=1 行无 smc_id_en 字段）"
    if vals == {0}:
        return "body VALID=1 行 smc_id_en=0 → 仅 SMC0"
    if vals == {7}:
        return "body VALID=1 行 smc_id_en=7 → 阈值门控全开（SMC0..7，按 vld_eff=vld&&(en>=SMC_ID)）"
    return f"body VALID=1 行混合 {sorted(vals)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    rep = decode_case(args.case_dir)
    print(f"== decode_case_smc_enablement: {rep['case']} ==")
    for st in rep["stages"]:
        print(f"\n[{st['stage']}] 使能范围: {_fmt_scope(st['queues'])}")
        for r in st["queues"]:
            en = ("en(bodyV)=" + ",".join(str(v) for v in r["smc_id_en_vld1"])) \
                if r["smc_id_en_vld1"] is not None else "en(bodyV)=N/A"
            en_all = ("en(all)=" + ",".join(str(v) for v in r["smc_id_en"])) \
                if r["smc_id_en"] is not None else "en(all)=N/A"
            ad = " ".join(f"{k}={v}" for k, v in r["addr_fields"].items())
            print(f"  {r['file']:<22} {r['unit']:<8} lines={r['lines']:<4} "
                  f"{en:<13} {en_all:<13} vld={r['vld_values']} "
                  f"loops(vld)={r['loops_vld']} {ad}")
    if rep["undecoded"]:
        print("\n[undecoded]（正面枚举，未静默跳过）:")
        for u in rep["undecoded"]:
            print(f"  {u['stage']}/{u['file']}: {u['reason']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"\njson -> {args.json}")


if __name__ == "__main__":
    main()
