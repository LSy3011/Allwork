---
name: aiacc_v5_core_refactor
description: AIACC 编译器 V5.0 核心模块（SMT 合成 & HSTS 编排）的标准化开发流，包含 Rtovr 物理路由约束。
---

# AIACC V5.0 硬件更新对齐流程 (7 角色版)

当用户提供硬件信息时，Agent 必须按以下流程执行。

---

## Step 1: 判定变更等级 (HCO)

| 等级 | 判断标准 | 示例 |
|:--- |:--- |:--- |
| **L0** | 数值变了，字段不变 | FPADD 延时 3→5 |
| **L1** | 字段变了，结构不变 | 新增 timing 条目 |
| **L2** | 需要新增算法逻辑 | 新增 Rtovr 公理 |
| **L3** | 需要重构模块接口 | Bank 8→16 |

在 `docs/hco/hw_changelog.md` 追加记录。

## Step 2: 更新硬件数据源 (HCO)

修改 `hw_config/hw_params.yaml`（约 40 行）。

## Step 3: 根据等级分流

- **L0/L1**：跳到 Step 7
- **L2/L3**：继续 Step 4

## Step 4: 创建 ADR (ADM)

在 `docs/adm/adr/ADR-NNN.md` 创建决策文档（5 章，< 100 行）。
同时更新 `docs/adm/roadmap.md`。

## Step 5: 更新源码结构 (SE) [NEW]

根据 ADR 第 3 章（算法影响），评估对代码结构的影响：

### 5a. 更新 `docs/se/source_structure.md`
- 是否需要新增源码文件？（如 `synthesis/rtovr_axioms.py`）
- 是否需要新增目录？

### 5b. 更新 `docs/se/module_contracts.md`
- 现有接口的输入/输出是否需要扩展？
- 是否需要新增接口？

### SE 影响判定矩阵
| ADR 算法影响 | SE 需要做什么 |
|:--- |:--- |
| 新增 SMT 公理 | `axioms.py` 新增函数签名 |
| 扩展足迹维度 | `FootprintMask` 接口扩展 |
| 新增代码模块 | `source_structure.md` 新增文件 |
| 修改模块接口 | `module_contracts.md` 更新契约 |
| 无代码影响 | 无需更新 SE 文件 |

## Step 6: 更新测试规格 (TFD)

根据 ADR 第 5 章 + SE 的接口定义：
- 更新 `docs/tfd/tdd_spec.md`
- 更新对应 `docs/tfd/phase*.md`

## Step 7: 列出影响范围 (PVO)

### L0/L1 影响速查
| YAML 字段 | 受影响测试 |
|:--- |:--- |
| `timing.*` | `test_raw_constraints` |
| `resource.ur_read_ports` | `test_bus_width_axiom` |
| `rtovr.port_num` | `test_rtovr_connectivity` |
| `rtovr.mutex_groups` | `test_port_mutex` |
| `encoding.bits_dly` | `test_dly_within_bits` |

### L2/L3 影响
由 ADR 第 5 章定义。

## Step 8: 更新门禁 (IG)

- 如果新增了里程碑 → 更新 `docs/ig/gate_checklist.md`
- 如果变更了已冻结 Phase → 标记"解冻重验"

---

## 完整路径对比

### L0 路径 (改数字): 3 步
```
HCO → hw_params.yaml → PVO 列出测试 → 完成
改 2 个文件
```

### L2 路径 (加功能): 8 步
```
HCO → ADM → SE → TFD → IE → PVO → IG → 完成
改 7+ 个文件，每个 < 100 行
```

---

## 文件路径速查

| 角色 | 文件 | 路径 |
|:--- |:--- |:--- |
| HCO | 变更日志 | `docs/hco/hw_changelog.md` |
| HCO | 硬件参数 | `hw_config/hw_params.yaml` |
| ADM | 架构基线 | `docs/adm/baseline.md` |
| ADM | 路线图 | `docs/adm/roadmap.md` |
| ADM | 决策记录 | `docs/adm/adr/ADR-NNN.md` |
| **SE** | **源码结构** | **`docs/se/source_structure.md`** |
| **SE** | **模块接口** | **`docs/se/module_contracts.md`** |
| TFD | TDD 规格 | `docs/tfd/tdd_spec.md` |
| TFD | 验证支柱 | `docs/tfd/verification.md` |
| PVO | Golden 基准 | `docs/pvo/golden_baseline.md` |
| IG | 门禁清单 | `docs/ig/gate_checklist.md` |
