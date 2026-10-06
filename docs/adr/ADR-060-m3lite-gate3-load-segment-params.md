---
adr_id: ADR-060
title: M3-lite 门③ LOAD 段参数与证据草稿（含落位屏障约束；非集成声明）
decided: 2026-10-06（DRAFT——Q2/J2 授权范围内的参数整理，未授权门③集成通过）
last_verified: 2026-10-06
still_holds: draft
builds_on: ADR-058, ADR-059
user_decision: user 2026-10-05 门② Q2（L1/L2 后整理门③ LOAD 参数草稿，不宣称集成通过）
  + user 2026-10-06 J2（门③须携带落位屏障约束）
---

# ADR-060 · M3-lite 门③ LOAD 段参数与证据草稿

> **授权边界（逐字纪律）**：本文只整理门③ LOAD 段的参数与证据并登记屏障约束；
> **不建 DFG 节点、不改 stage_partition/stage_manifest/sim_config/run_v1_verify、
> 不宣称门③集成通过**。门③本身的立项与验收另待用户指令。

## 1. LOAD 段发射参数（门② L1/L2 VCS 实测形态）

单 stage 纯搬运段 = 1 条 LDB + N 条 LDBAXI，全部 LOOPS=0 平坦：

| 字段 | LDB（IQ26） | LDBAXI（IQ27） |
|---|---|---|
| 行数 | 1 | N = 描述符数（L1:1 / L2:3） |
| 关键字段 | UR_ADDR=bank<<12\|off；UR_WR_LINE_CNT=total；LINE_BYTE_CNT=15；LINE_STRIDE=1；CONST/PRE_FILL/POST_FILL=0；INST_ID=0；SMC_ID_EN=0 | BRST=lines−1；GR_BASE_ADDR 按 (brst+1)*16 累进；GR_INTLV_ADDR=0；TID=0；INST_ID=0；SMC_ID_EN=0 |
| header | INC=16，VLD=1 | INC=(brst+1)*16，VLD=1 |
| 配对恒等 | `total == Σ(brst_i+1)`（聚合配对，w033 golden 同源） | — |

- IQ_EN 掩码 = bit26|bit27（0x0c000000）；单 stage；smc_id_en=0 仅 SMC_0 使能。
- 数据面判据全部实测通过：UR 落位 == HBM 源窗口逐行 bit-exact、窗口两端 sentinel
  不变、异变行集合 == 窗口、其余 7 bank 与其余 7 颗 SMC（sentinel′ 预置）零泄漏、
  HBM 源区无损、commit 级写次数恰 == total 且每条 wdata == 源图案。
- 守卫（生成器响拒，单测锚定）：brst=0 / brst∉[1,15] / 聚合恒等 / total≤255 /
  描述符≤8 / en=0 / 16B 对齐 / 窗口越预载区。
- 照抄 golden 未独立理解的字段（风险延续 ADR-059 R3/R4）：LINE_BYTE_CNT=15、TID=0。

## 2. 落位屏障约束（J2，门③契约必须携带）

**事实（ADR-059 v2.1 实测）**：LSU 批完成中断不保证 UR 落位——
`smi_iq_lsu_complete` 采样竞态使通知先于数据落位。实测时序（probe_cycle）：

| 用例 | dispatch | 通知(INTR_ASSERT) | UR 写区间 | LDB 信用返回 | 通知→末写滞后 |
|---|---|---|---|---|---|
| L1（16 行） | 145-148 | **150** | 166..181 | 183 | **+31 拍** |
| L2（33 行） | 199-202 | **270** | 176..270 | 272 | +0 拍（同拍） |

- L2 通知被 LDBAXI 三描述符推迟（该队列恰好撑过竞态空窗），但 LDB 队列的
  rdi_done 依旧提前（@202，outstanding=1）——**滞后量随形态波动，不可依赖**。
- 两数据点的经验标定（**外推未验证，仅作屏障初值**）：通知 ≈ dispatch+5（竞态
  路径）；末写 ≈ dispatch+21+total → 滞后 ≈ total+16。屏障等待 ≥ total+16+裕量。
- **约束**：门③ LOAD 段通知后、首个依赖 UR 的 COMPUTE 段发射前，TB/流程须插入
  显式固定等待（初值 ≥ total+48 拍，即实测滞后 + 32 拍裕量；门③实施时须以
  当次用例的 ur_write_trace 复测末写时刻并收紧/确认，禁止直接信任通知）。
- 信用返回（真实完成）无 CSR 可观测通道，轮询屏障不可行；固定等待是唯一可行
  屏障（限制，随 RTL 修复可解除）。

## 3. 证据指针

- 归档：`verification_v2/artifacts/m3lite_gate2_20261006/`（README + sha256 清单；
  比对器 verdict 可对沙箱 case 目录复跑重现）。
- 生成器/比对器/驱动：`verification_v2/tools/{ldb_review_case_gen,ur_window_compare}.py`、
  `m3lite_gate2_run.sh`；单测 `tests/unit/test_{ldb_review_case_gen,ur_window_compare}.py`。
- TB 探针（additive 纯观测，三处）：URA 写支路 / INTR_ASSERT 精确锚点 / 取证探针组；
  原文与 TB 哈希在归档内。

## 4. 明确未做（授权边界外，全部不宣称）

- 门③集成（LOAD→COMPUTE→STORE 自动组合、stage 编排改造、DFG 节点）。
- L3 loops 形态 LDB（探索项，Q1 已批降级，未做）。
- COMPUTE 段与 STORE 段的门③参数化（STORE 侧可镜像 ADR-058 S1-S3 参数）。
- 屏障机制的 TB/流程实现（仅登记约束与初值标定）。
