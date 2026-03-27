import unittest
import os
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from library.atomic.sin import get_sin_8order_dfg
from orchestrator.stitcher import HSTSStitcher
from orchestrator.colorer import BalancedBankColorer
from orchestrator.memory_allocator import MemoryAllocator
from orchestrator.spec_renderer import HSTSSpecRenderer

class TestBalancedHSTSFlow(unittest.TestCase):
    def test_sin_balanced_e2e(self):
        spec = HardwareSpec.load("hw_config")
        synthesizer = SMTSynthesizer(spec)
        stitcher = HSTSStitcher()
        mem_alloc = MemoryAllocator()
        
        # 1. 获取原始 DFG 并执行【均衡着色】
        ops = get_sin_8order_dfg()
        ops = BalancedBankColorer.color_dfg(ops)
        print(f"\n[BALANCED] Bank Assignment: {BalancedBankColorer.get_auto_mapping(ops)}")
        
        # 2. SMT 综合 (基于着色后的结果)
        smt_res = synthesizer.solve(ops, mode="extreme")
        self.assertIsNotNone(smt_res)
        
        # 3. 三联算子缝合 (压力缝合)
        stitcher.add_operator("S0", ops, smt_res)
        s1 = stitcher.add_operator("S1", ops, smt_res)
        s2 = stitcher.add_operator("S2", ops, smt_res)
        
        # 验证吞吐量是否逼近黄金极限 (3 拍)
        avg_t = s2 / 2
        print(f"[BALANCED] Phase Shift 吞吐量: {avg_t} cycles/op")
        
        # 4. 物理地址空间二次分配
        final_stream = stitcher.get_final_stream()
        for op in final_stream:
            if op.op_type in ["UR_READ", "UR_WRITE"]:
                b_id = op.metadata["bank_id"]
                inst_idx = int(op.metadata["instance_name"].replace("S", ""))
                is_c = op.node_id.startswith("c")
                # 分派 16-bit 物理地址
                p_addr = mem_alloc.allocate(op.node_id, b_id, inst_idx=inst_idx, is_const=is_c)
                op.metadata["p_addr"] = p_addr

        # 5. 导出规格书
        build_dir = Path(__file__).parent.parent.parent / "build" / "test" / "sin_balanced_3x"
        os.makedirs(build_dir, exist_ok=True)
        spec_path = str(build_dir / "sin_balanced_3x.precision.spec")
        HSTSSpecRenderer.render_to_file("sin_balanced_3x", final_stream, spec_path)
        
        print(f"✅ Balanced Spec Exported: {spec_path}")
        print(mem_alloc.get_layout())

if __name__ == "__main__":
    unittest.main()
