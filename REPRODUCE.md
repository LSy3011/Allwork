# ADR-056 V1 复现

基线标签：`adr056-v1-closed`，指向 `bfcff47`（包含 `7f92cb1` 验收实现；后续提交只补本文与 A2a 设计）。历史验收见 `verification_v2/artifacts/v1_step5_scale_sum_acceptance_20260930/README.md`。

## 环境

- 本机 Python 3.10.20；依赖固定于 `requirements.txt`。可在独立 checkout 中创建 `.venv` 并安装这些依赖。
- VCS、许可证以及 `/home/juno1023/u280_sim_v422` 沙箱为外部依赖，不随 Git 标签恢复。环境入口为 `vcs-env.sh`；使用前核对本机工具安装及许可配置。
- 必须恢复匹配的 RTL、TB 探针、runner 和 simv；逐项核对历史 `run_report.json` 的 provenance。仅有 Python 依赖不能复现 RTL 验收。
- 本文提供复现步骤，不表示本次已重跑 VCS。

## 从仓库根目录执行

```bash
.venv/bin/python3 -m pytest tests/ -q
source vcs-env.sh
verification_v2/tools/run_v1_formula_flow.sh verification_v2/ops/scale_sum.yaml --n-rows 4,8,60 --domain-mode v1_admission
verification_v2/tools/run_v1_formula_flow.sh verification_v2/ops/scale_sum.yaml --n-rows 240 --domain-mode v1_admission
verification_v2/tools/run_v1_formula_flow.sh verification_v2/ops/scale_sum.yaml --n-rows 244 --domain-mode v1_admission
```

历史预期：前两次验证 GREEN 并发布；240 的束缚队列实际为 128 行；244 返回非零及 `STATIC_ADMISSION_REJECTED`，不发布、不改变旧包。最后一条是预期失败，请单独检查报告，勿当作运行成功。测试历史基线为 349 passed / 7 skipped / 11 xfailed / 148 subtests，变动须解释。

回归通道分别执行：

```bash
verification_v2/tools/run_v1_formula_flow.sh verification_v2/ops/add.yaml --n-rows 4,8,60 --domain-mode frozen_regression
verification_v2/tools/run_v1_formula_flow.sh verification_v2/ops/add_v1.yaml --n-rows 4,8,60 --domain-mode v1_admission
```

无 VCS 时可加 `--no-run-vcs` 检查前半链，结果必须是 UNVERIFIED，不是验收成功，不发布。

## 证据与恢复

保留 `verification_v2/artifacts/`、`build/v1_runs/`（发布包默认位于 `verification_v2/artifacts/releases/`），并保存编译器 Git bundle 与外部沙箱快照/可恢复来源。README 索引不等于其引用的全部证据。新运行使用独立目录，不覆盖冻结文件。

备份归档计算 SHA256；在独立目录解包并逐文件比对内容。异机备份目的地待确认；同盘副本仅用于暂存，不算灾备完成。VCS 证据不得记作上板证据；scale_sum 的完整事件验收范围是单 SMC、II=2、单 stage、无反压展开。flat ISSUE 仍有独立债务。
