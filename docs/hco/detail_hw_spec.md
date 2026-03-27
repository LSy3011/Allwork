# 硬件物理约束与 HardwareSpec (HCO 详细规格)

> 来源：设计全书 §1 + §3。数据源为 `hw_config/hw_params.yaml`。

## 1. SMC 系统架构
- **计算位宽**：16/32-bit 并行浮点运算 (FPMUL, FPADD, FMA)
- **指令发射**：多路 IQ 架构，所有 IQ 完全独立并行执行
- **关键约束 1 - 无 FIFO**：无流水线缓冲，所有中间结果必须写回 Bank
- **关键约束 2 - Bank 冲突规则**：
  - R-R (同 Bank 同拍两个读) → **非法**
  - W-W (同 Bank 同拍两个写) → **非法**
  - R+W (同 Bank 同拍一读一写) → **合法**

## 2. 物理时序模型
- $T_{write\_back} = T_{issue} + Latency(Unit) + \Phi_{gap}$

## 3. 硬件参数清单
| 参数 | 消费点 | 影响 |
|:--- |:--- |:--- |
| Unit Latency | SMTSynthesizer | Z3 RAW 公理 |
| Unit Instances | GreedyScheduler | 资源表宽度 |
| Bank Count & Depth | AddrColoring | 着色搜索上界 |
| Bank Port Concurrency | HSTS, ResTable | 读写合法性 |
| Mutex Routing Groups | ResourceTable | 虚假碰撞 |
| DLY/LOOPS Bit-width | Emitter | 指令拆分 |

## 4. Header 字段规格
| 字段 | 位宽 | 限制 |
|:--- |:--- |:--- |
| VLD | 1 | 脉冲使能 |
| LOOPS | 8 | 最大 255 |
| DLY | 4 | 最大 15 拍 |
| INC | 4 | 步长 |
| EMBED | 3 | 最大 8 层嵌套 |

## 5. 硬件单元延时表
| 单元 | 延时 | 数据类型 |
|:--- |:--- |:--- |
| FPADD | 3 | fp16/fp32/bfloat16 |
| FPMUL | 3 | fp16/fp32/bfloat16 |
| INTADD | 1 | int8/int32 |
| FP16MUL | 2 | float16 |
| LOGIC | 1 | AND/OR/XOR |
| UR_READ | 1 | 4路并行 |
| UR_WRITE | 1 | 4路并行 |
| RtoVR | 1 | 40路分布式选通 |

## 6. RtoVR 分布式选通架构
- 非中心化总线，40 个独立 Target 插槽
- 每个 Target 有专属微指令队列 (IQ)
- 同拍可配置全部 40 条路径（无总线竞争）
- 刚性同步：$T_{ALU} = T_{RtoVR} + 1$

## 7. HardwareSpec Schema
```python
class HardwareSpec(BaseModel):
    smc_num: int = 32
    ur_bank_num: int = 8
    ur_bank_depth: int = 256
    ur_read_ports: int = 4
    rtovr_port_num: int = 4
    embed_max_level: int = 6
    unit_latency: Dict[str, int]
    connectivity: Dict[str, List[str]]
    rtovr_mutex_groups: List[Set[int]]
```

## 8. 五源配置加载
| 文件 | 字段 | 消费点 |
|:--- |:--- |:--- |
| hw_params.yaml | smc_num 等 | 全局边界 |
| pipeline_delays.yaml | unit_latency | SMT RAW |
| modules.yaml | ModuleDef | Lowering |
| iq_config.yaml | IQConfig | 压缩 |
| connectivity.xlsx | connectivity | Rtovr 路由 |
