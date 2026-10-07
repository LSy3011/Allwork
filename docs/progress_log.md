# 进展总览与论文素材索引（持续更新）

> 用途：单一入口看全部历史——每个里程碑的**日期、结论、证据指针、论文可用性**。
> 维护纪律：每完成一个阶段（或推翻一个既有结论）即追加；数字一律以实测/回源为准，
> 估计值必须标注为估计（纪律见 ADR-056 §G 与 feedback-scoped-milestones-no-overclaim）。
> 本文档是**索引层**：权威细节永远在被指向的 ADR / 证据归档里，此处不复制数据。

## 1. 当前状态快照（2026-09-30）

- **代码基线**：`develop@7f92cb1`（ADR-056 五步序全部 DONE；不推送远端）
- **测试基线**：pytest **349 passed / 7 skipped / 11 xfailed / 148 subtests**（只增不减纪律）
- **V1 交付结论（限定范围）**：在 ADR-056 §A 公式子集、II=1..2 已证槽形态、
  无反压展开分支、**单 SMC（SMC_0 逐拍深度）** 的限定内——只新增公式 YAML 配置，
  即可自动生成指令、真实 VCS 验证并发布算子包（scale_sum 实证）。
- **实测容量边界**：scale_sum n_max=240（束缚队列 rtovr-14 恰 128 行顶格，
  IQ 墙 `smi_top.v:22 SMI_IQ_INSTQ_DEPTH=128`）；n=244 → `STATIC_ADMISSION_REJECTED`。
- **证据等级声明**：本仓库编译器产物的最强证据 = **VCS RTL 级**（无任何上板记录；
  上板证据仅手写微码线有，见 [[memory:u280-llama-r2-onboard-pass]]）。

## 2. 里程碑时间线（倒序；每项 = 日期 / 结论 / 证据指针）

| 日期 | 里程碑 | 一句话结论 | 证据 |
|---|---|---|---|
| 2026-09-30 | **ADR-056 步⑤：V1 闭环验收** | scale_sum 配置-only 准入、n4/8/60/240 GREEN、n244 响亮拒绝、全回归过；步⑤暴露并修复失败码提取缺陷（`/G-xxx` 注解） | `verification_v2/artifacts/v1_step5_scale_sum_acceptance_20260930/README.md`；ADR-056 §F-回填 |
| 2026-09-30 | ADR-056 步④：统一验证链 | 十段编排器 run_v1_verify.py、发布通道分列（v1_admission vs frozen_regression）、失败不发布契约 | develop@74d6557；`tests/unit/test_run_v1_verify.py` |
| 2026-09-29 | 步④-1/2：冻结协议模型 + TB 三日志探针 | 注册表 v1→v2 独立版本化（v1 首跑 RED → RTL 行级取证 → 勘误修正）；72 铺针 + G1–G5 全局仲裁 | `artifacts/protocol_expectations_add_v2_20260929/` |
| 2026-09-28 | 步③：受限准入改造 + add 迁移 | L1 请求/L2 八闸门/L3 验证三层；add 零字节回归 GREEN | `artifacts/add_migration_audit_20260928_195127/` |
| 2026-09-26 | 步①②：基线冻结 + 独立参考发生器 | 有序 AST 参考对 add 三规模锚定 72/72 bit-exact；§C.4 双道数值域定稿 | `artifacts/reference_anchor_20260926/` |
| 2026-09-26 | ADR-056 立项 | V1 受限通用准入方案 v4 用户批准（范围划界/11 失败码/发布制） | `docs/adr/ADR-056-v1-scope-constrained-general-admission.md` |
| 2026-09-12 | ADR-053 阶段2-M1：编译器产物首个真 VCS 证据 | mul 经真 RTL VCS COMPARE PASSED，2048/2048 行 bit-exact | 见 memory `compiler-rtovr-dense-vs-sparse-mismatch` |
| 2026-09-12 | ADR-054/055：发射形态两级 | flat（II=1 单槽）/ 显式展开（II=2 已证形态）；embed 死区机制订正为 LOOP_PARA_SET 自锁 | docs/adr/ADR-054, ADR-055 |
| 2026-09-10 | ADR-052：STB 编译器建模阶段1 | store 编码层补齐（发码与 golden 逐位一致）；**VCS/硅证未做** | docs/adr/ADR-052 |
| 2026-09-10 | 基线切换 | HEAD(5-15)→develop(7-17，领先 256 提交/51 ADR) | memory `thesis-direction-compiler-closedloop` |
| 2026-06 上旬 | ADR-022/023/025/031 | 单 SMI 全局指令流模型确立；matmul=N 维广播机制确立；smc_id_en 全单元统一（待 VCS 逐 SMC 验证） | docs/adr/ 同名文件 |

（更早历史：ADR-001~021 见 docs/adr/，V1/Gemini 时代文档见 docs/{hco,adm,...}，引用前必看 frontmatter `still_holds`。）

## 3. 论文素材映射（论文章节 ↔ 证据）

> 四档覆盖纪律：可调度 / 可发射 / VCS / 上板 —— 任何结论引用前确认其档位，不混称。

| 论文内容 | 可用素材 | 证据档位 | 缺口 |
|---|---|---|---|
| 编译器架构（SymPy→MIR→SMT→LIR→hex） | CLAUDE.md、verification_v2_guide、ADR-001/004 | 代码事实 | — |
| SMT 调度与公理体系 | ADR-006/008/009/013/021 | pytest 全绿 | — |
| RtoVR 稀疏掩码问题与修复（问题发现案例） | ADR-053 + 全量普查（51/51 算子 472/680 条被屏蔽） | VCS 实证 | — |
| 发射机制（embed/flat/unroll 三分支） | ADR-054/055 + II 普查基线 | VCS（mul/add/scale_sum） | II>2 展开未证 |
| **V1 通用准入（核心贡献候选）** | ADR-056 全链：L1/L2/L3、八闸门、双道数值域、发布制 | VCS（add/add_v1/scale_sum × 4 规模点） | 仅单 SMC；形态覆盖=2 类槽形态 |
| 容量边界与响亮拒绝（工程严谨性案例） | 步⑤ n=240 顶格 + n=244 拒绝 + 失败码传递缺陷修复 | VCS + 契约测试 | — |
| 搬运路径（LDB/STB） | ADR-050（LDB 取证史）/ADR-052（STB 阶段1） | LDB 有 VCS；**STB 仅编码层** | M3 端到端未做 |
| 上板闭环 | **编译器产物无**；手写线 llama r2 PASS | 上板（手写线） | 编译器上板 = 最大缺口（已降级为周期抽验，不阻塞主线） |

## 4. 遗留债务登记（随验收保留，不豁免）

- flat ISSUE 层 VCS 实证缺口（ADR-056 §D.4 入门槛项）
- c0_spread 常数未冻结（仲裁器仅记录）
- 反压未建模（统一入口只支持无反压展开形态；出现即 UNVERIFIED）
- `check_iq_probe_trace.py` 注册表化缓办（消费端已由 arbitrate 闭合）
- DEBT-054-II2（add 被 flat 排除）、DEBT-054-DUALSRC、DEBT-055-CAPACITY（待 A2a 解除）
- 8-UNSAT 深查（仅当阻塞目标能力时启动）

## 5. 下一步路线（2026-09-30 用户拍板框架 + 判断见会话记录）

固定 V1 交付版本（tag + 复现说明）→ A2a 自动分段（解除 128 行墙）→ 结构扩张
（新槽形态/II>2 公式）→ M3 HBM 端到端 → 性能与易用性。
8-SMC 逐拍证据补位与 STB 单元级 VCS 预验证的插位建议见 2026-09-30 会话结论。


## 6. 2026-09-30 执行路线落地

- 本地标签 `adr056-v1-closed` 固定于 bfcff47：包含 7f92cb1 实现，差异仅进展文档；没有重新宣称验收。
- 新增 REPRODUCE.md 与 ADR-057 草案；草案复核结论 APPROVE-with-notes（2026-09-30）：两条 must-fix 已落字（ADR-056 G-stage/G-cap 闸门修订声明；验收①点名尾段单迭代形态对账注册表 v2），其余条款确认无误。未实现分段。
- 主线：受限 COMPUTE 分段 → 新结构 → 单 chunk M3-lite；chunk/8 SMC 尾块/上板非最低交付。
- manifest 与原始任务、指令和 RTL 独立对账；LSU 不纳入本轮 SMT；STB 侦察不默认免费并行。
- zwz 地址与目标目录尚待提供，异机备份未完成；本地归档不能算灾备。
- 口径订正：新公式完整事件验收为 II=2 无反压展开；II=1 flat 仍保留 ISSUE 实证债务。


## 2026-09-30 ADR-057 两段侦察续跑（实验，不发布）

- n244（60+1）与 n480（60+60）VCS 原生 compare、独立数值比对及输出地址接续检查通过。
- 证据索引：`verification_v2/artifacts/a2a_recon_followup_20260930/README.md`；各原始运行记录保留。
- 修复实验目录/算子身份混用、独立求解导致不同端口分配、时间与 probe cycle 原点混淆、缺失标记未进入 RED 等问题；没有放宽既有模型期望。
- 限制：C4 逐拍仍只记录；当前输出 bank 写检查不能代替全 bank 监测；应答与下一段 BEGIN 同拍导致 C2 空窗，不据此声称独立排干证明。正式分段器、manifest、统一入口尚待实现。

## 2026-10-01 ADR-057 验收② 正式链双双 GREEN（统一入口）

- **n=244（60+1）与 n=480（60+60）正式验收 GREEN 并发布**（统一入口 run_v1_verify，真实 VCS）：
  run `adr057_accept_n244_r2` / `adr057_accept_n480`，发布包 `releases/scale_sum_adr057_accept_n244_r2` /
  `scale_sum_adr057_accept_n480`。全链：G-cap→分段器→逐段重发射（共享同一 SMT 调度）→链式 sim→
  manifest 封印+D3 独立检查五层→仿真前期望事件冻结→多段打包→VCS→三区比对→逐拍多段仲裁（C1/C2/C3）。
- **C2 空窗语义修订（用户裁决 + evaluator 独立复核 APPROVE-with-notes）**：ack≡BEGIN 同拍空窗
  由显式 RED 降为报告 `limitations[]`（verdict 仅由正向违例决定；限制声明落 stdout 与 run_report
  落账，不静默）。依据：TB 主环零延迟顺序推进（同一 $time 打印两标记）系构造性空窗；残留写排除由
  C1 次序+C3 写集不相交前提下的全时线写归因正向支撑。errata 落 ADR-057「Errata 2026-10-01」。
  复核 notes 全落：N1 末段后写 RED 回归测试、N2 报告落账、N3 集合语义盲点披露、N4 硬键访问。
- **验收③ 基线污染发现与重立（用户裁决）**：冻结基线 669350 的 n8/n60 不可由任何可恢复代码状态
  复现（三向交叉取证：HEAD+WIP ≡ 7f92cb1 ≡ 74d6557+最终yaml，唯基线不同——74d6557 脏树中间态
  产物）。ADR-057 对单段发射零扰动反获更强证明。重立基线 run `adr057_rebaseline_n8` /
  `adr057_rebaseline_n60` 全链 VCS GREEN 已发布，字节与取证批次逐字节一致；n4/n240 基线不动。
  errata 之二 + 取证包 `artifacts/adr057_baseline_forensics_20261001/`。
  前向约束：git_dirty=true 的运行产物不得升格为冻结基线。
- 首次正式跑（adr057_accept_n244）按旧语义 RED 于空窗——熔断线按设计触发，run 目录保留作
  裁决轨迹证据。
- 限制不变：不声称段间独立排干证明；C4 逐拍对注册表 v2 的尾段形态对账仍待做；单段发布包与
  多段发布包并存，n8/n60 旧包（669350 系）保留不改写。

## 2026-10-05 ADR-057 挂账销号点收（四步序全闭合，时间盒内）

- **状态口径（用户指令逐字）**：A2a 已有 n244/n480 正式运行与发布证据；本次销号关闭的是
  ADR-057 验收条款④⑤的挂账项。n8/n60 系经批准的重立基线，不表述成对原冻结基线零字节一致。
- **步① 条款对照表**：`artifacts/adr057_closure_20261005/CLAUSE45_MAP.md`——条款④ 8 子项 +
  条款⑤ 6 子项逐条映射证据/缺项/处理，既有证据零重跑。
- **步② D1/D3 独立审计**（独立审计员，只读）：2 MAJOR + 5 MINOR；五点焦点（分段覆盖/地址
  偏移/manifest 独立性/改道默认关闭/发布条件）全过；**已发布两包正确性不受任一发现影响**。
- **步③ C4 尾段对账**：`C4_TAIL_RECONCILIATION.md`——16 队列×2 段（n244）+ 32 组合（n480）
  手工重算 vs 期望事件 vs 观测 trace 三层全一致；尾段 ISSUE 逐拍抽查 3 队列精确匹配 v2 模型；
  无新常数消费。
- **步④ 对抗与回归**：
  - MAJOR-2（检查器不解码指令，换 hex+改 sha+重封曾六层全绿）按用户裁决**有界实现**：
    检查器新增 IQ-HEX 层（physical_instructions.json 经生产 packer 重编码 ↔ iq_*.hex 逐行
    互校，双向，缺 json 即 RED）；同源限制如实登记（packer 同错不捕，由 VCS 行为闸背书）。
  - MINOR-3/4/5/7 全落：active_smc 逐段锚 [0]（SMC 码）、lanes 锚定 hw_config 位宽（TASK 码）、
    manifest iq path 相对化、调度缺键响拒/hw_dir 解析/assert→RuntimeError。
  - 新增测试 14 个全绿：A 组拦截闸矩阵 7（test_pipeline_runner_gate.py）+ B 组入口注入 4
    （漏段/重复段/偏移一格/非法布局 → artifact_audit 阶段 RED + 不发布 + sentinel 不动，
    先自检合成基线 GREEN 再注入）+ 检查器对抗 3（IQ-HEX 重封/active_smc/lanes）。
  - **新检查器复跑两已发布运行 build 快照双双 GREEN**——IQ-HEX 层对真实产物首次实证。
  - 回归：add frozen 补跑 GREEN（run `adr057_closure_add_reg`，n=4/8/60，frozen_baseline
    verdict=PASS，落 regression 通道）；mul 零字节审计 @99f4acc GREEN（13 文件 cmp -s）。
  - 全量 pytest **414 passed** / 7 skipped / 11 xfailed / 148 subtests（400→414 只增不减）。
- **限制登记（不推翻既有成果）**：裁决器检出证据=检查器有效性非 RTL 实验；写监测仅输出
  bank；IQ-HEX 同源限制；FETCH 逐拍相位未独立重核；c0_spread 未冻结仅记录；写区↔发射
  互校缺席（失效姿态响亮——引入第二地址源时升级为阻断项）。
- 关闭说明：`artifacts/adr057_closure_20261005/CLOSURE.md`。下一步主线 = M3-lite 三道门
  （单 SMC/单 chunk/安全规模/顺序执行；STB 2-3 工作日阻断熔断）+ A2b 半天预检小预算。

## 2026-10-05 M3-lite 启动（用户指令：阶段状态 = V1 已完成，A2a 已收口，M3-lite 开始）

- **收口证据口径（用户明示，逐字纪律）**：ADR-057 收口证据 = 414 项回归 + 已发布包复检查
  （IQ-HEX 新层对 n244_r2/n480 build 快照复跑双 GREEN）+ 限制登记（L1-L6）**三件套共同构成，
  不得只引用测试数量**。用户本轮未独立复验。
- **三道门（按序推进，用户已批准，无需再等启动批准）**：
  1. **STB 独验**：预置 UR，编译器生成 STB/STBAXI → 外部写出数据/地址/数量正确；
     非目标区保持预期；完成通知不早于所需写事务完成。
  2. **LDB 复核**：外部存储装载已知数据 → UR 落位正确；长度/burst 尾部/完成时序有证据。
  3. **完整组合**：自动生成 `LOAD → COMPUTE_0… → STORE`；输入仅放外部存储，最终外部输出
     与独立公式参考 bit-exact；统一入口全部检查通过后发布。
- **范围纪律（逐字）**：单 SMC、单 chunk、安全规模、顺序执行；不引入多 SMC、host chunk、
  搬运重叠或性能优化。**不将计算队列的 ISSUE 模型直接套到 LSU**（LSU IQ 24-27 走
  信用门控发射变体，ADR-050 实证）。
- **熔断线**：STB 投入 2-3 个工作日仍遇重大阻断 → 提交证据与原因，重新分配预算，不无限延长。
- **并行小预算**：A2b 保留半天预检（仅产出候选公式的 II、槽结构与容量预测）；
  论文同步写入已闭合的 V1 与 A2a 成果。
- **现状盘点（第一个动作）已完成（2026-10-05）**：ADR-050/052 回源 + 双路只读侦察 +
  关键取证（smc_id_en 显式 0 不被 payload_factory 回填，:95-97 证实）。报告：
  `verification_v2/artifacts/m3lite_recon_20261005/RECON.md`。
  - 现成不重建：STB 发射层（ADR-052 golden 整数全等）、TB 对 STB-only 单段结构合法、
    UR 预置、HBM dump（hbm_result_smc_i.hex）、完成通知链路（crd+outstanding→INT）、
    单-SMC case 骨架（compiler_add_n4）。
  - 门①需新建：**HBM 窗口自动比对**（全沙箱零消费方，手写 case 当年靠人工抽查）+
    参数化 STB case 生成器。
  - 决定性陷阱七条落账（RECON §2）：末段超时仅 WARN 的假绿通道、compare 脚本 SKIP 假绿、
    smc_id_en 阈值门控（缺省 7 = 8 SMC 全使能 → 首证必须显式 0）、HBM_INIT_DIR 全局共享
    污染、brst=0 禁区、tid=2 照抄、X→0 dump 语义。
  - 留给门③：STB/LDB DFG 节点（STB 零先例）、LOAD/STORE 段 kind（stage_partition/
    stage_manifest/sim_config/run_v1_verify 四处 + compile_admit 分支闸适配）；
    python sim LSU 建模按 ADR-050 先例不建（VCS 为准）。
- **设计草案已产出（待用户定夺，未实施）**：[ADR-058](adr/ADR-058-m3lite-gate1-stb-solo-verification.md)
  （DRAFT）——发射层生成器 + HBM 窗口比对六项断言 + 用例 S1(16 行单 burst)/S2(32 行镜像
  golden row5)/S3(24 行尾 burst) + 负向断言 N1/N2；三个待裁决点：Q1「编译器生成」口径
  （发射层 vs DFG）、Q2 TB 时序观测块（默认不动）、Q3 S2/S3 是否纳入门①充要条件。

## 2026-10-06 M3-lite 门① STB 独验 GREEN（ADR-058 draft→proposed）

- **三用例 VCS + 六项断言比对全过**（S1 16行单burst / S2 32行 loops=1 镜像 golden row5 /
  S3 双描述符 16+8 尾burst），证据归档 `verification_v2/artifacts/m3lite_gate1_20261006/`
  （HBM 大 dump 以 sha256 冻结+沙箱留存，case/快照确定性可重建）。判据逐字对照：
  数据/地址/数量=A1-A3 bit-exact；非目标区=A4 三剖面（预载区全域/X 区/其余 7 颗 en 泄漏）；
  完成时序=结构层 outstanding 排空 + 观测层 dump 晚于 intr（L2 缺口挂账）。
- **证据等级 = VCS RTL 级**（非上板；L2/L3/L4 挂账不变）。L1 核销：smc_1..7 空目录方案
  使 A4c 成为逐颗实证（8 颗全 dump、1..7 未被写）。
- 完成路径两通道均实测：S1/S2 走 batch interrupt（TB:810），S3 走 smi_iq_done（TB:816
  @cycle 20）——比对器 A5a 设计即接受双标记。
- 过程中两修补：生成器 STB 侧 LOOPS 恒 0（golden row5 字段级对照抓获，回归钉
  test_s2_loops_form）；预跑核查发现 TB dump 受 case_smc_max_idx 门控 → 补 smc_1..7
  空目录（否则 A4c 永缺证据）。
- **A2b 预检完成**：`artifacts/a2b_preflight_20261006/preflight.md`，20 候选 II 4..12
  全解出；**n_max 列 = 未校准估计不得用于准入**（用户裁定：scale_sum 实测 240 vs
  估计 63，一次偏保守≠恒保守，疑单位混用；正确口径 = 统一单位后逐队列 II_q·R_q+2，待实现）。
- 环境备注：权限分类器（glm-5.3）间歇故障，执行经沙箱外通道+用户终端混合完成；
  GREEN 判定均以重读产物为准。
- **下一步 = 门② LDB 复核**（从外部存储装载已知数据 → UR 落位/长度/burst 尾部/完成时序）。

## 2026-10-06 M3-lite 门② LDB 复核（ADR-059 v2，实施中）

- **ADR-059 按用户三处契约修正重写为 v2**（修正对照表在文末）：
  - 修正① ur_wr_line_cnt>1 前科正面取证：前科 = ADR-050 :146（DEBT-050-LDB-STREAM-REBASE
    chunk1 提前 DONE/落位错）+ :147（1:N 配对旧 RTL 证伪）；**当前沙箱 RTL 已变**
    （ldb_ur_axi_conv.v 2026-07-21 版，brst=0 守卫已修 :833-835、residue 块改写）→
    旧证伪不自动适用；**w033 golden 同形态（CNT=33、1:3 配对）2026-09-07 运行产物
    8/8 SMC per-SMC golden 全 match**（本轮重读比对，非存档报告），落位 == HBM 源
    逐行 bit-exact。残留风险 = 改编变量（en 7→0 / intlv→0 / 单 stage）→ L1 为首证，
    失败备选逐行写。
  - 修正② B4c 独立初值：生成器为 smc_1..7 显式写 sentinel′（GEN ur_only + TB
    $readmemh 装载机制取证），期望先验钉死，禁止首跑 dump 反钉；装载生效与无泄漏同证。
  - 修正③ B5 完成时序实证：复用既有 ur_write_trace.log（commit 级，常开）+ sim.log
    `stage_0 done (intr cleared) @probe_cycle P` 锚点（TB :2393，双通道通用，零 TB 改动）；
    B5b 实际写次数独立于终态行数；B5c max写 < P−MARGIN(1000) 且通知后零残留，
    gap ≤ MARGIN → RED「时序不可区分」（不确定窗转响亮失败）。
  - 口径 a：K16 改「当前可访问归档中未找到，不计入已验证覆盖」；
    口径 b：HBM_INIT_DIR 串行+备份 sha256+trap EXIT 恢复（m3lite_gate2_run.sh）。
- **工具落地**：`ldb_review_case_gen.py`（守卫 11 条）+ `ur_window_compare.py`
  （B1-B6 含 B5 三子项）+ `m3lite_gate2_run.sh`；单测 36/36（含 15 项 RED 注入全按
  预期翻转；B5c 边界 gap==MARGIN 语义对齐为严格不等——ADR 文「gap ≤ MARGIN → RED」）。
  全量 pytest 478（基线 442+36，只增不减）。
- ~~待做：L1/L2 VCS 实跑 → 归档 → 门③ LOAD 参数草稿（Q2 已批，不宣称集成通过）；
  L3 loops 形态为探索项（Q1 已批）。~~（见下节实跑结果）

## 2026-10-06 M3-lite 门② L1/L2 实跑：数据面全绿，完成时序 RED（重大实测发现）

- **过程三熔断全部按设计触发并处置**：① 首跑 B5b/B5c RED = 探针挂错通道
  （ur_write_trace 原探针只覆盖 CRU 写口 r_we_pipe；LDB 走 URA 独立通道，
  SMC_ALL.sv:990-992 → user_register i_ura_*）→ TB 补 URA 探针支路；
  ② 复跑 B5c RED「时序不可区分」（max写@181 距轮询锚点 gap=29 < MARGIN=1000）
  → 按 R5 预案改 TB 精确锚点（intr_ro 上升沿，smi_ctrl_regs.v:121，双完成通道
  同源），断言收紧为严格不等；③ 精确锚点首证即 RED：**通知沿@150 早于首条
  UR 写@166**——不是锚点误差，是真实现象。
- **发现（逐环取证，非推测）：LSU 批完成中断不保证数据落位。** 取证探针
  （batch_done/ir_err/dispatch_done/rdi_done/credit 六路）证实：intid 自增来自
  batch_done（非错误事件）；smi_iq_lsu_complete 的 WAIT_DRAIN 在 inst_queen
  dispatch-done 与 dly_queen 执行计数递增之间的流水线空窗采到 outstanding==0，
  提前放出 rdi_done（L1：RDI_DONE26/27@150 时 outstanding=1）。真实完成
  （单元信用返回）：L1 CRD_LDB@183、L2 CRD_LDB@272——均晚于通知沿
  （L1@150 / L2@270）。L2 因 LDBAXI 三描述符撑过空窗，通知推迟到末写同拍，
  但 LDB 队列（26）仍提前（@202，outstanding=1），语义下限不变。
- **与 ADR-050 前科的关系**：修正①证伪的是旧 RTL residue re-baseline 机制；
  「提前 done」行为类别在当前 RTL（2026-07-21 版）经 **lsu_complete 采样竞态**
  依然存在——前科的方向性直觉被新机制证实。
- **对门①的回溯影响（待用户裁决是否落 ADR-058 errata）**：门① A5 完成时序的
  「结构层 outstanding 排空」前提被此机制证伪；A1-A4 数据断言不受影响
  （dump 时刻远在写完后）。
- **裁决记录**：B5c 按修正③原文判 RED，未放宽判据、未按输出调期望。
  L1/L2 数据面（B1-B4c/B5a/B5b/B6）全绿。门②收口口径、门③屏障约束、
  门① errata 三项待用户裁决（ADR-059「待裁决」J1/J2/J3）。
- 证据归档 `verification_v2/artifacts/m3lite_gate2_20261006/`（含四轮 L1 现场
  关键产物、TB 探针原文与哈希、比对器可复跑 verdict）。TB 三处 additive 纯观测
  改动已透明呈报（ADR-059 Context）。全量 pytest **480 passed**（478+2，
  B5c 精确锚点化净增 2 个 RED 注入测试）。
- ~~**门③ LOAD 参数草稿未启动**（Q2 授权前提为「L1/L2 通过」，当前未完成时序项，
  待裁决后再动）。L3 loops 探索项同理搁置。~~
- **用户裁决（2026-10-06，四项）**：J1 门②按实测收口（判据原文「完成时序有证据」
  以否定性实测满足；数据面 GREEN + 否定结论收口；B5c 严格判据不改，其 RED 签名
  即基线）→ ADR-059 升 proposed；J2 门③携带显式落位屏障约束（初值 ≥ total+48 拍，
  实施时复测收紧）；J3 门① ADR-058 补 errata（A5 结构层前提证伪、措辞降级，
  数据结论不变）；当前状态 commit develop 不推送。
- **门②收口 = proposed**；门③ LOAD 参数草稿已出（[ADR-060](adr/ADR-060-m3lite-gate3-load-segment-params.md)，
  DRAFT）：L1/L2 实测参数表 + 屏障约束 + 证据指针；明确未做集成/DFG/L3。

## 2026-10-06 门②状态订正 + 门③同步契约授权（用户指令，逐字要点）

- **状态订正（用户原话）**：「门②取证收口：L1/L2 数据面通过；完成时序 B5c 失败，
  保留反例；门③必须解决 LOAD→COMPUTE 同步。」——门②**不记为全绿**；ADR-059
  状态块与本日志按此口径修订。用户本轮未独立复验新日志与源码，决策基于报告。
- **裁决 1：不批准 total+48 作正式完成屏障**。它仅是两个用例上的候选等待值，
  无上界证明，不得推广到其他长度/burst/反压/环境；只允许作**明确标注的实验
  侦察**候选值。「信用无 CSR 可观测→固定等待唯一可行」的论断被驳回为过早——
  须先核查现有完成/busy/错误/握手机制，并评估修复完成逻辑的成本。
- **裁决 2：门③设计核查与最小实验即日授权**（无需再等启动确认），四步序：
  ① 核查 LOAD 可用的真实完成机制——列明软件可观察信号、RTL 内部信号及其保证，
  区分正式方案与 TB 实验方案 → ② 修订 ADR-060——明确屏障类型、适用范围、
  超时与未验证时拒绝发布的行为 → ③ 最小 LOAD→COMPUTE 实验——输入只在外部
  存储、UR 预置不同 sentinel，证明 COMPUTE 首次有效读晚于对应 LOAD 最后写入
  → ④ 加入 STORE——独立核查最后外部写事务完成，再允许比对和发布。
  实验中可用 RTL 内部信用信号建对照屏障，但必须标注 **TB 辅助实验**，不得冒充
  现有 host 可执行协议。暂不把 LSU 塞进固定周期 SMT 调度器；优先在程序级
  stage 编排表达依赖。
- **裁决 3：STB 完成保证重新核对**。ADR-058 结构前提已被推翻，不能只补措辞
  继续依赖；门③结束条件须证明所需外部写事务已完成，不得再次依赖轮询或
  dump 延迟碰巧掩盖提前通知。
- **裁决 4：同步方案时间预算 1–2 个工作日**（机制核查 + 方案比较）：
  找到可靠现有协议→继续正式集成；需改 RTL→单独说明改动/版本/回归影响后再定；
  只剩无上界固定等待→保留限定实验结果，**不宣布 M3-lite 正式闭环**。
- **用户定调**：「这次发现明确解释了为什么『单段最终结果正确』不足以支持
  『多阶段安全组合』。当前最优先的工作是建立可靠的段间依赖，而不是先扩大
  集成改动面。」

### 步骤① 机制核查 + 步骤② ADR-060 修订完成（2026-10-06，当日）

- **核查结论（全文 `verification_v2/artifacts/m3lite_gate3_sync_recon_20261006/RECON.md`，
  sha256 已冻结）**：现有 host 可执行协议中**不存在** LOAD 落位的可靠完成机制——
  - 软件面穷举（cfg_reg_addr 全树仅 3 文件；smi_ctrl_regs.v 385 行全读）：
    完成信号仅 batch_done 双皮（0x004 intr / 0x024 smi_iq_done，:292-293 同源），
    无 per-IQ done/busy/outstanding/credit CSR；**无 UR host 读回**
    （BRAM_PORTA 顶层绑死 aiacc_top_f.v:1921-1925）。
  - 指令级：**COND 双死**——EN_COND=0 参数级禁用（smi_iq.v:63 默认，实例化宏
    未覆盖）且 ir_ready_bus 的 LSU 位（24-27）**声明后无驱动**（smi_top.v:289-315）；
    语义本为就绪撮合非完成。golden STB COND=26 因此惰性无效——DEBT-052-STB-HDR
    部分解谜。DLY 活但 ≤7 拍/指令，量级不足且无上界。START 队列（深度4）批间
    自动链接存在，但把竞态直接当依赖——作为同步手段反向。
  - RTL 内部真实信号（TB 辅助实验候选）：`crd_ldb_o/crd_ldbaxi_o` 结构性末写
    之后（ldb FSM :1384/:1461-1466）+ 门②实测；STB 侧 crd_stbaxi 仅到
    DW_axi_gm 受理（`i_saccept`），**不覆盖 b 通道**（b 封闭于 Synopsys IP 内部）；
    外部写真完成锚点 = TB HBM SRAM 模型写口。
  - lsu_complete 竞态定格：`.pre_sync_reset` 备份与现版逐字节一致——设计固有，
    非回归引入；修复方向 A（seen-nonzero，有空队列死锁陷阱）/B（信用对账）
    仅素描未实施，属裁决 4 分支 2 单独报批项。
- **方案定界**：F5 TB 辅助信用屏障 = 步骤③④实验屏障（标注仿真限定）；
  F6 RTL 修复 = 正式化唯一候选待报批；F7 固定等待降级为标注实验。
- **ADR-060 修订**：§2 重写为屏障类型表 + 实验屏障契约（信用齐才放行、
  COMPUTE 首读 > LOAD 末写、STORE 以 HBM 写口为锚、超时就 RED、未验证拒发）；
  §5 步骤③④实验设计要点（含屏障旁路负向断言）。
- **下一步 = 步骤③最小 LOAD→COMPUTE 实验**（TB 读口探针 + 信用屏障 +
  两队列指令生成；预算第 2 日内）。

### 步骤③ 参考挖掘完成（2026-10-06，用户指令：参照手写 mul_scalar + GPLPU 设计文档）

- **设计文档定位**：`~/Jianyuan/aiacc/doc/GLGPU.md`（2860 行；文件名 GLGPU、内容
  「GPLPU指令集架构」）。缺席断言纪律：按 "GPLPU" 文件名全盘未命中后逐层扩搜
  命中。RECON.md §8 已补遗。
- **重大互证（三条独立经验记录 ↔ 门②机制发现）**：
  ① 上游 legacy `op/mul_scalar/mul_scalar_op.cpp:226` 注释「RTL实测：LDB 的
  ur_wr_line_cnt>1 会提前 done 但不稳定落 UR」→ 该线弃 bulk 用 loops 模式；
  ② v3 `lsu_op_base.cpp::bulk_burst_segments` 三处避险（整 burst 尾拆 16+8+8 /
  避 brst=0 / WC==1 补 guard line，注释明写「outstanding completion 未收口」
  「后一个可能没搬进 UR 就 complete」）；
  ③ 门② VCS 实测 B5c RED（lsu_complete 采样竞态，通知先于落位）。
  三条跨代码库、跨时间的记录指向同族「完成早于落位」——论文级素材。
- **R3 核销**：LINE_BYTE_CNT=15 = N-1 编码（手写注释「0x0F // 16 字节/行」+
  文档最大 511），从「照抄 golden」降级为已理解常量。
- **上游资产勘察结论**：
  - `~/Jianyuan/SMI_IQ/op/mul_scalar`（legacy，Makefile DISABLED_OP_DIRS 屏蔽，
    未对齐 v4.19 iq 层）——但 compute 发射逻辑被 v3 复用；
  - `~/Jianyuan/SMI_IQ/load_store_v3/` = 上板血统 L-C-S 框架（mul_scalar/
    mul/select_int/bitwise_and 四算子 + `gen_*_case.cpp` 独立生成器 +
    verify_ddr_v3.py）；`MulScalarLsuOp` = LsuOpBase（LOAD/STORE bulk 分段 +
    三 stage 目录组装）+ legacy compile（compute 12+ IQ：UR_R×2/VIN68/69/70/
    RTOVR×5/FPMUL/UR_W，全平坦 jump=embed=cond=0，dly∈{0..7}）。
  - `default_compute_desc` 硬编码 num_smc=8/smc_id_en=7/gr_intlv=0x10000000/
    bulk_mode=true；LSU_WC 环境变量控 word_count（≤255）；scalar 固定 1.25。
  - 组装输出：`smi_iq_inst/stage_{0,1,2}/iq_*.hex` + stages.md（v0.4.20
    multi-stage 格式，与本沙箱 TB 同）、smc_*/ur_data 全零预置（门③需改写为
    sentinel）、output_bank/bank2_final.hex golden、meta_v3.json、
    **直写全局 hbm_init_dir**（D5 纪律：驱动须备份/恢复或重定向 scratch）。
- **门③ ③ 方案定型**：scratch 外部构建 v3 生成器（patch desc：num_smc=1/
  smc_id_en=0/gr_intlv=0、hbm_dir 重定向、WC=16 fp32），原样消费其
  stage_1 compute hex + golden（fp32 输入域 [-2,2]×1.25 全在规格化区，
  不触 FTZ）；LOAD/STORE 沿用其 stage_0/2 hex 或门②生成器形态（待装配时
  定）；TB 加 plusarg 门控信用屏障 + CRU 读口探针 + 屏障旁路负向用例。

### 步骤③ 工具链落地完成、构建被分类器故障阻断（2026-10-06）

- **四件套已落盘**（全部新增；11 用例首跑全过——480 全量基线复验随 VCS 同做）：
  - `tools/m3lite_gate3_case_gen.py`（装配器）：v3 生成器 scratch 调用 →
    结构校验（stage_0=2+2 行 / stage_1=12 文件集精确匹配）→ **stage_0 与门②
    build_and_emit 同参逐字节互校**（不等即响亮停止）→ 期望从冻结 HBM 快照
    独立推导（struct fp32；v3 golden 仅作布局一致性校验，非独立真源）→
    物化 case（smc_0 全 sentinel / smc_1..7 sentinel′ / output_bank 终态
    供 stock compare 第二意见 / g3_expect.json / v3_gen 归档）。
  - `tools/m3lite_gate3_compare.py`（比对器）：C0 证据前提（屏障标记恰位 +
    GEN 清单行数正面取证 2/12 + 末段假绿通道排除）/ C1 数据 / C2 哨兵全谱 /
    **C3 时序=min(窗口读拍) > max(窗口写拍) 严格不等 + > barrier release 拍** /
    C4 写账（bank0/1 每址恰 1 写且 wdata==源行——**端到端字节序闭环已用门②
    L1 实物 trace↔dump 逐字节确认**；窗外/旁 bank/跨 SMC 零写）/ C5 HBM 预载
    区==快照（**前缀语义**：快照 528 行 vs dump 1048576 行，尾部=未加载区
    X→0 不可判读，WARN 不判——落地前抓到的自身设计 bug）。
  - `tools/m3lite_gate3_run.sh`（驱动）：D5 备份/trap 恢复纪律照搬门②；
    正向（+G3_BARRIER）与负控（+G3_BYPASS）各用独立 case 目录（同 seed
    确定性重建），屏障参数从 g3_expect.json 回读。
  - `tests/test_m3lite_gate3.py`：11 用例（happy/C1/C2aux/C3/C4/C0 五路 RED
    注入 + 负控证活/负控未证活=RED + hex 回环 + FTZ 响拒 + 布局校验）。
- **共享沙箱资产追加改动（透明呈报，全部 additive 默认关）**：
  `run_sim_aiacc_top_f_multi_stage.sh` 增 `EXTRA_PLUSARGS` 透传口
  （原脚本无任何 plusarg 透传，RUN_CMD 固定五项——此为唯一新增口）。
- **静态预验全过**：TB 探针/屏障/负控/钩子逐行复读一致；v3 scalar=全窗
  1.25f（`mul_scalar_lsu_op.cpp:40/:123-136`）、HBM 仅写 memory_init1
  （num_smc=1 → :455-457 单文件）、528 行整（:285-289）、MSB-first
  （:311-319）与装配器假设逐条吻合。
- **构建（2026-10-07，用户终端接力完成）**：分类器持续拦 g++，按破阻协议
  移交用户终端；两轮排障——① 漏拷 `op/common/aiacc_op_ur.h`（ur_iq.h 相对
  包含，include 全量扫描确认唯一缺口）；② 链接缺 3 符号（gen_ur_read/write
  _inst、gen_fpmul_inst）→ 补拷 mul_scalar_op_{ur,compute,rtovr}_inst.cpp +
  ur_code_extractor.h。构建成功仅上游原样 warning；binary sha256
  e8e48c2c…ac5d 留档。`tests/test_m3lite_gate3.py` 11/11 首跑全过。

### 步骤③ 四轮驱动：正向闭环 GREEN + 负控 pivot 至并段单 trigger（2026-10-07）

- **驱动方式**：分类器持续拦 VCS/gen 命令，四轮均按破阻协议移交用户终端
  接力，事后重读产物核实（trap 恢复 sha256 逐轮一致 ✓）。
- **第 1 轮**：装配器守卫拦下——stage_1 实产 13 文件 vs 期望 12（漏
  rtovr-16）。**回源核实** `mul_scalar_op.cpp:505-510` 六条 rtovr 路由 +
  `:952` 命名 → 期望集订正为 13（「不按实际输出调期望」的正确执行例：
  先回源证合法性再改期望）；比对器 GEN 清单数改从 g3_expect 回读，不硬编码。
- **第 2 轮**：互校拦下——v3 定宽打印（28/40 字符带前导零）vs 门②去前导零
  （27/39；门② L1 历史 VCS 实证文件同为 27），**整数值 XOR==0**。互校改按值
  比对（$readmemh 值语义等价），两单测锁定（填充等价通过/真比特差异拒绝）。
- **第 3 轮**：**正向全绿 43 PASS/0 RED**——屏障 enter/release@338（恰
  2+2 脉冲）、窗口末写@259、COMPUTE 首读@931（gap=672）、INTR_ASSERT 恰 2、
  C1 乘积 bit-exact、C2 哨兵全谱、C4 写账逐址逐值、C5 HBM 无损、stock
  compare 独立第二意见 PASS。负控 time-0 $fatal——**`$test$plusargs` 前缀
  匹配坑**：`"G3_BARRIER"` 误中 `+G3_BARRIER_TIMEOUT=5000`。修：一律
  `$value$plusargs("NAME=%d")` 只认 `=1` 形态（声明 integer 化 + 文档订正）。
- **第 4 轮**：正向复绿（与第 3 轮逐数字一致，确定性重建 ✓）；负控 SIM
  PASSED 但**全绿=未证活**（比对器正确判 RED：不确定≠通过）——裸 intr 沿
  ~@300 反在末写@259 之后，BYPASS 结构性踩不中写窗。
- **trace 定量根因**（m3lite_g3_lc_w16 实物）：stage_1 BEGIN@338 → 取指@926
  ——**IQ_EN 同步 13 队列耗 ~588 拍，全部在 trigger 之前**；trigger→compute
  首读仅 **5 拍**（fetch@926→read@931）；LDB trigger→首写 **21 拍**
  （fetch@197→write@218），写窗 218..259。→ 任何「改放行时机」的负控
  （BYPASS/立即放行/固定早放行）首读都 ≥~700 拍，**结构性撞不上写窗**；
  快慢差在取数路径本身（寄存器读 vs AXI 取 HBM），是物理的。B5c 的「通知
  早于落位」是单窗 L1 形态实测（提前 ~16-31 拍），双窗 bulk 下批中断沿反在
  末写之后 ~40 拍——**case 形态相关，不可泛化**。
- **负控 pivot = 并段单 trigger（merged_single_stage）**：LOAD 2+2 与 compute
  13 队列并入 stage_0 同 trigger——compute 首读 ≈T+5 ≺ LDB 首写 ≈T+21、末写
  ≈T+62，**读恒先于写，C1/C3/C4(乘积账) 必红**；C2/C5 应绿（LOAD 无恙、
  红纯因竞态），正是负控应有的证伪剖面。**零 TB 改动**（单段多队列是 TB
  常规形态；GEN 按目录发现 stage→清单 15 行；TB probe_stage_count 探不到
  stage_1 清单即 count=1；链路机械闭环已正面核实）。FORCE_RACE 草拟后经本
  定量分析证死，**未编译即回退**；TB = 第 4 轮已验证状态 + BYPASS 档案注释
  （无功能改动，sha256 `6ef2bca9…285d8`）。
- **工具链增量**：case_gen `--merge-stages`；compare 负控双形态
  （merged=现行 / bypass=档案复现）+ merged 白名单放行 C4 bank2 末写 RED +
  并段 INTR_ASSERT 恰 1；run.sh 负控段切 merged（stock compare 预期 FAILED
  = 竞态第三意见，非故障）。单测 13→**15 全过**；**全量基线复验 495 passed**
  （480+15 恰符，7 skipped/11 xfailed 不动）。自抓自修一例：白名单前缀匹配
  撞上 Report 标签 `"RED "` 尾部空格 → 回退子串语义（两「证活」单测当场拦下）。
- **第 5 轮（2026-10-07，用户终端接力，备份 g3_20261007_135950）——步骤③
  证据链闭环**：
  - **正向第三次复现**（逐数字一致：release@338 / 首读@931 / 末写@259 /
    gap=672 / 43 PASS 0 RED / stock compare PASS）。TB 因注释改动触发重建，
    行为逐比特不变——「注释-only」声明获行为级确认。
  - **负控 NEGATIVE-CONTROL LIVE（exit=0）**：C1 红（16/16 垃圾乘积）、
    **C3 红 min_read=709 vs max_write=766（gap=−57，读真实先于写 57 拍，
    非边缘伪影）**、C4 仅「bank2 末写==期望乘积」红（白名单内）、
    **C2/C5 全绿**（LOAD 落位完好——红纯因竞态）、C0 全绿（无 G3 标记 /
    INTR_ASSERT 恰 1 / 清单 15 行 / 无 stage_1 目录正面取证）、stock compare
    独立 FAIL 作第三意见、trap 恢复 sha256 一致。
  - 定量模型自洽旁证：并段 trigger≈704（15 队列同步），首读 T+5=709、写窗
    T+21..62=725..766——5/21/62 拍模型对实测全中。
  - **裁决 2 步骤③完成判据全部满足**：COMPUTE 首次有效读严格晚于 LOAD
    最后写入（931>259）且晚于屏障 release（931>338）——VCS 实测三轮
    确定性复现；负控端到端证活（断言链在仿真内确实能红）。
  - 证据分级落账：正向屏障 = **TB 辅助实验**（ADR-060 §2.2 标注，非 host
    可执行协议、非上板语义）；VCS≠硅证；B5c 反例保留（门②不记全绿不变）。

## 2026-10-07 M3-lite 门③ 步骤④（STORE 完整链路）——drive-1/2/3 收口

- **实施**（ADR-060 §6 设计冻结后）：TB +三道闸（WBARRIER 写落位 / S4 完成
  闸 / 四事件探针 / 反压注入器），case_gen `--with-store` + 门①发射逐字节
  互校，compare C0-C8（C6 精确写账 / C7 四事件时序 / C8 完整链路 /
  fatal_expected 模式），run.sh 五跑驱动；单测 15→37（文件级 37/37），
  全量 pytest 495→**517**（只增不减）。
- **drive-1（TB 805a7969…1cbcc6）**：正向实质 GREEN——唯一 RED 系比对器
  C0「屏障恰各 1」spec bug（G3 信用屏障按段间转移逐次触发，3 段=2 对）；
  修为「对数=转移数 + 第 2+ 次直穿 waited==0 + 全部 release 计数回核」
  并补 2 个 RED 单测（先验依据=TB 挂点结构与计数不清零语义，非按输出调
  期望）。负控 LIVE。**W-stall 300 拍永久锁死写路径**（release 后 4746+
  拍零恢复、CRD_STBAXI 再未发、hbm_writes 恒 0；嫌疑=桥模型内部读自身
  wready 输出线 atcrambrg300.v:442/:323/:422-426）→ errata ⑤ +
  物证 wedge_wstall_drive1/，注入器移除勿恢复。
- **drive-2（TB 23efdf6f…0344，B 通道 BVALID 沿武装，用户终端接力）**：
  正向 **GREEN（red=0 warn=4）**——C0 修正经端到端验证（恰 2 对/直穿 0/
  计数回核）；WBARRIER waited=30（硬断言>0）；信用不对称 store 侧实证
  （CRD_STB@1184/CRD_STBAXI@1188 ≺ 末写@1191，gap=−7/−3）；C6 写账
  16/16 址恰 1 写、dina==乘积、wea==FFFF、零非目标；C7 四事件时序全序
  （issue@1155→CRD→末写/B@1191→S4 release/DUMP@1219）；C8 完整链路
  逐位。负控 **NEGATIVE-CONTROL LIVE**（C1/C3 gap=−63/C4 末写/C3s
  gap=−25/C6/C8 六路证伪）。两超时 PASS（to_wb $fatal@9495000ps；
  to_s4 $fatal@12885000ps，writes 16/16+bresp 0/1 精确隔离 bresp 半条件）。
  **BP 跑预注册 GREEN 未达成**：B 响应永久丢失（B 活窗仅 1 拍 + 桥模型
  空-FIFO 直通 atcrambrg300.v:344，`@(posedge bvalid)` 武装结构性迟到），
  S4 持满 5000 拍 $fatal、全程零 dump——**要求③结束顺序在「写齐+响应
  缺席」形态下生效的实证**。
- **drive-3（TB de839cc8…222c，AW 前置武装，用户批准改道）**：bready=0
  于 @1174（BVALID 前 17 拍）就位仍丢 B → **直通竞态假说否证**。结论：
  本 TB 的 ds2 B 响应对任何跨 BVALID 产生沿的停顿不可恢复（W / BVALID
  沿 / AW 前置三注入点、两通道一致）；子位点（桥 FIFO 捕获失效 vs DUT
  bready 窗口后不复位）未分清，FSDB 波形留档待查。⚠ drive-3 数据面
  4 项红系单独重跑未恢复 case 全局 HBM 预载的程序性失误（实载=a5a5 旧
  图案，写痕解码证实），与 B 结论正交；**纪律补录：单独重跑任一腿前
  先把该 case 的 memory_init_snapshot 恢复进 data_engine/sram/**。
- **收口（要求⑤证据矩阵）**：WBARRIER 等待→放行（自然 waited=30）✓；
  两超时响亮失败 ✓✓；S4 持死双形态（bresp 缺：drive-2/3；writes 缺：
  drive-1/to_s4）✓；「延迟→恢复→GREEN」形态环境不可达，落账 ADR-060
  §6.7/errata 6。归档：`verification_v2/artifacts/
  m3lite_gate3_round6_positive_20261007/` + `…_faultinjection_20261007/`
  （各 README 定量表 + SHA256SUMS）。**DS 写路反压耐受 = 开放发现**
  （根因调查或为论文素材）。门③步骤①-④至此全部执行完毕；集成仍属
  授权边界外。

## 2026-10-07 门③ 步骤④ 复核收口：用户两处结论收紧 + 任务 1（可恢复实验版本）落地

**用户复核指令（基于收口报告，未独立复查日志/代码，逐字要旨）**：
- **收紧①**：「延迟→恢复→GREEN 本 TB 不可达」过强 → 「**当前测试配置及
  已尝试注入方式下，尚未实现恢复**」；注入器/桥模型/DUT 根因未分清前
  不得认定硬件本身无法恢复。
- **收紧②**：超时负控证明的是「缺失完成条件时不会错误放行」；自然
  waited=30 证明「某一路实际发生等待」；二者不替代所有屏障的延迟-恢复验证。
- **drive-3 污染运行裁定**：继续保留为污染运行；依赖正确初态的结论只能
  由恢复预载后的干净运行支撑。
- **批准结论句（论文/进展总览可用，逐字）**：「M3-lite 已取得 TB 辅助的
  端到端功能正确性及缺失完成条件时拒绝放行的证据；host 完成协议和反压
  恢复仍是开放问题。」
- **授权两有界任务**（不立即修 RTL、不同时开三条大主线）：任务 1（半天）
  = 可恢复实验版本；任务 2（1-2 天）= 反压注入合法性核查与阻塞责任边界
  （三问：协议保持/不一致、B 响应生成-保存-消费三态定位、解除后阻塞点）。
- **F6 RTL 不授权直改**：先交具体修复提案（修改点/消缺机理/影响面/回归）。
- `tb_assisted_experiment` 统一入口通道可随后增加，但禁入 V1 已验证包
  通道、不得掩盖 host 同步未解决（随后项，本轮未实施）。

**文档收紧落账**：ADR-060 §6.5/§6.7/errata ⑥ 按裁定改写 + 新增 errata ⑦
（含三问与路线裁定全量）；round6 两份归档 README 系 sha256 冻结载体，
保留旧措辞不改，以 errata ⑦ 为准。

**任务 1 落地（消除人工预载步骤）**：
- 新工具 `verification_v2/tools/hbm_preload.py`：check（只读三方比对：
  现行全局 vs 冻结快照 vs expect 冻结哈希）/ restore（先验快照可信→
  复制→逐文件复核，任一失败拒跑且不半恢复）。
- `m3lite_gate3_case_gen.py`：expect 增 `hbm_preload.files` 8 文件 sha256
  冻结锚。
- `m3lite_gate3_compare.py`：`--tb-sha256` + 报告 `provenance`（sim 时/
  比对时双指纹并列，差异记 WARN 不 RED——归档复比对合法前进）。
- `m3lite_gate3_run.sh`：flock 整跑串行占用共享预载目录；每腿 VCS 前
  restore 门（拒跑即跳过该腿 VCS+比对）；测试时 TB 指纹传入比对器；
  [7/9] BP 腿期望按实测改写为持死形态（positive 信息性 + fatal_expected
  正式判定双模式）；头注退役史/errata 编号同步。
- 新 `m3lite_gate3_rerun_leg.sh`：单腿干净重跑唯一受支持入口
  （flock→restore 门→TB 指纹差异明示→run_sim），替代裸 run_sim。
- **实测**：`hbm_preload.py check` 对真实 `m3lite_g4_lc_w16_bp` 立即检出
  8/8 STALE（drive-3 污染形态复现，工具抓得住该故障类）。
- pytest **517→523**（+6：check 检出陈旧/restore 后转 CLEAN/快照-expect
  不符拒跑且不写/快照不全拒跑/缺现行文件检出/TB 指纹三态）。
- 待办：干净全量复跑（run.sh 五腿）——为 AW 前置武装 B 结论补干净运行
  支撑（drive-3 裁定）；其后任务 2 开工。

**任务 2 源码侦察收口（2026-10-07，纯只读核查，errata ⑧ 已回填）**：
- 桥实例两副本系 `ifdef NDS_FPGA` 互斥分支、同名 `u_sram_brg` ⇒ 探针
  路径 `GEN_SRAM[0].SMC0.u_sram.u_sram_brg.bresp_fifo_empty`。
- 矩阵 ds2 口实例 `ds2_ctrl` + 子实例 `ds_wdata_bresp` ⇒ 探针路径
  `dut.u_atcbmc300.ds2_ctrl.ds_wdata_bresp.{pending_bvalid,slv_bvalid,
  bresp_cnt_empty}`。
- **LSU 写主口身份落定**：SMCk 写经 us(3+k) 口，`us3_bready=
  de_stb_bready[0]`（DS 引擎驱动）；us2 tie 1 与本链路无关 ⇒ 合法 B
  反压注入点具体化 = force `de_stb_bready[0]`（master 源头，双方同
  视图），「延迟→恢复」重做候选。
- 探针补丁**有意暂缓**：干净复跑须先用现行 TB（de839cc8…222c）保
  指纹链纯洁，其后才上 additive 探针验证 errata ⑧ 假说。
- **阻塞**：glm-5.3 分类器连续 5 次超时（上 session 3 + 本 session 2，
  沙箱与 dangerouslyDisableSandbox 两通道均堵）→ 干净复跑启动与
  commit 均须 Bash，按破阻协议移交用户终端接力（命令见下条），
  事后重读 /tmp/g4_task1_rerun.log 与五份 g3_report_*.json 核实。

**接力轮回填（2026-10-07 深夜，ADR-060 errata ⑨）**：用户终端接力完成
干净全量复跑（新 case 基名 m3lite_g4_task1_clean_20261007_codex；首次
普通沙箱尝试撞许可证失败，该次不作证据已排除）。本会话逐标记核实：
- 五腿 gen 全 exit=0；预载门 RESTORED+VERIFIED ×5（8/8）；trap 恢复
  校验一致；五报告 provenance 双指纹一致（de839cc8…222c，TB 未动）。
- 正向 GREEN（red=0 warn=4），**全部关键拍号与 round6 drive-1 逐拍
  一致**（338/1021/959 waited=30/1184/1188/1191/1219/931>259）——
  跨运行确定性复现成立。
- 负控 NEGATIVE-CONTROL LIVE（red=6）；BP 腿 fatal_expected PASS +
  信息性 red=21（**数据面全绿，drive-3 的 +4 假红消失** = 干净补证
  落地）；两超时腿 PASS（TIMEOUT 标记 + 零 DUMP）。
- ⚠ BP 腿不得描述为「反压恢复通过」（缺失完成条件不误放行的证据；
  延迟-恢复仍未实现）。
- **新发现并修补执行漏洞**：①gen 目录已存在失败后旧产物会被沿用
  （原纯存在性检查放行）→ 五腿 gen 非零即 fail-fast；②脚本 exit=0
  不代表各腿通过 → 末行五腿期望形态汇总退出码（任何方向偏差含
  意外变绿均 exit=1 交人工复核）。接力轮增补（每腿 console log +
  SHA256SUMS.reports 归档步）已复审保留。+3 静态锚定测试。
- 归档 `verification_v2/artifacts/m3lite_gate3_round7_task1clean_20261007/`
  （README + 26 项 sha256 冻结）。
- pytest 523→**526**。待办：commit 任务 1 全集 → BPDBG 探针验证
  errata ⑧ 假说（任务 2 收口）。
