#!/usr/bin/env python3
"""把编译产物里的 golden（独立 numpy oracle）导成 VCS testbench 可读的 per-SMC 期望文件。

用法：
    python3 verification_v2/tools/export_golden_hex.py build/verification_v2/<op>

产物（写到 <op>/vcs_expected/）：
    smc_{k}_bank{B}_expected.hex   —— 每行一个 128-bit 词（32 hex 字符，无 0x，$readmemh 友好），
                                       4 个 fp32 lane：lane0=低 32 位，每 lane big-endian fp32。
                                       与仿真器 bank dump 逐位同款（已对 _read_sim_output 验证）。
    smc_{k}_bank{B}_expected.txt   —— 同序十进制 fp32（每行 4 个），便于人读/灵活比对。

⚠ 基准取 golden_data.npz（独立 numpy oracle），**非**仿真器自己的 output_bank（那是 model-A 输出）。
VCS 跑完拿各 SMC 的 bank{B} 输出对这些期望文件即可。
"""
import sys
import json
import struct
from pathlib import Path

import numpy as np


def f32_be_bits(v: float) -> int:
    """fp32 → 32-bit 整数（big-endian 字节序，与 _read_sim_output 的 struct.unpack('>I',...) 同款）。"""
    return struct.unpack(">I", struct.pack(">f", float(v)))[0]


def pack_words(vals) -> list:
    """一维 fp32 序列 → 128-bit 词列表（每词 4 lane，lane i 在 bit i*32）。"""
    words = []
    n = len(vals)
    for w0 in range(0, n, 4):
        word = 0
        for i in range(4):
            idx = w0 + i
            if idx < n:
                word |= (f32_be_bits(vals[idx]) & 0xFFFFFFFF) << (i * 32)
        words.append(word)
    return words


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    op_dir = Path(sys.argv[1])
    gold = np.load(op_dir / "golden_data.npz")["y"]
    cfg = json.loads((op_dir / "sim_config.json").read_text())
    smc_num = int(cfg.get("smc_num") or 1)
    out_tensors = [t for t in cfg.get("tensors", []) if t.get("role") == "output"]
    bank = int(out_tensors[0]["bank"]) if out_tensors else 5

    # golden 形状归一：1D(单 SMC) → (1, n)；2D(smc_num, n) 原样
    if gold.ndim == 1:
        gold2d = gold.reshape(1, -1)
    else:
        gold2d = gold
    n_smc = gold2d.shape[0]

    out_dir = op_dir / "vcs_expected"
    out_dir.mkdir(exist_ok=True)
    for k in range(n_smc):
        vals = gold2d[k].astype(np.float32).reshape(-1)
        words = pack_words(vals)
        hex_path = out_dir / f"smc_{k}_bank{bank}_expected.hex"
        txt_path = out_dir / f"smc_{k}_bank{bank}_expected.txt"
        with open(hex_path, "w") as fh:
            for w in words:
                fh.write(f"{w:032x}\n")
        with open(txt_path, "w") as ft:
            for i in range(0, len(vals), 4):
                ft.write(" ".join(f"{float(x):.7g}" for x in vals[i:i + 4]) + "\n")
    print(f"[export] op={op_dir.name} smc_num(golden)={n_smc} bank={bank} "
          f"n_per_smc={gold2d.shape[1]} → {out_dir}/ ({2 * n_smc} files)")
    print(f"  例: {out_dir}/smc_0_bank{bank}_expected.hex (+ .txt)")


if __name__ == "__main__":
    main()
