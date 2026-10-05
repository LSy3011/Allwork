"""ADR-057 拦截闸矩阵（pipeline_runner.run_full_stack 级，零 VCS / 零 SMT）。

覆盖缺口盘点（CLAUSE45_MAP 缺口②）：改道闸四分支此前无任何单元测试——
``allow_stage_partition`` 仅被 mock ``**_kw`` 吸收。本套件 mock 重层
（solver / assembler / packer / subprocess），把闸的六个行为钉死：

  A1 默认关闭：assemble 抛 G-cap 且调用方未选择加入 → 原 G-cap 逐字透出，
     分段器**未被调用**（main_verify 等既有入口契约不变，ADR-057 修订②明文）。
  A2 非 G-cap 直抛：ValueError 但无 G-cap 标记 → 原异常透出，分段器不被调用。
  A3 选择加入 + PartitionRefused → 原 G-cap 透出且 ``__cause__`` 挂拒收理由
     （诊断不丢，``raise e from pr``）。
  A4 选择加入 + 改道成功：逐段重发射收到正确 (m, stage_start)、共享同一调度、
     段间 branch_report 恒等 → 返回 stage_plan，manifest 落盘。
  A5 段间 branch_report 漂移 → [STATIC_ADMISSION_REJECTED/G-stage] 响亮拒绝。
  A6 admission=None（未请求 unroll）/ 多 SMC → 即使 allow=True 也直抛，
     分段器不被调用。
"""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import verification_v2.infra.pipeline_runner as pr
from driver.stage_partition import PartitionRefused

_REPO = Path(__file__).resolve().parents[2]

_GCAP = "[STATIC_ADMISSION_REJECTED/G-cap] 合成容量超限（测试桩）"
_OPS = [("ur-r0", 0), ("ur-r0", 4), ("rtovr-14", 2), ("rtovr-14", 8),
        ("vout0", 10)]
_II = 2
_N_EFF = 61           # 244 // lanes(4)；与 test_check_stage_manifest 同形态


# ------------------------------------------------------------------ 测试桩
class _FakeOp:
    element_bits = 32
    dtype = np.float32
    name = "fake_ck"
    formula_str = "alpha * (x + other)"
    cfg = {"name": "fake_ck", "formula": "alpha * (x + other)",
           "experimental_unroll": True}
    tensor_configs = {
        "x": {"bank": 0, "role": "input", "offset": 0, "length": 256},
        "other": {"bank": 1, "role": "input", "offset": 0, "length": 256},
        "alpha": {"bank": 3, "role": "param", "offset": 0, "length": 256},
        "y": {"bank": 2, "role": "output", "offset": 0, "length": 256},
    }

    def __init__(self, ops, experimental_unroll=True, parallel_dim=None):
        self._ops = ops
        self.experimental_unroll = experimental_unroll
        self.parallel_dim = parallel_dim

    def get_dfg(self, spec=None):
        return self._ops

    def prepare_input(self, n_rows, smc_num=1):
        return {k: np.zeros((smc_num, 8), dtype=np.float32)
                for k in ("x", "other", "alpha")}

    def inject_data(self, program, input_data, vector_lanes=None, spec=None):
        pass

    def inject_data_per_smc(self, programs, input_data, vector_lanes=None,
                            spec=None):
        pass

    def get_golden(self, input_data, vector_lanes=None):
        return {"y": np.zeros(4, dtype=np.float32), "intermediates": {}}


def _mk_ops():
    return [SimpleNamespace(trace_id=f"op{i}", op_type="UR_READ", metadata={})
            for i, _ in enumerate(_OPS)]


class _FakeSynth:
    """绕过真实 Z3 求解：直接返回与 _OPS 一致的调度/II/队列分配。"""

    def __init__(self, spec):
        pass

    def solve_with_units_report(self, ops, MII=1, MAX_II=64):
        return SimpleNamespace(result={
            "schedule": {f"op{i}": t for i, (_, t) in enumerate(_OPS)},
            "II": _II,
            "unit_assignments": {f"op{i}": q for i, (q, _) in enumerate(_OPS)},
        })


class _FakeInstr:
    def __init__(self, abs_t):
        self.abs_t = abs_t

    def to_dict(self):
        return {"abs_t": self.abs_t, "iq_name": "ur-r0", "stage": "BODY",
                "header": {}, "payload": {}}


class _AsmRecorder:
    """PhysicalAssembler 工厂桩：实例 0（闸前整体尝试）按 fail_msg 抛错；
    实例 k≥1（闸后逐段）返回合成指令并挂指定 branch_report。"""

    def __init__(self, fail_msg=_GCAP, stage_branches=({"q": "unroll"},
                                                       {"q": "unroll"})):
        self.fail_msg = fail_msg
        self.stage_branches = stage_branches
        self.instances = []

    def __call__(self, spec, verbosity=1):
        inst = _FakeAsm(self, len(self.instances))
        self.instances.append(inst)
        return inst


class _FakeAsm:
    def __init__(self, rec, idx):
        self.rec = rec
        self.idx = idx
        self.branch_report = {}
        self.assemble_calls = []

    def assemble(self, ops, II, num_iterations, admission=None, stage_start=0):
        self.assemble_calls.append({
            "num_iterations": num_iterations, "stage_start": stage_start,
            "II": II, "admission": admission, "n_ops": len(ops)})
        if self.idx == 0 and self.rec.fail_msg is not None:
            raise ValueError(self.rec.fail_msg)
        self.branch_report = dict(
            self.rec.stage_branches[min(self.idx - 1,
                                        len(self.rec.stage_branches) - 1)])
        return [_FakeInstr(abs_t=3 + num_iterations)]


class _FakePacker:
    """LIRPacker 桩：写出段目录最小布局（smc_0/iq_*.hex + sim_config.json），
    供下游 manifest 生成与 sim_config 拼接消费。"""

    def __init__(self, hw_config_name, verbosity=1):
        pass

    def emit_from_instructions(self, instrs, out_dir, program=None,
                               smc_num=None, lir_stream=None):
        smc0 = Path(out_dir) / "smc_0"
        smc0.mkdir(parents=True, exist_ok=True)
        (smc0 / "iq_rtovr-14.hex").write_text("00000001\n00000002\n",
                                              encoding="utf-8")
        cfg = {"iq_init": {"rtovr-14": "smc_0/iq_rtovr-14.hex"},
               "ur_init": {"0": "smc_0/ur_bank0.hex"}, "tensors": []}
        (Path(out_dir) / "sim_config.json").write_text(json.dumps(cfg),
                                                       encoding="utf-8")


class _PlanSpy:
    """plan_compute_stages 探针：被调用即记录并按需抛错/断言。"""

    def __init__(self, raises=None):
        self.calls = 0
        self.raises = raises

    def __call__(self, *a, **k):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        raise AssertionError("plan_compute_stages 不应被调用")


# ------------------------------------------------------------------ 环境
@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "UCodeSynthesizer", _FakeSynth)
    monkeypatch.setattr("frontend.rtovr_bypass.apply_rtovr_sparse_bypass",
                        lambda ops, spec: ops)
    runner = pr.PipelineRunner(str(_REPO / "hw_config"), show_review=False)
    runner.output_root = str(tmp_path / "build")
    asm = _AsmRecorder()
    monkeypatch.setattr(pr, "PhysicalAssembler", asm)
    return SimpleNamespace(
        runner=runner, asm=asm, monkeypatch=monkeypatch,
        input_data={k: np.arange(_N_EFF * 4, dtype=np.float32)
                    for k in ("x", "other", "alpha")})


def _run(env, op=None, input_data=None, **kw):
    op = op if op is not None else _FakeOp(_mk_ops())
    return env.runner.run_full_stack(
        op, dict(input_data or env.input_data), op_name="fake_ck", **kw)


# ------------------------------------------------------------------ A1 默认关闭
def test_a1_default_closed_passthrough(env):
    spy = _PlanSpy()
    env.monkeypatch.setattr(pr, "plan_compute_stages", spy)
    with pytest.raises(ValueError) as ei:
        _run(env)                      # allow_stage_partition 缺省 False
    assert str(ei.value) == _GCAP      # 原 G-cap 诊断逐字透出
    assert ei.value.__cause__ is None
    assert spy.calls == 0              # 分段器未被调用
    assert len(env.asm.instances) == 1  # 仅闸前整体尝试一次


# ------------------------------------------------------------------ A2 非 G-cap
def test_a2_non_gcap_passthrough(env):
    env.asm.fail_msg = "[STATIC_ADMISSION_REJECTED/G-dp] 合成数据通路拒收（测试桩）"
    spy = _PlanSpy()
    env.monkeypatch.setattr(pr, "plan_compute_stages", spy)
    with pytest.raises(ValueError) as ei:
        _run(env, allow_stage_partition=True)
    assert str(ei.value) == env.asm.fail_msg
    assert spy.calls == 0


# ------------------------------------------------------------------ A3 拒收挂因
def test_a3_partition_refused_chains_cause(env):
    refusal = PartitionRefused("合成拒收（测试桩）")
    spy = _PlanSpy(raises=refusal)
    env.monkeypatch.setattr(pr, "plan_compute_stages", spy)
    with pytest.raises(ValueError) as ei:
        _run(env, allow_stage_partition=True)
    assert str(ei.value) == _GCAP           # 原 G-cap 诊断逐字保留
    assert ei.value.__cause__ is refusal    # 分段器理由挂 __cause__，不丢
    assert spy.calls == 1


# ------------------------------------------------------------------ A4 改道成功
def test_a4_reroute_success(env):
    env.monkeypatch.setattr(pr, "LIRPacker", _FakePacker)
    env.monkeypatch.setattr(pr.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(returncode=0))
    res = _run(env, allow_stage_partition=True)

    # 分段计划（真实 plan_compute_stages 在合成调度上重算）
    assert res["stage_plan"] == {
        "n_eff": _N_EFF, "ii": _II, "m_max": 60,
        "stages": [{"stage_id": 0, "start": 0, "m": 60},
                   {"stage_id": 1, "start": 60, "m": 1}]}
    assert res["physical_instrs"] is None     # 多段无单一指令流

    # 逐段重发射：共享同一 emit_ops（同一 SMT 调度），参数逐段正确
    assert len(env.asm.instances) == 3        # 闸前 1 + 逐段 2
    first = env.asm.instances[0].assemble_calls[0]
    assert first["num_iterations"] == _N_EFF  # 闸前整体尝试
    per_stage = [(i.assemble_calls[0]["num_iterations"],
                  i.assemble_calls[0]["stage_start"])
                 for i in env.asm.instances[1:]]
    assert per_stage == [(60, 0), (1, 60)]
    admissions = {id(i.assemble_calls[0]["admission"])
                  for i in env.asm.instances}
    assert len(admissions) == 1               # 同一份 admission 贯穿
    assert env.asm.instances[0].assemble_calls[0]["admission"] is not None

    # 产物：manifest + 顶层 stages 契约落盘
    out = Path(res["output_dir"])
    assert (out / "stage_manifest.json").exists()
    top_cfg = json.loads((out / "sim_config.json").read_text())
    assert [(s["start"], s["m"]) for s in top_cfg["stages"]] == [(0, 60),
                                                                 (60, 1)]


# ------------------------------------------------------------------ A5 段间漂移
def test_a5_branch_drift_rejected(env):
    env.asm.stage_branches = ({"q": "unroll"}, {"q": "embed"})
    with pytest.raises(ValueError) as ei:
        _run(env, allow_stage_partition=True)
    assert "[STATIC_ADMISSION_REJECTED/G-stage]" in str(ei.value)


# ------------------------------------------------------------------ A6 不适用形态
def test_a6_admission_none_passthrough(env):
    """未请求 unroll → admission=None → 即使 allow=True 也直抛原 G-cap。"""
    spy = _PlanSpy()
    env.monkeypatch.setattr(pr, "plan_compute_stages", spy)
    op = _FakeOp(_mk_ops(), experimental_unroll=False)
    with pytest.raises(ValueError) as ei:
        _run(env, op=op, allow_stage_partition=True)
    assert str(ei.value) == _GCAP
    assert spy.calls == 0


def test_a6_multi_smc_passthrough(env):
    """多 SMC（parallel_dim）→ 超 ADR-057 范围 → 直抛原 G-cap。"""
    spy = _PlanSpy()
    env.monkeypatch.setattr(pr, "plan_compute_stages", spy)
    op = _FakeOp(_mk_ops(), parallel_dim="N")
    small = {k: np.zeros(8, dtype=np.float32) for k in ("x", "other", "alpha")}
    with pytest.raises(ValueError) as ei:
        _run(env, op=op, input_data=small, allow_stage_partition=True)
    assert str(ei.value) == _GCAP
    assert spy.calls == 0
