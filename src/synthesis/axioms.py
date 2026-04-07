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
    def add_rtovr_crossbar_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], spec: HardwareSpec):
        """
        槽位存活公理：在 [Pulse, Fire] 区间内，物理槽位不可被冲刷。
        """
        rt_ops = [o for o in ops if o.op_type == "RTOVR"]
        slot_groups = {}
        for op in rt_ops:
            slot = op.metadata.get("slot_id")
            if slot is not None: slot_groups.setdefault(slot, []).append(op)

        for slot, slot_ops in slot_groups.items():
            if len(slot_ops) <= 1: continue
            
            for i, op_i in enumerate(slot_ops):
                # 找到使用这个搬运结果的计算节点 (消费者)
                consumers = [o for o in ops if op_i.trace_id in o.metadata.get("depends_on", [])]
                for cons in consumers:
                    t_pulse_i = t_vars[op_i.trace_id]
                    t_fire_i = t_vars[cons.trace_id]
                    
                    for j, op_j in enumerate(slot_ops):
                        if i == j: continue
                        t_pulse_j = t_vars[op_j.trace_id]
                        # 约束：j 的搬运必须在 i 的区间之外 (Unique Label with consumer)
                        uid = "_".join(sorted([op_i.trace_id, op_j.trace_id]))
                        SynthesisAxioms._add_constraint(solver, z3.Or(t_pulse_j < t_pulse_i, t_pulse_j > t_fire_i), f"slot_ival_{slot}_{uid}_{cons.trace_id}")

    @staticmethod
    def add_read_port_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef]):
        """
        读端口时分复用公理：同物理读端口的不同读操作必须不同拍。
        """
        read_ops = [o for o in ops if o.op_type.upper() == "UR_READ"]
        for i in range(len(read_ops)):
            for j in range(i + 1, len(read_ops)):
                op_i, op_j = read_ops[i], read_ops[j]
                if op_i.trace_id in iq_vars and op_j.trace_id in iq_vars:
                    same_port = iq_vars[op_i.trace_id] == iq_vars[op_j.trace_id]
                    SynthesisAxioms._add_constraint(
                        solver,
                        z3.Implies(same_port, t_vars[op_i.trace_id] != t_vars[op_j.trace_id]),
                        f"rport_tdm_{op_i.trace_id}_{op_j.trace_id}"
                    )

    @staticmethod
    def add_iq_latch_protect_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef]):
        """
        IQ 锁存器保护公理：在搬运搬走数据前，IQ 不能被新的读操作覆盖。
        """
        read_ops = [o for o in ops if o.op_type.upper() == "UR_READ"]
        for i in range(len(read_ops)):
            for j in range(i + 1, len(read_ops)):
                op_A, op_B = read_ops[i], read_ops[j]
                if op_A.trace_id not in iq_vars or op_B.trace_id not in iq_vars: continue
                
                same_iq = iq_vars[op_A.trace_id] == iq_vars[op_B.trace_id]
                
                def get_protection_window(op_x):
                    consumers = [o for o in ops if op_x.trace_id in o.metadata.get("depends_on", [])]
                    if not consumers: return t_vars[op_x.trace_id]
                    m_val = t_vars[consumers[0].trace_id]
                    for cons in consumers[1:]:
                        m_val = z3.If(t_vars[cons.trace_id] > m_val, t_vars[cons.trace_id], m_val)
                    return m_val

                t_end_A = get_protection_window(op_A)
                t_end_B = get_protection_window(op_B)
                
                cond_B_after_A = t_vars[op_B.trace_id] > t_end_A
                cond_A_after_B = t_vars[op_A.trace_id] > t_end_B
                
                SynthesisAxioms._add_constraint(
                    solver,
                    z3.Implies(same_iq, z3.Or(cond_B_after_A, cond_A_after_B)),
                    f"iq_l_prot_{op_A.trace_id}_{op_B.trace_id}"
                )

    @staticmethod
    def add_alu_pipeline_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef], spec: HardwareSpec):
        """
        [ADPC 物理对齐] ALU 流水线公理：物理上共用同一单元的操作，其结果写回前必须搬走前一结果。
        """
        for alu_type in ["FPMUL", "FPADD"]:
            alu_ops = [o for o in ops if o.op_type == alu_type]
            if len(alu_ops) <= 1: continue
            lat = spec.delays.get(alu_type, 4)
            
            for i in range(len(alu_ops)):
                for j in range(i + 1, len(alu_ops)):
                    op_i, op_j = alu_ops[i], alu_ops[j]
                    
                    if op_i.trace_id in iq_vars and op_j.trace_id in iq_vars:
                        same_unit = iq_vars[op_i.trace_id] == iq_vars[op_j.trace_id]
                        
                        # 1. 发射排他
                        SynthesisAxioms._add_constraint(solver, 
                            z3.Implies(same_unit, t_vars[op_i.trace_id] != t_vars[op_j.trace_id]),
                            f"alu_issue_{alu_type}_{op_i.trace_id}_{op_j.trace_id}")
                        
                        # 2. Liveness 保护
                        cons_i = [o for o in ops if op_i.trace_id in o.metadata.get("depends_on", []) and o.op_type == "RTOVR"]
                        cons_j = [o for o in ops if op_j.trace_id in o.metadata.get("depends_on", []) and o.op_type == "RTOVR"]
                        
                        if cons_i:
                            i_before_j = t_vars[op_i.trace_id] < t_vars[op_j.trace_id]
                            i_move_done = z3.And([t_vars[c.trace_id] < t_vars[op_j.trace_id] + lat for c in cons_i])
                            SynthesisAxioms._add_constraint(solver, 
                                z3.Implies(z3.And(same_unit, i_before_j), i_move_done),
                                f"alu_l_i_{op_i.trace_id}_j_{op_j.trace_id}")
                                
                        if cons_j:
                            j_before_i = t_vars[op_j.trace_id] < t_vars[op_i.trace_id]
                            j_move_done = z3.And([t_vars[c.trace_id] < t_vars[op_i.trace_id] + lat for c in cons_j])
                            SynthesisAxioms._add_constraint(solver, 
                                z3.Implies(z3.And(same_unit, j_before_i), j_move_done),
                                f"alu_l_j_{op_j.trace_id}_i_{op_i.trace_id}")

    @staticmethod
    def add_write_port_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef]):
        """
        写端口时分复用公理。
        """
        write_ops = [o for o in ops if o.op_type.upper() == "UR_WRITE"]
        for i in range(len(write_ops)):
            for j in range(i + 1, len(write_ops)):
                op_i, op_j = write_ops[i], write_ops[j]
                if op_i.trace_id in iq_vars and op_j.trace_id in iq_vars:
                    same_port = iq_vars[op_i.trace_id] == iq_vars[op_j.trace_id]
                    SynthesisAxioms._add_constraint(
                        solver,
                        z3.Implies(same_port, t_vars[op_i.trace_id] != t_vars[op_j.trace_id]),
                        f"wport_tdm_{op_i.trace_id}_{op_j.trace_id}"
                    )

    @staticmethod
    def add_bank_conflict_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef]):
        """
        Bank 冲突公理：同 Bank 同类型操作不同拍。
        """
        bank_ops = [o for o in ops if o.metadata.get("manual_bank") is not None]
        bank_groups = {}
        for op in bank_ops:
            bid = op.metadata["manual_bank"]
            bank_groups.setdefault(bid, []).append(op.trace_id)
        
        for bid, op_ids in bank_groups.items():
            if len(op_ids) > 1:
                for i in range(len(op_ids)):
                    for j in range(i + 1, len(op_ids)):
                        id_i, id_j = op_ids[i], op_ids[j]
                        SynthesisAxioms._add_constraint(solver, t_vars[id_i] != t_vars[id_j], f"bank_gap_{bid}_{id_i}_{id_j}")

    @staticmethod
    def add_spill_reload_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef]):
        """
        Spill 重载互斥。
        """
        spill_groups = {}
        for op in ops:
            if op.op_type.upper() == "UR_READ":
                deps = op.metadata.get("depends_on", [])
                for d in deps:
                    if isinstance(d, str) and d.startswith("spill_"):
                        spill_groups.setdefault(d, []).append(op)
        
        for spill_id, reloads in spill_groups.items():
            if len(reloads) <= 1: continue
            for i in range(len(reloads)):
                for j in range(i + 1, len(reloads)):
                    op_i, op_j = reloads[i], reloads[j]
                    SynthesisAxioms._add_constraint(
                        solver,
                        t_vars[op_i.trace_id] != t_vars[op_j.trace_id],
                        f"spill_re_{spill_id}_{op_i.trace_id}_{op_j.trace_id}"
                    )

    @staticmethod
    def add_objective(solver: z3.Optimize, t_vars: Dict[str, z3.ArithRef]):
        max_t = z3.Int("max_t")
        for t in t_vars.values(): solver.add(max_t >= t)
        solver.minimize(max_t)
