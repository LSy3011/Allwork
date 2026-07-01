# 新增pass的流程

以在top层增加一个pass为例：

找到passes\.td文件，之后需要找到Pass\.h文件，之后建立一个\.cpp的具体实现的文件。需要注意的是，在这个项目的top层是不需要修改CMakeLists\.txt，因为我添加的\.cpp文件在Transforms这个文件夹下，已经被包含了，可以看以下的代码块，所以不需要重新添加。

```C++
file(GLOB _sources
          IR/*.cpp
          Interfaces/*.cpp
          Canonicalize/*.cpp
          Transforms/*.cpp
          Transforms/ProcessorOptimize/*.cpp
    )

```

除此之外，还需要注意一下

### 阶段 1：Step 1 \- 在 Passes\.td 中定义 Pass 元数据

`Passes.td` 是 Pass 的 “身份配置文件”，需通过 TableGen 语法定义 Pass 的核心属性，**必须严格遵循命名规范**。

#### 1\.1 编写 Pass 定义

在 `Passes.td` 文件末尾添加如下代码（以新增 `TopOpReorderPass` 为例，功能：对 Top 方言算子进行执行顺序重排）：

```Plain Text
// 1. （可选）若需统一基类，可复用 TopDialect 已有的 Pass 基类（若不存在则跳过）
// class TopPass<string name, string desc> : Pass<name, "ModuleOp"> {
//   let summary = desc;
//   let dependentDialects = ["TopDialect"]; // 统一依赖 Top 方言，避免重复声明
// }

// 2. 定义目标 Pass（核心）
def TopOpReorder : Pass<"top-op-reorder", "ModuleOp"> {
  // 2.1 Pass 功能描述（--help 时显示，必填）
  let summary = "Reorder Top dialect ops to optimize execution sequence";
  
  // 2.2 Pass 创建函数声明（需与后续 .h/.cpp 中的函数名完全一致，必填）
  let constructor = "createTopOpReorderPass()";
  
  // 2.3 依赖的 Dialect（处理 Top 算子必须依赖 TopDialect，必填）
  let dependentDialects = ["TopDialect"];
  
  // 2.4 （可选）Pass 配置参数（无参数则删除此字段）
  let options = [
    // 示例：添加 "enable-debug" 布尔参数，默认关闭
    Option<"enableDebug", "enable-debug", "bool", /*default=*/"false",
           "Enable debug log for op reorder">
  ];
}
```

#### 1\.2 关键注意事项

1. **Pass 命名规则**：

    - `.td` 中 `def` 后的名称（如 `TopOpReorder`）：采用 “大驼峰”，后缀可省略 `Pass`（后续生成基类时会自动加 `Base`）。

    - 命令行名称（如 `"top-op-reorder"`）：采用 “小写 \+ 中划线”，与 `def` 名称对应（如 `TopOpReorder` → `top-op-reorder`），方便命令行调用（如 `mlir-opt --top-op-reorder`）。

2. **`constructor`**** 字段**：必须以 `create` 开头，后接 Pass 逻辑名称（如 `createTopOpReorderPass`），**后续 \.h/\.cpp 中的函数名需完全匹配**，否则会导致 “未定义函数” 错误。

3. **`dependentDialects`**** 字段**：处理 Top 算子必须添加 `"TopDialect"`，若需处理量化算子（如 `quant::QuantizedType`），需额外添加 `"mlir::quant::QuantizationDialect"`。

### 阶段 2：Step 2 \- 在 Passes\.h 中声明 Pass 创建函数

`Passes.h` 是 Pass 的 “接口声明文件”，需声明 Pass 的创建函数，供 `Init.cpp` 注册和外部代码调用。

#### 2\.1 编写函数声明

在 `Passes.h` 文件的 `tpu_mlir::top` 命名空间内，添加如下声明（与 `.td` 中 `constructor` 字段对应）：

```C++
namespace tpu_mlir {
namespace top {

// （已有的其他 Pass 声明...）

// 新增 Pass 创建函数声明（核心，需与 .td 中 constructor 完全一致）
std::unique_ptr<mlir::OperationPass<mlir::ModuleOp>> createTopOpReorderPass();

} // namespace top
} // namespace tpu_mlir

#endif // TPU_MLIR_DIALECT_TOP_TRANSFORMS_PASSES_H
```

#### 2\.2 关键注意事项

1. **命名一致性**：函数名 `createTopOpReorderPass` 必须与 `.td` 中 `constructor = "createTopOpReorderPass()"` 完全一致（包括大小写、字母顺序）。

2. **返回类型**：固定为 `std::unique_ptr<mlir::OperationPass<mlir::ModuleOp>>`，表示 “处理 ModuleOp 级别的 Pass”（Top Dialect 主流 Pass 级别）。

3. **命名空间**：必须放在 `tpu_mlir::top` 命名空间内，避免与其他 Dialect 的 Pass 命名冲突。

### 阶段 3：Step 3 \- 编写 Pass 实现文件（\.cpp）

创建 `.cpp` 文件实现 Pass 的核心逻辑（如 IR 遍历、算子重排），是 Pass 功能的核心载体。

#### 3\.1 创建 \.cpp 文件

在 `lib/Dialect/Top/Transforms/` 目录下新建文件 `TopOpReorder.cpp`（命名规则：`Pass 逻辑名 + .cpp`，与 Pass 功能对应）。

#### 3\.2 编写 \.cpp 代码

完整代码示例如下：

```C++
//===----------------------------------------------------------------------===//
//
// 新增
//
//===----------------------------------------------------------------------===//

#include "tpu_mlir/Dialect/Top/Transforms/Passes.h"

using namespace llvm;

namespace tpu_mlir {
namespace top {

class TopOpReorderPass : public TopOpReorderBase<TopOpReorderPass> {
public:
  TopOpReorderPass() {}
  
  void runOnOperation() override {
    // Pass implementation goes here
    llvm::outs() << "TopOpReorderPass is running\n";
     
  }
};

std::unique_ptr<OperationPass<ModuleOp>> createTopOpReorderPass() {
  return std::make_unique<TopOpReorderPass>();
}

} // namespace top
} // namespace tpu_mlir
```

#### 3\.3 关键注意事项

1. **基类继承**：必须继承 `.td` 生成的基类（`def 名称 + Base`），如 `TopOpReorderBase`，若基类名错误（如写成 `TopOpReorderPassBase`），会报 “no template named XXX” 错误。

2. **`runOnOperation`**** 函数**：

    - 必须用 `override` 关键字（重写基类虚函数），缺失会导致 Pass 逻辑不执行。

    - 不可修改函数名和返回值（固定为 `void runOnOperation()`）。

3. **参数使用**：\.td 中定义的参数（如 `enableDebug`）会自动生成到基类中，可直接在类内使用，无需额外声明。

4. **类型 ID 注册**：最后一行 `MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID` 必须添加，否则 MLIR 框架无法识别 Pass 类型，导致运行时崩溃。



