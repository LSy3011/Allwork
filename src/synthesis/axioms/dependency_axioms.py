import z3
from typing import List, Dict, Any
from ir.mir.mir_op import MIROp
from api.hw_spec import HardwareSpec
from .base import AxiomBase

class DependencyAxioms(AxiomBase):
    """
    拓扑约束模块：处理 DFG 边权与时序顺序。
    """

    @staticmethod
    def add_latency_axiom(
        solver,
        ops: List[MIROp],
        t_vars: Dict[str, z3.ArithRef],
        II: int,
        spec: HardwareSpec,
        **kwargs
    ):
        """
        RAW 依赖公理（绝对时间）。
        确保消费者在生产者结果就绪后发射。
        """
        mode = kwargs.get("mode", "stable")
        for op in ops:
            for dep_id in op.metadata.get("depends_on", []):
                producer = next((o for o in ops if o.trace_id == dep_id), None)
                if not producer:
                    continue
                
                # 获取 Latency，V5 模式下严格检查
                op_type = producer.op_type.upper()
                if mode == "v5":
                    if op_type not in spec.delays:
                        raise KeyError(f"Critical SMT-V5 Error: Latency for '{op_type}' (Trace: {producer.trace_id}) missing in hardware spec!")
                    lat = spec.delays[op_type]
                else:
                    lat = spec.delays.get(op_type, 1)
                
                label = f"lat_{op.trace_id}_{dep_id}_{mode}"
                DependencyAxioms._add_constraint(
                    solver, t_vars[op.trace_id] >= t_vars[dep_id] + lat, label
                )

            # === RTOVR 增广边约束（Step 2）===
            latch_vars = kwargs.get("latch_vars")
            for prod_id, rtovr_info in op.metadata.get("rtovr_deps", {}).items():
                producer = next((o for o in ops if o.trace_id == prod_id), None)
                if not producer:
                    continue
                lat_p = spec.delays.get(producer.op_type.upper(), 1)
                # ADR-021 (fold-is-bug): SHIFTUP→RTOVR 边权 = SHIFTUP unit lat (=1)，与
                #   smc_id_en 无关。ADR-018 D3.b 曾对 SHIFTUP/SHIFTDN producer 做
                #   `lat_p += smc_id_en` 的 chain worst-case fold（标注 "safe
                #   over-approximation"），实测证明是 bug —— 它把 sim 的物理 chain 传播延迟
                #   错误塞进 SMT 逻辑调度，令所有 SMC 的 RTOVR 取错 iteration（fma 32-SMC 0/32）。
                #   chain 跨 SMC 的物理传播延迟曾由 emitter per-SMC INIT stagger 在 lowering 层补偿，
                #   不该在 SMT 层建模。删 fold 后 fma_parallel_n 32/32 bit-perfect。
                #   ⚠ ADR-023 已删该 stagger 机制（单 SMI 全局指令流 + sim 模型 A 同拍 broadcast
                #   fan-out 取代逐级 chain 延迟）；本 fold-deletion 结论不变，fma 现 8/8（PARAM_SMC_NUM=8）。
                #   ⚠ counter-verifier (ucode_solver.py validate_physical_schedule 维度6 RAW
                #   + 维度7 RTOVR) 已同步删除对应 fold，否则 axiom/verifier 不一致 →
                #   Synthesis failed。详见 ADR-021。
                # 🛸 [V6.0 LATCH OPTIMIZATION - HYBRID MODEL] 异步锁存与严格对齐混合建模
                # 创设虚拟锁存时间变量 t_latch
                t_latch = z3.Int(f"t_latch_{op.trace_id}_{prod_id}")
                if latch_vars is not None:
                    latch_vars[(op.trace_id, prod_id)] = t_latch
                
                # 1. 采样约束（producer 输出就绪后 RTOVR 采样到 t_latch）。
                # ── ADR-028 (user RTL 裁定 Q-a/Q-b, 2026-06-01)：DR_SHIFTUP_D 是**驻留**广播值，
                #   硬件支持 N 个 RTOVR 在**驻留窗口内不同拍**各采样一次（多列点积 = 时间维 scatter，
                #   Q-b），**无 1-拍瞬态覆盖**（区别普通 producer 的输出 1 拍后即被下一发射覆盖）。
                #   原 strict `t_latch == prod.t + lat` 把一个 SHIFTUP 的**全部消费者锁存钉死同一拍**
                #   → N 个 RTOVR 同拍写 FPMUL_S0 端口冲突 → 多列(cols_per_smc>1)必 UNSAT（ADR-026
                #   Stage-2a 张力真根因）。故对 broadcast SHIFTUP 放宽为**驻留窗口区间**：
                #   prod.t+lat <= t_latch <= prod.t+lat+II-1（窗口内任一拍采样，下个广播值落 dr_d 前）。
                #   普通 producer 仍严格 `==`（瞬态输出，必须当拍采样）。counter-verifier
                #   (ucode_solver 维度4) 本就用 undersample(>=)+overwrite(<prod.t+lat+II) 区间、非
                #   strict ==，故无需同步改。见 ADR-028 + hco 复核。
                prod_is_bcast_shiftup = (
                    producer.op_type.upper() == "SHIFTUP"
                    and producer.metadata.get("broadcast") == 1
                )
                if prod_is_bcast_shiftup:
                    DependencyAxioms._add_constraint(
                        solver, t_latch >= t_vars[prod_id] + lat_p,
                        f"rtovr_bcast_lo_{op.trace_id}_{prod_id}")
                    DependencyAxioms._add_constraint(
                        solver, t_latch <= t_vars[prod_id] + lat_p + (II - 1),
                        f"rtovr_bcast_hi_{op.trace_id}_{prod_id}")
                else:
                    DependencyAxioms._add_constraint(
                        solver, t_latch == t_vars[prod_id] + lat_p,
                        f"rtovr_strict_{op.trace_id}_{prod_id}")

                # 2. 无弹性缓冲消费者（乘法器/ALU/UR_WRITE/加法树）必须在锁存后**正好 1 拍**执行
                # （匹配 RtoVR 1-cycle latency）。此约束 per-consumer 恒严格——广播放宽的是「何时锁存」
                # （上方 t_latch 区间），不是「锁存后多久消费」。
                DependencyAxioms._add_constraint(
                    solver,
                    t_vars[op.trace_id] == t_latch + 1,
                    f"rtovr_latch_to_consumer_{op.trace_id}_{prod_id}"
                )
                
                # 3. 循环生命期安全约束（由于循环按 II 重复运行，本次迭代的消费者执行时刻必须在下一次 RTOVR 重写前完成）
                DependencyAxioms._add_constraint(
                    solver,
                    t_vars[op.trace_id] <= t_latch + II,
                    f"rtovr_latch_lifecycle_{op.trace_id}_{prod_id}"
                )

