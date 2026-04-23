from typing import List, Dict, Any, Union, Optional
import z3
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec


class SynthesisAxioms:
    """
    AIACC SMT 公理库 — 统一模调度版。

    所有互斥类公理接受可选 s_vars 参数：
      - s_vars 存在（模调度）→ 互斥比较变量使用模槽 s（同一 II 窗口内）
      - s_vars 为 None（不应出现，兼容旧调用）→ 回退到绝对时间 t

    这保证了：同端口/同单元的两个算子，即使绝对时间不同，
    只要它们落在同一个 II 槽位上，就会被判为冲突。
    """

    _label_idx = 0

    @staticmethod
    def _add_constraint(solver, constraint: Any, label_name: str):
        SynthesisAxioms._label_idx += 1
        unique_label = f"{label_name}_{SynthesisAxioms._label_idx}"
        if isinstance(solver, z3.Solver):
            solver.assert_and_track(constraint, z3.Bool(unique_label))
        else:
            solver.add(constraint)

    # ------------------------------------------------------------------
    # 时序因果链（RAW 延迟公理）
    # ------------------------------------------------------------------
    @staticmethod
    def add_latency_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        spec: HardwareSpec,
        mode: str = "stable",
    ):
        """RAW 依赖公理（绝对时间）。"""
        for op in ops:
            for dep_id in op.metadata.get("depends_on", []):
                producer = next((o for o in ops if o.trace_id == dep_id), None)
                if not producer:
                    continue
                lat = spec.delays.get(producer.op_type, 1)
                label = f"lat_{op.trace_id}_{dep_id}_{mode}"
                SynthesisAxioms._add_constraint(
                    solver, t_vars[op.trace_id] >= t_vars[dep_id] + lat, label
                )

    # ------------------------------------------------------------------
    # 槽位存活公理（RTOVR Crossbar）
    # ------------------------------------------------------------------
    @staticmethod
    def add_rtovr_crossbar_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        spec: HardwareSpec,
    ):
        """槽位存活公理：在 [Pulse, Fire] 区间内，物理槽位不可被冲刷。"""
        rt_ops = [o for o in ops if o.op_type == "RTOVR"]
        slot_groups: Dict[Any, list] = {}
        for op in rt_ops:
            slot = op.metadata.get("slot_id")
            if slot is not None:
                slot_groups.setdefault(slot, []).append(op)

        for slot, slot_ops in slot_groups.items():
            if len(slot_ops) <= 1:
                continue
            for i, op_i in enumerate(slot_ops):
                consumers = [
                    o for o in ops if op_i.trace_id in o.metadata.get("depends_on", [])
                ]
                for cons in consumers:
                    t_pulse_i = t_vars[op_i.trace_id]
                    t_fire_i = t_vars[cons.trace_id]
                    for j, op_j in enumerate(slot_ops):
                        if i == j:
                            continue
                        t_pulse_j = t_vars[op_j.trace_id]
                        uid = "_".join(sorted([op_i.trace_id, op_j.trace_id]))
                        SynthesisAxioms._add_constraint(
                            solver,
                            z3.Or(t_pulse_j < t_pulse_i, t_pulse_j > t_fire_i),
                            f"slot_ival_{slot}_{uid}_{cons.trace_id}",
                        )

    # ------------------------------------------------------------------
    # 读端口时分复用
    # ------------------------------------------------------------------
    @staticmethod
    def add_read_port_mutex_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        iq_vars: Dict[str, z3.ArithRef],
        s_vars: Optional[Dict[str, z3.ArithRef]] = None,
    ):
        """
        同物理读端口的不同读操作必须落在不同的模槽（s_vars 模式）。
        无 s_vars 时退化为绝对时间互斥（兼容）。
        """
        read_ops = [o for o in ops if o.op_type.upper() == "UR_READ"]
        for i in range(len(read_ops)):
            for j in range(i + 1, len(read_ops)):
                op_i, op_j = read_ops[i], read_ops[j]
                if op_i.trace_id in iq_vars and op_j.trace_id in iq_vars:
                    same_port = iq_vars[op_i.trace_id] == iq_vars[op_j.trace_id]
                    var_i = s_vars[op_i.trace_id] if s_vars else t_vars[op_i.trace_id]
                    var_j = s_vars[op_j.trace_id] if s_vars else t_vars[op_j.trace_id]
                    SynthesisAxioms._add_constraint(
                        solver,
                        z3.Implies(same_port, var_i != var_j),
                        f"rport_tdm_{op_i.trace_id}_{op_j.trace_id}",
                    )

    # ------------------------------------------------------------------
    # IQ 锁存器保护
    # ------------------------------------------------------------------
    @staticmethod
    def add_iq_latch_protect_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        iq_vars: Dict[str, z3.ArithRef],
    ):
        """IQ 锁存器保护：在搬运完成前，同 IQ 不能被新读操作覆盖（绝对时间）。"""
        read_ops = [o for o in ops if o.op_type.upper() == "UR_READ"]
        for i in range(len(read_ops)):
            for j in range(i + 1, len(read_ops)):
                op_A, op_B = read_ops[i], read_ops[j]
                if op_A.trace_id not in iq_vars or op_B.trace_id not in iq_vars:
                    continue

                same_iq = iq_vars[op_A.trace_id] == iq_vars[op_B.trace_id]

                def get_protection_window(op_x):
                    consumers = [
                        o for o in ops
                        if op_x.trace_id in o.metadata.get("depends_on", [])
                    ]
                    if not consumers:
                        return t_vars[op_x.trace_id]
                    m_val = t_vars[consumers[0].trace_id]
                    for cons in consumers[1:]:
                        m_val = z3.If(
                            t_vars[cons.trace_id] > m_val,
                            t_vars[cons.trace_id],
                            m_val,
                        )
                    return m_val

                t_end_A = get_protection_window(op_A)
                t_end_B = get_protection_window(op_B)

                cond_B_after_A = t_vars[op_B.trace_id] > t_end_A
                cond_A_after_B = t_vars[op_A.trace_id] > t_end_B

                SynthesisAxioms._add_constraint(
                    solver,
                    z3.Implies(same_iq, z3.Or(cond_B_after_A, cond_A_after_B)),
                    f"iq_l_prot_{op_A.trace_id}_{op_B.trace_id}",
                )

    # ------------------------------------------------------------------
    # ALU 流水线公理
    # ------------------------------------------------------------------
    @staticmethod
    def add_alu_pipeline_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        iq_vars: Dict[str, z3.ArithRef],
        spec: HardwareSpec,
        s_vars: Optional[Dict[str, z3.ArithRef]] = None,
    ):
        """
        ALU 流水线公理：
          1. 发射排他：同单元同槽位不可同时发射（使用 s_vars）
          2. Liveness 保护：前驱结果必须在后继覆盖前被搬走（绝对时间）
        """
        alu_types = [
            "FPMUL", "FPADD", "FPTOINT", "INTTOFP",
            "LOGIC", "SHIFTUP", "SHIFTDN", "INTADD",
        ]
        for alu_type in alu_types:
            alu_ops = [o for o in ops if o.op_type == alu_type]
            if len(alu_ops) <= 1:
                continue
            lat = spec.delays.get(alu_type, 4)

            for i in range(len(alu_ops)):
                for j in range(i + 1, len(alu_ops)):
                    op_i, op_j = alu_ops[i], alu_ops[j]
                    if op_i.trace_id not in iq_vars or op_j.trace_id not in iq_vars:
                        continue

                    same_unit = iq_vars[op_i.trace_id] == iq_vars[op_j.trace_id]

                    # 1. 发射排他（模槽）
                    var_i = s_vars[op_i.trace_id] if s_vars else t_vars[op_i.trace_id]
                    var_j = s_vars[op_j.trace_id] if s_vars else t_vars[op_j.trace_id]
                    SynthesisAxioms._add_constraint(
                        solver,
                        z3.Implies(same_unit, var_i != var_j),
                        f"alu_issue_{alu_type}_{op_i.trace_id}_{op_j.trace_id}",
                    )

                    # 2. Liveness 保护（绝对时间）
                    cons_i = [
                        o for o in ops
                        if op_i.trace_id in o.metadata.get("depends_on", [])
                        and o.op_type == "RTOVR"
                    ]
                    cons_j = [
                        o for o in ops
                        if op_j.trace_id in o.metadata.get("depends_on", [])
                        and o.op_type == "RTOVR"
                    ]

                    if cons_i:
                        i_before_j = t_vars[op_i.trace_id] < t_vars[op_j.trace_id]
                        i_move_done = z3.And(
                            [t_vars[c.trace_id] < t_vars[op_j.trace_id] + lat for c in cons_i]
                        )
                        SynthesisAxioms._add_constraint(
                            solver,
                            z3.Implies(z3.And(same_unit, i_before_j), i_move_done),
                            f"alu_l_i_{op_i.trace_id}_j_{op_j.trace_id}",
                        )

                    if cons_j:
                        j_before_i = t_vars[op_j.trace_id] < t_vars[op_i.trace_id]
                        j_move_done = z3.And(
                            [t_vars[c.trace_id] < t_vars[op_i.trace_id] + lat for c in cons_j]
                        )
                        SynthesisAxioms._add_constraint(
                            solver,
                            z3.Implies(z3.And(same_unit, j_before_i), j_move_done),
                            f"alu_l_j_{op_j.trace_id}_i_{op_i.trace_id}",
                        )

    # ------------------------------------------------------------------
    # 写端口时分复用
    # ------------------------------------------------------------------
    @staticmethod
    def add_write_port_mutex_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        iq_vars: Dict[str, z3.ArithRef],
        s_vars: Optional[Dict[str, z3.ArithRef]] = None,
    ):
        """写端口时分复用：同物理写端口不同写操作必须落在不同模槽。"""
        write_ops = [o for o in ops if o.op_type.upper() == "UR_WRITE"]
        for i in range(len(write_ops)):
            for j in range(i + 1, len(write_ops)):
                op_i, op_j = write_ops[i], write_ops[j]
                if op_i.trace_id in iq_vars and op_j.trace_id in iq_vars:
                    same_port = iq_vars[op_i.trace_id] == iq_vars[op_j.trace_id]
                    var_i = s_vars[op_i.trace_id] if s_vars else t_vars[op_i.trace_id]
                    var_j = s_vars[op_j.trace_id] if s_vars else t_vars[op_j.trace_id]
                    SynthesisAxioms._add_constraint(
                        solver,
                        z3.Implies(same_port, var_i != var_j),
                        f"wport_tdm_{op_i.trace_id}_{op_j.trace_id}",
                    )

    # ------------------------------------------------------------------
    # Bank 冲突公理
    # ------------------------------------------------------------------
    @staticmethod
    def add_bank_conflict_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        s_vars: Optional[Dict[str, z3.ArithRef]] = None,
    ):
        """同 Bank 同类型操作必须落在不同模槽。"""
        bank_ops = [o for o in ops if o.metadata.get("manual_bank") is not None]
        bank_groups: Dict[Any, list] = {}
        for op in bank_ops:
            bid = op.metadata["manual_bank"]
            bank_groups.setdefault(bid, []).append(op.trace_id)

        for bid, op_ids in bank_groups.items():
            if len(op_ids) > 1:
                for i in range(len(op_ids)):
                    for j in range(i + 1, len(op_ids)):
                        id_i, id_j = op_ids[i], op_ids[j]
                        var_i = s_vars[id_i] if s_vars else t_vars[id_i]
                        var_j = s_vars[id_j] if s_vars else t_vars[id_j]
                        SynthesisAxioms._add_constraint(
                            solver, var_i != var_j, f"bank_gap_{bid}_{id_i}_{id_j}"
                        )

    # ------------------------------------------------------------------
    # Spill/Reload 互斥
    # ------------------------------------------------------------------
    @staticmethod
    def add_spill_reload_mutex_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        s_vars: Optional[Dict[str, z3.ArithRef]] = None,
    ):
        """同 Spill 对应的多个 Reload 操作必须落在不同模槽。"""
        spill_groups: Dict[str, list] = {}
        for op in ops:
            if op.op_type.upper() == "UR_READ":
                for d in op.metadata.get("depends_on", []):
                    if isinstance(d, str) and d.startswith("spill_"):
                        spill_groups.setdefault(d, []).append(op)

        for spill_id, reloads in spill_groups.items():
            if len(reloads) <= 1:
                continue
            for i in range(len(reloads)):
                for j in range(i + 1, len(reloads)):
                    op_i, op_j = reloads[i], reloads[j]
                    var_i = s_vars[op_i.trace_id] if s_vars else t_vars[op_i.trace_id]
                    var_j = s_vars[op_j.trace_id] if s_vars else t_vars[op_j.trace_id]
                    SynthesisAxioms._add_constraint(
                        solver,
                        var_i != var_j,
                        f"spill_re_{spill_id}_{op_i.trace_id}_{op_j.trace_id}",
                    )

    # ------------------------------------------------------------------
    # 防覆盖公理（模调度专用）
    # ------------------------------------------------------------------
    @staticmethod
    def add_anti_overwrite_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        II: int,
        spec: HardwareSpec,
        no_output_fifo: bool = False,
    ):
        """
        生命周期防覆盖公理。

        约束：数据从物理锁存器产生到最后一个消费者读取的存活时间 ≤ II。
          max(t[consumer]) - (t[producer] + lat - 1) ≤ II

        物理含义：
          - no_output_fifo=True：硬件输出锁存器每 II 拍被下一迭代覆盖，
            无流水线寄存器缓冲。此时需强制在 II 窗口内完成消费。
          - no_output_fifo=False（默认）：标准流水线，输出寄存器持有值
            直到被消费，无覆盖风险，无需此约束。

        对于标准带 pipeline-register 的 FPU 架构，应保持 no_output_fifo=False，
        此时 II_min 由纯资源约束决定（= ceil(usage/instances)）。
        """
        if not no_output_fifo:
            return  # 标准流水线架构无需此约束

        for op in ops:
            consumers = [
                o for o in ops if op.trace_id in o.metadata.get("depends_on", [])
            ]
            if not consumers:
                continue

            m_val = t_vars[consumers[0].trace_id]
            for cons in consumers[1:]:
                m_val = z3.If(
                    t_vars[cons.trace_id] > m_val, t_vars[cons.trace_id], m_val
                )

            lat = spec.delays.get(op.op_type.upper(), 1)
            data_ready = t_vars[op.trace_id] + lat - 1

            SynthesisAxioms._add_constraint(
                solver,
                m_val - data_ready <= II,
                f"anti_overwrite_{op.trace_id}",
            )

    # ------------------------------------------------------------------
    # 优化目标（仅供外部工具使用，内部不再调用）
    # ------------------------------------------------------------------
    @staticmethod
    def add_objective(solver: z3.Optimize, t_vars: Dict[str, z3.ArithRef]):
        """最小化总周期数（供 z3.Optimize 使用）。"""
        if isinstance(solver, z3.Optimize):
            max_t = z3.Int("max_t")
            for t in t_vars.values():
                solver.add(max_t >= t)
            solver.minimize(max_t)
