"""ADR-057 D1 分段器契约测试：段长公式、覆盖精确性、拒收语义。

scale_sum 形态锚点（实测调度，II=2）：rtovr-14 span=3、vin0/ur-r0 span=2、
vout0/vin1 span=0 → m_max=60（rtovr-14 于 m=60 恰 128 行顶格，步⑤实测复核）。
"""
import pytest
from types import SimpleNamespace

from driver.stage_partition import (
    PartitionRefused, compute_queue_spans, plan_compute_stages,
)


def _op(tid, unit, t, **markers):
    md = {"unit_id": unit, "global_t": t}
    md.update(markers)
    return SimpleNamespace(trace_id=tid, metadata=md)


# scale_sum 调度形态（每队列若干 op 的 global_t；span 由 max//II−min//II 决定）
def scale_sum_ops():
    return [
        _op("x_read", "ur-r0", 0), _op("other_read", "ur-r0", 4),
        _op("alpha_read", "ur-r0", 2),
        _op("vin0_op", "vin0", 1), _op("vin1_op", "vin1", 5),
        _op("mul_op", "fpmul", 6), _op("add_op", "fpadd", 8),
        _op("rtovr_w", "rtovr-14", 2), _op("rtovr_r", "rtovr-14", 8),   # span=3
        _op("vout0_op", "vout0", 10),
    ]


def test_spans_from_schedule():
    spans = compute_queue_spans(scale_sum_ops(), ii=2)
    assert spans["rtovr-14"] == 8 // 2 - 2 // 2 == 3
    assert spans["ur-r0"] == 4 // 2 - 0 == 2
    assert spans["vin0"] == 0 and spans["vout0"] == 0 and spans["fpmul"] == 0


def test_scale_sum_n244_two_segments():
    plan = plan_compute_stages(scale_sum_ops(), ii=2, n_eff=61)
    assert plan.m_max == 60
    assert [(s.start, s.m) for s in plan.stages] == [(0, 60), (60, 1)]
    assert plan.stages[0].predecessor is None and plan.stages[1].predecessor == 0
    # 束缚队列顶格复核：段0 rtovr-14 恰 128 行；尾段 1 迭代 = 10 行
    assert plan.predicted_rows("rtovr-14", 60) == 128
    assert plan.predicted_rows("rtovr-14", 1) == 10
    assert plan.predicted_rows("vout0", 60) == 122


def test_scale_sum_n480_equal_segments():
    plan = plan_compute_stages(scale_sum_ops(), ii=2, n_eff=120)
    assert [(s.start, s.m) for s in plan.stages] == [(0, 60), (60, 60)]


@pytest.mark.parametrize("n_eff", [62, 61, 119, 120, 121, 180, 181, 300])
def test_coverage_exact_no_overlap_no_gap(n_eff):
    plan = plan_compute_stages(scale_sum_ops(), ii=2, n_eff=n_eff)
    covered = []
    for st in plan.stages:
        assert st.m > 0, "空段禁止"
        covered.extend(range(st.start, st.end))
    assert covered == list(range(n_eff)), "半开区间须精确覆盖 [0, n_eff)"
    assert all(st.m <= plan.m_max for st in plan.stages)
    # 满段等长、仅末段可短（等长满段+尾段）
    assert all(st.m == plan.m_max for st in plan.stages[:-1])


def test_every_stage_every_queue_fits():
    plan = plan_compute_stages(scale_sum_ops(), ii=2, n_eff=245)
    for st in plan.stages:
        for q in plan.spans:
            assert plan.predicted_rows(q, st.m) <= 128, (q, st.stage_id)


def test_reject_ii1():
    with pytest.raises(PartitionRefused, match="II=1"):
        plan_compute_stages(scale_sum_ops(), ii=1, n_eff=100)


def test_reject_nonpositive_n_eff():
    with pytest.raises(PartitionRefused, match="n_eff"):
        plan_compute_stages(scale_sum_ops(), ii=2, n_eff=0)


def test_reject_dedicated_branch_marker():
    ops = scale_sum_ops() + [_op("dst", "ur-w0", 12, stream_beats=16)]
    with pytest.raises(PartitionRefused, match="stream_beats"):
        plan_compute_stages(ops, ii=2, n_eff=100)


def test_reject_smi_ds_t_unit():
    ops = scale_sum_ops() + [_op("ds", "smi-ds-t", 12)]
    with pytest.raises(PartitionRefused, match="SMI_DS_T"):
        plan_compute_stages(ops, ii=2, n_eff=100)


def test_reject_cannot_hold_single_iteration():
    # 构造 span 巨大队列：t∈{0, 200}，II=2 → span=100 → headroom 63−100 < 0
    ops = [_op("a", "ur-r0", 0), _op("b", "ur-r0", 200)]
    with pytest.raises(PartitionRefused, match="1 次完整迭代"):
        plan_compute_stages(ops, ii=2, n_eff=100)


def test_reject_formula_actual_drift():
    """公式说单段装得下（m_max≥n_eff）却被调用——说明 G-cap 未真触发或公式漂移，
    拒绝分段（让原 G-cap 诊断出场）。"""
    with pytest.raises(PartitionRefused, match="漂移"):
        plan_compute_stages(scale_sum_ops(), ii=2, n_eff=10)


def test_missing_global_t_refused():
    ops = [_op("a", "ur-r0", None)]
    with pytest.raises(PartitionRefused, match="global_t"):
        plan_compute_stages(ops, ii=2, n_eff=100)


def test_missing_unit_id_refused():
    ops = [_op("a", None, 0)]
    with pytest.raises(PartitionRefused, match="无队列"):
        plan_compute_stages(ops, ii=2, n_eff=100)
