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
                # ADR-018 v7 D3.b + ADR-020 v6 P1 (语义订正):
                #   下面 fold 的 lat_p = SMC_{smc_id_en} 数据到达 RTOVR 的 worst-case
                #   latency 边界（含 chain 累加），**不是 SHIFTUP 自身的 unit lat**
                #   （SHIFTUP unit lat = 1，见 hw_config/pipeline_delays.yaml）。
                #   SMT 求解器按此 worst-case 算单 t_var，是 safe over-approximation。
                #   emitter post-stagger (src/driver/emitter.py) 按 SMC_k 减
                #   delta_k = (smc_id_en - k) × chain_stage_latency 修正实际 fire cycle:
                #     SMC_0           fire @ fold_abs_t - smc_id_en × chain_stage_latency
                #     SMC_{smc_id_en} fire @ fold_abs_t（与 fold 一致）
                #   compiler_schedule.json 中 SHIFTUP→RTOVR 边权数值勿误判为 SHIFTUP unit lat。
                #   详见 ADR-020 v6 §HCO OQ#1 复核 + ADR-018 D3.b errata。
                if producer.op_type.upper() in ('SHIFTUP', 'SHIFTDN'):
                    smc_id_en = producer.metadata.get('smc_id_en', 0)
                    if isinstance(smc_id_en, int) and smc_id_en > 0:
                        chain_stage_latency = 1  # ADR-018 D3.b
                        lat_p += smc_id_en * chain_stage_latency
                # 🛸 [V6.0 LATCH OPTIMIZATION - HYBRID MODEL] 异步锁存与严格对齐混合建模
                # 创设虚拟锁存时间变量 t_latch
                t_latch = z3.Int(f"t_latch_{op.trace_id}_{prod_id}")
                if latch_vars is not None:
                    latch_vars[(op.trace_id, prod_id)] = t_latch
                
                # 1. 严格采样约束（生产者完成输出的时刻，RTOVR 必须在 1 拍内立即采样，防寄存器覆盖溢出）
                DependencyAxioms._add_constraint(
                    solver,
                    t_latch == t_vars[prod_id] + lat_p,
                    f"rtovr_strict_{op.trace_id}_{prod_id}"
                )
                
                # Multipliers, ALUs, UR_WRITE, and addition trees do not have elastic buffers, 
                # so they must be executed in the cycle immediately following latching (strict equality == t_latch + 1)
                # to match RtoVR's 1-cycle latency.
                latch_cond = (t_vars[op.trace_id] == t_latch + 1)

                DependencyAxioms._add_constraint(
                    solver,
                    latch_cond,
                    f"rtovr_latch_to_consumer_{op.trace_id}_{prod_id}"
                )
                
                # 3. 循环生命期安全约束（由于循环按 II 重复运行，本次迭代的消费者执行时刻必须在下一次 RTOVR 重写前完成）
                DependencyAxioms._add_constraint(
                    solver,
                    t_vars[op.trace_id] <= t_latch + II,
                    f"rtovr_latch_lifecycle_{op.trace_id}_{prod_id}"
                )

