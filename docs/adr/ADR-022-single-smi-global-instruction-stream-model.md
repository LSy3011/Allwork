---
adr_id: ADR-022
title: 修正硬件模型 —— 单 SMI 全局指令流 + SMC_ID/smc_id_en 寻址（per-SMC emitter/sim 模型从根上错）
decided: 2026-05-29
last_verified: 2026-05-29
still_holds: proposed
supersedes_decision_in: [ADR-018, ADR-019, ADR-020, ADR-021]
---

# ADR-022 · 单 SMI 全局指令流硬件模型（修正 multi-SMC emitter / simulator 根模型）

> **状态：Proposed → hco APPROVE-with-notes（仅文档落定，代码未改）** —— 本 ADR 由 user 直接给出 RTL spec（表 542–548 SHIFTUP/SHIFTDN 接口 + 微指令 + 功能式）纠正了贯穿 ADR-018→021 的 multi-SMC 心智模型。**代码（emitter + simulator）尚未重构**；本 ADR 先锁正确模型 + 影响面，分阶段实施。hco 全新 context 独立复核（agentId `a240be2f`）：RTL 解读准确、emitter/simulator 偏离指认属实站得住，模型判定可凭 RTL 推导 Accept；补充 3 项已并入（SHIFTDN smc_id_en spec-gap 已被新 RTL 推翻、simulator OQ-1 数据源作废、Accepted 证据门槛）。
>
> **升 Accepted 门槛**：模型判定（单 SMI 全局流）凭 RTL 推导充分，可先 Accept；但「现有实现产出*错误*固件」这一更强主张待 **8-SMC 固件 vs VCS 实测**背书（VCS 分歧目前是推导预测，非实测）。
>
> **一句话**：硬件是**一个 SMI（单一指令源）+ 一份全局指令流**，指令顺 `CRU` 移位寄存器逐级流过 SMC 阵列，每个 SMC 靠自身 `SMC_ID`（配合指令 `smc_id_en` 使能范围）匹配执行；SHIFTUP/SHIFTDN 是**跨-SMC 数据搬运**原语（上行写 / 下行收）。当前 emitter 给每 SMC 各发一份 `smc_k/iq_*.hex`（8 份独立指令流 + per-SMC INIT stagger）+ Python 仿真器同构建模，**从根上偏离 RTL**。

## Context

### 误解的来源

ADR-018（multi-SMC scheduling）→ ADR-019（shift cross-smc yaml/sim 对齐）→ ADR-020（SHIFTUP IQ BODY loop）→ ADR-021（删 fold → fma 32/32 bit-perfect）一脉相承地建立在一个**未经 RTL spec 验证的 multi-SMC 模型**上：

- 假设每个 SMC 有**独立指令流**，编译器为 SMC_0..N-1 各发一份 `smc_k/iq_*.hex`；
- 假设 `parallel_dim="N"` 走 SHIFTUP、`parallel_dim="K"` 走 SHIFTDN，是两条由**算子语义**决定的互斥路径；
- 用 emitter 的 per-SMC INIT stagger（ADR-020 v6-4 `_compute_per_smc_stagger`）补偿"chain 物理传播延迟"。

ADR-021 拿到的「32/32、8/8 bit-perfect」是 **Python 仿真器 vs golden** 的自洽——但仿真器本身也按 per-SMC 独立流建模，**sim 自洽 ≠ 与 RTL 一致**。

### user 给出的 RTL spec（权威，表 542–548）

**SHIFTUP（表 542/544，功能式）**——指令携带 data，沿链上行写入：
```
if SMC_ID<=smc_id_en:                       # 指令逐级上行 SMC_0→SMC_{smc_id_en}
    SMC[SMC_ID]/CRU_SHIFTUP_O = SMC[SMC_ID]/CRU_SHIFTUP_I
if broadcast==0 && SMC_ID==smc_id_en:       # 单写：仅目的 SMC
    SMC[SMC_ID]/DR_SHIFTUP_D = CRU_SHIFTUP_O[data]
if broadcast==1 && SMC_ID<=smc_id_en:       # 广播写：所有 ≤smc_id_en
    SMC[SMC_ID]/DR_SHIFTUP_D = CRU_SHIFTUP_O[data]
```
- `CRU_SHIFTUP`（135b = vld + data[128] + broadcast + smc_id_en[5]）；写入的 data **来自指令自身**（由 SMI 注入），不是另一 SMC 的寄存器。
- smc_id_en 语义：broadcast=0 时是**目的 SMC ID**；broadcast=1 时是**使能上界**（`smc_id_en>=SMC_ID` 则本 SMC 被使能）。spec 明注「此处定义与一般计算模块有差别」。

**SHIFTDN（表 545/547/548，功能式）**——读源 SMC 数据，沿链下行回收：
```
if broadcast==0 && SMC_ID==smc_id_en:       # 单读：仅源 SMC 出数
    SMC[SMC_ID]/CRD_SHIFTDN_O[128:1] = DVR_SHIFTDN_S
    SMC[SMC_ID]/CRD_SHIFTDN_O[0] = 1
if broadcast==1 && SMC_ID<=smc_id_en:       # 广播读：所有 ≤smc_id_en
    SMC[SMC_ID]/CRD_SHIFTDN_O[128:1] = DVR_SHIFTDN_S
    SMC[SMC_ID]/CRD_SHIFTDN_O[0] = 1
if CRD_SHIFTDN_I[0]==1:                      # 下行回收 SMC_{src}→SMC_0
    SMC[SMC_ID]/CRD_SHIFTDN_O = SMC[SMC_ID]/CRD_SHIFTDN_I
```
- 上行选择指令 `CRU_SHIFTDN`（7b = vld + broadcast + smc_id_en）选源；源 SMC 把 `DVR_SHIFTDN_S`（计算输入数据）锁进下行寄存器 `CRD_SHIFTDN_O`，再沿链下行回收到 SMC_0。

> **⚠ 新 RTL 推翻旧 spec-gap（hco 复核发现）**：本次表 547 **明确列了 `smc_id_en(5)`**。而现有代码三处仍写「SHIFTDN spec gap — 表均未列，sim fallback PARAM_SMC_NUM-1=31」（`sim/smc_simulator/units/shift_dn_unit.py:17/20/60`、`hw_config/specs/units/shiftdn.yaml:27/43`、`hw_config/specs/aiacc_v2.4_master.yaml:798`，源自 ADR-019 errata task #19）。即本次 spec 相比 ADR-019 时代**已补全 SHIFTDN smc_id_en**，「fallback 31」的存在前提失效。实施阶段须把 smc_id_en 改为**从指令字段解码**而非 fallback（独立待办，见下，不是「硬编码改派生」那一条）。

### 修正后的硬件模型（本 ADR 锁定，user 逐点确认）

1. **单 SMI + 一份全局指令流**：一个指令源持有一份程序，指令顺 `CRU` 移位寄存器逐级流过 SMC 阵列。
2. **SMC_ID 寻址**：每个 SMC 有自身 5-bit `SMC_ID`；指令带 `smc_id_en` 使能范围；SMC 靠 `SMC_ID<=smc_id_en`（或 `==`）判断「这条是否给我」。**所有指令**（含 FPMUL/FPADD/RTOVR/UR 本地计算指令）都经此分发——user 确认 (A) 而非「per-SMC 本地流」(B)。
3. **per-SMC 本地资源**：每个 SMC 有自己的计算单元 + 用户寄存器（UR bank）。同一条指令在被使能的多个 SMC 上各自跑**本地数据**。
4. **SHIFTUP/SHIFTDN = 跨-SMC 数据搬运**：上行写（数据随指令 data 字段携带，broadcast/单点写入目标 SMC）/ 下行收（读源 SMC 的计算数据回收到 SMC_0）。smc_id_en × broadcast 组合出「单点 / 范围」四种寻址。

### 对 `fma_parallel_n`（y=a*b+c）的正确数据通路

| 张量 | 通路 |
|---|---|
| `a`（广播） | **1 条 SHIFTUP broadcast 指令**（smc_id_en=N-1, data=a），写到全部 N 个 SMC 的 DR_SHIFTUP_D |
| `b` / `c`（切分） | 各 SMC **本地 UR bank** 预载（`ur_data/bank*.hex`），无跨-SMC 搬运 |
| `y`（切分） | 各 SMC 本地算 `a*b+c`，结果留本地 bank；SMC_0 若需汇总才用 SHIFTDN gather（本测例 testbench 逐 SMC 读 bank，不需要） |

对照当前 emitter 产物：每个 `smc_k/` 都有完整 `iq_shiftup/fpadd/fpmul/rtovr/ur*.hex` = 「N 份独立指令流」的错误形态。正确应是**一份全局指令流**，其中 `a` 的分发是**单条** SHIFTUP broadcast，而非每 SMC 各发一条。

## Decision

1. **采纳修正后的硬件模型**（上节 4 点）为 multi-SMC 编译/仿真的唯一权威模型，依据 RTL spec 表 542–548。

2. **判定当前实现从根上错**：
   - **emitter**：`src/driver/emitter.py:_emit_multi_smc` 的「per-SMC `smc_k/iq_*.hex` + `_compute_per_smc_stagger` INIT shift」模型作废。正确固件 = **一份全局指令流**（指令带 SMC_ID/smc_id_en），UR 数据仍 per-SMC。
   - **simulator**：`sim/smc_simulator/tools/physical_runner.py:_run_multi_smc`（`SystemCluster(num_kernels=smc_num)` 各跑独立 program）需重建为**单 SMI 驱动 + shift-fabric（CRU 指令传播 + SMC_ID 匹配 + SHIFTUP/SHIFTDN 跨-SMC 搬运）**。
   - **SMT 调度层**（ADR-021 删 fold 的成果）需重新审视：删 fold 在 per-SMC 模型下让 sim 数值对齐，但正确模型下「chain 传播」由 RTL shift-fabric 原生表达，SMT 该建模什么需在实施阶段重新推导。

3. **supersede 前序决策的实现机制**（非全盘否定，见 §Supersedes）：ADR-018 的 per-SMC scheduling、ADR-020 v6-4 的 emitter per-SMC INIT stagger、ADR-021 的「删 fold → 32/32」**bit-perfect 结论本身**是 sim-vs-golden 自洽，但其依赖的 per-SMC 模型被本 ADR 推翻；这些机制在重构后大概率不再需要。

4. **`PARAM_SMC_NUM=8`**（本 session 已改，hco APPROVE-with-notes，agentId `afb8489a`）：真实流片是 8 SMC，spec 上界 32。仓库默认值定为 **8**。supersede ADR-021「保持 32」决策。hco 确认 8<16，LOGIC 4-bit / SMC_ID 5-bit 字段全安全，无活跃路径硬编码隐患。

5. **实施分阶段、文档先行**（ADR-004）：本 ADR 只落模型 + 判定。emitter / simulator 重构、SMT 层重新推导各为独立后续步骤，每步过 hco + evaluator + 全回归后才升 Accepted。

## Supersedes / Reverses

- **ADR-018**（multi-SMC scheduling）：per-SMC 独立指令流前提 → 改为单 SMI 全局流。
- **ADR-019**（shift cross-smc yaml/sim 对齐）：建立在 per-SMC sim 模型上，需随仿真器重建复核。
- **ADR-020**（SHIFTUP IQ BODY loop + v6-4 per-SMC INIT stagger）：stagger 是 per-SMC 模型的物理补偿，正确模型下由 shift-fabric 原生表达，机制大概率废弃。
- **ADR-021**（删 fold → 32/32 bit-perfect）：**bit-perfect 结论**是 sim 自洽的真结果，但其所依赖的 per-SMC 模型被推翻；「删 fold」在新模型下是否仍正确需重新推导。ADR-021「保持 PARAM_SMC_NUM=32」决策被本 ADR Decision 4 覆盖（改 8）。

## Consequences

### 正面
- multi-SMC 编译/仿真首次锚定到 RTL spec（表 542–548），而非继承自未验证的心智模型。
- 解释了为何要拿 8-SMC 固件比 VCS：现有 sim 自洽掩盖了 model mismatch，**VCS 比对是发现该 mismatch 的唯一手段**（注：截至本 ADR，VCS 分歧是**预测**，尚未实测确认）。
- 固件结构变简单：N 份重复指令流 → 一份全局流 + per-SMC 数据。

### 风险 / 待回归
- **重构面大，但 emitter ≠ simulator 成本对称（hco 复核订正）**：
  - **emitter 错在封装形态，编码层已部分对齐**：`emitter.py` 已有 `_inject_smc_id`（按 SMC_ID 注入）/ `_should_skip_chain_instr`（smc_id>smc_id_en 跳过 = chain 沿途语义）/ `_zero_broadcast_data_for_followers`（广播 follower data 清 0 = 承认 data 来自 chain head 非本地）——这些正是 RTL `smc_id_en` 寻址 + broadcast 语义的雏形。错的是「N 份物理 hex + per-SMC stagger」的封装，**字段逻辑可复用**，非从零重写。
  - **simulator 才是真正「从根上错」**：`cluster.py:SystemCluster` = N 个独立取指 kernel + chain wire（`_propagate_chain_inputs` 传的是数据 chain output，**不是指令**），**完全没有 CRU 指令传播建模**。把单 SMI 指令分发重建为 sim 核心机制，工作量 > emitter。排期须预期 **simulator 重建 > emitter 重构**，勿当等量工程。
  - 三处（emitter + simulator + 可能 SMT 层）联动改，任一不同步会破 e2e（参 ADR-021 §元教训「fold 三处副本」：改前先穷举所有耦合点）。
- **ADR-021 删 fold 的去留未定 + 现有绿测会失效**：新模型下需重新推导 SMT 该不该建模 chain 延迟；emitter 重构后 ADR-021 的回归基线（fma 32/32）**会失效并需重建**，是「推倒重建」而非「保留现状再叠加」——需 user 接受这一价值取舍。
- **SHIFTDN `smc_id_en` 改由指令字段解码**（独立项，非「硬编码改派生」）：新 RTL 表 547 已列 smc_id_en，`shift_dn_unit.py:17/20/60` + `shiftdn.yaml:27/43` + `aiacc_v2.4_master.yaml:798` 的「spec gap + fallback 31」前提失效，须改为解码指令字段。当前 fma（N 路径，不触发 SHIFTDN 下行 gather）未暴露。
- **`SMC_ID_EN_FALLBACK`/`chain_reduce_depth` 等过期 32-era 默认值**（`configurable.py:24` 注释「默认 31」等）：与 smc_id_en 字段解码一并清理。
- **K 路径在 8-SMC 下未验证**：现有测试矩阵全是 N 路径；重建后需补 SHIFTDN gather 在 8-SMC 下的 parity 用例。
- **多处 docstring/注释仍写死 31/32**（`hw_resource.py:118/125/131`、`pipeline_runner.py:180`、`configurable.py:24/108`、`shift_dn_unit.py:17/20/27`）：随实施批量订正。

## 待办（实施阶段，非本 ADR）
1. **emitter 重构**：单全局指令流 emit（SMC_ID/smc_id_en 编码 + SHIFTUP/SHIFTDN 跨-SMC 搬运），UR 数据保持 per-SMC。复用现有 `_inject_smc_id`/`_should_skip_chain_instr`/`_zero_broadcast_data_for_followers` 字段逻辑，去掉「N 份 hex + per-SMC stagger」封装。
2. **simulator 重建**（成本 > #1）：单 SMI + shift-fabric（CRU 指令逐级传播 + SMC_ID 匹配执行）。**作废 `shift_up_unit.py:57` 的 OQ-1「广播数据源固定 SMC_0 本地 data」模型**——按新 RTL，`DR_SHIFTUP_D` 的 data 来自**指令 data 字段**（SMI 注入），非任何 SMC 本地寄存器。
3. **SHIFTDN smc_id_en 改指令字段解码**（独立项）：新 RTL 表 547 已列 smc_id_en，删除 `shift_dn_unit.py:17/20/60` + `shiftdn.yaml:27/43` + `aiacc_v2.4_master.yaml:798` 的「spec gap + fallback 31」前提。
4. **SMT 层**：重新推导 chain 延迟建模（ADR-021 删 fold 去留），预期 fma 32/32 基线需重建。
5. 过期 32-era 默认值/注释批量订正（`configurable.py:24/108`、`hw_resource.py:118/125/131`、`pipeline_runner.py:180` 等）。
6. 补 K 路径 / SHIFTDN gather 8-SMC parity 测例（用解码 smc_id_en，不走 fallback）；加「未被 smc_id_en 使能的 SMC 不执行该指令」可证伪单测。
7. 每步 hco + evaluator + 全回归。
8. **8-SMC 固件 vs VCS 实测**：背书「现有实现产出错误固件」主张，作为升 Accepted 的最终证据。

## References
- RTL spec 表 542（SHIFTUP 接口）/ 544（SHIFTUP 微指令）/ 545（SHIFTDN 接口）/ 547（SHIFTDN 上行微指令）/ 548（SHIFTDN 下行微指令）+ 各自功能式（user 本 session 提供）。
- [ADR-018](ADR-018-multi-smc-scheduling-design.md) / [ADR-019](ADR-019-shift-cross-smc-yaml-sim-alignment.md) / [ADR-020](ADR-020-shiftup-iq-body-loop-multi-smc.md) / [ADR-021](ADR-021-fold-is-bug-delete-shiftup-rtovr-worstcase-fold.md)
- [ADR-004](ADR-004-doc-first-principle.md) 文档先行
- hco 复核 PARAM_SMC_NUM 32→8：agentId `afb8489a`（APPROVE-with-notes，本 session）
- hco 复核 ADR-022 模型 + emitter/sim 偏离判定：agentId `a240be2f`（APPROVE-with-notes，本 session；3 项补充已并入）
- 代码触点：`src/driver/emitter.py:_emit_multi_smc` / `sim/smc_simulator/tools/physical_runner.py:_run_multi_smc` / `sim/smc_simulator/units/shift_{up,dn}_unit.py` / `verification_v2/ops/configurable.py`
