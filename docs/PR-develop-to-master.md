# PR: develop → master — Gemini→Claude SOP 迁移 + V2 验证框架 + SMT 加速与瘦身

**一句话**：把项目从 Google Gemini 时代的 V1 架构整体迁移到 Claude + SOP v7.0 方法论，落地 V2 验证框架，修复 3 类硬件契约正确性缺陷，SMT 求解 2–15x 加速，并删除约 1 万行死代码。

---

## 边界声明

- **Covers**：`develop` 领先 `master` 的全部 51 个 commit。范围包括 SOP Phase 0 bootstrap、ADR-001~017 决策记录、V2 验证框架、SMT 求解器正确性 + 性能、axiom/solver/verifier 整合瘦身、依赖与缓存基建。
- **Does NOT cover**：硬件 RTL 本身、`sim/` 仿真器内核算法（仅调用方式被触及）、V1 历史 docs（`docs/{adm,se,tfd,pvo,ig}/` 按 ADR-005 仅标注 frontmatter 不重写）、未来 ADR-018（return-value lint enforcement，已在 ADR-017 标记 deferred）。
- **For**：单条决策的完整论证看对应 `docs/adr/ADR-NNN-*.md`；硬件契约语义看 `docs/glossary.md` + `.claude/agents/hco.md`；逐 commit 细节看 `git log master..develop`。

---

## 规模

| 指标 | 值 |
|---|---|
| commits | 51（`master..develop`） |
| 文件变更 | 405 |
| 行变更 | +8,039 / −18,377（**净 −10,338**） |
| 新增 ADR | 16（ADR-001~017，无 012 —— 并入 ADR-011） |
| 单元测试 | 20/20 PASS（smt-adversarial 13 + verifier-robustness 7） |
| e2e | 38 算子全 PASS（math/trig/activation/manual） |

---

## 五大主题

### A. SOP / Gemini→Claude 迁移（ADR-001~005）

- Phase 0 bootstrap：建立 `CLAUDE.md` / `docs/glossary.md` / HCO sub-agent
- 7 角色（HCO/ADM/SE/TFD/IE/PVO/IG）→ 仅 HCO 升级为 sub-agent，其余由 SOP 通用 agent 覆盖
- ADR-004 确立"文档先行"为目标（当时现状是代码先行）
- ADR-005 审计 V1 旧 ADR 的 `still_holds` 状态
- V1 死代码大清扫（`988e521`，`verification_v2` 活路径外全删）

### B. 硬件契约正确性修复（ADR-006/008/009 + matmul）

- **ADR-006**：SMTSynthesizer 缺失的 O(E) latch lifetime 上界补全 —— 修掉 smt-unit 2 fail + 1 error
- **ADR-008**：Bank 冲突精确语义定档（R-R / R-W / W-W 同 Bank 同 cycle 全禁，user 口径权威）
- **ADR-009**：把 W↔R 跨类型 mutex 补进 `add_bank_conflict_axiom`（之前只 TimingVerifier 兜底）；硬件无 forwarding，同地址也禁
- **matmul flaky fix**：`np.random.default_rng(0)` 固定输入，消除 FP tol 边界偶发 FAIL

### C. SMT 求解性能（ADR-011/014）

- **根因**：z3 4.16 默认 `arith.solver=6`（通用 simplex）在模调度的差分逻辑结构上走慢路径
- **解法**：`smt.arith.solver=2`（Bellman-Ford diff-logic）+ `random_seed=0` 确定性 + 去 assert_and_track 调试开销
- **实测**：log 29s→2s（14.7x）、rsqrt 7.9x、horner 2.1x；副产品 log/rsqrt 找到更优 II（baseline 因 z3 timeout 漏检次优解）
- ADR-014 把同样配置同步到 backend smt-unit 路径（−15% + 确定性）

### D. 架构瘦身（ADR-010/013/016/017 + 散点清理）

- **ADR-010**：22 个 axiom 全量审计 —— 仅 7 真 active，写出考古地图
- **ADR-013**：删 16 个 dead/stub axiom + 整个 `special_axioms.py`（−455 行）
- **ADR-016**：双 SMT 求解路径整合 —— 删 `SMTSynthesizer`，smt-unit 迁移到 `UCodeSynthesizer`（单一求解器）
- **ADR-017**：`TimingVerifier` 5 类校验并入 `validate_physical_schedule`，删整个 `backend.py`（单一 verifier）
- 散点死代码：scratch 烟测、`hw_*` intrinsics、`print_aligned_timeline`、`compile_tensor`、driver 的 Compactor/FieldMapper/hw_constants（共 −600+ 行）

### E. 基建（ADR-007/015）

- **ADR-007**：5 个直接依赖固定版本（`requirements.txt`）
- **ADR-015**：HardwareSpec pickle 缓存（mtime 失效）—— 启动 −50ms/run，环境变量可绕过

---

## ADR 索引

| ADR | 主题 | still_holds |
|---|---|---|
| 001 | verification_v2 单一入口 | yes |
| 002 | V1 死代码裁剪 | yes |
| 003 | 采用 SOP，7 角色坍缩 | yes |
| 004 | 文档先行原则 | yes |
| 005 | 旧 ADR 审计 | yes |
| 006 | SMT latch lifetime 上界 | yes |
| 007 | 锁定直接依赖 | yes |
| 008 | Bank 冲突精确语义 | yes |
| 009 | SMT 层补齐 Bank 约束 | yes |
| 010 | 22 axiom 全量审计（+ errata） | yes |
| 011 | SMT 求解加速 4 治理 | yes |
| 013 | axiom 墓地清扫 | yes |
| 014 | backend.py Phase 4 同步 | yes |
| 015 | HardwareSpec pickle 缓存 | yes |
| 016 | 双 SMT 求解路径整合 | yes |
| 017 | TimingVerifier 合并 | yes |

> ADR-012 故意空缺：原计划记录 timeout 阶梯调参，后被 ADR-011 Phase 4（arith.solver=2）方案取代，未单独成文。

---

## 验证状态

- **单元**：`python3 -m unittest tests.unit.test_smt_adversarial tests.unit.test_verifier_robustness` → 20/20 PASS
- **e2e**：`python3 verification_v2/main_verify.py --op all --mode e2e` → 38/38 PASS（含 activation 13、matmul、horner）
- **smt-unit**：13/13（ADR-006 + ADR-009 修复后；曾 2 fail + 1 error）
- **可重现性**：horner 连续 3 次 e2e 产出稳定 II=11（ADR-011 前 II=11/16 不稳定）

---

## 风险与回滚

- **逐 ADR 可回滚**：每个改动有独立 commit + ADR；回退某项不影响其他
- **最高风险项**：ADR-009 W↔R axiom（可能让隐性依赖"R+W 同 Bank 不被拒"的 yaml UNSAT）—— 已跑全 38 算子 e2e 矩阵确认无回归
- **不可逆项**：ADR-013/016/017 删代码 —— git history 可查，重启用需走新 ADR
- **已知遗留**：`add_side_effect_phase_axiom` 核心 `Implies` 注释但 bound 仍生效（ADR-010 errata，非 no-op，刻意保留）；ADR-017 的 "caller 必须检查返回值" 目前靠 docstring + binding，runtime enforce 留 ADR-018

---

## Reviewer 关注建议

1. **正确性优先看**：ADR-006 / ADR-008 / ADR-009（硬件契约）+ `src/synthesis/axioms/resource_axioms.py:add_bank_conflict_axiom`
2. **性能改动看**：ADR-011 `src/synthesis/ucode_solver.py`（arith.solver=2 + tristate + RecMII）
3. **删代码看**：ADR-013/016/017 的"删前验证矩阵"段，确认每次删除前 grep + 测试闭环
4. **HCO 已审**：ADR-009/011/013/016/017 均经 hco sub-agent 独立复核，阻断项已在对应 ADR 正文订正
5. **不需逐行看**：散点死代码删除（commit message 已自带 grep 证据 + 测试结论）
