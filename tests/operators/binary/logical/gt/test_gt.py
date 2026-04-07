#!/usr/bin/env python3
"""
a > b Logical Comparison
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

def get_gt_dfg():
    compiler = GraphCompiler()
    a = sp.Symbol('a')
    b = sp.Symbol('b')
    formula = sp.StrictGreaterThan(a, b)
    inputs_meta = {
        a: {"manual_bank": 0, "role": "operand_a"},
        b: {"manual_bank": 1, "role": "operand_b"}
    }
    return compiler.compile(inputs_meta, formula, out_name="y")

def test_gt():
    runner = StandardPipelineRunner(
        hw_config="hw_config",
        build_root="build/operators/binary/logical"
    )
    
    a_dat = np.array([1.5, 2.0, -1.0, 5.0], dtype=np.float32)
    b_dat = np.array([2.0, 1.5, -2.0, 5.0], dtype=np.float32)
    golden_y = (a_dat > b_dat).astype(np.float32)

    success = runner.run_full_pipeline(
        op_name="gt",
        get_dfg_fn=get_gt_dfg,
        input_data={"a": a_dat, "b": b_dat},
        golden_y=golden_y
    )
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    test_gt()
