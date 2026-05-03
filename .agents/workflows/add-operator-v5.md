---
description: AIACC 算子规模化扩展 SOP (V5.0 - YAML 驱动版)
---

# AIACC 算子扩展 SOP (V5.0 YAML-Driven)

得益于 AIACC AOS V5.0 物理通道（PhysicalMemoryLayout, UnitPayloadFactory）的全面对齐，现在 70+ 算子的扩展已经与底层物理装配代码**完全解耦**。
所有的新算子开发必须通过本工作流所描述的 **YAML 纯数据驱动** 的方式进行，严禁在 `assembler` 或 `emitter` 中写死算子名称或修改字典！

## 核心契约 (The Contract)
1. **单一事实源 (SSoT)**: 一切物理映射必须基于 `modules.yaml` 自动派生，禁止使用 Python 代码配置指令级 Metadata。
2. **零硬编码寻址**: 在 YAML 中通过 `bank` / `offset` 描述初始位置，中间 Spill / Reload 会由 `PhysicalMemoryLayout` 自动接管并仲裁。
3. **SymPy / AST 驱动**: 数学逻辑直接写于 YAML 的 `formula` 字段，复杂图关系通过 `compiler._lower_expr` 进行统一抽象。
4. **全属性透传**: 任意针对硬件行为的定制化（如精度 `precision: "FP16"`, `cast_to`, `shape`）都只需直接配在 YAML `tensors` 内，系统会 100% 透传至底层 Hex。

---

## 扩展流程 (4 步标准操作)

### 第一步：在 `verification_v2/ops/` 创建 YAML 定义文件
对于一个普通/复合算子（如 `leaky_relu`, `clip` 等），新建 `<op_name>.yaml`。
**模板参考：**
```yaml
name: "leaky_relu"
# 1. 直接编写数学公式，支持 SymPy 所有常用语义 (Max, Min, Piecewise, Add, Mul等)
formula: "Max(0.1 * x, x)"

# 2. 定义 IO 与存储布局
tensors:
  x:         { bank: 0, role: "input" }
  constants: { bank: 7, role: "constant" } # 统一预留给常量（如公式里的 0.1）
  y:         { bank: 2, role: "output" }

# 3. 设定测试用例的随机分布
test_data:
  x: { range: [-5.0, 5.0] }
```

**特殊能力（Tier 3/Tier 4 算子）：**
如果你的算子需要改变精度、做格式转换，直接在 tensors 里追加控制字：
```yaml
tensors:
  x: { bank: 0, role: "input", precision: "FP32", cast_to: "INT8" }
```

### 第二步：根据需要扩充 AST 映射 (仅当用到生僻数学函数时)
若你使用了特殊的 SymPy 函数（如 `sp.Piecewise`，`sp.sign`），检查 `src/frontend/compiler.py` 的 `_lower_expr`。
如果尚未映射，需在该函数里添加拦截，例如将 `Piecewise` 映射为 `FPADD (SUB)` 比对 + `LOGIC (SELECT)` 选通。
*注：目前已经内置支持加减乘除、Max/Min、绝对值、以及基本的 Piecewise。*

### 第三步：运行严格物理审计与模拟测试
必须运行 `main_verify.py` 进行端到端比对，这是检验物理图生成与硬件行为是否完全匹配的唯一标尺：
```bash
python verification_v2/main_verify.py --op <op_name> --review --model
```

### 第四步：核对验收报告
命令跑完后，你必须确认以下两项报告的内容：
1. **[AUDIT-REVIEW] Physical Header Summary**:
   检查被激活的硬件单元，看是否符合你的心理预期。例如，没有出现乱入的 `UNKNOWN` 单元，`LOGIC` 单元被正确激活等。
2. **[SIM-RESULTS] Golden vs. SMC Output**:
   确保终端打出绿色的 `Truth Audit: SUCCESS` 并且 `Max Diff < 1e-5`。
   如果终端打出 `********** ALL PHYSICAL CHECKS PASSED **********`，则该算子扩展宣布成功。

---

## 疑难排解 (Troubleshooting)

* **错误: `MemoryLayout CONFLICT`**
  * **病因**: YAML 中的 `bank` 和 `offset` 设计重叠，或者临时变量把输入数据冲刷掉了。
  * **解法**: 检查该 op 的 YAML `tensors` 字典配置，确保每个 tensor 有独立的物理存放区，或将空间让给 `scratch_bank`。
* **错误: `Cannot resolve IQ type for...` / `ValueError in UnitPayloadFactory`**
  * **病因**: 试图请求一个在硬件规格 (`hw_spec.modules`) 中不存在的单元。
  * **解法**: 查阅硬件团队的 `modules.yaml` 规范，对齐模块名字；如果是降级策略出错，检查 `_lower_expr` 映射是否拼错单元名。
* **错误: `ST` 信号错误或全 0**
  * **病因**: 依赖图节点（如比较节点）缺少 `UPDATE_ST=1` 或选通节点（如 RTOVR）没有收到 `sel`。
  * **解法**: 在 `_lower_expr` 构建节点时确保参数带全，Phase 4 已经实现了对端口 SEL 的严格断言。
