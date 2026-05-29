---
adr_id: ADR-021
title: SHIFTUP→RTOVR worst-case fold 是 bug 而非 over-approximation（删 3 处 fold → fma 32/32 bit-perfect）
decided: 2026-05-29
last_verified: 2026-05-29
still_holds: proposed
supersedes_decision_in: [ADR-020, ADR-018-D3.b]
---

# ADR-021 · 删除 SHIFTUP→RTOVR worst-case fold（fold-is-bug）

> **状态：Proposed → hco Approved（待落代码 + evaluator + 全回归升 Accepted）** —— 决策 + 实测证据已锁（本 session Y'-spike 实测）。hco 全新 context 独立复核（agentId `a662f05e`）**亲手复现 32/32 bit-perfect + Synthesis-failed 假象 + bank artifact + 非 chain 不破**，同意升 Accepted，无硬 blocker；补充见 Decision 项 6 + §元教训契约层 + §风险 matmul_parallel_n。
>
> **一句话**：`lat_p += smc_id_en`（SHIFTUP→RTOVR 边的 chain worst-case fold）不是 ADR-018 D3.b/ADR-020 历次假设的 "safe over-approximation"，而是**纯 bug** —— 它把 sim 的**物理 chain 传播延迟**错误地塞进 **SMT 逻辑调度**，导致所有 SMC 的 RTOVR 取错 iteration。删掉它（共 3 处：axiom 1 + verifier 2），SMT 按真实 unit-latency 排程，emitter 的 per-SMC INIT shift（ADR-020 v6-4 已实现）补偿物理 chain 延迟 → **fma_parallel_n 32/32 SMC bit-perfect**（实测，3 次稳定）。

## Context

### v8 起点与本 ADR 的颠覆性

ADR-020 v6 三审（commit `2d0178c`）把 v8 起点定为 **Y' 路径**（SHIFTUP→RTOVR 单边 deterministic per-SMC t_var + emitter 后处理）。本 session 执行 v8 前置 Y'-spike 时，证据链逐步推翻了整条 fold-based 假设：

1. **死 axiom**：Y'-spike 的 gate（`add_iq_latch_protect_axiom` 不 UNSAT）建立在一个 zero-call 的死 axiom 上（ADR-013 墓地漏网者，`axioms/__init__.py:53-54` 还是无限递归 wrapper）。gate 形同虚设。
2. **fold 在 3 处不是 1 处**：Y'/B/X 历次 prescription 只列了 `dependency_axioms.py` 一处 fold。实测：counter-verifier 还藏了两处（`ucode_solver.py:235-238` + `:267-273`）。只删 axiom 一处 → counter-verifier 仍用 `lat=32`、solver 解 `lat=1` → `[VERIFIER-FAIL] RAW Violation` → 无论 II 多大都 `Synthesis failed`。
3. **去 fold → 32/32**：3 处 fold 同删后，fma_parallel_n e2e **全部 32 SMC bit-perfect**（详见 §实测证据）。

第 2 点是历史关键：**8 个 commit + 三轮 HCO 裁定从未完整列出 fold 的三处副本**。任何"试删 axiom fold"的尝试都会撞上 counter-verifier 的另两处 → `Synthesis failed` → 误判"去 fold 不可行" → 转去 emitter 层绕路（v6-4）。本 session 是第一次 3 处同删 + 看 e2e。

### 物理 root cause（重定位）

- fma_parallel_n 的 `a` 通路：`a_read → SHIFTUP(broadcast,chain) → RTOVR → FPMUL`。SHIFTUP 把 `a` 沿 chain 广播到 32 个 SMC，SMC_k 的 `a` 物理上晚 `k+1` cycle 到达本 SMC 的 RTOVR。
- `b/c` 通路 partitioned，无 chain，延迟 0。
- **fold 的错误**：`dependency_axioms.py:64-68` 对 SHIFTUP producer 做 `lat_p += smc_id_en(=31)`，把 SHIFTUP→RTOVR 边权 fold 成 worst-case 32。这等于在 **SMT 逻辑调度层**断言"RTOVR 必须等 32 cycle 才采样"。但 chain 传播延迟是 **sim 的物理行为**（数据真的逐 SMC 流过去），不该由 SMT schedule 建模。
- 后果：SMT 把所有 SMC 的 `rt_a` 排到 `abs_t≈33`。sim 中 RTOVR 无 hold（`sim/.../rto_vr.py:32-33,45-50` 每 cycle 清零重锁存），II=1 每 cycle SHIFTUP.dr_d 更新。RTOVR @ cycle 33 锁存的是 iter 31 的 `a`（不是 iter 0）。SMC_0 FPMUL 收到 `iter31_a × iter0_b` → 错位 31 iter → 0/32。

### 正确的对齐机制

- **SMT 删 fold**：按真实 unit-latency 排（SHIFTUP unit lat=1，见 `hw_config/pipeline_delays.yaml`）→ `rt_a` 排到 `abs_t≈2`（SMC_0 视角紧凑）。
- **emitter per-SMC INIT shift**（ADR-020 v6-4 `_compute_per_smc_stagger` + `_emit_multi_smc`，commit `7187654` 已实现）：SMC_k 整 IQ stream 晚 `k` cycle 启动 BODY，**补偿 chain 物理传播的 `k` cycle 延迟**。
- 两者配合：每个 SMC_k 的"BODY 启动时机"与"`a` 数据到达时机"对齐 → 32/32。**v6-4 做对了一半（emitter），缺的一半正是"删 SMT fold"**；v6-4 因保留 fold（SMT 仍 SMC_31 视角）+ emitter 再叠 shift = 双重延迟，所以 partial（chain 串接 verified 但数值 0/32）。

## 实测证据（本 session Y'-spike，throwaway，已 git restore）

spike = 注释 3 处 fold（`dependency_axioms.py:68` + `ucode_solver.py:238` + `:273`），其余不动（emitter v6-4 stagger 保留）。

| 验证项 | 结果 | 证据 |
|---|---|---|
| **fma_parallel_n 32-SMC e2e** | `PASSED y: All 32 SMC outputs bit-perfect (atol=0.005)`，**3 次重跑稳定** | sim 逐元素打印 `[SMC_0] Golden=[19.078,6.649,6.394] \| SMC=[19.078,6.649,6.394]`（完全相等）；SMC_0 sim 从 baseline 错值 `[12.26,6.50,19.39]` 修正为 golden ref |
| **SMT II** | II=1 SAT and Verified（counter-verification 通过，II 不漂；删 fold 后 `rt_a` t 33→2） | |
| **horner_v3 e2e**（非 chain） | `ALL PHYSICAL CHECKS PASSED`（含 Bank Audit SUCCESS、Truth Audit SUCCESS） | 删 fold 对非 chain 算子零影响（无 SHIFTUP，fold 分支不触发） |
| **smt-unit** adversarial | II=17/9/13 全 `SAT and Verified`（同 baseline） | R3/R4/R5 反证不破 |
| **pytest** | 69 pass + 1 skip + **1 fail** | 唯一 fail = `test_d3b_chain_raw_axiom_extends_shiftup_latency`，它**专门断言 fold 存在** → 守护的是被本 ADR 认定为 bug 的行为，需随 fix 更新 |

### 一个独立的假阳性（不影响数值，handoff bug #4 同源）

去 fold 后 truth audit 仍报 `FAILED`，但**与数值无关**，是 handoff bug #4 的 auditor 两半假阳性（数值层 `PASSED y` 32/32 已由 golden 比对独立确证）：
- **(a) bank 假阳性**：`audit_bank_conflicts` 的 regex（`auditor.py`）**不捕获 `SMC_ID`**，把 32 个 SMC 的 broadcast read 按 cycle 合并 → `a`（broadcast，全 Bank 0）各 SMC 因 stagger 处于不同 iter（不同 addr）→ 永远误判 collision。**clean baseline 也报 279 collisions**。
- **(b) rigid audit 假阳性**：`_evaluate_expected_values` 主循环迭代求值用 `env.get(dep, 0.0)`，依赖 `for op in ops` 遍历顺序。ops 非拓扑序时，RTOVR/下游节点在其 dep 尚未入 env 时取到 0.0 → expected 假值（`rt_a Exp=0`；`rt_inl_2=0` 连累 `y_0_a1=c+0`、`out_obj` 错值）→ 4 个 RTOVR/output 节点假阳性 FAIL。

两半均为纯 auditor 端 bug，与数值/硬件无关。**本 PR 两半都修**（见 Decision 4），修后 fma_parallel_n e2e `exit=0` / `ALL PHYSICAL CHECKS PASSED`。

## Decision

1. **删除 3 处 SHIFTUP/SHIFTDN worst-case fold**（`lat_p/lat += smc_id_en`）：
   - `src/synthesis/axioms/dependency_axioms.py:64-68`（axiom，RTOVR 增广边）
   - `src/synthesis/ucode_solver.py:235-238`（validate_physical_schedule 维度6 RAW）
   - `src/synthesis/ucode_solver.py:267-273`（维度7 RTOVR undersampling）
   三处必须**同步删除**，否则 counter-verifier 与 solver 不一致 → `Synthesis failed`。
2. **保留 emitter v6-4 per-SMC INIT shift**（`_compute_per_smc_stagger` + `_emit_multi_smc`，commit `7187654`）—— 它是物理 chain 延迟补偿，是正确对齐机制的另一半。
3. **更新 `test_d3b_chain_raw_axiom_extends_shiftup_latency`**：它断言 fold 行为，需改为断言"删 fold 后 SHIFTUP→RTOVR 边权 = unit lat"（或删除该测试并新增 fma 32/32 bit-perfect 集成回归）。
4. **修 handoff bug #4 auditor 两半假阳性**（让 truth audit 反映真实数值 + e2e 退出码绿；独立于 fold，但同一验收路径必须一起修）：
   - **(a) bank 冲突按 `SMC_ID` 隔离**：`auditor.py` bank regex 捕获 `SMC_ID`，`cycle_reads` key 改为 `(cyc, smc_id)`，broadcast read 不再跨 SMC 误判（279 假阳性 → 0）。
   - **(b) rigid audit expected 拓扑序求值**：`_evaluate_expected_values` 主循环前按 `depends_on` 拓扑排序（Kahn's），确保 producer 先于 consumer 求值，消除 RTOVR/下游节点 `env.get(dep,0.0)` 取到未算 0.0 的假值。
   两半修后 fma_parallel_n e2e `exit=0`、`ALL PHYSICAL CHECKS PASSED`（回归 horner/tanh7/silu_taylor/matmul 全 exit=0，拓扑序对正确 case 无害）。
5. **订正 `dependency_axioms.py:53-63` P1 注释**：v6-2 P1 把 fold 描述为 "safe over-approximation"，现订正为 "fold 是 bug，已删除；SHIFTUP→RTOVR 边权 = unit lat；chain 物理延迟由 emitter per-SMC INIT shift 补偿"。
6. **（独立清理项，留独立小 PR — 不在本 PR）死 axiom `add_iq_latch_protect_axiom`**：该 axiom 零真实调用点（ADR-013 墓地漏网者），却仍在 `axioms/__init__.py` 的别名(:14)/wrapper(:52-54)/`__all__`(:68) + `physical_axioms.py:69-110` 留死 def。v8 Y'-spike 的 gate 曾误建立在它之上。
   - **纠正 hco 复核的一处误判**：hco 称 `__init__.py:53-54` 是 `def f(*a,**k): return f(*a,**k)` 无限递归 wrapper。实测不成立 —— 函数体内的 `add_iq_latch_protect_axiom` 解析到**模块级别名** `:14`（指向真 def），与 `:29-30 add_latency_axiom` 等 6 个 active wrapper **同一模式**；`add_latency_axiom` 用同模式且 active 工作正常 → 证明该模式不递归。它只是 **dead（零调用）**，不是 bug。
   - **决定不在本 PR 删**：死 axiom 清理与 fold 核心修复无逻辑关联（反 scope leak），且不影响 fma 32/32。留独立小 PR 处理。本 ADR 在此记录 + CLAUDE.md "Known unknowns" 应更正 ADR-013 表述为 "7 active + 1 dead export（latch_protect）"，确保下个 reviewer 有记录不再被它误导。

## Supersedes / Reverses

- **ADR-020 全部 v5/v5.1/v6 修复机制**：方向 B（emitter per-SMC stagger 作为唯一修法）、OQ#1 路径 A（emitter 后处理 + P1/P2）、v6-4 D.1'、v6 三审 **Y' adoption**、HCO 三审 4+1 裁定（V/W/X/Y/Y'）—— 全部建立在"fold 是对的、问题在别处"的错误前提上，**作废**。ADR-020 主诊断（"SHIFTUP→RTOVR 跨 SMC 数据流需补偿"）方向正确，但补偿机制不是 SMT fold/emitter stagger 单方，而是"删 SMT fold + emitter INIT shift"。ADR-020 frontmatter `still_holds` 改为 `superseded`。
- **ADR-018 D3.b**：`lat_p += smc_id_en` chain extra latency 的引入是错误建模，加 errata 指向本 ADR。
- **HCO 三轮裁定**（一审 reject B / 二审 D.1' / 三审 Y'）：HCO 在两轮复核中已自我推翻 Y'（agentId `aba741f5`、`a85b86a1`，记录于本 session）。本 ADR 是最终定论：问题不在"如何实现 per-SMC 延迟"（B/X/Y' 之争），而在"fold 这条约束本身不该存在"。

## Consequences

### 正面
- fma_parallel_n 32/32 bit-perfect（v8 主验收点达成），fix 范围 = 删 3 处 fold + 4 项配套，**非 7 文件全模型重构 / 非架构缺口**。
- 推翻了"ADR-018 D5.b/c lowering 架构对 broadcast-chain⊗partitioned 类算子表达力不足"的架构级悲观结论（HCO 重裁 §D）—— 现有架构足够，只是 fold 这条约束错了。
- 删 fold 后 SMT 求解更紧凑（II 不升反降；`rt_a` t 33→2），无负面性能影响。

### 风险 / 待回归
- **其他 chain 算子**：删 fold 影响所有 `SHIFTUP/SHIFTDN + smc_id_en>0` 的算子。hco 复核扫 `verification_v2/ops/` 确认 fma 是当前唯一**活** chain 算子。⚠ `matmul_parallel_n.yaml` 也含 broadcast/SHIFTUP/smc_id_en，但其 header 明写当前无可运行 operator（`ManualGemmParallelN` + SHIFTUP 注入未实现，到不了 scheduling）→ 不受本 fix 影响。**但它是"fold 删除后第一个须验的新 chain 算子"** —— 将来若接上 `ManualGemmParallelN`，必须重跑本审查。
- **smt-unit / pytest 守护**：`test_d3b_chain...` 必须更新（否则红）；新增 fma 32/32 集成回归锚点防未来回归。
- **bank artifact 修复的副作用**：修 `auditor.py` smc_id 隔离须确认不影响单 SMC 算子（horner 已 SUCCESS，单 SMC 无合并问题）。

## 元教训（为什么 8 commit + 3 轮 HCO 没发现）

1. **fold 的三处副本从未被完整枚举**。axiom 与 verifier 必须语义一致（ADR-009 修 Bank 冲突时也踩过 axiom/verifier 双处），但本次 fold 的 verifier 两处（`ucode_solver.py:235/270`）在历次 prescription 中全被漏掉。删单处即 `Synthesis failed`，制造了"去 fold 不可行"的假象。
2. **历史假设被层层继承未被实测挑战**。ADR-018 D3.b 引入 fold 时标注 "safe over-approximation"，此后 v1→v6 + 三轮 HCO 全部接受此前提，在其上发明 emitter stagger / Y' / B / X / 架构缺口论。**没有人直接跑过"删 fold + 看 e2e 32 SMC"**，因为 §元教训#1 的 `Synthesis failed` 假象挡住了。
3. **正向命题优先于方案辩论**。本 session 前半段陷入"Y'/B/X 哪个方案对"的可行性论证，被 user 纠偏"偏离了跑通 32 SMC"后，回到直接拿"删 fold 能否跑通"的事实，10 分钟得出 32/32。教训：面对"修不好的 bug"，先用最小实验验证最根本假设（这里是"fold 是否必要"），而非在既有方案空间里穷举。
4. **（hco 复核补充，契约层结构教训）axiom 与 verifier 不应各持一份硬件常量副本**。三轮 HCO 全错的更深根因：fold 这条约束散落在 axiom（dependency_axioms）+ counter-verifier（ucode_solver 两处）共三处，无单一 source-of-truth，无机制保证同步 —— ADR-009 修 Bank 冲突时已踩过同源坑（axiom/verifier 双处）。本 PR 删 fold 后这 3 处副本一并消失（fold 不再存在，无常量需同步），故本次无需立即抽象。但作为**预防性建议**记录：未来任何 axiom 与 verifier 共享的硬件约束常量（边权 / 延迟 / 资源上限），应抽成单一 helper/常量双方引用，而非靠人工枚举副本。此 pattern 已出现 2 次（ADR-009 + 本 ADR），第 3 次出现时应执行抽象（不提前抽象）。

## References

- 本 session Y'-spike 实测日志：`/tmp/spike_e2e.log`（fma 32/32 + golden==smc 逐元素）
- HCO 两轮重裁：agentId `aba741f5`（死 axiom + Option-1/2 二分）、`a85b86a1`（推翻自己的 Y' 采纳，承认基于未验证的 RTOVR-hold 假设）
- sim 模型调研：agentId `aa9f5d6`（RTOVR 无 hold + FPMUL 同拍双读 + bank 279 artifact）
- 删除目标：`src/synthesis/axioms/dependency_axioms.py:64-68` + `src/synthesis/ucode_solver.py:235-238` + `:267-273`
- 物理依据：`sim/smc_simulator/datapath/rto_vr.py:32-33,45-50`（RTOVR 无 hold）、`sim/smc_simulator/core/kernel.py:444-450`（FPMUL 同拍双读）
- emitter 对齐机制：`src/driver/emitter.py:161-195`（_emit_multi_smc）+ `:241-363`（_compute_per_smc_stagger）
- auditor bug #4：`verification_v2/infra/auditor.py:512-513,530-565`
- 待更新测试：`tests/unit/test_multi_smc_basic.py::test_d3b_chain_raw_axiom_extends_shiftup_latency`
- 死 axiom（独立清理项）：`src/synthesis/axioms/physical_axioms.py:69-110` + `axioms/__init__.py:53-54,68`
