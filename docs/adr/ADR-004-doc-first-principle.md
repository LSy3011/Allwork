---
adr_id: ADR-004
title: 文档先行原则（doc-first）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-004 · 文档先行原则

## Context

Phase 0 bootstrap 中，AI 把"代码先行于文档"错误地写进 `CLAUDE.md.local` 作为协作偏好。user 立即纠正：

> "代码先行不是原则，而是当前的现状，我希望的是文档先行。"

需要把"现状"和"目标"区分开，并在 SOP 体系里写明走向：

- **现状**：V1 / Gemini 时代留下大量过期 docs，代码已超前；为了避免被错误 docs 误导，临时**以代码为 source of truth**
- **目标**：未来开发流程是 **doc-first**——新功能 / 算子 / axiom 在写代码前必须先有 ADR 或 spec doc，让 docs 重新成为 source of truth

不做这条决策的后果：SOP 的 ADR、HCO sub-agent、bootstrap skill 都会逐渐被"代码先行"侵蚀，重新滑回 V1 时代 docs 失同步的状态。

## Decision

确立 **doc-first 为 SOP 核心原则**，在 mechanism 层面体现：

| 变更类型 | doc-first 是否适用 |
|---|---|
| 新功能 / 新算子 / 新 axiom / 流水线接口变更 | ✅ **必须**先写 ADR 或 spec doc，再写代码 |
| HW spec yaml 数值改动 | ✅ 必走 hco subagent + ADR |
| 架构 / 编译流水变更 | ✅ 必写 ADR |
| Bug fix / 重命名 / docstring / 单元测试新增 | ❌ 不适用（小改动不强求 spec） |
| 新算子 yaml 配置文件本身 | ❌ yaml 即是 spec，不需要额外 ADR |
| 临时实验性代码（不进 main 路径） | ❌ 不适用 |

**Mechanism**（落地方式）：

1. **CLAUDE.md Decision routing 总则**说明 doc-first 适用范围
2. **HCO sub-agent 输出格式**中"是否需要 ADR"已是必答字段
3. **CLAUDE.md.local 红线**：写代码前若发现无 ADR/spec 引用，应停下来问 user，而不是直接写代码
4. **未来 hook（待建）**：pre-commit 检测 commit message 是否引用了 ADR-NNN 或 docs 路径；若涉及 src/{synthesis,driver,api}/ 但无引用则警告

## Rejected

- **保持现状（代码先行作为常态）** ⚠
  - why：V1 时代教训——docs 失同步后无人维护，新人/AI 误读概率剧增；这就是 Phase 0 要做这么多刷新工作的根因
- **完全 spec-driven（所有改动包括 bug fix 都先写 spec）** ⚠
  - why：摩擦过大；bug fix / 重命名等小改动不需要 ADR；spec-driven 在小型项目（solo dev + AI）会变成形式主义
- **doc-first 但不写明边界** ⚠
  - why：边界不清会被自由心证；HCO sub-agent 和未来的 hook 都需要确定性规则

## Consequences

- ✅ ADR、spec doc 重新具备约束力，不再被代码追赶到失同步
- ✅ HCO / evaluator 等 sub-agent 有 spec 可对比，审查质量提升
- ✅ Decision routing 在 CLAUDE.md 中有"总则"锚点，未来 mechanism（hook）可挂载
- ⚠ 新功能初期开发摩擦增加（需先写 ADR），适合 solo dev + AI，不适合快速 prototype
- ⚠ 旧 V1 docs（`docs/{adm,se,tfd,pvo,ig}/`）批量过期问题不会因此自动解决——需要独立任务"docs 过期审计"
- 🔒 **binding**：
  - 涉及 src/{synthesis,driver,api,ir}/、`hw_config/**`、`src/synthesis/axioms/**` 的 PR 必须引用 ADR 或 spec doc
  - HCO / evaluator sub-agent 在审查时若发现无 ADR 引用，应标"待商榷"或"阻断"
  - 本 ADR 后续如有更精细的 mechanism（hook、checklist），独立 ADR 增量叠加
