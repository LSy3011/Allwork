from typing import List, Dict, Any
import z3
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec

class SynthesisAxioms:
    """定义驱动 SMT 合成的六大物理公理 (Z3 模型)"""

    @staticmethod
    def add_latency_axiom(solver: z3.Solver, ops: List[MIROp], 
                         t_vars: Dict[str, z3.ArithRef], spec: HardwareSpec):
        """
        公理 1: RAW 延迟约束。
        t_j >= t_i + latency_i (如果 op_j 依赖于 op_i 的结果)
        """
        for op in ops:
            # 这里的依赖分析后续由 HIR->MIR 转换器提供定义
            # 暂时通过 metadata 中的 'depends_on' 占位实现
            deps = op.metadata.get("depends_on", [])
            for dep_id in deps:
                # 查表获取上游节点的 latency
                # 注意：RtoVR 本身可能也有 1 拍延迟
                # 算子结果准备好后，经过 RtoVR 搬运到下一算子
                latency = op.metadata.get("dep_latency", 4) 
                solver.add(t_vars[op.node_id] >= t_vars[dep_id] + latency)

    @staticmethod
    def add_iq_mutex_axiom(solver: z3.Solver, ops: List[MIROp], 
                          t_vars: Dict[str, z3.ArithRef]):
        """
        公理 2: IQ 串行化约束。
        同一 IQ 在同一拍只能发射一条指令。
        """
        # 将相同 IQ 的操作分组合并
        iq_groups: Dict[int, List[str]] = {}
        for op in ops:
            iq_id = op.metadata.get("iq_id")
            if iq_id is not None:
                iq_groups.setdefault(iq_id, []).append(op.node_id)
        
        for iq_id, op_ids in iq_groups.items():
            if len(op_ids) > 1:
                # z3.Distinct 确保该 IQ 下的所有 T 不同
                solver.add(z3.Distinct([t_vars[oid] for oid in op_ids]))

    @staticmethod
    def add_bank_conflict_axiom(solver: z3.Solver, ops: List[MIROp], 
                               t_vars: Dict[str, z3.ArithRef]):
        """
        公理 3: UR Bank 读写冲突。
        - R-R 冲突: 同一 Bank 多个读必须串行化 (T_i != T_j)
        - R-W 冲突: 写必须在读之后 (T_w > T_r)
        """
        # 1. 识别读写动作与其所属 Bank
        bank_reads: Dict[int, List[str]] = {}  # bank_id -> [op_ids]
        bank_writes: Dict[int, List[str]] = {}
        
        for op in ops:
            rbanks = op.metadata.get("read_banks", [])
            wbanks = op.metadata.get("write_banks", [])
            for b in rbanks: bank_reads.setdefault(b, []).append(op.node_id)
            for b in wbanks: bank_writes.setdefault(b, []).append(op.node_id)

        # R-R 冲突
        for bank_id, r_op_ids in bank_reads.items():
            if len(r_op_ids) > 1:
                solver.add(z3.Distinct([t_vars[oid] for oid in r_op_ids]))
        
        # R-W 约束 (读先写后)
        for bank_id, r_op_ids in bank_reads.items():
            if bank_id in bank_writes:
                for r_id in r_op_ids:
                    for w_id in bank_writes[bank_id]:
                        solver.add(t_vars[w_id] > t_vars[r_id])

    @staticmethod
    def add_objective(solver: z3.Optimize, t_vars: Dict[str, z3.ArithRef]):
        """
        目标函数：最小化总周期。
        Minimize(Max(T_i))
        """
        max_t = z3.Int("max_t")
        for t in t_vars.values():
            solver.add(max_t >= t)
        solver.minimize(max_t)
