#!/usr/bin/env python3
"""m3lite_gate3_case_gen —— ADR-060 / M3-lite 门③步骤③：最小 LOAD→COMPUTE case 装配器。

输入来源（三链各司其职，职责边界见 ADR-060 §5）：
    - LOAD 段（stage_0）：**v3 上板血统生成器**（~/m3lite_gate3_gen，scratch 构建，
      不改 ~/Jianyuan 档案）产出，**并与门② VCS 实证发射逐字节互校**——
      不一致即响亮停止（不消费未验证形态）。
    - COMPUTE 段（stage_1）：v3 生成器的 mul_scalar compute 12 队列 hex 原样消费
      （en 字段源级正确，免位手术）；本沙箱无同级参照，结构性检查 + VCS 行为收口。
    - 期望与 sentinel：本装配器**独立**计算（fp32 numpy，从冻结 HBM 初值推导），
      与 v3 golden 的窗口逐字节一致作为**布局理解校验**（非独立真源，同源于
      HBM 初值——证据分级落账）。

产出（<sandbox>/test_atomic_ops/multi_inst_stage/<name>/）：
    smi_iq_inst/stage_0/{iq_ldb,iq_ldb_axi}.hex   # 双窗口 bulk LOAD（WC 行/窗）
    smi_iq_inst/stage_1/iq_*.hex                  # 12 队列 compute（无 STORE——步骤③）
    smc_0/ur_data/bank{0..7}.hex                  # 全 sentinel（≠源图案，B4c 纪律）
    smc_0/output_bank/bank{0..7}_final.hex        # 期望终态（stock compare 第二意见）
    smc_{1..7}/ur_data/bank{0..7}.hex             # sentinel′（修正② lineage）
    g3_expect.json                                # 门③比对器期望清单（含屏障参数）
    memory_init_snapshot/                         # 全局 memory_init 副本（证据冻结）
    v3_gen/                                       # v3 生成器原始输出归档（含其 golden）

屏障参数（TB +G3_BARRIER_*）：exp_ldb/exp_ldbaxi = stage_0 两文件实际行数
（指令数=信用 done 脉冲数），timeout 默认 5000（ADR-060 §2.2-3：超时即 RED，
禁止调大消红）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, "src"))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import ldb_review_case_gen as g2  # noqa: E402  门②生成器（图案/发射/守卫复用）
import stb_solo_case_gen as s1    # noqa: E402  门①生成器（STB/STBAXI 发射互校）

UR_BANKS = g2.UR_BANKS
UR_BANK_DEPTH = g2.UR_BANK_DEPTH
LINE_BYTES = g2.LINE_BYTES
HBM_INIT_DIR = g2.HBM_INIT_DIR

V3_GEN_DIR = os.path.expanduser("~/m3lite_gate3_gen")
V3_GEN_BIN = os.path.join(V3_GEN_DIR, "gen_mul_scalar_g3")

# v3 bulk 布局（lsu_op_base.cpp :80-107，intlv=0 单 SMC）：
#   input  @ byte 0（bank0 窗），scalar @ 4KB（bank1 窗），output @ 8KB（bank2 窗）
INPUT_BANK, SCALAR_BANK, OUTPUT_BANK = 0, 1, 2
SCALAR_HBM_BYTE_BASE = 0x1000
OUTPUT_HBM_BYTE_BASE = 0x2000
SCALAR_F32_BITS = 0x3FA00000  # 1.25f（mul_scalar_lsu_op.cpp:40 固定值）

# v3 stage_1 期望文件集（源级依据 mul_scalar_op.cpp:505-510 六条 rtovr 路由 +
# :952 命名 rtovr-<target_port>，is_lsu_iq 过滤后恰 13 文件：
# ur-r0/ur-r1 + 68_vin0/69_vin1/70_vout0 + rtovr-{3,4,14,15,16,35} + fpmul + ur-w0。
# 2026-10-07 首跑被本集合拦下（曾按早期侦察记 12 漏 rtovr-16）——守卫按设计工作，
# 回源核实路由表后订正为 13。）
EXPECTED_COMPUTE_FILES = {
    "iq_ur-r0.hex", "iq_ur-r1.hex",
    "iq_68_vin0.hex", "iq_69_vin1.hex", "iq_70_vout0.hex",
    "iq_rtovr-3.hex", "iq_rtovr-4.hex", "iq_rtovr-14.hex",
    "iq_rtovr-15.hex", "iq_rtovr-16.hex", "iq_rtovr-35.hex",
    "iq_fpmul.hex", "iq_ur-w0.hex",
}


# ── fp32 工具（期望独立计算用；numpy 非必需，struct 即可）─────────────────────
def f32_from_le(b: bytes) -> float:
    return struct.unpack("<f", b)[0]


def f32_to_le(v: float) -> bytes:
    # float32 截断：先打包成 f32 再解回，保证与 C float 语义一致
    return struct.pack("<f", struct.unpack("<f", struct.pack("<f", v))[0])


def read_hex_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def hex_line_to_bytes_be(line: str) -> bytes:
    """case 文件行（32 hex，MSB 在前，write_hex_file :311-319）→ 16 字节（小端序索引）。"""
    assert len(line) == 32, f"行宽异常：{line!r}"
    be = bytes.fromhex(line)
    return be[::-1]  # 转 byte[0..15] 自然序


def bytes_to_hex_line_le_as_msb(b: bytes) -> str:
    assert len(b) == 16
    return b[::-1].hex()


# ── v3 生成器调用与产物校验 ──────────────────────────────────────────────────
def run_v3_gen(work_dir: str, words: int, seed: int) -> dict:
    """scratch 构建的 v3 生成器产出原始 case；返回路径集。响亮失败。"""
    if not os.path.isfile(V3_GEN_BIN):
        raise SystemExit(
            f"[G3-GEN-GUARD] v3 生成器不存在：{V3_GEN_BIN}\n"
            f"  先构建（scratch，不改 ~/Jianyuan 档案）：\n"
            f"  cd {V3_GEN_DIR} && g++ -std=c++17 -O2 -Wall -I. -o gen_mul_scalar_g3 "
            f"load_store_v3/mul_scalar/gen_mul_scalar_lsu_case.cpp "
            f"load_store_v3/mul_scalar/mul_scalar_lsu_op.cpp load_store_v3/lsu_op_base.cpp "
            f"op/mul_scalar/mul_scalar_op.cpp op/common/lsu_emit.cpp "
            f"app/app_json_parser.cpp app/fp16_utils.cpp iq/modules/fpmul_iq.cpp "
            f"iq/modules/rtovr_iq.cpp iq/modules/ur_iq.cpp iq/modules/ldb_iq.cpp iq/modules/stb_iq.cpp")
    if not (1 <= words <= 255):
        raise SystemExit(f"[G3-GEN-GUARD] words={words} 越界 [1,255]")
    if words % 4 != 0:
        raise SystemExit(f"[G3-GEN-GUARD] words={words} 须为 4 的倍数（fp32 4 元素/行对齐 + "
                         f"bulk burst 整除纪律；门③最小实验不需要尾段形态）")
    case_root = os.path.join(work_dir, "v3_raw")
    hbm_dir = os.path.join(work_dir, "v3_hbm")
    os.makedirs(case_root, exist_ok=True)
    os.makedirs(hbm_dir, exist_ok=True)
    env = dict(os.environ)
    env.update({"G3_NUM_SMC": "1", "G3_SMC_ID_EN": "0", "G3_INTLV": "0",
                "LSU_WC": str(words)})
    r = subprocess.run([V3_GEN_BIN, case_root, hbm_dir, str(seed), "fp32", str(words)],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"[G3-GEN] v3 生成器失败 exit={r.returncode}\nstdout:{r.stdout}\nstderr:{r.stderr}")
    return {"case_root": case_root, "hbm_dir": hbm_dir, "stdout": r.stdout}


def validate_v3_output(v3: dict, words: int) -> dict:
    """结构校验 + 关键字段回读。返回 {ldb, axi, compute, stb, stb_axi, hbm_init1}。"""
    stage0 = os.path.join(v3["case_root"], "smi_iq_inst", "stage_0")
    stage1 = os.path.join(v3["case_root"], "smi_iq_inst", "stage_1")
    stage2 = os.path.join(v3["case_root"], "smi_iq_inst", "stage_2")
    for d in (stage0, stage1, stage2):
        if not os.path.isdir(d):
            raise SystemExit(f"[G3-GEN] v3 产物缺 stage 目录：{d}")
    ldb = read_hex_lines(os.path.join(stage0, "iq_ldb.hex"))
    axi = read_hex_lines(os.path.join(stage0, "iq_ldb_axi.hex"))
    if len(ldb) != 2 or len(axi) != 2:
        raise SystemExit(f"[G3-GEN] stage_0 行数异常：LDB {len(ldb)} 行 / LDBAXI {len(axi)} 行"
                         f"（期望各 2：bank0 input + bank1 scalar，WC={words} bulk 单 burst）")
    compute = {}
    for fn in sorted(os.listdir(stage1)):
        if fn.startswith("iq_") and fn.endswith(".hex"):
            compute[fn] = read_hex_lines(os.path.join(stage1, fn))
    missing = EXPECTED_COMPUTE_FILES - set(compute)
    extra = set(compute) - EXPECTED_COMPUTE_FILES
    if missing or extra:
        raise SystemExit(f"[G3-GEN] stage_1 文件集不符：缺 {sorted(missing)} 多 {sorted(extra)}")
    # stage_2 STORE（步骤④）：STB + STBAXI 各恰 1 行（单 burst 平坦形态）
    stb = read_hex_lines(os.path.join(stage2, "iq_stb.hex"))
    stb_axi = read_hex_lines(os.path.join(stage2, "iq_stb_axi.hex"))
    if len(stb) != 1 or len(stb_axi) != 1:
        raise SystemExit(f"[G3-GEN] stage_2 行数异常：STB {len(stb)} 行 / STBAXI {len(stb_axi)} 行"
                         f"（期望各 1：bank2 窗 {words} 行 → HBM 0x2000 单 burst）")
    hbm1 = os.path.join(v3["hbm_dir"], "memory_init1.hex")
    if not os.path.isfile(hbm1):
        raise SystemExit(f"[G3-GEN] v3 未产出 memory_init1.hex（{v3['hbm_dir']}）"
                         f"——hbm_dir 重定向必须生效，禁碰全局目录")
    return {"ldb": ldb, "axi": axi, "compute": compute,
            "stb": stb, "stb_axi": stb_axi, "hbm_init1": hbm1}


def _hexval(lines: list[str]) -> list[int]:
    """按值归一（2026-10-07 实证：v3 定宽打印前导零、门②去前导零，
    两者整数值 XOR==0；$readmemh 值语义等价。真比特差异仍会被拦）。"""
    return [int(x, 16) for x in lines]


def cross_check_load_stage(v3hex: dict, words: int) -> None:
    """v3 stage_0 与门② VCS 实证发射（同参数）按值互校；不一致即停止。"""
    # 窗口 A：bank0 off0 gr_base=0x0；窗口 B：bank1 off0 gr_base=0x1000（v3 bulk 布局）
    ref_ldb, ref_axi = [], []
    for bank, gr_base in ((INPUT_BANK, 0x0), (SCALAR_BANK, SCALAR_HBM_BYTE_BASE)):
        lb, ax = g2.build_and_emit([{"lines": words, "brst": 15}], ur_bank=bank,
                                   ur_offset=0, gr_base=gr_base, smc_id_en=0)
        ref_ldb += lb
        ref_axi += ax
    if _hexval(v3hex["ldb"]) != _hexval(ref_ldb) or _hexval(v3hex["axi"]) != _hexval(ref_axi):
        msg = ["[G3-GEN] stage_0 与门②发射互校失败（不消费未验证形态）"]
        for tag, a, b in (("LDB", v3hex["ldb"], ref_ldb), ("LDBAXI", v3hex["axi"], ref_axi)):
            if _hexval(a) != _hexval(b):
                msg.append(f"  {tag}: v3 {len(a)} 行 vs ref {len(b)} 行")
                for i, (x, y) in enumerate(zip(a, b)):
                    if int(x, 16) != int(y, 16):
                        xor = int(x, 16) ^ int(y, 16)
                        msg.append(f"    行{i}: v3={x}")
                        msg.append(f"         ref={y}  xor={xor:#x}")
        raise SystemExit("\n".join(msg))


def cross_check_store_stage(v3hex: dict, words: int) -> None:
    """v3 stage_2 与门① VCS 实证 STB 发射（同参数）按值互校；不一致即停止。

    （ADR-060 §6.1-④：完整链路要求 STORE 指令同样不消费未验证形态——
    互校参数与 v3 bulk 布局一一对应：bank2 off0 → HBM 0x2000，单 burst。）
    """
    ref_stb, ref_axi = s1.build_and_emit([{"lines": words, "brst": 15}],
                                         ur_bank=OUTPUT_BANK, ur_offset=0,
                                         gr_base=OUTPUT_HBM_BYTE_BASE, smc_id_en=0)
    if _hexval(v3hex["stb"]) != _hexval(ref_stb) or _hexval(v3hex["stb_axi"]) != _hexval(ref_axi):
        msg = ["[G3-GEN] stage_2 与门① STB 发射互校失败（不消费未验证形态）"]
        for tag, a, b in (("STB", v3hex["stb"], ref_stb), ("STBAXI", v3hex["stb_axi"], ref_axi)):
            if _hexval(a) != _hexval(b):
                msg.append(f"  {tag}: v3 {len(a)} 行 vs ref {len(b)} 行")
                for i, (x, y) in enumerate(zip(a, b)):
                    if int(x, 16) != int(y, 16):
                        xor = int(x, 16) ^ int(y, 16)
                        msg.append(f"    行{i}: v3={x}")
                        msg.append(f"         ref={y}  xor={xor:#x}")
        raise SystemExit("\n".join(msg))


# ── 期望计算（独立推导 + v3 golden 一致性校验）────────────────────────────────
def compute_expectations(hbm_init1: str, words: int) -> dict:
    """从冻结 HBM 初值推独立期望；返回 {input_lines, scalar_lines, product_lines}（MSB hex）。"""
    lines = read_hex_lines(hbm_init1)
    if len(lines) < (OUTPUT_HBM_BYTE_BASE // LINE_BYTES) + words:
        raise SystemExit(f"[G3-GEN] memory_init1 行数 {len(lines)} 不足（输出窗需 "
                         f"{(OUTPUT_HBM_BYTE_BASE // LINE_BYTES) + words} 行）")
    in0 = INPUT_BANK  # 仅注释锚点，无实际用途（HBM 布局与 bank 无关）
    del in0
    input_lines = lines[0:words]
    scalar_lines = lines[SCALAR_HBM_BYTE_BASE // LINE_BYTES:
                         SCALAR_HBM_BYTE_BASE // LINE_BYTES + words]
    # scalar 窗每行应为 4×1.25f（fill_input_port_data）；验证之（布局理解闸）
    expect_scalar = bytes_to_hex_line_le_as_msb(struct.pack("<IIII", *([SCALAR_F32_BITS] * 4)))
    for k, ln in enumerate(scalar_lines):
        if ln != expect_scalar:
            raise SystemExit(f"[G3-GEN] scalar 窗第 {k} 行 ≠ 4×1.25f：{ln}（布局理解被推翻，停止）")
    product_lines = []
    for ln in input_lines:
        b = hex_line_to_bytes_be(ln)
        out = b""
        for e in range(4):
            a = f32_from_le(b[e * 4:(e + 1) * 4])
            r = a * 1.25
            # 输入域 [-2,2]×1.25 ⊆ 规格化区（生成器分布），不触 FTZ——但仍响拒意外：
            r32 = struct.unpack("<f", struct.pack("<f", r))[0]
            if r32 != 0.0 and abs(r32) < 1.17549435e-38:
                raise SystemExit(f"[G3-GEN] 乘积落入非规格化区（{r32!r}）——FTZ 语义分歧区，换 seed")
            out += f32_to_le(r)
        product_lines.append(bytes_to_hex_line_le_as_msb(out))
    return {"input_lines": input_lines, "scalar_lines": scalar_lines,
            "product_lines": product_lines}


def cross_check_golden(v3: dict, exp: dict, words: int) -> None:
    """v3 golden（output_bank/bank2_final.hex）窗口 == 独立期望（同源校验，非独立真源）。"""
    g = read_hex_lines(os.path.join(v3["case_root"], "smc_0", "output_bank",
                                    f"bank{OUTPUT_BANK}_final.hex"))
    for k in range(words):
        if g[k] != exp["product_lines"][k]:
            raise SystemExit(f"[G3-GEN] v3 golden 第 {k} 行与独立期望不符：\n"
                             f"  v3={g[k]}\n  exp={exp['product_lines'][k]}\n"
                             f"（布局/字节序理解被推翻，停止）")


# ── case 物化 ────────────────────────────────────────────────────────────────
def materialize(case_dir: str, v3: dict, v3hex: dict, exp: dict, words: int,
                seed: int, barrier_timeout: int, write_hbm_init: bool,
                merge_stages: bool = False, with_store: bool = False,
                fi_bp_b: int = 0) -> dict:
    # stage_0 LOAD（与门②互校后的 v3 产物）
    s0 = os.path.join(case_dir, "smi_iq_inst", "stage_0")
    g2.write_hex_lines(os.path.join(s0, "iq_ldb.hex"), v3hex["ldb"])
    g2.write_hex_lines(os.path.join(s0, "iq_ldb_axi.hex"), v3hex["axi"])
    if merge_stages:
        # 负向对照：compute 13 队列并入 stage_0 与 LOAD 同 trigger。
        # 结构性读先于写（2026-10-07 m3lite_g3_lc_w16 实物 trace 定量）：
        # trigger→compute 首读 ~5 拍 ≺ trigger→LDB 首写 ~21 拍、末写 ~62 拍
        # → C1/C3/C4(乘积账) 必被证伪；段间 IQ_EN 同步 ~588 拍只在 trigger 前，
        # 并段后不再隔开读与写。无需任何 TB plusarg。
        # 步骤④扩：--with-store 时 STB/STBAXI 一并并入（17 队列 merged-3），
        # STB 的 URA 读同样结构性撞上未落位窗口（C3s/C6/C8 证伪面）。
        s1 = s0
    else:
        s1 = os.path.join(case_dir, "smi_iq_inst", "stage_1")
    # stage_1 COMPUTE（13 队列原样消费；merge 时落 stage_0）
    for fn, lines in sorted(v3hex["compute"].items()):
        g2.write_hex_lines(os.path.join(s1, fn), lines)
    # stage_2 STORE（步骤④ --with-store；与门①发射互校后物化；merge 时落 stage_0）
    if with_store:
        s2 = s0 if merge_stages else os.path.join(case_dir, "smi_iq_inst", "stage_2")
        g2.write_hex_lines(os.path.join(s2, "iq_stb.hex"), v3hex["stb"])
        g2.write_hex_lines(os.path.join(s2, "iq_stb_axi.hex"), v3hex["stb_axi"])
    if merge_stages:
        n_q = 2 + 2 + len(v3hex["compute"]) + (2 if with_store else 0)
        stages_md = (f"# {os.path.basename(case_dir)} stages\n\n- stage_count: 1"
                     f"（merged_single_stage 负向对照{'，含 STORE' if with_store else ''}）\n\n"
                     f"- stage_0: load + compute{' + store' if with_store else ''} 并段同 trigger"
                     f"（{n_q} 队列；v3 bulk WC={words}，LOAD 与门②、STORE 与门①发射按值互校通过；"
                     f"compute 13 队列原样消费）\n"
                     f"- 结构性竞态：compute 首读 ≈trigger+5 ≺ LDB 首写 ≈trigger+21；"
                     f"STB URA 读同样先于 bank2 乘数落位\n")
    elif with_store:
        stages_md = (f"# {os.path.basename(case_dir)} stages\n\n- stage_count: 3\n\n"
                     f"- stage_0: load（v3 bulk WC={words}，与门②发射按值互校通过）\n"
                     f"- stage_1: compute（v3 mul_scalar 13 队列，原样消费）\n"
                     f"- stage_2: store（STB/STBAXI 各 1 行，与门①发射按值互校通过；"
                     f"bank2 窗 {words} 行 → HBM byte 0x2000，单 burst）\n")
    else:
        stages_md = (f"# {os.path.basename(case_dir)} stages\n\n- stage_count: 2\n\n"
                     f"- stage_0: load（v3 bulk WC={words}，与门②发射按值互校通过）\n"
                     f"- stage_1: compute（v3 mul_scalar 13 队列，原样消费）\n")
    with open(os.path.join(case_dir, "smi_iq_inst", "stages.md"), "w", encoding="utf-8") as f:
        f.write(stages_md)

    # smc_0 UR 全 sentinel；期望终态 = load 后 bank0/1 窗=源图案、compute 后 bank2 窗=乘积
    for b in range(UR_BANKS):
        rows = [g2.sentinel_line(b, k) for k in range(UR_BANK_DEPTH)]
        g2.write_hex_lines(os.path.join(case_dir, "smc_0", "ur_data", f"bank{b}.hex"), rows)
        final = list(rows)
        if b == INPUT_BANK:
            final[0:words] = exp["input_lines"]
        elif b == SCALAR_BANK:
            final[0:words] = exp["scalar_lines"]
        elif b == OUTPUT_BANK:
            final[0:words] = exp["product_lines"]
        g2.write_hex_lines(os.path.join(case_dir, "smc_0", "output_bank",
                                        f"bank{b}_final.hex"), final)
    # smc_1..7 sentinel′（修正② lineage）
    for i in range(1, 8):
        for b in range(UR_BANKS):
            g2.write_hex_lines(os.path.join(case_dir, f"smc_{i}", "ur_data", f"bank{b}.hex"),
                               [g2.sentinel_aux_line(i, b, k) for k in range(UR_BANK_DEPTH)])

    # HBM：全局写 v3 产物（窗口含真实随机输入）；副本冻结留 case。2..8 = 泄漏标记。
    snap = os.path.join(case_dir, "memory_init_snapshot")
    os.makedirs(snap, exist_ok=True)
    hbm_lines = read_hex_lines(v3hex["hbm_init1"])
    mark = [g2.mark_pattern_line(k) for k in range(g2.HBM_MARK_LINES)]
    if write_hbm_init:
        g2.write_hex_lines(os.path.join(HBM_INIT_DIR, "memory_init1.hex"), hbm_lines)
        for i in range(2, 9):
            g2.write_hex_lines(os.path.join(HBM_INIT_DIR, f"memory_init{i}.hex"), mark)
    g2.write_hex_lines(os.path.join(snap, "memory_init1.hex"), hbm_lines)
    for i in range(2, 9):
        g2.write_hex_lines(os.path.join(snap, f"memory_init{i}.hex"), mark)

    # v3 原始产物归档（含其 golden 与 meta）
    shutil.copytree(v3["case_root"], os.path.join(case_dir, "v3_gen"))

    expect = {
        "schema": "m3lite.gate3_expect/v1",
        "case": os.path.basename(case_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_smc": 0, "smc_count": 8, "words": words, "seed": seed,
        "dtype": "fp32", "scalar_f32_bits": f"{SCALAR_F32_BITS:08x}",
        "ur_windows": {
            "input":  {"bank": INPUT_BANK,  "line0": 0, "lines": words},
            "scalar": {"bank": SCALAR_BANK, "line0": 0, "lines": words},
            "output": {"bank": OUTPUT_BANK, "line0": 0, "lines": words},
        },
        "hbm_layout": {"input_byte_base": 0x0, "scalar_byte_base": SCALAR_HBM_BYTE_BASE,
                       "output_byte_base": OUTPUT_HBM_BYTE_BASE, "intlv": 0},
        "barrier": {"exp_ldb_done": len(v3hex["ldb"]),
                    "exp_ldbaxi_done": len(v3hex["axi"]),
                    "timeout_cycles": barrier_timeout},
        "compute_files": sorted(v3hex["compute"].keys()),
        "compute_line_counts": {fn: len(ls) for fn, ls in sorted(v3hex["compute"].items())},
        "hbm_init_dir": HBM_INIT_DIR,
        "hbm_init_written": write_hbm_init,
        "notes": [
            "TB 辅助信用屏障（ADR-060 §2.2，F5）：仿真限定，非 host 可执行协议",
            "期望乘积由本装配器从冻结 HBM 初值独立推导；v3 golden 仅作布局一致性校验",
            "stage_0 与门② VCS 实证发射按值互校通过才物化（v3 定宽前导零 vs 门② "
            "去前导零，整数值 XOR==0——2026-10-07 实证，$readmemh 值语义等价）",
        ],
    }
    if with_store:
        # 步骤④ STORE 期望（ADR-060 §6；无 "store" 键 = v1 步骤③模式兼容）
        expect["store"] = {
            "enabled": True,
            "hbm_window": {"byte_base": OUTPUT_HBM_BYTE_BASE,
                           "addr0": OUTPUT_HBM_BYTE_BASE // LINE_BYTES,
                           "lines": words, "line_bytes": LINE_BYTES},
            "exp_stb_done": len(v3hex["stb"]),
            "exp_stbaxi_done": len(v3hex["stb_axi"]),
            "exp_bresp": len(v3hex["stb_axi"]),   # 单 burst 单 B；多描述符时按 burst 数
            "crosscheck": "byte_exact_vs_gate1_emit",
            "product_lines": exp["product_lines"],
        }
        expect["wbarrier"] = {"bank": OUTPUT_BANK, "writes": words,
                              "stage": 1, "timeout_cycles": barrier_timeout}
        expect["s4barrier"] = {"addr0": OUTPUT_HBM_BYTE_BASE // LINE_BYTES,
                               "writes": words, "bresp": len(v3hex["stb_axi"]),
                               "timeout_cycles": barrier_timeout}
        expect["notes"].append(
            "STORE 完成判据 = HBM 模型 RAM 写口落实 + B 响应计数；STB/STBAXI 信用"
            "结构性先于 HBM 写落实（ADR-060 §6.2），只记录不门控")
        if fi_bp_b > 0:
            # min_waited = cycles − 100：100 = 注入点（首个 AWVALID 上升沿，
            # stage_2 STBAXI 地址相）→S4 enter 重叠 + 模型重驱动余量的保守
            # 上界（drive-2 实测 BVALID@1191 vs S4 enter@1219；AW 更早 ~36 拍，
            # 重叠随之 ~64 < 100）；比对器另有精确一致性断言
            # （waited∈[bp_release−s4_enter−2,+50]）双保险。
            expect["fault_injection"] = {
                "type": "axi_b_backpressure", "cycles": fi_bp_b,
                "min_waited": fi_bp_b - 100,
                "note": "TB-only force/release bmc_ds2_bready（首个 AWVALID 上升沿"
                        "起押 B 通道，先于 BVALID 就位；写数据路不动）；故障注入"
                        "运行与正向证据分别归档（用户 2026-10-07 要求⑤）。"
                        "W 通道反压已退役——300 拍 wready 反压永久锁死写路径，"
                        "见 ADR-060 errata ⑤；BVALID 沿武装因结构性迟到同日退役，"
                        "见 errata 6"}
    if merge_stages:
        expect["negative_control"] = "merged_single_stage"
        expect["barrier"]["unused"] = True
        if with_store:
            expect["wbarrier"]["unused"] = True
            expect["s4barrier"]["unused"] = True
            expect.pop("fault_injection", None)
        expect["notes"].append(
            "merged_single_stage 负向对照：LOAD+COMPUTE 并段单 trigger，结构性"
            "读先于写竞态（trigger→读 ~5 拍 ≺ trigger→首写 ~21 拍）；屏障参数不使用")
    with open(os.path.join(case_dir, "g3_expect.json"), "w", encoding="utf-8") as f:
        json.dump(expect, f, indent=2, ensure_ascii=False)
    return expect


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="case 名（multi_inst_stage/<name>）")
    ap.add_argument("--words", type=int, default=16, help="每窗口行数（默认 16，4 的倍数）")
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--barrier-timeout", type=int, default=5000)
    ap.add_argument("--sandbox", default=os.path.expanduser("~/u280_sim_v422"))
    ap.add_argument("--work-dir", default=None, help="v3 生成暂存（默认 build/m3lite_gate3_gen_out/<name>）")
    ap.add_argument("--no-hbm-init", action="store_true")
    ap.add_argument("--merge-stages", action="store_true",
                    help="负向对照：LOAD+COMPUTE 并入单 stage 同 trigger"
                         "（结构性读先于写竞态，证伪 C1/C3；比对用 --mode negative）")
    ap.add_argument("--with-store", action="store_true",
                    help="步骤④：物化 stage_2 STORE（STB/STBAXI 各 1 行，与门①发射"
                         "按值互校后落盘）；与 --merge-stages 组合 = merged-3 负控")
    ap.add_argument("--fi-bp-b", type=int, default=0, metavar="N",
                    help="故障注入标签：expect 记录 axi_b_backpressure N 拍"
                         "（蕴含 --with-store；TB 侧实际注入由 run 脚本传 +G3_AXI_BP_B=N。"
                         "W 通道反压已退役：wready 长反压锁死写路径，ADR-060 errata ④）")
    args = ap.parse_args()
    if args.fi_bp_b > 0:
        args.with_store = True
    if args.merge_stages and args.fi_bp_b > 0:
        raise SystemExit("[G3-GEN-GUARD] --fi-bp-b 与 --merge-stages 互斥"
                         "（负控不携带故障注入标签）")

    case_dir = os.path.join(args.sandbox, "test_atomic_ops", "multi_inst_stage", args.name)
    if os.path.exists(case_dir):
        raise SystemExit(f"[G3-GEN-GUARD] case 目录已存在：{case_dir}（先清理再生成，防混旧产物）")
    work_dir = args.work_dir or os.path.join(ROOT, "build", "m3lite_gate3_gen_out", args.name)
    os.makedirs(work_dir, exist_ok=True)

    v3 = run_v3_gen(work_dir, args.words, args.seed)
    v3hex = validate_v3_output(v3, args.words)
    cross_check_load_stage(v3hex, args.words)
    if args.with_store:
        cross_check_store_stage(v3hex, args.words)
    exp = compute_expectations(v3hex["hbm_init1"], args.words)
    cross_check_golden(v3, exp, args.words)
    expect = materialize(case_dir, v3, v3hex, exp, args.words, args.seed,
                         args.barrier_timeout, not args.no_hbm_init,
                         merge_stages=args.merge_stages,
                         with_store=args.with_store, fi_bp_b=args.fi_bp_b)

    print(f"[G3-GEN] case 物化完成：{case_dir}")
    if args.merge_stages:
        print(f"  stage_0 并段：LDB×{len(v3hex['ldb'])} + LDBAXI×{len(v3hex['axi'])}"
              f" + COMPUTE {len(v3hex['compute'])} 队列"
              f"{' + STORE 2 队列' if args.with_store else ''} 同 trigger（负向对照）")
        print(f"  比对：python3 verification_v2/tools/m3lite_gate3_compare.py "
              f"{case_dir} --mode negative")
    else:
        print(f"  stage_0 LOAD：LDB×{len(v3hex['ldb'])} + LDBAXI×{len(v3hex['axi'])}"
              f"（与门②发射互校一致）；stage_1 COMPUTE：{len(v3hex['compute'])} 队列")
        if args.with_store:
            print(f"  stage_2 STORE：STB×1 + STBAXI×1（与门①发射互校一致）")
            wb, s4 = expect["wbarrier"], expect["s4barrier"]
            print(f"  屏障 plusargs：+G3_BARRIER=1 +G3_BARRIER_LDB={expect['barrier']['exp_ldb_done']} "
                  f"+G3_BARRIER_LDBAXI={expect['barrier']['exp_ldbaxi_done']} "
                  f"+G3_BARRIER_TIMEOUT={expect['barrier']['timeout_cycles']} "
                  f"+G3_WBARRIER=1 +G3_WBARRIER_BANK={wb['bank']} +G3_WBARRIER_WRITES={wb['writes']} "
                  f"+G3_WBARRIER_STAGE={wb['stage']} +G3_WBARRIER_TIMEOUT={wb['timeout_cycles']} "
                  f"+G3_S4_BARRIER=1 +G3_S4_ADDR0={s4['addr0']} +G3_S4_WRITES={s4['writes']} "
                  f"+G3_S4_BRESP={s4['bresp']} +G3_S4_TIMEOUT={s4['timeout_cycles']}"
                  + (f" +G3_AXI_BP_B={args.fi_bp_b}" if args.fi_bp_b > 0 else ""))
        else:
            print(f"  屏障 plusargs：+G3_BARRIER=1 +G3_BARRIER_LDB={expect['barrier']['exp_ldb_done']} "
                  f"+G3_BARRIER_LDBAXI={expect['barrier']['exp_ldbaxi_done']} "
                  f"+G3_BARRIER_TIMEOUT={expect['barrier']['timeout_cycles']}")
        print(f"  比对：python3 verification_v2/tools/m3lite_gate3_compare.py {case_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
