---
adr_id: ADR-027
title: sim 多广播 / SHIFTUP 单源喂多列 —— 广播-到-N列 通路忠实建模（gated on RTL 表 78/79）
decided: 2026-06-01
last_verified: 2026-06-01
still_holds: proposed
supersedes_part_of: ADR-026 B2（本轮 BLOCK multi-broadcast）
builds_on: ADR-023, ADR-025, ADR-026
blocked_by: DEBT-026-BCAST-MULTI
---

# ADR-027 · sim 多广播 / 广播激活喂单 SMC 多列 —— 通路忠实建模（**Proposed / BLOCKED on RTL**）

> **状态：Proposed，BLOCKED**。本 ADR 记录 [ADR-026](ADR-026-matmul-q-matrix-matrix-ds-transpose.md) Stage-2a 暴露的「单广播激活 a → 单 SMC 内多列点积」**通路忠实度**问题，并把它从 Stage-2a 主线**隔离**出来（Stage-2a 已用 model-A 预分发激活落地 dense 打包，见 ADR-026 2026-06-01-b）。**未实施**：根节点 Q2.1 需 user RTL 表背书（DEBT-026-BCAST-MULTI），无之不动 sim-core。

## Context（为何需要本 ADR）

ADR-026 Stage-2a（cols_per_smc=4）需把**单广播激活** a 喂给单 SMC 内的 **4 列 FPMUL**。实测两道墙（详见 ADR-026 「Stage-2a 落地 2026-06-01-b」）：

1. **SMT 侧**：广播单源 SHIFTUP_D 被 4 个 RTOVR 消费撞**强对齐选通** → UNSAT。要 SAT 必须 4 个 distinct 源（4 独立 a-read / 4 独立 SHIFTUP op）。
2. **sim 侧（multi-broadcast 缺口）**：`sim/smc_simulator/core/kernel.py:516` SHIFTUP 数据硬连读端口 0；`sim/smc_simulator/units/shift_up_unit.py` + `cluster.py:104-127 _broadcast_shiftup_fanout` 是**单 shiftup 单元 + 单 dr_d** 模型。4 个独立 SHIFTUP op（SMT-SAT@II=6）在 sim 中跨拍取值时 dr_d 被空 port0 clobber → 偶列对/奇列 0。即 `verification_v2/ops/configurable.py:140-143` 注释点名的 multi-broadcast sim 缺口。

## 根节点（hco BLOCK，agentId addc26afe31101fd8）—— 必须先闭合

**Q2.1（DEBT-026-BCAST-MULTI）**：`hw_config/specs/units/fpmul.yaml PREC_32 count=4` → FPMUL 是 **SIMD 4-lane**。单 SMC「4 列」在真实 RTL 里**很可能是 1 个 FPMUL 跨拍调用 4 次**（每次算一列的 4-lane 点积），而非 4 个物理乘法器、也非 1 乘法器对单标量时分。

→ 若推断成立，则当前 `ManualMatmulQ2Operator` / `ManualMatmulOperator` 的 **4-FPMUL-op DFG 是绕 SMT 强对齐的脚手架，数据通路层不忠实硬件**。在此基座上做任何 sim 多广播 fix（含下方 sticky 候选）都只是 model-A 终值对齐，**对 RTL 忠实度增信近零**（同 ADR-023「8/8 不验时序」陷阱）。

**故本 ADR 在拿到 RTL 表 78/79（单 SMC 内广播激活 × 本地多列的乘法-累加 lane 组织）+ 表 711 + SHIFTUP 寄存器表（DR_SHIFTUP_D 保持/刷新语义）之前，BLOCK，不动 sim-core。**

## 候选方案（待 RTL 裁定后取舍，勿先实施）

- **候选 A：sim sticky-broadcast**。让 `_broadcast_shiftup_fanout`/`shift_up_unit` 在广播期间 **stably 保持 dr_d = 当前广播值**（仅在有效 r0 广播读拍更新，非广播拍不被空 port0 clobber），使 N 个 FPMUL_S0 跨拍读一致 a。**hco：作为 sim model-A 修复方向合理（APPROVE-with-notes），但不是 RTL 忠实背书**；且建立在可疑的 4-FPMUL 通路上。
- **候选 B：按 Q2.1 真实通路重建**。若 RTL 确认「1 FPMUL SIMD 4-lane 算一列、4 列 = 4 次调用」，则正确建模 = 单广播一个 4-lane 词喂单 FPMUL 算一列的 4 lane，**根本不需要 4 个 SHIFTUP op**；广播只需单源单消费、cols 走时间维（loop over COLS），dense 打包靠 addtree 4 通道在 4 拍内分别落 S0..S3。这可能同时解掉 SMT 强对齐（单消费者）+ sim 多广播（单 dr_d 足够）。**优先级高于候选 A**（若 RTL 支持）。

## Decision（本轮）

**不实施。** Stage-2a 以 model-A 预分发激活（ADR-026 BROADCAST=False）落地 dense 打包；广播-到-N列 通路忠实 **defer 到本 ADR**，gated on：

1. **DEBT-026-BCAST-MULTI**：user RTL 表 78/79 单 SMC lane 组织（裁定 Q2.1：候选 A vs B）。
2. user RTL 表 711 + SHIFTUP 寄存器表：DR_SHIFTUP_D 保持/刷新语义（裁定 Q2.2 sticky 与否）。
3. 收齐后回 hco 复核 sim-core 改动 + 升 `still_holds: accepted`，并 supersede ADR-026 B2 的「本轮 BLOCK multi-broadcast」。

## Consequences

- **正面**：Stage-2a dense 打包不被广播通路争议连累，已 8/8 落地；通路忠实问题被显式隔离 + RTL 缺口建账（非埋雷）。
- **债务**：广播指令（SHIFTUP）在 Stage-2a 缺席（model-A），比 Stage-1/ADR-025 退化；终值 parity 不验广播/跨-SMC 时序。升 Accepted 需 RTL 表 + VCS。

## References
- [ADR-026](ADR-026-matmul-q-matrix-matrix-ds-transpose.md)（Stage-2a；本 ADR 隔离其广播通路争议）
- [ADR-023](ADR-023-single-smi-emitter-sim-impl-model-a.md)（SHIFTUP 单源 fan-out model-A；本 ADR 拟扩其多广播语义）
- [ADR-025](ADR-025-matmul-parallel-n-broadcast-addtree.md)（广播原语 cols=1）
- 代码触点：`sim/smc_simulator/core/kernel.py:516`（SHIFTUP data 硬连 port0）/ `sim/smc_simulator/units/shift_up_unit.py` / `sim/smc_simulator/system/cluster.py:104-127`（单 dr_d fan-out）/ `verification_v2/ops/configurable.py:140-143`（multi-broadcast 缺口注释）/ `verification_v2/ops/matmul_op.py ManualMatmulQ2Operator`（BROADCAST=True 诊断分支复现 UNSAT/sim-gap）
- RTL 缺口（user 提供后解 BLOCK）：表 78/79（单 SMC 乘法-累加 lane 组织）/ 表 711（Matmul_Q）/ SHIFTUP 寄存器表（DR_SHIFTUP_D 保持语义）
