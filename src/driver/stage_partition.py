"""ADR-057 D1：受限 COMPUTE 自动分段器（统一逻辑迭代切分）。

职责：当 unroll 展开程序超 IQ 容量墙（SMI_IQ_INSTQ_DEPTH=128，smi_top.v:22）时，
把 N_eff 次逻辑迭代切成「等长满段 + 尾段」的 COMPUTE 段序列，使**每个队列**每段
行数 L_q(m) = II·(m + span_q) + 2 ≤ capacity。

输入独立性（D1「最终以实际行数校验」的配套）：
  * span_q 从 **solver 调度**（op.metadata["global_t"] / ["unit_id"]）重算，
    与 assembler 发射路径共享公式但不回读发射产物；
  * 实际行数终裁由 assembler 既有闸门承担（ADR-055 D3 实算↔构造互校 +
    G-cap 容量硬拒，分段后逐段生效）。

边界（ADR-057「目标与边界」）：仅 II≥2 纯 unroll 形态（无 stream_beats /
dst_explicit / explicit_descriptor / smi-ds-t 等专用分支标记）；LOAD/STORE、
多 SMC、ragged tail（n_eff 不整除=尾段，此处支持；向量尾批属 §A.2-5 已在 L1 拒）、
chunk、反压均为非目标。

拒收语义（ADR-057「对 ADR-056 既有闸门的修订」②）：抛 ``PartitionRefused``——
调用方（pipeline_runner）须随即落回原 G-cap 响亮拒绝，不得静默换码。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# IQ 容量墙唯一来源在 assembler（smi_top.v:22 SMI_IQ_INSTQ_DEPTH）；此处 import
# 保证分段器与发射器容量判定永不同源漂移。
from driver.assembler import SMI_IQ_INSTQ_DEPTH

# 专用发射分支的元数据标记（hit 任一即非「纯 unroll」形态，分段器拒收——
# 这些分支的行数规则不是 II·(m+span)+2，D1 明文禁止套公式）。
_NON_UNROLL_MARKERS = (
    "stream_beats",          # :240 流式 N 条显式分支
    "dst_explicit_emit",     # :305 DST 显式 abs_t 分支
    "explicit_descriptor",   # :347 多描述符 LDB 分支
    "dst_two_gather_sep",    # :284 DST 两-gather CRI 分支
    "smi_ds_t_gather",       # SMI_DS_T datapath（G-dp 已拒，双保险）
)


class PartitionRefused(ValueError):
    """分段器拒收（非目标形态 / 不足以容纳一次完整迭代 / 布局不支持）。

    是 ValueError 子类：调用方捕获后**必须**重抛原 G-cap 异常（保留原诊断），
    本异常只承载「为何不能分段」的附加说明。
    """


@dataclass(frozen=True)
class ComputeStage:
    """单段计划（D3 manifest 的每段语义源）。区间半开 [start, end)。"""
    stage_id: int
    start: int          # 段起始全局逻辑迭代 s_k
    m: int              # 本段迭代数
    predecessor: Optional[int]
    kind: str = "COMPUTE"

    @property
    def end(self) -> int:
        return self.start + self.m


@dataclass(frozen=True)
class StagePlan:
    """分段计划：精确覆盖 [0, n_eff) 的段序列 + 每队列容量模型参数。"""
    n_eff: int
    ii: int
    capacity: int
    m_max: int                      # 单段最大迭代数（束缚队列顶格）
    spans: Dict[str, int]           # 每队列 span_q = max_t//II − min_t//II
    stages: List[ComputeStage] = field(default_factory=list)

    def predicted_rows(self, iq_name: str, m: int) -> int:
        """L_q(m) = II·(m + span_q) + 2（候选公式；实际行数以发射产物为准）。"""
        return self.ii * (m + self.spans[iq_name]) + 2


def _norm_queue(unit_id: Any) -> str:
    """队列名归一化——与 emit_expected_fire_sequence._norm 同一口径
    （小写 + '_'→'-'），保证分段器/期望事件检查器/分支表三方键名一致。"""
    return str(unit_id).strip().lower().replace("_", "-")


def compute_queue_spans(ops: List[Any], ii: int) -> Dict[str, int]:
    """从调度元数据重算每队列 span_q = (max_t//II) − (min_t//II)。

    与 assembler 行数公式同源：R_q = (max_t//II + m) − skip_q，
    skip_q = min_t//II → L_q(m) = II·(span_q + m) + 2。
    """
    by_q: Dict[str, List[int]] = {}
    for op in ops:
        uid = op.metadata.get("unit_id")
        if not uid:
            continue
        t = op.metadata.get("global_t")
        if t is None:
            raise PartitionRefused(
                f"op={getattr(op, 'trace_id', '?')} 缺 global_t——调度未落定，拒绝分段")
        by_q.setdefault(_norm_queue(uid), []).append(int(t))
    if not by_q:
        raise PartitionRefused("LIR 无任何 unit_id 归属——无队列可分段")
    return {q: (max(ts) // ii) - (min(ts) // ii) for q, ts in by_q.items()}


def plan_compute_stages(ops: List[Any], ii: int, n_eff: int,
                        capacity: int = SMI_IQ_INSTQ_DEPTH) -> StagePlan:
    """D1 统一切分：全体相关队列容量约束共同决定段长，等长满段 + 尾段。

    拒收（PartitionRefused）：
      * II < 2（unroll 准入分支不存在，分段无意义）；
      * 任一 op 带专用分支标记（行数规则非 II·(m+span)+2，D1 禁止套公式）；
      * n_eff ≤ 0；
      * m_max < 1（最紧队列连一次完整迭代都容纳不下）；
      * m_max ≥ n_eff（公式说单段装得下——与触发本器的 G-cap 事实矛盾，
        即公式/实现漂移；拒绝分段，让原 G-cap 诊断出场）。
    """
    if ii < 2:
        raise PartitionRefused(f"II={ii}<2：非 unroll 展开形态，不属 ADR-057 分段范围")
    if n_eff <= 0:
        raise PartitionRefused(f"n_eff={n_eff}≤0：无迭代可切分")
    for op in ops:
        for marker in _NON_UNROLL_MARKERS:
            if op.metadata.get(marker):
                raise PartitionRefused(
                    f"op={getattr(op, 'trace_id', '?')} 带专用分支标记 {marker}——"
                    f"行数规则非 II·(m+span)+2（D1 禁止套公式），分段器拒收")
        uid = str(op.metadata.get("unit_id", "")).lower()
        if "smi-ds-t" in uid:
            raise PartitionRefused("SMI_DS_T datapath IQ 不属分段范围（G-dp 已拒）")

    spans = compute_queue_spans(ops, ii)
    headroom = {q: (capacity - 2) // ii - sp for q, sp in spans.items()}
    binding = min(headroom, key=lambda q: headroom[q])
    m_max = headroom[binding]
    if m_max < 1:
        raise PartitionRefused(
            f"束缚队列 {binding}：span={spans[binding]}，单段连 1 次完整迭代都容纳不下"
            f"（L={ii}·(1+{spans[binding]})+2 > {capacity}）——ADR-057 D1 明确拒绝")
    if m_max >= n_eff:
        raise PartitionRefused(
            f"容量模型矛盾：公式 m_max={m_max} ≥ n_eff={n_eff}，但发射侧 G-cap 已触发"
            f"——公式与实算漂移（束缚队列 {binding}），拒绝按公式分段")

    stages: List[ComputeStage] = []
    s = 0
    sid = 0
    while s < n_eff:
        m = min(m_max, n_eff - s)
        stages.append(ComputeStage(stage_id=sid, start=s, m=m,
                                   predecessor=(sid - 1) if sid else None))
        s += m
        sid += 1
    # D1 覆盖断言：半开区间精确覆盖 [0, n_eff)，无重叠/遗漏/空段
    assert stages and stages[0].start == 0 and stages[-1].end == n_eff
    assert all(st.m > 0 for st in stages)
    assert all(stages[i].end == stages[i + 1].start for i in range(len(stages) - 1))
    return StagePlan(n_eff=n_eff, ii=ii, capacity=capacity, m_max=m_max,
                     spans=spans, stages=stages)
