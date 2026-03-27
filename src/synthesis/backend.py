from typing import List, Dict, Optional
import z3
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec
from .axioms import SynthesisAxioms

class SMTSynthesizer:
    def __init__(self, hw_spec: HardwareSpec):
        self.hw_spec = hw_spec

    def solve(self, ops: List[MIROp], mode="stable") -> Optional[Dict[str, int]]:
        z3.set_param('smt.core.resplit', True)
        t_vars = {op.node_id: z3.Int(f"t_{op.node_id}") for op in ops}
        
        opt = z3.Optimize()
        for t in t_vars.values(): opt.add(t >= 0)

        # 核心：透传 mode 到公理层
        SynthesisAxioms.add_latency_axiom(opt, ops, t_vars, self.hw_spec, mode=mode)
        SynthesisAxioms.add_iq_mutex_axiom(opt, ops, t_vars)
        SynthesisAxioms.add_bank_conflict_axiom(opt, ops, t_vars)
        SynthesisAxioms.add_rtovr_crossbar_axiom(opt, ops, t_vars, self.hw_spec, mode=mode)
        SynthesisAxioms.add_objective(opt, t_vars)
        
        if opt.check() == z3.sat:
            model = opt.model()
            return {oid: model[t_var].as_long() for oid, t_var in t_vars.items()}
        else:
            # 诊断逻辑 (回退到 Solver 以获取 UNSAT Core)
            s = z3.Solver()
            for t in t_vars.values(): s.add(t >= 0)
            SynthesisAxioms.add_latency_axiom(s, ops, t_vars, self.hw_spec, mode=mode)
            SynthesisAxioms.add_iq_mutex_axiom(s, ops, t_vars, mode=mode)
            SynthesisAxioms.add_bank_conflict_axiom(s, ops, t_vars)
            SynthesisAxioms.add_rtovr_crossbar_axiom(s, ops, t_vars, self.hw_spec, mode=mode)
            if s.check() == z3.unsat:
                 print(f"DEBUG: UNSAT Core [{mode} mode]:", s.unsat_core())
            return None
