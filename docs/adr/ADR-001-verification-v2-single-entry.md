---
adr_id: ADR-001
title: verification_v2/main_verify.py 作为唯一测试入口
decided: 2026-05-03
last_verified: 2026-05-15
still_holds: yes
---

# ADR-001 · verification_v2 单一测试入口

> 注：新 ADR 体系（docs/adr/）从 Phase 0 之后从 ADR-001 重新编号。旧 V1 时代 ADR 在 `docs/adm/adr/ADR-001~006`（保留作为历史归档）。

## Context

V1 测试体系散落在 `tests/operators/`（~139 文件）、`tests/unit/`（V1 风格 ~37 文件）、`sim/tests/`（5 个 g* 测试）、`sim/*.py`（21 个调试脚本）。没有统一入口，维护成本高，新算子接入路径不清晰。

## Decision

引入 `verification_v2/main_verify.py` 作为唯一测试入口（commit `113cefe`，2026-05-03）：

- **三种 mode**：`e2e`（端到端算子验证）、`smt-unit`（SMT 约束反证）、`hw-parity`（硬件物理 bit-perfect 比对）
- **算子注入两种**：YAML 驱动（`verification_v2/ops/*.yaml` + `ConfigurableOperator`）或手写 DFG（继承 `BaseOperator`）
- **算子分类**：`math` / `trig` / `activation` / `manual`（替代 V1 的 Atomic/Composite/Algebraic）

## Rejected

- **保留 V1 测试 + V2 并存** ⚠
  - why：双套体系维护成本叠加，AI 无法判断哪个是 source of truth
- **V1 → V2 渐进迁移**（边改边删）⚠
  - why：V1 架构无 ADPC 契约，与 V2 不兼容，迁移即重写

## Consequences

- ✅ 单命令（`--mode all`）覆盖完整验证
- ✅ 新算子接入 = 加一个 yaml + 注册到 OPERATOR_MAP
- ⚠ V1 ~283 个测试/源文件变成 dead weight，直到 2026-05-15 才清理（见 ADR-002）
- 🔒 **binding**：任何新算子必须通过 OPERATOR_MAP 或 YAML 加载器；不能新建独立的测试入口
