---
adr_id: ADR-034
title: SMI_DS_T 升指令驱动硬件单元（去 simulation_only，执行 ADR-033 milestone a）
decided: 2026-06-10
last_verified: 2026-06-10
still_holds: proposed
builds_on: ADR-033, ADR-022, ADR-023
supersedes_partial: ADR-033  # 仅 Stage-A 的 simulation_only 建模（Stage-I 落地后）；ADR-033 转置数值/datapath 结论保留
---

# ADR-034 · SMI_DS_T 升指令驱动硬件单元

> **状态：Proposed（doc-first per ADR-004，hw_config + hw_spec + 流水线接口变更 → 必过 hco）**。
> 2026-06-10 user 订正：**SMI_DS_T 是一等指令驱动硬件单元**（`CRI` 指令 + 表598 微指令 + `INST_DONE`），
> 和 UR/FPMUL 同构，不是 [ADR-033](ADR-033-smi-ds-t-cross-smc-transpose-datapath.md) Stage-A 建成的
> `simulation_only` 数据变换。本 ADR 执行 ADR-033 标的 **milestone (a)**：把 SMI_DS_T 从 model-A 捷径
> 升为真指令驱动单元（去 simulation_only + 加 IQ + DFG 节点 + sim 解码 CRI + SMT 依赖）。
>
> **一句话**：SMI_DS_T 应像 UR/FPMUL 那样被 SMI_IQ 指令流驱动——`UR_READ → SHIFTDN → SMI_DS_T(CRI) →
> SHIFTUP → UR_WRITE` 是 **5 条协同指令的真流水**，SMI_DS_T 是其中一个指令节点，而非 run 前的原子 sim 变换。

## Context

### SMI_DS_T 是指令驱动单元（文档证据，对照 UR/FPMUL）
| | FPMUL | UR | **SMI_DS_T** |
|---|---|---|---|
| 指令输入 | `CRU_FPMUL` | `CRU_UR` | **`CRI`**（表596，"输入的指令"，宽度 `*`） |
| 微指令 | 表523(FPMUL CRU 微指令) | 表563(UR CRU 微指令) | **表598: vld/intlv_en/intlv_grp/intlv_in/intlv_out** |
| 输出寄存器 | `DR_FPMUL_D` | `UR_RDATA` | **`DATA_OUT`(128)/`DATA_OUT_VLD`/`INST_DONE`** |
| 专属 IQ | `IQ_FPMUL` | `IQ_UR_READ/WRITE` | **缺 `IQ_SMI_DS_T`** |
| 位置 | SMC 微架构 | SMC 微架构 | **SMI 微架构和微指令**（SMI_IQ 同级） |

- **微指令瘦的原因**（表598 仅 vld+intlv_*，无 smc_id_en/addr/dim）：转置维度由设计参数固定
  （`PARAM_IN_LINE_NUM=PARAM_SMC_NUM` 行 × `PARAM_IN_LINE_BYTE_CNT=PARAM_UR_BYTE_CNT` 列，表597）；
  寻址由**周边指令外置**（UR_READ 各 SMC 读 + SHIFTDN gather 到 `CRD_SHIFTDN` + SHIFTUP scatter）。
  SMI_DS_T 只转置 `CRD_SHIFTDN` 进来的 tile。intlv_* 仅控 4 并发指令流水（`PARAM_INST_NUM=4`，[seq1041]）。
- **完整 datapath**`[seq1051]`：`UR read — SHIFTDN — SMI_DS_T — SHIFTUP — UR write`，SMI_DS_T 输出送
  「用于 SHIFTUP 对应的 SMI_IQ（具 `EXTRA_DATA`）」`[seq1050]`。
- **IQ_SMI_DS_T 存在性的正面文档依据**（hco 补）：`[seq967]`「一般情况下一个 SMI_IQ 控制 SMC 中的一个模块（如 FPADD）」
  + `[seq968]` UR 对应 8 个 SMI_IQ / `[seq969]` RtoVR 对应 64 个——「一 SMI_IQ 驱动一模块」通则强烈暗示 SMI_DS_T
  单实例 → 对应 1 个专属 SMI_IQ 驱动其 CRI。文档只明说 SMI_DS_T **输出**去 SHIFTUP 的 EXTRA_DATA IQ`[seq1050]`，
  **CRI 输入来源 IQ 未直说**（NEEDS-USER-FACT-034-IQDRIVE），但该通则是 IQ_SMI_DS_T 存在性的依据。

### ADR-033 Stage-A 的 model-A 捷径（待升级）
| 维度 | ADR-033 Stage-A（model-A） | 本 ADR 目标（指令驱动） |
|---|---|---|
| modules.yaml | `simulation_only: true` | 真单元（code_fields=表598，**去 simulation_only**） |
| 驱动 | `sim_config` 的 `smi_ds_t` 块 + `cluster.apply_smi_ds_t`（run 前原子段，绕开指令流） | **CRI 指令**（表598 微指令进指令流） |
| IQ | 无 | **IQ_SMI_DS_T**（SMI_IQ 驱动 CRI） |
| DFG/SMT | 不进调度 | **SMI_DS_T op 在 SHIFTDN→SHIFTUP 之间，SMT 看见数据依赖** |
| sim | `cluster.apply_smi_ds_t` 直接读 UR bank | **sim 单元解码 CRI、读 `CRD_SHIFTDN`、转置、出 `DATA_OUT`+`INST_DONE`** |
| 完成 | 无 | **INST_DONE 握手**（`PARAM_INST_NUM=4` outstanding） |

ADR-033 Stage-A/A' 的**转置数值正确性 + datapath 拓扑结论保留有效**；本 ADR 只把「如何驱动」从 sim_config
捷径升为指令流。Stage-A simulation_only 模型在 Stage-I 落地前**继续作为 working/fallback**。

## Decision

### D1. modules.yaml：去 simulation_only，SMI_DS_T 作真单元
- 删 `simulation_only: true`；保留 `code_fields`（表598: VALID/intlv_en/intlv_grp/intlv_in/intlv_out）。
- `type: cross_smc_transpose` 保留；它成为**可调度/可 emit 单元**（emitter 发其微指令、solver 排其依赖）。
- ⚠ 去 simulation_only 后 SMI_DS_T 进入 emitter/payload_factory/solver 消费路径——**必须**配 IQ（D2）+ DFG 引用（D3），
  否则变「声明却无指令」的反向死 module。死 module 检查从「不得被调度」反转为「**必须**被 IQ 引用」。

### D2. 加 IQ_SMI_DS_T 驱动 CRI —— **必须改三处配置（hco BLOCK 收口）**
> ⚠ **hco 阻断项**：`get_iq_ids(unit_type)`（hw_spec.py:205-237）对常规单元走 `system_core.yaml` 的
> `unit_instances` + `iq_registry`，**不是只读 `iq_config.yaml`**。只改 iq_config → `get_iq_ids("SMI_DS_T")` 返 `[]`
> → ucode_solver:460 `valid_iqs` **回退 `[0]` 与 FPADD 撞 slot**，调度静默错配。SHIFTUP/SHIFTDN 正是**三处齐备**才生效。
- **(a) `iq_config.yaml`**：`IQ_SMI_DS_T { module: SMI_DS_T, EN_LOOPS, BITS_CODE }`，仿 IQ_SHIFTUP/IQ_SHIFTDN。
- **(b) `system_core.yaml` `unit_instances`**：`SMI_DS_T: 1`（**SMI 中心单实例，count=1，非 per-SMC×N**——与 FPMUL 等 per-SMC 单元不同）。
- **(c) `system_core.yaml` `iq_registry`**：`SMI_DS_T: <新 base id>`，**避让现有密集区**（SHIFTUP=29/UR_READ=30/SHIFTDN=34/UR_WRITE=35/RTOVR=100，id 空间紧）。
- `BITS_CODE` 暂取表598 的 5 字段（**5 bit payload**）；**注意**：这只覆盖 Stage-I **单指令 intlv_en=0** 的 payload。
  表596 `CRI` 宽度 `*` 是**指令寄存器总宽**（可能含 inst_id/COND/DLY 等指令框架位，类比 SMI_IQ 表595 微指令格式）——
  与 5bit payload 不是一回事；CRI 框架宽 gated on NEEDS-USER-FACT-034-CRI，**不影响 Stage-I emit，但 Stage-II 必扩**。
- `specs/system/system_core.yaml` 改动本身触发 hco（已在本 ADR hco 审查内）。

### D3. DFG：SMI_DS_T 作 5 节点流水的指令节点
- 算子 get_dfg 生成真五段链：`UR_READ(各SMC列对齐读) → SHIFTDN(gather) → SMI_DS_T(转置) → SHIFTUP(scatter) → UR_WRITE`。
- SMI_DS_T op：输入边 = SHIFTDN gather 输出（`CRD_SHIFTDN`），输出边 → SHIFTUP scatter 输入。携 `iq_id`（IQ_SMI_DS_T）。
- 取代 ADR-033 Stage-A 的「inject 列对齐 + sim_config 驱动 apply_smi_ds_t + 复用 matmul_q compute」——
  现在转置是 DFG 里的真节点，SHIFTDN/SHIFTUP 是其前后真 op。

### D4. SMT：SHIFTDN→SMI_DS_T→SHIFTUP 依赖进调度（milestone a 本体）
- SMI_DS_T 作 `latency=k` 节点进 IIAnalyzer/ucode_solver：scatter 必须在 transpose 完成后、transpose 必须在
  gather 到齐后。这是 ADR-033 B-BLOCK-1 裁定的 (a) 路径——让调度器看见 scatter-after-gather 依赖边。
- `k` 初值取 model-A `latency=1`（Stage-I）；真 k（含 4 并发 interleave 启动间隔 [seq1041]「每两指令前后两拍」）
  gated on NEEDS-USER-FACT-034-CRI/-DSTDONE（Stage-II）。

### D5. sim：分两步——Stage-I 指令触发 + model-A 数值，Stage-II 真 step-driven 数据流（hco BLOCK 收口）
> ⚠ **hco 阻断项**：现 sim 的 SHIFTDN 链是 SMC-to-SMC 链式移位（cluster.py:82-102），**没有「汇聚到 SMI 中心
> 单点 CRD_SHIFTDN」的落点**，也无 SMI 中心实体在 step() 里 tick。把 D5 写成「SMI_DS_T 单元读 CRD_SHIFTDN」
> 看似小改，实际需 SMI 中心成为一等 tick 实体 + SHIFTDN 链 drain 到它。**必须显式裁定 Stage-I 做哪种**，否则
> 「SMT 有依赖边」与「sim 终值正确」各自绿但中间断开 = 假忠实。
- **Stage-I（裁定取此，低风险）= 指令触发 + model-A 数值**：modules.yaml 去 simulation_only 后，SMI_DS_T 的微指令
  （表598 vld）**真进指令流并被 emit/decode**；sim 端 SMI_DS_T 单元由其指令的 `vld` 触发，**数值仍复用现有
  `SmiDsTUnit` 转置核 + model-A gather**（读已 gather 到的 UR 数据，承接 ADR-033 apply_smi_ds_t 的数值路径，但
  改由 DFG 的 SMI_DS_T op 触发，非 sim_config）。DFG/SMT 拿到真节点 + 依赖边（D3/D4）。
  **caveat（必须显式）**：Stage-I 的 SMT 依赖边（结构）与 sim 数值（model-A）是**两条独立验证**——D7 必须
  既验 SMT 边存在、又验 sim 终值，且标注「step-driven 真数据流（SHIFTDN drain→CRD_SHIFTDN→SMI_DS_T tick）是 Stage-II」。
- **Stage-II（RTL-gated）= 真 step-driven 数据流**：SMI 中心成 cluster.step() 一等 tick 实体；SHIFTDN 链末端 drain
  到其 `CRD_SHIFTDN`；SMI_DS_T 单元在 step 中消费、出 `DATA_OUT`/`INST_DONE` → SHIFTUP；N 拍串行 gather 到齐序
  （承接 ADR-033 `test_gather_before_scatter_ordering`）。gated on NEEDS-USER-FACT-034-DSTDONE + VCS。
- **`cluster.apply_smi_ds_t` + sim_config `smi_ds_t` 块**：Stage-I 保留作数值执行器（由指令触发）；Stage-II 真 step-driven
  落地后删（superseded）。

### D6. 完成握手：INST_DONE / PARAM_INST_NUM=4 outstanding
- SMI_DS_T 出 `INST_DONE` 信号；4 并发指令的 outstanding 跟踪。**NEEDS-USER-FACT-034-DSTDONE**
  （承接 ADR-033：INST_DONE 不在 [seq1003] 的 5 个 outstanding SMI_IQ 列表内，回收路径文档未定义）。
- Stage-I 单指令（intlv_en=0、无并发）不依赖 outstanding；Stage-II 才需。

### 分阶段实施
- **Stage-I（结构，现可定，不依赖 3 个 RTL 缺口）**：modules.yaml 去 simulation_only + **三处** IQ 配置（D2 a/b/c）
  + DFG 五节点 + emit/decode 表598 微指令（vld，单指令 intlv_en=0）+ SMT latency=1 依赖边。**sim 数值走 model-A**
  （指令触发 SmiDsTUnit 转置核，D5 Stage-I）。目标：matmul_q_dst（K=4）/ matmul_q_dst_k8（K=8）**经指令流**
  e2e 8/8 bit-perfect（取代 sim_config 捷径，数值不变）+ SMT 依赖边可见。
  > hco 确认：Stage-I 只用表596/597/598 **已成文**的结构（接口寄存器存在性、vld 单指令、固定转置维度、五段拓扑），
  > 不会做出被 RTL 推翻的结构——3 个 RTL 缺口只**填充**字段细节，不**否定**该拓扑。
- **Stage-II（时序，RTL-gated）**：真 step-driven 数据流（D5 Stage-II）+ interleave 4 并发（intlv_*）+ INST_DONE
  outstanding + 真 latency k + COND 就绪同步。gated on NEEDS-USER-FACT-034-CRI/-IQDRIVE/-DSTDONE + VCS。

### D7. 可证伪验收门
- `test_smi_ds_t_iq_binding`（**hco：专杀 D2 配置链漏改**）：`get_iq_ids("SMI_DS_T")` 返回**非空**、base id 不与
  SHIFTUP(29)/UR_READ(30)/SHIFTDN(34)/UR_WRITE(35) 冲突、`res_to_iqs["SMI_DS_T"] != [0]`（防回退 FPADD slot）。
  比其余门更早暴露三处配置漏改。
- `test_smi_ds_t_emitted_microinst`：DFG 含 SMI_DS_T op，emitter 发出 IQ_SMI_DS_T 的表598 微指令；**断言到
  BitPacker 输出 hex 比特层**（5bit 窄微指令端序/对齐，非仅 payload dict——SMI_DS_T=5bit 是窄单元，端序路径未必测过）。
- `test_smi_ds_t_in_dfg_between_shiftdn_shiftup`：SMI_DS_T op 输入边来自 SHIFTDN、输出边去 SHIFTUP（拓扑断言）。
- `test_smt_sees_gather_transpose_scatter_dep`：SMT 调度里 scatter 的 t > transpose 的 t > gather 的 t（依赖边存在，
  专杀 model-A 静默乱序；承接 ADR-033 B-BLOCK-1 的 gather-before-scatter）。
- `test_matmul_via_instruction_driven_smi_ds_t`：matmul_q_dst(_k8) 经指令流 e2e 8/8 bit-perfect，**反证**：
  破坏 SMI_DS_T 微指令（vld=0）→ FAIL。⚠ hco caveat：本门验 sim 终值（model-A）、上一门验 SMT 边——
  Stage-I 两者**各自独立绿**，step-driven 真数据流贯通是 Stage-II，勿把两门各绿当「真数据流已验」。
- `test_no_simulation_only_no_orphan`：去 simulation_only 后 SMI_DS_T 被 IQ_SMI_DS_T 引用（无孤儿单元），
  且 matmul_q 等不含 SMI_DS_T 的算子不受影响（死 module 反向检查）。

## Consequences

### 正面
- SMI_DS_T 对齐 RTL 真模型（指令驱动单元），消除 ADR-033 Stage-A 的 simulation_only 捷径忠实度缺口（台账 D-13）。
- SHIFTDN→SMI_DS_T→SHIFTUP 依赖进 SMT = 真跨-SMC 数据流时序可被调度器看见（milestone a），为 VCS 对齐铺路。
- 转置数值核（SmiDsTUnit）复用，数值正确性不回退。

### 风险 / 债务
- **DEBT-034-IQ-DRIVE**：IQ_SMI_DS_T 的 CRI 驱动方式（哪个 WRI 写、EN_LOOPS/EXTRA_DATA 配置）文档未全；
  Stage-I 取最小可行配置，真配 gated on RTL。
- **去 simulation_only 的回归面**：SMI_DS_T 进 emitter/solver 消费链，须确保所有路径把它当正常单元处理
  （BitPacker/payload_factory/codebits/**packer.py:23,62 `if u_type not in spec.modules`** 一致）。SMI_DS_T=5bit 是
  **窄微指令单元**（vs SHIFTDN=129bit 宽），BitPacker 窄单元端序/对齐路径未必测过 → D7 `test_smi_ds_t_emitted_microinst`
  须断言到 hex 比特层。Stage-I 必跑全量 e2e + pytest 确认无回归。
- **SMT II 影响**：新依赖边可能抬升 II（gather→transpose→scatter 串行）；需实测不破现有 SAT。
- **缩放 ≠ 真时序**：Stage-I 仍 latency=1 model-A 近似，不验 4 并发 interleave 真时序；升 Accepted 待 VCS
  （still_holds: proposed）。

## Open questions —— 2026-06-10 doc 复核：3 个 NEEDS-USER-FACT **全部 doc-resolved**（非 RTL gap）

> user 订正「缺少哪个微指令，文档中都是有具体的微指令」属实。复跑 `spec_doc_align.py` + 逐表精读 v0.4.19
> 表593-598 + para876-970，3 个原标「gated on RTL」的缺口**实为文档已定**，仅**逐拍时序**才是 VCS-gated。
> 原 NEEDS-USER-FACT 过度保守，订正如下：

1. ~~**034-CRI**~~ **✅ DOC-RESOLVED**：**CRI = 表595「SMI_IQ 微指令格式」(VLD/JUMP/EMBED/EMBED_END/LOOPS/
   COND/DLY/INC/INC_EMBED/**CODE**) 的 INST 输出**（表593 `INST={CODE...}`）。其中 **CODE 字段 = 表598 的 5bit
   SMI_DS_T 专属 payload**（vld+intlv_*）。**emitter 现有 ISA_V5 header(41) + 5bit body 已是此结构**——
   header=SMI_IQ 框架、body=CODE。表598 5 字段**就是**完整 SMI_DS_T payload（无隐藏 inst_id）。
   **4 并发区分**（para961）：靠 `intlv_grp`(1bit 分 2 组) + **每两指令前后 2 拍启动、数据间插读取**；
   inst_id 是 **SMI_IQ 级**（表593 INST_ID=6bit / CRD_I 的 inst_id），不在 SMI_DS_T 微指令内。
2. ~~**034-IQDRIVE**~~ **✅ DOC-RESOLVED**：CRI 由**自身专属 SMI_IQ** 驱动（para894「一般 SMI_IQ 控一模块」）；
   **输出**去「SHIFTUP 对应、具 EXTRA_DATA 的 SMI_IQ」(para969)；EN_LOOPS/COND/DLY 是标准 SMI_IQ 参数（表594）。
   **datapath 同步 = COND**（para916：广播/数据整理类长延迟/不定延迟用 COND——「让读广播数据的 SMI_IQ 先行，
   其他模块用 COND 等数据整理完毕进队再同步执行」），正是 SMI_DS_T→SHIFTUP 的就绪同步机制。
3. ~~**034-DSTDONE**~~ **✅ DOC-CLARIFIED**：INST_DONE（表596）= 直接完成输出信号；SMI_DS_T **自管 4 outstanding**
   （表597 PARAM_INST_NUM=4 + para961 内部间插），**不**走 5-CRD-outstanding 路径——para920-925 的 5 个
   OUTSTANDING SMI_IQ 接 CRD_STB/STBAXI/LDB/LDBAXI/**CRD_DS**（per-SMC DS 表585-588，非 SMI_DS_T）。
   原「不在 5 outstanding 列表内」**确认且被解释**：SMI_DS_T 自管完成、下游用 COND 等 DATA_OUT_VLD/INST_DONE。

**仍 VCS-gated（逐拍时序，非微指令定义）**：① 4 并发「每两指令前后 2 拍启动」的精确 stagger；② COND 就绪延迟；
③ INST_DONE/DATA_OUT_VLD 的逐拍 valid 时序。**设计已 doc-full，时序忠实待 VCS**（still_holds: proposed 不变）。

## 文档全表落地（v0.4.19，SMI_DS_T 完整规格）
- **表596 接口**：`CRD_SHIFTDN(129 in) / DATA_OUT(128 out) / DATA_OUT_VLD(1) / CRI(* in) / INST_DONE(1)`。
- **表597 参数**：`PARAM_IN_LINE_BYTE_CNT=PARAM_UR_BYTE_CNT / PARAM_IN_LINE_NUM=PARAM_SMC_NUM / PARAM_INST_NUM=4`。
- **表598 微指令**（5bit）：`vld/intlv_en/intlv_grp/intlv_in/intlv_out`——**与 hw_config/modules.yaml 一致**。
- **功能**（para954-962）：每组取数指令对所有 SMC UR 读 1 行(PARAM_UR_BYTE_CNT 字节)×PARAM_SMC_NUM 行经 SHIFTDN
  传回 SMI；一个 SMI_DS_T 指令把 Tile 转置为 PARAM_UR_BYTE_CNT 行 × PARAM_SMC_NUM 列发出（一行 PARAM_SMC_NUM
  拆多个 PARAM_UR_BYTE_CNT 发）；4 并发、间插读/间插或顺序输出、收发不反压；输出→SHIFTUP EXTRA_DATA IQ。

## Stage-II 设计（doc-grounded，2026-06-10 起草，待 hco）
> Stage-I（Inc1/2a/2b）已让 SMI_DS_T 进指令流 + 指令 VLD 触发（数值 model-A）。Stage-II 把**逐拍数据流 + 4 并发
> interleave + COND 同步 + INST_DONE outstanding** 落到 sim，**设计全部 doc-grounded（上表）**，时序忠实待 VCS。
- **S2-D1 真 step-driven 数据流**：SMI_DS_T 成 cluster.step() 一等 tick 实体——SHIFTDN 链末端 drain 到其
  `CRD_SHIFTDN`（129b）；单元在 step 中消费、转置、出 `DATA_OUT`(128)/`DATA_OUT_VLD` → SHIFTUP；替 2b 的
  `apply_smi_ds_t` model-A 原子段（承接 ADR-033 `test_gather_before_scatter_ordering`）。
- **S2-D2 4 并发 interleave**（表597 PARAM_INST_NUM=4 + para961-962）：内部 4 指令槽，每两指令前后 2 拍启动、
  间插读取；前两指令读完即开始间插/顺序输出。`intlv_en/intlv_grp/intlv_in/intlv_out` 控间插行为。
- **S2-D3 下游就绪同步**（para916，hco N-2）：下游 SHIFTUP SMI_IQ 等 SMI_DS_T 输出就绪，替 Stage-I 的
  「INIT-stagger 相对序近似」（DEBT-034-TRIGGER-TIMING 闭合）。**就绪源文档允许两读法、勿写死单一通路**：
  (a) **COND**-on-INST_RDY（para916「其他模块用 COND 等数据整理完毕」）；(b) **EXTRA_DATA 队列非空**
  （SMI_DS_T 输出送「具 EXTRA_DATA 的 SHIFTUP SMI_IQ」para969，表593 INST_RDY 对附加数据队列 IQ 的拉高条件）。
  逐拍取哪个待 VCS。
- **S2-D4 INST_DONE 内部 outstanding**：单元自管 ≤4 outstanding（表597 PARAM_INST_NUM=4，非 5-CRD 路径）；
  INST_DONE 出。**回收路径 = doc-未直证外推（hco N-1）**：文档明确 STB/LDB/DS 的 outstanding 走 CRD 下行回
  OUTSTANDING≠0 SMI_IQ，但 **SMI_DS_T 的 INST_DONE 接哪个 SMI_IQ 的 CRD_I、是否需专属 OUTSTANDING≠0 IQ 文档留白**
  → gated on NEEDS-USER-FACT-034-DSTDONE。S2-D4「驱动 SMI_IQ 据此发新指令」是合理外推非 doc 直证。
- **S2-D5 真 latency k**：SMT 的 SMI_DS_T latency 从 Stage-I model-A=1 改为 doc 时序导出（含 2 拍间插启动间隔 +
  转置 + 输出延迟），闭合 DEBT-034-LOOPS-REDUNDANT 的「单次转置忠实」（assembler 支持 per-IQ 迭代/inst 数）。
- **验收门（待定）**：逐拍 DATA_OUT_VLD 时序断言 / 4 并发 interleave 顺序断言 / 下游就绪门控（COND 或
  EXTRA_DATA 队列）可证伪 / e2e 仍 8/8。**升 Accepted 仍待 VCS 对齐逐拍**（设计 doc-full ≠ 时序 RTL-verified）。
- **hco N-3（header 可选字段）**：表594 对 IQ_SMI_DS_T 的 EN_LOOPS/EN_COND/EN_DLY/EN_INC enable 位 + BITS_CODE
  文档 V1.0 列留空 → IQ_SMI_DS_T 的 header 字段配置取**最小可行、待 RTL/逐拍确认**，勿当 doc-confirmed。

### HCO 复核（doc 解析 + Stage-II 设计，agentId `ace9c99d339deb4ea`）
**APPROVE-with-notes**。5 条 doc 解析主张全 ACCEPT（逐句对原文表585-598 + SMI 功能段）：① CRI=表595 header+CODE(表598)
✓ ② 4 并发=intlv_grp+2拍 stagger、inst_id 在 SMI_IQ 级 ✓ ③ **CRD_DS=per-SMC DS(表585-588)≠SMI_DS_T，无 D-01 混淆** ✓
④ COND 同步家族正确（就绪源 COND/EXTRA_DATA 二读法）✓ ⑤ **转置维度未写反**（PARAM_SMC_NUM 行×PARAM_UR_BYTE_CNT
列 → 转 PARAM_UR_BYTE_CNT 行×PARAM_SMC_NUM 列）✓。无 BLOCK。3 NOTE 已并入 S2-D3/D4 + N-3。建议补 doc-契约回归测试
（`test_smi_ds_t_microinst_matches_doc_table598`：固化表598 5 字段、专杀未来把 DS 表587 的 inst_id/smc_id_en 串进
SMI_DS_T 的 D-01 回归）。`still_holds: proposed` 保持（设计 doc-full ≠ 时序 RTL-verified）。

### Increment S2-D1 ✅ DONE（2026-06-10）—— 真 step-driven 数据流（表596 接口，替 apply backdoor）
- **SmiDsTUnit 升一等 step-driven 单元**（`sim/.../units/smi_ds_t_unit.py`）：加表596 接口寄存器
  `DATA_OUT(128)/DATA_OUT_VLD/INST_DONE` + 方法 `load_tile(CRD_SHIFTDN gather)→execute(转置+建 DATA_OUT 队列，
  gather-before-scatter gate)→emit_step(逐词发 DATA_OUT，末词 INST_DONE)`。一行 smc_num 元素拆 ceil/word_lanes
  个 128b 词（para959）。
- **cluster 持一等单元 + 逐拍驱动**（`cluster.py`）：`setup_smi_ds_t_trigger` 建 `self.smi_ds_t_unit`；
  `_tick_smi_ds_t_trigger` 重构——VLD 触发 → gather 各 SMC 列对齐 UR 行 latch 进 CRD_SHIFTDN → execute 转置 →
  **每拍 emit_step 发一个 DATA_OUT 词 → scatter 写 SMC_0 bank0**（替 2b 的 `apply_smi_ds_t` 一次性 storage-to-storage
  backdoor）。数据**真经单元 DATA_OUT 寄存器逐词流出**（standalone 验：4 行 4 拍 spread + DATA_OUT_VLD 每拍 + INST_DONE 末拍）。
- **验收**：matmul_q_dst/_k8 e2e **8/8 bit-perfect**；全量 pytest 126 pass（+3 S2-D1 单测：`test_data_out_emitted_word_by_word_with_inst_done`
  / `test_execute_gates_on_gather_complete` / `test_multiword_row_split_k8`）+ 5 算子 e2e 8/8。
- **诚实边界（仍 model-A / VCS-gated）**：① **gather 仍读各 SMC SRC_BANK**（SHIFTDN 链物理 drain 到 CRD_SHIFTDN
  是 chain-shift 模型、N 拍串行 drain 未建——SHIFTDN unit 是单值链移非 N 列并行 gather，真 drain 待 S2 后续/VCS）；
  ② **逐拍 emit 间隔=1 拍**（非 doc para961 的 4 并发 2 拍 stagger，那是 S2-D2 interleave）；③ scatter 起拍仍靠
  INIT-stagger 相对序（DEBT-034-TRIGGER-TIMING 未闭合，S2-D3 COND 才闭）。**S2-D1 交付 = 真单元 + 表596 接口 +
  DATA_OUT 逐词流 + gather gate + INST_DONE 信号**（结构忠实），逐拍时序忠实仍 S2-D2/D3 + VCS。
- **⚠ 时序耦合（evaluator a7afb80 量化）**：当前 8/8 是 **scatter 逐词写(1 词/拍) 与 a_read 逐行读(1 行/拍)
  同速率 → 同拍 0-容差对齐**的产物，**非「写一定先于读」**。反证：把 emit 间隔从 1 拍拉到 2 拍 → word0 落对、
  word1+ 读到 stale → FAIL。即此 datapath 对任何改变 emit/read 相对速率的改动都脆——这正是 DEBT-034-TRIGGER-TIMING
  的数值体现，**S2-D3 COND 同步闭合前不要动 emit/read 速率**。
- **apply_smi_ds_t 保留**：作 fallback（无指令流时）+ 既有 unit 测试（TestSmiDsTClusterApply）数值核，未删。

## 实施进展（Stage-I）

### Increment 1 ✅ DONE（2026-06-10，commit `571f48b`）—— 配置链（hco D2 BLOCK）
- modules.yaml 去 `simulation_only`；三处 IQ 配置：iq_config `IQ_SMI_DS_T`（BITS_CODE=5）+ unit_instances `SMI_DS_T:1`
  + iq_registry `SMI_DS_T:40`。`get_iq_ids("SMI_DS_T")=[40]`（漏改则返 `[]` 回退 slot 0 撞 FPADD）。
- ⚠ **SSoT 修正**：hw_spec.py:163 master 文件存在时 `system_core` 取 `aiacc_v2.4_master.yaml` 嵌入版，
  `specs/system/system_core.yaml` 是 **fallback（被忽略）**。hco 原指 system_core.yaml 实为 fallback；真 SSoT 是 master。两处都改（master live + fallback 同步）。
- `test_smi_ds_t_iq_binding` 守门（替原 simulation_only 测试）。回归：5 算子 e2e 8/8 + 全量 pytest 118 pass。
- 现状：SMI_DS_T 已注册为指令驱动真单元，但**尚无 op 引用它**（算子仍走 sim_config apply_smi_ds_t 捷径）。

### Increment 2 设计（待实施，已侦查可行性）—— DFG 节点 + emit + SMT + sim 触发
- `ResourceType.SMI_DS_T` 已加（hw_resource.py，commit 见 Increment 1.5）。**SMT 侧已侦查可行**：`res_to_iqs`
  从 `iq_registry.keys()` 建（ucode_solver.py:36）→ SMI_DS_T 自动得 `[40]`；`_get_resource_type("SMI_DS_T")="SMI_DS_T"`；
  latency `delays.get("SMI_DS_T",1)=1`。故 SMI_DS_T op 进 DFG 后 SMT 应能调度到 iq 40、无冲突。
- **待做**：(1) 算子 get_dfg 加 SMI_DS_T op（a_read depends_on 它，转置先于广播读 bank0）；
  (2) emit 验证表598 微指令进流（payload_factory 从 modules.yaml code_fields 建；需核 iq_id 40 → iq_name → resolve_iq_type 链）；
  (3) sim 指令触发（cluster 检测 SMI_DS_T IQ fire → 触发 model-A 转置，替 run-前 apply_smi_ds_t）。
- **已知风险**（Increment 2 主要难点）：① **单次触发的 SMI_DS_T op vs a_read 的 M-loop 结构错配**（SMI_DS_T 转置一次产 M 行、a_read 循环 M 次读）——
  需作 prologue 或 inner_inc=0 idempotent；② **SMI 中心单实例在 N-kernel model-A sim 的触发**（SMI_DS_T 非 per-SMC，
  应 cluster 级触发一次，非每 kernel）；③ iq_name 解析链对 id 40 的处理未验。
- **D5 Stage-I caveat**：Increment 2 可拆 2a（DFG 节点 + emit + SMT，sim 数值暂留 run-前，验「结构」）+ 2b（sim 指令触发，验「指令驱动数值」）。

### Increment 2a ✅ DONE（2026-06-10）—— DFG 指令节点 + emit + SMT 依赖边（验「结构」）
- **DFG**（`matmul_op.py` 新增 `_inject_smi_ds_t_transpose` + `ManualMatmulQDsTOperator.get_dfg` override
  + `ManualMatmulQDsTK8Operator.get_dfg` 追加）：注入 `ds_gather(SHIFTDN,iq34) → ds_transpose(SMI_DS_T,iq40)`
  两节点，令 `a_read` 依赖 `ds_transpose`。**复用现有 broadcast `shiftup_a_read` 作 scatter**（不另建第二
  SHIFTUP，避开 sim 单 shiftup 实例 + multi-broadcast 限制）。transpose-path 单次 fire（`inner_loops=0`，
  解 known-risk ① M-loop 错配）。
- **SMT**：`UCodeSynthesizer.solve_with_units` II=1 SAT，调度 `ds_gather(t=0) < ds_transpose(t=1) <
  a_read(t=2) < shiftup_a_read(t=3)`——调度器看见 **gather→transpose→scatter** 依赖边（D4 / ADR-033 B-BLOCK-1 (a)）。
  res_to_iqs 自动映射 SMI_DS_T→[40]、SHIFTDN→[34]，无回退 slot 0。
- **emit**：`iq_smi-ds-t-0.hex` 真产出（`get_iq_name(40,"SMI_DS_T")="smi_ds_t-0"`，emitter bucket → hex）；
  5bit 窄微指令 body 端序经 BitPacker 验证（VALID@bit0、VLD header@bit45、总宽 46bit）。
- **sim（2a 边界，D5 Stage-I caveat）**：数值仍 100% 由 `cluster.apply_smi_ds_t`（run-前 model-A）提供——
  `smi-ds-t-0` 经 `load_program` 无同名 IQ → **静默 skip**（不扰动）；`ds_gather`(SHIFTDN) 单次 fire +
  smc_id_en 门控、不写 compute 输入 bank0 → 经实测不扰动。**SMT 依赖边（结构）与 sim 终值（model-A）是
  两条独立验证**，step-driven 真数据流（SHIFTDN drain→CRD_SHIFTDN→SMI_DS_T tick）是 **2b/Stage-II**。
- **验收**：matmul_q_dst / matmul_q_dst_k8 经指令流 e2e **8/8 bit-perfect**（数值不变，cycle 69→71 = prologue 2 拍）；
  matmul_q（无 SMI_DS_T 节点）不受影响（死 module 反向检查）；全量 pytest 121 pass（+3 D7 结构性单测）+ 5 算子 e2e 8/8。
- **D7 门覆盖**：`test_smi_ds_t_in_dfg_between_shiftdn_shiftup`（拓扑）/ `test_smt_sees_gather_transpose_scatter_dep`
  （SMT 依赖边）/ `test_smi_ds_t_emitted_microinst_bit_layer`（emit 比特层 + 反证）已落 `tests/unit/test_smi_ds_t.py`。
  `test_matmul_via_instruction_driven_smi_ds_t`（e2e 反证 vld=0→FAIL）留 2b（需 sim 真消费 SMI_DS_T 指令才有意义）。
- **未做（2b）**：sim 端加 `smi_ds_t` IQ + 解码器 + 由 vld 触发 SmiDsTUnit 转置核（替 apply_smi_ds_t），
  此时 emit 命名需对齐（`get_iq_name` whitelist 加 SMI_DS_T → "smi_ds_t" 才能 load_program 匹配；
  现 "smi-ds-t-0" 被 skip 是 2a 的有意设计）。known-risk ②（cluster 级单次触发非每 kernel）/ ③ 在 2b 处理。

### Increment 2b ✅ DONE（2026-06-10）—— sim 指令触发（验「指令驱动数值」）
- **触发机制（cluster 级单实例，解 known-risk ②）**：`SystemCluster.setup_smi_ds_t_trigger` + `_tick_smi_ds_t_trigger`
  （`cluster.py`）——cluster 持**一个** SMI_DS_T IQ（非 per-kernel），在 `step()` 最前（kernels step 前）
  evaluate；其指令 VLD 帧 `exec_active` 首次为真即触发 `apply_smi_ds_t` 一次（`smi_ds_t_fired` guard 防重复），
  写 SMC_0 bank0 先于本拍 a_read 读。`physical_runner._run_multi_smc` 改：**删 run-前无条件 apply**，
  从 iq_init 找 `smi_ds_t` 指令流 hex → `setup_smi_ds_t_trigger`（无流则 fallback 旧原子段）。
- **解码器 SSoT（解 known-risk ③）**：`spec_loader.get_unit("SMI_DS_T")` 返 None（无 `specs/units/smi_ds_t.yaml`）
  → payload_width=0 → VLD 偏移读 bit40 而非 bit45 → 永不触发。**改从 `kernels[0].hw_spec.modules["SMI_DS_T"]`
  （与 emitter 同一 SSoT=hw_config/modules.yaml）建 `InstructionDecoder`** → payload_width=5、VLD@bit45 正确。
  **不新建 `hw_config/specs/units/smi_ds_t.yaml`**（避免 hw_config 触点 + SSoT 分裂）。
- **数值核**：仍 model-A（`apply_smi_ds_t` 复用 `SmiDsTUnit` 转置核 + gather）；2b 只把**触发**从 run-前无条件
  改为指令 VLD 驱动。指令-驱动数值贯通：a_read 在转置触发后读 bank0（emit INIT-stagger 保 t[transpose]=1 <
  t[a_read]=2 的相对序 → 转置必先于 a_read 读，K=4/K=8 实测成立）。
- **验收**：matmul_q_dst / matmul_q_dst_k8 经指令触发 e2e **8/8 bit-perfect**；全量 pytest 122 pass
  （+1 反证单测 `test_instruction_driven_vld_gates_transpose`：VLD=1→触发+bank0 行对齐；**VLD=0→不触发+bank0 保 0**，
  证明转置真由指令 VLD 门控、非 run-前 hook）+ 5 算子 e2e 8/8。
- **债务**：① 转置指令在流中按 `num_iterations`(M) 重复 emit（assembler 不读 `inner_loops`，按全局迭代数）——
  `smi_ds_t_fired` guard 只 apply 一次、首触后停止驱动该 IQ，故**冗余无害**，但 emit 流非「单次转置」忠实
  （DEBT-034-LOOPS-REDUNDANT，真修需 assembler 支持 per-IQ 迭代数）。② 触发时机（cycle 3 起）依赖 INIT-stagger
  相对序，非逐拍 RTL 时序（仍 model-A，DEBT-034-TRIGGER-TIMING）。③ 升 Accepted 待 VCS（still_holds: proposed）。
- **未做（Stage-II）**：真 step-driven 数据流（SHIFTDN 链 drain→CRD_SHIFTDN→SMI_DS_T tick 出 DATA_OUT/INST_DONE，
  D5 Stage-II）+ interleave 4 并发 + 真 latency k，gated on NEEDS-USER-FACT-034-CRI/-IQDRIVE/-DSTDONE + VCS。

## HCO 复核（agentId `a65bb21037896d94e`）
**APPROVE-with-notes**。核心主张（SMI_DS_T 是指令驱动单元、应进指令流非 simulation_only）**成立**：表596（CRI+DATA_OUT+INST_DONE）
/ 表598（微指令）/ 表597（PARAM_INST_NUM=4）与 UR/FPMUL 同构，位 SMI 微架构层。Stage-I/II 切分诚实，Stage-I 只用
已成文结构、不会被 RTL 推翻。2 个合入前必补阻断项已收口：**D2**（配置链漏改 `system_core.yaml` 的 unit_instances+iq_registry
→ 否则 get_iq_ids 返 [] 回退 slot 0 撞 FPADD，已补三处 + 新 base id 避让）、**D5**（sim 数据流阶段裁定 → Stage-I 指令触发+model-A
数值、Stage-II 真 step-driven，显式 caveat 防 SMT 边与 sim 值各绿中间断开）。订正：表号 512→523、补 [seq967-969] IQ 通则依据、
补 packer 窄微指令消费点 + iq_binding 守门测试。`still_holds: proposed`，升 Accepted 待 VCS。

## References
- [ADR-033](ADR-033-smi-ds-t-cross-smc-transpose-datapath.md)（SMI_DS_T datapath + Stage-A model-A；本 ADR 执行其 milestone a，
  Stage-I 落地后 partial-supersede Stage-A 的 simulation_only 建模）
- [理解文档 docs/spec/aiacc-v0.4.19-understanding.md](../spec/aiacc-v0.4.19-understanding.md)（A4 补注 + 台账 D-13）
- v0.4.19 docx：表596（SMI_DS_T 接口，CRI/DATA_OUT/INST_DONE）/ 表597（设计参数）/ 表598（微指令）/
  `[seq1031-1051]`（功能 + 5 段 datapath + 输出去 SHIFTUP EXTRA_DATA IQ）；对照 表512（FPMUL）/ 表563（UR）/
  表593-595（SMI_IQ）
- [ADR-022](ADR-022-single-smi-global-instruction-stream-model.md) 单 SMI 全局指令流 / [ADR-031](ADR-031-smc-id-en-all-units-unify.md) 全单元字段
- 代码触点：`hw_config/modules.yaml`（去 simulation_only）/ `hw_config/iq_config.yaml`（新 IQ_SMI_DS_T）/
  **`hw_config/specs/system/system_core.yaml`（D2 hco BLOCK：`unit_instances.SMI_DS_T=1` + `iq_registry.SMI_DS_T=<新 base id>`，
  specs/system 改动触发 hco）** / `src/api/hw_spec.py:205-237`（get_iq_ids 行为，base id 取此）/
  `src/synthesis/ucode_solver.py:449-460`（valid_iqs 回退 [0] 撞 FPADD 的根因，SMT 依赖）/
  `sim/smc_simulator/units/smi_ds_t_unit.py`（升指令驱动 sim 单元，复用转置核）/
  `sim/smc_simulator/system/cluster.py:82-102 vs 129-167`（SHIFTDN 链 vs apply_smi_ds_t 原子段，Stage-II 接真数据流）/
  `verification_v2/ops/matmul_op.py`（get_dfg 加 SMI_DS_T 节点）/ `src/driver/{emitter,payload_factory,packer}.py`（发 SMI_DS_T 微指令）
