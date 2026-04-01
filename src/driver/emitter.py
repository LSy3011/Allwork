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
        
        # 1. 建立物理端子 ID 映射表 (基于硬件宏定义)
        node_to_src_id = {}
        for op in final_stream:
            m_type = op.op_type.upper()
            if m_type == "UR_READ":
                rid = op.metadata.get("iq_id", IQ_BASE_UR_READ)
                # 计算逻辑：基础端口偏移 + (IQ_ID - 基准偏移)
                iq_idx = rid - IQ_BASE_UR_READ if rid >= IQ_BASE_UR_READ else rid
                node_to_src_id[op.node_id] = PORT_OFFSET_UR_RDATA + iq_idx
            elif m_type == "FPMUL": 
                node_to_src_id[op.node_id] = PORT_ID_FPMUL_D
            elif m_type == "FPADD": 
                node_to_src_id[op.node_id] = PORT_ID_FPADD_D
            elif m_type == "INTADD":
                node_to_src_id[op.node_id] = PORT_ID_INTADD_D0

        # 2. 分桶聚合所有物理 IQ 流
        module_streams = {}
        for op in final_stream:
            m_name = op.op_type.upper()
            
            # --- 物理端口名对齐 (DASH Convention) ---
            if m_name == "UR_READ":
                idx = op.metadata.get("iq_id", IQ_BASE_UR_READ)
                idx = idx - IQ_BASE_UR_READ if idx >= IQ_BASE_UR_READ else idx
                name = f"ur-r{idx}"
            elif m_name == "UR_WRITE":
                idx = op.metadata.get("iq_id", IQ_BASE_UR_WRITE)
                idx = idx - IQ_BASE_UR_WRITE if idx >= IQ_BASE_UR_WRITE else idx
                name = f"ur-w{idx}"
            elif m_name == "RTOVR":
                name = f"rtovr-{op.metadata.get('slot_id', 0)}"
                deps = op.metadata.get("depends_on", [])
                if deps and deps[0] in node_to_src_id:
                    src_id = node_to_src_id[deps[0]]
                    op.metadata["p_src_id"] = src_id
                    # 🚀 物理对齐：直接注入 YAML 期望的字段名，绕过任何驱动推断
                    op.metadata["dr_index"] = src_id 
            else:
                name = m_name.lower()

            lookup = f"IQ_{m_name}"
            if "RTOVR" in m_name: lookup = "IQ_RTOVR"
            if lookup not in self.hw_spec.iqs: continue
            
            mod_spec = self.hw_spec.modules.get(m_name)
            if not mod_spec: continue
            
            payload = FieldMapper.map_op_to_payload(op, mod_spec)
            module_streams.setdefault(name, []).append({
                "t": op.metadata["global_t"], 
                "payload": payload,
                "iq_conf": self.hw_spec.iqs[lookup]
            })

        # 3. 二进制打包与 IQ 初始化记录
        iq_init = {}
        iq_reg = self.hw_spec.system_core.get("iq_registry", {})
        
        for name, stream in module_streams.items():
            # 推导逻辑基类以检索精确位宽
            base_type = "UR_READ" if "ur-r" in name else "UR_WRITE" if "ur-w" in name else "RTOVR" if "rtovr" in name else name.upper()
            bits_code = iq_reg.get(base_type, DEFAULT_BITS_CODE)
            
            # 物理副本隔离保护
            local_iq_conf = copy.deepcopy(stream[0]["iq_conf"])
            local_iq_conf.BITS_CODE = bits_code
            
            stream.sort(key=lambda x: x["t"])
            compressed = Compactor.compress_iq_stream(stream, local_iq_conf)
            
            hex_lines = []
            for cmd in compressed:
                val = BitPacker.pack_capsule(
                    iq_name=name, iq_conf=local_iq_conf, payload=cmd["payload"],
                    loops=cmd["loops"], dly=cmd["dly"]
                )
                
                # 🛸 物理点火：基于 OFFSET_VLD 宏进行状态位注入
                vld_bit = local_iq_conf.BITS_CODE + OFFSET_VLD
                if cmd.get("valid", 1) == 0:
                    val &= ~(1 << vld_bit)
                else:
                    val |= (1 << vld_bit)
                
                hex_lines.append(f"{val:032x}")
            
            hex_file_name = f"iq_{name.replace('-', '_')}.hex"
            with open(smc0 / hex_file_name, "w") as f:
                f.write("\n".join(hex_lines) + "\n")
            
            iq_init[name] = f"smc_0/{hex_file_name}"

        # 4. 导出 sim_config.json
        sim_conf = {
            "smc_id": 0,
            "iq_init": iq_init,
            "ur_init": {str(b): f"smc_0/ur_data/bank{b}.hex" for b in range(8)},
            "max_cycles": DEFAULT_MAX_CYCLES,
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
                    if isinstance(entry, (list, np.ndarray)):
                        packed = sum((int(struct.unpack("<I", struct.pack("<f", x))[0]) & 0xFFFFFFFF) << (32*i) for i, x in enumerate(entry[:4]))
                    else:
                        packed = int(struct.unpack("<I", struct.pack("<f", entry))[0]) & 0xFFFFFFFF
                    f.write(f"{packed:032x}\n")
        
        for t_name, t_obj in program.tensors.items():
            sim_conf["tensors"].append({
                "name": t_name, "bank": t_obj.bank, "addr": t_obj.offset,
                "shape": t_obj.shape, "role": t_obj.role
            })
