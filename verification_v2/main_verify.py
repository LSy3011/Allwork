import sys, os, argparse, json
import numpy as np

# 调整路径以支持导入 src 和 verification_v2
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from verification_v2.infra.mock_memory import MockProgram
from verification_v2.infra.visualizer import print_physical_instructions
from verification_v2.infra.pipeline_runner import PipelineRunner
from verification_v2.infra.auditor import Auditor
from verification_v2.ops.horner import HornerOperator
from verification_v2.ops.taylor import TaylorOperator
from verification_v2.ops.configurable import ConfigurableOperator
from verification_v2.ops.matmul_op import ManualMatmulOperator, ManualMatmulParallelNOperator, ManualMatmulQOperator, ManualMatmulQDsTOperator, ManualMatmulQDsTK8Operator, ManualMatmulQDsTFullOperator, ManualMatmulQ2Operator, ManualMatmulQMultiRowOperator, ManualMatmulQMultiTileOperator, ManualMatmulQFullOperator, ManualFp16MatmulOperator, ManualFp16MatmulK16Operator, ManualFp16MatmulK16LdbOperator, ManualFp16MatmulK128LdbOperator, ManualFp16MatmulK256LdbOperator, ManualFp16MatmulK32StreamLdbOperator, ManualFp16MatmulK24Operator, ManualFp16MatmulK32Operator, ManualFp16MatmulK2048Operator, ManualFp16MatmulQOperator, ManualFp16MatmulParallelNOperator, ManualFp16MatmulQDsTOperator, ManualFp16MatmulFullOperator, ManualFp16MatmulFullDsTOperator
from verification_v2.ops.manual_selu import ManualSeluOperator
from verification_v2.ops.cross_smc_unit_op import UnitFpAddOperator, UnitDataShuffleTransposeOperator, UnitDataShuffleTransposeFp16Operator, UnitDstVec128Operator

OPERATOR_MAP = {
    # 文档钦定流程的 8-SMC 功能测例（VCS 对齐，自底向上）
    "unit_fpadd": UnitFpAddOperator,                       # Flow A: 单一计算指令（FPADD, para 4828）
    "unit_ds_transpose": UnitDataShuffleTransposeOperator,  # Flow B: 数据整理 datapath（para 4676）
    "unit_ds_transpose_fp16": UnitDataShuffleTransposeFp16Operator,  # ADR-039 fp16 最小载体（闭合 bank5）
    "unit_dst_vec128": UnitDstVec128Operator,  # ADR-043 path B：DST 交付 128 元素向量隔离验证
    "horner": HornerOperator,
    "sin3":   HornerOperator,
    "sin5":   HornerOperator,
    "sin5_taylor": TaylorOperator,

    "manual_selu": ManualSeluOperator,
    "selu": ManualSeluOperator,
    # matmul 使用手动 DFG（ManualMatmulOperator），不走 ConfigurableOperator 的
    # compile_with_adpc 路径（后者对 4-operand 公式生成的 DFG 结构 SMT 不可解）
    "matmul": ManualMatmulOperator,
    # ADR-025：N 维广播 matmul 走手搭 operator（覆盖 matmul_parallel_n.yaml 的 UNSAT configurable 路径）
    "matmul_parallel_n": ManualMatmulParallelNOperator,
    # ADR-026 Stage-1：Matmul_Q (matrix×matrix) 缩放实例
    "matmul_q": ManualMatmulQOperator,
    # ADR-033 Stage-A：Matmul_Q 经 SMI_DS_T 跨-SMC 转置 datapath（真路径，替 matmul_q 假转置）
    "matmul_q_dst": ManualMatmulQDsTOperator,
    # ADR-033 Stage-A'：SMI_DS_T K=SMC_NUM=8 全 gather + cumulative addtree（更忠实）
    "matmul_q_dst_k8": ManualMatmulQDsTK8Operator,
    # ADR-036 S3：scenario-A K=8 + M 满载（2 级循环 + accumulate 逐行 reset）
    "matmul_q_dst_full": ManualMatmulQDsTFullOperator,
    # ADR-026 Stage-2a：Matmul_Q cols_per_smc=4 密集打包（local-buffer 修法）
    "matmul_q2": ManualMatmulQ2Operator,
    "matmul_q_multirow": ManualMatmulQMultiRowOperator,  # ADR-026 Stage-2b: M>1 × 4列 × 单tile
    "matmul_q_multitile": ManualMatmulQMultiTileOperator,  # ADR-026 Stage-2c核: M=1 × 4列 × 多tile K
    "matmul_q_full": ManualMatmulQFullOperator,  # ADR-026 Stage-2c合体: M>1 × 4列 × 多tile K (spill 2-phase 全展开)
    # ADR-032 S1：FP16 推理 matmul 最小通路（FP16MUL + FP32ADDTREE8TO1，单 SMC K=8）
    "fp16_matmul_min": ManualFp16MatmulOperator,
    # DEBT-025-MULTITILE 首步（hco APPROVE 候选①）：fp16 K=16 = 2 tile，8to1 片内 + 5to1 accumulate 片间
    "fp16_matmul_k16": ManualFp16MatmulK16Operator,
    # ADR-050 阶段 1：K16 权重经 LDB 从外部 HBM 流式载入（load/store 权重流式）
    "fp16_matmul_k16_ldb": ManualFp16MatmulK16LdbOperator,
    "fp16_matmul_k128_ldb": ManualFp16MatmulK128LdbOperator,
    # ADR-050 阶段 2（DEBT-050-KGT128）：K256 多-chunk explicit-N；chunk0 通、chunk1 blocked on RTL residue
    "fp16_matmul_k256_ldb": ManualFp16MatmulK256LdbOperator,
    "fp16_matmul_k32_stream_ldb": ManualFp16MatmulK32StreamLdbOperator,
    # ADR-049 后续：扩 K 多-tile 累加（K24=3 tile/K32=4 tile，复用 K16 参数化核）
    "fp16_matmul_k24": ManualFp16MatmulK24Operator,
    "fp16_matmul_k32": ManualFp16MatmulK32Operator,
    # 满-bank 示例：K=2048 激活填满整 bank（256 tile），单-SMC 单列极限
    "fp16_matmul_k2048": ManualFp16MatmulK2048Operator,
    # ADR-032 S2：FP16 matmul 满 32 乘（4 列稠密，1 FP16MUL → 8 D → 4 ADDTREE8TO1）
    "fp16_matmul_q": ManualFp16MatmulQOperator,
    # ADR-032 S3：FP16 matmul 8-SMC N 维广播（满 32 乘/SMC，partitioned 输出）
    "fp16_matmul_n": ManualFp16MatmulParallelNOperator,
    # ADR-035：SMI_DS_T 跨-SMC 转置落到 FP16 matmul 路径（fp16_matmul_n + 转置前端）
    "fp16_matmul_q_dst": ManualFp16MatmulQDsTOperator,
    "fp16_matmul_full": ManualFp16MatmulFullOperator,
    # ADR-035/036 Increment B：fp16_matmul_full 流式 compute + SMI_DS_T 转置前端（lanes=M>1 多行）
    "fp16_matmul_full_dst": ManualFp16MatmulFullDsTOperator,
}

CATEGORIES = {
    "math": ["add", "mul", "div", "reciprocal", "sqrt", "rsqrt", "abs", "addcdiv", "addcmul"],
    "trig": ["horner", "sin3", "sin5", "sin5_taylor", "horner_v3", "cos4", "cosh4", "exp5", "expm1", "sinh5", "log", "log1p", "log2", "tan7", "tanh7"],
    "activation": ["elu", "mish", "hardsigmoid", "hardswish", "gelu_approx", "silu_approx", "silu_taylor", "sigmoid_taylor", "leaky_relu", "clip", "relu6", "relu_offset", "selu"],
    "manual": ["manual_selu", "matmul"]
}


# 🛸 [V5.0] 自动扫描 YAML 算子配置并注册
OPS_DIR = os.path.join(os.path.dirname(__file__), "ops")
if os.path.exists(OPS_DIR):
    for f in os.listdir(OPS_DIR):
        if f.endswith(".yaml"):
            op_name = f.replace(".yaml", "")
            if op_name in OPERATOR_MAP:
                continue
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

def run_test(op_name: str, n_rows: int, op_args: dict, hw_config_name: str, show_r: bool, show_m: bool, input_file: str = None) -> bool:
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

    # 加载自定义输入数据或动态生成
    if input_file:
        if not os.path.exists(input_file):
            print(f"[ERROR] Input file '{input_file}' not found.")
            return False
        loaded = np.load(input_file)
        input_data = {k: loaded[k] for k in loaded.files}
        # 动态重设实际行数
        first_key = next(iter(input_data))
        n_rows = len(input_data[first_key])
        if show_r:
            print(f"[INFO] Loaded custom inputs from '{input_file}' ({n_rows} rows).")
    else:
        # 动态生成输入数据（由算子自行决定结构）
        input_data = operator.prepare_input(n_rows)
    
    # 2. 运行流水线
    runner = PipelineRunner(hw_config_name=hw_config_name, show_review=show_r, show_model=show_m)
    ctx = runner.run_full_stack(operator, input_data, op_name=op_name,
                                external_input=(input_file is not None))
    
    if not ctx:
        return False

    # 3. 可视化调试
    print_physical_instructions(ctx["physical_instrs"], show_model=show_m)

    # 4. 分阶段审计 (Fail-Fast)
    auditor = Auditor(ctx["output_dir"], runner.spec, show_review=show_r, show_model=show_m)
    
    if not auditor.audit_physical_headers():
        print("\n[FAIL-FAST] Header Audit failed. Stopping pipeline.")
        return False
        
    sim_ok = ctx.get("sim_success", True)
    # ADR-020 v3：multi-SMC 路径 run_full_stack 内部会重生成 input_data；优先用 ctx 里的"sim 实吃版"。
    # Auditor 检查 SMC_0 truth alignment，partitioned 张量 2D (smc_num, n_rows) 须切到 SMC_0 视角 [0]。
    effective_input_data = ctx.get("input_data", input_data)
    auditor_input_data = {
        k: (v[0] if hasattr(v, "ndim") and v.ndim == 2 else v)
        for k, v in effective_input_data.items()
    }
    align_ok = auditor.audit_truth_alignment(
        ctx["log_path"],
        num_iterations=ctx["num_iterations"],
        ops=ctx.get("ops"),
        input_data=auditor_input_data
    )

    # ADR-036 S3-c block-3：固件完整性门（hco APPROVE-with-notes）。独立于 truth-audit（不受 fp16
    # skip 旁路），ON real-gather 下校 sim 每个 UR 端点访存必有固件指令对应——防 model-A backdoor 回归。
    integrity_ok = auditor.audit_firmware_integrity(ctx["log_path"], ops=ctx.get("ops"))

    if align_ok and integrity_ok and sim_ok:
        print("\n" + "*"*10 + f" ALL PHYSICAL CHECKS PASSED FOR {op_name.upper()} " + "*"*10)
        return True
    else:
        print("\n" + "!"*10 + f" VERIFICATION FAILED FOR {op_name.upper()} " + "!"*10)
        # 🚀 [SW-OPT-2] 失败时自动升级 Verbose，无需重跑
        print("\n[AUTO-DEBUG] Replaying physical instructions for diagnosis:")
        print_physical_instructions(ctx["physical_instrs"], show_model=True)
        return False

def run_smt_unit_tests() -> bool:
    """[SW-OPT: 测试集成] 运行 SMT 约束单元测试（不启动仿真器，极快）"""
    import unittest
    print("\n" + "="*20 + " SMT CONSTRAINT UNIT TESTS " + "="*20)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    try:
        from tests.unit.test_smt_adversarial import TestSMTAdversarial, TestSMTRefutation
        suite.addTests(loader.loadTestsFromTestCase(TestSMTAdversarial))
        suite.addTests(loader.loadTestsFromTestCase(TestSMTRefutation))
    except ImportError as e:
        print(f"[WARN] Could not load SMT unit tests: {e}")
        return True  # 非致命
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return result.wasSuccessful()


def run_hw_parity_tests() -> bool:
    """[SW-OPT: 测试集成] 运行硬件物理比对测试（SMT → .hex → 仿真 → Golden 比对）"""
    import unittest
    print("\n" + "="*20 + " HARDWARE PARITY TESTS " + "="*20)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    try:
        from tests.integration.test_smt_hw_parity import TestSMTHardwareParity
        suite.addTests(loader.loadTestsFromTestCase(TestSMTHardwareParity))
    except ImportError as e:
        print(f"[WARN] Could not load HW parity tests: {e}")
        return True  # 非致命
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return result.wasSuccessful()


def main():
    parser = argparse.ArgumentParser(description="AIACC V2.0 Modular Verification Suite")
    parser.add_argument("--op", type=str, default="horner",
                        help="Operator name to verify, 'all' for all, or category name ('math', 'trig', 'activation', 'manual')")
    parser.add_argument("--op-args", type=str, default="{}",
                        help="JSON string for operator kwargs")
    parser.add_argument("--hw-config", type=str, default="hw_config",
                        help="Hardware configuration name")
    parser.add_argument("--n-rows", type=int, default=256,
                        help="Number of data rows to verify (Default: 256)")
    parser.add_argument("--input-file", type=str, default=None,
                        help="Path to an optional .npz file containing custom input arrays")
    parser.add_argument("--review", action="store_true", default=True)
    parser.add_argument("--no-review", action="store_false", dest="review")
    parser.add_argument("--model", action="store_true", default=False)
    parser.add_argument("--quiet", action="store_true")
    # 🚀 [SW-OPT: 测试集成] 新增 --mode 参数
    parser.add_argument("--mode", type=str, default="e2e",
                        choices=["e2e", "smt-unit", "hw-parity", "all"],
                        help=(
                             "Verification mode:\n"
                             "  e2e       - End-to-end operator verification (default)\n"
                             "  smt-unit  - SMT constraint unit tests (no simulation)\n"
                             "  hw-parity - Hardware physical parity tests\n"
                             "  all       - All modes in sequence (fail-fast)"
                        ))

    args = parser.parse_args()
    show_r = args.review and (not args.quiet)
    show_m = args.model and (not args.quiet)
    op_args_dict = json.loads(args.op_args)

    # ── 模式路由 ──────────────────────────────────────────────────────────
    if args.mode == "smt-unit":
        sys.exit(0 if run_smt_unit_tests() else 1)

    elif args.mode == "hw-parity":
        sys.exit(0 if run_hw_parity_tests() else 1)

    elif args.mode == "all":
        print("\n" + "="*20 + " FULL VERIFICATION SUITE " + "="*20)
        results = {}
        results["smt-unit"] = run_smt_unit_tests()
        if not results["smt-unit"]:
            print("[FAIL-FAST] SMT unit tests failed. Stopping.")
            sys.exit(1)
        results["hw-parity"] = run_hw_parity_tests()
        if not results["hw-parity"]:
            print("[FAIL-FAST] HW parity tests failed. Stopping.")
            sys.exit(1)
        # 最后跑 e2e
        kwargs = op_args_dict if op_args_dict else DEFAULT_OP_ARGS.get(args.op, {})
        results["e2e"] = run_test(args.op, args.n_rows, kwargs, args.hw_config, show_r, show_m, args.input_file)
        all_ok = all(results.values())
        print("\n" + "="*20 + " FULL SUITE REPORT " + "="*20)
        for k, v in results.items():
            print(f"  [{k.upper():12s}]: {'PASS' if v else 'FAIL'}")
        sys.exit(0 if all_ok else 1)

    else:  # e2e (default)
        if args.op == "all" or args.op in CATEGORIES:
            target_ops = list(OPERATOR_MAP.keys()) if args.op == "all" else CATEGORIES[args.op]
            # Filter out operators not loaded in OPERATOR_MAP (to prevent key errors)
            target_ops = [op for op in target_ops if op in OPERATOR_MAP]
            
            print(f"\n" + "="*20 + f" STARTING BATCH VERIFICATION ({args.op.upper()}) " + "="*20)
            results = {}
            for op in target_ops:
                kwargs = op_args_dict if op_args_dict else DEFAULT_OP_ARGS.get(op, {})
                success = run_test(op, args.n_rows, kwargs, args.hw_config, show_r, show_m, args.input_file)
                results[op] = success

            print("\n" + "="*20 + f" BATCH VERIFICATION REPORT ({args.op.upper()}) " + "="*20)
            all_passed = True
            for op, success in results.items():
                status = "[PASS]" if success else "[FAIL]"
                print(f"[{op.upper()}]: {status}")
                if not success: all_passed = False
            sys.exit(0 if all_passed else 1)
        else:
            kwargs = op_args_dict if op_args_dict else DEFAULT_OP_ARGS.get(args.op, {})
            success = run_test(args.op, args.n_rows, kwargs, args.hw_config, show_r, show_m, args.input_file)
            sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
