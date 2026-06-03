#!/usr/bin/env python3
"""从 AIACC 指令集架构 .docx 提取「上行微指令表」并 diff 当前 hw_config spec。

用途：硬件设计文档（权威）升版后，复跑此脚本对齐 master/modules yaml。
零依赖（stdlib zipfile + ElementTree）。

  python3 verification_v2/tools/spec_doc_align.py AIACC_指令集架构_v0.4.19.docx [--tables]

输出：
  - 各单元 doc 字段（名称+bit数）
  - 与 hw_config/modules.yaml + master functional_units 的 diff
    （缺字段 / 多字段 / 位宽不符 / 单元缺失 / code_bits 不符）
"""
import sys, zipfile, re
import xml.etree.ElementTree as ET
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ROOT = Path(__file__).resolve().parents[2]


def extract_uplink_tables(docx_path):
    """返回 {unit_name: [(field, bits_str), ...]}，仅「XX相关上行微指令表」。"""
    with zipfile.ZipFile(docx_path) as z:
        root = ET.fromstring(z.read("word/document.xml").decode("utf-8", "ignore"))
    body = root.find(f"{W}body")

    def ptext(p):
        return "".join(t.text or "" for t in p.iter(f"{W}t")).strip()

    def ctext(tc):
        return "".join(t.text or "" for t in tc.iter(f"{W}t")).strip()

    seq = []
    for ch in body:
        if ch.tag == f"{W}p":
            t = ptext(ch)
            if t:
                seq.append(("p", t))
        elif ch.tag == f"{W}tbl":
            rows = [[ctext(tc) for tc in tr.findall(f"{W}tr".replace("tr", "tc"))]
                    for tr in ch.findall(f"{W}tr")]
            rows = [[ctext(tc) for tc in tr.findall(f"{W}tc")] for tr in ch.findall(f"{W}tr")]
            seq.append(("t", rows))

    out = {}
    for i, (k, v) in enumerate(seq):
        if k != "t":
            continue
        title = ""
        for j in range(i - 1, -1, -1):
            if seq[j][0] == "p":
                title = seq[j][1]
                break
        m = re.search(r"([A-Za-z0-9]+)相关上行微指令", title)
        if not m:
            continue
        unit = m.group(1).upper()
        fields = []
        for r in v[1:]:  # skip header row
            if len(r) >= 2 and r[0].strip() and r[0].strip() != "……":
                fields.append((r[0].strip(), r[1].strip()))
        out[unit] = (title, fields)
    return out


def load_spec():
    import yaml
    mod = yaml.safe_load(open(ROOT / "hw_config/modules.yaml"))["modules"]
    mas = yaml.safe_load(open(ROOT / "hw_config/specs/aiacc_v2.4_master.yaml")).get("functional_units", {})
    return mod, mas


def norm(s):
    return re.sub(r"[_\s]", "", s.lower())


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    docx = sys.argv[1]
    show_tables = "--tables" in sys.argv
    doc = extract_uplink_tables(docx)
    mod, mas = load_spec()

    print(f"# doc 上行微指令表单元数: {len(doc)}\n")

    if show_tables:
        for u, (title, fs) in doc.items():
            print(f"=== {title} ===")
            for n, b in fs:
                print(f"   {n:24s} {b}")
            print()
        return

    # diff：以 doc 为权威，对每个 doc 单元找 modules.yaml 对应（按名规整匹配）
    print("## diff（doc 权威 vs 当前 spec）\n")
    mod_keys = {norm(k): k for k in mod}
    for u, (title, fs) in sorted(doc.items()):
        nu = norm(u)
        # 别名：FP32ADDTREE→FP32ADDTREE8TO1, LOGICAL→LOGIC, UR→UR_READ/UR_WRITE 等
        cand = mod_keys.get(nu)
        doc_smcen = [b for n, b in fs if "smc_id_en" in n.lower()]
        tag = ""
        if cand is None:
            tag = f"⚠ 当前 modules.yaml 无对应单元（doc 有 smc_id_en={bool(doc_smcen)}）"
            print(f"  {u:18s} {tag}")
            continue
        mfields = mod[cand].get("code_fields", [])
        mnames = [f.get("name") for f in mfields]
        has_smcen = any(n.lower() in ("smc_id_en",) for n in mnames)
        has_smcid = any(n.lower() == "smc_id" for n in mnames)
        in_master = cand.upper() in {k.upper() for k in mas}
        mas_fields = []
        if in_master:
            mi = mas[[k for k in mas if k.upper() == cand.upper()][0]].get("micro_instructions", [])
            mas_fields = [(f.get("field") or f.get("name")) for f in mi]
        mas_smcen = any(str(n).lower() == "smc_id_en" for n in mas_fields)
        # 判定
        notes = []
        if doc_smcen and not mas_smcen:
            if has_smcid:
                notes.append("master 用 SMC_ID 异名 → 应改 smc_id_en")
            elif has_smcen:
                notes.append("master 缺 smc_id_en（modules.yaml 有）→ 补 master")
            else:
                notes.append("master+modules 都缺 smc_id_en → 补")
        # 字段数对比
        doc_n = len(fs)
        notes.append(f"doc {doc_n}字段 / modules {len(mnames)}字段")
        print(f"  {u:18s} → {cand:16s} smcen(doc={bool(doc_smcen)},mod={has_smcen},mas={mas_smcen}) | {'; '.join(notes)}")

    print("\n## 当前 spec 有、doc 无 的单元（可能已废弃）")
    doc_norm = {norm(u) for u in doc}
    for k in mod:
        if norm(k) not in doc_norm and k.upper() not in ("RTOVR",):
            print(f"  {k}")


if __name__ == "__main__":
    main()
