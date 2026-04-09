# AIACC ISA V5 多级嵌套循环与地址寻址规约 (Loop & AGU Spec)

> **版本**：V1.0 (Draft)  
> **状态**：已在 `IQUnit.py` 仿真内核中验证  
> **适用场景**：大算子大矩阵运算、张量分片 (Tiling)、零开销指令折叠

---

## 1. 硬件架构概述

AIACC ISA V5 引入了 **“硬件控制流下沉”** 机制。每个指令队列 (IQ) 单元内部集成了多级循环控制器，允许编译器通过指令头（Header）的控制位，在不增加指令存储占位的条件下，实现复杂的张量寻址。

### 核心特性
*   **8 层嵌套支持**：硬件维护 Level 0 至 Level 7 共 8 组独立的计数寄存器。
*   **零开销跳转**：循环回跳由硬件 IQ 单元在指令发射周期内自动完成，不产生 Pipeline Bubble。
*   **全维度增量 (AGU)**：支持每一层循环独立的地址增量（Stride），实现多维张量映射。

---

## 2. 指令头 (Header) 控制字段

| 字段 | 位宽 | 语义描述 |
| :--- | :--- | :--- |
| **`LOOPS`** | 10 bits | 当前循环层级的最大迭代次数 (N-1)。0 代表执行 1 次。 |
| **`EMBED`** | 6 bits | **循环定义/层级标签**。标记该指令所属的最内层循环级别。 |
| **`EMBED_END`** | 6 bits | **退出掩码 (Exit Mask)**。每一位代表结束一个层级。`bit[i]=1` 表示结束 Level i+1。 |
| **`INC`** | 4 bits | **Level 0 地址增量**。带符号数，表示每轮脉冲循环的基址偏移。 |
| **`INC_EMBED`** | 6 bits | **Level 1 地址增量**。带符号数，表示当 Level 1 回跳时叠加的地址偏移。 |

---

## 3. 地址生成算法 (AGU Contract)

对于带有 `ADDR` 属性的指令（如 `UR_READ`, `UR_WRITE`），硬件在执行时会执行实时地址合成：

$$Addr_{eff} = Addr_{base} + \sum_{i=0}^{7} (Count_i \times Increment_i)$$

*   **$Count_i$**：对应层级当前的迭代计数器状态。
*   **$Increment_i$**：对应层级定义的步长。
*   **物理意义**：该公式允许在一条指令中描述一个 $N \times M \times ...$ 的超空间遍历。

---

## 4. 嵌套循环示例分析 (Loop Nesting Examples)

### 4.1 典型嵌套结构
通过 `EMBED` 的递进定义与 `EMBED_END` 的掩码退出（基于用户提供的硬件逻辑）。

```yaml
# 示例：多层嵌套（3、2、0 与 1、0 结构）
Inst 0: EMBED=3               # 定义并启动 Level 3 循环
Inst 1: EMBED=2               # 定义并启动 Level 2 循环
Inst 2: EMBED=0, LOOPS=255    # 定义 Level 0 (Pulse) 为 256 次，处于 Level 3, 2 内部
Inst 3: EMBED=1               # 定义并启动 Level 1 循环
Inst 4: EMBED=0               # 执行体 (处于 Level 3, 2, 1, 0 嵌套内)
Inst 5: EMBED=0, EMBED_END=1  # 第 1 层循环结束（Mask 0x1）
Inst 6: EMBED=1               # 逻辑继续
Inst 7: EMBED=0, EMBED_END=7  # 掩码 111 (0x7)，所有循环层级 (1, 2, 3) 结束
```

### 4.2 张量 Add 算子 ($1024 \times 1024$)
实现大矩阵计算的指令折叠：

```yaml
# Kernel: C[i] = A[i] + B[i]
# 假设处理 4096 个元素，Tile 128 (需循环 8 次处理全部)

Inst 0: EMBED=1, LOOPS=7, INC_EMBED=32   # 外循环：Level 1 步长为 32 个 128-bit 块
Inst 1: UR_READ Addr=A,  EMBED=0, INC=1, LOOPS=31 # 读 A，单次 Tile 内循环 32 次
Inst 2: UR_READ Addr=B,  EMBED=0, INC=1, LOOPS=31 # 读 B
Inst 3: FPADD  S0, S1,   EMBED=0, LOOPS=31        # 计算 (逻辑层不涉及地址)
Inst 4: UR_WRITE Addr=C, EMBED=0, INC=1, LOOPS=31, EMBED_END=1 # 结束 Level 1 内容
```

---

## 5. 约束与已知限制
*   **跨 IQ 同步**：硬件不保证不同 IQ 之间的 Loop 强对齐。编译器必须在 SMT 合成阶段通过 `global_t` 确保所有相关 IQ 的循环周期同步。
*   **寄存器重用**：在 Loop 内部，物理寄存器（DR）的生命周期会被覆盖，必须确保计算结果在下一轮 Loop 的数据到达前被消费。
