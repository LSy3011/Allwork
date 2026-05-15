---
adr_id: ADR-003
title: 引入 SOP v7.0，7 角色精简为 HCO + SOP 通用 sub-agents
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-003 · 引入 SOP，7 角色精简为 1 个

## Context

Gemini 时代项目使用 7 角色 mental model（HCO / ADM / SE / TFD / IE / PVO / IG），每角色对应 `docs/<role>/` 一个目录。但这是 **docs 约定**——AI 不会强制遵守，纯靠 prompt 提醒，且：

- ADM / SE / TFD / IE 都是通用软件工程角色，无 AIACC 专属知识
- PVO / IG 是验收门禁，可用 sub-agent 机制实现
- 只有 **HCO**（硬件契约审查）是 AIACC 领域不可让步的

迁移到 Claude Code + SOP v7.0（`~/sop-template/`）后，可以用 sub-agent + ADR + hooks 这些 **mechanism** 替代纯文档约定。

## Decision

**精简到 1 个 AIACC 专属 sub-agent + 复用 SOP 通用 sub-agents**：

| 旧角色 | 处理 |
|---|---|
| **HCO**（Hardware Contract Officer） | ⭐ 升级为 `.claude/agents/hco.md` sub-agent |
| ADM / SE / TFD / IE | 用 SOP 的 ADR + AI 自带能力替代，不再设角色 |
| PVO（物理验证官） | 用 SOP `evaluator` sub-agent |
| IG（集成守护者） | 按需用 SOP `ops` sub-agent（AIACC 无 prod release，多数场景不需要） |

决策路由写入项目级 `CLAUDE.md`。旧 `docs/{adm,se,tfd,pvo,ig}/` 保留作为历史知识库，不删但不再扩。

## Rejected

- **保留 7 角色 + 加 SOP sub-agents 并存** ⚠
  - why：冗余；稀释 HCO 这个唯一领域专属价值；增加 AI 决策成本（"该用 7 角色哪一个还是 SOP 哪一个"）
- **完全废弃 7 角色，全用 SOP 通用 sub-agents** ⚠
  - why：硬件契约（axioms 物理语义、Bank 冲突规则、RtoVR 选通拓扑）是 AIACC 不可替代的领域知识；SOP 通用 sub-agents 无 AIACC 上下文，无法识别硬件违约
- **HCO 仍作为 docs 约定，不升级为 sub-agent** ⚠
  - why：docs 约定无强制力，已经证明在 Gemini 时代被绕过；mechanism > prose

## Consequences

- ✅ 心智模型从 7 → 1：只需记住"硬件变更 → hco"
- ✅ 决策路由进 CLAUDE.md，可执行
- ✅ 与 SOP 体系兼容，可享 SOP 后续 mechanism 升级（hooks、`bootstrap` refresh）
- ⚠ 旧 `docs/{adm,se,tfd,pvo,ig}/` 内容会逐步过期，需要明确"reference only"标记
- ⚠ `.claude/agents/hco.md` 本身需要充分覆盖硬件契约场景（hw_config 变更、axiom 增删、ALU 延迟调整、Bank 冲突规则等），否则 sub-agent 会漏审
- 🔒 **binding**：HW spec / axiom 变更必须调用 hco subagent；物理验收变更必须调用 evaluator
