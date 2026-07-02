---
adr_id: ADR-045
title: 移植 vec128 已 VCS 验证的 SMI_DS_T 交付范式到 fp16_matmul_q_dst compute 激活路 —— 真激活落 held 末 beat + 时序从 global_t 推导
decided: 2026-07-01
last_verified: 2026-07-01
still_holds: proposed（诊断已 VCS+hco 收敛；机制待 hco 复核 + 落码 + VCS 验证）
builds_on: ADR-043, ADR-044
implements: ADR-043 DEBT-042-EMIT-SMT-LAYER（compute 相位成 SMT 不变量）的 q_dst 落地
supersedes: ADR-042 D2 的 col-0 8×8 single-word 激活 layout（对 q_dst-compute）；ADR-043 D1/D4 的「compute 采 resident-35 = beat15 padding」目标（beat15 是 hi-slot 垃圾、非真数据）
hco_review: APPROVE-with-notes（2026-07-01，RTL 行号级取证 smi_ds_t.v:58/64-70/88/298-317 + shift_up.v:49-58 + shift_down.v:38-50 + smi_top.v:384；3 BLOCK errata 回填见下，still_holds 不变）
user_decision: 移植 vec128 范式（2026-07-01，AskUserQuestion 明确选定）
---

# ADR-045 · 移植 vec128 交付范式到 q_dst compute 激活路

> **一句话**：`unit_dst_vec128`（ADR-044）已 **VCS 8/8 真绿**把 128 fp16 向量经 SMI_DS_T 转置路**按序、8-distinct/beat、无
> doubling** 交付 bank5，且**时序全从固定 latency + `global_t` 推导**（不手调 lead_delay）。`fp16_matmul_q_dst` 的激活路
> **还停在 col-0 8×8 single-word + resident-35（采 beat15）的旧错误分支**，没吃到 vec128 的成果 → VCS bank5=0。修 =
> 把 vec128 的交付机制（时间分离 gather 消 doubling + per-op intlv + two_word 路由 + 时序从 global_t 推导）**移植到 q_dst
> 激活路**，并让 compute 采样落在**真激活所在的 held 末 beat**（而非 padding beat15），使相位成为 SMT 建模的不变量。

## Context（为何 q_dst 卡在 bank5=0，而 vec128/unit 真绿）

三个已 VCS+hco 定死的事实（见 [[matmul-q-dst-vcs-rootcause-3stream]]、ADR-043、ADR-044）：

1. **SMI_DS_T 引擎已在 q_dst VCS 出数**（`hi_ready/start_out/data_out_vld` @cyc4099-4102，ADR-042 commit b3753b7）——
   引擎 datapath 通了。
2. **vec128 证明交付可做到 8-distinct/beat 无 doubling**（ADR-044，magic `[1..8|...]`）——doubling 是**两 gather 背靠背**的 bug，
   靠 **D1 时间分离**（`dst_two_gather_sep` + `dst_explicit_emit` + `DST_GATHER_DRAIN=2N`）消除。这推翻了 ADR-043 早期
   「单 beat 只能携 4-distinct、K=8 喂不出 8-lane 激活」的悲观结论。
3. **q_dst compute 仍取到垃圾** `[-100,-200]`（ADR-043 实现进度段，VCS `dvr_fp16mul_s4567`@cyc6150）：
   - 采样目标 = **resident-35 = beat15 = hi-slot = tile2 = 未注入 UR = 硅上垃圾**（坐实 hco BLOCK-2）；
   - 且当时未做时间分离 → beat 是 **B=2 half-split**（4 SMC/beat），非 8-lane。

**核心 RTL 张力（决定本 ADR 的设计）**：`shift_up.v:52-58` dr_shiftup 广播寄存器**每广播拍被覆盖**，**只有末 beat 被 held**
到下次广播 → compute 的 RTOVR resident 窗（`dependency_axioms.py:128-133`，II-1）只能稳定采到**末 beat（beat15）**。故：
- 采 **beat14（col-0 降序转置下 row0=[a0..a7] 现落的拍）** → 只 held 1 拍 → 需精确相位、脆（违「时序要算不要调」的隐患）；
- 采 **beat15（held、宽窗）** → 但 beat15 现是 hi-slot padding 垃圾。

**⟹ 干净解 = 让真激活 [a0..a7] 8-distinct 落到 held 末 beat（beat15），既宽窗又真。** 这正是 ADR-043 D2「activation 排
LAST_BEAT」的方向——它没错，只是当时缺 vec128 的 **two_word_gather**（把真数据路由到 hi-slot/T1）机制。vec128 已把该机制
落进共享 `_build_smi_ds_t_chain`（`two_word_gather`/`hi_word_offset`/per-op `intlv`/时间分离）——**q_dst 只是没用上**。

## Decision

**把 vec128 已 VCS 验证的 SMI_DS_T 交付范式移植到 q_dst 激活路**，四条子决策（前三条复用现成机制，仅第四条动 SMT 边）：

### D1. q_dst 激活路吃 vec128 的时间分离 gather（消 doubling → 8-distinct/beat）
`ManualFp16MatmulQDsTOperator.smi_ds_t_config` 加 `two_word_gather=True` + per-op `intlv`，走共享 chain 的时间分离路径
（`dst_two_gather_sep`/`dst_explicit_emit`/`DST_GATHER_DRAIN`，ADR-044 D1）。消除 B=2 half-split → 每真 beat = 8-distinct
[a0..a7]。**不新建机制**，仅 config 开关。

### D2. 真激活 [a0..a7] 路由到 held 末 beat（beat15 / hi-slot T1）〔核心，待 hco 定 beat 索引〕
用 two_word 的 **hi-slot（T1/word1，`ds_gather_1`）承载真激活**、lo-slot（T0）承载 padding（或反之，以实际落 beat15 者为准）。
配合 identity-via-layout 逆置换（ADR-044 D4 同法：`inject_data_per_smc` 用 RTL 转置置换之逆注入），使真 [a0..a7] 恰落
**held 末 beat**。**哪个 slot/word/layout 使 [a0..a7] 落 beat15 = hco + VCS 逐 beat 定**（RTL 置换硬定值 `smi_ds_t.v:313-317`
输出 lane =`(LINE_NUM-1-gi)` 降序 + `smi_ds_t.v:64-70` intlv_out 决定 T0/T1 出序）。

### D3. compute 采样边从 resident-35（beat15 padding）改锚**真激活所在的 held 末 beat**
`dst_compute_resident` marker 保留（compute 仍采 held 末 beat），但 D2 保证末 beat 现是**真数据**而非 padding。故
`SMI_DS_T_RESIDENT` 的**语义不变**（首 beat19 + window16 = 末 beat 就绪），仅**内容**从 padding 变真激活——采样边**无需改
边权**，只需 D2 把真数据摆到末 beat。**若** hco/VCS 判定真激活只能落中段 beat（非末 beat、held 1 拍），则退 D3'：
compute 采样拍 = `ds_transpose.global_t + SMI_DS_T_FIRST_BEAT + beat_index×per_beat`（新受控常量，从固定 latency 推导、非
resident 末 beat），并须把 emit 层广播拍也锚到同一 `global_t`（闭合 DEBT-042-EMIT-SMT-LAYER 的 emit-SMT 断裂）。

### D4. 时序从 global_t 推导，不手调 lead_delay（[[derive-timing-not-vcs-calibrate]]）
镜像 vec128（`UnitDstVec128Operator` 注释 358-361）：DST 广播→compute 采样拍由 SMT `global_t`（已含固定 gather/collect/
transpose latency + two-separated-gather 的 2N 增量）导出，**不设手调常量**。换 `PARAM_SMC_NUM`/K 自动对。VCS 只作**验证**
不作来源。

## 隔离 / 不回归（硬约束）

- **仅动 `ManualFp16MatmulQDsTOperator`**（q_dst-specific config + inject）+ compute 采样边的 marker 门控。共享
  `_build_smi_ds_t_chain` 的 vec128 机制**已 per-op cfg 门控**（默认单-word/intlv_out=1）→ fp32 `matmul_q_dst`/`fma`/
  `matmul_n`/`vec128` 走各自默认、不受影响。
- **fp32 DST 隔离**：`dst_compute_resident` marker 仍只 q_dst 设（`matmul_op.py:1626`）；fp32 q_dst 下游是 FP32ADDTREE5TO1、
  不设 marker → 留 19（ADR-043 D1' BLOCK-1）。
- **vec128 不回归**：vec128 用 `intlv=(0,0)`+`two_word`+identity-layout 交付 **bank5**（streaming-to-bank，无 compute）；
  q_dst 用 `two_word` 交付 **compute**（resident-sampling）——两者 cfg + 消费边不同，各自门控。

## Options considered

- **Opt-A（选，= D1+D2+D3）**：真激活落 held 末 beat + 复用 resident-35 语义。最小改动、相位天然宽窗、不碰 emit 结构。
- **Opt-B（备，= D3'）**：compute 采中段真 beat + emit 广播拍锚 global_t（彻底闭合 emit-SMT 断裂）。工作量大（动 assembler
  DST region emit 回灌 SMT），仅当 Opt-A 的 VCS 验相位仍脆时上。
- **Opt-C（拒）**：守 col-0 single-word + resident-35（现状）。ADR-043 自证 beat15 是 padding 垃圾、单 beat 半行——死路。

## 待 hco 复核 / 落码前须定

1. **beat 索引 + slot 路由**（D2 核心）：two_word + intlv 配置下，真激活 [a0..a7] 落哪个输出 beat？要落 held 末 beat15，
   real 该进 lo-slot(T0) 还是 hi-slot(T1)、word0 还是 word1、identity-layout 逆置换怎么算？（RTL `smi_ds_t.v:64-70/313-317`）
2. **held 末 beat 是否真 8-distinct**：two_word 时间分离后，末 beat 是否 8 个 distinct 激活 lane（compute dot8 需 [a0..a7]
   全 8 lane 同拍）？还是仍某种 pairing？（对照 vec128 magic `[1..8]` 无 doubling 应成立，但 q_dst 是单 token 非 16-beat 流）。
3. **K=8 单 token vs vec128 16-beat 流**：vec128 交付 128（16 beat 全用）；q_dst 只需 1 个 real beat（[a0..a7]）+ 其余 padding。
   two_word（2 tile）对「1 real + 1 padding tile」的 gather/transpose 语义是否 well-defined（padding tile 显式注零 vs 赌零）？
4. **DEBT-042-EMIT-SMT-LAYER 残留**：Opt-A 若成立，emit 广播拍（stream 分支 abs_t）与 SMT `global_t` 仍两条线——只要采样落
   held 末 beat 的宽窗内即吸收（ADR-043 Q4 验收判据：量测 rt_act abs_t 落 beat15 resident 窗的 margin）。须写进验收。

## hco 复核结论 + errata（2026-07-01，APPROVE-with-notes，落码须照此）

hco 独立 RTL 复核裁定 **Opt-A physically realizable、无硬 RTL 事实错误、支点「真激活落 held beat15」成立**，但三条 BLOCK
级订正 + 一项强制验收，落码前钉死：

- **E1（项1 BLOCK，落点精确到 sub=8）**：`smi_ds_t.v:68-70` beat15 恒路由 hi/T1（**与 intlv_out 无关**，比 ADR 表述更
  robust）。但 held beat15 的 lane 映射（VCS 证实，`cross_smc_unit_op.py:382-401 _identity_layout_D`）：`beat15 lane i =
  D[7-i][sub]`，`sub=(23-15)=8` → **held beat15 = 各 SMC 的 word1-元素0（sub=8）、SMC 降序**。故真激活须注到 **`D[smc][8]`
  （word1 首元素）**，不是 word1 末列（word1[7]=sub15 落 beat8、被后 7 拍覆盖 → 垃圾）。q_dst 是 **compute-sink**（beat15 的
  8 lane 对齐 FP16MUL 权重 lane），与 vec128 的 **bank-sink**（bank5 identity）**逆置换目标不同**：q_dst 令 `beat15 lane i =
  a[i]` → `D[7-i][8]=a[i]` → **`D[smc][8]=a[7-smc]`**（对着真实 compute lane 期望算，非 bank5 identity）。
- **E2（项3 BLOCK，padding 显式注零）**：pair 引擎 `ready`（`smi_ds_t_unit.v:305-307`）是**写 beat 计数触发**（`wp==WR_BEATS-1`）、
  **内容无关** → padding tile 内容即便 X 也不 stall、不致 bank5=0（那 bug 根因在 emit 下游，非 padding）。但 gather 读的是
  **UR 源**（非 storage）→ **必须显式写满每 SMC 16 fp16**（`word0[0:8]=0` + `word1[0]=a[7-smc]` + `word1[1:8]=0`），镜像
  vec128 `cross_smc_unit_op.py:414-417`。**只注 word1[0] 不注 word0 → gather 读未初始化 UR = 硅 X**（BLOCK）。
- **E3（项2 BLOCK，验收判据）**：RTL 转置每 beat = 输入 tile 一整列 = 天然 8-distinct（ADR-043 的「B=2 half-split」是 emit
  侧 col-0 single-word 产物、非 RTL）。但 beat15 lane 顺序 = **SMC 降序**（`smi_ds_t.v:316` `(LINE_NUM-1-gi)`）→ 权重 lane
  须对齐、否则**非零但 bit 错** → **验收必须 bank5 逐词 bit-match golden，禁用「捕获非零」放行**（ADR 已写 bit-match，保持）。
- **验收升级（项4，强制 GO/NO-GO）**：Opt-A 单 token q_dst **只有一个 SMI_DS_T session、无下轮** → `data_out` 保持 beat15
  到程序结束（`smi_ds_t.v:198-199` session 结束只清 beat 计数不清 data_out）→ **held 窗无界** → compute 采样 ≥beat15 广播拍
  即命中、极宽窗、比 ADR-042 beat14（held 1 拍）robust。**但 Opt-A 不闭 DEBT-042-EMIT-SMT-LAYER**（emit abs_t 与 SMT
  global_t 仍两线，赌无界 held 窗吸收漂移）→ 验收「改 ur_data/固件大小后相位不崩」**升为强制 GO/NO-GO**：崩 → 相位仍 VCS
  巧合 → **退 Opt-B（emit 广播拍回灌 global_t）**。且 `stream_beats` 必须 **≥16**（覆盖到 beat15），不足则 held 停中段 transient。
- **补充测试（hco 建议）**：加可证伪例——注 `D[smc][8]=marker`、`D[smc][15]=poison`，验采样拿 marker 非 poison（锁死 E1 落点、
  防回退 word1[7]）；`test_real_tile_e2e` 加 ur_data/固件 padding 长度扰动两组（把强制验收落成可跑测试）。

## 验收（升 Accepted）

VCS `dvr_fp16mul_s4567` 在真激活-resident 拍**捕获非零真 [a0..a7]**（非 padding、非 doubling）+ `fp16_matmul_q_dst` bank5
逐词 **bit-match golden**（8/8）；**且改 ur_data/固件大小后相位不崩**（证相位已是 SMT 不变量、非 VCS 巧合）——这是与
ADR-042 脆相位的关键区别。pytest `test_real_tile_e2e` + `test_smi_ds_t_e2e` 转绿或 relax 到 firmware+integrity 层。

## 关联

[[matmul-q-dst-vcs-rootcause-3stream]]（VCS+hco 全过程根因）、ADR-044（vec128 交付范式正本，被本 ADR 移植）、
ADR-043（DEBT-042-EMIT-SMT-LAYER + resident-35 由来 + col-0 自证错）、ADR-042（引擎出数）、
[[derive-timing-not-vcs-calibrate]]（时序从 global_t 推导原则）、[[shiftup-shiftdn-only-dst-datapath]]（shift_up.v:52-58 逐拍覆盖）、
[[adr-040-smt-timing-criterion]]（bank5=0 是 emit 下游 bug、非 SMT 缺边）。
