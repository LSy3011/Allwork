---
adr_id: ADR-029
title: FP32ADDTREE5TO1 微指令补 accumulate 位 —— 对齐 RTL 表 517（修 body 错位 + cumulative 编码）
decided: 2026-06-02
last_verified: 2026-06-02
still_holds: proposed
builds_on: ADR-022, ADR-023
---

# ADR-029 · FP32ADDTREE5TO1 微指令补 accumulate（对齐 RTL 表 517）

> **状态：Proposed（hw_config 契约变更，必过 hco）**。user 对照 RTL 表 517 发现 FP32ADDTREE5TO1 上行微指令缺 **accumulate** 位：spec 里 body=6 bit（vld+smc_id_en），真 RTL=7 bit（vld+accumulate+smc_id_en）。导致 (1) emitter body 比 RTL 少 1 bit → VCS 解码整条 header **左移 1 bit、凭空冒出 JUMP**；(2) 编译器 `cumulative` 标志**未编码进固件**（matmul 跨 tile 累加 / matmul_q2 单 tile 独立的控制丢失）。

## Context

- **触发**：user 准备 VCS，手读 `iq_fp32addtree5to1.hex` 发现 header 的 JUMP 位被置，追问。实为 body 宽度比 RTL 表 517 少 1 bit（accumulate）→ 7-bit-body 的 VCS 解码与 6-bit-body 的 emitter 错位。
- **RTL 表 517（user 2026-06-02，权威）**：FP32ADDTREE5TO1 上行微指令 = `vld(bit0) + accumulate(bit1) + smc_id_en(bit2-6)` = 7 bit。`accumulate`：'b1 累加（old_i 参与）/ 'b0 不累加。
- **权威 spec 源辨明（关键，避免改错文件）**：`HardwareSpec.load`（`src/api/hw_spec.py:153-177`）对每个 module 用 **master spec `specs/aiacc_v2.4_master.yaml` 的 `functional_units` 覆盖** `code_fields`（优先级高于 `modules.yaml.code_fields` 与 `specs/units/*.yaml`）。master 里 FP32ADDTREE5TO1 **只有 VALID**（连 smc_id_en 都没）→ 实际 emit 的 body 只有 1 bit VALID + padding。**故修必落 master**；只改 modules.yaml/units yaml 会被覆盖（已亲测：改后 loader 仍只见 VALID）。

## Decision

### D1. master spec 补全 FP32ADDTREE5TO1 微指令为表 517 的 3 字段
`specs/aiacc_v2.4_master.yaml` FP32ADDTREE5TO1 `micro_instructions` = `[VALID(1), accumulate(1), smc_id_en(5)]`，顺序严格按表 517（vld→accumulate→smc_id_en，低位→高位）。同步 `modules.yaml`（code_bits 6→7 + code_fields 三字段）与 `specs/units/fp32addtree5to1.yaml`（micro_instructions）保持三处一致（虽 loader 只认 master，一致避免后续混淆）。

### D2. `cumulative` → `accumulate` 编码桥
`src/driver/payload_factory.py`：addtree body 的 `ACCUMULATE` 字段 = DFG 的 `cumulative`（特例桥，类比既有 SUB/OP_TYPE 处理）。MIROp metadata 用 `cumulative`(bool)，RTL/code_fields 用 `accumulate`。matmul / matmul_parallel_n `cumulative=True`→1；matmul_q / matmul_q2 `cumulative=False`→0。

### D3. sim addtree honor accumulate
`sim/smc_simulator/units/fp32_add_tree_unit.py`：按解码的 `accumulate` 位决定 old_i 是否参与 —— `base_d0/d1 = dr_d0/d1 if accumulate else 0`。原实现恒累加（old_i 永不清零）；现 accumulate=0 时本拍 reduce 独立（matmul_q/q2 的非累加显式化，且与 VCS 一致）。缺省 ACCUM(1) 兜底。

## 验收（已实测）
- 4 个 addtree 算子全 exit=0 bit-perfect：matmul / matmul_parallel_n（cumulative=1）、matmul_q / matmul_q2（cumulative=0）。
- emit 校验：matmul addtree body `accumulate=1`、matmul_q2 `accumulate=0`（payload 桥正确）。
- **header 错位修复**：新 emit（body=7）addtree HDR 解码 = `VLD=1, LOOPS, EMBED_LOOPS=1`，**无 JUMP**（user 所见 JUMP 是 6-bit body 的 1-bit 错位伪影，已消）。
- pytest 79 pass / 6 skip；smt-unit 13/13。

## Consequences
- **正面**：固件与 RTL 表 517 对齐（body 7 bit）→ VCS 解码不再错位；`cumulative` 真正进固件（VCS 能区分累加/非累加）；sim 显式 honor accumulate（忠实度↑）。
- **债务 / 待裁定**：
  - **DEBT-029-SMCEN（hco 标：真隐患，非纯文档债，VCS P0 验收门）**：addtree `smc_id_en` 当前 emit=0（master 原本无此字段，恒 0 pad；本次加了字段但 payload 仍 default 0）。表 517 语义 `smc_id_en >= SMC_ID 才使能` → **=0 仅使能 SMC_0**。
    - **sim 结构性无感**：`fp32_add_tree_unit.py` 全文不读 smc_id_en，每 SMC 各自独立跑 addtree（`cluster.py` 每 SMC 一 kernel）→ **sim 8/8 对此 0 增信**，不能为 smc_id_en 正确性背书。
    - **会不会错取决于 RTL**：若 RTL addtree 局部计算受本 body `smc_id_en` 门控（表 517 字面）→ **smc_id_en=0 会让 SMC_1..7 的 addtree 不算、VCS 多-SMC matmul 只有 SMC_0 出结果（bug 级）**；若使能在 SMI/IQ dispatch 层（ADR-022 单 SMI 模型）→ 该 body 字段冗余、=0 无害。本仓任何文件答不了，**必须查 RTL / 跑 VCS**。
    - **裁定（hco）**：与 accumulate 修复正交、pre-existing（未引入未恶化），本次**可 defer**；但**钉为 VCS 多-SMC（N>1）addtree 首跑的 P0 验收门 —— 必须断言 SMC_1..7 的 addtree 实际触发**，不得隐性带过。在拿到 RTL 答案前，盲改 emit=PARAM_SMC_NUM-1 同样是无依据猜测（表 517 未给"compute 是否用 body 字段 gate"），故不本次改。
  - **FP32ADDTREE8TO1 未查**：它是 `VALID+DST_REG(2)+smc_id_en(5)=8`，与 5to1 表不同；是否也缺字段须对它自己的 RTL 表，本仓无，待 user 提供。
  - **still_holds: proposed**：hw_config 契约变更必过 hco；升 Accepted 需 VCS 实测确认 7-bit body + accumulate 语义。

## References
- RTL 表 517（user 2026-06-02，FP32ADDTREE5TO1 上行微指令）
- `src/api/hw_spec.py:153-177`（master spec 覆盖 code_fields 的加载优先级）
- `hw_config/specs/aiacc_v2.4_master.yaml`（权威源，本次主改）/ `hw_config/modules.yaml` / `hw_config/specs/units/fp32addtree5to1.yaml`（同步）
- `src/driver/payload_factory.py`（cumulative→accumulate 桥）/ `sim/smc_simulator/units/fp32_add_tree_unit.py`（honor accumulate）
- [ADR-022](ADR-022-single-smi-global-instruction-stream-model.md)（smc_id_en 寻址语义）
