#!/bin/bash
# ADR-055 A1 VCS 三规模驱动：产物已并须先跑 run_add_e2e_scales.sh。
# 每规模：VCS → 原生 COMPARE → 三区比对（three_zone_compare.py）→ 逐拍 trace 裁决
# （check_iq_probe_trace.py）。任何一步失败即停，保留全部日志。
# 用法: bash verification_v2/tools/run_add_vcs_scales.sh [--rebuild]
set -u
cd "$(dirname "$0")/../.."   # repo root
PY=.venv/bin/python
SIM=~/u280_sim_v422
RUNNER="$SIM/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh"
CASES="$SIM/test_atomic_ops/multi_inst_stage"
OUT=verification_v2/artifacts/e2e_add_20260926
REBUILD_ON_FIRST=0
[ "${1:-}" = "--rebuild" ] && REBUILD_ON_FIRST=1

fail=0
first=1
for spec in 8 60 4; do
  niter=$((spec / 4))
  tag="n${spec}"
  case_rel="multi_inst_stage/compiler_add_${tag}/smc_0"
  case_dir="$CASES/compiler_add_${tag}"
  echo "===== ${tag}: VCS ${case_rel} ====="
  # 脚本默认复用已编译 simv——TB 探针首次注入必须强制重建；之后规模复用
  rebuild_arg=""
  if [ ${REBUILD_ON_FIRST} -eq 1 ] && [ ${first} -eq 1 ]; then rebuild_arg="--rebuild"; fi
  first=0

  vcs_rc=0
  ( cd "$SIM" && ./sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh ${rebuild_arg} "$case_rel" ) \
      > "${OUT}/${tag}_vcs_run.log" 2>&1 || vcs_rc=$?
  echo "  vcs exit=${vcs_rc}"
  # 原生 COMPARE 判定：跑脚本自身跑 compare；同时抓 sim.log 里的 COMPARE 行
  cmp_log="$SIM/arith_regression/test_log/multi_stage/multi_inst_stage/compiler_add_${tag}/compare.log"
  if [ ! -f "${OUT}/${tag}_compare_native.log" ]; then :; fi
  [ -f "$cmp_log" ] && cp "$cmp_log" "${OUT}/${tag}_compare_native.log"
  grep -aE "COMPARE|PASS|FAIL" "$SIM/arith_regression/test_log/multi_stage/multi_inst_stage/compiler_add_${tag}/sim.log" \
      > "${OUT}/${tag}_simlog_grep.txt" 2>/dev/null || true
  [ ${vcs_rc} -ne 0 ] && { echo "  !! ${tag} VCS 失败——停"; exit 1; }

  tz_rc=0
  $PY verification_v2/tools/three_zone_compare.py "${case_dir}/smc_0" --label ${tag} \
      --instructions "${OUT}/${tag}/physical_instructions.json" --write-queue vout0 \
      > "${OUT}/${tag}_three_zone.json" 2>&1 || tz_rc=$?
  echo "  three_zone exit=${tz_rc}"

  tr_rc=0
  $PY verification_v2/tools/check_iq_probe_trace.py \
      --trace "${case_dir}/iq_probe_trace.log" \
      --build "${OUT}/${tag}" \
      --hex-dir "${case_dir}/smi_iq_inst/stage_0" \
      --num-iterations ${niter} \
      --queues "ur-r3:19,ur-r0:16" \
      > "${OUT}/${tag}_trace_report.json" 2>&1 || tr_rc=$?
  echo "  trace_check exit=${tr_rc}"

  if [ ${tz_rc} -ne 0 ] || [ ${tr_rc} -ne 0 ]; then
    echo "  !! ${tag} 存在失败面——保留产物日志，停止"
    exit 1
  fi
done
echo "VCS 三规模完成 → ${OUT}"
