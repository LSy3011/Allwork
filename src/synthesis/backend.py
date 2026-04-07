from typing import List, Dict
import z3
from ir.mir.mir_op import MIROp
from synthesis.axioms import SynthesisAxioms
from driver.hw_resource import ResourceType

class SMTSynthesizer:
    """
    AIACC SMT 合成引擎 (硬件对齐 · 动态资源版)。
    - 根据 system_core.yaml 动态分配物理 IQ 端口。
    - 自动识别单位实例 (Unit Instances) 数量，支持负载均衡。
    """
    def __init__(self, hw_spec):
        self.hw_spec = hw_spec
        self.norm_delays = {k.upper(): v for k, v in hw_spec.delays.items()}
        
        # 1. 建立资源到物理 IQ ID 的映射表
        self.res_to_iqs = self._build_resource_map(hw_spec)

    def _build_resource_map(self, hw_spec) -> Dict[str, List[int]]:
        """
        根据硬件定义构建资源池。
        例如: UR_READ -> [200, 201, 202, 203]
        """
        res_map = {}
        registry = hw_spec.config.get("iq_registry", {})
        instances = hw_spec.config.get("resource_constraints", {}).get("unit_instances", {})
        
        # 获取默认读写端口数
        default_r = hw_spec.config["resource_constraints"].get("total_read_ports", 1)
        default_w = hw_spec.config["resource_constraints"].get("total_write_ports", 1)
        
        # 处理基础 UR 端口 (通过 registry 注册表对齐)
        r_count = instances.get("UR_READ", default_r)
        base_r = registry.get("UR_READ", 30)
        res_map["UR_READ"] = [base_r + i for i in range(r_count)]
        
        w_count = instances.get("UR_WRITE", default_w)
        base_w = registry.get("UR_WRITE", 35)
        res_map["UR_WRITE"] = [base_w + i for i in range(w_count)]
        
        # 处理 ALU 系列单元 (RTOVR 已在 registry)
        for unit_type, count in instances.items():
            if unit_type in ["UR_READ", "UR_WRITE"]: continue # 已处理
            base_id = registry.get(unit_type, 0)
            res_map[unit_type] = [base_id + i for i in range(count)]
            
        return res_map

    def solve(self, ops: List[MIROp], strategy: str = "balanced", **kwargs) -> Dict[str, int]:
        solver = z3.Optimize()
        nodes_by_id = {op.trace_id: op for op in ops}
        t = {op.trace_id: z3.Int(f"t_{op.trace_id}") for op in ops}
        
        # 🛸 获取硬件全局注册表
        registry = self.hw_spec.config.get("iq_registry", {})
        instances = self.hw_spec.config.get("resource_constraints", {}).get("unit_instances", {})
        
        for op_id in t:
            solver.add(t[op_id] >= 0)

        # 2. 动态物理端口分配 (Symbolic Resource Mapping)
        iq_vars = {}
        for op in ops:
            # 获取算子要求的资源类型 (语义符号优先)
            res_key = None
            if "iq_type" in op.metadata:
                res_meta = op.metadata["iq_type"]
                res_key = res_meta.name if hasattr(res_meta, 'name') else str(res_meta)
            else:
                res_key = str(op.op_type).upper()
            
            # 从硬件规格中获取其对应的合法物理 IQ ID 池
            valid_iqs = self.hw_spec.get_iq_ids(res_key)
            if valid_iqs:
                iv = z3.Int(f"iq_{op.trace_id}")
                iq_vars[op.trace_id] = iv
                # 约束：只能在合法的物理实例中选一个
                solver.add(z3.Or([iv == iq_id for iq_id in valid_iqs]))
                
                # 如果 DFG 显式固定了分配位
                if op.metadata.get("iq_id") is not None:
                    solver.add(iv == op.metadata["iq_id"])
                    
            # 🛰️ 物理对齐：UR_WRITE 会根据其前驱 RTOVR 的 Slot 进行反向约束
            base_w = registry.get("UR_WRITE", 35)
            if res_key == "UR_WRITE" and op.metadata.get("iq_id") is None:
                for dep_id in op.metadata.get("depends_on", []):
                    dep_node = nodes_by_id.get(dep_id)
                    if dep_node and dep_node.op_type.upper() == "RTOVR" and "slot_id" in dep_node.metadata:
                        slot = int(dep_node.metadata["slot_id"])
                        if base_w <= slot <= base_w + 3: 
                            solver.add(iq_vars[op.trace_id] == slot)

        # 3. 数据依赖约束 (RAW) — (L-1 + transit)
        for op in ops:
            for dep_id in op.metadata.get("depends_on", []):
                if dep_id in t:
                    dep_node = nodes_by_id[dep_id]
                    delay = self.norm_delays.get(dep_node.op_type.upper(), 1)
                    # 🛰️ [INDUSTRIAL ALIGNMENT] UR_WRITE 之后需 2 拍物理缓冲，防止总线竞争窗口引发的读取 0 合法性冲突
                    transit = 2 if dep_node.op_type.upper() == "UR_WRITE" else (1 if dep_node.op_type.upper() != "RTOVR" else 0)
                    solver.add(t[op.trace_id] >= t[dep_id] + (delay - 1) + transit)

        # 4. 物理资源互斥与公理注入 (去硬编码版)
        SynthesisAxioms.add_iq_mutex_axiom(solver, ops, t)
        SynthesisAxioms.add_rtovr_crossbar_axiom(solver, ops, t, self.hw_spec)
        SynthesisAxioms.add_bank_conflict_axiom(solver, ops, t)
        SynthesisAxioms.add_liveness_flush_axiom(solver, ops, t, iq_vars)
        SynthesisAxioms.add_alu_liveness_axiom(solver, ops, t, iq_vars)

        # 5. 影子绑定与同步
        for op in ops:
            rtovr_deps = [d for d in op.metadata.get("depends_on", []) 
                         if d in nodes_by_id and nodes_by_id[d].op_type == "RTOVR"]
            
            if rtovr_deps and op.op_type.upper() in ["FPMUL", "FPADD", "UR_WRITE", "LOGIC"]:
                for rd in rtovr_deps:
                    # 🛸 [INDUSTRIAL ALIGNMENT] 强制数据-指令对齐
                    # 修改：从 >= 1 改为 == 1，确保数据在插槽内不会因为排布过早而丢失
                    solver.add(t[op.trace_id] == t[rd] + 1)
                    
                    # 🛸 物理插槽对齐 (Crucial for UR_WRITE)
                    # 如果是 UR_WRITE，必须强制将其前驱 RTOVR 的 Slot 设为与其物理 IQ 对应的位置
                    if op.op_type.upper() == "UR_WRITE":
                        # UR_W0(204) -> Slot 35, UR_W1(205) -> Slot 36 ...
                        # 我们通过 iq_vars 动态约束 RTOVR 的 slot_id 元数据
                        iq_var = iq_vars.get(op.trace_id)
                        if iq_var is not None:
                            # 建立逻辑：RTOVR_Slot = UR_WRITE_IQ - 204 + 35
                            # 注意：由于 slot_id 是 metadata 里的常量，我们需要在解算后回填
                            pass 

                # 🛰️ 物理松绑：不再强制所有操作数同步到达，硬件锁存器会处理异步。
                pass

        SynthesisAxioms.add_objective(solver, t)

        if solver.check() == z3.sat:
            m = solver.model()
            # 🛸 将推导出的物理队列号回填，实现资源自动负载均衡
            for op in ops:
                if op.trace_id in iq_vars:
                    assigned_iq = m[iq_vars[op.trace_id]].as_long()
                    op.metadata["iq_id"] = assigned_iq
                    
                    # 🛰️ 物理自动对位：如果当前是 UR_WRITE，强制纠正其消费的 RTOVR Slot
                    base_w = registry.get("UR_WRITE", 35)
                    if op.op_type.upper() == "UR_WRITE":
                        for dep_id in op.metadata.get("depends_on", []):
                            dep_node = nodes_by_id.get(dep_id)
                            if dep_node and dep_node.op_type.upper() == "RTOVR":
                                # 强制对齐 (UR_W1 (ID 36) -> Slot 36)
                                dep_node.metadata["slot_id"] = assigned_iq

            return {op_id: m[t[op_id]].as_long() for op_id in t}
        
        return None
