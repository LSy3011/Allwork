# 硬件信息完整性保障机制

> 解决：硬件架构师提供信息遗漏或不明确的问题

---

## 机制 1：Sprint 启动提问清单

每个 Sprint 开始前，大模型根据该 Sprint 需要实现的代码，
**主动向你提出一组精确问题**，你只需逐条回答。

### Sprint 1 提问清单 (示例)
```
在我开始实现 HardwareSpec 之前，请确认以下信息：

Q1: ur_read_ports = 4，这是每个 Bank 4 个读端口，
    还是全局共 4 个读端口？
    → 影响: add_bus_width_axiom() 的约束写法

Q2: FPADD 延时 3 拍，这是从操作数就绪到结果可用的拍数，
    还是从指令发射到结果写回的拍数？
    → 影响: add_raw_constraints() 的 T_j >= T_p + ?

Q3: 同 Bank 同拍一读一写，你说是合法的。
    那 UR_READ 和 UR_WRITE 可以同拍访问同一地址吗？
    → 影响: 冲突检测的条件

Q4: RtoVR 的 mutex_groups: [[0,2],[1,3]]，
    这是说 Port 0 和 Port 2 不能同拍使用？
    还是说它们共享同一条物理总线？
    → 影响: add_rtovr_port_mutex_axiom() 的语义

如果你对某个问题不确定，请回答 "TBD"，我会用保守假设先实现，
后续硬件明确后再通过 /refactor-aiacc 工作流更新。
```

### 提问清单的生成规则
大模型在写每个函数之前，扫描该函数消费的 hw_spec 字段，
对每个字段提出"这个值的物理语义是什么"的问题。

---

## 机制 2：YAML 中的 TBD 标记

当你不确定某个值时，不要猜，用 TBD 标记：

```yaml
# hw_params.yaml
timing:
  FPADD: 3
  FPMUL: 3
  INTADD: 1
  SHIFTUP: TBD    # ← 不确定，需要找硬件确认

encoding:
  bits_dly: 4
  bits_loops: TBD  # ← 旧代码是 10-bit，硬件文档说 8-bit，待确认
```

**代码中的防护**：
```python
class HardwareSpec(BaseModel):
    @validator("*", pre=True)
    def reject_tbd(cls, v, field):
        if v == "TBD":
            raise ValueError(
                f"字段 '{field.name}' 标记为 TBD，"
                f"请先确认硬件参数后再编译"
            )
        return v
```

→ 任何 TBD 值都会在加载时**立即报错**，不会悄悄用错误值运行。

---

## 机制 3：函数级消费声明

每个函数在文档注释中声明"我需要消费哪些硬件参数"：

```python
def add_raw_constraints(solver, ops, spec):
    """
    消费的硬件参数:
      - spec.unit_latency[op.type]  # 各单元延时
    物理假设:
      - 延时是从操作数就绪到结果可用的拍数
      - 结果在延时结束后的下一拍可被下游读取
    """
```

**好处**：当你看到"物理假设"，你可以立即判断对不对。
如果假设写错了，你可以在代码运行之前就纠正。

---

## 机制 4：硬件信息覆盖率检查

在 hw_params.yaml 中为每个字段标注"消费点"：

```yaml
timing:
  FPADD: 3         # 消费: synthesis/axioms.py::add_raw_constraints
  FPMUL: 3         # 消费: synthesis/axioms.py::add_raw_constraints
  UR_READ: 1       # 消费: orchestrator/stitcher.py::resolve_rw_raw

resource:
  ur_bank_num: 8   # 消费: layout/allocator.py::allocate
  ur_read_ports: 4 # 消费: synthesis/axioms.py::add_bus_width_axiom
```

**自动检查脚本** (Sprint 结束时运行)：
```python
# check_coverage.py
# 扫描 src/ 中所有 spec.xxx 的引用
# 对比 hw_params.yaml 中是否存在
# 报告: "代码引用了 spec.unit_latency['SHIFTUP']
#        但 hw_params.yaml 中没有 SHIFTUP"
```

→ 代码需要但 YAML 没有的参数 = **你遗漏了的硬件信息**

---

## 机制 5：假设登记簿

当大模型在实现中做了任何物理假设，登记到此文件：

```markdown
| 假设 | 位置 | 影响 | 状态 |
|:--- |:--- |:--- |:--- |
| FPADD 延时含写回拍 | axioms.py:L12 | RAW 约束偏1 | ❓待你确认 |
| Bank 0-3 为数据区, 4-7 为暂存区 | allocator.py:L30 | 着色策略 | ❓待你确认 |
| UR_R 和 UR_W 可同拍同地址 | stitcher.py:L85 | 冲突检测 | ✅ 已确认 |
```

→ 你每次打开新窗口，先检查这个文件，确认所有 ❓ 标记的假设。

---

## 总结：5 道机制的协作

```
Sprint 开始前:
  [机制 1] 大模型主动提问 → 你回答

实现过程中:
  [机制 2] TBD 值会被拦截 → 强制你补充
  [机制 3] 函数注释声明物理假设 → 你随时可审查

Sprint 结束时:
  [机制 4] 覆盖率检查 → 发现遗漏的硬件参数
  [机制 5] 假设登记簿 → 你确认所有 ❓ 项
```
