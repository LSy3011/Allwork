#!/usr/bin/env bash
# run_f6b.sh — F6-B 子系统级 TB 运行器（唯一受支持入口）
#
#   usage: run_f6b.sh legacy|fixed_p1|fixed_p2
#
# DUT = F6B_ISO（默认 /home/juno1023/u280_sim_v422_f6a_20261008 隔离副本）
# 内整个 smi/ 壳（smi_top 全集成）。legacy 模式应指原树：
#   F6B_ISO=/home/juno1023/u280_sim_v422 ./run_f6b.sh legacy   （原树只读）
# 模式之分只在 DUT 文件内容（修复实施在同副本内就地推进）：
#   legacy：硬校验 5 个关键文件 sha256 必须等于冻结清单原值（防「在改过的
#           副本上误跑 legacy」）——隔离副本阶段一就地修改后本模式**按设计
#           拒绝**（exit 6）；指向未动原树时通过。判据 v2 下 legacy 矩阵
#           B3 翻转（判据演进非行为变化，F6 文档 §12.6）。
#   fixed_p1：阶段一验收态。判据 v2（2026-10-10）起本模式**哈希钉死**
#           阶段一五文件+F6 模块指纹（BUILD_FINGERPRINT 2026-10-10），
#           阶段二就地改动后按设计拒绝（exit 6）；阶段一证据唯一载体 =
#           2026-10-09/10 归档 + 判据 v2 复跑档。
#   fixed_p2：阶段二完整契约验收（矩阵全 PASS；E1/E2/E3/E4 + off-by-one
#           修复，F6 文档 §12）。
#
# 冻结期望矩阵（f6b_check.py 判据 v2，2026-10-10 首跑前冻结）：
#   legacy  : B1=F B2=T B3=F B4=F B5=F B6=F
#   fixed_p1: B1=F B2=T B3=F B4=F B5=F B6=F
#   fixed_p2: 全部 PASS
# （v1 矩阵与归档：B3 legacy/fixed_p1=PASS 系「该判据下未观察到反例」的
#  诚实记录；v2「及时检出」标准下静默吸收即违约。详见 F6 文档 §12.6。）
#
# 执行边界 5 —— 失败分档，退出码互斥：
#   0  AS-EXPECTED（观测=冻结矩阵）
#   1  裁决不符（checker 判 MISMATCH）
#   2  编译失败
#   3  许可证失败（编译或运行期）
#   4  仿真超时（suite=timeout）
#   5  缺日志 / 缺 suite 结束标记（防旧日志假绿）
#   6  provenance 违例（缺文件 / 清单缺项 / legacy/fixed_p1 哈希偏离其钉）
#   7  裁决器自身解析失败（checker rc=2）
# 每轮 rm -rf work_<mode> 后重建，禁止复用旧日志。
set -uo pipefail

MODE="${1:-}"
if [ "$MODE" != legacy ] && [ "$MODE" != fixed_p1 ] && [ "$MODE" != fixed_p2 ]; then
    echo "usage: run_f6b.sh legacy|fixed_p1|fixed_p2" >&2; exit 64
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO="${F6B_ISO:-/home/juno1023/u280_sim_v422_f6a_20261008}"
MANIFEST="$HERE/../artifacts/f6_rtl_freeze_20261008/RTL_FREEZE_MANIFEST.txt"
WORK="$HERE/work_$MODE"

KEY_FILES="smi/smi_iq_lsu_complete.v smi/smi_ctrl_regs.v smi/smi_top.v smi/smi_iq_inst_queen.v smi/smi_iq_dly_queen.v"

# 阶段一（2026-10-10 补丁态）指纹钉——fixed_p1 模式硬校验
# （来源：artifacts/f6_phase1_rtl_20261010/BUILD_FINGERPRINT.txt）
p1_hash() {
    case "$1" in
        smi/smi_iq_lsu_complete.v)    echo 40b4573d20659c8767cd7caef8decf67ee90016d2083452bd9077a073cea149c ;;
        smi/smi_ctrl_regs.v)          echo 8caa28ae25df4c8b00c23ae7ba78fe9b3d8b9b023940df4438e86026f158fd60 ;;
        smi/smi_top.v)                echo 0a035052b1e43af62d584971a5d3b583afa58071400bf6cebe8c8b787dfc4461 ;;
        smi/smi_iq_inst_queen.v)      echo 8552b946c753c0ae08b6090eace57bd7518bd8308a9af3c43bbb64f2bc878230 ;;
        smi/smi_iq_dly_queen.v)       echo 6064801b5612f96be030530c71838836e7f60e28f88f105082e9288a189658c3 ;;
        smi/smi_iq_lsu_complete_f6.v) echo a84d82d389771e1fc43effbb1dee4feb2467130e82e4fa47501d210442ede05b ;;
        *) echo "" ;;
    esac
}

echo "[F6B] mode=$MODE iso=$ISO work=$WORK"

# ---- provenance：哈希记录；legacy/fixed_p1 模式硬校验各自基线（退出码 6） ----
for f in $KEY_FILES; do
    [ -f "$ISO/$f" ] || { echo "[F6B][FAIL] 缺文件: $ISO/$f"; exit 6; }
    SHA=$(sha256sum "$ISO/$f" | cut -d' ' -f1)
    echo "[F6B] sha256($f)=$SHA"
    if [ "$MODE" = legacy ]; then
        EXP=$(awk -v p="$f" '$5==p {print $1}' "$MANIFEST")
        [ -n "$EXP" ] || { echo "[F6B][FAIL] 清单中无 $f"; exit 6; }
        [ "$SHA" = "$EXP" ] || {
            echo "[F6B][FAIL] legacy 模式但 $f 已偏离冻结基线"
            echo "[F6B][FAIL]   expect=$EXP"; echo "[F6B][FAIL]   actual=$SHA"
            exit 6; }
    elif [ "$MODE" = fixed_p1 ]; then
        EXP=$(p1_hash "$f")
        [ -n "$EXP" ] || { echo "[F6B][FAIL] fixed_p1 钉中无 $f"; exit 6; }
        [ "$SHA" = "$EXP" ] || {
            echo "[F6B][FAIL] fixed_p1 模式但 $f 已偏离阶段一指纹"
            echo "[F6B][FAIL]   expect=$EXP"; echo "[F6B][FAIL]   actual=$SHA"
            echo "[F6B][FAIL] 阶段一证据唯一载体=2026-10-09/10 归档"
            exit 6; }
    fi
done
echo "[F6B] sha256(tb)=$(sha256sum "$HERE/tb_f6b_smi_top.v" | cut -d' ' -f1)"
echo "[F6B] sha256(checker)=$(sha256sum "$HERE/f6b_check.py" | cut -d' ' -f1)"

# ---- 修复模块 provenance（fixed_p1 模式入钉硬校验；其余只打印）----
F6MOD="$ISO/smi/smi_iq_lsu_complete_f6.v"
if [ -f "$F6MOD" ]; then
    SHA=$(sha256sum "$F6MOD" | cut -d' ' -f1)
    echo "[F6B] sha256(smi/smi_iq_lsu_complete_f6.v)=$SHA"
    if [ "$MODE" = fixed_p1 ]; then
        EXP=$(p1_hash smi/smi_iq_lsu_complete_f6.v)
        [ "$SHA" = "$EXP" ] || {
            echo "[F6B][FAIL] fixed_p1 模式但 F6 模块已偏离阶段一指纹"
            echo "[F6B][FAIL]   expect=$EXP"; echo "[F6B][FAIL]   actual=$SHA"
            exit 6; }
    fi
fi

# ---- 全新工作目录（边界 5：禁止复用旧日志） ----
rm -rf "$WORK"
mkdir -p "$WORK"

source /home/juno1023/u280_sim_v422/env.sh

cd "$WORK"
# 判据 v3：B9 T-PROTOERR 的 force 目标（F6 模块 arm_err → proto_err_o
# 路径）仅存在于阶段二缺口修复后的 DUT；fixed_p2 编译加 define，
# legacy/fixed_p1 走 TB 内 skipped=1 分支（裁决矩阵 S）
DEFS=""
if [ "$MODE" = fixed_p2 ]; then
    DEFS="+define+F6B_HAS_F6PROTO"
    echo "[F6B] define=F6B_HAS_F6PROTO（B9 force 目标启用）"
fi
vcs -sverilog -full64 -q -timescale=1ns/1ps \
    $DEFS \
    -F "$ISO/smi/smi_rtl.f" \
    "$HERE/tb_f6b_smi_top.v" -o simv_f6b > compile.log 2>&1
rc=$?
if [ $rc -ne 0 ] || [ ! -x ./simv_f6b ]; then
    if grep -qi 'license\|licence\|FLEXlm\|LM_LICENSE' compile.log; then
        echo "[F6B][FAIL] 许可证失败，见 $WORK/compile.log"; exit 3
    fi
    echo "[F6B][FAIL] 编译失败(rc=$rc)，见 $WORK/compile.log"; exit 2
fi

./simv_f6b +vcs+lic+wait > sim.log 2>&1
simrc=$?
[ -s sim.log ] || { echo "[F6B][FAIL] 缺仿真日志"; exit 5; }
if grep -qi 'license.*\(error\|denied\|cannot\|failed\)\|cannot.*license' sim.log; then
    echo "[F6B][FAIL] 运行期许可证失败，见 $WORK/sim.log"; exit 3
fi

# provenance 入日志（追加段，裁决器只认 [F6B_FACT] 行不受影响）
{
    for f in $KEY_FILES; do
        echo "[F6B] sha256($f)=$(sha256sum "$ISO/$f" | cut -d' ' -f1)"
    done
    [ -f "$F6MOD" ] && echo "[F6B] sha256(smi/smi_iq_lsu_complete_f6.v)=$(sha256sum "$F6MOD" | cut -d' ' -f1)"
    echo "[F6B] sha256(tb)=$(sha256sum "$HERE/tb_f6b_smi_top.v" | cut -d' ' -f1)"
    echo "[F6B] sha256(checker)=$(sha256sum "$HERE/f6b_check.py" | cut -d' ' -f1)"
    echo "[F6B] simv_exit=$simrc"
} >> sim.log

if ! grep -q 'suite=done' sim.log; then
    if grep -q 'suite=timeout' sim.log; then
        echo "[F6B][FAIL] 仿真超时，见 $WORK/sim.log"; exit 4
    fi
    echo "[F6B][FAIL] 缺 suite 结束标记（simrc=$simrc），见 $WORK/sim.log"; exit 5
fi

python3 "$HERE/f6b_check.py" --mode "$MODE" "$WORK/sim.log" | tee check.txt
chk=${PIPESTATUS[0]}
case $chk in
    0) echo "[F6B] overall=AS-EXPECTED"; exit 0 ;;
    1) echo "[F6B][FAIL] 裁决不符，见 $WORK/check.txt"; exit 1 ;;
    *) echo "[F6B][FAIL] 裁决器解析失败(rc=$chk)"; exit 7 ;;
esac
