---
adr_id: ADR-016
title: 整合双 SMT 求解路径（backend.SMTSynthesizer → ucode_solver.UCodeSynthesizer）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-016 · 双 SMT 求解路径整合

## Context

ADR-010 §双路径不一致清单 早确认 backend.SMTSynthesizer（smt-unit 测试路径）和 ucode_solver.UCodeSynthesizer（e2e 主路径）是平行实现，每个 ~370 行，constraint 集合存在 4 处显著差异。截至 ADR-015 完成后总账：

| 维度 | backend.SMTSynthesizer | ucode_solver.UCodeSynthesizer |
|---|---|---|
| 变量定义 | t, s=t%II, k=t//II 三套 z3.Int | t, u_vars 两套；q/r 由 `get_remainder_vars` 内部按需创 |
| RAW 下界 | `t >= t_dep + (delay-1) + transit`，transit=2 for UR_WRITE | `add_latency_axiom`：`t >= t_dep + delay` |
| RAW 上界（ADR-006）| inline，**覆盖所有**非 is_invariant/UR_WRITE 边 | 仅 RTOVR 路径（`add_latency_axiom` 内 rtovr_latch_lifecycle） |
| Shadow binding | inline `t in [t_rtovr+1, t_rtovr+II-1]` | `add_latency_axiom` 内 `t_latch = t_prod + lat` + `t_consumer = t_latch + 1`（严格 +1，无窗口） |
| iq latch protect | `add_iq_latch_protect_axiom` ✅ | 不调用 |
| side_effect_phase | 不调用 | 调用（虽然核心 `Implies` 注释，bound 仍有效；ADR-010 errata） |
| Post-verifier | `TimingVerifier.verify` | `validate_physical_schedule` |

两边各有"独有"约束。从结果看：
- backend 跑 smt-unit 13 反证测试 → 12 PASS + R10 PASS（13/13）
- ucode_solver 跑 e2e 38 算子 → 全 PASS

**消费者**：
- backend.SMTSynthesizer 仅 `tests/unit/test_smt_adversarial.py` 使用（13 测试）
- ucode_solver.UCodeSynthesizer 由 `verification_v2/infra/pipeline_runner.py` 使用（生产 e2e）

ADR-014 已确认 backend 和 ucode_solver 的 z3 配置（arith.solver=2 / random_seed=0 / DEBUG gating）严格对齐——两路径求解器层一致，差异**只在 SMT 约束模型**和 **post-verifier**。

### 整合可行性预判

smt-unit 测试用的反证 DFG 是 **RTOVR-free 合成图**（只 FPMUL/FPADD/UR_READ 等 compute nodes，无 RTOVR 路由）。ucode_solver 的 `compute_ops = [op for op in ops if op.op_type.upper() != "RTOVR"]` 在 RTOVR-free 输入上**直接生效**——所有 axiom 仍按计算节点处理。

但 backend 独有约束（inline RAW 上界、iq_latch_protect、TimingVerifier）若是反证语义的必要前提，迁移会让某些 UNSAT 测试错误地变成 SAT（反证失败）。**实测**才能决断。

## Decision

**1. 整合方向：删 backend.SMTSynthesizer，把 smt-unit 反证测试迁移到 ucode_solver.UCodeSynthesizer**

理由：
- ucode_solver 是 e2e 主路径（更核心、更频繁演进）
- backend 仅 1 个测试文件用，迁移面小
- 保留 e2e 主路径不动，回归风险只局限在 13 个反证测试

**2. 实施分两 Phase**

**Phase 1：实测迁移可行性**（本 ADR scope）
- 重写 `tests/unit/test_smt_adversarial.py`：用 `UCodeSynthesizer` + `_solve_for_ii_with_units` API
- 跑 13 测试，记录每条 pass/fail
- **若全 pass**：进入 Phase 2 删 backend
- **若部分 fail**：分析 root cause；若是 ucode_solver 缺少 backend 独有约束（如 iq_latch_protect），把那段约束**移植**到 ucode_solver（同时 e2e 不应回归——逻辑只增不减）

**Phase 2：删 SMTSynthesizer + 保留 TimingVerifier**（如果 Phase 1 全 pass）
- 删除 `src/synthesis/backend.py` 中的 `SMTSynthesizer` 类（约 265 行）
- 保留 `TimingVerifier` 类（test_verifier_robustness.py 使用）
- 验证 smt-unit 13/13 + verifier_robustness 2/2 + 全量 e2e 不回归
- 估计净减 ~265 行

**3. TimingVerifier 何去何从（实施修订 2026-05-15）**

实施时发现 `tests/unit/test_verifier_robustness.py` 直接用 `TimingVerifier` 测两个防御场景（Latch Overwrite + Bank Conflict 模槽碰撞），ucode_solver 的 `validate_physical_schedule` 未覆盖这两条精确语义。**Phase 2 调整**：

- 删 `SMTSynthesizer` 类（~265 行）
- 保留 `TimingVerifier` 类在 backend.py（~102 行）
- backend.py 用途从"smt-unit 求解器 + 后验"收窄成"独立 timing 后验工具"

未来若想把 TimingVerifier 的 Latch Overwrite / Bank Conflict 模槽碰撞校验也并入 ucode_solver 的 validate_physical_schedule，单独 ADR-017。本 ADR 不强制。

## Rejected

- **反向：删 ucode_solver，让 e2e 走 backend** ⚠
  - why：backend 没有 RTOVR latch_vars 处理（add_latency_axiom 内部的 rtovr_latch_lifecycle），e2e 生产路径需要这条；迁移成本远大于删 backend
- **保留双路径，只抽公共部分到 mixin / base class** ⚠
  - why：进一步增加代码层级，不减总 LOC；HCO 审查成本反而高
- **整合 TimingVerifier 与 validate_physical_schedule 为新 verifier** ⚠
  - why：Phase 2 已经把 TimingVerifier 删了，e2e 用 validate_physical_schedule 已经够；不必新建第三套
- **大重写：把两套都作废，写第三套 solver** ⚠
  - why：高风险无回报，e2e 已稳

## Consequences

- ✅ 估计净减 ~370 行（backend.py 全删）+ 测试代码 ~30 行调整
- ✅ "axiom 在 e2e 主路径调，但 smt-unit 不调" 这类不对称消失，HCO 审查只需关心一条求解路径
- ✅ 新加反证测试与新加 e2e axiom 配置自动一致
- ⚠ smt-unit 反证测试改名涉及（`solve(strategy="extreme")` → `solve_with_units(MII=1, MAX_II=32)`；`_solve_for_ii` → `_solve_for_ii_with_units`），任何外部脚本若引用旧 API 会 break——grep 确认目前**只有 test_smt_adversarial.py 用**
- ⚠ Phase 1 若发现 backend 独有约束确有保护作用，**必须先移植到 ucode_solver** 才能删 backend；不允许直接砍掉
- ⚠ 反证测试 R10（ADR-013 加的 ALU 排他）依赖 `_solve_for_ii(ops, II=1)` 返回 None；迁移后改成 status == UNSAT 断言
- 🔒 **binding**：
  - Phase 1 中任何反证测试因为 ucode_solver 缺约束 → 修复必须**加约束到 ucode_solver**，不允许"反证条件放宽"
  - 删 backend.py 是**单向门**——若未来某个反证测试需要 backend 风格的额外校验，必须移植到 ucode_solver 而不是恢复 backend

## Implementation outline

### Phase 1 测试迁移

```python
# tests/unit/test_smt_adversarial.py
from synthesis.ucode_solver import UCodeSynthesizer, SolverStatus
# 删 from synthesis.backend import SMTSynthesizer

class TestSMTAdversarial(unittest.TestCase):
    def setUp(self):
        self.spec = HardwareSpec.load("hw_config")
        self.synthesizer = UCodeSynthesizer(self.spec)  # 替换

    def test_fpmul_starvation(self):
        # ... 同前 ...
        res = self.synthesizer.solve_with_units(ops, MII=1, MAX_II=32)
        # 同 backend.solve 一样返回 dict 或 None
        self.assertIsNotNone(res)
        self.assertGreaterEqual(res["II"], 4)

class TestSMTRefutation(unittest.TestCase):
    def test_impossible_ii_bank_conflict(self):
        # ... 同前 ...
        status, _ = self.synthesizer._solve_for_ii_with_units(ops, II=8)
        self.assertEqual(status, SolverStatus.UNSAT,
            "II=8 应 UNSAT...")
```

### Phase 2 删 backend.py

```bash
git rm src/synthesis/backend.py
# 修 src/synthesis/__init__.py（如果引用了 SMTSynthesizer / TimingVerifier）
# 跑 smt-unit + 全量 e2e 验证
```

### 验证矩阵

- Phase 1 完成时：`python3 -m unittest tests.unit.test_smt_adversarial -v` 13/13 PASS
- Phase 2 完成时：smt-unit 13/13 + `--op horner --mode e2e` PASS + `--op matmul --mode e2e` PASS
