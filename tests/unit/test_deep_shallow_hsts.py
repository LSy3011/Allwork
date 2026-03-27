import unittest
import os
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from library.atomic.sin import get_sin_8order_dfg
from library.atomic.linear import get_linear_dfg
from orchestrator.stitcher import HSTSStitcher
from orchestrator.colorer import BalancedBankColorer
from orchestrator.memory_allocator import MemoryAllocator
from orchestrator.spec_renderer import HSTSSpecRenderer

class TestDeepShallowHSTS(unittest.TestCase):
    def test_hetero_sin_linear_sin(self):
        spec = HardwareSpec.load("hw_config")
        synthesizer = SMTSynthesizer(spec)
        stitcher = HSTSStitcher()
        mem_alloc = MemoryAllocator()
        
        # 1. 准备异质 DFG 并自动均衡着色
        sin_ops = BalancedBankColorer.color_dfg(get_sin_8order_dfg())
        lin_ops = BalancedBankColorer.color_dfg(get_linear_dfg())
        
        # 2. SMT 求解
        sin_res = synthesizer.solve(sin_ops, "extreme")
        lin_res = synthesizer.solve(lin_ops, "extreme")
        self.assertIsNotNone(sin_res)
        self.assertIsNotNone(lin_res)
        
        # 3. 缝合实验: Sin -> Linear -> Sin
        stitcher.add_operator("Sin_A", sin_ops, sin_res)
        offset_b = stitcher.add_operator("Lin_B", lin_ops, lin_res)
        offset_c = stitcher.add_operator("Sin_C", sin_ops, sin_res)
        
        print(f"\n[HETERO] Offsets: Sin_A(0), Lin_B({offset_b}), Sin_C({offset_c})")
        
        # 4. 空间分配
        final_stream = stitcher.get_final_stream()
        for op in final_stream:
            if op.op_type in ["UR_READ", "UR_WRITE"]:
                b_id = op.metadata["bank_id"]
                inst = op.metadata["instance_name"]
                is_c = op.node_id.startswith("c") or "mean" in op.node_id or "scale" in op.node_id
                p_addr = mem_alloc.allocate(op.node_id, b_id, inst_idx=inst, is_const=is_c)
                op.metadata["p_addr"] = p_addr

        # 5. 导出异构规格书
        build_dir = Path(__file__).parent.parent.parent / "build" / "test" / "hetero_sin_linear"
        os.makedirs(build_dir, exist_ok=True)
        spec_path = str(build_dir / "hetero.precision.spec")
        HSTSSpecRenderer.render_to_file("hetero_workload", final_stream, spec_path)
        
        print(f"✅ Hetero Spec Exported: {spec_path}")
        print(mem_alloc.get_layout())

if __name__ == "__main__":
    unittest.main()
