# 源码结构定义 (SE 专属)

> SE 负责定义"代码长什么样"——目录结构、模块边界、类接口。
> ADM 决定"架构是什么"，SE 决定"代码怎么组织"。

## 1. 源码全景文件树

```
src/
├── api/                          # 契约与输入层
│   ├── hw_spec.py                # HardwareSpec (Pydantic)
│   └── contracts.py              # SynthesisAPI, LayoutAPI (ABC)
│
├── ir/                           # 三层 IR 核心体系
│   ├── base.py                   # IRNode(node_id, trace_id, metadata)
│   ├── hir.py                    # HIRProgram, HIRNode(shape, dtype, deps)
│   ├── mir/
│   │   ├── op.py                 # MIROp(logic_phase, iq_type, is_rigid)
│   │   └── footprint.py          # FootprintMask(bank, port, unit)
│   └── lir/
│       ├── instr.py              # LIROp(abs_cycle, vld, loops, dly)
│       └── encoding.py           # 41-bit 编解码
│
├── synthesis/                    # SMT 合成引擎
│   ├── axioms.py                 # 六大物理公理
│   ├── backend.py                # Z3 Solver 驱动
│   ├── constraints.py            # MIR→Z3 翻译
│   ├── registry.py               # APB 缓存
│   └── stress.py                 # 压力测试接口
│
├── orchestrator/                 # HSTS 编排层
│   ├── footprint.py              # APB 足迹掩码
│   ├── resource.py               # AdvancedResTable
│   ├── stitcher.py               # 相位缝合 + DLY 注入
│   └── scheduler.py              # 全局调度
│
├── layout/                       # 空间管理
│   ├── liveness.py               # 生命周期分析
│   └── allocator.py              # 8-Bank 着色
│
├── driver/                       # 硬件驱动
│   ├── emitter.py                # 41-bit 打包
│   └── compressor.py             # 循环压缩 + 地址折叠
│
├── core/                         # 管控层
│   ├── pipeline.py               # PassManager (11-Pass 级联)
│   ├── audit.py                  # MLIR-Style 审计快照
│   └── checker.py                # 相位不变式守卫
│
└── library/                      # 算子模板库
    ├── atomic/template.py        # 原子算子 (sin, exp)
    ├── composite/template.py     # 组合算子 (atan2, gelu)
    └── structured/template.py    # 结构算子 (matmul)
```

## 2. 模块依赖规则（单向，禁止循环）

```
api/ir  ←  synthesis  ←  orchestrator  ←  layout  ←  driver
                                                        ↑
                                    core (PassManager 可调用所有)
```

## 3. 核心类接口定义

### api/hw_spec.py
```python
class HardwareSpec(BaseModel):
    smc_num: int
    ur_bank_num: int
    ur_bank_depth: int
    ur_read_ports: int
    rtovr_port_num: int
    unit_latency: Dict[str, int]
    connectivity: Dict[str, List[str]]
    rtovr_mutex_groups: List[Set[int]]

    @classmethod
    def load(cls, hw_config_dir: str) -> "HardwareSpec": ...
```

### synthesis/axioms.py
```python
def add_raw_constraints(solver, ops, spec): ...
def add_rr_conflict_axiom(solver, ops, spec): ...
def add_ww_conflict_axiom(solver, ops, spec): ...
def add_bus_width_axiom(solver, ops, spec): ...
def add_rtovr_connectivity_axiom(solver, ops, T, B, P, spec): ...
def add_rtovr_port_mutex_axiom(solver, ops, T, P, spec): ...
```

### orchestrator/stitcher.py
```python
class Stitcher:
    def stitch(apb_a, apb_b, res_table) -> Tuple[int, int]: ...
    def resolve_rr_conflict(ops) -> List[MIROp]: ...
    def resolve_port_conflict(apb_list) -> List[MIROp]: ...
    def inject_dly(lir_stream, pos, count): ...
```

### core/pipeline.py
```python
class PassManager:
    def register_pass(name, func): ...
    def run(hir_program, hw_spec) -> LIRStream: ...
```
