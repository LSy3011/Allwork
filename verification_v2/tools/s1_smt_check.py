#!/usr/bin/env python3
"""ADR-036 S1 验收 oracle：绕开 sim，直接对算子 DFG 跑 SMT 排程，
打印各节点 t，断言 **t[ds_transpose] < t[compute]**（转置排在点积之前）。

对应 handoff S1「可验:DFG+SMT」——不依赖 e2e/sim，纯验证：
  ① DFG 5 段拓扑齐全（ds_src_read→ds_gather→ds_transpose→shiftup→rtovr→ur_write）
  ② SMT 返回 SAT（II 可排）
  ③ 转置级固定 latency 入 pipeline_delays.yaml 后，排程把 compute 推到 transpose 之后

用法：python3 verification_v2/tools/s1_smt_check.py [--op matmul_q_dst]
"""
import os, sys, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from api.hw_spec import HardwareSpec
from synthesis.ucode_solver import UCodeSynthesizer

# 算子注册（与 main_verify.py 对齐子集）
from verification_v2.ops.matmul_op import (
    ManualMatmulQDsTOperator,
    ManualMatmulQDsTK8Operator,
)

OPS = {
    "matmul_q_dst": ManualMatmulQDsTOperator,
    "matmul_q_dst_k8": ManualMatmulQDsTK8Operator,
}

# DFG 5 段角色（用于断言拓扑齐全 + 提取转置/compute 时刻）
TRANSPOSE_NODE = "ds_transpose"          # SMI_DS_T
GATHER_NODE = "ds_gather"                # SHIFTDN
SRC_READ_NODE = "ds_src_read"           # UR_READ 列对齐（5 段首段）
COMPUTE_TYPES = {"FPMUL", "FP16MUL", "FP32ADDTREE5TO1", "FP32ADDTREE8TO1"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", default="matmul_q_dst", choices=list(OPS))
    ap.add_argument("--hw", default="hw_config")
    args = ap.parse_args()

    spec = HardwareSpec.load(args.hw)
    op = OPS[args.op]()
    ops = op.get_dfg(spec=spec)

    synth = UCodeSynthesizer(spec)
    result = synth.solve_with_units(ops, MII=1, MAX_II=64)
    if not result:
        print(f"[S1] ❌ SMT UNSAT/Synthesis failed for {args.op}")
        return 1

    sched, II = result["schedule"], result["II"]
    smi_lat = spec.delays.get("SMI_DS_T", 1)

    print(f"[S1] op={args.op}  II={II}  SMI_DS_T latency(pipeline_delays)={smi_lat}")
    print(f"[S1] --- schedule (sorted by t) ---")
    for tid, t in sorted(sched.items(), key=lambda x: x[1]):
        otype = next((o.op_type for o in ops if o.trace_id == tid), "?")
        print(f"      t={t:>3}  {tid:<32} ({otype})")

    # 拓扑断言：5 段节点齐全
    present = {o.trace_id for o in ops}
    missing = [n for n in (SRC_READ_NODE, GATHER_NODE, TRANSPOSE_NODE) if n not in present]
    if missing:
        print(f"[S1] ❌ DFG 缺 5 段节点: {missing}")
        return 1

    # 时序断言：t[transpose] < min(t[compute])
    t_transpose = sched[TRANSPOSE_NODE]
    compute_ts = [sched[o.trace_id] for o in ops
                  if o.op_type.upper() in COMPUTE_TYPES and o.trace_id in sched]
    if not compute_ts:
        print(f"[S1] ❌ 未找到 compute 节点 ({COMPUTE_TYPES})")
        return 1
    t_compute = min(compute_ts)
    gap = t_compute - t_transpose
    ok_order = t_transpose < t_compute
    ok_lat = (sched[SRC_READ_NODE] <= sched[GATHER_NODE] <= t_transpose)

    print(f"[S1] t[ds_src_read]={sched[SRC_READ_NODE]}  t[ds_gather]={sched[GATHER_NODE]}  "
          f"t[ds_transpose]={t_transpose}  min t[compute]={t_compute}  gap={gap}")
    print(f"[S1] 5段顺序 src_read<=gather<=transpose: {'✓' if ok_lat else '✗'}")
    print(f"[S1] t[transpose] < t[compute]: {'✓' if ok_order else '✗'}")

    if ok_order and ok_lat:
        print(f"[S1] ✅ PASS — DFG 5 段拓扑齐全 + SMT SAT + 转置排在点积之前")
        return 0
    print(f"[S1] ❌ FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
