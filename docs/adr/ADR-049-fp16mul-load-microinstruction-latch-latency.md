---
adr_id: ADR-049
title: load 微指令边 = latch latency（1）而非单元乘法 latency（5）—— 修 loop+load 算子的操作数寄存器 WAR 覆盖；三点 latency lookup 同步 + MicroGroup per-group latency
decided: 2026-07-02
last_verified: 2026-07-02
still_holds: yes（2026-07-02 落码 + K16 VCS bank5 lane0=155.14395 bit-match + q_dst VCS 8/8 SMC 无回归 + pytest 184 pass + II 6→4）
builds_on: ADR-047, ADR-048, ADR-032
refines: ADR-047（load 微指令补齐后，其 SMT 边权口径——latch 非乘法）
hco_review: hco 定 + 放行（2026-07-02，RTL 行级取证 fp16mul.v:98/106/108-220/237-278 latch=1 vs :288/405 compute=5；BLOCK：三点同步 + 禁 loop 门控）
user_decision: 契合「时序要算不要调」（derive-timing-not-vcs-calibrate）——latch=1 是 RTL 物理真值，非 VCS 标定
---

# ADR-049 · load 微指令 = latch latency（1），非单元乘法 latency（5）

> **一句话**：ADR-047 给 fp16mul 补的 `load_s0123/load_s4567` 微指令，其 SMT 依赖边权错用了单元乘法 latency（5，
> `spec.delays["FP16MUL"]`）。物理上 load 是**寄存器 latch（1 拍生效）**、非乘法流水（5 拍 trigger→DR）。在**循环+load**
> 算子（K16 首例）里，5-拍口径把 load→compute 链拉成 10 拍 >> II，致 iter1 的 load 抢先覆盖 s\*_bank 操作数（loop-carried
> WAR），VCS 实测 157.365 ≠ golden 155.14。修法 = MicroGroup 加 per-group `latency`，load 组=1，**三处 latency lookup
> 统一走 `spec.micro_delay()`**。

## Context（VCS + RTL 双证，hco 定）

### 症状（K16 首次上 VCS）
- `fp16_matmul_k16`（K=16=2 tile 累加核）golden y[0]=155.14395=tile0(66.18)+tile1(88.96)。
- VCS bank5 lane0 = **157.365** = 68.402 + 88.963。5to1 累加器**完全正确**（68.402+88.963=157.365，loop-carried 无问题）。
- 错在 tile0 的 8to1 partial=68.402（应 66.18，多 2.221）。

### 根因（tb_aiacc_top_f.vpd 波形，SMC_0）
- `s0_bank0_r`（权重 a，load_s0123 latch）：cyc2098 写 a[0:8] → cyc2104 写 a[8:16]（Δ=6=当时 II）。
- tile0 compute 采样于 cyc2107 → s0 已被 iter1 的 a[8:16] 覆盖（a[0:8] 只活 [2098,2104)，compute 晚 3 拍）。
- `s4_bank0_r`（激活 b，load_s4567）：cyc2103 写 b[0:8] → cyc2109 写 b[8:16]；compute@2107 ∈ [2103,2109) → 侥幸拿到 b[0:8]。
- → tile0 实算 a[8:16]·b[0:8]=68.402（错），tile1 实算 a[8:16]·b[8:16]=88.963（对），a[0:8] 被丢。VCS=68.402+88.963=157.365。

### 机理 = load 微指令边用了乘法 latency
schedule（修前 II=6）：load_s0123 t=3 → load_s4567 t=8（+5）→ inl_fp16mul t=13（+5）。链距 10 拍，因三处 latency lookup
（`spec.delays["FP16MUL"]`=5）对 load 微指令 producer 也取 5。compute 落在 load_s0123 后 10 拍 >> II=6 → iter1 的 load
（t=3+6=9）在 iter0 compute（t=13）采样前覆盖 s0 → WAR 违规。

### RTL 取证：latch = 1，compute = 5（hco 行级）
- `fp16mul.v:98/106` `cru_s0123_write_enable`/`cru_s4567_write_enable` = cru 组合译码；`:108-220` 八组 `s*_bank{0,1}_r`
  在 write_enable 后**下一 posedge** 更新 → **latch latency = 1**。`:237-278` `selected_s*_r` 组合从 bank 选出。
- `:288/405` compute 是 `compute_valid_r` 5 级移位 + `dr_fp16mul_d` 输出寄存器 = trigger→DR **5 拍**（ADR-048，输出口径）。
- load 的**输入 latch**口径（1）≠ compute 的**输出**口径（5）。5 拍是乘法流水，绝非 load。

## Decision

### D1：MicroGroup per-group latency + `spec.micro_delay()` helper
- `src/api/hw_spec.py` `MicroGroup` 加 `latency: Optional[int] = None`（None=用单元级 delays，向后兼容 30+ 单微指令单元）。
- `hw_config/modules.yaml` FP16MUL 的 `load_s0123`/`load_s4567` micro_groups 各加 `latency: 1`。
- `HardwareSpec.micro_delay(op_type, micro_variant)`：命中 micro_variant 且该组有 latency → 返之；否则 fallback `delays[op_type]`。

### D2：三点 latency lookup 同步（hco BLOCK——不同步 → Synthesis failed）
三处原本都 `delays.get(op_type)`（=5）、互相自洽故 SMT+verifier 齐过却 VCS 错（self-consistent-but-physically-wrong）。
统一改走 `micro_delay(..., producer/dep/op.micro_variant)`：
1. `src/synthesis/axioms/dependency_axioms.py:37` — RAW 下界（producer.micro_variant）。
2. `src/synthesis/ucode_solver.py:388` — ADR-006 latch lifetime 上界（dep.micro_variant）。**此上界即 WAR**：
   `t_c < t_load+1+II` 精确绑定操作数活窗 [t_load+1, t_load+1+II)，逼 compute 靠近 load。
3. `src/synthesis/ucode_solver.py:247/255` — validate_physical_schedule 维度6 RAW 下界 + overwrite 上界（op.micro_variant）。

### D3：不需要额外 WAR 反依赖（否定我方案 B）
既有 ADR-006 latch 上界（点 2）就是「compute[i] 读须在 load[i+1] 覆盖前」的 WAR。修对 delay=1 后它自动精确绑定，
再加独立 loop-carried 反依赖只会与之重叠、增 desync 面。

### D4：不按 loop 门控（hco BLOCK，契合「时序要算不要调」）
latch=1 对**所有** fp16mul load 边无条件物理成立（单 tile 的 load 也非 5 拍）。用「是否 loop」做条件 = 把物理谎言
（单 tile load=5）写进模型 = VCS-calibration hack，违 [[derive-timing-not-vcs-calibrate]]。故**统一修 + q_dst VCS blocking 复验**；
若 q_dst schedule 漂移致失败，补救走 emit abs_t 重锚（ADR-046 D3 derived lead-NOP），**不回退 latency**。

## Consequences

- K16 修后：schedule load_s0123@5→load_s4567@6→compute@8（compute 紧跟 load 3 拍内 << II）、**II 6→4**（消除长链虚增），
  操作数活窗内被采样。sim e2e PASS（155.14395）+ **VCS bank5 lane0=155.14395 bit-match golden**（硅证）。
- 爆炸半径：所有 `EMIT_FP16MUL_LOAD=True` 算子（q_dst / q_dst_dsт / K16 / full / full_dst）。单 tile（q_dst 等）无 loop-carried
  WAR，仅受下界放宽后 model 可能漂移（q_dst VCS 复验为准）。
- 债务 DEBT-049-ALLPATHS-VCS：全 load 算子 VCS 复验（本 ADR 只硅证 K16 + q_dst）。

## 验收（已达，升 Accepted）
- [x] K16 VCS bank5 lane0 bit-match 155.14395（tb_aiacc_top_f.vpd + ur_result_smc_0_bank5.hex）。
- [x] q_dst VCS 复验仍 8/8 SMC bit-match（下界放宽不破 DST 时序，schedule 漂移但 DST 到达仍对齐 compute@68）。
- [x] K16 sim e2e PASS + II=4 SAT + verifier 无 desync。
- [x] pytest 184 pass（三点同步无回归；test_matmul_q2 mock _Spec 补 micro_delay）。
- [x] 补测：micro_delay 契约单测（load=1/compute=5/fallback）+ K16 loop 操作数活窗断言（compute-load < II）。

## 债务
- DEBT-049-ALLPATHS-VCS：其余 load 算子（q_dst_full / full_dst）VCS 复验（本 ADR 硅证 K16 + q_dst）。
- 多-tile 扩 K（K=24/32 inner_loops++）VCS 累加环时序已由 K16 证（loop-carried 5to1 完美），但更大 K 撞 bank 墙需 HBM 流式（DEBT-025-MULTITILE）。
