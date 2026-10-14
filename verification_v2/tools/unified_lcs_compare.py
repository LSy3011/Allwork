#!/usr/bin/env python3
"""unified_lcs_compare —— ADR-061 D6：统一链路 VCS 后数据面比对。

输入 = case 目录（TB dump 已落盘）+ 程序 manifest + 各 COMPUTE 段的
physical_instructions.json（反空洞双源计数，沿用 ADR-055 A1 纪律）。

检查集（整数比较铁律；任一 RED → exit 1）：
  Z3  三区泛化（LOAD-aware 的 ADR-055 A1）：T={(b,i)|golden≠init}；
      |T| == vout0 BODY vld 行数（逐段求和）+ LOAD 行数（LOAD 服务 bank 数
      × n_lines，manifest 声明）；T 上 dump==golden；T 外 dump==golden==init。
  C-H HBM 输出窗终态 == golden UR 输出窗（STORE 映射逐字节）
      == 从 memory_init1 输入区独立重算（manifest 记录的公式经有序 AST
      fp32 逐 op 舍入求值——与硬件流水同序；输入按 role 重建：
      input=全行流、param=line0 四 lane 标量驻留形态正面取证）；
      HBM 窗口外 canary 完整（slice0 非窗口区 == 预载底图；slice1..7 ==
      标记图案——STORE 泄漏写检测）。
口径：UR 读写次序/写账/屏障证据在 arbitrate_program（本工具不重复）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "verification_v2" / "tools"))

import ldb_review_case_gen as g2  # noqa: E402
from three_zone_compare import live_bodily_writes  # noqa: E402
from unified_lcs_case_gen import (  # noqa: E402
    PROGRAM_MANIFEST_SCHEMA, PROGRAM_MANIFEST_SCHEMA_V2, SMC_COUNT,
    UR_BANKS, UR_BANK_DEPTH, LINE_BYTES)
from verification_v2.reference.ordered_ast import (  # noqa: E402
    FormulaRejection, parse_ordered_formula)
from verification_v2.reference import reference_generator as rg  # noqa: E402

F32 = np.float32
U32 = np.uint32


def _read_ints(p: Path) -> list[int]:
    if not p.is_file():
        raise SystemExit(f"2: 缺文件 {p}")
    vals = []
    for ln, raw in enumerate(p.read_text().splitlines(), 1):
        s = raw.strip()
        if not s:
            continue
        s = s[2:] if s.lower().startswith("0x") else s
        vals.append(int(s, 16))
    return vals


def _line_to_lanes(v: int) -> list[int]:
    """128b 行整数值 → [lane0..lane3]（lane0 = 最低 32b，与 npz 元素序一致）。"""
    return [(v >> (32 * i)) & 0xFFFFFFFF for i in range(4)]


def _lanes_to_line(lanes: list[int]) -> int:
    v = 0
    for i, w in enumerate(lanes):
        v |= (int(w) & 0xFFFFFFFF) << (32 * i)
    return v


class Compare:
    def __init__(self, case_dir: Path, instr_paths: list[Path]):
        self.case = Path(case_dir)
        self.instr_paths = [Path(p) for p in instr_paths]
        self.bad: list[str] = []
        self.report: dict = {}

    def err(self, msg: str) -> None:
        self.bad.append(msg)

    def run(self) -> int:
        man_p = self.case / "program_manifest.json"
        if not man_p.is_file():
            print(f"[ULCS-CMP] 2: 缺 program_manifest.json: {man_p}", file=sys.stderr)
            return 2
        man = json.loads(man_p.read_text(encoding="utf-8"))
        schema = man.get("schema_version")
        if schema == PROGRAM_MANIFEST_SCHEMA:
            is_v2 = False
        elif schema == PROGRAM_MANIFEST_SCHEMA_V2:
            is_v2 = True
        else:
            print(f"[ULCS-CMP] 2: manifest schema 不符（{schema!r}）",
                  file=sys.stderr)
            return 2
        n_lines = man["n_lines"]
        lay = {e["name"]: e for e in man["hbm_layout"]}
        loads = [e for e in man["hbm_layout"] if e["direction"] == "h2d"]
        out_e = next(e for e in man["hbm_layout"] if e["direction"] == "d2h")

        if is_v2:
            return self._finish_v2(self._run_v2(man, lay, loads, out_e, n_lines))

        init_b, gold_b, dump_b = [], [], []
        for b in range(UR_BANKS):
            init_b.append(_read_ints(self.case / "smc_0" / "ur_data"
                                     / f"bank{b}.hex"))
            gold_b.append(_read_ints(self.case / "smc_0" / "output_bank"
                                     / f"bank{b}_final.hex"))
            dump_b.append(_read_ints(self.case / "smc_0"
                                     / f"ur_result_smc_0_bank{b}.hex"))

        self._three_zone(man, loads, n_lines, init_b, gold_b, dump_b)
        self._c_h(man, lay, loads, out_e, n_lines, gold_b)
        if self.bad:
            print(f"[ULCS-CMP] RED（{len(self.bad)} 项）:")
            for m in self.bad:
                print(f"  - {m}")
            return 1
        print(f"[ULCS-CMP] GREEN: {self.case}（Z3 三区泛化 + C-H HBM 窗/"
              f"独立参考/STORE 映射三方一致 + canary 完整）")
        return 0

    # ---- Z3 三区泛化 ----
    def _three_zone(self, man, loads, n_lines, init_b, gold_b, dump_b,
                    tag: str = "") -> None:
        T: list[tuple[int, int]] = []
        for b in range(UR_BANKS):
            if not (len(init_b[b]) == len(gold_b[b]) == len(dump_b[b])
                    == UR_BANK_DEPTH):
                self.err(f"(Z3{tag}) bank{b} init/golden/dump 行数 "
                         f"{len(init_b[b])}/{len(gold_b[b])}/{len(dump_b[b])}"
                         f" != {UR_BANK_DEPTH}")
                continue
            for i in range(UR_BANK_DEPTH):
                if gold_b[b][i] != init_b[b][i]:
                    T.append((b, i))
        vout0_cnt = sum(live_bodily_writes(p, "vout0") for p in self.instr_paths)
        load_cnt = len(loads) * n_lines
        want = vout0_cnt + load_cnt
        self.report[f"three_zone{tag}"] = {
            "valid_lines": len(T), "vout0_body": vout0_cnt,
            "load_lines": load_cnt, "expected_total": want}
        if len(T) == 0:
            self.err(f"(Z3{tag}) |T|=0——有效区为空（反空洞）")
            return
        if len(T) != want:
            self.err(f"(Z3{tag}) |T|={len(T)} != vout0 BODY {vout0_cnt} + LOAD "
                     f"{load_cnt} = {want}（反空洞双源计数不符）")
        t_set = set(T)
        bad_t = [(b, i) for (b, i) in T if dump_b[b][i] != gold_b[b][i]]
        if bad_t:
            b, i = bad_t[0]
            self.err(f"(Z3{tag}) 有效区 dump != golden ×{len(bad_t)}（首例 bank{b} "
                     f"行{i}: dump={dump_b[b][i]:032x} golden={gold_b[b][i]:032x}）")
        bad_o = [(b, i) for b in range(UR_BANKS) for i in range(UR_BANK_DEPTH)
                 if (b, i) not in t_set
                 and not (dump_b[b][i] == gold_b[b][i] == init_b[b][i])]
        if bad_o:
            b, i = bad_o[0]
            self.err(f"(Z3{tag}) 非目标区 dump==golden==init 不成立 ×{len(bad_o)}"
                     f"（首例 bank{b} 行{i}: dump={dump_b[b][i]:032x} "
                     f"golden={gold_b[b][i]:032x} init={init_b[b][i]:032x}）")

    # ---- C-H ----
    def _c_h(self, man, lay, loads, out_e, n_lines, gold_b) -> None:
        out_bank, out_l0 = out_e["bank"], out_e["byte_base"] // LINE_BYTES
        hbm = _read_ints(self.case / "smc_0" / "hbm_result_smc_0.hex")
        golden_win = gold_b[out_bank][:n_lines]
        hbm_win = hbm[out_l0:out_l0 + n_lines]
        if len(hbm_win) != n_lines:
            self.err(f"(C-H) hbm_result 行数 {len(hbm)} 不足输出窗 "
                     f"[{out_l0},{out_l0 + n_lines})")
            return
        diff = [k for k in range(n_lines) if hbm_win[k] != golden_win[k]]
        if diff:
            k = diff[0]
            self.err(f"(C-H) HBM 输出窗 != golden UR 窗 ×{len(diff)}（首例行"
                     f"{k}: hbm={hbm_win[k]:032x} golden={golden_win[k]:032x}）"
                     f"——STORE 映射断裂")
        # 独立重算：输入 = memory_init1 各 h2d 区（外部存储真源）；公式 =
        # manifest 自包含记录，经有序 AST fp32 逐 op 舍入求值（与 V1 链
        # independent_reference 同一 machinery、与硬件流水同序）。公式驱动
        # 通用形态——不含算子名特判；输入按 role 重建并正面取证形态。
        init1 = _read_ints(self.case / "memory_init_snapshot" / "memory_init1.hex")
        formula = man.get("formula")
        if not isinstance(formula, str) or not formula.strip():
            self.err("(C-H) manifest 缺 formula 字段——独立参考无公式之源"
                     "（adr061_program_manifest_v1 须自包含记录；旧 case 须重新生成）")
            return
        load_entries = [e for e in man["hbm_layout"] if e["role"] in ("input", "param")]
        reserved = [e["name"] for e in man["hbm_layout"]
                    if e["role"] in ("output", "constant")]
        try:
            root = parse_ordered_formula(formula,
                                         [e["name"] for e in load_entries],
                                         reserved)
        except Exception as exc:  # noqa: BLE001——解析失败须响亮入档
            self.err(f"(C-H) 公式解析失败 {formula!r}: {exc}")
            return
        leaves = set(rg.leaf_paths(root).values())   # {路径: 叶名} → 叶名集

        def region_vals(e):
            l0 = e["byte_base"] // LINE_BYTES
            vals: list[int] = []
            for ln in init1[l0:l0 + e["lines"]]:
                vals.extend(_line_to_lanes(ln))
            return np.asarray(vals, dtype=U32).view(F32)

        inputs: dict[str, np.ndarray] = {}
        modes: dict[str, str] = {}
        for e in load_entries:
            name, role = e["name"], e["role"]
            if role == "input":
                inputs[name] = region_vals(e)
                modes[name] = "stream（全行）"
            else:
                # param = stride=0 驻留标量（yaml：消耗 1 word）：line0 四 lane
                # 须全等、其余行须全零（形态正面取证），然后按标量广播——与硬件
                # stride=0 重读 line0 的消费方式及 ADR-056 独立参考标量语义一致。
                l0 = e["byte_base"] // LINE_BYTES
                p_lines = init1[l0:l0 + e["lines"]]
                p0_lanes = _line_to_lanes(p_lines[0])
                if len(set(p0_lanes)) != 1:
                    self.err(f"(C-H) 参数 {name} line0 四 lane 不全等: "
                             f"{[f'{w:08x}' for w in p0_lanes]}——非标量驻留形态")
                    return
                if any(v != 0 for ln in p_lines[1:] for v in _line_to_lanes(ln)):
                    self.err(f"(C-H) 参数 {name} line1.. 存在非零——非 stride=0 "
                             "驻留形态（独立参考仅覆盖标量广播语义）")
                    return
                inputs[name] = np.asarray([p0_lanes[0]], dtype=U32).view(F32)
                modes[name] = "stride0 标量驻留（line0 四 lane 核验）"
        missing = leaves - set(inputs)
        if missing:
            self.err(f"(C-H) 公式叶 {sorted(missing)} 在布局中无输入区"
                     f"（formula={formula!r}，可提供={sorted(inputs)}）")
            return
        extra = set(inputs) - leaves
        if extra:
            self.err(f"(C-H) 布局输入 {sorted(extra)} 未被公式 {formula!r} 引用"
                     "——装载而未消费，不静默放过")
            return
        ev = rg.evaluate(root, inputs)
        y_ref = np.asarray(ev.y, dtype=F32).reshape(-1)
        if y_ref.size != 4 * n_lines:
            self.err(f"(C-H) 参考求值元素数 {y_ref.size} != 4·n_lines="
                     f"{4 * n_lines}（输入形态与布局不符）")
            return
        ref_win = [_lanes_to_line(y_ref.view(U32)[4 * k:4 * k + 4].tolist())
                   for k in range(n_lines)]
        diff_rg = [k for k in range(n_lines) if ref_win[k] != golden_win[k]]
        if diff_rg:
            k = diff_rg[0]
            self.err(f"(C-H) 独立重算（公式有序 AST）!= golden ×{len(diff_rg)}"
                     f"（首例行{k}: ref={ref_win[k]:032x} "
                     f"golden={golden_win[k]:032x}）")
        diff_rh = [k for k in range(n_lines)
                   if k < len(hbm_win) and ref_win[k] != hbm_win[k]]
        if diff_rh:
            self.err(f"(C-H) 独立重算（公式有序 AST）!= HBM 窗 ×{len(diff_rh)}"
                     f"（首例行{diff_rh[0]}）")
        self.report["c_h"] = {"golden_vs_hbm": not diff,
                              "ref_vs_golden": not diff_rg,
                              "ref_vs_hbm": not diff_rh,
                              "reference": f"公式有序 AST fp32 求值（{formula}），"
                                           "输入=memory_init1 区域按 role 重建",
                              "input_modes": modes}
        # canary：slice0 窗口外 == 预载底图（检查 memory_init1 覆盖范围）
        spans = [(e["byte_base"] // LINE_BYTES,
                  e["byte_base"] // LINE_BYTES + e["lines"])
                 for e in man["hbm_layout"]]
        init1_len = len(init1)
        bad_canary = 0
        for k in range(min(init1_len, len(hbm))):
            if any(a <= k < b for a, b in spans):
                continue
            if hbm[k] != int(g2.preload_pattern_line(k), 16):
                bad_canary += 1
                if bad_canary == 1:
                    self.err(f"(C-H) HBM slice0 窗口外 canary 被改 @行{k}: "
                             f"{hbm[k]:032x} != {g2.preload_pattern_line(k)}"
                             f"（泄漏写/越界写）")
        # slice1..7 == 标记图案（泄漏检测；mark 行数 = 预载行数）
        for i in range(1, 8):
            p = self.case / f"smc_{i}" / f"hbm_result_smc_{i}.hex"
            if not p.is_file():
                self.err(f"(C-H) 缺 HBM dump: {p}（泄漏检测面缺失）")
                continue
            sl = _read_ints(p)
            bad = [k for k in range(min(g2.HBM_MARK_LINES, len(sl)))
                   if sl[k] != int(g2.mark_pattern_line(k), 16)]
            if bad:
                self.err(f"(C-H) HBM slice{i} 标记区被改 ×{len(bad)}"
                         f"（首例行{bad[0]}）——跨片泄漏写")

    # ---- v2 八目标（equal_8smc_v1；规格 docs/8smc_equal_partition_spec
    # _20261014.md §3） ----
    def _run_v2(self, man, lay, loads, out_e, n_lines) -> None:
        """逐 SMC 三区 + C-H（C1 数据面，缺席即 RED 非 SKIP）；C2 可区分性运行
        时复查；C4 泄漏面反转（逐通道 h2d 区不变 + 窗口外 canary==预载底图）。
        runner 的「缺席即 SKIP」在本判据下无效——compared_smc 必须为 8。"""
        gold_wins: dict[int, list[int]] = {}
        compared: list[int] = []
        for k in range(SMC_COUNT):
            tag = f"/smc_{k}"
            files: dict[str, Path] = {}
            missing = False
            for b in range(UR_BANKS):
                for what, p in (
                        (f"init_b{b}", self.case / f"smc_{k}" / "ur_data"
                         / f"bank{b}.hex"),
                        (f"gold_b{b}", self.case / f"smc_{k}" / "output_bank"
                         / f"bank{b}_final.hex"),
                        (f"dump_b{b}", self.case / f"smc_{k}"
                         / f"ur_result_smc_{k}_bank{b}.hex")):
                    if not p.is_file():
                        self.err(f"(C1{tag}) 缺目标结果文件: {p.name} 于 {p}"
                                 f"——缺席即 RED（非 SKIP）")
                        missing = True
                    files[what] = p
            if missing:
                continue
            init_b = [_read_ints(files[f"init_b{b}"]) for b in range(UR_BANKS)]
            gold_b = [_read_ints(files[f"gold_b{b}"]) for b in range(UR_BANKS)]
            dump_b = [_read_ints(files[f"dump_b{b}"]) for b in range(UR_BANKS)]
            self._three_zone(man, loads, n_lines, init_b, gold_b, dump_b, tag)
            gold_wins[k] = gold_b[out_e["bank"]][:n_lines]
            self._c_h_channel(man, lay, loads, out_e, n_lines, gold_b, k)
            compared.append(k)
        self.report["v2"] = {"schema": PROGRAM_MANIFEST_SCHEMA_V2,
                             "compared_smcs": compared}
        # C1 量纲：8/8 全比对（缺任一目标结果即上面已 RED；此处正面记录）
        if len(compared) != SMC_COUNT:
            self.err(f"(C1) compared_smc={len(compared)} != {SMC_COUNT}——"
                     f"缺目标 SMC: "
                     f"{sorted(set(range(SMC_COUNT)) - set(compared))}")
        # C2：8 份 golden 输出窗两两不全等（M4 的运行时复查，防 8 路复制假绿）
        if len(gold_wins) == SMC_COUNT:
            for i in range(SMC_COUNT):
                for j in range(i + 1, SMC_COUNT):
                    if gold_wins[i] == gold_wins[j]:
                        self.err(f"(C2) smc_{i} 与 smc_{j} golden 输出窗全等"
                                 f"——可区分性破坏（8 路复制形态），整案无效")

    def _c_h_channel(self, man, lay, loads, out_e, n_lines, gold_b,
                     k: int) -> None:
        """v2 通道 k 的 C1/C-H/C4：窗口==golden、独立参考三方互校、h2d 区不
        变 + 窗外 canary==预载底图（泄漏面反转，取代 v1 slice1..7 标记区）。"""
        tag = f"/smc_{k}"
        out_bank, out_l0 = out_e["bank"], out_e["byte_base"] // LINE_BYTES
        hbm_p = self.case / f"smc_{k}" / f"hbm_result_smc_{k}.hex"
        if not hbm_p.is_file():
            self.err(f"(C1{tag}) 缺 HBM dump: {hbm_p}——缺一个结果文件即 RED（N2）")
            return
        hbm = _read_ints(hbm_p)
        golden_win = gold_b[out_bank][:n_lines]
        hbm_win = hbm[out_l0:out_l0 + n_lines]
        if len(hbm_win) != n_lines:
            self.err(f"(C1{tag}) hbm_result 行数 {len(hbm)} 不足输出窗 "
                     f"[{out_l0},{out_l0 + n_lines})")
            return
        diff = [i for i in range(n_lines) if hbm_win[i] != golden_win[i]]
        if diff:
            i = diff[0]
            self.err(f"(C1{tag}) HBM 输出窗 != golden UR 窗 ×{len(diff)}（首例行"
                     f"{i}: hbm={hbm_win[i]:032x} golden={golden_win[i]:032x}）"
                     f"——STORE 映射断裂/分片错置")
        # 独立重算（与 v1 C-H 同 machinery；输入 = 本通道 memory_init image）
        init_img = _read_ints(self.case / "memory_init_snapshot"
                              / f"memory_init{k + 1}.hex")
        formula = man.get("formula")
        if not isinstance(formula, str) or not formula.strip():
            self.err(f"(C-H{tag}) manifest 缺 formula 字段")
            return
        load_entries = [e for e in man["hbm_layout"]
                        if e["role"] in ("input", "param")]
        reserved = [e["name"] for e in man["hbm_layout"]
                    if e["role"] in ("output", "constant")]
        try:
            root = parse_ordered_formula(formula,
                                         [e["name"] for e in load_entries],
                                         reserved)
        except Exception as exc:  # noqa: BLE001——解析失败须响亮入档
            self.err(f"(C-H{tag}) 公式解析失败 {formula!r}: {exc}")
            return
        leaves = set(rg.leaf_paths(root).values())
        inputs: dict[str, np.ndarray] = {}
        for e in load_entries:
            name, role = e["name"], e["role"]
            l0 = e["byte_base"] // LINE_BYTES
            if role == "input":
                vals: list[int] = []
                for ln in init_img[l0:l0 + e["lines"]]:
                    vals.extend(_line_to_lanes(ln))
                inputs[name] = np.asarray(vals, dtype=U32).view(F32)
            else:
                p_lines = init_img[l0:l0 + e["lines"]]
                p0_lanes = _line_to_lanes(p_lines[0])
                if len(set(p0_lanes)) != 1:
                    self.err(f"(C-H{tag}) 参数 {name} line0 四 lane 不全等"
                             f"——非标量驻留形态")
                    return
                if any(v != 0 for ln in p_lines[1:]
                       for v in _line_to_lanes(ln)):
                    self.err(f"(C-H{tag}) 参数 {name} line1.. 存在非零——非 "
                             f"stride0 驻留形态（独立参考仅覆盖标量广播语义）")
                    return
                inputs[name] = np.asarray([p0_lanes[0]], dtype=U32).view(F32)
        missing = leaves - set(inputs)
        if missing:
            self.err(f"(C-H{tag}) 公式叶 {sorted(missing)} 在布局中无输入区")
            return
        extra = set(inputs) - leaves
        if extra:
            self.err(f"(C-H{tag}) 布局输入 {sorted(extra)} 未被公式 "
                     f"{formula!r} 引用——装载而未消费，不静默放过")
            return
        ev = rg.evaluate(root, inputs)
        y_ref = np.asarray(ev.y, dtype=F32).reshape(-1)
        if y_ref.size != 4 * n_lines:
            self.err(f"(C-H{tag}) 参考求值元素数 {y_ref.size} != 4·n_lines="
                     f"{4 * n_lines}")
            return
        ref_win = [_lanes_to_line(y_ref.view(U32)[4 * i:4 * i + 4].tolist())
                   for i in range(n_lines)]
        diff_rg = [i for i in range(n_lines) if ref_win[i] != golden_win[i]]
        if diff_rg:
            i = diff_rg[0]
            self.err(f"(C-H{tag}) 独立重算 != golden ×{len(diff_rg)}（首例行"
                     f"{i}: ref={ref_win[i]:032x} golden={golden_win[i]:032x}）")
        diff_rh = [i for i in range(n_lines)
                   if i < len(hbm_win) and ref_win[i] != hbm_win[i]]
        if diff_rh:
            self.err(f"(C-H{tag}) 独立重算 != HBM 窗 ×{len(diff_rh)}"
                     f"（首例行{diff_rh[0]}）")
        self.report[f"c_h{tag}"] = {
            "golden_vs_hbm": not diff, "ref_vs_golden": not diff_rg,
            "ref_vs_hbm": not diff_rh,
            "reference": f"公式有序 AST fp32 求值（{formula}），"
                         f"输入=memory_init{k + 1} 区域按 role 重建"}
        # C4 泄漏面反转：本通道 h2d 区不变 + 布局窗外 canary==预载底图
        spans = [(e["byte_base"] // LINE_BYTES,
                  e["byte_base"] // LINE_BYTES + e["lines"])
                 for e in man["hbm_layout"]]
        for e in man["hbm_layout"]:
            if e["direction"] != "h2d":
                continue
            l0 = e["byte_base"] // LINE_BYTES
            bad_in = [i for i in range(l0, min(l0 + e["lines"], len(hbm),
                                               len(init_img)))
                      if hbm[i] != init_img[i]]
            if bad_in:
                self.err(f"(C4{tag}) h2d 输入区 {e['name']} 被改 ×{len(bad_in)}"
                         f"（首例行{bad_in[0]}）——越界写")
        bad_canary = 0
        for i in range(min(len(init_img), len(hbm))):
            if any(a <= i < b for a, b in spans):
                continue
            if hbm[i] != int(g2.preload_pattern_line(i), 16):
                bad_canary += 1
                if bad_canary == 1:
                    self.err(f"(C4{tag}) HBM 布局窗外 canary 被改 @行{i}: "
                             f"{hbm[i]:032x} != {g2.preload_pattern_line(i)}"
                             f"（泄漏写/越界写）")

    def _finish_v2(self, _none) -> int:
        if self.bad:
            print(f"[ULCS-CMP] RED（{len(self.bad)} 项）:")
            for m in self.bad:
                print(f"  - {m}")
            return 1
        print(f"[ULCS-CMP] GREEN(v2 八目标): {self.case}（逐 SMC Z3 三区 + C-H "
              f"独立参考三方互校 + C2 可区分 + C4 通道泄漏面；compared_smc=8/8；"
              f"探索档监测缺口见 manifest limitations / spec §3 C3）")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("--instructions", type=Path, action="append", required=True,
                    help="各 COMPUTE 段 physical_instructions.json（可重复）")
    ap.add_argument("--report", type=Path, default=None)
    a = ap.parse_args()
    cmp_ = Compare(a.case_dir, a.instructions)
    rc = cmp_.run()
    cmp_.report["violations"] = cmp_.bad
    cmp_.report["verdict"] = "RED" if cmp_.bad else "GREEN"
    if a.report:
        a.report.write_text(json.dumps(cmp_.report, indent=2,
                                       ensure_ascii=False) + "\n",
                            encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
