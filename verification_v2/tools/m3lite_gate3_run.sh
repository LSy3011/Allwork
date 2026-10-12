#!/bin/bash
# m3lite_gate3_run.sh —— ADR-060 / M3-lite 门③步骤③+④：完整链路驱动。
# 用法：bash verification_v2/tools/m3lite_gate3_run.sh <case_base> [words] [seed]
# 五跑（步骤④，ADR-060 §6，用户 2026-10-07 五条要求）：
#   [正] multi_inst_stage/<case_base>       3 段（LOAD→COMPUTE→STORE）+ 全屏障
#        （G3_BARRIER 信用 + G3_WBARRIER 写落位 + G3_S4_BARRIER STORE 完成闸），
#        期望比对 GREEN（C0-C8）；
#   [负] <case_base>_neg  --merge-stages --with-store = merged-3 并段单 trigger
#        （无任何 G3 plusarg），期望 NEGATIVE-CONTROL LIVE（C1/C3/C3s/C6/C8 任红）；
#   [故] <case_base>_bp   反压负控：+G3_AXI_BP_B=400（明确标注的故障注入，
#        B 通道 v2：首个 AWVALID 上升沿起押 bready=0 共 400 拍，写数据路不动）。
#        ⚠ 期望再改写（2026-10-08 用户裁决，ADR-060 errata 10 / 任务2收口）：
#        边界注入已实证为非法注入点（分裂视图+孤儿 B 死锁，errata ⑧/BPDBG），
#        合法源端反压（us3bp n=150）已恢复通过。TB 新增常驻「响应账目异常
#        持续检查」（pending&cnt_empty 连续 16 个有效采样拍 $fatal）后，本腿
#        不再持死至 S4 超时——幻影后第 16 拍 [G3_RESPACCT][FATAL] 提前终结
#        （~1208 vs 原 62385000ps）。本腿现行定位 = 负控准确命中新报警：
#        比对跑双模式：positive（信息性，留痕不判）+ fatal_expected
#        respacct（应 PASS）。历史长时间死锁证据冻结于 round6/bpdbg 归档。
#        退役史：W 通道反压（+G3_AXI_BP_W）2026-10-07 首轮实测 300 拍
#        wready 反压写路径未恢复（errata ⑤）；BVALID 沿武装同日退役——
#        B 活窗仅 1 拍、force 结构性迟到（errata ⑥）。
#   [超] <case_base>_to_wb  +G3_WBARRIER_TIMEOUT=1 → 期望 $fatal 失败退出，
#        比对 --mode fatal_expected --expect-timeout wbarrier；
#   [超] <case_base>_to_s4  +G3_S4_TIMEOUT=50 +G3_AXI_BP_B=400 → 期望 $fatal，
#        比对 --mode fatal_expected --expect-timeout respacct
#        （同为边界注入腿，RESPACCT 于 ~1208 提前终结，S4 超时不再到达；
#        限制落账：S4BARRIER TIMEOUT 通道自此失去在套激发，通道保留未压实）。
#   故障注入三段（bp/to_wb/to_s4）与正向/负控分别归档（round6_faultinjection）。
# 纪律（ADR-059 D5 / ADR-060 §5，用户 2026-10-06 口径）：
#   - 串行运行；跑前备份全局 memory_init1..8（副本 + sha256）；
#   - trap EXIT 无条件恢复（含中断/失败路径），恢复后复校 sha256；
#   - 不 set -e——每步退出码留痕，由人判读 + 末行汇总退出码（见接力轮修补②）；
#   - 各跑独立 case 目录（同 seed 确定性重建），产物不互相覆盖。
# 任务 1 增补（用户 2026-10-07，ADR-060 errata ⑦）：
#   - flock 串行占用共享预载目录（$HBM_DIR/.m3lite_gate3.flock，整跑持有）；
#   - 每腿 VCS 前经 hbm_preload.py restore 从该 case 冻结快照恢复全局预载
#     并逐文件 sha256 复核（快照与 expect 冻结哈希不符=拒跑）——单独重跑
#     请用 m3lite_gate3_rerun_leg.sh（内部同一路径），禁止裸 run_sim；
#   - 测试时 TB 指纹每跑记录并传入比对器，报告 provenance 并列比对时刻
#     指纹，差异记 WARN 如实披露。
# 任务 1 接力轮增补与漏洞修补（2026-10-07 终端接力轮，用户验收时发现漏洞）：
#   - 每腿 VCS 输出 tee 留存 $BK/run_<leg>.console.log；跑末归档五腿报告与
#     日志 sha256 → $BK/SHA256SUMS.reports（接力轮已落地并实测）。
#   - 漏洞修补①：gen 非零退出（如 case 目录已存在）即 fail-fast 拒跑——
#     此前仅查 g3_expect.json 存在性，gen 失败后旧产物会被沿用继续跑
#     （接力轮实测发现）；现五腿 gen 全部「exit≠0 或产物缺失 → exit 1」。
#   - 漏洞修补②：脚本最终退出码 = 五腿期望形态汇总（exit=0 ⇔ 全部符合
#     预注册期望；任何方向偏差——包括「意外变绿」——均 exit=1 交人工
#     复核，不静默通过）。各腿期望形态：
#       positive：vcs==0 且 compare(positive)==0；
#       negative：compare(negative)==0（vcs 退出码留痕不判——stock
#         compare 撞见预期非 0）；
#       bp：vcs≠0（[G3_RESPACCT][FATAL] 确发）且
#         compare(fatal_expected --expect-timeout respacct)==0
#         （positive 信息性比对退出码留痕不判）；
#       to_wb：vcs≠0（WBARRIER TIMEOUT $fatal 确发）且
#         compare(fatal_expected wbarrier)==0；
#       to_s4：vcs≠0（[G3_RESPACCT][FATAL] 确发）且
#         compare(fatal_expected respacct)==0；
#       任一腿预载门拒跑 = 该腿 FAIL 入汇总。
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
SBX="${M3_SBX:-/home/juno1023/u280_sim_v422}"   # 2026-10-10 沙箱化：M3_SBX 覆盖指向
                                                # F6 全树沙箱；缺省=原树（默认行为不变）
HBM_DIR="$SBX/data_engine/sram"
CASE="${1:?usage: m3lite_gate3_run.sh <case_base> [words=16] [seed=12345]}"
WORDS="${2:-16}"
SEED="${3:-12345}"
TS=$(date +%Y%m%d_%H%M%S)
BK="build/m3lite_hbm_backup/g3_$TS"
mkdir -p "$BK"

# 任务 1：整跑串行占用共享预载目录（备份/各腿 gen+sim/trap 恢复全在锁内）
exec 9>"$HBM_DIR/.m3lite_gate3.flock"
flock 9
echo "[G3-RUN] 已取共享预载目录串行锁（flock $HBM_DIR/.m3lite_gate3.flock）"
TB_FILE="$SBX/sim_multi_stage/tb_aiacc_top_v04_f_multi_stage.v"
TB_SHA=$(sha256sum "$TB_FILE" | awk '{print $1}')
echo "[G3-RUN] 测试时 TB 指纹 = $TB_SHA（$TB_FILE）"
LEG_FAILS=""   # 汇总退出码（接力轮修补②）：每腿偏离预注册期望即追加

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

leg_preload_gate() {  # 任务 1：每腿 VCS 前从该 case 冻结快照恢复+校验全局预载
    $PY verification_v2/tools/hbm_preload.py restore \
        --case-dir "$SBX/test_atomic_ops/multi_inst_stage/$1" --hbm-dir "$HBM_DIR"
}

echo "=== [1/9] 生成正向 case：$CASE（words=$WORDS seed=$SEED，--with-store） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "$CASE" --words "$WORDS" --seed "$SEED" --sandbox "$SBX" --with-store
GEN_POS=$?
echo "gen(positive) exit=$GEN_POS"
if [ "$GEN_POS" -ne 0 ] || [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/$CASE/g3_expect.json" ]; then
    echo "[G3-RUN] RED 正向 gen 失败（exit=$GEN_POS）或产物缺失——fail-fast 拒跑，禁沿用旧产物；trap 仍恢复 HBM。"
    echo "[G3-RUN] 若因 case 目录已存在：换基名重跑，或人工清理 $SBX/test_atomic_ops/multi_inst_stage/$CASE 后重跑。"
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
if leg_preload_gate "$CASE"; then
    EXTRA_PLUSARGS="$G3_FULL" \
        bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
        "multi_inst_stage/$CASE/smc_0" -s 0 2>&1 | tee "$BK/run_positive.console.log"
    VCS_POS=${PIPESTATUS[0]}
    echo "vcs(positive) exit=$VCS_POS"
    echo "=== [3/9] 比对正向（期望 GREEN，C0-C8） ==="
    $PY verification_v2/tools/m3lite_gate3_compare.py \
        "$SBX/test_atomic_ops/multi_inst_stage/$CASE" --mode positive \
        --tb-sha256 "$TB_SHA" --sandbox "$SBX"
    CMP_POS=$?
    echo "compare(positive) exit=$CMP_POS"
    if [ "$VCS_POS" -eq 0 ] && [ "$CMP_POS" -eq 0 ]; then
        echo "[G3-RUN] 腿判 positive：AS-EXPECTED"
    else
        echo "[G3-RUN] 腿判 positive：UNEXPECTED（期望 vcs=0 且 compare=0）"
        LEG_FAILS="$LEG_FAILS positive"
    fi
else
    echo "[G3-RUN] RED 预载门拒跑（positive）——本腿无 VCS 无比对"
    LEG_FAILS="$LEG_FAILS positive(preload-gate)"
fi

echo "=== [4/9] 生成负控 case：${CASE}_neg（merged-3 并段单 trigger，含 STORE） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "${CASE}_neg" --words "$WORDS" --seed "$SEED" --sandbox "$SBX" --merge-stages --with-store
GEN_NEG=$?
echo "gen(negative) exit=$GEN_NEG"
if [ "$GEN_NEG" -ne 0 ] || [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_neg/g3_expect.json" ]; then
    echo "[G3-RUN] RED 负控 gen 失败（exit=$GEN_NEG）或产物缺失——fail-fast 拒跑，禁沿用旧产物；trap 仍恢复 HBM。"
    exit 1
fi

echo "=== [5/9] VCS 负控（merged-3 并段单 trigger，无 G3 plusargs） ==="
# 注：本段 stock compare 预期 COMPARE FAILED（bank2=垃圾乘积 vs output_bank 期望）
# ——这是竞态被踩中的独立第三意见，非故障；本脚本不 set -e，留痕后续跑 [6/9]。
if leg_preload_gate "${CASE}_neg"; then
    bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
        "multi_inst_stage/${CASE}_neg/smc_0" -s 0 2>&1 | tee "$BK/run_negative.console.log"
    echo "vcs(negative) exit=${PIPESTATUS[0]}（预期非 0：stock compare 撞见证伪；留痕不判）"
    echo "=== [6/9] 比对负控（期望 NEGATIVE-CONTROL LIVE） ==="
    $PY verification_v2/tools/m3lite_gate3_compare.py \
        "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_neg" --mode negative \
        --tb-sha256 "$TB_SHA" --sandbox "$SBX"
    CMP_NEG=$?
    echo "compare(negative) exit=$CMP_NEG"
    if [ "$CMP_NEG" -eq 0 ]; then
        echo "[G3-RUN] 腿判 negative：AS-EXPECTED"
    else
        echo "[G3-RUN] 腿判 negative：UNEXPECTED（期望 NEGATIVE-CONTROL LIVE）"
        LEG_FAILS="$LEG_FAILS negative"
    fi
else
    echo "[G3-RUN] RED 预载门拒跑（negative）——本腿无 VCS 无比对"
    LEG_FAILS="$LEG_FAILS negative(preload-gate)"
fi

# ── 故障注入三段（用户要求⑤，明确标注，与正向证据分别归档）─────────────────
BPN=400   # B 通道反压拍数（errata 10 后：RESPACCT 于幻影后第 16 拍提前终结，
          # BPN 只需覆盖幻影形成，400 保留沿用；旧 min_waited 语义已退役）

echo "=== [7/9] 故障注入 a：BP 边界注入负控（${CASE}_bp，+G3_AXI_BP_B=$BPN，期望 RESPACCT 命中） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "${CASE}_bp" --words "$WORDS" --seed "$SEED" --sandbox "$SBX" --with-store --fi-bp-b $BPN
GEN_BP=$?
echo "gen(bp) exit=$GEN_BP"
if [ "$GEN_BP" -ne 0 ] || [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_bp/g3_expect.json" ]; then
    echo "[G3-RUN] RED bp gen 失败（exit=$GEN_BP）或产物缺失——fail-fast 拒跑，禁沿用旧产物；trap 仍恢复 HBM。"
    exit 1
fi
if leg_preload_gate "${CASE}_bp"; then
    EXTRA_PLUSARGS="$G3_FULL +G3_AXI_BP_B=$BPN" \
        bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
        "multi_inst_stage/${CASE}_bp/smc_0" -s 0 2>&1 | tee "$BK/run_bp.console.log"
    VCS_BP=${PIPESTATUS[0]}
    echo "vcs(bp) exit=$VCS_BP（errata 10 后预期非 0：[G3_RESPACCT][FATAL] 提前终结）"
    # 双模式比对：positive=信息性（留痕不判）；fatal_expected respacct=
    # 正式判定（负控准确命中响应账目异常报警、其后零 dump → PASS）。
    $PY verification_v2/tools/m3lite_gate3_compare.py \
        "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_bp" --mode positive \
        --tb-sha256 "$TB_SHA" --sandbox "$SBX"
    echo "compare(bp, positive 信息性) exit=$?（信息性，留痕不判）"
    $PY verification_v2/tools/m3lite_gate3_compare.py \
        "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_bp" \
        --mode fatal_expected --expect-timeout respacct --tb-sha256 "$TB_SHA" --sandbox "$SBX"
    CMP_BP_FE=$?
    echo "compare(bp, fatal_expected respacct 正式判定) exit=$CMP_BP_FE"
    if [ "$VCS_BP" -ne 0 ] && [ "$CMP_BP_FE" -eq 0 ]; then
        echo "[G3-RUN] 腿判 bp：AS-EXPECTED"
    else
        echo "[G3-RUN] 腿判 bp：UNEXPECTED（期望 vcs≠0 且 fatal_expected respacct=0；若信息性变绿=恢复信号，须人工复核不得静默通过）"
        LEG_FAILS="$LEG_FAILS bp"
    fi
else
    echo "[G3-RUN] RED 预载门拒跑（bp）——本腿无 VCS 无比对"
    LEG_FAILS="$LEG_FAILS bp(preload-gate)"
fi

echo "=== [8/9] 故障注入 b：WBARRIER 超时（${CASE}_to_wb，+G3_WBARRIER_TIMEOUT=1） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "${CASE}_to_wb" --words "$WORDS" --seed "$SEED" --sandbox "$SBX" --with-store
GEN_TOWB=$?
echo "gen(to_wb) exit=$GEN_TOWB"
if [ "$GEN_TOWB" -ne 0 ] || [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_wb/g3_expect.json" ]; then
    echo "[G3-RUN] RED to_wb gen 失败（exit=$GEN_TOWB）或产物缺失——fail-fast 拒跑，禁沿用旧产物；trap 仍恢复 HBM。"
    exit 1
fi
# VCS $value$plusargs 首个匹配生效——同名参数不得出现两次，用模式替换覆盖
G3_TO_WB="${G3_FULL/+G3_WBARRIER_TIMEOUT=$WBT/+G3_WBARRIER_TIMEOUT=1}"
if leg_preload_gate "${CASE}_to_wb"; then
    EXTRA_PLUSARGS="$G3_TO_WB" \
        bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
        "multi_inst_stage/${CASE}_to_wb/smc_0" -s 0 2>&1 | tee "$BK/run_to_wb.console.log"
    VCS_TOWB=${PIPESTATUS[0]}
    echo "vcs(to_wb) exit=$VCS_TOWB（预期非 0：\$fatal 超时失败退出）"
    $PY verification_v2/tools/m3lite_gate3_compare.py \
        "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_wb" \
        --mode fatal_expected --expect-timeout wbarrier --tb-sha256 "$TB_SHA" --sandbox "$SBX"
    CMP_TOWB=$?
    echo "compare(to_wb, fatal_expected) exit=$CMP_TOWB"
    if [ "$VCS_TOWB" -ne 0 ] && [ "$CMP_TOWB" -eq 0 ]; then
        echo "[G3-RUN] 腿判 to_wb：AS-EXPECTED"
    else
        echo "[G3-RUN] 腿判 to_wb：UNEXPECTED（期望 vcs≠0 且 fatal_expected=0）"
        LEG_FAILS="$LEG_FAILS to_wb"
    fi
else
    echo "[G3-RUN] RED 预载门拒跑（to_wb）——本腿无 VCS 无比对"
    LEG_FAILS="$LEG_FAILS to_wb(preload-gate)"
fi

echo "=== [9/9] 故障注入 c：S4 超时（${CASE}_to_s4，+G3_S4_TIMEOUT=50 +G3_AXI_BP_B=$BPN，errata 10 后期望 RESPACCT 提前命中） ==="
$PY verification_v2/tools/m3lite_gate3_case_gen.py --name "${CASE}_to_s4" --words "$WORDS" --seed "$SEED" --sandbox "$SBX" --with-store
GEN_TOS4=$?
echo "gen(to_s4) exit=$GEN_TOS4"
if [ "$GEN_TOS4" -ne 0 ] || [ ! -f "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_s4/g3_expect.json" ]; then
    echo "[G3-RUN] RED to_s4 gen 失败（exit=$GEN_TOS4）或产物缺失——fail-fast 拒跑，禁沿用旧产物；trap 仍恢复 HBM。"
    exit 1
fi
G3_TO_S4="${G3_FULL/+G3_S4_TIMEOUT=$S4T/+G3_S4_TIMEOUT=50}"
if leg_preload_gate "${CASE}_to_s4"; then
    EXTRA_PLUSARGS="$G3_TO_S4 +G3_AXI_BP_B=$BPN" \
        bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
        "multi_inst_stage/${CASE}_to_s4/smc_0" -s 0 2>&1 | tee "$BK/run_to_s4.console.log"
    VCS_TOS4=${PIPESTATUS[0]}
    echo "vcs(to_s4) exit=$VCS_TOS4（errata 10 后预期非 0：[G3_RESPACCT][FATAL] 提前终结，S4 超时不再到达）"
    $PY verification_v2/tools/m3lite_gate3_compare.py \
        "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_s4" \
        --mode fatal_expected --expect-timeout respacct --tb-sha256 "$TB_SHA" --sandbox "$SBX"
    CMP_TOS4=$?
    echo "compare(to_s4, fatal_expected respacct) exit=$CMP_TOS4"
    if [ "$VCS_TOS4" -ne 0 ] && [ "$CMP_TOS4" -eq 0 ]; then
        echo "[G3-RUN] 腿判 to_s4：AS-EXPECTED"
    else
        echo "[G3-RUN] 腿判 to_s4：UNEXPECTED（期望 vcs≠0 且 fatal_expected respacct=0）"
        LEG_FAILS="$LEG_FAILS to_s4"
    fi
else
    echo "[G3-RUN] RED 预载门拒跑（to_s4）——本腿无 VCS 无比对"
    LEG_FAILS="$LEG_FAILS to_s4(preload-gate)"
fi

echo "=== 归档 sha256（五 case 目录 + 日志） ==="
sha256sum "$SBX/test_atomic_ops/multi_inst_stage/$CASE"/g3_*.json \
          "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_neg"/g3_*.json \
          "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_bp"/g3_*.json \
          "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_wb"/g3_*.json \
          "$SBX/test_atomic_ops/multi_inst_stage/${CASE}_to_s4"/g3_*.json \
          "$BK"/*.log > "$BK/SHA256SUMS.reports" || true
echo "完成。trap 将恢复全局 HBM 预载。备份与日志：$BK"

echo "=== 汇总（接力轮修补②：exit=0 ⇔ 五腿全部符合预注册期望） ==="
if [ -z "$LEG_FAILS" ]; then
    echo "[G3-RUN] 总判：ALL-LEGS-AS-EXPECTED"
    exit 0
else
    echo "[G3-RUN] 总判：RED——偏离期望的腿：$LEG_FAILS"
    echo "[G3-RUN] 任何方向偏差（含意外变绿）均须人工复核，不得静默通过。"
    exit 1
fi
