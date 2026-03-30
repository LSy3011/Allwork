import unittest
import os
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from orchestrator.stitcher import HSTSStitcher
from orchestrator.colorer import BalancedBankColorer
from orchestrator.memory_allocator import MemoryAllocator
from orchestrator.verifier import HSTSVerifier
from driver.emitter import LIRPacker

# 汇聚 Batch 6 的顶级负载 (最强压缩潜力: 存在大量跨算子 Gap 和 Loop 项)
from library.atomic.transcendentals.activation import get_mish_industrial_dfg, get_gelu_industrial_dfg, get_elu_industrial_dfg
from library.atomic.transcendentals.special_math import get_erf_industrial_dfg
from library.atomic.trig_extra.precision_trig import get_atan2_industrial_dfg
from library.atomic.transcendentals.sin_cos_exp import get_sin_industrial_dfg, get_exp_industrial_dfg

class TestLIREmitterFullStack(unittest.TestCase):
    def test_full_stack_binary_emission(self):
        spec = HardwareSpec.load("hw_config")
        synthesizer = SMTSynthesizer(spec)
        stitcher = HSTSStitcher()
        mem_alloc = MemoryAllocator()
        
        # 1. 组建 10 算子全量异构压力负载
        workload = [
            ("Sin", get_sin_industrial_dfg()), ("Mish", get_mish_industrial_dfg()), 
            ("Gelu", get_gelu_industrial_dfg()), ("Elu", get_elu_industrial_dfg()), 
            ("Erf", get_erf_industrial_dfg()), ("Atan2", get_atan2_industrial_dfg()),
            ("Exp", get_exp_industrial_dfg()),
            ("Cos", get_sin_industrial_dfg()) # 代指
        ]
        
        print(f"\n[FULL-STACK MISSION] Starting AIACC V5.0 End-to-End Compile...")
        
        for name, ops in workload:
            balanced_ops = BalancedBankColorer.color_dfg(ops)
            res = synthesizer.solve(balanced_ops)
            self.assertIsNotNone(res, f"Synthesis failed for {name}")
            stitcher.add_operator(name, balanced_ops, res)
            
        final_stream = stitcher.get_final_stream()
        
        # 2. 物理寻址
        for op in final_stream:
            if op.op_type in ["UR_READ", "UR_WRITE"]:
                is_c = any(k in op.node_id for k in ["c", "tc", "ec", "bias", "pi", "alpha"])
                op.metadata["p_addr"] = mem_alloc.allocate(op.node_id, op.metadata["bank_id"], 
                                                          inst_idx=op.metadata["instance_name"], is_const=is_c)

        # 3. 终极审计
        is_valid = HSTSVerifier.verify(final_stream, spec)
        self.assertTrue(is_valid, "Physical integrity violation detected before emission!")

        # 4. 二进制驱动封装 (LIR Packer)
        packer = LIRPacker("hw_config")
        bin_out_dir = Path(__file__).parent.parent.parent / "build" / "bin" / "sprint5_full"
        packer.emit_from_stream(final_stream, str(bin_out_dir))
        
        # 验证 bin 文件生成情况
        bin_files = list(bin_out_dir.glob("*.bin"))
        self.assertTrue(len(bin_files) > 0, "No binary files emitted!")
        print(f"\n[RESULT] Sprint 5 Full-Stack INTEGRATED SUCCESS. Output Directory: {bin_out_dir}")

if __name__ == "__main__":
    unittest.main()
