---
adr_id: ADR-048
title: compute 流水 latency 模型对齐 RTL —— FP16MUL 4→5（DR 输出寄存器级）+ FP32ADDTREE8TO1 3→4（回退 ADR-031 误编辑）；三文件锁步
decided: 2026-07-02
last_verified: 2026-07-02
still_holds: yes（2026-07-02 落码 + VCS bank5 全 4-lane bit-match + fp32 路 fma/matmul_parallel_n 8/8 无回归 + pytest 183 pass）
builds_on: ADR-032, ADR-047
refines: ADR-032 D1（FP16MUL SMT 边权口径「核 4」→「DR 可见 5」）
corrects: ADR-031（FP32ADDTREE8TO1 latency 4→3 是误编辑，两树物理同构、8to1 本该 4）
hco_review: hco 定 + 放行（2026-07-02，RTL 行级取证 fp16mul.v:288/405 + fp32_adder_tree_8_inputs_pipeline_8to1.v:405/495/605/664 + 对称 5to1；BLOCK-1 三文件锁步）
user_decision: user 2026-07-02 指出「硬件 latency 对齐」方向；FP16MUL 口径 4→5 已知会（RTL+VCS 铁证、契合「时序要算不要调」）
---

# ADR-048 · compute 流水 latency 对齐 RTL

> **一句话**：本会话 `fp16_matmul_q_dst` 的一连串 compute 相位问题（激活采样 / load / routing / **addtree 执行序**）共同根 =
> **编译器 latency 模型 ≠ RTL 真值** → SMT 排的顺序/拍在 VCS 塌乱。VCS+RTL 双证两处偏差：**FP16MUL 真=5（模型 4，漏
> DR 输出寄存器级）**、**FP32ADDTREE8TO1 真=4（模型 3，ADR-031 误编辑）**。按「时序要算不要调」对齐 latency = 修根、非
> 逐个 whack-a-mole 相位。

## Context（RTL 行级 + VCS 双证，hco 定）

- **FP16MUL = 5**：`fp16mul.v:288` `compute_valid_r` 5-bit 移位链，`dr_fp16mul_d*` 在 `compute_valid_r[3]` 捕获（:405）→
  trigger T → DR 可读 T+5。乘法器核 4 级（`fp16tofp32_multiplier.v`）+ **DR 输出寄存器 1 级** = 5。VCS 实测
  cru_fp16mul@10308→dr_fp16mul_d0@10313 = 5 吻合。
- **FP32ADDTREE8TO1 = 4**：`fp32_adder_tree_8_inputs_pipeline_8to1.v` 4 寄存器级（Stage1:405 / Stage2:495 / Stage3:605 /
  输出 o_dr:664，Stage0 已改组合）。与 `fp32_adder_tree_final_pipeline_5to1.v`（5to1）**物理同构 = 同 4 级**。模型
  `FP32ADDTREE5TO1=4`（对）而 `8TO1=3`（错）——ADR-031 §「8to1 4→3 顺带修」是误降 1。VCS 实测输入@10316→输出@10320 = 4 吻合。
- **+1 非全单元共有**（否定「统一输出 reg 级」猜想，hco）：FPMUL `fpmul.v:193` DR 组合旁路（无额外级）=4 不变；FPADD
  `o_sum` 已是寄存器化第 4 级 =4；SHIFTUP `shift_up.v:53` 单输出寄存器 =1。**只改 FP16MUL / FP32ADDTREE8TO1 两处**（VCS 已取证）。
- **无双算**：RTOVR 独立节点自带 latency=1；`SMI_DS_T_RESIDENT` 仅管 DST streaming 通路，与 compute 边无关。改真值不与任何补偿双算。
- **latency 语义 = trigger→DR 输出寄存器就绪**（RAW 边权，`dependency_axioms.py:106` lat_p = spec.delays[producer]）。少算输出 reg →
  consumer 早排 1 拍读 stale。这正是 addtree 列 0,3=0 的根（SMT 按 lat=3 排 y_0..3 spacing 3，真 DR 写在 +4 → reader 早读末 lane stale + 共享 s0/s1 端口 mis-spacing 争用 → 执行序呈 1,2,3,0）。

## Decision（BLOCK-1：三文件锁步，缺一即 SMT/sim 去同步）

同一 latency 落三文件喂两条独立路（SMT 调度 / Python sim 流水深度）：

| 单元 | pipeline_delays.yaml | specs/units/*.yaml | modules.yaml | 值 |
|---|---|---|---|---|
| FP16MUL | :8 | fp16mul.yaml:2 | :47(latency) | 4 → **5** |
| FP32ADDTREE8TO1 | :10 | fp32addtree8to1.yaml:2 | :245 | 3 → **4** |

`FP32ADDTREE5TO1` 保持 4 不动（已正确）。⚠ **只改 pipeline_delays 不改 specs/units = 最大陷阱**：SMT 按新值排+emit
abs_t、Python sim 按旧值重放 → sim/VCS 拍不一致（hw-parity 假绿/假红）。两路必须锁步。

## addtree lane 结构（hco 定，勿提前改结构）

`fp32_adder_tree_8_inputs_pipeline_8to1.v:665-671` 输出寄存器 `case(dest_reg_idx_s3)` **只写选中 32-bit lane、其余 HOLD**
= 按 dst_id 分 lane 累积、非整体覆盖 → **4 树共享单输出寄存器 lane-safe**、不需去链化/硬件 ready 锚定。列 0,3=0 真因 =
latency 3 少算（上）。**先落 latency 修 + 重跑 VCS；若 lane 仍掉，残余是 reader 调度/emit abs_t 排序（scheduling 层，非
addtree 结构）——不提前动结构。**

## Consequences / 验收

- **受影响面** = FP16 matmul 通路（FP16MUL + FP32ADDTREE8TO1，ADR-032）。`fma_parallel_n`/`matmul_parallel_n` 是 FP32 路
  （FPMUL/FPADD/FP32ADDTREE5TO1 均未改）→ 功能不受影响，但 sim 确定性重放**须复跑确认 II/结果不变**。
- **回归复跑**（BLOCK-1 落地后必跑）：`--op matmul --mode all`（fp16 q_dst 目标）+ `--op fma_parallel_n --mode e2e` +
  `--op matmul_parallel_n` + `pytest tests/`（12 smt-unit 不应引入新 UNSAT）。
- **可证伪单测**：FP16MUL 产 D、consumer t+4 读应 UNSAT / t+5 读应 SAT（钉边权=5）；同构 addtree8to1 边权=4。
- **验收（升 Accepted）**：VCS `fp16_matmul_q_dst` bank5 **全 4-lane 非零 + 逐词 bit-match golden_data.npz 的 y** + fp32 路无回归。
- refine ADR-032 D1：FP16MUL 的 SMT 边权口径从「核 4」→「DR 可见 5」（核 latency=4 的物理事实不变，是口径差）。
- corrects ADR-031：FP32ADDTREE8TO1 4→3 是误编辑，本 ADR 回退为 4。

## 关联
[[adr-045-qdst-delivery-works-optb]]（全过程 + latency 实测）、ADR-047（load 微指令根因、不同关注面）、ADR-032（FP16MUL
口径 refine）、ADR-031（8to1 latency 订正）、[[derive-timing-not-vcs-calibrate]]（latency 全固定→对齐真值即推导正确）。
