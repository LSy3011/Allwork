from typing import List, Dict, Any, Union
import z3
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec

class SynthesisAxioms:
    @staticmethod
    def _add_constraint(solver, constraint: Any, label_name: str):
        if isinstance(solver, z3.Solver):
            solver.assert_and_track(constraint, z3.Bool(label_name))
        else:
            solver.add(constraint)

    @staticmethod
    def add_latency_axiom(solver, ops: List[MIROp], 
                         t_vars: Dict[str, z3.ArithRef], spec: HardwareSpec, mode="stable"):
        """
        时序因果链条公理：保证 RAW 依赖正确。
        已切换至 trace_id 索引。
        """
        for op in ops:
            for dep_id in op.metadata.get("depends_on", []):
                producer = next((o for o in ops if o.trace_id == dep_id), None)
                if not producer: continue
                lat = spec.delays.get(producer.op_type, 1)
                
                label = f"lat_{op.trace_id}_{dep_id}_{mode}"
                if mode == "stable":
                    if producer.op_type == "UR_READ":
                        SynthesisAxioms._add_constraint(solver, t_vars[op.trace_id] >= t_vars[dep_id] + lat, f"flex_{label}")
                    else:
                        SynthesisAxioms._add_constraint(solver, t_vars[op.trace_id] == t_vars[dep_id] + lat, label)
                else:
                    SynthesisAxioms._add_constraint(solver, t_vars[op.trace_id] >= t_vars[dep_id] + lat, label)

    @staticmethod
    def add_rtovr_crossbar_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], spec: HardwareSpec, mode="stable"):
        rt_ops = [o for o in ops if o.op_type == "RTOVR"]
        slot_groups = {}
        for op in rt_ops:
            slot = op.metadata.get("slot_id")
            if slot is not None: slot_groups.setdefault(slot, []).append(op)

        for slot, slot_ops in slot_groups.items():
            if mode == "stable":
                if len(slot_ops) > 1:
                    SynthesisAxioms._add_constraint(solver, z3.Distinct([t_vars[o.trace_id] for o in slot_ops]), f"slot_dist_{slot}")
            else:
                for i, op_i in enumerate(slot_ops):
                    consumers = [o for o in ops if op_i.trace_id in o.metadata.get("depends_on", [])]
                    for cons in consumers:
                        t_start = t_vars[op_i.trace_id]
                        t_end = t_vars[cons.trace_id]
                        for j, op_j in enumerate(slot_ops):
                            if i == j: continue
                            uid = "_".join(sorted([op_i.trace_id, op_j.trace_id]))
                            SynthesisAxioms._add_constraint(solver, z3.Or(t_vars[op_j.trace_id] < t_start, t_vars[op_j.trace_id] > t_end), f"slot_ival_{slot}_{uid}")

    @staticmethod
    def add_iq_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], mode="stable"):
        if mode == "stable":
            iq_groups = {}
            for op in ops:
                iq = op.metadata.get("iq_id")
                if iq is not None: iq_groups.setdefault(iq, []).append(op.trace_id)
            for iq, op_ids in iq_groups.items():
                if len(op_ids) > 1:
                    SynthesisAxioms._add_constraint(solver, z3.Distinct([t_vars[oid] for oid in op_ids]), f"iq_mutex_{iq}")
        else:
            load_ops = [o for o in ops if o.op_type == "UR_READ"]
            iq_groups = {}
            for op in load_ops:
                iq = op.metadata.get("iq_id")
                if iq is not None: iq_groups.setdefault(iq, []).append(op)
            for iq, iq_ops in iq_groups.items():
                for i, op_i in enumerate(iq_ops):
                    consumers = [o for o in ops if op_i.trace_id in o.metadata.get("depends_on", [])]
                    for cons in consumers:
                        t_start = t_vars[op_i.trace_id]
                        t_end = t_vars[cons.trace_id] - 1
                        for j, op_j in enumerate(iq_ops):
                            if i == j: continue
                            uid = "_".join(sorted([op_i.trace_id, op_j.trace_id]))
                            SynthesisAxioms._add_constraint(solver, z3.Or(t_vars[op_j.trace_id] < t_start, t_vars[op_j.trace_id] > t_end), f"iq_ival_{iq}_{uid}")

    @staticmethod
    def add_liveness_flush_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef]):
        """
        防冲刷公理：已切换至 trace_id。
        """
        read_ops = [o for o in ops if o.op_type.upper() == "UR_READ"]
        for i in range(len(read_ops)):
            for j in range(i + 1, len(read_ops)):
                op_A, op_B = read_ops[i], read_ops[j]
                consumers_A = [o for o in ops if op_A.trace_id in o.metadata.get("depends_on", [])]
                consumers_B = [o for o in ops if op_B.trace_id in o.metadata.get("depends_on", [])]
                
                cond_B_after_A = z3.And([t_vars[op_B.trace_id] > t_vars[c.trace_id] for c in consumers_A]) if consumers_A else t_vars[op_B.trace_id] > t_vars[op_A.trace_id]
                cond_A_after_B = z3.And([t_vars[op_A.trace_id] > t_vars[c.trace_id] for c in consumers_B]) if consumers_B else t_vars[op_A.trace_id] > t_vars[op_B.trace_id]
                
                solver.add(z3.Implies(iq_vars[op_A.trace_id] == iq_vars[op_B.trace_id], z3.Or(cond_B_after_A, cond_A_after_B)))

    @staticmethod
    def add_bank_conflict_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef]):
        """
        Bank 冲突公理：已切换至 trace_id。
        """
        bank_ops = [o for o in ops if "bank_id" in o.metadata]
        bank_groups = {}
        for op in bank_ops:
            bid = op.metadata["bank_id"]
            bank_groups.setdefault(bid, []).append(op.trace_id)
        
        for bid, op_ids in bank_groups.items():
            if len(op_ids) > 1:
                SynthesisAxioms._add_constraint(solver, z3.Distinct([t_vars[oid] for oid in op_ids]), f"bank_mutex_{bid}")

    @staticmethod
    def add_objective(solver: z3.Optimize, t_vars: Dict[str, z3.ArithRef]):
        max_t = z3.Int("max_t")
        for t in t_vars.values(): solver.add(max_t >= t)
        solver.minimize(max_t)
