---
adr_id: ADR-060
title: M3-lite 门③ LOAD 段参数与证据草稿（含落位屏障约束；非集成声明）
decided: 2026-10-06（DRAFT——Q2/J2 授权范围内的参数整理，未授权门③集成通过）
last_verified: 2026-10-07
still_holds: draft
builds_on: ADR-058, ADR-059
user_decision: user 2026-10-05 门② Q2（L1/L2 后整理门③ LOAD 参数草稿，不宣称集成通过）
  + user 2026-10-06 J2（门③须携带落位屏障约束）
  + user 2026-10-06 门③四步序授权（①机制核查→②修订本 ADR→③最小 LOAD→COMPUTE
    实验→④STORE 末写核查）；裁决 1：total+48 不批作正式屏障；裁决 3：STB 完成
    保证重核；裁决 4：同步方案 1-2 日预算与三分支处置
---

# ADR-060 · M3-lite 门③ LOAD 段参数与证据草稿

> **授权边界（2026-10-06 用户新指令更新）**：门③**设计核查与最小实验已获授权**
> （四步序：① 完成机制核查 → ② 修订本 ADR → ③ 最小 LOAD→COMPUTE 实验 →
> ④ 加入 STORE）。仍然**不宣称门③集成通过、不发布**；集成改动面扩大需另待指令。
>
> **⚠ 用户 2026-10-06 裁决 1 落实**：`total+48` 固定等待**未获批准**作正式
> 完成屏障（两点候选值无上界证明，仅限明确标注的实验侦察）。步骤①机制核查
> 已完成（RECON.md），§2 已按裁决修订为屏障类型定界 + 实验屏障契约：
> 实验采用 **TB 辅助信用屏障**（crd_ldb/ldbaxi，结构性末写之后），标注仿真限定；
> 正式闭环需 F6 RTL 修复报批或用户接受仿真限定口径。

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

## 2. 落位屏障：机制核查结论与方案定界（2026-10-06 步骤①②修订）

> 机制核查全文：`verification_v2/artifacts/m3lite_gate3_sync_recon_20261006/RECON.md`
> （软件可观察信号穷举 / 指令级机制 / RTL 内部信号 / 修复选项 / 方案比较 F1-F7）。

**实测背景（ADR-059 v2.1）**：LSU 批完成中断不保证 UR 落位——
`smi_iq_lsu_complete` WAIT_DRAIN 在 dispatch→execute 空窗采到 outstanding==0
提前放行。实测（probe_cycle）：L1 通知@150 ≺ 写 166..181 ≺ CRD_LDB@183；
L2 通知@270 = 末写@270 ≺ CRD_LDB@272。滞后量随形态波动（+31 / +0），不可依赖。

### 2.1 屏障类型（机制核查判定）

| 类型 | 代表 | 状态 | 适用范围 |
|---|---|---|---|
| **正式 host 可执行** | batch_done（0x004/0x024 双皮同源） | ❌ 已证伪提前 | 不可单独用作 LOAD→COMPUTE 依赖 |
| 正式 host 可执行 | UR 读回校验 | ❌ 死路 | BRAM_PORTA 顶层绑死（aiacc_top_f.v:1921-1925），无硬件路径 |
| 指令级 | COND / DLY | ❌ 死路 | COND 双死（EN_COND=0 + ir_ready_bus LSU 位未驱动），语义为就绪撮合；DLY ≤7 拍无界 |
| 编排级 | START 队列自动链接 | ❌ 反向 | 把竞态直接当段间依赖（smi_ctrl_regs.v:304-311） |
| **TB 辅助实验** | 层级观测 `crd_ldb_o`/`crd_ldbaxi_o` 信用齐（结构性末写之后）再放行依赖段 | ✅ **实验屏障（采用）** | **仅限 VCS 仿真**；必须标注 TB 辅助，不得冒充 host 协议；非上板语义 |
| RTL 修复 | lsu_complete seen-nonzero / 信用对账（RECON §4 A/B） | ⏸ 单独报批 | 改动/版本/回归影响按用户裁决 4 分支 2 单独呈报后决定 |
| 固定等待 | total+48 等 | ⚠ 降级 | 用户裁决：无上界证明，仅限**明确标注的实验侦察**，不作正式屏障 |

### 2.2 屏障契约（步骤③④实验遵守）

1. **LOAD→COMPUTE**：LOAD 段发射后，TB 等 `crd_ldb_o` 与 `crd_ldbaxi_o` 的
   done 计数达到当段发射指令数（双通道齐），才写 COMPUTE 段 START；
   断言 COMPUTE 对 UR 窗口的首次有效读 cycle **严格大于** URA 末写 cycle。
2. **STORE→比对**：以 TB HBM SRAM 模型写口为外部写完成锚点，末次窗口写
   先于任何比对/发布动作；`crd_stbaxi_o` 仅证明 AXI 受理，不作终局证据。
3. **超时**：每道屏障带拍数上界（初值 5000 拍，实施时按实测收紧），
   超时即 RED 响亮失败，不允许静默放行、不允许以调大上界消红。
4. **未验证即拒发**：屏障未实际生效（探针缺失/计数不符/锚点缺席）的 run
   一律不得进入比对与发布；门③的发布级闭环在 F6 落地并回归、或用户明示
   接受「仿真限定闭环 + 限制落账」之前**不宣布**。

### 2.3 撤回记录

v1 草案的「信用无 CSR 可观测 → 固定等待唯一可行」论断被用户驳回为过早
排除其他路径；经步骤①核查修正为：CSR 面确无信用出口（§2.1 表），但
指令级/编排级/修复路径须一并排除或呈报后才得下结论——现已完成排除
（RECON §5）。

## 3. 证据指针

- 归档：`verification_v2/artifacts/m3lite_gate2_20261006/`（README + sha256 清单；
  比对器 verdict 可对沙箱 case 目录复跑重现）。
- 生成器/比对器/驱动：`verification_v2/tools/{ldb_review_case_gen,ur_window_compare}.py`、
  `m3lite_gate2_run.sh`；单测 `tests/unit/test_{ldb_review_case_gen,ur_window_compare}.py`。
- TB 探针（additive 纯观测，三处）：URA 写支路 / INTR_ASSERT 精确锚点 / 取证探针组；
  原文与 TB 哈希在归档内。

## 4. 明确未做（授权边界外，全部不宣称）

- 门③**集成**（LOAD→COMPUTE→STORE 自动组合、stage_partition/stage_manifest/
  sim_config/run_v1_verify 改造、DFG 节点）——四步序授权的是**设计核查与最小
  实验**，不是集成；集成改动面扩大另待指令（用户：「当前最优先的工作是建立
  可靠的段间依赖，而不是先扩大集成改动面」）。
- L3 loops 形态 LDB（探索项，未做）。
- COMPUTE 段与 STORE 段的门③参数化（STORE 侧可镜像 ADR-058 S1-S3 参数）。
- F6 RTL 修复（须单独呈报改动/版本/回归影响，用户裁决 4 分支 2）。
- 上板语义：TB 辅助屏障仅存在于仿真，任何上板声明另行立项。

## 5. 步骤③④实验设计要点（已获授权，实施前以此为准）

- **③ 最小 LOAD→COMPUTE**：输入仅外部存储；UR 预置与源图案互异的 sentinel
  （沿用门② B4c 纪律，期望先验钉死）；TB 辅助信用屏障（§2.2-1）放行
  COMPUTE；新增 CRU 读口窗口探针断言「COMPUTE 首读 > LOAD 末写」；
  数据终值比对沿用门①②比对器。
- **④ STORE**：COMPUTE 结果经 STB 写出；以 TB HBM SRAM 写口锚点独立核查
  最后外部写事务完成（§2.2-2），其后才允许比对与（不）发布判定。
  **（已按用户 2026-10-07 五条要求细化并实施于 §6。）**
- 负向断言：屏障被旁路（探针强制提前放行）时，COMPUTE 首读断言必须 RED——
  证明屏障与断言都活着。
- 超时与拒发纪律见 §2.2-3/4。

## 6. 步骤④ STORE 设计（2026-10-07 设计冻结——实施前以此为准）

> 用户 2026-10-07 五条硬性要求逐条落地。全部 TB 机制 additive、默认关、
> `[GATE4]` 标记、TB 辅助标注（非 host 可执行协议、非上板语义）。

### 6.1 用户五条要求 → 机制映射

| 用户要求（逐字意译） | 机制 |
|---|---|
| ① 区分指令发出/信用返回/HBM 模型实际写入/写响应；禁复用已被证伪的 batch interrupt | 四事件各自独立 TB 锚点（§6.4 表）；完成判据只认 ③+④，信用仅记录不门控 |
| ② 精确写账（地址/数据/字节使能/数量/重复/遗漏/非目标写） | `hbm_write_trace.log`（RAM 写口落实级，8 SMC 全探）+ 比对器 C6 七子断言 |
| ③ 结束顺序：所需写事务全部完成后才允许 dump/比对/结束 | S4 屏障门控 `do_ur_hbm_dump`（§6.3-3）；超时 `$fatal` 响亮失败 |
| ④ 完整链路逐位比对（外部输入→LOAD→COMPUTE→STORE→外部输出 vs 独立公式参考） | 3-stage case（v3 stage_2 物化）+ C8（HBM 输出窗 == 独立 fp32 struct 参考） |
| ⑤ 保持力测试（延迟/反压使屏障实际等待）+ 超时会失败退出 + 故障注入与正向分别归档 | WBARRIER/S4 正向自然等待实测 + `+G3_AXI_BP_W` 反压注入 + 两段超时注入（fatal_expected 比对模式）；`round6_positive` / `round6_faultinjection` 分目录归档 |

### 6.2 STORE 侧信用不对称（RTL 实证，B5c 危害类的 store 侧扩展）

- **STB(q24)** done 在 **AXI-Stream 末拍交给 DS 时**即发
  （`stb_ur_axi_conv.v:558-561` → done `:775-782`）——早于一切 AXI 活动。
- **STBAXI(q25)** done 在 **DW_axi_gm 写缓冲接受末拍**即发
  （`:1023-1043`；done 仅 `if(i_saccept)` `:1139-1148`）——严格先于 W 握手、
  无条件先于 B 响应；B 仅用于 err 捕获（`:1262-1272`，进 `crd_stbaxi_o[6]`）。
- 对照 LDB：信用结构性在末笔 UR 写之后（`ldb_ur_axi_conv.v:1385/:1461-1467`）。
- **结论：STORE 完成判据 = HBM 模型 RAM 写口落实 + B 响应计数；信用只记录、
  不门控。** §2.2-2「`crd_stbaxi_o` 仅证 AXI 受理」实测还要更弱（写缓冲接受即
  信用，未达 AXI 受理）。
- LSU 完成模块 `smi_iq_lsu_complete` 对 q24/25 与 q26/27/92 同构
  （`smi_top.v:592-621`，同一 WAIT_DRAIN 采样结构）⇒ stage_2 的 intr 同样
  不可作完成依据（B5c 类）。

### 6.3 三道闸与挂点（compute 侧「通知早于落位」实测驱动）

- round5 pos 实物 trace：bank2 窗口写恰 16 笔（addr 0..15 各 1 次），
  **末写 @958** ≻ stage_1 `BATCH_DONE @953` / `INTR_ASSERT @954`（+4/+5 拍，
  intr 断言时 addr 11..15 五行乘积尚未落位）⇒ stage_1→2 门控禁用 intr。
- **闸① stage_0→1：信用屏障**（既有 `+G3_BARRIER`，原位不动——步骤③已验证
  流程零改动）。
- **闸② stage_1→2：+G3_WBARRIER 写落位屏障**——CRU 管道目标 bank 窗口写
  **位掩码**（`reg [255:0]`，重复写不膨胀）达期望数才放行；挂点 = 该段 trigger
  后、`smi_wait_and_clear_intr` **前**（仅该槽位前移）。正向即实测 waited>0
  （预计 ~31 拍 = 「intr 会提前多少」的动态刻度）。
- **闸③ stage_2→dump：+G3_S4_BARRIER STORE 完成闸**——SMC0 HBM 窗口 RAM
  落实位掩码达期望数 **且** ds2 B 响应计数达期望数，才允许 `do_ur_hbm_dump`；
  CRD_STB/STBAXI 计数仅入标记行记录、不参与门控。挂点 = 主循环末段
  `do_ur_hbm_dump` 前；其后打印 `[GATE4][DUMP] begin @cycle` 标记（仅 S4 开）。

### 6.4 四事件 TB 观测点（全部 additive 探针，不改 DUT 任何信号）

| 事件 | 锚点（层级路径） | 载体 |
|---|---|---|
| ① 指令发出 | 既有 `IQP_ISSUE_HOOK(24/25)` | `iq_issue_trace.log`（`cycle iq valid data`） |
| ② 信用返回 | `dut.u_smi_top.i_crd_stb[6:0]` / `i_crd_stbaxi[6:0]`（[0]=vld [1]=done [5:2]=inst_id） | sim.log `[GATE4][CRD_STB/CRD_STBAXI]` 打印 + 沿计数（镜像既有 CRD_LDB 探针） |
| ③ HBM 实际写入 | `GEN_SRAM[k].SMCk.u_sram.u_sram.gen_ram_1MB_dw_128.mem.{ena,wea[15:0],addra[19:0],dina[127:0]}`，落实=`ena && \|wea` @posedge（ram65536x128.v:25-39） | `hbm_write_trace.log` 8 SMC 全探（`cycle smc addra wea dina`），抓非目标写 |
| ④ 写响应 | TB 顶线 `bmc_ds2_bvalid & bmc_ds2_bready`（SMC0↔ds2；模型 atcrambrg300.v:340-348 产生） | sim.log `[GATE4][HBM_BRESP]` 打印 + 计数 |

- STORE 侧「读后写」时序：STB 读 UR 走 URA 读通道
  （`dut.u_smc.stb_ur_re__to__ur_i_ura_re` / `stb_ur_addr__to__ur_i_ura_raddr`，
  SMC_ALL.sv:527-528/:987-988），不在既有 ur_read_trace（仅 CRU ports0-3）
  ⇒ 新增 `stb_ur_read_trace.log`（`# cycle ur_addr`）。

### 6.5 故障注入设计（保持力 + 超时，全部明确标注）

- **反压注入（现行 v2，B 通道 AW 前置武装，2026-10-07 用户批准改道）**
  `+G3_AXI_BP_B=n`：独立 initial——`wait(rst_n)` → `@(posedge
  bmc_ds2_awvalid)`（首个 ds2 AW 上升沿 = stage_2 STBAXI 写突发地址相；
  本形态 stage_0 读走 AR 不产生 AW、host 走 us1）→ `force
  bmc_ds2_bready = 1'b0` 持续 n 拍 → `release`；`[GATE4][BP]` 标记；
  默认 0=关。TB-only（force/release TB 顶 wire，功能驱动方=DUT 主口
  aiacc_top_f.v:126），AXI 合法停顿（master 可任意推迟 BREADY；桥模型
  经响应 FIFO 保持 BVALID——atcrambrg300.v:344 采样 bready=0 时响应
  入 FIFO、:346 非空即持 bvalid），RTL 零改动；**写数据路（AW/W 载荷）
  完全不动**，全部 HBM 写按原时刻落实、仅 B 响应被推迟——直接
  考验 S4 双条件的 bresp 半条件（writes 半条件的「不满足→不放行」由
  超时注入段证明）。**武装点必须先于 BVALID 就位**：v1 以
  `@(posedge bmc_ds2_bvalid)` 武装被实测结构性迟到——bvalid 上升沿
  由时钟沿 NBA 产生，模型在产生沿已以 bready=1 采样并走空-FIFO 直通
  （:344）视为交付，B 活窗仅 1 拍，force 永远迟到（实测：释放后
  4600+ 拍零握手、writes 16/16 却 bresp 0/1）——详见 errata 6。
  期望：S4 waited ∈ [bp_release−s4_enter−2, +50]
  （精确一致性，证明 waited 确由注入造成）且 ≥ min_waited（=n−100，
  100=注入点→S4 enter 重叠+模型重驱动余量上界），数据面全绿。
  （drive-2/3 实测：该期望两轮未达成——**当前测试配置及已尝试注入
  方式下**，B 响应延迟后未能恢复；注入器/桥模型/DUT 责任未分清，
  **不得外推为硬件本身不可恢复**（用户 2026-10-07 措辞裁定）。
  本实验现行价值 = bresp 半条件持死形态证据，见 errata 6/⑦ 与 §6.7。）
- **退役（2026-10-07 首轮实证）**：`+G3_AXI_BP_W`（首个 AW 握手后押
  wready=0）——300 拍 W 反压使写路径**永久锁死**（release 后 4746+ 拍
  零恢复、CRD_STBAXI 再未发、hbm_writes 恒 0）；机制嫌疑=桥模型内部
  逻辑直接读自身 wready 输出线被 force 污染（atcrambrg300.v:442/:323/
  :422-426）。详见 errata ⑤；该注入器已从 TB 移除，勿恢复。
- **超时注入两段**：WBARRIER（`+G3_WBARRIER_TIMEOUT=1`）与 S4
  （`+G3_S4_TIMEOUT=50 +G3_AXI_BP_B=400`）——期望 `$fatal` 非零退出、
  `[GATE4][*][ERR] TIMEOUT` 标记存在且其后无 `[GATE4][DUMP]`（比对器
  `--mode fatal_expected` 断言形态）。
- plusarg 纪律：一律 `$value$plusargs("NAME=%d")`（`$test$plusargs` 前缀坑
  实证，errata ③）；新名 G3_WBARRIER*/G3_S4_*/G3_AXI_BP_B 无相互前缀。

### 6.6 断言清单（比对器扩展；无 store 键 = v1 步骤③模式兼容）

- **C0 扩**：3-stage 计划 `[(0,2),(1,13),(2,2)]`、INTR 恰 3、G3 信用屏障
  enter/release 恰各「段间转移数」（3 段=2 对：stage_0→1 真门 + stage_1→2
  直穿；第 2+ 次必须 waited==0——信用计数跨段单调不清零）、`[GATE4]` 标记
  成对（enter/release）；merged-3：`[(0,17)]`、INTR 恰 1、无任何 G3/GATE4
  标记、无 stage_1/stage_2 目录（正面取证）。
- **C3s（新）**：STB URA 首读拍 > bank2 末写拍 且 > WBARRIER release 拍。
- **C5 修**：store 开启时 HBM 输出窗（byte 0x2000..0x20FF = addra 512..527）
  豁免比对，窗外仍 == 冻结快照。
- **C6 写账（新）**：hbm_write_trace 全 8 SMC 扫描——窗口址集恰 {512..527}、
  每址恰 1 写、dina == 独立期望乘积行、wea==FFFF、非目标写（窗外 + SMC1-7）
  为零；重复/遗漏/非目标三类单列明细。
- **C7 结束顺序+四事件时间轴（新）**：issue(q24/25) / CRD_STB / CRD_STBAXI /
  窗口首写 / 末写 / B 计数 / S4 release / DUMP 八列入报告；RED 条件：
  release 或 DUMP 不严格晚于末写、DUMP 早于末 B、B 计数≠期望、信用缺失；
  信用≺写落位 gap 记 note 不判 RED（结构性预期）。
- **C8 完整链路（新）**：hbm_result 窗口 == 独立 fp32 struct 参考（128b 行
  打包同 case_gen 输入窗打包法）。
- 负控（merged-3）白名单扩 `("C3s","C6 ","C8 ")`；证活判据 = C3s/C6/C8 任一红。
- **fatal_expected 模式（新）**：仅读 sim.log 断言超时标记 + 无 DUMP 其后。

### 6.7 限制与边界（落账）

- 三道闸全部 **TB 辅助实验**（§2.2 标注纪律延续），非 host 可执行协议、
  非上板语义；VCS ≠ 硅证。
- WBARRIER 只统计 CRU 管道目标 bank 写（本 case 乘积经 RTOVR→CRU 落位）；
  若未来 case 改走 URA 写路径，屏障超时响亮失败（安全方向），届时再扩。
- S4 的 B 计数为 ds2 全端口计数——本 case 唯 stage_2 产生写事务（stage_0
  为读、host 下载走 us1），期望恰 1；形态变化时需重核。
- W 通道反压：已试注入方式下写路径未恢复（errata ⑤，注入器退役）；
  B 通道反压：三种注入点下响应延迟后均未恢复（errata 6）——
  **「DS/写路对反压的耐受」整体为开放发现**：注入器、桥模型与 DUT
  的责任边界未分清，**不得外推为硬件本身无法恢复**（用户 2026-10-07
  措辞裁定）；波形物证已留档（round6_faultinjection/
  wedge_bstall_drive3_awarmed/tb_aiacc_top_multi_inst_stage.fsdb），
  根因调查另行立项（用户 2026-10-07 任务 2，1-2 日盒，三问见 errata ⑦；
  **源码级机制假说=边界线 force 分裂视图+孤儿 B 重复捕获死锁，
  可证伪探针预测见 errata ⑧**）。
- **「延迟→恢复→GREEN」保持力形态：当前测试配置及已尝试注入方式下
  尚未实现**（用户 2026-10-07 措辞裁定，取代此前「本 TB 不可达」的
  过强表述）。现有证据的准确语义——超时负控证明的是「**缺失完成条件
  时不会错误放行**」；WBARRIER 自然 waited=30 证明的是「**该路屏障
  实际发生了等待**」；二者**不能替代**各屏障的延迟-恢复验证，
  该验证仍为开放项。

## Errata（2026-10-07，步骤③实施后实证）

1. **§5 负向断言仪器更换（屏障旁路 → 并段单 trigger merged_single_stage）**。
   原文「屏障被旁路（探针强制提前放行）时首读断言必须 RED」的仪器在实测中
   **结构性失效**：+G3_BYPASS（裸 intr 沿放行）第 4 轮跑出全绿——裸沿 ~@300
   反在末写@259 之后（B5c「通知早于落位」为单窗 L1 形态实测、提前 ~16-31 拍；
   双窗 bulk 下批中断沿反在末写后 ~5 拍——**case 形态相关，不可泛化**）。
   trace 定量（m3lite_g3_lc_w16 实物）：段间 IQ_EN 同步 ~588 拍恒在 trigger
   之前，trigger→compute 首读仅 5 拍、trigger→LDB 首写 21 拍——任何「改放行
   时机」的负控首读 ≥~700 拍，撞不上写窗（218..259）。**现行负控 =
   merged_single_stage**（case_gen `--merge-stages`：LOAD+COMPUTE 并段同
   trigger，零 TB plusarg，单段多队列系 TB 常规形态）：compute 首读 T+5 ≺
   LDB 首写 T+21、末写 T+62，读恒先于写。第 5 轮实测：C1/C3/C4(乘积账) 红
   （C3 gap=−57）、C2/C5 绿（LOAD 无恙、红纯因竞态）、stock compare 独立
   FAIL 作第三意见、总判 **NEGATIVE-CONTROL LIVE**——断言链端到端证活。
   §5 目的条款（证明断言活着）保留且首次真正达成；BYPASS 留档（比对器
   双形态支持档案复现）。证据：`verification_v2/artifacts/
   m3lite_gate3_round4_20261007/` + `m3lite_gate3_round5_20261007/`
   （sha256 冻结清单随档）。
2. **屏障「保持力」未动态压实（限制落账）**：正向三轮（3/4/5）屏障均
   waited 0 拍——信用脉冲（@235/@261）先于轮询清断（@338）到达；且本形态
   裸 intr（@264）亦晚于末写（@259）5 拍，自然时序本已安全。步骤③
   「COMPUTE 首读 > LOAD 末写」结论由 **trace 直测承载**（931>259、三轮
   确定性复现），不依赖屏障延迟了什么。屏障定位 = 显式协议检查点（计数
   核验 2/2+2/2）+ case 形态变化时的防御纵深 + host 协议的仿真前导形态；
   其 timeout $fatal 通道正向未触发（**未压实通道**，落账）。一般形态下的
   强制保持力仍依赖 F6 RTL 修复（裁决 4 分支 2，单独报批）。
3. **工具链落地事实登记**：compute 段 = 13 队列（rtovr-16 曾漏，装配器守卫
   拦下后回源 mul_scalar_op.cpp:505-510 订正——「不按实际输出调期望」执行
   例）；v3↔门② LOAD 发射**按值**互校（定宽前导零 vs 去零，XOR==0）；
   `$test$plusargs` 前缀匹配坑实证（"G3_BARRIER" 误中 TIMEOUT）→ 一律
   `$value$plusargs("NAME=%d")`；TB 现行指纹 6ef2bca9…285d8（第 3-5 轮执行
   态 + 注释-only 差异，行为经重建复跑确认不变）；单测 15（门③）+ 全量
   pytest 495 passed（480+15 恰符）。
4. **步骤④首轮驱动实测（2026-10-07，TB 805a7969…1cbcc6）**：正向 3 段
   全屏障 **GREEN（C0-C8）**——WBARRIER waited=30（enter@929/release@959，
   landed 16/16；硬断言 waited>0 满足，屏障确实托住 intr 提前量）；S4 正向
   waited=0（末写@1191+B@1191 先于 enter@1219 完成，S4 保持力不由正向
   承载而由 B 反压段承载）；**信用不对称 store 侧实证**：CRD_STB@1184 /
   CRD_STBAXI@1188 vs 窗口末写@1191 → gap_stb=−7 / gap_stbaxi=−3（负=信用
   先于写落实，§6.2 静态结论的动态实测印证）；C7 四事件时间轴全序成立
   （issue@1155 → CRD@1184/1188 → 末写/B@1191 → S4 release/DUMP@1219）；
   C6 写账 16/16 址恰 1 写、dina==乘积、wea==FFFF、零非目标；C8 完整链路
   HBM 窗==独立乘积。**比对器 spec bug 修正（非 DUT 问题）**：G3 信用屏障
   按段间转移逐次触发（3 段计划→2 对 enter/release：@338 真门 waited=0 +
   @1021 直穿 waited=0），原「恰各 1」断言系步骤③两段形态遗留——修为
   「恰各转移数」+「第 2+ 次 waited==0 直穿不变量」+「全部 release 计数
   回核」，并补 2 个 RED 单测（对数错/直穿破各一）。负控 merged-3 依旧
   NEGATIVE-CONTROL LIVE（C1/C3/C3s/C6/C8 五路证伪全踩中）。
5. **W 通道反压永久锁死写路径（开放发现，已留档）**：首轮
   `+G3_AXI_BP_W=300`（inject@1174=首个 AW 握手后，release@1473，机制本身
   工作正常）——release 后直至 S4 超时 $fatal@6238500ps（**4746+ 拍**）写
   路径零恢复：hbm_writes 恒 0/16、bresp 0/1、**CRD_STBAXI 自始至终未发**
   （i_saccept 再未来——写缓冲未再接受），而 CRD_STB@1184 不受影响
   （流侧信用与 W 反压解耦，再度实证不对称）。嫌疑机制：桥模型
   atcrambrg300.v 内部逻辑直接读自身 `wready` 输出线（:442 `else if
   (wvalid & wready)`、:323 `wr_cs_en`、:422-426 次态逻辑），TB force 灌进
   模型 FSM 采样；模型 vs DW_axi_gm 配置 vs glue 的责任未分清——**留作
   后续根因调查（涉 DS 反压耐受性，或为论文素材），该注入器已移除勿
   恢复**。物证：`verification_v2/artifacts/
   m3lite_gate3_round6_faultinjection_20261007/wedge_wstall_drive1/`
   （sim.log ×2 + expect，sha256 随档）。
6. **保持力 B 通道两轮实证：ds2 B 响应在本 TB 不可延迟（2026-10-07，
   drive-2/3）**：预注册的「反压延迟→恢复→GREEN」期望**两轮均未达成**
   （如实留档，未调期望）——
   - drive-2（TB 23efdf6f…0344，BVALID 沿武装 `+G3_AXI_BP_B=400`）：
     inject@1191/release@1590 机制正常，但释放后 4600+ 拍零 B 握手；
     writes 16/16 全落位、双信用齐发、C6 写账全绿，唯 bresp 恒 0/1 →
     S4 持满 5000 拍 $fatal@62385000ps（TB:3297）、全程零 dump——
     **要求③结束顺序在「写齐+响应缺席」形态下生效的实证**。
   - 机制：正向 B 握手于 bvalid 首活拍即完成（@1191）= B 活窗仅 1 拍；
     BVALID 沿武装结构性迟到（bvalid 上升沿系时钟沿 NBA 产物，桥模型
     在产生沿已以 bready=1 走空-FIFO 直通 atcrambrg300.v:344 消耗响应）。
   - drive-3（TB de839cc8…222c，AW 前置武装，用户 2026-10-07 批准改道）：
     bready=0 于 @1174 就位（BVALID 前 17 拍）仍丢 B——**直通竞态假说
     否证**。结论：本 TB 的 ds2 B 响应对任何跨 BVALID 产生沿的停顿不可
     恢复（W / BVALID 沿 / AW 前置三注入点、两种通道一致）；子位点
     （桥响应 FIFO 捕获失效 vs DUT 侧 bready 窗口后不复位）未分清，
     FSDB 波形留档待查（round6_faultinjection/wedge_bstall_drive3_awarmed/）。
     ⚠ drive-3 数据面 4 项红系单独重跑未恢复 case 全局 HBM 预载的程序性
     失误（实载预载=a5a5 旧图案 ≠ case 输入，写痕解码证实），与 B 结论
     正交；单独重跑纪律：先把该 case 的 memory_init_snapshot 恢复进
     data_engine/sram/ 再跑。**用户 2026-10-07 裁定：drive-3 继续作为
     污染运行保留；依赖正确初态的结论只能由恢复预载后的干净运行支撑**
     ——AW 前置武装形态的 B 结论列入任务 1 干净复跑补证（BVALID 沿武装
     的同类结论本由干净的 drive-2 承载）。
   - 超时两腿（drive-2）：WBARRIER timeout=1 → $fatal@9495000ps
     （TB:3266）其后无 DUMP ✓；S4 timeout=50+B 反压 → $fatal@12885000ps
     （TB:3297），writes 16/16 + bresp 0/1 精确隔离 bresp 半条件 ✓。
   - 证据矩阵收口：S4 保持力由「持死→响亮超时」双形态（bresp 缺：
     drive-2/3；writes 缺：drive-1 errata ⑤ + to_s4）与 WBARRIER 自然
     waited=30（正向硬断言）联合承载；「延迟→恢复→GREEN」形态在
     **当前测试配置及已尝试注入方式下尚未实现**（用户 2026-10-07
     措辞裁定后的准确表述；各证据的精确语义边界见 §6.7 与 errata ⑦）。
   - TB 指纹链：6ef2bca9…285d8（round4/5）→ 805a7969…1cbcc6（drive-1）
     → 23efdf6f…0344（drive-2）→ de839cc8…222c（drive-3，仅 BP 注入器
     武装点+注释差异；G3_AXI_BP_B=0 时与 23efdf6f 行为全同，正/负/超时
     腿证据不受指纹差影响）。单测：门③文件级 37/37（+2：屏障对数/直穿
     RED），全量 pytest 517。
7. **用户 2026-10-07 复核指令落实（两处结论收紧 + 路线裁定）**：
   - **收紧①**：「延迟→恢复→GREEN 本 TB 不可达」过强——改为「**当前
     测试配置及已尝试注入方式下，尚未实现恢复**」。注入器、桥接模型与
     DUT 的根因未区分前，不得认定硬件本身无法恢复（§6.5/§6.7/errata ⑥
     已按此改写；round6 两份归档 README 系 sha256 冻结载体，保留旧措辞
     不改，以本 errata 为准）。
   - **收紧②**：超时负控证明的是「**缺失完成条件时不会错误放行**」；
     自然 waited=30 证明的是「**某一路屏障实际发生了等待**」。二者
     **不能替代所有屏障的延迟-恢复验证**（§6.7 已按此改写）。
   - **drive-3 污染运行裁定**：继续作为污染运行保留；依赖正确初态的
     结论只能由恢复预载后的干净运行支撑。
   - **批准的对外的结论句（论文/进展总览可用，逐字）**：
     「M3-lite 已取得 TB 辅助的端到端功能正确性及缺失完成条件时拒绝
     放行的证据；host 完成协议和反压恢复仍是开放问题。」
   - **授权两个有界任务（不立即修 RTL、不同时开三条大主线）**：
     任务 1（半天）= 固定可恢复实验版本、消除人工预载步骤——run.sh
     每次自动恢复对应 snapshot、校验哈希、串行占用共享预载目录、
     明示测试时 TB 与当前 TB 的差异；任务 2（1-2 天）= 核查反压注入
     合法性、定位阻塞责任边界，三问：①停顿期间通道协议是否保持、
     有无「一端认为握手成功另一端未接受」的不一致；②B 响应是根本
     没生成、已生成未保存、还是已消费未计入观测；③解除注入后阻塞
     在哪个状态或未清计数上。
   - **F6 RTL 不授权直改**：先形成具体修复提案（修改点/为何消除提前
     完成/影响哪些 LSU 路径/需要哪些回归）再报批。
   - **统一入口 `tb_assisted_experiment` 通道**：可随后增加，用于一键
     复现本实验结果，但**不得进入普通 V1 已验证包通道**，且不得掩盖
     host 同步尚未解决的事实（本条为随后项，本 errata 落地时未实施）。
8. **任务 2 源码级机制假说（2026-10-07，探针确认待跑——当前为源码推演
   + 归档日志旁证，非仿真新证据）**：反压「不可恢复」的嫌疑从 DUT/桥
   功能缺陷转向**注入方式本身**——force 打在桥↔矩阵边界线
   （bmc_ds2_bready）上，制造了 AXI 握手分裂视图：
   - **问①（协议保持/不一致）**：矩阵 B 输入级
     `atcbmc300_ds_wdata_bresp_ctrl.v:422/:429` 的捕获条件**只认
     `ds_bvalid` 输入、不采样 bready**（`:412 ds_bready=~pending_bvalid`
     仅是输出反压）；桥 `atcrambrg300.v:344` 却严格按 bready 入账。
     force 置 0 期间：**矩阵照样收走 B 并交付 DW_axi_gm（T≈1191 直通
     或 pending 一拍），桥却认为未握手、把响应压入 FIFO 并持 bvalid=1**
     ——同一响应一端消费、一端留存，正是用户问①的分裂视图。
   - **问②（B 的三态定位）**：B **已生成**（桥 bresp_valid 脉冲）、
     **已被矩阵/DUT 消费**（矩阵捕获不依赖 bready；旁证：drive-2/3
     BP 日志 INTR×3 正常、CRD_STBAXI@1188 正常、HBM_BRESP 打印恰 0 次）
     且**桥内留存孤儿副本永未被取走**（FIFO 入而未出）。
   - **问③（解除后阻塞点）**：孤儿 bvalid=1 持高 → 矩阵在 T+1/T+2 把它
     **当作新 B 重复捕获**进 pending（此刻 bresp_cnt 已随真交付清零）
     → `slv_bvalid=(pending|ds_bvalid) & ~bresp_cnt_empty` 被
     `bresp_cnt_empty` 否决 → 永不上呈 master → pending_bvalid 永 1
     → `ds_bready` 永 0 → 桥 FIFO 永不排空。死锁自锁，与释放无关。
     W 通道 drive-1 锁死同属一类（桥自读自身 wready 输出 :442 等，
     force 污染模型 FSM）。
   - **为何正向永不破**：直通形态 bready=1 时桥不入 FIFO（:344 直通
     消耗），bvalid 仅 1 拍脉冲，矩阵拍后即交付，无孤儿可复制。
   - **推论（对用户收紧①的直接回应）**：现有证据**不再支持**「写路
     反压耐受」作为对 DUT/模型的怀疑——三注入点全部打在边界线上，
     该位置在本链路结构（矩阵捕获不采 bready + 桥 bready 严格入账）
     下**原理性不可能**模拟合法反压。合法 B 反压注入点 = master 源头
     （DW_axi_gm 侧 bready，使矩阵 mst_bready=0 → pending 正常捕获 →
     ds_bready=0 双方同视图）或矩阵路由内部；边界线 force 已证死。
   - **可证伪探针预测（待跑，诊断 TB 补丁 additive 默认仅 BP 激活）**：
     释放后 64 拍内 bmc_ds2_bvalid 恒 1、bmc_ds2_bready 恒 0；
     桥 u_bresp_fifo.empty 恒 0（孤儿常驻）；矩阵 ds2 口
     pending_bvalid 自 ~1192 起恒 1、slv_bvalid 恒 0、bresp_cnt_empty=1。
     任一不符即推翻本假说。预测若中，则「恢复」在边界线注入下结构性
     不可达，BP 实验应以 master 侧注入重做（届时再评估「延迟→恢复」
     形态是否可达）。
   - 旁证核查命令留档：`grep -c "GATE4..HBM_BRESP" 归档/drive*/sim*.log`
     = 0/0；`INTR_ASSERT` 各 3；CRD 时序与正向逐拍吻合。
   - **探针路径侦察收口（2026-10-07，纯只读源码核查，探针补丁待干净
     复跑后上）**：
     - 桥实例：ae350_ram_subsystem.v :171/:266 两个 atcrambrg300 系
       `ifdef NDS_FPGA` / `else` 互斥编译分支、同名 `u_sram_brg`，仿真
       态仅一份激活 ⇒ 探针路径
       `GEN_SRAM[0].SMC0.u_sram.u_sram_brg.bresp_fifo_empty`。
     - 矩阵 ds2 口实例名 `ds2_ctrl`（atcbmc300.v:19401，
       `ifdef ATCBMC300_SLV2_SUPPORT`），wdata_bresp_ctrl 子实例名
       `ds_wdata_bresp`（atcbmc300_ds_axi.v:1315）⇒ 探针路径
       `dut.u_atcbmc300.ds2_ctrl.ds_wdata_bresp.{pending_bvalid,
       slv_bvalid,bresp_cnt_empty}`。
     - **LSU 写主口身份**：SMCk 的 STBAXI 写经矩阵 us(3+k) 口——
       `us3_bready = de_stb_bready[0]`（aiacc_top_f.v:877），由 DS 引擎
       stb_bready 输出驱动（:2016，generate 版 :2139）；us2 tie 1（:850）
       与本链路无关。⇒ **合法 B 反压注入点具体化**：force
       `de_stb_bready[0]`（master 源头；矩阵 mst_bready[3] 随之为 0 →
       pending 正常捕获 → ds_bready=0，桥/矩阵同视图），无需再碰
       边界线。此亦为「延迟→恢复」形态重做的候选注入方式（任务 2
       收口后评估）。
9. **任务 1 干净全量复跑验收 + 执行漏洞修补（2026-10-07 终端接力轮）**：
   glm-5.3 分类器连续 5 次超时，按破阻协议移交用户终端接力；新 case 基名
   `m3lite_g4_task1_clean_20261007_codex` 五腿全跑（接力前一次普通沙箱
   尝试撞许可证失败，该次不作验收证据，已排除）。本会话事后重读产物
   逐标记核实：
   - **任务 1 出口验收通过**：五腿 gen 全 exit=0；每腿预载门
     RESTORED+VERIFIED（8/8 ×5）；退出 trap 恢复校验一致
     （SHA256SUMS.before == after_restore）；五份报告 provenance 双指纹
     一致（de839cc8…222c，TB 未动）。
   - **drive-3 干净补证落地**：干净预载下 BP 腿数据面全绿（C4 写账五项、
     C6 写账七项全 PASS），drive-3 的 +4 数据面假红消失——确系预载污染
     所致，与 B 结论正交（errata ⑥ 判断获干净运行支撑）。信息性
     positive red=21 warn=2，与干净的 drive-2 同类形态一致；fatal_expected
     正式判定 PASS（S4 持满超时 $fatal、零 dump）。⚠ 本轮 BP 腿**不得**
     描述为「反压恢复通过」——其为「缺失完成条件时不会错误放行」的
     证据；「延迟→恢复→GREEN」在当前测试配置及已试注入方式下仍未实现。
   - **正向腿跨运行确定性复现**：全部关键拍号与 round6 drive-1 逐拍一致
     （屏障 338/1021、WBARRIER waited=30 @959、CRD 1184/1188、末写/B
     1191、S4/DUMP 1219、C3 gap=672）——同 seed 确定性重建跨进程成立。
   - **执行漏洞（接力方验收时发现，本轮修补）**：①生成器因目录已存在
     失败后旧 g3_expect.json 仍在、原纯存在性检查放行 → 可能沿用旧产物
     继续跑；②脚本最终 exit=0 不代表各腿通过。修补：五腿 gen 非零即
     fail-fast 拒跑（exit 1，提示换基名/人工清理）；脚本最终退出码 =
     五腿预注册期望形态汇总（exit=0 ⇔ 全部 AS-EXPECTED；任何方向偏差
     含「意外变绿」均 exit=1 交人工复核）。接力轮落地项透明呈报：每腿
     console log tee 留存 $BK + 跑末 SHA256SUMS.reports 归档步骤系接力轮
     增补，已复审保留。3 个静态锚定测试（tests/test_m3lite_gate3.py
     run_sh_*）。归档：
     `verification_v2/artifacts/m3lite_gate3_round7_task1clean_20261007/`
     （README 定量表 + 26 项 sha256 冻结清单）。
