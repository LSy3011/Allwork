---
adr_id: ADR-046
title: fp16_matmul_q_dst Opt-B —— compute 采样相位靠 emit abs_t 锚定（垫 derived lead-NOP）落 held 交付窗，RESIDENT 校正到全交付管线且降级为 SMT 排序职责
decided: 2026-07-01
last_verified: 2026-07-01
still_holds: proposed（hco 定机制 + RTL 行级取证；待落码 + VCS 出 [a0..a7] 非零 bank5）
builds_on: ADR-045, ADR-043, ADR-044
implements: ADR-045 Opt-B（DEBT-042-EMIT-SMT-LAYER 的 q_dst compute 采样相位闭合）
hco_review: 机制由 hco 定（2026-07-01，RTL 行级取证 smi_ds_t.v:58/134/167/179/311-317 + shift_up.v:52-58 + smi_top.v:370/384/665 + smi_iq_dly_queen.v:550/225/245/111）
user_decision: Opt-B（emit 回灌 global_t，2026-07-01 AskUserQuestion）
---

# ADR-046 · q_dst Opt-B：emit abs_t 锚定 compute 落 held 交付窗

> **一句话**：ADR-045 移植 vec128 交付范式后，`fp16_matmul_q_dst` 的激活 **[a0..a7] 已 VCS 证实正确交付**并在
> `dr_shiftup_d` held 无界（cyc11955）。bank5 仍 0 仅因 **compute 恰在同边沿采样（same-edge race）→ 激活采成 0**。
> 修 = **emit 层给 compute IQ 垫 derived lead-NOP**，使 compute body 执行拍 ≥ 交付完成拍 + 2（anti-race），全部由
> **固定 latency 推导**（[[derive-timing-not-vcs-calibrate]]）；RESIDENT 同步校正到全交付管线值，但职责**降级为仅保 SMT
> 排序/verifier 一致**，VCS 拍对齐由 emit 锚定承担。

## Context（VCS 实证 + hco RTL 取证）

- **交付正确**（ADR-045 落地）：`ds_t_data_out` beat15 = [a0..a7]（word1[0]=a[7-smc] 转置）；`data_out_vld` 窗
  cyc11933-11949（16 beat，T0=0/beat15=真数据）；`dr_shiftup_d` @cyc11955 = [a0..a7]，**held 无界**（单 token 无下轮
  session，`smi_ds_t.v` data_out 仅 rst 复位）。
- **唯一 bug 面**：`dr_shiftup_reg` 每广播拍覆盖、末 beat held（shift_up.v:52-58）；compute RTOVR latch（rt_act →
  `dvr_fp16mul_s4567`）@cyc11955 同 posedge 采样 → 取旧值 0 → 乘零 → bank5=0。单发 compute 错过不重试。
- **hco 定的两个独立失败模式**：
  1. **RESIDENT=35 漏算整条 IQ14 EXTRA_DATA 交付管线**（≈5-6 级，非"漏 1 寄存器"）。
  2. **跨-II abs_t→VCS 拍映射非线性**：RESIDENT 是 SMT 逻辑 t 边权，compute VCS 拍经其 II=19 kernel 映射、
     dr_shiftup=beat15 锚在 DST 引擎实拍（II=2 + data-gated）→ 两端 t→cycle 比例不同 → SMT 的 `t(compute)=t(ds_t)+42`
     **不保证** `cycle(compute) ≥ cycle(dr_shiftup)+2`。**故只改 RESIDENT 不足以修，lever 必在 emit abs_t 锚定。**

## 关键 RTL 事实（hco 取证）

- **SHIFTUP `base_abs=0`（无视 global_t）对 EXTRA_DATA IQ 合法、非 DEBT-042 症状**：`smi_iq_dly_queen.v:550`
  EXTRA_DATA 分支 `inst_rdy` 仅在 `rdi_start & extra_data_exist_data` 拉高 → IQ14 在 DATAQ 有 DST 数据前**不发射广播**、
  硬件自门控。abs_t=0-15 只定"IQ 何时 ready pop"、不定广播拍（广播 VALUE 拍由 data_out 出数节奏定）。**故 SHIFTUP
  emit 不改**（DEBT-042 原则只针对**非-EXTRA_DATA** stream 如 UR_W0，那个靠 `stream_lead_delay` 补偿）。
- **完整固定交付链**（PARAM_SMC_NUM 无关，仅随 TILE/WINDOW 缩放；hco 行级枚举）：

  | 段 | 拍 | RTL |
  |---|---|---|
  | ds_transpose_anchor → data_out beat0（drain） | 3 | smi_ds_t.v:167 out_phase → :134 out_vld_d → :179 data_out（三级寄存器）|
  | data_out beat0 → beat15（WINDOW−1，1 beat/cyc） | 15 | smi_ds_t.v:58 LAST_BEAT=15，连续 16 拍 |
  | **data_out beat15 → dr_shiftup_reg（`SMI_DS_T_DELIVER`≈6，RESIDENT 漏的）** | **≈6** | ①smi_top.v:384 wrd→DATAQ +1；②smi_iq_dly_queen.v:225/245/111 FSM +2~3；③smi_top.v:665 o_cru_shiftup_r +1；④shift_up.v:54 dr_shiftup_reg +1 |
  | dr_shiftup_reg → compute 可采（RTOVR latch） | 1 | rtovr sel=29 latch |
  | anti-race margin（严格 ≥1，避同边沿） | 1（取 2 穿 latch） | shift_up.v:54 无握手 |

  ⟹ `ds_transpose_anchor → compute 可采 ≈ 3+15+6+1+1 = 42`。**残差**：dly_queen FSM 2 vs 3 级 = 唯一 ±1，须
  cycle-trace 敲死（DEBT-046-DELIVER-FSM），不得当自由旋钮。

## Decision（Opt-B 机制，hco 定，两件事缺一不可）

### D1. 新增受控常量 `SMI_DS_T_DELIVER`（`pipeline_delays.yaml`）
= IQ14 EXTRA_DATA 结构管线 ≈6（DATAQ+dly_queen FSM+o_cru_shiftup_r+shift_up），**RTL-derived 非 VCS 标**。注释带上表
行级取证 + DEBT-046-DELIVER-FSM（FSM 2/3 级残差）。

### D2. RESIDENT 校正到全交付管线 + 职责降级
`SMI_DS_T_RESIDENT = SMI_DS_T(19) + (SMI_DS_T_WINDOW−1) + SMI_DS_T_DELIVER ≈ 19+15+6 = 40`（was 35，漏 §DELIVER）。
**职责切分**：RESIDENT 只保 **SMT 逻辑排序 + verifier 一致**（`dependency_axioms.py:59-64` 边 + `ucode_solver.py:245-246`
维度4/6 必同取，否则 ADR-006/021 类 desync），**不再承担 VCS 拍对齐**——那由 D3 emit 锚定承担。全符号式。

### D3. emit abs_t 锚定：compute IQ 垫 derived lead-NOP〔核心新机制〕
compute body abs_t 必须 `≥ dr_shiftup_beat15_abs + 2`，其中
`dr_shiftup_beat15_abs = CRI_abs_t + 16(collect) + 3(drain) + (WINDOW−1) + SMI_DS_T_DELIVER`（`CRI_abs_t` 由 ADR-044
two_word 固定早发 = 1~2）。给 compute IQ（携 `dst_compute_resident` 消费者 + 其激活 RTOVR 路由 IQ）垫 lead-NOP
（`SMIHeader(vld=0, loops=lead)`），`lead = 目标 abs_t − compute 自然 body abs_t`，复用现成 lead-NOP 机器
（`assembler.py:113-120`）。**全部由固定 latency + emit 结构算出** → 换 K/PARAM_SMC_NUM 时 WINDOW 随元素/B 缩放、
DELIVER 固定、CRI_abs_t 固定 → 自动重算、无魔法常量。`+2` anti-race 必须显式 derived。
- held 窗**无界**（hco）⟹ 锚定只需**下界**、overshoot 安全 → derived 值 + 小安全 margin 即 robust。

## 隔离 / 不回归

- lead-NOP 锚定**仅命中带 `dst_compute_resident` 的 compute 路径**（q_dst）；unit/fma/matmul_n/vec128 无此 marker、不锚。
- RESIDENT 40 仍只在 `dst_compute_resident` 门控内取（ADR-043 D1'）；fp32 q_dst 下游 ADDTREE5TO1 无 marker、留 19。
- vec128（bank-sink、无 compute）不经 compute-resident 边、不锚。

## Options considered

- **Opt-B（选）= D1+D2+D3**：emit abs_t 锚定 + RESIDENT 校正。原则正确（推导式）、闭合 DEBT-042 的 emit-SMT 断裂。
- **Opt-B'（备，hco 提，更大改）**：把 compute 收进 DST region（同 II=2 `dst_explicit_emit` 显式 abs_t 流），与 shiftup
  共用 t→abs_t 映射。margin 天然在 VCS 存活，但 compute 脱离 compute-II kernel、改动更大。仅当 D3 lead-NOP 路径失效再上。
- **Opt-A（拒，ADR-045 已议）= 单 bump RESIDENT**：VCS-side 标定、违 derive 原则、跨-II 非线性会吞 margin（hco 证不足）。

## 待落码 / 验收

- **测试**（hco 建议）：① SMT 对抗单测：`dst_compute_resident` 最小图，断言 compute body abs_t ≥ `CRI_abs_t+collect+drain+
  (WINDOW-1)+DELIVER+2`；人为设 DELIVER=0 → 期望 assert fail（防假绿）。② emit 单测：改 K/PARAM_SMC_NUM，断言 lead-NOP
  loops 按公式变（验"换 K 自动对"）。
- **VCS 验收（升 Accepted）**：`dvr_fp16mul_s4567` 在交付-held 拍捕获非零真 [a0..a7] + bank5 逐词 **bit-match
  golden_data.npz 的 y**（=dot products [71.37,91.73,81.34,69.53]...，**非** model-A output_bank）；**改 ur_data/固件大小后
  相位不崩**（证相位是 emit-derived 不变量）。
- **DEBT-046-DELIVER-FSM**：dly_queen FSM 2 vs 3 级的 ±1 须 VCS cycle-trace 敲死，升 DEBT-043-RESIDENT-LAT-VCS 到 closed。

## 关联
[[adr-045-qdst-delivery-works-optb]]（交付已证 + 精确时序）、ADR-045（Opt-B 决策）、ADR-043（DEBT-042-EMIT-SMT-LAYER +
RESIDENT 由来）、ADR-044（two_word + CRI 早发）、[[derive-timing-not-vcs-calibrate]]（emit 落 global_t+固定 offset）。
