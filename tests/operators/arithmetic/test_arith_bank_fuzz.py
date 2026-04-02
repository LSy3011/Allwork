import numpy as np
import pytest
import itertools
from api.op_tester import OperatorPhysicalTester
from library.atomic.arithmetic.basic_ops import get_add_dfg, get_mul_dfg, get_reciprocal_dfg
from library.atomic.activations.relu import get_relu_dfg
from library.atomic.transcendentals.sin import get_sin_8order_dfg
from tests.operators.utils.data_generator import ArithTestDataGenerator

class TestArithmeticBankRobustness:
    """
    基础算子物理鲁棒性测试 (ADPC 标准版)。
    [V3.2 更新]: 纳入 Sin 8阶泰勒展开验证。
    """

    @pytest.mark.parametrize("op_type", ["add", "mul", "rec", "relu", "sin"])
    @pytest.mark.parametrize("data_mode", ["fuzz", "boundary"])
    def test_arithmetic_ccug_fuzzing(self, op_type, data_mode):
        """对基础算子执行 CCUG 角色自适应压力测试"""
        tester = OperatorPhysicalTester()
        
        # 1. 算法选择与角色对应的参考模型
        if op_type == "add":
            dfg_ops = get_add_dfg()
            role_to_input = {"operand_a": "x_val", "operand_b": "y_val"}
            ref_fn = lambda d: d["operand_a"] + d["operand_b"]
        elif op_type == "mul":
            dfg_ops = get_mul_dfg()
            role_to_input = {"operand_a": "x_val", "operand_b": "y_val"}
            ref_fn = lambda d: d["operand_a"] * d["operand_b"]
        elif op_type == "rec":
            dfg_ops = get_reciprocal_dfg()
            role_to_input = {"operand_a": "x_val", "initial_guess": "y0_val"}
            ref_fn = lambda d: d["initial_guess"] * (2.0 - d["operand_a"] * d["initial_guess"])
        elif op_type == "relu":
            dfg_ops = get_relu_dfg()
            role_to_input = {"operand_a": "x_val"}
            ref_fn = lambda d: np.where(np.isnan(d["operand_a"]), 0.0, np.maximum(0, d["operand_a"]))
        elif op_type == "sin":
            dfg_ops = get_sin_8order_dfg()
            role_to_input = {"operand_a": "x_val"}
            ref_fn = lambda d: np.sin(d["operand_a"])
        else:
            raise ValueError(f"Unknown op_type: {op_type}")

        # 2. 扫描 DFG 提取语义映射 (支持一角色多节点)
        # 🎉 支持 V3.3 物理保真 DFG：同一个 role 可能对应多个 UR_READ
        role_to_names = {} 
        ccug_nodes = []
        for op in dfg_ops:
            role = op.metadata.get("role")
            if role:
                if role not in role_to_names:
                    role_to_names[role] = []
                role_to_names[role].append(op.trace_id)
                ccug_nodes.append(op.trace_id)
            elif op.metadata.get("is_const"):
                # 常量也作为冲突源节点
                ccug_nodes.append(op.trace_id)

        # 3. 产出物理独立的多通道测试数据
        raw_data = ArithTestDataGenerator.generate(op_type, mode=data_mode, size=4)
        
        # 4. 角色 -> 物理节点 -> 数据通道映射
        input_data = {}
        role_data = {} # 用于 ref_fn 的计算
        
        # 建立 role <-> data_channel 的标准映射关系
        role_data_channel_map = {
            "operand_a": "ch_0",
            "operand_b": "ch_1",
            "initial_guess": "ch_1" 
        }
        
        # 🛸 核心修复：遍历所有节点，确保所有 shared-role 节点都喂入相同数据
        for role, p_names in role_to_names.items():
            ch_key = role_data_channel_map.get(role)
            if ch_key in raw_data:
                val = raw_data[ch_key]
                role_data[role] = val
                for p_name in p_names:
                    input_data[p_name] = val

        # 5. 自动化常量提取
        constants = {}
        for op in dfg_ops:
            if op.metadata.get("is_const"):
                constants[op.trace_id] = float(op.metadata.get("value", 0.0))
        
        # 6. 动态计算预期值 (角色驱动)
        with np.errstate(divide='ignore', invalid='ignore'):
            golden_y = ref_fn(role_data)
        
        # 7. 生成 CCUG 冲突组合 (2^K)
        combinations = list(itertools.product([0, 1], repeat=len(ccug_nodes)))
        print(f"\n🚀 [ADPC-ST-V3] CCUG ({data_mode}) | OP: {op_type.upper()} | NameMap: {role_to_names}")

        for i, config_tuple in enumerate(combinations):
            static_banks = {node: config_tuple[j] for j, node in enumerate(ccug_nodes)}
            
            success, msg = tester.run_full_stack_verification(
                op_name=f"arith/{op_type}/{data_mode}_ccug_{i}",
                dfg_ops=dfg_ops,
                input_data=input_data,
                golden_y=golden_y,
                constants=constants,
                static_banks=static_banks,
                strategies=["balanced"] 
            )
            
            assert success, f"🚨 {op_type} CCUG 失败: {msg}"

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
