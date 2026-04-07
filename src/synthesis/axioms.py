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
                        # 约束：j 的搬运必须在 i 的区间之外
                        # 要么 j 先搬，要么 j 在 i 开火后（严格大于）搬
                        uid = "_".join(sorted([op_i.trace_id, op_j.trace_id]))
                        SynthesisAxioms._add_constraint(solver, z3.Or(t_pulse_j < t_pulse_i, t_pulse_j > t_fire_i), f"slot_ival_{slot}_{uid}")

    @staticmethod
    def add_read_port_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef]):
        """
        读端口时分复用公理：对于共享同一物理读端口的 UR_READ 操作，
        只需保证不在同一时刻 (T) 发出读请求。
        """
        read_ops = [o for o in ops if o.op_type.upper() == "UR_READ"]
        for i in range(len(read_ops)):
            for j in range(i + 1, len(read_ops)):
                op_i, op_j = read_ops[i], read_ops[j]
                if op_i.trace_id in iq_vars and op_j.trace_id in iq_vars:
                    same_port = iq_vars[op_i.trace_id] == iq_vars[op_j.trace_id]
                    # 不同拍 (T_i ≠ T_j)
                    diff_time = t_vars[op_i.trace_id] != t_vars[op_j.trace_id]
                    SynthesisAxioms._add_constraint(
                        solver,
                        z3.Implies(same_port, diff_time),
                        f"rport_tdm_{op_i.trace_id}_{op_j.trace_id}"
                    )

    @staticmethod
    def add_iq_latch_protect_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef]):
        """
        [ADPC 物理对齐] IQ 锁存器保护公理：当 UR_READ A 的数据正在被搬运 (RTOVR) 时，
        同一物理端口对应的 IQ 在此窗口内不可被新的读操作 B 冲刷。
        保护窗口：[T_read, T_last_move]
        """
        read_ops = [o for o in ops if o.op_type.upper() == "UR_READ"]
        for i in range(len(read_ops)):
            for j in range(i + 1, len(read_ops)):
                op_A, op_B = read_ops[i], read_ops[j]
                if op_A.trace_id not in iq_vars or op_B.trace_id not in iq_vars:
                    continue
                
                same_iq = iq_vars[op_A.trace_id] == iq_vars[op_B.trace_id]
                
                def get_protection_window(op_x):
                    # 找到该读操作的所有直接消费者 (搬运节点 RTOVR)
                    consumers = [o for o in ops 
                               if op_x.trace_id in o.metadata.get("depends_on", [])
                               and o.op_type.upper() == "RTOVR"]
                    if not consumers:
                        return t_vars[op_x.trace_id]
                    # 最后一个搬运操作的时间
                    if len(consumers) == 1:
                        return t_vars[consumers[0].trace_id]
                    # 为 z3 构建 max 表达式
                    # 注意：z3.max 在 4.16+ 表现稳定，早期版本建议用 If 嵌套
                    m_val = t_vars[consumers[0].trace_id]
                    for cons in consumers[1:]:
                        m_val = z3.If(t_vars[cons.trace_id] > m_val, t_vars[cons.trace_id], m_val)
                    return m_val

                t_last_move_A = get_protection_window(op_A)
                t_last_move_B = get_protection_window(op_B)
                
                # 若 A 先读，则 B 必须在 A 的最后一个搬运之后发生 (读端口复用动作)
                cond_B_after_A = t_vars[op_B.trace_id] > t_last_move_A
                # 若 B 先读，则 A 必须在 B 的最后一个搬运之后发生
                cond_A_after_B = t_vars[op_A.trace_id] > t_last_move_B
                
                SynthesisAxioms._add_constraint(
                    solver,
                    z3.Implies(same_iq, z3.Or(cond_B_after_A, cond_A_after_B)),
                    f"iq_latch_{op_A.trace_id}_{op_B.trace_id}"
                )

    @staticmethod
    def add_alu_pipeline_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], spec: HardwareSpec):
        """
        [ADPC 物理对齐] ALU 流水线公理：ALU 可以在每拍发射指令 (Pipeline Issue)。
        但在新结果写回本地 D 寄存器前，前一次计算的结果必须在此之前被 RTOVR 搬走。
        保护窗口：[T_fire + latency, T_move]
        """
        for alu_type in ["FPMUL", "FPADD"]:
            alu_ops = [o for o in ops if o.op_type == alu_type]
            if len(alu_ops) <= 1: continue
            lat = spec.delays.get(alu_type, 4)
            
            for i in range(len(alu_ops)):
                for j in range(i + 1, len(alu_ops)):
                    op_i, op_j = alu_ops[i], alu_ops[j]
                    
                    # 1. 发射排他：同拍只能发射一个
                    SynthesisAxioms._add_constraint(
                        solver,
                        t_vars[op_i.trace_id] != t_vars[op_j.trace_id],
                        f"alu_issue_{alu_type}_{op_i.trace_id}_{op_j.trace_id}"
                    )
                    
                    # 2. 结果寄存器 (Liveness) 保护
                    # 查找消费此 ALU 结果的所有 RTOVR
                    consumers_i = [o for o in ops if op_i.trace_id in o.metadata.get("depends_on", []) and o.op_type == "RTOVR"]
                    consumers_j = [o for o in ops if op_j.trace_id in o.metadata.get("depends_on", []) and o.op_type == "RTOVR"]
                    
                    # 如果 i 先计算 (T_i < T_j)，则 i 的结果被搬走的时间 (T_move_i) 
                    # 必须在 j 的结果写回的时间 (T_j + lat) 之前。
                    if consumers_i:
                        i_before_j = t_vars[op_i.trace_id] < t_vars[op_j.trace_id]
                        i_moved_before_j_done = z3.And([
                            t_vars[c.trace_id] < t_vars[op_j.trace_id] + lat
                            for c in consumers_i
                        ])
                        SynthesisAxioms._add_constraint(solver, z3.Implies(i_before_j, i_moved_before_j_done), f"alu_l_prot_i_{op_i.trace_id}_j_{op_j.trace_id}")
                    
                    if consumers_j:
                        j_before_i = t_vars[op_j.trace_id] < t_vars[op_i.trace_id]
                        j_moved_before_i_done = z3.And([
                            t_vars[c.trace_id] < t_vars[op_i.trace_id] + lat
                            for c in consumers_j
                        ])
                        SynthesisAxioms._add_constraint(solver, z3.Implies(j_before_i, j_moved_before_i_done), f"alu_l_prot_j_{op_j.trace_id}_i_{op_i.trace_id}")

    @staticmethod
    def add_write_port_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef]):
        """
        写端口时分复用公理：同物理写端口的操作只需在不同拍写即可。
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
    def _legacy_add_iq_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef]):
        """
        指令队列排他公理：IQ 作为一个物理寄存器，其结果在被 RTOVR 搬走前不可被冲刷（针对 UR_READ）。
        """
        load_ops = [o for o in ops if o.op_type == "UR_READ"]
        iq_groups = {}
        for op in load_ops:
            iq = op.metadata.get("iq_id")
            if iq is not None: iq_groups.setdefault(iq, []).append(op)
            
        for iq, iq_ops in iq_groups.items():
            if len(iq_ops) <= 1: continue
            for i, op_i in enumerate(iq_ops):
                consumers = [o for o in ops if op_i.trace_id in o.metadata.get("depends_on", [])]
                for cons in consumers:
                    t_read_i = t_vars[op_i.trace_id]
                    t_move_i = t_vars[cons.trace_id]
                    
                    for j, op_j in enumerate(iq_ops):
                        if i == j: continue
                        t_read_j = t_vars[op_j.trace_id]
                        uid = "_".join(sorted([op_i.trace_id, op_j.trace_id]))
                        SynthesisAxioms._add_constraint(solver, z3.Or(t_read_j < t_read_i, t_read_j >= t_move_i), f"iq_ival_{iq}_{uid}")

    @staticmethod
    def _legacy_add_liveness_flush_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef]):
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
    def _legacy_add_alu_liveness_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], iq_vars: Dict[str, z3.ArithRef]):
        """
        [工业级架构对齐] ALU 防复写公理：ALU 的执行结果必须等待所有下游消费者(RTOVR)读取完毕后，才能执行下一次计算。
        """
        alu_ops = [o for o in ops if o.op_type in ["FPMUL", "FPADD"]]
        for i in range(len(alu_ops)):
            for j in range(i + 1, len(alu_ops)):
                op_A, op_B = alu_ops[i], alu_ops[j]
                if op_A.op_type != op_B.op_type: continue

                consumers_A = [o for o in ops if op_A.trace_id in o.metadata.get("depends_on", [])]
                consumers_B = [o for o in ops if op_B.trace_id in o.metadata.get("depends_on", [])]
                
                cond_B_after_A = z3.And([t_vars[op_B.trace_id] > t_vars[c.trace_id] for c in consumers_A]) if consumers_A else t_vars[op_B.trace_id] > t_vars[op_A.trace_id]
                cond_A_after_B = z3.And([t_vars[op_A.trace_id] > t_vars[c.trace_id] for c in consumers_B]) if consumers_B else t_vars[op_A.trace_id] > t_vars[op_B.trace_id]
                
                solver.add(z3.Implies(iq_vars[op_A.trace_id] == iq_vars[op_B.trace_id], z3.Or(cond_B_after_A, cond_A_after_B)))

    @staticmethod
    def add_bank_conflict_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef]):
        """
        Bank 冲突公理 (V2.0 硬件对齐版)：
        同 Bank 同类型操作不可在同一拍执行。
        每 Bank 每拍最多 1 读 + 1 写 (读写端口物理独立)。
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
                        # 同 Bank 不同拍 (仅禁止同拍, 无额外间隔)
                        SynthesisAxioms._add_constraint(solver, t_vars[id_i] != t_vars[id_j], f"bank_gap_{bid}_{id_i}_{id_j}")

    @staticmethod
    def add_spill_reload_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef]):
        """
        Spill 重载互斥公理：依赖同一 spill 的多个 reload 读操作
        必然分配到同一 Bank/Addr，因此必须不同拍 (Bank 读端口互斥)。
        同理，spill 的写操作 (UR_WRITE) 与其 reload 读操作也需不同拍。
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
                        f"spill_reload_{spill_id}_{op_i.trace_id}_{op_j.trace_id}"
                    )

    @staticmethod
    def add_objective(solver: z3.Optimize, t_vars: Dict[str, z3.ArithRef]):
        max_t = z3.Int("max_t")
        for t in t_vars.values(): solver.add(max_t >= t)
        solver.minimize(max_t)
