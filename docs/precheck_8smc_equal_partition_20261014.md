# 8-SMC 等长分片有界预检（2026-10-14，v2 修订）

> 任务来源：用户 2026-10-14——「启动 8-SMC 等长分片的有界预检。先查清现有通用入口、
> 地址规划、使能和验证器需要改什么；暂不混入尾块、多 chunk 或归约；如果条件具备，
> 再做软件侧和 VCS 实验，不依赖板卡窗口。」
> 性质：**有界静态预检**（读码 + 已归档事实 + **产物解码实证**；未新跑 VCS、未触板、
> 未改任何 RTL/生产代码；新增只读解码工具 verification_v2/tools/decode_case_smc_enablement.py）。
> 口径：【源码】= 代码原文；【归档】= 已冻结文档/发布产物；【解码】= 对发布 case
> 最终产物逐行解码的实测；【推断】标出。
> 本预检**不授权实现**；改动清单供裁定后按立法序推进。
>
> **v2 修订（2026-10-14 用户裁定 + 解码实证）**：v1 的 F2/F3 与 §3.3「使能零改动、
> 当前运行硬件层已是 8-SMC 执行」结论**作废**——用户给出直接反例（LOAD/STORE 发射
> 显式传 `smc_id_en=0`，显式值覆盖 payload_factory 缺省 7），本文按解码证据重写；
> §3.4 WBARRIER 从「可选增强非阻断」改判为**正式验收的必备项**（用户裁定②）；
> 准确口径＝**已验证的数据范围为 SMC0，具体执行使能按实际指令逐阶段确定**（§2.5）。

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
| F2 | payload_factory 对**未显式给定**的 compute payload 缺省 emit smc_id_en = PARAM_SMC_NUM−1 = 7；**显式值覆盖缺省**。发布 case 的 COMPUTE 段经解码实证：body VALID=1 行全部 smc_id_en=7（广播至 8 SMC），但 smc_1..7 在哨兵数据上执行、结果不外送（STORE 只读 SMC0）——「8 SMC 执行同一流」成立，「8 SMC 数据面有效」不成立 | 【源码+解码】 | src/driver/payload_factory.py:110-121；hw_config/hw_params.yaml:2；§2.5 解码矩阵 |
| F3 | **LOAD/STORE 发射显式传 smc_id_en=0**：统一 LCS case-gen 对 LDB/LDB_AXI/STB/STB_AXI 全部显式给 0（覆盖缺省 7），且生成器守卫**响拒非零**（`[LDB-GEN-GUARD] M3-lite 单 SMC 复核要求 smc_id_en=0`）。解码实证：三段案例 LOAD/STORE 段 body VALID=1 行全部 en=0 → **仅 SMC0 搬运**。v1「LOAD/STORE 缺省已全开」结论错误 | 【源码+解码】 | unified_lcs_case_gen.py:263-270；ldb_review_case_gen.py:116-117；§2.5 解码矩阵 |
| F4 | 数据面 = **每 SMC 一条独立 HBM 通道**：TB 侧 8 个 memory_init{1..8}.hex 逐 SMC 装载；板侧包同构（hbm_data 8 路）；llama r2 已证 8 通道逐通道 bit-exact 可比 | 【归档】 | TB :2413-2417；n244 包 README §3；llama r2 板端归档 |
| F5 | 当前统一 LCS case-gen **硬编码单 SMC 数据面**：manifest `active_smc: 0`；smc_0 得真实 UR 预载+golden，smc_1..7 填哨兵；memory_init1=真实数据、2..8=哨兵模式 | 【源码】 | unified_lcs_case_gen.py:297、:347-354、:379-382 |
| F6 | 验证器 unified_lcs_compare **只读 smc_0**（UR 初始/golden/dump + HBM 结果四处） | 【源码】 | unified_lcs_compare.py:95-100、:156 |
| F7 | VCS runner 与板端 runner **本就逐 SMC 可比**：smc_k/output_bank 存在即比对、缺席即 SKIP（现靠缺席哨兵实现单 SMC 范围） | 【归档】 | n8_vcs_runner_stdout.log（pass=1 skip=7）；板包 README §7 |
| F8 | TB 装载侧**已为 8 SMC 工作**：per-SMC data_list（smc_0..7）逐 SMC 解析、UR backdoor 逐 SMC $readmemh；smc_1..7 目录在既有案例中已存在 | 【源码】 | TB :2399-2405、:2130-2152 |
| F9 | WBARRIER 写落位监测**只看 smc_0**（`dut.u_smc.u_user_register.r_waddr_pipe`） | 【源码】 | TB :2992 |
| F10 | 迭代次数编在指令字内（VIN/LOOP_PARA_SET 等），由编译器按调度解发射——**用 n_per=n/8 重新编译即得正确的每-SMC 迭代计数**，无需手改指令 | 【归档】 | ADR-055 系列；compiler_schedule.json |
| F11 | check_program_manifest.py 不校验 active_smc/num_smc 字段（schema 扩展无既有闸阻拦，但须自觉走 schema 版本记录） | 【源码·正面枚举】 | check_program_manifest.py 全文无该二字段 |
| F12 | op yaml 无分片声明：parallel_dim 是 ADR-018 遗留（"N"/"K" matmul 语义），scale_sum/add 等四算子均未设 | 【源码】 | configurable.py:44-48；各 op yaml |

## 2.5 产物解码实证（v2 新增；用户裁定①的执行）

工具：`verification_v2/tools/decode_case_smc_enablement.py`（只读；按 modules.yaml
code_fields 与 BitPacker 同序动态解码，禁手抄位表；未知文件/带 micro_groups 单元
记入 undecoded 不猜布局）。**决定性口径 = body VALID=1 行的 smc_id_en**——每队列
末行是 header vld=1 + JUMP=1 + body VALID=0 的队列终止项，其 smc_id_en=0 为无关值
（单元不动作），不计入使能判定。

三个发布 case 的逐段使能矩阵（2026-10-14 解码）：

| case | LOAD 段 | COMPUTE 段 | STORE 段 |
|---|---|---|---|
| ulcs_scale_sum_adr061_accept2_n16（三段，板端已 PASS） | en=0 仅 SMC0（LDB×4 + LDB_AXI×4，gr_base=4096/8192/12288/16384） | **en=7 广播**（fpadd/fpmul/vin0/vin1/vout0/ur-r0/ur-r3/ur-w0 全部；rtovr 无 en 字段，ADR-031 例外） | en=0 仅 SMC0（STB×1 + STB_AXI×1，gr_base=20480=0x5000，与 meta out_bank/byte_base 一致） |
| ulcs_horner_v3u_hv3u_step4fix_n8_committed（三段，II=6 多槽） | en=0 仅 SMC0 | **en=7 广播** | en=0 仅 SMC0 |
| ulcs_scale_sum_adr061_accept2_n244（四段，双 COMPUTE 分段） | en=0 仅 SMC0（LDB 61 行/段 + LDB_AXI×16，4 基址×4 子偏移） | **en=7 广播**（stage_1 大段 + stage_2 尾段均同） | en=0 仅 SMC0（STB×4 + STB_AXI×4，gr_base=20480..21248 四窗） |

地址路由证据：LOAD 侧 LDB_AXI 每条描述符一个 `gr_base_addr`，落在该 SMC 自己的
HBM 通道模型内（TB 8 通道 memory_init{1..8} 逐 SMC 装载，F4）；单 SMC 案例中所有
描述符地址都在通道 1（SMC0）数据窗内。STORE 侧 STB_AXI `gr_base_addr` 与
manifest hbm_layout byte_base 逐值一致。**8-SMC 分片时地址规划=8 通道同偏移、
不同内容**（§3.2），无需新地址机制。

结论（用户准确口径的实证版）：**已验证的数据范围为 SMC0；逐阶段使能 = LOAD 仅
SMC0 → COMPUTE 广播 8 SMC（SMC1..7 数据面无效）→ STORE 仅 SMC0。** 当前形态下
「硬件层已是全程 8-SMC 有效执行」不成立。

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

### 3.3 使能 —— LOAD/STORE 必须改，COMPUTE 已广播（v2 重写）

- **COMPUTE 段无需改**：解码实证 body VALID=1 行已 en=7 广播（§2.5）。
- **LOAD/STORE 段必须改**（v1「零改动」作废）：把 LDB/LDB_AXI/STB/STB_AXI 的
  smc_id_en 从显式 0 改为 7（或目标集阈值），改动面 = case-gen 发射参数
  （unified_lcs_case_gen.py:263-270 的尾参）+ 生成器守卫放开
  （ldb_review_case_gen.py:116-117 当前响拒非零；STB 侧同型守卫待实施时一并核）。
  属 **case-gen/工具层改动，不触 RTL、不触编译器调度**；但守卫是 M3-lite 单 SMC
  复核的既有闸门，放开须按规则变更流程（记录失败形态→加鉴别测试→改规则→回归），
  不得静默绕过。
- IQ_EN 掩码按队列不按 SMC；TRIGGER 携带 subq sid 非 SMC 号 → 触发链路无需改。

### 3.4 验证器 —— 主要改动面

| 组件 | 现状 | 需改 |
|---|---|---|
| unified_lcs_compare.py | 仅 smc_0（F6） | 循环 smc_0..7：逐 SMC 读 golden/dump 比对；verdict 聚合规则（全 8 PASS 才 GREEN；任一 FAIL 报 SMC 号） |
| unified_lcs_case_gen.py | 单 SMC 数据面（F5） | 8 份 UR 预载/golden + 8 通道真数据镜像 + manifest active_smc 语义扩展（0 → 列表或新增字段，记 schema 版本） |
| 独立 fp32 参考 | 单 SMC golden 源 | 逐 SMC 切片各算一份（同一参考实现，输入切片不同） |
| VCS/板端 runner | 已逐 SMC 可比（F7） | **零改动**（供 8 份 golden 即自动 8 比对）；但 runner「缺席即 SKIP」**不得**作为 8-SMC 验收规则（用户裁定）——manifest 声明的目标缺席必须 FAIL，由 compare/manifest 一致性闸强制 |
| WBARRIER（TB） | 仅监测 smc_0（F9） | **正式验收必备**（用户裁定②）：全目标完成屏障不可作为可选增强——LOAD→COMPUTE 须所有相关 SMC 数据就绪，COMPUTE→STORE 须各 SMC 结果可读，末段 dump 须等全部目标输出完成；各通道延迟可不同，**终值碰巧正确不证明段间保障成立**。实施 = WBARRIER 掩码扩至全部目标 SMC（或证明现有汇总信号确实覆盖全部目标，举证责任在实施方）；探索运行可先行，但不得以「仅 SMC0 屏障 + 末端比对兜底」形态通过正式验收 |

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
5. **TB 监测缺口（验收阻断级，v2 升级）**：WBARRIER/S4BARRIER 均 smc_0 中心；
   按用户裁定②，正式验收**必须**全目标监测或证明汇总信号覆盖全部目标。两条路径：
   **A. 扩展 TB 监测**（WBARRIER 掩码 ×8 + S4 屏障全目标）——属原树 TB 改动，
   破「原树 RTL/TB 不改」既有惯例，**须用户单独授权并走 TB 版本化/冻结流程**；
   **B. 汇总信号覆盖证明**——需正面证明现有完成信号（LSU 完成中断语义、en=7
   下 STB 的完成聚合）确实以全部目标 SMC 为条件，举证责任在实施方，证明未立前
   按路径 A 准备。探索运行（非验收）可先以现状 TB 进行，报告须标注监测覆盖缺口。
6. **board 侧**：包结构/runner 不变（F7/F4），但本预检不以板端为条件。

## 5. 条件评估与建议路径（v2 重写）

**条件具备，但使能不是零改动。** v1「使能零改动」作废后的真实改动面：
① case-gen 数据面 ×8（8 份可区分 UR/golden + 8 通道真数据镜像）；
② LOAD/STORE 使能 ×8（发射参数 0→7 + 生成器守卫按规则流程放开）；
③ compare 验证面 ×8（逐 SMC 比对，缺一即 FAIL，禁 SKIP 兜底）；
④ manifest schema 版本化（active_smc 语义扩展 + 与指令使能/输入/输出一致性校验）；
⑤ 全目标完成屏障（§4-5 路径 A 需 TB 改动授权，或路径 B 覆盖证明）。
编译器调度零改动、runner 零改动的结论维持。
建议路径（按用户 2026-10-14 五步令执行）：

1. ✅ 步①：修正预检 + 解码代表 case 使能与地址（本稿 v2 + §2.5 矩阵）；
2. 步②：定义等长分片配置/manifest/全目标完成条件（n_total/n_per/active_smcs，
   缺目标结果必须 FAIL）；
3. 步③：scale_sum 首证 n_total=128、n_per=16——8 份**可区分**输入、独立 fp32
   参考逐 SMC 各算一份，8/8 通过且零 SKIP；
4. 步④：负控（错置一个 SMC 分片、缺一个结果文件均可检出）+ 第二规模；
5. 步⑤：手写对照（映射与指标统一后，小规模起步，不从 n_total=8160 开始）。
报告口径：数据面证据 = 8/8 SMC bit-exact；不宣称吞吐倍数（段拍主项是 TB 装载），
性能解读按 perf_metrics_definition §4 纪律。
