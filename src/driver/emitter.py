import os, json, struct, numpy as np, copy
from pathlib import Path
from typing import List, Dict, Tuple, Any

from api.hw_spec import HardwareSpec
from driver.field_mapper import FieldMapper
from driver.compactor import Compactor
from driver.packer import BitPacker
from driver.hw_constants import (
    IQ_BASE_UR_READ, IQ_BASE_UR_WRITE, 
    PORT_OFFSET_UR_RDATA, PORT_ID_FPMUL_D, PORT_ID_FPADD_D, PORT_ID_INTADD_D0,
    OFFSET_VLD, DEFAULT_BITS_CODE, DEFAULT_MAX_CYCLES
)
from ir.lir.lir_op import PhysicalInstruction, SMIHeader

class LIRPacker:
    """
    AIACC AI-COMPILER EMITTER (V5.0: Pure Serialization).
    负责将已经物理定稿的 PhysicalInstruction 序列化为 Hex 文件。
    不再承担任何路由推导或时序逻辑。
    """
    def __init__(self, config_dir: str = "hw_config"):
        self.hw_spec = HardwareSpec.load(config_dir)

    def emit_from_instructions(self, instructions: List[PhysicalInstruction], case_root: str, program=None):
        root = Path(case_root)
        smc0 = root / "smc_0"
        ur_dir, bank_dir, analysis = smc0/"ur_data", smc0/"output_bank", smc0/"analysis"
        for d in [smc0, ur_dir, bank_dir, analysis]: os.makedirs(d, exist_ok=True)
        (root / "sim_trace.log").touch()
        
        # 1. 按物理 IQ 分桶
        iq_buckets: Dict[str, List[PhysicalInstruction]] = {}
        for instr in instructions:
            iq_buckets.setdefault(instr.iq_name, []).append(instr)

        # 2. 二进制打包与 Hex 生成
        iq_init = {}
        for iq_name, stream in iq_buckets.items():
            # 严格按时间排序
            stream.sort(key=lambda x: x.abs_t)
            
            # 获取硬件规格
            # 兼容性：从 iq_name 反查 iq_conf
            # logic_0 -> IQ_LOGIC
            lookup_type = iq_name.split("_")[0].upper()
            iq_lookup = f"IQ_{lookup_type}"
            if iq_lookup not in self.hw_spec.iqs:
                # 兜底：如果是未知 IQ，尝试从 modules 找
                iq_conf = type('obj', (object,), {'BITS_CODE': 128})
            else:
                iq_conf = self.hw_spec.iqs[iq_lookup]

            hex_lines = []
            for instr in stream:
                # 调用 BitPacker 进行位拼接
                val = BitPacker.pack_capsule(
                    iq_name=iq_name,
                    iq_conf=iq_conf,
                    payload=instr.payload.get("bits", 0),
                    hw_spec=self.hw_spec,
                    **instr.header.to_dict()
                )
                hex_lines.append(f"{val:032x}")
            
            hex_file_name = f"iq_{iq_name}.hex"
            with open(smc0 / hex_file_name, "w") as f:
                f.write("\n".join(hex_lines) + "\n")
            
            iq_init[iq_name.replace("_", "-")] = f"smc_0/{hex_file_name}"
            print(f"[EMITTER] IQ: {iq_name} | Instructions: {len(hex_lines)}")

        # 3. 导出仿真配置
        actual_max_t = max((instr.abs_t for instr in instructions), default=0)
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
                    packed = 0
                    for i, x in enumerate(entry[:4]):
                        if isinstance(x, int):
                            bits = x & 0xFFFFFFFF
                        else:
                            bits = int(struct.unpack(">I", struct.pack(">f", float(x)))[0]) & 0xFFFFFFFF
                        packed |= (bits << (32 * i))
                    f.write(f"{packed:032x}\n")
        
        for t_name, t_obj in program.tensors.items():
            sim_conf["tensors"].append({
                "name": t_name, "bank": t_obj.bank, "addr": t_obj.offset,
                "shape": t_obj.shape, "role": t_obj.role
            })
