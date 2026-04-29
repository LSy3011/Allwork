import sys, os
import numpy as np

# 设置路径
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, 'src'))

from verification_v2.ops.horner import HornerOperator
from verification_v2.infra.pipeline_runner import PipelineRunner
from api.hw_spec import HardwareSpec

def run_test_suite():
    print("="*20 + " AIACC V2.0 FINAL SMOKE TEST (DYNAMIC ORDER) " + "="*20)
    
    spec = HardwareSpec.load("hw_config")
    vector_width = spec.params.PARAM_SMC_VECTOR_WIDTH
    lanes = vector_width // 32 # 假设 FP32
    print(f"[HW] Hardware Config: hw_config | Lanes: {lanes}")

    # 1. 测试 3 阶 Horner (4 个常数)
    print("\n[STEP 1] Testing 3rd-Order Horner (4 constants)...")
    op3 = HornerOperator([1.0, 0.0, -1.0, 0.5])
    print(f"  Shape of coeffs: {op3.tensor_configs['coeffs']['shape']}")
    assert op3.tensor_configs['coeffs']['shape'][0] == 4

    # 2. 测试 6 阶 Horner (7 个常数)
    print("\n[STEP 2] Testing 6th-Order Horner (7 constants)...")
    # c6, c5, c4, c3, c2, c1, c0
    op6 = HornerOperator([0.1, -0.1, 0.2, -0.2, 0.3, -0.3, 0.5])
    print(f"  Shape of coeffs: {op6.tensor_configs['coeffs']['shape']}")
    assert op6.tensor_configs['coeffs']['shape'][0] == 7

    # 3. 运行全链路验证 (6阶)
    print("\n[STEP 3] Running Full-Stack Verification for 6th-Order...")
    runner = PipelineRunner(hw_config_name="hw_config", show_review=False, show_model=False)
    
    n_rows = 16
    input_data = op6.prepare_input(n_rows)
    ctx = runner.run_full_stack(op6, input_data)
    
    if ctx:
        print(f"  ✅ 6th-Order Execution SUCCESS.")
        print(f"  II (Initiation Interval): {ctx['II']}")
        print(f"  Total Phys Instrs: {len(ctx['physical_instrs'])}")
    else:
        print("  ❌ 6th-Order Execution FAILED.")
        sys.exit(1)

    print("\n" + "="*20 + " DYNAMIC ORDER TEST: PASSED " + "="*20)

if __name__ == "__main__":
    run_test_suite()
