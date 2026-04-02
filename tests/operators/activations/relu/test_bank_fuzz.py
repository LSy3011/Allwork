import pytest
import itertools
import numpy as np
from src.library.atomic.activations.relu import get_relu_dfg
from src.api.op_tester import OperatorPhysicalTester

class TestReLUBankRobustness:
    """
    ReLU 算子 Bank 冲突鲁棒性扫射测站
    验证编译器在 [x_rd, zero_rd] 并发冲突下的调度避让能力。
    """
    def test_relu_ccug_fuzzing(self):
        tester = OperatorPhysicalTester()
        dfg_ops = get_relu_dfg()
        
        # 1. 定义并发冲突节点 (Row 0 Concurrent Group)
        ccug_nodes = ["x_rd", "zero_rd"]
        
        # 2. 生成 2^2 = 4 种 Bank 分配矩阵
        combinations = list(itertools.product([0, 1], repeat=len(ccug_nodes)))
        print(f"\n🚀 [RELU-FUZZ] Starting 4-case CCUG-Matrix sweep for ReLU Physical.")
        
        # 准备物理测试数据
        x = np.array([-1.5, 0.0, 1.5, 5.0], dtype=np.float32)
        y_ref = np.maximum(0, x).astype(np.float32)
        constants = {"zero_rd": 0.0}

        # 3. 循环扫射
        for i, config_tuple in enumerate(combinations):
            static_banks = {node: config_tuple[j] for j, node in enumerate(ccug_nodes)}
            config_str = ", ".join([f"{node}:B{bank}" for node, bank in static_banks.items()])
            
            print(f"CASE [{i}/3]: Running with {config_str} ...")
            
            success, msg = tester.run_full_stack_verification(
                op_name=f"relu/fuzz_ccug/case_{i}",
                dfg_ops=dfg_ops,
                input_data={"x_rd": x},
                golden_y=y_ref,
                constants=constants,
                static_banks=static_banks,
                strategies=["balanced"]
            )
            
            assert success, f"🚨 ReLU Bank 冲突验证失败 [Case {i}]: {msg} ({config_str})"
        
        print("\n✅ [RELU-FUZZ] All 4 CCUG-Matrix cases PASSED with Bit-Perfect Parity.")
