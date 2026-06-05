---
adr_id: ADR-033
title: Matmul 激活转置的真 datapath = SMI_DS_T 跨-SMC（修正 ADR-026 DS 误用）
decided: 2026-06-05
last_verified: 2026-06-05
still_holds: proposed
supersedes_partial: ADR-026  # 仅推翻 ADR-026 的 DS/Stage-3 transpose 建模；ADR-026 的 compute 机制(广播左+每SMC列点积+addtree)保留
builds_on: ADR-022, ADR-023, ADR-025, ADR-026
---

# ADR-033 · Matmul 激活转置 = SMI_DS_T 跨-SMC datapath

> **状态：Proposed（doc-first per ADR-004，新硬件模块 → 必过 hco）**。基于 2026-06-05 完成的
> [v0.4.19 文档系统理解](../spec/aiacc-v0.4.19-understanding.md)（分歧台账 D-01）。
>
> **一句话**：matmul（matrix×matrix）激活的「列对齐→行对齐」转置，**真路径是 SMI_DS_T**
> （表596-598，跨-SMC：UR read → SHIFTDN gather → SMI_DS_T 转置 → SHIFTUP scatter → UR write，
> `[seq1051]`），**不是 [ADR-026](ADR-026-matmul-q-matrix-matrix-ds-transpose.md) 当初建模的 DS 模块**
> （表585-588，每-SMC、挂 STB↔LDB、仅单 SMC 内 UR→UR、用途是 DRAM writeback reshape）。
> ADR-026 的 compute 机制（广播左 + 每 SMC 列点积 + addtree cumulative）**不受影响、继续有效**；
> 本 ADR 只替换「激活转置如何发生」这一块（ADR-026 Stage-3 / DS 部分 superseded）。

## Context

### 不一致的来源：docx 里有两个 transpose 引擎
| | **DS（Data Shuffle）** 表585-588 | **SMI_DS_T（Data Shuffle_Transpose）** 表596-598 |
|---|---|---|
| 物理位置 | **每-SMC**（有 `SMC_ID`(5)），挂 STB↔LDB 的 AXI Stream 通道 | **SMI 中心**，单实例 |
| 指令寄存器 | `CRU_DS`/`CRD_DS` + `SMC_ID` | `CRI` + `INST_DONE`（数据经 SHIFTDN/SHIFTUP） |
| 数据来源/去向 | `PARAM_TRANPOSE_SRC=STB的UR读`、`DST=STB的UR写` → **单 SMC 内 UR→UR**`[seq888]` | 输入 `CRD_SHIFTDN`(129) ← 跨-SMC gather；输出 `DATA_OUT`(128) → SHIFTUP `[表596]` |
| 转置对象 | 本地 UR tile（`PARAM_TRANSPOSE_SIZE=32` 方阵） | 跨-SMC tile：`PARAM_SMC_NUM` 行 × `PARAM_UR_BYTE_CNT` 列 → 转置 `[seq1037-1039]` |
| 用途 | 写回 DRAM / store-buffer 本地 reshape `[seq1112]` | **matmul 激活广播前的行对齐转置**`[seq1242]` |

两者**不矛盾、各管一段**。ADR-026（2026-06-01）把 matmul 激活转置安到了 **DS** 身上，成因＝当时 user 只给了
表585-588，SMI_DS_T（表596-598）在更完整的 v0.4.19 才齐。ADR-026 自己也露馅过——它写「DS model-A 不经过
STB/AXI 通路」，可 DS 的唯一真实接口就在 STB/AXI 上，等于把 DS 抽空成纯 sim 变换，正因接错了引擎。

### 铁证：为什么 matmul 转置必须是 SMI_DS_T（跨-SMC）而非 DS（单 SMC）
- **matrix×matrix 过程** `[seq1239-1242]`：激活读回是 `PARAM_UR_BYTE_CNT/ACT_BYTE` 行 × `PARAM_SMC_NUM` 列的
  **跨所有 SMC 的 Tile，按列对齐**；必须经「**激活矩阵排序微指令**」「**转置为行对齐**」再广播。
- **SMI_DS_T 功能** `[seq1037-1039]`：「对所有 SMC 进行 UR 读，经 **SHIFTDN 传回给 SMI**」「转置为
  `PARAM_UR_BYTE_CNT` 行、`PARAM_SMC_NUM` 列的 Tile」「输出送到 **SHIFTUP** 对应的 SMI_IQ」。一字对上。
- **对比 vector×matrix** `[seq1197]`：B=2 时读回流水天然连续，**「不需要进行转置即可发出广播」**。
  → 转置只在 matrix×matrix 才需要，且**跨-SMC**（DS 是单 SMC，物理上做不到跨-SMC gather→broadcast）。
- **完整 datapath** `[seq1051]`：**UR read — SHIFTDN — SMI_DS_T — SHIFTUP — UR write**。

### 代码现状（无返工）
- `ShuffleUnit` / DS / SMI_DS_T 在代码里**全部不存在**（ADR-026 把 DS 整个 defer 到 Stage-3 没做）。
- `matmul_q`/`matmul_q2` 的「转置」是 data-prep 预排行对齐 + `BROADCAST=False/True` model-A，**从未建模任何硬件转置**。
- 所以本 ADR 是「把 ADR-026 没做的 Stage-3 从 DS 改成 SMI_DS_T」，不是拆已落地代码。

## Decision

### D1. matmul 激活转置 = SMI_DS_T 五段 datapath（取代 ADR-026 DS）
matmul（matrix×matrix）激活「列对齐→行对齐」转置，建模为跨-SMC 五段链：
```
UR read  →  SHIFTDN(gather)  →  SMI_DS_T(transpose)  →  SHIFTUP(scatter/broadcast)  →  UR write
 各SMC读一行     汇聚回 SMI         转置 SMC_NUM×UR_BYTE      广播行对齐数据到各 SMC      落回 UR/喂广播
```
- 输入 Tile = `PARAM_SMC_NUM` 行 × `PARAM_UR_BYTE_CNT` 列；输出 Tile = `PARAM_UR_BYTE_CNT` 行 × `PARAM_SMC_NUM` 列 `[seq1037-1039]`。
- 参数化（**勿硬编码 8**）：`PARAM_IN_LINE_NUM=PARAM_SMC_NUM`、`PARAM_IN_LINE_BYTE_CNT=PARAM_UR_BYTE_CNT`，保证换 32 不破（台账 D-02）。
- ⚠ **Tile 是逻辑视图，物理是串行流**（hco 订正）：输入 Tile 由 **N=PARAM_SMC_NUM 拍 SHIFTDN 逐行汇聚**而成（每拍一行 `CRD_SHIFTDN`=129bit=128 数据+1 有效位），`[seq1039]`「一行 PARAM_SMC_NUM 的数据分为多个 PARAM_UR_BYTE_CNT 发出」、`[seq1041-1042]` 间插读/输出。datapath 图里的「一张 Tile」是 N 拍串行 gather 的逻辑视图，**非一拍到齐**；model-A 下数值正确，但忠实建模须保留串行流性质（见 D6 ordering 门）。

### D2. SMI_DS_T 模块 spec（modules.yaml 新条目 + 新 sim 单元 → 必过 hco）
忠实文档表596-598：
- **接口** `[表596]`：`CRD_SHIFTDN`(129, 输入数据) / `DATA_OUT`(128, 输出寄存器) / `DATA_OUT_VLD`(1) / `CRI`(*, 输入指令) / `INST_DONE`(1)。
- **设计参数** `[表597]`：`PARAM_IN_LINE_BYTE_CNT=PARAM_UR_BYTE_CNT` / `PARAM_IN_LINE_NUM=PARAM_SMC_NUM` / `PARAM_INST_NUM=4`。
- **微指令** `[表598]`：`vld`(1) / `intlv_en`(1) / `intlv_grp`(1) / `intlv_in`(1) / `intlv_out`(1)。
- **定位**：SMI 中心模块（非每-SMC，无 SMC_ID）；与 RtoVR 同属「不进 per-SMC compute」类。

### D3. sim 取 model-A：转置数值忠实，控制/时序 defer
- **本轮只建模 SMI_DS_T 的转置数值功能**：sim 端把跨-SMC 收集的 `PARAM_SMC_NUM × PARAM_UR_BYTE_CNT` Tile
  做真转置（列对齐→行对齐），让广播拿到正确激活行。**必须真搬数值**（沿用 ADR-026 B4 教训：no-op/view 会拿到列对齐错数据→静默算错）。
- **SHIFTDN gather / SHIFTUP scatter 复用现有** `shift_dn_unit.py` / `shift_up_unit.py`（已建模、伪代码见理解文档 A2）。
  新增 **中心转置单元**（`sim/smc_simulator/`，参照 `cluster._broadcast_shiftup_fanout` 接入位置，做 UR→UR 跨-SMC 转置变换）。
- **SMT 边界裁定（hco B-BLOCK-1 收口，原 Open Q1）**：与 ADR-026 的 DS 不同——ADR-026 的 DS 是单 SMC 内
  UR→UR、SHIFTDN/SHIFTUP 不参与，model-A 自洽；**本 ADR 的五段链里 SHIFTDN gather 与 SHIFTUP scatter 是真 op，
  中心转置核夹在两个真 op 之间，存在 SMT 看不见的数据依赖断点**（调度器不知 scatter 必须等转置完成、而转置依赖全部
  gather 到齐；`[seq990]` 这条链「几十周期」靠 COND + SMI_IQ `EXTRA_DATA` 就绪同步）。**裁定**：
  - **Stage-A 取 (b) 整链 model-A 原子段**（同 ADR-023 SHIFTUP fan-out 同拍处理模式，对现有 SMT/SAT 零扰动、风险最低）：
    sim 端按 gather→transpose→scatter 顺序原子执行，**`modules.yaml` SMI_DS_T 标 `simulation_only: true`**，不配 IQ/RTOVR sel
    （避免死 module）。**caveat（必须显式）**：8/8 对「跨-SMC gather→transpose→scatter 时序」忠实度近零，同 ADR-023/026 陷阱。
  - **(a) 把 SHIFTDN→SMI_DS_T→SHIFTUP 数据依赖进 SMT**（中心核作 latency=k spec 节点，数值仍在 sim 算，让调度器看见
    scatter-after-gather 依赖边）= **忠实度里程碑**，gated on NEEDS-USER-FACT-033-DSTDONE + VCS，非 Stage-A 前置。
  - D6 新增 `test_gather_before_scatter_ordering` 把该依赖断点钉成可证伪门，防 model-A 静默乱序。
- **defer 债务**：
  - **DEBT-033-DST-CTRL**：`intlv_en/grp/in/out` 4 并发指令间插时序 + `CRI`/`INST_DONE` 握手 + `PARAM_INST_NUM=4` outstanding。model-A 终值 parity 不依赖。
    ⚠ **hco B-BLOCK-2**：SMI_DS_T 的 `INST_DONE`/`PARAM_INST_NUM=4` outstanding **不在 `[seq1003]` 列举的 5 个
    OUTSTANDING≠0 SMI_IQ（STB/STBAXI/LDB/LDBAXI/DS）之内**，文档未定义其完成信号接哪个 SMI-IS → 这是 v0.4.19
    本身未闭合的契约缺口，升格为 NEEDS-USER-FACT-033-DSTDONE（见下），不埋在 model-A defer 里。
  - **DEBT-033-DST-FIELDS**：`CRI` 指令逐字段位宽（表598 只给 5 个 1bit 字段，`CRI` 宽度标 `*` 未定）以 user 表为准，本仓无对照源。
  - **DEBT-033-COND-TIMING**：matmul 广播「几十周期」延迟靠 COND 等待 + SHIFTUP SMI_IQ 的 `EXTRA_DATA` 就绪（`[seq990/1050]`，台账 D-07）—— model-A 不建模 COND 数据就绪同步。

### D4. DS（表585-588）保留，但与 matmul 解耦
- DS 仍是真模块（每-SMC store-buffer reshape / DRAM writeback transpose），**不删不改**；只是**不再是 matmul 激活转置的引擎**。
- ADR-026 里「DS 做激活 transpose」的全部表述 + Stage-3(DS) backlog **superseded by 本 ADR**；ADR-026 的 compute
  机制（广播左、每 SMC 列点积、addtree 8 通道 cumulative、dense 4/词打包）**保留有效**。

### D5. 分阶段（保 ADR-026 已绿的 compute 不破）
- **Stage-A**：单 SMI_DS_T 指令（`intlv_en=0`）+ 真转置数值 + SHIFTDN gather / SHIFTUP scatter 串成链，
  喂给现有 matmul_q compute（替掉 data-prep 预排假转置）。`--mode e2e` @ PARAM_SMC_NUM=8 → 8/8 bit-perfect。
- **Stage-B**：`intlv_en=1` 多并发指令间插（`PARAM_INST_NUM=4`），对齐表598 interleave 语义（DEBT-033-DST-CTRL）。
- **Stage-C**：放大到 PARAM_SMC_NUM=32 参数化验证（NEEDS-VCS）。
- 缩放参数算子可配，勿硬编码。

### D6. 可证伪验收门
- `test_smi_ds_t_transpose_numeric`：跨-SMC tile（SMC_NUM×UR_BYTE，非对称矩阵）转置为 UR_BYTE×SMC_NUM，
  按 `[seq1037-1039]` 真转置；配对偶用例 no-op/view 实现必 fail（沿用 ADR-026 B4 硬门）。
- `test_smi_ds_t_is_cross_smc`：断言转置输入来自**所有 SMC 的 SHIFTDN gather**（非单 SMC 本地），
  专杀「退回 DS 单-SMC UR→UR」的回归。
- `test_matmul_via_smi_ds_t_datapath`：matmul_q 走 SMI_DS_T 五段链 e2e bit-perfect（终值 = numpy `X@W` 对应 per-SMC 切片）。
- `test_smi_ds_t_param_smc_num_scalable`：换 PARAM_SMC_NUM（如 4/8）转置维度随参数变，无硬编码 8。
- `test_gather_before_scatter_ordering`（hco B-BLOCK-1）：SHIFTDN gather 未到齐时 SHIFTUP scatter 不得读到 stale/零的中心 tile —— 断言转置核输出依赖**全部** N 行 gather 完成。Stage-A 取 (b) 原子段则为 sim 内序检查；若升 (a) 进 SMT 则验依赖边存在。专杀 model-A 静默乱序。

## Consequences

### 正面
- matmul 激活转置首次对齐 RTL 真机制（跨-SMC SMI_DS_T），修正 ADR-026 的引擎误用。
- 最大复用：SHIFTDN/SHIFTUP 已建模，compute 机制（ADR-025/026）不动，只补中心转置核。
- DS 与 matmul 解耦，各自语义清晰，消除「DS model-A 不经 STB/AXI」的自相矛盾。

### 风险 / 债务
- **DEBT-033-DST-CTRL / -DST-FIELDS / -COND-TIMING**：interleave 时序 / CRI 字段位宽 / COND 就绪同步，model-A defer，闭合需 user RTL + VCS。
- **缩放 ≠ 真规模**：8-SMC model-A 终值 parity，不验跨-SMC 转置/广播时序；升 Accepted 唯一路径 = VCS（still_holds: proposed）。
- **NEEDS-USER-FACT-033-CRI**：`CRI` 指令字段逐字位宽（表598 `CRI` 宽度 `*` 未定）；实施 modules.yaml SMI_DS_T code_fields 前须 user 提供。
- **NEEDS-USER-FACT-033-INTLV**：interleave（intlv_en/grp/in/out）4 并发间插的精确数据编排（表598 描述 + `[seq1040]`），model-A 单指令不依赖，Stage-B 须 user 确认。
- **NEEDS-USER-FACT-033-DSTDONE**（hco B-BLOCK-2 升格）：SMI_DS_T `INST_DONE`/`PARAM_INST_NUM=4` outstanding 完成信号的回收路径 —— 不在 `[seq1003]` 的 5-SMI_IQ outstanding 列表内，文档未定义其连哪个 SMI-IS。这是不可让步的多并发完成同步契约，须 user RTL 裁定。

## Open questions
1. ~~SMI_DS_T 转置核是否进 SMT？~~ **【hco 已裁定，见 D3】** Stage-A 取 (b) 整链 model-A 原子段（零扰动现有 SMT + 显式
   忠实度 caveat）；(a) 进 SMT 依赖建模 = 忠实度里程碑，gated on NEEDS-USER-FACT-033-DSTDONE + VCS。
2. ~~SHIFTUP scatter 的 broadcast/smc_id_en 取值？~~ **【hco 已确认】** matmul 行对齐广播 = `broadcast==1` +
   `smc_id_en=N-1`（`[seq559/566]` 全 SMC 收同一行；与 N-1 缺省一致，无冲突，台账 D-04）。
3. 与 ADR-026 Stage-2b/2c（M>4 多行 / 大 K 多 tile）的次序：SMI_DS_T 先行还是并行？（纯工程排期，交 user/主 agent。）

## 实施进展（Stage-A）

### Increment 1 ✅ DONE（2026-06-05，commit `de0ace5`）
- `SmiDsTUnit`（`sim/smc_simulator/units/smi_ds_t_unit.py`）：SMI 中心跨-SMC 转置核，忠实表596-598。
  接口 `reset/gather(smc_id,row)/is_ready/transpose()/transpose_tile(纯)`；`transpose()` gather 未到齐抛错。
- 7 可证伪单测（`tests/unit/test_smi_ds_t.py`）全过：门 1（transpose_numeric + no-op 对偶）/ 门 4
  （param_smc_num_scalable）/ 门 5 unit 级（ordering）/ 门 2 unit 级（is_cross_smc）。全量 pytest 114 pass。

### Increment 2/3 设计（待实施，可冷启续上）
**缩放实例（最低 compute 风险，复用 matmul_q 已验通路）**：M=4、**K=4**、gather 跨 SMC_0..K-1（=4 个 SMC）、
广播到全 8 SMC。激活 X[4,4] **列对齐存**（非转置间插）：SMC_s（s=0..3）持列 s = `[X[0,s]..X[3,s]]`（其 UR 行）。
SMI_DS_T gather 4×4 → 转置 4×4 → 恢复行 X[m,:]（K=4 一词）→ SHIFTUP 广播 → 各 SMC `dot(X[m,:],W[:,s])`。
**compute 与 matmul_q 完全一致（K=4 单 addtree 词、无 cumulative）**，唯一新增 = 跨-SMC 转置前端。
（注：Stage-A gather 跨 K=4 个 SMC 而非全 8，是缩放选择；SmiDsTUnit 已对任意 smc_num 测过。更忠实的
K=SMC_NUM=8 实例需 cumulative addtree 2 词，留 Stage-A'。）

**运行时集成（cluster 真跨-SMC gather，非 inject-time 假转置）**：
1. `cluster._smi_ds_t_transpose(cfg)`：run() 起始一次性 model-A 原子段——从各 SMC UR bank 读列对齐激活
   （gather）→ `SmiDsTUnit.transpose` → 写行对齐激活回 SMC_0 激活 bank（供 ur-r0 广播读）。之后 sim 与 matmul_q 同。
2. **config 经 sim_config.json 串入**：emitter 写 `smi_ds_t` 块（src bank/addr、dst bank/addr、gather_smc_cnt、lanes）；
   `physical_runner._run_multi_smc` 读后在 `cluster.run` 前调 `_smi_ds_t_transpose`。需 BaseOperator → emitter 的
   sim_config-extra 通道（查 emitter `_emit_multi_smc` 是否已有算子自定义 sim_config hook，无则加最小 hook）。
3. `matmul_q_dst` 算子（`matmul_op.py`，subclass ManualMatmulQOperator）：`inject_data_per_smc` 列对齐存激活；
   提供 `smi_ds_t` config；复用 matmul_q 的 get_dfg/get_golden/compute。注册 `main_verify.py` OPERATOR_MAP。
4. `modules.yaml` 新 `SMI_DS_T` 条目（`simulation_only: true`，表598 字段 vld/intlv_*；CRI 宽度 NEEDS-USER-FACT-033-CRI）；
   **须加 `ModuleSpec.simulation_only: bool = False`（hw_spec.py schema 变更 → 重过 hco）**；
   加后必跑 matmul_q e2e + 全量 pytest 确认 emitter/solver 不把 SMI_DS_T 当可调度单元（死 module 验证）。

**剩余 D6 门**：门 3 `test_matmul_via_smi_ds_t_datapath`（matmul_q_dst e2e 8/8 bit-perfect）；
门 2/5 的 cluster 级（gather 真从多 SMC bank 读 + scatter 前 gather 全到齐）。

**触点文件**：`sim/smc_simulator/system/cluster.py` / `sim/smc_simulator/tools/physical_runner.py` /
`src/driver/emitter.py` / `verification_v2/ops/matmul_op.py` / `verification_v2/main_verify.py` / `hw_config/modules.yaml` / `src/api/hw_spec.py`。
**routing**：modules.yaml 新条目 + hw_spec.py schema → 重过 hco 后再 commit。

## HCO 复核（agentId `a54596b1c3aec48eb`）
**APPROVE-with-notes**。引擎判定（DS↔SMI_DS_T 两模块、matmul 用 SMI_DS_T）逐字钉死、supersede 范围（仅 DS/Stage-3，
compute 机制保留）划得精准、代码现状声明属实（无返工）、SMI_DS_SG/SMI_DS_RD（V1.0 不支持）正确未引、D2 接口/参数/
微指令忠实表596-598、D6 测试门充分。2 个进入实施前收口项已并入本 ADR：**B-BLOCK-1**（Open Q1 SMT 边界 → D3 裁定
Stage-A 原子段 + 里程碑 (a)）、**B-BLOCK-2**（SMI_DS_T 完成握手不在 5-outstanding 列表 → 升 NEEDS-USER-FACT-033-DSTDONE）；
论据订正（gather N 拍串行非一拍一 tile → D1 加注）；补测试（gather-before-scatter ordering → D6）。`still_holds: proposed`
维持，升 Accepted 唯一路径 = VCS。

## References
- [理解文档 docs/spec/aiacc-v0.4.19-understanding.md](../spec/aiacc-v0.4.19-understanding.md)（分歧台账 D-01，本 ADR 直接后继）
- v0.4.19 docx：表596-598（SMI_DS_T）/ 表585-588（DS，对比）/ `[seq1037-1051]`（SMI_DS_T 功能 + 五段 datapath）/
  `[seq1239-1242]`（matrix×matrix 激活排序转置）/ `[seq1197]`（vector×matrix 不需转置）/ `[seq990]`（COND 广播等待）
- [ADR-026](ADR-026-matmul-q-matrix-matrix-ds-transpose.md)（DS/Stage-3 transpose 部分 superseded；compute 机制保留）
- [ADR-025](ADR-025-matmul-parallel-n-broadcast-addtree.md)（广播左 + 每 SMC 列点积 + addtree，复用）
- [ADR-023](ADR-023-single-smi-emitter-sim-impl-model-a.md) / [ADR-022](ADR-022-single-smi-global-instruction-stream-model.md)（单 SMI 模型 + SHIFTUP/SHIFTDN model-A）
- 代码触点：`hw_config/modules.yaml`（新 `SMI_DS_T` module，simulation_only）/ `sim/smc_simulator/`（新中心转置单元 + 复用 shift_dn/shift_up_unit）/
  `sim/smc_simulator/system/cluster.py`（SMI_DS_T 转置接入，参照 `_broadcast_shiftup_fanout`）/ `verification_v2/ops/matmul_op.py`（matmul_q 改走 SMI_DS_T 链）
