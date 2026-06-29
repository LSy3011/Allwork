# AIACC 指令集架构 v0.4.19 — 文档理解 + 分歧台账

> **目的**：把权威设计文档 `AIACC_指令集架构_v0.4.19.docx` 系统性读透一遍，产出（A）忠实架构图谱
> 与（B）「文档权威 vs 我们 ADR/代码」的分歧台账。起因：我们的 ADR 是沿 user 历次零散给的表
> 长出来的，从未对完整文档做过一次对齐 —— [ADR-026 把 matmul 激活转置接到了错误的 DS 模块](../adr/ADR-026-matmul-q-matrix-matrix-ds-transpose.md)，
> 很可能不是孤例。本文是「追上文档」的基线。
>
> **来源**：`AIACC_指令集架构_v0.4.19.docx`（451 标题 / 115 表 / 1336 段）。提取工具
> `verification_v2/tools/spec_doc_align.py`（升版复跑）；本次另用全量提取
> `/tmp/aiacc_v0.4.19_full.txt`（document-order，含表格逐行）。
> **范围**：V1.0 支持部分（跳过标「V1.0不支持/待完善」的 LSU/MID/卷积/MAC-matmul/量化）。
> **方法**：7 个 subagent 分子系统忠实摘要 → 主 agent 合成 + 对照 ADR 建台账。
> **日期**：2026-06-05。引用记法 `[seq N]`=提取文件段号，`表NNN`=文档表号。
> **状态**：理解快照（非决策）。台账每条标「文档证据 / 我们现状 / 分歧性质 / 建议」。

---

## Part A · 忠实架构图谱（V1.0）

### A0. 系统顶层 —— 单 SMI 全局指令流（文档明证）

- **一个 MID + 一个 SMI + 多个 SMC + LSU**（逐字「一个…一个…多个」）`[seq 35]`。印证
  [ADR-022](../adr/ADR-022-single-smi-global-instruction-stream-model.md) 单 SMI 模型 —— **这是文档级证据，不是推断**。
- **SMC_ID 由 SMI 经 shift 链初始化**：软件向 SMI 控制寄存器 `0x8 smc.smc_id` 连续写
  `PARAM_SMC_NUM` 个值，从离 SMI 最远的 SMC 到最近的，以 shift 方式注入各 SMC 的 SMC_ID 寄存器 `[seq 928, 表591]`。
- **指令沿 CRU 链向北广播、CRD 链向南回收**`[seq 91-101]`：
  `SMC_ID==0: CRU=pop(command_queue)`；`SMC_ID<=smc_id_en: CRU[i]=CRU[i-1]`（逐级复制）；
  `SMC_ID==smc_id_en: CRD=CRU`（链顶折返）；`SMC_ID<smc_id_en: CRD[i-1]=CRD[i]`（向南回传）。
- **典型规格**`[表51]`：FP16 向量单元 32×8=256；FP32 向量 32×4=128；FP16 矩阵乘 32×32=1024；
  核内存储 1MB；**SMC 数量 = 32**（`PARAM_SMC_NUM=32`，`PARAM_SMC_AXI_EN=1`）`[表53]`。
  （项目 sim 用缩放值 8，见台账 D-02。）

### A1. SMC 计算单元（SMC 微架构）

> 所有计算单元都有 `vld`(1, bit0) + `smc_id_en`(5)；`smc_id_en >= SMC_ID` 则该 SMC 的本单元被使能
> （印证 [ADR-031](../adr/ADR-031-smc-id-en-all-units-unify.md) 全单元统一）。**唯一例外 = RtoVR（无 smc_id_en，见 A2）**。

| 单元 | CNT | 精度 | 关键微指令字段 | 累加/语义要点 | 出处 |
|---|---|---|---|---|---|
| INTADD | 4×32b 或 32×8b | 4/8/16/32b(8/16 暂不支持) | src_*_prec/sign, dst_sign, add_or_sub, up_stat_en | 4bit+8bit 或 32bit+32bit 两模式；32bit 模式更新 ST(GT/EQ/LS×4 组) | 表54-56 |
| INT8toFP16 | 32 | int8→fp16 | src_sign | 纯类型转换 | 表57-59 |
| **FP16MUL** | **32** | fp16 | CRU_FP16MUL{,_S0123,_S4567}；src_0/1_id, src3210_high_id, src7654_high/low_id | **S0-S3 来自广播、S4-S7 来自本地**；S4567 共用单 DVR 端口；`D[x]=mul(S0_*[x],S4_*[x])`… 即广播×本地成对乘 | 表510-514 |
| **FP32ADDTREE5TO1** | **8** | fp32 | **`accumulate`(1)** | 5入=4 源+1 目的累加值；**无 reset 信号，`accumulate=0` 即清零**；8 个 per-lane 独立累加通道(ACC_CHANNEL) | 表515-517 |
| **FP32ADDTREE8TO1** | **1** | fp32 | **`dst_id`(2)，无 accumulate** | 8入1出 = S0 四组+S1 四组求和；**本单元无累加字段**(跨 tile 累加不能靠它自身) | 表518-520 |
| FPMUL | 4(32b)/8(16b) | 16/32b | src_prec, dst_prec | element-wise 乘，无累加 | 表521-523 |
| FPADD | 4(32b)/8(16b) | 16/32b | src/dst_prec, add_or_sub, up_stat_en | 更新 ST(8 组×3bit) | 表524-526 |
| FPtoFP / INTtoINT / FPtoINT / INTtoFP | 4 | 16↔32 | src/dst_prec, *_high_or_low；**INTtoINT 有 src+dst_sign / INTtoFP 有 src_sign / FPtoFP·FPtoINT 无 sign** | 纯精度/类型转换 | 表527-538 |
| LOGIC | 4 | 16/32b | **`inst_typ`(4bit, 16 op)**, prec | Copy/Not/And/Or/Xor/Select_GT/EQ/LS/6 种 shift/Get_first_one/zero；双源 S0/S1 + 状态源 ST | 表539-541 |

- **未给数值**：文档 SMC 微架构段**未列任何单元的 latency/throughput 周期数**（设计参数表只给数量/精度/子字并行）。
  我们 spec/ADR 里的 latency（如 ADDTREE8TO1=3）来源不在此章节，需 pipeline_delays.yaml 或别处佐证。

### A2. SMC 数据通路单元

**SHIFTUP（上行广播）** `[表542-544, seq561-567]` —— 跨-SMC 广播权威定义，逐字伪代码：
```
if SMC_ID<=smc_id_en:           SMC[SMC_ID]/CRU_SHIFTUP_O = CRU_SHIFTUP_I        # 指令沿链向北传
if broadcast==0 && SMC_ID==smc_id_en:  DR_SHIFTUP_D = CRU_SHIFTUP_O[data]        # 点写：单 SMC
if broadcast==1 && SMC_ID<=smc_id_en:  DR_SHIFTUP_D = CRU_SHIFTUP_O[data]        # 广播：SMC_0..smc_id_en 全得同一 data
```
- ⚠ **`smc_id_en` 语义在 SHIFTUP 与计算单元不同**（文档显式提示「与一般计算模块的定义有差别」）：
  广播时是范围上界；**不广播时 `smc_id_en` 是目标 UR 的 SMC ID**（不是 N-1）。见台账 D-04。

**SHIFTDN（下行收集）** `[表545-548, seq586-594]`：
```
if broadcast==0 && SMC_ID==smc_id_en:  CRD_SHIFTDN_O[128:1]=DVR_SHIFTDN_S; [0]=1
if broadcast==1 && SMC_ID<=smc_id_en:  CRD_SHIFTDN_O[128:1]=DVR_SHIFTDN_S; [0]=1
if CRD_SHIFTDN_I[0]==1:                 CRD_SHIFTDN_O = CRD_SHIFTDN_I              # 链式整体转发
```
- **全程无加法**：只有覆盖式装载 + 链式整体转发 → 印证 [ADR-025](../adr/ADR-025-matmul-parallel-n-broadcast-addtree.md)
  「SHIFTDN 仅搬运/拼接、无 sum-reduce」。SMI_DS_T 路径上的 SHIFTDN 被文档归为「数据信号，与 outstanding 无关」`[seq994]`。

**UR（User Register）** `[表561-563]`：8 bank（`PARAM_UR_BANK_BIT_NUM=3`）× 256 addr（`_ADDR_BIT_NUM=8`）×
16 字节（`PARAM_UR_BYTE_CNT=16`）。4 读口 + 4 写口连 RtoVR，1 异步口连 LDB。读写双驱动源（CRU 指令 / STB·LDB），
无反压、靠编译器避冲突。地址寄存器位宽 = bank(3)+addr(8) = 11bit。

**STB / LDB（Burst Store/Load）** `[表549-560]`：UR↔外部地址 burst（第 n 个 SMC 起点
`gr_base_addr + gr_intlv_addr*n`）。各拆 STB+STBAXI / LDB+LDBAXI 两条解耦指令，中间 AXI Stream，
`PARAM_OUTSTANDING_INST_NUM=4`。**UR→UR 搬运数据流 = STB 读 UR → DS 整理 → LDB**`[seq627/665]`；
`tid`(2) 选「是否经 DS」+「去 LDB 还是 STBAXI」。最后一个 SMC 以 `SMC_ID 全 1` 判定。

**RtoVR（DR/UR → DVR/UVR 选通矩阵）** `[表564-566, seq755]`：把 34 路输入（各计算单元 D 回读 +
UR_RDATA_0..3）选到 39 路输出（各计算单元 DVR/UVR S 输入 + DVR_VIN/VOUT/SHIFTDN_S + UVR_WDATA）。
每输出一个 `mv_out*`(6bit 选源) + `OUT_FROM_IN_EN_*`(64bit 位图门控)。**纯选通无计算 →「不使用 SMI_ID」**
（无 SMC_ID/smc_id_en，是 ADR-031 唯一例外的文档依据）。

**VIN0/VIN1/VOUT0/MIN0/MIN1/MOUT0** `[表567-584]`：6 个同构 128bit 单级流水寄存器（PR），每-SMC 例化，
`vld`+`smc_id_en`，仅做 `DR_*_D = DVR_*_S` 搬运。向量路 V*、矩阵路 M*。**我们当前 sim/IR 未建模这些 PR**（见 D-08）。

**DS（Data Shuffle）** `[表585-588, seq869-900]` —— ⚠ **注意：这不是 matmul 用的转置引擎**：
- **每-SMC 模块**（有 `SMC_ID`(5)），坐落 STB↔LDB 的 AXI Stream 通道（4 组 AXISM Master/Slave × STB/LDB）。
- `PARAM_TRANSPOSE_SIZE=32`，**`PARAM_TRANPOSE_SRC=STB的UR读数据`、`PARAM_TRANPOSE_DST=STB的UR写数据`**，
  **「当前设计仅支持 UR 到 UR 的 transpose」**`[seq888]` → **单 SMC 内 UR→UR**，不跨 SMC。
- 上行字段：`op_type`(1, reshape/transpose), `data_type`(2), `r_in/out_cnt`(8), `t_dim0/1_cnt`(5, PyTorch transpose 语义),
  `inst_id`(4), `smc_id_en`(5)；下行 `done`/`inst_id`。`PARAM_OUTSTANDING_INST_NUM=4`。
- **用途 = 写回 DRAM / store-buffer 本地 reshape**`[seq1112]`，**不是** matmul 激活的跨-SMC 广播转置。

### A3. SMI 架构与指令机制

**SMI** `[表589-592]`：单实例全局指令单元。AXI Slave 入口（数据 128b/地址 32b）→ GIF → 控制寄存器 +
指令寄存器（偏移 0x1000，众多 SMI_IQ 的 SRAM 映射）。输出 `CRU_O` 大 bus，每组子信号连一个 SMI_IQ；
输入 `CRD={SHIFTDN,STB,STBAXI,LDB,LDBAXI,DS}`，每组连一个 SMI-IS。

**启动/完成流程** `[seq933-944]`：写指令 → 顺序写 `smi_iq_en*` → `smi_iq_sid` → `smi_iq_start`。
胶水逻辑维护 RDI_START/RDI_DONE 队列（深度各 4）；所有 SMI_IQ 空闲时发 RDI_START+RDI_SID；
当某 sid 下所有使能 SMI_IQ 都返回 RDI_DONE/RDI_EID → `smi_iq_done=1` 产生中断。

**SMI_IQ（指令队列）** `[表593-595]`：
- 微指令字段 `VLD/JUMP/EMBED/EMBED_END/LOOPS/COND/DLY/INC/INC_EMBED/CODE`。
  `INSTQ_DEPTH=32`，`OUTSTANDING_INST_NUM` ∈ {0,4}。
- **一个 SMI_IQ 一般控一个 SMC 模块；UR 需 8 个 SMI_IQ（W0~W3+R0~R3）；RtoVR 需 64 个 SMI_IQ**`[seq967-969]`。
- **`COND` vs `DLY`**`[seq989-990]`：DLY 调短暂时序差；**长/不定延迟用 COND**。逐字「**做矩阵乘法时从 UR 读取
  需要广播的数据往往对应几十个时钟周期…让需读广播数据的 SMI_IQ 先行执行，其他模块用 COND 等待广播数据
  取回、数据整理完毕、进入对应 SMI_IQ 数据队列，再同步执行**」—— matmul 广播时序的权威机制（见 D-07）。
- outstanding：SMI 例化 5 个 OUTSTANDING≠0 的 SMI_IQ，分别连 STB/STBAXI/LDB/LDBAXI/DS 的 CRD。

### A4. SMI_DS_T（Data Shuffle_Transpose）—— matmul 数据整理的真 datapath

> **结论：matmul 激活的跨-SMC 转置 = SMI_DS_T，不是 DS。** 这是本次理解任务最重要的纠正（台账 D-01）。

- **接口** `[表596]`：输入 `CRD_SHIFTDN`(129) ← 跨-SMC gather；输出 `DATA_OUT`(128) → SHIFTUP；`CRI` 指令；`INST_DONE`。
- **设计参数** `[表597]`：`PARAM_IN_LINE_BYTE_CNT=PARAM_UR_BYTE_CNT`，`PARAM_IN_LINE_NUM=PARAM_SMC_NUM`，`PARAM_INST_NUM=4`。
- **微指令** `[表598]`：`vld` + `intlv_en/intlv_grp/intlv_in/intlv_out`（4 个并发指令、两两间插）。
- **功能** `[seq1031-1042]`：
  1. 「对所有 SMC 进行 UR 读，读取之后通过 **SHIFTDN 传回给 SMI**」，每 SMC 一行 `PARAM_UR_BYTE_CNT` 字节、共 `PARAM_SMC_NUM` 行；
  2. 「一个 SMI_DS_T 指令将接收上述数据 **Tile 转置为 `PARAM_UR_BYTE_CNT` 行、`PARAM_SMC_NUM` 列的 Tile**」；
  3. 输出「送到用于 **SHIFTUP** 对应的 SMI_IQ（该 SMI_IQ 具 EXTRA_DATA）」`[seq1050]`。
- **逐字总纲** `[seq1051]`：**「通过 UR read —— SHIFTDN —— SMI_DS_T —— SHIFTUP —— UR write 形成了数据整理的完整 datapath。」**
- V1.0 仅「从 SMC 读、写到 SMC」；SMC↔DRAM 三种组合待 LSU 完善。
- **SMI_DS_T 是一等指令驱动硬件单元**（2026-06-10 user 订正，台账 D-13）：和 UR/FPMUL 一样有指令输入
  （`CRI`，表596）+ 微指令格式（表598）+ 输出寄存器 + 完成信号（`INST_DONE`）。位于「SMI 微架构和微指令」，
  是 SMI_IQ 的同级单元。**微指令瘦**（仅 vld+intlv_*，无 smc_id_en/addr/dim）因转置维度由设计参数固定、
  寻址由周边 UR_READ/SHIFTDN/SHIFTUP 指令外置 —— 它只转置 `CRD_SHIFTDN` 进来的 tile。**完整 datapath =
  5 条协同指令的流水**，SMI_DS_T 是其中一个指令节点。⚠ [ADR-033](../adr/ADR-033-smi-ds-t-cross-smc-transpose-datapath.md)
  Stage-A 把它建成 `simulation_only`（sim_config 驱动、绕开指令流）是 model-A 捷径；升为真指令驱动单元
  见 [ADR-034](../adr/ADR-034-smi-ds-t-instruction-driven-unit.md)（milestone a）。

### A5. Tensor 存储模型

- **间插展开 / 连续展开**`[seq1069-1075]`：D3 维在 Sub_UR（即跨 SMC）上展开。**间插因子 B** = 每个 SMC 连续存多少个
  D3=1 切片，典型 1/2/`2*PARAM_UR_BYTE_CNT/ELEMENT_BYTE_NUM`。
- **存储格式命名**`[seq1090-1099]`：1D `UR_Vec_{Norm,Tran_Succ,Tran_Inte}_D3inner`；2D `UR_Mat_{Norm,Tran_Succ,Tran_Inte}`。
  - ⚠ 文档原文「非转置间插」与「转置间插」**同记为 `UR_Mat_Tran_Inte`**（命名冲突，疑笔误，照录）。
  - 广播维度 stride 可为 0。`Dx_stride` 支持正/负/零。
- 外部地址空间格式 `GR_Mat_{Norm,Tran}` 等。CNN 4D（`UR_Act_*`/`UR_Ker_*`）V1.0 不支持。
- 数据类型 V1.0：fp32/fp16/bf16/uint·int{4,8,16,32}/bool（Y）；fp64/complex/fp8/fp4/int64（N，下一版加 FP8）。

### A6. matmul 微指令编程（加法树）

**核心区分 —— 这解释了 SMI_DS_T 为何存在**：

| | vector × matrix `[seq1196-1203]` | matrix × matrix `[seq1239-1248]` |
|---|---|---|
| 激活读回形状 | `PARAM_UR_BYTE_CNT/ACT_BYTE×PARAM_SMC_NUM` 向量 | `PARAM_UR_BYTE_CNT/ACT_BYTE` 行 × `PARAM_SMC_NUM` 列 **Tile** |
| B=2 读回 | **「不需要进行转置即可发出广播」**（流水天然连续） | **「读回来的数据是按照列对齐的」** |
| 排序步骤 | 无 | **有：「激活矩阵排序微指令」→「将读回的 Tile 转置为行对齐」再广播** = **SMI_DS_T** |
| 广播对象 | 广播向量 | 广播左=行片段 / 广播右=列片段 |

- 通用约束：vector×matrix **只能广播向量**`[seq1174]`；matrix×matrix 广播左则右须转置存储、广播右则左须非转置`[seq1221-1222]`；
  **结果矩阵存储 = 非广播矩阵存储**。每 SMC 与本地权重列点积（SMC 内 ADDTREE5TO1 + cumulative K 累加）产部分和；跨-SMC 只广播、无 sum-reduce（印证 ADR-025）。
- **连乘转置 relabel**`[seq1227]`：「非转置间插经转置（实际不需操作）→ 转置后的转置间插」，靠安排存储格式规避搬运。
- **表711 LLAMA2 例**：Matmul_Q = **广播左**（激活非转置间插→DS-T 转置→广播 × 转置间插权重）；
  **Matmul_K = 广播右**（输出非转置间插）—— **广播右矩阵这条我们完全没建模**（D-06）。

---

## Part B · 分歧台账（文档权威 vs 我们 ADR/代码）

> 性质：🔴 实质错误（已落地/已决策与文档冲突） · 🟡 缺口（文档有、我们没建模） · 🟢 已对齐（文档确认我们对）
> · ⚪ 待验证（需读代码/VCS 才能定）。「建议」非承诺，仅下一步候选。

### D-01 🔴 matmul 激活转置引擎接错：DS → 应为 SMI_DS_T
- **文档**：matmul「激活矩阵排序微指令」= 把列对齐 Tile 转置为行对齐 `[seq1242]`，机制 = SMI_DS_T
  跨-SMC（SHIFTDN（下行收集）→转置→SHIFTUP broadcast）`[seq1037-1051]`。DS（表585-588）是每-SMC、挂 STB↔LDB、
  `SRC/DST=STB的UR读/写`、仅单 SMC 内 UR→UR `[seq888/1552]`，用途是 DRAM writeback reshape。
- **我们现状**：[ADR-026](../adr/ADR-026-matmul-q-matrix-matrix-ds-transpose.md) 把 matmul 激活 transpose 建模为
  **DS 模块（表585-588）**（`CRU_DS/CRD_DS`、UR→UR、AXI tid b00..b11）。代码侧 DS/ShuffleUnit/SMI_DS_T **全未落地**
  （Stage-3 defer），matmul_q/q2 的「转置」是 data-prep 预排行对齐的假转置。
- **分歧性质**：🔴 设计选错引擎。成因＝ADR-026（06-01）时 user 只给了表585-588，SMI_DS_T（表596-598）在更完整的
  v0.4.19 才齐。好消息：无错误代码要拆（DS 从没实现）。
- **建议**：写 ADR-033，supersede ADR-026 的 DS/Stage-3 部分，spec SMI_DS_T 五段 datapath；走 hco。
  → ✅ **已落地**：[ADR-033](../adr/ADR-033-smi-ds-t-cross-smc-transpose-datapath.md)（hco APPROVE-with-notes，2026-06-05）。

### D-02 ⚪ PARAM_SMC_NUM：文档 32 / sim 缩放 8
- **文档**：`PARAM_SMC_NUM=32`（真实流片）`[表53/51]`；SMI_DS_T 转置 Tile 尺寸、间插因子均以 SMC_NUM 参数化。
- **我们现状**：sim `PARAM_SMC_NUM=8`（缩放实例，ADR-022 改）。
- **性质**：⚪ 已知缩放，非错误；但 SMI_DS_T 的 transpose 正确性须保证「换 32 不破」（参数化勿硬编码）。
- **建议**：ADR-033 把 SMI_DS_T tile 尺寸写成 SMC_NUM 参数化；标 NEEDS-VCS 验 32。

### D-03 🟢 单 SMI 全局指令流 + SMC_ID shift 链 —— 文档确认
- **文档**：一个 SMI`[seq35]` + SMC_ID shift 链初始化`[seq928]` + CRU/CRD 链传播`[seq91-101]`。
- **我们现状**：ADR-022/023 已转单 SMI 模型。
- **性质**：🟢 已对齐（文档是 ADR-022 的强背书）。注意 ADR-023 sim 取「模型 A 中转」非真 CRU fabric，仍 still_holds: proposed。

### D-04 🟢/⚪ SHIFTUP/SHIFTDN 的 smc_id_en 语义 ≠ 计算单元（已核：blanket N-1 不误伤）
- **文档**：SHIFTUP/SHIFTDN 的 `smc_id_en` **广播时**=范围上界（同计算单元）；**不广播时(broadcast==0)
  =目标/源 UR 的 SMC ID**（单个值，非 N-1）。文档两处显式「与一般计算模块的定义有差别」`[表544/547]`。
- **我们现状（已核 2026-06-05）**：`payload_factory.py:105-116` 的 N-1 缺省**只作用于未显式设 smc_id_en 的
  compute 单元**；注释明确「SHIFTUP 等显式设 smc_id_en 的 op 由 meta_upper 命中（val 非 None），不进此分支」。
  → **ADR-031 的 blanket N-1 不会误伤 SHIFTUP/SHIFTDN**（它们走 op metadata 显式赋值）。
- **性质**：🟢 缺省机制无误伤；⚪ 残留＝SHIFTUP/SHIFTDN 各 op 的 broadcast 位 + smc_id_en 取值是否
  按文档区分「broadcast==1 范围 / broadcast==0 目标 ID」由各算子 metadata 负责，需逐 op 核（非缺省 bug）。
- **建议**：ADR-033 的 SHIFTUP broadcast / SHIFTDN（下行收集） 部分显式写明 broadcast 位与 smc_id_en 取值；
  从 🔴 降级（原担心的缺省误伤已排除）。

### D-05 ⚪ FP16MUL S0123=广播 / S4567=本地（matmul-native 组织）
- **文档**：`DR_FP16MUL_S0..S3 来自广播`、`S4..S7 来自本地`，S4567 共用单 DVR 端口；
  `D[x]=mul(S0_*[x],S4_*[x])`… 即广播激活 × 本地权重成对乘 `[seq198-251]`。
- **我们现状**：[ADR-032](../adr/ADR-032-fp16-matmul-fp16mul-addtree8to1.md) FP16 matmul 用 FP16MUL S0123/S4567，
  S1→S3 已 e2e bit-perfect。需核对「S0123 走广播、S4567 走本地」的角色分配与文档一致（ADR-032 提到「角色互换」）。
- **性质**：⚪ 待核对（可能已对，需读 ADR-032 实现）。
- **建议**：对照 ADR-032 的 producer 角色分配 vs 文档广播/本地，确认无反接。

### D-06 🟡 广播右矩阵（Matmul_K）完全未建模
- **文档**：表711 Matmul_K = **广播右矩阵**，输出非转置间插；广播右则左须非转置存储 `[seq1222/1253]`。
- **我们现状**：ADR-025/026 只做广播左（vector×matrix / Matmul_Q）。广播右无 ADR、无算子。
- **性质**：🟡 缺口（半个 matmul 谱系缺失）。
- **建议**：纳入 matmul 路线图 backlog，独立 ADR；非当前 SMI_DS_T 任务前置。

### D-07 ✅ ~~matmul 广播时序靠 COND 等待，未建模~~ —— superseded by [ADR-040](../adr/ADR-040-smt-single-stream-timing-broadcast-cancels-gather-needs-2n-edge.md)（2026-06-29, hco APPROVE-w-cond）
- **订正**：① 激活广播的 `2·PARAM_SMC_NUM`（=16）是 **SMI_DS_T 节点内在 latency、已在 SMT 建模**
  （`matmul_op.py:237` ds_transpose op_type=SMI_DS_T；`s1_smt_check.py` 在验），其下游消费=每-SMC 本地点积
  → 属「广播偏移相消」类，**单流 SMT 本就正确**，非「未建模」。② 文档 §988(`[seq990]`) 把此路径列为 **COND**
  例，但 user 2026-06-29 override 改判 **DLY 静态确定边权**（V1.0 全片上、延迟确定；COND 留待将来 DRAM 源）。
- **真正未建模的**：P2 真 gather-to-sink/concat（§1204 Matmul_QK）的 **≈N 单向边**（DEBT-040-SINK-EDGE，**不复用 2N**）。
- **bank5=0 归因**：emit/lowering 下游 bug（DLY 未标定完整流水），**非 SMT 边缺失**；详见 ADR-040 + [[timing-verify-strategy-plan-c]]。

### D-08 🟡 VIN/VOUT/MIN/MOUT 流水寄存器 + RtoVR 字段建模深度（spec_doc_align 已证缺口）
- **文档**：VIN0/1·VOUT0·MIN0/1·MOUT0 是 6 个真实 PR 单元`[表567-584]`；RtoVR 是 34→39 全交叉选通矩阵
  （`mv_out*` 6bit + `OUT_FROM_IN_EN_*` 64bit 位图）`[表564-566]`，且**无 smc_id_en**。
- **我们现状（spec_doc_align 2026-06-05）**：`modules.yaml` **无** MIN0/MIN1/MOUT0/VIN0/VIN1/VOUT0/STBAXI/LDB/LDBAXI
  对应单元（工具报「无对应单元」）；**RtoVR doc 6 字段 / modules 仅 2 字段**（选通字段欠建模）。RtoVR 我们建模
  （rtovr_config consumers_dr 止于 33，与文档 34 输入吻合），但微指令字段深度不足。
- **性质**：🟡 多个 V1.0 PR 单元 + RtoVR 选通字段缺建模（model-A 终值 parity 不依赖，但忠实度缺口）。
- **建议**：升忠实度时补这 6 个 PR + RtoVR `mv_out*`/`OUT_FROM_IN_EN_*` 字段；RtoVR「无 smc_id_en」与 ADR-031 一致✓。

### D-12 🟡 spec_doc_align：STB 缺 smc_id_en + 多个 V1.0 单元名错配/缺失
- **文档/工具**：`spec_doc_align.py`（doc 权威 vs modules.yaml）报：**STB**「master+modules 都缺 smc_id_en → 补」
  （STB 是 V1.0 支持单元，表551 有 smc_id_en）；**LOGIC vs LOGICAL** 名错配（doc 表541 单元名 `LOGICAL`，
  我们叫 `LOGIC` → 工具两边都报「无对应」）；`UR` vs 我们的 `UR_READ/UR_WRITE` 名错配；
  `FP32ADDTREE`(表520 标题简称) 未自动映射到我们的 `FP32ADDTREE8TO1`（工具名匹配 artifact，非真缺失）。
- **性质**：🟡 STB smc_id_en 是真缺口（V1.0 单元）；其余多为命名错配，需人工确认而非工具自动判。
- **建议**：补 STB smc_id_en；统一命名（LOGIC↔LOGICAL / UR↔UR_READ/WRITE）或在 spec_doc_align 加别名表；
  与 D-10 LOGIC inst_typ=4bit 一并核 modules.yaml 字段。

### D-09 ⚪ FP32ADDTREE8TO1 无 accumulate 字段
- **文档**：ADDTREE8TO1 微指令只有 `vld/dst_id/smc_id_en`，**无 accumulate**`[表520]`；ADDTREE5TO1 才有 accumulate。
- **我们现状**：ADR-032 FP16 matmul 用 ADDTREE8TO1 做 reduce。若需跨 tile cumulative（DEBT-FP16-MULTITILE），
  **不能靠 8TO1 自身累加**，须外部累加器（UR 部分和 或 5TO1）。
- **性质**：⚪ 设计约束（影响 multi-tile 方案，非当前 bug）。
- **建议**：DEBT-FP16-MULTITILE 设计时明确累加机制不走 8TO1；核对 ADR-032 是否已假设错。

### D-10 ⚪ LOGIC inst_typ=4bit vs CLAUDE.md「LOGIC 4→5」
- **文档**：LOGIC 微指令 `inst_typ`=**4bit**（16 op 全用满），`CRU_LOGIC`=6bit `[表541]`。
- **我们现状**：CLAUDE.md/ADR-031 提「LOGIC 4→5」。需确认「4→5」指哪个字段（若指 inst_typ 则与文档 4bit 冲突；
  若指 code_bits/其他则另说）。
- **性质**：⚪ 待澄清（可能是不同字段，非冲突）。
- **建议**：复跑 spec_doc_align.py 比对 modules.yaml LOGIC code_fields vs 表541。

### D-11 🟢 全单元 smc_id_en、RtoVR 例外 —— 文档确认 ADR-031
- **文档**：所有计算/搬运单元 `smc_id_en`(5)；RtoVR 明确「不使用 SMI_ID」`[seq755]`。
- **我们现状**：ADR-031 正是「除 RTOVR 外全单元统一 smc_id_en」。
- **性质**：🟢 已对齐（文档强背书 ADR-031 方向）。但注意 D-04 的 SHIFTUP/SHIFTDN 语义差例外。

### D-13 🔴 SMI_DS_T 应是指令驱动单元，非 simulation_only（2026-06-10 user 订正）
- **文档**：SMI_DS_T 是一等指令驱动硬件单元（A4 补注）——`CRI` 指令输入（表596）+ 微指令格式（表598:
  vld/intlv_*）+ `DATA_OUT`/`INST_DONE` 输出寄存器，和 UR/FPMUL 同构（各有 `CRU_*` 指令 + 微指令 + 输出）。
  位于「SMI 微架构和微指令」，应由 SMI_IQ 指令流驱动，是 5 条协同指令 datapath 里的一个指令节点。
- **我们现状**：[ADR-033](../adr/ADR-033-smi-ds-t-cross-smc-transpose-datapath.md) Stage-A 把它建成
  `modules.yaml simulation_only: true` + `sim_config` 驱动 `cluster.apply_smi_ds_t`（run 前原子段，**绕开指令流**）——
  model-A 捷径，不进 DFG/SMT/IQ，无 CRI 解码、无 INST_DONE 握手。
- **性质**：🔴 忠实度缺口（model-A 终值 parity 对指令驱动/时序忠实性增信近零）。正是 ADR-033 标的 milestone (a)。
- **建议**：写 [ADR-034](../adr/ADR-034-smi-ds-t-instruction-driven-unit.md) 升为指令驱动单元（去 simulation_only +
  加 IQ_SMI_DS_T + DFG 节点 + sim 单元解码 CRI + SMT 依赖），走 hco。gated on NEEDS-USER-FACT-033-CRI/-DSTDONE
  + IQ 驱动方式（3 个 RTL 缺口）。

---

## 后续动作清单（出自台账）

1. ~~**ADR-033**（D-01）：spec SMI_DS_T 五段 datapath，supersede ADR-026 DS/Stage-3，走 hco。~~ ✅ **已写 + hco
   APPROVE-with-notes**（2026-06-05，[ADR-033](../adr/ADR-033-smi-ds-t-cross-smc-transpose-datapath.md)）。2 BLOCK 已收口
   （SMT 边界裁定 / 完成握手升 NEEDS-USER-FACT-033-DSTDONE）。下一步 = Stage-A 实施（sim 中心转置核 + matmul_q 改走五段链）。
2. ~~核对 D-04 SHIFTUP/SHIFTDN smc_id_en~~ ✅ 已核（2026-06-05，payload_factory:111 显式赋值不走 N-1，无误伤）。
3. 核对 D-05 ADR-032 FP16MUL 广播/本地角色；D-09 8TO1 无累加对 multi-tile 的影响（读 ADR-032 实现）。
4. 补 modules.yaml：D-12 STB smc_id_en + 命名错配；D-10 LOGIC inst_typ=4bit；spec_doc_align 加别名表。
5. backlog：D-06 广播右矩阵、D-07 COND 时序忠实、D-08 PR 单元 + RtoVR 字段建模深度。
6. **ADR-034**（D-13）：SMI_DS_T 升指令驱动单元（去 simulation_only + IQ + DFG + CRI 解码 + SMT 依赖），走 hco。
   gated on 3 个 RTL 缺口（CRI 宽度 / IQ 驱动方式 / INST_DONE 回收）。← milestone (a)，2026-06-10 起草。

> 升 Accepted 唯一路径＝VCS/RTL。本文所有「文档证据」忠实于 v0.4.19，但 v0.4.19 本身多处标「疑笔误/待完善」
> （如 `UR_Mat_Tran_Inte` 命名冲突、表711 标题、转置间「差/插」混用），引用时以 [seq] 原文为准。
