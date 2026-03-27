# Phase 6: 算子库集成与 E2E (M20-M25)

> 预计 5-7 天。最终 Boss：sin + atan2 Bit-Perfect。

## M20: library/atomic/template.py — 原子算子
**关键方法**：`define_pulses() -> List[MIROp]`
**首个算子**：sin (Horner: 4 FPMUL + 2 FPADD)
**验收**：`pytest tests/unit/test_library.py::test_sin_atomic_pulses_valid -v`

## M21: library/composite/template.py — 组合算子
**关键方法**：`stitch_apbs() -> List[APB]`
**首个算子**：atan2 (sin + cos + div + atan 缝合)
**验收**：`pytest tests/unit/test_library.py::test_atan2_composite_no_conflict -v`

## M22: core/pipeline.py — PassManager
**关键类**：`PassManager.register_pass()`, `.run()`
**验收**：`pytest tests/integration/test_compiler_flow.py::test_sin_pipeline_e2e -v`
**通过**：sin 全链路跑通，每 Pass 产出 Snapshot

## M23: core/audit.py — 审计快照
**验收**：`pytest tests/integration/test_compiler_flow.py::test_audit_dumps_exist -v`
**通过**：build/audit/ 生成 04_smt_synthesis.mir 等

## M24: core/checker.py — 不变式守卫
**函数**：`check_post_synthesis()`, `check_post_stitcher()`
**验收**：`pytest tests/integration/test_invariant_guard.py -v`
**通过**：违规 APB → `PhysViolationError`

## M25: E2E 位精确验证 ★
**验收**：
```bash
pytest tests/integration/test_e2e_bit_perfect.py::test_sin_bit_perfect -v
pytest tests/integration/test_e2e_bit_perfect.py::test_atan2_bit_perfect -v
```
**通过**：LIR → 仿真器 → 与 PyTorch 误差 = 0

---
*M25 通过 = V5.0 发布条件*
