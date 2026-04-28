import z3
from typing import List, Dict, Any, Optional
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec
from synthesis.axioms import SynthesisAxioms

class V5SynthesisAxioms(SynthesisAxioms):
    """
    AIACC-V5 专用物理公理扩展。
    """

    @staticmethod
    def add_v5_latency_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        spec: HardwareSpec,
    ):
        """[V5 强约束] RAW 依赖：强制确保 latency 存在，否则报错。"""
        for op in ops:
            for dep_id in op.metadata.get("depends_on", []):
                producer = next((o for o in ops if o.trace_id == dep_id), None)
                if not producer: continue
                
                op_type = producer.op_type.upper()
                if op_type not in spec.delays:
                    raise KeyError(f"Critical SMT-V5 Error: Latency for '{op_type}' (Trace: {producer.trace_id}) missing in hardware spec!")
                lat = spec.delays[op_type]
                
                label = f"v5_lat_{op.trace_id}_{dep_id}"
                V5SynthesisAxioms._add_constraint(
                    solver, t_vars[op.trace_id] >= t_vars[dep_id] + lat, label
                )

    @staticmethod
    def add_v5_anti_overwrite_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        II: int,
        spec: HardwareSpec
    ):
        """
        [V5 强约束] 防覆盖公理。
        强制要求：消费必须在 II 窗口内完成，且严格小于 II 以避免冲突。
        """
        for op in ops:
            if op.metadata.get("is_invariant", False): continue
            consumers = [o for o in ops if op.trace_id in o.metadata.get("depends_on", [])]
            if not consumers: continue

            m_val = t_vars[consumers[0].trace_id]
            for cons in consumers[1:]:
                m_val = z3.If(t_vars[cons.trace_id] > m_val, t_vars[cons.trace_id], m_val)

            # 获取正确的 latency
            op_type = op.op_type.upper()
            lat = spec.delays.get(op_type, 1)
            data_ready = t_vars[op.trace_id] + lat - 1

            # 🛸 [FIX] 边界修正：必须 < II 拍，确保在下一迭代冲刷前完成消费
            V5SynthesisAxioms._add_constraint(
                solver,
                m_val - data_ready < II,
                f"v5_anti_overwrite_{op.trace_id}"
            )

    @staticmethod
    def add_v5_shadow_binding_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        II: int
    ):
        """[V5 策略] 影子绑定：上限为 II - 1。"""
        nodes_by_id = {op.trace_id: op for op in ops}
        for op in ops:
            rtovr_deps = [d for d in op.metadata.get("depends_on", []) if d in nodes_by_id and nodes_by_id[d].op_type == "RTOVR"]
            if rtovr_deps and op.op_type.upper() in ["FPMUL", "FPADD", "UR_WRITE", "LOGIC", "FPTOINT", "INTTOFP", "SHIFTUP", "SHIFTDN", "INTADD"]:
                for rd in rtovr_deps:
                    label = f"v5_shadow_{rd}_{op.trace_id}"
                    # 硬件除 RTOVR 外可锁存，因此间距仅受限于模调度窗口
                    upper_bound = max(1, II - 1)
                    V5SynthesisAxioms._add_constraint(solver, t_vars[op.trace_id] >= t_vars[rd] + 1, f"{label}_lower")
                    V5SynthesisAxioms._add_constraint(solver, t_vars[op.trace_id] <= t_vars[rd] + upper_bound, f"{label}_upper")
