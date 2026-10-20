#!/usr/bin/env python3
# sum_lane0_ref_check.py —— SUM 首证（受限跨迭代 fp32 SUM，lane0-only）独立参考检查
#
# 依据 docs/softmax_v1_spec_design_20261019.md §3.7 契约（2026-10-19 用户锁定）：
#   - 数值参考按冻结的串行 fp32 顺序独立计算：s₀=+0、s_{i+1}=fp32(s_i+x_i)（RNE），
#     输入取自 **preload hex 字节**（非算子 prepare_input 数组——独立性所在）；
#   - 全词 bit-exact 比对（lane1-3 恒 +0 的布局承诺由全词比对覆盖，契约 §3.7-2）；
#   - 负控语义：本脚本只报告一致/不一致；负控用例的「通过」= 本脚本检出 mismatch。
#
# 用法：
#   sum_lane0_ref_check.py --pre  --ur <ur_data_dir> [--L 16] [--rows 1]
#   sum_lane0_ref_check.py --post --ur <ur_data_dir> --out <output_bank_dir> [--L 16] [--rows 1]
#     --pre : 布局审计（x 词 lane1-3 全 +0；零种子词值报告）+ 打印逐行参考和（比特型）。
#     --post: 先行 --pre 全量，再比对 output bank2 行 r 全词 == [s_r,0,0,0] 比特型。
# 退出码：0=全过；1=任一不一致；2=用法/文件缺失。
#
# hex 口径（fair_smul64w_ref_check.py 同款实测约定）：每行 16B，4×u32 大端文本序，
# 元素 0（lane0）= 最右 8 个 hex 字符（u32 LSB）。

import argparse
import os
import struct
import sys

import numpy as np

X_BANK, Z_BANK, Y_BANK = 1, 3, 2
UR_DEPTH = 256


def read_hex_lines(path):
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip()]


def line_to_u32x4(line):
    if line.startswith(("0x", "0X")):
        line = line[2:]
    b = bytes.fromhex(line)
    if len(b) != 16:
        raise ValueError(f"行宽 != 16B: {line!r}")
    return struct.unpack(">4I", b)  # [3] = lane0（LSB u32）


def u32_to_f32(u):
    return struct.unpack("<f", struct.pack("<I", u))[0]


def serial_sum_bits(xs):
    """冻结语义：s₀=+0，s_{i+1}=fp32(s_i+x_i)（RNE），返回末态 u32 比特型。"""
    s = np.float32(0.0)
    for v in xs:
        s = np.float32(s + np.float32(v))
    return struct.unpack("<I", struct.pack("<f", s))[0]


def run(case_ur, out_dir, L, rows, do_post):
    fails = []
    for b, tag in ((X_BANK, "x"), (Z_BANK, "zero")):
        p = os.path.join(case_ur, f"bank{b}.hex")
        if not os.path.exists(p):
            print(f"[FATAL] 缺 preload 文件: {p}")
            return 2
    x_lines = read_hex_lines(os.path.join(case_ur, f"bank{X_BANK}.hex"))
    z_lines = read_hex_lines(os.path.join(case_ur, f"bank{Z_BANK}.hex"))
    if len(x_lines) != UR_DEPTH or len(z_lines) != UR_DEPTH:
        print(f"[FATAL] preload 行数不为 {UR_DEPTH}")
        return 2

    # 布局审计 + 逐行参考
    ref_bits = []
    for r in range(rows):
        seed = line_to_u32x4(z_lines[r])
        seed_f = u32_to_f32(seed[3])
        print(f"[row {r}] 零种子词 lanes={['%08x' % u for u in seed]} "
              f"(lane0={seed_f!r})")
        xs = []
        for i in range(L):
            w = line_to_u32x4(x_lines[r * L + i])
            if w[0] != 0 or w[1] != 0 or w[2] != 0:
                fails.append(f"x{r}_{i} lane1-3 非 +0: {['%08x' % u for u in w]}")
            xs.append(u32_to_f32(w[3]))
        bits = serial_sum_bits(xs)
        ref_bits.append(bits)
        print(f"[row {r}] 参考和 s_{L} = 0x{bits:08x} ({u32_to_f32(bits)!r})")

    if not do_post:
        if fails:
            print("\n".join("[FAIL] " + f for f in fails))
            return 1
        print("[PRE] 布局审计通过（x 词 lane1-3 全 +0）")
        return 0

    y_lines = read_hex_lines(os.path.join(out_dir, f"bank{Y_BANK}_final.hex"))
    if len(y_lines) != UR_DEPTH:
        print(f"[FATAL] output bank{Y_BANK} 行数 {len(y_lines)} != {UR_DEPTH}")
        return 2
    for r in range(rows):
        got = line_to_u32x4(y_lines[r])
        want = (0, 0, 0, ref_bits[r])
        if got != want:
            fails.append(
                f"row {r} 输出词不一致: got={['%08x' % u for u in got]} "
                f"want={['%08x' % u for u in want]}"
                f"（lane0 got={u32_to_f32(got[3])!r} want={u32_to_f32(want[3])!r}）")

    # 非目标写检查①：bank2 零尾（第 rows 词起应全 +0——preload 即全零，
    # 任何非目标写都会留下痕迹）。
    for a in range(rows, UR_DEPTH):
        w = line_to_u32x4(y_lines[a])
        if w != (0, 0, 0, 0):
            fails.append(f"bank2 非目标词 {a} 被写: {['%08x' % u for u in w]}")

    # 非目标写检查②：输入/常量 bank 运行后应与 preload 逐行一致
    # （回写污染 x/零种子 = 归约语义被静默破坏的通道之一）。
    for b, pre in ((X_BANK, x_lines), (Z_BANK, z_lines)):
        post_p = os.path.join(out_dir, f"bank{b}_final.hex")
        if not os.path.exists(post_p):
            print(f"[WARN] 缺 {post_p}，跳过 bank{b} 完整性比对")
            continue
        post = read_hex_lines(post_p)
        if len(post) != UR_DEPTH:
            fails.append(f"bank{b}_final 行数 {len(post)} != {UR_DEPTH}")
            continue
        for a in range(UR_DEPTH):
            if line_to_u32x4(post[a]) != line_to_u32x4(pre[a]):
                fails.append(f"bank{b} 词 {a} 运行后被改写: "
                             f"pre={pre[a]} post={post[a]}")

    if fails:
        print("\n".join("[FAIL] " + f for f in fails))
        return 1
    print(f"[POST] {rows} 行输出全词 bit-exact == 独立串行 fp32 参考（RNE）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--ur", required=True, help="ur_data 目录（preload bank*.hex）")
    ap.add_argument("--out", default=None, help="output_bank 目录（--post 必填）")
    ap.add_argument("--L", type=int, default=16)
    ap.add_argument("--rows", type=int, default=1)
    args = ap.parse_args()
    if args.pre == args.post:
        print("用法：--pre 与 --post 恰取其一")
        return 2
    if args.post and not args.out:
        print("--post 需要 --out")
        return 2
    return run(args.ur, args.out, args.L, args.rows, args.post)


if __name__ == "__main__":
    sys.exit(main())
