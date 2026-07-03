---
adr_id: ADR-051
title: Assembler compute-loop 计数与调度流水深度解耦（解 M-unroll per-IQ loop 计数崩）
decided: 2026-07-03
last_verified: 2026-07-03
still_holds: proposed
implements: ADR-026
builds_on: ADR-023
---

# ADR-051 · Assembler compute-loop 计数与调度软件流水深度解耦

> **状态：Proposed（doc-first，代码未改）** —— 修 `src/driver/assembler.py` 的 per-IQ `loops_val` 推导，使**多-recurrence / 空间展开**（如 ADR-026 `matmul_q_full` 的 M 行展开）的模调度能正确 emit。属 `src/driver/` 流水线接口变更 → **必过 hco + 写 ADR**（CLAUDE.md routing）。
>
> **一句话**：当前 `loops_val` 从**每-IQ 的 global_t t-span** 推（含"软件流水深度"项 `max_t//iq_II`），只在**单-recurrence 紧凑调度**下正确；M 行空间展开产生多条独立子链，SMT 把它们放到**不同流水深度** → 各 IQ loop 计数不一致 → cumulative 迭代错乱 / 数据错位。需把**逻辑循环次数（=已知 num_iterations）**与**调度的软件流水深度**解耦。

## Context

### 触发：ADR-026 Stage-2c 合体（matmul_q_full）撞墙
`ManualMatmulQFullOperator`（M>1 × 4 列 × 多-tile K）取 **unroll-M + loop-T** 结构（K tile 时间循环、M 行空间展开成 M 个独立子块，每行一个自己的 cumulative addtree 累加器——绕开单 addtree 跨行 reset 污染，是忠实 + 性能最优的结构，见 [ADR-026](ADR-026-matmul-q-matrix-matrix-ds-transpose.md) + memory `m-unroll-vs-per-iq-loopcount`）。

- **SMT 秒解**（II=11 SAT verified），但 **e2e 8/8 全错**。
- 备选 full-unroll（num_iter=1、spill 2-phase）已**证伪双重堵死**（2026-07-03 实测）：COLS=4 → 16 FPMUL → z3 II 16-22 全 UNKNOWN/UNSAT（per-II 150s+60s 仍解不出）；COLS=2 smoke → II 8-27 **几乎全 proven UNSAT**（结构性不可行，非 SMT 难度）。→ full-unroll 不是忠实可扩路径。**唯一忠实 + 可扩（K 循环、真实规模）结构 = unroll-M + loop-T，卡点在本 ADR 要修的 assembler。**

### 根因：per-IQ loops_val 从 t-span 推，含软件流水深度项
`src/driver/assembler.py:238-256`（每 IQ 一算）：
```python
max_t_global = max(op.global_t for op in ops)      # 该 IQ 的 op 最大 global_t
t_min_iq     = min(op.global_t for op in ops)       # 该 IQ 的 op 最小 global_t
skip_iterations = t_min_iq // iq_II
eff_num_iter    = num_iterations                     # (SMI_DS_T dp 特例=2)
total_iterations_needed = (max_t_global // iq_II) + eff_num_iter
loops_val = total_iterations_needed - skip_iterations - 1
          = (max_t_global//iq_II - t_min_iq//iq_II) + num_iterations - 1
```
即 **`loops_val = D_iq + num_iterations - 1`**，其中 `D_iq = (max_t//iq_II - t_min//iq_II)` = 该 IQ 的 op 跨的 **II-窗口数** = 该 IQ 在软件流水里的深度。

- **单-recurrence 紧凑调度**（如 `matmul_q_multitile` M=1，VCS/e2e 已通）：各 IQ 的 op 落在**同一 II 窗** → `D_iq=0` → `loops_val = num_iter-1`（**所有 IQ 一致、正确**）。
- **M 行空间展开**：M 条独立子链，SMT satisfiability（**无 makespan 最小化**，`ucode_solver.py:424` 纯 `solver.check()`）把 row1 整条链自由放到 global_t≈110（row0 在 ~0-20）→ 某些 IQ（如 ur-r0 同时挂 row0+row1 的 a/temp 读、单 addtree IQ 同时挂 y_r0/y_r1）的 op **跨多个 II 窗** → `D_iq` 各不相同（实测 `hdr_ur-r0 LOOP:2` vs `hdr_rtovr-3 LOOP:1`）→ **各 IQ 循环次数不一致** → cumulative addtree 多迭代 over-accumulate（row0 sim=208 vs golden=66）+ 调度铺开致 sim 超时截断（row1 ITER1 missing）。

### 配套地址补偿也依赖同一深度假设
`assembler.py:277-278` 的 `relative_skip = op.global_t//iq_II - iq_start//iq_II; addr = logical_addr - stride*relative_skip` —— 把深度-`k` 的 op 地址预减 `k*stride`，假设 loop 会多跑 `k` 次把它推到位。M-unroll 的**虚假深度**使补偿地址一起错位。故 loop 计数与地址补偿是**同一软件流水模型的两面**，须一并考量。

## Decision（提 hco 裁定；候选二选一或组合）

**目标**：compute IQ 的**逻辑循环次数**由**已知 `num_iterations`** 决定，不被调度的（可能虚假的）软件流水深度污染；且地址/时序仍正确。

### 候选 P-i：solver 加「每-IQ flatness / makespan」约束（保 emit 数学不变，低 VCS 风险）
- **改 `ucode_solver.py`**：加约束使**每个 IQ 的 op 落在一个 II 窗内**（`max_t - min_t < iq_II`，或等价 per-IQ 深度=0），即逼 SMT 产出 emitter 已 VCS-证明能正确处理的"紧凑/flat"调度。
- **优点**：`assembler.py` 的 `loops_val` / `relative_skip` 数学**一字不改**（multitile/fma/所有现算子的 VCS-proven emit 路径零风险）；只是缩小 SMT 解空间到"flat"子集。
- **风险 / 开放点**：
  - 单-recurrence 深链（a_read→FPMUL→...→out ~9 深 + 各 latency）**本就跨 >II 拍** → "每 IQ 一个 II 窗"可能**过紧 → UNSAT**。实测 2026-07-03 全局 makespan cap=25 仍产生 `D_iq` 不一致（1 vs 2）→ **简单全局 cap 不够**，需**per-IQ span 约束**。
  - 正确的约束粒度 = ？（per-IQ `max_t-min_t < C·iq_II`？C=1 还是 2？会不会挤掉合法深流水？）——**需 hco 判**。

### 候选 P-ii：assembler 直接用 `loops_val = eff_num_iter - 1`（去 `max_t//iq_II` 深度项）
- **改 `assembler.py:255-256`**：`loops_val = eff_num_iter - 1`（compute IQ；memory-IQ/SMI_DS_T/stream 特例另议）。
- **前提假设（须 hco 对 RTL embed-loop 语义确认）**：模调度的 op 投影到 kernel slot（`slot = global_t % iq_II`）+ `relative_skip` 地址补偿后，跑 `num_iterations` 次即产正确数据流——**即 emitter 本就该只跑逻辑次数、深度由 slot 投影+地址补偿吸收**。
- **风险 / 开放点**：
  - 若 RTL 的 level-1 embed-loop **确实需要**额外 `D_iq` 次 replay 来 drain 深流水 op（prologue/epilogue 语义），则去掉该项会**少跑 → 深 op 取不到数**。当前 `max_t//iq_II` 项**为何存在**（哪个历史算子/VCS case 依赖它 >0）是关键——**须 hco/git 考古确认**。若从来没算子真正依赖 `D_iq>0`（单-recurrence 恒 flat→D=0），则该项是"从未被正例检验的死项"，P-ii 安全。
  - `relative_skip` 是否要同步改（若深度不再由 loop 吸收）。

### 倾向 & 组合
- **倾向 P-i**（保 VCS-proven emit 数学不变、风险局部在 solver），但需 hco 定"per-IQ flatness 约束粒度"且验不过紧致 UNSAT。
- **P-ii 更小改动**但依赖"深度项本是死项"的假设，**须 hco + git 考古**证 `D_iq>0` 从未被正例依赖。
- 二者非互斥：可 P-ii（loops=num_iter-1）+ 轻量 flatness（保证 `relative_skip` 地址对）。

### 非目标（本 ADR 不做）
- **2-level 嵌套循环**（M 外 / T 内）：`sim/smc_simulator/control/iq_unit.py` 的 `loop_states[8]` 已支持 8-level 嵌套，但 assembler 只 emit level=1。这是更大改动（且 cumulative addtree 跨外循环 reset 仍需配 spill）——留作独立 ADR，本 ADR 聚焦"单层循环 + M 空间展开"的 loop 计数正确性。
- 不动 memory-IQ（UR/LDB level-1 embed 地址步进，ADR-050 已 VCS）、SMI_DS_T dp（`eff_num_iter=2` 固定，ADR-043）、stream IQ（N 条显式，ADR-041）三条特例路径——本改**只针对 compute IQ 的 `eff_num_iter=num_iterations` 分支**。

## Consequences

### 正面
- 解锁 ADR-026 Stage-2c 合体（M>1 × 多-tile）的忠实 + 性能最优结构（unroll-M + loop-T），且**通用**——任何"多独立 recurrence / 空间展开进单层循环"算子受益（非仅 matmul_q_full）。
- P-i 保 emit 数学不变 → 现有算子 VCS-proven 路径零回归。

### 风险 / 债务
- **DEBT-051-FLATNESS-GRANULARITY**（P-i）：per-IQ flatness 约束粒度若过紧 → 合法深流水算子 UNSAT；须回归全算子集（pytest + 关键 e2e：fma/matmul/multitile/horner/fp16_matmul_k16 等）验不破。
- **DEBT-051-DEPTH-TERM-ARCHAEOLOGY**（P-ii）：`max_t//iq_II` 深度项的历史依据须 git/VCS 考古；若某算子真依赖 `D_iq>0`，P-ii 破坏之。
- 本改是 **model-A / sim + emit 层**；对 RTL 忠实度的最终背书仍需 VCS（升 Accepted 门）。

## Open questions（提 hco）
1. **RTL level-1 embed-loop 语义**：HDR `loops=L` → kernel（II slot）replay `L+1` 次。深度-`k` 的 op（`global_t` 在第 k 个 II 窗）+ `relative_skip=k` 地址补偿，是否**必须**多 `k` 次 replay 才 drain？还是 slot 投影已足、逻辑次数=num_iterations 即够？（决定 P-i vs P-ii）
2. **`max_t//iq_II` 深度项的历史正例**：有无算子/VCS case 真依赖 `D_iq>0`？（若无 → P-ii 安全）
3. **P-i flatness 粒度**：per-IQ `max_t-min_t` 该 bound 到几倍 iq_II 才既防虚假深度、又不挤掉合法深链？
4. 是否需同步动 `relative_skip` 地址补偿。

## References
- [ADR-026](ADR-026-matmul-q-matrix-matrix-ds-transpose.md)（matmul_q_full 触发本 ADR；Stage-2c 合体）
- [ADR-050](ADR-050-ldb-load-to-ur-weight-streaming.md)（memory-IQ level-1 embed 地址步进，本改不动此路径）/ [ADR-043](#)（SMI_DS_T eff_num_iter=2）/ [ADR-041](#)（stream IQ N 条显式）——三条特例路径边界
- [ADR-023](ADR-023-single-smi-emitter-sim-impl-model-a.md)（单 SMI emitter 模型）
- memory `m-unroll-vs-per-iq-loopcount`（根因 + 全景）
- 代码触点：`src/driver/assembler.py:238-278`（loops_val + relative_skip）/ `src/synthesis/ucode_solver.py:357-424`（调度约束 + 无 makespan 的 `solver.check()`）
- WIP checkpoint commit `ac063b1`（P2 结构落地 + BLOCKED 记录）
