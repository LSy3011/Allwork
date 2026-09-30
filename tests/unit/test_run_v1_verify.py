# -*- coding: utf-8 -*-
"""ADR-056 步④-3 统一入口编排器的失败传递 / 发布门 / 运行隔离 / 报告契约测试。

策略（ADR-056 §D.2 与用户 2026-09-29 步④-3 规格）：
- **11 类失败逐一传递**：注入在任一阶段抛 ``StageFailure([code])`` 的 fake 阶段，
  断言 outcome=RED、失败码/阶段/退出码/日志路径逐字段透传、fail-fast 后续阶段
  显式 ``not_executed``、后续规模 ``NOT_RUN``、发布被阻断。
- **失败不发布**：预先放置旧成功包 sentinel，失败运行后逐字节不变、无新增发布物、
  run 目录诊断材料保留（run_report.json 存在）。
- **旧产物不串用**：瞬态 build 目录预置垃圾文件，真实 ``_stage_compile_admit``
  （PipelineRunner 打桩）必须先清零再重建，快照内无垃圾。
- **UNVERIFIED = 不发布**：BRANCH_NOT_UNROLL（fake）与 VCS_NOT_RUN（真实
  ``_stage_vcs`` + run_vcs=false）两条路径，各自 outcome=UNVERIFIED、无发布。
- **三态透亮**：合成 SolverReport（final=unsat / unknown）经 SynthesisRejected 注入
  真实 ``_stage_compile_admit``，SOLVER_UNSAT / SOLVER_UNKNOWN 可分别触发。
- **机器可读总报告**：success run 的 required_checks 覆盖全部 STAGE_ORDER、
  成功包内嵌 run_report.json 与运行目录版逐字节一致。
- **集成**（沙箱在位时）：add n4 + run_vcs=false 全真实阶段跑通至打包，
  outcome=UNVERIFIED（VCS_NOT_RUN）、参考重写逐字节相同。
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))
sys.path.append(str(_ROOT / "verification_v2" / "tools"))

import run_v1_verify as rv                       # noqa: E402
from verification_v2.infra.pipeline_runner import SynthesisRejected   # noqa: E402
from src.synthesis.ucode_solver import SolverStatus, SolverReport     # noqa: E402

SANDBOX = Path.home() / "u280_sim_v422"
_SANDBOX_OK = (SANDBOX / "sim_multi_stage" / "run_sim_aiacc_top_f_multi_stage.sh").is_file()
ADD_YAML = _ROOT / "verification_v2" / "ops" / "add.yaml"


# ---------------------------------------------------------------------------
# 脚手架
# ---------------------------------------------------------------------------

def _fake_op_yaml(tmp_path: Path, name: str = "fakeop") -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text("name: fake\n", encoding="utf-8")
    return p


def mk_cfg(tmp_path: Path, op_name: str = "fakeop", **over) -> rv.RunConfig:
    raw = {
        "op_yaml": str(tmp_path / f"{op_name}.yaml"),
        "n_rows": [4],
        "domain_mode": "frozen_regression",
        "run_vcs": False,
        "runs_root": str(tmp_path / "runs"),
        "releases_root": str(tmp_path / "releases"),
        "sandbox": str(tmp_path / "sandbox"),
    }
    raw.update(over)
    if not (tmp_path / f"{op_name}.yaml").is_file():
        _fake_op_yaml(tmp_path, op_name)
    return rv.RunConfig(raw)


def pass_stages() -> dict:
    return {name: (lambda sctx: None) for name in rv.STAGE_ORDER}


def many_params_stages(stage: str, exc_factory) -> dict:
    st = pass_stages()

    def boom(sctx):
        raise exc_factory()

    st[stage] = boom
    return st


def run_orch(tmp_path, stages=None, **cfg_over):
    cfg = mk_cfg(tmp_path, **cfg_over)
    orch = rv.Orchestrator(cfg, stages=stages or pass_stages())
    return cfg, orch, orch.run()


# ---------------------------------------------------------------------------
# 出口 2-a：11 类失败逐一传递
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", sorted(rv.FAILURE_CODES_11))
def test_11_failure_codes_propagate(tmp_path, code):
    exc = lambda: rv.StageFailure([code], f"detail-{code}",           # noqa: E731
                                  logs={"sim_log": "case/sim.log"}, exit_code=7)
    cfg, orch, report = run_orch(
        tmp_path, stages=many_params_stages("compile_admit", exc))

    assert report["outcome"] == "RED"
    assert report["published"] is False
    frag = report["sizes"]["4"]
    assert frag["outcome"] == "RED"
    fail = frag["failure"]
    assert fail["codes"] == [code]
    assert fail["stage"] == "compile_admit"
    assert fail["exit_code"] == 7
    assert fail["logs"] == {"sim_log": "case/sim.log"}
    rec = {r["stage"]: r for r in frag["stages"]}
    assert rec["compile_admit"]["status"] == "fail"
    for later in rv.STAGE_ORDER[rv.STAGE_ORDER.index("compile_admit") + 1:]:
        assert rec[later]["status"] == "not_executed"
    # 机器可读总检查单同样带失败详情
    chk = {c["check"]: c for c in report["required_checks"]}
    assert chk["compile_admit@n4"]["status"] == "fail"
    assert chk["compile_admit@n4"]["failure"]["codes"] == [code]
    # 失败不发布
    assert not (tmp_path / "releases").exists() or not list((tmp_path / "releases").iterdir())
    # 诊断材料保留：运行目录报告已落盘
    assert (cfg.runs_root / "fakeop" / cfg.run_id / "run_report.json").is_file()


@pytest.mark.parametrize("stage", rv.STAGE_ORDER[:6])
def test_fail_fast_at_any_stage_marks_remaining_not_executed(tmp_path, stage):
    exc = lambda: rv.StageFailure(["NUMERIC_MISMATCH"], "x")          # noqa: E731
    _, _, report = run_orch(tmp_path, stages=many_params_stages(stage, exc))
    rec = {r["stage"]: r["status"] for r in report["sizes"]["4"]["stages"]}
    assert rec[stage] == "fail"
    for later in rv.STAGE_ORDER[rv.STAGE_ORDER.index(stage) + 1:]:
        assert rec[later] == "not_executed"


def test_fail_fast_skips_later_sizes(tmp_path):
    exc = lambda: rv.StageFailure(["PACKAGE_OR_EMISSION_ERROR"], "x")  # noqa: E731
    _, _, report = run_orch(
        tmp_path, stages=many_params_stages("package", exc), n_rows=[4, 8, 60])
    assert report["sizes"]["4"]["outcome"] == "RED"
    for key in ("8", "60"):
        assert report["sizes"][key]["outcome"] == "NOT_RUN"
        assert report["sizes"][key]["note"]


# ---------------------------------------------------------------------------
# 出口 2-b：编排级三码（非 §D.2，同样 RED + 阻断发布）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["config_invalid", "registry_seal", "unclassified"])
def test_orchestrator_level_codes_are_red_not_publishable(tmp_path, kind):
    if kind == "config_invalid":
        exc = lambda: rv.ConfigInvalid("hw_config 加载失败: boom")    # noqa: E731
        want = ["CONFIG_INVALID"]
    elif kind == "registry_seal":
        exc = lambda: rv.StageFailure(["REGISTRY_SEAL_MISMATCH"], "封印不符")  # noqa: E731
        want = ["REGISTRY_SEAL_MISMATCH"]
    else:
        exc = lambda: RuntimeError("oops")                            # noqa: E731
        want = ["UNCLASSIFIED_INTERNAL_ERROR"]
    stages = many_params_stages("config_check", exc)
    _, _, report = run_orch(tmp_path, stages=stages)
    frag = report["sizes"]["4"]
    assert report["outcome"] == "RED"
    assert frag["failure"]["codes"] == want
    assert report["published"] is False


def test_generic_exception_is_unclassified_never_silent(tmp_path):
    exc = lambda: ValueError("无码普通 ValueError")                    # noqa: E731
    _, _, report = run_orch(tmp_path, stages=many_params_stages("arbitrate", exc))
    fail = report["sizes"]["4"]["failure"]
    assert fail["codes"] == ["UNCLASSIFIED_INTERNAL_ERROR"]
    assert "ValueError" in fail["detail"] and "无码" in fail["detail"]


# ---------------------------------------------------------------------------
# 出口 2-c：失败不发布——旧成功包 sentinel 逐字节不变、无半截包
# ---------------------------------------------------------------------------

def test_failure_run_preserves_legacy_release_sentinel(tmp_path):
    rel = tmp_path / "releases"
    legacy = rel / "add_LEGACY"
    legacy.mkdir(parents=True)
    sentinel = legacy / "SENTINEL.bin"
    sentinel.write_bytes(b"legacy-success\x00bytes")
    before = sorted(p.name for p in rel.iterdir())

    exc = lambda: rv.StageFailure(["SIM_RUNTIME_ERROR"], "sim 非零")   # noqa: E731
    _, _, report = run_orch(tmp_path, stages=many_params_stages("vcs", exc),
                            run_id="failrun")
    assert report["outcome"] == "RED" and report["published"] is False
    assert sentinel.read_bytes() == b"legacy-success\x00bytes"
    assert sorted(p.name for p in rel.iterdir()) == before      # 无新增、无 .staging 残留


# ---------------------------------------------------------------------------
# 出口 2-c'：旧产物不串用——瞬态 build 目录先清零；run 目录不可复用
# ---------------------------------------------------------------------------

class _OkRunner:
    """打桩 PipelineRunner：临时 build 目录只写本 run 的文件。"""

    MARKER = "fresh_marker.txt"

    def __init__(self, **kw):
        self.kw = kw

    def run_full_stack(self, op, input_data, op_name=None, external_input=False):
        build_dir = rv.REPO_ROOT / "build" / "verification_v2" / op_name
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / self.MARKER).write_text("fresh", encoding="utf-8")
        return {"sim_success": True, "branch_report": {}, "num_iterations": 1,
                "ops": [], "input_data": dict(input_data or {}),
                "log_path": str(build_dir / f"{op_name}_sim.log")}


def test_compile_stage_purges_stale_transient_artifacts(tmp_path, monkeypatch):
    build_dir = rv.REPO_ROOT / "build" / "verification_v2" / "add"
    junk = build_dir / "JUNK_STALE_FROM_PREVIOUS_RUN.txt"
    build_dir.mkdir(parents=True, exist_ok=True)
    junk.write_text("stale junk 绝不可串用", encoding="utf-8")
    monkeypatch.setattr(rv, "PipelineRunner", _OkRunner)
    try:
        cfg = mk_cfg(tmp_path, op_name="add",
                     op_yaml=str(ADD_YAML), run_id="purge_check")
        orch = rv.Orchestrator(cfg, stages=pass_stages())
        orch.stages["compile_admit"] = orch._stage_compile_admit
        report = orch.run()
        assert report["outcome"] == "GREEN"
        copied = cfg.runs_root / "add" / cfg.run_id / "n4" / "build"
        assert (copied / _OkRunner.MARKER).is_file()
        assert not (copied / junk.name).exists(), "瞬态垃圾被串用到新运行产物——运行隔离被破"
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def test_run_dir_run_id_collision_is_rejected(tmp_path):
    cfg = mk_cfg(tmp_path, run_id="collide")
    rv.Orchestrator(cfg, stages=pass_stages()).run()
    with pytest.raises(FileExistsError):
        rv.Orchestrator(cfg, stages=pass_stages()).run()


# ---------------------------------------------------------------------------
# 出口 2-d：UNVERIFIED 不发布（边界检查的两种触发）
# ---------------------------------------------------------------------------

def test_unverified_branch_not_unroll_blocks_publish(tmp_path):
    exc = lambda: rv.Unverified("BRANCH_NOT_UNROLL", "flat 分支 ISSUE 实证缺口")  # noqa: E731
    _, _, report = run_orch(
        tmp_path, stages=many_params_stages("compile_admit", exc))
    frag = report["sizes"]["4"]
    assert report["outcome"] == "UNVERIFIED"
    assert frag["unverified"]["reason_code"] == "BRANCH_NOT_UNROLL"
    assert report["published"] is False
    assert not (tmp_path / "releases").exists() or not list((tmp_path / "releases").iterdir())
    # UNVERIFIED 不是失败——failure 字段不得出现
    assert "failure" not in frag


def test_real_vcs_stage_unverified_when_run_vcs_false(tmp_path):
    """真实 ``_stage_vcs`` + run_vcs=False → VCS_NOT_RUN；其后链路 not_executed。"""
    cfg = mk_cfg(tmp_path)                       # mk_cfg 默认 run_vcs=False
    orch = rv.Orchestrator(cfg, stages=pass_stages())
    orch.stages["vcs"] = orch._stage_vcs
    report = orch.run()
    assert report["outcome"] == "UNVERIFIED"
    frag = report["sizes"]["4"]
    rec = {r["stage"]: r["status"] for r in frag["stages"]}
    assert rec["vcs"] == "unverified"
    assert frag["unverified"]["reason_code"] == "VCS_NOT_RUN"
    for later in ("native_compare", "three_zone", "arbitrate"):
        assert rec[later] == "not_executed"
    assert report["published"] is False


# ---------------------------------------------------------------------------
# 三态透亮：SOLVER_UNSAT / SOLVER_UNKNOWN 分别可触发（合成 SolverReport）
# ---------------------------------------------------------------------------

def _solver_report(final: str, ii: int = 8) -> SolverReport:
    return SolverReport(result=None,
                        per_ii=[(ii, final)],
                        final=final,
                        budget_exhausted=(final == SolverStatus.UNKNOWN.value))


@pytest.mark.parametrize("final,expect_code", [
    (SolverStatus.UNSAT.value, "SOLVER_UNSAT"),
    (SolverStatus.UNKNOWN.value, "SOLVER_UNKNOWN"),
])
def test_solver_unsat_unknown_independently_triggerable(tmp_path, monkeypatch,
                                                        final, expect_code):
    class _RejRunner:
        def __init__(self, **kw):
            pass

        def run_full_stack(self, *a, **kw):
            raise SynthesisRejected(_solver_report(final))

    monkeypatch.setattr(rv, "PipelineRunner", _RejRunner)
    cfg = mk_cfg(tmp_path, op_name="add", op_yaml=str(ADD_YAML))
    orch = rv.Orchestrator(cfg, stages=pass_stages())
    orch.stages["compile_admit"] = orch._stage_compile_admit
    report = orch.run()
    fail = report["sizes"]["4"]["failure"]
    assert report["outcome"] == "RED"
    assert fail["stage"] == "compile_admit"
    assert fail["codes"] == [expect_code]
    assert final in fail["detail"]          # z3 三态原文透出，不靠 grep PASSED


# ---------------------------------------------------------------------------
# GREEN 全通过 → 发布；成功包内嵌报告与运行目录版逐字节一致
# ---------------------------------------------------------------------------

def test_green_publishes_byte_identical_embedded_report(tmp_path):
    # mk_cfg 默认 domain_mode=frozen_regression → 发布到 regression 子通道
    cfg, orch, report = run_orch(tmp_path, run_id="green1", n_rows=[4, 8])
    assert report["outcome"] == "GREEN" and report["published"] is True
    rel_dir = Path(report["release_dir"])
    assert rel_dir.is_dir() and rel_dir.name == f"fakeop_{cfg.run_id}"
    assert rel_dir.parent.name == "regression"
    run_report = cfg.runs_root / "fakeop" / cfg.run_id / "run_report.json"
    assert (rel_dir / "run_report.json").read_bytes() == run_report.read_bytes()
    embedded = json.loads((rel_dir / "run_report.json").read_text(encoding="utf-8"))
    assert embedded["published"] is True and embedded["release_dir"] == str(rel_dir)
    # staging 目录不残留
    assert not (rel_dir.parent / f".staging_fakeop_{cfg.run_id}").exists()


def test_frozen_regression_never_lands_in_v1_channel(tmp_path):
    """用户 2026-09-30 规则：旧域回归 ≠ V1 准入，不得发布成普通 V1 已验证包。"""
    cfg, orch, report = run_orch(tmp_path, run_id="frz1")
    assert report["outcome"] == "GREEN" and report["published"] is True
    assert report["verification_class"] == "frozen_baseline_regression"
    rel_root = cfg.releases_root
    assert sorted(p.name for p in rel_root.iterdir()) == ["regression"]
    assert not (rel_root / f"fakeop_{cfg.run_id}").exists()
    rel_dir = rel_root / "regression" / f"fakeop_{cfg.run_id}"
    readme = (rel_dir / "README.md").read_text(encoding="utf-8")
    assert "历史基线回归通过包" in readme
    assert "frozen_baseline_regression" in readme
    assert "不得引用为 V1" in readme
    embedded = json.loads((rel_dir / "run_report.json").read_text(encoding="utf-8"))
    assert embedded["verification_class"] == "frozen_baseline_regression"


def test_v1_admission_green_publishes_to_main_channel(tmp_path):
    cfg, orch, report = run_orch(tmp_path, run_id="v1g", domain_mode="v1_admission")
    assert report["outcome"] == "GREEN" and report["published"] is True
    assert report["verification_class"] == "v1_admission"
    rel_dir = Path(report["release_dir"])
    assert rel_dir.parent == cfg.releases_root          # 主通道，不在 regression 下
    readme = (rel_dir / "README.md").read_text(encoding="utf-8")
    assert "V1 已验证成功包" in readme


def test_report_schema_required_checks_and_sha256sums(tmp_path):
    cfg, orch, report = run_orch(tmp_path, run_id="schema1")
    assert report["schema"] == rv.REPORT_SCHEMA == "adr056_f3_run_report_v1"
    checks = report["required_checks"]
    got = {c["check"] for c in checks if c["check"].endswith("@n4")}
    assert got == {f"{s}@n4" for s in rv.STAGE_ORDER}
    assert all(c["status"] == "pass" for c in checks)
    assert all("evidence_dir" in c for c in checks)
    run_dir = cfg.runs_root / "fakeop" / cfg.run_id
    sums = (run_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "run_config.resolved.json" in sums
    assert "run_report.json" not in sums and "SHA256SUMS.txt" not in sums.replace(
        "run_report.json", "")


# ---------------------------------------------------------------------------
# CONFIG_INVALID 契约（预检面：exit 2，无 run 目录残留）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expect", [
    ({"n_rows": [4], "mystery_key": 1}, "未知键"),
    ({"n_rows": []}, "非空正 int"),
    ({"n_rows": [0]}, None),
    ({"n_rows": [4], "domain_mode": "bogus"}, "domain_mode"),
])
def test_config_invalid_contract(tmp_path, raw, expect):
    raw = dict(raw)
    raw["op_yaml"] = str(_fake_op_yaml(tmp_path))
    with pytest.raises(rv.ConfigInvalid) as ei:
        rv.RunConfig(raw)
    if expect:
        assert expect in str(ei.value)


def test_config_missing_op_file_and_main_exit2(tmp_path, capsys):
    with pytest.raises(rv.ConfigInvalid):
        rv.RunConfig({"op_yaml": str(tmp_path / "nope.yaml"), "n_rows": [4]})
    rc = rv.main(["--op-yaml", str(tmp_path / "nope.yaml"), "--n-rows", "4",
                  "--no-run-vcs"])
    assert rc == 2
    assert "CONFIG_INVALID" in capsys.readouterr().err


def test_cli_unknown_key_config_file_rejected(tmp_path):
    cfg_yaml = tmp_path / "run_cfg.yaml"
    cfg_yaml.write_text(
        "op_yaml: {op}\nn_rows: [4]\nrun_vcs: false\nsandbox: {sb}\n"
        "runs_root: {rr}\nreleases_root: {rl}\nzz_extra: 1\n".format(
            op=_fake_op_yaml(tmp_path), sb=tmp_path, rr=tmp_path / "r", rl=tmp_path / "rl"),
        encoding="utf-8")
    with pytest.raises(rv.ConfigInvalid) as ei:
        rv.build_config(["--config", str(cfg_yaml)])
    assert "zz_extra" in str(ei.value)


# ---------------------------------------------------------------------------
# 集成：add n4 全真实阶段、run_vcs=false → UNVERIFIED（VCS_NOT_RUN）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _SANDBOX_OK, reason="VCS 沙箱不在位")
def test_integration_add_n4_unverified_no_vcs(tmp_path):
    cfg = rv.RunConfig({
        "op_yaml": str(ADD_YAML),
        "n_rows": [4],
        "domain_mode": "frozen_regression",
        "run_vcs": False,
        "runs_root": str(tmp_path / "runs"),
        "releases_root": str(tmp_path / "releases"),
    })
    report = rv.Orchestrator(cfg).run()
    assert report["outcome"] == "UNVERIFIED"
    assert report["published"] is False
    frag = report["sizes"]["4"]
    rec = {r["stage"]: r["status"] for r in frag["stages"]}
    for s in ("config_check", "formula_domain_check", "compile_admit",
              "independent_reference", "artifact_audit", "package"):
        assert rec[s] == "pass", f"{s} 须过，实得 {frag['stages']}"
    assert rec["vcs"] == "unverified"
    assert frag["unverified"]["reason_code"] == "VCS_NOT_RUN"
    ref_golden = json.loads((cfg.runs_root / "add" / cfg.run_id / "n4"
                             / "evidence" / "reference_golden.json").read_text())
    assert ref_golden["byte_identical"] is True
    assert not (tmp_path / "releases").exists() or not list((tmp_path / "releases").iterdir())
    assert report["frozen_baseline"]["checked"] is True
