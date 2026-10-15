#!/usr/bin/env python3
"""run_unified_lcs_verify —— ADR-061：scale_sum 统一入口（LOAD→COMPUTE→STORE
自动完整链路）验证运行器。

复用 run_v1_verify.Orchestrator 的既有阶段（config_check /
formula_domain_check / compile_admit / independent_reference / artifact_audit /
native_compare / archive / publish——COMPUTE 链与检查零改动），替换四段：

  package     → _stage_unified_package：布局驱动装配完整程序 case
                （unified_lcs_case_gen）+ program manifest 独立检查
                （check_program_manifest）——不过即 RED 不发布；
  vcs         → _stage_unified_vcs：flock 串行占用共享 HBM 预载目录 +
                备份/复核 → hbm_preload.restore（快照↔expect 双锚）→
                EXTRA_PLUSARGS 全屏障（信用/WBARRIER/S4）跑 VCS →
                无条件恢复备份并复核（任务 1 纪律平移）；
  three_zone  → _stage_unified_checks：unified_lcs_compare（Z3 三区泛化
                LOAD-aware + C-H HBM 窗/独立参考/STORE 映射三方一致）；
  arbitrate   → _stage_unified_arbitrate：arbitrate_program（程序级
                C-L/C-S/C-W + COMPUTE 段逐拍复用）。

报告口径（D7）：sync_model=tb_assisted、TB 路径+sha256、RTL 版本板端期望值
标注全部入 provenance；不宣称 host 自主闭环；STORE 外部完成仍受 F6 线既知
边界限制（S4 屏障=TB 辅助排干佐证，非硬件契约）。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "verification_v2" / "tools"))

import arbitrate_probe_logs as arb  # noqa: E402
import hbm_preload  # noqa: E402
from check_program_manifest import Checker as ManifestChecker  # noqa: E402
from run_v1_verify import (  # noqa: E402
    Orchestrator, RunConfig, SizeContext, StageFailure, Unverified,
    sha256_file)
from unified_lcs_case_gen import build_unified_case  # noqa: E402
from unified_lcs_compare import Compare as LcsCompare  # noqa: E402

HBM_INIT_FILES = [f"memory_init{i}.hex" for i in range(1, 9)]


class UnifiedOrchestrator(Orchestrator):
    def __init__(self, cfg: RunConfig):
        super().__init__(cfg)
        self.stages["package"] = self._stage_unified_package
        self.stages["vcs"] = self._stage_unified_vcs
        self.stages["three_zone"] = self._stage_unified_checks
        self.stages["arbitrate"] = self._stage_unified_arbitrate

    # ------------------------------------------------------------------
    def _stage_unified_package(self, sctx: SizeContext) -> None:
        case = self.cfg.sandbox / "test_atomic_ops" / self.cfg.suite / sctx.case_name
        tb = self.cfg.sandbox / "sim_multi_stage" / "tb_aiacc_top_v04_f_multi_stage.v"
        try:
            op = self._get_operator(sctx)
            manifest = build_unified_case(
                sctx.build_copy, case, sctx.n, tb,
                op_name=Path(self.cfg.op_yaml).stem,
                formula=op.formula_str,
                partition=self.cfg.partition)
        except SystemExit as e:
            raise StageFailure(["TYPE_LAYOUT_UNSUPPORTED"],
                               f"统一 case 装配响亮拒绝: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"],
                               f"统一 case 装配异常: {type(e).__name__}: {e}") from e
        chk = ManifestChecker(case)
        rc = chk.run()
        (sctx.ev(f"n{sctx.n}_program_manifest_check.log")).write_text(
            "\n".join(chk.bad) + ("\n" if chk.bad else "") or "GREEN\n",
            encoding="utf-8")
        if rc != 0:
            raise StageFailure(
                ["PACKAGE_OR_EMISSION_ERROR"],
                f"program manifest 独立检查 RED（{len(chk.bad)} 项）: "
                + " ; ".join(chk.bad[:6]),
                logs={"program_manifest": str(case / "program_manifest.json")})
        sctx.case_root = case
        sctx.case_rel = f"{self.cfg.suite}/{sctx.case_name}/smc_0"
        # D5：程序 manifest 与屏障/双锚 expect 为一等证据——拷入 evidence
        # （run 级 SHA256SUMS 自动覆盖），不留沙箱唯一副本；拷贝后立即
        # 逐字节哈希复核，不符即响亮失败（不静默降级）
        for name in ("program_manifest.json", "g3_expect.json"):
            dst = sctx.ev(f"n{sctx.n}_{name}")
            shutil.copy2(case / name, dst)
            if sha256_file(dst) != sha256_file(case / name):
                raise StageFailure(
                    ["PACKAGE_OR_EMISSION_ERROR"],
                    f"证据拷贝复核不符: {name}——run 包不自包含，人工核查")
        self.provenance["unified_lcs"] = {
            "entry": "run_unified_lcs_verify.py (ADR-061)",
            "sync_model": "tb_assisted",
            "tb_path": str(tb),
            "tb_sha256": sha256_file(tb),
            "rtl_version_expected_board":
                manifest.get("rtl_version_expected_board"),
            "rtl_version_note": manifest["tb"]["note"],
            "program_manifest_schema": manifest["schema_version"],
            "program_manifest_sha256":
                sha256_file(case / "program_manifest.json"),
            "completion_boundary":
                "STORE 外部完成 = TB 辅助屏障（WBARRIER/S4）排干佐证，"
                "非硬件完成契约（F6 线既知边界）；不宣称 host 自主闭环",
        }

    # ------------------------------------------------------------------
    def _stage_unified_vcs(self, sctx: SizeContext) -> None:
        if not self.cfg.run_vcs:
            raise Unverified("VCS_NOT_RUN",
                             "run_vcs=false——VCS 及其后裁决未执行，明确报告"
                             "未能验证（不输出 GREEN、不发布）")
        hbm_dir = self.cfg.sandbox / "data_engine" / "sram"
        lock_path = hbm_dir / ".ulcs_lcs.flock"
        backup_dir = sctx.ev(f"n{sctx.n}_hbm_backup")
        backup_dir.mkdir(parents=True, exist_ok=True)
        hbm_dir.mkdir(parents=True, exist_ok=True)
        anchor: dict = {}
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            # 备份全局预载（含缺席标记）+ sha256 锚
            anchor = {}
            for name in HBM_INIT_FILES:
                src = hbm_dir / name
                if src.is_file():
                    shutil.copy2(src, backup_dir / name)
                    anchor[name] = sha256_file(src)
                else:
                    (backup_dir / f"{name}.ABSENT").touch()
                    anchor[name] = None
            (backup_dir / "SHA256SUMS.before.json").write_text(
                json.dumps(anchor, indent=2) + "\n", encoding="utf-8")
            rc = hbm_preload.restore(str(sctx.case_root), str(hbm_dir))
            if rc != 0:
                raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"],
                                   "hbm_preload restore 拒跑（快照↔expect 双锚"
                                   "不符或复制复核失败）——冻结证据须人工核查")
            exp = json.loads((sctx.case_root / "g3_expect.json")
                             .read_text(encoding="utf-8"))
            bar, wb, s4 = exp["barrier"], exp["wbarrier"], exp["s4barrier"]
            plus = (f"+G3_BARRIER=1 +G3_BARRIER_LDB={bar['exp_ldb_done']}"
                    f" +G3_BARRIER_LDBAXI={bar['exp_ldbaxi_done']}"
                    f" +G3_BARRIER_TIMEOUT={bar['timeout_cycles']}"
                    f" +G3_WBARRIER=1 +G3_WBARRIER_BANK={wb['bank']}"
                    f" +G3_WBARRIER_WRITES={wb['writes']}"
                    f" +G3_WBARRIER_STAGE={wb['stage']}"
                    f" +G3_WBARRIER_TIMEOUT={wb['timeout_cycles']}"
                    f" +G3_S4_BARRIER=1 +G3_S4_ADDR0={s4['addr0']}"
                    f" +G3_S4_WRITES={s4['writes']} +G3_S4_BRESP={s4['bresp']}"
                    f" +G3_S4_TIMEOUT={s4['timeout_cycles']}")
            # 路径 A（2026-10-14）：配置可选的附加 plusargs（默认空 ⇒ 既有运行
            # 逐比特同形）。由 run_v1_verify.RunConfig 校验为 '+' 前缀字符串列表，
            # 仅 TB 侧已声明的显式实验开关（全目标门控 / 延迟注入 / 负控）。
            for _pa in getattr(self.cfg, "extra_plusargs", []) or []:
                plus += f" {_pa}"
            runner = self.cfg.sandbox / "sim_multi_stage" \
                / "run_sim_aiacc_top_f_multi_stage.sh"
            stdout_path = sctx.ev(f"n{sctx.n}_vcs_runner_stdout.log")
            env = dict(os.environ, EXTRA_PLUSARGS=plus)
            cmd = ["bash", str(runner), sctx.case_rel]
            try:
                with open(stdout_path, "w") as fh:
                    proc = subprocess.run(cmd, stdout=fh,
                                          stderr=subprocess.STDOUT, env=env,
                                          timeout=self.cfg.vcs_timeout_s)
            except subprocess.TimeoutExpired:
                logs = self._collect_vcs_evidence(sctx, stdout_path)
                raise StageFailure(
                    ["SIM_TIMEOUT_OR_HANG"],
                    f"VCS 墙钟超时（>{self.cfg.vcs_timeout_s}s）: {sctx.case_rel}",
                    logs=logs, exit_code=None)
            logs = self._collect_vcs_evidence(sctx, stdout_path)
            (sctx.ev(f"n{sctx.n}_tb_plusargs.txt")).write_text(plus + "\n",
                                                               encoding="utf-8")
            if proc.returncode != 0:
                sim_log = Path(logs.get("sim_log", ""))
                sig_timeout = sim_log.is_file() and \
                    "smi_wait_and_clear_intr timeout" in sim_log.read_text(
                        errors="replace")
                code = "SIM_TIMEOUT_OR_HANG" if sig_timeout else "SIM_RUNTIME_ERROR"
                raise StageFailure(
                    [code],
                    f"VCS runner 非零退出（simv/TB/屏障/compare 任一关未过）: "
                    f"{sctx.case_rel}",
                    logs=logs, exit_code=proc.returncode)
            sctx.vcs_logs = logs
        finally:
            # 无条件恢复备份并复核（中断/失败路径同走——任务 1 纪律）
            for name in HBM_INIT_FILES:
                dst = hbm_dir / name
                if anchor.get(name) is not None:
                    shutil.copy2(backup_dir / name, dst)
                    if sha256_file(dst) != anchor[name]:
                        print(f"[ULCS-VCS][WARN] 恢复后复核不符: {name}——人工核查",
                              file=sys.stderr)
                elif dst.is_file():
                    dst.unlink()
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _collect_vcs_evidence(self, sctx: SizeContext,
                              runner_stdout: Path) -> dict:
        out = super()._collect_vcs_evidence(sctx, runner_stdout)
        if sctx.case_root and sctx.case_root.is_dir():
            for name in ("ur_read_trace.log", "hbm_write_trace.log",
                         "stb_ur_read_trace.log",
                         # 路径 A（2026-10-14）全目标观测日志：逐 SMC 读/写、
                         # 逐位置信用链、逐通道写响应。缺席即缺席（is_file 判）
                         # ——原树 TB 无这些文件时行为不变。
                         "pa_ur_wr_trace.log", "pa_ur_rd_trace.log",
                         "pa_stb_rd_trace.log", "pa_crd_trace.log",
                         "pa_bresp_trace.log"):
                src = sctx.case_root / name
                if src.is_file():
                    dst = sctx.ev(f"n{sctx.n}_{name}")
                    shutil.copy2(src, dst)
                    out[f"probe_{name}"] = sctx.rel(dst)
        return out

    # ------------------------------------------------------------------
    def _stage_unified_checks(self, sctx: SizeContext) -> None:
        if sctx.stage_plan:
            instr = [sctx.build_copy / "stages" / f"stage_{st['stage_id']}"
                     / "physical_instructions.json"
                     for st in sctx.stage_plan["stages"]]
        else:
            instr = [sctx.build_copy / "physical_instructions.json"]
        for p in instr:
            if not p.is_file():
                raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"],
                                   f"缺 physical_instructions.json: {p}")
        cmp_ = LcsCompare(sctx.case_root, instr)
        rc = cmp_.run()
        cmp_.report["violations"] = cmp_.bad
        cmp_.report["verdict"] = "RED" if cmp_.bad else "GREEN"
        out_path = sctx.ev(f"n{sctx.n}_unified_lcs_compare.json")
        out_path.write_text(json.dumps(cmp_.report, indent=2,
                                       ensure_ascii=False) + "\n",
                            encoding="utf-8")
        if rc == 2:
            raise StageFailure(["SIM_RUNTIME_ERROR"],
                               "统一比对输入缺失/坏（exit=2）",
                               logs={"unified_lcs_compare": sctx.rel(out_path)},
                               exit_code=2)
        if rc != 0:
            raise StageFailure(["NUMERIC_MISMATCH"],
                               f"统一比对 RED（{len(cmp_.bad)} 项）: "
                               + " ; ".join(cmp_.bad[:6]),
                               logs={"unified_lcs_compare": sctx.rel(out_path)},
                               exit_code=1)

    # ------------------------------------------------------------------
    def _stage_unified_arbitrate(self, sctx: SizeContext) -> None:
        manifest = json.loads((sctx.case_root / "program_manifest.json")
                              .read_text(encoding="utf-8"))
        exp_compute = {}
        for st in manifest["stages"]:
            if st["kind"] != "COMPUTE":
                continue
            p = sctx.evidence_dir / st["expected_events_file"]
            if not p.is_file():
                raise StageFailure(["PER_BEAT_PROTOCOL_MISMATCH"],
                                   f"缺 COMPUTE 期望事件文件: {p}")
            exp_compute[st["stage_id"]] = json.loads(p.read_text(encoding="utf-8"))
        sim_log = sctx.evidence_dir / f"n{sctx.n}_sim_log.log"
        try:
            report = arb.arbitrate_program(sctx.case_root, manifest,
                                           exp_compute, sim_log)
        except Exception as e:  # noqa: BLE001
            raise StageFailure(["PER_BEAT_PROTOCOL_MISMATCH"],
                               f"程序仲裁器异常（探针日志/段标记坏）: "
                               f"{type(e).__name__}: {e}") from e
        sctx.arb_limitations = list(report["limitations"])
        (sctx.ev(f"n{sctx.n}_arbitrate_report.json")).write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        for lim in sctx.arb_limitations:
            print(f"[ARBITRATE-LIMITATION] {lim}")
        if report.get("verdict") != "GREEN":
            raise StageFailure(
                ["PER_BEAT_PROTOCOL_MISMATCH"],
                f"程序仲裁 RED（{len(report.get('violations', []))} 违例）: "
                + " ; ".join(str(v) for v in report.get("violations", [])[:8]),
                logs={"arbitrate_report": sctx.rel(
                    sctx.evidence_dir / f"n{sctx.n}_arbitrate_report.json")})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, required=True,
                    help="RunConfig YAML/JSON（键集 = run_v1_verify CONFIG_KEYS）")
    a = ap.parse_args()
    import yaml
    raw = yaml.safe_load(a.config.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        print(f"[ULCS-RUN] 配置须为映射: {a.config}", file=sys.stderr)
        return 1
    cfg = RunConfig(raw)
    orch = UnifiedOrchestrator(cfg)
    report = orch.run()
    print(f"[ULCS-RUN] outcome={report['outcome']} published={report['published']}"
          f" run_dir={report['run']['run_dir']}")
    return 0 if report["outcome"] == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
