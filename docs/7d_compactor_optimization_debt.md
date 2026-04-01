# AIACC 7D 压缩器技术债与重构规范

## 1. 当前状态 (Current Status)
目前的 `Compactor` 已经通过 `_pass1_linear_folding` 实现了稳定的线性折叠。其核心改进包括：
- **NOP + LOOPS 扩展支持**：支持在 `VLD=0` 时通过 `LOOPS` 字段代表多个时钟周期的物理空转（气泡），大幅减小了线性指令流的 HEX 体积。
- **元数据对齐**：修复了 `LIRPacker` 丢失 `inc`, `embed` 等循环元数据的严重 Bug。

## 2. 遗留问题：为什么暂时禁用了 7D 探测？
### 2.1 位域逻辑值冲刷 (Bit-Bleeding)
`Compactor._detect_strides` 在尝试寻找 2D-7D 嵌套循环时，采用的是对物理 `payload` 进行直接加减运算。这种方式在面对高度紧致的位域（Bitfields）时存在致命缺陷：
- 例如：`payload` = `[BANK: 3 bits | ADDR: 8 bits | VLD: 1 bit]`。
- 如果 ADDR 字段在 Stride 计算中发生进位（如 `0xFF + 1`），会导致高位的 `BANK` 被修改，甚至导致 `VLD` 位被误写。
- 这正是导致 8 阶 Sin 算子在开启高阶压缩后出现 `0.07` 以上数值误差的原因（指令被损毁了）。

### 2.2 循环特征误判
由于 128-bit 指令打包后其整体数值具有很大的随机性，直接对整数做 Stride 探测可能在数学上碰巧命中步进规律，但在物理语义上这可能是不共存的两个算子操作。

## 3. 重构路径 (Planned Refactoring)
为了支持 MatMul、Conv2D 等天然矩阵算子的高阶折叠，我们需要将 `Compactor` 升级为 **“字段感知型探测器 (Field-Aware Detector)”**。

### 3.1 字段解耦验证 (Decoupled Verification)
在探测 Stride 时，必须将 128-bit 负载拆解为原始字段：
1. **静态字段校验**：循环体内的 `Opcode`, `Bank`, `PREC`, `VLD` 必须在每一拍都完全一致。
2. **唯一动态字段**：仅允许 `ADDR` 字段在不同循环层级间存在 Stride 偏移。

### 3.2 掩码保护计算 (Masked Calculation)
计算 Stride 时必须基于各个字段在硬件 spec 中的 Offset 和 Width。
```python
# 拟议的伪代码逻辑
stride = (p[i+s].addr - p[i].addr) 
# 且满足 p[i+s].opcode == p[i].opcode ...
```

## 4. 结论与风险提示
- 在完成上述 “字段感知” 重构之前，**禁止在生产环境中启用 7D 探测**，否则会导致指令损毁。
- 目前的 1D 线性折叠足以支撑 Sin、Exp、Atan2 等非矩阵型算子的 Bit-Perfect 验证。
- 建议优先处理矩阵算子生成时的 Tile 拆分，届时再回归修复此模块。
