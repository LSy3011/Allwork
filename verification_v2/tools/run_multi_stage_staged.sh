#!/usr/bin/env bash
# run_multi_stage_staged.sh —— multi_stage 裸运行器的 staging 防污染受控入口
# （2026-10-15 用户批准：「staging 纳入受控运行入口」）
#
# 背景（反例证据）：主线 TB 从全局目录 data_engine/sram/memory_init{1..8}.hex
# 预载 HBM，从不读 case 自带 hbm_data/；裸运行器不做 staging。
# 2026-10-15 未 staging 直跑 mul_scalar_llama_r2 chunk0000 吃到 m3lite 残留，
# 8/8 COMPARE FAIL（结果恰 ×1.25）而 SIM PASSED、$finish 拍与正确运行完全相同
# ——「周期稳定」不能替代输入正确性检查。归档
# verification_v2/artifacts/perf_hw_r2_vcs_rerun_20261015/（含污染物快照）。
#
# 固定序（用户原文）：独占共享预载资源 → 备份 → 装载当前 case 输入 →
# 逐文件校验 → 仿真与比较 → 无论成功失败都恢复并校验。
#
# 锁：与 m3lite 线同一把 = $HBM_DIR/.m3lite_gate3.flock（文件名为历史遗留；
# 2026-10-15 用户裁定所有相关运行器共用同一共享资源锁，故不另起新锁文件）。
#
# 用法：
#   run_multi_stage_staged.sh <case_rel>        # 如 multi_inst_stage/<case>/smc_0
# 环境：
#   SIM_ROOT        默认 ~/u280_sim_v422
#   EXTRA_PLUSARGS  透传给裸运行器（如 WBARRIER 门控 plusarg）
# 退出码：0=仿真+比较通过；2=用法/前提缺；3=缺 case 输入；4=装载校验失败；
#   5=仿真或比较 FAIL；6=备份失败；7=恢复或恢复校验失败（最重：全局目录
#   可能仍持 case 数据，须人工处置，绝不可静默继续）。
set -u

TAG="[STAGED-RUN]"
SIM_ROOT="${SIM_ROOT:-$HOME/u280_sim_v422}"
HBM_DIR="$SIM_ROOT/data_engine/sram"
LOCK_FILE="$HBM_DIR/.m3lite_gate3.flock"
RUNNER_REL="multi_inst_stage"
TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HBM_PRELOAD="$TOOLS_DIR/hbm_preload.py"
PY="${PYTHON:-python3}"

REL="${1:-}"
if [[ -z "$REL" ]]; then
    echo "$TAG 用法：$0 <case_rel>（如 multi_inst_stage/<case>/smc_0）" >&2
    exit 2
fi
CASE_DIR="$SIM_ROOT/test_atomic_ops/$(dirname "$REL")"
RUNNER_DIR="$SIM_ROOT/sim_multi_stage"
RUNNER="$RUNNER_DIR/run_sim_aiacc_top_f_multi_stage.sh"
for p in "$HBM_DIR" "$RUNNER" "$HBM_PRELOAD"; do
    if [[ ! -e "$p" ]]; then
        echo "$TAG RED 前提缺失：$p" >&2
        exit 2
    fi
done
if [[ ! -d "$CASE_DIR/hbm_data" ]]; then
    echo "$TAG RED 缺输入目录：$CASE_DIR/hbm_data ——拒跑（不仿真）" >&2
    exit 3
fi

echo "$TAG case=$REL"
echo "$TAG 等待共享预载目录锁（flock $LOCK_FILE）"
exec 9>"$LOCK_FILE"
flock 9
echo "$TAG 已取锁"

BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hbm_staged_backup.XXXXXXXX")"
export STAGED_BACKUP_DIR="$BACKUP_DIR"   # 测试钩子；正常运行器不使用
RESTORED=0
NEED_RESTORE=0   # 备份成功后才置 1：备份前全局未动，失败路径不触发恢复

do_restore() {
    # 无论成败都恢复并校验；失败=最重档 7
    "$PY" "$HBM_PRELOAD" restore-backup --backup-dir "$BACKUP_DIR" --hbm-dir "$HBM_DIR"
    rc=$?
    if [[ $rc -eq 0 ]]; then
        RESTORED=1
        rm -rf "$BACKUP_DIR"
        echo "$TAG 恢复完成并已校验；备份已清理"
    else
        echo "$TAG CRITICAL: 恢复失败（hbm_preload rc=$rc）——全局 HBM 目录" >&2
        echo "$TAG 可能仍持 case 数据！备份留存于 $BACKUP_DIR ，须人工处置" >&2
    fi
    return $rc
}
# 兜底：脚本被正常退出路径之外的方式终止时仍尝试恢复
on_exit() {
    if [[ $NEED_RESTORE -eq 1 && $RESTORED -eq 0 && -d "$BACKUP_DIR" ]]; then
        echo "$TAG 兜底恢复（异常/提前退出路径）" >&2
        do_restore || exit 7
    fi
}
trap on_exit EXIT

echo "$TAG [1/4] 备份现行全局 HBM → $BACKUP_DIR"
"$PY" "$HBM_PRELOAD" backup --hbm-dir "$HBM_DIR" --backup-dir "$BACKUP_DIR" || exit 6
NEED_RESTORE=1

echo "$TAG [2/4] 装载 case 输入（$CASE_DIR/hbm_data → 全局）并逐文件校验"
# 注：stage 先查 8 源齐备再写；若复制中途失败（全局已半装载），trap 兜底恢复
"$PY" "$HBM_PRELOAD" stage --src-dir "$CASE_DIR/hbm_data" --hbm-dir "$HBM_DIR" || exit 4

echo "$TAG [3/4] 仿真与比较（裸运行器；EXTRA_PLUSARGS=${EXTRA_PLUSARGS:-<空>}）"
RUN_RC=0
(cd "$RUNNER_DIR" && ./"$(basename "$RUNNER")" "$REL") || RUN_RC=$?

echo "$TAG [4/4] 恢复备份并校验（运行器退出码=$RUN_RC）"
do_restore
REST_RC=$?
if [[ $REST_RC -ne 0 ]]; then
    exit 7
fi
if [[ $RUN_RC -ne 0 ]]; then
    echo "$TAG 总判：RED（仿真或比较 FAIL，rc=$RUN_RC）；环境已恢复"
    exit 5
fi
echo "$TAG 总判：GREEN（仿真+比较通过；环境已恢复并校验）"
exit 0
