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

## Open questions / NEEDS-USER-FACT（gated on RTL）
1. **NEEDS-USER-FACT-034-CRI**（承接 033-CRI）：CRI 宽度（`*`）；表598 的 5 字段是 CRI 完整 payload 吗？
   4 并发（PARAM_INST_NUM=4）如何区分指令槽（intlv_grp 仅 1bit 分 2 组，无 inst_id）？
2. **NEEDS-USER-FACT-034-IQDRIVE**：SMI_DS_T 的 CRI 由哪个 SMI_IQ 驱动？其 EN_LOOPS/EXTRA_DATA/COND/DLY 配置？
   （文档只说**输出**去 SHIFTUP 的 EXTRA_DATA IQ`[seq1050]`，未说 CRI 来源 IQ。）
3. **NEEDS-USER-FACT-034-DSTDONE**（承接 033-DSTDONE）：INST_DONE 回收路径——不在 [seq1003] 的 5 个
   outstanding SMI_IQ 列表内，接哪个 SMI-IS？

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
