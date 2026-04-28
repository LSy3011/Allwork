import copy
from typing import List, Dict, Any
from ir.lir.lir_op import LIROp, PhysicalInstruction, SMIHeader
from api.hw_spec import HardwareSpec

class PhysicalAssembler:
    """
    AIACC 物理装配器 (V26.0 - INC 字段修正版)。
    根据用户指示，将地址步长填入 INC 字段 (Bit 6-9)，同时保留 INC_EMBED 以兼容仿真器。
    """
    def __init__(self, hw_spec: HardwareSpec):
        self.hw_spec = hw_spec

    def _get_clean_id(self, node: Any) -> str:
        if isinstance(node, str): return node
        return getattr(node, "trace_id", str(node))

    def assemble(self, lir_stream: List[LIROp], II: int = 1, num_iterations: int = 1) -> List[PhysicalInstruction]:
        trace_to_slot = {}
        trace_to_type = {}
        registry = self.hw_spec.config.get("iq_registry", {})
        print(f"  [REG-AUDIT] Loaded Registry: {registry}")
        for op in lir_stream:
            tid = self._get_clean_id(op)
            u_id = op.metadata.get("unit_id", "").lower()
            if not u_id: continue
            
            # 🛸 [V27.4 FIX] 统一处理 ur-rX, ur-wX 和通用单元 ID 解析
            if "ur-r" in u_id:
                idx_str = u_id.split("-r")[-1].replace("-", "")
                idx = int(idx_str)
                base = registry.get("UR_READ", 30)
                phys_id = base + idx
            elif "ur-w" in u_id:
                idx_str = u_id.split("-w")[-1].replace("-", "")
                idx = int(idx_str)
                phys_id = registry.get("UR_WRITE", 35) + idx
            else:
                # 尝试从 metadata 中直接获取 iq_id 或通过 unit_id 查表
                phys_id = op.metadata.get("iq_id") or op.metadata.get("slot_id")
                if phys_id is None and u_id:
                    phys_id = registry.get(u_id.upper(), 0)
                phys_id = int(phys_id or 0)
                
            trace_to_slot[tid] = phys_id
            trace_to_type[tid] = str(op.op_type).upper().strip()
            print(f"  [ID-TRACE] Op:{tid} U_ID:{u_id} Slot:{trace_to_slot[tid]}")
        
        print(f"  [ASM-DEBUG] Trace to Slot Mapping: {trace_to_slot}")

        iq_buckets: Dict[str, List[LIROp]] = {}
        for op in lir_stream:
            m_type = str(op.op_type).upper().strip()
            tid = self._get_clean_id(op)
            phys_id = trace_to_slot.get(tid, 0)
            if "RTOVR" in m_type:
                registry = self.hw_spec.config.get("iq_registry", {})
                base_rt = registry.get("RTOVR", 100)
                iq_name = f"rtovr-{phys_id - base_rt}"
            else:
                u_type_str = str(op.op_type).upper().strip()
                iq_name = self.hw_spec.get_iq_name(phys_id, u_type_str).upper()
                iq_name = iq_name.replace("UR-R-", "UR-R").replace("UR-W-", "UR-W")
                iq_name = iq_name.replace("_", "-").lower()
            iq_buckets.setdefault(iq_name, []).append(op)
            
        physical_instructions = []
        for iq_name, ops in iq_buckets.items():
            # 🛸 [V27.4 COMPACT] 极致紧凑按需起跑：Absolute_Cyc = 2 + t
            t_min_iq = min(op.metadata.get("global_t", 0) for op in ops)
            skip_iterations = t_min_iq // II
            
            # 🛸 [V5.2 FIX] 全量加入一拍 INIT，确保 loops = l_init - 1 >= 0
            l_init = (skip_iterations * II) + 1
            
            # 1. 发射单元定制的 INIT 帧 (Cyc 0 ~ l_init-1)
            physical_instructions.append(PhysicalInstruction(
                iq_name=iq_name, abs_t=0, trace_id=f"init_{iq_name}",
                header=SMIHeader(vld=0, loops=l_init - 1), payload={},
                stage="INIT", op_type=str(ops[0].op_type)
            ))

            # 2. 发射单元定制的 HDR 帧 (Cyc l_init)
            iq_max_stride = 0
            for op in ops:
                s = int(op.metadata.get("stride", 0))
                if s > iq_max_stride: iq_max_stride = s
            
            is_memory_iq = ("ur-" in iq_name.lower()) or ("rtovr" in iq_name.lower())
            hdr_inc_embed = max(iq_max_stride, 1) if is_memory_iq else 0
            
            # 🛸 [V27.4 DRAIN] 总循环次数需要减去该单元跳过的次数
            max_t_global = max(op.metadata.get("global_t", 0) for op in ops)
            total_iterations_needed = (max_t_global // II) + num_iterations
            loops_val = total_iterations_needed - skip_iterations - 1
            
            physical_instructions.append(PhysicalInstruction(
                iq_name=iq_name, abs_t=l_init, trace_id=f"hdr_{iq_name}",
                header=SMIHeader(vld=1, level=1, loops=loops_val, inc=iq_max_stride, inc_embed=hdr_inc_embed), 
                payload={}, stage="HDR", op_type=str(ops[0].op_type)
            ))
            
            # 3. 发射 BODY 帧 (从物理周期 l_init + 1 开始)
            kernel_map: Dict[int, Any] = {op.metadata["global_t"] % II: op for op in ops}
            
            # 🛸 [DEBUG V27.4] 核心审计：检查槽位冲突
            slots_str = ", ".join([f"Slot{s}:{op.trace_id}" for s, op in sorted(kernel_map.items())])
            print(f"  [KERNEL-MAP-AUDIT] Unit:{iq_name:<10} | {slots_str}")
            for k in range(II):
                if k in kernel_map:
                    op = kernel_map[k]
                    op_stride = int(op.metadata.get("stride", 0))
                    logical_addr = op.metadata.get("logical_addr", 0)
                    # 🛸 [V27.5 FIX] 相对预减补偿：地址合成使用 IQ 全局 CNT。
                    # 该 IQ 的第一个 BODY 帧对应的 CNT 为 0。
                    # 相对偏移 = (当前算子全局拍 - 该 IQ 起始全局拍) // II
                    iq_start_t = min(o.metadata.get("global_t", 0) for o in ops)
                    relative_skip = (op.metadata.get("global_t", 0) - iq_start_t) // II
                    addr = (logical_addr - op_stride * relative_skip)
                    
                    # 🛸 [V5.2 FIX] 地址合成归一化：内核指令不带 Stride，由外层 HDR 统一控制
                    op_mask = 0x1 if (is_memory_iq and op_stride != 0) else 0
                    op_inc = 0 
                    header = SMIHeader(vld=1, inc=op_inc, dly=0, inc_embed=op_mask)
                    payload = self._resolve_physical_payload(op, trace_to_slot, trace_to_type)
                    
                    # 🛸 动态修正 ADDR 字段 (包含预减结果)
                    if "ADDR" in payload:
                        payload["ADDR"] = addr % 256
                        payload["BANK"] = (addr // 256) % 8
                    trace_id = op.trace_id
                else:
                    header = SMIHeader(vld=0, inc=0, dly=0, inc_embed=0)
                    payload = {}
                    trace_id = f"nop_{iq_name}_{k}"

                # 🚀 [V5.2 FINAL] 绝对时序对齐公式：Cyc = l_init + 1 + k
                # 其中 l_init = (skip * II) + 1。
                # 代入得 Cyc = (skip * II) + 2 + k。
                # 当 skip=0, k=t 时，Cyc = 2 + t。完美对齐！
                abs_t_val = l_init + 1 + k
                
                if k == II - 1: header.embed_end = 1
                
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=abs_t_val, 
                    header=header, payload=payload,
                    stage="BODY", local_id=k, op_type=str(ops[0].op_type), trace_id=trace_id
                ))
                
        return physical_instructions

    def _resolve_physical_payload(self, op: Any, trace_to_slot: Dict[str, int], trace_to_type: Dict[str, str]) -> Dict[str, Any]:
        res = copy.deepcopy(op.metadata.get("physical_attrs", {}))
        m_type = str(op.op_type).upper().strip()
        res["SRC_PREC"] = 1; res["DST_PREC"] = 1
        
        if m_type in ["FPADD", "FPMUL"]:
            res["VALID"] = 1
            if m_type == "FPADD":
                res["SUB"] = 1 if (op.metadata.get("logic_op") == "SUB" or op.trace_id.startswith("sub")) else 0
                res["UPDATE_ST"] = 1
            
        if m_type in ["UR_READ", "UR_WRITE"]:
            res["VALID"] = 1
            addr = op.metadata.get("logical_addr")
            if addr is not None:
                # 🛸 [REVERT V27.0] 确认为“后加用”契约，撤销预减补偿
                res["ADDR"] = addr % 256; res["BANK"] = (addr // 256) % 8
            if m_type == "UR_WRITE":
                res["WBE"] = 0xFFFF

        if "RTOVR" in m_type:
            inputs = op.metadata.get("depends_on", [])
            if inputs:
                src_tid = self._get_clean_id(inputs[0])
                src_type = trace_to_type.get(src_tid, "UNKNOWN")
                src_slot = trace_to_slot.get(src_tid, 0)
                
                res["VALID"] = 1
                if src_type == "FPADD": res["SEL"] = 4
                elif src_type == "FPMUL": res["SEL"] = 3
                elif src_type == "UR_READ":
                    registry = self.hw_spec.config.get("iq_registry", {})
                    base_ur = registry.get("UR_READ", 30)
                    res["SEL"] = 30 + (src_slot - base_ur)
                else:
                    res["SEL"] = 0
                
                print(f"  [RTOVR-ASM-AUDIT] Op:{op.trace_id} Src:{src_tid} SrcType:{src_type} SrcSlot:{src_slot} -> SEL:{res['SEL']}")
            else:
                res["SEL"] = 0
                
        if m_type not in ["RTOVR"]:
            res["SMC_ID"] = 0
            
        # 🛸 [DEBUG]
        if m_type in ["UR_READ", "UR_WRITE", "FPMUL", "FPADD"]:
             print(f"  [PAYLOAD-AUDIT] Op:{op.trace_id} Type:{m_type} Payload:{res}")
             
        return res
