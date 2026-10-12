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
    PROGRAM_MANIFEST_SCHEMA, UR_BANKS, UR_BANK_DEPTH, LINE_BYTES)
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
        if man.get("schema_version") != PROGRAM_MANIFEST_SCHEMA:
            print(f"[ULCS-CMP] 2: manifest schema 不符", file=sys.stderr)
            return 2
        n_lines = man["n_lines"]
        lay = {e["name"]: e for e in man["hbm_layout"]}
        loads = [e for e in man["hbm_layout"] if e["direction"] == "h2d"]
        out_e = next(e for e in man["hbm_layout"] if e["direction"] == "d2h")

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
    def _three_zone(self, man, loads, n_lines, init_b, gold_b, dump_b) -> None:
        T: list[tuple[int, int]] = []
        for b in range(UR_BANKS):
            if not (len(init_b[b]) == len(gold_b[b]) == len(dump_b[b])
                    == UR_BANK_DEPTH):
                self.err(f"(Z3) bank{b} init/golden/dump 行数 "
                         f"{len(init_b[b])}/{len(gold_b[b])}/{len(dump_b[b])}"
                         f" != {UR_BANK_DEPTH}")
                continue
            for i in range(UR_BANK_DEPTH):
                if gold_b[b][i] != init_b[b][i]:
                    T.append((b, i))
        vout0_cnt = sum(live_bodily_writes(p, "vout0") for p in self.instr_paths)
        load_cnt = len(loads) * n_lines
        want = vout0_cnt + load_cnt
        self.report["three_zone"] = {
            "valid_lines": len(T), "vout0_body": vout0_cnt,
            "load_lines": load_cnt, "expected_total": want}
        if len(T) == 0:
            self.err("(Z3) |T|=0——有效区为空（反空洞）")
            return
        if len(T) != want:
            self.err(f"(Z3) |T|={len(T)} != vout0 BODY {vout0_cnt} + LOAD "
                     f"{load_cnt} = {want}（反空洞双源计数不符）")
        t_set = set(T)
        bad_t = [(b, i) for (b, i) in T if dump_b[b][i] != gold_b[b][i]]
        if bad_t:
            b, i = bad_t[0]
            self.err(f"(Z3) 有效区 dump != golden ×{len(bad_t)}（首例 bank{b} "
                     f"行{i}: dump={dump_b[b][i]:032x} golden={gold_b[b][i]:032x}）")
        bad_o = [(b, i) for b in range(UR_BANKS) for i in range(UR_BANK_DEPTH)
                 if (b, i) not in t_set
                 and not (dump_b[b][i] == gold_b[b][i] == init_b[b][i])]
        if bad_o:
            b, i = bad_o[0]
            self.err(f"(Z3) 非目标区 dump==golden==init 不成立 ×{len(bad_o)}"
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
