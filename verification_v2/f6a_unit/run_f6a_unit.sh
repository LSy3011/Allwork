#!/usr/bin/env bash
# run_f6a_unit.sh — F6-A 契约级单元 TB 运行器（唯一受支持入口）
#
#   usage: run_f6a_unit.sh legacy|fixed
#
# legacy：DUT=隔离副本内**原版** smi_iq_lsu_complete.v（经
#         lsu_complete_legacy_wrap 暴露契约端口）。期望矩阵见
#         f6a_unit_check.py（C1/C5 应 VIOLATION=复现原始竞态与同拍错误
#         漏放，其余 PASS）。
# fixed ：DUT=隔离副本内修复版（F6-A RTL 实施后才可编译）。
#
# 纪律：DUT 源仅取自隔离副本（F6A_ISO，默认
# /home/juno1023/u280_sim_v422_f6a_20261008）；每次运行把 DUT sha256
# 打印入日志（provenance）；编译产物在 work_<mode>/（可再生）。
set -euo pipefail

MODE="${1:?usage: run_f6a_unit.sh legacy|fixed}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO="${F6A_ISO:-/home/juno1023/u280_sim_v422_f6a_20261008}"
if [ "$MODE" = legacy ]; then
    DUT="$ISO/smi/smi_iq_lsu_complete.v"        # 原版冻结模块（逐字节）
else
    DUT="$ISO/smi/smi_iq_lsu_complete_f6.v"     # F6-A 阶段一修复模块
fi
WORK="$HERE/work_$MODE"
rm -rf "$WORK"    # 每轮全新 work 目录（对齐 run_f6b.sh 纪律，防旧日志假绿——
                  # 2026-10-10 实证：旧 check.txt 滞留，仅靠 stdout 才未误归档）
mkdir -p "$WORK"

[ -f "$DUT" ] || { echo "[F6A][FAIL] DUT 不存在: $DUT（隔离副本未建立？）"; exit 2; }
DUT_SHA=$(sha256sum "$DUT" | cut -d' ' -f1)
echo "[F6A] mode=$MODE dut=$DUT"
echo "[F6A] dut_sha256=$DUT_SHA"

# VCS 环境（许可证/PATH）
source /home/juno1023/u280_sim_v422/env.sh

cd "$WORK"
if [ "$MODE" = legacy ]; then
    vcs -sverilog -full64 -q +define+F6A_LEGACY \
        "$HERE/tb_f6a_lsu_complete.v" \
        "$HERE/lsu_complete_legacy_wrap.v" \
        "$DUT" -o simv_f6a > compile.log 2>&1 || {
            echo "[F6A][FAIL] 编译失败，见 $WORK/compile.log"; exit 2; }
else
    vcs -sverilog -full64 -q \
        "$HERE/tb_f6a_lsu_complete.v" \
        "$DUT" -o simv_f6a > compile.log 2>&1 || {
            echo "[F6A][FAIL] 编译失败（修复版未实现？），见 $WORK/compile.log"; exit 2; }
fi

./simv_f6a +vcs+lic+wait > sim.log 2>&1 || true   # 事实行即使有 $fatal 也已落盘

echo "[F6A] dut_sha256=$DUT_SHA" >> sim.log
python3 "$HERE/f6a_unit_check.py" --mode "$MODE" "$WORK/sim.log" | tee "$WORK/check.txt"
exit "${PIPESTATUS[0]}"   # 裁决退出码经管道显式传递（pipefail 双保险）
