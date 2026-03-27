# AIACC Compiler V5.0

## 快速上下文恢复

新对话窗口打开后，按以下顺序读取文件恢复上下文：

### 第 1 步：读项目索引（理解结构）
```
docs/00_index.md
```

### 第 2 步：读 Skill（理解流程）
```
.agents/skills/aiacc_v5_core_refactor/SKILL.md
```

### 第 3 步：按任务类型读对应文件

| 任务 | 读哪些文件 |
|:--- |:--- |
| 硬件参数变更 | `hw_config/hw_params.yaml` + Skill |
| 新增硬件特性 | Skill + `docs/adm/adr/` 下最新 ADR |
| 新增算子 | `.agents/workflows/add-operator.md` |
| 理解架构 | `docs/adm/baseline.md` + `docs/adm/adr/ADR-002*.md` |
| 写代码 | `docs/se/source_structure.md` + `docs/se/module_contracts.md` |
| 写测试 | `docs/tfd/phase{N}*.md` |
| 验证结果 | `docs/tfd/verification.md` |

### 为什么这能保证一致性

1. **Skill 是行为约束**：大模型读了 Skill 就知道该按什么流程操作
2. **ADR 是决策记录**：每个架构决策有编号，不会被遗忘
3. **SE 文件是代码蓝图**：接口契约和文件树是硬约束
4. **Git 是终极保障**：任何文件被改乱了，`git diff` 能看到

## 项目状态

- Git 仓库: `/home/zhangjun/2_code/NPU/AIACC/aiacc_compiler/`
- 当前架构决策: ADR-001(Rtovr) + ADR-002(Bank分配B+) + ADR-003(Lowering)
- 7 个角色: HCO → ADM → SE → TFD → IE → PVO → IG
- 2 个工作流: `/refactor-aiacc`(硬件变更) + `/add-operator`(新增算子)

## 常用命令

```bash
# 生成设计报告
python3 build_report.py

# 查看 Git 历史
git log --oneline

# 查看当前文件结构
find docs/ -name "*.md" | sort
```
