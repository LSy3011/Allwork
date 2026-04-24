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
            
            # 🛸 [NEW] V5.1 层次化对齐探测
            # 如果 DFG 节点带有 inner_loops，则说明处于循环张量模式，必须走“启动-循环-对齐”流
            is_hierarchical = any(op.metadata.get("inner_loops", 0) > 0 for op in ops)
            II = 3 # 默认对齐 MII=3
            
            if is_hierarchical:
                # 🛸 层次化生成流: INIT -> HDR -> BODY[II]
                t_start = min(op.metadata.get("global_t", 0) for op in ops)
                total_iters = max(op.metadata.get("inner_loops", 0) for op in ops)
                
                # A. INIT: 初始相位偏移 (利用 LOOPS 字段垫高周期)
                if t_start > 0:
                    init_header = SMIHeader(vld=0, loops=t_start-1, dly=0)
                    physical_instructions.append(PhysicalInstruction(
                        iq_name=iq_name, header=init_header, payload={"bits": 0},
                        abs_t=0, trace_id=f"{iq_name}_init"
                    ))
                
                # B. HDR: 循环头配置 (EMBED=1, 配置总循环次数)
                hdr_header = SMIHeader(vld=1, embed=1, loops=total_iters, dly=0)
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, header=hdr_header, payload={"bits": 0},
                    abs_t=t_start, trace_id=f"{iq_name}_hdr"
                ))
                
                # C. BODY: 模 II 展开的循环内核 (II 拍指令)
                slots = [None] * II
                for op in ops:
                    slot_idx = op.metadata.get("global_t", 0) % II
                    slots[slot_idx] = op
                    
                for k in range(II):
                    op = slots[k]
                    m_type = op.op_type.upper() if op else "NOP"
                    if op:
                        self._bind_operand_ports(op, nodes_by_id, node_to_src_id)
                        # 🛸 关键对齐：SMC_ID 统一设为 31
                        op.metadata["SMC_ID"] = 31
                        header = SMIHeader(vld=1, inc=1, embed_end=1 if k == II - 1 else 0, dly=0)
                        mod_spec = self.hw_spec.modules.get(m_type)
                        payload_bits = FieldMapper.map_op_to_payload(op, mod_spec) if mod_spec else 0
                    else:
                        header = SMIHeader(vld=0, dly=0, embed_end=1 if k == II - 1 else 0)
                        payload_bits = 0
                    
                    physical_instructions.append(PhysicalInstruction(
                        iq_name=iq_name, header=header, payload={"bits": payload_bits},
                        abs_t=t_start + k, trace_id=f"{iq_name}_{k}"
                    ))
            else:
                # 🛰️ 常规单点指令流
                last_t = 0
                for i in range(len(ops)):
                    op = ops[i]
                    curr_t = op.metadata.get("global_t", 0)
                    relative_dly = curr_t - last_t
                    
                    while relative_dly > 7:
                        physical_instructions.append(PhysicalInstruction(
                            iq_name=iq_name, header=SMIHeader(vld=0, dly=7), payload={"bits": 0},
                            abs_t=last_t + 7, trace_id=f"nop_{iq_name}_{last_t + 7}"
                        ))
                        last_t += 7
                        relative_dly -= 7

                    m_type = op.op_type.upper()
                    self._bind_operand_ports(op, nodes_by_id, node_to_src_id)
                    header = SMIHeader(
                        vld=int(op.metadata.get("vld", 1)), dly=int(relative_dly),
                        loops=int(op.metadata.get("loops", 0)),
                        inc=int(op.metadata.get("inc", 0)),
                        embed=int(op.metadata.get("embed", 0)),
                        embed_end=int(op.metadata.get("embed_end", 0)),
                        jump=int(op.metadata.get("jump", 0)),
                        level=int(op.metadata.get("level", 0))
                    )
                    mod_spec = self.hw_spec.modules.get(m_type)
                    payload_bits = FieldMapper.map_op_to_payload(op, mod_spec) if mod_spec else 0
                    
                    physical_instructions.append(PhysicalInstruction(
                        iq_name=iq_name, header=header, payload={"bits": payload_bits},
                        abs_t=curr_t, trace_id=op.trace_id
                    ))
                    last_t = curr_t
            
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
