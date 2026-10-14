#!/usr/bin/env python3
"""unified_lcs_case_gen —— ADR-061：scale_sum 统一入口的完整程序 case 装配器。

把一个**公式编译器链构建产物**（build_copy：ADR-056 单段或 ADR-057 多段）
装配成 VCS 可执行的完整程序 case：

    smi_iq_inst/stage_0/            LOAD（LDB/LDBAXI，布局驱动自动生成）
    smi_iq_inst/stage_1 .. stage_K  COMPUTE_k（编译器段产物**原样拷贝**，零改动）
    smi_iq_inst/stage_{K+1}/        STORE（STB/STBAXI，布局驱动自动生成）
    smc_0/ur_data/bank*.hex         LOAD 服务 bank = 行索引哨兵（LOAD 承重纪律），
                                    输出 bank = 全零预填（核验 build 预载本即全零），
                                    其余 bank = build 预载逐字节原样
    smc_0/output_bank/bank*_final   LOAD bank：哨兵底 + 窗口=HBM 源数据（=build 预载
                                    窗口）；输出 bank：build golden 原样（核验尾部
                                    全零）；其余：build 终态原样
    smc_{1..7}/ur_data/             辅助哨兵（门③ lineage）
    memory_init_snapshot/           HBM 镜像冻结副本（restore 唯一来源）
    g3_expect.json                  屏障参数 + hbm_preload.files 哈希锚
    program_manifest.json           adr061_program_manifest_v1（D5）

布局权威 = sim_config.tensors[]（D1）。响亮拒绝（D8）一律 SystemExit
[ULCS-GEN-GUARD]——不半截物化、不静默回落。

HBM 布局：LOAD 张量按 sim_config 声明序各占一个 4KB 区，基址 0x1000*(i+1)
（g2/s1 守卫要求 gr_base>0）；输出张量随后。全部区两两不相交、16B 对齐、
落在 memory_init1 预载范围内。UR 字节约定 bank_k line0 = k*4096（门③实测）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, "src"))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import ldb_review_case_gen as g2  # noqa: E402  门②发射器/图案/守卫（VCS 实证形态）
import stb_solo_case_gen as s1    # noqa: E402  门①发射器（VCS 实证形态）

UR_BANKS = g2.UR_BANKS               # 8
UR_BANK_DEPTH = g2.UR_BANK_DEPTH     # 256 行
LINE_BYTES = g2.LINE_BYTES           # 16
HBM_REGION_BYTES = 4096              # 每张量一个 4KB 对齐区
HBM_FIRST_BASE = 0x1000              # gr_base>0 守卫（g2/s1）
MAX_BURST_LINES = 16                 # brst<=15 → 单描述符 <=16 行
BARRIER_TIMEOUT = 5000               # 与门③同款缺省（超时即 RED，不调大消红）

PROGRAM_MANIFEST_SCHEMA = "adr061_program_manifest_v1"
PROGRAM_MANIFEST_SCHEMA_V2 = "ulcs_program_manifest_v2_8smc"
PARTITION_SCHEMES = ("equal_8smc",)  # 规格：docs/8smc_equal_partition_spec_20261014.md
SMC_COUNT = 8                        # PARAM_SMC_NUM（hw_params.yaml）
EXPECT_SCHEMA = "adr061.unified_expect/v1"

# HBM 通道地址窗跨度 256MB：atcbmc300 交叉开关按地址译码选通道
# （data_engine/noc/rtl/include/atcbmc300_config.vh:172-201：
# SLV2..SLV9 = HBM ch0..ch7，base=0x0/0x1000_0000/…/0x7000_0000，各 256MB）。
# per-SMC 基址公式 addr_k = gr_base + k*gr_intlv_addr（RTL 行级：
# ldb_ur_axi_conv.v:392-425 / stb_ur_axi_conv.v:360-377）——
# 广播指令 + intlv=本常量 ⇒ SMC k 落通道 k 同偏移窗（equal_8smc_v1 原生形态；
# 2026-10-14 首跑 intlv=0 → 8 master 同址全路由 ch0 的 RED 归因）。
CHANNEL_STRIDE = 0x10000000

LOAD_ROLES = ("input", "param", "constant")
OUTPUT_ROLE = "output"


def _die(msg: str) -> None:
    raise SystemExit(f"[ULCS-GEN-GUARD] {msg}")


def _partition_transport(partition: str | None) -> tuple[int, int]:
    """分片模式的传输参数绑定（en, intlv）。

    equal_8smc_v1 ⇒ en=7（8 目标广播）且 intlv=CHANNEL_STRIDE（通道跨度），
    二者同时成立才构成已复核分片形态。注意绑定不反向扩散：本函数不约束
    其他 en=7 用途的步长（其他合法布局可不同——守卫绑分片模式，不绑所有
    en=7 指令）。
    """
    if partition is None:
        return 0, 0
    if partition == "equal_8smc":
        return SMC_COUNT - 1, CHANNEL_STRIDE
    _die(f"partition={partition!r} 未知（已定义: {PARTITION_SCHEMES}），响亮拒绝")


def _check_v2_axi_windows(axi_lines: list[str], unit: str, intlv: int) -> None:
    """equal_8smc_v1 地址窗核验——**解码最终产物**，非发射参数重述。

    每条 AXI 指令：①GR_INTLV_ADDR 无截断等于通道步长；②逐 SMC 基址
    addr_k = gr_base + k*intlv 与其整段 burst 末地址全部落入通道 k 窗
    [k*stride, (k+1)*stride)（不跨窗；窗顶 2^31 << 2^40，溢出被窗检查吸收）。
    """
    import iq_field_diff as IFD  # 局部 import：仅 v2 路径需要
    mods = IFD.load_modules_yaml(os.path.join(ROOT, "hw_config", "modules.yaml"))
    for idx, row in enumerate(axi_lines):
        d = IFD.decode(row, unit, mods)
        body, hdr = d["body"], d["header"]
        if body["GR_INTLV_ADDR"] != intlv:
            _die(f"{unit} 行{idx} GR_INTLV_ADDR={body['GR_INTLV_ADDR']:#x} != "
                 f"{intlv:#x}（字段截断或发射失实，响亮拒绝）")
        base, brst = body["GR_BASE_ADDR"], body["BRST"]
        burst = (brst + 1) * LINE_BYTES * (hdr.get("LOOPS", 0) + 1)
        for k in range(SMC_COUNT):
            a0, a1 = base + k * intlv, base + k * intlv + burst
            lo, hi = k * CHANNEL_STRIDE, (k + 1) * CHANNEL_STRIDE
            if not (lo <= a0 and a1 <= hi):
                _die(f"{unit} 行{idx} SMC{k} 地址窗越界：[{a0:#x},{a1:#x}) "
                     f"未落入通道{k} 窗 [{lo:#x},{hi:#x})（跨窗/溢出，响亮拒绝）")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(p: Path) -> str:
    return _sha256_bytes(Path(p).read_bytes())


def _read_hex_lines(p: Path) -> list[str]:
    return [ln.strip() for ln in Path(p).read_text(encoding="utf-8").splitlines()
            if ln.strip()]


# ----------------------------------------------------------------------
# D8 布局解析与守卫
# ----------------------------------------------------------------------
def parse_layout(build_copy: Path, n: int) -> dict:
    """sim_config.tensors[] → 布局计划。任一越出 D1 形态即响亮拒绝。"""
    cfg_path = Path(build_copy) / "sim_config.json"
    if not cfg_path.is_file():
        _die(f"缺 sim_config.json: {cfg_path}")
    sim_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    tensors = sim_cfg.get("tensors")
    if not isinstance(tensors, list) or not tensors:
        _die("sim_config.tensors 缺失或为空——非 D1 布局形态")
    if n % 4 != 0:
        _die(f"n={n} 非 4 的倍数（ragged tail 超范围，响亮拒绝）")
    n_lines = n // 4

    loads, outputs = [], []
    seen_banks: dict[int, str] = {}
    for t in tensors:
        name, bank, role = t.get("name"), t.get("bank"), t.get("role")
        addr, shape = t.get("addr"), t.get("shape")
        if not isinstance(name, str) or not isinstance(bank, int) \
                or not isinstance(role, str):
            _die(f"张量项缺 name/bank/role: {t}")
        if addr != 0:
            _die(f"张量 {name} addr={addr} != 0（D1 仅覆盖 addr=0 起始形态）")
        if not (isinstance(shape, list) and len(shape) == 2
                and shape[0] == n_lines and shape[1] == 4):
            _die(f"张量 {name} shape={shape} != [{n_lines},4]（n={n} 推导）")
        if not (0 <= bank < UR_BANKS):
            _die(f"张量 {name} bank={bank} 越界 [0,{UR_BANKS})")
        if bank in seen_banks:
            _die(f"张量 bank 窗交叠：{name} 与 {seen_banks[bank]} 同占 bank{bank}")
        seen_banks[bank] = name
        if role in LOAD_ROLES:
            loads.append(t)
        elif role == OUTPUT_ROLE:
            outputs.append(t)
        else:
            _die(f"张量 {name} role={role!r} 非 {{input,param,constant,output}}")
    if len(outputs) != 1:
        _die(f"输出张量数 {len(outputs)} != 1（V1 单输出式）")
    if not loads:
        _die("无 LOAD 服务张量（input/param/constant 全缺）")
    if n_lines < 2:
        _die(f"n_lines={n_lines} < 2：单描述符 brst=0 是 RTL 死锁禁区"
             f"（DEBT-050-LDBAXI-BRST0），响亮拒绝")
    if n_lines > 255:
        _die(f"n_lines={n_lines} > 255（LDB ur_wr_line_cnt 8b 上限）")
    if (len(loads) + 2) * HBM_REGION_BYTES > g2.HBM_PRELOAD_LINES * LINE_BYTES:
        _die(f"HBM 区越出预载容量：{len(loads)} 个 LOAD 张量 + 输出 + 前导哨兵区")

    # HBM 基址分配（声明序确定）：LOAD 张量 0x1000*(i+1)，输出随后
    hbm_layout = []
    for i, t in enumerate(loads):
        hbm_layout.append({
            "name": t["name"], "role": t["role"], "bank": t["bank"],
            "ur_line0": 0, "lines": n_lines,
            "byte_base": HBM_FIRST_BASE + i * HBM_REGION_BYTES,
            "bytes": n_lines * LINE_BYTES, "direction": "h2d"})
    out = outputs[0]
    hbm_layout.append({
        "name": out["name"], "role": OUTPUT_ROLE, "bank": out["bank"],
        "ur_line0": 0, "lines": n_lines,
        "byte_base": HBM_FIRST_BASE + len(loads) * HBM_REGION_BYTES,
        "bytes": n_lines * LINE_BYTES, "direction": "d2h"})
    # 两两不相交（构造上不相交，此处正向核验——缺席断言须正面取证）
    spans = sorted((e["byte_base"], e["byte_base"] + HBM_REGION_BYTES, e["name"])
                   for e in hbm_layout)
    for (a0, a1, na), (b0, _b1, nb) in zip(spans, spans[1:]):
        if b0 < a1:
            _die(f"HBM 区交叠：{na} [{a0:#x},{a1:#x}) vs {nb} @{b0:#x}")

    # 段计划：多段（ADR-057）或单段（ADR-056）
    stages_decl = sim_cfg.get("stages")
    if stages_decl:
        segs = [{"stage_id": int(s["stage_id"]), "start": int(s["start"]),
                 "m": int(s["m"])} for s in stages_decl]
        segs.sort(key=lambda s: s["stage_id"])
        if [s["stage_id"] for s in segs] != list(range(len(segs))):
            _die(f"sim_config.stages 段号不连续: {[s['stage_id'] for s in segs]}")
        if segs[0]["start"] != 0:
            _die("首段 start != 0——覆盖链不从 0 起")
        for a, b in zip(segs, segs[1:]):
            if b["start"] != a["start"] + a["m"]:
                _die(f"段间覆盖断裂: stage_{a['stage_id']} end="
                     f"{a['start'] + a['m']} != stage_{b['stage_id']} start={b['start']}")
        if segs[-1]["start"] + segs[-1]["m"] != n_lines:
            _die(f"段覆盖终点 {segs[-1]['start'] + segs[-1]['m']} != n_lines={n_lines}")
    else:
        segs = [{"stage_id": 0, "start": 0, "m": n_lines}]

    return {"sim_cfg": sim_cfg, "sim_cfg_path": cfg_path, "n_lines": n_lines,
            "loads": loads, "output": out, "hbm_layout": hbm_layout,
            "segments": segs, "multi": bool(stages_decl)}


def chunk_descs(n_lines: int) -> list[dict]:
    """n_lines → 描述符列（单描述符 ≤16 行，brst=lines-1∈[1,15]）。"""
    descs = []
    rem = n_lines
    while rem > MAX_BURST_LINES:
        descs.append({"lines": MAX_BURST_LINES, "brst": MAX_BURST_LINES - 1})
        rem -= MAX_BURST_LINES
    if rem == 1:
        _die("描述符尾块 lines=1 → brst=0 禁区（DEBT-050-LDBAXI-BRST0），响亮拒绝")
    if rem:
        descs.append({"lines": rem, "brst": rem - 1})
    return descs


# ----------------------------------------------------------------------
# equal_8smc_v1 分片数据面（规格：docs/8smc_equal_partition_spec_20261014.md）
# ----------------------------------------------------------------------
def _lanes_of(ln: str) -> list[int]:
    v = int(ln, 16)
    return [(v >> (32 * i)) & 0xFFFFFFFF for i in range(4)]


def _pack_lanes(lanes) -> str:
    v = 0
    for i, w in enumerate(lanes):
        v |= (int(w) & 0xFFFFFFFF) << (32 * i)
    return f"{v:032x}"


def _derive_slices(load_src_lines: dict, loads: list, n_lines: int) -> dict:
    """slice 0 = build 数据原样；slice k>=1 = 逐 input 张量确定性仿射派生
    （fp32 语义：v*(1+0.5k+0.25i) + (±0.125k)，i=LOAD 声明序张量下标，
    奇张量取负偏置）。param/constant 全通道同源。可区分性由 golden 两两
    不全等的构造后检查兜底（M4），派生式本身不作可区分性证明。"""
    import numpy as np
    channels = {0: {b: list(ls) for b, ls in load_src_lines.items()}}
    for k in range(1, SMC_COUNT):
        ch = {}
        for i, t in enumerate(loads):
            bank = t["bank"]
            if t["role"] != "input":
                ch[bank] = list(load_src_lines[bank])
                continue
            vals: list[int] = []
            for ln in load_src_lines[bank]:
                vals.extend(_lanes_of(ln))
            arr = np.asarray(vals, dtype=np.uint32).view(np.float32)
            a = np.float32(1.0 + 0.5 * k + 0.25 * i)
            b = np.float32(0.125 * k if i % 2 == 0 else -0.125 * k)
            out = (arr * a + b).astype(np.float32).view(np.uint32)
            ch[bank] = [_pack_lanes(out[4 * r:4 * r + 4])
                        for r in range(n_lines)]
        channels[k] = ch
    return channels


def _reference_window(formula: str, hbm_layout: list,
                      channel_region: dict, n_lines: int) -> list[str]:
    """公式有序 AST fp32 独立参考（与 unified_lcs_compare C-H 同 machinery）：
    输入按 role 从通道 HBM 区重建（input=全行流；param=stride0 标量驻留，
    形态正面取证），返回 golden 输出窗 hex 行（n_lines 行）。"""
    import numpy as np
    from verification_v2.reference.ordered_ast import parse_ordered_formula
    from verification_v2.reference import reference_generator as rg
    load_entries = [e for e in hbm_layout if e["role"] in ("input", "param")]
    reserved = [e["name"] for e in hbm_layout
                if e["role"] in ("output", "constant")]
    root = parse_ordered_formula(formula, [e["name"] for e in load_entries],
                                 reserved)
    inputs: dict = {}
    for e in load_entries:
        lines = channel_region[e["name"]]
        vals: list[int] = []
        for ln in lines:
            vals.extend(_lanes_of(ln))
        arr = np.asarray(vals, dtype=np.uint32).view(np.float32)
        if e["role"] == "input":
            inputs[e["name"]] = arr
        else:
            if any(v != 0 for v in arr[4:]):
                _die(f"param {e['name']} line1.. 非零——非 stride0 驻留形态")
            lanes0 = arr[:4].tolist()
            if len(set(lanes0)) != 1:
                _die(f"param {e['name']} line0 四 lane 不全等——非标量驻留形态")
            inputs[e["name"]] = np.asarray([lanes0[0]], dtype=np.float32)
    ev = rg.evaluate(root, inputs)
    y = np.asarray(ev.y, dtype=np.float32).reshape(-1)
    if y.size != 4 * n_lines:
        _die(f"参考求值元素数 {y.size} != 4*n_lines={4 * n_lines}")
    y_u = y.view(np.uint32)
    return [_pack_lanes(y_u[4 * k:4 * k + 4]) for k in range(n_lines)]


# ----------------------------------------------------------------------
# 装配主流程
# ----------------------------------------------------------------------
def build_unified_case(build_copy: Path, case_dir: Path, n: int,
                       tb_path: Path, *, op_name: str, formula: str,
                       generated_at: str | None = None,
                       partition: str | None = None) -> dict:
    build_copy = Path(build_copy)
    case_dir = Path(case_dir)
    if not isinstance(op_name, str) or not op_name:
        _die("op_name 缺失——manifest 须如实记录算子身份（禁硬编码）")
    if not isinstance(formula, str) or not formula.strip():
        _die("formula 缺失——manifest 须自包含记录公式串（C-H 独立参考之源）")
    if case_dir.exists():
        _die(f"case 目录已存在: {case_dir}（拒绝沿用/覆盖旧产物，先人工清理）")
    is8 = partition is not None
    if is8 and partition not in PARTITION_SCHEMES:
        _die(f"partition={partition!r} 未知（已定义: {PARTITION_SCHEMES}），响亮拒绝")
    lay = parse_layout(build_copy, n)
    n_lines, segs = lay["n_lines"], lay["segments"]
    K = len(segs)
    descs = chunk_descs(n_lines)
    loads, out_t = lay["loads"], lay["output"]
    hbm_layout = lay["hbm_layout"]
    load_banks = {t["bank"] for t in loads}
    out_bank = out_t["bank"]
    out_base = next(e["byte_base"] for e in hbm_layout if e["role"] == OUTPUT_ROLE)

    # ---- COMPUTE 产物存在性核验（先全验后落盘——不半截物化） ----
    seg_iq: list[list[Path]] = []
    for s in segs:
        src = (build_copy / "stages" / f"stage_{s['stage_id']}" / "smc_0") \
            if lay["multi"] else (build_copy / "smc_0")
        iqs = sorted(src.glob("iq_*.hex"))
        if not iqs:
            _die(f"COMPUTE 段 {s['stage_id']} 无 iq_*.hex: {src}")
        seg_iq.append(iqs)
    ur_src = (build_copy / "stages" / "stage_0" / "smc_0" / "ur_data") \
        if lay["multi"] else (build_copy / "smc_0" / "ur_data")
    ob_src = build_copy / "smc_0" / "output_bank"
    for b in range(UR_BANKS):
        if not (ur_src / f"bank{b}.hex").is_file():
            _die(f"build 预载缺 bank{b}: {ur_src}")
        if not (ob_src / f"bank{b}_final.hex").is_file():
            _die(f"build 终态缺 bank{b}_final: {ob_src}")

    # 输出 bank：build 预载必须本即全零（零预填语义的正面取证，非假设）；
    # build 终态尾部 [n_lines..256) 必须全零（COMPUTE 不写窗外——写区声明的
    # 同源核验），且 golden 窗口行全非零（与零预填可区分）。
    out_pre = _read_hex_lines(ur_src / f"bank{out_bank}.hex")
    out_fin = _read_hex_lines(ob_src / f"bank{out_bank}_final.hex")
    if len(out_pre) != UR_BANK_DEPTH or len(out_fin) != UR_BANK_DEPTH:
        _die(f"输出 bank 行数 != {UR_BANK_DEPTH}（预载 {len(out_pre)}/终态 {len(out_fin)}）")
    norm_pre = [ln.lower().removeprefix("0x") for ln in out_pre]
    norm_fin = [ln.lower().removeprefix("0x") for ln in out_fin]
    if any(int(ln, 16) != 0 for ln in norm_pre):
        _die(f"输出 bank{out_bank} build 预载非全零——零预填形态不成立，拒绝")
    if any(int(ln, 16) != 0 for ln in norm_fin[n_lines:]):
        _die(f"输出 bank{out_bank} build 终态窗口外 [行{n_lines}..) 存在非零行"
             f"——COMPUTE 写区越出声明窗，拒绝")
    if any(int(ln, 16) == 0 for ln in norm_fin[:n_lines]):
        _die(f"输出 golden 窗口 [0,{n_lines}) 存在全零行——与零预填不可区分，拒绝")

    # LOAD 源数据：HBM 区内容 = build 预载 bank 窗口行**逐字节原样**
    # （门③实证 HBM→UR 逐字节送达形态； LOAD 后 UR 窗 == build 预载窗 ==
    # 编译器 python sim 所见初值，数值语义闭环）。
    load_src_lines: dict[int, list[str]] = {}
    for t in loads:
        bl = _read_hex_lines(ur_src / f"bank{t['bank']}.hex")
        if len(bl) != UR_BANK_DEPTH:
            _die(f"bank{t['bank']} 预载行数 {len(bl)} != {UR_BANK_DEPTH}")
        load_src_lines[t["bank"]] = [ln.lower().removeprefix("0x")
                                     for ln in bl[:n_lines]]

    # ---- 指令发射（真 LIRPacker，门①/门② VCS 实证形态） ----
    # 分片传输参数绑定：equal_8smc_v1 ⇒ en=7 且 intlv=通道步长（A′ 修复——
    # 首跑 intlv=0 致 8 master 同址全路由 channel0；守卫已复核 {0,7}）
    en, intlv = _partition_transport(partition)
    ldb_lines_all: list[str] = []
    ldbaxi_lines_all: list[str] = []
    for t in loads:
        base = next(e["byte_base"] for e in hbm_layout if e["name"] == t["name"])
        g2.guard("unified-ldb", descs, t["bank"], 0, base, en, intlv)
        ldb_ls, axi_ls = g2.build_and_emit(descs, t["bank"], 0, base, en, intlv)
        ldb_lines_all += ldb_ls
        ldbaxi_lines_all += axi_ls
    s1.guard("unified-stb", descs, out_bank, 0, out_base, en, intlv)
    stb_lines, stbaxi_lines = s1.build_and_emit(descs, out_bank, 0, out_base,
                                                en, intlv)
    if is8:
        # 解码最终产物逐 SMC 核验地址窗（字段无截断 + 不跨通道窗）
        _check_v2_axi_windows(ldbaxi_lines_all, "LDB_AXI", intlv)
        _check_v2_axi_windows(stbaxi_lines, "STB_AXI", intlv)

    # ---- 物化 ----
    stage_dirs: list[tuple[int, str, list[tuple[str, list[str] | Path]]]] = []
    stage_dirs.append((0, "LOAD", [("iq_ldb.hex", ldb_lines_all),
                                   ("iq_ldb_axi.hex", ldbaxi_lines_all)]))
    for i, s in enumerate(segs):
        stage_dirs.append((1 + i, "COMPUTE",
                           [(p.name, p) for p in seg_iq[i]]))
    stage_dirs.append((K + 1, "STORE", [("iq_stb.hex", stb_lines),
                                        ("iq_stb_axi.hex", stbaxi_lines)]))

    manifest_stages = []
    for sid, kind, files in stage_dirs:
        sdir = case_dir / "smi_iq_inst" / f"stage_{sid}"
        sdir.mkdir(parents=True, exist_ok=False)
        fmeta = []
        for fn, payload in files:
            dst = sdir / fn
            if isinstance(payload, Path):
                shutil.copy2(payload, dst)
            else:
                g2.write_hex_lines(str(dst), payload)
            fmeta.append({"file": fn, "lines": len(_read_hex_lines(dst)),
                          "sha256": _sha256_file(dst)})
        entry: dict = {"stage_id": sid, "kind": kind,
                       "predecessor": (sid - 1) if sid > 0 else None,
                       "iq_files": fmeta}
        if is8:
            entry["active_smcs"] = list(range(SMC_COUNT))
        else:
            entry["active_smc"] = 0
        if kind == "LOAD":
            entry["covers"] = [
                {"tensor": t["name"], "bank": t["bank"], "line0": 0,
                 "lines": n_lines,
                 "hbm_byte_base": next(e["byte_base"] for e in hbm_layout
                                       if e["name"] == t["name"])}
                for t in loads]
            entry["expected_ur_write_windows"] = [
                {"bank": t["bank"], "line0": 0, "lines": n_lines} for t in loads]
            entry["expected_queue_ids"] = [26, 27]
            entry["completion_contract_id"] = "adr061.load.window_ack+tb_credit_barrier"
        elif kind == "COMPUTE":
            seg = segs[sid - 1]
            entry["iteration_range"] = [seg["start"], seg["start"] + seg["m"]]
            entry["adr057_stage_id"] = seg["stage_id"]
            entry["expected_ur_write_windows"] = [
                {"bank": out_bank, "line0": seg["start"], "lines": seg["m"]}]
            entry["expected_queue_ids"] = "per adr057 expected-events file"
            entry["completion_contract_id"] = "adr057.compute.per_cycle_ack"
            entry["expected_events_file"] = \
                f"expected_events_stage_{seg['stage_id']}.json" if lay["multi"] \
                else "expected_events.json"
        else:
            entry["covers"] = [
                {"tensor": out_t["name"], "bank": out_bank, "line0": 0,
                 "lines": n_lines, "hbm_byte_base": out_base}]
            entry["expected_ur_write_windows"] = []
            entry["expected_hbm_write_window"] = {
                "byte_base": out_base, "line0": out_base // LINE_BYTES,
                "lines": n_lines}
            entry["expected_queue_ids"] = [24, 25]
            entry["completion_contract_id"] = \
                "adr061.store.tb_barrier(wbarrier+s4)+fixed_wait(实验)"
        manifest_stages.append(entry)

    # equal_8smc_v1 数据面：切片推导 + 逐通道 golden（v1 恒等：仅 smc_0）
    if is8:
        channels = _derive_slices(load_src_lines, loads, n_lines)
        gold_out: dict[int, list[str]] = {0: list(norm_fin)}
        for k in range(1, SMC_COUNT):
            ch_region = {e["name"]: channels[k][e["bank"]]
                         for e in hbm_layout if e["direction"] == "h2d"}
            win = _reference_window(formula, hbm_layout, ch_region, n_lines)
            if any(int(ln, 16) == 0 for ln in win):
                _die(f"smc_{k} 参考 golden 窗存在全零行——与零预填不可区分，拒绝")
            gold_out[k] = win + [g2.ZERO_LINE] * (UR_BANK_DEPTH - n_lines)
        # M4 构造自检：8 份 golden 输出窗两两不全等（可区分性硬要求，
        # 不满足即配置无效——拒收而非降级；防 8 路复制假绿）
        for i in range(SMC_COUNT):
            for j in range(i + 1, SMC_COUNT):
                if gold_out[i][:n_lines] == gold_out[j][:n_lines]:
                    _die(f"smc_{i} 与 smc_{j} golden 输出窗全等——8 路复制形态，"
                         f"跨片错置不可检出，配置无效（M4/C2）")
    else:
        channels = {0: load_src_lines}
        gold_out = {0: list(norm_fin)}

    # UR 预载 / 期望终态（equal_8smc_v1：8 通道对称同构——同 bank 同窗形、
    # 内容=本通道切片；单 SMC 模式保持 smc_0 承载 + smc_1..7 哨兵′ 既有形态）
    smc_range = range(SMC_COUNT) if is8 else range(1)
    for k in smc_range:
        for b in range(UR_BANKS):
            if b in load_banks:
                pre = [g2.sentinel_line(b, kk) for kk in range(UR_BANK_DEPTH)]
                fin = list(pre)
                fin[:n_lines] = channels[k][b]
            elif b == out_bank:
                pre = [g2.ZERO_LINE] * UR_BANK_DEPTH
                fin = gold_out[k]  # smc_0 = build golden；k>=1 = 独立参考窗
            else:
                pre = [ln.lower().removeprefix("0x")
                       for ln in _read_hex_lines(ur_src / f"bank{b}.hex")]
                fin = [ln.lower().removeprefix("0x")
                       for ln in _read_hex_lines(ob_src / f"bank{b}_final.hex")]
            g2.write_hex_lines(str(case_dir / f"smc_{k}" / "ur_data"
                                   / f"bank{b}.hex"), pre)
            g2.write_hex_lines(str(case_dir / f"smc_{k}" / "output_bank"
                                   / f"bank{b}_final.hex"), fin)
    if not is8:
        for i in range(1, 8):
            for b in range(UR_BANKS):
                g2.write_hex_lines(
                    str(case_dir / f"smc_{i}" / "ur_data" / f"bank{b}.hex"),
                    [g2.sentinel_aux_line(i, b, k) for k in range(UR_BANK_DEPTH)])

    # HBM 镜像：窗口外 canary（g2.preload_pattern_line），输入窗=源数据，
    # 输出窗=全零。总长 = 输出区末 + 256 行 canary 尾。
    # equal_8smc_v1：8 通道同偏移不同内容（输入窗=本通道切片，
    # param/constant 全通道同源）；单 SMC 模式保持 init1 真数据 +
    # init2..8 泄漏标记的既有形态。
    out_line0 = out_base // LINE_BYTES
    total_hbm_lines = out_line0 + (n_lines) + 256
    init_imgs: dict[int, list[str]] = {}
    for k in (range(SMC_COUNT) if is8 else range(1)):
        img: list[str] = []
        for kk in range(total_hbm_lines):
            byte0 = kk * LINE_BYTES
            hit = next((e for e in hbm_layout
                        if e["byte_base"] <= byte0
                        < e["byte_base"] + e["bytes"]), None)
            if hit is None:
                img.append(g2.preload_pattern_line(kk))
            elif hit["direction"] == "h2d":
                img.append(channels[k][hit["bank"]]
                           [kk - hit["byte_base"] // LINE_BYTES])
            else:
                img.append(g2.ZERO_LINE)
        init_imgs[k] = img
    snap = case_dir / "memory_init_snapshot"
    snap.mkdir(parents=True, exist_ok=False)
    for k, img in init_imgs.items():
        g2.write_hex_lines(str(snap / f"memory_init{k + 1}.hex"), img)
    if not is8:
        for i in range(2, 9):
            g2.write_hex_lines(str(snap / f"memory_init{i}.hex"),
                               [g2.mark_pattern_line(k)
                                for k in range(g2.HBM_MARK_LINES)])

    # 区域源哈希（manifest 锚定，检查器复核篡改）
    # v2：每 tensor per-SMC 8 值（{tensor: {smc_k: sha}}）——通道同偏移不同
    # 内容，输入张量哈希必须可区分（M2，与 C2 互证）；v1 保持单值。
    region_hashes = {}
    for e in hbm_layout:
        l0 = e["byte_base"] // LINE_BYTES
        if is8:
            region_hashes[e["name"]] = {
                f"smc_{k}": _sha256_bytes(
                    ("\n".join(init_imgs[k][l0:l0 + e["lines"]]) + "\n")
                    .encode())
                for k in range(SMC_COUNT)}
        else:
            region_hashes[e["name"]] = _sha256_bytes(
                ("\n".join(init_imgs[0][l0:l0 + e["lines"]]) + "\n").encode())

    hbm_preload_files = {
        f"memory_init{i}.hex": _sha256_file(snap / f"memory_init{i}.hex")
        for i in range(1, 9)}

    last_compute_sid = K  # stage_1..stage_K 的末段
    store_sid = K + 1
    expect = {
        "schema": EXPECT_SCHEMA,
        "case": case_dir.name,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat(),
        "sync_model": "tb_assisted",
        "op_n": n, "n_lines": n_lines,
        "stage_count": K + 2,
        "stages": [{"stage_id": s["stage_id"], "kind": s["kind"]}
                   for s in manifest_stages],
        "barrier": {"exp_ldb_done": len(ldb_lines_all),
                    "exp_ldbaxi_done": len(ldbaxi_lines_all),
                    "timeout_cycles": BARRIER_TIMEOUT},
        "wbarrier": {"bank": out_bank, "writes": n_lines,
                     "stage": last_compute_sid,
                     "timeout_cycles": BARRIER_TIMEOUT},
        "s4barrier": {"addr0": out_line0, "writes": n_lines,
                      "bresp": len(stbaxi_lines),
                      "timeout_cycles": BARRIER_TIMEOUT},
        "hbm_layout": hbm_layout,
        "hbm_preload": {"files": hbm_preload_files},
        "notes": [
            "TB 辅助屏障（ADR-060 §2.2/§6，F5）：仿真限定，非 host 可执行协议",
            "LOAD 服务 bank 的 TB 预载一律行索引哨兵——LOAD 承重，禁止预装正确数据",
            "STORE 完成判据 = HBM 模型 RAM 写口落实 + B 响应计数（S4BARRIER）；"
            "STB/STBAXI 信用结构性先于 HBM 写落实（ADR-060 §6.2），只记录不门控",
            "COMPUTE 段产物 = 公式编译器链字节原样拷贝（manifest 逐文件 sha256 锚定）",
        ] + ([
            "equal_8smc_v1：信用链计数含 8 SMC 聚合（crd 链 daisy-chain 过"
            "全部 SMC），exp 计数保持单 SMC 指令数（>= 语义），观测值入日志"
            "不门控；WBARRIER/S4 仅 smc_0 通道——smc_1..7 落位未被屏障监测，"
            "正式验收须全目标监测（spec §3 C3），本轮为探索档",
        ] if is8 else []),
    }
    (case_dir / "g3_expect.json").write_text(
        json.dumps(expect, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    stage_man = build_copy / "stage_manifest.json"
    tb_sha = _sha256_file(tb_path) if Path(tb_path).is_file() else None
    manifest = {
        "schema_version": (PROGRAM_MANIFEST_SCHEMA_V2 if is8
                           else PROGRAM_MANIFEST_SCHEMA),
        "op": op_name, "formula": formula,
        "n": (SMC_COUNT * n if is8 else n), "lanes": 4, "n_lines": n_lines,
        "generated_at": expect["generated_at"],
        "hashes": {
            "sim_config_json": _sha256_file(lay["sim_cfg_path"]),
            "stage_manifest_json": (_sha256_file(stage_man)
                                    if stage_man.is_file() else None),
            "golden_data_npz": (_sha256_file(build_copy / "golden_data.npz")
                                if (build_copy / "golden_data.npz").is_file()
                                else None),
        },
        "tb": {"path": str(tb_path), "sha256": tb_sha,
               "note": "VCS 仿真 = 源树编译 simv + 本 TB；RTL_VERSION=0x00042200 "
                       "为板端 CSR 读取期望值（上板证据线），VCS 侧不断言该寄存器"},
        "rtl_version_expected_board": "0x00042200",
        "hbm_layout": hbm_layout,
        "hbm_region_sha256": region_hashes,
        "stages": manifest_stages,
        "sentinel": {"ur_load_served": "g2.sentinel_line(b,k)=5E.....",
                     "hbm_canary": "g2.preload_pattern_line(k)=B0A0.....",
                     "hbm_mark_2_8": "g2.mark_pattern_line(k)=EE1D.....",
                     "output_prefill": "zero"},
        "sync_model": "tb_assisted",
        "fixed_waits": [],
        "limitations": [
            "LOAD/STORE 生成仅覆盖 D1 布局形态（addr=0、整行、单 SMC、bank×4096）",
            "LOAD 数据与编译器 python sim 预载同源（同一 build 产物）；独立参考是"
            "独立 numpy fp32 重算，输入数据本身非独立第二来源（ADR-056 既有口径）",
            "完成时序信任 TB 辅助屏障，非硬件完成契约（F6 线既知边界）",
            "段间空窗维持 ADR-057 errata-2026-10-01 口径（limitations 分列，"
            "不单独致 RED）",
        ],
    }
    if is8:
        manifest["partition"] = {
            "scheme": "equal_8smc_v1",
            "n_total": SMC_COUNT * n, "n_per": n, "smc_count": SMC_COUNT,
            "channel_stride": CHANNEL_STRIDE,
            "addr_rule": (
                "addr_k = byte_base + k*channel_stride；atcbmc300 交叉开关按"
                "地址译码选通道（noc/rtl/include/atcbmc300_config.vh:172-201，"
                "SLV2..9=HBM ch0..7 各 256MB）；per-SMC 基址公式 RTL 行级锚="
                "ldb_ur_axi_conv.v:392-425 / stb_ur_axi_conv.v:360-377"),
            "input_derivation": (
                "slice0=build 预载原样；slice k>=1 = 逐 input 张量确定性仿射"
                "派生（fp32：v*(1+0.5k+0.25i) + (±0.125k)，i=张量声明序，奇"
                "张量取负偏置）；param/constant 全通道同源；golden smc_1..7 ="
                " 公式有序 AST fp32 独立参考逐通道生成"),
        }
        manifest["golden_files"] = [
            {"file": f"smc_{k}/output_bank/bank{b}_final.hex",
             "sha256": _sha256_file(case_dir / f"smc_{k}" / "output_bank"
                                    / f"bank{b}_final.hex")}
            for k in range(SMC_COUNT) for b in range(UR_BANKS)]
        manifest["sentinel"]["hbm_mark_2_8"] = \
            "无 smc_1..7 数据哨兵（equal_8smc_v1 全通道真数据）"
        manifest["limitations"] = [
            "LOAD/STORE 生成仅覆盖 D1 布局形态（addr=0、整行、bank×4096）",
            "equal_8smc_v1 探索档：TB 完成监测仍 SMC0 中心（WBARRIER/S4 仅 "
            "smc_0 通道；信用链聚合计数仅记录不门控）——正式验收须全目标监测"
            "（docs/8smc_equal_partition_spec_20261014.md §3 C3，路径 A TB 改"
            "动未授权）",
            "smc_1..7 golden = 公式独立参考直接生成（与 C-H 同 machinery）， "
            "smc_0 保持 build golden 三方互校",
            "输入切片 1..7 为确定性仿射派生，非独立第二来源（ADR-056 既有口"
            "径延伸）；slice0 与编译器 python sim 预载同源",
            "完成时序信任 TB 辅助屏障，非硬件完成契约（F6 线既知边界）",
        ]
    (case_dir / "program_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build-copy", type=Path, required=True,
                    help="编译器构建产物快照（含 sim_config.json / smc_0 / stages?/）")
    ap.add_argument("--case-dir", type=Path, required=True,
                    help="目标 case 目录（须不存在——拒绝覆盖旧产物）")
    ap.add_argument("--n", type=int, required=True, help="元素数（4 的倍数）")
    ap.add_argument("--tb", type=Path,
                    default=Path.home() / "u280_sim_v422" / "sim_multi_stage"
                    / "tb_aiacc_top_v04_f_multi_stage.v")
    ap.add_argument("--op-name", required=True,
                    help="算子名（manifest 如实记录，禁硬编码）")
    ap.add_argument("--formula", required=True,
                    help="公式串（manifest 自包含记录，C-H 独立参考之源）")
    ap.add_argument("--partition", default=None, choices=list(PARTITION_SCHEMES),
                    help="分片方案（缺省=单 SMC；equal_8smc=八目标等长分片，"
                         "此时 --n 语义=每 SMC 元素数 n_per）")
    a = ap.parse_args()
    man = build_unified_case(a.build_copy, a.case_dir, a.n, a.tb,
                             op_name=a.op_name, formula=a.formula,
                             partition=a.partition)
    print(f"[ULCS-GEN] case = {a.case_dir}")
    print(f"[ULCS-GEN] stages = "
          + " → ".join(f"{s['kind']}(stage_{s['stage_id']})"
                       for s in man["stages"]))
    print(f"[ULCS-GEN] n={a.n} n_lines={man['n_lines']} "
          f"load_banks={[t['bank'] for t in man['stages'][0]['covers']]} "
          f"out_bank={man['stages'][-1]['covers'][0]['bank']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
