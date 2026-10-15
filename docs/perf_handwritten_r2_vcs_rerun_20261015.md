# 手写 r2 对照侧 VCS 复跑与性能取数（2026-10-15）

依据：2026-10-14 路线图「阶段三 10/25-28 性能与自动化评估」+ 2026-10-15 收口令
「回到性能评估主线：先选小规模、公平条件的手写/编译器对照，使用明确起止事件的
指标」。本文为**手写对照侧**的第一块基石：把 09-04 板证过的 mul_scalar llama r2
案例在本机主线 TB 下复跑、复现其 PASS、并按
`docs/perf_metrics_definition_20261014.md` §2 的区间定义取数。

- 公平性路径裁定：`docs/fair_handwritten_baseline_selection_20261014.md` §3
  ——手写 r2 case 目录与编译器 case 同构，可同 TB VCS 复跑，零开发。
- 归档：`verification_v2/artifacts/perf_hw_r2_vcs_rerun_20261015/`（README +
  全量日志/指标/哈希清单 + SHA256SUMS）。
- 提取工具：`verification_v2/tools/perf_marker_extract.py`（本轮新写）。

## 1. 运行身份（可复现性）

| 项 | 值 |
|---|---|
| RTL/TB 树 | `~/u280_sim_v422` 主线原树（非沙箱） |
| TB | `sim_multi_stage/tb_aiacc_top_v04_f_multi_stage.v`，sha256 `3324a33b…f7c5`（F6 冻结在册件，**未改动**；WBARRIER 为其内建缺省关闭功能，本轮仅以 plusarg 打开） |
| 运行器 | `run_sim_aiacc_top_f_multi_stage.sh`（复用 simv；`EXTRA_PLUSARGS` 透传） |
| case | `test_atomic_ops/multi_inst_stage/mul_scalar_llama_v422_r2_qk_chunk000{0..4}`；与 09-04 发布包（tar sha256 `2d2009b0…2d2d`）内 case 逐字节一致（09-04 包外仅存 dump 增量） |
| 负载 | 每 chunk：8 SMC × 255 词（fp32）×α（0x3DB504F3）→ HBM bank2 @byte 8192；共 5 chunk |
| 编译器 git | develop@598e2ba（本文与工具为未提交增量） |

## 2. 重大陷阱登记：全局 HBM 预载目录（首轮复跑曾因此全红）

**机制**：主线 TB 从全局目录 `~/u280_sim_v422/data_engine/sram/memory_init{1..8}.hex`
预载 HBM 模型，**从不读 case 自带的 `hbm_data/`**；裸运行器脚本也不做 staging。
09-04 的原始流由其生成器拷贝数据（run_rtl_test_v3.sh:89），该步骤不在裸运行器里。

**本轮实证**：首轮直跑（未 staging）时全局目录残留 10-08 m3lite 数据
（memory_init2..8 为同一均匀填充，含常数 1.25=0x3FA00000）。结果：

- SIM PASSED、`$finish @28485000ps` 与 09-04 **完全相同**——该 case 控制流
  与数据无关，错数据不改变任何时序/完成标记；
- 但 8/8 COMPARE FAIL，新 dump 恰为 x×1.25（golden 为 x×α，α=0x3DB504F3）；
- 恰 32 个文件变化（每 SMC 的结果 dump），指令文件零变化。

**处置**：跑前 `cp case/hbm_data/memory_init*.hex → data_engine/sram/`；
跑后恢复跑前快照（已执行，`sha256sum -c` 核验 OK）。
m3lite 线已有 `hbm_preload.py` 快照↔expect 双锚工具（任务1），
**裸运行器路径没有这道防线**——跨线复跑 case 时必须先 staging。
此陷阱已写入归档 README 首部。

## 3. 复现结果（修正 staging 后，三重锚点）

| chunk | runner COMPARE | 09-04 哈希清单 | verify_ddr_v3 | T_total_sim |
|---|---|---|---|---|
| 0000 | 8/8 PASS | 216/216 OK（含 72 结果 dump） | 8/8 PASS | 2661 |
| 0001 | 8/8 PASS | 216/216 OK | 8/8 PASS | 2661 |
| 0002 | 8/8 PASS | 216/216 OK | 8/8 PASS | 2661 |
| 0003 | 8/8 PASS | 216/216 OK | 8/8 PASS | 2661 |
| 0004 | 8/8 PASS | 216/216 OK | 8/8 PASS | 2661 |

- 「09-04 哈希清单」= 复跑前对 case 目录现场的逐文件封存（每 chunk 216 项）。
  复跑重写的 dump 与 09-04 逐字节一致 ⇒ **09-04 的 VCS PASS 在本机主线 TB
  下可确定性复现**（dump mtime 证实确为本轮重写，非残留）。
- 五 chunk `T_total_sim` 恒 2661：数据无关控制流的确定性，跨 chunk 零漂移。
- verify_ddr：DDR dump @store_byte_base 8192（行 512）起 255 词 == golden，8 SMC 全过。

## 4. 区间取数（类型② TB 拍；chunk0000，其余 chunk 同构同值）

标记源：stage BEGIN/done `@probe_cycle`、IQ 装载 `[TB_AXI_DBG]` ps
（经本运行自校准偏移 19.5 拍，残差 0）、TRIGGER=触发写自身 B 握手行
（双侧夹逼，bound_width 随 JSON）、WBARRIER enter/release。

### 4.1 R1 无门控（五 chunk 全同）

| stage | span | T_load_inst | T_cfg_trigger | trig→done（上界） | trig→intr | T_ack | 拍/行 |
|---|---|---|---|---|---|---|---|
| 0 LOAD | 1176 | 156 | 78 | 942 | 869 | 73 | 13.0（12 beats） |
| 1 COMPUTE | 919 | 507 | 78 | 334 | 277 | 57 | 13.0（39 beats） |
| 2 STORE | 566 | 78 | 78 | 410 | 330 | 80 | 13.0（6 beats） |

`T_total_sim` = 2661（stage_0 BEGIN→stage_2 done，不含 S4/dump）。
段间 gap = 0（done 与下段 BEGIN 同拍）。

### 4.2 R2 WBARRIER 门控（chunk0000；bank=2 writes=255 stage=1 timeout=5000）

- 门控标记：enter @1779（= trigger lb 1773 + 6 拍 armed 延迟）、
  release @2048、landed=255、waited=269；
- **COMPUTE 段 `T_exec_obs` = 275**（trigger→末笔 UR 落位放行，上界）；
  其中 waited=269 是屏障真实等待（首笔写 @1793=trigger+20 流水线填充，
  255 写窗 1793→2047），`T_poll` = 2（release@2048→INTR@2050）；
- 门控不改变数据面：复跑后 216/216 哈希仍全 OK；
- 门控记账开销：stage_1 span 919→922、total 2661→2664（+3 拍），ack 57→60。

### 4.3 结构观察（诚实口径）

- **IQ 装载是固定开销，执行窗随 n 增长**：三段装载合计 741 拍
  （57 beats×13 拍），与 n 无关；COMPUTE 执行窗 275 ≈ 255 词/SMC 流式提交
  （≈1 词/拍/SMC，8 SMC 并行 2040 词）。本 case COMPUTE 段装载占 55%，
  与编译器小 n 案例「段拍 95-97% 系装载」（perf_data_collection §5）不矛盾——
  那里 n=8/16 极小，执行窗仅 30-50 拍。**跨算子比较前必须剥离 T_load_inst**
  （metrics 定义纪律②）。
- `T_cfg_trigger` 三段恒 78：装载尾→触发写的 host 侧固定序列。
- STORE 段 trig→done=410 为代理上界（含 STORE 信用完成路径；F6 已证 legacy
  完成早于真信用，本指标不做完成语义断言，只作区间）。

## 5. 工具勘误（本轮自检出，已修）

`perf_marker_extract.py` 初版两处缺陷，首轮输出即暴露并被单调性护栏拦截：

1. ps→拍换算多乘 10（`ps/1000−off` 误作 `ps/10000×10−off` 的交换律错误等价物）；
2. TRIGGER 锚取「后一条 probe 行」在 probe 稀疏段严重高估（stage_1 实测
   ub−lb=276 拍）。改为双侧夹逼：lb=前面最近时标行（实证即触发写自身的
   `[AXI_DEBUG] B-Channel Handshake Completed`，与 DISPATCH_DONE 差 7 拍、
   与计算首写差 20 拍，数量级自洽），区间端点一律用 lb 并报 bound_width。

修复后输出通过全部单调性护栏；§4 数字为修复后产物。

## 6. 口径与限制

- 全部为**类型② TB 执行行为拍**（probe_cycle，周期 10000ps），非板端时序；
  墙钟（复用 simv，每次数秒）不参与区间。
- `T_exec_obs` = trigger→WBARRIER release，是「发射+执行+落位」的上界，
  **不称硬件纯计算时间**；waited=269 证明正向运行中放行确实晚于落位，
  但这是测量装置读数，**不是**注入/恢复证据（路径 A 收口口径不变）。
- 手写侧结论限本 case 形态（mul_scalar、8 SMC 等长、255 词/chunk、5 chunk）。
- 与编译器侧的公平对照（裁小至同 n_total/n_per 的配对构造）尚未开始——
  那是下一步，需按 selection 文档 §3 另行构造小规模对。

## 7. 下一步

1. 手写侧裁小（chunk 内词数）+ 编译器同规模配对，同 TB 同指标出对照行；
2. 对照行进论文实验表（docs/thesis_experiment_table_20261013.md 扩充）；
3. sram 全局目录陷阱的防线（staging 脚本化）已在归档 README 登记，
   是否纳入运行器改造留用户裁定。
