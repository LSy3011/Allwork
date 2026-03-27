# 源码结构定义 (SE 专属, ADR-002/003 更新)

## 1. 源码全景文件树

```
src/
├── api/
│   ├── hw_spec.py                # HardwareSpec (Pydantic 五源加载)
│   ├── contracts.py              # SynthesisAPI, LayoutAPI (ABC)
│   └── registry.py              # OperatorRegistry (算子注册表) [GAP-2]
│
├── ir/
│   ├── base.py                   # IRNode(node_id, trace_id)
│   ├── hir.py                    # HIRProgram, HIRNode
│   ├── mir/
│   │   ├── op.py                 # MIROp(logic_phase, iq_type, is_rigid)
│   │   └── footprint.py          # FootprintMask(bank, port, unit)
│   └── lir/
│       ├── instr.py              # LIROp(abs_cycle, vld, loops, dly)
│       └── encoding.py           # 41-bit 编解码
│
├── synthesis/
│   ├── axioms.py                 # 六大物理公理
│   ├── backend.py                # Z3 Solver 驱动
│   ├── constraints.py            # MIR→Z3 翻译
│   ├── registry.py               # APB 缓存
│   └── stress.py                 # 压力测试接口
│
├── orchestrator/
│   ├── footprint.py              # APB 足迹掩码
│   ├── resource.py               # AdvancedResTable
│   ├── stitcher.py               # 相位缝合 + DLY 注入
│   └── scheduler.py              # 全局调度
│
├── layout/
│   ├── liveness.py               # 生命周期分析
│   └── allocator.py              # 8-Bank 着色 (受 SMT exclusions 约束)
│
├── driver/
│   ├── emitter.py                # 41-bit 打包
│   └── compressor.py             # 循环压缩 + 地址折叠
│
├── core/
│   ├── pipeline.py               # PassManager (11-Pass 级联)
│   ├── audit.py                  # MLIR-Style 审计快照
│   └── checker.py                # 相位不变式守卫
│
├── verification/                 # [GAP-1] 独立验证工具
│   ├── smt_verifier.py           # SMTOutputVerifier
│   ├── stream_scanner.py         # StreamScanner (R-R/W-W 扫描)
│   └── constraint_checker.py     # ConstraintChecker (Layout 满足 SMT 约束)
│
├── library/                      # 算子模板库 [GAP-3]
│   ├── base.py                   # OperatorTemplate 基类
│   ├── atomic/                   # Atomic 算子 (SMT 合成)
│   │   ├── sin.py
│   │   ├── exp.py
│   │   └── ...
│   ├── composite/                # Composite 算子 (HSTS 缝合)
│   │   ├── atan2.py
│   │   └── ...
│   └── algebraic/                # Algebraic 算子 (启发式)
│       ├── add.py
│       └── ...
│
└── tests/
    ├── conftest.py               # [GAP-4] Mock 工厂
    ├── unit/
    └── integration/
```

## 2. 模块依赖规则

```
api/ir  ←  synthesis  ←  orchestrator  ←  layout  ←  driver
                                                        ↑
                              core (可调用所有)
                              verification (可读取所有输出，不可修改)
```

## 3. 关键类接口

### api/registry.py (算子注册表) [GAP-2]
```python
class OperatorRegistry:
    """算子→路径+模板 的中心映射"""
    _registry: Dict[str, OperatorEntry]

    def register(name: str, path: str, template: str): ...
    def get_template(name: str) -> OperatorTemplate: ...
    def get_path(name: str) -> str:  # "atomic"|"composite"|"algebraic"

# 使用方式 (算子注册)
registry.register("sin", path="atomic", template="library.atomic.sin")
registry.register("atan2", path="composite", template="library.composite.atan2")
registry.register("add", path="algebraic", template="library.algebraic.add")
```

### library/base.py (算子模板基类) [GAP-3]
```python
class OperatorTemplate(ABC):
    """所有算子模板的基类"""

    @abstractmethod
    def lower(self, hir_node, hw_spec) -> List[MIROp]:
        """HIR 算子 → MIR 脉冲序列"""

    @abstractmethod
    def get_reference(self, input_tensor) -> Tensor:
        """PyTorch 参考输出 (Bit-Perfect 验证用)"""
```

### verification/constraint_checker.py [GAP-1]
```python
class ConstraintChecker:
    """验证 Layout 的 bank_map 满足 SMT 的 exclusions"""

    def check(self, bank_map, exclusions, windows) -> List[str]:
        violations = []
        for (t_a, t_b) in exclusions:
            if bank_map[t_a] == bank_map[t_b]:
                violations.append(f"EXCLUSION VIOLATED: {t_a},{t_b} both Bank{bank_map[t_a]}")
        return violations
```

### tests/conftest.py (Mock 工厂) [GAP-4]
```python
def make_mock_spec(**overrides) -> HardwareSpec:
    """从真实 YAML 派生 Mock，可覆盖任意字段"""
    base = HardwareSpec.load("hw_config/")
    for k, v in overrides.items():
        setattr(base, k, v)
    return base
```
