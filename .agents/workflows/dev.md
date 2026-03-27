---
description: 开发入口 - 每次开发前必须先执行此工作流选择任务
---

# /dev 工作流 (开发入口)

每次开始开发时，执行此工作流。**禁止跳过直接写代码。**

## Step 1: 读取当前状态
// turbo
读取 `docs/adm/sprint_plan.md` 和最近的 git log。

## Step 2: 展示可选任务菜单

根据当前 Sprint 进度，向用户展示以下格式的菜单：

```
══════════════════════════════════════════════
       AIACC V5.0 开发任务面板
══════════════════════════════════════════════
当前 Sprint: S0X - [名称]
Git Tag:     v5.0-S0(X-1) 已完成

📋 可选任务:
  [1] feat(xxx): 实现 xxx 函数
  [2] feat(xxx): 实现 yyy 函数
  [3] test(xxx): 补充 zzz 测试
  [4] fix(xxx):  修复 zzz 问题

⚙️ 流程操作:
  [H] 硬件参数变更 → 执行 /refactor-aiacc
  [A] 新增算子     → 执行 /add-operator
  [R] 重构模块     → 执行 ADR 流程
  [C] 检查遗漏     → 查看 assumptions.md

🔒 你不能做:
  - 跳过当前 Sprint 的未完成任务
  - 不写测试直接实现
  - 不走 ADR 直接改架构

请选择 [1-4/H/A/R/C]:
══════════════════════════════════════════════
```

## Step 3: 用户选择后执行

### 选了数字 [1-4] → 按 TDD 流程
1. 读取对应的 `docs/tfd/phase{N}.md` 中的函数签名
2. 读取 `docs/se/module_contracts.md` 中的接口定义
3. 读取 `docs/hco/assumptions.md` 检查是否有 ❓ 需要先确认
4. 提出 Sprint 提问清单 (机制 1)
5. 用户回答后开始实现
6. 实现后立即运行测试

### 选了 [H] → 硬件变更
执行 `.agents/workflows/refactor-aiacc.md`

### 选了 [A] → 新增算子
执行 `.agents/workflows/add-operator.md`

### 选了 [R] → 重构模块
1. 先运行 `check_deps.py` 评估影响面
2. 创建 ADR 文档
3. 更新 `se/source_structure.md` 和 `se/module_contracts.md`
4. 确保新模块仍实现原有接口

### 选了 [C] → 检查遗漏
读取 `docs/hco/assumptions.md`，展示所有 ❓ 项

## Step 4: 完成后更新状态
- 在 Sprint 任务列表中标记完成
- 运行 `pytest` 确认绿灯
- `git commit` 提交
