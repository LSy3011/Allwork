# AIACC Compiler

NPU 算子编译器：SymPy 公式 → MIR → Z3 SMT 调度 → LIR → hex 固件 → SMC 仿真比对。

## Run
- 主入口：`python3 verification_v2/main_verify.py --op <name> --mode {e2e|smt-unit|hw-parity|all}`
- 算子类别：`math` / `trig` / `activation` / `manual`（V1 的 Atomic/Composite/Algebraic 已废弃）
- pytest：`pytest tests/`（仅 5 个活测试，全部 V2 后）
- 已知失败：`--mode smt-unit` 当前 `failures=2, errors=1`（test_smt_adversarial 的边界证伪，遗留 bug）

## Tech
- Python 3.10 · z3-solver · pydantic · sympy · numpy · pyyaml
- ⚠ 无 `requirements.txt` / `pyproject.toml` / `Dockerfile` / CI 配置（已知缺失）
- HW spec：`hw_config/{hw_params,modules,iq_config,pipeline_delays,rtovr_config}.yaml` + `specs/{system,units}/`
- 仿真器纯 Python（`sim/smc_simulator/`），通过 subprocess 调起；`sim/aiacc_hardware/specs` symlink → `hw_config/specs/`

## Glossary
详见 [docs/glossary.md](docs/glossary.md)。核心：SMC / UR / Bank / IQ / RtoVR / ADPC / MIR / LIR / II。

## Sub-agents
- `.claude/agents/hco.md` — AIACC 专属硬件契约审查；hw_config/、SMT axioms、ALU 延迟相关变更必经
- SOP 通用：`evaluator`（独立验收，反 self-eval bias）/ `appsec` / `db` / `ops` / `pm` / `ux`（横向关注点，按需调用）

## Decision routing

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

- ⚠ **smt-unit 2 fail + 1 error 的 root cause** —— `test_smt_adversarial.TestSMTRefutation` 的 II=16/17 边界证伪在 V2 引入前后即存在；尚未定位是 axiom 漏洞还是测试本身的数学下界推导错误
- ⚠ **HSTS 是否真死** —— `compactor.py` 注释提到 "Automated IQ Loop Stitching"，可能是 HSTS 的轻量后继；需读 compactor.py 确认
- ⚠ **UR 命名** —— hw_config yaml 写 "Unified Register"，但语义是 "User Register"（SRAM 实现）；统一时机未定
- ⚠ **依赖版本漂移风险** —— 无 requirements.txt，z3 / pydantic / sympy 版本未锁；CI 缺失也无法预警
- ⚠ **ADPC 版本散落** —— 源码注释里 V2.0 / V4.0 / V5.0 / V5.1 / V5.4 / V6.0 并存；统一版本号语义未定
- ⚠ **旧 ADR-001~006 still_holds 未审计** —— `docs/adm/adr/` 中的 5 个 ADR 是否仍成立，需要逐个标 yes/partial/no
- ⚠ **Bank R-R 物理语义精确定义** —— docs/adm/collaboration_guide.md 提到 "R-R 冲突的精确定义只有你知道"，建议落到 hco subagent 的 prompt 里

## Historical docs (V1/Gemini era)
`docs/{hco,adm,se,tfd,pvo,ig}/` 是 7-角色 Gemini 时代 docs。原 7 角色 → 现仅 HCO 升级为 sub-agent，其余被 SOP 覆盖（详见 docs/glossary.md 末段）。
- ✅ 可参考：`docs/hco/`（硬件约束语义） + `docs/adm/adr/ADR-001 ~ 006`（决策历史）
- 🪦 已过期：HSTS / AddrColoring / 11-Pass Pipeline / 六大公理 / Atomic-Composite 算子分类
- 新决策一律走 `docs/adr/ADR-NNN.md`，不再扩 7 角色目录
