#!/usr/bin/env python3
"""stb_solo_case_gen —— ADR-058 / M3-lite 门①：STB 独验 case 生成器。

由编译器**生产发射组件**（SMIHeader + PhysicalInstruction + LIRPacker，ADR-052 D5 同路径）
生成 STB/STBAXI 指令，并物化完整 VCS case：

    <sandbox>/test_atomic_ops/multi_inst_stage/<name>/
        smc_0/ur_data/bank0..7.hex            # bank=ur_bank 为确定性图案，其余全零
        smc_0/output_bank/bank0..7_final.hex  # == ur_data（UR 无损断言 + 堵 compare SKIP 假绿）
        smc_1..7/                             # 空目录：诱使 TB dump 全 8 颗 HBM（A4c en 泄漏证据）
        smi_iq_inst/stage_0/iq_stb.hex        # 平坦形态（手写 golden row5 家族，无 embed/TERM）
        smi_iq_inst/stage_0/iq_stb_axi.hex    # 与 STB 同拍（同 stage 同 TRIGGER）
        hbm_expect.json                       # 比对脚本 hbm_window_compare.py 的期望清单
        memory_init_snapshot/                 # 本 case 预载的全局 memory_init*.hex 副本（证据）

契约（全部有出处，见 RECON.md §3 / ADR-058 契约表）：
    - 配对恒等：每条描述符 lines == (loops+1) * (brst+1)（golden row5/row1-4 均满足）
    - brst ∈ [1,15]（brst=0 是 LDB 侧已证 RTL 死锁禁区，STB 侧同构未证 → 响拒）
    - STB HDR.INC 单位=行；STBAXI HDR.INC 单位=字节（golden row5：16 ↔ 256）
    - 单 SMC 独验：smc_id_en 显式 0（阈值门控，0 = 仅 SMC_0；缺省 7 = 8 SMC 全使能）
    - tid=2 照抄 golden（DEBT-052-STB-TID，语义未独立取证）

用法：
    python3 verification_v2/tools/stb_solo_case_gen.py \
        --name compiler_stb_solo_s1 --desc lines=16,brst=15 \
        --ur-bank 1 --gr-base 0x1000
    # S3 多描述符：--desc lines=16,brst=15 --desc lines=8,brst=7
    # S2 循环形态：--desc lines=32,brst=15,loops=1
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, "src"))

from driver.emitter import LIRPacker                    # noqa: E402
from ir.lir.lir_op import PhysicalInstruction, SMIHeader  # noqa: E402

UR_BANKS = 8
UR_BANK_DEPTH = 256          # 行（128b/行）
UR_BANK_SHIFT = 12           # ur_addr[14:12] = bank → bank 步进 1<<12 字节
LINE_BYTES = 16              # 128b/行
HBM_INIT_DIR = os.path.expanduser("~/u280_sim_v422/data_engine/sram")
HBM_PRELOAD_LINES = 4096     # memory_init1.hex 预载行数（窗口前后各留足非目标区）
HBM_MARK_LINES = 4096        # memory_init2..8.hex 标记行数（en 泄漏检查：必须覆盖窗口，
                             # 否则泄漏写 X→dump 0 落在 X 区恰为 0 检不出）
TID = 2                      # golden 照抄值


# ── 确定性数据图案（生成器与比对脚本共享本模块，防双源漂移）─────────────────
def ur_pattern_line(k: int) -> str:
    """UR 侧第 k 行（128b hex，MSB 在前；$readmemh/dump 同表示，无需字节序换算）。"""
    return f"{0x11110000 + k:08x}{0x22220000 + k:08x}{0x33330000 + k:08x}{0x44440000 + k:08x}"


def preload_pattern_line(k: int) -> str:
    """HBM 预载第 k 行：非零可识别（与 UR 图案及全零都显著不同）。"""
    return f"{0xA5A50000 + k:08x}{0x5A5A0000 + k:08x}{0xA5A50000 + k:08x}{0x5A5A0000 + k:08x}"


def mark_pattern_line(k: int) -> str:
    """memory_init2..8 的标记行：非零、非预载图案（en 泄漏时 X→dump 0 ≠ 本图案 → 可检出）。"""
    return f"{0xEEEE0000 + k:08x}" * 4


ZERO_LINE = "0" * 32


# ── 参数守卫（响亮拒绝，配单测）────────────────────────────────────────────
def parse_desc(s: str) -> dict:
    """'lines=16,brst=15[,loops=1]' → dict。"""
    d = {}
    for kv in s.split(","):
        k, _, v = kv.partition("=")
        k, v = k.strip(), v.strip()
        if k not in ("lines", "brst", "loops") or not v:
            raise SystemExit(f"[STB-GEN-GUARD] --desc 键非法：{kv!r}（允许 lines/brst/loops）")
        d[k] = int(v, 0)
    return d


def guard(name: str, descs: list[dict], ur_bank: int, ur_offset: int, gr_base: int,
          smc_id_en: int) -> None:
    if not (0 <= ur_bank < UR_BANKS):
        raise SystemExit(f"[STB-GEN-GUARD] ur_bank={ur_bank} 越界 [0,{UR_BANKS})")
    if ur_offset % LINE_BYTES != 0 or not (0 <= ur_offset < UR_BANK_DEPTH * LINE_BYTES):
        raise SystemExit(f"[STB-GEN-GUARD] ur_offset={ur_offset} 非 16B 对齐或越界")
    if gr_base % LINE_BYTES != 0 or gr_base <= 0:
        raise SystemExit(f"[STB-GEN-GUARD] gr_base={gr_base:#x} 必须为正且 16B 对齐"
                         f"（0 会削弱地址正确性证据）")
    if smc_id_en not in (0, 7):
        raise SystemExit(f"[STB-GEN-GUARD] smc_id_en={smc_id_en} 未复核形态："
                         f"仅 0（M3-lite 单 SMC）或 7（equal_8smc_v1 八目标广播）"
                         f"已复核，部分使能 1..6/越界 8..31 响拒")
    total = 0
    for i, d in enumerate(descs):
        lines, brst, loops = d.get("lines"), d.get("brst"), d.get("loops", 0)
        if lines is None or brst is None:
            raise SystemExit(f"[STB-GEN-GUARD] desc{i} 缺 lines/brst：{d}")
        if brst == 0:
            raise SystemExit(f"[STB-GEN-GUARD] desc{i} brst=0 是 RTL 死锁禁区"
                             f"（DEBT-050-LDBAXI-BRST0，STB 侧同构未证）")
        if not (1 <= brst <= 15):
            raise SystemExit(f"[STB-GEN-GUARD] desc{i} brst={brst} 越界 [1,15]")
        if not (1 <= lines <= 255):
            raise SystemExit(f"[STB-GEN-GUARD] desc{i} lines={lines} 越界 [1,255]（8b 字段）")
        if lines != (loops + 1) * (brst + 1):
            raise SystemExit(f"[STB-GEN-GUARD] desc{i} 配对恒等式不成立：lines={lines} != "
                             f"(loops={loops}+1)*(brst={brst}+1)={(loops+1)*(brst+1)}"
                             f"（golden row5/row1-4 均满足此式）")
        total += lines
    ur_line0 = ur_offset // LINE_BYTES
    if ur_line0 + total > UR_BANK_DEPTH:
        raise SystemExit(f"[STB-GEN-GUARD] UR 读越界：起始行 {ur_line0} + 总行数 {total} "
                         f"> {UR_BANK_DEPTH}")
    if gr_base + total * LINE_BYTES > HBM_PRELOAD_LINES * LINE_BYTES:
        raise SystemExit(f"[STB-GEN-GUARD] HBM 窗口越出预载区：gr_base={gr_base:#x} + "
                         f"{total} 行 > {HBM_PRELOAD_LINES} 行预载（非目标区证据依赖预载）")


# ── 指令发射 ────────────────────────────────────────────────────────────────
def build_and_emit(descs: list[dict], ur_bank: int, ur_offset: int, gr_base: int,
                   smc_id_en: int) -> tuple[list[str], list[str]]:
    """逐描述符构造 STB/STBAXI 对，经真 LIRPacker 发射，返回 (stb 行列表, stb_axi 行列表)。

    平坦形态（镜像手写 golden row5 家族）：每描述符一行 vld=1 数据行，无 INIT/embed/TERM；
    STB HDR.INC=行数（行单位）、STBAXI HDR.INC=行数*16（字节单位），loops>0 时生效。
    """
    packer = LIRPacker(config_dir="hw_config", verbosity=0)
    stb_instrs, axi_instrs = [], []
    ur_addr, axi_addr = (ur_bank << UR_BANK_SHIFT) | ur_offset, gr_base
    for d in descs:
        lines, brst, loops = d["lines"], d["brst"], d.get("loops", 0)
        stb_instrs.append(PhysicalInstruction(
            iq_name="stb", abs_t=len(stb_instrs), stage="BODY",
            # golden row5 实证：迭代只由 STBAXI 承担（LOOPS 在 AXI 侧），STB 恒 LOOPS=0
            # 一次读全部 CNT 行（UR 读由 STB 单元内部分批，两流节奏由信用门控协调）。
            # INC=brst+1 为惰性常数（LOOPS=0 时无效，row5 恰好 16=15+1 与之吻合）。
            header=SMIHeader(**{"inc": brst + 1, "vld": 1}),
            payload={"VALID": 1, "ur_addr": ur_addr, "ur_rd_line_cnt": lines,
                     "line_byte_cnt": 15, "line_stride": 1, "inst_id": 0,
                     "smc_id_en": smc_id_en, "tid": TID}))
        axi_instrs.append(PhysicalInstruction(
            iq_name="stb-axi", abs_t=len(axi_instrs), stage="BODY",
            header=SMIHeader(**{"inc": (brst + 1) * LINE_BYTES, "vld": 1,
                                **({"loops": loops} if loops else {})}),
            payload={"VALID": 1, "first_byte_strb": 0, "last_byte_strb": 0, "brst": brst,
                     "gr_base_addr": axi_addr, "gr_intlv_addr": 0, "inst_id": 0,
                     "smc_id_en": smc_id_en}))
        ur_addr += lines * LINE_BYTES
        axi_addr += lines * LINE_BYTES

    tmp = tempfile.mkdtemp(prefix="stb_solo_emit_")
    try:
        packer.emit_from_instructions(stb_instrs + axi_instrs, tmp, smc_num=1)
        out = {}
        for fn, key in (("iq_stb.hex", "stb"), ("iq_stb-axi.hex", "stb_axi")):
            p = os.path.join(tmp, "smc_0", fn)
            if not os.path.exists(p):
                raise RuntimeError(f"[STB-GEN] emitter 未产出 {fn}（{os.listdir(os.path.join(tmp, 'smc_0'))}）")
            with open(p, encoding="utf-8") as f:
                out[key] = [l.strip() for l in f if l.strip()]
        return out["stb"], out["stb_axi"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── case 物化 ───────────────────────────────────────────────────────────────
def write_hex_lines(path: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def materialize(case_dir: str, descs: list[dict], ur_bank: int, ur_offset: int,
                gr_base: int, smc_id_en: int, stb_hex: list[str], axi_hex: list[str],
                write_hbm_init: bool) -> dict:
    total = sum(d["lines"] for d in descs)
    ur_line0 = ur_offset // LINE_BYTES

    # UR 数据：目标 bank = 全零底 + 图案段；其余 bank 全零
    for b in range(UR_BANKS):
        rows = [ZERO_LINE] * UR_BANK_DEPTH
        if b == ur_bank:
            for k in range(total):
                rows[ur_line0 + k] = ur_pattern_line(k)
        write_hex_lines(os.path.join(case_dir, "smc_0", "ur_data", f"bank{b}.hex"), rows)
        # UR golden == 预置（STB 只读 UR：无损断言 + 堵 compare SKIP 假绿）
        write_hex_lines(os.path.join(case_dir, "smc_0", "output_bank", f"bank{b}_final.hex"), rows)

    # IQ hex
    write_hex_lines(os.path.join(case_dir, "smi_iq_inst", "stage_0", "iq_stb.hex"), stb_hex)
    write_hex_lines(os.path.join(case_dir, "smi_iq_inst", "stage_0", "iq_stb_axi.hex"), axi_hex)

    # smc_1..7 空目录（无 ur_data）：GEN 会为每个空目录写空 data_list_smc<i>_subq0.txt
    # （generate_data_list_coverage.py:922-931），TB 探针 fopen 命中即计入
    # case_smc_max_idx（tb_aiacc_top_v04_f_multi_stage.v:2304-2310），从而 dump
    # 全部 8 颗 HBM（:968）——A4c 的 en 泄漏检查才有证据载体；否则 TB 只 dump smc_0。
    for i in range(1, 8):
        os.makedirs(os.path.join(case_dir, f"smc_{i}"), exist_ok=True)

    # HBM 预载：memory_init1.hex = 已知填充；2..8 = 泄漏检测标记。副本留存 case。
    snap = os.path.join(case_dir, "memory_init_snapshot")
    os.makedirs(snap, exist_ok=True)
    pre1 = [preload_pattern_line(k) for k in range(HBM_PRELOAD_LINES)]
    mark = [mark_pattern_line(k) for k in range(HBM_MARK_LINES)]
    if write_hbm_init:
        write_hex_lines(os.path.join(HBM_INIT_DIR, "memory_init1.hex"), pre1)
        for i in range(2, 9):
            write_hex_lines(os.path.join(HBM_INIT_DIR, f"memory_init{i}.hex"), mark)
    write_hex_lines(os.path.join(snap, "memory_init1.hex"), pre1)
    for i in range(2, 9):
        write_hex_lines(os.path.join(snap, f"memory_init{i}.hex"), mark)

    expect = {
        "schema": "m3lite.stb_solo_expect/v1",
        "case": os.path.basename(case_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_smc": 0, "smc_count": 8,
        "window": {"byte_base": gr_base, "lines": total},
        "ur_source": {"bank": ur_bank, "byte_offset": ur_offset},
        "descriptors": descs,
        "smc_id_en": smc_id_en, "tid": TID,
        "preload": {"lines": HBM_PRELOAD_LINES, "pattern": "preload_pattern_line"},
        "mark": {"lines": HBM_MARK_LINES, "pattern": "mark_pattern_line"},
        "instructions": {"iq_stb": stb_hex, "iq_stb_axi": axi_hex},
        "hbm_init_dir": HBM_INIT_DIR,
        "hbm_init_written": write_hbm_init,
    }
    with open(os.path.join(case_dir, "hbm_expect.json"), "w", encoding="utf-8") as f:
        json.dump(expect, f, indent=2, ensure_ascii=False)
    return expect


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="case 名（multi_inst_stage/<name>）")
    ap.add_argument("--desc", action="append", required=True,
                    help="描述符：lines=N,brst=M[,loops=K]；可重复（多描述符平坦流）")
    ap.add_argument("--ur-bank", type=int, required=True)
    ap.add_argument("--ur-offset", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--gr-base", type=lambda s: int(s, 0), required=True)
    ap.add_argument("--smc-id-en", type=int, default=0)
    ap.add_argument("--sandbox", default=os.path.expanduser("~/u280_sim_v422"))
    ap.add_argument("--no-hbm-init", action="store_true", help="只生成 case，不写全局 memory_init")
    args = ap.parse_args()

    descs = [parse_desc(s) for s in args.desc]
    guard(args.name, descs, args.ur_bank, args.ur_offset, args.gr_base, args.smc_id_en)

    stb_hex, axi_hex = build_and_emit(descs, args.ur_bank, args.ur_offset,
                                      args.gr_base, args.smc_id_en)
    case_dir = os.path.join(args.sandbox, "test_atomic_ops", "multi_inst_stage", args.name)
    if os.path.exists(case_dir):
        raise SystemExit(f"[STB-GEN-GUARD] case 目录已存在：{case_dir}（先清理再生成，防混旧产物）")
    expect = materialize(case_dir, descs, args.ur_bank, args.ur_offset, args.gr_base,
                         args.smc_id_en, stb_hex, axi_hex, not args.no_hbm_init)

    print(f"[STB-GEN] case 物化完成：{case_dir}")
    print(f"  描述符 {len(descs)} 条，总行数 {expect['window']['lines']}，"
          f"窗口 [{args.gr_base:#x}, {args.gr_base + expect['window']['lines']*16:#x})")
    print(f"  iq_stb.hex {len(stb_hex)} 行 / iq_stb_axi.hex {len(axi_hex)} 行"
          f"  smc_id_en={args.smc_id_en} tid={TID}")
    print(f"  HBM 预载：{'已写 ' + HBM_INIT_DIR + '（副本留存 memory_init_snapshot/）' if not args.no_hbm_init else '跳过（--no-hbm-init）'}")
    print(f"  下一步：bash {args.sandbox}/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh "
          f"multi_inst_stage/{args.name}/smc_0 -s 0")
    print(f"  比对：python3 verification_v2/tools/hbm_window_compare.py {case_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
