import copy
from typing import List, Dict, Any
from ir.lir.lir_op import LIROp, PhysicalInstruction, SMIHeader
from api.hw_spec import HardwareSpec

class PhysicalAssembler:
    """
    AIACC 物理装配器 (V5.1 - 零硬编码版)。
    """
    def __init__(self, hw_spec: HardwareSpec, verbosity: int = 2):
        self.hw_spec = hw_spec
        self.verbosity = verbosity

    def _get_clean_id(self, node: Any) -> str:
        if isinstance(node, str): return node
        return getattr(node, "trace_id", str(node))

    def _get_registry_val(self, key: str) -> int:
        """从注册表中提取基地址，若缺失则抛出异常，强制要求规格对齐"""
        val = self.hw_spec.config.get("iq_registry", {}).get(key)
        if val is None:
            raise ValueError(f"CRITICAL: Resource key '{key}' missing in iq_registry. Please update hardware spec.")
        return val

    def assemble(self, lir_stream: List[LIROp], II: int = 1, num_iterations: int = 1) -> List[PhysicalInstruction]:
        trace_to_slot = {}
        trace_to_type = {}
        
        for op in lir_stream:
            tid = self._get_clean_id(op)
            u_id = op.metadata.get("unit_id", "").lower()
            if not u_id: continue
            
            # 🛸 [V5.1 FIX] 移除 30/35/100 等魔法数字
            if "ur-r" in u_id:
                idx = int(u_id.split("-r")[-1].replace("-", ""))
                phys_id = self._get_registry_val("UR_READ") + idx
            elif "ur-w" in u_id:
                idx = int(u_id.split("-w")[-1].replace("-", ""))
                phys_id = self._get_registry_val("UR_WRITE") + idx
            elif "rtovr" in u_id:
                idx = int(u_id.split("-")[-1])
                phys_id = self._get_registry_val("RTOVR") + idx
            else:
                phys_id = op.metadata.get("iq_id") or op.metadata.get("slot_id")
                if phys_id is None:
                    phys_id = self._get_registry_val(u_id.upper())
                phys_id = int(phys_id)

            trace_to_slot[tid] = phys_id
            trace_to_type[tid] = str(op.op_type).upper().strip()
        
        iq_buckets: Dict[str, List[LIROp]] = {}
        for op in lir_stream:
            m_type = str(op.op_type).upper().strip()
            tid = self._get_clean_id(op)
            phys_id = trace_to_slot.get(tid, 0)
            if "RTOVR" in m_type:
                base_rt = self._get_registry_val("RTOVR")
                iq_name = f"rtovr-{phys_id - base_rt}"
            else:
                iq_name = self.hw_spec.get_iq_name(phys_id, m_type).upper()
                iq_name = iq_name.replace("UR-R-", "UR-R").replace("UR-W-", "UR-W")
                iq_name = iq_name.replace("_", "-").lower()
            iq_buckets.setdefault(iq_name, []).append(op)
            
        physical_instructions = []
        for iq_name, ops in iq_buckets.items():
            t_min_iq = min(op.metadata.get("global_t", 0) for op in ops)
            skip_iterations = t_min_iq // II
            l_init = (skip_iterations * II) + 1
            
            physical_instructions.append(PhysicalInstruction(
                iq_name=iq_name, abs_t=0, trace_id=f"init_{iq_name}",
                header=SMIHeader(vld=0, loops=l_init - 1), payload={},
                stage="INIT", op_type=str(ops[0].op_type)
            ))

            iq_max_stride = 0
            for op in ops:
                s = int(op.metadata.get("stride", 0))
                if s > iq_max_stride: iq_max_stride = s
            
            is_memory_iq = ("ur-" in iq_name.lower()) or ("rtovr" in iq_name.lower())
            hdr_inc_embed = max(iq_max_stride, 1) if is_memory_iq else 0
            
            max_t_global = max(op.metadata.get("global_t", 0) for op in ops)
            # ADR-038/039：SMI_DS_T B=2 配对 datapath（SMI_DS_T 指令 IQ + 喂它的 SHIFTDN gather IQ）须
            # 始终做 **2 slot（B=2）** 迭代——issue_cnt==2 才出数 + 两 slot 各须 8 beat 填满。这与输出词数
            # （num_iterations）**无关**：fp16(8 元素=1 词)num_iterations=1 → 只 1 issue + gather 只送 1 slot
            # → hi 槽 open 不 fill → hi_ready=0 → 永不出数（VCS tb_dst_fp16 实证）。fp32(2 词)是
            # num_iterations=2 巧合凑够。故对这些 IQ 用 eff_num_iter=max(num_iterations,2)，与输出词数解耦。
            is_smi_ds_t_dp = ("smi-ds-t" in iq_name.lower()
                              or any(op.metadata.get("smi_ds_t_gather") for op in ops))
            # ADR-041 BLOCK-2/D2：SMI_DS_T 输出 16-beat 流——SHIFTUP/RTOVR/UR_W0 须持续广播 WINDOW 拍
            #   （逐拍取不同 data_out beat）。这些 IQ 的节点带 stream_beats（cross_smc_unit_op 在 dst-datapath
            #   建节点时打标，门控 producer=SMI_DS_T；fma/matmul broadcast SHIFTUP 无此标记 → 不受影响）。
            #   用 stream_beats 覆盖 IQ 级 LOOPS（与输出词数 num_iterations 解耦：fp16 num_iterations=1 但
            #   须广播 8/16 拍逐行）。SHIFTUP INC=0（data 硬连 data_out）、UR_W0 INC=1（连续行）由各 op stride 定。
            stream_beats = max((int(op.metadata.get("stream_beats", 0)) for op in ops), default=0)
            if stream_beats > 0:
                eff_num_iter = stream_beats
            elif is_smi_ds_t_dp:
                eff_num_iter = max(num_iterations, 2)
            else:
                eff_num_iter = num_iterations
            total_iterations_needed = (max_t_global // II) + eff_num_iter
            loops_val = total_iterations_needed - skip_iterations - 1

            physical_instructions.append(PhysicalInstruction(
                iq_name=iq_name, abs_t=l_init, trace_id=f"hdr_{iq_name}",
                header=SMIHeader(vld=1, level=1, loops=loops_val, inc=iq_max_stride, inc_embed=hdr_inc_embed), 
                payload={}, stage="HDR", op_type=str(ops[0].op_type)
            ))
            
            # ADR-041 DEBT-041-STREAM-DLY（hco 二审 catch，**VCS-gated，不在此修**）：D2 要求 stream IQ
            #   产「同一窗口内连续拍 DLY=1」。本想用 1-slot kernel（真 op 不补 nop 到 II → HDR loop 每拍回跳
            #   = 连续）实现，但实测：model-A sim 不忠实建模 SMI_DS_T 16 拍连续 data_out 流（feed 逐拍填充
            #   时序非真），连续 beat 会在 scatter 填 feed 前就读 → 取零、sim 全 0 FAIL。即此修法无法在
            #   现 sim 验证（sim 时序盲），强行落只会换一种假绿/真退化。故保持 II-slot kernel（II-spaced
            #   strided），DEBT-041-STREAM-DLY 留待 VCS + sim 流式模型升级（DEBT-038-B2-GATHER/spec §6）一并解。
            kernel_map: Dict[int, Any] = {op.metadata["global_t"] % II: op for op in ops}
            for k in range(II):
                if k in kernel_map:
                    op = kernel_map[k]
                    op_stride = int(op.metadata.get("stride", 0))
                    logical_addr = op.metadata.get("logical_addr", 0)
                    iq_start_t = min(o.metadata.get("global_t", 0) for o in ops)
                    relative_skip = op.metadata.get("global_t", 0) // II - iq_start_t // II
                    addr = (logical_addr - op_stride * relative_skip)
                    
                    op_mask = 0x1 if (is_memory_iq and op_stride != 0) else 0
                    header = SMIHeader(vld=1, inc=0, dly=0, inc_embed=op_mask)
                    payload = self._resolve_physical_payload(op, trace_to_slot, trace_to_type)
                    
                    # [Phase 2 SSoT] 统一使用 PhysicalMemoryLayout 解析地址
                    is_ur_iq = "ur-" in iq_name.lower()
                    if is_ur_iq:
                        from src.core.memory_layout import PhysicalMemoryLayout
                        layout = PhysicalMemoryLayout.from_spec(self.hw_spec)
                        orig_bank, orig_offset = layout.resolve(int(logical_addr))
                        comp_offset = (orig_offset - op_stride * relative_skip) % layout.bank_depth
                        payload["BANK"] = orig_bank
                        payload["ADDR"] = comp_offset
                    trace_id = op.trace_id
                else:
                    header = SMIHeader(vld=0, inc=0, dly=0, inc_embed=0)
                    payload = {}
                    trace_id = f"nop_{iq_name}_{k}"

                abs_t_val = l_init + 1 + k
                if k == II - 1: header.embed_end = 1
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=abs_t_val, header=header, payload=payload,
                    stage="BODY", local_id=k, op_type=str(ops[0].op_type), trace_id=trace_id
                ))
        return physical_instructions

    def _resolve_physical_payload(self, op: Any, trace_to_slot: Dict[str, int], trace_to_type: Dict[str, str]) -> Dict[str, Any]:
        """
        [Phase 3] 代理至 UnitPayloadFactory 处理，摒弃硬编码字典。
        """
        from src.driver.payload_factory import UnitPayloadFactory
        return UnitPayloadFactory.build(op, trace_to_slot, trace_to_type, self.hw_spec)
