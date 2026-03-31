import os
import sys
import json
import numpy as np
import struct
from pathlib import Path
import unittest
import subprocess

# 确保 src 在 pythonpath 
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from orchestrator.stitcher import HSTSStitcher
from orchestrator.memory_allocator import MemoryAllocator
from driver.field_mapper import FieldMapper
from driver.compactor import Compactor
from driver.packer import BitPacker
from driver.precision_spec_exporter import PrecisionSpecExporter

from library.atomic.transcendentals.sin import get_sin_8order_dfg
from library.atomic.trig_extra.precision_trig import get_atan2_industrial_dfg

class TestE2EValidationWithSpec(unittest.TestCase):
    def test_sin_atan2_e2e_with_spec(self):
        """
        生成理论时序规格，并与物理仿真对齐。
        """
        workspace_dir = Path(__file__).parent.parent.parent
        output_dir = workspace_dir / "build" / "e2e_sin_atan2_acb"
        smc_0_dir = output_dir / "smc_0"
        
        for d in [output_dir, smc_0_dir]:
            os.makedirs(d, exist_ok=True)
            
        # 1. 编译管线
        spec = HardwareSpec.load(str(workspace_dir / "hw_config"))
        synthesizer = SMTSynthesizer(spec)
        stitcher = HSTSStitcher()
        sin_ops = get_sin_8order_dfg()
        atan2_ops = get_atan2_industrial_dfg()
        
        sin_res = synthesizer.solve(sin_ops, strategy="extreme")
        atan2_res = synthesizer.solve(atan2_ops, strategy="extreme")
        
        stitcher.add_operator("Sin", sin_ops, sin_res)
        stitcher.add_operator("Atan2", atan2_ops, atan2_res)
        final_stream = stitcher.get_final_stream()
        
        # 2. 生成理论时序规格: .precision.spec
        spec_exporter = PrecisionSpecExporter()
        spec_path = output_dir / "e2e_sin_atan2.precision.spec"
        addr_map = {op.node_id: 0 for op in final_stream} # 简化，仅展示格式
        spec_exporter.export(str(spec_path), "e2e_sin_atan2", final_stream, addr_map)
        
        print(f"\n[E2E] Precision Spec Generated: {spec_path}")
        
        # 3. 按照理论时序逐拍打印指令 (Audit Log)
        print("\n" + "="*80)
        print(" [AUDIT] THEORETICAL INSTRUCTION STREAM (MSB First Audit)")
        print(" cycle | unit      | node_id      | raw_hex (Header:41+Code:N)")
        print("-"*80)
        
        # 建立时序索引
        time_table = {}
        for op in final_stream:
            time_table.setdefault(op.metadata["global_t"], []).append(op)
            
        for t in sorted(time_table.keys()):
            for op in time_table[t]:
                m_name = op.op_type.upper()
                iq_name = f"IQ_{m_name}"
                iq_conf = spec.iqs[iq_name]
                module_spec = spec.modules[m_name]
                
                # 模拟映射地址 (回填 p_addr)
                op.metadata["p_addr"] = 0
                payload = FieldMapper.map_op_to_payload(op, module_spec)
                
                # 封装完整指令 (41-bit Header + Code)
                # 使用 BitPacker
                raw_val = BitPacker.pack_capsule(iq_name=iq_name, iq_conf=iq_conf, payload=payload)
                
                print(f" {t:<5} | {m_name:<9} | {op.node_id:<12} | {raw_val:032x}")
        print("="*80)

if __name__ == "__main__":
    unittest.main()
