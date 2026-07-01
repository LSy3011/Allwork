# 关于 TPU-MLIR 源码阅读（二）

@Juno

这个部分主要是算子支持部分，主要讲述了如何添加算子，以及如何将 Top 层算子转换到 Tpu 层。

参考的视频的链接如下：[TPU-MLIR 系列讲解（八）：Lowering in TPU-MLIR_哔哩哔哩_bilibili](https://www.bilibili.com/video/BV1gg411z7mC/?share_source=copy_web&vd_source=90fd7c624ed0c40af96748bd0b8dd3e8)，[Ep20 Add a New Operator_哔哩哔哩_bilibili](https://www.bilibili.com/video/BV1tL411r71p/?share_source=copy_web&vd_source=90fd7c624ed0c40af96748bd0b8dd3e8)

参考的文档链接如下：[8. Lowering — TPU-MLIR 1.16 文档](https://doc.sophgo.com/sdk-docs/v25.03.01/docs_latest_release/docs/tpu-mlir/developer_manual/html/08_lowering.html)

请先看视频和文档链接，再阅读以下内容。

## TOP.td

### 一、TOP Dialect 定义

- 定义 TPU-MLIR 的核心 Dialect（`top`），用于封装 TPU 相关的操作和属性，是整个文件的基础命名空间。

```cpp
// =============================================================================
//
// Defines TOP Dialect.
//
//===----------------------------------------------------------------------===//
def Top_Dialect : Dialect {
  let name = "top";  // Dialect名称，在MLIR中用于标识操作（如`top.conv`）
  let summary = "A topdialect for the TPU_MLIR specification";  // 简要描述：TPU-MLIR专用Dialect
  let cppNamespace = "::tpu_mlir::top";  // C++命名空间，避免符号冲突}
```

### 二、TOP 属性定义

- 定义了 TPU 操作中常用的属性类型，均为字符串枚举（只能取预定义值），用于约束操作的行为（如比较方式、归约类型等）。

```xml
//===----------------------------------------------------------------------===//
// TOP Attributes.
//===----------------------------------------------------------------------===//

// 自定义属性基类：继承自Top_Dialect，包含名称和助记符
class Top_Attr<string attrName, string attrMnemonic, list<Trait> traits = []>
    : AttrDef<Top_Dialect, attrName, traits> {
  let mnemonic = attrMnemonic;  // 属性的助记符，用于MLIR文本格式解析
}

// 字符串枚举属性模板：值只能是cases中的选项
class AnyStrAttrOf<list<string> cases> : StringBasedAttr<
  CPred<!foldl(  // 约束条件：值必须是cases中的一个
      "$_self.cast<StringAttr>().getValue() == \"" # !head(cases) # "\"",
      !foreach(case, !tail(cases),
               "$_self.cast<StringAttr>().getValue() == \"" # case # "\""),
      prev, cur, prev # " || " # cur)>,
  "string attribute whose value is " #  // 描述信息
    !foldl(/*init*/!head(cases), /*list*/!tail(cases),
           prev, cur, prev # ", or " # cur)>;

// 具体属性定义：基于AnyStrAttrOf，限定特定字符串值
def ArgModeAttr: AnyStrAttrOf<["ArgMin","ArgMax"]>;  // 用于Arg操作：取最小/最大值索引
def CompareModeAttr: AnyStrAttrOf<["Equal","Greater",...]>;  // 比较操作模式（等于、大于等）
def ReduceModeAttr: AnyStrAttrOf<["ReduceMin","ReduceMax",...]>;  // 归约操作模式（求最小、最大等）
// ... 其他属性（插值模式、像素格式、通道格式等）
```

### 三、基础操作定义

- 定义了基础操作（`NoneOp`、`WeightOp`、`InputOp`），分别用于返回空值、加载权重、处理输入，是模型执行的起点。

```java
//===----------------------------------------------------------------------===//
// TOP Op Definition.
//===----------------------------------------------------------------------===//

// 基础操作基类：所有TOP操作的父类，绑定到Top_Dialect
class Top_BaseOp<string mnemonic, list<Trait> traits = []> :
    Op<Top_Dialect, mnemonic, traits> ;

// 空操作：返回NoneType
def Top_NoneOp : Top_BaseOp<"None"> {
  let summary = "none operator";
  let description = [{ A none Op to return a NoneType. }];  // 功能：返回空类型
  let results = (outs NoneType);  // 输出：NoneType
}

// 权重操作：加载权重数据（从文件或内嵌字节）
def Top_WeightOp : Top_BaseOp<"Weight"> {
  let summary = "weight operator";
  let description = [{
    若inline_bytes为空，则从.npz文件加载权重；否则从inline_bytes加载。
    无输入，输出为与权重文件匹配的n维张量。
  }];
  let arguments = (ins  // 输入属性：缩放因子、存储模式、压缩参数等
    OptionalAttr<F64ArrayAttr>:$scale,
    OptionalAttr<StoreModeAttr>:$store_mode,
    // ... 其他属性
  );
  let results = (outs AnyRankedTensor:$output);  // 输出：带形状的张量
  let hasCanonicalizer = 1;  // 启用规范化（如权重格式转换）
  let extraClassDeclaration = [{  // 额外C++方法：读取权重、克隆权重等
    template<typename T> std::shared_ptr<std::vector<T>> read();
    // ... 其他方法
  }];
}

// 输入操作：处理模型输入（可选预处理）
def Top_InputOp: Top_BaseOp<"Input", [DeclareOpInterfaceMethods<ShapeInterface>]> {
  let summary = "Input operator";
  let description = [{ 处理输入张量，支持预处理（如Resize、Pad、归一化等）}];
  let arguments = (ins  // 输入：原始输入张量；属性：预处理参数（像素格式、通道格式等）
    AnyRankedTensor:$input,
    OptionalAttr<I64ArrayAttr>:$shape_tensor,
    // ... 其他属性
  );
  let results = (outs AnyTensor:$output);  // 输出：预处理后的张量
}
```

### 四、核心操作定义（示例）

- 定义了核心操作（如 `BatchNormOp`、`ConvOp`），涵盖深度学习中常见的计算逻辑，每个操作包含功能描述、数学公式、输入输出及属性约束，是模型计算的核心单元。

```java
// 函数操作基类：继承自Top_BaseOp，绑定推理、计算量、形状接口
class Top_Op<string mnemonic, list<Trait> traits = []> :
    Top_BaseOp<mnemonic, !listconcat(traits,
       [DeclareOpInterfaceMethods<InferenceInterface>,
        DeclareOpInterfaceMethods<FlopsInterface>,
        DeclareOpInterfaceMethods<ShapeInterface>])>;

// 批归一化操作
def Top_BatchNormOp: Top_Op<"BatchNorm", [SupportFuseRelu]> {
  let summary = "BatchNormalization operation";
  let description = [{
    1. 功能：对4D输入（NCHW/NHWC）进行批归一化，加速训练并增强稳定性。
    2. 公式：output = (input - E[input]) / sqrt(variance + epsilon) * gamma + beta
    3. 输入：input（输入张量）、mean（均值）、variance（方差）、gamma/beta（可学习参数）
    4. 属性：epsilon（防止除零的小值）、do_relu（是否后接ReLU）等
  }];
  let arguments = (ins  // 输入和属性
    AnyTensor:$input,
    AnyTensor:$mean,
    AnyTensor:$variance,
    AnyTensorOrNone:$gamma,  // 可选参数
    AnyTensorOrNone:$beta,
    DefaultValuedAttr<F64Attr, "1e-05">:$epsilon,
    // ... 其他属性
  );
  let results = (outs AnyTensor:$output);  // 输出：归一化后的张量
  let hasCanonicalizer = 1;  // 支持规范化（如融合操作）
}

// 卷积操作
def Top_ConvOp: Top_Op<"Conv", [SupportFuseRelu, ...]> {
  let summary = "Convolution operator";
  let description = [{
    1. 功能：对输入张量进行2D卷积，用于特征提取。
    2. 公式：output(N, C_out, H, W) = sum(input * filter) + bias
    3. 输入：input（输入张量）、filter（卷积核）、bias（偏置，可选）
    4. 属性：kernel_shape（核大小）、strides（步长）、pads（填充）、groups（分组数）等
  }];
  let arguments = (ins  // 输入和属性
    AnyTensor:$input,
    AnyTensor:$filter,
    AnyTensorOrNone:$bias,
    I64ArrayAttr:$kernel_shape,
    // ... 其他属性
  );
  let results = (outs AnyTensor:$output);  // 输出：卷积结果
  let hasCanonicalizer = 1;  // 支持规范化（如权重重排）
}

// ... 其他操作（池化、激活、注意力机制等）
```

### 五、控制流操作（示例）

- 定义控制流操作（`IfOp`、`LoopOp` 等），支持条件分支和循环，用于实现复杂的执行逻辑（如模型中的动态控制）。

```java
// 条件操作：根据条件执行不同分支
def Top_IfOp : Top_Op<"If"> {
  let summary = "if operation";
  let description = [{ 条件分支：若cond为真执行then_branch，否则执行else_branch }];
  let arguments = (ins AnyTensor:$cond);  // 输入：条件张量（布尔型）
  let results = (outs Variadic<AnyTensor>:$output);  // 输出：分支执行结果
  let regions = (region SizedRegion<1>:$then_branch, SizedRegion<1>:$else_branch);  // 两个分支区域
}

// 循环操作：支持多种终止条件的循环
def Top_LoopOp : Top_Op<"Loop"> {
  let summary = "Loop operation";
  let description = [{ 通用循环结构，支持最大迭代次数、终止条件等 }];
  let arguments = (ins  // 输入：最大迭代次数、初始条件、循环变量初始值
    AnyTypeOf<[AnyTensor, NoneType]>:$M,
    AnyTypeOf<[AnyTensor, NoneType]>:$cond,
    Variadic<AnyTypeOf<[AnyTensor, NoneType]>>:$v_initial
  );
  let results = (outs Variadic<AnyTypeOf<[AnyTensor, NoneType]>>:$v_final_and_scan_outputs);  // 输出：最终变量和扫描结果
  let regions = (region SizedRegion<1>:$body);  // 循环体区域
}
```

### 六、TOP 层中有关 TPU 的算子定义

```
//Tpu_BinaryShiftOp 是一个针对 TPU 优化的算子，主要功能是对两个输入张量执行二进制运算（如加减乘），并结合移位操作和数值调整机制，适用于量化场景下的高效计算。
def Tpu_BinaryShiftOp: Top_Op<"BinaryShift"> {
  let summary = "Binary with shift operator";

  let description = [{
    1.Op Introduction
    The BinaryShift operator is designed to perform binary operations on two input tensors with an additional shift operation.

    2.Math formula
    ```math
        output = saturation(input1 +/-/* input2 >> -shift)
    ```

    3.activation and weight
    input1(act.): input tensor;
    input2(act.): input tensor;

    4.attribute
    shift: a shift value applied to the quantized data before scaling.;
    mode: the type of binary operation to be performed, addition, subtraction, or other types of binary operations.;
    is_reverse: whether the subtraction operation is performed in reverse order.;
    saturation: whether the output should be saturated.
                When set to true, the output will be clamped to a predefined range to prevent overflow or underflow during the operation.;
    round_mode: how values are rounded during the conversion from higher precision to lower precision.;
  }];

  let arguments = (
    ins AnyTensor:$input1,
    AnyTensor:$input2,
    BinaryShiftAttr:$mode,
    SI32Attr:$shift,
    DefaultValuedAttr<BoolAttr, "false">:$is_reverse,
    DefaultValuedAttr<BoolAttr, "true">:$saturation,
    DefaultValuedAttr<RoundModeAttr, "\"HalfAwayFromZero\"">:$round_mode
  );

  let results = (outs AnyTensor:$output);
}

//Tpu_RopeOp 是一个专为 TPU 优化的复杂张量运算算子，主要用于多输入张量的组合计算，融合了乘法、移位、加法、饱和截断等操作，适用于量化场景下需要高精度且高效计算的场景（如神经网络中的注意力机制、特征融合等）。
def Tpu_RopeOp: Top_Op<"Rope"> {
  let summary = "Rope operator";

  let description = [{
    1.Op Introduction
    The Rope operator is a specialized tensor operation designed for efficient computations involving multiple input tensors.

    2.Math formula
    ```math
        output=saturation((input1 x shift(input2, mul1_shift))⊕(input3 x shift(input2, mul2_shift))) + shift(input3, dd_shift)
    ```
    The operator ⊕ represents the addition of the two multiplicative results.
    The function shift(input,shift_value) applies a shift to the input tensor based on the provided shift value.
    The saturation function ensures that the output remains within a defined range, preventing overflow or underflow.

    3.activation and weight
    input1(act.): input tensor;
    input2(act.): input tensor;
    input3(act.): input tensor;

    4.attribute
    is_permute_optimize:whether to apply optimization for permuting the input tensors.;
    mul1_round_mode: the rounding mode to be used for the first multiplication operation.;
    mul2_round_mode: Similar to mul1_round_mode, this attribute defines the rounding mode for the second multiplication operation.;
    add_round_mode: the rounding mode for the addition operation.;
    mul1_shift: the number of bits to shift the result of the first multiplication.;
    mul2_shift: Similar to mul1_shift, this attribute defines the number of bits to shift for the second multiplication operation.;
    add_shift: the number of bits to shift the result of the addition operation.;
    mul1_saturation: whether the output of the first multiplication should be saturated.
                     When set to true, the result will be clamped to prevent overflow or underflow.;
    mul2_saturation: Similar to mul1_saturation, this attribute specifies whether saturation should be applied to the second multiplication's output.;
    add_saturation: whether to apply saturation to the output of the addition operation.;
  }];

  let arguments = (
    ins AnyTensor:$input1,
    AnyTensor:$input2,
    AnyTensor:$input3,
    DefaultValuedAttr<BoolAttr, "false">:$is_permute_optimize,
    DefaultValuedAttr<RoundModeAttr, "\"HalfAwayFromZero\"">:$mul1_round_mode,
    DefaultValuedAttr<RoundModeAttr, "\"HalfAwayFromZero\"">:$mul2_round_mode,
    DefaultValuedAttr<RoundModeAttr, "\"HalfAwayFromZero\"">:$add_round_mode,
    DefaultValuedAttr<SI32Attr, "0">:$mul1_shift,
    DefaultValuedAttr<SI32Attr, "0">:$mul2_shift,
    DefaultValuedAttr<SI32Attr, "0">:$add_shift,
    DefaultValuedAttr<BoolAttr, "true">:$mul1_saturation,
    DefaultValuedAttr<BoolAttr, "true">:$mul2_saturation,
    DefaultValuedAttr<BoolAttr, "true">:$add_saturation
  );

  let results = (outs AnyTensor:$output);
}

//Tpu_BinaryConstShiftOp 是针对 “张量与常数” 的二进制运算设计的 TPU 优化算子，融合了常数缩放、移位和饱和截断，适用于量化场景下张量与固定常数的高效运算.
def Tpu_BinaryConstShiftOp: Top_Op<"BinaryConstShift"> {
  let summary = "Binary Const with shift operator";

  let description = [{
    1.Op Introduction
    The BinaryConstShift operator is a specialized tensor operation that combines binary arithmetic with constant scaling and shifting.

    2.Math formula
    ```math
        output = saturation(input +/-/* scale >> -shift)
    ```

    3.activation and weight
    input(act.): input tensor;

    4.attribute
    scale: a scaling factor multiplies the input tensor.;
    shift: a shift value applied to the quantized data before scaling.;
    is_reverse: whether the subtraction operation is performed in reverse order.;
    saturation: whether the output should be saturated.
                When set true, the output will be clamped to a predefined range to prevent overflow or underflow during the operation.;
    round_mode: It determines how values are rounded during the conversion from higher precision to lower precision.;
  }];

  let arguments = (
    ins AnyTensor:$input,
    SI32Attr:$scale,
    BinaryShiftAttr:$mode,
    SI32Attr:$shift,
    DefaultValuedAttr<BoolAttr, "false">:$is_reverse,
    DefaultValuedAttr<BoolAttr, "true">:$saturation,
    DefaultValuedAttr<RoundModeAttr, "\"HalfAwayFromZero\"">:$round_mode
  );

  let results = (outs AnyTensor:$output);
}
```

## TPU.td

该文件是针对其深度学习处理器（TPU）基于 MLIR（Multi-Level Intermediate Representation）框架定义的 TPU Dialect（方言）实现，用于描述 TPU 上的深度学习操作、属性及类型。以下从原理、逻辑、代码三方面解析：

### 一、原理：TPU Dialect 的设计目标与作用

MLIR 是一个灵活的中间表示框架，允许通过 “Dialect” 扩展定义特定领域的操作（Operation）、属性（Attribute）和类型（Type）。TPU Dialect 的核心目标是：

1. 抽象 TPU 硬件特性：将 TPU 支持的深度学习操作（如卷积、池化、激活函数等）形式化定义为 MLIR 可识别的操作，使其能被编译器理解。
2. 支持编译优化与代码生成：通过定义操作的接口（如 `LocalGenInterface`、`GlobalGenInterface`），为 TPU 的代码生成（如硬件指令生成）和优化（如算子融合、内存布局优化）提供元信息。
3. 统一表示与跨层协作：在编译器的前端（如模型导入）、中端（如优化）、后端（如代码生成）之间，用统一的 TPU 操作表示计算，简化各阶段的协作。

### 二、逻辑结构：文件的核心组成部分

文件按 MLIR TableGen 的语法规则组织，整体逻辑从 “Dialect 基础定义” 到 “具体操作实现” 层层递进，核心结构如下：

1. Dialect 基础定义

定义了 TPU Dialect 的名称（`tpu`）、功能摘要及对应的 C++ 命名空间，是所有 TPU 操作、属性的 “命名空间”

```
def Tpu_Dialect : Dialect {
  let name = "tpu";
  let summary = "A tpu dialect for the SOPHGO Deep Learning processors";
  let cppNamespace = "::tpu_mlir::tpu";
}
```

1. 属性（Attributes）定义

属性用于描述操作的参数（如卷积核大小、激活函数类型），分为两类：

- 枚举属性：限制参数的可选值，如：

  - `ArgModeAttr`：指定是 “ArgMin” 还是 “ArgMax”；
  - `Tpu_ActiveMode`：定义激活函数类型（TANH、SIGMOID、RELU 等 37 种）。
- 结构属性：组合多个参数形成复杂配置，如：

  - `Tpu_LayerGroupAttr`：描述层分组参数（输出地址、缓冲区大小、维度切片信息等）；
  - `Tpu_CompressAttr`：配置压缩 / 解压缩参数（是否压缩、偏移量、符号位等）。

1. 类型（Types）定义

定义操作输入 / 输出的数据类型，如：

- `AnyTensorOrNone`：表示任意张量或空类型，用于泛化操作的输入输出。

1. 操作（Operations）定义

这是文件的核心，定义了 TPU 支持的所有深度学习操作（共约 130 种），每个操作包含：

- 功能描述：操作的作用（如 `Tpu_Conv2DOp` 实现 2D 卷积）；
- 数学公式：计算逻辑（如卷积的滑动窗口求和公式）；
- 输入输出：输入张量（如输入特征图、权重）和输出张量；
- 属性：操作的配置参数（如卷积的核大小、步长、填充）；
- 接口实现：用于代码生成的接口（如 `LocalGenInterface` 用于本地硬件指令生成）。

### 三、代码解析：核心组件详解

1. 属性定义示例：`Tpu_ActiveMode`（激活函数模式）

```java
def Tpu_ActiveMode : I32EnumAttr<"ActiveMode",
    "Activation mode for ActiveOp, for sigmoid/exp, e.g.",
    [
      I32EnumAttrCase<"TANH", 0>,
      I32EnumAttrCase<"SIGMOID", 1>,
      I32EnumAttrCase<"RELU", 2>,
      // ... 共37种激活模式
    ]>{
  let genSpecializedAttr = 0;
  let cppNamespace = "::tpu_mlir::tpu";
}
def Tpu_ActiveModeAttr : EnumAttr<Tpu_Dialect, Tpu_ActiveMode, "active_mode">;
```

- 作用：限制 `Tpu_ActiveOp`（激活操作）的 `active_mode` 属性只能取预定义的激活函数类型，确保参数合法性。
- 用法：在 `Tpu_ActiveOp` 中通过 `Tpu_ActiveModeAttr:$mode` 引用，指定具体激活函数。

1. 操作定义示例：`Tpu_Conv2DOp`（2D 卷积）

```javascript
def Tpu_Conv2DOp: Tpu_Op<"Conv2D", [SupportFuseRelu,
    DeclareOpInterfaceMethods<LocalGenInterface, ...>]> {
  let summary = "convolution 2d operator";
  let description = [{
    1.Op Introduction: 2D卷积操作，应用于CNN中的特征提取。
    2.Math formula: 
      output(N, C_out, H, W) = sum(input * filter) + bias
    3.activation and weight: 输入特征图、滤波器、偏置。
    4.attribute: 核大小、步长、填充、分组数等。
  }];
  let arguments = (ins
    AnyRankedTensor:$input,    // 输入特征图
    AnyRankedTensor:$filter,   // 卷积核
    AnyTensorOrNone:$bias,     // 偏置（可选）
    I64ArrayAttr:$kernel_shape, // 核大小 [kh, kw]
    I64ArrayAttr:$strides,     // 步长 [sh, sw]
    // ... 其他属性
  );
  let results = (outs AnyRankedTensor:$output); // 输出特征图
}
```

- 核心逻辑：通过 `kernel_shape`、`strides` 等属性定义卷积的几何参数，数学公式描述滑动窗口内的加权求和过程。
- 接口：实现 `LocalGenInterface` 用于生成 TPU 本地执行的指令，支持卷积的硬件加速。

1. 操作的共性设计

- 继承关系：多数操作继承自 `Tpu_Op`，该类默认实现了代码生成相关的接口（如 `GlobalGenInterface`、`InferenceInterface`），确保操作可被编译流程处理。
- 属性复用：多个操作共享相同的属性（如 `Tpu_RoundModeAttr` 用于指定量化时的舍入模式），减少冗余定义。
- 数学公式：每个操作都通过数学公式明确计算逻辑，为代码生成和验证提供依据（如 `Tpu_AddOp` 的 `output = input1 + input2`）。

## /include/tpu_mlir/Conversion/Passes.td

这个 `.td` 文件用于定义 TPU-MLIR 框架中的方言转换 Pass（编译过程中的转换步骤），主要功能是将高层的 `Top` 方言算子转换为其他目标方言（`Tpu`、`Tosa`、`Linalg`）的算子。以下是详细解析：

### 一、文件结构与作用

- 开头的宏定义（`#ifndef`/`#define`/`#endif`）是头文件保护机制，避免重复包含。
- `include "mlir/Pass/PassBase.td"` 引入 MLIR 框架中 Pass 定义的基础模板，确保符合 MLIR 的 Pass 规范。
- 核心内容是三个转换 Pass 的定义：`ConvertTopToTpu`、`ConvertTopToTosa`、`ConvertTopToLinalg`，分别对应不同的目标方言转换。

### 二、各 Pass 详解

#### `ConvertTopToTpu` Pass

- 作用：将 `Top` 方言（高层抽象算子）转换为 `Tpu` 方言（TPU 硬件适配算子），是面向 TPU 硬件执行的关键转换。
- 核心字段解析：

  - `summary`：简要描述功能 ——“将顶层 Top 算子转换为 Tpu 算子”。
  - `constructor`：指定创建该 Pass 的工厂函数 `tpu_mlir::createConvertTopToTpu()`（对应 C++ 实现）。
  - `dependentDialects`：声明依赖的方言 ——`TopDialect`（源方言）和 `TpuDialect`（目标方言），确保转换时加载相关方言定义。
  - `options`：定义 Pass 的可配置参数（转换时的选项）：
    - `qtable`：量化表，指定哪些算子需要量化到特定模式。
    - `isAsymmetric`：是否启用非对称量化（`true` 为非对称，`false` 为对称）。
    - `doWinograd`：是否尝试使用 Winograd 算法（加速卷积计算）。
    - `weightFileName`：权重文件名称（用于保存转换后的权重数据）。
    - `quantGroupSize`：分组量化的组大小（用于 W4A16/W8A16 等分组量化模式）。
    - `quantSymmetric`：W4A16/W8A16 量化是否为对称模式。
    - `matmulPerchannel`：矩阵乘（MatMul）是否使用逐通道量化（vs 逐张量量化）。
    - `geluMode`：GELU 激活函数的计算模式（支持 `normal`/`tanh`/`sigm` 三种）。

#### `ConvertTopToTosa` Pass

- 作用：将 `Top` 方言转换为 `Tosa` 方言（MLIR 中的一种通用张量操作方言，适用于机器学习推理）。
- 核心字段解析：

  - `summary`：“将顶层 Top 算子转换为 Tosa 算子”。
  - `constructor`：工厂函数 `tpu_mlir::createConvertTopToTosa()`。
  - `dependentDialects`：依赖 `TopDialect` 和 `TosaDialect`。
  - `options`：仅 `includeWeight` 选项 —— 控制是否在转换后的 `tosa.mlir` 中包含权重数据（`true` 包含，`false` 不包含）。

#### `ConvertTopToLinalg` Pass

- 作用：将 `Top` 方言转换为 `Linalg` 方言（MLIR 中的线性代数方言，适用于通用线性代数操作）。
- 核心字段解析：

  - `summary`：“将顶层 Top 算子转换为 Linalg 算子”。
  - `constructor`：工厂函数 `tpu_mlir::createConvertTopToLinalg()`。
  - `dependentDialects`：依赖 `TopDialect` 和 `LinalgDialect`。
  - `options`：仅 `includeWeight` 选项 —— 控制是否在转换后的 `linalg.mlir` 中包含权重数据。

## /lib/Dialect/Top/Interfaces/Abs.cpp

这个部分主要是结合 TOP.td 文件中的关于 Top_Op 的相关内容进行阐述的，因为在.td 文件中仅仅是对模板进行了声明，编译器并不知道要对张量进行什么样的操作，所以需要通过 inference 接口来进行声明。首先先回顾一下 Top_Op 类的定义，它继承了 Top_BaseOp，而由 Top_Op 派生的算子需要提供 InferenceInterface，FlopsInterface 和 ShapeInterface 接口。

```
class Top_Op<string mnemonic, list<Trait> traits = []> :
    Top_BaseOp<mnemonic, !listconcat(traits,
       [DeclareOpInterfaceMethods<InferenceInterface>,
        DeclareOpInterfaceMethods<FlopsInterface>,
        DeclareOpInterfaceMethods<ShapeInterface>])>;
```

- `InferenceInterface`（推理接口）：定义算子的实际计算逻辑，是算子执行推理 / 运算的核心入口。
- `FlopsInterface`（计算量接口）：计算算子浮点运算量（FLOPs），用于性能分析和优化。
- `ShapeInterface`（形状接口）：用于在形状未知的情况下，推理输出形状。

接下来我们就以 Abs.cpp 为例查看一下具体的算子实现。

```cpp
#include "tpu_mlir/Support/MathUtils.h"

/**
 * @brief 计算Abs算子的浮点运算量(FLOPs)
 * @return 算子的FLOPs数值
 * @note 对于Abs算子，每个元素需要执行一次绝对值运算，因此FLOPs等于输出张量的元素总数
 */
int64_t top::AbsOp::getFLOPs() { 
    // 通过module工具类获取输出张量的元素总数，作为FLOPs计算结果
    return module::getNumElements(getOutput()); 
}

/**
 * @brief 初始化Abs算子（推理前准备）
 * @param p 推理参数结构体，包含输入输出数据指针等信息
 * @return 操作结果，success()表示初始化成功
 * @note 由于Abs算子逻辑简单，无需额外初始化操作，直接返回成功
 */
LogicalResult top::AbsOp::init(InferenceParameter &p) { 
    return success(); 
}

/**
 * @brief 销毁Abs算子（推理后清理）
 * @param p 推理参数结构体
 * @note 由于Abs算子没有申请额外资源，无需清理操作，函数体为空
 */
void top::AbsOp::deinit(InferenceParameter &p) {}

/**
 * @brief 执行Abs算子的推理计算（核心逻辑）
 * @param p 推理参数结构体，包含输入输出数据指针等信息
 * @return 操作结果，success()表示推理成功
 * @note 实现逐元素取绝对值的运算，使用OpenMP并行加速
 */
LogicalResult top::AbsOp::inference(InferenceParameter &p) {
    // 获取输出张量的元素总数，确定计算范围
    auto num_element = module::getNumElements(getOutput());
    
    // 使用OpenMP进行并行计算，提高处理效率
    // schedule(static, omp_schedule(num_element))：根据元素数量动态分配线程任务
#pragma omp parallel for schedule(static, omp_schedule(num_element))
    for (int i = 0; i < num_element; ++i) {
        // 读取第i个输入元素
        auto val = p.inputs[0][i];
        // 计算绝对值并写入第i个输出位置
        p.outputs[0][i] = std::abs(val);
    }
    return success();
}

/**
 * @brief 推导Abs算子输出张量的形状
 * @note 对于Abs这类逐元素操作，输入和输出张量形状相同，直接复用框架通用形状推导逻辑
 */
void top::AbsOp::shape_inference() { 
    // 调用框架通用形状推导函数，自动计算输出形状
    common_shape_inference(getOperation()); 
}
```

## /lib/Dialect/Tpu/Interfaces/Common/Active.cpp

与上同理，我们需要为 TPU 算子推理的接口，但是由于 `Flops` 信息和 `Shape` 信息可以从 Top 算子中得到，所以我们需要 inference 接口。具体的 Tpu_Op 的算子类的定义可以见下：

```
class Tpu_Op<string mnemonic, list<Trait> traits = []> :
    Op<Tpu_Dialect, mnemonic, !listconcat(traits,
       [TpuTypeRestrict,
       DeclareOpInterfaceMethods<GlobalGenInterface>,
       DeclareOpInterfaceMethods<InferenceInterface>,
       DeclareOpInterfaceMethods<DynGlobalGenInterface>])> ;
```

- `TpuTypeRestrict`（TPU 类型限制特性）：对算子的输入 / 输出张量类型施加约束，确保符合 TPU 硬件的类型要求。
- `GlobalGenInterface`（全局生成接口）：定义算子的全局代码生成逻辑，用于将算子转换为 TPU 可执行的底层指令或中间表示。
- `InferenceInterface`（推理接口）:定义算子的推理计算逻辑，是算子执行实际数值运算的入口。
- `DynGlobalGenInterface`（动态全局生成接口）：处理动态场景下的全局代码生成，支持输入形状可变或参数动态调整的算子。

接着，同理我们看一个例子，是 Active 算子，这个算子是在/lib/Dialect/Tpu/Interfaces/Common/Active.cpp。

```cpp
#include "tpu_mlir/Dialect/Tpu/Transforms/Codegen/Dynamic/DynamicLayer.hpp"

#include "tpu_mlir/Support/ActiveUtils.h"       // 引入激活函数相关工具
#include "tpu_mlir/Support/GenericCpuFunc.h"    // 引入通用CPU函数支持
#include "tpu_mlir/Support/LutFunc.h"           // 引入查找表（LUT）函数支持

// 静态变量，用于存储输出张量的存储类型（如BF16、F16等）
static mlir::Type t;

/**
 * @brief ActiveOp算子的初始化函数
 * @param p 推理参数结构体，包含输入输出数据指针等信息
 * @return 初始化结果，success()表示成功
 * @note 激活算子逻辑简单，无需额外初始化操作，直接返回成功
 */
LogicalResult tpu::ActiveOp::init(InferenceParameter &p) { return success(); }

/**
 * @brief ActiveOp算子的资源释放函数
 * @param p 推理参数结构体
 * @note 激活算子未申请额外资源，无需清理操作，函数体为空
 */
void tpu::ActiveOp::deinit(InferenceParameter &p) {}

/**
 * @brief 激活函数的并行计算实现
 * @param p 推理参数结构体，包含输入输出数据
 * @param num 元素总数，即计算的循环范围
 * @param func 具体的激活函数（如ReLU、Sigmoid等）
 * @note 使用OpenMP并行加速，逐元素应用激活函数
 */
static void active_func(InferenceParameter &p, int64_t num, activate_f func) {
  // OpenMP并行循环，按元素数量静态分配任务，提高计算效率
#pragma omp parallel for schedule(static, omp_schedule(num))
  for (int i = 0; i < num; ++i) {
    // 对第i个输入元素应用激活函数，结果存入输出
    p.outputs[0][i] = func(p.inputs[0][i]);
  }
}

/**
 * @brief ActiveOp算子的核心推理函数，执行激活计算
 * @param p 推理参数结构体
 * @return 推理结果，success()表示成功
 * @note 实现激活函数的计算逻辑，并根据输出类型进行数据转换
 */
LogicalResult tpu::ActiveOp::inference(InferenceParameter &p) {
  // 获取输入张量形状，并将输出张量形状设置为与输入一致（激活函数不改变形状）
  auto in_shape = module::getShape(getInput());
  module::setShape(getOutput(), in_shape);
  
  // 记录输出张量的存储类型，用于后续数据转换
  t = module::getStorageType(getOutput());
  // 计算输入张量的元素总数，确定计算范围
  auto num_element = module::getNumElements(getInput());
  
  // 调用并行计算函数，应用当前算子指定的激活函数
  active_func(p, num_element, getActivateFunc(*this));
  
  // 根据输出类型，将计算结果转换为BF16或F16
  if (t.isBF16()) {
    BF16(p.outputs[0], p.outputs[0], num_element);  // 转换为BF16
  } else if (t.isF16()) {
    F16(p.outputs[0], p.outputs[0], num_element);  // 转换为F16
  }
  return success();
}

/**
 * @brief 判断当前算子是否支持本地代码生成（针对特定硬件）
 * @return 支持返回success()，否则返回failure()
 * @note 仅CV18xx平台支持ABSVAL（绝对值）模式的激活函数，其他模式不支持
 */
LogicalResult tpu::ActiveOp::LocalGenSupport() {
  if (module::isCV18xx()) {  // 若硬件平台为CV18xx
    if (getMode() == ActiveMode::ABSVAL) {  // 仅支持绝对值激活模式
      return success();
    } else {
      return failure();
    }
  }
  // 其他平台默认支持所有激活模式
  return success();
}

/**
 * @brief 为前端（FW）分配并设置激活层的参数
 * @param param 指向参数结构体的指针，用于存储配置信息
 * @note 填充激活类型、输入输出属性等参数，供底层计算使用
 */
void tpu::ActiveOp::assign_fw_param(void *param) {
  fw_active_layer_param_t layer_param = {0};  // 初始化激活层参数结构体
  
  // 设置激活函数类型（如ReLU、ABSVAL等）
  layer_param.active_type = (int)getMode();
  // 暂未实现ReLU上限功能，相关参数置0
  layer_param.if_relu = 0;
  layer_param.relu_upper_limit = 0.f;
  
  // 获取输入张量形状，计算输入通道数（IC）
  auto shape = module::getShape(getInput());
  layer_param.ic = shape.size() > 1 ? shape[1] : 1;
  
  // 暂未实现输入输出的缩放转换，相关参数置1.0
  layer_param.input_scale_back2float = 1.f;
  layer_param.output_scale_back2float = 1.f;
  
  // 设置输入和输出张量的符号属性（是否为有符号类型）
  layer_param.opd_sign = module::isSign(getInput());
  layer_param.res_sign = module::isSign(getOutput());
  
  // 将参数复制到目标指针，供底层使用
  memcpy(param, &layer_param, sizeof(fw_active_layer_param_t));
}

/**
 * @brief 获取算子的索引映射关系
 * @return 包含输入和输出映射的ArrayAttr
 * @note 激活函数为逐元素操作，输入和输出的索引映射均为恒等映射（形状完全一致）
 */
ArrayAttr tpu::ActiveOp::getIndexingMaps() {
  auto shape = module::getShape(getInput());
  // 创建与输入形状维度数相同的恒等映射（每个维度索引直接对应）
  AffineMap identity_map =
      AffineMap::getMultiDimIdentityMap(shape.size(), getContext());
  // 输入和输出均使用恒等映射（激活函数不改变元素索引关系）
  SmallVector<AffineMap> indexingMaps{identity_map, identity_map};
  return Builder(getContext()).getAffineMapArrayAttr(indexingMaps);
};

/**
 * @brief 判断当前算子是否支持多核并行计算
 * @return 始终返回false，表示不支持多核并行
 * @note 激活函数通常为轻量级逐元素操作，多核并行收益有限，故默认关闭
 */
bool tpu::ActiveOp::support_multi_core() { return false; }
```

## /lib/Dialect/Tpu/Interfaces/BM1684X/Active.cpp

因为 Tpu 算子会用于不同的硬件的代码生成，所以这里我们需要对不同的硬件生成额外的接口。

- LocalGenInterface 用于应用了 LayerGroup 的算子；
- 而没有应用 LayerGroup 的算子，则会使用 GlobalGenInterface；
- 因此，所有算子都会声明 GlobalGenInterface，但只有部分算子会额外实现 LocalGen 。

![](static/CSEzbz5IOoRnt0x6usPcCMOcn6g.png)

这个例子是针对 BM1684X 的 Active 算子。

```cpp
#include "tpu_mlir/Dialect/Tpu/Transforms/Codegen/Dynamic/DynamicLayer.hpp"
#include "tpu_mlir/Support/MathUtils.h"

using namespace tpu_mlir::backend;

// =========================================
// GlobalGenInterface 全局代码生成接口
// 用于生成全局范围内的算子执行代码（针对BM1684x平台）
// =========================================

/**
 * @brief 为BM1684x平台生成ActiveOp算子的全局代码
 * @note 全局代码指在整个张量上执行的操作，不涉及分片计算
 */
void tpu::ActiveOp::codegen_global_bm1684x() {
  // 初始化激活算子的全局配置结构体
  active_global_spec_t spec = {0};
  // 设置激活函数类型（如ReLU、Tanh等，从算子属性中获取）
  spec.common.active_type = (int)getMode();
  
  // 若算子包含系数参数（如某些激活函数的系数），则复制到配置中
  if (getCoeffs().has_value()) {
    const auto coeffs_ = module::getF64Array(getCoeffs().value());
    for (int i = 0; i < coeffs_->size(); ++i) {
      spec.common.coeffs[i] = (float)coeffs_->at(i);
    }
  }
  
  // 获取当前算子的输入/输出规格（如内存地址、形状等）
  auto op = getOperation();
  auto input_spec = BM168x::get_input_spec(op);
  auto output_spec = BM168x::get_output_spec(op);
  
  // 调用BM1684x平台的全局激活函数API，执行算子计算
  BM168x::call_global_func("backend_api_active_global", &spec, sizeof(spec),
                           input_spec->data(), output_spec->data());
}

// =========================================
// LocalGenInterface 本地代码生成接口
// 用于生成分片计算的代码（针对BM1684x平台），适用于大规模张量分块处理
// =========================================

/**
 * @brief 计算BM1684x平台上本地计算（分片）所需的缓冲区大小
 * @param in_lmem_bytes 输入张量在本地内存中的字节数
 * @param out_lmem_bytes 输出张量在本地内存中的字节数
 * @param in_* slice 输入张量各维度的分片大小（n/c/h/d/w）
 * @param out_* slice 输出张量各维度的分片大小（n/c/h/d/w）
 * @param group_type 分组计算类型
 * @return 所需缓冲区的字节数
 * @note 不同激活函数需要的临时缓冲区大小不同，根据激活模式动态计算
 */
int64_t tpu::ActiveOp::getBufferSize_bm1684x(
    int64_t in_lmem_bytes, int64_t out_lmem_bytes, int64_t in_nslice,
    int64_t in_cslice, int64_t in_hslice, int64_t in_dslice, int64_t in_wslice,
    int64_t out_nslice, int64_t out_cslice, int64_t out_hslice,
    int64_t out_dslice, int64_t out_wslice, group_type_t group_type) {
  // 获取输入张量的存储类型（如F32、BF16等），计算每个元素的字节数
  auto stype = module::getStorageType(getInput());
  int64_t dtype_len = stype.getIntOrFloatBitWidth() / 8;
  int64_t buffer_size = 0;  // 缓冲区大小，初始化为0
  // 计算单个分片的张量大小（按输入本地内存大小除以n维度分片数）
  int64_t tensor_size = in_lmem_bytes / in_nslice;
  
  // 根据激活函数模式，计算所需的缓冲区大小（不同激活函数需要的临时空间不同）
  switch (getMode()) {
    case ActiveMode::ERF:  // 误差函数激活
      // 需要3倍张量大小的空间，加上指数系数和ERF系数的对齐空间
      buffer_size = 3 * tensor_size;
      buffer_size += align_up(32 * dtype_len, Arch::EU_BYTES) +  // 32个指数系数，按EU字节对齐
                     align_up(10 * dtype_len, Arch::EU_BYTES);   // 10个ERF系数，按EU字节对齐
      break;
      
    case ActiveMode::TANH:    // 双曲正切
    case ActiveMode::MISH:    // Mish激活
    case ActiveMode::EXP:     // 指数函数
    case ActiveMode::ELU:     // ELU激活
    case ActiveMode::SWISH:   // Swish激活
    case ActiveMode::LOG_SIGMOID:  // 对数Sigmoid
    case ActiveMode::SILU:    // SILU激活（与Swish类似）
    case ActiveMode::SIGMOID: // Sigmoid激活
    case ActiveMode::TGELU:   // 近似GELU
    case ActiveMode::QGELU:   // 量化GELU
      // 需要2倍张量大小的工作空间，加上32个指数系数的对齐空间
      buffer_size = 2 * align_up(tensor_size, Arch::EU_BYTES);
      buffer_size += align_up(32 * dtype_len, Arch::EU_BYTES);
      
      // 特殊处理：MARS3或SGTPUV8平台上的BF16类型QGELU，需要额外工作空间和查找表
      if (module::isMARS3() || (module::isSGTPUV8() && 
          getMode() == ActiveMode::QGELU && stype.isBF16())) {
        buffer_size += align_up(tensor_size, Arch::EU_BYTES);  // 额外工作空间
        buffer_size += align_up(128 * dtype_len, Arch::EU_BYTES);  // 128个表项的查找表
      }
      break;
      
    case ActiveMode::SOFT_PLUS:  // SoftPlus激活
      // 需要2倍张量大小的工作空间，加上指数系数和对数系数的对齐空间
      buffer_size = 2 * align_up(tensor_size, Arch::EU_BYTES);
      buffer_size += align_up(32 * dtype_len, Arch::EU_BYTES);  // 指数系数
      // 对数系数：F32需要16个，其他类型需要8个，均按EU字节对齐
      if (stype.isF32())
        buffer_size += align_up(16 * dtype_len, Arch::EU_BYTES);
      else
        buffer_size += align_up(8 * dtype_len, Arch::EU_BYTES);
      break;
      
    case ActiveMode::GELU:  // GELU激活
      // 需要4倍张量大小的空间，加上指数系数和ERF系数的对齐空间
      buffer_size = 4 * tensor_size;
      buffer_size += align_up(32 * dtype_len, Arch::EU_BYTES) +  // 指数系数
                     align_up(10 * dtype_len, Arch::EU_BYTES);   // ERF系数
      // 特殊处理：MARS3或SGTPUV8平台上的BF16类型，需要额外查找表
      if (module::isMARS3() || (module::isSGTPUV8() && stype.isBF16())) {
        buffer_size += align_up(128 * dtype_len, Arch::EU_BYTES);
      }
      break;
      
    case ActiveMode::LN:    // 层归一化（此处作为激活函数处理）
    case ActiveMode::LOG2:  // 以2为底的对数
    case ActiveMode::TAN:   // 正切函数
    case ActiveMode::SIN:   // 正弦函数
    case ActiveMode::COS:   // 余弦函数
      // 需要2倍（张量大小+系数空间）的缓冲区
      buffer_size = 2 * (tensor_size + align_up(32 * dtype_len, Arch::EU_BYTES));
      break;
      
    case ActiveMode::ARCSIN:  // 反正弦
    case ActiveMode::ARCCOS:  // 反余弦
      // 需要1倍张量大小 + 系数空间的缓冲区
      buffer_size = tensor_size + align_up(32 * dtype_len, Arch::EU_BYTES);
      break;
      
    case ActiveMode::HSWISH:  // 硬Swish
    case ActiveMode::HSIGMOID: // 硬Sigmoid
      // 缓冲区大小等于输入本地内存大小（无需额外空间）
      buffer_size = in_lmem_bytes;
      break;
      
    default:  // 其他激活模式无需额外缓冲区
      break;
  }
  return buffer_size;
}

/**
 * @brief 为BM1684x平台生成ActiveOp算子的本地代码（分片计算）
 * @param n_step/c_step/... 各维度的分片步长
 * @param group_type 分组计算类型
 * @param sec_info 本地计算的分片信息（如起始/结束索引）
 * @note 本地代码针对张量的一个分片进行计算，适用于大规模张量分块处理
 */
void tpu::ActiveOp::codegen_local_bm1684x(int64_t n_step, int64_t c_step,
                                          int64_t h_step, int64_t d_step,
                                          int64_t w_step,
                                          group_type_t group_type,
                                          local_sec_info_t &sec_info) {
  // 获取当前算子的输入/输出规格（针对分片）
  auto op = getOperation();
  auto input_spec = BM168x::get_input_spec(op, group_type);
  auto output_spec = BM168x::get_output_spec(op, group_type);
  // 获取当前分片的组信息（如缓冲区地址、分片索引等）
  auto gi = getGroupInfo(n_step, h_step, d_step, w_step, c_step);

  // 初始化本地激活算子的配置结构体
  active_local_spec_t spec;
  memset(&spec, 0, sizeof(spec));  // 清零初始化
  spec.common.active_type = (int)getMode();  // 设置激活函数类型
  spec.buffer_addr = gi.buffer_addr;  // 设置缓冲区地址
  
  // 复制系数参数到配置中（若存在）
  if (getCoeffs().has_value()) {
    const auto coeffs_ = module::getF64Array(getCoeffs().value());
    for (int i = 0; i < coeffs_->size(); ++i) {
      spec.common.coeffs[i] = (float)coeffs_->at(i);
    }
  }

  // 调用BM1684x平台的本地激活函数API，执行分片计算
  BM168x::call_local_func("backend_api_active_local", &spec, sizeof(spec),
                          &sec_info, input_spec->data(), output_spec->data());
}

/**
 * @brief 为BM1684x平台生成动态本地代码（序列化配置参数到缓冲区）
 * @param buffer 存储配置参数的缓冲区指针（nullptr时返回所需大小）
 * @return 配置参数的字节数
 * @note 动态代码生成用于将算子配置序列化，供运行时动态加载
 */
int64_t tpu::ActiveOp::dyn_codegen_local_bm1684x(void *buffer) {
  // 若缓冲区为空，返回配置结构体的大小
  if (!buffer)
    return sizeof(active_local_spec_t);
  
  // 获取默认分组信息（用于缓冲区地址等）
  auto gi = getGroupInfo(0, 0, 0, 0, 0);
  // 初始化本地激活配置
  active_local_spec_t spec = {0};
  spec.common.active_type = (int)getMode();
  spec.buffer_addr = gi.buffer_addr;
  
  // 复制系数参数（若存在）
  if (getCoeffs().has_value()) {
    const auto coeffs_ = module::getF64Array(getCoeffs().value());
    for (int i = 0; i < coeffs_->size(); ++i) {
      spec.common.coeffs[i] = (float)coeffs_->at(i);
    }
  }

  // 将配置参数序列化到缓冲区
  return BM168x::dynamic_spec_to_buffer(buffer, spec);
}

// ======================================
// Dynamic GlobalGenInterface 动态全局代码生成接口
// 用于生成全局范围内的动态配置（针对BM1684x平台）
// ======================================

/**
 * @brief 为BM1684x平台生成动态全局代码（序列化配置参数到缓冲区）
 * @param buffer 存储配置参数的缓冲区指针（nullptr时返回所需大小）
 * @return 配置参数的字节数
 */
int64_t tpu::ActiveOp::dyn_codegen_global_bm1684x(void *buffer) {
  // 若缓冲区为空，返回配置结构体的大小
  if (!buffer)
    return sizeof(active_global_spec_t);
  
  // 初始化全局激活配置
  active_global_spec_t spec;
  memset(&spec, 0, sizeof(spec));  // 清零初始化
  spec.common.active_type = (int)getMode();  // 设置激活函数类型
  
  // 复制系数参数（若存在）
  if (getCoeffs().has_value()) {
    const auto coeffs_ = module::getF64Array(getCoeffs().value());
    for (int i = 0; i < coeffs_->size(); ++i) {
      spec.common.coeffs[i] = (float)coeffs_->at(i);
    }
  }
  
  // 将配置参数序列化到缓冲区
  return BM168x::dynamic_spec_to_buffer(buffer, spec);
}

/**
 * @brief 获取BM1684x平台上的前端（FW）类型
 * @return 前端类型标识（此处为激活算子类型）
 */
int64_t tpu::ActiveOp::get_fw_type_bm1684x() { return FW_BMNET_ACTIVE; }
```

## /include/tpu_mlir/Support/OpRewriterPatternEx.h

### 一、整体定位

这些类均是对 MLIR 原生模式重写机制（`OpRewritePattern`、`RewritePattern`、`ConversionPattern`）的扩展封装，主要用于 TPU-MLIR 框架中算子的转换流程（如将高层方言算子转换为底层可执行形式、进行类型转换等），核心解决以下问题：

1. 统一流程：为所有算子转换逻辑提供一致的 “匹配 - 重写 - 日志 - 统计” 流程模板。
2. 可扩展性：通过模板和虚函数，让开发者只需关注具体算子的转换逻辑（`matchAndRewriteImpl`），无需重复编写日志、统计、基础匹配框架。
3. 调试友好：增加匹配成功日志打印、匹配次数统计，方便调试和分析转换流程。

### 二、核心设计思路

基于模板方法模式（Template Method Pattern）：

- 父类（如 `OpRewriterPatternEx`）定义整体流程框架（`matchAndRewrite`），包含固定逻辑（日志、统计）和可变逻辑（`matchAndRewriteImpl`，由子类实现）。
- 子类只需实现 `matchAndRewriteImpl`，专注于具体算子 “如何匹配、如何重写”，降低开发复杂度。

### 三、各组件分工

1. `OpRewriterPatternEx<SourceOp>`
   - 适用场景：针对特定类型算子（`SourceOp`，如 `top::AddOp`）的转换，是最常用的算子重写基类。
   - 核心逻辑：
     - 调用 `matchAndRewriteImpl` 执行具体转换。
     - 重写成功时，更新匹配次数统计（线程安全）、打印调试日志。

   ```cpp
   ```

// 模板类，继承自 MLIR 的 OpRewritePattern，用于对特定类型算子（SourceOp）进行模式重写
template <typename SourceOp>
class OpRewriterPatternEx : public mlir::OpRewritePattern<SourceOp> {
public:
// 构造函数，初始化父类并记录模式名称、设置模式收益（用于模式优先级）
OpRewriterPatternEx(mlir::MLIRContext *context,
llvm::StringRef patternName = "",
mlir::PatternBenefit benefit = 1)
: mlir::OpRewritePattern<SourceOp>(context, benefit),
patternName(patternName) {}

```
// 重写 MLIR 模式匹配 - 重写流程中的 matchAndRewrite 方法，是模式匹配成功后执行重写的入口
mlir::LogicalResult
matchAndRewrite(SourceOp op, mlir::PatternRewriter &rewriter) const override {
    // 调用子类必须实现的 matchAndRewriteImpl 方法，执行具体的匹配与重写逻辑
    mlir::LogicalResult result = matchAndRewriteImpl(op, rewriter);
    if (mlir::succeeded(result)) {  // 如果重写成功
        if (!patternName.empty()) {  // 若模式名称非空，进行匹配次数统计
            std::lock_guard<std::mutex> lock(
                tpu_mlir::module::patternMatchCountsMutex);  // 加锁保证线程安全
            ++tpu_mlir::module::patternMatchCounts[patternName];  // 匹配次数加 1
        }
        if (shouldPrint(op) && !patternName.empty()) {  // 如果需要打印且模式名称非空
            PASS_LOG_DEBUG_BLOCK({  // 调试打印块（可根据编译配置控制是否生效）
                llvm::outs() << patternName << " : " << op.getOperationName()
                             << " succeed!";  // 打印模式名称和成功匹配重写的算子名称
            });
        }
    }
    return result;  // 返回重写结果（成功或失败等状态）
}
```

protected:
// 纯虚函数，要求子类必须实现，用于编写具体算子的匹配和重写逻辑
virtual mlir::LogicalResult
matchAndRewriteImpl(SourceOp op, mlir::PatternRewriter &rewriter) const = 0;

```
// 虚函数，控制是否打印重写成功的日志，默认返回 true（即打印），子类可重写修改行为
virtual bool shouldPrint(SourceOp op) const { return true; }
```

private:
std::string patternName;  // 记录当前模式的名称，用于日志和统计
// 静态方法，用于打印所有模式的匹配次数统计信息（当前代码中未在外部调用，可扩展用于调试）
static void printPatternMatchCounts() {
std::lock_guard[std::mutex](std::mutex) lock(tpu_mlir::module::patternMatchCountsMutex);
for (const auto &entry : tpu_mlir::module::patternMatchCounts) {
std::cout << "Pattern [" << entry.first << "] matched " << entry.second
<< " times.\n";
}
}
};

```

2. `OpRewriterPatternEx2<SourceOp, ElementType>`
	- 扩展点：额外引入 `ElementType` 模板参数，可用于针对特定元素类型（如 `Float32Type`、`Int8Type`）的算子处理，细化转换逻辑。
	- 典型用法：处理需区分数据类型的算子（如量化算子，需根据输入输出类型做不同转换）。
	```cpp
// 模板类，继承自 MLIR 的 OpRewritePattern，额外引入 ElementType 模板参数，可用于针对特定元素类型算子处理
template <typename SourceOp, typename ElementType>
class OpRewriterPatternEx2 : public mlir::OpRewritePattern<SourceOp> {
public:
    // 构造函数，初始化父类并记录模式名称、设置模式收益
    OpRewriterPatternEx2(mlir::MLIRContext *context,
                         llvm::StringRef patternName = "",
                         mlir::PatternBenefit benefit = 1)
        : mlir::OpRewritePattern<SourceOp>(context, benefit),
          patternName(patternName.str()) {}

    // 重写 matchAndRewrite 方法，执行模式匹配 - 重写流程
    mlir::LogicalResult
    matchAndRewrite(SourceOp op, mlir::PatternRewriter &rewriter) const override {
        // 调用子类实现的具体重写逻辑
        mlir::LogicalResult result = matchAndRewriteImpl(op, rewriter);
        if (mlir::succeeded(result)) {  // 重写成功时
            if (shouldPrint(op) && !patternName.empty()) {  // 按需打印日志
                PASS_LOG_DEBUG_BLOCK({
                    llvm::outs() << patternName << "_" << op.getOperationName()
                                 << " succeed!"
                                 << "\n";
                });
            }
        }
        return result;
    }

protected:
    // 纯虚函数，子类需实现具体算子的匹配和重写逻辑
    virtual mlir::LogicalResult
    matchAndRewriteImpl(SourceOp op, mlir::PatternRewriter &rewriter) const = 0;

    // 虚函数，控制是否打印日志，默认打印
    virtual bool shouldPrint(SourceOp op) const { return true; }

private:
    std::string patternName;  // 记录模式名称，用于日志
};
```

3. `OpRewriterPatternEx3`
   - 适用场景：更通用的算子匹配，不局限于特定 `SourceOp` 类型（通过 `MatchAnyOpTypeTag` 或指定 `typeName` 匹配任意 / 特定类型算子）。
   - 优势：可处理多类型算子的统一转换逻辑（如所有算术运算算子的通用优化）。

   ```cpp
   ```

// 类，继承自 MLIR 的 RewritePattern，可匹配任意类型算子（通过 MatchAnyOpTypeTag）进行重写
class OpRewriterPatternEx3 : public mlir::RewritePattern {
public:
// 构造函数一：匹配任意类型算子，初始化父类并记录模式名称、设置收益
OpRewriterPatternEx3(mlir::MLIRContext *context,
llvm::StringRef patternName = "",
mlir::PatternBenefit benefit = 1)
: mlir::RewritePattern(mlir::Pattern::MatchAnyOpTypeTag(), benefit,
context),
patternName(patternName.str()) {}

```
// 构造函数二：匹配指定类型（通过 typeName 指定）的算子，初始化父类并记录模式名称、设置收益
OpRewriterPatternEx3(mlir::MLIRContext *context, llvm::StringRef patternName,
                     mlir::PatternBenefit benefit, llvm::StringRef typeName)
    : mlir::RewritePattern(typeName, benefit, context),
      patternName(patternName.str()) {}

// 重写 matchAndRewrite 方法，处理 Operation 类型的算子（更通用，可处理各种算子）
mlir::LogicalResult
matchAndRewrite(Operation *op,
                mlir::PatternRewriter &rewriter) const override {
    // 调用子类实现的具体重写逻辑
    mlir::LogicalResult result = matchAndRewriteImpl(op, rewriter);
    if (mlir::succeeded(result)) {  // 重写成功时
        if (shouldPrint(op) && !patternName.empty()) {  // 按需打印日志
            PASS_LOG_DEBUG_BLOCK({
                llvm::outs() << patternName << "_" << op->getName().getStringRef()
                             << " succeed!"
                             << "\n";
            });
        }
    }
    return result;
}
```

protected:
// 纯虚函数，子类需实现具体的匹配和重写逻辑，参数为 Operation 指针，更通用
virtual mlir::LogicalResult
matchAndRewriteImpl(Operation *op, mlir::PatternRewriter &rewriter) const = 0;

```
// 虚函数，控制是否打印日志，默认打印
virtual bool shouldPrint(Operation *op) const { return true; }
```

private:
std::string patternName;  // 记录模式名称，用于日志
};

```

4. `ConversionPatternEx`
	- 适用场景：方言转换 + 类型转换场景（需配合 `TypeConverter`），处理算子时需同时调整操作数类型。
	- 典型用法：将一种方言的算子转换为另一种方言，并适配类型（如 `Top` 方言转 `Tpu` 方言时，处理量化类型转换）。
	```cpp
// 引入 MLIR 方言转换相关头文件，用于类型转换场景的模式重写
#include "mlir/Transforms/DialectConversion.h"
// 类，继承自 MLIR 的 ConversionPattern，用于在方言转换（类型转换）场景下进行模式重写
class ConversionPatternEx : public ConversionPattern {
public:
    // 构造函数，接收类型转换器、模式名称、收益、上下文，初始化父类并记录模式名称
    ConversionPatternEx(TypeConverter &typeConverter, StringRef patternName,
                        PatternBenefit benefit, MLIRContext *ctx)
        : ConversionPattern(typeConverter, patternName, benefit, ctx),
          patternName(patternName.str()) {}

    // 重写方言转换流程中的 matchAndRewrite 方法，处理算子及操作数，进行类型转换相关重写
    LogicalResult
    matchAndRewrite(Operation *op, ArrayRef<Value> operands,
                    ConversionPatternRewriter &rewriter) const override {
        // 调用子类实现的具体重写逻辑，处理算子和操作数
        LogicalResult result = matchAndRewriteImpl(op, operands, rewriter);
        if (succeeded(result)) {  // 重写成功时
            if (shouldPrint(op) && !patternName.empty()) {  // 按需打印日志
                PASS_LOG_DEBUG_BLOCK({
                    llvm::outs() << "Pattern [" << patternName
                                 << "] successfully applied to operation: "
                                 << op->getName().getStringRef() << "\n";
                });
            }
        }
        return result;
    }

protected:
    // 纯虚函数，子类需实现具体的匹配和重写逻辑，需处理操作数和类型转换
    virtual LogicalResult
    matchAndRewriteImpl(Operation *op, ArrayRef<Value> operands,
                        ConversionPatternRewriter &rewriter) const = 0;

    // 虚函数，控制是否打印日志，默认打印
    virtual bool shouldPrint(Operation *op) const { return true; }

private:
    std::string patternName;  // 记录模式名称，用于日志
};
```

## /include/tpu_mlir/Conversion/TopToTpu/TopLowering.h

这段代码是 TPU-MLIR 框架中算子下转换（Lowering）的核心实现，负责将高层抽象的 `Top` 方言算子转换为底层硬件可执行的 `Tpu` 方言算子，同时处理类型适配、量化转换、控制流适配等关键逻辑。以下从整体逻辑、核心功能及模块划分三个维度解析：

### 一、整体逻辑

代码的核心目标是实现 “高层算子 → 底层算子” 的转换，适配 TPU 硬件的计算能力（如支持的精度、内存布局、指令集等）。整体流程遵循 MLIR 的 “模式匹配 - 重写” 机制：

1. 类型转换：将输入张量转换为硬件支持的类型（如 INT8、BF16、F8 等），包括量化 / 反量化逻辑。
2. 算子映射：将 `Top` 方言的算子（如 `Add`、`Conv`）映射到 `Tpu` 方言的对应算子，补充硬件相关属性（如量化参数、计算模式）。
3. 控制流适配：将高层控制流算子（`If`、`Loop`）转换为硬件可执行的控制流结构，确保分支 / 循环逻辑正确映射。
4. 配置驱动：通过全局配置（如量化开关、算法选择）动态调整转换行为，适配不同硬件平台和精度需求。

### 二、核心功能

1. 类型转换与量化处理
   实现张量在不同数据类型（整数、浮点、量化类型）之间的转换，满足硬件对数据精度的要求：

   - 支持 INT4/INT8/INT16 等整数类型，F16/BF16/F32/F8 等浮点类型的转换。
   - 提供量化（`do_requant`）、反量化（`do_dequant`）函数，处理 “量化空间 ↔ 原始空间” 的数值映射（如 INT8→INT32→INT8）。
   - 通过 `getQuantXXXType` 系列函数生成目标类型（如 `getQuantInt8Type` 生成 INT8 量化类型）。
2. 控制流算子转换
   针对 `If`（条件分支）和 `Loop`（循环）等控制流算子，实现从 `Top` 方言到 `Tpu` 方言的适配：

   - 转换分支 / 循环体的结构，将原终止符（如 `ReturnOp`）替换为 `Tpu` 方言的 `YieldOp`（硬件认可的分支返回指令）。
   - 根据硬件支持的精度自动调整输出类型（如 INT8 模式下分支输出转为 INT8）。
3. 通用算子下转换框架
   为所有 `Top` 方言算子提供统一的下转换接口，通过模板类和虚函数实现灵活扩展：

   - `TopLowering`：通用算子的基类，根据数据类型（INT8/F16 等）调用对应的 `LoweringXXX` 方法（如 `LoweringINT8` 处理 INT8 精度）。
   - `TopShapeLowering`：形状相关算子（如 `Reshape`、`Concat`）的基类，专注于形状转换逻辑。
4. 硬件适配与配置
   通过配置动态调整转换行为，适配不同硬件平台和场景：

   - `LoweringConfig`：存储全局配置（如是否量化、是否启用 Winograd 算法加速卷积）。
   - `getOpQuantMode`：根据算子名称和硬件限制（如部分平台不支持 F16）确定实际量化模式，确保硬件兼容性。

### 三、模块划分及功能

代码按功能可划分为 8 个核心模块，各模块职责如下：

1. 类型转换工具（Type Conversion Utilities）

- 功能：提供张量类型转换的基础接口，生成硬件支持的目标类型。
- 核心接口：

  - `getQuantInt16Type`/`getQuantInt8Type` 等：生成整数量化类型。
  - `getQuantFloatType`（模板）：生成浮点类型（如 F16、BF16）。
  - `ScfTypeConverter`：控制流算子的类型转换器，确保分支 / 循环中类型合法。

```cpp
/// 获取量化后的INT16类型（用于张量）
/// @param v: 输入张量值
/// @param asymmetric: 是否采用非对称量化（对称量化偏移为0，非对称可能有偏移）
/// @return 适配后的INT16量化类型（RankedTensorType）
mlir::Type getQuantInt16Type(Value v, bool asymmetric = false);

/// 获取量化后的INT8类型
mlir::Type getQuantInt8Type(Value v, bool asymmetric = false);

/// 获取指定缩放因子、偏移量和位宽的量化整数类型
/// @param v: 输入张量
/// @param scale: 量化缩放因子（浮点范围到整数范围的映射比例）
/// @param offset: 量化偏移量（非对称量化时使用）
/// @param bits: 量化位宽（默认8位）
mlir::Type getQuantIntType(Value v, double scale, double offset, int bits = 8);

/// 获取量化后的INT4类型
mlir::Type getQuantInt4Type(Value v, bool asymmetric = false);

/// 获取量化后的布尔类型
mlir::Type getQuantBoolType(Value v);

/// 模板函数：获取量化后的浮点类型（如F16、BF16等）
/// @tparam ElemTy: 目标浮点元素类型（如mlir::Float16Type）
/// @param v: 输入张量
template <typename ElemTy>
static mlir::Type getQuantFloatType(Value v);

/// 控制流（SCF）类型转换器
/// 用于在控制流算子（如If、Loop）转换时处理输入/输出类型适配，确保类型合法性
class ScfTypeConverter : public TypeConverter {
public:
  ScfTypeConverter() {
    // 类型转换优先级：后添加的转换器会被优先尝试
    // 1. 默认转换：直接返回原类型（若类型合法）
    addConversion([](Type type) { return type; });
    // 2. 张量类型转换：检查元素类型是否合法，合法则返回原张量类型
    addConversion([&](TensorType type) -> std::optional<Type> {
      if (isLegal(type.getElementType()))
        return type;
      return std::nullopt; // 元素类型不合法时返回空（转换失败）
    });

    // 源类型实例化：将输入值转换为目标类型（通过UnrealizedConversionCastOp临时转换）
    addSourceMaterialization([&](OpBuilder &builder, Type resultType,
                                 ValueRange inputs,
                                 Location loc) -> std::optional<Value> {
      if (inputs.size() != 1)
        return std::nullopt; // 仅支持单输入转换

      // 创建临时转换算子，将输入转换为目标类型
      return builder.create<UnrealizedConversionCastOp>(loc, resultType, inputs)
          .getResult(0);
    });

    // 目标类型实例化：类似源类型，用于反向转换
    addTargetMaterialization([&](OpBuilder &builder, Type resultType,
                                 ValueRange inputs,
                                 Location loc) -> std::optional<Value> {
      if (inputs.size() != 1)
        return std::nullopt;

      return builder.create<UnrealizedConversionCastOp>(loc, resultType, inputs)
          .getResult(0);
    });
  }

  /// 检查函数类型签名是否合法（所有输入/输出类型均可通过转换）
  bool isSignatureLegal(mlir::FunctionType funcType) {
    return llvm::all_of(llvm::concat<const mlir::Type>(funcType.getInputs(),
                                                       funcType.getResults()),
                        [this](mlir::Type type) { return isLegal(type); });
  }

  /// 检查CallOp的类型签名是否合法（参数和返回值类型均可转换）
  bool isSignatureLegal(mlir::func::CallOp call) {
    auto f = [this](mlir::Type type) { return isLegal(type); };
    return llvm::all_of(call.getOperandTypes(), f) &&
           llvm::all_of(call.getResultTypes(), f);
  }
};
```

1. 控制流转换（Control Flow Lowering）

- 功能：转换 `If` 和 `Loop` 等控制流算子，适配硬件的控制流指令。
- 核心类：

  - `IfOpLowering`：将 `Top::IfOp` 转换为 `Tpu::IfOp`，迁移分支体并替换终止符。
  - `LoopOpLowering`：将 `Top::LoopOp` 转换为 `Tpu::LoopOp`，调整循环参数类型并更新输出。

  ```cpp
  ```

/// Top 方言 IfOp 到 Tpu 方言 IfOp 的下转换类
/// 负责将高层条件分支算子转换为硬件可执行的条件分支算子，处理类型适配和分支结构
class IfOpLowering : public ConversionPatternEx {
public:
/// 构造函数：绑定 IfOp 类型和转换上下文
/// @param typeConverter: 类型转换器
/// @param ctx: MLIR 上下文
explicit IfOpLowering(TypeConverter &typeConverter, MLIRContext *ctx)
: ConversionPatternEx(typeConverter, top::IfOp::getOperationName(), 1,
ctx) {}

protected:
/// 匹配 Top::IfOp 并转换为 Tpu::IfOp
LogicalResult
matchAndRewriteImpl(Operation *op, ArrayRef<Value> operands,
ConversionPatternRewriter &rewriter) const override {
std::vector[mlir::Type](mlir::Type) new_types; // 转换后 IfOp 的输出类型列表
auto real_mode = module::getMode(); // 获取全局执行模式（如 INT8、F16 等）
// 统一 F16/BF16 模式别名（将带权重类型的模式映射为基础模式）
if (module::isF16Modes()) {
real_mode = module::Mode::F16;
} else if (module::isBF16Modes()) {
real_mode = module::Mode::BF16;
}
// 根据执行模式确定每个输出的目标类型
for (int i = 0; i < op->getNumResults(); i++) {
switch (real_mode) {
case module::Mode::INT8:
new_types.push_back(getQuantInt8Type(op->getResult(i), module::isAsymmetric()));
break;
case module::Mode::INT4:
// INT4 暂用 INT8 类型过渡（硬件可能不直接支持 INT4 存储）
new_types.push_back(getQuantInt8Type(op->getResult(i), module::isAsymmetric()));
break;
case module::Mode::F16:
new_types.push_back(getQuantFloatType[mlir::Float16Type](mlir::Float16Type)(op->getResult(i)));
break;
case module::Mode::BF16:
new_types.push_back(getQuantFloatType[mlir::BFloat16Type](mlir::BFloat16Type)(op->getResult(i)));
break;
default:
new_types.emplace_back(op->getResultTypes()[i]); // 其他模式保持原类型
break;
}
}

```
// 创建Tpu方言IfOp，继承原IfOp的位置、操作数和属性
auto tpuIfOp = rewriter.create<tpu::IfOp>(
    op->getLoc(), new_types, op->getOperands(), op->getAttrs());
// 为Then/Else分支创建基本块（初始化分支结构）
rewriter.createBlock(&(tpuIfOp.getThenBranch()));
rewriter.createBlock(&(tpuIfOp.getElseBranch()));
auto ifOp = dyn_cast<top::IfOp>(op); // 转型为Top::IfOp以获取分支
// 转换Then分支：将原分支内容迁移到Tpu IfOp的Then分支
graphToTpuBranch(rewriter, op->getLoc(), ifOp.getThenBranch(),
                 tpuIfOp.getThenBranch());
// 转换Else分支：同理处理Else分支
graphToTpuBranch(rewriter, op->getLoc(), ifOp.getElseBranch(),
                 tpuIfOp.getElseBranch());
// 替换原IfOp的所有使用为新的Tpu IfOp，并删除原算子
op->replaceAllUsesWith(tpuIfOp.getOperation());
rewriter.eraseOp(op);
return success();
```

}

/// 重写时是否打印日志（此处禁用，避免冗余输出）
bool shouldPrint(Operation *op) const override { return false; }

private:
/// 将原分支（Region）内容迁移到 Tpu 算子的分支，并替换终止符
/// @param rewriter: 模式重写器
/// @param loc: 位置信息
/// @param graph: 原 Top 方言分支区域
/// @param tpuBranch: Tpu 方言算子的目标分支区域
void graphToTpuBranch(PatternRewriter &rewriter, Location loc, Region &graph,
Region &tpuBranch) const {
OpBuilder::InsertionGuard insertGuard(rewriter); // 保存当前插入点，避免干扰

```
// 清空目标分支原有块，接管原分支的所有操作
rewriter.eraseBlock(&tpuBranch.back());
tpuBranch.takeBody(graph);
rewriter.setInsertionPointToEnd(&tpuBranch.back()); // 移动插入点到新分支末尾

// 将原分支的终止符（ReturnOp）替换为Tpu方言的YieldOp（分支返回算子）
Operation *returnOp = tpuBranch.back().getTerminator();
rewriter.replaceOpWithNewOp<tpu::YieldOp>(returnOp, returnOp->getOperands());
```

}
};

/// Top 方言 LoopOp 到 Tpu 方言 LoopOp 的下转换类
/// 负责将高层循环算子转换为硬件可执行的循环算子，处理迭代逻辑和类型适配
class LoopOpLowering : public ConversionPatternEx {
public:
/// 构造函数：绑定 LoopOp 类型和转换上下文
explicit LoopOpLowering(TypeConverter &typeConverter, MLIRContext *ctx)
: ConversionPatternEx(typeConverter, top::LoopOp::getOperationName(), 1,
ctx) {}

protected:
/// 匹配 Top::LoopOp 并转换为 Tpu::LoopOp
LogicalResult
matchAndRewriteImpl(Operation *op, ArrayRef<Value> operands,
ConversionPatternRewriter &rewriter) const override {
std::vector[mlir::Type](mlir::Type) new_types; // 转换后 LoopOp 的输出类型列表
auto real_mode = module::getMode();
if (module::isF16Modes()) {
real_mode = module::Mode::F16;
} else if (module::isBF16Modes()) {
real_mode = module::Mode::BF16;
}
// 根据执行模式确定输出类型（逻辑同 IfOp）
for (int i = 0; i < op->getNumResults(); i++) {
switch (real_mode) {
case module::Mode::INT8:
new_types.push_back(getQuantInt8Type(op->getResult(i), module::isAsymmetric()));
break;
case module::Mode::INT4:
new_types.push_back(getQuantInt8Type(op->getResult(i), module::isAsymmetric()));
break;
case module::Mode::F16:
new_types.push_back(getQuantFloatType[mlir::Float16Type](mlir::Float16Type)(op->getResult(i)));
break;
case module::Mode::BF16:
new_types.push_back(getQuantFloatType[mlir::BFloat16Type](mlir::BFloat16Type)(op->getResult(i)));
break;
default:
new_types.emplace_back(op->getResultTypes()[i]);
break;
}
}

```
// 创建Tpu方言LoopOp，继承原LoopOp的位置、操作数和属性
auto tpuLoopOp = rewriter.create<tpu::LoopOp>(
    op->getLoc(), new_types, op->getOperands(), op->getAttrs());
// 为Loop体创建基本块
rewriter.createBlock(&(tpuLoopOp.getBody()));
auto loopOp = dyn_cast<top::LoopOp>(op); // 转型为Top::LoopOp以获取循环体
// 转换循环体：将原Loop体迁移到Tpu LoopOp的体
graphToTpuBranch(rewriter, op->getLoc(), loopOp.getBody(),
                 tpuLoopOp.getBody());

// 调整循环体参数类型，与输入操作数类型保持一致
for (int i = 0; i < tpuLoopOp.getBody().getNumArguments(); i++) {
  auto type = tpuLoopOp.getOperand(i).getType();
  tpuLoopOp.getBody().getArgument(i).setType(type);
}

// 更新LoopOp的输出类型（与循环体终止符的输出类型一致）
auto yieldOp = tpuLoopOp.getBody().front().getTerminator();
for (int i = 0; i < tpuLoopOp.v_final().size(); i++) {
  auto type = yieldOp->getOperand(i + 1).getType();
  tpuLoopOp.getResult(i).setType(type);
}
// 替换原LoopOp的所有使用为新的Tpu LoopOp，并删除原算子
op->replaceAllUsesWith(tpuLoopOp.getOperation());
rewriter.eraseOp(op);
return success();
```

}

/// 重写时是否打印日志（禁用）
bool shouldPrint(Operation *op) const override { return false; }

private:
/// 将原循环体迁移到 Tpu LoopOp 的体，并替换终止符
void graphToTpuBranch(PatternRewriter &rewriter, Location loc, Region &graph,
Region &tpuBranch) const {
OpBuilder::InsertionGuard insertGuard(rewriter);

```
rewriter.eraseBlock(&tpuBranch.back());
tpuBranch.takeBody(graph);
rewriter.setInsertionPointToEnd(&tpuBranch.back());

// 将原循环体的终止符替换为Tpu::YieldOp
Operation *returnOp = tpuBranch.back().getTerminator();
rewriter.replaceOpWithNewOp<tpu::YieldOp>(returnOp, returnOp->getOperands());
```

}
};

```

1. 下转换配置（Lowering Configuration）

- 功能：存储和管理转换过程中的全局配置，控制转换行为。

- 核心结构：
	- `LoweringConfig`：包含量化开关（`isQuantized`）、Winograd 算法开关（`doWinograd`）、算子特定量化模式（`quantize_map`）等。
	```cpp
/// 下转换配置结构体
/// 存储全局下转换参数，控制量化、算法选择等行为
struct LoweringConfig {
  static bool isQuantized; /// 是否启用量化模式（影响类型转换逻辑）
  static bool doWinograd;  /// 是否对卷积启用Winograd算法（加速卷积计算）
  /// 算子量化模式映射：key为算子名，value为该算子的特定量化模式（覆盖全局模式）
  static std::map<std::string, module::Mode> quantize_map;
  /// 算子拆分映射：存储需要拆分的算子配置（复杂算子拆分为多个简单算子）
  static std::map<std::string, std::set<std::string>> split_map;

  /// 更新算子的量化模式（添加/覆盖算子特定配置）
  static void update(const std::string &name, module::Mode mode) {
    quantize_map[name] = mode;
  }
};
```

1. 算子下转换基类（Lowering Base Classes）

- 功能：定义算子下转换的统一接口，为具体算子提供实现模板。
- 核心类：

  - `TopLowering`：通用算子基类，提供 `LoweringINT8`/`LoweringF16` 等虚函数，子类需实现具体转换逻辑。
  - `TopShapeLowering`：形状算子基类，专注于形状转换（如 `Reshape`），仅需实现 `Lowering` 方法。

```cpp
/// Top形状算子下转换基类（模板类）
/// 专门用于形状相关算子（如Reshape、Concat）的下转换，仅需实现形状转换逻辑
template <typename OpTy>
class TopShapeLowering : public OpRewriterPatternEx<OpTy> {
public:
  /// 构造函数：绑定算子类型和MLIR上下文
  TopShapeLowering(mlir::MLIRContext *context)
      : OpRewriterPatternEx<OpTy>(context) {}

protected:
  /// 匹配算子并执行形状下转换
  mlir::LogicalResult
  matchAndRewriteImpl(OpTy opTy,
                      mlir::PatternRewriter &rewriter) const override {
    Lowering(rewriter, opTy); // 调用形状转换实现
    return success();
  }

public:
  /// 形状下转换接口（需子类实现）
  virtual void Lowering(PatternRewriter &rewriter, OpTy opTy) const {
    UNREACHABLE_OP("Not Implemented", opTy);
  }

  /// 是否打印日志（默认不打印）
  bool shouldPrint(OpTy opTy) const override { return false; }
};
```

1. 通用转换函数（Common Lowering Functions）

- 功能：封装重复的算子转换逻辑，简化具体算子的实现。
- 核心函数：

  - `lowering_common`：通用转换逻辑，根据目标类型创建 `Tpu` 算子，处理操作数（尤其是权重的类型转换）。
  - `lowering_common_int8`/`lowering_common_f16`：针对特定类型的转换封装（如 INT8、F16）。

  ```cpp
  ```

_/// 获取 F8E5M2 量化类型（5 位指数 +2 位尾数）_
Type getQuantF8E5M2Type(Value v);

_/// F8 下转换：将原算子转换为 F8 类型的目标算子_
template <typename OpTy>
static OpTy lowering_common_f8(PatternRewriter &rewriter, Operation *from,
bool isE4, int num_operands = 0) {
}

_/// 模板函数实现：获取量化浮点类型（如 F16、BF16）_
template <typename ElemTy>
static mlir::Type getQuantFloatType(Value v) {
}

_/// 获取 BF16 量化类型（封装 getQuantFloatType）_
static mlir::Type getQuantBF16Type(Value v) {
}

_/// 获取 F16 量化类型（封装 getQuantFloatType）_
static mlir::Type getQuantF16Type(Value v) {
}

_/// 浮点类型通用下转换：将原算子转换为指定浮点类型的目标算子_
template <typename OpTy, typename ElemTy>
static OpTy lowering_common_float(PatternRewriter &rewriter, Operation *from,
int num_operands = 0) {
}

_/// F32 下转换（封装 lowering_common_float）_
template <typename OpTy>
static OpTy lowering_common_f32(PatternRewriter &rewriter, Operation *from,
int num_operands = 0) {
}

_/// BF16 下转换（封装 lowering_common_float）_
template <typename OpTy>
static OpTy lowering_common_bf16(PatternRewriter &rewriter, Operation *from,
int num_operands = 0) {
}

_/// F16 下转换（封装 lowering_common_float）_
template <typename OpTy>
static OpTy lowering_common_f16(PatternRewriter &rewriter, Operation *from,
int num_operands = 0) {
}

```

1. 量化 / 反量化工具（Quantization Utilities）

- 功能：处理量化相关的数值转换，确保计算在量化空间中正确执行。

- 核心函数：
	- `do_dequant`：将量化类型（如 INT8）反量化为原始类型（如 INT32）。
	- `do_requant`：将原始类型（如 INT32）重新量化为目标类型（如 INT8）。
	- `do_requantFp`：浮点类型的量化转换（如 F32→INT8）。

```cpp
/// 反量化：从INT8转换为INT32（用于计算中间结果）
/// @param name_loc: 位置信息（用于错误定位）
/// @param input: 输入INT8值
/// @param to_type: 目标INT32类型
/// @param multiplier: 乘法因子（量化参数）
/// @param rshift: 右移位数（量化参数）
/// @param mode: 反量化模式（算法选择）
/// @param lshift: 左移位数（补充计算）
/// @param rmode: 舍入模式
Value do_dequant(Location name_loc, Value input, Type to_type,
                 int64_t multiplier, int64_t rshift, tpu::DequantMode mode,
                 int64_t lshift,
                 tpu::RoundMode rmode = tpu::RoundMode::HalfAwayFromZero);

/// 重量化：从INT32转换为INT8（将计算结果转回量化空间）
/// @param name_loc: 位置信息
/// @param input: 输入INT32值
/// @param to_type: 目标INT8类型
/// @param tensorType: 是否为张量类型
/// @param multiplier: 乘法因子
/// @param shift: 移位位数
/// @param mode: 重量化模式
/// @param rmode: 舍入模式
Value do_requant(Location name_loc, Value input, Type to_type, bool tensorType,
                 int64_t multiplier, int64_t shift, tpu::RequantMode mode,
                 tpu::RoundMode rmode = tpu::RoundMode::HalfAwayFromZero);

/// 重量化（带量化参数）：使用量化参数（scale/zp）从INT32转换为INT8
Value do_requant(Location name_loc, Value input, Value quant, Type to_type,
                 bool tensorType, tpu::RequantMode mode,
                 tpu::RoundMode rmode = tpu::RoundMode::HalfAwayFromZero);

/// 按轴重量化：支持沿指定轴（如通道轴）进行重量化（适配分组量化）
Value do_requant_axis(Location name_loc, Value input, Value quant, Type to_type,
                      bool tensorType, tpu::RequantMode mode,
                      tpu::RoundMode rmode = tpu::RoundMode::HalfAwayFromZero,
                      int64_t rq_axis = 1, bool fuse_rq = false);

/// 浮点重量化：从浮点类型转换为量化类型（如F32到INT8）
Value do_requantFp(
    Value input, double scale, double offset, Type to_type,
    std::string &to_name,
    tpu::RequantMode mode = tpu::RequantMode::MultiplierShift,
    tpu::RoundMode rmode = tpu::RoundMode::HalfAwayFromZero,
    tpu::RoundMode first_rmode = tpu::RoundMode::HalfAwayFromZero);

/// 浮点重量化（带量化参数）：使用量化参数从浮点转换为量化类型
Value do_requantFp(
    Value input, Value quant, Type to_type, bool tensorType,
    std::string &to_name, tpu::RequantMode mode,
    tpu::RoundMode rmode = tpu::RoundMode::HalfAwayFromZero,
    tpu::RoundMode first_rmode = tpu::RoundMode::HalfAwayFromZero);

/// 将字符串模式转换为tpu::RequantMode枚举（如"multiplier_shift"转MultiplierShift）
tpu::RequantMode get_requant_mode(std::string mode);

/// 将字符串模式转换为tpu::DequantMode枚举
tpu::DequantMode get_dequant_mode(std::string mode);

/// 将字符串模式转换为tpu::RoundMode枚举（如"half_away_from_zero"转对应枚举）
tpu::RoundMode get_round_mode(std::string mode);

/// 权重反量化函数
/// 功能：将量化的权重张量（如INT8）反量化为目标类型（如F32），应用量化参数
/// @param input: 量化的权重张量
/// @param to_type: 反量化后的目标类型
/// @param multiplier: 量化乘法因子（用于恢复原始范围）
/// @param shift: 量化移位参数（用于恢复原始范围）
/// @param lshift: 额外左移参数（补充计算调整）
/// @return 反量化后的权重张量
Value do_weight_dequant(Value input, Type to_type, int64_t multiplier,
                        int64_t shift, int64_t lshift);

/// 常量反量化函数
/// 功能：对量化的常量值（标量）执行反量化计算，返回INT32结果
/// @param input: 量化的常量值
/// @param multiplier: 量化乘法因子
/// @param shift: 量化移位参数
/// @param lshift: 额外左移参数
/// @return 反量化后的INT32值
int32_t do_const_dequant(Value input, int64_t multiplier, int64_t shift,
                         int64_t lshift);

/// 数据传输转换：在不同量化空间之间转换（如INT8到INT8，调整scale/zp）
/// @param in: 输入值（源量化空间）
/// @param out: 输出值（目标量化空间，用于获取类型）
/// @param asymmetric: 是否为非对称量化
Value do_transfer(Value in, Value out, bool asymmetric);

/// 浮点数据传输转换：量化浮点类型之间的转换（如F16到BF16）
Value do_transfer_fp(Value in, Value out, bool asymmetric,
                     tpu::RoundMode rmode = tpu::RoundMode::HalfAwayFromZero);

/// 获取F8E4M3量化类型（4位指数+3位尾数）
Type getQuantF8E4M3Type(Value v);

/// 获取F8E5M2量化类型（5位指数+2位尾数）
Type getQuantF8E5M2Type(Value v);

/// 模板函数实现：获取量化浮点类型（如F16、BF16）
template <typename ElemTy>
static mlir::Type getQuantFloatType(Value v) {
  Type newType = v.getType();
  if (newType.isa<mlir::NoneType>()) {
    return newType; // NoneType直接返回
  }
  auto sType = module::getStorageType(v);
  if (sType.isa<ElemTy>() == false) {
    // 若存储类型不是目标浮点类型，构造新类型
    auto shape = module::getShape(v); // 获取张量形状
    auto ctx = v.getContext();
    if (module::isCalibratedType(v)) {
      // 校准类型（带min/max范围）：保留校准信息，更新元素类型
      auto caliType = module::getCalibratedType(v);
      auto newCaliType = quant::CalibratedQuantizedType::get(
          ElemTy::get(ctx), caliType.getMin(), caliType.getMax());
      newType = RankedTensorType::get(shape, newCaliType);
    } else {
      // 普通类型：直接构造目标浮点类型的张量
      newType = RankedTensorType::get(shape, ElemTy::get(ctx));
    }
  }
  return newType;
}

/// 获取BF16量化类型（封装getQuantFloatType）
static mlir::Type getQuantBF16Type(Value v) {
  return getQuantFloatType<BFloat16Type>(v);
}

/// 获取F16量化类型（封装getQuantFloatType）
static mlir::Type getQuantF16Type(Value v) {
  return getQuantFloatType<Float16Type>(v);
```

1. 张量操作工具（Tensor Operation Utilities）

- 功能：辅助处理张量的形状调整、维度转换等操作。
- 核心函数：

  - `do_reshape`：重塑张量形状。
  - `do_transpose`：调整张量维度顺序。
  - `do_binary_saclar`：生成标量二元运算（如 `AddConst`、`MulConst`）。

  ```cpp
  ```

/// 二元标量操作生成函数
/// 功能：对输入张量应用标量二元运算（如加、减、乘、除常量），生成新的算子节点
/// @tparam OpTy: 目标二元算子类型（如 tpu::AddConstOp、tpu::MulConstOp 等）
/// @param input: 输入张量值（运算的左操作数）
/// @param to_type: 输出张量的目标类型
/// @param scalar: 标量常量（运算的右操作数）
/// @param suffix: 新算子名称的后缀（用于区分不同操作，默认"_binary"）
/// @return 新生成算子的输出张量
template <typename OpTy>
Value do_binary_saclar(Value input, Type to_type, int64_t scalar,
const char *suffix = "_binary") {
// 获取输入张量的存储类型（如 INT8、F16 等，[[maybe_unused]]标记表示可能未使用但保留）
[[maybe_unused]] auto from_stype = module::getStorageType(input);
// 获取目标类型的存储类型
[[maybe_unused]] auto to_stype = module::getStorageType(to_type);
// 获取 MLIR 上下文（用于创建新算子）
auto ctx = input.getContext();
// 创建算子构建器（用于构造新的 IR 节点）
OpBuilder builder(ctx);

// 构造输出张量类型：保持输入张量的形状，使用目标存储类型
auto newType = to_type;
newType = RankedTensorType::get(module::getShape(input), to_stype);

// 设置插入点：在输入张量的定义之后插入新算子（保证 IR 的执行顺序）
builder.setInsertionPointAfterValue(input);
// 准备算子属性：存储标量常量值（以 F64 类型存储，兼容各类计算）
std::vector<NamedAttribute> attrs;
attrs.push_back(
builder.getNamedAttr("const_val", builder.getF64FloatAttr(scalar)));

// 生成新算子的名称：原输入算子名称 + 后缀（确保名称唯一）
std::string new_name = module::getName(input.getDefiningOp()).str() + suffix;
// 创建带名称的位置信息（用于错误提示和调试）
auto name_loc = NameLoc::get(builder.getStringAttr(new_name));
// 创建目标类型的二元算子，并传入输入张量、属性
auto newOp =
builder.create<OpTy>(name_loc, newType, ValueRange{input}, attrs);
// 返回新算子的输出张量
return newOp.getOutput();
}

/// F8 类型 ReLU 激活函数
/// 功能：对 F8 类型的输入张量应用 ReLU 激活（带上限裁剪），输出指定类型
/// @param input: F8 类型的输入张量
/// @param to_type: 输出张量的目标类型
/// @param relu_limit: ReLU 的上限值（超过此值的元素将被裁剪）
/// @return 激活后的输出张量
Value do_f8_relu(Value input, Type to_type, double relu_limit);

/// 张量形状重塑函数
/// 功能：将输入张量重塑为指定的目标形状（元素总数保持不变）
/// @param input: 待重塑的输入张量
/// @param to_type: 目标形状的 RankedTensorType（包含新形状信息）
/// @return 重塑后的张量
Value do_reshape(Value input, RankedTensorType to_type);

/// 张量维度转置函数
/// 功能：按照指定的维度顺序重新排列输入张量的维度
/// @param name_loc: 位置信息（用于错误定位）
/// @param input: 待转置的输入张量
/// @param order: 新的维度顺序（如[2,0,1]表示将原维度 0→1，1→2，2→0）
/// @return 转置后的张量
Value do_transpose(Location name_loc, Value input, std::vector<int64_t> &order);

```

1. 数据传输工具（Data Transfer Utilities）

- 功能：处理数据在主机（CPU）和设备（TPU）之间的传输。

- 核心函数：
	- `try_insert_host2device`/`try_insert_device2host`：自动插入主机与设备间的数据传输算子。
	- `insert_device2host`：手动插入设备到主机的传输算子。
	```cpp
/// 尝试插入主机到设备数据传输算子
/// 功能：在指定算子的第idx个输入前插入tpu::Host2DeviceOp，将数据从主机内存传输到设备内存
/// @param op: 目标算子（需要输入数据的算子）
/// @param idx: 输入参数的索引（指定哪个输入需要传输）
void try_insert_host2device(Operation *op, uint32_t idx);

/// 尝试插入设备到主机数据传输算子
/// 功能：在指定算子的第idx个输入前插入tpu::Device2HostOp，将数据从设备内存传输到主机内存
/// @param op: 目标算子（需要输入数据的算子）
/// @param idx: 输入参数的索引（指定哪个输入需要传输）
void try_insert_device2host(Operation *op, uint32_t idx);

/// 插入设备到主机数据传输算子
/// 功能：将指定张量从设备内存传输到主机内存，并转换为目标类型
/// @param v: 设备端的张量
/// @param to: 传输后的目标类型
/// @param user: 使用传输结果的算子（用于确定插入位置，默认为nullptr）
/// @return 传输到主机后的张量
Value insert_device2host(Value v, Type to, Operation *user = nullptr);
```

## /include/tpu_mlir/Conversion/TopToTpu/LoweringBM1684X.h

这个文件是“Top 方言到 TPU 方言” 转换流程的关键组成部分，其核心功能是定义算子转换类、声明转换规则注册函数，为模型从高层抽象（Top 方言）到硬件可执行格式（TPU 方言）的转换提供基础框架。

#### 1. 声明转换规则注册函数

提供函数接口用于声明三个核心注册函数，作为后续转换类与 MLIR 框架对接的 “入口”。

- `populateTopCfOpToTpuConversionPatterns`：注册控制流（Control Flow）算子的转换规则，确保条件分支、循环等控制逻辑能适配 `bm1684x` 硬件。
- `populateTopShapeToTpuConversionPatterns`：注册形状相关算子的转换规则（即 `SHAPE_LOWERING_BM1684X` 生成的类），处理张量形状调整逻辑。
- `populateTopToTpuConversionPatterns`：注册通用算子的转换规则（即 `LOWERING_BM1684X` 生成的类），覆盖大部分计算型算子的转换。

```java
// 控制流算子转换规则注册
void populateTopCfOpToTpuConversionPatterns(RewritePatternSet &patterns,
                                            TypeConverter &typeConverter,
                                            MLIRContext *ctx);

// 形状算子转换规则注册
void populateTopShapeToTpuConversionPatterns(RewritePatternSet *patterns);

// 通用算子转换规则注册
void populateTopToTpuConversionPatterns(RewritePatternSet *patterns);
```

#### 2. 定义硬件适配的算子转换类

通过宏定义批量生成针对 `bm1684x` 平台的算子转换类，覆盖形状处理和多数据类型转换场景，确保高层算子能适配硬件特性。

- `SHAPE_LOWERING_BM1684X` 宏：生成形状相关算子的转换类
  该宏定义了继承自 `TopShapeLowering` 的模板类（如 `AddTryLowering`、`ConcatTryLowering`），每个类包含 `Lowering` 方法，用于实现特定算子的形状转换逻辑（如张量维度调整、拼接 / 切片的形状计算等）。
  实例化的算子包括 `Add`、`Concat`、`Reshape`、`Slice` 等，均为需要处理形状变化的基础算子，确保转换后张量形状符合硬件计算要求。

```java
// 宏定义：生成形状转换类的模板
#define SHAPE_LOWERING_BM1684X(OP)                                             \
  struct OP##TryLowering : public TopShapeLowering<top::OP##Op> {              \
    OP##TryLowering(MLIRContext *ctx) : TopShapeLowering<top::OP##Op>(ctx) {}  \
    void Lowering(PatternRewriter &rewriter, top::OP##Op op) const override;   \
  };

// 实例化具体算子的形状转换类
SHAPE_LOWERING_BM1684X(Add)
SHAPE_LOWERING_BM1684X(Concat)
// ...（其他形状算子）
```

- `LOWERING_BM1684X` 宏：生成多数据类型适配的算子转换类
  该宏定义了继承自 `TopLowering` 的模板类（如 `ConvLowering`、`ReluLowering`），包含针对不同数据类型的转换方法：
  - 整数类型：`LoweringINT8`、`LoweringINT4`（支持对称 / 非对称量化）；
  - 浮点类型：`LoweringBF16`、`LoweringF16`、`LoweringF8`、`LoweringF32`；
  - 量化类型：`LoweringQuantized`。

  ```typescript
  ```

// 宏定义：生成支持多数据类型的算子转换类
// 功能：为每个算子创建一个继承自 TopLowering 的转换类，支持多种数据类型
// 模板参数：OP 为算子名称（如 Abs、Conv 等）
// 生成的类名格式：OP##Lowering（如 AbsLowering）
// 包含方法：针对不同数据类型的转换实现（需在 cpp 中实现）
//   - LoweringINT8/INT4: 整数类型转换（支持对称/非对称量化）
//   - LoweringBF16/F16/F8/F32: 浮点类型转换
//   - LoweringQuantized: 量化类型转换
#define LOWERING_BM1684X(OP)                                                   
struct OP##Lowering : public TopLowering[top::OP##Op](top::OP##Op) {                      
OP##Lowering(MLIRContext *ctx) : TopLowering[top::OP##Op](top::OP##Op)(ctx) {}          
void LoweringINT8(PatternRewriter &rewriter, top::OP##Op op,               
bool asymmetric) const override;                         
void LoweringINT4(PatternRewriter &rewriter, top::OP##Op op,               
bool asymmetric) const override;                         
void LoweringBF16(PatternRewriter &rewriter,                               
top::OP##Op op) const override;                          
void LoweringF16(PatternRewriter &rewriter,                                
top::OP##Op op) const override;                           
void LoweringF8(PatternRewriter &rewriter, top::OP##Op op) const override; 
void LoweringF32(PatternRewriter &rewriter,                                
top::OP##Op op) const override;                           
void LoweringQuantized(PatternRewriter &rewriter,                          
top::OP##Op op) const override;                     
};

// 实例化具体算子的多类型转换类
LOWERING_BM1684X(Abs)              // 绝对值运算
LOWERING_BM1684X(Add)              // 加法运算
LOWERING_BM1684X(Arccos)           // 反余弦运算
LOWERING_BM1684X(Arctanh)          // 反双曲正切运算
// ...（其他通用算子）

```

- 这些方法负责将高层算子（如卷积、激活函数、池化等）转换为`bm1684x`硬件支持的数据类型格式，适配硬件的计算精度（如低精度 INT4/INT8 加速、浮点 BF16/F16 优化等）。
实例化的算子覆盖范围极广，包括算术运算（`Add`、`Mul`）、神经网络核心算子（`Conv`、`MatMul`、`BatchNorm`）、激活函数（`Relu`、`GELU`）、注意力机制（`Attention`、`Rope`）等，基本涵盖深度学习模型的常见算子。

## /lib/Conversion/TopToTpu/LoweringBM1684X.cpp

该文件是`bm1684x`硬件平台算子转换的实现文件，与之前分析的头文件`LoweringBM1684X.h`配套，负责将头文件中声明的算子转换类与 MLIR 转换框架对接，最终实现从 “Top 方言” 到 “TPU 方言” 的算子转换。其核心功能是注册算子转换规则，并定义特定类型算子的转换逻辑，为模型在`bm1684x`平台的部署提供完整的转换链路。

整体遵循 “定义转换模式→整合转换规则→对接 MLIR 框架” 的流程：

1. 先定义`ShapeArithConvert`模板，为特定类型算子（形状算术类）提供转换逻辑；

```cpp
// 模板结构体：形状算术算子转换（如加减乘除等涉及形状计算的算子）
// 继承自OpRewriterPatternEx，用于定义MLIR的算子转换模式
template <typename OpTy>
struct ShapeArithConvert : public OpRewriterPatternEx<OpTy> {
public:
  // 构造函数：初始化父类，指定转换模式名称为"ShapeArithConvert"
  ShapeArithConvert(mlir::MLIRContext *context)
      : OpRewriterPatternEx<OpTy>(context, "ShapeArithConvert") {}

  // 核心方法：匹配并转换算子
  // 参数：op为待转换的top dialect算子，rewriter为MLIR的模式重写器
  LogicalResult matchAndRewriteImpl(OpTy op,
                                    PatternRewriter &rewriter) const override {
    Value out = op.getOutput();  // 获取算子的输出值
    if (isa<ReturnOp>(op))       // 若为返回操作，不转换，返回失败
      return failure();

    // 检查所有输入操作数是否由"形状生产者"生成（具有ShapeProducer特性）
    // 确保输入是形状相关的合法数据
    for (uint32_t idx = 0; idx < op.getNumOperands(); idx++) {
      Value opd = op.getOperand(idx);
      auto def_op = opd.getDefiningOp();  // 获取输入的定义算子
      if (!def_op || !def_op->hasTrait<trait::ShapeProducer>())
        return failure();  // 若输入不合法，转换失败
    }

    // 处理后续算子：若当前算子的用户是Device2HostOp（设备到主机的数据传输），则删除该D2H算子
    // 目的：形状计算无需设备到主机的传输，简化流程
    auto users = op->getUsers();  // 获取当前算子的所有用户（依赖该算子输出的算子）
    for (auto i = users.begin(); i != users.end(); ++i) {
      auto user = *i;
      if (!isa<tpu::Device2HostOp>(user)) {  // 过滤非Device2HostOp的用户
        continue;
      }
      auto next_d2sOp = dyn_cast<tpu::Device2HostOp>(user);
      // 将D2H算子的输出替换为其输入（跳过D2H操作）
      next_d2sOp.getOutput().replaceAllUsesWith(next_d2sOp.getInput());
      rewriter.eraseOp(next_d2sOp);  // 删除D2H算子
    }

    // 准备新算子的属性：将top算子的属性转换为tpu::ShapeArithOp的属性
    std::vector<NamedAttribute> attrs;
    std::string op_name = op.getOperationName().str();  // 获取原算子名称（如"top.AddOp"）
    int pos = op_name.find("top.");
    op_name = op_name.erase(pos, 4);  // 去除"top."前缀，保留算子核心名称（如"AddOp"）
    // 添加"type"属性，标识原算子类型
    attrs.emplace_back(
        rewriter.getNamedAttr("type", rewriter.getStringAttr(op_name)));
    // 复制原算子的所有属性
    for (auto &attr : op->getAttrs()) {
      attrs.push_back(attr);
    }

    // 替换原算子为tpu::ShapeArithOp（tpu方言的形状算术算子）
    rewriter.replaceOpWithNewOp<tpu::ShapeArithOp>(op, out.getType(),
                                                   op.getOperands(), attrs);
    return success();  // 转换成功
  }

  // 辅助方法：是否打印转换信息（此处返回false，不打印）
  bool shouldPrint(OpTy opTy) const override { return false; }
};
```

1. 通过三个注册函数，将头文件中声明的转换类（如 `ConvLowering`、`ShapeTryLowering`）和上述模板实例，统一添加到 MLIR 的 `RewritePatternSet`（转换规则集合）；

```cpp
// 控制流算子转换规则注册函数（实现头文件中的声明）
// 功能：将控制流算子（如If、Loop）的转换规则添加到转换集合
void populateTopCfOpToTpuConversionPatterns(RewritePatternSet &patterns,
                                            TypeConverter &typeConverter,
                                            MLIRContext *ctx) {
  // 注册IfOp和LoopOp的转换类（这些类在头文件中通过宏定义生成）
  patterns.insert<IfOpLowering, LoopOpLowering>(typeConverter, ctx);
}

// 形状相关算子转换规则注册函数（实现头文件中的声明）
// 功能：将形状操作算子的转换规则添加到转换集合
void populateTopShapeToTpuConversionPatterns(RewritePatternSet *patterns) {
  // 添加形状相关算子的转换类（如Shape、Concat、Slice等）
  patterns->add<
      // clang-format off
      ShapeTryLowering,        // Shape算子转换
      ConcatTryLowering,       // 拼接算子转换
      UnsqueezeTryLowering,    // 增加维度算子转换
      SqueezeTryLowering,      // 压缩维度算子转换
      SliceTryLowering,        // 切片算子转换
      //...其他算子
      // clang-format on
      >(patterns->getContext());  // 传入MLIR上下文

  // TODO：待添加更多形状算子（如GT、LT、GE、LE、MIN、MAX、SQRT等）
  // 添加算术类形状算子的转换（使用ShapeArithConvert模板）
  patterns->add<ShapeArithConvert<top::AddOp>,  // top.AddOp转换为tpu.ShapeArithOp
                ShapeArithConvert<top::SubOp>,  // top.SubOp转换
                ShapeArithConvert<top::MulOp>,  // top.MulOp转换
                ShapeArithConvert<top::DivOp>>( // top.DivOp转换
      patterns->getContext());
}

// 通用算子转换规则注册函数（实现头文件中的声明）
// 功能：将所有通用算子的转换规则添加到转换集合
void populateTopToTpuConversionPatterns(RewritePatternSet *patterns) {
  // 添加所有通用算子的转换类（覆盖数学运算、神经网络层、激活函数等）
  patterns->add<
      // clang-format off
      AbsLowering,                 // 绝对值算子转换
      AddLowering,                 // 加法算子转换
      ArccosLowering,              // 反余弦算子转换
      ArctanhLowering,             // 反双曲正切算子转换
      ArgLowering,                 // 参数算子（如ArgMax）转换
      AddConstLowering,            // 与常量加法算子转换
      AvgPoolLowering,             // 平均池化算子转换
      //...其他算子
      // clang-format on
      >(patterns->getContext());  // 传入MLIR上下文
}
```

1. 当 MLIR 执行方言转换时，自动匹配并应用这些规则，将 Top 方言算子批量转换为 `bm1684x` 可执行的 TPU 方言算子。

## /lib/Conversion/TopToTpu/TopToTpuPass.cpp

### 一、主函数逻辑

可以将整个流程整合为四大核心阶段：

#### 一、转换前准备（初始化与预处理）

核心功能：搭建基础环境，处理形状兼容性，配置量化参数，为后续转换做全面准备。

- 基础环境初始化：获取模型模块（`module_`）、MLIR 上下文（`ctx_`）和主函数（`mainFunc_`），配置量化分组、权重文件路径等基础参数。
- 计算量与形状预处理：统计模型 FLOPs；通过 `TryInsertTileBinaryPattern` 为高维张量插入 `Tile` 操作，解决维度超限（如超过 4 维）和广播不兼容问题，确保形状符合硬件基本要求。
- 量化与校准配置：初始化 Winograd 算法和量化表；根据模型是否已量化（`TOP_QUANTIZED` 状态）设置量化参数（对称 / 非对称），未量化模型执行校准流程获取量化范围。
- 关 Winograd 算法可以参考这个链接的介绍：[三种思路实现 Winograd 卷积, 配上代码的保姆级教程-CSDN 博客](https://blog.csdn.net/hsqyc/article/details/116136385)

#### 二、平台特定优化（硬件特性适配）

核心功能：针对不同 TPU 平台（如 BM1684X、MARS3 等）的硬件特性，优化算子参数和计算模式，最大化硬件性能。

- BM1684X/BM1688 优化：为 `MatMul` 算子设置逐通道量化属性（提升精度），若后续接浮点 `Add` 操作则输出 int16 类型（适配精度需求）。
- MARS3/SGTPUV8 优化：调整 `GELU` 激活函数的近似计算模式，适配硬件高效实现。
- 通用平台优化：为 W4A16 量化模式预处理 `MatMul`；针对 Transformer 模型在特定平台启用 KV 缓存优化（提升推理效率）。

#### 三、核心算子转换（Top→TPU 方言）

核心功能：将高层 Top 方言算子转换为目标平台的 TPU 方言算子，是转换流程的核心，这里调用的之前的 LoweringBM1684X（型号，这里可以替换）.cpp 的接口。

- 形状算子转换：通过平台专属函数（如 `bm1684x::populateTopShapeToTpuConversionPatterns`），将 `Reshape`/`Concat` 等形状操作转换为 TPU 方言，确保形状处理符合硬件逻辑。
- 控制流转换：针对 BM1684X/BM1690 系列，将 `IfOp`/`LoopOp` 等控制流算子转换为 TPU 支持的形式，保证分支、循环逻辑可执行。
- 通用计算算子转换：通过平台专属函数（如 `bm1684::populateTopToTpuConversionPatterns`），将 `Conv`/`Relu`/`MatMul` 等核心计算算子转换为 TPU 方言，适配硬件指令集。
- 类型调整与优化：调整 `Reshape`/`Tile` 等 TPU 算子的输出类型；在 BM1684X 平台融合 `Cast+Active` 算子，在 CV18xx 平台优化 `Cast` 输入，提升性能。

#### 四、后处理与验证（确保转换完整正确）

核心功能：优化转换后算子的类型一致性，验证转换完整性，标志转换完成。

- 类型调整与优化：调整 `Reshape`/`Tile` 等 TPU 算子的输出类型；在 BM1684X 平台融合 `Cast+Active` 算子，在 CV18xx 平台优化 `Cast` 输入，提升性能。
- 转换验证：遍历所有算子，检查是否存在未转换的 Top 方言算子（排除权重 / 输入等特殊算子），确保全部转换为 TPU 方言；更新模块类型并标记状态为 `TPU_LOWERED`，完成转换。

#### 五、源代码展示

```cpp
void ConvertTopToTpu::runOnOperation() {
  module_ = getOperation();  // 获取当前模块
  ctx_ = &getContext();      // 获取MLIR上下文
  mainFunc_ = module::getMainFuncOp(module_);  // 获取主函数

  LoweringConfig::isQuantized = false;  // 初始化量化标记
  // 设置量化分组信息
  module::setGroupQuantInfo(quantGroupSize, quantSymmetric);
  // 设置权重文件路径（若有）
  if (weightFileName != "") {
    module::setWeightFileName(weightFileName);
  }

  // 计算并标记模型的FLOPs（若未计算）
  int64_t flops = module::getFLOPs();
  if (flops == 0) {
    mainFunc_.walk([&](FlopsInterface op) { flops += op.getFLOPs(); });
    module::setFLOPs(flops);
  }

  // 应用Tile插入模式（处理二进制操作和MatMul的高维兼容）
  RewritePatternSet patterns(ctx_);
  patterns.clear();
  patterns.add<TryInsertTileBinaryPattern<top::SubOp>,
               TryInsertTileBinaryPattern<top::MaxOp>,
               TryInsertTileBinaryPattern<top::MinOp>,
               TryInsertTileBinaryPattern<top::CompareOp>,
               TryInsertTileMatMulPattern>(ctx_);
  // 非BM1684X系列平台额外处理Add和Mul操作
  if (!module::isBM1684XFamily()) {
    patterns.add<TryInsertTileBinaryPattern<top::AddOp>,
                 TryInsertTileBinaryPattern<top::MulOp>>(ctx_);
  }
  applyPatternsAndFoldGreedily(module_, std::move(patterns));

  // 初始化Winograd配置和量化表
  patterns.clear();
  LoweringConfig::doWinograd =
      doWinograd.hasValue() ? doWinograd.getValue() : false;
  init_qtable();

  // 处理量化配置（根据模型状态设置量化参数）
  if (module::isState(module::State::TOP_QUANTIZED)) {
    module::setAsymmetric(true);  // 已量化模型默认使用非对称量化
    LoweringConfig::isQuantized = true;
  } else {
    LoweringConfig::isQuantized = false;
    module::setAsymmetric(isAsymmetric);  // 设置非对称量化标记
    calibration_process();  // 执行校准流程
  }

  // 针对BM1684X/BM1688平台处理MatMul的量化配置
  if ((module::isBM1684X() || module::isBM1688()) &&
      !LoweringConfig::isQuantized &&
      (module::getMode() == module::Mode::INT8 ||
       module::getMode() == module::Mode::UINT8)) {
    // 设置MatMul的逐通道量化属性
    if (matmulPerchannel) {
      mainFunc_.walk([&](Operation *op) {
        if (isa<top::WeightOp, top::NoneOp, top::InputOp, ModuleOp, FuncOp,
                ReturnOp>(op)) {
          return;
        }
        if (isa<top::MatMulOp>(op)) {
          mlir::Attribute tmp = mlir::BoolAttr::get(op->getContext(), true);
          op->setAttr("matmulPerchannelQuant", tmp);
        }
      });
    }
    // 为MatMul设置输出int16属性（若后续接浮点Add操作）
    mainFunc_.walk([&](Operation *op) {
      auto users = op->getUsers();
      auto users_len = std::distance(users.begin(), users.end());
      if (isa<top::MatMulOp>(op) && users_len == 1) {
        for (auto user : users) {
          if (isa<top::AddOp>(user)) {
            auto name = module::getName(user).str();
            if (LoweringConfig::quantize_map.find(name) !=
                    LoweringConfig::quantize_map.end() &&
                (LoweringConfig::quantize_map[name] == module::Mode::F16 ||
                 LoweringConfig::quantize_map[name] == module::Mode::BF16)) {
              mlir::Attribute tmp = mlir::BoolAttr::get(op->getContext(), true);
              op->setAttr("output_int16", tmp);
              break;
            }
          }
        }
      }
    });
  }

  // 针对MARS3/SGTPUV8平台设置GELU近似模式
  if (module::isMARS3() || module::isSGTPUV8()) {
    mainFunc_.walk([&](Operation *op) {
      if (isa<top::WeightOp, top::NoneOp, top::InputOp, ModuleOp, FuncOp,
              ReturnOp>(op)) {
        return;
      }
      if (auto geluOp = dyn_cast<top::GELUOp>(op)) {
        if (geluOp.getApproxMode() == "normal")
          geluOp.setApproxMode(geluMode);  // 设置指定的近似模式
      }
    });
  }

  // 应用W4A16 MatMul准备模式（非TOP_QUANTIZED状态下）
  if (!module::isState(module::State::TOP_QUANTIZED)) {
    module::applyPatternOnce<W4A16MatMulPreparePattern>(module_);
  }

  // 处理KV缓存（针对特定平台和模式）
  if ((module::isBM1684XFamily() || module::isBM1690Family()) &&
      (module::getMode() == module::Mode::W8F16 ||
       module::getMode() == module::Mode::W4F16 ||
       module::getMode() == module::Mode::W8BF16 ||
       module::getMode() == module::Mode::W4BF16 ||
       module::getMode() == module::Mode::F16) &&
      module::isState(module::State::TOP_CALIBRATED)) {  // 需存在校准表
    kv_cache_process();
  }

  // 处理形状相关操作（按平台适配）
  if (module::isBM1684XFamily() || module::isBM1690Family()) {
    bm1684x::populateTopShapeToTpuConversionPatterns(&patterns);
  } else if (module::isBM1684Family()) {
    bm1684::populateTopShapeToTpuConversionPatterns(&patterns);
  }
  applyPatternsAndFoldGreedily(module_, std::move(patterns));

  // 处理设备到主机的数据转换
  device2host_process();
  patterns.clear();

  // 针对BM1684X/BM1690平台处理控制流操作（If/Loop）
  if (module::isBM1684XFamily() || module::isBM1690Family()) {
    ConversionTarget target(*ctx_);
    ScfTypeConverter typeConverter;
    // 设置合法方言和非法操作
    target.addLegalDialect<mlir::func::FuncDialect, top::TopDialect,
                           tpu::TpuDialect>();
    target.addIllegalOp<top::IfOp, top::LoopOp>();
    // 设置CallOp的动态合法性
    target.addDynamicallyLegalOp<mlir::func::CallOp>(
        [&](mlir::func::CallOp op) { return typeConverter.isLegal(op); });
    // 应用控制流转换模式
    bm1684x::populateTopCfOpToTpuConversionPatterns(patterns, typeConverter,
                                                    ctx_);
    if (failed(applyPartialConversion(module_, target, std::move(patterns))))
      signalPassFailure();  // 转换失败则标记错误
    patterns.clear();
  }

  // 处理主机到设备的数据转换
  host2device_convert_process();

  // 转换其他操作（按平台适配）
  if (module::isBM1684XFamily() || module::isBM1690Family()) {
    bm1684x::populateTopToTpuConversionPatterns(&patterns);
  } else if (module::isBM1684Family()) {
    bm1684::populateTopToTpuConversionPatterns(&patterns);
  } else if (module::isCV18xx()) {
    cv18xx::populateTopToTpuConversionPatterns(&patterns);
  } else {
    llvm_unreachable("Not Implemented");  // 未实现的平台
  }

  // 应用模式（每个模式仅应用一次）
  auto config = GreedyRewriteConfig();
  config.maxIterations = 1;
  applyPatternsAndFoldGreedily(module_, std::move(patterns), config);

  // 调整形状相关操作的类型（Reshape/Unsqueeze等）
  patterns.clear();
  patterns.add<
      ForwardTypePattern<tpu::ReshapeOp>, ForwardTypePattern<tpu::UnsqueezeOp>,
      ForwardTypePattern<tpu::SqueezeOp>, ForwardTypePattern<tpu::TileOp>,
      ForwardInt32TypePattern<tpu::SqueezeOp>,
      ForwardInt32TypePattern<tpu::SliceOp>,
      ForwardInt32TypePattern<tpu::PermuteOp>,
      ForwardInt32TypePattern<tpu::ShapeReduceOp>>(ctx_);
  applyPatternsAndFoldGreedily(module_, std::move(patterns));

  // 处理Cast操作
  cast_process();

  // 针对BM1684X平台应用Cast+Active优化
  if (module::isBM1684XFamily()) {
    patterns.clear();
    patterns.add<CastActivePattern>(ctx_);
    applyPatternsAndFoldGreedily(module_, std::move(patterns));
  }

  // 处理ReLU操作
  relu_process();

  // 针对CV18xx平台优化Cast输入
  if (module::isCV18xx()) {
    patterns.clear();
    patterns.add<CastInputCV18xxPattern>(ctx_);
    applyPatternsAndFoldGreedily(module_, std::move(patterns));
  }

  // 更新模块类型并标记状态为TPU_LOWERED
  module::updateModuleTypes();
  module::setState(module::State::TPU_LOWERED);

  // 检查是否仍有未转换的Top级操作（非权重/输入/返回等）
  bool hasTopOp = false;
  mainFunc_.walk([&](Operation *op) {
    if (isa<top::WeightOp, top::NoneOp, top::InputOp, ModuleOp, FuncOp,
            ReturnOp>(op)) {
      return;
    }
    if (!isa<tpu::TpuDialect>(op->getDialect())) {  // 非TPU方言的操作
      op->dump();
      hasTopOp = true;
    }
  });
  if (hasTopOp) {
    llvm_unreachable("unimplemented tpu dialect!");  // 存在未转换的操作，报错
  }
}
```

### 二、其他重要函数

`TryInsertTileBinaryPattern` 类是 MLIR 框架中用于解决二进制算子（如 Add、Sub、Mul 等）形状兼容性问题的核心转换模式，其核心逻辑是通过 “维度合并判断” 和 “Tile 操作插入”，确保算子输入形状适配硬件的维度限制（最大 4 维）和广播需求。整体功能逻辑可归纳为：

### 核心目标

硬件通常仅支持最大 4 维的张量运算，且二进制算子的两个输入可能需要广播（形状不同但兼容）。当输入张量维度超过 4 维，且无法通过合并维度适配 4 维限制时，该类会自动插入 `Tile` 操作（维度扩展），使输入形状符合硬件要求，保证算子能正常执行。

### 关键逻辑流程

#### 1. 过滤处理对象

仅针对双操作数的二进制算子（如 Add、Sub），排除返回操作（`ReturnOp`）。通过 `matchAndRewriteImpl` 的初始判断实现：

```cpp
if (isa<ReturnOp>(op) || opd_num != 2) return failure();
```

#### 2. 形状分析与判断

获取两个输入的形状（`shape1`、`shape2`），计算最大维度数（`shape_dim`），并通过两个关键判断决定是否需要处理：

- 是否需要广播（`needBroadcast`）：从最后一维向前比较，若存在 “维度值不同且均不为 1” 的情况（如 `(2,3)` 与 `(2,4)`），则需要广播。
- 是否能合并为 4 维（`canMergeTo4D`）：尝试合并连续维度（通过 `can_be_merged` 判断是否可合并，`merge_two_dims` 执行合并），若能将维度降到 4 维以内，则无需插入 Tile；否则需要处理。

#### 3. 插入 Tile 操作解决不兼容问题

当输入需要广播且无法合并为 4 维时，对超出 4 维的维度插入 `Tile` 操作（通过 `try_insert_tile` 实现）：

- 针对维度为 1 的输入（如 `shape1[i] = 1` 而 `shape2[i] = 5`），将其扩展为另一输入的维度大小（如扩展为 5），确保形状匹配。
- `Tile` 操作仅扩展指定轴，其他轴保持不变，避免不必要的计算开销。
