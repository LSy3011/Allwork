from typing import List, Dict, Optional, Set, Tuple
import copy
import z3
from ir.mir.mir_op import MIROp
import synthesis.axioms as axioms
from driver.hw_resource import ResourceType
from core.ii_analyzer import IIAnalyzer
from api.hw_spec import HardwareSpec


class SMTSynthesizer:
    """
    AIACC SMT 合成引擎 — 统一模调度版。

    模调度天然兼容串行：
      - 串行场景  => MII = MAX_II = 关键路径长度，II 固定，s = t（单次迭代）
      - 流水场景  => MII 由 IIAnalyzer 静态给出，MAX_II 为宽松上界，搜索最优 II

    不再区分 strategy，所有路径统一走 _solve_for_ii。
    """

    def __init__(self, hw_spec):
        self.hw_spec = hw_spec
        self.norm_delays = {k.upper(): v for k, v in hw_spec.delays.items()}
        self.res_to_iqs = self._build_resource_map(hw_spec)

    # ------------------------------------------------------------------
    # 资源池构建
    # ------------------------------------------------------------------
    def _build_resource_map(self, hw_spec) -> Dict[str, List[int]]:
        """根据硬件定义构建资源池，例如 UR_READ -> [30, 31, 32, 33]。"""
        res_map = {}
        registry = hw_spec.config.get("iq_registry", {})
        instances = hw_spec.config.get("resource_constraints", {}).get("unit_instances", {})

        default_r = hw_spec.config["resource_constraints"].get("total_read_ports", 1)
        default_w = hw_spec.config["resource_constraints"].get("total_write_ports", 1)

        r_count = instances.get("UR_READ", default_r)
        base_r = registry.get("UR_READ", 30)
        res_map["UR_READ"] = [base_r + i for i in range(r_count)]

        w_count = instances.get("UR_WRITE", default_w)
        base_w = registry.get("UR_WRITE", 35)
        res_map["UR_WRITE"] = [base_w + i for i in range(w_count)]

        for unit_type, count in instances.items():
            if unit_type in ["UR_READ", "UR_WRITE"]:
                continue
            base_id = registry.get(unit_type, 0)
            res_map[unit_type] = [base_id + i for i in range(count)]

        return res_map

    # ------------------------------------------------------------------
    # 主入口：统一模调度搜索
    # ------------------------------------------------------------------
    def solve(self, ops: List[MIROp], MII: int = 1, MAX_II: int = 32, **kwargs) -> Optional[Dict]:
        """
        统一模调度求解。

        Args:
            ops:    算子列表（DFG 节点）
            MII:    理论最小 II（由 IIAnalyzer 给出，或调用方提供）
            MAX_II: 搜索上界（保底）

        Returns:
            {"schedule": {op_id: cycle}, "II": int} 或 None（不可调度）

        Notes:
            串行调度等价于 MII = MAX_II = 关键路径周期数，
            此时模调度退化为单迭代，s_i = t_i，无需额外分支。
        """
        print(f"[SMT] Modulo scheduling {len(ops)} ops, MII={MII}, MAX_II={MAX_II}")

        best_ii = None
        best_schedule = None

        # 阶段一：混合二分搜索
        low, high = MII, MAX_II
        while low <= high:
            mid = (low + high) // 2
            print(f"[SMT]   Trying II={mid} ...", end=" ", flush=True)
            schedule = self._solve_for_ii(copy.deepcopy(ops), II=mid)
            if schedule is not None:
                print("SAT")
                best_ii = mid
                best_schedule = schedule
                high = mid - 1
            else:
                print("UNSAT")
                low = mid + 1

        # 阶段二：局部回溯（防非单调漏检，最多向下探 3 步）
        if best_ii is not None and best_ii > MII:
            fallback_limit = max(MII, best_ii - 3)
            print(f"[SMT]   Fallback probe [{best_ii - 1}, {fallback_limit}]")
            for fallback_ii in range(best_ii - 1, fallback_limit - 1, -1):
                print(f"[SMT]   Trying II={fallback_ii} ...", end=" ", flush=True)
                schedule = self._solve_for_ii(copy.deepcopy(ops), II=fallback_ii)
                if schedule is not None:
                    print("SAT (better found!)")
                    best_ii = fallback_ii
                    best_schedule = schedule
                else:
                    print("UNSAT")

        if best_schedule is None:
            print("[SMT] UNSAT for all II in range.")
            return None

        print(f"[SMT] Done. Best II = {best_ii}")
        
        # ── 后验验证 (Timing Verifier) ──────────────────────────────────
        verifier = TimingVerifier(self.hw_spec)
        verifier.verify(ops, best_schedule, best_ii)
        
        return {"schedule": best_schedule, "II": best_ii}

    # ------------------------------------------------------------------
    # 核心求解器：对固定 II 求 SAT
    # ------------------------------------------------------------------
    def _solve_for_ii(self, ops: List[MIROp], II: int) -> Optional[Dict[str, int]]:
        """
        对给定 II 建立 SMT 模型并求解。

        模调度变量：
          t[op]  — 绝对发射时间（整数，无界）
          s[op]  = t[op] mod II  — 槽位（0..II-1），用于互斥约束
          k[op]  = t[op] // II  — 迭代号（≥ 0）

        关键约束分组：
          1. 线性化：t = k*II + s，0 ≤ s < II，k ≥ 0
          2. RAW 依赖：基于绝对时间 t（跨迭代正确）
          3. 端口/资源互斥：基于模槽 s（同端口同槽位 → 冲突）
          4. 防覆盖：数据存活期（最后消费者 t - 数据就绪 t）≤ II
          5. 影子绑定：ALU ← RTOVR 时间窗口（1 ≤ delta ≤ 4）
        """
        solver = z3.Solver()

        nodes_by_id = {op.trace_id: op for op in ops}
        t = {op.trace_id: z3.Int(f"t_{op.trace_id}") for op in ops}
        s = {op.trace_id: z3.Int(f"s_{op.trace_id}") for op in ops}
        k = {op.trace_id: z3.Int(f"k_{op.trace_id}") for op in ops}

        registry = self.hw_spec.config.get("iq_registry", {})

        # ── 1. 模调度线性化 ──────────────────────────────────────────────
        for op_id in t:
            solver.add(t[op_id] == k[op_id] * II + s[op_id])
            solver.add(s[op_id] >= 0, s[op_id] < II)
            solver.add(k[op_id] >= 0)

        # ── 2. 动态物理端口分配（符号化 IQ）─────────────────────────────
        iq_vars = {}
        for op in ops:
            res_key = None
            if "iq_type" in op.metadata:
                res_meta = op.metadata["iq_type"]
                res_key = res_meta.name if hasattr(res_meta, "name") else str(res_meta)
            else:
                res_key = str(op.op_type).upper()

            valid_iqs = self.hw_spec.get_iq_ids(res_key)
            if valid_iqs:
                iv = z3.Int(f"iq_{op.trace_id}")
                iq_vars[op.trace_id] = iv
                solver.add(z3.Or([iv == iq_id for iq_id in valid_iqs]))

                if op.metadata.get("iq_id") is not None:
                    solver.add(iv == op.metadata["iq_id"])

            # UR_WRITE 根据前驱 RTOVR slot 反向约束
            base_w = registry.get("UR_WRITE", 35)
            if res_key == "UR_WRITE" and op.metadata.get("iq_id") is None:
                for dep_id in op.metadata.get("depends_on", []):
                    dep_node = nodes_by_id.get(dep_id)
                    if dep_node and dep_node.op_type.upper() == "RTOVR" \
                            and "slot_id" in dep_node.metadata:
                        slot = int(dep_node.metadata["slot_id"])
                        if base_w <= slot <= base_w + 3:
                            solver.add(iq_vars[op.trace_id] == slot)

        # ── 3. RAW 依赖（绝对时间）───────────────────────────────────────
        for op in ops:
            for dep_id in op.metadata.get("depends_on", []):
                if dep_id not in t:
                    continue
                dep_node = nodes_by_id[dep_id]
                delay = self.norm_delays.get(dep_node.op_type.upper(), 1)
                # UR_WRITE 后继需 2 拍缓冲，其余 transit=1
                transit = 2 if dep_node.op_type.upper() == "UR_WRITE" else 1
                solver.add(t[op.trace_id] >= t[dep_id] + (delay - 1) + transit)

        # ── 4. 资源互斥公理（基于模槽 s）────────────────────────────────
        axioms.add_resource_constraint(solver, ops, t, II, self.hw_spec, u_vars=iq_vars)
        axioms.add_iq_latch_protect_axiom(solver, ops, t, II, self.hw_spec, u_vars=iq_vars)
        axioms.add_rtovr_crossbar_axiom(solver, ops, t, II, self.hw_spec, u_vars=iq_vars)

        # ── 5. 防覆盖公理（数据存活期 ≤ II）────────────────────────────
        # [I1] 对此架构，启用 no_output_fifo=True，强制消费者在数据被覆盖前读取
        axioms.add_anti_overwrite_axiom(solver, ops, t, II, self.hw_spec)

        # ── 6. 影子绑定（RTOVR → ALU，窗口 [1, II-1] 拍，绝对时间）────────
        for op in ops:
            rtovr_deps = [
                d for d in op.metadata.get("depends_on", [])
                if d in nodes_by_id and nodes_by_id[d].op_type == "RTOVR"
            ]
            if rtovr_deps and op.op_type.upper() in [
                "FPMUL", "FPADD", "UR_WRITE", "LOGIC",
                "FPTOINT", "INTTOFP", "SHIFTUP", "SHIFTDN", "INTADD"
            ]:
                for rd in rtovr_deps:
                    solver.add(t[op.trace_id] >= t[rd] + 1)
                    # [A2] 上界应为 II - 1，因为数据在第 II 拍会被下一迭代覆盖
                    solver.add(t[op.trace_id] <= t[rd] + max(1, II - 1))

        # ── 7. 对称性破缺 (Symmetry Breaking) ─────────────────────────────
        # 对于相同类型的资源，如果在同一个 Slot 发射，强制其物理编号有序
        res_groups: Dict[str, List[MIROp]] = {}
        for op in ops:
            if op.trace_id in iq_vars:
                rtype = str(op.op_type).upper()
                res_groups.setdefault(rtype, []).append(op)
        
        for rtype, g_ops in res_groups.items():
            # 按 trace_id 排序以获得稳定约束
            sorted_ops = sorted(g_ops, key=lambda x: x.trace_id)
            for i in range(len(sorted_ops) - 1):
                o1, o2 = sorted_ops[i], sorted_ops[i+1]
                solver.add(z3.Implies(s[o1.trace_id] == s[o2.trace_id],
                                     iq_vars[o1.trace_id] < iq_vars[o2.trace_id]))

        # ── 求解 ────────────────────────────────────────────────────────
        if solver.check() != z3.sat:
            return None

        m = solver.model()

        # 回填物理 IQ 分配
        for op in ops:
            if op.trace_id in iq_vars:
                assigned_iq = m[iq_vars[op.trace_id]].as_long()
                op.metadata["iq_id"] = assigned_iq
                if op.op_type.upper() == "UR_WRITE":
                    for dep_id in op.metadata.get("depends_on", []):
                        dep_node = nodes_by_id.get(dep_id)
                        if dep_node and dep_node.op_type.upper() == "RTOVR":
                            dep_node.metadata["slot_id"] = assigned_iq

        return {op_id: m[t[op_id]].as_long() for op_id in t}

class TimingVerifier:
    """
    后验验证器：不使用 SMT，基于物理规则二次检查调度结果。
    这是最后的防线，拦截任何可能的 SMT 建模漏洞。
    支持 Spec-Driven 动态扩展。
    """
    def __init__(self, spec: HardwareSpec):
        self.spec = spec
        self.delays = spec.delays
        # 动态加载硬件限额
        self.constraints = spec.config.get("resource_constraints", {})
        self.unit_limits = self.constraints.get("unit_instances", {})
        self.max_reads = self.constraints.get("total_read_ports", 4)
        self.max_writes = self.constraints.get("total_write_ports", 4)

    def verify(self, ops: List[MIROp], schedule: Dict[str, int], II: int):
        nodes = {op.trace_id: op for op in ops}
        
        # 每一拍的资源统计矩阵
        # slot -> { resource_type: count }
        slot_res_usage: Dict[int, Dict[str, int]] = {}
        # slot -> set of iq_id (物理 ID 唯一性)
        slot_physical_registry: Dict[int, Set[int]] = {}
        # (slot, bank_id) -> trace_id
        bank_occupancy: Dict[Tuple[int, int], str] = {}

        for op in ops:
            t_p = schedule[op.trace_id]
            s_p = t_p % II
            res_type = op.op_type.upper()
            lat = self.spec.delays.get(res_type, 1)

            # 初始化该槽位的统计
            slot_res_usage.setdefault(s_p, {})
            slot_physical_registry.setdefault(s_p, set())

            # --- 1. 资源类限额动态验证 ---
            if res_type in self.unit_limits:
                slot_res_usage[s_p][res_type] = slot_res_usage[s_p].get(res_type, 0) + 1
                limit = self.unit_limits[res_type]
                if slot_res_usage[s_p][res_type] > limit:
                    raise RuntimeError(f"[Verifier] Resource Exhausted: {res_type} count "
                                     f"{slot_res_usage[s_p][res_type]} > limit {limit} at slot {s_p}")

            # --- 2. 物理 IQ ID 唯一性验证 ---
            iq_id = op.metadata.get("iq_id")
            if iq_id is not None:
                if iq_id in slot_physical_registry[s_p]:
                    raise RuntimeError(f"[Verifier] Physical ID Conflict: IQ {iq_id} "
                                     f"doubly assigned at slot {s_p}")
                slot_physical_registry[s_p].add(iq_id)

            # --- 3. Bank 互斥验证 ---
            bank_id = op.metadata.get("manual_bank")
            if bank_id is not None:
                bank_key = (s_p, bank_id)
                if bank_key in bank_occupancy:
                    prev_id = bank_occupancy[bank_key]
                    raise RuntimeError(f"[Verifier] Bank Conflict: Bank {bank_id} "
                                     f"contested by {prev_id} and {op.trace_id} at slot {s_p}")
                bank_occupancy[bank_key] = op.trace_id

            # --- 4. 端口带宽验证 ---
            if res_type == "UR_READ":
                slot_res_usage[s_p]["READ_BUS"] = slot_res_usage[s_p].get("READ_BUS", 0) + 1
                if slot_res_usage[s_p]["READ_BUS"] > self.max_reads:
                    raise RuntimeError(f"[Verifier] Read Bandwidth Error: slot {s_p} exceeds {self.max_reads}")

            if res_type == "UR_WRITE":
                slot_res_usage[s_p]["WRITE_BUS"] = slot_res_usage[s_p].get("WRITE_BUS", 0) + 1
                if slot_res_usage[s_p]["WRITE_BUS"] > self.max_writes:
                    raise RuntimeError(f"[Verifier] Write Bandwidth Error: slot {s_p} exceeds {self.max_writes}")

            # --- 5. 依赖验证 (RAW & Shadow Binding) ---
            consumers = [o for o in ops if op.trace_id in o.metadata.get("depends_on", [])]
            for cons in consumers:
                t_c = schedule[cons.trace_id]
                if t_c < t_p + lat:
                    raise RuntimeError(f"[Verifier] RAW Violation: {op.trace_id} -> {cons.trace_id} "
                                     f"(t_p={t_p}, t_c={t_c}, lat={lat})")
                
                # 数据存活窗口 [lat, lat + II - 1]
                # [算法优化] 如果 Producer 是循环不变量，则不触发此报错
                if not op.metadata.get("is_invariant", False) and t_c >= t_p + lat + II:
                    raise RuntimeError(f"[Verifier] Latch Overwrite: {op.trace_id} expired "
                                     f"before {cons.trace_id} read (II={II})")

            if res_type == "RTOVR":
                alu_cons = [o for o in consumers if o.op_type.upper() in ["FPMUL", "FPADD", "UR_WRITE"]]
                for alu in alu_cons:
                    t_alu = schedule[alu.trace_id]
                    upper_bound = max(1, II - 1)
                    if not (1 <= t_alu - t_p <= upper_bound):
                        raise RuntimeError(f"[Verifier] Shadow Binding Violation: {op.trace_id} -> {alu.trace_id} "
                                         f"interval {t_alu-t_p} not in [1, {upper_bound}]")
        
        print(f"[Verifier] Passed! II={II}, Nodes={len(ops)}, Conflicts=0. Bit-Perfect Ready.")
