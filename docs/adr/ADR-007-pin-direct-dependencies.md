---
adr_id: ADR-007
title: 用 requirements.txt 锁定直接依赖版本
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-007 · 用 requirements.txt 锁定直接依赖

## Context

项目无 `requirements.txt` / `pyproject.toml` / `Pipfile`，5 个直接第三方依赖（`z3-solver` / `pydantic` / `sympy` / `numpy` / `PyYAML`）版本未锁定：

- 新机重装无标准命令，需查 import 逐个 `pip install`
- 旧机 CI 缺失（已知，ADR-未来）无法预警版本漂移
- 特别风险：`z3-solver` 主要版本之间 API 不兼容；`pydantic` v1→v2 是 breaking change；`numpy` 2.x 与 1.x 部分 dtype 行为不同

当前环境（已验证 main_verify.py 全模式通过）：
```
z3-solver==4.16.0.0
pydantic==2.13.3
sympy==1.14.0
numpy==2.2.6
PyYAML==6.0.3
```

## Decision

新建 `requirements.txt`，列**直接依赖固定版本（==）**，不列间接依赖：

```
z3-solver==4.16.0.0
pydantic==2.13.3
sympy==1.14.0
numpy==2.2.6
PyYAML==6.0.3
```

部署：`pip install -r requirements.txt`（Python 3.10）。

## Rejected

- **`pyproject.toml` + poetry / uv / pip-tools** ⚠
  - why：单人项目（CLAUDE.md.local "单 dev + AI"）+ 无团队同步压力，引入构建工具增加心智负担超过可重现性收益；待项目规模上升再迁
- **不锁版本（裸 `pip install z3 pydantic ...`）** ⚠
  - why：违反可重现性；z3 主版本跳变会撞 SMT axiom API（`solver.assert_and_track` / `unsat_core` 形式都可能动）
- **`pip freeze` 全量导出（含间接依赖）** ⚠
  - why：维护开销大——每次升级直接依赖都要重 freeze；间接依赖在 pip 解析中本来就有合理范围，固化反而限制安全 patch 升级
- **范围版本（`>=`）** ⚠
  - why：对 z3 / pydantic 这种 API 频繁变动的库，范围 = 隐性破坏；固定 == 更安全，升级时显式动版本号 + 重新验证

## Consequences

- ✅ 新机 1 行命令完成依赖安装
- ✅ 版本漂移风险被钉住，升级是显式动作
- ✅ HCO sub-agent 审查 hw_config 变更时可以默认依赖版本不漂
- ⚠ 间接依赖版本仍由 pip 决定，可能漂移；目前无观察到的具体问题
- ⚠ 当任一直接依赖升级时，必须重新跑 `--op horner` + `--mode smt-unit` + `--mode hw-parity` 验证后再固化新版本号到 requirements.txt
- 🔒 **binding**：
  - 升级直接依赖必须先在新分支跑完整验证矩阵，PR 同时改 requirements.txt 与代码（如 API 适配）
  - 若需要更严格的可重现性（间接依赖锁定），独立 ADR 决策迁 pip-tools 或 uv
