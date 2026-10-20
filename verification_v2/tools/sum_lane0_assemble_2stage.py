#!/usr/bin/env python3
# sum_lane0_assemble_2stage.py —— 契约 §3.7-5「两行连续无复位」正向检查的 VCS two-stage 用例拼装
#
# 背景（docs/softmax_v1_spec_design_20261019.md v4 实证）：ADR-056 展开分支 = 模调度帧重复
# 执行，单 schedule 两行共 FPADD_D 被帧内交错污染（sum_lane0_2row 实测 -65514.0，保留为
# expected-fail 检出证据）。正向两行检查按用户锁定的执行序移至 **VCS two-stage 组合**：
#   stage_0 = 已 VCS 通过的单行 L=16 程序（sum_lane0，行 0）
#   stage_1 = 同一参数化生成器的 row_base=1 程序（sum_lane0_r1，行 1，地址整体偏移一行）
# 冻结 TB 多段语义（已从源码正面取证）：单次复位在首段前、UR 仅起始装载一次、段间无复位
# （FPADD_D 等寄存器残留真实存在）、dump 在末段中断清除后。故两段地址不相交（行 0/1 各占
# 独立 x/seed/y 词）的两程序连续执行 = 契约要求的「第二行不继承第一行累计值」真实载体：
# 行 1 的首加必须经零种子路由（VIN1）而非依赖 FPADD_D 残留。
#
# 本脚本职责（一次性拼装，全量留痕）：
#   1. 拼合 preload：bank1 词 0..15 取行0 构建、词 16..31 取行1 构建、其余零；bank3 词 0/1
#      各取对应行种子（均 +0）；bank0/2/4..7 全零。
#   2. golden output_bank：bank1/3 = 拼合 preload（输入 bank 终态=预载，l16 已打包用例同款
#      惯例）；bank2 词 0/1 = 独立参考（sum_lane0_ref_check.serial_sum_bits，冻结串行 fp32
#      RNE，从 **拼合后 preload 字节** 计算）；其余全零。plain 32-hex 无 0x 前缀（打包惯例）。
#   3. stage_0/stage_1 iq_*.hex 原样拷贝（sha256 留证），生成器脚本会重建 iq_inst_*_subq0.txt。
#   4. 打印全文件 sha256 manifest（归档用）。
#
# 用法：
#   sum_lane0_assemble_2stage.py \
#     --row0-iq <sum_lane0 build smc_0 或已打包 case stage_0> --row0-ur <sum_lane0 build ur_data> \
#     --row1-iq <sum_lane0_r1 build smc_0>                 --row1-ur <sum_lane0_r1 build ur_data> \
#     --out <case dir>
# 退出码：0=拼装完成；1=任一前置检查失败；2=用法/文件缺失。

import argparse
import hashlib
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sum_lane0_ref_check import (UR_DEPTH, X_BANK, Z_BANK, Y_BANK,
                                 line_to_u32x4, read_hex_lines, serial_sum_bits,
                                 u32_to_f32)

L = 16  # 首验规格锁定 L=16（契约 §3.7-2）
IQ_FILES = ["iq_fpadd.hex", "iq_rtovr-5.hex", "iq_rtovr-6.hex", "iq_rtovr-14.hex",
            "iq_rtovr-15.hex", "iq_rtovr-16.hex", "iq_rtovr-35.hex",
            "iq_ur-r0.hex", "iq_ur-r1.hex", "iq_ur-r2.hex", "iq_ur-r3.hex",
            "iq_ur-w0.hex", "iq_vin0.hex", "iq_vin1.hex", "iq_vout0.hex"]
ZERO_LINE = "0" * 32


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def load_bank_lines(ur_dir, bank):
    p = os.path.join(ur_dir, f"bank{bank}.hex")
    if not os.path.exists(p):
        print(f"[FATAL] 缺 preload: {p}")
        sys.exit(2)
    lines = read_hex_lines(p)
    if len(lines) != UR_DEPTH:
        print(f"[FATAL] {p} 行数 {len(lines)} != {UR_DEPTH}")
        sys.exit(2)
    # 统一为无 0x 前缀纯 32-hex
    out = []
    for ln in lines:
        if ln.startswith(("0x", "0X")):
            ln = ln[2:]
        if len(ln) != 32:
            print(f"[FATAL] {p} 行宽异常: {ln!r}")
            sys.exit(2)
        out.append(ln.lower())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--row0-iq", required=True)
    ap.add_argument("--row0-ur", required=True)
    ap.add_argument("--row1-iq", required=True)
    ap.add_argument("--row1-ur", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    fails = []

    # ── 前置检查①：两段 iq 文件齐全、行0 段不含行1 地址（抽样审计在 ref_check --pre 完成）──
    for tag, d in (("row0", args.row0_iq), ("row1", args.row1_iq)):
        for f in IQ_FILES:
            if not os.path.exists(os.path.join(d, f)):
                fails.append(f"{tag} 缺 iq 文件: {os.path.join(d, f)}")

    # ── 拼合 preload ─────────────────────────────────────────────
    r0_b1 = load_bank_lines(args.row0_ur, X_BANK)
    r1_b1 = load_bank_lines(args.row1_ur, X_BANK)
    r0_b3 = load_bank_lines(args.row0_ur, Z_BANK)
    r1_b3 = load_bank_lines(args.row1_ur, Z_BANK)

    # 前置检查②：两半不相交（行0 构建的词 16..31 必须全零，行1 构建的词 0..15 必须全零），
    # 否则拼合会静默吞数据。
    for i in range(L, 2 * L):
        if r0_b1[i] != ZERO_LINE:
            fails.append(f"行0 bank1 词 {i} 非零（应留给行1）: {r0_b1[i]}")
    for i in range(0, L):
        if r1_b1[i] != ZERO_LINE:
            fails.append(f"行1 bank1 词 {i} 非零（应留给行0）: {r1_b1[i]}")
    if r1_b3[0] != ZERO_LINE:
        fails.append(f"行1 bank3 词 0 非零（应留给行0 种子）: {r1_b3[0]}")

    if fails:
        print("\n".join("[FAIL] " + f for f in fails))
        return 1

    merged_b1 = r0_b1[:L] + r1_b1[L:2 * L] + [ZERO_LINE] * (UR_DEPTH - 2 * L)
    merged_b3 = [r0_b3[0], r1_b3[1]] + [ZERO_LINE] * (UR_DEPTH - 2)

    # ── 独立参考（冻结串行 fp32 RNE，从拼合后字节计算）────────────
    ref = {}
    for R in (0, 1):
        xs = [u32_to_f32(line_to_u32x4(merged_b1[R * L + i])[3]) for i in range(L)]
        # lane1-3 布局审计（与 ref_check --pre 同口径）
        for i in range(L):
            w = line_to_u32x4(merged_b1[R * L + i])
            if w[0] != 0 or w[1] != 0 or w[2] != 0:
                fails.append(f"x{R}_{i} lane1-3 非 +0: {['%08x' % u for u in w]}")
        bits = serial_sum_bits(xs)
        ref[R] = bits
        print(f"[row {R}] 参考和 s_{L} = 0x{bits:08x} ({u32_to_f32(bits)!r})")
    if fails:
        print("\n".join("[FAIL] " + f for f in fails))
        return 1

    # ── golden：输入 bank = 拼合 preload；bank2 词 R = [0,0,0,ref_R]；其余全零 ──
    golden = {b: [ZERO_LINE] * UR_DEPTH for b in range(8)}
    golden[X_BANK] = merged_b1
    golden[Z_BANK] = merged_b3
    for R in (0, 1):
        golden[Y_BANK][R] = "0" * 24 + f"{ref[R]:08x}"

    # ── 写盘 ─────────────────────────────────────────────────────
    ur_out = os.path.join(args.out, "smc_0", "ur_data")
    ob_out = os.path.join(args.out, "smc_0", "output_bank")
    s0_out = os.path.join(args.out, "smi_iq_inst", "stage_0")
    s1_out = os.path.join(args.out, "smi_iq_inst", "stage_1")
    for d in (ur_out, ob_out, s0_out, s1_out):
        os.makedirs(d, exist_ok=True)

    merged_ur = {X_BANK: merged_b1, Z_BANK: merged_b3}
    for b in range(8):
        lines = merged_ur.get(b, [ZERO_LINE] * UR_DEPTH)
        with open(os.path.join(ur_out, f"bank{b}.hex"), "w") as f:
            f.write("\n".join(lines) + "\n")
        with open(os.path.join(ob_out, f"bank{b}_final.hex"), "w") as f:
            f.write("\n".join(golden[b]) + "\n")

    for src_dir, dst_dir in ((args.row0_iq, s0_out), (args.row1_iq, s1_out)):
        for f in IQ_FILES:
            shutil.copyfile(os.path.join(src_dir, f), os.path.join(dst_dir, f))

    # ── manifest（归档用）─────────────────────────────────────────
    print("\n[MANIFEST sha256]")
    for root, _, files in os.walk(args.out):
        for f in sorted(files):
            p = os.path.join(root, f)
            print(f"  {sha256(p)}  {os.path.relpath(p, args.out)}")
    print(f"\n[OK] two-stage 用例已拼装: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
