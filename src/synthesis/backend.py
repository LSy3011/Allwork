from typing import List, Dict
import z3
from ir.mir.mir_op import MIROp

class SMTSynthesizer:
    """
    AIACC SMT 合成引擎。
    负责在满足所有物理约束（Latency, IQ, Bank, Slot）的前提下，寻找最优发射时序。
    """
    def __init__(self, hw_spec):
        self.hw_spec = hw_spec
        # 预先处理归一化时延映射
        self.norm_delays = {k.upper(): v for k, v in hw_spec.delays.items()}

    def solve(self, ops: List[MIROp], strategy: str = "balanced") -> Dict[str, int]:
        solver = z3.Optimize()
        
        # 0. 辅助索引 
        nodes_by_id = {op.node_id: op for op in ops}
        
        # 1. 声明变量
        t = {op.node_id: z3.Int(f"t_{op.node_id}") for op in ops}
        for op_id in t:
            solver.add(t[op_id] >= 0)

        # 2. 数据依赖约束 (RAW)
        for op in ops:
            for dep_id in op.metadata.get("depends_on", []):
                if dep_id in t:
                    dep_node = nodes_by_id[dep_id]
                    # 获取指令延迟，默认为 1
                    delay = self.norm_delays.get(dep_node.op_type.upper(), 1)
                    solver.add(t[op.node_id] >= t[dep_id] + delay)

        # 3. 物理资源互斥约束 (IQ & Bank & Slot)
        for i in range(len(ops)):
            for j in range(i + 1, len(ops)):
                op_i, op_j = ops[i], ops[j]
                
                # A. IQ 发射互斥
                if ("iq_id" in op_i.metadata and "iq_id" in op_j.metadata and 
                    op_i.metadata["iq_id"] == op_j.metadata["iq_id"]):
                    solver.add(t[op_i.node_id] != t[op_j.node_id])
                
                # B. Bank 访问互斥
                if ("bank_id" in op_i.metadata and "bank_id" in op_j.metadata and 
                    op_i.metadata["bank_id"] == op_j.metadata["bank_id"]):
                    solver.add(t[op_i.node_id] != t[op_j.node_id])
                
                # C. 物理 Slot 占用互斥 (同拍改写同一槽位)
                if ("slot_id" in op_i.metadata and "slot_id" in op_j.metadata and 
                    op_i.metadata["slot_id"] == op_j.metadata["slot_id"]):
                    solver.add(t[op_i.node_id] != t[op_j.node_id])

        # 4. 目标函数: 最小化算子 Latency
        max_t = z3.Int("max_t")
        for op_id in t:
            solver.add(max_t >= t[op_id])
        solver.minimize(max_t)

        if solver.check() == z3.sat:
            model = solver.model()
            return {op_id: model[t[op_id]].as_long() for op_id in t}
        
        return None
