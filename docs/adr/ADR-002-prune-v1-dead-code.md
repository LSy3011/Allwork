---
adr_id: ADR-002
title: V1 死代码大清理（Big Prune）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-002 · V1 死代码大清理

## Context

ADR-001 后，`verification_v2/main_verify.py` 成为唯一活路径。可达性分析显示：
- src/ 中约 17 个模块被触达；其余 ~85% 为 V1 残留
- tests/ 中 5 个测试文件仍活；其余 ~95% 为 V1 残留
- sim/ 中 `smc_simulator/` + `aiacc_hardware/` + `tests/helpers.py` 是活的；20+ 个顶层调试脚本死

合计：**283 文件 / 16079 行**自 V2 引入以来从未被触达。

## Decision

单 commit 删除全部死代码（`988e521`，2026-05-15），按 batch 删除并每批跑 `--op horner` + `--mode smt-unit` 验证不引入新失败：

| Batch | 内容 |
|---|---|
| 1 | src/library, orchestrator, tools, layout, synthesis/{engine,hw_adapter} + 2 个 yaml.disabled |
| 2 | src/{api/V1 入口, core/tiling, frontend/intrinsics 误删后回滚, ir/hir, synthesis/{encoder,regression_manager}} |
| 3 | tests/operators, regression, scratch + V1 unit/integration 测试 |
| 4 | sim/*.py 顶层调试脚本 + sim/tests/test_g*.py |

最终验证：`--op horner` ✅ · `--op activation` 13/13 ✅ · `--mode hw-parity` 3/3 ✅ · `--mode smt-unit` 失败数与基线一致（2 fail + 1 error，preexisting）。

## Rejected

- **多 PR 渐进删除** ⚠
  - why：每 PR 都要重跑完整验证，验证成本与一次删除一样高，但 review 负担反而更大
- **保留 V1 作为"历史参考"** ⚠
  - why：误导新读者 + 让 AI agent 把废弃模块当 source of truth；git history 已经保留完整记录
- **先文档化 V1 再删除** ⚠
  - why：V1 已无人维护，文档化无价值；docs/ 里的 V1 描述已经够多了

## Consequences

- ✅ 文档与代码对齐（部分 docs 仍过期，但范围明确）
- ✅ `bootstrap` skill 的 tech walkthrough 结果可信
- ✅ 减少 AI context 噪音
- ⚠ V1-only 知识需通过 `git show 988e521^:<path>` 找回
- 🔒 **binding**：V1 模块不可被新代码引用；如果发现遗漏的活路径依赖，立即从 `988e521^` checkout 回来 + 写 follow-up ADR
