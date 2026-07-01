---
adr_id: ADR-042
title: SMI_DS_T 无 B=1 模式（须 2 tile / 16-beat / 2 CRI）+ DST 数据通路三流须作密集 II=1 region 与 compute II 解耦（修 fp16_matmul_q_dst VCS bank5=0）
decided: 2026-07-01
last_verified: 2026-07-01
still_holds: proposed（根因 VCS+hco 实证已闭；修法设计已定、实现未落；升 Accepted 待 VCS hi_ready 拉高 + bank5≠0 bit-match golden）
builds_on: ADR-038, ADR-041
supersedes_partial: ADR-041 DEBT-041-STREAM-DLY（升级为「三流协同 + 双-tile 完整性」，非仅 CRI 相邻）
hco_review: BLOCK-then-endorse-direction（2026-07-01，RTL 独立复核：证伪「单纯 issue 相邻即可」、指出真根因 = gather 单-tile + 三流被 compute II 打散；endorse 两-SMI_DS_T-节点 + 三流密集 region 方向，条件见下）
---

# ADR-042 · SMI_DS_T 无 B=1 + DST 三流密集 region 解耦 compute II

> **状态：proposed**（根因闭合、设计定；实现 + VCS 未落）。
>
> **一句话**：`fp16_matmul_q_dst` VCS bank5 全零的真根因**不是** handoff 假设的「SHIFTUP-phase 流式病」，
> 而是 **SMI_DS_T pair 引擎的 hi slot 饿死**——q_dst 的 SHIFTDN gather 只发 1 个 tile（8 拍），但 RTL pair
> 引擎硬编码需 **2 个 distinct tile = 16 拍**；且 compute II=10 把 DST 三条流（gather / CRI / broadcast）全打散。
> 修 = ①激活按 8×8 tile 呈现（col-0 填 [a0..a7] + padding，产 16 拍）②三流作**密集 II=1 streaming region、
> 整体对 compute II 门控**。

## Context（VCS 实证 + hco RTL 复核，2026-07-01）

VCS 波形（`tb_aiacc_top_f.dut.u_smi_top.u_ds_t.*`）：`data_out_vld` 全程 0 → SMI_DS_T 从不出数 → SHIFTUP
广播空 → compute 乘零 → bank5=0。两 `cri_vld`@cyc 4185/4195（相距 10=compute II）；`lo_ready`→1 但 `hi_ready`
**恒 0** → `start_out`（`smi_ds_t.v:88` = both_issued & both_open & lo_ready & **hi_ready** & ~out_phase）永假。

hco 独立 RTL 复核（`smi_ds_t.v`）判定真根因（BLOCK 初版「单纯 issue 相邻」拟修）：

- **每个 slot 需 `WR_BEATS = PARAM_IN_LINE_BYTE_CNT(16) × PARAM_IN_LINE_NUM(8) / 16 = 8` 个 distinct crd 拍
  才 `ready<=1`**（`smi_ds_t.v:267-268/301-310`）。B=2 pair 须 **16 拍 gather / 2 个 distinct tile 描述符**
  填满 lo+hi。hi 靠 gather 流后 8 拍（第二 tile，`wr_slot` 在 both_open&lo_ready 后切 hi，line 107-110）。
- **SMI_DS_T 无 B=1 逃逸**：`start_out` 唯一置位处硬要求 `hi_ready`；session 只在 `hi_out_last_beat` 关
  （line 196-204）。**任何走 SMI_DS_T 的转置都必须喂满 2 full tile + 2 CRI，否则 deadlock 在 data_out_vld=0。**
- **emit 实证**：q_dst `iq_shiftdn` count byte=**0x08**（1 tile）、描述符 offset 0x00/0x00 重复 + 孤立 0x20（丢 enable）；
  PASS `unit_ds_transpose_fp16` count=**0x10**（2 tile）、offset 0x00/0x20 distinct。q_dst `iq_shiftup` 广播仅 3 active 行、
  PASS 16 密集行。→ **compute II=10 把三条流全打散**（描述符失一致 / CRI 拉 10 拍 / 广播稀疏）；
  `unit_ds_transpose`（纯 shuffle、II=2）三流紧凑故 VCS 8/8。
- 根因**为何 q_dst 是单-tile**：q_dst 激活 = K=8 向量（8 SMC 各 1 fp16，lanes=1），gather 仅 8 元素 = 1 tile；
  unit（8×8 矩阵 = 64 元素）天然填满 16 拍。**向量必须补成 8×8 tile。**

## Decision

### D1. 硬件契约固化：SMI_DS_T 无 B=1〔hco ✓〕
走 SMI_DS_T 的任何转置 = **2 full tile（16 拍 gather）+ 2 CRI（pair）**。单-逻辑-tile 语义（fp16 向量）
**emit 层必须补 padding 第二 tile**。禁止指望 RTL 单-issue 收尾（会 deadlock）。

### D2. 激活向量按 8×8 tile 呈现（q_dst）〔本 ADR 推导，待 VCS 证〕
K=8 激活向量 [a0..a7] **列 0 对齐存**：SMC_s bank{SRC_BANK} 持 `[a[s], 0,0,0,0,0,0,0]`（8 fp16 = 1 词，
7 padding）。gather 全 8×8 tile（col0=[a0..a7]，其余 0）→ SMI_DS_T 转置 8×8 → **输出 row 0 = [a0..a7]**
（转置把 col0 变 row0）、row1-7=0。B=2 → row0 落 **beat 0**。compute 读 beat 0 = [a0..a7]。
`smi_ds_t_config` lanes=1→8（`ROWS=8`），mirror `unit_ds_transpose_fp16` 已 VCS-green 的 8×8 机制。

### D3. DST 三流作密集 II=1 streaming region、对 compute II 门控〔hco endorse 方向〕
`gather(2 tile,16 拍) → SMI_DS_T CRI(pair,相邻) → SHIFTUP broadcast(16 密集拍)` 三流须作**自带 II=1 的流式
region 整体发射**，与 compute loop II=10 解耦（RTL `session_active` 本就是 session 语义）。hco endorse 路径 =
**建两个 SMI_DS_T DFG 节点**（ds_transpose / ds_transpose_1，同构已工作的 ds_gather/ds_gather_1 abs_t 5/6），
让 SMT 排相邻；但每个 DS_T 节点须各绑其 **gather-tile 描述符 + broadcast 拍**作一等 companion 一起密集落。

- 🔴 **BLOCK-1（必修）**：gather 必发 2 个 **distinct** tile 描述符（`iq_shiftdn.hex` count=0x10、offset byte5
  0x00/0x20）。当前 ADR-038 两条 gather 复用同一 DVR_SHIFTDN_S 源 → 产**重复** tile（hi=lo 复制、非 distinct）→
  仍饿死。须让第二条 gather 读 padding 第二 tile（offset 0x20）。
- 🔴 **BLOCK-2（必修）**：SHIFTUP broadcast 加 `stream_beats=16` 密集流（本会话已把 q_dst 广播 SHIFTUP 改锚
  ds_transpose，但**单脉冲**、未加 stream_beats → 仍稀疏）。门控仍 producer.op_type==SMI_DS_T（ADR-041 BLOCK-2）。
- 🔴 **BLOCK-3（必修）**：compute（II=10）须在广播 16 拍窗口内某拍 sample beat 0；DST region 与 compute 的相位
  由 SMI_DS_T RAW 边（`SMI_DS_T=19`）+ region 内 DLY 标定，VCS 实测调（mirror ADR-041 STREAM_LEAD_DELAY 手法）。

## Consequences

- 波及共享 `_build_smi_ds_t_chain`（第二 tile 描述符）+ assembler DST-region 密集发射 + q_dst 的 DFG/inject/golden。
  fp32 DST（matmul_q_dst）+ fp16_matmul_full_dst 同病、同修（但本 ADR 先只 q_dst 落地 + VCS，其余传播留 DEBT）。
- sim model-A 不精确建模 16-beat 双-tile 流 → q_dst `--mode e2e` sim parity 可能须从 sim-parity 断言退到
  firmware+integrity（mirror ADR-041 DEBT-041-ADDR-MAP 对 unit 的处理）。验收以 **VCS bank5 逐词 bit-match golden** 为准。
- padding 第二 tile 的真值（zeros）+ beat 0 携激活的**确切 beat 序**（B=2 intlv 下）须 VCS 逐拍证（DEBT-042-BEAT-MAP）。

## Debts（升 Accepted 前须闭）
- **DEBT-042-DISTINCT-TILE**：第二条 gather 须读 distinct padding tile（非复用同源）；ADR-038 复用源模型对 q_dst 失效。
- **DEBT-042-REGION-DLY（2026-07-01 VCS 实测精确定位，未闭）**：结构三块（BLOCK-1/2/3）已落 + 波形验证——
  q_dst 现两 `cri_vld` 相邻（cyc 4081/4083，was 10 拍）、shiftdn 描述符 count=0x10 + offset 0x00/0x20 distinct
  （= unit 结构）、shiftup 16-beat 流。**但 `hi_ready` 仍 0 → data_out_vld 仍 0 → bank5 仍全零**，真因 = **相位**：
  SMT 把 `ds_transpose`(CRI) 排 ALAP（t=11，紧邻 compute t=33 前的 shiftup t=30），而 gather 排 ASAP（t=4，ds_src_read t=2）
  → gather 数据窗（crd_vld 4073–4093）在 both_open（两 CRI 齐、4084）**前 11 拍**就流，早 beat 被丢（RTL `!both_open→wr_slot=lo`），
  lo 过填、hi 只剩 2 拍饿死。对照 unit（works）：crd 起 1458 / both_open 1463（Δ5）/ lo_ready 1470 / **hi_ready 1478**。
  gather 密度两者相同（20 拍窗、~10 beat @2-spacing）——差**仅**在 CRI 相对 gather 的相位（unit Δ5 vs q_dst Δ11）。
  另 shiftup 16-beat 流现 emit 在 abs_t 0–15，远早于 data_out（~t=32）→ 需 stream_lead_delay 推进窗口。
  **修向（VCS 迭代）**：把 DST gather（ds_src_read/ds_gather）排/emit 到紧邻 ds_transpose（gap≈2，非 ASAP），
  或 pin ds_transpose ASAP（gather+1）+ 同步 shiftup/compute 相位。三参数（gather 延迟 / shiftup lead / compute sample）
  耦合，须 VCS 逐拍标定。**这是 bank5 从「结构对」→「硅出数」的最后一里。**
- **DEBT-042-PROPAGATE**：fp32 matmul_q_dst / fp16_matmul_full_dst / matmul_q_dst_k8 同修待传播（先 q_dst 打样）。
- **DEBT-042-SIM-MODEL**：sim model-A 16-beat 双-tile 流未建模；sim parity 断言须相应放宽。

## 可证伪测试（emit 层，无需 VCS 即可拦回归）
- `iq_shiftdn.hex` count=0x10 且 2 个 distinct 描述符（byte5 0x00/0x20）。
- `iq_shiftup.hex` 广播 active 行数 = 2×OUT_ROWS = 16（非稀疏）。
- VCS 门（升 Accepted）：`u_ds_t.hi_ready` 拉高 ≥1 次、`data_out_vld` ≥16 拍、bank5 逐词 bit-match golden。

## 关联
memory：[[matmul-q-dst-vcs-rootcause-3stream]]（本 ADR 的 VCS+hco 实证）、[[shiftup-shiftdn-only-dst-datapath]]
（smi_top.v:384 广播源硬接 ds_t_data_out）、[[matmul-addtree-dataflow-authoritative]]（激活广播必经 SMI_DS_T）、
[[vcs-env-and-dst-bank5-finding]]（VCS 环境 + unit_ds_transpose_fp16 VCS 8/8 参照）。
