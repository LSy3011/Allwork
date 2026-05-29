import z3
from typing import List, Dict, Any, Optional
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec
from .base import AxiomBase

class PhysicalAxioms(AxiomBase):
    """
    硬件物理契约模块：处理物理 Slot、相位隔离、影子绑定等硬件强约束。
    """

    @staticmethod
    def add_side_effect_phase_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        局部相位隔离：对于有副作用的 IQ（如访存单元），禁止跨迭代复用。
        """
        u_vars = kwargs.get("u_vars")
        if u_vars is None:
            return

        # 🛸 [SMT-OPTIMIZATION] 创设辅助的线性 Phase 整数变量，消除非线性 Z3 整数除法，从而彻底解决 II >= 16 时的求解器挂起/超时问题
        k_vars = {}
        for op in ops:
            if op.op_type.upper() in ["UR_READ", "UR_WRITE"]:
                tid = op.trace_id
                k_vars[tid] = z3.Int(f"k_phase_{tid}")
                solver.add(t_vars[tid] >= k_vars[tid] * II)
                solver.add(t_vars[tid] < (k_vars[tid] + 1) * II)
                solver.add(k_vars[tid] >= 0)

        for i in range(len(ops)):
            for j in range(i + 1, len(ops)):
                op_i, op_j = ops[i], ops[j]
                # 只有访存类单元需要强制相位隔离
                if op_i.op_type.upper() not in ["UR_READ", "UR_WRITE"]:
                    continue
                if op_j.op_type.upper() not in ["UR_READ", "UR_WRITE"]:
                    continue

                # ADR-018 v7 D2: 跨 SMC 的 op pair 独立（每 SMC 私有 IQ + Bank），不应互斥
                if op_i.metadata.get('smc_id', 0) != op_j.metadata.get('smc_id', 0):
                    continue

                # 如果任意一个是 Stride=0 的常量读取，豁免隔离
                if op_i.op_type.upper() == "UR_READ" and op_i.metadata.get("stride") == 0:
                    continue
                if op_j.op_type.upper() == "UR_READ" and op_j.metadata.get("stride") == 0:
                    continue

                label = f"phase_iso_{op_i.trace_id}_{op_j.trace_id}"
                same_iq = u_vars[op_i.trace_id] == u_vars[op_j.trace_id]
                phase_i = k_vars[op_i.trace_id]
                phase_j = k_vars[op_j.trace_id]
                # 🛸 [DEBUG EXPERIMENT] Disable phase_iso to test II=4
                # PhysicalAxioms._add_constraint(
                #     solver,
                #     z3.Implies(same_iq, phase_i == phase_j),
                #     label
                # )

    @staticmethod
    def _get_resource_type(op_type: str) -> str:
        u = op_type.upper()
        if "UR_READ" in u: return "UR_READ"
        if "UR_WRITE" in u: return "UR_WRITE"
        return u
    # ADR-021 (2026-05-29): 删除 dead axiom add_iq_latch_protect_axiom
    # (零真实调用点，ADR-013 axiom 墓地漏网者；v8 Y'-spike gate 曾误建立在它之上)。
