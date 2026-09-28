#!/bin/bash
# ADR-055 A1 三规模 e2e 驱动 + 归档（不落 /tmp）。
# 用法: bash verification_v2/tools/run_add_e2e_scales.sh
# 仅 e2e + 独立检查 + 归档；VCS 打包/运行/三区比对/逐拍 trace 为后续单独步骤（需 handshake）。
set -u
cd "$(dirname "$0")/../.."   # repo root
PY=.venv/bin/python
STAMP=20260926
OUT=verification_v2/artifacts/e2e_add_${STAMP}
mkdir -p "$OUT"
for spec in 8 60 4; do
  niter=$((spec / 4))        # add fp32 vec4 → num_iterations = n_rows/4
  tag="n${spec}"
  echo "===== ${tag}: n_rows=${spec} num_iterations=${niter} ====="
  if $PY verification_v2/main_verify.py --op add --mode e2e --n-rows ${spec} \
      > "${OUT}/${tag}_main_verify.log" 2>&1; then rc=0; else rc=$?; fi
  echo "e2e exit=${rc}" >> "${OUT}/${tag}_main_verify.log"
  echo "  e2e exit=${rc}"
  if [ ${rc} -ne 0 ]; then
    echo "  !! ${tag} e2e 失败——保留日志，停止（不放宽守卫，不继续打包）"
    exit ${rc}
  fi
  B=build/verification_v2/add
  mkdir -p "${OUT}/${tag}/smc_0"
  cp "$B/physical_instructions.json" "$B/compiler_schedule.json" "$B/branch_report.json" \
     "$B/expected_values.json" "${OUT}/${tag}/"
  cp $B/smc_0/iq_*.hex "${OUT}/${tag}/smc_0/"
  if $PY verification_v2/tools/parse_e2e_scale.py "$B" --num-iterations ${niter} --label ${tag} \
      > "${OUT}/${tag}_parse.txt" 2>&1; then prc=0; else prc=$?; fi
  echo "parse exit=${prc}" >> "${OUT}/${tag}_parse.txt"
  if $PY verification_v2/tools/emit_expected_fire_sequence.py --build "$B" --num-iterations ${niter} \
      > "${OUT}/${tag}_independent_check.log" 2>&1; then crc=0; else crc=$?; fi
  echo "checker exit=${crc}" >> "${OUT}/${tag}_independent_check.log"
  echo "  parse exit=${prc}  checker exit=${crc}"
  # 顺手打包 VCS case（packager 只拷不编，~秒级；VCS 运行是后续独立步骤）
  rc_pkg=0
  $PY verification_v2/tools/package_vcs_case.py --op add --case compiler_add_${tag} \
      >> "${OUT}/${tag}_package.log" 2>&1 || rc_pkg=$?
  echo "package exit=${rc_pkg}" >> "${OUT}/${tag}_package.log"
  echo "  package exit=${rc_pkg}  case=compiler_add_${tag}"
  if [ ${prc} -ne 0 ] || [ ${crc} -ne 0 ] || [ ${rc_pkg} -ne 0 ]; then
    echo "  !! ${tag} 存在失败面——保留产物日志，停止"
    exit 1
  fi
done
echo "归档完成 → ${OUT}"
