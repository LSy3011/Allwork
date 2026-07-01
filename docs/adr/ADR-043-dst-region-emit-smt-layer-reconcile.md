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

## path B 隔离验证（2026-07-01，user 选「先只验 DST 交付 128 元素」）
新建 `unit_dst_vec128`（`cross_smc_unit_op.py:UnitDstVec128Operator`，注册 main_verify）：128 元素向量、转置连续存
（`ur_vec_tran_succ`，16 fp16/SMC）→ DST → bank5 全 16 beat，golden=[a0..a127] identity。SAT@II=2、firmware 出、
harness 建（copy unit_ds_transpose_fp16 模板）、VCS 跑通。
🔴 **VCS 实测：DST 现**不**按序交付 128**：bank5 = **a[7..0] 降序、每值 replicated 全 8 lane、B=2 doubled（2 beat/值）、
16 beat 后全零**——即只交付**前 8 个值、降序+复制**（= unit_ds_transpose 的 8×8 单列转置 pattern），非 128 distinct 按序。
→ **`ur_vec_tran_succ` + unit 配置不产生 doc §1195 的按序 128 交付**。DEBT-043-VEC-BEAT-FORMAT 未闭：128 元素的
转置连续 → 8×8-tile → 转置 → 按序输出的**布局映射须设计**（2 tile 各 8×8、B=2 无 doubling、16 beat 128 distinct）。
真值须对 doc §1187/1194-1195 的粒度模型 + VCS 逐 beat 反推正确 layout（下一步：从「a[7..0] 降序复制」反推
当前 gather/transpose 实际读了什么，再设计使 128 distinct 按序落 16 beat）。

## 反推 gather/transpose 实读了什么（2026-07-01，VCS i_crd_shiftdn + bank5 逐 beat 解码）
**结论：DST 现广播 SMC0 的 word0、非跨-SMC 收集 128。**
- **bank5 交付** = SMC0 word0 `[a0..a7]` **降序（a7 先）+ 每值 replicated 全 8 lane + B=2 doubled**，**转置连续/间插两种注入
  bit-identical**（改注入不改 bank5）。
- **replicated 全 lane ⟹ 转置输入 tile 每列恒定**（`M[row][col]=SMC0[col]` 对所有 row）⟹ **gather 把 SMC0 的 word
  广播进全 8 tile 行**（`_build_smi_ds_t_chain` 的 `broadcast=1` gather 语义，ADR-038），**非各 SMC 收 1 distinct 行**。
- 佐证：`i_crd_shiftdn` 在转置连续下 = SMC0 `[a0..a7]` 升序（8 beat）；转置间插下 gather 读变跨-SMC `[a0,a16,..a112]`
  **但 bank5 不变**——因转置级仍广播 SMC0 word。
- **即：128 交付的限制在 gather 的 broadcast 语义（读一个 SMC 的 word 广播），非注入 layout。** 修 128 delivery 须让
  gather **各 SMC 收 distinct 行（broadcast=0 或多-word 收集）** + transpose 产 128 distinct 按序，是**config/机制设计**
  （非注入微调）。DEBT-043-VEC-BEAT-FORMAT 未闭：下一步动 `_build_smi_ds_t_chain` 的 gather broadcast/多-word 语义 + 过 hco。
  ⚠ unit_ds_transpose 同 chain（broadcast=1）却出 distinct——差在 unit 每 SMC 数据**列对齐**、transpose 真 8×8；须对比
  unit 的 gather 为何不 replicate（可能 unit 的注入让每 SMC 数据落不同列）。

## 对比 unit_ds_transpose gather + 文档示例一致性（2026-07-01，VCS 逐 beat 反推）
**文档有两条不同 datapath（关键！）**：
- **向量×矩阵（§1194-1195）**：读回数据**按序恰好是向量、「不需要转置即可发出广播」**（passthrough，无真转置；
  转置间插存储预排成让硬件恒-转置变 no-op）。每拍广播 8 元素。
- **矩阵×矩阵（§1237-1240）**：读回**列对齐**Tile（每拍一列的一部分）→ **激活矩阵排序微指令真转置**为行对齐 → 广播。

**VCS 反推对比**：
- **unit_ds_transpose gather = (SMC0,e0),(SMC1,e0),…,(SMC7,e0)**（各 SMC 的 element0 = **列0 跨 SMC**）→ **列对齐 distinct**
  → 转置产 distinct 输出。**✅ 与文档矩阵×矩阵示例（§1238）一致**——unit 就是矩阵真转置载体。
- **vec128 转置连续 gather = (SMC0,e0..e7)**（全 SMC0 一行）→ **非列对齐**→ 转置广播复制。**❌ 不一致**。
- **根因 = 机制错配（非 layout 微调）**：vec128 是**向量**、该走 §1195 向量 passthrough（转置间插存储 + 无真转置读），
  但我把它路由进了 **unit 的矩阵×矩阵真转置 datapath** → 用错机制。故 128 向量无法按序交付。

**修向（DEBT-043-VEC-BEAT-FORMAT，须过 hco）**：vec128 须走**向量 datapath**——① 激活转置间插存储（预排使硬件转置=no-op）；
② gather 读须产**按序向量**（§1195「读回按序恰好是向量连续数据」，非矩阵列对齐→转置）；③ 确认 RTL SMI_DS_T 对向量
存储是否真出 no-op（doc §1225「转置实际不需要进行操作」= 逻辑 relabel）——即 8×8 转置作用于转置间插向量存储是否
恰好吐按序。这需对着 §1195 的读时序 + 转置间插存储的具体元素排布逐拍推，或直接问 user/RTL「向量存储怎么摆使
恒-转置输出按序」。**当前 unit 载体（矩阵）不能直接复用给向量。**

## 向量转置间插存储怎么摆（2026-07-01，直接据文档 §1070-1099 + §1188-1195 示例）
**结论（doc 权威，无需问 user）**：
- **存储 = 转置间插 B=2（§1073 定义 + §1188 示例明确「间插因子B为2」）**：`Slice_x = Tensor[B·x+B·y·SMC_NUM : …]`。
  B=2/SMC_NUM=8 → 每 SMC 持**元素对轮转**：SMC0=[a0,a1, a16,a17, a32,a33, … a112,a113]、SMC1=[a2,a3, a18,a19,…]、
  … SMC7=[a14,a15,…]。（🔴 我 vec128 用的 `ur_vec_tran_inte(8)` 默认 **B=16**=§1092 typical=**对 128 元素退化成转置连续**，
  从没真跑过 B=2 间插——这是第一个 bug。）
- **读 = 向量读（§1195 passthrough）**：**跨-SMC 按 SMC 序读每 SMC 的一对** → a0,a1|a2,a3|…|a14,a15=a[0..15] 按序，
  下一 y-块 a[16..31]… → 整向量按序、**「不需要转置」**（无排序微指令）。B=2 间插存储正是让「跨-SMC 对读」出按序的原因。
- **与 unit_ds_transpose（矩阵 §1238-1240）本质不同**：矩阵读**列对齐** + 应用**排序(转置)微指令**；向量读**跨-SMC 对读** +
  **无转置**。同一 fabric 上是**两条不同读微指令**。
- **vec128 两 bug**：① 间插因子用 B=16（=转置连续、单-SMC 读）而非示例 B=2；② 走了 unit 的矩阵 datapath（列对齐+转置+
  SMC0 广播）而非向量对读（无转置）。
- **修向**：① 存储改 B=2 element-pair 间插（`ur_vec_tran_inte` 传 B=2 / 或新 helper）；② 读走**向量 datapath（无排序微指令、
  跨-SMC 对读）**——须确认 RTL 能否读+SHIFTUP 广播**不经** SMI_DS_T 转置（doc 向量无排序，但 [[shiftup-shiftdn-only-dst-datapath]]
  smi_top.v:384 SHIFTUP 源硬连 ds_t_data_out → 张力：向量若也必经 SMI_DS_T，则 B=2 间插存储须使 8×8 转置输出恰按序）。
  **这一条 RTL 事实是落码前唯一待核**（向量读是否经 SMI_DS_T），过 hco/RTL；存储 B=2 与向量对读逻辑已由 doc 定死。

## B=2 存储实验结果（2026-07-01，VCS）：转置输出**不按序**，坐实问题在 read datapath 非存储
改存储 B=2（显式 CrossSmcLayout B=2）后 VCS bank5：`a49,a48,a33,a32,a17,a16,a1,a0` 降序 + 全 lane replicated。
解码 = **SMC0 word0（B=2 下 =[a0,a1,a16,a17,a32,a33,a48,a49]）反序 + 复制**——与 B=16 **同款 broadcast-SMC0 行为**，
只是 SMC0 内容变了。**⟹ B=2 存储没修好；输出恒 = SMC0 word 反序复制、与 B=2/16 无关。问题在 read datapath
（DST 链读/广播 SMC0 一个 word），非存储 layout。** 坐实 ADR「vec128 走错 read datapath」：向量须**跨-SMC 对读
（§1195，无转置）**，当前 unit 矩阵链（列对齐+转置+广播 SMC0）给不出。**下一步 = 设计/找向量 read datapath**
（读时各 SMC 贡献自己的对、拼成按序，非广播单 SMC）——须对 RTL 确认向量读微指令（是否经 SMI_DS_T / 如何跨-SMC 拼），过 hco。

## hco RTL 取证：向量读经 SMI_DS_T，跨-SMC 靠 SHIFTDN 时序串行 gather（2026-07-01）
**Q1 向量是否经 SMI_DS_T = 是，无第二条路。** SHIFTUP 广播 payload 唯一硬连 `ds_t_data_out`（`smi_top.v:384`，只 IQ14
拿 128b，其余 `wrd_data=0`）。向量/矩阵共用 `UR读→DVR_SHIFTDN_S→SHIFTDN 链 gather→SMI_DS_T→ds_t_data_out→IQ14
广播`。doc §1195「不需转置」≠「绕过 SMI_DS_T」——是 SMI_DS_T 配成输出已按序（无真转置重排），数据仍穿 smi_ds_t 模块。
**Q2 跨-SMC 怎么拼 = SHIFTDN 菊花链「移动 vld 注入前沿」时序串行 gather**（非单条静态广播）。链 `aiacc_top_f.v:2024-2026`
SMC_k 读 chain[k+1]、出 chain[k]，SMC_0 = 终端喂 SMI（`:1821`）。`shift_down.v:38-50`：**`vld_in=1` → 该 SMC 注入自己的
`dvr_shiftdn_s`、不转发上游；只 `vld_in=0` → 才透传上游**。按序 = vld 前沿每拍下移一跳（1 lat/hop），8 拍依次落
SMC7..SMC0 的 word 填满 SMI_DS_T 一 slot 的 8 行（`smi_ds_t.v:301-317`，WR_BEATS=8）。
**Q3 我载体恒广播 SMC0 = `broadcast=1` 静态保持塌缩到链终端 SMC0（真 bug、非布局）。** `matmul_op.py:204/228` 两 gather
都 `broadcast=1,smc_id_en=cnt-1` → 每个使能 SMC 都注入、**永不进 `:46` 转发** → 终端 SMC_0 独活 → `i_crd_shiftdn` 恒
SMC0 word → SMI_DS_T 8 行全写 SMC0 同 word → 转置吐单元素 replicated（逐字对上 VCS「SMC0 word 反序全 lane 复制」；
反序 = `smi_ds_t.v:304/316`）。与 B=2/16 无关（塌缩在 SHIFTDN 注入级、上游于 UR 布局）。
🔴 **订正 ADR-038**：`matmul_op.py:198-204` 注释「gather 须 broadcast=1」与 RTL **相反**——`broadcast=1` 静态塌缩 SMC0、
`broadcast=0` 静态只匹配 SMC 注入（非匹配保持不转发）**都不给 8-distinct**；跨-SMC 累积**必须靠 vld_in=0 时序转发**。
**Q4 修向**：禁静态 broadcast gather，改**时序串行 gather**——vld 前沿每拍下移一 SMC（`broadcast=0`+逐拍步进 `smc_id_en=k`
或单拍 vld 依赖链转发造前沿），使 `i_crd_shiftdn` 8 拍出 8 distinct SMC word；对齐 SMI_DS_T pair（每 slot 8 beat ready +
issue_cnt==2）。具体以 VCS 波形钉（DEBT-038-B2-GATHER）。
🔴 **hco 存疑（须证伪测试）**：unit_ds_transpose「distinct 列对齐」若也发 broadcast=1，VCS 应同样塌缩 SMC0 → hco 判
矩阵 distinct **大概率 model-A 假绿**（`_tick_smi_ds_t_trigger` 直读各 SMC storage、对 broadcast/smc_id_en 全盲）。
⚠ **但我 VCS 实测 unit i_crd_shiftdn = (SMC0,e0)..(SMC7,e0) distinct**（moving-front）——与 hco 判断张力；须
**证伪测试**（8 SMC 各灌不同 magic、断言 crd_shiftdn_chain[0] 8 拍 8-distinct）对 unit + vec128 都跑，钉死谁真谁假。
连带风险：本会话「matmul 引擎出数」若 gather 也塌缩，则 compute 读的激活可能也非真跨-SMC（VCS data_out_vld 拉高≠数据对）。

## 证伪测试结论（2026-07-01，magic 每 SMC 灌常量 s+1，VCS）：gather 两者都 OK，collapse 在下游时序
`AIACC_GATHER_MAGIC=1`（`cross_smc_unit_op._inject_gather_magic`）每 SMC_s 源灌常量 (s+1)，VCS 解码：
| | gather `i_crd_shiftdn` | 输出 bank5 |
|---|---|---|
| **unit** | SMC **1..8 distinct**（cyc 1460,1464,..1476 紧凑 2-apart）| `[4,4,3,3,2,2,1,1]`/`[8,8,7,7,6,6,5,5]`——**全 8 SMC 在**✅ |
| **vec128** | SMC **1..8 distinct**（但 cyc 1460 后 **32 拍 gap** 到 1492,1494..1504）| **全 beat = SMC0(1)**❌ 塌缩 |

**结论（订正 hco Q3）**：
- 🔴 **hco「broadcast=1 静态塌缩 gather 到 SMC0」被证伪**——两者 gather `i_crd_shiftdn` 都 8-distinct（vld 是**移动前沿**、
  非静态保持；emit 实际产前沿）。hco 从 `shift_down.v:38-50` 推的「静态塌缩」前提与实际 emit 不符。
- ✅ **unit VCS 输出全 8 SMC distinct → unit 非 model-A 假绿**（hco 存疑亦证伪）；连带**本会话 matmul 引擎 gather VCS-real、不塌缩**，前述 DST 工作站得住。
- 🔴 **vec128 collapse = gather 时序 bug（非 broadcast、非存储、非机制）**：SMC0 beat 被 **32 拍 gap 孤立**（1460 独在、SMC1-7 挤在 1492-1504）→
  SMI_DS_T slot 收集窗只捕到 SMC0 那 1 拍、重复填 8 行 → 输出 SMC0 replicated。unit beats 紧凑 2-apart → 8 拍全落窗内 → 全 SMC。
- **修向（精确化）**：修 vec128 gather 的 **SMC0-孤立 32 拍 gap**（让 8 SMC beat 紧凑落进 SMI_DS_T 收集窗）。根源疑在 vec128 的
  `lanes=ROWS=16`（vs unit 8）或 2-tile（128=2×64）emit——不是 gather broadcast 语义、不是存储 B、不是 datapath 机制。
  DEBT-043-VEC-BEAT-FORMAT 收窄为**单点时序 bug**：SMC0 beat 为何早 32 拍。magic 测试工具（env-gated）留作回归证伪。

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
