---
adr_id: ADR-014
title: backend.SMTSynthesizer 同步 ADR-011 Phase 1/3/4（arith.solver=2 + 确定性 seed + 去 tracking）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-014 · backend.py 同步 Phase 1/3/4

## Context

ADR-011 把 z3 求解策略升级到 `arith.solver=2`（差分逻辑 Bellman-Ford solver）+ `random_seed=0` + 禁用 `tracked_add` overhead，在 ucode_solver e2e 主路径上拿到 2-15x 加速。

但 ADR-011 §5 主动 scope-out backend.py（smt-unit 测试路径），理由：backend.py 没有显式 timeout → 不会触发 UNKNOWN→当 UNSAT 的可重现 bug → ADR-011 核心动机（正确性）不适用。

ADR-013 清理 axiom 墓地后，剩余的两条 SMT 求解路径只有 `ucode_solver`（e2e）和 `backend.SMTSynthesizer`（smt-unit）。后者：

- `src/synthesis/backend.py:119` `solver = z3.Solver()`，无 `random_seed`、无 `smt.arith.solver` 显式设置 → z3 默认（`arith.solver=6` simplex）
- `backend.py:120-128` `tracked_add` 把所有约束走 `assert_and_track`，给每条约束创带名字的 Bool label → 调试 overhead，生产路径无 unsat_core 消费者
- 当前 smt-unit 跑 13 个反证测试 ~22-23s

backend.py 的 reverse 测试（如 `test_impossible_ii_latch_lifetime`）依赖 z3 正确判 UNSAT。如果 z3 默认 solver 在某些图上走慢路径（实测 ucode_solver 走过同样的问题），反证测试可能：
- 一直能跑过去，但**慢**（影响 CI 时间）
- 极端情况下，复杂反证图被 z3 不彻底搜索（不太可能，因为 backend 无 timeout，但理论可行）

更重要：smt-unit 测试结果**非确定性**会污染 CI/CD（同一 commit 两次跑可能不同）。虽然反证测试只关心"该 UNSAT 是否 UNSAT"，但 SAT 测试（`test_fpmul_starvation` 期望 SAT II>=4）的 II 值若变化，会让 reviewer 困惑。

ADR-011 §5 binding 早写过："若未来 backend.py 加 timeout，**必须同步改 tristate**——加 ADR 引用本决策"。本 ADR **不加 timeout**（保留无限耐心+反证测试的契约），但同步 Phase 1/3/4 的另外三件子事——它们与 timeout 正交，纯加速 + 确定性收益。

## Decision

**1. `backend.py:_solve_for_ii` 同步 ADR-011 Phase 1/3/4**

具体改动（基于 `src/synthesis/backend.py` 当前结构）：

```python
solver = z3.Solver()
solver.set("random_seed", 0)              # Phase 1: 确定性
solver.set("smt.arith.solver", 2)         # Phase 4: 差分逻辑 solver

# Phase 3: 生产路径禁用 tracked_add；AIACC_SOLVER_DEBUG=1 才启用
import os
if os.environ.get("AIACC_SOLVER_DEBUG") == "1":
    # 保留现有 tracked_add 用于 unsat_core 调试
    ...
else:
    pass  # 走 z3 原生 solver.add
```

复用 ucode_solver.py 中已有的 `_DEBUG_SOLVER` 环境变量语义。

**2. 不加 timeout / tristate**

backend.py 的反证测试需要"无限耐心"——`test_impossible_ii_latch_lifetime` 等用例的数学下界证明就是 z3 必须跑到 UNSAT 才算 pass。加 timeout 会引入 UNKNOWN 可能性，让反证语义模糊。本 ADR scope-out timeout 这件事。

**3. 不改 `solve()` trial loop / IIAnalyzer**

IIAnalyzer 已经在 ADR-011 Phase 2 改过（含 RecMII），backend.py 已经通过 `solve()` 用上了。trial loop 是确定性线性扫描，无需变。

**4. 不动 `axiom add` 调用**

backend.py:207-208 调 `add_resource_constraint` + `add_iq_latch_protect_axiom`，是 ADR-010 / ADR-013 留下的 7 active axiom 之一，本 ADR 不动。

**5. 验证矩阵**

提交前必须通过：
- `python3 -m unittest tests.unit.test_smt_adversarial -v` → 13/13 pass，**时间从 ~22-23s 下降**（具体多少看实测，期望 30-50% 降）
- 同算子两次跑 smt-unit，II 值完全一致（确定性回归门）

## Rejected

- **同时加 timeout + tristate（完全镜像 ucode_solver）** ⚠
  - why：backend.py 是反证测试路径，无限耐心是 by-design 语义；加 timeout 会让 `test_impossible_ii_*` 测试在 UNKNOWN 时退化为模糊结论
- **同时改 trial loop（如换二分搜索）** ⚠
  - why：ADR-011 reject 二分（II 空间非单调）；本 ADR 不动
- **同步 `axiom add` 调用集合**（如让 backend.py 也调 `add_side_effect_phase_axiom`） ⚠
  - why：ADR-010 Table 已记两路径差异，是 product 现状；动 axiom 调用需独立 ADR + HCO 审

## Consequences

- ✅ smt-unit 测试时间**预期下降 30-50%**（实测以 horner ucode_solver 5x 加速类推到 smt-unit 反证图）
- ✅ 确定性：同 commit 两次跑 smt-unit 永远产出同一 II / 同一 SAT trajectory
- ✅ Phase 3 去 `tracked_add` 削减 13 个测试的 z3 内部 Bool 变量开销
- ⚠ **理论风险**：`arith.solver=2` 在某些 z3 内部数据结构上行为与 `=6` 不同；反证测试历来用 `=6` 跑过，换 `=2` 后若有任何 test 状态翻转，本 ADR 必须 revert
- ⚠ smt-unit 测试不在 e2e regression 矩阵里直接 surface 失败原因，**Implementation 后必须立刻跑 13/13 不变**才能提交
- 🔒 **binding**：
  - backend.py 的 z3 配置必须与 ucode_solver.py 的 z3 配置保持 (arith.solver / random_seed / tracking) 一致；任一边动需另一边同步或新 ADR 论证差异
  - 反证测试用例新加时**禁止依赖 z3 内部搜索路径**（如不要断言"II=N 必 unsat 但 II=N-1 必 sat 且 z3 在 5s 内完成"——只断言 sat/unsat）

## Implementation outline

```python
# src/synthesis/backend.py:_solve_for_ii — minimal diff

import os
_DEBUG_SOLVER = os.environ.get("AIACC_SOLVER_DEBUG") == "1"

def _solve_for_ii(self, ops, II):
    solver = z3.Solver()
    solver.set("random_seed", 0)          # NEW
    solver.set("smt.arith.solver", 2)     # NEW

    if _DEBUG_SOLVER:
        # 原 tracked_add 保留在 DEBUG 路径
        orig_add = solver.add
        _track_idx = 0
        def tracked_add(*args, **kwargs):
            nonlocal _track_idx
            for arg in args:
                _track_idx += 1
                lbl = z3.Bool(f"lbl_{_track_idx}_{str(arg)[:80].replace(' ', '').replace('+', '_').replace('-', '_')}")
                solver.assert_and_track(arg, lbl)
        solver.add = tracked_add
    # else: 走 z3 原生 solver.add，不做 wrapper

    # 其余 axiom 加载、约束生成等保持不变
    ...
```

注：`_DEBUG_SOLVER` 已在 `src/synthesis/ucode_solver.py:24` 定义。可以 import 复用或本地再定义（保持模块解耦）。当前选择**本地定义**，避免 backend.py 反向依赖 ucode_solver.py。
