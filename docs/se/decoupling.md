# 模块解耦保障机制 (SE 专属)

> 核心原则：**文档说"禁止"没用，必须代码级强制执行。**

---

## 一、当前模块依赖矩阵

```
api/ir  ←  synthesis  ←  orchestrator  ←  layout  ←  driver
                                                        ↑
                              core (可调用所有)
                              verification (只读)
```

规则：箭头方向是"允许依赖"，反方向 = **禁止**。

---

## 二、强制执行机制 1: __init__.py 出口管控

每个模块的 `__init__.py` **只导出**被允许的接口：

```python
# src/synthesis/__init__.py
from .constraints import synthesize   # ← 唯一出口
from .registry import APBCache

# 不导出 axioms, backend, stress → 外部不可直接访问内部实现
```

**效果**：orchestrator 只能 `from synthesis import synthesize`，
不可能 `from synthesis.axioms import add_raw_constraints`。

---

## 三、强制执行机制 2: 依赖检查脚本

```python
# check_deps.py (Sprint 结束时运行)

ALLOWED_DEPS = {
    "api":           [],
    "ir":            ["api"],
    "synthesis":     ["api", "ir"],
    "orchestrator":  ["api", "ir", "synthesis"],
    "layout":        ["api", "ir", "orchestrator"],
    "driver":        ["api", "ir", "layout"],
    "core":          ["api", "ir", "synthesis", "orchestrator", "layout", "driver"],
    "verification":  ["api", "ir", "synthesis", "orchestrator", "layout", "driver"],
    "library":       ["api", "ir"],
}

# 扫描 src/ 下每个模块的 import，检查是否违反
# 违反时输出:
#   ❌ synthesis/axioms.py 导入了 orchestrator.stitcher
#      → 违反规则: synthesis 禁止依赖 orchestrator
```

---

## 四、强制执行机制 3: 接口抽象层 (ABC)

模块之间不直接依赖具体类，而是依赖抽象接口：

```python
# src/api/contracts.py (所有模块的接口定义)

class ISynthesizer(ABC):
    @abstractmethod
    def synthesize(self, ops, spec) -> APB: ...

class IStitcher(ABC):
    @abstractmethod
    def stitch(self, apb_list, res_table) -> StitchedResult: ...

class IAllocator(ABC):
    @abstractmethod
    def allocate(self, mir, exclusions, spec) -> AllocationResult: ...
```

```python
# src/core/pipeline.py
class PassManager:
    def __init__(self,
        synthesizer: ISynthesizer,     # 注入接口，不是具体类
        stitcher: IStitcher,
        allocator: IAllocator,
    ): ...
```

**效果**：把 synthesis 模块整个重写，只要实现 ISynthesizer，
其他模块的代码 **一行都不用改**。

---

## 五、重构安全评估：你想拆模块时的 4 步检查

### Step 1: 影响面扫描
```bash
python3 check_deps.py --module synthesis
# 输出: 被 orchestrator, core 依赖
#       通过接口 ISynthesizer.synthesize()
#       通过数据类 APB
```

### Step 2: 接口不变检查
拆分后的新模块**必须仍然实现**原来的接口：
- 原来: `synthesis` 实现 `ISynthesizer`
- 拆分: `synthesis_z3` + `synthesis_heuristic` **都实现** `ISynthesizer`
→ 下游模块不受影响

### Step 3: 数据类不变检查
拆分后的模块**必须仍然输出**相同的数据结构：
- APB 的字段（timing, exclusions, windows）不能变
- 可以新增字段，不能删除或改名

### Step 4: 测试回归
```bash
pytest tests/ -v  # 全量跑一遍，全绿才安全
```

---

## 六、模块健康度指标 (Sprint 结束时检查)

| 指标 | 健康 | 危险 | 检查方式 |
|:--- |:--- |:--- |:--- |
| 模块间 import 数 | ≤ 3 个 | > 5 个 | check_deps.py |
| 跨模块直接类引用 | 0 个 | > 0 个 | grep "from xxx.internal" |
| 模块内文件数 | ≤ 5 个 | > 8 个 | 考虑拆分 |
| 单文件行数 | < 300 行 | > 500 行 | 考虑拆分 |
| 接口方法数 | ≤ 5 个 | > 10 个 | 接口太胖考虑拆分 |

---

## 七、典型重构场景

### 场景: synthesis 要拆成 z3 版 + 启发式版
```
拆之前:
  synthesis/
    axioms.py
    backend.py
    constraints.py
  接口: ISynthesizer.synthesize() → APB

拆之后:
  synthesis/
    z3/           ← Z3 求解方案
      axioms.py
      backend.py
    heuristic/    ← 启发式方案 (新增)
      greedy.py
    factory.py    ← 根据算子复杂度选择方案
  接口: ISynthesizer.synthesize() → APB (不变!)

影响: 0 个外部模块需要修改
原因: 外部只通过 ISynthesizer 接口交互
```
