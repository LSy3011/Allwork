# AIACC 物理流水线验证框架 (V2.0) 使用指南

本指南旨在说明如何使用工业化升级后的 AIACC 验证框架。该框架采用了 **SSoT (Single Source of Truth)** 架构，确保编译器行为与硬件规格（YAML）绝对对齐。

---

## 1. 核心架构与 SSoT 来源

框架的所有物理决策均动态来源于 `hw_config/` 目录下的配置文件。以下是关键信息的获取路径：

| 物理属性 | 硬件配置文件路径 | 关键字段 / 逻辑 |
| :--- | :--- | :--- |
| **SIMD 通道数** | `hw_params.yaml` | 根据 `PARAM_SMC_VECTOR_WIDTH` / `Precision` 动态计算。 |
| **存储 Bank 深度** | `hw_params.yaml` | `PARAM_UR_BANK_DEPTH` (影响 DFG 地址生成与数据注入)。 |
| **算子流水线延迟** | `pipeline_delays.yaml` | 从 `pipeline_delays` 映射中动态获取各单元 Latency。 |
| **指令比特位宽** | `specs/units/*.yaml` | `code_bits` 决定了 LIR 打包时的指令长度。 |
| **指令字段偏移** | `specs/units/*.yaml` | `code_fields` 定义了 `VALID`, `SRC_PREC`, `SUB` 等字段的精确比特位置。 |
| **RtoVR 输入选择** | `rtovr_config.yaml` | `consumers_dr` 定义了 `SEL` 字段的索引值。 |
| **RtoVR 物理插槽** | `rtovr_config.yaml` | `producers_dvr` 定义了物理单元对应的 Crossbar 输出槽位 ID。 |

---

## 2. 快速开始

### 2.1 运行标准验证流程
使用 `main_verify.py` 可以一键完成从算子定义到物理仿真的全过程：

```bash
python3 verification_v2/main_verify.py --n_rows 128
```

### 2.2 运行冒烟测试（支持动态阶数）
验证框架对高阶算子的支持能力：

```bash
python3 verification_v2/scratch/final_smoke_test.py
```

---

## 3. 开发新算子

要增加一个新的验证算子，请遵循以下标准化步骤：

1.  **继承基类**：在 `verification_v2/ops/` 中继承 `BaseOperator`。
2.  **定义 DFG**：在 `get_dfg(self, spec)` 中描述逻辑图。**注意**：必须使用传入的 `spec` 来获取端口 ID，严禁硬编码。
3.  **实现 Golden**：在 `get_golden` 中实现数值预期。
4.  **数据注入**：在 `inject_data` 中将 Tensor 放入 Mock 存储。

---

## 4. 关键改进说明 (针对工业化需求)

### 4.1 动态仿真超时 (Iteration-Aware Timeout)
针对长指令链算子，框架会自动计算仿真时限：
*   **计算公式**：`Max_T = 静态指令最大拍数 + (II * 迭代次数) + 50 (排空宽放)`。
*   该值会自动注入到 `sim_config.json` 中，覆盖默认的 120 周期限制。

### 4.2 精度脱敏
`LIRPacker` 会根据 `vector_lanes` 自动调整 Hex 文件的对位逻辑。无论是 4 通道 FP32 还是未来可能的 8 通道 FP16，均无需修改驱动代码。

### 4.3 路由自动化
`PhysicalAssembler` 会自动完成逻辑依赖到物理端口的映射。如果您在硬件 YAML 中交换了 FPADD 和 FPMUL 的位置，验证框架在下次运行时会自动感知并生成正确的 `SEL` 码。

---

## 5. 调试建议

如果验证失败，请按以下顺序排查：
1.  **`compiler_schedule.json`**：检查 SMT Solver 产生的调度时间是否符合预期。
2.  **`v2_test_output.log`**：查看 `[AUDIT-DATA]` 标签，对比每个周期的物理端子数值。
3.  **`timeline_ascii.txt`**：检查物理占空比，确认指令是否在预期的 `2 + t` 周期发射。

---
*Last Updated: 2026-04-29*
*AIACC Verification Team*
