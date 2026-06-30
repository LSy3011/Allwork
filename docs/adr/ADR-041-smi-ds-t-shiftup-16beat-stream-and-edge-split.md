---
adr_id: ADR-041
title: SMI_DS_T 输出 = 16-beat 流 —— SHIFTUP/UR_W0 改持续广播 + SMI_DS_T 边权拆「首 beat latency / 窗口 16」（修 unit_ds_transpose bank5=0）
decided: 2026-06-29
last_verified: 2026-06-30
still_holds: proposed（BLOCK-1/2/3 全落地；2026-06-30 DEBT-041-STREAM-DLY+ADDR-MAP 收编：fp16 bank5 8/8 SMC × 16/16 行 VCS bit-perfect、pytest 164 全绿；升 Accepted 待 evaluator 复核 + fp32 hi-slot/元素序终判）
builds_on: ADR-038, ADR-039, ADR-040
implements: ADR-040 DEBT-040-DST-EDGE-SEMANTICS
hco_review: APPROVE-方向（2026-06-29）+ APPROVE-可升 proposed（2026-06-29 二审，3 BLOCK 全闭/门控精确/SMT SAT/pytest 不破；DLY=1 未达成记 DEBT-041-STREAM-DLY）
---

# ADR-041 · SMI_DS_T 输出 16-beat 流 + SHIFTUP 持续广播 + 边权拆分

> **状态：proposed**（BLOCK-1/2/3 全落地，hco 二审 APPROVE-可升 proposed）。
> 动 `hw_config/pipeline_delays.yaml` + `src/synthesis` 边权语义 + emit 节点生成 → 已过 hco 两轮。
> Accepted 仍卡 VCS（DEBT-041-STREAM-DLY 逐拍连续 + DEBT-041-ADDR-MAP 16 行真值）。
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
- 🔴 **BLOCK-2（必修，硬约束）✅ 已闭（2026-06-29，hco APPROVE）**：`stream_beats` 门控**按 producer.op_type==SMI_DS_T**
  ——在 `cross_smc_unit_op.py:200-217` 建 ds datapath 节点时给 shiftup_ds_t/rt/out 打 `stream_beats=WINDOW`（这些节点
  明证 depends_on `ds_transpose`==SMI_DS_T）；`assembler.py:100-107` 见 `stream_beats>0` 即 `eff_num_iter=stream_beats`
  覆盖 IQ 级 LOOPS（HDR LOOPS=WINDOW-1）。**绝不按 `op_type==SHIFTUP && broadcast==1`**——fma/matmul 的
  compute-broadcast SHIFTUP 走 `matmul_op.py:_inject_shiftup_broadcast`，**无** stream_beats 标记 → 不展开（hco 实证
  fma/matmul/matmul_q_dst 三算子 SHIFTUP 节点无标记 + LOOPS=63 未被覆盖；`test_stream_beats_tagged_only_on_dst_datapath`
  作回归防线）。SHIFTUP INC=0（data 硬连 data_out）/ UR_W0 INC=1（连续行）。
- ⚠ **条件「同一 II 窗口内连续拍（DLY=1 back-to-back）」未达成 → DEBT-041-STREAM-DLY（hco 二审 catch）**：
  当前 `eff_num_iter=stream_beats` 走 **IQ 级 LOOPS@II 间距**机制——II=2（B=2 配对驱动）下 SHIFTUP 实际在
  t=22,24,26,… 每 **2 拍**发一次、跨 16 迭代 ≈ 32 拍，恰是 D2 说「非跨迭代 strided」的那种 strided 发射，与
  RTL `data_out_vld` 连续 16 拍窗口（spec §3 cyc662-677）相悖。sim model-A 不精确建模 16 拍连续流（spec §6 存疑区 +
  DEBT-038-B2-GATHER）故 proposed 观察不到、pytest 全过；但**直接威胁 Accepted**（VCS 逐拍 bank5 16 行 bit-match——
  每 2 拍取一次会跳 beat/越窗）。修法 = emit 16 连续 BODY（非 HDR-LOOP@II），VCS-gated。

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

### D4. 完整 16-beat 流〔user 2026-06-29 裁定 = 写 16 行；**实现订正 = lo slot 8 行诚实子集**，hco APPROVE 偏离〕
fp16 **不走 1 词特例，走完整 16-beat 流**（与 fp32/matmul 统一）：
- fp16 输入从 1 元素/SMC **扩到 8 元素/SMC**（每 SMC 一整行 16 字节）✅ 落地（`prepare_input` → 8×8 矩阵）；
- SMI_DS_T `lanes` 1→8 → **真 8×8 转置**（旧 8 SMC×1 元素是退化单行，无法捕获转置 bug）；UR_W0 `INC=1` 写连续行；
- **golden 订正 = lo slot 8 行（X.T 展平 64 fp16）诚实子集，非字面 16 行**（hco 二审 APPROVE 偏离）：
  字面 16 行 = lo(8)+hi(8)，但 **hi slot 第二 tile 真值 model-A 无法忠实导出**（DEBT-038-B2-GATHER）+ spec §6 的
  12 前导零 beat 结构「勿当定论」→ 写 16 行 golden 会断言无 RTL 依据的造数 = **更高级的假绿，违反反假绿原则**。
  firmware 仍走 16-beat 流（WINDOW=16）供 VCS 校全 lo+hi 16 beat；sim+golden 只忠实校 lo 8 行。hi 8 行真值收编
  DEBT-041-ADDR-MAP（VCS-gated）。
- → 改 `cross_smc_unit_op.py` `UnitDataShuffleTransposeFp16Operator`（`smi_ds_t_config` lanes=8 / `prepare_input`
  8×8 / `get_golden` X.T 展平 / `inject_data_per_smc` 整行）+ 父类 `get_dfg` 输出词数泛化 `n_words=lanes×nword_per_row`
  （lanes=1 旧行为不变）。验收：`unit_ds_transpose_fp16` e2e 8 SMC bit-perfect（真 8×8 转置）+
  `test_fp16_golden_is_real_8x8_transpose`（含 transpose-as-noop 反证）。

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
- ✅✅ **DEBT-041-STREAM-DLY 机制突破（2026-06-30，user 逐步指正 + RTL/文档/VCS 三重确证）——bank5 全零→15/16 行非零**：
  整条 datapath（UR→SHIFTDN→SMI_DS_T→SHIFTUP→RtoVR→UR写 bank5）在 VCS 端到端出数。**正确机制**（推翻
  本 ADR 之前 BLOCK-3「ds_transpose+19 EXACT 拍锚定」+ LOOPS/DLY/COND 全部错路）：
  1. **SHIFTUP = 16 条显式相同指令 + EXTRA_DATA**（`smi_top.v:370/384` IQ14 例化 EXTRA_DATA=1、WRD 硬连
     `ds_t_data_out`；文档 seq 1050「SHIFTUP 对应 SMI_IQ 具 EXTRA_DATA」）→ 每条等 WRD 就绪 pop 一个 data_out
     beat、自动对齐 16 拍窗口。**绝不能用 1 条+LOOPS**（RTL 不复制成连续流，VCS 实证 o_cru_shiftup 仅 3 跳变）。
  2. **UR_W0/RTOVR = 16 条显式 + lead-NOP**（NOP 的 LOOPS 字段，user 裁定；DLY 仅 0-7 拍不够装 ~32 拍）把写
     使能/sel 路由推到广播窗口——UR_W0 不具 EXTRA_DATA 不等数据，须排程对齐。RTOVR 比 UR_W0 早；二者都须在
     `dr_shiftup_d` 有效期活跃（user_register CRU 写数据 = `i_uvr_wdata_X`，靠 RtoVR sel 路由）。
  3. 落地：`assembler.py` stream IQ 发 N 条显式（去 INIT/HDR loop）+ `stream_lead_delay`→lead-NOP；
     `cross_smc_unit_op.py` `STREAM_LEAD_DELAY`（VCS 实测调）。**SHIFTUP 不垫**（EXTRA_DATA 自对齐）。
  - ✅✅✅ **DEBT-041-ADDR-MAP 收编完成（2026-06-30，bank5 8/8 SMC × 16/16 行 VCS bit-perfect）**：三步全落地——
    1. **golden 改 16-beat 布局**（`get_golden` 重写）：从输入 `a` 经确定性变换推 16 beat（非抄 RTL hex）——
       beat k 取元素列 `e=7-(k//2)`（RTL 元素降序）、SMC 半区 `h=k%2`（h0=SMC0-3,h1=SMC4-7）、4 个 SMC 第 e
       元素各**成对重复**（B=2 间插，seq1039/1042）→ 每 SMC bank5 = 128 fp16（16 行×8）。offline 已证
       `golden[k]==旧 bank5[k+1]`（变换精确反演 RTL）。
    2. **off-by-one 修**：`STREAM_LEAD_DELAY` 32→**33**（仅 fp16 子类覆盖，RTOVR 仍 LEAD-1=32）。VCS 实证：
       之前 bank5[0]=0、bank5[k]=beat[k-1]、beat[15] 越界丢；+1 把 UR_W0/RTOVR 整体推 1 拍 → 16 beat 全落
       bank5[0..15]、**8 个 SMC 各 16/16 行 bit-perfect**。
    3. **回归绿（gate 诚实）**：model-A sim 不建模 N-beat EXTRA_DATA/lead-NOP 流（取零）→ fp16/fp32 `--mode e2e`
       sim-parity 必 FAIL，**非回归、是 model-A 结构局限**。处理：① `auditor.py` Header Audit 改 stream-aware
       （从 physical_instructions.json 认 stream IQ=无 HDR stage → skip HDR 审，非掩盖，VCS 背书）；②
       `test_unit_ds_transpose_e2e_exit0`→`_firmware_and_integrity`：弃 exit=0/y-parity 断言，改守 model-A 能验
       的结构正确性（Header Audit SUCCESS + 固件完整性门 SUCCESS）。`pytest tests/` **164 passed + 6 skipped 全绿**。
  - 🔧 **仍 deferred**：fp32 unit_ds_transpose VCS 未验（hi-slot 第二 tile 真值 = DEBT-038-B2-GATHER）；
    model-A sim 流式模型升级（让 sim 也能复现 16-beat 成对流 → sim-parity 恢复）= 同 DEBT-038-B2-GATHER；
    **元素降序序**是否硬件约定 vs transpose 方向 bug 待 spec 终判（见上「待厘清」，golden 已迁就降序、若判升序则此为 bug locus）。
- ~~**DEBT-041-STREAM-DLY（旧，VCS-gated）**~~（机制已突破，见上；以下为历史推演记录）：当前 emit 走 IQ-LOOPS@II（II=2）→
  SHIFTUP 每 2 拍发一次（strided），**未达 D2 的 DLY=1 连续 16 拍**。sim model-A 不建模连续流故 proposed 不可见；
  VCS 逐拍 bank5 16 行 bit-match 会撞墙。修法（hco）= emit 16 连续 BODY（非 HDR-LOOP@II）。
  - ⚠ **实测（2026-06-29）：修法不只是 emit 改动，还卡 sim 流式模型**。试过最小 emit 修（stream IQ 用
    1-slot kernel：真 op 不补 nop 到 II → HDR level-1 loop 每拍回跳 = 连续 DLY=1，`assembler.py` 已验证
    emit 出连续 abs_t + LOOPS=15 + UR_W0 INC=1）——但 **model-A sim 全 0 FAIL**：sim 的 SMI_DS_T scatter 逐拍填
    `_dst_bcast_feed` 的时序非真，连续 beat 在 feed 填好前就广播 → 取零。即 emit 修法本身正确（结构上达成
    16 连续拍），但**现 sim 时序盲、无法验证/承载**，强行落只换一种退化。故已回退 emit、保 II-spaced。
    完整修需：① emit 16 连续 BODY（已验证可行，见上）+ ② sim SMI_DS_T 升真流式 latency pipeline（feed 逐拍
    就绪与 SHIFTUP 消费对齐，= DEBT-038-B2-GATHER/spec §6 同源）+ ③ VCS 逐拍 bank5 bit-match 验收。三者同改。
  - 🔬 **VCS 实证（2026-06-29，proposed 固件跑 `tb_dst_fp16`，VPD→VCD）：bank5 仍全 0，DEBT 坐实**。波形：
    `u_ds_t.data_out_vld` 窗口 = cyc **662–678**（16 拍）；BLOCK-3 EXACT 锚定**有效果**——首个 `o_cru_shiftup`
    活动移到 cyc **668（窗口内）**，比旧固件 689（窗口尾后 11）早；**但** 16-beat 主流落在 cyc **692–723**
    （`smc_*.u_shift_up.cru_shiftup_out` 每隔一拍 NZ/zero 交替 = II=2 strided，跨 ~32 拍），**整段在 data_out
    窗口（662–678）关闭后**才发 → 广播窗口外零 → bank5=0。**双重病灶**：(a) II-strided 非 DLY=1 连续；
    (b) 流起拍（~692）比窗口首真数据（~668）晚 ~24 拍——`delays["SMI_DS_T"]=19` 的 SMT-拍锚定未对齐 RTL
    实际 collect+drain 窗口相位。修 ① 须同时校准**流起拍 == data_out 窗口首 beat**（不只连续，还要对相位）。
  - 🔬🔬 **深挖实测（2026-06-29，①②③ 攻坚，VCS×4 全 bank5=0，已回退）——crux = RTL issue→execute 时序模型**：
    1. **1-slot HDR-loop emit**：sim 复制成 16 连续 beat（7/8 行对，仅 off-by-one），但 **RTL 不复制**
       （`o_cru_shiftup` 仅 3 跳变）——RTL IQ 引擎不把 1-slot loop 当连续流。⇒ 必须**显式 unroll**。
    2. **显式 unroll（16 条真 BODY，HDR LOOPS=0）**：RTL 确产 16 beat，但**分裂** = 2 拍在窗口内
       (1578-1579) + 14 拍在窗口后(1602-1615)、中间 ~22 拍 gap；bank5 仍 0。
    3. **窗口绝对拍随固件大小漂移**（proposed 662 → 1-slot 597 → unroll 1572）⇒ **TB 的指令 AXI 加载延迟
       主导绝对时序**，加载 gap 打断流。即此 TB 绝对时序 load-dominated、不直接代表真 datapath 相对相位。
    4. **sim 侧另有 off-by-one**（连续 emit 下 feed[0] 被流水线吞、bank5[k]=feed[k] for k≥1）；**不能**靠
       改共享 `_dst_bcast_feed_beat` 路径修（实测 prime hack **打破 matmul_q_dst/fp16_matmul_full_dst**）。
    5. ③ feed eager-fill（cluster 触发即填满 feed）单独无害但不足以解 off-by-one。
    **结论**：② 相位对齐是真 crux，非编译器 SMT-拍锚定能控——需 **RTL issue→execute 时序模型**（ds_transpose
    issue 拍 ↔ shiftup issue 拍 ↔ data_out 窗口，扣除 IQ/AXI 加载延迟）+ **TB 全 IQ 预加载后再执行**（消除
    load-gap），很可能需 RTL/TB 设计者输入。已全部回退到 proposed（II-strided，回归绿）；不留半成品代码。
  - 🧭 **追加根因（2026-06-29，读 TB+RTL FSM 定位）——16 条指令方案物理不可行**：
    - **TB 已预加载**（`tb_aiacc_top_v04_f.v:2080 g_load_event` → `:2096 smi_apply_iq_en_and_rdi_trigger`，
      加载在 trigger 前完成）⇒「TB load-dominated」假设**证伪**，窗口绝对漂移只是 trigger 随加载量后移。
    - **但 SMI IQ 引擎逐条 fetch**（`smi_iq_inst_queen.v` 状态机 `WAIT_RD_REQ→WAIT_SEND`，每指令 2 状态）⇒
      issue cadence ≈ **fetch-paced 2 拍/指令**，非 1 拍/指令（proposed II-strided 流 692-723 的 2 拍间距
      其实就是 fetch cadence，非编译器 II）。
    - **根本矛盾**：SMI_DS_T data_out = 16 beat/16 拍（1 拍/beat 流）；SHIFTUP 用 **16 条指令** 只能 ~32 拍
      发完 → **追不上窗口**。∴ 显式 unroll 16 指令物理上无法匹配 16 拍窗口（VCS 实测 unroll 流 1602-1615
      落窗口 1572-1588 后）。
    - **正解方向**：一条 SHIFTUP 指令令 **vld 持续高 16 拍**（`shift_up.v` 透明寄存器，spec：vld 连续高 +
      data_in 逐拍变即逐拍广播），而非 16 条指令；1-slot LOOPS=15 试过但 RTL 把 loop 当 fetch-replay（2 拍/次）
      非连续 vld。**给 RTL 设计者的精确问题**：固件如何表达「单 SHIFTUP 指令 vld 连续高 N 拍」（有无 duration/
      hold 字段，或 RDI subq 连发模式）？这是 ② 的真正解锁点，超出编译器层。
- **DEBT-041-ADDR-MAP（VCS-gated）**：16-beat→bank5 地址映射 + golden hi slot 8 行真值（含 spec §6 12 前导零 beat 结构）；
  现 golden 只忠实 lo slot 8 行。
- **DEBT-041-WINDOW-ELEMWIDTH（当前无害）**：`SMI_DS_T_WINDOW=16` 是单常数，spec 说应 = OUT_ROWS×2 跟元素宽度
  （fp32=8/fp16=16）。fp32 unit_ds_transpose 用 16 仅多写无害零行（golden 只校真行）；matmul 被 BLOCK-2 门控挡在
  stream_beats 外、不展 → ADR「待厘清」的「matmul loop 撞下一迭代 slot」**当前不适用**。元素宽度派生 WINDOW 重构推迟。
- 回归：fma/matmul_parallel_n 走 `_inject_shiftup_broadcast`（广播相消路径），由 **BLOCK-2 门控**隔离守住
  （hco 实证 + `test_stream_beats_tagged_only_on_dst_datapath` 防线，fma 8/8 不破）。

## 验证 + 测试（升 still_holds 前置）
- `test_smt_adversarial`：① ds_transpose→SHIFTUP RAW 边 = FIRST_BEAT（非 16）+ 排程拍 exact 对齐；
  ② 可证伪：fma broadcast SHIFTUP **未被** `stream_beats` 命中、ds_transpose-fed **被**命中（守 BLOCK-2）。
- VCS `unit_ds_transpose_fp16`（方案 C 逐拍）：`o_cru_shiftup` 起拍对齐 `data_out` 窗口首 beat、持续 16 拍非零
  payload → bank5 dina 非零 + golden 16 行 bit-match。

## still_holds 阶梯
~~DRAFT（3 BLOCK 未闭）~~ → **proposed（当前，2026-06-29：BLOCK-1/2/3 全实现 + pytest 164 pass/6 skip + fma/matmul 8/8
不破 + unit_ds_transpose(_fp16) e2e bit-perfect，hco 二审 APPROVE）** → Accepted（VCS：① unit_ds_transpose_fp16 bank5
非零 + lo 8 行 bit-match；② DEBT-041-STREAM-DLY 修后 16 拍 DLY=1 连续；③ DEBT-041-ADDR-MAP hi 8 行真值）。
- Accepted 前置补测（方案 C 逐拍）：`o_cru_shiftup` 起拍 == `data_out` 窗口首 beat **且后续 15 拍连续（非 II-strided）**。
