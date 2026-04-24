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

    def assemble(self, lir_stream: List[LIROp], II: int = 1, num_iterations: int = 1) -> List[PhysicalInstruction]:
        """
        [V5.5+ 终极对齐版] 编织分层流，并补全硬件默认属性。
        :param num_iterations: 硬件循环迭代次数 (自适应)
        """
        node_to_src_id = self._build_physical_routing_map(lir_stream)
        nodes_by_id = {op.trace_id: op for op in lir_stream}
        
        # 1. 建立 IQ 分桶 (计算单元用标准名，RTOVR 用 Slot 名)
        iq_buckets: Dict[str, List[LIROp]] = {}
        for op in lir_stream:
            m_type = op.op_type.upper()
            iq_id = op.metadata.get("iq_id")
            if m_type == "RTOVR":
                slot_id = op.metadata.get("slot_id")
                # 🛸 严格对齐物理 Slot: rtovr_3, rtovr_4...
                iq_name = f"rtovr_{slot_id}" if slot_id is not None else "rtovr_unknown"
            elif iq_id is not None:
                # 🛸 使用 Spec 标准名: FPMUL-0, FPADD-0, UR-READ-0...
                iq_name = self.hw_spec.get_iq_name(iq_id, op.op_type).upper()
            else:
                continue
            iq_buckets.setdefault(iq_name, []).append(op)
            
        physical_instructions = []
        
        # 2. 对每个物理 IQ 进行流编织
        for iq_name, ops in iq_buckets.items():
            first_op = ops[0]
            op_type = first_op.op_type.upper()
            
            # A. INIT: 初始相位偏移
            t_start = min(op.metadata.get("global_t", 0) for op in ops)
            if t_start > 0:
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=0, trace_id=f"init_{iq_name}",
                    header=SMIHeader(vld=0, loops=t_start), payload={},
                    stage="INIT", comment=f"Initial Phase Offset: {t_start} iterations",
                    op_type=op_type
                ))
                
            # B. HDR: 循环头 (自适应 num_iterations)
            physical_instructions.append(PhysicalInstruction(
                iq_name=iq_name, abs_t=1, trace_id=f"hdr_{iq_name}",
                header=SMIHeader(vld=1, embed=1, loops=num_iterations), payload={},
                stage="HDR", comment=f"Loop Header Config (Iter={num_iterations})",
                op_type=op_type
            ))
            
            # C. BODY: 内核循环
            kernel_map: Dict[int, LIROp] = {op.metadata["global_t"] % II: op for op in ops}
            
            for k in range(II):
                if k in kernel_map:
                    op = kernel_map[k]
                    self._bind_operand_ports(op, nodes_by_id, node_to_src_id)
                    # 🛸 属性补全与 SMC_ID 锁定
                    payload = self._fill_default_payload(op.op_type, op.metadata)
                    
                    p_instr = PhysicalInstruction(
                        iq_name=iq_name, abs_t=k + 2, 
                        header=SMIHeader(vld=1, inc=1), 
                        payload=payload,
                        stage="BODY", local_id=k,
                        trace_id=op.trace_id, op_type=op.op_type
                    )
                    if k == II - 1: p_instr.header.embed_end = 1
                    physical_instructions.append(p_instr)
                else:
                    # NOP 填充
                    physical_instructions.append(PhysicalInstruction(
                        iq_name=iq_name, abs_t=k + 2,
                        header=SMIHeader(vld=0), payload={},
                        stage="BODY", local_id=k,
                        trace_id="NOP", op_type=op_type,
                        comment="NOP Cell"
                    ))
                    if k == II - 1: physical_instructions[-1].header.embed_end = 1

        return sorted(physical_instructions, key=lambda x: (x.iq_name, x.abs_t))

    def _fill_default_payload(self, op_type: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """根据 modules.yaml 补全默认硬件字段，并强制锁定关键位"""
        # 🛸 鲁棒匹配模块名
        m_type_upper = op_type.upper()
        target_mod = None
        for mod_name in self.hw_spec.modules:
            if mod_name.upper() == m_type_upper:
                target_mod = self.hw_spec.modules[mod_name]
                break
        
        payload = copy.deepcopy(metadata)
        
        if target_mod:
            for field in target_mod.code_fields:
                f_name = field.name
                if f_name not in payload:
                    # 🛸 活跃指令 VALID 默认设为 1，其他按 0 补全
                    if f_name.upper() == "VALID" or f_name.lower() == "vld":
                        payload[f_name] = 1
                    else:
                        payload[f_name] = 0
            
            # 🛸 强制对齐 SMC_ID (兼容多种命名)
            for smc_key in ["SMC_ID", "smc_id_en"]:
                if smc_key in payload or any(f.name == smc_key for f in target_mod.code_fields):
                    payload[smc_key] = 31
                    
        return payload
    def _build_physical_routing_map(self, stream: List[LIROp]) -> Dict[tuple, int]:
        """
        [V5.2 自适应路由] 建立物理端子 ID 映射表。
        不再依赖硬编码字典，而是根据 Registry 基址与 Unit 端口定义动态推算。
        """
        node_to_src_id = {}
        registry = self.hw_spec.config.get("iq_registry", {})
        
        for op in stream:
            m_type = op.op_type.upper()
            iq_id = op.metadata.get("iq_id")
            
            # 🛸 [NEW] 优先使用已分配的物理 iq_id 作为路由基址
            if iq_id is not None:
                base_id = iq_id
            else:
                base_id = registry.get(m_type)
                if base_id is None:
                    # 🛸 处理 UR_READ 等特殊注册名
                    if m_type == "UR_READ": base_id = registry.get("UR_READ", 30)
                    elif m_type == "UR_WRITE": base_id = registry.get("UR_WRITE", 35)
                    else: continue
            
            # 获取该单元的输出端口列表
            mod_spec = self.hw_spec.modules.get(m_type)
            if not mod_spec: continue
            
            # 🛸 [FIX] Pydantic 对象访问模式
            output_ports = getattr(mod_spec, "ports", {}).get("outputs", [])
            if not output_ports:
                # 兼容旧版或简化定义的 D 端口
                node_to_src_id[(op.trace_id, "D")] = base_id
            else:
                for i, port in enumerate(output_ports):
                    # 如果 port 也是对象，同样需要处理
                    p_name = port.get("name", "D") if isinstance(port, dict) else getattr(port, "name", "D")
                    node_to_src_id[(op.trace_id, p_name)] = base_id + i
                    
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
