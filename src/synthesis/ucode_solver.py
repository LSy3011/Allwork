import os
import time
import z3
from enum import Enum
from typing import List, Dict, Any, Optional, Tuple
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec
import synthesis.axioms as axioms


# ADR-011: tristate solver return semantics
class SolverStatus(Enum):
    SAT = "sat"
    UNSAT = "unsat"
    UNKNOWN = "unknown"


# ADR-011 constants
_TIMEOUT_PRIMARY_MS = 30_000   # 单次 z3 timeout（30s）
_TIMEOUT_RETRY_MS = 15_000     # 换 seed 重试 timeout（15s）
_SEED_PRIMARY = 0              # 主 seed（确定性）
_SEED_RETRY = 42               # 重试 seed（换搜索路径）
_TOTAL_BUDGET_S = int(os.environ.get("AIACC_SOLVER_BUDGET_S", "300"))
_DEBUG_SOLVER = os.environ.get("AIACC_SOLVER_DEBUG") == "1"


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
        🚀 [SAT-OPT-1] 线性扫描：从精确 MII 向上寻找第一个 SAT 且验证通过的方案。
        彻底解决非单调硬件约束下的二分查找漏检问题（例如 addcmul 的 II=2..4 为 SAT, II=5 为 UNSAT）。
        因为精确 MII 极准，所以通常第 1 次或第 2 次探测即 SAT，综合性能、稳定性和最优解质量均达到极致。
        """
        from core.ii_analyzer import IIAnalyzer
        computed_mii = IIAnalyzer(self.hw_spec).compute(ops)
        effective_mii = max(MII, computed_mii)
        
        # 保存初始 metadata 状态，防止被之前 SAT 的迭代污染
        original_meta = {op.trace_id: {"iq_id": op.metadata.get("iq_id"), "slot_id": op.metadata.get("slot_id")} for op in ops}
        
        best_res = None
        best_meta = None
        budget_start = time.perf_counter()

        for ii in range(effective_mii, MAX_II + 1):
            # ADR-011: total-budget cap; 病态 case 防卡死
            if time.perf_counter() - budget_start > _TOTAL_BUDGET_S:
                print(f"[BUDGET-EXHAUSTED] {_TOTAL_BUDGET_S}s exceeded at II={ii}; "
                      f"set AIACC_SOLVER_BUDGET_S=N to extend")
                break

            # 每次探测前恢复初始状态
            for op in ops:
                if original_meta[op.trace_id]["iq_id"] is None:
                    op.metadata.pop("iq_id", None)
                else:
                    op.metadata["iq_id"] = original_meta[op.trace_id]["iq_id"]

                if original_meta[op.trace_id]["slot_id"] is None:
                    op.metadata.pop("slot_id", None)
                else:
                    op.metadata["slot_id"] = original_meta[op.trace_id]["slot_id"]

            # ADR-011: 确定性 seed=0 主跑；UNKNOWN 时换 seed=42 重试一次（改搜索路径）
            print(f"[SOLVER] Trying II={ii}, seed={_SEED_PRIMARY}, timeout={_TIMEOUT_PRIMARY_MS}ms...")
            status, payload = self._solve_for_ii_with_units(
                ops, ii, timeout_ms=_TIMEOUT_PRIMARY_MS, random_seed=_SEED_PRIMARY,
            )
            if status == SolverStatus.UNKNOWN:
                print(f"[SOLVER]   UNKNOWN at seed={_SEED_PRIMARY}; retry seed={_SEED_RETRY}, timeout={_TIMEOUT_RETRY_MS}ms")
                status, payload = self._solve_for_ii_with_units(
                    ops, ii, timeout_ms=_TIMEOUT_RETRY_MS, random_seed=_SEED_RETRY,
                )

            if status == SolverStatus.SAT:
                schedule, unit_map = payload
                if self.validate_physical_schedule(ops, schedule, unit_map, ii):
                    print(f"[SOLVER] II={ii} is SAT and Verified. Found optimal II.")
                    best_res = {"schedule": schedule, "II": ii, "unit_assignments": unit_map}
                    best_meta = {op.trace_id: {"iq_id": op.metadata.get("iq_id"), "slot_id": op.metadata.get("slot_id")} for op in ops}
                    break
                else:
                    print(f"[VERIFIER] Counter-verification FAILED for II={ii}. Trying next.")
            elif status == SolverStatus.UNSAT:
                print(f"[SOLVER] II={ii} is UNSAT (proven). Trying next.")
            else:  # UNKNOWN after both seeds
                print(f"[SOLVER] II={ii} UNKNOWN after 2 seeds (seed=0 + seed=42); skipping")
                
        # 应用最佳的 metadata 结果
        if best_meta:
            for op in ops:
                if best_meta[op.trace_id]["iq_id"] is not None:
                    op.metadata["iq_id"] = best_meta[op.trace_id]["iq_id"]
                if best_meta[op.trace_id]["slot_id"] is not None:
                    op.metadata["slot_id"] = best_meta[op.trace_id]["slot_id"]
                    
        return best_res

    def validate_physical_schedule(self, ops: List[MIROp], schedule: Dict[str, int], unit_map: Dict[str, str], II: int) -> bool:
        """
        全物理维度严格反证审计（ADR-017 合并 TimingVerifier 后总入口）。

        ⚠️ CALLER MUST CHECK RETURN VALUE
        失败仅 print + return False，不抛 RuntimeError。caller 必须显式分流。
        现有 caller：solve_with_units（ucode_solver.py:94）已正确 `if validate(...):`。

        校验维度（return False 早退，按顺序）：
          1. Resource limit per slot（unit_instances）—— 含 RTOVR=64
          2. Physical IQ ID uniqueness per slot —— op.metadata.iq_id
          3. Bank `manual_bank` 冲突 —— 三段精确（ADR-009）：
             W-W 互斥 / R-R 同地址豁免、异地址互斥 / W↔R 无豁免互斥
          4. Read/Write port bandwidth（total_read_ports / total_write_ports）
          5. Modulo Collision by iq_name（与 axiom 维度互补）
          6. RAW + Latch overwrite（compute-to-compute 路径）
          7. RTOVR undersampling + overwrite（RTOVR-routed 路径，原有）
        """
        nodes_by_id = {op.trace_id: op for op in ops}
        iq_to_modulo: Dict[str, set] = {}
        slot_iq_ids: Dict[int, set] = {}
        slot_res_usage: Dict[int, Dict[str, int]] = {}
        # bank 三段累积：以 (s, bank_id) 索引
        slot_bank_writes: Dict[Tuple[int, int], List[str]] = {}
        slot_bank_reads: Dict[Tuple[int, int], List[Tuple[str, Any]]] = {}  # (trace_id, logical_addr)

        constraints = self.hw_spec.config.get("resource_constraints", {})
        unit_limits = constraints.get("unit_instances", {})
        max_reads = constraints.get("total_read_ports", 4)
        max_writes = constraints.get("total_write_ports", 4)

        for op in ops:
            t = schedule.get(op.trace_id)
            if t is None: continue
            s = t % II
            res_type = op.op_type.upper()
            slot_res_usage.setdefault(s, {})

            # 1. Resource limit per slot (含 RTOVR=64)
            if res_type in unit_limits:
                slot_res_usage[s][res_type] = slot_res_usage[s].get(res_type, 0) + 1
                if slot_res_usage[s][res_type] > unit_limits[res_type]:
                    print(f"[VERIFIER-FAIL] Resource Exhausted: {res_type} count "
                          f"{slot_res_usage[s][res_type]} > limit {unit_limits[res_type]} at slot {s}")
                    return False

            # 2. Physical IQ ID uniqueness per slot
            iq_id = op.metadata.get("iq_id")
            if iq_id is not None:
                slot_iq_ids.setdefault(s, set())
                if iq_id in slot_iq_ids[s]:
                    print(f"[VERIFIER-FAIL] Physical ID Conflict: IQ {iq_id} doubly assigned at slot {s}")
                    return False
                slot_iq_ids[s].add(iq_id)

            # 3. Bank manual_bank 三段精确语义（ADR-009）
            bank_id = op.metadata.get("manual_bank")
            if bank_id is not None:
                addr = op.metadata.get("logical_addr")
                key = (s, bank_id)
                if res_type == "UR_WRITE":
                    # W-W：同 (slot, bank) 任意两 W → fail
                    if slot_bank_writes.get(key):
                        prev_w = slot_bank_writes[key][0]
                        print(f"[VERIFIER-FAIL] Bank W-W Conflict: bank {bank_id} slot {s} "
                              f"contested by {prev_w} and {op.trace_id}")
                        return False
                    # W↔R：同 (slot, bank) 已有 R → fail（无地址豁免）
                    if slot_bank_reads.get(key):
                        prev_r, _ = slot_bank_reads[key][0]
                        print(f"[VERIFIER-FAIL] Bank W↔R Conflict: bank {bank_id} slot {s} "
                              f"W={op.trace_id} R={prev_r} (no forwarding, ADR-009)")
                        return False
                    slot_bank_writes.setdefault(key, []).append(op.trace_id)
                elif res_type == "UR_READ":
                    # W↔R：同 (slot, bank) 已有 W → fail
                    if slot_bank_writes.get(key):
                        prev_w = slot_bank_writes[key][0]
                        print(f"[VERIFIER-FAIL] Bank W↔R Conflict: bank {bank_id} slot {s} "
                              f"R={op.trace_id} W={prev_w}")
                        return False
                    # R-R：同地址豁免，异地址互斥
                    for prev_r, prev_addr in slot_bank_reads.get(key, []):
                        if addr is None or prev_addr is None or addr != prev_addr:
                            print(f"[VERIFIER-FAIL] Bank R-R Conflict: bank {bank_id} slot {s} "
                                  f"addrs {prev_addr} vs {addr} ({prev_r}, {op.trace_id})")
                            return False
                    slot_bank_reads.setdefault(key, []).append((op.trace_id, addr))

            # 4. Read/Write port bandwidth
            if res_type == "UR_READ":
                slot_res_usage[s]["READ_BUS"] = slot_res_usage[s].get("READ_BUS", 0) + 1
                if slot_res_usage[s]["READ_BUS"] > max_reads:
                    print(f"[VERIFIER-FAIL] Read Bandwidth: slot {s} count "
                          f"{slot_res_usage[s]['READ_BUS']} > {max_reads}")
                    return False
            elif res_type == "UR_WRITE":
                slot_res_usage[s]["WRITE_BUS"] = slot_res_usage[s].get("WRITE_BUS", 0) + 1
                if slot_res_usage[s]["WRITE_BUS"] > max_writes:
                    print(f"[VERIFIER-FAIL] Write Bandwidth: slot {s} count "
                          f"{slot_res_usage[s]['WRITE_BUS']} > {max_writes}")
                    return False

            # ↓↓↓ 以下校验 RTOVR op 跳过（modulo by iq_name + RAW 仅 compute 路径）
            if res_type == "RTOVR":
                continue

            iq_name = unit_map.get(op.trace_id)
            if iq_name:
                # 5. Modulo Collision by iq_name
                if s in iq_to_modulo.get(iq_name, set()):
                    print(f"[VERIFIER-FAIL] Modulo Collision in {iq_name} at Slot {s}")
                    return False
                iq_to_modulo.setdefault(iq_name, set()).add(s)

            # 6. RAW + Latch overwrite（compute-to-compute 路径）
            lat = self.hw_spec.delays.get(res_type, 1)
            # ADR-021 (fold-is-bug): 删除 SHIFTUP/SHIFTDN chain worst-case fold。
            # 与 dependency_axioms 同步——边权 = unit lat，chain 物理延迟由 emitter
            # per-SMC INIT shift 补偿，不在此处建模。(原 ADR-018 D3.b `lat += smc_id_en`
            # 是 bug，详见 ADR-021。verifier 与 axiom 必须一致，否则 Synthesis failed。)
            consumers = [o for o in ops if op.trace_id in o.metadata.get("depends_on", [])]
            for cons in consumers:
                t_c = schedule.get(cons.trace_id)
                if t_c is None: continue
                if t_c < t + lat:
                    print(f"[VERIFIER-FAIL] RAW Violation: {op.trace_id} -> {cons.trace_id} "
                          f"(t_p={t}, t_c={t_c}, lat={lat})")
                    return False
                is_inv = (op.metadata.get("is_invariant", False) or
                          op.metadata.get("is_const", False) or
                          op.metadata.get("stride", 1) == 0 or
                          res_type == "UR_WRITE")
                if not is_inv and t_c >= t + lat + II:
                    print(f"[VERIFIER-FAIL] Latch Overwrite: {op.trace_id} expired "
                          f"before {cons.trace_id} read (II={II})")
                    return False
                
        # 4. [时序审计] 生产 -> RTOVR 采样 -> ALU 消费 严格对齐
        for op in ops:
            if op.op_type.upper() == "RTOVR": continue
            m_type = op.op_type.upper()
            deps = op.metadata.get("depends_on", [])
            
            # [OPT Step3] RTOVR 已抽象为边，从消费者反推采样时刻
            for prod_id, rtovr_info in op.metadata.get("rtovr_deps", {}).items():
                prod = nodes_by_id.get(prod_id)
                if not prod:
                    continue
                lat_p = self.hw_spec.delays.get(prod.op_type.upper(), 1)
                # ADR-021 (fold-is-bug): 删除 SHIFTUP/SHIFTDN chain worst-case fold
                # (原 ADR-018 D3.b `lat_p += smc_id_en`)。边权 = unit lat，chain 物理延迟
                # 由 emitter per-SMC INIT shift 补偿。与 dependency_axioms / 维度6 同步。
                t_prod = schedule.get(prod_id)
                if t_prod is None: continue
                t_consumer = schedule.get(op.trace_id)
                if t_consumer is None: continue
                
                # 🛸 [V6.0 LATCH OPTIMIZATION] 优先从 schedule 中获取真实的 RTOVR 调度时间，降级退化到 t_consumer - 1
                rtovr_node = None
                for dep_id in deps:
                    node = nodes_by_id.get(dep_id)
                    if node and node.op_type.upper() == "RTOVR":
                        if prod_id in node.metadata.get("depends_on", []):
                            rtovr_node = node
                            break
                
                if rtovr_node and schedule.get(rtovr_node.trace_id) is not None:
                    t_rtovr_inferred = schedule[rtovr_node.trace_id]
                else:
                    t_rtovr_inferred = t_consumer - 1
                if t_rtovr_inferred < t_prod + lat_p:
                    print(f"[VERIFIER-FAIL] RTOVR undersampling: {op.trace_id} reads before {prod_id} ready "
                          f"(inferred t_rtovr={t_rtovr_inferred}, need >= {t_prod + lat_p})")
                    return False
                
                # 🛸 [BUG FIX] 修正物理覆盖上限公式：
                # 锁存器仅在下一次迭代输出完成时（t_prod + lat_p + II）才被覆盖。
                # 且如果生产者是常量、不变量、stride=0 访存或持久化写入，则免除覆盖检查。
                is_inv = (prod.metadata.get("is_invariant", False) or 
                          prod.metadata.get("is_const", False) or 
                          prod.metadata.get("stride", 1) == 0 or
                          "UR_WRITE" in prod.op_type.upper())
                if not is_inv:
                    limit = t_prod + lat_p + II
                    if t_rtovr_inferred >= limit:
                        print(f"[VERIFIER-FAIL] RTOVR overwrite: {op.trace_id} reads after {prod_id} latch overwritten "
                              f"(inferred t_rtovr={t_rtovr_inferred}, limit < {limit})")
                        return False
                            
        return True

    def _solve_for_ii_with_units(
        self,
        ops: List[MIROp],
        II: int,
        timeout_ms: int = _TIMEOUT_PRIMARY_MS,
        random_seed: int = _SEED_PRIMARY,
    ) -> Tuple[SolverStatus, Optional[Tuple[Dict[str, int], Dict[str, str]]]]:
        """ADR-011: tristate 返回。每次调用全新建 Solver——禁止外部复用。
        random_seed 强制确定性；外层用 seed=0 主跑、seed=42 重试以换搜索路径。
        """
        compute_ops = [op for op in ops if op.op_type.upper() != "RTOVR"]

        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        solver.set("random_seed", random_seed)  # Phase 1：确定性
        # ADR-011 Phase 4：使用 arith.solver=2（老 Bellman-Ford diff-logic 实现）
        # 实测 horner e2e：default(arith.solver=6)=53s vs arith.solver=2=11s。
        # 模调度大量约束形如 t[i] - t[j] >= c，是差分逻辑 special shape，
        # diff-logic solver 多项式时间；通用 simplex 在此结构上走慢路径。
        solver.set("smt.arith.solver", 2)
        # Phase 3：生产路径用 z3 原生 solver.add；assert_and_track 仅 DEBUG 模式
        # tracked_add 给每条约束创 Bool label 用于 unsat_core，纯调试 overhead。
        if _DEBUG_SOLVER:
            _track_idx = 0
            def tracked_add(*args, **kwargs):
                nonlocal _track_idx
                desc = kwargs.pop("desc", "")
                if desc:
                    desc = "_" + desc.replace(" ", "_").replace("=", "eq")
                for arg in args:
                    _track_idx += 1
                    lbl = z3.Bool(f"lbl_{_track_idx}{desc}")
                    solver.assert_and_track(arg, lbl)
            solver.add = tracked_add
        else:
            _orig_add = solver.add
            def native_add(*args, **kwargs):
                kwargs.pop("desc", None)  # 忽略 desc 仅为兼容签名
                _orig_add(*args, **kwargs)
            solver.add = native_add
        
        t_vars = {op.trace_id: z3.Int(f"t_{op.trace_id}") for op in compute_ops}
        u_vars = {op.trace_id: z3.Int(f"u_{op.trace_id}") for op in compute_ops}

        # 1. 基础物理约束
        for op in compute_ops:
            # 🛸 [V27.1 REVERT] 回归 t >= 0。依靠 L_init=1 来提供物理启动缓冲。
            solver.add(t_vars[op.trace_id] >= 0, desc=f"t_pos_{op.trace_id}")
            u_type = self._get_resource_type(op.op_type)
            valid_iqs = self.res_to_iqs.get(u_type, [0])
            # 恢复：UR 单元可以在同类池中自由选择 (ur-w0, 1, 2, 3 均可)
            solver.add(z3.Or([u_vars[op.trace_id] == iq_id for iq_id in valid_iqs]), desc=f"valid_iq_{op.trace_id}")

        # 2. 物理公理
        latch_vars = {}
        axioms.add_latency_axiom(solver, compute_ops, t_vars, II, self.hw_spec, latch_vars=latch_vars)

        # ── 2.1. ADR-006 latch lifetime 上界（ADR-016 从 backend.py 移植）─────
        # 覆盖**所有**非 invariant/UR_WRITE 的直接 RAW 边（含 compute → compute）
        # add_latency_axiom 内的 rtovr_latch_lifecycle 只覆盖 RTOVR 路径；
        # 这里补全跨 compute 节点的直接依赖。e2e 真实 DFG 大多走 RTOVR，本约束触发少；
        # 反证测试 R3/R4/R5 依赖此约束精确推出 II>=17。
        nodes_by_id = {op.trace_id: op for op in compute_ops}
        for op in compute_ops:
            for dep_id in op.metadata.get("depends_on", []):
                if dep_id not in t_vars:
                    continue  # RTOVR 节点已被 compute_ops 过滤
                dep = nodes_by_id[dep_id]
                delay = self.hw_spec.delays.get(dep.op_type.upper(), 1)
                is_inv = (dep.metadata.get("is_invariant", False) or
                          dep.metadata.get("is_const", False) or
                          dep.metadata.get("stride", 1) == 0 or
                          dep.op_type.upper() == "UR_WRITE")
                if not is_inv:
                    solver.add(t_vars[op.trace_id] < t_vars[dep_id] + delay + II,
                               desc=f"latch_ub_{op.trace_id}_{dep_id}")

        # ── 2.5. 通用循环携带依赖约束 (Generic Loop-Carried Dependency Constraints) ──
        for op in compute_ops:
            for dep_id, latency in op.metadata.get("loop_carried_depends_on", {}).items():
                if dep_id in t_vars:
                    print(f"[SMT] Adding loop-carried constraint from DFG: {op.trace_id} + II >= {dep_id} + {latency}")
                    solver.add(t_vars[op.trace_id] + II >= t_vars[dep_id] + latency, desc=f"loop_carried_{op.trace_id}_{dep_id}")

        # axioms.add_rtovr_crossbar_axiom(solver, compute_ops, t_vars, II, self.hw_spec, u_vars=u_vars)
        # axioms.add_shadow_binding_axiom(solver, compute_ops, t_vars, II, self.hw_spec)
        # axioms.add_anti_overwrite_axiom(solver, compute_ops, t_vars, II, self.hw_spec)
        axioms.add_side_effect_phase_axiom(solver, compute_ops, t_vars, II, self.hw_spec, u_vars=u_vars)
        # axioms.add_rtovr_alignment_axiom(solver, compute_ops, t_vars, II, self.hw_spec)


        # 3. 模冲突
        axioms.add_resource_constraint(solver, compute_ops, t_vars, II, self.hw_spec, u_vars=u_vars)
        # axioms.add_rtovr_modulo_constraint(solver, ops, t_vars, II, self.hw_spec, u_vars=u_vars, latch_vars=latch_vars)
        # axioms.add_rtovr_liveness_axiom(solver, ops, t_vars, II, self.hw_spec, u_vars=u_vars, latch_vars=latch_vars)

        # 4. RtoVR 物理绑定
        # axioms.add_physical_binding_axiom(solver, compute_ops, t_vars, II, self.hw_spec, u_vars=u_vars)
        # 5. 手动 IQ 绑定 (供调试使用)
        for op in compute_ops:
            manual_iq = op.metadata.get("iq_id")
            if manual_iq is not None:
                solver.add(u_vars[op.trace_id] == manual_iq, desc=f"manual_iq_{op.trace_id}")

        # ADR-011: tristate 分流
        res = solver.check()
        if res == z3.unsat:
            if _DEBUG_SOLVER:
                try:
                    core = solver.unsat_core()
                    print(f"[UNSAT-CORE] ({len(core)} constraints): {[str(c) for c in core]}", flush=True)
                except Exception as _e:
                    print(f"[UNSAT-CORE] unavailable: {_e}", flush=True)
            return (SolverStatus.UNSAT, None)
        if res != z3.sat:
            # z3.unknown — 不调 unsat_core（ADR-011 阻断项：unknown 时该 API 行为未定义）
            return (SolverStatus.UNKNOWN, None)
        model = solver.model()
        schedule = {op.trace_id: model.eval(t_vars[op.trace_id]).as_long() for op in compute_ops}
        
        # 🛸 [V6.0 LATCH OPTIMIZATION] 评估并提取求解出的虚拟锁存器 RTOVR 调度时间
        latch_schedule = {}
        if latch_vars:
            for key, var in latch_vars.items():
                latch_val = model.eval(var)
                if hasattr(latch_val, "as_long"):
                    latch_schedule[key] = latch_val.as_long()
        registry = self.hw_spec.config.get("iq_registry", {})
        
        # 🛸 [V5.3 PATCH] 动态获取基地址，移除硬编码 100
        base_rt = registry.get("RTOVR", 100) 
        
        unit_assignments = {}
        for op in compute_ops:
            u_id = model.eval(u_vars[op.trace_id]).as_long()
            iq_name = self.hw_spec.get_iq_name(u_id, op.op_type)
            unit_assignments[op.trace_id] = iq_name
            op.metadata["iq_id"] = u_id

        # [OPT Step 3] Post-hoc 恢复 RTOVR 节点信息
        dvr_map = self.hw_spec.get_dvr_map()
        for op in compute_ops:
            u_type = self._get_resource_type(op.op_type)
            u_id = op.metadata["iq_id"]
            
            # 🛸 [ENGINEERING] 修正模块信息查找路径
            unit_info = self.hw_spec.modules.get(u_type, {})
            if hasattr(unit_info, "model_dump"): unit_info = unit_info.model_dump()
            port_names = [p.split("-")[-1] for p in unit_info.get("inputs", [])]
            if not port_names:
                port_names = ["S0", "S1", "S2", "S3", "ST"]
            
            valid_iqs = self.res_to_iqs.get(u_type, [0])
            idx = valid_iqs.index(u_id) if u_id in valid_iqs else 0
            inst_name = f"{u_type}_{idx}" if len(valid_iqs) > 1 else u_type
            
            deps = op.metadata.get("depends_on", [])
            for i, dep_id in enumerate(deps):
                producer = next((o for o in ops if o.trace_id == dep_id), None)
                if producer and producer.op_type.upper() == "RTOVR":
                    # 恢复时间：优先使用 SMT 求解的 latch_schedule，否则降级回退到 t_consumer - 1
                    prod_id = producer.metadata.get("depends_on", [None])[0]
                    latch_time = latch_schedule.get((op.trace_id, prod_id))
                    if latch_time is not None:
                        schedule[producer.trace_id] = latch_time
                    else:
                        schedule[producer.trace_id] = schedule[op.trace_id] - 1
                    
                    # 恢复物理绑定。dvr_map[inst][port_names[i]] 索引法优先（FPMUL/FPADD 等：消费者
                    # depends_on[i] 对齐输入端口 i、端口名经 split('-') 可取，dvr_map 解析正确——勿动，
                    # 否则破 hardsigmoid 等已 work 的 RTOVR 绑定）。
                    p_name = port_names[i] if i < len(port_names) else "S0"
                    if inst_name in dvr_map and p_name in dvr_map[inst_name]:
                        target_slot = dvr_map[inst_name][p_name]
                    else:
                        # ADR-036 契约修复：索引法失败时回退节点显式 slot_id。SHIFTDN 不满足索引法前提
                        # （DVR_SHIFTDN_S 是第 2 输入 + modules 用下划线 + ds_gather 单 dep i=0）→ 旧版误算
                        # slot 0（rtovr-0）；rt_ds_src 带 slot_id=PS.SHIFTDN_S → 取 slot 34（rtovr-34 = DVR_SHIFTDN_S）。
                        explicit_slot = producer.metadata.get("slot_id")
                        if explicit_slot is not None and not isinstance(explicit_slot, int):
                            explicit_slot = getattr(explicit_slot, "value", None)
                        target_slot = explicit_slot if isinstance(explicit_slot, int) else 0

                    rt_id = base_rt + target_slot
                    
                    # 🛸 [ENGINEERING] 多端口生产者解析：根据 dep_port 找到正确的 Source ID
                    # 查找该 RTOVR 节点对应生产者的哪个端口 (D 或 ST)
                    dep_port = producer.metadata.get("dep_port", "D")
                    dr_map = self.hw_spec.get_dr_map() # Unit -> Port -> ID
                    
                    # 获取真实的生产者节点（不是 RTOVR，而是 RTOVR 的输入源）
                    # 注意：在 DFG 中，RTOVR 节点只有一个 dependency，即真正的生产者
                    real_producer_id = producer.metadata.get("depends_on", [None])[0]
                    real_producer = next((o for o in ops if o.trace_id == real_producer_id), None)
                    real_producer_iq_id = real_producer.metadata.get("iq_id", u_id) if real_producer else u_id
                    
                    # 根据物理 ID 反查实例名，用于 dr_map 匹配 (例如 "UR_READ_0")
                    real_producer_inst = self.hw_spec.get_iq_name(real_producer_iq_id).upper().replace("-", "_")
                    
                    # 查找优先级: 实例级(UR_READ_0) > 类型级(UR_READ) > 默认(real_producer_iq_id)
                    sel_id = dr_map.get(real_producer_inst, dr_map.get(real_producer.op_type.upper() if real_producer else "", {})).get(dep_port, real_producer_iq_id)
                    
                    unit_assignments[producer.trace_id] = self.hw_spec.get_iq_name(rt_id, "RTOVR")
                    producer.metadata["iq_id"] = rt_id
                    producer.metadata["slot_id"] = target_slot
                    producer.metadata["sel"] = sel_id

        return (SolverStatus.SAT, (schedule, unit_assignments))
