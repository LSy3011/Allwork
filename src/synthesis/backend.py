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

        # 1.5. 物理端口变量化 (Dynamic Port Allocation)
        iq_vars = {}
        for op in ops:
            if op.op_type.upper() == "UR_READ":
                iv = z3.Int(f"iq_{op.node_id}")
                iq_vars[op.node_id] = iv
                if op.metadata.get("iq_id") is not None:
                    solver.add(iv == op.metadata["iq_id"])
                else:
                    # 允许求解器自由将其分配在 200~203 之间
                    solver.add(iv >= 200, iv <= 203)
            elif op.op_type.upper() == "UR_WRITE":
                iv = z3.Int(f"iq_{op.node_id}")
                iq_vars[op.node_id] = iv
                # UR_WRITE physical port must match its RtoVR pipeline slot:
                # SLOT_WDATA_0=35 -> ur-w0 (204)
                # SLOT_WDATA_1=36 -> ur-w1 (205)
                # ...
                if op.metadata.get("iq_id") is not None:
                    solver.add(iv == op.metadata["iq_id"])
                else:
                    dep_slot = None
                    for dep_id in op.metadata.get("depends_on", []):
                        dep_op = next((o for o in ops if o.node_id == dep_id), None)
                        if dep_op and dep_op.op_type.upper() == "RTOVR" and "slot_id" in dep_op.metadata:
                            dep_slot = dep_op.metadata["slot_id"]
                            break
                    
                    if dep_slot is not None and 35 <= dep_slot <= 38:
                        solver.add(iv == 204 + (dep_slot - 35))
                    else:
                        solver.add(iv >= 204, iv <= 207)

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
        for i in range(len(ops)):
            for j in range(i + 1, len(ops)):
                op_i, op_j = ops[i], ops[j]
                
                # A. 静态与动态 IQ 发射互斥
                # 如果两个算子最终被分配到同一个物理端口，则它们不能在同一拍点火
                iq_i = iq_vars.get(op_i.node_id, op_i.metadata.get("iq_id"))
                iq_j = iq_vars.get(op_j.node_id, op_j.metadata.get("iq_id"))
                
                if iq_i is not None and iq_j is not None:
                    if isinstance(iq_i, (int, float)) and isinstance(iq_j, (int, float)):
                        if iq_i == iq_j:
                            solver.add(t[op_i.node_id] != t[op_j.node_id])
                    else:
                        # 符号化互斥约束
                        solver.add(z3.Implies(iq_i == iq_j, t[op_i.node_id] != t[op_j.node_id]))
                
                # B. 物理 Slot 占用互斥 (同拍改写同一槽位)
                if ("slot_id" in op_i.metadata and "slot_id" in op_j.metadata and 
                    op_i.metadata["slot_id"] == op_j.metadata["slot_id"]):
                    solver.add(t[op_i.node_id] != t[op_j.node_id])

        # C. Bank 冲突公理
        SynthesisAxioms.add_bank_conflict_axiom(solver, ops, t)
        
        # D. Liveness 防冲刷公理 (Register Allocation)
        SynthesisAxioms.add_liveness_flush_axiom(solver, ops, t, iq_vars)

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
            # 将推导出的硬件队列号写回 AST Metadata，实现寄存器自适应分配
            for op in ops:
                if op.node_id in iq_vars:
                    op.metadata["iq_id"] = model[iq_vars[op.node_id]].as_long()
            return {op_id: model[t[op_id]].as_long() for op_id in t}
        
        return None
