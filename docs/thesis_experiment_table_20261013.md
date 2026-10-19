# 论文实验表：编译器产物验证结果统一对比（数据截止 2026-10-13；§7 增补 2026-10-18）

> 用途：毕业论文实验章节的统一对比表底稿。每个数字都带锚（run ID / release /
> 归档 sha256），口径分级标签【实测】【源码】【归档】【推断】【未校准】不混称；
> 四档覆盖纪律（可调度/可发射/VCS/上板）逐行适用。
> 权威细节永远在被指向的 ADR / 步⑤收口文档 / 归档里，本表是索引层不复制数据。

## 1. 统一对比主表（V1 配置-only 准入链四算子 + 板端首例）

全部 VCS 行为【实测】：TB sha256 `3324a33b…`（RESPACCT 常驻版），RTL 源树
4.22（板端 CSR 期望 `0x00042200`）；统一 LCS 链 provenance 见 §6。

| 算子 | 公式（求值序） | II（锚） | 计算槽结构 | 输入流/参数 | 单段 n_max（校准锚） | 超容量分段实例 | 统一 LCS 程序链 | VCS 结果 | 板端结果 |
|---|---|---|---|---|---|---|---|---|---|
| add | `x + other` | **2**（普查 + 3 实跑 compiler_schedule.json） | fpadd 单槽 | 2 流式，无参数 | 【未校准】准入层既有上限 240（非顶格 VCS 校准）；n=256 响亮拒绝（回归锚 `test_g3_add_overcapacity_refuses_loudly`） | 无（未超上限运行） | 未跑 | 4/8/60 全 GREEN+published（5 运行枚举 §6） | 未跑 |
| scale_sum | `alpha * (x + other)`（加后乘驻留标量） | **2** | fpadd 单槽 | 2 流式 + 1 驻留标量 | **240**（rtovr-14 恰 128 行顶格 GREEN；n=244 单段响亮拒绝） | 244/480 两段（ADR-057 验收②正式链 GREEN+published） | n16/60/240 三段、n244 四段（ADR-061 accept2 复跑全 GREEN+published） | 全 GREEN | **n16 上板 `CASE PASS compared_smc=1`（HBM 64B bit-exact，板端档首例，§4）** |
| horner_v3u | `((c0·x + c1)·x + c2)·x + c3`（Horner 6 级链） | **6** | fpadd 3 槽 + fpmul 3 槽（三槽双单元，计算队列多槽首证） | 1 流式 + 4 参数 + 常量 bank | **68**（七队列恰 128 行顶格 GREEN；n=70 响亮早拒） | 72 两段 17+1（尾段写址 17 payload+hex 双证） | n8 三段、n72 四段 GREEN+published | 全 GREEN | 未跑 |
| addcmulu | `x + t1*t2*value`（m1=t1·t2；m2=m1·value；y=x+m2） | **3** | fpmul 双槽单单元 | 3 流式 + 1 驻留标量 | **156**（rtovr-14/vin0 恰 128 行顶格 GREEN；n=158/1028 响亮早拒） | 160 两段：**V1 39+1 与统一链 38+2 两实例均 GREEN**（§3） | n8 三段、n160 四段 GREEN+published | 全 GREEN | 未跑 |

- II 锚点总注：四算子 V1 链实证 II 值集 = **{2,3,6}**；II=1 唯一 VCS 证据 = mul
  （前 V1 证据类，§2 单列）。勘误史见 addcmul 步⑤ §2 表下注与 horner 步⑤ §6 E1。
- 公式文本锚：`alpha * (x + other)` 出自 `verification_v2/ops/scale_sum.yaml:8`
  ——r1–r3 上板包 README/PACKAGE_INFO 曾误写 `alpha*x + other`（纯文档错误，
  执行链与 golden 未受影响；勘误已登记 artifacts/onboard_scale_sum_n16_20261013/README.md）。
- 求值序冻结：四算子逐节点 fp32 求值序已入注册表 v3 `FORMULA_SEMANTICS_*`
  （records_sha256 `500c539e…`）——「代数等价」不足为凭，addcmulu 的
  ((t1·t2)·value) 与遗留 ((value·t1)·t2) 逐位可区分。
- 容量数字纪律：n_max 均为 VCS 顶格校准值（恰 128 行 = `smi_top.v:22
  SMI_IQ_INSTQ_DEPTH` 硬件常数），非构造预测；add 行例外已标【未校准】。
- 放置漂移注记：addcmulu 三个放置实例（A 18 队列/span≤3、B 17 队列 ur-r3 重、
  C 17 队列 ur-r2 span=4）各自全链 GREEN，正确性判据实例不变；
  「发射产物跨进程逐字节确定性」禁全称（步⑤ §2 第二档第 12 条）。

## 2. mul 单列（前 V1 证据类，不混入四算子集合）

| 算子 | 公式 | II | 证据 | 口径 |
|---|---|---|---|---|
| mul | `x * other`（flat 单槽） | **1** | ADR-054 阶段2-M1（2026-09-12）：`multi_inst_stage/compiler_mul/smc_0` COMPARE PASSED，8 bank×256 行 = **2048/2048 bit-exact**，独立 python 复核 mismatch=0 | 先于 V1 配置-only 准入链（未经准入/容量分段/统一 LCS machinery）；「II=1 flat 保留 ISSUE 实证债务」既有口径不变；板端另有手写微码线 llama r2 mul_scalar PASS（非编译器产物，不并账） |

## 3. 分段实例详表（容量墙 128 行/段 = 硬件常数；边界 = f(实际调度, span_q, 墙)）

| 算子 | n | 分段形态 | 链 | 结果 | 锚 |
|---|---|---|---|---|---|
| scale_sum | 244 | 两段 COMPUTE | V1 正式链（ADR-057 验收②） | GREEN+published | releases/scale_sum_adr057_* |
| scale_sum | 480 | 两段 COMPUTE | 同上 | GREEN+published | 同上 |
| scale_sum | 244 | 四段程序（LOAD+2×COMPUTE+STORE） | 统一 LCS（ADR-061 accept2） | GREEN+published | releases/scale_sum_adr061_accept2_n244 |
| horner_v3u | 72 | 17+1 | 统一 LCS | GREEN+published（尾段写址 17 双证） | releases/horner_v3u_hv3u_step4fix_n72_20261012_codex_r2 |
| addcmulu | 160 | **39+1** | V1 链 | GREEN+published | releases/addcmulu_acu_step3_n160_20261012 |
| addcmulu | 160 | **38+2** | 统一 LCS | GREEN+published（放置实例 C，ur-r2 span=4 束缚） | releases/addcmulu_acu_step4_ulcs_n160_20261012 |

钉死句（注册表 v3 `SEGMENTATION_DECISION_RULE`）：冻结的是**决策规则与实例
证据**——同一 n=160 存在 39+1 与 38+2 两种合法分段，均经全链验证；
**禁称「n=160 必须切成某一种形状」**；容量墙 128 行/段是硬件常数，单段迭代
容量为实例相关区间（addcmulu 实测 38–39）。

## 4. 板端证据（上板档第 1 例，2026-10-13）

- scale_sum n=16 统一 LCS，zwz U280（4.22 bitstream）：版本门
  `0x00042200`+IQP1 过 → 软复位 PASS → 8 路预载 → 三段 DONE（intid delta
  全 1）→ HBM 输出窗 64B 与 golden（锚 = VCS 侧独立 fp32 参考）逐行
  bit-exact → **`CASE PASS compared_smc=1`**。
- 判据 = 数据面 bit-exact，非 DONE 时序；host ms 轮询对 ns 级完成竞态有
  5–6 数量级遮蔽（解释流程顺畅，不构成竞态无害证明；F6 措辞裁定）。
- 范围：本产物本规模本三段形态、单 SMC（smc_0；smc_1..7 按设计 SKIP，
  镜像 VCS 证据范围）。不外推任意规模/多 SMC/他算子/任意公式；
  host 完成协议闭环仍 F6 开放问题。
- 归档：`artifacts/onboard_scale_sum_n16_20261013/`（board_log.txt 逐字实录
  sha256 `4c55563c…` + README + SHA256SUMS）；包
  `~/u280_fpga_packages/…_r3.tar.gz`（sha256 `22bfad6a…`，板证 runner
  `d8e6be6d…`）。

## 5. 可宣称 / 不外推（口径转引，权威在两份步⑤收口文档 §2 第三档）

**可宣称**：在 ADR-056 §A 公式子集内，「新公式 = 新 yaml 配置」即可经同一
通用链路（无算子名/张量名特判）完成准入、调度、分段、程序装配与 VCS 验证；
II 实证值集 {2,3,6}（V1 链）；多槽谱系三档（单槽/双槽单单元/三槽双单元）；
≥3 因子乘积结合序分歧可用配置级文本对齐消解（一例实证）；C-H 公式化独立
参考跨案例零改动复用（一例实证）；编译器产物已有 1 例板端实际输出证据
（本表 §4 范围限定内）。

**明确不外推**：任意 II（实测 2/3/6）；任意多槽/多单元组合（实测三档）；
任意参数个数与布局（≤4 参数 + 1 常量 bank）；多 SMC、ragged tail、归约/
矩阵/原地更新；host 自主完成闭环；发射产物跨进程逐字节确定性；分段边界
实例不变性；任意 ≥3 因子公式结合序对齐可行性；上板推广（仅 §4 一例范围）。

## 6. 锚点清单（数据 → 证据位置）

| 数据 | 锚 |
|---|---|
| 四算子覆盖矩阵（管辖版） | docs/addcmul_step5_closure_20261013.md §2（含 II 勘误注）；horner 步⑤ §2/§6 |
| II=2 add 锚 | census3_progress.txt + build/v1_runs/add/{20261010_102442_d036c3, adr057_closure_add_reg, hv3u_regress_add_frozen_20261012_codex} compiler_schedule.json |
| add 运行枚举（全 4/8/60 GREEN+published） | build/v1_runs/add/ 5 运行：20260930_021905_b0a304、20260930_034720_d28bfb、20261010_102442_d036c3、adr057_closure_add_reg、hv3u_regress_add_frozen_20261012_codex |
| add n=256 响亮拒绝回归锚 | tests/…`test_g3_add_overcapacity_refuses_loudly`（ADR-057 :84 修订史） |
| scale_sum n_max=240 / n=244 拒 | ADR-056 步⑤（rtovr-14 恰 128 行）；`verification_v2/artifacts/v1_step5_scale_sum_acceptance_20260930/` |
| scale_sum 244/480 两段 | ADR-057 验收②（销号 e83bc8c） |
| scale_sum 统一 LCS 四规模 | ADR-061 accept + accept2（commit A=7911103/B=0792941；e76592b/3657c59/268be91）；run_report git_head e76592b（dirty=true 按实）、registry v2 973861aa…、manifest 86f48037… |
| horner_v3u 全链 | docs/horner_v3_step{1..5}_20261012；releases/horner_v3u_hv3u_step{2,3,4fix}_* |
| addcmulu 全链 | docs/addcmul_{static,step2,step3,step4,step5}_2026101{2,3}；releases/addcmulu_acu_step{2,3,4}_* |
| 注册表 v3（求值序/分段规则/容量口径冻结） | verification_v2/tools/protocol_registry_v3.yaml（records_sha256 500c539e…）+ docs/registry_v3_argument_20261013.md |
| 板端首例 | artifacts/onboard_scale_sum_n16_20261013/（SHA256SUMS）；包 r3 tarball 22bfad6a… |
| mul 单列证据 | memory compiler-rtovr-dense-vs-sparse-mismatch（ADR-054 阶段2-M1 条） |
| pytest 基线 | 578 passed（7 skipped，11 xfailed，148 subtests）@ 2026-10-13 |
| §7 公平对照（组合程序） | docs/fair_pair_scalar_mul_64w_design_20261018.md；artifacts/fair_pair_smul64w_20261018/（SHA256SUMS 15 项）；commit d59111a |
| §8 公平对照第二规模 | docs/fair_pair_smul255w_precheck_20261018.md；artifacts/fair_pair_smul255w_20261018/（SHA256SUMS 15 项） |

## 7. 公平对照：手写 vs 编译器（组合程序，2026-10-18，commit d59111a）

**回答的问题**：在相同输入字节、相同搬运框架（LOAD/STORE 三段）、相同
RTL/TB 环境下，编译器生成程序能否正确运行，以及额外成本落在哪里。
**口径（用户裁定置顶）**：这是公平对照实验的**组合程序**，不是新增的
公式驱动统一 LCS 能力；不进入 §1 主表（未经准入/容量校准/统一 LCS 链）。

- 任务：`y_i = fp32(α·x_i)`，α=2.5 四 lane 同值（单一标量乘前提写入双方
  输入规格）；64 UR 词/SMC（256 元素）× 8 SMC × fp32 × seed=20261018。
- 框架：手写生成器产 LOAD/STORE/数据/golden，两变体逐字节同一
  （meta/hbm_data/ur_data/golden/stage_0+2 全 hex IDENTICAL 审计）；
  唯一差异面 = stage_1 COMPUTE 指令集 + 其 data_list 条目。
  α 按 64 词**双侧共同整载**——属**保守的共同搬运框架**（证据库无单词
  LOAD 描述符已验证先例），**不声称标量输入搬运已优化**。

**正确性（先决，两链独立全绿）**：COMPARE 逐 SMC UR bank2（256 行含零尾）
**8/8 零 SKIP ×2**；verify_ddr HBM 窗 8/8 ×2；独立 IEEE 参考（先与手写
生成器 golden 逐位互校）8/8 ×2；非目标写检查零改写 ×2；TB 3324a33b 未漂移。

**取数（TB 执行行为层，scoped 事件对照）**：

| 指标 | A 手写 | B 编译器 | Δ |
|---|---|---|---|
| COMPUTE 逻辑指令行（hex 行/13 队列） | 28 | 39 | +11（+39.3%） |
| COMPUTE 指令装载（AXI beats） | 39 | 54 | +15 beats |
| 装载成本（13 拍/beat） | 507 拍 | 702 拍 | **+195 拍 = (54−39)×13** |
| stage_1 span | 729 | 924 | +195（+26.7%） |
| 完成观测窗口（触发→done，**含轮询与应答的上界代理**） | 144 拍 | 144 拍 | 0 |
| T_total（stage_0 BEGIN→stage_2 done） | 1713 | 1908 | +195（+11.4%） |
| LOAD/STORE 共享段（span/load/trig→done） | 实测同值 | 同左 | 0 |

表述纪律（用户 2026-10-18 收紧）：
- 写「**完成观测窗口相同**」，**不写「计算周期相同」**——该窗口含轮询与
  应答，可能掩盖较小的执行差异；
- **逻辑指令行（28→39）与装载 beat（39→54）分列**；195 拍写为
  (54−39)×13=195，不表述为「每条逻辑指令占一个 beat」；
- 段分解 `span = load + cfg(78) + trig→done` 四段算术闭合；
- Δ 全部位于 COMPUTE 段指令装载面（TB AXI 装载行为层）：可归因**指令
  足迹**，不归因硬件计算能力（段拍 ~95–97% 系 TB 装载，perf §5）；
- 不套 v3 unroll 常数，不宣称 flat 逐拍契约；限本配置本规模，不外推容量。

锚：设计 `docs/fair_pair_scalar_mul_64w_design_20261018.md`；归档
`verification_v2/artifacts/fair_pair_smul64w_20261018/`（15 项 sha256：
sim 日志/perf JSON/独立参考/verify_ddr/TB sha）。
静态拆解（四步序②，2026-10-18）：
`docs/fair_pair_smul64w_instr_decomposition_20261018.md`——Δ11 行**全部
属 INIT 角色行**（编译器 13 队列统一 [INIT,BODY,TERM] vs 手写 11 队列
[BODY′,TERM] 无独立 INIT；rtovr-35/ur-w0 两侧同 3 行）；无延迟填充行、
无额外终止行（TERM 12/13 逐字节一致）；Δ15 beats = 行差 × 每行 beat 数
（字节落位函数，两侧同队列相同）：7×1+4×2+2×0=15，逐队列算术闭合；
本轮只归因不优化。

## 8. 公平对照第二规模：255 词/SMC（1020 元素，2026-10-18）

四步序③④：预检 `docs/fair_pair_smul255w_precheck_20261018.md`
（双方支持 255w、**准入零放宽**——手写 r2 VCS 先例；编译器
`--n-rows 1020` e2e 全绿、BODY LOOPS=254 实发、字段 10bit 合法；
yaml 零改动，length=声明的 UR word 分配上界非元素数）→ 同一公平条件
正式配对（同一生成器框架、α=2.5 同一补丁-还原序、同一 seed、TB 同 sha）。

**正确性（先决，两链独立全绿）**：COMPARE **8/8 零 SKIP ×2**；
verify_ddr 255 词窗 8/8 ×2；独立 IEEE 参考（--words 255）8/8 ×2；
非目标写零 ×2。**编译器 flat 形态 VCS 实证规模由此前 LOOPS=63 扩至
LOOPS=254（首上 RTL 即 8/8 零 SKIP）**。

**取数与跨规模观测**：

| 量 | 64w A→B | 255w A→B |
|---|---|---|
| COMPUTE 逻辑行 | 28→39 | 28→39（**在这两个测试规模下数量相同**） |
| COMPUTE 装载 beats（×13 拍） | 39→54 | 39→54（**在这两个测试规模下数量相同**） |
| 装载成本差 | +195 拍 | +195 拍（**绝对值恒定**） |
| 完成观测窗口（上界代理） | 144=144 | 334=334（两侧同步 +190） |
| T_total | 1713→1908（+11.4%） | 2661→2856（**+7.3%**） |

核心观测：两侧 COMPUTE 均为批量/平坦形态，指令足迹**在这两个测试规模下
数量相同** ⇒
装载差额绝对值恒定 +195 拍，**相对占比随规模下降（11.4%→7.3%）**；
共享段同规模内两侧逐拍一致（公平性成立），其跨规模自身差异属框架
生成器合法发射差异，不入 A/B 对照。
⚠ 两规模不足以确立任意规模恒定性（容量墙形态切换可能改变结论，
两规模均远在墙下）。表述纪律同 §7（观测窗口≠计算周期；行与 beat
分列；组合程序口径，不入 §1 主表）。

锚：归档 `verification_v2/artifacts/fair_pair_smul255w_20261018/`
（SHA256SUMS 15 项）；预检 `docs/fair_pair_smul255w_precheck_20261018.md`。

## 9. INIT→DLY 折叠：有界编译器优化实验（2026-10-19，commit a01190d）

用户 2026-10-19 令（有界实验，非协议扩张）：§7/§8 已证 Δ195 拍全部来自统一
发射模板的 INIT 行 ⇒ 检查点①②③（逐队列语义比较→结构规则→单候选变换）
→ ④验收。**用户裁定（本行收口，不追残余 26 拍）**：成果表述为
「**在三个已测规模中，通过编译器侧 INIT 折叠保持结果正确，减少 13 个装载
beats，对应当前 TB 下减少 169 拍**」；保持默认关闭合理；**相同完成观测窗
仍不能单独证明内部逐拍完全相同**。

**变换（仅限 ADR-054 flat 分支，默认关 `AIACC_FLAT_INIT_FOLD=1` 开启）**：
`l_init+1+op_dly ≤ 7` 时摘除 INIT 行、相位并入 BODY 头部 3bit DLY 字段；
条件不满足原三行发射逐字不动；无算子名特判；零硬件/hw_config 变更。
等价性 = RTL 两模块（smi_iq_iic.v dly 仅附首 fire + smi_iq_dly_queen.v
dly=k 晚 k 拍）+ sim 单测 + 手写侧 VCS 先例三源闭合。

**正确性（先决，四腿全绿）**：VCS 四腿（16w A/B-fold、64w B-fold、
255w B-fold）COMPARE **8/8 零 SKIP**；verify_ddr 三规模 8/8 bit-exact；
独立 IEEE 参考 + 非目标写零 4/4 腿；pytest 双模式 656 passed 零漂移；
编译器侧潜伏 sim dly 计数器 bug 随本检查点暴露并修复（零演练路径，
6 项新增单测锁定）。新增第三规模 16w（UR 容量下界一侧的边界核验）。

**取数（TB 执行行为层，与 §7/§8 同口径）**：

| 量 | B 未折叠 → B 折叠 | 备注 |
|---|---|---|
| COMPUTE 逻辑指令行 | 39 → 30（−9） | 9 队列折叠；vout0/rtovr-16/rtovr-35/ur-w0 保留 |
| COMPUTE 装载 beats | 54 → 41（**−13**） | 三规模数量相同 |
| 装载成本 | 702 → 533 拍（**−169**） | 实测与静态投影逐拍一致；跨规模绝对恒定 |
| 完成观测窗口（上界代理） | 144/334 → 不变 | 相位零扰动；⚠ 不能单独证明内部逐拍完全相同 |
| T_total | 1908→1739（64w，−8.9%）；2856→2687（255w，−5.9%） | −169 拍绝对恒定 |
| 16w 新规模 | A 1215 / B-fold 1241 | 与 A 残余 +26 拍 |

对 A 手写基线残余 **+2 beats ≈ +26 拍**：逐队列归因 13 队列中 11 队列逐项
相同，唯一差异 = rtovr-16、vout0 各 +1 beat（INIT 保留：l_init+1=10/11
超 3bit DLY 域），属结构性保留而非调度失败。表述纪律同 §7/§8，另加：
−169 拍为 **TB 指令装载拍实测差，不写作板端加速比**；不承诺消除全部
195 拍（实为消除 169、结构性保留 26）。

锚：可行性检查点与验收 `docs/fair_pair_init_fold_feasibility_20261019.md`；
归档 `verification_v2/artifacts/fair_pair_init_fold_acceptance_20261019/`
（SHA256SUMS 26 项）；sim dly 语义锁定 `tests/unit/test_iq_unit_dly.py`。
