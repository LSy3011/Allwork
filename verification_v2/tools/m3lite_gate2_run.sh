#!/bin/bash
# m3lite_gate2_run.sh —— ADR-059 v2 门②：备份全局 HBM 预载 → 生成 → VCS → 比对 → 恢复
# 用法：bash verification_v2/tools/m3lite_gate2_run.sh <case_name> [生成器参数...]
# 纪律（ADR-059 D5，用户 2026-10-06 口径 b）：
#   - 串行运行；跑前备份全局 memory_init1..8（副本 + sha256）；
#   - trap EXIT 无条件恢复（含中断/失败路径），恢复后复校 sha256；
#   - 不 set -e——每步退出码留痕，由人判读。
set -uo pipefail
cd /home/juno1023/aiacc_compiler
source vcs-env.sh
PY=.venv/bin/python
SBX="${M3_SBX:-/home/juno1023/u280_sim_v422}"   # 2026-10-10 沙箱化：M3_SBX 覆盖指向
                                                # F6 全树沙箱；缺省=原树（默认行为不变）
HBM_DIR="$SBX/data_engine/sram"
CASE="${1:?usage: m3lite_gate2_run.sh <case_name> [gen args...]}"
shift
GEN_ARGS=("$@")
TS=$(date +%Y%m%d_%H%M%S)
BK="build/m3lite_hbm_backup/$TS"
mkdir -p "$BK"

echo "=== [0/4] 备份全局 HBM 预载 → $BK ==="
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

echo "=== [1/4] 生成 case（写全局 memory_init，副本留存 case） ==="
$PY verification_v2/tools/ldb_review_case_gen.py --name "$CASE" --sandbox "$SBX" "${GEN_ARGS[@]}"
echo "gen exit=$?"

echo "=== [2/4] VCS ==="
bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
    "multi_inst_stage/$CASE/smc_0" -s 0
echo "vcs exit=$?"

echo "=== [3/4] UR 窗口比对（B1-B6 含完成时序三子项） ==="
$PY verification_v2/tools/ur_window_compare.py \
    "$SBX/test_atomic_ops/multi_inst_stage/$CASE"
echo "compare exit=$?"

echo "=== [4/4] 完成（trap 将恢复全局 HBM 预载） ==="
