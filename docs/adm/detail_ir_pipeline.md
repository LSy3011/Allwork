# IR 体系与编译流水线 (ADM 详细规格 - 1/3)

> 来源：设计全书 §2 + §4 + §5。定义项目结构、三层 IR 和 11-Pass 流水线。

## 1. 三层 IR 体系

### HIR (语义数据流层)
- op_id, shape, dtype, dependencies, fusion_group

### MIR (脉冲调度层) — SMT 主输入
- logic_phase: 相对于 T0 的逻辑节拍偏移
- resource_footprint: 资源占用掩码
- is_rigid: 刚性脉冲标记
- persistent_banks: 跨算子持久性
- bank_rebind_id: Ph2 重分配占位

### LIR (物理二进制层)
- abs_cycle: 绝对时钟周期
- encoded_value: 41-bit 微码包
- vld_bit, loop_meta, trace_ptr

## 2. IR 转换生命周期
| 阶段 | 输入→输出 | 关键产出 |
|:--- |:--- |:--- |
| Lowering | HIR→MIR | sin → 4 FPMUL + 2 FPADD |
| Synthesis | MIR→APB | 最优 LogicPhase + Bank |
| Orchestrator | APBs→Scheduled MIR | DLY 缝合 |
| Emission | MIR→LIR | 41-bit 二进制 |

## 3. 算子 IR 路径
- **Atomic**: sin/exp → SMT 全量合成
- **Composite**: atan2/gelu → HSTS 缝合
- **Structured**: MatMul → Tiled Loop

## 4. 11-Pass 流水线
| # | Pass | 硬件依赖 | 实施文件 |
|:--- |:--- |:--- |:--- |
| 5.1 | GraphFusion | N/A | passes/fusion.py |
| 5.2 | PatternLowering | OpcodeMap | library/template.py |
| 5.3 | Tiling | Bank Depth | passes/tiling.py |
| 5.4 | SMTSynthesis | Latency | synthesis/constraints.py |
| 5.5 | ResourceTable | Instances | orchestrator/resource.py |
| 5.6 | APB_Stitcher | Bus Mutex | orchestrator/stitcher.py |
| 5.7 | Liveness | N/A | layout/liveness.py |
| 5.8 | AddrColoring | Bank Count | layout/allocator.py |
| 5.9 | LoopCompressor | LOOPS Bits | driver/compressor.py |
| 5.10| AddrRefolding | INC Bit | driver/compressor.py |
| 5.11| BinaryEmission | Header Spec | driver/emitter.py |

## 5. 项目文件树
见设计全书 §2.5（约 50 行目录结构图）。
