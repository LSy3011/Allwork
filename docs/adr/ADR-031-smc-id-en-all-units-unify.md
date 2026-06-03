---
adr_id: ADR-031
title: smc_id_en 全单元统一（除 RTOVR）—— 对齐设计文档 v0.4.19，推翻 ADR-030 Q2 + 选项-ii
decided: 2026-06-03
last_verified: 2026-06-03
still_holds: proposed
builds_on: ADR-030, ADR-029, ADR-023, ADR-022
supersedes: ADR-030 §D3(Q2 scope-REJECT), emitter.py 选项-ii
---

# ADR-031 · smc_id_en 全单元统一（除 RTOVR）

> **状态：Proposed（hw_config 多文件契约变更，已过 hco + user RTL/设计文档定论 + evaluator）**。
> user 提供权威设计文档 **AIACC_指令集架构_v0.4.19.docx**，确立：**除 RTOVR 外所有硬件单元都有
> `smc_id_en`(5bit)**，现有 spec 里叫 `SMC_ID` 的字段就是 `smc_id_en` 的异名。据此把全 compute
> 单元统一为 `smc_id_en` 并缺省 emit `PARAM_SMC_NUM-1`，修掉「真硬件仅 SMC_0 跑 compute」的多-SMC bug。

## Context

- **触发**：ADR-030 修了 addtree `smc_id_en` emit N-1 后，user 追问其他单元；逐步定论「除 RTOVR 外所有单元都有 smc_id_en，SMC_ID 是其异名」。随后提供设计文档 v0.4.19 作权威源。
- **推翻历史误判**：
  - **ADR-030 §D3 / Q2**（hco 当时裁）：「compute 的 `SMC_ID` ≠ `smc_id_en`，是两套字段族，维持 default 0，DEBT-023-COMPUTE-ADDR open」——**被 user RTL 证伪**。SMC_ID 就是 smc_id_en，compute emit 0 → 真硬件仅 SMC_0 跑 compute（多-SMC bug）。
  - **选项-ii**（`emitter.py:115` 注释，旧 hco agentId a45dedc230c151316）：「compute SMC_ID default 0，硬件按自身寄存器分发」——前提错，作废。
  - **一次错误方向**（本会话）：曾按「master 是完整 SSoT」从 modules.yaml **删** smc_id_en 对齐 master——方向反了（master 不全），已 git 还原。正解是**往 master 补**。
- **权威源辨明**：`HardwareSpec.load`（`hw_spec.py:153-177`）用 master `functional_units` **全量替换** code_fields，但 **code_bits 留 modules.yaml、从不重算**；packer 用 code_bits 做 header 左移量。**master 缺字段 + modules.yaml code_bits 不同步 = ADR-029 同类 header 错位（假 JUMP）**。
- **文档提取工具**：`verification_v2/tools/spec_doc_align.py`（零依赖 stdlib）从 .docx 提取「上行微指令表」并 diff spec。文档升版复跑即可。

## Decision

### D1. master functional_units 统一 smc_id_en（对齐 v0.4.19）
- **改名 `SMC_ID`(5)→`smc_id_en`(5)**：FPADD(表526) / FPMUL(表523) / LOGIC(表541) / INTADD(表56) / UR_READ / UR_WRITE / FP16MUL（顺带，dormant）。
- **补 `smc_id_en`(5)**：FPTOINT(表535) / INTTOFP(表538) / INTTOINT(表532) / FPTOFP(表529) / INT8TOFP16(表59) / FP32ADDTREE8TO1(表520)。
- **FP32ADDTREE8TO1 latency 4→3**（master 与 pipeline_delays 权威 3 冲突，顺带修）。
- **master 不删字段**（与本会话曾误删的方向相反）。

### D2. modules.yaml 同步（code_bits 是 packer 真实依据）
- 改名 SMC_ID→smc_id_en（FPADD/FPMUL/UR_READ/UR_WRITE）；**LOGIC `SMC_ID`(4)→`smc_id_en`(5)**（原 4bit 致字段和 10≠code_bits 11；改 5 修正，code_bits 11 不变）。
- **INTTOFP 补 `src_sign` + code_bits 10→11**（表538 有 src_sign，原 modules.yaml 漏）。
- **SHIFTDN 重构**：`VALID+data(128)` → `VALID+broadcast+smc_id_en`，**code_bits 129→7**（表547：data 走源寄存器 DVR_SHIFTDN_S 非立即数；原 spec 把 data 当指令字段是误读）。
- units/*.yaml 同步改名/重构（fallback-only 但保三处一致，防再次混淆）。

### D3. emit 缺省 N-1 自动覆盖（payload_factory 不改）
ADR-030 的 `field_name == "SMC_ID_EN"` 特例缺省 = `PARAM_SMC_NUM-1`。统一改名后该特例**自动覆盖全 compute** → 全 emit N-1，**payload_factory 一行不改**。SHIFTDN/SHIFTUP 等显式设 smc_id_en 的 op 由 metadata 优先。

### D4. smc_id_en 语义（统一）
`smc_id_en >= SMC_ID 才使能`（阈值门控），所有单元一致。SHIFTUP/SHIFTDN **非广播**时 smc_id_en 另义 = 目标 UR 的 SMC ID（端点寻址，表547 注「与一般计算模块有差别」即指此，不矛盾于广播时的门控语义）。

## 验收（sim 层 + 固件 bit 级已实测；VCS 待跑）
- **生效 spec 全单元 `code_bits == 字段和`**（新测 `tests/unit/test_codebits_consistency.py`，防 ADR-029/031 类错位回归）。
- **smc_id_en 覆盖**：除 RTOVR（应无）+ STB（dormant 未实现，deferred）外全单元有。
- **固件 bit 级**：FPMUL VALID 拍 body `0x3F` → smc_id_en=7（=N-1，compute 全 SMC 使能）✓。
- **doc↔spec diff**：A 类全 `smcen(doc/mod/mas)=True` 字段数吻合。
- **回归**：pytest **87 pass / 6 skip**（含新 codebits 测 + 改写的 ADR-030 compute 测）；matmul/fma 4 算子 + trig/activation（clip/hardsigmoid/leaky_relu/relu6/sigmoid_taylor/abs/horner/cos4）e2e 全 exit=0（sim 对 smc_id_en 全盲，rename+N-1 自洽不破）。

## Consequences
- **正面**：固件 compute smc_id_en=N-1 → VCS 上 SMC_1..7 真使能本地 compute，修掉多-SMC 结构 bug；全 spec 与设计文档 v0.4.19 对齐（字段名/位宽/code_bits）；codebits 一致性 fail-fast。
- **sim 增信为零 / VCS 唯一验收门**：与 ADR-030 同理，sim model-A 对 smc_id_en 不门控。`still_holds: proposed`，升 Accepted 须 VCS 多-SMC 跑出 SMC_1..7 各正确。
- **被 supersede**：ADR-030 §D3(Q2) + 选项-ii；DEBT-023-COMPUTE-ADDR 关闭（compute 确有 smc_id_en）。
- **未纳入（deferred，文档已记）**：
  - **DEBT-031-LOGIC-OPTYPE**：modules.yaml LOGIC OP_TYPE 编码（SELECT_GE/LE/NE=8/9/10）与 v0.4.19 表541 inst_typ（8-15=移位/rotate/CLZ/CTZ）冲突；master 已是 RTL 编码，modules.yaml cosmetic 落后；kernel `lower_piecewise` 用 SELECT_GE/LE/NE 需改派生逻辑 → 单列。
  - **DEBT-031-MISSING-UNITS**：v0.4.19 有但 spec 无的单元（FP16MUL/VIN0/VIN1/VOUT0/MIN0/MIN1/MOUT0/DS/LDB/LDBAXI/STBAXI/合并 UR）——编译器未用到，待用到再补。
  - **DEBT-031-STB**：STB 当前 vld+slot_id=2 字段 vs 表551 8 字段，dormant 无调用。
  - **DEBT-031-CODEBITS-NOCHECK**：loader 不校验 code_bits vs 字段和——已由新测部分兜底，根治需 loader 加 assert。

## 关联
- hco 综合复核（本会话，agentId a2f176672abde2643）：方向 B APPROVE + code_bits 逐单元同步硬约束 + LOGIC/SHIFTDN NEEDS-USER-FACT（已由 v0.4.19 文档/user 答）。
- 设计文档：`AIACC_指令集架构_v0.4.19.docx`（user 2026-06-03 提供，权威）；提取工具 `verification_v2/tools/spec_doc_align.py`。
- builds_on ADR-030（addtree 特例机制，本 ADR 扩到全单元）。
