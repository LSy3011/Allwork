#!/bin/bash
# m3lite_gate1_run.sh —— ADR-058 门①：S1 生成 → VCS → 比对（单用例全流程）
# 用法：bash verification_v2/tools/m3lite_gate1_run.sh compiler_stb_solo_s1
# 纪律：串行（VCS 同 op 并发互毁）；不 set -e——每步退出码留痕，由人判读。
set -uo pipefail
cd /home/juno1023/aiacc_compiler
source vcs-env.sh
PY=.venv/bin/python
SBX=/home/juno1023/u280_sim_v422
CASE="${1:?usage: m3lite_gate1_run.sh <case_name>}"
shift
GEN_ARGS=("$@")

echo "=== [1/3] 生成 case（含全局 memory_init 预载） ==="
$PY verification_v2/tools/stb_solo_case_gen.py --name "$CASE" "${GEN_ARGS[@]}"
echo "gen exit=$?"

echo "=== [2/3] VCS ==="
bash "$SBX/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh" \
    "multi_inst_stage/$CASE/smc_0" -s 0
echo "vcs exit=$?"

echo "=== [3/3] HBM 窗口六项断言比对 ==="
$PY verification_v2/tools/hbm_window_compare.py \
    "$SBX/test_atomic_ops/multi_inst_stage/$CASE"
echo "compare exit=$?"
