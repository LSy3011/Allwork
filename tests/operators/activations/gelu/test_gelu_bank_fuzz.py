import pytest
import itertools
import numpy as np
from src.library.atomic.transcendentals.activation import get_gelu_industrial_dfg
from src.api.op_tester import OperatorPhysicalTester

class TestGeluBankRobustness:
    """
    Gelu 算子 Bank 冲突扫射
    验证 x_in, const_1702 的并发处理能力。
    """
    def test_gelu_ccug_fuzzing(self):
        tester = OperatorPhysicalTester()
        dfg_ops = get_gelu_industrial_dfg()
        
        # 识别并发冲突团
        ccug_nodes = ["x_in", "const_1702"]
        combinations = list(itertools.product([0, 1, 2, 3], repeat=len(ccug_nodes))) # 4 个 Bank
        print(f"\n🚀 [GELU-FUZZ] Starting {len(combinations)} cases for Gelu Physical.")
        
        input_data = {"x_in": [0.0]*4}
        golden_y = [0.0]*4
        constants = {"const_1702": 1.702}

        for i, config_tuple in enumerate(combinations):
            static_banks = {node: config_tuple[j] for j, node in enumerate(ccug_nodes)}
            success, msg = tester.run_full_stack_verification(
                op_name=f"activations/gelu/fuzz_{i}",
                dfg_ops=dfg_ops,
                input_data=input_data,
                golden_y=golden_y,
                constants=constants,
                static_banks=static_banks,
                strategies=["balanced"]
            )
            assert success, f"🚨 Gelu Bank 冲突验证失败: {static_banks}"
        print("✅ [PASS] Gelu is robust against all bank combinations.")
