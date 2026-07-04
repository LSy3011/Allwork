# Firmware、算子实现与 atomic 命令生成流程

## 1. firmware 侧做什么

firmware 侧负责把上层 API 请求变成硬件可执行动作。它既可以在 cmodel 中模拟，也可以在设备 MCU 上运行。

它处理的问题包括：

- API 请求分发。
- 算子参数解释。
- 数据搬运、计算、同步的执行计划。
- 将抽象硬件动作翻译成具体芯片命令。
- profile、timer、cache command、message sync 等运行时能力。

## 2. API 消息分发流程

runtime 路径下，上层通常提交的是：

```text
api_id + 参数 buffer + byte_size
```

firmware 的分发流程是：

```text
api_route(api_id, api_buf, byte_size)
  -> 根据 api_id 匹配 CASE_API
  -> 调用 sg_api_xxx(api_buf, byte_size)
  -> 解析具体参数
  -> 执行 nodechip_xxx 或 api_xxx
  -> 调用 tpu_kernel 接口生成/执行命令
```

这条路径依赖 API ID 契约。API ID 定义了 host/runtime 与 firmware 对同一个算子的共同理解。

## 3. firmware_core 的作用

firmware_core 是算子和控制逻辑的主体。它负责把“算子语义”降到“硬件动作序列”。

典型职责：

- 判断 global/local 场景。
- 规划 tensor 切分和循环。
- 申请和复用 local buffer。
- 安排 GDMA 搬运。
- 安排 BDC/TIU 计算。
- 处理 multi-core、multi-engine 同步。
- 调用 profile/timer/debug 接口。

它尽量通过 TPU kernel 抽象访问硬件，而不是直接写某个芯片的寄存器字段。

## 4. TPU kernel 抽象层的作用

TPU kernel 层把硬件能力抽象成统一函数。算子实现面对的是：

```text
搬运：tpu_gdma_*
计算：tpu_bdc_*
同步：tpu_sync_* / send_msg / wait_msg
资源：tpu_npu_num / tpu_eu_num / local mem 查询
工具：stride、地址、dtype、cast、range 检查
```

这样做的好处是：

1. 算子代码不需要知道每颗芯片的寄存器格式。
2. 多芯片复用同一套算子逻辑成为可能。
3. 芯片差异集中在底层实现中。

## 5. atomic/gen_cmd 的作用

atomic/gen_cmd 是最接近硬件指令的层。它把 TPU kernel 的抽象调用变成具体命令字段。

可以理解为：

```text
算子说：我要搬一块 tensor
  -> tpu_kernel 说：这是一次 GDMA copy
  -> atomic/gen_cmd 说：这些寄存器字段应该这样填
  -> 命令进入 engine 队列或指令缓存
```

atomic 层通常负责：

- 填写 GDMA/SDMA/CDMA/BD/HAU 等 engine 的描述符。
- 处理地址、stride、shape、dtype、压缩、转置等字段。
- 分配 command id 和 dependency id。
- 把命令写入缓存、ring buffer 或设备队列。

## 6. cmodel 和设备执行的差异

同一套算子逻辑在 cmodel 和设备上含义不同：

### cmodel

```text
生成命令 + 模拟硬件行为 + 校验结果
```

cmodel 更适合：

- 快速功能验证。
- 单步 debug。
- 对比不同实现的结果。
- 复现部分并行/同步问题。

### 设备 firmware

```text
生成命令 + 操作真实硬件 engine + 与 MCU/runtime 协同
```

设备 firmware 更关注：

- 初始化。
- 中断/消息队列。
- cache/DDR/本地内存真实行为。
- 多核、多 engine、真实时序。

## 7. fullnet 与动态执行

firmware 中还存在 fullnet 和 dynamic fullnet 相关流程。可以粗略理解为：

- 普通 API route：一次请求对应一个算子或一类操作。
- fullnet：更偏向整网或静态网络运行，可能直接配置一批命令和寄存器。
- dynamic fullnet：支持动态加载、动态编译或运行时变化的网络执行。

这些流程仍然复用 firmware_core、tpu_kernel 和底层命令生成能力。

## 8. 新增算子的 firmware 流程

新增一个算子时，流程通常是：

```text
定义参数/spec
  -> 实现 global/local 算子 API
  -> 在算子中规划搬运、计算和同步
  -> 调用 tpu_kernel 抽象接口
  -> 如抽象接口不足，补充 tpu_kernel 能力
  -> 在芯片底层实现新接口或补充 atomic/gen_cmd
  -> 接入 backend API 或 runtime API route
  -> 添加 cmodel/tpuDNN/layer/PLD 测试
```

如果只是 compiler 后端需要生成指令，不一定需要新增 runtime API ID；如果 runtime/bmlib 也需要通过 api_id 调用，则需要补充消息分发。

## 9. 调试 firmware/atomic 的关键问题

### 算子逻辑错

优先看：

```text
参数解析 -> tensor shape/stride -> tiling -> local buffer -> tpu_kernel 调用顺序
```

### 指令字段错

优先看：

```text
tpu_kernel 调用参数 -> atomic/gen_cmd 字段填充 -> 指令 dump -> 指令文档对照
```

### 同步错

优先看：

```text
command id -> send/wait msg -> engine 并行状态 -> core barrier -> multi-core context
```

### cmodel 对、设备错

优先看：

```text
构建选项差异 -> cache/DDR/local memory 行为 -> 多线程/多核同步 -> firmware_top 初始化
```

## 10. 这部分的核心理解

firmware/atomic 不是一个单点模块，而是一条逐步降级链：

```text
API 请求
  -> 算子语义
  -> 搬运/计算/同步计划
  -> TPU kernel 抽象调用
  -> atomic 命令字段
  -> engine 执行
```

阅读或迁移时，应优先确认自己当前改的是哪一层，而不是先陷入具体文件名。
