---
name: aiacc_operator_factory
description: AIACC 算子重构工厂 - 确保所有算子（Add, Mul, Sqrt, Gelu）从 DFG 构造到比特级仿真验证的一致性。
---

# AIACC 算子重构工厂 (V5.0-Compliance)

为了确保 AIACC 编译器的重构质量，所有算子的新增与改动必须强制对齐 `Sin/Relu` 的工业级标准。

---

## 1. DFG 构造规范 (基于 MIROpBuilder)

所有算子定义文件必须遵循以下代码组织结构，禁止使用硬编码数字或局部闭包函数。

### 强制标准 (Structure):
- **资源导入**: 必须从 `driver.hw_resource` 导入 `ResourceType as RT` 和 `PortSlot as PS`。
- **构造器**: 必须使用 `ir.mir.builder.MIROpBuilder`。
- **签名对齐**:
    - `RTOVR` 算子：第三个参数必须是 `PortSlot (PS)`。
    - `ALU` 算子：第三个参数必须是 `ResourceType (RT)`。
    - `Memory` 算子：显式传入 `bank=N`。

### 模板示例 (`scripts/operator_template.py`):
```python
def get_example_dfg():
    b = MIROpBuilder()
    x = b.make_op("UR_READ", "x_rd", RT.UR_READ, [], bank=0)
    r0 = b.make_op("RTOVR", "r0", PS.ALU_S0, [x])
    res = b.make_op("ALU", "res", RT.ALU, [r0])
    rt_fin = b.make_op("RTOVR", "rt_fin", PS.UR_W1_S, [res])
    b.make_op("UR_WRITE", "w_fin", RT.UR_WRITE, [rt_fin], bank=7)
    return b.get_dfg()
```

---

## 2. 验证流程规范 (Bit-Perfect Validation)

所有重构后的算子必须通过“物理击穿测试”，即在大并发、真实内存分配的情况下，通过回环比对验证。

### 强制流程 (Test Flow):
1. **沙箱隔离**: 每个测试必须在 `build/smoke_<op_name>` 下创建独立目录。
2. **物理编排**: 必须使用 `SMTSynthesizer` + `HSTSStitcher`。
3. **内存审计**: 必须通过 `MemoryAllocator` 分配物理 Bank 和 BaseAddr。
4. **仿真比对**: 调用 `physical_runner.py`，比对 `bank7_final.hex` 与 Numpy 预期值。

### 模板示例 (`scripts/test_template.py`):
集成在 `tests/integration/test_operator_smoke.py` 中。

---

## 3. 开发检查单 (Checklist)

- [ ] 是否完全去除了所有物理数字标识（IQ ID）？
- [ ] 算子是否支持符号化依赖？
- [ ] 测试是否验证了 128-bit 矢量的全宽一致性？
- [ ] `global_t` 是否由 `Stitcher` 自动推导？
