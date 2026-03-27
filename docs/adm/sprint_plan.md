# AIACC V5.0 敏捷开发计划

> Git 分支 = 进度把控。每个 Sprint 结束打 Tag，不达标不合入。

---

## 一、Git 分支策略

```
master (受保护)
  │ 只接受 Sprint 结束时的合入
  │
  ├── sprint/S01-hw-contract     ← Sprint 1
  ├── sprint/S02-ir-foundation   ← Sprint 2
  ├── sprint/S03-smt-core        ← Sprint 3
  ├── sprint/S04-hsts-stitch     ← Sprint 4
  ├── sprint/S05-layout-driver   ← Sprint 5
  └── sprint/S06-e2e-validation  ← Sprint 6
```

**规则**：
- `master` 永远可用，每次合入都能 `python3 build_report.py` 成功
- Sprint 分支内自由提交，Sprint 结束时 `git merge --no-ff` 合入 master
- 合入前必须通过 IG 门禁清单
- 每个 Sprint 结束打 Tag：`v5.0-S01`, `v5.0-S02`, ...

---

## 二、Sprint 计划

### Sprint 1: 硬件契约层 (目标: HardwareSpec 可用)
**时长**: 3-5 天 | **里程碑**: M01-M02

| 任务 | 角色 | 产出 | Git 提交规范 |
|:--- |:--- |:--- |:--- |
| 创建 hw_config/ 五源数据文件 | HCO | YAML/xlsx | [Done] |
| 实现 HardwareSpec.load() | IE | src/api/hw_spec.py | [Done] |
| 实现 Pydantic 校验 | IE | src/api/hw_spec.py | [Done] |
| 写测试 + Mock 工厂 | TFD | tests/conftest.py | [Done] |
| 验证 | PVO | 测试报告 | - |

**Sprint 1 Definition of Done**:
```bash
pytest tests/unit/test_hw_spec.py -v  # 全绿
git tag v5.0-S01
```

---

### Sprint 2: 三层 IR 基础 (目标: HIR→MIR→LIR 数据流通)
**时长**: 3-5 天 | **里程碑**: M03-M06

| 任务 | 角色 | 产出 | Git 提交规范 |
|:--- |:--- |:--- |:--- |
| 实现 IRNode + trace_id | IE | src/ir/base.py | [Done] |
| 实现 HIRProgram | IE | src/ir/hir.py | [Done] |
| 实现 MIROp + FootprintMask | IE | src/ir/mir/ | [Done] |
| 实现 LIROp + 41-bit 编解码 | IE | src/ir/lir/ | [Done] |
| 写各层测试 | TFD | tests/unit/test_ir.py | [Done] |

**Sprint 2 DoD**:
```bash
pytest tests/unit/test_ir.py -v        # 全绿
pytest tests/unit/test_lir.py -v       # 41-bit 编解码 bit-perfect
git tag v5.0-S02
```

---

### Sprint 3: SMT 合成引擎 (目标: sin 合成产出合法 APB) ★核心
**时长**: 5-7 天 | **里程碑**: M07-M11

| 任务 | 角色 | 产出 | Git 提交规范 |
|:--- |:--- |:--- |:--- |
| 六大公理实现 | IE | src/synthesis/axioms.py | `feat(smt): 六大公理` |
| Z3 Solver 驱动 | IE | src/synthesis/backend.py | `feat(smt): Z3 后端` |
| MIR→Z3 翻译 | IE | src/synthesis/constraints.py | `feat(smt): 约束翻译` |
| APB 缓存 | IE | src/synthesis/registry.py | `feat(smt): APB 缓存` |
| SMTOutputVerifier | IE | src/verification/ | `feat(verify): 公理回溯` |
| sin 模板 (大模型生成→冻结) | SE+IE | src/library/atomic/sin.py | `feat(lib): sin 模板` |
| 压力测试 | TFD | tests/unit/test_axioms.py | `test(smt): UNSAT 压力` |

**Sprint 3 DoD**:
```bash
pytest tests/unit/test_axioms.py -v     # 六大公理 SAT/UNSAT 正确
pytest tests/unit/test_synthesis.py -v  # sin APB 通过 SMTOutputVerifier
git tag v5.0-S03
```

---

### Sprint 4: HSTS 编排层 (目标: sin+cos 缝合无冲突)
**时长**: 5-7 天 | **里程碑**: M12-M15

| 任务 | 角色 | 产出 | Git 提交规范 |
|:--- |:--- |:--- |:--- |
| FootprintMask (Bank+Port+Unit) | IE | src/orchestrator/footprint.py | [Done] |
| AdvancedResTable | IE | src/orchestrator/resource.py | [Done] |
| Stitcher (Phase Shift + DLY) | IE | src/orchestrator/stitcher.py | [Done] |
| StreamScanner | IE | src/verification/stream_scanner.py | [Done] |
| ConstraintChecker | IE | src/verification/constraint_checker.py | [Done] |
| R-R/Port 冲突测试 | TFD | tests/unit/test_stitcher.py | [Done] |

**Sprint 4 Achievement (Finalized)**:
- [x] Conflict-free operator stitching on Single SMC.
- [x] Latch-aware slot de-allocation for extreme throughput (3 cycles/op).
- [x] Gapless HSTS Specification Export (.gapless.spec).

**Sprint 4 DoD**:
```bash
pytest tests/unit/test_stitcher_robust.py -v   # Phase Shift 正确 (3-Sin Verified)
git tag v5.0-S04
```

---

### Sprint 5: 空间与驱动层 (目标: Bank 着色 + 二进制打包) - **IN_PROGRESS**
**时长**: 3-5 天 | **里程碑**: M16-M19

| 任务 | 角色 | 产出 | Git 提交规范 |
|:--- |:--- |:--- |:--- |
| BalancedBankColorer (均衡着色) | IE | src/orchestrator/colorer.py | `feat(layout): 均衡着色` |
| 生命周期分析 | IE | src/layout/liveness.py | `feat(layout): 生命周期` |
| 8-Bank 地址映射与隔离 | IE | src/orchestrator/memory_allocator.py | `feat(layout): 地址映射` |
| LIR 41-bit 二进制指令打包 | IE | src/driver/emitter.py | `feat(driver): 二进制打包` |
| 着色+打包测试 | TFD | tests/unit/test_layout.py | `test(layout): 着色无冲突` |

**Sprint 5 DoD**:
```bash
pytest tests/unit/test_layout.py -v    # 着色满足 exclusions
pytest tests/unit/test_driver.py -v    # 41-bit bit-perfect
git tag v5.0-S05
```

---

### Sprint 6: 全链路集成 (目标: sin E2E Bit-Perfect) ★最终
**时长**: 5-7 天 | **里程碑**: M20-M25

| 任务 | 角色 | 产出 | Git 提交规范 |
|:--- |:--- |:--- |:--- |
| PassManager 11-Pass 级联 | IE | src/core/pipeline.py | `feat(core): PassManager` |
| 审计快照 | IE | src/core/audit.py | `feat(core): 审计导出` |
| 不变式守卫 | IE | src/core/checker.py | `feat(core): 不变式守卫` |
| OperatorRegistry | IE | src/api/registry.py | `feat(api): 算子注册` |
| sin E2E | IE | tests/integration/ | `feat(e2e): sin 全链路` |
| atan2 E2E | IE | tests/integration/ | `feat(e2e): atan2 全链路` |
| Bit-Perfect 验证 | PVO | 验证报告 | - |

**Sprint 6 DoD**:
```bash
pytest tests/integration/test_e2e.py -v  # sin + atan2 bit-perfect
pytest --tb=short                        # 全量 0 FAIL 0 SKIP
git tag v5.0-S06
git tag v5.0-release
```

---

## 三、进度追踪: Git Tag 里程碑

```
v5.0-S01  HardwareSpec 可用           ▓▓░░░░ 30%
v5.0-S02  三层 IR 打通                ▓▓▓▓░░ 60%
v5.0-S03  sin APB 合成成功            ▓▓▓░░░ 50%  ★关键里程碑
v5.0-S04  sin+cos 缝合无冲突          ▓▓▓▓░░ 70%
v5.0-S05  Bank 着色 + 二进制打包      ▓▓▓▓▓░ 85%
v5.0-S06  sin E2E Bit-Perfect        ▓▓▓▓▓▓ 100% ★发布
```

## 四、每日开发节奏

```
开始开发:
  git checkout -b sprint/S0N-xxx

每完成一个函数:
  pytest tests/unit/test_xxx.py::test_yyy -v
  git add . && git commit -m "feat(module): 函数名"

Sprint 结束:
  全量 pytest → 全绿
  检查 ig/gate_checklist.md → 全部打勾
  git checkout master && git merge --no-ff sprint/S0N-xxx
  git tag v5.0-S0N
  python3 build_report.py
```

## 五、风险管控

| 风险 | Sprint | 触发条件 | 应对 |
|:--- |:--- |:--- |:--- |
| Z3 安装失败 | S03 | `import z3` 报错 | 提前在 S01 验证环境 |
| sin 合成 UNSAT | S03 | MaxCycle 不够 | 逐步放大 MaxCycle |
| 缝合后仍有冲突 | S04 | StreamScanner 报错 | 检查 FootprintMask 维度 |
| 着色超过 8 Bank | S05 | Spill 触发 | 调整算子的 Bank 需求 |
| E2E 不一致 | S06 | bit 不对 | 用 audit 快照逐 Pass 定位 |
