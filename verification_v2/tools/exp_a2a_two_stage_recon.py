#!/usr/bin/env python3
"""ADR-057 验收① 实验侦察：scale_sum n=244 最小两段（60+1）真实 VCS 对账。

⚠ 实验性质（ADR-057 验收①原文：「未接入正式链前仅记实验」）：
  * 不经 run_v1_verify 正式链、不写 releases/、**不构成 V1 证据**；
  * 全部产物入 verification_v2/artifacts/exp_a2a_two_stage_<ts>/；
  * 唯一生产代码改动 = src/driver/assembler.py 新增 ``stage_start`` 可选参数
    （ADR-057 D2 的 s；默认 0 = 既有行为逐字节不变，由锚点 A0 回归证明）。

做法
----
  run A：标准 n=240 全程（不打补丁路径，stage_start 默认 0）→ stage_0 指令；
         与步⑤ n=240 冻结 run 快照逐字节对账（锚点 A0）。
  run B：n=244，monkeypatch 复用 run A 的已求解 LIR，使其只发尾段
         (m=1, s=60) → stage_1 指令 + 全量 244 输入 ur_data + golden 基底。
         （正式链的分段器/统一入口属验收②，此处仅手工两段。）
  golden：独立有序 AST 参考（verification_v2.reference）对 244 点求值，
         按 run_v1_verify §C.3 同法重写 output_bank 输出区并记 byte_identical。
  case：手铺 multi_inst_stage 两段布局（package_vcs_case.py docstring 契约；
         data_list 由 sandbox runner 每次运行自动生成）。
  VCS：run_sim_aiacc_top_f_multi_stage.sh → 编排器外独立重跑原生 compare。

对账检查（写入 recon_report.json；硬检查任一失败 → verdict=RED）
---------------------------------------------------------------
  A0 锚点：run A 的 iq_*.hex 与冻结 n=240 快照逐字节一致（同时证明
     stage_start 默认参数对既有发射零漂移）。
  S1 行数自洽：每队列 lines(stage_0) − lines(stage_1) = 59·II_q 且
     II_q 为正整数、span_q = (lines_1 − 2)/II_q − 1 ≥ 0；rtovr-14 段0 = 128 行
     （顶格墙实证复核）。
  S2 静态地址：build 快照 physical_instructions.json 中 BANK==2（y）的
     写地址——stage_0 ⊆ [0,60)、stage_1 == {60}。
  C1 段间次序（TB @time 全局时标，本实验配套 additive $display 升级）：
     stage_0 末笔 UR 写 ≤ stage_1 BEGIN；stage_1 全部写 ≥ 其 BEGIN。
  C2 观察窗：stage_0 done(intr cleared) 后、stage_1 BEGIN 前无 smc0 输出 bank 写；
     两标记同拍时窗口为空，不单独构成排干证明。
  C3 地址接续：smc0/bank2 写地址——stage_0 段 ⊆ [0,60)，stage_1 段 == {60}，
     并集 == [0,61)，不相交。
  C5 数值双通道：① 原生 compare 独立重跑 exit=0；② 本脚本独立解包
     ur_result bank2 前 61 词 × 4 lane 与独立参考 244 点逐位比对。
  （C4 尾段事件形态 vs 注册表 v2 的逐拍裁决属验收②统一入口职责；本实验
     仅记录各队列两段 ISSUE/FETCH 事件计数与行数对应关系作首次观察。）

用法
----
    # 干跑（只生成两段 + 静态检查，不跑 VCS）：
    python3 verification_v2/tools/exp_a2a_two_stage_recon.py --no-run-vcs
    # 全量（含 VCS）：
    python3 verification_v2/tools/exp_a2a_two_stage_recon.py --run-vcs
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO_ROOT))
sys.path.append(str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "verification_v2" / "tools"))

from verification_v2.ops.configurable import ConfigurableOperator            # noqa: E402
from verification_v2.reference.ordered_ast import parse_ordered_formula      # noqa: E402
from verification_v2.reference import reference_generator as rg              # noqa: E402
from verification_v2.infra.pipeline_runner import PipelineRunner             # noqa: E402
from driver.assembler import PhysicalAssembler                               # noqa: E402
from export_golden_hex import pack_words                                     # noqa: E402

F32 = np.float32
OP_YAML = REPO_ROOT / "verification_v2" / "ops" / "scale_sum.yaml"
# 步⑤ n=240 冻结 run（GREEN，rtovr-14 恰 128 行顶格）——锚点 A0 的对照源
DEFAULT_ANCHOR = (REPO_ROOT / "build" / "v1_runs" / "scale_sum"
                  / "20260930_032502_a757e9" / "n240" / "build" / "smc_0")
SUITE = "multi_inst_stage"
# Stage markers use @probe_cycle directly: $time and reset-relative probe cycles
# do not share an origin. Never infer one by dividing the other.


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def iq_row_counts(d: Path) -> dict:
    """{iq 文件名: 非空行数}（一指令一行；实算行数口径与 ADR-055 D3 一致）。"""
    out = {}
    for f in sorted(d.glob("iq_*.hex")):
        out[f.name] = sum(1 for ln in f.read_text().splitlines() if ln.strip())
    return out


def run_pipeline(op, inputs, op_name: str):
    """跑一次完整前端→发射→落盘，返回 (build_dir, ctx)。

    尾段偏移不在此注入——由调用方在调用前 monkeypatch
    ``PhysicalAssembler.assemble``（实验隔离：补丁只活在本进程，不触生产调用点）。
    """
    build_dir = REPO_ROOT / "build" / "verification_v2" / op_name / op.name
    if build_dir.exists():
        shutil.rmtree(build_dir)
    runner = PipelineRunner(hw_config_name="hw_config",
                            show_review=False, show_model=False)
    runner.output_root = str(build_dir.parent)
    ctx = runner.run_full_stack(op, inputs, op_name=op.name, external_input=False)
    return build_dir, ctx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=244)
    ap.add_argument("--seg", type=int, default=60, help="段 0 迭代数（尾段 = N_eff − seg）")
    ap.add_argument("--lanes", type=int, default=4)
    ap.add_argument("--case-name", default="exp_scale_sum_2stage_n244")
    ap.add_argument("--sandbox", type=Path, default=Path.home() / "u280_sim_v422")
    ap.add_argument("--anchor", type=Path, default=DEFAULT_ANCHOR,
                    help="冻结 n=240 build/smc_0 快照（A0 对账源）")
    ap.add_argument("--run-vcs", dest="run_vcs", action="store_true")
    ap.add_argument("--no-run-vcs", dest="run_vcs", action="store_false")
    ap.set_defaults(run_vcs=False)
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    n, m0, lanes = args.n, args.seg, args.lanes
    assert n % lanes == 0 and m0 > 0
    n_eff = n // lanes
    m1 = n_eff - m0
    assert m1 > 0, "本实验要求两段非空"

    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = REPO_ROOT / "verification_v2" / "artifacts" / f"exp_a2a_two_stage_{ts}"
    out_dir.mkdir(parents=True, exist_ok=False)
    report = {"ts": ts, "n": n, "n_eff": n_eff, "segments": [
        {"stage": 0, "s": 0, "m": m0}, {"stage": 1, "s": m0, "m": m1}],
        "checks": {}, "observations": {}, "provenance": {}}
    report["provenance"]["git"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    report["provenance"]["git_dirty"] = bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip())
    report["provenance"]["tb_sha256"] = sha256_file(
        args.sandbox / "sim_multi_stage" / "tb_aiacc_top_v04_f_multi_stage.v")
    report["provenance"]["source_sha256"] = {
        "assembler": sha256_file(REPO_ROOT / "src/driver/assembler.py"),
        "recon_script": sha256_file(Path(__file__)),
        "op_yaml": sha256_file(OP_YAML),
    }
    report["provenance"]["tb_note"] = (
        "TB 四处 stage 标记加 @time 及共享 @probe_cycle（纯 $display 观测性改动，"
        "改动前 sha256=cea299e22a7fcb359bef32254825aecd1dd6f312ac21ae6ab7cb3549326db31a）")

    print(f"[recon] n={n} N_eff={n_eff} 段: {m0}+{m1}；产物目录 {out_dir}")

    # ---------------- run A：标准 n=240（stage_0 指令源 + A0 锚点） ----------------
    op = ConfigurableOperator(str(OP_YAML))
    inputs_a = op.prepare_input(m0 * lanes)
    orig_assemble = PhysicalAssembler.assemble
    scheduled = {}
    def capture(self, lir_stream, II=1, num_iterations=1, admission=None, stage_start=0):
        scheduled.update(lir=copy.deepcopy(lir_stream), II=II)
        return orig_assemble(self, lir_stream, II=II, num_iterations=num_iterations,
                             admission=admission, stage_start=stage_start)
    PhysicalAssembler.assemble = capture
    try:
        build_a, ctx_a = run_pipeline(op, inputs_a, "exp_a2a_s0")
    finally:
        PhysicalAssembler.assemble = orig_assemble
    iq_a = build_a / "smc_0"
    rows_a = iq_row_counts(iq_a)
    anchor_rows = iq_row_counts(args.anchor)
    a0_detail, a0_ok = {}, True
    for name in sorted(set(rows_a) | set(anchor_rows)):
        fa, fb = iq_a / name, args.anchor / name
        same = fa.is_file() and fb.is_file() and sha256_file(fa) == sha256_file(fb)
        a0_detail[name] = {"rows_run_a": rows_a.get(name), "rows_anchor": anchor_rows.get(name),
                           "byte_identical": same}
        a0_ok &= same
    report["checks"]["A0_anchor_vs_frozen_n240"] = {"pass": a0_ok, "detail": a0_detail}
    print(f"[recon] A0 锚点（stage_0 ≡ 冻结 n240）: {'PASS' if a0_ok else 'FAIL'}")

    # ---------------- run B：n=244，patch 出尾段 (m=1, s=60) ----------------
    inputs_b = op.prepare_input(n)
    orig_assemble = PhysicalAssembler.assemble
    patch_state = {"calls": 0}

    def _patched(self, lir_stream, II=1, num_iterations=1, admission=None, stage_start=0):
        patch_state["calls"] += 1
        return orig_assemble(self, copy.deepcopy(scheduled["lir"]), II=scheduled["II"], num_iterations=m1,
                             admission=admission, stage_start=m0)

    PhysicalAssembler.assemble = _patched
    try:
        build_b, ctx_b = run_pipeline(op, inputs_b, "exp_a2a_s1")
    finally:
        PhysicalAssembler.assemble = orig_assemble
    report["observations"]["run_b_assemble_calls"] = patch_state["calls"]
    report["observations"]["run_b_sim_success"] = bool(ctx_b.get("sim_success"))
    report["observations"]["run_b_note"] = (
        "run B 的 python sim 只执行尾段程序（仅算 j=60 一词），sim_success 不作判据；"
        "有效产物 = stage_1 指令 + 全量输入 ur_data + 布局元数据")
    iq_b = build_b / "smc_0"
    rows_b = iq_row_counts(iq_b)

    # 输出区定位与编排器同源：sim_config.json 的 output 张量（bank/addr），不硬编码
    sim_cfg = json.loads((build_b / "sim_config.json").read_text())
    out_tensors = [t for t in sim_cfg.get("tensors", []) if t.get("role") == "output"]
    assert len(out_tensors) == 1, f"输出张量数 {len(out_tensors)} ≠ 1"
    out_bank, out_addr = int(out_tensors[0]["bank"]), int(out_tensors[0].get("addr", 0))
    report["observations"]["output_region"] = {"bank": out_bank, "addr": out_addr,
                                               "words": n_eff}

    # ---------------- S1：行数自洽 ----------------
    s1_detail, s1_ok = {}, True
    for name in sorted(set(rows_a) | set(rows_b)):
        ra, rb = rows_a.get(name), rows_b.get(name)
        entry = {"rows_stage0": ra, "rows_stage1": rb}
        if ra is None or rb is None:
            entry["ok"] = False
            s1_ok = False
        else:
            diff = ra - rb
            if m0 == m1:
                # 等长两段（如 n=480 的 60+60）：行数差恒 0，II/span 无法由差反推，
                # 退化为「两段行数全等」断言（地址差异由 S2/C3 承担）。
                entry.update(ok=(ra == rb), note="等长两段：行数应全等")
                s1_ok &= ra == rb
            elif diff % (m0 - m1) != 0 or diff <= 0:
                entry.update(ok=False, note=f"行差 {diff} 不被 (m0−m1)={m0 - m1} 整除")
                s1_ok = False
            else:
                ii = diff // (m0 - m1)
                span = (rb - 2) // ii - 1 if ii else -1
                ok = ii >= 1 and span >= 0 and (rb - 2) % ii == 0
                entry.update(ok=ok, II=ii, span=span)
                s1_ok &= ok
        s1_detail[name] = entry
    s1_detail["rtovr-14 顶格复核"] = {"rows_stage0": rows_a.get("iq_rtovr-14.hex"),
                                      "expect": 128,
                                      "ok": rows_a.get("iq_rtovr-14.hex") == 128}
    s1_ok &= s1_detail["rtovr-14 顶格复核"]["ok"]
    report["checks"]["S1_row_count_consistency"] = {"pass": s1_ok, "detail": s1_detail}
    print(f"[recon] S1 行数自洽: {'PASS' if s1_ok else 'FAIL'}")

    # ---------------- S2：静态地址（发射产物直读） ----------------
    def y_write_addrs(build: Path):
        out = {}
        pj = json.loads((build / "physical_instructions.json").read_text())
        for ins in pj:
            pay = ins.get("payload") or {}
            if pay.get("BANK") == out_bank and "ADDR" in pay:
                out.setdefault(ins["iq_name"], set()).add(int(pay["ADDR"]))
        return {k: sorted(v) for k, v in out.items()}

    addr_a, addr_b = y_write_addrs(build_a), y_write_addrs(build_b)
    exp0, exp1 = set(range(m0)), {m0 + i for i in range(m1)}
    got0 = set().union(*(set(v) for v in addr_a.values())) if addr_a else set()
    got1 = set().union(*(set(v) for v in addr_b.values())) if addr_b else set()
    s2_ok = got0 == exp0 and got1 == exp1
    report["checks"]["S2_static_y_write_addrs"] = {
        "pass": s2_ok,
        "detail": {"per_iq_stage0": addr_a, "per_iq_stage1": addr_b,
                   "union_stage0": sorted(got0), "union_stage1": sorted(got1),
                   "expect_stage0_subset": [0, m0], "expect_stage1": sorted(exp1)}}
    print(f"[recon] S2 静态写地址 stage0⊆[0,{m0}) stage1=={sorted(exp1)}: "
          f"{'PASS' if s2_ok else 'FAIL'}")

    # ---------------- golden：独立参考重写输出区 ----------------
    tensor_names = [k for k, c in op.tensor_configs.items() if c.get("role") in ("input", "param")]
    root = parse_ordered_formula(op.formula_str, tensor_names)
    ref_in = {k: np.asarray(v, dtype=F32) for k, v in inputs_b.items() if k in tensor_names}
    ev = rg.evaluate(root, ref_in)
    ref_y = np.asarray(ev.y, dtype=F32).reshape(-1)
    assert len(ref_y) == n
    domains = {k: (1.0, 2.0) for k in ("x", "other")}
    domains["alpha"] = (2.5, 2.5)
    st = rg.static_domain_check(root, domains)
    dyn = rg.dynamic_data_check(ev, domains, rg.leaf_paths(root))
    report["observations"]["domain_check"] = {
        "static_ok": st.ok, "dynamic_diags": len(dyn),
        "note": "与 v1_admission 同域 [1,2]/alpha=2.5；本实验作为硬检查"}

    # 输出区定位已在 run B 后完成（out_bank/out_addr，与编排器同源）
    bank2 = build_b / "smc_0" / "output_bank" / f"bank{out_bank}_final.hex"
    orig_bytes = bank2.read_bytes()
    words = [int(ln.strip(), 16) for ln in orig_bytes.decode().splitlines() if ln.strip()]
    packed = pack_words(ref_y)
    assert len(packed) == n_eff
    assert out_addr + len(packed) <= len(words), "参考输出区超 bank 词数"
    for i, w in enumerate(packed):
        words[out_addr + i] = w
    with open(bank2, "w", encoding="utf-8") as f:
        for w in words:
            f.write(f"0x{w:032x}\n")
    byte_identical = bank2.read_bytes() == orig_bytes
    report["observations"]["golden_rewrite"] = {
        "byte_identical_to_emitter_golden": byte_identical,
        "note": "op.get_golden 与独立 AST 参考一致性（run_v1_verify §C.3 同法）",
        "sha256_before": hashlib.sha256(orig_bytes).hexdigest(),
        "sha256_after": sha256_file(bank2)}

    # ---------------- 铺 case ----------------
    case = args.sandbox / "test_atomic_ops" / SUITE / args.case_name
    if case.exists():
        shutil.rmtree(case)
    (case / "smi_iq_inst" / "stage_0").mkdir(parents=True)
    (case / "smi_iq_inst" / "stage_1").mkdir(parents=True)
    for f in sorted(iq_a.glob("iq_*.hex")):
        shutil.copy2(f, case / "smi_iq_inst" / "stage_0" / f.name)
    for f in sorted(iq_b.glob("iq_*.hex")):
        shutil.copy2(f, case / "smi_iq_inst" / "stage_1" / f.name)
    shutil.copytree(build_b / "smc_0" / "ur_data", case / "smc_0" / "ur_data")
    shutil.copytree(build_b / "smc_0" / "output_bank", case / "smc_0" / "output_bank")
    report["provenance"]["case"] = str(case)
    report["provenance"]["case_stage_files"] = {
        "stage_0": sorted(f.name for f in (case / "smi_iq_inst" / "stage_0").glob("*.hex")),
        "stage_1": sorted(f.name for f in (case / "smi_iq_inst" / "stage_1").glob("*.hex"))}
    # build 快照归档（防瞬态目录被后续运行清掉）
    shutil.copytree(build_a, out_dir / "build_stage0_n240")
    shutil.copytree(build_b, out_dir / "build_stage1_tail")
    print(f"[recon] case 已铺: {case}")

    verdict_checks = [a0_ok, s1_ok, s2_ok, st.ok, not dyn]

    # ---------------- VCS + 原生 compare + 段间对账 ----------------
    if not args.run_vcs:
        report["verdict"] = "UNVERIFIED" if all(verdict_checks) else "RED"
        report["verdict_note"] = "--no-run-vcs：VCS 及段间对账未执行（不构成任何通过声明）"
    else:
        runner = args.sandbox / "sim_multi_stage" / "run_sim_aiacc_top_f_multi_stage.sh"
        case_rel = f"{SUITE}/{args.case_name}/smc_0"
        stdout_path = out_dir / "vcs_runner_stdout.log"
        with open(stdout_path, "w") as fh:
            proc = subprocess.run(["bash", str(runner), case_rel],
                                  stdout=fh, stderr=subprocess.STDOUT,
                                  timeout=args.timeout, cwd=args.sandbox)
        report["observations"]["vcs_runner_exit"] = proc.returncode
        verdict_checks.append(proc.returncode == 0)
        simv = args.sandbox / "sim_output_aiacc_top_f_multi_stage" / "simv"
        report["provenance"]["simv_sha256"] = sha256_file(simv) if simv.is_file() else None

        # 证据拷贝
        logs_root = args.sandbox / "arith_regression" / "test_log" / "multi_stage"
        sim_log_src = logs_root / SUITE / args.case_name / "sim.log"
        sim_log = out_dir / "sim.log"
        if sim_log_src.is_file():
            shutil.copy2(sim_log_src, sim_log)
        for name in ("iq_probe_trace.log", "iq_issue_trace.log", "ur_write_trace.log"):
            src = case / name
            if src.is_file():
                shutil.copy2(src, out_dir / name)

        # 原生 compare 独立重跑（结构化退出码）
        cmp_py = args.sandbox / "sim_multi_stage" / "compare_ur_to_golden_multi_stage.py"
        cmp_proc = subprocess.run(
            [sys.executable, str(cmp_py),
             "--test-atomic-ops", str(args.sandbox / "test_atomic_ops"),
             "--only-relpath", case_rel, "--log", str(out_dir / "native_compare.log")],
            capture_output=True, text=True)
        (out_dir / "native_compare_stdout.log").write_text(
            cmp_proc.stdout + cmp_proc.stderr, encoding="utf-8")
        c5a_ok = cmp_proc.returncode == 0
        report["checks"]["C5a_native_compare"] = {"pass": c5a_ok,
                                                  "exit": cmp_proc.returncode}

        # 段间对账（TB @probe_cycle 与写侧日志共享计数器）
        tb_marks = []
        if sim_log.is_file():
            for ln in sim_log.read_text(errors="replace").splitlines():
                m = re.search(r"\[TB_MULTI_STAGE\] (===== stage_(\d+)/\d+ BEGIN ====="
                              r"|stage_(\d+) done \(intr cleared\)"
                              r"|batch interrupt @ cycle \d+|all stages complete)"
                              r" @time (\d+) @probe_cycle (\d+)", ln)
                if m:
                    tb_marks.append({"line": ln.strip(), "time_ns": int(m.group(4)),
                                     "cycle": int(m.group(5))})
        report["observations"]["tb_stage_marks"] = tb_marks
        begins = [m for m in tb_marks if "BEGIN" in m["line"]]
        acks = [m for m in tb_marks if "stage_0 done" in m["line"]]

        writes = []
        wt = out_dir / "ur_write_trace.log"
        if wt.is_file():
            for ln in wt.read_text().splitlines():
                if ln.startswith("#") or not ln.strip():
                    continue
                cyc, smc, bank, we, addr = ln.split()[:5]
                if int(smc) == 0 and int(bank) == out_bank and int(we) == 1:
                    writes.append((int(cyc), int(addr)))
        report["observations"]["smc0_bank2_write_count"] = len(writes)

        if len(begins) == 2 and acks:
            b0, b1 = begins[0]["cycle"], begins[1]["cycle"]
            ack0 = acks[0]["cycle"]
            w0 = [w for w in writes if w[0] < b1]
            w1 = [w for w in writes if w[0] >= b1]
            c1_ok = bool(w0) and bool(w1) and b0 < ack0 <= b1 and max(c for c, _ in w0) < ack0
            report["checks"]["C1_stage_order"] = {
                "pass": c1_ok,
                "detail": {"stage0_begin": b0, "stage1_begin": b1,
                           "stage0_last_write": max((c for c, _ in w0), default=None),
                           "stage1_first_write": min((c for c, _ in w1), default=None)}}
            resid = [w for w in writes if ack0 <= w[0] < b1]
            report["checks"]["C2_no_residual_write_after_ack"] = {
                "pass": len(resid) == 0,
                "detail": {"stage0_ack_cycle": ack0, "stage1_begin_cycle": b1,
                           "window_cycles": b1 - ack0, "vacuous_window": b1 == ack0,
                           "residual_writes": resid[:20]}}
            a0set = {a for _, a in w0}
            a1set = {a for _, a in w1}
            c3_ok = len(w0) == len(exp0) and len(w1) == len(exp1) and a0set == exp0 and a1set == exp1 and not (a0set & a1set) \
                and (a0set | a1set) == (exp0 | exp1)
            report["checks"]["C3_rtl_addr_continuity"] = {
                "pass": c3_ok,
                "detail": {"stage0_addrs_sorted": sorted(a0set),
                           "stage1_addrs_sorted": sorted(a1set),
                           "union_covers_0_to_60": (a0set | a1set) == (exp0 | exp1)}}
            verdict_checks += [c1_ok, len(resid) == 0, c3_ok]
        else:
            verdict_checks.append(False)
            report["checks"]["C1_stage_order"] = {
                "pass": False,
                "detail": f"TB 阶段标记缺失（begins={len(begins)} acks={len(acks)}）——"
                          f"无法切段，段间对账不成立"}

        # C5b：独立解包比对（不依赖 sandbox compare）
        ur_res = case / "smc_0" / f"ur_result_smc_0_bank{out_bank}.hex"
        if ur_res.is_file():
            res_words = [int(ln.strip(), 16)
                         for ln in ur_res.read_text().splitlines() if ln.strip()]
            lanes_got = []
            for w in res_words[out_addr:out_addr+n_eff]:
                for i in range(4):
                    lanes_got.append((w >> (i * 32)) & 0xFFFFFFFF)
            ref_bits = [int(np.float32(v).view(np.uint32)) for v in ref_y]
            mism = [(i, g, r) for i, (g, r) in enumerate(zip(lanes_got, ref_bits)) if g != r]
            c5b_ok = len(mism) == 0 and len(lanes_got) >= n
            report["checks"]["C5b_independent_bit_compare"] = {
                "pass": c5b_ok, "points": n,
                "mismatch_count": len(mism), "first_mismatches": mism[:5]}
            verdict_checks.append(c5b_ok)
        else:
            report["checks"]["C5b_independent_bit_compare"] = {
                "pass": False, "detail": f"缺 {ur_res}"}
            verdict_checks.append(False)

        # C4 观察：两段各队列 ISSUE 事件计数（仅记录，不裁决）
        iss = out_dir / "iq_issue_trace.log"
        if iss.is_file() and len(begins) == 2:
            per = {"stage0": {}, "stage1": {}}
            for ln in iss.read_text().splitlines():
                if ln.startswith("#") or not ln.strip():
                    continue
                cyc, iq, vld = ln.split()[:3]
                if int(vld) != 1:
                    continue
                seg = "stage0" if int(cyc) < begins[1]["cycle"] else "stage1"
                per[seg][iq] = per[seg].get(iq, 0) + 1
            report["observations"]["C4_issue_event_counts"] = per

        verdict_checks.append(c5a_ok)
        report["verdict"] = "GREEN" if all(verdict_checks) else "RED"
        report["verdict_note"] = (
            "实验裁决：GREEN = A0/S1/S2 + C1/C2/C3 + C5a/C5b 全过；"
            "**仍不构成 V1 证据**（ADR-057 验收①：未接入正式链前仅记实验）")

    # 归档
    (out_dir / "recon_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8")
    sums = []
    for f in sorted(out_dir.rglob("*")):
        if f.is_file() and f.name != "SHA256SUMS.txt":
            sums.append(f"{sha256_file(f)}  {f.relative_to(out_dir)}")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"[recon] verdict = {report['verdict']}  报告: {out_dir / 'recon_report.json'}")
    return 0 if report["verdict"] != "RED" else 1


if __name__ == "__main__":
    sys.exit(main())
