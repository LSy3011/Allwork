# TPU 后端总体流程与模块职责

## 1. 后端解决的问题

这套 TPU 后端代码解决的是“把上层编译器或运行时提交的算子请求，转化为 TPU 可以执行的命令或 firmware 行为”。它既服务编译器 codegen，也服务 cmodel 仿真和设备端 firmware。

核心目标可以分成三件事：

1. 接收上层算子参数、张量描述和执行上下文。
2. 选择对应算子实现，完成数据切分、访存规划、计算指令生成和同步安排。
3. 根据运行场景，把结果变成指令缓存、cmodel 行为或设备 firmware 可执行命令。

## 2. 总体工作流

```text
上层请求
  -> 参数/张量描述解析
  -> 算子 API 分发
  -> 算子实现规划数据流和计算流
  -> TPU kernel 抽象接口表达硬件动作
  -> 芯片相关底层生成具体命令
  -> 指令缓存 / cmodel 仿真 / 设备执行
```

这里有两条最重要的入口路径：

### 编译器后端路径

```text
compiler codegen
  -> backend_api_xxx_global/local
  -> api_xxx_global/local
  -> firmware_core 算子实现
  -> tpu_kernel 接口
  -> 芯片底层命令生成
  -> backend_api_get_tpu_inst_data 取回指令
```

这条路径的目的不是直接运行模型，而是让编译器拿到 TPU 指令二进制，写进最终模型或指令段。

### runtime / firmware 路径

```text
host runtime / bmlib / tpuDNN
  -> API ID + 参数 buffer
  -> firmware api_route
  -> sg_api_xxx
  -> nodechip_xxx / api_xxx
  -> tpu_kernel 接口
  -> cmodel 仿真或设备执行
```

这条路径的目的更接近运行时执行：host 侧把 API 请求发给 firmware，firmware 根据 API ID 分发到具体算子。

## 3. 各部分的作用

### compiler / codegen

compiler 负责把高层 IR 或 layer 描述降到后端可理解的参数结构。它调用后端动态库中的 C 接口，让后端根据参数生成 TPU 指令。

compiler 不需要知道每个寄存器字段如何填写，只需要知道：

- 调哪个 backend API。
- 参数结构如何填。
- 如何从后端取回指令缓存。

### backend API

backend API 是 compiler 与 TPU 后端之间的适配层。它负责：

- 接收 `void *param`、参数长度、输入输出 tensor spec。
- 把参数反序列化成具体算子 spec。
- 设置 command id / parallel id 上下文。
- 调用 firmware_core 中真正的算子 API。
- 提供指令缓存查询接口，让 compiler 取回 GDMA/TIU/CDMA/HAU 等 engine 的命令。

它的本质是“编译器可调用的封装层”。

### firmware_core

firmware_core 是算子逻辑层。它决定一个算子如何在 TPU 上执行，包括：

- global/local 两类执行场景。
- 输入输出 tensor 如何切分。
- 何时做 DMA 搬运。
- 何时发 BDC/TIU 计算指令。
- 多核、多 engine 之间如何同步。
- 需要哪些临时 buffer。

同一套 firmware_core 代码会被复用到多种产物中：compiler backend、cmodel firmware、设备 firmware。

### TPU kernel 抽象层

TPU kernel 抽象层给 firmware_core 提供统一硬件能力。算子不直接拼寄存器，而是调用抽象接口表达意图，例如：

- 查询 NPU 数、local memory、EU 数。
- 计算 stride、local memory 地址和数据类型大小。
- 发起 GDMA 搬运。
- 发起 BDC/TIU 计算。
- 进行 core/engine 同步。
- 输出 log 和 assert。

这一层的价值是屏蔽不同芯片底层差异。

### 芯片相关底层实现

芯片相关底层负责把抽象硬件动作转成具体芯片命令。它处理：

- 寄存器字段布局。
- atomic 指令生成。
- GDMA/SDMA/CDMA/BD/HAU 等 engine 的命令格式。
- base address、同步 ID、profile、timer、cache 等底层状态。

这一层是最接近硬件指令集和寄存器的部分。

### cmodel

cmodel 在 host 上模拟 TPU 行为。它用于：

- 在没有真实设备时验证算子功能。
- 对比结果正确性。
- 复现部分并行流水问题。
- 辅助 debug 指令生成和同步问题。

### device firmware

device firmware 是最终跑在设备 MCU 上的固件。它负责实际设备上的初始化、API 请求处理、底层命令执行和设备侧调度。

## 4. 三种执行语义

同一套后端代码在不同场景下有不同语义：

### 生成指令

compiler 调 backend API，后端只需要生成并缓存命令，最后让 compiler 取走。这是 codegen 视角。

### 模拟执行

cmodel firmware 在 host 上模拟硬件执行，适合功能验证。这是仿真视角。

### 设备执行

设备 firmware 在 MCU 上接收 runtime 请求并操作真实硬件。这是部署视角。

## 5. 关键边界

1. compiler 不直接关心寄存器细节。
2. backend API 不实现复杂算子逻辑，只做参数解析、上下文管理和转发。
3. firmware_core 负责算子算法和调度，但应尽量通过 TPU kernel 抽象访问硬件。
4. 芯片相关底层负责真实命令格式，不应反向依赖上层算子逻辑。
5. cmodel 和 device firmware 共享大量接口，是为了保证仿真行为尽量贴近设备行为。

## 6. 最重要的理解模型

可以把整个后端理解为一个逐层降级过程：

```text
算子语义
  -> 参数结构
  -> 算子 API
  -> 数据搬运/计算/同步计划
  -> TPU kernel 调用
  -> atomic 命令
  -> engine 指令流
```

这个模型比记目录更重要。只要抓住这条链，就能理解新增算子、调试错误和迁移芯片时各自该改哪里。
