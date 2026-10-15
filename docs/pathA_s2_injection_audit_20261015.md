# 路径 A 步骤二 · 注入点只读审计（2026-10-15）

**裁定依据（用户 2026-10-15）**：暂停②③③b注入；先核对本轮 RTL/TB 指纹、实际
force 层级、信号驱动关系及握手日志；解释与既有 us3bp 合法注入证据的差异；
不得直接恢复已退役的边界 force；确认「同一握手两端视图一致、阻塞期间 VALID
及载荷稳定、释放后无丢失或重复响应」后再选定注入点；暂不扩大到 LOAD 侧；
时间盒内找不到合法注入方式则保留正常运行+全目标监测证据，明确
「延迟恢复/保持力未验证」，不升级相应验收结论。

本文 = 该裁定的只读审计轮交付。除注明「实测」外，全部结论为 RTL 直接读码 +
既有运行日志核对；未新跑任何注入。

## A. 指纹核对

| 项 | 结果 |
|---|---|
| RTL（pA 沙箱 vs 冻结原树 u280_sim_v422） | `diff -rq data_engine/` 全空 = **零漂移** |
| TB（步骤一冻结 → 现沙箱） | `15f6b42a…`（artifacts/pathA_step1_tb_20261014 封）→ `f55a7c92…` |
| TB 增量 | 5 块，全部步骤二仪表：+51 声明块（:2260-2310）、+3 stage-BEGIN 预置触发（:2480-2482）、+75 门控块（:2523-2597）、±声明前移（旧 :2566-2586 21 行声明→新 :2695-2696 仅留初值，声明逐字迁往顶部块，VCS 先声明后引用约束）、+163 注入器/trace（:3854-4016）。另：门控存在性分支修复见 artifacts/pathA_s2_gatefix_20261015（同日先前轮） |
| 生产 RTL / 编译器 | 本轮零改动 |

## B. force 层级与信号驱动关系（全部行号为沙箱现树，与冻结原树逐字节一致）

force 语句：TB :3950-3957 `force dut.de_stb_bready[k]=1'b0` / `dut.de_ldb_rready[k]`。

链路（以 k=3 为例）：

```
SMC_ALL(k=3) stb_top.u_stb → DW_axi_gm(.bready) → DW_axi_gm_core  ← 驱动源
  → SMC_ALL.sv:176 output stb_bready
  → aiacc_top_f.v:2139 .stb_bready(de_stb_bready[gi_smc])   ← TB force 的网
  → aiacc_top_f.v:955  assign us6_bready = de_stb_bready[3]
  → atcbmc300 us6 端口（BMC 侧采样点）                          ← 唯一被 force 影响的消费者
```

**核心发现（DW_axi_gm_core.v，STB/LDB 两 master 共用同一文件）**：

- `bready_gc = gready`（:455）、`rready_gc = gready`（:462）；
- `gready = (~respb_full | (respb_full & mready))`（:508）；
- 响应/数据入内部 respb FIFO 的判据 = `push = gvalid & gready`（:467），
  其中 gvalid=bvalid/rvalid（**输入**）；
- `assign bready/rready = gready_gc`（:510/:511）为纯输出门；
- **全文件无任何处回读 bready/rready 输出网**（:38/:111/:143/:455/:511 为全部引用）。

⇒ **核的接收节奏由内部 gready 决定，force 外部网线不改变核的接收行为**。
STB 侧 stb_top.v:222 `.mready(1'b1)` ⇒ gready≡1（永接收）；
LDB 侧 ldb_top.v:168 `.o_mready(w_gm_mready)` 由控制 FSM 驱动 ⇒ respb 未满时
gready=1（本例载数拍数 ≪ FIFO 深度，恒成立）。

## C. 握手日志核对（实测）

**用例②**（stb_b force k=3，300 拍，stage_2 BEGIN 预置 @3582，release @3882）：

- force 生效：3710–3880 共 18 个采样点 `de_stb_bready[3]=0 ∧ us6_bready=0`
  （力传到 BMC us6 端口输入）；同窗 `bmc_ds5_bready=1, bmc_ds5_bvalid=0`；
- **ch3 B 握手 @3743，与正向后门①逐拍相同**（force 窗完整覆盖 3743）；
- 门控 release-ready @3780 waited=0、GREEN——与①一致；
- pa_bresp / pa_stb_rd / pa_ur_wr 三 trace 与①逐比特一致（diff 全空）。

⇒ 对门控可观测面与 DUT 数据面，本注入**惰性**。机制：(i) 门控 bresp 计数 Tap 在
BMC 下游边界 `bmc_ds{2+k}_bvalid&bready`（TB :3385-3399），与被 force 的 SMC 侧
线之间隔着 BMC 内部缓冲；(ii) SMC 核按内部 gready 节奏消费 B（§B），
存储完成不等线握手。
未测空缺：stall 期间 us6_bvalid 是否如约在 us6 侧挂起（采样未含；不影响
「惰性」结论——结论由 (i)+(ii) 与逐比特一致性闭环）。

**用例③b**（ldb_r force k=3，500 拍 @stage_0）：smc_3 UR 比对 FAIL
（pass=7 fail=1）；受损 bank 出现**位置错置但数值貌似合法**的 fp32
（应为零的 bank3/bank7 落入 40580000/404d5555/4042aaab/40380000 等）。
机制（RTL 级闭合）：force 线=0 ⇒ BMC 按 AXI 规则对该拍持 rvalid ⇒
核以 gready=1 每拍重复 push 同一数据拍入 respb ⇒ 载数据流混入重复拍。
**与②同一结构位点：核不采样自身 ready 输出网，force 即分裂视图。**
性质界定：这是「在该网施加 force」的必然后果，非合法运行下的 DUT 缺陷
（无 force 时线 rready==内部 gready，逐拍一一对应）；按既有纪律不外推硬件。

附注（边界收窄）：②的 B 重复 push 在 respb 内腐化但本配置单拍存储/SMC ⇒
无可观测后果；**「惰性」结论限本配置**，多笔在飞存储下该注入可能误导——
故该点是「判废」而非「无害可用」。

## D. 与 us3bp 合法注入证据（2026-10-08）的差异解释

- us3bp 实测 inject1172 < risen1192 < release1321 ≤ drained1321（delay=129）：
  四个标志**全部在被 force 的那根线上**（`dut.de_stb_bvalid[0] &&
  dut.de_stb_bready[0]`，TB :3541-3557）。它证明的是：BMC us 端口在
  bready=0 期间合规持 bvalid、释放后线握手干净排空、运行保持 GREEN。
- 该块自带限制注记（TB :3500-3503，2026-10-08 落笔）：
  **「S4 barrier bresp counter taps the ds2 boundary and therefore does NOT
  observe this stall」**——即当时已明确：ds 侧观测点看不到这种 stall。
- 步骤二门控的可观测面恰在 ds 侧（bmc_ds{2+k}）。步骤二注入器注释
  （:3859-3862）把 errata-8 的「线级合法性」过度推广为「对 ds 侧观测面有效」，
  是本步骤的设计错误（我的），今以本审计更正。
- 结论：**两线证据不矛盾**——同一 force、不同观测者拓扑；10-08 的限制注记
  恰好预言了本轮②的逐比特一致。同时须如实收窄 us3bp 的历史口径：
  其「delay→recover→GREEN」中delay=129 是**线握手排空**的延迟，
  非 DUT 完成/数据面延迟（与今机制一致）；该口径当时已注记，现再次确认。

## E. 候选注入点评估（仅 STORE 侧；按裁定不扩 LOAD；三确认全部落实前不选定）

| 候选 | ①两端视图一致 | ②阻塞期 VALID+载荷稳定 | ③释放后无丢失/重复 | 判定 |
|---|---|---|---|---|
| S0 `dut.de_stb_bready[k]`（现状） | **不成立**（核不采样该网，§B RTL 实证） | —（①已否） | —（B 重复入 FIFO，本配置无后果但结构性存在） | **判废** |
| S1 `bmc_ds{2+k}_bready`（BMC 输出→桥输入边界线） | 桥侧采样该线（atcrambrg300.v:344/:346 持 BVALID 入响应 FIFO，ds2 上 BPDBG P1-P3 实证）；**BMC 侧 = 自身输出被 ds_ctrl 采样（atcbmc300.v:21552 → atcbmc300_ds_wdata_bresp_ctrl），须在本配置探针实证**——历史风险：2026-10-08 死锁不动点「矩阵 ds2 上送否决+边界 bready 永不复位」（round6 注错套件另一配置） | 桥 FIFO 持 bvalid 机制已证（ds2）；ds5 需逐通道复核 | 须实证：pa_bresp_cnt[k] 恰=1、drain 恰一次、RESPACCT 静默、比对 GREEN | **唯一候选，但按裁定不得直接恢复；须经探针跑落实三确认后由用户再裁定** |
| S2 TB 存储模型内延迟（激励层、不 force） | 结构性满足（模型是 TB 自己的） | 同左 | 同左 | 仅列示：改动 TB 环境行为模型，未推荐 |

**S1 探针设计（如获批准才跑，1-2 跑+分析，当日时间盒）**：
plusarg 新增 `+PA_DLY_KIND=ds_b`（默认关），force `bmc_ds5_bready=0`（k=3，
300 拍 stage_2 BEGIN 预置），全程纯观测探针：
① 逐拍同采 {bmc_ds5_bvalid/bready（线）, 桥侧 GEN_SRAM[3].…u_sram_brg 响应
FIFO 状态, 矩阵 ds5_ctrl pending/slv_bvalid/bresp_cnt_empty}——每一次线握手
须恰对应桥侧一次弹出与矩阵侧一次前送，无单侧计数；
② stall 期间 bmc_ds5_bvalid 恒 1 且 bid/bresp 稳定（逐拍比对），释放前
de_stb_bvalid[3] 升起并自持；
③ 释放后 pa_bresp_cnt[3] 恰==1、us6 侧 drain 握手恰一次、常驻 RESPACCT
块静默、本 case 比对 GREEN、其余通道 trace 与①仅时标差。
任一不满足 ⇒ 该点判废，步骤二转入裁定书的兜底口径。

## F. 状态与结论口径（2026-10-15 用户裁定，现行有效）

- **步骤二按兜底口径收口**：保留正常运行（① GREEN）、全目标监测（步骤一探针）
  与已成立的门控证据（④⑤ 负控有牙，修复轮见
  artifacts/pathA_s2_gatefix_20261015）。
- **「非对称延迟下的恢复与保持力」尚未验证**——明确登记，不升级相应正式验收
  结论。②③ 注入判废（S0），③b 之 RED 系注入致损非产品缺陷（两运行目录已置
  READONLY_NOTE.md 防误读）；TB 注入器注释已就地 errata。
- **S1（bmc_ds{2+k}_bready）留作独立诊断候选**：待「握手两端一致性 + 注入
  合法性」完成**论证**后再决定是否施行，不阻塞当前编译器主线；本文 §E 的
  探针设计仅作届时参考，未获施行授权。
- 本文全部结论限于本配置 VCS 仿真 + RTL 读码，不外推板端硬件。

## G. 沙箱资产登记（2026-10-15 收口，补 §A 指纹链末段）

| 项 | 值 |
|---|---|
| 沙箱路径 | `/home/juno1023/u280_sim_v422_pA_20261014/`（基线原树 `~/u280_sim_v422` 不动） |
| TB 指纹链 | `3324a33b…f7c5`（基线，F6 冻结在册）→ 步骤一 `15f6b42a…` → 门控修复 `f55a7c92…` → 勘误注释后 **`69f574bcba398e60ec8936518ac8780d3f2dc97fe9fa919f068b5c3c0aa41584`**（210673 字节，末次仅注释） |
| RTL 漂移 | `data_engine/` 对原树 `diff -rq` 全空 = 零漂移（收口时点复核） |
| 补丁件 | `verification_v2/artifacts/pathA_s2_closure_20261015/tb_patch_vs_mainline_3324a33b.diff`（631 行 unified diff，基线→沙箱全量） |
| 登记归档 | `verification_v2/artifacts/pathA_s2_closure_20261015/`（README + 补丁 + SHA256SUMS 已封） |

引用文档勘误落点汇总：ADR-060 errata 11 / F6-lsu-completion-contract-design.md
勘误节 / task2-closure-ds2-bresp-injection-monitoring.md 勘误节 /
8smc_pathA_full_target_monitoring_20261014.md §3 勘误 + §5 覆盖矩阵 /
progress_log 2026-10-08、2026-10-10 两条历史条目内联勘误 + 2026-10-15 条 /
tests/test_m3lite_gate3.py 模块头与 us3bp 绿基线 docstring 注释勘误。
冻结归档（m3lite_gate3_us3bp_20261008 等）一律未动。
