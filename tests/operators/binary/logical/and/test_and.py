#!/usr/bin/env python3
"""
a & b Bitwise AND
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

def get_and_dfg():
    compiler = GraphCompiler()
    a = sp.Symbol('a')
    b = sp.Symbol('b')
    formula = sp.And(a, b)
    inputs_meta = {
        a: {"manual_bank": 0, "role": "operand_a"},
        b: {"manual_bank": 1, "role": "operand_b"}
    }
    return compiler.compile(inputs_meta, formula, out_name="y")

def test_and():
    runner = StandardPipelineRunner(
        hw_config="hw_config",
        build_root="build/operators/binary/logical"
    )
    
    # Using hex patterns to test bitwise logic
    a_dat = np.array([0xFFFFFFFF, 0xAAAAAAAA, 0x55555555, 0x00000000], dtype=np.uint32).view(np.float32)
    b_dat = np.array([0x00000000, 0xFFFFFFFF, 0xAAAAAAAA, 0x55555555], dtype=np.uint32).view(np.float32)
    
    # Math: (a & b)
    a_u = a_dat.view(np.uint32)
    b_u = b_dat.view(np.uint32)
    golden_y = (a_u & b_u).view(np.float32)

    success = runner.run_full_pipeline(
        op_name="and",
        get_dfg_fn=get_and_dfg,
        input_data={"a": a_dat, "b": b_dat},
        golden_y=golden_y
    )
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    test_and()
