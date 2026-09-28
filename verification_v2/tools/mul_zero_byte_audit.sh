#!/bin/bash
# ADR-055 D0 附加门槛：mul 零字节 diff 审计。
# 用 A1 落码后的编译器**重新生成** mul e2e 产物，与 M1 的不可变 sandbox 基线
# （~/u280_sim_v422/.../compiler_mul/smi_iq_inst/stage_0/iq_*.hex，2026-09-12 VCS PASSED 的那次）
# 逐文件 byte-diff。判决：
#   * diff 为空 → 证明 A1 代码对 mul 产物零影响（白名单门控的产品级证实 + 字节级证实）。
#   * 任何 diff → RED，打印差异文件清单并 exit 1（不得当既有失败忽略）。
# ⚠ 若 mul 重生成失败（编译器侧），同样 exit 1 保留日志。
set -u
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
OUT=verification_v2/artifacts/e2e_add_20260926
BASE=~/u280_sim_v422/test_atomic_ops/multi_inst_stage/compiler_mul/smi_iq_inst/stage_0
LOG="${OUT}/mul_zero_byte_audit.log"
{
  echo "=== mul e2e 重生成（A1 代码下）$(date) ==="
  rc=0
  $PY verification_v2/main_verify.py --op mul --mode e2e || rc=$?
  echo "mul e2e exit=${rc}"
  [ ${rc} -ne 0 ] && { echo "RED: mul e2e 失败"; exit 1; }

  echo "=== byte-diff vs M1 基线 ${BASE} ==="
  n_diff=0
  shopt -s nullglob
  for f in "${BASE}"/iq_*.hex; do
    name="$(basename "$f")"
    cur="build/verification_v2/mul/smc_0/iq_*.hex"
    curf="build/verification_v2/mul/${name}"
    # 产物落盘可能在 smc_0/ 下；先找真实路径
    if [ ! -f "${curf}" ]; then
      curf="build/verification_v2/mul/smc_0/${name}"
    fi
    if [ ! -f "${curf}" ]; then
      echo "MISSING-CURRENT: ${name}（基线有、现行产物无）"
      n_diff=$((n_diff+1)); continue
    fi
    if ! cmp -s "$f" "${curf}"; then
      echo "DIFF: ${name}"
      cmp -l "$f" "${curf}" | head -5
      n_diff=$((n_diff+1))
    fi
  done
  for f in build/verification_v2/mul/iq_*.hex build/verification_v2/mul/smc_0/iq_*.hex; do
    name="$(basename "$f")"
    [ -f "${BASE}/${name}" ] || { echo "EXTRA-CURRENT: ${name}（现行产物有、基线无）"; n_diff=$((n_diff+1)); }
  done
  if [ ${n_diff} -eq 0 ]; then
    echo "GREEN: mul 产物零字节 diff（$(ls "${BASE}"/iq_*.hex | wc -l) 文件逐一 cmp -s）"
  else
    echo "RED: ${n_diff} 处差异"; exit 1
  fi
} 2>&1 | tee "${LOG}"
exit ${PIPESTATUS[0]}
