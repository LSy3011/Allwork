# 函数级 TDD 规格书 (TFD 专属)

> 每增加一个硬件特性，TFD 在此追加对应的测试函数签名。

## Phase 1: 硬件契约层
- M01-F05: `parse_connectivity(path) -> ConnectivityGraph`
- M01-F06: `validate_rtovr_mutex(spec) -> None`

## Phase 3: 合成引擎
- M08-F01: `add_raw_constraints(solver, ops, spec)`
- M08-F02: `add_rr_conflict_axiom(solver, ops, spec)`
- M08-F03: `add_ww_conflict_axiom(solver, ops, spec)`
- M08-F04: `add_bus_width_axiom(solver, ops, spec)`
- M08-F05: `add_rtovr_connectivity_axiom(solver, ops, T, B, P, spec)` [ADR-001]
- M08-F06: `add_rtovr_port_mutex_axiom(solver, ops, T, P, spec) [ADR-001]`

## Phase 4: 编排层
- M12-F01: `FootprintMask.record(cycle, iq_type, bank_id)`
- M12-F02: `FootprintMask.record_port(cycle, port_id) [ADR-001]`
- M14-F01: `Stitcher.stitch(apb_a, apb_b, res_table)`
- M14-F02: `Stitcher.resolve_rr_conflict(ops)`
- M14-F06: `Stitcher.resolve_port_conflict(apb_list)` [ADR-001]

## Phase 5: 驱动与 LIR 层 [ADR-005]
- **M19-F01**: `BitPacker.pack_41bit(iq_conf, payload, loops, dly) -> int`
- **M19-F02**: `LIRPacker.check_bit_budget_collision(code_width, tools_width) -> bool`
- **M19-F03**: `Compactor.compress_with_nop_loop(stream) -> List[LIRPacket]`
- **M19-F04**: `HSTSVerifier.trace_slot_lifetime(stream) -> Dict[int, Range]`

## 测试原则
- **属性测试优先**：测"所有 schedule 满足 RAW"，不测"sin 拍数=12"
- **Mock 工厂**：`make_mock_spec(**overrides)` 从真实 YAML 派生
