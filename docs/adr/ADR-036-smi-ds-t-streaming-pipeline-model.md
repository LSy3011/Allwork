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

### D2. 整条 datapath = 固定 latency 静态排程(全 DLY,不建 COND)（Q3 再订正,user 定）
`UR_read(列对齐, B=2 双读) → SHIFTDN(gather,定长) → SMI_DS_T(流式转置,定长) → SHIFTUP(广播,定长) →
RTOVR → 点积 → UR_write`,**全链固定 latency**。
- **不建 COND**(user)：COND 是给**延迟不定**(para988 的 DRAM 例)用的;SMI_DS_T 这条链全是**确定固定延迟**
  (SHIFTDN N hops + 转置 + SHIFTUP N hops 都是定数),**编译器直接按总 latency 把 compute 排在 datapath 之后**即可。
- SMT 的 `t[c]≥t[p]+lat` + II=1 **本就是这个静态排程**——只要把 SMI_DS_T datapath 各级固定 latency 入
  `pipeline_delays.yaml`,compute 自然排在转置完成之后,**无需 COND 等待**。RTL 即便硬件用 COND,因延迟固定,出来拍数
  与静态排程一致 → sim 仍逐拍对齐(latency 填对的前提下)。

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
  gather-gate `SmiDsTUnit` + DST-SHIFTDN/DST-SHIFTUP 后门日志 —— **整体作废**，由本流式模型取代。
  （ADR-033 的转置**数值正确性** + datapath 拓扑结论保留。）

## Open questions / 已定项
- ~~**Q1**（中心 vs per-SMC）~~ **已定（文档）**：表596 接口（单 CRI/INST_DONE/CRD_SHIFTDN）+ para1030「将多个 SMC 数据
  读回 SMI」+ 表597 buffer 全局单份 → **SMI 中心单实例**（非 per-SMC）。sim 放 cluster 级一个；剩「如何与 per-SMC
  SHIFTUP/RTOVR 对齐」是工程细节。
- **Q3 → 已定（user：不建 COND，固定 latency 静态排程）**：para988 的 COND 是给**延迟不定**（DRAM）用的;SMI_DS_T 链
  全是**确定固定延迟**,编译器直接按总 latency 把 compute 排在 datapath 之后(SMT `t[c]≥t[p]+lat`+II=1 即此)。**sim/SMT
  不建 COND 等待机制**,只需 SMI_DS_T datapath 各级固定 latency 填对(D3)。RTL 硬件即便用 COND,延迟固定 → 拍数一致。
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

## S1 实现（2026-06-10，DFG+SMT 切片落地；hco a4f6b70 BLOCK→修复→APPROVE-with-notes）
S1 验收 = **DFG+SMT**（非 e2e bit-perfect，后者 S3）。落地：
- **D3 latency**：`pipeline_delays.yaml` 加 `SMI_DS_T: 16`（=PARAM_SMC_NUM*2，Q2「转置 latency=gather
  完成点」；SHIFTDN/SHIFTUP 全局 unit lat 维持 1 不污染他算子）。hco 核实 16 数值正确 + 无双计/漏计。
- **D1 5 段 DFG**：`_inject_smi_ds_t_transpose` 补首段 `ds_src_read`(UR_READ 列对齐)→ds_gather→ds_transpose
  →shiftup→rtovr→ur_write。SMT 实证（`verification_v2/tools/s1_smt_check.py` 绕 sim）：II=2、
  t[ds_transpose]=4 < min t[compute]=25（gap=21=16+datapath 尾链），5 段顺序 ✓。
- **D4 验收 ①（e2e 不回归）**：3 算子（matmul_q_dst/matmul_q_dst_k8/fp16_matmul_q_dst）`--mode e2e` exit=0。

### 实现不变量（hco BLOCK 修复，必须保持）
**「emit 过滤 = audit 过滤」**：structural_only 节点（ds_src_read）既被 `pipeline_runner` emit 前过滤、
也被 `auditor.audit_truth_alignment` 同步过滤。缺一即假阳——truth auditor 对没 emit 的节点查 sim log
必 FAIL → `--mode e2e` exit=1（数值 bit-perfect 但假红）。回归护栏：`tests/unit/test_smi_ds_t.py::
TestSmiDsTS1FiveStage`（源码不变量）+ `tests/integration/test_smi_ds_t_e2e.py`（e2e 退出码）。

### 债务
- **DEBT-036-STRUCTURAL-ANCHOR**：`ds_src_read` 标 `structural_only`（SMT 可见、sim 不可见），令「SMT 拓扑」
  与「sim 实际执行」受控分叉——S1/S2 期真值由 model-A `apply_smi_ds_t` backdoor 供，故 SMT 排程对「真流式
  列对齐读时序」忠实度增信近零（同 ADR-021/023 sim 自洽≠RTL）。**退出条件**：S3 删 model-A backdoor 时
  同步删 flag、ds_src_read 转真 UR_READ 进 sim，VCS 验收 ② 兜底。
- **DEBT-036-K8COMPUTE**：scenario-A 的 K=8/M=4 **bit-perfect compute 本轮未做**（user 2026-06-10 裁定推 S3）。
  实测撞两道结构墙：① 2 词广播撞**单 SHIFTUP 单源**（sim 广播单元硬接 ur-r0，第二广播源不支持）→ SMT
  全 II UNSAT；② 单广播+cumulative 需**逐行 reset** = DEBT-026 Stage-2。候选第三路 = local-per-SMC（改
  `apply_smi_ds_t` scatter-to-all + 本地 2 词 addtree8to1 单发），需 sim 改 + 偏离文档 SHIFTUP-广播模型、
  过 hco。`matmul_q_dst` 暂留 K=4 Stage-A（working 8/8）。K=8 真做 = S3（需 S2 流式单元 + K>4/M>1 compute 方案）。

## S2 实现（2026-06-10，流式 stepped 单元 + 固定 latency pipeline；hco ac50587 APPROVE-with-notes）
S2（user 裁定「全流式+时序现在做」）：仿真器 SMI_DS_T 从 gather-gate 改为流式 stepped 单元。落地（3 文件）：
- **D1 流式接口**（`sim/smc_simulator/units/smi_ds_t_unit.py`）：新增 `evaluate({valid,tile})`/`tick()`/
  `get_outputs()`（对标 fp32addtree8to1_unit）。evaluate 用 `transpose_tile` 纯核（**无 is_ready/raise gate**）转置 →
  行词 FIFO + 启动 latency 倒计时；tick 在 latency 窗口内发气泡(DATA_OUT_VLD=0)、到点后每拍发 1 行词（线速）。
- **D2/D3 固定 latency**（`system/cluster.py`）：`_tick_smi_ds_t_trigger` 改 `evaluate`+`tick` 驱动；latency 取
  `hw_spec.delays["SMI_DS_T"]`(=16)，**与 SMT 的 a_read=t[transpose]+latency 双侧单源耦合**（同读一份 yaml）。
  实测：DST-SHIFTDN@Cyc6 → DST-SHIFTUP@Cyc22-25（=6+16）；e2e 仍 bit-perfect（cluster.step 内 `_tick`(scatter)
  先于 kernel.step(a_read) 同拍 → 写先于读）。
- **gate 旁路**：live 路径已不走 gather-gate；物理方法（gather/is_ready/transpose-raise/load_tile/execute/
  emit_step）保留给 `apply_smi_ds_t` fallback，**S3 随 backdoor 删**。

### hco 实证（ac50587，独立复跑 + 反证）
3 算子 e2e exit=0 + scatter 拍偏移=latency 已独立复跑；**hco 亲手解耦反证**（cluster latency≠SMT latency）→ e2e
exit=1 + truth auditor 逐拍报 a_read stale → **任何 latency 漂移 loud-fail，非静默错**（脆弱性担忧消除）。

### S2 忠实度边界 / 债务
- **输入仍 burst、仅输出 latency-pipeline**：evaluate 一次性收整 tile（非 D1「每拍 1 列片入」），输出侧才真逐拍。
  对终值无影响（bit-perfect），真 SHIFTDN 逐拍 drain 列片 = **DEBT-S4-SHIFTDN-DRAIN**（gated on VCS，无 VCS 不可校准）。
- **DST-SHIFTUP 同拍 R+W 对 arbiter 不可见**（既存 DEBT，非 S2 引入）：scatter 直写 `kernels[0].ur_bank.storage`
  绕 per-kernel arbiter，故 Cyc22 SMC_0 bank0 的 scatter(W)+a_read(R) 同拍不报冲突。**S3 退出条件**：删 backdoor、
  ds_src_read 转真 UR_READ 进 sim 后，此同拍 R+W 进 arbiter 视野 → **必须确认 DLY 把 a_read 排在 scatter 之后不同拍**，
  否则暴真 bank 冲突。**【S3-b RETIRE，见 S3-b 债务段】**：full fan-out 重构后 ON 路径 scatter 不再写 bank0
  （转置行经 `_dst_bcast_feed` 直喂 SHIFTUP）→ 此 bank0 R+W 伪冲突在 ON 路径物理消失（OFF backdoor 仍保留至 S3-c）。
- latency=16 仍是 Q2「先按 gather 完成点建模」假设；逐拍与 RTL 一致 = 验收 ②，**待 S4 VCS**。`still_holds: proposed` 维持。

## S3-a 实现（2026-06-15，gather 去 backdoor → 真固件 UR 读端口；hco BLOCK 提案 A → 修正 → 落地）
S3-a（VCS 第一手反馈暴露 model-A backdoor 缺口：`fp16_matmul_full_dst` 固件首个 UR 读=bank0 a_read，
但 sim 首个 UR 读=DST-SHIFTDN bank6——两者不对齐，因 bank6 列对齐读标 `structural_only` 被 emit 前过滤、
固件无 bank6 读指令，sim 靠 `cluster.py` storage 直读蒙过；8/8 假绿因只比 sim-vs-golden、不检查固件-log 一致）。
**目标 = gather 侧去 backdoor，让 sim 走真固件指令**（scatter 留 S3-b、删 model-A 残余留 S3-c）。

落地（env flag `AIACC_DST_REAL_GATHER` 守，默认 OFF=维持 backdoor，ON=真 gather；ON/OFF 逐 bit A/B 护栏）：
- **`matmul_op._inject_smi_ds_t_transpose`**：ON → `ds_src_read` 去 `structural_only`（进 emit/sim、真 emit
  BANK=6 读指令）且**不硬 pin iq_id**（让 solver 分配端口）；OFF → 维持 `structural_only=True` + pin。
- **`pipeline_runner._dst_src_capture_config`**（镜像 `_broadcast_capture_config`）：post-solve 回读 ds_src_read
  实际落的端口（`port=iq_id-UR_READ基址`）→ `smi_ds_t cfg["src_read_port"]` + patch `dst_src_capture{port,bank}`。
- **`kernel.set_dst_src_capture` + 读 evaluate 快照**：独立捕获寄存器 `_dst_src_capture_data`，同端口读配置 bank
  时锁存（与 `_bcast_capture` 同构）→ 即使 ds_src 与广播 a_read 共享 port0，两寄存器按 bank（6 vs 0）独立快照、
  互不 clobber（sim 建模层避 clobber，**不加 SMT 约束**——hco H1 端口独占 axiom 已证伪 UNSAT，见 ADR-037）。
- **`cluster._tick_smi_ds_t_trigger`**：`cfg["src_read_port"]` 存在 → gather 从 `kernels[s]._dst_src_capture_data`
  取数，**删 storage[bank6] 直读**；否则维持 backdoor。

### hco 裁定（agentId a8d2840595118f5c8，BLOCK 提案 A → 修正后方向 ACCEPT）
- **BLOCK 原提案「ds_src_read 硬 pin ur-r1」**：4 个 UR 读端口已被 fp16 compute（4 权重 + 广播激活）占满
  （ADR-037 实测 II=4..18+ 全 UNSAT），无空闲 ur-r1。修正 = 不硬 pin、solver 在转置 prologue 窗口（ds_src 是
  单次 `shape=[1]`，与稳态读时间错开）分配端口 + post-solve 回读（实测 solver 分到 port0=ur-r0，与广播源共享，
  靠双捕获寄存器按 bank 区分而无冲突）。
- **Q2 ACCEPT**：从读端口锁存器取数是合格 model-A 中转、SHIFTDN 下行搬运段仍记 DEBT-S4，**是进步非倒退**
  （从「绕一切直读 storage」收敛到「占真 R 端口 + 进 arbiter 视野」）。
- **Q4 确认**：bank0 同拍 R+W 此阶段不触（scatter 仍 backdoor 写 bank0、不进 arbiter），S3-b scatter 端口化才暴露。

### 实证（本会话）
- ON：II=10 SAT、固件含 1 条 `BANK=6 ADDR=0` UR 读指令（trace_id=ds_src_read，落 ur-r0）、sim 8 条 DST-SHIFTDN
  Port:0 Bank:6 读捕获寄存器（非零真实 fp16 列数据）、**8/8 bit-perfect e2e exit=0**。
- A/B：ON vs OFF 输出逐 bit 一致（两路均 bit-perfect vs golden）。
- 回归：pytest 145 passed / 6 skipped（默认 OFF 不受影响；含 S3-a 新增 4 测试）。
- **可证伪性**：`_dst_src_capture_data` 初值 0、仅由真 bank6 读填充；若固件无该读指令则 gather 得 0 → 输出错 → FAIL。
  ON 8/8 通过 ⇒ 真固件读生效（非 backdoor）。

### S3-a 债务 / 待办
- **DEBT-036-DST-SRC-PORT**（hco Q1，user-fact 待 RTL）：SMI_DS_T 跨-SMC 列对齐源读（SMC_s 读本地 bank6 列 s）
  **物理上走哪条 datapath/端口**未明——(a) SHIFTDN 内部搬运读，不占 compute 的 4 个 UR_READ 端口；或 (b) 复用
  compute 的 UR_READ 端口（端口时序耦合是真约束）。S3-a 取**保守模型 (b)**（占真端口、靠排程错开）。**升 Accepted
  门**之一 = user 答此 datapath/端口归属 + VCS 实测；若证 (a)，把 ds_src 建成 UR_READ IQ 的做法需重构。
- **默认 OFF 暂不翻**：S3-a 仅 gather 侧；backdoor 整体删（含 scatter S3-b + model-A 残余 S3-c）后才翻默认/删 flag。
- 固件完整性门（hco 建议，auditor 扫 sim log 每 UR 读写事件必有固件指令对应、否则 FAIL，抓 backdoor）= S3-b/c 增量。
  **已部分前移**（hco a1d94523 note 1）：`pipeline_runner` 在 real-gather 标记存在但端口回读失败时 loud-fail，
  拒绝「固件真读 / sim 静默回 backdoor」半态（现 emit 路径触发不到，守未来回归）。其可证伪单测留 S3-b。
- DEBT-S4-SHIFTDN-DRAIN / DST-SHIFTUP 同拍 R+W（既存）见 S2 债务段。`still_holds: proposed` 维持。

## S3-b 实现（2026-06-15，scatter 去 backdoor —— **事实订正：scatter ≠ UR 写**；hco afa16aa1 设计裁定）

### ⚠ 事实订正（hco afa16aa1，推翻 handoff c92748e 对 S3-b 的前提，必看）
handoff 设想「S3-b = scatter 转真 UR 写指令」。**这个前提是错的**——hco 据 spec 查实：文档钦定的真实
datapath 是 `SMI_DS_T DATA_OUT → SHIFTUP 广播（DR_SHIFTUP_D, rtovr_src 29）→ RtoVR → compute`
（`shiftup.yaml:24-27,50-53` + `rtovr_config.yaml:34` consumers_dr 29 + ADR-033 D1/D3-Q2）。**转置后的
行对齐激活是被 SHIFTUP 广播-消费的，从不单独写回一个 UR slot**；datapath 末端的 UR_WRITE 是 compute
**结果**写回、不是转置激活写回。
- 故 `cluster.py` 的 `storage[bank0][addr]=word`（scatter 写）**不是隐藏真指令的 backdoor**（gather 侧 bank6
  读才是——RTL 有该读、被 structural_only 过滤掉了，S3-a 已修），而是 sim model-A 把转置结果「暂存 bank0
  让 a_read 去读」的**速率匹配中转 artifact**——RTL 里没有这一步、也没有对应 UR 写指令。
- **选项 A（加 scatter UR_WRITE DFG 节点）/ 选项 B（端口化直写进 arbiter）= hco no-go**：会坐实一条 RTL
  不发的 UR 写、制造新固件-RTL 不一致（比现状更糟），且 B 暴露的 bank0 R+W 是 sim 自造伪冲突、对忠实度零增信。
- **gather 侧（S3-a）是真 backdoor 去除；scatter 侧无 backdoor 可去**——这个不对称是核心洞察。

🔤 **术语订正（2026-06-15，user 裁定）——「gather/scatter」不是硬件单元**：本 ADR（及 ADR-033/034、代码）里的
gather/scatter 是借并行计算术语的「方向」描述，**非 RTL 单元名**。真硬件只有 **SHIFTDN**（下行链，"gather" 方向）
+ **SHIFTUP**（上行链，`broadcast`/`P2P` 两 mode）——"scatter" 实为 **SHIFTUP broadcast**（`broadcast=1` + `smc_id_en`，
`DR_SHIFTUP_D ← CRU_SHIFTUP_O[data]`，`shiftup.yaml`）。sim log 端点 tag 已正名：`DST-SHIFTDN`(原 DST-GATHER) /
`DST-SHIFTUP`(原 DST-SCATTER) / `SHIFTUP-BCAST`(原 SHIFTUP-FEED)。**保留**纯编程动词/参数（`gather_smc_cnt` 计数、
`SmiDsTUnit.gather()` 收行方法、`ds_gather` trace_id=SHIFTDN 节点、`scatter_to_feed` cfg）——非硬件名声明、改之纯 churn。

### 落点（user 2026-06-15 裁定 full fan-out 重构）
让 SMI_DS_T DATA_OUT 经真 SHIFTUP 广播 fan-out 逐行直喂 compute、删 bank0 staging + a_read 读 bank0
对 bank0 的数据依赖。hco 评 full 重构「时序忠实度仍 S4-blocked（churn without fidelity gain）」、保守落点
（留 staging + 记债）亦可；user 选 full 重构（清掉 model-A staging artifact，为 S3-c 删 model-A 铺路）。

### 落地（env flag AIACC_DST_REAL_GATHER 守，ON = 完整真 datapath = S3-a gather + S3-b scatter；OFF 维持 backdoor）
转置结果改走专用「SHIFTUP 广播输入缓冲」`_dst_bcast_feed`（建模 RTL `SMI_DS_T DATA_OUT → SHIFTUP
data 入口 CRU_SHIFTUP_I → DR_SHIFTUP_D → RtoVR`），删 `cluster.py` 的 `storage[bank0]=word` staging：
- **`kernel`**：加 `_dst_bcast_feed`(行缓冲) + `_dst_bcast_feed_cur` + `set_dst_bcast_feed(bank)`；`_evaluate_ur`
  里广播源(port0 a_read)读 feed_bank addr=m 时按 **decode 的 addr** 取第 m 行（NOTE-Q3-A）；SHIFTUP data
  三分支优先级 `_dst_bcast_feed > _bcast_capture_data > live`（NOTE-Q3-B）。`_dst_bcast_feed_cur` 更新 guard
  同 `_bcast_capture`（仅 port0 读 feed_bank 才更新）→ **保留 ADR-037 防 clobber 性质**。
- **`cluster._tick_smi_ds_t_trigger`**：`cfg.scatter_to_feed` 时投 `kernels[0]._dst_bcast_feed[da+off]=word`（审计
  Unit:SHIFTUP-BCAST），删 bank0 storage 直写。
- **`pipeline_runner`**：real-gather 时 `cfg.scatter_to_feed=True` + patch `dst_bcast_feed{bank,smc:0}`。
- **`physical_runner`**：读 `dst_bcast_feed` → 仅 head(SMC_0) `set_dst_bcast_feed`。
- **a_read 保持真指令**（BLOCK-Q2）：仍 emit 真 BANK=0 读、占 ur-r0 端口、arbiter 可见，作 SHIFTUP 广播触发
  + 提供 decode 的 addr=m 索引；其 bank0(空)读值被转置行替代（**禁止**标 structural_only/不 emit，否则退化 backdoor）。

### hco 设计裁定（agentId a6cc1a2，APPROVE-with-notes）+ 实证
3 硬约束（BLOCK-Q2 / NOTE-Q3-A / NOTE-Q3-B）已落实。实证：ON e2e 8/8 bit-perfect exit=0 + 3 次 cold-run 逐 bit
一致（确定性）+ A/B（ON vs OFF）逐 bit 一致 + 固件含真 a_read BANK=0 读 + scatter 8 条 SHIFTUP-BCAST（零 UR-WRITE
bank0）。pytest 147 passed（+2 S3-b 单测：行索引跟随 decode addr / dispatch 优先级反证）。广播路径算子（fp16_matmul_n
/ matmul_parallel_n / fma_parallel_n）OFF 默认未破（三分支 dispatch `_dst_bcast_feed=None` 回落原路径）。

### S3-b 债务（hco Q4）
- **RETIRE「scatter 写 bank0 + a_read 读 bank0 数据依赖」**：full 重构后转置行经 `_dst_bcast_feed` 直喂，ON
  路径 a_read 不再依赖 bank0 有转置数据（仍读 bank0 作触发，读值弃用）。
- **RETIRE「DST-SHIFTUP 同拍 R+W 对 arbiter 不可见」（S2 债务段）**：ON 路径 bank0 **不再被 scatter 写** →
  bank0 R+W 伪冲突**物理消失**（不是「绕 arbiter」，是根本没这个写）。⚠ **仅 ON 路径**；OFF backdoor 仍写
  bank0，该伪冲突在 OFF 保留，至默认翻 ON / 删 flag（S3-c）才全消。
- **新增 DEBT-036-DST-BCAST-FEED-TIMING**（= S4 时序簇，交叉引用 DEBT-023-BCAST-TIMING / DEBT-S4-SHIFTDN-DRAIN）：
  `_dst_bcast_feed` 是 burst 一次性收整 tile 的中转（非逐拍 SMI_DS_T DATA_OUT → SHIFTUP data 入口流式投递）；
  逐拍投递时序 + SHIFTUP 入口寄存器 hold 拍 gated on user-fact（同 ADR-037 升 Accepted 门表）+ VCS。
- **拓扑忠实度真进步**（hco Q1）：删 UR write→UR read 往返 artifact，转置行喂进 SHIFTUP data 入口（对齐
  spec `DR_SHIFTUP_D=CRU_SHIFTUP_O[data]`）；但**时序忠实度仍 S4-blocked**（user 已接受 churn caveat）。
- **DEBT-036-STRUCTURAL-ANCHOR / 默认 OFF**：不关闭——S3-b 只删 scatter staging，gather 侧捕获寄存器中转
  + model-A 整体删 = S3-c（+ VCS）。`still_holds: proposed` 维持。

## S3-c 实现（2026-06-25，block-2：删 apply_smi_ds_t backdoor + gate 方法；前置 triage matmul_q_dst_full）

handoff 把 S3-c 剩余拆 3 块，user 裁定先做 **block-2「删 apply_smi_ds_t + gate 方法」**。前置 = triage
`matmul_q_dst_full`（既存 bug：OFF/ON 都 FAILED y SMC_0=0）。

### Triage：matmul_q_dst_full 既存 bug 根因
`ManualMatmulQDsTFullOperator` 是 **compute 隔离 increment**——`get_dfg` 直注 bank0（绕转置/广播前端）、
**不调 `_inject_smi_ds_t_transpose`、不产 smi-ds-t hex**。但它从 `ManualMatmulQDsTK8Operator` **误继承
`smi_ds_t_config()`**（返回非空 cfg）→ `pipeline_runner` patch `smi_ds_t` 进 sim_config →
`physical_runner` 见 cfg 但无 smi-ds-t hex → 落 `apply_smi_ds_t` fallback，从 src_bank=4 gather 写回
**SMC_0 bank0 words[0,1]**，正好 clobber 直注的 X[0]（m=0 通道）→ `SMC_0 y[0]=0`（words[2..7] 的
m=1,2,3 未覆盖 → 正确，故 7/8 + SMC_0 仅首元素错）。
- **修法**（选项 a「去掉误继承的 cfg」）：override `smi_ds_t_config` 返 `None` → `pipeline_runner:125
  if cfg` 跳过整段 patch，不落 fallback。修后 `matmul_q_dst_full` **PASSED y: All 8 SMC bit-perfect**。
- ⚠ 该算子 e2e 仍 exit=1：rigid auditor 对每个中间值期望 `Exp 0.000000`（不建模直注输入的预期值）
  —— 属 **block-1（auditor 建模）**范畴，**非** apply_smi_ds_t 引起；本算子无断言 exit=0 的 e2e 测试。

### 删除（apply_smi_ds_t 确为死代码）
确认 run 中真 datapath（`setup_smi_ds_t_trigger` + `_tick_smi_ds_t_trigger`）走流式
`SmiDsTUnit.evaluate/tick`（转置核 = `transpose_tile` 静态纯函数），**不调** `apply_smi_ds_t`。删后
`apply_smi_ds_t` 仅剩 physical_runner fallback（已死）+ 测试在用：
- `cluster.py`：删 `apply_smi_ds_t` 方法（model-A storage-to-storage 原子 backdoor）。
- `smi_ds_t_unit.py`：删 `gather/is_ready/missing/transpose`（4 个实例 gate 方法，gather-before-scatter
  gate 实现）+ 死状态 `_buf/_out` + 未用 `Optional` 导入。保留 `transpose_tile`（流式核）+ codec + evaluate/tick。
- `physical_runner.py`：「cfg 在但无 smi-ds-t hex」分支由 fallback 改 **loud-fail**（`RuntimeError`，同 S3-a
  反半态原则——拒绝静默回 backdoor）。
- 测试：删 `TestSmiDsTGatherOrdering`（3 测）+ `TestSmiDsTClusterApply` 的 2 个 apply 测试 + 一处死
  monkeypatch（`fc.apply_smi_ds_t = ...`，trigger 路径从不读）。覆盖迁移：转置数值/多词拆分 →
  `TestSmiDsTTranspose`+`TestSmiDsTS2Streaming`；跨-SMC 数值 → e2e。gather-before-scatter gate 随
  backdoor 退役（流式路径 by-design 无 gate）。

### 验收
- pytest **139 passed / 6 skipped**（= 144 − 5 删测，无失败）。
- `matmul_q_dst_full` y bit-perfect（triage 修复）；广播路径 `fp16_matmul_q_dst` / `fp16_matmul_full_dst`
  OFF+ON 双路均 **PASSED y bit-perfect**（A/B 护栏未破）。
- 忠实度增信不变：删的是 model-A backdoor 死代码，时序仍 S4-blocked。`still_holds: proposed` 维持。

## S3-c 实现（2026-06-25，block-1：fp32 auditor 建模 ON datapath + 翻默认 OFF→ON）

block-2 后续 user 裁定接 **block-1「翻默认 OFF→ON」**。前置 = fp32 truth auditor 建模 ON datapath。

### 根因：fp32 rigid auditor 按 OFF datapath 建模
ON 下 fp32 算子（matmul_q_dst / _k8）y bit-perfect 但 e2e exit=1，rigid auditor FAIL 于两个 SMI_DS_T
datapath 节点（仅这两个，timing 'L' 已对、仅 value 失配）：
- **ds_src_read**（ON 真 gather 读）：实际读列对齐激活（V0=X[0,0]），auditor 期望 0——因 OFF 下它是
  structural_only（被整体跳过），auditor 无其值模型。
- **a_read**（broadcast_source）：实际读空 bank0=0（真广播数据经 `_dst_bcast_feed` 入 SHIFTUP 缓冲），
  auditor 期望转置后 X[m,:]——因 OFF 下转置结果 scatter 写 bank0、a_read 从 bank0 读到它。

（fp16 算子 ON 已 exit=0：其 rigid audit 整体被跳过 = DEBT-FP16-AUDITOR，值按 FP32 解码对 fp16 假阳。）

### auditor 改：ON real-gather 下 SMI_DS_T datapath 端点 value-exempt
`verification_v2/infra/auditor.py`：ON（判据 = `ds_src_read` flag 存在，仅 ON 置）时把 ds_src_read +
broadcast_source 节点列入 `value_exempt_ids` → rigid compute 审计**仅校其时序、值作参考不报错**
（标 `[DATAPATH-EXEMPT]`，仅 failure-detail 模式打印）。理据：这两节点的「值」不在 compute 读端口可见
（属 SMI_DS_T datapath，由 DST-SHIFTDN/DST-SHIFTUP AUDIT-DATA 事件 + 最终 y parity 兜底），rigid compute
审计校它们的 compute 期望值是 category error——正如 OFF 下 ds_src_read 因 structural_only 被完全跳过。
OFF 路径 `value_exempt_ids` 空、严校不变（a_read 仍校 bank0 scatter 值）。

**DEBT-036-DST-AUDIT-SENSITIVITY（evaluator ACCEPT-with-caveats 实测）**：豁免后这两节点的值正确性**只剩
y-parity 兜底**，而 y-parity 用 `atol=0.005`，比原 rigid `ur-r` 的 `5e-4` 容差宽 ~10×。evaluator 反证：
对 datapath feed 注 gross error（常量覆写）→ y FAIL 0/8 抓得到；但注 LSB mantissa flip（`word ^ 0x1`）→
y 仍 bit-perfect、audit SUCCESS，两层皆漏。即「y parity 验证」措辞**高估覆盖**——实际 bottoms out 在
0.005 非 bit-exact，这两节点的检测灵敏度从 5e-4 降到 0.005。单 tile fp32 点积此 gap 偏 academic，但记债。
未来收紧 = 精确建模 ds_src_read 的 ON 期望值（per-SMC 列对齐读 V0=列首元素）替豁免（需 evaluator 的
`_evaluate_expected_values` 加 ds_src 列语义）。S3-b 的 bank0-not-polluted 不变量已由 A/B 测试
（`test_on_exit0_and_bit_identical_to_off` 验 scatter_to_feed + 无 bank0 staging）覆盖，故未在 rigid
审计另加 a_read==0 冗余严校。

### 翻默认 OFF→ON
`dst_real_gather_enabled()` 默认从 `"0"` 改 `"1"`（`matmul_op.py`）。受影响 4 算子（matmul_q_dst / _k8 /
fp16_matmul_q_dst / fp16_matmul_full_dst）默认走真 datapath；OFF 须显式 `AIACC_DST_REAL_GATHER=0`（留作
A/B 对照）。测试同步：A/B 护栏 OFF leg + S1 structural_only 测试 + S3a OFF 测试均改显式 `=0`（不再依赖
默认）。flag + OFF backdoor 路径删除待 VCS（升 Accepted）。

### 验收
- pytest **139 passed / 6 skipped**（无回归）。
- 默认（ON）：4 受影响算子 e2e 全 exit=0（含 fp32 rigid audit）。显式 OFF：matmul_q_dst /
  fp16_matmul_full_dst 仍 exit=0（A/B 双路 + 逐 bit 一致护栏未破）。
- 忠实度：默认现走真固件 gather 读 + SHIFTUP 广播缓冲喂（去 model-A backdoor）。时序仍 S4-blocked
  （DEBT-036-DST-SRC-PORT 保守模型 b + DEBT-036-DST-BCAST-FEED-TIMING）。`still_holds: proposed` 维持，
  升 Accepted 仍卡 VCS（SMC_1..7 各算对 + 列对齐源读端口/broadcast 逐拍时序的 user RTL 事实）。

## S3-c 实现（2026-06-25，block-3：固件完整性门；hco APPROVE-with-notes）

block-1/2 后 user 裁定接 **block-3「固件完整性门」**（hco 原创建议，S3-a 待办）。triage + hco 评审后落地。
`verification_v2/infra/auditor.py::audit_firmware_integrity` + main_verify 接入。

### 门的定位与边界（hco 必须落明）
- **定位 = 防回归哨兵**，**非增信工具**：守 S3 拆 backdoor 成果不被未来 commit 悄悄加回（如某访存走
  storage 直读、sim-vs-golden 仍假绿——正是 S3-a 当初靠人肉 VCS diff 才发现的 bank6 缺口）。
- **对 VCS/RTL 逐拍忠实度增信 ≈ 0**（同 ADR-021/023/036 全系列 caveat）。门只保证「sim 每个 UR 端点访存
  都能追溯到一条 emit 的固件指令」，**不**保证 ① 该指令 addr/value 逐拍与 RTL 一致（=VCS 的活）② SHIFTDN/
  SHIFTUP 搬运段时序（DEBT-S4）③ latency=16 是否对。`still_holds: proposed` 不变，门不改 VCS 门槛。
- **边界 = UR 端点指令存在性**，非逐拍/逐 addr/搬运段忠实。sim 只发 DST 端点事件（搬运段未建模 debt），
  门对中间 hops 无能为力、也不为它们造假指令。

### 规则集（hco 裁定后，匹配粒度 = (trace_id, port, bank)，不逐 addr）
- **规则1**  UR-READ/READ + UR-WRITE/WRITE → 必有同 (port,bank) 固件 UR 指令。
- **规则2**  UR-READ/DST-SHIFTDN → 锚定**具名 ds_src_read**（trace_id=='ds_src_read' 且 (port,bank) 匹配），
  **非**「该 bank 有任意 UR_READ」（hco 阻断项①：防无关同-bank compute 读蒙混）。
- **规则4**  SHIFTUP-BCAST/DST-SHIFTUP → datapath feed，归属 ds_transpose（SMI_DS_T 指令），不找 UR 写指令
  （勿坐实 RTL 不发的 UR 写 = S3-b no-go 方向）。
- **规则3**  UR-WRITE/DST-SHIFTUP（OFF bank0 staging）→ ON 路径 **unreachable**（ON 永走 SHIFTUP-BCAST、
  bank0 不写）；仅 OFF 诊断态、**非 ON 正常分支**（hco 阻断项②）。

### 关键设计点（hco notes 全数纳入）
- **只在 ON 跑（hco 裁 a）**：判据 = ops 带 `ds_src_read` flag（仅 ON 置）。OFF 是已知 backdoor 参考、
  豁免——对已知 backdoor 跑门是同义反复，且 OFF 的 DST 事件按定义就是 backdoor，会破 A/B 的 OFF exit=0 断言。
- **独立顶层方法、不被 fp16 旁路**：fp16 truth-audit 整体 skip（DEBT-FP16-AUDITOR），门若挂在其内部则一半
  DST 算子（fp16_matmul_q_dst/_full_dst）失效。门做成独立调用、只比对指令存在性不解码 value，不受 FP16-only
  解码缺陷影响。
- **多 SMC 共归属（守 SMC_1..7 盲区）**：扫**全部 SMC_ID** 事件（非只 audit_smc=0），但允许它们共归属同一条
  `SMC_ID_EN`-broadcast 指令（单 SMI 全局指令流 ADR-022/023，一条指令覆盖 8 SMC）→ 按 (port,bank) 匹配。
- **双计豁免**：ON 下同 (smc,port,bank) 的 Tag:READ（真指令执行、填捕获寄存器）+ DST-SHIFTDN（读捕获寄存器
  的 datapath 再标注）是**同一次物理读**，各自归属 ds_src_read、不报「重复访存无指令」。

### 验收
- pytest **146 passed / 6 skipped**（+7 门单测，含可证伪反证）。
- 4 受影响算子默认 ON e2e exit=0 且门 SUCCESS（matmul_q_dst 136 / _k8 74 / fp16_q_dst 113 /
  fp16_full_dst 512 UR 端点访存全 backed）；显式 OFF 门豁免、exit=0（A/B 不破）。
- 门单测（`tests/unit/test_smi_ds_t.py::TestSmiDsTS3cIntegrityGate`）：可证伪反证（缺 ds_src_read →
  DST-SHIFTDN FAIL）/ 具名锚定（无关同-bank UR_READ 不蒙混）/ 双计豁免 / 多 SMC 共归属 / SMC_1..7 盲区
  抓取 / OFF 豁免，6 维全覆盖。+ 集成断言 ON fp16 路径门被调用且 PASS（不被 fp16 skip 旁路）。

**S3-c 三块（block-1 翻默认 + block-2 删 backdoor + block-3 完整性门）全部落地。** 升 Accepted 仍卡 VCS
（DEBT-036-DST-SRC-PORT / -BCAST-FEED-TIMING / -AUDIT-SENSITIVITY + SMC_1..7 各算对的 RTL 实测）。

## 契约保真订正（2026-06-25，UR_read→SHIFTDN 补 DVR_SHIFTDN_S 路由 RTOVR；hco APPROVE-with-notes）

user（VCS 测 matmul 时，硬件 authority）指出：数据整理 datapath `UR read—SHIFTDN—SMI_DS_T—SHIFTUP—UR
write` 里 **UR_read → SHIFTDN 之间漏了 RTOVR**。核 `shiftdn.yaml`：SHIFTDN 源 = `DVR_SHIFTDN_S`
（producers_dvr slot 34，经 RtoVR 路由），语义 `CRD_SHIFTDN_O <- DVR_SHIFTDN_S`，无 data 立即数字段
（code_bits=7）。故 UR 读出**必经一个路由到 SHIFTDN_S 的 RTOVR** 才到 SHIFTDN，`_inject_smi_ds_t_transpose`
旧版 ds_src_read→ds_gather 直连是契约缺口（影响所有 5 个 SMI_DS_T 算子的 emit 固件 = VCS 比对面）。

**不对称（spec 钦定）**：SHIFTUP 广播源经 `data` 微指令字段直读 ur-r0（前无 RTOVR、后有 RTOVR sel=29）；
SHIFTDN 经 DVR 读源（前需 RTOVR）。仅此一处缺口（SHIFTDN→SMI_DS_T、SMI_DS_T→SHIFTUP 不缺）。

**实现（hco 纠正 naive 失败）**：AIACC 里 RTOVR **不是被调度的节点**（ucode_solver 排除出 compute_ops、无
t_var），而是消费者上的 `rtovr_deps` 边（dependency_axioms 建 t_latch 串进调度）。故：
- 加 RTOVR 节点 `rt_ds_src_SHIFTDN_S`（slot_id=PS.SHIFTDN_S=34）**供 emit 真路由 IQ**（mirror matmul rt_b）；
- `ds_gather.depends_on=[rt_ds_src_SHIFTDN_S]` + `rtovr_deps={ds_src_read:{dvr:34,sel:-1}}`（sel=-1 占位，
  payload_factory 对 UR 读源解析为 UR_READ_{port}_D）——边让 dependency_axioms 重定向真生产者 + 建 t_latch；
- naive 只加节点不加 rtovr_deps → `t_c=-1` 悬空 UNSAT；只加边不加节点 → 调度对但不 emit 路由 IQ。**两者都要**。
- OFF：rt_ds_src 标 `structural_only`（mirror ds_src_read，仅 SMT 锚点不进 emit）；
- ON：rt_ds_src 标 `smi_ds_t_route` → rigid 真值审计 **value-exempt**（block-1 同款，SMI_DS_T datapath 端点
  值不在 compute 读端口可见；否则读真 gather 值 3.55 vs 期望 0 假阳）。

**验收**：5 个 SMI_DS_T 算子（matmul_q_dst/_k8/fp16_q_dst/fp16_full_dst/unit_ds_transpose）ON exit=0 +
8 SMC bit-perfect；OFF 回归 exit=0。固件现含 `rtovr-0 SEL=源端口`（ON）路由 SHIFTDN_S。pytest 149 passed
（+可证伪 SMT 单测：ds_gather 排程晚 ds_src_read >=2 拍 = UR_lat1+RtoVR_latch1，证路由边非 no-op）。
SMT 影响低（单 prologue 1 t_latch + 3 约束，strict 分支）。`still_holds: proposed`，升 Accepted 仍卡 VCS
（SHIFTDN 经 slot 34 路由的真时序）。

## References
- v0.4.19 docx：para1148-1168（表71-77 FPADD 流水范式）/ para1193-1201（向量×矩阵，无转置）/
  para1236-1246（矩阵×矩阵 + 激活矩阵排序=SMI_DS_T）/ 表542-548（SHIFTUP/SHIFTDN）/ 表593-595（SMI_IQ）/ 表596-598（SMI_DS_T）
- [ADR-033](ADR-033-smi-ds-t-cross-smc-transpose-datapath.md) / [ADR-034](ADR-034-smi-ds-t-instruction-driven-unit.md)（model-A，本 ADR 推翻其 gather-gate + 后门）
- 代码触点：`sim/smc_simulator/units/smi_ds_t_unit.py`（重写为流式）/ `sim/smc_simulator/system/cluster.py`（删后门触发）/
  `sim/smc_simulator/core/kernel.py`（units 加 smi_ds_t + IQ 驱动）/ `hw_config/pipeline_delays.yaml`（SMI_DS_T latency）/
  `src/synthesis/ucode_solver.py`（SMI_DS_T 段进 DLY 对齐排程）
