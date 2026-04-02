import numpy as np
import pytest
from library.atomic.activations.relu import get_relu_dfg
from api.op_tester import OperatorPhysicalTester

class TestReLUPhysical:
    def test_relu_basic_phys(self):
        """
        验证 ReLU 算子在真实物理模拟下的跨单元 (FPADD -> LOGIC) 协同。
        """
        tester = OperatorPhysicalTester()
        dfg_ops = get_relu_dfg()
        
        # 准备测试数据: 4路矢量
        x = np.array([-1.5, 0.0, 1.5, 5.0], dtype=np.float32)
        y_ref = np.maximum(0, x).astype(np.float32)
        
        # 定义常量 (Bank-ID 需要与 get_relu_dfg 对齐)
        # c1_rd 对应 zero_rd
        constants = {
            "zero_rd": 0.0
        }
        
        # 执行全栈验证
        print("\n🚀 [INDUSTRIAL-SIM] Testing ReLU (FP32 Mode) ...")
        success, msg = tester.run_full_stack_verification(
            op_name="relu/phys_basic",
            dfg_ops=dfg_ops,
            input_data={"x_rd": x},
            golden_y=y_ref,
            constants=constants,
            strategies=["balanced"] # 采用平衡策略，确保跨周期协同
        )
        
        assert success, f"🚨 ReLU 物理验证失败: {msg}"
        print(f"✅ ReLU 物理验证成功: {msg}")

if __name__ == "__main__":
    pytest.main([__file__, "-s"])
