import pytest
import itertools
import numpy as np
from src.library.atomic.arithmetic.basic_ops import get_add_dfg, get_mul_dfg, get_reciprocal_dfg
from src.api.op_tester import OperatorPhysicalTester

class TestArithmeticBankRobustness:
    """
    基础算术算子 Bank 冲突鲁棒性扫射 (Add/Mul/Rec)
    验证 x_rd 与 y_rd/c_rd 在同一物理 Bank 时的串行化能力。
    """
    def setup_method(self):
        self.tester = OperatorPhysicalTester()

    @pytest.mark.parametrize("op_type", ["add", "mul", "rec"])
    def test_arithmetic_ccug_fuzzing(self, op_type):
        if op_type == "add":
            dfg_ops = get_add_dfg()
            ccug_nodes = ["x_rd", "y_rd"]
            input_data = {"x_rd": [10.0]*4, "y_rd": [5.5]*4}
            golden_y = [15.5]*4
            constants = {}
        elif op_type == "mul":
            dfg_ops = get_mul_dfg()
            ccug_nodes = ["x_rd", "y_rd"]
            input_data = {"x_rd": [2.0]*4, "y_rd": [4.0]*4}
            golden_y = [8.0]*4
            constants = {}
        else: # rec
            dfg_ops = get_reciprocal_dfg()
            ccug_nodes = ["x_rd", "y0_rd", "c2_rd"]
            input_data = {"x_rd": [4.0]*4, "y0_rd": [0.25]*4}
            golden_y = [0.25]*4 # y0 * (2 - x*y0)
            constants = {"c2_rd": 2.0}

        # 生成冲突组合 (2^K)
        combinations = list(itertools.product([0, 1], repeat=len(ccug_nodes)))
        print(f"\n🚀 [ARITH-FUZZ] Starting {len(combinations)} cases for {op_type}.")

        for i, config_tuple in enumerate(combinations):
            static_banks = {node: config_tuple[j] for j, node in enumerate(ccug_nodes)}
            config_str = ", ".join([f"{node}:B{bank}" for node, bank in static_banks.items()])
            
            success, msg = self.tester.run_full_stack_verification(
                op_name=f"arith/{op_type}/fuzz_{i}",
                dfg_ops=dfg_ops,
                input_data=input_data,
                golden_y=golden_y,
                constants=constants,
                static_banks=static_banks,
                strategies=["balanced"]
            )
            assert success, f"🚨 {op_type} Bank 冲突验证失败: {config_str}"
        print(f"✅ [PASS] {op_type} is robust against all bank combinations.")
