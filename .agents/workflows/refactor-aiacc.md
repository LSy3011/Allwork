---
description: AIACC 编译器重构自动化工作流 - 确保物理属性与逻辑代码始终对齐
---

# /refactor-aiacc 工作流

当硬件参数变更时，按以下步骤严格执行。**禁止直接编辑 800 行的设计文档。**

## Step 1: 更新唯一数据源
只修改 `hw_config/hw_params.yaml`（约 40 行的小文件）。
用户提供什么硬件信息，就更新什么字段，其他字段不动。

## Step 2: 查 Skill 传播矩阵
// turbo
读取 `.agents/skills/aiacc_v5_core_refactor/SKILL.md`，
根据变更类型（A 延时 / B 资源 / C 路由 / D 编码）确定影响范围。

## Step 3: 同步 TDD 文件
只更新 `docs/v5_function_level_tdd.md` 中受影响的测试函数签名。
该文件约 45 行，可以安全编辑。

## Step 4: 同步验证文件
只更新 `docs/v5_smt_hsts_verification.md` 中受影响的支柱描述。
该文件约 340 行，可以安全编辑。

## Step 5: 同步路线图
只更新 `docs/v5_dev_roadmap.md` 中受影响的里程碑描述。
该文件约 360 行，可以安全编辑。

## Step 6: 设计文档不改内容，只追加 Changelog
对 `docs/aiacc_compiler_v5_complete_detailed_design.md` **不做内容修改**。
只在文件末尾追加一行 Changelog 记录：
```
## Changelog
- [日期] 硬件变更：XXX → 详见 hw_config/hw_params.yaml
```

## 注意事项
- **绝对不要**在 800 行的设计文档中间插入或替换内容
- 所有硬件数值的唯一真相源是 `hw_config/hw_params.yaml`
- 设计文档中的数值是"快照"，真实值以 YAML 为准
