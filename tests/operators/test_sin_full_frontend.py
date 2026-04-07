import sys
import numpy as np
from pathlib import Path
import sympy as sp

ws = Path.cwd()
sys.path.append(str(ws / "src"))

from tools.standard_pipeline_runner import StandardPipelineRunner
from frontend import GraphCompiler

def get_frontend_dfg():
    compiler = GraphCompiler()
    x = sp.Symbol('x')
    
    # 采用最优拓扑的 Horner 嵌套形式（Sympy 会自动折叠解析）
    formula = x * (1.0 - x**2 * (0.1666666667 - x**2 * 0.0083333333))
    
    # 展示预着色：将输入强制放在 Bank 1 以验证 Bank Allocator 解耦能力
    inputs_meta = { x: {"manual_bank": 1, "role": "operand_a"} }
    
    # 一键编译全图
    dfg = compiler.compile(inputs_meta, formula, out_name="y")
    return dfg


def test_sin_frontend_pipeline():
    print("🚀 [Frontend-To-Backend] Starting Full Pipeline Auto-Compiler...")
    
    runner = StandardPipelineRunner(hw_config="hw_config", build_root="build/standard")
    sys.path.append(str(Path(__file__).parent))
    from utils.data_generator import ArithTestDataGenerator
    
    gen_data = ArithTestDataGenerator.generate("sin", mode="fuzz", size=4)
    test_x = gen_data["ch_0"]
    input_data = {"x": test_x}
    golden_y = np.sin(test_x)

    success = runner.run_full_pipeline(
        op_name="sin_taylor_auto",
        get_dfg_fn=get_frontend_dfg,
        input_data=input_data,
        golden_y=golden_y
    )

    if success:
        print("\n✅ Auto Frontend Sin(x) Pipeline Verification: PASSED")
    else:
        print("\n❌ Auto Frontend Sin(x) Pipeline Verification: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    test_sin_frontend_pipeline()
