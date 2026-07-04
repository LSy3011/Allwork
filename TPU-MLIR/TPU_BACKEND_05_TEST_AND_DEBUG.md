# 测试验证与调试流程

## 1. 为什么需要多套测试路径

TPU 后端同时服务指令生成、host 仿真、设备 firmware 和 PLD/EDA，因此测试也分成多个层级。

不同测试路径关注点不同：

```text
backend API 测试：指令是否生成正确
tpuDNN/cmodel 测试：算子结果是否正确
layer 测试：单算子逻辑是否正确
PLD/EDA 测试：硬件时序、指令字段、engine 行为是否正确
实机测试：runtime、firmware、硬件集成是否正确
```

## 2. cmodel 验证流程

cmodel 是最常用的开发验证路径。它在 host 上模拟 TPU 行为，适合快速验证算子功能。

典型流程：

```text
初始化芯片环境
  -> 构建 cmodel firmware 或测试
  -> 运行 tpuDNN/layer/cmodel 测试
  -> 对比输出结果
  -> 根据日志定位算子或指令问题
```

适合定位：

- 参数解析错误。
- shape/stride 错误。
- tiling 错误。
- local buffer 规划错误。
- 基础同步错误。

## 3. tpuDNN 测试流程

`tpuDNN` 是更接近真实上层使用方式的测试入口。它适合验证算子 API、runtime 和 cmodel/设备之间的集成。

### cmodel 模式

```text
使用 cmodel firmware
  -> 在 host 上运行 tpuDNN tests
  -> 验证功能正确性
```

### PCIe 模式

```text
关闭 cmodel
  -> 构建设备侧可运行测试
  -> 拷贝到目标机器
  -> 通过真实 runtime 和设备执行
```

### SoC 模式

```text
使用 SoC 交叉编译
  -> 链接 SoC 版本 bmlib/runtime
  -> 在目标 SoC 环境运行
```

## 4. layer 测试流程

layer 测试更贴近单算子实现。适合在算子开发阶段使用。

关注点：

- 单个 global/local 算子是否正确。
- buffer size 是否合理。
- tpu_kernel 调用顺序是否正确。
- cmodel 与预期结果是否一致。

当 tpuDNN 测试失败时，可以把问题缩小到 layer 测试中复现。

## 5. PLD/EDA 验证流程

PLD/EDA 面向更接近硬件的验证场景。它关注：

- 指令字段是否符合硬件定义。
- engine 是否能正确执行。
- 多 engine 并行和同步是否正确。
- 性能相关行为是否符合预期。
- firmware 是否能在仿真平台正常运行。

适合定位 cmodel 难以暴露的问题，比如真实 engine 时序、寄存器字段兼容性和多核同步问题。

## 6. tv_gen 流程

tv_gen 用于生成测试向量和 dump 数据。它常用于硬件验证团队或指令级验证。

流程可以理解为：

```text
打开 tv_gen 构建选项
  -> 编译生成 tv_gen 程序
  -> 运行指定 case
  -> 输出输入数据、命令、期望输出或差分数据
  -> 交给仿真/硬件验证环境使用
```

常用能力：

- dump API message。
- dump mask 或差分数据。
- 控制是否 dump GDDR/CDDR。
- 按 step dump local memory。
- dump GDMA log。

## 7. 指令错误的定位流程

如果怀疑 compiler 生成的指令不对，可以按下面路径定位：

```text
backend_api_xxx_global/local
  -> 参数结构是否正确
  -> tensor spec 是否正确
  -> command id node 是否正确
  -> firmware_core 是否调用了预期 tpu_kernel 接口
  -> atomic/gen_cmd 是否填了正确字段
  -> dump 指令并用 reg parser 解析
  -> 对照指令文档
```

重点检查：

- dtype。
- shape/stride。
- global/local 地址。
- command id / dependency id。
- engine 类型是否正确。
- group 边界是否正确。

## 8. 结果错误的定位流程

如果输出结果错，建议按这个顺序：

```text
先用 cmodel 复现
  -> 固定随机输入
  -> 关闭多线程降低不确定性
  -> 打开 DEBUG 和 firmware log
  -> 检查参数解析
  -> 检查 tiling 和 local buffer
  -> dump 中间结果
  -> 对比 reference
```

常见原因：

- padding/stride/dilation 处理错误。
- quant/requant 参数错误。
- local memory overlap。
- dtype cast 或 round mode 不一致。
- global/local 路径实现不一致。

## 9. 同步错误的定位流程

同步问题通常表现为偶现错误、不同 core 数结果不同、cmodel 多线程才失败、设备失败但单线程 cmodel 正常。

建议流程：

```text
关闭多线程验证是否消失
  -> 检查 parallel_start/end
  -> 检查 send_msg/wait_msg 配对
  -> 检查 command id node 传递
  -> 检查 core_idx/core_num 设置
  -> 检查 tpu_sync_all / barrier
  -> 在 PLD/EDA 或实机上验证
```

## 10. cmodel 对但设备错

这类问题通常不在纯算子数学逻辑，而在设备相关行为：

- cache flush/invalidate。
- DDR/local memory 地址映射。
- firmware_top 初始化。
- 中断或 message fifo。
- 多核同步。
- 编译选项不一致。
- cmodel 未覆盖的硬件限制。

定位时应先确认：

```text
同一 CHIP_ARCH
同一源码版本
同一 EXTRA_CONFIG 关键选项
同一 firmware_core 产物来源
同一输入数据和模型
```

## 11. debug 开关的用途

常用开关：

```text
DEBUG=ON                    # debug 构建，减少内联，便于定位
USING_MULTI_THREAD_ENGINE=OFF # cmodel 单线程，降低不确定性
USING_FW_PRINT=ON           # 打开 firmware 打印
USING_FW_DEBUG=ON           # 打开更详细 firmware debug
DUMP_CACHED_CMD=ON          # dump cached command
LOG_TO_FILE=ON              # 日志写文件
```

这些开关不是越多越好。建议先单线程 + DEBUG 复现，再逐步打开 firmware debug 和 command dump。

## 12. 推荐排障顺序

```text
1. 明确失败发生在 codegen、cmodel、PLD/EDA 还是实机。
2. 如果是结果错，先用 cmodel 固定输入复现。
3. 如果是指令错，先 dump backend 指令并解析。
4. 如果是偶现，先关闭多线程和多核缩小范围。
5. 如果 cmodel 对设备错，重点查 cache、地址、同步和构建选项。
6. 最后再进入芯片 atomic/gen_cmd 或 firmware_top 层排查。
```
