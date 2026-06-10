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
