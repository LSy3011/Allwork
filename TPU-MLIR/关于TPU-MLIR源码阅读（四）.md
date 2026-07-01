# 关于TPU\-MLIR源码阅读（四）

## 1\.主要QAT目录

- `/home/fhj/tpu-mlir-1.19/python/tools/qat/` \- QAT量化主目录

- `/home/fhj/tpu-mlir-1.19/python/tools/qat/sophgo_mq/` \- Sophgo MQ（可能是Sophgo Model Quantization）实现

### 1\.核心量化实现

在 `/home/fhj/tpu-mlir-1.19/python/tools/qat/sophgo_mq/sophgo_mq/` 目录中包含：

#### **量化核心模块**：

- `advanced_ptq.py` \- 高级PTQ（Post\-Training Quantization）量化

- `convert_deploy.py` \- 转换和部署相关功能

- `prepare_by_platform.py` \- 平台准备功能

- `observer.py` \- 观察者模式实现

- `scheme.py` \- 量化方案定义

- `fuser_method_mappings.py` \- 融合方法映射

- `fusion_method.py` \- 融合方法实现

#### **伪量化实现**（fake\_quantize目录）：

- 多种量化方法实现，如adaround、dsq、lsq、gptq等

- 不同精度量化，如FP4、FP8、FP16等

#### **自定义量化器**（custom\_quantizer目录）：

- `academic_quantizer.py` \- 学术量化器

- `sophgo_tpu_quantizer.py` \- Sophgo TPU专用量化器

- `model_quantizer.py` \- 模型量化器

#### **部署相关**（deploy目录）：

- `convert_xir.py` \- XIR模型转换

- `deploy_sophgo.py` \- Sophgo平台部署

- 其他平台部署文件

#### **训练相关文件**：

- `qat_train_all_model.py` \- QAT训练主文件

- `ptq_train_all_model.py` \- PTQ训练主文件

#### 基础概念和数据结构（首先阅读）

1. `scheme.py`

- 定义量化方案（QuantizeScheme）数据结构

- 包含量化的基本参数如对称性、通道维度、位宽等

2. `observer.py`

- 实现各种观察器（Observer）类

- 用于收集激活值和权重的统计信息

- 包括MinMaxObserver、EMAMinMaxObserver等

3. `fake_quantize/` 目录

- 实现各种伪量化算法

- 包括LearnableFakeQuantize、E4M3FakeQuantize、DSQFakeQuantize等

#### 模型准备阶段（其次阅读）

1. `prepare_by_platform.py`

- 核心函数：`prepare_by_platform()`

- 根据平台和量化策略准备模型

- 插入伪量化节点到模型中

#### `custom_quantizer/` 目录

- 实现针对不同平台的自定义量化器

- 如sophgo\_tpu\_quantizer\.py针对Sophgo TPU平台

#### `fuser_method_mappings.py` 和 `fusion_method.py`

- 模型融合相关方法

- 将可融合的层合并以提高效率

2. 模型转换和部署（然后阅读）

#### `convert_deploy.py`

- 模型转换和部署功能

- 将训练好的量化模型转换为部署格式

#### `deploy/` 目录

- 不同平台的部署实现

- 如deploy\_sophgo\.py针对Sophgo平台

3. 训练和校准流程（接着阅读）

#### `utils/state.py`

- 管理模型的不同状态（校准、量化等）

- 控制伪量化节点的行为

#### `qat_train_all_model.py` 和 `ptq_train_all_model.py`

- QAT和PTQ训练的入口脚本

- 展示如何使用整个量化框架

4. 高级功能（最后阅读）

#### `advanced_ptq.py`

- 高级PTQ算法实现

- 如AdaRound、BRECQ等

#### `mix_precision/` 目录

- 混合精度量化实现

#### `nn/` 目录

- 自定义神经网络模块

## 推荐的阅读顺序

1. **基础概念**：

    - `scheme.py` \- 了解量化方案定义

    - `observer.py` \- 了解观察器机制

    - `fake_quantize/__init__.py` 和几个典型实现如 `learnable.py`

2. **模型准备**：

    - `prepare_by_platform.py` \- 核心准备函数

    - `custom_quantizer/sophgo_tpu_quantizer.py` \- 平台特定量化器

3. **训练流程**：

    - `utils/state.py` \- 状态管理

    - `qat_train_all_model.py` \- QAT训练入口

4. **模型部署**：

    - `convert_deploy.py` \- 模型转换

    - `deploy/deploy_sophgo.py` \- 平台特定部署

5. **高级特性**：

    - `advanced_ptq.py` \- 高级算法

    - 其他特定功能模块

这种顺序将帮助您从基础概念开始，逐步深入理解整个量化框架的工作原理和实现细节。

## 1\.scheme\.py

这个文件定义了一个 `QuantizeScheme` 类，主要用于**描述和配置神经网络量化（Quantization）的方案参数**。在深度学习中，量化是将高精度浮点数模型转换为低精度整数模型的技术，用于减少模型大小和加速推理，这个类正是为了统一管理量化过程中的各种配置选项。

### 核心功能解析

1. **量化方案的核心参数配置**

    - 通过 `__init__` 方法初始化量化的关键参数：

        - `symmetry`：是否使用对称对称量化 \*\*（对称量化的阈值范围关于 0 对称，非对称称为非对称量化）\*\*

        - `per_channel`：是否使用**按通道量化**（对张量的每个通道单独计算量化参数，更精确但计算量更大）

        - `pot_scale`：量化缩放因子（scale）是否为**2 的幂**（便于硬件加速）

        - `bit`：量化位宽（如 8bit，决定了量化后的精度）

        - 其他扩展参数（通过 `**kwargs` 接收）

2\.\*\* 映射 PyTorch 量化方案 \*\*\- 根据初始化参数，自动映射到 PyTorch 官方的量化方案（`torch_qscheme`）：

- 对称 \+ 按通道 → `torch.per_channel_symmetric`

- 非对称 \+ 按通道 → `torch.per_channel_affine`

- 对称 \+ 按张量 → `torch.per_tensor_symmetric`

- 非对称 \+ 按张量 → `torch.per_tensor_affine`这使得该类可以直接与 PyTorch 的量化 API 对接。

3\.\*\* 生成观测器（Observer）参数 \*\*\- `to_observer_params` 方法生成量化观测器所需的完整参数，包括：

- `quant_min`/`quant_max`：量化后的整数范围（根据位宽和对称性计算）

- `dtype`：量化后的数据类型（对称量化用 `torch.qint8`，非对称用 `torch.quint8`）

- `qscheme`：PyTorch 量化方案（前面映射的结果）

- `ch_axis`：按通道量化时的通道轴（默认为 0）这些参数可直接用于 PyTorch 量化观测器（如 `MinMaxObserver`）的初始化，用于统计张量的分布并计算量化参数（scale 和 zero\_point）。

4\.\*\* 方案信息展示 \*\*\- `str` 方法格式化输出量化方案的关键参数，便于调试和日志记录。

## 2\.observer\.py

这段代码实现了一系列 PyTorch 量化过程中使用的观察者（Observer）类，用于计算张量的量化参数（缩放因子 scale 和零点 zero\_point）。这些观察者类针对不同的量化场景和需求，实现了多种量化参数计算策略。

主要功能分析如下：

1. **基础类 ObserverBase**：

    - 继承自 PyTorch 的\_observerBase，提供了通用的初始化和基础方法

    - 支持张量级（per\-tensor）和通道级（per\-channel）量化

    - 支持幂等缩放因子（pot\_scale）

    - 实现了量化范围（quant\_min, quant\_max）的计算逻辑

2. **具体观察者实现**：

    - **MinMaxObserver**：最基础的观察者，记录整个校准数据集的全局最小值和最大值来计算量化参数

    - **MinMaxFloorObserver**：在 MinMax 基础上，增加了基于最小损失的量化参数优化，支持不同量化类型（输入、参数等）

    - **EMAMinMaxObserver**：使用指数移动平均（EMA）来更新最小值和最大值，适合处理批次数据的动态变化

    - **PoTModeObserver**：基于幂等缩放因子（Power\-of\-Two）的观察者，记录最频繁出现的缩放因子模式

    - **EMAQuantileObserver**：结合 EMA 和分位数（Quantile）方法，使用直方图统计来确定剪辑阈值

    - **ClipStdObserver**：基于均值和标准差的观察者，通过设定标准差倍数来剪辑数据范围

    - **LSQObserver 和 LSQPlusObserver**：实现了 LSQ（Learned Step Size Quantization）相关的量化参数计算方法基于张量的统计特性（均值、标准差等）来确定缩放因子

    - **MSEObserver 和 EMAMSEObserver**：通过最小化均方误差（MSE）来寻找最优的量化范围EMAMSEObserver 增加了 EMA 机制来平滑批次间的变化

    - **KLDObserver**：基于 KL 散度（Kullback\-Leibler Divergence）的观察者封装了 PyTorch 原生的 HistogramObserver 实现

3. **核心功能**：

    - 记录输入张量的统计信息（最小值、最大值、均值、标准差等）

    - 根据不同策略计算最优量化参数（scale 和 zero\_point）

    - 支持不同量化模式（对称 / 非对称、张量级 / 通道级）

    - 提供了多种优化策略（MSE 最小化、EMA 平滑、KL 散度等）

这些观察者类在模型量化校准阶段发挥作用，通过分析校准数据来确定最佳的量化参数，从而在保证模型精度的同时，最大化量化带来的性能提升和存储节省。

## 3\.adaround\_quantizer\.py

这段代码实现了基于 AdaRound 算法的伪量化（Fake Quantization）功能，主要用于神经网络量化过程中的权重量化优化。下面从功能和原理两方面进行分析：

### 功能分析

该代码定义了`AdaRoundFakeQuantize`类，继承自`QuantizeBase`，主要核心功能是：

1. 实现了 AdaRound（Adaptive Rounding）量化算法，这是一种基于学习的量化舍入策略

2. 支持张量级（per\-tensor）和通道级（per\-channel）量化

3. 能够在训练过程中动态调整量化参数，平衡量化精度损失

4. 提供伪量化功能，即在训练时模拟量化效果但保持浮点计算，便于反向传播优化

### 核心原理

1. **伪量化基础**：伪量化是量化感知训练（QAT）的关键技术，通过在训练过程中模拟量化 / 反量化操作，让模型适应量化带来的精度损失。基本公式：`量化值 = clamp(round(x/scale + zero_point), quant_min, quant_max)反量化值 = (量化值 - zero_point) * scale`

2. **AdaRound 算法**：传统量化使用四舍五入（Round\-to\-Nearest）策略，而 AdaRound 通过学习得到最优舍入策略：

    - 引入可学习参数`alpha`，通过 sigmoid 函数生成舍入掩码

    - 公式：`舍入值 = floor(x/scale) + rectified_sigmoid(alpha)`

    - 其中`rectified_sigmoid`是修正的 sigmoid 函数，确保输出在 \[0,1\] 范围内

3. **核心计算过程**：

    - `_rectified_sigmoid`：生成 0 到 1 之间的舍入权重

    - `adaround_forward`：实现基于 AdaRound 的伪量化前向过程

    - `init_alpha`：初始化可学习参数`alpha`，使初始舍入行为接近传统四舍五入

    - `forward`：根据模式选择传统伪量化或 AdaRound 量化

4. **学习过程**：通过反向传播优化`alpha`参数，使量化后的权重能最好地保留模型性能。在推理阶段，会使用硬决策（`hard_value=True`）进行舍入，即直接取 0 或 1。



