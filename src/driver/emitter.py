import os
import json
import struct
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

    def emit_from_stream(self, final_stream: List, output_root: str, program=None):
        # 1. 建立 ACB 目录结构 (Root -> smc_0, analysis)
        root = Path(output_root)
        smc0 = root / "smc_0"
        analysis = root / "analysis"
        os.makedirs(smc0, exist_ok=True)
        os.makedirs(analysis, exist_ok=True)
        
        print(f"\n[EMITTER] Scaling for ACB standard layout: {output_root}")
        
        module_streams: Dict[str, List[Dict]] = {}
        for op in final_stream:
            m_name = op.op_type.upper()
            
            # --- 物理 IR 端口映射 (Standard Logic) ---
            if m_name == "UR_READ":
                iq_name = "ur-r0" 
            elif m_name == "UR_WRITE":
                iq_name = "ur-w0"
            elif m_name == "RTOVR":
                slot = op.metadata.get("slot_id", 0)
                iq_name = f"rtovr-{slot}"
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
            stream.sort(key=lambda x: x["t"])
            compressed = Compactor.compress_iq_stream(stream, iq_conf)
            
            # 生成标准的 iq_unit.hex
            hex_lines = []
            for cmd in compressed:
                val = BitPacker.pack_capsule(
                    iq_name=iq_name, iq_conf=iq_conf, payload=cmd["payload"], 
                    loops=cmd["loops"], dly=cmd["dly"]
                )
                if cmd.get("valid") == 0: val &= ~(1 << 40)
                hex_lines.append(f"{val:032x}")
                
            hex_file_name = f"iq_{iq_name.replace('-','_')}.hex"
            with open(smc0 / hex_file_name, "w") as f:
                f.write("\n".join(hex_lines) + "\n")
            
            # sim_config.json 中的路径映射：必须包含子目录前缀
            iq_init[iq_name] = f"smc_0/{hex_file_name}"
            print(f"  > [smc_0/{iq_name:<15}] Hex: {len(compressed):<4}")

        # 2. 导出 UR 初始化数据 (Root 目录下的 ur_bankN.hex)
        ur_init = {}
        if program and hasattr(program, 'memory_allocator'):
            for b_id, storage in program.memory_allocator.banks.items():
                hex_name = f"ur_bank{b_id}.hex"
                with open(smc0 / hex_name, "w") as f:
                    for val in storage:
                        bits = struct.unpack(">I", struct.pack(">f", val))[0]
                        f.write(f"{bits:08x}\n")
                ur_init[str(b_id)] = f"smc_0/{hex_name}"

        # 3. 生成 Case Manifest (Root/sim_config.json)
        tensors = []
        if program:
            for n, tensor in program.tensors.items():
                tensors.append({
                    "name": n, "bank": tensor.bank_id, "addr": tensor.offset,
                    "shape": tensor.shape, "role": "input" if tensor.initial_data is not None else "output"
                })
        
        sim_config = {
            "smc_id": 0,
            "iq_init": iq_init,
            "ur_init": ur_init,
            "max_cycles": 1000,
            "tensors": tensors
        }
        
        with open(root / "sim_config.json", "w") as f:
            json.dump(sim_config, f, indent=4)
        
        print(f"\n✅ REPLICATION SUCCESS. Standard Case Manifest locked at {root}")
