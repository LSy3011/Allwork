#!/usr/bin/env python3
"""
acos(x) Approximation via pi/2 - asin(x)
AIACC V2.1 Standard Flow
"""
import sys
import numpy as np
from pathlib import Path
import sympy as sp

ws = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ws / "src"))

from tools.standard_pipeline_runner import StandardPipelineRunner
from frontend import GraphCompiler

def get_acos_dfg():
    compiler = GraphCompiler()
    x_sym = sp.Symbol('x_in')
    
    # acos(x) = pi/2 - asin(x)
    # asin(x) ≈ x + (1/6)x^3 + (3/40)x^5 + (5/112)x^7
    x2 = x_sym * x_sym
    x3 = x2 * x_sym
    x5 = x3 * x2
    x7 = x5 * x2
    
    asin_approx = x_sym + x3 * (1.0/6.0) + x5 * (0.075) + x7 * (5.0/112.0)
    formula = 1.57079632679 - asin_approx
    
    inputs_meta = {
        x_sym: {"manual_bank": 0, "role": "operand_a"}
    }
    return compiler.compile(inputs_meta, formula, out_name="y")

def test_acos():
    runner = StandardPipelineRunner(
        hw_config="hw_config",
        build_root="build/operators/unary/transcendentals"
    )

    fx = 0.5
    input_data = {"x_in": fx}
    
    # Golden matching the DFG approximation
    asin_v = fx + (1.0/6.0)*fx**3 + (0.075)*fx**5 + (5.0/112.0)*fx**7
    golden_y = 1.57079632679 - asin_v

    success = runner.run_full_pipeline(
        op_name="acos",
        get_dfg_fn=get_acos_dfg,
        input_data=input_data,
        golden_y=golden_y
    )
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    test_acos()
