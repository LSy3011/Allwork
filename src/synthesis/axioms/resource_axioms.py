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
    def get_remainder_vars(solver, tid, t_vars, II):
        if not hasattr(solver, "remainder_cache"):
            solver.remainder_cache = {}
        if (tid, II) not in solver.remainder_cache:
            q = z3.Int(f"q_{tid}")
            r = z3.Int(f"r_{tid}")
            solver.add(t_vars[tid] == q * II + r, desc=f"rem_eq_{tid}")
            solver.add(z3.And(r >= 0, r < II), desc=f"rem_bound_{tid}")
            solver.remainder_cache[(tid, II)] = (q, r)
        return solver.remainder_cache[(tid, II)]

    @staticmethod
    def add_resource_constraint(solver, ops, t_vars, II, spec, **kwargs):
        """
        通用资源约束入口。
        """
        ResourceAxioms.add_modulo_constraint(solver, ops, t_vars, II, spec, **kwargs)
        # 兼容旧版的其他资源约束
        ResourceAxioms.add_bank_conflict_axiom(solver, ops, t_vars, II, spec, **kwargs)
        ResourceAxioms.add_spill_reload_mutex_axiom(solver, ops, t_vars, II, spec, **kwargs)
        # ADR-018 v7 D3.d/D3.e：多 SMC chain 相关 axiom（N=1 / 非 chain 算子下 no-op）
        ResourceAxioms.add_broadcast_unique_source_axiom(solver, ops, t_vars, II, spec, **kwargs)
        ResourceAxioms.add_chain_iq_propagation_axiom(solver, ops, t_vars, II, spec, **kwargs)

    @staticmethod
    def add_modulo_constraint(solver, ops, t_vars, II, spec, **kwargs):
        """
        基础模冲突：同物理单元的操作必须落在不同的模槽。
        """
        u_vars = kwargs.get("u_vars")
        if u_vars is None:
            return

        # 按 (资源类型, smc_id) 分组操作（ADR-018 v7 D2：多 SMC key 扩展）
        # N=1 模式下所有 op smc_id=0 → 等价单 SMC 单维 key；多 SMC 下每 SMC ALU 独立 modulo 池
        type_to_ops: Dict[tuple, List[MIROp]] = {}
        for op in ops:
            u_type = ResourceAxioms._get_resource_type(op.op_type)
            smc_id = op.metadata.get('smc_id', 0)
            type_to_ops.setdefault((u_type, smc_id), []).append(op)

        for (u_type, smc_id), op_list in type_to_ops.items():
            if len(op_list) <= 1:
                continue

            # 🛸 [SMT-REFACTOR] 已经剥离了 RTOVR 的 SMT 物理搜索，直接放行
            if u_type == "RTOVR":
                continue
            
            # 过滤掉不存在 u_vars 里的游离节点
            op_list = [op for op in op_list if op.trace_id in u_vars]
            if len(op_list) <= 1:
                continue
            
            # 获取该类型的所有物理实例 ID
            valid_iqs = spec.get_iq_ids(u_type)
            if not valid_iqs:
                continue

            if II <= 6:
                # Pre-create and cache remainder variables once per op to avoid redundant constraint generation
                rem_vars = {}
                for op in op_list:
                    _, r = ResourceAxioms.get_remainder_vars(solver, op.trace_id, t_vars, II)
                    rem_vars[op.trace_id] = r

                for iq_id in valid_iqs:
                    # 对于每一个物理单元 iq_id，每一个模槽 slot，最多只能有一个操作映射上去
                    for slot in range(II):
                        # 构造谓词列表：(op 映射到 iq_id 且 落在 slot 槽位)
                        predicates = []
                        for op in op_list:
                            r = rem_vars[op.trace_id]
                            cond = z3.And(
                                u_vars[op.trace_id] == iq_id,
                                r == slot
                            )
                            predicates.append((cond, 1))
                        
                        label = f"mod_pble_{u_type}_smc{smc_id}_{iq_id}_s{slot}"
                        # 使用 Pseudo-Boolean Le (<= 1) 约束
                        ResourceAxioms._add_constraint(
                            solver,
                            z3.PbLe(predicates, 1),
                            label
                        )
            else:
                # 🛸 [SMT-REFACTOR Step 1.2] 高 II 时，切换到商余数分解与两两互斥
                q_vars = {}
                r_vars = {}
                for op in op_list:
                    tid = op.trace_id
                    q, r = ResourceAxioms.get_remainder_vars(solver, tid, t_vars, II)
                    q_vars[tid] = q
                    r_vars[tid] = r
                
                # 两两互斥：如果两个操作分配在同一个物理单元上，则它们的余数必然不同
                for i in range(len(op_list)):
                    for j in range(i + 1, len(op_list)):
                        op_i, op_j = op_list[i], op_list[j]
                        tid_i, tid_j = op_i.trace_id, op_j.trace_id
                        
                        label = f"mod_gap_{u_type}_smc{smc_id}_{tid_i}_{tid_j}"
                        ResourceAxioms._add_constraint(
                            solver,
                            z3.Implies(
                                u_vars[tid_i] == u_vars[tid_j],
                                r_vars[tid_i] != r_vars[tid_j]
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
    def add_bank_conflict_axiom(solver, ops=None, t_vars=None, II=None, spec=None, u_vars=None, **kwargs):
        """
        Bank 冲突公理：同一个 Bank 的相同操作类型（读或写）在 II 窗口内不得冲突。
        - 写操作（UR_WRITE）：在同 Bank 内必须严格执行模冲突互斥。
        - 读操作（UR_READ）：只有在逻辑地址不相同时，才需要执行模冲突互斥；相同地址支持广播读取。
        - 🚀 [HARDWARE ALIGNMENT] 显式豁免 stride == 0 的常量/参数读取。
        """
        # 兼容关键字/位置参数调用
        if ops is None:
            ops = kwargs.get("compute_ops", [])

        # 兼容旧签名 (solver, ops, t_vars, s_vars=None) 
        # 如果 II 是字典或 spec 为 None，说明是旧版调用，此时第四个参数为 s_vars
        is_legacy = False
        s_vars = None
        II_val = None
        if isinstance(II, dict) or spec is None:
            is_legacy = True
            s_vars = II
        else:
            II_val = II

        def get_op_bank_and_addr(op) -> tuple:
            bid = None
            addr = op.metadata.get("logical_addr")
            if op.metadata.get("manual_bank") is not None:
                bid = op.metadata["manual_bank"]
            elif op.metadata.get("bank_id") is not None:
                bid = op.metadata["bank_id"]
            elif addr is not None:
                depth = 256
                if spec is not None and hasattr(spec, "params"):
                    depth = getattr(spec.params, "PARAM_UR_BANK_DEPTH", 256)
                bid = addr // depth
            return bid, addr

        def get_mod_val_legacy(op):
            return s_vars[op.trace_id] if s_vars else t_vars[op.trace_id]

        # 按 (smc_id, Bank ID) 分组操作（ADR-018 v7 D2：多 SMC key 扩展）
        # ADR-009 errata: 跨 SMC 同 bank_id 同 addr 是不同物理 Bank，应 SAT 不冲突；
        # R-R 同地址豁免 / W↔R 无豁免规则均限于同 SMC 内成立。
        # N=1 模式所有 op smc_id=0 → 等价单 SMC 单维 key。
        bank_writes = {} # (smc_id, bank_id) -> List[MIROp]
        bank_reads = {}  # (smc_id, bank_id) -> List[MIROp]

        for op in ops:
            u_type = ResourceAxioms._get_resource_type(op.op_type)
            bid, addr = get_op_bank_and_addr(op)
            smc_id = op.metadata.get('smc_id', 0)
            if bid is not None:
                key = (smc_id, bid)
                if u_type == "UR_WRITE":
                    bank_writes.setdefault(key, []).append(op)
                elif u_type == "UR_READ":
                    bank_reads.setdefault(key, []).append(op)

        if is_legacy:
            # 1. 写冲突公理：同一个 Bank 的任意两个写操作在 II 内必然冲突（必须位于不同模槽）
            for (smc_id, bid), write_ops in bank_writes.items():
                if len(write_ops) > 1:
                    for i in range(len(write_ops)):
                        for j in range(i + 1, len(write_ops)):
                            op_i, op_j = write_ops[i], write_ops[j]
                            label = f"bank_gap_W_smc{smc_id}_{bid}_{op_i.trace_id}_{op_j.trace_id}"
                            ResourceAxioms._add_constraint(
                                solver, 
                                get_mod_val_legacy(op_i) != get_mod_val_legacy(op_j), 
                                label
                            )

            # 2. 读冲突公理：同一个 Bank 的两个读操作，只有当它们逻辑地址不同时，才会在 II 内冲突
            for (smc_id, bid), read_ops in bank_reads.items():
                if len(read_ops) > 1:
                    for i in range(len(read_ops)):
                        for j in range(i + 1, len(read_ops)):
                            op_i, op_j = read_ops[i], read_ops[j]
                            addr_i = op_i.metadata.get("logical_addr")
                            addr_j = op_j.metadata.get("logical_addr")
                            
                            should_conflict = True
                            if addr_i is not None and addr_j is not None:
                                should_conflict = (addr_i != addr_j)
                            
                            if should_conflict:
                                label = f"bank_gap_R_smc{smc_id}_{bid}_{op_i.trace_id}_{op_j.trace_id}"
                                ResourceAxioms._add_constraint(
                                    solver, 
                                    get_mod_val_legacy(op_i) != get_mod_val_legacy(op_j), 
                                    label
                                )
                            else:
                                # 相同物理地址：
                                label = f"bank_gap_R_same_smc{smc_id}_{bid}_{op_i.trace_id}_{op_j.trace_id}"
                                ResourceAxioms._add_constraint(
                                    solver,
                                    z3.Implies(
                                        t_vars[op_i.trace_id] != t_vars[op_j.trace_id],
                                        get_mod_val_legacy(op_i) != get_mod_val_legacy(op_j)
                                    ),
                                    label
                                )

            # 3. W↔R 跨类型冲突（ADR-009）：同 Bank 任意 (W, R) 对必须落在不同模槽
            # 无地址豁免：硬件无 forwarding（user 2026-05-15 确认）
            for (smc_id, bid) in set(bank_writes.keys()) | set(bank_reads.keys()):
                writes = bank_writes.get((smc_id, bid), [])
                reads = bank_reads.get((smc_id, bid), [])
                if not writes or not reads:
                    continue
                for op_w in writes:
                    for op_r in reads:
                        label = f"bank_gap_WR_smc{smc_id}_{bid}_{op_w.trace_id}_{op_r.trace_id}"
                        ResourceAxioms._add_constraint(
                            solver,
                            get_mod_val_legacy(op_w) != get_mod_val_legacy(op_r),
                            label
                        )
        else:
            # 🚀 [SMT-REFACTOR Step 1.2] 全局消除 % II 原生取模
            conflict_ops = []
            for ops_list in list(bank_writes.values()) + list(bank_reads.values()):
                conflict_ops.extend(ops_list)
            
            # 为这些节点建立 / 复用 Quotient-Remainder (商-余数) 辅助变量
            rem_vars = {}
            for op in set(conflict_ops):
                tid = op.trace_id
                _, r_var = ResourceAxioms.get_remainder_vars(solver, tid, t_vars, II_val)
                rem_vars[tid] = r_var
                
            def get_mod_val(op):
                return rem_vars[op.trace_id]
                
            for (smc_id, bid), write_ops in bank_writes.items():
                if len(write_ops) > 1:
                    for i in range(len(write_ops)):
                        for j in range(i + 1, len(write_ops)):
                            ResourceAxioms._add_constraint(
                                solver, get_mod_val(write_ops[i]) != get_mod_val(write_ops[j]), 
                                f"bank_gap_W_smc{smc_id}_{bid}_{write_ops[i].trace_id}_{write_ops[j].trace_id}"
                            )

            for (smc_id, bid), read_ops in bank_reads.items():
                if len(read_ops) > 1:
                    for i in range(len(read_ops)):
                        for j in range(i + 1, len(read_ops)):
                            addr_i = read_ops[i].metadata.get("logical_addr")
                            addr_j = read_ops[j].metadata.get("logical_addr")
                            if addr_i is None or addr_j is None or addr_i != addr_j:
                                ResourceAxioms._add_constraint(
                                    solver, get_mod_val(read_ops[i]) != get_mod_val(read_ops[j]), 
                                    f"bank_gap_R_smc{smc_id}_{bid}_{read_ops[i].trace_id}_{read_ops[j].trace_id}"
                                )
                            else:
                                # 🚀 [CROSS-ITERATION BANK CONFLICT SOLUTION]
                                # 相同物理 Bank 的相同逻辑地址读取，只有在物理时钟周期完全一致 (t_i == t_j) 时，才能共享/广播读取。
                                # 否则如果落在不同的物理周期 (t_i != t_j)，它们的模槽 (modulo slot) 必须严格互斥，防止跨迭代物理碰撞！
                                ResourceAxioms._add_constraint(
                                    solver,
                                    z3.Implies(
                                        t_vars[read_ops[i].trace_id] != t_vars[read_ops[j].trace_id],
                                        get_mod_val(read_ops[i]) != get_mod_val(read_ops[j])
                                    ),
                                    f"bank_gap_R_same_smc{smc_id}_{bid}_{read_ops[i].trace_id}_{read_ops[j].trace_id}"
                                )

            # 3. W↔R 跨类型冲突（ADR-009）：同 Bank 任意 (W, R) 对必须落在不同模槽
            # 无地址豁免：硬件无 forwarding（user 2026-05-15 确认）
            for (smc_id, bid) in set(bank_writes.keys()) | set(bank_reads.keys()):
                writes = bank_writes.get((smc_id, bid), [])
                reads = bank_reads.get((smc_id, bid), [])
                if not writes or not reads:
                    continue
                for op_w in writes:
                    for op_r in reads:
                        ResourceAxioms._add_constraint(
                            solver,
                            get_mod_val(op_w) != get_mod_val(op_r),
                            f"bank_gap_WR_smc{smc_id}_{bid}_{op_w.trace_id}_{op_r.trace_id}"
                        )

    @staticmethod
    def add_broadcast_unique_source_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        ADR-018 v7 D3.d (OQ-6 b 双层保险)：SHIFTUP broadcast 唯一源约束。
        At-Most-1 active broadcast=1 op per IQ_SHIFTUP cycle slot
        （emitter 复制是 lowering 阶段，不映射回多个 SMT 变量；本 axiom 守逻辑层 op 唯一）。
        N=1 / 非 broadcast 算子下 op_list 为空，no-op。
        """
        shiftup_bc_ops = [
            o for o in ops
            if o.op_type.upper() == 'SHIFTUP' and o.metadata.get('broadcast') == 1
        ]
        if len(shiftup_bc_ops) <= 1:
            return

        # 任意两条 broadcast SHIFTUP op 必须落在不同 modulo slot
        for i in range(len(shiftup_bc_ops)):
            for j in range(i + 1, len(shiftup_bc_ops)):
                op_i, op_j = shiftup_bc_ops[i], shiftup_bc_ops[j]
                _, r_i = ResourceAxioms.get_remainder_vars(solver, op_i.trace_id, t_vars, II)
                _, r_j = ResourceAxioms.get_remainder_vars(solver, op_j.trace_id, t_vars, II)
                label = f"bc_unique_{op_i.trace_id}_{op_j.trace_id}"
                ResourceAxioms._add_constraint(
                    solver,
                    r_i != r_j,
                    label
                )

    @staticmethod
    def add_chain_iq_propagation_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        ADR-018 v7 D3.e (OQ-2 + OQ-8 (i))：SHIFTUP/SHIFTDN chain 沿途 SMC 同步发射 IQ slot 占用。
        - 沿途 SMC_0..smc_id_en 在 t=op.t_issue 各占自己 chain IQ slot
        - 复用 modulo collision pool：key (chain_iq_type, smc_along_path, t%II) 至多 1 op
        - 当前 smc_id_en 是编译期常量（emitter 在 SMT solve 后填）；smc_id_en 是 SMT 变量场景留 Stage 2
        - N=1 / 非 chain 算子下 chain_ops 为空，no-op
        """
        from collections import defaultdict

        chain_ops = [
            o for o in ops
            if o.op_type.upper() in ('SHIFTUP', 'SHIFTDN')
        ]
        if not chain_ops:
            return

        # (chain_iq_type, smc_along_path) -> List[(op, r_var)]
        slot_pool: Dict[tuple, List[tuple]] = defaultdict(list)
        for op in chain_ops:
            smc_id_en = op.metadata.get('smc_id_en', 0)
            if not isinstance(smc_id_en, int):
                continue  # smc_id_en 是 SMT 变量场景留 Stage 2
            chain_type = op.op_type.upper()
            _, r_var = ResourceAxioms.get_remainder_vars(solver, op.trace_id, t_vars, II)
            for smc_k in range(smc_id_en + 1):
                slot_pool[(chain_type, smc_k)].append((op, r_var))

        # 同 (chain_type, smc_k) 池内任意两条 op 的 remainder 必须不同（同 cycle 不能两条 chain op 占同 SMC IQ）
        for (chain_type, smc_k), op_slot_list in slot_pool.items():
            if len(op_slot_list) <= 1:
                continue
            for i in range(len(op_slot_list)):
                for j in range(i + 1, len(op_slot_list)):
                    op_i, r_i = op_slot_list[i]
                    op_j, r_j = op_slot_list[j]
                    if op_i.trace_id == op_j.trace_id:
                        continue
                    label = f"chain_iq_{chain_type}_smc{smc_k}_{op_i.trace_id}_{op_j.trace_id}"
                    ResourceAxioms._add_constraint(
                        solver,
                        r_i != r_j,
                        label
                    )

    @staticmethod
    def add_spill_reload_mutex_axiom(solver, ops, t_vars, II, spec, **kwargs):
        """
        Spill/Reload 互斥：同 Spill 对应的多个 Reload 操作必须落在不同模槽。
        ADR-018 v7 D2: 按 (smc_id, spill_id) 分组——spill 落在 per-SMC 私有 Bank，
        跨 SMC 同 spill_id 是不同物理资源，独立。N=1 模式 smc_id=0 等价旧行为。
        """
        spill_groups: Dict[tuple, list] = {}
        for op in ops:
            if op.op_type.upper() == "UR_READ":
                smc_id = op.metadata.get('smc_id', 0)
                for d in op.metadata.get("depends_on", []):
                    if isinstance(d, str) and d.startswith("spill_"):
                        spill_groups.setdefault((smc_id, d), []).append(op)

        for (smc_id, spill_id), reloads in spill_groups.items():
            if len(reloads) <= 1:
                continue
            for i in range(len(reloads)):
                for j in range(i + 1, len(reloads)):
                    op_i, op_j = reloads[i], reloads[j]
                    label = f"spill_re_smc{smc_id}_{spill_id}_{op_i.trace_id}_{op_j.trace_id}"
                    ResourceAxioms._add_constraint(
                        solver,
                        t_vars[op_i.trace_id] % II != t_vars[op_j.trace_id] % II,
                        label
                    )

