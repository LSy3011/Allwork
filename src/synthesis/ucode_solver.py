import z3
import json
from typing import List, Dict, Optional, Tuple
from synthesis.backend import SMTSynthesizer
from synthesis.axioms import SynthesisAxioms
from ir.mir.mir_op import MIROp

class UCodeSynthesizer(SMTSynthesizer):
    """
    专门用于微码生成的合成器。
    通过子类化实现物理资源捕获，完全不修改基类 backend.py。
    """
    
    def _get_resource_type(self, op_type: str) -> str:
        """从算子类型映射到硬件资源组"""
        op_type = op_type.upper()
        if op_type in ["FPMUL", "FPADD", "RTOVR"]: return op_type
        if op_type == "UR_READ": return "UR_READ"
        if op_type == "UR_WRITE": return "UR_WRITE"
        return "ALU"

    def solve_with_units(self, ops: List[MIROp], MII: int, MAX_II: int) -> Optional[Dict]:
        """
        全量模调度求解，返回包含 unit_assignments 的字典。
        """
        print(f"[UCode-SMT] Modulo scheduling {len(ops)} ops, searching II in [{MII}, {MAX_II}]")
        
        for ii in range(MII, MAX_II + 1):
            print(f"[UCode-SMT] Trying II={ii}...", end=" ", flush=True)
            res = self._solve_for_ii_with_units(ops, ii)
            if res:
                print("SAT")
                schedule, unit_assignments = res
                return {
                    "schedule": schedule,
                    "II": ii,
                    "unit_assignments": unit_assignments
                }
            print("UNSAT")
        return None

    def _solve_for_ii_with_units(self, ops: List[MIROp], II: int) -> Optional[Tuple[Dict[str, int], Dict[str, str]]]:
        """
        内部求解逻辑：建立 SMT 模型并提取物理单元分配。
        """
        solver = z3.Solver()
        t_vars = {op.trace_id: z3.Int(f"t_{op.trace_id}") for op in ops}
        u_vars = {op.trace_id: z3.Int(f"u_{op.trace_id}") for op in ops}

        # 1. 基础物理约束
        for op in ops:
            solver.add(t_vars[op.trace_id] >= 0)
            res_type = self._get_resource_type(op.op_type)
            
            # 从基类预构建的资源映射中获取物理 IQ ID 列表
            valid_iqs = self.res_to_iqs.get(res_type, [])
            if not valid_iqs:
                # 容错：如果找不到特定类型，尝试查找通用的 ALU
                valid_iqs = self.res_to_iqs.get("ALU", [0])
            
            # 约束 u_vars 必须在有效物理 ID 列表中
            solver.add(z3.Or([u_vars[op.trace_id] == iq_id for iq_id in valid_iqs]))

        # 2. 物理公理集加载
        SynthesisAxioms.add_latency_axiom(solver, ops, t_vars, self.hw_spec)
        SynthesisAxioms.add_shadow_binding_axiom(solver, ops, t_vars, II)
        SynthesisAxioms.add_anti_overwrite_axiom(solver, ops, t_vars, II, self.hw_spec)

        # 3. 物理槽位互斥 (Modulo Conflict on Units)
        for i in range(len(ops)):
            for j in range(i + 1, len(ops)):
                op_i, op_j = ops[i], ops[j]
                if self._get_resource_type(op_i.op_type) == self._get_resource_type(op_j.op_type):
                    # 如果分配到了同一个物理单元实例，则其槽位必须不同
                    solver.add(z3.Implies(u_vars[op_i.trace_id] == u_vars[op_j.trace_id],
                                         t_vars[op_i.trace_id] % II != t_vars[op_j.trace_id] % II))

        # 🛸 [NEW] V5.5 RtoVR 强绑定约束 (Physical Slot Binding)
        # 动态从 rtovr_config.yaml 加载映射表
        DVR_MAP = self.hw_spec.get_dvr_map()
        
        registry = self.hw_spec.config.get("iq_registry", {})
        base_rt = registry.get("RTOVR", 100)

        for op in ops:
            if op.op_type.upper() == "RTOVR":
                # 寻找这个 RtoVR 的消费者
                consumer = None
                port_idx = 0
                for c_node in ops:
                    deps = c_node.metadata.get("depends_on", [])
                    if op.trace_id in deps:
                        consumer = c_node
                        port_idx = deps.index(op.trace_id)
                        break
                
                if consumer:
                    c_type = consumer.op_type.upper()
                    if "WRITE" in c_type: # 适配 UR_WRITE
                        port_name = "D"
                    else:
                        port_name = f"S{port_idx}"
                    
                    if c_type in DVR_MAP and port_name in DVR_MAP[c_type]:
                        target_slot = DVR_MAP[c_type][port_name]
                        # 🛸 强制绑定到物理槽位
                        solver.add(u_vars[op.trace_id] == base_rt + target_slot)
                        print(f"[SMT-FIX] Binding {op.trace_id} -> {consumer.trace_id}:{port_name} (Slot {target_slot})")

        if solver.check() != z3.sat:
            return None

        model = solver.model()
        schedule = {op.trace_id: model.eval(t_vars[op.trace_id]).as_long() for op in ops}
        
        unit_assignments = {}
        for op in ops:
            u_id = model.eval(u_vars[op.trace_id]).as_long()
            iq_name = self.hw_spec.get_iq_name(u_id, op.op_type)
            unit_assignments[op.trace_id] = iq_name
            
            # 🛸 [FIX] 直接回填物理 ID 到元数据
            op.metadata["iq_id"] = u_id
            if op.op_type.upper() == "RTOVR":
                # 计算局部槽位 ID (用于 PhysicalAssembler 的分桶)
                registry = self.hw_spec.config.get("iq_registry", {})
                base_rt = registry.get("RTOVR", 100)
                op.metadata["slot_id"] = u_id - base_rt
            
        return schedule, unit_assignments
