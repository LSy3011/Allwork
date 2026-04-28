import z3
from typing import List, Dict, Any, Optional, Tuple
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec
from synthesis.axioms import SynthesisAxioms as V5SynthesisAxioms

class UCodeSynthesizer:
    """
    AIACC 微码综合器 (V25.0 - 恢复通用资源池分配)。
    修正了对 UR Bank 绑定的误解：ur-wx 可以自由访问所有 Bank。
    """
    def __init__(self, hw_spec: HardwareSpec):
        self.hw_spec = hw_spec
        self.res_to_iqs = {}
        registry = self.hw_spec.config.get("iq_registry", {})
        for res_type in registry.keys():
            self.res_to_iqs[res_type.upper()] = self.hw_spec.get_iq_ids(res_type)

    def _get_resource_type(self, op_type: str) -> str:
        u = op_type.upper()
        if "UR_READ" in u: return "UR_READ"
        if "UR_WRITE" in u: return "UR_WRITE"
        return u

    def solve_with_units(self, ops: List[MIROp], MII: int = 1, MAX_II: int = 32) -> Optional[Dict[str, Any]]:
        for II in range(MII, MAX_II + 1):
            print(f"[SOLVER] Trying II={II}...")
            result = self._solve_for_ii_with_units(ops, II)
            if result:
                schedule, unit_map = result
                # 🛸 [V5.1 ADD] 引入“脚本反证”机制 (Script-based Counter-Verification)
                # 即使 SMT 返回 SAT，也必须由脚本二次确认绝对时间对齐，确保硬件 IQ 安全。
                if self.validate_physical_schedule(ops, schedule, unit_map, II):
                    return {"schedule": schedule, "II": II, "unit_assignments": unit_map}
                else:
                    print(f"[VERIFIER] Counter-verification FAILED for II={II}. Forcing II increment.")
        return None

    def validate_physical_schedule(self, ops: List[MIROp], schedule: Dict[str, int], unit_map: Dict[str, str], II: int) -> bool:
        """
        全物理维度严格反证审计 (Rigid Physical Audit V5.2)。
        """
        nodes_by_id = {op.trace_id: op for op in ops}
        iq_to_modulo: Dict[str, set] = {}
        iq_to_phases: Dict[str, set] = {}
        
        for op in ops:
            t = schedule[op.trace_id]
            iq_name = unit_map.get(op.trace_id)
            if not iq_name: continue
            
            # 1. [资源审计] 模冲突二次确认
            mod = t % II
            if mod in iq_to_modulo.get(iq_name, set()):
                print(f"[VERIFIER-FAIL] Modulo Collision in {iq_name} at Slot {mod}")
                return False
            iq_to_modulo.setdefault(iq_name, set()).add(mod)

            # 2. [安全审计] 访存相位隔离 (Side-Effect Isolation)
            if op.op_type.upper() in ["UR_READ", "UR_WRITE"]:
                if not (op.op_type.upper() == "UR_READ" and op.metadata.get("stride") == 0):
                    phase = t // II
                    iq_to_phases.setdefault(iq_name, set()).add(phase)
        
        # 3. [相位审计] 检查访存单元是否跨 Phase
        for iq_name, phases in iq_to_phases.items():
            if len(phases) > 1:
                print(f"[VERIFIER-FAIL] Phase Contamination in {iq_name}: {phases}")
                return False
                
        # 4. [时序审计] 生产 -> RTOVR 采样 -> ALU 消费 严格对齐
        for op in ops:
            m_type = op.op_type.upper()
            deps = op.metadata.get("depends_on", [])
            
            # 审计：RTOVR 必须在生产者完成的第一拍采样
            if m_type == "RTOVR" and deps:
                prod = nodes_by_id.get(deps[0])
                if prod:
                    lat = self.hw_spec.delays.get(prod.op_type.upper(), 1)
                    if schedule[op.trace_id] != (schedule[prod.trace_id] + lat):
                        print(f"[VERIFIER-FAIL] RTOVR Timing Drift: {op.trace_id} sampled late.")
                        return False
            
            # 审计：ALU 必须在 RTOVR 脉冲后的下一拍采样
            if m_type in ["FPMUL", "FPADD"]:
                for dep_id in deps:
                    if dep_id in nodes_by_id and nodes_by_id[dep_id].op_type == "RTOVR":
                        delta = schedule[op.trace_id] - schedule[dep_id]
                        if delta != 1:
                            print(f"[VERIFIER-FAIL] ALU Sampling Window Violation: {op.trace_id} -> {dep_id} delta={delta}")
                            return False
                            
        return True

    def _solve_for_ii_with_units(self, ops: List[MIROp], II: int) -> Optional[Tuple[Dict[str, int], Dict[str, str]]]:
        solver = z3.Solver()
        t_vars = {op.trace_id: z3.Int(f"t_{op.trace_id}") for op in ops}
        u_vars = {op.trace_id: z3.Int(f"u_{op.trace_id}") for op in ops}

        # 1. 基础物理约束
        for op in ops:
            # 🛸 [V27.1 REVERT] 回归 t >= 0。依靠 L_init=1 来提供物理启动缓冲。
            solver.add(t_vars[op.trace_id] >= 0)
            u_type = self._get_resource_type(op.op_type)
            valid_iqs = self.res_to_iqs.get(u_type, [0])
            # 恢复：UR 单元可以在同类池中自由选择 (ur-w0, 1, 2, 3 均可)
            solver.add(z3.Or([u_vars[op.trace_id] == iq_id for iq_id in valid_iqs]))

        # 2. 物理公理
        V5SynthesisAxioms.add_latency_axiom(solver, ops, t_vars, self.hw_spec)
        V5SynthesisAxioms.add_rtovr_crossbar_axiom(solver, ops, t_vars, self.hw_spec)
        # 🛸 [V27.0 RESTORE] 恢复影子绑定公理，确保 RTOVR 脉冲选通窗口对齐
        V5SynthesisAxioms.add_shadow_binding_axiom(solver, ops, t_vars, II)
        # 🛸 [V27.0 RESTORE] 恢复防覆盖公理，确保在无 FIFO 架构下的数据存活期
        V5SynthesisAxioms.add_anti_overwrite_axiom(solver, ops, t_vars, II, self.hw_spec, no_output_fifo=True)
        # 🛸 [V5.2 ADD] 局部相位隔离公理：仅针对访存等有副作用的单元
        V5SynthesisAxioms.add_side_effect_phase_axiom(solver, ops, t_vars, u_vars, II)

        # 🛸 [V27.5 FIX] 强制 RTOVR 严格对齐采样
        for op in ops:
            if op.op_type.upper() == "RTOVR":
                for dep_id in op.metadata.get("depends_on", []):
                    producer = next((o for o in ops if o.trace_id == dep_id), None)
                    if producer:
                        lat = self.hw_spec.delays.get(producer.op_type.upper(), 1)
                        solver.add(t_vars[op.trace_id] == t_vars[producer.trace_id] + lat)

        # 3. 模冲突
        for i in range(len(ops)):
            for j in range(i + 1, len(ops)):
                op_i, op_j = ops[i], ops[j]
                if self._get_resource_type(op_i.op_type) == self._get_resource_type(op_j.op_type):
                    solver.add(z3.Implies(u_vars[op_i.trace_id] == u_vars[op_j.trace_id],
                                         t_vars[op_i.trace_id] % II != t_vars[op_j.trace_id] % II))

        # 4. RtoVR 条件绑定 (保持 V21 鲁棒逻辑)
        DVR_MAP = self.hw_spec.get_dvr_map()
        registry = self.hw_spec.config.get("iq_registry", {})
        base_rt = registry.get("RTOVR", 100)
        for op in ops:
            u_type = self._get_resource_type(op.op_type)
            valid_iqs = self.res_to_iqs.get(u_type, [])
            deps = op.metadata.get("depends_on", [])
            if not deps: continue
            port_names = ["S0", "S1", "S2", "S3", "D", "ST"]
            if u_type == "UR_WRITE": port_names = ["D"]
            for i, dep_id in enumerate(deps):
                producer = next((o for o in ops if o.trace_id == dep_id), None)
                if producer and producer.op_type == "RTOVR":
                    p_name = port_names[i] if i < len(port_names) else "S0"
                    for idx, iq_id in enumerate(valid_iqs):
                        inst_name = f"{u_type}_{idx}" if len(valid_iqs) > 1 else u_type
                        if inst_name in DVR_MAP and p_name in DVR_MAP[inst_name]:
                            target_slot = DVR_MAP[inst_name][p_name]
                            solver.add(z3.Implies(u_vars[op.trace_id] == iq_id, 
                                                 u_vars[producer.trace_id] == base_rt + target_slot))
        # 5. 手动 IQ 绑定 (供调试使用)
        for op in ops:
            manual_iq = op.metadata.get("iq_id")
            if manual_iq is not None:
                solver.add(u_vars[op.trace_id] == manual_iq)

        if solver.check() != z3.sat:
            # print(f"[SOLVER] II={II} is UNSAT.")
            return None
        model = solver.model()
        schedule = {op.trace_id: model.eval(t_vars[op.trace_id]).as_long() for op in ops}
        unit_assignments = {}
        for op in ops:
            u_id = model.eval(u_vars[op.trace_id]).as_long()
            iq_name = self.hw_spec.get_iq_name(u_id, op.op_type)
            unit_assignments[op.trace_id] = iq_name
            op.metadata["iq_id"] = u_id
            if op.op_type.upper() == "RTOVR":
                op.metadata["slot_id"] = u_id - base_rt
        return schedule, unit_assignments
