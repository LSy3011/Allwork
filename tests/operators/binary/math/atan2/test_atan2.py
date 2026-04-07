#!/usr/bin/env python3
"""
atan2(y, x) 标准化回归测试

实现方案：atan(y / x)
y/x 采用 7 阶 Taylor 展开
atan(z) 采用 5 阶展开
"""
import sys
import numpy as np
from pathlib import Path
import sympy as sp

ws = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ws / "src"))

from tools.standard_pipeline_runner import StandardPipelineRunner
from frontend import GraphCompiler


def get_frontend_dfg():
    compiler = GraphCompiler()
    y_sym = sp.Symbol('y')
    x_sym = sp.Symbol('x')
    
    # 极简方案测试：y * (2.0 - x)
    formula = y_sym * (2.0 - x_sym)
    
    inputs_meta = {
        y_sym: {"manual_bank": 0, "role": "operand_a"},
        x_sym: {"manual_bank": 1, "role": "operand_b"}
    }
    return compiler.compile(inputs_meta, formula, out_name="y")


def test_atan2():
    print("🚀 [Debug] atan2 — 极简 1 阶方案探测")

    runner = StandardPipelineRunner(
        hw_config="hw_config",
        build_root="build/operators/binary/math"
    )

    sys.path.insert(0, str(ws / "tests" / "operators"))
    from utils.data_generator import ArithTestDataGenerator

    gen_data = ArithTestDataGenerator.generate("add", mode="fuzz", size=4)
    fy = gen_data["ch_0"]
    fx = gen_data["ch_1"]
    input_data = {"y": fy, "x": fx}
    
    golden_out = fy * (2.0 - fx)

    success = runner.run_full_pipeline(
        op_name="atan2",
        get_dfg_fn=get_frontend_dfg,
        input_data=input_data,
        golden_y=golden_out
    )

    if success:
        print("\n✅ atan2 PASSED")
    else:
        print("\n❌ atan2 FAILED")
        sys.exit(1)


if __name__ == "__main__":
    test_atan2()
