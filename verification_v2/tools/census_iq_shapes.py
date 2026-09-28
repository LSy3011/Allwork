#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IQ 流形态普查 v3：与 e2e 同路径（get_dfg → rtovr 绕行 pass → solve → assemble）。
逐算子即时落盘到 /tmp/census3_progress.txt，防超时截断丢数据。"""
import sys, fcntl
from pathlib import Path

ROOT = Path("/home/juno1023/aiacc_compiler")
sys.path.append(str(ROOT)); sys.path.append(str(ROOT / "src"))
sys.path.append(str(ROOT / "sim")); sys.path.append(str(ROOT / "verification_v2"))

from api.hw_spec import HardwareSpec
from verification_v2.ops.configurable import ConfigurableOperator
from synthesis.ucode_solver import UCodeSynthesizer
from driver.assembler import PhysicalAssembler

OUT = Path("/tmp/census3_progress.txt")
spec = HardwareSpec.load(str(ROOT / "hw_config"))
syn = UCodeSynthesizer(spec)

def emit(line):
    with OUT.open("a") as f:
        f.write(line + "\n")
    print(line, flush=True)

OUT.write_text("")   # 新轮清空
emit(f"{'op':<18} {'II':<9} {'flat':>4} {'embed':>5} {'spc':>4}  embed队列")
for yp in sorted((ROOT / "verification_v2/ops").glob("*.yaml")):
    name = yp.stem
    try:
        op = ConfigurableOperator(str(yp))
        ops = op.get_dfg(spec)
        from frontend.rtovr_bypass import apply_rtovr_sparse_bypass
        ops = apply_rtovr_sparse_bypass(ops, spec)   # 同 pipeline_runner:58
        res = syn.solve_with_units(ops, MII=1, MAX_II=64)
        if res is None:
            emit(f"{name:<18} UNSAT"); continue
        II = res["II"]
        for o in ops:
            o.metadata["global_t"] = res["schedule"][o.trace_id]
            o.metadata["unit_id"] = res.get("unit_assignments", {}).get(o.trace_id)
        instrs = PhysicalAssembler(spec, verbosity=0).assemble(ops, II=II, num_iterations=1)
        per_q = {}
        for p in instrs:
            per_q.setdefault(p.iq_name, set()).add(p.stage)
        flat = sorted(q for q, s in per_q.items() if "TERM" in s and "HDR" not in s)
        embed = sorted(q for q, s in per_q.items() if "HDR" in s)
        spc = sorted(q for q, s in per_q.items() if "HDR" not in s and "TERM" not in s)
        emit(f"{name:<18} II={II:<5} {len(flat):>4} {len(embed):>5} {len(spc):>4}  "
             f"{','.join(embed) if embed else '-'}")
    except Exception as e:
        emit(f"{name:<18} ERR:{type(e).__name__} {str(e).splitlines()[0][:70]}")
emit("DONE")
