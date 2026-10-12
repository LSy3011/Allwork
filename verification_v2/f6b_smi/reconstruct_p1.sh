#!/usr/bin/env bash
# reconstruct_p1.sh — 用封存 diff 重建阶段一（2026-10-10 补丁态）DUT 树
#
# 用途：判据 v2 下复跑 fixed_p1（阶段二就地改动已覆盖工作副本）。
# 方法：整树 rsync 当前隔离副本，再按归档 f6_phase1_rtl_20261010/diffs/
#       精确回退/重建 7 个文件：
#         冻结未动（=原树即阶段一态）：smi_iq_lsu_complete.v（隔离副本从未改）、
#           smi_ctrl_regs.v（阶段一未改——当前副本已被阶段二改动，须回退原树）
#         基线+累计 diff：smi_rtl.f / smi_top.v / smi_iq.v / smi_iq_dly_queen.v /
#           smi_iq_inst_queen.v
#         新模块全量 diff：smi_iq_lsu_complete_f6.v（.new.diff）
#       逐字节 sha256 对照 BUILD_FINGERPRINT.txt，全部一致才输出 ALL-OK。
# 纪律：原树/隔离副本均只读（本脚本只读它们，写入仅在目标临时树）。
set -euo pipefail

DST="${1:-/home/juno1023/u280_sim_v422_f6p1_recon}"
ISO="/home/juno1023/u280_sim_v422_f6a_20261008"
ORIG="/home/juno1023/u280_sim_v422"
ARCH="/home/juno1023/aiacc_compiler/verification_v2/artifacts/f6_phase1_rtl_20261010"
FP="$ARCH/BUILD_FINGERPRINT.txt"
D="$ARCH/diffs"

for x in smi_rtl.f.diff smi_top.v.diff smi_iq.v.diff smi_iq_dly_queen.v.diff \
         smi_iq_inst_queen.v.diff smi_iq_lsu_complete_f6.v.new.diff; do
    [ -f "$D/$x" ] || { echo "[RECON][FAIL] 缺 $D/$x"; exit 1; }
done

rm -rf "$DST"
mkdir -p "$DST"
rsync -a "$ISO/" "$DST/"
cd "$DST"

# 阶段二已动的 ctrl_regs 回退冻结态（=阶段一态）
cp "$ORIG/smi/smi_ctrl_regs.v" smi/smi_ctrl_regs.v
echo "[RECON] smi/smi_ctrl_regs.v <- 冻结基线（阶段一未改）"

# 基线 + 累计 diff
for f in smi_rtl.f smi_top.v smi_iq.v smi_iq_dly_queen.v smi_iq_inst_queen.v; do
    cp "$ORIG/smi/$f" "smi/$f"
    patch -s "smi/$f" < "$D/$f.diff"
    echo "[RECON] rebuilt smi/$f <- $f.diff"
done

# 新模块（对空文件打全量 diff）
rm -f smi/smi_iq_lsu_complete_f6.v
cp /dev/null smi/smi_iq_lsu_complete_f6.v
patch -s smi/smi_iq_lsu_complete_f6.v < "$D/smi_iq_lsu_complete_f6.v.new.diff"
echo "[RECON] rebuilt smi/smi_iq_lsu_complete_f6.v <- .new.diff"

# 逐字节对照指纹（6 文件全列于 BUILD_FINGERPRINT）
FAIL=0
while read -r EXP PTH; do
    case "$EXP" in ''|\#*) continue ;; esac
    REL="${PTH#/home/juno1023/u280_sim_v422_f6a_20261008/}"
    case "$REL" in smi/*) ;; *) continue ;; esac
    [ -f "$DST/$REL" ] || { echo "[RECON][FAIL] 缺 $REL"; FAIL=1; continue; }
    ACT=$(sha256sum "$DST/$REL" | cut -d' ' -f1)
    if [ "$ACT" = "$EXP" ]; then
        echo "[RECON] OK   $REL"
    else
        echo "[RECON][FAIL] $REL"; echo "  expect=$EXP"; echo "  actual=$ACT"; FAIL=1
    fi
done < "$FP"
[ "$FAIL" = 0 ] && echo "[RECON] ALL-OK 阶段一重建树：$DST" || { echo "[RECON][FAIL] 指纹不符"; exit 1; }
