---
name: aiacc_operator_factory
description: AIACC 算子重构工厂 (V4.0 ADPC) - 确保 70+ 算子从 DFG 构造到比特级仿真验证的一致性。
---

# AIACC 算子重构工厂 (V4.0 ADPC)

本 Skill 定义了 AIACC 算子的 **工业级开发与验证标准**，所有新增算子必须强制对齐 `Add/Sin/ReLU` 的 V4.0 规范。

---

## 1. DFG 构造规范 (Logic-Physical Contract)

### 1.1 语义定位标签 (The Anchors)
不再依赖 `trace_id` 的字符串匹配，必须使用元数据标签：
- **输入角色**: `UR_READ` 必须通过 `role="operand_a"` (或 `operand_b`, `initial_guess`) 进行标识。
- **常数固化**: 所有算法常数必须通过 `is_const=True` 和 `value=3.14` 直接定义在 DFG 内部。
- **输出锚定**: 最终写回节点必须标记 `is_output=True` 和 `meta_name="xxx_result"`。

### 1.2 物理保真拓扑 (Phys-Safe Design)
针对 AIACC **Pulse-Pass (脉冲存储)** 架构：
- **深层 DFG**: 路径深度超过 2 拍的中间变量，必须显式通过 `UR_WRITE` 存入 Bank 并重读，防止物理信号丢失。
- **输入重读**: 长路径末端的输入引用（如 Sin 最后一拍的 x）建议重新执行 `UR_READ` 以保证对齐。

---

## 2. 验证流程规范 (Automatic Multichannel Testing)

### 2.1 数据工厂注册 (Data Registry)
禁止在测试脚本中硬编码数据。必须在 `tests/operators/utils/data_generator.py` 的 `OP_DATA_SPECS` 中注册：
- `range`: 算子的数学收敛域（如 Sin 为 -1.57 ~ 1.57）。
- `seeds`: 算子的关键边界点（如 0.0, NaN, Inf, Pi/2）。

### 2.2 全自动 CCUG 冲突扫射
所有算子必须在 `tests/operators/arithmetic/test_arith_bank_fuzz.py` 中通过 64+ 种 Bank 冲突组合的压力测试。

---

## 3. 开发检查单 (V4.0 Checklist)

- [ ] **角色对齐**：是否所有输入节点都有 `role` 标签？
- [ ] **系数固化**：泰勒展开等系数是否已通过 `value=xxx` 移入 DFG？
- [ ] **输出锚定**：最终输出是否标记了 `is_output=True`？
- [ ] **物理安全**：深层 DFG 是否包含了必要的中间变量存取逻辑？
- [ ] **数据通道**：`data_generator.py` 是否已配置该算子的收敛域？
- [ ] **比特对齐**：`test_arith_bank_fuzz.py` 是否跑通了 64 个 CCUG Case？

---

**遵循此 SOP，即代表你正在交付符合生产环境要求的工业级算子。**
