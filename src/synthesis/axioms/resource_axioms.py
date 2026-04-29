import z3
from typing import List, Dict, Any, Optional
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec
from .base import AxiomBase

class ResourceAxioms(AxiomBase):
    """
    模调度冲突模块：处理计算单元、Bank、读写端口在 II 窗口内的模冲突。
    """

    @staticmethod
    def add_resource_constraint(solver, ops, t_vars, II, spec, **kwargs):
        """
        通用资源约束入口。
        """
        ResourceAxioms.add_modulo_constraint(solver, ops, t_vars, II, spec, **kwargs)
        # 兼容旧版的其他资源约束
        ResourceAxioms.add_bank_conflict_axiom(solver, ops, t_vars, II, spec, **kwargs)
        ResourceAxioms.add_spill_reload_mutex_axiom(solver, ops, t_vars, II, spec, **kwargs)

    @staticmethod
    def add_modulo_constraint(solver, ops, t_vars, II, spec, **kwargs):
        """
        基础模冲突：同物理单元的操作必须落在不同的模槽。
        """
        u_vars = kwargs.get("u_vars")
        if u_vars is None:
            return

        for i in range(len(ops)):
            for j in range(i + 1, len(ops)):
                op_i, op_j = ops[i], ops[j]
                # 检查是否可能映射到同一资源类型
                type_i = ResourceAxioms._get_resource_type(op_i.op_type)
                type_j = ResourceAxioms._get_resource_type(op_j.op_type)
                
                if type_i == type_j:
                    label = f"mod_conflict_{op_i.trace_id}_{op_j.trace_id}"
                    # 如果 unit_id 相同，则 mod 必须不同
                    ResourceAxioms._add_constraint(
                        solver,
                        z3.Implies(
                            u_vars[op_i.trace_id] == u_vars[op_j.trace_id],
                            t_vars[op_i.trace_id] % II != t_vars[op_j.trace_id] % II
                        ),
                        label
                    )

    @staticmethod
    def _get_resource_type(op_type: str) -> str:
        u = op_type.upper()
        if "UR_READ" in u: return "UR_READ"
        if "UR_WRITE" in u: return "UR_WRITE"
        return u

    @staticmethod
    def add_bank_conflict_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        Bank 冲突公ly：同 Bank 同类型操作必须落在不同模槽。
        """
        bank_ops = [o for o in ops if o.metadata.get("manual_bank") is not None]
        bank_groups: Dict[Any, list] = {}
        for op in bank_ops:
            bid = op.metadata["manual_bank"]
            bank_groups.setdefault(bid, []).append(op.trace_id)

        for bid, op_ids in bank_groups.items():
            if len(op_ids) > 1:
                for i in range(len(op_ids)):
                    for j in range(i + 1, len(op_ids)):
                        id_i, id_j = op_ids[i], op_ids[j]
                        label = f"bank_gap_{bid}_{id_i}_{id_j}"
                        ResourceAxioms._add_constraint(
                            solver, 
                            t_vars[id_i] % II != t_vars[id_j] % II, 
                            label
                        )

    @staticmethod
    def add_spill_reload_mutex_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        Spill/Reload 互斥：同 Spill 对应的多个 Reload 操作必须落在不同模槽。
        """
        spill_groups: Dict[str, list] = {}
        for op in ops:
            if op.op_type.upper() == "UR_READ":
                for d in op.metadata.get("depends_on", []):
                    if isinstance(d, str) and d.startswith("spill_"):
                        spill_groups.setdefault(d, []).append(op)

        for spill_id, reloads in spill_groups.items():
            if len(reloads) <= 1:
                continue
            for i in range(len(reloads)):
                for j in range(i + 1, len(reloads)):
                    op_i, op_j = reloads[i], reloads[j]
                    label = f"spill_re_{spill_id}_{op_i.trace_id}_{op_j.trace_id}"
                    ResourceAxioms._add_constraint(
                        solver,
                        t_vars[op_i.trace_id] % II != t_vars[op_j.trace_id] % II,
                        label
                    )

    @staticmethod
    def add_read_port_mutex_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        读端口时分复用。
        """
        # 已经在 add_modulo_constraint 中通过 u_vars 处理了通用的端口/单元复用
        pass

    @staticmethod
    def add_write_port_mutex_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        写端口时分复用。
        """
        # 已经在 add_modulo_constraint 中通过 u_vars 处理了通用的端口/单元复用
        pass
