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
        # 对称性破缺优化
        ResourceAxioms.add_symmetry_breaking_axiom(solver, ops, t_vars, II, spec, **kwargs)

    @staticmethod
    def add_modulo_constraint(solver, ops, t_vars, II, spec, **kwargs):
        """
        基础模冲突：同物理单元的操作必须落在不同的模槽。
        """
        u_vars = kwargs.get("u_vars")
        if u_vars is None:
            return

        # 按资源类型分组操作
        type_to_ops: Dict[str, List[MIROp]] = {}
        for op in ops:
            u_type = ResourceAxioms._get_resource_type(op.op_type)
            type_to_ops.setdefault(u_type, []).append(op)

        for u_type, op_list in type_to_ops.items():
            if len(op_list) <= 1:
                continue
            
            # 获取该类型的所有物理实例 ID
            valid_iqs = spec.get_iq_ids(u_type)
            if not valid_iqs:
                continue

            for iq_id in valid_iqs:
                # 对于每一个物理单元 iq_id，每一个模槽 slot，最多只能有一个操作映射上去
                for slot in range(II):
                    # 构造谓词列表：(op 映射到 iq_id 且 落在 slot 槽位)
                    predicates = []
                    for op in op_list:
                        cond = z3.And(
                            u_vars[op.trace_id] == iq_id,
                            t_vars[op.trace_id] % II == slot
                        )
                        predicates.append((cond, 1))
                    
                    label = f"mod_pble_{u_type}_{iq_id}_s{slot}"
                    # 使用 Pseudo-Boolean Le (<= 1) 约束
                    ResourceAxioms._add_constraint(
                        solver,
                        z3.PbLe(predicates, 1),
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
    def add_symmetry_breaking_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        对称性破缺公理：强制同类资源实例按顺序使用，消除等价排列产生的搜索空间。
        
        策略：对于资源类型 T，如果使用了实例 k，则必须有某个操作使用了实例 k-1。
        这确保了求解器不会跳过实例（例如只用 0 和 2 而不用 1），
        因为在完全对称的情况下，[0, 2] 与 [0, 1] 是等价的。
        """
        u_vars = kwargs.get("u_vars")
        if u_vars is None: return

        # 按类型分组操作
        type_to_ops: Dict[str, List[MIROp]] = {}
        for op in ops:
            u_type = ResourceAxioms._get_resource_type(op.op_type)
            # 只对已知具有物理连接对称性的类型进行破缺 (如 UR 端口)
            # 如果是 ALU，由于 RTOVR 物理绑定可能不对称，不建议开启。
            if u_type in ["UR_READ", "UR_WRITE"]:
                type_to_ops.setdefault(u_type, []).append(op)

        for u_type, op_list in type_to_ops.items():
            valid_iqs = spec.get_iq_ids(u_type)
            if len(valid_iqs) <= 1: continue

            # 对物理实例 ID 进行排序，建立序关系
            sorted_iqs = sorted(valid_iqs)
            
            for k in range(1, len(sorted_iqs)):
                iq_prev = sorted_iqs[k-1]
                iq_curr = sorted_iqs[k]
                
                # 谓词：是否有操作使用了当前实例 iq_curr
                used_curr = z3.Or([u_vars[op.trace_id] == iq_curr for op in op_list])
                # 谓词：是否有操作使用了前一个实例 iq_prev
                used_prev = z3.Or([u_vars[op.trace_id] == iq_prev for op in op_list])
                
                label = f"sym_break_{u_type}_iq{iq_curr}_needs_iq{iq_prev}"
                # 约束：If Used(iq_curr) Then Used(iq_prev)
                ResourceAxioms._add_constraint(
                    solver,
                    z3.Implies(used_curr, used_prev),
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
