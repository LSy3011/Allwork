---
adr_id: ADR-039
title: SHIFTUP 广播负载硬连 = SMI_DS_T.data_out —— unit_ds_transpose 弃 bank0 staging、按硬连 DST datapath 重建广播/写回
decided: 2026-06-29
last_verified: 2026-06-29
still_holds: proposed
builds_on: ADR-022, ADR-023, ADR-025, ADR-036, ADR-038
supersedes_partial: ADR-036  # S3-b 的 _dst_bcast_feed(转置行→SHIFTUP 广播输入)方向对，但仍挂 a_read(bank0) 触发；本 ADR 去 bank0 staging round-trip
---

# ADR-039 · SHIFTUP 广播负载硬连 = SMI_DS_T.data_out

> **状态：Proposed（doc-first per ADR-004；改 SHIFTUP/SHIFTDN 跨-SMC datapath 语义 + DFG 接口 → 必过 hco。
> 已过 hco：APPROVE-with-conditions，C1–C5 + 加严 VCS 验收已并入；实现走 C2 选项 (a) 不污染共享 helper）**
>
> **一句话**：ADR-038 配对 B=2 已 VCS 证实让 SMI_DS_T 出数（`hi_ready/start_out/data_out` dead→alive），
> 但 `unit_ds_transpose` bank5 仍全 0。RTL 取证（evaluator PASS×5）：**SHIFTUP 的广播数据负载在硬件里
> 硬连 = `SMI_DS_T.data_out`，没有别的源**；编译器现用「`a_read` 读 bank0 staging → 独立 SHIFTUP 广播」
> 是**虚构 round-trip**、与这条硬连唯一流相争 → data_out 从未到 bank5 写端口。修 = 按硬连流重建：
> `ds_transpose(data_out) → SHIFTUP(IQ14 wrd 硬连) → 广播 → 各 SMC UR_write bank5`，**删 a_read/bank0 staging**。

## Context

### 起源：ADR-038 VCS 实测（[[timing-verify-strategy-plan-c]]）
方案 C 时序 diff 跑 `unit_ds_transpose`：ADR-038 配对 B=2（第二条 SHIFTDN gather）**VCS 证实**让 SMI_DS_T
配对引擎 dead→alive（`hi_ready`=1@7115ns / `start_out`=1@7115ns / `data_out_vld`=1@7145ns / `data_out`
输出非零转置数据）。但 `timing_diff --bank 5` 仍 FAIL：bank5 `bram_bank_inst[5].dina` **全程字面零**——
转置数据产出了（7265-7295ns 4 个非零词）却从未到达 bank5 写端口。

### 硬件契约（user 2026-06-29 权威 + RTL 取证，evaluator 独立 PASS×5；见 [[shiftup-shiftdn-only-dst-datapath]]）
**「SHIFTUP/SHIFTDN 只有 docx 的 DST datapath 一种用法，硬件只实现这个数据流」**：
`UR read — SHIFTDN(gather) — SMI_DS_T(transpose) — SHIFTUP(broadcast) — UR write`。

RTL 端到端三重锁死（广播 payload 唯一源 = `ds_t_data_out`）：
1. `smi/smi_top.v:384` `.wrd_data((ID==14)?ds_t_data_out:32'b0)` —— **IQ14=SHIFTUP**（smi_iq.v:52/92、
   cru_widths.vh:80 多点印证；IQ15=SHIFTDN），其广播数据口 `wrd_data` 字面量硬连 = `ds_t_data_out`
   （128b 全宽，无截断），**无 mux / 无 wri / 无 wra 旁路**。
2. `smi/smi_iq_inst_queen.v:265` `r_data <= wrd_data` → `L532` 读出指令中段 **[128:1] 恒取 r_data**
   —— 编译器发的 `wri_data` 只填 bit0(vld) + 高 47 位控制(broadcast/smc_id_en)，**立即数填不进 128b 数据域**。
3. `smc/shift_up/shift_up.v:34` `data_in = cru_shiftup_in[128:1]` —— **SMC 端 shift_up 唯一数据输入 =
   指令字 [128:1]，无任何本地 UR 读出口** → 物理上无法广播本地读出的数据。

VCS 印证缓冲机制：`data_out_vld` 拉高(7145ns)期间 data_out 锁进 IQ14 的 `r_data`；`o_cru_shiftup` 指令
后发(7435ns)读 r_data 广播 —— SHIFTUP 读的就是硬连进来的转置行，**不是 bank0 的某个 UR 读**。

### 编译器现状（为何 bank5=0）
`verification_v2/ops/cross_smc_unit_op.py::UnitDataShuffleTransposeOperator.get_dfg` +
`matmul_op._inject_shiftup_broadcast`：
- `a_read`(UR_READ bank0, pin ur-r0, broadcast_source=True) → `shiftup_a_read`(SHIFTUP, 广播 a_read 值)
  → `rt_a`(RTOVR sel=SHIFTUP_D) → `out`(UR_WRITE bank5)。
- sim（ADR-036 S3-b `_dst_bcast_feed`）：SMI_DS_T 转置行喂 SMC_0 的 SHIFTUP 广播输入缓冲，`a_read` 按
  decode addr 取本行 —— **sim 自洽**（bank5 拿到转置行），故 e2e 8/8 假绿。
- **但真硬件 SHIFTUP 广播的是 `data_out`（硬连），不是 `a_read`（bank0）**。bank0 在硬件里根本没被
  SMI_DS_T 写过（S3-b 已删 bank0 staging 写），`a_read` 读空 bank0 → 广播空 → bank5=0。`a_read`/bank0
  round-trip 是 **model-A 残留虚构**，与硬连 `data_out→SHIFTUP` 唯一流相争。

## Decision（hco APPROVE-with-conditions，2026-06-29；C1–C5 已并入下方「钉死」节）

### D1. unit_ds_transpose 广播/写回按硬连 DST datapath 重建
目标 DFG（删 `a_read` + bank0 staging；**保留 RTOVR sel=29，见 C1**）：
```
ds_src_read(UR_READ 列对齐) → SHIFTDN gather ×2(B=2, ADR-038) → ds_transpose(SMI_DS_T → data_out)
                                                                      │ (硬连 wrd_data, IQ14)
                                                          shiftup(SHIFTUP, dep=ds_transpose, 广播 data_out)
                                                                      │ (dr_shiftup_out)
                                                          rt(RTOVR sel=SHIFTUP_D=29, dep=shiftup)
                                                                      │ (唯一物理通路 → UR 写口)
                                                          out(UR_WRITE bank5, dep=rt, 捕获广播行)
```
- `shiftup` 依赖 `ds_transpose`（数据源 = data_out，非 a_read）；**删 `a_read`(bank0) 节点 + broadcast_source/
  bank0 staging**。SHIFTUP 指令只需在 `data_out_vld` 窗口后发（读 IQ14 已锁的 r_data，DATAQ_DEPTH=128）。
- `out`(UR_WRITE bank5) 经 `rt`(RTOVR sel=29) 依赖 `shiftup`（C1：`dr_shiftup_out`→UR 写口唯一物理通路 = RTOVR
  sel=29，rtovr_config.yaml:34）；每转置行 → bank5 consecutive addr。
- **范围限 unit_ds_transpose**（`UnitDataShuffleTransposeOperator`）；实现走 C2 选项 (a)——**另起一条锚在
  SMI_DS_T 节点的注入路径，shared `_inject_shiftup_broadcast` 一字不动** → matmul/fma 不受影响、D3 切分干净。

### D2. sim 镜像硬连流（删 a_read-bank0 round-trip 残留；C5 钉死触发改写）
- `cluster._tick_smi_ds_t_trigger` 的 scatter 已走 `_dst_bcast_feed`（S3-b，方向对）；本 ADR 去掉「a_read 读
  bank0 触发广播 + 选行」的 model-A 触发（kernel.py:670-676），改 **SHIFTUP 按自身 issue/decode addr 选第 m 行
  feed**（镜像 RTL `data_out→wrd→广播`）。三分支优先级（kernel.py:575-579 `_dst_bcast_feed > _bcast_capture
  > live`）在 a_read 消失后须退化为「SHIFTUP 直取 feed[m]」、不回退 live read。
- **完整性门 / audit 端点**随结构改：UR-WRITE bank5 归属 `out`，SHIFTUP 广播事件归属 `shiftup`，**删 bank0 读
  端点**；C5 钉死「SHIFTUP 无 UR_READ 源」不得被完整性门误判为残缺访存（ON real-gather 门须豁免无源 SHIFTUP）。

### 钉死（hco C1–C5，落码前必满足）
- **[C1 · RTOVR 不可删]** `dr_shiftup_out` → UR 写口唯一物理通路 = RTOVR sel=29（rtovr_config.yaml:34、
  shift_up.v 输出）。删的只是 a_read+bank0；链路保留 `…→shiftup→RTOVR(sel=29)→UR_WRITE`。
- **[C2 · 共享 helper 不污染，最关键]** SHIFTUP 现由 `_inject_shiftup_broadcast`（matmul_op.py:22-100）从
  `UR_READ + broadcast_source` 锚生出，**与 matmul/fma 共享**。删 a_read = 删锚 → 注入器不再产 SHIFTUP。
  **采纳选项 (a)**：为 unit_ds_transpose 写**独立注入**（锚在 ds_transpose/SMI_DS_T 节点，产 SHIFTUP(dep=
  ds_transpose)+RTOVR(sel=29)+UR_WRITE），`_inject_shiftup_broadcast` **一字不改**。⚠ 绝不走选项 (b) 改共享
  helper（那等于动 matmul/fma、须撤 D3 并补 fma/matmul 回归）。
- **[C3 · smc_id_en 广播门]** shift_up.v:44 捕获门 = `vld & broadcast & (SMC_ID[4:0] <= smc_id_en)`。8 SMC 全
  捕获须 `broadcast=1 & smc_id_en=PARAM_SMC_NUM-1`。`PARAM_SMC_NUM=8 → smc_id_en=7`（承 ADR-031 全单元 emit
  N-1）；须确认 spec 链把 8 传进注入器（非默认 32→31）。
- **[C4 · systolic passthrough]** shift_up.v:25-26,40-46 广播指令字沿逐-SMC 链透传（每 hop 1 拍，
  pipeline_delays.yaml:16 注「广播链全长另计」），门 = `SMC_ID<=smc_id_en`。Q1 时序须承认 passthrough，
  勿把「单 hop=1」与「整链 broadcast 延迟」混算。
- **[C5 · sim 触发改写]** 见 D2：SHIFTUP 按 decode addr 选行 + 删 bank0 读端点 + 完整性门豁免无源 SHIFTUP。

### D3.【牵连，本 ADR 仅 flag 不解】matmul/fma SHIFTUP 广播激活疑非物理
SHIFTUP 既只能广播 DS_T 转置输出，则 ADR-023(fma_parallel_n)/ADR-025(matmul_parallel_n) 的「SHIFTUP 广播
激活向量」在 RTL `wrd_data`/`shift_up.data_in` 层**无源**（evaluator 推论 2 PASS）。其 8/8 bit-perfect 是
sim 自洽（sim SHIFTUP 广播任意 `dr_d`，RTL 不能）。**激活若要经 SHIFTUP 广播，必须先过 SMI_DS_T（会转置）。**
→ 记 **DEBT-039-MATMUL-BCAST-NONPHYS**，单独 ADR + hco 评审「multi-SMC matmul 激活分发到底怎么在硬件上做」
（与 DEBT-023-B 真 fabric 同根）。本 ADR 不在此动 ADR-023/025。

## 待 hco / 实现期钉死的开放问题
- **[Q1 时序]** data_out 流式出 N 行（latency=16 后每拍一行），SHIFTUP 须逐行广播、UR_write 逐行写 bank5
  consecutive addr。SHIFTUP 指令发射拍 vs data_out_vld 窗口 vs r_data FIFO（DATAQ_DEPTH=128）的精确对齐——
  VCS 实测 `o_cru_shiftup`@7435ns 在 data_out 词(7265-7295ns)之后，靠 r_data 缓冲。**含 C4 systolic passthrough：
  广播指令字沿 SMC 链每 hop 1 拍透传，整链 broadcast 延迟 ≠ SHIFTUP 单 hop=1**（pipeline_delays.yaml:16）
  （DEBT-036-DST-BCAST-FEED-TIMING）。
- **[Q2 hi 半字]** ADR-038 DEBT-038-B2-GATHER：data_out 现 = high-16 重复（gate-filler hi 复制 lo），hi slot
  须携各 fp32 low-16。即便 D1 喂数路径打通，hi 内容仍须修对才 bit-perfect。**两 bug 正交，先 D1（喂数）
  见 bank5 收到 high-16-dup 数据，再修 Q2 hi 半字。**
- **[Q3 多算子]** 5 个 SMI_DS_T 算子里 matmul_q_dst 等的广播也走 a_read-bank0 吗？本 ADR 先收 unit_ds_transpose，
  其余按同模式迁移（实现期逐一 VCS）。

## 建议测试（hco，可证伪）
- **结构契约门（pytest）**：断言 `UnitDataShuffleTransposeOperator.get_dfg` 产出**无 `broadcast_source`/bank0
  UR_READ 节点**，且 `SHIFTUP.depends_on` 含 SMI_DS_T(ds_transpose)、`RTOVR.sel==29`、`UR_WRITE` 经 RTOVR
  —— 锚 C1+C2，防回退 a_read round-trip。
- **fma/matmul 回归门**：断言 fma_parallel_n / matmul_parallel_n 的 SHIFTUP 注入产物 byte 不变（证 C2 选项 (a)
  没污染共享 helper）。
- **方案 C 逐事件 diff**：bank5.dina **per-SMC（SMC_1..7 各一条）** 非零断言，不止 SMC_0。

## 验证 / 验收（VCS-gated，hco 加严）
- pytest 不破（契约门 + 现有 SMI_DS_T 测试）；`--op unit_ds_transpose` e2e exit=0。
- **升 Accepted 卡 VCS（hco：原「bank5 非零」必要非充分，须加三条 DST datapath 忠实条件）**：
  1. **广播到达 SMC_1..7**（验 smc_id_en=7 门 shift_up.v:44），不止 SMC_0——防重蹈 ADR-031「真硬件仅 SMC_0 跑」盲区；
  2. **行→bank5 consecutive addr 对齐**：data_out 第 m 行 → bank5 addr=m（不止「非零」）；
  3. **SHIFTUP 发射拍落在 data_out_vld 窗口内**（r_data FIFO 缓冲对齐闭合 DEBT-036-DST-BCAST-FEED-TIMING）。

## Consequences
- ADR-036 S3-b 的 `_dst_bcast_feed`(转置行→SHIFTUP)**方向保留**；本 ADR 只 supersede 其仍挂 `a_read`(bank0)
  触发的残留。
- model-A sim 假绿再添一例：bank0 staging round-trip 让 sim 8/8 但 RTL bank5=0；方案 C 是唯一第三方 oracle。
- D3 暴露的 matmul 牵连若成立，ADR-025 N 维广播机制需重审（大范围，单列 DEBT-039-MATMUL-BCAST-NONPHYS）。
