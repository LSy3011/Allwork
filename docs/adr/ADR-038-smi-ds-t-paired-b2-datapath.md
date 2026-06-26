---
adr_id: ADR-038
title: SMI_DS_T 必须成对 B=2 交织发射（方案 C VCS PoC 证实 model-A 单指令是 load-bearing 功能 bug；supersede ADR-033/034/036 单指令简化）
decided: 2026-06-26
last_verified: 2026-06-26
still_holds: proposed
builds_on: ADR-022, ADR-023, ADR-033, ADR-034, ADR-036
supersedes_partial: ADR-033, ADR-034, ADR-036  # 三者把 SMI_DS_T 建成 intlv_en=0 单指令单 tile（Stage-I model-A 简化）；本 ADR 推翻为配对 B=2，保留它们的转置数值/拓扑/流式结论
---

# ADR-038 · SMI_DS_T 必须成对 B=2 交织发射

> **状态：Proposed（doc-first per ADR-004；改 emitter/DFG/sim 流水线接口 + supersede SMI_DS_T datapath 契约 → 必过 hco。已过 hco：BLOCK + 修复方向 APPROVE-with-conditions）**。
>
> **一句话**：方案 C 时序验证 PoC 用 VCS 波形抓到——`unit_ds_transpose` 等全部 5 个 SMI_DS_T 算子的固件
> 发**单条** SMI_DS_T（`intlv_en=0`），但 V1.0 RTL `smi_ds_t.v` 是**无条件配对引擎**（`start_out` 要 `issue_cnt==2`
> + lo/hi 两 slot 都 ready），单指令永远不出数 → **输出全 0**。sim 8/8 是 model-A 自洽假绿，对 RTL 忠实度为零。
> 修复 = compiler 发**成对 T0+T1 + B=2 双 gather** 填 lo/hi 两 slot。**这是纯 compiler 修，RTL 无 bug，不需新 axiom。**

## Context

### 起源：方案 C 时序验证 PoC（[[timing-verify-strategy-plan-c]]）
承 `docs/hardware_timing_model_DRAFT.md`：AIACC 是时序确定性架构，编译器核心交付物 = 时序排布，
**验证必须验时序、不能只验终值**。方案 C = 编译器逐拍期望时间线 vs VCS 实测波形逐事件 diff。
工具落地 `verification_v2/tools/timing_poc/`（vcd_parser + extract_ur_writes + timing_diff），
免 Verdi 跑 VCS（UCLI `dump -type VPD` → `vpd2vcd` → 纯文本 VCD）。

- **known-good `unit_fpadd`**：值+时序全绿，**SMC 串行偏移实测 [0..7] = 时序模型预测**（串行 SMC 传播首次 VCS 波形级证据）。
- **cross-SMC `unit_ds_transpose`**：抓到第一个真 bug（本 ADR 主题）。

### 症状
`unit_ds_transpose` VCS **FAIL 8/8，输出 bank5 全 0**（golden 2 行非零 `3f88764f3f94fa7b400510be40630fec` / `407ac049...`）。
排除 staging：输入 bank4 在 RTL 已正确加载（= golden）。

### 根因链（VCD 实测 + 只读 RTL 确认，均未改代码）
RTL `aiacc_rtl/smi/smi_ds_t.v`：

```
start_out = both_issued & both_open & lo_ready & hi_ready & ~out_phase    // line 88
both_issued = (issue_cnt == 2'd2)                                          // line 81
```

| 信号（VCD scope `dut.u_smi_top.u_ds_t.*`） | 实测 | 含义 |
|---|---|---|
| `cri_ds_t` | t=5505000 非零 | SMI_DS_T 指令发射、CRI 解码到位 |
| `session_active` / `issue_cnt` | →2 | 引擎启动、两次 issue |
| `both_open` / `lo_ready`(br0) | =1 | 两 slot 开、低 slot 收满 |
| **`hi_ready`(br1)** | **永 0** | **高 slot 从未 ready** |
| `crd_vld`（gather 输入有效） | 仅 1 上升沿 | gather 只送一个 slot 的数据 |
| `start_out` / `out_phase` / `data_out_vld` | 永 0 | 输出阶段从不启动 → `data_out`=0 |
| `u_ds_t.crd_data`（内部输入） | cyc551=`40630fec` 等 | 转置数值通路本身正确（golden line0 逆序、每 2 拍一字、bits[31:0]） |

→ 转置内部已收到正确数据，但**配对/输出门**卡死：单 gather 只填 lo slot、hi slot 饿死 → `hi_ready=0` → `start_out` 永 0 → 不发射。

### Compiler 侧固件（`build/verification_v2/unit_ds_transpose/`）
- 只发**单条** `iq_smi-ds-t-0.hex`（无配对 `-1`）+ 单条 `ds_gather`（shiftdn）。
- `compiler_schedule.json`：`ds_gather`(shiftdn,t=2) + `ds_transpose`(smi_ds_t-0,t=3) 各一。
- SMI_DS_T BODY payload `{VALID:1, INTLV_EN:0, INTLV_GRP:0, INTLV_IN:0, INTLV_OUT:0}` = 单指令非交织。

## Decision（hco 裁决）

### 1) ISA v0.4.19 SMI_DS_T 权威语义 = 矩阵×矩阵转置**必须成对（B=2 交织）**
- 表598 原文（docx para4649）："**目前每个组为两个指令**，具有相同的组编号的两个指令属于同一组，**且 intlv_en 必定为 1，intlv_in 和 intlv_out 至少一个为 1**，可以两个都为 1。"
- para5143（matmul 激活读取）："**连续发出两个读指令**，由于读取流水线的构造，**当 B=2 的时候**，读回的数据…恰好是…连续的 `B*(PARAM_UR_BYTE_CNT/ACT)*PARAM_SMC_NUM` 个数据。"
- RTL `smi_ds_t.v` 与此一致：`PARAM_INST_NUM=4` = 2 组×2 槽；输出 `LAST_BEAT+1=OUT_ROWS*2=16` 拍 = 2 tile；无单 tile 输出路径。

### 2) Q1 = V1.0 RTL 无单指令模式（纯 RTL 事实，确定）
- `smi_ds_t.v` 解码只有 `cri_ds_t[0]=vld / [2]=intlv_grp / [3]=intlv_in / [4]=intlv_out`；**`cri_ds_t[1]` (intlv_en) 全程不解码**（grep 零命中）。
- `start_out` 无条件要 `issue_cnt==2`，无 issue_cnt==1 出数路径。
- → **V1.0 操作上永远配对，RTL 无 bug**。ISA 表598 的 intlv_en 位对 V1.0 是 spec-intent 余量，硅就是配对-only。

### 3) Q2 = intlv 组合是**逐算子的输出存储格式选择**，非 matmul 通用常量
RTL 两 bit 精确语义（已读 `smi_ds_t.v`）：

| bit | 作用 | =0 | =1 |
|---|---|---|---|
| `intlv_in` (write-router L100-112) | 输入填两 slot 方式 | 先填满 lo 再 hi | 按 `wr_toggle` lo/hi 间插 |
| `intlv_out` (output-scheduler L64-70) | 输出排序 | 按指令顺序（lo 全行→hi）= **转置连续 `UR_Mat_Tran_Succ`** | lo/hi 行间插 = **转置间插 `UR_Mat_Tran_Inte`** |

- docx para4668："输出…间插输出…**也可以按照指令顺序输出**。" para4719/4720：转置连续 vs 转置间插（典型 B=2）。
- para5130：matmul 矩阵列数小→转置连续，大→转置间插，**由结果向量期望存储方式决定**。
- → 每个 DST 算子按其 golden 存储格式定 intlv；表598 只硬约束 `intlv_en=1` 且 `intlv_in|intlv_out ≥ 1`。
  `unit_ds_transpose` 单测用其 golden 反推钉死；真 matmul 链按 para5176-5216 各输入标注分别设。

### 4) 根因归属 = 纯 compiler bug（model-A 简化债务兑现）
- compiler 把 SMI_DS_T 建成 `intlv_en=0` 单指令单 tile —— 正是 ADR-033/034/036 **Stage-I 显式 defer 的 model-A 简化**
  （DEBT-033-DST-CTRL / DEBT-033-DST-FIELDS / DEBT-036-DST-BCAST-FEED-TIMING 等标 VCS-gated）。
- **本 PoC 证明该 deferred 债务不是时序近似误差，而是 load-bearing 的功能正确性缺口**（输出全 0，非拍数略偏）。
- 因 RTL 不解码 intlv_en 且配对无条件，**任何单指令字段组合都无法让 RTL 出数** → 排除「字段设错走错配对路径」假设。

## BLOCK（hco）

- **[BLOCK]** 配对修复落地前，全部 5 个 SMI_DS_T 算子（`unit_ds_transpose` / `matmul_q_dst` / `matmul_q_dst_k8` /
  `fp16_matmul_q_dst` / `fp16_matmul_full_dst`）的 sim 8/8 是 model-A 自洽假绿，**不得升 Accepted、不得作 RTL 一致性证据**。
- **[BLOCK]** model-A sim `sim/smc_simulator/units/smi_ds_t_unit.py`（一次性转置、无 2-slot 门）必须改成镜像 RTL
  `start_out`（lo+hi 双 ready + issue_cnt==2 才出数），否则下次配对回归仍静默假绿。

## 修复方向（APPROVE-with-conditions）

### 实现期 VCD 确证机制（2026-06-26，追 tb_dst.vcd 锁死）
追 `u_ds_t` 内部信号逐拍（cyc550-560）确证修法，**比 ADR 初稿的"发配对桶"更窄更准**：
- **配对（两次 issue）已发生**：`cri_ds_t=00001` 在 cyc550-551 连两拍 cri_vld=1 → `issue_cnt` 0→1→2、`both_open`=1。
  **配对靠同一 SMI_DS_T IQ 的两拍 cri_vld（IQ93 单流），不是两个 IQ 桶** → **无需改 hw_config/iq_registry、无需 `iq_smi-ds-t-1` 第二桶、无 hco 二轮**。
- **真缺口 = gather 数据只够一个 tile**：`wr_vld0` 填 lo slot 8 beats（cyc551-559）后 `crd_vld` 数据流枯竭，
  `wr_vld1`（hi slot）cyc560 才起、拿不满 8 beats → `br1/hi_ready` 永 0 → `start_out` 永 0。
- **`intlv_in=0` 顺序填已对**（RTL write-router「!lo_ready→lo else hi」= 先填 lo 再 hi，与实测一致）。
- → **核心修复 = emit 第二次 gather（B=2 双读 SHIFTDN，供第二个 tile 填 hi slot）**。对 `unit_ds_transpose`，hi slot 数据
  甚至不进 golden（2 词 UR 写只取 lo 转置前 2 词），只需让 `hi_ready` 能 assert；真 matmul 的 hi tile 携真实第二半列。

### 改动点（动 DFG/sim，**无需新 axiom、无需 hw_config/hco 二轮**）
- `verification_v2/ops/matmul_op.py`（`_inject_smi_ds_t_transpose`）：**emit 第二次 ds_src_read + RTOVR + SHIFTDN gather（B=2）**
  填 hi slot；`ds_transpose` 设 `intlv_en=1`（表598 合规；RTL 不解码但据 spec 置位）。
- `sim/smc_simulator/system/cluster.py`（`_tick_smi_ds_t_trigger`）+ `units/smi_ds_t_unit.py`：模型升 2-tile→2-slot +
  **输出门控 lo+hi 双 ready（镜像 RTL `start_out`）**，专杀 model-A 单 tile 静默假绿（hco BLOCK#2）。
- `src/driver/payload_factory.py`：确认 `intlv_en=1` 经 metadata→payload 传到（physical_instructions 现示 INTLV_EN）。
- `src/synthesis/ucode_solver.py`（可能）：第二 gather 的排程 + 2 拍 stagger（latency 静态对齐，无新 axiom）。
- **不需要**：`iq_smi-ds-t-1` 第二 IQ 桶（配对是同 IQ 两拍）、hw_config iq_registry 改动。

## 建议测试（可证伪）

- `tests/unit/test_smi_ds_t.py::test_smi_ds_t_emits_pair`：断言 SMI_DS_T datapath 产出**成对** smi-ds-t-0 + smi-ds-t-1
  （intlv_en=1、intlv_grp 一致、intlv_in|intlv_out≥1）+ **两个** gather（B=2 双读填 lo/hi）；单条 emit → FAIL。
- `tests/unit/test_smi_ds_t.py::test_smi_ds_t_sim_gates_on_both_slots`：sim 在仅 lo ready / 仅 1 次 issue 时**不得出数**（镜像 RTL `start_out`），专杀 model-A 静默单 tile。
- 方案 C harness：把「`smi-ds-t-*` 配对存在性 + `hi_ready` 达成」加入 VCS 逐事件 diff 断言，作该 datapath oracle。

## 实现落地（2026-06-26，Stage-I，sim+pytest 绿，still_holds 仍 proposed 卡 VCS）

配对 B=2 修复已落地（纯 compiler，无新 axiom、无 hw_config/iq_registry 改动）：

- **DFG（`_inject_smi_ds_t_transpose`，5 算子共享）**：emit 第二条 gather `ds_gather_1`（SHIFTDN，
  broadcast=1）填 hi slot。关键 SAT 修复：`ds_gather_1` 经**松弛 RAW 边** `depends_on=["ds_src_read"]`
  （非 g0 的刚性 rtovr latch），复用 g0 已路由进 `DVR_SHIFTDN_S` 的同一源——SHIFTDN 微指令仅
  vld/broadcast/smc_id_en（源走固定寄存器非立即数），故第二条 gather **无需第二条 rtovr 路由 / 无需第二
  UR 读端口**（绕开 ADR-037 端口占满 UNSAT + 绕开「两 rtovr latch 把两 consumer 钉同拍 → 单 SHIFTDN IQ
  资源冲突」UNSAT）。`ds_transpose` 设 `intlv_en=1, intlv_in=0, intlv_out=1`（表598 合规）。
- **sim（`SmiDsTUnit`）**：加 `pairs=2` 配对门（默认 2），`evaluate` 累积 slot，仅填满 pairs 个 slot
  （issue_cnt==2）才触发出数——镜像 RTL `start_out`，堵 model-A 单 tile 静默假绿（hco BLOCK#2）。
  lo slot 决定输出行；hi slot 仅满足配对门（值复用 lo，DEBT-038-B2-GATHER）。
- **sim（`_tick_smi_ds_t_trigger`）**：喂两次 tile 填 lo+hi 开门；hi 复用 lo 数据、**不发重复 DST-SHIFTDN
  audit 事件**（UR 端点仍单读，与 VCS 单 broadcast 读对齐，不破固件完整性门计数）。
- **契约门转强制**：`tests/unit/test_smi_ds_t.py::TestADR038PairedB2Contract` 两测的 `@expectedFailure`
  已摘（现为强制门）；model-A 单 tile 流式单测改喂两 slot。
- **验证**：pytest 154 passed / 6 skipped（原 152+2xfail）；5 个 SMI_DS_T 算子 e2e 全 `exit=0`、
  固件完整性门 SUCCESS（unit_ds_transpose 58 / matmul_q_dst 120 / _k8 74 / fp16_q_dst 137 /
  fp16_full_dst 536 UR 端点全指令-backed）；firmware 实测 emit 两条 SHIFTDN gather + SMI_DS_T
  body INTLV_EN=1/INTLV_OUT=1。
- **未闭合（VCS-gated）**：sim 终值 parity 不验跨-SMC 时序、对单 SMI 忠实度近零；升 Accepted 须 VCS 跑出
  `unit_ds_transpose` bank5 由全 0 → golden（`hi_ready=1 → start_out → data_out` 非零）+ 5 算子全
  bit-perfect。hi slot 真实第二半列数据 = DEBT-038-B2-GATHER；每算子 intlv 组合 = DEBT-038-INTLV-PER-OP。

## Consequences

- ADR-033 的转置数值/拓扑结论、ADR-034 的指令驱动单元、ADR-036 的流式流水模型**保留**；本 ADR 只 supersede 三者的
  **`intlv_en=0` 单指令单 tile 简化**。
- model-A sim 假绿被证为系统性风险：sim 缺 2-slot 配对门 → 结构性功能缺口静默通过。方案 C 是该类缺口的唯一第三方 oracle。
- **still_holds=proposed，升 Accepted 卡 VCS**：配对固件 + sim 配对门落地后，`unit_ds_transpose` 须 VCS 跑出
  `hi_ready=1 → start_out → data_out` 非零 + bank5 = golden，5 个算子全 VCS bit-perfect。

## 未决事实（实现期钉死，已有 RTL/spec 依据不再卡 user）

- **[DEBT-038-INTLV-PER-OP]** 每个 DST 算子的 intlv_in/intlv_out 组合按其 golden 存储格式定（连续 vs 间插），
  实现期 golden-match 钉死。
- **[DEBT-038-PAIR-STAGGER]** 配对两指令「前后两个时钟周期启动」精确 stagger + INST_DONE/`PARAM_INST_NUM=4`
  outstanding 回收路径（承 DEBT-033/034-DSTDONE，VCS-gated）。
- **[DEBT-038-B2-GATHER]** B=2 双 gather（第二次读）的 SHIFTDN 发射 + 数据填 hi slot 的精确时序（承 DEBT-036-DST-BCAST-FEED-TIMING）。
