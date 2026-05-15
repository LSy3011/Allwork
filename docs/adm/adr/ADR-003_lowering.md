---
adr_id: ADR-003 (legacy, docs/adm/adr/)
title: Lowering 流程（大模型辅助 + Bit-Perfect 冻结）
status: superseded
still_holds: no
last_verified: 2026-05-15
superseded_by: docs/adr/ADR-001-verification-v2-single-entry.md
audited_by: docs/adr/ADR-005-old-adr-audit.md
notes: |
  🪦 src/library/atomic/ 已被 Big Prune 删除（commit 988e521）
  🔄 算子流程改为 yaml-driven（verification_v2/ops/*.yaml + ConfigurableOperator）
     + 手写 DFG（继承 verification_v2/ops/base.py:BaseOperator）
  ✅ Bit-Perfect 验证思想仍在（main_verify.py --mode hw-parity），实现路径完全不同
---

# ADR-003: Lowering 流程 (大模型辅助 + Bit-Perfect 冻结)

## 1. 问题描述
70+ 算子需要展开为 MIR 脉冲序列（如 sin → 4 FPMUL + 2 FPADD）。
手写全部模板工作量巨大，但自动生成有精度风险。

## 2. 决策：大模型生成 → Bit-Perfect 验证 → 冻结为模板

```
算子数学定义 (如 sin Horner 展开)
    │
    ▼ [大模型生成]
候选 MIR 脉冲序列 (草稿)
    │
    ▼ [仿真器 Bit-Perfect 验证]
    │
    ├→ 通过 → 冻结为 library/atomic/sin.py (模板)
    └→ 失败 → 人工修正 → 重新验证
```

## 3. 核心原则
- **大模型是草稿生成器，不是真相源**
- 冻结后的模板文件才是真相
- 模板文件纳入 Git 版本管理
- 任何对模板的修改必须重新通过 Bit-Perfect 验证

## 4. 风险控制
- 浮点运算顺序敏感：`a*b + c*d ≠ c*d + a*b`
- 大模型可能改变运算顺序导致 bit 不一致
- 因此每个模板冻结前必须通过仿真器对比 PyTorch 参考值

## 5. 对现有设计的影响
- `se/source_structure.md`：`library/atomic/` 下每个算子一个 `.py` 文件
- `tfd/phase6_integration.md`：每个算子模板都有 Bit-Perfect 测试
- `ig/gate_checklist.md`：Phase 6 门禁新增"所有模板已通过 Bit-Perfect"
