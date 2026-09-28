#!/bin/bash
# ADR-056 §B.3 迁移零字节审计（白名单制 → L1/L2 受限结构准入）。
#
# 契约（ADR-056 v4 §B.3 原文）：「迁移前后产物必须零字节一致……不一致 = RED，定位
# 修复，不放宽。」对照面 = 冻结基线 verification_v2/artifacts/e2e_add_20260926/ 的
# FROZEN_BASELINE_SHA256SUMS.txt 清单锁定的 60 文件（3 规模 × 20：4 json + 16 IQ hex）。
# 冻结基线**原样不动**（§F.4；禁止「重跑一遍把基线刷绿」）；本工具重跑 add e2e 三规模、
# 捕获同构文件集、与基线做**双向** byte-diff：
#   DIFF            两侧同路径存在但字节不同
#   MISSING-CURRENT 清单有、当前产物缺
#   EXTRA-CURRENT   当前产物有、清单外（文件集扩张）
# 任一发现 → RED，exit 1（不放宽）；全空 → GREEN，exit 0。
# 形态沿用 mul_zero_byte_audit.sh 的「重生成 → 双向 byte-diff 判决」；e2e 驱动与归档
# 形态沿用 run_add_e2e_scales.sh（n_rows 顺序 8 60 4、同 main_verify 调用、同 cp 集）。
set -u
cd "$(dirname "$0")/../.."   # repo root
PY=.venv/bin/python
BASE=verification_v2/artifacts/e2e_add_20260926
MANIFEST="$BASE/FROZEN_BASELINE_SHA256SUMS.txt"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT=verification_v2/artifacts/add_migration_audit_${STAMP}
mkdir -p "$OUT"
LOG="$OUT/audit.log"
exec > >(tee "$LOG") 2>&1

if [ ! -f "$MANIFEST" ]; then
  echo "RED: 冻结基线清单缺失: $MANIFEST"
  exit 1
fi
# 基线自身完整性先验（防基线被意外改动后误判迁移有差）
( cd "$BASE" && sha256sum -c FROZEN_BASELINE_SHA256SUMS.txt ) > "$OUT/baseline_selfcheck.log" 2>&1 \
  || { echo "RED: 冻结基线 sha256 自检失败（基线已变？详见 baseline_selfcheck.log）——停止，不比对"; exit 1; }
echo "基线 sha256 自检 OK（60/60）"

echo "== 阶段 1/2：重跑 add e2e 三规模并捕获产物（→ $OUT）=="
for spec in 8 60 4; do
  tag="n${spec}"
  echo "----- ${tag}: n_rows=${spec} -----"
  if $PY verification_v2/main_verify.py --op add --mode e2e --n-rows ${spec} \
      > "${OUT}/${tag}_main_verify.log" 2>&1; then rc=0; else rc=$?; fi
  echo "e2e exit=${rc}" | tee -a "${OUT}/${tag}_main_verify.log"
  if [ ${rc} -ne 0 ]; then
    echo "RED: ${tag} e2e 失败（exit=${rc}）——准入链在运行期即红；保留日志，停止，不放宽"
    exit 1
  fi
  B=build/verification_v2/add
  mkdir -p "${OUT}/${tag}/smc_0"
  cp "$B/physical_instructions.json" "$B/compiler_schedule.json" "$B/branch_report.json" \
     "$B/expected_values.json" "${OUT}/${tag}/"
  cp $B/smc_0/iq_*.hex "${OUT}/${tag}/smc_0/"
  echo "  捕获 4 json + $(ls "${OUT}/${tag}/smc_0/" | wc -l) hex"
done

echo "== 阶段 2/2：与冻结基线双向 byte-diff =="
findings=0
# 方向 1：清单 60 行 → DIFF / MISSING-CURRENT
while read -r _sha rel; do
  [ -z "${rel:-}" ] && continue
  if [ ! -f "$OUT/$rel" ]; then
    echo "MISSING-CURRENT  $rel"; findings=$((findings+1))
  elif ! cmp -s "$BASE/$rel" "$OUT/$rel"; then
    echo "DIFF             $rel"; findings=$((findings+1))
  fi
done < <(awk '{print $2}' "$MANIFEST")
# 方向 2：当前产物 → EXTRA-CURRENT（清单外文件）
manifest_list=$(mktemp); current_list=$(mktemp)
awk '{print $2}' "$MANIFEST" | sort > "$manifest_list"
( cd "$OUT" && find n4 n8 n60 -type f | sort ) > "$current_list"
while read -r rel; do
  echo "EXTRA-CURRENT    $rel"; findings=$((findings+1))
done < <(comm -13 "$manifest_list" "$current_list")
rm -f "$manifest_list" "$current_list"

echo "== 判决 =="
if [ "${findings}" -eq 0 ]; then
  echo "GREEN: add 三规模迁移产物与冻结基线 60/60 零字节一致（ADR-056 §B.3）"
  echo "GREEN" > "$OUT/VERDICT.txt"
  exit 0
else
  echo "RED: ${findings} 处不一致——定位修复，不放宽；冻结基线未动"
  echo "RED (${findings} findings)" > "$OUT/VERDICT.txt"
  exit 1
fi
