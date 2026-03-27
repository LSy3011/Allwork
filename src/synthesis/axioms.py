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
        时序因果链条：
        - Stable: 核心计算链刚性，但允许加载指令弹性偏移，防止 IQ 拥堵死锁。
        - Extreme: 全链路弹性，依赖区间锁保证安全。
        """
        for op in ops:
            for dep_id in op.metadata.get("depends_on", []):
                producer = next((o for o in ops if o.node_id == dep_id), None)
                if not producer: continue
                lat = spec.delays.get(producer.op_type, 1)
                
                label = f"lat_{op.node_id}_{dep_id}_{mode}"
                if mode == "stable":
                    # 允许 UR_READ 滑动，其余锁死
                    if producer.op_type == "UR_READ":
                        SynthesisAxioms._add_constraint(solver, t_vars[op.node_id] >= t_vars[dep_id] + lat, f"flex_{label}")
                    else:
                        SynthesisAxioms._add_constraint(solver, t_vars[op.node_id] == t_vars[dep_id] + lat, label)
                else:
                    SynthesisAxioms._add_constraint(solver, t_vars[op.node_id] >= t_vars[dep_id] + lat, label)

    @staticmethod
    def add_rtovr_crossbar_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], spec: HardwareSpec, mode="stable"):
        rt_ops = [o for o in ops if o.op_type == "RTOVR"]
        slot_groups = {}
        for op in rt_ops:
            slot = op.metadata.get("slot_id")
            if slot is not None: slot_groups.setdefault(slot, []).append(op)

        for slot, slot_ops in slot_groups.items():
            if mode == "stable":
                # Stable 模式只需保证单拍 Slot ID 不冲突 (因为链路是刚性的，数据不留存)
                if len(slot_ops) > 1:
                    SynthesisAxioms._add_constraint(solver, z3.Distinct([t_vars[o.node_id] for o in slot_ops]), f"slot_dist_{slot}")
            else:
                # Extreme 模式必须检查全周期区间锁
                for i, op_i in enumerate(slot_ops):
                    consumers = [o for o in ops if op_i.node_id in o.metadata.get("depends_on", [])]
                    for cons in consumers:
                        # 物理特性：一旦计算单元启动（t_vars[cons.node_id]），数据即被 Latch 进流水线。
                        # Slot 在下一拍即可释放给其他算子。
                        t_start = t_vars[op_i.node_id]
                        t_end = t_vars[cons.node_id]
                        for j, op_j in enumerate(slot_ops):
                            if i == j: continue
                            uid = "_".join(sorted([op_i.node_id, op_j.node_id]))
                            SynthesisAxioms._add_constraint(solver, z3.Or(t_vars[op_j.node_id] < t_start, t_vars[op_j.node_id] > t_end), f"slot_ival_{slot}_{uid}")

    @staticmethod
    def add_iq_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], mode="stable"):
        if mode == "stable":
            iq_groups = {}
            for op in ops:
                iq = op.metadata.get("iq_id")
                if iq is not None: iq_groups.setdefault(iq, []).append(op.node_id)
            for iq, op_ids in iq_groups.items():
                if len(op_ids) > 1:
                    SynthesisAxioms._add_constraint(solver, z3.Distinct([t_vars[oid] for oid in op_ids]), f"iq_mutex_{iq}")
        else:
            # Extreme 模式使用 IQ 数据寄存器锁定
            load_ops = [o for o in ops if o.op_type == "UR_READ"]
            iq_groups = {}
            for op in load_ops:
                iq = op.metadata.get("iq_id")
                if iq is not None: iq_groups.setdefault(iq, []).append(op)
            for iq, iq_ops in iq_groups.items():
                for i, op_i in enumerate(iq_ops):
                    consumers = [o for o in ops if op_i.node_id in o.metadata.get("depends_on", [])]
                    for cons in consumers:
                        t_start = t_vars[op_i.node_id]
                        t_end = t_vars[cons.node_id] - 1
                        for j, op_j in enumerate(iq_ops):
                            if i == j: continue
                            uid = "_".join(sorted([op_i.node_id, op_j.node_id]))
                            SynthesisAxioms._add_constraint(solver, z3.Or(t_vars[op_j.node_id] < t_start, t_vars[op_j.node_id] > t_end), f"iq_ival_{iq}_{uid}")

    @staticmethod
    def add_bank_conflict_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef]):
        pass

    @staticmethod
    def add_objective(solver: z3.Optimize, t_vars: Dict[str, z3.ArithRef]):
        max_t = z3.Int("max_t")
        for t in t_vars.values(): solver.add(max_t >= t)
        solver.minimize(max_t)
