# 公平手写对照选定（阶段三前置 · 任务2，2026-10-14）

> 任务来源：用户 2026-10-14——「选定公平的手写对照。先确认双方公式求值顺序、精度、
> 输入范围、规模、SMC 数量、RTL/TB 和同步方式一致；条件不一致时分列展示，不计算
> 加速比。」
> 性质：**资产普查 + 一致性核查 + 选定建议**（未新跑实验、未触板、未改代码）。
> 口径：【源码】/【归档】/【推断】分级；不一致维度按用户规则 → 分列展示。

## 1. 候选手写资产普查（正面枚举）

| # | 资产 | 形态 | 可比性入口 |
|---|---|---|---|
| H1 | **llama r2 mul_scalar**（`~/u280_fpga_packages/mul_scalar_llama_r2_v422_8smc_5chunks_rtl_pass_20260904_173154`） | 手写微码 case（multi_inst_stage 同构目录：meta_v3 + smi_iq_inst/stage_* + smc_0..7），5 chunk × 8 SMC，**板端 5/5 PASS、40/40 SMC DDR bit-exact**（2026-09-04） | case 目录与编译器产物**同构** → 同一 TB 可直接 VCS 装载复跑【归档+推断】 |
| H2 | mul_scalar 255w v3（2026-08-28，rtl_exact 变体） | r2 的前身包（同 8-SMC 手写线，单 chunk 255 词/SMC）；PACKAGE_INFO 注明「UR writeback advanced by 2 loop issues」时序惯例 | 同 H1 但被 r2 取代，作历史参照 |
| H3 | ManualMatmulParallelNOperator（matmul 手写线） | **仅 python sim 模型 A**，无 VCS、无 RTL 运行 | 执行环境不同且非 RTL 证据 → **不具备对照资格**（DEBT-023-BCAST-TIMING 未清） |
| H4 | rh_real2tile_slots_fpga_20260921（09-21 板端实跑包） | 板证封装惯例来源包 | 非算子对照对象（封装参照物） |

编译器侧可比资产：mul `x*other` flat II=1，**前 V1 证据类**（ADR-054 阶段2-M1，
2026-09-12，`compiler_mul/smc_0` COMPARE PASSED，2048/2048 bit-exact，单 SMC，VCS）。
四算子（add/scale_sum/horner_v3u/addcmulu）**无对应手写资产**——手写线只有 mul 系。
故唯一候选配对 = **编译器 mul ↔ 手写 mul_scalar（H1）**。

## 2. 七维一致性核查（编译器 mul ↔ H1）

| 维度 | 编译器 mul（前 V1，VCS） | 手写 mul_scalar r2（板端） | 一致？ |
|---|---|---|---|
| 公式求值顺序 | y_i = x_i·other_i（逐元素 1 FPMUL，2 读流 + 1 写流） | y_i = x_i·α，α=0.08838834764831845 常驻 bank（逐元素 1 FPMUL，2 读流 + 1 写流：fpmul + ur_r0 + ur_r1 + ur_w0） | **结构同构**；操作数语义不同（other 逐元素变 vs α 常驻）——对时序/流量无影响，对「同一计算」声明需注意 |
| 精度 | fp32 | fp32 | ✓ |
| 输入范围 | x∈[-10,10]、other∈[-5,15] 随机 | 真实 llama QK 值域 | ✗（对 fp32 流水时序无影响；对数值内容比对不可互用） |
| 规模 | 2048 元素（单 SMC 一次性） | 255 词/SMC/chunk × 5 chunk × 8 SMC（=1020 元素/SMC/chunk，chunk 间重载） | ✗ |
| SMC 数量 | 1（数据面） | 8 | ✗ |
| RTL / TB | RTL v4.22 + tb_aiacc_top_v04_f_multi_stage（VCS） | RTL v4.22 + IQP1（**板端**） | RTL 同代 ✓；**执行环境不同**（VCS vs 板） |
| 同步方式 | TB-assisted（WBARRIER/S4 屏障 + 20 拍轮询） | host DONE 轮询 10ms + DMA 回读 | ✗（**判据同为数据面 bit-exact ✓**） |

**裁定（按用户规则）**：七维中四维不一致（输入范围/规模/SMC 数/同步+环境）
→ **当前不存在可计算加速比的现成配对**。两侧数字分列展示，账本继续分立——
论文实验表既有「板端另有手写微码线 llama r2 mul_scalar PASS（非编译器产物，
不并账）」（docs/thesis_experiment_table_20261013.md §2）口径维持不变。

## 3. 达成公平配对的路径（按代价排序）

**路径 A（推荐，分两步消差）**：
1. **环境/RTL/同步三维消差（零新开发）**：H1 的 r2 case 目录与编译器 case 同构
   （multi_inst_stage 格式），用**同一 TB 在 VCS 复跑手写 r2 case**——两侧随即同
   RTL、同 TB、同屏障/轮询机制、同拍数口径（perf_metrics_definition §2 全区间
   可两侧复取）。注意 H2 注记的手写侧「UR writeback 提前 2 个 loop issue」时序
   惯例属发射形态差异，报告时须明示（不影响数据判据，影响拍数解读）。
2. **SMC 数/规模消差（依赖任务3；规模起点经用户 2026-10-14 裁定修订）**：
   编译器 mul 走 8-SMC 等长分片（precheck_8smc_equal_partition_20261014.md）。
   **暂不从 n_total=8160 开始**（用户裁定：此时每 SMC 1020 元素，已超首轮单
   COMPUTE 段范围）——先选**两条路径都支持的小规模**（手写侧裁小 chunk 或单
   chunk 前几行，编译器侧同 n_total/n_per）；8160 对齐仅作远期目标，之后再引入
   分段或多 chunk。公式侧把 `other` 配成常驻 α（数据配置层，不改公式文本、
   不触准入规则）。
   完成后七维中仅剩「操作数语义（常驻 α vs 逐元素 other）」一项**声明性差异**，
   届时在报告限制节明示，可算同结构对照。

**路径 B**：手写侧裁 1-SMC 小 case 对照编译器现有 2048 run —— 手写线无现成
1-SMC case，需新写并重新板/VCS 验证，代价高于路径 A 且对标价值低（1-SMC 不是
手写线的代表形态）。**不推荐**。

**路径 C（当前默认）**：维持分列，不做配对。阶段三若时间不足，A-1 单步即可
先行（手写 case VCS 复跑不依赖任务3），先把「同环境拍数」拿到手。

## 4. 对照成立后的度量口径（预登记，防届时口径漂移）

- 比较量：**T_exec_obs（VCS 拍，类型②）为主**；段拍（含 TB 装载流量）只作辅助
  展示且必须按 perf_data_collection §5 分解标注；**不用墙钟秒比**（1s 量化）。
- 加速比仅在七维（含声明性差异已明示）全部核对后计算；任何一维不一致 → 分列。
- 手写侧拍数若含「writeback 提前 2 issue」形态差异，拍数比较按「发射形态不同」
  加注，不追平、不修正。
- 结论边界：对照回答「同一算子在**同环境**下编译器产物 vs 手写微码的执行行为」，
  不外推为编译器 vs 人手的普遍结论（单一算子、单一手写作者、单一实例）。

## 5. 锚点

| 结论 | 锚 |
|---|---|
| H1 板端 5/5 + 40/40 bit-exact、算子语义、shape、IQ 构成 | r2 包 PACKAGE_INFO.txt；rtl_validation/summary.tsv |
| 编译器 mul 2048/2048、前 V1 证据类、不并账口径 | docs/thesis_experiment_table_20261013.md §2；ADR-054 阶段2-M1 |
| 手写 case 与编译器 case 目录同构 | r2 包 cases/chunk0000/ 与 ulcs_* case 目录结构对照 |
| 手写侧 writeback 提前 2 issue 惯例 | 255w 包 PACKAGE_INFO.txt `fpga_writeback_note` |
| 段拍以 TB 装载为主、拍数比较纪律 | docs/perf_data_collection_20261014.md §5；docs/perf_metrics_definition_20261014.md §4 |
