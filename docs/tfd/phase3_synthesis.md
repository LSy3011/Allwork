# Phase 3: 合成引擎 (M07-M11) ★核心

> 预计 3-5 天。目标：SMTSynthesizer 合成满足物理约束的 APB。

## M07: Z3 环境验证
**验收**：
```bash
python3 -c "import z3; s = z3.Solver(); s.add(z3.Int('x') > 0); print(s.check())"
pytest tests/unit/test_synthesis.py::test_z3_basic_sat -v
```

## M08: synthesis/axioms.py — 六大公理
**要实现的函数**：
- `add_raw_constraints(solver, ops, hw_spec)` — RAW 延时
- `add_rr_conflict_axiom(solver, ops, hw_spec)` — R-R Bank
- `add_ww_conflict_axiom(solver, ops, hw_spec)` — W-W Bank
- `add_bus_width_axiom(solver, ops, hw_spec)` — UR 带宽
- `add_rtovr_connectivity_axiom(solver, ops, T, B, P, spec)` [ADR-001]
- `add_rtovr_port_mutex_axiom(solver, ops, T, P, spec)` [ADR-001]
**验收**：`pytest tests/unit/test_axioms.py -v -k "axiom"`
**通过**：违反→UNSAT，满足→SAT

### ADR-001 专属测试
```python
def test_connectivity_blocks_illegal_route():
    solver.add(B[0] == 0, P[0] == 3)  # Bank0 连不到 Port3
    assert solver.check() == z3.unsat

def test_port_mutex():
    # T0==T1, P0==P1 → UNSAT
```

## M09: synthesis/backend.py — 求解驱动
**函数**：`solve()`, `extract_schedule()`, `diagnose_unsat()`
**验收**：`pytest tests/unit/test_backend.py::test_unsat_diagnosis_names_axiom -v`

## M10: synthesis/constraints.py — MIR→Z3
**验收**：`pytest tests/unit/test_synthesis.py::test_sin_operator_synthesis -v`
**通过**：sin 的 4 FPMUL + 2 FPADD 合成后满足全部物理约束

## M11: synthesis/registry.py — APB 缓存
**函数**：`cache_key()`, `lookup()`, `store()`, `invalidate_all()`
**验收**：`pytest tests/unit/test_registry.py::test_cache_miss_on_hw_change -v`

---
*门禁：M11 全绿才进 Phase 4*
