---
adr_id: ADR-037
title: SHIFTUP 广播源独立捕获寄存器（修 DEBT-032 fp16 matmul flaky；推翻 ADR-023 D2(B) live-read 中转）
decided: 2026-06-11
last_verified: 2026-06-11
still_holds: proposed
builds_on: ADR-022, ADR-023, ADR-032
supersedes_partial: ADR-023  # D2(B) 的「SHIFTUP data = ur-r0 live 读」中转被独立捕获寄存器取代
---

# ADR-037 · SHIFTUP 广播源独立捕获寄存器

> **状态：Proposed（doc-first per ADR-004，改 SHIFTUP 数据通路 sim 建模 → 过 hco）**。
> hco 独立复核判定 **H2（clobber 是 sim model-A artifact，非 RTL）**，背书本修法方向、明确否决 H1 锁存寿命 axiom。
> 本 ADR 在终值 parity 层 resolve DEBT-032-RESIDENT-TIMING（消 flaky）；**绝对拍忠实度仍需 user RTL 流式时序表 + VCS**，故 still_holds=proposed。

## Context

### 症状（DEBT-032-RESIDENT-TIMING）
fp16 N-广播 matmul（`fp16_matmul_n` / `fp16_matmul_full`）跨 Z3 seed / cold-cache **flaky**，失败时全 SMC 输出 0
（非 addtree 读 0，是**激活广播读到 0**）。ADR-032 的「8/8 bit-perfect」是 schedule-lucky（暖 cache 时 SHIFTUP 恰在 clobber 前捕获）。

### 根因链（sim log 铁证，cycle = 2 + SMT_t；fp16_matmul_n 坏排程）
| op | IQ | t | Cyc | 读 |
|---|---|---|---|---|
| `a_read`（激活广播源） | ur-r0 | 20 | 22 | Bank0/Addr0 = 真激活 |
| `w0_read` prologue 伪读 | ur-r0 | (36→steady) | **28** | Bank1/Addr255 = **0**（addr 0−stride·skip 负数 wrap 255） |
| `shiftup_a_read`（广播捕获） | shiftup | 27 | **29** | 拿到被覆盖的 **0** |

`a_read` 与 `w0_read` 共享 ur-r0（port0）；w1/w2/w3 各占 r1/r2/r3。5 个读（1 激活+4 权重）挤 4 端口，结构上必有
一端口翻倍，恰是广播源 port0。`w0_read` 的 modulo prologue 伪读（Cyc28）覆盖 port0 输出锁存器
（`ur_bank.py`：UR 读端口带输出锁存器，hold 到下次读覆盖），早于 SHIFTUP 在 Cyc29 捕获 → 广播 0 → 全 SMC FP16MUL 读 a=0 → 全 0。

### hco 判定：H2（sim artifact），不是 RTL
hco 独立读 spec 表542-544（`docs/spec/aiacc-v0.4.19-understanding.md`）SHIFTUP 权威伪代码：

```
if broadcast==1 && SMC_ID<=smc_id_en: DR_SHIFTUP_D = CRU_SHIFTUP_O[data]
```

SHIFTUP 暴露的数据来源是 **`CRU_SHIFTUP_O[data]`** —— 指令字（`CRU_SHIFTUP_I` 链寄存器）里的 `data` 微指令字段
（`hw_config/specs/units/shiftup.yaml:37-40`，128-bit immediate），**物理上不 live 读 UR 读端口**。故：
- **H1（live 读、clobber 在 VCS 同样发生 → 加 SMT 锁存寿命 axiom）被 spec 直接证伪**。`sim/.../kernel.py:531`
  把 SHIFTUP `data` 硬接 `ur_bank.get_read_data(0)`（fire 拍 live 读 port0）是 model-A 顶替（DEBT-023-A：
  流式激活 64 字流装不进 128b immediate），**非 RTL 行为**。Cyc28 clobber 是这条 hack + 读端口锁存共同制造的 artifact。
- **H2（硬件早锁，clobber 是 sim artifact → 给 SHIFTUP 独立捕获寄存器）成立。**

## Decision

给 SHIFTUP 广播源一个**独立捕获寄存器**（per-kernel `_bcast_capture_data`）：广播源读（配置 `port` 读配置 `bank`）
发生拍把激活快照进该寄存器、hold 到广播 fire，**不被同端口异 bank（权重）读 clobber**。SHIFTUP 的 `data` 改取该快照
（配了 `broadcast_capture` 时；否则保持旧 live-read，向后兼容）。对齐 spec「SHIFTUP 暴露指令字 `data` 字段而非 live UR 端口」语义。

判别信号：广播源读 **bank0**，clobber 它的权重读 bank1-4（同 port0 异 bank）—— sim log 实证。捕获只在「配置端口读配置 bank」时更新。
⚠ **前提不变量**：判别靠 bank 区分广播源 vs 权重读，依赖「广播源读的 bank 与任何权重读的 bank 不相交」这一 **op 级布局不变量**（当前所有 fp16 matmul 满足：激活独占 bank0、权重 bank1-4）；**非结构性保证**。未来若某算子广播源与某权重共享 bank，判别会误捕权重 → 需改用更强标识（如 immediate tag）。

快照拍取 **a_read 拍**（与 live get_read_data 1 拍流水一致）：终值 parity 正确；绝对拍忠实度（SMI 逐字注入 vs SMI_DS_T DATA_OUT 锁存）是 DEBT-023-BCAST-TIMING 待闭合的 user-fact（见下）。

## Implementation（5 处，零 emitter 签名改动）

- `verification_v2/ops/matmul_op.py` `_inject_shiftup_broadcast`：广播源 UR_READ 打 `metadata["broadcast_source"]=True`。
- `verification_v2/infra/pipeline_runner.py` `_broadcast_capture_config(ops)`：从 DFG 找 broadcast_source，解析
  `port = iq_id − UR_READ 基址`、`bank = logical_addr 经 PhysicalMemoryLayout` → patch `broadcast_capture` 进 sim_config（镜像 smi_ds_t_config 的 post-emit patch）。
- `sim/.../tools/physical_runner.py`：读 `config["broadcast_capture"]` → 每 kernel `set_broadcast_capture(port, bank)`。
- `sim/.../core/kernel.py`：`_bcast_capture`/`_bcast_capture_data` + `set_broadcast_capture`；`_evaluate_ur` 捕获
  `_next_read_ports_out[port]`（本拍读、下拍可见）；shiftup `data` 配了则用快照、否则 live。
- `src/synthesis/ucode_solver.py`：**无改动**（曾试 H1 端口分配确定性约束，证伪后回退，见 Rejected）。

## Consequences

- ✅ **flaky 消除**：`fp16_matmul_n` 4/4 + `fp16_matmul_full` 4/4 cold-build（清缓存）确定性 8/8 bit-perfect（修前各 0/4）。
- ✅ **H2 修 sim 模型而非排程**：对 DEBT-032 实际失败模式（同端口异 bank 权重读 clobber port0 锁存器）鲁棒，
  不依赖 Z3 侥幸排程。⚠ 收窄：`_bcast_capture_data` 是**单寄存器**只 hold 最后一次广播源读，M>4 流式下正确性
  仍隐含「第 m 行广播在第 m+1 行激活读覆盖快照前 fire」的排程序假设（当前排程经验满足，3/3 bit-perfect；
  绝对鲁棒需 per-iteration 快照或 VCS 确认）。
- ✅ **零回归**：pytest 141 pass / 6 skip；`fp16_matmul_q`（单 SMC 广播）/ `matmul_parallel_n` / `fma_parallel_n`
  （均走广播路径）e2e PASS bit-perfect（未 clobber 时快照值 == live 值，向后兼容）。
- ⚠ `fp16_matmul_full` 从 blocked-flaky 转 bit-perfect → ADR-035 SMI_DS_T 转置前端可接续（mirror fp16_matmul_q_dst）。
- ⚠ supersede ADR-023 D2(B) 的 live-read 中转（仍保留为 `broadcast_capture` 未配时的 fallback / N=1 兼容）。

## 升 Accepted 的门（仍 proposed）

- **user-fact（DEBT-023-BCAST-TIMING / DEBT-023-A 闭合）**：流式广播激活每拍由谁、在哪拍驱动 SHIFTUP `data` 入口？
  二选一 (a) SMI 逐字注入 immediate（`CRU_SHIFTUP_I[data]` 每拍换字）；(b) 经 SMI_DS_T `DATA_OUT`(表596) / UR 读端口
  锁存进 SHIFTUP 入口寄存器并保持。需 **SMI→CRU_SHIFTUP 流式注入时序表** + SHIFTUP 入口寄存器锁存/hold 拍。
  H2「a_read 拍快照」终值正确但绝对拍待此表。
- **VCS**：SMC_1..7 各正确 + 广播逐拍时序与 RTL diff。

## Rejected alternatives

- **H1 — SMT 锁存寿命 axiom**（「广播源端口在 a_read↔捕获间不可被覆盖」/「强制 shiftup 紧跟 a_read」）：
  hco 阻断。把 sim-artifact 约束固化进 SMT、在 RTL 不存在对应物理约束 → 过约束调度 + 永久绑死错误硬件模型（ADR-004 反例）。
- **端口分配确定性**（hco 建议的临时修法：广播源独占端口、权重排除）：**结构性不可行**。4 个权重经 FP16MUL 的 S0-S3
  在**同一拍**被消费 → 4 个权重读必须同拍完成 → 需满 4 个端口。保留 port0 给 a_read 后只剩 3 端口给 4 个同拍权重读 →
  全 II UNSAT（实测 II=4..18+ 均 proven UNSAT）。已回退该约束。
