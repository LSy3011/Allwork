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
