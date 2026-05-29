# DEBT-023 债务清单 —— 单 SMI 模型落地遗留（ADR-023 衍生）

> **状态**：Open（4 项全 open，2026-05-29 建档）
> **来源**：[ADR-023](ADR-023-single-smi-emitter-sim-impl-model-a.md) 落地单 SMI 全局指令流（模型 A 中转）时显式推迟的债务。
> **本档作用**：把散在 ADR-022/023 行内的 DEBT-023-* 聚成一张可跟踪清单 + 标清**每项卡在哪个 RTL 表**，供 user 一次性补料。
> **不卡现状**：4 项均不影响 `fma_parallel_n` 8/8 bit-perfect 现状；它们卡的是 **ADR-023 `proposed → Accepted`**。

## 升 Accepted 的两道硬门（债务的总出口）

| 门 | ADR-022 待办 | 现状 | 依赖的债务项 |
|---|---|---|---|
| **SMT 层重推** chain 延迟建模（ADR-021 删 fold 在单 SMI 模型下是否仍正确） | #4 | 未做（本轮只验「不 UNSAT」，未验语义正确） | 与 DEBT-023-B 同源（fabric 时序决定 SMT 该建模什么） |
| **8-SMC 固件 vs VCS 实测** 背书 | #8 | 未做（VCS mismatch 仅结构级实证，无数值级） | DEBT-023-B/-BCAST-TIMING 闭合后才有忠实固件可比 |

> ⚠ 现 8/8 是**终值 parity**，**不验跨-SMC 时序**，对单 SMI 模型忠实性增信近零（evaluator ACCEPT-with-caveats）。两道门不过，8/8 不构成「单 SMI 已忠实实现」证据。

---

## 债务项

### DEBT-023-B — simulator 模型 A → 模型 B（真 CRU shift-fabric）

- **是什么**：现 simulator 是**模型 A（lockstep 复制）** —— N 个 kernel 加载同一套 hex，SHIFTUP broadcast 走「同拍单源 fan-out」（`cluster.py:_broadcast_shiftup_fanout`，abolish 了 `_propagate_chain_inputs` 的逐级 1-cycle 相对延迟）。**不是**真 CRU fabric（单取指 + 指令沿 SMC_0→SMC_k 逐级 1-cycle 传播 + SMC_ID 匹配执行）。
- **掩盖了什么**：任何依赖 SHIFTUP 到达时序的 bug（某 SMC 在 `a` 未传到时即算 `a*b+c`，真硬件读旧值/0；A 里 `a` 对所有 SMC 瞬时可得）；以及 SHIFTDN 下行回收时序 bug（fma 本轮不触发 SHIFTDN，风险潜伏）。
- **卡在哪个 RTL 表**：表 542/544（SHIFTUP）之外的 **SMI↔CRU shift-fabric 逐级传播驱动时序表**（指令如何沿 SMC 链逐拍推进、SMC_ID 匹配时刻）。与 DEBT-023-BCAST-TIMING 高度重叠。
- **代码触点**：`sim/smc_simulator/system/cluster.py:89,104,131,141`、`sim/smc_simulator/units/shift_up_unit.py`、`sim/smc_simulator/tools/physical_runner.py:_run_multi_smc`。
- **映射**：ADR-022 待办 #2 的真实交付目标。

### DEBT-023-BCAST-TIMING — 广播 `a` 的流式注入时序

- **是什么**：广播 `a` 是 **64 个 128-bit 字的流**（装不进单条指令 128-bit immediate），现实现为「SMC_0 bank0 单源经 ur-r0 读出 + sim 同拍 fan-out」（方案 B）。这是合法 lowering，但「SMI 如何逐字驱动 CRU_SHIFTUP 注入广播」的**真实硬件时序未知**。
- **卡在哪个 RTL 表**：RTL 表 542/544 之外的 **SMI → CRU_SHIFTUP 数据注入流式时序表**（逐字注入节奏 vs 读端口+fabric 模型）。
- **代码触点**：`sim/smc_simulator/units/shift_up_unit.py:16-20,35-36,62-65`、`verification_v2/ops/configurable.py:_inject_shiftup_broadcast (114+)`。
- **映射**：ADR-022 待办 #8（与 DEBT-023-B 合并由 VCS 背书）。

### DEBT-023-A — 广播 `a` 逐字 immediate 展开（备选忠实路径）

- **是什么**：D2 初稿曾设想「广播 data 走指令 data 字段（逐字 immediate 展开）」，因 `a` 是 64 字流装不进 128b immediate 被推翻（ERRATA，单测 `test_shiftup_data_from_instr_field` 一并作废）。**保留为备选**：若 RTL 证实硬件确为「SMI 逐字注入 immediate」，则此路径才是忠实实现，需替换现方案 B。
- **卡在哪个 RTL 表**：同 DEBT-023-BCAST-TIMING —— 需 RTL 明确 SMI 注入是「逐字 immediate」还是「读端口 + fabric」。**二者互斥**，RTL 表到位后 A 与 B 仅一者成立。
- **代码触点**：`verification_v2/ops/configurable.py:_inject_shiftup_broadcast`（data 字段填值逻辑）。
- **映射**：ADR-023 D2 ERRATA。

### DEBT-023-COMPUTE-ADDR — compute 指令 SMC_ID 寻址语义（命名分裂）

- **是什么**：`hw_config/modules.yaml` 寻址字段命名分裂，且比 ADR-023 D6b 记录的更广（本档实测订正）：
  - **`SMC_ID`（单值，从不 gating / 惰性字段）**：FPADD、FPMUL、LOGIC、UR_READ、UR_WRITE
  - **`smc_id_en`（范围，真 gating）**：INTADD、FPTOINT、INTTOFP、INTTOINT、FPTOFP、SHIFTUP/SHIFTDN
  - → 即**整个 INT-add + 全部类型转换族 + shift 族**用范围语义，**只有 FP 算术 + LOGIC + UR 访存**用单值。compute `SMC_ID` 在 behavioral_semantics 中从不 gating。
- **本轮为何不卡 fma**：fma 全员参与（`smc_id_en=N-1`），compute `SMC_ID` 走 `default:0` 即可（按 ADR-022:62 选项 ii「硬件自身 SMC_ID 寄存器分发」）。
- **卡在哪个 RTL 表**：**user 从未提供 compute 微指令表**（权威 RTL 只覆盖表 542–548 = SHIFTUP/SHIFTDN）。需闭合两问：
  1. compute `SMC_ID` 是否**真不 gating**（惰性字段确认）？
  2. compute 是否需「**子集使能**」范围语义？→ 若需，则 `SMC_ID`→`smc_id_en` 是 **modules.yaml schema 变更，必经 hco + 新 ADR**。
- **代码触点**：`hw_config/modules.yaml`（FPADD/FPMUL/LOGIC/UR_* 的 `SMC_ID` 字段 vs INTADD/转换族的 `smc_id_en`）。
- **映射**：ADR-023 D6b（BLOCK 未来 schema，不卡 fma）。

---

## 一句话「待 user 提供 RTL 表」总清单

| 需要的 RTL 材料 | 闭合的债务 |
|---|---|
| **SMI↔CRU shift-fabric 逐级传播 + SMC_ID 匹配时序表**（表 542/544 之外） | DEBT-023-B、-BCAST-TIMING、-A（三者由同一份时序表二选一定型 + 决定 SMT #4 该建模什么） |
| **compute 微指令表**（FP 算术 / LOGIC / UR / INT / 转换族的 SMC_ID vs smc_id_en gating 语义） | DEBT-023-COMPUTE-ADDR |
| **8-SMC 固件 vs VCS 数值级实测**（需上面时序表到位后产出忠实固件再比对） | 升 Accepted 门 #8 |

> 收到上述材料前，ADR-023 维持 `still_holds: proposed`，4 项债务 Open。
