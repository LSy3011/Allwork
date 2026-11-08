> **2026-11-05 定位更新**：下文是早期 Neura/MLIR 探索记录，本次未重新构建该项目。论文中建议作为研究基础与路线演进，当前主线为 AIACC 硬件约束建模、算子编译与容量感知代码生成，见[中期方案](../中期/中期与毕业论文方案_20261105.md)。下文 Pass/IR 流程闭环不等于多任务硬件执行与收益预测已经验证。

# CGRA 设计记录与项目梳理

本文整理自 `cgra的设计记录 (1).md`，并结合当前 `dataflow-main` 项目源码状态进行归纳。原始记录偏向“实现草稿 + 代码片段”，本文按项目结构、Pass 流水线、IR 属性契约和实验流程重新组织，便于后续维护、汇报和继续开发。

## 1. 项目定位

`dataflow-main` 是一个基于 MLIR 的 Neura/dataflow 编译项目。当前 CGRA 相关扩展的核心目标是：从高层 MLIR/affine 循环中识别计算任务，分析任务间数据依赖，提取静态特征，再基于收益模型将任务映射到 CGRA 网格资源上，最后进行拥塞感知路由，为后端生成或评估硬件执行方案提供 IR 标注。

当前项目中与 CGRA 映射强相关的主线可以概括为：

```text
MLIR 输入
  -> TaskDependencyAnalysisPass
  -> StaticFeatureExtractionPass
  -> ProfitBasedSpatialOrchestrationPass
  -> NegotiatedRoutingPass
  -> 带 task / feature / placement / route 信息的标注 IR 与日志
```

## 2. 相关目录

```text
dataflow-main/
  include/NeuraDialect/
    NeuraPasses.td              # Pass TableGen 注册
    NeuraPasses.h               # Pass C++ 声明
  lib/NeuraDialect/Transforms/
    StaticFeatureExtractionPass.cpp
    TaskDependencyAnalysisPass.cpp
    ProfitBasedSpatialOrchestrationPass.cpp
    NegotiatedRoutingPass.cpp
    CMakeLists.txt              # Transform pass 构建入口
  tools/mlir-neura-opt/
    mlir-neura-opt.cpp          # 命令行 opt 工具入口
  test/samples/
    bert/                       # BERT affine/linalg 样例与图
    lenet/                      # LeNet 样例
  experiments/
    01_baseline_no_fusion_4x4/
    02_resource_abundant_8x8/
    03_resource_constrained_4x4/
    04_bert_analysis/
    05_lenet_analysis/
```

## 3. Pass 设计梳理

### 3.1 TaskDependencyAnalysisPass

源码位置：

```text
lib/NeuraDialect/Transforms/TaskDependencyAnalysisPass.cpp
```

命令行参数：

```text
--task-dependency-analysis
```

该 Pass 将函数顶层的 `affine.for` / `scf.for` 识别为任务 Task，并为每个任务分配 `neura.task_id`。随后遍历任务内部的访存操作，收集读写 buffer 集合，判断任务之间的数据依赖。

当前实现支持的访存来源包括：

- `affine.load` / `affine.store`
- `memref.load` / `memref.store`
- `vector.transfer_read` / `vector.transfer_write`
- `memref.copy`

依赖分类：

- RAW：前一个任务写、后一个任务读，记录为 `streaming_succs`，表示可作为流式生产-消费关系处理。
- WAW / WAR：写写冲突或读写反依赖，记录为 `waiting_succs`，表示需要等待或串行化。
- 无共享 buffer：视为独立任务，不写依赖边。

写入 IR 的主要属性：

```text
neura.task_id          : i32
neura.streaming_succs  : array<i32>
neura.waiting_succs    : array<i32>
```

同时该 Pass 会向 `stderr` 输出 DOT 图描述，便于将任务依赖图可视化。

### 3.2 StaticFeatureExtractionPass

源码位置：

```text
lib/NeuraDialect/Transforms/StaticFeatureExtractionPass.cpp
```

命令行参数：

```text
--static-feature-extraction
```

该 Pass 面向每个 Task 提取静态特征，并把特征作为属性写回 IR。若前序已经运行过 `TaskDependencyAnalysisPass`，它会优先分析带有 `neura.task_id` 的任务；否则会退化为分析函数顶层 affine loop。

核心特征包括：

- `r_vec`：向量化比例，基于结果类型是否为 `vector` 统计。
- `i_arith`：算术强度，近似为算术操作数 / 访存操作数。
- `r_affine`：规则访存比例，统计 affine/vector transfer 等规则访存占比。
- `fan_avg`：平均扇出，用于近似通信压力。
- `s_buffer`：静态 buffer 位数需求，来自被访问 memref 的静态 shape。
- `v_live_out`：任务输出数据量估计，当前以 store/transfer_write 近似。
- `rec_mii`：循环递归最小启动间隔估计，当前用高延迟操作近似区分。
- `l_depth`：任务内部 loop 深度。
- `op_count`：任务内部操作数，用于后续融合粒度限制和延迟估算。

写入 IR 的主要属性：

```text
neura.r_vec
neura.i_arith
neura.r_affine
neura.fan_avg
neura.s_buffer
neura.v_live_out
neura.rec_mii
neura.l_depth
neura.op_count
```

### 3.3 ProfitBasedSpatialOrchestrationPass

源码位置：

```text
lib/NeuraDialect/Transforms/ProfitBasedSpatialOrchestrationPass.cpp
```

命令行参数：

```text
--spatial-orchestration="chip-width=8 chip-height=8"
```

该 Pass 是 CGRA 映射决策的核心。它读取前两个 Pass 产生的任务依赖与静态特征，完成逻辑融合、资源受限调度和空间放置。

整体流程：

1. 从 IR 读取 `neura.task_id`、`neura.streaming_succs` 和静态特征属性。
2. 用收益模型评估任务扩容收益与融合收益。
3. 对 streaming 依赖任务进行贪心逻辑融合，形成 Super Task / Block。
4. 基于依赖图执行资源受限 list scheduling，将 Block 分配到不同 time step。
5. 在每个 time step 内使用 2D first-fit packing 放置到 CGRA 网格。
6. 将放置坐标写回 IR，并输出总延迟估计日志。

当前实现中值得注意的参数：

```text
chip-width / chip-height       # CGRA 网格尺寸
sram-size                      # SRAM 容量，默认 2097152 bit
cost-global / cost-neighbor    # 全局通信与邻近通信代价
alpha / beta / sigmoid-k       # 收益模型权重
i-thresh                       # 算术强度阈值
fusion-threshold               # 融合收益阈值
max-fusion-size                # 单个融合块最大操作数
packing-bonus                  # 计算/访存互补融合奖励
high-penalty                   # 访存/访存资源冲突惩罚
mismatch-weight                # RecMII 不匹配惩罚
```

写入 IR 的主要属性：

```text
neura.placement_x
neura.placement_y
```

原始设计记录中提到的 `neura.tile.coord`、`neura.tile.size`、`neura.time_step`、`neura.super_task_id` 在当前代码中没有完整写回；当前实现重点写回 placement 坐标，并在日志中输出 time step、block 数和 latency。

### 3.4 NegotiatedRoutingPass

源码位置：

```text
lib/NeuraDialect/Transforms/NegotiatedRoutingPass.cpp
```

命令行参数：

```text
--negotiated-routing="width=8 height=8 max-iter=30"
```

该 Pass 是设计记录之后项目继续推进出来的路由阶段。它读取 `neura.placement_x` / `neura.placement_y` 和 `neura.streaming_succs`，为跨 tile 的任务依赖构建 net，并在 2D mesh Routing Resource Graph 上执行 PathFinder 风格的协商式 A* 路由。

核心机制：

- 每个 CGRA tile 对应一个 routing node。
- 上下左右和 local port 被建模为 routing link。
- link 有容量、当前使用量、历史拥塞代价和基础代价。
- 每轮 rip-up and reroute 后检查 overflow link。
- 若仍拥塞，则提高历史代价和当前拥塞惩罚，促使下一轮绕行。

该 Pass 当前主要输出路由日志，还没有把完整 path 写回 IR。

## 4. Pass 注册与构建状态

当前三个设计记录中的 Pass 以及后续新增的路由 Pass 均已注册到：

```text
include/NeuraDialect/NeuraPasses.td
include/NeuraDialect/NeuraPasses.h
lib/NeuraDialect/Transforms/CMakeLists.txt
```

`NeuraPasses.td` 中已有：

```text
static-feature-extraction
task-dependency-analysis
spatial-orchestration
negotiated-routing
```

`CMakeLists.txt` 中也已经把对应 `.cpp` 文件加入 `MLIRNeuraTransforms`。

## 5. 推荐运行流程

单独做任务依赖分析：

```sh
./tools/mlir-neura-opt/mlir-neura-opt \
  --task-dependency-analysis \
  ../test/samples/bert/bert_affine.mlir \
  > annotated_bert.mlir
```

完整 CGRA 映射流程：

```sh
./tools/mlir-neura-opt/mlir-neura-opt \
  -affine-super-vectorize="virtual-vector-size=4" \
  --task-dependency-analysis \
  --static-feature-extraction \
  --spatial-orchestration="chip-width=8 chip-height=8" \
  --negotiated-routing="width=8 height=8 max-iter=30" \
  ../test/samples/bert/bert_affine.mlir \
  > annotated_mapped_bert.mlir
```

说明：

- 统计信息、DOT 图、调度与路由日志通常输出到 `stderr`。
- 标注后的 IR 输出到 `stdout`，因此可用重定向保存。
- 如果只运行 `spatial-orchestration` 而没有先运行 `task-dependency-analysis`，将无法读取 `neura.task_id`，映射阶段会提示没有任务。
- 如果不运行 `static-feature-extraction`，收益模型缺少显式特征，当前实现会依赖默认值，结果可信度会下降。

## 6. 实验目录含义

当前 `experiments/` 下已有多组实验结果，说明项目已经不仅停留在 Pass 原型：

- `01_baseline_no_fusion_4x4`：4x4 网格、不启用融合的 baseline。
- `02_resource_abundant_8x8`：8x8 资源相对充足场景。
- `03_resource_constrained_4x4`：4x4 资源受限场景，用于观察调度和装箱压力。
- `04_bert_analysis`：BERT 样例的 baseline / fusion / routing 对比结果。
- `05_lenet_analysis`：LeNet 样例的 baseline / fusion / DSE 结果。

这些目录中的 `.log`、`.mlir`、`.csv` 和 `.png` 可作为调参和汇报材料来源。

## 7. 当前设计亮点

1. Pass 流水线已经形成闭环：依赖分析、特征提取、收益建模、空间映射、协商路由都有对应实现。
2. IR 属性作为阶段之间的接口，降低了 Pass 间直接耦合。
3. 依赖分析已覆盖 vector transfer 和 memref copy，适配 BERT 这类向量化后的 MLIR。
4. 映射阶段考虑了资源受限 scheduling，不再假设所有任务可一次性放入 CGRA。
5. 融合收益模型引入计算/访存互补性、融合粒度上限和 RecMII mismatch 惩罚，比原始草稿更接近实际硬件约束。
6. 后续路由阶段使用 PathFinder 思路，开始把 placement 结果和 NoC 拥塞联系起来。

## 8. 当前不足与建议

1. `ProfitBasedSpatialOrchestrationPass` 当前只写回 `neura.placement_x/y`，建议补齐 `neura.time_step`、`neura.super_task_id`、`neura.tile.size` 等属性，方便后端和可视化直接消费。
2. `StaticFeatureExtractionPass` 的 `rec_mii`、`v_live_out`、访存延迟仍是粗粒度估计，建议后续保存更细的 `n_compute`、`n_memory`、`n_load`、`n_store`。
3. `TaskDependencyAnalysisPass` 目前以 buffer 级别判断依赖，没有区分 affine index 范围，可能把可并行的不同切片误判为冲突。
4. `NegotiatedRoutingPass` 目前主要输出日志，建议将每条 net 的 path、hop 数、拥塞状态写回 IR 或 JSON。
5. 原始设计记录中的中文内容存在编码显示问题，建议保留本文作为维护版文档，原文件作为历史记录即可。
6. 建议给 BERT/LeNet 流程补充一键脚本，把 pass pipeline、日志保存、DOT/PNG 生成和结果 CSV 汇总串起来。

## 9. 后续文档组织建议

建议将项目文档拆为以下几类：

```text
README.md                         # 构建与快速开始
docs/CGRA_PASS_PIPELINE.md         # Pass 流水线与 IR 属性契约
docs/CGRA_PROFIT_MODEL.md          # 收益模型公式、参数、调参方法
docs/CGRA_EXPERIMENTS.md           # BERT/LeNet/资源受限实验说明
docs/CGRA_TODO.md                  # 已知问题与后续计划
```

如果暂时不想拆分，本文可以作为单文件总览，后续再逐步拆出去。

