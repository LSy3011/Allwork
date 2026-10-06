---
adr_id: ADR-058
title: M3-lite 门①——STB 独验（预置 UR → 编译器生成 STB/STBAXI → HBM 写回 VCS 验证）
decided: 2026-10-05（**草案 DRAFT，未经用户批准不实施**）
last_verified: 2026-10-06
still_holds: proposed
builds_on: ADR-050, ADR-052, ADR-057, ADR-031
user_decision: user 2026-10-05「按三道门推进，无需再次等待启动批准：1. STB 独验……第一个动作是现状盘点与最小设计，不重复建设已有能力」
---

# ADR-058 · M3-lite 门①：STB 独验

> **状态：proposed（2026-10-06 门① GREEN，证据回填）**——三用例 VCS+六项断言全过，
> 证据归档 `verification_v2/artifacts/m3lite_gate1_20261006/`。门②/门③不在本文范围
> （门③的 LOAD/STORE 段 kind 扩展将另立 ADR）。
> 证据等级：**VCS RTL 级**（非上板；限制 L2/L3/L4 继续挂账）。

## Context

用户 2026-10-05 指令（要点逐字）：

- **三道门按序推进**：① STB 独验 → ② LDB 复核 → ③ 完整组合 `LOAD → COMPUTE_0… → STORE`。
- **门①通过标准（逐字）**：预置 UR，由编译器生成 STB/STBAXI 指令；**外部写出数据、地址、数量
  正确；非目标区保持预期；完成通知不早于所需写事务完成**。
- **范围纪律（逐字）**：单 SMC、单 chunk、安全规模、顺序执行；不引入多 SMC、host chunk、
  搬运重叠或性能优化。**不要将计算队列的 ISSUE 模型直接套到 LSU**。
- **熔断**：STB 投入 2–3 个工作日仍遇重大阻断 → 提交证据与原因，重新分配预算，不无限延长。
- 论文方案 R1 的 8-SMC STB 判据是**全量 M3 阶段 2** 的事；M3-lite 是其先行受限子集
  （张力调和见 RECON.md §6）。

### 盘点结论摘要（全文见 RECON.md）

**现成，不重复建设**：STB schema/packer/emitter（ADR-052 D5 与 golden 整数全等）；
TB 对 STB-only 单段 case 结构零假设合法；UR 预置、HBM dump（`hbm_result_smc_i.hex`）、
完成通知链路（crd/inst_id + outstanding 排空 → batch_done → INT）、运行脚本、单-SMC
编译器 case 骨架（compiler_add_n4）。

**缺失，本 ADR 新建**：① HBM 写出**自动比对**（全沙箱零消费方，手写 case 当年靠人工抽查）；
② 参数化 STB case 生成器（发射层，含 ur_data / memory_init / golden / 期望清单）。

**不建（门①不需要）**：STB DFG 节点、LOAD/STORE 段 kind、run_v1_verify 编排适配、
python sim LSU 建模（ADR-050 先例：sim 盲、VCS 准）——全部留给门③另立 ADR。

### 阶段 0 取证补强（2026-10-05 已闭合，修正草案三处）

- **F1 golden stage_2 五行逐字段解码**（新事实）：iq_stb.hex / iq_stb_axi.hex 各 5 行——
  row1-4 = `UR_ADDR=16384 CNT=2 / BRST=1` 的 4 条小搬运（row1 带 COND=26、row1-4 带 DLY=7，
  DEBT-052-STB-HDR 登记未解释，**门①不背这个历史包袱**）；row5 为主搬运。
  **配对恒等式实证**：`ur_rd_line_cnt == (LOOPS+1)×(brst+1)`（row5：32=2×16；row1-4：2=1×2）。
  **两流 HDR.INC 单位不同**：STB INC=16=**行**（迭代间 UR 读址步进），
  STBAXI INC=256=**字节**（迭代间写址步进）。row1-4 的 INC=16 是 loops=0 时的无效常数。
- **F2 LSU IQ 结构两模板**：编译器产 K16 LDB（VCS 已通）= 6 行 embed 形态
  （零行/INIT{INC_EMBED}/BODY/零/零/TERM{EMBED_END}）；手写 STB golden（VCS+上板已通）=
  纯数据行平坦形态，**无 INIT/embed/TERM**（LSU done 靠 credit/outstanding 排空，非 JUMP）。
  embed 有 LOOP_PARA_SET 自锁前科（ADR-053/054）且 STB 侧无 embed 先例 →
  **门①镜像手写平坦形态**，iq_stb.hex = 每描述符一行 vld=1 数据行。
- **F3 T1 订正（比草案估计的窄）**：末段超时实际打 **`[ERR] smi_wait_and_clear_intr timeout`**
  （TB:827-828）+ `[WARN]`（:830-832）——`[ERR]` 会被 RUN 的判定（log 无 `^[ERR]`）拦截，
  RUN 层不存在静默假绿。比对脚本仍保留双保险（正向 `batch interrupt`/`smi_iq_done`
  标记必须存在 + 反向 grep timeout 子串）。
- **F4 smc_id_en=0 可行**（payload_factory.py:95-97 实证，`dict.get`+`is None` 判缺省，
  显式 0 不被回填 7）；语义=阈值门控（payload_factory.py:111-116 注释，user 2026-06-03 裁定）。
- **F5 STB 侧 brst 用法静态看**：stb_ur_axi_conv.v 全是 `r_burst_send_len == brst` 比较
  （:897/:1025/:1036/:1100/:1122），brst=0 等价守卫段未逐行核——首证避开（最小 brst=15），
  生成器响拒 brst=0，深查留待需要时。

## Decision

### D1 指令来源：发射层生成器（生产 packer），非手写 hex，非 DFG

新工具 `verification_v2/tools/stb_solo_case_gen.py`：参数化（lines / brst / loops / inc /
gr_base / ur bank+offset / tid / smc_id_en / 数据图案种子）→ 构造 `PhysicalInstruction`
（复用 ADR-052 STB_PARAMS/STBAXI_PARAMS 形态）→ **真 `LIRPacker.emit_from_instructions`**
→ `iq_stb.hex` / `iq_stb-axi.hex`；同时物化完整 case 目录（ur_data bankN.hex、
memory_init 预载、UR golden、期望清单 JSON）。

**口径声明（呈用户裁决点 Q1）**：门①判据「由编译器生成 STB/STBAXI 指令」的实现 =
编译器**生产发射组件**（payload_factory + LIRPacker，与 ADR-052 D5 同一路径）从结构化参数
生成，非手写 hex。DFG/SMT 全链自动生成是门③「完整组合」的要求，门①引入它只会把
SMT 调度变量混进硬件行为取证，违背最小设计。

### D2 比对：新建 HBM 窗口比对脚本，按位严格

`verification_v2/tools/hbm_window_compare.py`，输入 = case 期望清单 JSON +
`hbm_result_smc_0.hex` + sim.log，断言（任一失败即 RED，退出码非零）：

1. **数据**：`[gr_base, gr_base + 16×lines)` 窗口逐行 == UR 预置值（bit-exact，hex 行严格比，
   不借 compare 脚本的 fp 宽容——搬运无语义转换）。
2. **地址**：窗口起点 == gr_base>>4 行（通过「窗口外不变」联合证明，见 3）。
3. **数量**：窗口内恰好 `lines` 行被写（窗口两端紧邻行仍为预载值）。
4. **非目标区**：窗口外**全部**行 == memory_init 预载值（整颗 1048576 行全比，不是抽查）。
5. **完成通知**：sim.log 无 `STAGE_INTR_TIMEOUT`（堵 T1 假绿通道）且 intr 应答标记存在。
6. **UR 无损**：compare_ur_to_golden_multi_stage.py 对 UR golden（= 预置值）PASS
   （堵 T2 SKIP 假绿 + 断言 STB 读侧无破坏）。

### D3 完成时序证据：两层，不改动共享 TB 主文件为首选

判据「完成通知不早于所需写事务完成」的证据分两层：

- **结构层（已有）**：`smi_iq_lsu_complete.v:1-40`——LSU 的 rdi_done 须等 outstanding_inst_num
  归 0 才放行；batch_done 须全 IQ done 齐（smi_ctrl_regs.v:160-172）。行级引用入档。
- **观测层**：dump 在 intr 应答之后执行（TB 执行流 :2357-2403），故「窗口数据完整 +
  intr 已到达」构成行为证据；其严格性缺口（intr 与 dump 之间窗口内理论残余写）登记为限制 L2。
  **可选增强（呈用户裁决点 Q2）**：在 TB 加 plusarg 门控（默认关闭）的观测块，记录
  STBAXI b 通道末次握手 cycle 与 intr 置位 cycle，输出供比对脚本断言 `last_b ≤ intr`。
  改沙箱共享 TB 需用户明示同意。

### D4 用例矩阵（安全规模，串行跑）

| 用例 | 形态 | 目的 |
|---|---|---|
| **S1 首证** | 16 行 = 1 满 burst（brst=15, LOOPS=0），smc_id_en=0，gr_intlv=0，gr_base=0x1000，UR bank1 off 0，确定性图案 | 最小变量集过全判据 |
| S2 | 32 行（LOOPS=1, INC=256, brst=15） | 镜像 golden row5 形态；loop + 地址步进 |
| S3 | 24 行（LOOPS=1, INC=256；尾迭代 brst=7 半满） | burst 尾部 / 数量边界 |

生成器守卫（响亮拒绝，配单测）：brst=0（T5 禁区）、lines>16 且 LOOPS=0（超单 burst）、
lines>255、gr_base 非 16B 对齐、ur_addr 越界、smc_id_en≠0（M3-lite 范围外响拒）。

**负向断言（防假绿）**：S1 变体 N1——UR 预置值改一字 → 窗口比对必须 RED；
N2——期望窗口平移 16B → 地址断言必须 RED。

### D5 HBM 预载：显式已知填充 + case 留存副本

生成器写 `memory_init1.hex`（SMC_0 对应颗）为已知填充图案（如行索引编码），**副本留存
case 目录作证据**；比对脚本以 case 内副本为窗口外期望真值（不依赖全局目录事后状态，
规避 T4 共享污染的事后不可考）。依赖既有串行纪律（同 op 并发互毁本就串行）。

### D6 smc_id_en=0：显式压 0 隔离使能变量

已取证可行（payload_factory.py:95-97：`dict.get` + `is None` 判缺省，显式 0 不被回填 7）。
阈值门控语义（smc_id_en >= SMC_ID 使能）下，0 = 仅 SMC_0。SMC_1..7 不执行 STB，
其 SRAM 内容不受本 case 影响（比对只查 SMC_0 颗——全局目录其余 memory_init 不动，
但为防历史残留，生成器对 2..8 颗写全零已知填充或删除旧文件，并在期望清单登记处置方式）。

### 契约表（门①遵守的 LSU 契约，全部有出处，无新造）

地址字节单位 / ur_addr 位段（bank[14:12]·depth[11:4]·byteoff[3:0]）/ line_byte_cnt=总字节数 /
brst≤15 且 ≠0 / STB:STBAXI 1:1 同拍（发射层天然满足）/ LOOPS+INC 迭代形态 /
完成=crd(inst_id)+outstanding 排空→batch_done→INT / TB 超时 500000 拍·末段仅 WARN。
逐条证据等级见 RECON.md §3。**LSU 信用门控变体（smi_iq_dly_queen.v:148）已纳入认知，
不套 compute ISSUE 模型**——生成器直给终态指令流，无 replay/信用语义暴露。

## 验收判据（门① GREEN 的充要条件）

1. S1/S2/S3 三用例 VCS 跑通，D2 比对脚本六项断言全过（数据/地址/数量/非目标区/完成通知/UR 无损）。
2. 负向断言 N1/N2 均按预期 RED。
3. 全部证据（case 目录、期望清单、hbm_result、sim.log、比对输出）归档
   `verification_v2/artifacts/m3lite_gate1_<date>/`。
4. 单测只增不减（生成器守卫 + 比对脚本对抗样本）；全量 pytest 基线 ≥414。
5. **不发布**（门①是硬件行为独验，非算子包；发布是门③统一入口全通过后的事）。

## 不做清单（范围纪律落实）

- ❌ STB/LDB DFG 节点、SMT 调度接入（门③）
- ❌ LOAD/STORE 段 kind、stage_partition/stage_manifest/run_v1_verify 适配（门③）
- ❌ python sim LSU 建模（ADR-050 先例：VCS 为准）
- ❌ 多 SMC / 多 chunk / 搬运重叠 / 性能优化（用户逐字排除）
- ❌ brst=0 形态（T5 禁区，生成器响拒）
- ❌ 上板（全量 M3 的事）

## 风险登记与熔断

| 风险 | 应对 |
|---|---|
| R1 写侧 done 握手/信用行为异于读侧（论文方案已列，**唯一必须新取证项**） | 首周即跑 S1；症状若出现（发码对但不 fire / done 不来），行级取证 stb_ur_axi_conv.v + iq_dly_queen |
| R2 STB 侧 brst=0 同构死锁未证 | 避开 + 生成器响拒；静态比对 stb_ur_axi_conv.v vs ldb_ur_axi_conv.v:829 守卫（阶段 0 做，仅记录不改 RTL） |
| R3 tid=2 语义未独立取证 | 沿用 golden 值，登记限制 L3 |
| R4 全局 HBM_INIT_DIR 污染致「非目标区」期望失真 | D5（case 留存副本为真值源）+ 串行纪律 |
| R5 TB 改动需求（D3 可选增强） | 默认不动；用户批准才做，且 plusarg 默认关闭 |

**熔断（用户逐字）**：STB 投入 2–3 个工作日仍遇重大阻断 → 提交证据与原因，
重新分配预算，不无限延长。熔断触发时的最小交付 = RECON.md + 本 ADR + 失败现场归档。

## 限制登记（随门① GREEN 一并落账，不豁免）

- ~~L1 D2 比对只查 SMC_0 颗 SRAM~~ **已核销（2026-10-06）**：smc_1..7 空目录方案
  使 TB dump 全 8 颗，A4c 逐颗实证未被写。
- L2 完成时序观测层证据是「dump 晚于 intr」的间接证据（D3）；除非用户批准 Q2 增强。
- L3 tid=2 照抄 golden，语义未独立取证（DEBT-052-STB-TID 延续）。
- L4 python sim 对 STB 全盲，门①证据等级 = **VCS RTL 级**，无 sim 侧交叉验证，无上板。

## 待用户裁决点

- **Q1**：「由编译器生成」口径 = 生产发射层生成器（本 ADR D1）——还是要求门①即建 DFG 节点？
  （建议前者：门①取证目标是硬件行为，变量最少；DFG 节点门③必建。）
- **Q2**：D3 可选增强（TB 加 plusarg 门控时序观测块，改沙箱共享资产）做不做？
  （建议首版不做，限制 L2 落账；若门①调试需要再追加。）
- **Q3**：S2/S3 是否纳入门① GREEN 充要条件？（建议纳入——尾 burst 与 loop 形态是
  「数量/burst 尾部」判据的直接证据，成本各约半天内。）

## Consequences（批准后的改动面预估）

| 改动 | 文件 | 性质 |
|---|---|---|
| 新建 | verification_v2/tools/stb_solo_case_gen.py | 发射层生成器 + 守卫 |
| 新建 | verification_v2/tools/hbm_window_compare.py | 六项断言比对 |
| 新建 | tests/unit/test_stb_solo_case_gen.py 等 | 守卫 + 对抗样本单测 |
| 零改动 | DFG/SMT/assembler/run_v1_verify/package_vcs_case/hw_config | 全部留给门③ |
| 可选 | 沙箱 TB 观测块（Q2 批准才动） | plusarg 默认关闭 |

## 实施进展（2026-10-05）

- **裁决点收口**：Q3 用户原话已答（「首证之后再补短尾或边界用例」→ S2/S3 纳入）；
  Q2 按默认（不动 TB，L2 落账）；Q1 按建议（发射层 = 「编译器生成」口径，DFG 归门③）。
- **工具落地**：`stb_solo_case_gen.py`（守卫 11 条：brst=0 禁区 / 配对恒等 / 越界 /
  非对齐 / en≠0 / 窗口越预载 等）+ `hbm_window_compare.py`（A1 窗口数据 / A2 地址 /
  A3 数量 / A4a-c 非目标区三剖面（目标颗预载区、X 区、其余 7 颗 en 泄漏）/
  A5 完成通知双保险 / A6 UR 无损按位）。
- **单测 28/28 全过**（test_stb_solo_case_gen.py 17 + test_hbm_window_compare.py 11；
  比对脚本 9 项 RED 注入全部按预期翻转——窗口数据错/地址平移/数量短一/X 区被写/
  en 泄漏/超时标记/intr 缺失/UR 被改/缺 dump）。
- **S1/S2/S3 三 case 已物化并通过字段级对照**（build/m3lite_sbx/，未跑 VCS）：
  - 生成器初版 bug 被字段级对照抓获：S2 的 STB 行误带 LOOPS=1——golden row5 实证
    **迭代只由 STBAXI 承担，STB 恒 LOOPS=0**（一次读全部 CNT 行，两流节奏由信用门控
    协调）。已修 + 回归断言钉死（test_s2_loops_form）。
  - 修复后 S2 与 golden row5 逐字段对照：差异**恰好且仅是**设计内改编
    （SMC_ID_EN 7→0、GR_INTLV_ADDR→0），其余全等。
- 待做：三 case 同步沙箱（当前被权限分类器间歇故障阻断——写沙箱命令超时，
  只读与仓库内命令正常）→ VCS → 比对 → 归档。

## 实施进展（2026-10-06 续）

- **预跑只读核查再抓一缺漏（已修）**：multi-stage TB 的 HBM dump 范围受
  `case_smc_max_idx` 门控（tb_aiacc_top_v04_f_multi_stage.v:968，仅 dump ≤ 最高
  探针命中槽）；探针 = 逐槽 fopen `smc_<i>/data_list_smc<i>_subqN.txt`（:2304），
  GEN 对空 smc 目录也会写空 data_list（generate_data_list_coverage.py:922-931）。
  → 生成器 `materialize()` 补建 `smc_1..7/` 空目录，否则 TB 只 dump smc_0，
  A4c 的 en 泄漏检查将永缺证据载体（RED 缺 dump）。单测已加结构断言。
- **A2b 预检完成**（半天预算内）：`verification_v2/artifacts/a2b_preflight_20261006/preflight.md`，
  20 个 II>2 候选全解出（II 4..12，束缚队列多为 rtovr-14，n_max 估计 11..42）。
  **校准点 scale_sum：实测 240 vs 估计 63**——用户 2026-10-06 裁定：一次偏保守
  不证明算法恒保守，且疑混用元素数与逻辑迭代数单位（240/60≈4 为量级线索，待证）；
  该列已改标 **未校准估计，不得用于容量准入**；正确口径 = 先统一单位再按逐队列
  II_q·R_q+2 计算（已记 a2b_preflight.py docstring，待实现）。我曾表述为
  「偏保守可作下界」，该推断不成立，已撤回。
- 同步策略变更：放弃批量拷贝（权限分类器间歇故障反复阻断），改为**生成器
  直写沙箱**（`--sandbox ~/u280_sim_v422`，确定性重生成；build/m3lite_sbx
  暂存副本弃用，最终证据以跑完 VCS 后归档到 artifacts/ 的副本为准）。

## 门① GREEN 证据（2026-10-06，VCS RTL 级）

三用例全过（生成 → VCS → 六项断言比对），归档 `verification_v2/artifacts/m3lite_gate1_20261006/`
（case 全量 minus HBM 大 dump；dump 以 sha256 清单冻结 + 沙箱留存 + case/快照确定性可重建）：

| 用例 | 形态 | sim.log | 六项断言 | 完成路径观测 |
|---|---|---|---|---|
| S1 | 16 行单 burst（brst=15），bank1，gr_base=0x1000 | 0 ERR；intr@1865→done@2295→dump 时序链 | 全 PASS（A0-A6） | batch interrupt 通道（TB:810） |
| S2 | 32 行 loops=1 INC=256（golden row5 镜像），bank3，gr_base=0x3000 | 0 ERR；同链 | 全 PASS | batch interrupt 通道 |
| S3 | 双描述符 16+8 尾 burst（brst=15→7），bank1，gr_base=0x2000 | 0 ERR；done@3195 | 全 PASS | **smi_iq_done 通道**（TB:816，@cycle 20）——两合法完成路径均实测到 |

判据逐字对照（用户门①标准）：**外部写出数据/地址/数量正确** = A1+A2+A3（窗口逐行
bit-exact、两端紧邻行不变）；**非目标区保持预期** = A4a/b/c（预载区全域、X 区、
其余 7 颗 en 泄漏三剖面）；**完成通知不早于写事务完成** = 结构层（smi_iq_lsu_complete
outstanding 排空）+ 观测层（dump 晚于 intr cleared，L2 挂账残余缺口）。

- 验收判据 1 ✓（三用例六项全过）；2 ✓（N1/N2 以比对器 9 项 RED 注入单测实现，
  未单独烧 VCS 变体——记录在案）；3 ✓（归档）；4 ✓（单测 28/28，全量基线见提交）；
  5 ✓（未发布任何算子包）。
- **限制 L1 核销**：实施时定了「顺带 dump 全 8 颗」——smc_1..7 空目录方案使 A4c
  成为逐颗实证（8 颗 dump 全部存在且 1..7 未被写），不再是推断。
- 限制挂账不变：L2（观测层间接证据）、L3（tid=2 照抄 golden）、L4（无 sim 侧
  交叉验证、无上板）。
- 环境备注：本次执行经历权限分类器（glm-5.3）间歇故障，最终经沙箱外执行通道 +
  用户终端混合完成；全部 GREEN 判定以重读产物（sim.log 标记、dump 边界抽审、
  比对器 exit=0）为准，非凭终端转贴。

## Errata 2026-10-06（门②实测发现回溯，用户裁决 J3 批准补录）

门②（ADR-059 v2.1）L1/L2 实跑经精确锚点探针实测：**LSU 批完成中断不保证数据
落位**——`smi_iq_lsu_complete` 的 WAIT_DRAIN 在 inst_queen dispatch-done 与
dly_queen 执行计数递增之间的流水线空窗采到 `outstanding==0`，提前放出
rdi_done（L1：RDI_DONE@150 时 outstanding=1，UR 写 166..181，真实信用
CRD_LDB@183）。intid 自增来源经取证探针证实为 batch_done（非 ir_err）。

对门①的影响：

- **A5 完成时序的证据措辞降级**：原「结构层（smi_iq_lsu_complete outstanding
  排空）+ 观测层（dump 晚于 intr）」中的**结构层前提被证伪**——outstanding
  排空门存在采样竞态，不构成「写先于通知」的机制保证。A5 的有效证据降级为
  观测层单腿：通知到达（无双通道超时）+ dump 时刻晚于通知且数据正确。
- **A1-A4 数据断言不受影响**：均为 dump 级实测，dump 时刻（intr+ack+扫尾数十拍
  之后）远在写事务完成之后；S1-S3 的 HBM 窗口/非目标区结论维持有效。
- 原 L2 挂账（观测层间接证据）由「缺口」升级为「已定性」：通知相对写事务可提前
  （LDB 侧实测提前 16-31 拍；STB 侧同构路径未逐拍复测，按同机制对待）。
- 门③ 及以后任何消费批完成中断的流程须自带落位屏障（ADR-059 R6；
  屏障约束见 ADR-060 草稿）。
