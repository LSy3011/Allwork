---
adr_id: ADR-047
title: fp16mul 操作数加载链缺失 —— 编译器从不发射 load_s0123/load_s4567 微指令导致 S-bank 恒 0（RTL 恒不出数，被 sim auto-latch 假绿掩盖）；补 load 微指令 + 硬件 COND/DLY 锚定 + 修 sim 契约
decided: 2026-07-02
last_verified: 2026-07-02
still_holds: yes（2026-07-02 VCS 8/8 SMC×4lane bit-match golden；配 ADR-048 latency 对齐）
builds_on: ADR-032（D2 FP16MUL micro_groups 定义）, ADR-045, ADR-046
supersedes: ADR-046 D3（abs_t lead-NOP 锚定——前提被证伪：fp16mul 不读 dvr 总线、abs_t 跨-II 不可比且不解释恒 0）
hco_review: 根因由 hco 定（2026-07-02，RTL 行级 fp16mul.v:94-106/108-221/237-278 + smi_top.v:653-655 + SMC_ALL.sv:352-359 + smi_iq_cru_widths.vh:41/44/47 + smi_iq_dly_queen.v:241/263/545-551）+ 主 session VCS 内部探针确认
user_decision: 落 ADR-047 + 只修 q_dst 打通 VCS（全 fp16mul 路系统性修登 follow-up 债务，2026-07-02 AskUserQuestion）
---

# ADR-047 · fp16mul 操作数加载链缺失（真根因，翻案 ADR-046 D3）

> **一句话**：`fp16_matmul_q_dst` VCS bank5=0 的**真根因**不是相位（ADR-046 D3 的 same-edge-race/abs_t 锚定被证伪），而是
> **编译器从不发射 `load_s0123`/`load_s4567` 微指令（IQ3/IQ4 写使能）** → fp16mul 内部操作数寄存器 `s*_bank*_r` 恒 0 →
> 乘法器读的 `selected_s*_r` 恒 0 → 乘积恒 0 → bank5=0。**与 trigger 时序无关**。且 sim 用「总线非零即自动锁存」假模型
> 掩盖了它 → 全 fp16mul compute 路（fma/matmul_n/q_dst）的 sim「8/8」是假绿、**从没真在 RTL 出过数**。

## Context（真根因，hco RTL 取证 + VCS 内部探针一锤定音）

fp16mul 乘法器**不直接读 dvr 总线**，数据通路三段两级寄存器（`fp16mul.v`）：
1. `dvr_fp16mul_s*_w`（RtoVR crossbar 输出，`SMC_ALL.sv:352-356`）—— 主 session VCS 观测的 `dvr_fp16mul_s4567=[a0..a7]`
   就是这层**上游总线**（红鲱鱼）。
2. `s*_bank*_r`（fp16mul 内部操作数寄存器，`fp16mul.v:108-221`）—— **仅当 `cru_fp16mul_s0123/s4567` 写使能拉高**
   才从 dvr 锁存（写使能 = load 微指令的 vld 位，`fp16mul.v:95/98/102/106`）。
3. `selected_s*_r`（组合，`fp16mul.v:237-278`）← `s*_bank*_r`，乘法器 `fp16_a/b`（`:317-355`）读它，**无从 dvr 直连旁路**。

`cru_fp16mul_s0123`(IQ3)/`cru_fp16mul_s4567`(IQ4) 与 trigger `cru_fp16mul`(IQ2) 是**并列独立 IQ 槽**
（`smi_iq_cru_widths.vh:41/44/47`；`smi_top.v:653-655` 三者从 `iq_inst[2/3/4]` 同 posedge 各自寄存）。

**VCS 内部寄存器探针铁证（主 session tb_qdst.vcd）**：`cru_fp16mul_s0123/s4567`（load 写使能）**恒 0**、
`s0/s4_bank0_r`（操作数寄存器）**恒 0**、`selected_s0/s4_r`（乘法器实读）**恒 0** → 乘积 `dr_fp16mul_d*` 恒 0。

**编译器从不发 load 微指令**：DFG（`matmul_op.py:1436-1443`）只发 `RTOVR→PS.FP16MUL_S*`（路由到 dvr 总线）+
`FP16MUL` trigger；`micro_variant` 机制（`payload_factory.py:60-66` / `packer.py:16/57` / modules.yaml FP16MUL
micro_groups compute/load_s0123/load_s4567，ADR-032 D2）**全代码库只读不设**。

## 系统性影响（latent bug + sim 假绿）

`sim/smc_simulator/units/fp16_mul_unit.py:55-78` 用「dvr 总线非零即自动锁存进 regs」模型（**不要求写使能**）→
sim 对 load 微指令缺失全盲 → **fma/matmul_n/q_dst 的 sim「8/8 bit-perfect」全是假绿**、fp16mul compute 从没真硅出数
（同 ADR-031 smc_id_en sim 全盲类）。q_dst 是第一个在 compute 路跑 VCS 才撞破。**user 决策：本 ADR 只修 q_dst 打通 VCS，
全 fp16mul 路系统性修登 DEBT-047-FP16MUL-LOAD-ALLPATHS。**

## Decision（hco 定，三件事）

### D1. q_dst DFG 显式发 `load_s0123` / `load_s4567` 微指令〔主修，必做〕
在 q_dst 的 FP16MUL compute 前加两条 FP16MUL-type op：`micro_variant="load_s0123"`（load S0-S3 操作数寄存器）+
`micro_variant="load_s4567"`（load S4-S7）。这两条发射 `cru_fp16mul_s0123/s4567` 写使能，把 RtoVR 路由到 dvr 总线的
值锁进 `s*_bank*_r`。**缺此任何 trigger 时序无效**。须解决 emit 映射：load 微指令进独立 IQ 流（RTL `iq_inst[3]/[4]`），
即产出 `iq_fp16mul_s0123.hex`/`iq_fp16mul_s4567.hex`（generate_data_list_coverage 映射到 iq_inst[3/4]）。

### D2. 锚定用硬件 COND/DLY ready 握手，非 abs_t〔关键，翻 ADR-046 D3〕
`smi_iq_dly_queen.v:241/263` 有 per-inst COND（`inst_rdy_in[w_mem_cond]`）+ DLY（`w_mem_dly`）。三段最小时序（全固定
latency 推导，[[derive-timing-not-vcs-calibrate]]）：**RtoVR 产 dvr → s_bank load（COND-等操作数产生方 inst_rdy、晚 dvr ≥1
拍）→ trigger（COND-等 load 完成 / DLY≥1、晚 load ≥1 拍，避 `fp16mul.v` 写-then-组合读同边沿）**。绑物理 ready 事件 →
免疫跨-II abs_t 非线性（这才是「能预测 VCS 塌拍的锚定量」= ready 握手非拍号，解释了 abs_t 100≫42 仍塌同拍）。
- 待商榷（hco）：fp16mul IQ2 是否支持 COND 字段。不支持则 COND 退 DLY-only，可能仍需 compute 收进 DST region 同域（Opt-B'）。

### D3. 修 sim/RTL 契约分歧〔防假绿复活，守回归〕
`fp16_mul_unit.py` 改成**要求 `cru_fp16mul_s0123/s4567` vld 才锁 regs**（对齐 RTL 写使能语义），否则后续 fp16mul 工作
全程假绿、无法用 sim 守回归。

## 隔离 / 不回归

- D1 load 微指令 + D2 COND 锚定**仅加在 q_dst DFG**（本 ADR scope）。fma/matmul_n/其他 fp16mul 路暂不动（DEBT-047-FP16MUL-LOAD-ALLPATHS）。
- D3 sim 修**全局生效**（fp16_mul_unit 是共享单元）→ 会让 fma/matmul_n 的 sim 从「假绿」变红（因它们也没发 load）。
  **这是暴露真相、非回归**——须同步给 fma/matmul_n 也发 load（否则 pytest 破）。故 D3 与「全路补 load」实际耦合：
  **要么 D3 只在 q_dst 门控（临时）、要么 D1 同时给全 fp16mul 路发 load**。落码时按此权衡（倾向：D1 抽成共享 helper、
  全 fp16mul DFG 都发 load，一次修对；D3 全局改 → 全路一致 RTL-忠实）。⚠ 这可能使 scope 实际略超「只 q_dst」——落码时评估。

## Options considered
- **补 load 微指令 + COND 锚定（选，hco 推荐）**：最 RTL-忠实、最小、符合「算不要调」。
- **Opt-B'（compute 收进 DST region 同 II 流）**：仍须先补 load，否则一样 0；仅当 COND 不可用再降级。
- **ADR-046 D3 abs_t lead-NOP（拒/supersede）**：前提被证伪（不读 dvr、abs_t 跨-II 不可比、不解释恒 0）。

## 待落码 / 验收
- **补测**：① emit 对抗单测——断言 q_dst 发射 IQ 集合**含 IQ3/IQ4**、load dispatch 早于 trigger（人为删 load → assert fail）。
  ② sim 忠实性单测——dvr 有值但无写使能 → 断言 D=0（防 auto-latch 假绿复活）。
- **VCS 验收（升 Accepted）**：`selected_s*_r` / `dr_fp16mul_d*` 非零 + bank5 逐词 **bit-match golden_data.npz 的 y**
  （=dot products [71.37,91.73,81.34,69.53]...）。
- **DEBT-047-FP16MUL-LOAD-ALLPATHS**：fma/matmul_n/full 等全 fp16mul 路同缺 load，须系统性补（follow-up）。
- **DEBT-047-COND-FIELD**：fp16mul IQ2 COND 字段可用性待 VCS/RTL 确认（不支持则 DLY-only / Opt-B'）。

## 关联
[[adr-045-qdst-delivery-works-optb]]（全过程 + VCS 内部探针）、ADR-046（D1/D2 SMI_DS_T_DELIVER+RESIDENT=40 仍成立、
D3 被本 ADR supersede）、ADR-032（D2 FP16MUL micro_groups 定义，本 ADR 兑现其 emit）、ADR-031（sim 全盲假绿同类先例）、
[[derive-timing-not-vcs-calibrate]]（COND ready 握手 = 硬件锚定非拍号）。
