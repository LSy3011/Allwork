import sys
import numpy as np
from pathlib import Path

# 设置搜索路径以引用 src 目录
ws = Path.cwd()
sys.path.append(str(ws / "src"))

from tools.standard_pipeline_runner import StandardPipelineRunner
from library.atomic.transcendentals.sin import get_sin_standard_dfg

def test_sin_standard_pipeline():
    """
    AIACC Sin(x) 标准流水线回归测试 (V6.0)。
    验证：MIR -> Bank-Algo -> SMT -> LIR -> SMC 的全链路对齐。
    """
    # 1. 初始化流水线
    runner = StandardPipelineRunner(hw_config="hw_config", build_root="build/standard")

    from utils.data_generator import ArithTestDataGenerator
    
    # 2. 准备数据场景
    # 使用标准数据生成器，生成一个包含4个元素的向量（对齐 128-bit 数据线）
    gen_data = ArithTestDataGenerator.generate("sin", mode="fuzz", size=4)
    test_x = gen_data["ch_0"]
    input_data = {"x": test_x}
    golden_y = np.sin(test_x)

    # 3. 启动 5-Stage 标准化生产线
    success = runner.run_full_pipeline(
        op_name="sin_taylor",
        get_dfg_fn=get_sin_standard_dfg,
        input_data=input_data,
        golden_y=golden_y
    )

    if success:
        print("\n✅ Standard Sin(x) Pipeline Verification: PASSED")
    else:
        print("\n❌ Standard Sin(x) Pipeline Verification: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    test_sin_standard_pipeline()
