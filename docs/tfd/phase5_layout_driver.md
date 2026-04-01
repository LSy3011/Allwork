# Phase 5: 物理布局与规格驱动驱动程序 (Layout & Spec-Driven Driver)

本阶段负责将逻辑张量分配到物理 Bank，并将逻辑 DFG 转换为符合硬件 ISA (Instruction Set Architecture) 的物理比特流。同时引入“无偏见点火”架构以实现软硬解耦。

## 支柱架构：驱动层 (M19) 的“三位一体”验证审计 [ADR-006]

驱动层不再承担“逻辑推断”职责，而是作为一个**规格描述符解析器**。其验证体系由以下三项核心支柱构成：

### 支柱 5: 语义一致性审计 (Semantic Alignment)
* **核心意图**：验证算子库中定义的 `metadata` 意图是否精准投射到硬件比特。
* **通过准则**：`metadata` 中的每一个硬件 Key（如 `SUB=1`），其对应的位偏移值必须与 `HardwareSpec` 保持比特级同步。

### 支柱 6: 规格边界限定 (Schema Boundary)
* **核心意图**：确保驱动层不打包任何 YAML 规范之外的“幽灵比特”，具备比特溢出防御能力。

### 支柱 7: 编解码闭环验证 (Codec Loopback Parity)
* **核心意图**：验证驱动生成的二进制流能够被仿真器解码器无损还原。
* **验收工具**：`pytest tests/unit/test_driver_sim_parity.py`

---

## 模块详细清单与验收标准

### M16: layout/liveness.py — 生命周期分析
**职责**：分析 MIR 算子流中张量定义的活跃区间，为 Bank 复用提供依据。
**验证函数**：`analyze(mir_stream) -> Dict[tensor_id, Interval]`
**验收指令**：`pytest tests/unit/test_liveness.py::test_interval_correctness -v`
**通过准则**：3 个跨算子张量的活跃区间起始/结束值与理论调度点精确一致。

### M17: layout/allocator.py — Bank 着色与冲突消解
**职责**：在 UR 读写端口限制下，为张量分配物理 Bank，处理 Bank 冲突。
**验证函数**：`allocate(intervals, hw_spec) -> Dict[tensor_id, bank_id]`
**验收指令**：
- `pytest tests/unit/test_allocator.py::test_8bank_coloring_no_conflict -v`
- `pytest tests/unit/test_allocator.py::test_spill_triggered_on_overflow -v`
**通过准则**：8 个并发访问张量分配到互不冲突的 Bank；超出容量时触发溢出（Spill）异常。

### M18: driver/compressor.py — 循环压缩与指令合并
**职责**：利用硬件循环机制压缩指令流，执行地址 INC 字段折叠。
**验证函数**：`compress_loops()`, `fold_addresses()`
**验收指令**：
- `pytest tests/unit/test_driver_compressor.py::test_loop_count_within_bits -v`
- `pytest tests/unit/test_driver_compressor.py::test_loop_split_on_overflow -v`
**通过准则**：单层循环超过位宽限制时（如 >255）自动拆分为多层嵌套。

### M19: driver/emitter.py — 规格驱动打包机 (LIRPacker)
**职责**：作为机械打包的核心，根据物理 YAML 全量映射 metadata 到比特。
**核心原则 (ADR-006)**：
1. **Spec 反射**：系统动态加载 `unit.yaml`（真相源）。
2. **比特流拼接**：按物理规格声明顺序执行位移累加，禁止基于 OpName 的隐式推断。
**验收指令**：`pytest tests/unit/test_driver_emitter.py::test_instruction_emit_matches_sim_decoder -v`

---
*门禁：M19 全线通过 Pillar 7 闭环测试后，方可进入系统集成 Phase 6。*
