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
                         t_vars: Dict[str, z3.ArithRef], spec: HardwareSpec):
        """
        全刚性脉冲传递模型：
        1. 数据的每个生命周期阶段 (Read -> Route -> Comp -> Route -> Write) 锁死为 Latency 步长。
        2. 唯一的自由度是整根骨牌链在时间轴上的平移。
        """
        for op in ops:
            deps = op.metadata.get("depends_on", [])
            for dep_id in deps:
                producer = next((o for o in ops if o.node_id == dep_id), None)
                if not producer: continue
                lat = spec.delays.get(producer.op_type, 1)
                
                label = f"rigid_lat_{op.node_id}_{dep_id}"
                
                # 只有 RAW 屏障 (计算任务等待之前的写任务完成) 允许弹性延时 >=
                # 这是因为数据已经在 Bank 里落盘了，晚点读没事。
                if op.op_type == "UR_READ" and producer.op_type == "UR_WRITE":
                    SynthesisAxioms._add_constraint(solver, t_vars[op.node_id] >= t_vars[dep_id] + lat, f"barrier_{label}")
                else:
                    # 所有的脉冲链条 (进入总线或进入计算单元) 必须刚性等式对齐
                    SynthesisAxioms._add_constraint(solver, t_vars[op.node_id] == t_vars[dep_id] + lat, label)

    @staticmethod
    def add_rtovr_crossbar_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef], spec: HardwareSpec):
        slot_groups = {}
        for op in ops:
            if op.op_type == "RTOVR":
                slot = op.metadata.get("slot_id")
                if slot is not None: slot_groups.setdefault(slot, []).append(op.node_id)
        for slot, op_ids in slot_groups.items():
            if len(op_ids) > 1:
                SynthesisAxioms._add_constraint(solver, z3.Distinct([t_vars[oid] for oid in op_ids]), f"slot_mutex_{slot}")

    @staticmethod
    def add_iq_mutex_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef]):
        iq_groups = {}
        for op in ops:
            if op.op_type in ["FPMUL", "FPADD", "UR_READ", "UR_WRITE"]:
                iq = op.metadata.get("iq_id")
                if iq is not None: iq_groups.setdefault(iq, []).append(op.node_id)
        for iq, op_ids in iq_groups.items():
            if len(op_ids) > 1:
                SynthesisAxioms._add_constraint(solver, z3.Distinct([t_vars[oid] for oid in op_ids]), f"iq_mutex_{iq}")

    @staticmethod
    def add_bank_conflict_axiom(solver, ops: List[MIROp], t_vars: Dict[str, z3.ArithRef]):
        bank_reads, bank_writes = {}, {}
        for op in ops:
            for b in [0,1,2,3]: # 模拟 Bank RW
                bank_reads.setdefault(b, []).append(op.node_id)
        # [暂略简化以集中解决冲刷冲突...]
        pass

    @staticmethod
    def add_objective(solver: z3.Optimize, t_vars: Dict[str, z3.ArithRef]):
        max_t = z3.Int("max_t")
        for t in t_vars.values(): solver.add(max_t >= t)
        solver.minimize(max_t)
