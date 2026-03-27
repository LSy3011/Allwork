# 阶段门禁检查清单 (IG 专属, 7 角色版)

> 每个 Phase 结束时逐项检查。全部打勾才能进入下一阶段。

## Phase 1 → Phase 2 门禁
- [ ] `HardwareSpec.load()` 能正确加载五源 YAML
- [ ] 非法 YAML 注入时抛出 `ValidationError`
- [ ] `make_mock_spec()` 基线与真实 YAML 一致
- [ ] **[SE]** `src/api/hw_spec.py` 接口与 `se/module_contracts.md` 一致
- [ ] 零 FAIL, 零 SKIP

## Phase 2 → Phase 3 门禁
- [ ] HIR→MIR→LIR 的 `trace_id` 全程一致
- [ ] LIROp 41-bit 编解码 bit-perfect
- [ ] **[SE]** `src/ir/` 目录结构与 `se/source_structure.md` 一致
- [ ] **[SE]** IR 模块无循环依赖
- [ ] 零 FAIL, 零 SKIP

## Phase 3 → Phase 4 门禁
- [ ] 六大公理单独测试，违反时 UNSAT，满足时 SAT
- [ ] `diagnose_unsat()` 能精确报告公理名
- [ ] sin 算子合成后通过 SMTOutputVerifier
- [ ] APB 缓存在 hw_spec 变更后自动失效
- [ ] **[SE]** `synthesis/` 不依赖 `orchestrator/` 或 `layout/`
- [ ] 零 FAIL, 零 SKIP

## Phase 4 → Phase 5 门禁
- [ ] R-R/W-W/Port 冲突判定 100% 正确
- [ ] Stitcher 自动错拍消解 R-R 冲突
- [ ] DLY 精确等于 `T_b - T_a - 1`
- [ ] StreamScanner 全量扫描 CLEAN
- [ ] **[SE]** Stitcher 的 `stitch()` 签名与 `module_contracts.md` 一致
- [ ] 零 FAIL, 零 SKIP

## Phase 5 → Phase 6 门禁
- [ ] 8 Bank 着色无冲突
- [ ] 循环 257 次正确拆分为两层嵌套
- [ ] 41-bit 二进制与仿真器参考 bit-perfect
- [ ] **[SE]** `driver/` 只依赖 `layout/` 输出，不直接读 `synthesis/`
- [ ] 零 FAIL, 零 SKIP

## Phase 6 发布门禁
- [ ] sin E2E bit-perfect
- [ ] atan2 E2E bit-perfect
- [ ] 全量 `pytest` 零 FAIL, 零 SKIP
- [ ] **[SE]** 模块依赖矩阵全部满足（无循环引用）
- [ ] 所有 ADR 的测试函数已实现并通过
