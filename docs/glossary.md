# AIACC 术语表

> 截至 2026-05-15（commit 988e521 之后死代码清理完成）。
> 与 `docs/adm/baseline.md` / `docs/hco/detail_hw_spec.md` 等 V1 文档不一致时**以本表 + 代码为准**。

## 硬件

| 缩写 | 全称 | 说明 |
|---|---|---|
| **SMC** | Stream Multiprocessor Compute Unit | 硬件核心单元，32 实例 |
| **UR** | User Register | 8-Bank 主存储，物理 SRAM 实现 |
| **Bank** | UR 内部分块 | 8 Bank × 256 深度；R-R / W-W 同拍冲突非法，R+W 合法 |
| **IQ** | Instruction Queue | 多路独立并行发射 |
| **RtoVR / RTOVR** | Register-to-Vector-Routing | 分布式选通搬运，40 target 插槽，无总线竞争；刚性同步 T_ALU = T_RtoVR + 1 |

⚠ **命名矛盾**：`hw_config/specs/aiacc_v2.4_master.yaml` + `sim/smc_simulator/memory/ur_bank.py` 中 "UR" 描述为 "Unified Register"，**以本表 "User Register" 为准**，后续可批量改 yaml/注释统一。

## ALU 单元（延迟见 `hw_config/pipeline_delays.yaml`）

| 单元 | 延迟（cycles） |
|---|---|
| FPADD / FPMUL | 3 |
| FP16MUL | 2 |
| INTADD / LOGIC / UR_READ / UR_WRITE / RtoVR | 1 |

## 编译流水（V2 实际，4 阶段）

```
SymPy 公式
  → Frontend (src/frontend/)        — GraphCompiler 把公式编译成 MIR 图，预分配槽位
  → Synthesis (src/synthesis/)      — Z3 求 II + 物理调度
  → Driver (src/driver/)            — LIR 装配 → hex 固件
  → Sim (sim/smc_simulator/)        — Python 物理仿真，bit-perfect 比对
```

V1 残留概念（已废弃，部分 docs 仍提）：
- 🪦 HIR（高层 IR） · AddrColoring · HSTS Stitching · 11-Pass Pipeline · Atomic/Composite/Algebraic 算子分类

## 关键缩写

| 缩写 | 全称 |
|---|---|
| **ADPC** | AIACC Data Path Contract — 算子开发标准协议；入口 `BaseOperator.compile_with_adpc()` |
| **MIR / LIR** | Mid / Low-level Intermediate Representation |
| **II** | Initiation Interval — 模调度发射间隔（Z3 求最小可行 II） |
| **DFG** | Data Flow Graph |
| **ADR** | Architecture Decision Record |

## SMT axioms（代码现状 ~22 个，4 组）

| 组 | 文件 | 数量 |
|---|---|---|
| dependency | `src/synthesis/axioms/dependency_axioms.py` | 2 |
| resource | `src/synthesis/axioms/resource_axioms.py` | 9 |
| physical | `src/synthesis/axioms/physical_axioms.py` | 9 |
| special | `src/synthesis/axioms/special_axioms.py` | 2 |

⚠ docs/{adm,tfd,ig}/* 多处 "六大公理" 是历史叙事，与代码现状不符。

## 算子分类（V2，唯一活的）

| 类别 | 算子（节选） | 来源 |
|---|---|---|
| **math** | add / mul / div / reciprocal / sqrt / rsqrt / abs / addcdiv / addcmul | yaml |
| **trig** | horner / sin* / cos* / cosh* / exp* / log* / sinh* / tan* / tanh* | yaml |
| **activation** | elu / mish / hardsigmoid / silu* / gelu* / sigmoid* / leaky_relu / clip / relu6 / selu / ... | yaml |
| **manual** | matmul · manual_selu（手写 MIR DFG，不走 yaml） | python |

YAML 算子由 `verification_v2/ops/configurable.py:ConfigurableOperator` 动态加载；manual 算子继承 `verification_v2/ops/base.py:BaseOperator`。

## 7 角色（历史，已弃用 → 仅 HCO 升级保留）

原始 7 角色（Gemini 时代 docs 目录约定）：

| 缩写 | 中文 | 处理 |
|---|---|---|
| **HCO** | Hardware Contract Officer（硬件契约官） | ⭐ 升级为 `.claude/agents/hco.md` sub-agent |
| **ADM** | Architecture Decision Maker（架构决策者） | → SOP 的 ADR 机制 |
| **SE** | Software Engineer（软件工程师） | → AI 自带能力 |
| **TFD** | Test-First Designer（测试先行者） | → SOP 隐含的 test-first 原则 |
| **IE** | Implementation Engineer（实现工程师） | → AI 主角，无独立角色 |
| **PVO** | Physical Verification Officer（物理验证官） | → SOP `evaluator` sub-agent |
| **IG** | Integration Guardian（集成守护者） | → SOP `ops` sub-agent（按需，AIACC 无 prod release） |

旧 `docs/{adm,se,tfd,pvo,ig}/` 目录保留作为知识库（不删），但**新决策一律走 `docs/adr/ADR-NNN.md`**，不再扩 7 角色目录。
