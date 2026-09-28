"""IQ 指令流字段解码器（阶段2-M1 调试用，编译器 vs golden 对照）。

位布局权威来源（三源一致，禁手抄）：
- `~/Jianyuan/aiacc/SMI_IQ/op/mul_scalar/mul_scalar_op.cpp::serialize_header_bits`
- `~/u280_sim_v422/smi/smi_iq.v:85` 注释
- `op/bitwise_and/bitwise_and_op.cpp:164-169` BITS/MASK 常量

instr（右对齐，code 在低位）= (header << code_bits) | code
header 41b，LSB→MSB: INC_EMBED[5:0] INC[9:6] DLY[12:10] COND[19:13]
  LOOPS[29:20] EMBED_END[35:30] EMBED[38:36] JUMP[39] VLD[40]
code_bits = IQ_BITS - 41；IQ_BITS 从同目录 iq_inst_*_subq0.txt 头行
`IQ_ID=... IQ_BITS=...` 读取（即 generator 的 IQ_LAYOUT_MAP 实际值）。

协议语义（mul_scalar_op.cpp）：
- NOP wait 行: vld=0, embed=7, loops=chunk-1（append_nop_wait）
- op 行: vld=1, jump=0, embed=0
- 终止行: vld=1, jump=1（append_end，EMBED_END 结束后跳转）

用法：
    python3 verification_v2/tools/decode_iq_stream.py <iq_inst 目录> [iq_inst 目录2 ...]
对目录下每个 iq_inst_*_subq*.txt：取 IQ_ID/IQ_BITS，找同名 raw iq_*.hex，
逐行解码打印字段表。
"""
import re
import sys
from pathlib import Path

FIELDS = [
    ("inc_embed", 0, 0x3F),
    ("inc", 6, 0xF),
    ("dly", 10, 0x7),
    ("cond", 13, 0x7F),
    ("loops", 20, 0x3FF),
    ("embed_end", 30, 0x3F),
    ("embed", 36, 0x7),
    ("jump", 39, 0x1),
    ("vld", 40, 0x1),
]

# raw hex 文件名 → iq_inst 文件名的对应规则（文件名两侧都试过）
def find_raw(stage_dir: Path, inst_name: str) -> Path | None:
    # iq_inst_fpmul_subq0.txt → iq_fpmul.hex ; iq_inst_68_vin0_subq0.txt → iq_68_vin0.hex
    m = re.match(r"iq_inst_(.+)_subq\d+\.txt$", inst_name)
    if not m:
        return None
    stem = m.group(1)
    stem_dash = stem.replace("_", "-") if stem[0].isalpha() else stem
    stem_nonum = re.sub(r"^\d+_", "", stem)
    cands = [
        stage_dir / f"iq_{stem}.hex",
        # ur_r0 → ur-r0；rtovr_14 → rtovr-14；68_vin0 → vin0
        stage_dir / f"iq_{stem_dash}.hex",
        stage_dir / f"iq_{stem_nonum}.hex",
    ]
    for c in cands:
        if c.is_file():
            return c
    # 兜底：glob
    hits = sorted(stage_dir.glob(f"iq_*{stem.split('_')[-1]}*.hex"))
    return hits[0] if hits else None


def decode_dir(stage_dir: Path) -> None:
    print(f"\n===== {stage_dir} =====")
    for inst in sorted(stage_dir.glob("iq_inst_*_subq*.txt")):
        first = inst.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        mi = re.search(r"\bIQ_ID=(\d+)\b", first)
        mb = re.search(r"\bIQ_BITS=(\d+)\b", first)
        if not (mi and mb):
            print(f"{inst.name}: 无法解析 IQ_ID/IQ_BITS，跳过")
            continue
        iq_id, iq_bits = int(mi.group(1)), int(mb.group(1))
        code_bits = iq_bits - 41
        raw = find_raw(inst.parent, inst.name)
        print(f"\n--- {inst.name}  (IQ_ID={iq_id} IQ_BITS={iq_bits} code_bits={code_bits})"
              f"  raw={'缺' if raw is None else raw.name}")
        if raw is None:
            continue
        for ln, line in enumerate(raw.read_text().splitlines()):
            line = line.strip()
            if not line:
                continue
            v = int(line, 16) & ((1 << iq_bits) - 1)
            code = v & ((1 << code_bits) - 1) if code_bits > 0 else 0
            h = v >> code_bits
            fs = {name: (h >> shift) & mask for name, shift, mask in FIELDS}
            fstr = " ".join(f"{k}={x}" for k, x in fs.items())
            print(f"  [{ln}] code=0x{code:0{max(1,(code_bits+3)//4)}x}  {fstr}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for d in sys.argv[1:]:
        decode_dir(Path(d))


if __name__ == "__main__":
    main()
