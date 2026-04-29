import z3
from typing import List, Dict, Any, Optional, Tuple
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec
import synthesis.axioms as axioms

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
        """
        使用二分查找优化 II 搜索过程。
        """
        low, high = MII, MAX_II
        best_res = None
        
        while low <= high:
            mid = (low + high) // 2
            print(f"[SOLVER] Trying II={mid} (Range: [{low}, {high}])...")
            
            result = self._solve_for_ii_with_units(ops, mid)
            if result:
                schedule, unit_map = result
                # 执行物理验证审计
                if self.validate_physical_schedule(ops, schedule, unit_map, mid):
                    print(f"[SOLVER] II={mid} is SAT and Verified.")
                    best_res = {"schedule": schedule, "II": mid, "unit_assignments": unit_map}
                    high = mid - 1 # 继续寻找更小的 II
                else:
                    print(f"[VERIFIER] Counter-verification FAILED for II={mid}. Treating as UNSAT.")
                    low = mid + 1
            else:
                print(f"[SOLVER] II={mid} is UNSAT.")
                low = mid + 1
                
        return best_res

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
        axioms.add_latency_axiom(solver, ops, t_vars, II, self.hw_spec)
        axioms.add_rtovr_crossbar_axiom(solver, ops, t_vars, II, self.hw_spec, u_vars=u_vars)
        axioms.add_shadow_binding_axiom(solver, ops, t_vars, II, self.hw_spec)
        axioms.add_anti_overwrite_axiom(solver, ops, t_vars, II, self.hw_spec)
        axioms.add_side_effect_phase_axiom(solver, ops, t_vars, II, self.hw_spec, u_vars=u_vars)
        axioms.add_rtovr_alignment_axiom(solver, ops, t_vars, II, self.hw_spec)


        # 3. 模冲突
        axioms.add_resource_constraint(solver, ops, t_vars, II, self.hw_spec, u_vars=u_vars)

        # 4. RtoVR 物理绑定
        axioms.add_physical_binding_axiom(solver, ops, t_vars, II, self.hw_spec, u_vars=u_vars)
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
        registry = self.hw_spec.config.get("iq_registry", {})
        base_rt = registry.get("RTOVR", 100)
        unit_assignments = {}
        for op in ops:
            u_id = model.eval(u_vars[op.trace_id]).as_long()
            iq_name = self.hw_spec.get_iq_name(u_id, op.op_type)
            unit_assignments[op.trace_id] = iq_name
            op.metadata["iq_id"] = u_id
            if op.op_type.upper() == "RTOVR":
                op.metadata["slot_id"] = u_id - base_rt
        return schedule, unit_assignments
