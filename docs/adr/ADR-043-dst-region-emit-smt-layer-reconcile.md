---
adr_id: ADR-043
title: DST datapath 的 emit/SMT 双层 reconcile —— 让 broadcast→compute 相位成为 SMT 建模的不变量（非 VCS-fragile）
decided: 2026-07-01
last_verified: 2026-07-01
still_holds: proposed（问题已 VCS+hco 定位；方案 hco APPROVE-可实现带 3 BLOCK-条件已解，待实现 + VCS 验证）
builds_on: ADR-041, ADR-042
implements: ADR-042 DEBT-042-EMIT-SMT-LAYER
partial_supersedes: ADR-041 D2（对 resident-sampling 边：WINDOW 进 RAW latency；streaming-to-bank 边仍 19，见 D1）
hco_review: APPROVE-可实现（2026-07-01，Opt-A 原理上足以成 SMT 不变量、Opt-B 非必需仅 VCS 兜底；3 BLOCK 已解，下方 D1'/D2'/D4）
---

# ADR-043 · DST datapath emit/SMT 双层 reconcile

> **状态：proposed**（问题闭合、方案待 hco + VCS）。
>
> **一句话**：`fp16_matmul_q_dst` 的 SMI_DS_T 引擎已在 VCS 出数（ADR-042，commit b3753b7），但 bank5 仍 0 的**真根**
> 不是「VCS 逐拍调 3 个参数」，而是 **DST datapath 的三条流（gather / CRI pair / 16-beat broadcast）emit 时刻脱离 SMT
> t_vars 模型** → compute 依 SMT 模型排程、却在**另一个时间线**采样 broadcast → 相位只靠 VCS 时序巧合成立、脆（任何
> ur_data/固件改动都崩，ADR-042 实证）。修 = 让 broadcast→compute 相位成为 **SMT 建模的不变量**。

## Context（层断裂的具体证据）

commit b3753b7 的 q_dst schedule（`compiler_schedule.json` t_var vs `physical_instructions.json` emit abs_t）：

| node | SMT t_var | emit abs_t | 说明 |
|---|---|---|---|
| ds_gather | 20 | 20 | 标准 lowering，一致 |
| ds_transpose | 22 | 24 | iq_II=2 block，近似一致 |
| **shiftup_a_read** | **41** | **0** | 🔴 stream_beats 分支在 abs_t 0 发 16 拍、**完全无视 t_var=41** |
| rt_act (compute 采样) | 48 | 50 | 依 SMT 模型（shiftup t_var 41 + resident 窗）排 |

**三个不一致的时间线，同一个 broadcast**：
1. **SMT 模型**：shiftup 在 t_var=41（transpose 22 + `SMI_DS_T`=19），compute rt_act 在 [42, 51] resident 窗采样。
2. **emit**：shiftup 16-beat 流在 abs_t 0–15（stream 分支 `assembler.py:88-124`，base_abs=lead_delay+1，无视 l_init）。
3. **runtime（VCS）**：RTL SHIFTUP 具 EXTRA_DATA（`smi_top.v:370/384`）→ 逐指令等 WRD(data_out) 就绪才 pop → broadcast
   **实际跟 data_out 窗口走**（转置出数后 16 拍），与上两者都不同。activation（element 0 → 转置降序 beat 14）落 data_out
   窗尾、dr_shiftup_d resident ~transpose_CRI+42。

compute 按 SMT 模型（1）排在 rt_act~t_var48，但真 activation 按 runtime（3）在 transpose+42 才到 → **早 ~20 拍采空 → 乘零
→ bank5=0**。SMT 模型（`SMI_DS_T`=19，只到**首 beat**）**没建模 16-beat 流到 activation beat 的真实 latency**。

> hco review-2（ADR-042）判定此为 root（DEBT-042-EMIT-SMT-LAYER）：「在 axiom pin latch 改的是 t_vars、动不了
> stream emit 层 → 治标不治本」。ADR-042 实证注 tile2 零 → ur_data 变大 → TB AXI 时序漂 +11 → 相位崩，坐实脆。

## Decision（方案，待 hco）

**核心原则**：broadcast→compute 相位必须由 **SMT 边权 + emit lowering 一致地建模**，使「compute 采样拍 == activation
在 dr_shiftup_d resident 的拍」成为求解出的不变量，而非 VCS 巧合。三个子决策：

### D1. compute-consuming 路径的 SMI_DS_T latency = 到 **resident LAST_BEAT**，非首 beat〔核心〕
现 `SMI_DS_T`=19 是**首 beat 就绪**（collect16+drain3）。但 compute 采样的是**驻留值 = 流的最后一拍**（`shift_up.v:52-58`
每广播拍覆盖，只末拍 held 到下次广播——hco Q3）。故 **compute-consuming 的 transpose→shiftup 边 latency 应 = 首 beat +
WINDOW = `SMI_DS_T_FIRST_BEAT(19)` + `SMI_DS_T_WINDOW(16)` ≈ 35**（到末 beat 就绪）。
- **q_dst-specific / dst-datapath-compute-specific**，非全局改 `SMI_DS_T`=19（`unit_ds_transpose` 无 compute、直写 bank5 全
  16 beat，仍用 19 + STREAM_LEAD_DELAY，勿动）。门控：consumer 是 dst broadcast 的 compute-side（有 dst_datapath_value +
  下游是 FP16MUL 路），非 unit 的 UR_WRITE-side。
- **axiom + counter-verifier 必同步**（`dependency_axioms.py:52-61` RAW 边 + `ucode_solver.py:249/300` 维度4/6 区间）——
  否则 `t_c >= t+lat+II` 触发 overwrite fail（ADR-006/021 类 desync）。

### D2. activation 排到 resident LAST_BEAT〔hco Q3〕
把 activation 布局到 **beat 15（LAST_BEAT，`smi_ds_t.v:58`）**——流末拍是唯一 held-到-下次-广播的驻留值 → 现有 RTOVR
resident 窗（`dependency_axioms.py:128-133`，II-1）直接够、免额外 extend。哪种 element 布局 → beat 15 需 VCS 逐拍定
（B=2 intlv 值序；element 0 现落 beat 14，差 1，可能就是 pairing 的低/高半）。

### D3. tile2 显式注零 + 与相位一起落〔hco BLOCK-2 / DEBT-042-DISTINCT-TILE〕
padding tile2 显式注零（硅 X 风险），但注零改 ur_data 大小 → TB 时序漂 → **必须在 D1/D2 把相位变成 SMT 不变量之后**
一起落（否则再次崩脆相位）。相位一旦是 SMT 建模的不变量，ur_data 大小变化由 SMT 重排吸收、不再崩。

### hco 落码门控（2026-07-01，3 BLOCK 已解 + 2 订正，实现须照此）
- **D1'（BLOCK-1 gating）**：D1 的 q_dst-specific 门控**不能用 `dst_datapath_value`**——unit（`cross_smc_unit_op.py:190/193/197`）
  与 q_dst（`matmul_op.py:1549`）都设它、会误伤。须在 q_dst 注入点加**独立 marker `dst_compute_resident=True`**，D1 边权只
  门控这个新 flag；**不**用「下游是否 FP16MUL」图推断（fp32 q_dst 下游是 FP32ADDTREE5TO1、会漏）。unit 不设 → 自然留 19。
- **D1''（BLOCK-2 verifier override）**：`ucode_solver.py:232` 的 `lat=delays.get(res_type)` **无 per-edge override 钩子**；
  axiom 用 35、verifier 用 19 → 维度6 overwrite（`:249` `t_c>=t+lat+II`）误杀 → Synthesis failed。须在 `ucode_solver.py:232`
  **和** `dependency_axioms.py:37` 两处都加「consumer 带 `dst_compute_resident` → lat = `SMI_DS_T_RESIDENT`」的一致取值。
- **D4（BLOCK-3 常量单点）**：把 `SMI_DS_T_RESIDENT = SMI_DS_T(19) + SMI_DS_T_WINDOW(16) = 35` 落成 `pipeline_delays.yaml`
  **受控常量**（单点定义、axiom+verifier 共读），并在 yaml 注释显式声明「对 resident-sampling 边 partial-supersede
  ADR-041 D2『WINDOW 不进 RAW latency』」（该结论对 streaming-to-bank 的 unit 仍成立）。35 = (WINDOW−1) + shiftup reg +1
  的相消（`shift_up.v:54` +1 / `smi_ds_t.v:58` LAST_BEAT=15），非「+整窗」；VCS 校 ±1~2。
- **D2' 订正（beat15 = 跨 slot、非 index+1）**：`smi_ds_t.v:68-70` intlv_out=1 下 beat14[0]=0→**lo slot**、beat15[0]=1→**hi slot**。
  故「activation 放 beat15」= **把 element-0 路由到 hi-slot gather（`ds_gather_1`，`matmul_op.py:223`）**，是真实数据路由改动、
  非布局参数微调。DEBT-043-BEAT15-LAYOUT 按此写。
- **Q4 残余风险 → 验收判据**：rt_act 在 compute-IQ（`iq_II=II`）abs_t 被模运算量化到 II 栅格、ds_transpose 在 iq_II=2 IQ →
  t_var 差 35 ≠ abs_t 差 35。只要量化 slack 落在 beat15 resident 窗（≈II）内即吸收。**验收须量测 rt_act abs_t 落在 beat15
  resident 窗内的 margin**——若超窗（compute 落流中段）才上 Opt-B。**别事后猜，把 margin 量测写进验收。**

## Options considered
- **Opt-A（选，= D1+D2）**：建模到 resident 末 beat 的 latency + activation 排驻留拍。最小改动闭合相位、不碰 stream emit 结构。
- **Opt-B（备，hco 早期 endorse）**：把 CRI pair 建成**两个显式 SMI_DS_T DFG 节点**（ds_transpose/_1，同构 ds_gather/_1）让
  SMT 自然排相邻 + 整个 DST region 当**自带局部 II 的子图**由 SMT 建模（非 assembler 事后 iq_II=2 重 bucket）。更彻底、
  但改 ucode_solver 子图调度、工作量大。Opt-A 若 VCS 验不过（相位仍脆）再上 Opt-B。
- **Opt-C（拒）**：compute 等 data_out_vld 的 COND 握手——`shift_up.v` 对 RTOVR 无 ready/valid 握手，硬件层无可等信号
  （hco 判定不可实现）。V1.0 静态 DLY 排程（[[aiacc-hardware-timing-model]]）。

## 实现进度（2026-07-01：D1/D4 落地 + VCS 验证，相位 reconcile 成功；剩 activation 数据格式）
D4（`pipeline_delays.yaml` SMI_DS_T_RESIDENT=35）+ D1'（q_dst SHIFTUP 加 `dst_compute_resident` marker）+ D1''
（`dependency_axioms.py` + `ucode_solver.py` 两处按 marker 取 35）落地。**SMT SAT-verified @II=17**（无 desync）；
schedule 实测 shiftup=transpose+35、rt_act=t77（was 48）。**回归干净**：fp32 `matmul_q_dst` 仍 8/8 bit-perfect（marker
隔离住、留 19）、pytest 183 pass（仅 2 q_dst WIP 红）。
✅ **VCS 验证相位 reconcile 成功**：`dvr_fp16mul_s4567`（compute 激活输入）**现捕获非零广播值 @cyc6150**（D1 前恒空）
= data_out held 末 beat（beat15）——**compute 终于采到 SMI_DS_T 广播的驻留值**，D1 latency 修奏效。
🔴 **仍 bank5=0，剩 activation 数据格式（非相位）**：捕获值 = `[-100,-200,...]` **垃圾**，两因：
1. **beat15 = hi slot = tile2 = 未注入 UR = 硅上垃圾**（坐实 hco BLOCK-2）→ D2' 须把 activation 路由到 hi-slot tile2 + 显式注入。
2. **beat 是 B=2 half-split**（VCS beat14 lo = `[4.234,4.234,-0.033,-0.033,...]` = 4 distinct 成对，非 8 lane [a0..a7]）——
   一个 beat 只携半行(4 SMC)、compute 需 8 lane [a0..a7]。**col-0 8×8 呈现 + compute 读单 beat 无法交付 8 lane 激活**。
   这是 K=8 向量经 B=2-paired SMI_DS_T 到 compute 8-lane 的**语义映射问题**，非相位——须 RTL/doc（表79 vector×matrix
   激活格式）厘清「向量该怎么摆进 8×8 使转置某 beat = [a0..a7] 8 distinct」。DEBT-043-VEC-BEAT-FORMAT。

## DEBT-043-VEC-BEAT-FORMAT 厘清（2026-07-01，doc 表78/79 + §1187/1194-1195）
**向量激活怎么摆进 8×8 的答案**：doc 的向量×矩阵机制是给 **K=128（基础粒度）** 的，非 K=8：
- §1187：`基础计算粒度 = B*(UR_BYTE_CNT/ACT_BYTE)*SMC_NUM = 2*8*8 = 128` 元素向量，广播、与各 SMC 权重列点积。
- **§1195（关键）**：激活存 **转置连续/间插**，B=2 读回**按顺序恰好是向量连续数据、「不需要进行转置即可发出广播」**——
  存储**预排**成让硬件恒-8×8-转置变 passthrough、吐出**按序**向量。每拍广播 `UR_BYTE/ACT=8` fp16。16 beat×8=128 元素。
  compute **流式扫 16 beat 累加** 128 元素点积（非读单 beat）。§1194：读耗 `SMC_NUM*2=16` 拍、**一半是气泡**。
- **表78**：小向量（K< `UR_BYTE/ELEM*SMC_NUM`=64）用 **转置连续**（`ur_vec_tran_succ`，`cross_smc_layout.py:95` chunk=1
  → **每 SMC 1 元素**）；大向量用转置间插（多 SMC）。
- 🔴 **本会话 D2 的 col-0 8×8 layout 错**：正确 = 转置连续（`ur_vec_tran_succ`，= q_dst **原始**代码，被 D2 替掉）。
- 🔴 **对 q_dst K=8（玩具、低于粒度）的启示**：转置连续 1 元素/SMC，B=2 读 → [a0..a7] 在**真数据 beat**（按序）+
  其余气泡。**compute 应读真数据 beat（in-order），非 held 末 beat（K=8 时末 beat 是气泡）** → D1「采驻留末 beat」
  对 K=8 玩具是错目标（对真 K=128 流式才读全 beat）。**修向**：① inject 回 `ur_vec_tran_succ`；② compute 采真数据
  beat（VCS 定位 [a0..a7] 落哪拍）；③ 或直接把 q_dst 测试维度提到 K=64/128 让机制自然成立（更贴 doc、避免玩具边界）。
  D1/D4 的相位-invariant 机制**仍对**（compute 采样拍锚 ds_transpose、是 SMT 不变量），只是「采哪个 beat」的目标要改。

## Consequences / Debts
- **DEBT-043-RESIDENT-LAT-VCS**：`SMI_DS_T_FIRST_BEAT + WINDOW ≈ 35` 是推算，真值（含 broadcast 跨-SMC 传播）须 VCS
  逐拍标定（transpose_CRI → activation-at-dr_shiftup_d）。落进 `pipeline_delays.yaml` 成受控常量（连同 `SMI_DS_T_WINDOW`）。
- **DEBT-043-BEAT15-LAYOUT**：哪种 element/intlv 布局把 activation 映到 beat 15，VCS 定。
- 波及 `_build_smi_ds_t_chain` / dependency_axioms / ucode_solver / q_dst inject —— 全过 hco（SMT + 硬件契约）。
- **验收（升 Accepted）**：VCS `dvr_fp16mul_s4567` 在 activation-resident 拍捕获非零 + bank5 逐词 bit-match golden；
  且**改 ur_data/固件大小后相位不崩**（证相位已是 SMT 不变量、非 VCS 巧合）——这是与 ADR-042 脆相位的关键区别。

## 关联
[[matmul-q-dst-vcs-rootcause-3stream]]（VCS+hco 全过程）、ADR-042（三流密集 region + 引擎出数 + DEBT-042-EMIT-SMT-LAYER）、
[[shiftup-shiftdn-only-dst-datapath]]（shift_up.v:52-58 逐拍覆盖 + smi_top.v:384 EXTRA_DATA）、ADR-041（首 beat/WINDOW 拆分由来）。
