---
adr_id: ADR-023
title: 单 SMI 全局指令流的首个落地实现 —— emitter 去 8 份复制 + simulator 模型 A（时序不忠实中转）
decided: 2026-05-29
last_verified: 2026-05-29
still_holds: proposed
implements: ADR-022
supersedes_decision_in: [ADR-020]
---

# ADR-023 · 单 SMI 模型落地（emitter 单全局流 + simulator 模型 A）

> **状态：Proposed（doc-first，代码未改）** —— 本 ADR 是 [ADR-022](ADR-022-single-smi-global-instruction-stream-model.md) 锁定的「单 SMI + 一份全局指令流」硬件模型的**首个工程落地**（ADR-022 待办 #1 emitter + #2 simulator）。HCO 全新 context 复核设计方案：**APPROVE-with-notes**（agentId `a93906710d712e849`），5 条阻断/建议 notes 已并入本 ADR Decision。
>
> **一句话**：emitter 从「per-SMC 8 份 `smc_k/iq_*.hex` + INIT stagger」改为「**一套** `iq_*.hex`，指令带 `smc_id_en`，UR 数据仍 per-SMC」；simulator 取 **模型 A（lockstep 复制）** —— N 个 kernel 加载同一套 hex —— 作为**时序不忠实的中转实现**，足够验收 fma_parallel_n 的终值 parity，但**不验收跨-SMC 时序**；真 CRU shift-fabric（模型 B）记为债务留 ADR-022 待办 #2。

## Context

ADR-022 把硬件模型锁定为单 SMI 全局指令流，但只落了**模型 + 判定**，代码（emitter + simulator）未改。本 ADR 落地 user 在 session 中拍板的两个决定，并把 HCO 复核 notes 固化为实施契约。

### user 已拍板（前提，不在本 ADR 重新论证）
1. **固件目标格式**：一套 per-IQ-unit hex（`iq_shiftup/fpmul/fpadd/rtovr.hex` 各一份），**去掉 8 份 `smc_k/` 复制 + 去掉 per-SMC INIT stagger**；指令带 `smc_id_en`（广播由 `smc_id_en × broadcast` 表达）；**UR 数据仍 per-SMC**（`smc_k/ur_data/bank*.hex`）。`sim_config.json` 新 schema = 单 `iq_init` + `per_smc_ur_init`。
2. **范围**：emitter + simulator **一起改**（共享 sim_config 契约，单改 emitter 会让 e2e 变红），目标 fma_parallel_n hw-parity 重新变绿。`PARAM_SMC_NUM=8`。

### HCO 复核裁定（agentId `a93906710d712e849`，APPROVE-with-notes）
- **[yes]** 固件格式忠实表达表 542–548 单 SMI 模型；SHIFTUP 广播 `a` = **单条指令 `smc_id_en=N-1, broadcast=1`**，data 来自指令字段（RTL 表 542/544，ADR-022:38-41）。删 `_zero_broadcast_data_for_followers`（不该有 follower 行）、删 `_compute_per_smc_stagger` + INIT stagger 正确。`PARAM_SMC_NUM=8` 字段全安全。
- **核心分叉裁定**：模型 A 的 lockstep 抹平 CRU 逐级 1-cycle 传播延迟。对 fma（`a` 广播 SHIFTUP + `b/c` 本地 + `y` 本地算，testbench 逐 SMC 读 bank **终值**），k 拍传播延迟**只改时刻、不改终值** → A=B 对本测例终值等价。**但 A 会掩盖任何依赖 SHIFTUP 到达时序的 bug**（某 SMC 在 `a` 未传到时即算 `a*b+c`，真硬件读旧值/0；A 里 `a` 对所有 SMC「瞬时」可得）。故 A 合法但**仅对无跨-SMC-时序依赖的算子终值**，不是足够可证伪的 simulator。

## Decision

### D1. emitter 改为单全局指令流（ADR-022 待办 #1）
- `src/driver/emitter.py:_emit_multi_smc`：**不再** `for smc_id in range(smc_num)` 写 8 份 `smc_k/iq_*.hex`；改为只写**一套** `iq_*.hex`（指令保留 `smc_id_en` 字段）。
- **删 per-SMC 遗留注入「三处副本」**（HCO 续审 agentId `a45dedc230c151316` 补全；同源于已废的 per-SMC 模型，呼应 ADR-021 §元教训「fold 三副本漏改即破」）：
  1. `_compute_per_smc_stagger` 调用 + INIT `loops += smc_shift` stagger（emitter.py:160-168/193-195）。**正式 supersede ADR-020 v6-4**。
  2. `_zero_broadcast_data_for_followers`（emitter.py:407-432）及调用点（198-200）。
  3. **`_inject_smc_id`（emitter.py:434-452）及调用点（196-197）** —— per-SMC k 注入 compute 指令 `SMC_ID` code_field。单 SMI 模型下 compute 分发靠**硬件自身 SMC_ID 寄存器**（ADR-022:62 选项 (ii)，HCO Accept），指令 `SMC_ID` 是遗留物。⚠ **不静默删字段**（FPMUL code_bits=8 含 SMC_ID:5，字段仍占比特位）：不再注入 k，走 modules.yaml 已声明的 `default: 0`。LOGIC 4-bit fail-fast（emitter.py:367-383）失去意义，一并清理（本 ADR 留档，非静默删）。
- UR 数据仍 per-SMC 写 `smc_k/ur_data/bank*.hex`。`sim_config.json` 写单 `iq_init` + `per_smc_ur_init`（list, index=smc_id）。
- emit 入口 `emitter.py:44-48` 的 `lir_stream is None → raise` 随 `_compute_per_smc_stagger` 一并处理（去掉强制要求或保留为 no-op，不留死参数歧义）；同步 `pipeline_runner` 传 `lir_stream=` 的调用点。

### D2. 广播 `a` = 单源同拍 fan-out（ERRATA 2026-05-29，取代「走指令 data 字段」）
> **ERRATA（HCO 三审 agentId `a7de85055575f69d7`）**：D2 初稿「广播 data 走指令 data 字段」建立在「`a` 能塞进单条指令 128-bit immediate」的**错误前提**上。实测 `build/.../fma_parallel_n/`：`a` 是 **64 个 128-bit 字的流**，预载在 SMC_0 bank0（follower bank0 全 0），SHIFTUP 通过 **ur-r0 读端口 + BODY loop（3 指令）** 逐拍读出广播，128-bit data 字段装不下。初稿 D2 + 单测 `test_shiftup_data_from_instr_field` **推翻**。

采纳 **方案 (B)**：
- **数据源不变**：`a` 流仍从 **SMC_0 bank0 单源** 经 ur-r0 + SHIFTUP BODY loop 逐字读出（这是「64 字流」的合法 lowering，SMC_0 的读端口物理上扮演「SMI 向 CRU_SHIFTUP 注入 data」单源角色）。
- **sim 改 broadcast fan-out**：`broadcast==1 && smc_id<=smc_id_en` 时，**所有使能 SMC 同拍**取 SMC_0 的广播值写入各自 `DR_SHIFTUP_D`（**abolish `_propagate_chain_inputs` 的逐级 1-cycle 相对延迟**）。这与 D1 删 stagger 是绑定动作：删了补偿延迟的 stagger，必须同时删被补偿的 chain 相对延迟，否则 lockstep FPMUL 读到错位 stale `a`（HCO 三审已验证此推导）。
- **「禁 ur_bank 源」收窄**：前轮「禁 ur_bank 源」的真实动机只有「禁 OQ-1 把广播误读为各 SMC 各读各的本地 bank（per-SMC 自治）」与 RTL 相关；SMC_0 单源读出 + fan-out **不违反** RTL 表 542「data 不来自另一个被广播 SMC 的本地寄存器」。故**不禁 SMC_0 单源，禁 follower 各读本地 bank**。
- 触点：`sim/smc_simulator/system/cluster.py:84-98`（broadcast 路径改 fan-out）、`shift_up_unit.py:55-71`、`kernel.py:516`、订正 `shift_up_unit.py:18` OQ-1 docstring + `configurable.py:107/120` OQ-1 注释。
- **债务**：**DEBT-023-A**（方案 A 逐字 immediate 展开，留作 RTL 证实硬件确为 SMI 逐字注入时的忠实路径）；**DEBT-023-BCAST-TIMING**（broadcast 流式注入时序需 RTL 表 542/544 之外的 SMI↔CRU_SHIFTUP 驱动时序闭合，与 DEBT-023-B 合并由 VCS 待办 #8 背书）。

### D3. simulator 取模型 A（lockstep 复制，时序不忠实中转）（ADR-022 待办 #2 的中转交付）
- `physical_runner.py:_run_multi_smc`（247-261）：改读单 `iq_init`，**N 个 ParallelKernel 全部 `load_program` 同一套 hex**；UR 仍 per-SMC（`per_smc_ur_init[k]`）。去 `assert len(per_smc_iq)==smc_num`。
- **A 明确标注为「时序不忠实中转」**：A **不验收跨-SMC 时序**；模型 B（单取指 + CRU shift-fabric，指令沿 SMC_0→SMC_k 逐级 1-cycle 传播 + SMC_ID 匹配）是 ADR-022 待办 #2 的**真实交付目标**，记为债务 **DEBT-023-B**。
- **A 单独不得作为「单 SMI 模型已忠实实现」的证据**（重蹈 ADR-022「sim 自洽 ≠ 与 RTL 一致」覆辙）。升 Accepted 的最终证据仍是 ADR-022 待办 #8（8-SMC 固件 vs VCS 实测）。

### D4. 模型 A 的可证伪验收门（HCO 阻断 #2，硬性）
A 必须配两个可证伪单测，否则 A 把 smc_id_en 契约偷偷绕过：
- `tests/unit/test_multi_smc_smc_id_en_gating.py`：SHIFTUP broadcast `smc_id_en=3` @ N=8，断言 **SMC 4..7 的 `DR_SHIFTUP_D` 保持 0/旧值**（未使能不执行），SMC 0..3 拿到广播 data。对应 ADR-022:135。（不依赖被推翻的 immediate 前提，原样保留）
- `tests/unit/test_shiftup_broadcast_single_source.py`（ERRATA 取代 `test_shiftup_data_from_instr_field`）：N=8、SMC_0 bank0 预载非零 `a`、**follower bank0 全 0**，跑 broadcast SHIFTUP。断言 (1) **所有使能 SMC 同拍拿到相同 `DR_SHIFTUP_D`**；(2) 该值 = SMC_0 bank0 单源读出值，**follower bank0=0 不影响结果**（专杀方案 (C) 式各读各的本地源 + 守「单一 SMI 源广播」契约）。

### D5. 穷举 3 处隐藏耦合（HCO 阻断 #3）
任一漏改破 e2e（参 ADR-021 §元教训「fold 三处副本」）：
1. `physical_runner.py:247-261` schema 读取（已并入 D3）。
2. `src/synthesis/axioms/resource_axioms.py:344 add_chain_iq_propagation_axiom`（按 `smc_id_en+1` 个 per-SMC slot 建模 IQ 占用，line 369）+ emit `lir_stream` 入口（emitter.py:44-48）。单 SMI 模型下「沿途 N 个 SMC 各占 IQ slot」物理含义变了（单流只有一个 IQ 实例）。**本轮至少确认它不让重构后 fma UNSAT**；axiom 语义是否仍正确属 ADR-022 待办 #4，本 ADR 记 `still_holds: 待重核`。按 CLAUDE.md routing，axiom 语义变更须走 hco + ADR —— 本轮**不改语义只验不 UNSAT**，真改留待办 #4。
3. `verification_v2/ops/configurable.py:107/120` 算子层 OQ-1 假设注释 + LIR data 字段填值（已并入 D2）。

### D6. SHIFTDN smc_id_en fallback（HCO 建议 #4，本轮 defer）
fma 走 N 路径不触发 SHIFTDN 下行 gather → `shift_dn_unit.py:20/60` 的 `SMC_ID_EN_FALLBACK=31` 本轮不被执行路径触及，**完整修正 defer 到 ADR-022 待办 #3**。但本轮做两件 1 行级清理：
- `shift_dn_unit.py:20` 硬编码 `31` → `PARAM_SMC_NUM-1` 派生（避免 8-SMC 下越界死常量）。
- 确认 fma_parallel_n 的 LIR **零 SHIFTDN op**（含 vld=0 占位）；若有则 D6 升级为本轮必改。

### D6b. compute 指令 SMC_ID 寻址语义 —— RTL 表缺口（BLOCK 未来 schema，不卡 fma）
HCO 续审（agentId `a45dedc230c151316`）发现：compute/访存模块命名分裂 —— FPMUL/FPADD/LOGIC/UR_* code_field 是 `SMC_ID`（单值），但同为 compute 的 INTADD(modules.yaml:123) 用 `smc_id_en`（范围）。behavioral_semantics 中 compute 的 `SMC_ID` **从不 gating**（惰性字段），而 SHIFTUP 的 `smc_id_en` 真 gating。**user 给的权威 RTL 只覆盖表 542–548（SHIFTUP/SHIFTDN），compute 微指令表从未提供。**
- **本轮 fma 不被卡**：fma 全员参与（smc_id_en=N-1），compute `SMC_ID` 走 default 0 即可，按 (ii) 实施。
- **BLOCK 两件未来事项**（需 user 提供 RTL compute 微指令表才能闭合）：(1) compute `SMC_ID` 是否真不 gating；(2) compute 是否需「子集使能」范围语义 → 若需则 `SMC_ID`→`smc_id_en` 是 modules.yaml schema 变更，**必经 hco + 新 ADR**。记为 **DEBT-023-COMPUTE-ADDR**。

### D7. 验收 + 流程（HCO 建议 #5）
- fma_parallel_n hw-parity @ `PARAM_SMC_NUM=8` 求 **8/8 bit-perfect**（主验收）。
- 因是「推倒重建基线」（ADR-021 的 32/32 绿测会失效），单独起 **evaluator** 复核 8/8 非 self-eval（CLAUDE.md routing：`--mode hw-parity` 行为变化必调 evaluator）。
- 升 Accepted 条件：fma 8/8 + D4 两单测 pull + evaluator 背书 +（最终）ADR-022 待办 #8 VCS 实测。

## Consequences

### 正面
- emitter 产物从 N 份重复指令流 → 一份全局流 + per-SMC 数据，首次与 RTL 表 542–548 结构对齐。
- 广播 `a` 从「ur_bank 本地读 + OQ-1 假设源固定 SMC_0」改为「指令 data 字段携带」，消除 ADR-022:131 点名的 OQ-1 错误模型。
- 模型 A 复用现有 N-kernel 基建，simulator 改动远小于 ADR-022:121 预期的「从零建 fabric」—— 代价是把 B 的债务显式推迟（DEBT-023-B），而非掩盖。

### 风险 / 债务
- **DEBT-023-B（模型 B 真 fabric）**：A 抹平 CRU 传播时序，掩盖 SHIFTUP 到达时序类 bug 与 SHIFTDN 下行回收时序 bug。fma 本轮不触发 SHIFTDN，但 SHIFTUP 时序风险真实存在，仅靠 D4 单测部分兜底（验门控生效，不验传播延迟）。B 是 ADR-022 待办 #2 真目标。
- **ADR-021 fma 32/32 基线失效**：本轮基线重建为 8/8（推倒重建，非叠加）。
- **add_chain_iq_propagation_axiom 语义待重核**（D5.2）：本轮只验不 UNSAT，真改留 ADR-022 待办 #4。
- **SHIFTDN smc_id_en 完整修正 defer**（D6，ADR-022 待办 #3）。
- 多处 docstring/注释仍写死 31/32，随 ADR-022 待办 #5 批量订正。

## Supersedes / Reverses
- **ADR-020**（SHIFTUP IQ BODY loop + v6-4 per-SMC INIT stagger）：stagger 机制 D1 正式删除。
- 部分覆盖 **ADR-018/019** 的 per-SMC 独立流实现机制（随 simulator 模型 A/B 演进）。

## References
- [DEBT-023-inventory.md](DEBT-023-inventory.md) —— 本 ADR 4 项债务（B/A/BCAST-TIMING/COMPUTE-ADDR）的聚合跟踪清单 + 待 user 提供的 RTL 表料单
- [ADR-022](ADR-022-single-smi-global-instruction-stream-model.md)（被实现的硬件模型 + 8 步待办）
- [ADR-004](ADR-004-doc-first-principle.md) 文档先行
- HCO 设计复核：agentId `a93906710d712e849`（APPROVE-with-notes，本 session）
- RTL spec 表 542/544（SHIFTUP）/ 545/547/548（SHIFTDN）
- 代码触点：`src/driver/emitter.py` / `sim/smc_simulator/tools/physical_runner.py` / `sim/smc_simulator/core/kernel.py:516` / `sim/smc_simulator/units/shift_{up,dn}_unit.py` / `verification_v2/ops/configurable.py:_inject_shiftup_broadcast` / `src/synthesis/axioms/resource_axioms.py:344`
