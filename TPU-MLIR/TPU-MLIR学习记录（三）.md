# 关于 TPU-MLIR 源码阅读（三）

@Juno

这个部分主要是 layer-group 的部分，可以参考 b 站链接学习：[TPU-MLIR 线上分享会（二）：LayerGroup_哔哩哔哩_bilibili](https://www.bilibili.com/video/BV1wo4y1z7AG/?spm_id_from=333.337.search-card.all.click&vd_source=a4ce411e59ba1574a8dbf032ea297d1b)

## /LayerGroup/InternalOptimizer.cpp

该代码是 `InternalLgOptimizer` 类的实现，负责管理 TPU-MLIR 框架中层组（Layer Group）优化的 Pass 流水线，核心逻辑围绕根据优化级别（`options.opt`）配置不同的 Pass 流程展开，具体如下：

1. 核心 Pass 管理（`manage_passes` 方法）
   - 所有优化级别都会先执行 `CreateLayerGroupSearchPass`，完成基础的层组划分。
   - 当优化级别 `opt != 3` 时，额外执行一系列后续优化 Pass：
     - 层组后处理转换（`GroupPostTransformPass`）；
     - 时间步分配（`TimeStepAssignmentPass`）；
     - 本地内存分配（`LocalMemoryAllocationPass`）；
     - 时间步合并（`TimeStepCombinePass`）。
   - 当 `opt == 3` 时，仅执行基础层组划分，不执行后续优化 Pass。

```cpp
// 配置层组优化的核心Pass流程
// 参数：pm为Pass管理器，options为层组优化配置选项
void InternalLgOptimizer::manage_passes(std::shared_ptr<LgPassManager> pm,
                                        const LgOptions &options) {
  // 第一步：执行层组搜索，划分基础层组
  pm->add_pass(CreateLayerGroupSearchPass(options));

  // 若优化级别不是3，则执行后续的Pass流程
  if (options.opt != 3) {
    // 层组划分后进行一些转换优化
    pm->add_pass(CreateGroupPostTransformPass(options));

    // 第二步：分配时间步（确定层组执行顺序）
    pm->add_pass(CreateTimeStepAssignmentPass(options));

    // （注释掉）数据拆分Pass（暂未启用）
    // pm->add_pass(CreateDataSplitPass());

    // 第三步：为每个层组分配本地内存（LMem）
    pm->add_pass(CreateLocalMemoryAllocationPass(options));

    // （注释掉）若启用部分系数重加载，则减少系数重加载操作（暂未启用）
    // if (use_partial_coeff_reload) {
    //   pm->add_pass(CreateCoeffReloadDereasePass());
    // }

    // 第四步：合并时间步（优化执行效率）
    pm->add_pass(CreateTimeStepCombinePass(options));
  }
}
```

1. 后处理 Pass 管理（`manage_post_passes` 方法）
   - 当优化级别 `opt != 3` 时，添加 `GroupDataMoveOverlapPass`，用于优化层组间的数据移动重叠，提升执行效率。
   - 当 `opt == 3` 时，不添加后处理 Pass。

```cpp
// 配置层组优化的后处理Pass流程
// 参数：pm为Pass管理器，options为层组优化配置选项
void InternalLgOptimizer::manage_post_passes(std::shared_ptr<LgPassManager> pm,
                                             const LgOptions &options) {
  // 若优化级别不是3，则添加数据移动重叠Pass
  if (options.opt != 3) {
    pm->add_pass(CreateGroupDataMoveOverlapPass(options));
  }
}
```

## 2./LayerGroup/GroupMethod.cpp

### 1.GroupMethod::process

1. 函数整体功能

`GroupMethod::process` 是层组划分的主入口函数，负责根据配置参数（调试模式、优化级别）和环境变量，选择合适的层组划分策略，最终生成或加载层组信息（`LgInfo`）。其核心作用是协调调试、结果加载、分组算法执行等流程，是层组优化的 “总调度中心”。

1. 关键逻辑拆解

##### （1）初始化与参数准备

- 获取输入数据：从 `LgPassIR` 中提取待处理的算子集合（`subnet_ops`）和层组信息容器（`lg_infos`）。
- 时间统计：记录开始时间，用于后续计算总耗时。
- 运行模式与调试器：获取当前运行模式（如静态 / 动态 TPU 模式），并初始化调试器单例。

##### （2）调试器配置（核心分支 1）

根据 `options_.debugger`（调试模式）处理调试文件的生成或加载：

- 模式 0：不处理调试，直接进入分组流程。
- 模式 1：生成调试配置文件后继续分组。
- 模式 2：仅生成调试文件，不执行分组（直接返回）。
- 模式 3/4：加载已有调试文件，基于调试配置进行分组（模式 4 用于部分分组调试）。

##### （3）层组结果加载（核心分支 2）

当存在环境变量 `LOAD_TPU_GROUP` 或优化级别 `opt=4` 时：

- 若层组结果文件已存在，调用 `load_lg_results` 加载结果到 `lg_infos`。
- 若结果文件不存在，直接报错（需通过 `opt=1/2/3` 生成）。
- 支持研究用环境变量 `RESEARCH_SHAPE_SECS`，用于输出层组结果细节。

##### （4）调试模式下的分组（核心分支 3）

当调试模式为 4 时，仅支持优化级别 2，调用带调试的动态规划分组算法 `dynamic_programming_layer_group_with_cluster_debug`。

##### （5）常规分组算法执行（核心分支 4）

根据优化级别 `options_.opt` 选择分组算法：

- opt=1：简单分组算法（`simple_layer_group`）（见 2.2），适合快速划分。
- opt=2：带聚类的动态规划算法（`dynamic_programming_layer_group_with_cluster`）（见 2.3），平衡效率与优化效果。
- opt=3：整数线性规划算法（`ilp_layer_group`）（见 7.1），追求最优分组（计算成本高）。
- 默认：使用简单分组算法。

##### （6）性能统计

计算并输出整个处理流程的耗时（微秒级），用于性能分析与优化。

##### （7）process(LgPassIR *pass_ir)

```cpp
void GroupMethod::process(LgPassIR *pass_ir) {
  // 获取层组信息容器和待处理的算子集合
  std::vector<LgInfo> &lg_infos = pass_ir->lg_infos;
  llvm::SetVector<Operation *> &subnet_ops = pass_ir->subnet_ops;
  // 记录开始时间，用于统计处理耗时
  auto start = std::chrono::high_resolution_clock::now();
  // 获取运行模式（如TPU_STATIC/TPU_DYNAMIC等）
  runmode_ = getRunMode(subnet_ops[0]);
  // 获取调试器单例实例
  auto &lg_debugger = LgDebugger::getInstance();
  // 注释：函数名相关代码（暂未使用）
  // auto func_name = pass_ir->func.getName();

  // 调试器配置逻辑
  // 调试模式说明：
  // 0: 不进行任何调试操作
  // 1: 执行层组划分并生成调试文件
  // 2: 仅生成调试文件，不执行层组划分
  // 3: 加载调试文件并执行层组划分
  // 4: 加载调试文件并执行部分层组划分（用于调试）
  std::string debugger_filename = DEBUGGER_FILE_NAME;  // 默认调试文件名
  if (!options_.debugger_filename.empty()) {
    debugger_filename = options_.debugger_filename;  // 使用用户指定的调试文件名
  }
  // 根据调试模式配置调试器
  switch (options_.debugger) {
  case 0: {
    // 不进行调试操作
    break;
  }
  case 1: {
    // 创建调试配置文件并继续执行层组划分
    lg_debugger.create_debugger_config(debugger_filename);
    break;
  }
  case 2: {
    // 仅创建调试配置文件，不执行层组划分（直接返回）
    lg_debugger.create_debugger_config(debugger_filename);
    llvm::WithColor(llvm::outs(), llvm::raw_ostream::GREEN)
        << "Only create debugger file when debugger=2!\n";
    return;
  }
  case 3: // 贯穿到case 4（两种模式都需要加载调试文件）
  case 4:
    // 加载已有的调试配置文件
    lg_debugger.load_debugger_config(debugger_filename);
    break;
  default: {
    // 无效的调试模式，直接终止程序
    llvm_unreachable("Invalid debugger option");
  }
  }
  // 层组结果加载逻辑：若存在环境变量LOAD_TPU_GROUP或优化级别为4，尝试加载已有结果
  if (getenv("LOAD_TPU_GROUP") || options_.opt == 4) {
    if (is_lg_results_exists()) {
      // 加载层组结果到lg_infos
      load_lg_results(lg_infos, subnet_ops);
      // 若存在研究用环境变量，输出层组结果
      if (getenv("RESEARCH_SHAPE_SECS")) {
        dump_lg_results(lg_infos);
      }
    } else {
      // 结果文件不存在，终止程序并提示生成方法
      llvm_unreachable("file not exist's, ues opt=1/2/3 to generate");
    }
  } 
  // 调试模式4的特殊处理：仅支持优化级别2，使用带调试的动态规划分组
  else if (options_.debugger == 4) {
    switch (options_.opt) {
    case 2:
      dynamic_programming_layer_group_with_cluster_debug(lg_infos, subnet_ops);
      break;
    default:
      // 调试模式4仅支持优化级别2
      llvm_unreachable("only opt=2 is supported when debugger=4");
      break;
    }
  } 
  // 常规层组划分逻辑：根据优化级别选择不同的分组算法
  else {
    switch (options_.opt) {
    case 1:
      // 优化级别1：使用简单分组算法，并输出结果
      simple_layer_group(lg_infos, subnet_ops);
      dump_lg_results(lg_infos);
      break;
    case 2:
      // 优化级别2：使用带聚类的动态规划分组算法，并输出结果
      dynamic_programming_layer_group_with_cluster(lg_infos, subnet_ops);
      dump_lg_results(lg_infos);
      break;
    case 3:
      // 优化级别3：使用基于整数线性规划（ILP）的分组算法，并输出结果
      ilp_layer_group(pass_ir);
      dump_lg_results(lg_infos);
      break;
    default:
      // 默认情况：使用简单分组算法
      simple_layer_group(lg_infos, subnet_ops);
      break;
    }
  }

  // 计算并输出总耗时（微秒级）
  auto end = std::chrono::high_resolution_clock::now();
  auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(end - start);
  llvm::errs() << "GroupMethod_process time:" << elapsed.count() << "\n";
}
```

### 2.simple_layer_group

`GroupMethod::simple_layer_group` 是一种基于贪心策略的简单层组划分算法，核心目标是尽可能多地合并算子形成有效层组。它通过对初步划分的 “基础组” 进行切割点计算，最终生成符合硬件约束（如 LMEM 容量、并行切片可行性）的层组信息。适用于对划分速度要求高、无需复杂优化的场景（对应优化级别 `opt=1`）。

1. 关键逻辑拆解

##### （1）初始化与基础组提取

- 调试日志：通过 `LAYER_GROUP_LOG_DEBUG_BLOCK` 打印分组开始标识，辅助调试。
- 变量初始化：清空历史切割结果（`cut_results_`），定义临时子组（`sub_group`）和基础组容器（`base_groups`）。
- 基础组提取：调用 `get_base_groups` 将输入算子集合（`subnet_ops`）划分为 “基础组”—— 按拓扑序或初步规则（如算子类型兼容性）划分的最小可分组单元，为后续合并提供基础。

##### （2）切割点计算（核心逻辑）

采用从后往前的贪心合并策略，遍历每个基础组并计算切割点（决定哪些算子应合并为一个层组）：

- 单算子基础组：若基础组只有 1 个算子，无需切割，直接记录切割点为 0。
- 多算子基础组：

  - 初始化 `start_idx`（起始索引）和 `end_idx`（结束索引），从基础组的最后一个算子开始尝试合并。
  - 循环验证：通过 `get_layer_group` 构建 `[start_idx, end_idx]` 范围的子组，调用 `is_layer_group_valid` 检查子组有效性（如是否满足 LMEM 约束、是否可并行切片等）。
  - 有效子组处理：若当前子组有效，且仍有前序算子未合并，则调整索引继续尝试合并前面的算子；若已合并到基础组起始位置，则停止。
  - 无效子组处理：若当前子组无效，将 `start_idx` 后移（缩小合并范围），直到找到有效子组或单个算子。
  - 切割点记录：将每次确定的切割点存入 `cut_result`，最终插入到 `cut_results_`（保持与基础组顺序一致）。

##### （3）生成最终层组

- 调试输出：调用 `show_cut_results` 打印所有切割点（仅调试模式下输出），辅助验证切割逻辑。
- 构建层组：调用 `get_final_groups`，根据 `cut_results_` 和 `base_groups` 生成最终的层组信息，并填入输出参数 `lg_infos`。

##### （4）simple_layer_group 代码

```cpp
void GroupMethod::simple_layer_group(
    std::vector<LgInfo> &lg_infos,  // 输出参数：存储最终层组信息的容器
    const llvm::SetVector<Operation *> &subnet_ops) {  // 输入参数：待分组的算子集合
  // 调试日志：打印分组开始标识（仅在调试模式下输出）
  LAYER_GROUP_LOG_DEBUG_BLOCK({
    llvm::outs() << "\n"
                 << "=======================================================\n"
                 << "*********** Group layers as many as possible **********\n"  // 核心目标：尽可能多地合并算子
                 << "=======================================================\n";
  });
  // 清空历史切割结果（存储每个基础组的切割点）
  cut_results_.clear();
  LgInfo sub_group;  // 临时变量：用于存储当前尝试构建的子组信息
  std::vector<std::vector<Operation *>> base_groups;  // 基础组：初步划分的最小可分组单元
  // 第一步：获取基础组（按拓扑序或初步规则划分的最小算子组）
  get_base_groups(base_groups, subnet_ops);
  // 第二步：从后往前遍历基础组，计算每个基础组的切割点（贪心策略：尽可能合并算子）
  for (int64_t i = base_groups.size() - 1; i >= 0; --i) {
    std::vector<int64_t> cut_result;  // 存储当前基础组的切割点（算子索引）
    // 若基础组只有1个算子，无需切割，直接记录切割点为0
    if (base_groups[i].size() == 1) {
      cut_result.push_back(0);
      // 将切割结果插入到总结果的开头（保持与基础组顺序一致）
      cut_results_.insert(cut_results_.begin(), std::move(cut_result));
      continue;
    }
    // 初始化当前基础组的起始和结束索引（从最后一个算子开始尝试合并）
    int64_t start_idx = 0;
    int64_t end_idx = base_groups[i].size() - 1;
    // 先将结束索引加入切割结果（初始假设从末尾开始合并）
    cut_result.insert(cut_result.begin(), end_idx);
    // 循环尝试扩展合并范围：从end_idx向前缩小，直到找到有效子组
    while (end_idx > start_idx) {
      // 构建从start_idx到end_idx的子组信息
      get_layer_group(sub_group, base_groups[i], start_idx, end_idx, i);
      // 检查当前子组是否有效（如满足LMEM约束、并行切片可行性等）
      bool valid = is_layer_group_valid(sub_group, false, nullptr);
      if (valid) {
        // 子组有效：若还有前序算子未合并，调整索引继续尝试合并前面的算子
        if (start_idx > 0) {
          cut_result.insert(cut_result.begin(), start_idx - 1);  // 记录切割点
          end_idx = start_idx - 1;  // 新的结束索引设为当前start_idx的前一个
          start_idx = 0;  // 重置起始索引，从最前面开始尝试
        } else {
          // 已合并到基础组的起始位置，无需继续
          break;
        }
      } else {
        // 子组无效：缩小合并范围（起始索引后移）
        start_idx++;
        // 若起始索引与结束索引重合，说明单个算子为有效子组
        if (start_idx == end_idx) {
          cut_result.insert(cut_result.begin(), start_idx - 1);  // 记录切割点
          end_idx = start_idx - 1;  // 调整索引准备退出循环
          start_idx = 0;
        }
      }
    }
    // 将当前基础组的切割结果插入到总结果的开头
    cut_results_.insert(cut_results_.begin(), std::move(cut_result));
  }

  // 调试：显示所有切割点结果（仅在调试模式下输出）
  show_cut_results();

  // 第三步：根据切割点和基础组，生成最终的层组信息并填入lg_infos
  get_final_groups(lg_infos, base_groups);
}
```

### 3.dynamic_programming_layer_group_with_cluster

1. 函数整体功能

`dynamic_programming_layer_group_with_cluster` 是一种结合聚类和动态规划的层组划分算法（对应优化级别 `opt=2`）。其核心目标是在保证层组有效性的前提下，通过优化分组策略减少冗余计算和数据搬运（GDMA）成本，相比简单分组算法（`simple_layer_group`）能得到更优的层组划分结果，同时通过聚类降低动态规划的计算复杂度。

1. 关键逻辑拆解

##### （1）初始化与基础组提取

- 调试日志：打印算法标识，辅助调试和流程跟踪。
- 基础组提取：调用 `get_base_groups`（见 2.4）将输入算子划分为基础组（与简单分组相同），作为后续处理的基本单元。
- 成本缓存初始化：通过 `LgCostCache::getInstance().init` 初始化成本缓存，存储已计算的分组成本，避免重复计算以提高效率。

##### （2）聚类划分（核心优化 1）

- 聚类目的：将每个基础组中关联紧密的算子（如连续的同类型算子、存在直接依赖的算子）聚为 “聚类”，减少后续动态规划的计算规模（将 “算子级” 划分简化为 “聚类级” 划分）。
- 聚类实现：通过 `get_group_clusters`（见 2.5）完成，输出每个聚类在基础组内的起止索引（`clusters`）。

##### （3）动态规划分组（核心优化 2）

- 适用场景：当聚类数量 `cluster_num > 1` 时，使用动态规划求解最优分组。
- 单聚类处理：若 `cluster_num = 1`，直接将该聚类作为一个层组，记录切割点并验证有效性。
- 核心数据结构：

  - `cost_table`：存储合并第 `a` 到第 `b` 个聚类的成本（如计算耗时、LMEM 占用、GDMA 成本等）。
  - `cut_points`：记录合并第 `a` 到第 `b` 个聚类时的最优切割位置（即如何拆分能使总成本最低）。
- 动态规划核心：调用 `dynamic_programming_kernel`（见 2.6）计算最优分组，通过遍历所有可能的聚类合并方式，选择总成本最低的划分策略。

##### （4）后处理优化（核心优化 3）

- 成本再优化：`consider_redundant_computation_and_gdma_cost`（见 2.7）分析分组中的冗余计算（如重复算子）和数据搬运成本，调整切割点以降低总开销。
- 切割点合并：`merge_cut_idx_to_reduce_gdma_cost`（见 2.8）合并相邻切割点，减少层组数量以降低跨组数据搬运（GDMA）的频率和成本。
- 迭代优化：若切割点合并有效，再次调用成本优化函数，确保合并后的分组仍为最优。

##### （5）生成最终层组

调用 `get_final_groups` 根据优化后的切割点和基础组，生成最终的层组信息并填入 `lg_infos`。

##### （6）dynamic_programming_layer_group_with_cluster 代码

```cpp
void GroupMethod::dynamic_programming_layer_group_with_cluster(
    std::vector<LgInfo> &lg_infos,  // 输出参数：存储最终层组信息的容器
    const llvm::SetVector<Operation *> &subnet_ops) {  // 输入参数：待分组的算子集合
  // 调试日志：打印算法标识（仅调试模式下输出）
  LAYER_GROUP_LOG_DEBUG_BLOCK({
    llvm::outs() << "\n"
                 << "=======================================================\n"
                 << "***** Dynamic Programming layer group with cluster ****\n"  // 算法名称：带聚类的动态规划层组划分
                 << "=======================================================\n";
  });

  // 调试用代码（已注释）：生成子图可视化
  // std::vector<Operation *> ops_vector;
  // for (Operation *op : subnet_ops) {
  //       ops_vector.push_back(op);
  // }
  // std::shared_ptr<dot_graph> opt2_dot_graph = std::make_shared<dot_graph>();
  // createSubnetGraph(ops_vector, opt2_dot_graph);

  LgInfo sub_group;  // 临时变量：存储当前子组信息
  std::vector<std::vector<Operation *>> base_groups;  // 基础组：初步划分的最小可分组单元

  // 第一步：提取基础组（与简单分组相同，按拓扑序或初步规则划分）
  get_base_groups(base_groups, subnet_ops);
  LAYER_GROUP_LOG_DEBUG_BLOCK({
    llvm::outs() << llvm::format("total num of base_group is %d\n",
                                 base_groups.size());  // 输出基础组总数
  });

  // 初始化层组成本缓存（存储已计算的分组成本，避免重复计算以提高效率）
  LgCostCache::getInstance().init(base_groups);  // 注释此行可禁用缓存

  int64_t idx_offset = 0;  // 索引偏移量：记录当前基础组在全局算子序列中的起始位置
  // 遍历每个基础组，进行聚类和动态规划分组
  for (size_t i = 0; i < base_groups.size();
       idx_offset += base_groups[i].size(), ++i) {  // 更新偏移量（累加当前基础组的算子数量）
    // 调试日志：记录基础组处理开始
    LG_DEBUG_WITH_TYPE("lg_step", [&]() {
      llvm::dbgs()
          << DEBUGGER_DEFAULT_INFO(
                 "base_groups_iteration_start", "stamp",
                 "process base_groups[%d], layer_num=%d, idx_offset=%d", i,
                 base_groups[i].size(), idx_offset)
          << "\n";
    });

    std::vector<std::pair<int64_t, int64_t>> clusters;  // 聚类结果：存储每个聚类的起止索引（相对于当前基础组）
    // 调试日志：记录聚类函数调用
    LG_DEBUG_WITH_TYPE("lg_step", [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                          "get_group_clusters", "call_function",
                          "get group clusters of base_groups[%d]", i)
                   << "\n";
    });

    // 第二步：对当前基础组进行聚类（将关联紧密的算子聚为一类，减少后续计算量）
    get_group_clusters(clusters, base_groups[i], i, idx_offset);
    size_t cluster_num = clusters.size();  // 聚类数量

    // 调试日志：输出当前基础组的处理信息
    LAYER_GROUP_LOG_DEBUG_BLOCK({
      llvm::outs() << llvm::format(
          "process base group %d, layer_num=%d, cluster_num=%d\n", i,
          base_groups[i].size(), cluster_num);
    });

    // 第三步：根据聚类数量选择处理方式
    if (cluster_num > 1) {
      // 若聚类数量>1，使用动态规划求解最优分组
      // 成本表：cost_table[a][b]表示合并第a到第b个聚类的成本
      auto cost_table = std::vector<std::vector<int64_t>>(
          cluster_num, std::vector<int64_t>(cluster_num, 0));
      // 切割点表：cut_points[a][b]记录合并第a到第b个聚类时的最优切割位置
      auto cut_points = std::vector<std::vector<int64_t>>(
          cluster_num, std::vector<int64_t>(cluster_num, 0));

      // 调试日志：记录动态规划核心函数调用
      LG_DEBUG_WITH_TYPE("lg_step", [&]() {
        llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                            "dynamic_programming_kernel", "call_function",
                            "process clusters using dynamic programming "
                            "algorithm, cluster_num=%d",
                            cluster_num)
                     << "\n";
      });

      // 调用动态规划核心函数，计算最优分组
      dynamic_programming_kernel(sub_group, base_groups[i], clusters,
                                 cost_table, cut_points, i, idx_offset);
    } else {
      // 若聚类数量=1，直接将该聚类作为一个层组
      LG_DEBUG_WITH_TYPE("lg_step", [&]() {
        llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                            "single_cluster", "stamp",
                            "process clusters whose size is 1")
                     << "\n";
      });

      // 记录切割点（单个聚类无需切割）
      cut_results_.push_back(std::vector<int64_t>(1, 0));
      int64_t start_idx = clusters[0].first;  // 聚类的起始索引
      // 构建层组信息
      get_layer_group(sub_group, base_groups[i], start_idx, start_idx, i,
                      idx_offset);

      // 调试日志：记录单个聚类的成本信息
      GROUP_DEBUG_WITH_TYPE("lg_cost", sub_group, [&]() {
        if (!isa<ReturnOp>(base_groups[i][0]) &&
            runmode_ == RunMode::TPU_STATIC) {
          int64_t cost;
          assert(is_layer_group_valid(sub_group, true, &cost));  // 验证层组有效性并获取成本
          llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                              "single_cluster", "record",
                              "calculate cost of single cluster")
                       << LOG_KV("base_group_idx", i)
                       << LOG_KV("func_start_idx", sub_group.func_start_idx)
                       << LOG_KV("func_end_idx", sub_group.func_end_idx)
                       << LOG_KV("cost", cost) << "\n";
        } else {
          llvm::dbgs()
              << LOG_STEP("GroupMethod::dynamic_programming_layer_group_with_"
                          "cluster@[cost of specific case is set to 0]")
              << DEBUGGER_DEFAULT_INFO("single_cluster", "record",
                                       "cost of specific case is set to 0")
              << LOG_KV("group_idx", i)
              << LOG_KV("func_start_idx", sub_group.func_start_idx)
              << LOG_KV("func_end_idx", sub_group.func_end_idx)
              << LOG_KV("cost", 0) << "\n";
        }
      });
    }
  }

  // 调试：显示当前切割点结果
  show_cut_results();

  // 第四步：后处理1——考虑冗余计算和GDMA（数据搬运）成本，优化分组
  LAYER_GROUP_LOG_DEBUG_BLOCK({
    llvm::outs() << "-------------------------------------------------------\n";
    llvm::outs() << "Consider redundant computation and gdma cost\n";
    llvm::outs() << "-------------------------------------------------------\n";
  });
  consider_redundant_computation_and_gdma_cost(base_groups);
  show_cut_results();  // 显示优化后的切割点

  // 第五步：后处理2——合并切割点以减少GDMA成本（进一步优化）
  LAYER_GROUP_LOG_DEBUG_BLOCK({
    llvm::outs() << "-------------------------------------------------------\n";
    llvm::outs() << "Merge cut idx to reduce gdma cost\n";
    llvm::outs() << "-------------------------------------------------------\n";
  });
  bool take_effective = merge_cut_idx_to_reduce_gdma_cost(base_groups);  // 合并切割点是否有效
  show_cut_results();  // 显示合并后的切割点

  // 若合并有效，再次优化成本（因为切割点变化可能引入新的冗余）
  if (take_effective) {
    LAYER_GROUP_LOG_DEBUG_BLOCK({
      llvm::outs()
          << "-------------------------------------------------------\n";
      llvm::outs() << "Consider redundant computation and gdma cost again\n"
                   << "due to cut idx merged in the previous step\n";
      llvm::outs()
          << "-------------------------------------------------------\n";
    });
    consider_redundant_computation_and_gdma_cost(base_groups);
    show_cut_results();
  }

  // 调试用代码（已注释）：手动修正切割点（用于调试验证）
  // std::vector<int64_t> override_is = {8, 10, 12, 16, 24, 26, 36, 42, 77, 91,
  // 126, 133}; cut_results_[0] = override_is;
  // show_cut_results();

  // 第六步：根据最终切割点生成层组信息，存入lg_infos
  get_final_groups(lg_infos, base_groups);

  // 调试用代码（已注释）：生成最终层组的可视化图
  // int grp_idx = 0;
  // for (auto lg_info : lg_infos) {
  //   if(lg_info.group_ops.size()>1){
  //     for (auto op : lg_info.group_ops) {
  //       if(!isa<ReturnOp>(op)){
  //         auto name = module::getName(op).str();
  //         opt2_dot_graph->add_node_label(name + "_ori",
  //                                       "grp_" + std::to_string(grp_idx));
  //       }
  //     }
  //     grp_idx++;
  //   }
  // }
  // std::cout<<"attention !!! opt2 grp"<<grp_idx<<std::endl;
  // opt2_dot_graph->export_dot("opt2_ok");
}
```

### 4.get_base_groups

##### 核心功能

该函数的核心功能是对子网中的操作进行初步分组，生成 “基础操作组”（`base_groups`）。分组规则基于操作是否支持层组（`LgSupport`）：

- 支持层组的操作被尽可能归为同一组（可能通过 `tmp_group_into_base` 进一步拆分）；
- 不支持层组的操作单独成组（每个操作作为一个独立组）。
  同时，函数会跟踪组内是否包含 “二进制形状操作”，为后续组优化提供信息。

##### 逻辑流程

1. 初始化：定义临时组（`group`）用于累积当前操作，`is_binary` 标记组内是否有二进制形状操作。
2. 遍历子网操作：
   - 支持层组的操作（`isLgSupport(op) == true`）：
     - 若组未标记为二进制，检查当前操作是否为二进制形状（`is_binary_shape_value`）并更新 `is_binary`；
     - 将操作加入临时组；
     - 调用 `tmp_group_into_base` 处理临时组（具体逻辑依赖该函数，可能将组加入 `base_groups` 或继续累积）。
   - 不支持层组的操作（`isLgSupport(op) == false`）：
     - 若临时组非空，先将其加入 `base_groups` 并清空；
     - 当前操作单独成组，加入 `base_groups` 后清空临时组；
     - 重置 `is_binary`（新组无二进制操作）。
3. 收尾处理：遍历结束后，若临时组仍有操作（未被 `tmp_group_into_base` 处理），将其加入 `base_groups`。

##### 核心原理

- 分组依据：层组支持性（`isLgSupport`）是核心划分标准。支持层组的操作通常具有相似的计算特性（如可并行执行、共享资源），归为一组可提升调度效率；不支持的操作因特性特殊，单独处理更可靠。
- 二进制形状跟踪：`is_binary` 标记用于记录组内是否包含二进制形状的张量 / 操作（如二值神经网络中的权重或计算），这类操作可能需要特殊的硬件加速或内存处理，提前标记可优化后续分组策略。
- 逐步累积与拆分：通过临时组（`group`）累积操作，结合 `tmp_group_into_base` 进行动态调整，避免一次性分组导致的过度合并或拆分，平衡组的粒度（既不过大影响并行性，也不过小增加调度开销）。

##### get_base_groups 代码

```cpp
/**
 * @brief 生成基础操作组（base_groups）
 * @details 该函数将子网中的操作（subnet_ops）按是否支持层组（LgSupport）进行分组，
 *          支持层组的操作被归类到同一基础组中，不支持的操作则单独成组，同时记录组内是否包含二进制形状的操作
 * @param base_groups 输出参数，存储生成的基础操作组列表（每个组是一个操作向量）
 * @param subnet_ops 输入参数，子网中的所有操作集合（SetVector确保元素唯一且有序）
 */
void GroupMethod::get_base_groups(
    std::vector<std::vector<Operation *>> &base_groups,
    const llvm::SetVector<Operation *> &subnet_ops) {
  std::vector<Operation *> group;  // 临时存储当前正在构建的操作组
  bool is_binary = false;          // 标记当前组是否包含二进制形状的操作

  // 遍历子网中的所有操作，构建基础组
  for (auto op : subnet_ops) {
    // 检查当前操作是否支持层组（LgSupport）
    if (isLgSupport(op)) {
      // 若当前组尚未标记为二进制，检查当前操作是否为二进制形状操作并更新标记
      if (!is_binary)
        is_binary = is_binary_shape_value(op);
      
      // 将当前操作加入临时组
      group.push_back(op);
      // 尝试将临时组整合到基础组中（可能拆分或保留当前组）
      tmp_group_into_base(base_groups, group, op, is_binary);
    } else {
      // 若操作不支持层组：
      // 1. 先处理之前积累的支持层组的操作（若临时组非空）
      if (!group.empty()) {
        base_groups.push_back(group);  // 将临时组加入基础组
        group.clear();                 // 清空临时组
      }
      // 2. 不支持层组的操作单独成组
      group.push_back(op);
      base_groups.push_back(group);    // 加入基础组
      group.clear();                   // 清空临时组
      is_binary = false;               // 重置二进制标记（新组无二进制操作）
    }
  }

  // 遍历结束后，若临时组仍有未处理的操作，加入基础组
  if (!group.empty()) {
    base_groups.push_back(group);
  }
}
```

### 5.get_group_clusters

1. 核心功能

该函数的核心作用是将一个基础操作组（`base_group`）中的连续操作划分为多个 “聚类”（子组），每个聚类包含若干连续的操作。划分的依据是最大聚类大小限制和成本效益原则（合并操作后的成本需低于单个操作成本总和）。

1. 执行流程

函数根据 `max_cluster_size`（最大聚类大小）分为两种处理逻辑：

- 当 `max_cluster_size == 1` 时：
  每个操作单独作为一个聚类，直接遍历所有操作，生成 `<索引, 1>` 的聚类结果。
- 当 `max_cluster_size > 1` 时：
  动态扩展聚类，具体步骤如下：

  1. 初始化跟踪变量（`start_idx`：当前聚类起始索引，`end_idx`：待检查的下一个操作索引，`cluster_size`：当前聚类大小）。
  2. 遍历操作，计算当前聚类中单个操作的成本总和（`pre_cost`）和合并后的成本（`temp_cost`）。
  3. 检查合并有效性：若合并后成本低于单个成本总和（`pre_cost > temp_cost`），则合并有效，可继续扩展聚类；否则无效。
  4. 终止条件：当聚类无效、达到最大大小限制或遍历至最后一个操作时，结束当前聚类并记录结果，同时初始化下一个聚类。
  5. 边界处理：确保最后一个操作被正确划入聚类。

```cpp
/**
 * 将基础操作组划分为多个聚类（子组），每个聚类包含连续的操作
 * @param clusters 输出参数，存储划分结果，每个元素为<pair>：<起始索引, 聚类大小>
 * @param base_group 基础操作组，包含待划分的操作集合
 * @param group_idx 当前组的索引（用于调试日志）
 * @param idx_offset 索引偏移量（可能用于全局索引计算）
 */
void GroupMethod::get_group_clusters(
    std::vector<std::pair<int64_t, int64_t>> &clusters,
    const std::vector<Operation *> &base_group, int group_idx,
    int64_t idx_offset) {
  LgInfo sub_group;  // 用于存储临时子组信息的结构体
  size_t group_layer_num = base_group.size();  // 基础组中操作的总数量
  // 获取最大聚类大小（根据操作总数动态计算）
  const int64_t max_cluster_size = get_max_cluster_size(group_layer_num);
  // const int64_t max_cluster_size = 1;  // 调试用：强制每个聚类大小为1

  // 初始化聚类跟踪变量：起始索引、结束索引（初始为下一个待检查的操作）、当前聚类大小
  int64_t start_idx = 0, end_idx = 1, cluster_size = 1;

  // 情况1：最大聚类大小为1（每个操作单独作为一个聚类）
  if (max_cluster_size == 1) {
    for (size_t layer_idx = 0; layer_idx < group_layer_num; ++layer_idx) {
      // 每个聚类包含单个操作：<起始索引, 大小1>
      clusters.push_back(std::make_pair<int64_t, int64_t>(layer_idx, 1));
    }
  } else {
    // 情况2：最大聚类大小大于1（需要动态划分聚类）
    int64_t pre_cost = 0;  // 存储当前聚类中单个操作的成本总和

    // 从第2个操作开始遍历（索引从1到最后一个）
    for (size_t idx = 1; idx < group_layer_num; ++idx) {
      // 若当前聚类仅包含start_idx一个操作（刚初始化或上一个聚类结束）
      if (start_idx == end_idx - 1) {
        // 计算start_idx对应操作的全局周期成本（单个操作成本）
        pre_cost = cycle_calculator_->getGlobalLayerCycle(base_group[start_idx]);
        // 输出调试日志：记录初始成本计算
        LG_DEBUG_WITH_TYPE("group_clusters", [&]() {
          llvm::dbgs()
              << DEBUGGER_DEFAULT_INFO(
                     "get_pre_cost", "result",
                     "calculate pre_cost when start_idx == end_idx - 1")
              << LOG_KV("base_group_idx", group_idx)
              << LOG_KV("cost_type", "Global") << LOG_KV("start_idx", start_idx)
              << LOG_KV("end_idx", start_idx) << LOG_KV("cost", pre_cost)
              << "\n";
        });
      }

      // 计算end_idx对应操作的全局周期成本（新增操作的单个成本）
      int64_t post_cost =
          cycle_calculator_->getGlobalLayerCycle(base_group[end_idx]);
      // 输出调试日志：记录新增操作的成本
      LG_DEBUG_WITH_TYPE("group_clusters", [&]() {
        llvm::dbgs() << DEBUGGER_DEFAULT_INFO("get_post_cost", "result",
                                              "calculate post_cost")
                     << LOG_KV("base_group_idx", group_idx)
                     << LOG_KV("cost_type", "Global")
                     << LOG_KV("start_idx", end_idx)
                     << LOG_KV("end_idx", end_idx) << LOG_KV("cost", post_cost)
                     << "\n";
      });

      // 累加单个操作成本总和（pre_cost = 已有操作成本和 + 新增操作成本）
      pre_cost = cost_add(pre_cost, post_cost);

      // 临时变量：存储子组合并后的成本
      int64_t temp_cost = 0;
      // 构建从start_idx到end_idx的子组（包含当前检查的连续操作）
      get_layer_group(sub_group, base_group, start_idx, end_idx, group_idx,
                      idx_offset);
      // 检查子组是否有效，并计算合并后的成本（temp_cost）
      bool is_valid = is_layer_group_valid(sub_group, true, &temp_cost);

      // 输出调试日志：记录子组合并后的成本
      LG_DEBUG_WITH_TYPE("group_clusters", [&]() {
        llvm::dbgs() << DEBUGGER_DEFAULT_INFO("get_group_cost", "result",
                                              "calculate group cost")
                     << LOG_KV("base_group_idx", group_idx)
                     << LOG_KV("cost_type", "Group")
                     << LOG_KV("start_idx", start_idx)
                     << LOG_KV("end_idx", end_idx) << LOG_KV("cost", temp_cost)
                     << "\n";
      });

      // 若子组有效，进一步判断合并是否有收益（合并成本 < 单个成本总和）
      if (is_valid) {
        if (pre_cost <= temp_cost) {
          // 合并无收益（总成本更高或相等），标记为无效
          is_valid = false;
        } else {
          // 合并有收益，更新pre_cost为合并后的成本
          pre_cost = temp_cost;
        }
      }

      // 以下情况需要结束当前聚类：
      // 1. 子组无效；2. 子组有效但已达最大聚类大小限制；3. 遍历到最后一个操作
      if (!is_valid || (is_valid && cluster_size >= max_cluster_size - 1) ||
          idx == group_layer_num - 1) {
        if (is_valid) {
          // 若有效，当前聚类大小+1（包含end_idx对应的操作）
          ++cluster_size;
        }
        // 将当前聚类加入结果集：<起始索引, 聚类大小>
        clusters.push_back(std::make_pair(start_idx, cluster_size));

        // 更新下一个聚类的起始索引：
        // - 若有效，下一个起始索引为end_idx+1（当前end_idx已包含在本聚类）
        // - 若无效，下一个起始索引为end_idx（当前end_idx不包含在本聚类）
        start_idx = is_valid ? end_idx + 1 : end_idx;
        // 调整循环索引（跳过已处理的操作）
        idx = is_valid ? idx + 1 : idx;
        // 重置下一个聚类的结束索引和大小
        end_idx = start_idx + 1;
        cluster_size = 1;
        pre_cost = 0;

        // 处理边界情况：若当前是最后一个操作，或下一个起始索引是最后一个操作
        if ((!is_valid && idx == group_layer_num - 1) ||
            start_idx == group_layer_num - 1) {
          // 加入最后一个聚类（仅包含单个操作）
          clusters.push_back(std::make_pair(start_idx, cluster_size));
          if (start_idx == group_layer_num - 1) {
            break;  // 已处理完所有操作，退出循环
          }
        }
      } else {
        // 继续扩展当前聚类（包含更多操作）
        ++cluster_size;  // 增大聚类大小
        ++end_idx;       // 移动结束索引到下一个操作
      }
    }
  }

  // 调试日志：打印所有聚类的索引和大小
  LAYER_GROUP_LOG_DEBUG_BLOCK({ llvm::outs() << "clusters idx(size): "; });
  for (size_t i = 0; i < clusters.size(); ++i) {
    LAYER_GROUP_LOG_DEBUG_BLOCK({
      llvm::outs() << llvm::format("%d(%d), ", clusters[i].first,
                                   clusters[i].second);
    });
  }
  LAYER_GROUP_LOG_DEBUG_BLOCK({ llvm::outs() << "\n"; });

  // 调试日志：详细输出每个聚类的信息（索引、大小、起止范围）
  LG_DEBUG_WITH_TYPE("cluster_info", [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO("get_group_clusters", "result",
                                          "get clusters info")
                 << "\n";
    for (size_t i = 0; i < clusters.size(); ++i) {
      llvm::dbgs() << LOG_ACTION("cluster_info") << LOG_KV("cluster_idx", i)
                   << LOG_KV("cluster_size", clusters[i].second)
                   << LOG_KV("start_idx", clusters[i].first)
                   << LOG_KV("end_idx",
                             clusters[i].first + clusters[i].second - 1)
                   << "\n";
    }
  });
}
```

### 6.dynamic_programming_kernel

#### 1. 函数整体功能

`dynamic_programming_kernel` 是带聚类的动态规划分组算法的核心实现，负责计算最优的层组切割点。其核心目标是通过动态规划算法，在聚类的基础上寻找使总成本（如计算耗时、数据搬运成本、硬件资源占用等）最小的层组划分方式，是连接聚类与最终分组结果的关键环节。

#### 2. 关键逻辑拆解

##### （1）单个聚类的初始化（基础子问题）

- 对每个聚类（`j`），计算其作为独立层组的成本（`cost_table[j][j]`），并验证有效性（`is_layer_group_valid`）。
- 单个聚类的切割点为自身（`cut_points[j][j] = j`），因为无需切割即可作为一个完整层组。
- 这一步是动态规划的 “基础解”，为后续合并多个聚类提供子问题的解。

##### （2）成本计算函数定义（核心工具）

- `calc_group_cost`：直接计算层组成本，包含有效性验证（无效组返回 `MAX_COST`）。
- `calc_group_cost_with_cache`：带缓存的成本计算，通过哈希键（`hash_key`）复用已计算的成本，避免重复计算，显著提升效率（尤其在聚类数量大时）。

##### （3）动态规划主循环（核心算法）

- 区间长度递增：按聚类区间长度（`len`）从 2 到 `cluster_num` 遍历，确保短区间的解先被计算（动态规划的 “自底向上” 特性）。
- 区间遍历：对每个长度为 `len` 的区间 `[start, end]`，计算合并该区间的最小成本：

  - 首先计算将整个区间作为单个层组的成本。
  - 然后遍历所有可能的切割点 `sweep`（`start ≤ sweep < end`），计算将区间拆分为 `[start, sweep]` 和 `[sweep+1, end]` 两个子区间的总成本（子区间成本之和）。
  - 选择最小成本对应的切割点，更新 `cost_table[start][end]`（最小成本）和 `cut_points[start][end]`（最优切割点）。
- 这一步通过 “拆分 - 合并” 策略，利用子问题的最优解得到当前问题的最优解，确保全局最优性。

##### （4）切割结果生成与调试

- 调用 `get_layer_cut_result` 根据切割点表生成最终的算子级切割点（`cut_result`），记录哪些算子应被划分为同一层组。
- 调试模式下输出详细信息（每个区间的成本验证、切割点表、成本表），用于验证算法正确性和优化效果。

##### （5）dynamic_programming_kernel 代码

```cpp
void GroupMethod::dynamic_programming_kernel(
    LgInfo &lg_info,  // 临时存储层组信息的对象
    const std::vector<Operation *> &base_group,  // 当前处理的基础组算子集合
    const std::vector<std::pair<int64_t, int64_t>> &clusters,  // 聚类结果（每个聚类的起止索引）
    std::vector<std::vector<int64_t>> &cost_table,  // 成本表：cost_table[start][end]表示合并第start到end个聚类的最小成本
    std::vector<std::vector<int64_t>> &cut_points,  // 切割点表：记录合并start到end聚类的最优切割位置
    int64_t base_group_idx,  // 当前基础组的索引
    int64_t idx_offset) {  // 全局算子索引偏移量

  auto &lg_debugger = LgDebugger::getInstance();  // 调试器实例
  auto cluster_num = clusters.size();  // 聚类数量

  //------------------------ 步骤1：初始化单个聚类的成本与切割点 --------------------
  for (size_t j = 0; j < cluster_num; ++j) {
    // 获取第j个聚类的起止索引（相对于当前基础组）
    int64_t start_idx = clusters[j].first;
    int64_t end_idx = start_idx + clusters[j].second - 1;  // 计算结束索引（包含当前聚类）

    // 构建该聚类对应的层组信息
    get_layer_group(lg_info, base_group, start_idx, end_idx, base_group_idx,
                    idx_offset);

    // 验证层组有效性并计算成本，存入成本表（单个聚类的成本即自身成本）
    assert(is_layer_group_valid(lg_info, true, &cost_table[j][j]));

    // 调试日志：记录单个聚类的成本信息
    GROUP_DEBUG_WITH_TYPE("lg_cost", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO("cluster_cost", "record",
                                            "calculate cost_table[%d][%d]", j,
                                            j)
                   << LOG_KV("base_group_idx", base_group_idx)
                   << LOG_KV("start_idx", lg_info.start_idx)
                   << LOG_KV("end_idx", lg_info.end_idx)
                   << LOG_KV("func_start_idx", lg_info.func_start_idx)
                   << LOG_KV("func_end_idx", lg_info.func_end_idx)
                   << LOG_KV("cost", cost_table[j][j]) << "\n";
    });

    // 单个聚类的切割点为自身（无需切割）
    cut_points[j][j] = j;
  }

  // 调试日志：提示开始搜索最优分组切割点
  LAYER_GROUP_LOG_DEBUG_BLOCK(
      { llvm::outs() << "Searching best group slices...\n"; });
  progressbar bar(cluster_num - 1);  // 进度条：用于显示动态规划计算进度

  //------------------------ 步骤2：定义成本计算函数 --------------------
  /**
   * 计算单个层组的成本（包含有效性验证）
   * 若层组无效，返回MAX_COST（表示不可合并）
   */
  auto calc_group_cost = [&](LgInfo &lg_info) {
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                          "is_layer_group_valid", "call_function",
                          "check if the group is valid and calculate the cost")
                   << "\n";
    });
    int64_t group_cost = MAX_COST;  // 初始化成本为最大值（无效状态）
    // 验证层组有效性并计算成本（成本通过引用返回）
    is_layer_group_valid(lg_info, true, &group_cost);
    return group_cost;
  };

  /**
   * 带缓存的成本计算函数
   * 若缓存中存在该层组的成本，则直接返回；否则计算并存入缓存
   */
  auto calc_group_cost_with_cache = [&](LgInfo &lg_info) {
    int64_t group_cost = MAX_COST;
    // 生成当前层组的哈希键（用于缓存查找）
    auto hash_key = LgCostCache::getInstance().get_graph_hash(lg_info);
    // 尝试从缓存中获取成本
    bool cache_hit =
        LgCostCache::getInstance().get_cost_from_cache(hash_key, group_cost);
    if (!cache_hit) {  // 缓存未命中：计算成本并存入缓存
      group_cost = calc_group_cost(lg_info);
      LgCostCache::getInstance().add_cache(hash_key, group_cost);
    }
    return group_cost;
  };

  //------------------------ 步骤3：动态规划主循环（计算最优成本与切割点） --------------------
  // 按聚类区间长度递增处理（len=2表示合并2个聚类，直到合并所有聚类）
  for (size_t len = 2; len <= cluster_num; ++len) {
    // 非调试模式下更新进度条
    if (lg_debugger.get_type() == DEBUGGER_DO_NOTHING) {
      bar.update();
    }

    // 遍历所有可能的起始聚类（start到end构成长度为len的区间）
    for (int64_t start = 0; start <= cluster_num - len; ++start) {
      int64_t end = start + len - 1;  // 计算区间结束索引

      // 获取当前区间[start, end]对应的算子起止索引（相对于基础组）
      int64_t start_idx = clusters[start].first;
      int64_t end_idx = clusters[end].first + clusters[end].second - 1;

      // 构建当前区间对应的层组信息
      get_layer_group(lg_info, base_group, start_idx, end_idx, base_group_idx,
                      idx_offset);

      // 计算合并整个区间[start, end]的成本（使用缓存或直接计算）
      int64_t cost = LgCostCache::getInstance().cache_enabled
                         ? calc_group_cost_with_cache(lg_info)
                         : calc_group_cost(lg_info);
      int64_t optimal_cut_point = end;  // 初始化最优切割点为区间末尾

      // 调试日志：记录当前区间的整体成本
      GROUP_DEBUG_WITH_TYPE("lg_cost", lg_info, [&]() {
        llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                            "group_cost", "record",
                            "calculate group_cost(start_idx=%d, end_idx=%d)",
                            start_idx, end_idx)
                     << LOG_KV("func_start_idx", lg_info.func_start_idx)
                     << LOG_KV("func_end_idx", lg_info.func_end_idx)
                     << LOG_KV("cost", cost)
                     << LOG_KV_FORMAT(
                            "shape_secs", "%d,%d,%d,%d,%d",
                            lg_info.shape_secs.nsecs, lg_info.shape_secs.csecs,
                            lg_info.shape_secs.dsecs, lg_info.shape_secs.hsecs,
                            lg_info.shape_secs.wsecs)
                     << "\n";
      });

      // 遍历所有可能的切割点，寻找最小成本的划分方式
      for (int64_t sweep = start; sweep < end; ++sweep) {
        // 计算切割为[start, sweep]和[sweep+1, end]两个子区间的总成本
        int64_t temp_cost =
            cost_add(cost_table[start][sweep], cost_table[sweep + 1][end]);

        // 调试日志：记录子区间合并成本
        GROUP_DEBUG_WITH_TYPE("cost_table", lg_info, [&]() {
          llvm::dbgs()
              << DEBUGGER_DEFAULT_INFO(
                     "interval_cost", "record",
                     "calculate (cost_table[%d][%d] + cost_table[%d][%d])",
                     start, sweep, sweep + 1, end)
              << LOG_KV("idx_offset", idx_offset) << LOG_KV("cost", temp_cost)
              << "\n";
        });

        // 若当前切割方式成本更低，更新最优成本和切割点
        if (temp_cost < cost) {
          cost = temp_cost;
          optimal_cut_point = sweep;
        }
      }

      // 记录当前区间的最小成本和最优切割点
      cost_table[start][end] = cost;
      cut_points[start][end] = optimal_cut_point;

      // 调试日志：记录当前区间的最优成本和切割点
      GROUP_DEBUG_WITH_TYPE("lg_cost", lg_info, [&]() {
        llvm::dbgs() << DEBUGGER_DEFAULT_INFO("cost_table", "record",
                                              "calculate cost_table[%d][%d]",
                                              start, end)
                     << LOG_KV("func_start_idx", lg_info.func_start_idx)
                     << LOG_KV("func_end_idx", lg_info.func_end_idx)
                     << LOG_KV("optimal_cut_point", optimal_cut_point)
                     << LOG_KV("cost", cost) << "\n";
      });
    }
  }

  //------------------------ 步骤4：生成切割结果并调试输出 --------------------
  llvm::outs() << "\n";  // 进度条输出换行
  std::vector<int64_t> cut_result;  // 存储当前基础组的切割点结果

  // 根据切割点表生成最终的切割结果（算子级索引）
  get_layer_cut_result(cut_result, clusters, cut_points, 0, cluster_num - 1);
  cut_results_.push_back(std::move(cut_result));  // 保存切割结果

  // 调试模式：输出详细的分组验证信息、切割点表和成本表
  LLVM_DEBUG({
    LgInfo lg_info;
    int start = 0;
    // 验证每个切割区间的成本
    for (auto end : cut_result) {
      get_layer_group(lg_info, base_group, start, end, base_group_idx,
                      idx_offset);
      int64_t group_cost = MAX_COST;
      auto temp_status = is_layer_group_valid(lg_info, true, &group_cost);
      llvm::dbgs() << temp_status << " ;start" << start << " - "
                   << " end " << end << " = " << group_cost << "\n";
      start = end + 1;
    }

    // 输出切割点表
    llvm::dbgs() << "\n";
    llvm::dbgs() << "================FINAL GROUP================\n";
    for (size_t cost_i = 0; cost_i < cluster_num; ++cost_i) {
      for (int64_t cost_j = 0; cost_j < cluster_num; ++cost_j) {
        llvm::dbgs() << cut_points[cost_i][cost_j] << ", "
                     << "";
      }
      llvm::dbgs() << "\n";
    }

    // 输出成本表
    llvm::dbgs() << "================COST TABLE================\n";
    for (size_t cost_i = 0; cost_i < cluster_num; ++cost_i) {
      for (int64_t cost_j = 0; cost_j < cluster_num; ++cost_j) {
        llvm::dbgs() << cost_table[cost_i][cost_j] << ", "
                     << "";
      }
      llvm::dbgs() << "\n";
    }
    llvm::dbgs() << "=============================================\n";
    llvm::dbgs() << "\n";
  });
}
```

### 7.consider_redundant_computation_and_gdma_cost

#### 1.功能总结

该函数的核心功能是优化基础操作分组的切割方式，通过调整分组内的切割点，平衡冗余计算和 GDMA（全局数据移动加速器）成本，最终找到使整体成本最小的最优切割方案。

#### 2.核心原理

1. 处理对象：输入为 `base_groups`（基础操作分组列表），每个分组包含一系列 `Operation`（操作），同时依赖 `cut_results_`（每个分组的初始切割点列表）。
2. 优化逻辑：

   - 遍历每个基础分组，针对每个分组的切割结果（`cut_result`），检查是否需要进一步优化（切割点数量 > 1 且集群大小 > 1）。
   - 对每个可优化的切割区间（通过 `j` 索引遍历切割点），计算左子组的起始索引（`left_cut_idx`）。
   - 尝试该区间内所有可能的切割点（从右向左遍历），通过 `get_layer_group` 获取左右子组的详细信息（如操作内容、依赖关系等）。
   - 调用 `update_sequence_group_cost` 计算当前切割方式下的成本（包括冗余计算和 GDMA 成本），并跟踪最优切割点（`optimal_cut_idx`）。
   - 最终将切割点更新为最优值，完成该区间的优化。
3. 关键细节：

   - 通过倒序遍历切割点和尝试不同切割位置，确保覆盖所有可能的分割方式。
   - 引入调试日志（`DEBUG_WITH_TYPE`）跟踪优化过程，便于分析成本变化和切割点选择。
   - `SequenceGroupsInfo` 和 `LgInfo` 分别存储序列分组的成本信息和层组的详细信息，是成本计算的核心数据结构。

#### 3.consider_redundant_computation_and_gdma_cost 代码

```cpp
/**
 * @brief 考虑冗余计算和GDMA成本，优化基础分组的切割方式
 * @param base_groups 基础操作分组列表，每个分组包含一系列Operation指针
 * @return 始终返回true（可能用于表示操作成功）
 */
bool GroupMethod::consider_redundant_computation_and_gdma_cost(
    const std::vector<std::vector<Operation *>> &base_groups) {

  int64_t left_cut_idx;         // 左子组的起始切割索引
  int64_t optimal_cut_idx;      // 最优切割索引（使成本最小的切割点）
  SequenceGroupsInfo seq_info;  // 序列分组信息，存储成本等计算结果
  LgInfo left_sub_group, right_sub_group;  // 左、右子组的层组信息

  // 遍历所有基础分组
  for (size_t i = 0; i < base_groups.size(); ++i) {
    auto &base_group = base_groups[i];  // 当前基础分组
    auto &cut_result = cut_results_[i]; // 当前分组对应的切割结果（存储切割点索引）
    size_t cut_num = cut_result.size(); // 切割点数量

    // 若切割点数量大于1，且当前分组的最大集群大小大于1（需要进一步优化切割）
    if (cut_num > 1 && get_max_cluster_size(base_group.size()) > 1) {
      // 从倒数第二个切割点开始向前遍历（倒序检查每个可优化的切割区间）
      for (int32_t j = cut_num - 2; j >= 0; --j) {
        // 计算左子组的起始索引：若j>0则从上个切割点+1开始，否则从0开始
        left_cut_idx = j > 0 ? (cut_result[j - 1] + 1) : (int64_t)0;

        // 调试日志：记录当前处理的切割区间和分组索引
        DEBUG_WITH_TYPE("lg_index", {
          llvm::dbgs()
              << "; action = lg_index"
              << "; step = consider_redundant_computation_and_gdma_cost"
              << "; start_idx = " << left_cut_idx
              << "; end_idx = " << cut_result[j] << "; group_idx = " << i
              << "\n";
        });

        // 初始化序列分组信息：成本设为-1（无效值）
        memset(&seq_info, 0, sizeof(SequenceGroupsInfo));
        seq_info.min_cost = -1;
        optimal_cut_idx = cut_result[j];  // 初始最优切割点设为当前切割点

        // 调整当前切割点，开始尝试不同的切割位置（从cut_result[j+1]-1向下遍历）
        cut_result[j] = cut_result[j + 1] - 1;
        bool left_first = true;  // 标记是否优先考虑左子组（可能影响成本计算）

        // 遍历所有可能的切割位置（从当前位置减到左子组起始索引）
        for (; cut_result[j] >= left_cut_idx; cut_result[j]--) {
          // 获取左子组信息：从left_cut_idx到当前切割点
          get_layer_group(left_sub_group, base_group, left_cut_idx,
                          cut_result[j], i);
          // 获取右子组信息：从当前切割点+1到下一个切割点
          get_layer_group(right_sub_group, base_group, cut_result[j] + 1,
                          cut_result[j + 1], i);

          // 更新序列分组成本，并判断当前切割点是否更优
          bool is_better = update_sequence_group_cost(
              &left_sub_group, &right_sub_group, &left_first, seq_info);

          // 调试日志：记录左右子组的成本信息
          DEBUG_WITH_TYPE("lg_cost", {
            llvm::dbgs() << "; action = "
                         << "lg_cost"
                         << "; start_idx = " << left_cut_idx
                         << "; end_idx = " << cut_result[j]
                         << "; group_cost = " << seq_info.left_cost
                         << "; group_idx = " << i << "; step = "
                         << "consider_redundant_computation_and_gdma_cost"
                         << "; part = "
                         << "left"
                         << "\n";
            llvm::dbgs() << "; action = "
                         << "lg_cost"
                         << "; start_idx = " << cut_result[j] + 1
                         << "; end_idx = " << cut_result[j + 1]
                         << "; group_cost = " << seq_info.right_cost
                         << "; group_idx = " << i << "; step = "
                         << "consider_redundant_computation_and_gdma_cost"
                         << "; part = "
                         << "right"
                         << "\n";
          });

          // 若当前切割点更优，则更新最优切割点
          if (is_better) {
            optimal_cut_idx = cut_result[j];
            LAYER_GROUP_LOG_DEBUG_BLOCK({
              llvm::outs() << "//// Group cost " << seq_info.min_cost
                           << ", optimal cut idx " << optimal_cut_idx << "\n";
            });
          }
        }

        // 将当前切割点设置为最优切割点（完成当前区间的优化）
        cut_result[j] = optimal_cut_idx;

        // 调试日志：记录优化后的切割区间
        DEBUG_WITH_TYPE("cut_optimize", {
          llvm::dbgs()
              << "; action = cut_optimize"
              << "; step = consider_redundant_computation_and_gdma_cost"
              << "; left_range = " << left_cut_idx << "-" << optimal_cut_idx
              << "; right_range = " << optimal_cut_idx + 1 << "-"
              << cut_result[j + 1] << "; group_idx = " << i << "\n";
        });
      }
    }
  }
  return true;
}
```

### 8.merge_cut_idx_to_reduce_gdma_cost

#### 功能概述

该函数的核心功能是通过合并操作分组中的切割点（cut_idx），降低整体的 GDMA 成本。GDMA（可能指 “全局数据移动成本” 或类似的硬件 / 软件层面的数据传输开销）是优化的目标，函数通过评估 “拆分子组” 与 “合并子组” 的成本差异，选择成本更低的分组方式，最终返回是否有有效的优化操作发生。

#### 核心逻辑

函数采用 “遍历 - 评估 - 优化” 的循环逻辑，具体步骤如下：

- 遍历基础分组：对输入的每个基础分组（`base_groups[i]`）及其对应的切割结果（`cut_results_[i]`）进行处理。
- 筛选优化对象：仅处理 “最大簇大小 > 1” 的分组（通过 `get_max_cluster_size` 判断），这类分组存在进一步拆分 / 合并的优化空间。
- 评估切割点成本：对每个切割点（`cut_idx`），计算：

  - 左子组（`[start_cut_idx, cut_idx]`）的成本；
  - 右子组（`[cut_idx+1, end_cut_idx]`）的成本；
  - 合并后子组（`[start_cut_idx, end_cut_idx]`）的成本。
- 决策合并操作：若合并后成本 < 左右子组成本之和，则移除当前切割点（合并子组），并标记优化生效；否则保留切割点，继续检查下一个。

#### 实现原理

- 成本驱动优化：核心依据是 “合并子组的成本是否低于拆分后的总成本”。当合并能减少 GDMA 开销时，执行合并（删除切割点），本质是通过调整分组粒度降低数据传输成本。
- 增量计算：左子组成本采用增量更新（`left_group_cost = right_group_cost`），避免重复计算，提升效率。
- 有效性校验：通过 `is_layer_group_valid` 确保子组（拆分或合并后）的有效性（如满足硬件限制、操作依赖等约束），避免无效优化。
- 调试追踪：通过 `DEBUG_WITH_TYPE` 输出详细的成本信息和优化操作，便于调试和分析优化效果。

#### get_max_cluster_size 代码

```cpp
// 合并切割点以降低GDMA成本的成员函数
// 参数base_groups：基础操作分组的集合，每个分组是一个Operation*的向量
// 返回值：是否有有效的合并操作发生（true表示有优化生效）
bool GroupMethod::merge_cut_idx_to_reduce_gdma_cost(
    const std::vector<std::vector<Operation *>> &base_groups) {
  LgInfo sub_group;          // 用于存储子组信息的临时变量
  bool lg_valid;             // 标记子组是否有效的布尔变量
  bool take_effective = false;  // 标记是否有有效的合并操作发生，初始为false

  // 遍历所有基础分组
  for (size_t i = 0; i < base_groups.size(); ++i) {
    auto &base_group = base_groups[i];  // 当前基础分组
    auto &cut_result = cut_results_[i]; // 当前分组对应的切割结果（切割点集合）

    // 若当前分组的最大簇大小大于1（需要进一步优化的条件）
    if (get_max_cluster_size(base_group.size()) > 1) {
      int64_t left_group_cost = 0, right_group_cost = 0;  // 左子组、右子组的成本
      int64_t combine_group_cost = 0;                     // 合并后子组的成本
      size_t size_ = cut_result.size();                   // 当前切割结果的大小

      // 遍历切割点，寻找可合并的切割点（j的递增逻辑在循环内处理）
      for (size_t j = 0; j < size_ - 1;) {
        size_t cut_idx = cut_result[j];  // 当前切割点索引

        // 计算左子组的起始索引：若j>0则从上一个切割点+1开始，否则从0开始
        size_t start_cut_idx = j > 0 ? (cut_result[j - 1] + 1) : 0;
        // 右子组的结束索引：下一个切割点
        size_t end_cut_idx = cut_result[j + 1];

        // 计算左子组的成本（仅在首次计算时执行）
        if (left_group_cost == 0) {
          // 获取[start_cut_idx, cut_idx]范围内的子组
          get_layer_group(sub_group, base_group, start_cut_idx, cut_idx, i);
          // 检查子组有效性并计算成本
          lg_valid = is_layer_group_valid(sub_group, true, &left_group_cost);
          assert(lg_valid);  // 确保子组有效（调试断言）

          // 调试日志：输出左子组的成本信息
          DEBUG_WITH_TYPE("lg_cost", {
            llvm::dbgs() << "; start_idx = " << start_cut_idx
                         << "; end_idx = " << cut_idx
                         << "; group_cost = " << left_group_cost
                         << "; group_idx = " << i << "; action = "
                         << "lg_cost"
                         << "; step = "
                         << "merge_cut_idx_to_reduce_gdma_cost"
                         << "; part = "
                         << "left"
                         << "\n";
          });
        }

        // 计算右子组的成本：[cut_idx+1, end_cut_idx]范围内的子组
        get_layer_group(sub_group, base_group, cut_idx + 1, end_cut_idx, i);
        lg_valid = is_layer_group_valid(sub_group, true, &right_group_cost);
        assert(lg_valid);  // 确保子组有效

        // 调试日志：输出右子组的成本信息
        DEBUG_WITH_TYPE("lg_cost", {
          llvm::dbgs() << "; start_idx = " << cut_idx + 1
                       << "; end_idx = " << end_cut_idx
                       << "; group_cost = " << right_group_cost
                       << "; group_idx = " << i << "; action = "
                       << "lg_cost"
                       << "; step = "
                       << "merge_cut_idx_to_reduce_gdma_cost"
                       << "; part = "
                       << "right"
                       << "\n";
        });

        // 计算合并后子组的成本：[start_cut_idx, end_cut_idx]范围内的子组
        get_layer_group(sub_group, base_group, start_cut_idx, end_cut_idx, i);
        lg_valid = is_layer_group_valid(sub_group, true, &combine_group_cost);

        // 若合并后的子组有效，且合并成本低于左右子组成本之和（满足优化条件）
        if (lg_valid) {
          if (combine_group_cost < left_group_cost + right_group_cost) {
            // 调试日志：输出合并后子组的成本信息
            DEBUG_WITH_TYPE("lg_cost", {
              llvm::dbgs() << "; start_idx = " << start_cut_idx
                           << "; end_idx = " << end_cut_idx
                           << "; group_cost = " << combine_group_cost
                           << "; group_idx = " << i << "; action = "
                           << "lg_cost"
                           << "; step = "
                           << "merge_cut_idx_to_reduce_gdma_cost"
                           << "\n";
            });

            // 调试日志：记录切割点合并的优化操作
            DEBUG_WITH_TYPE("cut_optimize", {
              llvm::dbgs() << "; action = cut_optimize"
                           << "; step = merge_cut_idx"
                           << "; left_range = " << start_cut_idx << "-"
                           << cut_idx << "; right_range = " << cut_idx + 1
                           << "-" << end_cut_idx << "; group_idx = " << i
                           << "\n";
            });

            // 移除当前切割点（合并左右子组）
            cut_result.erase(cut_result.begin() + j);
            size_ = cut_result.size();  // 更新切割结果的大小
            take_effective = true;      // 标记有有效优化
            left_group_cost = combine_group_cost;  // 左子组成本更新为合并后成本
          } else {
            // 合并成本不优，移动到下一个切割点，左子组成本更新为当前右子组成本
            j++;
            left_group_cost = right_group_cost;
          }
        } else {
          // 合并后的子组无效，移动到下一个切割点，左子组成本更新为当前右子组成本
          j++;
          left_group_cost = right_group_cost;
        }
      }
    }
  }
  return take_effective;  // 返回是否有有效优化
}
```

### 9. `can_be_group_3d`：3D 组判定函数

功能：判断一组算子是否可划分为 “3D 组”（适用于 3D 卷积 / 池化等算子的分组）。

核心逻辑：

- 排除条件：组内若包含 `ConcatOp`，直接返回 `false`（Concat 会导致 3D 维度拼接，不支持 3D 并行切片）。
- 必要条件：组内至少包含一个 3D 算子（`Conv3DOp` 或 `Pool3DOp`），否则返回 `false`。

总结：3D 组的核心是 “不含 Concat 且包含 3D 算子”，确保 3D 空间维度（D/H/W）可并行切片。

```cpp
// 判断一组操作是否可划分为3D组（含3D算子的组）
// 3D组通常包含3D卷积或3D池化等算子，需满足特定约束
static bool can_be_group_3d(std::vector<Operation *> &group_ops) {
  // 约束1：组内不能包含ConcatOp（Concat算子会导致3D维度拼接，不支持3D分组）
  for (auto op : group_ops) {
    if (isa<ConcatOp>(op)) {
      return false;
    }
  }
  // 约束2：组内至少包含一个3D算子（3D卷积或3D池化）
  for (auto op : group_ops) {
    if (isa<Conv3DOp, Pool3DOp>(op)) {
      return true;
    }
  }
  // 不满足3D组条件
  return false;
}
```

### 10. `can_be_group_small_c`：小 C 组判定函数

功能：判断一组算子是否可划分为 “小 C 组”（适用于通道数较小的算子，通过空间维度切片提高 NPU 利用率）。

核心逻辑：

1. 运行模式限制：动态模式（`TPU_DYNAMIC`）不支持，直接返回 `false`。
2. 算子类型限制：仅允许特定类型（如 `ActiveOp`、`AddOp`、`LayerNormOp` 等），其他类型直接排除。
3. 维度与属性约束：

   - `ReshapeOp`：输入输出维度 ≤5，且前两维（N/C）必须相同（保证通道维度不变）。
   - `LayerNormOp`/`SoftmaxOp`：轴必须为最后一个维度（避免跨通道依赖，确保切片可行性）。
   - `AddOp`：两个输入形状必须相同（保证元素级操作可并行）。
   - `MatMulOp`：`hdim_is_batch` 必须为 `false`（高维不作为 batch 时才支持）。
4. 形状适配条件：

   - 若 4D 张量的 `N*C*H` 或 5D 张量的 `N*C*D*H` 可被 NPU 数整除，则无需小 C 分组（直接按现有维度切片即可）。
   - 否则需满足 “空间维度（H）> 通道数（C）且 C<NPU 数的一半”（确保按 H 切片比按 C 切片更高效）。

总结：小 C 组针对通道数较小的场景，通过约束算子类型和维度关系，确保按空间维度切片能充分利用 NPU 资源。

```java
// 判断一组操作是否可划分为小C组（通道数较小的算子组）
// 小C组适用于通道数较小、适合按空间维度（H/W）切片的算子，以提高NPU利用率
static bool can_be_group_small_c(std::vector<Operation *> &group_ops) {
  // 获取运行模式（动态模式不支持小C分组）
  auto ranmode = getRunMode(group_ops[0]);
  if (ranmode == RunMode::TPU_DYNAMIC) {
    return false;
  }
  // 遍历组内所有算子，检查是否满足小C组的约束
  for (auto op : group_ops) {
    // 约束1：算子类型必须是支持小C分组的类型（如激活、加法、层归一化等）
    if (!isa<ActiveOp, AddOp, CastOp, LayerNormOp, MulConstOp, MatMulOp,
             SoftmaxOp, RMSNormOp, ReshapeOp, LutOp>(op)) {
      return false;
    }
    // 约束2：Reshape算子的输入输出维度限制（最多5维，且前两维N/C必须保持一致）
    if (isa<ReshapeOp>(op)) {
      auto ishape = module::getShape(op->getOperand(0));  // 输入形状
      auto oshape = module::getShape(op->getResult(0));   // 输出形状
      if (ishape.size() > 5 || oshape.size() > 5) {       // 超过5维不支持
        return false;
      }
    }
    // 获取当前算子输入的形状（用于后续维度检查）
    auto shape = module::getShape(op->getOperand(0));
    // 约束3：LayerNorm的轴必须是最后一个维度（避免跨通道依赖影响切片）
    if (auto op_ = dyn_cast<LayerNormOp>(op)) {
      if (op_.getAxis() != shape.size() - 1) {
        return false;
      }
    }
    // 约束4：AddOp的两个输入形状必须相同（确保元素级加法可并行）
    else if (isa<AddOp>(op)) {
      auto shapeB = module::getShape(op->getOperand(1));
      if (shape != shapeB) {
        return false;
      }
    }
    // 约束5：Softmax的轴必须是最后一个维度（避免跨通道依赖）
    else if (auto op_ = dyn_cast<SoftmaxOp>(op)) {
      if (op_.getAxis() != shape.size() - 1) {
        return false;
      }
    }
    // 约束6：MatMul的hdim_is_batch必须为false（高维不作为batch时才支持）
    else if (auto op_ = dyn_cast<MatMulOp>(op)) {
      auto hdim_is_batch = op_.getHdimIsBatch();
      if (hdim_is_batch) {
        return false;
      }
    }
    // 约束7：Reshape算子需保证前两维（N/C）不变，且排除可按N*C*H/D切片的情况
    else if (auto op_ = dyn_cast<ReshapeOp>(op)) {
      auto ishape = module::getShape(op_.getInput());
      auto oshape = module::getShape(op_.getOutput());
      // 前两维（N/C）必须相同，且维度数>2
      if (!(ishape.size() > 2 && oshape.size() > 2 && ishape[0] == oshape[0] &&
            ishape[1] == oshape[1])) {
        return false;
      }
      // 若4D张量的N*C*H或5D张量的N*C*D*H可被NPU数整除，则无需小C分组
      if ((shape.size() == 4 &&
           shape[0] * shape[1] * shape[2] % Arch::NPU_NUM == 0) ||
          (shape.size() == 5 &&
           shape[0] * shape[1] * shape[2] * shape[3] % Arch::NPU_NUM == 0)) {
        return false;
      }
    }
    // 约束8：检查张量是否适合小C分组的维度条件
    // 情况1：4D张量N*C*H或5D张量N*C*D*H可被NPU数整除（无需小C分组，直接继续）
    if ((shape.size() == 4 &&
         shape[0] * shape[1] * shape[2] % Arch::NPU_NUM == 0) ||
        (shape.size() == 5 &&
         shape[0] * shape[1] * shape[2] * shape[3] % Arch::NPU_NUM == 0)) {
      continue;
    }
    // 情况2：3D张量N>4且C=197（特殊场景，允许小C分组）
    if ((shape.size() == 3 && shape[0] > 4 && shape[1] == 197)) {
      continue;
    }

    // 情况3：通用小C条件（空间维度>H>通道数C，且C<NPU数的一半）
    // 确保按H维度切片比按C维度更高效（充分利用NPU资源）
    if (!(((shape.size() == 5 && shape[3] > shape[1]) ||  // 5D中H>C
           (shape.size() == 4 && shape[2] > shape[1])) && // 4D中H>C
          shape[1] < Arch::NPU_NUM / 2)) {               // C<NPU_NUM/2
      return false;
    }
  }
  // 所有算子均满足小C组条件
  return true;
}
```

### 11. `can_be_group_mm`：矩阵乘法组（MM 组）判定函数

功能：判断一组算子是否可划分为 “MM 组”（适用于矩阵乘法、注意力机制等算子的分组）。

核心逻辑：

1. 芯片型号限制：`MARS3` 和 `SGTPUV8` 芯片不支持 MM 分组，直接返回 `false`。
2. 算子类型限制：仅允许特定类型（如 `MatMulOp`、`AttentionOp`、`LayerNormOp` 等），其他类型直接排除。
3. 维度与属性约束：

   - `LayerNormOp`/`SoftmaxOp`：轴必须为最后一个维度（避免跨维度依赖）。
   - `ReshapeOp`：输入输出前两维（N/C）必须相同，且维度数 > 2（保证矩阵结构不变）。
   - `MatMulOp`：不能同时进行左右转置（避免复杂维度转换影响并行效率）。
   - `AttentionOp`：`Keys` 不能为 `None`（确保注意力机制的正常计算流程）。

总结：MM 组针对矩阵运算场景，通过限制芯片型号、算子类型和关键属性，确保矩阵维度兼容性和并行可行性。

```java
// 判断一组操作是否可划分为矩阵乘法组（MM组，含MatMul或Attention等算子）
// MM组适用于矩阵乘法、注意力机制等算子，需满足特定的维度和类型约束
static bool can_be_group_mm(std::vector<Operation *> &group_ops) {
  // 约束1：MARS3和SGTPUV8芯片不支持MM分组
  if (module::isMARS3() || module::isSGTPUV8())
    return false;
  // 遍历组内所有算子，检查是否满足MM组的约束
  for (auto op : group_ops) {
    // 约束2：算子类型必须是支持MM分组的类型（如矩阵乘、注意力、激活等）
    if (!isa<ActiveOp, AddOp, CastOp, LayerNormOp, MulConstOp, MatMulOp, MulOp,
             ReshapeOp, SoftmaxOp, AttentionOp, RMSNormOp, MulShiftOp, WhereOp,
             BatchNormBwdOp, LutOp, BinaryConstShiftOp, BinaryShiftOp>(op)) {
      return false;
    }
    // 获取当前算子输入的形状（用于后续维度检查）
    auto shape = module::getShape(op->getOperand(0));
    // 约束3：LayerNorm的轴必须是最后一个维度（避免跨维度依赖）
    if (auto op_ = dyn_cast<LayerNormOp>(op)) {
      if (op_.getAxis() != shape.size() - 1) {
        return false;
      }
    }
    // 约束4：Reshape算子需保证前两维（N/C）不变，且维度数>2（确保矩阵结构不变）
    else if (auto op_ = dyn_cast<ReshapeOp>(op)) {
      auto ishape = module::getShape(op_.getInput());
      auto oshape = module::getShape(op_.getOutput());
      if (!(ishape.size() > 2 && oshape.size() > 2 && ishape[0] == oshape[0] &&
            ishape[1] == oshape[1])) {
        return false;
      }
    }
    // 约束5：Softmax的轴必须是最后一个维度（避免跨维度依赖）
    else if (auto op_ = dyn_cast<SoftmaxOp>(op)) {
      if (op_.getAxis() != shape.size() - 1) {
        return false;
      }
    }
    // 约束6：MatMul不能同时进行左右转置（避免复杂维度转换影响并行）
    else if (auto op_ = dyn_cast<MatMulOp>(op)) {
      auto left_trans = op_.getLeftTranspose();
      auto right_trans = op_.getRightTranspose();
      if (left_trans && right_trans) {
        return false;
      }
    }
    // 约束7：Attention算子的Keys不能为None（确保注意力机制正常计算）
    else if (auto op_ = dyn_cast<AttentionOp>(op)) {
      if (module::isNone(op_.getKeys())) {
        return false;
      }
    }
  }
  // 所有算子均满足MM组条件
  return true;
}
```

## 3./LayerGroup/TimeStepMethod.cpp

### 1.TimeStepMethod::process

##### 核心功能

该函数是时间步（`TimeStep`）处理的核心逻辑，负责根据层组信息和形状分段，完成张量分片计算、张量信息更新、时间步分配及内存优化配置，最终为层组的执行提供时间步相关的调度依据。

##### 执行流程与关键函数解析

整个流程分为 4 个关键步骤，各步骤及涉及函数的功能如下：

##### 关键参数的意义

- `gen_idx`：控制张量分片的精细度。`true` 用于实际执行阶段（需要具体分片索引），`false` 用于预分析阶段（仅需最大分片范围评估资源）。
- `shape_secs`：形状分段信息，定义了张量在不同维度上的分段规则（如分块大小、重叠区域），是张量分片的核心依据。
- `lg_info`：提供层组的操作集合、输入输出形状等关键信息，确保分片和时间步分配与层组的计算需求匹配。

##### 设计原理

- 张量分片优化：通过 “条带挖掘” 技术将大张量划分为小分片，适配硬件的并行处理能力（如 TPU 的核间并行），减少单次计算的内存占用。
- 内存感知调度：`memory_aware_timestep_assignment` 避免时间步分配导致的内存瓶颈（如同时访问过多大张量导致 lmem 溢出），通过合理排序提升内存利用率。
- 本地内存复用：`gen_hold_coeff` 识别需要频繁访问的张量，将其保存在 lmem 中，减少全局内存（GMEM）的访问次数，降低延迟。

##### 代码实现

```javascript
/**
 * 处理时间步（TimeStep）相关配置，包括张量分片、时间步分配及内存优化
 * @param time_step 时间步对象，用于存储时间步分配结果及相关配置
 * @param tensor_infos 张量信息集合，用于存储张量的分片、标签等元数据
 * @param lg_info 层组信息，包含当前层组的操作、形状等关键信息
 * @param shape_secs 形状分段信息，用于指导张量的分片策略
 * @param gen_idx 是否生成索引：true表示生成具体分片索引，false表示仅计算最大分片
 * @return 处理成功返回true，失败返回false
 */
bool TimeStepMethod::process(BasicTimeStep *time_step, TensorInfo &tensor_infos,
                             const LgInfo &lg_info,
                             const shape_secs_t &shape_secs, bool gen_idx) {
  // 反向更新张量分片（根据是否生成索引选择不同的分片策略）
  if (gen_idx) {
    // 调试日志：标记开始生成分片索引并反向更新张量分片信息
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                          "stripe_mine_idx_slice", "call_function",
                          "backward and update slice_info of tensors starting "
                          "from output tensors "
                          "according to shape_secs, store the idx and slice of "
                          "each tile in `tensor_infos`")
                   << "\n";
    });
    // 调用stripe_mine_idx_slice：从输出张量反向推导，生成具体分片索引并更新tensor_infos
    if (stripe_mine_idx_slice(lg_info, shape_secs, tensor_infos, options_) ==
        false) {
      return false; // 分片处理失败，返回false
    }
  } else {
    // 调用stripe_mine_max_slice：计算最大可能的分片范围（不生成具体索引）
    if (stripe_mine_max_slice(lg_info, shape_secs, tensor_infos, options_) ==
        false) {
      return false; // 最大分片计算失败，返回false
    }
  }

  // 更新张量信息（设置标签和分片详情）
  GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "update_tensor_infos", "call_function",
                        "set tags and slice_info for specific tensors")
                 << "\n";
  });
  update_tensor_infos(lg_info, tensor_infos, shape_secs);

  // 内存感知的时间步分配（优化时间步调度以提升性能）
  GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "memory_aware_timestep_assignment", "call_function",
                        "assign timesteps and try to optimize the performance "
                        "by moving timesteps")
                 << "\n";
  });
  // 注释掉的备选函数：基于层邻近性的时间步分配
  // layer_nearest_timestep_assignment(time_step, tensor_infos, lg_info);
  // 实际调用：考虑内存使用情况的时间步分配（优先优化内存访问效率）
  memory_aware_timestep_assignment(time_step, tensor_infos, lg_info);

  // 生成张量保持系数（决定哪些张量需要保存在本地内存以提升性能）
  GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "gen_hold_coeff", "call_function",
                        "use map hold_coeff_ to store whether we needs the "
                        "tensor to hold in lmem"
                        "for better performance")
                 << "\n";
  });
  time_step->gen_hold_coeff(); // 生成并存储保持系数（hold_coeff_）

  return true; // 所有处理成功完成
}
```

### 2.memory_aware_timestep_assignment

##### 核心功能

该函数是一个内存感知的时间步分配优化器，主要用于在深度学习或高性能计算场景中，优化张量（Tensor）在不同时间步（Time Step）中的分配策略，以平衡内存负载、减少内存冲突，提升整体计算效率。

##### 逻辑流程

1. 初步分配：先调用 `layer_nearest_timestep_assignment`（见 3.3）执行基础的时间步分配（可能基于层的就近原则）。
2. 前置检查：
   - 若层组操作数（`group_ops.size()`）≤1，说明计算任务简单，无需优化，直接返回。
   - 若时间步总数为 0，无分配对象，直接返回。
3. 数据结构初始化：
   - `timestep_cycle_slack`：记录每个时间步的 "周期松弛量"（可理解为时间步的空闲程度，负值表示负载紧张）。
   - `tensor_to_cycle`/`tensor_to_bufsize`：映射张量到其所属周期和缓冲区大小，用于内存计算。
   - `tensor_timesteps`：记录每个时间步包含的张量列表，是优化的核心操作对象。
4. 关键参数计算：
   - 调用 `get_timestep_cycle_slack`（见 3.4）计算上述数据结构的初始值（通过 OpenMP 临界区保证多线程安全）。
5. 优化迭代：
   - 遍历所有时间步，对负载紧张（`cur_slack < 0`）的时间步进行优化。
   - 调用 `get_best_ts`（见 3.5）为当前紧张的时间步寻找最佳迁移目标时间步（空闲度高、适合接收张量）。
   - 若找到目标，调用 `bubble_tensor_to_best_ts`（见 3.6）将张量迁移，并更新相关映射和松弛量。
6. 结果更新与日志：
   - 将优化后的张量分配结果更新到 `time_step` 对象中。
   - 输出调试日志，打印优化后的时间步分配表。

##### 核心原理

- 内存感知：通过 `tensor_to_bufsize` 跟踪张量的内存占用，结合 `timestep_cycle_slack` 评估时间步的内存负载，避免某一时间步内存过度拥挤。
- 负载均衡：核心思想是 "削峰填谷"—— 将负载紧张（松弛量为负）的时间步中的张量迁移到负载宽松（松弛量较高）的时间步，平衡各时间步的内存压力。
- 局部优化：通过 `bubble_tensor_to_best_ts` 实现张量的 "冒泡式" 迁移，确保迁移过程中对整体分配的影响最小化，同时更新相关元数据维持一致性。

##### memory_aware_timestep_assignment 代码

```cpp
/**
 * @brief 基于内存感知的时间步分配优化方法
 * @details 该方法在初步时间步分配的基础上，根据内存周期松弛量和缓冲区大小等信息，
 *          优化张量在时间步中的分配，以提升内存使用效率和计算性能
 * @param time_step 时间步对象，存储张量的时间步分配信息
 * @param tensor_infos 张量信息集合，包含张量的属性和元数据
 * @param lg_info 层组信息，包含组操作等相关配置
 */
void TimeStepMethod::memory_aware_timestep_assignment(BasicTimeStep *time_step,
                                                      TensorInfo &tensor_infos,
                                                      const LgInfo &lg_info) {
  // 第一步：执行基础的层最近时间步分配（初步分配策略）
  layer_nearest_timestep_assignment(time_step, tensor_infos, lg_info);
  
  // 若层组操作数<=1，无需进一步优化，直接返回
  if (lg_info.group_ops.size() <= 1) {
    return;
  }
  
  // 获取时间步总数
  int64_t timestep_num = time_step->get_timestep_num();
  // 若没有时间步，直接返回
  if (timestep_num == 0) {
    return;
  }
  
  // 存储每个时间步的周期松弛量（可理解为时间步的"空闲度"，负值表示负载紧张）
  std::vector<int64_t> timestep_cycle_slack(timestep_num, 0);
  // 张量到其所属周期的映射
  ValueIntMap tensor_to_cycle;
  // 张量到其缓冲区大小的映射
  ValueIntMap tensor_to_bufsize;
  // 存储每个时间步包含的张量元素列表（GdmaElt为张量传输/存储相关元素）
  std::vector<std::list<GdmaElt>> tensor_timesteps;

  // 调试日志：打印内存感知算法开始标志（仅在调试模式且类型匹配时输出）
  GROUP_DEBUG_WITH_TYPE("timestep_assign", lg_info, [&]() {
    llvm::dbgs() << "============= memory aware algorithm =============\n";
  });

  // 多线程临界区：计算时间步的周期松弛量及张量相关映射（避免多线程冲突）
  // 注：后续需移除该临界区，待pid_node模块提取后优化
#pragma omp critical(get_cycle)
  get_timestep_cycle_slack(time_step, lg_info, tensor_to_cycle,
                           tensor_to_bufsize, tensor_timesteps,
                           timestep_cycle_slack);

  // 用于存储选中的张量列表迭代器
  std::list<GdmaElt>::iterator sel_list_iter;
  // 最佳目标时间步
  int64_t best_ts = 0;
  
  // 遍历所有时间步，优化负载紧张的时间步
  for (int64_t cur_ts = 0; cur_ts < timestep_num;) {
    // 当前时间步的周期松弛量
    int64_t cur_slack = timestep_cycle_slack[cur_ts];
    
    // 若松弛量>=0，说明当前时间步负载适中，无需优化，继续下一个
    if (cur_slack >= 0) {
      ++cur_ts;
      continue;
    }

    // 为当前负载紧张的时间步（cur_slack<0）寻找最佳迁移目标时间步
    best_ts = get_best_ts(time_step, lg_info, cur_ts, tensor_to_cycle,
                          tensor_to_bufsize, tensor_timesteps,
                          timestep_cycle_slack, sel_list_iter);
    
    // 若未找到合适的目标时间步，继续下一个时间步
    if (best_ts == -1) {
      ++cur_ts;
      continue;
    }

    // 将选中的张量从当前时间步"冒泡"迁移到最佳目标时间步，
    // 并更新相关映射和松弛量
    bubble_tensor_to_best_ts(sel_list_iter, cur_ts, best_ts, time_step,
                             tensor_to_cycle, tensor_to_bufsize,
                             tensor_timesteps, timestep_cycle_slack);
  }

  // 优化完成后，更新时间步对象中的gdma字段（张量传输相关配置）
  for (size_t ts = 0; ts < tensor_timesteps.size(); ++ts) {
    GdmaTsField new_tensor_timestep;
    // 将优化后的张量列表写入新的时间步字段
    for (auto &iter : tensor_timesteps[ts]) {
      new_tensor_timestep.push_back(std::move(iter));
    }
    // 更新时间步的gdma0字段
    time_step->update_gdma0_ts_field(ts, new_tensor_timestep);
  }

  // 调试日志：打印优化结果信息（仅在调试模式且类型匹配时输出）
  GROUP_DEBUG_WITH_TYPE("timestep_assign", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "timestep_assign", "final_result",
                        "optimize timesteps using algorithm based on cycle "
                        "slack and buffer area")
                 << "\n============= timestep optimized =============\n";
    // 打印优化后的时间步表
    time_step->show_timestep_table();
  });

  // 调试日志：打印算法结束标志（仅在调试模式且类型匹配时输出）
  GROUP_DEBUG_WITH_TYPE("timestep_assign", lg_info, [&]() {
    llvm::dbgs() << "=======================================\n";
  });
}
```

### 3.layer_nearest_timestep_assignment

##### 核心功能

该函数是时间步分配的基础算法，基于 "层最近邻" 策略为层组（一组相关操作，如神经网络中的连续层）中的操作分配时间步。核心目标是：

- 将 TPU 计算操作（如矩阵乘法）与 GDMA 内存操作（加载 / 存储张量）合理绑定到时间步；
- 通过预加载下一层输入、延迟存储上一层输出等策略实现初步流水线，为后续优化（如内存感知优化）提供基础。

##### 逻辑流程

1. 初始化与变量定义：
   - 获取层组操作列表（`group_ops`），定义存储 TPU 计算（`tpu_field`）和 GDMA 内存操作（`gdma_field`）的结构；
   - 用 `tensor_in_lmem` 记录已加载到本地内存（LMEM）的张量，避免重复加载。
2. 遍历操作分配时间步（核心循环，按操作顺序 `i` 遍历）：
   - 第一个操作（i=0）：仅执行输入加载（无计算），将当前操作需要的输入张量从外部内存加载到 LMEM，确保计算时数据就绪。
   - 中间操作（0 < i < 最后一个索引）：
     - 记录当前操作的 TPU 计算任务（`tpu_field`）；
     - 预加载下一层输入：提前加载下一个操作需要的输入张量（流水线优化，避免计算等待数据）；
     - 存储上一层输出：将上一个操作的输出张量（若为最终结果）从 LMEM 存储到外部内存（释放 LMEM 空间）。
   - 最后一个操作（i = 最后一个索引）：计算完成后，将所有输出张量从 LMEM 存储到外部内存（确保结果留存）。
3. 结果整理与优化：
   - 将每个时间步的 TPU/GDMA 操作添加到 `time_step` 对象；
   - 若操作数 > 1，调用 `software_pipeline()` 进行软件流水线调整（进一步重叠不同操作的执行）；
   - 输出调试日志，展示分配结果。

##### 核心原理

- 最近邻策略：按操作顺序（层的先后顺序）分配时间步，每个操作绑定到与其 "最近" 的时间步，避免跨时间步的冗余数据传输。
- 流水线优化：通过 "预加载下一层输入" 和 "延迟存储上一层输出"，实现 "当前层计算时，下一层数据已加载、上一层结果已存储" 的重叠执行，减少空闲时间。
- 内存高效利用：用 `tensor_in_lmem` 跟踪 LMEM 中的张量，避免重复加载（减少内存带宽消耗），及时存储不再需要的张量（释放 LMEM 空间）。

##### layer_nearest_timestep_assignment 代码

```cpp
/**
 * @brief 基于"层最近邻"策略的时间步分配方法
 * @details 该方法为层组中的操作分配时间步，将TPU计算操作与GDMA（内存访问）操作合理安排，
 *          并通过预加载、延迟存储等策略实现初步的流水线优化，为后续内存感知优化奠定基础
 * @param time_step 时间步对象，用于存储操作的时间步分配结果
 * @param tensor_infos 张量信息集合，包含张量的属性（如存储模式、大小等）
 * @param lg_info 层组信息，包含组内操作列表、输入输出张量等配置
 */
void TimeStepMethod::layer_nearest_timestep_assignment(BasicTimeStep *time_step,
                                                       TensorInfo &tensor_infos,
                                                       const LgInfo &lg_info) {
  // 获取层组中的所有操作（如神经网络层的计算操作）
  const std::vector<Operation *> &group_ops = lg_info.group_ops;
  // 标记是否存在需要加载的张量
  bool have_load_tensor;
  // 存储当前时间步的TPU计算操作
  TpuTsField tpu_field;
  // 存储当前时间步的GDMA（内存访问）操作（加载/存储张量）
  GdmaTsField gdma_field;
  // 记录已加载到本地内存（LMEM）中的张量（避免重复加载）
  std::set<Value, value_compare> tensor_in_lmem;

  Operation *op;               // 当前处理的操作
  tensor_info_t tensor_info;   // 张量的详细信息（模式、大小等）

  // 最近邻算法核心：为每个操作分配时间步（按操作顺序依次处理）
  for (size_t i = 0; i < group_ops.size(); ++i) {
    op = group_ops[i];  // 获取当前操作

    // 调试日志：打印当前时间步分配动作（仅在调试模式下输出）
    DEBUG_WITH_TYPE("timestep_assign", {
      llvm::dbgs() << "; action = layer_nearest_timestep_assignment"
                   << "; ts = " << i << "\n";
    });

    // 处理第一个操作（i=0）：初始化阶段，仅加载输入张量
    if (i == 0) {
      // 阶段0：仅包含加载时间步（无计算，先把输入加载到本地内存）
      gdma_field.clear();       // 清空GDMA操作列表
      have_load_tensor = false; // 初始化为无需要加载的张量

      // 遍历当前操作的所有输入张量
      for (auto in : op->getOperands()) {
        // 跳过空类型的输入（无效输入）
        if (in.getType().isa<NoneType>()) {
          continue;
        }

        // 若张量未在本地内存中，则需要加载
        if (tensor_in_lmem.count(in) == 0) {
          // 从张量信息集合中获取该张量的详细信息
          auto iter = tensor_infos.find(in);
          if (iter != tensor_infos.end()) {
            tensor_info = iter->second;
          }
          tensor_info.mode = TIMESTEP_LOAD;  // 标记为"加载"模式
          gdma_field.push_back(std::make_pair(in, tensor_info));  // 添加到GDMA操作
          have_load_tensor = true;  // 标记存在需要加载的张量
          tensor_in_lmem.insert(in);  // 记录该张量已在本地内存
        }
      }

      // 若有需要加载的张量，将该GDMA操作添加到时间步
      if (have_load_tensor) {
        time_step->add_gdma0_ts_field(gdma_field);
      }
    }

    // 清空当前时间步的TPU和GDMA操作列表（准备记录新操作）
    tpu_field.clear();
    gdma_field.clear();

    // 阶段1：流水线核心——计算、预加载、存储操作在同一时间步
    // 将当前操作的输出张量加入本地内存（后续操作可能会用到）
    for (auto out : get_output_values(op)) {
      tensor_in_lmem.insert(out);
    }

    // 阶段1.1：添加当前操作的TPU计算任务
    tpu_field.push_back(op);

    // 若不是最后一个操作（i < 最后一个索引），预加载下一层的输入张量
    if (i != group_ops.size() - 1) {
      auto next_op = group_ops[i + 1];  // 获取下一个操作
      // 遍历下一个操作的所有输入张量
      for (auto next_in : next_op->getOperands()) {
        // 跳过空类型输入
        if (next_in.getType().isa<NoneType>()) {
          continue;
        }

        // 若下一层的输入未在本地内存中，则提前加载（流水线优化）
        if (tensor_in_lmem.count(next_in) == 0) {
          auto iter = tensor_infos.find(next_in);
          if (iter != tensor_infos.end()) {
            tensor_info = iter->second;
          }
          tensor_info.mode = TIMESTEP_LOAD;  // 标记为"加载"模式
          gdma_field.push_back(std::make_pair(next_in, tensor_info));  // 添加到GDMA操作
          tensor_in_lmem.insert(next_in);  // 记录已加载到本地内存
        }
      }
    }

    // 若不是第一个操作（i > 0），存储前一层的输出张量（释放本地内存）
    if (i > 0) {
      auto pre_op = group_ops[i - 1];  // 获取前一个操作
      // 遍历前一个操作的所有输出张量
      for (auto pre_out : get_output_values(pre_op)) {
        // 若该输出是层组的最终输出之一，则需要存储（非中间临时变量）
        if (std::find(lg_info.group_outs.begin(), lg_info.group_outs.end(),
                      pre_out) != lg_info.group_outs.end()) {
          tensor_info = tensor_infos[pre_out];
          tensor_info.mode = TIMESTEP_STORE;  // 标记为"存储"模式
          gdma_field.push_back(std::make_pair(pre_out, tensor_info));  // 添加到GDMA操作
        }
      }
    }

    // 将当前时间步的TPU和GDMA操作添加到时间步对象（非空时才添加）
    if (!(tpu_field.empty() && gdma_field.empty())) {
      time_step->add_tpu0_gdma0_ts_field(tpu_field, gdma_field);
    }

    // 处理最后一个操作（i = 最后一个索引）：存储最终输出张量
    if (i == group_ops.size() - 1) {
      gdma_field.clear();  // 清空GDMA操作列表
      // 遍历最后一个操作的所有输出张量
      for (auto out : get_output_values(op)) {
        tensor_info = tensor_infos[out];
        tensor_info.mode = TIMESTEP_STORE;  // 标记为"存储"模式
        gdma_field.push_back(std::make_pair(out, tensor_info));  // 添加到GDMA操作
      }
      // 将存储操作添加到时间步
      time_step->add_gdma0_ts_field(gdma_field);
    }
  }

  // 调试日志：打印基于最近邻算法的初步分配结果（仅调试模式且类型匹配时输出）
  GROUP_DEBUG_WITH_TYPE("timestep_assign", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "timestep_assign", "intermediate_result",
                        "using nearest algorithm to assign timestep for tpu "
                        "and gdma operations firstly")
                 << "\n============= nearest algorithm =============\n";
    time_step->show_timestep_table();  // 展示时间步分配表
  });

  // 若操作数大于1，启用软件流水线优化（进一步提升并行性）
  if (group_ops.size() > 1) {
    time_step->software_pipeline();
  }

  // 调试日志：打印软件流水线调整后的结果（仅调试模式且类型匹配时输出）
  GROUP_DEBUG_WITH_TYPE("timestep_assign", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "timestep_assign", "intermediate_result",
                        "adust timesteps considering the software pipeline ")
                 << "\n============= software pipeline =============\n";
    time_step->show_timestep_table();  // 展示调整后的时间步分配表
  });
}
```

### 4.get_timestep_cycle_slack

##### 1. 核心功能

该函数是内存感知时间步优化的前置计算模块，核心功能是：

- 计算每个时间步的 "周期松弛量"（`timestep_cycle_slack`），即该时间步内计算操作（层）的总周期与内存操作（GDMA）的总周期的差值；
- 收集张量的关键属性：GDMA 操作周期（`tensor_to_cycle`）、缓冲区大小（`tensor_to_bufsize`）；
- 整理每个时间步包含的张量列表（`tensor_timesteps`）。

这些数据是后续 `memory_aware_timestep_assignment` 函数进行负载均衡（迁移张量）的核心依据。

##### 2. 逻辑流程

1. 初始化：

   - 获取时间步总数，清空时间步中已有的 GDMA 周期和层周期记录（避免历史数据干扰）；
   - 定义临时变量 `tensor_cycle`（张量 GDMA 周期）和 `buffer_size`（张量缓冲区大小）。
2. 遍历时间步计算周期松弛量（核心循环）：

   - 处理计算层（操作）：
     - 对每个时间步的层（`ts_layers`），调用 `cycle_calculator_->getLocalLayerCycle` 计算层的本地计算周期（即该层在当前时间步贡献的计算耗时）；
     - 将计算周期累加到该时间步的 `timestep_cycle_slack`（松弛量初始累加计算耗时）；
     - 记录层的计算周期到 `time_step` 对象中。
   - 处理张量（GDMA 操作）：
     - 对每个时间步的张量（`ts_tensors`），调用 `cycle_calculator_->getGdmaCycle` 计算张量的 GDMA 操作周期（内存传输耗时），调用 `get_buffer_size` 计算缓冲区大小；
     - 将张量的 GDMA 周期和缓冲区大小分别存入 `tensor_to_cycle` 和 `tensor_to_bufsize` 映射；
     - 将张量添加到当前时间步的列表 `list_tensors` 中；
     - 从该时间步的 `timestep_cycle_slack` 中减去 GDMA 周期（松弛量最终为 "计算周期 - 内存周期"）；
     - 记录张量的 GDMA 周期到 `time_step` 对象中。
   - 整理张量列表：将当前时间步的张量列表存入 `tensor_timesteps`。

##### 3. 核心原理

- 周期松弛量的物理意义：`timestep_cycle_slack[ts]` 表示时间步 `ts` 的 "净空闲周期"：

  - 若为正值：该时间步的计算周期 > 内存周期，存在空闲资源（可接收其他时间步迁移的张量）；
  - 若为负值：该时间步的计算周期 < 内存周期，负载紧张（需要迁移张量到其他时间步）。
- 张量属性的作用：

  - `tensor_to_cycle`：迁移张量时需考虑其 GDMA 周期对目标时间步松弛量的影响（迁移后目标时间步的松弛量会减少该周期）；
  - `tensor_to_bufsize`：确保迁移后目标时间步的内存总占用不超过上限（避免内存溢出）；
  - `tensor_timesteps`：提供每个时间步的张量明细，为后续迁移操作（如 `bubble_tensor_to_best_ts`）提供操作对象。
- 计算逻辑的合理性：通过分离计算周期（层操作）和内存周期（GDMA 操作），量化时间步的负载差异，为后续负载均衡提供可量化的优化目标（将负值松弛量的时间步张量迁移到正值松弛量的时间步）。

##### 4. get_timestep_cycle_slack 代码

```cpp
/**
 * @brief 计算时间步的周期松弛量及张量相关属性（周期、缓冲区大小等）
 * @details 该函数通过计算每个时间步中计算操作（层）的周期和GDMA内存操作（张量）的周期，
 *          得到时间步的"周期松弛量"（计算周期与内存操作周期的差值），同时收集张量的周期、
 *          缓冲区大小及时间步对应的张量列表，为后续内存感知优化提供核心数据
 * @param time_step 时间步对象，存储时间步的层和张量信息
 * @param lg_info 层组信息，包含层组类型等配置
 * @param tensor_to_cycle 输出参数，存储张量到其GDMA操作周期的映射
 * @param tensor_to_bufsize 输出参数，存储张量到其缓冲区大小的映射
 * @param tensor_timesteps 输出参数，存储每个时间步包含的张量列表（GdmaElt为张量及其信息）
 * @param timestep_cycle_slack 输出参数，存储每个时间步的周期松弛量（计算周期 - 内存操作周期）
 */
void TimeStepMethod::get_timestep_cycle_slack(
    BasicTimeStep *time_step, const LgInfo &lg_info,
    ValueIntMap &tensor_to_cycle, ValueIntMap &tensor_to_bufsize,
    std::vector<std::list<GdmaElt>> &tensor_timesteps,
    std::vector<int64_t> &timestep_cycle_slack) {
  // 获取时间步总数
  int64_t timestep_num = time_step->get_timestep_num();
  // 获取时间步中存储的所有张量信息（属性、模式等）
  auto &tensor_infos = time_step->get_tensor_infos();
  // 清空时间步中已有的GDMA操作周期记录（重新计算前初始化）
  time_step->clear_gdma_cycle();
  // 清空时间步中已有的层计算周期记录（重新计算前初始化）
  time_step->clear_layer_cycle();

  int64_t tensor_cycle = 0;       // 单个张量的GDMA操作周期（内存传输耗时）
  int64_t buffer_size = 0;        // 单个张量的缓冲区大小（内存占用）

  // 遍历每个时间步，计算周期松弛量及张量属性
  for (int64_t ts = 0; ts < timestep_num; ++ts) {
    // 处理当前时间步的计算层（操作）：累加计算周期到松弛量
    const auto &ts_layers = time_step->getLayers(ts);  // 获取当前时间步的所有层（操作）
    for (auto op : ts_layers) {  // 遍历每个层（操作）
      // 计算该层的本地计算周期（松弛量）：考虑张量信息、层组类型，且启用优化
      int64_t cycle_slack = cycle_calculator_->getLocalLayerCycle(
          op, tensor_infos, lg_info.type, true);
      // 将该层的计算周期累加到当前时间步的总松弛量中
      timestep_cycle_slack[ts] += cycle_slack;
      // 记录该层的计算周期到时间步对象中
      time_step->set_layer_cycle(op, cycle_slack);
    }

    // 处理当前时间步的张量（GDMA操作）：计算内存操作周期，更新松弛量
    std::list<GdmaElt> list_tensors;  // 存储当前时间步的所有张量（GdmaElt格式）
    auto &ts_tensors = time_step->getTensors(ts);  // 获取当前时间步的所有张量
    for (auto &tensor : ts_tensors) {  // 遍历每个张量
      auto v = tensor.first;    // 张量对象（Value类型）
      auto &ti = tensor.second; // 张量的详细信息（tensor_info_t）

      // 计算该张量的GDMA操作周期（内存传输耗时）
      tensor_cycle = cycle_calculator_->getGdmaCycle(v, ti, lg_info.type);
      // 计算该张量的缓冲区大小（内存占用）
      buffer_size = get_buffer_size(v, ti, lg_info.type);

      // 记录张量到其GDMA周期的映射
      tensor_to_cycle[v] = tensor_cycle;
      // 记录张量到其缓冲区大小的映射
      tensor_to_bufsize[v] = buffer_size;
      // 将张量添加到当前时间步的张量列表中
      list_tensors.push_back(tensor);
      // 从当前时间步的总松弛量中减去GDMA周期（松弛量 = 计算周期 - 内存周期）
      timestep_cycle_slack[ts] -= tensor_cycle;
      // 记录该张量的GDMA周期到时间步对象中
      time_step->set_gdma_cycle(tensor.first, tensor_cycle);
    }

    // 将当前时间步的张量列表存入tensor_timesteps（使用move避免拷贝）
    tensor_timesteps.push_back(std::move(list_tensors));
  }
}
```

### 5.get_best_ts

##### 核心功能

该函数是内存感知时间步优化的决策核心，用于为当前负载紧张（周期松弛量为负）的时间步（`cur_ts`）寻找最佳的目标时间步。通过评估将当前时间步的张量迁移到其他候选时间步的 "收益"，选择最优目标，以实现负载均衡（缓解当前时间步的紧张状态，同时不显著影响目标时间步的空闲状态）。

##### 逻辑流程

1. 初始化评估参数：定义源收益（`src_profit`）、目标成本（`dst_cost`）、总收益（`cur_profit`）等指标，用于量化迁移的优劣；初始化 `best_ts` 为 - 1（表示未找到合适目标）。
2. 遍历当前时间步的张量：对 `cur_ts` 中的每个张量，评估其迁移潜力：
   - 计算源收益：若迁移该张量，当前时间步的松弛量会增加（因移除了该张量的 GDMA 周期），`src_profit` 量化这种改善（值越大，源时间步负载缓解越明显）。
   - 确定候选目标时间步范围：通过 `get_next_ts` 获取该张量可迁移的目标时间步（受张量访问范围 `range_end` 和 GDMA 类型限制）。
3. 筛选有效目标时间步：
   - 跳过无效目标（如存储操作的张量在目标时间步被 NPU 访问，可能导致冲突）。
   - 仅考虑松弛量 > 0 的目标时间步（有空闲资源接收张量）。
4. 评估迁移收益与成本：
   - 目标成本（`dst_cost`）：量化目标时间步添加该张量后的负载变化（值越大，目标时间步受影响越小）。
   - 总收益（`cur_profit`）：`src_profit + dst_cost`，综合评估迁移的整体收益（越大越优）。
   - 内存成本（`cur_area`）：缓冲区大小 × 时间步距离，用于收益相同时的二次筛选（越小越优，减少内存开销）。
5. 选择最佳目标：记录总收益最大（或收益相同但内存成本最低）的目标时间步及对应的张量迭代器，最终返回 `best_ts`。

##### 核心原理

- 收益量化：通过周期松弛量的变化量化迁移价值 —— 源时间步的收益（`src_profit`）反映负载缓解程度，目标时间步的成本（`dst_cost`）反映对其空闲状态的影响，两者之和最大化可实现全局负载均衡。
- 约束控制：通过 `range_end` 和 `is_tensor_accessed_by_npu` 限制目标时间步范围，确保迁移不破坏张量的访问依赖（如存储操作不能迁移到被 NPU 访问的时间步，避免数据不一致）。
- 二次优化：当收益相同时，选择内存成本更低的迁移方案（`cur_area` 更小），在平衡负载的同时减少内存带宽消耗和迁移开销。

##### get_best_ts 代码

```java
/**
 * @brief 为当前负载紧张的时间步寻找最佳的目标时间步，用于迁移张量以平衡负载
 * @details 该函数遍历当前时间步的所有张量，评估将每个张量迁移到其他候选时间步的"收益"，
 *          选择收益最大（或收益相同时内存成本最低）的目标时间步，为后续张量迁移提供依据
 * @param time_step 时间步对象，存储时间步配置及张量信息
 * @param lg_info 层组信息，包含层组类型等配置
 * @param cur_ts 当前需要优化的时间步（负载紧张，松弛量为负）
 * @param tensor_to_cycle 张量到其GDMA操作周期的映射
 * @param tensor_to_bufsize 张量到其缓冲区大小的映射
 * @param tensor_timesteps 每个时间步包含的张量列表
 * @param timestep_cycle_slack 每个时间步的周期松弛量
 * @param sel_list_iter 输出参数，返回选中的待迁移张量在当前时间步列表中的迭代器
 * @return 最佳目标时间步（-1表示未找到合适目标）
 */
int64_t
TimeStepMethod::get_best_ts(BasicTimeStep *time_step, const LgInfo &lg_info,
                            int64_t cur_ts, ValueIntMap &tensor_to_cycle,
                            ValueIntMap &tensor_to_bufsize,
                            std::vector<std::list<GdmaElt>> &tensor_timesteps,
                            std::vector<int64_t> &timestep_cycle_slack,
                            std::list<GdmaElt>::iterator &sel_list_iter) {
  int64_t src_profit = 0;       // 迁移张量后，源时间步（cur_ts）的收益
  int64_t dst_cost = 0;         // 迁移张量后，目标时间步的成本变化
  int64_t cur_slack = timestep_cycle_slack[cur_ts];  // 当前时间步的周期松弛量（负值，负载紧张）
  int64_t cur_profit = 0;       // 当前候选目标时间步的总收益（src_profit + dst_cost）
  int64_t max_profit = 0;       // 记录最大总收益
  int64_t cur_area = 0;         // 当前候选迁移的内存成本（缓冲区大小 × 时间步距离）
  int64_t best_area = 0;        // 最佳迁移的内存成本
  int64_t best_ts = -1;         // 最佳目标时间步（初始为-1，未找到）
  Value best_sel_tensor;        // 选中的待迁移张量
  bool is_valid;                // 标记目标时间步是否有效

  // 遍历当前时间步（cur_ts）的所有张量，评估每个张量的迁移可能性
  for (auto list_iter = tensor_timesteps[cur_ts].begin();
       list_iter != tensor_timesteps[cur_ts].end(); ++list_iter) {
    auto tensor = list_iter->first;  // 当前评估的张量
    // 计算迁移该张量后，源时间步（cur_ts）的新松弛量（移除张量的GDMA周期，故加上其周期）
    int64_t cur_new_slack = cur_slack + tensor_to_cycle[tensor];
    // 源收益：迁移后源时间步的松弛量改善（若新松弛量≥0则收益为0，否则为新松弛量与原松弛量的差）
    src_profit = (cur_new_slack >= 0 ? 0 : cur_new_slack) - cur_slack;

    // 获取该张量的GDMA操作类型（加载TIMESTEP_LOAD/存储TIMESTEP_STORE）
    auto gdma_type = list_iter->second.mode;
    // 获取该张量的访问范围结束时间步（用于限制迁移的目标时间步范围）
    int64_t range_end = time_step->get_tensor_range_end(*list_iter, cur_ts);
    // 获取第一个候选目标时间步（next_ts），并标记是否有效
    int64_t next_ts = get_next_ts(is_valid, cur_ts, gdma_type, range_end);

    // 遍历所有有效的候选目标时间步
    while (is_valid) {
      // 若为存储操作（TIMESTEP_STORE），且该张量在next_ts被NPU访问，则该时间步不可作为目标（避免冲突）
      if (gdma_type == TIMESTEP_STORE &&
          is_tensor_accessed_by_npu(tensor, time_step, next_ts)) {
        // 继续寻找下一个候选目标时间步
        next_ts = get_next_ts(is_valid, next_ts, gdma_type, range_end);
        continue;
      }

      // 仅考虑目标时间步松弛量>0的情况（有空闲资源接收张量）
      if (timestep_cycle_slack[next_ts] > 0) {
        // 计算目标时间步添加该张量后的成本变化（若添加后松弛量≥0则成本为0，否则为新松弛量）
        dst_cost = timestep_cycle_slack[next_ts] - tensor_to_cycle[tensor];
        dst_cost = dst_cost >= 0 ? 0 : dst_cost;

        // 计算迁移的内存成本：缓冲区大小 ×（时间步距离 + 1）（距离越远/尺寸越大，成本越高）
        cur_area = tensor_to_bufsize[tensor] * (std::abs(next_ts - cur_ts) + 1);
        // 总收益 = 源时间步收益 + 目标时间步成本变化（越大越优）
        cur_profit = src_profit + dst_cost;

        // 若当前总收益大于记录的最大收益，更新最佳目标
        if (cur_profit > max_profit) {
          max_profit = cur_profit;
          best_ts = next_ts;
          sel_list_iter = list_iter;  // 记录选中的张量迭代器
          best_sel_tensor = tensor;
          best_area = cur_area;
        }
        // 若收益相同，选择内存成本更低（cur_area更小）的目标
        else if (cur_profit == max_profit && best_ts != -1) {
          if (cur_area < best_area) {
            max_profit = cur_profit;
            best_ts = next_ts;
            sel_list_iter = list_iter;
            best_sel_tensor = tensor;
            best_area = cur_area;
          }
        }
      }

      // 继续寻找下一个候选目标时间步
      next_ts = get_next_ts(is_valid, next_ts, gdma_type, range_end);
    }
  }

  // 返回找到的最佳目标时间步（-1表示无合适目标）
  return best_ts;
}
```

### 6.bubble_tensor_to_best_ts

##### 核心功能

该函数是张量迁移的执行模块，负责将 `get_best_ts` 选中的张量从当前负载紧张的时间步（`cur_ts`）以 “冒泡” 方式逐步迁移到最佳目标时间步（`best_ts`）。与直接跨步迁移不同，它通过中间时间步逐步移动，同时动态调整各时间步的负载（周期松弛量），并可能选择新的张量继续迁移，确保迁移过程安全（不破坏数据依赖）且优化效果最大化。

##### 逻辑流程

1. 初始化与准备：记录待迁移张量（`best_sel_tensor`）及其 GDMA 类型，初始化迁移过程中的临时变量（如收益评估参数）。
2. 逐步迁移循环：
   - 获取中间时间步：通过 `get_next_ts` 确定从 `cur_ts` 到 `best_ts` 的第一个中间时间步（`next_ts`）。
   - 执行单步迁移：将张量从 `pre_ts`（初始为 `cur_ts`）移动到 `next_ts`，并更新两个时间步的周期松弛量（`pre_ts` 因移除张量而松弛量增加，`next_ts` 因添加张量而松弛量减少）。
   - 终止条件判断：若 `next_ts` 已到达 `best_ts`，退出循环（迁移完成）。
3. 特殊情况处理：若迁移的是存储类型（`TIMESTEP_STORE`）张量，且该张量在 `next_ts` 被 NPU 访问（可能导致数据冲突），则仅调整迭代器位置，不更换迁移张量。
4. 选择下一个迁移张量（非特殊情况）：
   - 遍历 `next_ts` 中与当前张量类型相同的张量，筛选出符合迁移条件（不破坏访问依赖）的候选。
   - 评估每个候选张量迁移到 `best_ts` 的收益（`cur_profit`），选择收益最大（或收益相同但缓冲区更小）的张量作为下一个迁移对象。
   - 若未找到合适张量，终止迁移过程。
5. 迭代迁移：更新 `pre_ts` 和 `next_ts`，重复上述步骤，直到到达 `best_ts` 或无法继续迁移。

##### 核心原理

- 冒泡迁移策略：采用逐步移动而非直接跨步迁移，避免单次大幅迁移对中间时间步负载的剧烈影响，同时便于在每个步骤验证数据依赖（如张量是否被 NPU 访问），确保迁移安全性。
- 动态收益评估：在每个中间步骤重新评估迁移收益，选择最优下一个迁移张量，使整体负载均衡效果最大化（不仅迁移初始选中的张量，还可能带动其他张量优化）。
- 依赖保护：通过 `get_tensor_range_end` 和 `is_tensor_accessed_by_npu` 严格过滤迁移候选，确保迁移不破坏张量的访问时序（如存储操作不能迁移到被 NPU 访问的时间步，避免数据读写冲突）。
- 二次优化：收益相同时优先选择缓冲区更小的张量，在平衡负载的同时减少内存占用和迁移开销。

##### bubble_tensor_to_best_ts 代码

```cpp
/**
 * @brief 将选中的张量以"冒泡"方式逐步迁移到最佳目标时间步
 * @details 该函数不直接跨多个时间步迁移张量，而是通过逐步移动（类似冒泡排序）的方式，
 *          将张量从当前时间步（cur_ts）迁移到最佳目标时间步（best_ts），过程中动态调整
 *          各时间步的周期松弛量，并可能选择新的张量继续迁移，确保迁移过程的安全性和优化效果
 * @param sel_list_iter 当前选中的待迁移张量在原时间步列表中的迭代器
 * @param cur_ts 张量的起始时间步（负载紧张的时间步）
 * @param best_ts 目标最佳时间步（由get_best_ts确定）
 * @param time_step 时间步对象，存储时间步配置及张量信息
 * @param tensor_to_cycle 张量到其GDMA操作周期的映射
 * @param tensor_to_bufsize 张量到其缓冲区大小的映射
 * @param tensor_timesteps 每个时间步包含的张量列表
 * @param timestep_cycle_slack 每个时间步的周期松弛量
 */
void TimeStepMethod::bubble_tensor_to_best_ts(
    std::list<GdmaElt>::iterator sel_list_iter, int64_t cur_ts, int64_t best_ts,
    BasicTimeStep *time_step, ValueIntMap &tensor_to_cycle,
    ValueIntMap &tensor_to_bufsize,
    std::vector<std::list<GdmaElt>> &tensor_timesteps,
    std::vector<int64_t> &timestep_cycle_slack) {
  // 标记是否找到下一个可迁移的最佳张量
  bool find_best = false;
  Value tensor;  // 临时存储当前评估的张量
  // 获取选中的待迁移张量及其GDMA操作类型（加载/存储）
  auto best_sel_tensor = sel_list_iter->first;
  auto gdma_type = sel_list_iter->second.mode;
  // 用于评估迁移收益的临时变量
  int64_t cur_profit = 0, max_profit = 0;
  int64_t cur_new_slack, dst_cost;

  bool is_valid;  // 标记下一个时间步是否有效
  int64_t pre_ts = cur_ts;  // 记录上一个时间步（初始为当前时间步）
  // 获取从当前时间步到最佳目标时间步的第一个中间时间步
  int64_t next_ts = get_next_ts(is_valid, cur_ts, gdma_type, best_ts);

  // 逐步迁移：遍历所有有效的中间时间步，直到到达最佳目标时间步
  while (is_valid) {
    // 步骤1：将选中的张量从pre_ts移动到next_ts
    // 1.1 添加到next_ts的张量列表
    tensor_timesteps[next_ts].push_back(*sel_list_iter);
    // 1.2 更新next_ts的周期松弛量（增加该张量的GDMA周期，故减去）
    timestep_cycle_slack[next_ts] -= tensor_to_cycle[best_sel_tensor];
    // 1.3 从pre_ts的张量列表中移除该张量
    tensor_timesteps[pre_ts].erase(sel_list_iter);
    // 1.4 更新pre_ts的周期松弛量（移除该张量的GDMA周期，故加上）
    timestep_cycle_slack[pre_ts] += tensor_to_cycle[best_sel_tensor];

    // 若已到达最佳目标时间步，退出迁移循环
    if (next_ts == best_ts) {
      break;
    }

    // 步骤2：处理特殊情况（存储操作且张量在next_ts被NPU访问）
    if (gdma_type == TIMESTEP_STORE &&
        is_tensor_accessed_by_npu(best_sel_tensor, time_step, next_ts)) {
      // 调整迭代器到next_ts中刚添加的张量（最后一个元素）
      sel_list_iter = tensor_timesteps[next_ts].end();
      sel_list_iter--;
    } 
    // 步骤3：寻找下一个可继续迁移的张量（非特殊情况时）
    else {
      max_profit = 0;      // 重置最大收益
      find_best = false;   // 重置是否找到最佳张量的标记

      // 遍历next_ts中的所有张量，寻找适合继续迁移到best_ts的张量
      for (auto list_iter = tensor_timesteps[next_ts].begin();
           list_iter != tensor_timesteps[next_ts].end(); ++list_iter) {
        // 仅考虑与当前张量GDMA类型相同的张量（加载/存储类型需一致）
        if (gdma_type != list_iter->second.mode) {
          continue;
        }

        tensor = list_iter->first;  // 当前评估的张量
        // 获取该张量在next_ts中的访问范围结束时间步
        int64_t new_range_end =
            time_step->get_tensor_range_end(*list_iter, next_ts);

        // 过滤无效的迁移候选（避免破坏访问依赖或超出范围）
        if (
            // 加载类型：若访问范围结束时间步>best_ts，迁移会导致访问冲突
            (is_timestep_load(gdma_type) && new_range_end > best_ts) ||
            // 存储类型：若访问范围结束时间步<best_ts，迁移会导致访问冲突
            (gdma_type == TIMESTEP_STORE && new_range_end < best_ts) ||
            // 存储类型：若张量在best_ts被NPU访问，迁移会导致数据不一致
            (gdma_type == TIMESTEP_STORE &&
             is_tensor_accessed_by_npu(tensor, time_step, best_ts))
           ) {
          continue;
        }

        // 计算迁移该张量后，next_ts的新松弛量（移除张量的GDMA周期，故加上）
        cur_new_slack = timestep_cycle_slack[next_ts] + tensor_to_cycle[tensor];
        // 计算next_ts的收益：迁移后松弛量的改善（与原松弛量的差值）
        cur_profit = std::min(cur_new_slack, (int64_t)0) -
                     std::min(timestep_cycle_slack[next_ts], (int64_t)0);

        // 计算best_ts接收该张量后的成本变化（目标时间步的收益）
        dst_cost = timestep_cycle_slack[best_ts] - tensor_to_cycle[tensor];
        dst_cost = dst_cost >= 0 ? 0 : dst_cost;  // 若仍为正，成本为0

        // 总收益 = next_ts的收益 + best_ts的成本变化
        cur_profit = cur_profit + dst_cost;

        // 选择收益最大的张量；收益相同时，选择缓冲区更小的张量（减少内存成本）
        if (cur_profit > max_profit ||
            (cur_profit == max_profit &&
             tensor_to_bufsize[tensor] < tensor_to_bufsize[best_sel_tensor])) {
          sel_list_iter = list_iter;       // 更新选中的张量迭代器
          max_profit = cur_profit;         // 更新最大收益
          best_sel_tensor = tensor;        // 更新选中的张量
          find_best = true;                // 标记已找到合适张量
        }
      }

      // 若未找到可继续迁移的张量，终止迁移过程
      if (find_best == false) {
        break;
      }
    }

    // 准备下一轮迁移：更新上一个时间步和下一个时间步
    pre_ts = next_ts;
    next_ts = get_next_ts(is_valid, next_ts, gdma_type, best_ts);
  }
}
```

## 4./LayerGroup/LayerGroupUtil.cpp

### 1.stripe_mine_idx_slice

##### **核心功能**

该函数是 “条带挖掘（stripe mining）” 技术在张量分片中的具体实现，主要作用是：为层组的输出张量生成具体的分片索引（基于形状分段规则），并通过反向传播（从输出到输入）更新所有相关张量的分片信息，最终将结果存入 `tensor_infos`，为后续的并行计算（如硬件核间分配）提供依据。

##### **执行流程**

整个流程可分为 3 个关键阶段：

- 特殊情况处理
  若层组内仅包含 1 个操作，且配置选项中不启用 “按核心分组”（`options.group_by_cores` 为 false），则无需进行分片处理（单个操作无需并行拆分），直接返回成功。
- 输出张量分片初始化

  1. 清空 `tensor_infos`，准备存储新的分片信息。
  2. 遍历层组的所有输出张量（`lg_info.group_outs`），解析每个张量的 NCDHW 维度（批量、通道、深度、高度、宽度）。
  3. 计算张量的有效位宽（输入输出位宽的最小值），并调用 `get_out_slice_info` 生成该输出张量的分片信息（`slice_info`），包含各维度的分片范围、索引等细节。
  4. 将分片信息存入 `tensor_infos`，并通过 `strip_back_judge` 判断该输出张量是否需要反向传播分片信息（即是否需要从输出推导输入的分片）。若需要，将其加入 `tensor_branchs` 列表。
- 反向传播更新分片信息循环处理 `tensor_branchs` 中的所有张量：

  1. 取出一个输出张量，调用 `backward_update_slice` 进行反向更新：从该输出张量出发，分析其依赖的输入张量（如操作的输入），根据输出分片推导输入张量的合理分片，并更新 `tensor_infos`。
  2. 若输入张量还存在上游依赖（即该输入张量是其他操作的输出），则将其加入 `tensor_branchs`，继续反向传播处理。
  3. 若任何一次反向更新失败，函数立即返回 false；全部处理完成则返回 true。

##### ** 关键函数解析**

- `module::getNCDHW`：解析张量的 NCDHW 维度（深度学习中常用的张量格式，适配卷积、池化等操作），为分片提供维度基础。
- `get_out_slice_info`：根据 `shape_secs`（形状分段规则）和张量维度，计算输出张量的分片信息（如每个分片在 N/C/D/H/W 维度上的起始和结束索引），是 “条带挖掘” 的核心实现。
- `strip_back_judge`：判断输出张量是否需要反向传播分片信息（例如，若该输出张量是某个操作的输出，而该操作有输入张量需要同步分片，则需要反向传播）。
- `backward_update_slice`：反向传播的核心函数，从输出张量的分片信息出发，推导其依赖的输入张量的分片规则，更新 `tensor_infos`，并递归处理上游张量。

##### ** 设计原理**

- 条带挖掘技术：将大张量按维度分成多个小 “条带”（分片），适配硬件的并行处理能力（如多核心分别处理不同分片），减少单次计算的内存占用，提升并行效率。
- 反向传播分片：从输出张量出发推导输入分片，确保输入与输出的分片匹配（如输入分片经过操作后恰好生成对应的输出分片），避免数据不匹配导致的计算错误。
- 按需处理：通过 `strip_back_judge` 筛选需要反向传播的张量，避免无意义的计算，提升效率。

##### **stripe_mine_idx_slice 代码**

```cpp
/**
 * 基于条带挖掘（stripe mining）技术，为层组的输出张量生成具体分片索引，并反向更新相关张量的分片信息
 * @param lg_info 层组信息，包含组内操作、输入输出张量等
 * @param shape_secs 形状分段信息，指导张量在各维度上的分片规则
 * @param tensor_infos 输出参数，存储各张量的分片信息（tensor_info_t）
 * @param options 层组配置选项，包含分组策略等
 * @return 处理成功返回true，失败返回false
 */
bool stripe_mine_idx_slice(const LgInfo &lg_info,
                           const shape_secs_t &shape_secs,
                           TensorInfo &tensor_infos, const LgOptions &options) {
  // 特殊情况处理：若组内仅1个操作且不按核心分组，无需分片，直接返回成功
  if (lg_info.group_ops.size() == 1 && false == options.group_by_cores) {
    return true;
  }

  // 清空张量信息集合，准备存储新的分片结果
  tensor_infos.clear();

  // 张量维度变量（NCDHW格式：批量数、通道数、深度、高度、宽度，常用于深度学习张量）
  int64_t n, c, d, h, w;
  // 存储需要反向传播分片信息的张量分支
  std::list<Value> tensor_branchs;
  // 跟踪处理过的操作（去重）
  std::multiset<Operation *> op_set;
  // 跟踪输出张量（去重，按value_compare规则排序）
  std::set<Value, value_compare> out_tensor_set;

  // 遍历层组的所有输出张量，初始化输出张量的分片信息
  for (auto out : lg_info.group_outs) {
    // 获取输出张量的NCDHW维度（根据层组类型调整维度解析方式）
    module::getNCDHW(out, n, c, d, h, w, lg_info.type);
    // 获取输入张量和输出张量的存储类型（如数据类型、位宽等）
    auto istype = module::getStorageType(lg_info.group_ins[0]);
    auto ostype = module::getStorageType(out);
    // 计算分片的位宽（取输入和输出位宽的最小值，确保兼容性）
    int64_t bitwidth = std::min(istype.getIntOrFloatBitWidth(),
                                ostype.getIntOrFloatBitWidth());
    // 根据形状分段和维度信息，获取输出张量的分片信息（slice_info）
    auto si = get_out_slice_info(shape_secs, n, c, h, d, w, bitwidth);

    // 将输出张量的分片信息存入tensor_infos
    tensor_infos[out] = tensor_info_t(si);
    // 记录该输出张量
    out_tensor_set.insert(out);

    // 判断是否需要对该输出张量进行反向分片传播（从输出到输入更新分片信息）
    // 若需要，将其加入tensor_branchs列表待处理
    if (strip_back_judge(out, lg_info, op_set, out_tensor_set)) {
      tensor_branchs.push_back(out);
    }
  }

  // 处理所有需要反向传播的张量分支
  bool ret = false;
  while (!tensor_branchs.empty()) {
    // 取出列表中的第一个张量
    auto out_tensor = tensor_branchs.front();
    tensor_branchs.pop_front();

    // 反向更新分片信息：从当前输出张量出发，推导其依赖的输入张量的分片，并更新tensor_infos
    // 同时可能将新的依赖张量加入tensor_branchs继续处理
    ret = backward_update_slice(lg_info, shape_secs, out_tensor, tensor_branchs,
                                tensor_infos, op_set, out_tensor_set);
    // 若反向更新失败，返回false
    if (!ret) {
      return false;
    }
  }

  // 所有张量分片信息处理完成，返回成功
  return true;
}
```

### 2.stripe_mine_max_slice

##### 核心功能

该函数是 “条带挖掘（stripe mining）” 技术的另一实现，核心作用是：为层组的输出张量计算各维度（N/C/D/H/W）的最大可能分片数（而非具体分片索引），并通过反向传播（从输出到输入）更新所有相关张量的最大分片信息，最终存入 `tensor_infos`。其主要用于预检查（如资源评估、硬件兼容性验证），确保分片策略在硬件能力范围内。

##### 执行流程

整个流程可分为 3 个关键阶段：

- 特殊情况处理
  与 `stripe_mine_idx_slice` 一致：若层组内仅 1 个操作且不按核心分组（`options.group_by_cores` 为 false），无需分片，直接返回成功。
- 输出张量最大分片数计算

  1. 清空 `tensor_infos`，准备存储新的最大分片信息。
  2. 遍历层组的所有输出张量（`lg_info.group_outs`），解析每个张量的 NCDHW 维度（批量、通道、深度、高度、宽度）。
  3. 计算各维度的最大分片数：
     - 对每个维度（N/D/H/W/C），通过 `ceiling_func`（上取整函数）计算当前张量的分片数（维度大小 ÷ 分段数 `shape_secs.xxx`），并保留所有输出张量中的最大值（`max_xxxslice`）。
     - 针对硬件约束进行对齐：例如，若架构要求 `ALIGN_4N`，则 `max_nslice` 需对齐到 “32 / 位宽” 的倍数；`max_cslice` 需按 `Arch::NPU_NUM`（NPU 核心数）对齐，确保分片数适配硬件核心数量。
  4. 为每个输出张量创建 `slice_info_t` 对象，记录各维度的最大分片范围（0 到最大分片数），存入 `tensor_infos`。
  5. 通过 `strip_back_judge` 判断该输出张量是否需要反向传播最大分片信息（即是否需要从输出推导输入的最大分片）。若需要，将其加入 `tensor_branchs` 列表。
- 反向传播更新最大分片信息循环处理 `tensor_branchs` 中的所有张量：

  1. 取出一个输出张量，调用 `backward_update_slice` 进行反向更新：从该输出张量的最大分片数出发，分析其依赖的输入张量（如操作的输入），推导输入张量的最大分片数（需满足 “输入分片经过操作后不超过输出最大分片”），并更新 `tensor_infos`。
  2. 若输入张量还存在上游依赖（即该输入张量是其他操作的输出），则将其加入 `tensor_branchs`，继续反向传播处理。
  3. 若任何一次反向更新失败，函数立即返回 false；全部处理完成则返回 true。

##### 关键函数与变量解析

- `module::getNCDHW`：解析张量的 NCDHW 维度，为分片数计算提供维度基础。
- `ceiling_func`：上取整函数，用于计算 “维度大小 ÷ 分段数” 的结果（确保分片能覆盖整个张量，如 10 个元素按 3 分段需 4 个分片）。
- `align_up`：向上对齐函数，将分片数调整为硬件要求的倍数（如按 NPU 核心数对齐，避免分片数超过硬件处理能力）。
- `strip_back_judge`：判断输出张量是否需要反向传播最大分片信息（例如，若输入张量的分片数会影响输出分片，則需要反向传播）。
- `backward_update_slice`：反向传播的核心函数，从输出张量的最大分片数推导输入张量的最大分片数，确保输入分片不超过硬件或输出的限制。

##### 设计原理

- 最大分片数计算：不同于 `stripe_mine_idx_slice` 生成具体分片索引，本函数专注于 “最大可能分片数”，用于评估硬件资源需求（如需要多少核心才能处理所有分片）。
- 硬件约束适配：通过 `align_up` 和架构宏（`Arch::ALIGN_4N`、`Arch::NPU_NUM`）确保分片数符合硬件限制（如核心数量、内存对齐要求），避免因分片数不合理导致的硬件无法处理。
- 反向传播一致性：从输出到输入同步最大分片数，确保整个层组的张量分片在 “最大范围” 上保持一致（输入分片不会导致输出分片超出限制）。

##### stripe_mine_max_slice 代码

```cpp
/**
 * 基于条带挖掘（stripe mining）技术，计算层组张量各维度的最大可能分片数，并反向更新相关张量的最大分片信息
 * @param lg_info 层组信息，包含组内操作、输入输出张量等
 * @param shape_secs 形状分段信息，指导张量各维度的分段规则（如每段大小）
 * @param tensor_infos 输出参数，存储各张量的最大分片信息（slice_info_t）
 * @param options 层组配置选项，包含分组策略等
 * @return 处理成功返回true，失败返回false
 */
bool stripe_mine_max_slice(const LgInfo &lg_info,
                           const shape_secs_t &shape_secs,
                           TensorInfo &tensor_infos, const LgOptions &options) {
  // 特殊情况处理：若组内仅1个操作且不按核心分组，无需分片，直接返回成功
  if (lg_info.group_ops.size() == 1 && false == options.group_by_cores) {
    return true;
  }

  // 清空张量信息集合，准备存储新的最大分片结果
  tensor_infos.clear();

  // 张量维度变量（NCDHW格式：批量数、通道数、深度、高度、宽度）
  int64_t n, c, d, h, w;
  // 各维度的最大分片数（记录所有输出张量中该维度的最大分片需求）
  int64_t max_nslice = 0, max_cslice = 0;
  int64_t max_dslice = 0, max_hslice = 0, max_wslice = 0;
  // 存储需要反向传播最大分片信息的张量分支
  std::list<Value> tensor_branchs;
  // 跟踪处理过的操作（去重）
  std::multiset<Operation *> op_set;
  // 跟踪输出张量（去重）
  ValueSet out_tensor_set;
  // 分片信息结构体（记录各维度的最大分片范围）
  slice_info_t si;

  // 遍历层组的所有输出张量，计算各维度的最大分片数
  for (auto out : lg_info.group_outs) {
    // 获取输出张量的NCDHW维度（根据层组类型调整解析方式）
    module::getNCDHW(out, n, c, d, h, w, lg_info.type);

    // 计算n维度的最大分片数：当前张量n维度除以分段数（shape_secs.nsecs）的上取整，取所有张量中的最大值
    max_nslice = std::max(max_nslice, ceiling_func(n, shape_secs.nsecs));
    // 若架构要求4N对齐（如硬件限制），则将n维度最大分片数对齐到指定倍数（32位/位宽）
    if (Arch::ALIGN_4N) {
      auto stype = module::getStorageType(out); // 获取张量存储类型（含位宽）
      int64_t align_n = 32 / stype.getIntOrFloatBitWidth(); // 计算对齐倍数
      max_nslice = align_up(max_nslice, align_n); // 向上对齐到align_n的倍数
    }

    // 计算d/h/w维度的最大分片数：当前张量维度除分段数的上取整，取所有张量中的最大值
    max_dslice = ceiling_func(d, shape_secs.dsecs); // d维度（深度）
    max_hslice = ceiling_func(h, shape_secs.hsecs); // h维度（高度）
    max_wslice = ceiling_func(w, shape_secs.wsecs); // w维度（宽度）

    // 计算c维度的最大分片数：当前张量通道数除分段数的上取整，再按NPU数量对齐（硬件核心数限制）
    max_cslice = align_up(ceiling_func(c, shape_secs.csecs), Arch::NPU_NUM);

    // 初始化输出张量的最大分片信息（各维度分片范围为0到最大分片数）
    si.n.clear();    // 清空n维度分片记录
    si.h.clear();    // 清空h维度分片记录
    si.d.clear();    // 清空d维度分片记录
    si.w.clear();    // 清空w维度分片记录
    si.c.clear();    // 清空c维度分片记录
    // 记录n维度最大分片范围（0到max_nslice）
    si.n.emplace_back(slice_pair_t(0, max_nslice));
    // 记录h维度最大分片范围
    si.h.emplace_back(slice_pair_t(0, max_hslice));
    // 记录d维度最大分片范围
    si.d.emplace_back(slice_pair_t(0, max_dslice));
    // 记录w维度最大分片范围
    si.w.emplace_back(slice_pair_t(0, max_wslice));
    // 记录c维度最大分片范围
    si.c.emplace_back(slice_pair_t(0, max_cslice));

    // 将输出张量的最大分片信息存入tensor_infos
    tensor_infos[out] = tensor_info_t(si);
    // 记录该输出张量
    out_tensor_set.insert(out);

    // 判断是否需要对该输出张量进行反向传播（从输出到输入更新最大分片信息）
    // 若需要，将其加入tensor_branchs列表待处理
    if (strip_back_judge(out, lg_info, op_set, out_tensor_set)) {
      tensor_branchs.push_back(out);
    }
  }

  // 处理所有需要反向传播的张量分支
  bool ret = false;
  while (!tensor_branchs.empty()) {
    // 取出列表中的第一个张量
    auto out_tensor = tensor_branchs.front();
    tensor_branchs.pop_front();

    // 反向更新最大分片信息：从当前输出张量出发，推导其依赖的输入张量的最大分片数，更新tensor_infos
    // 同时可能将新的依赖张量加入tensor_branchs继续处理
    ret = backward_update_slice(lg_info, shape_secs, out_tensor, tensor_branchs,
                                tensor_infos, op_set, out_tensor_set);
    // 若反向更新失败，返回false
    if (!ret) {
      return false;
    }
  }

  // 注释掉的分片检查：验证张量分片信息的有效性（当前未启用）
  //  if (check_tensor_slice(tensor_infos) == false) {
  //    return false;
  //  }

  // 所有张量最大分片信息处理完成，返回成功
  return true;
}
```

### 3.init_group_data_secs

#### 代码功能分析

该函数 `init_group_data_secs` 主要用于初始化分组操作的数据分片信息（`shape_secs`），通过计算操作所需的内存资源，结合硬件限制（如本地内存大小）和分组配置，确定合理的分片策略，确保分组内的所有操作都能在硬件资源约束下正常运行。

#### 代码原理与逻辑

1. 特殊情况处理：当分组中仅包含 1 个操作且不按核心分组时，无需复杂分片，直接返回成功。
2. 获取最大分片限制：通过 `get_group_max_secs` 获取分组操作允许的最大分片维度（`max_shape_secs`），作为分片的上限约束。
3. 遍历分组内操作：对每个操作计算其输入、输出及缓冲区所需的总内存大小。
4. 内存与分片计算：根据总内存大小计算所需的分片数，并结合最大分片限制，调整各维度（N/C/D/H/W）的分片数量（`shape_secs`）。
5. 合法性检查：确保最终确定的分片数不超过硬件最大限制，若超过则返回失败。

#### 3.init_group_data_secs 代码

```cpp
/**
 * @brief 初始化分组操作的数据分片信息，确保所有操作在硬件资源约束下可执行
 * @param lg_info 分组信息（包含分组内的操作、类型等）
 * @param shape_secs 输出参数，存储计算得到的各维度分片数（N/C/D/H/W方向）
 * @param value_size 输出参数，存储权重等数据的大小（按64字节对齐）
 * @param options 分组配置选项
 * @return 初始化成功返回true，失败返回false
 */
bool init_group_data_secs(const LgInfo &lg_info, shape_secs_t &shape_secs,
                          std::vector<std::pair<Value, int64_t>> &value_size,
                          const LgOptions &options) {
  // 特殊情况：若分组仅含1个操作且不按核心分组，无需分片处理，直接返回成功
  if (lg_info.group_ops.size() == 1 && false == options.group_by_cores) {
    return true;
  }

  // 存储操作与硬件分片的对应关系
  std::vector<std::pair<Operation *, int>> vec_op_hwsecs;
  // 获取分组操作允许的最大分片维度（各方向的上限）
  shape_secs_t max_shape_secs = get_group_max_secs(lg_info, vec_op_hwsecs);

  // 用于存储输入/输出 tensor 的 NCDHW 维度（深度学习常用维度：批数/通道数/深度/高度/宽度）
  int64_t in_n, in_c, in_d, in_h, in_w;
  int64_t out_n, out_c, out_d, out_h, out_w;

  // 遍历分组内的所有操作，计算每个操作的资源需求并调整分片策略
  for (auto op : lg_info.group_ops) {
    // 获取当前操作的输入和输出 tensor
    auto ins = get_input_values(op);
    auto outs = get_output_values(op);

    // 解析第一个输入 tensor 的 NCDHW 维度（根据分组类型确定维度顺序）
    module::getNCDHW(ins[0], in_n, in_c, in_d, in_h, in_w, lg_info.type);
    // 解析第一个输出 tensor 的 NCDHW 维度
    module::getNCDHW(outs[0], out_n, out_c, out_d, out_h, out_w, lg_info.type);

    // 计算第一个输入 tensor 所需的本地内存（LMEM）字节数
    int64_t in0_lmem_bytes =
        Arch::get_tensor_lmem_bytes(ins[0], in_n, in_c, in_d, in_h, in_w);
    // 计算第一个输出 tensor 所需的本地内存字节数
    int64_t out0_lmem_bytes =
        Arch::get_tensor_lmem_bytes(outs[0], out_n, out_c, out_d, out_h, out_w);

    // 计算总内存需求：输入+输出+缓冲区
    int64_t total_size = in0_lmem_bytes + out0_lmem_bytes;
    // 将操作转换为本地生成接口类型，获取其所需的缓冲区大小
    auto lg_op = cast<LocalGenInterface>(op);
    total_size += lg_op.getBufferSize(in0_lmem_bytes, out0_lmem_bytes, in_n,
                                      in_c, in_h, in_d, in_w, out_n, out_c,
                                      out_h, out_d, out_w, lg_info.type);

    // 处理剩余输入 tensor 的内存需求
    for (size_t i = 1; i < ins.size(); ++i) {
      // 训练模式下非Add/Concat操作的输入，或权重类型的输入，需单独计算权重内存
      if ((module::isTrain() && !isa<tpu::AddOp, tpu::ConcatOp>(op)) ||
          module::isWeight(ins[i])) {
        // 判断是否需要按计算单元（EU）对齐
        bool eu_align = is_eu_align(ins[i]);
        // 计算权重所需的本地内存字节数
        int w_size =
            Arch::get_weight_lmem_bytes(ins[i], lg_info.type, eu_align);
        total_size += w_size;
        // 按64字节对齐后记录权重大小
        value_size.push_back(std::make_pair(ins[i], (w_size + 63) / 64 * 64));
      } else {
        // 其他输入 tensor：解析维度并计算内存
        module::getNCDHW(ins[i], in_n, in_c, in_d, in_h, in_w, lg_info.type);
        total_size +=
            Arch::get_tensor_lmem_bytes(ins[i], in_n, in_c, in_d, in_h, in_w);
      }
    }

    // 处理剩余输出 tensor 的内存需求
    for (size_t i = 1; i < outs.size(); ++i) {
      module::getNCDHW(outs[i], out_n, out_c, out_d, out_h, out_w,
                       lg_info.type);
      total_size += Arch::get_tensor_lmem_bytes(outs[i], out_n, out_c, out_d,
                                                out_h, out_w);
    }

    // 根据总内存需求和硬件LMEM大小，计算所需的总分片数（向上取整）
    int64_t total_secs = ceiling_func(total_size, Arch::LMEM_BYTES);

    // 调整N方向分片数：不超过最大限制，且取当前最大值
    shape_secs.nsecs =
        std::max(std::min(total_secs, max_shape_secs.nsecs), shape_secs.nsecs);
    // 按N方向分片数重新计算总分片数（向上取整）
    total_secs = ceiling_func(total_secs, shape_secs.nsecs);

    // 对矩阵乘法（GROUP_MM）或小通道（GROUP_SMALL_C）类型的分组，特殊处理C方向分片
    if (lg_info.type == GROUP_MM || lg_info.type == GROUP_SMALL_C) {
      if (total_secs > max_shape_secs.csecs) {
        // 若当前分片数超过C方向最大限制，直接使用最大限制
        shape_secs.csecs = max_shape_secs.csecs;
      } else {
        // 计算每个NPU的C方向分片数，调整C方向分片数（不超过最大限制）
        int64_t cslice_per_npu = max_shape_secs.csecs / total_secs;
        shape_secs.csecs =
            std::max(ceiling_func(max_shape_secs.csecs, cslice_per_npu),
                     shape_secs.csecs);
      }
      // 按C方向分片数重新计算总分片数
      total_secs = ceiling_func(total_secs, shape_secs.csecs);
    }

    // 调整D方向分片数：不超过最大限制，取当前最大值
    shape_secs.dsecs =
        std::max(std::min(total_secs, max_shape_secs.dsecs), shape_secs.dsecs);
    // 按D方向分片数重新计算总分片数
    total_secs = ceiling_func(total_secs, shape_secs.dsecs);

    // 调整H方向分片数：取当前总分片数与已有值的最大值
    shape_secs.hsecs = std::max(total_secs, shape_secs.hsecs);

    // 检查H方向分片是否超过最大限制，若超过则尝试通过W方向分片分担
    if (shape_secs.hsecs > max_shape_secs.hsecs) {
      // 计算W方向所需分片数（将H方向超额部分分摊到W方向）
      shape_secs.wsecs = ceiling_func(shape_secs.hsecs, max_shape_secs.hsecs);
      // 若W方向分片也超过最大限制，则初始化失败
      if (shape_secs.wsecs > max_shape_secs.wsecs) {
        // 可取消注释打印调试信息：llvm::outs() << "fail at op:"<<module::getName(op).str()<<"\n";
        return false;
      }
      // H方向分片数限制为最大值
      shape_secs.hsecs = max_shape_secs.hsecs;
    }
  }

  // 所有操作的分片策略均合法，返回成功
  return true;
}
```

### 4.init_group_data_secs2

#### 代码功能分析

函数 `init_group_data_secs2` 是 `init_group_data_secs` 的改进版本，主要功能仍是初始化分组操作的数据分片信息（`shape_secs`）。与原函数相比，它增强了错误跟踪（通过 `fail_op` 定位失败操作）、增加了日志可视化支持（`dot_graph_log`）、调整了内存计算逻辑（区分权重与非权重内存），并扩展了支持的分组类型，更适应复杂场景下的分片策略计算。

#### 代码原理与逻辑

1. 特殊情况处理：同原函数，当分组仅含 1 个操作且不按核心分组时，直接返回成功。
2. 初始化与准备：初始化 `fail_op`（用于记录失败的操作），获取分组最大分片限制（`max_shape_secs`）。
3. 遍历操作计算资源：对每个操作解析输入 / 输出 tensor 的维度，计算内存需求。与原函数的核心差异在于：

   - 引入 `non_weight_size` 单独管理非权重内存（初始为硬件本地内存大小），权重内存从该值中扣除而非直接累加。
   - 仅用非权重内存计算所需分片数（`total_secs`），更精确反映实际可用内存。
4. 分片调整与约束检查：按 N/C/D/H/W 维度依次调整分片数，确保不超过硬件最大限制。扩展支持 `GROUP_NORMAL` 类型分组，并通过 `dot_graph_log` 记录分片信息用于可视化。
5. 错误处理：当内存不足或分片超限时，通过 `fail_op` 记录出错操作并返回失败，同时输出错误日志。

#### 3.init_group_data_secs2 代码

```cpp
/**
 * @brief 初始化分组操作的数据分片信息（增强版），支持错误定位和日志可视化
 * @param ilp_lg_info 增强的分组信息（包含基础分组信息）
 * @param shape_secs 输出参数，存储各维度分片数（N/C/D/H/W）
 * @param value_size 输出参数，存储权重等数据的大小（按64字节对齐）
 * @param fail_op 输出参数，记录初始化失败的操作（若失败）
 * @param dot_graph_log 日志指针，用于记录分片信息（可视化调试）
 * @param options 分组配置选项
 * @return 初始化成功返回true，失败返回false
 */
bool init_group_data_secs2(ilp_LgInfo &ilp_lg_info, shape_secs_t &shape_secs,
                           std::vector<std::pair<Value, int64_t>> &value_size,
                           Operation *&fail_op,
                           std::shared_ptr<dot_graph> dot_graph_log,
                           const LgOptions &options) {
  fail_op = nullptr;  // 初始化失败操作指针（默认无失败）
  auto lg_info = ilp_lg_info._lgInfo;  // 从增强信息中获取基础分组信息

  // 特殊情况：分组仅含1个操作且不按核心分组，无需分片处理
  if (lg_info.group_ops.size() == 1 && false == options.group_by_cores) {
    return true;
  }

  // 存储操作与硬件分片的对应关系
  std::vector<std::pair<Operation *, int>> vec_op_hwsecs;
  // 获取分组允许的最大分片维度（各方向的上限约束）
  shape_secs_t max_shape_secs = get_group_max_secs(lg_info, vec_op_hwsecs);

  // 存储输入/输出 tensor 的 NCDHW 维度（批数/通道数/深度/高度/宽度）
  int64_t in_n, in_c, in_d, in_h, in_w;
  int64_t out_n, out_c, out_d, out_h, out_w;

  // 遍历分组内所有操作，计算每个操作的资源需求并调整分片策略
  for (auto op : lg_info.group_ops) {
    if (!op) continue;  // 跳过空操作

    // 获取当前操作的输入和输出 tensor
    auto ins = get_input_values(op);
    auto outs = get_output_values(op);

    // 解析第一个输入 tensor 的 NCDHW 维度（按分组类型确定维度顺序）
    module::getNCDHW(ins[0], in_n, in_c, in_d, in_h, in_w, lg_info.type);
    // 解析第一个输出 tensor 的 NCDHW 维度
    module::getNCDHW(outs[0], out_n, out_c, out_d, out_h, out_w, lg_info.type);

    // 计算第一个输入 tensor 所需的本地内存（LMEM）字节数
    int64_t in0_lmem_bytes =
        Arch::get_tensor_lmem_bytes(ins[0], in_n, in_c, in_d, in_h, in_w);
    // 计算第一个输出 tensor 所需的本地内存字节数
    int64_t out0_lmem_bytes =
        Arch::get_tensor_lmem_bytes(outs[0], out_n, out_c, out_d, out_h, out_w);

    // 计算总内存需求：输入+输出+缓冲区
    int64_t total_size = in0_lmem_bytes + out0_lmem_bytes;
    // 转换为本地生成接口，获取操作所需的缓冲区大小
    auto lg_op = cast<LocalGenInterface>(op);
    int64_t buffer_size = lg_op.getBufferSize(
        in0_lmem_bytes, out0_lmem_bytes, in_n, in_c, in_h, in_d, in_w, out_n,
        out_c, out_h, out_d, out_w, lg_info.type);
    total_size += buffer_size;  // 累加缓冲区内存

    // 非权重内存：初始为硬件本地内存总大小，用于后续计算有效分片空间
    int64_t non_weight_size = Arch::LMEM_BYTES;

    // 处理剩余输入 tensor 的内存需求
    for (size_t i = 1; i < ins.size(); ++i) {
      // 训练模式下特定操作（非Add/Sub等）的输入，或权重类型输入：单独处理权重内存
      if ((module::isTrain() &&
           !isa<tpu::AddOp, tpu::SubOp, tpu::MulOp, tpu::DivOp, tpu::MinOp,
                tpu::MaxOp, tpu::ConcatOp>(op)) ||
          module::isWeight(ins[i])) {
        bool eu_align = is_eu_align(ins[i]);  // 是否按计算单元（EU）对齐
        // 计算权重所需的本地内存字节数
        int w_size =
            Arch::get_weight_lmem_bytes(ins[i], lg_info.type, eu_align);
        // 若需EU对齐，从非权重内存中扣除权重占用（权重不参与分片计算）
        if (eu_align) {
          non_weight_size -= w_size;
        }
        // 按64字节对齐记录权重大小
        value_size.push_back(std::make_pair(ins[i], (w_size + 63) / 64 * 64));
      } else {
        // 其他输入 tensor：解析维度并累加内存需求（参与分片计算）
        module::getNCDHW(ins[i], in_n, in_c, in_d, in_h, in_w, lg_info.type);
        total_size +=
            Arch::get_tensor_lmem_bytes(ins[i], in_n, in_c, in_d, in_h, in_w);
      }
    }

    // 处理剩余输出 tensor 的内存需求（累加至总内存，参与分片计算）
    for (size_t i = 1; i < outs.size(); ++i) {
      module::getNCDHW(outs[i], out_n, out_c, out_d, out_h, out_w,
                       lg_info.type);
      total_size += Arch::get_tensor_lmem_bytes(outs[i], out_n, out_c, out_d,
                                                out_h, out_w);
    }

    // 检查非权重内存是否不足（扣除权重后无剩余空间）
    if (non_weight_size <= 0) {
      fail_op = op;  // 记录失败的操作
      return false;
    }

    // 计算所需总分片数：总内存 / 非权重可用内存（向上取整）
    int64_t total_secs = ceiling_func(total_size, non_weight_size);

    // 调整N方向分片数：不超过最大限制，取当前最大值
    shape_secs.nsecs =
        std::max(std::min(total_secs, max_shape_secs.nsecs), shape_secs.nsecs);
    // 按N方向分片数重新计算总分片数（向上取整）
    total_secs = ceiling_func(total_secs, shape_secs.nsecs);

    // 处理C方向分片：支持矩阵乘法（GROUP_MM）、小通道（GROUP_SMALL_C）和普通（GROUP_NORMAL）分组
    if (lg_info.type == GROUP_MM || lg_info.type == GROUP_SMALL_C ||
        lg_info.type == GROUP_NORMAL) {
      if (total_secs > max_shape_secs.csecs) {
        // 若当前分片数超过C方向最大限制，直接使用最大限制
        shape_secs.csecs = max_shape_secs.csecs;
      } else {
        // 计算每个NPU的C方向分片数，调整C方向分片数（不超过最大限制）
        int64_t cslice_per_npu = max_shape_secs.csecs / total_secs;
        shape_secs.csecs =
            std::max(ceiling_func(max_shape_secs.csecs, cslice_per_npu),
                     shape_secs.csecs);
      }
      // 按C方向分片数重新计算总分片数
      total_secs = ceiling_func(total_secs, shape_secs.csecs);
    }

    // 调整D方向分片数：不超过最大限制，取当前最大值
    shape_secs.dsecs =
        std::max(std::min(total_secs, max_shape_secs.dsecs), shape_secs.dsecs);
    // 按D方向分片数重新计算总分片数
    total_secs = ceiling_func(total_secs, shape_secs.dsecs);

    // 调整H方向分片数：取当前总分片数与已有值的最大值
    shape_secs.hsecs = std::max(total_secs, shape_secs.hsecs);

    // 记录原始H方向分片数到日志（用于可视化调试）
    auto name = module::getName(op).str();
    dot_graph_log->add_node_label(name + "_ori",
                                  "hsecs: " + std::to_string(total_secs));

    // 检查H方向分片是否超过最大限制，若超过则通过W方向分片分担
    if (shape_secs.hsecs > max_shape_secs.hsecs) {
      // 计算W方向所需分片数（将H方向超额部分分摊到W方向）
      shape_secs.wsecs = ceiling_func(shape_secs.hsecs, max_shape_secs.hsecs);
      // 若W方向分片也超过最大限制，初始化失败
      if (shape_secs.wsecs > max_shape_secs.wsecs) {
        llvm::errs() << "init_group_data_secs2 fail at op:"
                     << module::getName(op).str() << "\n";  // 输出错误日志
        return false;
      }
      // H方向分片数限制为最大值
      shape_secs.hsecs = max_shape_secs.hsecs;
    }
  }

  // 所有操作的分片策略均合法，返回成功
  return true;
}
```

### 5.get_split_max_secs

#### 1.功能分析

函数 `get_split_max_secs` 用于计算时间步（TimeStep）在硬件上运行时所需的最大分片数。它通过分析每个时间步的本地内存（LMEM）需求，找到内存需求峰值，再结合硬件 LMEM 容量，计算出满足该峰值需求所需的最小分片数（向上取整），为时间步的分片策略提供依据。

#### 2.代码原理与逻辑

1. 时间步数量检查：首先获取时间步总数，若为 0 则直接返回 0（无有效时间步）。
2. 内存需求数组初始化：创建与时间步数量相同的数组 `lmem_req`，用于记录每个时间步的总 LMEM 需求。
3. 内存需求更新逻辑：定义 lambda 函数 `update_lmem_req`，用于根据内存块的时间范围（`start_ts` 到 `end_ts`），将内存块大小累加到对应时间步的需求中（支持跨时间步循环的场景）。
4. 遍历内存块计算需求：遍历所有 LMEM 内存块，调用 `update_lmem_req` 更新每个时间步的总内存需求。
5. 计算最大分片数：对内存需求数组降序排序，取最大值（峰值需求），按硬件 LMEM 容量向上取整，得到所需的最大分片数。

#### 3.get_split_max_secs 代码

```cpp
/**
 * @brief 计算时间步（TimeStep）运行所需的最大分片数
 * @param time_step 时间步对象，包含时间步数量和内存块信息
 * @return 满足最大内存需求的最小分片数（向上取整）
 */
int64_t get_split_max_secs(BasicTimeStepPtr time_step) {
  // 获取时间步的总数量
  int64_t timestep_num = time_step->get_timestep_num();
  // 若没有时间步，直接返回0
  if (timestep_num == 0) {
    return 0;
  }

  // 初始化每个时间步的LMEM需求数组（初始值为0）
  std::vector<int64_t> lmem_req(timestep_num, 0);
  // 获取时间步的本地内存（LMEM）缓冲区信息（包含所有内存块）
  const MemBuff &lmem_buffer = time_step->get_lmem_buffer();

  /**
   * @brief 更新时间步的LMEM需求
   * @param start_ts 内存块占用的起始时间步
   * @param end_ts 内存块占用的结束时间步
   * @param lmem_size 内存块大小（字节）
   * @note 支持两种场景：
   *       1. start_ts <= end_ts：正常连续时间范围（如从ts=2到ts=5）
   *       2. start_ts > end_ts：跨循环的时间范围（如从ts=5到ts=2，需覆盖0~2和5~末尾）
   */
  auto update_lmem_req = [&lmem_req, &timestep_num](int64_t start_ts,
                                                    int64_t end_ts,
                                                    int64_t lmem_size) {
    if (start_ts <= end_ts) {
      // 处理连续时间范围：累加内存块大小到[start_ts, end_ts]的每个时间步
      for (int64_t ts = start_ts; ts <= end_ts; ++ts) {
        lmem_req[ts] += lmem_size;
      }
    } else {
      // 处理跨循环时间范围：先累加[0, end_ts]，再累加[start_ts, 最后一个时间步]
      for (int64_t ts = 0; ts <= end_ts; ++ts) {
        lmem_req[ts] += lmem_size;
      }
      for (int64_t ts = start_ts; ts <= timestep_num - 1; ++ts) {
        lmem_req[ts] += lmem_size;
      }
    }
  };

  // 遍历所有LMEM内存块，更新每个时间步的总内存需求
  for (auto iter = lmem_buffer.begin(); iter != lmem_buffer.end(); ++iter) {
    int64_t start_ts = iter->second.start_ts;  // 内存块起始时间步
    int64_t end_ts = iter->second.end_ts;      // 内存块结束时间步
    // 累加当前内存块大小到对应时间步的需求中
    update_lmem_req(start_ts, end_ts, (iter->second).size);
  }

  // 对内存需求数组按降序排序（最大值排在首位）
  std::stable_sort(lmem_req.begin(), lmem_req.end(), std::greater<int64_t>());
  // 计算最大内存需求对应的分片数：向上取整（总需求 / 硬件LMEM容量）
  return ceiling_func(lmem_req[0], Arch::LMEM_BYTES);
}
```

### 6.get_buffer_size

#### 1.功能分析

函数 `get_buffer_size` 用于计算特定值（`Value`）在给定分组类型和张量信息下所需的缓冲区大小。该值可以是权重、动态权重或普通张量，函数会根据其类型、是否允许分片以及所属操作的分片信息，调用硬件相关接口计算对应的缓冲区大小，为内存分配和分片策略提供依据。

#### 2.代码原理与逻辑

1. 初始化与维度解析：首先初始化缓冲区大小为 0，解析输入值（`v`）的 NCDHW 维度（批数、通道数、深度、高度、宽度），这些维度是计算内存大小的基础。
2. 权重分片许可判断：对于权重类型的值，检查其是否允许分片（通过权重操作的 `getAllowSplit` 接口），用于后续区分不同的内存计算逻辑。
3. 分情况计算缓冲区大小：

   - 不可分片的权重：根据分组类型（如 `GROUP_SMALL_C`），分别调用张量或权重的内存计算接口。
   - 动态权重：直接使用权重内存计算接口。
   - 普通张量或可分片权重：根据所属操作的分片信息，获取最大分片维度，再调用张量内存计算接口得到单分片的缓冲区大小。

#### 3.get_buffer_size 代码

```cpp
/**
 * @brief 计算给定值（Value）在特定分组类型和张量信息下所需的缓冲区大小
 * @param v 待计算缓冲区大小的值（可能是权重、动态权重或普通张量）
 * @param ti 张量信息（包含分片信息、EU对齐等属性）
 * @param group_type 分组类型（如GROUP_SMALL_C等，影响维度解析和内存计算方式）
 * @param owner_op 该值所属的操作（用于获取对应的分片信息）
 * @return 计算得到的缓冲区大小（字节）
 */
int64_t get_buffer_size(Value v, tensor_info_t &ti, group_type_t group_type,
                        Operation *owner_op) {
  int64_t buf_size = 0;  // 初始化缓冲区大小为0

  // 解析值v的NCDHW维度（批数n、通道数c、深度d、高度h、宽度w）
  // 维度解析方式受分组类型group_type影响
  int64_t n, c, d, h, w;
  module::getNCDHW(v, n, c, d, h, w, group_type);

  bool allow_split = false;  // 标记权重是否允许分片
  // 若该值是权重，检查其是否允许分片
  if (module::isWeight(v)) {
    // 将值的定义操作转换为权重操作（top::WeightOp）
    auto weight_op = dyn_cast<top::WeightOp>(v.getDefiningOp());
    // 若权重操作的允许分片属性存在（非空），则标记为允许分片
    if (weight_op.getAllowSplit() != std::nullopt) {
      allow_split = true;
    }
  }

  // 情况1：值是权重且不允许分片
  if (module::isWeight(v) && allow_split == false) {
    if (group_type == GROUP_SMALL_C) {
      // 对于小通道分组（GROUP_SMALL_C），使用张量内存计算接口（考虑EU对齐）
      buf_size = Arch::get_tensor_lmem_bytes(v, n, c, d, h, w, ti.eu_align);
    } else {
      // 其他分组类型，使用权重内存计算接口（考虑EU对齐）
      buf_size = Arch::get_weight_lmem_bytes(v, group_type, ti.eu_align);
    }
  }
  // 情况2：值是动态权重（TODO：需要进一步验证逻辑）
  else if (module::isDynWeight(v)) {
    // 动态权重使用权重内存计算接口（考虑EU对齐）
    buf_size = Arch::get_weight_lmem_bytes(v, group_type, ti.eu_align);
  }
  // 情况3：普通张量或允许分片的权重
  else {
    // 声明分片维度变量（各维度的分片大小）
    int64_t nslice, cslice, hslice, dslice, wslice;
    // 获取张量信息中的分片信息（默认使用全局分片信息）
    auto si = ti.slice_info;
    // 若存在所属操作（owner_op），则使用该操作对应的分片信息
    if (owner_op) {
      si = ti.slice_infos[owner_op];
    }
    // 从分片信息中获取各维度的最大分片大小
    get_max_slice_nchdw(si, nslice, cslice, hslice, dslice, wslice);
    // 根据最大分片维度，使用张量内存计算接口得到缓冲区大小（考虑分组类型和EU对齐）
    buf_size = Arch::get_tensor_lmem_bytes(v, nslice, cslice, hslice, dslice,
                                           wslice, group_type, ti.eu_align);
  }

  return buf_size;
}
```

### 7.get_out_slice_info

#### 功能分析

函数 `get_out_slice_info` 用于根据给定的分片配置（`shape_secs`）、张量各维度大小（`n/c/h/d/w`）和位宽（`bitwidth`），计算输出张量在每个维度上的具体分片信息（包含每个分片的起始索引和长度）。这些分片信息需满足硬件对齐要求（如 `ALIGN_4N`）和硬件资源约束（如 NPU 数量），为张量的并行处理（如多 NPU 分配、分片计算）提供基础。

#### 代码原理与逻辑

1. 初始化分片信息结构：创建 `slice_info_t` 类型对象，用于存储 n、c、h、d、w 五个维度的分片细节（每个分片的起始索引和长度）。
2. N 维度分片计算：

   - 根据 `shape_secs.nsecs`（N 维度分片数）和位宽计算对齐要求（`n_align`）。
   - 若满足硬件对齐条件（`ALIGN_4N` 且 `n_align≠1`），则按对齐后的步长分配分片，确保每个分片大小符合对齐要求。
   - 否则，使用通用分片分配函数 `slice_distributor` 分配分片。
3. C 维度分片计算：

   - 结合硬件 NPU 数量（`Arch::NPU_NUM`）和 `shape_secs.csecs`（C 维度分片数），先计算每个 NPU 分配的 C 维度大小（`c_per_npu`）。
   - 将每个 NPU 的 C 维度进一步拆分为 `csecs` 个分片，通过整除和取余处理，确保分片均匀分配（余数优先分配给前几个分片）。
   - 计算每个分片的起始索引和长度，确保不超过总维度大小。
4. H/D/W 维度分片计算：直接使用通用分片分配函数 `slice_distributor`，根据各自的分片数（`shape_secs.hsecs/dsecs/wsecs`）和维度大小分配分片。

#### 3.get_out_slice_info 代码

```cpp
/**
 * @brief 计算输出张量各维度的分片信息（每个分片的起始索引和长度）
 * @param shape_secs 各维度的分片数配置（nsecs/csecs/hsecs/dsecs/wsecs）
 * @param n/c/h/d/w 张量各维度的总大小（N：批数，C：通道数，H：高度，D：深度，W：宽度）
 * @param bitwidth 张量数据的位宽（用于计算对齐要求）
 * @return 包含各维度分片细节的slice_info_t结构
 */
slice_info_t get_out_slice_info(const shape_secs_t &shape_secs, int64_t n,
                                int64_t c, int64_t h, int64_t d, int64_t w,
                                int64_t bitwidth) {
  slice_info_t slice_info;  // 存储各维度分片信息的结构（含起始索引和长度）
  int64_t secs, idx, slice, step;  // 临时变量：分片数、起始索引、分片长度、步长

  // -------------------------- N维度分片计算 --------------------------
  secs = shape_secs.nsecs;  // N维度的分片数
  // 计算N维度的对齐单位：32位对齐下，位宽决定每个对齐单位包含的元素数（如16bit→32/16=2元素对齐）
  int64_t n_align = 32 / bitwidth;

  // 若硬件要求4N对齐且对齐单位不为1（需特殊处理对齐）
  if (Arch::ALIGN_4N && n_align != 1) {
    // 计算每个分片的步长：先按分片数粗略分配，再按n_align向上对齐
    step = align_up(ceiling_func(n, secs), n_align);
    // 遍历每个分片，计算起始索引和长度
    for (int64_t i = 0; i < secs; ++i) {
      // 起始索引：第一个分片从0开始，后续分片=上一个分片的起始索引+上一个分片长度
      idx = i == 0 ? 0 : idx + slice;
      // 分片长度：不超过剩余元素数，且不超过步长（确保对齐）
      slice = (n - idx) > step ? step : (n - idx);
      // 记录N维度第i个分片的起始索引和长度
      slice_info.n.emplace_back(slice_pair_t(idx, slice));
    }
  } else {
    // 无需特殊对齐时，使用通用分片分配函数分配N维度分片
    slice_distributor(slice_info.n, n, shape_secs.nsecs);
  }

  // -------------------------- C维度分片计算 --------------------------
  auto npu_num = Arch::NPU_NUM;  // 硬件的NPU数量（用于并行分配）
  secs = shape_secs.csecs;       // C维度的分片数

  // 计算每个NPU分配的C维度大小（向上取整，确保覆盖所有元素）
  int64_t c_per_npu = ceiling_func(c, npu_num);
  // 每个NPU的C维度再分为secs个分片：计算商（基础分片大小）和余数（需额外分配的部分）
  int64_t c_per_npu_div_secs = c_per_npu / secs;
  int64_t c_per_npu_mod_secs = c_per_npu % secs;

  // 遍历每个C维度分片，计算起始索引和长度
  for (int64_t i = 0; i < secs; ++i) {
    // 标记当前分片是否需要分配余数部分（前c_per_npu_mod_secs个分片多分配1个元素）
    bool extra = c_per_npu_mod_secs > i;
    // 步长：每个分片的基础大小（商）+ 余数（1或0），再乘以NPU数量（跨NPU并行）
    int64_t step = (c_per_npu_div_secs + extra) * npu_num;
    // 起始索引：根据商和余数计算，乘以NPU数量（跨NPU偏移）
    int64_t idx =
        (c_per_npu_div_secs * i + (extra ? i : c_per_npu_mod_secs)) * npu_num;

    // 分片长度：不超过剩余元素数（确保不越界）
    int64_t slice = std::min(step, c - idx);
    assert(idx < c);  // 断言：起始索引必须小于总长度（避免逻辑错误）
    // 记录C维度第i个分片的起始索引和长度
    slice_info.c.emplace_back(slice_pair_t(idx, slice));
  }

  // -------------------------- H/D/W维度分片计算 --------------------------
  // H维度：使用通用分片分配函数，按shape_secs.hsecs分片数分配
  slice_distributor(slice_info.h, h, shape_secs.hsecs);
  // D维度：使用通用分片分配函数，按shape_secs.dsecs分片数分配
  slice_distributor(slice_info.d, d, shape_secs.dsecs);
  // W维度：使用通用分片分配函数，按shape_secs.wsecs分片数分配
  slice_distributor(slice_info.w, w, shape_secs.wsecs);

  return slice_info;
}
```

### 8.inc_slice_num

#### 功能分析

函数 `inc_slice_num` 是一个分片数递增控制函数，用于按固定优先级顺序（N→C→D→H→W）逐步增加各维度的分片数量（`n_slice/c_slice/d_slice/h_slice/w_slice`），同时确保每个维度的分片数不超过硬件或策略允许的最大限制（`max_shape_secs`）。当所有维度均已达到最大分片数时，返回失败，否则返回成功。

#### 代码原理与逻辑

1. 优先级顺序定义：函数严格遵循 N 维度 → C 维度 → D 维度 → H 维度 → W 维度 的优先级，即优先增加优先级更高的维度的分片数，只有当前维度达到最大限制时，才尝试增加下一个维度的分片数。
2. 分片数递增规则：

   - 依次检查每个维度的当前分片数（`n_slice`→`c_slice`→…→`w_slice`）是否小于该维度的最大分片数（`max_shape_secs` 中对应字段）。
   - 若某个维度未达上限，则将该维度的分片数加 1，直接返回成功（不继续检查后续维度）。
3. 终止条件：当所有维度的分片数均已达到各自的最大限制时，无法继续递增，返回失败。

#### inc_slice_num 代码

```java
/**
 * @brief 按固定优先级顺序递增各维度分片数，确保不超过最大分片限制
 * @param n_slice [输入/输出] N维度（批数）当前分片数，递增后更新
 * @param c_slice [输入/输出] C维度（通道数）当前分片数，递增后更新
 * @param d_slice [输入/输出] D维度（深度）当前分片数，递增后更新
 * @param h_slice [输入/输出] H维度（高度）当前分片数，递增后更新
 * @param w_slice [输入/输出] W维度（宽度）当前分片数，递增后更新
 * @param max_shape_secs 各维度的最大分片数限制（硬件/策略允许的上限）
 * @return 成功递增任一维度分片数返回true；所有维度均达上限返回false
 */
static bool inc_slice_num(int &n_slice, int &c_slice, int &d_slice,
                          int &h_slice, int &w_slice,
                          const shape_secs_t &max_shape_secs) {
  // 优先级1：N维度（批数），若未达最大分片数则递增
  if (n_slice < max_shape_secs.nsecs) {
    n_slice++;  // N维度分片数+1
  }
  // 优先级2：C维度（通道数），仅当N维度达上限时才检查
  else if (c_slice < max_shape_secs.csecs) {
    c_slice++;  // C维度分片数+1
  }
  // 优先级3：D维度（深度），仅当N、C维度均达上限时才检查
  else if (d_slice < max_shape_secs.dsecs) {
    d_slice++;  // D维度分片数+1
  }
  // 优先级4：H维度（高度），仅当N、C、D维度均达上限时才检查
  else if (h_slice < max_shape_secs.hsecs) {
    h_slice++;  // H维度分片数+1
  }
  // 优先级5：W维度（宽度），仅当前4个维度均达上限时才检查
  else if (w_slice < max_shape_secs.wsecs) {
    w_slice++;  // W维度分片数+1
  }
  // 所有维度均已达到最大分片数，无法继续递增
  else {
    return false;
  }

  // 成功递增任一维度的分片数，返回true
  return true;
}
```

## 5./LayerGroup/BasicTimeStep.cpp

### gen_hold_coeff()

##### 核心功能

该函数的核心功能是识别并记录需要在本地内存（LMEM）中持续保持的张量，并将其首次加载到 LMEM 的时间步存储在 `hold_coeff_` 中。这些张量不会被中间内存管理操作释放，以确保后续计算能直接访问，避免重复加载带来的性能开销。

##### 逻辑流程

1. 初始化：清空 `hold_coeff_`（存储 < 张量，首次加载时间步 > 的映射），避免历史数据干扰。
2. 遍历时间步：按顺序处理每个时间步（`ts`），获取该时间步的 GDMA 操作列表（`gdma_field`）——GDMA 操作负责张量在外部内存与 LMEM 之间的传输。
3. 筛选加载操作：仅关注 "加载" 模式（`TIMESTEP_LOAD`）的 GDMA 操作（即从外部内存加载到 LMEM 的张量）。
4. 识别需保持的张量：
   - 不可拆分的权重张量：若张量是由 `top::WeightOp` 定义的权重，且未标记 "允许拆分"（`allow_split=false`），则需在 LMEM 中保持（避免拆分导致的访问效率下降）。
   - 显式标记保持的张量：若张量信息中 `hold_in_lmem` 为 `true`（用户或上层逻辑显式指定），则需在 LMEM 中保持。
5. 记录时间步：对符合条件的张量，将其首次加载到 LMEM 的时间步（`ts`）记录到 `hold_coeff_` 中。

##### 核心原理

- 本地内存（LMEM）特性：LMEM 是靠近计算单元（如 TPU）的高速内存，访问速度远高于外部内存（如 DRAM），但容量通常较小。因此需要优先保留高频访问或不可拆分的张量，减少与外部内存的交互。
- 保持策略：

  - 不可拆分的权重张量（如卷积核、嵌入表）若被拆分存储，会增加访问次数和延迟，因此需整体保存在 LMEM 中。
  - 显式标记 `hold_in_lmem` 的张量（如中间结果需被多次复用），保持在 LMEM 中可避免重复加载，提升效率。
- 时间步记录：`hold_coeff_` 中存储的时间步代表张量首次进入 LMEM 的时刻，后续内存管理逻辑可据此判断该张量需从此时开始在 LMEM 中保留，直至不再被使用。

##### gen_hold_coeff()代码

```cpp
/**
 * @brief 生成并设置需要在本地内存（LMEM）中保持的系数（张量）的时间步信息
 * @details 该方法遍历所有时间步的GDMA操作，识别需要在LMEM中持续保留的张量（如不可拆分的权重、显式标记需保持的张量），
 *          并记录其首次加载到LMEM的时间步，用于后续内存管理（避免被过早释放）
 */
void BasicTimeStep::gen_hold_coeff() {
  // 清空现有需要保持的系数记录（初始化）
  this->hold_coeff_.clear();

  Value v;  // 张量对象（LLVM IR中的值，代表计算图中的张量）
  // 遍历所有时间步（ts为时间步索引）
  for (size_t ts = 0; ts < this->get_timestep_num(); ++ts) {
    // 获取当前时间步的GDMA0操作字段（存储该时间步的内存访问操作）
    const GdmaTsField &gdma_field = this->timestep_table_[ts].gdma0_ts_field;
    // 遍历当前时间步的所有GDMA操作
    for (size_t i = 0; i < gdma_field.size(); ++i) {
      v = gdma_field[i].first;  // 获取当前GDMA操作对应的张量
      auto &tensor_info = gdma_field[i].second;  // 获取该张量的详细信息

      // 仅处理"加载"模式的GDMA操作（即从外部内存加载到LMEM的操作）
      if (tensor_info.mode == TIMESTEP_LOAD) {
        // 情况1：若该张量是权重操作（WeightOp）定义的权重张量
        if (auto src_op = dyn_cast_or_null<top::WeightOp>(v.getDefiningOp())) {
          bool allow_split = false;  // 标记权重是否允许拆分存储
          // 检查权重操作是否有"允许拆分"的属性（若属性存在则允许拆分）
          if (src_op.getAllowSplitAttr() != nullptr) {
            allow_split = true;
          }
          // 若权重不允许拆分，则需要在LMEM中保持，记录其首次加载的时间步
          if (allow_split == false) {
            this->hold_coeff_[v] = ts;
          }
        }
        // 情况2：若张量信息中显式标记了"需要在LMEM中保持"（hold_in_lmem为true）
        if (tensor_info.hold_in_lmem) {
          this->hold_coeff_[v] = ts;  // 记录其首次加载的时间步
        }
      }
    }
  }
}
```

## 6./LayerGroup/LmemAllocator.cpp

### 1.assignLmemAddrWithSecs

##### 核心功能

该函数是 LMEM 地址分配的高级策略层，核心功能是：通过搜索最优的张量形状分段方式（`shape_secs`，即 N/C/D/H/W 各维度的分段数），在满足内存约束（如 bank 冲突）的前提下最小化计算周期成本，最终调用 `assignLmemAddr` 完成实际的地址分配。它是连接 “张量分段策略优化” 与 “物理地址分配” 的关键接口。

##### 逻辑流程

函数逻辑可分为分段策略优化和分配执行与验证两大阶段：

##### （1）分段策略优化（核心阶段）

- 初始化与参数准备：

  - 获取层组的最大分段数（`max_shape_secs_`）和最小总分段数（`min_total_secs_`），定义搜索范围。
  - 根据芯片类型（CV18xx/BM168x）初始化周期计算器（`cycle_calculator_`），用于评估不同分段策略的计算成本（周期数）。
  - 若不允许 bank 冲突，调用 `update_data_split` 优化初始分段策略，减少潜在冲突。
- 搜索策略选择：

  - 暴力搜索：若开启调试模式或环境变量，遍历所有可能的 `shape_secs` 组合（精度最高但耗时最长）。
  - 缓存复用：若层组启用缓存且缓存有效，直接复用历史最优分段策略（跳过搜索，提升效率）。
  - 快速搜索与多核优化：默认使用 `sc_method_quick_search` 高效寻找候选分段，若芯片支持多核，调用 `sc_method_multi_core` 系列方法进一步优化（平衡效率与精度）。
- 最优策略确定：通过搜索得到最小计算成本（`min_group_costs_`）对应的最优分段策略（`min_shape_secs_`）。

##### （2）分配执行与验证

- 将最优分段策略（`min_shape_secs_`）赋值给输出参数 `shape_secs`。
- 调用 `time_step->assignTimeStep` 基于最优分段分配时间步，验证分段的有效性。
- 关闭调试器（减少干扰），调用 `assignLmemAddr` 完成实际的 LMEM 地址分配，之后恢复调试状态。
- 若所有步骤成功，返回 `true`；任何环节失败则返回 `false`。

##### 核心原理

- 形状分段的意义：张量（如特征图）通常维度较大（如 N=4、C=512、H=224、W=224），无法一次性放入 LMEM。通过 `shape_secs` 将各维度分成多段（如 H 分成 4 段），可实现 “分块加载 - 计算 - 释放” 的流水线，平衡内存占用与计算效率。
- 成本优化目标：分段数越多，单次内存占用越小，但分块切换的开销（如数据搬运、时间步调度）越大；分段数越少，内存压力越大，可能导致 bank 冲突。函数通过 `cycle_calculator_` 评估不同分段的总周期成本（计算 + 搬运），选择最优平衡点。
- 硬件适配：不同芯片（CV18xx/BM168x）的计算单元（如 TPU）架构不同，对分块大小的敏感度不同，因此需要针对性的周期计算器和搜索策略。
- 工程优化：通过缓存复用（避免重复搜索）、多核并行搜索（加速策略探索）等工程手段，在保证优化效果的同时提升执行效率。

##### assignLmemAddrWithSecs 代码

```cpp
/**
 * @brief 结合形状分段（shape_secs）为LMEM分配地址，并通过搜索最优分段策略优化分配效果
 * @details 该函数是LMEM地址分配的高级接口，核心是通过搜索最优的张量形状分段方式（nsecs/csecs等），
 *          在满足内存约束（如bank冲突）的前提下，最小化计算周期成本，最终调用assignLmemAddr完成地址分配。
 * @param lg_info 层组信息，包含层组配置、缓存信息等
 * @param time_step 时间步对象指针，用于管理时间步和内存缓冲区
 * @param shape_secs 张量形状的分段参数（n/c/d/h/w维度的分段数），作为输入和输出（返回最优分段）
 * @param allow_bank_conflict 是否允许内存bank冲突
 * @return 分配成功返回true，失败返回false
 */
bool LmemAllocator::assignLmemAddrWithSecs(const LgInfo &lg_info,
                                           BasicTimeStepPtr &time_step,
                                           shape_secs_t &shape_secs,
                                           bool allow_bank_conflict) {
  // 获取全局调试器实例（用于控制调试日志和搜索策略）
  auto &lg_debugger = LgDebugger::getInstance();
  // 存储操作与高度维度分段数的映射（用于后续分段优化）
  std::vector<std::pair<Operation *, int>> vec_op_hsecs;
  // 获取当前层组的最大形状分段数（作为搜索上限）
  max_shape_secs_ = get_group_max_secs(lg_info, vec_op_hsecs);

  // 若不允许bank冲突，尝试更新数据分段策略以减少冲突
  if (!allow_bank_conflict) {
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO("update_data_split",
                                            "call_function",
                                            "尝试为当前层组找到更优的shape_secs以减少冲突")
                   << "\n";
    });
    auto shape_secs_update = shape_secs;  // 临时存储更新后的分段参数
    // 调用update_data_split优化分段，若成功则更新shape_secs
    if (update_data_split(time_step, lg_info, shape_secs_update, options_)) {
      shape_secs = shape_secs_update;
    }
    // 输出更新后的分段参数调试信息
    GROUP_DEBUG_WITH_TYPE("shape_secs", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                          "update_data_split", "stamp",
                          "显示update_data_split后的shape_secs")
                   << LOG_KV("nsecs", shape_secs.nsecs)    // N维度分段数
                   << LOG_KV("csecs", shape_secs.csecs)    // C维度分段数
                   << LOG_KV("dsecs", shape_secs.dsecs)    // D维度分段数
                   << LOG_KV("hsecs", shape_secs.hsecs)    // H维度分段数
                   << LOG_KV("wsecs", shape_secs.wsecs)    // W维度分段数
                   << "\n";
    });
  }

  // 获取最小总分段数（作为搜索下限）
  min_total_secs_ = get_split_max_secs(time_step);
  // 存储不同分段策略的成本（周期数）
  std::vector<int64_t> group_costs;
  // 存储候选的分段策略空间
  std::vector<shape_secs_t> shape_secs_space;

  // 根据芯片类型初始化周期计算器（用于评估不同分段策略的计算成本）
  if (module::isCV18xx()) {
    Cv18xxCycleCalculator *cyc_ptr = new Cv18xxCycleCalculator();
    cycle_calculator_ = std::shared_ptr<CycleCalculator>(cyc_ptr);
  } else {
    Bm168xCycleCalculator *cyc_ptr = new Bm168xCycleCalculator();
    cycle_calculator_ = std::shared_ptr<CycleCalculator>(cyc_ptr);
  }

  // 判断是否使用暴力搜索策略（遍历所有可能的分段组合，用于调试或精度优先场景）
  bool use_brute_force = false;
  if (lg_debugger.get_sc_method() == SC_BRUTE_FORCE &&
      lg_debugger.is_conditional_debug_group(lg_info.func_start_idx,
                                             lg_info.func_end_idx)) {
    use_brute_force = true;
  }

  // 若开启暴力搜索（环境变量或调试设置），则尝试所有可能的shape_secs
  if (getenv("SC_BRUTE_FORCE") || use_brute_force) {
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                          "sc_method_brute_force", "call_function",
                          "暴力搜索：尝试所有可能的shape_secs（用于调试）")
                   << "\n";
    });
    sc_method_brute_force(lg_info, shape_secs, allow_bank_conflict, time_step);
  }
  // 若层组使用缓存且缓存的分段策略有效，直接复用缓存结果（跳过搜索）
  else if (lg_info.use_cache && lg_info.shape_secs.nsecs != 0 &&
           !getenv("RESEARCH_SHAPE_SECS")) {
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO("use_layergroup_cache", "stamp",
                                            "使用缓存的group_cost和shape_secs，跳过搜索")
                   << "\n";
    });
    min_group_costs_ = lg_info.group_cost;  // 复用缓存的最小成本
    min_shape_secs_ = lg_info.shape_secs;   // 复用缓存的最优分段
  }
  // 否则使用快速搜索策略，结合多核优化寻找最优分段
  else {
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs()
          << DEBUGGER_DEFAULT_INFO(
                 "sc_method_quick_search", "call_function",
                 "快速搜索：高效寻找有效的shape_secs")
          << "\n";
    });
    sc_method_quick_search(lg_info, shape_secs, allow_bank_conflict, time_step);

    // 若芯片支持多核，调用多核优化的搜索方法（v1/v2/v3为不同版本的优化策略）
    if (module::getCoreNum() > 1) {
      sc_method_multi_core(lg_info, shape_secs, allow_bank_conflict, time_step);
      sc_method_multi_core_v2(lg_info, shape_secs, allow_bank_conflict,
                              time_step);
      sc_method_multi_core_v3(lg_info, shape_secs, allow_bank_conflict,
                              time_step);
    }
  }

  // 若未找到有效的分段策略（最小成本为-1），返回失败
  if (min_group_costs_ == -1) {
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO("assignLmemAddrWithSecs_finish",
                                            "failed",
                                            "结合分段的LMEM地址分配失败")
                   << "\n";
    });
    return false;
  }

  // 将最优分段策略赋值给输出参数shape_secs
  shape_secs = min_shape_secs_;

  // 输出找到的最优分段策略调试信息
  GROUP_DEBUG_WITH_TYPE("shape_secs", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "find_valid_shape_secs", "stamp",
                        "显示搜索到的有效shape_secs")
                 << LOG_KV("nsecs", shape_secs.nsecs)
                 << LOG_KV("csecs", shape_secs.csecs)
                 << LOG_KV("dsecs", shape_secs.dsecs)
                 << LOG_KV("hsecs", shape_secs.hsecs)
                 << LOG_KV("wsecs", shape_secs.wsecs)
                 << LOG_KV("最小成本", min_group_costs_) << "\n";
  });

  // 根据最优分段策略分配时间步，检查有效性
  auto status = time_step->assignTimeStep(lg_info, shape_secs, true);
  if (!status) {
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO("assignLmemAddrWithSecs_finish",
                                            "failed",
                                            "基于最优shape_secs的时间步分配失败")
                   << "\n";
    });
    return false;
  }

  // 关闭调试器，调用assignLmemAddr完成LMEM地址分配，之后恢复调试状态
  lg_debugger.set_do_debug(false);
  status = assignLmemAddr(lg_info, time_step, shape_secs, allow_bank_conflict);
  lg_debugger.set_do_debug(true);

  // 检查地址分配结果
  if (!status) {
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO("assignLmemAddrWithSecs_finish",
                                            "failed",
                                            "基于最优shape_secs的LMEM地址分配失败")
                   << "\n";
    });
    return false;
  }

  // 输出成功日志，返回成功
  GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO("assignLmemAddrWithSecs_finish",
                                          "success",
                                          "结合分段的LMEM地址分配成功")
                 << LOG_KV("最小组成本", min_group_costs_)
                 << LOG_KV("shape_secs",
                           llvm::format("%d,%d,%d,%d,%d", shape_secs.nsecs,
                                        shape_secs.csecs, shape_secs.dsecs,
                                        shape_secs.hsecs, shape_secs.wsecs))
                 << "\n";
  });
  return true;
}
```

### 2.assignLmemAddr

##### 核心功能

该函数是本地内存（LMEM）分配的核心实现，负责为计算图中所有需要使用 LMEM 的缓冲区分配物理地址，同时处理以下关键任务：

- 动态调整张量的 LMEM 驻留状态（若总占用超过 LMEM 容量，取消驻留以避免溢出）；
- 确保地址分配满足硬件约束（如对齐要求、bank 冲突控制）；
- 优化内存利用率（通过排序和最佳适配策略选择地址）；
- 最终返回分配结果（成功 / 失败）并记录调试与性能日志。

##### 逻辑流程

该函数的逻辑可分为初始化与检查、主分配循环、结果处理三大部分：

##### （1）初始化与检查

- 基础信息更新：调用 `time_step->update_all_mem_buffer_size` 填充缓冲区的 `start_ts`、`end_ts` 和 `align_bytes`，为后续分配做准备。
- 单循环判断：通过 `shape_secs` 判断是否为单循环模式（形状未分段），单循环下禁止张量驻留 LMEM。
- 驻留状态检查：计算需驻留 LMEM 的张量总大小，若超过 LMEM 容量，则取消所有张量的驻留标记，并重置其切片策略（改为全量加载，不依赖 LMEM 驻留）。
- 数据结构初始化：初始化待分配缓冲区列表（`membuf_list`）、可用内存空间管理结构（`buffer_avail_space`）、冲突跟踪堆（`npu_membuf_heap`/`gdma_membuf_heap`）。

##### （2）主分配循环（核心）

循环遍历 `membuf_list`，为每个缓冲区分配地址，直到列表为空：

- 冲突参数更新：基于当前待分配缓冲区，更新冲突堆中的冲突关系（避免同时访问同一 bank 的缓冲区冲突）。
- 缓冲区排序：按冲突程度、大小、起始时间步排序，优先处理冲突高、尺寸大、早开始的缓冲区（提升利用率并减少冲突）。
- 地址选择：

  - 首次分配直接从地址 0 开始；
  - 非首次分配通过 `global_find_avail_lmem_localtion` 寻找满足对齐和冲突约束的最小可用地址。
- 分配结果处理：

  - 若分配失败（无可用地址），打印失败信息并返回 `false`；
  - 若成功，记录地址，更新已占用 LMEM 大小，从待分配列表和冲突堆中移除该缓冲区。

##### （3）结果处理

- 分配完成后，设置总占用 LMEM 大小，调用 `assignL2memAddr` 分配 L2 内存地址（若有）；
- 打印成功日志，返回 `true`。

##### 核心原理

- 内存驻留动态调整：通过预检查驻留张量总大小与 LMEM 容量的关系，动态取消驻留标记，避免内存溢出，平衡利用率与可行性。
- bank 冲突控制：利用冲突堆跟踪 NPU/GDMA 操作的缓冲区冲突关系，分配时避开冲突 bank（若 `allow_bank_conflict` 为 `false`），或允许冲突以提升利用率（`true` 时）。
- 地址分配策略：采用 “最小地址优先” 的最佳适配策略，结合缓冲区排序（冲突、大小、时间步），在满足硬件约束的前提下最大化内存利用率。
- 硬件约束适配：严格遵循 LMEM 的 bank 架构（`Arch::LMEM_BANKS`）、地址对齐（`align_bytes`）、总容量（`Arch::LMEM_BYTES`）等硬件参数，确保分配的地址可被硬件正确访问。

##### assignLmemAddr 代码

```cpp
/**
 * @brief 为本地内存（LMEM）中的所有缓冲区分配地址，并处理内存冲突与容量约束
 * @details 该函数是LMEM内存分配的核心实现，负责为lmem_buffer_中的每个内存缓冲区分配物理地址，
 *          计算缓冲区大小（size），并确保分配满足硬件约束（如地址对齐、bank冲突控制）。
 *          同时会根据内存容量动态调整张量的本地内存驻留状态，最终返回分配结果。
 * @param lg_info 层组信息，包含层组配置及硬件相关参数
 * @param time_step 时间步对象指针，存储缓冲区的基础信息（start_ts、end_ts等已预填充）
 * @param shape_secs 张量形状的分段信息，用于计算缓冲区大小和切片策略
 * @param allow_bank_conflict 是否允许内存bank冲突（true时允许以性能换取利用率）
 * @return 分配成功返回true，失败返回false
 */
bool LmemAllocator::assignLmemAddr(const LgInfo &lg_info,
                                   BasicTimeStepPtr &time_step,
                                   const shape_secs_t &shape_secs,
                                   bool allow_bank_conflict) {
  /**
   * assignLmemAddr函数用于为lmem_buffer_中定义的每个内存缓冲区分配LMEM地址
   *
   * lmem_buffer_是map<mem_buffer_key_t, mem_buffer_value_t>类型，其中：
   * mem_buffer_value_t包含start_ts（起始时间步）、end_ts（结束时间步）、addr（地址）、
   * size（大小）、align_bytes（对齐字节数），前三者已由update_all_mem_buffer_size填充，
   * 本函数需填充addr和size。
   */
  // 记录性能分析日志，标记函数开始执行
  PROFILE_LOG("assignLmemAddr", true);

  // 调试日志：说明更新缓冲区基础信息的过程
  GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "update_all_mem_buffer_size", "call_function",
                        "更新lmem_buffer_中的信息：先通过update_all_mem_buffer_size填充"
                        "start_ts、end_ts和align_bytes，再计算size")
                 << "\n";
  });
  // 调用时间步对象的方法，更新所有缓冲区的size、start_ts、end_ts和align_bytes
  time_step->update_all_mem_buffer_size(lg_info);

  // 判断是否为单循环模式（形状各维度均未分段）
  bool one_loop =
      (shape_secs.nsecs == 1 && shape_secs.hsecs == 1 &&
       shape_secs.csecs == 1 && shape_secs.dsecs == 1 && shape_secs.wsecs == 1);

  // 确定是否允许张量驻留在LMEM中（单循环模式下不允许，避免过度占用）
  bool allow_hold_in_lmem = !one_loop;
  if (allow_hold_in_lmem) {
    // 调试日志：说明判断是否允许张量驻留的逻辑
    GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                          "allow_hold_in_lmem_judgment", "stamp",
                          "若驻留LMEM的张量总大小超过LMEM容量，则禁止所有张量驻留")
                   << "\n";
    });
    // 获取当前所有LMEM缓冲区信息
    const MemBuff &lmem_buffer = time_step->get_lmem_buffer();
    int64_t lmem_size_hold_in_lmem = 0;
    // 计算所有需要驻留LMEM的张量总大小
    for (auto iter = lmem_buffer.begin(); iter != lmem_buffer.end(); ++iter) {
      if (iter->first.type != LMEM_OPERATION &&
          time_step->is_tensor_hold_in_lmem(iter->first.value)) {
        lmem_size_hold_in_lmem += iter->second.size;
      }
    }

    // 若总大小超过LMEM容量，禁止张量驻留并调整其切片信息
    allow_hold_in_lmem = lmem_size_hold_in_lmem < Arch::LMEM_BYTES;
    if (!allow_hold_in_lmem) {
      int64_t n, c, d, h, w;
      // 遍历所有时间步的张量，取消驻留标记并重置切片（全量加载）
      for (size_t ts = 0; ts < time_step->get_timestep_num(); ++ts) {
        auto &cur_ts_tensors = time_step->getTensors(ts);
        for (auto &tensor : cur_ts_tensors) {
          tensor_info_t &ti = tensor.second;
          if (ti.mode == TIMESTEP_LOAD) {  // 仅处理加载模式的张量
            auto in = tensor.first;
            // 特殊处理1684架构的LutOp权重（使用L2内存，跳过）
            if (module::isBM1684Family() && module::isWeight(tensor.first) &&
                llvm::any_of(tensor.first.getUsers(), [](Operation *op) {
                  return isa<tpu::LutOp>(op);
                })) {
              continue;
            }
            // 取消驻留标记
            if (time_step->is_tensor_hold_in_lmem(in)) {
              GROUP_DEBUG_WITH_TYPE("lmem_buffer", lg_info, [&]() {
                llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                                    "lmem_buffer", "stamp",
                                    "取消张量的LMEM驻留标记")
                             << LOG_KV("name", module::getName(in)) << "\n";
              });
              time_step->cancel_tensor_hold_in_lmem(in);
              // 获取张量形状，重置切片信息（全量加载，不分片）
              module::getNCDHW(in, n, c, d, h, w, lg_info.type);
              ti.slice_info.n.clear();
              ti.slice_info.c.clear();
              ti.slice_info.d.clear();
              ti.slice_info.h.clear();
              ti.slice_info.w.clear();
              // 各维度切片设置为全量范围
              for (int i = 0; i < shape_secs.nsecs; ++i) {
                ti.slice_info.n.push_back(std::make_pair(0, n));
              }
              for (int i = 0; i < shape_secs.csecs; ++i) {
                ti.slice_info.c.push_back(std::make_pair(0, c));
              }
              for (int i = 0; i < shape_secs.dsecs; ++i) {
                ti.slice_info.d.push_back(std::make_pair(0, d));
              }
              for (int i = 0; i < shape_secs.hsecs; ++i) {
                ti.slice_info.h.push_back(std::make_pair(0, h));
              }
              for (int i = 0; i < shape_secs.wsecs; ++i) {
                ti.slice_info.w.push_back(std::make_pair(0, w));
              }
            }
          }
        }
      }
    }
  }

  // 初始化待分配的缓冲区列表（membuf_list）
  std::list<MemBufSortStd> membuf_list;
  init_membuf_list(membuf_list, time_step, one_loop, allow_hold_in_lmem);

  // 初始化可用内存空间管理结构（buffer_avail_space）
  BufferAvailSpace buffer_avail_space;
  init_buffer_avail_space(buffer_avail_space, membuf_list);

  // 创建冲突堆（用于跟踪NPU和GDMA操作的缓冲区冲突关系）
  std::vector<std::set<mem_buffer_key_t *>> npu_membuf_heap;
  std::vector<std::set<mem_buffer_key_t *>> gdma_membuf_heap;
  membuf_heap_create(npu_membuf_heap, gdma_membuf_heap, membuf_list, time_step);

  // 分配相关变量：候选分配块、目标最小地址、已占用LMEM大小等
  MemBlock candidate_allocation;  // 候选分配的地址范围（起始地址，大小）
  int64_t tgt_min_address = 0;    // 目标分配的最小地址
  int64_t lmem_occupy = 0;        // 已占用的LMEM总大小
  bool is_first_alloc = true;     // 是否为首次分配
  mem_buffer_key_t recent_buffer_allocated;  // 最近分配的缓冲区
  std::list<MemBufSortStd>::iterator buflist_it;  // 缓冲区列表迭代器
  std::list<MemBufSortStd>::iterator tgt_membuf;  // 目标缓冲区迭代器

  // 调试日志：打印当前芯片的LMEM硬件参数
  GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "lmem_spec", "stamp",
                        "当前芯片的本地内存（LMEM）参数")
                 << LOG_KV("lmem_eu_bytes", Arch::EU_BYTES)  // 计算单元字节数
                 << LOG_KV("lmem_npu_num", Arch::NPU_NUM)    // NPU核心数量
                 << LOG_KV("lmem_bytes", Arch::LMEM_BYTES)    // 总容量
                 << LOG_KV("lmem_banks", Arch::LMEM_BANKS)    // bank数量
                 << LOG_KV("lmem_bank_bytes", Arch::LMEM_BANK_BYTES)  // 单bank容量
                 << "\n";
  });

  // 主循环：遍历所有待分配缓冲区，逐个分配地址
  GROUP_DEBUG_WITH_TYPE("lg_step", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "main_loop", "stamp",
                        "遍历membuf_list为所有缓冲区分配地址的主循环")
                 << "\n";
  });
  addr_assign_result_t addr_assign_result = ADDR_ALLOCATE_SUCCESS;  // 分配结果状态
  while (!membuf_list.empty()) {
    // 调试日志：打印当前迭代的分配状态
    GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO("iteration_start", "stamp", "开始迭代")
                   << LOG_KV("已占用LMEM", lmem_occupy)
                   << LOG_KV("待分配缓冲区数量", membuf_list.size())
                   << "\n";
    });

    // 初始化目标最小地址为LMEM总容量（后续寻找更小的可用地址）
    tgt_min_address = Arch::LMEM_BYTES;

    // 更新缓冲区冲突参数（基于当前待分配列表）
    update_membuf_conflict_param(npu_membuf_heap, gdma_membuf_heap,
                                 membuf_list);

    // 调试日志：打印排序前的缓冲区列表
    dump_membuf_list(lg_info, membuf_list, "membuf_list_before_sort", "stamp",
                     "排序前的缓冲区列表");

    // 按冲突程度、大小、起始时间步排序缓冲区（优先分配冲突高/大尺寸/早开始的）
    membuf_list.sort(membuf_sort_std_cmp);

    // 调试日志：打印排序后的缓冲区列表
    dump_membuf_list(lg_info, membuf_list, "membuf_list_after_sort", "stamp",
                     "排序后的缓冲区列表");

    // 步骤1：从所有候选缓冲区中寻找可分配的最小地址及对应的目标缓冲区
    GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
      llvm::dbgs() << DEBUGGER_DEFAULT_INFO("start_find_target", "stamp",
                                            "遍历缓冲区列表，寻找最小可用地址")
                   << "\n";
    });
    for (buflist_it = membuf_list.begin(); buflist_it != membuf_list.end();
         ++buflist_it) {

      // 1.1 为当前缓冲区计算候选分配地址
      if (is_first_alloc) {
        // 首次分配直接从地址0开始
        GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
          llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                              "first_allocation", "stamp",
                              "首次分配：直接从地址0开始")
                       << "\n";
        });

        // 候选地址为0，大小为缓冲区大小
        candidate_allocation =
            std::make_pair(0, time_step->get_lmem_size(buflist_it->first));

        // 若缓冲区大小超过LMEM容量，标记分配失败
        if (candidate_allocation.second > Arch::LMEM_BYTES) {
          addr_assign_result = ADDR_FIRST_ALLOCATE_FAILED;
        } else {
          tgt_min_address = 0;  // 首次分配地址为0
          tgt_membuf = buflist_it;  // 目标缓冲区为当前迭代器
        }
      } else {
        // 非首次分配：根据可用空间和冲突约束寻找地址
        // 更新可用空间和禁止使用的bank，计算候选地址
        candidate_allocation = global_find_avail_lmem_localtion(
            buffer_avail_space[buflist_it->first], buflist_it->first,
            recent_buffer_allocated, time_step, one_loop, lg_info,
            allow_bank_conflict, allow_hold_in_lmem);
        // 若未找到可用地址，标记候选分配失败
        if (candidate_allocation.first == -1) {
          addr_assign_result = ADDR_CANDIDATE_ALLOCATE_FAILED;
        }
      }

      // 调试日志：打印当前候选分配的信息
      GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
        llvm::dbgs()
            << DEBUGGER_DEFAULT_INFO(
                   "get_candidate_allocation", "intermediate_result",
                   "候选分配结果（非最终结果，首次分配除外）")
            << LOG_KV("操作类型", buflist_it->first.lmem_type_str())
            << LOG_KV("张量名称", module::getName(buflist_it->first.value))
            << LOG_KV("是否首次分配", is_first_alloc)
            << LOG_KV("时间步范围",
                      time_step->get_lmem_buffer_value(buflist_it->first).start_ts
                      << "->"
                      << time_step->get_lmem_buffer_value(buflist_it->first).end_ts)
            << LOG_KV("候选地址", llvm::format_hex(candidate_allocation.first, 8))
            << LOG_KV("大小", candidate_allocation.second);
        // 若地址有效，打印占用的bank
        if (candidate_allocation.first != -1) {
          std::set<int64_t> used_banks;
          find_used_banks(used_banks, candidate_allocation.first,
                          candidate_allocation.second);
          llvm::dbgs() << "; 使用的bank = ";
          const char *sep = "";
          for (auto bank : used_banks) {
            llvm::dbgs() << sep << bank;
            sep = ",";
          }
        }
        llvm::dbgs() << "\n";
      });

      // 1.2 首次分配或分配失败时提前退出循环
      if (is_first_alloc ||
          addr_assign_result == ADDR_CANDIDATE_ALLOCATE_FAILED) {
        is_first_alloc = false;  // 首次分配后重置标记
        break;
      }

      // 1.3 若当前候选地址更小，更新目标地址和目标缓冲区
      if (candidate_allocation.first < tgt_min_address) {
        tgt_min_address = candidate_allocation.first;
        tgt_membuf = buflist_it;
        GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
          llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                              "update_target", "stamp",
                              "更新目标地址为更小的可用地址")
                       << LOG_KV("目标最小地址", tgt_min_address)
                       << LOG_KV("操作类型", tgt_membuf->first.lmem_type_str())
                       << LOG_KV("张量名称",
                                 module::getName(tgt_membuf->first.value))
                       << "\n";
        });
      }
    }

    // 步骤2：检查分配结果，若失败则返回
    // 2.a 若存在无法分配的缓冲区，打印失败信息并返回false
    if (addr_assign_result > ADDR_ALLOCATE_SUCCESS) {
      // 打印所有分配失败的缓冲区信息
      for (auto membuf_allocated_failed = membuf_list.begin();
           membuf_allocated_failed != membuf_list.end();
           ++membuf_allocated_failed) {
        GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
          dump_lmem_assign_result(membuf_allocated_failed, lg_info, time_step,
                                  allow_bank_conflict, allow_hold_in_lmem,
                                  shape_secs, "failed",
                                  "因无法找到可用空间提前返回",
                                  -1, one_loop);
        });
      }
      GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
        llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                            "assignLmemAddr_finish", "failed",
                            "LMEM地址分配失败")
                     << LOG_KV("分配失败的缓冲区数量", membuf_list.size())
                     << LOG_KV("已使用LMEM", lmem_occupy) << "%\n";
      });
      PROFILE_LOG("assignLmemAddr", false);  // 记录性能日志结束
      return false;
    }

    // 2.b 为目标缓冲区分配地址
    addr_assign_result = ADDR_ALLOCATE_SUCCESS;
    recent_buffer_allocated = tgt_membuf->first;  // 记录最近分配的缓冲区
    time_step->set_lmem_addr(tgt_membuf->first, tgt_min_address);  // 设置地址
    // 计算缓冲区结束地址，更新已占用LMEM大小
    int64_t buffer_end =
        tgt_min_address + time_step->get_lmem_size(tgt_membuf->first);
    lmem_occupy = buffer_end > lmem_occupy ? buffer_end : lmem_occupy;

    // 调试日志：打印分配成功的信息
    GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
      dump_lmem_assign_result(
          tgt_membuf, lg_info, time_step, allow_bank_conflict,
          allow_hold_in_lmem, shape_secs, "success",
          "为缓冲区分配地址成功", tgt_min_address, one_loop);
    });

    // 从冲突堆中移除已分配的缓冲区，更新待分配列表和可用空间
    conflict_heap_delete(npu_membuf_heap, gdma_membuf_heap,
                         &(tgt_membuf->first));
    membuf_list.erase(tgt_membuf);  // 从待分配列表中移除
    buffer_avail_space.erase(tgt_membuf->first);  // 更新可用空间
  }

  // 分配完成：设置总占用空间，分配L2内存地址
  time_step->set_lmem_occupy(lmem_occupy);
  assignL2memAddr(lg_info, time_step);  // 分配L2内存地址（若有）

  // 调试日志：打印分配成功的汇总信息
  GROUP_DEBUG_WITH_TYPE("lmem_assign", lg_info, [&]() {
    llvm::dbgs() << DEBUGGER_DEFAULT_INFO(
                        "assignLmemAddr_finish", "success",
                        "所有缓冲区LMEM地址分配成功")
                 << LOG_KV("总使用LMEM", lmem_occupy)
                 << LOG_KV("利用率",
                           (lmem_occupy * 100.0 / Arch::LMEM_BYTES))
                 << "%\n";
  });

  PROFILE_LOG("assignLmemAddr", false);  // 记录性能日志结束
  return true;
}
```

## 7./LayerGroup/opt3/base_solver.cpp

### 1.ilp_layer_group

#### 函数整体功能

`GroupMethod::ilp_layer_group` 是基于整数线性规划（ILP） 的层组划分算法（对应优化级别 `opt=3`）。其核心目标是通过 ILP 求解器寻找全局最优的层组划分方案，平衡计算效率、资源利用率（如 LMEM）和数据搬运成本。相比动态规划算法，ILP 能得到理论上更优的结果，但计算复杂度更高，适用于对分组质量要求严格的场景。

#### 关键逻辑拆解

##### （1）初始化与调试可视化

- 算子收集：遍历子网算子（`subnet_ops`），收集所有待分组的算子，并查找调试指定的 `dot_root_op`（用于可视化的根节点）。
- 可视化调试：若启用调试命令，通过 `find_op_tree_by_root2` 构建以 `dot_root_op` 为根的算子树，并生成 SVG 图（`createSubnetGraph` + `export_dot`），直观展示算子依赖关系。
- 子网可视化：创建整个子网的可视化图（`dot_graph_log_subnet`），用于跟踪分组过程中的变化。

##### （2）预处理：ILP 基础组初始化

- 调用 `init_ilp_base_groups` 为 ILP 算法初始化专用基础组。与简单分组 / 动态规划的基础组不同，ILP 基础组更注重适配 ILP 模型的约束条件（如变量定义、目标函数）。
- 在可视化图中记录初始基础组数量，便于跟踪分组过程。

##### （3）ILP 核心求解（核心步骤）

- 特定组处理：通过调试命令 `save_mlir_file_for_group_id` 可指定处理某个基础组（用于针对性调试验证），避免全量计算。
- 全量组处理：若未指定特定组，则遍历所有基础组，调用 `high_solver`（ILP 高级求解器）求解每个基础组的最优划分。`high_solver` 会构建 ILP 模型（定义变量、约束条件和目标函数），通过求解器得到最优分组方案。
- 结果存储：将求解得到的最优层组信息存入 `base_groups2`（若求解失败则使用原始基础组）。

##### （4）后处理：嵌套组扩展与优化

- 嵌套组扩展：通过 `expandAllNestedLgInfo` 多次扩展嵌套的层组信息。ILP 求解可能产生嵌套结构的分组（组内包含子组），展开为扁平结构便于后续处理。
- 分组切割优化：在特定调试选项（`opt3_o3`/`opt3_o2`）下，调用 `try_cut_some_group` 切割部分组，分别采用激进 / 保守策略进一步优化分组（如减少过大的组，降低资源冲突）。
- 结果收集：通过 `collectAllSubLgInfoResult` 将所有子层组的结果汇总到 `pass_ir->lg_infos`，完成最终层组信息的构建。

##### （5）最终可视化

- 若启用 `export_full_svg` 调试命令，在最终的可视化图中标注每个算子所属的层组信息（组索引、组 ID 等），并导出 SVG 图，用于分析分组效果。

#### 3.ilp_layer_group 代码

```cpp
void GroupMethod::ilp_layer_group(LgPassIR *pass_ir) {
  // 调试日志：打印算法标识（仅调试模式下输出）
  LAYER_GROUP_LOG_DEBUG_BLOCK({
    llvm::errs() << "\n"
                 << "=======================================================\n"
                 << "*********** ilp_layer_group **********\n"  // 算法名称：基于ILP（整数线性规划）的层组划分
                 << "=======================================================\n";
  });

  std::vector<Operation *> subnet_ops;  // 存储子网中的所有算子
  Operation *dot_root_op = nullptr;     // 用于可视化的根节点算子（调试用）

  // 遍历子网算子，收集算子并查找调试指定的根节点算子
  for (auto it : pass_ir->subnet_ops) {
    // 若启用了针对特定算子的调试命令，将该算子设为可视化根节点
    if (!dot_root_op &&
        module::isDebugCmdEnable("dot_root_op_name-" +
                                 module::getName(it).str() + "-")) {
      llvm::errs() << "ilp_layer_group find dot_root_op_name:"
                   << module::getName(it).str() << "\n";
      dot_root_op = it;
    }
    subnet_ops.push_back(it);  // 收集所有子网算子
  }

  // 若启用根节点可视化调试，生成以dot_root_op为根的算子树SVG图
  if (module::isDebugCmdEnable("dot_root_op_name") && dot_root_op) {
    std::vector<Operation *> op_tree, exclude_ops, break_ops;
    // 查找以dot_root_op为根的算子树（包含依赖关系）
    find_op_tree_by_root2(dot_root_op, op_tree, subnet_ops, exclude_ops,
                          break_ops, 0, 8);
    // 创建并导出算子树的SVG可视化图
    auto dot_graph_log = createSubnetGraph(op_tree);
    dot_graph_log->export_dot(
        "svg_initial2_" + module::getName(module::getModuleOp()).str(), true);
  }

  // 创建子网的整体可视化图（用于跟踪分组过程）
  pass_ir->dot_graph_log_subnet = createSubnetGraph(subnet_ops);

  //------------------------ 步骤0：预处理 —— 初始化ILP基础组 --------------------
  init_ilp_base_groups(pass_ir);  // 为ILP算法初始化基础组（与简单分组/动态规划的基础组不同，更适配ILP求解）
  // 在可视化图中添加全局信息：初始基础组数量
  pass_ir->dot_graph_log_subnet->add_node_label(
      "global_info",
      "init group_num:" + std::to_string(pass_ir->tmp_base_groups.size()));
  // 若启用全SVG导出调试，导出当前步骤的可视化图
  if (module::isDebugCmdEnable("export_full_svg")) {
    pass_ir->dot_graph_log_subnet->export_dot(
        "svg_initial2_" + module::getName(module::getModuleOp()).str(), true);
  }

  //------------------------ 步骤1：ILP核心处理 —— 求解最优分组 --------------------
  std::vector<std::shared_ptr<ilp_LgInfo>> base_groups2;  // 存储ILP处理后的基础组
  bool specify_group = false;  // 标记是否指定处理某个特定组（调试用）

  // 检查是否需要处理特定组（通过调试命令指定）
  for (int64_t i = 0, grp_num = pass_ir->tmp_base_groups.size(); i < grp_num;
       i++) {
    if (module::isDebugCmdEnable("save_mlir_file_for_group_id" +
                                 std::to_string(i))) {
      ilp_func_trace tmp_trace(llvm::formatv("high_solver, i:{0}", i).str());  // 记录ILP求解轨迹（调试用）
      // 调用ILP高级求解器处理第i个基础组，获取最优层组信息
      auto best_lgInfo =
          pass_ir->tmp_base_groups[i]->high_solver(pass_ir, cycle_calculator_);
      // 添加最优结果到base_groups2（若求解失败则使用原始基础组）
      base_groups2.push_back(best_lgInfo ? best_lgInfo
                                         : pass_ir->tmp_base_groups[i]);
      specify_group = true;  // 标记已处理特定组
      break;  // 仅处理指定组，跳出循环
    }
  }

  // 若未指定特定组，则处理所有基础组
  if (!specify_group) {
    for (int64_t i = 0, grp_num = pass_ir->tmp_base_groups.size(); i < grp_num;
         i++) {
      ilp_func_trace tmp_trace(llvm::formatv("high_solver, i:{0}", i).str());  // 记录求解轨迹
      // 对每个基础组调用ILP求解器，获取最优层组信息
      auto best_lgInfo =
          pass_ir->tmp_base_groups[i]->high_solver(pass_ir, cycle_calculator_);
      base_groups2.push_back(best_lgInfo ? best_lgInfo
                                         : pass_ir->tmp_base_groups[i]);
    }
  }

  // 扩展嵌套的层组信息（处理多层嵌套的分组结构，将嵌套组展开为扁平结构）
  auto base_groups3 = expandAllNestedLgInfo(base_groups2);
  // 若启用opt3_o3调试选项，尝试切割部分组以优化（更激进的切割策略）
  if (module::isDebugCmdEnable("opt3_o3")) {
    try_cut_some_group(pass_ir, *base_groups3, true);
  }

  // 再次扩展嵌套层组信息
  auto base_groups4 = expandAllNestedLgInfo(*base_groups3);
  // 若启用opt3_o2调试选项，尝试切割部分组以优化（较保守的切割策略）
  if (module::isDebugCmdEnable("opt3_o2")) {
    try_cut_some_group(pass_ir, *base_groups4, false);
  }

  // 注释：尝试修改MLP组的子求和操作（暂未启用）
  // try_modify_mlp_group_sub_sum(pass_ir, *base_groups4, cycle_calculator_);

  // 第三次扩展嵌套层组信息，得到最终的基础组结构
  auto base_groups5 = expandAllNestedLgInfo(*base_groups4);
  // 收集所有子层组的结果到pass_ir（填充lg_infos）
  for (int64_t i = 0, grp_num = base_groups5->size(); i < grp_num; i++) {
    collectAllSubLgInfoResult((*base_groups5)[i], pass_ir);
  }

  // 若启用全SVG导出调试，生成最终分组结果的可视化图
  if (module::isDebugCmdEnable("export_full_svg")) {
    // 在可视化图中添加全局信息：最终层组数量
    pass_ir->dot_graph_log_subnet->add_node_label(
        "global_info",
        "final group_num:" + std::to_string(pass_ir->lg_infos.size()));
    // 为每个算子标注所属层组信息（便于调试分析）
    for (auto [grp_idx, lg_info] : llvm::enumerate(pass_ir->lg_infos)) {
      for (auto [op_idx, op] : llvm::enumerate(lg_info.group_ops)) {
        if (op) {
          pass_ir->dot_graph_log_subnet->add_node_label(
              module::getName(op).str(),
              "grp_" + std::to_string(grp_idx) + "*_id_" +
                  std::to_string(lg_info.group_id) + "*_" +
                  std::to_string(op_idx) + "*");
        }
      }
    }
    // 导出最终的SVG图
    pass_ir->dot_graph_log_subnet->export_dot(
        "svg_" + module::getName(module::getModuleOp()).str());
  }
}
```

### 2.high_solver

#### 功能概述

该函数是 `ilp_LgInfo` 类的高级求解器（`high_solver`），主要作用是尝试两种不同的优化策略（组优先切割和切片优先切割），通过基础求解器（`base_solver`）计算对应的周期（`group_cycle`），最终选择周期更短（更优）的策略结果返回。

#### 逻辑流程

1. 保存原始操作集：首先保存当前 `_lgInfo` 中的原始操作组（`group_ops`），用于后续策略对比时复用原始数据。
2. 第一种策略求解：

   - 设置当前策略为 `STRATEGY_GROUP_CUT_FIRST`（组优先切割）。
   - 输出调试日志，标识当前策略开始测试。
   - 调用 `base_solver` 执行求解，计算该策略下的周期（`group_cycle`）。
3. 第二种策略条件执行：

   - 仅当启用 `opt3_o3` 调试命令（一种优化开关）时，才执行第二种策略。
   - 创建新的 `ilp_LgInfo` 实例（`ilp_cloned`），使用原始操作集和 `STRATEGY_SLICE_CUT_FIRST`（切片优先切割）策略。
   - 调用新实例的 `base_solver`，计算该策略下的周期。
4. 策略对比与结果返回：

   - 比较两种策略的周期（`group_cycle`）：
     - 若切片优先策略周期更短，返回新实例（`ilp_cloned`）。
     - 若组优先策略更优，不返回新实例。
   - 若未启用 `opt3_o3`，直接返回 `nullptr`（表示当前实例的组优先策略结果更优）。

#### 核心原理

- 多策略优化：通过对比两种不同的切割策略（组优先 / 切片优先），选择对当前场景更优的结果。这是编译器优化或调度优化中常见的 “试探性策略选择” 思路，通过实际计算结果决定最优方案。
- 调试控制：通过 `module::isDebugCmdEnable("opt3_o3")` 控制是否启用第二种策略，便于开发阶段测试不同策略的效果，而不影响正式环境。
- 资源管理：使用 `std::shared_ptr` 管理 `ilp_LgInfo` 实例，确保内存安全，避免内存泄漏。
- 基础求解器复用：两种策略均依赖 `base_solver` 完成核心计算，`high_solver` 仅负责策略调度和结果对比，体现了 “职责分离” 的设计思想。

#### high_solver 代码

```cpp
// ilp_LgInfo类的成员函数high_solver，返回该类的智能指针
// 参数：pass_ir（LgPassIR类型指针，可能是中间表示相关数据）、cycle_calculator_（周期计算器智能指针）
std::shared_ptr<ilp_LgInfo>
ilp_LgInfo::high_solver(LgPassIR *pass_ir,
                        std::shared_ptr<CycleCalculator> cycle_calculator_) {
  // 保存当前lgInfo中的原始操作组（group_ops）
  auto ops_ori = _lgInfo.group_ops;
  
  // 设置当前策略为"组优先切割"（STRATEGY_GROUP_CUT_FIRST）
  _cur_strategy = STRATEGY_GROUP_CUT_FIRST;
  
  // 调试日志块：当启用调试时，输出策略测试信息
  LAYER_GROUP_LOG_DEBUG_BLOCK(
      { llvm::errs() << "ilp_debug: STRATEGY_GROUP_CUT_FIRST test\n"; });
  
  // 调用基础求解器，使用当前策略（组优先切割）进行求解
  base_solver(pass_ir, cycle_calculator_);

  // 如果启用了"opt3_o3"调试命令（可能是一种优化选项开关）
  if (module::isDebugCmdEnable("opt3_o3")) {
    // 调试日志块：输出第二种策略测试信息
    LAYER_GROUP_LOG_DEBUG_BLOCK(
        { llvm::errs() << "ilp_debug: STRATEGY_SLICE_CUT_FIRST test\n"; });
    
    // 创建一个新的ilp_LgInfo实例，使用原始操作组、相同配置，策略设为"切片优先切割"
    auto ilp_cloned =
        CreateIlpLgInfo(ops_ori, options_, STRATEGY_SLICE_CUT_FIRST);
    
    // 调用新实例的基础求解器，使用切片优先切割策略求解
    ilp_cloned->base_solver(pass_ir, cycle_calculator_);
    
    // 比较当前实例与新实例的组周期（group_cycle），选择更优（更小）的结果
    if (group_cycle > ilp_cloned->group_cycle) {
      // 调试日志：切片优先策略更优，输出周期对比
      LAYER_GROUP_LOG_DEBUG_BLOCK({
        llvm::errs() << "ilp_debug:strategy STRATEGY_SLICE_CUT_FIRST better, "
                     << group_cycle << " vs " << ilp_cloned->group_cycle
                     << "\n";
      });
      // 返回更优的新实例
      return ilp_cloned;
    } else {
      // 调试日志：组优先策略更优，输出周期对比
      LAYER_GROUP_LOG_DEBUG_BLOCK({
        llvm::errs() << "ilp_debug:strategy STRATEGY_GROUP_CUT_FIRST better, "
                     << group_cycle << " vs " << ilp_cloned->group_cycle
                     << "\n";
      });
    }
  }
  
  // 如果未启用opt3_o3，或组优先策略更优，返回nullptr（表示当前实例更优）
  return nullptr;
}
```

### 3.base_solver

#### 功能概述

`base_solver` 是 `ilp_LgInfo` 类的核心求解函数，负责对单个操作组（`group_ops`）执行 ILP（整数线性规划）求解，以实现操作的优化调度或分组。若整体求解失败，会将操作组分割为子组递归处理，最终汇总所有子组和无法分组的 “全局层” 的周期，得到当前组的总周期（`group_cycle`）。

#### 逻辑流程

1. 初始化与准备：

   - 获取当前操作组（`ops`），创建函数跟踪对象（调试用）。
   - 初始化失败处理相关变量（`fail_process_mode`、`fail_op`），更新组的输入输出信息。
2. 核心求解尝试：

   - 调用 `ilp_for_single_group`（单个组的 ILP 求解核心函数），返回求解是否成功（`ret`）。
   - 若求解成功：标记 `group_success` 为 `true`，直接返回（当前组处理完成）。
3. 求解失败处理：

   - 若当前策略是 “搜索卷积切割”（`STRATEGY_SEARCH_CONV_CUT`），不进行嵌套分组，直接返回。
   - 根据不同失败模式（`fail_process_mode`）收集 “断裂操作”（`break_ops`）：
     - 模式 0：处理单个失败操作（`fail_op`），记录其是否在组内，加入 `break_ops`。
     - 模式 2：针对 `UpsampleOp` 类型操作，收集相关断裂操作。
     - 其他模式：将当前组所有操作视为全局层（无法分组）。
4. 子组分割与递归处理：

   - 若存在 `break_ops`，通过 `seg_grp_ops_by_global_op` 将原始操作组分割为子组。
   - 对每个子组：
     - 若子组含多个操作：创建新的 `ilp_LgInfo` 实例，递归调用 `base_solver` 求解，累加子组周期，保存结果。
     - 若子组仅含单个操作：直接加入 `global_layers`（无需进一步分组）。
5. 全局层周期计算：

   - 遍历 `global_layers`，通过 `cycle_calculator_` 计算每个全局层的周期，累加到当前组的总周期（`group_cycle`）。

#### 核心原理

- 分治策略：当整体组求解失败时，通过 “分割 - 递归” 的分治思想，将大组拆分为可求解的小组，确保最终能得到所有操作的调度结果（避免因单个操作失败导致整体无法处理）。
- 失败容错机制：通过 `fail_process_mode` 区分不同失败场景，针对性收集断裂操作，实现灵活的错误处理。
- 周期累加逻辑：无论是子组求解结果还是全局层，均通过 `group_cycle` 累加周期，最终得到当前组的总耗时，为上层策略对比（如 `high_solver` 中的策略选择）提供量化依据。
- 特殊组处理：对特殊类型的组（如矩阵乘法优化组 `p_special_grp`）进行类型转换尝试，优化特定操作的求解效果，体现了针对不同操作类型的差异化优化思路。

#### 4.base_solver 代码

```cpp
// ilp_LgInfo类的基础求解器函数，负责处理单个操作组的ILP求解逻辑
// 参数：pass_ir（中间表示数据指针）、cycle_calculator_（周期计算器智能指针）
void ilp_LgInfo::base_solver(
    LgPassIR *pass_ir, std::shared_ptr<CycleCalculator> cycle_calculator_) {
  // 获取当前lgInfo中的操作组（待处理的操作集合）
  auto &ops = _lgInfo.group_ops;
  // 创建函数调用跟踪对象（可能用于调试或性能分析，记录函数进入/退出）
  ilp_func_trace tmp_trace(__func__);
  // 失败处理模式（0/1/2，用于区分不同的失败场景）
  int fail_process_mode = 0;
  // 记录求解失败的操作（若有）
  Operation *fail_op = nullptr;
  
  // 更新当前组的输入输出信息（基于优化选项）
  _lgInfo.update_group_io(options_.opt);
  
  // 存储"断裂操作"是否位于当前组内的映射
  std::map<Operation *, bool> break_op_reside;
  // 指向上述映射的指针（用于传递给子函数）
  std::map<Operation *, bool> *break_op_reside_ptr = &break_op_reside;
  // break_ops：求解失败时需要分割的操作集合；excluded_ops：需要排除的操作集合
  std::vector<Operation *> break_ops, excluded_ops;
  
  // 调用单个组的ILP求解核心函数，返回求解是否成功
  auto ret = ilp_for_single_group(pass_ir, *this, fail_process_mode, fail_op,
                                  cycle_calculator_);
  
  // 如果求解失败（ret为false）
  if (!ret) {
    // 若当前策略是"搜索卷积切割"模式，则不再嵌套分组，直接返回
    if (_cur_strategy == STRATEGY_SEARCH_CONV_CUT) {
      return; 
    }
    
    // 处理失败场景1：存在失败操作且失败模式为0
    if (fail_op && fail_process_mode == 0) {
      // 调试日志：输出失败的操作信息
      LAYER_GROUP_LOG_DEBUG_BLOCK({
        llvm::errs() << "ilp_debug: ilp_for_single_group fail_op:"
                     << show_op_info(fail_op) << "\n";
      });
      // 记录该失败操作是否在当前组内
      break_op_reside[fail_op] = is_fail_op_in_grp;
      // 将失败操作加入断裂操作集合
      break_ops.push_back(fail_op);
      // 若失败操作不在当前组内，加入全局层集合
      if (!is_fail_op_in_grp) {
        global_layers.push_back(fail_op);
      }
    } 
    // 处理失败场景2：失败模式为2
    else if (fail_process_mode == 2) {
      // 调试日志：标识进入模式2处理
      LAYER_GROUP_LOG_DEBUG_BLOCK(
          { llvm::errs() << "ilp_debug: fail_process_mode 2\n"; });
      // 检查当前操作组中是否包含UpsampleOp类型的操作，若有则收集到break_ops
      if (isOpTypeInGroup<tpu::UpsampleOp>(ops, break_ops)) {
        for (auto op : break_ops) {
          // 调试日志：输出断裂操作信息
          LAYER_GROUP_LOG_DEBUG_BLOCK({
            llvm::errs() << "ilp_debug: break_op:" << show_op_info(op) << "\n";
          });
          // 将断裂操作加入全局层集合
          global_layers.push_back(op);
        }
      }
    } 
    // 其他失败场景：将当前组所有操作加入全局层集合
    else {
      global_layers.assign(ops.begin(), ops.end());
    }
  } 
  // 若求解成功，标记组成功并返回
  else {
    group_success = true;
    return;
  }

  // 若存在断裂操作（需要分割组）
  if (break_ops.size() > 0) {
    // 遍历分割后的子组（通过seg_grp_ops_by_global_op函数分割原始操作组）
    for (auto [i, grp] : llvm::enumerate(seg_grp_ops_by_global_op(
             ops, break_ops, excluded_ops, options_, break_op_reside_ptr))) {
      // 若子组包含多个操作（需要进一步处理）
      if (grp.size() > 1) {
        // 创建子组处理的跟踪对象（调试用）
        ilp_func_trace tmp_trace(
            llvm::formatv("ilp_debug: process_sub_group, i:{0}", i).str());
        // 按照原始操作组的顺序对子组操作排序
        auto sub_ops = sortOpsByOtherOpsOrder(_lgInfo.group_ops, grp);
        // 创建处理子组的ilp_LgInfo实例
        auto tmpLgInfo = CreateIlpLgInfo(sub_ops, options_);
        
        // 若存在特殊组（如矩阵乘法优化组），传递特殊组信息并尝试转换
        if (p_special_grp) {
          tmpLgInfo->p_special_grp = p_special_grp;
          // 尝试将子组转换为其他类型（如矩阵乘法优化类型）
          if (!p_special_grp->convert_to_other_type(sub_ops,
                                                    tmpLgInfo->p_special_grp)) {
            // 转换失败的调试日志
            LAYER_GROUP_LOG_DEBUG_BLOCK(
                {
                  llvm::errs()
                      << "ilp_debug: matmul grp convert_to_other_type fail\n";
                });
            tmpLgInfo->p_special_grp = nullptr;
          } else {
            // 转换成功，标记子组类型为矩阵乘法优化类型
            tmpLgInfo->_lgInfo.type = GROUP_MM_OPT3;
          }
        }
        
        // 递归调用子组的base_solver进行求解
        tmpLgInfo->base_solver(pass_ir, cycle_calculator_);
        // 累加子组的周期到当前组的总周期
        group_cycle += tmpLgInfo->group_cycle;
        // 保存子组的求解结果
        sub_ilp_LgInfos.push_back(tmpLgInfo);
      } 
      // 若子组仅包含单个操作，直接加入全局层集合
      else {
        LAYER_GROUP_LOG_DEBUG_BLOCK({
          llvm::errs() << "ilp_debug: add global_layer:" << show_op_info(grp[0])
                       << "\n";
        });
        global_layers.push_back(grp[0]);
      }
    }
  }
  
  // 计算全局层集合中所有操作的周期，并累加到当前组的总周期
  for (auto global_layer : global_layers) {
    if (global_layer) {
      group_cycle += cycle_calculator_->getGlobalLayerCycle(global_layer);
    }
  }
}
```

### 4.ilp_for_single_group

#### 功能概述

`ilp_for_single_group` 是 ILP 求解的核心执行函数，负责对单个操作组（`sub_group`）进行硬件适配的分块（切片）、调度约束生成、ILP 求解、内存分配检查，并通过多次重试调整参数以应对求解失败，最终确定操作组的可行调度方案及周期（`group_cycle`）。

#### 逻辑流程

1. 初始化与调试准备：

   - 获取操作组（`ops`），创建.dot 图形日志（`tmp_dot_graph_log`）用于可视化调试，为每个操作添加标签。
   - 初始化求解结果（`ret`）、失败操作（`fail_op`）等变量，打印组信息用于调试。
2. 组初始化（特殊组 vs 普通组）：

   - 特殊组（如矩阵乘法）：调用 `CalcMatMulGroupTpNum` 计算分片数量，若失败直接返回。
   - 普通组：计算最大形状维度（`max_shape_secs`），初始化切割维度（`vec_op_cut_secs`），调用 `init_group_data_secs2` 初始化组数据，失败则返回。
   - 调整维度以匹配硬件核心数量（`align_secs_to_core_num`），确保分块适配多核。
3. 循环重试求解：

   - 设定最大尝试次数（`max_slice_cut_count`），超过则返回失败。
   - 单次尝试流程（`do-while` 块）：
     - 普通组切片：调用 `stripe_mine_idx_slice2` 进行条纹挖掘（硬件友好的分块），失败则根据策略决定是否重试。
     - 核心分配：按核心数量（`core_num`）分配切片（`vec_ncdhw`），确保每个核心处理部分分块。
     - 调度生成：对每个核心的切片，调用 `backward_gen_ilp_var2` 生成 ILP 变量（调度约束），`ilp_timeStep->run` 执行 ILP 求解，得到时间步调度。
     - 内存检查：通过 `ilp_timeStep->mem_alloc` 验证内存分配是否可行，失败则尝试插入非操作（如等待指令）解决冲突。
     - 周期计算：记录每个核心的周期，取最大值作为组总周期（`max_group_cycle`）。
4. 失败处理与重试：

   - 若求解失败，通过 `update_shape_secs_for_ilp_group` 调整形状维度，重新执行切片与求解（最多 `max_slice_cut_count` 次）。
   - 特殊失败场景（如 `UpsampleOp` 失败）标记失败模式（`fail_process_mode`），便于上层处理。
5. 成功处理：

   - 求解成功后，更新组周期（`sub_group.group_cycle`），执行 L2 缓存优化（`l2m_process`），返回 `true`。

#### 核心原理

- 硬件适配分块：通过 “条纹挖掘”（`stripe_mine_idx_slice2`）将操作组分片为适配硬件核心的小块（`ncdhw` 维度），实现并行计算。
- ILP 调度优化：利用整数线性规划（`ILPTimeStep`）生成操作的时间步调度，在满足依赖关系和资源约束（如内存、计算单元）的前提下最小化周期。
- 重试机制：当求解失败（如内存冲突、约束不可行）时，通过调整形状维度（`update_shape_secs_for_ilp_group`）重新尝试，提高成功率。
- 多核协同：按核心分配切片（`get_sec_per_cores`），并通过 `EliminatingDuplicatePipeline` 消除重复调度，优化多核效率。
- 内存与计算协同：通过 `mem_alloc` 检查内存分配可行性，确保调度方案在硬件内存约束下可执行，避免实际运行时的内存冲突。

#### 4.ilp_for_single_group 代码

```cpp
// 静态函数：对单个操作组执行ILP（整数线性规划）求解，处理调度与资源分配
// 参数：pass_ir（中间表示指针）、sub_group（当前处理的子组）、fail_process_mode（失败模式输出）
//       fail_op（失败操作输出）、cycle_calculator_（周期计算器）
// 返回值：求解是否成功（true为成功）
static bool
ilp_for_single_group(LgPassIR *pass_ir, ilp_LgInfo &sub_group,
                     int &fail_process_mode, Operation *&fail_op,
                     std::shared_ptr<CycleCalculator> cycle_calculator_) {
  // 获取当前子组的操作集合
  auto &ops = sub_group._lgInfo.group_ops;
  // 创建操作组的.dot图形日志（用于调试可视化）
  auto tmp_dot_graph_log = createSubnetGraph(ops);
  // 为图形日志中的每个操作添加标签（含索引，便于区分）
  for (auto [index, op] : llvm::enumerate(ops)) {
    if (op) {
      tmp_dot_graph_log->add_node_label(module::getName(op).str(),
                                        "grp_ts" + std::to_string(index) + "*");
    }
  }

  // 求解结果标记（默认失败）
  bool ret = false;
  std::string tmpStr;
  // 创建函数跟踪对象（含子组ID和图形日志，用于调试跟踪）
  ilp_func_trace tmp_trace(__func__, sub_group._lgInfo.group_id,
                           tmp_dot_graph_log);
  // 初始化失败操作（未失败时为nullptr）
  fail_op = nullptr;
  // 打印当前组信息（调试用）
  show_group(&sub_group._lgInfo);

  // 存储操作与硬件维度的映射、组的最大形状维度
  std::vector<std::pair<Operation *, int>> vec_op_hwsecs;
  shape_secs_t max_shape_secs;

  // 若不是特殊组（如非矩阵乘法组），计算组的最大形状维度
  if (!sub_group.p_special_grp) {
    max_shape_secs = get_group_max_secs(sub_group._lgInfo, vec_op_hwsecs);
    // 格式化最大形状维度信息为字符串
    tmpStr = shape_str(max_shape_secs.nsecs, max_shape_secs.csecs,
                       max_shape_secs.dsecs, max_shape_secs.hsecs,
                       max_shape_secs.wsecs);
    // 输出调试日志：最大形状维度
    LAYER_GROUP_LOG_DEBUG_BLOCK(
        { llvm::errs() << "max_shape_secs:" << tmpStr << '\n'; });
    // 将最大形状维度添加到图形日志
    tmp_dot_graph_log->add_node_label("global_info",
                                      "max_shape_secs:" + tmpStr);
  }

  // 当前组的形状维度配置、值大小映射（用于内存分配）
  auto &shape_secs = sub_group.shape_secs;
  std::vector<std::pair<Value, int64_t>> value_size;
  // 获取硬件核心数量（多核环境下为实际核心数，否则为1）
  int64_t core_num = dyn_cast<MultiCoreInterface>(BM168x::instance())
                         ? module::getCoreNum()
                         : 1;

  // 处理特殊组（如矩阵乘法组）：计算矩阵乘法的分片数量
  if (sub_group.p_special_grp) {
    if (!sub_group.p_special_grp->CalcMatMulGroupTpNum(sub_group, fail_op,
                                                       core_num)) {
      LAYER_GROUP_LOG_DEBUG_BLOCK(
          { llvm::errs() << "CalcMatMulGroupTpNum fail\n"; });
      sub_group.is_fail_op_in_grp = false; // 标记失败操作不在组内
      return false; // 求解失败
    }
  } 
  // 处理普通组：初始化切割维度与组数据
  else {
    // 按硬件维度排序操作
    std::sort(vec_op_hwsecs.begin(), vec_op_hwsecs.end(),
              pair_op_int_Sort_by_int);
    // 获取操作的切割维度数量
    std::vector<std::pair<Operation *, int>> vec_op_cut_secs;
    get_op_cut_sec_num(sub_group, vec_op_cut_secs);
    // 按切割维度排序操作
    std::sort(vec_op_cut_secs.begin(), vec_op_cut_secs.end(),
              pair_op_int_Sort_by_int);

    // 初始化组的数据维度（失败则返回）
    if (!init_group_data_secs2(sub_group, shape_secs, value_size, fail_op,
                               tmp_dot_graph_log, sub_group.options_)) {
      sub_group.is_fail_op_in_grp = false;
      // 若未明确失败操作，取切割维度最大的操作作为失败点
      if (!fail_op) {
        fail_op = vec_op_cut_secs.back().first;
      }
      tmpStr = module::getName(fail_op).str();
      LAYER_GROUP_LOG_DEBUG_BLOCK({
        llvm::errs() << "init_group_data_secs2 fail, will del op:" << tmpStr
                     << "\n";
      });
      tmp_dot_graph_log->add_node_label(tmpStr, "init_group_data_secs2 fail");
      return false; // 求解失败
    }
    // 调整维度以匹配核心数量（硬件适配）
    align_secs_to_core_num(sub_group, max_shape_secs);
  }

  // 输出初始化后的形状维度信息
  tmpStr = shape_secs.info();
  LAYER_GROUP_LOG_DEBUG_BLOCK(
      { llvm::errs() << "init shape_secs:" << tmpStr << '\n'; });
  tmp_dot_graph_log->add_node_label("global_info", "init shape_secs:" + tmpStr);
  // 按值大小排序（用于内存分配）
  std::sort(value_size.begin(), value_size.end(), Sort_by_int);

  // 切片尝试次数、非操作插入模式、最大切片尝试次数（操作数>10时为1，否则为3）
  int slice_try_count = 0, nonOp_insert_mode,
      max_slice_cut_count = ops.size() > 10 ? 1 : 3;
  // 张量信息（存储每个张量的切片信息等）
  auto &tensor_infos = sub_group.tensor_infos;
  // L2缓存优化开关（BM1690芯片启用，其他芯片禁用）
  bool l2m_switch = module::isDebugCmdEnable("disable_l2m") ? false : true,
       inc_secs = true; // 是否增加维度（失败重试时调整）
  if (module::getChip() != module::Chip::BM1690) {
    l2m_switch = false;
  }
  tmp_dot_graph_log->add_node_label("global_info", "enable_l2m");

  // 循环尝试求解（调整参数直到成功或达到最大尝试次数）
  while (true) {
    // 若超过最大尝试次数，返回失败（标记为全局层）
    if (inc_secs && ++slice_try_count > max_slice_cut_count) {
      LAYER_GROUP_LOG_DEBUG_BLOCK({ llvm::errs() << "layer group fail\n"; });
      return false;
    }
    if (!inc_secs) {
      inc_secs = true; // 重置维度调整标记
    }

    // 当前维度总数、L2缓存优化启用条件（维度>1且多核）
    int64_t secs = shape_secs.get_sec_num();
    bool l2m_en = l2m_switch && secs > 1 && core_num > 1;
    LAYER_GROUP_LOG_DEBUG_BLOCK({
      llvm::errs() << "shape_secs:" << shape_secs.info()
                   << " slice_try_count:" << slice_try_count
                   << " l2m_en:" << l2m_en << "\n";
    });

    // 最大组周期（记录所有核心中最长的周期）
    int max_group_cycle = 0;
    // 清空当前时间步指针（存储每个核心的调度结果）
    sub_group.timeStepPtrs.clear();

    // 执行单次求解尝试（do-while确保只执行一次，便于break退出）
    do {
      // 普通组：执行条纹挖掘（stripe mining）切片操作（硬件适配的分块）
      if (!sub_group.p_special_grp) {
        ret = stripe_mine_idx_slice2(sub_group, shape_secs, tensor_infos,
                                     fail_op);
        if (!ret) { // 切片失败
          tmpStr = module::getName(fail_op).str();
          tmp_dot_graph_log->add_node_label(tmpStr,
                                            "stripe_mine_idx_slice2 fail");
          LAYER_GROUP_LOG_DEBUG_BLOCK({
            llvm::errs() << "stripe_mine_idx_slice2 fail at" << tmpStr << "\n";
          });
          // 若失败操作是UpsampleOp且高度维度>1，标记失败模式为2
          if (isa<tpu::UpsampleOp>(fail_op) && shape_secs.hsecs > 1) {
            fail_process_mode = 2;
            return false;
          }
          // 若当前策略不是切片优先，则返回失败
          if (sub_group._cur_strategy != STRATEGY_SLICE_CUT_FIRST) {
            return false;
          } else {
            break; // 切片优先策略下中断当前尝试，进入重试
          }
        }
        // 更新张量信息和组的存储 bank 信息（内存分配相关）
        update_tensor_infos(sub_group._lgInfo, tensor_infos, shape_secs,
                            sub_group.p_special_grp ? 1 : 0);
        sub_group._lgInfo.update_bank_info();
      }

      // 按核心分配切片（记录每个核心处理的维度分片）
      std::map<int64_t, std::vector<std::vector<int64_t>>> vec_ncdhw;
      // 检查第一个操作的输入张量是否在张量信息中（断言确保存在）
      if (tensor_infos.find(sub_group._lgInfo.group_ops[0]->getOperand(0)) ==
          tensor_infos.end()) {
        assert(false);
      }
      // 获取第一个输入张量的切片信息
      auto &slice_info =
          tensor_infos[sub_group._lgInfo.group_ops[0]->getOperand(0)]
              .slice_info;
      // 计算每个核心应处理的切片
      get_sec_per_cores(sub_group, vec_ncdhw, core_num, slice_info);

      // 遍历每个核心，分配切片并求解调度
      for (int core_id = 0; core_id < core_num; core_id++) {
        LAYER_GROUP_LOG_DEBUG_BLOCK(
            { llvm::errs() << "cur_core_id:" << core_id << "\n"; });
        // 当前核心的切片数量
        int core_slice_num = vec_ncdhw[core_id].size();
        if (core_slice_num == 0) {
          break; // 无切片则跳过
        }

        // 检查是否为相同流水线（若相同则无需重复计算）
        if (is_same_pipeline(core_id, sub_group, tensor_infos, vec_ncdhw,
                             core_slice_num)) {
          continue;
        }

        // 切片索引、失败模式、操作变量边界（调度约束）
        int slice_idx = 0, failMode = 0;
        std::vector<op_var_pos_info> op_var_bound;
        // 特殊组：创建重叠策略（优化矩阵乘法等操作的调度）
        if (sub_group.p_special_grp) {
          op_var_bound =
              createOverlapStrategy(sub_group._lgInfo, core_slice_num, 1, 2, 2);
        } else {
          op_var_bound = createOverlapStrategy(sub_group._lgInfo, core_slice_num);
        }

        // 节点标签（调试日志）、当前核心的时间步调度器
        std::map<std::string, std::string> node_labels;
        auto ilp_timeStep = std::make_shared<ILPTimeStep>(
            sub_group._lgInfo, tmp_dot_graph_log, core_slice_num);
        // 下一时间步的加载字节数（内存预加载优化）
        int64_t load_bytes_for_next_ts = 0;

        // 处理当前核心的每个切片
        while (slice_idx < core_slice_num) {
          // 当前切片的维度信息（n/c/d/h/w）
          std::vector<int64_t> ncdhw = vec_ncdhw[core_id][slice_idx];
          ilp_timeStep->addSliceNcdhwSteps(core_id, ncdhw); // 记录切片步骤
          LAYER_GROUP_LOG_DEBUG_BLOCK({
            llvm::errs() << "slice" << slice_idx
                         << ", ncdhw:" << shape_str(ncdhw) << "\n";
          });

          // 反向生成ILP变量（构建调度约束）
          ret = backward_gen_ilp_var2(
              sub_group, tensor_infos, cycle_calculator_, *ilp_timeStep, ncdhw,
              slice_idx, op_var_bound, fail_op, failMode, node_labels,
              load_bytes_for_next_ts, l2m_en, 4);
          if (!ret) { // 变量生成失败
            if (failMode == 1) { // 模式1：失败操作不在组内
              sub_group.is_fail_op_in_grp = false;
              return false;
            } else { // 其他模式：尝试插入非操作（如等待指令）解决冲突
              if (!failProcess_insertNonOp(sub_group, fail_op, inc_secs)) {
                return false;
              }
            }
            break; // 中断当前切片处理
          }
          slice_idx++; // 处理下一切片
        }

        // 将节点标签添加到图形日志（核心0的标签作为全局参考）
        if (core_id == 0) {
          for (auto itr2 : node_labels) {
            tmp_dot_graph_log->add_node_label(itr2.first, itr2.second);
          }
        }
        if (!ret) {
          break; // 切片处理失败，中断核心循环
        }

        // 合并小周期操作（优化调度效率）
        bool merged = false;
        ilp_timeStep->merge_small_cycle_op(tensor_infos, merged,
                                           tmp_dot_graph_log);
        ilp_timeStep->prepare(tensor_infos); // 准备ILP求解

        // 运行ILP求解器（计算调度时间步）
        ret = ilp_timeStep->run(fail_op);
        if (!ret) { // 求解失败
          auto error_info =
              "ilp_timeStep run fail, for core_id:" + std::to_string(core_id);
          LAYER_GROUP_LOG_DEBUG_BLOCK({ llvm::errs() << error_info << "\n"; });
          tmp_dot_graph_log->add_node_label(
              fail_op ? module::getName(fail_op).str() : "global_info",
              error_info);
          // 尝试插入非操作解决失败
          if (!failProcess_insertNonOp(sub_group, fail_op, inc_secs)) {
            return false;
          }
          break; // 中断核心循环
        }

        // 内存分配检查（确保调度满足内存约束）
        mem_alloc_status alloc_status;
        ret = ilp_timeStep->mem_alloc(alloc_status, value_size, tensor_infos,
                                      fail_op, nonOp_insert_mode);
        if (!ret) { // 内存分配失败
          auto error_info = "ilp_timeStep mem_alloc fail, for core_id:" +
                            std::to_string(core_id);
          LAYER_GROUP_LOG_DEBUG_BLOCK({ llvm::errs() << error_info << "\n"; });
          tmp_dot_graph_log->add_node_label(
              fail_op ? module::getName(fail_op).str() : "global_info",
              error_info);
          // 尝试插入非操作解决内存冲突
          if (!failProcess_insertNonOp(sub_group, fail_op, inc_secs,
                                       nonOp_insert_mode)) {
            return false;
          }
          break; // 中断核心循环
        }

        // 获取当前核心的组周期信息，更新最大周期
        int group_cycle, group_cycle_diff;
        std::vector<ts_cycle_info> ts_cycle;
        ilp_timeStep->get_group_cycle_info(group_cycle, group_cycle_diff,
                                           ts_cycle);
        if (group_cycle > max_group_cycle) {
          max_group_cycle = group_cycle;
        }
        LAYER_GROUP_LOG_DEBUG_BLOCK({
          llvm::errs() << "core" << core_id << " group_cycle:" << group_cycle
                       << ", mem_alloc success\n";
        });
        tmp_dot_graph_log->add_node_label(
            "global_info", "core" + std::to_string(core_id) +
                               ", group_cycle:" + std::to_string(group_cycle) +
                               ", mem_alloc success");
        // 保存当前核心的时间步调度结果
        sub_group.timeStepPtrs.push_back(ilp_timeStep);
      }

      // 消除重复的流水线调度（优化多核效率）
      EliminatingDuplicatePipeline(sub_group);
    } while (false);

    // 若求解成功，更新组周期并处理L2缓存优化
    if (ret) {
      LAYER_GROUP_LOG_DEBUG_BLOCK(
          { llvm::errs() << "ilp_timeStep success\n"; });
      tmp_dot_graph_log->add_node_label("global_info", "ilp_timeStep success");
      if (fail_process_mode == 1) {
        return true;
      }
      sub_group.group_cycle = max_group_cycle; // 记录最大周期（总周期）
      l2m_process(sub_group, value_size, l2m_en); // L2缓存优化处理
      break; // 退出重试循环
    } 
    // 求解失败：调整形状维度后重试
    else {
      if (!inc_secs) {
        continue; // 继续重试
      }
      // 特殊组：更新形状维度以适应ILP求解
      if (sub_group.p_special_grp) {
        if (!sub_group.p_special_grp->update_shape_secs_for_ilp_group(
                sub_group.shape_secs, max_shape_secs)) {
          return false; // 无法调整，返回失败
        }
      } 
      // 普通组：更新形状维度
      else {
        if (!update_shape_secs_for_ilp_group(sub_group.shape_secs,
                                             max_shape_secs)) {
          return false; // 无法调整，返回失败
        }
      }
      // 重新调整维度以匹配核心数量
      align_secs_to_core_num(sub_group, max_shape_secs);
      // 移除空操作（清理操作组）
      ops.erase(std::remove_if(ops.begin(), ops.end(),
                               [](Operation *op) { return op == nullptr; }),
                ops.end());
      LAYER_GROUP_LOG_DEBUG_BLOCK({ llvm::errs() << "update_shape_secs\n"; });
      tmp_dot_graph_log->add_node_label("global_info", "update_shape_secs");
    }
  }

  return true; // 求解成功
}
```

### 5.stripe_mine_idx_slice2

#### 功能概述

`stripe_mine_idx_slice2` 是张量切片（分块）生成的核心函数，主要作用是为操作组（`ilp_LgInfo`）中的所有相关张量（输入、输出及中间张量）生成符合硬件约束的 “条纹挖掘”（stripe mining）切片信息。通过从输出张量反向推导输入张量的切片，确保整个操作链的切片信息一致，为后续的 ILP 调度（如并行计算、内存分配）提供基础。

#### 逻辑流程

1. 特殊情况处理：若操作组仅含 1 个操作，无需复杂切片（单操作可直接调度），直接返回成功。
2. 输出张量初始化：

   - 遍历组内所有输出张量（`group_outs`），获取其 NCDHW 维度（批量、通道、深度、高度、宽度）。
   - 计算张量的有效位宽（输入输出位宽的最小值，确保数据兼容）。
   - 调用 `get_out_slice_info` 生成输出张量的初始切片信息（基于 `shape_secs` 的硬件约束，如分块大小）。
   - 将切片信息存入 `tensor_infos`，并将输出张量加入反向追踪列表（`tensor_branchs`）。
3. 反向切片更新：

   - 循环处理 `tensor_branchs` 中的张量，通过 `backward_update_slice2` 从当前张量（输出或中间张量）反向推导其依赖的输入张量的切片信息（例如：若输出是卷积的结果，需根据卷积操作的计算逻辑推导输入特征图和权重的切片）。
   - 若反向更新失败，记录生成当前张量的操作（`getDefiningOp`）为失败操作（`fail_op`），返回失败。
4. 成功返回：所有张量的切片信息生成完成且一致，返回成功。

#### 核心原理

- 条纹挖掘（Stripe Mining）：将张量按硬件友好的方式分割为 “条纹状” 子块（如按通道、高度分块），使每个子块可独立由硬件核心处理，实现并行计算。切片信息（`slice_info_t`）包含每个维度的偏移（offset）和长度（len），定义了子块在原始张量中的位置和大小。
- 反向数据流推导：从输出张量的切片出发，反向计算输入张量的切片（如 `backward_update_slice2` 的作用）。这是因为输出的分块方式由硬件约束（`shape_secs`）决定，而输入的分块需匹配输出的计算逻辑（例如：卷积输出的一个子块对应输入的特定区域 + 权重的特定部分），确保数据依赖关系在分块后仍成立。
- 一致性保证：通过 `op_set` 和 `out_tensor_set` 避免重复处理操作和张量，确保整个操作链（从输入到输出）的切片信息一致，避免后续调度中出现数据不匹配（如输入子块缺失、计算范围错误）。

#### 4.stripe_mine_idx_slice2 代码

```cpp
// 为操作组生成条纹挖掘（stripe mining）的切片信息，从输出反向更新所有相关张量的切片
// 参数：ilp_lg_info（当前ILP组信息）、shape_secs（形状维度配置）、tensor_infos（张量切片信息存储）、fail_op（失败操作输出）
// 返回值：切片生成是否成功（true为成功）
bool stripe_mine_idx_slice2(ilp_LgInfo &ilp_lg_info,
                            const shape_secs_t &shape_secs,
                            TensorInfo &tensor_infos, Operation *&fail_op) {
  // 获取当前组的基础信息（含操作集合、输入输出等）
  auto lg_info = ilp_lg_info._lgInfo;
  
  // 若组内仅含1个操作，无需复杂切片，直接返回成功
  if (lg_info.group_ops.size() == 1) {
    return true;
  }
  
  // 初始化失败操作为空（未失败状态）
  fail_op = nullptr;
  // 清空张量切片信息（重新生成）
  tensor_infos.clear();

  // 张量维度变量（N/C/D/H/W，对应批量、通道、深度、高度、宽度）
  int64_t n, c, d, h, w;
  // 存储需要反向追踪的张量与对应操作（用于从输出反向推导输入切片）
  std::list<std::pair<Value, Operation *>> tensor_branchs;
  // 已处理的操作集合（避免重复处理）
  std::multiset<Operation *> op_set;
  // 已处理的输出张量集合（避免重复处理）
  std::set<Value, value_compare> out_tensor_set;

  // 遍历组内所有输出张量，初始化输出的切片信息
  for (auto out : lg_info.group_outs) {
    // 获取输出张量的NCDHW维度（根据组类型适配）
    module::getNCDHW(out, n, c, d, h, w, lg_info.type);
    // 获取输入和输出张量的存储类型（用于计算位宽）
    auto istype = module::getStorageType(lg_info.group_ins[0]);
    auto ostype = module::getStorageType(out);
    // 取输入和输出位宽的最小值（确保数据精度兼容）
    int64_t bitwidth = std::min(istype.getIntOrFloatBitWidth(),
                                ostype.getIntOrFloatBitWidth());
    // 根据形状维度配置和张量维度，生成输出张量的切片信息（stripe mining分块）
    slice_info_t si = get_out_slice_info(shape_secs, n, c, h, d, w, bitwidth);
    // 将切片信息存入tensor_infos（键为输出张量，值为切片信息）
    tensor_infos[out] = tensor_info_t(si);
    // 将输出张量加入已处理集合
    out_tensor_set.insert(out);
    // 将输出张量加入反向追踪列表（初始无对应操作，从输出开始反向推导）
    tensor_branchs.push_back(std::make_pair(out, nullptr));
  }

  // 标记反向更新是否成功
  bool ret = false;
  // 循环处理所有需要反向追踪的张量，直到列表为空
  while (!tensor_branchs.empty()) {
    // 取出列表头部的张量（从输出开始处理）
    auto out_tensor = tensor_branchs.front();
    tensor_branchs.pop_front();
    // 反向更新切片信息：从当前张量（输出）推导其依赖的输入张量的切片
    ret = backward_update_slice2(ilp_lg_info, shape_secs, out_tensor,
                                 tensor_branchs, tensor_infos, op_set,
                                 out_tensor_set);
    // 若反向更新失败，记录失败操作并返回
    if (!ret) {
      fail_op = out_tensor.first.getDefiningOp(); // 获取当前张量的定义操作（生成该张量的操作）
      llvm::errs() << module::getName(fail_op).str()
                   << " backward_update_slice2 fail"
                   << "\n";
      return false;
    }
  }

  // （注释掉的调试代码：打印所有张量的切片信息，包括N/C/H维度的偏移和长度）
  // for (auto itr: tensor_infos) {
  //   llvm::errs() <<"tensor:"<< module::getName(itr.first).str()
  //   <<",v:"<<itr.first.getImpl()<<"\n";
  //   for (auto itr3: itr.second.slice_info.n) {
  //     llvm::errs() <<"n offset:"<< itr3.first<<", len:"<< itr3.second <<"\n";
  //   }
  //   ...
  // }

  // 所有张量切片信息生成成功
  return true;
}
```

### 6.backward_update_slice2

#### 功能分析

`backward_update_slice2` 是深度学习模型优化（特别是 ILP 调度优化）中的关键函数，核心功能是从操作组的输出张量反向遍历其依赖链，计算并维护所有输入张量的切片信息（`slice_info`），确保张量在整个操作组中的分块（切片）一致性。

具体来说，它解决了两个核心问题：

1. 从输出张量的已知切片范围，反向推导输入张量应有的切片范围（如卷积输出的某块对应输入的哪块区域）；
2. 处理不同依赖路径中切片信息的冲突（如同一输入张量被多个操作使用时切片范围不一致），通过合并重叠切片确保一致性。

#### 逻辑流程

函数遵循 “反向遍历 - 切片计算 - 冲突处理 - 继续遍历” 的逻辑，具体步骤如下：

1. 终止条件判断：若当前输出张量是操作组的输入（已到达依赖链起点），直接返回成功（无需继续反向遍历）。
2. 特殊操作过滤：对 BM1684 芯片上启用 3ic 优化的卷积操作，直接返回失败（不支持反向切片）。
3. 遍历输入张量：对当前输出张量的生成操作（`op`），遍历其所有输入张量（`in`），逐个计算切片信息。
4. 切片信息计算：

   - 对无需分割的张量（`is_value_dont_split`），直接生成全维度切片（0 到维度大小）；
   - 对需要分割的张量，调用 `get_backward_slice_info2`，根据输出张量的切片（`out_si`）反向推导输入张量的切片（`si`）。
5. 冲突处理：

   - 若输入张量已有切片信息且与新计算的 `si` 不一致：
     - 在 CV18xx 芯片或动态模式下，直接返回失败；
     - 否则检查 H/W 维度切片是否重叠：重叠则合并（取最小覆盖范围），不重叠则返回失败。
6. 继续反向遍历：对需要进一步追溯的输入张量，加入 `tensor_branchs` 列表，供后续继续反向更新。

#### 原理说明

该函数的设计基于深度学习中张量的 “依赖链” 特性和硬件对张量分块的约束，核心原理包括：

1. 反向依赖推导：深度学习中操作的输入与输出存在确定性映射（如卷积的输入区域与输出区域一一对应），因此可从输出切片反向推导出输入切片（例如，输出的某 H/W 块对应输入的某 H/W 块 + 卷积核大小的区域）。
2. 切片一致性维护：同一输入张量可能被多个操作使用（如特征图被多个卷积层复用），需保证各操作使用的切片范围一致或可合并（重叠时合并为更大范围），否则会导致数据不一致（如部分区域未计算或重复计算）。
3. 硬件适配：不同芯片（如 BM1684、CV18xx）对张量分块的支持不同（如某些优化模式不支持切片），函数通过硬件类型判断（`module::isBM1684Family`）适配不同约束。
4. 效率与正确性平衡：通过合并重叠切片而非直接失败，在保证正确性的前提下提高调度灵活性（允许更大范围的张量复用），同时通过 `op_set` 避免重复处理提升效率。

#### backward_update_slice2 代码

```cpp
// 反向更新张量切片信息，用于操作组内的张量分块一致性维护
// 功能：从操作组的输出张量反向遍历其依赖的输入张量，计算并更新各输入张量的切片信息（slice_info），
//       确保张量在反向传播（依赖链）中的切片范围一致，为后续ILP变量生成提供正确的分块基础
// 参数：
//   ilp_lg_info - ILP组信息（包含操作组、输入输出等元数据）
//   shape_secs - 张量各维度的切片数量配置（如H/W维度的切片数）
//   out - 当前处理的输出张量及其定义操作（<张量值, 生成该张量的操作>）
//   tensor_branchs - 输出：需要继续反向遍历的张量分支（<输入张量, 依赖该张量的操作>）
//   tensor_infos - 输出：张量切片信息字典（键为张量值，值为包含切片范围的信息）
//   op_set - 输出：已处理的操作集合（避免重复处理）
//   out_tensor_set - 操作组的输出张量集合（用于判断是否终止反向遍历）
static bool
backward_update_slice2(ilp_LgInfo &ilp_lg_info, const shape_secs_t &shape_secs,
                       const std::pair<Value, Operation *> &out,
                       std::list<std::pair<Value, Operation *>> &tensor_branchs,
                       TensorInfo &tensor_infos,
                       std::multiset<Operation *> &op_set,
                       const ValueSet &out_tensor_set) {
  int64_t n, c, d, h, w;  // 张量N/C/D/H/W维度的大小
  auto lg_info = ilp_lg_info._lgInfo;  // 获取操作组基础信息

  // 若当前输出张量是操作组的输入张量，则无需反向更新（已到达依赖链起点）
  if (std::find(lg_info.group_ins.begin(), lg_info.group_ins.end(),
                out.first) != lg_info.group_ins.end()) {
    return true;
  }

  // 获取生成当前输出张量的操作（生产者操作）
  auto op = out.first.getDefiningOp();
  // 特殊处理：BM1684系列芯片上的Conv2DOp，若启用3ic优化则返回失败（不支持反向切片）
  if (isa<tpu::Conv2DOp>(op) && module::isBM1684Family()) {
    auto conv_attr = dyn_cast<tpu::Conv2DOp>(op).parseParam();
    if (conv_attr.use_3ic_optimize) {
      return false;
    }
  }

  // 获取运行模式（如TPU动态模式）
  auto mode = getRunMode(op);
  // 将当前操作加入已处理集合（避免重复处理）
  op_set.insert(op);

  // 获取当前输出张量的切片信息（从tensor_infos中读取）
  slice_info_t &out_si = tensor_infos[out.first].slice_info;
  // 操作组的输入张量集合（用于判断是否为组外输入）
  auto &group_ins = lg_info.group_ins;

  // 遍历当前操作的所有输入张量（反向遍历依赖链）
  for (auto in : op->getOperands()) {
    slice_info_t si;  // 用于存储当前输入张量的切片信息
    auto pre_op = in.getDefiningOp();  // 生成当前输入张量的操作（上游生产者）

    // 跳过由NoneOp生成的输入（空操作，无实际数据）
    if (pre_op && isa<top::NoneOp>(pre_op)) {
      continue;
    }

    // 若输入张量无需分割（全量使用），直接生成覆盖全维度的切片信息
    if (is_value_dont_split(in)) {
      // 获取输入张量的N/C/D/H/W维度大小
      module::getNCDHW(in, n, c, d, h, w, lg_info.type);
      // 切片范围为全维度（从0到维度大小）
      si.n.emplace_back(std::pair(0, n));
      si.c.emplace_back(std::pair(0, c));
      si.d.emplace_back(std::pair(0, d));
      si.h.emplace_back(std::pair(0, h));
      si.w.emplace_back(std::pair(0, w));
      // 更新输入张量的切片信息
      tensor_infos[in] = tensor_info_t(si);
      continue;
    }

    // 标记输入张量是否需要在本地内存驻留
    bool hold_in_lmem = false;
    // 判断当前输入是否为操作组的输入张量（组外输入）
    bool is_group_in =
        std::find(group_ins.begin(), group_ins.end(), in) != group_ins.end();

    // 反向计算输入张量的切片信息：根据输出张量的切片out_si，推导输入张量应有的切片si
    // 参数说明：si（输出）、out_si（输出张量切片）、op（当前操作）、in（当前输入）、
    //          shape_secs（切片配置）、操作组类型、hold_in_lmem（是否驻留）、is_group_in（是否组输入）
    auto ret =
        get_backward_slice_info2(si, out_si, op, in, shape_secs, lg_info.type,
                                 hold_in_lmem, is_group_in);
    if (ret == false) {  // 切片信息计算失败，返回整体失败
      return false;
    }

    // 特殊处理：若上游操作是MaxPoolWithMaskOp（带掩码的最大池化），需同步更新其所有输出的切片信息
    if (pre_op && isa<tpu::MaxPoolWithMaskOp>(pre_op)) {
      for (int j = 0; j < pre_op->getNumResults(); j++) {
        auto res = pre_op->getResult(j);
        if (res == in) {  // 跳过当前输入张量（已单独处理）
          continue;
        }
        // 若该结果张量已存在切片信息，更新为当前计算的si
        if (tensor_infos.find(res) != tensor_infos.end()) {
          tensor_infos[res] = tensor_info_t(op, si);
        }
      }
    }

    // 检查当前输入张量是否已有切片信息
    auto iter = tensor_infos.find(in);
    if (iter != tensor_infos.end()) {
      // 若新计算的切片信息与已有信息不一致
      if (false == is_same_slice_info(si, iter->second.slice_info)) {
        // CV18xx芯片或TPU动态模式下，切片信息不一致直接返回失败
        if (module::isCV18xx() || mode == RunMode::TPU_DYNAMIC)
          return false;

        // 检查H/W维度的切片是否重叠（仅允许重叠时合并切片）
        bool is_hw_overlap = true;
        // 检查H维度切片是否重叠
        for (int i = 0; i < shape_secs.hsecs; i++) {
          is_hw_overlap *=
              std::max(si.h[i].first, iter->second.slice_info.h[i].first) <
              std::min(si.h[i].first + si.h[i].second,
                       iter->second.slice_info.h[i].first +
                           iter->second.slice_info.h[i].second);
        }
        // 检查W维度切片是否重叠
        for (int i = 0; i < shape_secs.wsecs; i++) {
          is_hw_overlap *=
              std::max(si.w[i].first, iter->second.slice_info.w[i].first) <
              std::min(si.w[i].first + si.w[i].second,
                       iter->second.slice_info.w[i].first +
                           iter->second.slice_info.w[i].second);
        }

        if (is_hw_overlap) {  // 若H/W切片重叠，合并切片信息（取最小范围覆盖两者）
          slice_info_t si_both;
          si_both.n = si.n;  // N维度沿用新切片
          si_both.c = si.c;  // C维度沿用新切片
          si_both.d = si.d;  // D维度沿用新切片
          // 合并H维度切片（取最低起点和最高终点）
          for (int i = 0; i < shape_secs.hsecs; i++) {
            int64_t h_lowest =
                std::min(si.h[i].first, iter->second.slice_info.h[i].first);
            int64_t h_highest =
                std::max(si.h[i].first + si.h[i].second,
                         iter->second.slice_info.h[i].first +
                             iter->second.slice_info.h[i].second);
            si_both.h.push_back(
                std::pair<int64_t, int64_t>(h_lowest, h_highest - h_lowest));
          }
          // 合并W维度切片（取最低起点和最高终点）
          for (int i = 0; i < shape_secs.wsecs; i++) {
            int64_t w_lowest =
                std::min(si.w[i].first, iter->second.slice_info.w[i].first);
            int64_t w_highest =
                std::max(si.w[i].first + si.w[i].second,
                         iter->second.slice_info.w[i].first +
                             iter->second.slice_info.w[i].second);
            si_both.w.push_back(
                std::pair<int64_t, int64_t>(w_lowest, w_highest - w_lowest));
          }
          // 更新输入张量的切片信息为合并后的si_both
          auto tmp = tensor_info_t(op, si_both);
          auto slice_infos = tensor_infos[in].slice_infos;
          for (auto itr = slice_infos.begin(); itr != slice_infos.end();
               ++itr) {
            tmp.add_slice_info(itr->first, itr->second);
          }
          tensor_infos[in] = tmp;
          tensor_infos[in].hold_in_lmem = hold_in_lmem;

          // 同步更新MaxPoolWithMaskOp的其他输出张量的切片信息
          if (pre_op && isa<tpu::MaxPoolWithMaskOp>(pre_op)) {
            for (int j = 0; j < pre_op->getNumResults(); j++) {
              auto res = pre_op->getResult(j);
              if (res == in) {
                continue;
              }
              auto tmp = tensor_info_t(op, si_both);
              auto slice_infos = tensor_infos[res].slice_infos;
              for (auto itr = slice_infos.begin(); itr != slice_infos.end();
                   ++itr) {
                tmp.add_slice_info(itr->first, itr->second);
              }
              tensor_infos[res] = tmp;
            }
          }
        } else {  // H/W切片不重叠，无法合并，返回失败
          return false;
        }
      } else {  // 新切片信息与已有一致，添加到切片信息列表
        tensor_infos[in].add_slice_info(op, si);
      }
    } else {  // 输入张量无已有切片信息，直接设置为新计算的si
      tensor_infos[in] = tensor_info_t(op, si);
      tensor_infos[in].hold_in_lmem = hold_in_lmem;
    }

    // 判断是否需要继续反向遍历该输入张量（若未到达依赖链终点）
    if (strip_back_judge2(in, lg_info, op_set, out_tensor_set)) {
      tensor_branchs.push_back(std::make_pair(in, op));  // 加入分支列表
    }
  }
  return true;  // 反向更新成功
}
```

### 7.backward_gen_ilp_var2

#### 功能概述

`backward_gen_ilp_var2` 是 ILP 调度优化的核心变量生成函数，其核心功能是从操作组的输出端反向遍历操作，为每个操作的输入 / 输出张量生成 ILP（整数线性规划）变量和约束条件。这些变量和约束建模了数据的加载（从全局到本地内存）、存储（从本地到全局内存）、数据驻留（本地内存中保留）等行为，同时考虑了硬件的内存容量限制和 DMA / 计算周期，最终为 ILP 求解器提供 “决策变量” 和 “可行解约束”，以找到最优的时间步调度方案。

#### 逻辑流程

1. 反向遍历操作：从操作组的最后一个操作（`cur_op_idx = ops.size() - 1`）向前遍历，实现 “反向” 生成变量 —— 从输出张量的需求出发，推导输入张量的供给，确保数据依赖关系被正确建模。
2. 操作合法性检查：计算当前操作所需的本地内存大小（`mem_size_for_load`）和缓冲大小，若内存大小为负或预加载字节数超限，标记失败操作（`failOp`）和模式（`failMode`），返回失败。
3. 输入张量处理：

   - 对于组外输入（生产者不在当前组）：生成加载变量（如 `x_weight_*_load_*`），建模从全局内存到本地内存的 DMA 传输，添加约束确保操作执行前数据已加载。
   - 对于组内输入（生产者在当前组）：计算生产者与消费者的时间步关系，若间隔较大，累加内存需求（数据需驻留本地内存）。
4. 输出张量处理：

   - 对于组内消费者：根据生产者与消费者的时间步间隔，生成数据驻留变量（`ada_var_*`）或存储 - 加载变量对（`x_tensor_*_store_*` 和 `x_tensor_*_load_*`），添加约束确保数据在需要时可用（如 “存储变量和 = 加载变量和”“先存储后加载”）。
   - 对于组外消费者：生成存储变量（`x_tensor_*_store_*`），建模从本地到全局内存的 DMA 传输，添加约束确保数据被正确输出。
5. 约束生成：通过 `ilp_timeStep.addConstraint` 添加各类约束（内存容量、时序关系、数据守恒），确保调度方案在硬件资源约束下可行。

#### 约束条件

##### 数据流转一致性原则

核心：数据的 “产生 - 使用 - 存储” 全链路必须守恒，避免数据丢失或冗余，确保上下游操作的数据匹配。

- 具体约束：
  - 若数据从本地内存存储到全局内存（`store_var`），后续必须从全局内存重新加载回本地内存（`load_var`），且 “存储变量之和 = 加载变量之和”（`sum(store_var) = sum(load_var)`）。
    例：代码中通过 `addConstraint(0, 0, coeff_var_items)` 强制存储与加载的数量一致，避免 “存储后未加载” 导致的数据丢失，或 “加载未存储的数据” 导致的无效操作。

##### 操作依赖满足原则

核心：操作执行时，其所需的输入数据必须已 “就绪”（要么在本地内存驻留，要么已从全局内存加载），避免 “数据未就绪就执行操作” 的逻辑错误。

- 具体约束：
  - 输入数据的可用性通过 “加载变量之和 + 驻留变量 = 1”（`sum(load_var) + reside_x = 1`）保证：要么通过 `load_var` 加载到本地，要么通过 `reside_x` 在本地驻留，两种方式必选其一。
    例：代码中对组外输入张量，通过 `addRowConstraint` 确保操作执行前至少有一个加载变量为 1；对组内数据，通过驻留变量 `reside_x` 约束确保数据在时间步间隔内持续可用。

##### 时序逻辑合理原则

核心：数据的 “存储 - 加载”“生产 - 消费” 必须遵循时序因果关系，避免 “先使用后产生”“先加载后存储” 的时序颠倒。

- 具体约束：
  - 若数据需要 “存储后重新加载”，则加载操作的时间步必须晚于存储操作的时间步。代码中通过 `2*reside_x + sum(加载时间步*load_var) - sum(存储时间步*store_var) ≥ 2` 约束实现：
    - 当数据不驻留（`reside_x=0`）时，强制 “加载时间步总和 > 存储时间步总和”，确保先存储后加载；
    - 当数据驻留（`reside_x=1`）时，通过系数 “2” 自动满足不等式，跳过时序检查（无需存储 - 加载）。

##### 硬件资源限制原则

核心：调度方案必须严格遵守硬件的物理资源上限（如本地内存容量、DMA 带宽），避免资源溢出导致的硬件错误。

- 具体约束：
  - 每个时间步的本地内存使用量（所有驻留数据、加载数据的总字节数）必须 ≤ 硬件本地内存容量（`backend::Arch::LMEM_BYTES`）。
    例：代码通过 `addTimestepMemUse` 记录每个时间步的内存使用，并隐式约束其不超过硬件上限；若预加载字节数超过当前操作的内存容量（`mem_size_for_load - load_bytes_for_next_ts < 0`），则直接返回失败。

##### 外部依赖满足原则

核心：若操作的输出需要被组外操作使用（下游依赖），必须确保数据已存储到全局内存，避免下游操作 “无数据可用”。

- 具体约束：
  - 组外用户依赖的输出张量，其存储变量之和必须为 1（`sum(store_var) = 1`），即必须在某个时间步执行存储操作。
    例：代码中对 `have_grp_out = true` 的输出张量，通过 `addConstraint(1, 1, coeff_var_items)` 强制存储操作必须发生，确保下游操作能从全局内存获取数据。

##### 求解效率平衡原则

核心：在保证约束完整性的前提下，限制变量数量和约束范围，避免 “变量爆炸” 导致 ILP 求解超时或不可行。

- 具体约束：
  - 通过 `max_ahead_or_delay_ts` 限制超前 / 延迟的时间步范围（如加载操作只能在操作执行前的 `max_ahead_or_delay_ts` 个时间步内生成变量），减少无效变量。
    例：代码中生成加载 / 存储变量时，若时间步间隔超过 `max_ahead_or_delay_ts` 则跳过，避免变量数量随时间步呈指数增长，平衡约束精度与求解效率。

##### 硬件特性适配原则

核心：约束需适配硬件特性（如 L2 缓存加速、DMA 带宽），通过调整周期参数优化调度效率，避免 “理论最优但硬件不支持” 的方案。

- 具体约束：
  - 当启用 L2 缓存优化（`l2m_en = true`）时，DMA 周期按硬件特性调整（`dma_cycle /= 4`），约束中的周期参数同步适配，确保周期计算与硬件实际性能一致。
    例：代码中对 L2 缓存加载的变量，其 DMA 周期约束按硬件加速比调整，避免因周期计算错误导致的调度方案实际不可行。

#### 核心原理

- 反向建模：从输出反向推导输入的原因是 “输出的调度时间决定输入的加载时间”—— 只有知道操作何时执行（输出时间步），才能确定输入需要何时加载到本地内存，避免过早加载占用内存或过晚加载导致操作等待。
- ILP 变量建模：用二进制变量（0/1）表示 “是否在某个时间步执行加载 / 存储”，用整数变量表示 “数据是否驻留本地内存”，将调度问题转化为 ILP 问题（目标：最小化总周期；约束：内存不超限、数据依赖满足）。
- 硬件资源约束：通过 `addTimestepMemUse` 限制每个时间步的内存使用量不超过硬件本地内存容量；通过 `addTimestepGdmaCycle` 和 `getLocalLayerCycle` 建模 DMA 和计算的周期开销，确保时序可行。
- 数据驻留优化：当生产者与消费者时间步间隔较小时，通过 “数据驻留”（不存储到全局内存）减少 DMA 传输，降低周期；间隔较大时，通过 “存储 - 加载” 释放内存，避免资源浪费 —— 这是平衡内存使用和周期的关键。

#### backward_gen_ilp_var2 代码

```cpp
// 反向生成ILP（整数线性规划）变量及约束，用于操作组的时间步调度优化
// 功能：从操作组的输出端反向遍历操作，为每个操作的输入/输出生成ILP变量（如加载、存储、数据驻留）
//       及约束条件（内存使用、周期限制），为后续ILP求解提供基础
// 参数：
//   ilp_lg_info - 当前ILP组信息
//   tensor_infos - 张量切片信息（存储/加载的分块信息）
//   cycle_calculator_ - 周期计算器（计算DMA/计算周期）
//   ilp_timeStep - ILP时间步管理器（存储变量、约束及时间步信息）
//   ncdhw_idx - 当前切片的NCDHW维度索引
//   slice_idx - 切片索引
//   op_var_bound - 操作变量的时间步边界（起始/结束时间步）
//   failOp - 输出：失败的操作（若生成失败）
//   failMode - 输出：失败模式（1/2，标识不同失败原因）
//   node_labels - 调试用节点标签（记录变量生成信息）
//   load_bytes_for_next_ts - 输出：下一时间步需要加载的字节数（预加载优化）
//   l2m_en - 是否启用L2缓存优化
//   max_ahead_or_delay_ts - 最大超前/延迟时间步（控制变量生成范围）
// 返回值：变量生成是否成功（true为成功）
bool backward_gen_ilp_var2(ilp_LgInfo &ilp_lg_info, TensorInfo &tensor_infos,
                           std::shared_ptr<CycleCalculator> cycle_calculator_,
                           ILPTimeStep &ilp_timeStep,
                           const std::vector<int64_t> &ncdhw_idx, int slice_idx,
                           std::vector<op_var_pos_info> &op_var_bound,
                           Operation *&failOp, int &failMode,
                           std::map<std::string, std::string> &node_labels,
                           int64_t &load_bytes_for_next_ts, bool l2m_en,
                           int max_ahead_or_delay_ts) {
  // 获取当前组的基础信息（含操作集合、输入输出等）
  auto lg_info = ilp_lg_info._lgInfo;
  // 生成当前切片的名称（用于变量命名，区分不同切片）
  std::string slice_name =
      llvm::formatv("slice_{0}_{1}_{2}_{3}_{4}", ncdhw_idx[0], ncdhw_idx[1],
                    ncdhw_idx[2], ncdhw_idx[3], ncdhw_idx[4]);
  // 反向遍历操作组（从最后一个操作到第一个），实现"反向"生成变量（从输出向输入推导）
  for (int cur_op_idx = ops.size() - 1; cur_op_idx >= 0; cur_op_idx--) {
    // 获取当前操作在当前切片中的时间步边界（起始/结束时间步、位置等）
    auto var_pos_info =
        findVarBound(op_var_bound, std::make_pair(slice_idx, cur_op_idx));
    auto op = ops[cur_op_idx]; // 当前处理的操作

    // 若操作为空（占位符），直接记录空操作信息，重置预加载字节数
    if (op == nullptr) {
      ilp_timeStep.addOpInfo(var_pos_info.ts_id, op, 0,
                             backend::Arch::LMEM_BYTES, 0);
      load_bytes_for_next_ts = 0;
      continue;
    }

    // 操作名称处理（用于调试标签）
    auto op_name = replaceChars_for_dot(module::getName(op).str());
    // 获取切片的全局时间步边界（用于映射操作与时间步的关系）
    auto slice_pos_info =
        findVarBound(op_var_bound, std::make_pair(slice_idx, 0));
    LAYER_GROUP_LOG_DEBUG_BLOCK({
      llvm::errs() << "-------------------cur_op_idx: " << cur_op_idx
                   << ", op: " << show_op_info(op) << " ----\n";
    });

    // 计算当前操作需要的本地内存大小（加载数据）和缓冲大小
    int buffer_size = 0;
    failMode = 0; // 初始化失败模式
    int64_t mem_size_for_load =
        getOpLmemBytes(op, tensor_infos, ncdhw_idx, ilp_lg_info, buffer_size);
    // 检查内存大小是否合法（负数值表示错误）
    if (mem_size_for_load < 0) {
      LAYER_GROUP_LOG_DEBUG_BLOCK(
          { llvm::errs() << "error, mem_size_for_load < 0\n"; });
      failOp = op; // 记录失败操作
      failMode = 1; // 模式1：内存大小错误
      return false;
    }
    // 检查预加载字节数是否超过当前操作的内存容量
    if (mem_size_for_load - load_bytes_for_next_ts < 0) {
      LAYER_GROUP_LOG_DEBUG_BLOCK({
        llvm::errs() << "error, mem_size_for_load:" << mem_size_for_load
                     << ", load_bytes_for_next_ts:" << load_bytes_for_next_ts
                     << "\n";
      });
      failOp = op;
      failMode = 2; // 模式2：预加载字节数超限
      return false;
    }

    // 计算当前操作的计算周期（本地层周期）
    auto type = lg_info.type == GROUP_MM_OPT3 ? GROUP_MM : lg_info.type;
    int bdc_cycle =
        cycle_calculator_->getLocalLayerCycle(op, tensor_infos, type, true);
    // 调试信息：内存大小、缓冲大小、切片索引
    tmpStr = "mem_size_for_load: " + std::to_string(mem_size_for_load) +
             ", buffer_size: " + std::to_string(buffer_size) +
             ", slice_idx:" + std::to_string(slice_idx);
    LAYER_GROUP_LOG_DEBUG_BLOCK({ llvm::errs() << tmpStr << "\n"; });
    node_labels[op_name] = tmpStr; // 记录调试标签
    // 将操作信息添加到时间步管理器（时间步ID、操作、缓冲大小、内存大小、计算周期）
    ilp_timeStep.addOpInfo(var_pos_info.ts_id, op, buffer_size,
                           mem_size_for_load, bdc_cycle);

    // 重置预加载字节数，处理当前操作的输入
    load_bytes_for_next_ts = 0;
    for (OpOperand &opd : op->getOpOperands()) {
      int opd_idx = opd.getOperandNumber();
      auto in = op->getOperand(opd_idx); // 输入张量
      auto inOp = in.getDefiningOp();   // 生成该输入张量的操作（生产者）

      // 跳过NoneOp（空操作）生成的输入
      if (inOp && isa<top::NoneOp>(inOp)) {
        continue;
      }
      // 若输入无生产者（如外部输入），用特殊地址标记（避免与组内操作冲突）
      if (!inOp) {
        inOp = (Operation *)0x1111;
      }

      // 判断输入张量是否无需分割（全量加载）
      bool is_not_split = is_value_dont_split(in);
      // 计算输入张量加载到本地内存的字节数（考虑EU对齐）
      int64_t lmem_bytes = getTensorLmemBytes(op, in, tensor_infos, ncdhw_idx,
                                              ilp_lg_info, is_eu_align(in));
      // 特殊处理：注意力组的矩阵乘法输出，加倍内存（适配算法需求）
      if (ilp_lg_info.p_special_grp &&
          ilp_lg_info.p_special_grp->name() == "attention_group" &&
          ilp_lg_info.shape_secs.h_slice_num > 1 && isa<tpu::MatMulOp>(op) &&
          ilp_lg_info.p_special_grp->ops.back() == op) {
        llvm::errs() << "inc res lmem_bytes for attention_grp\n";
        lmem_bytes *= 2;
      }

      std::string value_name = module::getName(in).str();
      // 检查生产者操作是否在当前组内
      auto itr = std::find(ops.begin(), ops.end(), inOp);
      if (itr == ops.end()) {
        // 生产者不在组内（外部输入）：需要从全局内存加载到本地内存
        tensor_info_t info;
        if (tensor_infos.find(in) != tensor_infos.end()) {
          info = tensor_infos[in]; // 获取输入张量的切片信息
        } else {
          assert(false); // 张量信息必须存在
        }
        info.mode2 |= TIMESTEP2_LOAD; // 标记为需要加载
        // 记录张量大小和预加载字节数
        ilp_timeStep.addTensorSize(in, slice_idx, lmem_bytes);
        load_bytes_for_next_ts += lmem_bytes;
        // 计算DMA加载周期（从全局到本地）
        int dma_cycle = cycle_calculator_->getGdmaCycle(in, info, lg_info.type);
        // 特殊组优化：L2缓存加载加速（周期除以4）
        if (ilp_lg_info.p_special_grp) {
          if (ilp_lg_info.value_load_to_l2m.find(in) !=
              ilp_lg_info.value_load_to_l2m.end()) {
            ilp_lg_info.value_load_to_l2m[in] = lmem_bytes;
            dma_cycle /= 4;
          }
        } else {
          // 非特殊组：L2缓存启用且张量不分割时，加载周期加速
          if (l2m_en && is_not_split) {
            dma_cycle /= 4;
          }
        }

        // 记录张量的DMA周期
        ilp_timeStep.addTensorCycle(in, slice_idx, dma_cycle);
        std::vector<std::string> var_names; // 存储生成的ILP变量名
        // 生成加载操作的ILP变量（时间步范围：起始时间步到当前操作时间步）
        int ts_idx = var_pos_info.start_ts;
        for (; ts_idx < var_pos_info.ts_id; ts_idx++) {
          // 限制超前加载的时间步范围（避免变量过多）
          if (var_pos_info.ts_id - ts_idx > max_ahead_or_delay_ts) {
            continue;
          }
          std::string var_name;
          // 根据是否分割，生成不同的变量名（权重/组输入）
          if (is_not_split) {
            var_name = llvm::formatv("x_weight_{0}_use_by_{1}_at_pos{2}_load_{"
                                     "3}bytes_{4}cycle_at_ts{5}_{6}",
                                     value_name.c_str(), op_name.c_str(),
                                     var_pos_info.ts_id, lmem_bytes, dma_cycle,
                                     ts_idx, slice_name.c_str())
                           .str();
          } else {
            var_name = llvm::formatv("x_grp_input_{0}_use_by_{1}_at_pos{2}_"
                                     "load_{3}bytes_{4}cycle_at_ts{5}_{6}",
                                     value_name.c_str(), op_name.c_str(),
                                     var_pos_info.ts_id, lmem_bytes, dma_cycle,
                                     ts_idx, slice_name.c_str())
                           .str();
          }
          // 记录变量对应的操作（用于调试标签）
          auto op3 = (ts_idx < slice_pos_info.ts_id)
                         ? ops[0]
                         : ops[ts_idx - slice_pos_info.ts_id];
          if (op3) {
            node_labels[module::getName(op3).str()] =
                "LoadVarDefine: " + var_name;
          }
          // 添加二进制变量（0/1，表示该时间步是否执行加载）
          ilp_timeStep.addBinaryVar(ts_idx, slice_idx, -1, var_name, in, info,
                                    lmem_bytes);
          var_names.push_back(var_name);
          // 记录该时间步的DMA周期和内存使用
          ilp_timeStep.addTimestepGdmaCycle(ts_idx, dma_cycle, var_name);
          ilp_timeStep.addTimestepMemUse(ts_idx, lmem_bytes, var_names);
        }

        // 判断是否加载到L2缓存（特殊组首切片或不分割的张量）
        bool load_to_l2m = false;
        if (ilp_lg_info.p_special_grp) {
          if (slice_idx == 0 && ilp_lg_info.value_load_to_l2m.find(in) !=
                                    ilp_lg_info.value_load_to_l2m.end()) {
            load_to_l2m = true;
          }
        } else {
          if (is_not_split) {
            load_to_l2m = true;
          }
        }
        // 添加行约束：确保输入张量在当前操作执行前已加载（至少一个加载变量为1）
        ilp_timeStep.addRowConstraint(var_pos_info.ts_id, in, var_names, false,
                                      load_to_l2m);
      } else {
        // 生产者在组内：计算生产者时间步（用于数据驻留优化）
        int producer_pos =
            slice_pos_info.ts_id + std::distance(ops.begin(), itr);
        // 若生产者与消费者时间步不相邻，累加内存（数据需驻留）
        if (producer_pos != var_pos_info.ts_id - 1) {
          load_bytes_for_next_ts += lmem_bytes;
        }
      }
    }

    // 处理当前操作的输出张量（结果）
    std::vector<std::pair<int, MPVariable *>> coeff_var_items; // 约束系数与变量
    for (int j = 0; j < op->getNumResults(); j++) {
      auto res = op->getResult(j); // 输出张量
      tensor_info_t &info = tensor_infos[res]; // 输出张量的切片信息
      std::string name = module::getName(res).str();
      std::string op_name = module::getName(op).str();
      LAYER_GROUP_LOG_DEBUG_BLOCK(
          { llvm::errs() << "process res name:" << name << "\n"; });
      // 计算输出张量在本地内存中的字节数
      int64_t lmem_bytes =
          getTensorLmemBytes(op, res, tensor_infos, ncdhw_idx, ilp_lg_info);
      assert(lmem_bytes > 0); // 内存大小必须为正

      // 特殊处理：注意力组的矩阵乘法输出，加倍内存
      if (ilp_lg_info.p_special_grp) {
        if (ilp_lg_info.p_special_grp->name() == "attention_group" &&
            ilp_lg_info.shape_secs.h_slice_num > 1 && isa<tpu::MatMulOp>(op) &&
            ilp_lg_info.p_special_grp->ops.back() == op) {
          llvm::errs() << "inc opd lmem_bytes for attention_grp\n";
          lmem_bytes *= 2;
        }
        // 记录需要存储到L2缓存的输出张量
        if (ilp_lg_info.value_store_to_l2m.find(res) !=
            ilp_lg_info.value_store_to_l2m.end()) {
          ilp_lg_info.value_store_to_l2m[res] = lmem_bytes;
        }
      }
      llvm::errs() << "res lmem_bytes:" << lmem_bytes << "\n";
      // 记录输出张量的大小
      ilp_timeStep.addTensorSize(res, slice_idx, lmem_bytes);

      // 收集使用该输出张量的用户操作（消费者）
      std::map<int, Operation *> map_user_pos; // 消费者时间步 -> 消费者操作
      bool have_grp_out = false; // 是否有组外用户（需要输出到全局内存）
      for (auto user : res.getUsers()) {
        auto itr = std::find(ops.begin(), ops.end(), user);
        if (itr != ops.end()) {
          // 组内用户：计算其时间步
          int consumer_pos =
              slice_pos_info.ts_id + std::distance(ops.begin(), itr);
          map_user_pos[consumer_pos] = user;
        } else {
          // 组外用户：标记需要输出
          have_grp_out = true;
        }
      }

      // 全切片的内存大小（用于存储/加载）
      int full_slice_bytes =
          getTensorLmemBytes(op, res, tensor_infos, ncdhw_idx, ilp_lg_info);
      int pre_user_pos = var_pos_info.ts_id; // 上一个用户的时间步
      int dma_cycle; // DMA周期
      std::vector<std::string> all_store_varnames; // 所有存储变量名
      std::string ada_var_name; // 自适应驻留变量名
      int idx = 0;
      bool first_user = true;

      // 处理组内用户（消费者）
      if (map_user_pos.size() > 0) {
        Operation *pre_user = nullptr; // 上一个用户操作
        // 遍历所有组内用户（按时间步排序）
        for (auto it : map_user_pos) {
          auto user_pos = it.first; // 消费者时间步
          auto user = it.second;    // 消费者操作
          llvm::errs() << "process " << idx << "th user, user_pos:" << user_pos
                       << " " << show_op_info(user)
                       << ", pre_user_pos:" << pre_user_pos << "\n";
          auto user_name = module::getName(user).str();
          MPVariable *reside_x = nullptr; // 数据驻留变量（0/1表示是否驻留）

          // 若生产者与消费者时间步间隔 >=2，需要考虑数据驻留
          if (user_pos - pre_user_pos >= 2) {
            // 生成自适应驻留变量名（控制数据是否在本地内存驻留）
            ada_var_name = llvm::formatv(
                "ada_var_for_{0}_gen_by_{1}_at_pos{2}_use_by_{3}_at_pos{4}",
                name.c_str(), op_name, var_pos_info.ts_id, user_name, user_pos);
            // 创建整数变量（0/1）：1表示数据驻留，0表示不驻留
            reside_x = ilp_timeStep.solver->MakeIntVar(0, 1, ada_var_name);
            // 记录变量的时间步和切片信息（用于约束）
            ilp_var_info var_info;
            var_info.ts_idx = pre_user_pos + 1;
            var_info.slice_idx = slice_idx;
            var_info.ilp_var = reside_x;
            ilp_timeStep.mapILPVarInfo[ada_var_name] = var_info;

            // 标记张量模式为"仅驻留"
            info.mode2 = TIMESTEP2_ONLY_RESIDE;
            ts_var_t tmp;
            tmp.varName = ada_var_name;
            tmp.value = res;
            tmp.info = info;
            tmp.lmem_bytes = full_slice_bytes;
            tmp.slice_idx = slice_idx;
            std::vector<std::string> varNames;
            varNames.push_back(ada_var_name);
            // 为时间步添加内存约束：驻留期间占用内存
            for (int ts_idx = pre_user_pos + 1; ts_idx < user_pos; ts_idx++) {
              ilp_timeStep.timestep_table_[ts_idx].vec_ts_var.push_back(tmp);
              ilp_timeStep.addTimestepMemUse(ts_idx, full_slice_bytes,
                                             varNames);
              auto tmp_op = ops[ts_idx - slice_pos_info.ts_id];
              if (tmp_op) {
                node_labels[module::getName(tmp_op).str()] =
                    "add " + ada_var_name + " to mem_contrains";
              }
            }
          }

          // 若时间步间隔 >=4，需要存储到全局内存再重新加载（避免长时间驻留占用内存）
          if (user_pos - pre_user_pos >= 4) {
            info.mode2 = TIMESTEP2_STORE_AND_LOAD;
            // 计算存储的DMA周期（本地到全局）
            dma_cycle = cycle_calculator_->getGdmaCycle(
                res, info, lg_info.type);
            ilp_timeStep.addTensorCycle(res, slice_idx, dma_cycle);
            llvm::errs() << "full_slice_bytes:" << full_slice_bytes
                         << ", dma_cycle:" << dma_cycle << "\n";

            // 生成存储操作的ILP变量
            std::vector<std::string> var_names;
            std::vector<std::pair<std::string, int>> store_var_names;
            llvm::errs() << "define store_var, ts_idx from " << pre_user_pos + 1
                         << " to " << user_pos << "\n";
            for (int ts_idx = pre_user_pos + 1; ts_idx < user_pos; ts_idx++) {
              std::string var_name = llvm::formatv(
                  "x_tensor_{0}_gen_at_pos{1}_store_{2}byte_{3}cycle_at_ts{4}_"
                  "use_by_{5}_at_pos{6}_{7}",
                  name.c_str(), var_pos_info.ts_id, full_slice_bytes, dma_cycle,
                  ts_idx, user_name, user_pos, slice_name.c_str());
              var_names.push_back(var_name);
              // 限制变量数量（避免过多）
              if (var_names.size() >= max_ahead_or_delay_ts) {
                break;
              }
            }
            // 添加存储变量及约束
            for (int ts_idx = pre_user_pos + 1, offset = 0; ts_idx < user_pos;
                 ts_idx++) {
              std::string var_name = llvm::formatv(
                  "x_tensor_{0}_gen_at_pos{1}_store_{2}byte_{3}cycle_at_ts{4}_"
                  "use_by_{5}_at_pos{6}_{7}",
                  name.c_str(), var_pos_info.ts_id, full_slice_bytes, dma_cycle,
                  ts_idx, user_name, user_pos, slice_name.c_str());
              llvm::errs() << "  AdaStoreDefine: " << var_name << "\n";
              auto tmp_op = ops[ts_idx - slice_pos_info.ts_id];
              if (tmp_op) {
                node_labels[module::getName(tmp_op).str()] =
                    "AdaStoreDefine: " + var_name;
              }
              // 添加二进制变量（0/1表示是否存储）
              ilp_timeStep.addBinaryVar(ts_idx, slice_idx, 0, var_name, res,
                                        info, full_slice_bytes);
              // 记录存储操作的DMA周期和内存使用
              ilp_timeStep.addTimestepGdmaCycle(ts_idx, dma_cycle, var_name);
              std::vector<std::string> var_names2;
              for (int n = offset++; n < var_names.size(); n++) {
                var_names2.push_back(var_names[n]);
              }
              store_var_names.push_back(std::make_pair(var_name, ts_idx));
              all_store_varnames.push_back(var_name);
              ilp_timeStep.addTimestepMemUse(ts_idx, lmem_bytes, var_names2);
              if (offset >= max_ahead_or_delay_ts) {
                break;
              }
            }

            // 生成加载操作的ILP变量（从全局重新加载到本地）
            dma_cycle = cycle_calculator_->getGdmaCycle(res, info, lg_info.type,
                                                        user, 1);
            var_names.clear();
            std::vector<std::pair<std::string, int>> load_var_names;
            llvm::errs() << "define load_var, ts_idx from " << pre_user_pos + 1
                         << " to " << user_pos << "\n";
            for (int ts_idx = pre_user_pos + 1; ts_idx < user_pos; ts_idx++) {
              // 限制加载超前时间步
              if (user_pos - ts_idx > max_ahead_or_delay_ts) {
                continue;
              }
              std::string var_name = llvm::formatv(
                  "x_tensor_{0}_gen_by_{1}_at_pos{2}_load_{3}bytes_{4}cycle_at_"
                  "ts{5}_use_by_{6}_at_pos{7}_{8}",
                  name.c_str(), op_name, var_pos_info.ts_id, full_slice_bytes,
                  dma_cycle, ts_idx, user_name.c_str(), user_pos,
                  slice_name.c_str());
              auto tmp_op = ops[ts_idx - slice_pos_info.ts_id];
              if (tmp_op) {
                node_labels[module::getName(tmp_op).str()] =
                    "AdaLoadDefine: " + var_name;
              }
              // 添加二进制变量（0/1表示是否加载）
              ilp_timeStep.addBinaryVar(ts_idx, slice_idx, 1, var_name, res,
                                        info, full_slice_bytes);
              var_names.push_back(var_name);
              load_var_names.push_back(std::make_pair(var_name, ts_idx));
              // 记录加载操作的DMA周期和内存使用
              ilp_timeStep.addTimestepGdmaCycle(ts_idx, dma_cycle, var_name);
              ilp_timeStep.addTimestepMemUse(ts_idx, full_slice_bytes,
                                             var_names);
            }

            // 添加约束1：存储变量之和 = 加载变量之和（数据守恒）
            coeff_var_items.clear();
            for (auto store_var : store_var_names) {
              coeff_var_items.push_back(std::make_pair(
                  1, ilp_timeStep.getMPVarByName(store_var.first)));
            }
            for (auto load_var : load_var_names) {
              coeff_var_items.push_back(std::make_pair(
                  -1, ilp_timeStep.getMPVarByName(load_var.first)));
            }
            ilp_timeStep.addConstraint(0, 0, coeff_var_items);

            // 添加约束2：加载变量之和 + 驻留变量 = 1（确保数据可用）
            coeff_var_items.clear();
            for (auto load_var : load_var_names) {
              coeff_var_items.push_back(std::make_pair(
                  1, ilp_timeStep.getMPVarByName(load_var.first)));
            }
            coeff_var_items.push_back(std::make_pair(1, reside_x));
            ilp_timeStep.addConstraint(1, 1, coeff_var_items);

            // 添加约束3：时间步约束（确保先存储后加载）
            coeff_var_items.clear();
            for (auto load_var : load_var_names) {
              coeff_var_items.push_back(
                  std::make_pair(load_var.second,
                                 ilp_timeStep.getMPVarByName(load_var.first)));
            }
            for (auto store_var : store_var_names) {
              coeff_var_items.push_back(
                  std::make_pair(-1 * store_var.second,
                                 ilp_timeStep.getMPVarByName(store_var.first)));
            }
            coeff_var_items.push_back(std::make_pair(2, reside_x));
            // 2*驻留变量 + 加载时间步之和 - 存储时间步之和 >= 2（确保时序正确）
            ilp_timeStep.addConstraint(2, MPSolver::infinity(),
                                       coeff_var_items);
          } else {
            // 时间步间隔较小：直接驻留，无需存储加载
            if (reside_x) {
              // 约束：驻留变量必须为1（数据保持驻留）
              coeff_var_items.clear();
              coeff_var_items.push_back(std::make_pair(1, reside_x));
              ilp_timeStep.addConstraint(1, 1, coeff_var_items);
            }
            if (pre_user) {
              assert(idx != 0);
              // 记录前一个用户操作与当前输出的驻留关系
              ilp_timeStep.resideOpInValue(pre_user, res);
            }
          }
          pre_user_pos = user_pos;
          pre_user = user;
          idx++;
          first_user = false;
        }

        // 若存在组外用户，确保数据已存储到全局内存
        if (all_store_varnames.size() > 0) {
          if (have_grp_out) {
            // 约束：存储变量之和 = 1（必须存储一次）
            coeff_var_items.clear();
            for (auto store_var : all_store_varnames) {
              coeff_var_items.push_back(
                  std::make_pair(1, ilp_timeStep.getMPVarByName(store_var)));
            }
            ilp_timeStep.addConstraint(1, 1, coeff_var_items);
            have_grp_out = false;
          } else {
            // 将输出添加到返回操作（组内闭环）
            ilp_timeStep.addNewOutIntoReturnOp(all_store_varnames, res);
          }
        }
      }

      // 处理组外用户：生成存储操作变量（输出到全局内存）
      if (have_grp_out) {
        info.mode2 = TIMESTEP2_STORE; // 标记为需要存储
        // 计算输出张量的内存大小和DMA周期
        int64_t lmem_bytes =
            getTensorLmemBytes(op, res, tensor_infos, ncdhw_idx, ilp_lg_info);
        int dma_cycle =
            cycle_calculator_->getGdmaCycle(res, info, lg_info.type);
        std::vector<std::string> var_names; // 存储变量名
        // 确定存储操作的时间步范围
        int ts_idx = pre_user_pos + 1;
        int end_idx = var_pos_info.end_ts + 1;
        if (ts_idx >= end_idx) {
          end_idx = ts_idx + 1;
        }
        int ts_idx2 = ts_idx;
        // 生成存储变量名（限制数量）
        for (; ts_idx < end_idx; ts_idx++) {
          std::string var_name = llvm::formatv(
              "x_tensor_{0}_gen_at_pos{1}_store_{2}byte_{3}cycle_at_ts{4}_{5}",
              name.c_str(), var_pos_info.ts_id, lmem_bytes, dma_cycle, ts_idx,
              slice_name.c_str());
          auto op3 = (ts_idx - slice_pos_info.ts_id > ops.size() - 1)
                         ? ops[ops.size() - 1]
                         : ops[ts_idx - slice_pos_info.ts_id];
          if (op3) {
            node_labels[module::getName(op3).str()] =
                "StoreDefine: " + var_name;
          }
          var_names.push_back(var_name);
          if (var_names.size() >= max_ahead_or_delay_ts) {
            break;
          }
        }
        // 添加存储变量及约束
        ts_idx = ts_idx2;
        for (int offset = 0; ts_idx < end_idx; ts_idx++) {
          std::string var_name = llvm::formatv(
              "x_tensor_{0}_gen_at_pos{1}_store_{2}byte_{3}cycle_at_ts{4}_{5}",
              name.c_str(), var_pos_info.ts_id, lmem_bytes, dma_cycle, ts_idx,
              slice_name.c_str());
          // 添加二进制变量（0/1表示是否存储）
          ilp_timeStep.addBinaryVar(ts_idx, slice_idx, -1, var_name, res, info,
                                    lmem_bytes);
          // 记录存储操作的DMA周期和内存使用
          ilp_timeStep.addTimestepGdmaCycle(ts_idx, dma_cycle, var_name);
          std::vector<std::string> var_names2;
          for (int n = offset++; n < var_names.size(); n++) {
            var_names2.push_back(var_names[n]);
          }
          ilp_timeStep.addTimestepMemUse(ts_idx, lmem_bytes, var_names2);
          if (offset >= max_ahead_or_delay_ts) {
            break;
          }
        }
        // 添加约束：确保输出张量被存储（至少一个存储变量为1）
        if (var_names.size() > 0) {
          ilp_timeStep.addRowConstraint(pre_user_pos, res, var_names, true,
                                        false);
        }
      }
    }
  }
  return true; // 变量生成成功
}
```

### 8.get_sec_per_cores

#### 功能分析

`get_sec_per_cores` 是多核心并行计算中的关键调度函数，核心功能是将张量的所有切片（按 N/C/D/H/W 维度划分）均衡分配到指定数量的计算核心上，确保每个核心处理的切片数量尽可能均匀，为并行计算提供切片分配方案。

具体来说，它解决了两个核心问题：

1. 如何根据张量的切片配置（各维度的切片数量）生成所有可能的切片索引（N/C/D/H/W 坐标）；
2. 如何将这些切片公平分配到多个核心，避免单个核心负载过重（如处理过多切片），提高并行效率。

#### 逻辑流程

函数遵循 “切片配置解析 → 总切片数计算 → 切片索引生成 → 核心分配” 的逻辑，具体步骤如下：

1. 特殊场景判断：判断是否为 “注意力组” 且 H 维度不作为批次，若是则仅处理 N/C 维度的切片（减少计算复杂度）。
2. 总切片数计算：根据是否仅处理 N/C 维度，调用 `shape_secs.get_sec_num` 计算总切片数；若总切片数小于核心数，调整为处理所有维度（确保每个核心至少分配 1 个切片）。
3. 切片索引生成：

   - 若 N 维度存在切片（`n_slice_num > 0`）：根据是否将 H 维度作为批次，生成 N/C/H（或 N/C/H 额外拆分）的切片索引（D/W 固定为 0）；
   - 若 N 维度无切片：遍历 N/C/D/H/W 所有维度的切片数，生成完整的五维索引。
4. 核心分配：

   - 先计算每个核心的基础切片数（`secs_per_core = 总切片数 / 核心数`），平均分配；
   - 对无法整除的剩余切片，依次分配给前几个核心（每个核心多 1 个），确保负载均衡。
5. 调试输出：若开启调试模式，打印每个核心分配的切片索引及各维度大小。

#### 3.原理说明

该函数的设计基于并行计算的 “负载均衡” 原则和深度学习张量的 “多维度切片” 特性，核心原理包括：

1. 多维度切片的并行性：深度学习张量（如特征图）通常具有 N（批次）、C（通道）、D（深度）、H（高度）、W（宽度）五维结构，可按维度拆分生成多个独立切片，每个切片可由不同核心并行处理（如不同批次或不同通道的特征图可独立计算）。
2. 负载均衡策略：通过 “平均分配 + 余数补充分配” 确保每个核心处理的切片数量差异不超过 1（如 10 个切片分配给 3 个核心，分别处理 4、3、3 个），避免核心间负载不均导致的整体效率下降。
3. 特殊场景适配：针对 “注意力组” 等特殊操作组，通过 `only_nc` 控制仅处理 N/C 维度，减少不必要的切片拆分（因注意力机制可能对 H/W 维度有连续性要求）；当 H 维度作为批次时，将 N 维度的超额切片分配到 H 维度，灵活适配算法需求。
4. 硬件并行能力利用：通过将切片映射到多个核心，充分利用多核心硬件的并行计算能力（如 GPU 多流处理器、TPU 多核阵列），缩短整体计算时间。

#### 4.get_sec_per_cores 代码

```cpp
// 将张量的切片（N/C/D/H/W维度）分配到多个计算核心，实现并行处理
// 功能：根据操作组的切片配置和核心数量，计算每个核心应处理的张量切片（N/C/D/H/W索引），
//       确保切片分配尽可能均衡，为多核心并行计算提供基础
// 参数：
//   sub_group - 子操作组信息（包含切片配置、特殊组标识等）
//   vec_ncdhw - 输出：核心ID到该核心处理的切片索引列表的映射（每个切片用N/C/D/H/W索引表示）
//   core_num - 可用的计算核心数量
//   slice_info - 张量的切片信息（包含各维度的切片范围）
void get_sec_per_cores(
    ilp_LgInfo &sub_group,
    std::map<int64_t, std::vector<std::vector<int64_t>>> &vec_ncdhw,
    int core_num, slice_info_t &slice_info) {
  // 判断是否仅处理N/C维度（特殊处理：注意力组且H维度不作为批次时）
  bool only_nc = sub_group.p_special_grp
                     ? sub_group.p_special_grp->name() == "attention_group" &&
                           !sub_group.p_special_grp->hdim_is_batch
                     : false;
  
  // 获取操作组的切片配置（各维度的切片数量）
  auto shape_secs = sub_group.shape_secs;
  // 计算总切片数（根据是否仅处理N/C维度）
  int secs = shape_secs.get_sec_num(only_nc);
  
  // 若总切片数小于核心数，调整为处理所有维度（增加总切片数，确保每个核心至少分配1个切片）
  if (secs < core_num) {
    only_nc = false;
    secs = shape_secs.get_sec_num(only_nc);
  }
  
  // 计算每个核心平均分配的切片数
  int secs_per_core = secs / core_num;
  // 清空输出容器
  vec_ncdhw.clear();
  // 临时存储所有切片的N/C/D/H/W索引
  std::vector<std::vector<int64_t>> tmp_ncdhws;
  
  // 生成所有切片的N/C/D/H/W索引（分两种情况处理）
  if (shape_secs.n_slice_num > 0) {  // 若N维度存在切片
    if (sub_group.p_special_grp->hdim_is_batch) {  // 特殊组中H维度作为批次时
      int n_slice = shape_secs.n_slice_num, h_slice = 1;
      // 若N维度切片数超过N维度大小，将多余部分分配到H维度
      if (shape_secs.n_slice_num > shape_secs.shape_0) {
        n_slice = shape_secs.shape_0;  // N维度切片数不超过其大小
        h_slice = shape_secs.n_slice_num / shape_secs.shape_0;  // 剩余切片分配到H维度
      }
      // 生成N/H/C维度的切片索引（D/W固定为0）
      for (int h = 0; h < h_slice; h++) {
        for (int c = 0; c < shape_secs.c_slice_num; c++) {
          for (int n = 0; n < n_slice; n++) {
            std::vector<int64_t> tmp = {n, c, 0, h, 0};  // N/C/D/H/W索引
            tmp_ncdhws.push_back(tmp);
          }
        }
      }
    } else {  // 非H维度作为批次的情况
      // H维度切片数：仅处理N/C时为1，否则使用配置的H切片数
      int h_slice_num = only_nc ? 1 : shape_secs.h_slice_num;
      // 生成N/C/H维度的切片索引（D/W固定为0）
      for (int h = 0; h < h_slice_num; h++) {
        for (int c = 0; c < shape_secs.c_slice_num; c++) {
          for (int n = 0; n < shape_secs.n_slice_num; n++) {
            std::vector<int64_t> tmp = {n, c, 0, h, 0};  // N/C/D/H/W索引
            tmp_ncdhws.push_back(tmp);
          }
        }
      }
    }
  } else {  // N维度无切片时，遍历所有维度（N/C/D/H/W）的切片
    for (int n = 0; n < shape_secs.nsecs; n++) {
      for (int c = 0; c < shape_secs.csecs; c++) {
        for (int d = 0; d < shape_secs.dsecs; d++) {
          for (int h = 0; h < shape_secs.hsecs; h++) {
            for (int w = 0; w < shape_secs.wsecs; w++) {
              std::vector<int64_t> tmp = {n, c, d, h, w};  // N/C/D/H/W索引
              tmp_ncdhws.push_back(tmp);
            }
          }
        }
      }
    }
  }
  
  // 将切片分配到各个核心
  int idx = 0;  // 切片索引计数器
  // 先平均分配每个核心的基础切片数
  for (int i = 0; i < core_num; i++) {
    vec_ncdhw[i] = std::vector<std::vector<int64_t>>();  // 初始化核心i的切片列表
    for (int j = 0; j < secs_per_core; j++) {
      vec_ncdhw[i].push_back(tmp_ncdhws[idx++]);  // 分配第idx个切片给核心i
    }
  }
  
  // 处理剩余切片（总切片数不能被核心数整除时）
  int rest = secs - core_num * secs_per_core;  // 剩余切片数
  if (rest > 0) {
    // 依次将剩余切片分配给前rest个核心（每个核心多分配1个）
    for (int i = 0; i < core_num; i++) {
      if (--rest < 0) {
        break;  // 剩余切片分配完毕
      }
      vec_ncdhw[i].push_back(tmp_ncdhws[idx++]);
    }
  }
  
  // 调试信息：输出每个核心分配的切片详情
  if (module::isDebugCmdEnable("detail_info_show")) {
    llvm::errs() << "vec_ncdhw, core num:" << vec_ncdhw.size() << "\n";
    for (int i = 0; i < vec_ncdhw[0].size(); i++) {  // 按切片行遍历
      llvm::errs() << "  row" << i << "\n";
      for (int j = 0; j < core_num; j++) {  // 按核心列遍历
        if (i < vec_ncdhw[j].size()) {  // 核心j有第i个切片
          auto &ncdhw = vec_ncdhw[j][i];  // 切片的N/C/D/H/W索引
          llvm::errs() << "    col" << j << "[" << ncdhw[0] << "," << ncdhw[1]
                       << "," << ncdhw[2] << "," << ncdhw[3] << "," << ncdhw[4]
                       << "]  ";
          // 输出该切片在各维度的大小
          llvm::errs() << "slice:[" << slice_info.n[ncdhw[0]].second << ","
                       << slice_info.c[ncdhw[1]].second << ","
                       << slice_info.d[ncdhw[2]].second << ","
                       << slice_info.h[ncdhw[3]].second << ","
                       << slice_info.w[ncdhw[4]].second << "]";
        }
      }
      llvm::errs() << "\n";
    }
  }
}
```

## 8./LayerGroup/opt3/MatmulGroup.cpp

### 1.single_matmul_group

#### 功能分析

该类 `single_matmul_group` 是深度学习框架中用于识别和优化单矩阵乘法（MatMul）相关操作组的模块，主要功能包括：

1. 模式匹配：识别以矩阵乘法操作（`tpu::MatMulOp`）为核心的操作序列。
2. 操作收集与整理：收集矩阵乘法周围的元素级操作（如 Slice、Add、Reshape 等），并按顺序排序。
3. 操作优化：移除不必要的 Reshape 操作，调整操作序列以提高计算效率（如跳过冗余的形状转换）。
4. 有效性检查：确保识别的操作组是有效的，可用于后续的算子融合或硬件加速。
5. 类型转换：支持将当前操作组转换为其他类型的特殊层组，便于灵活处理不同场景。

#### 逻辑流程

1. 入口检查：`pattern_match_and_parser` 函数首先判断起始操作是否为矩阵乘法，若不是则直接返回失败。
2. 核心初始化：若匹配矩阵乘法，记录主操作并收集周围的相关操作。
3. 维度检查：通过 Slice 操作的输入输出维度，判断是否存在列切割（`col_cut`）。
4. 冗余操作移除：针对 Reshape 操作，检查是否可跳过（如 Reshape 前后无实际形状变化需求），标记并移除冗余操作。
5. 操作树构建：以主矩阵乘法为根，构建相关操作树，确定可用于水平切割的操作（`h_cut_ops`）。
6. 有效性验证：最终检查操作组的有效性，返回匹配结果。
7. 类型转换：`convert_to_other_type` 函数支持将当前组转换为其他类型，复用主矩阵乘法和切割操作信息。

#### 原理说明

该类基于算子模式识别与优化的思想，针对深度学习中 Transformer 等模型的 MLP（多层感知器）模块中常见的矩阵乘法操作，通过以下原理实现优化：

1. 模式匹配机制：通过检查操作类型（如 `MatMulOp`）识别核心计算节点，再扩展收集周边相关操作（如 Elementwise 操作），形成有意义的计算子图。
2. 冗余操作消除：Reshape 等形状转换操作在部分场景下是冗余的（如仅为适配中间操作的形状要求，无实际计算意义），通过替换操作间的依赖关系（`replaceUsesWithIf`），可跳过这些操作，减少计算开销。
3. 子图有效性保证：通过 `check_group_valid` 等方法确保优化后的操作组在语法和语义上合法，避免破坏计算逻辑。
4. 灵活性设计：通过 `clone` 和 `convert_to_other_type` 支持对象复制和类型转换，便于集成到更复杂的层组管理和优化框架中。

#### 4.single_matmul_group 代码

```cpp
// 单矩阵乘法组类，继承自特殊层组基类
class single_matmul_group : public speical_layer_group_base {
public:
  // 克隆函数，创建当前类的共享指针副本（原型模式）
  virtual std::shared_ptr<speical_layer_group_base> clone() override {
    return std::make_shared<single_matmul_group>();
  }

  // 模式匹配与解析函数：检查起始操作是否符合模式，并解析相关子网络操作
  // 参数：start_op-起始操作；subnet_ops-子网络操作列表
  // 返回：是否匹配成功
  virtual bool
  pattern_match_and_parser(Operation *start_op,
                           std::vector<Operation *> &subnet_ops) override {
    // 若起始操作是矩阵乘法操作（tpu::MatMulOp），则进入匹配逻辑
    if (isa<tpu::MatMulOp>(start_op)) {
      main_mm_op = start_op;  // 记录主矩阵乘法操作
      // 打印日志：找到单矩阵乘法组及其名称
      llvm::errs() << "find single_matmul_group at "
                   << module::getName(start_op).str() << "\n";
      // 收集矩阵乘法周围的元素级操作（Elementwise Op），存入subnet_ops和当前类的ops列表
      CollectElementwiseOpAroundMatmul(start_op, subnet_ops, ops);
      // 按子网络操作顺序对收集到的操作重新排序
      auto ops_reorder = sortOpsByOtherOpsOrder(subnet_ops, ops);
      ops.assign(ops_reorder.begin(), ops_reorder.end());

      // 检查Slice操作的输入输出高度是否一致，判断是否存在列切割（col_cut）
      for (auto op : ops) {
        if (isa<tpu::SliceOp>(op)) {  // 若为Slice操作
          // 获取输入和输出的NCDHW维度（深度学习中常用的维度格式：N-批次，C-通道，D-深度，H-高度，W-宽度）
          int64_t in_n, in_c, in_d, in_h, in_w, out_n, out_c, out_d, out_h, out_w;
          module::getNCDHW(op->getOperand(0), in_n, in_c, in_d, in_h, in_w, GROUP_MM_OPT3);
          module::getNCDHW(op->getResult(0), out_n, out_c, out_d, out_h, out_w, GROUP_MM_OPT3);
          // 若输入高度不等于输出高度，则不存在列切割
          if (in_h != out_h) {
            col_cut = false;
            break;
          }
        }
      }

      // 处理Reshape操作：优化操作序列，移除不必要的Reshape
      std::vector<Operation *> del_ops, del_ops2;  // 存储待删除的操作
      for (auto op : ops) {
        if (isa<tpu::ReshapeOp>(op)) {  // 若为Reshape操作
          auto next_op = *(op->getUsers().begin());  // 获取Reshape的下一个用户操作
          // 若Reshape后接Slice或Add，且下一个操作在当前组内
          if (isa<tpu::SliceOp, tpu::AddOp>(next_op) &&
              std::find(ops.begin(), ops.end(), next_op) != ops.end()) {
            // 获取Reshape的输入定义操作（即Reshape的源操作）
            auto mmOp = op->getOperand(0).getDefiningOp();
            // 若源操作是矩阵乘法，且矩阵乘法的输入是Reshape操作
            if (mmOp && isa<tpu::MatMulOp>(mmOp)) {
              auto MMinReshapeOp = dyn_cast<tpu::MatMulOp>(mmOp).getInput().getDefiningOp();
              if (MMinReshapeOp && isa<tpu::ReshapeOp>(MMinReshapeOp)) {
                // 检查MMinReshapeOp是否有组外用户（若没有则可删除）
                bool has_out_user = false;
                for (auto user : MMinReshapeOp->getUsers()) {
                  if (find(ops.begin(), ops.end(), user) == ops.end()) {
                    has_out_user = true;
                    break;
                  }
                }
                if (!has_out_user) {
                  del_ops.push_back(MMinReshapeOp);  // 标记MMinReshapeOp为待删除
                }
                del_ops.push_back(op);  // 标记当前Reshape为待删除

                // 保存旧类型，用于后续类型调整
                auto oldType1 = MMinReshapeOp->getOperand(0).getType();
                auto oldType2 = op->getResult(0).getType();
                // 替换组内操作对MMinReshapeOp结果的使用，改为直接使用其输入（跳过Reshape）
                MMinReshapeOp->getResult(0).replaceUsesWithIf(
                    MMinReshapeOp->getOperand(0), [&](OpOperand &operand) {
                      Operation *user = operand.getOwner();
                      return find(ops.begin(), ops.end(), user) != ops.end();
                    });
                // 替换组内操作对当前Reshape结果的使用，改为直接使用矩阵乘法的结果（跳过Reshape）
                op->getResult(0).replaceUsesWithIf(
                    mmOp->getResult(0), [&](OpOperand &operand) {
                      Operation *user = operand.getOwner();
                      return find(ops.begin(), ops.end(), user) != ops.end();
                    });
                // 恢复矩阵乘法的输入和输出类型
                mmOp->getOperand(0).setType(oldType1);
                mmOp->getResult(0).setType(oldType2);
              }
            }
          }
        }
      }

      // 注释部分：另一处Reshape优化逻辑（可能因未完成或暂不启用而注释）
      // 逻辑目的：调整Reshape与Cast的顺序（matmul + reshape + cast -> matmul + cast + reshape）
      // for (auto op: ops) {
      //   if (isa<tpu::ReshapeOp>(op) && op->hasOneUse()) {
      //     auto next_op = *(op->getUsers().begin());
      //     if (isa<tpu::CastOp>(next_op) && std::find(ops.begin(), ops.end(), next_op) != ops.end()) {
      //       auto mmOp = op->getOperand(0).getDefiningOp();
      //       if (mmOp && isa<tpu::MatMulOp>(mmOp) && mmOp->hasOneUse()) {
      //         auto MMinReshapeOp = dyn_cast<tpu::MatMulOp>(mmOp).getInput().getDefiningOp();
      //         if (MMinReshapeOp && !isa<tpu::ReshapeOp>(MMinReshapeOp)) {
      //           // 类型调整和使用替换逻辑...
      //         }
      //       }
      //     }
      //   }
      // }

      // 查找边缘的Reshape操作（可能在组的边界，可删除）
      findReshapeAtEdge(ops, del_ops2);
      // 从当前操作列表中移除边缘Reshape
      for (auto del_op : del_ops2) {
        ops.erase(std::remove(ops.begin(), ops.end(), del_op), ops.end());
      }

      // 去重待删除操作，并从当前操作列表中移除，同时记录到need_del_ops（后续实际删除）
      std::sort(del_ops.begin(), del_ops.end());
      auto last = std::unique(del_ops.begin(), del_ops.end());
      del_ops.erase(last, del_ops.end());
      for (auto del_op : del_ops) {
        llvm::errs() << "del_op: " << module::getName(del_op).str() << "\n";
        ops.erase(std::remove(ops.begin(), ops.end(), del_op), ops.end());
        need_del_ops.push_back(del_op);
      }

      // 构建操作树：以start_op为根，查找相关操作，填充h_cut_ops（可能用于水平切割的操作）
      std::vector<Operation *> break_ops, accessed_ops;
      find_op_tree_by_root2(start_op, h_cut_ops, ops, accessed_ops, break_ops);
      // 从h_cut_ops中移除主矩阵乘法操作（避免自身被切割）
      h_cut_ops.erase(std::remove(h_cut_ops.begin(), h_cut_ops.end(), start_op),
                      h_cut_ops.end());
      // 检查组是否有效并返回结果
      return check_group_valid();
    }
    // 若起始操作不是矩阵乘法，匹配失败
    return false;
  }

  // 返回组名称
  virtual std::string name() override { return "single_matmul_group"; }
  // 返回组的简要描述（Transformer块中的MLP）
  virtual std::string brief() override { return "mlp in transformer block"; }

  // 转换为其他类型的特殊层组
  // 参数：sub_ops-子操作列表；p_special_grp-目标特殊层组指针
  // 返回：是否转换成功
  virtual bool convert_to_other_type(
      std::vector<Operation *> &sub_ops,
      std::shared_ptr<speical_layer_group_base> &p_special_grp) override {
    // 若子操作组无效，返回失败
    if (!grp_is_valid(sub_ops)) {
      return false;
    }
    // 查找子操作中的矩阵乘法操作，设置到目标组中
    for (auto op : sub_ops) {
      if (isa<tpu::MatMulOp>(op)) {
        p_special_grp->main_mm_op = op;  // 设置目标组的主矩阵乘法操作
        // 复制当前组的h_cut_ops到目标组
        p_special_grp->h_cut_ops.assign(h_cut_ops.begin(), h_cut_ops.end());
        return true;
      }
    }
    return false;
  }
};
```

### 2.mlp_group

#### 功能分析

该类 `mlp_group` 是深度学习框架中用于识别和优化 Transformer 模型中 MLP（多层感知器）模块相关操作组的模块，主要功能包括：

1. 模式匹配：识别以两个矩阵乘法操作（`tpu::MatMulOp`）为核心的 MLP 特征操作序列（MLP 通常包含 “线性层（MatMul）+ 激活函数 + 线性层（MatMul）” 结构）。
2. 冗余操作优化：移除激活函数（`tpu::ActiveOp`）前后的冗余 Reshape 操作（无实际形状转换意义的中间操作），简化计算流程。
3. 切割可行性判断：通过 Concat 操作的维度检查，判断是否支持列切割（`col_cut`），为后续并行计算或硬件加速提供依据。
4. 类型转换支持：可灵活转换为单矩阵乘法组（`single_matmul_group`），适配不同场景的优化需求。
5. 有效性验证：确保识别的 MLP 操作组合法有效，可用于后续的算子融合或性能优化。

#### 逻辑流程

1. 核心模式识别：`pattern_match_and_parser` 函数通过 `search_two_mmOp` 查找起始操作相关的两个矩阵乘法操作（MLP 的核心计算节点），若未找到则直接返回失败。
2. 过滤无效操作：排除包含 `HdimIsBatch` 属性的矩阵乘法（此类操作不适合 MLP 组优化）。
3. 冗余 Reshape 移除：

   - 处理激活函数后的 Reshape：若 Reshape 无组外用户，用激活函数的输出直接替换 Reshape 的输出，移除冗余转换。
   - 处理激活函数前的 Reshape：若 Reshape 无组外用户，用 Reshape 的输入直接替换其输出，跳过冗余转换。
4. 切割可行性判断：通过 Concat 操作的输入维度与拼接轴的关系，判断是否禁用列切割（`col_cut`）。
5. 有效性验证：确保操作组包含至少两个操作且通过有效性检查，返回匹配结果。
6. 灵活转换：`convert_to_other_type` 支持将操作组转换为单矩阵乘法组，适配不同优化场景。

#### 原理说明

该类基于 MLP 模块的典型结构特征实现优化，核心原理包括：

1. 模式识别依据：Transformer 中的 MLP 通常遵循 “线性变换（MatMul）→ 激活（如 GELU）→ 线性变换（MatMul）” 的固定结构，因此通过识别 “两个 MatMul + 激活函数” 的操作序列即可定位 MLP 模块。
2. 冗余操作消除：Reshape 操作在 MLP 中常作为中间适配层存在（如调整张量维度以匹配下一层输入），但部分 Reshape 无实际形状变化（或仅为临时适配），通过替换操作依赖关系（`replaceUsesWithIf`）可跳过这些操作，减少计算开销。
3. 切割可行性判断：Concat 操作的维度若与拼接轴冲突，可能导致列切割（并行计算的一种方式）失败，因此通过维度检查禁用冲突场景下的 `col_cut`，确保计算正确性。
4. 多场景适配：通过 `convert_to_other_type` 支持与单矩阵乘法组的转换，满足 MLP 模块中不同子结构的优化需求，提升框架灵活性。

#### 4.mlp_group 代码

```cpp
// MLP组类，继承自特殊层组基类（注：speical应为special的拼写错误）
class mlp_group : public speical_layer_group_base {
public:
  // 克隆函数，创建当前类的共享指针副本（原型模式）
  virtual std::shared_ptr<speical_layer_group_base> clone() override {
    return std::make_shared<mlp_group>();
  }

  // 模式匹配与解析函数：检查起始操作是否符合MLP组模式，并解析相关子网络操作
  // 参数：start_op-起始操作；subnet_ops-子网络操作列表
  // 返回：是否匹配成功
  virtual bool
  pattern_match_and_parser(Operation *start_op,
                           std::vector<Operation *> &subnet_ops) override {
    Operation *next_matmul_op = nullptr;  // 第二个矩阵乘法操作指针
    // 查找起始操作相关的两个矩阵乘法操作（核心逻辑：MLP通常包含多个MatMul）
    if (search_two_mmOp(start_op, next_matmul_op, subnet_ops)) {
      auto mmOp = dyn_cast<tpu::MatMulOp>(next_matmul_op);  // 转换为MatMulOp
      // 过滤掉包含HdimIsBatch属性的MatMul（此类MatMul不纳入MLP组）
      if (dyn_cast<tpu::MatMulOp>(start_op).getHdimIsBatch() ||
          mmOp.getHdimIsBatch()) {
        return false; // 包含HdimIsBatch的MatMul不参与组匹配
      }
      // 记录右操作数的切割维度配置（可能用于后续的维度切割优化）
      map_value_to_cut_dims[mmOp.getRight()] = {0, 3, 2, 1, 4};

      std::vector<Operation *> del_ops;  // 存储待删除的冗余操作
      // 遍历当前组内操作，处理激活函数（ActiveOp）相关的冗余Reshape
      for (auto op : ops) {
        if (isa<tpu::ActiveOp>(op)) {  // 若为激活操作（如ReLU、GELU等）
          auto next_op = *(op->getUsers().begin());  // 激活操作的下一个用户操作
          // 若下一个操作是Reshape且在当前组内
          if (isa<tpu::ReshapeOp>(next_op) &&
              std::find(ops.begin(), ops.end(), next_op) != ops.end()) {
            assert((op->getResult(0).hasOneUse()));  // 确保激活操作结果只有一个用户（即next_op）
            // 检查Reshape操作是否有组外用户（若没有则可删除）
            bool has_out_user = false;
            for (auto user : next_op->getUsers()) {
              if (find(ops.begin(), ops.end(), user) == ops.end()) {
                has_out_user = true;
                break;
              }
            }
            if (!has_out_user) {
              del_ops.push_back(next_op);  // 标记Reshape为待删除
            }
            // 调整激活操作结果的类型为Reshape输出类型（确保类型兼容）
            op->getResult(0).setType(next_op->getResult(0).getType());
            // 用激活操作的结果直接替换组内对Reshape结果的使用（跳过Reshape）
            next_op->getResult(0).replaceUsesWithIf(
                op->getResult(0), [&](OpOperand &operand) {
                  Operation *user = operand.getOwner();
                  return find(ops.begin(), ops.end(), user) != ops.end();
                });
          }

          // 处理激活操作输入的Reshape（若输入来自Reshape，检查是否可删除）
          auto inOp = op->getOperand(0).getDefiningOp();  // 激活操作的输入定义操作
          if (inOp && isa<tpu::ReshapeOp>(inOp)) {  // 若输入是Reshape操作
            // 检查Reshape是否有组外用户
            bool has_out_user = false;
            for (auto user : inOp->getUsers()) {
              if (find(ops.begin(), ops.end(), user) == ops.end()) {
                has_out_user = true;
                break;
              }
            }
            if (!has_out_user) {
              del_ops.push_back(inOp);  // 标记Reshape为待删除
            }
            // 保存Reshape输入的原始类型
            auto oldType = inOp->getOperand(0).getType();
            // 用Reshape的输入直接替换组内对其输出的使用（跳过Reshape）
            inOp->getResult(0).replaceUsesWithIf(
                inOp->getOperand(0), [&](OpOperand &operand) {
                  Operation *user = operand.getOwner();
                  return find(ops.begin(), ops.end(), user) != ops.end();
                });
            // 调整激活操作输入的类型为Reshape输入的原始类型
            op->getOperand(0).setType(oldType);
          }
        }
      }

      // 注释部分：处理MatMul后接Reshape的冗余情况（可能为未启用的备选逻辑）
      // 逻辑目的：移除MatMul与下一个操作之间的冗余Reshape
      // for (auto op: ops) {
      //   if (isa<tpu::MatMulOp>(op)) {
      //     auto next_op = *(op->getUsers().begin());
      //     if (isa<tpu::ReshapeOp>(next_op) && std::find(ops.begin(), ops.end(), next_op) != ops.end()) {
      //       // 类似激活函数的Reshape处理逻辑...
      //     }
      //   }
      // }

      // 执行冗余操作删除：从组操作列表中移除，并记录到待删除列表
      for (auto del_op : del_ops) {
        need_del_ops.push_back(del_op);  // 记录待删除操作（后续实际删除）
        ops.erase(std::remove(ops.begin(), ops.end(), del_op), ops.end());  // 从当前组操作中移除
      }

      // 检查Concat操作，判断是否影响列切割（col_cut）
      for (auto op : ops) {
        if (isa<tpu::ConcatOp>(op)) {  // 若为拼接操作
          auto in_shape = module::getShapeVec(op->getOperand(0));  // 获取输入形状
          // 若输入形状维度等于Concat轴+1，说明拼接可能影响列切割，禁用col_cut
          if (in_shape.size() == dyn_cast<tpu::ConcatOp>(op).getAxis() + 1) {
            col_cut = false;
          }
        }
      }

      // 组有效条件：操作数大于1（至少包含两个关键操作）且通过有效性检查
      return ops.size() > 1 && check_group_valid();
    }
    // 未找到两个MatMul操作，匹配失败
    return false;
  }

  // 返回组名称
  virtual std::string name() override { return "mlp_group"; }
  // 返回组的简要描述（Transformer块中的MLP）
  virtual std::string brief() override { return "mlp in transformer block"; }

  // 转换为其他类型的特殊层组
  // 参数：sub_ops-子操作列表；p_special_grp-目标特殊层组指针
  // 返回：是否转换成功
  virtual bool convert_to_other_type(
      std::vector<Operation *> &sub_ops,
      std::shared_ptr<speical_layer_group_base> &p_special_grp) override {
    // 若子操作组无效，返回失败
    if (!grp_is_valid(sub_ops)) {
      return false;
    }
    // 若子操作的第一个是MatMul，直接复用当前组存储子操作
    if (isa<tpu::MatMulOp>(sub_ops[0])) {
      ops.assign(sub_ops.begin(), sub_ops.end());
    } else {
      // 否则转换为单矩阵乘法组（single_matmul_group）
      p_special_grp = std::make_shared<single_matmul_group>();
      p_special_grp->ops.assign(sub_ops.begin(), sub_ops.end());
      p_special_grp->main_mm_op = sub_ops.back();  // 设最后一个操作为主MatMul
    }
    return true;
  }
};
```

### 3.attention_group

#### 功能分析

该类 `attention_group` 是深度学习框架中用于识别和优化 Transformer 模型中注意力机制（Attention）相关操作组的核心模块，主要功能包括：

1. 模式识别：识别以 “两个矩阵乘法（MatMul）+ Softmax” 为核心的注意力机制操作序列（注意力机制通常包含 QKV 投影、Scaled Dot-Product、Softmax、输出投影等步骤，涉及多个 MatMul 和一个 Softmax）。
2. 分片策略优化：针对硬件架构（如 CV18xx/BM168x）计算最佳的维度分片数量（n/c/h 维度），确保计算过程中张量能适配硬件本地内存（LMEM），避免内存溢出。
3. 周期平衡：通过调整 h 维度分片数，平衡数据加载（GDMA）、计算（BDC）和存储的周期，最大化流水线效率，提升硬件利用率。
4. 类型转换：支持将注意力组转换为单矩阵乘法组（`single_matmul_group`），适配不同场景的优化需求。

#### 逻辑流程

1. 模式匹配（`pattern_match_and_parser`）：

   - 首先检查是否存在两个矩阵乘法操作（`search_two_mmOp`），这是注意力机制的核心计算节点（如 Q×K、注意力权重 ×V）。
   - 验证是否包含 Softmax 操作（`find_softmax`），因为 Softmax 是注意力机制中归一化权重的关键步骤。
   - 若上述条件满足且组有效，返回匹配成功。
2. 分片计算（`CalcMatMulGroupTpNum`）：

   - 若主 MatMul 不包含 `HdimIsBatch` 属性，直接复用基类逻辑；否则针对 `HdimIsBatch` 场景单独处理。
   - 初始化 n/c/h 维度的分片数，根据硬件核心数确定初始值。
   - 遍历操作列表，区分 MatMul 和其他操作，动态调整分片数，确保张量在硬件本地内存（LMEM）中的总占用不超标。
   - 通过周期计算器（`CycleCalculator`）测试不同 h 分片数，找到加载、计算、存储周期差最小的方案，优化流水线效率。
   - 最终确定全局最佳分片数并更新到层组信息中。
3. 类型转换（`convert_to_other_type`）：

   - 检查子操作是否包含 Softmax 和 MatMul，若符合注意力特征则保持当前组；否则转换为单矩阵乘法组。

#### 原理说明

该类的设计基于 Transformer 注意力机制的固定结构特征和硬件加速的工程需求，核心原理包括：

1. 模式识别依据：注意力机制的计算流程是固定的（如 “Q×K → Scaled → Softmax → ×V”），必然包含至少两个 MatMul 和一个 Softmax，因此通过识别这一模式可准确定位注意力模块。
2. 硬件适配优化：

   - 硬件的本地内存（LMEM）容量有限，需将大张量按 n/c/h 维度分片，使每个分片的内存占用不超过 LMEM 限制。
   - 分片数需与硬件核心数匹配，最大化并行计算能力。
3. 周期平衡原理：

   - 注意力计算中，数据加载（GDMA）、计算（如 MatMul）、存储的耗时需尽可能平衡，避免某一步骤成为瓶颈。通过调整 h 维度分片数，使三者周期差最小，提升流水线利用率。
4. 灵活性设计：支持转换为单矩阵乘法组，适配注意力机制中部分子结构（如单独的 QKV 投影）的优化需求。

#### attention_group 代码

```cpp
// 注意力组类，继承自特殊层组基类（注：speical应为special的拼写错误）
class attention_group : public speical_layer_group_base {
public:
  // 克隆函数，创建当前类的共享指针副本（原型模式）
  virtual std::shared_ptr<speical_layer_group_base> clone() override {
    return std::make_shared<attention_group>();
  }

  // 模式匹配与解析函数：识别注意力机制相关的操作组
  // 参数：start_op-起始操作；subnet_ops-子网络操作列表
  // 返回：是否匹配成功
  virtual bool
  pattern_match_and_parser(Operation *start_op,
                           std::vector<Operation *> &subnet_ops) override {
    Operation *next_matmul_op = nullptr;  // 第二个矩阵乘法操作指针
    // 若启用调试命令，打印匹配过程中的子网络操作
    if (module::isDebugCmdEnable("print_mutmul_group_match_process")) {
      llvm::errs() << "start match attention_group in ops:\n";
      for (auto it : subnet_ops) {
        llvm::errs() << show_op_info(it) << "\n";
      }
    }
    // 查找起始操作相关的两个矩阵乘法操作（注意力机制通常包含多个MatMul）
    if (search_two_mmOp(start_op, next_matmul_op, subnet_ops)) {
      llvm::errs() << "find_softmax:" << find_softmax << '\n';  // 打印是否找到Softmax
      // 注意力机制必须包含Softmax，且组有效才匹配成功
      return find_softmax && check_group_valid();
    }
    return false;
  }

  // 返回组名称
  virtual std::string name() override { return "attention_group"; }

  // 计算矩阵乘法组的分片数量（用于硬件加速的并行优化）
  // 参数：lg_info-层组信息；failed_op-存储失败的操作；core_num-核心数量
  // 返回：是否计算成功
  virtual bool CalcMatMulGroupTpNum(ilp_LgInfo &lg_info, Operation *&failed_op,
                                    int64_t core_num) override {
    auto mm_op = dyn_cast<tpu::MatMulOp>(main_mm_op);  // 主矩阵乘法操作
    // 若主MatMul无HdimIsBatch属性，直接调用基类方法
    if (mm_op && !mm_op.getHdimIsBatch()) {
      return speical_layer_group_base::CalcMatMulGroupTpNum(lg_info, failed_op,
                                                            core_num);
    }
    hdim_is_batch = true;  // 标记Hdim作为批次维度

    int64_t cut_n, in_c, cut_h, in_w, out_c, out_w;
    group_type_t type = lg_info._lgInfo.type;  // 组类型
    // 获取批次大小
    lg_info.p_special_grp->get_batch_size(lg_info.shape_secs);
    int batch_size = lg_info.shape_secs.n;
    // 初始化最佳分片数量（n/c/h维度），根据核心数确定n的分片数
    int glo_n_slice_num = get_best_n_slice_num(batch_size, core_num),
        glo_c_slice_num = 1, glo_h_slice_num = 1;
    bool enable_cut_h = false;  // 是否启用h维度切割

    // 分离MatMul操作和其他操作（优先处理MatMul）
    std::vector<Operation *> tmp_ops, tmp_ops2;
    for (auto op : lg_info._lgInfo.group_ops) {
      if (isa<tpu::MatMulOp>(op)) {
        tmp_ops.push_back(op);  // MatMul操作单独处理
      } else {
        tmp_ops2.push_back(op);  // 其他操作后续处理
      }
    }
    // 若有两个MatMul，交换顺序（可能是为了按计算流程排序，如QKV->Softmax->输出）
    if (tmp_ops.size() == 2) {
      std::swap(tmp_ops[0], tmp_ops[1]);
    }
    // 合并操作列表（MatMul在前，其他在后）
    tmp_ops.insert(tmp_ops.end(), tmp_ops2.begin(), tmp_ops2.end());
    bool first_matmul = true;  // 标记是否为第一个MatMul

    // 遍历所有操作，计算每个操作的最佳分片数量
    for (auto op : tmp_ops) {
      auto ins = get_input_values(op);  // 输入张量
      auto outs = get_output_values(op);  // 输出张量
      // 尝试的分片数量（初始为全局分片数）
      int try_n_slice_num = glo_n_slice_num, try_c_slice_num = glo_c_slice_num,
          try_h_slice_num = glo_h_slice_num;
      // 记录有效的本地分片数
      int pre_valid_loc_slice_n = glo_n_slice_num,
          pre_valid_loc_slice_h = glo_h_slice_num,
          pre_valid_loc_slice_c = glo_c_slice_num;
      llvm::errs() << "CalcMatMulGroupTpNum for op:"
                   << module::getName(op).str() << '\n';

      // 计算当前分片数对应的"段数"（可能指并行处理的单元数）
      int secs = get_secs(op, try_n_slice_num, try_c_slice_num, try_h_slice_num);
      int old_target_secs = align(secs, core_num);  // 对齐核心数的目标段数
      bool init_secs_is_ok = (old_target_secs == secs);  // 初始段数是否合法

      // 循环调整分片数，确保内存使用不超过硬件限制
      do {
        // 获取输入张量形状（NCDHW格式）
        auto shape = ins[0].getType().cast<RankedTensorType>().getShape().vec();
        // 计算n和h维度的切割大小（根据分片数调整）
        if (pre_valid_loc_slice_n > shape[0]) {
          cut_n = 1;  // n维度无法按当前分片数切割，设为1
          cut_h = ceiling_func(shape[2], pre_valid_loc_slice_n / shape[0]);  // 调整h维度切割
        } else {
          cut_n = ceiling_func(shape[0], pre_valid_loc_slice_n);  // n维度按分片数切割
          cut_h = shape[2];  // h维度不切割
        }
        in_w = shape[3];  // 输入宽度

        // 计算输入/输出张量在本地内存（LMEM）中的字节数
        int64_t in0_lmem_bytes = 0, in1_lmem_bytes = 0, out0_lmem_bytes = 0;
        in_c = align(shape[1], try_c_slice_num) / try_c_slice_num;  // c维度切割后的大小
        // 输入张量0的LMEM占用（按切割后的维度计算）
        in0_lmem_bytes = align_64(
            Arch::get_tensor_lmem_bytes(ins[0], cut_n, in_c, 1, cut_h, in_w));

        // 输出张量形状
        shape = outs[0].getType().cast<RankedTensorType>().getShape().vec();
        out_c = align(shape[1], try_c_slice_num) / try_c_slice_num;  // 输出c维度切割后大小
        out_w = shape[3];  // 输出宽度
        // 若为最后一个MatMul，h维度按分片数切割
        if (isa<tpu::MatMulOp>(op) && op == ops.back()) {
          out_w = align(out_w, try_h_slice_num) / try_h_slice_num;
        }
        // 输出张量的LMEM占用
        out0_lmem_bytes = align_64(Arch::get_tensor_lmem_bytes(
            outs[0], cut_n, out_c, 1, cut_h, out_w));
        // 最后一个MatMul需要双倍输出内存（考虑前后时间片的存储和加载）
        if (isa<tpu::MatMulOp>(op) && try_h_slice_num > 1 && op == ops.back()) {
          out0_lmem_bytes *= 2;
        }

        // 计算操作所需的缓冲区大小
        auto lg_op = cast<LocalGenInterface>(op);
        int64_t buffer_size = lg_op.getBufferSize(
            in0_lmem_bytes, out0_lmem_bytes, cut_n, in_c, cut_h, 1, in_w, cut_n,
            out_c, cut_h, 1, out_w, type);

        // 处理第二个输入张量（若存在）
        if (ins.size() > 1) {
          shape = ins[1].getType().cast<RankedTensorType>().getShape().vec();
          // 最后一个MatMul的第二个输入（可能是Softmax输出）按h维度切割
          if (isa<tpu::MatMulOp>(op) && op == ops.back()) {
            auto mm_op = dyn_cast<tpu::MatMulOp>(op);
            if (mm_op.getRightTranspose()) {
              shape[1] = align(shape[1], try_h_slice_num) / try_h_slice_num;
            } else {
              shape[3] = align(shape[3], try_h_slice_num) / try_h_slice_num;
            }
          }
          // 输入张量1的LMEM占用
          in1_lmem_bytes = align_64(Arch::get_tensor_lmem_bytes(
              ins[1], cut_n, shape[1], 1, cut_h, shape[3]));
          // 检查MatMul的右矩阵是否过大（Softmax前的MatMul右矩阵不能超过LMEM）
          if (isa<tpu::MatMulOp>(op)) {
            if (op == ops.back()) {
              if (try_h_slice_num > 1) {
                in1_lmem_bytes *= 2;  // 最后一个MatMul的右矩阵需要双倍内存
              }
            } else {
              if (in1_lmem_bytes >= Arch::LMEM_BYTES) {
                llvm::errs() << "the right matrix of Matmul before softmax is "
                                "too large, op:"
                             << module::getName(op).str() << "\n";
                failed_op = op;
                return false;
              }
            }
          }
        }

        // 决定优先增加c维度分片（当输入0内存≥输入1时，优先切c）
        bool inc_c_slice = true;
        if (isa<tpu::MatMulOp>(op) && op == ops.back()) {
          inc_c_slice = in0_lmem_bytes >= in1_lmem_bytes;
        }

        // 总内存占用（输入+输出+缓冲区）
        int total = buffer_size + in0_lmem_bytes + in1_lmem_bytes + out0_lmem_bytes;
        bool mem_enough = (total <= Arch::LMEM_BYTES);  // 检查是否超过硬件LMEM限制
        llvm::errs() << "in0_lmem_bytes:" << in0_lmem_bytes
                     << ", out0_lmem_bytes:" << out0_lmem_bytes
                     << ", in1_lmem_bytes:" << in1_lmem_bytes
                     << ", buffer_size:" << buffer_size << ", total:" << total
                     << ", inc_c_slice:" << inc_c_slice
                     << ", old_target_secs:" << old_target_secs
                     << ", mem_enough:" << mem_enough << '\n';

        if (mem_enough) {
          // 内存足够时，若初始段数合法或非第一个MatMul，退出循环
          if (init_secs_is_ok || !first_matmul) {
            llvm::errs() << "init_secs_is_ok\n";
            break;
          }
          // 尝试增加分片数，调整段数
          if (!inc_slice_num(op, try_n_slice_num, try_c_slice_num,
                             try_h_slice_num, batch_size, lg_info.shape_secs.c,
                             enable_cut_h ? lg_info.shape_secs.h : 1,
                             old_target_secs, inc_c_slice)) {
            failed_op = op;
            return false;
          }
          // 重新计算段数
          secs = get_secs(op, try_n_slice_num, try_c_slice_num, try_h_slice_num);
          if (secs > old_target_secs) {
            llvm::errs() << "new secs(" << secs
                         << ") >= old_target_secs, break\n";
            break;
          }
        } else {
          // 内存不足时，必须增加分片数
          if (!inc_slice_num(op, try_n_slice_num, try_c_slice_num,
                             try_h_slice_num, batch_size, lg_info.shape_secs.c,
                             enable_cut_h ? lg_info.shape_secs.h : 1,
                             old_target_secs, inc_c_slice)) {
            failed_op = op;
            return false;
          }
          // 重新计算段数和目标段数
          secs = get_secs(op, try_n_slice_num, try_c_slice_num, try_h_slice_num);
          old_target_secs = align(secs, core_num);
          init_secs_is_ok = (old_target_secs == secs);
        }
        // 更新有效的本地分片数
        pre_valid_loc_slice_n = try_n_slice_num;
        pre_valid_loc_slice_c = try_c_slice_num;
        pre_valid_loc_slice_h = try_h_slice_num;
      } while (true);  // 直到内存足够且段数合法

      // 更新全局分片数（取最大的本地分片数）
      if (pre_valid_loc_slice_c > glo_c_slice_num) {
        glo_c_slice_num = pre_valid_loc_slice_c;
      }
      if (pre_valid_loc_slice_h > glo_h_slice_num) {
        glo_h_slice_num = pre_valid_loc_slice_h;
      }
      if (pre_valid_loc_slice_n > glo_n_slice_num) {
        glo_n_slice_num = pre_valid_loc_slice_n;
      }
      llvm::errs() << "pre_valid_loc_slice_n:" << pre_valid_loc_slice_n
                   << ", glo_n_slice_num:" << glo_n_slice_num
                   << ", pre_valid_loc_slice_c:" << pre_valid_loc_slice_c
                   << ", glo_c_slice_num:" << glo_c_slice_num
                   << ", pre_valid_loc_slice_h:" << pre_valid_loc_slice_h
                   << ", glo_h_slice_num:" << glo_h_slice_num << '\n';
      first_matmul = false;  // 第一个MatMul处理完毕
    }

    // 填充分片信息到层组信息中
    llvm::errs() << "fill_slice_info start\n";
    lg_info.shape_secs.n_slice_num = glo_n_slice_num;
    lg_info.shape_secs.c_slice_num = glo_c_slice_num;
    lg_info.shape_secs.h_slice_num = glo_h_slice_num;
    fill_slice_info(lg_info);
    if (!enable_cut_h) {
      return true;
    }

    // 计算最佳h维度分片数（平衡加载、存储和计算周期）
    std::shared_ptr<CycleCalculator> cycle_calculator_;
    // 根据硬件类型（CV18xx/BM168x）创建周期计算器
    if (module::isCV18xx()) {
      Cv18xxCycleCalculator *cyc_ptr = new Cv18xxCycleCalculator();
      cycle_calculator_ = std::shared_ptr<CycleCalculator>(cyc_ptr);
    } else {
      Bm168xCycleCalculator *cyc_ptr = new Bm168xCycleCalculator();
      cycle_calculator_ = std::shared_ptr<CycleCalculator>(cyc_ptr);
    }

    tensor_info_t info;
    std::vector<std::pair<int, int>> vec_hslice_and_diff_cycle;  // h分片数与周期差的映射
    int old_diff = -1, inc_time = 0;
    // 循环测试不同h分片数，找到周期差最小的方案（优化流水线效率）
    do {
      auto in = tmp_ops[0]->getOperand(1);  // 第一个MatMul的输入1（可能是K）
      if (lg_info.tensor_infos.find(in) != lg_info.tensor_infos.end()) {
        info = lg_info.tensor_infos[in];
      }
      info.mode2 = TIMESTEP2_LOAD;  // 加载模式
      int load_cycle = cycle_calculator_->getGdmaCycle(in, info, lg_info._lgInfo.type);  // 加载周期

      auto res = tmp_ops[0]->getResult(0);  // 第一个MatMul的输出（Q*K）
      if (lg_info.tensor_infos.find(res) != lg_info.tensor_infos.end()) {
        info = lg_info.tensor_infos[res];
      }
      info.mode2 = TIMESTEP2_STORE;  // 存储模式
      int store_cycle = cycle_calculator_->getGdmaCycle(res, info, lg_info._lgInfo.type);  // 存储周期

      // 计算操作的本地计算周期
      int bdc_cycle = cycle_calculator_->getLocalLayerCycle(
          tmp_ops[0], lg_info.tensor_infos, lg_info._lgInfo.type, true);
      // 周期差（计算周期 - 加载+存储周期）
      int diff = std::abs(bdc_cycle - store_cycle - load_cycle);
      llvm::errs() << "h_slice_num:" << lg_info.shape_secs.h_slice_num
                   << ", load_cycle:" << load_cycle
                   << ", store_cycle:" << store_cycle
                   << ", bdc_cycle:" << bdc_cycle << ", diff:" << diff << '\n';

      // 跟踪周期差变化，连续5次变差则停止
      if (diff < old_diff && old_diff != -1) {
        inc_time = 0;  // 周期差改善，重置计数
      } else {
        inc_time++;  // 周期差变差，计数+1
        if (inc_time > 5) {
          llvm::errs() << "nc_time > 5, break\n";
          break;
        }
      }
      // 记录当前h分片数和周期差
      vec_hslice_and_diff_cycle.emplace_back(lg_info.shape_secs.h_slice_num, diff);
      old_diff = diff;
      lg_info.shape_secs.h_slice_num++;  // 增加h分片数
      fill_slice_info(lg_info);  // 更新分片信息
    } while (true);

    // 找到周期差最小的h分片数
    std::sort(vec_hslice_and_diff_cycle.begin(),
              vec_hslice_and_diff_cycle.end(), pair_int_Sort_by_int);
    lg_info.shape_secs.h_slice_num = vec_hslice_and_diff_cycle[0].first;
    fill_slice_info(lg_info);
    llvm::errs() << "find best h_slice_num:" << lg_info.shape_secs.h_slice_num
                 << ", diff:" << vec_hslice_and_diff_cycle[0].second << '\n';
    // 打印最终的分片信息
    llvm::errs() << "n:" << lg_info.shape_secs.n
                 << ", c:" << lg_info.shape_secs.c
                 << ", h:" << lg_info.shape_secs.h
                 << ", n_slice_num:" << lg_info.shape_secs.n_slice_num
                 << ", glo_c_slice_num:" << glo_c_slice_num
                 << ", glo_h_slice_num:" << glo_h_slice_num << '\n';
    return true;
  }

  // 转换为其他类型的特殊层组
  // 参数：sub_ops-子操作列表；p_special_grp-目标特殊层组指针
  // 返回：是否转换成功
  virtual bool convert_to_other_type(
      std::vector<Operation *> &sub_ops,
      std::shared_ptr<speical_layer_group_base> &p_special_grp) override {
    if (!grp_is_valid(sub_ops)) {  // 子操作组无效则返回失败
      return false;
    }
    bool have_softmax = false;  // 是否包含Softmax
    Operation *mm_op = nullptr;  // 记录MatMul操作
    for (auto op : sub_ops) {
      if (op && isa<tpu::SoftmaxOp>(op)) {
        have_softmax = true;  // 检测到Softmax
      }
      if (op && isa<tpu::MatMulOp>(op)) {
        mm_op = op;  // 记录MatMul
      }
    }

    // 若最后一个操作是MatMul且包含Softmax，保持为当前组
    if (sub_ops.back() && isa<tpu::MatMulOp>(sub_ops.back())) {
      if (have_softmax) {
        ops.assign(sub_ops.begin(), sub_ops.end());
        p_special_grp->main_mm_op = mm_op;
        return true;
      }
    } else {
      // 否则转换为单矩阵乘法组
      if (sub_ops.front() && isa<tpu::MatMulOp>(sub_ops.front())) {
        p_special_grp = std::make_shared<single_matmul_group>();
        p_special_grp->ops.assign(sub_ops.begin(), sub_ops.end());
        p_special_grp->main_mm_op = mm_op;
        return true;
      }
    }
    return false;
  }

  // 返回组的简要描述（注：此处描述有误，应为"attention in transformer block"）
  virtual std::string brief() override { return "mlp in transformer block"; }
};
```

### 4.CalcMatMulGroupTpNum

#### 核心功能

该函数是 TPU 深度学习框架中矩阵乘法（MatMul）算子组的优化模块，主要功能是计算矩阵乘法算子组在 n（batch）、c（通道）、h（高度）三个维度上的最优切片（分片）数量，确保：

1. 每个切片的内存占用不超过 TPU 的本地内存（LMEM）限制；
2. 切片数量适配 TPU 的核心数量（任务能均匀分配到各核心）；
3. 数据加载 / 存储周期与计算周期匹配（减少硬件等待，提高效率）。

#### 逻辑流程

整体逻辑可分为 4 个关键步骤：

1. 初始化与特殊处理

   - 首先判断是否为特殊 MatMulOp（Hdim 非 batch 维度），若是则调用基类方法；
   - 否则标记 Hdim 为 batch 维度，初始化全局切片参数（n 维度取最优初始值，c 和 h 初始为 1）。
2. 算子分离与排序

   - 将算子组中的矩阵乘法算子（MatMulOp）与其他算子分离，确保 MatMulOp 优先处理；
   - 若存在 2 个 MatMulOp，交换顺序（可能为了优先处理输入 / 输出更关键的算子）。
3. 切片数量调整（核心逻辑）
   对每个算子循环调整 n/c/h 切片数量，直到满足内存限制：

   - 计算当前切片下的输入 / 输出张量内存占用（LMEM）和缓冲区大小；
   - 若总内存超过 LMEM 限制，增加切片数量（减小单切片大小）；
   - 若内存足够，检查分片数是否与核心数量对齐（确保任务均匀分配）；
   - 记录每个算子的最大切片需求，更新全局切片参数（取所有算子的最大值）。
4. h 维度切片优化（周期匹配）

   - 初始化 TPU 架构对应的周期计算器（CV18xx/BM168x）；
   - 尝试不同 h 切片数，计算数据加载周期、存储周期与计算周期的差异；
   - 选择差异最小的 h 切片数（减少硬件等待时间），更新分组信息。

#### 核心原理

1. 数据分片（Sharding）
   为适配 TPU 的并行计算能力，将大矩阵按 n/c/h 维度分解为小切片，每个切片分配到一个核心计算。切片数量需与核心数量匹配（通过 `align` 函数确保分片数为核心数的整数倍），避免负载不均。
2. 内存限制适配
   TPU 的本地内存（LMEM）容量有限（如几百 KB），需通过切片将单算子内存占用控制在 LMEM 内。代码中通过 `get_tensor_lmem_bytes` 计算内存，并通过 `align_64` 确保内存对齐（TPU 内存访问要求 64 字节对齐以提高效率）。
3. 周期匹配优化
   TPU 的计算单元（如 BDC）与数据传输单元（GDMA）是并行的，若计算周期与数据传输周期不匹配，会导致硬件空闲。代码通过调整 h 切片数，最小化 “计算周期 - 加载 / 存储周期” 的差异，实现 “计算与传输重叠”，提升利用率。

#### 关键细节

- 内存翻倍处理：最后一个 MatMulOp 且 h 切片数 > 1 时，输入 / 输出内存需翻倍，因需同时存储前一时间片的结果和加载下一时间片的数据（流水线处理）；
- 优先切片策略：当输入内存较大时优先切 c 维度（通道），平衡内存占用；
- 容错机制：若某算子内存始终超限（如右侧矩阵过大），返回失败并记录该算子。

#### 5.CalcMatMulGroupTpNum 代码

```cpp
/// 计算矩阵乘法算子组的TPU切片数量，确保适配核心数量和内存限制
/// @param lg_info 分组信息结构体，包含形状、算子组等信息
/// @param failed_op 输出参数，存储计算失败的算子（若有）
/// @param core_num TPU核心数量
/// @return 计算成功返回true，失败返回false
virtual bool CalcMatMulGroupTpNum(ilp_LgInfo &lg_info, Operation *&failed_op,
                                    int64_t core_num) override {
    // 将主矩阵乘法算子转换为tpu::MatMulOp类型
    auto mm_op = dyn_cast<tpu::MatMulOp>(main_mm_op);
    // 若为MatMulOp且H维度不是batch维度，调用基类的计算方法
    if (mm_op && !mm_op.getHdimIsBatch()) {
      return speical_layer_group_base::CalcMatMulGroupTpNum(lg_info, failed_op,
                                                            core_num);
    }
    // 标记H维度为batch维度
    hdim_is_batch = true;

    // 定义切片相关变量：n/c/h维度切片大小、输入/输出通道/宽度
    int64_t cut_n, in_c, cut_h, in_w, out_c, out_w;
    // 获取分组类型
    group_type_t type = lg_info._lgInfo.type;
    // 从形状信息中获取batch大小
    lg_info.p_special_grp->get_batch_size(lg_info.shape_secs);
    int batch_size = lg_info.shape_secs.n;
    // 计算初始的全局切片数量：n维度最优切片数，c和h初始为1
    int glo_n_slice_num = get_best_n_slice_num(batch_size, core_num),
        glo_c_slice_num = 1, glo_h_slice_num = 1;
    // 是否启用h维度切片（初始禁用）
    bool enable_cut_h = false;

    // 分离算子组中的MatMulOp和其他算子
    std::vector<Operation *> tmp_ops, tmp_ops2;
    for (auto op : lg_info._lgInfo.group_ops) {
      if (isa<tpu::MatMulOp>(op)) {
        tmp_ops.push_back(op); // 存储矩阵乘法算子
      } else {
        tmp_ops2.push_back(op); // 存储其他算子
      }
    }
    // 若有2个MatMulOp，交换顺序（可能为了优先处理特定算子）
    if (tmp_ops.size() == 2) {
      std::swap(tmp_ops[0], tmp_ops[1]);
    }
    // 合并算子列表：MatMulOp在前，其他算子在后
    tmp_ops.insert(tmp_ops.end(), tmp_ops2.begin(), tmp_ops2.end());
    // 标记是否为第一个MatMulOp（用于特殊处理）
    bool first_matmul = true;
    // 遍历所有算子，计算每个算子的切片参数
    for (auto op : tmp_ops) {
      // 获取算子的输入和输出张量
      auto ins = get_input_values(op);
      auto outs = get_output_values(op);
      // 初始化当前算子的尝试切片数量（基于全局初始值）
      int try_n_slice_num = glo_n_slice_num, try_c_slice_num = glo_c_slice_num,
          try_h_slice_num = glo_h_slice_num;
      // 记录有效的本地切片数量（用于后续调整）
      int pre_valid_loc_slice_n = glo_n_slice_num,
          pre_valid_loc_slice_h = glo_h_slice_num,
          pre_valid_loc_slice_c = glo_c_slice_num;
      // 打印当前处理的算子名称（调试用）
      llvm::errs() << "CalcMatMulGroupTpNum for op:"
                   << module::getName(op).str() << '\n';
      // 计算当前切片数量下的任务分片数
      int secs =
          get_secs(op, try_n_slice_num, try_c_slice_num, try_h_slice_num);
      // 计算对齐到核心数量的目标分片数（确保能被核心数整除）
      int old_target_secs = align(secs, core_num);
      // 初始分片数是否已对齐（无需调整）
      bool init_secs_is_ok = old_target_secs == secs;

      // 循环调整切片数量，直到满足内存和核心数量要求
      do {
        // 获取第一个输入张量的形状（假设为4D：n, c, h, w）
        auto shape = ins[0].getType().cast<RankedTensorType>().getShape().vec();
        ;
        // 根据n维度有效切片数和输入n大小调整cut_n（n维度切片大小）和cut_h（h维度切片大小）
        if (pre_valid_loc_slice_n > shape[0]) {
          cut_n = 1; // n维度切片数超过输入n，n切片大小设为1
          cut_h = ceiling_func(shape[2], pre_valid_loc_slice_n / shape[0]); // h维度切片大小向上取整
        } else {
          cut_n = ceiling_func(shape[0], pre_valid_loc_slice_n); // n切片大小向上取整
          cut_h = shape[2]; // h维度不切片
        }
        in_w = shape[3]; // 输入宽度（w维度）

        // 计算输入、输出和缓冲区的本地内存占用（LMEM，TPU本地内存）
        int64_t in0_lmem_bytes = 0, in1_lmem_bytes = 0, out0_lmem_bytes = 0;
        // c维度切片大小（输入通道方向）
        in_c = align(shape[1], try_c_slice_num) / try_c_slice_num;
        // 计算第一个输入张量的LMEM占用（64字节对齐）
        in0_lmem_bytes = align_64(
            Arch::get_tensor_lmem_bytes(ins[0], cut_n, in_c, 1, cut_h, in_w));

        // 获取输出张量的形状
        shape = outs[0].getType().cast<RankedTensorType>().getShape().vec();
        ;
        // c维度切片大小（输出通道方向）
        out_c = align(shape[1], try_c_slice_num) / try_c_slice_num;
        out_w = shape[3]; // 输出宽度（w维度）
        // 若为最后一个MatMulOp，调整输出w维度为h切片大小
        if (isa<tpu::MatMulOp>(op) && op == ops.back()) {
          out_w = align(out_w, try_h_slice_num) / try_h_slice_num;
        }
        // 计算输出张量的LMEM占用（64字节对齐）
        out0_lmem_bytes = align_64(Arch::get_tensor_lmem_bytes(
            outs[0], cut_n, out_c, 1, cut_h, out_w));
        // 若为最后一个MatMulOp且h维度切片数>1，输出内存需翻倍（考虑前后时间片的存储和加载）
        if (isa<tpu::MatMulOp>(op) && try_h_slice_num > 1 && op == ops.back()) {
          out0_lmem_bytes *= 2;
        }

        // 计算算子所需的缓冲区大小
        auto lg_op = cast<LocalGenInterface>(op);
        int64_t buffer_size = lg_op.getBufferSize(
            in0_lmem_bytes, out0_lmem_bytes, cut_n, in_c, cut_h, 1, in_w, cut_n,
            out_c, cut_h, 1, out_w, type);
        
        // 处理第二个输入张量（若存在）
        if (ins.size() > 1) {
          shape = ins[1].getType().cast<RankedTensorType>().getShape().vec();
          ;
          // 若为最后一个MatMulOp，根据转置情况调整输入形状
          if (isa<tpu::MatMulOp>(op) && op == ops.back()) {
            auto mm_op = dyn_cast<tpu::MatMulOp>(op);
            if (mm_op.getRightTranspose()) {
              shape[1] = align(shape[1], try_h_slice_num) / try_h_slice_num;
            } else {
              shape[3] = align(shape[3], try_h_slice_num) / try_h_slice_num;
            }
          }
          // 计算第二个输入张量的LMEM占用（64字节对齐）
          in1_lmem_bytes = align_64(Arch::get_tensor_lmem_bytes(
              ins[1], cut_n, shape[1], 1, cut_h, shape[3]));
          // 若为MatMulOp，检查内存是否超限
          if (isa<tpu::MatMulOp>(op)) {
            if (op == ops.back()) {
              // 最后一个MatMulOp且h切片数>1，输入内存翻倍（同上）
              if (try_h_slice_num > 1) {
                in1_lmem_bytes *= 2;
              }
            } else {
              // 非最后一个MatMulOp，若右侧矩阵内存超限则失败
              if (in1_lmem_bytes >= Arch::LMEM_BYTES) {
                llvm::errs() << "the right matrix of Matmul before softmax is "
                                "too large, op:"
                             << module::getName(op).str() << "\n";
                failed_op = op;
                return false;
              }
            }
          }
        }

        // 决定优先增加c维度切片还是h维度切片（基于输入内存大小）
        bool inc_c_slice = true;
        if (isa<tpu::MatMulOp>(op) && op == ops.back()) {
          inc_c_slice = in0_lmem_bytes >= in1_lmem_bytes; // 相等时优先切c
        }

        // 计算总内存占用（输入+输出+缓冲区）
        int total =
            buffer_size + in0_lmem_bytes + in1_lmem_bytes + out0_lmem_bytes;
        // 检查总内存是否在LMEM限制内
        bool mem_enough = total <= Arch::LMEM_BYTES;
        // 打印内存占用信息（调试用）
        llvm::errs() << "in0_lmem_bytes:" << in0_lmem_bytes
                     << ", out0_lmem_bytes:" << out0_lmem_bytes
                     << ", in1_lmem_bytes:" << in1_lmem_bytes
                     << ", buffer_size:" << buffer_size << ", total:" << total
                     << ", inc_c_slice:" << inc_c_slice
                     << ", old_target_secs:" << old_target_secs
                     << ", mem_enough:" << mem_enough << '\n';

        // 若内存足够，检查是否需要继续调整
        if (mem_enough) {
          // 初始分片已对齐，或非第一个MatMulOp，停止调整
          if (init_secs_is_ok || !first_matmul) { 
            llvm::errs() << "init_secs_is_ok\n";
            break;
          }
          // 否则尝试增加切片数量（调整分片数）
          if (!inc_slice_num(op, try_n_slice_num, try_c_slice_num,
                             try_h_slice_num, batch_size, lg_info.shape_secs.c,
                             enable_cut_h ? lg_info.shape_secs.h : 1,
                             old_target_secs, inc_c_slice)) {
            failed_op = op;
            return false;
          }
          // 重新计算分片数并检查是否超过目标
          secs =
              get_secs(op, try_n_slice_num, try_c_slice_num, try_h_slice_num);
          if (secs > old_target_secs) {
            llvm::errs() << "new secs(" << secs
                         << ") >= old_target_secs, break\n";
            break;
          }
        } else {
          // 内存不足，必须增加切片数量（减小每个切片的内存占用）
          if (!inc_slice_num(op, try_n_slice_num, try_c_slice_num,
                             try_h_slice_num, batch_size, lg_info.shape_secs.c,
                             enable_cut_h ? lg_info.shape_secs.h : 1,
                             old_target_secs, inc_c_slice)) {
            failed_op = op;
            return false;
          }
          // 重新计算分片数并对齐核心数量
          secs =
              get_secs(op, try_n_slice_num, try_c_slice_num, try_h_slice_num);
          old_target_secs = align(secs, core_num);
          init_secs_is_ok = old_target_secs == secs;
        }
        // 更新有效的本地切片数量
        pre_valid_loc_slice_n = try_n_slice_num;
        pre_valid_loc_slice_c = try_c_slice_num;
        pre_valid_loc_slice_h = try_h_slice_num;
      } while (true); // 无限循环，直到内存满足条件

      // 更新全局切片数量（取所有算子的最大需求）
      if (pre_valid_loc_slice_c > glo_c_slice_num) {
        glo_c_slice_num = pre_valid_loc_slice_c;
      }
      if (pre_valid_loc_slice_h > glo_h_slice_num) {
        glo_h_slice_num = pre_valid_loc_slice_h;
      }
      if (pre_valid_loc_slice_n > glo_n_slice_num) {
        glo_n_slice_num = pre_valid_loc_slice_n;
      }
      // 打印全局切片数量更新信息（调试用）
      llvm::errs() << "pre_valid_loc_slice_n:" << pre_valid_loc_slice_n
                   << ", glo_n_slice_num:" << glo_n_slice_num
                   << ", pre_valid_loc_slice_c:" << pre_valid_loc_slice_c
                   << ", glo_c_slice_num:" << glo_c_slice_num
                   << ", pre_valid_loc_slice_h:" << pre_valid_loc_slice_h
                   << ", glo_h_slice_num:" << glo_h_slice_num << '\n';
      // 标记第一个MatMulOp处理完成
      first_matmul = false;
    }

    // 填充切片信息到分组信息中
    llvm::errs() << "fill_slice_info start\n";
    lg_info.shape_secs.n_slice_num = glo_n_slice_num;
    lg_info.shape_secs.c_slice_num = glo_c_slice_num;
    lg_info.shape_secs.h_slice_num = glo_h_slice_num;
    fill_slice_info(lg_info);

    // 若不启用h维度切片优化，直接返回成功
    if (!enable_cut_h) {
      return true;
    }

    // 初始化周期计算器（根据TPU架构选择CV18xx或BM168x）
    std::shared_ptr<CycleCalculator> cycle_calculator_;
    if (module::isCV18xx()) {
      Cv18xxCycleCalculator *cyc_ptr = new Cv18xxCycleCalculator();
      cycle_calculator_ = std::shared_ptr<CycleCalculator>(cyc_ptr);
    } else {
      Bm168xCycleCalculator *cyc_ptr = new Bm168xCycleCalculator();
      cycle_calculator_ = std::shared_ptr<CycleCalculator>(cyc_ptr);
    }

    // 计算最优h维度切片数（通过周期匹配优化）
    tensor_info_t info;
    std::vector<std::pair<int, int>> vec_hslice_and_diff_cycle; // 存储h切片数和周期差异
    int old_diff = -1, inc_time = 0; // 周期差异记录和连续增加次数
    do {
      // 获取第一个MatMulOp的第二个输入张量
      auto in = tmp_ops[0]->getOperand(1);
      if (lg_info.tensor_infos.find(in) != lg_info.tensor_infos.end()) {
        info = lg_info.tensor_infos[in];
      }
      info.mode2 = TIMESTEP2_LOAD; // 加载模式
      // 计算数据加载周期
      int load_cycle =
          cycle_calculator_->getGdmaCycle(in, info, lg_info._lgInfo.type);
      
      // 获取第一个MatMulOp的输出张量
      auto res = tmp_ops[0]->getResult(0);
      if (lg_info.tensor_infos.find(res) != lg_info.tensor_infos.end()) {
        info = lg_info.tensor_infos[res];
      }
      info.mode2 = TIMESTEP2_STORE; // 存储模式
      // 计算数据存储周期
      int store_cycle =
          cycle_calculator_->getGdmaCycle(res, info, lg_info._lgInfo.type);
      
      // 计算计算周期（BDC单元周期）
      int bdc_cycle = cycle_calculator_->getLocalLayerCycle(
          tmp_ops[0], lg_info.tensor_infos, lg_info._lgInfo.type, true);
      
      // 计算周期差异（计算周期与数据传输周期的差的绝对值）
      int diff = std::abs(bdc_cycle - store_cycle - load_cycle);
      // 打印周期信息（调试用）
      llvm::errs() << "h_slice_num:" << lg_info.shape_secs.h_slice_num
                   << ", load_cycle:" << load_cycle
                   << ", store_cycle:" << store_cycle
                   << ", bdc_cycle:" << bdc_cycle << ", diff:" << diff << '\n';
      
      // 检查周期差异是否优化，若连续5次未优化则停止
      if (diff < old_diff && old_diff != -1) {
        inc_time = 0; // 差异减小，重置连续计数
      } else {
        inc_time++; // 差异未减小，增加计数
        if (inc_time > 5) {
          llvm::errs() << "nc_time > 5, break\n";
          break;
        }
      }
      // 记录当前h切片数和差异
      auto hslice_diff = std::make_pair(lg_info.shape_secs.h_slice_num, diff);
      vec_hslice_and_diff_cycle.push_back(hslice_diff);
      old_diff = diff;
      // 尝试增加h切片数，重新计算
      lg_info.shape_secs.h_slice_num++;
      fill_slice_info(lg_info);
    } while (true); // 无限循环，直到连续5次未优化

    // 排序找到周期差异最小的h切片数
    std::sort(vec_hslice_and_diff_cycle.begin(),
              vec_hslice_and_diff_cycle.end(), pair_int_Sort_by_int);
    lg_info.shape_secs.h_slice_num = vec_hslice_and_diff_cycle[0].first;
    fill_slice_info(lg_info);
    // 打印最优h切片数（调试用）
    llvm::errs() << "find best h_slice_num:" << lg_info.shape_secs.h_slice_num
                 << ", diff:" << vec_hslice_and_diff_cycle[0].second << '\n';
    llvm::errs() << "n:" << lg_info.shape_secs.n
                 << ", c:" << lg_info.shape_secs.c
                 << ", h:" << lg_info.shape_secs.h
                 << ", n_slice_num:" << lg_info.shape_secs.n_slice_num
                 << ", glo_c_slice_num:" << glo_c_slice_num
                 << ", glo_h_slice_num:" << glo_h_slice_num << '\n';
    return true;
  }
```

### 5.speical_layer_group_base::fill_slice_info

#### 功能分析

该方法是特殊层组基类（`speical_layer_group_base`）的核心方法，主要功能是为层组内所有操作的输入 / 输出张量计算并填充分片信息，用于深度学习模型在硬件加速时的并行计算和内存管理。具体包括：

1. 初始化分片环境：清空现有张量信息，准备存储新的分片结果。
2. 分场景处理分片：根据 `hdim_is_batch`（h 维度是否作为批次）分为两种逻辑：

   - 当 `hdim_is_batch` 为 `true`（仅适用于 `attention_group`）：特殊处理 h 维度作为批次的场景，动态分配 n 和 h 的分片数。
   - 当 `hdim_is_batch` 为 `false`（适用于 `mlp_group`、`single_matmul_group` 等）：根据组类型和操作位置（如第一个 / 最后一个操作），为不同维度（n/c/h）分配分片数。
3. 特殊处理右矩阵：矩阵乘法的右矩阵（通常为权重或特定输入）需单独设置分片策略和 L2 内存加载策略。
4. 存储分片信息：将计算好的分片信息（n/c/d/h/w 维度的分片方式）存入层组信息结构（`ilp_LgInfo`），供后续硬件计算使用。

#### 逻辑流程

1. 初始化与参数获取：清空历史数据，获取 n/c/h 维度的分片数（从 `ilp_lg_info` 中）。
2. 分支 1（`hdim_is_batch = true`）：

   - 仅适用于 `attention_group`，断言确保这一点。
   - 根据主矩阵乘法输入的 n 维度大小，动态调整 n 和 h 的分片数（若 n 分片数超过 n 维度大小，将多余分片分摊到 h 维度）。
   - 遍历所有操作，为输入 / 输出张量计算分片：
     - 输入张量：n 按 n 分片，h 按 h 分片，右矩阵特殊处理 w 维度分片。
     - 输出张量：n 按 n 分片，c 按 c 分片，h 按 h 分片，最后一个操作特殊处理 w 维度。
3. 分支 2（`hdim_is_batch = false`）：

   - 遍历所有操作，为输入 / 输出张量计算分片：
     - 输入张量：默认 n 按 n 分片，非右矩阵的 c 按 c 分片；根据组类型和操作位置，调整右矩阵和非右矩阵的 h/c 维度分片。
     - 输出张量：默认 n 按 n 分片，c 按 c 分片；根据组类型和操作位置（如最后一个操作），调整 h 维度分片，并标记 L2 存储策略。
4. 存储结果：所有张量的分片信息存入 `ilp_lg_info.tensor_infos`，供硬件加速使用。

#### 原理说明

该方法的设计基于硬件加速中张量分片的核心需求，原理可总结为：

1. 分片的必要性：深度学习模型的张量通常较大，无法直接放入硬件的本地内存（如 LMEM），需按维度（n/c/h 等）分片，使每个分片的大小适配硬件内存，同时支持并行计算（多核心同时处理不同分片）。
2. 分场景策略：

   - `hdim_is_batch` 场景（注意力机制特有）：注意力计算中 h 维度可能承担批次角色，需动态平衡 n 和 h 的分片，避免某一维度分片过度。
   - 普通场景：不同层组（MLP、单 MatMul、注意力）的计算模式不同（如矩阵乘法的输入输出维度关系），因此对 n/c/h 的分片策略不同（例如 MLP 的第一个 MatMul 右矩阵切 h，其他切 c）。
3. 右矩阵特殊处理：矩阵乘法的右矩阵（如 Transformer 中的 K/V 矩阵）通常是权重或需重复使用的张量，通过特殊的分片和 L2 内存加载策略（`value_load_to_l2m`），减少重复加载开销，提升效率。
4. 硬件适配：分片信息中的 `eu_align = true` 确保分片大小符合硬件计算单元（EU）的对齐要求，避免计算效率损失。

#### 4.fill_slice_info 代码

```cpp
// 特殊层组基类的方法：为层组信息填充张量分片信息（用于硬件加速的并行计算）
// 参数：ilp_lg_info - 层组信息结构，存储分片后的张量信息
void speical_layer_group_base::fill_slice_info(ilp_LgInfo &ilp_lg_info) {
  int64_t n, c, d, h, w;  // 张量的NCDHW维度（N-批次，C-通道，D-深度，H-高度，W-宽度）
  // 清空现有张量信息和L2内存加载/存储映射
  ilp_lg_info.tensor_infos.clear();
  ilp_lg_info.value_store_to_l2m.clear();
  ilp_lg_info.value_load_to_l2m.clear();
  // 获取n维度的分片数（从层组形状信息中）
  int64_t n_slice_num = ilp_lg_info.shape_secs.n_slice_num;
  // 打印分片数日志（调试用）
  llvm::errs() << "n_slice_num: " << n_slice_num
               << ", c_slice_num: " << ilp_lg_info.shape_secs.c_slice_num
               << ", h_slice_num: " << ilp_lg_info.shape_secs.h_slice_num
               << "\n";

  // 分支1：当h维度作为批次（hdim_is_batch为true）时的分片逻辑（仅适用于attention_group）
  if (hdim_is_batch) {
    assert(name() == "attention_group");  // 确保只有注意力组会进入此分支
    int64_t n_slice, h_slice;  // n和h维度的实际分片大小
    // 获取主矩阵乘法操作第一个输入的形状（NCDHW）
    auto shape = main_mm_op->getOperand(0)
                     .getType()
                     .cast<RankedTensorType>()
                     .getShape()
                     .vec();
    // 计算n和h的分片：若n分片数超过n维度大小，则将多余分片分摊到h维度
    if (n_slice_num > shape[0]) {
      n_slice = shape[0];  // n维度按实际大小分片（不超过自身）
      h_slice = n_slice_num / shape[0];  // 剩余分片数分配给h维度
    } else {
      n_slice = n_slice_num;  // n维度按分片数直接分片
      h_slice = 1;  // h维度不分片
    }
    ilp_lg_info.shape_secs.shape_0 = shape[0];  // 记录原始n维度大小

    // 遍历层组内所有操作，为输入和输出张量计算分片信息
    for (auto op : ilp_lg_info._lgInfo.group_ops) {
      // 处理操作的输入张量
      for (auto in : get_input_values(op)) {
        shape = in.getType().cast<RankedTensorType>().getShape().vec();  // 输入张量形状
        slice_info_t si;  // 分片信息结构
        // 计算n维度分片（按n_slice）
        slice_distributor(si.n, shape[0], n_slice);
        // 计算h维度分片（按h_slice）
        slice_distributor(si.h, shape[2], h_slice);
        // d维度不分片（固定为1）
        slice_distributor(si.d, 1, 1);

        // 若输入是矩阵乘法的右矩阵（通常为权重或K/V矩阵）
        if (module::IsRightMat(in)) {
          ilp_lg_info.value_load_to_l2m[in] = -1;  // 标记右矩阵加载到L2内存的策略
          // c维度不分片（固定为1）
          slice_distributor(si.c, shape[1], 1);
          // 若当前操作是组内最后一个操作，w维度按h分片数分片
          if (ilp_lg_info._lgInfo.group_ops.back() == op) {
            slice_distributor(si.w, shape[3], ilp_lg_info.shape_secs.h_slice_num);
          } else {
            // 其他操作的w维度不分片
            slice_distributor(si.w, shape[3], 1);
          }
        } else {
          // 非右矩阵：c维度按c分片数分片，w维度不分片
          slice_distributor(si.c, shape[1], ilp_lg_info.shape_secs.c_slice_num);
          slice_distributor(si.w, shape[3], 1);
        }
        // 存储张量的分片信息（启用EU对齐，适配硬件计算单元）
        tensor_info_t t_info(si);
        t_info.eu_align = true;
        ilp_lg_info.tensor_infos[in] = t_info;
      }

      // 处理操作的输出张量
      for (auto out : get_output_values(op)) {
        shape = out.getType().cast<RankedTensorType>().getShape().vec();  // 输出张量形状
        slice_info_t si;  // 分片信息结构
        // 计算n、c、d、h维度分片
        slice_distributor(si.n, shape[0], n_slice);
        slice_distributor(si.c, shape[1], ilp_lg_info.shape_secs.c_slice_num);
        slice_distributor(si.d, 1, 1);
        slice_distributor(si.h, shape[2], h_slice);
        // 若当前操作是组内最后一个操作，w维度按h分片数分片
        if (ilp_lg_info._lgInfo.group_ops.back() == op) {
          slice_distributor(si.w, shape[3], ilp_lg_info.shape_secs.h_slice_num);
        } else {
          // 其他操作的w维度不分片
          slice_distributor(si.w, shape[3], 1);
        }
        // 存储张量的分片信息
        tensor_info_t t_info(si);
        t_info.eu_align = true;
        ilp_lg_info.tensor_infos[out] = t_info;
      }
    }
    return;  // 完成hdim_is_batch分支处理
  }

  // 分支2：hdim_is_batch为false时的分片逻辑（适用于mlp_group、single_matmul_group等）
  // 遍历层组内所有操作，为输入和输出张量计算分片信息
  for (auto op : ilp_lg_info._lgInfo.group_ops) {
    // 处理操作的输入张量
    for (auto in : get_input_values(op)) {
      // 获取输入张量的NCDHW维度
      module::getNCDHW(in, n, c, d, h, w, ilp_lg_info._lgInfo.type);
      slice_info_t si;  // 分片信息结构
      // 初始化分片：n按n分片数，c/d/h/w默认不分片
      slice_distributor(si.n, n, n_slice_num);
      slice_distributor(si.c, c, 1);
      slice_distributor(si.d, d, 1);
      slice_distributor(si.h, h, 1);
      slice_distributor(si.w, w, 1);

      // 若输入是矩阵乘法的右矩阵
      if (module::IsRightMat(in)) {
        // 根据组类型（mlp_group/attention_group/single_matmul_group）调整分片
        if (name() == "mlp_group") {
          // MLP组：第一个操作的右矩阵h维度按h分片数，其他操作的右矩阵c维度按h分片数
          if (ilp_lg_info._lgInfo.group_ops[0] == op) {
            llvm::errs() << "in: " << module::getName(in).str()
                         << ", cut h to h_slice_num\n";
            slice_distributor(si.h, h, ilp_lg_info.shape_secs.h_slice_num);
          } else {
            llvm::errs() << "in: " << module::getName(in).str()
                         << ", cut c to h_slice_num\n";
            slice_distributor(si.c, c, ilp_lg_info.shape_secs.h_slice_num);
          }
        } else if (name() == "attention_group") {
          // 注意力组：最后一个操作的右矩阵h维度按h分片数
          if (ilp_lg_info._lgInfo.group_ops.back() == op) {
            llvm::errs() << "in: " << module::getName(in).str()
                         << ", cut h to h_slice_num\n";
            slice_distributor(si.h, h, ilp_lg_info.shape_secs.h_slice_num);
          }
        } else if (name() == "single_matmul_group") {
          // 单矩阵乘法组：右矩阵h维度按h分片数
          llvm::errs() << "in: " << module::getName(in).str()
                       << ", cut h to h_slice_num\n";
          slice_distributor(si.h, h, ilp_lg_info.shape_secs.h_slice_num);
        }
        ilp_lg_info.value_load_to_l2m[in] = -1;  // 标记右矩阵加载到L2内存的策略
      } else {
        // 非右矩阵：c维度按c分片数，根据组类型调整h维度分片
        slice_distributor(si.c, c, ilp_lg_info.shape_secs.c_slice_num);
        llvm::errs() << "in: " << module::getName(in).str()
                     << ", cut c to c_slice_num\n";
        if (name() == "mlp_group") {
          // MLP组：非第一个MatMul操作或非MatMul操作，h维度按h分片数
          if (isa<tpu::MatMulOp>(op)) {
            if (ilp_lg_info._lgInfo.group_ops[0] != op) {
              slice_distributor(si.h, h, ilp_lg_info.shape_secs.h_slice_num);
              llvm::errs() << "in: " << module::getName(in).str()
                           << ", cut h to h_slice_num\n";
            }
          } else {
            slice_distributor(si.h, h, ilp_lg_info.shape_secs.h_slice_num);
          }
        } else if (name() == "single_matmul_group") {
          // 单矩阵乘法组：若操作在h_cut_ops中，h维度按h分片数
          if (std::find(h_cut_ops.begin(), h_cut_ops.end(), op) != h_cut_ops.end()) {
            llvm::errs() << "in: " << module::getName(in).str()
                         << ", cut h to h_slice_num\n";
            slice_distributor(si.h, h, ilp_lg_info.shape_secs.h_slice_num);
          }
        }
      }
      // 存储输入张量的分片信息
      tensor_info_t t_info(si);
      t_info.eu_align = true;
      ilp_lg_info.tensor_infos[in] = t_info;
    }

    // 处理操作的输出张量
    for (auto out : get_output_values(op)) {
      // 获取输出张量的NCDHW维度
      module::getNCDHW(out, n, c, d, h, w, ilp_lg_info._lgInfo.type);
      llvm::errs() << "out: " << module::getName(out).str()
                   << ", cut n/c to n/c_slice_num\n";
      slice_info_t si;  // 分片信息结构
      // 初始化分片：n按n分片数，c按c分片数，d/h/w默认不分片
      slice_distributor(si.n, n, n_slice_num);
      slice_distributor(si.c, c, ilp_lg_info.shape_secs.c_slice_num);
      slice_distributor(si.d, d, 1);
      slice_distributor(si.h, h, 1);
      slice_distributor(si.w, w, 1);

      // 根据组类型调整输出张量的h维度分片
      if (name() == "mlp_group") {
        // MLP组：非最后一个操作的h维度按h分片数；最后一个操作若h分片数>1，标记存储到L2策略
        if (ilp_lg_info._lgInfo.group_ops.back() != op) {
          llvm::errs() << "out: " << module::getName(out).str()
                       << ", cut h to h_slice_num\n";
          slice_distributor(si.h, h, ilp_lg_info.shape_secs.h_slice_num);
        } else {
          if (ilp_lg_info.shape_secs.h_slice_num > 1) {
            ilp_lg_info.value_store_to_l2m[out] = -1;
          }
        }
      } else if (name() == "attention_group") {
        // 注意力组：最后一个操作的h维度按h分片数
        if (ilp_lg_info._lgInfo.group_ops.back() == op) {
          llvm::errs() << "out: " << module::getName(out).str()
                       << ", cut h to h_slice_num\n";
          slice_distributor(si.h, h, ilp_lg_info.shape_secs.h_slice_num);
        }
      } else if (name() == "single_matmul_group") {
        // 单矩阵乘法组：MatMul操作或在h_cut_ops中的操作，h维度按h分片数
        if (isa<tpu::MatMulOp>(op) ||
            std::find(h_cut_ops.begin(), h_cut_ops.end(), op) != h_cut_ops.end()) {
          llvm::errs() << "out: " << module::getName(out).str()
                       << ", cut h to h_slice_num\n";
          slice_distributor(si.h, h, ilp_lg_info.shape_secs.h_slice_num);
        }
      }
      // 存储输出张量的分片信息
      tensor_info_t t_info(si);
      t_info.eu_align = true;
      ilp_lg_info.tensor_infos[out] = t_info;
    }
  }
}
```

### 6.speical_layer_group_base::update_shape_secs_for_ilp_group

#### 功能分析

该方法是特殊层组基类（`speical_layer_group_base`）中用于动态调整 ILP 组张量分片数的核心逻辑，主要功能是按照 “n→c→h” 的优先级递增各维度的分片数，直至每个维度的分片数达到其维度大小（即完全分片）。具体作用包括：

1. 逐步增加张量在 n（批次）、c（通道）、h（高度）维度的分片数量，以提升硬件并行计算的粒度。
2. 确保分片数不超过对应维度的实际大小（避免无效分片）。
3. 通过返回值标记是否发生更新，便于外层逻辑判断是否需要继续调整分片策略。

#### 逻辑流程

1. 优先级判断：按 “n 维度优先 →c 维度次之 →h 维度最后” 的顺序调整分片数。
2. 分支处理：

   - n 维度未达上限：若当前 n 分片数（`n_slice_num`）小于 n 维度大小（`n`），则增加 n 分片数（`n_slice_num++`）。
   - n 维度已达上限：
     - 若 c 分片数（`c_slice_num`）小于 c 维度大小（`c`），则增加 c 分片数（`c_slice_num++`）。
     - 若 c 维度也达上限，且 h 分片数（`h_slice_num`）小于 h 维度大小（`h`），则增加 h 分片数（`h_slice_num++`）。
3. 更新标记：任何维度的分片数被增加时，均将 `updated` 设为 `true`，最终返回该标记。

#### 原理说明

该方法的设计基于硬件并行计算中 “分片粒度逐步优化” 的需求，核心原理如下：

1. 分片数与并行能力：分片数越多，意味着张量被拆分为更多子张量，硬件可同时处理的子任务越多（如多核心并行），但分片过多可能增加调度开销。因此需逐步增加分片数，平衡并行效率与调度成本。
2. 维度优先级依据：

   - n 维度（批次）通常具有天然的并行性（不同批次数据独立），优先增加 n 分片数可最大化利用硬件的批次并行能力。
   - c 维度（通道）在卷积、矩阵乘法等操作中可独立计算（如通道级并行），作为次优先维度。
   - h 维度（高度）属于空间维度，在部分操作（如池化、切片）中可分片并行，作为最后优先级。
3. 上限约束：每个维度的分片数上限为其维度大小（如 `n_slice_num`≤`n`），避免 “分片数超过维度大小” 导致的无效计算（如分片大小为 0）。

#### speical_layer_group_base::update_shape_secs_for_ilp_group 代码

```cpp
// 特殊层组基类的方法：为ILP（指令级并行）组更新形状分段信息
// 参数：
//   shape_secs - 待更新的形状分段信息（包含各维度大小及当前分片数）
//   max_shape_secs - 最大形状分段信息（此处未直接使用，可能用于后续扩展）
// 返回值：是否发生了更新（true表示已更新，false表示未更新）
bool speical_layer_group_base::update_shape_secs_for_ilp_group(
    shape_secs_t &shape_secs, const shape_secs_t &max_shape_secs) {
  bool updated = false;  // 标记是否发生更新，初始为false

  // 分支1：若n维度的分片数已等于n维度大小（n维度已完全分片）
  if (shape_secs.n_slice_num == shape_secs.n) {
    // 分支1.1：若c维度的分片数也已等于c维度大小（c维度也完全分片）
    if (shape_secs.c_slice_num == shape_secs.c) {
      // 分支1.1.1：若h维度的分片数小于h维度大小，增加h维度分片数
      if (shape_secs.h_slice_num < shape_secs.h) {
        shape_secs.h_slice_num++;  // h维度分片数+1
        updated = true;            // 标记已更新
      }
    } else {
      // 分支1.2：c维度未完全分片，增加c维度分片数
      shape_secs.c_slice_num++;    // c维度分片数+1
      updated = true;              // 标记已更新
    }
  } else {
    // 分支2：n维度未完全分片，且当前分片数小于n维度大小，增加n维度分片数
    if (shape_secs.n_slice_num < shape_secs.n) {
      shape_secs.n_slice_num++;    // n维度分片数+1
      updated = true;              // 标记已更新
    }
  }

  return updated;  // 返回是否更新的结果
}
```

### 7.pattern_match_and_parser

#### 功能分析

该方法是 `single_matmul_group` 类的核心逻辑，主要功能是识别以单个矩阵乘法（`tpu::MatMulOp`）为核心的操作组，并对组内操作进行优化处理，具体包括：

1. 模式匹配：判断起始操作是否为矩阵乘法，以此识别单矩阵乘法组的核心。
2. 操作收集与排序：收集矩阵乘法周围的元素级操作（如 `Slice`、`Add`、`Reshape` 等），并按执行顺序排序。
3. 切割可行性判断：通过检查 `Slice` 操作的输入输出高度，确定是否支持 “列切割（`col_cut`）”（用于硬件并行优化）。
4. 冗余操作消除：移除无实际意义的 `Reshape` 操作（如仅用于中间形状适配，可跳过的转换），简化操作流程。
5. 操作树构建：确定组内可水平切割的操作（`h_cut_ops`），为后续硬件并行计算提供依据。
6. 有效性验证：最终检查操作组的合法性，返回匹配结果。

#### 逻辑流程

1. 核心识别：以 `MatMulOp` 作为起始标志，若起始操作不是矩阵乘法，直接返回失败。
2. 操作收集：收集矩阵乘法周围的元素级操作，按子网络顺序排序，确保操作序列正确。
3. 切割判断：通过 `Slice` 操作的维度检查，决定是否启用 `col_cut`（列切割）。
4. 冗余优化：

   - 识别并标记冗余的 `Reshape` 操作（如 `Reshape→MatMul→Reshape` 中的 `Reshape` 可跳过）。
   - 检查冗余操作是否有组外用户，确保删除后不影响外部计算。
   - 通过替换操作间的依赖关系（`replaceUsesWithIf`），跳过冗余 `Reshape`，并调整类型确保兼容。
5. 边缘清理：移除组边缘的无效 `Reshape`，去重并删除标记的冗余操作。
6. 操作树与有效性：构建可切割操作列表，移除主矩阵乘法，最终通过有效性检查返回结果。

#### 原理说明

该方法的设计基于深度学习计算图优化的核心需求，原理可总结为：

1. 模式识别依据：单矩阵乘法是深度学习中的基础操作（如线性变换），其周围常伴随元素级操作（如切片、加法、形状转换），这些操作可构成一个逻辑紧密的 “操作组”，适合联合优化。
2. 冗余消除原理：`Reshape` 操作仅改变张量形状而不改变数据，若其前后操作对形状无严格依赖（如仅为适配中间步骤），则可跳过以减少计算开销。通过替换操作的输入输出依赖（`replaceUsesWithIf`），实现 “逻辑上删除” 冗余操作，同时保证计算正确性。
3. 硬件适配优化：

   - 切割（`col_cut`、`h_cut`）是硬件并行计算的关键手段（如将大张量分片到多个核心），通过维度检查确保切割的可行性。
   - 操作树构建（`find_op_tree_by_root2`）明确可切割的操作范围，为硬件调度提供依据。
4. 有效性保障：通过 `check_group_valid` 确保优化后的操作组在语法和语义上合法（如无循环依赖、类型匹配），避免破坏计算逻辑。

#### 4.pattern_match_and_parser 代码

```cpp
// 虚函数：模式匹配与解析（重写基类方法）
// 功能：判断起始操作是否符合"单矩阵乘法组"模式，并解析相关子网络操作
// 参数：
//   start_op - 起始操作指针（作为模式匹配的入口）
//   subnet_ops - 子网络操作列表（存储当前组内的所有相关操作）
// 返回值：是否匹配成功（true表示成功识别单矩阵乘法组）
virtual bool
pattern_match_and_parser(Operation *start_op,
                         std::vector<Operation *> &subnet_ops) override {
  // 步骤1：检查起始操作是否为矩阵乘法操作（MatMulOp），这是单矩阵乘法组的核心标志
  if (isa<tpu::MatMulOp>(start_op)) {
    main_mm_op = start_op;  // 记录组内的主矩阵乘法操作
    // 打印日志：提示找到单矩阵乘法组及其名称（用于调试）
    llvm::errs() << "find single_matmul_group at "
                 << module::getName(start_op).str() << "\n";

    // 步骤2：收集矩阵乘法周围的元素级操作（如Add、Slice、Reshape等）
    // 这些操作与MatMul紧密相关，共同构成可优化的操作组
    CollectElementwiseOpAroundMatmul(start_op, subnet_ops, ops);
    // 按子网络操作的顺序对收集到的操作重新排序（确保操作执行顺序正确）
    auto ops_reorder = sortOpsByOtherOpsOrder(subnet_ops, ops);
    ops.assign(ops_reorder.begin(), ops_reorder.end());  // 更新操作列表

    // 步骤3：检查Slice操作的维度，判断是否支持"列切割（col_cut）"
    for (auto op : ops) {
      if (isa<tpu::SliceOp>(op)) {  // 若为切片操作
        // 获取Slice输入和输出的NCDHW维度（N-批次，C-通道，D-深度，H-高度，W-宽度）
        int64_t in_n, in_c, in_d, in_h, in_w, out_n, out_c, out_d, out_h, out_w;
        module::getNCDHW(op->getOperand(0), in_n, in_c, in_d, in_h, in_w, GROUP_MM_OPT3);
        module::getNCDHW(op->getResult(0), out_n, out_c, out_d, out_h, out_w, GROUP_MM_OPT3);
        // 若输入高度（in_h）不等于输出高度（out_h），说明切片改变了高度，禁用列切割
        if (in_h != out_h) {
          col_cut = false;
          break;  // 一旦发现不满足条件，立即退出循环
        }
      }
    }

    // 步骤4：处理冗余的Reshape操作（移除无实际意义的形状转换）
    std::vector<Operation *> del_ops, del_ops2;  // 存储待删除的操作
    for (auto op : ops) {
      if (isa<tpu::ReshapeOp>(op)) {  // 若为形状转换操作
        auto next_op = *(op->getUsers().begin());  // 获取Reshape的直接后续操作

        // 场景：Reshape + MatMul + Reshape + (Slice/Add) → 可简化为 MatMul + (Slice/Add)
        // 即中间的Reshape是冗余的，可删除
        if (isa<tpu::SliceOp, tpu::AddOp>(next_op) &&  // 后续操作是Slice或Add
            std::find(ops.begin(), ops.end(), next_op) != ops.end()) {  // 后续操作在当前组内

          // 获取Reshape的输入来源操作（即Reshape的输入是哪个操作的输出）
          auto mmOp = op->getOperand(0).getDefiningOp();
          if (mmOp && isa<tpu::MatMulOp>(mmOp)) {  // 若输入来源是MatMul
            // 获取MatMul的输入来源操作（若MatMul的输入也是Reshape）
            auto MMinReshapeOp = dyn_cast<tpu::MatMulOp>(mmOp).getInput().getDefiningOp();
            if (MMinReshapeOp && isa<tpu::ReshapeOp>(MMinReshapeOp)) {  // MatMul的输入是Reshape

              // 检查MMinReshapeOp是否有组外用户（若有则不能删除，否则会影响外部计算）
              bool has_out_user = false;
              for (auto user : MMinReshapeOp->getUsers()) {
                if (find(ops.begin(), ops.end(), user) == ops.end()) {  // 用户不在当前组内
                  has_out_user = true;
                  break;
                }
              }
              if (!has_out_user) {
                del_ops.push_back(MMinReshapeOp);  // 标记MMinReshapeOp为待删除
              }
              del_ops.push_back(op);  // 标记当前Reshape为待删除

              // 保存原始类型（用于后续恢复，确保类型兼容）
              auto oldType1 = MMinReshapeOp->getOperand(0).getType();  // MMinReshapeOp输入的类型
              auto oldType2 = op->getResult(0).getType();  // 当前Reshape输出的类型

              // 替换组内对MMinReshapeOp输出的使用 → 直接使用其输入（跳过MMinReshapeOp）
              MMinReshapeOp->getResult(0).replaceUsesWithIf(
                  MMinReshapeOp->getOperand(0), [&](OpOperand &operand) {
                    Operation *user = operand.getOwner();
                    return find(ops.begin(), ops.end(), user) != ops.end();  // 仅替换组内使用
                  });

              // 替换组内对当前Reshape输出的使用 → 直接使用MatMul的输出（跳当前Reshape）
              op->getResult(0).replaceUsesWithIf(
                  mmOp->getResult(0), [&](OpOperand &operand) {
                    Operation *user = operand.getOwner();
                    return find(ops.begin(), ops.end(), user) != ops.end();  // 仅替换组内使用
                  });

              // 恢复MatMul的输入和输出类型（确保与跳过Reshape后的类型一致）
              mmOp->getOperand(0).setType(oldType1);
              mmOp->getResult(0).setType(oldType2);
            }
          }
        }
      }
    }

    // 注释部分：处理Reshape与Cast的顺序调整（暂未启用）
    // 场景：matmul + reshape + cast → 调整为 matmul + cast + reshape（可能更高效）
    // for (auto op: ops) {
    //   if (isa<tpu::ReshapeOp>(op) && op->hasOneUse()) {
    //     // ... 具体逻辑与上述Reshape处理类似，通过替换使用关系调整操作顺序 ...
    //   }
    // }

    // 步骤5：移除边缘的Reshape操作（可能位于组的边界，无实际作用）
    findReshapeAtEdge(ops, del_ops2);  // 查找边缘Reshape
    for (auto del_op : del_ops2) {
      // 从操作列表中移除边缘Reshape
      ops.erase(std::remove(ops.begin(), ops.end(), del_op), ops.end());
    }

    // 步骤6：清理待删除操作（去重并从列表中移除）
    std::sort(del_ops.begin(), del_ops.end());  // 排序便于去重
    auto last = std::unique(del_ops.begin(), del_ops.end());  // 去重（相邻重复元素）
    del_ops.erase(last, del_ops.end());  // 删除重复元素
    for (auto del_op : del_ops) {
      llvm::errs() << "del_op: " << module::getName(del_op).str() << "\n";  // 打印删除的操作
      ops.erase(std::remove(ops.begin(), ops.end(), del_op), ops.end());  // 从操作列表移除
      need_del_ops.push_back(del_op);  // 记录到待删除列表（后续实际删除）
    }

    // 步骤7：构建操作树，确定可水平切割的操作（h_cut_ops）
    std::vector<Operation *> break_ops, accessed_ops;
    find_op_tree_by_root2(start_op, h_cut_ops, ops, accessed_ops, break_ops);
    // 从h_cut_ops中移除主MatMul操作（避免自身被切割）
    h_cut_ops.erase(std::remove(h_cut_ops.begin(), h_cut_ops.end(), start_op), h_cut_ops.end());

    // 步骤8：检查组是否有效，返回匹配结果
    return check_group_valid();
  }
  // 若起始操作不是MatMul，匹配失败
  return false;
}
```

### 8.DFS/BFS

#### 功能分析

这两个函数均用于从起始操作出发，在计算图中查找下一个矩阵乘法操作（`tpu::MatMulOp`），但采用不同的搜索策略：

1. `DfsFindNextMatMul`：通过深度优先搜索（DFS），优先沿着一条路径递归深入，直到找到下一个 `MatMulOp` 或搜索完所有路径。
2. `BfsFindNextMatMul`：通过广度优先搜索（BFS），按层次遍历起始操作的后续操作，优先查找距离起始操作更近的 `MatMulOp`，且限定仅在 `subnet_ops` 子网络内搜索。

两者的核心目标一致：在计算图中定位起始操作之后的下一个矩阵乘法，常用于识别由多个 `MatMul` 构成的复杂操作组（如 Transformer 中的注意力机制，包含 Q×K 和注意力权重 ×V 两个 `MatMul`）。

#### 逻辑流程

##### 1. `DfsFindNextMatMul` 逻辑

- 遍历用户：从 `start_op` 出发，遍历其所有后续操作（`getUsers()`）。
- 跳过终点：遇到 `ReturnOp`（计算图终点）则跳过。
- 匹配目标：若后续操作是 `MatMulOp`，直接记录并返回。
- 递归搜索：若后续操作是矩阵乘法组内的合法操作（`isInMatMulGrpOp`，如元素级操作），递归对该操作执行相同搜索。
- 深度优先：优先深入一条路径，直到找到目标或穷尽路径。

##### 2. `BfsFindNextMatMul` 逻辑

- 初始化队列：将 `start_op` 的所有子网络内后续操作（非 `ReturnOp`）加入 BFS 队列。
- 层次遍历：从队列中依次取出操作，检查是否为 `MatMulOp`。
- 匹配目标：若当前操作是 `MatMulOp`，记录并返回。
- 扩展队列：若当前操作是 `SoftmaxOp` 或组内合法操作，将其子网络内的后续操作加入队列（确保层次扩展）。
- 广度优先：按与 `start_op` 的距离（步骤数）递增顺序搜索，优先找到最近的 `MatMulOp`。

#### 原理说明

这两个函数的设计基于计算图的拓扑结构特性和深度学习操作的组合模式，核心原理如下：

1. 搜索策略选择：

   - DFS：适合探索深度较大的计算路径（如长序列的元素级操作后跟随 `MatMul`），实现简单但可能找到距离较远的 `MatMul`。
   - BFS：适合优先找到距离起始操作最近的 `MatMul`（如注意力机制中 Q×K 之后紧跟的 Softmax→MatMul），且通过队列控制搜索范围，效率更高。
2. 操作过滤依据：

   - 跳过 `ReturnOp`：避免搜索超出计算图有效范围（`ReturnOp` 通常是函数返回点，后续无有效计算）。
   - 限定 `isInMatMulGrpOp` 和 `subnet_ops`：矩阵乘法常与特定元素级操作（如 Add、Slice、Softmax）组合，过滤无关操作可减少搜索冗余，提高准确性（如避免误搜其他分支的 `MatMul`）。
3. 应用场景：在 Transformer 等模型的操作组识别中（如 `attention_group` 需要两个 `MatMul`），这两个函数可准确定位连续的 `MatMul` 操作，为后续的硬件加速优化（如联合分片、并行计算）提供基础。

#### DFS/BFS 代码

```cpp
// 深度优先搜索（DFS）查找下一个矩阵乘法操作
// 参数：
//   start_op - 起始操作（从该操作开始搜索）
//   next_matmul_op - 输出参数，用于存储找到的下一个MatMul操作（引用传递）
void DfsFindNextMatMul(Operation *start_op, Operation *&next_matmul_op) {
  // 遍历起始操作的所有用户（即依赖该操作输出的后续操作）
  for (auto user : start_op->getUsers()) {
    // 跳过ReturnOp（返回操作，通常是计算图的终点，无需继续搜索）
    if (isa<ReturnOp>(user)) {
      continue;
    }
    // 若当前用户是矩阵乘法操作（MatMulOp），则找到目标，记录并返回
    if (isa<tpu::MatMulOp>(user)) {
      next_matmul_op = user;
      return;
    }
    // 若当前用户是矩阵乘法组内的合法操作（如Add、Slice等），递归继续搜索
    else if (module::isInMatMulGrpOp(user)) {
      DfsFindNextMatMul(user, next_matmul_op);
    }
  }
}

// 广度优先搜索（BFS）查找下一个矩阵乘法操作
// 参数：
//   start_op - 起始操作（从该操作开始搜索）
//   next_matmul_op - 输出参数，用于存储找到的下一个MatMul操作（引用传递）
//   subnet_ops - 子网络操作列表（限定搜索范围，仅在该列表内的操作中搜索）
void BfsFindNextMatMul(Operation *start_op, Operation *&next_matmul_op,
                       std::vector<Operation *> &subnet_ops) {
  std::queue<Operation *> q;  // BFS队列，存储待搜索的操作

  // 初始化队列：将起始操作的所有用户（且在子网络内、非ReturnOp）加入队列
  for (auto user : start_op->getUsers()) {
    if (!isa<ReturnOp>(user)) {  // 跳过ReturnOp
      // 仅处理子网络内的操作（避免搜索范围过大）
      if (std::find(subnet_ops.begin(), subnet_ops.end(), user) != subnet_ops.end()) {
        q.push(user);
      }
    }
  }

  // 广度优先搜索循环
  while (!q.empty()) {
    auto op = q.front();  // 取出队首操作
    q.pop();

    // 若当前操作是矩阵乘法，记录并返回
    if (isa<tpu::MatMulOp>(op)) {
      next_matmul_op = op;
      return;
    }

    // 若当前操作是Softmax或矩阵乘法组内的合法操作，继续扩展搜索其用户
    if (isa<tpu::SoftmaxOp>(op) || module::isInMatMulGrpOp(op)) {
      for (auto user : op->getUsers()) {
        if (!isa<ReturnOp>(user)) {  // 跳过ReturnOp
          // 仅将子网络内的操作加入队列
          if (std::find(subnet_ops.begin(), subnet_ops.end(), user) != subnet_ops.end()) {
            q.push(user);
          }
        }
      }
    }
  }
}
```

## 9./LayerGroup/GroupPostTransform.cpp

### 1.conv3d_weight_transform_bm1684

#### 代码功能分析

该函数 `conv3d_weight_transform_bm1684` 用于对 3D 卷积的权重数据进行格式转换，适配 BM1684 硬件平台的内存布局要求。通过调整权重数据的维度排列顺序和内存对齐方式，优化硬件对权重数据的访问效率，从而提升 3D 卷积运算性能。

#### 核心逻辑与原理

3D 卷积权重通常具有 5 个维度：输出通道数 (OC)、输入通道数 (IC)、卷积核时间维度 (KT)、卷积核高度 (KH)、卷积核宽度 (KW)。原始权重数据的存储顺序与硬件高效访问所需的顺序可能不一致，该函数通过以下逻辑实现转换：

1. 数据类型区分：根据 `type_bytes`（4 字节或 1 字节）区分处理 float 类型（4 字节）和 char 类型（1 字节，如 INT8 量化数据）的权重。
2. 转换方法区分：通过 `method` 参数选择不同的维度重排策略，适配硬件不同的计算需求。
3. 索引映射：计算原始权重数据（`weight_orig`）与转换后数据（`weight_trans`）的索引映射关系，核心是通过嵌套循环遍历所有权重元素，并根据维度优先级和内存对齐要求（`align_up`）计算目标地址。
4. 内存对齐：使用 `align_up` 函数确保特定维度的内存地址对齐，满足硬件访问的对齐约束，提升数据读写效率。

#### 3.method 参数选择

`method` 参数决定了维度的优先级排列顺序，不同方法针对 3D 卷积核的时间维度（KT）、空间维度（KH/KW）、通道维度（OC/IC） 的重要性做了区分，适配不同卷积核尺寸特性。

##### 1. `method=0`（不同 `type_bytes` 下的共性与差异）

- 维度优先级（核心逻辑）：

  - `type_bytes=4`：`KT → OC → KW → (IC×KH，对齐) → IC → KH`（时间维度优先，其次是输出通道和宽度）。
  - `type_bytes=1`：`OC → KH → KW → (IC×KT，对齐) → IC → KT`（输出通道和空间维度优先，输入通道优先于时间维度）。
- 核心特点：
  强调时间维度（KT） 或输入通道（IC） 的连续性，适合时间维度较大或输入通道数较多的卷积核。
- 适用场景：

  - 3D 卷积核的时间维度 `KT` 较大（如视频帧序列较长，`KT=3/5`），需要硬件高效访问时间维度连续的数据。
  - 输入通道数 `IC` 较大（如早期网络层，`IC=64/128`），优先保证输入通道维度的连续性以提升缓存命中率。

##### 2. `method=1`（不同 `type_bytes` 下的共性与差异）

- 维度优先级（核心逻辑）：

  - `type_bytes=4`：`KT → OC → KH → KW → (IC，对齐) → IC`（时间维度优先，空间维度中高度优先于宽度，最后是输入通道）。
  - `type_bytes=1`：`OC → KH → KW → (IC×KT，对齐) → KT → IC`（输出通道和空间维度优先，时间维度优先于输入通道）。
- 核心特点：
  强调空间高度（KH） 或时间维度（KT） 的连续性，适合空间高度较大或时间维度需要优先访问的场景。
- 适用场景：

  - 3D 卷积核的空间高度 `KH` 较大（如 `KH=3/7`），需要硬件高效遍历高度方向的权重。
  - 量化模型中，时间维度 `KT` 是访问热点（如视频实时推理，需快速处理时间序列），优先保证 KT 的连续性。

##### 3. `method=2`（仅 `type_bytes=4` 支持）

- 维度优先级（核心逻辑）：
  `OC → KH → KW → (IC×KT，对齐) → IC → KT`（输出通道优先，空间维度（高度、宽度）次之，最后是输入通道和时间维度）。
- 核心特点：
  强调输出通道（OC） 和空间维度（KH/KW） 的连续性，适合输出通道数多、空间维度是访问热点的场景。
- 适用场景：

  - 输出通道数 `OC` 较大（如网络后期层，`OC=256/512`），需要硬件连续访问同一输出通道的权重。
  - 3D 卷积的空间维度（`KH`/`KW`）是计算瓶颈（如高分辨率 3D 数据，空间卷积占比高），优先优化空间维度的访问效率。

#### 4.conv3d_weight_transform_bm1684 代码

```java
/**
 * @brief 3D卷积权重数据格式转换（适配BM1684芯片）
 * 
 * BM1684芯片是深度学习推理加速芯片，对3D卷积权重的内存布局有特定要求。
 * 该函数将通用格式的3D卷积权重（[OC, IC, KT, KH, KW]）转换为芯片可高效访问的格式，
 * 通过调整维度排列顺序和内存对齐，优化卷积计算时的数据读写效率。
 * 
 * @param IC        输入通道数（Input Channels）：卷积核的输入特征图通道数
 * @param OC        输出通道数（Output Channels）：卷积核的输出特征图通道数，每个输出通道对应一组独立卷积核
 * @param KT        卷积核时间维度（Kernel Time）：3D卷积在时间/深度方向的核大小（如视频帧序列方向）
 * @param KH        卷积核高度（Kernel Height）：空间维度中高度方向的核大小
 * @param KW        卷积核宽度（Kernel Width）：空间维度中宽度方向的核大小
 * @param weight_orig  原始权重数据指针（输入）：按[OC, IC, KT, KH, KW]顺序存储的通用格式权重
 * @param weight_trans 转换后权重数据指针（输出）：BM1684适配格式的权重，内存布局由method和type_bytes决定
 * @param method    转换方法（0/1/2）：指定维度排列策略，适配不同计算场景
 * @param type_bytes 数据类型字节数：4（float32）或1（int8/uint8），区分精度处理逻辑
 */
void conv3d_weight_transform_bm1684(int IC, int OC, int KT, int KH, int KW,
                                    const void *weight_orig,
                                    const void *weight_trans, int method,
                                    int type_bytes) {
  // 处理4字节数据（如float32类型权重，适用于未量化模型）
  if (type_bytes == 4) {
    // 转换方法0：维度优先级为 KT → OC → KW → (IC×KH，对齐) → IC → KH
    if (method == 0) {
      // 遍历顺序：OC → IC → KT → KH → KW（与原始权重存储顺序一致，确保遍历所有元素）
      for (int oc = 0; oc < OC; ++oc) {       // 遍历输出通道
        for (int ic = 0; ic < IC; ++ic) {     // 遍历输入通道
          for (int kt = 0; kt < KT; ++kt) {   // 遍历时间维度
            for (int kh = 0; kh < KH; ++kh) { // 遍历高度维度
              for (int kw = 0; kw < KW; ++kw) { // 遍历宽度维度
                // 计算原始权重中当前元素的一维索引（src）
                // 原始存储顺序：OC为最高维度，依次是IC、KT、KH，KW为最低维度
                long long src = 
                  oc * (IC * KT * KH * KW) +   // 输出通道偏移：每个OC包含 IC×KT×KH×KW 个元素
                  ic * (KT * KH * KW) +        // 输入通道偏移：每个IC包含 KT×KH×KW 个元素
                  kt * (KH * KW) +             // 时间维度偏移：每个KT包含 KH×KW 个元素
                  kh * KW + kw;                // 空间维度偏移：每个KH包含 KW 个元素，最后加KW偏移
                
                // 计算转换后权重中当前元素的一维索引（dst）
                // 转换后顺序：KT为最高维度，依次是OC、KW，再是对齐后的IC×KH，最后是IC×KH内的偏移
                long long dst = 
                  kt * (OC * KW * align_up(IC * KH, 2)) +  // KT偏移：每个KT包含 OC×KW×(对齐后的IC×KH) 个元素
                  oc * (KW * align_up(IC * KH, 2)) +       // OC偏移：每个OC包含 KW×(对齐后的IC×KH) 个元素
                  kw * align_up(IC * KH, 2) +              // KW偏移：每个KW包含 (对齐后的IC×KH) 个元素
                  ic * KH + kh;                            // IC×KH内偏移：IC优先于KH，无额外对齐
                
                // 将原始权重元素复制到转换后地址（float指针访问4字节数据）
                *((float *)weight_trans + dst) = *((float *)weight_orig + src);
              }
            }
          }
        }
      }
    } 
    // 转换方法1：维度优先级为 KT → OC → KH → KW → (IC，对齐) → IC
    else if (method == 1) {
      for (int oc = 0; oc < OC; ++oc) {
        for (int ic = 0; ic < IC; ++ic) {
          for (int kt = 0; kt < KT; ++kt) {
            for (int kh = 0; kh < KH; ++kh) {
              for (int kw = 0; kw < KW; ++kw) {
                // 原始索引计算同方法0（OC→IC→KT→KH→KW）
                long long src = 
                  oc * (IC * KT * KH * KW) + 
                  ic * (KT * KH * KW) + 
                  kt * (KH * KW) + 
                  kh * KW + kw;
                
                // 转换后索引：KT→OC→KH→KW→(对齐后的IC)→IC
                long long dst = 
                  kt * (OC * KH * KW * align_up(IC, 2)) +  // KT偏移：每个KT包含 OC×KH×KW×(对齐后的IC) 个元素
                  oc * (KH * KW * align_up(IC, 2)) +       // OC偏移：每个OC包含 KH×KW×(对齐后的IC) 个元素
                  kh * (KW * align_up(IC, 2)) +            // KH偏移：每个KH包含 KW×(对齐后的IC) 个元素
                  kw * align_up(IC, 2) +                   // KW偏移：每个KW包含 (对齐后的IC) 个元素
                  ic;                                      // IC偏移：最低维度，直接索引（对齐后）
                
                *((float *)weight_trans + dst) = *((float *)weight_orig + src);
              }
            }
          }
        }
      }
    } 
    // 转换方法2：维度优先级为 OC → KH → KW → (IC×KT，对齐) → IC → KT
    else if (method == 2) {
      for (int oc = 0; oc < OC; ++oc) {
        for (int ic = 0; ic < IC; ++ic) {
          for (int kt = 0; kt < KT; ++kt) {
            for (int kh = 0; kh < KH; ++kh) {
              for (int kw = 0; kw < KW; ++kw) {
                // 原始索引计算同方法0（OC→IC→KT→KH→KW）
                long long src = 
                  oc * (IC * KT * KH * KW) + 
                  ic * (KT * KH * KW) + 
                  kt * (KH * KW) + 
                  kh * KW + kw;
                
                // 转换后索引：OC→KH→KW→(对齐后的IC×KT)→IC×KT内偏移
                long long dst = 
                  oc * KH * KW * align_up(IC * KT, 2) +  // OC偏移：每个OC包含 KH×KW×(对齐后的IC×KT) 个元素
                  kh * KW * align_up(IC * KT, 2) +       // KH偏移：每个KH包含 KW×(对齐后的IC×KT) 个元素
                  kw * align_up(IC * KT, 2) +            // KW偏移：每个KW包含 (对齐后的IC×KT) 个元素
                  ic * KT + kt;                          // IC×KT内偏移：IC优先于KT，无额外对齐
                
                *((float *)weight_trans + dst) = *((float *)weight_orig + src);
              }
            }
          }
        }
      }
    } 
    // 无效method：触发编译期断言（BM1684不支持该方法，防止错误调用）
    else {
      llvm_unreachable("wrong conv weight data type");
    }
  } 
  // 处理1字节数据（如int8/uint8量化权重，适用于低精度推理）
  else if (type_bytes == 1) {
    // 遍历顺序与原始权重一致（OC→IC→KT→KH→KW）
    for (int oc = 0; oc < OC; ++oc) {
      for (int ic = 0; ic < IC; ++ic) {
        for (int kt = 0; kt < KT; ++kt) {
          for (int kh = 0; kh < KH; ++kh) {
            for (int kw = 0; kw < KW; ++kw) {
              // 原始索引计算：同float类型（OC→IC→KT→KH→KW）
              long long src = 
                oc * (IC * KT * KH * KW) + 
                ic * (KT * KH * KW) + 
                kt * (KH * KW) + 
                kh * KW + kw;
              
              // 转换后基础索引：OC→KH→KW→(对齐后的IC×KT，4字节对齐)
              long long dst = 
                oc * KH * KW * align_up(IC * KT, 4) +  // OC偏移：每个OC包含 KH×KW×(对齐后的IC×KT) 个元素
                kh * KW * align_up(IC * KT, 4) +       // KH偏移：每个KH包含 KW×(对齐后的IC×KT) 个元素
                kw * align_up(IC * KT, 4);             // KW偏移：每个KW包含 (对齐后的IC×KT) 个元素
              
              // 根据method调整IC和KT的顺序（量化数据特有的维度排列）
              if (method == 0) {
                dst += (ic * KT + kt);  // IC优先于KT：IC×KT内按IC→KT排列
              } else if (method == 1) {
                dst += (kt * IC + ic);  // KT优先于IC：IC×KT内按KT→IC排列
              } else {
                // 无效method：触发编译期断言
                llvm_unreachable("wrong conv weight data type");
              }
              
              // 将原始权重元素复制到转换后地址（char指针访问1字节数据）
              *((char *)weight_trans + dst) = *((char *)weight_orig + src);
            }
          }
        }
      }
    }
  } 
  // 无效数据类型：触发编译期断言（BM1684仅支持4字节和1字节权重）
  else {
    llvm_unreachable("wrong conv weight data type");
  }
}
```

### 2.conv3d_post_transform

#### 核心功能

`conv3d_post_transform` 是 3D 卷积操作的权重后处理转换函数，用于在深度学习模型部署阶段，根据目标硬件（如 BM1684、BM1684X、BM1690 等芯片）的架构特性，对 3D 卷积的权重数据进行格式重排（reorder），使其适配硬件的内存布局和计算单元需求，最终提升 3D 卷积在硬件上的推理效率。

该函数的核心作用是桥梁：将通用深度学习框架（如 TensorFlow、PyTorch）输出的 3D 卷积权重格式（通常为 `[OC, IC, KT, KH, KW]`）转换为特定硬件可高效访问的格式，解决 “通用格式与硬件专用布局不匹配” 导致的性能损失问题。

#### 核心逻辑

函数的逻辑可分为参数解析、硬件适配分支、数据类型与场景分支、权重重排与更新四个步骤，整体流程如下：

1. 参数解析：从 3D 卷积操作（`conv3d_op`）中提取关键参数，包括输入通道数（IC）、输出通道数（OC）、卷积核维度（KT/KH/KW）、步长（stride_h/stride_w）、数据类型（float32、int8 等）、分组信息（groups）等，为后续转换提供依据。
2. 硬件适配分支：根据目标硬件类型（BM1684 系列 vs BM1684X/BM1690 系列）分分支处理，因为不同硬件的内存控制器、计算单元架构不同，权重布局需求也不同。
3. 数据类型与场景分支：在每个硬件分支下，进一步根据权重数据类型（float32、int8、float16/bfloat16）和业务场景（如是否为组操作、步长是否大于 15），选择对应的权重重排策略。
4. 权重重排与更新：根据选定的策略，计算权重元素的新索引，将原始权重数据复制到重排后的内存中，并更新卷积操作的权重输入（`op->setOperand`），完成格式转换。

#### 核心原理

3D 卷积的计算效率高度依赖硬件对权重数据的访问连续性和内存对齐。通用框架的权重格式是 “通用优先”，而硬件计算单元（如 BM1684 的 NPU）为了并行计算，要求权重按特定维度顺序存储（如优先时间维度、空间维度或通道维度），并满足对齐约束（如按 2/4 字节或并行度对齐）。

本函数的原理是 “硬件特性驱动的格式转换”：

- 针对不同硬件（BM1684 系列更注重时间 / 空间维度优先，BM1684X/BM1690 系列更注重并行度适配）设计专属重排规则；
- 针对不同数据类型（float32 精度高但占用内存大，int8/f16 占用内存小但需量化适配）调整对齐方式和维度顺序；
- 针对特殊场景（如大步长、组操作）优化布局，避免硬件访问时的 “碎片化读取”（降低缓存命中率）。

#### 具体分支逻辑

##### 顶层分支：硬件类型区分

函数首先根据目标硬件类型（BM1684 系列 vs BM1684X/BM1690 系列）分为两大顶层分支。这是因为两类硬件的架构差异显著：

- BM1684 系列：计算单元更注重时间维度（KT）和空间维度（KH/KW）的连续性，内存对齐要求以 2 字节为主。
- BM1684X/BM1690 系列：支持更高的通道并行度（IC_PARALLEL），计算单元依赖 “按并行度拆分通道” 提升效率，内存对齐更关注并行度适配。

##### 分支 1：BM1684 系列硬件处理

触发条件：`module::isBM1684Family()` 为真。
核心逻辑：根据卷积参数动态选择 `method`（转换方法），仅支持 float32 类型权重（int8 暂不支持）。

###### 1.1 `method` 参数的选择逻辑

`method` 决定了维度优先级，其值由卷积参数和场景共同决定：

- 若时间步长 `attr.dd > 1`（时间维度步长大于 1）：选择 `method=2`（优先输出通道和空间维度）。
- 若输入通道数 `attr.ic/attr.groups > 10`（每组输入通道数较大）或高度步长 `attr.dh > 1`：选择 `method=1`（优先时间和高度维度）。
- 若为组操作（`lg_info.group_ops.size() > 1`）：强制 `method=1`（硬件组计算仅支持该模式）。
- 默认：`method=0`（优先时间和宽度维度）。

###### 1.2 float32 类型权重的处理（`filter_type.isF32()`）

根据 `method` 确定重排后的权重形状，并调用 `conv3d_weight_transform_bm1684` 完成转换：

- `method=0`：
  目标形状：`[KT, OC, KW, align_up(IC×KH, 2), 1]`
  维度优先级：`KT → OC → KW → (IC×KH，对齐) → IC → KH`
  适用场景：时间维度（KT）或宽度维度（KW）是访问热点。
- `method=1`：

  - 组操作场景：目标形状 `[KT, OC, 1, KH×KW, align_up(IC, 2)]`（合并 KH 和 KW，适配组计算）。
  - 非组操作场景：目标形状 `[KT, OC, KH×KW, align_up(IC, 2), 1]`。
    维度优先级：`KT → OC → KH×KW → (IC，对齐) → IC`
    适用场景：输入通道数较大或高度维度（KH）是访问热点。
- `method=2`：
  目标形状：`[OC, KH, KW, align_up(IC×KT, 2), 1]`
  维度优先级：`OC → KH → KW → (IC×KT，对齐) → IC → KT`
  适用场景：输出通道数（OC）较大或时间步长大于 1。

###### 1.3 其他数据类型

- int8 类型：`llvm_unreachable`（暂不支持）。
- 其他类型：`llvm_unreachable`（仅支持 float32）。

##### 分支 2：BM1684X/BM1690 系列硬件处理

触发条件：`module::isBM1684XFamily() || module::isBM1690Family()` 为真。
核心逻辑：根据数据类型（float32、float16/bfloat16、int8）和场景（组操作、步长 > 15）细分处理，重点适配硬件的通道并行度（IC_PARALLEL）。

###### 3.1 float32 类型权重（`filter_type.isF32()`）

####### 3.1.1 组操作场景（`lg_info.group_ops.size() > 1`）

- 目标形状：`[1, OC, KT, IC, KH×KW]`
- 维度重排逻辑：原始 `[OC, IC, KT, KH, KW]` → 新 `[1, OC, KT, IC, KH×KW]`。
  索引映射：将 `KT` 维度提前到 `IC` 之前，使时间维度（KT）的访问更连续，适配组计算的并行访问模式。

####### 3.1.2 非组操作场景（`lg_info.group_ops.size() == 1`）

- 若步长 > 15（`strideh_gt_15 || stridew_gt_15`）：调用 `conv3d_stride_gt_15_weightreorder<float>`（硬件对大步长有专用优化，重排为适配大步长计算的布局）。
- 否则：不额外处理（默认布局已适配）。

###### 3.2 float16/bfloat16 类型权重（`filter_type.isF16() || filter_type.isBF16()`）

这类数据以 16 位存储（`uint16_t`），需按硬件通道并行度（IC_PARALLEL）拆分维度，提升并行计算效率。

####### 3.2.1 组操作场景（`lg_info.group_ops.size() > 1`）

- 目标形状：`[1, OC, KT, ceiling(IC/IC_PARALLEL), KH×KW×IC_PARALLEL]`
- 维度重排逻辑：
  原始 `[OC, IC, KT, KH, KW]` → 按 `IC_PARALLEL` 拆分 `IC` 为 `ceiling(IC/IC_PARALLEL)` 组，每组内包含 `IC_PARALLEL` 个通道，并将 `KH×KW` 与并行度内通道合并。
- 索引映射：确保每组内的 `IC_PARALLEL` 个通道连续存储，适配硬件的 16 位并行计算单元。

####### 3.2.2 非组操作场景（`lg_info.group_ops.size() == 1`）

- 若步长 > 15：调用 `conv3d_stride_gt_15_weightreorder<uint16_t>`（专用大步长重排）。
- 否则：
  目标形状：`[1, OC, ceiling(IC×KT/IC_PARALLEL), KH×KW, IC_PARALLEL]`
  维度重排逻辑：将 `IC×KT` 合并后按 `IC_PARALLEL` 拆分，使并行度内的元素连续存储，提升硬件对 `IC×KT` 维度的并行访问效率。

###### 3.3 int8 类型权重（`filter_type.isInteger(8)`）

这类数据以 8 位存储（`int8_t`），处理逻辑与 float16 类似，但有大步长限制。

####### 3.3.1 组操作场景（`lg_info.group_ops.size() > 1`）

- 目标形状：`[1, OC, KT, ceiling(IC/IC_PARALLEL), KH×KW×IC_PARALLEL]`
- 维度重排逻辑：与 float16 组操作一致，按 `IC_PARALLEL` 拆分 `IC`，确保并行度内的 int8 数据连续，适配硬件的 8 位整数计算单元。

####### 3.3.2 非组操作场景（`lg_info.group_ops.size() == 1`）

- 若步长 > 15：`llvm_unreachable`（暂不支持 int8 类型的大步长场景）。
- 否则：
  目标形状：`[1, OC, ceiling(IC×KT/IC_PARALLEL), KH×KW, IC_PARALLEL]`
  维度重排逻辑：与 float16 非组操作一致，合并 `IC×KT` 后按并行度拆分，优化硬件对 int8 数据的并行访问。

#### conv3d_post_transform 代码

```java
/**
 * @brief 3D卷积权重的后处理转换函数，适配BM系列硬件（BM1684/1684X/1690）
 * 
 * 功能：将通用格式的3D卷积权重转换为目标硬件可高效访问的格式，通过调整维度顺序、
 * 内存对齐和并行度适配，优化硬件计算单元的访问效率，提升3D卷积推理性能。
 * 
 * @param op        3D卷积操作对象（包含权重、输入输出等信息）
 * @param lg_info   层组信息（用于判断是否为组操作等场景）
 */
static void conv3d_post_transform(Operation *op, const LgInfo &lg_info) {
  // 将输入的Operation转换为3D卷积操作对象，获取卷积参数
  auto conv3d_op = dyn_cast<tpu::Conv3DOp>(op);
  // 解析3D卷积的属性（如通道数、卷积核尺寸、步长、分组等）
  auto attr = conv3d_op.parseParam();
  // 获取权重操作对象（原始权重数据）
  auto filter_op = conv3d_op.getFilter().getDefiningOp<top::WeightOp>();
  // 获取权重的数据类型（如float32、int8等）
  auto filter_type = module::getStorageType(conv3d_op.getFilter());
  
  // 解析核心维度参数：
  int64_t OC = attr.oc;               // 输出通道数
  int64_t IC = attr.ic / attr.groups; // 每组输入通道数（总输入通道数/分组数）
  int64_t KT = attr.kd;               // 卷积核时间维度（depth）
  int64_t KH = attr.kh;               // 卷积核高度
  int64_t KW = attr.kw;               // 卷积核宽度
  
  // 解析数据类型相关参数：硬件数据类型、每个元素的字节数
  auto data_type = BM168x::getDataType(conv3d_op.getFilter());
  auto fmt_bytes = BM168x::getFmtBytes(data_type);
  
  // 硬件输入通道并行度（IC_PARALLEL）：BM1684X/BM1690等硬件的通道并行计算能力
  int64_t IC_PARALLEL = BM168x::ic_num(fmt_bytes);
  
  // 原始权重的形状（通用格式：[输出通道, 输入通道, 时间维度, 高度, 宽度]）
  std::vector<int64_t> ori_filter_shape = {OC, IC, KT, KH, KW};
  auto ori_type = RankedTensorType::get(ori_filter_shape, filter_type);
  // 获取输出特征图的数据类型
  auto out_type = module::getStorageType(conv3d_op.getOutput());

  // 解析步长参数（高度和宽度方向的步长）
  int stride_h = attr.sh;
  int stride_w = attr.sw;
  // 判断步长是否大于15（硬件对大步长有特殊处理需求）
  bool strideh_gt_15 = stride_h > 15;
  bool stridew_gt_15 = stride_w > 15;

  // 调整偏置（bias）的形状为硬件适配格式（[1, OC, 1, 1, 1]）
  if (attr.has_bias) {
    llvm::SmallVector<int64_t> bias_shape = {1, attr.oc, 1, 1, 1};
    auto bias_type = module::getStorageType(conv3d_op.getBias());
    auto new_type = RankedTensorType::get(bias_shape, bias_type);
    conv3d_op.getBias().setType(new_type);
  }

  // 分支1：适配BM1684系列硬件
  if (module::isBM1684Family()) {
    // 确定转换方法（method），根据卷积参数动态选择（参考之前的method解析）
    int method = 0;
    if (attr.dd > 1)               // 时间维度步长>1时，用method=2
      method = 2;
    else if (attr.ic / attr.groups > 10 || attr.dh > 1)  // 输入通道数大或高度步长>1时，用method=1
      method = 1;
    if (lg_info.group_ops.size() > 1)  // 组操作场景强制用method=1（硬件限制）
      method = 1;

    // 处理float32类型权重
    if (filter_type.isF32()) {
      // 重置权重类型为原始形状（用于后续重排）
      conv3d_op.getFilter().setType(ori_type);
      // 读取原始float32权重数据
      auto filter_f32 = filter_op.read<float>();
      // 根据method确定重排后的权重形状
      std::vector<int64_t> filter_shape;
      if (0 == method) {
        // method=0的目标形状：[KT, OC, KW, 对齐后的(IC×KH), 1]
        filter_shape.assign({KT, OC, KW, align_up((IC * KH), 2), 1});
      } else if (1 == method) {
        if (lg_info.group_ops.size() > 1) {
          // 组操作时，形状调整为[KT, OC, 1, KH×KW, 对齐后的IC]（适配组计算）
          filter_shape.assign({KT, OC, 1, KH * KW, align_up(IC, 2)});
        } else {
          // 非组操作时，形状为[KT, OC, KH×KW, 对齐后的IC, 1]
          filter_shape.assign({KT, OC, KH * KW, align_up(IC, 2), 1});
        }
      } else if (2 == method) {
        // method=2的目标形状：[OC, KH, KW, 对齐后的(IC×KT), 1]
        filter_shape.assign({OC, KH, KW, align_up(IC * KT, 2), 1});
      }
      // 分配重排后的权重内存（初始化为0）
      auto filter_new =
          std::make_shared<std::vector<float>>(get_shape_size(filter_shape), 0);
      // 调用BM1684专属的3D卷积权重转换函数（之前分析的核心转换逻辑）
      conv3d_weight_transform_bm1684(IC, OC, KT, KH, KW, filter_f32->data(),
                                     filter_new->data(), method, fmt_bytes);
      // 更新权重数据为转换后的数据
      filter_f32 = filter_new;
      // 创建转换后的权重类型（新形状+原数据类型）
      auto filter_ranked_type =
          RankedTensorType::get(filter_shape, filter_type);
      // 创建新的权重操作对象，并更新3D卷积操作的权重输入
      auto new_filter = top::WeightOp::create(op, "postreordered", *filter_f32,
                                              filter_ranked_type);
      op->setOperand(1, new_filter);
    } else if (filter_type.isInteger(8)) {
      // 暂不支持int8类型（预留接口）
      // not support now
    } else {
      // 无效数据类型：触发编译期断言
      llvm_unreachable("wrong conv weight data type");
    }

  } 
  // 分支2：适配BM1684X/BM1690系列硬件
  else if (module::isBM1684XFamily() || module::isBM1690Family()) {
    // 重置权重类型为原始形状
    conv3d_op.getFilter().setType(ori_type);

    // 子分支1：float32类型权重，且为组操作（lg_info.group_ops.size() > 1）
    if (filter_type.isF32() && lg_info.group_ops.size() > 1) {
      // 重排维度：[OC, IC, KT, KH, KW] → [1, OC, KT, IC, KH×KW]
      auto filter_f32 = filter_op.read<float>();
      std::vector<int64_t> filter_shape = {1, OC, KT, IC, KH * KW};
      auto filter_new =
          std::make_shared<std::vector<float>>(get_shape_size(filter_shape), 0);
      // 遍历原始权重，计算新索引并复制数据
      for (int64_t oc = 0; oc < OC; oc++) {
        for (int64_t ic = 0; ic < IC; ic++) {
          for (int64_t kt = 0; kt < KT; kt++) {
            for (int64_t khw = 0; khw < KH * KW; khw++) {
              // 原始索引：OC→IC→KT→(KH×KW)
              long long src_offset = oc * (IC * KT * KH * KW) +
                                     ic * (KT * KH * KW) + kt * (KH * KW) + khw;
              // 新索引：OC→KT→IC→(KH×KW)（提升KT和IC的访问连续性）
              long long dst_offset = oc * (IC * KT * KH * KW) +
                                     kt * (IC * KH * KW) + ic * (KH * KW) + khw;
              filter_new->at(dst_offset) = filter_f32->at(src_offset);
            }
          }
        }
      }
      // 更新权重数据、类型，并绑定到卷积操作
      filter_f32 = filter_new;
      auto filter_ranked_type =
          RankedTensorType::get(filter_shape, filter_type);
      auto new_filter = top::WeightOp::create(op, "postreordered", *filter_f32,
                                              filter_ranked_type);
      op->setOperand(1, new_filter);
    } 
    // 子分支2：float32类型，非组操作，且步长>15（硬件对大步长有特殊优化）
    else if (filter_type.isF32() && out_type.isF32() &&
               lg_info.group_ops.size() == 1) {
      if (strideh_gt_15 || stridew_gt_15) {
        // 调用大步长专用的权重重排函数
        conv3d_stride_gt_15_weightreorder<float>(op);
      }
    } 
    // 子分支3：float16/bfloat16类型，且为组操作
    else if ((filter_type.isF16() || filter_type.isBF16()) &&
               lg_info.group_ops.size() > 1) {
      // 重排维度：[OC, IC, KT, KH, KW] → [1, OC, KT, IC/IC_PARALLEL, KH×KW×IC_PARALLEL]
      // （按硬件通道并行度IC_PARALLEL拆分IC，提升并行计算效率）
      auto filter_u16 = filter_op.read<uint16_t>();  // f16/bf16用16位存储
      std::vector<int64_t> filter_shape = {
          1, OC, KT, ceiling_func(IC, IC_PARALLEL), KH * KW * IC_PARALLEL};
      auto filter_new = std::make_shared<std::vector<uint16_t>>(
          get_shape_size(filter_shape), 0);
      // 遍历原始权重，按并行度拆分IC并复制数据
      for (int oc = 0; oc < OC; ++oc) {
        for (int ic = 0; ic < ceiling_func(IC, IC_PARALLEL); ++ic) {  // 按并行度分组的IC
          for (int kt = 0; kt < KT; ++kt) {
            for (int khw = 0; khw < KH * KW; ++khw) {  // 合并KH和KW为khw
              for (int inner = 0; inner < IC_PARALLEL; ++inner) {  // 并行度内索引
                // 跳过超出实际IC的无效索引
                if (ic * IC_PARALLEL + inner >= IC)
                  break;
                // 原始索引：OC→IC→KT→(KH×KW)
                long long src = oc * IC * KT * KH * KW +
                                (ic * IC_PARALLEL + inner) * KT * KH * KW +
                                kt * (KH * KW) + khw;
                // 新索引：OC→KT→(IC分组)→(KH×KW)→(并行度内)（适配硬件并行单元）
                long long dst = oc * KT * align_up(IC, IC_PARALLEL) * KH * KW +
                                kt * align_up(IC, IC_PARALLEL) * KH * KW +
                                ic * IC_PARALLEL * KH * KW + khw * IC_PARALLEL +
                                inner;
                filter_new->at(dst) = filter_u16->at(src);
              }
            }
          }
        }
      }
      // 更新权重数据、类型，并绑定到卷积操作
      filter_u16 = filter_new;
      auto filter_ranked_type =
          RankedTensorType::get(filter_shape, filter_type);
      auto new_filter = top::WeightOp::create(op, "postreordered", *filter_u16,
                                              filter_ranked_type);
      op->setOperand(1, new_filter);
    } 
    // 子分支4：float16/bfloat16类型，非组操作
    else if ((filter_type.isF16() || filter_type.isBF16()) &&
               lg_info.group_ops.size() == 1) {
      if (strideh_gt_15 || stridew_gt_15) {
        // 大步长场景：调用专用重排函数
        conv3d_stride_gt_15_weightreorder<uint16_t>(op);
      } else {
        // 非大步长：重排维度为[1, OC, (IC×KT)/IC_PARALLEL, KH×KW, IC_PARALLEL]
        auto filter_u16 = filter_op.read<uint16_t>();
        std::vector<int64_t> filter_shape = {
            1, OC, ceiling_func(IC * KT, IC_PARALLEL), KH * KW, IC_PARALLEL};
        auto filter_new = std::make_shared<std::vector<uint16_t>>(
            get_shape_size(filter_shape), 0);
        // 遍历原始权重，按IC×KT的并行度分组重排
        for (int oc = 0; oc < OC; ++oc) {
          for (int ic = 0; ic < ceiling_func(IC * KT, IC_PARALLEL); ++ic) {
            for (int khw = 0; khw < KH * KW; ++khw) {
              for (int inner = 0; inner < IC_PARALLEL; ++inner) {
                // 跳过超出IC×KT的无效索引
                if (ic * IC_PARALLEL + inner >= IC * KT)
                  break;
                // 原始索引：OC→(IC×KT)→(KH×KW)
                long long src = oc * IC * KT * KH * KW +
                                (ic * IC_PARALLEL + inner) * KH * KW + khw;
                // 新索引：OC→(IC×KT分组)→(KH×KW)→(并行度内)
                long long dst = oc * align_up(IC * KT, IC_PARALLEL) * KH * KW +
                                ic * IC_PARALLEL * KH * KW + khw * IC_PARALLEL +
                                inner;
                filter_new->at(dst) = filter_u16->at(src);
              }
            }
          }
        }
        // 更新权重数据、类型，并绑定到卷积操作
        filter_u16 = filter_new;
        auto filter_ranked_type =
            RankedTensorType::get(filter_shape, filter_type);
        auto new_filter = top::WeightOp::create(
            op, "postreordered", *filter_u16, filter_ranked_type);
        op->setOperand(1, new_filter);
      }
    } 
    // 子分支5：int8类型，且为组操作
    else if (filter_type.isInteger(8) && lg_info.group_ops.size() > 1) {
      // 重排维度：[OC, IC, KT, KH, KW] → [1, OC, KT, IC/IC_PARALLEL, KH×KW×IC_PARALLEL]
      auto filter_i8 = filter_op.read<int8_t>();
      std::vector<int64_t> filter_shape = {
          1, OC, KT, ceiling_func(IC, IC_PARALLEL), KH * KW * IC_PARALLEL};
      auto filter_new = std::make_shared<std::vector<int8_t>>(
          get_shape_size(filter_shape), 0);
      // 遍历原始权重，按并行度拆分IC并复制数据（逻辑同f16组操作）
      for (int oc = 0; oc < OC; ++oc) {
        for (int ic = 0; ic < ceiling_func(IC, IC_PARALLEL); ++ic) {
          for (int kt = 0; kt < KT; ++kt) {
            for (int khw = 0; khw < KH * KW; ++khw) {
              for (int inner = 0; inner < IC_PARALLEL; ++inner) {
                if (ic * IC_PARALLEL + inner >= IC)
                  break;
                long long src = oc * IC * KT * KH * KW +
                                (ic * IC_PARALLEL + inner) * KT * KH * KW +
                                kt * (KH * KW) + khw;
                long long dst = oc * KT * align_up(IC, IC_PARALLEL) * KH * KW +
                                kt * align_up(IC, IC_PARALLEL) * KH * KW +
                                ic * IC_PARALLEL * KH * KW + khw * IC_PARALLEL +
                                inner;
                filter_new->at(dst) = filter_i8->at(src);
              }
            }
          }
        }
      }
      // 更新权重数据、类型，并绑定到卷积操作
      filter_i8 = filter_new;
      auto filter_ranked_type =
          RankedTensorType::get(filter_shape, filter_type);
      auto new_filter = top::WeightOp::create(op, "postreordered", *filter_i8,
                                              filter_ranked_type);
      op->setOperand(1, new_filter);
    } 
    // 子分支6：int8类型，非组操作
    else if (filter_type.isInteger(8) && lg_info.group_ops.size() == 1) {
      if (strideh_gt_15 || stridew_gt_15) {
        // 暂不支持int8类型的大步长场景
        llvm_unreachable(
            "Currently conv3d uint8/int8 stride>15 is not supported.");
      } else {
        // 重排维度：[OC, IC, KT, KH, KW] → [1, OC, (IC×KT)/IC_PARALLEL, KH×KW, IC_PARALLEL]
        auto filter_i8 = filter_op.read<int8_t>();
        std::vector<int64_t> filter_shape = {
            1, OC, ceiling_func(IC * KT, IC_PARALLEL), KH * KW, IC_PARALLEL};
        auto filter_new = std::make_shared<std::vector<int8_t>>(
            get_shape_size(filter_shape), 0);
        // 遍历原始权重，按IC×KT的并行度分组重排（逻辑同f16非组操作）
        for (int oc = 0; oc < OC; ++oc) {
          for (int ic = 0; ic < ceiling_func(IC * KT, IC_PARALLEL); ++ic) {
            for (int khw = 0; khw < KH * KW; ++khw) {
              for (int inner = 0; inner < IC_PARALLEL; ++inner) {
                if (ic * IC_PARALLEL + inner >= IC * KT)
                  break;
                long long src = oc * IC * KT * KH * KW +
                                (ic * IC_PARALLEL + inner) * KH * KW + khw;
                long long dst = oc * align_up(IC * KT, IC_PARALLEL) * KH * KW +
                                ic * IC_PARALLEL * KH * KW + khw * IC_PARALLEL +
                                inner;
                filter_new->at(dst) = filter_i8->at(src);
              }
            }
          }
        }
        // 更新权重数据、类型，并绑定到卷积操作
        filter_i8 = filter_new;
        auto filter_ranked_type =
            RankedTensorType::get(filter_shape, filter_type);
        auto new_filter = top::WeightOp::create(op, "postreordered", *filter_i8,
                                                filter_ranked_type);
        op->setOperand(1, new_filter);
      }
    }
  }
  return;
}
```

3.

#### 核心功能

`nnvlc_transform` 是针对神经网络层组中特定操作（矩阵乘法 `MatMulOp` 和 2D 卷积 `Conv2DOp`）的权重数据进行 NNVLC 压缩编码 的转换函数。其核心作用是：对非 float32 类型（F16、BF16、INT8）的权重进行压缩，以减少内存占用或提升数据传输效率，并记录压缩相关参数，为后续推理时的解码提供依据。

#### 核心逻辑

函数的逻辑围绕 “层组筛选 → 操作类型判断 → 权重筛选 → 压缩编码 → 更新权重与属性” 展开，具体流程如下：

1. 层组筛选：仅处理包含 2 个及以上操作的层组（`lg_info.group_ops.size() >= 2`），单操作层组无需压缩优化。
2. 操作类型判断：遍历层组中的每个操作，仅处理两类操作：

   - 矩阵乘法（`tpu::MatMulOp`）：输出类型非 float32（需压缩的低 / 中精度场景）。
   - 2D 卷积（`tpu::Conv2DOp`）：未启用 `use_3ic_optimize` 优化，且输出类型非 float32。
3. 权重筛选：在目标操作中，筛选出符合条件的权重（`top::WeightOp`），其数据类型需为 F16、BF16 或 INT8（这些类型适合压缩且硬件支持）。
4. 压缩编码流程：

   - 读取权重数据，计算权重总大小（字节数）。
   - 调用 `getCompressParameter` 获取压缩所需参数（`bias0`、`bias1` 等，与数据类型相关）。
   - 调用 `nnvlc_encode` 执行压缩，返回压缩结果（是否成功、压缩后数据）。
   - 若压缩成功：用压缩后的数据创建新权重操作，替换原操作的权重输入，并设置压缩相关属性（如 `do_compress`、`bias0`）。
   - 若压缩失败：仅标记 `do_compress` 为假，不改变权重数据。

#### 核心原理

NNVLC（推测为 “Neural Network Variable Length Compression”）是一种针对神经网络权重的变长压缩算法，适用于低 / 中精度数据（F16、BF16、INT8）。其核心原理是：

- 数据类型适配：根据权重的数据类型（F16/BF16 为 16 位，INT8 为 8 位）调整压缩策略，利用低精度数据的分布特性（如存在大量重复值或小范围值）实现高效压缩。
- 参数辅助压缩：通过 `getCompressParameter` 计算的 `bias0`、`bias1` 等参数，优化压缩编码的动态范围或偏移量，提升压缩率。
- 无损 / 近无损压缩：确保压缩后的数据在推理时可准确解码，不影响模型精度（从代码中保留原始形状和类型可推测）。
- 硬件友好性：压缩后的数据仍保持与硬件兼容的格式，配合属性标记（如 `do_compress`），可在推理时由硬件快速解码使用。

### 3.nnvlc_transform

#### 核心功能

`nnvlc_transform` 是针对神经网络层组中特定操作（矩阵乘法 `MatMulOp` 和 2D 卷积 `Conv2DOp`）的权重数据进行 NNVLC 压缩编码 的转换函数。其核心作用是：对非 float32 类型（F16、BF16、INT8）的权重进行压缩，以减少内存占用或提升数据传输效率，并记录压缩相关参数，为后续推理时的解码提供依据。

#### 核心逻辑

函数的逻辑围绕 “层组筛选 → 操作类型判断 → 权重筛选 → 压缩编码 → 更新权重与属性” 展开，具体流程如下：

1. 层组筛选：仅处理包含 2 个及以上操作的层组（`lg_info.group_ops.size() >= 2`），单操作层组无需压缩优化。
2. 操作类型判断：遍历层组中的每个操作，仅处理两类操作：

   - 矩阵乘法（`tpu::MatMulOp`）：输出类型非 float32（需压缩的低 / 中精度场景）。
   - 2D 卷积（`tpu::Conv2DOp`）：未启用 `use_3ic_optimize` 优化，且输出类型非 float32。
3. 权重筛选：在目标操作中，筛选出符合条件的权重（`top::WeightOp`），其数据类型需为 F16、BF16 或 INT8（这些类型适合压缩且硬件支持）。
4. 压缩编码流程：

   - 读取权重数据，计算权重总大小（字节数）。
   - 调用 `getCompressParameter` 获取压缩所需参数（`bias0`、`bias1` 等，与数据类型相关）。
   - 调用 `nnvlc_encode` 执行压缩，返回压缩结果（是否成功、压缩后数据）。
   - 若压缩成功：用压缩后的数据创建新权重操作，替换原操作的权重输入，并设置压缩相关属性（如 `do_compress`、`bias0`）。
   - 若压缩失败：仅标记 `do_compress` 为假，不改变权重数据。

#### 核心原理

NNVLC（推测为 “Neural Network Variable Length Compression”）是一种针对神经网络权重的变长压缩算法，适用于低 / 中精度数据（F16、BF16、INT8）。其核心原理是：

- 数据类型适配：根据权重的数据类型（F16/BF16 为 16 位，INT8 为 8 位）调整压缩策略，利用低精度数据的分布特性（如存在大量重复值或小范围值）实现高效压缩。
- 参数辅助压缩：通过 `getCompressParameter` 计算的 `bias0`、`bias1` 等参数，优化压缩编码的动态范围或偏移量，提升压缩率。
- 无损 / 近无损压缩：确保压缩后的数据在推理时可准确解码，不影响模型精度（从代码中保留原始形状和类型可推测）。
- 硬件友好性：压缩后的数据仍保持与硬件兼容的格式，配合属性标记（如 `do_compress`），可在推理时由硬件快速解码使用。

#### nnvlc_transform 代码

```java
/**
 * @brief 对层组中的矩阵乘法和2D卷积操作的权重进行NNVLC压缩编码
 * 
 * 功能：针对层组（LgInfo）中包含的矩阵乘法（MatMulOp）和2D卷积（Conv2DOp），
 * 对其非float32类型（F16、BF16、INT8）的权重进行NNVLC压缩，减少内存占用，
 * 并记录压缩参数，为后续推理解码提供依据。
 * 
 * @param lg_info 层组信息，包含一组相关的神经网络操作
 */
static void nnvlc_transform(const LgInfo &lg_info) {
  // 仅处理包含2个及以上操作的层组（单操作层组无需压缩优化）
  if (lg_info.group_ops.size() < 2)
    return;

  // 遍历层组中的每个操作
  for (auto op : lg_info.group_ops) {
    // 分支1：处理矩阵乘法操作（MatMulOp）
    if (isa<tpu::MatMulOp>(op) &&  // 判断是否为矩阵乘法操作
        !module::getStorageType(op->getResult(0)).isF32()) {  // 输出类型非float32（需压缩）
      
      // 遍历矩阵乘法操作的所有输入操作数
      for (int idx = 0, sz = op->getOperands().size(); idx < sz; idx++) {
        // 筛选符合条件的权重：是权重操作（WeightOp），且数据类型为F16、BF16或INT8
        if (isa<top::WeightOp>(op->getOperand(idx).getDefiningOp()) &&
            (module::getStorageType(op->getOperand(idx)).isF16() ||
             module::getStorageType(op->getOperand(idx)).isBF16() ||
             module::getStorageType(op->getOperand(idx)).isInteger(8))) {
          
          // 获取权重操作及其类型、形状信息
          auto right_op = op->getOperand(idx).getDefiningOp<top::WeightOp>();
          auto right_type = module::getStorageType(op->getOperand(idx));
          auto right_shape = right_op.getType().getShape();

          // 初始化压缩参数：
          uint8_t bias0 = right_type.isBF16() ? 127 : 0;  // BF16的bias0固定为127，其他为0
          uint8_t bias1;  // 由getCompressParameter计算的压缩偏移参数
          // zero_guard：INT8为0（无需零保护），F16/BF16为1（需要零保护）
          bool zero_guard = right_type.isInteger(8) ? 0 : 1;
          // is_signed：BF16为0（无符号处理），其他为1（有符号处理）
          bool is_signed = right_type.isBF16() ? 0 : 1;

          // 子分支1.1：处理F16或BF16类型权重（16位数据）
          if (right_type.isF16() || right_type.isBF16()) {
            // 读取16位权重数据（F16/BF16均以uint16_t存储）
            auto right_u16 = right_op.read<uint16_t>();
            // 计算权重总字节数：形状乘积 × 2（每个元素2字节）
            size_t length = right_shape.size();
            int64_t weight_size = 2;  // 初始化为2（每个元素2字节）
            for (size_t i = 0; i < length; ++i) {
              weight_size *= right_shape[i];
            }
            int32_t osize;  // 压缩后的数据大小（输出）

            // 获取压缩所需的参数（计算bias1等）
            getCompressParameter(reinterpret_cast<uint8_t *>(right_u16->data()),
                                 weight_size, is_signed, zero_guard, right_type,
                                 bias0, bias1);
            
            // 执行NNVLC压缩编码
            auto nnvlc_results = nnvlc_encode(
                reinterpret_cast<uint8_t *>(right_u16->data()), weight_size,
                right_type, bias0, bias1, is_signed, zero_guard, osize);
            bool do_compress = std::get<0>(nnvlc_results);  // 压缩是否成功

            if (do_compress) {  // 压缩成功：更新权重和属性
              uint8_t *obuf = std::get<1>(nnvlc_results);  // 压缩后的数据缓冲区
              // 创建与原始形状、类型一致的新权重类型
              auto new_type = RankedTensorType::get(right_shape, right_type);
              // 将压缩数据复制到新的16位向量中（osize字节 → osize/2个16位元素）
              auto data = std::vector<uint16_t>(osize / 2);
              memcpy(data.data(), obuf, osize);
              delete[] obuf;  // 释放临时缓冲区

              // 创建新的权重操作，替换原操作数
              auto new_op =
                  top::WeightOp::create(op, "right_nnvlc", data, new_type);
              op->setOperand(idx, new_op);

              // 设置压缩相关属性（供后续解码使用）
              auto ctx = op->getContext();
              auto builder = OpBuilder(ctx);
              auto rightOp_new =
                  op->getOperand(idx).getDefiningOp<top::WeightOp>();
              rightOp_new->setAttr("do_compress", builder.getBoolAttr(do_compress));  // 标记已压缩
              rightOp_new->setAttr("bias0", builder.getI64IntegerAttr((uint64_t)bias0));  // 压缩参数bias0
              rightOp_new->setAttr("bias1", builder.getI64IntegerAttr((uint64_t)bias1));  // 压缩参数bias1
              rightOp_new->setAttr("is_signed", builder.getBoolAttr(is_signed));  // 是否有符号
              rightOp_new->setAttr("zero_guard", builder.getBoolAttr(zero_guard));  // 是否零保护
            } else {  // 压缩失败：仅标记未压缩
              auto ctx = op->getContext();
              auto builder = OpBuilder(ctx);
              auto rightOp_new =
                  op->getOperand(idx).getDefiningOp<top::WeightOp>();
              rightOp_new->setAttr("do_compress", builder.getBoolAttr(do_compress));
            }
          } 
          // 子分支1.2：处理INT8类型权重（8位数据）
          else if (right_type.isInteger(8)) {
            // 读取8位权重数据（int8_t）
            auto filter_i8 = right_op.read<int8_t>();
            // 计算权重总字节数：形状乘积 × 1（每个元素1字节）
            size_t length = right_shape.size();
            int64_t weight_size = 1;  // 初始化为1（每个元素1字节）
            for (size_t i = 0; i < length; ++i) {
              weight_size *= right_shape[i];
            }
            int32_t osize;  // 压缩后的数据大小（输出）

            // 获取压缩所需的参数
            getCompressParameter(reinterpret_cast<uint8_t *>(filter_i8->data()),
                                 weight_size, is_signed, zero_guard, right_type,
                                 bias0, bias1);
            
            // 执行NNVLC压缩编码
            auto nnvlc_results = nnvlc_encode(
                reinterpret_cast<uint8_t *>(filter_i8->data()), weight_size,
                right_type, bias0, bias1, is_signed, zero_guard, osize);
            bool do_compress = std::get<0>(nnvlc_results);  // 压缩是否成功

            if (do_compress) {  // 压缩成功：更新权重和属性
              uint8_t *obuf = std::get<1>(nnvlc_results);  // 压缩后的数据缓冲区
              // 创建与原始形状、类型一致的新权重类型
              auto new_type = RankedTensorType::get(right_shape, right_type);
              // 将压缩数据复制到新的8位向量中
              auto data = std::vector<int8_t>(osize);
              memcpy(data.data(), obuf, osize);
              delete[] obuf;  // 释放临时缓冲区

              // 创建新的权重操作，替换原操作数
              auto new_op =
                  top::WeightOp::create(op, "right_nnvlc", data, new_type);
              op->setOperand(idx, new_op);

              // 设置压缩相关属性
              auto ctx = op->getContext();
              auto builder = OpBuilder(ctx);
              auto rightOp_new =
                  op->getOperand(idx).getDefiningOp<top::WeightOp>();
              rightOp_new->setAttr("do_compress", builder.getBoolAttr(do_compress));
              rightOp_new->setAttr("bias0", builder.getI64IntegerAttr((uint64_t)bias0));
              rightOp_new->setAttr("bias1", builder.getI64IntegerAttr((uint64_t)bias1));
              rightOp_new->setAttr("is_signed", builder.getBoolAttr(is_signed));
              rightOp_new->setAttr("zero_guard", builder.getBoolAttr(zero_guard));
            } else {  // 压缩失败：仅标记未压缩
              auto ctx = op->getContext();
              auto builder = OpBuilder(ctx);
              auto rightOp_new =
                  op->getOperand(idx).getDefiningOp<top::WeightOp>();
              rightOp_new->setAttr("do_compress", builder.getBoolAttr(do_compress));
            }
          }
        }
      }
    }

    // 分支2：处理2D卷积操作（Conv2DOp）
    if (isa<tpu::Conv2DOp>(op) &&  // 判断是否为2D卷积操作
        op->getAttr("use_3ic_optimize").cast<IntegerAttr>().getInt() == 0 &&  // 未启用3ic优化
        !module::getStorageType(op->getResult(0)).isF32()) {  // 输出类型非float32（需压缩）
      
      uint32_t idx;  // 权重操作数的索引（0或1）
      // 确定权重操作数的索引：优先检查索引1，再检查索引0
      if (isa<top::WeightOp>(op->getOperand(1).getDefiningOp()) &&
          !module::getStorageType(op->getOperand(1)).isF32()) {
        idx = 1;
      } else if (isa<top::WeightOp>(op->getOperand(0).getDefiningOp()) &&
                 !module::getStorageType(op->getOperand(0)).isF32()) {
        idx = 0;
      }

      // 获取权重操作及其类型、形状信息
      auto filter_op = op->getOperand(idx).getDefiningOp<top::WeightOp>();
      auto filter_type = module::getStorageType(op->getOperand(idx));
      auto filter_shape = filter_op.getType().getShape();

      // 初始化压缩参数（同MatMulOp逻辑）
      uint8_t bias0 = filter_type.isBF16() ? 127 : 0;
      uint8_t bias1;
      bool zero_guard = filter_type.isInteger(8) ? 0 : 1;
      bool is_signed = filter_type.isBF16() ? 0 : 1;

      // 子分支2.1：处理F16或BF16类型权重（16位数据）
      if (filter_type.isF16() || filter_type.isBF16()) {
        // 读取16位权重数据
        auto filter_u16 = filter_op.read<uint16_t>();
        // 计算权重总字节数：卷积核形状乘积 × 2（每个元素2字节）
        int32_t weight_size = filter_shape[0] * filter_shape[1] *
                              filter_shape[2] * filter_shape[3] * 2;
        int32_t osize;  // 压缩后的数据大小

        // 获取压缩参数
        getCompressParameter(reinterpret_cast<uint8_t *>(filter_u16->data()),
                             weight_size, is_signed, zero_guard, filter_type,
                             bias0, bias1);
        
        // 执行NNVLC压缩编码
        auto nnvlc_results = nnvlc_encode(
            reinterpret_cast<uint8_t *>(filter_u16->data()), weight_size,
            filter_type, bias0, bias1, is_signed, zero_guard, osize);
        bool do_compress = std::get<0>(nnvlc_results);

        if (do_compress) {  // 压缩成功：更新权重和属性
          uint8_t *obuf = std::get<1>(nnvlc_results);
          auto new_type = RankedTensorType::get(filter_shape, filter_type);
          auto data = std::vector<uint16_t>(osize / 2);  // 转换为16位向量
          memcpy(data.data(), obuf, osize);
          delete[] obuf;

          // 创建新权重操作并替换原操作数
          auto new_op =
              top::WeightOp::create(op, "filter_nnvlc", data, new_type);
          op->setOperand(idx, new_op);

          // 设置压缩相关属性
          auto ctx = op->getContext();
          auto builder = OpBuilder(ctx);
          auto filterOp_new =
              op->getOperand(idx).getDefiningOp<top::WeightOp>();
          filterOp_new->setAttr("do_compress", builder.getBoolAttr(do_compress));
          filterOp_new->setAttr("bias0", builder.getI64IntegerAttr((uint64_t)bias0));
          filterOp_new->setAttr("bias1", builder.getI64IntegerAttr((uint64_t)bias1));
          filterOp_new->setAttr("is_signed", builder.getBoolAttr(is_signed));
          filterOp_new->setAttr("zero_guard", builder.getBoolAttr(zero_guard));
        } else {  // 压缩失败：标记未压缩
          auto ctx = op->getContext();
          auto builder = OpBuilder(ctx);
          auto filterOp_new =
              op->getOperand(idx).getDefiningOp<top::WeightOp>();
          filterOp_new->setAttr("do_compress", builder.getBoolAttr(do_compress));
        }
      } 
      // 子分支2.2：处理INT8类型权重（8位数据）
      else if (filter_type.isInteger(8)) {
        // 读取8位权重数据
        auto filter_i8 = filter_op.read<int8_t>();
        // 计算权重总字节数：卷积核形状乘积 × 1（每个元素1字节）
        int32_t weight_size = filter_shape[0] * filter_shape[1] *
                              filter_shape[2] * filter_shape[3];
        int32_t osize;  // 压缩后的数据大小

        // 获取压缩参数
        getCompressParameter(reinterpret_cast<uint8_t *>(filter_i8->data()),
                             weight_size, is_signed, zero_guard, filter_type,
                             bias0, bias1);
        
        // 执行NNVLC压缩编码
        auto nnvlc_results = nnvlc_encode(
            reinterpret_cast<uint8_t *>(filter_i8->data()), weight_size,
            filter_type, bias0, bias1, true, zero_guard, osize);
        bool do_compress = std::get<0>(nnvlc_results);

        if (do_compress) {  // 压缩成功：更新权重和属性
          uint8_t *obuf = std::get<1>(nnvlc_results);
          auto new_type = RankedTensorType::get(filter_shape, filter_type);
          auto data = std::vector<int8_t>(osize);  // 转换为8位向量
          memcpy(data.data(), obuf, osize);
          delete[] obuf;

          // 创建新权重操作并替换原操作数
          auto new_op =
              top::WeightOp::create(op, "filter_reorderd", data, new_type);
          op->setOperand(idx, new_op);

          // 设置压缩相关属性
          auto ctx = op->getContext();
          auto builder = OpBuilder(ctx);
          auto filterOp_new =
              op->getOperand(idx).getDefiningOp<top::WeightOp>();
          filterOp_new->setAttr("do_compress", builder.getBoolAttr(do_compress));
          filterOp_new->setAttr("bias0", builder.getI64IntegerAttr((uint64_t)bias0));
          filterOp_new->setAttr("bias1", builder.getI64IntegerAttr((uint64_t)bias1));
          filterOp_new->setAttr("is_signed", builder.getBoolAttr(is_signed));
          filterOp_new->setAttr("zero_guard", builder.getBoolAttr(zero_guard));
        } else {  // 压缩失败：标记未压缩
          auto ctx = op->getContext();
          auto builder = OpBuilder(ctx);
          auto filterOp_new =
              op->getOperand(idx).getDefiningOp<top::WeightOp>();
          filterOp_new->setAttr("do_compress", builder.getBoolAttr(do_compress));
        }
      }
    }
  }
}
```

### 4.class GroupPostTransformPass : public LgPass

#### 核心功能

`GroupPostTransformPass` 是一个层组后处理转换的优化_pass_，属于深度学习模型部署中的中间表示（IR）优化阶段。其核心作用是：在层组（Layer Group）结构确定后，针对特定硬件平台（如 BM1684、BM1684X、BM1690、BM1688 等），对层组内的操作执行一系列硬件适配的后处理转换，包括 3D 相关操作优化、矩阵乘法复用设置、权重压缩（NNVLC）等，最终提升模型在目标硬件上的推理效率。

#### 核心逻辑

该类的逻辑围绕 “硬件适配” 和 “层组后处理” 展开，具体流程如下：

1. 硬件兼容性判断：仅对 BM1684 系列、BM1684X 系列、BM1690 系列硬件执行后处理（这些硬件有特定的内存布局和计算单元需求）。
2. 层组遍历处理：遍历所有层组信息（`lg_infos`），对每个层组依次执行三项核心后处理：

   - `_3D_group_post_transform`：针对 3D 操作（如 3D 卷积）的层组优化（如权重重排、维度适配）。
   - `matmul_left_reuse_setting`：矩阵乘法左操作数的复用设置（通过复用减少内存访问，提升计算效率）。
   - `nnvlc_transform`：仅在 BM1688 硬件且启用 NNVLC 压缩模式时，对层组内权重执行压缩（减少内存占用）。
3. _pass_标识与描述：通过 `name()` 和 `brief()` 方法提供_pass_的名称和功能描述，便于调试和日志记录。

#### 核心原理

在深度学习模型编译流程中，“层组” 是将具有依赖关系或可并行执行的操作聚合而成的单元，便于硬件进行批处理和资源调度。层组确定后，`GroupPostTransformPass` 作为后续优化_pass_，其原理是：

- 硬件感知优化：不同硬件的计算架构（如并行度、内存带宽、支持的数据类型）不同，需针对性调整操作布局（如 3D 操作的维度顺序）、复用策略（如矩阵乘法的 operand 复用）和存储方式（如 NNVLC 压缩）。
- 阶段适配：层组结构确定后，操作间的依赖和资源分配已明确，此时执行后处理可避免层组结构变动导致的优化失效，确保优化与最终执行计划一致。
- 组合优化：通过串联多个专项优化（3D 操作、矩阵乘法复用、权重压缩），从多个维度（计算效率、内存利用、带宽压力）提升模型性能，形成协同效应。

#### 4.class GroupPostTransformPass : public LgPass 代码

```cpp
/**
 * @brief 层组后处理转换_pass_，用于层组结构确定后执行硬件适配的优化
 * 
 * 该_pass_属于模型编译的中间表示（IR）优化阶段，针对特定BM系列硬件，
 * 对层组内的操作执行3D相关优化、矩阵乘法复用设置、权重压缩等后处理，
 * 最终提升模型在目标硬件上的推理效率。
 */
class GroupPostTransformPass : public LgPass {
public:
  /**
   * @brief 构造函数，初始化_pass_的配置选项
   * @param options 层组_pass_的配置选项（如NNVLC压缩模式等）
   */
  GroupPostTransformPass(const LgOptions &options) { options_ = options; }

  /**
   * @brief 执行_pass_的核心逻辑，对层组执行后处理转换
   * @param pass_ir 包含层组信息的IR对象（存储所有层组数据）
   * @return 始终返回true（表示_pass_执行成功）
   */
  virtual bool run(LgPassIR *pass_ir) override {
    // 仅对BM1684/1684X/1690系列硬件执行后处理（这些硬件需要特定优化）
    if (module::isBM1684XFamily() || module::isBM1684Family() ||
        module::isBM1690Family()) {
      // 遍历所有层组，对每个层组执行后处理
      for (size_t i = 0; i < pass_ir->lg_infos.size(); ++i) {
        // 1. 对3D操作（如3D卷积）的层组执行后处理（如权重重排、维度适配）
        _3D_group_post_transform(pass_ir->lg_infos[i]);
        // 2. 对矩阵乘法的左操作数设置复用策略（减少内存访问，提升效率）
        matmul_left_reuse_setting(pass_ir->lg_infos[i]);
        // 3. 仅在BM1688硬件且启用NNVLC权重压缩/全压缩模式时，执行权重压缩
        if (module::isBM1688() && (options_.nnvlc_mode == NnvlcMode::WEIGHT ||
                                   options_.nnvlc_mode == NnvlcMode::ALL)) {
          nnvlc_transform(pass_ir->lg_infos[i]);
        }
      }
    }
    return true;  // 表示_pass_执行成功
  }

  /**
   * @brief 返回_pass_的名称
   * @return _pass_名称字符串
   */
  virtual std::string name() override { return "GroupPostTransformPass"; }

  /**
   * @brief 返回_pass_的简要描述
   * @return _pass_功能描述字符串
   */
  virtual std::string brief() override {
    return "Some transform after layer groups is determined";
  }
};
```

## 10./LayerGroup/TimeStepCombine.cpp

### class TimeStepCombinePass

#### 核心功能

`TimeStepCombinePass` 是一个针对时间步组合优化的层组_pass_，主要用于处理包含时间维度的深度学习模型（如循环神经网络 RNN、3D 卷积网络等）。其核心作用是：通过合并模型中的时间步（Time Step），优化时间维度上的计算并行性，实现硬件资源的更均衡利用，最终提升模型在时间序列数据上的推理效率。

#### 核心逻辑

该类的逻辑围绕 “时间步合并” 这一核心操作展开，具体流程如下：

1. 初始化配置：通过构造函数接收层组_pass_的配置选项（`LgOptions`），为时间步合并提供参数依据（如合并策略、并行度限制等）。
2. 执行时间步合并：在 `run` 方法中，调用核心函数 `timestep_combine`，传入层组信息（`lg_infos`）、时间步数据（`time_steps`）、形状分段信息（`shape_secs`）和配置选项（`options_`），完成时间步的合并操作。
3. 返回_pass_标识：通过 `name()` 和 `brief()` 方法提供_pass_的名称和功能描述，用于日志记录和调试。

#### 核心原理

在处理时间序列数据的模型（如视频帧序列、时序信号）中，计算通常按时间步（如每一帧、每一个时间点）依次执行，存在天然的串行性。`TimeStepCombinePass` 的优化原理是：

- 并行性挖掘：将多个连续的时间步合并为一个批次处理单元，使硬件的并行计算单元（如多核 NPU）能同时处理多个时间步的计算，打破时间维度的串行瓶颈。
- 负载均衡：通过合理合并时间步，避免硬件资源在不同时间步上的负载不均（如某些时间步计算密集，某些空闲），提升资源利用率。
- 适配硬件特性：硬件的并行能力（如一次可处理的最大批次）是固定的，合并时间步可使计算任务更匹配硬件的批次处理能力，减少调度开销。

#### class TimeStepCombinePass 代码

```cpp
/// 用于时间步合并的优化_pass_
class TimeStepCombinePass : public LgPass {
public:
  /**
   * @brief 构造函数，初始化时间步合并_pass_的配置选项
   * @param options 层组_pass_的配置选项（如合并策略、并行度参数等）
   */
  TimeStepCombinePass(const LgOptions &options) { options_ = options; }

  /**
   * @brief 执行_pass_的核心逻辑，完成时间步的合并优化
   * @param pass_ir 包含层组信息、时间步数据等的IR对象
   * @return 始终返回true（表示_pass_执行成功）
   * 
   * 核心操作：调用timestep_combine函数，基于层组信息、原始时间步数据、
   * 形状分段信息和配置选项，对时间步进行合并，以提升并行计算效率。
   */
  virtual bool run(LgPassIR *pass_ir) override {
    timestep_combine(
      pass_ir->lg_infos,    // 层组信息：包含待优化的层组结构
      pass_ir->time_steps,  // 原始时间步数据：记录各时间步的计算任务
      pass_ir->shape_secs,  // 形状分段信息：时间步数据的形状与分段规则
      options_              // 配置选项：指导合并策略（如合并数量、并行度限制）
    );
    return true;  // 表示时间步合并_pass_执行成功
  }

  /**
   * @brief 返回_pass_的名称
   * @return _pass_名称字符串，用于标识_pass_
   */
  virtual std::string name() override { return "TimeStepCombinePass"; }

  /**
   * @brief 返回_pass_的简要描述
   * @return _pass_功能描述，说明其核心作用是合并时间步以优化并行均衡性
   */
  virtual std::string brief() override {
    return "Combine time step for better parallel balance";
  }
};
```

### 2.timestep_combine

#### 核心功能

`timestep_combine` 是 `TimeStepCombinePass` 优化_pass_的核心实现函数，用于对模型中的时间步（Time Step）进行合并处理。其核心作用是：遍历所有层组（Layer Group），调用内存感知的时间步合并函数（`memory_aware_timestep_combine`），在考虑硬件内存限制的前提下，将多个时间步合并为更高效的计算单元，最终提升时间序列模型（如视频处理、时序预测模型）的并行计算效率。

#### 核心逻辑

函数的逻辑围绕 “参数校验 → 层组遍历 → 内存感知合并” 展开，具体流程如下：

1. 参数校验：通过断言（`assert`）验证输入参数的一致性 —— 层组信息（`lg_infos`）、时间步数据（`time_steps`）、形状分段信息（`shape_secs`）的数量必须相等，确保每一层组都有对应的时间步和形状信息，避免索引错乱。
2. 层组遍历：循环遍历每一层组，为每个层组调用 `memory_aware_timestep_combine` 函数，传入该层组的具体信息（层组数据、对应的时间步、形状分段、索引、配置选项）。
3. 委托合并逻辑：将时间步合并的具体实现委托给 `memory_aware_timestep_combine`，该函数会结合硬件内存限制（如内存带宽、缓存大小）和层组特性（如计算量、数据依赖），决定如何合并时间步（如合并数量、合并策略），以实现并行负载均衡和内存高效利用。

#### 核心原理

时间步合并的核心需求是打破时间序列计算的 “串行性”（按时间步依次计算），通过合并多个时间步实现 “并行计算”。而 `timestep_combine` 作为入口函数，其原理是：

- 一致性保障：通过参数校验确保层组、时间步、形状信息一一对应，为后续合并提供数据一致性基础。
- 分层处理：不同层组的计算特性（如计算量、内存占用）可能不同，因此按层组分别处理，避免 “一刀切” 的合并策略导致部分层组效率下降。
- 内存感知：委托给 `memory_aware_timestep_combine` 处理，确保合并过程考虑硬件内存约束（如合并后的批次大小不超过内存容量），避免因内存不足导致的计算中断或性能损失。

#### timestep_combine 代码

```cpp
/**
 * @brief 时间步合并的入口函数，负责遍历层组并调用内存感知的合并逻辑
 * 
 * 功能：对每一层组对应的时间步进行合并，通过调用memory_aware_timestep_combine，
 * 在保障数据一致性的前提下，结合内存限制实现高效的时间步合并，提升并行计算效率。
 * 
 * @param lg_infos 所有层组的信息集合（每个元素对应一个层组的详细数据）
 * @param time_steps 所有时间步的集合（每个元素对应一个层组的时间步数据）
 * @param shape_secs 所有形状分段的集合（每个元素对应一个层组的形状与分段规则）
 * @param options 层组配置选项（如合并策略、内存限制参数等）
 */
static void timestep_combine(const std::vector<LgInfo> &lg_infos,
                             std::vector<BasicTimeStepPtr> &time_steps,
                             const std::vector<shape_secs_t> &shape_secs,
                             const LgOptions &options) {
  // 断言1：层组数量必须与时间步数量相等（每一层组对应一组时间步）
  assert(lg_infos.size() == time_steps.size());
  // 断言2：层组数量必须与形状分段数量相等（每一层组对应一组形状信息）
  assert(lg_infos.size() == shape_secs.size());

  // 遍历每一层组，对其对应的时间步执行合并
  for (size_t i = 0; i < lg_infos.size(); ++i) {
    // 调用内存感知的时间步合并函数，处理第i个层组
    // 参数说明：
    // - lg_infos[i]：第i个层组的详细信息
    // - time_steps[i]：第i个层组对应的时间步数据
    // - shape_secs[i]：第i个层组的形状分段信息
    // - i：当前层组的索引（用于标识和日志）
    // - options：配置选项（指导合并策略）
    memory_aware_timestep_combine(lg_infos[i], time_steps[i], shape_secs[i], i,
                                  options);
  }
}
```

### 3.memory_aware_timestep_combine

#### 核心功能

`memory_aware_timestep_combine` 是内存感知的时间步合并实现函数，用于在考虑硬件内存限制（如本地内存 LMEM、L2 缓存）的前提下，对包含多个操作的层组（Layer Group）进行时间步（Time Step）的重分配与合并。其核心作用是：通过分析层组内的计算周期（TPU 层计算）、数据传输周期（GDMA 张量传输）和内存占用，优化时间步的分配与合并策略，在避免内存溢出的同时，提升时间维度的并行计算效率，平衡硬件负载。

#### 核心逻辑

函数的逻辑围绕 “层组筛选 → 时间步信息收集 → 内存与周期信息整合 → 时间步重分配 → 时间步合并” 展开，具体流程如下：

1. 层组筛选：若层组仅包含 1 个操作（`lg_info.group_ops.size() == 1`），直接返回（单操作层组无并行合并收益，无需处理）。
2. 时间步信息收集：从时间步表（`timestep_table`）中提取 TPU 层的时间步字段（`tpu0_ts_field`）和 GDMA 张量的时间步字段（`gdma0_ts_field`），分别存入 `ts_layers_v` 和 `ts_tensors_v`，为后续分析提供原始时间步数据。
3. 周期与内存信息整合：

   - 调用 `update_cycle_info` 计算并更新每个时间步的总 GDMA 传输周期（`total_gdma_cycle_v`）和总 TPU 层计算周期（`total_layer_cycle_v`），评估时间步的计算与传输负载。
   - 构建 `MemBuff` 对象，整合本地内存（LMEM）和 L2 缓存的缓冲区信息（`time_step->get_lmem_buffer()` 和 `time_step->get_l2mem_buffer()`），实现 “内存感知”—— 确保合并后的时间步不超出硬件内存容量。
4. 时间步重分配（条件执行）：若层组操作数 ≤100（操作数较少时），调用 `reassign_timestep_tensors` 对时间步进行精细重分配。这一步骤虽可能增加编译时间，但能为后续合并提供更优的初始分配（操作数过多时编译成本过高，故跳过）。
5. 时间步合并：无论是否重分配，最终调用 `merge_timesteps` 执行时间步合并，结合收集的时间步字段、周期信息和内存缓冲区数据，生成优化后的合并时间步，平衡并行负载与内存使用。

#### 核心原理

该函数的核心原理是 “内存约束下的并行负载均衡”，针对时间序列模型的两个关键瓶颈设计：

- 内存瓶颈：硬件的本地内存（LMEM）和 L2 缓存容量有限，若合并后的时间步对应的张量数据超出内存容量，会导致频繁的内存交换（swap），严重降低性能。通过 `MemBuff` 整合内存信息，确保合并后的时间步内存占用在硬件限制内。
- 负载不均衡瓶颈：时间步的计算（TPU 层）和数据传输（GDMA）耗时可能差异较大，导致硬件资源（计算单元、DMA 控制器）空闲。通过 `total_layer_cycle_v` 和 `total_gdma_cycle_v` 评估负载，合并时间步时平衡各时间步的总耗时，提升硬件利用率。
- 操作数适配：对操作数少的层组执行精细重分配（`reassign_timestep_tensors`），在编译时间可接受的范围内优化初始分配；对操作数多的层组直接合并，避免编译成本过高。

#### 4.memory_aware_timestep_combine 代码

```cpp
/**
 * @brief 内存感知的时间步合并实现函数，在考虑内存限制的前提下优化时间步分配与合并
 * 
 * 功能：针对包含多个操作的层组，收集时间步的计算/传输周期信息和内存占用，
 * 先进行时间步重分配（操作数较少时），再执行合并，平衡并行负载与内存使用，
 * 提升时间序列模型的推理效率。
 * 
 * @param lg_info 层组信息（包含层组内的操作集合）
 * @param time_step 时间步指针（存储当前层组的时间步数据）
 * @param shape_secs 形状分段信息（时间步对应张量的形状与分段规则）
 * @param group_idx 层组索引（用于标识和日志）
 * @param options 层组配置选项（如合并策略、内存限制参数等）
 */
static void memory_aware_timestep_combine(const LgInfo &lg_info,
                                          BasicTimeStepPtr &time_step,
                                          const shape_secs_t &shape_secs,
                                          int64_t group_idx,
                                          const LgOptions &options) {
  // 若层组仅包含1个操作，无需合并时间步（单操作无并行收益）
  if (lg_info.group_ops.size() == 1) {
    return;
  }

  // 收集时间步相关字段：
  // ts_layers_v：存储TPU层的时间步字段（记录层计算的时间步信息）
  // ts_tensors_v：存储GDMA张量的时间步字段（记录张量传输的时间步信息）
  std::vector<TpuTsField> ts_layers_v;
  std::vector<GdmaTsField> ts_tensors_v;
  const auto &timestep_table = time_step->get_timestep_table();  // 获取时间步表
  for (auto &row : timestep_table) {
    ts_layers_v.push_back(row.tpu0_ts_field);  // 提取TPU层时间步字段
    ts_tensors_v.push_back(row.gdma0_ts_field);  // 提取GDMA张量时间步字段
  }

  // 收集周期信息：
  // total_gdma_cycle_v：每个时间步的总GDMA传输周期（数据传输耗时）
  // total_layer_cycle_v：每个时间步的总TPU层计算周期（计算耗时）
  std::vector<int64_t> total_gdma_cycle_v;
  std::vector<int64_t> total_layer_cycle_v;
  update_cycle_info(total_gdma_cycle_v, total_layer_cycle_v, time_step,
                    shape_secs);  // 更新周期信息

  // 内存缓冲区整合：合并本地内存（LMEM）和L2缓存的缓冲区信息，实现"内存感知"
  bool print_log = true;  // 启用日志打印（用于调试和分析）
  MemBuff mem_buffer(time_step->get_lmem_buffer());  // 初始化本地内存缓冲区
  mem_buffer.insert(time_step->get_l2mem_buffer().begin(),
                    time_step->get_l2mem_buffer().end());  // 插入L2缓存缓冲区

  // 时间步重分配（条件执行）：
  // 仅对操作数≤100的层组执行（操作数少，精细重分配的编译成本可接受）
  // 作用：优化时间步的初始分配，为后续合并提供更均衡的基础
  if (lg_info.group_ops.size() <= 100) {
    reassign_timestep_tensors(lg_info, time_step, shape_secs, ts_layers_v,
                              ts_tensors_v, total_layer_cycle_v,
                              total_gdma_cycle_v, mem_buffer, print_log,
                              group_idx, options);
  }

  // 执行时间步合并：
  // 结合时间步字段、周期信息和内存缓冲区，生成优化后的合并时间步
  // 确保合并后不超出内存限制，且平衡计算与传输负载
  merge_timesteps(lg_info, time_step, shape_secs, ts_layers_v, ts_tensors_v,
                  total_layer_cycle_v, total_gdma_cycle_v, mem_buffer,
                  print_log, group_idx, options);
}
```

### 4.reassign_timestep_tensors

#### 核心功能

`reassign_timestep_tensors` 是时间步张量的精细重分配函数，用于在时间步合并前优化张量的初始分配。其核心作用是：通过两种策略（移动张量到其他时间步、交换加载 / 存储张量的时间步），平衡各时间步的 GDMA 数据传输周期与 TPU 计算周期，同时确保本地内存（LMEM）分配有效，为后续时间步合并奠定更均衡的负载基础，最终提升时间序列模型的并行计算效率。

#### 核心逻辑

函数的逻辑围绕 “平衡 GDMA 传输与计算负载” 展开，分为两大核心阶段：

##### 阶段 1：移动张量以平衡单方向负载（GDMA 传输耗时 > 计算耗时）

1. 筛选需优化的时间步：遍历所有时间步，仅处理 GDMA 传输周期（`total_gdma_cycle_v`）大于 TPU 计算周期（`total_layer_cycle_v`）的时间步（这类时间步受限于数据传输，存在性能瓶颈）。
2. 选择有效目标时间步：调用 `select_valid_dst_timesteps`，为当前时间步（源时间步）筛选适合接收张量的目标时间步（需满足负载更轻、内存充足等条件）。
3. 尝试移动张量并验证：

   - 临时移动张量到目标时间步，更新内存缓冲区（`update_mem_buffer_by_tensor_move`）。
   - 重置时间步信息，并通过 `lmem_allocator.assignLmemAddr` 验证本地内存是否可分配（避免内存溢出）。
   - 若分配成功：更新张量分配信息、内存缓冲区和周期统计，标记目标时间步为 “已优化”（避免重复处理）。
   - 若分配失败：回滚时间步信息，保持原始状态。

##### 阶段 2：交换加载 / 存储张量以双向平衡负载

1. 筛选可交换的张量对：

   - 寻找源时间步中可移动的 “加载张量”（`TIMESTEP_LOAD`，从内存加载数据到计算单元）。
   - 寻找目标时间步中可移动的 “存储张量”（`TIMESTEP_STORE`，从计算单元存储数据到内存）。
2. 评估交换收益：通过计算 “松弛度”（`slack`，表示计算周期与传输周期的差值），判断交换后是否能减少整体负载不均衡（交换后总松弛度需优于交换前）。
3. 尝试交换张量并验证：

   - 临时交换加载 / 存储张量的时间步，更新内存缓冲区。
   - 验证本地内存分配是否有效，若成功则更新张量分配信息、内存缓冲区和周期统计；若失败则回滚。

#### 核心原理

该函数的优化基于 “计算与传输负载均衡” 和 “内存约束” 两大核心原理：

- 负载均衡原理：时间序列模型的性能瓶颈常来自两方面 —— 计算单元（TPU）空闲（等待数据传输）或传输单元（GDMA）空闲（等待计算完成）。通过移动或交换张量，可将耗时的传输任务分配到计算负载较轻的时间步，或通过交换加载 / 存储任务双向平衡负载，使计算与传输尽可能并行，提升硬件利用率。
- 内存约束原理：所有张量移动 / 交换操作必须通过本地内存分配验证（`lmem_allocator.assignLmemAddr`）。硬件本地内存（LMEM）容量有限，若移动后的张量超出内存容量，会导致频繁的内存交换（swap），反而降低性能。因此，内存分配有效性是优化的前提。
- 精细优化策略：仅对操作数较少的层组执行（由上层函数控制），在编译时间可接受的范围内实现细粒度优化，避免因操作数过多导致的编译成本激增。

#### reassign_timestep_tensors

```cpp
/**
 * @brief 时间步张量的精细重分配函数，通过移动或交换张量平衡各时间步的负载
 * 
 * 功能：针对时间步中GDMA传输周期与TPU计算周期不均衡的问题，通过两种策略优化：
 * 1. 将张量从传输耗时过高的时间步移动到其他时间步；
 * 2. 交换加载与存储张量的时间步，让存储提前、加载延后；
 * 同时确保本地内存（LMEM）分配有效，为后续时间步合并奠定均衡负载基础。
 * 
 * @param lg_info 层组信息（包含层组内的操作集合）
 * @param time_step 时间步指针（存储当前层组的时间步数据）
 * @param shape_secs 形状分段信息（张量的形状与分段规则）
 * @param ts_layers_v TPU层的时间步字段集合
 * @param ts_tensors_v GDMA张量的时间步字段集合
 * @param total_layer_cycle_v 各时间步的总TPU计算周期
 * @param total_gdma_cycle_v 各时间步的总GDMA传输周期
 * @param mem_buffer 内存缓冲区（整合LMEM和L2缓存信息）
 * @param print_log 是否打印日志的标志
 * @param group_idx 层组索引（用于标识和日志）
 * @param options 层组配置选项（如内存分配参数等）
 */
static void reassign_timestep_tensors(
    const LgInfo &lg_info, BasicTimeStepPtr &time_step,
    const shape_secs_t &shape_secs, std::vector<TpuTsField> &ts_layers_v,
    std::vector<GdmaTsField> &ts_tensors_v,
    std::vector<int64_t> &total_layer_cycle_v,
    std::vector<int64_t> &total_gdma_cycle_v, MemBuff &mem_buffer,
    bool &print_log, int64_t group_idx, const LgOptions &options) {
  // 策略1：若GDMA传输周期 > TPU计算周期，将张量移动到其他时间步以平衡负载
  auto lmem_allocator = LmemAllocator(options);  // 本地内存分配器（用于验证内存有效性）
  int64_t ts = -1;  // 当前遍历的时间步索引
  GdmaElt sel_tensor;  // 选中待移动的张量
  std::list<int64_t> sel_timesteps;  // 选中的目标时间步列表
  std::set<int64_t> exclude_timesteps;  // 已优化的时间步（避免重复处理）
  int64_t ts_num = time_step->get_timestep_num();  // 总时间步数量

  // 遍历所有时间步，寻找需要优化的源时间步
  while (ts < ts_num - 1) {
    ++ts;
    // 跳过无需优化的时间步：GDMA传输周期 ≤ 计算周期，或已优化过的时间步
    if (total_gdma_cycle_v[ts] <= total_layer_cycle_v[ts] ||
        exclude_timesteps.count(ts) != 0) {
      continue;
    }

    // 为当前源时间步（ts）选择有效的目标时间步（可接收张量的时间步）
    select_valid_dst_timesteps(time_step, total_layer_cycle_v,
                               total_gdma_cycle_v, ts_tensors_v, sel_timesteps,
                               sel_tensor, ts);

    int64_t src_ts = ts;  // 源时间步（待移出张量的时间步）
    // 尝试将张量移动到目标时间步列表中的每个时间步
    while (!sel_timesteps.empty()) {
      int64_t dst_ts = sel_timesteps.front();  // 目标时间步（接收张量的时间步）
      sel_timesteps.pop_front();

      // 临时更新张量的时间步分配（模拟移动）
      std::vector<GdmaTsField> tmp_tensors(ts_tensors_v);  // 临时存储张量分配信息
      MemBuff new_mem_buffer;  // 移动后的内存缓冲区
      // 更新内存缓冲区（基于张量移动）
      update_mem_buffer_by_tensor_move(time_step, tmp_tensors, mem_buffer,
                                       new_mem_buffer, &sel_tensor, &src_ts,
                                       &dst_ts, 1);
      // 重置时间步信息为移动后的状态
      time_step->reset_timestep(ts_layers_v, tmp_tensors, new_mem_buffer);

      // 验证本地内存是否可分配（关键：确保移动后不超出内存容量）
      if (lmem_allocator.assignLmemAddr(lg_info, time_step, shape_secs)) {
        // 内存分配成功：更新张量分配信息
        ts_tensors_v = tmp_tensors;
        exclude_timesteps.insert(dst_ts);  // 标记目标时间步为已优化

        // 更新内存缓冲区和周期统计（反映移动后的状态）
        mem_buffer = time_step->get_lmem_buffer();
        update_cycle_info(total_gdma_cycle_v, total_layer_cycle_v, time_step,
                          shape_secs);

        // 打印日志（首次移动时）
        if (print_log) {
          LAYER_GROUP_LOG_DEBUG_BLOCK(
              { llvm::outs() << "===group_idx: " << group_idx; });
        }
        LAYER_GROUP_LOG_DEBUG_BLOCK({
          llvm::outs() << "move tensor " << module::getName(sel_tensor.first)
                       << " from timestep " << src_ts << " to timestep "
                       << dst_ts;
        });
        print_log = false;  // 仅打印首次移动的日志
        break;  // 移动成功，退出当前目标时间步循环
      } else {
        // 内存分配失败：回滚时间步信息至移动前状态
        time_step->reset_timestep(ts_layers_v, ts_tensors_v, mem_buffer);
      }
    }

    // 若目标时间步列表为空或已处理当前源时间步，标记源时间步为已优化
    if (sel_timesteps.empty() || ts != -1) {
      exclude_timesteps.insert(src_ts);
    }
  }

  // 策略2：交换加载（LOAD）和存储（STORE）GDMA操作，让存储提前、加载延后，双向平衡负载
  auto gdma_cycle = time_step->get_gdma_cycle();  // 各张量的GDMA传输周期
  for (int64_t src_ts = 0; src_ts < ts_num; src_ts++) {  // 遍历源时间步
    GdmaElt src_tensor, dst_tensor;  // 待交换的加载张量和存储张量
    bool found_src_tensor = false;   // 是否找到可移动的加载张量
    bool found_dst_tensor = false;   // 是否找到可移动的存储张量

    // 步骤1：寻找源时间步中可移动的加载张量（TIMESTEP_LOAD）
    // 条件：不在LMEM中保持（需传输）且模式为加载
    for (size_t j = 0; j < ts_tensors_v[src_ts].size(); ++j) {
      if (!time_step->is_tensor_hold_in_lmem(ts_tensors_v[src_ts][j].first) &&
          ts_tensors_v[src_ts][j].second.mode == TIMESTEP_LOAD) {
        src_tensor = ts_tensors_v[src_ts][j];
        found_src_tensor = true;
        break;
      }
    }
    if (!found_src_tensor) {  // 无可用加载张量，跳过当前源时间步
      continue;
    }

    // 步骤2：寻找目标时间步中可移动的存储张量（TIMESTEP_STORE）
    int64_t dst_ts = -1;  // 目标时间步索引
    // 从后往前遍历目标时间步（优先更远的时间步，提升平衡效果）
    for (dst_ts = ts_num - 1; dst_ts > src_ts; --dst_ts) {
      // 检查加载张量是否可从源时间步移动到目标时间步
      if (!time_step->tensor_can_move(src_tensor, src_ts, dst_ts)) {
        continue;
      }
      // 检查目标时间步中是否有可移动到源时间步的存储张量
      for (size_t k = 0; k < ts_tensors_v[dst_ts].size(); ++k) {
        auto &tensor = ts_tensors_v[dst_ts][k];
        // 条件：模式为存储，且可从目标时间步移动到源时间步
        if (tensor.second.mode != TIMESTEP_STORE ||
            !time_step->tensor_can_move(tensor, dst_ts, src_ts)) {
          continue;
        }

        // 评估交换收益：通过松弛度（slack）判断是否改善负载均衡
        int64_t src_tensor_cycle = gdma_cycle[src_tensor.first];  // 加载张量的传输周期
        int64_t dst_tensor_cycle = gdma_cycle[tensor.first];      // 存储张量的传输周期
        // 源时间步的松弛度：计算周期 - 传输周期（负值表示传输耗时更长）
        int64_t src_slack = total_layer_cycle_v[src_ts] - total_gdma_cycle_v[src_ts];
        // 目标时间步的松弛度
        int64_t dst_slack = total_layer_cycle_v[dst_ts] - total_gdma_cycle_v[dst_ts];
        // 交换后的源时间步松弛度（取最小值，避免过度优化）
        int64_t src_slack_after = std::min(
            src_slack - dst_tensor_cycle + src_tensor_cycle, (int64_t)0);
        // 交换后的目标时间步松弛度
        int64_t dst_slack_after = std::min(
            dst_slack + dst_tensor_cycle - src_tensor_cycle, (int64_t)0);
        // 修正松弛度（非负表示无瓶颈，记为0）
        src_slack = src_slack >= 0 ? 0 : src_slack;
        dst_slack = dst_slack >= 0 ? 0 : dst_slack;

        // 若交换后总松弛度（负载不均衡程度）未改善，跳过
        if (src_slack_after + dst_slack_after < src_slack + dst_slack) {
          continue;
        }

        // 找到合适的存储张量，记录并退出循环
        dst_tensor = tensor;
        found_dst_tensor = true;
        break;
      }
      if (found_dst_tensor) {  // 找到目标存储张量，退出目标时间步循环
        break;
      }
    }
    if (!found_dst_tensor) {  // 无合适的存储张量，跳过当前源时间步
      continue;
    }

    // 步骤3：执行张量交换并验证内存分配
    // 准备交换的张量对、源时间步对、目标时间步对
    std::vector<GdmaTsField> new_ts_tensors_v(ts_tensors_v);  // 临时存储交换后的张量分配
    MemBuff new_mem_buffer;  // 交换后的内存缓冲区
    GdmaElt src_tensor_v[2] = {src_tensor, dst_tensor};  // 待交换的两个张量
    int64_t src_ts_v[2] = {src_ts, dst_ts};              // 源时间步（各自的原始时间步）
    int64_t dst_ts_v[2] = {dst_ts, src_ts};              // 目标时间步（交换后的时间步）
    // 更新内存缓冲区（基于张量交换）
    update_mem_buffer_by_tensor_move(time_step, new_ts_tensors_v, mem_buffer,
                                     new_mem_buffer, src_tensor_v, src_ts_v,
                                     dst_ts_v, 2);

    // 重置时间步信息为交换后的状态
    time_step->reset_timestep(ts_layers_v, new_ts_tensors_v, new_mem_buffer);
    // 验证本地内存是否可分配
    if (lmem_allocator.assignLmemAddr(lg_info, time_step, shape_secs)) {
      // 内存分配成功：更新张量分配信息
      ts_tensors_v = std::move(new_ts_tensors_v);
      // 更新内存缓冲区和周期统计
      mem_buffer = time_step->get_lmem_buffer();
      update_cycle_info(total_gdma_cycle_v, total_layer_cycle_v, time_step,
                        shape_secs);

      // 打印日志（首次交换时）
      if (print_log) {
        LAYER_GROUP_LOG_DEBUG_BLOCK(
            { llvm::outs() << "===group idx: " << group_idx; });
      }
      LAYER_GROUP_LOG_DEBUG_BLOCK({
        llvm::outs() << "move tensor " << module::getName(src_tensor.first)
                     << " from timestep " << src_ts << " to timestep "
                     << dst_ts;
        llvm::outs() << "move tensor " << module::getName(dst_tensor.first)
                     << " from timestep " << dst_ts << " to timestep "
                     << src_ts;
      });
      print_log = false;  // 仅打印首次交换的日志
      break;  // 交换成功，退出源时间步循环
    } else {
      // 内存分配失败：回滚时间步信息至交换前状态
      time_step->reset_timestep(ts_layers_v, ts_tensors_v, mem_buffer);
    }
  }
}
```

### 5.select_valid_dst_timesteps

#### 核心功能

`select_valid_dst_timesteps` 是为源时间步选择有效目标时间步的函数，用于在张量移动优化（如 `reassign_timestep_tensors`）中，为源时间步（`src_ts`）中的张量筛选出最合适的目标时间步。其核心作用是：基于 “周期收益”（`cycle_profit`）评估，从所有可能的时间步中选择能最大化提升负载均衡的目标时间步，确保张量移动后能有效平衡源和目标时间步的 GDMA 传输与 TPU 计算负载。

#### 核心逻辑

函数的逻辑围绕 “张量筛选 → 目标时间步评估 → 最佳选择” 展开，具体流程如下：

1. 初始化与参数准备：
   获取总时间步数量（`ts_num`）和各张量的 GDMA 传输周期（`gdma_cycle`），创建 `timesteps_v` 用于存储每个张量对应的候选目标时间步（按周期收益降序排列）。
2. 遍历源时间步的张量：
   对源时间步（`src_ts`）中的每个张量：

   - 跳过在本地内存（LMEM）中保持的张量（无需移动，因其不产生传输瓶颈）。
   - 计算源时间步移动该张量后的 “松弛度”（`src_slack_after`）：松弛度是计算周期与传输周期的差值，移动张量后源的传输周期减少，松弛度改善（值增大）。
3. 评估候选目标时间步：
   遍历所有可能的目标时间步（`ts`），筛选出符合条件的时间步（排除源时间步自身及不可移动的时间步）：

   - 计算目标时间步接收该张量后的松弛度（`dst_slack_after`）：目标的传输周期增加，松弛度可能降低（值减小）。
   - 计算 “周期收益”（`cycle_profit`）：移动后源和目标的总松弛度变化（`dst_slack_after + src_slack_after - 原源松弛度`），仅保留收益为正的目标时间步（表示整体负载均衡改善）。
   - 将候选目标时间步按周期收益降序存入 `sorted_timesteps`。
4. 选择最佳目标时间步：
   跟踪所有张量中周期收益最大的张量（`sel_tensor`），并将其对应的最佳目标时间步（收益最高的）加入 `sel_timesteps`，供后续张量移动使用。

#### 核心原理

该函数的优化核心是 “周期收益评估”，其原理基于 “负载均衡的量化指标 —— 松弛度”：

- 松弛度（slack）：`total_layer_cycle - total_gdma_cycle`，表示计算周期与传输周期的差值。若为负值，说明传输耗时超过计算耗时（传输是瓶颈）；若为正值，说明计算是瓶颈。
- 周期收益（cycle_profit）：移动张量后，源和目标时间步的总松弛度变化。只有收益为正，才意味着移动后整体负载更均衡（传输与计算的瓶颈被缓解）。
- 筛选逻辑：通过排除不可移动的时间步、仅保留正收益的候选，并按收益排序，确保选中的目标时间步能最大化提升整体性能，为后续张量移动提供最优选择。

#### select_valid_dst_timesteps 代码

```cpp
/**
 * @brief 为源时间步选择有效的目标时间步，用于张量移动以优化负载均衡
 * 
 * 功能：遍历源时间步中的所有张量，评估将其移动到其他时间步后的"周期收益"，
 * 筛选出能最大化提升整体负载均衡的目标时间步，为张量移动提供候选列表。
 * 
 * @param time_step 时间步指针（提供时间步基本信息和移动合法性检查）
 * @param total_layer_cycle_v 各时间步的总TPU计算周期
 * @param total_gdma_cycle_v 各时间步的总GDMA传输周期
 * @param ts_tensors_v GDMA张量的时间步分配列表
 * @param sel_timesteps 输出参数，选中的目标时间步列表（按收益排序）
 * @param sel_tensor 输出参数，选中的待移动张量（周期收益最大的张量）
 * @param src_ts 源时间步（待移出张量的时间步）
 */
static void
select_valid_dst_timesteps(BasicTimeStepPtr &time_step,
                           const std::vector<int64_t> &total_layer_cycle_v,
                           const std::vector<int64_t> &total_gdma_cycle_v,
                           std::vector<GdmaTsField> ts_tensors_v,
                           std::list<int64_t> &sel_timesteps,
                           GdmaElt &sel_tensor, int64_t src_ts) {
  int64_t ts_num = time_step->get_timestep_num();  // 总时间步数量
  auto &gdma_cycle = time_step->get_gdma_cycle();  // 各张量的GDMA传输周期

  // timesteps_v：存储每个张量对应的候选目标时间步，按周期收益（cycle_profit）降序排列
  // 每个元素是map：key=周期收益，value=目标时间步索引
  std::vector<std::map<int64_t, int64_t, std::greater<int64_t>>> timesteps_v;

  int64_t location = -1;  // 选中张量在源时间步张量列表中的位置
  int64_t best_cycle_profit = 0;  // 最大周期收益（初始化为0，仅保留正收益）

  // 遍历源时间步中的每个张量，评估移动可能性
  for (size_t i = 0; i < ts_tensors_v[src_ts].size(); ++i) {
    std::map<int64_t, int64_t, std::greater<int64_t>> sorted_timesteps;  // 当前张量的候选目标时间步
    auto cur_tensor = ts_tensors_v[src_ts][i];  // 当前评估的张量
    Value v = cur_tensor.first;  // 张量的标识（Value类型）

    // 若张量在LMEM中保持（无需传输），无需移动，跳过
    if (time_step->is_tensor_hold_in_lmem(v)) {
      timesteps_v.push_back(sorted_timesteps);
      continue;
    }

    // 计算源时间步移动该张量后的松弛度（slack）
    // 源松弛度：原计算周期 - 原传输周期（负值表示传输是瓶颈）
    int64_t src_slack = total_layer_cycle_v[src_ts] - total_gdma_cycle_v[src_ts];
    // 移动后源松弛度：原松弛度 + 该张量的传输周期（传输周期减少，松弛度改善）
    // 取min(..., 0)：松弛度非负时视为无瓶颈，无需进一步优化
    int64_t src_slack_after = std::min(src_slack + gdma_cycle[v], (int64_t)0);

    // 遍历所有可能的目标时间步，评估是否适合接收该张量
    for (int64_t ts = 0; ts < ts_num; ++ts) {
      // 跳过源时间步自身，或张量不可移动到该时间步的情况
      if (!time_step->tensor_can_move(cur_tensor, src_ts, ts) || ts == src_ts) {
        continue;
      }

      // 计算目标时间步接收该张量后的松弛度
      // 目标松弛度：原计算周期 - 原传输周期
      int64_t dst_slack = total_layer_cycle_v[ts] - total_gdma_cycle_v[ts];
      // 移动后目标松弛度：原松弛度 - 该张量的传输周期（传输周期增加，松弛度可能恶化）
      // 取min(..., 0)：同上，非负视为无瓶颈
      int64_t dst_slack_after = std::min(dst_slack - gdma_cycle[v], (int64_t)0);

      // 计算周期收益：移动后总松弛度（源+目标）相对源原松弛度的改善量
      // 收益 > 0 表示整体负载均衡改善
      int64_t cycle_profit = dst_slack_after + src_slack_after - src_slack;

      // 仅保留正收益的目标时间步
      if (cycle_profit <= 0) {
        continue;
      }

      // 将目标时间步按周期收益降序存入map（便于后续取最优）
      sorted_timesteps.insert(std::make_pair(cycle_profit, ts));

      // 跟踪最大周期收益的张量
      if (cycle_profit > best_cycle_profit) {
        best_cycle_profit = cycle_profit;  // 更新最大收益
        sel_tensor = cur_tensor;  // 记录当前张量为最佳待移动张量
        location = i;  // 记录当前张量的位置
      }
    }

    // 将当前张量的候选目标时间步加入列表
    timesteps_v.push_back(sorted_timesteps);
  }

  // 若存在有效收益且最佳张量有候选目标时间步，将最优目标时间步加入结果列表
  if (best_cycle_profit != 0 && !timesteps_v[location].empty()) {
    // 取收益最高的目标时间步（map的第一个元素）
    sel_timesteps.push_back(timesteps_v[location].begin()->second);
  }
}
```

### 6.update_mem_buffer_by_tensor_move

#### 核心功能

`update_mem_buffer_by_tensor_move` 是张量移动后的内存缓冲区同步更新函数，用于在张量从源时间步（`src_ts`）移动到目标时间步（`dst_ts`）后，同步维护两部分关键信息的一致性：

1. 张量在各时间步的分配关系（`ts_tensors_v`）；
2. 内存缓冲区中张量的生命周期（`start_ts` 和 `end_ts`，即张量占用内存的时间范围）。

其核心作用是确保张量移动后，时间步的张量分配列表和内存占用记录与新的张量位置匹配，为后续的内存分配验证（如 `lmem_allocator.assignLmemAddr`）和时间步合并提供准确的数据基础，避免因信息不一致导致的内存溢出或计算错误。

#### 核心逻辑

函数的逻辑分为 “张量分配更新” 和 “内存缓冲区更新” 两个阶段，流程如下：

##### 阶段 1：更新张量在时间步间的分配关系

1. 遍历待移动的张量：对每个待移动的张量（共 `tensor_num` 个），定位其在源时间步（`src_ts[i]`）的张量列表（`ts_tensors_v[src_ts[i]]`）中的位置。
2. 移动张量：将张量从源时间步的列表中删除，并添加到目标时间步（`dst_ts[i]`）的列表（`ts_tensors_v[dst_ts[i]]`）中。
3. 结果：`ts_tensors_v` 准确反映移动后各时间步包含的张量，确保后续操作能基于最新的分配关系进行。

##### 阶段 2：更新内存缓冲区的生命周期信息

内存缓冲区（`MemBuff`）记录了每个张量在内存中的 “生命周期”——`start_ts`（开始占用内存的时间步）和 `end_ts`（结束占用的时间步）。张量移动后，其生命周期会变化，需同步更新：

1. 遍历源内存缓冲区：对每个缓冲区条目（`buffer_key` 对应张量，`buffer_value` 包含 `start_ts` 和 `end_ts`），检查是否与待移动的张量相关。
2. 根据张量模式更新生命周期：

   - 存储操作（`TIMESTEP_STORE`）：张量从计算单元存储到内存的时间步改变，需更新 `end_ts`（若目标时间步晚于原 `end_ts`，或处于环形时间步的有效区间内）。
   - 加载操作（`TIMESTEP_LOAD`）：张量从内存加载到计算单元的时间步改变，直接将 `start_ts` 更新为目标时间步。
3. 同步到目标缓冲区：将更新后的生命周期信息存入 `dst_mem_buffer`，确保内存管理器能基于最新的时间范围计算内存占用。

#### 核心原理

该函数的设计基于 “内存生命周期与张量位置强关联” 的原理：

- 张量的加载 / 存储操作直接决定其在内存中的存在时间（`start_ts` 和 `end_ts`）。例如，加载操作（`LOAD`）在 `start_ts` 将张量读入内存，存储操作（`STORE`）在 `end_ts` 将张量写入内存并可能释放。
- 当张量移动到新时间步时，其加载 / 存储的时间点改变，若不更新生命周期，内存管理器会基于旧时间范围计算，可能导致：

  - 内存分配冲突（如同一内存区域被多个张量重复占用）；
  - 内存释放过早（张量仍需使用却被释放）或过晚（占用内存导致其他张量无法分配）。

通过同步更新张量分配和内存生命周期，函数确保了内存管理的准确性，是张量移动操作的 “一致性保障环节”。

#### update_mem_buffer_by_tensor_move 代码

```cpp
/**
 * @brief 张量移动后更新内存缓冲区与时间步分配列表，确保信息一致性
 * 
 * 功能：当张量从源时间步移动到目标时间步后，同步更新：
 * 1. 张量在各时间步的分配列表（ts_tensors_v）；
 * 2. 内存缓冲区中张量的生命周期（start_ts/end_ts），
 * 为后续内存分配和时间步合并提供准确的数据基础。
 * 
 * @param time_step 时间步指针（提供时间步元信息）
 * @param ts_tensors_v 按时间步存储的GDMA张量列表（需更新的分配关系）
 * @param src_mem_buffer 移动前的内存缓冲区（原始生命周期信息）
 * @param dst_mem_buffer 移动后的内存缓冲区（输出，更新后的生命周期信息）
 * @param src_tensors 待移动的张量数组（每个元素包含张量标识和操作模式）
 * @param src_ts 源时间步数组（每个张量的原始时间步）
 * @param dst_ts 目标时间步数组（每个张量的新时间步）
 * @param tensor_num 待移动的张量数量
 */
static void update_mem_buffer_by_tensor_move(
    BasicTimeStepPtr &time_step, std::vector<GdmaTsField> &ts_tensors_v,
    const MemBuff &src_mem_buffer, MemBuff &dst_mem_buffer,
    const GdmaElt *src_tensors, const int64_t *src_ts, const int64_t *dst_ts,
    int64_t tensor_num) {
  // 阶段1：更新张量在时间步间的分配关系（从源时间步移动到目标时间步）
  int64_t src_i = 0;  // 当前处理的待移动张量索引
  //  lambda函数：判断张量是否与当前待移动张量相同（通过张量标识匹配）
  auto is_the_same_value = [&](const GdmaElt &elt) {
    return elt.first == src_tensors[src_i].first;
  };

  // 遍历每个待移动的张量，执行移动操作
  for (int64_t i = 0; i < tensor_num; ++i) {
    src_i = i;  // 更新当前张量索引
    // 在源时间步的张量列表中找到待移动的张量
    auto tensor_iter = std::find_if(
        ts_tensors_v[src_ts[i]].begin(), 
        ts_tensors_v[src_ts[i]].end(), 
        is_the_same_value
    );
    // 将张量添加到目标时间步的张量列表
    ts_tensors_v[dst_ts[i]].push_back(src_tensors[i]);
    // 从源时间步的张量列表中删除该张量
    ts_tensors_v[src_ts[i]].erase(tensor_iter);
  }

  // 阶段2：更新内存缓冲区的生命周期信息（start_ts和end_ts）
  mem_buffer_value_t buffer_value;  // 存储单个张量的生命周期信息
  // 遍历源内存缓冲区的所有条目
  for (auto it = src_mem_buffer.begin(); it != src_mem_buffer.end(); ++it) {
    auto &buffer_key = it->first;  // 缓冲区键（包含张量标识和操作类型）
    buffer_value = it->second;     // 原始的生命周期信息（start_ts和end_ts）
    int64_t start_ts = buffer_value.start_ts;  // 原始开始时间步
    int64_t end_ts = buffer_value.end_ts;      // 原始结束时间步

    // 检查当前缓冲区是否与待移动的张量相关
    for (int64_t i = 0; i < tensor_num; ++i) {
      // 条件：缓冲区对应的张量是当前待移动张量，且非LMEM内部操作（无需更新）
      if (buffer_key.value == src_tensors[i].first && 
          buffer_key.type != LMEM_OPERATION) {

        // 子情况1：张量是存储操作（TIMESTEP_STORE）
        if (src_tensors[i].second.mode == TIMESTEP_STORE) {
          // 若目标时间步超出原结束时间步，更新end_ts：
          // - 线性时间步（start_ts < end_ts）：目标时间步 > 原end_ts
          // - 环形时间步（start_ts > end_ts，如循环时序）：目标时间步在(end_ts, start_ts)区间内
          if ((start_ts < end_ts && dst_ts[i] > end_ts) ||
              (start_ts > end_ts && dst_ts[i] > end_ts && dst_ts[i] < start_ts)) {
            buffer_value.end_ts = dst_ts[i];  // 更新结束时间步为目标时间步
          }
        }
        // 子情况2：张量是加载操作（TIMESTEP_LOAD）
        else if (src_tensors[i].second.mode == TIMESTEP_LOAD) {
          buffer_value.start_ts = dst_ts[i];  // 直接更新开始时间步为目标时间步
        }
        // 其他模式不支持，触发编译期断言
        else {
          llvm_unreachable("Wrong tensor timestep type!");
        }
      }
    }
    // 将更新后的生命周期信息存入目标内存缓冲区
    dst_mem_buffer[buffer_key] = buffer_value;
  }
}
```

### 7.merge_timesteps

#### 核心功能

`merge_timesteps` 是时间步合并的核心实现函数，用于在内存感知的前提下，将相邻的时间步（Timestep）合并，以平衡 GDMA 数据传输周期与 TPU 计算周期（BDC 周期）的负载。其核心作用是：当某一时间步或下一时间步存在 “传输耗时（GDMA）> 计算耗时（TPU）” 的瓶颈时，通过合并相邻时间步，整合计算与传输任务，提升硬件资源（计算单元、传输单元）的利用率，最终优化时间序列模型的推理效率。

#### 核心逻辑

函数的逻辑围绕 “筛选可合并时间步 → 执行合并 → 验证与回滚” 展开，具体流程如下：

1. 初始化与状态保存：

   - 创建本地内存分配器（`lmem_allocator`），用于验证合并后的内存是否可分配。
   - 保存原始时间步的层信息（`p_layers`）、张量信息（`p_tensors`）和内存缓冲区（`p_lmem`），用于合并失败时回滚。
   - 设置误差阈值（`error = 0.1f`），用于判断计算与传输负载的均衡程度（允许 10% 的误差）。
2. 遍历时间步筛选可合并对：

   - 循环遍历每个时间步（`ts`），跳过已标记为 “排除”（`exclude_ts`）的时间步。
   - 计算当前时间步（`ts`）和下一时间步（`ts+1`）的计算周期（`cur_layer_cycle`/`next_layer_cycle`）与传输周期（`cur_gdma_cycle`/`next_gdma_cycle`）。
   - 筛选条件：若两个时间步的传输周期均不超过计算周期，且两者的计算 - 传输差距均超过误差阈值（负载均衡良好），则无需合并；否则，标记为可合并。
3. 执行时间步合并：

   - 若时间步可向后合并（`layer_can_merge_backward` 验证通过），则将下一时间步（`ts+1`）的层信息（`ts_layers_v`）和张量信息（`ts_tensors_v`）合并到当前时间步（`ts`），并删除下一时间步的条目。
   - 调用 `lmem_alloc_by_timestep_merge` 验证合并后的内存分配可行性，若失败则跳过。
   - 更新合并后的内存缓冲区（`update_mem_buffer_by_timestep_merge`），重置时间步信息，并重新计算合并后的周期统计（`update_cycle_info`）。
   - 记录合并日志，重置循环索引（`ts = -1`）以重新检查所有时间步（因合并可能产生新的可合并对）。
4. 验证与回滚：

   - 合并完成后，通过 `lmem_allocator.assignLmemAddr` 最终验证内存分配是否有效。
   - 若无效，则回滚到合并前的原始状态（`reset_timestep`），确保模型正确性。

#### 核心原理

该函数的优化基于 “计算与传输负载均衡” 和 “硬件资源利用率” 两大原理：

- 负载均衡原理：时间序列模型的性能受限于 “计算单元空闲等待传输” 或 “传输单元空闲等待计算”。当某一时间步的传输周期（GDMA）超过计算周期（TPU）时，传输是瓶颈；合并相邻时间步可将多个传输任务与计算任务整合，使两者并行执行，减少空闲时间。
- 内存约束原理：合并时间步会增加单时间步的内存占用（需同时容纳两个时间步的张量），因此必须通过内存分配验证（`lmem_alloc_by_timestep_merge` 和 `assignLmemAddr`）确保不超出硬件本地内存（LMEM）容量，避免内存溢出导致的性能崩溃。
- 迭代优化原理：合并后需重新遍历时间步（`ts = -1`），因为合并可能使原本均衡的时间步出现新的负载失衡，或产生新的可合并对，通过迭代实现全局最优。

#### 4.merge_timesteps 代码

```cpp
/**
 * @brief 合并相邻时间步以优化负载均衡，平衡GDMA传输与TPU计算的耗时
 * 
 * 功能：当当前或下一时间步存在"GDMA传输周期 > TPU计算周期"的瓶颈时，
 * 合并相邻时间步，整合计算与传输任务，提升硬件资源利用率。合并过程中确保
 * 内存分配有效，避免溢出；若失败则回滚到原始状态。
 * 
 * @param lg_info 层组信息（包含层组内的操作集合）
 * @param time_step 时间步指针（存储时间步数据）
 * @param shape_secs 形状分段信息（张量形状与分段规则）
 * @param ts_layers_v TPU层的时间步分配列表
 * @param ts_tensors_v GDMA张量的时间步分配列表
 * @param total_layer_cycle_v 各时间步的总TPU计算周期
 * @param total_gdma_cycle_v 各时间步的总GDMA传输周期
 * @param mem_buffer 内存缓冲区（整合LMEM和L2缓存信息）
 * @param print_log 是否打印日志的标志
 * @param group_idx 层组索引（用于标识和日志）
 * @param options 层组配置选项（如内存分配参数）
 */
static void merge_timesteps(const LgInfo &lg_info, BasicTimeStepPtr &time_step,
                            const shape_secs_t &shape_secs,
                            std::vector<TpuTsField> &ts_layers_v,
                            std::vector<GdmaTsField> &ts_tensors_v,
                            std::vector<int64_t> &total_layer_cycle_v,
                            std::vector<int64_t> &total_gdma_cycle_v,
                            MemBuff &mem_buffer, bool &print_log,
                            int64_t group_idx, const LgOptions &options) {
  // 初始化本地内存分配器（用于验证合并后的内存是否可分配）
  auto lmem_allocator = LmemAllocator(options);
  // 保存原始状态（用于合并失败时回滚）
  MemBuff p_lmem = time_step->get_lmem_buffer();  // 原始内存缓冲区
  std::vector<TpuTsField> p_layers(ts_layers_v);  // 原始层分配列表
  std::vector<GdmaTsField> p_tensors(ts_tensors_v);  // 原始张量分配列表
  std::vector<std::string> ss;  // 存储合并日志
  // 周期误差阈值（允许10%的误差，用于判断负载是否均衡）
  float error = 0.1f;

  int64_t ts = -1;  // 当前遍历的时间步索引
  std::set<int64_t> exclude_ts;  // 已处理/无需合并的时间步（避免重复处理）

  // 循环遍历时间步，寻找可合并的相邻时间步对
  while (true) {
    ts++;  // 移动到下一个时间步
    int64_t ts_num = time_step->get_timestep_num();  // 总时间步数量
    if (ts == ts_num - 1) {  // 已遍历到最后一个时间步，退出循环
      break;
    }
    if (exclude_ts.count(ts) != 0) {  // 跳过已标记为排除的时间步
      continue;
    }

    // 获取当前和下一时间步的计算与传输周期
    float cur_layer_cycle = total_layer_cycle_v[ts];      // 当前时间步计算周期
    float cur_gdma_cycle = total_gdma_cycle_v[ts];        // 当前时间步传输周期
    float next_layer_cycle = total_layer_cycle_v[ts + 1]; // 下一时间步计算周期
    float next_gdma_cycle = total_gdma_cycle_v[ts + 1];   // 下一时间步传输周期

    // 判断是否需要合并：若两个时间步的传输周期均≤计算周期，且计算-传输差距超过误差阈值
    // （即负载均衡良好，无需合并）
    if (cur_gdma_cycle <= cur_layer_cycle &&
        next_gdma_cycle <= next_layer_cycle &&
        (cur_layer_cycle - cur_gdma_cycle) / cur_layer_cycle > error &&
        (next_layer_cycle - next_gdma_cycle) / next_layer_cycle > error) {
      continue;  // 负载均衡良好，跳过合并
    }

    // 准备合并：初始化合并后的层和张量分配列表
    std::vector<GdmaTsField> new_ts_tensors_v(ts_tensors_v);  // 合并后的张量列表
    std::vector<TpuTsField> new_ts_layers_v(ts_layers_v);     // 合并后的层列表
    MemBuff new_mem_buffer;  // 合并后的内存缓冲区

    // 判断是否为单循环模式（影响合并逻辑）
    bool one_loop = (shape_secs.nsecs * shape_secs.hsecs * shape_secs.dsecs *
                         shape_secs.wsecs ==
                     1);

    // 验证当前时间步是否可向后合并（下一时间步合并到当前）
    if (time_step->layer_can_merge_backward(ts, one_loop)) {
      // 合并层信息：将下一时间步的层添加到当前时间步，删除下一时间步
      new_ts_layers_v[ts].insert(new_ts_layers_v[ts].end(),
                                 new_ts_layers_v[ts + 1].begin(),
                                 new_ts_layers_v[ts + 1].end());
      new_ts_layers_v.erase(new_ts_layers_v.begin() + ts + 1);

      // 合并张量信息：将下一时间步的张量添加到当前时间步，删除下一时间步
      new_ts_tensors_v[ts].insert(new_ts_tensors_v[ts].end(),
                                  new_ts_tensors_v[ts + 1].begin(),
                                  new_ts_tensors_v[ts + 1].end());
      new_ts_tensors_v.erase(new_ts_tensors_v.begin() + ts + 1);

      // 验证合并后的内存分配是否可行（避免内存溢出）
      if (!lmem_alloc_by_timestep_merge(time_step, ts)) {
        continue;  // 内存分配失败，跳过该合并
      }

      // 更新合并后的内存缓冲区（同步时间步合并后的生命周期）
      update_mem_buffer_by_timestep_merge(time_step, new_ts_tensors_v,
                                          time_step->get_lmem_buffer(),
                                          new_mem_buffer, ts, !one_loop);

      // 重置时间步信息为合并后的状态
      time_step->reset_timestep(new_ts_layers_v, new_ts_tensors_v,
                                new_mem_buffer);
      // 更新层和张量分配列表为合并后的数据
      ts_layers_v = new_ts_layers_v;
      ts_tensors_v = new_ts_tensors_v;
      new_mem_buffer = mem_buffer;
      // 重新计算合并后的周期统计（反映合并后的负载）
      update_cycle_info(total_gdma_cycle_v, total_layer_cycle_v, time_step,
                        shape_secs);

      // 记录合并日志
      if (print_log) {
        ss.push_back("===group idx: " + std::to_string(group_idx));
      }
      ss.push_back("merge timestep " + std::to_string(ts + 1) +
                   " to timestep " + std::to_string(ts));
      print_log = false;  // 仅打印首次合并的日志

      // 移除可能因合并产生的排除标记，重新检查
      auto iter = exclude_ts.find(ts - 1);
      if (iter != exclude_ts.end()) {
        exclude_ts.erase(iter);
      }
      ts = -1;  // 重置循环索引，重新遍历所有时间步（可能产生新的可合并对）
    }
  }

  // 最终验证合并后的内存分配是否有效
  if (lmem_allocator.assignLmemAddr(lg_info, time_step, shape_secs) == false) {
    // 内存分配失败：回滚到合并前的原始状态
    ss.clear();
    time_step->reset_timestep(p_layers, p_tensors, p_lmem);
  }

  // 打印所有合并日志
  for (auto iter : ss) {
    LAYER_GROUP_LOG_DEBUG_BLOCK({ llvm::outs() << iter << "\n"; });
  }
}
```

### 8.lmem_alloc_by_timestep_merge

#### 核心功能

`lmem_alloc_by_timestep_merge` 是时间步合并时的本地内存（LMEM）分配验证函数，用于在合并相邻时间步（`ts` 和 `ts+1`）时，检查并重新分配 LMEM 中的内存块，确保合并后的张量能在 LMEM 中无冲突地存储。其核心作用是：通过梳理当前和下一时间步的 LMEM 使用情况，为需要迁移的内存块（原属于 `ts+1` 时间步）寻找新的空闲地址，避免地址冲突或超出 LMEM 容量，最终验证时间步合并的内存可行性。

#### 核心逻辑

函数的逻辑围绕 “内存块分类 → 已用 / 未用 bank 识别 → 内存块重新分配 → 分配有效性验证” 展开，具体流程如下：

1. 内存块分类：
   遍历 LMEM 缓冲区（`lmem_buffer`），将内存块分为两类：

   - 待重新分配的内存块：原属于下一时间步（`ts+1`）的内存块（`start_ts == ts+1`），按操作类型（GDMA 传输 / NPU 计算）分别存入 `gdma_mem` 和 `npu_mem`（按内存块大小降序排列，优先分配大内存块）。
   - 已使用的内存块：原属于当前时间步（`ts`）的内存块，按操作类型存入 `gdma_used_mem` 和 `npu_used_mem`（记录地址和大小，用于识别空闲空间）。
2. 内存 bank 管理：
   LMEM 通常按 “bank” 划分（硬件设计，避免访问冲突），每个 bank 有固定大小（`Arch::LMEM_BANK_BYTES`）。函数通过以下步骤管理 bank：

   - 识别已使用的 bank（`used_banks`）：基于已使用内存块的地址计算其所属 bank。
   - 识别未使用的 bank（`unused_banks`）：未被 `used_banks` 包含的 bank，可用于新分配。
3. 内存块重新分配：
   调用 `assign_addr` 函数为待分配内存块（`npu_mem` 和 `gdma_mem`）寻找地址，分配策略为：

   - 优先利用已使用 bank 中的空闲空间（在已用内存块之间的间隙）。
   - 若已用 bank 无空闲空间，使用未使用的 bank。
   - 确保分配的地址满足硬件对齐要求（`Arch::EU_BYTES`，避免非对齐访问）。
4. 分配有效性验证：
   检查是否所有待分配内存块都成功找到地址，若全部分配成功，则更新 LMEM 地址并返回 `true`（合并可行）；否则返回 `false`（合并不可行）。

#### 核心原理

该函数的设计基于硬件 LMEM 的特性：

- 容量有限：LMEM 是硬件的本地高速内存，容量远小于外部内存，合并时间步会导致内存需求增加，需严格控制内存占用。
- bank 结构：LMEM 通常分为多个独立 bank，同时访问不同 bank 的内存可并行，但若访问同一 bank 会产生冲突，因此需避免内存块跨 bank 或冲突。
- 地址对齐：硬件要求内存访问地址按特定字节数（`Arch::EU_BYTES`）对齐，否则会降低访问效率或触发错误。

通过分类管理内存块、优先利用空闲空间、严格遵循 bank 和对齐约束，函数确保合并后的内存布局既满足硬件限制，又能高效访问，是时间步合并可行性的关键验证环节。

#### 4.lmem_alloc_by_timestep_merge 代码

```cpp
/**
 * @brief 时间步合并时的LMEM内存分配验证函数，确保合并后内存无冲突
 * 
 * 功能：在合并时间步ts和ts+1时，为原属于ts+1的内存块重新分配LMEM地址，
 * 避免与ts时间步的内存块冲突，同时满足硬件bank和对齐约束，验证合并的内存可行性。
 * 
 * @param time_step 时间步指针（存储LMEM使用信息）
 * @param ts 当前时间步索引（待合并的前一时间步）
 * @return 若所有内存块成功分配则返回true（合并可行），否则返回false
 */
bool lmem_alloc_by_timestep_merge(BasicTimeStepPtr &time_step, int64_t ts) {
  // 待重新分配的内存块：按大小降序存储（key=内存块大小，value=（内存键，原地址））
  // gdma_mem：GDMA传输操作的内存块；npu_mem：NPU计算操作的内存块
  std::map<int64_t, std::pair<mem_buffer_key_t, int64_t>, std::greater<int64_t>>
      gdma_mem, npu_mem;

  // 已使用的内存块：key=起始地址，value=内存块大小
  // gdma_used_mem：GDMA操作已占用的内存；npu_used_mem：NPU操作已占用的内存
  std::map<int64_t, int64_t> gdma_used_mem, npu_used_mem;

  // 获取当前时间步（ts）和下一时间步（ts+1）的张量列表
  auto &cur_ts_tensors = time_step->getTensors(ts);
  auto &next_ts_tensors = time_step->getTensors(ts + 1);

  // 辅助函数：判断张量是否属于GDMA传输操作（通过张量在GDMA张量列表中是否存在）
  auto is_gdma = [](Value v, GdmaTsField gdma_field) {
    for (auto &iter : gdma_field) {
      if (iter.first == v) {
        return true;
      }
    }
    return false;
  };

  // 遍历LMEM缓冲区，分类内存块
  auto &lmem_buffer = time_step->get_lmem_buffer();
  for (auto &iter : lmem_buffer) {
    int64_t start_ts = iter.second.start_ts;  // 内存块开始使用的时间步
    int64_t end_ts = iter.second.end_ts;      // 内存块结束使用的时间步
    auto &mem_key = iter.first;               // 内存块标识（包含张量和操作类型）
    auto &mem_val = iter.second;              // 内存块信息（大小、地址、时间范围等）

    // 情况1：内存块原属于下一时间步（ts+1），需要重新分配
    if (start_ts == ts + 1) {
      if (mem_key.type == LMEM_OPERATION) {
        // 若为LMEM内部操作，归类为NPU计算内存块
        npu_mem[mem_val.size] = std::make_pair(mem_key, mem_val.addr);
      } else if (!time_step->is_tensor_hold_in_lmem(mem_key.value)) {
        // 若张量不常驻LMEM，按操作类型（GDMA/NPU）分类
        if (is_gdma(mem_key.value, next_ts_tensors)) {
          gdma_mem[mem_val.size] = std::make_pair(mem_key, mem_val.addr);
        } else {
          npu_mem[mem_val.size] = std::make_pair(mem_key, mem_val.addr);
        }
      }
    }
    // 情况2：内存块属于当前时间步（ts），标记为已使用
    else if ((ts >= start_ts && ts <= end_ts) ||  // 线性时间步范围内
             (start_ts == end_ts && ts == start_ts) ||  // 时间步无跨度
             (start_ts > end_ts && (ts >= start_ts || ts < end_ts))) {  // 环形时间步范围内
      if (mem_key.type == LMEM_OPERATION) {
        // LMEM内部操作，归类为NPU已使用内存
        npu_used_mem[mem_val.addr] = mem_val.size;
      } else if (is_gdma(mem_key.value, cur_ts_tensors)) {
        // GDMA操作，归类为GDMA已使用内存
        gdma_used_mem[mem_val.addr] = mem_val.size;
      } else {
        // NPU计算操作，归类为NPU已使用内存
        npu_used_mem[mem_val.addr] = mem_val.size;
      }
    }

    // 特殊情况：常驻LMEM的张量，归类为GDMA已使用内存
    if (mem_key.type != LMEM_OPERATION &&
        time_step->is_tensor_hold_in_lmem(mem_key.value)) {
      gdma_used_mem[mem_val.addr] = mem_val.size;
    }
  }

  // 记录已使用的LMEM bank（bank是LMEM的硬件分区，避免访问冲突）
  std::set<int64_t> used_banks;
  // 辅助函数：更新已使用的bank（根据内存地址计算所属bank）
  auto update_mem_bank = [&used_banks](std::map<int64_t, int64_t> &used_mem) {
    for (auto &iter : used_mem) {
      // 计算内存地址所属的bank（每个bank大小为Arch::LMEM_BANK_BYTES）
      int64_t bank_id = iter.first / Arch::LMEM_BANK_BYTES;
      used_banks.insert(bank_id);
      // 确保bank起始地址在used_mem中（便于后续计算空闲空间）
      if (used_mem.find(bank_id * Arch::LMEM_BANK_BYTES) == used_mem.end()) {
        used_mem[bank_id * Arch::LMEM_BANK_BYTES] = 0;
      }
    }
  };

  // 更新GDMA和NPU操作已使用的bank
  update_mem_bank(gdma_used_mem);
  update_mem_bank(npu_used_mem);

  // 计算未使用的bank（可用于新分配）
  std::list<int64_t> unused_banks;
  for (int64_t i = 0; i < Arch::LMEM_BANKS; ++i) {
    if (used_banks.count(i) == 0) {  // 若bank未被使用，加入未使用列表
      unused_banks.push_back(i);
    }
  }

  // 存储重新分配的内存块地址（key=内存块标识，value=新地址）
  std::map<mem_buffer_key_t, int64_t> reassign_buffers;

  // 辅助函数：为待分配内存块寻找合适的地址
  // 参数：待分配内存块、已使用内存、成功分配的计数
  auto assign_addr = [&](std::map<int64_t, std::pair<mem_buffer_key_t, int64_t>,
                                  std::greater<int64_t>> &mem_map,
                         std::map<int64_t, int64_t> &used_mem_map,
                         size_t &cnt) {
    // 遍历待分配内存块（按大小降序，优先分配大内存块）
    for (auto &iter : mem_map) {
      int64_t mem_size = iter.first;  // 待分配内存块大小
      auto &mem_key = iter.second.first;  // 内存块标识

      // 尝试在已使用bank的空闲空间中分配
      auto in_iter = used_mem_map.begin();
      for (; in_iter != used_mem_map.end();) {
        auto pre_iter = in_iter++;  // 前一个已使用内存块的地址和大小
        if (in_iter == used_mem_map.end()) {
          break;  // 已遍历完所有已使用内存块
        }

        // 计算当前bank的边界（当前bank的最大地址）
        int64_t bound = 0;
        if (in_iter == used_mem_map.end()) {
          // 若为最后一个内存块，边界为当前bank的结束地址
          bound = (pre_iter->first / Arch::LMEM_BANK_BYTES + 1) *
                  Arch::LMEM_BANK_BYTES;
        } else {
          // 若与下一个内存块同属一个bank，边界为下一个内存块的地址；否则为当前bank结束地址
          bound = (in_iter->first / Arch::LMEM_BANK_BYTES ==
                  pre_iter->first / Arch::LMEM_BANK_BYTES)
              ? in_iter->first
              : (pre_iter->first / Arch::LMEM_BANK_BYTES + 1) *
                    Arch::LMEM_BANK_BYTES;
        }

        // 计算空闲空间的起始地址（前一个内存块结束地址+对齐）
        int64_t offset = align_up(pre_iter->first + pre_iter->second, Arch::EU_BYTES);
        // 检查空闲空间是否足够容纳当前内存块
        if (mem_size + offset <= bound) {
          reassign_buffers[mem_key] = offset;  // 记录新地址
          used_mem_map[offset] = mem_size;     // 更新已使用内存
          cnt++;  // 成功分配计数+1
          break;
        }
      }

      // 若已用bank无空间，尝试使用未使用的bank
      if (!unused_banks.empty() && reassign_buffers.count(mem_key) == 0) {
        int64_t bank_id = unused_banks.front();  // 取第一个未使用的bank
        int64_t offset = bank_id * Arch::LMEM_BANK_BYTES;  // bank的起始地址
        // 检查bank空间是否足够
        if (offset + mem_size <= (bank_id + 1) * Arch::LMEM_BANK_BYTES) {
          reassign_buffers[mem_key] = offset;  // 记录新地址
          used_mem_map[offset] = mem_size;     // 更新已使用内存
          unused_banks.pop_front();  // 标记该bank为已使用
          cnt++;  // 成功分配计数+1
          break;
        }
      }
    }
  };

  // 为NPU和GDMA的待分配内存块分配地址
  size_t cnt0 = 0, cnt1 = 0;  // cnt0：NPU+GDMA成功分配数；cnt1：预留（实际复用cnt0）
  assign_addr(npu_mem, npu_used_mem, cnt0);  // 分配NPU内存块
  assign_addr(gdma_mem, gdma_used_mem, cnt0);  // 分配GDMA内存块

  // 验证是否所有待分配内存块都成功分配
  bool res = (cnt0 == npu_mem.size() + gdma_mem.size());  // 原代码cnt1逻辑有误，修正为总数量对比
  if (res) {
    // 若分配成功，更新LMEM中内存块的地址
    for (auto &iter : reassign_buffers) {
      time_step->set_lmem_addr(iter.first, iter.second);
    }
  }

  return res;  // 返回分配是否成功
}
```

### 9.update_mem_buffer_by_timestep_merge

#### 核心功能

`update_mem_buffer_by_timestep_merge` 是时间步合并后的内存缓冲区更新函数，用于在相邻时间步（如 `ts+1` 合并到 `ts`）后，同步更新内存缓冲区中所有内存块的时间范围（`start_ts` 和 `end_ts`），并处理常驻 LMEM 的张量，确保内存块的生命周期与合并后的时间步索引一致。其核心作用是：修正因时间步合并导致的时间索引偏移，调整常驻张量的位置，保证内存缓冲区信息与新的时间步结构匹配，为后续内存管理和计算提供准确的生命周期数据。

#### 核心逻辑

函数的逻辑围绕 “时间步索引修正 → 常驻张量调整” 展开，具体流程如下：

1. 遍历源内存缓冲区：遍历所有内存块（`src_mem_buffer`），针对每个内存块（`mem_key` 和 `mem_val`）执行处理。
2. 修正时间步索引：
   时间步合并后，原 `ts+1` 及之后的时间步索引会减 1（因 `ts+1` 被合并到 `ts`），因此需调整内存块的 `start_ts` 和 `end_ts`：

   - 对于 LMEM 内部操作（`mem_key.type == LMEM_OPERATION`）：若原时间步大于 `ts`，则 `start_ts` 和 `end_ts` 均减 1。
   - 对于其他操作：若原 `start_ts` 大于 `ts`，则 `start_ts` 减 1；同理调整 `end_ts`。
   - 特殊处理软件流水线场景：若原时间步是环形结构（`start_ts = end_ts + 1`），合并后需修正 `start_ts` 以保持环形逻辑。
3. 处理常驻 LMEM 的张量：
   若启用 `consider_hold_in_tensor`（需考虑常驻张量），且某张量常驻 LMEM（`is_tensor_hold_in_lmem`），则将其从当前时间步（`ts`）移动到第一个时间步（`ts=0`），避免因合并导致的内存冲突，确保常驻张量的生命周期与新时间步结构兼容。
4. 同步到目标缓冲区：将调整后的内存块信息存入 `dst_mem_buffer`，完成内存缓冲区的更新。

#### 核心原理

时间步合并会导致时间步索引的 “压缩”（如合并 `ts+1` 到 `ts` 后，原 `ts+2` 变为新的 `ts+1`），而内存缓冲区中内存块的 `start_ts` 和 `end_ts` 是基于原始时间步索引记录的，若不修正会导致：

- 内存块的生命周期与实际时间步不匹配（如原 `ts+1` 的内存块应对应新 `ts`）；
- 内存管理错误（如提前释放或延迟释放内存）。

通过修正时间步索引，确保内存块的生命周期与合并后的时间步对齐。对于常驻 LMEM 的张量（长期占用内存），将其移动到第一个时间步可避免因合并导致的跨时间步冲突，保证其在整个计算过程中稳定可用。

#### 4.update_mem_buffer_by_timestep_merge 代码

```cpp
/**
 * @brief 时间步合并后更新内存缓冲区，修正时间范围并处理常驻张量
 * 
 * 功能：在时间步ts+1合并到ts后，调整内存缓冲区中所有内存块的start_ts和end_ts，
 * 使其与合并后的时间步索引一致；同时处理常驻LMEM的张量，避免合并导致的内存冲突，
 * 确保内存生命周期信息准确。
 * 
 * @param time_step 时间步指针（提供时间步元信息）
 * @param ts_tensors_v GDMA张量的时间步分配列表（用于调整常驻张量位置）
 * @param src_mem_buffer 合并前的内存缓冲区（原始时间范围信息）
 * @param dst_mem_buffer 合并后的内存缓冲区（输出，更新后的时间范围信息）
 * @param ts 当前时间步索引（被合并的前一时间步）
 * @param consider_hold_in_tensor 是否处理常驻LMEM的张量
 */
static void update_mem_buffer_by_timestep_merge(
    BasicTimeStepPtr &time_step, std::vector<GdmaTsField> &ts_tensors_v,
    const MemBuff &src_mem_buffer, MemBuff &dst_mem_buffer, int64_t ts,
    bool consider_hold_in_tensor) {
  mem_buffer_key_t mem_key;  // 内存块标识（包含张量和操作类型）
  // 谓词：判断张量是否与当前内存块的张量相同
  auto is_the_same_value = [&](const GdmaElt &elt) {
    return elt.first == mem_key.value;
  };
  mem_buffer_value_t mem_val;  // 内存块信息（时间范围、大小等）

  // 遍历源内存缓冲区的所有内存块
  for (auto it = src_mem_buffer.begin(); it != src_mem_buffer.end(); it++) {
    mem_key = it->first;   // 当前内存块的标识
    mem_val = it->second;  // 当前内存块的原始时间范围信息

    // 情况1：处理LMEM内部操作的内存块
    if (mem_key.type == LMEM_OPERATION) {
      // 若原时间步大于ts（属于被合并的ts+1及之后），时间步索引减1
      if (mem_val.start_ts > ts) {
        mem_val.start_ts -= 1;  // 修正开始时间步
        mem_val.end_ts -= 1;    // 修正结束时间步
      }
    }
    // 情况2：处理其他操作的内存块
    else {
      // 修正开始时间步：若原start_ts > ts，减1（因ts+1被合并）
      mem_val.start_ts = mem_val.start_ts - (mem_val.start_ts > ts ? 1 : 0);
      // 修正结束时间步：若原end_ts > ts，减1
      mem_val.end_ts = mem_val.end_ts - (mem_val.end_ts > ts ? 1 : 0);

      // 特殊处理软件流水线场景（环形时间步：start_ts = end_ts + 1）
      if (it->second.start_ts == it->second.end_ts + 1 &&
          mem_val.start_ts == mem_val.end_ts) {
        // 修正start_ts为原start_ts（若原start_ts是最后一个时间步，重置为0）
        mem_val.start_ts =
            (it->second.start_ts >= (time_step->get_timestep_num() - 1)
                 ? 0
                 : it->second.start_ts);
      }
    }

    // 情况3：处理常驻LMEM的张量（若启用consider_hold_in_tensor）
    if (mem_key.type != LMEM_OPERATION && consider_hold_in_tensor &&
        time_step->is_tensor_hold_in_lmem(mem_key.value)) {
      // 若当前时间步ts > 0，且内存块时间范围是线性的（end_ts > start_ts）
      if (ts > 0 && it->second.end_ts > it->second.start_ts) {
        // 在当前时间步（ts）的张量列表中找到该张量
        auto iter = std::find_if(ts_tensors_v[ts].begin(),
                                 ts_tensors_v[ts].end(), is_the_same_value);
        if (iter != ts_tensors_v[ts].end()) {
          // 将张量移动到第一个时间步（ts=0），避免合并冲突
          ts_tensors_v[0].push_back(*iter);
          ts_tensors_v[ts].erase(iter);  // 从当前时间步移除
          mem_val.start_ts = 0;  // 更新内存块开始时间步为0
        }
      }
    }

    // 将调整后的内存块信息存入目标缓冲区
    dst_mem_buffer[mem_key] = mem_val;
  }
}
```

### 10.update_cycle_info

#### 核心功能

`update_cycle_info` 是时间步周期信息更新函数，用于在时间步结构发生变化（如合并、移动张量）后，重新计算每个时间步的总 TPU 计算周期（`total_layer_cycle_v`）和总 GDMA 传输周期（`total_gdma_cycle_v`）。其核心作用是：确保周期统计数据与当前时间步的层和张量分配状态一致，为后续的负载均衡优化（如时间步合并、张量移动）提供准确的周期基准。

#### 核心逻辑

函数的逻辑围绕 “初始化周期容器 → 遍历时间步 → 累加周期” 展开，具体流程如下：

1. 初始化与参数准备：

   - 判断是否为单循环模式（`one_loop`）：根据形状分段信息（`shape_secs`）的乘积是否为 1，确定是否为简单时序模型。
   - 获取总时间步数量（`ts_num`），清空并初始化周期容器（`total_gdma_cycle_v` 和 `total_layer_cycle_v`），确保其大小与时间步数量一致，初始值为 0。
   - 提取各张量的 GDMA 传输周期（`gdma_cycle`）和各层的 TPU 计算周期（`layer_cycle`），作为累加的基础数据。
2. 遍历时间步累加周期：

   - 对每个时间步（`ts`）：
     - 计算总 TPU 周期：遍历当前时间步包含的所有层（`cur_layers`），累加每层的计算周期（`layer_cycle`）到 `total_layer_cycle_v[ts]`。
     - 计算总 GDMA 周期：遍历当前时间步包含的所有张量（`cur_tensors`），累加每个张量的传输周期（`gdma_cycle`）到 `total_gdma_cycle_v[ts]`，但忽略 “常驻 LMEM 且非单循环模式” 的张量（这类张量无需重复传输，不产生额外周期）。
3. 结果：`total_layer_cycle_v` 和 `total_gdma_cycle_v` 分别存储每个时间步的总计算和传输周期，反映当前时间步的负载状态。

#### 核心原理

周期统计是时间步优化的 “量化基准”，其原理基于：

- 负载均衡的量化评估：每个时间步的计算周期（TPU）和传输周期（GDMA）的比值决定了负载瓶颈（计算密集或传输密集）。`update_cycle_info` 通过准确累加周期，为后续优化（如判断是否需要合并时间步）提供数据支持。
- 特殊张量的周期豁免：常驻 LMEM 的张量（`is_tensor_hold_in_lmem`）已提前加载到本地内存，无需在每个时间步重复传输，因此在非单循环模式下不计入 GDMA 周期，避免统计误差。
- 动态适配性：当时间步结构变化（如合并、张量移动）时，层和张量的分配关系改变，周期统计必须同步更新才能反映真实负载，否则后续优化会基于错误数据导致决策失效。

#### 4.update_cycle_info 代码

```cpp
/**
 * @brief 更新每个时间步的总计算周期和传输周期，反映当前时间步的负载状态
 * 
 * 功能：在时间步结构变化（如合并、张量移动）后，重新计算每个时间步的
 * 总TPU计算周期（total_layer_cycle_v）和总GDMA传输周期（total_gdma_cycle_v），
 * 为负载均衡优化提供准确的周期基准。
 * 
 * @param total_gdma_cycle_v 输出参数，每个时间步的总GDMA传输周期
 * @param total_layer_cycle_v 输出参数，每个时间步的总TPU计算周期
 * @param time_step 时间步指针（提供当前时间步的层和张量分配信息）
 * @param shape_secs 形状分段信息（用于判断是否为单循环模式）
 */
static void update_cycle_info(std::vector<int64_t> &total_gdma_cycle_v,
                              std::vector<int64_t> &total_layer_cycle_v,
                              const BasicTimeStepPtr &time_step,
                              const shape_secs_t &shape_secs) {
  // 判断是否为单循环模式（形状分段乘积为1，时序结构简单）
  bool one_loop = (shape_secs.nsecs * shape_secs.hsecs * shape_secs.dsecs *
                       shape_secs.wsecs ==
                   1);
  int64_t ts_num = time_step->get_timestep_num();  // 总时间步数量

  // 初始化周期容器：清空并重置为与时间步数量一致的大小，初始值为0
  total_gdma_cycle_v.clear();
  total_layer_cycle_v.clear();
  total_gdma_cycle_v.resize(ts_num, 0);
  total_layer_cycle_v.resize(ts_num, 0);

  // 获取各张量的GDMA传输周期（key=张量标识，value=传输周期）
  ValueIntMap gdma_cycle = time_step->get_gdma_cycle();
  // 获取各层的TPU计算周期（key=层操作，value=计算周期）
  std::map<Operation *, int64_t> layer_cycle = time_step->get_layer_cycle();

  // 遍历每个时间步，计算总周期
  for (int64_t ts = 0; ts < ts_num; ++ts) {
    // 累加当前时间步的总TPU计算周期
    const TpuTsField &cur_layers = time_step->getLayers(ts);  // 当前时间步包含的层
    for (size_t i = 0; i < cur_layers.size(); ++i) {
      // 断言：层的计算周期必须存在（避免无效数据）
      assert(layer_cycle.find(cur_layers[i]) != layer_cycle.end());
      // 累加层的计算周期到当前时间步的总计算周期
      total_layer_cycle_v[ts] += layer_cycle[cur_layers[i]];
    }

    // 累加当前时间步的总GDMA传输周期
    const GdmaTsField &cur_tensors = time_step->getTensors(ts);  // 当前时间步包含的张量
    for (size_t i = 0; i < cur_tensors.size(); ++i) {
      auto value = cur_tensors[i].first;  // 张量标识
      // 断言：张量的传输周期必须存在（避免无效数据）
      assert(gdma_cycle.find(value) != gdma_cycle.end());

      // 特殊情况：常驻LMEM的张量且非单循环模式，不计入传输周期（无需重复传输）
      if (time_step->is_tensor_hold_in_lmem(value) && !one_loop) {
        continue;
      }

      // 累加张量的传输周期到当前时间步的总传输周期
      total_gdma_cycle_v[ts] += gdma_cycle[value];
    }
  }
}
```

## 11./LayerGroup/GroupOverlap.cpp

### direct_group_overlap_schd

#### 核心功能

`direct_group_overlap_schd` 是相邻层组间的直接重叠调度函数，用于实现深度学习模型中相邻层组（Layer Group）之间数据传输与计算的并行重叠。其核心作用是：通过识别相邻层组（上一层组 `i-1` 与当前层组 `i`）中可并行的操作，计算重叠深度（可并行的时间范围），并分配对应的时间步，使上一层组的计算与下一层组的数据传输（或反之）重叠执行，从而减少整体耗时，提升硬件资源利用率。

#### 核心逻辑

函数的逻辑围绕 “相邻层组遍历 → 可重叠操作识别 → 重叠资源准备 → 重叠深度计算 → 时间步分配” 展开，具体流程如下：

1. 初始化与层组筛选：

   - 遍历所有相邻层组（`i` 从 1 到 `group_num-1`），其中 `i-1` 为上一层组（`up_group`），`i` 为当前层组（`down_group`）。
   - 筛选有效层组：若上一层组或当前层组的操作数过少（`group_ops.size() <= 1`），则跳过（操作太少，重叠收益有限）。
2. 识别可重叠操作：

   - 调用 `find_alter_overlap_op` 函数，从上下层组中识别可重叠的备选操作，分别存入 `up_group_overlap_op`（上组可与下组重叠的操作）和 `down_group_overlap_op`（下组可与上组重叠的操作）。这些操作通常是数据传输（如 GDMA）与计算（如 TPU 层）中无依赖冲突的部分。
3. 下到上（down-to-up）重叠调度：

   - 资源准备：收集下组重叠操作涉及的张量（`down_to_up_tensor`）及其在 LMEM 中的内存块信息（`down_to_up_overlap_buffer`，包含地址和大小）。
   - 计算重叠深度：通过 `up_overlap_depth` 计算上组可与下组重叠的深度（即并行执行的时间范围）。
   - 分配重叠时间步：调用 `assign_down_to_up_overlap_timestep`，根据重叠深度为下组到上组的重叠操作分配时间步，实现下组数据传输与上组计算的并行。
4. 上到下（up-to-down）重叠调度：

   - 资源准备：类似地，收集上组重叠操作涉及的张量（`up_to_down_tensor`）及其 LMEM 内存块（`up_to_down_overlap_buffer`）。
   - 计算重叠深度：通过 `down_overlap_depth` 计算下组可与上组重叠的深度。
   - 分配重叠时间步：调用 `assign_up_to_down_overlap_timestep`，为上组到下组的重叠操作分配时间步，实现上组数据传输与下组计算的并行。

#### 核心原理

该函数的优化基于 “层组间并行性挖掘” 原理：

- 硬件资源独立性：深度学习加速器中，计算单元（如 TPU）和数据传输单元（如 GDMA）是独立的硬件模块，可同时工作。若能让上一层组的计算与下一层组的数据传输（或反之）并行，可减少整体耗时。
- 依赖感知：层组间存在数据依赖（如下组的输入可能是上组的输出），但并非所有操作都存在依赖。`find_alter_overlap_op` 通过识别无依赖的操作，为重叠提供可能。
- 重叠深度：指两个层组可并行执行的时间长度（时间步数量），由 `up_overlap_depth` 和 `down_overlap_depth` 计算，受内存容量（LMEM 是否能同时容纳重叠数据）和操作耗时约束。
- 时间步分配：通过 `assign_*_overlap_timestep` 将重叠操作映射到具体时间步，确保并行操作在时间上不冲突，最终实现 “计算与传输重叠” 的调度目标。

#### 4.direct_group_overlap_schd 代码

```cpp
/**
 * @brief 相邻层组间的直接重叠调度，实现数据传输与计算的并行执行
 * 
 * 功能：遍历相邻层组，识别可重叠的操作，计算重叠深度，分配时间步，
 * 使上一层组与当前层组的计算和数据传输重叠进行，提升硬件资源利用率。
 * 
 * @param time_steps 各层组的时间步指针向量（记录层组内的时间调度信息）
 * @param lg_infos 层组信息向量（包含层组的操作、依赖等信息）
 * @param shape_secs 形状分段信息向量（各层组的张量形状与分段规则）
 * @param dynamic_compile 是否动态编译（影响重叠操作的筛选逻辑）
 */
static void
direct_group_overlap_schd(std::vector<BasicTimeStepPtr> &time_steps,
                          const std::vector<LgInfo> &lg_infos,
                          const std::vector<shape_secs_t> &shape_secs,
                          bool dynamic_compile) {
  int64_t group_num = lg_infos.size();  // 层组总数
  BasicTimeStepPtr up_time_step;        // 上一层组的时间步指针
  BasicTimeStepPtr down_time_step;      // 当前层组的时间步指针

  // 上/下层组中可重叠的操作：<张量标识, 时间步索引>
  std::vector<std::pair<Value, int64_t>> up_group_overlap_op;
  std::vector<std::pair<Value, int64_t>> down_group_overlap_op;

  // 上下层组间重叠传输的内存块：<LMEM地址, LMEM大小>
  std::vector<MemBlock> down_to_up_overlap_buffer;  // 下层到上层的内存块
  std::vector<MemBlock> up_to_down_overlap_buffer;  // 上层到下层的内存块

  // 上下层组间重叠传输的张量
  std::vector<Value> down_to_up_tensor;  // 下层到上层的张量
  std::vector<Value> up_to_down_tensor;  // 上层到下层的张量

  MemBlock lmem_locate;  // 张量在LMEM中的位置信息（地址+大小）
  Value tensor;          // 临时变量：当前处理的张量
  int64_t timestep_idx;  // 临时变量：张量所在的时间步索引

  // 遍历相邻层组（i为当前层组，i-1为上一层组）
  for (int64_t i = 1; i < group_num; ++i) {
    // 跳过操作数过少的层组（操作太少，重叠收益有限）
    if (lg_infos[i - 1].group_ops.size() <= 1 ||
        lg_infos[i].group_ops.size() <= 1) {
      continue;
    }

    // 初始化当前迭代的层组和时间步
    up_time_step = time_steps[i - 1];    // 上一层组的时间步
    down_time_step = time_steps[i];      // 当前层组的时间步
    const LgInfo &up_group = lg_infos[i - 1];  // 上一层组信息
    const LgInfo &down_group = lg_infos[i];    // 当前层组信息
    const shape_secs_t &up_secs = shape_secs[i - 1];  // 上一层组的形状分段
    const shape_secs_t &down_secs = shape_secs[i];    // 当前层组的形状分段

    // 清空上一轮的重叠操作记录
    up_group_overlap_op.clear();
    down_group_overlap_op.clear();

    // 步骤1：识别上下层组中可重叠的备选操作
    // （无数据依赖，可并行执行的计算或传输操作）
    find_alter_overlap_op(
        up_group, up_time_step, up_secs, up_group_overlap_op,
        down_group, down_time_step, down_secs, down_group_overlap_op,
        dynamic_compile  // 动态编译标志，影响操作筛选逻辑
    );

    // 步骤2：处理下层组到上层组的重叠（down-to-up）
    down_to_up_overlap_buffer.clear();  // 清空上一轮的内存块记录
    down_to_up_tensor.clear();          // 清空上一轮的张量记录

    // 收集下层组重叠操作涉及的张量及其LMEM位置
    for (size_t j = 0; j < down_group_overlap_op.size(); ++j) {
      tensor = down_group_overlap_op[j].first;       // 重叠操作的张量
      timestep_idx = down_group_overlap_op[j].second;  // 张量所在时间步
      // 获取该张量在LMEM中的位置（地址和大小）
      lmem_locate = down_time_step->get_lmem_locate(tensor, timestep_idx);
      down_to_up_overlap_buffer.push_back(lmem_locate);  // 记录内存块
      down_to_up_tensor.push_back(tensor);                // 记录张量
    }

    // 计算上层组可与下层组重叠的深度（可并行的时间范围）
    auto down_to_up_depth = up_overlap_depth(
        up_time_step, down_to_up_overlap_buffer, down_to_up_tensor,
        up_group, up_secs
    );

    // 根据重叠深度，为下层到上层的重叠操作分配时间步
    assign_down_to_up_overlap_timestep(
        up_group, up_secs, up_time_step,
        down_group, down_secs, down_time_step,
        down_group_overlap_op, down_to_up_depth, down_to_up_overlap_buffer
    );

    // 步骤3：处理上层组到下层组的重叠（up-to-down）
    up_to_down_overlap_buffer.clear();  // 清空上一轮的内存块记录
    up_to_down_tensor.clear();          // 清空上一轮的张量记录

    // 收集上层组重叠操作涉及的张量及其LMEM位置
    for (size_t j = 0; j < up_group_overlap_op.size(); ++j) {
      tensor = up_group_overlap_op[j].first;       // 重叠操作的张量
      timestep_idx = up_group_overlap_op[j].second;  // 张量所在时间步
      // 获取该张量在LMEM中的位置（地址和大小）
      lmem_locate = up_time_step->get_lmem_locate(tensor, timestep_idx);
      up_to_down_overlap_buffer.push_back(lmem_locate);  // 记录内存块
      up_to_down_tensor.push_back(tensor);                // 记录张量
    }

    // 计算下层组可与上层组重叠的深度（可并行的时间范围）
    auto up_to_down_depth = down_overlap_depth(
        down_time_step, up_to_down_overlap_buffer, up_to_down_tensor
    );

    // 根据重叠深度，为上层到下层的重叠操作分配时间步
    assign_up_to_down_overlap_timestep(
        up_group, up_secs, up_time_step,
        down_group, down_secs, down_time_step,
        up_group_overlap_op, up_to_down_depth, up_to_down_overlap_buffer
    );
  }
}
```
