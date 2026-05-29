---
adr_id: ADR-020
title: SHIFTUP 多 SMC bit-perfect 收尾（v5：hco 复核选 B 方向 → emitter per-SMC stagger + ADR-018 D3.b worst-case fold 订正）
decided: 2026-05-28
last_verified: 2026-05-28
still_holds: partial
---

# ADR-020 · SHIFTUP 多 SMC bit-perfect 收尾

> **状态：Accepted (v4, partial-fix)** —— bug #1/#2 已修并 commit；bug #3 cycle-level root cause 已锁定（本 session）但代码未修；bug #4 仍未触碰
>
> **演进**：
> - v1（同日早）：决策"改 iq_unit.py body loop，让 SHIFTUP 每 SMC fire ≥32 次/SMC（修法 A）"
> - v1 hco 预审：**Reject-with-major-rework**（修法 A 与 `shiftup.yaml:55 iq_slot_consumption` 字面矛盾；fire=32 数值巧合非语义对；根因可能在 driver 非 sim）
> - evidence-gathering 三步（同日下午）：实测**否定 v1 假设**，揭示假"真根因方向"（见 §Context Evidence v1/v2）
> - **v2（同日下午）**：决策从 "iq_unit.py 状态机" 转向 "SHIFTUP chain 数据流时序"；H1/H2/H3/H4 待 cycle-level 调试
> - **v3（同日晚 prior session）**：drill-down dump 后 **H1/H2/H3/H4 + H5/H6/H7 全排除**；真根因裂为 4 层独立 bug（input_data 双源 / Auditor parser 缺 smc_id / sim 真数值错 / expected_val_map trace_id 映射错）；bug #1/#2 修，bug #3/#4 留 v4
> - **v4（同日晚 prior turn）**：bug #3 cycle-level dump 锁定 → **compiler axiom vs sim 实现语义不一致**：`dependency_axioms.py:56-60` SHIFTUP producer `lat_p += smc_id_en` 假设 chain 提供 N-cycle 缓冲；`shift_up_unit.py` 实现为 1-stage register（无 buffer）→ SMC_0 chain head 收到 a 的 iter 偏移 +(smc_num-1)=+31
> - **v5（同日晚 prior session）**：hco subagent 独立复核 4 方向 → **reject A/C/D，选 B**（emitter per-SMC stagger）。关键判读重定位 v4 误判：**spec yaml 是对的**（SHIFTUP 单拍 chain register 无 N-stage buffer 语义），**sim 是对的**（严格按 spec），**真错在 axiom 实现**（`dependency_axioms.py:56-60` 把 ADR-018 D3.b per-SMC RAW 公式 fold 成单 t_var worst-case）。hco 另揭 v4 未充分认识的：**SMC_1..30 chain 中段全部错位**（不只 SMC_0），方向 D 被彻底否决
> - **v5.1（同日晚 prior session）**：OQ #3 解（"差 2 cycle"来源 = base lat 边）+ B stagger 精确公式（`delta_k = (smc_id_en - k) × chain_stage_latency = 31 - k`）+ 链式传播范围审定（a 通路全 stagger / b/c partitioned 配套提前），代码全未动
> - **v6（本版，2026-05-28 同日下一 session）**：hco subagent 独立复核 §OQ #1 选型（emitter 后处理 vs SMT 重算）→ **选 A 路径（emitter 后处理）+ 2 项硬性补丁**：P1（fold 公式注释订正语义，消除 "misleading 32" audit 误读）+ P2（emitter post-stagger 后必须复跑 ResourceAxioms 做 per-SMC verifier，否则 hex 层资源冲突 SMT 不知）。counter-evidence spike 测求解器代价（HCO 信心最低点）→ Δ ≈ 0%，理由 #3 弱化但不改选型（理由 #1 抽象层切分 / #2 已 per-SMC axiom 不重复造轮子 更根本）。本版状态 **Proposed**（决策证据已锁，实现未落）

## Context

### 触发

[ADR-018](ADR-018-multi-smc-scheduling-design.md) Stage 1 七子任务（commit `52babc2`）完成后，`fma_parallel_n` 32-SMC 验证：
- ✅ SMC_0 bit-perfect
- ❌ SMC_1..31 全 fail（1/32 match）

### Evidence (2026-05-28 evidence-gathering)

#### E1 · SHIFTUP IQ stream（hex dump 实测）

```
SMC_0/1/31 iq_shiftup.hex 三行 byte-完全相同：
  line 0: 00000800000000000000000000000000000000000000
  line 1: 88000000000000000000000000000000000000000000
  line 2: 80200000007e00000000000000000000000000000001  ← VLD=1 仅此一行
```

**含义**：每 SMC SHIFTUP IQ 只 fire 1 次（line 2 唯一 VLD=1）—— 与 `hw_config/specs/units/shiftup.yaml:55` `iq_slot_consumption: "OQ-8 (i) 同步发射 t=t0 一次性"` 一致。**这是 spec-correct 不是 bug**。
→ **直接否定 v1 修法 A 的核心假设**（"fire ≥32 次/SMC"）

#### E2 · OQ-8 (i) 原始决议复核（ADR-018 v7 line 134/149/153）

- D3.e (IQ 同步发射) 与 D3.b (chain 数据 RAW 延迟) 是**两个独立 axiom**
- IQ slot 一次性消耗（t=t0）；chain 数据在物理上以 `chain_stage_latency=1` cycle/stage 沿链传播
- v1 草稿误把"IQ 同步发射"读成"持续 fire"

#### E3 · ur_data per-SMC inject（emitter 行为实测）

| 文件 | SMC_0 | SMC_1 | SMC_31 |
|---|---|---|---|
| `ur_data/bank0.hex` line 0 | `3f88764f...0630fec` (a 值) | 全 0 | 全 0 |
| `ur_data/bank1.hex` line 0 | `407ac049...4088193d` (b/c) | `3f8166f1...404b2b02` (差异) | (其他差异) |

- bank0 仅 SMC_0 有 data —— 符合 spec（SMC_k>0 用 `cru_shiftup_i` 不用 local_data）
- bank1 per-SMC 不同 —— b[k]/c[k] 正确 per-SMC 注入

#### E4 · per-SMC compare 框架（physical_runner 实测）

- `sim/smc_simulator/tools/physical_runner.py:299 _verify_multi_smc_per_smc` 真正工作
- 实测最终输出：
  ```
  FAILED y: 1/32 SMCs match.
  SMC_1 Max Diff=11.262639999389648
  sim[0..2] = [4.272484, 3.506026, 4.836310]
  ref[0..2] = [15.535124, 13.361784, 9.798473]
  ```
- `expected_values.json = []` 只是 `golden_res["intermediates"]` 占位，**不参与** per-SMC compare（用的是 `golden_data.npz.y` shape (32, n_rows)）
- ratio sim/ref ≈ 0.275（非零非全偏），暗示**部分** chain 数据传到但**值不对**或时序错位

#### E5 · emitter `_zero_broadcast_data_for_followers` 实际行为

- `emitter.py:231-256` 注释承诺 SMC_k>0 SHIFTUP data 字段清 0
- 但实测三 SMC `iq_shiftup.hex` byte-完全相同 —— 清零**没生效**
- 推测：payload dict 实际 key 不是 'data'/'DATA'，函数 silent no-op
- **对 SMC_k>0 数值正确性可能无害**（SHIFTUP unit `shift_up_unit.py:60` 在 SMC_k>0 走 `chain_in` 不走 `local_data`），但**暴露 emitter 函数 silent-failure 模式**

### 真根因方向

evidence 强烈指向 **SHIFTUP chain 数据流时序错位** 而非 IQ stream / fire 次数：

| Hypothesis | 描述 | 优先级 |
|---|---|---|
| H1 | SHIFTUP chain 数据流时序错位：`cluster._propagate_chain_inputs` 1-cycle/stage delay vs sim 内 SMC_k SHIFTUP unit 消费 `cru_shiftup_i` 的 cycle 不一致；SHIFTUP IQ 在 t=t0 同步 fire 但 `cru_shiftup_i` 在 t=t0+k cycle 才到位 → SMC_k 在 t=t0 消费 chain 时读到 stale/0 | **P0** |
| H2 | D3.b chain RAW axiom (ADR-018 v7) 未正确建模 chain_stage_latency；SMT 调度算的 RtoVR 消费 `DR_SHIFTUP_D` cycle 与 sim 实际 expose cycle 不一致 | P1 |
| H3 | `shift_up_unit.py:64-71` DR_SHIFTUP_D expose 时机错（broadcast 模式下 SMC_k 何时把 `_next_cru_o` 暴露给 RtoVR）| P1 |
| H4 | emitter `_zero_broadcast_data_for_followers` silent no-op：SMC_k>0 data 字段未真清零，导致打包出的 hex line 2 包含本不该有的 SMC_0 data 字段值 | P2（暂判对 SMC_k>0 数值无害，但需修） |

## Decision

### D1 · 范围

本 ADR-020 v2 目标：**`fma_parallel_n` 32/32 SMC bit-perfect**。

阻断 1（本 ADR）：SHIFTUP chain 数据流时序对齐 + 真根因下针。
阻断 2 / 3（commit `52babc2`）：移交后续 ADR。

### D2 · 实施流程（**先调试再下针**，不再 commit-then-investigate）

#### D2.a 加 cycle-level 调试打印（一次性 scaffold，验完即拆）

在 `sim/smc_simulator/units/shift_up_unit.py` 加临时打印：
```python
# evaluate() 末尾
print(f"[SHIFTUP-CYCLE] cycle={cycle} smc={smc_id} "
      f"chain_in={chain_in:#034x} local_data={local_data:#034x} "
      f"VALID={instr.VALID} smc_id_en={smc_id_en} broadcast={broadcast} "
      f"next_cru_o={self._next_cru_o:#034x} next_dr_d={self._next_dr_d:#034x}")
```

跑 `python3 verification_v2/main_verify.py --op fma_parallel_n`，dump 前 50 cycle 的 SHIFTUP-CYCLE 日志，对照：
- SMC_0 cycle 何时 fire VALID + cru_o 何时 expose
- SMC_1 cycle 何时收到 chain_in（应在 SMC_0 cru_o tick 完成的下一 cycle）
- SMC_1 cycle 何时 fire VALID + dr_d 何时 expose
- RtoVR 消费 DR_SHIFTUP_D 的 cycle（看 SMC_1 timeline rtovr-3 起始 cycle 35）

#### D2.b 根因定位

D2.a 数据出来后，匹配 H1/H2/H3/H4 之一：
- H1 命中：SMC_1 chain_in 到位 cycle > SMC_1 rtovr-3 消费 cycle → 修 cluster 或 shift_up_unit 时序
- H2 命中：SMT 调度算的 RtoVR consume cycle 早于 chain 数据 expose cycle → 修 axiom
- H3 命中：SMC_k>0 broadcast 模式 dr_d expose 条件不满足 → 修 shift_up_unit
- H4 命中：emit hex line 2 实际包含错误 data → 修 emitter `_zero_broadcast_data_for_followers`

#### D2.c 修补

按 D2.b 定位结果，最小化修补（< 50 行，单一 surface）。改动文件预期：
- H1/H3 → `sim/smc_simulator/units/shift_up_unit.py` 或 `sim/smc_simulator/system/cluster.py`
- H2 → `src/synthesis/axioms/**`（**必经 hco 复核**）
- H4 → `src/driver/emitter.py`

#### D2.d 守护测试

`tests/test_adr020_shiftup_chain_propagation.py`（新建）：
- cycle-level 断言：SMC_k 的 `chain_in` 值在 cycle = t0+k 等于 SMC_0 broadcast 值
- 端到端断言：`fma_parallel_n` 32/32 SMC `y` array 与 golden bit-perfect
- 字段断言：emitter 输出的 SMC_k>0 `iq_shiftup.hex` 中 data 字段 = 0（覆盖 H4 silent-failure 即使不命中也防回归）

### D3 · 验收

| 项 | 标准 |
|---|---|
| `python3 verification_v2/main_verify.py --op fma_parallel_n` | 32/32 SMCs bit-perfect |
| `pytest tests/` | 65 基线 + 新加守护测试全 pass |
| 调试 scaffold | D2.a 临时打印必须清理 |
| `_zero_broadcast_data_for_followers` silent no-op | 修复或显式声明 by-design（推荐前者） |
| ADR-018 v7 D3.b/D3.e axiom 行为 | 不动；若 H2 命中需 hco 复核 |

## Consequences

### 正向

- 真正解决 SMC_k>0 bit-perfect mismatch（v1 修法 A 改 iq_unit 是误诊，会留真 bug 不动）
- 揭示并修复 emitter silent-failure 模式（`_zero_broadcast_data_for_followers`）
- 守护测试加 cycle-level chain 数据传播断言（v1 仅 fire count 不足）

### 负向

- 工作量比 v1 大：需先 D2.a 调试再 D2.b 定位再 D2.c 修，不是单文件 < 30 行的一步到位
- 若 H2 命中（axiom 层），ADR 范围扩大需 hco 复核 + 可能触发 ADR-018 v8

### 风险与缓解

| 风险 | 缓解 |
|---|---|
| D2.a 调试 scaffold 漏拆留在 sim 代码里 | D2.d 守护测试加 grep 断言 `[SHIFTUP-CYCLE]` 字符串不出现在 sim/ 中 |
| 修 chain 时序导致单 SMC fma 回归 | ADR-018 21 项守护 + 65 基线 pytest cover |
| H1+H2 同时命中（数据流时序错 + axiom 不正确）| 优先修 H1（sim 层），axiom 修留 ADR-020 v3 |
| H4 silent no-op 修后导致 ADR-018 D5.c 行为变化 | 修 `_zero_broadcast_data_for_followers` 同时加单元测试断言函数真清零 |

## Open Questions

| OQ | 描述 |
|---|---|
| OQ-1 | D2.a 调试 dump 中 SMC_1 chain_in 在 cycle ≤ 34 是否到位（rtovr-3 消费在 cycle 35 开始）？是 → H1 排除；否 → H1 强命中 |
| OQ-2 | broadcast 模式下 SHIFTUP `_next_dr_d` 在 SMC_k>0 的 expose 条件 `is_broadcast and smc_id <= smc_id_en` 是否在 sim cycle 中确实成立？ |
| OQ-3 | D3.b chain RAW axiom 在 SMT 调度中实际产出的 RtoVR 消费 cycle vs sim expose cycle 是否对齐？需对比 `compiler_schedule.json` 中 RtoVR consumer 的 t vs sim 实测 |

## hco 复核请求

- D2.a 调试 + D2.b 定位完成后：
  - 若根因在 sim 层 (H1/H3): hco 复核**可选**（无 hw_spec / axiom 变更）
  - 若根因在 axiom 层 (H2): **必经 hco**
  - 若根因在 emitter (H4): hco 复核**可选**（仅 silent-failure 修复，不动 ISA / hw_spec）
- 本 ADR v2 接受为 Accepted 前：建议无需再过 hco（v2 已基于实测 evidence 不再有 v1 的硬件 spec 矛盾）

## References

### v1 历史（保留为反向学习样本）

- v1 决策：修 `iq_unit.py` body loop 让 SHIFTUP fire ≥ 32 次/SMC
- v1 hco 反对要点：
  1. 与 `shiftup.yaml:55 iq_slot_consumption: "t=t0 同步发射"` 字面冲突
  2. `fire=32` 是 `chain_stage_latency × smc_num` 与 `fma_parallel_n.N` 数值巧合
  3. 根因极可能在 emitter / shift_up_unit chain expose，不在 IQ 状态机
- 教训：commit message 中的 grep 数据要在 ADR 中**作为 evidence 实测复验**，不能直接当 hypothesis 基础

### 关键文件

- [ADR-018](ADR-018-multi-smc-scheduling-design.md) v7 · 多 SMC 调度骨架
- [ADR-019](ADR-019-shift-cross-smc-yaml-sim-alignment.md) v6 · SHIFTUP/SHIFTDN 字段补齐
- commit `52babc2` · session-handoff 三阻断范围圈定
- `hw_config/specs/units/shiftup.yaml:55` · `iq_slot_consumption` spec
- `sim/smc_simulator/units/shift_up_unit.py:39-78` · **v2 主要嫌疑面**
- `sim/smc_simulator/system/cluster.py:84-97` · 1-cycle/stage chain delay
- `sim/smc_simulator/tools/physical_runner.py:299` · per-SMC compare 框架
- `src/driver/emitter.py:231-256` · `_zero_broadcast_data_for_followers` silent no-op
- `src/synthesis/axioms/**` · D3.b chain RAW axiom 实现（H2 嫌疑面）
- `build/verification_v2/fma_parallel_n/fma_parallel_n_sim.log` 末行 · sim 实测 FAILED 数据

### 实测 sim 输出（v2 evidence 锚）

```
Multi-SMC Simulation Finished at Cycle 97. Verifying Data...
FAILED y: 1/32 SMCs match.
SMC_1 Max Diff=11.262639999389648
sim[0..2]=[4.272484, 3.506026, 4.836310]
ref[0..2]=[15.535124, 13.361784, 9.798473]
```

---

## v3 · 根因再裂（2026-05-28 晚 · 本 session）

### Context Evidence v3

#### E6 · H5 dump 否定（UR bank loader OK）

- `build/verification_v2/fma_parallel_n/smc_0/ur_data/bank0.hex` 与 sim 跑后 `output_bank/bank0_final.hex` **逐行完全一致**（去 `0x` 前缀后 diff exit=0）
- 256 行 hex，前 64 行非零（一行 128-bit = 4 fp32 → 共 256 个非零 fp32）—— commit `05ad013` msg 中 "a[4..31] 全 0" 与 "line 0 仅 4 fp32" 全部 retract
- 结论：URBank.load_hex 加载正确，整 sim 期间未误改 bank 0 → **H5 排除**

#### E7 · INNER vs OUTER input_data raw dump（temp print 实测）

在 `main_verify.py:90`（prepare_input 调点）与 `pipeline_runner.py:39`（multi-SMC 重生成点）各加临时 print 各 3 字段：

| 字段 | OUTER (main_verify) | INNER (run_full_stack) |
|---|---|---|
| `a[0..3]` | -0.44, 0.68, -0.38, -0.51 (randn σ=1) | 3.55, 2.08, 1.16, 1.07 (uniform 1-5) |
| `c[0..3]` | 1.03, -2.81, 0.75, 0.70 (randn) | 3.08, 3.85, 1.93, 1.36 (uniform 1-5) |
| `b` shape | (256,) 1D | (32, 256) 2D |

- INNER `a[0..3]` 与 `bank0.hex` line 0 word-reverse decode 完全匹配 → **sim 实吃 INNER**
- Auditor 用 OUTER 算 expected → expected/actual 数据源系统性不一致

#### E8 · sim AUDIT-DATA 跨 SMC log key 冲突（auditor.py:380）

- `grep -c "Cyc:39 .*UR-READ.*Port:2"` → **32 条**（每 SMC 一条，V0 各不相同）
- `auditor.py:380` dict key `(cyc, f"ur-r{port}")` 32 SMC 共用 → **后写覆盖前写**
- 实测 Auditor 看到的 V0 = SMC_31 的 c[31,0]=4.566896，非 SMC_0 的 c[0,0]=3.076964
- 结论：Auditor parser **缺 smc_id 维度**

#### E9 · sim SMC_0 真数值错（bug #3 锚）

- `output_bank/bank5_final.hex` (SMC_0) line 0 lanes（LE iter 顺序）= [12.26, 6.50, 19.39, 9.27]
- 期望 `y[0, 0..3] = a[0..3] * b[0, 0..3] + c[0, 0..3]` = [19.08, 6.65, 6.39, 5.79]
- 算式：`3.547847 * 4.510116 + 3.076964 = 19.078` ✓ 与 ref[0]=19.08 一致
- sim_smc_0[0]=12.26 不在 SMC_0 任何理论 lane 位置；最接近 `y[0, 5]=12.83`（差 0.57，非简单 lane swap）
- → 怀疑 lane order / iter-index / chain inject 在 SMC_0 通路里有真数值错（bug #3，未修）

#### E10 · expected_val_map trace_id 映射异常（bug #4 锚）

bug #2 修后剩余 4 个 Auditor FAIL：

| trace_id | IQ | sim Val | Auditor Exp | 真期望 | 异常 |
|---|---|---|---|---|---|
| `rt_a_read_3_FPMUL_S0_1` | rtovr-3 | 2.04 | 0.00 | a[0,0]=3.55 | Exp 缺失（默认 0） |
| `rt_inl_2_FPADD_S1_4` | rtovr-6 | 9.18 | 0.00 | t1=a*b≈16.0 | 同上 |
| `rt_y_0_a1_UR_W0_S_5` | rtovr-35 | 12.26 | 3.08 | y[0,0]=19.08 | Exp 取到 c[0,0] 而非 y |
| `out_obj_0` | ur-w0 | 12.26 | 3.08 | y[0,0]=19.08 | 同上 |

- 第 3/4 行 Exp=3.08=c[0,0] —— Auditor `expected_val_map[out_obj_0]` 取的是 c_read_1 同源（DFG fma 中 `c` 是直通到 add 第 2 输入）
- → bug #4: trace_id → expected_val_map 计算或映射错（未修）

### 真根因（v3 落定）

不是 SHIFTUP chain 数据流时序（v2 假设），而是 4 层独立 bug：

| bug | 层 | 状态 | 修法 | commit |
|---|---|---|---|---|
| **#1** | input_data 双源（main_verify 单 SMC randn vs run_full_stack 多 SMC uniform）| ✅ 修 | `pipeline_runner.run_full_stack` ctx 加 `input_data` 字段 + `main_verify` 调 Auditor 时优先用 `ctx["input_data"]` + partitioned 2D 切 `[0]` | 本 commit |
| **#2** | Auditor parser dict key 缺 smc_id（32 条同 cycle 同 port AUDIT-DATA 覆盖到 SMC_31）| ✅ 修 | 5 处 `[AUDIT-DATA]` print 加 `SMC_ID:{smc_id}` + Auditor parser 解析 SMC_ID 字段并按 `audit_smc=0` 过滤 + 单 SMC 旧日志（无字段）解析为 `smc_id=0` 向后兼容 | 本 commit |
| **#3** | sim SMC_0 真数值错（lane/iter/chain inject 路径在 SMC_0 通路有 mismatch） | ❌ 未修 | TBD（需 dump SMC_0 ur-w0 写 bank 5 各 ITER 实际 V0 vs 理论 a\*b+c per-lane / per-iter）| Next session |
| **#4** | Auditor expected_val_map trace_id 映射错（rt_y_0_a1 / out_obj_0 取到 c 而非 fpadd 输出）| ❌ 未修 | TBD（查 `_evaluate_expected_values` 对 rt_* / out_obj_* trace_id 的 env 计算路径）| Next session |

### v1/v2 假设全部 retract

| 假设 | v1/v2 描述 | v3 验证结果 |
|---|---|---|
| H1 chain 数据流时序错位 | SMC_k chain_in 到位晚于 RtoVR 消费 cycle | **排除**（commit `52babc2` D2.a dump 已验，chain 对齐） |
| H2 D3.b axiom 错 | SMT 调度算的 RtoVR 消费 cycle 早 | **排除**（同 D2.a） |
| H3 DR_SHIFTUP_D expose 时机错 | SMC_k>0 broadcast 模式下未 expose | **排除**（同 D2.a） |
| H4 emitter silent no-op 影响 SMC_k>0 | `_zero_broadcast_data_for_followers` 漏清 | **排除**（fma_parallel_n 实测 SMC_0 也 fail，与 SMC_k>0 行为无关） |
| H5 UR bank loader 未加载 bank0.hex 到 storage | SMC_0 storage[0] 全 0 | **排除**（bank0.hex == bank0_final.hex 逐行匹配） |
| H6 prepare_input fma_parallel_n a vector layout 错（只 4 不 32） | bank0.hex 仅 line 0 有数据 | **排除**（256 行中前 64 行非零 = 256 个 fp32 完整） |
| H7 UR_READ port 0 routing 错 | ur-r0 拿不到 storage[0] 值 | **排除**（bug #2 修后 ur-r* 全 PASS） |

### 回归引入点

```
$ git log --oneline -L 36,42:verification_v2/infra/pipeline_runner.py
a0f5b99 feat(adr-018-stage1): D3.b + per-SMC data e2e partial (task #18 sub-7/7)
+        if is_multi_smc:
+            first_val = next(iter(input_data.values()))
+            n_rows_actual = first_val.shape[-1]
+            input_data = operator.prepare_input(n_rows_actual, smc_num=smc_num_eff)
```

- bug #1 由 commit `a0f5b99`（task #18 sub-7）引入 `run_full_stack` 内部重生成 input_data 但未回传 ctx
- bug #2 由 a0f5b99 同 commit 引入 multi-SMC 路径但 sim AUDIT-DATA print / Auditor parser 仍按单 SMC 模型（缺 smc_id 维度）
- bug #3 / #4 至少从 a0f5b99 起就潜伏（commit `52babc2` "SMC_0 bit-perfect ✓" claim 反向不成立 —— 当时实测 SMC_0 也 fail，或测试覆盖盲区）

### META · 本 session 反省

- prior session commit `05ad013` 中 v2 evidence 段三处错认（**已 retract**）：
  1. "SMC_0 sim 实际值 0.0 全部" → 实测 sim_smc_0[0] = 12.26（非零错值）
  2. "bank0.hex line 0 仅 4 个 fp32; a[4..31] 全 0" → 实测前 64 行非零 = 256 个 fp32
  3. "真根因方向 H5/H6/H7" → 7 个 hypothesis 全排除；真根因是 4 层独立 bug
- 共同 anti-pattern：commit-msg-as-truth + 单层 hypothesis-locked。修法：每 ADR Evidence 段必含 raw dump 数值，不引 commit msg 自述
- bug #1 修法验证后**暴露 bug #2/#3/#4**：分层 bug 调试需要 fix 一层 dump 一层，不能假设 "fix #1 完 SMC_0 即 bit-perfect"。下次 ADR Decision 段应预留"fix-then-verify 螺旋阶数" 

### v3 Decision

#### D5 · 本 session 已交付

- **bug #1 修法**：`verification_v2/infra/pipeline_runner.py` line 167-170 ctx return 加 `"input_data": input_data`；`verification_v2/main_verify.py` line 109-119 调 `auditor.audit_truth_alignment` 时优先用 `ctx["input_data"]` 并对 partitioned 2D 切 `[0]`
- **bug #2 修法**：`sim/smc_simulator/memory/ur_bank.py:68,79` + `sim/smc_simulator/datapath/rto_vr.py:41` + `sim/smc_simulator/units/fp_mul_unit.py:101` + `sim/smc_simulator/units/fp_add_unit.py:162` 共 5 处 `[AUDIT-DATA]` print 加 `SMC_ID:{getattr(self, 'smc_id', 0)}` 前缀；`sim/smc_simulator/core/kernel.py:108-117` 在 ParallelKernel __init__ 末尾对 `ur_bank/rtovr/units` 全部 setattr smc_id；`verification_v2/infra/auditor.py:328,366-394` 加 `audit_smc=0` 参数，parser 解析可选 SMC_ID 前缀并过滤
- **守护**：pytest 65/65 不回归（单 SMC 路径 SMC_ID 字段缺失走兼容分支 smc_id=0 = audit_smc=0）
- **fma_parallel_n e2e**：bug #1 修后 Auditor 用对 expected；bug #2 修后 ur-r* 3 个 op 由 FAIL → PASS（不再出现在 FAIL 列表）；剩 4 个 FAIL 全是 rt_* / out_obj_0（bug #4）+ sim 真数值仍错（bug #3）

#### D6 · Next Session 起点（ADR-020 v4 / 或拆 ADR-021）

1. **bug #3** drill-down：dump SMC_0 ur-w0 写 bank 5 各 ITER 的 V0（cycle, addr, V0）vs 理论 `a*b+c` per-iter per-lane 对照，确定是 lane order / iter-index / fpmul-fpadd chain inject 哪一环错
2. **bug #4** drill-down：在 `auditor._evaluate_expected_values` 加 print，对 trace_id ∈ {rt_a_read_3_FPMUL_S0_1, rt_inl_2_FPADD_S1_4, rt_y_0_a1_UR_W0_S_5, out_obj_0} 看 env 计算路径，确认是 DFG metadata 缺失还是 evaluator 走错分支
3. 若 bug #3 / #4 修后 fma_parallel_n SMC_0 仍 fail → 进 SHIFTUP chain / RtoVR routing 真深层调研（v2 H1/H2/H3 假设重新激活的可能性，需 cycle-level dump 重启）
4. v4 落地后 ADR-020 状态升 Accepted 全清

### v3 References

- 本 commit · bug #1 + #2 修法 + ADR-020 v3 段
- commit `a0f5b99` · 回归引入点（task #18 sub-7）
- commit `05ad013` · v1+v2 ADR 撰写 + v2 evidence（含三处错认，本 v3 retract）
- `verification_v2/infra/pipeline_runner.py:36-42, 167-170` · bug #1 修复点（重生成 + ctx 回传）
- `verification_v2/main_verify.py:109-119` · bug #1 Auditor 调用路径（含 partitioned 2D 切片）
- `sim/smc_simulator/core/kernel.py:108-117` · bug #2 smc_id 下传 setattr
- `verification_v2/infra/auditor.py:328,366-394` · bug #2 parser 修法 + audit_smc 参数

---

## v4 · bug #3 cycle-level root cause（2026-05-28 晚 · 本 session）

### Context Evidence v4

#### E11 · bank5_final.hex line 0 word-reverse decode 锚点确认

bank5_final.hex 每行 32 hex chars = 4 fp32 (LE lane 0 = rightmost word)：

```
line 0 hex: 0x41144a36419b152b40cff19641441d26
  word-reverse decode → lane 0..3 = [12.2571, 6.4982, 19.3853, 9.2681]
```

与 v3 commit msg `c249715` 中 "[12.26, 6.50, 19.39, 9.27]" 完全一致 → 锚点正确。

#### E12 · 理论 y[0,0..3] 与 actual 全 SMC y matrix 全位置搜索

固定 seed `default_rng(0)` 复现 SMC_0 输入：
- `a[0..3] = [3.548, 2.079, 1.164, 1.066]`
- `b[0,0..3] = [4.510, 1.347, 3.834, 4.157]`
- `c[0,0..3] = [3.077, 3.849, 1.933, 1.356]`
- 期望 `y[0,0..3] = a[0..3]*b[0,0..3]+c[0,0..3] = [19.078, 6.650, 6.395, 5.788]`

actual 4 lane 在所有 32-SMC × 256-row y matrix 中搜索 `|diff| < 0.05`：
- actual lane 0 = 12.2571 在 SMC_0 最接近 y[105]/y[247]（远离 0），SMC_1..31 各有零散匹配
- **无任何 lane permutation / iter shift / SMC permutation 单一变换能解释 4 lane 同时匹配**
- → 简单几何错排假设全否定

#### E13 · cycle-level AUDIT-DATA dump（per cycle SMC_0 数据流）

`build/verification_v2/fma_parallel_n/fma_parallel_n_sim.log` 解析（V0 字段 word-reverse decode）：

| Cyc | Unit | Tag | 数据 | 实测 lane 值 | 期望 | 判定 |
|---|---|---|---|---|---|---|
| 2 | ur-r0 | READ Bank:0 Addr:0 | a[0..3] | [3.55, 2.08, 1.16, 1.07] | a[0..3] = [3.55, 2.08, 1.16, 1.07] | ✅ a 读出正确 |
| 36 | fpmul | COMPUTE S0 | **a[124..127]** | [2.04, 1.97, 4.55, 1.90] | a[0..3] = [3.55, 2.08, 1.16, 1.07] | ❌ **+31 iter 偏移** |
| 36 | fpmul | COMPUTE S1 | b[0,0..3] | [4.51, 1.35, 3.83, 4.16] | b[0,0..3] | ✅ b 通路对 |
| 41 | fpadd | COMPUTE S0 | c[0,0..3] | [3.08, 3.85, 1.93, 1.36] | c[0,0..3] | ✅ c 通路对 |
| 41 | fpadd | COMPUTE S1 | FPMUL_out | [9.18, 2.65, 17.45, 7.91] | a*b[0]=[16.0, 2.80, 4.46, 4.43] | ❌ 由 S0 错传导 |
| 46 | ur-w0 | WRITE Bank:5 Addr:0 | y_actual[0] | [12.26, 6.50, 19.39, 9.27] | y[0,0..3]=[19.08, 6.65, 6.39, 5.79] | ❌ 由 fpmul S0 错传导 |

**单点错误定位**：`Cyc:36 FPMUL S0` 错；b 通路（partitioned，SMC own bank 读）和 c 通路（同）全对 → bug 在 a 通路（broadcast，走 SHIFTUP chain）。

#### E14 · 精确 bug formula（iter 0/1 数值验证）

per-iter dump（FPMUL Cyc 36..67，对应 iter 0..31）：

| iter k | FPMUL S0 lane 0 实测 | 对应 a[?] | 偏移 |
|---|---|---|---|
| 0 | 2.0355 | **a[124]** | +124 = 31×4 |
| 1 | 1.4982 | **a[128]** | +124 |
| 2 | 4.2388 | **a[132]** | +124 |
| ... | ... | ... | 恒 +124 |
| 31 | 1.2611 | **a[248]** | +124 |

bug formula：
```
sim_actual_y[k] = a[(k+31)*4 .. (k+31)*4+3]  ·  b[0, k*4 .. k*4+3]  +  c[0, k*4 .. k*4+3]
                      ^^^^^^                  partitioned 正确    partitioned 正确
                      broadcast +31 iter 偏移
```

验证：
- iter 0：predicted = `a[124..127]·b[0,0..3]+c[0,0..3] = [12.2571, 6.4982, 19.3853, 9.2681]` = actual ✓
- iter 1：predicted = `a[128..131]·b[0,4..7]+c[0,4..7] = [9.9566, 7.1136, 15.8016, 8.1564]` = decode(bank5 line 1) ✓

→ **bug formula 锁定，offset = `smc_num - 1` = 31 iter（恰好链长 - 1）**

### 真根因 v4 · Compiler axiom vs Sim 实现语义不一致

#### 编译器 axiom 假设

`src/synthesis/axioms/dependency_axioms.py:52-60`：

```python
lat_p = spec.delays.get(producer.op_type.upper(), 1)  # SHIFTUP base = 1
# ADR-018 v7 D3.b (task #18 sub-7): SHIFTUP/SHIFTDN 跨 SMC chain RAW.
# SPMD 模型 consumer 单 t_var 必须满足最差 SMC_{smc_id_en} 数据到达，
# 故 chain_stage_latency × smc_id_en 计入 producer 有效 lat。
if producer.op_type.upper() in ('SHIFTUP', 'SHIFTDN'):
    smc_id_en = producer.metadata.get('smc_id_en', 0)
    if isinstance(smc_id_en, int) and smc_id_en > 0:
        chain_stage_latency = 1  # ADR-018 D3.b
        lat_p += smc_id_en * chain_stage_latency  # 32-SMC: lat_p = 1 + 31 = 32
```

**axiom 隐含模型**：SHIFTUP producer 对 ALL consumer SMC 而言，其数据有效到达时间 = `t_prod + 1 + smc_id_en`。
- 反推：consumer 必须在 ≥ `t_prod + 32` 时才能采样
- 反推：producer 的实际效果如同一个 `smc_id_en`-stage **延迟 FIFO**

#### 仿真器实现

`sim/smc_simulator/units/shift_up_unit.py:39-78`（latency=1）：

```python
# SMC_0 chain head 本地数据源；其他 SMC 接上游 CRU_SHIFTUP_I
if smc_id == 0:
    self._next_cru_o = local_data & ((1 << 128) - 1)
else:
    self._next_cru_o = chain_in & ((1 << 128) - 1)
# broadcast 模式所有 smc_id ≤ smc_id_en 都暴露 DR_SHIFTUP_D
if (is_broadcast and smc_id <= smc_id_en):
    self._next_dr_d = self._next_cru_o
```

`sim/smc_simulator/core/kernel.py:512-517`：

```python
self.units['shiftup'].evaluate({
    ...
    'data': self.ur_bank.get_read_data(0)  # SMC_0 chain head 本地数据源
})
```

**sim 实际模型**：SHIFTUP 是 **1-stage register**，每 cycle `cru_o ← local_data`（SMC_0）/`cru_o ← chain_in`（其他）。DR_SHIFTUP_D 每 cycle 都被新数据覆盖。

#### SMC_0 与 SMC_{smc_id_en} 行为差异（精确时序）

设 a_read iter k 在 cycle `2+k` fire（abs_t=2, II=1）：

| 时刻 | SMC_0 DR_SHIFTUP_D | SMC_31 DR_SHIFTUP_D（chain tail）|
|---|---|---|
| Cyc 2 | a[0..3]（自读 1 cycle 后） | 0（chain 还没传到）|
| Cyc 3 | a[4..7]（被覆盖）| 0 |
| ... | 每 cycle 覆盖 | 0 |
| Cyc 33 | a[124..127] | a[0..3]（chain 31 hop 刚到）|
| Cyc 36 | **a[136..139]** ≈ +31 iter | **a[12..15]** ≈ +3 iter（rtovr setup 余量） |

`Cyc:36 FPMUL fire` 时（SPMD 全 SMC 同 cycle）：
- 编译器假设 ALL SMC 都拿到 iter 0 的 a → 这只对 chain tail SMC_31 成立（粗略对齐，可能仍差几 cycle）
- SMC_0 chain head 拿到 iter ~33 的 a → 实测 iter 31

**→ 这就是 bug #3 的物理根因**：axiom 把 SHIFTUP 当 N-stage FIFO 用，sim 把它当 1-stage register 实现；SPMD 模型对 chain head 失效。

#### 重要推论

- **bug 只发生在 SMC_0**：SMC_31 chain tail 大致对齐（仍可能 off-by-1/2，需独立验证）
- **b、c 通路无 bug**：partitioned tensor 每 SMC own bank 读，不走 chain
- **数值 offset = smc_num - 1 = 31** 不是巧合 —— chain 长度 - 1

### v4 Decision

#### D7 · 4 个修复方向 inventory

| 方向 | 修法 | pros | cons |
|---|---|---|---|
| **A** · 修 sim | SMC_0 SHIFTUP 加 `smc_num`-stage circular buffer（write cycle t，read cycle t+N） | axiom 不动；emitter 不动；fma_parallel_n 立即 bit-perfect | sim ≠ hw spec（spec 中 SHIFTUP 是 1-stage）→ 等于篡改 sim；需 hco 决定是否接受"sim 偏离 spec"的语义 |
| **B** · 修 axiom + emitter | emitter 给每 SMC stagger a_read abs_t（SMC_0 后移 `smc_num-1` cycle，SMC_31 不变） | sim 不动；与现 hw spec 对齐 | 违反 SPMD "同指令同 cycle fire"；编译模型大改；emitter 复杂度增；可能引爆其他 axiom |
| **C** · 修 hw_spec + sim | shiftup.yaml 加 `smc_num`-stage internal pipeline；sim 配套加 N-stage shift register | 最干净，axiom + sim + hw spec 三方对齐 | 硬件成本（N=32 × 128bit register ≈ 512B/SMC × 32 SMC = 16KB SRAM）；需 hco + hw 团队联审；周期长 |
| **D** · 修 routing | SMC_0 rtovr-3 SEL 单独走本地直读（如 SEL=0 / 直读 R0），SMC_1..31 维持 SEL=29 (DR_SHIFTUP_D) | sim 不动；emitter 改动局限；可让 SMC_0 bit-perfect first 目标短期可达 | 违反 routing 跨 SMC 统一性；SMC_1..30 chain 中段是否仍 off-by-K 需逐 SMC 验证；SPMD 模型概念漏洞 |

#### D8 · 推荐路径（待 hco 复核）

- **短期**（ADR-020 v4 收尾达成 SMC_0 bit-perfect first）：方向 **D**（routing per-SMC），最小动 emitter，不动 axiom / sim / hw_spec
- **长期**（达成 32-SMC bit-perfect 全清，ADR-020 Accepted 状态升）：方向 **C**（修 hw_spec），最干净对齐
- **决策权**：本 ADR 仅 inventory，不 commit 修法；hco subagent 复核决定 A/B/C/D 选哪个；hco 之前 sim/axiom/hw_spec 任何一处改动都 **禁止落盘**

#### D9 · 本 session 已交付

- bug #3 cycle-level root cause 锁定（E11-E14 evidence + bug formula + axiom/sim mismatch 定位）
- 4 修法方向 inventory + 推荐 D 短期 / C 长期
- **未交付**：未改任何代码（sim / axiom / emitter / hw_spec / yaml 全部不动）；未跑修复后回归；未触碰 bug #4

### Open Questions v4

1. **SMC_31 是否真 bit-perfect？** v4 未独立验证 SMC_31（chain tail）。若 SMC_31 也错（偏移 +K 非 0），则方向 D 不适用，必须走 C；若 SMC_31 对则 D 可作短期修法
2. **方向 D 的副作用**：SMC_1..30（chain 中段）DR_SHIFTUP_D 偏移多少？是否也需 per-SMC stagger SEL？
3. **abs_t 排程数值精确分解**：实测 `abs_t(FPMUL) - abs_t(a_read) = 36 - 2 = 34`；axiom 给出 `lat_p = 32`，差 2 cycle 待 SMT trace 确认是否由 rtovr / latch 贡献
4. **bug #4（Auditor expected_val_map）独立性**：bug #3 修后 bug #4 是否仍存？v4 未触碰
5. **历史 ADR-018 D3.b 决策回溯**：`dependency_axioms.py:53` 注释提到 "ADR-018 v7 D3.b (task #18 sub-7)"；该决策当时是否考虑过 sim 端的实现对齐？若 ADR-018 当时假设 sim 同步实现 N-stage delay 而 sim 实际只实现 1-stage，则属 ADR-018 收尾遗留

### hco 复核请求 v4

> **触发条件**：变更涉及 `src/synthesis/axioms/` / `hw_config/specs/units/shiftup.yaml` / `sim/smc_simulator/units/shift_up_unit.py` 三处之一（CLAUDE.md decision routing 第 1-2 行）。本 ADR 不直接改这三处但提出修法方向 inventory，需 hco subagent 给出方向 A/B/C/D 的选型决策 + 修法范围审定。
>
> **请 hco 评估**：
> - 方向 A（sim N-stage buffer）是否构成"sim 偏离 spec"违规？sim 是否应严格反映 hw_spec？
> - 方向 B（emitter per-SMC stagger）是否破坏 SPMD 编译模型？ADR-018 是否预留此可能性？
> - 方向 C（hw_spec 改 SHIFTUP 为 N-stage）的硬件成本（≈ 16KB 额外 SRAM）是否可接受？是否有更轻量 hw 方案（如复用现有 bank latch）？
> - 方向 D（routing per-SMC）的 SMC_1..30 副作用边界？

### v4 References

- 本 commit · ADR-020 v4 段（cycle-level root cause + 修法 inventory）
- `src/synthesis/axioms/dependency_axioms.py:52-60` · SHIFTUP producer lat_p += smc_id_en 公式（**根因点 1**）
- `sim/smc_simulator/units/shift_up_unit.py:39-78` · SHIFTUP sim 实现（1-stage register，**根因点 2**）
- `sim/smc_simulator/core/kernel.py:512-517` · SHIFTUP local_data wiring (ur_bank port 0 → shiftup data)
- `sim/smc_simulator/system/cluster.py:84-97` · `_propagate_chain_inputs` 链 propagation（SMC_0 chain_in=0 / SMC_31 chain tail）
- `hw_config/pipeline_delays.yaml:16` · `SHIFTUP: 1` base latency
- `hw_config/modules.yaml:201-206` · SHIFTUP module spec（in: CRU_SHIFTUP_I, SMC_ID / out: CRU_SHIFTUP_O, DR_SHIFTUP_D）
- `build/verification_v2/fma_parallel_n/fma_parallel_n_sim.log` · cycle-level AUDIT-DATA dump 源（E13 表数据来自此）
- `build/verification_v2/fma_parallel_n/smc_0/output_bank/bank5_final.hex` · actual 输出 line 0/1（E11/E14 验证）
- `build/verification_v2/fma_parallel_n/physical_instructions.json` · abs_t 排程（a_read=2, fpmul=36, fpadd=41, ur-w0=46）
- commit `52babc2` · ADR-018 Stage 1 收尾时遗留（"SMC_0 bit-perfect ✓" 反向 claim 不成立的回归引入点之一）

---

## v5 · hco 复核 + B 方向选定（2026-05-28 晚 · 本 session）

### Context · 复核背景

v4 落盘后调 hco subagent 独立复核 4 修法方向（A/B/C/D）。hco 不见主对话上下文，仅按 ADR-020 v4 + `src/synthesis/axioms/dependency_axioms.py` + `sim/smc_simulator/units/shift_up_unit.py` + `hw_config/specs/units/shiftup.yaml` + `hw_config/modules.yaml` + `hw_config/pipeline_delays.yaml` + `ADR-018 D3.b` 原文做独立判读。

主对话 v4 §D8 当前倾向（短期 D / 长期 C）**被 hco 全部 reject**。

### v5 真根因再校准（v4 描述误判订正）

v4 §"真根因 v4" 段写的是 "compiler axiom 假设 chain 提供 N-stage 延迟 FIFO；sim 实现为 1-stage register；二者不一致"。这个 framing 隐含**两端都有错**。hco 判读颠覆此 framing：

| 层 | v4 framing | v5 hco 校准 |
|---|---|---|
| spec yaml（`hw_config/specs/units/shiftup.yaml` L20-29, 50-55） | 模糊 | ✅ **对**：`latency: 1` + `chain_stage_latency: 1`，behavioral_semantics 明文单拍 chain hop，**无任何 N-stage buffer 字段** |
| sim 实现（`shift_up_unit.py:39-78`） | 模糊 | ✅ **对**：严格按 spec 1-stage register |
| **axiom 实现**（`dependency_axioms.py:56-60`） | "假设 N-cycle buffer" | ❌ **唯一错点**：把 ADR-018 D3.b 原始 per-SMC RAW 公式 fold 成单 t_var worst-case |

**ADR-018 D3.b 原始公式（`docs/adr/ADR-018-multi-smc-scheduling-design.md:99-104`）**：
```
∀ chain op, ∀ k ∈ [0, smc_id_en]:
    Implies(k <= smc_id_en,
            t_DR_SHIFTUP_D_at_SMC_k >= t_issue + k * chain_stage_latency)
```
注意 `t_DR_SHIFTUP_D_at_SMC_k` —— **per-SMC 独立 t_var**。

**axiom 实现 fold**（`dependency_axioms.py:53-60`，task #18 sub-7 引入）：
```python
# SPMD 模型 consumer 单 t_var 必须满足最差 SMC_{smc_id_en} 数据到达
lat_p += smc_id_en * chain_stage_latency  # 折成 op.t_var 单维 worst-case
```

**fold 的代价**：所有 SMC 的 consumer fire 被绑到同一 `op.t_var`，编译器把 chain tail (k=smc_id_en) 的延迟需求强加到所有 SMC。对 chain tail 自身（SMC_31）—— 大致对齐；对 chain head（SMC_0）—— 数据早 31 cycle 到达后被覆盖，等到 fire 时已 +31 iter 漂移；**对 chain 中段（SMC_1..30）—— 漂移 +(smc_id_en - k) iter**。

→ **v4 §Open Question 1（SMC_31 是否真 bit-perfect）优先级因 hco 判读下降**：即便 SMC_31 真对，SMC_1..30 仍全错位，方向 D（只修 SMC_0）失效。

### v5 4 方向最终评估（hco 表）

| 方向 | 违 spec? | 违 axiom? | 违 SPMD? | 违 chain 拓扑? | 物理可信? | hco 裁定 |
|---|---|---|---|---|---|---|
| **A** sim N-stage buffer | **YES**（spec 仅 1-stage；32-deep FIFO 伪造 hw） | NO | NO | NO | NO | **reject** |
| **B** emitter per-SMC stagger | NO | **YES**（破坏 fold 后的单 t_var SPMD 模型；axiom 须订正 ≠ 破坏 D3.b 数学） | YES（但本属 D5.c lowering 层差异化的自然延伸） | NO | YES | **✅ 选** |
| **C** spec 改 N-stage internal pipeline | NO | NO | NO | NO | **NO**（≈ 16 KB SRAM 跨 chain，远超 SHIFTUP 当前预算；需 RTL 团队介入） | **reject** |
| **D** SMC_0 局部直读 | NO | NO（axiom 仍守 SMC_1..31） | **YES** | **YES**（chain head 不再参与 chain 暴露） | partial | **reject**（未解 SMC_1..30 中段，仅 hard-code chain head） |

**v4 数值校准**：v4 §D7 估方向 C "16 KB SRAM 仅 SHIFTUP 一处"算错；hco 订正 = 单 SMC 32×128 bit = 512 B/SMC × 32 SMC = **16 KB 全 chain**，仍远超 SHIFTUP 当前硬件预算（chain register 共 256 bit）。

### v5 Decision · 选 B（emitter per-SMC stagger）

#### D10 · B 选型 3 理由（hco 给）

1. **B 是唯一与 spec + sim 现状对齐且能根治 SMC_0..30 全 chain 的方向**。spec L54 明文 `chain_stage_latency: 1`，sim 严格 1-stage。要让 chain 每 SMC 在同一 fire cycle 拿到同一 iter，consumer fire cycle 必须 per-SMC stagger：SMC_k fire 在 `t0 + (smc_id_en - k) × 1`。

2. **B 不破 ADR-018 D3.b 公式本身**。D3.b 原文是 per-SMC `t_DR_SHIFTUP_D_at_SMC_k` 公式，本就承认 per-SMC 时序差异；axiom 当前 fold 成单 t_var 是过度简化。**B 把 D3.b 原始 per-SMC 时序约束真正下推到 emitter**。

3. **"违 SPMD" 是表象**。ADR-018 D5.b/D5.c 已经接受 emitter 在 lowering 阶段做 per-SMC hex 差异化（每 SMC hex 不同 NOP padding）。Per-SMC abs_t stagger 是 D5.c lowering 自然延伸，**SMT 模型层仍 1 个逻辑 op，stagger 只发生在 hex emit 阶段**，不构成 SPMD 编译模型颠覆。

#### D11 · 修法范围（hco 审定）

**必动文件**：
- `src/synthesis/axioms/dependency_axioms.py:53-60` —— 删 `lat_p += smc_id_en × chain_stage_latency` worst-case fold；恢复 ADR-018 D3.b per-SMC RAW 公式（per-SMC consumer t_var 而非单 t_var），**或**在 emitter 层 lowering 后插入 per-SMC NOP padding 实现等价效果
- `src/driver/emitter.py`（或同模块）—— 实现 consumer op per-SMC abs_t stagger：SMC_k.fire = `t_logical + (smc_id_en - k) × chain_stage_latency`
- ADR-020 v6（实施 commit 后）：记录 B 落地代码改动 + 回归结果
- `docs/adr/ADR-018-multi-smc-scheduling-design.md:93-103` —— D3.b 段加 errata 标记 "worst-case fold deprecated by ADR-020 v5"（**不动 D3.b 原始公式本身**，原始公式仍正确）

**不动文件**（防 scope creep）：
- `hw_config/specs/units/shiftup.yaml`（spec 是对的）
- `hw_config/modules.yaml:201-211`（同上）
- `hw_config/pipeline_delays.yaml:16`（`SHIFTUP: 1` 正确）
- `sim/smc_simulator/units/shift_up_unit.py`（sim 严格按 spec）
- `sim/smc_simulator/system/cluster.py:84-97`（chain propagation 结构正确）

**退役/订正**：
- `dependency_axioms.py:53-55` 注释（"SPMD 模型 consumer 单 t_var 必须满足最差 SMC_{smc_id_en} 数据到达"）须明确订正：worst-case SPMD fold 已 deprecated；改 per-SMC stagger（ADR-020 v5）

**测试覆盖**：
- `fma_parallel_n` 32-SMC（主验证点，必须 bit-perfect 全 32 SMC）
- `horner` / `activation` / `matmul` 等非 SHIFTUP chain 算子（回归不应破）
- 含 `SHIFTUP broadcast=1` 的所有算子（`grep -rn 'SHIFTUP' verification_v2/ops/` 定位）
- 新增 `tests/unit/test_smt_adversarial.py` 用例：构造 2-SMC SHIFTUP broadcast 算例，断言 emitter 输出 SMC_0 / SMC_1 abs_t 差 1

### v5 风险列表（hco 给的 4 条）

1. **emitter 复杂度上升 O(SMC_NUM)**：lowering 阶段须 per-SMC 计算 abs_t offset；II 计算（`II = max abs_t span`）可能因 stagger 变大。需重跑 II 回归（horner II=7、activation II=?）确认无回退。

2. **下游 op 链式 stagger 传播**：consumer（FPMUL）下游还有 op（FPADD、UR_WRITE），若 a 通路 stagger 而 b/c partitioned 不 stagger，**FPMUL S0/S1 输入会同 cycle 错对齐**。必须解 v4 §OQ #3（`abs_t(FPMUL) - abs_t(a_read) = 34` vs axiom `lat_p = 32` 差 2 cycle 的来源），否则 B 落地后可能引入新 off-by-K。**此风险阻塞 B 实施**。

3. **全量 SMT 回归**：ADR-018 D3.b "worst-case fold" 被订正后，所有依赖该 fold 的 SMT 算例（task #18 sub-7/7 跑过的 32-SMC e2e）须全量回归。已知 `a0f5b99`（task #18 sub-7）commit 引入该 fold，可能其他 op 也踩过类似坑但未被发现。

4. **Per-SMC stagger 与 IQ slot 占用二阶效应**：ADR-018 D3.e `add_chain_iq_propagation_axiom` 假设"沿途 SMC 同 cycle 占 IQ slot"（L139-142）。consumer op stagger 不直接影响 SHIFTUP 自身 IQ 占用（SHIFTUP fire 仍同步），但 consumer FPMUL 的 per-SMC stagger 会让 FPMUL IQ slot 在不同 SMC 落在不同 cycle，**资源 modulo 约束需要 per-SMC 重算**，否则可能 over-constrain。

### v5 Open Questions（v4 OQ 校准）

1. ~~SMC_31 是否真 bit-perfect？~~ **降级**（即便 SMC_31 对，SMC_1..30 仍全错位，D 已 reject；SMC_31 状态对 B 选型不再关键，但仍可作 B 落地后的回归基线）
2. ~~方向 D 副作用~~ **关闭**（D reject）
3. **`abs_t(FPMUL) - abs_t(a_read) = 34` vs axiom `lat_p = 32` 差 2 cycle 来源** —— **升级为 v5 高优先级阻塞项**：B 落地前必须解，否则 stagger 量取值无依据
4. **bug #4（Auditor expected_val_map）独立性** —— 仍未触碰
5. **ADR-018 Stage 2 D3.b 决策回溯（v4 §OQ 5）**：hco 自查标记 ADR-018 三/四审遗漏 producer-side holding 语义；ADR-020 v6 落地时需在 ADR-018 加 errata 反向链回

### v5 hco 复核结论引

> **给主对话**：reject 主对话当前的"短期 D / 长期 C"路径。D 不修 SMC_1..30，C 重定义硬件。B 是 ADR-018 D3.b 公式真正下推到 lowering 层，是 spec + sim + axiom 数学层唯一一致的修法。短期就走 B。

### v5 References

- 本 commit · ADR-020 v5 段（hco 复核 + B 选型 + 修法范围）
- hco subagent 独立复核报告（本 session 触发，aeea859ea... agent id）
- `docs/adr/ADR-018-multi-smc-scheduling-design.md:93-115` · D3.b 原始 per-SMC RAW 公式（**B 修法基础**）
- `src/synthesis/axioms/dependency_axioms.py:53-60` · 当前 worst-case fold 实现（**待删订正点**）
- `src/driver/emitter.py` · per-SMC stagger 实施目标点（具体行号 v6 落地时确认）
- `hw_config/specs/units/shiftup.yaml:20-29, 50-55` · spec 真源（不动）
- v4 §D7/D8（本文件 L491-512）· 4 方向 inventory（hco 否定主对话推荐路径）
- v4 §OQ #3 · `abs_t` 差 2 cycle 来源 → **v5 升级高优先级阻塞项** → **v5.1 已解**

---

## v5.1 · OQ #3 解 + B stagger 精确公式（2026-05-28 晚 · 本 session）

### 状态变更

v5 §Open Questions OQ #3（`abs_t(FPMUL) - abs_t(a_read) = 34` vs axiom `lat_p = 32` 差 2 cycle 来源）由 **升级阻塞项 → 已解**。B 修法实施路径全部 unblock；下一 session 可直接进 v6 代码落地。

### Context · 时间链 4 边分解

`build/verification_v2/fma_parallel_n/physical_instructions.json` BODY-stage abs_t 实测：

| # | 源 | 目标 | 边类型 | latency 来源（`hw_config/pipeline_delays.yaml`）| diff |
|---|---|---|---|---|---|
| – | – | `a_read_3` (abs_t=2) | – | – | – |
| ① | `a_read_3` | `shiftup_a_read_3` (abs_t=3) | RAW | `lat(UR_READ)=1` | **+1** |
| ② | `shiftup_a_read_3` | `rt_a_read_3_FPMUL_S0_1` (abs_t=35) | chain RAW（worst-case fold） | `lat(SHIFTUP)=1 + smc_id_en × chain_stage_latency = 1 + 31 = 32` | **+32** |
| ③ | `rt_a_read_3_FPMUL_S0_1` | `inl_2` (abs_t=36) | RAW | `lat(RTOVR)=1` | **+1** |

总差 `36 - 2 = 34 = 1 + 32 + 1` ✓

### 裁定 · "差 2 cycle 来源"

axiom `lat_p = 32`（在 `dependency_axioms.py:56-60` fold 计算）**只是 SHIFTUP→RTOVR 这一条边**（即边 ②）。`a_read → fpmul` 总差 34 还包括上下游两条 base latency 边（① 与 ③）：

- **边 ①（UR_READ→SHIFTUP）**：`lat=UR_READ=1`，producer 端 base latency，与 chain 无关
- **边 ③（RTOVR→FPMUL）**：`lat=RTOVR=1`，consumer 端 base latency，与 chain 无关

这 2 cycle 是 pipeline 各 unit 的固有 latency，**B 修法不动它们**，仅订正边 ② 的 fold 计算。

### B 修法 stagger 精确公式

**当前 axiom（fold）**：

```
∀ SMC:  t_rtovr = t_shiftup + lat(SHIFTUP) + smc_id_en × chain_stage_latency
                = t_shiftup + 1 + 31
                = t_shiftup + 32       (所有 SMC 共用单 t_var)
```

**B 修法（ADR-018 D3.b per-SMC 原式 + 1 cycle SHIFTUP base lat）**：

```
∀ k ∈ [0, smc_id_en]:
    t_rtovr_at_SMC_k = t_shiftup + lat(SHIFTUP) + k × chain_stage_latency
                     = t_shiftup + 1 + k
                     = t_shiftup + 1 + k    (per-SMC 独立 t_var)
```

**Stagger 偏移量（emitter 在 lowering 阶段对每 SMC 应用）**：

```
delta_k = (smc_id_en - k) × chain_stage_latency
        = (31 - k) × 1
        = 31 - k cycles
```

emitter 输出 hex 时，SMC_k 的 rtovr-3 abs_t = current_fold_abs_t - delta_k。

**SMC_k 实际 fire cycle**（基于当前排程 abs_t=35）：

| SMC | delta_k | rtovr-3 fire cycle | 备注 |
|---|---|---|---|
| 0 | 31 | **4** | 最早 fire；chain head 数据立即可用 |
| 1 | 30 | 5 | 等 1 hop chain |
| 15 | 16 | 19 | 等 15 hop chain |
| 31 | 0 | **35** | 与当前 fold 一致；chain tail 数据最晚到 |

### 链式 stagger 传播范围（hco 风险 #2 显式化）

**B 修法不能孤立 stagger rtovr-3**。一旦 rtovr-3 per-SMC stagger，整 DFG 经 SHIFTUP-跨链的所有 op 都必须同 delta_k stagger，否则 FPMUL S0/S1 同 cycle 错对齐。

#### a 通路（跨 SHIFTUP，全须 stagger）：

| trace_id | unit | 当前 abs_t | SMC_0 修后 abs_t | 备注 |
|---|---|---|---|---|
| `rt_a_read_3_FPMUL_S0_1` | RTOVR | 35 | 4 | a 通路 RtoVR (输入 FPMUL S0) |
| `inl_2` | FPMUL | 36 | 5 | 由 S0 拖动 |
| `rt_inl_2_FPADD_S1_4` | RTOVR | 40 | 9 | 由 FPMUL 拖动 |
| `y_0_a1` | FPADD | 41 | 10 | 由 S1 拖动 |
| `rt_y_0_a1_UR_W0_S_5` | RTOVR | 45 | 14 | 由 FPADD 拖动 |
| `out_obj_0` | UR_WRITE | 46 | 15 | 由 RtoVR 拖动 |

#### b/c 通路（partitioned，不跨 SHIFTUP；但 consumer fire 时间被 a 通路拖动）：

| trace_id | unit | 当前 abs_t | SMC_0 修后 abs_t | 备注 |
|---|---|---|---|---|
| `b_read_4` | UR_READ | 34 | **3** | 必须配套提前 31 cycle；否则 SMC_0 FPMUL 在 cycle 5 fire 时 b 还没读到 |
| `rt_b_read_4_FPMUL_S1_2` | RTOVR | 35 | 4 | 同上 |
| `c_read_1` | UR_READ | 39 | **8** | 必须配套提前 31 cycle |
| `rt_c_read_1_FPADD_S0_3` | RTOVR | 40 | 9 | 同上 |

**关键结论**：b/c 通路虽然 partitioned 不需 chain，但 consumer fire 时间因 a 通路 stagger 而提前，**b/c 也必须 per-SMC 提前 read，否则 FPMUL/FPADD 操作数缺失**。emitter stagger 算法必须对 **整 DFG 拓扑**（不只 a 通路）做 per-SMC offset 计算。

### B 修法实施算法（v6 落地参考）

```
for each consumer_op in DFG (topological order):
    delta_at_SMC_k(consumer_op) = max(
        delta_at_SMC_k(producer_op) for producer_op in deps(consumer_op)
    )
    # 若 consumer 直接跨 SHIFTUP chain dep:
    if has_shiftup_chain_dep(consumer_op):
        delta_at_SMC_k(consumer_op) = max(
            delta_at_SMC_k(consumer_op),
            (smc_id_en - k) × chain_stage_latency
        )
    # emitter 输出 SMC_k 的 abs_t:
    abs_t_at_SMC_k(consumer_op) = current_fold_abs_t(consumer_op) - delta_at_SMC_k(consumer_op)
```

注意：
- DFG topological order 保证 producer 的 delta 先算
- `max` 取最严约束（任何一条上游 stagger 都会拖动 consumer）
- partitioned tensor 的 UR_READ 没有 producer，但若它是某 staggered consumer 的依赖，则 emitter 必须反向传播 stagger

### v5.1 Open Questions（新增 + 校准）

1. **emitter stagger 算法是否能与 SMT 求解器解耦？** 当前 SMT 求解器算出统一 abs_t（基于 fold lat_p），emitter 在 lowering 阶段做 per-SMC subtract。这是后处理范式（low-cost），还是 SMT 必须重算 per-SMC t_var（high-cost）？v6 落地前需选型。

2. **II 是否变？** 当前 II=1（compiler_schedule.json 实测）。per-SMC stagger 后，单 SMC 自己的 II 不变（stagger 是 inter-SMC offset），但 **整 cluster total cycles** 减少（SMC_0 比 SMC_31 早完成 31 cycle），跨 SMC 同步点（如 OQ-8 (i) SHIFTUP 同步发射）须验证不被破坏。

3. **OQ-8 (i) "沿途 SMC 同 cycle 占 IQ slot"（D3.e axiom）与 consumer per-SMC stagger 是否冲突？** SHIFTUP 自身仍同步 fire（不变），但 consumer FPMUL 的 IQ slot 在不同 SMC 落不同 cycle → 资源 modulo 须 per-SMC 重算（hco 风险 #4）。具体哪些 axiom 受影响 v6 落地时枚举。

### v5.1 Decision

- **B 修法 unblock**：OQ #3 解，stagger 公式精确化，链式传播范围审定
- **v6 起点定义**（next session）：
  1. 解 v5.1 OQ #1（emitter stagger 后处理 vs SMT 重算选型）
  2. 动 `src/synthesis/axioms/dependency_axioms.py:56-60` 删 fold
  3. 动 `src/driver/emitter.py` 加 per-SMC stagger 算法（按 v5.1 §"实施算法"）
  4. 动 `docs/adr/ADR-018-multi-smc-scheduling-design.md` D3.b 段加 errata
  5. 全量回归（fma_parallel_n 32-SMC + horner + activation + matmul）
  6. ADR-020 v6 commit
- **本 session 不动任何代码**（仅 doc）；hco 复核范围未扩，v5 修法范围审定仍有效

### v5.1 References

- v4 §E13 cycle-level AUDIT-DATA dump · 时间链实测源
- v5 §"v5 真根因再校准" · spec/sim 对，axiom fold 错
- `build/verification_v2/fma_parallel_n/physical_instructions.json` · BODY-stage abs_t 排程
- `hw_config/pipeline_delays.yaml:2-22` · UR_READ=1, RTOVR=1, SHIFTUP=1, FPMUL=4, FPADD=4 base latencies
- `docs/adr/ADR-018-multi-smc-scheduling-design.md:99-103` · D3.b 原始 per-SMC 公式
- `docs/adr/ADR-018-multi-smc-scheduling-design.md:139-153` · D3.e SHIFTUP IQ propagation（v5.1 OQ #3 关联）

---

## v6 · HCO OQ#1 复核（emitter 后处理 vs SMT 重算选型）+ counter-evidence spike（2026-05-28 同日下一 session · 本版本 Proposed）

### 状态变更

v5.1 §Open Question #1（B 修法 per-SMC stagger 应在 emitter 后处理层还是 SMT 求解层实现）→ 由 hco subagent 独立复核裁定，选 A 路径（emitter 后处理）+ 2 项硬性补丁。counter-evidence spike 实测确认 HCO 自标信心最低点（求解器代价）不构成 A 路径决定性论据，但抽象层切分 + 已 per-SMC axiom 不重复造轮子两条更根本理由保住选型。**本版 Proposed → emitter / axiom 代码全未动；hco 二次复核 + 全量回归通过后升 Accepted**。

### Decision · 选 A 路径 + 补丁 P1 + 补丁 P2

**A 路径**：SMT 求解器仍按现 fold `lat_p = 1 + smc_id_en × chain_stage_latency` 算单 t_var 统一 abs_t（与现 axiom 行为等价，求解器复杂度不变）；emitter 在 lowering 阶段对每条 op 在 SMC_k 输出时 subtract `delta_k = (smc_id_en - k) × chain_stage_latency`，按 v5.1 §"实施算法"（L760-772）+ 风险 #4（反向 BFS 传播 b/c partitioned 通路）做 forward topo + backward propagation 双 pass。

**补丁 P1（doc-only · `dependency_axioms.py:53-60`）**：fold 公式**不动**，但注释扩写为多行明示：

```
lat_p in fold 表示 SMC_{smc_id_en} 数据到达 RTOVR 的 worst-case latency
边界（含 chain 累加），不是 SHIFTUP 自身的 unit lat。emitter post-stagger
阶段会按 SMC_k 减 delta_k = (smc_id_en - k) × chain_stage_latency 修正
实际 fire cycle。详见 ADR-020 v6 §HCO OQ#1 复核 + ADR-018 D3.b errata。
```

**补丁 P2（emitter 新增 `_verify_post_stagger_resources`）**：emitter post-stagger 后必须复跑 ResourceAxioms 做 per-SMC sanity check。HCO 已确认工程上可行（构造 mock t_vars dict 把 `abs_t_at_SMC_k` 当作 `t_vars["op@smc_k"]` 喂入 ResourceAxioms 类接口）。**否则 hex 层资源冲突 SMT 不知**（具体反例见 §HCO 风险 #2）。

### HCO 复核摘要（5 条核心理由）

#### 理由 1 · SMT 抽象层与 emitter 抽象层的职责切分（最强论据）

ADR-018 D5.b/D5.c 已立"SMC 维度 ≠ SMT 求解维度"先例：

- **SMT 视图**：DFG 算子（trace_id）的逻辑时序——单 op 1 个 t_var 求出何时 fire
- **emitter 视图**：把单 op 物化为 SMC_NUM 份 hex（lowering），数据流跨 SMC 通过物理 chain 延迟到达

`src/driver/emitter.py:139-149` 已经在 lowering 阶段做 broadcast 复制 + chain 沿途 follower SMC payload data 清 0（D5.c）。**SHIFTUP/SHIFTDN 的 SMC 维度从来就不在 SMT 里**——没有 32 个 SHIFTUP t_var，只有 1 个 t_var + emitter 复制 32 份。"per-SMC stagger 也是 emitter 维度"是同质问题。

路径 B 反其道而行：把 lowering 的 broadcast 物理事实回灌成 32 × t_var per op，破坏 ADR-018 已经稳定的"SMT 守逻辑层 / emitter 守 SMC 物理层"分层。**这是抽象层错位，不是 cost 问题**。

#### 理由 2 · 资源 modulo per-SMC 已在 axiom 层实现（B 重复造轮子）

`resource_axioms.py:46-54`（add_modulo_constraint）、`:172-188`（add_bank_conflict_axiom）、`:343-387`（add_chain_iq_propagation_axiom）已全部按 `(u_type, smc_id)` / `(smc_id, bid)` / `(chain_type, smc_k)` 二维 key 分池——**SMC 维度的资源独立性早在 ADR-018 D2 落定**。

也就是说：SMT 已经按"每 SMC 私有 IQ / private Bank"建模资源，**唯一漏掉的就是 dependency_axioms.py:56-60 那一条 SHIFTUP→RTOVR 边的 latency**。路径 B 要把所有 op 的 t_var 都 per-SMC 化才能让那一条边真值化，但其余资源 axiom 早就 per-SMC 了——这是把一根筋的局部偏差扩大成全模型重构，违反"最小变更修复偏差"原则。

#### 理由 3 · 求解器复杂度估算（HCO 信心最低点 → v6-3 spike 已弱化此论据）

`ucode_solver.py:354` 创建 t_vars 一次 `O(ops)`。当前 fma_parallel_n 65 ops → 65 个 t_var。

路径 B 下：
- t_vars × 32 = 2080 个整数变量
- `add_iq_latch_protect_axiom`（`physical_axioms.py:69-110`）的 `O(N²) consumer-pair` 在 cross-SMC 下退化为 O((N×32)²) = O(N²×1024)（但已 skip cross-SMC pair `:81-83`，实际不膨胀）
- z3 wall-clock 上界估算（HCO 原始判断）：SAT 阶段 100s–10min 量级

**v6-3 spike 实测结果（见下节）**：变量数膨胀本身不是 z3 瓶颈（deterministic 约束零成本）。**理由 #3 部分弱化**，但理由 #1/#2 仍保住选型。

#### 理由 4 · "Misleading 32" 不是 B 才能解（A 加 P1 等价解）

v5 hco 提到 B 的卖点是"axiom 不再 lie"。但 *axiom 谎言 = fold 公式的物理语义错位*——只要把 dependency_axioms.py:60 那行注释 + 边语义订正为 "lat_p = worst-case `1 + smc_id_en × chain_stage_latency`，即 SMC_{smc_id_en} 到达时间，作为单 t_var 必须满足的最晚边界"，axiom 就不再 misleading，只是 *conservative*——而保守是 SMT 合法行为（safe over-approximation）。emitter 后处理减去 delta_k 等价于"事后说：SMC_k 实际比这个 worst-case 早 delta_k cycle 完成"，这是 *refinement after-the-fact*，符合 ADR-018 lowering 范式。

#### 理由 5 · 回归边界

路径 A 改动局限 emitter 后处理 + dependency_axioms 注释订正——按 v5.1 §"实施算法" L757-772 算 stagger，**不动求解器**。回归只需重跑 fma_parallel_n（32-SMC SHIFTUP-broadcast 算子）+ horner / activation / matmul（非 chain，本应 delta = 0 无影响）。如果 stagger 算法在 `delta_k = 0` 时输出与原 emitter 字节级一致（这是必须的属性测试），那么非 chain 算子的回归是 free pass。

路径 B 改 `t_vars` 数据结构 → backend.py validate_physical_schedule、emit_from_instructions、`schedule[op.trace_id]` 全链路（unit_assignments 注入 latch_time `:467-473`）都要适配 per-SMC schedule。所有算子的 SAT/UNSAT 边界都可能漂动（II 重算 ⇒ v5.1 OQ #2）。

### HCO 风险点（按严重度排序）

#### 风险 #1（高）· "fold misleading 32" 误导后续 audit
`dependency_axioms.py:60` 当前的 fold `lat_p += smc_id_en × chain_stage_latency` 在 fma_parallel_n 下数值 = 32，但 **物理上没有任何一条边真有 32 cycle latency**——SHIFTUP 自己只有 1 cycle base lat。下次有人读 SMT compiler_schedule.json 看到 SHIFTUP→RTOVR 边权 = 32 时，可能误判为 "SHIFTUP unit 有 32 cycle latency" → 触发错误的 hw_spec 修改。

**A 路径回归断言点（补丁 P1）**：
- `dependency_axioms.py:53-60` 注释扩写为多行明示语义（见 §Decision 补丁 P1 原文）
- 在 compiler_schedule.json 输出层附 `physical_meaning: "worst_case_smc_{smc_id_en}_arrival"` 字段（v6-6 task 处理）

#### 风险 #2（高）· OQ #3 资源 modulo per-SMC 重算 vs hex 层资源冲突
v5.1 OQ #3 已识别但**没解**：consumer FPMUL 在 SMC_0 落 cycle 5、SMC_31 落 cycle 35，**modulo II=1 下 r=0 vs r=0 仍同 slot**（fma_parallel_n 现状 II=1），但 II>1 时会落到不同 slot。SMT 现在按统一 t_var 算 modulo，emitter post-stagger 后 hex 层实际 r 可能 ≠ SMT 算的 r。

**A 路径下的具体反例**：
- 假设某未来算子 II=8，2 个 SHIFTUP-broadcast 后下游各跟 1 个 UR_WRITE，落同 Bank
- SMT 按 fold 算 t_a=40, t_b=44 → r_a=0, r_b=4 → bank_gap 通过
- emitter post-stagger 后 SMC_0：t_a → 9, t_b → 13 → 仍 r_a=1, r_b=5（II=8）
- 但 SMC_15：t_a → 25, t_b → 29 → r_a=1, r_b=5
- 看似 OK，但跨 SMC 同 Bank 共享场景（如 broadcast 写回）会 r_a 不一致 → hex 层 bank 冲突，SMT 不知

→ 这是补丁 P2 的存在理由：**emitter post-stagger 后必须复跑资源 axiom 做 sanity check**。

**A 路径回归断言点（补丁 P2）**：
- `src/driver/emitter.py` 加 `_verify_post_stagger_resources(stream_per_smc)`，逻辑是把 ResourceAxioms 重新跑一遍但输入是 `abs_t_at_SMC_k` 而非 SMT t_var
- 反证测试（v6-7）：构造 2 SHIFTUP-broadcast + 1 UR_WRITE 同 Bank，stagger 后某 SMC r 冲突 → verifier 必须 FAIL

#### 风险 #3（中）· II 真值（OQ #2）
A 路径下 SMT 算的 II 是基于 worst-case fold 的保守值。real per-SMC II 可能更小（SMC_0 自己 critical path 比 fold 短）。

- A 接受 *over-estimation* —— II 偏高是 safe over-approximation
- B 路径下 II 真正反映 per-SMC critical path —— 可能解锁更小 II

**判断**：II 保守 = 算子吞吐打折但不出错。II 真值需求是 *optimization*，不是 *correctness*。v6 阶段先求对，再求优。**接受 A 的 II 保守是正确选型**。

#### 风险 #4（中）· stagger 算法的拓扑反向传播
v5.1 §"b/c 通路 partitioned 配套提前" 已审定 b_read/c_read 也要 stagger。emitter 需要做**反向 BFS 传播**——consumer 的 stagger 反传到 producer。这与 v5.1 实施算法 L760-772 的 forward topo + max 不完全一致。

**A 路径回归断言点**：
- 实施时算法须为：`pass 1 forward topo (a 通路 stagger 计算) → pass 2 backward propagation (b/c 通路 producer 提前)`
- 反证测试：fma_parallel_n 32-SMC 修后 `b_read_4 SMC_0 abs_t = 3`（v5.1 L750）必须实测验证

#### 风险 #5（低）· 调试 mental mapping
A 路径下 SMT compiler_schedule.json 显示统一 abs_t，sim trace 显示 per-SMC fire cycle，**调试需 mental mapping `sim_t = smt_t - delta_k`**。

**缓解**（v6-6 task）：
- emitter 在 `physical_instructions.json` 多写一列 `original_smt_abs_t` + `delta_k` + `effective_smc_k_abs_t`
- ADR-020 v6 文档化这个 mapping

### v6-3 Spike · Counter-evidence 实测（HCO 信心最低点验证）

#### Spike 设计

按 HCO §counter-evidence 推荐：在 develop 分支临时 patch `dependency_axioms.py:56-60`，对 SHIFTUP→RTOVR 边引入 `K=smc_id_en+1=32` 个 deterministic per-SMC t_var（约束 `t_smc_k == t_prod + 1 + k × chain_stage_latency`），不删原 fold（保留 latch_vars 依赖不破），纯测变量数膨胀代价。跑 `python3 verification_v2/main_verify.py --op fma_parallel_n --mode smt-unit --quiet --no-review` 各 3 次。

#### Spike 实测

| 指标 | Baseline (无 spike) | Spike (+32 deterministic per-SMC t_var) |
|---|---|---|
| Run 1 | 23.2s (cold) | 20.4s |
| Run 2 | 20.4s | 20.2s |
| Run 3 | 20.4s | 20.5s |
| **稳态均值（排 cold）** | **20.4s** | **20.3s** |
| Δ | — | **≈ 0% (噪音范围)** |

#### Spike 结论

变量数本身**不是 z3 瓶颈**（deterministic 约束一旦 base t_var 固定，per-SMC t_var 立即决定，不引入新搜索分支）。HCO 理由 #3 **部分论据被推翻**。

#### Spike 局限

1. 仅 +32 deterministic 约束（z3 不必搜索）；真 B 路径下 per-SMC t_var 是 *non-deterministic*（参与求解，与 resource_axioms 等其他 axiom 交互）
2. fma_parallel_n 仅 1 条 SHIFTUP→RTOVR 边；真 B 下 ~65 ops × 32 SMC = 2080 t_var
3. HCO 自己已承认 `iq_latch_protect` cross-SMC pair 已 skip（`physical_axioms.py:81-83`，不会 O(N²×SMC²) 膨胀）

#### Spike 对选型的影响

理由 #3 **弱化 → 不再是 A vs B 的决定性论据**。但**最终选 A 不变** —— HCO 理由 #1（抽象层切分）+ #2（已 per-SMC axiom 不重复造轮子）是更根本的论据，与求解器代价无关。

#### Spike 清理

`git restore src/synthesis/axioms/dependency_axioms.py`；pytest 65/65 ✓；工作树 clean；spike patch 不入 commit。

### v6 Implementation 清单（emitter / axiom 代码全未动 · Proposed 状态待 v6 落地后转 Accepted）

| # | 文件 | 改动 | 风险 | 必经 |
|---|---|---|---|---|
| 1 | `src/synthesis/axioms/dependency_axioms.py:53-60` | 补丁 P1：fold 注释扩写（doc-only，公式不动） | 低 | hco 二次复核 |
| 2 | `src/driver/emitter.py` | 新增 `_compute_per_smc_stagger(stream)`（forward topo + backward BFS 双 pass） | 高 | hco 二次复核 |
| 3 | `src/driver/emitter.py` | 补丁 P2：新增 `_verify_post_stagger_resources(per_smc_stream)`（复跑 ResourceAxioms） | 高 | hco 二次复核 |
| 4 | `src/driver/emitter.py` 输出 | `physical_instructions.json` 加 `original_smt_abs_t` / `delta_k` / `effective_smc_k_abs_t` 三列（debug aid） | 低 | – |
| 5 | `tests/unit/test_smt_adversarial.py` | 新增 stagger 用例 + P2 verifier 反证 case | 中 | – |
| 6 | `docs/adr/ADR-018-multi-smc-scheduling-design.md:93-103` | D3.b 段加 errata 标记（指向本 v6） | 低 | – |
| 7 | 回归 | fma_parallel_n 32-SMC + horner + activation + matmul + grep SHIFTUP 全算子 + pytest 65→≥65 + II 回归 | – | – |
| 8 | ADR-020 v6 § Implementation 段补具体行号 + 状态升 Accepted | – | – | hco 三审 |

### v6 Open Questions

1. **v5.1 OQ #2（II 真值）继承**：A 路径接受 II over-approximation 作为 safe 边界。若 future profiling 显示 II 保守过头影响吞吐，再考虑分阶段 B（HCO 不推荐分阶段 B，工程上不如直接 A + emitter）。**状态：Deferred（不阻塞 v6 落地）**
2. **v5.1 OQ #3（资源 modulo per-SMC 重算）**：补丁 P2 verifier 解决。**状态：v6 落地后 closed by P2**
3. **stagger 算法 `delta_k = 0` 时字节级一致性属性**（HCO 理由 #5）：实施时须显式断言 horner / activation / matmul 等非 chain 算子 emitter 输出与 v5.1 baseline 字节级一致。**状态：Open（v6-7 反证测试覆盖）**

### v6 References

- 本 session HCO 复核报告（agentId `a0004457276a40d50`，markdown 输出已嵌入本节）
- `dependency_axioms.py:56-60` · 补丁 P1 目标行
- `src/driver/emitter.py:139-149` · D5.c broadcast 复制现有 lowering 先例（与 P2 同模板）
- `src/synthesis/axioms/resource_axioms.py:46-54 / 172-188 / 343-387` · 已 per-SMC 资源 axiom（理由 #2 证据）
- `src/synthesis/axioms/physical_axioms.py:81-83` · iq_latch_protect cross-SMC skip（理由 #3 / spike 局限 #3）
- `docs/adr/ADR-018-multi-smc-scheduling-design.md:139-153` · D5.b/D5.c lowering 先例（理由 #1）
- v5 §D11 修法范围 · v6 落地必动/不动清单（v5.1 不扩范围）

---

## v6 · HCO 二审 v6-4 implementation outline + 红旗 #1 物理验证（2026-05-28 同日下一 session）

### Context

v6 §Decision + §Implementation 清单 row #1-2 已 HCO 一审审定 prescriptive text；v6-2 P1 注释（commit `80bb88d`）+ v6-7 TDD 骨架（commit `735547a`）+ v6-8 ADR-018 D3.b errata（commit `b6eaf1c`）已 mechanical 落锤。**v6-4 `_compute_per_smc_stagger` 真代码落地前**，按 v4 META 反省（"涉及 axiom/sim/hw_spec 选型应预先调 hco 而非先 self-推荐"），主对话先 prep 4 个 open design Q 的 implementation outline，调 HCO 二审独立裁定。

### HCO 二审 4 个 Q 裁定摘要

| Q | 主对话 outline | HCO 裁定 | 关键差异 |
|---|---|---|---|
| Q1 stagger 算法注入层级 | A.1 emitter 内部（ADR §Implementation row #2 锁定） | ✓ 通过 | + 强制 `lir_stream` keyword-only + `smc_num>=2` 缺 lir_stream **fail-fast raise**（禁止 silent fallback 到 delta=0，遵循 ADR-018 D5.a "SMC_ID 4-bit fail-fast" 同模式） |
| Q2 PhysicalInstruction schema | 不扩 | ✓ 通过 | v6-6 debug 字段写在 emitter 本地 `instr_to_dump_dict(instr, smc_id)`，**不污染 `PhysicalInstruction.to_dict()`**（后者 SMC 无关，多处调用） |
| Q3 abs_t 偏移具现到 hex | B.1/B.2/B.3 候选未决 | ⚠ **B.1/B.2/B.3 全否** → **D.1' INIT 行 loops 字段减法** | 见下 §Q3 物理分析 |
| Q4 P2 verifier 耦合 | C.1 共享 delta_map | ✓ 通过 | ResourceAxioms 接口**不能字面复用**（z3.Int 与 plain int 不能混算），P2 verifier 写 **plain Python 版本** + v6-5 前置小重构（提 `_get_resource_type` + bank_id 派生到 `src/synthesis/axioms/_resource_helpers.py` 独立模块，axiom 类和 verifier 共用） |

### Q3 物理分析（HCO 推断 + 主对话 sim source 推理证据交叉验证）

**B.1（SMI header.dly）致命缺陷**：
- `iq_unit.py:82` DLY 仅 3-bit (max 7 cycle)，fma_parallel_n smc_id_en=31 需装 31 cycle —— **位宽物理不够**
- DLY 是单次 IQ 延后取指，**只能延后不能加速**——方向反

**B.2（NOP padding 重排）致命缺陷**：
- 方向反（主对话 outline "给 SMC_k 头部插 `(smc_id_en - k)` 个 NOP" 实际方向应为 "给 SMC_k 头部插 `k × chain_stage_latency` 个 NOP"）
- 即使方向修正，NOP 行破坏 INIT/HDR/BODY 的 `l_init` 全局对齐（`assembler.py:71`）

**B.3（stream 物理重排重算 l_init）致命缺陷**：
- 同 IQ bucket 内不同 op delta 可能不同（a 通路 RTOVR vs b/c partitioned UR_READ），不能简单减常数
- 跨 IQ stream 全局同步靠 `l_init = (skip_iterations × II) + 1` 维护，重算会丢失同步

**D.1' 落锤 prescription（HCO 推荐）**：
```
SMC_k INIT 行 loops 字段 = instr.header.loops - d_smc
where d_smc = delta_per_smc[smc_k] = (smc_id_en - smc_k) × chain_stage_latency
```

物理含义：以 SMC_{smc_id_en} 为基准（INIT loops 不动；hex 字节级与 v5.1 baseline 一致），SMC_k (k < smc_id_en) INIT loops 减 `d_smc`，**整 IQ stream 提前 d_smc cycle 启动 BODY**。SMC_0 d_smc 最大，SMC_{smc_id_en} d_smc=0。

**SMI 协议兼容性**：
- `iq_unit.py:81` LOOPS 10-bit (max 1023)，d_smc ≤ 31 远小于上限，**新 loops 不溢出**
- 跨 IQ 同步：同 SMC 内所有 IQ 减同一 d_smc，**SMC 内同步不破**；跨 SMC 不同 IQ stream 在不同 cycle 启动是物理需求

### 红旗 #1 物理验证（sim source 推理证据 · D5.c "沿途 SMC 同 cycle 发射" 是 SMT 抽象错觉）

HCO 推断 D5.c 物理违反 chain 1-cycle 串接需求，但无 RTL spec 直接支撑。主对话用户选择 **sim source 推理路径**验证（轻量，5–10 min）。

**Decisive 物理证据**（`sim/smc_simulator/system/cluster.py:84-101` + `sim/smc_simulator/units/shift_up_unit.py:39-78`）：

```
SystemCluster.step() 每 cycle 三阶段:
  1. _propagate_chain_inputs():
       SMC_{k+1}.chain_inputs['shiftup'] ← SMC_k.units['shiftup'].cru_o
       (读的是 cycle T-1 末 tick 后的 cru_o 值)
  2. for each kernel: kernel.step()
       SHIFTUP.evaluate(): _next_cru_o = chain_in (来源 cycle T-1)
       SHIFTUP.tick(): cru_o ← _next_cru_o (cycle T 末才生效)
  3. cycle_count += 1
```

**反证：SMC_1 SHIFTUP @ cycle T 与 SMC_0 同 cycle 的语义崩塌**
```
cycle T 开头: SMC_1.chain_inputs['shiftup'] = SMC_0.cru_o(at T-1 末) ← STALE
cycle T eval(SMC_1): chain_in = stale value (上次循环残留 / 0)
cycle T tick(SMC_0): cru_o ← local_data (valid) ← 但已晚，SMC_1 已读错
```

**正解：SMC_1 SHIFTUP @ cycle T+1 才能拿正确数据**
```
cycle T tick(SMC_0): cru_o ← local_data (valid)
cycle T+1 开头: SMC_1.chain_inputs['shiftup'] = SMC_0.cru_o(T 末) = local_data ✓
cycle T+1 eval(SMC_1): chain_in = local_data ✓
```

**与 v4 §bug #3 fma_parallel_n SMC_1..31 全错位完美对应**：当前 emitter 给所有 SMC 同 hex，sim 严格执行，所有 SMC cycle T 同步 issue SHIFTUP；SMC_0 取 local_data 正确，SMC_1..31 取 stale chain_in → 数据错。**v4 bug #3 cycle-level root cause 由此再次 cross-validated**。

**裁定**：
- D5.c "沿途 SMC 同 cycle 发射 SHIFTUP" **是 SMT 抽象错觉 confirmed**
- 物理上 SMC_k SHIFTUP **必须晚 SMC_0 共 k cycle fire**（chain 1-cycle 串接时延需求）
- **HCO D.1' INIT loops 减法方向 verified**（SMC_0 d_smc=31 → 早 31 cycle 启动 → SMC_31 与 SMC_0 之间 SHIFTUP 物理串接 1-cycle 时延对齐）
- 红旗 #1 **closed**

### 关联 errata（v6 后续，不阻塞 v6-4）

**ADR-018 D3.e `add_chain_iq_propagation_axiom`** (`resource_axioms.py:343-387`) 假设"沿途 SMC 同 cycle 占同 chain IQ slot"——此假设与 D.1' 物理冲突（D.1' 让 SMC_k 比 SMC_0 晚 k cycle issue SHIFTUP）。

**v6-4 落地后必须配套订正 D3.e**：
- 选项 (a) 删 axiom（chain IQ slot 占用本就是 D5.c 抽象错觉的副产物，物理上 SMC_k 在不同 cycle 占 slot 不冲突）
- 选项 (b) 改 axiom 假设 per-SMC stagger 后的 effective_abs_t 算 modulo

**HCO 二审报告 §counter-evidence #3 提议**：v6-4 落地 + 32-SMC 回归通过后再做 D3.e errata 判读，避免范围扩散到 v6-4。本 ADR 仅 flag 该 errata 候选项。

### Q4 P2 verifier 严格 caveat

ResourceAxioms 接口 `add_resource_constraint(solver, ops, t_vars, II, spec)` 要求 `t_vars: Dict[trace_id, z3.ArithRef]`；C.1 共享 delta_map 路径下 verifier 用 `Dict[trace_id, int]` 喂入会破 z3 类型契约。

**v6-5 落锤 spec**：
- `_verify_post_stagger_resources(instructions, lir_stream, delta_per_op, smc_num, II)` 写 **plain Python 版**：循环 SMC × instr，按 effective_abs_t = abs_t - delta_per_op[(tid, smc_k)] 算 modulo，对每 (bank_id, mod_slot) bucket 检查冲突
- bank_id / mod_slot 派生逻辑**字节级一致** with `resource_axioms.py:155-188 get_op_bank_and_addr` + `_get_resource_type`（避免 silent drift）
- **v6-5 前置小重构**（在 v6-5 task 内处理）：提 `_get_resource_type` + bank_id helper 到 `src/synthesis/axioms/_resource_helpers.py`，axiom 类 + emitter verifier 共用解析

### v6-4 实施 spec（HCO 落锤版，主对话照此写真代码）

#### 1. `_compute_per_smc_stagger` 函数签名

```python
@staticmethod
def _compute_per_smc_stagger(
    lir_stream: List["LIROp"],
    smc_num: int,
    chain_stage_latency: int = 1,  # ADR-018 D3.b
) -> Tuple[Dict[Tuple[str, int], int], Dict[int, int]]:
    """
    Returns:
      delta_per_op[(trace_id, smc_k)] : 该 op 在 SMC_k 上相对 SMC_{smc_id_en} 提前的 cycle 数（≥ 0）
      delta_per_smc[smc_k]            : max over all op delta，该 SMC 整 IQ stream INIT loops 应减的 cycle 数
    """
```

算法：
```
Pass 1 forward topo（沿 depends_on + rtovr_deps）:
  对每条 SHIFTUP/SHIFTDN producer p:
    smc_id_en = p.metadata['smc_id_en']
    if smc_id_en <= 0: skip
    for k in range(min(smc_num, smc_id_en + 1)):
      d = (smc_id_en - k) × chain_stage_latency
      for c in consumer-of-p (rtovr_deps + depends_on 反查):
        delta_per_op[(c.trace_id, k)] = max(..., d)
  然后沿拓扑正向传播 max（consumer 继承 producer 的 delta）

Pass 2 backward BFS:
  worklist = {tid for (tid, k) where delta > 0}
  while worklist:
    cons_id = pop
    for prod_id in cons.depends_on:
      for k in range(smc_num):
        if delta[(cons_id, k)] > delta[(prod_id, k)]:
          delta[(prod_id, k)] = delta[(cons_id, k)]
          worklist.add(prod_id)

delta_per_smc[k] = max(delta_per_op[(t, k)] for t in lir_stream)
```

#### 2. `emit_from_instructions` 入口签名（Q1 fail-fast）

```python
def emit_from_instructions(
    self,
    instructions: List[PhysicalInstruction],
    case_root: str,
    program=None,
    smc_num: int = None,
    *,                                          # keyword-only 分隔
    lir_stream: Optional[List["LIROp"]] = None, # NEW: DFG 拓扑入口
):
    if smc_num is None or smc_num == 1:
        return self._emit_single_smc(instructions, case_root, program)
    if lir_stream is None:
        raise ValueError(
            "[ADR-020 v6] _emit_multi_smc requires lir_stream for per-SMC stagger; "
            "pipeline_runner.py must pass lir_stream= keyword."
        )
    delta_per_op, delta_per_smc = self._compute_per_smc_stagger(
        lir_stream, smc_num, chain_stage_latency=1
    )
    return self._emit_multi_smc(
        instructions, case_root, program, smc_num, delta_per_op, delta_per_smc
    )
```

#### 3. `_emit_multi_smc` 注入 d_smc（Q3 D.1'）

```python
def _emit_multi_smc(
    self, instructions, case_root, program, smc_num,
    delta_per_op=None, delta_per_smc=None,
):
    ...
    for smc_id in range(smc_num):
        d_smc = (delta_per_smc or {}).get(smc_id, 0)
        ...
        for iq_name, stream in iq_buckets.items():
            ...
            for instr in stream:
                if self._should_skip_chain_instr(instr, smc_id):
                    continue
                header_eff = instr.header
                if instr.stage == "INIT" and d_smc > 0:
                    header_eff = copy.copy(instr.header)
                    new_loops = instr.header.loops - d_smc
                    if new_loops < 0:
                        raise RuntimeError(
                            f"[ADR-020 v6] SMC_{smc_id} stagger d_smc={d_smc} "
                            f"超过 INIT loops={instr.header.loops}; "
                            f"fold worst-case latency 过小不足以容纳 stagger."
                        )
                    header_eff.loops = new_loops
                payload_per_smc = self._inject_smc_id(...)
                payload_per_smc = self._zero_broadcast_data_for_followers(...)
                val = BitPacker.pack_capsule(..., **header_eff.to_dict())
                hex_lines.append(...)
```

#### 4. `pipeline_runner.py:110-112` 调用点修改

```python
packer.emit_from_instructions(
    physical_instrs, output_dir,
    program=emit_program,
    smc_num=smc_num_eff,
    lir_stream=ops if is_multi_smc else None,  # NEW
)
```

### Zero-diff 属性自检（HCO 风险 #5 / TDD test_stagger_emitter_output_byte_equivalence_at_delta_zero 物理保证）

**当 lir_stream 无 SHIFTUP/SHIFTDN 算子**（horner / activation / matmul 等非 chain 算子）→ Pass 1 跳过所有 producer → delta_per_op 全 0 → delta_per_smc 全 0 → 全 SMC d_smc = 0 → INIT loops 不动 → hex 与 v5.1 baseline **字节级一致**。

**当 smc_id == smc_id_en（最慢 SMC）**→ d_smc = 0 → header_eff = instr.header → hex 字节级一致。fma_parallel_n 32-SMC 下 SMC_31 hex = v5.1 baseline，SMC_0..30 是 stagger 修正版。

### v6-4 落代码后必跑（HCO §counter-evidence）

1. **CE #2 INIT loops margin sanity check**：fma_parallel_n 32-SMC 跑通后 inspect compiler_schedule.json，确认所有 IQ l_init ≥ 31（SMC_0 d_smc = 31）。若 raise RuntimeError 则触 ADR-020 v6 §"INIT margin 不足"边界，需独立判读
2. **TDD 6 个 `TestEmitterPerSMCStagger` 测试全 pass**（含 backward BFS + delta=0 字节一致 + P2 反例）
3. **horner/activation/matmul hex hw-parity diff == 0 字节级**（free pass 属性，HCO 理由 #5）
4. **fma_parallel_n 32-SMC hw-parity 通过**（v6 主验证点，bit-perfect SMC_0..31）

### 文件影响清单（v6-4 真代码 commit）

| 文件 | 改动 |
|---|---|
| `src/driver/emitter.py` | +`_compute_per_smc_stagger` 静态方法 + emit_from_instructions 入口扩 `lir_stream` keyword-only + `_emit_multi_smc` 内 d_smc 注入 |
| `verification_v2/infra/pipeline_runner.py:110-112` | 调用点加 `lir_stream=ops if is_multi_smc else None` |

### v6 Updated Implementation 清单（替换 §"v6 Implementation 清单"）

| # | 文件 | 改动 | 状态 |
|---|---|---|---|
| 1 | `dependency_axioms.py:53-60` | 补丁 P1 注释扩写 | ✓ commit `80bb88d` |
| 2 | `src/driver/emitter.py` | `_compute_per_smc_stagger` + 入口 fail-fast + d_smc 注入 | v6-4 next |
| 3 | `src/driver/emitter.py` | 补丁 P2 `_verify_post_stagger_resources`（plain Python 版） | v6-5 next |
| 3a | `src/synthesis/axioms/_resource_helpers.py` 新增 | v6-5 前置小重构：提 `_get_resource_type` + bank helper | v6-5 前置 |
| 4 | `src/driver/emitter.py` 输出 | physical_instructions.json 加 `original_smt_abs_t / delta_k / effective_smc_k_abs_t` 三列（写在 emitter 本地 `instr_to_dump_dict(instr, smc_id)`，不污染 PhysicalInstruction.to_dict）| v6-6 next |
| 5 | `tests/unit/test_smt_adversarial.py` | TDD 骨架 6 测试 | ✓ commit `735547a` |
| 6 | `docs/adr/ADR-018-multi-smc-scheduling-design.md:93-127` | D3.b errata | ✓ commit `b6eaf1c` |
| 7 | 全量回归 | fma_parallel_n 32-SMC + horner + activation + matmul + SHIFTUP 全算子 + pytest 65→≥65 + II 回归 + CE #2 INIT margin | v6-9 |
| 8 | ADR-020 v6 § Implementation 补行号 + 状态升 Accepted（hco 三审） | v6-10 | |
| **errata** | ADR-018 D3.e `add_chain_iq_propagation_axiom` 订正候选 | v6 落地后判读（不阻塞 v6-4） | TBD |

### v6 二审 References

- 本 session HCO 二审报告（agentId `a4c938d4e6139f49a`，markdown 输出已嵌入本段）
- `sim/smc_simulator/system/cluster.py:84-101` · `_propagate_chain_inputs` 每 cycle 串接 1-cycle 时延（红旗 #1 物理证据）
- `sim/smc_simulator/units/shift_up_unit.py:39-78` · ShiftUpUnit `evaluate → tick` 协议（红旗 #1 物理证据）
- `src/driver/emitter.py:211-256` · D5.c `_should_skip_chain_instr` + `_zero_broadcast_data_for_followers`（per-SMC lowering 现先例，与 D.1' 同模板）
- `src/driver/assembler.py:71-95` · INIT/HDR `l_init` / `loops_val` 物理含义参照
- `sim/smc_simulator/control/iq_unit.py:81-148` · SMI 协议 sim 端 PC 推进，验证 INIT loops 字段是 stagger 注入点
- `tests/unit/test_smt_adversarial.py:332-446` · TDD 骨架已锁 API contract（Q1=A.1 不需 errata）

---

## v6 · v6-4 落地实测发现 + 0/32 深层 root cause（2026-05-28 同日下一 session · 本版本 partial fix）

### Context

v6-4 真代码（commit `7187654`）落锤后跑 `fma_parallel_n --mode e2e` 验证。HCO 二审 §Q3 D.1' 路径 `SMC_smc_id_en 基准 INIT loops 减法` 在 fma_parallel_n 实测触发 CE #2 边界（`ur-r0` IQ INIT loops=0 < d_smc=31 → RuntimeError），切到 `SMC_0 基准加法` 等价变体（HCO §"修正 D.1' prescription" 段提到的双基准等价选项之一）。物理上让 SMC_31 比 SMC_0 晚 31 cycle 启动 BODY，chain 串接 1-cycle 时延对齐。

### Sim cycle 数据 cross-validate HCO 物理推断（红旗 #1 完全 closed）

**v6-4 D.1' SMC_0 基准变体 hex INIT loops**：

| SMC | INIT loops (hex) | smc_shift = max_dsmc - delta_per_smc[k] |
|---|---|---|
| 0  | 0x22=34 (baseline 不动) | 31 - 31 = 0 |
| 15 | 0x31=49 (+15) | 31 - 16 = 15 |
| 31 | 0x41=65 (+31) | 31 - 0 = 31 |

**sim 实测 cycle 数据**（`fma_parallel_n_sim.log` AUDIT-DATA）：

| SMC | First FPMUL fire cycle | Delta vs SMC_0 |
|---|---|---|
| 0  | Cyc 36 | 0 |
| 31 | Cyc 67 | +31 ✓ |

**裁定**：chain 串接物理时延（v5.1 §链式传播范围 / HCO sim source 推理）**完全 verified**。D.1' SMC_0 基准变体让 SMC_k 在 cluster cycle (baseline + k × cs_lat) 启动 BODY，chain SMC_k → SMC_{k+1} cru_o 推送的 1-cycle 时延物理满足。

### 但 fma_parallel_n 32-SMC e2e 仍 `0/32 SMCs match`

**v6-4 不是 regression**：stash 实验确认 baseline（无 v6-4 patch）同样 `0/32 match`。v4 §bug #3 cycle-level root cause 假设"v4 baseline SMC_0 bit-perfect"在实测中**不成立**——SMC_0 baseline 也 fail。

**深层 root cause（v6 ADR + HCO 二审 prescription 未 cover 的层）**：

SMT abs_t 在 SMC_0 视角过保守。fma_parallel_n SMT 排程实测（`compiler_schedule.json`）：

| op | abs_t | iq |
|---|---|---|
| `a_read_3` | 0 | ur-r0 |
| `shiftup_a_read_3` | 1 | shiftup |
| `rt_a_read_3_FPMUL_S0_1` | **33** | rtovr-3 (fold worst-case = 1 + 32) |
| `inl_2` | 34 | fpmul |
| `b_read_4` | 32 | ur-r1 (partitioned, no chain) |
| `c_read_1` | 37 | ur-r2 |

**SMC_0 视角问题**：
- SMC_0 SHIFTUP @ cluster cycle 2（abs_t=1 + cluster baseline 1） → dr_d = iter 0 a
- 但 II=1 每 cycle SHIFTUP 处理新 iter，cycle 33 时 SHIFTUP.dr_d 已被 iter 32 a 覆盖（31 cycle 间已经 evaluate 31 次 → tick 31 次）
- SMC_0 RTOVR-3 @ cluster cycle 33（abs_t=33）取 SHIFTUP.dr_d **= iter 32 a 而非 iter 0 a**
- → SMC_0 FPMUL S0 收到 iter 32 a + iter 0 b/c → 计算结果错位 31 iter

D.1' SMC_0 基准变体只修了"沿途 SMC chain 启动时机"（SMC_31 晚 31 cycle 启动），**没修 SMC_0 自身 BODY 内部 abs_t fold 过保守问题**。SMC_0 RTOVR-3 仍按 abs_t=33 fire（baseline 不动），仍取 iter 32 a。

### 修复方向（HCO 三审 brief 候选）

**选项 V** · 给 SMC_0 BODY abs_t 减 31（"SMC_smc_id_en 基准减法" 真方向）
- 等价 v6 ADR §Decision 原 prescription（HCO §Q3 推荐）
- 触发 ur-r0 INIT loops=0 RuntimeError
- 需配套：assembler 端预增 `l_init += max_dsmc` margin，让所有 IQ INIT loops 至少 = max_dsmc，emitter 端可减
- 范围扩到 `src/driver/assembler.py`（v6 二审 §Q1 = A.1 emitter 内部 violate）

**选项 W** · emitter 端模拟 assembler l_init margin（不动 assembler）
- emitter 在 D.1' 公式之上叠加 `INIT loops += max_dsmc`（统一推后 cluster 启动）+ SMC_k -= delta_per_smc[k]
- net for SMC_0: +max_dsmc - delta_per_smc[0] = 31 - 31 = 0 (不动)
- net for SMC_31: +max_dsmc - 0 = 31
- **这正是当前 v6-4 实施** —— 但 SMC_0 abs_t fold 问题未修
- 等价表明：单调 INIT loops 调整无法同时修"SMC_0 abs_t fold"+"SMC_k chain 启动时序"两个独立维度

**选项 X** · BODY 行 stagger（非 INIT 层 stagger）
- 不调 INIT loops，给 SMC_0 BODY 行 abs_t 相对位置减 31（emitter 给 SMC_k BODY 行重排）
- 改 assembler `abs_t_val = l_init + 1 + k` 公式 per-SMC
- 范围更广，但物理上直接修 fold worst-case 问题

**选项 Y** · SMT 层重审 abs_t fold 范围（回 OQ #1 选 B 路径）
- v6 ADR §HCO 一审已 reject B；但实测显示 A 路径单层调 INIT loops 不足
- 需重新评估"per-SMC t_var 一律"是否真不可行（v6-3 spike 显示 ≈ 0% wall-clock 代价但局限 deterministic）

### v6-4 partial fix 范围 + v8 next

**v6-4 partial fix（commit `7187654`）已锁住**：
- `_compute_per_smc_stagger` API + 算法（forward topo + backward BFS） ✓
- D.1' SMC_0 基准变体 emitter 实施 ✓
- chain 串接 1-cycle 时延物理 verified（sim cycle 数据） ✓
- TDD 5/5 测试 pass + pytest 70/70 不破 ✓

**未交付**：
- fma_parallel_n 32-SMC bit-perfect（与 v4 baseline 同 0/32）
- SMC_0 自身 abs_t fold over-conservative root cause

**HCO 三审 brief（next session）**：评估选项 V/W/X/Y 优劣 + 决定 v6-4 partial fix 是否升 Accepted（chain 串接已修 = partial closure）或 v6 整体延 v7/v8。

### v6-4 实测 References

- commit `7187654` · v6-4 真代码 4 文件
- `build/verification_v2/fma_parallel_n/fma_parallel_n_sim.log` · sim cycle 实测数据
- `build/verification_v2/fma_parallel_n/compiler_schedule.json` · SMT abs_t 实测
- v4 §"4 层独立 bug" · bug #3 SHIFTUP chain RAW（v6-4 chain 串接已修） + bug #4 Auditor（独立轨道）
- v6 §HCO 二审 §counter-evidence #2 INIT loops margin sanity check（实测命中触发 D.1' 基准切换）
