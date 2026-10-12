# horner_v3u 步②小规模计算验证报告（2026-10-12）

> 用户令五步②：小规模计算验证（独立参考＋真实 VCS＋逐拍裁决；先隔离计算结构
> 问题）。对象 = `horner_v3u`（等价目的新配置，理由见步①报告 §5/§4）。
> 本步范围 = COMPUTE 单段链（ADR-056/057 V1 链，TB 预载 UR、UR 终态比对 +
> 逐拍仲裁）；LOAD/STORE 自动链路属步④。
>
> 证据分级：【实测】= 当日真实运行；【源码】；【归档】。

## 0. 结论

**步②通过**：n=4/8/60 三规模 V1 链全 GREEN + published，逐拍仲裁 18/18 队列
全 ok、三区比对全 GREEN、写账零非法。II=6 形态首个 VCS 证据**一轮通过**，
未改任何生产代码/通用规则/协议常数（配置-only + 测试名单随配置更新）。

| run_id | n | outcome | published | 释放包 |
|---|---|---|---|---|
| hv3u_step2_n4_20261012 | 4 | GREEN | True | releases/horner_v3u_hv3u_step2_n4_20261012 |
| hv3u_step2_n8_20261012 | 8 | GREEN | True | releases/horner_v3u_hv3u_step2_n8_20261012 |
| hv3u_step2_n60_20261012 | 60 | GREEN | True | releases/horner_v3u_hv3u_step2_n60_20261012 |

## 1. 配置迁移（步①两项处置落地）

`verification_v2/ops/horner_v3u.yaml`（新文件；horner_v3.yaml 原样守护 census
快照可复现性——add_v1 克隆先例）：

- 公式逐字不变：`((c0 * x + c1) * x + c2) * x + c3`。
- bank 布局：x=0、c0..c3 分置 1/3/4/5、y=2、constants=7（一 bank 一张量，
  满足 ADR-061 case-gen 闸 `unified_lcs_case_gen.py:111`【源码】）。
- 参数值改 fp32 恰可表：0.125/−0.25/0.375/−0.5（步① §4 动态道发现）。
- `experimental_unroll: true` + 全张量 `length: 256`（ADR-056 §B.1/§F.5）。
- 快照守卫 `test_admission_l1_l2.py` 同步：语料 38→39、flag 名单
  [add, add_v1, horner_v3u, scale_sum]——该测试设计即随新配置更新；
  `test_yaml_corpus_zero_conflict_and_single_flag` 等 30 项全过【实测】。

## 2. 准入干跑（run_vcs=false；门级/发射级证据，非 VCS）

| n | 结果 | 关键实测 |
|---|---|---|
| 4/8/60/68 | 准入→装配全过（UNVERIFIED@VCS_NOT_RUN 为预期终点） | 18 队列发射；Python SMC 参考比对 PASS |
| 68 | **单段恰顶格**：fpadd/rtovr-3/4/5/6/14/vin0 七队列实测发射 = **128 行**（L=6·(17+4)+2，与构造预测逐字节一致） | case dryrun_hv3u_…_n68 |
| 72 | **分段器介入**：2 段（m=17+1），stage_0 七队列 128 行、stage_1 32 行（=6·(1+4)+2 ✓）——ADR-057 语义下超单段容量 → 分段而非响拒 | case dryrun_hv3u_…_n72 |

对账修正（步①报告 §6 的一处口径）：span_q 是**调度/布局相关**量——旧布局
（参数共 bank1）fpmul span=4，新布局 fpmul span=3、fpadd 仍 4，束缚队列由
「6 条同界」变为「7 条 span=4 同界」（fpadd/rtovr-3/4/5/6/14/vin0）；
单段 n_max 构造预测 68 不变，本轮由真实发射行数在门级确认（n=68 恰 128、
n=72 分段 17+1）。**容量实测校准（VCS 级）仍属步③。**

## 3. VCS 逐拍裁决（注册表 v2 公式，II=6 首次行使）

期望 = 调度 + 注册表 v2 冻结公式（II 为绑定变量；未引入任何新常数），
仿真前落盘（§D.3 纪律）。裁决结果：

| n | 队列 ok | fpadd fetch | fpadd issue | vin0 issue | 写账 | 三区 |
|---|---|---|---|---|---|---|
| 4 | 18/18 | 32 exp / 32+1 残尾 | **39/39**（=7+1+6·5+1） | 33/33 | 8 写零非法 | GREEN |
| 8 | 18/18 | 38 / 38+1 | 45/45 | 39/39 | 16 写零非法 | GREEN |
| 60 | 18/18 | 116 / 116+1 | **123/123**（=7+1+6·19+1） | 117/117 | 120 写零非法 | GREEN |

逐项核对：L_q=II·R_q+2 行数恒等式、INIT_PAD（l_init=skip·II+1）、TERM 相位
（l_init+1+II·R_q）、ISSUE_TOTAL（v2 勘误 +1 TERM 拍）在 II=6 全部逐拍成立；
残尾取指恰 1 次符合 PROBES_TAIL_SEMANTICS；跨队列 c0 同锚（6073@n4）。
violations 全空、limitations 无。

## 4. 治理登记：注册表 v2 边界与 v3 追加问题（留用户裁定）

- v2 冻结记录的 proven_boundary 明文「A1 已证形态 add/II=2；II=1/flat/压缩不在
  边界内」——**II=6 展开形未被排除，但也非已证**；ASSERT_FORMS 条注明「断言形式
  可继承，常数值在新形态须独立论证后冻结」。
- 本步三规模 GREEN = II=6 形态的**探索证据**（正面）。独立论证要点（备 v3
  追加冻结时引用）：①各公式 II 依赖性 = 发射器构造本身
  （`assembler.py:491-634` 展开环对 iq_II 通式、无 II==2 假设【源码】）；
  ②ISSUE_PHASE_OFFSET=1 的 provenance 是 RTL 结构性寄存器边界
  （smi_iq_iic.v:96 output reg + TB posedge 采样语义），与 II 无关；
  ③本步 3 规模 × 18 队列逐拍实测零偏差。
- **待裁定**：是否据此将 II=6 展开形追加冻结入注册表 v3（records 追加 +
  `--seal` 重封；v2 记录不动）。本步结果在 v3 冻结前口径 = 「探索证据 GREEN」，
  不构成该形态协议模型的正式已证边界扩张。

## 5. 回归

- pytest 全量（见 progress_log 当日条目；基线 571 + 本批新增）。
- 生产代码零改动（git diff 仅：新 yaml、守卫测试名单、探针/文档）。
- add/scale_sum 的 VCS 级回归通道（frozen_regression/add_v1/scale_sum 四配置）
  按惯例在步③④验收节点统一复跑——本步无规则变更，触发条件未达
  （用户令：「若需要改通用规则……再修规则并回归 add、scale_sum」）。

## 6. 步③入口条件

1. 容量边界实测：n=68 单段 VCS（恰顶格形态）+ n=72 分段 VCS（17+1 尾段检查）；
2. 分段链检查：尾段/地址/中间状态（ADR-057 既有检查栈）；
3. 响亮拒绝边界：继续上探至真实拒绝点（UR bank/HBM 窗或分段器上限），
   记录拒绝形态。
