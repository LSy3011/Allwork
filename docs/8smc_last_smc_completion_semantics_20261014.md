# 8-SMC 汇总完成信号（CRD 链）覆盖全部目标的结构举证（提案，待用户裁定）

> 性质：**举证材料 / 提案**，对应 spec
> `docs/8smc_equal_partition_spec_20261014.md` §3 C3 **路径 B**
> （「正面证明现有汇总完成信号以全部目标为条件，举证责任在实施方」）。
> **本稿不构成验收升级**：按用户 2026-10-14 明令，A′ 轮只签收「正确路由与
> 8-SMC 数据结果的探索证据」；是否据本稿升级为正式 8-SMC 统一链验收，
> 由用户裁定。路径 A（TB 加全目标监测，原树 TB 改动）仍未获授权。
> 日期：2026-10-14。载体 run：`8smc_intlv_n128_r2`（case
> `ulcs8smc2r2_scale_sum_8smc_intlv_n128_r2_n16`，run_dir
> `build/v1_runs/scale_sum/8smc_intlv_n128_r2/n16/`，`outcome=GREEN`）。
> 其前身 run1（`8smc_intlv_n128`）与 r2 的 case 载荷 `*.hex` 228/228 逐字节
> 相同——**该事实只证指令与数据载荷同一，不证两次运行逐拍同一**；**本文引用
> 的全部周期级观测一律取自 r2 自身日志**（`r2/evidence/n16_sim_log.log`、
> `n16_ur_write_trace.log`、`n16_hbm_write_trace.log` 等，已随归档冻结）。
> 归档：`verification_v2/artifacts/8smc_intlv_n128_20261014/`。

## 0. 结论（分档）

| 结论 | 证据档 |
|---|---|
| 汇总信号 `smi_top.i_crd_{ldb,ldbaxi,stb,stbaxi}` 的每一次脉冲，**结构上**要求 SMC0..7 全部以同一 inst_id 完成（AND 归约） | 【源码】逐行锚（§2） |
| 运行时该信号**每指令批次恰 1 脉冲**（非每 SMC 1 脉冲），与「AND 归约后按批上报」一致 | 【实测】A′ run 日志（§1） |
| 该信号**不覆盖**：STORE 的 HBM 落位（信用在 AXI-Stream 交接时点发出，「record-only」）、上游缓存队列溢出/丢事件、程序结束时未配对的挂起事件 | 【源码】+【实测】（§3） |
| 「8 目标全部完成」的**数据面**独立佐证 | 【实测】8/8 三区互校 + 逐 SMC UR 写账 20×8 + 逐通道 HBM 写账 4×8（§4） |

## 1. 运行时观测（A′ run，n_total=128 / n_per=16）

指令批次：LOAD = 4×LDB + 4×LDBAXI；STORE = 1×STB + 1×STBAXI
（`smi_iq_inst/stage_0/iq_ldb.hex` 4 行、`iq_ldb_axi.hex` 4 行；`stage_2` 各 1 行）。

信用脉冲（TB 探针读 `smi_top.i_crd_*` 单聚合总线）：

```
[TB_PROBE] CRD_LDBAXI @probe_cycle 343 crd=0000011
[TB_PROBE] CRD_LDB    @probe_cycle 348 crd=0000011
[TB_PROBE] CRD_LDBAXI @probe_cycle 357 crd=0000111
[TB_PROBE] CRD_LDB    @probe_cycle 362 crd=0000111
[TB_PROBE] CRD_LDBAXI @probe_cycle 371 crd=0001011
[TB_PROBE] CRD_LDB    @probe_cycle 376 crd=0001011
[TB_PROBE] CRD_LDBAXI @probe_cycle 385 crd=0001111
[TB_PROBE] CRD_LDB    @probe_cycle 390 crd=0001111
[GATE4][CRD_STB]    @probe_cycle 3754 crd=0000011
[GATE4][CRD_STBAXI] @probe_cycle 3758 crd=0000011
```

- 脉冲数 = **LDB 4 / LDBAXI 4 / STB 1 / STBAXI 1**，与指令条数逐一相等；
  若信号是「每 SMC 各自上报」，本应看到 4×8=32 与 1×8=8。⇒ 上报粒度是
  **指令批次**，一颗一颗 SMC 的完成不构成一次上报。
- `crd[4:2]`（inst_id，含 valid/done 低位）依次 0→1→2→3，与 4 条 LOAD 指令
  的 inst_id 递增一致。
- STORE 侧 S4 屏障显式标注 `credits record-only`：`crd_stb=1 crd_stbaxi=1`
  与 `hbm_writes=4 bresp=1` 并列，屏障判据取后者。

## 2. 结构举证（逐行锚，只读源码）

### 2.1 链的拓扑：SMC7 为源、SMC0 为上报点，链尾接 0

- `data_engine/aiacc_top_f.v:459-472`：四条 CRD 链声明
  `crd_{ldb,ldbaxi,stb,stbaxi}_chain[0:NUM_SMC]`，且
  `chain[NUM_SMC] = 0`（SMC7 的上游恒为 0 事件）。
- `:1858-1861`：`smi_top.i_crd_{stb,stbaxi,ldb,ldbaxi} <- chain[0]` ——
  **上报点只接 SMC0 的输出**。
- `:2117-2120 / :2144-2147`（generate，逐 SMC）：
  `*_crd_*_i <- chain[gi_smc+1]`、`*_crd_*_o -> chain[gi_smc]`；
  `:1994-1997 / :2023-2026` 为 SMC0（gi_smc=0）同型连接。

⇒ 任何到达 `smi_top` 的事件，必然沿 SMC7→SMC6→…→SMC0 逐跳传递。

### 2.2 逐跳转发的门条件：必须「上游事件 ∧ 本地完成」同 inst_id 配对

`data_engine/stb_ldb/stb/stb_ur_axi_conv.v:803-856`（STB；LDB/LDBAXI 同型）：

- `:815-816` 末位 SMC（`smc_id == smc_id_en`，即 SMC7）：
  `crd_*_o <= {inst_id, done, valid}` **直驱**（无上游可等）。
- `:817-839` 非末位 SMC：先把上游事件（`w_micro_inst_*_d_*`，来自
  `chain[smc_id+1]`）缓存进 `r_upstream_smc_*_busy[0..3]`。
- `:845-861` 转发条件（核心）：
  `r_current_smc_*_busy[i] ∧ inst_id 相等 ∧ r_upstream_smc_*_busy[j]`
  三者同时成立才置 `crd_*_o`，并**同时清掉本地与上游两个 busy 位**。
- `:1542-1565`（LDB 侧 `r_current_smc_ldb_busy` 置位条件）：本地完成事件
  来自 `r_micro_inst_ldb_d_valid/done/inst_id`，即**该 SMC 自己那条指令批次
  的完成**（不是别颗、也不是全局）。

⇒ 归纳：链下游第 k 跳输出的每个事件，要求 SMC_k..SMC7 全部以该 inst_id
完成。上报点（SMC0 输出）因此 = **8 颗目标在同 inst_id 上的 AND**。
「last SMC」分支（`:815`）只负责**发起**首个事件，不承担聚合；聚合由
非末位分支的配对条件承担。

### 2.3 与逐-SMC 观察的一致性

`crd_ldbaxi` 另携带 `err`（`:1009`），但 `err` 只在本批事件里透传，不改变
配对条件；`intid`（`inst_id`）逐批递增与 §1 观测一致。

## 3. 该信号覆盖不到的东西（必须在验收口径里显式保留）

1. **STORE 的 HBM 落位不在信用之内**：STB 的完成事件在 AXI-Stream 交接
   （`w_micro_inst_stb_d_valid`）时点产生，早于 HBM 写落位；故信用只能证
   「8 目标均已交接其 store 流」。S4 屏障因此以 `hbm_writes + bresp` 为判据
   （§1 实测行，`record-only` 标注即此事）。**「信用齐 ⟹ 数据已落位」不成立**。
2. **上游缓存队列容量**：`r_upstream_smc_*_busy[PARAM_OUTSTANDING_INST_NUM]`
   为定深（本形态 4 深）。同 SMC 在飞指令数超过队列深度时的丢事件/复用行为
   **未审计**（超出本轮范围）；本 run 每段在飞 ≤4，未触及。
3. **未配对挂起事件的静默性**：程序结束时仍有上游事件未被本地配对，则不产生
   上报（**假阴性方向**，不会造成误放行）；即汇总信号是「完成的下界」，不是
   双射。
4. **逐 SMC 读侧不可见**：`ur_read_trace.log` 实测仅覆盖 SMC0（16 条全 s=0），
   故「8 份输入分别进入对应 SMC」不由读侧证据承载（由 §4 数据面承载）。

## 4. 数据面独立佐证（同 run）

- 8/8 三区互校 GREEN、零 SKIP（`n16_unified_lcs_compare.json`）；
- 逐 SMC UR 写账：`ur_write_trace.log` s=0..7 各 20 笔（LOAD 窗内 **16 笔**
  = 4 bank × 4 line，COMPUTE 段 4 笔）；仲裁器 v2 逐 SMC 检查报告 LOAD 窗内
  16/16、`outside=0 / missing=0`（`per_smc_ur_writes`）；
- 逐通道 HBM 写账：`hbm_write_trace.log` s=0..7 各 4 笔、addra 0x500-0x503
  （`per_channel_hbm_writes`）；首跑（intlv=0）对照为 32 笔全落 s=0。

## 5. 若要升级为正式验收，尚缺什么（供裁定）

| 选项 | 内容 | 状态 |
|---|---|---|
| 路径 B（本稿） | 以 §2 结构举证 + §1 运行时脉冲粒度作「汇总信号覆盖全部目标」的正面证明 | **待用户裁定**；未裁定前维持探索口径 |
| 路径 A | 原树 TB 加逐 SMC 链抽头（`crd_*_chain[1..7]` 计数，期望每抽头 4 脉冲）或逐 SMC done 计数 | **须用户单独授权**（TB 改动 + 版本化冻结）；未授权 |
| STORE 侧 | host 完成协议闭环（F6 开放问题）+ HBM 落位判据维持现状 | 开放 |

## 6. 锚点与口径

- 证据分级：§2 为【源码】（只读逐行，未改 RTL）；§1/§4 为【实测】（VCS run
  日志，run_id 见题注）；本稿不含【推断】结论。
- 未声称：硬件不可恢复性、板上行为、SMC0 中心屏障已等价于全目标监测。
- 相关：`docs/8smc_equal_partition_spec_20261014.md`（C3）、
  `verification_v2/artifacts/8smc_first_red_intlv0_20261014/README.md`（首跑归因）。
