# Phase 5: 空间与驱动层 (M16-M19)

> 预计 2-3 天。

## M16: layout/liveness.py — 生命周期分析
**函数**：`analyze(mir_stream) -> Dict[tensor_id, Interval]`
**验收**：`pytest tests/unit/test_liveness.py::test_interval_correctness -v`
**通过**：3 个张量的区间值精确一致

## M17: layout/allocator.py — Bank 着色
**函数**：
- `allocate(intervals, hw_spec) -> Dict[tensor_id, bank_id]`
- `spill_to_main_memory(tensor_id)`
**验收**：
```bash
pytest tests/unit/test_allocator.py::test_8bank_coloring_no_conflict -v
pytest tests/unit/test_allocator.py::test_spill_triggered_on_overflow -v
```
**通过**：8 张量各分不同 Bank；第 9 个触发 Spill

## M18: driver/compressor.py — 循环压缩
**函数**：`compress_loops()`, `fold_addresses()`
**验收**：
```bash
pytest tests/unit/test_driver.py::test_loop_count_within_bits -v
pytest tests/unit/test_driver.py::test_loop_split_on_overflow -v
pytest tests/unit/test_driver.py::test_inc_folding_consecutive -v
```
**通过**：256次=单层; 257次=两层嵌套; 连续地址=INC 激活

## M19: driver/emitter.py — 二进制打包
**函数**：`emit(lir_stream, hw_spec) -> bytes`, `pack_instruction()`
**验收**：`pytest tests/unit/test_driver.py::test_41bit_emit_matches_reference -v`
**通过**：与仿真器参考 bit-perfect

---
*门禁：M19 全绿才进 Phase 6*
