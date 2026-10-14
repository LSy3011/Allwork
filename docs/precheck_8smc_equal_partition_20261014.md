# 8-SMC 等长分片有界预检（2026-10-14）

> 任务来源：用户 2026-10-14——「启动 8-SMC 等长分片的有界预检。先查清现有通用入口、
> 地址规划、使能和验证器需要改什么；暂不混入尾块、多 chunk 或归约；如果条件具备，
> 再做软件侧和 VCS 实验，不依赖板卡窗口。」
> 性质：**有界静态预检**（读码 + 已归档事实；未新跑 VCS、未触板、未改任何代码）。
> 口径：【源码】= 代码原文；【归档】= 已冻结文档/发布产物；【推断】标出。
> 本预检**不授权实现**；改动清单供裁定后按立法序推进。

## 1. 边界（本次预检的适用范围）

- **等长分片 only**：总元素数 n 被 8 SMC × 4 lane = 32 整除（n % 32 == 0），
  每 SMC 分得 n_per = n/8 元素、ceil(n_per/4) 次迭代；**无 ragged tail**。
- 不混入：尾块处理、多 chunk、归约/跨 SMC 数据交换（SHIFTUP/SHIFTDN 不参与）。
- 算子限当前已证 map 形态（{+,*} 逐元素）；首选 scale_sum（n16 板端已 PASS、
  容量已校准、分段已证）作首个候选。
- 板端不依赖：预检结论的可行实验路径全部软件侧 + VCS。

## 2. 现有机制事实（改动清单的前提）

| # | 事实 | 口径 | 锚 |
|---|---|---|---|
| F1 | 硬件模型 = **单 SMI 全局指令流 + smc_id_en 阈值寻址**；指令流全体 SMC 共享，不存在 per-SMC 指令流 | 【归档】 | ADR-022（ADR-018→021 per-SMC 机制全被 supersede） |
| F2 | **compute 单元缺省已 emit smc_id_en = PARAM_SMC_NUM−1 = 7**——当前每次发布运行中 8 个 SMC 本就全部执行同一 COMPUTE 流（smc_1..7 在哨兵数据上算，结果被忽略）；ADR-031 的「真硬件仅 SMC_0 跑 compute」bug 已在发射层修复 | 【源码】 | src/driver/payload_factory.py:110-121；hw_config/hw_params.yaml:2 `PARAM_SMC_NUM: 8` |
| F3 | LDB/STB（含 AXI 变体）同样有 smc_id_en 门控（ADR-031「除 RTOVR 外全单元」），缺省同 emit 7 → LOAD/STORE 流也由 8 SMC 在**各自 HBM 通道**上同偏移执行 | 【源码+归档】 | ADR-031；payload_factory 同段 |
| F4 | 数据面 = **每 SMC 一条独立 HBM 通道**：TB 侧 8 个 memory_init{1..8}.hex 逐 SMC 装载；板侧包同构（hbm_data 8 路）；llama r2 已证 8 通道逐通道 bit-exact 可比 | 【归档】 | TB :2413-2417；n244 包 README §3；llama r2 板端归档 |
| F5 | 当前统一 LCS case-gen **硬编码单 SMC 数据面**：manifest `active_smc: 0`；smc_0 得真实 UR 预载+golden，smc_1..7 填哨兵；memory_init1=真实数据、2..8=哨兵模式 | 【源码】 | unified_lcs_case_gen.py:297、:347-354、:379-382 |
| F6 | 验证器 unified_lcs_compare **只读 smc_0**（UR 初始/golden/dump + HBM 结果四处） | 【源码】 | unified_lcs_compare.py:95-100、:156 |
| F7 | VCS runner 与板端 runner **本就逐 SMC 可比**：smc_k/output_bank 存在即比对、缺席即 SKIP（现靠缺席哨兵实现单 SMC 范围） | 【归档】 | n8_vcs_runner_stdout.log（pass=1 skip=7）；板包 README §7 |
| F8 | TB 装载侧**已为 8 SMC 工作**：per-SMC data_list（smc_0..7）逐 SMC 解析、UR backdoor 逐 SMC $readmemh；smc_1..7 目录在既有案例中已存在 | 【源码】 | TB :2399-2405、:2130-2152 |
| F9 | WBARRIER 写落位监测**只看 smc_0**（`dut.u_smc.u_user_register.r_waddr_pipe`） | 【源码】 | TB :2992 |
| F10 | 迭代次数编在指令字内（VIN/LOOP_PARA_SET 等），由编译器按调度解发射——**用 n_per=n/8 重新编译即得正确的每-SMC 迭代计数**，无需手改指令 | 【归档】 | ADR-055 系列；compiler_schedule.json |
| F11 | check_program_manifest.py 不校验 active_smc/num_smc 字段（schema 扩展无既有闸阻拦，但须自觉走 schema 版本记录） | 【源码·正面枚举】 | check_program_manifest.py 全文无该二字段 |
| F12 | op yaml 无分片声明：parallel_dim 是 ADR-018 遗留（"N"/"K" matmul 语义），scale_sum/add 等四算子均未设 | 【源码】 | configurable.py:44-48；各 op yaml |

## 3. 四层改动清单

### 3.1 通用入口 —— 最小路径**不需要改编译器**

- 等长分片的编译数学：对 map 算子，8-SMC 等长分片的每-SMC 程序 == 同一算子以
  n' = n/8 编译的程序（迭代数、II、指令结构全同，仅数据切片不同）。**编译器无需
  新增分片概念**：用既有入口编译 n' 即可（F10）。
- 需要新增的是**入口参数语义记录**：run_config/manifest 须明确记「n_total = n，
  partition = equal×8，n_per = n/8」——provenance 诚实性问题，非调度问题。
- op yaml **不动**（避免触碰准入路径 → 不触发规则立法序）；分片属 case-gen/工具层。
  若未来要把 partition 提为 yaml 字段，届时按立法序单独立项。

### 3.2 地址规划 —— 结构不变，内容切片

- UR：每 SMC 对称同构（同 bank、同 line0、同窗口形状），仅数据内容为本 SMC 切片
  → **地址规划逻辑零改动**；case-gen 需生成 8 份 UR 预载/期望（LOAD 流从 HBM 取数，
  UR 预载仍是哨兵，golden 必须逐 SMC 真算）。
- HBM：8 通道**同偏移、不同内容**（memory_init{k} = 第 k 片）；hbm_layout 的
  byte_base/窗口尺寸不变（每通道窗口 = n_per/4 行）→ case-gen 的镜像生成从
  「1 真 + 7 哨兵」改为「8 真」；canary 区域保留。
- 分段（n_per 超容量时）逐 SMC 同构分段，段边界全 SMC 一致——机制沿用 ADR-057，
  但**本次边界内不要求**（n_per ≤ 240 即可整段）。

### 3.3 使能 —— 零改动（已全开）

- smc_id_en 缺省 7（F2/F3）；IQ_EN 掩码按队列不按 SMC；TRIGGER 携带的是 subq sid
  非 SMC 号 → **使能链路无需任何改动**。这也是本预检最重要的发现：
  **当前发布运行在硬件行为层已经是 8-SMC 执行，缺的只是数据面与验证面**。

### 3.4 验证器 —— 主要改动面

| 组件 | 现状 | 需改 |
|---|---|---|
| unified_lcs_compare.py | 仅 smc_0（F6） | 循环 smc_0..7：逐 SMC 读 golden/dump 比对；verdict 聚合规则（全 8 PASS 才 GREEN；任一 FAIL 报 SMC 号） |
| unified_lcs_case_gen.py | 单 SMC 数据面（F5） | 8 份 UR 预载/golden + 8 通道真数据镜像 + manifest active_smc 语义扩展（0 → 列表或新增字段，记 schema 版本） |
| 独立 fp32 参考 | 单 SMC golden 源 | 逐 SMC 切片各算一份（同一参考实现，输入切片不同） |
| VCS/板端 runner | 已逐 SMC 可比（F7） | **零改动**（供 8 份 golden 即自动 8 比对） |
| WBARRIER（TB） | 仅监测 smc_0（F9） | 可选增强（×8 或逐 SMC 掩码）；**非阻断**——屏障只是保持力辅助，数据正确性由末端比对兜底；不增强时须在报告中标注「smc_1..7 落位未被屏障监测」 |

## 4. 约束与开放问题

1. **n % 32 == 0** 硬约束（等长、无尾块）；建议首个实验 n = 256（n_per=32，迭代 8，
   远低容量墙）或与既有 n16 对照的 n = 128（n_per=16）。
2. **容量口径**：n_max 校准值（scale_sum 240）是**每-SMC** 口径；分片后总量上限
   8×240=1920 是【推断】，未校准——总量容量声明须另行 VCS 实测，不随本预检生效。
3. **段拍口径**：指令流共享 → 指令装载流量（TB 段拍主项，见 perf_data_collection
   §5）**不随 SMC 数增长**；T_exec_obs 预期与单 SMC 同量级（各 SMC 并行同构执行）。
   这是 8-SMC 对吞吐的真实杠杆点，但**数字以 VCS 实测为准，不预支**。
4. **manifest schema**：active_smc 语义扩展属产物 schema 变更，需版本化记录
   （ADR-061 manifest schema adr061_program_manifest_v1 → 小版本演进或新 id）。
5. **TB 监测缺口**：WBARRIER/S4BARRIER 均 smc_0 中心；8-SMC 运行的「保持力实证」
   覆盖下降到 1/8，须在实验报告限制节明示（不影响数据面对账）。
6. **board 侧**：包结构/runner 不变（F7/F4），但本预检不以板端为条件。

## 5. 条件评估与建议路径

**条件具备。** 使能零改动、编译器零改动、TB 装载零改动、runner 零改动；改动集中在
case-gen（数据面 ×8）与 compare（验证面 ×8）两个工具文件 + manifest schema 记录。
建议路径（待用户裁定后按序执行）：

1. case-gen 分片扩展（scale_sum，n=256 → n_per=32）+ 逐 SMC golden（同一独立 fp32 参考）；
2. compare ×8 扩展 + manifest schema 版本记录；配「非等长拒绝」「n%32≠0 拒绝」
   两个负控测试（pytest）；
3. VCS 跑 8-SMC case（TB 现成），比对全 8 SMC + 记录 T_exec_obs 与段拍构成；
4. 报告口径：数据面证据 = 8/8 SMC bit-exact；不宣称吞吐倍数（段拍主项是 TB 装载），
   性能解读按 perf_metrics_definition §4 纪律。
