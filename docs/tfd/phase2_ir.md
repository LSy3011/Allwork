# Phase 2: 三层 IR 定义 (M03-M06)

> 预计 2-3 天。目标：HIR→MIR→LIR 完整数据结构，可序列化。

## M03: ir/base.py — 公共基类
```python
class IRNode:
    node_id: str      # UUID
    trace_id: str     # 溯源 ID，贯穿全层
    metadata: dict
```
**验收**：`pytest tests/unit/test_ir.py::test_trace_id_propagation -v`
**通过**：HIR→LIR 后 trace_id 一致

## M04: ir/hir.py — 算子图
**关键类**：`HIRProgram`, `HIRNode(op_id, shape, dtype, parents, children)`
**验收**：
```bash
pytest tests/unit/test_hir.py::test_dag_construction -v
pytest tests/unit/test_hir.py::test_shape_inference -v
```

## M05: ir/mir/ — 脉冲调度层
**关键类**：`MIROp(logic_phase, iq_type, bank_src, bank_dst, is_rigid)`
**验收**：`pytest tests/unit/test_mir.py::test_footprint_overlap_detection -v`
**通过**：同拍同Bank两读 → `has_rr_conflict()` == True

## M06: ir/lir/ — 物理二进制层
**关键类**：`LIROp(abs_cycle, vld, loops, dly, inc, encoded_value)`
**验收**：`pytest tests/unit/test_lir.py::test_41bit_encode_decode -v`
**通过**：编码→解码 bit-perfect

---
*门禁：M06 全绿才进 Phase 3*
