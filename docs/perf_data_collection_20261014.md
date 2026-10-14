# 性能数据收集底稿（阶段三 10/25–28 前置取数，2026-10-14）

> 目的：把路线图阶段三「汇总编译时间、II、指令行数、stage 数、执行周期」的**取数**前置
> 完成——全部来自既有发布运行的产物（run_report.json / compiler_schedule.json /
> program_manifest.json / arbitrate_report.json），无新跑 VCS、无板。
> **分析与「公平的手写对照」选择留阶段三**；本稿只固定数字与口径。
> 口径：【实测·VCS】= TB 拍；【实测·host】= 墙钟秒（时间戳 1s 量化）；【源码】= 产物文件字段。
> 不外推：本表数字限所列运行，不代表其他规模/算子。

## 1. 口径与单位

| 量 | 单位/含义 | 来源 |
|---|---|---|
| II | 启动间隔（调度解） | `*/build/compiler_schedule.json` 字段 `II` |
| stage 数 / 各段指令行数 | 段 = LOAD/COMPUTE/STORE；行数 = 该段全部 `iq_files[].lines` 之和（hex 行 = 128-bit 指令字） | `*/evidence/*_program_manifest.json` `stages[]` |
| 各段执行拍数 | TB 拍 = arbitrate 报告 `markers.begins/acks` 逐段差（ack−begin）；VCS 仿真时序，**非板端时序** | `*/evidence/*_arbitrate_report.json` `markers` |
| compile_admit / vcs / 全链墙钟 | 秒（run_report `sizes.*.stages[].started/finished_utc`，1s 量化，±1s 不定） | `*/run_report.json` `sizes` |

## 2. 统一 LCS 链（LOAD→COMPUTE→STORE 完整程序）八组发布运行

| 算子 | n | II | stage 数 | 各段指令行数 | 各段执行拍（TB 拍） | compile_admit (s) | vcs (s) | 全链 (s) | 发布 run |
|---|---|---|---|---|---|---|---|---|---|
| scale_sum | 16 | 2 | 3 | 8 / 174 / 2 | 430 / 3140 / 198 | 1 | 6 | 13 | adr061_accept2_n16 |
| scale_sum | 60 | 2 | 3 | 8 / 526 / 2 | 468 / 9168 / 198 | 1 | 6 | 13 | adr061_accept2_n60 |
| scale_sum | 240 | 2 | 3 | 20 / 1966 / 8 | 1084 / 33828 / 468 | 2 | 7 | 15 | adr061_accept2_n240 |
| scale_sum | 244 | 2 | **4**（2 段 COMPUTE） | 20 / 1966 / 78 / 8 | 1084 / 33838 / 1496 / 468 | 2 | 7 | 16 | adr061_accept2_n244 |
| horner_v3u | 8 | 6 | 3 | 12 / 468 / 2 | 534 / 7970 / 198 | 1 | 6 | 13 | hv3u_step4fix_n8_committed_20261012 |
| horner_v3u | 72 | 6 | **4**（17+1） | 18 / 2088 / 360 / 4 | 842 / 34982 / 6170 / 288 | 2 | 7 | 16 | hv3u_step4fix_n72_20261012_codex_r2 |
| addcmulu | 8 | 3 | 3 | 10 / 190 / 2 | 482 / 3415 / 198 | 1 | 5 | 12 | acu_step4_ulcs_n8_20261012 |
| addcmulu | 160 | 3 | **4**（38+2） | 20 / 2026 / 190 / 6 | 1046 / 33019 / 3415 / 378 | 2 | 7 | 16 | acu_step4_ulcs_n160_20261012 |

## 3. V1 COMPUTE-only 链（add，无统一 LCS 运行——add 的 LCS 列 = 未跑）

| 算子 | n | II | compile_admit (s) | vcs (s) | 全链 (s) | 发布 run（同 run 三规模） |
|---|---|---|---|---|---|---|
| add | 4 | 2 | 1–2 | 1–2 | 3 | 20261010_102442_d036c3；hv3u_regress_add_frozen_20261012_codex（5 运行枚举见论文实验表 §6） |
| add | 8 | 2 | 1–2 | 2 | 3 | 同上 |
| add | 60 | 2 | 2 | 2 | 4 | 同上 |

注：add V1 链 arbitrate 报告 schema 不同（`c0_by_queue/cross_queue/writes`，无段
markers——COMPUTE-only 仲裁），故本表不给 add 的段拍数；其 COMPUTE 段时序若要入
阶段三分析，用 IQ 探针日志另行提取（届时取数，不在本稿）。

## 4. 事实性观察（不外推）

- COMPUTE 段指令行数与执行拍随 n 近似线性增长（scale_sum：174/526/1966 行 ↔
  3140/9168/33828 拍 @ n=16/60/240）；LOAD/STORE 段小且弱随 n 变。
- 分段案例（n244/n72/n160）相对其最大单段形态，多出的是一段小 COMPUTE（78/360/190
  行；1496/6170/3415 拍）——跨段开销在本批数据中以「第二段自身执行」形态出现，
  无独立「切换开销」计字段（阶段三若要分离切换开销需另立取数口径）。
- 同 n=8 下 horner_v3u COMPUTE 拍（7970）显著高于 scale_sum n16（3140，其迭代数还
  多一倍）与 addcmulu n8（3415）——与 II=6、6 级依赖链、18 队列的结构差一致；
  归一化（每迭代拍数）分析留阶段三。
- 墙钟全部 ≤16s/规模（1s 量化）：compile_admit 恒 1–2s，vcs 5–7s（LCS）/1–2s
  （COMPUTE-only）；编译时间在全部已测运行中非瓶颈。

## 5. 锚点与复取方法

复取脚本逻辑（python3，仓库根）：读 `build/v1_runs/<op>/<run>/run_report.json`
（sizes → 墙钟）、`**/compiler_schedule.json`（II）、`**/evidence/*_program_manifest.json`
（stages → 行数）、`**/evidence/*_arbitrate_report.json`（markers → 段拍）。
本稿数字 2026-10-14 从上述路径逐字段提取，未四舍五入（墙钟受 1s 量化）。
