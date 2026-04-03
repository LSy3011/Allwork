# AIACC 编译器 5-Stage 工业化标准管线 (ADPC V4.0)

本规范定义了 AIACC 编译器从纯逻辑算法代码直到物理底层比特级验证的完整工业流水线架构。该架构彻底解耦了“算法拓扑表示”与“物理调度约束”，确保硬件仿真端能够完美映射编译器的输出。

---

## 阶段概览 (Pipeline Architecture)

整个编译生命周期分为 5 个有着严格边界的独立阶段：
1. **Stage 1 (MIR)**: 纯拓扑构建 (Logical Topology)
2. **Stage 2 (Bank Algo)**: 寄存器堆分配 (Physical Memory Allocation) 
3. **Stage 3 (SMT Synthesis)**: 约束求解此时序编排 (Temporal & Routing Synthesis)
4. **Stage 4 (LIR Emission)**: 并行指令打包装配 (Hexadecimal LIR Generation)
5. **Stage 5 (SMC Sim)**: SMT与物理引擎对齐验证 (Bit-Perfect Simulation Guard)

---

## Stage 1: DFG 拓扑构建 (MIR Representation)
**专注逻辑，屏蔽一切物理硬件噪音。**

* **核心功能**：建立纯粹的数学计算流图。所有算子在这里表现为抽象的“运算节点”和“边（数据流）”的关系，**绝对杜绝任何对于 Bank、Addr、流水线周期的硬编码。**
* **算法引擎**：`MIROpBuilder` 词法追踪及拓扑排序生成器。
* **输入 (Input)**：由人工依据算法逻辑定义的对象实例创建顺序（如泰勒展开逻辑）。
* **输出 (Output)**：未绑定的 Machine IR (MIR) 图，表现为 `List[MIROp]` 有向无环图序列。
* **验证标准**：通过分析抽象图结构（入边与出边是否丢失循环引用）即可判断连通性，具备严格执行拓扑排序后无死锁。

---

## Stage 2: 内存/Bank 分配器 (Resource Allocation)
**解决物理存储的结构性冲突 (Structural Hazards)。**

* **核心功能**：遍历输入流并为计算图配置底层的 `UR` (Unified Register) Bank与地址隔离空间。确保在同一个 `tick` 周期内，硬件不会由于试图重读或并写同一个 Bank 而触发总线冲突。
* **算法引擎**：采用了基于图着色启发式搜索的 `BalancedBankColorer` 以及动态生命区间分析 (Liveness Analysis) 的 `GreedyAllocator`。
* **输入 (Input)**：纯拓扑 MIR 有向无环图。
* **输出 (Output)**：属性被注入的 MIR。每个 `UR_READ` 和 `UR_WRITE` 获得严格绑定的 `metadata.bank_id` 以及 `metadata.addr`。
* **验证标准**：运行静态冲突检测脚本。如果在任一局部数据流窗口内发现了同一 Bank 下的数据交叉竞争，系统会提前在编译前端抛出冲突异常。

---

## Stage 3: SMT 时序与路由综合 (SMT Temporal & Routing Synthesis)
**软硬件结合的最强阶段，决定指令何在、何时发生。**

* **核心功能**：解决计算指令应该放到哪个特定周期的流水线队列（周期 `t`）中，以及多路并发指令流向哪个独立的硬件调度通道（路由通道如 `iq_rtovr-3`）。
* **算法引擎**：直接采用顶级定理证明器 Z3 (`SMTSynthesizer`)。通过注入 `RAW/WAW/WAR` 数据相关依赖延迟，吞吐量，多并发计算单元占用的综合约束集合 (Axioms) 进行多维数学逻辑求解。
* **输入 (Input)**：注入了 Bank 属性的拓扑图，带入各个计算器的物理 `Latency` 定义清单。
* **输出 (Output)**：所有计算节点和控制节点被注入硬时钟坐标 `global_t`，控制节点（尤其是 `RTOVR` 交叉开关）被强制注入准确对应的目的端 `slot_id` 以及对应的硬件 IQ 队列名称。
* **验证标准**：
  - SMT Solver 返回满足度状态态 (`sat` / `unsat`) ；若是 `unsat` 即意味着当前算力的吞吐量或并行物理上限无法承载这种图操作，报出具体瓶颈。
  - 导出并检测全局 `global_t` 时间步是否严格保持硬件延迟契约。

---

## Stage 4: LIR 指令分发打包 (LIR Emission & Hex Distribution)
**严苛死板的位操作 (Bit-wise Encoding)，严格遵从芯片指令 ISA。**

* **核心功能**：按照 SMT 给定的时间戳 `global_t`，把所有的逻辑算子转换为硬件能运行的 128-bit 或更少长度的 Hex 二进制流。当遇到无指令的周期主动填充 NOP。
* **核心动作 (Crossbar Routing)**：根据上一步绑定的物理映射，直接拆分 RtoVR 单一算符，按其物理 destination `slot_id` 将其精准地送进对应的独立通道文件 `iq_rtovr-{slot_id}.hex` 中。
* **算法引擎**：`LIRPacker` 和 `FieldMapper`。（解析 `aiacc_vXXX_master.yaml` 根据具体的位偏移动态封包）。
* **输入 (Input)**：带有完整 `bank`，`addr` 以及 `global_t` 时间序列的 MIR 列表。完全展开并配置了初值的 `sim_config.json` 大纲。
* **输出 (Output)**：按照不同的 IQ 池独立写成的物理二进制文件，如：`iq_fpmul.hex`, `iq_rtovr-3.hex`、以及初次内存数据注入文件。
* **验证标准**：自动生成 `timeline_ascii.txt` 仿真映射图。确保生成的 hex 文件行数代表总流水线长度，没有乱码和被丢弃的计算节点。

---

## Stage 5: 物理引擎级仿真 (Hardware SMC Simulation)
**比特级别的完全重构仿真对齐。**

* **核心功能**：启动并初始化真正的架构仿真引擎系统 `SMCKernel`，在 0 人工介入的环境下，利用自动化生成的多边界数据集与芯片实际的运行后 Bank 存储数据进行完全对照。
* **算法引擎**：
  - **Data Driver**: `ArithTestDataGenerator` (支持 fuzz，特定数值边界遍历生成)。
  - **Emulator**: 遵守 LSB-First (128-bit 并行通道截断) 的多通道运行机 `physical_runner.py`。
  - **Verdict**: `numpy.allclose`（兼备绝对残差与 NaN 感染隔离的判断算法）。
* **输入 (Input)**：LIR 二进制指令组、自动生成的标准参考数据集 (Golden Data 矩阵)。
* **输出 (Output)**：物理仿真的最终内存输出切片对比报表，最高误差(Max Diff)值，最终决定流水线是否标记为 `[PASSED]` 或者抛出 `[FAILED]`。
* **验证标准**：提取位于配置记录中 `role='output'` 标注的所有张量的内存。在切层解析 (32-bit Shift Unpack) 之后，其结果浮点数的漂移必须完全小于 `5e-3`。任何不应该存在的 NaN 重污染均会立即挂起退出。
