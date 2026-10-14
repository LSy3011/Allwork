# 性能数据收集底稿（阶段三 10/25–28 前置取数，2026-10-14）

> 目的：把路线图阶段三「汇总编译时间、II、指令行数、stage 数、执行周期」的**取数**前置
> 完成——全部来自既有发布运行的产物（run_report.json / compiler_schedule.json /
> program_manifest.json / arbitrate_report.json），无新跑 VCS、无板。
> **分析与「公平的手写对照」选择留阶段三**；本稿只固定数字与口径。
> 口径：【实测·VCS】= TB 拍；【实测·host】= 墙钟秒（时间戳 1s 量化）；【源码】= 产物文件字段。
> 不外推：本表数字限所列运行，不代表其他规模/算子。
>
> **2026-10-14 修订（用户裁定）**：三种时间必须分开，不可互相替代——
>
> | 时间类型 | 本稿对应量 | 说明什么 | 不能说明什么 |
> |---|---|---|---|
> | ① 调度与发射规模 | II / 有效迭代数 / 指令行数 | 调度解的形态与发射规模 | 整个算子的实际吞吐率 |
> | ② TB 各段拍数 | arbitrate markers 段拍 | **指定仿真环境（本 TB）下的执行行为** | 板端延迟；「纯计算开销」（构成见 §5——段拍以 TB 指令装载为主，硬件计算占比 ~1%） |
> | ③ 编译/VCS/全链墙钟 | run_report 时间戳 | 当前工具链运行成本 | 稳定可推广的性能结论（1s 量化、机器/负载相关） |

## 1. 口径与单位

| 量 | 类型 | 单位/含义 | 来源 |
|---|---|---|---|
| II | ① | 启动间隔（调度解） | `*/build/compiler_schedule.json` 字段 `II` |
| stage 数 / 各段指令行数 | ① | 段 = LOAD/COMPUTE/STORE；行数 = 该段全部 `iq_files[].lines` 之和（hex 行 = 128-bit 指令字 = TB 侧一个 AXI 装载 beat） | `*/evidence/*_program_manifest.json` `stages[]` |
| 各段执行拍数 | ② | TB 拍 = arbitrate 报告 `markers.begins/acks` 逐段差（ack−begin）；VCS 仿真时序，**非板端时序**；**构成见 §5（含 TB 指令装载/轮询/屏障/固定等待，非纯硬件执行）** | `*/evidence/*_arbitrate_report.json` `markers` |
| compile_admit / vcs / 全链墙钟 | ③ | 秒（run_report `sizes.*.stages[].started/finished_utc`，**1s 量化，±1s 不定——只适合粗粒度记录，不适合算百分比提升**）；vcs 段**复用预编译 simv**（runner stdout 明示「复用 simv（需 fsdb/kdb 请加 --rebuild）」），**不含 VCS 编译/elaboration**；许可证等待未单独记录 | `*/run_report.json` `sizes` |

## 2. 统一 LCS 链（LOAD→COMPUTE→STORE 完整程序）八组发布运行

> 「各段执行拍」= 类型②（指定 TB 环境下的执行行为，**含 TB 指令装载/轮询/屏障，
> 构成实测分解见 §5，非纯硬件计算**）；指令行数/II = 类型①；墙钟 = 类型③（1s 量化）。

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

- COMPUTE 段指令行数与段拍随 n 近似同步增长（scale_sum：174/526/1966 行 ↔
  3140/9168/33828 拍 @ n=16/60/240）——**但二者同为类型①/② 的 TB 侧量**：
  经 §5 分解，段拍 ~95–97% 是按行数线性增长的 TB 指令装载流量，故此相关性主要
  反映「装载流量随行数增长」，不反映硬件计算随 n 的增长规律。
- 分段案例（n244/n72/n160）相对其最大单段形态，多出的是一段小 COMPUTE（78/360/190
  行；1496/6170/3415 拍）——跨段开销在本批数据中以「第二段自身执行」形态出现，
  无独立「切换开销」计字段（阶段三若要分离切换开销需另立取数口径）。
- ~~同 n=8 下 horner_v3u COMPUTE 拍（7970）显著高于 scale_sum n16 与 addcmulu n8~~
  → **2026-10-14 修订：此观察撤回到「TB 行为」层级**。§5 实测分解表明段拍差异的
  主要解释是指令行数（horner 468 行 vs scale_sum 174 行）乘以 TB 每行装载拍数
  （13/26 拍），而非 II=6；两例的硬件计算窗口（TRIGGER→末写落位）实测均仅
  ~30–50 拍。「horner 拍数更高」作为 TB 仿真行为成立，**不能归因于 II=6，也不能
  外推为板端吞吐差异**。
- 墙钟全部 ≤16s/规模（1s 量化，粗粒度记录）：compile_admit 恒 1–2s，vcs 5–7s（LCS）
  /1–2s（COMPUTE-only，复用 simv、不含编译）；编译时间在全部已测运行中非瓶颈。
  **1s 量化下不算百分比提升**；墙钟为当前机器/负载下的工具链成本，非稳定性能结论。

## 5. 段拍数构成分解（2026-10-14 增补：回答「COMPUTE 段拍数包含什么」）

**问题**（用户裁定须核对）：COMPUTE 段拍数是否包含指令装载、host 模拟轮询、屏障和
固定等待。**答案：四项全部包含**（写落位屏障在段内；信用/STORE 完成屏障在段外，
见下）。以下为两个发布运行的实测逐拍分解【实测·VCS，TB 源码锚见末行】。

### 5.1 段内时序结构（TB 源码层）

段拍 = arbitrate markers `ack−begin`，两个 marker 都是 TB `$display` 打印时刻的
`probe_cycle` 采样：BEGIN 打印于段循环开头（**指令装载之前**），done 打印于中断清除
之后。段循环体顺序（tb_aiacc_top_v04_f_multi_stage.v:2428-2470）：

```
BEGIN 打印(marker 起点) → parse_and_load_data_list_collect（每 INST 行一次 axi_write_beat）
→ sync_iq_en（纯文件解析，0 拍）→ smi_apply_iq_en_and_rdi_trigger（5 笔 CSR AXI 写 + 3×repeat(2)）
→ [写落位屏障 WBARRIER，仅被观察段] → smi_wait_and_clear_intr（每 20 拍轮询 INT/DONE）
→ intid ack（1 读 + 1 写 + repeat(2) + 1 读）→ done 打印(marker 终点)
→ [信用屏障 g3_credit_barrier_wait —— 段外，落在 done_k→BEGIN_{k+1} 间隙]
→ [末段后：S4 STORE 完成屏障 —— 段外，:2465-67]
```

- **指令装载在段内**：UR/HBM 初始化是开场一次性 backdoor `$readmemh`（0 拍，
  :2407/:2418），但**每段 IQ 指令是逐行真实 AXI 写**（load_iq_inst_txt:1129 →
  axi_write_beat:466，AW/W/B 全握手串行 + 末尾 repeat(2)），实测每行 **13 或 26 拍**
  （双峰；26 拍 = 13 拍基础上多一拍级等待，随目标队列而异）。
- **host 模拟轮询在段内**：完成检测 = 每 20 拍（TB_STAGE_INTR_POLL，:30）轮询一次
  CSR，**检出量化 ≤19 拍**（两例实测均为 +19）；轮询循环本身每拍推进。
- **屏障**：写落位屏障（GATE4 WBARRIER，发布运行中开启）在段内、轮询之前；
  信用屏障与 S4 STORE 完成屏障在 done marker **之后**（段外，不计入任何段拍；
  已发布数据中相邻段 done→BEGIN 间隙实测为 0，屏障即满足时不产生间隙）。
- **固定等待在段内但小**：trigger 路径 3×repeat(2) + ack 内 repeat(2)，合计 <10 拍；
  TB_STAGE_FIXED_WAIT=5000 仅末段超时兜底路径，发布运行未触发。

### 5.2 实测分解（两例，COMPUTE 段）

| 构成 | horner_v3u n8 stage_1（段拍 7970） | scale_sum n16 stage_1（段拍 3140） |
|---|---|---|
| 指令 AXI 装载（BEGIN→装载完） | **7774 拍（97.5%）**，468 行（338 行@13 + 130 行@26） | **2964 拍（94.4%）**，174 行（~17 拍/行均） |
| IQ_EN/TRIGGER CSR 写 + 固定等待 | 84 拍 | 84 拍（数值相同=结构恒定开销） |
| 写落位屏障（≈TRIGGER 后可观察的计算排干窗） | 50 拍（@8404→@8454，waited=50） | 30 拍（@3490→@3520，waited=30） |
| 轮询检出量化 | 19 拍（屏障释放@8454→中断检出@8473） | 19 拍（@3520→@3539） |
| intid ack（AXI 读+写+读） | 43 拍（@8473→done@8516） | 43 拍（@3539→done@3582） |
| **合计** | **7970 ✓** | **3140 ✓** |

锚：horner `n8_sim_log.log`（BEGIN@546 / data_list done@83395000ps / WBARRIER enter·release
@8404·8454 / intr@8473 / done@8516）；scale_sum `n16_sim_log.log`（BEGIN@442 / data_list
done@34255000ps / WBARRIER @3490·3520 / intr@3539 / done@3582）；时钟 10ns/拍。

### 5.3 结论口径

- 本 TB 的「各段执行拍数」= **TB host 侧行为为主**（指令装载流量 ~95%+、轮询/ack/
  屏障为固定小项），**硬件纯计算窗口（TRIGGER→末写落位）实测 ~30–50 拍，占段拍 ~1%**。
- 因此段拍可作「指定 TB 环境下的执行行为」记录（类型②），**不可**读作纯计算开销，
  不可外推板端延迟，不可用于算子间吞吐比较（行数差异主导）。
- 阶段三若需「纯计算拍数」口径，可用 WBARRIER 窗口或 IQ 探针另行取数（届时立项，
  不在本稿）；本稿 §2 表保留段拍原值并加注本节。

## 6. 锚点与复取方法

复取脚本逻辑（python3，仓库根）：读 `build/v1_runs/<op>/<run>/run_report.json`
（sizes → 墙钟）、`**/compiler_schedule.json`（II）、`**/evidence/*_program_manifest.json`
（stages → 行数）、`**/evidence/*_arbitrate_report.json`（markers → 段拍）。
本稿数字 2026-10-14 从上述路径逐字段提取，未四舍五入（墙钟受 1s 量化）。
