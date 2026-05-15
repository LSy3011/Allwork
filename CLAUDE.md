# AIACC Compiler

NPU 算子编译器：SymPy 公式 → MIR → Z3 SMT 调度 → LIR → hex 固件 → SMC 仿真比对。

## Run
- 主入口：`python3 verification_v2/main_verify.py --op <name> --mode {e2e|smt-unit|hw-parity|all}`
- 算子类别：`math` / `trig` / `activation` / `manual`（V1 的 Atomic/Composite/Algebraic 已废弃）
- pytest：`pytest tests/`（仅 5 个活测试，全部 V2 后）
- ~~已知失败：smt-unit 2 fail + 1 error~~ — **2026-05-15 已修复**（ADR-006 latch upper bound + ADR-009 W↔R 双修），smt-unit 12 测试全 pass

## Tech
- Python 3.10 · 5 个直接依赖固定版本见 [`requirements.txt`](requirements.txt)（z3-solver / pydantic / sympy / numpy / PyYAML，详见 [ADR-007](docs/adr/ADR-007-pin-direct-dependencies.md)）
- ⚠ 无 `Dockerfile` / CI 配置（已知缺失）
- HW spec：`hw_config/{hw_params,modules,iq_config,pipeline_delays,rtovr_config}.yaml` + `specs/{system,units}/`
- 仿真器纯 Python（`sim/smc_simulator/`），通过 subprocess 调起；`sim/aiacc_hardware/specs` symlink → `hw_config/specs/`

## Glossary
详见 [docs/glossary.md](docs/glossary.md)。核心：SMC / UR / Bank / IQ / RtoVR / ADPC / MIR / LIR / II。

## Sub-agents
- `.claude/agents/hco.md` — AIACC 专属硬件契约审查；hw_config/、SMT axioms、ALU 延迟相关变更必经
- SOP 通用：`evaluator`（独立验收，反 self-eval bias）/ `appsec` / `db` / `ops` / `pm` / `ux`（横向关注点，按需调用）

## Decision routing

> 🔒 **总则（[ADR-004](docs/adr/ADR-004-doc-first-principle.md) 文档先行）**：新功能 / 新算子 / 新 axiom / 流水线接口变更，**写代码前必须先有 ADR 或 spec doc**。Bug fix、单算子 yaml、重命名、docstring 不受此约束。

| 触发路径 | 处理 |
|---|---|
| `hw_config/**` 任何 yaml 数值变更 | 必调 `hco` subagent + 写 ADR |
| `src/synthesis/axioms/**` 增删 axiom | 必调 `hco` subagent + 写 ADR |
| `src/api/hw_spec.py` schema 变更 | 必调 `hco` subagent |
| `src/driver/{hw_constants,hw_resource}.py` | 必调 `hco` subagent（硬件常量/资源类型） |
| `src/{frontend,synthesis,driver,ir}/` 流水线接口变更 | 写 ADR 到 `docs/adr/` |
| `verification_v2/main_verify.py` mode / 入口变更 | 写 ADR |
| `--mode hw-parity` 行为变化 / 新算子物理验收 | 调 `evaluator` subagent 复核 |
| 新算子 yaml（`verification_v2/ops/*.yaml`） | AI 自决，需跑 `--op <name>` 通过 |
| Bug fix / 重命名 / docstring / 单元测试新增 | AI 自决，无 ADR |
| `docs/{adm,se,tfd,pvo,ig}/**` 旧 docs 改动 | **暂不主动改**（CLAUDE.md.local 红线） |

## Known unknowns

- ~~⚠ smt-unit 2 fail + 1 error~~ — **2026-05-15 resolved**（ADR-006 + ADR-009，12 测试全 pass）
- ⚠ **HSTS 是否真死** —— `compactor.py` 注释提到 "Automated IQ Loop Stitching"，可能是 HSTS 的轻量后继；需读 compactor.py 确认
- ~~⚠ 旧 ADR still_holds 未审计~~ — **已审计**（2026-05-15，见 docs/adr/ADR-005）：1 个 yes (ADR-006) / 2 个 partial (001, 005) / 2 个 no (002, 003)
- ~~⚠ Bank 冲突 axiom 与 spec 不一致~~ — **2026-05-15 resolved**（ADR-009 修 axiom，axiom/Verifier/ADR-008 三层一致）
- ~~⚠ axiom 墓地~~ — **2026-05-15 resolved**（ADR-013 删 16 项 + special_axioms.py 文件；axiom 模块现 22→7 真 active，全部生效；smt-unit 13/13 + horner/matmul/cos4 e2e PASS）
- ⚠ **`add_side_effect_phase_axiom` 不是 no-op**（ADR-010 errata）—— ADR-011 实施 A/B 实测：k_phase 变量等价于 quotient-remainder 分解，**实际影响 z3 搜索**（log II=7 UNSAT 时间 ±137%，II=8 SAT 时间 ±85%，净总打平）。core `Implies(same_iq, phase==phase)` 注释掉但 bound 约束仍生效。ADR-010 标 "no-op" 是错的，ADR-011 Consequences 段已订正
- ~~⚠ matmul flaky FAIL~~ — **2026-05-15 resolved**（`matmul_op.py:prepare_input` 改用 `np.random.default_rng(0)` 固定 seed，3 次重跑稳定 PASS）

## Historical docs (V1/Gemini era)
`docs/{hco,adm,se,tfd,pvo,ig}/` 是 7-角色 Gemini 时代 docs。原 7 角色 → 现仅 HCO 升级为 sub-agent，其余被 SOP 覆盖（详见 docs/glossary.md 末段）。
- ✅ 可参考：`docs/hco/`（硬件约束语义） + `docs/adm/adr/{ADR-001,002,003,005,006}`（V1 决策历史，无 004；状态已审计见 [docs/adr/ADR-005](docs/adr/ADR-005-old-adr-audit.md)，引用前**必看 frontmatter `still_holds`**）
- 🪦 已过期：HSTS / AddrColoring / 11-Pass Pipeline / 六大公理 / Atomic-Composite 算子分类
- 新决策一律走 `docs/adr/ADR-NNN.md`，不再扩 7 角色目录
