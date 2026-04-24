import copy
from typing import List, Dict, Any
from ir.lir.lir_op import LIROp, PhysicalInstruction, SMIHeader
from api.hw_spec import HardwareSpec
from driver.field_mapper import FieldMapper
from driver.hw_constants import PORT_OFFSET_UR_RDATA

class PhysicalAssembler:
    """
    AIACC 物理指令装配器 (V5.0)。
    负责将带有逻辑元数据的 LIR 流“坍缩”为最终的物理指令对象。
    """
    def __init__(self, hw_spec: HardwareSpec):
        self.hw_spec = hw_spec

    def assemble(self, lir_stream: List[LIROp]) -> List[PhysicalInstruction]:
        """
        核心装配逻辑：
        1. 建立物理端子 ID 映射 (Resolve Routing)
        2. 实例化 SMIHeader (Construct Header)
        3. 生成 Payload (Map Body)
        """
        nodes_by_id = {op.trace_id: op for op in lir_stream}
        node_to_src_id = self._build_physical_routing_map(lir_stream)
        
        # 1. 初始分桶与属性映射
        iq_buckets: Dict[str, List[LIROp]] = {}
        for op in lir_stream:
            m_type = op.op_type.upper()
            iq_id = op.metadata.get("iq_id")
            
            if m_type == "RTOVR":
                slot_id = op.metadata.get("slot_id")
                iq_name = f"rtovr_{slot_id}" if slot_id is not None else "rtovr_unknown"
            elif iq_id is not None:
                iq_name = self.hw_spec.get_iq_name(iq_id).lower().replace("-", "_")
            else:
                iq_name = m_type.lower()
            
            iq_buckets.setdefault(iq_name, []).append(op)
        
        physical_instructions = []
        
        # 2. 逐 IQ 处理时间序列
        for iq_name, ops in iq_buckets.items():
            # 按绝对时间排序
            ops.sort(key=lambda x: x.metadata.get("global_t", 0))
            
            last_t = 0 # 追踪上一条指令的发射时间
            for i in range(len(ops)):
                op = ops[i]
                curr_t = op.metadata.get("global_t", 0)
                
                # 🛸 关键协议对齐：
                # 1. 第一条指令的 DLY (在指令 0 之前的等待) 实际上是靠指令 0 的 DLY 字段定义的
                #    注意：在硬件实现中，IQ 链表头的 DLY 代表“发射前等待”。
                # 2. 这里的逻辑需要极其精确：
                #    Instr 0 (at T0) -> DLY 应为 T0
                #    Instr 1 (at T1) -> Instr 0 的 DLY 应改为 T1 - T0 ? 
                #    不对，旧版逻辑是：
                #    stream[0].dly = T0
                #    stream[1].dly = T1 - T0
                #    这意味着指令 i 的 DLY 字段控制的是“发射指令 i 之前”的等待时间。
                
                relative_dly = curr_t - last_t
                last_t = curr_t
                
                print(f"[ASSEMBLER-DEBUG] IQ:{iq_name} | Op:{op.trace_id} | AbsT:{curr_t} | DLY:{relative_dly}")
                
                m_type = op.op_type.upper()
                self._bind_operand_ports(op, nodes_by_id, node_to_src_id)

                header = SMIHeader(
                    vld=int(op.metadata.get("vld", 1)),
                    dly=int(relative_dly),
                    loops=int(op.metadata.get("loops", 0)),
                    inc=int(op.metadata.get("inc", 0)),
                    embed=int(op.metadata.get("embed", 0)),
                    embed_end=int(op.metadata.get("embed_end", 0)),
                    jump=int(op.metadata.get("jump", 0)),
                    level=int(op.metadata.get("level", 0))
                )

                mod_spec = self.hw_spec.modules.get(m_type)
                payload_bits = FieldMapper.map_op_to_payload(op, mod_spec) if mod_spec else 0
                
                instr = PhysicalInstruction(
                    iq_name=iq_name,
                    header=header,
                    payload={"bits": payload_bits},
                    abs_t=curr_t,
                    trace_id=op.trace_id
                )
                physical_instructions.append(instr)
            
        return sorted(physical_instructions, key=lambda x: (x.abs_t, x.iq_name))

    def _build_physical_routing_map(self, stream: List[LIROp]) -> Dict[tuple, int]:
        """建立物理端子 ID 映射表 (RtoVR/ALU)"""
        node_to_src_id = {}
        for op in stream:
            iq_id = op.metadata.get("iq_id")
            if iq_id is None: continue
            
            iq_name = self.hw_spec.get_iq_name(iq_id)
            if "ur-r" in iq_name:
                idx = int(iq_name.replace("ur-r", ""))
                node_to_src_id[(op.trace_id, "D")] = PORT_OFFSET_UR_RDATA + idx
            else:
                # ALU 映射字典
                d_map = {
                    "INTADD": 0, "FPMUL": 3, "FPADD": 4, "LOGIC": 10,
                    "FPTOFP": 6, "INTTOINT": 7, "FPTOINT": 8, "INTTOFP": 9,
                    "INT8TOFP16": 14, "FP16MUL": 18,
                    "FP32ADDTREE5TO1": 26, "FP32ADDTREE8TO1": 28, "SHIFTDN": 29
                }
                st_map = { "INTADD": 2, "FPADD": 5 }
                
                m_type = op.op_type.upper()
                if m_type in d_map: node_to_src_id[(op.trace_id, "D")] = d_map[m_type]
                if m_type in st_map: node_to_src_id[(op.trace_id, "ST")] = st_map[m_type]
        return node_to_src_id

    def _bind_operand_ports(self, op: LIROp, nodes_by_id: Dict[str, LIROp], node_to_src_id: Dict[tuple, int]):
        """执行物理端口绑定，处理 RTOVR 递归穿透。"""
        m_type = op.op_type.upper()
        deps = op.metadata.get("depends_on", [])
        
        for i, dep_id in enumerate(deps):
            # 1. 递归查找真实的生产者 (穿透 RTOVR)
            real_producer_id = dep_id
            requested_port = op.metadata.get("dep_port", "D") if m_type == "RTOVR" else "D"
            
            # 👽 如果依赖的是一个 RTOVR 节点，说明我们需要“穿透”它去拿它背后的源
            if dep_id in nodes_by_id:
                dep_obj = nodes_by_id[dep_id]
                if dep_obj.op_type == "RTOVR":
                    # 获取 RTOVR 想要拉取的端口 (D 或 ST)
                    requested_port = dep_obj.metadata.get("dep_port", "D")
                    # 获取 RTOVR 背后的真实源 ID
                    if dep_obj.metadata.get("depends_on"):
                        real_producer_id = dep_obj.metadata["depends_on"][0]
            
            # 2. LOGIC 的第三个操作数固定拉取状态位 (ST)
            if m_type == "LOGIC" and i == 2: 
                requested_port = "ST"

            # 3. 查找物理端口 ID 并注入元数据
            if (real_producer_id, requested_port) in node_to_src_id:
                sid = node_to_src_id[(real_producer_id, requested_port)]
                
                # --- 核心对齐逻辑 ---
                if m_type == "RTOVR":
                    # RTOVR 的微指令字段名通常为 SEL (见 modules.yaml)
                    op.metadata["SEL"] = sid
                else:
                    # 通用计算单元字段映射
                    port_suffix = f"s{i}_src" if i < 2 else "st_src"
                    op.metadata[port_suffix] = sid
                    # 兼容性冗余
                    op.metadata["p_src_id"] = sid
