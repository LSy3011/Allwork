# cgra的设计记录

## 1\. 两个pass的设计

### 一、 Pass 1: 多维静态特征提取 \(`StaticFeatureExtractionPass`\)

**功能**：分析 IR 代码，提取向量化率、算术强度、循环深度、缓冲区需求等关键性能指标。

#### C\+\+ 源代码

**文件路径**：`lib/NeuraDialect/Transforms/StaticFeatureExtractionPass.cpp`

```C++
#include "NeuraDialect/NeuraPasses.h"
#include "NeuraDialect/NeuraDialect.h"
#include "mlir/Dialect/Affine/IR/AffineOps.h"
#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/Dialect/MemRef/IR/MemRef.h"
#include "mlir/Dialect/SCF/IR/SCF.h"
#include "mlir/Dialect/Vector/IR/VectorOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/Pass/Pass.h"
#include "llvm/Support/Debug.h"
#include "llvm/Support/Format.h"
#include <iostream>
#include <numeric>

#define DEBUG_TYPE "static-feature-extraction"

namespace mlir {
namespace neura {

struct StaticFeatureExtractionPass
    : public PassWrapper<StaticFeatureExtractionPass, OperationPass<func::FuncOp>> {
  
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(StaticFeatureExtractionPass)

  void getDependentDialects(DialectRegistry &registry) const override {
    registry.insert<affine::AffineDialect, 
                    memref::MemRefDialect, 
                    arith::ArithDialect,
                    vector::VectorDialect>();
  }

  StringRef getArgument() const override { return "static-feature-extraction"; }

  // 辅助函数：计算 Loop Depth
  unsigned getLoopDepth(Operation *op) {
    unsigned depth = 0;
    Operation *curr = op->getParentOp();
    while (curr) {
      if (isa<affine::AffineForOp>(curr) || isa<scf::ForOp>(curr)) {
        depth++;
      }
      curr = curr->getParentOp();
    }
    return depth;
  }

  // 辅助函数：估算操作的 Latency
  unsigned getLatencyEstimate(Operation *op) {
    if (isa<arith::AddFOp, arith::AddIOp>(op)) return 1;
    if (isa<arith::MulFOp, arith::MulIOp>(op)) return 2;
    if (isa<arith::DivFOp, arith::DivSIOp>(op)) return 10;
    if (isa<memref::LoadOp, affine::AffineLoadOp>(op)) return 4;
    return 1;
  }

  void runOnOperation() override {
    func::FuncOp func = getOperation();
    
    // --- 统计计数器 ---
    long long n_vec = 0;
    long long n_scalar = 0;
    long long n_compute = 0;
    long long n_memory = 0;
    long long n_indexed_mem = 0;
    long long n_total_mem_ops = 0;
    long long n_load = 0;
    long long n_store = 0;
    long long s_buffer_bits = 0;
    unsigned max_loop_depth = 0;
    long long total_fan_out = 0;
    long long n_ops_for_fanout = 0;
    
    func.walk([&](Operation *op) {
      if (op == func || op->hasTrait<OpTrait::IsTerminator>()) return;

      // 1. 向量化率
      bool isVec = false;
      for (auto resultType : op->getResultTypes()) {
        if (isa<VectorType>(resultType)) {
          isVec = true;
          break;
        }
      }
      if (isVec) n_vec++;
      else n_scalar++;

      // 2. 算术强度 & 访存特征
      bool isMemoryOp = false;
      if (op->getDialect()->getNamespace() == "arith" || 
          op->getDialect()->getNamespace() == "math") {
         n_compute++;
      }

      if (isa<affine::AffineLoadOp, affine::AffineStoreOp>(op)) {
        n_indexed_mem++;
        n_total_mem_ops++;
        n_memory++;
        isMemoryOp = true;
        if (isa<affine::AffineLoadOp>(op)) n_load++;
        else n_store++;
      } 
      else if (isa<memref::LoadOp, memref::StoreOp>(op)) {
        n_total_mem_ops++;
        n_memory++;
        isMemoryOp = true;
        if (isa<memref::LoadOp>(op)) n_load++;
        else n_store++;
      }

      // 3. 循环计算深度
      if (!isMemoryOp && !isa<affine::AffineForOp, scf::ForOp>(op)) {
        unsigned depth = getLoopDepth(op);
        if (depth > max_loop_depth) max_loop_depth = depth;
      }

      // 5. 静态缓冲区需求 (同时统计栈和堆)
      if (auto allocaOp = dyn_cast<memref::AllocaOp>(op)) {
        auto type = allocaOp.getType();
        if (type.hasStaticShape()) {
          s_buffer_bits += type.getNumElements() * type.getElementTypeBitWidth();
        }
      } else if (auto allocOp = dyn_cast<memref::AllocOp>(op)) {
        auto type = allocOp.getType();
        if (type.hasStaticShape()) {
          s_buffer_bits += type.getNumElements() * type.getElementTypeBitWidth();
        }
      }

      // 8. 平均扇出度
      if (op->getNumResults() > 0) {
        n_ops_for_fanout++;
        for (auto result : op->getResults()) {
          total_fan_out += std::distance(result.use_begin(), result.use_end());
        }
      }
    });

    // 7. RecMII
    double max_rec_mii = 0.0;
    func.walk([&](affine::AffineForOp forOp) {
       unsigned loop_body_latency = 0;
       forOp.getBody()->walk([&](Operation *innerOp) {
          loop_body_latency = std::max(loop_body_latency, getLatencyEstimate(innerOp));
       });
       double current_mii = (double)loop_body_latency / 1.0; 
       if (current_mii > max_rec_mii) max_rec_mii = current_mii;
    });

    // 9. 活跃变数
    long long n_live_in = func.getArguments().size();
    long long n_live_out = func.getFunctionType().getNumResults();
    long long v_live = n_live_in + n_live_out;

    // --- 计算最终指标 ---
    double r_vec = (double)n_vec / (n_vec + n_scalar + 1e-9);
    double i_arith = (double)n_compute / (n_memory + 1e-9);
    double r_affine = (double)n_indexed_mem / (n_total_mem_ops + 1e-9);
    double r_ls = (double)n_load / (n_store + 1e-9);
    double fan_avg = (double)total_fan_out / (n_ops_for_fanout + 1e-9);
    
    // --- 输出结果到 stderr ---
    llvm::errs() << "--------------------------------------------------\n";
    llvm::errs() << "Static Features for Function: " << func.getName() << "\n";
    llvm::errs() << "--------------------------------------------------\n";
    llvm::errs() << llvm::format("R_vec (Vectorization Rate):    %.2f\n", r_vec);
    llvm::errs() << llvm::format("I_arith (Arithmetic Intensity):%.2f\n", i_arith);
    llvm::errs() << llvm::format("Loop Depth:                    %u\n", max_loop_depth);
    llvm::errs() << llvm::format("R_affine (Regular Mem Access): %.2f\n", r_affine);
    llvm::errs() << llvm::format("S_buffer (Static Buffer Bits): %lld\n", s_buffer_bits);
    llvm::errs() << llvm::format("R_L/S (Load/Store Ratio):      %.2f\n", r_ls);
    llvm::errs() << llvm::format("RecMII (Estimate):             %.2f\n", max_rec_mii);
    llvm::errs() << llvm::format("Fan_avg (Avg Fan-out):         %.2f\n", fan_avg);
    llvm::errs() << llvm::format("V_live (Live Variables):       %lld\n", v_live);
    llvm::errs() << "--------------------------------------------------\n";
  }
};

std::unique_ptr<OperationPass<func::FuncOp>> createStaticFeatureExtractionPass() {
  return std::make_unique<StaticFeatureExtractionPass>();
}

} // namespace neura
} // namespace mlir
```

### 二、 Pass 2: Task 依赖关系判断 \(`TaskDependencyAnalysisPass`\)

**功能**：分析顶层循环任务之间的数据依赖，判断依赖类型（Streaming/Independent/Conflict），生成 DOT 可视化代码，并**将分析结果（TaskID, Successors）作为 Attribute 写入 IR**。

#### C\+\+ 源代码

**文件路径**：`lib/NeuraDialect/Transforms/TaskDependencyAnalysisPass.cpp`

```C++
#include "NeuraDialect/NeuraPasses.h"
#include "NeuraDialect/NeuraDialect.h"
#include "mlir/Dialect/Affine/IR/AffineOps.h"
#include "mlir/Dialect/MemRef/IR/MemRef.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/Dialect/SCF/IR/SCF.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Matchers.h"
#include "mlir/Pass/Pass.h"
#include "llvm/Support/Debug.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/Support/Format.h"
#include "llvm/Support/raw_ostream.h"
#include <iostream>
#include <vector>
#include <string>
#include <map>

namespace mlir {
namespace neura {

// 定义一个结构体来描述一个 Task
struct TaskInfo {
  int id;                 // 任务编号
  Operation* op;          // 对应的顶层 Loop Operation
  llvm::DenseSet<Value> readBuffers;   // 读取的内存集合
  llvm::DenseSet<Value> writtenBuffers; // 写入的内存集合
  
  // 存储分析出的后继任务ID
  std::vector<int> streamingSuccs; // 适合融合/流式的后继
  std::vector<int> conflictSuccs;  // 必须串行等待的后继
};

class TaskDependencyAnalysisPass
    : public PassWrapper<TaskDependencyAnalysisPass, OperationPass<func::FuncOp>> {
public:
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(TaskDependencyAnalysisPass)

  StringRef getArgument() const override { return "task-dependency-analysis"; }
  StringRef getDescription() const override { return "Analyze dependencies and annotate tasks with relationships"; }

  void getDependentDialects(DialectRegistry &registry) const override {
    registry.insert<affine::AffineDialect, memref::MemRefDialect, scf::SCFDialect>();
  }

  void collectMemoryAccess(Operation *op, TaskInfo &info) {
    op->walk([&](Operation *innerOp) {
      if (auto loadOp = dyn_cast<affine::AffineLoadOp>(innerOp)) {
        info.readBuffers.insert(loadOp.getMemRef());
      } else if (auto loadOp = dyn_cast<memref::LoadOp>(innerOp)) {
        info.readBuffers.insert(loadOp.getMemRef());
      } else if (auto storeOp = dyn_cast<affine::AffineStoreOp>(innerOp)) {
        info.writtenBuffers.insert(storeOp.getMemRef());
      } else if (auto storeOp = dyn_cast<memref::StoreOp>(innerOp)) {
        info.writtenBuffers.insert(storeOp.getMemRef());
      }
    });
  }

  void runOnOperation() override {
    func::FuncOp func = getOperation();
    OpBuilder builder(&getContext()); // 用于创建属性

    llvm::errs() << "==================================================================\n";
    llvm::errs() << "Task Analysis: Analyzing & Annotating IR for " << func.getName() << "\n";
    llvm::errs() << "==================================================================\n";

    // --- 1. 识别 Tasks 并分配 ID ---
    std::vector<TaskInfo> tasks;
    int taskIdCounter = 0;

    for (Operation &op : func.getBody().front()) {
      if (isa<affine::AffineForOp>(op) || isa<scf::ForOp>(op)) {
        TaskInfo info;
        info.id = taskIdCounter++;
        info.op = &op;
        collectMemoryAccess(&op, info);
        tasks.push_back(info);
      }
    }

    if (tasks.empty()) return;

    // --- 2. 分析依赖关系并记录 ---
    for (size_t i = 0; i < tasks.size(); ++i) {
      for (size_t j = i + 1; j < tasks.size(); ++j) {
        auto &taskA = tasks[i];
        const auto &taskB = tasks[j];

        bool isFlowDep = false;   // RAW (Streaming)
        bool isConflict = false;  // WAW or WAR

        // Check Flow
        for (Value val : taskA.writtenBuffers) {
          if (taskB.readBuffers.count(val)) { isFlowDep = true; break; }
        }
        // Check Conflict (Output or Anti)
        if (!isFlowDep) { 
            for (Value val : taskA.writtenBuffers) {
                if (taskB.writtenBuffers.count(val)) { isConflict = true; break; }
            }
            if (!isConflict) {
                for (Value val : taskA.readBuffers) {
                    if (taskB.writtenBuffers.count(val)) { isConflict = true; break; }
                }
            }
        }

        // 记录关系到 TaskA 的数据结构中
        if (isFlowDep) {
            taskA.streamingSuccs.push_back(taskB.id);
        } else if (isConflict) {
            taskA.conflictSuccs.push_back(taskB.id);
        }
      }
    }

    // --- 3. 将分析结果写入 IR (Annotate) ---
    for (auto &task : tasks) {
        Operation* op = task.op;
        
        // 3.1 写入 Task ID
        op->setAttr("neura.task_id", builder.getI32IntegerAttr(task.id));

        // 3.2 写入 Streaming Successors (适合融合的目标)
        if (!task.streamingSuccs.empty()) {
            SmallVector<Attribute> succAttrs;
            for (int id : task.streamingSuccs) {
                succAttrs.push_back(builder.getI32IntegerAttr(id));
            }
            op->setAttr("neura.streaming_succs", builder.getArrayAttr(succAttrs));
        }

        // 3.3 写入 Conflict Successors (必须等待的目标)
        if (!task.conflictSuccs.empty()) {
            SmallVector<Attribute> conflictAttrs;
            for (int id : task.conflictSuccs) {
                conflictAttrs.push_back(builder.getI32IntegerAttr(id));
            }
            op->setAttr("neura.waiting_succs", builder.getArrayAttr(conflictAttrs));
        }
    }

    // --- 4. 生成 DOT 图 (保持可视化功能) ---
    std::string dotOutput;
    llvm::raw_string_ostream dotStream(dotOutput);
    dotStream << "digraph TaskGraph {\n  rankdir=TB;\n  node [shape=box, style=filled, fillcolor=white];\n";
    
    for (const auto& t : tasks) {
        dotStream << "  Task" << t.id << " [label=\"Task " << t.id << "\\n(" << t.op->getName().getStringRef() << ")\"];\n";
    }
    for (const auto& t : tasks) {
        for (int succId : t.streamingSuccs) {
             llvm::errs() << llvm::format("Edge: Task %2d -> Task %2d : [STREAMING] (Flow)\n", t.id, succId);
             dotStream << "  Task" << t.id << " -> Task" << succId << " [color=\"#006400\", penwidth=2.0, label=\"Flow\"];\n";
        }
        for (int succId : t.conflictSuccs) {
             llvm::errs() << llvm::format("Edge: Task %2d -> Task %2d : [DEPENDENT] (Conflict)\n", t.id, succId);
             dotStream << "  Task" << t.id << " -> Task" << succId << " [color=red, style=dashed, label=\"Conflict\"];\n";
        }
        // Independent 不画线
    }
    dotStream << "}\n";

    llvm::errs() << "IR has been annotated with 'neura.task_id', 'neura.streaming_succs', etc.\n";
    llvm::errs() << "\n[DOT Graph Code below - Copy to .dot file for visualization]\n" << dotOutput << "\n";
  }
};

std::unique_ptr<OperationPass<func::FuncOp>> createTaskDependencyAnalysisPass() {
  return std::make_unique<TaskDependencyAnalysisPass>();
}

} // namespace neura
} // namespace mlir
```

### 三、 必需的集成配置 \(Integration\)

为了让上述代码工作，你需要修改以下文件：

#### 1\. `include/NeuraDialect/NeuraPasses.td` \(注册 Pass\)

添加：

```Bash
def StaticFeatureExtraction : Pass<"static-feature-extraction", "func::FuncOp"> {
  let summary = "Extract static features";
  let constructor = "mlir::neura::createStaticFeatureExtractionPass()";
}

def TaskDependencyAnalysis : Pass<"task-dependency-analysis", "func::FuncOp"> {
  let summary = "Analyze dependencies between tasks";
  let constructor = "mlir::neura::createTaskDependencyAnalysisPass()";
}
```

#### 2\. `include/NeuraDialect/NeuraPasses.h` \(C\+\+ 声明\)

添加：

```C++
#include "mlir/Dialect/Func/IR/FuncOps.h" // 必须包含
// ...
namespace mlir {
namespace neura {
// ...
std::unique_ptr<OperationPass<func::FuncOp>> createStaticFeatureExtractionPass();
std::unique_ptr<OperationPass<func::FuncOp>> createTaskDependencyAnalysisPass();
// ...
}
}
```

#### 3\. `lib/NeuraDialect/Transforms/CMakeLists.txt` \(构建列表\)

添加：

```C++
add_mlir_library(MLIRNeuraTransforms
  # ...
  StaticFeatureExtractionPass.cpp
  TaskDependencyAnalysisPass.cpp
  # ...
)
```

#### 4\. `tools/mlir-neura-opt/mlir-neura-opt.cpp` \(工具入口\)

**必须包含所有用到的 Dialect 和 registerAllPasses**：

```C++
#include "mlir/Dialect/Bufferization/IR/Bufferization.h"
#include "mlir/Dialect/Vector/IR/VectorOps.h"
#include "mlir/Dialect/Tensor/IR/Tensor.h"
#include "mlir/Dialect/Math/IR/Math.h"
// ...
int main(...) {
  // ...
  mlir::DialectRegistry registry;
  // ... 注册 Neura ...
  registry.insert<mlir::bufferization::BufferizationDialect>();
  registry.insert<mlir::vector::VectorDialect>();
  registry.insert<mlir::tensor::TensorDialect>();
  registry.insert<mlir::math::MathDialect>();
  
  mlir::neura::registerPasses();
  mlir::registerAllPasses(); // <--- 关键：注册官方向量化等 Pass
  // ...
}
```

### 四、 测试文件修正 \(`bert_affine.mlir`\)

由于 LLVM 版本差异，必须修正测试文件中的语法。

1. **Bufferization**

- 旧：`to_memref %val : memref<...>`

- 新：`to_memref %val : tensor<...> to memref<...>`

2. **Tensor**

- 旧：`tensor.expand_shape ... into ...`

- 新：`tensor.expand_shape ... output_shape [...] : ... into ...`

### 五、 构建与运行命令

#### 编译

```C++
cd ~/workspace/dataflow/build
ninja mlir-neura-opt
```

#### 运行特征提取 \(Feature Extraction\)

*使用官方向量化 Pass 预处理，并查看统计结果：*

```C++
./tools/mlir-neura-opt/mlir-neura-opt \
  -affine-super-vectorize="virtual-vector-size=4" \
  --static-feature-extraction \
  ../test/samples/bert/bert_affine.mlir > /dev/null
```

*\(注：**`> /dev/null`** 用于屏蔽 IR 代码输出，统计信息会显示在 stderr\)*

#### 运行任务分析 \(Dependency Analysis\)

*查看依赖关系并生成图，同时保存标注后的 IR 到文件：*

```C++
./tools/mlir-neura-opt/mlir-neura-opt \
  --task-dependency-analysis \
  ../test/samples/bert/bert_affine.mlir > annotated_bert.mlir
```

#### 生成可视化图

将 `annotated_bert.mlir` 运行输出中 `[DOT Graph Code]` 下方的代码块复制到文件 `graph.dot`，然后运行：

```C++
dot -Tpng graph.dot -o graph.png
```

## 2\.基于收益模型的空间编排器

这是一个关于 **Pass 3: 基于收益模型的空间编排 \(ProfitBasedSpatialOrchestrationPass\)** 的完整总结。

这个 Pass 是编译器的核心决策组件，它负责将抽象的计算任务映射到具体的硬件资源上。它不仅要考虑如何放置任务（空间），还要考虑任务执行的顺序（时间），从而实现**时空联合调度**。

---

### 一、 功能总结

该 Pass 的工作流程包含四个核心阶段：

1. **特征提取与收益建模 \(Modeling\)**：

    - **输入**：从 IR 中读取 `TaskDependencyAnalysis` 生成的依赖图。

    - **扩容收益 \($P\_\{exp\}$\)**：基于 PPT 公式（$R\_\{vec\}, I\_\{arith\}, L\_\{depth\}, S\_\{buffer\}$ 等）计算单任务扩容带来的加速比。

    - **融合收益 \($P\_\{fusion\}$\)**：计算相邻任务融合带来的通信节省（流式传输 vs DRAM 读写）。

2. **逻辑融合 \(Logical Fusion\)**：

    - 基于融合收益，将强流式依赖的任务合并为“超级任务块 \(Super Task / Block\)”。

    - 这确保了紧密耦合的任务在物理布局上被作为一个整体处理。

3. **时序调度 \(Temporal Scheduling\)**：

    - **解决资源约束**：使用 **资源受限的列表调度 \(Resource\-Constrained List Scheduling\)** 算法。

    - 如果当前时间步（Time Step）的硬件资源（Tile 数量）不足，则自动将剩余任务推迟到下一个时间步。

    - 解决了 BERT 模型 190 个任务无法一次性放入有限网格的问题。

4. **空间装箱 \(Spatial Packing\)**：

    - 在每个时间步内，根据扩容收益决定任务尺寸（$1\\times1$, $2\\times1$, $2\\times2$ 等）。

    - 使用 **2D First\-Fit 装箱算法** 确定每个任务在 CGRA 上的具体坐标 $\(x, y\)$。

    - **结果回写**：将最终的 `neura.tile.coord`, `neura.tile.size`, `neura.time_step` 等属性写入 MLIR 代码，指导后端生成指令。

---

### 二、 完整 C\+\+ 实现代码

请确保文件路径为：`lib/NeuraDialect/Transforms/ProfitBasedSpatialOrchestrationPass.cpp`

```C++
#include "NeuraDialect/NeuraPasses.h"
#include "NeuraDialect/NeuraDialect.h"
#include "mlir/Dialect/Affine/IR/AffineOps.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/Dialect/MemRef/IR/MemRef.h"
#include "mlir/Dialect/SCF/IR/SCF.h"
#include "mlir/Dialect/Vector/IR/VectorOps.h"
#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/Pass/Pass.h"
#include "llvm/Support/Debug.h"
#include "llvm/Support/Format.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/SetVector.h"
#include <vector>
#include <cmath>
#include <algorithm>
#include <iostream>
#include <map>
#include <set>

#define DEBUG_TYPE "spatial-orchestration"

namespace mlir {
namespace neura {

// --- 1. 硬件架构参数配置 ---
struct ArchitectureConfig {
  double sram_capacity_bits = 256 * 1024 * 8; // 256KB SRAM
  double cost_global_noc = 100.0;             // DRAM/Global access cost
  double cost_neighbor = 10.0;                // Neighbor access cost
  double i_thresh = 0.5;                      // 算术强度阈值
  double alpha = 2.0;                         // 向量化权重
  double beta = 0.5;                          // Fan-out 惩罚权重
  double k = 5.0;                             // Sigmoid 斜率
  double packing_bonus = 50.0;                // 拼图奖励基数
  double high_penalty = 1000.0;               // 争用惩罚
};

// --- 2. 任务与块定义 ---
struct TaskStats {
  int id;
  Operation* op;
  
  // 特征
  double r_vec = 0.0;
  double i_arith = 0.0;
  unsigned l_depth = 0;
  double r_affine = 1.0; 
  double fan_avg = 0.0;
  long long s_buffer = 0;
  double rec_mii = 1.0;
  long long v_live_out = 0; 
  
  std::vector<int> preds; 
  std::vector<int> succs; 
};

struct TaskBlock {
  int blockId;
  std::vector<int> taskIds; 
  
  double total_scaling_score = 0.0;
  long long total_s_buffer = 0;
  
  int width = 1;
  int height = 1;
  int x = -1;
  int y = -1;
  int time_step = -1;
};

// --- 3. 收益模型实现 ---
class ProfitModel {
  ArchitectureConfig arch;

public:
  ProfitModel(ArchitectureConfig config) : arch(config) {}

  double sigmoid(double x) {
    return 1.0 / (1.0 + std::exp(-arch.k * x));
  }

  // 扩容收益
  double calculateExpansionProfit(const TaskStats& t) {
    double term_scale = 1.0 + std::log2(1.0 + (double)t.s_buffer / arch.sram_capacity_bits);
    double term_parallel = (1.0 + arch.alpha * t.r_vec) * std::log(1.0 + t.l_depth);
    double term_memory = sigmoid(t.i_arith - arch.i_thresh);
    double term_affine = t.r_affine;
    double term_noc = 1.0 / (1.0 + arch.beta * t.fan_avg);
    
    return term_scale * term_parallel * term_memory * term_affine * term_noc;
  }

  // 融合收益
  double calculateFusionProfit(const TaskStats& t1, const TaskStats& t2) {
    double c = 1.0; // 默认流式模式
    double benefit_affinity = c * t1.v_live_out * (arch.cost_global_noc - arch.cost_neighbor);
    double benefit_packing = 0.0;
    
    double min_mii = std::min(t1.rec_mii, t2.rec_mii) + 1e-9;
    double penalty_mismatch = c * (std::abs(t1.rec_mii - t2.rec_mii) / min_mii) * 50.0; 
    double cost_contention = 0.0;

    return benefit_affinity + benefit_packing - penalty_mismatch - cost_contention;
  }
};

// --- 4. 空间规划器 ---
class GridPlanner {
  int chipW, chipH;
  std::vector<bool> grid; 

public:
  GridPlanner(int w, int h) : chipW(w), chipH(h) {
    reset();
  }

  void reset() {
    grid.assign(chipW * chipH, false);
  }

  bool isOccupied(int x, int y, int w, int h) {
    if (x + w > chipW || y + h > chipH) return true;
    for (int j = 0; j < h; ++j) {
      for (int i = 0; i < w; ++i) {
        if (grid[(y + j) * chipW + (x + i)]) return true;
      }
    }
    return false;
  }

  void mark(int x, int y, int w, int h) {
    for (int j = 0; j < h; ++j) {
      for (int i = 0; i < w; ++i) {
        grid[(y + j) * chipW + (x + i)] = true;
      }
    }
  }

  std::pair<int, int> place(int w, int h) {
    for (int y = 0; y <= chipH - h; ++y) {
      for (int x = 0; x <= chipW - w; ++x) {
        if (!isOccupied(x, y, w, h)) {
          mark(x, y, w, h);
          return {x, y};
        }
      }
    }
    return {-1, -1};
  }
};

// --- 5. Pass 实现 ---
class ProfitBasedSpatialOrchestrationPass
    : public PassWrapper<ProfitBasedSpatialOrchestrationPass, OperationPass<func::FuncOp>> {
    
    // 命令行选项
    Option<int> optChipWidth{*this, "chip-width", llvm::cl::desc("Width of CGRA mesh"), llvm::cl::init(8)};
    Option<int> optChipHeight{*this, "chip-height", llvm::cl::desc("Height of CGRA mesh"), llvm::cl::init(8)};

public:
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(ProfitBasedSpatialOrchestrationPass)

  // 1. 默认构造函数
  ProfitBasedSpatialOrchestrationPass() = default;

  // 2. 拷贝构造函数 (关键：解决 Option 拷贝报错问题)
  ProfitBasedSpatialOrchestrationPass(const ProfitBasedSpatialOrchestrationPass &other)
      : PassWrapper(other) {
    this->optChipWidth = other.optChipWidth;
    this->optChipHeight = other.optChipHeight;
  }

  StringRef getArgument() const override { return "spatial-orchestration"; }
  StringRef getDescription() const override { return "Orchestrate tasks onto tiles based on profit model"; }

  void getDependentDialects(DialectRegistry &registry) const override {
    registry.insert<affine::AffineDialect, memref::MemRefDialect>();
  }

  void extractFeaturesFromOp(Operation* op, TaskStats& stats) {
     long long n_vec = 0, n_scalar = 0, n_compute = 0, n_mem = 0;
     long long fan_out = 0, ops_cnt = 0;
     op->walk([&](Operation *innerOp) {
        if (innerOp == op) return;
        ops_cnt++;
        bool isVec = false;
        for (auto t : innerOp->getResultTypes()) if (isa<VectorType>(t)) isVec = true;
        if (isVec) n_vec++; else n_scalar++;
        if (innerOp->getName().getStringRef().contains("arith") || 
            innerOp->getName().getStringRef().contains("math")) n_compute++;
        if (isa<affine::AffineLoadOp, affine::AffineStoreOp>(innerOp)) n_mem++;
        if (auto alloc = dyn_cast<memref::AllocOp>(innerOp)) {
             auto t = alloc.getType();
             if (t.hasStaticShape()) stats.s_buffer += t.getNumElements() * t.getElementTypeBitWidth();
        }
        if (auto store = dyn_cast<affine::AffineStoreOp>(innerOp)) {
             auto t = store.getMemRef().getType().cast<MemRefType>();
             if (t.hasStaticShape()) stats.v_live_out += 1; 
        }
        if (isa<affine::AffineForOp>(innerOp)) stats.l_depth++;
        for (auto res : innerOp->getResults()) {
            fan_out += std::distance(res.use_begin(), res.use_end());
        }
     });
     stats.r_vec = (double)n_vec / (n_vec + n_scalar + 1e-9);
     stats.i_arith = (double)n_compute / (n_mem + 1e-9);
     stats.fan_avg = (double)fan_out / (ops_cnt + 1e-9);
     stats.rec_mii = 1.0;
     op->walk([&](Operation *innerOp) {
         if (innerOp->getName().getStringRef().contains("exp") || 
             innerOp->getName().getStringRef().contains("div")) stats.rec_mii = 10.0;
     });
  }

  void runOnOperation() override {
    func::FuncOp func = getOperation();
    OpBuilder builder(&getContext());
    ArchitectureConfig config;
    ProfitModel model(config);

    llvm::errs() << "=== Profit-Based Spatial Orchestration (Time-Multiplexed) ===\n";
    llvm::errs() << "CGRA Size: " << optChipWidth << " x " << optChipHeight << "\n\n";

    // 1. 构建 Task 数据
    std::map<int, TaskStats> taskMap;
    std::vector<int> allTaskIds;
    
    func.walk([&](affine::AffineForOp op) {
      if (auto idAttr = op->getAttrOfType<IntegerAttr>("neura.task_id")) {
        int id = idAttr.getInt();
        TaskStats s;
        s.id = id;
        s.op = op;
        if (auto succs = op->getAttrOfType<ArrayAttr>("neura.streaming_succs")) {
            for (auto attr : succs) s.succs.push_back(attr.cast<IntegerAttr>().getInt());
        }
        extractFeaturesFromOp(op, s);
        taskMap[id] = s;
        allTaskIds.push_back(id);
      }
    });

    if (allTaskIds.empty()) {
        llvm::errs() << "No tasks found. Run --task-dependency-analysis first.\n";
        return;
    }

    // 2. 逻辑融合 -> TaskBlock
    std::map<int, int> taskToBlockMap;
    std::vector<TaskBlock> blocks;
    for (int id : allTaskIds) {
        TaskBlock b;
        b.blockId = blocks.size();
        b.taskIds.push_back(id);
        blocks.push_back(b);
        taskToBlockMap[id] = b.blockId;
    }

    // 简单贪心融合
    for (auto& pair : taskMap) {
        int u = pair.first;
        TaskStats& tU = pair.second;
        int blockU = taskToBlockMap[u];
        for (int v : tU.succs) {
            if (!taskMap.count(v)) continue;
            TaskStats& tV = taskMap[v];
            int blockV = taskToBlockMap[v];
            if (blockU == blockV) continue;
            if (model.calculateFusionProfit(tU, tV) > 500.0) {
                TaskBlock& bU = blocks[blockU];
                TaskBlock& bV = blocks[blockV];
                if (bV.taskIds.empty()) continue;
                for (int taskInV : bV.taskIds) {
                    bU.taskIds.push_back(taskInV);
                    taskToBlockMap[taskInV] = blockU;
                }
                bV.taskIds.clear();
            }
        }
    }

    std::vector<TaskBlock*> activeBlocks;
    for (auto& b : blocks) {
        if (b.taskIds.empty()) continue;
        double maxScore = 0.0;
        for (int tid : b.taskIds) {
            double s = model.calculateExpansionProfit(taskMap[tid]);
            if (s > maxScore) maxScore = s;
            b.total_s_buffer += taskMap[tid].s_buffer;
        }
        b.total_scaling_score = maxScore;
        activeBlocks.push_back(&b);
    }
    llvm::errs() << "Generated " << activeBlocks.size() << " Super-Tasks (Blocks) after fusion.\n";

    // 3. 资源受限列表调度 (Resource-Constrained List Scheduling)
    std::map<int, int> blockInDegree;
    std::map<int, std::vector<int>> blockAdj;
    for (auto* b : activeBlocks) blockInDegree[b->blockId] = 0;

    for (auto* bU : activeBlocks) {
        for (int tid : bU->taskIds) {
            for (int succId : taskMap[tid].succs) {
                int blockVId = taskToBlockMap[succId];
                if (blockVId != bU->blockId) {
                    bool exists = false;
                    for(int adj : blockAdj[bU->blockId]) if(adj == blockVId) exists = true;
                    if(!exists) {
                        blockAdj[bU->blockId].push_back(blockVId);
                        blockInDegree[blockVId]++;
                    }
                }
            }
        }
    }

    std::vector<TaskBlock*> readyQueue;
    for (auto* b : activeBlocks) {
        if (blockInDegree[b->blockId] == 0) readyQueue.push_back(b);
    }

    std::vector<std::vector<TaskBlock*>> timeSteps;
    int maxTiles = optChipWidth * optChipHeight;

    auto estimateTiles = [&](TaskBlock* b) -> int {
        if (b->total_scaling_score > 10.0) return 4;
        if (b->total_scaling_score > 5.0) return 2;
        return 1;
    };

    while (!readyQueue.empty()) {
        // 按收益排序，高优先级先调度
        std::sort(readyQueue.begin(), readyQueue.end(), [](TaskBlock* a, TaskBlock* b){
            return a->total_scaling_score > b->total_scaling_score;
        });

        std::vector<TaskBlock*> currentStepBlocks;
        std::vector<TaskBlock*> nextReadyQueue; 
        
        int currentTilesUsed = 0;

        for (auto* b : readyQueue) {
            int needed = estimateTiles(b);
            if (currentTilesUsed + needed <= maxTiles) {
                currentStepBlocks.push_back(b);
                currentTilesUsed += needed;
            } else {
                nextReadyQueue.push_back(b);
            }
        }
        
        if (currentStepBlocks.empty() && !readyQueue.empty()) {
             currentStepBlocks.push_back(readyQueue[0]);
             nextReadyQueue.erase(nextReadyQueue.begin());
        }

        timeSteps.push_back(currentStepBlocks);

        std::vector<TaskBlock*> newlyReady;
        for (auto* b : currentStepBlocks) {
            for (int neighbor : blockAdj[b->blockId]) {
                blockInDegree[neighbor]--;
                if (blockInDegree[neighbor] == 0) {
                    for (auto* ptr : activeBlocks) {
                        if (ptr->blockId == neighbor) {
                            newlyReady.push_back(ptr);
                            break;
                        }
                    }
                }
            }
        }
        
        readyQueue = nextReadyQueue;
        readyQueue.insert(readyQueue.end(), newlyReady.begin(), newlyReady.end());
    }

    llvm::errs() << "Scheduled into " << timeSteps.size() << " Time Steps (Resource Constrained).\n";

    // 4. 空间编排 (Per Time Step Packing)
    GridPlanner planner(optChipWidth, optChipHeight);

    for (int t = 0; t < timeSteps.size(); ++t) {
        llvm::errs() << "\n--- Time Step " << t << " ---\n";
        planner.reset(); 

        auto& stepBlocks = timeSteps[t];
        std::sort(stepBlocks.begin(), stepBlocks.end(), [](TaskBlock* a, TaskBlock* b) {
            return a->total_scaling_score > b->total_scaling_score;
        });

        for (auto* b : stepBlocks) {
            double score = b->total_scaling_score;
            int w = 1, h = 1;
            if (score > 10.0) { w = 2; h = 2; }
            else if (score > 5.0) { w = 2; h = 1; }

            auto pos = planner.place(w, h);
            
            if (pos.first == -1 && (w > 1 || h > 1)) {
                w = 1; h = 1;
                pos = planner.place(w, h);
            }

            if (pos.first != -1) {
                b->x = pos.first; b->y = pos.second; b->width = w; b->height = h;
                for (int tid : b->taskIds) {
                    Operation* op = taskMap[tid].op;
                    op->setAttr("neura.tile.coord", builder.getI32ArrayAttr({b->x, b->y}));
                    op->setAttr("neura.tile.size", builder.getI32ArrayAttr({b->width, b->height}));
                    op->setAttr("neura.time_step", builder.getI32IntegerAttr(t));
                    op->setAttr("neura.super_task_id", builder.getI32IntegerAttr(b->blockId));
                }
                llvm::errs() << llvm::format("  Block %d (Tasks: %lu, Score: %.2f) -> Placed at (%d, %d), Size %dx%d\n", 
                                             b->blockId, b->taskIds.size(), score, b->x, b->y, w, h);
            } else {
                llvm::errs() << llvm::format("  Block %d (Score: %.2f) -> FAILED due to Fragmentation in Step %d\n", 
                                             b->blockId, score, t);
            }
        }
    }
    llvm::errs() << "==========================================\n";
  }
};

std::unique_ptr<OperationPass<func::FuncOp>> createProfitBasedSpatialOrchestrationPass() {
  return std::make_unique<ProfitBasedSpatialOrchestrationPass>();
}

} // namespace neura
} // namespace mlir
```

### 三、 构建与运行命令

#### 重新编译

确保新的 Pass 文件已被包含在 `CMakeLists.txt` 中，然后执行：

```Plain Text
cd ~/workspace/dataflow/build
ninja mlir-neura-opt
```

#### 运行完整分析流程

使用以下命令来测试完整的分析、依赖提取和空间编排流程。这里我们将 CGRA 尺寸设定为 **8x8** 以模拟较大的加速器资源。

```Plain Text
./tools/mlir-neura-opt/mlir-neura-opt \
  -affine-super-vectorize="virtual-vector-size=4" \
  --task-dependency-analysis \
  --spatial-orchestration="chip-width=8 chip-height=8" \
  ../test/samples/bert/bert_affine.mlir > annotated_mapped_bert.mlir
```

**输出解读**：

- **`Task Analysis`** 部分会显示任务依赖图的 DOT 代码。

- **`Profit-Based Spatial Orchestration`** 部分会显示融合建议。

- **`Spatial Mapping`** 部分会按 **Time Step** 分组，显示每个任务块被放置的坐标 `(x, y)` 和尺寸 `Size WxH`。

- 最终生成的 `annotated_mapped_bert.mlir` 文件中，每个 Loop 操作都会包含详细的 `neura.tile.coord` 和 `neura.time_step` 属性，可以直接用于后续的硬件代码生成。

