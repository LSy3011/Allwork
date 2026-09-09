#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# load_store_v3 一键 RTL 测试（优先 RTL v4.20/v4.21 multi_stage，兼容 v4.19）
#
# v3 = 原生 bulk 发码（LDB/STB 用 ur_wr_line_cnt 一条搬多行，替代 v2 的 loops 外壳）。
#      若 RTL_DIR/sim_multi_stage 存在，则使用 multi_inst_stage 三阶段 TB；
#      否则使用 4.19 verification_v2 兼容版式。
#
# 用法:
#   ./run_rtl_test_v3.sh <mul|bitwise_and|mul_scalar|select_int> [--seed N] [--no-rebuild]
#
# 流程: 编译 gen → 生成微指令+数据+golden+meta → data_list → RTL 仿真(含 UR 比对)
#       → verify_ddr_v3.py（DDR 写回数据 == golden 真比对）
# ----------------------------------------------------------------------------
# 其他同事改这里:
#   RTL_DIR : 你的 RTL v4.19 仓库根目录
# ----------------------------------------------------------------------------
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"        # .../SMI_IQ/load_store_v3
SMI_IQ_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"       # .../SMI_IQ

# >>> 改这里 <<<
RTL_DIR="${RTL_DIR:-/home/jabari/Jianyuan_aiacc_v4.19}"

OPERATOR=""
SEED="$(date +%s)"
REBUILD_FLAG="--rebuild"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --seed)        SEED="$2"; shift 2;;
        --no-rebuild)  REBUILD_FLAG=""; shift;;
        -*)            echo "未知选项: $1"; exit 1;;
        *)             OPERATOR="$1"; shift;;
    esac
done
[[ -z "$OPERATOR" ]] && { echo "用法: [RTL_DIR=...] $0 <mul|bitwise_and|mul_scalar|select_int> [--seed N] [--no-rebuild]"; exit 1; }
[[ -d "$RTL_DIR" ]] || { echo "错误: RTL 目录不存在: $RTL_DIR"; exit 1; }

CASE_NAME="${OPERATOR}_8smc_v3"
if [[ -d "${RTL_DIR}/sim_multi_stage" && -f "${RTL_DIR}/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" ]]; then
    RTL_MULTI_STAGE=1
    CASE_REL="multi_inst_stage/${CASE_NAME}"
else
    RTL_MULTI_STAGE=0
    CASE_REL="verification_v2/${CASE_NAME}"
fi
CASE_ABS="${RTL_DIR}/test_atomic_ops/${CASE_REL}"
OUT_DIR="${SCRIPT_DIR}/out"
GEN_BIN="${OUT_DIR}/gen_${OPERATOR}_lsu_case"
mkdir -p "$OUT_DIR"

echo "=============================================="
echo " load_store_v3 RTL 测试 (bulk 发码)"
echo " 算子: $OPERATOR   Seed: $SEED"
echo " RTL:  $RTL_DIR"
echo " Case: $CASE_REL"
echo " Mode: $([[ ${RTL_MULTI_STAGE} -eq 1 ]] && echo 'multi_stage' || echo 'legacy_4.19')"
echo "=============================================="

# ---- Step 1: 编译 gen ----
echo ""; echo "[1/5] 编译 $OPERATOR L-C-S 生成器..."
LSU_MAIN=$(find "$SCRIPT_DIR" -path "*/${OPERATOR}/*" -name "gen_*_lsu_case.cpp" | head -1)
[[ -z "$LSU_MAIN" ]] && LSU_MAIN=$(find "$SCRIPT_DIR" -maxdepth 1 -name "gen_${OPERATOR}_lsu_case.cpp" | head -1)
LSU_OP_SRC=$(find "$SCRIPT_DIR" -path "*/${OPERATOR}/*" -name "*_lsu_op.cpp" | head -1)
if [[ -z "$LSU_MAIN" || -z "$LSU_OP_SRC" ]]; then
    echo "错误: 未找到 ${OPERATOR} 的 gen_*_lsu_case.cpp / *_lsu_op.cpp"; exit 1
fi

cd "$SMI_IQ_DIR"
OP_SRCS=$(find op -name "*.cpp" | grep -vE "/(div|div_scalar|select|expand|Mean-dim|mul_scalar|softmax|sub|add|cos|exp|neg|pow|unsqueeze)/" | grep -vE "/test_" | grep -vE "_main\.cpp$" | tr '\n' ' ')
if [[ "$OPERATOR" == "mul_scalar" ]]; then
    OP_SRCS+=" $(find op/mul_scalar -name '*.cpp' | grep -vE '_main\\.cpp$' | tr '\n' ' ')"
fi
IQ_SRCS=$(find iq -name "*.cpp" | tr '\n' ' ')
g++ -std=c++17 -O2 -fno-stack-protector -I"$SMI_IQ_DIR" -I"$SMI_IQ_DIR/app" \
    "$LSU_MAIN" "$SCRIPT_DIR/lsu_op_base.cpp" "$LSU_OP_SRC" \
    $OP_SRCS $IQ_SRCS \
    app/tvgen_core.cpp app/tvgen_dynamic.cpp app/simulator_config_gen.cpp app/app_json_parser.cpp app/fp16_utils.cpp \
    math_utils/newton_method/newton_method.cpp math_utils/taylor_series/taylor_series.cpp \
    -o "$GEN_BIN" 2>&1 | grep -iE "error" && { echo "编译失败"; exit 1; } || true
[[ -x "$GEN_BIN" ]] || { echo "错误: gen 未编译出来"; exit 1; }

# ---- Step 2: 生成微指令 + 数据 + golden + meta ----
echo ""; echo "[2/5] 生成用例 (seed=$SEED)..."
rm -rf "$CASE_ABS"
GEN_ARGS=("$CASE_ABS" "${RTL_DIR}/data_engine/sram" "$SEED")
if [[ "$OPERATOR" == "mul_scalar" ]]; then
    GEN_ARGS+=("${MUL_SCALAR_DTYPE:-${LSU_DTYPE:-fp32}}" "${LSU_WC:-255}")
elif [[ "$OPERATOR" == "select_int" ]]; then
    GEN_ARGS+=("${SELECT_INT_INPUT_WORDS:-${LSU_WC:-96}}"
               "${SELECT_INT_OUTPUT_WORDS:-${LSU_WC:-32}}"
               "${SELECT_INT_SOURCE_OFFSET_WORDS:-0}"
               "${SELECT_INT_DTYPE:-${LSU_DTYPE:-fp32}}"
               "${SELECT_INT_OUTPUT_BYTES:-0}")
fi
"$GEN_BIN" "${GEN_ARGS[@]}"

# ---- Step 3: data_list ----
echo ""; echo "[3/5] 生成 data_list..."
cd "$RTL_DIR"
if [[ ${RTL_MULTI_STAGE} -eq 1 ]]; then
    python3 ./sim_multi_stage/generate_data_list_coverage_multi_stage.py "${CASE_REL}/smc_0" --subq 0 > /dev/null 2>&1
else
    python3 ./generate_data_list_coverage.py "$CASE_REL" --subq 0 > /dev/null 2>&1
fi

# ---- Step 4: RTL 仿真 (内部含 UR 比对) ----
echo ""; echo "[4/5] RTL 仿真..."
LOG="${OUT_DIR}/${CASE_NAME}_rtl.log"
if [[ ${RTL_MULTI_STAGE} -eq 1 ]]; then
    SKIP_GENERATE_DATA_LIST=1 SKIP_COMPARE=0 bash ./sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh $REBUILD_FLAG "${CASE_REL}/smc_0" -s 0 > "$LOG" 2>&1 || true
else
    SKIP_GENERATE_DATA_LIST=1 SKIP_COMPARE=0 bash ./run_sim_aiacc_top_f.sh $REBUILD_FLAG verification_v2 "${CASE_REL}/smc_0" -s 0 > "$LOG" 2>&1 || true
fi
echo -n "  UR 比对(参考): "; grep "Summary" "$LOG" | tail -1 || echo "(无 Summary)"

# ---- Step 5: DDR 真比对（store 写回 == golden）----
echo ""; echo "[5/5] DDR 真比对 (verify_ddr_v3.py)..."
python3 "${SCRIPT_DIR}/verify_ddr_v3.py" "$CASE_ABS"
VERIFY_RC=$?

echo ""
echo "=============================================="
echo "  DDR 真比对退出码: ${VERIFY_RC} (0=全 SMC 通过)"
if [[ ${RTL_MULTI_STAGE} -eq 1 ]]; then
    FSDB="$(find sim_output_aiacc_top_f_multi_stage -name '*.fsdb' 2>/dev/null | head -1)"
else
    FSDB="$(find sim_output_aiacc_top_f -name '*.fsdb' 2>/dev/null | head -1)"
fi
[[ -n "$FSDB" ]] && echo "  波形: ${RTL_DIR}/${FSDB}"
echo "  日志: $LOG"
echo "=============================================="
exit $VERIFY_RC
