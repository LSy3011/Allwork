---
description: AIACC 算子标准化开发流 (V4.0 - ADPC 契约版)
---

# AIACC 算子标准化开发 SOP (V4.0)

本工作流旨在确保所有算子（Add, Sin, ReLU 等）从 DFG 构造到比特级仿真验证均遵循 **ADPC (物理与逻辑解耦规范)**。

## 核心契约 (The Contract)
1.  **逻辑角色解耦**：输入节点通过 `role` 标签标识，不再依赖物理名字。
2.  **常数灵魂归位**：所有的常数（如泰勒系数）必须原位定义在 DFG 内部（`is_const=True`）。
3.  **物理保真 (Pulse-Pass)**：深层 DFG 必须显式处理中间变量的 Bank 写回与重读。
4.  **自动锚定**：输出节点通过 `is_output=True` 显式声明，实现结果自动比对。

---

## 开发步骤

### 第一步：实现 DFG (library/atomic/...)
使用 `MIROpBuilder` 构造 DFG，并注入标准元数据：
```python
def get_example_dfg():
    b = MIROpBuilder()
    # 1. 输入 (角色化)
    x = b.make_op("UR_READ", "x_rd", RT.UR_READ, [], role="operand_a")
    # 2. 定量 (原位化)
    c1 = b.make_op("UR_READ", "c1", RT.UR_READ, [], is_const=True, value=3.14159)
    # 3. 运算 (物理保正)
    # ... 复杂的 FPMUL / FPADD 逻辑
    res = b.make_op("FPMUL", "res", RT.FPMUL, [r0, r1])
    # 4. 输出 (锚定化)
    rt_fin = b.make_op("RTOVR", "rt_fin", PS.UR_W1_S, [res])
    b.make_op("UR_WRITE", "out", RT.UR_WRITE, [rt_fin], 
              is_output=True, meta_name="example_result")
    return b.get_dfg()
```

### 第二步：注册数学特性 (tests/operators/utils/data_generator.py)
在 `OP_DATA_SPECS` 中增加算子的收敛域和边界种子：
```python
"example_op": {
    "range": (-10.0, 10.0), # 随机扫射范围
    "seeds": [0.0, 1.0, -1.0, 3.14159] # 边界扫射种子
}
```

### 第三步：集成全自动测试 (tests/operators/arithmetic/test_arith_bank_fuzz.py)
在参数化列表中增加算子，并定义它的 Golden 参考模型：
```python
@pytest.mark.parametrize("op_type", ["add", "mul", "rec", "relu", "sin", "example_op"])
def test_arithmetic_ccug_fuzzing(self, op_type, data_mode):
    # ... 映射逻辑已自动化 ...
    if op_type == "example_op":
        dfg_ops = get_example_dfg()
        role_to_input = {"operand_a": "x_val"} # 将角色映射到通道 ch_0 (x_val)
        ref_fn = lambda d: d["operand_a"] * 3.14159 # 定义数学预期值
```

---

## 验收准则 (Acceptance Criteria)
1.  **64+ CCUG 通关**：在 64 种物理冲突组合下均通过验证。
2.  **Bit-Perfect**：仿真输出与 `ref_fn` 的最大误差符合配置（通常为 5e-3）。
3.  **无硬编码 Bank**：DFG 内部除必要的最终输出写回外，不应包含硬编码的 Bank ID（由 SMT 自动合成）。
