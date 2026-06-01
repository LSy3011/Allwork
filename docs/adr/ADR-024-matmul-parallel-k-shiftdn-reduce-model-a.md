---
adr_id: ADR-024
title: 8-SMC K 维 matmul —— 跨-SMC SHIFTDN gather-reduce（model A 终值 parity）
decided: 2026-06-01
last_verified: 2026-06-01
still_holds: superseded
implements: ADR-022
supersedes_decision_in: [ADR-018]
superseded_by: ADR-025
---

> # 🪦 WITHDRAWN（2026-06-01，未实施即作废）
> **前提被 RTL 表 78/79（向量·矩阵乘法存储格式 + 加法树 GEMM 微指令流）证伪。** user 提供的权威 spec 表明：本硬件的 matmul **不靠跨-SMC SHIFTDN sum-reduce**，而是 **广播激活向量（SHIFTUP）+ 每 SMC 与本地权重列点积（reduce 在 SMC 内部用 FP32ADDTREE5TO1 + UR 累加）+ 每 SMC partitioned 输出**——即 **N 维 output-stationary**。SHIFTDN 仅用于串联 matmul 的结果收集/拼接（纯搬运，非求和），这也解释了为何契约里 SHIFTDN 无加法、无 `SHIFTDN_D` 回读（它本就不做 reduce）。
> **本 ADR 的 K 维 SHIFTDN gather-reduce 模型整体作废，由 [ADR-025](ADR-025-matmul-parallel-n-broadcast-addtree.md) 取代。** 以下内容保留作历史记录 + SHIFTDN datapath 调研存档（DEBT-024-* 一并作废，因 reduce 不走 SHIFTDN）。

# ADR-024 · 8-SMC K-parallel matmul（SHIFTDN gather-reduce，model A 中转）〔WITHDRAWN〕

> **状态：Proposed（doc-first，代码未改）** —— 落地 user 拍板的「8 个 SMC、K 维并行的 matmul」。本 ADR 是 ADR-018 v7 Stage 2（`parallel_dim="K"` + SHIFTDN chain reduce）的首个真实落地，该 Stage 在 ADR-018 时代仅留 stub（`configurable.py:23-26` 声明 `parallel_dim="K"`/`chain_reduce_depth` 但无任何实现），并被 ADR-022/023 单 SMI 重构 orphan。
>
> **HCO 复核：APPROVE-with-notes（agentId `aa737225488f50078`）** —— 2 个阻断项（均文档订正级，不推翻 model-A 路线）已并入下方 D2 / 新增 DEBT-024-REDUCE-DFG / 订正 OQ-1·OQ-2；4 条建议项并入 D2/D4/D5/D6。**核心订正**：RTL 表 545/547/548 的 SHIFTDN 是 **single-source relay（下行覆盖），不做任何累加**；本 ADR 的 gather-sum 是 **model-A 纯 sim 构造，对 RTL 无对应**，不得反向归因为「RTL 下行语义」（重蹈 ADR-022 推翻 OQ-1 覆辙）。
>
> **一句话**：新增手搭-DFG 算子 `matmul_parallel_k`（公式 `y = Σ_{k=0..7} a_k·b_k`，SMC k 持 a_k/b_k 算 partial_k = a_k*b_k，跨 8 SMC 经 SHIFTDN **gather-reduce** 求和 → 单个 y 落 SMC_0）；simulator 取 **model A 中转**（同拍 gather fan-in + 求和，**非真 CRU shift-fabric 逐拍下行**），足够验收终值 parity，**不验跨-SMC reduce 时序**（归 DEBT-023-B）。

## Context

### user 拍板（前提，不在本 ADR 重新论证）
1. **并行维度 = K**（cross-SMC reduce），非 N（output-stationary）。明知走 SHIFTDN 债务区（DEBT-023-B / ADR-022 待办 #3/#6）。
2. **形状 = 8 K-terms，一 SMC 一项**：`y[i] = Σ_{k=0..7} a_k[i]·b_k[i]`。SMC k 持 a_k、b_k（两者都 **K-partitioned**，2D shape=(8, n_rows)，每 SMC 不同数据），算 partial_k[i]=a_k[i]*b_k[i]；reduce 在 SMC 维。输出单个 y 向量。
3. **实现路线 = A**：手搭 DFG（仿 `ManualMatmulOperator`），**绕开** `ConfigurableOperator.compile_with_adpc` 对多-operand dot product 的**结构性 UNSAT**（本 session 实证：4-operand 单 SMC 无 SHIFTUP 注入也一路 UNSAT 到 II=64，根因是 `standard_kernels.py:33 lower_fp32addtree_5to1` 产的 DFG，与 multi-SMC 无关；`matmul.yaml` 正是靠手搭 DFG 的 `ManualMatmulOperator` 绕开，II=6 跑通）。
4. **RTL 仅表 545/547/548**（SHIFTDN 接口 + 上/下行微指令），**无** SHIFTDN 跨-SMC reduce 的逐拍 gather/累加时序表 → 走 model A 终值 parity，逐拍时序记债。

### 现状关键事实（本 session 调研，证据见 References）
- **SHIFTDN sim 是纯数据搬运、不做累加**（`shift_dn_unit.py:67-72`）：`smc_id==smc_id_en` 出源 `regs_s0`、`smc_id<smc_id_en` 把上游 `chain_in` 往下传、否则保持。**没有任何加法**。→ K 维 reduce 的「求和」不在 SHIFTDN 单元里，必须在别处表达。
- **smc_id_en 仍 fallback**（`shift_dn_unit.py:22/64`，=`num_kernels-1`），未从指令字段解码（ADR-022 待办 #3 未做）。`modules.yaml` / `shiftdn.yaml` 的 SHIFTDN **code_fields 无 smc_id_en**（只有 VALID+data），与 behavioral_semantics 自相矛盾——本 ADR **不动此 schema**（见 D3）。
- **K 路径 stub**：`configurable.py` 无 `_inject_shiftdn_chain_reduce`、无 K 维 golden、无 K 维数据形态。
- **chain-reduce SMT axiom**（`resource_axioms.py:344 add_chain_iq_propagation_axiom`）是 per-SMC 独立流时代设计，单 SMI 模型下语义待重核（ADR-022 待办 #4）。
- **零 K 维 / SHIFTDN 测试**。

## Decision

### D1. 新增手搭算子 `ManualMatmulParallelKOperator`（路线 A）
- 新建于 `verification_v2/ops/matmul_op.py`（或同目录新文件），注册到 `main_verify.py:OPERATOR_MAP["matmul_parallel_k"]`（手动算子不走 yaml 自动扫描）。
- 设 `self.parallel_dim = "K"` → `pipeline_runner._compute_smc_num` 据此触发多-SMC emit（smc_num=PARAM_SMC_NUM=8）。
- **per-SMC DFG（每 SMC 跑同一套 hex，model A）**：`UR_READ a_k` + `UR_READ b_k` → `RTOVR(FPMUL_S0/S1)` → `FPMUL → partial_k` → `RTOVR(DVR_SHIFTDN_S)` → `SHIFTDN`（`broadcast=0`、`smc_id_en=num_kernels-1=7`、`dep_port="S"`）。SHIFTDN 输出在 SMC_0 = gather-reduce 求和值（求和由 D2 sim 完成）。SMC_0 `RTOVR(UR_W0_S) → UR_WRITE y`。
- **数据/golden**：
  - `prepare_input(n_rows, smc_num=8)`：a、b 均 K-partitioned，2D `(8, n_rows)`，固定 `default_rng(0)`（与 matmul_op 同模式防 flaky）。
  - `get_golden`：`y[i] = Σ_{k=0..7} a[k,i]*b[k,i]`（elementwise 乘后沿 SMC 维求和），单个 y 向量。
  - `inject_data_per_smc`：每 SMC k 注入 `a[k]`、`b[k]`（都 partitioned，无 broadcast）。

### D2. simulator 取 model A：sim 端 gather-sum（**纯 sim 构造，非 RTL 语义**）
> **HCO BLOCK-1 订正**：RTL 表 545/547/548 的 SHIFTDN = **single-source/broadcast relay**（`SMC_ID==smc_id_en` 出单源 `DVR_SHIFTDN_S`；`CRD_SHIFTDN_I[0]==1` 下行逐级覆盖往 SMC_0 传，last-writer-wins），**RTL 任何地方都不累加**（ADR-022:46-53）。下方的「求和」是 **compiler-level reduce 语义在 sim 端的 model-A 实现**，**对 RTL 无对应**，待 RTL reduce 微指令表（user 现无）澄清硬件实际怎么做 K-reduce。**严禁**把它表述为「RTL 下行语义」。

- SHIFTDN sim 纯搬运、无加法（`shift_dn_unit.py:67-72` 实证），且 model A lockstep 下 SMC_0 的 addtree **无法跨-SMC 读其他 SMC 寄存器** → reduce 的求和在 model-A 阶段**只能做成 sim 端 gather 机制**（DFG 内 per-hop FPADD 需跨-SMC 寄存器读，model A 表达不了）。
- 在 `cluster.py` 新增 `_gather_shiftdn_reduce`（对称于 `_broadcast_shiftup_fanout`，行 104-127）：本拍 SHIFTDN active 时，**同拍**把所有使能 SMC（`0<=k<=smc_id_en`）的 `DVR_SHIFTDN_S` 源值**按 k 升序求和**，结果写入 **SMC_0** 的 `DR_SHIFTDN`/`cru_o`。
- **实现位置（HCO 建议）**：SHIFTUP 的 `_broadcast_shiftup_fanout` 与 `_propagate_chain_inputs` 并存（`cluster.py:141` step 里都调）。故**只绕过/改 `_propagate_chain_inputs` 的 SHIFTDN 下行分支（`cluster.py:101-102`）**，**不整段删该函数**（否则破 fma 的 SHIFTUP 回归）。
- **终值正确性论据**：加法结合律 → Σ 与求和顺序/到达时刻无关（模 FP 舍入）。**FP 一致性（HCO 建议）**：`_gather_shiftdn_reduce` 与 `get_golden` 两侧求和**都固定 k 升序**，避免 FP32 非结合性在 atol=5e-3 边界 flaky（matmul 史上栽过，见 `matmul_op.py:32`）。
- **landing = SMC_0**：理由是 **(a) 与 D5 单输出验收形态一致；(b) SHIFTDN relay 物理终点就是 smc_id=0**——**不是**因为「更贴 RTL reduce 语义」（RTL 无此语义）。备选「fan-in 到所有 SMC 8/8 全等」记录但不取（多源同拍写下行链在 RTL 是 last-writer-wins/总线竞争，全等假设同样无 RTL 依据）。
- **债务（两条，区分）**：
  - **DEBT-024-REDUCE-TIMING**（并入 DEBT-023-B）：model A 抹平逐拍下行 + 累加时序，掩盖依赖 partial 到达时序的 reduce bug。SHIFTDN 路径**现在真被触发**（不再是 fma 时代潜伏）。
  - **DEBT-024-REDUCE-DFG**（HCO BLOCK-2，比时序债务更根本）：sim-sum 把 K-reduce 的**真实 compute-DFG 坍缩为零 op**——SMC_0 上**没有任何 FPADD/addtree 节点参与 reduce**，SMT 层（D4）只看到 N 条 SHIFTDN、看不到加法器资源占用。真硬件的 reduce 更可能是 **N-1 次 single-source SHIFTDN（逐个把 partial_k 搬到 SMC_0）+ SMC_0 上 per-hop FPADD/addtree**。本轮 model-A 终值 parity 可接受，但**「reduce 的 compute-DFG 缺失」是显式债务**，闭合需 RTL reduce 微指令表。⚠ **实施者勿误认「K-reduce 已忠实落地」——本轮只落「sim 端求和」**。

### D3. smc_id_en 走 fallback，本轮不改 hw_config schema
- 8-SMC 全员参与 reduce → `smc_id_en = num_kernels-1 = 7`，现有实例级 fallback（`shift_dn_unit.py:36`，cluster 按 `num_kernels-1` 注入）**已够用**，**不触发门控子集**。
- 故本轮**不**给 SHIFTDN 加 `smc_id_en` code_field（`modules.yaml`/`shiftdn.yaml` schema 不动），**不**做指令字段解码。完整解码 = ADR-022 待办 #3，**独立 ADR**（user 有表 547 已列 smc_id_en，可后续忠实补，但属 schema 变更须 hco + ADR，不混入本轮）。记 **DEBT-024-SMCIDEN-DECODE**。
- ⚠ 风险：fallback 正确性依赖「8 SMC 全员、无子集 reduce」假设；任何 K<8 / 子集 reduce 变体需先做待办 #3。本 ADR 仅覆盖满-8 reduce。

### D4. SMT / axiom：只验不 UNSAT，不改语义
- `add_chain_iq_propagation_axiom`（`resource_axioms.py:344-387`，按 `range(smc_id_en+1)` 给每沿途 SMC 建 IQ slot 占用，满-8 时每条 SHIFTDN 占 8 slot）单 SMI 语义待重核（ADR-022 待办 #4）。本轮**只验**它不让 matmul_parallel_k 的 SHIFTDN DFG UNSAT / 误调度（仿 ADR-023 D5.2），**不改语义**。
- **K 路径首次真正触发该 axiom 的 SHIFTDN 分支**（fma 走 SHIFTUP）。**实测兜底（HCO 建议，不能只 assert 不 UNSAT）**：实施时显式打印该 axiom 对 matmul_parallel_k 产生的约束条数 + 求得的 II，确认 8-SMC × 多 SHIFTDN op 不把 II 推到 UNSAT。若 UNSAT/错调度 → 升级为本轮必处理，按 routing **必经 hco + ADR**。
- 检查 SHIFTDN 有无类似 ADR-021 被删的 worst-case fold（`lat_p += smc_id_en`）残留——HCO 已代码确认 **三处 fold（`dependency_axioms.py:53-64` + `ucode_solver.py:233-235/266-267`）已被 ADR-021 全删**，SHIFTDN 边权 = unit lat(=1)，无残留。

### D5. 验收形状：单输出落 SMC_0（1/1，非 8/8）
- K-reduce 输出是**单个 y**（在 SMC_0），不同于 N-parallel 的 per-SMC 8 份。`auditor` / `pipeline_runner` 的「检查 SMC_0 truth alignment」（main_verify.py:111）天然适配，但**多-SMC 输出比对逻辑需识别「K 维只比 SMC_0、不要求 8/8」**。实施须确认 auditor **不因 follower SMC（1..7）输出 bank 是未定义/旧值而误报 FAIL**（8/8 框架默认逐 SMC 比对，K 维须排除 follower 输出）。
- 主验收：`matmul_parallel_k --mode e2e` → SMC_0 的 y bit-perfect（atol=5e-3，同 matmul）。

### D6. 可证伪验收门（仿 ADR-023 D4，硬性）
- `test_matmul_k_partial_per_smc`：N=8，断言每 SMC k 的 FPMUL partial = a[k]*b[k]（reduce 前的 per-SMC 中间值正确）。
- `test_shiftdn_gather_sum_single_truth`：N=8，构造可分辨 partial（如 partial_k=2^k），断言 SMC_0 收到的 SHIFTDN 输出 = Σ_k partial_k，且**漏掉任一 SMC 贡献即失败**（守「全员 gather」契约，专杀「只取 chain 顶端单值」式 bug——即现 sim 纯搬运语义若未改会暴露）。
- `test_matmul_k_follower_output_excluded`（HCO 建议，对应 D5 风险）：断言 auditor 对 SMC_1..7 follower 输出 bank **不纳入比对、不误报 FAIL**。
- 轻量 axiom 兜底（HCO 建议，对应 D4）：断言 matmul_parallel_k 经 `add_chain_iq_propagation_axiom` 后 **SAT 且 II 在预期范围**。

### D7. 流程（routing 强制）
- 本 ADR（doc-first）→ **hco 复核**（SHIFTDN 语义 + chain-reduce + D2 sim 中转裁定 + D3 fallback 可接受性 + D4 axiom 不破）→ 实施 → `--mode hw-parity` 行为变化 **必调 evaluator** 复核 → 全回归（pytest + fma_parallel_n 8/8 不回归 + matmul 单 SMC 不回归）。
- 升 Accepted 条件：matmul_parallel_k SMC_0 bit-perfect + D6 两单测 pull + evaluator 背书 +（最终）ADR-022 待办 #8 VCS 实测（SHIFTDN reduce 固件 vs VCS）。

## Consequences

### 正面
- 首次落地 K 维 cross-SMC reduce，补上 ADR-018 v7 Stage 2 的 stub。
- 复用 model A 现有基建（N-kernel + 同拍中转），sim 改动 = 加一个对称于 `_broadcast_shiftup_fanout` 的 `_gather_shiftdn_reduce`，远小于从零建 fabric。
- 绕开 configurable 4-operand UNSAT（手搭 DFG），不碰高 blast-radius 的 `lower_fp32addtree_5to1`。

### 风险 / 债务
- **DEBT-024-REDUCE-DFG（HCO BLOCK-2，最根本）**：model-A sim-sum 把 K-reduce 真实 compute-DFG 坍缩为零 op，SMC_0 无任何加法节点参与 reduce，SMT 只见 N 条 SHIFTDN、不见加法器资源。真硬件 reduce 更可能是 N-1 single-source SHIFTDN + per-hop FPADD/addtree。**「reduce 的 compute-DFG 缺失」**闭合需 RTL reduce 微指令表（user 现无）。**勿误认 K-reduce 已忠实落地。**
- **DEBT-024-REDUCE-TIMING（并入 DEBT-023-B）**：model A 抹平 SHIFTDN 逐拍下行 + 累加时序。**与 fma 不同，SHIFTDN 路径现在真被触发**——B（真 fabric）的缺失从「潜伏」变「已暴露面更大」。需 RTL 表 545/547/548 之外的 reduce 逐拍时序表闭合（user 现无）。
- **DEBT-024-SMCIDEN-DECODE**：smc_id_en 走 fallback，仅覆盖满-8 reduce；子集 reduce 需先做 ADR-022 待办 #3（schema 变更 + hco）。
- **add_chain_iq_propagation_axiom 语义待重核**（D4，ADR-022 待办 #4）：本轮只验不 UNSAT + 实测约束条数兜底。
- **验收降级提示**：SMC_0 bit-perfect 是**终值 parity**，对 SHIFTDN reduce 时序 + reduce-DFG 忠实性**增信近零**（同 ADR-023 evaluator caveat）。
- 单输出验收路径（D5）是新形态，须确认 auditor 不误报 follower。

## Supersedes / Reverses
- **ADR-018 v7 Stage 2**（`parallel_dim="K"` SHIFTDN chain reduce stub）：本 ADR 给出首个真实实现（model A 中转版）。
- 不动 ADR-023 N 维路径（SHIFTUP broadcast）；二者并存。

## Open questions（HCO 已裁定，留档）
1. **D2 landing**（已决）：落 SMC_0。理由 = D5 单输出形态一致 + SHIFTDN relay 终点 = smc_id 0；**非**「更贴 RTL reduce 语义」（RTL 表 545/547/548 无 fan-in-sum 操作，两选项对 RTL 都无对应）。
2. **D2 求和位置**（已决）：model-A 阶段取 sim gather-sum；DFG 内 per-hop FPADD 更贴真 fabric 但 model A 表达不了跨-SMC 寄存器读 → 记 DEBT-024-REDUCE-DFG，待 RTL reduce 微指令表。
3. **D4**（已裁）：`add_chain_iq_propagation_axiom` 确属 ADR-022 待办 #4 范畴，本轮不改语义，但须实测约束条数 + II 兜底（见 D4），不能只 assert。

## References
- [ADR-022](ADR-022-single-smi-global-instruction-stream-model.md)（单 SMI 模型 + 8 步待办：#3 SHIFTDN smc_id_en 解码 / #6 K 路径 parity / #8 VCS）
- [ADR-023](ADR-023-single-smi-emitter-sim-impl-model-a.md)（N 维 SHIFTUP model A，本 ADR 的对称范式）+ [DEBT-023-inventory.md](DEBT-023-inventory.md)
- [ADR-019](ADR-019-shift-cross-smc-yaml-sim-alignment.md)（SHIFTDN errata task #19，fallback=31 由来）
- [ADR-018](ADR-018-multi-smc-scheduling-design.md)（v7 Stage 2 K 维 stub 出处）
- [ADR-004](ADR-004-doc-first-principle.md) 文档先行
- 代码触点：`verification_v2/ops/matmul_op.py`（新算子）/ `verification_v2/main_verify.py:OPERATOR_MAP`（注册）/ `sim/smc_simulator/system/cluster.py`（新 `_gather_shiftdn_reduce`）/ `sim/smc_simulator/units/shift_dn_unit.py`（gather 状态）/ `verification_v2/infra/auditor.py`（单输出 SMC_0 验收）/ `src/synthesis/axioms/resource_axioms.py:344`（只验不 UNSAT）
- RTL spec 表 545（SHIFTDN 接口）/ 547（上行微指令，已列 smc_id_en）/ 548（下行微指令）
- 现状证据：`shift_dn_unit.py:67-72`（纯搬运无加法）/ `configurable.py:23-26`（K stub）/ `standard_kernels.py:33`（4-operand UNSAT 源）/ 本 session 隔离实验（4-operand 单 SMC UNSAT 到 II=64）
