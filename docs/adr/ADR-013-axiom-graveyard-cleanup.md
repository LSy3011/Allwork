---
adr_id: ADR-013
title: 删除 16 个 dead / no-op axiom + special_axioms.py 文件（axiom 墓地清扫）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-013 · Axiom 墓地清扫

## Context

ADR-010 全量审计 22 axiom 后确认：22 个 def 中**只有 7 个真 active**，1 个语义有效（`add_side_effect_phase_axiom`，详 ADR-010 errata），剩余 **15 个是 dead 或 stub**，加上 1 个 class-only `add_alu_pipeline_axiom` 共 16 项是纯负担。

ADR-010 §Decision §4 当时主动 reject 立即清理，理由：(a) 部分可能含 V1/Gemini 时代设计意图记录；(b) 删除不可逆；(c) 没明显性能瓶颈。

ADR-011 实施完成 + smt-unit 12/12 + e2e 38/38 全 PASS（含 matmul seed fix）+ horner/log/expm1 等慢算子 2-15x 加速后，代码层面的稳定基线已经牢固。**user 2026-05-15 显式批准最激进档清理**，因此本 ADR 推翻 ADR-010 §4 决定。

### 16 项清理对象（来自 ADR-010 Table C+D+E）

**Table C（10 项）—— 注册了但所有调用注释**：
1. `add_dependency_axiom`（dependency_axioms.py:85，wrapper for latency）
2. `add_anti_overwrite_axiom`（physical_axioms.py:74）
3. `add_shadow_binding_axiom`（physical_axioms.py:101）
4. `add_physical_binding_axiom`（physical_axioms.py:129）
5. `add_rtovr_crossbar_axiom`（physical_axioms.py:201）
6. `add_rtovr_alignment_axiom`（physical_axioms.py:13）
7. `add_rtovr_modulo_constraint`（resource_axioms.py:392）
8. `add_rtovr_liveness_axiom`（resource_axioms.py:482）
9. `add_alu_liveness_axiom`（physical_axioms.py:275）
10. `add_iq_alignment_axiom`（physical_axioms.py:332）

**Table D（5 项）—— 未 export 且 body 是 `pass` stub 或主动禁用**：
11. `add_symmetry_breaking_axiom`（resource_axioms.py:331，有真实现但 backend.py:230 明确注释"与商余数降维冲突"——业务决策已禁，无价值保留）
12. `add_read_port_mutex_axiom`（resource_axioms.py:376，body `pass`）
13. `add_write_port_mutex_axiom`（resource_axioms.py:384，body `pass`）
14. `add_cross_bank_copy_axiom`（special_axioms.py:13，body `pass`）
15. `add_long_latency_chain_axiom`（special_axioms.py:21，body `pass`）

**Table E（1 项）**：
16. `add_alu_pipeline_axiom`（axioms/__init__.py:105，class method 内非模块级，零调用）

**额外**：因 #14/#15 是 `special_axioms.py` 唯二内容，连同 `SpecialAxioms` class 一并删除整个文件。

## Decision

**1. 删除 16 个 axiom 函数 + `special_axioms.py` 文件**

具体改动：

- `src/synthesis/axioms/dependency_axioms.py`：删 #1（保留 `add_latency_axiom`）
- `src/synthesis/axioms/physical_axioms.py`：删 #2 #3 #4 #5 #6 #9 #10（保留 `add_iq_latch_protect_axiom`、`add_side_effect_phase_axiom`）
- `src/synthesis/axioms/resource_axioms.py`：删 #7 #8 #11 #12 #13（保留 `add_resource_constraint` / `add_modulo_constraint` / `add_bank_conflict_axiom` / `add_spill_reload_mutex_axiom`）
- `src/synthesis/axioms/special_axioms.py`：**整个文件删除**
- `src/synthesis/axioms/__init__.py`：
  - 删 `from .special_axioms import SpecialAxioms`
  - 删 16 个 module-level `add_*` 别名
  - 删 `SynthesisAxioms` class 中对应的 16 个 wrapper 方法
  - 删 class 内 `add_alu_pipeline_axiom`（#16）
  - 删 `__all__` 中对应条目

**2. 不删 `SynthesisAxioms` class 本身**

ADR-010 提到："兼容旧版的 SynthesisAxioms 类，以便现有的单元测试和外部脚本可以无缝调用"。即使内部空，class 壳作为 import 兼容契约保留——若彻底删 class，外部任何 `from synthesis.axioms import SynthesisAxioms` 立刻 ImportError。class 内仅留 active axiom 的 wrapper（latency、resource_constraint 系列）。

**3. 不动 `add_side_effect_phase_axiom`**

ADR-010 Errata 2026-05-15 实测确认它不是 no-op，搜索路径有实质影响。保留。

**4. 不动 7 个 active axiom**

保留：`add_latency_axiom` / `add_resource_constraint` / `add_modulo_constraint` / `add_bank_conflict_axiom` / `add_spill_reload_mutex_axiom` / `add_iq_latch_protect_axiom` / `add_side_effect_phase_axiom`（+ backend.py:198 inline ADR-006 上界，非函数）。

**5. 验证矩阵**

提交前必须通过：
- `python3 -m unittest tests.unit.test_smt_adversarial -v` → **13/13** pass（新增 R10 ALU spike）
- `python3 verification_v2/main_verify.py --op horner --mode e2e` → SAT II=11, PASS
- `python3 verification_v2/main_verify.py --op matmul --mode e2e` → SAT II=6, PASS
- 任意一个 trig 算子（如 cos4）→ PASS

**5.1 新增 ALU 发射排他 spike 测试**（HCO 审查 2026-05-15 待商榷项）

`add_alu_pipeline_axiom`（class-only line 105-137）实现"同 ALU 同模槽不可同时发射"。它从未被调用，但内容是潜在有用物理约束。删之前需验证 `add_resource_constraint`（active）能否 cover：构造 2 个 FPADD op、相同 iq_id、相同 t → 期望 UNSAT。

测试加到 `tests/unit/test_smt_adversarial.py:TestSMTRefutation.test_impossible_alu_issue_collision`（R10）。若该测试在删除前已 pass → 安全删；若 fail → 必须先补强 `add_resource_constraint` 才能删。

## Rejected

- **保留 #11 `add_symmetry_breaking_axiom`** ⚠
  - why：它有真实现而不是 stub，backend.py:230 用 comment 显式禁用。但 ADR-010 已确认其"与商余数降维深度冲突"，没人主动启用价值；保留只是混淆 reviewer。删
- **删整个 `special_axioms.py` 但保留空 SpecialAxioms class 作为兼容壳** ⚠
  - why：grep 显示零外部引用，class 壳无价值
- **同步动 backend.py 路径里的 inline 实现**（如 ADR-006 inline O(E) 上界）⚠
  - why：那不是 axiom 函数，是 inline 代码；当前 active 且经 smt-unit/e2e 验证；不在本 ADR scope
- **改 `axioms/__init__.py` 类结构（合并模块、改 namespace）** ⚠
  - why：保守起见，本 ADR 只删不改结构。后续若需要 namespace 重构走独立 ADR

## Consequences

- ✅ **代码量减少**：估计 ~400-600 行删减；axiom 模块只剩真 active 实现
- ✅ **mental model 清晰**：reviewer 看到的 17 个 axiom 都是有用的（7 active + 1 模型异常但有效 + 9 active 子句）。ADR-010 Table 永远是"考古史"，新人不会误以为 22 个都在 work
- ✅ **HCO 审查负担降低**：B 类清单不必再考虑 14 个 dead 是否需要重启用
- ⚠ **不可逆**：删了就没了；若未来某个新算子需要 anti_overwrite 之类的语义，需要从 git history 翻出来（不是 V1 设计意图丢失，git log 仍可访问，但翻出来重启用要走新 ADR 审查链）
- ⚠ **smt-unit 重新跑必须 pass**：删之后立刻验证；任何 regression 必须立刻 revert + 加 ADR
- ⚠ **历史 docs 中 16 个 dead-axiom 引用作为考古文献保留，不修**（HCO 审查 2026-05-15 待商榷项）：
  - `docs/hardware/smt_axiom_analysis.md` / `smt_optimization_plan.md` 表格大量引用即将删除的 axiom 名
  - `docs/operators/fmod_analysis.md:41` 实写 `SynthesisAxioms.add_rtovr_crossbar_axiom`
  - `docs/adr/ADR-006-smt-latch-lifetime-upper-bound.md` 多处引用 `add_anti_overwrite_axiom`（作为"已废弃对照"，by design）
  - 处理：在 `docs/hardware/smt_axiom_analysis.md` 顶部加 **deprecated banner** 指向 ADR-010/013；其他 docs 不动（ADR 文档天然是历史归档；CLAUDE.md 红线 "docs/{adm,se,tfd,pvo,ig}/** 暂不主动改" 体现同一原则）
  - 后续 grep 一个已删 axiom 名字若命中 docs 而非代码 → 是预期行为不是 bug
- 🔒 **binding**：
  - 任何"我们以前有个 axiom 叫 X" 的回忆性引用一律去 git log 查；不允许"凭印象"提及已删 axiom
  - 未来新 axiom 必须走 ADR + HCO + 真实调用点注册（沿用 ADR-010 §Decision §3 binding）
  - `SynthesisAxioms` class 壳保留作为兼容契约，禁止"顺便也删了" without 新 ADR

## Implementation outline

```
# Step 1: source files
rm src/synthesis/axioms/special_axioms.py

# Step 2: physical_axioms.py - delete 7 methods
#   add_rtovr_alignment_axiom, add_anti_overwrite_axiom, add_shadow_binding_axiom,
#   add_physical_binding_axiom, add_rtovr_crossbar_axiom, add_alu_liveness_axiom,
#   add_iq_alignment_axiom
# Keep: add_iq_latch_protect_axiom, add_side_effect_phase_axiom, _get_resource_type

# Step 3: resource_axioms.py - delete 5 methods
#   add_symmetry_breaking_axiom, add_read_port_mutex_axiom, add_write_port_mutex_axiom,
#   add_rtovr_modulo_constraint, add_rtovr_liveness_axiom
# Keep: add_resource_constraint, add_modulo_constraint, add_bank_conflict_axiom,
#   add_spill_reload_mutex_axiom, get_remainder_vars, _get_resource_type

# Step 4: dependency_axioms.py - delete 1 method
#   add_dependency_axiom (wrapper)
# Keep: add_latency_axiom

# Step 5: axioms/__init__.py - 明列三件子事（HCO 审查 2026-05-15 阻断项）
#   5a. delete `from .special_axioms import SpecialAxioms` (line 4)
#   5b. delete 11 module-level `add_*` aliases (lines 8, 12, 13, 17, 19-22, 24, 25)
#   5c. delete `__all__` entries: "SpecialAxioms" (line 143) + 11 个 "add_xxx" 字符串
#       (lines 146, 149, 150, 153, 155-158, 160, 161)
#   5d. delete 16 wrapper methods inside `SynthesisAxioms` class (lines 41-58, 69-102)
#   5e. delete `add_alu_pipeline_axiom` class method (lines 105-137, 33 行 z3 约束代码)
#
#   保留集（11 项）：DependencyAxioms / ResourceAxioms / PhysicalAxioms / SynthesisAxioms
#   + 7 个 active add_* （latency / resource_constraint / modulo_constraint /
#   bank_conflict / spill_reload_mutex / iq_latch_protect / side_effect_phase）
#   + ADR-006 inline 上界（非函数化，留在 backend.py，不动）

# Step 6: verify
python3 -m unittest tests.unit.test_smt_adversarial -v   # 12/12
python3 verification_v2/main_verify.py --op horner --mode e2e --quiet --no-review
python3 verification_v2/main_verify.py --op matmul --mode e2e --quiet --no-review
```
