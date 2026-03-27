# AIACC V5.0 项目总索引

## 目录结构 = 角色分工 (7 个角色)

```
docs/
├── 00_index.md                    ← 你正在看的
│
├── hco/                           ← HCO 硬件契约官
│   ├── hw_changelog.md            变更日志
│   └── detail_hw_spec.md          硬件物理约束 + HardwareSpec
│
├── adm/                           ← ADM 架构决策者
│   ├── baseline.md                架构基线（冻结后不改）
│   ├── roadmap.md                 25 里程碑速查
│   ├── detail_ir_pipeline.md      IR体系 + 11-Pass 流水线
│   ├── detail_synthesis.md        SMT 六大公理
│   ├── detail_hsts_hazard.md      HSTS 缝合 + UR冲突消解
│   ├── detail_topology.md         算子分类 + 模块依赖
│   └── adr/
│       └── ADR-001_rtovr.md       Rtovr 架构决策
│
├── se/                            ← SE 软件工程师 [NEW]
│   ├── source_structure.md        源码全景文件树 + 目录规范
│   └── module_contracts.md        模块接口契约 (输入/输出定义)
│
├── tfd/                           ← TFD 测试先行者
│   ├── tdd_spec.md                函数级 TDD 规格
│   ├── verification.md            九大验证支柱
│   ├── detail_guards.md           审计/守卫/溯源
│   ├── phase1_hw_contract.md      M01-M02
│   ├── phase2_ir.md               M03-M06
│   ├── phase3_synthesis.md        M07-M11
│   ├── phase4_orchestrator.md     M12-M15
│   ├── phase5_layout_driver.md    M16-M19
│   └── phase6_integration.md      M20-M25
│
├── pvo/                           ← PVO 物理验证官
│   └── golden_baseline.md         Golden APB 基准
│
└── ig/                            ← IG 集成守护者
    └── gate_checklist.md          阶段门禁清单
```

## 7 角色流转 (含 SE)

```
硬件变更 → [HCO] hw_changelog + hw_params.yaml
              → [ADM] adr/ADR-NNN + roadmap
                  → [SE] source_structure + module_contracts
                      → [TFD] tdd_spec + phase* (红灯)
                          → [IE] 实现代码 (绿灯)
                              → [PVO] golden_baseline (验证)
                                  → [IG] gate_checklist (放行)
```

## 角色职责速查

| 角色 | 回答什么问题 | 专属目录 |
|:--- |:--- |:--- |
| HCO | 硬件参数是什么？ | `hco/` + `hw_config/` |
| ADM | 架构是什么？怎么演进？ | `adm/` |
| **SE** | **代码怎么组织？接口怎么定义？** | **`se/`** |
| TFD | 怎么测试？ | `tfd/` |
| IE | 怎么实现？ | `src/` (代码) |
| PVO | 结果对不对？ | `pvo/` |
| IG | 能不能放行？ | `ig/` |
