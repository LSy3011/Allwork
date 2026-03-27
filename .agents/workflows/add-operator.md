---
description: AIACC 新增算子的标准化工作流 - 从数学公式到 Bit-Perfect 验证
---

# /add-operator 工作流

当需要新增一个算子时，按以下步骤执行。

## Step 1: 分类 (ADM)
根据算子复杂度判定路径：
- **Algebraic** (≤3 MIR ops): add, mul, relu, neg
- **Atomic** (4~15 MIR ops): sin, exp, log, sqrt
- **Composite** (>15 MIR ops 或多段): atan2, gelu, layernorm

## Step 2: 生成模板 (SE + 大模型)
// turbo
读取 `docs/adm/adr/ADR-003_lowering.md` 确认流程。

对于 Atomic 算子：
```
大模型生成候选 MIR 序列 → 存入 src/library/atomic/{name}.py
```
对于 Composite 算子：
```
拆解为多个 Atomic 子步骤 → 存入 src/library/composite/{name}.py
```
对于 Algebraic 算子：
```
直接映射到 1-2 条 IQ 指令 → 存入 src/library/algebraic/{name}.py
```

模板必须继承 `OperatorTemplate` 基类，实现 `lower()` 和 `get_reference()`。

## Step 3: 注册算子 (SE)
在 `src/api/registry.py` 中注册：
```python
registry.register("{name}", path="{path}", template="library.{path}.{name}")
```

## Step 4: 写红灯测试 (TFD)
```python
# tests/unit/test_{name}.py
def test_{name}_lower_produces_valid_mir():
    template = registry.get_template("{name}")
    mir_ops = template.lower(mock_hir_node, mock_spec)
    assert len(mir_ops) > 0
    assert all(isinstance(op, MIROp) for op in mir_ops)

def test_{name}_bit_perfect():
    # 全链路: lower → synthesize → stitch → emit → simulate
    sim_out = full_compile_and_simulate("{name}", test_input)
    ref_out = template.get_reference(test_input)
    assert torch.equal(sim_out, ref_out)
```

## Step 5: 实现并验证 (IE + PVO)
1. 实现 `lower()` 方法
2. 运行 `pytest tests/unit/test_{name}.py` → 绿灯
3. 运行 `SMTOutputVerifier` → 通过
4. 运行 `StreamScanner` → CLEAN
5. 运行 Bit-Perfect 对比 → 通过

## Step 6: 冻结模板 (IG)
```bash
git add src/library/{path}/{name}.py tests/unit/test_{name}.py
git commit -m "feat: 新增算子 {name} ({path}) - Bit-Perfect 已验证"
```

## 检查清单
- [ ] 算子已分类 (Atomic/Composite/Algebraic)
- [ ] 模板继承 OperatorTemplate
- [ ] 已注册到 OperatorRegistry
- [ ] 红灯测试已就绪
- [ ] Bit-Perfect 验证通过
- [ ] Git 提交
