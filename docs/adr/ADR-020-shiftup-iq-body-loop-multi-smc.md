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
> - **v5（本版，同日晚 本 session）**：hco subagent 独立复核 4 方向 → **reject A/C/D，选 B**（emitter per-SMC stagger）。关键判读重定位 v4 误判：**spec yaml 是对的**（SHIFTUP 单拍 chain register 无 N-stage buffer 语义），**sim 是对的**（严格按 spec），**真错在 axiom 实现**（`dependency_axioms.py:56-60` 把 ADR-018 D3.b per-SMC RAW 公式 fold 成单 t_var worst-case）。hco 另揭 v4 未充分认识的：**SMC_1..30 chain 中段全部错位**（不只 SMC_0），方向 D 被彻底否决

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
- v5 §D11 修法范围 · v6 落地必动/不动清单（v5.1 不扩范围）
