# 方案 C 时序 diff PoC

编译器**期望时间线** vs VCS **实测波形** 的逐事件比对，用于验证 AIACC
时序确定性架构的"时序排布"这一核心交付物（不只验终值数值）。

> 背景与裁定见仓库 `docs/hardware_timing_model_DRAFT.md`（待 ADR-038）+
> memory `timing-verify-strategy-plan-c` / `aiacc-hardware-timing-model`。

## 现状（2026-06-26，PoC）

`unit_fpadd`（纯 compute，无 cross-SMC）跑通：bank2（FPADD 输出）128 检查点
（8 SMC × 16 addr）**值 + 时序全绿**；负测注入值/时序故障均精确抓到。
**SMC 串行偏移实测 `[0..7]` = 时序模型预测**，首次 VCS 波形级证据。

边界：当前验**相对时序结构**（SMC +k、addr +i·II）+ 值；anchor = SMC_0 首写
cycle 为实测量，**绝对 issue→write 流水延迟被吸收、未验**。

## 组件

| 文件 | 职责 |
|---|---|
| `vcd_parser.py` | 通用 VCD 流式解析（信号表 + 选定信号值变化流），与具体信号无关 |
| `extract_ur_writes.py` | 从 VCD 抽 UR bank 写事件（**按 clk posedge 采样**：VCD 只记变化，wea 常驻须按沿采） |
| `timing_diff.py` | compiler 期望（`vcs_expected/` + `compiler_schedule.json` II）vs VCS 实测，找第一处发散 |

## 跑法

### 1) 产 VCD（免 Verdi，VPD→vpd2vcd）

⚠ **依赖未版本化的 RTL 侧改动**（`/home/yq/1_code/aiacc_rtl/`，非 git repo）：
- `run_sim_aiacc_top_f.sh` 已加 `WAVE_VCD=1` 模式（`-debug_access+all`，不挂 novas FSDB PLI）
- `wave_vcd_aiacc_top_f.do`：UCLI `dump -type VPD`（注释须 Tcl `#`）

```bash
cd /home/yq/1_code/aiacc_rtl
source /home/yq/projects/luban/flow/vcs-env.sh
WAVE_VCD=1 ./run_sim_aiacc_top_f.sh verification_v2 verification_v2/unit_fpadd/smc_0/
# 产 sim_output_aiacc_top_f/tb_aiacc_top_f.vpd
vpd2vcd sim_output_aiacc_top_f/tb_aiacc_top_f.vpd sim_output_aiacc_top_f/tb_aiacc_top_f.vcd
```

### 2) diff

```bash
cd verification_v2/tools/timing_poc
python3 timing_diff.py \
  --build /home/yq/1_code/aiacc_compiler/build/verification_v2/unit_fpadd \
  --vcd /home/yq/1_code/aiacc_rtl/sim_output_aiacc_top_f/tb_aiacc_top_f.vcd \
  --bank 2
# exit 0 全绿 / 1 有发散 / 2 数据缺失
```

调试单信号：`python3 vcd_parser.py <vcd> '<正则>'` 列匹配信号 + VCD code。
