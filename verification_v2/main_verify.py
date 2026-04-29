import sys, os, argparse
import numpy as np

# 调整路径以支持导入 src 和 verification_v2
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from verification_v2.infra.mock_memory import MockProgram
from verification_v2.infra.visualizer import print_physical_instructions, print_aligned_timeline
from verification_v2.infra.pipeline_runner import PipelineRunner
from verification_v2.infra.auditor import Auditor
from verification_v2.ops.horner import HornerOperator

# 算子注册表：未来新增算子只需在此添加映射
OPERATOR_MAP = {
    "horner": HornerOperator
}

def run_test(op_name: str, n_rows: int, show_r: bool, show_m: bool):
    if show_r:
        print("\n" + "="*20 + f" AIACC V2.0 Modular Verification: {op_name.upper()} " + "="*20)
    
    if not show_m:
        import logging
        logging.getLogger("ParallelKernel").setLevel(logging.WARNING)
        logging.getLogger("UCodeSynthesizer").setLevel(logging.WARNING)

    # 1. 动态准备算子与数据
    if op_name not in OPERATOR_MAP:
        print(f"❌ [ERROR] Operator '{op_name}' not found in registry.")
        return

    # 实例化算子（此处可扩展更复杂的初始化参数逻辑）
    if op_name == "horner":
        # 测试 5 阶 Horner (6 个常数)
        coeffs = [0.1, -0.2, 0.3, -0.4, 0.5, -0.6] 
        operator = HornerOperator(coeffs)
    else:
        operator = OPERATOR_MAP[op_name]()

    # 动态生成输入数据（由算子自行决定结构）
    input_data = operator.prepare_input(n_rows)
    
    # 2. 运行流水线
    runner = PipelineRunner(hw_config_name="hw_config", show_review=show_r, show_model=show_m)
    ctx = runner.run_full_stack(operator, input_data)
    
    if not ctx:
        return

    # 3. 可视化调试
    print_physical_instructions(ctx["physical_instrs"], show_model=show_m)
    
    # 解析实际轨迹用于对齐图
    timeline_path = os.path.join(ctx["output_dir"], "smc_0", "analysis", "timeline_ascii.txt")
    actual_activity = {}
    if os.path.exists(timeline_path):
        with open(timeline_path, "r") as f:
            for line in f:
                if "|" in line and "iq_name" not in line and "occupancy" not in line:
                    parts = line.split("|")
                    if len(parts) >= 2:
                        name = parts[0].strip()
                        trace = parts[1].strip().split()
                        actual_activity[name] = trace

    print_aligned_timeline(ctx["schedule"], actual_activity, ctx["II"], max_cycles=50, show_review=show_r)

    # 4. 分阶段审计 (Fail-Fast)
    auditor = Auditor(ctx["output_dir"], runner.spec, show_review=show_r, show_model=show_m)
    
    if not auditor.audit_physical_headers():
        print("\n❌ [FAIL-FAST] Header Audit failed. Stopping pipeline.")
        return
        
    if auditor.audit_truth_alignment(ctx["log_path"], num_iterations=ctx["num_iterations"]):
        print("\n" + "✨"*10 + " ALL PHYSICAL CHECKS PASSED " + "✨"*10)
    else:
        print("\n" + "❌"*10 + " VERIFICATION FAILED " + "❌"*10)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="AIACC V2.0 Modular Verification Suite")
    parser.add_argument("--op", type=str, default="horner", help="Operator name to verify (e.g., horner)")
    parser.add_argument("--n-rows", type=int, default=16, help="Number of data rows to verify (Default: 16)")
    parser.add_argument("--review", action="store_true", default=True, help="Show high-level review info (Default)")
    parser.add_argument("--no-review", action="store_false", dest="review", help="Hide review info")
    parser.add_argument("--model", action="store_true", default=False, help="Show detailed model debug info")
    parser.add_argument("--quiet", action="store_true", help="Run in extreme quiet mode")

    args = parser.parse_args()
    
    show_r = args.review and (not args.quiet)
    show_m = args.model and (not args.quiet)

    run_test(args.op, args.n_rows, show_r, show_m)

if __name__ == "__main__":
    main()
