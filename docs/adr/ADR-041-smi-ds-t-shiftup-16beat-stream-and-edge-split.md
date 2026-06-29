---
adr_id: ADR-041
title: SMI_DS_T 输出 = 16-beat 流 —— SHIFTUP/UR_W0 改持续广播 + SMI_DS_T 边权拆「首 beat latency / 窗口 16」（修 unit_ds_transpose bank5=0）
decided: 2026-06-29
last_verified: 2026-06-29
still_holds: DRAFT（BLOCK-1 已落地 2026-06-29：SMI_DS_T=19 + WINDOW=16；剩 BLOCK-2/3 闭合后升 proposed）
builds_on: ADR-038, ADR-039, ADR-040
implements: ADR-040 DEBT-040-DST-EDGE-SEMANTICS
hco_review: APPROVE-方向（2026-06-29）—— 3 BLOCK（verifier 同步 / 门控按 producer / exact 对齐）必闭才可实现
---

# ADR-041 · SMI_DS_T 输出 16-beat 流 + SHIFTUP 持续广播 + 边权拆分

> **状态：DRAFT**（hco 方向 APPROVE，但 **3 个 BLOCK 必须闭合才可从 DRAFT 升 proposed→实现**）。
> 动 `hw_config/pipeline_delays.yaml` + `src/synthesis` 边权语义 + emit 节点生成 → 已过 hco 一轮。
>
> **一句话**：RTL+VCS 实证（[SMI_DS_T datapath 时序](../spec/smi-ds-t-datapath-timing.md)）——SMI_DS_T 一条配对
> 指令内部自动吐 **16-beat 流**，但编译器把 `ds_transpose→shiftup_ds_t→ur-w0` 建成**单 op 单拍**、且把「窗口
> 长度 16」当 RAW 边权 → SHIFTUP 单脉冲排到窗口尾后 11 拍取空 `ds_t_data_out` → bank5=0。修 = ① SHIFTUP/
> RTOVR/UR_W0 改 16-beat 持续流；② SMI_DS_T 边权拆「首 beat latency（RAW 边）/ 窗口 16（广播持续+lifecycle）」。

## Context

实测链（`tb_dst.vcd`，clk=10ns，详见 spec 文档）：`ds_transpose` SMT t=3 / `shiftup_ds_t` t=19（差恒 16 =
`add_latency_axiom` 施加 SMI_DS_T latency）；RTL `data_out_vld` 窗口 cyc662–677（16 拍），`o_cru_shiftup` 单脉冲
@cyc689（窗口尾后）。collect 已核查**无 bug**（前 12 零 beat 属数据正确性轴，ADR-040 §核查）。

RTL 关键事实（hco 复核）：
- `smi_ds_t.v:58` `LAST_BEAT=OUT_ROWS×2`、`:165-205` 逐拍 `out_vld_d→data_out`（16-beat 流）；`OUT_ROWS=LINE_BYTE/ELEM_BYTE`（跟元素宽度走，fp32→8 beat）。
- **`shift_up.v:50-60` 是透明 1 拍寄存器**（`dr_shiftup_reg<=data_in`，无内部 beat 计数/取数）→ 只要 `vld_in` 持续 16 拍、`data_in`（硬连 `ds_t.data_out`）逐拍变，就逐拍广播不同 beat。**16-beat 流 RTL 原生支持，不改 RTL。**

## Decision（hco APPROVE-方向 + user 裁 D4；3 BLOCK 标注必闭）

### D1. SMI_DS_T 边权拆两量〔hco ✓〕
`SMI_DS_T=16` 拆为 **`SMI_DS_T_FIRST_BEAT`**（首 beat 就绪 latency，用于 `add_latency_axiom` 的 ds_transpose→SHIFTUP
**RAW 边**）+ **`SMI_DS_T_WINDOW=OUT_ROWS×2`**（SHIFTUP/UR_W0 广播持续拍数 + RTOVR latch lifecycle / 资源占用）。
- 🔴 **BLOCK-1（必修）**：`add_latency_axiom`（`dependency_axioms.py:37,39`）+ counter-verifier 三点
  （`ucode_solver.py:232,265,376`，`delays.get` 通用读）必须**同步取同一 FIRST_BEAT 值**；保留 `SMI_DS_T` 为
  FIRST_BEAT 别名/显式 map，防 `delays.get` 落默认 1（ADR-021「axiom/verifier 必须同步」同类故障）。
- 波及面已查清：仅上述 4 点 + `s1_smt_check.py:55`（诊断）；`system_core/aiacc_v2.4_master.yaml` 的 `SMI_DS_T`
  是 **IQ count / base-id 命名空间、非 delays**，不动。

### D2. SHIFTUP/RTOVR/UR_W0 改 16-beat 流〔hco ✓ 选 A〕
**选项 A（LOOPS 展开）**：保留单节点、加 `stream_beats=WINDOW`，emit 展开成 `LOOPS=WINDOW` 连续发射
（复用 `ur-r0` LOOPS=16 先例 + `assembler.py:95-101` IQ 级循环覆盖）。SHIFTUP `INC=0`（data 硬连）、UR_W0 `INC=1`（连续行）。
- 🔴 **BLOCK-2（必修，硬约束）**：`stream_beats` 门控**必须按 producer.op_type==SMI_DS_T**（gate on
  `dst_datapath_value` + producer 为 SMI_DS_T），**绝不能按 `op_type==SHIFTUP && broadcast==1`**——后者命中
  fma/matmul compute-broadcast SHIFTUP（`dependency_axioms.py:88-92` resident 谓词同款）→ 把 fma 单值广播展成
  16 拍流、打爆 fma 8/8。门控来源 = `cross_smc_unit_op.py:183-185` 建 ds datapath 节点时打标。
- 条件：emit 须产**同一 II 窗口内连续拍（DLY=1 back-to-back）**，非跨迭代 strided。

### D3. 时序分三量〔实测订正 hco 的 FIRST_BEAT=11 估值〕
VCS 实测（`tb_dst.vcd`：`cri_vld` issue@cyc641/643 配对、`data_out_vld` 窗口开@662、首真数据@674）→ **issue→窗口开
= 21 拍**（非 hco 估的 11）。分三量：
- **collect = WR_BEATS × (配对×B 间插) ≈ 16–18 拍**（hco 估 8 偏低——漏了**两配对 unit 串行 + B=2 间插气泡**：u0
  `wp`0→7@cyc643–650、u1@651–658）。**随 B/配对漂移**，故 hco「不折叠进 FIRST_BEAT」的原则**更该坚持**——collect
  建在 `ds_gather/ds_gather_1 → ds_transpose` 的数据流边（gather 齐），不进 FIRST_BEAT。
- **`SMI_DS_T_FIRST_BEAT`(drain) = ready → data_out 窗口开 ≈ 3 拍**，SMI_DS_T 内部常数、不随 B。
- **`SMI_DS_T_WINDOW` = OUT_ROWS×2 = 16**。
- ✅ **hco 二轮裁定（2026-06-29，RTL 取证 + VCS 全链对账 `cri_vld@641 + gather_fill2 + collect16 + drain3 = 662`）**：
  - collect = **B×WR_BEATS = 2×8 = 16 确定拍**（连续供数地板；**与 PARAM_SMC_NUM 和元素宽度(fp16/fp32)无关**，
    仅随 `TILE_BYTES/B`——`WR_BEATS=TILE_BYTES/16` 用字节数，元素宽度只改 OUT_ROWS/WINDOW。订正上文「16–18 漂移」）。
  - 选 **(ii) 节点延迟**：collect+drain 做成 `delays["SMI_DS_T"]=19`（gather_fill≈2 归 SHIFTDN 边、不进节点），
    SHIFTDN 边不动；axiom(`:37/:39`)+verifier(`:232/:265/:376`) 按 producer op_type **自动同步取 19** → **BLOCK-1 零结构改动**。
    （(i) 落 SHIFTDN 边会污染 fma/matmul 所有 SHIFTDN + 重蹈 ADR-021 fold，否决。）
  - drain = **3 寄存器级**（`smi_ds_t.v:88/167/134/179`，VCS 658→662 对账）。
- ✅ **BLOCK-1 已落地（2026-06-29）**：`pipeline_delays.yaml` SMI_DS_T 16→**19** + 新增 `SMI_DS_T_WINDOW=16`；
  fp16/fp32 e2e II=2 SAT + sim 8/8 bit-perfect + pytest 全过（`test_smi_ds_t_e2e` drain 守恒断言 16→19 同步）。
  边类型确认 ✓：`shiftup_ds_t` 走 `depends_on`(直连 RAW)、非 rtovr_deps（`cross_smc_unit_op.py:183`）。
  **剩 BLOCK-2（stream_beats 门控按 producer）+ BLOCK-3（D5 emit EXACT 锚定）未落。**

### D4. 完整 16-beat 流〔user 2026-06-29 裁定 = 写 16 行〕
fp16 **不走 1 词特例，走完整 16-beat 流**（与 fp32/matmul 统一）：
- fp16 输入从 1 元素/SMC **扩到 8 元素/SMC**（每 SMC 一整行 16 字节）；
- golden 从 1 词 **改 16 行**（配对 2 指令各 8 行转置）；UR_W0 `INC` 写 16 连续行。
- → 改 `cross_smc_unit_op.py` `UnitDataShuffleTransposeFp16Operator` 的 `prepare_input`/`get_golden`/
  `inject_data_per_smc`（行240-258）+ `bcast` `inner_loops`（行196）。

### D5. exact 对齐验收〔hco BLOCK-3，必修〕
SHIFTUP vld 与 `data_out` 窗口是 **EXACT 对齐**（`shift_up` 无握手，早/晚 1 拍即取窗口外零 = bank5=0 机理）。
而 `add_latency_axiom`（`:43`）是 `>=` 下界、SMT 可加 slack 排晚 → 重新错窗。
- 🔴 **BLOCK-3（必修）**：① FIRST_BEAT 取 RTL 精确值；② **emit 锚定**发射拍 = ds_transpose issue + FIRST_BEAT
  （恰好相等，非 SMT 松弛解）；③ 16 拍 vld 一窗内连续 DLY=1。新增验收：**SHIFTUP 流首拍 == data_out 窗口首 beat**（方案 C 逐拍验）。

## 待厘清
- `hw_spec.py:44` `simulation_only`「SMI_DS_T 不进 SMT 调度」是 matmul model-A 语境 **stale**；ds datapath 的
  `ds_transpose` 是真调度节点（spec 文档 §4 三层 t-diff=16 实证），D1 落地顺手厘清。
- `WINDOW` 新概念需接 emit(LOOPS) + **SHIFTUP/UR_W0 16 拍占用进 modulo/resource 约束**（否则 matmul loop 场景与下一迭代发射撞 slot）。

## 风险 / 债务
- **DEBT-041-ADDR-MAP**：16-beat→bank5 地址映射 + golden 16 行真值（含前导零 beat）；
- 回归：fma/matmul_parallel_n 走 `_inject_shiftup_broadcast`（广播相消路径），由 **BLOCK-2 门控**隔离守住。

## 验证 + 测试（升 still_holds 前置）
- `test_smt_adversarial`：① ds_transpose→SHIFTUP RAW 边 = FIRST_BEAT（非 16）+ 排程拍 exact 对齐；
  ② 可证伪：fma broadcast SHIFTUP **未被** `stream_beats` 命中、ds_transpose-fed **被**命中（守 BLOCK-2）。
- VCS `unit_ds_transpose_fp16`（方案 C 逐拍）：`o_cru_shiftup` 起拍对齐 `data_out` 窗口首 beat、持续 16 拍非零
  payload → bank5 dina 非零 + golden 16 行 bit-match。

## still_holds 阶梯
DRAFT（本稿，3 BLOCK 未闭）→ proposed（BLOCK-1/2/3 实现且 pytest 不破）→ Accepted（VCS unit_ds_transpose_fp16 bank5 非零 + golden 16 行 bit-match）。
