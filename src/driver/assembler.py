import copy
from typing import List, Dict, Any
from ir.lir.lir_op import LIROp, PhysicalInstruction, SMIHeader
from api.hw_spec import HardwareSpec

class PhysicalAssembler:
    """
    AIACC 物理装配器 (V13.0)。
    对齐精度语义：0=FP16, 1=FP32 (Hardware Default)。
    """
    def __init__(self, hw_spec: HardwareSpec):
        self.hw_spec = hw_spec

    def _get_clean_id(self, node: Any) -> str:
        if isinstance(node, str): return node
        return getattr(node, "trace_id", str(node))

    def assemble(self, lir_stream: List[LIROp], II: int = 1, num_iterations: int = 1) -> List[PhysicalInstruction]:
        trace_to_slot = {}
        for op in lir_stream:
            tid = self._get_clean_id(op)
            phys_id = op.metadata.get("iq_id") or op.metadata.get("slot_id") or 0
            trace_to_slot[tid] = int(phys_id)

        iq_buckets: Dict[str, List[LIROp]] = {}
        for op in lir_stream:
            m_type = str(op.op_type).upper().strip()
            tid = self._get_clean_id(op)
            phys_id = trace_to_slot.get(tid, 0)
            
            if "RTOVR" in m_type:
                registry = self.hw_spec.config.get("iq_registry", {})
                base_rt = registry.get("RTOVR", 100)
                # 🛸 [FIX] RTOVR 命名对齐仿真器 (rtovr-0 而非 rtovr_0)
                iq_name = f"rtovr-{phys_id - base_rt}"
            else:
                # 🛸 [FIX] UR 命名对齐仿真器 (ur-r0 而非 ur-r-0)
                iq_name = self.hw_spec.get_iq_name(phys_id, op.op_type).upper()
                iq_name = iq_name.replace("UR-R-", "UR-R").replace("UR-W-", "UR-W")
                iq_name = iq_name.replace("_", "-").lower()
            iq_buckets.setdefault(iq_name, []).append(op)
            
        physical_instructions = []
        for iq_name, ops in iq_buckets.items():
            first_op = ops[0]
            op_type = str(first_op.op_type).upper().strip()
            t_vals = [op.metadata.get("global_t", 0) for op in ops]
            t_first = min(t_vals)
            align_t = (t_first // II) * II
            l_init = align_t - 2
            
            if l_init >= 0:
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=0, trace_id=f"init_{iq_name}",
                    header=SMIHeader(vld=0, loops=l_init), payload={},
                    stage="INIT", op_type=op_type
                ))

            loops_val = max(0, num_iterations + (max(t_vals)//II - min(t_vals)//II) - 1)
            physical_instructions.append(PhysicalInstruction(
                iq_name=iq_name, abs_t=1, trace_id=f"hdr_{iq_name}",
                # 🛸 [FIX] 设置 level=1 以触发硬件/仿真器的 EMBED_LOOPS 逻辑
                header=SMIHeader(vld=1, embed=1, level=1, loops=loops_val), payload={},
                stage="HDR", op_type=op_type
            ))
            
            kernel_map: Dict[int, LIROp] = {op.metadata["global_t"] % II: op for op in ops}
            
            # --- [II-ALIGNED BODY GENERATION] ---
            # 🚀 [II-ALIGNED] 强制补全 II 个周期的指令流，消除时序漂移
            for k in range(II):
                if k in kernel_map:
                    op = kernel_map[k]
                    # 有操作的周期，VLD=1
                    header = SMIHeader(vld=1, inc=0, dly=0)
                    payload = self._resolve_physical_payload(op, trace_to_slot)
                    trace_id = op.trace_id
                else:
                    # 无操作的周期，插入 VLD:0 的 NOP 占位符
                    header = SMIHeader(vld=0, inc=0, dly=0)
                    payload = {}
                    trace_id = f"nop_{iq_name}_{k}"
                
                # 🛸 关键对齐：EMBED_END:1 必须且只能在第 II-1 条指令上 (即周期的末尾)
                if k == II - 1:
                    header.embed_end = 1
                
                p_instr = PhysicalInstruction(
                    iq_name=iq_name, abs_t=k + 2, 
                    header=header, payload=payload,
                    stage="BODY", local_id=k, op_type=op_type, trace_id=trace_id
                )
                physical_instructions.append(p_instr)
        return physical_instructions

    def _resolve_physical_payload(self, op: Any, trace_to_slot: Dict[str, int]) -> Dict[str, Any]:
        res = copy.deepcopy(op.metadata.get("physical_attrs", {}))
        m_type = str(op.op_type).upper().strip()
        
        # 🛸 [TRUTH-ALIGN] 精度语义：0=FP16, 1=FP32
        # 遵循硬件默认值：如果没有指定，默认为 FP32 (1)
        data_format = op.metadata.get("data_format", "FP32")
        if data_format == "FP16":
            res["SRC_PREC"] = 0
            res["DST_PREC"] = 0
        else: # FP32 or Default
            res["SRC_PREC"] = 1
            res["DST_PREC"] = 1

        if m_type == "FPADD":
            if op.metadata.get("logic_op") == "SUB" or op.trace_id.startswith("sub"):
                res["SUB"] = 1
            else:
                res["SUB"] = 0
            res["UPDATE_ST"] = op.metadata.get("UPDATE_ST", 1)

        if m_type in ["UR_READ", "UR_WRITE"]:
            logical_addr = op.metadata.get("logical_addr")
            if logical_addr is not None:
                res["ADDR"] = logical_addr % 256
                res["BANK"] = (logical_addr // 256) % 8
        
        if "RTOVR" in m_type:
            inputs = op.metadata.get("depends_on", [])
            if inputs:
                src_tid = self._get_clean_id(inputs[0])
                src_slot = trace_to_slot.get(src_tid, 0)
                res["SEL"] = src_slot

        res["VALID"] = 1
        res["SMC_ID"] = 31 
        
        for key in ["ADDR", "BANK", "SEL", "SUB", "WBE", "SRC_PREC", "DST_PREC"]:
            if key in op.metadata:
                res[key] = op.metadata[key]

        return res
