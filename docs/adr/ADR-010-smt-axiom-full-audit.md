---
adr_id: ADR-010
title: SMT axiom 全量审计（22 def，7 active，15 dead 或 no-op）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-010 · SMT axiom 全量审计

## Context

ADR-009 实施 W↔R 跨类型 mutex 后，user 提出 "完整评估一下 SMT 算法公理"。本 ADR 把 `src/synthesis/axioms/` 下全部 22 个 `add_*_axiom` / `add_*_constraint` 函数定义全部摸一遍，给出：哪些真在跑、哪些是死代码、哪些 export 了但实现空、哪些注册了但调用注释掉、哪些两条调用路径（`backend.SMTSynthesizer` vs `ucode_solver.UcodeSolver`）行为不一致。

审计方法：
1. `grep "def add_.*axiom\|def add_.*constraint"` → 22 个 def
2. `grep "^[^#]*axioms\." src/synthesis/{ucode_solver,backend}.py` → 真实调用点
3. 逐函数读 body，判断 stub / 真实现
4. 对照 `axioms/__init__.py:__all__` 看 export 状态

## 22 axiom 分类

### A. ACTIVE（生产路径真生效，7 个）

| # | Axiom | 模块 | export | backend | ucode | 备注 |
|---|---|---|---|---|---|---|
| 1 | `add_latency_axiom` | dependency | yes | ❌ (用 inline RAW) | ✅ L198 | 含 RTOVR latch lifecycle 子公理（dependency_axioms.py:78-82），覆盖 e2e 主路径的 latch 上界 |
| 2 | `add_resource_constraint` | resource | yes | ✅ L207 | ✅ L215 | 入口聚合调用 #3/#4/#5 |
| 3 | `add_modulo_constraint` | resource | yes | indirect (经 #2) | indirect (经 #2) | u_vars 模槽互斥；II<=6 用枚举法，II>6 用商-余数辅助变量 |
| 4 | `add_bank_conflict_axiom` | resource | yes | indirect (经 #2) | indirect (经 #2) | ADR-008/009：W-W / R-R / W↔R 三段；R-R 同地址保留豁免 |
| 5 | `add_spill_reload_mutex_axiom` | resource | yes | indirect (经 #2) | indirect (经 #2) | spill_* 元数据驱动；纯算子路径无 spill 时无影响 |
| 6 | `add_iq_latch_protect_axiom` | physical | yes | ✅ L208 | ❌ | UR_READ 同 IQ 不重叠；只在 backend smt-unit 路径生效，ucode_solver 不调 |
| 7 | （非 axiom）ADR-006 inline O(E) 上界 | backend.py:198 | n/a | ✅ inline | ❌（靠 #1 子句覆盖） | 非函数化但生产实际工作 |

### B. ACTIVE 但模型异常（被调用但核心 `Implies` 约束注释掉，1 个）

| # | Axiom | 状态 |
|---|---|---|
| 8 | `add_side_effect_phase_axiom` | ucode_solver:210 调用。代码 line 67-71 标记 `[DEBUG EXPERIMENT] Disable phase_iso to test II=4` 把核心 `Implies(same_iq, phase_i == phase_j)` 注释掉。但 lines 39-45 仍然为每个 UR_READ/UR_WRITE 创建 `k_phase_*` 变量并 add 三条 bound 约束（`t >= k*II`, `t < (k+1)*II`, `k >= 0`）—— 等价于 quotient-remainder 分解，**有实际语义作用**（详见末尾 Errata）|

> **⚠ Errata 2026-05-15**（ADR-011 实施 A/B 测得）：本条早期标 "no-op，价值零" 是**错误**结论。lines 39-45 的 bound 约束等价 `t = k*II + r, 0 <= r < II, k >= 0`，与 `add_modulo_constraint` 的 `get_remainder_vars` 重复但**不冗余**——z3 必须协调两套 quotient 编码，搜索路径会改变。A/B 实测 log 算子：删 k_phase 后 II=7 UNSAT 时间 +137%（2s→4.9s）、II=8 SAT 时间 -85%（3.6s→0.5s），净总打平但**分布显著改变**。结论：**不是 no-op**，是"语义有效但 phase 隔离副作用未启用"。**不应在 ADR-013 类清理任务里删它**。

### C. DEAD：注册了但所有调用都被注释（8 个）

| # | Axiom | 状态 |
|---|---|---|
| 9 | `add_dependency_axiom` | wrapper for `add_latency_axiom`；无任何调用 |
| 10 | `add_anti_overwrite_axiom` | backend.py:211 "已废弃"；ucode_solver:209 注释 |
| 11 | `add_shadow_binding_axiom` | backend 改用 inline (L215-227)；ucode_solver:208 注释 |
| 12 | `add_physical_binding_axiom` | ucode_solver:220 注释；backend 无 |
| 13 | `add_rtovr_crossbar_axiom` | backend.py:212 "已废弃"；ucode_solver:207 注释 |
| 14 | `add_rtovr_alignment_axiom` | ucode_solver:211 注释；backend 无 |
| 15 | `add_rtovr_modulo_constraint` | ucode_solver:216 注释；backend 无 |
| 16 | `add_rtovr_liveness_axiom` | ucode_solver:217 注释；backend 无 |
| 17 | `add_alu_liveness_axiom` | 全文 grep 无调用 |
| 18 | `add_iq_alignment_axiom` | 全文 grep 无调用 |

注：#17 #18 都已 export 到 `__all__` 但确实零调用。

### D. DEAD：未 export + body 是空 `pass` 或被显式禁用（5 个）

| # | Axiom | export? | body |
|---|---|---|---|
| 19 | `add_symmetry_breaking_axiom` | ❌ | 有真实现，但 backend.py:230 注释 "证明与商余数降维模型深度冲突"；零调用 |
| 20 | `add_read_port_mutex_axiom` | ❌ | `pass`（注释：已被 `add_modulo_constraint` 通过 u_vars 吸收） |
| 21 | `add_write_port_mutex_axiom` | ❌ | `pass`（同上） |
| 22 | `add_cross_bank_copy_axiom` | ❌ | `pass`（注释：预留占位） |
| 23 | `add_long_latency_chain_axiom` | ❌ | `pass`（同上） |

注：HCO 文档中"未 export 即不生效"对 #20-#23 严格说不适用 —— 即使加进 `__all__` 也只是空跑。

### E. 额外死代码（1 个）

`add_alu_pipeline_axiom` 定义在 `axioms/__init__.py:105` 的 `SynthesisAxioms` 类内（非模块级函数），未在 `__all__`，零调用。

## 双路径不一致清单

| 维度 | backend.SMTSynthesizer（smt-unit + 历史路径） | ucode_solver.UcodeSolver（e2e 主路径） |
|---|---|---|
| latency lower bound | inline L188 `t >= t_dep + (delay-1) + transit` | `add_latency_axiom` |
| latency upper bound (ADR-006) | inline L198，覆盖**所有**非 is_invariant/UR_WRITE depends_on | 仅 RTOVR 路径（`add_latency_axiom` 内 rtovr_latch_lifecycle）；非 RTOVR 的 RAW 边无 |
| shadow binding | inline L215-227（FPMUL/FPADD/UR_WRITE 等 + RTOVR） | 注释掉，无 |
| iq latch protect | `add_iq_latch_protect_axiom` ✅ | 不调用 |
| side_effect_phase | 无 | 调了；核心 `Implies` 注释掉，但 bound 约束仍有效（≈ quotient-remainder，**非 no-op**，详见 §B Errata） |
| loop_carried_depends_on | inline L204 | inline L205（两边等价） |
| resource_constraint（modulo + bank + spill） | ✅ | ✅ |

**关键差异**：e2e 主路径的 latch upper bound 只覆盖 RTOVR 边，不覆盖 ALU 直传或 UR_READ→ALU 这种没有显式 RTOVR 中介的 RAW 边。在 AIACC 当前架构下"所有跨单元数据都经 RTOVR"是事实，所以**实践上等价**——但模型层是不同实现。

## Decision

**1. 本 ADR 只做诊断，不动代码** —— 任何 axiom 增删都触发 hco subagent 审查，需另起 ADR。

**2. CLAUDE.md Known unknowns 立刻更新**：
- 旧条目 "smt-unit 2 fail + 1 error" 标 **resolved**（ADR-006 + ADR-009 双修后 12 测试全 pass，2026-05-15 验证）
- 新条目 "**axiom 墓地**"：15/22 axiom 是 dead 或 no-op，HCO subagent 审查时如发现 diff 涉及 #9-#23 任一，**默认不要重新启用**——按 ADR-006 reject 段写过的"避免与商余数降维模型冲突"
- 新条目 "**`add_side_effect_phase_axiom` 模型异常**"：ucode_solver:210 调用，核心 `Implies` 注释但 bound 仍生效。**Errata 2026-05-15**：早期标 "no-op" 是错的，实测 A/B 影响 z3 搜索路径。**不应轻易删**。HCO 审查 phase 相关 yaml/spec 变更时仍需决断是否恢复 `Implies` 子句

**3. HCO sub-agent 知识库更新**：
- 领域常量段加 "axiom 真生效列表：#1-#7"（见本 ADR Table A）
- B 类（axiom 增删）清单加："新 axiom 必须在 `axioms/__init__.py:__all__` + `ucode_solver` 或 `backend.py` 真实调用点同步注册，否则就是死代码"
- 删除 HCO 文档里 "5 个未 export 即不生效" 的描述 —— 太粗，应改为指向本 ADR Table D（4/5 是 `pass` stub 即使 export 也不工作）

**4. 不要清理死代码**：
- 15 个死 axiom 不要立即删，理由：(a) 部分曾经服务过 V1/Gemini 时代的多套 backend（v5 模式等），可能含设计意图记录；(b) 删除是不可逆操作，目前没有性能瓶颈，留着没成本（除了 #8 `side_effect_phase_axiom` 的冗余变量开销，但量级很小）；(c) 若要清理，应单独跑 hco 审查 + 一次性删除 + ADR
- **TODO 候选**（不在本 ADR 决定）：择期删 #9-#23 的 14 个无效 def，集中清扫

## Rejected

- **立即清理 15 个死 axiom** ⚠
  - why：不可逆 + 无明显收益 + 可能误删 V1 设计意图 + 不在本次评估范围
- **修复 `add_side_effect_phase_axiom` no-op（启用核心约束）** ⚠
  - why：注释是 "[DEBUG EXPERIMENT] Disable phase_iso to test II=4" 留下的，启用必引发 II 抬升，需 HCO 审查 + e2e 回归矩阵 + 单独 ADR
- **统一 backend 和 ucode_solver 的 axiom 调用顺序与集合** ⚠
  - why：两条路径服务不同目标（smt-unit 反证 vs e2e 实际生产），强行对齐会引入回归。两个路径都已生产验证，差异是可接受的 product 现状
- **添加自动化 lint 检查 "axiom 未注册到 __all__ 报错"** ⚠
  - why：5 个未 export 中 4 个是 `pass` stub，强制 export 反而暴露空函数被外部调用的隐患；现状 import 失败提示是更好的"防呆"

## Consequences

- ✅ 22 个 axiom 现状从 "HCO 文档说 17 export / 5 未 export" 升级到"22 个 def → 7 真 active / 1 no-op / 14 dead"的精确账本
- ✅ smt-unit 12 测试全 pass（含本次新加 R5-R9）已是事实，CLAUDE.md Known unknowns 可清旧条目
- ✅ HCO subagent 审查 axiom 改动时有明确的"哪些是活的、不要碰已废弃的"清单
- ⚠ `add_side_effect_phase_axiom` no-op 是新发现的待定项，作为新 Known unknown 待 user/HCO 决断
- ⚠ backend / ucode_solver 双路径差异是 product 现状，本 ADR 不修；若未来某个新算子在 e2e 暴露 latch upper bound 漏洞，应触发 ADR-011 来在 ucode_solver 也补 inline 上界（非 RTOVR 路径）
- 🔒 **binding**：
  - 新 axiom 必须同时 (a) 注册 `__all__` (b) 真实调用 (c) 写 ADR；缺一就是新增死代码
  - 删除 axiom 必须先验证 backend / ucode_solver 两边均无活调用（grep 时区分 `^[^#]*axioms\.add_X` 与注释）
  - 任何 axiom 即使 export 也必须实现 body；`pass` stub 视为承诺未兑现，不应保留在 `__all__`
