---
adr_id: ADR-040
title: SMT 单流时序模型的正确性判据 —— 广播偏移相消(单流正确)、真跨-SMC gather-to-sink 偏移不相消(单流缺单向≈N边)，不引入 SMC 维度
decided: 2026-06-29
last_verified: 2026-06-29
still_holds: proposed
builds_on: ADR-021, ADR-022, ADR-023, ADR-025, ADR-031, ADR-038, ADR-039
supersedes_partial: >
  docs/spec/aiacc-v0.4.19-understanding.md D-07（「matmul 广播时序靠 COND 等待、未建模」）——
  改判为「激活广播的 2·PARAM_SMC_NUM 是 SMI_DS_T 节点内在延迟、已在 SMT 建模；机制 DLY 静态边权，
  非 COND 动态等待（user 2026-06-29 override 文档 §988 的 COND 例）」
hco_review: APPROVE-with-conditions（2026-06-29）—— 2 阻断项(D2 P1/P2 区分 + DEBT-040 归因)已并入本稿
---

# ADR-040 · SMT 单流时序模型的正确性判据（广播相消 / 真 gather-to-sink 缺单向边）

> **状态：Proposed（doc-first per ADR-004；裁定 SMT 调度时序模型是否需引入 SMC 维度 + 跨-SMC 边权
> → 改 `src/synthesis/` 调度语义、必过 hco。已过 hco：APPROVE-with-conditions，2 阻断项已订正并入）**
>
> **一句话**：质疑「SMT 只排单 SMC 时序、没建模多 SMC、结果肯定不对」（user 2026-06-29）。核查 +
> 文档/代码取证后裁定：**对广播路径不成立**（指令流串行使生产/消费偏移相消，单流 `t_var` 在所有 SMC
> 同样正确，追认 ADR-021 删 SHIFTUP fold 对广播是对的）；**只对"真跨-SMC gather-to-sink"成立**
> （单向收集到汇点、偏移不相消、单流缺一条 **≈N 单向边**）。修法**不引入 SMC 维度**。
> ⚠ 关键澄清（hco）：matmul **激活广播**的 `2·PARAM_SMC_NUM` 延迟是 **SMI_DS_T 节点自身内在 latency、
> 已在 SMT 建模**（其下游消费是每-SMC 本地点积，属相消类），**不是** gather 缺的那条边；真 gather
> （§1204 Matmul_QK 收集到 sink + concat）是另一条路径、≈N 单向、无转置回程、**不可套 2N**。

## Context

### 起源：user 质疑（2026-06-29）
> 「之前的时序模型求解 SMT 都是单 SMC 的时序排布，根本没有考虑过多 SMC，求解的结果肯定不对。」

### 主张1（代码事实，hco CONFIRM）：SMT 确无 SMC 维度
`src/synthesis/ucode_solver.py:349-350` 只建 `t_vars`/`u_vars` 两类、无 `t_{op,smc}`；边权 = unit latency
（`dependency_axioms.py:37-43` `lat=spec.delays.get(op_type)`、`t_c >= t_dep+lat`；`ucode_solver.py:241`
verifier 同款）。ADR-021 已删 `lat += smc_id_en` fold（`dependency_axioms.py:53-64`、
`ucode_solver.py:233-236,266-268`），其「靠 emitter stagger 补偿」理由被 ADR-023 删 stagger 后**抽空**
（`dependency_axioms.py:60-61` 自承）。**质疑前提（单流、补偿层空）全部属实。**

### 主张2（相消判据，hco CONFIRM-with-conditions）：指令流串行 ⇒ 广播偏移相消
单 SMI 全局流经 CRU 寄存器链逐 SMC 转发，SMC_k 在全局发射拍 `t` 之后 `+k` 拍执行（1 latency/hop）。
- RTL 实质（CRU 寄存器链、systolic 1/hop passthrough）由 **ADR-039 C4 背书**（`smi/smi_top.v:384` +
  `smc/shift_up/shift_up.v:34,44`）+ user 2026-06-26 权威（[[aiacc-hardware-timing-model]]）。
  ⚠ 订正：早稿引「smi_top.v:558/583」是 **docx 段落号误植为 .v 行号**；**RTL 不在本仓**
  （`aiacc_rtl/` 独立库），本仓不可直接核行号，实质以 ADR-039 为准。
- **隐含前提（hco 补）**：相消要求 P 与 C 的 `smc_id_en` 范围一致（否则门控 gap 内的 SMC 读不到广播值、
  相消破）。V1.0 因 ADR-031 全 compute 统一 `smc_id_en=PARAM_SMC_NUM-1`，此前提**恰好满足**。

### 主张3（hco 部分订正）：2N 是 SMI_DS_T 节点内在延迟、已建模 —— 不是 gather 缺的边
- `2·PARAM_SMC_NUM` 有逐字文档背书（docx para5142/5473 §1194/§1237「广播给所有 SMC 的激活读取消耗
  PARAM_SMC_NUM×2 时间、一半气泡」；N=8→16，与 `hw_config/pipeline_delays.yaml:18` SMI_DS_T=16 一致）。
- 🔑 **但它是 SMI_DS_T 节点 input→output 的内在 latency，且早已在 SMT 里**：`verification_v2/ops/matmul_op.py:237-244`
  的 `ds_transpose` 节点 `op_type="SMI_DS_T"`（在 `compute_ops` 内拿 t_var），`add_latency_axiom` 对其下游
  SHIFTUP 消费者施加 `t_shiftup >= t_ds_transpose + spec.delays["SMI_DS_T"]`=+16；`verification_v2/tools/s1_smt_check.py`
  正在验这条 16 拍 gap。**激活广播链的最终消费者是每-SMC 本地点积 → 属主张2 相消类**，2N 非"跨-SMC gather 边"。

### 主张4（DLY 机制，hco CONFIRM + 引用订正）
- DLY 字段语义证实（docx para4485「发射到 DLY 数值对应的延迟线、移位到末端才真正发出」；`hw_spec.py:53-54`
  `EN_DLY/BITS_DLY`）。SMT `t_var → DLY` 静态错拍映射成立（§1149-1168 向量加法样例 DLY 累积 0→1→2→3→4、
  COND 全 0）。
- ⚠ **引用方向订正（hco）**：docx para4557（§988）**恰恰把「矩阵乘从 UR 读广播数据、几十拍」列为"延迟很长
  → 难用 DLY → 改用 COND"的例子**。本 ADR 走 DLY **不是文档支持，而是 user 2026-06-29 权威 override**
  （[[matmul-addtree-dataflow-authoritative]] §50-53：V1.0 全片上、延迟确定 → DLY 够，COND 留待将来 DRAM 源）。

## Decision

### D1. 正确性判据（核心，hco CONFIRM）
设 SMC_k 于 `t+k` 执行。对生产者 P→消费者 C：
- **同-SMC 消费（含 SHIFTUP 广播本地消费）**：P、C 同 +k，相对时序 `(t_C+k)−(t_P+k)=t_C−t_P` 与 k 无关
  → **偏移相消** → 单流 `t_C ≥ t_P+lat` 在所有 SMC 成立 → **单流 SMT 正确**。
  **前提**：P/C `smc_id_en` 范围一致（V1.0 ADR-031 uniform 满足）。
- **真跨-SMC gather-to-sink 消费**（P 在 SMC_j、C 在汇点 SMC_i 经 SHIFTDN 收集，j≠i）：相对时序含 `(i−j)`
  **不相消** → 单流缺一条边。

→ **结论：user「单 SMC 求解肯定不对」对广播不成立（相消）、只对真 gather-to-sink 成立。** 已落地
`fma_parallel_n`/`matmul_parallel_n` 热路径纯广播、reduce 在 SMC 内（ADR-025）、无 gather-to-sink，故 8/8
**是判据保证、非侥幸**。

### D2. 两类跨-SMC 数据流必须分开（hco 阻断项1 订正）
| | **P1：DST-datapath 激活广播** | **P2：真 gather-to-sink / concat** |
|---|---|---|
| 路径 | `SHIFTDN gather行 → SMI_DS_T 转置 → SHIFTUP 回程广播` | 各 SMC 结果**单向收集到一个汇点 SMC** + 拼接 |
| 文档 | §946/§1194 | §1204 Matmul_QK「Matmul_K 结果收集到最后一个 SMC 再拼接」 |
| 最终消费 | 每-SMC 本地点积 → **属 D1 相消类** | 汇点 SMC 读收集值 → **D1 不相消** |
| 延迟 | `2·PARAM_SMC_NUM`（SMI_DS_T 节点内在、**已建模**，主张3） | **≈N 单向**（无转置、无回程广播） |
| SMT 现状 | **边已在**（`ds_transpose` op_type=SMI_DS_T lat=16） | **缺这条单向边** ← 本 ADR 要补的真目标 |

**删早稿「V1.0 跨-SMC gather 恒走 2N 往返」全称断言**——它把 P1 的 2N 错套到 P2，与本 ADR 自引的
Matmul_QK（P2，单向无往返）矛盾。**P2 不可复用 2N**。

### D3. 机制 = DLY 静态延迟线，V1.0 不用 COND
SMT `t_var` → SMI_IQ `DLY` 字段。P2 的 ≈N gather 延迟虽长但（V1.0 片上）编译期确定 → DLY。
**依据是 user 2026-06-29 override，非文档**（文档 §988 把此类列为 COND 例，见主张4）。COND 留待将来 DRAM 源。

### D4. 文档订正
- **追认 ADR-021**：删 SHIFTUP→RTOVR fold 对**广播**正确（D1 相消）——理由虽被 ADR-023 抽空，广播本无需补偿。
- **supersede understanding.md D-07**：「靠 COND、未建模」改判为「P1 的 2N 已建模；机制 DLY 确定边权（user override）」。

## Consequences

### 正面
- 一次定死「SMT 要不要加 SMC 维度」=**不加**；并厘清 2N（P1，已建模、相消类）与真 gather 边（P2，待补、不相消）两者不可混。
- fma/matmul-N 的 8/8 从「经验绿」升为「判据保证绿」（广播相消可证）。

### 债务 / 待办（hco 阻断项2 订正归因；2026-06-29 RTL+VCS 实证再精确化）
- **DEBT-040-SINK-EDGE**：P2 真 gather-to-sink 的 **≈N 单向边**尚未写进 `dependency_axioms`/verifier。本 ADR
  P2 路径唯一真"缺的边"。**不复用 2N**。
- **DEBT-040-DST-EDGE-SEMANTICS（bank5=0 真因，RTL+VCS 实证 → supersede 早稿"emit 下游 bug"粗判）**：
  完整取证见 [SMI_DS_T datapath 时序](../spec/smi-ds-t-datapath-timing.md)。`unit_ds_transpose` bank5=0 =
  `SMI_DS_T=16` 实为**输出吞吐窗口**（RTL `smi_ds_t.v` `LAST_BEAT=OUT_ROWS×2`，行58），却被
  `add_latency_axiom` 当 ds_transpose→SHIFTUP 的 **RAW latency 边**误用 → SHIFTUP 排 ds_transpose+16 =
  data_out 窗口尾后 11 拍（VCS `tb_dst.vcd`：`o_cru_shiftup@cyc689` vs 真数据@cyc674–677）→ 硬连
  `wrd_data=ds_t_data_out`（ADR-039）取窗口外零 → payload 空 → bank5 dina 零。门控全对，非多-SMC/缺边。
  **修 = `SMI_DS_T` 拆两量**：① **首 beat 就绪 latency**（collect + ~3 级，用于 RAW 边）② **窗口 16**
  （SHIFTUP 持续广播拍数 / 资源占用）。**病灶在 SMT 边权语义 + yaml 数值语义，非 emit-DLY 层**（早稿据旧
  VCD 7435/7295 判 emit 是粗判；`tb_dst.vcd`+RTL 设计级数精确化为边权语义）。改 `hw_config`+`synthesis` → 走 hco。
- **开放项 ①（2N 含不含广播）—— ✅ 已闭合（RTL 实证）**：`16 = OUT_ROWS×2`（单 SMI_DS_T 内部输出 beat 数，
  跟元素宽度走），**不是** gather8+broadcast8 来回。[[aiacc-hardware-timing-model]]「16=8+8 来回」+
  [[matmul-addtree-dataflow-authoritative]] §55「ON TOP」两种解释**均作废**（详见 spec 文档订正 A）。
- **开放项 ②（P2 是否静态，需 user）**：docx para2455/2723/3938 揭示跨-SMC 指令握手「下游 SMC 完成才向上游
  发指令」= data-dependent backpressure；对 P2 concat 可能使完成时序**非纯编译期常数**，与 D3「确定→DLY」存疑。
  V1.0 是否对 concat 保证静态，待 user 裁。

### 建议补充测试（hco）
- `tests/`：构造**真 gather-to-sink 最小图**（P 在 SMC_j、C 在 sink、无 SMI_DS_T 节点），证单流 SMT 当前
  **确实缺边**（可调出错序 SAT），坐实 D1 对 P2 的「缺边」且**不该复用 2N**。
- 扩 `s1_smt_check.py`：断言 `ds_transpose` 节点的 16 拍边**已存在**（防回归误删），并打印它与 **SHIFTUP 发射
  窗口（应对齐 data_out 首 beat）** 的分层，固化「16 是窗口非 latency」结论。

### still_holds: proposed —— 升 Accepted 两件分开前置
(a) **P2 真 gather-to-sink 单向边权落地**（非复用 2N）；
(b) **SMI_DS_T 边权拆分**（首 beat latency vs 窗口 16）落地 + SHIFTUP 对齐 data_out 首 beat → `unit_ds_transpose`
bank5 非零 + Matmul_QK gather 跨-SMC 时序 VCS 逐拍复验（方案 C，验时序非只验终值）。
