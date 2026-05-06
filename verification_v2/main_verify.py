import sys, os, argparse, json
import numpy as np

# 调整路径以支持导入 src 和 verification_v2
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from verification_v2.infra.mock_memory import MockProgram
from verification_v2.infra.visualizer import print_physical_instructions, print_aligned_timeline
from verification_v2.infra.pipeline_runner import PipelineRunner
from verification_v2.infra.auditor import Auditor
from verification_v2.ops.horner import HornerOperator
from verification_v2.ops.taylor import TaylorOperator
from verification_v2.ops.configurable import ConfigurableOperator

OPERATOR_MAP = {
    "horner": HornerOperator,
    "sin3":   HornerOperator,
    "sin5":   HornerOperator,
    "sin5_taylor": TaylorOperator
}

# 🛸 [V5.0] 自动扫描 YAML 算子配置并注册
OPS_DIR = os.path.join(os.path.dirname(__file__), "ops")
if os.path.exists(OPS_DIR):
    for f in os.listdir(OPS_DIR):
        if f.endswith(".yaml"):
            op_name = f.replace(".yaml", "")
            yaml_path = os.path.join(OPS_DIR, f)
            # 使用 lambda 延迟加载，模拟类构造行为
            OPERATOR_MAP[op_name] = lambda path=yaml_path, **kwargs: ConfigurableOperator(path)

# 默认批处理算子参数
DEFAULT_OP_ARGS = {
    "horner": {"coeffs": [0.1, -0.2, 0.3, -0.4, 0.5, -0.6]},
    "sin3":   {"coeffs": [-1/6.0, 0.0, 1.0, 0.0]},
    "sin5":   {"coeffs": [1/120.0, 0.0, -1/6.0, 0.0, 1.0, 0.0]},
    "sin5_taylor": {"coeffs": {5: 1/120.0, 3: -1/6.0, 1: 1.0}}
}

def run_test(op_name: str, n_rows: int, op_args: dict, hw_config_name: str, show_r: bool, show_m: bool) -> bool:
    if show_r:
        print("\n" + "="*20 + f" AIACC V2.0 Modular Verification: {op_name.upper()} " + "="*20)
    
    if not show_m:
        import logging
        logging.getLogger("ParallelKernel").setLevel(logging.WARNING)
        logging.getLogger("UCodeSynthesizer").setLevel(logging.WARNING)

    # 1. 动态准备算子与数据
    if op_name not in OPERATOR_MAP:
        print(f"[ERROR] Operator '{op_name}' not found in registry.")
        return False

    # 实例化算子（通过参数注入实现去硬编码）
    operator = OPERATOR_MAP[op_name](**op_args)

    # 动态生成输入数据（由算子自行决定结构）
    input_data = operator.prepare_input(n_rows)
    
    # 2. 运行流水线
    runner = PipelineRunner(hw_config_name=hw_config_name, show_review=show_r, show_model=show_m)
    ctx = runner.run_full_stack(operator, input_data, op_name=op_name)
    
    if not ctx:
        return False

    # 3. 可视化调试
    print_physical_instructions(ctx["physical_instrs"], show_model=show_m)
    
    # 解析实际轨迹用于对齐图 (下沉到 PipelineRunner)
    actual_activity = runner.parse_simulation_timeline(ctx["output_dir"])

    print_aligned_timeline(ctx["schedule"], actual_activity, ctx["II"], max_cycles=50, show_review=show_r)

    # 4. 分阶段审计 (Fail-Fast)
    auditor = Auditor(ctx["output_dir"], runner.spec, show_review=show_r, show_model=show_m)
    
    if not auditor.audit_physical_headers():
        print("\n[FAIL-FAST] Header Audit failed. Stopping pipeline.")
        return False
        
    sim_ok = ctx.get("sim_success", True)
    align_ok = auditor.audit_truth_alignment(
        ctx["log_path"], 
        num_iterations=ctx["num_iterations"],
        ops=ctx.get("ops"),
        input_data=input_data
    )
    
    if align_ok and sim_ok:
        print("\n" + "*"*10 + f" ALL PHYSICAL CHECKS PASSED FOR {op_name.upper()} " + "*"*10)
        return True
    else:
        print("\n" + "!"*10 + f" VERIFICATION FAILED FOR {op_name.upper()} " + "!"*10)
        return False

def main():
    parser = argparse.ArgumentParser(description="AIACC V2.0 Modular Verification Suite")
    parser.add_argument("--op", type=str, default="horner", help="Operator name to verify (e.g., horner) or 'all' for batch testing")
    parser.add_argument("--op-args", type=str, default="{}", help="JSON string for operator kwargs")
    parser.add_argument("--hw-config", type=str, default="hw_config", help="Hardware configuration name")
    parser.add_argument("--n-rows", type=int, default=16, help="Number of data rows to verify (Default: 16)")
    parser.add_argument("--review", action="store_true", default=True, help="Show high-level review info (Default)")
    parser.add_argument("--no-review", action="store_false", dest="review", help="Hide review info")
    parser.add_argument("--model", action="store_true", default=False, help="Show detailed model debug info")
    parser.add_argument("--quiet", action="store_true", help="Run in extreme quiet mode")

    args = parser.parse_args()
    
    show_r = args.review and (not args.quiet)
    show_m = args.model and (not args.quiet)

    op_args_dict = json.loads(args.op_args)

    if args.op == "all":
        print("\n" + "="*20 + " STARTING BATCH VERIFICATION " + "="*20)
        results = {}
        for op in OPERATOR_MAP.keys():
            kwargs = op_args_dict if op_args_dict else DEFAULT_OP_ARGS.get(op, {})
            success = run_test(op, args.n_rows, kwargs, args.hw_config, show_r, show_m)
            results[op] = success
        
        print("\n" + "="*20 + " BATCH VERIFICATION REPORT " + "="*20)
        all_passed = True
        for op, success in results.items():
            status = "[PASS]" if success else "[FAIL]"
            print(f"[{op.upper()}]: {status}")
            if not success: all_passed = False
        sys.exit(0 if all_passed else 1)
    else:
        kwargs = op_args_dict if op_args_dict else DEFAULT_OP_ARGS.get(args.op, {})
        success = run_test(args.op, args.n_rows, kwargs, args.hw_config, show_r, show_m)
        sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
