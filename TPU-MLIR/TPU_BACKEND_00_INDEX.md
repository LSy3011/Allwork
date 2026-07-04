# TPU 后端流程索引


## 文档列表

1. `TPU_BACKEND_01_ARCHITECTURE.md`
   - TPU 后端总体工作流
   - compiler、backend API、firmware、cmodel、device firmware 的角色
   - 主要执行路径之间的关系

2. `TPU_BACKEND_02_BUILD_AND_PRODUCTS.md`
   - 构建模式与产物之间的关系
   - 为什么同一套核心代码会生成 backend 库、cmodel firmware 和设备 firmware
   - 常用构建流程的目的

3. `TPU_BACKEND_03_BACKEND_API.md`
   - compiler 如何调用后端生成 TPU 指令
   - 参数解析、command id 管理、指令缓存查询流程
   - backend API 在整条链路中的职责边界

4. `TPU_BACKEND_04_FIRMWARE_AND_ATOMIC.md`
   - firmware 如何接收 API 消息并分发到算子
   - 算子如何通过 TPU kernel 抽象生成硬件命令
   - atomic/gen_cmd 在指令落地中的作用

5. `TPU_BACKEND_05_TEST_AND_DEBUG.md`
   - cmodel、tpuDNN、PLD/EDA、实机验证的用途差异
   - 常见 debug 路径
   - 结果错误、指令错误、同步错误分别怎么定位

## 一句话主线

```text
compiler 或 runtime 提交算子请求
  -> 后端解析参数与张量描述
  -> firmware_core 选择算子实现和切分策略
  -> tpu_kernel 抽象接口表达搬运/计算/同步
  -> 芯片相关底层实现生成具体硬件命令
  -> cmodel 仿真、指令导出或设备 firmware 执行
```
