#!/usr/bin/env bash
# ADR-056 §D.1 契约壳：一条命令跑完整验证链（统一入口 run_v1_verify.py 的薄封装）。
#
#   用法: verification_v2/tools/run_v1_formula_flow.sh <op_yaml> --n-rows a,b,c [其余选项...]
#   例:   verification_v2/tools/run_v1_formula_flow.sh verification_v2/ops/add.yaml \
#            --n-rows 4,8,60 --domain-mode frozen_regression
#
# 退出码: 0=GREEN（全链通过并已发布）；1=RED/UNVERIFIED（不发布）；2=CONFIG_INVALID。
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "用法: $0 <op_yaml> --n-rows a,b,c [--domain-mode v1_admission|frozen_regression] [--no-run-vcs]" >&2
  exit 2
fi

OP_YAML="$1"; shift
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

PY="$REPO_ROOT/.venv/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"

exec "$PY" verification_v2/tools/run_v1_verify.py --op-yaml "$OP_YAML" "$@"
