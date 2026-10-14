#!/usr/bin/env python3
"""ldb_review_case_gen —— ADR-059 v2 / M3-lite 门②：LDB 复核 case 生成器。

由编译器**生产发射组件**（SMIHeader + PhysicalInstruction + LIRPacker，与门①同路径）
生成 LDB/LDBAXI 指令，并物化完整 VCS case：

    <sandbox>/test_atomic_ops/multi_inst_stage/<name>/
        smc_0/ur_data/bank0..7.hex            # 全 sentinel（未落位区必须保持才可证伪）
        smc_0/output_bank/bank0..7_final.hex  # 期望终态（窗口=src 图案，其余=sentinel）
        smc_1..7/ur_data/bank0..7.hex         # 修正②：独立 sentinel′ 显式初始值
                                              # （GEN ur_only 模式写入 data_list → TB 装载，
                                              #  B4c 先验期望，禁止首跑 dump 反钉）
        smi_iq_inst/stage_0/iq_ldb.hex        # 平坦形态（镜像 golden w033，无 embed/TERM）
        smi_iq_inst/stage_0/iq_ldb_axi.hex    # 每 burst 段一行，聚合成对
        ldb_expect.json                       # 比对脚本 ur_window_compare.py 的期望清单
        memory_init_snapshot/                 # 本 case 预载的全局 memory_init*.hex 副本（证据）

契约（全部有出处，见 ADR-059 v2 Context「修正①取证」）：
    - 形态镜像 golden w033：单条 LDB（CNT=total>1）↔ N 条 LDBAXI 的 1:N 聚合配对
      （7 月旧 RTL 证伪过该形态，但当前沙箱 RTL 2026-07-21 版已修，w033 8/8 SMC 实证）。
    - 聚合配对恒等：total == Σ(brst_i+1)（w033：33=16+9+8）；LDB 侧恒 LOOPS=0。
    - line_byte_cnt=15 / line_stride=1 照抄 w033（语义未独立理解，R3 登记）。
    - brst ∈ [1,15]；单 SMC：smc_id_en 显式 0；LDB 侧 tid=0（w033 值，与 STB 侧
      tid=2 非对称，各照抄各 golden）。
    - 改编变量（相对 w033）：en 7→0、intlv 0x10000000→0、单 stage 纯搬运 → L1 为首证。

用法：
    python3 verification_v2/tools/ldb_review_case_gen.py \
        --name compiler_ldb_review_l1 --desc lines=16,brst=15 \
        --ur-bank 1 --gr-base 0x1000
    # L2 尾 burst：--desc lines=16,brst=15 --desc lines=9,brst=8 --desc lines=8,brst=7
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
HBM_MARK_LINES = 4096        # memory_init2..8.hex 标记行数（覆盖窗口，泄漏可检出）
TID = 0                      # golden w033 LDBAXI 照抄值（与 STB 侧 tid=2 非对称）
LDB_HDR_INC = 16             # w033 LDB HDR.INC=16（LOOPS=0 惰性常数，照抄）
LINE_BYTE_CNT = 15           # w033 照抄值（≠16，语义未独立理解，ADR-059 R3）
LINE_STRIDE = 1              # w033 照抄值
MAX_DESCS = 8                # 平坦行数上界自律


# ── 确定性数据图案（生成器与比对脚本共享本模块，防双源漂移）─────────────────
def src_pattern_line(k: int) -> str:
    """HBM 源窗口第 k 行（128b hex，MSB 在前；$readmemh/dump 同表示）。"""
    return f"{0x1DB00000 + k:08x}{0x1DB10000 + k:08x}{0x1DB20000 + k:08x}{0x1DB30000 + k:08x}"


def sentinel_line(b: int, k: int) -> str:
    """smc_0 UR bank b 第 k 行 sentinel：非零、非源图案、逐字节非零
    （堵 line_byte_cnt=15 只写 15 字节的旁路——未写字节保持 sentinel 即可检出）。"""
    return f"{0x5E000000 + (b << 20) + k:08x}" * 4


def sentinel_aux_line(i: int, b: int, k: int) -> str:
    """smc_1..7 UR sentinel′（修正②独立初值）：编码 smc/bank/line，与 smc_0 互异。"""
    return f"{0xA0DE0000 + (i << 24) + (b << 16) + k:08x}" * 4


def preload_pattern_line(k: int) -> str:
    """HBM 预载底图第 k 行（窗口外必须保持的值）。"""
    return f"{0xB0A00000 + k:08x}{0xB0A10000 + k:08x}{0xB0A00000 + k:08x}{0xB0A10000 + k:08x}"


def mark_pattern_line(k: int) -> str:
    """memory_init2..8 标记行：非零、非底图（en 泄漏写他时 X→dump 0 ≠ 本图案 → 可检出）。"""
    return f"{0xEE1D0000 + k:08x}" * 4


ZERO_LINE = "0" * 32


# ── 参数守卫（响亮拒绝，配单测）────────────────────────────────────────────
def parse_desc(s: str) -> dict:
    """'lines=16,brst=15[,loops=1]' → dict。"""
    d = {}
    for kv in s.split(","):
        k, _, v = kv.partition("=")
        k, v = k.strip(), v.strip()
        if k not in ("lines", "brst", "loops") or not v:
            raise SystemExit(f"[LDB-GEN-GUARD] --desc 键非法：{kv!r}（允许 lines/brst/loops）")
        d[k] = int(v, 0)
    return d


def guard(name: str, descs: list[dict], ur_bank: int, ur_offset: int, gr_base: int,
          smc_id_en: int, intlv: int = 0) -> None:
    if not (0 <= ur_bank < UR_BANKS):
        raise SystemExit(f"[LDB-GEN-GUARD] ur_bank={ur_bank} 越界 [0,{UR_BANKS})")
    # gr_intlv_addr 字段 40bit（ldb_ur_axi_conv.v:296 [92:53]）；
    # 分片语义绑定（en=7 ⇒ intlv=通道步长）在 unified_lcs_case_gen 的
    # equal_8smc_v1 分支，此处只做字段级合法性（不绑全局 en=7 规则）。
    if not (0 <= intlv < (1 << 40)) or intlv % LINE_BYTES != 0:
        raise SystemExit(f"[LDB-GEN-GUARD] intlv={intlv:#x} 越出 40bit 字段或非 "
                         f"{LINE_BYTES}B 对齐")
    if ur_offset % LINE_BYTES != 0 or not (0 <= ur_offset < UR_BANK_DEPTH * LINE_BYTES):
        raise SystemExit(f"[LDB-GEN-GUARD] ur_offset={ur_offset} 非 16B 对齐或越界")
    if gr_base % LINE_BYTES != 0 or gr_base <= 0:
        raise SystemExit(f"[LDB-GEN-GUARD] gr_base={gr_base:#x} 必须为正且 16B 对齐"
                         f"（0 会削弱地址正确性证据）")
    if smc_id_en not in (0, 7):
        raise SystemExit(f"[LDB-GEN-GUARD] smc_id_en={smc_id_en} 未复核形态："
                         f"仅 0（M3-lite 单 SMC）或 7（equal_8smc_v1 八目标广播）"
                         f"已复核，部分使能 1..6/越界 8..31 响拒")
    if len(descs) > MAX_DESCS:
        raise SystemExit(f"[LDB-GEN-GUARD] 描述符数 {len(descs)} > {MAX_DESCS}（平坦行数上界自律）")
    total = 0
    for i, d in enumerate(descs):
        lines, brst, loops = d.get("lines"), d.get("brst"), d.get("loops", 0)
        if lines is None or brst is None:
            raise SystemExit(f"[LDB-GEN-GUARD] desc{i} 缺 lines/brst：{d}")
        if brst == 0:
            raise SystemExit(f"[LDB-GEN-GUARD] desc{i} brst=0 响拒（门②范围不需要；"
                             f"DEBT-050-LDBAXI-BRST0 旧 RTL 死锁禁区，保守起见仍禁）")
        if not (1 <= brst <= 15):
            raise SystemExit(f"[LDB-GEN-GUARD] desc{i} brst={brst} 越界 [1,15]")
        if not (1 <= lines <= 255):
            raise SystemExit(f"[LDB-GEN-GUARD] desc{i} lines={lines} 越界 [1,255]（8b 字段）")
        if lines != (loops + 1) * (brst + 1):
            raise SystemExit(f"[LDB-GEN-GUARD] desc{i} 配对恒等式不成立：lines={lines} != "
                             f"(loops={loops}+1)*(brst={brst}+1)={(loops+1)*(brst+1)}")
        total += lines
    if total > 255:
        raise SystemExit(f"[LDB-GEN-GUARD] 聚合 total={total} > 255（ur_wr_line_cnt 8b 上限）")
    ur_line0 = ur_offset // LINE_BYTES
    if ur_line0 + total > UR_BANK_DEPTH:
        raise SystemExit(f"[LDB-GEN-GUARD] UR 写越界：起始行 {ur_line0} + 总行数 {total} "
                         f"> {UR_BANK_DEPTH}")
    if gr_base + total * LINE_BYTES > HBM_PRELOAD_LINES * LINE_BYTES:
        raise SystemExit(f"[LDB-GEN-GUARD] HBM 窗口越出预载区：gr_base={gr_base:#x} + "
                         f"{total} 行 > {HBM_PRELOAD_LINES} 行预载（非目标区证据依赖预载）")


# ── 指令发射 ────────────────────────────────────────────────────────────────
def build_and_emit(descs: list[dict], ur_bank: int, ur_offset: int, gr_base: int,
                   smc_id_en: int, intlv: int = 0) -> tuple[list[str], list[str]]:
    """单条 LDB（CNT=total）+ 每描述符一条 LDBAXI，经真 LIRPacker 发射。

    平坦形态（镜像 golden w033）：无 INIT/embed/TERM，全 LOOPS=0（loops>0 时仅
    AXI 侧携带，与门① STB 同型）；LDB HDR.INC=16 惰性常数照抄 w033。
    """
    total = sum(d["lines"] for d in descs)
    packer = LIRPacker(config_dir="hw_config", verbosity=0)
    ldb_instrs, axi_instrs = [], []
    ldb_instrs.append(PhysicalInstruction(
        iq_name="ldb", abs_t=0, stage="BODY",
        header=SMIHeader(**{"inc": LDB_HDR_INC, "vld": 1}),
        payload={"VALID": 1, "ur_addr": (ur_bank << UR_BANK_SHIFT) | ur_offset,
                 "ur_wr_line_cnt": total, "line_byte_cnt": LINE_BYTE_CNT,
                 "line_stride": LINE_STRIDE, "const": 0, "pre_fill": 0,
                 "post_fill": 0, "inst_id": 0, "smc_id_en": smc_id_en}))
    axi_addr = gr_base
    for d in descs:
        lines, brst, loops = d["lines"], d["brst"], d.get("loops", 0)
        axi_instrs.append(PhysicalInstruction(
            iq_name="ldb-axi", abs_t=len(axi_instrs), stage="BODY",
            header=SMIHeader(**{"inc": (brst + 1) * LINE_BYTES, "vld": 1,
                                **({"loops": loops} if loops else {})}),
            payload={"VALID": 1, "first_byte_strb": 0, "last_byte_strb": 0, "brst": brst,
                     "gr_base_addr": axi_addr, "gr_intlv_addr": intlv, "inst_id": 0,
                     "smc_id_en": smc_id_en, "tid": TID}))
        axi_addr += lines * LINE_BYTES

    tmp = tempfile.mkdtemp(prefix="ldb_review_emit_")
    try:
        packer.emit_from_instructions(ldb_instrs + axi_instrs, tmp, smc_num=1)
        out = {}
        for fn, key in (("iq_ldb.hex", "ldb"), ("iq_ldb-axi.hex", "ldb_axi")):
            p = os.path.join(tmp, "smc_0", fn)
            if not os.path.exists(p):
                raise RuntimeError(f"[LDB-GEN] emitter 未产出 {fn}（{os.listdir(os.path.join(tmp, 'smc_0'))}）")
            with open(p, encoding="utf-8") as f:
                out[key] = [l.strip() for l in f if l.strip()]
        return out["ldb"], out["ldb_axi"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── case 物化 ───────────────────────────────────────────────────────────────
def write_hex_lines(path: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def materialize(case_dir: str, descs: list[dict], ur_bank: int, ur_offset: int,
                gr_base: int, smc_id_en: int, ldb_hex: list[str], axi_hex: list[str],
                write_hbm_init: bool) -> dict:
    total = sum(d["lines"] for d in descs)
    ur_line0 = ur_offset // LINE_BYTES

    # smc_0 UR：全 8 bank 全 sentinel；期望终态 = 窗口 src 图案 + 其余 sentinel
    for b in range(UR_BANKS):
        rows = [sentinel_line(b, k) for k in range(UR_BANK_DEPTH)]
        write_hex_lines(os.path.join(case_dir, "smc_0", "ur_data", f"bank{b}.hex"), rows)
        final = list(rows)
        if b == ur_bank:
            for k in range(total):
                final[ur_line0 + k] = src_pattern_line(k)
        write_hex_lines(os.path.join(case_dir, "smc_0", "output_bank", f"bank{b}_final.hex"),
                        final)

    # smc_1..7 UR：修正②——独立确定的 sentinel′ 初值（GEN ur_only → data_list → TB 装载）
    for i in range(1, 8):
        for b in range(UR_BANKS):
            rows = [sentinel_aux_line(i, b, k) for k in range(UR_BANK_DEPTH)]
            write_hex_lines(os.path.join(case_dir, f"smc_{i}", "ur_data", f"bank{b}.hex"),
                            rows)

    # IQ hex
    write_hex_lines(os.path.join(case_dir, "smi_iq_inst", "stage_0", "iq_ldb.hex"), ldb_hex)
    write_hex_lines(os.path.join(case_dir, "smi_iq_inst", "stage_0", "iq_ldb_axi.hex"), axi_hex)

    # HBM 预载：memory_init1 = 底图 + 窗口 src 图案；2..8 = 泄漏检测标记。副本留存 case。
    snap = os.path.join(case_dir, "memory_init_snapshot")
    os.makedirs(snap, exist_ok=True)
    win0 = gr_base // LINE_BYTES
    pre1 = [preload_pattern_line(k) for k in range(HBM_PRELOAD_LINES)]
    for k in range(total):
        pre1[win0 + k] = src_pattern_line(k)
    mark = [mark_pattern_line(k) for k in range(HBM_MARK_LINES)]
    if write_hbm_init:
        write_hex_lines(os.path.join(HBM_INIT_DIR, "memory_init1.hex"), pre1)
        for i in range(2, 9):
            write_hex_lines(os.path.join(HBM_INIT_DIR, f"memory_init{i}.hex"), mark)
    write_hex_lines(os.path.join(snap, "memory_init1.hex"), pre1)
    for i in range(2, 9):
        write_hex_lines(os.path.join(snap, f"memory_init{i}.hex"), mark)

    expect = {
        "schema": "m3lite.ldb_review_expect/v1",
        "case": os.path.basename(case_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_smc": 0, "smc_count": 8,
        "ur_window": {"bank": ur_bank, "byte_offset": ur_offset, "lines": total},
        "hbm_source": {"byte_base": gr_base, "lines": total},
        "descriptors": descs,
        "smc_id_en": smc_id_en, "tid": TID,
        "line_byte_cnt": LINE_BYTE_CNT, "line_stride": LINE_STRIDE,
        "preload": {"lines": HBM_PRELOAD_LINES, "pattern": "preload_pattern_line"},
        "mark": {"lines": HBM_MARK_LINES, "pattern": "mark_pattern_line"},
        "instructions": {"iq_ldb": ldb_hex, "iq_ldb_axi": axi_hex},
        "hbm_init_dir": HBM_INIT_DIR,
        "hbm_init_written": write_hbm_init,
    }
    with open(os.path.join(case_dir, "ldb_expect.json"), "w", encoding="utf-8") as f:
        json.dump(expect, f, indent=2, ensure_ascii=False)
    return expect


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="case 名（multi_inst_stage/<name>）")
    ap.add_argument("--desc", action="append", required=True,
                    help="描述符：lines=N,brst=M[,loops=K]；可重复（聚合 1:N 平坦流）")
    ap.add_argument("--ur-bank", type=int, required=True)
    ap.add_argument("--ur-offset", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--gr-base", type=lambda s: int(s, 0), required=True)
    ap.add_argument("--smc-id-en", type=int, default=0)
    ap.add_argument("--sandbox", default=os.path.expanduser("~/u280_sim_v422"))
    ap.add_argument("--no-hbm-init", action="store_true", help="只生成 case，不写全局 memory_init")
    args = ap.parse_args()

    # 2026-10-10 沙箱化（F6 全树沙箱裁定步③）：全局 HBM 预载目录跟随
    # --sandbox，不再恒定写原树（原树只读纪律；默认行为不变）。
    global HBM_INIT_DIR
    HBM_INIT_DIR = os.path.join(args.sandbox, "data_engine", "sram")

    descs = [parse_desc(s) for s in args.desc]
    guard(args.name, descs, args.ur_bank, args.ur_offset, args.gr_base, args.smc_id_en)

    ldb_hex, axi_hex = build_and_emit(descs, args.ur_bank, args.ur_offset,
                                      args.gr_base, args.smc_id_en)
    case_dir = os.path.join(args.sandbox, "test_atomic_ops", "multi_inst_stage", args.name)
    if os.path.exists(case_dir):
        raise SystemExit(f"[LDB-GEN-GUARD] case 目录已存在：{case_dir}（先清理再生成，防混旧产物）")
    expect = materialize(case_dir, descs, args.ur_bank, args.ur_offset, args.gr_base,
                         args.smc_id_en, ldb_hex, axi_hex, not args.no_hbm_init)

    print(f"[LDB-GEN] case 物化完成：{case_dir}")
    print(f"  LDB 1 行 CNT={expect['ur_window']['lines']} ↔ LDBAXI {len(axi_hex)} 描述符"
          f"（聚合恒等 {expect['ur_window']['lines']}==Σ(brst+1) 已守卫）")
    print(f"  窗口 bank{args.ur_bank} off {args.ur_offset:#x} ← HBM [{args.gr_base:#x}, "
          f"{args.gr_base + expect['ur_window']['lines']*16:#x})")
    print(f"  smc_id_en={args.smc_id_en} tid={TID} line_byte_cnt={LINE_BYTE_CNT} stride={LINE_STRIDE}")
    print(f"  smc_1..7 独立 sentinel′ 已物化（修正②）")
    print(f"  HBM 预载：{'已写 ' + HBM_INIT_DIR + '（副本留存 memory_init_snapshot/）' if not args.no_hbm_init else '跳过（--no-hbm-init）'}")
    print(f"  下一步：bash {args.sandbox}/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh "
          f"multi_inst_stage/{args.name}/smc_0 -s 0")
    print(f"  比对：python3 verification_v2/tools/ur_window_compare.py {case_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
