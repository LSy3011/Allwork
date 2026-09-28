#!/usr/bin/env python3
"""
iq_field_diff —— IQ 固件 hex 的**字段级**比对工具（ADR-052 D5 的验收工具）。

为什么需要它（而不是直接 diff 文本）：
    编译器 emitter.py 用 `hex_chars=(total_bits+3)//4`（半字节对齐），而权威/手写线用
    `(header+code+7)//8`（字节对齐）。对 LDB(106b)/LDBAXI(154b)/STB(100b) 差 1 个字符，
    对 STBAXI(152b) 恰好相等。**数值等价，纯 padding 差异**。文本 diff 会把等价物报成差异，
    字段级比对则直接告诉你"哪个字段不同、期望多少、实得多少"。

权威来源（本工具不硬编码，全部现读）：
    - body 字段布局：`hw_config/modules.yaml` 的 `code_bits` / `code_fields`（声明序 == 物理位序）
    - header 位布局：`src/driver/packer.py` 的 `ISA_V5_HEADER_LSU`(50b) / `ISA_V5_HEADER`(41b)
    - 位序方向：`lsu_emit.cpp:serialize_code_words_lsb_first` —— hex 串最左 = MSB，
      即 `int(line, 16)` 后 `bit0` 是 vld。

用法：
    # 打印一份固件的字段级解码
    python iq_field_diff.py build/.../smc_0/iq_stb.hex --unit STB --dump

    # 两份固件字段级比对（编译器产物 vs 手写 golden）
    python iq_field_diff.py <mine.hex> <golden.hex> --unit STB

    # 比对整个目录下的 iq_*.hex（按 basename 配对）
    python iq_field_diff.py <mine_dir> <golden_dir> --pair
"""
from __future__ import annotations

import argparse
import os
import re
import sys

try:
    import yaml
except ImportError:
    sys.exit("需要 PyYAML：pip install PyYAML")

# ── header 位布局（与 src/driver/packer.py 一致；LSU 与 compute 的唯一差异是 INC 4b→13b）──
HDR_COMPUTE = {
    "INC_EMBED": (0, 6), "INC": (6, 4), "DLY": (10, 3), "COND": (13, 7),
    "LOOPS": (20, 10), "EMBED_END": (30, 6), "EMBED_LOOPS": (36, 3),
    "JUMP": (39, 1), "VLD": (40, 1),
}
HDR_LSU = {
    "INC_EMBED": (0, 6), "INC": (6, 13), "DLY": (19, 3), "COND": (22, 7),
    "LOOPS": (29, 10), "EMBED_END": (39, 6), "EMBED_LOOPS": (45, 3),
    "JUMP": (48, 1), "VLD": (49, 1),
}
LSU_UNITS = {"LDB", "LDB_AXI", "STB", "STB_AXI"}


def norm_unit(u: str) -> str:
    return u.upper().replace("-", "_")


def load_modules_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    # modules.yaml 顶层可能是 {modules: {...}} 或直接 {...}
    if isinstance(doc, dict) and "modules" in doc and isinstance(doc["modules"], dict):
        return {k.upper(): v for k, v in doc["modules"].items()}
    return {k.upper(): v for k, v in doc.items()}


def body_layout(modules: dict, unit: str, variant: str | None = None):
    u = norm_unit(unit)
    if u not in modules:
        raise KeyError(f"modules.yaml 中无单元 {u}（已知：{sorted(modules)}）")
    m = modules[u]
    grp = (m.get("micro_groups") or {}).get(variant) if variant else None
    src = grp if grp else m
    bits = src["code_bits"]
    fields = [(f["name"].upper(), int(f["bits"])) for f in src["code_fields"]]
    declared = sum(w for _, w in fields)
    if declared != bits:
        print(f"  ⚠ {u}: code_fields 位宽和 {declared} != code_bits {bits}", file=sys.stderr)
    return bits, fields


def decode(line: str, unit: str, modules: dict, variant: str | None = None):
    u = norm_unit(unit)
    cbits, fields = body_layout(modules, u, variant)
    hdr_map = HDR_LSU if u in LSU_UNITS else HDR_COMPUTE
    hdr_w = sum(w for _, w in hdr_map.values())

    val = int(line.strip(), 16)
    body = val & ((1 << cbits) - 1)
    hdr = val >> cbits

    h = {k: (hdr >> s) & ((1 << w) - 1) for k, (s, w) in hdr_map.items()}

    b, pos = {}, 0
    for name, w in fields:
        b[name] = (body >> pos) & ((1 << w) - 1)
        pos += w
    return {"total_bits": hdr_w + cbits, "header": h, "body": b, "raw": val}


def decode_raw(line, cbits, hdr_map, fields):
    """不依赖 modules.yaml 的裸解码（供反编码自检）。"""
    hdr_w = sum(w for _, w in hdr_map.values())
    val = int(line.strip(), 16)
    body = val & ((1 << cbits) - 1)
    hdr = val >> cbits
    h = {k: (hdr >> s) & ((1 << w) - 1) for k, (s, w) in hdr_map.items()}
    b, pos = {}, 0
    for name, w in fields:
        b[name] = (body >> pos) & ((1 << w) - 1); pos += w
    return {"total_bits": hdr_w + cbits, "header": h, "body": b, "raw": val}


def fmt(d, only_nonzero=True):
    h = d["header"]
    b = d["body"]
    hs = " ".join(f"{k}={v}" for k, v in h.items() if v or not only_nonzero)
    bs = " ".join(f"{k}={v}" for k, v in b.items() if v or not only_nonzero)
    return f"[{d['total_bits']}b] HDR{{ {hs} }}  BODY{{ {bs} }}"


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


def compare(a_path, b_path, unit, modules, variant=None):
    a = read_lines(a_path)
    b = read_lines(b_path)
    ok = True
    if len(a) != len(b):
        print(f"  ✗ 行数不同：{os.path.basename(a_path)}={len(a)} vs {os.path.basename(b_path)}={len(b)}")
        ok = False
    for i in range(min(len(a), len(b))):
        da = decode(a[i], unit, modules, variant)
        db = decode(b[i], unit, modules, variant)
        if da["raw"] == db["raw"]:
            continue
        ok = False
        print(f"  ✗ row{i+1} 值不同：0x{da['raw']:x} vs 0x{db['raw']:x}")
        for sec in ("header", "body"):
            for k in da[sec]:
                if da[sec][k] != db[sec][k]:
                    print(f"      {sec}.{k:<16} 我={da[sec][k]:<12} 参考={db[sec][k]}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="IQ 固件字段级比对（ADR-052 D5）")
    ap.add_argument("mine", help="编译器产物（.hex 或目录）")
    ap.add_argument("golden", nargs="?", help="参考产物（.hex 或目录）")
    ap.add_argument("--unit", required=False, help="单元类型，如 STB / STB_AXI / LDB / LDB_AXI")
    ap.add_argument("--variant", default=None, help="micro_variant（如 load_s0123）")
    ap.add_argument("--modules", default=None, help="modules.yaml 路径")
    ap.add_argument("--dump", action="store_true", help="只解码打印，不比对")
    ap.add_argument("--all-fields", action="store_true", help="连 0 值字段也打印")
    ap.add_argument("--pair", action="store_true", help="目录模式：按 basename 配对")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    mod_path = args.modules or os.path.join(root, "hw_config", "modules.yaml")
    modules = load_modules_yaml(mod_path)

    if args.dump:
        unit = args.unit or os.path.basename(args.mine).replace("iq_", "").replace(".hex", "")
        for i, ln in enumerate(read_lines(args.mine), 1):
            print(f"row{i:<3} len={len(ln):<3} {fmt(decode(ln, unit, modules, args.variant), not args.all_fields)}")
        return 0

    if args.pair:
        names = sorted(n for n in os.listdir(args.mine) if n.startswith("iq_") and n.endswith(".hex"))
        if not names:
            sys.exit(f"{args.mine} 下无 iq_*.hex")
        allok = True
        for n in names:
            g = os.path.join(args.golden, n)
            if not os.path.exists(g):
                print(f"[{n}] 参考缺失，跳过（参考侧候选：{os.listdir(args.golden)}）")
                allok = False
                continue
            u = infer_unit(n, modules)
            print(f"[{n}] unit={u}")
            r = compare(os.path.join(args.mine, n), g, u, modules, args.variant)
            print(f"  → {'一致' if r else '不一致'}")
            allok &= r
        return 0 if allok else 1

    if not args.golden:
        sys.exit("需要 golden（或加 --dump）")
    unit = args.unit or infer_unit(args.mine, modules)
    print(f"unit={unit}  modules={mod_path}")
    r = compare(args.mine, args.golden, unit, modules, args.variant)
    print("一致" if r else "不一致")
    return 0 if r else 1


def infer_unit(filename: str, modules: dict) -> str:
    """从 iq_<name>.hex 推断单元名；'-'→'_' 归一化（同 TB 的 _normalize_name_token）。"""
    base = os.path.basename(filename)
    m = re.match(r"^iq_(.+)\.hex$", base)
    tok = (m.group(1) if m else base).replace("-", "_").upper()
    if tok in modules:
        return tok
    # 常见别名
    alias = {"LDB_AXI": "LDB_AXI", "STB_AXI": "STB_AXI"}
    tok2 = alias.get(tok, tok)
    if tok2 in modules:
        return tok2
    raise KeyError(f"无法从 {base} 推断单元（token={tok}，已知={sorted(modules)}）")


if __name__ == "__main__":
    sys.exit(main())
