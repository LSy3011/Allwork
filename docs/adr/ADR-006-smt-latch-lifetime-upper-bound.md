---
adr_id: ADR-006
title: 补全 SMT solver 的 O(E) Latch Lifetime 上界约束
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-006 · 补全 SMT O(E) Latch Lifetime 上界

> 注：本 ADR 在新 namespace `docs/adr/`。与 `docs/adm/adr/ADR-006_semantic_lowering.md`（旧 V1 namespace）同号但不同空间，互不取代——新旧编号独立（详见 ADR-005-old-adr-audit.md）。

## Context

`--mode smt-unit` 长期遗留 2 fail + 1 error（自 verification_v2 引入以来即存在），根因定位完毕：

### 失败现象

| 测试 | 行为 |
|---|---|
| `test_asymmetric_fork_join`（用例 5，ERROR） | SMT 返回 II=6 的 schedule；Verifier post-check `backend.py:335` 抓出 "Latch Overwrite: a expired before c_1 read" |
| `test_impossible_ii_latch_lifetime`（反证 R3，FAIL） | 数学下界 II=17 的图，强制 II=16，应 UNSAT 但 SMT 返回 schedule |
| `test_sat_just_above_impossible`（反证 R4，FAIL） | 同一图 II=16/17 边界验证，II=16 应 UNSAT 但 SAT |

### 数学约束（对每条 RAW 依赖边 producer p → consumer c）

```
t_c < t_p + lat_p + II   （若 p 非循环不变量）
```

含义：消费者必须在生产者数据被下一次迭代覆盖之前读取（"Zero-FIFO Latch" 语义）。

### 根因（单一）

`src/synthesis/backend.py:178-194` 的 RAW 依赖循环里：

```python
# 下界：必须等数据产生后才能用
solver.add(t[op.trace_id] >= t[dep_id] + (delay - 1) + transit)

# 🛸 [SMT-REFACTOR] 极简 O(E) 上界，替代臃肿的 add_anti_overwrite_axiom
is_inv = (dep_node.metadata.get("is_invariant", False) or 
          dep_node.metadata.get("is_const", False) or 
          dep_node.metadata.get("stride", 1) == 0 or
          "UR_WRITE" in dep_node.op_type.upper())
# ← 注释承诺的"O(E) 上界" solver.add() 缺失！
```

注释承诺了上界，但实现段**只算了 `is_inv` 标志，从未发出对应的 `solver.add()`**。`axioms.add_anti_overwrite_axiom` 在 `backend.py:206-208` 被显式注释掉，替代品写了一半即停手——一次遗留未完成的重构。

### 影响半径

- 受影响：`src/synthesis/backend.py:SMTSynthesizer`（即 `--mode smt-unit` / `--mode hw-parity` 路径）
- 不受影响：`src/synthesis/ucode_solver.py:UCodeSynthesizer`（verification_v2 e2e 主路径），其有独立的 `add_iq_latch_protect_axiom` 调用
- 生产 yaml 算子的 II 均远高于 latch overwrite 边界（horner II=11, activation II≥10），bug 不在 e2e 触发；`TimingVerifier` post-check 是兜底防线

## Decision

在 `src/synthesis/backend.py:_solve_for_ii` 的 RAW 依赖循环末尾**内联补 3 行**：

```python
if not is_inv:
    # O(E) 上界：消费者必须在生产者数据被下一迭代覆盖前读取
    solver.add(t[op.trace_id] < t[dep_id] + delay + II)
```

具体插入位置：第 194 行（`is_inv = (...)` 块结束）之后。

**Mechanism 选择**：
- ❌ 不复活 `axioms.add_anti_overwrite_axiom`（保持"已废弃"状态）
- ❌ 不在 `src/synthesis/axioms/physical_axioms.py` 新增 axiom 函数（避免与现有 `add_iq_latch_protect_axiom` / `add_alu_liveness_axiom` 混淆）
- ✅ 内联在 RAW 依赖循环（与下界紧邻），可读性最佳
- ✅ HCO sub-agent 在 PR 审查中复核 `is_inv` 4 个豁免条件的物理语义完备性（hco.md B/D 类清单）

## Rejected

- **复活 `axioms.add_anti_overwrite_axiom` 并调用** ⚠
  - why：上次注释掉的明确原因是"臃肿"（注释原文 "替代臃肿的 add_anti_overwrite_axiom"），复活违背重构原意；同时与"5 个未 export axiom 治理方向"相反
  - HCO 审查澄清（2026-05-15）：旧 axiom **真正的"臃肿"不是 O 复杂度**（约束总数仍是 O(E)），而是 (1) **豁免不全**——只有 `is_invariant` 一条，漏 `stride==0` + `UR_WRITE`；(2) **形式冗余**——公式 `t_cons - (t_p + lat - 1) <= II` 含多余的 `-1+1` 抵消
- **在 `physical_axioms/` 加新 axiom（如 `add_latch_lifetime_axiom`）** ⚠
  - why：与现有 `add_iq_latch_protect_axiom`（IQ 层）/ `add_alu_liveness_axiom`（ALU 层）容易混淆；O(E) 是 RAW 依赖的直接上界，与下界紧邻，内联实现可读性更高
- **不修，把 3 个测试 mark 为 expected-failure** ⚠
  - why：测试套件名为 `TestSMTRefutation`，反证 SMT 不给假解是项目对 SMT 信任承诺的核心，不应静音
- **Quick fix 先修代码 + 后补 ADR** ⚠
  - why：违反 ADR-004 doc-first 总则；axiom 实质变更（即使是补回历史遗漏）必须先有 ADR + hco 审查

## Consequences

- ✅ 3 个 smt-unit 失败转 pass，长期遗留消除
- ✅ SMT 与 `TimingVerifier` 行为一致，不再依赖 Verifier 作 "二级拦截" 来兜底建模漏洞
- ✅ `axioms.add_anti_overwrite_axiom` 废弃状态保持清晰，废弃决策不被复活式逆转
- ⚠ `is_inv` 4 个豁免（`is_invariant` / `is_const` / `stride==0` / `UR_WRITE`）需 hco subagent 复核物理语义完备性——是否有遗漏类型？
- ⚠ 理论上微增 SMT 求解开销（每条 RAW 边多一条约束），但 O(E) 远小于现有 axiom O(V²) 量级，可忽略
- 🔒 **binding**：
  - 本约束**必须**在 `SMTSynthesizer._solve_for_ii` 内联实现，不可移到 `axioms/`
  - 未来引入新 producer 豁免类型（如 streaming output / persistent state），必须更新 `is_inv` 表达式 + 触发 hco subagent 审查 + 增量 ADR
  - 若未来 `UCodeSynthesizer`（e2e 主路径）也被发现需要同等约束补全，应通过独立 ADR 决策（逻辑结构不同，不能简单照搬）
