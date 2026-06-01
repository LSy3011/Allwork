---
adr_id: ADR-026
title: Matmul_Q（matrix×matrix GEMM）—— 广播左 + DS transpose + 每 SMC 多列点积
decided: 2026-06-01
last_verified: 2026-06-01
still_holds: proposed
implements: ADR-022
builds_on: ADR-025
---

# ADR-026 · Matmul_Q（matrix×matrix，广播左 + DS transpose）

> **状态：Proposed（doc-first，代码未改）** —— 把 [ADR-025](ADR-025-matmul-parallel-n-broadcast-addtree.md) 的 vector×matrix 原语推广到 **matrix×matrix GEMM**，聚焦 RTL 表 711 的 **Matmul_Q**（LLAMA2 Q 投影）的确定布局。新增硬件依赖 **DS（Data Shuffle）模块**（RTL 表 585–588）做激活 transpose → **必过 hco**。
>
> **一句话**：Matmul_Q = 广播激活行（左，非转置间插，经 DS transpose 列对齐→行对齐）× 每 SMC 本地多个权重列（右，转置间插）做点积（SMC 内 addtree + cumulative K 累加）→ 每 SMC 产多列×多行输出（转置间插）。复用 ADR-025 的广播+点积原语，新增**多左行 M + 每 SMC 多列 + DS transpose + 多-tile K**。
>
> **HCO 复核：APPROVE-with-notes（agentId `a8b3968af56491f38`）** —— 4 个阻断项已收口进下方 D2/D3/D6 + Open questions（均文档级，无不可让步契约被破坏）：**B1** 多列锁定为「4 条独立点积链」（非多通道，单 addtree 无多 old_i）；**B2** 多左行锁定「时间串行单广播源」（非并行多源）；**B3** DS 锁定「不进 SMT、纯 sim 变换、modules.yaml 不配 IQ/sel」；**B4** 激活 DS transpose 锁定「必须真搬」（relabel 只适用转置间插张量，方向纠正）。新增 DEBT-026-DS-FIELDS。**B1–B4 收口前不进入实施。**

## Context

### Matmul_Q 确定布局（RTL 表 711，LLAMA2 7B Decode，每头一个 matmul）
| 张量 | 逻辑 shape | 存储 | SMC 分布（PARAM_SMC_NUM=32 原始 / 8 缩放） |
|---|---|---|---|
| 激活 X（左，**广播**） | iLen × 4096（K=4096） | **非转置间插** | iLen 行散到 SMC，每 SMC iLen/32 行 × 4096 |
| 权重 W（右） | 4096 × 128（N=128） | **转置间插** | 128 列散到 SMC，**每 SMC 4 列**（每列长 K=4096） |
| 输出 Q | iLen × 128 | **转置间插** | 每 SMC iLen × 4 |

- 计算：`Q[i,j] = Σ_{k=0..K-1} X[i,k]·W[k,j]`。广播 X 行 i（长 K）→ 每 SMC 与本地 4 个权重列各做点积 → 每 SMC 每行产 4 个输出。
- **广播左矩阵**（表 710：广播左则右必须转置存储；Matmul_Q 右=转置间插 ✓）。
- 激活 非转置间插 → 读回 tile 列对齐 → **DS transpose 转行对齐** → 再广播（表 711「激活矩阵排序微指令」）。

### 与 ADR-025（vector×matrix）的关系：同原语 + 4 项推广
ADR-025 已跑通 8/8 的原语 = 广播一行 × 每 SMC 一列点积（SMC 内 cumulative addtree）。Matmul_Q 在其上加：
1. **多左行 M**（iLen 行）→ 循环原语 M 次 → M 行结果。
2. **每 SMC 多列**（4 列）→ 每行每 SMC 产 4 个点积（非 1 个）。
3. **DS transpose**（激活 非转置间插 → 行对齐）—— 新硬件模块。
4. **多-tile K**（K=4096 远超单 tile 4-lane）→ 部分和写回 UR + 跨 tile cumulative 累加（DEBT-025-MULTITILE）。

### DS（Data Shuffle）模块（RTL 表 585–588，user 2026-06-01 提供）
- **功能**：UR→UR 的 transpose / reshape（表 587 `op_type`）。transpose 用 `t_dim0_cnt`/`t_dim1_cnt`（1..32，PyTorch transpose 语义），`PARAM_TRANSPOSE_SIZE=32`。
- **跨-SMC 指令链**：`CRU_DS`/`CRD_DS` 上/下行指令寄存器 + `smc_id_en`（≥SMC_ID 则使能）——与 SHIFTUP/SHIFTDN 同模式。
- **乱序握手**：`inst_id`(4bit) + 下行 `done`，`PARAM_OUTSTANDING_INST_NUM=4`（SMI 最多 4 条在飞）。
- **AXI Stream**：tid 路由（STB/LDB，`b00..b11`）。
- **关键优化**：表 711「转置间插经过转置实际不需要进行操作」—— 某些布局下 transpose 是 **relabel/view（不搬数据）**。

## Decision

### D1. 缩放的 Matmul_Q（8-SMC sim 可验收实例，保结构）
LLAMA2 原始是 32-SMC/K=4096，无法直接在 8-SMC sim 跑。取**结构保真的缩放实例**（PARAM_SMC_NUM=8）：
- Stage-1：`K=4`（单 tile，复用 ADR-025 原语）、`cols_per_smc=1`、`M`=小（如 4）。X[M,4] 广播 × W[4, 8]（1 列/SMC）→ Q[M, 8]（每 SMC M 个输出）。**先打通「多左行 + DS transpose + 每 SMC 输出向量」plumbing。**
- Stage-2：`cols_per_smc=4`（保 Matmul_Q 的 4 列/SMC 结构）→ 每 SMC M×4 输出。
- Stage-3：`K` 多-tile（大 K + 部分和写回 UR + cumulative 累加）。
- 缩放参数写成算子可配（不硬编码），便于后续放大到 32-SMC/真 K。

### D2. compute = 复用 ADR-025 原语 × 多行 × 多列
- **每 SMC 每个权重列 = 一条独立点积链（HCO B1 锁定）**：`cols_per_smc` 列 → `cols_per_smc` 个**独立** FP32ADDTREE5TO1 op，各带**自己的** `loop_carried_depends_on` 自环（独立 `old_i` 累加器）+ 独立 `iq_id` + 独立输出 bank/addr。**不走「单 addtree 多通道 S0..S3」**——单个 addtree op 只有一个输出寄存器，4 列共用会让 4 个 `old_i` 互相覆盖 → 静默算错（比 UNSAT 更危险）。`reduction_per_channel` 是 addtree **内部 lane 归约**参数，**不是**多累加器。真多通道独立 partial-sum 寄存器需 RTL 背书（表 79/585–588 未覆盖），**无背书不选**。
- **多左行 M = 时间串行、单广播源（HCO B2 锁定）**：M 行**逐行串行**广播（一次广播一行，M 次迭代复用同一单广播源），**非空间并行多源**。`cluster._broadcast_shiftup_fanout`（cluster.py:113）结构上只支持单 head 单 `bcast_val` → 任何「M 行并行多广播」会撞 `configurable.py:140-143` 的 multi-broadcast sim 缺口，本轮 BLOCK。每行产一组（cols_per_smc 个）输出，写到 per-SMC 输出不同地址（转置间插 layout）。
- 复用 ADR-025 已验证：SHIFTUP 单源广播 fan-out（ADR-023）、cumulative addtree 自环 `loop_carried`（II≥4，每条链各一份）。

### D3. DS transpose 取 model-A 数值建模（先功能后时序）
- **本轮只建模 DS 的 transpose 数值功能**：UR→UR 按 `t_dim0_cnt`/`t_dim1_cnt` 做 PyTorch-语义转置（列对齐→行对齐），让广播拿到正确的激活行。
- **激活的 DS transpose 必须真搬数值（HCO B4 锁定，方向纠正）**：Matmul_Q 激活是**非转置间插**（表 711 行 1），读回列对齐 → 必须真转置成行对齐。表 711「转置间插经过转置实际不需操作」的 **relabel/no-op 只适用已是转置间插的张量**（权重/输出，而它们在 Matmul_Q 中**不走** DS）。**严禁**把激活 DS 当 no-op/view（会拿到列对齐错数据 → 静默算错）。`ShuffleUnit` 必须真实现 PyTorch 语义转置数值；`test_ds_transpose_numeric` 是硬门不可跳。
- **DS 不进 SMT 调度、纯 sim 数据变换（HCO B3 锁定 Open Q3）**：DS 走 sim 端直接 UR→UR 数据变换（参照 `cluster._broadcast_shiftup_fanout` 接入位置），**不进 DFG/不进 SMT**。故 `hw_config/modules.yaml` 的 DS 条目**不配 IQ/RTOVR sel 等调度字段**（`rtovr_config.yaml` consumers_dr 现止于 33，不新增 DS sel），只作**纯文档性 spec 条目**并标 `simulation_only: true`，避免「声明却无调度引用」的死 module 反模式。DS model-A **不经过 STB/AXI 通路**（`cluster.py:70` BusArbiter/AXI 是死代码原型），直接 sim UR 变换。
- **defer 到债务**：
  - **DEBT-026-DS-CTRL**：CRU_DS/CRD_DS 上下行指令链 + `inst_id`/`done` 握手 + `OUTSTANDING=4` + AXI Stream tid 路由 + reshape op。控制/时序，model-A 终值 parity 不依赖（同 ADR-023 SHIFTUP 取舍）。
  - **DEBT-026-DS-FIELDS（HCO 待商榷）**：DS 微指令逐字段位宽（`op_type`/`data_type`/`t_dim0_cnt`/`t_dim1_cnt`/`inst_id=4`/`smc_id_en=5` 等）以 user 表 587/588 为准，本仓无对照源；实施 modules.yaml DS code_fields 前须 user 提供逐字段位宽表（同 DEBT-023-COMPUTE-ADDR 类缺口）。
- 新增 **sim 单元 `ShuffleUnit`**（`sim/smc_simulator/units/`，参照 shift_*_unit 跨-SMC 链骨架但本轮只实现 transpose 数值）。**按 routing：modules.yaml 新 module + 新单元 → 必过 hco + 本 ADR。**

### D4. 验收形态：每 SMC 输出 = M × cols_per_smc 的 2D tile（转置间插）
- 输出不再是 ADR-025 的单标量；每 SMC 产 `M × cols_per_smc` 个值。golden shape `(smc_num, M*cols_per_smc)`（或保 2D 语义），verifier 走 per-SMC 8/8（复用 `_verify_multi_smc_per_smc` 的 2D 路径）。
- 主验收：缩放 Matmul_Q `--mode e2e` @ PARAM_SMC_NUM=8 → 8/8 bit-perfect；golden = numpy `X @ W` 的对应 per-SMC 切片。
- 按 routing：`--mode hw-parity` 新算子物理验收 → **必调 evaluator**。

### D5. SMT / axiom：复用，只验不 UNSAT
- 复用 ADR-025 的 SHIFTUP 广播 + addtree cumulative 递归。多列（多通道 addtree）+ 多行可能改变资源/递归 II —— 本轮**只验不 UNSAT / 不错调度**，axiom 语义不动（ADR-022 待办 #4 范畴）。DS 若进 SMT 调度需 hco 复核其 IQ 占用建模。

### D6. 可证伪验收门
- `test_matmul_q_multi_row`：M 个左行各产正确结果行（漏行/错行 → fail）。
- `test_matmul_q_multi_col_independent_accum`（HCO B1 强化）：cols_per_smc=4，构造 4 列权重各不同，断言每 SMC 4 个输出 = 4 个**独立**点积且**互不串扰**（专杀「4 列共用 old_i 互相覆盖」，非仅「各不相同」）。
- `test_ds_transpose_numeric`（HCO B4 硬门）：DS transpose 按 t_dim0/dim1 真做 PyTorch 转置；配对偶用例 `test_ds_activation_must_actually_transpose`（非对称矩阵，no-op/view 实现必 fail）。
- `test_matmul_q_single_broadcast_across_M_rows`（HCO B2）：M>1 行，断言全程 `_broadcast_shiftup_fanout` active 广播源恒为 1（M 行间串行复用，非并行多源）。

## 实施进展（2026-06-01）

> **Stage-1 DONE**（evaluator ACCEPT-with-caveats，agentId `a32159188c72f6092`）：`ManualMatmulQOperator`（`matmul_op.py`）落地 **M=4 行 × cols_per_smc=1 × K=4 单 tile**，`matmul_q --mode e2e` **8/8 bit-perfect + Truth Audit SUCCESS**（exit=0）；pytest 71 pass/6 skip，fma/matmul_parallel_n/matmul 无回归；4 可证伪单测。
>
> **实施关键发现（补充本 ADR 设计）**：
> 1. **M ≤ addtree pipeline 深度(latency=4)**：addtree 是 cumulative 且**无 reset**（`fp32addtree5to1.yaml` 仅 VALID+smc_id_en）。II=1 下 row 0..3 靠 pipeline 隔离 old_i=0 独立；**M>4 时 row i 累加 row(i-4)**（evaluator 复现污染）。M>4 的正解 = **8 行映射 8 个 addtree 通道(S0..S7)** 或 **UR 部分和**，记 **Stage-2**。
> 2. **输出 1/词稀疏布局**：1-col addtree 输出在 D0 lane0，每行写一个 128-bit 词（lanes 1-3=0）。golden shape `(smc_num, 4*M)` 忠实反映（verifier 按 4/词解包）。**密集 4/词打包 = Stage-2**（cols_per_smc=4 用 4 通道天然填满一词）。记 **DEBT-026-PACK**。
> 3. **权重 b 平铺 M 份**（`prepare_input` 返回 (smc_num, M*K)）：让 b-read 逐行读到同列 **且 auditor 用同一 `input_data["b"]` 建模一致**，避免 Truth Audit 假阳性（auditor 用未平铺 b 会在 ITER≥1 期望 0）。
> 4. **DS transpose 未实施**（激活按行对齐预排，单 tile 下 transpose 无意义）—— Stage-1b/Stage-2 才需，`ShuffleUnit` + modules.yaml DS 条目尚未加（hco DS 触发未 fire，符合范围）。
>
> **D6 门的 Stage 归属（evaluator caveat 订正）**：D6 列的 `multi_col_independent_accum`（B1，cols_per_smc=4）+ `ds_transpose_numeric` 对（B4，DS）属 **Stage-2 门**，Stage-1（1 列、无 DS）不覆盖。Stage-1 实测门 = multi_row_dot / per_smc_distinct / single_broadcast_source / m_within_pipeline_depth（`tests/unit/test_matmul_q.py`）。

## DS 可排除性结论 + 后续 Stage backlog（2026-06-01）

### DS 是否阻塞 matmul？——**否，可排除**（Stage-1 实证）
- **matmul 计算本身不依赖 DS**。Stage-1 `matmul_q` 8/8 跑通**全程无 DS**：`inject_data_per_smc` 把激活按**行对齐**直接注入 SMC_0，无任何 transpose 步骤。
- **DS 的唯一作用**是「激活以 非转置间插 存储、读回列对齐 → 在硬件 UR→UR 转置成行对齐再广播」（表 711 激活矩阵排序微指令）。在**验证/sim 流里我们控制注入布局**，可总是预排成行对齐 → **DS 可完全跳过**。
- **取舍（model-A 性质）**：跳过 DS = 在 data-prep 层「假做」transpose（预排行对齐），产**正确终值**，但**不建模** 非转置间插 的硬件 transpose（数据搬运 + DS 时序）。真实固件若激活确为 非转置间插，需 DS 做在线转置——这是**忠实度里程碑**，非「能否跑通」问题。
- **推论**：**Stage-2（4 列 / 大 K / M>4）可全程 DS-free 推进**，靠 data-prep 预排激活。DS 实施推迟到「忠实建模 非转置间插 在线转置 + 串联 matmul 布局」的独立里程碑，且 gated on DEBT-026-DS-FIELDS（RTL 逐字段位宽）+ hco。

### Stage-2a 实施发现（2026-06-01，初判，部分被下方 2026-06-01-b 订正）
尝试 cols_per_smc=4（4 通道密集打包）撞 **结构性 UNSAT**（实测 II=4..15 全 UNSAT，去广播注入仍 UNSAT）：
- **根因**：单个广播 a-read 喂 4 个串行 FPMUL（1 个 FPMUL 单元）的 FPMUL_S0 路由 = matmul_op 注释点名的「单物理乘法器串行发射 + RTOVR 强对齐选通冲突」。`ManualMatmulOperator` 靠 **4 个独立 a-read（不同 iq_id 破对称）** 解 SAT。
- **张力**：4-col 要 4 个独立 a-read（SAT 需要）vs 广播只能单源（B2 / multi-broadcast sim 限制）。二者直接冲突。
- **修法（local-buffer 间接，初判）**：广播 a 一次 → SHIFTUP → 写本地 bank → 各 SMC 4 次独立读 → 4 FPMULs。**↓ 下方 2026-06-01-b 证伪：local-buffer 全 UNSAT。**

### Stage-2a 落地（2026-06-01-c：**真广播 direct 交付**，amended by ADR-028）

> **⚠ 2026-06-01-c 订正（[ADR-028](ADR-028-broadcast-shiftup-latch-window-relax.md)，hco APPROVE-with-notes agentId a5f93c926224c323c）**：下方 2026-06-01-b 的「走 model-A 预分发 + 广播通路 BLOCK on RTL 表 78/79（ADR-027）」**前提被证伪**。真根因不是乘法器数（`fpmul.yaml throughput=1+PREC_32 count=4` 已定死=1 个 SIMD-4-lane 跨拍 4 次）、也不是 sim 多广播缺口，而是 `dependency_axioms` 的 **RTOVR 锁存严格约束 `t_latch==prod.t+lat`** 把一个 broadcast SHIFTUP 的多消费者锁存钉死同拍 → 同拍写 FPMUL_S0 冲突。user 裁定 **Q-a（DR_SHIFTUP_D 驻留、支持 N-RTOVR 多读）+ Q-b（时间维 scatter）** 后，**放宽该 axiom 为驻留窗口区间即解**（ADR-028）。matmul_q2 现走 **BROADCAST=True/direct 真广播**（单 SHIFTUP 喂 4 列，SHIFTUP 广播指令在固件中），8/8 bit-perfect，**取代下方 model-A 预分发**。ADR-027 标 superseded。**Stage-2b 启用 M>1 多列前须先补「M>1 + 单 SHIFTUP 喂多列」e2e**（hco note：驻留窗口上界 II-1 的多行正确性当前仅逻辑+verifier 上界守、无实测）。

**（历史 2026-06-01-b）结论：DEBT-026-PACK（dense 4/词打包）已落地 + 8/8 bit-perfect（exit=0）**；广播-到-4列 的忠实通路曾被 hco BLOCK（根节点 Q2.1），隔离到 ~~[ADR-027](ADR-027-sim-multi-broadcast-shiftup.md)~~（已 superseded by ADR-028）。下方留存 model-A 期推理。

- **local-buffer 修法证伪（SMT 全 UNSAT）**：实测 4 种 DFG 变体均 UNSAT —— ① 单 buffer 写 + 4 读同 cell；② 4 写 distinct 地址 + 4 读（4 RTOVR 消费单 SHIFTUP_D 撞强对齐）；③ 单写 + 4 读 distinct 读端口；④ 单写 distinct 地址。两道墙：4 RTOVR 读同一 SHIFTUP_D 源撞强对齐选通 / 4 读同一 intermediate cell UNSAT。**local-buffer 计划作废，勿再试。**
- **indep 修法（4 独立 SHIFTUP op）SMT-SAT@II=6 但 sim 多广播缺口**：`sim/.../kernel.py:516` SHIFTUP 数据硬连读端口 0（`get_read_data(0)`）+ 单 shiftup 单元/单 dr_d → 4 个广播消费者跨拍取值时偶列对/奇列 0（实测 sim[0]=32.92✓/sim[1]=0✗/sim[2]=24.45✓）。即 `configurable.py:140-143` 点名的 multi-broadcast sim 缺口。
- **hco 复核 BLOCK（agentId addc26afe31101fd8）根节点 Q2.1**：`hw_config/specs/units/fpmul.yaml PREC_32 count=4` → FPMUL 是 SIMD 4-lane；单 SMC「4 列」**很可能是 1 个 FPMUL 跨拍调用**而非 4 物理乘法器。当前 4-FPMUL-op DFG 是绕 SMT 强对齐的**脚手架，数据通路忠实度未证**。在此基座上叠 sim sticky-fix 会产出「8/8 但忠实度增信近零」（同 ADR-023 陷阱）。**需 user RTL 表 78/79 单 SMC lane 组织裁定（DEBT-026-BCAST-MULTI）。**
- **交付决策（user 采纳 hco 建议）**：本轮走 **BROADCAST=False（model-A 预分发激活）** 隔离广播通路争议 —— 激活在 data-prep 复制注入每 SMC bank0 的 COLS 个词，绕开整个广播-到-4列 通路，单独闭合 dense 打包。`ManualMatmulQ2Operator`（matmul_op.py）+ `matmul_q2` 注册：`--op matmul_q2 --mode e2e` **8 SMC bit-perfect + exit=0**（rigid-audit 假阳性已修：权重按 per-列 meta_name `b{c}` 建模，避 auditor 把 4 列当 col0）。诊断开关 `BROADCAST=True` 保留 buffer/indep 分支供 ADR-027 复现。
- **机制 = 单 addtree 4 通道 S0..S3**（见下方 B1-errata，hco 重裁推翻原 B1）。
- **忠实度边界（model-A）**：BROADCAST=False **不建模 SHIFTUP 广播指令**（比 Stage-1/ADR-025 退化），是「能否跑通 dense 打包」与「广播通路忠实」的解耦取舍。广播通路忠实留 ADR-027，gated on RTL 表 78/79。
- **新债务**：
  - **DEBT-026-BCAST-MULTI（hco）**：单 SMC「广播激活 × 本地多列」的乘法-累加 lane 组织（1 FPMUL SIMD 4-lane / 4 乘法器 / 时分）+ DR_SHIFTUP_D 保持语义，须 user RTL 表 78/79 + 711 闭合。是 ADR-027 的 gating。
  - **DEBT-026-ADDTREE-8CH-RTL（hco）**：addtree 8 通道 old_i per-lane 独立性仅 sim + spec yaml 背书，无 RTL `.v`/C 模型可对照（本仓全盘无源）。升 Accepted 需 user RTL（addtree 输出寄存器 lane 布局 + old_i 取数路径）或 VCS。

### B1-errata（2026-06-01，hco 重裁，**推翻原 B1**，agentId addc26afe31101fd8）

> **新裁定**：cols_per_smc 列 = **单个 FP32ADDTREE5TO1 op 的多通道 S0..S{C-1}**（`reduction_per_channel=4`），**非** C 个独立 addtree op。依据：(a) `sim/smc_simulator/units/fp32_add_tree_unit.py:112-145` 与 spec `hw_config/specs/units/fp32addtree5to1.yaml:43-48 ACC_CHANNEL count=8` 均确立 **8 个 per-lane 独立累加器通道**（`add_result[i]=Si[0..3]+old_i`，old_i=输出寄存器 lane i，通道间物理隔离）；(b) `ManualMatmulOperator`（`matmul`）以同机制 e2e bit-perfect（hco 亲跑 exit=0，4 通道产 4 distinct 值不串扰）；(c) `test_matmul_q2.py::test_8channel_no_crosstalk` 直驱 sim addtree 8 通道 distinct 输入断言 0 串扰。
> **原 B1 因果倒置**：「单输出寄存器、4 列共用 old_i 互相覆盖」错 —— 真正会互相覆盖的是「4 个独立 addtree op 在**单物理** addtree 单元不同拍各写**整个** D0/D1（128bit）」（后 op 覆盖前 op lane）；单 op 4 通道一拍并行算全 lane 反而不覆盖。
> **遗留约束**：①M=1（Stage-2a 聚焦 4-col 打包）；M>4 多行映射 8 通道仍是 Stage-2b（addtree pipeline 深度=4，见 Stage-1 发现 #1）。②cumulative 按 tile 数定（单 tile K=4 → False；多 tile → True，Stage-2c）。
> **忠实度 caveat**：8 通道独立性仅 sim+spec 背书（DEBT-026-ADDTREE-8CH-RTL），升 Accepted 需 VCS/RTL。

### 后续 Stage backlog
| Stage | 内容 | 依赖 | 是否需 DS |
|---|---|---|---|
| ~~**Stage-2a**~~ ✅ | cols_per_smc=4 → 单 addtree 4 通道密集 4/词打包（**DEBT-026-PACK 已落地，8/8 exit=0**，model-A 预分发激活）。广播-到-4列 忠实通路 → ADR-027（DEBT-026-BCAST-MULTI，gated on RTL 表 78/79） | 复用现有 | 否（预排激活） |
| **Stage-2b** | M>4 多行 → 8 行映射 8 addtree 通道(S0..S7) 或 UR 部分和（解 M≤4 pipeline 限制） | hco（通道映射建模） | 否 |
| **Stage-2c** | 大 K 多-tile（K=4096）→ 部分和写回 UR + 跨 tile 累加（解 DEBT-025-MULTITILE） | — | 否 |
| **Stage-3 (DS)** | `ShuffleUnit` + modules.yaml DS 条目 + 非转置间插 在线转置 + 上下行握手 | **DEBT-026-DS-FIELDS（user RTL 表 587/588 逐字段位宽）+ hco** | 是（本体） |
| **Accepted** | 8-SMC 固件 vs VCS 实测背书 | user RTL/VCS | — |

> **优先级建议**：Stage-2a/b/c 全部 DS-free，可在现有基建上推进真实 Matmul_Q 规模；DS（Stage-3）作为忠实度里程碑最后做，避免被 RTL 字段缺口（DEBT-026-DS-FIELDS）阻塞主线。

## Consequences

### 正面
- 首个 matrix×matrix GEMM，对齐 RTL 表 710/711 的真实机制（广播左 + 每 SMC 列点积 + 转置间插 + DS transpose）。
- 最大复用 ADR-025（广播原语 + cumulative 递归）+ ADR-023（SHIFTUP fan-out）。
- DS 以 model-A 数值落地，避免一上来建全控制链。

### 风险 / 债务
- **DEBT-026-DS-CTRL**：DS 的上下行指令链 + inst_id/done 握手 + OUTSTANDING=4 + AXI tid 路由 + reshape，本轮 defer，model-A 不验其时序。闭合需 RTL DS 时序细节 + VCS。
- **DEBT-026-DS-FIELDS（HCO）**：DS 微指令逐字段位宽待 user 表 587/588 逐字段提供，本仓无对照源；实施 modules.yaml DS code_fields 前须确认。
- **DEBT-025-MULTITILE**（继承）：大 K 多-tile 部分和写回 UR + 累加，Stage-3 才做。
- **多列独立链资源/递归（B1 已锁机制）**：cols_per_smc 条独立链各占资源 + 各自 loop_carried，需实测不 UNSAT（多链 II 抬升风险）。
- **缩放 ≠ 真 Matmul_Q**：本轮 8-SMC/小 K 缩放，32-SMC/K=4096 真规模未验；终值 parity 走 model A，不验跨-SMC 广播/DS 时序（增信对 RTL 近零，升 Accepted 需 VCS）。

## Open questions（HCO 已裁定，留档）
1. **（已定，B4）** 激活 DS transpose **必须真搬**（非 relabel）；relabel 只适用转置间插张量（Matmul_Q 中不走 DS）。`ShuffleUnit` 真实现转置数值。
2. ~~**（已定，B1）** cols_per_smc 列 = 多条独立点积链，非单 addtree 多通道。~~ **【2026-06-01 推翻，见上方 B1-errata】** 实为：单 addtree 多通道 S0..S{C-1}（8 通道 per-lane 独立累加器）。原 B1 因果倒置。
3. **（已定，B3）** DS **不进 SMT 调度**，纯 sim 数据变换；modules.yaml DS 只作 `simulation_only` 文档条目，不配 IQ/sel（避免死 module）。

## References
- [ADR-027](ADR-027-sim-multi-broadcast-shiftup.md)（sim 多广播 / 广播激活喂多列通路忠实，隔离自本 ADR Stage-2a，BLOCKED on RTL 表 78/79）
- RTL 表 710（matrix×matrix 存储格式）/ 表 711（Matmul_Q 等 LLAMA2 举例）/ 表 585–588（DS 模块接口+微指令）（user 2026-06-01 提供）/ 表 78/79（单 SMC 乘法-累加 lane 组织，DEBT-026-BCAST-MULTI 待 user 提供）
- [ADR-025](ADR-025-matmul-parallel-n-broadcast-addtree.md)（vector×matrix 原语，本 ADR 复用 + 推广）
- [ADR-023](ADR-023-single-smi-emitter-sim-impl-model-a.md)（SHIFTUP 广播 model A）/ [ADR-022](ADR-022-single-smi-global-instruction-stream-model.md)（单 SMI 模型）
- [ADR-004](ADR-004-doc-first-principle.md) 文档先行
- 代码触点：`verification_v2/ops/matmul_op.py`（新算子，复用 ManualMatmulParallelNOperator 原语）/ `sim/smc_simulator/units/`（新 `ShuffleUnit`）/ `hw_config/modules.yaml`（新 `DS` module）/ `main_verify.py:OPERATOR_MAP` / `sim/smc_simulator/system/cluster.py`（DS 数据变换接入，参照 `_broadcast_shiftup_fanout`）
