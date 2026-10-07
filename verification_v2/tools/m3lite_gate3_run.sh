#!/bin/bash
# m3lite_gate3_run.sh —— ADR-060 / M3-lite 门③步骤③+④：完整链路驱动。
# 用法：bash verification_v2/tools/m3lite_gate3_run.sh <case_base> [words] [seed]
# 五跑（步骤④，ADR-060 §6，用户 2026-10-07 五条要求）：
#   [正] multi_inst_stage/<case_base>       3 段（LOAD→COMPUTE→STORE）+ 全屏障
#        （G3_BARRIER 信用 + G3_WBARRIER 写落位 + G3_S4_BARRIER STORE 完成闸），
#        期望比对 GREEN（C0-C8）；
#   [负] <case_base>_neg  --merge-stages --with-store = merged-3 并段单 trigger
#        （无任何 G3 plusarg），期望 NEGATIVE-CONTROL LIVE（C1/C3/C3s/C6/C8 任红）；
#   [故] <case_base>_bp   反压保持力：+G3_AXI_BP_B=400（明确标注的故障注入，
#        B 通道 v2：首个 AWVALID 上升沿起押 bready=0 共 400 拍，写数据路不动），
#        期望 GREEN 且 waited>=min_waited(=300) 且 waited 落在
#        [bp_release-s4_enter-2,+50] 精确区间（证明 waited 确由注入造成）。
#        注：W 通道反压（+G3_AXI_BP_W）已于 2026-10-07 退役——首轮实测
#        300 拍 wready 反压使写路径永久锁死（ADR-060 errata ⑤）；
#        BVALID 沿武装同日退役——B 活窗仅 1 拍、force 结构性迟到（errata 6）。
#   [超] <case_base>_to_wb  +G3_WBARRIER_TIMEOUT=1 → 期望 $fatal 失败退出，
#        比对 --mode fatal_expected --expect-timeout wbarrier；
#   [超] <case_base>_to_s4  +G3_S4_TIMEOUT=50 +G3_AXI_BP_W=300 → 期望 $fatal，
#        比对 --mode fatal_expected --expect-timeout s4barrier。
#   故障注入三段（bp/to_wb/to_s4）与正向/负控分别归档（round6_faultinjection）。
# 纪律（ADR-059 D5 / ADR-060 §5，用户 2026-10-06 口径）：
#   - 串行运行；跑前备份全局 memory_init1..8（副本 + sha256）；
#   - trap EXIT 无条件恢复（含中断/失败路径），恢复后复校 sha256；
#   - 不 set -e——每步退出码留痕，由人判读；
#   - 各跑独立 case 目录（同 seed 确定性重建），产物不互相覆盖。
# TB/脚本共享资产改动（透明呈报，全部 additive，默认关）：
#   tb_aiacc_top_v04_f_multi_stage.v —— G3_BARRIER/G3_BYPASS plusargs（=1 形态，
#       互斥）、信用沿计数、ur_read_trace 探针（smc_0 ports0-3）；
#       步骤④新增（[GATE4]，ADR-060 §6）：hbm_write_trace 8-SMC RAM 写口探针、
#       stb_ur_read_trace URA 读探针、CRD_STB/CRD_STBAXI 打印+沿计数（record-only）、
#       HBM_BRESP 打印+计数、WBARRIER（位掩码写落位屏障）、S4BARRIER（HBM 落实+B
#       响应双条件 dump 门）、G3_AXI_BP_B 反压注入（首个 AWVALID 起 force/release
#       bmc_ds2_bready，TB-only，AXI 合法停顿，RTL 零改动；前身为 W 通道注入，
#       因 wready 反压锁死写路径于 2026-10-07 退役（errata ⑤）；BVALID 沿武装
#       因结构性迟到同日退役（errata 6））；
#   run_sim_aiacc_top_f_multi_stage.sh —— EXTRA_PLUSARGS 透传口（默认空）。
set -uo pipefail
cd /home/juno1023/aiacc_compiler
source vcs-env.sh
PY=.venv/bin/python
SBX=/home/juno1023/u280_sim_v422
HBM_DIR="$SBX/data_engine/sram"
CASE="${1:?usage: m3lite_gate3_run.sh <case_base> [words=16] [seed=12345]}"
WORDS="${2:-16}"
SEED="${3:-12345}"
TS=$(date +%Y%m%d_%H%M%S)
BK="build/m3lite_hbm_backup/g3_$TS"
mkdir -p "$BK"

echo "=== [0/9] 备份全局 HBM 预载 → $BK ==="
for i in 1 2 3 4 5 6 7 8; do
    f="$HBM_DIR/memory_init$i.hex"
    if [ -f "$f" ]; then
        cp "$f" "$BK/memory_init$i.hex"
    else
        touch "$BK/memory_init$i.hex.ABSENT"
    fi
done
sha256sum "$HBM_DIR"/memory_init[1-8].hex > "$BK/SHA256SUMS.before" || true

restore_hbm() {
    echo "=== [trap] 恢复全局 HBM 预载 ← $BK ==="
    for i in 1 2 3 4 5 6 7 8; do
        f="$HBM_DIR/memory_init$i.hex"
        if [ -f "$BK/memory_init$i.hex" ]; then
            cp "$BK/memory_init$i.hex" "$f"
        elif [ -f "$BK/memory_init$i.hex.ABSENT" ] && [ -f "$f" ]; then
            rm -f "$f"
        fi
    done
    sha256sum "$HBM_DIR"/memory_init[1-8].hex > "$BK/SHA256SUMS.after_restore" || true
    if diff "$BK/SHA256SUMS.before" "$BK/SHA256SUMS.after_restore" > /dev/null; then
        echo "[trap] 恢复校验一致（sha256 全同）"
    else
        echo "[trap] WARN 恢复后与跑前不一致——人工核查 $BK"
    fi
}
trap restore_hbm EXIT

echo "=== [1/9] 生成正向 case：$CASE（words=$WORDS seed=$SEED，--with-store） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "$CASE" --words "$WORDS" --seed "$SEED" --with-store
GEN_POS=$?
echo "gen(positive) exit=$GEN_POS"
if [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/$CASE/g3_expect.json" ]; then
    echo "[G3-RUN] 正向 gen 未产出 g3_expect.json（exit=$GEN_POS）——跳过本驱动后续全部步骤，trap 仍恢复 HBM。"
    echo "[G3-RUN] 修复后重跑前清理：rm -rf $SBX/test_atomic_ops/multi_inst_stage/$CASE"
    exit 1
fi

EJ="$SBX/test_atomic_ops/multi_inst_stage/$CASE/g3_expect.json"
BL=$($PY -c "import json;print(json.load(open('$EJ'))['barrier']['exp_ldb_done'])")
BA=$($PY -c "import json;print(json.load(open('$EJ'))['barrier']['exp_ldbaxi_done'])")
BT=$($PY -c "import json;print(json.load(open('$EJ'))['barrier']['timeout_cycles'])")
WBK=$($PY -c "import json;print(json.load(open('$EJ'))['wbarrier']['bank'])")
WBN=$($PY -c "import json;print(json.load(open('$EJ'))['wbarrier']['writes'])")
WBS=$($PY -c "import json;print(json.load(open('$EJ'))['wbarrier']['stage'])")
WBT=$($PY -c "import json;print(json.load(open('$EJ'))['wbarrier']['timeout_cycles'])")
S4A=$($PY -c "import json;print(json.load(open('$EJ'))['s4barrier']['addr0'])")
S4N=$($PY -c "import json;print(json.load(open('$EJ'))['s4barrier']['writes'])")
S4B=$($PY -c "import json;print(json.load(open('$EJ'))['s4barrier']['bresp'])")
S4T=$($PY -c "import json;print(json.load(open('$EJ'))['s4barrier']['timeout_cycles'])")
G3_FULL="+G3_BARRIER=1 +G3_BARRIER_LDB=$BL +G3_BARRIER_LDBAXI=$BA +G3_BARRIER_TIMEOUT=$BT +G3_WBARRIER=1 +G3_WBARRIER_BANK=$WBK +G3_WBARRIER_WRITES=$WBN +G3_WBARRIER_STAGE=$WBS +G3_WBARRIER_TIMEOUT=$WBT +G3_S4_BARRIER=1 +G3_S4_ADDR0=$S4A +G3_S4_WRITES=$S4N +G3_S4_BRESP=$S4B +G3_S4_TIMEOUT=$S4T"
echo "屏障参数：LDB=$BL LDBAXI=$BA TIMEOUT=$BT | WBARRIER bank=$WBK writes=$WBN stage=$WBS to=$WBT | S4 addr0=$S4A writes=$S4N bresp=$S4B to=$S4T"

echo "=== [2/9] VCS 正向（信用屏障 + WBARRIER + S4 全屏障） ==="
EXTRA_PLUSARGS="$G3_FULL" \
    bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
    "multi_inst_stage/$CASE/smc_0" -s 0 2>&1 | tee "$BK/run_positive.console.log"
echo "vcs(positive) exit=${PIPESTATUS[0]}"

echo "=== [3/9] 比对正向（期望 GREEN，C0-C8） ==="
$PY verification_v2/tools/m3lite_gate3_compare.py \
    "$SBX/test_atomic_ops/multi_inst_stage/$CASE" --mode positive
echo "compare(positive) exit=$?"

echo "=== [4/9] 生成负控 case：${CASE}_neg（merged-3 并段单 trigger，含 STORE） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "${CASE}_neg" --words "$WORDS" --seed "$SEED" --merge-stages --with-store
GEN_NEG=$?
echo "gen(negative) exit=$GEN_NEG"
if [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_neg/g3_expect.json" ]; then
    echo "[G3-RUN] 负控 gen 未产出 g3_expect.json（exit=$GEN_NEG）——跳过后续，trap 仍恢复 HBM。"
    exit 1
fi

echo "=== [5/9] VCS 负控（merged-3 并段单 trigger，无 G3 plusargs） ==="
# 注：本段 stock compare 预期 COMPARE FAILED（bank2=垃圾乘积 vs output_bank 期望）
# ——这是竞态被踩中的独立第三意见，非故障；本脚本不 set -e，留痕后续跑 [6/9]。
bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
    "multi_inst_stage/${CASE}_neg/smc_0" -s 0 2>&1 | tee "$BK/run_negative.console.log"
echo "vcs(negative) exit=${PIPESTATUS[0]}（预期非 0：stock compare 撞见证伪）"

echo "=== [6/9] 比对负控（期望 NEGATIVE-CONTROL LIVE） ==="
$PY verification_v2/tools/m3lite_gate3_compare.py \
    "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_neg" --mode negative
echo "compare(negative) exit=$?"

# ── 故障注入三段（用户要求⑤，明确标注，与正向证据分别归档）─────────────────
BPN=400   # B 通道反压拍数：min_waited=BPN-100=300（100=注入点→S4 enter
          # 重叠+模型重驱动余量上界，首轮实测重叠 28）；另受精确区间断言约束

echo "=== [7/9] 故障注入 a：BP 反压保持力（${CASE}_bp，+G3_AXI_BP_B=$BPN） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "${CASE}_bp" --words "$WORDS" --seed "$SEED" --with-store --fi-bp-b $BPN
echo "gen(bp) exit=$?"
if [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_bp/g3_expect.json" ]; then
    echo "[G3-RUN] bp gen 未产出 g3_expect.json——跳过后续，trap 仍恢复 HBM。"
    exit 1
fi
EXTRA_PLUSARGS="$G3_FULL +G3_AXI_BP_B=$BPN" \
    bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
    "multi_inst_stage/${CASE}_bp/smc_0" -s 0 2>&1 | tee "$BK/run_bp.console.log"
echo "vcs(bp) exit=${PIPESTATUS[0]}（期望 0：B 反压只延迟响应，不改结果）"
$PY verification_v2/tools/m3lite_gate3_compare.py \
    "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_bp" --mode positive
echo "compare(bp, positive + waited>=$((BPN-100)) + 精确区间) exit=$?"

echo "=== [8/9] 故障注入 b：WBARRIER 超时（${CASE}_to_wb，+G3_WBARRIER_TIMEOUT=1） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "${CASE}_to_wb" --words "$WORDS" --seed "$SEED" --with-store
echo "gen(to_wb) exit=$?"
if [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_wb/g3_expect.json" ]; then
    echo "[G3-RUN] to_wb gen 未产出 g3_expect.json——跳过后续，trap 仍恢复 HBM。"
    exit 1
fi
# VCS $value$plusargs 首个匹配生效——同名参数不得出现两次，用模式替换覆盖
G3_TO_WB="${G3_FULL/+G3_WBARRIER_TIMEOUT=$WBT/+G3_WBARRIER_TIMEOUT=1}"
EXTRA_PLUSARGS="$G3_TO_WB" \
    bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
    "multi_inst_stage/${CASE}_to_wb/smc_0" -s 0 2>&1 | tee "$BK/run_to_wb.console.log"
echo "vcs(to_wb) exit=${PIPESTATUS[0]}（预期非 0：\$fatal 超时失败退出）"
$PY verification_v2/tools/m3lite_gate3_compare.py \
    "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_wb" --mode fatal_expected --expect-timeout wbarrier
echo "compare(to_wb, fatal_expected) exit=$?"

echo "=== [9/9] 故障注入 c：S4 超时（${CASE}_to_s4，+G3_S4_TIMEOUT=50 +G3_AXI_BP_B=$BPN） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "${CASE}_to_s4" --words "$WORDS" --seed "$SEED" --with-store
echo "gen(to_s4) exit=$?"
if [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_s4/g3_expect.json" ]; then
    echo "[G3-RUN] to_s4 gen 未产出 g3_expect.json——跳过后续，trap 仍恢复 HBM。"
    exit 1
fi
G3_TO_S4="${G3_FULL/+G3_S4_TIMEOUT=$S4T/+G3_S4_TIMEOUT=50}"
EXTRA_PLUSARGS="$G3_TO_S4 +G3_AXI_BP_B=$BPN" \
    bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
    "multi_inst_stage/${CASE}_to_s4/smc_0" -s 0 2>&1 | tee "$BK/run_to_s4.console.log"
echo "vcs(to_s4) exit=${PIPESTATUS[0]}（预期非 0：\$fatal 超时失败退出）"
$PY verification_v2/tools/m3lite_gate3_compare.py \
    "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_s4" --mode fatal_expected --expect-timeout s4barrier
echo "compare(to_s4, fatal_expected) exit=$?"

echo "=== 归档 sha256（五 case 目录 + 日志） ==="
sha256sum "$SBX/test_atomic_ops/multi_inst_stage/$CASE"/g3_*.json \
          "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_neg"/g3_*.json \
          "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_bp"/g3_*.json \
          "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_wb"/g3_*.json \
          "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_s4"/g3_*.json \
          "$BK"/*.log > "$BK/SHA256SUMS.reports" || true
echo "完成。trap 将恢复全局 HBM 预载。备份与日志：$BK"
