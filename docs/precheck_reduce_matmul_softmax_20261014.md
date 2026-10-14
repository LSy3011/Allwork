# 新算子类型限时能力预检：归约 / matmul / softmax（2026-10-14）

> 任务来源：用户 2026-10-14 路线图原文——「归约、matmul、softmax 等新类型，现在可以做
> 一次限时能力预检：明确缺的是表达、lowering、调度还是存储规划。若需要大幅重构，就
> 安排到中期之后；不能把'新增一个算子'当成几天内必然完成的小任务。」
> 性质：**限时静态预检**（读码 + 已冻结文档分析；本预检未新跑 VCS、未触板）。
> 口径分级：【源码】= 读代码/配置原文；【归档】= 已冻结 ADR/收口文档；【推断】标出。
> 「缺」的判定基准 = 配置链当前能力边界（ADR-056 §A + 四算子实证形态）；缺席断言均
> 附正面枚举锚（搜索目标本身存在且已列出），不凭空断言。

## 1. 判读基准：当前链的固有形态 = 逐元素流式 map

ADR-056 §A.1（【归档】原文）：用户公式层 = 「逐元素 fp32 表达式：张量输入（stride=1）
×标量参数（stride=0 驻留），经加法+乘法的有限组合构成**单输出**式；**无副作用、
无归约、无向量内跨元素引用**」。§A.2-1：运算符 ⊆ {+,*}，函数调用/幂/除法/二元减法/
比较/选择全部范围外。§A.2-2：lower 后计算节点单元 ⊆ {FPMUL, FPADD}，「出现任何其他
计算单元（LOGIC 等）即范围外」。§A.3 非目标明文：**矩阵/归约/移位类结构**。

已实证的四算子（add / scale_sum / horner_v3u / addcmulu）全部为上述 map 形态：
每个输出元素 = 同下标输入元素的函数；horner 的 6 级链与 addcmulu 的 3 因子链都是
**元素内** DFG，不跨迭代。前端实现锚（【源码】）：`verification_v2/ops/configurable.py:73`
把每个张量名 sympify 为**原子 Symbol**——无下标、无求和构造、单表达式单输出。

## 2. 逐类型分层判定

### 2.1 归约（y = Σxᵢ 类，含 sum/mean/max 同构族）

| 层 | 判定 | 依据 |
|---|---|---|
| 表达 | **缺** | 多对一映射超「单输出式」；§A.1「无归约、无向量内跨元素引用」明文排除；张量名是原子 Symbol，语法面无求和构造【源码+归档】 |
| lowering | **缺** | 需跨迭代累加节点（loop-carried 边）。配置链四算子 DFG 全为元素内（horner/addcmulu 链不跨迭代）【实测·四案例】。先例存在但不在配置链：手写 matmul 的 cumulative addtree 自环（loop_carried II≥4，仅 python sim 模型 A，无 VCS）【归档 ADR-025】 |
| 调度 | **部分有先例** | loop-carried II≥4 在手写线已知【归档 ADR-025】；配置链 SMT 从未携带跨迭代 carried 边运行【推断·四算子全 map】 |
| 存储规划 | **缺** | 输出为标量/少量值，非流式整 bank；部分和跨段传递需累加器重载约定——n244 已证「UR 跨段保持」机制本身可行【实测】，但发射侧无累加器约定支持【推断】 |

小结：**四层均缺，但每层有可指认的最小先例或机制**。第一阻断 = 表达层语法扩展
（reduction 构造 + 公式语义注册表新增记录 + 文本闸扩展）。属中等重构，排中期冻结后。

### 2.2 matmul（Y = A·B）

硬件机制**已知**【归档 ADR-025】：N 维广播（SHIFTUP）+ 每 SMC 与本地权重列点积
（SMC 内 FP32ADDTREE5TO1/8TO1 归约）+ 每 SMC partitioned 输出；跨 SMC 只有广播，
**无 sum-reduce**；ADR-024（K 维 SHIFTDN gather-reduce）前提证伪、整体作废。

| 层 | 判定 | 依据 |
|---|---|---|
| 表达 | **缺（配置链）** | 需 2-D 索引/广播语义（A[i,k]·B[k,j]），超 stride-1 流式 map；yaml 遗留字段 `parallel_dim: "N"/"K"`（ADR-018，configurable.py:48-58）仅是 partition 声明，非表达式能力【源码】 |
| lowering | **部分有先例** | 广播流 SHIFTUP + addtree 单元在 hw_config【源码 modules.yaml:340/:353/:362】；`ManualMatmulParallelNOperator` 手搭 DFG 绕行（configurable 4-operand UNSAT）【归档】；**配置链 lowering 当前不能生成广播+addtree 形态**【推断】 |
| 调度 | **部分有先例** | cumulative addtree 自环 loop_carried II≥4，python sim 8/8 bit-perfect @ N=8【归档】；配置链 SMT 未运行此形态【推断】 |
| 存储规划 | **部分有先例** | 权重驻留 vs 流式布局、输出按 SMC 分区在手写线已落地；K 多 tile 未做（DEBT-025-MULTITILE 开放）【归档】 |

已有资产：`ManualMatmulParallelNOperator` 8/8 bit-perfect @ N=8（python sim **模型 A**；
债务 DEBT-023-BCAST-TIMING 广播时序未验、DEBT-025-MULTITILE、**无 VCS**）——手写线
资产，不在配置链，升 Accepted 需 VCS。

小结：缺 = 表达 + 配置链 lowering；手写线有模型 A 资产但 VCS/时序债务未清。
「配置链新增 matmul」= 大任务；「手写线升 VCS」是另一条既有债务线。两者都排中期后。

### 2.3 softmax（yᵢ = exp(xᵢ−max x) / Σⱼ exp(xⱼ−max x)）

| 层 | 判定 | 依据 |
|---|---|---|
| 表达 | **缺 ×4** | exp（函数）、max（归约）、sum（归约）、div（除法）四个**独立** §A.2-1 违规；且需双趟数据流（先 max/sum 后 normalize），超单趟流式【归档+推断】 |
| lowering | **缺** | 硬件单元正面枚举（【源码】modules.yaml 全 19 单元）：FPADD/FPMUL/FP16MUL/LOGIC/RTOVR/VIN0/VIN1/VOUT0/STB/LDB/INTADD/FPTOINT/INTTOFP/INTTOINT/FPTOFP/FP32ADDTREE5TO1/FP32ADDTREE8TO1/SHIFTUP/SHIFTDN——**无 EXP/DIV/MAX 单元**。LOGIC 有 SELECT_GT/EQ/LS/GE/LE/NE 比较选择（modules.yaml:103-113）但 §A.2-2 把 LOGIC 整体排除在 V1 基础操作集外。exp 可多项式逼近落入 {+,*} 但需值域规约（依赖比较选择）与精度论证；div 可 Newton-Raphson 凑 {+,*} 但需迭代【推断】 |
| 调度 | **缺** | 双趟 + 迭代逼近，无任何先例【推断】 |
| 存储规划 | **缺** | normalize 前须全向量物化（非流式）；max/sum 部分量存放【推断】 |

小结：**大幅重构**（逼近算法设计 + 多趟数据流 + 无现成硬件单元）。按用户规则
明确排中期冻结之后。

## 3. 汇总矩阵

| 类型 | 表达 | lowering | 调度 | 存储规划 | 重构量级 | 排期建议 |
|---|---|---|---|---|---|---|
| 归约（sum 族） | 缺 | 缺（有手写先例） | 部分有先例 | 缺 | 中 | 中期冻结后 |
| matmul | 缺 | 缺（手写线模型 A 资产） | 部分有先例 | 部分有先例 | 中–大 | 中期冻结后 |
| softmax | 缺 ×4 | 缺（无硬件单元） | 缺 | 缺 | **大** | 中期冻结后，且需先立项逼近方案 |

## 4. 对路线图的输入

- 三类新算子**均不进入** 10/29 中期冻结前窗口；阶段二（8 SMC 等长分片）**不受影响**——
  它复用已证 map 形态做多 SMC 使能/地址分配，不是新算子类型。
- 「新增一个算子」对这三类**不是几天的小任务**：最小者（归约）也须先动表达层语法
  与注册表语义，属规则变更，届时须走立法序（先失败形态记录→鉴别性测试→规则修改
  →add/scale_sum 回归）。
- 本预检**不授权任何实现**；中期后若启动，归约是三类中阻断最少、先例最多的入口。

## 5. 锚点

| 结论 | 锚 |
|---|---|
| 公式层=map、无归约、{+,*}、{FPMUL,FPADD}、矩阵/归约非目标 | docs/adr/ADR-056-v1-scope-constrained-general-admission.md §A.1/A.2-1/A.2-2/A.3 |
| 张量名=原子 Symbol、parallel_dim 仅为 partition 字段 | verification_v2/ops/configurable.py:48-58、:73 |
| matmul 硬件机制=广播+addtree、ADR-024 作废、手写线 8/8 模型 A+债务 | docs/adr/ADR-025-matmul-parallel-n-broadcast-addtree.md；CLAUDE.md Known unknowns |
| 硬件 19 单元枚举（无 EXP/DIV/MAX；LOGIC 含 SELECT_*） | hw_config/modules.yaml 全量（:2-:374），LOGIC encoding :103-113 |
| 四算子全 map 形态（含 horner 元素内 6 级链） | docs/thesis_experiment_table_20261013.md §1 |
| UR 跨段保持机制可行（分段程序前提） | scale_sum n244 包 README（2026-10-14）；ADR-061 accept2 n244 GREEN |
