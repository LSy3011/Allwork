# AIACC fmod(a, b) 算子前端拆分分析

`fmod(a, b) = a - b * trunc(a / b)`

在 AIACC 编译器前端 (GraphCompiler) 中，该算子会被拆分为以下底层 DFG 节点序列：

## 1. 硬件单元使用情况
| 阶段 | 逻辑算子 | 对应硬件单元 | 预计逻辑拍数 (Logic Cycles) |
| :--- | :--- | :--- | :--- |
| **除法近似** | `1/b` (7阶泰勒) | `FPMUL`, `FPADD` | 约 16 拍 |
| **商计算** | `q = a * (1/b)` | `FPMUL` | 1 拍 |
| **截断取整** | `trunc(q)` | `FPTOINT`, `INTTOFP` | 2 拍 |
| **逻辑补偿** | `floor` (含负数) | `FPADD` (CMP), `LOGIC` (SEL) | 2 拍 |
| **乘加合并** | `a - b * q_int` | `FPMUL`, `FPADD` | 2 拍 |
| **访存** | `UR_READ`, `UR_WRITE` | `UR` 端口 | 首尾各 1 拍 |

## 2. 节点规模估算 (全展开版)
以 `fmod(a, b)` 为例，采用 7 阶 Taylor 展开：
1.  **输入加载**: 2 (a, b)
2.  **Taylor 1/b**: 
    - `b-1`: 1 (FPADD)
    - `1-dx*r` 循环 (x7): 7 * (1 FPMUL + 1 FPADD) = 14
3.  **计算 q = a/b**: 1 (FPMUL)
4.  **trunc/floor 逻辑**:
    - `FPTOINT` + `INTTOFP` (转换): 2
    - `FPADD` (比较) + `RTOVR` (搬运 ST): 2
    - `FPADD` (减1) + `LOGIC` (选择): 2
5.  **最终计算**: 
    - `b * q_int`: 1 (FPMUL)
    - `a - Result`: 1 (FPADD)
6.  **输出写回**: 1 (UR_WRITE)
7.  **物理 RTOVR 节点 (关键)**: 
    - 每个算子平均消耗 2.5 个 RTOVR (Input_S0, Input_S1, Output_D/ST)。
    - **总 ALU 节点数**: ~25 
    - **总 RTOVR 节点数**: ~25 * 2.5 约 60
    - **总 DFG 节点规模**: **约 80 - 90 个节点**。

## 3. 为什么 26 个节点的诊断版仍然 UNSAT？
在测试 `a - floor(a)` 时（约 26 节点），出现 UNSAT 的核心嫌疑点不在于“计算量太大”（`atan` 75 节点已过），而在于 **数据流冲突**：
*   **LOGIC 单元瓶颈**: `floor` 算子引入了 `LOGIC` 单元，它需要同时消费 `FPADD.ST` (状态位) 和 `FPADD.D` (数据位)。
*   **RTOVR 槽位锁死**: 现有的 SMT 约束 `SynthesisAxioms.add_rtovr_crossbar_axiom` 可能在处理 `ST` 端口与其后继 `LOGIC` 的 T+1 规约时，与主数据流的 Liveness (逻辑存活期) 产生了硬性时间窗重叠，导致解空间在该物理连线下为空。

## 4. 优化方向
- **策略 A**: 简化 `floor` 实现。如果 `fmod` 的输入域已知为正，可用纯 `FPTOINT -> INTTOFP` 代替完整的 `floor`（减少 `LOGIC` 和 `ST` 依赖）。
- **策略 B**: 修复 SMT 公理。放宽 `ST` 端口搬运的 Liveness 约束，确保它不被后续 `FPADD` 指令过早覆盖。
