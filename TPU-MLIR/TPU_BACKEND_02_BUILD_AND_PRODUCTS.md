# 构建流程与产物关系

## 1. 为什么构建模式复杂

这套 TPU 后端不是只生成一个库。它需要服务三类场景：

1. 编译器后端：需要一个可被 compiler 调用的动态库，用于生成 TPU 指令。
2. host 仿真：需要 cmodel firmware，在 host 上模拟设备行为。
3. 设备运行：需要真正运行在设备 MCU 或 PLD/EDA 环境中的 firmware。

因此，同一批核心代码会在不同 CMake 选项下生成不同产物。

## 2. 环境初始化的作用

使用前通常先执行：

```sh
source scripts/envsetup.sh [CHIP_ARCH]
```

它的作用不是简单设置路径，而是建立当前构建上下文：

- 选择目标芯片。
- 设置工程根路径。
- 指定 backend 库、cmodel firmware、libsophon、工具链等默认位置。
- 加载一组构建函数，例如 `rebuild_firmware`、`rebuild_test`、`rebuild_backend_lib_cmodel`。

所以后续命令大多依赖这个 shell 环境。

## 3. 构建决策主线

顶层构建可以理解为：

```text
选择 CHIP_ARCH
  -> 载入该芯片的 base 配置
  -> 决定 BASE_LIBS
  -> 编译 firmware_core
  -> 根据构建模式选择 backend_api / cmodel / tpuDNN / tests / firmware_top
```

其中 `BASE_LIBS` 是关键变量。它表示“当前芯片底层支持库”。

- cmodel 模式下，通常包含 cmodel 和 firmware_base。
- 设备模式下，通常只包含 firmware_base。

## 4. 主要产物一：编译器后端库

产物：

```text
libbackend_${CHIP_ARCH}.so
```

用途：给 compiler/codegen 调用，用于生成 TPU 指令。

它包含：

```text
backend API 封装
+ firmware_core 算子逻辑
+ 芯片底层支持库
```

典型构建意图：

```text
USING_CMODEL=ON
USING_BACKEND_API=ON
USING_MULTI_THREAD_ENGINE=OFF
```

为什么关闭多线程：compiler 生成指令时更关心确定性命令生成，不需要模拟完整异步硬件行为。

## 5. 主要产物二：cmodel firmware

产物：

```text
libcmodel_firmware.so
```

用途：host 侧运行时或测试程序加载它，用来模拟 firmware/设备行为。

它包含：

```text
firmware_core
+ cmodel 实现
+ 芯片底层支持库
```

典型构建意图：

```text
USING_CMODEL=ON
USING_BACKEND_API=OFF
USING_MULTI_THREAD_ENGINE=ON
```

为什么打开多线程：为了更接近硬件多 engine 异步执行行为，可以暴露一部分同步和流水问题。

## 6. 主要产物三：设备 firmware

产物可能是：

```text
libfirmware_core.so
libfirmware_core.a
firmware 二进制
```

用途：运行在真实设备 MCU、PLD 或 EDA 环境。

它包含：

```text
firmware_core
+ 芯片底层 firmware_base
+ firmware_top 启动和平台代码
```

典型构建意图：

```text
USING_CMODEL=OFF
USING_FW_SIMULATION=OFF 或 ON
USING_INT_MSGFIFO=ON
```

如果是 PLD/EDA，还会打开：

```text
USING_PLD_TEST
USING_EDA
USING_FAKE_DDR_MODE
USING_FW_DEBUG
```

## 7. 主要产物四：runtime/bmlib/tpuDNN 测试产物

这类产物用于验证和运行测试。它们的核心作用是把 host 侧测试程序和 firmware/cmodel/设备连接起来。

- cmodel 测试：使用 cmodel 版本 runtime 和 `libcmodel_firmware.so`。
- PCIe 测试：关闭 cmodel，测试程序在设备环境中调用真实 runtime。
- SoC 测试：使用 SoC 交叉编译和对应平台库。

## 8. 常见构建流程的意义

### 构建 compiler 后端

```sh
rebuild_backend_lib_cmodel
```

目标：生成可供 compiler 调用的 backend 动态库。适合 codegen 调试。

### 构建 cmodel firmware

```sh
rebuild_firmware_cmodel
```

目标：生成 host 仿真的 firmware 库。适合功能验证。

### 构建设备 firmware

```sh
rebuild_firmware
```

目标：生成设备侧 firmware 相关产物。适合实机或系统集成。

### 构建 PLD/EDA firmware

```sh
rebuild_firmware_for_palladium
rebuild_firmware_for_eda
```

目标：生成适合硬件仿真环境的 firmware。适合指令级和系统级验证。

### 构建测试

```sh
rebuild_test
rebuild_test layer
rebuild_test pld
rebuild_test cmodel
```

目标：分别验证 tpuDNN、layer、PLD 和芯片 cmodel 层面的行为。

## 9. EXTRA_CONFIG 的定位

`EXTRA_CONFIG` 用于临时改变构建行为，不改脚本本身。常用场景：

```sh
export EXTRA_CONFIG="-DDEBUG=ON"
export EXTRA_CONFIG="-DDEBUG=ON -DUSING_MULTI_THREAD_ENGINE=OFF"
export EXTRA_CONFIG="-DUSING_FW_PRINT=ON -DUSING_FW_DEBUG=ON"
export EXTRA_CONFIG="-DMAX_TPU_CORE_NUM=2"
```

这些配置常用于：

- 增强调试信息。
- 降低并行度，提升复现稳定性。
- 打开 firmware 内部打印。
- 限制 cmodel 模拟 core 数。

## 10. 构建流程的本质

构建系统的本质是用不同选项回答三个问题：

1. 目标是生成指令、仿真执行，还是设备执行？
2. 使用哪个芯片的底层实现？
3. 是否需要测试、debug、profile 或 PLD/EDA 特殊能力？

对应关系如下：

```text
生成指令 -> backend_api + firmware_core + BASE_LIBS
仿真执行 -> cmodel firmware + firmware_core + cmodel + BASE_LIBS
设备执行 -> firmware_core + firmware_base + firmware_top
测试验证 -> tpuDNN/test + runtime + 对应 firmware/cmodel/backend
```
