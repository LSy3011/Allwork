# 性能指标定义 v1（阶段三前置 · 任务1，2026-10-14）

> 任务来源：用户 2026-10-14——「完善性能指标定义。每个计时区间绑定明确的开始/结束
> 事件；区分总时长、装载、执行、等待。统一 n、有效迭代数、UR word、有效输出元素
> 等单位。」
> 性质：**定义文档**（不新跑实验）。所有区间绑定到**已存在、可在归档产物中复查的
> 事件**；当前不可从归档产物复取的口径单列「待立项取数」，不假装已有。
> 依据的 TB 结构事实与两例实测分解见 `docs/perf_data_collection_20261014.md` §5
> （本稿不重复数字，只定义口径）。三种时间类型（①调度与发射规模 ②TB 执行行为
> ③工具链墙钟）的分离沿用该文文首约定。

## 1. 单位统一

| 单位 | 定义 | 锚 |
|---|---|---|
| n | 算子输入元素数（fp32 元素，单 SMC 口径） | op yaml / run_config |
| UR word | 128-bit = 4 个 fp32 lane；UR 一行 | hw_config；TB 回读/dump 行 |
| 有效迭代数 | 单 SMC 实际发射的循环迭代数 = ceil(n / 4 lane)（当前四算子全部 stride-1、4 lane 填满；ragged tail 出现时按实例记录，不预设公式） | compiler_schedule.json |
| 有效输出元素 | 实际写出并参与比对的元素数（当前 == n；分段案例按段分列，合计 == n） | manifest hbm_layout / compare JSON |
| 指令行数 | 该段全部 IQ 队列 hex 行数之和 = TB 侧 AXI 装载 beat 数（1 行 = 1 beat，已逐文件核验一致） | program_manifest `iq_files[].lines` |
| TB 拍 | VCS 仿真时钟拍（10 ns/拍，probe_cycle 差值） | TB `iq_probe_cycle` |
| 墙钟秒 | host 时间戳差（**1 s 量化，±1 s；只作粗粒度记录，不算百分比**） | run_report `sizes.*.stages[].started/finished_utc` |

## 2. 计时区间定义（类型②：TB 执行行为，逐段）

每个区间给「开始事件 / 结束事件 / 当前可否从归档产物复取」。

| 区间 | 开始事件 | 结束事件 | 含义 | 可复取性 |
|---|---|---|---|---|
| **T_load_inst** | 段 BEGIN marker 打印（段循环 :2428） | `data_list done` 打印（$time） | 该段 IQ 指令经 TB AXI 逐行装载的流量耗时（实测 13/26 拍/行，占段拍 ~95%+） | ✅ sim 日志两打印点 |
| **T_cfg_trigger** | `data_list done` | TRIGGER CSR 写完成（IQ_EN×4 + CFG + TRIGGER 共 6 笔 AXI 写 + 3×repeat(2)） | host 侧配置与发射触发开销（实测恒定 ~84 拍） | ✅ sim 日志（TRIGGER 打印 + WBARRIER enter 夹逼） |
| **T_exec_obs** | TRIGGER 写完成 | WBARRIER release（被观察段末写落位，:2440-42） | **当前可得的最接近「硬件纯执行」的可观察窗**（实测 30–50 拍） | ✅ sim 日志 WBARRIER enter/release 打印（发布运行均开启） |
| **T_poll** | 执行事件实际发生（中断/落位） | 轮询检出（smi_wait_and_clear_intr，:803-823） | 完成检测量化（每 20 拍一轮，≤19 拍过冲，实测 +19） | ✅ sim 日志（release→intr 打印差） |
| **T_ack** | 中断检出 | done marker 打印（:2451） | intid 应答 + 复查（2×AXI 读 + 1×AXI 写 + repeat(2)，实测恒定 ~43 拍） | ✅ sim 日志 |
| **T_stage_span** | BEGIN marker | done marker | = T_load_inst + T_cfg_trigger + T_exec_obs + T_poll + T_ack（两例实测逐项相加精确相等） | ✅ arbitrate_report markers |
| **T_interstage_gap** | done_k marker | BEGIN_{k+1} marker | 段间间隙（含信用屏障 g3_credit_barrier_wait，:2454-56；即满足时实测 = 0） | ✅ markers 相邻段差 |
| **T_total_sim** | stage_0 BEGIN | 末段 done marker | Σ T_stage_span + Σ T_interstage_gap（**不含**末段 done 之后的 S4 STORE 完成屏障与 dump，:2457-69） | ✅ markers 首末差 |

边界口径（明确不计入）：
- UR/HBM 初始化 = 开场一次性 backdoor `$readmemh`（0 拍），不进任何区间；
- S4 STORE 完成屏障 / UR·HBM dump / compare 在末段 done marker **之后**，不进 T_total_sim；
- TB_STAGE_FIXED_WAIT(5000) 仅超时兜底路径，正常发布运行不出现；出现时该运行另行标注，不与正常运行并表。

## 3. 计时区间定义（类型③：工具链墙钟）

| 区间 | 开始/结束事件 | 备注 |
|---|---|---|
| W_compile_admit | run_report `sizes.*.stages[]` 中 compile+admit 步的 started/finished_utc | 1 s 量化 |
| W_vcs | 同上 vcs 步 | **复用预编译 simv**（runner stdout 明示「复用 simv（需 fsdb/kdb 请加 --rebuild）」），**不含 VCS 编译/elaboration**；含 data_list 生成 + simv 执行 + compare；许可证等待未单独记录（1 s 量化下亦不可分） |
| W_chain | 全链首步 started → 末步 finished | 含中间文件搬运；机器/负载相关 |

类型①（II / 有效迭代数 / 指令行数）无计时区间，属调度解形态量，按 §1 单位直接报告。

## 4. 报告纪律（阶段三执行时遵守）

1. 三种时间类型**分行报告**，互相不替代、不换算；跨类型比率（如「拍/行」）只允许
   同类型内或与类型①的显式标注组合（例：T_load_inst / 指令行数 = TB 装载单价，
   属 TB 行为描述，非硬件属性）。
2. 「执行」「延迟」「吞吐」字样默认指 **T_exec_obs 或板端实测**；用 T_stage_span 充当时
   必须标注「TB 段拍（以装载流量为主）」。
3. 段拍做算子间比较时必须先按 §2 分解剥离 T_load_inst；未剥离的比较结论无效
   （2026-10-14 实测：段拍差异主因 = 行数 × TB 装载单价，非 II）。
4. 墙钟只报区间与粗粒度结论（「非瓶颈」级），不算百分比、不做趋势拟合。
5. 板端数字（将来）单独一类（④板端实测），与类型② 严格分列；bitstream 版本、
   host 等待策略（poll 周期/超时）必须随数字同行记录。

## 5. 待立项取数（当前归档产物不能复取，不假装已有）

- **T_exec 精确窗**（TRIGGER 接受 → 末写落位探针）：WBARRIER 窗口是其上界近似
  （含屏障进入前的几拍）；精确窗需 IQ/写探针提取，阶段三立项。
- 板端各区间（host 轮询周期 10 ms 下的 DONE 等待、DMA 搬运时长）：随上板实验记录，
  口径届时按 §4-5 执行。
- add V1 链 COMPUTE 段时序（其 arbitrate schema 无段 markers）：用 IQ 探针日志另取。
