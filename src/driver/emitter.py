import os
import json
import struct
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple

from api.hw_spec import HardwareSpec
from driver.field_mapper import FieldMapper
from driver.compactor import Compactor
from driver.packer import BitPacker

class LIRPacker:
    """
    AIACC AI-COMPILER EMITTER (100% REPLICATION OF aiacc_refactor PROTOCOL).
    Ensures file layout and sim_config structure match the standard E2E platform.
    """
    def __init__(self, config_dir: str = "hw_config"):
        self.hw_spec = HardwareSpec.load(config_dir)

    def emit_from_stream(self, final_stream: List, case_root: str, program=None):
        # 1. 建立 ACB 标准 7 层目录结构
        root = Path(case_root)
        smc0 = root / "smc_0"
        ur_data_dir = smc0 / "ur_data"
        output_bank_dir = smc0 / "output_bank"
        analysis = smc0 / "analysis"
        
        for d in [root, smc0, ur_data_dir, output_bank_dir, analysis]:
            os.makedirs(d, exist_ok=True)
            
        # 2. 生成标准占位符文件 (对齐协议)
        (root / "input_data.npz").touch(exist_ok=True)
        (root / "golden_data.npz").touch(exist_ok=True)
        (root / "sim_trace.log").touch(exist_ok=True)
        with open(root / "SMC_CoVerification_Report.md", "w") as f:
            f.write("# SMC Co-Verification Report\nStatus: Pending Simulation\n")

        # 构建节点到物理源 ID 的映射 (用于 RtoVR 寻址)
        node_to_src_id = {}
        for op in final_stream:
            m_type = op.op_type.upper()
            # 物理 Netlist 映射：Producers -> Consumers (按照 rtovr_config.yaml 物理编号)
            # UR_READ 产生的数据通过 UR_RDATA 端口进入网络 (Index 30-33)
            if m_type == "UR_READ":
                node_to_src_id[op.node_id] = 30 + (op.metadata.get("iq_id", 200) - 200)
            # 算子输出端口 (Index 1-29)
            elif m_type == "FPMUL":
                node_to_src_id[op.node_id] = 3 # DR_FPMUL_D
            elif m_type == "FPADD":
                node_to_src_id[op.node_id] = 4 # DR_FPADD_D
            elif m_type == "INTADD":
                node_to_src_id[op.node_id] = 0 # DR_INTADD_D0

        print(f"\n[EMITTER] Scaling for ACB standard layout: {case_root}")
        
        module_streams: Dict[str, List[Dict]] = {}
        for op in final_stream:
            m_name = op.op_type.upper()
            
            # --- 物理 IR 端口映射 (Standard Logic) ---
            if m_name == "UR_READ":
                iq_idx = op.metadata.get("iq_id", 200) - 200
                iq_name = f"ur-r{iq_idx}"
            elif m_name == "UR_WRITE":
                iq_idx = op.metadata.get("iq_id", 204) - 204
                iq_name = f"ur-w{iq_idx}"
            elif m_name == "RTOVR":
                slot = op.metadata.get("slot_id", 0)
                iq_name = f"rtovr-{slot}"
                # 🛸 关键修复：解析 RtoVR 依赖的物理源 ID
                deps = op.metadata.get("depends_on", [])
                if deps and deps[0] in node_to_src_id:
                    op.metadata["p_src_id"] = node_to_src_id[deps[0]]
            else:
                iq_name = m_name.lower()
            
            # 获取硬件配置
            lookup_base = m_name
            lookup_name = f"IQ_{lookup_base}"
            if lookup_name not in self.hw_spec.iqs:
                if "RTOVR" in lookup_base: lookup_name = "IQ_RTOVR"
                else: continue
            
            module_spec = self.hw_spec.modules.get(m_name)
            if not module_spec: continue
            
            payload = FieldMapper.map_op_to_payload(op, module_spec)
            module_streams.setdefault(iq_name, []).append({
                "t": op.metadata["global_t"], 
                "payload": payload,
                "origin_op_type": m_name,
                "iq_conf": self.hw_spec.iqs[lookup_name]
            })
            
        iq_init = {}
        for iq_name, stream in module_streams.items():
            iq_conf = stream[0]["iq_conf"]
            iq_reg = self.hw_spec.system_core.get('iq_registry', {})
            reg_width = iq_reg.get(iq_name.upper().replace('_', '-'), 0)
            if reg_width > 0: iq_conf.BITS_CODE = reg_width
            stream.sort(key=lambda x: x["t"])
            compressed = Compactor.compress_iq_stream(stream, iq_conf)
            
            # 生成标准的 iq_unit.hex
            hex_lines = []
            for cmd in compressed:
                val = BitPacker.pack_capsule(
                    iq_name=iq_name, iq_conf=iq_conf, payload=cmd["payload"], 
                    loops=cmd["loops"], dly=cmd["dly"]
                )
                # 🛸 关键修复：NOP 的 VLD 位在 Header(Bit 40)。对于 LSB 对齐指令，它是 bits_code + 40
                if cmd.get("valid") == 0: 
                    val &= ~(1 << (iq_conf.BITS_CODE + 40))
                hex_lines.append(f"{val:032x}")
                
            hex_file_name = f"iq_{iq_name.replace('-','_')}.hex"
            # 指令文件位于 smc_0 根下
            with open(smc0 / hex_file_name, "w") as f:
                f.write("\n".join(hex_lines) + "\n")
            
            # sim_config.json 路径对齐
            iq_init[iq_name] = f"smc_0/{hex_file_name}"
            print(f"  > [smc_0/{iq_name:<15}] Hex: {len(compressed):<4}")

        # 3. 导出 UR 初始化数据 (100% 对齐 128-bit 硬件位宽)
        ur_init = {}
        if program and hasattr(program, 'memory_allocator'):
            for b_id, storage in program.memory_allocator.banks.items():
                hex_name = f"bank{b_id}.hex"
                with open(ur_data_dir / hex_name, "w") as f:
                    for entry in storage:
                        # AIACC V5.0 每个 Bank 地址对应 128-bit Vector (4 x FP32)
                        # 支持 list/ndarray 对齐模式 或 scalar 填充模式
                        if isinstance(entry, (list, np.ndarray)):
                            # 如果是 4 个元素，按照 [E0, E1, E2, E3] -> LSB to MSB 顺序打包
                            packed_val = 0
                            for i, val in enumerate(entry):
                                if i >= 4: break
                                if isinstance(val, (float, np.float32, np.float64)):
                                    bits = struct.unpack("<I", struct.pack("<f", float(val)))[0]
                                else:
                                    bits = int(val) & 0xFFFFFFFF
                                packed_val |= (bits << (i * 32))
                            f.write(f"{packed_val:032x}\n")
                        else:
                            # Scalar 情况：补齐高位 96-bit 为 0
                            if isinstance(entry, (float, np.float32, np.float64)):
                                bits = struct.unpack("<I", struct.pack("<f", float(entry)))[0]
                            else:
                                bits = int(entry) & 0xFFFFFFFF
                            f.write(f"{bits:032x}\n")
                ur_init[str(b_id)] = f"smc_0/ur_data/{hex_name}"

        # 4. 生成 Case Manifest (Root/sim_config.json)
        sim_config = {
            "smc_id": 0,
            "iq_init": iq_init,
            "ur_init": ur_init,
            "max_cycles": 120, # 对齐审计需求，缩短仿真时间
            "tensors": []
        }
        
        if program:
            for n, tensor in getattr(program, 'tensors', {}).items():
                sim_config["tensors"].append({
                    "name": n, "bank": tensor.bank_id, "addr": tensor.offset,
                    "shape": tensor.shape, "role": "input" if tensor.initial_data is not None else "output"
                })
        
        with open(root / "sim_config.json", "w") as f:
            json.dump(sim_config, f, indent=4)
        
        print(f"\n✅ REPLICATION SUCCESS. Standard Case Manifest locked at {root}")
