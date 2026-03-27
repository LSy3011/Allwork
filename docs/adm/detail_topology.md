# 架构拓扑与模块依赖 (ADM 详细规格 - 附)

> 来源：设计全书 §8 + §11 + §13 + §14 + §15。

## 1. 70+ 算子分流模型
| 类型 | 代表算子 | 路径 |
|:--- |:--- |:--- |
| Atomic | sin, exp, log, sqrt, recip, ceil, fmod | SMT 全量合成 |
| Composite | atan2, gelu, layernorm, le, ge, remainder | HSTS 缝合 |
| Algebraic | add, mul, relu, abs, neg, sub | 启发式 Lowering |

## 2. 算子开发三步走
1. **分类**：Atomic / Composite / Structured
2. **模板套用**：继承 `library/{pathway}/template.py`
3. **验证**：Auto-Synthesis + Mock-Audit

## 3. 架构防耦合三纪律
- **禁止 Pass 横向调用**：所有交互由 PassManager 统一编排
- **离线内核注入**：Orchestrator 可独立于 Synthesis 工作（MockAPB）
- **职责冷切割**：synthesis→局部最优解, orchestrator→错峰位, layout→着色

## 4. 模块依赖矩阵
| 模块 | 必须依赖 | 严禁依赖 |
|:--- |:--- |:--- |
| api/ir | None | synthesis, orchestrator, driver |
| synthesis | api, ir | orchestrator, layout, driver |
| orchestrator | api, ir, synthesis | layout, driver |
| layout | api, ir, orchestrator | synthesis, driver |
| driver | api, ir, layout | synthesis, orchestrator |
| core | All | None |

## 5. 耦合三公理
1. 无状态副作用：Pass 间仅通过 IR 对象通信
2. 上游不可见：synthesis 不知道 APB 如何被缝合
3. 下游强制契约：driver 不得修改 layout 分配的地址
