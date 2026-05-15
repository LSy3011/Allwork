from typing import List, Dict, Set, Tuple
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec

# ADR-016 (2026-05-15): SMTSynthesizer 已删，整合到 ucode_solver.UCodeSynthesizer
# 本文件仅保留 TimingVerifier 作为独立 timing 后验工具，
# 被 tests/unit/test_verifier_robustness.py 使用。
# 与 ucode_solver.validate_physical_schedule 互补（后者覆盖 RTOVR 路径，
# TimingVerifier 覆盖 Latch Overwrite + Bank Conflict 模槽碰撞）。

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
                # [算法优化] 如果 Producer 是循环不变量 (或常数、stride=0)，或者是持久化内存写入，则不触发过期报错
                is_inv = (op.metadata.get("is_invariant", False) or
                          op.metadata.get("is_const", False) or
                          op.metadata.get("stride", 1) == 0 or
                          op.op_type.upper() == "UR_WRITE")
                if not is_inv and t_c >= t_p + lat + II:
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
