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
    
    # 泰勒展开 9阶表达式 (即包含 5 个子项的精细多项式逼近)
    # x - x^3/3! + x^5/5! - x^7/7! + x^9/9!
    # = x * (1 - x^2 * (1/6 - x^2 * (1/120 - x^2 * (1/5040 - x^2 / 362880))))
    c_inv6 = 0.1666666667
    c_inv120 = 0.0083333333
    c_inv5040 = 0.0001984127
    c_inv362880 = 2.75573192e-06
    
    # 全部依靠前端强大的表达式展开引擎，无任何手动连线！
    formula = x * (1.0 - x**2 * (c_inv6 - x**2 * (c_inv120 - x**2 * (c_inv5040 - x**2 * c_inv362880))))
    
    inputs_meta = { x: {"manual_bank": 2, "role": "operand_a"} }
    
    dfg = compiler.compile(inputs_meta, formula, out_name="y")
    return dfg


def test_sin_8_pipeline():
    print("🚀 [Frontend-To-Backend] Starting High-Order (9-Order) Sin(x) Auto-Compiler...")
    
    runner = StandardPipelineRunner(hw_config="hw_config", build_root="build/sin8")
    sys.path.append(str(Path(__file__).parent))
    from utils.data_generator import ArithTestDataGenerator
    
    gen_data = ArithTestDataGenerator.generate("sin", mode="fuzz", size=4)
    test_x = gen_data["ch_0"]
    input_data = {"x": test_x}
    golden_y = np.sin(test_x)

    success = runner.run_full_pipeline(
        op_name="sin_taylor_high_order",
        get_dfg_fn=get_frontend_dfg,
        input_data=input_data,
        golden_y=golden_y
    )

    if success:
        print("\n✅ High-Order Sin(x) Pipeline Verification: PASSED")
    else:
        print("\n❌ High-Order Sin(x) Pipeline Verification: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    test_sin_8_pipeline()
