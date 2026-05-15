---
adr_id: ADR-005
title: 旧 ADR-001~006 still_holds 审计
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-005 · 旧 ADR namespace 审计

## Context

`docs/adm/adr/` 下有 5 个 V1 / Gemini 时代 ADR（编号 001/002/003/005/006，**无 004**）：

| 文件 | 标题 |
|---|---|
| ADR-001_rtovr.md | Rtovr 物理路由特性 |
| ADR-002_bank_allocation.md | Bank 分配架构 (方案 B+: 约束分离) |
| ADR-003_lowering.md | Lowering 流程（大模型辅助 + Bit-Perfect 冻结） |
| ADR-005.md | 统一 41-bit 微码 ISA 与嵌套循环合成架构 |
| ADR-006_semantic_lowering.md | 物理语义下沉与规格驱动 (Semantic Lowering & Spec-Driven Driver) |

这些 ADR 都按 V1 风格写：**无 frontmatter / 无日期 / 无 still_holds 字段**。Phase 0 之后引入新 ADR namespace（`docs/adr/`），需要审计旧 ADR 哪些仍权威、哪些已被代码现状超越——否则未来 AI / 新读者打开旧 ADR 会被误导。

## Decision

**对每个旧 ADR 加 yaml frontmatter**（不改正文），字段含 status / still_holds / last_verified / superseded_by（如适用） / notes / audited_by。

依据 sop-template 的 ADR-013：frontmatter 是 metadata 非决策内容，不违反 ADR immutability 原则。

## 审计结论

| ADR | still_holds | 摘要 |
|---|:---:|---|
| ADR-001 Rtovr | 🟡 **partial** | RTOVR SMT 公理仍存在；HSTS / Stitcher 部分已废弃 |
| ADR-002 Bank Allocation B+ | 🔴 **no** | 三层切割模型彻底废弃；仅"Bank 互斥通过 SMT"精神保留 |
| ADR-003 Lowering | 🔴 **no** | library/atomic 已删；算子流程改为 yaml-driven，被新 ADR-001 取代 |
| ADR-005 41-bit ISA | 🟡 **partial** | 41-bit header 仍存在；位宽分配描述与代码不符；L1/L2/L3 命名不存在 |
| ADR-006 Semantic Lowering | 🟢 **yes** | field_mapper 无硬编码；spec-driven driver 原则完全成立 |

详细证据已记入各旧 ADR 的 frontmatter `notes:` 字段。

## Rejected

- **重写旧 ADR 正文以匹配代码** ⚠
  - why：违反 ADR immutability；丢失"决策当时的语境"这一最大价值
- **删除已 superseded 的旧 ADR** ⚠
  - why：删除即丢失历史，git history 可找但读 docs 看不到决策演化路径
- **不审计，只在 CLAUDE.md 加一句"旧 ADR 部分过期"** ⚠
  - why：粒度太粗，未来读者无法判断哪个具体 ADR 还能依赖

## Consequences

- ✅ 旧 ADR 仍可读，但状态显式标注，不再被误当作权威
- ✅ 新决策路径清晰：写代码前应先查 `docs/adr/` 新 namespace；引用旧 ADR 时必须看 frontmatter 状态
- ✅ ADR-013 immutability convention 在 AIACC 项目得到延续
- ⚠ 旧 ADR 中提到的术语（HSTS / FootprintMask / library/atomic 等）仍可能误导新读者翻阅旧 docs/{adm,tfd,...}/ 时——这是 Known unknowns 中的"docs 过期审计"任务，本 ADR 不处理
- 🔒 **binding**：
  - 引用旧 `docs/adm/adr/` 文件时，**必须先看其 frontmatter `still_holds` 字段**；partial / no / superseded 的内容不可作为决策依据
  - HCO / evaluator sub-agent 审查时若发现 PR 引用了 partial/no 的旧 ADR 作为依据，应标"阻断"
  - 后续若发现旧 ADR frontmatter 与代码现状再次偏离，更新 `last_verified` 字段，不重写决策内容
