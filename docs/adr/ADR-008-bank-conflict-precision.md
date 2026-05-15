---
adr_id: ADR-008
title: Bank 冲突规则精确定义（R-R / R-W / W-W 同 Bank 同 cycle 全禁止）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-008 · Bank 冲突精确语义

## Context

Bank 冲突规则是 AIACC 硬件不可让步的物理基线之一，但在文档中有两套相互矛盾的描述：

- **V1 旧描述**（`docs/hco/detail_hw_spec.md` + `docs/adm/baseline.md`）："R-R 非法、W-W 非法、**R+W 合法**"
- **user 口径**（2026-05-15 在 Phase 0 Known unknowns 解决阶段口述）："同 Bank 同 cycle，不同 UR 端口对该 Bank 的**读读、读写、写写指令都是禁止的**"——即 R+W 也是非法

`docs/adm/collaboration_guide.md` 明确写过 "R-R 冲突的精确定义只有你知道"——印证了旧描述并非权威。

### 代码现状（与 user 口径对照）

| 代码位置 | 行为 | 与 user 口径一致？ |
|---|---|---|
| `src/synthesis/backend.py:304-308`（TimingVerifier post-check） | 任何两 op 同 Bank 同 slot 报 Conflict（不区分 R/W） | ✅ 一致 |
| `src/synthesis/axioms/resource_axioms.py:add_bank_conflict_axiom` | 只禁同类型（R-R 或 W-W），R-R 还有"相同地址豁免（广播读取）" | ❌ 太松（R+W 在 SMT 层未禁） |
| `src/synthesis/ucode_solver.py` (e2e 主路径) | **未调用 `add_bank_conflict_axiom`**，仅 latency / side-effect-phase / resource 三个 axiom | ❌ 完全无 Bank axiom 在 SMT 层 |

生产算子（horner / activation / matmul）能跑通，是因为 yaml 算子 layout（`manual_bank` metadata）显式把读写分散到不同 Bank，自然避开冲突；以及 TimingVerifier post-check 在物理验证阶段拦下。但**SMT 层与硬件契约存在不一致**——与 ADR-006（latch lifetime）完全相同的 pattern：axiom 漏 + Verifier 兜底。

## Decision

**1. 确立 Bank 冲突精确语义**（user 口径为权威）：

> 同一 Bank，同一 cycle，**任意两个 UR 端口（4R + 4W = 8 个独立端口中任意两个）**对该 Bank 的访问指令——无论是 R-R、R-W、还是 W-W——**全部禁止**。

物理原因：硬件 RTL 层面，Bank 是单端口存储宏（SRAM）；多个 UR 端口同 cycle 访问会产生总线竞争 / 未定义行为。

**2. 文档落点**：

- `docs/glossary.md` 硬件段："R-R / W-W 同拍冲突非法，R+W 合法" → 改为"R-R / R-W / W-W 任意组合 同 Bank 同 cycle 全部禁止"
- `.claude/agents/hco.md` 领域常量段 + D 类审查清单：同步更新
- 旧 `docs/hco/detail_hw_spec.md` 不动（V1 历史归档，按 ADR-005 audit 已 partial），但 glossary / hco.md 是新读者第一入口，足够覆盖

**3. 代码层不一致作为独立 follow-up**：

SMT 层 axiom 漏（backend.py 太松 + ucode_solver 完全无）已是 known issue，但**本 ADR 不处理**——理由：与 ADR-006 模式一致需要独立 hco 审查 + 完整 axiom 设计 + 影响半径分析（ucode_solver 主路径补 axiom 是否会拒掉现有 yaml 算子）。作为新 Known unknown 加进 CLAUDE.md 待后续单独走。

## Rejected

- **接受 V1 旧描述（R+W 合法）作为权威** ⚠
  - why：与 user 口述硬件契约矛盾，且 TimingVerifier 代码已按 user 口径严格执行——若以 V1 为权威，Verifier 是 bug
- **现在就修 axiom + ucode_solver** ⚠
  - why：影响半径未评估，需 hco subagent 复核（ADR-006 修 latch lifetime 也是先 ADR-006 后修代码）；本 ADR 聚焦"精确语义"这一 user-only knowledge
- **保留两套描述，让代码读者自己判断** ⚠
  - why：违反 doc-first，ambiguity 在硬件契约层是致命的

## Consequences

- ✅ Bank 冲突的精确语义不再是 user-only knowledge，从 Known unknowns 转出
- ✅ hco subagent 在审查 Bank 相关变更时有明确权威可引（不必每次主动询问 user）
- ✅ glossary / hco.md 与 TimingVerifier 行为一致；与 ucode_solver 缺失一致性变成显式 follow-up
- ⚠ V1 旧 docs 仍写 "R+W 合法"，未刷新——按 ADR-005 audit 旧 docs 是历史归档，需要查阅时**必看 frontmatter still_holds**；不强制刷新
- 🔒 **binding**：
  - 任何涉及 Bank 冲突约束的 axiom / Verifier 变更必须以 user 口径（R-R/R-W/W-W 全禁）为准
  - 若发现新算子物理仿真踩到 R+W 同 Bank 同 cycle 错误，**不应**简单加 metadata 绕开，而应触发本 ADR 的 binding：先调 hco subagent，可能进 ADR-009（补 axiom）
