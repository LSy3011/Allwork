---
adr_id: ADR-036
title: SMI_DS_T 重建为流式流水单元（推翻 model-A gather-gate，对齐文档线速流水编程模型）
decided: 2026-06-10
last_verified: 2026-06-10
still_holds: proposed
builds_on: ADR-022, ADR-032, ADR-033, ADR-034
supersedes_partial: ADR-033, ADR-034  # model-A 的 gather-gate SmiDsTUnit + cluster 后门触发整体作废
---

# ADR-036 · SMI_DS_T 重建为流式流水单元

> **状态：Proposed（doc-first per ADR-004，推翻 SMI_DS_T 既有 sim 模型 + 硬件 datapath 语义 → 必过 hco）**。
> user 订正：NPU 是**固定 latency + 指令时序对齐**的同步机器；SMI_DS_T 不是「攒齐再转」的 gather-gate 单元，
> 而是**线速流水里的一个流式转置级**（文档「激活矩阵排序微指令」）。本 ADR 按文档 para1193-1246 的
> **线速流水编程模型**重建 SMI_DS_T，目标 **sim 逐拍行为与 RTL 一致**（VCS diff 确认）。
> **范围：直接做满 K=8（PARAM_SMC_NUM=8）、B=2**；小算子（K=4）边角本轮不管。

## Context

### model-A 错在哪（推翻）
ADR-033/034 的 `SmiDsTUnit`：`gather() 逐 SMC 攒满 → is_ready() → transpose() 未齐抛错 → 一次性出` +
cluster 后门触发（`_tick_smi_ds_t_trigger` 在 kernels step 外 ad-hoc 戳 storage）。**「攒齐 + gate + 后门」
这套抽象与硬件不符**：固定 latency 同步机器没有运行时等待，所有单元靠 SMI_IQ 指令 + DLY 静态对齐、线速流。

### 文档的真实模型：DLY 对齐的线速流水（para1148-1246）
- **范式（表71-77 FPADD 样例）**：一条算子 = 多个单元的 SMI_IQ 协同；**全部 `LOOPS=N`（数据长度）+ `DLY` 错位**
  （UR_R DLY0 → RtoVR DLY1 → FPADD DLY2 → RtoVR DLY3 → UR_W DLY4），N 个数据**逐拍流过**，每单元每拍处理 1 片，
  DLY 把各级在流水里对齐。**无单元等待**。这就是「靠硬件单元指令时序对齐」。
- **向量×矩阵（para1193-1201）**：激活是向量（**H=1**，如 LLAMA2 Decode 单 token，表79 Matmul_Q），B=2 双读 →
  数据按序回、**不用转置**直接广播 → **无 SMI_DS_T**。
- **矩阵×矩阵（para1236-1246）**：激活是矩阵（**H=iLen>1**，如 Prefill 多 token，表711 Matmul_Q），读回**列对齐**
  （para1238「每拍数据是一列的一部分」）→ **必须 SMI_DS_T「激活矩阵排序」转成行对齐**（para1239）再广播。
  **SMI_DS_T = 此处比向量×矩阵多出的流式转置级**。
- **⚠ SMI_DS_T 触发条件（hco ab23044 BLOCK 订正）= 激活以列对齐读回（H>1 矩阵激活，转置间插），不是算子名**。
  「Matmul_Q」在表79(H=1)是向量×矩阵**不用** SMI_DS_T、在表711(H=iLen>1)才是矩阵×矩阵用 SMI_DS_T。**误区**：
  原 `matmul_q_dst_k8`(M=1 向量)套 SMI_DS_T 是伪场景（向量该 B=2 直读）；`matmul_q_dst`(M=4 矩阵)才是正确场景。

### 文档给的精确时序量（权威，supersede 之前口头/拼凑的 latency）
| 量 | 文档值 | 出处 |
|---|---|---|
| 一次广播读延迟 | **PARAM_SMC_NUM*2 拍**（一半气泡） | para1194/1237 |
| 间插因子 | **B=2**（连发两读，第二读填第一读气泡）→ 合流线速无气泡 | para1195/1238 |
| 线速吞吐 | 每拍收发 **PARAM_UR_BYTE_CNT/ACTIVATION_BYTE_CNT** 元素（fp32=16/4=4/拍） | para1195 |
| 读回 tile | (PARAM_UR_BYTE_CNT/ACTIVATION_BYTE_CNT) 行 × 2*PARAM_SMC_NUM 列，**列对齐** | para1238 |
| SMI_DS_T 输出 | 转成行对齐；每行分 **2*PARAM_SMC_NUM/(PARAM_UR_BYTE_CNT/ACTIVATION_BYTE_CNT)** 拍广播，每拍该多元素 | para1239 |

K=8 / fp32 实例：PARAM_SMC_NUM=8、PARAM_UR_BYTE_CNT/ACTIVATION_BYTE_CNT=4 → 读延迟 16 拍（8 气泡，B=2 填）、
线速 4 元素/拍、读回 4 行×16 列列对齐 tile、转置后每行分 2*8/4=4 拍广播。

## Decision

### D1. SMI_DS_T 升为 `kernel.units` 里的流式转置单元（去 cluster 后门）
- 在 `ParallelKernel.units` 加 `smi_ds_t`（每 SMC 一个实例？还是 SMI 中心单实例 —— **见 Q1**），由其 SMI_IQ 驱动；
  删 `SystemCluster._tick_smi_ds_t_trigger` 后门 + `SmiDsTUnit` 的 gather-gate（`is_ready`/`transpose() 抛错`）。
- 单元语义 = **流式列对齐→行对齐重排**：每拍消费 1 片列对齐输入、产 1 片行对齐输出，固定 latency；
  内部 buffer = (PARAM_UR_BYTE_CNT/ACTIVATION_BYTE_CNT) 行 × 2*PARAM_SMC_NUM 列（systolic 重排，非攒齐 gate）。

### D2. 整条 datapath = COND 屏障(SMI_DS_T→compute) + DLY 对齐线速流水(各段内部)（Q3 订正后）
`UR_read(列对齐, B=2 双读) → SHIFTDN(线速回传 SMI) → SMI_DS_T(流式转置) → SHIFTUP(线速广播 EXTRA_DATA 队列) →
[COND 屏障:下游等 INST_RDY] → RTOVR → 点积 → UR_write`。
- **COND 屏障**(para988):SMI_DS_T 这条长延迟广播链先行,compute 段经 COND 等转置好的激活进 SHIFTUP 数据队列(INST_RDY)
  再启动——**不是全程 DLY**(原 D2 错,已订正)。
- 过屏障后 **compute 段内部** `LOOPS`=数据长度 + `DLY` 错位 + II=1 线速(表71-77 范式),SMT 的 `t[c]≥t[p]+lat`+II=1
  适用于这段。**COND 屏障须在 sim/SMT 单独建模**(等待语义,非静态 DLY)。

### D3. 固定 latency 入 `pipeline_delays.yaml`
- SMI_DS_T 转置级 latency（**Q2**：文档未给单一数，需从「读延迟 PARAM_SMC_NUM*2 + 转置重排」推或 RTL 定）。
- SHIFTDN/SHIFTUP 线速链：1/hop（现 pipeline_delays=1），全链 PARAM_SMC_NUM 拍 —— 与 para1194 的 PARAM_SMC_NUM*2
  （含气泡）如何对应需 hco 核（**Q3**）。

### D4. 范围与验收
- **范围（scenario A，user 定）= 8-SMC 矩阵×矩阵，H>1**：激活 X[H, K=8]（**H=4>1 矩阵**）**列对齐铺满 8 SMC**
  （SMC_s 持列 s = [X[0,s]..X[H-1,s]]）→ SMI_DS_T 流式转置 → 行对齐 X[h,:]（K=8）逐行广播 → 每 SMC 与本地权重
  W[:,s] 点积 → Q[h,s]。**= `matmul_q_dst` 模式(M=H>1 矩阵)扩到 K=8 满 8-SMC gather**，不是 M=1 向量的 `matmul_q_dst_k8`。
  B=2、fp32（fp16 + 小算子后续）。
- **tile 粒度对账（hco 订正）**：文档两套 framing 须分清——**unit-spec（表596/597，字节粒度）**：输入 PARAM_SMC_NUM
  行 × PARAM_UR_BYTE_CNT(16B) 列、输出 PARAM_UR_BYTE_CNT(16) 行 × PARAM_SMC_NUM 列；**matmul-usage（para1238，元素粒度
  +B=2）**：(PARAM_UR_BYTE_CNT/ACT_BYTE=4) 行 × 2*PARAM_SMC_NUM=16 列。sim 建模取**元素粒度**，须在实现注释里写明
  与字节粒度 unit-spec 的换算（B=2 把列翻倍），勿混用。
- **验收**：① e2e 仍 bit-perfect（数值不回归）；② **sim log 的 UR_read(列对齐)/SHIFTDN/广播(DR_SHIFTUP_D)/UR_write
  事件的 cycle + value 与 VCS 逐拍一致**（user VCS diff 确认 —— 这是「完全一致」的 oracle）；③ 无 bank0 同拍 R+W
  伪冲突（DLY 对齐后 a_read 自然在 UR_write 之后）。

### 被 supersede
- ADR-033 Stage-A `apply_smi_ds_t` model-A 原子段、ADR-034 Stage-I 2a/2b/S2-D1/S2-D1b 的 cluster-trigger +
  gather-gate `SmiDsTUnit` + DST-GATHER/DST-SCATTER 后门日志 —— **整体作废**，由本流式模型取代。
  （ADR-033 的转置**数值正确性** + datapath 拓扑结论保留。）

## Open questions / 已定项
- ~~**Q1**（中心 vs per-SMC）~~ **已定（文档）**：表596 接口（单 CRI/INST_DONE/CRD_SHIFTDN）+ para1030「将多个 SMC 数据
  读回 SMI」+ 表597 buffer 全局单份 → **SMI 中心单实例**（非 per-SMC）。sim 放 cluster 级一个；剩「如何与 per-SMC
  SHIFTUP/RTOVR 对齐」是工程细节。
- **Q3 → 已定（para988，COND-gate + DLY-compute 混合）**：SMI_DS_T 长延迟广播链**用 COND**（para988「读广播数据的
  SMI_IQ 先行，其他模块 COND 等数据整理进队列再同步执行」），**不是全程 DLY**。即:转置好的激活进 SHIFTUP 的 EXTRA_DATA
  队列前,下游 compute 经 **COND 等 INST_RDY**（数据队列非空）→ 过屏障后 compute 段 DLY 对齐线速跑。**D2「全程 DLY」
  订正为「COND 屏障(SMI_DS_T→compute)+ DLY(compute 内部)」混合**。⚠ 实现含义:sim 的 IQ 须建 COND 等待(INST_RDY_IN),
  SMT 的 `t[c]≥t[p]+lat` 不直接建 COND wait,COND 屏障要单独建模。
- **Q2（剩余真空白）**：SMI_DS_T 转置级**固定 latency**（第一片列对齐入→第一片行对齐出）几拍？para1039「前两个指令完成
  数据读取后**立即**开始输出」暗示转置级近 0 额外延迟、主延迟在 gather（PARAM_SMC_NUM*2）。**先按「转置 latency=gather
  完成点」建模,VCS 校准**；真单一数 gated on RTL。

## HCO 复核（纸面模型，agentId `ab23044e43fa9e383`）
**APPROVE-with-notes**（方向正确,para1038/1039/1049 铁证支持推翻 gather-gate）。逐条:① DLY 范式(表71-77 全 LOOPS=N
+DLY 阶梯,DLY=累积 latency,para1162「DLY为2…延迟了2拍」) **ACCEPT**;② **Matmul_Q 用不用 SMI_DS_T 取决于 H** 不是算子名
(表79 H=1 向量不用 / 表711 H=iLen>1 矩阵才用)——原文无条件主张 **CHALLENGE,已条件化**(scenario A 钉死 H>1);③ 时序量表
基本对,但 **tile 粒度 4×16(元素+B2) vs 16×8(字节 unit-spec) 混用 CHALLENGE,已加对账**;④ Q1 文档已定 SMI 中心单实例
(已降级)、Q3 para988 指向 COND 非 DLY(已订正混合模型)、Q2 真空白;⑤ supersede 切分 **ACCEPT**(ADR-033/034 均 proposed,
推翻 gather-gate+后门、保留转置数值+拓扑)。2 BLOCK(主张2 条件化 / 主张3 tile 对账)已落地。债务:无 VCS 前 sim 自洽对
流式时序忠实度增信近零(同 ADR-021/023),验收 ② 未做不可升 Accepted,`still_holds: proposed` 维持。

## References
- v0.4.19 docx：para1148-1168（表71-77 FPADD 流水范式）/ para1193-1201（向量×矩阵，无转置）/
  para1236-1246（矩阵×矩阵 + 激活矩阵排序=SMI_DS_T）/ 表542-548（SHIFTUP/SHIFTDN）/ 表593-595（SMI_IQ）/ 表596-598（SMI_DS_T）
- [ADR-033](ADR-033-smi-ds-t-cross-smc-transpose-datapath.md) / [ADR-034](ADR-034-smi-ds-t-instruction-driven-unit.md)（model-A，本 ADR 推翻其 gather-gate + 后门）
- 代码触点：`sim/smc_simulator/units/smi_ds_t_unit.py`（重写为流式）/ `sim/smc_simulator/system/cluster.py`（删后门触发）/
  `sim/smc_simulator/core/kernel.py`（units 加 smi_ds_t + IQ 驱动）/ `hw_config/pipeline_delays.yaml`（SMI_DS_T latency）/
  `src/synthesis/ucode_solver.py`（SMI_DS_T 段进 DLY 对齐排程）
