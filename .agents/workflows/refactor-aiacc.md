---
description: AIACC 编译器重构自动化工作流 - 确保物理属性与逻辑代码始终对齐
---

# /refactor-aiacc 工作流 (7 角色版)

当硬件参数变更时，按以下步骤严格执行。

## Step 1: 更新唯一数据源 (HCO)
只修改 `hw_config/hw_params.yaml`（约 40 行）。

## Step 2: 查 Skill 传播矩阵 (HCO)
// turbo
读取 `.agents/skills/aiacc_v5_core_refactor/SKILL.md`，判定变更等级。

## Step 3: 创建 ADR (ADM, 仅 L2/L3)
在 `docs/adm/adr/ADR-NNN.md` 创建决策文档。
更新 `docs/adm/roadmap.md`。

## Step 4: 更新源码结构 (SE, 仅 L2/L3) [NEW]
检查 `docs/se/source_structure.md` 是否需要新增文件/目录。
检查 `docs/se/module_contracts.md` 是否需要扩展接口。

## Step 5: 更新测试规格 (TFD)
更新 `docs/tfd/tdd_spec.md` 和对应 `docs/tfd/phase*.md`。

## Step 6: 设计决策走 ADR
新决策写 `docs/adr/ADR-NNN-*.md`（doc-first，见 ADR-004）。
V1 七角色设计报告生成器 `build_report.py` 已废弃删除（被 ADR 体系取代）。

## Step 7: 更新门禁 (IG)
更新 `docs/ig/gate_checklist.md`。

## 注意事项
- 每个文件 < 100 行，安全可编辑
- 所有硬件数值的唯一真相源是 `hw_config/hw_params.yaml`
- 所有代码结构的唯一定义在 `docs/se/source_structure.md`
