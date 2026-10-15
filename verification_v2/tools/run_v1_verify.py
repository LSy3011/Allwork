#!/usr/bin/env python3
"""ADR-056 步④-3 统一入口编排器：一次配置驱动的完整验证链。

链路（§D.1 契约，用户 2026-09-29 规格原文顺序）
----------------------------------------------
配置校验 → 有序公式与数值检查 → 编译及结构准入 → 独立参考 → 产物审计 →
打包 → VCS → 原生比对、三区与逐拍裁决 → 归档 → 成功后发布

四项纪律（同规格原文）
----------------------
* **运行隔离**：每次运行独立目录 ``build/v1_runs/<op>/<run_id>/``；固定输入
  （inputs.npz 逐位入档）、配置（resolved config + sha256）、代码（git HEAD/dirty +
  关键文件 sha256）及协议模型版本（注册表封印）。编译瞬态目录
  ``build/verification_v2/<op>`` 每规模**先整体删除再重建**，产物即刻快照入 run 目录，
  审计/打包只读 run 目录副本；VCS case 名 run 专属且打包 clean=True
  （rmtree 全 case 目录，旧探针日志不可残留）。**禁止混用旧日志或旧产物。**
* **失败传递**：保留 §D.2 的 11 类失败代码 + 失败阶段 + 原始退出码 + 日志路径；
  首个失败即 fail-fast（跨规模亦停），立即阻止发布；**不能只凭日志中的 PASSED
  判断成功**——原生比对由本编排器**独立重跑** compare 并取结构化退出码，三区/仲裁
  同理取结构化裁决。
* **边界检查**：当前模型仅支持**无反压展开形态**。非 unroll 分支、未运行 VCS、
  及其他不能裁决的情形 → outcome=**UNVERIFIED**（明确报告未能验证，**不输出
  GREEN、不发布**）；请求 flat 路径时 ISSUE 实证缺口照旧挂账（UNROLL_NOT_REQUESTED）。
* **发布控制**：所有必需裁决通过（outcome=GREEN）后才物化成功包（staging→rename）；
  失败运行只保留诊断材料（run 目录全留），**不更新成功包、不留半截包**。
  发布通道按 domain_mode 严格分列（用户 2026-09-30 规则）：
  ``v1_admission`` GREEN → ``releases/<op>_<run_id>/``（V1 已验证包）；
  ``frozen_regression`` GREEN → ``releases/regression/<op>_<run_id>/``
  （**历史基线回归通过包，报告 verification_class 标为 frozen_baseline_regression，
  不构成、也不得混同为 V1 准入成功证据**）。

outcome 枚举：GREEN（全部必需检查通过）/ RED（失败，failure_code ∈ 11 码或
编排级非 11 码 CONFIG_INVALID / REGISTRY_SEAL_MISMATCH / UNCLASSIFIED_INTERNAL_ERROR）
/ UNVERIFIED（未裁决，reason_code 见报文）。预检/编排级错误不属于 §D.2 验证失败码，
但其一律 RED 并阻断发布——本决策已在步④-3 报告中向用户明示。

用法
----
    python3 verification_v2/tools/run_v1_verify.py \
        --op-yaml verification_v2/ops/add.yaml --n-rows 4,8,60 \
        --domain-mode frozen_regression
或（§D.1 契约壳）verification_v2/tools/run_v1_formula_flow.sh <op_yaml> --n-rows a,b,c
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO_ROOT))
sys.path.append(str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "verification_v2" / "tools"))

from verification_v2.ops.configurable import ConfigurableOperator            # noqa: E402
from verification_v2.reference.ordered_ast import (                          # noqa: E402
    FormulaRejection, parse_ordered_formula)
from verification_v2.reference import reference_generator as rg              # noqa: E402
from verification_v2.infra.pipeline_runner import (                          # noqa: E402
    PipelineRunner, SynthesisRejected)
from verification_v2.infra.auditor import Auditor                            # noqa: E402
from verification_v2.infra import protocol_registry as pr_mod                # noqa: E402
import emit_expected_fire_sequence as emit_fx                                # noqa: E402
import arbitrate_probe_logs as arb                                           # noqa: E402
from package_vcs_case import package as package_case                         # noqa: E402
from package_vcs_case import package_multi as package_case_multi             # noqa: E402
import check_stage_manifest as manifest_ck                                   # noqa: E402

F32 = np.float32

# ---------------------------------------------------------------------------
# §D.2 失败码与裁决枚举
# ---------------------------------------------------------------------------

FAILURE_CODES_11 = frozenset([
    "SYNTAX_UNSUPPORTED", "BASE_OP_OUT_OF_SCOPE", "TYPE_LAYOUT_UNSUPPORTED",
    "SOLVER_UNSAT", "SOLVER_UNKNOWN", "STATIC_ADMISSION_REJECTED",
    "PACKAGE_OR_EMISSION_ERROR", "SIM_RUNTIME_ERROR", "SIM_TIMEOUT_OR_HANG",
    "PER_BEAT_PROTOCOL_MISMATCH", "NUMERIC_MISMATCH",
])
# 编排级非 11 码（预检/模型完整性/未分类内部错误）——同样 RED + 阻断发布，
# 与 §D.2 验证失败码分列，不混称。
ORCH_FAILURE_CODES = frozenset([
    "CONFIG_INVALID", "REGISTRY_SEAL_MISMATCH", "UNCLASSIFIED_INTERNAL_ERROR",
])
OUTCOMES = ("GREEN", "RED", "UNVERIFIED")

STAGE_ORDER = [
    "config_check", "formula_domain_check", "compile_admit",
    "independent_reference", "artifact_audit", "package", "vcs",
    "native_compare", "three_zone", "arbitrate",
]
# archive / publish 是全局阶段（跨规模收口），不入逐规模 STAGE_ORDER。

REPORT_SCHEMA = "adr056_f3_run_report_v1"
CONFIG_KEYS = frozenset([
    "op_yaml", "n_rows", "domain_mode", "hw_config", "suite", "sandbox",
    "vcs_timeout_s", "case_prefix", "run_vcs", "runs_root", "releases_root",
    "run_id", "partition", "extra_plusargs",
])
DOMAIN_MODES = ("v1_admission", "frozen_regression")
# frozen_regression 下允许放行（记录为设计内证据）的静态保守拒类别
FROZEN_STATIC_TOLERATED = frozenset(["SUBNORMAL_BAND"])

# 提取契约：[CODE] 或 [CODE1|CODE2]，允许一个 "/注解" 尾巴（如 assembler L2 闸门的
# "[STATIC_ADMISSION_REJECTED/G-cap]"）——注解只是定位信息，提取时丢弃，只认 §D.2 码。
_CODE_RE = re.compile(r"\[([A-Z0-9_]+(?:\|[A-Z0-9_]+)*)(?:/[A-Za-z0-9_-]+)?\]")


# ---------------------------------------------------------------------------
# 异常载体
# ---------------------------------------------------------------------------

class ConfigInvalid(Exception):
    """配置/调用面格式性错误（非 §D.2 验证失败码；outcome=RED，阻断发布）。"""


class StageFailure(RuntimeError):
    """阶段失败：携带 §D.2 码（或编排级码）、原始退出码与日志路径。"""

    def __init__(self, codes: List[str], detail: str,
                 logs: Optional[Dict[str, str]] = None,
                 exit_code: Optional[int] = None):
        self.codes = list(codes)
        self.detail = detail
        self.logs = dict(logs or {})
        self.exit_code = exit_code
        super().__init__(f"[{'|'.join(self.codes)}] ({self.detail[:200]})")


class Unverified(RuntimeError):
    """当前模型不支持/未执行裁决 → outcome=UNVERIFIED（不显绿、不发布）。"""

    def __init__(self, reason_code: str, detail: str):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"[UNVERIFIED/{reason_code}] {detail[:200]}")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class RunConfig:
    """配置驱动：YAML/CLI → 校验后定值；allowlist 外键即 CONFIG_INVALID。"""

    def __init__(self, raw: Dict[str, Any]):
        unknown = set(raw) - CONFIG_KEYS
        if unknown:
            raise ConfigInvalid(f"配置含未知键 {sorted(unknown)}（allowlist={sorted(CONFIG_KEYS)}）")
        self.op_yaml = Path(str(raw.get("op_yaml") or ""))
        if not self.op_yaml.is_file():
            raise ConfigInvalid(f"op_yaml 不存在: {self.op_yaml}")
        self.op_yaml = self.op_yaml.resolve()
        self.op_name = self.op_yaml.stem

        n_rows = raw.get("n_rows")
        if not isinstance(n_rows, list) or not n_rows \
                or any((not isinstance(n, int)) or isinstance(n, bool) or n <= 0
                       for n in n_rows):
            raise ConfigInvalid(f"n_rows 须为非空正 int 列表: {n_rows!r}")
        self.n_rows = list(n_rows)

        self.domain_mode = raw.get("domain_mode", "v1_admission")
        if self.domain_mode not in DOMAIN_MODES:
            raise ConfigInvalid(f"domain_mode ∈ {DOMAIN_MODES}: {self.domain_mode!r}")

        self.hw_config = str(raw.get("hw_config", "hw_config"))
        self.suite = str(raw.get("suite", "multi_inst_stage"))
        self.sandbox = Path(str(raw.get("sandbox", Path.home() / "u280_sim_v422"))).resolve()
        self.vcs_timeout_s = int(raw.get("vcs_timeout_s", 1800))
        if self.vcs_timeout_s <= 0:
            raise ConfigInvalid(f"vcs_timeout_s 须为正: {self.vcs_timeout_s}")
        self.case_prefix = str(raw.get("case_prefix", "compiler"))
        self.run_vcs = bool(raw.get("run_vcs", True))
        self.partition = raw.get("partition", None)
        if self.partition is not None:
            if self.partition not in ("equal_8smc",):
                raise ConfigInvalid(
                    f"partition={self.partition!r} 未知（仅统一 LCS Orchestrator"
                    f" 支持 equal_8smc；缺省=单 SMC）")
            # partition 语义下 n_rows 元素 = 每 SMC 分片元素数 n_per
        self.runs_root = Path(str(raw.get("runs_root", REPO_ROOT / "build" / "v1_runs"))).resolve()
        self.releases_root = Path(str(
            raw.get("releases_root", REPO_ROOT / "verification_v2" / "artifacts" / "releases"))).resolve()
        self.run_id = str(raw.get("run_id") or self._default_run_id())

        # 附加仿真 plusargs（默认空 = 与既有运行逐比特同形；仅供 TB 侧已声明的
        # 显式实验开关使用，如路径 A 的全目标门控 +PA_FULLTARGET=1 / 延迟注入
        # +PA_DLY_*）。逐项校验前缀，避免误传非 plusarg 词被 runner 分词。
        extra = raw.get("extra_plusargs", []) or []
        if not isinstance(extra, list) or any(
                (not isinstance(x, str)) or (not x.startswith("+")) for x in extra):
            raise ConfigInvalid(
                f"extra_plusargs 须为以 '+' 开头的字符串列表: {extra!r}")
        self.extra_plusargs = list(extra)

        if self.run_vcs:
            runner_sh = self.sandbox / "sim_multi_stage" / "run_sim_aiacc_top_f_multi_stage.sh"
            if not runner_sh.is_file():
                raise ConfigInvalid(f"VCS runner 不存在: {runner_sh}")

    @staticmethod
    def _default_run_id() -> str:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"{ts}_{uuid.uuid4().hex[:6]}"

    def resolved_dict(self) -> Dict[str, Any]:
        return {
            "op_yaml": str(self.op_yaml), "n_rows": self.n_rows,
            "domain_mode": self.domain_mode, "hw_config": self.hw_config,
            "suite": self.suite, "sandbox": str(self.sandbox),
            "vcs_timeout_s": self.vcs_timeout_s, "case_prefix": self.case_prefix,
            "run_vcs": self.run_vcs, "runs_root": str(self.runs_root),
            "releases_root": str(self.releases_root), "run_id": self.run_id,
            "extra_plusargs": list(self.extra_plusargs),
        }

    def digest(self) -> str:
        blob = json.dumps(self.resolved_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 通用小具
# ---------------------------------------------------------------------------

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def bits_equal(a: np.ndarray, b: np.ndarray) -> bool:
    a = np.asarray(a, dtype=F32).reshape(-1)
    b = np.asarray(b, dtype=F32).reshape(-1)
    return a.shape == b.shape and bool(np.array_equal(a.view(np.uint32), b.view(np.uint32)))


def codes_from_value_error(exc: BaseException) -> List[str]:
    """从 ValueError 消息提取 [CODE] / [CODE1|CODE2] 前缀中的 §D.2 码。"""
    head = str(exc)[:400]
    for m in _CODE_RE.finditer(head):
        codes = [c for c in m.group(1).split("|") if c in FAILURE_CODES_11]
        if codes:
            return codes
    return []


def git_meta(repo: Path) -> Dict[str, Any]:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, timeout=20)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                               capture_output=True, text=True, timeout=20)
        return {"git_head": head.stdout.strip() or None,
                "git_dirty": bool(dirty.stdout.strip()),
                "git_rc": head.returncode}
    except Exception as e:  # noqa: BLE001
        return {"git_head": None, "git_dirty": None, "error": repr(e)}


# ---------------------------------------------------------------------------
# 阶段上下文
# ---------------------------------------------------------------------------

class SizeContext:
    """单规模运行态：跨阶段传递的中间产物 + 阶段记录 + 证据路径。"""

    def __init__(self, orch: "Orchestrator", n: int):
        self.orch = orch
        self.cfg = orch.cfg
        self.n = n
        self.dir: Path = orch.run_dir / f"n{n}"
        self.evidence_dir: Path = self.dir / "evidence"
        self.build_copy: Optional[Path] = None
        self.case_name = f"{self.cfg.case_prefix}_{self.cfg.op_name}_{self.cfg.run_id}_n{n}"
        self.case_root: Optional[Path] = None
        self.case_rel: Optional[str] = None
        # 中间产物
        self.operator: Optional[ConfigurableOperator] = None
        self.input_data: Optional[Dict[str, np.ndarray]] = None
        self.formula_root = None
        self.ref_eval: Optional[rg.Evaluation] = None
        self.ctx: Optional[Dict[str, Any]] = None          # run_full_stack ctx
        self.runner: Optional[PipelineRunner] = None
        # 记录
        self.stage_records: List[Dict[str, Any]] = []
        self.outcome: str = "GREEN"
        self.failure: Optional[Dict[str, Any]] = None
        self.unverified: Optional[Dict[str, Any]] = None
        self.vcs_logs: Optional[Dict[str, str]] = None
        # ADR-057：多段计划（单段为 None）。compile_admit 从 runner ctx 取回，
        # 其后的审计/打包/仲裁据此走逐段路径。
        self.stage_plan: Optional[Dict[str, Any]] = None
        # ADR-057 errata-2026-10-01 / evaluator N2：仲裁限制声明随 run 报告落账，
        # 顶层 verdict 载体自含（不逆转 outcome，但不得只在 stdout 出现）。
        self.arb_limitations: List[str] = []

    def ev(self, name: str) -> Path:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        return self.evidence_dir / name

    def rel(self, p: Optional[Path]) -> Optional[str]:
        if p is None:
            return None
        try:
            return str(Path(p).resolve().relative_to(REPO_ROOT))
        except ValueError:
            return str(p)


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------

# (run_dir, n, run_id 注入) 阶段签名：Callable[[SizeContext], None]
StageFn = Callable[[SizeContext], None]


class Orchestrator:
    def __init__(self, cfg: RunConfig, stages: Optional[Dict[str, StageFn]] = None):
        self.cfg = cfg
        self.run_dir: Path = cfg.runs_root / cfg.op_name / cfg.run_id
        self.stages: Dict[str, StageFn] = stages or {
            "config_check": self._stage_config_check,
            "formula_domain_check": self._stage_formula_domain_check,
            "compile_admit": self._stage_compile_admit,
            "independent_reference": self._stage_independent_reference,
            "artifact_audit": self._stage_artifact_audit,
            "package": self._stage_package,
            "vcs": self._stage_vcs,
            "native_compare": self._stage_native_compare,
            "three_zone": self._stage_three_zone,
            "arbitrate": self._stage_arbitrate,
        }
        self.provenance: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=False)
        (self.run_dir / "run_config.resolved.json").write_text(
            json.dumps(self.cfg.resolved_dict(), indent=2, sort_keys=True,
                       ensure_ascii=False) + "\n", encoding="utf-8")

        size_reports: Dict[str, Any] = {}
        first_bad: Optional[SizeContext] = None
        for n in self.cfg.n_rows:
            sctx = SizeContext(self, n)
            self._run_one_size(sctx)
            size_reports[str(n)] = self._size_report_fragment(sctx)
            if sctx.outcome != "GREEN":
                first_bad = sctx
                break                       # 失败传递：fail-fast，不跑后续规模
        # 未执行的规模显式标记（报告完整性）
        for n in self.cfg.n_rows:
            if str(n) not in size_reports:
                size_reports[str(n)] = {"n": n, "stages": [], "outcome": "NOT_RUN",
                                        "note": "前序规模失败/未裁决，fail-fast 跳过后续规模"}

        bad_outcomes = {s["outcome"] for s in size_reports.values()} - {"GREEN"}
        if first_bad is not None:
            outcome = first_bad.outcome     # RED 或 UNVERIFIED
        elif bad_outcomes:
            outcome = "UNVERIFIED"
        else:
            outcome = "GREEN"

        report: Dict[str, Any] = {
            "schema": REPORT_SCHEMA,
            "verification_class": self._verification_class(),
            "run": {"op": self.cfg.op_name, "run_id": self.cfg.run_id,
                    "domain_mode": self.cfg.domain_mode,
                    "n_rows": self.cfg.n_rows,
                    "started_utc": self._started, "finished_utc": utc_now_iso(),
                    "run_dir": str(self.run_dir)},
            "provenance": self.provenance,
            "outcome": outcome,
            "published": False,
            "release_dir": None,
            "sizes": size_reports,
            "required_checks": self._flatten_required_checks(size_reports),
            "frozen_baseline": self._frozen_manifest_note(),
        }

        # 发布门：仅全 GREEN 物化成功包（staging → rename，失败运行不动发布路径）。
        # 顺序纪律：先把 published/release_dir 定值并落 run 目录最终报告，
        # 成功包内嵌报告 = 该最终报告的字节拷贝（不得embedded一份 published=false 的自相矛盾版本）。
        if outcome == "GREEN":
            report["published"] = True
            report["release_dir"] = str(
                self._release_base() / f"{self.cfg.op_name}_{self.cfg.run_id}")
        report_path = self.run_dir / "run_report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8")
        if outcome == "GREEN":
            try:
                self._publish()
            except Exception as e:  # noqa: BLE001 —— 发布失败降级记档，不可崩溃丢报告
                report["published"] = False
                report["release_dir"] = None
                report["publish_error"] = f"{type(e).__name__}: {e}"
                report_path.write_text(
                    json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
        self._write_sha256sums()
        return report

    @property
    def _started(self) -> str:
        if not hasattr(self, "__started"):
            setattr(self, "__started", utc_now_iso())
        return getattr(self, "__started")

    def _run_one_size(self, sctx: SizeContext) -> None:
        sctx.dir.mkdir(parents=True, exist_ok=False)
        for name in STAGE_ORDER:
            rec = self._invoke_stage(name, sctx)
            sctx.stage_records.append(rec)
            if rec["status"] != "pass":
                break
        self._finalize_size(sctx)

    def _finalize_size(self, sctx: SizeContext) -> None:
        if sctx.failure and sctx.outcome == "GREEN":
            sctx.outcome = "RED"
        if sctx.unverified and sctx.outcome == "GREEN":
            sctx.outcome = "UNVERIFIED"
        # 机器可读检查单完整性：fail-fast 未走到的阶段显式标记而非消失
        done = {rec["stage"] for rec in sctx.stage_records}
        for name in STAGE_ORDER:
            if name not in done:
                sctx.stage_records.append(
                    {"stage": name, "status": "not_executed",
                     "note": "前序阶段失败/未裁决，fail-fast 阻断"})

    def _invoke_stage(self, name: str, sctx: SizeContext) -> Dict[str, Any]:
        rec: Dict[str, Any] = {"stage": name, "started_utc": utc_now_iso(),
                               "status": "pass"}
        try:
            self.stages[name](sctx)
        except StageFailure as e:
            rec["status"] = "fail"
            sctx.outcome = "RED"
            sctx.failure = {"codes": e.codes, "stage": name, "detail": e.detail,
                            "exit_code": e.exit_code, "logs": e.logs}
        except Unverified as e:
            rec["status"] = "unverified"
            sctx.outcome = "UNVERIFIED"
            sctx.unverified = {"reason_code": e.reason_code, "stage": name,
                               "detail": e.detail}
        except ConfigInvalid as e:
            # 运行内配置性错误（如 hw_config 加载失败）——编排级非 11 码，RED 阻断发布
            rec["status"] = "fail"
            sctx.outcome = "RED"
            sctx.failure = {"codes": ["CONFIG_INVALID"], "stage": name,
                            "detail": str(e), "exit_code": None, "logs": {}}
        except Exception as e:  # noqa: BLE001 —— 兜底也必须是 RED，绝不静默
            # 逃逸到兜底的 ValueError 仍可能带 [CODE] 前缀（如阶段未自行包装的
            # 组装期闸门消息）——先按契约提取；仅未登记码/无码才落 UNCLASSIFIED。
            codes = codes_from_value_error(e) if isinstance(e, ValueError) else []
            rec["status"] = "fail"
            sctx.outcome = "RED"
            sctx.failure = {"codes": codes or ["UNCLASSIFIED_INTERNAL_ERROR"],
                            "stage": name,
                            "detail": f"{type(e).__name__}: {e}",
                            "exit_code": None, "logs": {}}
        rec["finished_utc"] = utc_now_iso()
        if sctx.failure and rec["stage"] == sctx.failure["stage"]:
            rec["failure"] = sctx.failure
        if sctx.unverified and rec["stage"] == sctx.unverified["stage"]:
            rec["unverified"] = sctx.unverified
        return rec

    # ------------------------------------------------------------------
    # 阶段实现
    # ------------------------------------------------------------------

    def _stage_config_check(self, sctx: SizeContext) -> None:
        """一次性校验（每规模幂等复用）：规模∈lanes 整数倍、注册表封印、provenance 入档。"""
        cfg = self.cfg
        from src.api.hw_spec import HardwareSpec
        try:
            spec = HardwareSpec.load(cfg.hw_config)
        except Exception as e:  # noqa: BLE001
            raise ConfigInvalid(f"hw_config 加载失败: {e}") from e
        lanes = int(spec.params.PARAM_SMC_VECTOR_WIDTH) // 32
        for n in cfg.n_rows:
            if n % lanes != 0:
                raise StageFailure(
                    ["TYPE_LAYOUT_UNSUPPORTED"],
                    f"n_rows={n} 不整除 vector_lanes={lanes}——规模越出 V1 布局约束")
            consumed = n // lanes
            if consumed > 256:
                raise StageFailure(
                    ["TYPE_LAYOUT_UNSUPPORTED"],
                    f"n_rows={n} 静态消耗 {consumed} words > bank_depth=256（规模越界）")
        # 协议模型：加载即自校验封印
        try:
            reg = pr_mod.load_default()
        except Exception as e:  # noqa: BLE001
            raise StageFailure(["REGISTRY_SEAL_MISMATCH"],
                               f"协议注册表封印/结构校验失败: {type(e).__name__}: {e}")
        if not self.provenance:
            reg_dir = REPO_ROOT / "verification_v2" / "tools"
            vcs_side = {}
            for rel in ["sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh",
                        "sim_multi_stage/compare_ur_to_golden_multi_stage.py",
                        "sim_multi_stage/tb_aiacc_top_v04_f_multi_stage.v"]:
                p = cfg.sandbox / rel
                vcs_side[rel] = {"sha256": sha256_file(p), "size": p.stat().st_size} \
                    if p.is_file() else {"missing": True}
            simv = cfg.sandbox / "sim_output_aiacc_top_f_multi_stage" / "simv"
            vcs_side["sim_output_aiacc_top_f_multi_stage/simv"] = (
                {"sha256": sha256_file(simv), "size": simv.stat().st_size,
                 "mtime_utc": _dt.datetime.fromtimestamp(
                     simv.stat().st_mtime, _dt.timezone.utc).isoformat(timespec="seconds")}
                if simv.is_file() else {"missing": True})
            self.provenance = {
                "compiler_repo": git_meta(REPO_ROOT),
                "op_yaml_sha256": sha256_file(cfg.op_yaml),
                "run_config_sha256": cfg.digest(),
                "protocol_registry": {
                    "path": str(reg_dir / f"protocol_registry_v{reg.version}.yaml"),
                    "version": reg.version,
                    "records_sha256": reg.records_digest,
                    "registry_file_sha256": sha256_file(
                        reg_dir / f"protocol_registry_v{reg.version}.yaml"),
                },
                "vcs_side": vcs_side,
            }

    def _get_operator(self, sctx: SizeContext) -> ConfigurableOperator:
        if sctx.operator is None:
            try:
                sctx.operator = ConfigurableOperator(str(self.cfg.op_yaml))
            except ValueError as e:
                codes = codes_from_value_error(e) or ["UNCLASSIFIED_INTERNAL_ERROR"]
                raise StageFailure(codes, f"算子配置加载失败: {e}") from e
        return sctx.operator

    def _declared_domains(self, sctx: SizeContext) -> Dict[str, tuple]:
        """op yaml test_data → 声明域；形状非法 → TYPE_LAYOUT_UNSUPPORTED（§D.2 域越界族）。"""
        spec = yaml.safe_load(self.cfg.op_yaml.read_text(encoding="utf-8"))
        td = spec.get("test_data") or {}
        domains: Dict[str, tuple] = {}
        for name, entry in td.items():
            if isinstance(entry, dict) and "range" in entry:
                lo, hi = entry["range"]
                if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) \
                        or isinstance(lo, bool) or isinstance(hi, bool):
                    raise StageFailure(["TYPE_LAYOUT_UNSUPPORTED"],
                                       f"test_data[{name}].range 端点非数值: {entry!r}")
                domains[name] = (float(lo), float(hi))
            elif isinstance(entry, list) and len(entry) == 1 \
                    and isinstance(entry[0], (int, float)) and not isinstance(entry[0], bool):
                domains[name] = (float(entry[0]), float(entry[0]))
            else:
                raise StageFailure(["TYPE_LAYOUT_UNSUPPORTED"],
                                   f"test_data[{name}] 形状不支持（须 {{range:[lo,hi]}} 或单值列表）: {entry!r}")
        return domains

    def _stage_formula_domain_check(self, sctx: SizeContext) -> None:
        """有序公式解析（SYNTAX_UNSUPPORTED）+ §C.4 双道（静态/动态 → TYPE_LAYOUT_UNSUPPORTED）。

        domain_mode=frozen_regression：静态保守拒（SUBNORMAL_BAND 族）记录为设计内
        证据后放行、动态道必须过（与 anchor 实证语义一致）；v1_admission：双道 AND 必过。
        """
        op = self._get_operator(sctx)
        tensors = op.tensor_configs
        tensor_names = [n for n, c in tensors.items() if c.get("role") in ("input", "param")]
        try:
            root = parse_ordered_formula(op.formula_str, tensor_names)
        except FormulaRejection as e:
            raise StageFailure(["SYNTAX_UNSUPPORTED"], f"有序公式拒绝: {e}") from e
        sctx.formula_root = root

        try:
            sctx.input_data = op.prepare_input(sctx.n)
        except Exception as e:  # noqa: BLE001
            raise StageFailure(["UNCLASSIFIED_INTERNAL_ERROR"],
                               f"prepare_input 异常: {type(e).__name__}: {e}") from e
        np.savez(sctx.ev("inputs.npz"),
                 **{k: np.asarray(v) for k, v in sctx.input_data.items()})

        domains = self._declared_domains(sctx)
        st = rg.static_domain_check(root, domains)
        inputs = {k: np.asarray(v, dtype=F32)
                  for k, v in sctx.input_data.items() if k in tensor_names}
        ev = rg.evaluate(root, inputs)
        sctx.ref_eval = ev
        dyn = rg.dynamic_data_check(ev, domains, rg.leaf_paths(root))

        rec_detail = {
            "static_ok": st.ok,
            "static_kinds": sorted({d.kind for d in st.diagnostics}),
            "dynamic_diags": len(dyn),
        }
        if self.cfg.domain_mode == "v1_admission":
            if not st.ok or dyn:
                raise StageFailure(
                    ["TYPE_LAYOUT_UNSUPPORTED"],
                    f"§C.4 双道未过（mode=v1_admission 须双道 AND）: {rec_detail} "
                    f"first_static={str(st.diagnostics[0]) if st.diagnostics else None} "
                    f"first_dynamic={str(dyn[0]) if dyn else None}")
        else:  # frozen_regression
            bad_kinds = set(rec_detail["static_kinds"]) - FROZEN_STATIC_TOLERATED
            if bad_kinds:
                raise StageFailure(
                    ["TYPE_LAYOUT_UNSUPPORTED"],
                    f"frozen_regression 静态诊断越出可容忍族 {sorted(FROZEN_STATIC_TOLERATED)}: "
                    f"{sorted(bad_kinds)} —— {rec_detail}")
            if dyn:
                raise StageFailure(
                    ["TYPE_LAYOUT_UNSUPPORTED"],
                    f"frozen_regression 动态道必须过，实得 {len(dyn)} 诊断: {dyn[0]}")
        # 通过：判定留档
        (sctx.ev("domain_check.json")).write_text(
            json.dumps({"mode": self.cfg.domain_mode, "domains": {k: list(v) for k, v in domains.items()},
                        **rec_detail}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

    def _stage_compile_admit(self, sctx: SizeContext) -> None:
        """SMT 求解 + L2 准入 + 发射 + python sim；产物快照即刻入 run 目录。

        失败码归属：SynthesisRejected → SOLVER_UNSAT/SOLVER_UNKNOWN（三态透出）；
        [CODE] 前缀 ValueError → 原码透出；sim 非零 → SIM_RUNTIME_ERROR；
        其余发射期异常 → PACKAGE_OR_EMISSION_ERROR。
        """
        op = self._get_operator(sctx)
        build_dir = REPO_ROOT / "build" / "verification_v2" / self.cfg.op_name
        if build_dir.exists():                       # 运行隔离：瞬态目录先清零
            shutil.rmtree(build_dir)
        sctx.runner = PipelineRunner(hw_config_name=self.cfg.hw_config,
                                     show_review=False, show_model=False)
        try:
            ctx = sctx.runner.run_full_stack(
                op, sctx.input_data, op_name=self.cfg.op_name, external_input=False,
                allow_stage_partition=True)   # ADR-057：分段拦截仅此入口选择加入
        except SynthesisRejected as e:
            raise StageFailure(
                [e.failure_code],
                f"求解器拒绝（三态透出 final={e.report.final}"
                f"{'；预算踩停' if e.report.budget_exhausted else ''}）: {e}") from e
        except ValueError as e:
            codes = codes_from_value_error(e)
            if codes:
                raise StageFailure(codes, f"准入/装配拒绝: {e}") from e
            raise StageFailure(["UNCLASSIFIED_INTERNAL_ERROR"],
                               f"compile 阶段 ValueError 无码: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"],
                               f"发射期异常: {type(e).__name__}: {e}") from e
        sctx.ctx = ctx
        if not ctx.get("sim_success", False):
            raise StageFailure(
                ["SIM_RUNTIME_ERROR"],
                f"python 物理仿真非零退出（见 {ctx.get('log_path')}）",
                logs={"sim_log": ctx.get("log_path")})
        # 边界闸：仅无反压展开形态可继续裁决
        branches = (ctx.get("branch_report") or {})
        non_unroll = {q: b for q, b in branches.items() if b != "unroll"}
        if non_unroll:
            raise Unverified("BRANCH_NOT_UNROLL",
                             f"存在非 unroll 分支 {non_unroll}——逐拍裁决模型仅支持"
                             f"无反压展开形态，明确报告未能验证")
        # 产物快照即刻入 run 目录（其后所有审计/打包只读本副本）
        sctx.build_copy = sctx.dir / "build"
        shutil.copytree(build_dir, sctx.build_copy)
        # ADR-057：多段计划挂到规模上下文（单段 None，后续阶段零分支）
        sctx.stage_plan = ctx.get("stage_plan")

    def _stage_independent_reference(self, sctx: SizeContext) -> None:
        """§C.3：以独立参考为打包 golden 源——逐位比 golden_data.npz，重写 output_bank 输出区。

        参考与 npz 逐位不等 → NUMERIC_MISMATCH；重写区与 sim 终态字节不等（npz 相等
        但 bank 不等的链内悖论）→ NUMERIC_MISMATCH。add 锚定语义下重写应逐字节
        相同——差分如实入档。
        """
        npz_path = sctx.build_copy / "golden_data.npz"
        if not npz_path.is_file():
            raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"], f"缺 golden_data.npz: {npz_path}")
        golden_y = np.asarray(np.load(npz_path)["y"], dtype=F32).reshape(-1)
        ref_y = np.asarray(sctx.ref_eval.y, dtype=F32).reshape(-1)
        if not bits_equal(ref_y, golden_y):
            idx = int(np.nonzero(ref_y.view(np.uint32) != golden_y.view(np.uint32))[0][0])
            raise StageFailure(
                ["NUMERIC_MISMATCH"],
                f"独立参考 ≠ golden_data.npz 第 {idx} 点: "
                f"ref=0x{ref_y.view(np.uint32)[idx]:08X} golden=0x{golden_y.view(np.uint32)[idx]:08X}")

        sim_cfg = json.loads((sctx.build_copy / "sim_config.json").read_text())
        out_tensors = [t for t in sim_cfg.get("tensors", []) if t.get("role") == "output"]
        if len(out_tensors) != 1:
            raise StageFailure(["TYPE_LAYOUT_UNSUPPORTED"],
                               f"sim_config 输出张量数 {len(out_tensors)} ≠ 1（V1 单输出式）")
        bank, addr = int(out_tensors[0]["bank"]), int(out_tensors[0].get("addr", 0))
        bank_path = sctx.build_copy / "smc_0" / "output_bank" / f"bank{bank}_final.hex"
        if not bank_path.is_file():
            raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"], f"缺 {bank_path}")
        orig_bytes = bank_path.read_bytes()
        words = [int(l.strip(), 16) for l in orig_bytes.decode().splitlines() if l.strip()]
        from export_golden_hex import pack_words
        packed = pack_words(ref_y)
        if addr + len(packed) > len(words):
            raise StageFailure(
                ["TYPE_LAYOUT_UNSUPPORTED"],
                f"参考输出区 [{addr},{addr + len(packed)}) 超 bank 词数 {len(words)}")
        for i, w in enumerate(packed):
            words[addr + i] = w
        with open(bank_path, "w", encoding="utf-8") as f:
            for w in words:
                f.write(f"0x{w:032x}\n")
        new_bytes = bank_path.read_bytes()
        byte_identical = new_bytes == orig_bytes
        (sctx.ev("reference_golden.json")).write_text(json.dumps({
            "ref_vs_npz_bits_equal": True,
            "golden_bank": f"smc_0/output_bank/bank{bank}_final.hex",
            "rewrite_word_span": [addr, addr + len(packed)],
            "sha256_before": hashlib.sha256(orig_bytes).hexdigest(),
            "sha256_after": hashlib.sha256(new_bytes).hexdigest(),
            "byte_identical": byte_identical,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if not byte_identical:
            raise StageFailure(
                ["NUMERIC_MISMATCH"],
                f"参考重写区与 sim 终态 golden 字节不等（npz 层已逐位相等——链内不一致，"
                f"熔断定位；before={hashlib.sha256(orig_bytes).hexdigest()[:16]} "
                f"after={hashlib.sha256(new_bytes).hexdigest()[:16]}）")

    def _stage_artifact_audit(self, sctx: SizeContext) -> None:
        """产物审计：Auditor 三门 + 期望事件仿真前落盘（重建侧违例自行拒盘）。

        ADR-057：多段构建先行 D3 manifest 独立检查（封印 + 计划/地址/行数重算
        对照），再逐段 headers + 逐段期望事件（段起始偏移进地址公式）；
        truth/firmware 两门以全迭代数在顶层快照跑一次（链式 sim 日志覆盖全程）。
        """
        ctx, bc = sctx.ctx, sctx.build_copy
        sim_log = str(bc / f"{self.cfg.op_name}_sim.log")   # 快照副本内的仿真日志
        auditor_top = Auditor(str(bc), sctx.runner.spec, show_review=False,
                              show_model=False)

        if sctx.stage_plan:
            findings = manifest_ck.check_manifest(
                bc, REPO_ROOT / "verification_v2" / "ops")
            if findings:
                raise StageFailure(
                    ["PACKAGE_OR_EMISSION_ERROR"],
                    f"ADR-057 D3 manifest 独立检查 RED（{len(findings)} 项）: "
                    + " ; ".join(f"[{c}] {m}" for c, m in findings[:6]),
                    logs={"stage_manifest": str(bc / "stage_manifest.json")})
            for st in sctx.stage_plan["stages"]:
                k = st["stage_id"]
                auditor_k = Auditor(str(bc / "stages" / f"stage_{k}"),
                                    sctx.runner.spec, show_review=False,
                                    show_model=False)
                if not auditor_k.audit_physical_headers():
                    raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"],
                                       f"stage_{k} audit_physical_headers RED"
                                       f"（发射头部违例）", logs={"sim_log": sim_log})
        else:
            if not auditor_top.audit_physical_headers():
                raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"],
                                   "audit_physical_headers RED（发射头部违例）",
                                   logs={"sim_log": sim_log})

        effective = ctx.get("input_data", sctx.input_data)
        auditor_inputs = {k: (v[0] if hasattr(v, "ndim") and v.ndim == 2 else v)
                          for k, v in effective.items()}
        if not auditor_top.audit_truth_alignment(sim_log,
                num_iterations=ctx["num_iterations"], ops=ctx.get("ops"),
                input_data=auditor_inputs):
            raise StageFailure(["NUMERIC_MISMATCH"],
                               "audit_truth_alignment RED（sim 终态与独立期望不符）",
                               logs={"sim_log": sim_log})
        if not auditor_top.audit_firmware_integrity(sim_log, ops=ctx.get("ops")):
            raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"],
                               "audit_firmware_integrity RED（固件覆盖缺口）")

        if sctx.stage_plan:
            for st in sctx.stage_plan["stages"]:
                k = st["stage_id"]
                ev_k = sctx.ev(f"expected_events_stage_{k}.json")
                bad = emit_fx.audit(bc / "stages" / f"stage_{k}", st["m"],
                                    hw_config=self.cfg.hw_config,
                                    require_unroll=True, emit_events_path=ev_k,
                                    stage_start=st["start"],
                                    stage={"stage_id": k, "start": st["start"],
                                           "m": st["m"]})
                if bad:
                    raise StageFailure(["PER_BEAT_PROTOCOL_MISMATCH"],
                                       f"stage_{k} 发射行独立重建/比对违例 "
                                       f"{len(bad)} 条: "
                                       + " ; ".join(str(b) for b in bad[:8]),
                                       logs={"audit_log": str(ev_k)})
        else:
            ev_path = sctx.ev("expected_events.json")
            bad = emit_fx.audit(bc, ctx["num_iterations"], hw_config=self.cfg.hw_config,
                                require_unroll=True, emit_events_path=ev_path)
            if bad:
                raise StageFailure(["PER_BEAT_PROTOCOL_MISMATCH"],
                                   f"发射行独立重建/比对违例 {len(bad)} 条: "
                                   + " ; ".join(str(b) for b in bad[:8]),
                                   logs={"audit_log": str(ev_path)})

    def _stage_package(self, sctx: SizeContext) -> None:
        """打包 VCS case（clean=True 抹除旧 case 全目录——旧探针日志不可串用）。

        ADR-057 D5：多段构建走 package_multi（段号连续校验 + clean 只清一次 +
        ur_data 取 stage_0 / golden 取顶层链式终态），段产物缺失即拒绝打包。
        """
        try:
            if sctx.stage_plan:
                case = package_case_multi(sctx.build_copy, self.cfg.suite,
                                          sctx.case_name, self.cfg.sandbox,
                                          clean=True)
            else:
                case = package_case(sctx.build_copy, self.cfg.suite, sctx.case_name,
                                    self.cfg.sandbox, clean=True)
        except SystemExit as e:
            raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"], f"打包器拒绝: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise StageFailure(["PACKAGE_OR_EMISSION_ERROR"],
                               f"打包异常: {type(e).__name__}: {e}") from e
        sctx.case_root = case
        sctx.case_rel = f"{self.cfg.suite}/{sctx.case_name}/smc_0"

    def _collect_vcs_evidence(self, sctx: SizeContext, runner_stdout: Path) -> Dict[str, str]:
        """VCS 侧证据拷贝入 run 目录（成败都拷）。返回相对路径表。"""
        logs_root = self.cfg.sandbox / "arith_regression" / "test_log" / "multi_stage"
        sim_log = logs_root / f"{self.cfg.suite}" / sctx.case_name / "sim.log"
        batch = logs_root / "batch_compare.log"
        out: Dict[str, str] = {"vcs_runner_stdout": sctx.rel(runner_stdout)}
        for tag, src in (("sim_log", sim_log), ("batch_compare_log", batch)):
            if src.is_file():
                dst = sctx.ev(f"n{sctx.n}_{tag}.log")
                shutil.copy2(src, dst)
                out[tag] = sctx.rel(dst)
        if sctx.case_root and sctx.case_root.is_dir():
            for name in ("iq_probe_trace.log", "iq_issue_trace.log", "ur_write_trace.log"):
                src = sctx.case_root / name
                if src.is_file():
                    dst = sctx.ev(f"n{sctx.n}_{name}")
                    shutil.copy2(src, dst)
                    out[f"probe_{name}"] = sctx.rel(dst)
        return out

    def _stage_vcs(self, sctx: SizeContext) -> None:
        if not self.cfg.run_vcs:
            raise Unverified("VCS_NOT_RUN",
                             "run_vcs=false——VCS 及其后裁决未执行，明确报告未能验证"
                             "（不输出 GREEN、不发布）")
        runner = self.cfg.sandbox / "sim_multi_stage" / "run_sim_aiacc_top_f_multi_stage.sh"
        stdout_path = sctx.ev(f"n{sctx.n}_vcs_runner_stdout.log")
        cmd = ["bash", str(runner), sctx.case_rel]
        with open(stdout_path, "w") as fh:
            try:
                proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                      timeout=self.cfg.vcs_timeout_s)
            except subprocess.TimeoutExpired:
                logs = self._collect_vcs_evidence(sctx, stdout_path)
                raise StageFailure(["SIM_TIMEOUT_OR_HANG"],
                                   f"VCS 墙钟超时（>{self.cfg.vcs_timeout_s}s）: {sctx.case_rel}",
                                   logs=logs, exit_code=None)
        logs = self._collect_vcs_evidence(sctx, stdout_path)
        if proc.returncode != 0:
            sim_log = Path(logs.get("sim_log", ""))
            sig_timeout = sim_log.is_file() and \
                "smi_wait_and_clear_intr timeout" in sim_log.read_text(errors="replace")
            code = "SIM_TIMEOUT_OR_HANG" if sig_timeout else "SIM_RUNTIME_ERROR"
            raise StageFailure([code],
                               f"VCS runner 非零退出（simv/TB/compare 任一关未过）: {sctx.case_rel}",
                               logs=logs, exit_code=proc.returncode)
        sctx.vcs_logs = logs

    def _stage_native_compare(self, sctx: SizeContext) -> None:
        """原生比对：编排器**独立重跑** compare（结构化退出码，非 PASSED 字样）。"""
        cmp_py = self.cfg.sandbox / "sim_multi_stage" / "compare_ur_to_golden_multi_stage.py"
        log_path = sctx.ev(f"n{sctx.n}_native_compare.log")
        proc = subprocess.run(
            [sys.executable, str(cmp_py),
             "--test-atomic-ops", str(self.cfg.sandbox / "test_atomic_ops"),
             "--only-relpath", sctx.case_rel, "--log", str(log_path)],
            capture_output=True, text=True)
        sctx.evidence_dir.joinpath(f"n{sctx.n}_native_compare_stdout.log") \
            .write_text(proc.stdout + proc.stderr, encoding="utf-8")
        if proc.returncode == 1:
            raise StageFailure(["NUMERIC_MISMATCH"],
                               f"原生 compare RED: {sctx.case_rel}",
                               logs={"native_compare_log": sctx.rel(log_path)},
                               exit_code=1)
        if proc.returncode != 0:
            raise StageFailure(["SIM_RUNTIME_ERROR"],
                               f"原生 compare 环境/输入错误（exit={proc.returncode}）",
                               logs={"native_compare_log": sctx.rel(log_path)},
                               exit_code=proc.returncode)

    def _stage_three_zone(self, sctx: SizeContext) -> None:
        tz_py = REPO_ROOT / "verification_v2" / "tools" / "three_zone_compare.py"
        out_path = sctx.ev(f"n{sctx.n}_three_zone.json")
        # ADR-057：多段时逐段 physical_instructions.json 全部传入（工具内计数求和；
        # 三区定义本身与分段无关——golden/init/终态 dump 都是全范围文件）。
        if sctx.stage_plan:
            instr_args: List[str] = []
            for st in sctx.stage_plan["stages"]:
                instr_args += ["--instructions",
                               str(sctx.build_copy / "stages"
                                   / f"stage_{st['stage_id']}"
                                   / "physical_instructions.json")]
        else:
            instr_args = ["--instructions",
                          str(sctx.build_copy / "physical_instructions.json")]
        proc = subprocess.run(
            [sys.executable, str(tz_py), str(sctx.case_root / "smc_0"),
             "--label", f"n{sctx.n}",
             *instr_args,
             "--write-queue", "vout0"],
            capture_output=True, text=True)
        out_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")
        if proc.returncode == 1:
            raise StageFailure(["NUMERIC_MISMATCH"],
                               f"三区比对 RED: {str(proc.stdout)[:300]}",
                               logs={"three_zone": sctx.rel(out_path)}, exit_code=1)
        if proc.returncode != 0:
            raise StageFailure(["SIM_RUNTIME_ERROR"],
                               f"三区比对输入缺失/坏（exit={proc.returncode}）",
                               logs={"three_zone": sctx.rel(out_path)},
                               exit_code=proc.returncode)

    def _stage_arbitrate(self, sctx: SizeContext) -> None:
        if sctx.stage_plan:
            # ADR-057 D4：逐段期望（仿真前冻结）+ sim.log 段标记 → 多段仲裁
            # （按 TB 标记切窗、段内复用单段裁决、段间 C1/C2/C3）。
            expected_stages = []
            for st in sctx.stage_plan["stages"]:
                p = sctx.evidence_dir / f"expected_events_stage_{st['stage_id']}.json"
                expected_stages.append(json.loads(p.read_text(encoding="utf-8")))
            sim_log = sctx.evidence_dir / f"n{sctx.n}_sim_log.log"
            try:
                report = arb.arbitrate_multi(sctx.case_root, expected_stages, sim_log)
            except Exception as e:  # noqa: BLE001
                raise StageFailure(["PER_BEAT_PROTOCOL_MISMATCH"],
                                   f"多段仲裁器异常（探针日志/段标记坏）: "
                                   f"{type(e).__name__}: {e}") from e
            # 多段报告必带 limitations 键——缺键即仲裁器重构事故，宁 KeyError 不静默
            # （evaluator N4）。
            sctx.arb_limitations = list(report["limitations"])
        else:
            ev_path = sctx.evidence_dir / "expected_events.json"
            expected = json.loads(ev_path.read_text(encoding="utf-8"))
            try:
                report = arb.arbitrate(sctx.case_root, expected)
            except Exception as e:  # noqa: BLE001
                raise StageFailure(["PER_BEAT_PROTOCOL_MISMATCH"],
                                   f"仲裁器异常（探针日志坏/缺失）: {type(e).__name__}: {e}") from e
            sctx.arb_limitations = []   # 单段报告（ADR-056 形态）无此键，显式置空
        (sctx.ev(f"n{sctx.n}_arbitrate_report.json")).write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for lim in sctx.arb_limitations:
            # 限制声明不逆转 verdict，但必须上日志——不许静默放过（ADR-057 errata-2026-10-01）
            print(f"[ARBITRATE-LIMITATION] {lim}")
        if report.get("verdict") != "GREEN":
            raise StageFailure(["PER_BEAT_PROTOCOL_MISMATCH"],
                               f"逐拍仲裁 RED（{len(report.get('violations', []))} 违例）: "
                               + " ; ".join(str(v) for v in report.get("violations", [])[:8]),
                               logs={"arbitrate_report": sctx.rel(
                                   sctx.evidence_dir / f"n{sctx.n}_arbitrate_report.json")})

    # ------------------------------------------------------------------
    # 报告 / 归档 / 发布
    # ------------------------------------------------------------------

    def _size_report_fragment(self, sctx: SizeContext) -> Dict[str, Any]:
        frag: Dict[str, Any] = {
            "n": sctx.n, "outcome": sctx.outcome, "stages": sctx.stage_records,
            "case_name": sctx.case_name,
            "evidence_dir": sctx.rel(sctx.evidence_dir),
        }
        if sctx.arb_limitations:
            # evaluator N2：限制声明随报告落账（GREEN 也带），顶层载体自含
            frag["arbitrate_limitations"] = list(sctx.arb_limitations)
        if sctx.failure:
            frag["failure"] = sctx.failure
        if sctx.unverified:
            frag["unverified"] = sctx.unverified
        return frag

    def _flatten_required_checks(self, size_reports: Dict[str, Any]) -> List[Dict[str, Any]]:
        """机器可读总检查单：全部必需检查 × 证据路径（出口 3）。"""
        checks: List[Dict[str, Any]] = []
        for n_key, frag in size_reports.items():
            for rec in frag.get("stages", []):
                item: Dict[str, Any] = {
                    "check": f"{rec['stage']}@n{n_key}",
                    "status": rec["status"],
                    "finished_utc": rec.get("finished_utc"),
                }
                if rec.get("failure"):
                    item["failure"] = rec["failure"]
                if rec.get("unverified"):
                    item["unverified"] = rec["unverified"]
                ev_dir = frag.get("evidence_dir")
                if ev_dir:
                    item["evidence_dir"] = ev_dir
                checks.append(item)
            if not frag.get("stages"):
                checks.append({"check": f"pipeline@n{n_key}", "status": frag["outcome"],
                               "note": frag.get("note")})
        return checks

    def _frozen_manifest_note(self) -> Dict[str, Any]:
        """frozen_regression 模式下复核冻结清单（add 设施自验的零字节前提）。"""
        note: Dict[str, Any] = {"checked": False}
        if self.cfg.domain_mode != "frozen_regression" or self.cfg.op_name != "add":
            note["reason"] = "仅 add + frozen_regression 触发冻结清单复核"
            return note
        sums = REPO_ROOT / "verification_v2/artifacts/e2e_add_20260926/FROZEN_BASELINE_SHA256SUMS.txt"
        if not sums.is_file():
            note["reason"] = f"冻结清单缺失: {sums}"
            return note
        proc = subprocess.run(["sha256sum", "-c", sums.name], cwd=sums.parent,
                              capture_output=True, text=True)
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        ok = sum(1 for l in lines if l.rstrip().endswith(": OK"))
        note.update({"checked": True, "ok_count": ok, "rc": proc.returncode,
                     "manifest_sha256": sha256_file(sums)})
        note["verdict"] = "PASS" if (proc.returncode == 0 and ok == len(lines) and ok > 0) else "FAIL"
        return note

    def _verification_class(self) -> str:
        """frozen_regression 只证明「旧基线未退化」，不是 V1 准入证据——两类不得混称。"""
        return ("frozen_baseline_regression"
                if self.cfg.domain_mode == "frozen_regression" else "v1_admission")

    def _release_base(self) -> Path:
        """发布通道分列：历史基线回归包不进入普通 V1 已验证包目录。"""
        if self.cfg.domain_mode == "frozen_regression":
            return self.cfg.releases_root / "regression"
        return self.cfg.releases_root

    def _publish(self) -> Path:
        base = self._release_base()
        rel_dir = base / f"{self.cfg.op_name}_{self.cfg.run_id}"
        staging = base / f".staging_{self.cfg.op_name}_{self.cfg.run_id}"
        if rel_dir.exists():
            raise RuntimeError(f"发布路径已存在（run_id 冲突）: {rel_dir}")
        base.mkdir(parents=True, exist_ok=True)
        if staging.exists():                    # 上次崩溃残留的半截包，先清零再重建
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        shutil.copy2(self.run_dir / "run_report.json", staging / "run_report.json")
        if self.cfg.domain_mode == "frozen_regression":
            title = ("# ADR-056 步④-3 历史基线回归通过包\n\n"
                     "**verification_class = frozen_baseline_regression：本包仅证明旧冻结"
                     "基线在新链路下未退化，不构成、不得引用为 V1 数值域准入成功证据。**\n")
        else:
            title = ("# ADR-056 步④-3 V1 已验证成功包\n\n"
                     "**verification_class = v1_admission：§C.4 双道（静态+动态）全过的"
                     "安全数值域配置，真实 VCS 与全部裁决 GREEN。**\n")
        (staging / "README.md").write_text(
            title + f"\n- op: `{self.cfg.op_name}`  run_id: `{self.cfg.run_id}`  "
            f"domain_mode: `{self.cfg.domain_mode}`  n_rows: {self.cfg.n_rows}\n"
            f"- 全部必需检查 GREEN；完整诊断/证据链: `{self.run_dir}`\n"
            f"- 机器可读报告: `run_report.json`（schema `{REPORT_SCHEMA}`，与 run 目录版字节一致）\n",
            encoding="utf-8")
        os.rename(staging, rel_dir)
        return rel_dir

    def _write_sha256sums(self) -> None:
        """run 目录全量哈希清单（排除 run_report.json/SHA256SUMS.txt 自身）。"""
        lines = []
        for p in sorted(self.run_dir.rglob("*")):
            if p.is_file() and p.name not in ("SHA256SUMS.txt", "run_report.json"):
                lines.append(f"{sha256_file(p)}  {p.relative_to(self.run_dir)}")
        (self.run_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n",
                                                     encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_n_rows(text: str) -> List[int]:
    try:
        return [int(t.strip()) for t in text.split(",") if t.strip()]
    except ValueError as e:
        raise ConfigInvalid(f"--n-rows 解析失败（须逗号分隔正整数）: {text!r}") from e


def build_config(argv: Optional[List[str]] = None) -> RunConfig:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=None, help="运行配置 YAML（可选；CLI 覆盖）")
    ap.add_argument("--op-yaml", type=str, default=None)
    ap.add_argument("--n-rows", type=str, default=None, help="如 4,8,60")
    ap.add_argument("--domain-mode", choices=DOMAIN_MODES, default=None)
    ap.add_argument("--hw-config", default=None)
    ap.add_argument("--sandbox", default=None)
    ap.add_argument("--vcs-timeout-s", type=int, default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--run-vcs", dest="run_vcs", action="store_true", default=None)
    ap.add_argument("--no-run-vcs", dest="run_vcs", action="store_false")
    a = ap.parse_args(argv)

    raw: Dict[str, Any] = {}
    if a.config is not None:
        cfg_doc = yaml.safe_load(a.config.read_text(encoding="utf-8"))
        if not isinstance(cfg_doc, dict):
            raise ConfigInvalid(f"配置 YAML 顶层须为映射: {a.config}")
        raw.update(cfg_doc)
    for k, v in {"op_yaml": a.op_yaml, "domain_mode": a.domain_mode,
                 "hw_config": a.hw_config, "sandbox": a.sandbox,
                 "vcs_timeout_s": a.vcs_timeout_s, "run_id": a.run_id,
                 "run_vcs": a.run_vcs}.items():
        if v is not None:
            raw[k] = v
    if a.n_rows is not None:
        raw["n_rows"] = _parse_n_rows(a.n_rows)
    return RunConfig(raw)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        cfg = build_config(argv)
    except ConfigInvalid as e:
        # 预检失败同样落机器可读材料：无 run 目录可落（run_id/目录未定）→ stderr + exit 2
        print(f"[CONFIG_INVALID] {e}", file=sys.stderr)
        return 2
    if getattr(cfg, "partition", None) is not None:
        print(f"[CONFIG_INVALID] V1 入口不支持 partition={cfg.partition!r}"
              f"——分片仅统一 LCS 入口 run_unified_lcs_verify.py 支持",
              file=sys.stderr)
        return 2
    orch = Orchestrator(cfg)
    try:
        report = orch.run()
    except FileExistsError as e:
        print(f"[CONFIG_INVALID] 运行目录已存在（run_id 冲突，请换 --run-id 或清理）: {e}",
              file=sys.stderr)
        return 2
    outcome = report["outcome"]
    print(f"[run_v1_verify] op={cfg.op_name} run_id={cfg.run_id} outcome={outcome} "
          f"published={report['published']}")
    print(f"  run 目录: {orch.run_dir}")
    if report["published"]:
        print(f"  成功包: {report['release_dir']}")
    if report.get("publish_error"):
        print(f"  发布失败（已降级记档）: {report['publish_error']}", file=sys.stderr)
    for n_key, frag in report["sizes"].items():
        if frag["outcome"] != "GREEN":
            extra = frag.get("failure") or frag.get("unverified") or {"note": frag.get("note")}
            print(f"  n{n_key}: {frag['outcome']} "
                  f"{json.dumps(extra, ensure_ascii=False)[:400]}")
    return 0 if outcome == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
