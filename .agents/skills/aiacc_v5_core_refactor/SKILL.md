---
name: aiacc_v5_core_refactor
description: AIACC 编译器 V5.0 核心模块（SMT 合成 & HSTS 编排）的标准化开发流，包含 Rtovr 物理路由约束。
---

# AIACC V5.0 硬件更新对齐流程

当用户提供硬件信息时，Agent 必须按以下流程执行。
**核心原则：每个角色只改自己的文件，每个文件 < 100 行。**

---

## Step 1: 判定变更等级 (戴 HCO 帽子)

读取用户提供的硬件信息，判断属于哪一类：

| 等级 | 判断标准 | 示例 |
|:--- |:--- |:--- |
| **L0** | 数值变了，字段不变 | FPADD 延时 3→5 |
| **L1** | 字段变了，结构不变 | 新增一个 timing 条目 |
| **L2** | 需要新增算法逻辑 | 新增 Rtovr 公理 |
| **L3** | 需要重构模块接口 | Bank 8→16，地址位宽不够 |

然后在 `aiacc_v5_docs/hco/hw_changelog.md` 追加一行记录：
```
| 日期 | 等级 | 变更内容 | YAML 字段 | ADR |
```

---

## Step 2: 更新硬件数据源 (仍然 HCO)

修改 `smi_iq/aiacc_refactor/hw_config/hw_params.yaml`。
这个文件约 40 行，可以安全编辑。

- L0/L1：修改已有字段或新增字段。
- L2/L3：新增配置段（如 `rtovr:`）。

---

## Step 3: 根据等级分流

### L0/L1 路径（参数变更，简单流程）

直接跳到 Step 6（跑测试验证）。
不需要创建 ADR，不需要改架构文档。

### L2/L3 路径（架构变更，完整流程）

继续 Step 4 和 Step 5。

---

## Step 4: 创建 ADR (戴 ADM 帽子)

在 `aiacc_v5_docs/adm/adr/` 下创建新文件 `ADR-NNN_feature_name.md`。

ADR 固定 5 章，控制在 100 行以内：

```
# ADR-NNN: [特性名称]
## 1. 硬件变更描述
## 2. 对 hw_params.yaml 的影响
## 3. 对算法层的影响 (哪些公理/模块需要新增或修改)
## 4. 对 Roadmap 的影响 (哪些里程碑需要新增/修改)
## 5. 对 TDD 的影响 (新增哪些测试函数)
```

同时更新以下 ADM 文件：
- `aiacc_v5_docs/adm/roadmap.md` — 在对应 Phase 下追加新里程碑
- `aiacc_v5_docs/adm/baseline.md` — **不改内容**，只在底部追加 ADR 引用

---

## Step 5: 更新测试规格 (戴 TFD 帽子)

根据 ADR 第 5 章列出的测试函数，更新：
- `aiacc_v5_docs/tfd/tdd_spec.md` — 追加新的测试函数签名
- `aiacc_v5_docs/tfd/verification.md` — 如有新的验证支柱，追加描述

---

## Step 6: 验证影响 (戴 PVO 帽子)

列出受影响的已有测试，告知用户需要重跑哪些：

### L0/L1 影响范围速查表

| YAML 字段 | 受影响的测试 |
|:--- |:--- |
| `timing.*` | `test_raw_constraints`, `test_sin_synthesis` |
| `resource.ur_read_ports` | `test_bus_width_axiom`, `test_bus_overflow` |
| `rtovr.port_num` | `test_rtovr_connectivity`, `test_port_mutex` |
| `rtovr.mutex_groups` | `test_port_mutex`, `test_port_collision_shift` |
| `encoding.bits_dly` | `test_dly_within_bits`, `test_41bit_encode` |

### L2/L3 影响范围

由 ADR 第 5 章定义。所有新增的测试函数都需要先写红灯测试。

---

## Step 7: 更新门禁 (戴 IG 帽子)

检查 `aiacc_v5_docs/ig/gate_checklist.md`：
- 如果新增了里程碑，在对应 Phase 的门禁清单中追加检查项。
- 如果变更了已冻结的 Phase，标记为"解冻重验"。

---

## 完整执行示例

### 示例 A: FPADD 延时 3→5 (L0)

```
Step 1: 判定 L0（数值变了，字段不变）
Step 2: hw_params.yaml timing.FPADD: 5
Step 3: L0 路径 → 跳到 Step 6
Step 6: 需重跑 test_raw_constraints, test_sin_synthesis
Step 7: 无需更新门禁
```

共修改 **2 个文件**：hw_changelog.md + hw_params.yaml

### 示例 B: 新增 DMA 通道 (L2)

```
Step 1: 判定 L2（需要新增 DMA 调度公理）
Step 2: hw_params.yaml 新增 dma 段
Step 3: L2 路径 → 继续
Step 4: 创建 ADR-002_dma_channel.md
        更新 roadmap.md（M08 新增 DMA 公理函数）
Step 5: 更新 tdd_spec.md（新增 M08-F07 add_dma_axiom）
Step 6: 列出受影响测试 + 新增测试
Step 7: Phase 3 门禁新增 DMA 公理检查项
```

共修改 **5 个文件**，每个都 < 100 行。

---

## 文件路径速查

| 角色 | 文件 | 路径 |
|:--- |:--- |:--- |
| HCO | 变更日志 | `aiacc_v5_docs/hco/hw_changelog.md` |
| HCO | 硬件参数 | `smi_iq/aiacc_refactor/hw_config/hw_params.yaml` |
| ADM | 架构基线 | `aiacc_v5_docs/adm/baseline.md` |
| ADM | 路线图 | `aiacc_v5_docs/adm/roadmap.md` |
| ADM | 决策记录 | `aiacc_v5_docs/adm/adr/ADR-NNN.md` |
| TFD | TDD 规格 | `aiacc_v5_docs/tfd/tdd_spec.md` |
| TFD | 验证支柱 | `aiacc_v5_docs/tfd/verification.md` |
| PVO | Golden 基准 | `aiacc_v5_docs/pvo/golden_baseline.md` |
| IG | 门禁清单 | `aiacc_v5_docs/ig/gate_checklist.md` |
