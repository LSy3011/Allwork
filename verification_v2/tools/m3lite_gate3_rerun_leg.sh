#!/bin/bash
# m3lite_gate3_rerun_leg.sh —— M3-lite 门③ 单腿干净重跑的**唯一受支持入口**
# （任务 1，用户 2026-10-07；drive-3 污染运行教训：裸 run_sim 绕过预载恢复
#  → 以 a5a5 旧图案跑出数据面假红，ADR-060 errata ⑥/⑦）。
#
# 用法：
#   bash verification_v2/tools/m3lite_gate3_rerun_leg.sh <case_name> [plusargs...]
# 例：
#   bash verification_v2/tools/m3lite_gate3_rerun_leg.sh m3lite_g4_lc_w16_bp \
#        "+G3_BARRIER=1 +G3_BARRIER_LDB=1 +G3_BARRIER_LDBAXI=1 ..."
#
# 固定四步序（每步失败即拒跑）：
#   1) flock 串行占用共享预载目录（与 run.sh 同一把锁）；
#   2) hbm_preload.py restore：从该 case 冻结快照恢复全局预载并逐文件
#      sha256 复核（快照 vs expect 冻结哈希不符 = 冻结证据不可信 = 拒跑）；
#   3) 明示 TB 指纹差异：当前 TB sha256 vs 该 case 既有 g3_report_*.json
#      记录的测试时指纹（如有）；
#   4) 调 run_sim（plusargs 经 EXTRA_PLUSARGS 透传）。
# 跑完提示比对命令（比对须显式另跑，本脚本不代判）。
set -uo pipefail
cd /home/juno1023/aiacc_compiler
source vcs-env.sh
PY=.venv/bin/python
SBX=/home/juno1023/u280_sim_v422
HBM_DIR="$SBX/data_engine/sram"
TB_FILE="$SBX/sim_multi_stage/tb_aiacc_top_v04_f_multi_stage.v"
CASE="${1:?usage: m3lite_gate3_rerun_leg.sh <case_name> [plusargs...]}"
PLUSARGS="${2:-}"
CASE_DIR="$SBX/test_atomic_ops/multi_inst_stage/$CASE"

echo "=== [rerun 1/4] flock 串行占用共享预载目录 ==="
exec 9>"$HBM_DIR/.m3lite_gate3.flock"
flock 9
echo "[rerun] 已取锁（$HBM_DIR/.m3lite_gate3.flock）"

echo "=== [rerun 2/4] 恢复+校验该 case 冻结预载快照 ==="
if [ ! -d "$CASE_DIR" ]; then
    echo "[rerun] RED case 目录不存在：$CASE_DIR"
    exit 1
fi
$PY verification_v2/tools/hbm_preload.py restore --case-dir "$CASE_DIR" --hbm-dir "$HBM_DIR"
RC=$?
if [ $RC -ne 0 ]; then
    echo "[rerun] RED 预载恢复/校验失败（rc=$RC）——拒跑 VCS（drive-3 教训："
    echo "        依赖正确初态的结论只能由恢复预载后的干净运行支撑）"
    exit $RC
fi

echo "=== [rerun 3/4] TB 指纹差异明示（当前 vs 该 case 既有报告记录） ==="
TB_NOW=$(sha256sum "$TB_FILE" | awk '{print $1}')
echo "[rerun] 当前 TB 指纹 = $TB_NOW"
for r in "$CASE_DIR"/g3_report_*.json; do
    [ -f "$r" ] || continue
    PREV=$($PY -c "import json,sys
try:
    p = json.load(open('$r')).get('provenance') or {}
    print(p.get('tb_sha256_sim') or p.get('tb_sha256_compare_time') or 'NONE')
except Exception:
    print('NONE')" 2>&1)
    if [ "$PREV" = "NONE" ]; then
        echo "[rerun]   $r：无 provenance 指纹（旧格式报告，测试时指纹未记录）"
    elif [ "$PREV" = "$TB_NOW" ]; then
        echo "[rerun]   $r：指纹一致 ✓"
    else
        echo "[rerun]   $r：⚠ 差异 记录=$PREV 当前=$TB_NOW ——解读旧报告以其记录指纹为准"
    fi
done

echo "=== [rerun 4/4] VCS ==="
EXTRA_PLUSARGS="$PLUSARGS" \
    bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
    "multi_inst_stage/$CASE/smc_0" -s 0
echo "[rerun] vcs exit=$?"
echo "[rerun] 比对请显式另跑，例："
echo "  $PY verification_v2/tools/m3lite_gate3_compare.py $CASE_DIR --mode positive --tb-sha256 $TB_NOW"
