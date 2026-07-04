# backend API 流程说明

## 1. backend API 的定位

backend API 是 compiler 与 TPU 后端之间的桥。compiler 不直接调用 firmware 内部函数，也不直接填硬件寄存器，而是调用 backend API。

backend API 的核心职责是：

1. 接收 compiler 传来的算子参数。
2. 接收输入输出 tensor 描述。
3. 管理 command id / dependency id 上下文。
4. 调用 firmware_core 的算子 API。
5. 让 compiler 查询已经生成好的 TPU 指令。

它不是主要的算子实现层，而是“参数入口 + 指令导出接口”。

## 2. compiler 调用流程

一次典型 compiler 调用可以理解为：

```text
compiler 准备算子参数结构
  -> 调用 backend_api_xxx_global/local
  -> backend API 解析 void* 参数
  -> 设置 command id 上下文
  -> 调用 api_xxx_global/local
  -> firmware_core 生成 TPU 命令
  -> compiler 查询指令缓存
  -> compiler 写入模型或指令段
```

其中 `global/local` 表示两类不同执行场景：

- global：输入输出主要位于 global/system memory。
- local：输入输出已经在 local memory 或局部切片上下文中。

## 3. 参数解析流程

backend API 接口通常接收：

```text
void *param
int param_size
input tensor specs
output tensor specs
void *pid_node
```

内部会执行类似逻辑：

```text
检查 param_size
  -> 将 param memcpy 到具体 spec 结构
  -> 设置 id node 上下文
  -> 调用 firmware_core API
```

这使得 compiler 和 backend 之间可以通过稳定的 C ABI 交互，而不暴露 C++ 对象或复杂内部类型。

## 4. command id / parallel id 的作用

TPU 指令生成不是简单顺序追加。不同 engine 之间存在依赖，例如：

- GDMA 搬运完成后 TIU/BDC 才能计算。
- TIU 计算完成后 GDMA 才能写回。
- 多核或多 engine 并行时需要维护不同 id node。

backend API 会在进入算子前设置 id node，退出时取回更新后的 id node。这样 compiler 可以持续维护依赖状态。

可以理解为：

```text
pid_node 是 compiler 与后端之间共享的指令依赖上下文
```

## 5. 指令缓存查询流程

算子 API 执行后，后端内部已经生成了不同 engine 的命令。compiler 通过查询接口取回：

```text
backend_api_get_tpu_inst_size(engine)
backend_api_get_tpu_inst_data(engine)
backend_api_get_tpu_inst_group_number()
backend_api_get_tpu_inst_number_per_group(engine)
backend_api_get_tpu_inst_size_per_group(engine)
backend_api_get_total_id(engine)
backend_api_clear_tpu_inst_data()
```

engine 名通常类似：

```text
gdma:0
tiu:0
cdma:0
hau:0
```

这说明后端指令是按 engine 和 group 管理的，不只是一个无结构 byte stream。

## 6. backend API 与 firmware_core 的关系

backend API 不自己实现 conv、matmul、softmax 等复杂逻辑。它会转调 firmware_core 中的 API，例如：

```text
backend_api_conv_global
  -> api_conv_global

backend_api_layer_norm_local
  -> api_layer_norm_local

backend_api_softmax_global
  -> api_softmax_global
```

因此算子功能正确性主要由 firmware_core 和底层命令生成保证；backend API 主要保证参数、上下文和导出接口正确。

## 7. 标准算子的接入模式

标准算子通常遵循下面的模式：

```text
定义算子 spec
  -> 实现 api_xxx_global/local
  -> backend API 暴露 backend_api_xxx_global/local
  -> compiler 调用 backend_api_xxx
```

这让新增算子比较机械：只要 firmware_core 已有 `api_xxx`，backend API 层往往只是增加一条导出声明/宏注册。

## 8. 特殊算子和 legacy 参数

部分算子有历史参数结构或特殊入口，例如 3D conv、deconv、requant、grid sample、yolo 等。这些参数结构可能不完全来自新的 layer API spec，而是通过 backend API 自己维护的参数定义承接。

这类接口通常用于：

- 兼容历史 compiler。
- 支持特殊数据结构。
- 承接尚未完全迁移到统一 layer API 的算子。

## 9. backend API 的调试重点

如果 compiler 调用 backend 后指令不对，优先检查：

1. compiler 传入的 param_size 是否与 spec 匹配。
2. 参数结构字段是否与后端定义一致。
3. 输入输出 tensor spec 是否正确。
4. id node 是否在并行段前后正确传入和取回。
5. 指令缓存是否在每个 op 前后正确 clear/query。
6. 生成的是哪个 engine 的指令，是否取错 engine 名。

## 10. backend API 的职责边界

backend API 应该保持轻量。它应该做：

- C ABI 入口。
- 参数解析。
- 上下文 guard。
- 调用 firmware_core。
- 暴露指令缓存。

它不应该做：

- 复杂 tiling 策略。
- 芯片寄存器字段计算。
- 大量算子逻辑。
- runtime 设备管理。

这些应分别属于 firmware_core、firmware_base 或 runtime。
