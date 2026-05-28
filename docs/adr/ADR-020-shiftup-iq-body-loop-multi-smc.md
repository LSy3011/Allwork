---
adr_id: ADR-020
title: SHIFTUP 多 SMC bit-perfect 收尾（v3：根因转 Auditor 通路 / chain 排除）
decided: 2026-05-28
last_verified: 2026-05-28
still_holds: partial
---

# ADR-020 · SHIFTUP 多 SMC bit-perfect 收尾

> **状态：Accepted (v3, partial-fix)** —— bug #1/#2 已修并 commit；bug #3/#4 列入 Next Session 起点
>
> **演进**：
> - v1（同日早）：决策"改 iq_unit.py body loop，让 SHIFTUP 每 SMC fire ≥32 次/SMC（修法 A）"
> - v1 hco 预审：**Reject-with-major-rework**（修法 A 与 `shiftup.yaml:55 iq_slot_consumption` 字面矛盾；fire=32 数值巧合非语义对；根因可能在 driver 非 sim）
> - evidence-gathering 三步（同日下午）：实测**否定 v1 假设**，揭示假"真根因方向"（见 §Context Evidence v1/v2）
> - **v2（同日下午）**：决策从 "iq_unit.py 状态机" 转向 "SHIFTUP chain 数据流时序"；H1/H2/H3/H4 待 cycle-level 调试
> - **v3（本版，同日晚）**：drill-down dump 后 **H1/H2/H3/H4 + H5/H6/H7 全排除**；真根因裂为 4 层独立 bug（input_data 双源 / Auditor parser 缺 smc_id / sim 真数值错 / expected_val_map trace_id 映射错）；bug #1/#2 本 session 修，bug #3/#4 留 ADR-020 v4

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
