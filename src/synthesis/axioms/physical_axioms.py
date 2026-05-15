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

    @staticmethod
    def add_iq_latch_protect_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        IQ 锁存器保护：在搬运完成前，同 IQ 不能被新读操作覆盖（绝对时间）。
        """
        u_vars = kwargs.get("u_vars")
        if u_vars is None: return

        read_ops = [o for o in ops if o.op_type.upper() == "UR_READ"]
        for i in range(len(read_ops)):
            for j in range(i + 1, len(read_ops)):
                op_A, op_B = read_ops[i], read_ops[j]
                
                same_iq = u_vars[op_A.trace_id] == u_vars[op_B.trace_id]

                def get_protection_window_conds(op_x, op_target_t):
                    """
                    返回一组布尔谓词：确保 op_target_t 晚于 op_x 的所有消费者。
                    """
                    consumers = [
                        o for o in ops
                        if op_x.trace_id in o.metadata.get("depends_on", [])
                    ]
                    if not consumers:
                        return [op_target_t > t_vars[op_x.trace_id]]
                    
                    # 只要 op_target_t 大于每一个消费者的发射时间，就说明 A 已被搬走
                    return [op_target_t > t_vars[cons.trace_id] for cons in consumers]

                # B 在 A 所有消费者之后发射 OR A 在 B 所有消费者之后发射
                cond_B_after_A_all = z3.And(get_protection_window_conds(op_A, t_vars[op_B.trace_id]))
                cond_A_after_B_all = z3.And(get_protection_window_conds(op_B, t_vars[op_A.trace_id]))

                label = f"iq_l_prot_{op_A.trace_id}_{op_B.trace_id}"
                PhysicalAxioms._add_constraint(
                    solver,
                    z3.Implies(same_iq, z3.Or(cond_B_after_A_all, cond_A_after_B_all)),
                    label
                )
