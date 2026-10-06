---
adr_id: ADR-059
title: M3-lite 门②——LDB 复核（外部存储装载已知数据 → UR 落位/长度/burst 尾部/完成时序）
decided: 2026-10-06（v2 按用户三处契约修正重写；v2.1 实跑后按用户裁决按实测收口——数据面 GREEN + 完成时序否定性结论；修正落实见文末对照表）
last_verified: 2026-10-06
still_holds: proposed
builds_on: ADR-058, ADR-050, ADR-052
user_decision: user 2026-10-05「2. LDB 复核：从外部存储装载已知数据；UR 实际落位正确；长度、burst 尾部和完成时序有证据」+ user 2026-10-06 三处修正（ur_wr_line_cnt 前科处理 / B4c 独立初值 / B5 完成时序实证）+ Q1/Q2 批复
---

# ADR-059 · M3-lite 门②：LDB 复核

> **状态：proposed（用户 2026-10-06 裁决按实测收口）**——数据面全绿；
> 完成时序 = 否定性实测结论（LSU 批完成中断不保证 UR 落位，lsu_complete
> 采样竞态机制），限制落账，门③携带屏障约束（ADR-060 草稿）。
> 门①已 GREEN（ADR-058 proposed，develop@799efee，A5 errata 随本裁决补）。
> 门②是门①的**对偶**：数据流向反转（HBM→UR），机器大部复用。本文只记差异与新增取证。
> v2 相对 v1 的全部变更由用户 2026-10-06 评审意见驱动，逐条落实见文末「修正对照表」。
> v2.1 变更：B5c 精确锚点 + 实测发现段（见「实施进展与实测发现」）。

## 实施进展与实测发现（2026-10-06，v2.1）

**L1/L2 实跑结果**（VCS，工具与断言按本 ADR 实现；证据归档
`verification_v2/artifacts/m3lite_gate2_20261006/`）：

| 断言族 | L1（16 行单 burst） | L2（33 行 16+9+8 尾 burst） |
|---|---|---|
| B1-B3 落位数据/地址/数量 | ✅ 全对 | ✅ 全对 |
| B4a/B4b/B4c（含 56 项 sentinel′ 装载生效+无泄漏） | ✅ | ✅ |
| B5a 通知到达+无超时 / B5b 写次数==total（commit 级）/ B4b′/B4c′ | ✅ | ✅ |
| B6 HBM 源区无损 | ✅ | ✅ |
| **B5c 完成时序（修正③）** | ❌ **通知@150 ≺ 首写@166（末写@181）** | ❌ **通知@270 = 末写@270，LDB 信用@272** |

**发现（机制已逐环取证，非推测）**：**LSU 批完成中断不保证数据落位。**

1. intid_hw 自增（= 通知源，smi_ctrl_regs.v:281/:285）由 **batch_done** 触发
   （非 ir_err_evt——取证探针 IR_ERR 全程未触发；INT 读回 0x101，int_err=0）。
2. batch_done 要求 IQ26/27 两队列 rdi_done；LSU 队列经 `smi_iq_lsu_complete`
   门控（smi_top.v:591-621：dispatch-done 后等 outstanding 排零）。
3. **竞态**：L1 实测 DISPATCH_DONE26/27@148 → BATCH_DONE@149 → INTR@150，
   而 RDI_DONE26/27@150 时 **outstanding=1**——lsu_complete 的 WAIT_DRAIN 在
   指令从 inst_queen 到 dly_queen 执行计数递增的流水线空窗内采到
   outstanding==0，**排空门形同虚设**。
4. 真实完成信号（单元信用返回）远在其后：L1 CRD_LDBAXI@178 / CRD_LDB@183；
   L2 CRD_LDBAXI@230/249/267（三描述符）/ **CRD_LDB@272（晚于通知@270）**。
5. L1 与 L2 形态差异仅改变竞态表现：L2 的 LDBAXI 队列因 WAIT_DRAIN 首采时
   outstanding 已非零而「等对了」（三信用排零后才放 done），但 LDB 队列
   （26）依旧提前（@202，outstanding=1）——批完成取两队列 done 的与，
   语义下限不变。
6. 与 ADR-050 前科的关系：修正①证伪的是 2026-07 旧 RTL 的 residue re-baseline
   机制；本次实测的提前通知是**另一机制（lsu_complete 采样竞态）**，「提前 done」
   这一行为类别在当前 RTL（2026-07-21 版）依然存在。
7. **对门①的回溯影响（待用户裁决是否落 ADR-058 errata）**：门① A5 的完成时序
   证据为「结构层 outstanding 排空 + 观测层 dump 晚于 intr」，其结构层前提
   被本机制证伪；门①数据断言（A1-A4 dump 级）不受影响（dump 时刻远在写完后）。

**为何数据面仍全对**：TB/软件在通知后还有轮询+ack+扫尾数十拍才 dump/进入下段，
落位延迟（L1 ≈31 拍）被自然掩盖——w033 golden 及历史流程同理。这是「侥幸正确」，
不是契约保证。

**处置状态**：B5c 按修正③原文判 RED，**不放宽判据、不调期望**。门②能否收口、
以何口径收口（见「待裁决」），待用户决定。

### 待裁决（2026-10-06 呈用户）——**已全部裁决（用户 2026-10-06）**

- **J1 门②收口口径 → 按实测收口**：门②判据原文「完成时序**有证据**」以实测记录
  满足——证据的实质内容是否定性的（硬件不提供「写先于通知」保证）。门② =
  **数据面 GREEN + 完成时序否定性结论（机制+证据）** 收口，限制落账。
  **比对器 B5c 保持严格判据不改**——其 RED 是如实记录硬件行为的预期签名，
  不是待修测试失败；回归中该签名（L1 gap=-31 / L2 gap=0）即为基线。
- **J2 门③屏障 → 携带**：门③ LOAD 段契约须含显式落位屏障约束——LOAD 段通知后、
  依赖段发射前插入固定等待，等待值以实测滞后又加裕量标定（L1 实测通知→末写
  滞后 31 拍；屏障值与标定方法见 ADR-060 草稿），不承认「自然延迟够用」。
- **J3 门① errata → 补**：ADR-058 增 errata——A5 完成时序证据措辞降级为
  「通知到达 + 观测层 dump 晚于通知」，注明「结构层 outstanding 排空」前提被
  门②实测机制（lsu_complete 采样竞态）证伪；A1-A4 数据结论不变。

**门②收口状态（裁决后）：proposed**——数据面 B1-B4c/B5a/B5b/B6 双用例全绿；
完成时序项 = 否定性实测结论（见上节），作为硬件语义事实与限制登记，
供门③屏障设计与论文引用。

## Context

用户门②判据（逐字）：预置外部存储，由编译器生成 LDB/LDBAXI 指令；
**UR 实际落位正确；长度、burst 尾部和完成时序有证据**。
范围纪律同门①（单 SMC、单 chunk、安全规模、顺序执行）。
用户 2026-10-06 补充：门②只证明生产发射组件生成的搬运指令行为；公式到完整程序的
自动集成仍由门③承担，本门不宣称门③集成通过。

### 侦察结论（2026-10-06，全部有出处）

**现成，不重复建设**：
- LDB/LDB_AXI schema 与发射路径早已在生产链（modules.yaml:232-261；payload_factory
  前缀匹配基建 ADR-050 期就绪；槽号 26/27 跨 5 版本稳定）。
- **golden 先例族 `mul_scalar_boundary_{fp16,fp32}_w*`**：逐宽度扫尾；
  w033 字段级解码（2026-10-06 重解，iq_field_diff）：
  - `iq_ldb.hex` 2 行：`UR_ADDR=0/4096`（bank0/bank1）、`UR_WR_LINE_CNT=33`、
    `LINE_BYTE_CNT=15`、`LINE_STRIDE=1`、CONST/PRE_FILL/POST_FILL=0、SMC_ID_EN=7、LOOPS=0；
  - `iq_ldb_axi.hex` 6 行：每 bank 三描述符 `BRST=15/8/7 @ gr_base 0/256/400`
    （bank1 镜像 @4096/4352/4496），**聚合配对恒等 33=16+9+8** ✓；
    GR_INTLV_ADDR=0x10000000、TID=0、SMC_ID_EN=7、LOOPS=0、INC=256（惰性常数）。
  - LDB 填充字段全 0；LDB 侧 TID=0（与 STB 侧 golden tid=2 非对称，各照抄各 golden）。
- 完成链路与 dump 机制同门①（同一 TB，双完成通道已实测）。
- 上板已有整体证据：llama r2 mul_scalar（含 LDB 装载）40/40 SMC DDR bit-exact
  （记忆 [[u280-llama-r2-onboard-pass]]）——门②补的是**单通道分项证据**。

**关键订正（缺席断言的正面取证，口径 a 修订）**：此前会话摘要曾记「编译器产 K16 LDB
（VCS 已通）」——本轮搜索 `~/u280_sim_v422/arith_regression` 与 `test_atomic_ops` 两树，
在**当前可访问归档中未找到** fp16_matmul_k16 的 VCS 运行痕迹（沙箱 multi_inst_stage 内
0 个 fp16_matmul case；build/ 侧仅有 python sim 日志）。该说法**不计入已验证覆盖**
（不扩大为「编译器 LDB 从未验证」）；「由编译器生成」口径沿用门① Q1（生产发射组件，
非 DFG，非 K16 embed 形态——embed 有 LOOP_PARA_SET 自锁前科，且 golden LDB 全平坦无 embed）。

**机制注意**：golden case 的 `hbm_data/memory_init*.hex` **只被 FPGA 侧 runner 消费**
（grep 全沙箱：package_sim_to_fpga.py + fpga_host_runner，TB/多阶段脚本零引用）——
VCS 流只读全局 `+HBM_INIT_DIR`。门②沿用门① D5：生成器写全局 + case 留快照作真值源；
全局目录的共享状态纪律见 D5（口径 b 修订）。

**gate① 副产品可直接复用**：S1-S3 运行证实 smc_1..7 空目录方案对 **ur_result**
dump 同样生效（TB :962 同门控）；ur_write_trace.log 探针在门① case 实际生成
（格式 `# cycle smc bank we addr wbe wdata`，TB :2460/:2497，常开无 plusarg）。

**探针覆盖缺口（L1/L2 首跑抓获，已修）**：原 ur_write_trace 探针只挂 CRU 提交口
`r_we_pipe`（Port0-3 仲裁后，user_register.v:190-248）——**LDB 写 UR 走 URA 独立
通道**（ldb_ur_axi_conv `.ur_we/.ur_waddr/.ur_wdata` → SMC_ALL.sv:990-992 →
user_register `i_ura_*` :90-92 → bank 写 mux :353），绕开 Port0-3 仲裁，首跑 trace
为空而落位全对（B1-B4c/B6 全绿反证）。已在 TB 探针块尾部补 **URA 支路**
（同 7 列格式、共享 iq_probe_cycle、纯观测不驱动任何 DUT 信号、bank 解码自
i_ura_waddr[14:12]）；simv 需 `--rebuild` 重建一次。比对器零改动。

**TB 改动透明呈报（两处，均 additive 纯观测，不驱动任何 DUT 信号）**：
① 上段 URA 探针支路（g_urw_ura）；② 完成通知精确锚点探针——对
`dut.u_smi_top.u_ctrl_regs.intr_ro` 上升沿打印 `INTR_ASSERT @probe_cycle`
（缘由与语义见 D2-B5c v2.1 与 R5 处置记录）。两处改动均已进入本 ADR 与
进展日志，随归档固化。

### 修正①取证：ur_wr_line_cnt>1 前科 vs 当前 RTL（2026-10-06，用户要求的同字段/同版本/同模式确认）

用户指出：手写线曾注明该字段 >1 出现提前 done、UR 落位不稳定，不能仅凭 golden 文件
存在认定安全。正面取证结果：

1. **前科原文定位**（ADR-050，2026-07-02/03，当时 RTL）：
   - :146 **DEBT-050-LDB-STREAM-REBASE**——多 chunk 背靠背（explicit-N 多对
     LDB:LDBAXI），chunk1 只写 line16 末字节 `0x69`、**1 行即 DONE**——「提前 done、
     落位不稳」的技术本体，根因 = 写侧共享流缓冲 residue 不按描述符 re-baseline；
   - :147 **非对称 1-write/N-read → VCS bank1=0（证伪）**——当时结论
     「RTL 要求 LDB:LDBAXI 1:1 配对」。
   - 同时段 :131/:132：**单描述符** CNT>1 本身无前科——K16/K32/K128
     （ur_wr_line_cnt=lines，1:1 配对）全 VCS bit-match。
2. **同一字段确认**：是同一字段（ADR-050 :25 schema 与今解码同位同宽）。前科语境 =
   多描述符配对形态，非单描述符 CNT>1 本身。
3. **RTL 版本已变（关键新事实）**：当前沙箱 `ldb_ur_axi_conv.v` mtime **2026-07-21**，
   晚于前科；**brst=0 死锁守卫已修**（:833-835 特判 `brst==4'd0 → DONE`，注释明示
   4-bit underflow——即 DEBT-050-LDBAXI-BRST0 的修复）；residue 块 :1205-1225 已改写
   （出现 receiving-while-sending 条件分支）。→ **7 月证伪结论不自动适用于当前 RTL**。
4. **同模式实证（w033 golden，当前沙箱）**：w033 的 LDB 形态正是 7 月证伪的
   **1-write/N-read**（1 条 CNT=33 ↔ 3 条 LDBAXI/bank）。本轮重读其 2026-09-07 运行
   产物：**8/8 SMC 各自 per-SMC golden 逐行一致**（dump 与 iq hex mtime 同 run 窗口
   16:08:16-18）；smc_0 bank0/bank1 落位区 == 源 `hbm_data/memory_init1.hex`
   对应窗口**逐行 bit-exact**（bank1 全 = 0x3fa00000=1.25 广播）；stages.md 证实
   stage_0 仅 LDB/LDBAXI 两 IQ——落位数据只能来自 LDB。证据等级标注：此为
   **2026-10-06 重读产物比对**，非当年存档 compare 报告。
5. **残留差异（门②相对 w033 的改编变量）**：SMC_ID_EN 7→0（单 SMC 使能）、
   GR_INTLV 0x10000000→0（单 SMC 无交织语义）、单 stage 纯搬运（无 compute/store 段）。
   **L1 的定位 = 这些改编变量下该形态的门②首证**，不是对「CNT>1 是否安全」的赌博；
   若 L1 失败，备选 = 逐行写方案（每行一描述符，brst≥1 拆段），届时另行评估。
6. **未独立理解的字段**：`LINE_BYTE_CNT=15`（≠16）语义未独立取证——照抄 w033
   golden 值（该值在同形态下已实证工作），登记为风险 R3。

## Decision

### D1 生成器 `ldb_review_case_gen.py`（新文件，对偶镜像，不改动门①工具）

- HBM 侧：目标颗（memory_init1，SMC_0）预载底图 + **窗口区写源数据图案**
  `src_pattern_line(k)`；2..8 颗 = mark 图案（HBM 侧观测用；LDB 只读 HBM）。
- UR 侧 smc_0：`ur_data` 全 8 bank 预置 **sentinel 图案**（非零、非源图案——未落位区
  必须保持 sentinel 才证明「只写了该写的」）；`output_bank` golden = 期望终态
  （窗口行=src 图案、其余=sentinel）——内建 compare 顺带复核。
- **UR 侧 smc_1..7（修正②：独立、确定的初始值）**：生成器为 smc_1..7 **每颗每 bank**
  显式写 `ur_data/bankN.hex` = 确定性 sentinel′ 图案（按 smc/bank/line 编号可解码，
  与 smc_0 sentinel 及源图案三者互异），期望值**生成时即钉死在期望清单 JSON**。
  可装载性已取证：multi-stage GEN 对每个 smc_* 子目录以 `ur_only=True` 模式写
  data_list（generate_data_list_coverage_multi_stage.py:260），TB 按
  `g_ur_load_path 非空即 $readmemh`（TB :2138/:2147）装载。**禁止**以首跑 dump
  反钉期望——B4c 期望全部来自生成器先验。
- 指令（平坦形态，镜像 golden w033）：LDB 行 `{ur_addr=bank<<12|off,
  ur_wr_line_cnt=total, line_byte_cnt=15, line_stride=1, const=0, pre_fill=0,
  post_fill=0, inst_id=0, smc_id_en=0}` header `{inc:16, vld:1}` **LOOPS=0**；
  LDBAXI 每 burst 段一行 `{brst, gr_base_addr 累进, gr_intlv_addr=0, inst_id=0,
  smc_id_en=0, tid=0}` header `{inc:(brst+1)*16, vld:1}` LOOPS=0。
  **聚合配对恒等 `total == Σ(brst_i+1)`**（golden w033 实证；与 STB 侧逐描述符
  恒等不同——两侧配对粒度不同，各自以各自 golden 为准）。
- 守卫（响拒，配单测）：brst=0（RTL 已修但门②范围不需要，保守响拒）/
  brst∉[1,15] / 聚合恒等 / UR 写越界 / 窗口越预载区 / smc_id_en≠0 / gr_base≤0
  或非 16B 对齐 / 描述符数>8（平坦行数上界自律）。

### D2 比对器 `ur_window_compare.py`（六项断言 + 时序子项，缺证据即 RED）

- **B1 落位数据**：ur_result_smc_0 目标 bank 窗口行 == 源图案（bit-exact）。
- **B2 落位地址**：窗口两端紧邻 UR 行保持 sentinel。
- **B3 数量**：窗口恰好 total 行被改写（终态行数）。
- **B4 非目标区**：B4a 目标 bank 窗口外 == sentinel；B4b 其余 7 bank == sentinel；
  **B4c（修正②）** smc_1..7 的 ur_result 全 8 bank == **生成器预置的各自 sentinel′
  逐行 bit-exact**（装载未生效则 dump 为 X→0 转换值 ≠ sentinel′ → RED；
  en=0 泄漏写使其偏离 → RED。装载生效与无泄漏由同一断言同证，期望值与本次执行
  结果无因果链）；附加写级佐证：ur_write_trace 中 smc≠0 条目数 == 0。
- **B5 完成通知与时序（修正③，三子项）**：
  - **B5a 通知存在**：sim.log 有 `batch interrupt` 或 `smi_iq_done` 标记，
    无 `smi_wait_and_clear_intr timeout` 子串（同 A5 双保险）。
  - **B5b 实际写次数**（独立于 B3 终态行数）：ur_write_trace.log 中
    (smc=0, bank=目标, addr∈窗口) 条目数 **== total**，每条 wbe=0xffff；
    (smc=0, bank=目标, addr∉窗口) 与 (smc≠0) 条目数 == 0。
  - **B5c 写完成先于通知 + 通知后零残留（v2.1 改精确锚点）**：锚点 =
    sim.log `[TB_MULTI_STAGE] INTR_ASSERT @probe_cycle N`——TB 新增 additive
    纯观测探针，对 DUT 内部 `u_smi_top.u_ctrl_regs.intr_ro` 做上升沿检测
    （intr_ro = intid_hw≠intid_sw，smi_ctrl_regs.v:121/:368；intid_hw 自增由
    batch_done 触发 :281-285，smi_iq_done_r 同源 :292-293——**单锚点覆盖双完成
    通道**；与 ur_write_trace 共用 iq_probe_cycle 计数域）。断言 ① 单 stage
    用例 INTR_ASSERT 恰 1 条；② `max(全部 UR 写 cycle) < N`（**严格不等**——
    同沿歧义无法排除「通知不晚于末写」，响亮 RED）；③ 全 trace 无
    cycle ≥ N 的 UR 写（通知后零残留，覆盖全部 8 SMC）；④ 交叉一致性
    N ≤ stage_0 done 清除点 P（TB :2393 原有打印，仍保留）；⑤ gap = N −
    max写cycle 数值入档。
    **v2.1 变更缘由**：L1 探针修复后首跑实测 max写@181、轮询锚点 P@210，
    gap=29 —— 轮询锚点不确定窗（轮询 20 拍 + AXI 读/ack 往返开销）即达数十拍，
    MARGIN=1000 方案必然 RED「时序不可区分」（实测确实如此，熔断按设计触发）。
    按 R5 预案改为精确锚点：TB 增强（共享沙箱资产，此处透明呈报），
    **未放宽断言语义**——严格不等与零残留判据反而收紧（同沿/沿后一律 RED）。
- **B6 源区无损**：hbm_result_smc_0 窗口 == 源图案（LDB 只读 HBM，堵写回串扰）。

### D3 用例矩阵（串行）

| 用例 | 形态 | 目的 |
|---|---|---|
| **L1 首证** | 16 行单 burst（brst=15），bank1 off 0，gr_base=0x1000 | 改编变量（en=0/intlv=0/单 SMC/单 stage）下 w033 形态的门②首证 |
| L2 | 33 行三描述符（16+9+8，brst=15/8/7），bank1，gr_base=0x1000 | **镜像 golden w033 单颗改编**：burst 尾部判据的直接证据 |
| L3（探索项，Q1 已批） | 32 行单描述符 loops=1（对偶 STB golden row5） | LDB 侧 loops 形态**无 golden 背书**——不阻断门②，不占主线；仅 L1/L2 收口有余量才做，失败转风险登记 |

负向断言：比对器 RED 注入单测（窗口数据错/落位平移/数量短一/sentinel 区被染/
B4c sentinel′ 偏离/trace 写次数错/gap≤MARGIN/通知后残留写/超时/缺 dump/缺 trace），
同门①对抗方法论。

### D4 与门①的复用边界

复用：TB/运行脚本/归档与 sha256 冻结策略/完成通道观测。新建三件：生成器、比对器、
驱动脚本（及各自单测）。不建：DFG 节点、sim 建模（门③另立）。

### D5 全局 HBM_INIT_DIR 共享状态纪律（口径 b 修订）

全局 `+HBM_INIT_DIR` 是共享状态，驱动脚本 `m3lite_gate2_run.sh` 执行纪律：

1. **串行运行**：任何时刻仅一个门②流程在跑（沿用门①纪律）。
2. **跑前备份**：生成器写全局前，驱动把当前全局 `memory_init1..8.hex` 副本 +
   sha256 清单存入 `build/m3lite_hbm_backup/<ts>/`。
3. **退出恢复**：`trap EXIT` 无条件恢复备份（含中断/失败路径），恢复后再校验
   sha256 一致；恢复动作与前后哈希打印进日志并随 case 归档。
4. 若某次实验**有意保留**全局状态，必须在归档日志显式声明「保留+理由」，
   不允许默认污染。

## 验收判据（门② GREEN 充要条件）

1. L1/L2 VCS + B1-B6（含 B5a/B5b/B5c 三子项）全过；L3 作探索项另行记录
   （过则加分，不过转风险登记）。
2. 负向注入单测全按预期 RED。
3. 证据归档 `artifacts/m3lite_gate2_<date>/`（同门①载体分级；含 ur_write_trace
   与 HBM_INIT_DIR 备份/恢复哈希记录）。
4. pytest 基线只增不减（现 442）。
5. 不发布。
6. **Q2 已批**：L1/L2 通过后立即整理门③ LOAD 段参数与证据草稿（仅记录参数，
   不建 DFG，不宣称门③集成通过）。

## 风险登记

- R1 ~~LDB 侧信用门控/配对风险低~~（v1 表述撤回）：**ur_wr_line_cnt>1 有前科
  （ADR-050 :146/:147，旧 RTL），当前 RTL 已修且 w033 同形态 8/8 实证（修正①），
  残留风险 = 改编变量（en=0/intlv=0/单 stage）未证**。L1 即该残留假设的实验首证；
  失败后再评估逐行写方案（每行一描述符），不提前称风险低。
- R2 L3 loops 形态无 golden 背书 → 已按 Q1 降探索项。
- R3 `LINE_BYTE_CNT=15`（≠16）语义未独立理解，照抄 w033 golden；若 L1 出现
  we/字节使能异常症状，首选怀疑此字段。
- R4 tid 非对称（LDB golden=0 vs STB golden=2）照抄各自 golden，语义未独立取证
  （DEBT-052-STB-TID 家族延续）。
- R5 ~~B5c 的 MARGIN=1000 为保守设定……不放行含糊通过~~（**2026-10-06 已按预案
  处置**）：L1 复跑实测 gap=29 < MARGIN → RED「时序不可区分」，熔断按设计触发；
  处置 = TB 增加 intr_ro 上升沿精确锚点探针（additive 纯观测，见 D2-B5c v2.1），
  MARGIN 方案废弃。残留限制：锚点语义 = 「通知对软件可观测的沿」（intr_ro 升起），
  非 intid 寄存器翻转当拍；完成保证的解读以「写 commit 先于通知可观测」为口径，
  不声称证明 LDB 内部 rdi_done 的成因链。
- R6 **LSU 批完成中断不保证数据落位**（2026-10-06 实测成立，见「实施进展与实测
  发现」）：smi_iq_lsu_complete WAIT_DRAIN 在 dispatch→execute 流水线空窗采到
  outstanding==0 提前放行；任何消费「批完成 = 搬运结束」语义的流程（TB 多 stage
  衔接、host 侧同步、未来编译器排布）都必须自带落位屏障。限制长期有效，
  除非 RTL 修复并复测。

## 修正对照表（用户 2026-10-06 意见 → v2 落实位置）

| 用户意见 | 落实 |
|---|---|
| 修正① ur_wr_line_cnt=total 与前科冲突：确认同字段/同版本/同模式；L1 作实验首证；不提前称风险低 | Context「修正①取证」5 点（前科定位/同字段/RTL 2026-07-21 已变/w033 同模式实证/改编变量）；R1 重写（撤回「风险低」，L1=首证，备选逐行写） |
| 修正② B4c 独立确定初值：SMC1-7 显式 sentinel + 验证加载生效；禁用首跑 dump 钉期望 | D1 新增 smc_1..7 sentinel′ 物化（GEN ur_only + TB 装载机制取证）；B4c 重写（先验期望 + 装载/无泄漏同证 + trace 写级佐证） |
| 修正③ B5 须证完成时序：末次写 vs 通知先后；写次数独立于 B3 | B5 拆三子项：B5a 通知存在 / B5b 实际写次数（trace 计数）/ B5c 完成时序锚点。**v2.1**：轮询锚点不确定窗实测过大（L1 gap=29 < MARGIN=1000 → 熔断 RED），按 R5 预案改 TB intr_ro 上升沿精确锚点（严格 max写<N、cycle≥N 零残留、与清除点交叉一致、gap 入档） |
| 口径 a：K16 表述不扩大 | Context 订正段改「当前可访问归档中未找到，不计入已验证覆盖」 |
| 口径 b：HBM_INIT_DIR 共享状态纪律 | D5（串行/备份+sha256/trap EXIT 恢复并复校/显式保留声明） |
| Q1：L3 降探索项 | D3 表格注 + 验收判据 1 |
| Q2：L1/L2 过后立即整理门③ LOAD 参数草稿，不宣称集成通过 | 验收判据 6 |
