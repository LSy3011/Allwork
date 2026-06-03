---
adr_id: ADR-030
title: addtree smc_id_en 缺省 emit PARAM_SMC_NUM-1 —— 闭合 DEBT-029-SMCEN（body-gate 裁定）
decided: 2026-06-03
last_verified: 2026-06-03
still_holds: proposed
builds_on: ADR-029, ADR-023, ADR-022
---

# ADR-030 · addtree `smc_id_en` 缺省 emit `PARAM_SMC_NUM-1`（闭合 DEBT-029-SMCEN）

> ⚠ **§D3(Q2) 被 [ADR-031](ADR-031-smc-id-en-all-units-unify.md) 推翻**：本 ADR D3 当时裁「compute 的 `SMC_ID` ≠ `smc_id_en`，维持 default 0」——user 设计文档 v0.4.19 证伪（SMC_ID 是 smc_id_en 异名，除 RTOVR 外全单元都有，全 compute 应 emit N-1）。**本 ADR 的 addtree emit N-1 机制（D1/D2/D4）仍成立**，只是 scope 被 ADR-031 扩到全单元。引用本 ADR Q2 结论前先读 ADR-031。

> **状态：Proposed（payload emit 契约变更，已过 hco + user RTL 裁定）**。ADR-029 补了 addtree `smc_id_en` 字段但 payload 仍 default 0 → 多-SMC matmul 在真硬件上**只有 SMC_0 跑 addtree**，SMC_1..7 被门控掉。本 ADR 把缺省改为 `N-1`（使能 SMC_0..N-1）。**sim 对此字段全盲，8/8 bit-perfect 增信为零；VCS 是唯一验收门。**

## Context

- **触发**：上一会话 handoff 留 DEBT-029-SMCEN 为 VCS P0 验收门，问 addtree `smc_id_en` 是 body-gate 还是 dispatch-gate。本会话 user 给 RTL 裁定。
- **user RTL 事实（2026-06-03，权威）**：`smc_id_en >= SMC_ID 才使能`（阈值门控；`SMC_ID` 是每 SMC 硬连寄存器）。即 `smc_id_en` = 参与计算的最大 SMC_ID。**追问后 user 再确认：所有硬件单元的 `smc_id_en` 含义一致**（消解 ADR-022:42「SHIFTUP smc_id_en 与一般计算模块有差别」的疑虑 —— 那「差别」指 SHIFTUP 的跨-SMC 数据搬运 fabric，非 `smc_id_en` 门控语义本身）。
- **bug 证实**：addtree（FP32ADDTREE5TO1，计算模块）的 `smc_id_en` 缺省落 master yaml `default: 0`（`payload_factory` 未设则取 field.default）。按阈值语义 → `0>=0` 真、`0>=1..7` 假 → **仅 SMC_0 使能 addtree**。多-SMC matmul 每 SMC 本应跑本地点积的 addtree reduce，现 SMC_1..7 全空。
- **sim 为何 8/8 假绿**：sim 只有 SHIFTUP broadcast 路径消费 `smc_id_en`（`cluster.py:108-124` / `shift_up_unit.py:71-81`）；compute/addtree 路径（`fp_mul_unit` / `fp_add_tree_unit` / `ur_bank`）**不读 `smc_id_en`/`SMC_ID` 做 enable**。model-A 下 N kernel 各跑全部指令 → 8/8 对此字段结构性无感。

## Decision

### D1. payload_factory 加 `SMC_ID_EN` 特例缺省 = `PARAM_SMC_NUM-1`
`src/driver/payload_factory.py`：`field_name == "SMC_ID_EN"` 且 metadata 未显式设时，缺省 `val = max(hw_spec.params.PARAM_SMC_NUM - 1, 0)`（类比既有 `ACCUMULATE`/`SUB`/`OP_TYPE` 特例桥，落在 `val is None` 分支，保证 metadata 显式值优先）。`smc_id_en=N-1` = 多-SMC 默认全员参与本地计算，是硬件契约级缺省，集中在 payload_factory 单点生效，对**任何在生效 spec 里携带 `smc_id_en` 字段**的 op 统一兜底，避免 DFG 逐个漏设重蹈 DEBT-029。

**生效覆盖面（2026-06-03 枚举核实）**：master spec（`HardwareSpec.load` 权威，覆盖 modules.yaml）里**只有 SHIFTUP + FP32ADDTREE5TO1** 两个单元真带 `smc_id_en` —— 本特例对二者生效（SHIFTUP 另由 `_inject_shiftup_broadcast` 显式设值，走 metadata 优先；ADDTREE5TO1 走本缺省 N-1）。modules.yaml 另列 smc_id_en 的 FP32ADDTREE8TO1 / FPTOINT / INTTOFP / INTTOINT / FPTOFP / INT8toFP16 / SHIFTDN，其 master functional_units **未定义该字段**（被剥）→ 固件不 emit、本特例无字段可兜。其中仅 FPTOINT/INTTOFP 被 `standard_kernels` 用于**单-SMC** trig/activation 截断（N=1→smc_id_en 无意义）；FP32ADDTREE8TO1/SHIFTDN 不在任何活跃多-SMC 路径（matmul 走 5TO1 + SHIFTUP）。故**无活跃 bug**。但若未来多-SMC reduce 用 8TO1、或启用 SHIFTDN gating，它们**缺字段于权威 spec**（比 emit=0 更深的 gap，需 user 给 RTL 微指令表才能补字段）= **DEBT-030-8TO1-SHIFTDN-SMCEN（open，user-only RTL 事实）**，与 DEBT-023 SHIFTDN spec gap 同源。

### D2. 硬约束：精确匹配 `SMC_ID_EN`，绝不误伤 `SMC_ID`
特例**必须** `field_name == "SMC_ID_EN"` 精确匹配，**不得**用 `"SMC_ID" in field_name` 模糊匹配 —— 否则会把 compute（FPADD/FPMUL/LOGIC/UR_READ/UR_WRITE）的 `SMC_ID` 字段也设成 N-1，踩中下方 scope REJECT。

### D3. scope 边界：compute 的 `SMC_ID` 维持 default 0（不动）
compute 微指令的 `SMC_ID`（≠ `smc_id_en`，两套异名字段族）维持前次 hco「选项 ii」裁定（`emitter.py:115-116`，default 0，硬件按自身 SMC_ID 寄存器分发）。user 2026-06-03 的事实只覆盖 `smc_id_en`，未给 compute 微指令 RTL 表。**本次仅修 addtree `smc_id_en`，前次选项-ii 不被推翻**（二者不同字段族，非 hco 改口）。compute `SMC_ID` 是否门控 / 需范围语义 = **DEBT-023-COMPUTE-ADDR 仍 open**，待 user 给 compute 微指令 RTL 表（X2）才闭合。

## 验收（sim 层 + 固件 bit 级已实测；VCS 待跑）
- **payload 单测**（`tests/unit/test_addtree_smc_id_en_field.py`，3 用例全 pass）：
  - addtree（无 metadata）→ `payload["SMC_ID_EN"] == N-1`（=7 @ PARAM_SMC_NUM=8）；
  - 防误伤反证：FPMUL 的 `SMC_ID` 维持 0、无 `SMC_ID_EN` 凭空字段；
  - metadata 优先级：显式 `smc_id_en=3` → payload 取 3。
- **固件 bit 级**（重生成 build + golden）：`iq_fp32addtree5to1.hex` VALID 拍 body 低 7 位 —— matmul_q2=`0x1D`(VALID1/acc0/**sen7**)、matmul_q=`0x1D`、matmul_parallel_n=`0x1F`(VALID1/acc1/**sen7**)。fma_parallel_n 不走 addtree（一致）。
- **回归**：pytest 86 pass / 6 skip（含新 3 测）；4 算子 e2e 全 exit=0（sim 全盲，smc_id_en 改不影响 sim 终值，符合预期）。

## Consequences
- **正面**：addtree 固件 `smc_id_en=N-1` 与 RTL 阈值语义对齐 → VCS 上 SMC_1..7 能真正跑本地 addtree reduce；缺省集中 payload_factory 单点，杜绝 DFG 漏设。
- **债务 / 待闭合**：
  - **DEBT-029-SMCEN 升级为「emit 已修、VCS 待验」**：sim 增信为零，本 ADR `still_holds: proposed`，升 Accepted **必须** VCS 多-SMC matmul 跑出 SMC_1..7 各有正确 addtree 输出。VCS 通过前 DEBT 不得标 closed。
  - **DEBT-023-COMPUTE-ADDR（仍 open，X2）**：compute `SMC_ID` 门控语义无 RTL 源，本 ADR 明确不动它。
  - 其余 ADR-026/028/029 model-A 时序债务不受本 ADR 影响（正交）。

## 关联
- hco 复核（本会话，agentId ab78722c492e960a1）：Q1 APPROVE（条件 user 确认 X1，已确认）/ Q2 REJECT 扩 scope / Q4 APPROVE payload_factory 特例 + 硬约束。
- 前次 hco 选项-ii（`emitter.py:115`，agentId a45dedc230c151316）：compute `SMC_ID` default 0，本 ADR D3 维持。
- builds_on ADR-029（补 accumulate 字段，本 ADR 补该字段的 emit 值）。
