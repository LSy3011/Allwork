---
adr_id: ADR-044
title: unit_dst_vec128 走 SMI_DS_T 转置路交付 128-distinct —— 双-word 双-gather 时间分离 + per-op intlv + 放开 solver 单-DVR-路由
decided: 2026-07-01
last_verified: 2026-07-01
still_holds: proposed（user 选 Path X；hco RTL 裁定修向 APPROVE；待实现 + VCS 全绿）
builds_on: ADR-038, ADR-043
supersedes: ADR-043 item ① 的 "2-word ds_src_read burst" 机制（hco 证伪：背靠背写同一 DVR → 两 gather 都读 word1 → word0 丢 + 仍 doubling）
hco_review: APPROVE-可实现（2026-07-01，RTL 行号级取证 shift_down.v:38-45 / smi_ds_t.v:64-70/100-112/141-159/313-317 / tb_aiacc_top_shift.v:84-89/570-586/658-671）
user_decision: Path X（修 SMI_DS_T 转置路）—— 2026-07-01 明确选定（非 doc 首选的 transpose-free Path Y）
---

# ADR-044 · unit_dst_vec128 SMI_DS_T 128-distinct 交付

> **一句话**：`unit_dst_vec128` 要把 128 fp16 向量经 SMI_DS_T 转置路按序交付 bank5。当前复用与 `unit_ds_transpose_fp16`
> **逐字节相同**的 gather/transpose 固件，该固件只产 **64-distinct-doubled**（单 8×8 tile）。要 128-distinct 须让
> SMI_DS_T 的 T0/T1 成对 session 收 **16 个 distinct 输入行**（8 SMC × 2 word），而非现在的「8 SMC 各 ×2」。

## Context（VCS 实证根因，全硅背书）

- **magic oracle**（每 SMC 灌常量 s+1）VCS 实测 bank5 = `beat0=[1,1,2,2,3,3,4,4], beat1=[5,5,6,6,7,7,8,8]`（每 2 beat 重复）。
  即：8 SMC 都到达，但**每 SMC 写入 2 个相邻 write-beat**（16 write-beat / 8 SMC = 2× 过采样）→ beat 内 doubling；
  且两 tile 按 **SMC-group（0-3 / 4-7）** 切、**非 word0/word1**。
- **doubling 真根因**（`aiacc_rtl/smc/shift_down/shift_down.v:38-45`）：`vld_in` 分支优先于透传分支。**两条相邻**
  gather 广播脉冲 → 每 SMC 在 T+1 仍走 vld 分支重灌自己的 DVR、不透传邻居 → SMI 收 SMC0,SMC0,SMC1,SMC1,…。
  **单**脉冲则正常透传 → 收 SMC0..SMC7 8 个 distinct。
- **权威时序 spec**（AIACC ISA v0.4.19 + `tb_aiacc_top_shift.v`）：T0+T1 成对 session 收 `N_in_valid=16=2×WR_BEATS(8)`
  个 distinct 输入行，8 进 T0、8 进 T1，连续输出 16 beat。TB 的两 gather 被 **16 NOP 隔开**（`tb:84-89`）+ 读
  **distinct tile**（`tb:570-586`）+ CRI `intlv 全 0`（`tb:658-671`）。
- **doc 岔路裁定**（`docs/spec/aiacc-v0.4.19-understanding.md`）：vector×matrix 的 B=2 读回「不需转置即可广播」
  （Path Y transpose-free），SMI_DS_T 转置是给 matrix×matrix 的列对齐 Tile 用的。**user 明确选 Path X**（仍走
  SMI_DS_T 转置路），本 ADR 据此展开；Path Y 记为未采纳备选。

## Decision（Path X 机制，hco APPROVE）

要让 gather 交付 16 个 distinct 行 `[SMC0.w0..SMC7.w0 | SMC0.w1..SMC7.w1]`，三个必要条件（缺一回到 64）：

### D1. 两 gather **时间分离**（各为孤立单脉冲）
`ds_gather`(→T0) 与 `ds_gather_1`(→T1) 之间加 drain 延迟（≥ gather 链 drain ≈8-16 拍），各自单脉冲 →
各吐 8 个 distinct SMC 行（消除 shift_down.v:38-45 的 per-SMC doubling）。当前二者相邻发射 = doubling 根因。

### D2. 两 gather 各读**不同词**（word0 / word1）
- `ds_src_read`(word0@ADDR0) + 新增 `ds_src_read_hi`(word1@ADDR1) 两条 UR 读。
- 两条 RTOVR 路由 word0/word1 → DVR_SHIFTDN_S，**时间分离**：word0 route 在 gather0 采样前、word1 route 在
  gather0 采样后 gather1 采样前（顺序重载同一 DVR slot）。
- `ds_gather` 依赖 word0 route、`ds_gather_1` 依赖 word1 route。
- 🔴 **核心 blocker（编译器限制、非 RTL）= solver「每 DVR slot 单路由」**。RTL 完全支持顺序重载 DVR_SHIFTDN_S
  不同词；须放开 solver 让第二条 RTOVR 排到同一 slot 的晚拍（见 D4 实现）。

### D3. intlv **提成 per-op** = (intlv_in=0, intlv_out=0)
- `intlv_out=0`（`smi_ds_t.v:64-70`）→ 顺序出：先 T0 的 8 beat 再 T1 的 8 beat（当前 =1 交替，错）。
- `intlv_in=0`（`smi_ds_t.v:100-112`）→ 顺序写：先填满 T0 再 T1。
- 🔴 **BLOCK**：`intlv_out:1` 现写死在**共享** `_build_smi_ds_t_chain`，被 fp16/matmul_q/fma 共用（其 golden 照
  intlv_out=1 反推）。**必须提成 `smi_ds_t_config` 返回值 + `_build_smi_ds_t_chain` 读取**，默认保持
  (in=0,out=1) 向后兼容；vec128 显式 (0,0)。不得改共享默认。

### D4. golden 序 = layout 逆置换（VCS 逐 beat 定后钉死）
identity `[a0..a127]` 合法目标，靠 `inject_data_per_smc` 的 layout = RTL 转置置换之逆 达成（doc §1227 relabel）。
RTL 置换硬定值：`smi_ds_t.v:313-317` 输出行 lane = `(LINE_NUM-1-gi)`（SMC 逆序）+ rc 顺序。**先落 D1-D3 让
gather 出 16 distinct，再 VCS 逐 beat 解实际置换，二选一收口**：(a) layout 取逆 → golden 保 identity（推荐）；
(b) 保简单 layout、golden 订正成 RTL 自然序（DEBT-044-VEC-BEAT-FORMAT）。当前 `CrossSmcLayout(INTERLEAVE,B=2)` 是猜的。

## Consequences

- 修 `_build_smi_ds_t_chain`（per-op intlv + 双-read/双-route/双-gather）、`ucode_solver.py`/`dependency_axioms.py`
  （放开单-DVR-路由 + 两 route 时间分离约束）、`cross_smc_unit_op.py`（vec128 config + inject layout）、
  `_tick_smi_ds_t_trigger`（sim 喂 word0→T0/word1→T1）。
- **验收 = VCS compare_ur_to_golden PASS 8/8**（user 定标准）。magic 期望修后 = `[1,2,3,4,5,6,7,8 | ...]`（无 doubling）。
- **DEBT-044-FP16-DOUBLE-TILE**：`unit_ds_transpose_fp16` 的 VCS 8/8 是退化自洽（两 tile 同词、未真验双-tile）；
  vec128 是第一个真正验证双-distinct-tile datapath 的用例。
- 未采纳 Path Y（transpose-free 向量对读，doc 首选）记为备选；若 Path X 工程量失控可回退评估。
