#!/usr/bin/env python3
# fair_smul64w_ref_check.py —— 公平对照组合程序（scalar_mul 64w×8SMC）独立参考检查
#
# 依据 docs/fair_pair_scalar_mul_64w_design_20261018.md §5：
#   - 独立参考：从 hbm_data 字节独立计算 fp32(x·α)，不依赖任一侧 golden 生成路径；
#   - UR 零尾兼作非目标写检查（golden bank2 = 256 行 = 64 有效 + 192 零行）；
#   - HBM 窗外零写检查（post 模式：TB dump vs staged 镜像逐字节，窗内除外）。
#
# 布局（生成器 meta_v3.json 实测登记）：
#   每通道（memory_init<K>.hex，16B/行）：x @行0..63（byte_base 0）、
#   α @行256..319（byte_base 4096，4 lane 同值）、y 输出窗 @行512..575（byte_base 8192）。
#
# 用法：
#   fair_smul64w_ref_check.py --pre  <case_dir>   # golden 一致性：IEEE(x·α)==golden bank2（8/8）+零尾
#   fair_smul64w_ref_check.py --post <case_dir>   # 运行后：hbm_result 窗内==IEEE 参考、窗外==staged 镜像
# 退出码：0=全过；1=任一失败；2=用法/文件缺失。

import argparse
import struct
import sys
import os

WORDS = 64          # x/y 各 64 词
ALPHA_LINE0 = 256   # byte_base 4096 / 16
OUT_LINE0 = 512     # byte_base 8192 / 16
UR_DEPTH = 256      # golden bank2 总行数（64 有效 + 零尾）


def read_hex_lines(path):
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip()]


def line_to_f32x4(line):
    b = bytes.fromhex(line)
    assert len(b) == 16, f"行宽 != 16B: {line!r}"
    # 实测（2026-10-18，fair_smul64w pre 校验）：write_hex_file 按 u32 值 MSB
    # 优先打印，词内 4 个 fp32 为大端文本序（"40200000"=2.5 直读）
    return struct.unpack(">4f", b)


def f32x4_to_line(vals):
    return struct.pack(">4f", *vals).hex()


def ieee_ref_line(x_line, alpha):
    return f32x4_to_line(tuple(
        struct.unpack("<f", struct.pack("<f", x * alpha))[0] for x in x_line))


def check_pre(case):
    """golden bank2 == IEEE(x·α)，8/8 通道；零尾 192 行全零。"""
    fails = []
    for k in range(1, 9):
        hbm = read_hex_lines(os.path.join(case, "hbm_data", f"memory_init{k}.hex"))
        golden = read_hex_lines(os.path.join(
            case, f"smc_{k-1}", "output_bank", "bank2_final.hex"))
        if len(golden) != UR_DEPTH:
            fails.append(f"smc_{k-1} golden 行数 {len(golden)} != {UR_DEPTH}")
            continue
        alpha = line_to_f32x4(hbm[ALPHA_LINE0])[0]
        lanes = line_to_f32x4(hbm[ALPHA_LINE0])
        if any(v != alpha for v in lanes):
            fails.append(f"smc_{k-1} α 四 lane 不同值: {lanes}")
        for i in range(WORDS):
            ref = ieee_ref_line(line_to_f32x4(hbm[i]), alpha)
            if golden[i] != ref:
                fails.append(f"smc_{k-1} bank2 行{i}: golden={golden[i]} ref={ref}")
                break
        for i in range(WORDS, UR_DEPTH):
            if golden[i] != "0" * 32:
                fails.append(f"smc_{k-1} bank2 零尾行{i} 非零: {golden[i]}")
                break
    return fails


def check_post(case):
    """运行后：hbm_result 输出窗 == IEEE 参考；窗外 == hbm_data staged 镜像。"""
    fails = []
    for k in range(1, 9):
        hbm = read_hex_lines(os.path.join(case, "hbm_data", f"memory_init{k}.hex"))
        res_path = os.path.join(case, f"smc_{k-1}", f"hbm_result_smc_{k-1}.hex")
        if not os.path.isfile(res_path):
            fails.append(f"smc_{k-1} 缺 hbm_result（未运行？）")
            continue
        res = read_hex_lines(res_path)
        # dump = 全通道 1M 行；staged 镜像只定义 [0, OUT_LINE0+WORDS)。
        # 非目标写检查 = [0,512)==staged 前段 且 [576,末) 全零（TB 对未初始化
        # 区零填充，实证 2026-10-18：手写变体窗外零非零行）。
        if len(res) < OUT_LINE0 + WORDS:
            fails.append(f"smc_{k-1} hbm_result 行数 {len(res)} 不足输出窗")
            continue
        alpha = line_to_f32x4(hbm[ALPHA_LINE0])[0]
        # 窗内：== IEEE(x·α)
        for i in range(OUT_LINE0, OUT_LINE0 + WORDS):
            ref = ieee_ref_line(line_to_f32x4(hbm[i - OUT_LINE0]), alpha)
            if res[i] != ref:
                fails.append(f"smc_{k-1} HBM 窗行{i}: dump={res[i]} ref={ref}")
                break
        # 窗外前段：[0, OUT_LINE0) == staged 镜像
        for i in range(OUT_LINE0):
            if res[i] != hbm[i]:
                fails.append(f"smc_{k-1} HBM 窗外行{i} 被改写: "
                             f"staged={hbm[i]} dump={res[i]}")
                break
        # 窗外尾段：[OUT_LINE0+WORDS, 末) 全零
        for i in range(OUT_LINE0 + WORDS, len(res)):
            if res[i] != "0" * 32:
                fails.append(f"smc_{k-1} HBM 尾段行{i} 非零（越窗写）: {res[i]}")
                break
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("case")
    a = ap.parse_args()
    if not (a.pre or a.post) or (a.pre and a.post):
        print("用法: --pre|--post <case_dir>")
        return 2
    need = [os.path.join(a.case, "hbm_data", f"memory_init{k}.hex") for k in range(1, 9)]
    need += [os.path.join(a.case, f"smc_{k-1}", "output_bank", "bank2_final.hex")
             for k in range(1, 9)]
    missing = [p for p in need if not os.path.isfile(p)]
    if missing:
        print(f"缺文件: {missing[:3]}{'...' if len(missing) > 3 else ''}")
        return 2
    fails = check_pre(a.case) if a.pre else check_post(a.case)
    if fails:
        print(f"FAIL ({len(fails)}):")
        for f_ in fails[:20]:
            print(" ", f_)
        return 1
    print(f"PASS {'pre' if a.pre else 'post'}: 8/8 通道 "
          f"{'golden==IEEE(x·α) 且零尾全零' if a.pre else 'HBM 窗==IEEE 参考且窗外零改写'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
