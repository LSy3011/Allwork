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
| 2026-10-13 | **上板实验包备妥（首个编译器产物上板包；备而不跑，dry-run exit=0）** | 按用户裁定「小规模、明确硬件版本和等待策略、不扩多 SMC/复杂尾块/F6 新 bitstream」：选 scale_sum n=16 统一 LCS（ADR-061 accept2 提交态复跑发布，最保守已证形态）打包——case（meta_v3.json out_bank=2/store_byte_base=20480/out_words=4 + hbm_data 8 路镜像 + 三段 IQ + 仅 smc_0 golden 镜像 VCS 单 SMC 范围）+ evidence 审计锚 + README（版本门 0x00042200/IQP1、等待策略明文=DONE 轮询 10ms/10s 但判据=HBM 64B bit-exact 数据面、停止条件、不扩大清单）；runner dry-run 全程走通 exit=0（软复位→8 路预载 stride 0x10000000→三段加载 stage_1 228 写→回读窗 0x5000→smc_1..7 按设计 SKIP）；tar+sha256 唯一命名封存；**未接触板卡**，板端执行留用户在 zwz 手动进行 | ~/u280_fpga_packages/scale_sum_ulcs_n16_v422_compiler_onboard_prepared_20261013{,.tar.gz,.tar.gz.sha256}（sha256 3cc3191a…） |
| 2026-10-13 | **注册表 v3 追加冻结 DONE（用户裁定五行表 + 独立论证先行，常数零改动）** | 独立论证 docs/registry_v3_argument_20261013.md 先行（II 独立性=RTL 文本归约：硬件无 II 寄存器、l_init/行数以编码值进入，smi_iq_iic.v:253/:828-838/:1202-1203/:841-862 + emit:159 + stage_partition:88；公式语义=三层签名互等+ordered_ast 机检；两必答项=漂移分层/分段实例相关）；v3 文件 17 条（9 携入：5 边界扩展+2 仅出处+2 逐字；8 新增=公式语义×4+漂移政策+分段规则+容量口径+已证范围），--seal 500c539e…，默认切换 v3（v1/v2 零改动存档+测试锁定）；「不冻结 n=160 唯一形状/mul 单列不入边界/不外推任意同 II 程序」逐字入记录；pytest 578 | verification_v2/tools/protocol_registry_v3.yaml；docs/registry_v3_argument_20261013.md |
| 2026-10-13 | **覆盖矩阵 II 锚点勘误（用户裁定触发）：add=II=2，mul 单列不入四算子集** | 用户核对四算子表统计对象（「II=1 对应哪个算子、哪次运行必须有锚点」）→ 正面取证：II 普查 add 解出 II=2，三份 add V1 实跑 compiler_schedule.json 全 II=2/unroll——两份步⑤文档 add=II=1 系事实性错误；II=1 唯一 VCS 证据=mul（ADR-054 阶段2-M1，2026-09-12，2048/2048 bit-exact），属前 V1 准入链证据类（且 II=1 flat 保留 ISSUE 实证债务），按裁定单列不混入；四算子 V1 链实证 II 值集={2,3,6} | docs/addcmul_step5_closure_20261013.md §2 表下勘误注（已修正）；docs/horner_v3_step5_closure_20261012.md §6 errata E1 |
| 2026-10-13 | **addcmul 步⑤：第二复杂案例五步序收口（覆盖矩阵四算子口径）** | 五步序全闭合：零生产代码、零通用规则改动（C-H 通用化跨案例效益首个正面实证）；覆盖矩阵第一档加 addcmulu 行（II=3 双槽单单元、n_max=156 VCS 校准、统一链分段 38–39 实例相关）；第二档新增 5 条（双槽单单元/结合序文本对齐法/含负单符号域/漂移下正确性稳定性/C-H 复用）；第三档新增三不外推（发射逐字节确定性/分段边界实例不变/任意多因子对齐可行）；注册表 v3 两案例素材齐备留裁定 | docs/addcmul_step5_closure_20261013.md |
| 2026-10-12 | **addcmul 步④：统一 LCS 集成零规则改动通过（C-H 通用化首个正面实证）** | n=8/n=160 统一 LCS 链双双首轮 GREEN+published（公式驱动 C-H 直接消费 x+t1*t2*value，立法序未启动）；程序段时序全录（n8 三段 ack@4107、n160 四段 ack@37870）；附随观察=统一链两进程发射实例 C（17 队列、ur-r2 span=4 束缚→分段 38+2），分段边界为求解实例相关量（38–39 两侧均 VCS 实测），「n=160 唯一分段形态」禁称；全程提交态 74cf64b 干净树 | docs/addcmul_step4_unified_lcs_20261013.md；releases/addcmulu_acu_step4_ulcs_n{8,160}_20261012 |
| 2026-10-12 | **addcmul 步③：容量 VCS 校准 n_max=156 + 分段链验证通过** | 单段 n_max=156 经 VCS 校准（rtovr-14/vin0 恰 128 行 GREEN，近壁形状跨 5 进程稳定）；n=160 分段 39+1 全 GREEN（段间 C1/C2/C3 全过、尾段写址恰 bank4 row39、stage_0 与 n=156 十八队列逐字节一致）；n=158（不整除）/n=1028（静态消耗越界）config_check 响亮早拒；预测-实测算账全闭合（构造预测保守方向）；GREEN 运行全提交态（c0ba5f3b dirty=false） | docs/addcmul_step3_capacity_20261012.md；releases/addcmulu_acu_step3_n{156,160}_20261012 |
| 2026-10-12 | **addcmul 步②：小规模计算验证通过（II=3 双槽首个 VCS 证据）+ 发射层放置漂移实证** | n=4/8/60 三规模 V1 链全 GREEN+published，逐拍 18/18 队列 ok、三区全绿、写账零非法；发射链 span=3（L_q=3(m+3)+2 五档成立），干跑实测 n=156 单段恰 128 行顶格（构造预测 152 保守已修正）；**方法学实证：放置漂移可达真实发射产物**（n=4 同配置两进程两合法变体 1/8；近壁 n=156 跨 5 进程逐字节稳定）——步①开放问题关闭，「跨进程确定性」口径收缩；生产代码零改动 | docs/addcmul_step2_smallscale_20261012.md；releases/addcmulu_acu_step2_n{4,8,60}_20261012；VARIANCE.md 当日补遗 |
| 2026-10-12 | **addcmul 第二复杂案例·步①：静态确认通过（三因子乘积结合序分歧首发实证 + U9 关闭）** | 遗留文本 ((value·t1)·t2) vs DFG ((t1·t2)·value) 结合序分歧正面鉴别（fp32 乘结合不安全；horner 全二元乘无此类）→ 处置=配置级公式文本对齐（x+t1*t2*value 三层签名互等，零代码零特判）；II=3 复现、fpmul 双槽 U9【推断】→【实测】、容量构造预测 n_max≈152（未校准禁准入）；遗留域 SUBNORMAL_BAND ×6、候选安全域双过；求解器放置漂移登记（II 稳定/放置随进程漂移，校准锚=发射产物实测）；生产代码零改动 | docs/addcmul_static_confirmation_20261012.md；artifacts/addcmul_static_20261012/（gitignored，sha256 锚） |
| 2026-10-12 | **horner_v3 步④：统一 LCS 集成通过（经一次公式化通用规则修复，按用户立法序）** | 首跑暴露 C-H 独立参考 scale_sum 硬编码（RED 留档）→ 6 项鉴别性测试 → 公式驱动修复（零名称特判，commit bd2ed7b）→ n=8/n=72 统一链全 GREEN+published；scale_sum/add 回归全 GREEN；pytest 577；提交态 n=8 复验闭合（dirty=false、包内报告一致） | docs/horner_v3_step4_unified_lcs_20261012.md；releases/horner_v3u_hv3u_step4fix_n8_committed_20261012、…_n72_20261012_codex_r2 |
| 2026-10-12 | **horner_v3 步③：自动分段验证通过（容量边界 VCS 校准 + 尾段/地址/中间状态 + 响拒两点）** | 单段 n_max=68 经 VCS 校准（七队列恰 128 行 GREEN）；n=72 分段 17+1 全 GREEN，尾段写 ADDR=17 经 payload+hex 位级双证、三路 x 流滞后 0/2/3 结构解释死读绕回、段间 C1/C2/C3 全过；n=72 stage_0 与 n=68 单段 18/18 hex 逐字节一致；n=70/n=1028 config_check 响亮早拒；生产代码零改动 | docs/horner_v3_step3_segmentation_20261012.md；releases/horner_v3u_hv3u_step3_n{68,72}_20261012 |
| 2026-10-12 | **复杂结构首证 horner_v3 步①②：静态确认 + 小规模 VCS 全 GREEN** | II=6 Horner 6 级链（fpadd/fpmul 各 3 有效槽，计算队列多槽首证）经等价目的新配置 horner_v3u 过通用准入，n=4/8/60 三规模 V1 链 GREEN+published、逐拍 18/18 队列 ok、三区全绿；有序浮点语义三层机器比对 MATCH；生产代码零改动；注册表 v3 冻结留裁定 | docs/horner_v3_static_confirmation_20261012.md；docs/horner_v3_step2_smallscale_20261012.md；releases/horner_v3u_hv3u_step2_n{4,8,60}_20261012 |
| 2026-10-10 | **ADR-061：scale_sum 统一 LOAD→COMPUTE→STORE 链路验收（五项令 #4）** | 公式链 COMPUTE + 布局驱动自动 LOAD/STORE 统一入口串通：n16/60/240 三段、n244 四段全 GREEN+published；证据限已测布局/单 SMC/TB 辅助同步/RTL 0x00042200，不支持上板或任意公式 | ADR-061 §4/§5；`releases/scale_sum_adr061_accept_n{16,60,240,244}` |
| 2026-10-10 | **F6 阶段二：错误检出/通知/FAILED 锁定定向验证通过（五项令 #2）** | TB-B 三模式（legacy 原树/重建 p1/阶段二 p2）判据 v2 全 AS-EXPECTED，fixed_p2 B1–B6 首跑全 PASS；签收=「隔离 RTL 中 IQ24–27 定向验证通过」，**不支持完整契约已修复/可上板** | F6 文档 §13；`artifacts/f6_phase2_rtl_20261010`、`f6b_smi_fixed_p2_20261010` |
| 2026-10-10 | F6 阶段一复核补丁（用户复核裁定） | 重复载荷同拍 NBA 漏洞→当拍协议异常并入完成排除；C7 定向+变异核验（移除排除项即复现）；签收措辞限定为隔离 RTL 定向验证 | F6 文档 §11.5 |
| 2026-10-10 | F6 阶段二设计冻结 + 复杂公式结构预检（五项令 #5 穿插） | E1/E1′/E2/E3/E4 设计、R-DELAY-0 审计表、判据 v2 矩阵**先冻结后跑**；horner_v3 静态预检交付 | F6 文档 §12；`docs/precheck_complex_formula_20261010.md` |
| 2026-10-09 | F6 阶段一：提前完成竞态修复（隔离副本） | TB-A C1 done 22→32=lcv+2 转绿、兼容用例逐拍一致；TB-B fixed_p1 首跑 AS-EXPECTED | F6 文档 §11；`artifacts/f6_phase1_rtl_20261009`、`f6b_smi_fixed_p1_20261009` |
| 2026-10-08 | F6 契约 v3 定稿 + RTL 冻结基线 | LSU 完成契约 v3（Q6/Q7/三边界）裁决落定；139 文件冻结清单+隔离副本建立 | F6 文档 §1–§10；`artifacts/f6_rtl_freeze_20261008` |
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
| F6：完成协议缺陷「发现—复现—修复—定向验证」闭环（问题驱动验证案例） | F6 契约 v3 + TB-A/TB-B 三模式（legacy/重建 p1/p2）+ 判据演进 v1→v2 记录 + 变异核验 | 仿真（VCS TB 定向；隔离 RTL） | 门②门③真实单元回归未在修复 RTL 上执行；不支持上板/完整契约宣称 |
| 上板闭环 | **编译器产物无**；手写线 llama r2 PASS | 上板（手写线） | 编译器上板 = 最大缺口（已降级为周期抽验，不阻塞主线） |

## 4. 遗留债务登记（随验收保留，不豁免）

- F6 门②（LDB L1/L2 真实 LOAD）/门③（M3-lite 真实 STORE）回归未在阶段二修复 RTL 上执行（五项令 #3）；STORE 外部完成缺口（F6-C）保持验证屏障，不宣称 host 闭环已解决
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

## 2026-10-08 任务 2 收口：BPDBG 实证 → master 侧合法注入「延迟→恢复→GREEN」→ F6（矩阵线）提案起草

- **BPDBG 探针实证 errata ⑧ 假说全中**：释放后 64/64 拍不动点
  `{bmc_ds2_bvalid=1, bready=0, brg_fifo_empty=0, mtx_pending=1, mtx_slv_bvalid=0, mtx_cnt_empty=1}`；
  「桥 FIFO 捕获失效」子假说证伪。死锁=矩阵 ds2 上送否决自锁（errata ⑧ 机制成立）。
  TB 指纹 `95382ceb…030b`；归档 `artifacts/m3lite_gate3_bpdbg_20261008/`。
- **us3bp n=150 首次达成「延迟→恢复→GREEN」**（合法注入点 `de_stb_bready[0]`，
  master 源头）：inject@1172 < bvalid risen@1192 < release@1321 ≤ drained@1321，
  delay=129 拍，释放后窗口矩阵 ds2 三态 pending=0 无楔死，比对 GREEN。
  此前「反压后未恢复」归因**注入点非法**（边界 force 分裂视图），非 DUT/桥缺陷；
  措辞裁定对称保持：不外推「硬件已证健壮」。n=400 腿 $finish 早于预定 release
  的假绿已分账（教训：注入窗必须短于运行自然尾部）。比对器 us3_mode 分支 +4 测试；
  pytest 530。TB 指纹 `355d48d4…52a54`；归档 `artifacts/m3lite_gate3_us3bp_20261008/`。
- **F6 指代钉死**：F6 本义 = LSU 完成竞态修复（RECON §4 A/B，IQ24/25/26/27/92
  影响面，**仍待拟**）；矩阵死锁为任务 2 遗留的另一条线。
- **F6 提案（矩阵线）已起草待裁决**：`docs/adr/F6-proposal-ds2-bresp-forward-veto.md`。
  结论：合法环境下矩阵无功能缺陷（幻影必要条件是 force 分裂视图，源码因果论证 +
  合法注入实证对照）；推荐选项 B = 不改 RTL + TB 侧常驻「不可能态」检查
  （pending&cnt_empty 持续 >16 拍即响亮失败）；选项 C（RTL fail-open）不推荐。
  四裁决问题：命名（F6/F6b）/ 选项 / 阈值与处置 / LSU 线 F6 是否立项。

## 2026-10-08 任务2收口裁决落地：RESPACCT 常驻监测 + 全链回归符合预注册期望

- **用户四问裁决（2026-10-08）**：①命名——矩阵线定名「任务2收口：DS2 写响应
  注入与监测」，**F6 专属 LSU 线**（提前完成竞态及完成契约修复）；②选项 B 批准
  带两处论证修订（删「无功能缺陷/只能由 force 制造」绝对表述；§4「pending=1 时
  无 forward、无减账」与 RTL :413 不符，重写为接收/缓存/交付/计数守恒两交付形态）；
  ③阈值 16 连续有效采样拍 `$fatal`——**诊断防抖阈值，非协议允许延迟上界**，专属
  标记 `[G3_RESPACCT][FATAL]`，编排器识别判 RED 不发布、不得被预期超时通道吸收；
  ④**LSU F6 立即立项**，先 1–2 工作日设计评审，**暂不授权改 RTL**；修复
  WAIT_DRAIN 提前采零 ≠ 自动得到 STORE 外部完成保证，设计须分四完成层次
  （分发/LOAD/STORE/软件可见）。
- **定稿文档**：`docs/adr/task2-closure-ds2-bresp-injection-monitoring.md`
  （旧 F6-proposal 文件删除）；ADR-060 errata ⑩ 回填。
- **实施**：TB 新增 RESPACCT 常驻块（复位清零/条件消失即清零/第 16 连续命中拍
  触发，先诊断行后 $fatal；指纹 `3324a33b…f7c5`）；比对器新增 respacct 专属
  判定通道 + C0 全模式「无 RESPACCT」防误报 + 超时分支防吸收断言；run.sh bp/
  to_s4 腿期望改判 respacct（**限制落账：S4BARRIER TIMEOUT 通道自此失去在套
  激发**，WBARRIER TIMEOUT 由 to_wb 继续覆盖）；pytest +5（gate3 55/55，全量
  **535**）；VCS 冒烟编译 exit=0（generate 作用域分层引用 elaborate 通过）。
- **回归（同日下午，后台链 45s）**：套件 `m3lite_g4_respacct_20261008` 总判
  **ALL-LEGS-AS-EXPECTED**——positive GREEN（red=0 warn=4）零误报；neg
  NEGATIVE-CONTROL LIVE（red=6）；**bp 与 to_s4 腿均被 RESPACCT 提前终结于
  12275000ps**（同拍确定性；旧死锁原挂 62385000ps），fatal_expected respacct
  判定双双 PASS（标记恰 2 处=TB $display+VCS echo，其后零 dump）；to_wb 腿
  WBARRIER 超时通道独立激发（9495000ps）且防吸收断言 PASS。第六腿 us3bp
  n=150 复跑 **GREEN（red=0 warn=6）零误报**，拍号与 round6 首次达成逐拍
  一致（inject1172/risen1192/release1321/drained1321，delay=129 stall=149），
  信用不对称复测同值（−7/−3）。
- **裁决预期逐条核对**：负控准确命中新报警 ✓；合法反压与正常运行不误报 ✓；
  旧死锁不再要求存活至原超时 ✓；历史证据原样冻结 ✓。
- **归档**：`verification_v2/artifacts/m3lite_gate3_respacct_20261008/`
  （README + 六腿 sim.log + 七份报告 + expect 快照 + 全链 console log，
  16 项 sha256 冻结）。
- **下一步（裁决执行顺序第二步）**：F6 LSU 完成契约设计文档（1–2 工作日
  设计评审，不改 RTL；四完成层次 + RECON §4 A/B 对照评估 + 影响面
  IQ24/25/26/27/92 与 llama r2 上板同路径 + InchiTech 第三方 IP 权属）。

## 2026-10-08 F6 LSU 完成契约设计评审稿交付（裁决④第二步，未改 RTL）

- **文档**：`docs/adr/F6-lsu-completion-contract-design.md`。按裁决④四完成
  层次立契约骨架：L0 分发完成（rdi_done_dispatch=w_prog_done，inst_queen
  :405-413/:441-442，空队列 0>=0 即 fire）/ L1 LOAD 完成（crd_ldb 结构性
  在末写后，缺口只在排空武装）/ **L2 STORE 完成（crd_stbaxi 语义上限=AXI
  受理，DUT 内无 B 对账：DW_axi_gm 响应侧 svalid/sdata/sid/slast 全悬空、
  mready 绑 1，stb_top.v:216-222；B 错误仅经 sresp→axi_err 单路径上报）** /
  L3 软件可见完成（batch_done 聚合，保证=min(L1,L2)）。
- **A/B 候选评估**：seen-nonzero 双陷阱（空队列死锁需 N=0 旁路；电平式
  武装不可区分「未升起」与「已升已落」）；计数相等双陷阱（批间稳态 0==0；
  64bit 计数器跨批自由滚动无批边界）。**共同根因=排空判定缺「本批应计
  指令数 N」输入**。
- **契约草案 C-F6**：C-ARM（N 随 dispatch-done 送出，N==0 当拍完成）+
  C-DRAIN（增量对账 (crd−base)==N，D1 快照/D2 递减两形态，评审建议 D1）+
  C-VIS（sid 配对不变量经 START 链构造保持）。语义上限钉死：**修 WAIT_DRAIN
  只把 L1 提到满额、L2 提到信用上限；不自动得 STORE 外部完成**。
- **选项表**：F6-0 不改（保底）/ **F6-A 推荐**（C-F6 武装，改动面
  inst_queen+lsu_complete+smi_top 连线）/ F6-B 不推荐（批语义渗入执行层）/
  F6-C（B 对账）建议独立立项不捆绑。
- **影响面**：五队列完成只会变晚不会变早（单调加强论证）；llama r2 上板
  同路径须板级复验决策（上板 PASS 结论不自动继承）；START 链式批污染通道
  消除属收益面但回归须新增多批链用例；手写线三处经验避险修复后是否可退役
  另行评估不得默认退役；软复位相干冲刷已核销（smi_rst_n 含 cfg_rst_pulse）。
- **评审发现落账**：R1 ir_err 与 batch_done 共享 intid 自增（中断计数
  混叠）；R2 信用超发不阻断 done；R3 w_axi_err 直连分接被注释禁用；
  R5 outstanding 2bit 饱和视图对零检测无损。
- **留用户裁决五问**：F6-A 批准与否/D1 vs D2；R1 是否配套修；F6-C 独立
  立项确认；板级复验触发条件；InchiTech 上游版本基线沟通。

## 2026-10-08 F6 五项裁决 + v2 有界契约修订（仍未授权改 RTL）

- **用户裁决（五项+两措辞修订）**：①F6-A 方向保留、D1 首选，但 v1 契约
  有**永不完成漏洞**——dispatch-done 拍取信用快照漏掉武装前已返回的信用
  （N=3 已回 2 笔则增量永远达不到 3），「已升已落天然入账」撤回；基线须
  前移到**批起点**，dispatch-done 只锁定目标数；完成=四联言（分发关闭 ∧
  应发齐 ∧ 信用齐 ∧ 无未处置错误）；N 不得直接取 `r_wr_inst_cnt`（存储
  写计数），1:1 对应仅在枚举支持形态成立。②R1 独立登记不改编码，但
  错误语义必须进 F6-A 验收（错误不得经计数相等形成成功；host 可区分
  错误/成功；错误退出与后续 START 规则须定义）。③F6-C 独立立项不捆绑；
  「DUT 内无任何 B 对账」收紧为「批完成判定链接入的现有信号中无写响应
  完成保证」。④板级复验另行裁决（提交 RTL 差异/指纹/影响路径/回归/
  构建回滚五材料），旧板端 PASS 不继承。⑤本地冻结实际 VCS RTL+依赖+
  参数+哈希、隔离副本实验；上游确认为正式合入前提。措辞修订：「完成
  只晚不早」→「绝不早于本批真实完成条件」；竞态用例旧 RED/新 GREEN，
  兼容用例允许两版皆 GREEN。
- **v2 修订稿已交付**：`docs/adr/F6-lsu-completion-contract-design.md`
  四钉死——**C-BASE**（批起点 S=START 自动弹出拍，注册脉冲每批恰一次；
  cru/crd 双基线快照；首笔信用下界 ≥S+3；批起点不变量 crd==cru 防御
  检查+响亮拒绝；软复位相干归属）·**C-N**（单位审计表：M3-lite LSU
  描述符行实测 LDB 2/2、STB 1/1 成立；VLD=0/LOOPS>0/INIT/TERM 混入=
  排除形态；EXTRA_DATA 范围外；核心假设 A-credit「每个 inst_valid 恰一
  信用」可证伪、内建 id 自校兜底；N=inst_amount 于 dispatch-done 同拍
  采样=R9 窗口）·**C-DRAIN**（四联言对注册值求值；同拍归属 t≥S；第 N
  笔信用 t+1 可见 ⇒ done 绝不早于真实完成；64bit 模减回绕非问题；
  N==0 当拍完成=时序变更点按兼容用例验收）·**C-ERR**（E1 err_sticky
  阻断计数成功/E2 单元错误位接入重映射/E3 int_err 置位+done_r 保持 0/
  E4 FAILED 退出+START 冲刷+软复位恢复/E5 进验收）。
- **评审新发现（v2 回源取证）**：R6 int_err 不被 ir_err 置位（仅队列
  溢出置位）→错误中断与成功不可区分；R7 信用单元错误位（7bit bit6）
  在 smi_top :494-497 重映射被丢弃，ir_err 仅反映 dly_queen 簿记自检；
  R8 错误后 glue_busy 不清→批楔死、START 停摆至 cfg_rst；R9
  r_wr_inst_cnt 于 dispatch-done 同拍清零→N 采样窗口=同拍。
- **留裁决**：Q6 错误时 intid 是否停止自增（建议停）；Q7 N 排除形态
  走准入门拒绝还是契约内挂死（建议准入门）。
- 矩阵线按裁决维持限定范围收口，不再追加工作。

## 2026-10-08 F6 第三轮裁决：Q6/Q7 定案 + 三条时序边界 → v3 契约（边界先落契约与测试，仍不改功能 RTL）

- **第三轮裁决全文落实**（用户对 v2 的五点回覆）：**「同意继续采用 D1
  双基线方案，进入定向测试与隔离实验实现的准备；上述边界先落入契约
  和测试，再改功能 RTL。无需再扩展一轮大方案。」** F6-A 目标重申限定
  = **本批发射与信用正确对账**；STORE 外部完成仍属 F6-C。
- **Q6 定案**：错误时 intid **继续自增**（v2「停自增」建议被否——
  `intr_ro=(intid_hw_r != intid_sw_r)`，停自增将饿死等中断的 host）。
  最小方案入 E3：intid=通知事件序号、同一失败沿触发只通知一次、
  int_err 置位 + done_r 保持 0、同拍错误优先不登记成功、明文
  「intid 增加≠一批成功完成」、int_q_full（R11：8bit 通知序号回卷
  保护 :118/:122，满时 :282 置 int_err 抑制自增）入测试 T-INTFULL。
- **Q7 定案**：**准入拒绝**（响亮挂死不缔约）；检查覆盖**最终指令包
  一切入口**（非仅 case_gen），拒绝报告队列/行/字段/原因；**IQ92
  限制修复范围**——信用源 `u_smc.ds_crd_ds_o`（6bit 链无错误位，
  R12）未审计，不得据四队列证据宣称五队列支持，IQ92 实例保留旧
  行为（per-instance 使能）。
- **三条时序边界落入契约**：
  ① 同拍错误双排除（err_sticky ∧ err_detect_now）+ 错误源流水线
  延迟审计表（实施准备必做）+ T-ERRSYN/T-ERRLATE；
  ② FAILED 持续锁存（排队与新写 START 均封锁，不靠 host 自律）+
  **新结构性发现 R10：cfg_rst 不触达信用源**（u_smc 仅 rst_n，
  aiacc_top_f.v:1916-1918；o_cfg_rst 上行 TB 悬空）——「cfg_rst 后
  必然安全恢复」不得写成已证结论，恢复路径 (a) 实测静默窗 /(b) 信用
  源门控待择，T-RSTLATE 先行且两案测试相同；
  ③ N+sid 单载荷 `{dispatch_closed_valid, batch_sid, N}`（生产者
  inst_queen 在 rdi_done 脉冲拍的清零同 posedge 注册，NBA 读清前值；
  消费者 lsu_complete 单拍捕获），附正常批/空批/连续不同 sid +
  S 快照四张逐拍表；基线快照精化为**静默窗 [S,S+3) 内任一拍**。
- **首批验证优先级**（裁决原文）落 §9.0：T-PREARM（武装前信用已
  返回，旧 RED/新 GREEN）/ T-EMPTY、T-LASTCRD、T-ERRSYN /
  T-FAILLOCK / T-RSTLATE / T-INTFULL。
- **冻结基线落地**（第二轮裁决 5）：`verification_v2/artifacts/
  f6_rtl_freeze_20261008/`（README + RTL_FREEZE_MANIFEST.txt 79 行
  sha256+size+mtime + SHA256SUMS）；TB 指纹 3324a33b…f7c5 与
  RESPACCT 回归归档一致（未漂移）；smi/+data_engine/ RTL mtime 全为
  2026-07-21（原始树自备份以来未改）；编译器仓库侧脚本以 git commit
  记账不入清单。隔离副本在实施启动时 cp -a 建立并以清单校验。
- **v3 文档**：`docs/adr/F6-lsu-completion-contract-design.md`（全量
  重写为 v3；新增 R10/R11/R12；§10 六问全部落定，剩四项实施准备项：
  错误延迟审计表/恢复路径择定/隔离副本/DS 审计[仅当未来主张 IQ92]）。

## 2026-10-09 F6 四步执行令：步①②③完成 + 步④ TB-A legacy 基线 AS-EXPECTED

- **执行令（2026-10-08 用户原文）**：①错误源延迟审计（错误不晚于成功
  判定被识别，覆盖末笔信用与错误同拍）②固定恢复边界（FAILED 持续锁定；
  cfg_rst 不得单独解锁；未证明的静默等待/信用屏蔽不作恢复依据；
  T-RSTLATE 验证迟到信用不触发新批/假成功）③可恢复隔离副本（实际
  源码+编译依赖+哈希，不只清单；mtime 不作历史未修改证明）④定向测试
  （先复现原始竞态，再覆盖空批/末信用/同拍错误/FAILED 锁定/通知队列满；
  快照选定明确采样拍）。范围 IQ24–27；IQ92/STORE 外部完成/通用软恢复
  不纳入；准备完成后才进隔离副本最小 RTL 修复。
- **步① DONE**：§4.3 审计表——枚举错误源全部与信用计数同块同拍注册
  （dly_queen :184-202 同 always 块同 `if(w_crd_inst_done)`，相对延迟 0）；
  设计规则 **R-DELAY-0** 钉死后续接入；附注 source-2 的 :193 预增量比较
  存在 1/16 同 id 迟到信用逃逸窗（非安全机制，已记录）。
- **步② DONE**：§4.5 E4 重写——failed_latch 双置位（err_sticky 上升沿
  ∨ 在飞期间 cfg_rst），**仅 rst_n 解锁**（rst_n 触达信用源 R10；
  cfg_rst 空闲期=合法配置操作）；静默等待/信用屏蔽明确排除；
  T-RSTLATE 重写为三子项（在飞 cfg_rst 封锁 / 迟到信用无武装无联言
  无 done / rst_n 后新批 GREEN）。
- **步③ DONE**：冻结清单扩围修正 79→139 文件（首版漏 smc/ 编译依赖，
  README 记作废）；隔离副本 `/home/juno1023/u280_sim_v422_f6a_20261008/`
  建立并 **139/139 逐哈希校验**（ISOLATE_VERIFY.log ALL-OK）；mtime 降格
  为元数据，唯一历史正面证据=lsu_complete 与 .pre_sync_reset 备份
  **sha256 逐字节一致**（40b4573d…149c，新增 R13）；冻结目录 SHA256SUMS
  重封（README/manifest v2/ISOLATE_VERIFY）。
- **步④ TB-A DONE（前半）**：`verification_v2/f6a_unit/`（契约级单元 TB +
  legacy 包装器 + 分离裁决器 f6a_unit_check.py + 运行器，DUT sha256 打入
  日志）。**legacy 基线 overall=AS-EXPECTED**：C1 原始竞态复现
  （done−末信用可见=−8，dispatch 落 outstanding==0 空窗即放行）、
  C5 同拍错误漏放复现（err 145 可见后 146 仍放行 done，旧模块无错误
  输入）、C2/C3/C4/C6 PASS。快照采样拍钉死 SAMP=S+1（§4.1，删除
  「静默窗内任一拍」歧义）；断言一律相对拍差，==仅约束 fixed、legacy 按
  >=判「不早于真实完成」。TB 自身两缺陷已修并记录（复位沉降窗监测器
  重捕获滞留 err_sticky；可见拍打印 +1 偏差统一为 lc+2）。
  归档 `verification_v2/artifacts/f6a_unit_legacy_baseline_20261009/`
  （事实日志+裁决输出+7 项 sha256）。
- **步④ TB-B DONE（后半，同日完成）**：`verification_v2/f6b_smi/`
  （DUT=隔离副本内整个 smi_top 壳；host 全走真实 AXI；信用在 u_smc
  边界端口 `i_crd_stb` 注入并逐笔 `[F6B_INJ]` 记录；分离裁决器；
  运行器分档退出码 0-7 + 每轮全新 work 目录 + legacy 五文件哈希守卫
  对齐冻结清单）。**legacy 基线 overall=AS-EXPECTED**（B1-B4 预期
  首跑前冻结、B5 于首跑前冻结）：
  B1 T-FAILLOCK VIOLATION 三违约全命中（R6 实证 err 仅 intid+1 不置
  int_err；cfg_rst 清锁后新批照样发射=iss_postcfgrst 1；1 指令批零真
  信用竞态完成 eid=2）；
  B2 T-INTFULL PASS（R11 回卷保护完好 50→255→int_err+intr）；
  B3 T-RSTLATE 字面序 PASS（诚实注记：旧信用先于新 START 时 legacy
  为 executing_inst_cnt 0→7 下溢静默楔死，与锁定 host 面不可区分，
  鉴别归 B4/B5）；
  B4 1 指令批竞态抢先完成（intr@3374 早于旧信用@3387 十三拍）；
  **B5 真·归因假成功实证**（C=2 指令防竞态抢跑，两笔 cfg_rst 前批次
  旧信用 id=0/1 凑足 cru=2，intr@3656 晚于第二笔@3652 四拍——R10
  危害在集成壳内复现）。
  普遍观察：legacy 上一切 N=1 批完成皆为竞态完成（对照批 D 的 intr
  亦早于其真信用 2 拍），与 TB-A C1 互证，直接支撑 C-N 计数要求。
  TB 自身缺陷修复记录：negedge 采样幻影握手（DW_axi_gs aselect 组合
  抬起 ready 骗过电平等待 → 改 posedge 接受计数器，写通道此前能用仅
  相位侥幸）、CSR 读协议 arsize=2、eid 字段勘定 w[8:7]（首轮 B3
  SURPRISE 全部来源=TB bug）、捕获器取存须在 hard_reset 前。
  悬空 ir_*_rdy 已登记不补绑（iss0=3 实证本配置不触达）。
  归档 `verification_v2/artifacts/f6b_smi_legacy_baseline_20261009/`
  （实测报告+sim/check/compile 三日志+12 项 sha256）。
- **执行令步④至此全部完成**。其后：隔离副本内最小 RTL 修复
  （不重启大方案讨论；范围 IQ24-27；IQ92/STORE 外部完成/通用软恢复
  不纳入）。
- **只读核验 DONE（2026-10-09，用户只读核验令步①-④；原树与冻结
  基线全程未动，未改任何 RTL）**：
  步① 版本确认 4.22（PARAM_RTL_VERSION=0x00042200）；完成链结构
  证实空窗（lsu_complete 45 行通读：不持 N、不做信用对账，WAIT_DRAIN
  首采样 outstanding==0 即完成）。
  步②「以前为什么通过」：mul_scalar stage_2 STB/STBAXI（IQ24/25）均
  INST_LINES=1（N=1）→ 板上同为竞态完成（RTL 同源强推断非直接观测）；
  通过原因=host 时标遮蔽（poll 10ms/DMA ms 级 vs 竞态窗口 ns 级，
  5-6 个数量级余量）+ runner 从不检查提前 done（唯一交叉检查
  int_delta≠1 为非致命 WARN，竞态完成恰好 intid+1 连 WARN 都不触发）。
  步③ C1 逐拍复核：TB 语义核验六项全过（Δ1/Δ2 诚实注记均惰性）；
  复现逐字段一致（done@22 vs 末信用可见@30，−8 拍）；决定拍铁证
  （done 置位 posedge 上 cru=1∧crd=1，指令 2/3 尚未发射）；NBA 采样
  推论：同拍注册不救 ⇒ N=1 批结构必然竞态。新增 trace TB
  `f6a_unit/tb_f6a_c1_trace.v`（归档 TB 全部未动），归档
  `artifacts/f6a_c1_trace_20261009/`（5 项 sha256）。
  步④ 三分类落账于 F6 设计文档 §10-6：已证实旧实现缺陷（提前完成
  竞态+N=1 结构必然性）/ C-F6 新增要求（C-N/C-DRAIN/C-PAYLOAD/C-ERR/
  E4/SAMP=S+1/R6 记录）/ 尚待确认（错误处理与复位锁定进一步核验待
  用户裁决；IQ92/STORE 外部完成范围外；板上直接观测需探针未做）。
  最小修改范围初判：仅提前完成一项=F6-A 核心子集（C-BASE+C-N+
  C-DRAIN+C-PAYLOAD；C-ERR/E4 可后置），拆分实施待裁决。
- **第四轮裁决落账（2026-10-09，当前管辖）**：先补齐错误处理与复位
  锁定的只读核验（错误来源及延迟/同拍错误与 done 优先级/cfg_rst 清理
  范围/在飞状态判断/rst_n 是否覆盖旧信用来源，继续区分旧行为与新增
  契约要求）；随后分两阶段实现——阶段一修提前完成（计数对账+批次
  载荷+规定采样时序，仅签收「提前完成修复」，不宣称 TB-A/TB-B fixed
  全 PASS），阶段二补齐 E1-E5+R-DELAY-0+恢复边界后执行完整 fixed
  验收与既定回归。**最终按完整 C-F6 v3.1 契约验收，拆分不降低验收
  要求**；阶段一接口设计须容纳后续错误抑制与锁定。三处收紧已同步
  修订（F6 文档 §10-6/裁决记录 7、trace 归档 README 重封、本条）：
  ①「N=1 竞态」限已核实时序与初始状态、不推广未核实路径；
  ②host 毫秒级等待仅解释旧测试未暴露，无板上直接时序证据不断言
  「真实写入必然完成、因果无害」；③不提前断言改动全部只在
  lsu_complete 内（修复需接入发射数/信用/批次信息）。不重启大方案
  讨论、不必每步等批准；原树/冻结基线/IQ92/STORE 外部完成不动。
- **只读核验② DONE（2026-10-09，第四轮裁决第 1 项：错误处理与复位
  锁定；纯源码核验无新仿真；引用文件 8/8 哈希复核=冻结清单）**，五问
  全部直接读码闭环、落 F6 文档 §10-7：
  Q1 错误来源=dly_queen err（信用同拍注册，R-DELAY-0 成立；粘性）→
  ir_err OR → 边沿检测 → 仅 intid+1（2 posedge），int_err 不置位（B1
  源码级印证 R6）；**源码观察①=检出 off-by-one（预增 crd>cru，首笔
  越界信用不触发，与 B5 静默吸收一致）**；**源码观察②=粘性 err+OR
  边沿 ⇒ 后续错误（含跨队列）复位前静默**；IQ0-23/28-66/桥/93 err 与
  error_inst_queen 全为死路（正面取证）。
  Q2 同拍 err+done：单 intid 自增入口、done 照常置位、无优先级无排除
  ——host 面与干净成功不可区分（单元级 C5+系统级 ctrl_regs :281-297）。
  Q3 cfg_rst=ctrl_regs 全可变状态清零+smi_rst_n 单拍扇出全部 IQ/
  lsu_complete/dispatcher/packer/ds_t（dly_queen cru/crd/executing/err
  全清→粘性 err 清后通知重新武装）；不触达 u_axi_wrapper/DW_axi_gs
  （raw rst_n）与 u_smc 信用源（R10；o_cfg_rst 唯一消费者为 TB，全树
  grep 正面取证）。
  Q4 host 可读全集=CFG/INT/IQ_EN×4/START/DONE/RTL_VERSION，**无在飞/
  outstanding/credit 可见性**；glue_busy 等内部态无 host 通道。
  Q5 rst_n 覆盖信用源逐跳直接读码：u_smc(:1916-1918 唯一 rst_n)→
  u_stb→stb_ur_axi_conv GEN_CRD_STB_O :804/GEN_CRD_STBAXI_O :1161+
  ldb_ur_axi_conv :1482/:1000 四路信用寄存器复位分支全直接读到——
  **rst_n 后无旧信用滞留（E4 rst_n 唯一解锁自洽）；cfg_rst 不触达
  （R10）危害已由 B5 实证**。DS（IQ92）链未审计（R12 范围外保留）。
  三分类追加：旧行为登记 6 条/C-F6 新增要求归阶段二/本轮无新增尚待
  确认源码问题。**下一步=阶段一实现（隔离副本内修提前完成：计数对账
  +批次载荷+规定采样时序，接口须容纳后续错误抑制与锁定；仅签收
  「提前完成修复」）**。
- **F6 阶段一实现 DONE（2026-10-09，用户阶段一令：只验收「提前完成修复」，
  错误抑制/通知/FAILED 锁定留阶段二）**，隔离副本 6 文件（全树 diff 正面
  证据仅此：新增 smi_iq_lsu_complete_f6.v=SAMP 快照基线+C-PAYLOAD 载荷
  捕获+C-DRAIN 计数对账完成条件；dly_queen/inst_queen 纯暴露与载荷寄存；
  smi_iq/smi_top 布线+IQ24-27 换实例；IQ92 与 smi_iq_lsu_complete.v
  40b4573d…149c 冻结不动）。
  **TB-A fixed AS-EXPECTED**：C1 竞态转绿（done 22→32=末信用可见+2，NBA
  最紧合法拍）；兼容 C2/C3/C4/C6 与 legacy 归档逐拍 bit 一致；C5 同拍错误
  转阻断。钉拍勘误已记录（判据翻译偏差一拍，三重独立证明：契约 §4.3+归档
  trace、NBA 不可能性、legacy 实测 2/3/2/2/2；期望矩阵不变）。
  **TB-B fixed_p1 首跑 AS-EXPECTED**（对照首跑前冻结矩阵，fail 清单逐项
  与推导一致）：B1 doneC 1→0/eidC 2→0/intid_prerstn 1→0（壳内竞态修复+
  无假完成通知），遗留恰 interr_err（R6/E3）+iss_postcfgrst（E4）两
  阶段二项；B4 intr 3374→3392 从早于旧信用 13 拍移至其后+5（机制：竞态
  →计数闸住的归因假成功，E4 遗留）；B5 与 legacy 逐字段 bit 一致（本就
  归因形态）；B2 bit 一致（ctrl_regs 内部退化路径不触达）；B3 判据字段
  不变、信息项 intr_cyc +6（D 恢复批竞态→真信用完成）。
  **归档三件套已封**（SHA256SUMS 0 non-OK）：f6_phase1_rtl_20261009
  （diffs/指纹/范围证据）、f6a_unit_fixed_p1_20261009、
  f6b_smi_fixed_p1_20261009；F6 文档 §11 落账。
  **阶段二 RED 清单（不得计作 fixed 通过）**：E3/R6 int_err 不置位；E4
  FAILED 锁存（挡新 START+归因假成功）；R10 系统恢复未证；crd>=cru 同拍
  判据定向测试；R-DELAY-0 布线/off-by-one/executing 楔死未动。
  **下一步=阶段二**（E1-E5+R-DELAY-0+恢复边界，然后完整 fixed 验收
  TB-A/TB-B 全 PASS + 既定回归）。
- **F6 阶段一复核补丁 DONE（2026-10-10，用户 diff 复核发现同拍漏洞→修复+
  定向测试+复跑，签收候选）**：漏洞=原实现「重复载荷→arm_err<=1」与完成
  判定同拍求值的 NBA 竞态（重复载荷恰逢计数达标时读 arm_err 旧值 0，同时
  记异常又发 done）。修复=仅 smi_iq_lsu_complete_f6.v 一文件：
  w_payload_anom_now=dispatch_closed_valid&&dispatch_closed 当拍显式并入
  完成排除（协议异常明确优先），862faaf9…→a84d82d3…。
  **定向测试 C7（判据首跑前冻结）**：重复载荷窗连开 3 拍覆盖完成判定首次
  通过 posedge 两种口径+恢复批正控；**变异核验牙齿实证**（移除排除项的
  变异 DUT 上 done1=218 漏洞原样复现 MISMATCH；补丁 DUT done1=−1 阻断∧
  恢复批 done2=239/d2=2 不误锁死）。复跑全绿零意外回归：TB-A fixed 七用例
  AS-EXPECTED（C1-C6 与 2026-10-09 归档逐拍一致）；TB-A legacy AS-EXPECTED
  （C7=VIOLATION 复现，DUT 40b4573d 冻结）；TB-B fixed_p1 AS-EXPECTED 且
  **FACT/INJ 与 2026-10-09 归档逐行 bit 一致**（壳级不触发该路径）；
  run_f6b.sh provenance 段补记修复模块哈希。
  **措辞勘误两条落实**（live 文档；2026-10-09 归档 README 封存不改）：
  ①「永不完成=响亮失败」→「阻止完成；模块无 err 输出端口，对外只能由
  host/外部超时机制发现」；②「物理最紧」→「在当前注册级数、接口与采样
  约定下的最早合法拍」。F6 文档 §11.5 落账。
  **归档 2026-10-10 三件套已封**（SHA256SUMS 0 non-OK）：
  f6_phase1_rtl_20261010（含评审态→补丁态 diff，评审态从已封归档重建
  哈希逐字节一致 862faaf9）、f6a_unit_fixed_p1_20261010（含变异证据）、
  f6b_smi_fixed_p1_20261010。
  **签收措辞（用户裁定）**：「隔离 RTL 中，IQ24–27 在已验证合法形态和
  正常批次边界下，提前完成竞态修复通过定向验证」——不支持「完整 LSU
  完成契约已修复」或「可替换原版上板」。
  **下一步=F6 阶段二（预算 2-3 工作日检查点，范围锁死 IQ24-27：错误及时
  检出传播+通知一次+FAILED 锁定挡排队/新写 START+cfg_rst 不单独解锁；
  不含通用软恢复/IQ92/STORE B 对账）→ 真实单元回归（LDB L1/L2、M3-lite
  正负控）→ scale_sum 统一入口自动完整案例 → 复杂结构案例（静态预检已
  并行启动）**；STORE 最终完成保留现有验证屏障，不宣称 host 闭环已解决。
- **F6 缺口修复轮 DONE（2026-10-10，第四轮复核裁定四缺口关闭）**：裁定
  先将阶段二口径降级为「所列 TB 用例通过」（§13.5 errata），登记四缺口；
  本轮全部定向关闭——#2 控制层错误同拍优先（done 登记包 `!lsu_err_any`，
  批账务照常退役）、#3 通知遮蔽（lsu_err 1-bit OR→4-bit 逐源向量+逐源
  边沿检出+lsu_err_evt 独立通知项）、#4a 协议异常上报（F6 模块
  `proto_err_o=arm_err` 逐源并入，触发 E3+E4）、#4b crd>=cru 同拍边界
  （信用源因果链行号钉死审计：conv 本地数组完成→链尾常数 0 无幻影注入
  ⇒ 合法信用不误报；审计级非形式证明）。**判据 v3**（B1–B6 同 v2 + 新增
  B7 T-ERRPRIO/B8 T-ERRMASK/B9 T-PROTOERR）先冻结后跑；**B7 激励首跑
  未达真同拍**（接受拍间距 +2 实测 err 晚 1 拍注册）→ 修正融合任务
  credit24_26_aligned（间距 +1，推导与绝对延迟无关）后复跑：fixed_p2
  （=p2b）**B1–B9 全 PASS**，legacy/fixed_p1 对照矩阵不变、B9 SKIP，
  三模式 exit 0；TB-A fixed 7/7 含 C7 扩展字段 proto=218（#4a 产生机制
  端证据）。附带登记：0x004[2] FAILED 软件可见接口扩展语义与版本兼容
  （PARAM_RTL_VERSION 未升，§14.3）；**运行器硬化**=run_f6a_unit.sh 补
  rm -rf+tee+PIPESTATUS（归档前发现 10-09 旧 check.txt 滞留，差一刻误
  归档）。p2→p2b 改动面恰 3 文件（重建 p2 态哈希逐项恰等 p2 归档指纹
  后 diff；未触 4 文件不变式对账；累计仍恰 7 文件）。**归档三件套**：
  f6_phase2b_rtl_20261010、f6b_smi_fixed_p2b_20261010（判据 v3 三模式+
  TB-A 连续性）；F6 文档 §14 落账。**签收口径**：四缺口关闭证据齐；
  是否恢复「E1–E5 全闭合」留用户裁定；仍不支持「完整契约已修复/可上板」。
  **下一步=第③项真实单元回归**（全树沙箱已按裁定 5 步授权同步准备：
  u280_sim_v422_f6full_20261010 rsync 完；待 overlay 7 文件+白名单核验+
  路径审计+独立构建后按 LDB L1→L2→M3-lite 顺序）。

- **2026-10-10｜F6 真实单元回归 DONE（五项令 #3，复核裁定步⑤）**：
  p2b 全树沙箱（overlay 7 文件白名单核验通过、TB 3324a33b 未漂移、
  工具链 M3_SBX 全转发）。LDB L1/L2 compare exit=0——**门② B5c 反例
  关闭**（stock「通知≺写 31 拍」→ p2b 末写严格先于通知 gap=+5 ×2）；
  M3-lite 五腿 ALL-LEGS-AS-EXPECTED 且与 stock 逐拍一致（RESPACCT
  @12275000ps、to_wb @9495000ps、bp red=22、负控 red=6 全同值）；
  第六腿 us3bp n=150 GREEN——注入链逐拍一致（inject1172<risen1192<
  release1321≤drained1321，delay=129），**STORE 完成时序修正显形**：
  stage_2 INTR stock@1159≺真信用@1184/1188（legacy 竞态真实路径实况）
  → p2b@1191=末信用+3（契约时序），数据面零代价。归档
  f6_realunit_regression_20261010（29 项 sha256）；F6 文档 §15 落账。
  **签收**：信用注入结论获真实 LOAD/STORE 路径验证成立；E1–E5 全闭合
  口径仍留用户裁定；不支持上板。**下一步=第④项**（scale_sum 统一入口
  自动完整 LOAD→COMPUTE→STORE）与第⑤项静态预检接续。

## 2026-10-10｜ADR-061：scale_sum 统一 LOAD→COMPUTE→STORE 链路验收（五项令 #4）

> 里程碑：此前分别成立的公式编译、自动分段和搬运验证，已经通过统一入口串起来。

- 统一入口完成公式编译、搬运段组合、VCS 验证、裁决与发布
  （`verification_v2/tools/run_unified_lcs_verify.py` + 四件套
  unified_lcs_case_gen / check_program_manifest / unified_lcs_compare /
  arbitrate_program；ADR-061 §3 D1–D8）。
- n=16/60/240：LOAD→COMPUTE→STORE 三段程序通过
  （run_id=adr061_accept_n{16,60,240}，均 GREEN+published；C-L
  首读>release>末写 = 3496>442>367 / 9540>480>411 / 34726>1096>1023；
  run 目录 `build/v1_runs/scale_sum/adr061_accept_n{16,60,240}/`，发布包
  `verification_v2/artifacts/releases/scale_sum_adr061_accept_n{16,60,240}/`）。
- n=244：LOAD→COMPUTE₀→COMPUTE₁→STORE 四段程序通过，计算跨段地址与写回
  检查通过（run_id=adr061_accept_n244；LOAD 244/244、STORE 61/61、
  C1 34868<34934 与 36374<36430、S4@36898>末写@36822、WBARRIER@36368∈
  [34934,36430] 跨段累积掩码实证、Z3 双源计数 305=61+244）。
- n=480：ADR-057 既有计算分段链回归通过
  （run_id=adr057_regress_n480_20261010），**不计作本轮完整 LCS 链通过**。
- add、scale_sum 四配置回归 GREEN（add_frozen_regression /
  add_v1_admission / scale_sum_v1_n4n8n60 / scale_sum_v1_n240_boundary）；
  pytest 566 passed。
- 证据范围：当前已测试布局、单 SMC、TB 辅助同步和指定 RTL 版本（发布包
  provenance 逐项核验：tb_sha256=3324a33b…、sync_model=tb_assisted、
  rtl_version=0x00042200）；不代表上板通过或任意公式均支持。
- 保留 alpha stride=0、同拍 release 和探针格式等限制，逐项链接
  ADR-061 §5 与对应运行报告
  （`releases/scale_sum_adr061_accept_n*/run_report.json`）。
- C-L 同拍裁定落地（用户 2026-10-10 三条规则）：首读<末写→RED；
  末写==首读→RED（不降级——无存储端口语义或更精采样证据）；首读≤release
  →RED；release>BEGIN₁→RED；release==BEGIN₁→仅 limitation（TB 零延迟
  构造性同拍，次序证据由①③严格大于独立承载）——ADR-061 §5 比较矩阵 +
  `tests/unit/test_unified_lcs.py` 33 项含边界双向钉死。
- 已知证据打包缺口：program_manifest.json / g3_expect.json 未拷入 run
  evidence（文件在沙箱 case 目录，sha256 锚在 run_report provenance）；
  copy2 补丁已拟定但因分类器故障未完成复测——本轮提交保持与四轮验收实际
  运行代码逐字节一致，补丁+复跑验证列为后续任务（ADR-061 §5 末条）。
- 上板实验包状态单列：`/home/juno1023/m3lite_w16_fpga_20261010.tar.gz`
  （sha256=3d59949f…fd73）+ `artifacts/m3lite_w16_fpga_pkg_20261010/`，
  不能由统一链验收推定已完成。

## 2026-10-12｜收口复核三点裁定落地：C-L 有界核查 + D5 归档关闭 + E1–E5 对账

- **C-L 多 release 有界核查（裁定①）**：用户指出 `min(releases)` 在多
  release 同为首读前置时漏放。正面核验四次验收运行：g3_credit 实测
  **每段过渡各释放一次**（n=16 [442,3582]、n=60 [480,9648]、n=240
  [1096,34924]、n=244 [1096,34934,36430]）；首读均在 stage_1 窗内、
  前置集恰 1 元——单 release 分支成立，原 GREEN 判决有效。漏洞形态
  仍然真实存在（首读与后段入口 release 同拍时 min 规则放行）→
  仲裁器改为按段匹配（release 归于首个 begins[k]≥r 的段入口；前置集
  P={r≤begins[j_fr]}，首读须 > max(P)，含同拍从严；release 越末段
  BEGIN 不属任何入口 → RED），新增 3 项合成钉死测试（36+15 全过）。
  commit `e76592b`。
- **D5 归档缺口关闭（裁定②四步全执行）**：①沙箱原始 manifest/g3_expect
  已保存至 `artifacts/adr061_manifest_recovery_20261012/`（manifest
  sha256 对锚四次全 MATCH；g3_expect 与 tb_plusargs 11 字段互证
  CONSISTENT；旧 run 目录/发布包零改动）；②copy2+拷后哈希复核补丁落地
  （不符即 StageFailure）；③四规模复跑 adr061_accept2_n{16,60,240,244}
  全 GREEN+published，run 包自包含（两文件入 evidence 且计入 run 级
  SHA256SUMS），拍号与首轮逐拍一致，新 C-L 规则下判决不变；④单独提交，
  旧证据未覆盖。
- **E1–E5 文档对账（裁定③）**：F6 文档新增 §16 证据对账矩阵（实现/
  用例/结果/限制逐项照录已冻结归档，不重复仿真）。**不构成全闭合宣布**；
  恢复能力不扩大到通用软恢复或上板。commit `3657c59`。
- 里程碑性质更新：ADR-061 由「功能验收通过」升为「功能验收通过 +
  交付证据完整（accept2 起自包含）」。

## 2026-10-12｜复杂结构首证 horner_v3：步①静态确认 + 步②小规模 VCS 全 GREEN

- **用户令（2026-10-12）**：ADR-061 按「功能验收＋交付证据完整（accept2 起）」
  收口；E1–E5 登记为「在已列明范围与用例内完成实现、测试和证据对账」（不简写
  为全闭合）。下一执行目标 = **优先核查 horner_v3 作复杂结构首证**（五步表：
  静态确认→小规模计算验证→自动分段验证→统一 LCS 集成→收口）；使用前确认有序
  浮点语义；配置不满足通用入口则新增等价目的配置、不走手写 lowering；**禁名称
  特判**，规则改动须先记失败形态+有鉴别力测试+回归 add/scale_sum。
- **步① 静态确认**（docs/horner_v3_static_confirmation_20261012.md +
  tools/horner_v3_static_probe.py + artifacts/horner_v3_static_20261012/）：
  有序语义三层机器比对全 MATCH（文本有序 AST ↔ SymPy 规范化树 ↔ DFG 计算树，
  交换律层规范化签名）；II=6 复现 census；**fpadd[0,2,4]/fpmul[1,3,5] 三有效槽
  = 计算队列多槽首证**（add/scale_sum 恒单槽）；rtovr-14/vin0 满槽 6/6；
  span_q 定义钉死 assembler.py:218/395-396 原式，scale_sum 对照 L(240)=128
  PASS。两个新发现：①动态域闸拒遗留单值参数（0.1/-0.2/0.3/-0.4 非 fp32 恰可表，
  4 项 OUT_OF_DECLARED_RANGE；预检只跑静态道未覆盖）；②统一入口 case-gen
  bank 交叠闸拒 c0..c3 共 bank1。
- **步② 小规模计算验证**（docs/horner_v3_step2_smallscale_20261012.md）：
  等价目的新配置 `horner_v3u.yaml`（公式逐字不变；参数分置 bank1/3/4/5 +
  恰可表值 0.125/-0.25/0.375/-0.5；flag+length），守卫测试名单随配置更新
  （语料 38→39）。准入干跑：n=4..68 单段全过、**n=68 七队列恰 128 行顶格**、
  n=72 分段器 17+1（门级证据，容量 VCS 校准归步③）。真实 VCS：**n=4/8/60
  三规模全 GREEN+published**，逐拍仲裁 18/18 队列 ok（fpadd issue
  39/45/123 = ISSUE_TOTAL 公式逐项吻合，TERM +1 拍勘误在 II=6 成立）、
  三区全 GREEN、写账零非法——**II=6 形态一轮通过，零生产代码改动**。
- **治理待裁定**：注册表 v2 边界表述覆盖 II=2 已证形态；本次 II=6 三规模
  GREEN 计为探索证据，v3 追加冻结（含 II 独立性论证要点）留用户裁定，
  冻结前不宣称该形态协议模型正式已证边界扩张。
- 回归：pytest 571 passed / 7 skipped / 11 xfailed / 148 subtests 全绿；
  生产代码零改动（diff = 新 yaml + 守卫名单 + 探针/文档）。commits：
  `610009e`（步①）+ 当次（步②）。

## 2026-10-12｜horner_v3 步③：自动分段验证通过

- **容量边界 VCS 校准**：步①构造预测 n_max=68 升级为 VCS 校准——n=68 单段
  七队列（fpadd/rtovr-3/4/5/6/14/vin0）恰 128 行 GREEN；n=72 由 ADR-057
  分段器切 17+1 两段 GREEN；n=1024（bank 恰满 256 行）干跑准入 16 段。
- **尾段**：stage_1 恰覆盖迭代 [17,18)，L=32=6·(1+4)+2，issue 计数与 n=4
  同型；写值三区 bit-exact。
- **地址（payload+hex 位级双证）**：y 写两段 0..16/{17} 连续不相交；
  三路 x 流滞后 0/2/3（mul1/mul2/mul3 各一份 x），stage_1 活读全 ADDR=17；
  死读（prologue 模 256 绕回 253..255 / epilogue 18..20）由活性排除，
  每流读数恰 R_q=span+m；UR_WRITE row3 位级解码 BANK=2/ADDR=17/WBE=0xFFFF。
- **中间状态**：C1 次序/C2 零残留（同拍空窗 errata 沿用登记）/C3 写集相等
  全过；**跨案例一致性：n=72 stage_0 ≡ n=68 单段，18/18 hex sha256 相同**。
- **响亮拒绝**：n=70（不整除 4）与 n=1028（257>256 行）均 RED@config_check、
  零产物发布、原因明确；容量界夹在 256/257 行之间。
- 生产代码零改动。详见 docs/horner_v3_step3_segmentation_20261012.md。

## 2026-10-12｜horner_v3 步④：统一 LCS 集成通过

- **失败形态（留档）**：首跑 `hv3u_step4_n8_20261012` RED@three_zone
  「(C-H) 布局缺张量 'other'」——ADR-061 C-H 独立参考实现硬编码
  scale_sum 形态（直取 x/other/alpha 内联重算），且 manifest `op` 硬编码
  `"scale_sum"`（provenance 错标）。两条均属实现窄于 D6 通用设计表述。
- **按用户立法序修复**（禁名称特判）：先加 6 项鉴别性测试（36→42，
  horner 合成 case 双向钉死：GREEN 必绿；参数区互换/参数形态破坏/缺
  formula/未声明叶/装载未消费输入五类各自必 RED）→ 修复 = manifest
  自包含 `formula` 字段 + C-H 公式驱动（有序 AST fp32 逐 op 求值，与 V1
  independent_reference 同 machinery；输入按 role 重建，param stride0
  驻留形态正面取证）+ runner 传真实 op/formula。commit `bd2ed7b`
  （5 文件 380+/31-，含 ADR-061 §7 errata 三条）。
- **修复后实测全 GREEN+published**：n=8（3 段程序，LOAD 12/12 行、
  C-L 首读8408>release546>末写487、C-H 三方一致）；n=72（4 段程序
  LOAD+COMPUTE×2+STORE，LOAD 108/108、两 COMPUTE 段末写<ack、STORE
  18/18、6 项同拍 limitation 沿用 errata 口径登记）。
- **回归**：scale_sum 统一链 n=60 GREEN+published（新公式路径判决不变）；
  add 冻结链 n=4/8/60 GREEN+published；pytest 577 passed。
- **事件留档**：发布冲突（同 run_id 二次发布被门禁阻断，GREEN run 未发布，
  既有 release 未删未覆盖=按设计）；VCS 许可证失败 RED 一次
  （`_codex` 留档，沙箱外获准通道复跑 `_codex_r2` GREEN）。
- **provenance 口径（用户裁定）**：脏树运行未存工具源码哈希——如实登记
  「无法从运行自证完整恒等」，辅助链=mtime 链+git status 恰 5 文件+commit
  恰含此 5 文件；**闭合数据点=提交态 n=8 复验**（bd2ed7b、dirty=false、
  TB 3324a33b…、RTL 0x00042200、GREEN+published、包内报告逐字节一致）。
  n=72 条件复跑条款列入步⑤跟踪。
- 详见 docs/horner_v3_step4_unified_lcs_20261012.md。

## 2026-10-12｜addcmul 第二复杂案例·步①：静态确认通过

- **用户令（2026-10-12）**：addcmul 为第二复杂结构案例，走五步序；步①并入
  precheck U9（addcmul 逐槽结构【推断】）补探针。方法模板 = horner_v3 步①。
- **核心结构发现（三因子乘积结合序分歧，首发实证）**：编译器 DFG 消费 SymPy
  树（configurable.py:213）而非有序 AST；`value*t1*t2` 三因子乘积被 SymPy
  按规范序 `(t1,t2,value)` 平坦化、经 lower_mul 左折叠 → DFG 实算
  `((t1·t2)·value)`，与遗留文本序 `((value·t1)·t2)` 结合序不同——fp32 乘
  结合不安全 ⇒ 遗留 addcmul.yaml **名义语义与编译语义分歧**（存量配置缺陷
  登记；该配置从未过 V2 验收，无已发布错误产物）。**处置 = 配置级公式文本
  对齐**：等价文本 `x + t1 * t2 * value` sympify 同一树、共享同一 DFG，而
  文本序 == 折叠序 → 三层签名互等（A/B 剥 NARY3 标记/C 全 MATCH）。
  零生产代码改动、零名称特判。
- **U9 关闭**：fpmul 双计算槽【推断】→【实测（求解器）】（两实例 [0,2]/[0,1]，
  双槽属性稳定）；II=3 复现 census；17 队列无满槽。
- **容量构造预测** n_max=152（m_max=38，四实例数值稳定；束缚队列身份
  ur-r2/vin0 漂移 span 均 4）——未校准、禁用于准入，步③ VCS 校准。
- **域闸**：遗留 x[-1,1]/t1[-2,2]/t2[0,5] 跨 0 → SUBNORMAL_BAND ×6 复核成立
  （机制=区间-带相交规则 reference_generator.py:230-237）；候选安全域
  cand-A（全正）/cand-B（含负）静态双过（正式值步②定案）；value=0.5 恰可表。
- **方法学发现**：Z3 模型挑选随进程状态/代码路径漂移（同脚本三连跑逐字节
  一致），II 稳定、放置不稳；D-control 口径改为「II 复现断言 + span/L
  观察量」，校准锚 = 发射产物实测（ADR-056 步⑤）。详见
  artifacts/addcmul_static_20261012/VARIANCE.md。附带修复：两静态探针
  `d.path`→`.node_path` 潜在崩溃字段（horner 探针未重跑，归档不动）。
- 生产代码零改动。详见 docs/addcmul_static_confirmation_20261012.md。

## 2026-10-12｜addcmul 步②：小规模计算验证通过 + 发射层放置漂移实证

- **步②通过**：addcmulu（步①对齐公式 `x + t1*t2*value` + cand-B 含负安全域 +
  value=0.5 恰可表）n=4/8/60 三规模 V1 链全 GREEN+published；逐拍仲裁
  18/18 队列全 ok（issue 期望==实测、fetch=期望+1 残尾恰 1）、三区全 GREEN、
  写账零非法（8/16/120 写、wbe 全 0xffff、smc_seen 0..7）。一轮通过，
  零生产代码/通用规则/协议常数改动（配置-only + 守卫名单 39→40）。
- **容量对账修正**：真实发射链 span=3（L_q=3·(m+3)+2 在 m=1/2/15/38/39 五档
  逐字节成立；束缚队列 rtovr-14/vin0）——步①构造预测（span=4 → n_max=152）
  保守，干跑实测 n=152→125 行、**n=156→128 行恰顶格单段**（未分段）；
  构造式 n_max 修正为 156（仍未校准，步③ VCS 顶格 + n=160 上探）。
- **方法学实证（步①开放问题关闭）**：放置漂移**可达真实发射产物**——n=4
  同配置 08:11 干跑进程发射变体 B（17 队列 rtovr-37/ur-w2，ur-r3=17 行）、
  08:21 VCS 进程发射变体 A（18 队列 rtovr-35/ur-r1/ur-w0，ur-r3=5 行）；
  6 个定量探针进程 6/6=变体 A（sha256 29388453…）；漂移率观察 1/8。
  **近壁稳定**：n=156 跨 5 进程全 128 行、sha256 de7f00bc… 一致。
  口径收缩：horner 步③ 18/18 跨进程 hex 一致仍为实测事实，但「真实发射链
  跨进程确定性」不得再作全称陈述；当日口头「规模间路由差异」归因撤回
  （正解=进程漂移）。注册表 v3 论证新增必答项：冻结公式与放置选择的关系。
- **provenance**：三 run_report 内嵌 op_yaml_sha256=a66aa9da…8239 == 本提交
  addcmulu.yaml 逐字节；TB 3324a33b…f7c5 / simv 72c2c031…1c96 / 注册表 v2
  973861aa… 三锚互符；运行时 git_head=f64a787 dirty=true（脏内容恰为本步
  两文件）。
- 详见 docs/addcmul_step2_smallscale_20261012.md；VARIANCE.md 当日补遗
  （gitignored）；探针 case 目录取证后已清理，规范副本存
  build/v1_runs/addcmulu/acu_varprobe_*。

## 2026-10-12｜addcmul 步③：容量 VCS 校准 n_max=156 + 分段链验证通过

- **容量校准落锤**：n=156 单段（m=39）VCS 全链 GREEN，被仿真包中束缚队列
  rtovr-14/vin0 = 恰 128 行（直接计数）⇒ addcmulu 单段 n_max=156（VCS 校准
  值）；三区 39/39 行 bit-exact、逐拍 18/18 队列 ok、写账 312 零非法、
  c0=37962。预测-实测算账闭合：步①构造 152（span=4 保守）→ 步②干跑修正
  156（span=3，L_q=3(m+3)+2 五档成立）→ 步③ VCS 证实；全程实测优先。
- **上探分段**：n=160（构造预测 L=131>128）由分段器介入为 39+1 两段全
  GREEN——ADR-057 语义（超墙分段而非响拒）在第二案例复现；段间 C1 段序/
  C2 无残留写（空窗构造性注记照录）/C3 地址连续全过；尾段写账恰
  {bank4,row39}×1、8 写零非法；c0 锚 stage_0=37962（== n=156）、
  stage_1=40561。
- **跨案例 hex**：n=160 stage_0 与 n=156 单段 18/18 队列逐字节一致
  （两独立进程；按步②口径限定已实测进程集合，不作全称推广）。
- **响亮拒绝两点**：n=158（不整除 vector_lanes=4）/n=1028（静态消耗
  257>256）均 config_check TYPE_LAYOUT_UNSUPPORTED 早拒、published=False，
  与 horner n=70/n=1028 同闸门族。
- **provenance**：GREEN 运行全提交态（git_head=c0ba5f3b dirty=false，
  yaml/TB/simv/注册表四锚互符）；RED 探针早拒无 provenance 块属预期。
- 生产代码零改动；详见 docs/addcmul_step3_capacity_20261012.md。

## 2026-10-12｜addcmul 步④：统一 LCS 集成零规则改动通过

- **零规则改动首轮通过**：n=8（三段 LOAD[12,494]+COMPUTE[494,3909]+STORE
  ack@4107）与 n=160（四段 LOAD[12,1058]+COMPUTE×2+STORE ack@37870）统一
  LCS 链双双首轮 GREEN+published；program_manifest 记录公式原文
  `x + t1 * t2 * value` 被通用 C-H 消费——horner 步④公式化修复对第二
  复杂公式的适用性首个正面实证；立法序未启动、回归触发条件未达。
- **逐拍与比对**：两 COMPUTE 段 17/17 队列逐拍 ok；写账 16/304+16 零非法
  （wbe 全 0xffff、smc 0..7）；unified_lcs_compare 双 GREEN；C-L/C2
  同拍-空窗诚实注记照录（硬断言承载次序）。
- **附随方法学观察（实例 C）**：统一链两进程发射 17 队列变体（ur-r2
  span=4 束缚）→ 分段 m_max=38（38+2），与步③ V1 链实例 A 的 39+1
  不同；stage_manifest queue_spans 与发射行数逐行自洽（seg0 ur-r2=128
  恰顶格）。**分段边界 = 求解实例相关量**：n=160 不得宣称唯一分段形态；
  正确性判据（C1/C2/C3、bit-exact）实例不变。容量墙 128 行/段为硬件
  常数；单段迭代容量实测区间 38–39（两值均 VCS 验证）。注册表 v3 开放
  问题追加「分段决策实例相关性与公式冻结的关系」。
- **provenance**：两运行 git_head=74cf64b1 dirty=false（提交态）；
  yaml/TB/注册表三锚与步②③一致。生产代码零改动。
- 详见 docs/addcmul_step4_unified_lcs_20261013.md。

## 2026-10-13｜上板实验包备妥（备而不跑；首个编译器产物上板包）

- **裁定依据**：2026-10-13 用户令「上板实验包现在值得作为下一项主要交付——先交付小规模、
  明确硬件版本和等待策略的实验包，取得板端实际输出证据；不立即扩大到多 SMC、
  复杂尾块或 F6 新 bitstream」。本包交付边界 = 文件齐备 + 本地 dry-run 通过 +
  等待策略/判据/停止条件明文；**未接触板卡**（standing 约束：不自动连接/烧录，
  板端执行由用户在 zwz 手动进行）。
- **用例选择**：scale_sum n=16 统一 LCS（`ulcs_scale_sum_adr061_accept2_n16_n16`）——
  V1 链最保守已证形态（II=2 单槽、ADR-056/057/061 验收史、accept2 提交态复跑
  GREEN+published）。provenance 锚：compiler_repo git_head e76592b（dirty=true 按实）、
  registry v2 973861aa…（run 先于 v3；v3 常数零改动故 v2 期望继续有效）、
  TB 3324a33b…、manifest 86f48037…、op_yaml e17f4a24…。
- **包构造**：`case/`（meta_v3.json {num_smc:8, out_bank:2, out_words:4, word_bytes:16,
  store_byte_base:20480}——全部取自 manifest hbm_layout.y 实测值；hbm_data=8 路
  memory_init 镜像；smi_iq_inst 三段；golden 仅 smc_0/output_bank/bank2_final.hex，
  smc_1..7 故意缺席 → runner SKIP 与 VCS 单 SMC 证据范围一致，不预测哨兵窗输出）
  + `evidence/`（manifest/run_report/compare/reference 审计锚）+ README.md。
- **等待策略明文**（README §4）：DONE 轮询 10ms/10s 上限每段；**DONE 置位 ≠ 数据落位**
  （F6 已证 legacy 竞态）→ 判据 = HBM 输出窗 64B 与独立 fp32 参考 bit-exact（数据面）；
  int_delta≠1 仅 WARN；err 位置位即 FAIL；超时不自动重试不自动复位。
- **dry-run 实证**：exit=0；软复位→8 路 HBM 预载（stride 0x10000000）→stage_0/1/2
  依次加载（stage_1 = 16 IQ 文件 228 packer 写）→回读窗地址 0x5000=20480 与
  meta 一致→smc_1..7 SKIP→收尾。版本门（RTL_VERSION=0x00042200 + IQP1 cap）经源码
  核实仅在非 dry-run 路径执行，dry-run 不触任何设备节点。
- **封存**：`~/u280_fpga_packages/scale_sum_ulcs_n16_v422_compiler_onboard_prepared_20261013/`
  （60 文件）+ `.tar.gz`（sha256 3cc3191a47dacd00…，唯一命名未覆盖历史包）。
- **r2 修订（当日，用户 zwz 首跑发现）**：README §5 命令把 runner 路径错写为
  `tools/../aiacc_u280_case_runner.py`（zwz 不存在；runner 不属 XDMA tools 树，
  系路径猜测错误）→ r2 起 runner 随包携带（单文件纯 stdlib，
  sha256 5f173003bb4ee382…，与 dry-run 校验同一文件；meta_v3 契约耦合故禁混用
  zwz 历史副本）+ 命令改相对路径 + §0 修订注。包内 runner 对包内 case dry-run
  exit=0 复验。r1 包保留不覆盖、作废以 r2 为准：
  `…_prepared_20261013_r2/` + `.tar.gz`（sha256 06a2b6366b4492dd…）。
- **r3 修订（当日，用户指示参照 09-21 板端实跑包 rh_real2tile_slots_fpga_20260921）**：
  ①runner 换为板证 v2 副本（09-21 包内文件 sha256 d8e6be6dbdcc6385…，zwz 实跑同源；
  为 r2 所携副本超集——多出 multitile/stage-range/hbm_extra_regions 路径对本 case
  全不激活（meta 无对应键），两 runner 对本 case dry-run 段迹逐行一致 16/228/4
  packer 写、exit=0；版本门 0x00042200+IQP1 双处俱在）；
  ②对齐实跑包封装惯例：新增 run_one_case.sh 入口（XDMA_TOOLS_DIR 双路径回退，
  入口脚本 dry-run exit=0 实证）+ PACKAGE_INFO.txt（provenance/版本门/等待策略
  key=value）+ PACKAGE_SHA256SUMS.txt（63 行，`sha256sum -c` 63/63 OK）；
  ③§5 补设备节点权限步骤（2026-10-13 zwz 首跑实证 /dev/xdma0_user EACCES：
  sudo 或 chmod a+rw，临时）。r2 包保留不覆盖，以 r3 为准：
  `…_prepared_20261013_r3/` + `.tar.gz`（sha256 22bfad6ad7773bb3…）。
- **不扩大清单**（README §7）：板端若通过 = 该产物此规模此三段形态的实际输出证据；
  不外推任意规模/多 SMC/其他算子；VCS 侧 sync_model=tb_assisted，host 完成协议闭环
  仍是 F6 开放问题；不触及 F6 修复 RTL/新 bitstream/尾块扩展。

## 2026-10-13｜注册表 v3 追加冻结（用户裁定五行表 + 独立论证先行）

- **裁定依据**：用户 2026-10-13「注册表 v3：进入追加冻结，范围绑定已验证
  形态」+ 五行冻结内容表（公式语义/调度漂移/分段决策/容量结论/已证范围）
  +「冻结决策规则和实例证据，不冻结 n=160 必须切成某一种形状」+「冻结
  调整后的公式、逐步 fp32 运算顺序及独立参考，不能只写代数等价」。
- **独立论证先行**（docs/registry_v3_argument_20261013.md）：①II 独立性=
  RTL 文本归约——smi_iq_iic.v 无 II 寄存器/参数/分支（参数表 :33-35 仅
  EN_LOOPS/BITS_LOOPS/LOOPS_MSB_BIT）；l_init 以 LOOPS 字段编码值进入
  硬件（:253 译码、:828-838 计数回放、:1202-1203 退出清零；发射侧
  emit_expected_fire_sequence.py:159 写入）；II·R_q 以行数形态进入
  （:841-862 每行一拍；L_q=II·R_q+2，stage_partition.py:88 + manifest
  自洽机检）；TERM 补拍/ISSUE 偏移为结构常量（:96 output reg、
  :662/:671/:693）——II 仅进入编码值与行数，扩展非数值外推。
  ②公式语义=最终文本+有序 AST+逐节点舍入序+独立参考（三层签名互等 +
  ordered_ast canonical digest 验包机检）——「代数等价」不足为凭
  （addcmulu ((t1·t2)·value) vs 遗留 ((value·t1)·t2) 逐位可区分）。
  ③必答项①漂移与公式冻结分层（公式是队列级 f(skip,II,R_q)，放置改变
  t 集合而非 f；期望逐实例独立生成）。④必答项②分段=发射后处理，
  边界=f(实际调度, span_q, 容量墙)，39+1/38+2 两实例同合法。
  探索证据（II=6 六运行、II=3 七运行逐拍仲裁全 GREEN）严格单列、
  不作论证依据。
- **v3 文件**（verification_v2/tools/protocol_registry_v3.yaml，v1/v2
  零改动）：17 条 = 9 携入（INIT_PAD/TERM_PHASE/ISSUE_PHASE_OFFSET/
  ISSUE_TOTAL/ASSERT_FORMS 五边界扩展至 II∈{2,3,6} 多槽形态；PROJ_FETCH/
  PROJ_ISSUE 仅出处追加；PROBES_TAIL/GLOBAL_MONITOR 逐字）+ 8 新增
  （FORMULA_SEMANTICS×4 含各算子求值序与 sha256 锚/PLACEMENT_DRIFT_
  POLICY/SEGMENTATION_DECISION_RULE/CAPACITY_MEASURED_SCOPE/
  PROVEN_SCOPE_V3）。--seal → records_sha256=500c539e3ed6ce88…；
  --check 冒烟通过。
- **默认切换**：_DEFAULT_PATH → v3（注释链 v1→v2→v3）；
  emit_expected_fire_sequence.py 两处唯一来源注释同步；历史包复验须
  显式加载旧版文件（v1→v2 既有模式）。测试：默认版断言 2→3 +
  枚举 17 条 + 新增 test_v2_archive_preserved_untouched；
  **pytest 578**（+1，7 skipped，11 xfailed）。
- **常数零改动**：全部既有记录 value 逐字保留；「不为匹配实测改常数」
  无施行对象。禁称清单入档（论证 §7）：n=160 唯一形状/任意 II/跨进程
  逐字节确定性/容量外推/mul 入边界/任意公式文本语义保留/host 闭环/上板。

## 2026-10-13｜addcmul 步⑤：第二复杂案例五步序收口

- **五步序全闭合**（①f64a787 ②c0ba5f3 ③74cf64b ④1d0e634 ⑤本次）：
  全程零生产代码、零通用规则改动——唯一前置规则修复（horner 步④ C-H
  公式化）由第一案例触发、本案例直接受益（统一 LCS 首轮通过，立法序
  未启动），通用修复的跨案例效益获首个正面实证。
- **覆盖矩阵更新为四算子口径**（docs/addcmul_step5_closure_20261013.md
  §2，取代 horner 步⑤ §2）：第一档加 addcmulu 行（II=3、fpmul 双槽
  单单元、三流式+驻留标量、含负单符号域、单段 n_max=156 VCS 校准、
  统一链分段 38–39 实例相关）；第二档新增第 9–13 条（双槽单单元形态/
  三因子结合序配置级文本对齐法/含负单符号安全域/放置漂移下正确性
  稳定性/C-H 通用化复用）；第三档可宣称增量 = II∈{2,3,6}（V1 链；
  II=1 仅 mul 一例、属前 V1 证据类，按 2026-10-13 用户裁定单列——
  见里程碑表当日勘误行）、多槽
  三档谱系、结合序对齐法（一例）；新增三不外推 = 发射逐字节跨进程
  确定性、分段边界实例不变性、任意多因子对齐可行性。
- **方法学资产入档**：三因子乘积结合序分歧处置法（步①）；放置漂移
  三实例登记 + 近壁稳定性 + 实例不变判据口径（步②④）——注册表 v3
  论证素材两案例齐备（新增两必答项），留用户裁定。
- pytest 基线 577（步②提交时点复跑，其后 docs-only）。E1–E5/上板包/
  条件复跑条款/遗留 addcmul.yaml 冻结登记，口径同 horner 步⑤。
