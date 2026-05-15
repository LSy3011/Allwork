---
adr_id: ADR-011
title: SMT 求解加速 + Z3 确定性 4 项治理（arith.solver=2 为关键）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-011 · SMT 求解加速 4 项治理（确定性 / 紧 MII / 去 tracking / tactic）

## Context

ADR-010 全量审计 22 axiom 后，对 horner + log 做 per-axiom + per-II profile，发现：

1. **SMT 不是 e2e 主瓶颈**：log 算子 SMT 5.9s / e2e 29s = 20%，80% 在 LIR/hex/Python sim/Golden
2. **SMT 内部 constraint 生成 < 0.1%**，99.9% 在 `solver.check()`，**所以 axiom 模型微调几乎没收益**
3. **真痛点是 z3 非确定性**：horner 同算子两次 e2e 跑产出 II=11 vs II=16（45% 吞吐差）。原因：`ucode_solver.py:171 solver.set("timeout", 10000)` + line 227 `if solver.check() != z3.sat: return None` 把 z3 `unknown`（10s 内没决定）一刀切当 unsat 处理，外层 trial 跳到下个 II
4. **escalation 不是解药**：本 ADR 早期版本曾尝试"UNKNOWN 走 10s→30s→90s 升级 timeout 重试"，实测 horner II=9 跑满 130s 仍 unknown（z3 多花 9 倍时间也没用），编译时间从 23s 涨到 150s。**z3 在硬实例上多发 timeout 不会改变搜索路径**。

### Z3 三态语义

| z3.check() 返回 | 含义 | 旧代码动作 | 新代码动作 |
|---|---|---|---|
| `sat` | 找到 model | 取 model | 取 model |
| `unsat` | **证明**无解 | return None → 下一 II | return UNSAT，跳过 |
| `unknown` | 在 timeout 内未决定 | return None → 下一 II ❌ | **换 random_seed 重试 1 次；仍 unknown 跳过并 warn** |

### 加速思路

z3 在模调度问题上的耗时主要源于：(a) 在 II 边界附近（接近 SAT/UNSAT 临界）的搜索空间爆炸；(b) 每个 trial 的常数开销。所以加速分两路：

- **缩小 trial 次数**：MII 越接近真 SAT II，UNSAT trial 越少（horner 当前 MII=9 但真 SAT=11，浪费 II=9,10 两次 trial）
- **每次 trial 更快**：去掉调试 overhead、调 z3 tactic

## Decision

**Phase 1：z3 确定性 + tristate + 换 seed 重试**

1.1 `solver.set("random_seed", 0)`：每次调用同一 seed → **z3 搜索路径完全确定**，同算子永远产出同一 II。

1.2 `_solve_for_ii_with_units` 返回 tristate `(SolverStatus, payload)`：
- `(SAT, (schedule, unit_map))`
- `(UNSAT, None)`：z3 **证明**无解
- `(UNKNOWN, None)`：z3 timeout，未决定

1.3 单次 timeout 30s（不再阶梯）。UNKNOWN 时**换 seed=42 重试 1 次** 15s；仍 unknown 则跳过该 II 并 log warn。

1.4 Solver 每次 attempt 完整重建（HCO 阻断项）；UNKNOWN 分支不调 `unsat_core()`（HCO 阻断项）。

1.5 总 budget 上限 `300s`，环境变量 `AIACC_SOLVER_BUDGET_S` 覆盖。

**Phase 2：Loop-carried RecMII —— 跳过 SAT II 必然大于 L 的 trial**

⚠ **2026-05-15 设计修正**：本 ADR 早期版本曾把"关键路径延迟"作为 MII 下界，**这是错的** —— 模调度中关键路径 = 单迭代 schedule 长度，可以远大于 II（流水线重叠的本质就是 critical_path > II）。horner 关键路径 = 9 FPMUL × 4 lat = 36 cycles 而 SAT II=11；按错误下界会强制 II ≥ 36 → 全部 UNSAT。

正确做法：**只在有 loop-carried back-edge 时才推高 MII**。

当前 `src/core/ii_analyzer.py:IIAnalyzer.compute()` 只算两类下界：
- 资源 MII：`max(ceil(usage_count / hw_instances))` 按 op type 分类
- Bank MII：`max(ops_per_bank)`

新增 `_compute_recurrence_mii(ops) -> int`：扫描所有 op 的 `metadata.loop_carried_depends_on`（dep_id → latency 字典），取 `max(latency)` 作为 RecMII 下界。模调度理论 ：单 back-edge 强制 II ≥ latency。

最终 MII = `max(II_res, II_bank, II_rec, 1)`。

⚠ **horner / 大部分算子无 loop_carried_depends_on，Phase 2 在它们身上是 no-op**；只对 activation / 部分 manual 算子有效。所以 Phase 2 收益是**条件性的**，不应作为通用加速依赖。

**Phase 3：移除 `assert_and_track` 生产路径**

`ucode_solver.py:172-182` 把所有 `solver.add()` 替换成 `assert_and_track(arg, Bool(f"lbl_{N}"))`，每条约束生成命名 label 留给 unsat_core 提取。**问题**：
- ADR-011 Phase 1 已禁 UNKNOWN 分支调 unsat_core
- 生产路径只在 `z3.unsat` 分支调，但实际 ucode_solver.py:264-269 已注释掉那个 print
- 即整个 `_track_idx` + label 创建是**纯调试机器**，生产无收益

改造：`tracked_add` 仅在 `AIACC_SOLVER_DEBUG=1` 环境变量下启用；默认走原生 `solver.add`。

预期：z3 内部 hashing / clause 存储减少 → 10-30% 加速（实测确认）。

**Phase 4：z3 arith.solver=2（差分逻辑 special path）**

**核心发现**：z3 4.16 默认 `smt.arith.solver=6`（通用 simplex LIA），但**模调度问题大量约束形如 `t[i] - t[j] >= c`，是差分逻辑（difference logic）的 special shape**。z3 提供 `smt.arith.solver=2`（基于 Bellman-Ford 的 diff-logic 实现），对此结构是**多项式时间**。

实测 bench（horner e2e，2026-05-15）：

| Tactic | 设置 | elapsed | 备注 |
|---|---|---|---|
| A (default) | `arith.solver=6` | 53.32s | II=9 timeout，含 UNKNOWN skip |
| B | `arith.solver=6` 显式 | 53.31s | 同 A |
| C | `arith.solver=6` + `relevancy=0` | 90s | UNSAT 全 II（设置不稳） |
| **D** | **`arith.solver=2`** | **10.95s** ✅ | **II=11 无 UNKNOWN，5x faster** |

固定 `solver.set("smt.arith.solver", 2)` 写入 `_solve_for_ii_with_units` 默认。

**Phase 1-4 综合 bench**（2026-05-15，e2e wall-clock）：

| 算子 | Baseline | Phase 1-4 | 加速 | II 改进 |
|---|---|---|---|---|
| horner | 23.30s | 10.95s | 2.1x | 11→11 |
| log | 29.30s | **1.99s** | **14.7x** | 11→**8** ⬇ |
| expm1 | 16.20s | 3.35s | 4.8x | 9→9 |
| rsqrt | 15.30s | 1.93s | 7.9x | 9→**8** ⬇ |
| exp5 | 15.30s | 7.61s | 2.0x | 11→11 |
| sqrt | 5.03s | 1.88s | 2.7x | 8→8 |
| tanh7 | 4.60s | 1.64s | 2.8x | 7→7 |

**双重收益**：
- 编译时间 2-15 倍加速（核心 Phase 4 贡献）
- log / rsqrt 找到更优 II（baseline 的 II 是 default solver timeout 漏检的次优解）
- horner 3 次 reproducibility 完美（10.95s ±0.02s）

Phase 1+3 单独贡献：可重现性 + 10-30% solver overhead 削减；Phase 4 是数量级加速的关键。

## Rejected

- **timeout 阶梯 10/30/90s 重试** ⚠（本 ADR 早期版本）
  - why：实测 horner II=9 跑满 130s 仍 unknown；z3 同一搜索路径多发时间不改变结论。**正确做法是换 seed 改变搜索路径**
- **完全去 timeout（unlimited）** ⚠
  - why：病态 case 卡住数小时；快速失败语义有价值
- **二分 II** ⚠
  - why：模调度的 II 空间在硬件约束下非单调（line 28 注释：addcmul II=2..4 SAT, II=5 UNSAT）
- **完全干掉 tristate，把 UNKNOWN 当 UNSAT 处理（接受 45% 吞吐损失）** ⚠
  - why：编译器产出不可重现是 binding 红线；确定性 seed 加上换 seed 重试已极大减少 UNKNOWN 概率
- **改 z3 → Or-Tools / 自研** ⚠
  - why：影响半径太大、重写工作量过大、当前 z3 90%+ 算子 < 1s SAT 已足够
- **并行多 seed z3 race**（多进程同时跑不同 seed，取最先 SAT 的） ⚠
  - why：v1 保守起见；并行带来日志混乱、超量内存、不可重现风险（race 顺序不稳）。留 ADR-013 评估

## Consequences

- ✅ **可重现**：z3 random_seed=0 + 单一 entry path → 同算子两次跑同一 II
- ✅ **更快**：Phase 2 紧 MII 直接跳过 unreachable trial；Phase 3 去 tracking 开销；Phase 4 tactic 调优
- ✅ **正确性优先**：UNKNOWN 不再被吞作 UNSAT
- ⚠ **Phase 1 单算子最坏 case**：1 个真 unknown II × (30 + 15) = 45s（比当前 escalation 130s 好，但比旧 10s 差）；用 Phase 2 紧 MII 让 unknown trial 极少出现
- ⚠ **Phase 4 数值未定**：本 ADR 不预设最优 tactic 组合，实施时附 bench 数据；若 default 已最快则 phase 4 是 no-op
- ⚠ **AIACC_SOLVER_DEBUG=1 模式**：unsat_core 调试要先 export 此环境变量再跑；CLAUDE.md 记一条
- 🔒 **binding**：
  - 任何对 `_solve_for_ii_with_units` 的修改必须保持 tristate enum 返回语义
  - 不允许去掉 `random_seed=0` 设置（除非新 ADR 论证）
  - 不允许在生产路径无条件启用 `tracked_add` —— 必须 gated by `AIACC_SOLVER_DEBUG`
  - 任何对 IIAnalyzer 的 MII 算法改动必须保证 "MII 仍是 SAT II 的下界"（即给出的 MII ≤ 真 SAT II）

## Implementation outline

```python
# ucode_solver.py

import os, time, z3
from enum import Enum

class SolverStatus(Enum):
    SAT = "sat"
    UNSAT = "unsat"
    UNKNOWN = "unknown"

_TIMEOUT_PRIMARY_MS = 30_000
_TIMEOUT_RETRY_MS = 15_000
_RETRY_SEED = 42
_TOTAL_BUDGET_S = int(os.environ.get("AIACC_SOLVER_BUDGET_S", "300"))
_DEBUG = os.environ.get("AIACC_SOLVER_DEBUG") == "1"

def _solve_for_ii_with_units(self, ops, II, timeout_ms=_TIMEOUT_PRIMARY_MS, random_seed=0):
    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    solver.set("random_seed", random_seed)
    # Phase 4 tactic (实测后写入 default)
    # solver.set("smt.arith.solver", 6)
    if _DEBUG:
        # tracked_add only when debugging unsat_core
        ...
    # ... axiom 加载 ...
    res = solver.check()
    if res == z3.sat:
        return (SolverStatus.SAT, (schedule, unit_map))
    if res == z3.unsat:
        return (SolverStatus.UNSAT, None)
    return (SolverStatus.UNKNOWN, None)

def solve_with_units(self, ops, MII=1, MAX_II=32):
    # Phase 2: 紧 MII
    computed_mii = IIAnalyzer(self.hw_spec).compute(ops)  # 内部已含 critical-path
    effective_mii = max(MII, computed_mii)

    budget_start = time.perf_counter()
    for ii in range(effective_mii, MAX_II + 1):
        if time.perf_counter() - budget_start > _TOTAL_BUDGET_S:
            print(f"[BUDGET-EXHAUSTED] {_TOTAL_BUDGET_S}s exceeded; set AIACC_SOLVER_BUDGET_S=N to extend")
            break

        # 默认 seed=0
        status, payload = self._solve_for_ii_with_units(ops, ii, _TIMEOUT_PRIMARY_MS, 0)
        if status == SolverStatus.UNKNOWN:
            # 换 seed 重试一次
            print(f"[SOLVER] II={ii} UNKNOWN at seed=0; retrying seed={_RETRY_SEED}")
            status, payload = self._solve_for_ii_with_units(ops, ii, _TIMEOUT_RETRY_MS, _RETRY_SEED)

        if status == SolverStatus.SAT:
            schedule, unit_map = payload
            if self.validate_physical_schedule(ops, schedule, unit_map, ii):
                print(f"[SOLVER] II={ii} SAT and Verified. Optimal.")
                # ...
                break
        elif status == SolverStatus.UNSAT:
            print(f"[SOLVER] II={ii} UNSAT (proven). Next.")
        else:
            print(f"[SOLVER] II={ii} UNKNOWN after 2 seeds; skipping with warning")
```

```python
# src/core/ii_analyzer.py — Phase 2 RecMII（修正后）

def _compute_recurrence_mii(self, ops) -> int:
    """Loop-carried back-edge 下界。模调度理论：每条 back-edge II >= latency。"""
    max_rec = 1
    for op in ops:
        for _dep_id, lat in op.metadata.get("loop_carried_depends_on", {}).items():
            if lat > max_rec:
                max_rec = lat
    return max_rec

def compute(self, ops):
    # ... 现有 II_res / II_bank 计算 ...
    II_rec = self._compute_recurrence_mii(ops)
    return max(II_res, II_bank, II_rec, 1)
```

## 测试矩阵

- 单元测试：`tests/unit/test_solver_tristate.py`
  - `test_unsat_returns_status_unsat`：proven UNSAT 返回 `(UNSAT, None)`
  - `test_unknown_returns_status_unknown`：故意复杂图 + timeout=10ms 返回 `(UNKNOWN, None)`
  - `test_seed_retry_succeeds`：seed=0 必 unknown、seed=42 必 SAT 的边界图（horner 是候选）
  - `test_budget_exhausted_terminates`：mock time.perf_counter 让 budget 在第 2 个 II 耗尽
- 集成测试：`tests/integration/test_horner_reproducibility.py`
  - 3 次跑同 op 断言 II 完全一致
- Bench：horner / log / expm1 / rsqrt e2e 计时，与 baseline 23s/29s/16s/15s 对比
