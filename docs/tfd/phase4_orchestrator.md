# Phase 4: 编排层 (M12-M15)

> 预计 3-4 天。可用 Mock-APB 与 Phase 3 并行开发。

## M12: orchestrator/footprint.py — 足迹掩码
**关键类 FootprintMask**：
- `record(cycle, iq_type, bank_id)`
- `record_port(cycle, port_id)` [ADR-001]
- `has_rr_conflict(cycle, bank_id) -> bool`
- `has_ww_conflict(cycle, bank_id) -> bool`
- `has_port_conflict(cycle, port_id) -> bool` [ADR-001]
- `merge(other: FootprintMask)`
**验收**：`pytest tests/unit/test_footprint.py -v`
**通过**：R-R/W-W/Port 冲突判定 100% 正确

## M13: orchestrator/resource.py — 全局资源表
**关键类 AdvancedResTable**：
- `can_place(apb, offset) -> bool`
- `commit(apb, offset)`
**验收**：`pytest tests/unit/test_resource_table.py::test_mock_apb_placement -v`

## M14: orchestrator/stitcher.py — 相位缝合
**关键函数**：
- `stitch(apb_a, apb_b, res_table) -> (offset_b, dly_gap)`
- `inject_dly(lir_stream, position, count)`
- `resolve_rr_conflict(ops) -> List[MIROp]`
- `resolve_port_conflict(apb_list)` [ADR-001]
- `resolve_rw_raw(read_op, write_op) -> int`
**验收**：
```bash
pytest tests/unit/test_stitcher.py::test_phase_shift_resolves_rr -v
pytest tests/unit/test_stitcher.py::test_dly_injected_correctly -v
pytest tests/unit/test_stitcher.py::test_port_collision_triggers_shift -v
pytest tests/unit/test_stitcher.py::test_rw_raw_nop_count -v
```

### ADR-001 专属测试
```python
def test_port_collision_triggers_shift():
    # APB_A/B 不同 Bank 但同 Port → 必须 Phase Shift
    assert offset >= 1
```

## M15: UR 冲突消解集成
**验收**：`pytest tests/unit/test_stitcher.py::test_stress_all_banks_same -v`
**通过**：全 Bank 0 → 自动串行化 → 无冲突

---
*门禁：M15 全绿才进 Phase 5*
