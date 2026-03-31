from typing import List, Dict
import z3
from ir.mir.mir_op import MIROp
from synthesis.axioms import SynthesisAxioms

class SMTSynthesizer:
    """
    AIACC SMT 合成引擎 (六大公理 · 模块化版)。
    - 通过 SynthesisAxioms 注入所有物理约束，不内联任何公理逻辑。
    - 增量修正：Latency 归一化 (L-1 + transit) 与 Sibling 同步。
    """
    def __init__(self, hw_spec):
        self.hw_spec = hw_spec
        # 预先处理归一化时延映射
        self.norm_delays = {k.upper(): v for k, v in hw_spec.delays.items()}

    def solve(self, ops: List[MIROp], strategy: str = "balanced", **kwargs) -> Dict[str, int]:
        solver = z3.Optimize()
        
        # 0. 辅助索引 
        nodes_by_id = {op.node_id: op for op in ops}
        
        # 1. 声明变量
        t = {op.node_id: z3.Int(f"t_{op.node_id}") for op in ops}
        for op_id in t:
            solver.add(t[op_id] >= 0)

        # 2. 数据依赖约束 (RAW) — 128-bit 归一化 Latency
        # Latency 包含点火那一拍，故流水等待为 (L-1)。
        # 数据离开计算单元进入 RtoVR 选通网需要 +1 拍 (transit)。
        for op in ops:
            for dep_id in op.metadata.get("depends_on", []):
                if dep_id in t:
                    dep_node = nodes_by_id[dep_id]
                    delay = self.norm_delays.get(dep_node.op_type.upper(), 1)
                    # transit: 非 RtoVR 的结果需要 1 拍到达总线
                    transit = 1 if dep_node.op_type.upper() != "RTOVR" else 0
                    solver.add(t[op.node_id] >= t[dep_id] + (delay - 1) + transit)

        # 3. 物理资源互斥约束 — 委托给 axioms.py 的模块化公理
        # A. IQ 发射互斥 (原始三维互斥: iq_id + bank_id + slot_id)
        for i in range(len(ops)):
            for j in range(i + 1, len(ops)):
                op_i, op_j = ops[i], ops[j]
                
                # A. IQ 发射互斥
                if ("iq_id" in op_i.metadata and "iq_id" in op_j.metadata and 
                    op_i.metadata["iq_id"] == op_j.metadata["iq_id"]):
                    solver.add(t[op_i.node_id] != t[op_j.node_id])
                
                # B. 物理 Slot 占用互斥 (同拍改写同一槽位)
                if ("slot_id" in op_i.metadata and "slot_id" in op_j.metadata and 
                    op_i.metadata["slot_id"] == op_j.metadata["slot_id"]):
                    solver.add(t[op_i.node_id] != t[op_j.node_id])

        # C. Bank 冲突公理 — 委托 axioms.py
        SynthesisAxioms.add_bank_conflict_axiom(solver, ops, t)

        # 4. 影子绑定 (Sibling Sync)
        # 如果一个计算单元 (FPMUL/FPADD/UR_WRITE) 依赖多个 RtoVR，
        # 这些 RtoVR 必须同拍点火，且 ALU 比它们晚 1 拍。
        for op in ops:
            rtovr_deps = [d for d in op.metadata.get("depends_on", []) 
                         if d in nodes_by_id and nodes_by_id[d].op_type == "RTOVR"]
            
            if rtovr_deps and op.op_type.upper() in ["FPMUL", "FPADD", "UR_WRITE"]:
                # ALU 必须比 RtoVR 晚恰好 1 拍
                for rd in rtovr_deps:
                    solver.add(t[op.node_id] == t[rd] + 1)
                # 兄弟 RtoVR 同拍对齐
                if len(rtovr_deps) >= 2:
                    for k in range(len(rtovr_deps) - 1):
                        solver.add(t[rtovr_deps[k]] == t[rtovr_deps[k+1]])

        # 5. 优化目标 — 委托 axioms.py
        SynthesisAxioms.add_objective(solver, t)

        if solver.check() == z3.sat:
            model = solver.model()
            return {op_id: model[t[op_id]].as_long() for op_id in t}
        
        return None
