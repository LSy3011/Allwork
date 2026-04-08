#!/usr/bin/env python3
import sys
import numpy as np
from pathlib import Path
import sympy as sp

ws = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ws / "src"))

from tools.standard_pipeline_runner import StandardPipelineRunner
from frontend import GraphCompiler

def get_softsign_dfg():
    compiler = GraphCompiler()
    x = sp.Symbol('x')
    
    # softsign(x) = x / (1 + |x|)
    # Use auto-lowering of 1/(1+|x|) to Taylor via compiler.py
    formula = x / (1.0 + sp.Abs(x))
    
    inputs_meta = {
        x: {"manual_bank": 0, "role": "operand_a"}
    }
    return compiler.compile(inputs_meta, formula, out_name="y")

def test_softsign():
    print("🚀 [Development] softsign (x / (1 + |x|)) - DFG Composition")

    runner = StandardPipelineRunner(
        hw_config="hw_config",
        build_root="build/operators/activation"
    )

    # Use range [-0.8, 0.8] so |x| + 1 stays in [1, 1.8] for Taylor convergence of 1/u
    fx = np.array([-0.8, -0.2, 0.2, 0.8], dtype=np.float32)
    input_data = {"x": fx}
    
    # Golden matching the DFG's auto-lowered reciprocal
    # 1/u approx (5th order): 1 - du + du^2 - du^3 + du^4 - du^5
    abs_x = np.abs(fx)
    du = abs_x # (1+abs_x) - 1.0
    recip = 1.0 - du * (1.0 - du * (1.0 - du * (1.0 - du * (1.0 - du))))
    golden_y = fx * recip

    success = runner.run_full_pipeline(
        op_name="softsign",
        get_dfg_fn=get_softsign_dfg,
        input_data=input_data,
        golden_y=golden_y
    )
    
    if success:
        print("\n✅ softsign PASSED")
    else:
        print("\n❌ softsign FAILED")
        sys.exit(1)

if __name__ == "__main__":
    test_softsign()
