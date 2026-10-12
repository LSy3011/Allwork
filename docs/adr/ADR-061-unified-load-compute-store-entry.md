---
adr_id: ADR-061
title: scale_sum 统一入口：LOAD→COMPUTE→STORE 自动完整链路（公式链 COMPUTE + 布局驱动 LOAD/STORE 自动生成 + 程序 manifest）
decided: 2026-10-10
last_verified: 2026-10-12
still_holds: proposed
hco_review: pending
builds_on: ADR-004, ADR-055, ADR-056, ADR-057, ADR-058, ADR-059, ADR-060
user_decision: 2026-10-10 执行令（三）：①COMPUTE 必须来自公式编译器链，不接手写 compute hex；②LOAD/STORE 段由数据布局与程序计划自动生成；③manifest 记录段类型/依赖/地址/逻辑迭代区间/完成要求；④先接受小规模单 COMPUTE 段；⑤再接受超容量多 COMPUTE 段规模；⑥保留独立参考、三区比对、逐拍/写账检查、失败不发布；⑦TB 辅助同步与 RTL 版本写入报告，不宣称 host 自主闭环；⑧保留 add/mul/scale_sum 已验收回归路径
---

# ADR-061：scale_sum 统一入口自动完整 LOAD→COMPUTE→STORE

> **状态：proposed（2026-10-10 验收④⑤全绿 + 回归全绿，见 §4/§6）。硬件数值不变；
> F6 在隔离副本，不入本链。**

## 1. 背景与缺口

ADR-056/057 已交付：用户只新增配置，编译器自主准入、生成单段或受限多段
**COMPUTE** 指令，自动 VCS + 独立语义验证（n≤240 单段、n=244/480 两段均
GREEN）。但该链路的 case 里**只有 COMPUTE stage**——输入数据由 TB 直接预载
UR bank（`smc_0/ur_data/bank*.hex`），输出靠 UR 终态比对。没有真 LOAD/STORE
指令，数据不从外部存储来，链路不是「编译器产物 → 硬件」的完整形态。

M3-lite 线（ADR-058/059/060）反向证明了另一半：LOAD（LDB/LDBAXI，门②）与
STORE（STB/STBAXI，门①）发射形态均 VCS 实证，门③ LOAD→COMPUTE→STORE 全链路
GREEN——但其 COMPUTE 来自 v3 mul_scalar 生成器，**不是公式编译器产物**。

本 ADR 合并两条血统：**公式链 COMPUTE + 布局驱动自动 LOAD/STORE**，一个入口
产出完整可执行程序与机器可读 manifest，并沿用全部既有检查与失败不发布纪律。

## 2. 范围与非目标

**范围**：scale_sum（ADR-056 已准入形态：无状态、非原地、单输出逐元素、
fp32、单 SMC、顺序执行）；n%4==0（整 UR 行）；单段 n≤240（ADR-056 实测容量
边界）与多段 n>240（ADR-057 分段器决定段数；UR bank 容量内）；数据 UR 驻留。

**非目标**（与 ADR-057 对齐并追加）：多 SMC、host chunk、ragged tail（n%4≠0
响亮拒绝）、矩阵/归约/原地更新、LOAD/STORE 进入 SMT、流水重叠、上板、F6 修复
并入（F6 在隔离副本，不入本链）、host 自主完成闭环（见 D7）。

## 3. 决策

### D1 数据布局权威来源 = 编译产物 sim_config.tensors[]

统一入口不反解指令 hex 推布局。每段编译产物 `sim_config.json` 的 `tensors[]`
给出 `name/bank/addr/shape/role`（scale_sum n=244 实测：x=bank0、other=bank1、
alpha=bank3(param)、constants=bank7、y=bank2，各 [n/4,4]，addr=0）。UR 字节
地址约定：bank_k line0 = k×4096（与 TB/SRAM 模型及门③实测一致，紧凑原生布局，
无映射转换）。LOAD/STORE 生成器以此 + 段计划为唯一布局输入；manifest 记录
sim_config 哈希。

### D2 LOAD 段自动生成

对每个 role∈{input,param,constant} 的张量，按其 bank 窗生成一对
LDB+LDBAXI（复用门② `ldb_review_case_gen` 发射器——VCS 实证形态，窗口化
多 inst 由发射器按容量自行切分；line_cnt≤255/inst）：HBM 区 → UR bank 窗。
HBM 侧每张量一个 4KB 对齐区，统一入口显式声明字节基址，两两不相交，
HBM 镜像（memory_init1）内容 = 输入数据（与现链路预载内容逐字节同源：
同一 `golden_data.npz`/输入生成器）。

**LOAD 承重纪律**：被 LOAD 服务的 bank，TB 预载一律改写 ee1d%04x 行索引哨兵
（门③同款），禁止预装正确 UR 数据使 LOAD 虚设；y（输出 bank）预填全零哨兵。
常量 bank（role=constant）同样经 LOAD 从 HBM 来——一切初始化数据走外部存储。

### D3 STORE 段自动生成

对 role=output 张量，按其 bank 窗生成 STB+STBAXI（复用门①
`stb_solo_case_gen` 发射器——VCS 实证形态；brst≤15 拍/inst，多 inst 切分
由发射器完成）。目标 HBM 输出区显式声明、与输入区不相交、预填全零。

### D4 程序计划与段序

程序 = 有序段列：`LOAD → COMPUTE_0 → … → COMPUTE_{k-1} → STORE`（k=1 即
④小规模形态；k≥2 即⑤超容量形态）。段间依赖线性（每段 predecessor=前段）。
COMPUTE 段由 ADR-057 分段器产出（段数/迭代区间/发射字节零改动——本 ADR 不动
COMPUTE 发射）。LOAD 先于一切 UR 读；STORE 后于一切 UR 写——由段序 + TB 逐段
done/ack 保证，仲裁器正向核验（D6）。

### D5 程序 manifest（adr061_program_manifest_v1）

机器可读、随 run 归档、计入 SHA256SUMS。字段：`schema_version`；输入配置/
sim_config/段计划/硬件配置哈希；`hbm_layout[]`（每区 name/byte_base/bytes/
方向/对应 UR bank 窗）；每段：`stage_id/kind∈{LOAD,COMPUTE,STORE}/predecessor/
逻辑迭代区间（COMPUTE 段；LOAD/STORE 记覆盖行窗）/地址偏移（word 与 byte 单位
分列）/active_smc/IQ 文件与哈希/实际行数/期望写区/completion_contract_id`。
COMPUTE 段内嵌 ADR-057 manifest 引用（不复制其字段，哈希锚定）。

检查器扩展 `check_stage_manifest`：从原始任务重算段覆盖与地址范围，对照
manifest、解码指令（LDB/STB 逐 INST 地址字段 == manifest 声明——复用板包
static_check 同款逐 INST 解码器）与 RTL 事件。漏段/重复段/偏移错/写区错/
LOAD 窗与张量声明不符均 RED；篡改测试同 ADR-057 D3 双形态（未重封印 +
重算哈希后语义篡改）。

### D6 检查栈（全保留 + 并入门③型）

在 run_v1_verify 既有栈（准入/独立参考/期望先行/三区比对/逐拍仲裁/manifest
校验/失败不发布）之上，对本链路并入门③同型正向检查：
- **C-L**：一切 COMPUTE 首读 UR 晚于 LOAD 末写 UR，且晚于 LOAD 段 done/ack
  （首读@>末写@>release@ 顺序仲裁）；
- **C-S**：一切写事件 ∈ 本段期望写区（COMPUTE 写 y 窗；STORE 写 HBM 输出窗；
  写账逐拍，非目标写即 RED）；
- **C-H**：HBM 输出窗终态 == 独立参考（fp32 逐 lane）== golden UR 终态经
  STORE 映射（三区一致）；
- **C-W**：STORE 外部完成不靠中断假设——TB 辅助屏障（WBARRIER/S4 同型：
  计数 UR 写/HBM 写 + bresp）佐证排干；固定等待仅实验手段并如实标注；
- 段间空窗维持 ADR-057 errata 口径（limitations 分列，不单独致 RED），
  跨段残留写检查维持 RED。

### D7 完成契约与报告口径

同步 = **TB 辅助**（TB host 环逐段装载/触发/等 done/清中断）；报告写入：
TB 标识与 sha256、RTL 版本 0x00042200、各段完成/应答/末写拍号、固定等待
参数（若有）并标注「仅为实验等待」。**不宣称** host 自主闭环、不宣称 STORE
外部完成已由硬件契约保证（F6 线既知边界，STORE 保留验证屏障）。

### D8 响亮拒绝

n%4≠0；n 超 UR bank 容量；张量 bank 窗交叠；HBM 区交叠；LOAD 窗与
sim_config.tensors 声明不符；段计划与 ADR-057 分段器输出不符——均
STATIC_ADMISSION_REJECTED 类诊断，不发布。

## 4. 验收（2026-10-10 全项执行完毕）

**④ ✅**：n∈{16, 60, 240} 单 COMPUTE 段 → 3 段程序 VCS 全 GREEN +
D6 全检查 GREEN + manifest 校验 GREEN + 篡改测试 RED（36 项单测）。
关键拍号（LOAD 覆盖 / STORE 覆盖 / C-L 首读>release>末写）：
n=16：16/16、4/4、3496>442>367；n=60：60/60、15/15、9540>480>411；
n=240：240/240、60/60、34726>1096>1023。run_id=adr061_accept_n{16,60,240}，
published=True。
**⑤ ✅**：n=244（2 COMPUTE 段）→ 4 段程序全 GREEN：LOAD 244/244、
COMPUTE s1/s2 逐拍全绿且 C1 末写<ack（34868<34934、36374<36430）、
STORE 61/61、S4@36898>末写@36822、WBARRIER@36368∈末 COMPUTE 段窗
[34934,36430]（跨段累积掩码实证）、Z3 双源计数 305=61+244。
run_id=adr061_accept_n244，published=True。
**n=480 维持 ✅**：ADR-057 既有链（run_v1_verify，本轮零改动）复跑
GREEN+published，run_id=adr057_regress_n480_20261010。
**回归 ✅**：add_frozen_regression/add_v1_admission（n4n8n60）、
scale_sum_v1_n4n8n60、scale_sum_v1_n240_boundary 四配置全 GREEN；
pytest 全绿（566 项，含本 ADR 33 项）；ADR-057 COMPUTE 发射零改动
（本 ADR 不动 COMPUTE 链，git diff 无 src/ 与发射器变更）。
**口径注记**：scale_sum_v1_n244_over.yaml（ADR-056 步⑤-4 响亮拒绝
用例）在 ADR-057 后语义已转为多段准入——本次复跑 GREEN（2 段
multi_stage），确认 ADR-057 行为不漂；配置注释陈旧系 ADR-057 既有
状态（s54_n244_recoded @2026-09-30 为拒绝形态历史证据），非本轮引入。

## 5. 限制（落账）

- LOAD/STORE 生成器只覆盖 D1 布局形态（addr=0 起始、整行、单 SMC、bank×4096
  字节约定）；形状/基址越出即拒。
- LOAD 数据与现链路预载同源（同一输入生成器）；「独立参考」仍是独立计算
  （numpy fp32），输入数据本身非独立第二来源——与 ADR-056 既有口径一致。
- 完成时序信任 TB 辅助屏障与固定等待的组合，非硬件完成契约（F6 边界）。
- alpha/constants 按张量声明整窗 LOAD（含复制行），不做 stride=0 驻留压缩。
- **alpha stride=0 驻留形态（2026-10-10 实证）**：yaml 声明 alpha 消耗 1 word，
  编译产物 ur_data bank3 实际驻留 = 仅 line0 四 lane 全等（0x40200000×4）、
  其余行全零——尽管 sim_config 声明 shape [n/4,4]。C-H 独立重算据此采用
  **标量广播**语义，并加正面形态守卫（line0 四 lane 全等 ∧ line1.. 全零，
  越出即 RED 不静默）。constants bank7 同形态同理处理。
- **C-L 比较矩阵与用户裁定口径（2026-10-10 裁定钉死）**。比较对象三个：
  首读 = COMPUTE 窗内对 LOAD 服务 bank 的最早 UR 读；末写 = LOAD 窗内最后
  UR 写；release = G3 信用屏障首次 release。裁决：
  ① 首读 < 末写 → RED（已实现硬断言；且因两段窗以 BEGIN 半开分离，
  `末写 < BEGIN₁ ≤ 首读` 在标记完好时结构性成立，该断言是防标记损坏的
  防御闸——**真正承载「读不先于写」的是**：迟到 LOAD 写落入 COMPUTE 窗
  即被 G4 非法写捕获、落入 STORE 窗即被「STORE 段 UR 写恒空」捕获，
  两条均 RED，已有专测钉死）；
  ② 末写 == 首读 → RED（不降为 limitation——无 UR 存储端口同拍读写
  语义或更精采样证据前，同拍不可宣称安全）；
  ③ **首读 ≤ max(前置 release 集) → RED**（含同拍，从严——同拍采样
  分辨率不足以证明读在屏障放行之后）。前置集按段匹配（2026-10-12 用户
  有界核查裁定）：release r 归于首个满足 begins[k] ≥ r 的段入口 k；
  首读所在段 j_fr 的前置集 P={r : r ≤ begins[j_fr]}；单 release 退化
  为原规则；多 release 同为首读前置时须晚于其最大值——原 min 实现在
  「首读与后段入口 release 同拍」形态下有洞（同拍即违例却放行），
  已由合成测试双向钉死（同拍 RED / 严格大于 GREEN）；release 不归于
  任何段入口（晚于末段 BEGIN）→ RED；
  ④ 无 release ≤ BEGIN₁ → RED（屏障未在 LOAD→COMPUTE 段间生效；
  与原「release > BEGIN₁ → RED」对单 release 等价，对多 release 更准）；
  ⑤ release == 其归属段 BEGIN → 不单独致 RED 但逐条落 limitation
  （TB 段间零延迟顺序推进的构造性同拍，与 ADR-057 errata 空窗同源），
  **且不据此声称独立屏障证据**——次序证据由 ①③ 的严格大于独立承载。
  **已测案例核查（2026-10-12）**：四次验收运行 g3_credit release 实测
  均为每段过渡一次（n=16 [442,3582]、n=60 [480,9648]、n=240
  [1096,34924]、n=244 [1096,34934,36430]，恰为各自 begins[1..]）；
  首读均落在 stage_1 窗内，前置集恰 1 元（单 release 分支成立），
  新规则下原 GREEN 判决逐拍不变（首读 > max(P) 余量三个数量级）。
  实测全部严格大于、余量三个数量级：n=16 首读3496 > release442 > 末写367；
  n=60 9540>480>411；n=240 34726>1096>1023；n=244 34726>1096>1027。
- **LOAD/STORE 段 ack↔末写关系仅观察**（B5c/ADR-058 errata 既知：LSU 批完成
  通知可先于写落位）：仲裁器记录 ack_vs_last_write 观察量并落 limitation，
  不作硬断言；COMPUTE 段维持 ADR-057 实证域「末写 < ack」硬断言。
- **探针日志格式钉死**：hbm_write_trace.log = 5 列（cycle smc addra(%05h)
  wea(%04h) dina(%032h)）；ur_read_trace.log = 5 列（cycle smc port bank
  addr，smc0 port0..3）。
- **WBARRIER 掩码自复位累积**：掩码位自复位起累积、在末 COMPUTE 段 xi 处
  武装一次即可覆盖跨段写计数（writes=n_lines 全程序口径），在
  smi_wait_and_clear_intr 之前进入——门③既有语义，本链平移。
- **manifest 检查③b = 再发射逐字节比对**：从 manifest 声明参数经真
  LIRPacker 重算描述符再发射，与 case 指令文件逐字节相等——语义篡改（重算
  哈希后改指令）与字节篡改双形态均被捕获（替代 D5 原拟的逐 INST 解码器，
  等价且更严：发射器即解码关系的正向构造）。
- ~~**已知证据打包缺口（2026-10-10 落账，不影响本轮验收判定）**~~
  **已关闭（2026-10-12，用户裁定四步）**：①沙箱四次运行原始
  manifest/g3_expect 已保存至独立归档
  `artifacts/adr061_manifest_recovery_20261012/`——manifest sha256 与
  run_report provenance 锚四次全 MATCH，g3_expect 与证据内
  tb_plusargs.txt 反解 11 字段逐项互证 CONSISTENT（旧 run 目录与发布包
  未做任何修改）；②copy2 补丁+拷后哈希复核（不符即 StageFailure 响亮
  失败）已落地；③四规模复跑 adr061_accept2_n{16,60,240,244} 全
  GREEN+published，run 包自包含（两文件入 evidence 且计入 run 级
  SHA256SUMS），拍号与首轮逐拍一致（确定性复现），且在新 C-L 段匹配
  最大值规则下判决不变；④本修复单独提交，旧证据未覆盖。

## 6. 实现记录（2026-10-10）

四件套 + 运行器全部落地（verification_v2/tools/）：
- `unified_lcs_case_gen.py`：D1 布局解析 + D2/D3 自动生成 + 哨兵/零预填
  承重纪律 + HBM 镜像快照 + g3_expect + program_manifest（D5 全字段）。
- `check_program_manifest.py`：①段链/schema ②布局重算 ③a 指令 sha256
  ③b 再发射逐字节 ④预载图案 ⑤终态/HBM 镜像 ⑥canary/标记 ⑦屏障参数
  == 实际文件行数——任一 RED 不发布。
- `arbitrate_probe_logs.py::arbitrate_program`：程序级仲裁（LOAD 窗取指
  ⊆{26,27} + UR 写 ⊆ 声明窗 + 覆盖 == 声明；COMPUTE 段复用 ADR-056/057
  逐拍核心 + C1 末写<ack；STORE 窗取指 ⊆{24,25} + UR 写恒空 + HBM 写
  ⊆ 声明窗 + 覆盖 == 声明；C-L/C-S/C-W 三段间契约）。
- `unified_lcs_compare.py`：Z3 三区泛化（|T| == Σvout0 BODY 行数 +
  LOAD 张量数×n_lines 双源反空洞计数）+ C-H 三方一致（HBM 窗 == golden
  UR 窗 == numpy 独立重算）+ canary/标记泄漏检测。
- `run_unified_lcs_verify.py`：Orchestrator 子类，仅替换 package/vcs/
  three_zone/arbitrate 四段，其余（准入/独立参考/期望先行/原生比对/
  归档/发布门禁）原样继承；vcs 段平移任务 1 纪律（flock + 全局 HBM
  备份 sha256 锚 + hbm_preload.restore 双锚门 + 无条件恢复复核）。
- 测试：tests/unit/test_unified_lcs.py 36 项（chunk_descs×4/布局守卫×10/
  manifest 检查篡改×5/程序仲裁合成日志红绿×13/比对篡改×4——对抗自验，
  含 C-L 同拍降级边界双向钉死、多 release 段匹配最大值规则（2026-10-12
  有界核查）与迟到 LOAD 写不可藏匿专测）。
- **验收④⑤结果全录 §4**（n∈{16,60,240} 单段 + n=244 两段全 GREEN
  published；n=480 既有链不回退；add/scale_sum 回归全 GREEN）。
  验收配置归档 verification_v2/artifacts/run_configs/adr061_*.yaml。

## 7. Errata

### errata-2026-10-12（horner_v3u 步④暴露，当日修复）

1. **C-H 独立参考实现曾硬编码 scale_sum 形态**（`unified_lcs_compare.py` 旧版
   直取布局张量 x/other/alpha 并内联 `alpha*(x+other)`）——越出本 ADR D6 的
   通用设计表述（「独立参考（fp32 逐 lane）」未限定公式）。失败形态：
   horner_v3u n=8 统一链 RED@three_zone「(C-H) 布局缺张量 'other'」
   （run `hv3u_step4_n8_20261012`，保留为失败形态档案）。
   修复 = **公式驱动泛化**：manifest 新增自包含 `formula` 字段；C-H 以
   有序 AST fp32 逐 op 求值（与 V1 链 independent_reference 同 machinery），
   输入按 role 重建（input=全行流、param=stride0 标量驻留——line0 四 lane
   全等 ∧ line1.. 全零的正面形态取证逐参数保留）；公式叶缺/布局多载/
   公式未声明名均响亮 RED。无任何算子名/张量名特判。
2. **manifest `"op"` 字段曾硬编码 `"scale_sum"`**（`unified_lcs_case_gen.py`）——
   provenance 错误（horner case 被记录为 scale_sum）。修复 = 由 runner 传入
   真实算子名（op_yaml 词干）+ 公式串（`op.formula_str`），二者缺失即
   装配响亮拒绝；CLI 同步增加 `--op-name/--formula` 必填。
3. 鉴别性测试：`tests/unit/test_unified_lcs.py` 36→42（新增 horner 形态
   合成 case 双向钉死：GREEN 必须绿；参数区互换/参数形态破坏/缺 formula/
   未声明叶/装载未消费输入五类必须各自 RED）。scale_sum 合成 fixture
   原样通过 = 旧形态零回归。真实链回归：scale_sum 统一入口复跑记录于
   docs/horner_v3_step4_unified_lcs_20261012.md。
