---
adr_id: ADR-028
title: broadcast SHIFTUP 锁存窗口放宽 —— 驻留 DR_SHIFTUP_D 喂多列 FPMUL（user RTL 裁定 Q-a/Q-b）
decided: 2026-06-01
last_verified: 2026-06-01
still_holds: proposed
supersedes: ADR-027（sim 多广播 deferral —— 经查无需 sim-core，axiom 放宽即解）
amends: ADR-026 Stage-2a（model-A 预分发 → 真广播 direct）
builds_on: ADR-023, ADR-025, ADR-026
---

# ADR-028 · broadcast SHIFTUP 锁存窗口放宽（驻留 DR_SHIFTUP_D 喂多列）

> **状态：Proposed（axiom 改动，必过 hco）**。把 ADR-026 Stage-2a「单广播激活 a → 单 SMC 多列点积」的 SMT UNSAT 真根因定位到 `dependency_axioms` 的 **RTOVR 锁存严格约束**，依 user RTL 裁定（Q-a 驻留广播 / Q-b 时间维 scatter）**放宽为驻留窗口区间**。结果：matmul_q2（cols_per_smc=4）走**真广播**（SHIFTUP 在固件中）8/8 bit-perfect，**取代 ADR-026 的 model-A 预分发**，且 6 个既有算子无回归。

## Context —— 真根因（订正 ADR-026/027 的误判）

ADR-026 Stage-2a 把「单广播 a → 4 列」UNSAT 归因为「需 4 distinct 源 vs 广播单源」，并（跟随 hco Q2.1）拟 BLOCK 到 RTL 表 78/79 + sim 多广播改造（ADR-027）。**经实测定位，二者皆非真根因**：

- **乘法器数已被 yaml 定死**：`fpmul.yaml` `throughput:1` + 单元单例 + `PREC_32 count=4`（4-lane SIMD）→ 4 列 = **1 个 FPMUL SIMD-4-lane 跨拍发射 4 次**（一列/次）。4-FPMUL-op DFG 在 `count=1` 单元上本就串行，是忠实模型。RTL 表 78/79 对「乘法器数」**不需要**。
- **真根因 = RTOVR 锁存严格约束**（`src/synthesis/axioms/dependency_axioms.py`，`add_latency_axiom` 内）：
  ```
  t_latch == t_vars[prod_id] + lat_p          # 严格：所有消费者锁存钉死同一拍
  t_vars[consumer] == t_latch + 1             # 无弹性缓冲，锁存后正好 1 拍消费
  ```
  当一个 broadcast SHIFTUP 被 **N 个 RTOVR**（N 列的 FPMUL_S0）消费时，`t_latch` 对每个 (consumer, SHIFTUP) 都 `== SHIFTUP.t + lat`（同值）→ **N 个 RTOVR 锁存同一拍 → 同拍写 FPMUL_S0 端口冲突 → UNSAT**。这与 cols_per_smc 无关地禁止任何「单广播喂多消费者」。

## user RTL 裁定（2026-06-01，硬件契约 ground truth）

- **Q-a**：`a` **驻留**在 DR_SHIFTUP_D，**硬件支持 N 个 RTOVR（跨拍/同拍）读同一广播源**，无 1-拍瞬态覆盖（区别普通 producer 输出 1 拍后即被下一发射覆盖）。
- **Q-b**：4 列拼一词 = **时间维 scatter**（循环/错拍各列落不同 lane），硬件就现有这些内容（无额外隐藏机制）。

## Decision

### D1. 对 broadcast SHIFTUP producer 放宽锁存为驻留窗口区间
`dependency_axioms.add_latency_axiom` 的 RTOVR 增广边：当 `producer.op_type=="SHIFTUP" and producer.metadata["broadcast"]==1` 时，把
```
t_latch == prod.t + lat            # 原严格
```
改为
```
prod.t + lat <= t_latch <= prod.t + lat + (II - 1)     # 驻留窗口内任一拍采样
```
- 下界 = producer 输出就绪（undersample 防护，与 verifier 维度4 一致）。
- 上界 = 下一次广播值落 dr_d 前（`prod.t + II` 是下一迭代 SHIFTUP，+lat 上 dr_d；区间取 II-1 守在覆盖前），**多左行 M>1 时保证读到本行 a**（单行 M=1 时上界宽松无害）。
- **「锁存后正好 1 拍消费」(`consumer == t_latch+1`) 保持严格**（无弹性缓冲是物理真）；放宽的只是「何时锁存」。
- **普通 producer 仍严格 `==`**（瞬态输出，必须当拍采样）—— guard 最小化 blast radius。

### D2. counter-verifier 无需同步改
`ucode_solver.validate_physical_schedule` 维度4 本就用 `undersample (t_rtovr >= prod.t+lat)` + `overwrite (t_rtovr < prod.t+lat+II，含 is_inv 豁免)` **区间**判定、非 strict ==。放宽后的 schedule 落在其区间内 → 不触 VERIFIER-FAIL。（区别 ADR-021 的 fold：那是 axiom/verifier 双边都要改；此处 verifier 本就宽，仅 axiom 过严。）

### D3. matmul_q2 走真广播 direct，取代 model-A
`ManualMatmulQ2Operator` 交付路径 = `BROADCAST=True / BCAST_MODE="direct"`：单 a-read（broadcast）→ 唯一 SHIFTUP → COLS 个 FPMUL_S0 RTOVR 全消费它 → 时间维 scatter 进 4 addtree 通道。**SHIFTUP 广播指令在固件中**（比 ADR-026 model-A 预分发忠实）。

## 验收（已实测）
- `--op matmul_q2 --mode e2e` **SAT@II=6 + 8 SMC bit-perfect + exit=0**（重跑稳定）。
- **无回归**：matmul / matmul_parallel_n / matmul_q / fma_parallel_n / horner / cos4 全 exit=0 bit-perfect；pytest 76 pass/6 skip；smt-unit 13/13 OK。
- 单测 `test_matmul_q2.py::test_shipped_path_is_faithful_single_broadcast`（断言唯一广播 SHIFTUP + COLS 个 FPMUL_S0 RTOVR 全消费它 sel=29）。

## Consequences
- **正面**：Stage-2a 拿到**真广播**忠实路径（SHIFTUP 指令在固件），非 model-A；放宽是**广义能力**——任何「单广播源喂多消费者」（未来多列/多消费场景）自动可解；既有单消费者广播算子（matmul_parallel_n/matmul_q/fma）无回归。
- **债务 / 待验**：
  - **`still_holds: proposed`**：axiom 改动**必过 hco** 复核（驻留窗口语义是否忠实 RTL DR_SHIFTUP_D 时序）+ 升 Accepted 需 VCS。
  - **上界 II-1 的多行正确性**：M>1（Stage-2b）时驻留窗口上界须精确卡在下一行广播前；M=1 已验，M>1 待 Stage-2b 实测。
  - 终值 parity（model-A 计算时序层面仍不验跨-SMC 广播**物理**时序，继承 DEBT-023-BCAST-TIMING）；但**广播指令存在性**已忠实（优于 ADR-026 model-A）。
- **supersede ADR-027**：sim 多广播改造**不需要**（单 dr_d 驻留 + 单 SHIFTUP 已够，indep 4-SHIFTUP 是错误方向）。ADR-027 标 superseded。

## References
- `src/synthesis/axioms/dependency_axioms.py`（add_latency_axiom RTOVR 锁存，本 ADR 放宽点）
- `src/synthesis/ucode_solver.py:254-306`（维度4 counter-verifier，本就区间判定）
- `hw_config/specs/units/fpmul.yaml`（throughput=1 + PREC_32 count=4，定死乘法器数）
- [ADR-026](ADR-026-matmul-q-matrix-matrix-ds-transpose.md)（Stage-2a，本 ADR amends 其 model-A 为真广播）/ [ADR-027](ADR-027-sim-multi-broadcast-shiftup.md)（superseded）/ [ADR-023](ADR-023-single-smi-emitter-sim-impl-model-a.md)（SHIFTUP fan-out）/ [ADR-021](ADR-021-fold-is-bug-delete-shiftup-rtovr-worstcase-fold.md)（axiom/verifier 同步先例）
- user RTL 裁定 Q-a（DR_SHIFTUP_D 驻留 + N-RTOVR 多读）/ Q-b（时间维 scatter）（2026-06-01）
