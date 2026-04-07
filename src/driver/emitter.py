import os, json, struct, numpy as np, copy
from pathlib import Path
from typing import List, Dict, Tuple

from api.hw_spec import HardwareSpec
from driver.field_mapper import FieldMapper
from driver.compactor import Compactor
from driver.packer import BitPacker
from driver.hw_constants import (
    IQ_BASE_UR_READ, IQ_BASE_UR_WRITE, 
    PORT_OFFSET_UR_RDATA, PORT_ID_FPMUL_D, PORT_ID_FPADD_D, PORT_ID_INTADD_D0,
    OFFSET_VLD, DEFAULT_BITS_CODE, DEFAULT_MAX_CYCLES
)

class LIRPacker:
    """
    AIACC AI-COMPILER EMITTER (Macro-Driven Version).
    负责将 LIR 流转换为物理指令 Hex 及仿真配置文件。
    """
    def __init__(self, config_dir: str = "hw_config"):
        self.hw_spec = HardwareSpec.load(config_dir)

    def emit_from_stream(self, final_stream: List, case_root: str, program=None):
        root = Path(case_root)
        smc0 = root / "smc_0"
        ur_dir, bank_dir, analysis = smc0/"ur_data", smc0/"output_bank", smc0/"analysis"
        for d in [smc0, ur_dir, bank_dir, analysis]: os.makedirs(d, exist_ok=True)
            
        (root / "sim_trace.log").touch()
        
        # 1. 建立物理端子 ID 映射表 (基于硬件规格动态反查)
        node_to_src_id = {}
        nodes_by_id = {op.trace_id: op for op in final_stream}
        
        for op in final_stream:
            iq_id = op.metadata.get("iq_id")
            if iq_id is not None:
                iq_name = self.hw_spec.get_iq_name(iq_id)
                # 🛸 动态映射 RtoVR 的 Source ID：UR_READ (ur-r0..3) 作为物理读数据源
                if "ur-r" in iq_name:
                    idx = int(iq_name.replace("ur-r", ""))
                    node_to_src_id[(op.trace_id, "D")] = PORT_OFFSET_UR_RDATA + idx
                else:
                    # ALU 路由直连 (同步 D/ST 端子) - 完全物理对接 rtovr_config.yaml
                    d_id, st_id = -1, -1
                    m_type = op.op_type.upper()
                    
                    d_map = {
                        "INTADD": 0, "FPMUL": 3, "FPADD": 4, "LOGIC": 10,
                        "INT8TOFP16": 14, "FP16MUL": 18,
                        "FP32ADDTREE5TO1": 26, "FP32ADDTREE8TO1": 28, "SHIFTDN": 29
                    }
                    st_map = { "INTADD": 2, "FPADD": 5 }
                    
                    if m_type in d_map: d_id = d_map[m_type]
                    if m_type in st_map: st_id = st_map[m_type]
                    
                    if d_id != -1: node_to_src_id[(op.trace_id, "D")] = d_id
                    if st_id != -1: node_to_src_id[(op.trace_id, "ST")] = st_id

        # 2. 分桶聚合所有物理 IQ 流
        module_streams = {}
        for op in final_stream:
            iq_id = op.metadata.get("iq_id")
            m_type = op.op_type.upper()
            
            # --- [ADPC V4.0 ALIGNMENT] 确定物理 IQ 名称 ---
            # RtoVR 在硬件上是 Crossbar 矩阵，每一个输入端口（Slot）都是一个独立的物理指令队列。
            # 直接通过其目的端 slot_id 来确定它所在的物理队列文件，彻底消除将 RtoVR 视为单一整体的思维。
            if m_type == "RTOVR":
                slot_id = op.metadata.get("slot_id")
                name = f"rtovr-{slot_id}" if slot_id is not None else "rtovr-unknown"
            elif iq_id is not None:
                name = self.hw_spec.get_iq_name(iq_id)
            else:
                name = m_type.lower()

            # 🚀 [MULTI-OPERAND ROUTING] 处理 S0, S1, ST 等复杂端口映射
            deps = op.metadata.get("depends_on", [])
            for i, dep_id in enumerate(deps):
                # 针对 ALU 单元的映射习惯: 0->S0, 1->S1, 2->ST
                port_suffix = f"s{i}_src" if i < 2 else "st_src"
                
                # 🛸 [BUG-FIX] 穿透 RTOVR 寻找真实物理生成者
                real_producer_id = dep_id
                requested_port = "D"
                
                # 情况 1：如果当前算子 op 是 RTOVR，它直接在元数据里声明了要引用的端子 (D 或 ST)
                if m_type == "RTOVR" and i == 0:
                    requested_port = op.metadata.get("dep_port", "D")
                
                # 情况 2：如果依赖项 dep_id 是 RTOVR，说明它是计算单元的中间跳板
                if dep_id in nodes_by_id:
                    dep_obj = nodes_by_id[dep_id]
                    if dep_obj.op_type == "RTOVR":
                        # 计算单元通过 RTOVR 引用 ST 时，由 RTOVR 的 dep_port 属性递归透传
                        requested_port = dep_obj.metadata.get("dep_port", "D")
                        if dep_obj.metadata.get("depends_on"):
                            real_producer_id = dep_obj.metadata["depends_on"][0]
                
                # 特殊校准：LOGIC 单元的第 3 个操作数强制走物理 ST 链路
                if m_type == "LOGIC" and i == 2: requested_port = "ST"

                if (real_producer_id, requested_port) in node_to_src_id:
                    sid = node_to_src_id[(real_producer_id, requested_port)]
                    op.metadata[port_suffix] = sid
                    if i == 0: op.metadata["p_src_id"] = sid
                
                # 🛸 关键诊断：打印每一个 RTOVR 和计算算子的物理端子指向
                if op.op_type == "RTOVR" or m_type == "LOGIC":
                    print(f"  [LIR-BIND] Op:{op.trace_id} ({m_type}) Dep[{i}]:{real_producer_id}.{requested_port} -> ID:{op.metadata.get(port_suffix, 'MISSING')}")

            # 🛸 物理审计：确认 IQ 是否在白名单中
            lookup = f"IQ_{m_type}"
            if "RTOVR" in m_type: lookup = "IQ_RTOVR"
            if "UR_READ" in m_type: lookup = "IQ_UR_READ"
            if "UR_WRITE" in m_type: lookup = "IQ_UR_WRITE"
            
            if lookup not in self.hw_spec.iqs:
                print(f"[EMIT-DENIED] Op:{op.trace_id} Type:{m_type} Lookup:{lookup} IQ:{name} not in {list(self.hw_spec.iqs.keys())}")
                continue
            
            # DEBUG PRINT
            # print(f"[EMIT-LOG] Op:{op.trace_id} Type:{m_type} IQ:{name} T:{op.metadata['global_t']}")

            mod_spec = self.hw_spec.modules.get(m_type)
            if not mod_spec: continue
            
            payload = FieldMapper.map_op_to_payload(op, mod_spec)
            module_streams.setdefault(name, []).append({
                "t": op.metadata["global_t"], 
                "payload": payload,
                "iq_conf": self.hw_spec.iqs[lookup]
            })

        print(f"[EMIT-SUMMARY] Collected Streams: {list(module_streams.keys())}")
        # 3. 二进制打包与 IQ 初始化记录
        iq_init = {}
        iq_reg = self.hw_spec.system_core.get("iq_registry", {})
        
        for name, stream in module_streams.items():
            if name == "ur-r1":
                times = [s["t"] for s in stream]
                print(f"[EMIT-TRACE] IQ: ur-r1 | Op T-seq: {times}")

            # 🛸 直接采用硬件规格中定义的物理位宽，不再依赖由于巧合导致的 iq_reg 映射
            local_iq_conf = copy.deepcopy(stream[0]["iq_conf"])
            
            stream.sort(key=lambda x: x["t"])
            compressed = Compactor.compress_iq_stream(stream, local_iq_conf)
            
            hex_lines = []
            for cmd in compressed:
                val = BitPacker.pack_capsule(
                    iq_name=name, iq_conf=local_iq_conf, payload=cmd["payload"],
                    loops=cmd["loops"], dly=cmd["dly"],
                    inc=cmd.get("inc", 0), 
                    embed=cmd.get("embed", 0), 
                    jump=cmd.get("jump", 0),
                    embed_end=cmd.get("embed_end", 0),
                    hw_spec=self.hw_spec,
                    valid=cmd.get("valid", 1)
                )

                
                # 🛸 标准化物理点火 (VLD/VALID)
                # 遵循 V5.0 物理规范：VLD 在 Header 的第 40 位，Header 在 Payload 的高侧。
                vld_bit = local_iq_conf.BITS_CODE + OFFSET_VLD
                if cmd.get("valid", 1) != 0:
                    val |= (1 << vld_bit)
                else:
                    val &= ~(1 << vld_bit)
                
                hex_lines.append(f"{val:032x}")
            
            # 🛸 [NAMING ALIGNMENT] 强制全局小写 + 下划线对齐
            # 这样无论 get_iq_name 返回什么（LOGIC 或 logic-0），产出的文件名都是 iq_logic_0.hex
            safe_name = name.lower().replace("-", "_")
            hex_file_name = f"iq_{safe_name}.hex"
            with open(smc0 / hex_file_name, "w") as f:
                f.write("\n".join(hex_lines) + "\n")
            
            print(f"[EMITTER-DEBUG] IQ: {name} | Name Mapping: {safe_name} | Hex Lines: {len(hex_lines)}", flush=True)
            
            iq_init[name] = f"smc_0/{hex_file_name}" # 这里 Key 依然用原始 name 以匹配 SMC Core

        # 4. 导出 sim_config.json
        # 自动适配仿真周期: 取 DEFAULT 与实际排布最大拍+余量 中的较大值
        actual_max_t = max((s["t"] for stream in module_streams.values() for s in stream), default=0)
        effective_max_cycles = max(DEFAULT_MAX_CYCLES, actual_max_t + 10)
        sim_conf = {
            "smc_id": 0,
            "iq_init": iq_init,
            "ur_init": {str(b): f"smc_0/ur_data/bank{b}.hex" for b in range(8)},
            "max_cycles": effective_max_cycles,
            "tensors": []
        }
        
        if program:
            self._handle_program_data(program, ur_dir, sim_conf)

        with open(root / "sim_config.json", "w") as f:
            json.dump(sim_conf, f, indent=2)

        print(f"[EMIT DONE] case emitted to {case_root}")

    def _handle_program_data(self, program, ur_dir, sim_conf):
        """处理 Bank 数据初始化与 Tensor 元数据映射。"""
        for b_id, storage in program.memory_allocator.banks.items():
            with open(ur_dir / f"bank{b_id}.hex", "w") as f:
                for entry in storage:
                    if not isinstance(entry, (list, np.ndarray, tuple)):
                        # 🛸 标量广播扩展: 如果原始数据是标量，其物理行填满 4 份一样的数
                        entry = [entry] * 4
                    
                    # 🛰️ 128-bit 矢量物理通道对位 (修正为 LSB-First: Index i -> Lane i)
                    packed = sum((int(struct.unpack(">I", struct.pack(">f", x))[0]) & 0xFFFFFFFF) << (32 * i) for i, x in enumerate(entry[:4]))
                    f.write(f"{packed:032x}\n")
        
        for t_name, t_obj in program.tensors.items():
            sim_conf["tensors"].append({
                "name": t_name, "bank": t_obj.bank, "addr": t_obj.offset,
                "shape": t_obj.shape, "role": t_obj.role
            })
