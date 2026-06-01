---
adr_id: ADR-025
title: 8-SMC N 维 matmul —— 广播激活 + 每 SMC 加法树点积（output-stationary）
decided: 2026-06-01
last_verified: 2026-06-01
still_holds: proposed
implements: ADR-022
supersedes: ADR-024
supersedes_decision_in: [ADR-018, ADR-020]
---

# ADR-025 · 8-SMC N 维 matmul（广播激活 + 每 SMC 加法树点积）

> **状态：Proposed（doc-first，代码未改）** —— 取代 [ADR-024](ADR-024-matmul-parallel-k-shiftdn-reduce-model-a.md)（K 维 SHIFTDN reduce，前提被 RTL 表 78/79 证伪）。落地 user 拍板的「8-SMC matmul」，按 RTL 表 78/79 的**加法树向量·矩阵乘法**真实机制实现。
>
> **一句话**：matmul = **广播激活向量（SHIFTUP，ADR-023 已跑通）+ 每 SMC 与本地权重列做点积（reduce 在 SMC 内部用 FP32ADDTREE5TO1）+ 每 SMC 输出自己那份结果（partitioned，8/8 验收）**。无跨-SMC sum-reduce、无 SHIFTDN、不改 axiom、不改 hw_config 契约。

## Context

### RTL 表 78/79（user 2026-06-01 提供，权威）确立的硬件 matmul 机制
- **表 78（存储格式可行性表）**：所有可行（Yes）组合的实现方式统一为「**从一个 SMC 读取向量，广播，与各个 SMC 中的列向量点积**」。前提：广播向量则矩阵必须**转置存储**（每 SMC 持若干列）；**结果存储方式必与非广播矩阵一致**（→ 结果 partitioned 跨 SMC）。
- **表 79 + 微指令流（加法树 GEMM）**：
  1. **激活向量读取微指令**：从一个 SMC 读出、线速广播给所有 SMC（每周期广播 `PARAM_UR_BYTE_CNT/ACTIVATION_BYTE_CNT` 个数据）。→ **SHIFTUP 广播**（ADR-023 N 维路径）。
  2. **权重矩阵读取微指令**：广播数据在每 SMC 与本地读取的权重 Tile 做点积。
  3. **点积计算微指令**：`每个 SMC 中产生 1 个 RESULT`。→ reduce 在 **SMC 内部**（FP32ADDTREE5TO1），非跨-SMC。
  4. **结果写回微指令**：`所有 SMC 合并起来产生 1 行 PARAM_SMC_NUM 列的 RESULT Tile`。→ 输出 **partitioned**。大 K 的累加靠**部分和写回 UR + 跨 tile 在同一 SMC 内累加**（三种复用策略），仍非跨-SMC。
- **SHIFTDN 的真实角色**：仅出现在**串联 matmul 的结果收集/拼接**（表 79 Matmul_QK「把 Matmul_K 结果收集存储到最后一个 SMC 再拼接」）——**纯数据搬运/concat，非求和**。这解释了为何 SHIFTDN 契约无加法、无 `SHIFTDN_D` 回读路径（`rtovr_config.yaml` consumers_dr 止于 33，无 SHIFTDN_D）：它本就不做 reduce。**本 ADR 不触 SHIFTDN。**

### 为何作废 ADR-024
ADR-024 假设 matmul 走「K 维跨-SMC SHIFTDN gather-sum-reduce」，撞上「SHIFTDN 纯搬运无加法 + 无 RtoVR 回读」的墙。表 78/79 表明该前提错误：matmul 的 reduce 是 **SMC 内部加法树**，跨-SMC 只有**广播**。ADR-024 的墙是个**伪问题**。

### 唯一真难点（与 multi-SMC 无关）
`ConfigurableOperator.compile_with_adpc` 对多-operand dot product（`a0*b0+...`）产**结构性 UNSAT**（本 session 实证：4-operand 单 SMC 无 SHIFTUP 注入也一路 UNSAT 到 II=64，根因 `standard_kernels.py:33 lower_fp32addtree_5to1` 的 DFG）。单 SMC `matmul.yaml` 正是靠手搭 DFG 的 `ManualMatmulOperator`（`matmul_op.py`，II=6）绕开。→ **路线 A**：N 维 matmul 也走手搭 DFG。

## Decision

### D1. 新增手搭算子 `ManualMatmulParallelNOperator`（路线 A，复用 ManualMatmulOperator 的 SMC 内点积）
- 新建于 `verification_v2/ops/matmul_op.py`，注册 `main_verify.py:OPERATOR_MAP["matmul_parallel_n"] = ManualMatmulParallelNOperator`。
  - ⚠ 现有 `verification_v2/ops/matmul_parallel_n.yaml` 走 ConfigurableOperator 自动扫描（UNSAT）。OPERATOR_MAP 显式注册**优先于** yaml 自动扫描（`main_verify.py:45-46` 同名跳过），故手注册即覆盖。yaml 保留作**数据布局文档**（标注 superseded-by 本算子）或删除，实施时定。
- `self.parallel_dim = "N"` → `pipeline_runner._compute_smc_num` 触发多-SMC emit（smc_num=PARAM_SMC_NUM=8）。
- **per-SMC DFG（同一套 hex，model A）= 复用 ManualMatmulOperator 的 SMC 内点积结构**（4×FPMUL → 中间 Bank 写回并读 → FP32ADDTREE5TO1 → 输出），**唯二改动**：
  1. 激活 `a*` 标 `smc_layout="broadcast"`，权重 `b*` 标 `smc_layout="partitioned"`，输出 `y` `partitioned`。
  2. `get_dfg` 末尾对 broadcast 的 `a*` UR_READ 注入 SHIFTUP（复用 `configurable._inject_shiftup_broadcast` 同款逻辑：SMC_0 单源 + `smc_id_en=7` + 消费者 RTOVR `sel=29 SHIFTUP_D`；sim 由 `cluster._broadcast_shiftup_fanout` 同拍 fan-out）。
- **语义**：每 SMC n：`y_n = Σ_{k} a[k]·b_n[k]`（广播激活 × 本 SMC 权重列，SMC 内加法树求和）。8 SMC → 8 份独立 `y_n`（partitioned 输出）。

### D2. 广播激活 = 单广播流（避开旧 multi-broadcast sim 限制）
- 旧 `matmul_parallel_n.yaml`（configurable）把 `a0..a3` 当 **4 个并行 broadcast 流**，撞 `configurable.py:140-143` 标记的「多 broadcast sim 端只硬连 ur-r0 一个端口」限制。
- 表 79「激活线速广播」表明激活是**一条流**（每周期广播 N 个元素）。本 ADR 据此把激活组织为**单广播源**：a 的多个元素走 **同一 ur-r0 广播端口**（128-bit 字含 4 fp32 lane，或随 n_rows tile 逐周期流出），**只产生单一 broadcast SHIFTUP 源** → 复用 ADR-023 已验证的单源 fan-out，**不触发 multi-broadcast 缺口**。
- 实施时确认：SMC 内点积消费这 4 个 lane/元素时，sim 端单源广播值（`cluster._broadcast_shiftup_fanout` 的 `head_su.dr_d`）能正确喂给 4 个 FPMUL。若 ManualMatmul 的「4 个独立 a-read」结构需 4 源，则改为「单广播 read + lane 拆分」或「单流逐周期」，避免多源。**这是本 ADR 的首要实施风险（见 D5 风险）。**

### D3. 不改 axiom / hw_config / SHIFTDN
- 复用 ADR-023 的 SHIFTUP 广播机制（含 `resource_axioms.py:316` 的 broadcast 唯一源 axiom：单广播源天然满足）。**不增删 axiom，不改 hw_config，不碰 SHIFTDN** → 按 routing **本 ADR 无需 hco**（无 axiom/hw_config/SHIFTUP-语义变更，仅复用）。
- 若实施中发现需改 axiom（如 SMC 内点积 + 广播组合产意外约束）→ 立即停，按 routing 走 hco + ADR。

### D4. 验收：8/8 bit-perfect（per-SMC 输出）+ evaluator 复核
- 主验收：`matmul_parallel_n --mode e2e` @ PARAM_SMC_NUM=8 → **8/8 SMC 输出 bit-perfect**（atol=5e-3，同 matmul），复用 N 维 golden/data 基建（`_get_golden_parallel_n` / `_prepare_input_parallel_n` 同模式，或在 manual operator 内实现等价 per-SMC golden）。
- 按 routing：`--mode hw-parity` 行为变化 / 新算子物理验收 → **必调 evaluator** 复核 8/8 非 self-eval。
- 回归不破：fma_parallel_n 8/8、matmul 单 SMC、pytest 全绿。

### D5. 可证伪验收门（仿 ADR-023 D4）
- `test_matmul_n_broadcast_shared`：N=8，断言所有 SMC 的激活输入 = 同一广播源值（守「单源广播」契约），且每 SMC 权重不同（partitioned）。
- `test_matmul_n_per_smc_output`：N=8，构造可分辨权重（如 b_n = n），断言 SMC_n 输出 = Σ_k a[k]·b_n[k] 各不相同（守「output-stationary，每 SMC 算自己那份」契约，专杀「所有 SMC 算同一份」式 bug）。

## Consequences

### 正面
- 与 RTL 表 78/79 的真实 matmul 机制对齐（广播 + SMC 内点积 + partitioned 输出）。
- 复用既有基建：ADR-023 SHIFTUP 广播（fma 8/8 已验证）+ ManualMatmulOperator SMC 内加法树点积（II=6 已验证）。改动面小、风险集中在 D2 单广播流接线。
- 绕开 configurable 4-operand UNSAT（手搭 DFG），不碰高 blast-radius 的 `lower_fp32addtree_5to1`。
- **不进 SHIFTDN 债务区**（DEBT-023-B / ADR-022 待办 #3/#6 与本 matmul 无关）。

### 风险 / 债务
- **D2 单广播流接线（首要风险）**：ManualMatmul 原结构是 4 个独立 a-read，N 维需收敛为单广播源。若处理不当重蹈 multi-broadcast sim 限制 → 实施时优先验证此点（单测 `test_matmul_n_broadcast_shared`）。
- **广播 SHIFTUP + SMC 内 addtree 的 SMT 可解性**：configurable 同形态 UNSAT（其 DFG 之过）；手搭 DFG 控制结构应可 SAT，但**须实测确认**（首跑若 UNSAT，回到 DFG 结构调整，非 axiom）。
- **终值 parity 边界**：8/8 是终值 parity；广播时序由 ADR-023 model A 中转承载（DEBT-023-B 的 SHIFTUP 时序债务仍适用，但本 ADR 不新增——与 fma 同级）。
- 大 K（>单 tile）的 UR 累加复用（表 79 三策略）**本轮不实现**，仅做单 tile 点积；多 tile 累加记为后续（DEBT-025-MULTITILE）。

## Supersedes / Reverses
- **ADR-024**（K 维 SHIFTDN reduce）：整体作废（前提被表 78/79 证伪）。
- **ADR-018 v7 Stage 1 X / ADR-020**（真 GEMM target 的 per-SMC 机制）：本 ADR 给出 N 维 matmul 的首个真实落地，复用 ADR-023 单 SMI 广播取代旧 per-SMC 机制。
- 不影响 ADR-023 N 维 fma 路径（本 ADR 是其在 matmul 上的推广）。

## References
- RTL 表 78（向量·矩阵乘法存储格式可行性）/ 表 79（加法树 GEMM 微指令流 + LLAMA2 7B 举例）（user 2026-06-01 提供）
- [ADR-023](ADR-023-single-smi-emitter-sim-impl-model-a.md)（SHIFTUP 广播 model A，本 ADR 复用）+ [DEBT-023-inventory.md](DEBT-023-inventory.md)
- [ADR-024](ADR-024-matmul-parallel-k-shiftdn-reduce-model-a.md)（被取代的 K 维方案 + SHIFTDN datapath 调研存档）
- [ADR-004](ADR-004-doc-first-principle.md) 文档先行
- 代码触点：`verification_v2/ops/matmul_op.py`（新算子，复用 ManualMatmulOperator 点积结构）/ `main_verify.py:OPERATOR_MAP`（注册覆盖 yaml）/ `verification_v2/ops/configurable.py:_inject_shiftup_broadcast`（广播注入逻辑复用）/ `sim/smc_simulator/system/cluster.py:_broadcast_shiftup_fanout`（ADR-023 现成，无需改）
- 现状证据：`matmul_op.py:140`（SMC 内 FP32ADDTREE5TO1 点积）/ `fma_parallel_n` 8/8（广播路径已验证）/ 本 session 隔离实验（configurable 4-operand UNSAT）
