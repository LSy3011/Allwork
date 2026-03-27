# AIACC V5.0 项目总索引

## 目录结构 = 角色分工

```
aiacc_v5_docs/
├── 00_index.md                    ← 你正在看的
│
├── hco/                           ← HCO 硬件契约官
│   ├── hw_changelog.md            变更日志
│   └── detail_hw_spec.md          硬件物理约束 + HardwareSpec (§1+§3)
│
├── adm/                           ← ADM 架构决策者
│   ├── baseline.md                架构基线（冻结后不改）
│   ├── roadmap.md                 25 里程碑速查
│   ├── detail_ir_pipeline.md      IR体系 + 11-Pass 流水线 (§2+§4+§5)
│   ├── detail_synthesis.md        SMT 六大公理 (§6)
│   ├── detail_hsts_hazard.md      HSTS 缝合 + UR冲突消解 (§7+§16)
│   ├── detail_topology.md         算子分类 + 模块依赖 (§8+§11+§13-§15)
│   └── adr/
│       └── ADR-001_rtovr.md       Rtovr 架构决策
│
├── tfd/                           ← TFD 测试先行者
│   ├── tdd_spec.md                函数级 TDD 规格
│   ├── verification.md            九大验证支柱
│   └── detail_guards.md           审计/守卫/溯源 (§9+§10+§12)
│
├── pvo/                           ← PVO 物理验证官
│   └── golden_baseline.md         Golden APB 基准
│
└── ig/                            ← IG 集成守护者
    └── gate_checklist.md          阶段门禁清单
```

## 原始文档 → 拆分映射

| 设计全书章节 | 拆分到 | 行数 |
|:--- |:--- |:--- |
| §1 硬件物理 + §3 HardwareSpec | `hco/detail_hw_spec.md` | ~95 行 |
| §2 目录 + §4 IR + §5 Pipeline | `adm/detail_ir_pipeline.md` | ~75 行 |
| §6 SMT 合成引擎 | `adm/detail_synthesis.md` | ~75 行 |
| §7 HSTS + §16 UR 冲突 | `adm/detail_hsts_hazard.md` | ~85 行 |
| §8+§11+§13-§15 拓扑与依赖 | `adm/detail_topology.md` | ~55 行 |
| §9+§10+§12 验证与审计 | `tfd/detail_guards.md` | ~55 行 |

**每个文件都 < 100 行，安全可编辑。**

## 角色流转

```
硬件变更 → [HCO] hw_changelog + hw_params.yaml
              → [ADM] adr/ADR-NNN + roadmap + detail_*
                  → [TFD] tdd_spec + detail_guards (红灯)
                      → [IE] 实现代码 (绿灯)
                          → [PVO] golden_baseline (验证)
                              → [IG] gate_checklist (放行)
```
