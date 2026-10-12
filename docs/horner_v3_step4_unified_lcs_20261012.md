# horner_v3u 步④统一 LCS 集成报告（2026-10-12）

> 用户令五步④：统一 LCS 集成——horner_v3u（II=6 Horner 长链）经 ADR-061
> 统一入口跑完整 LOAD→COMPUTE→STORE 程序级链路。
> 用户立法（逐字）：**"不能通过新增算子名称特判获得通过。若需要改通用规则，
> 先记录具体失败形态，增加有鉴别力的测试，再修规则并回归 add、scale_sum。"**
> 本步实际触发了一次通用规则修复（C-H 独立参考公式化），全程按该序执行。
>
> 证据分级：【实测】= 当日真实运行/读取；【源码】；【归档】= sha256 锚定产物。

## 0. 结论

**步④通过（经一次通用规则修复）**。首跑暴露 ADR-061 C-H 独立参考的
scale_sum 硬编码缺陷（失败形态 §1 留档）；按用户立法序修复为**公式驱动**
（零算子名/张量名特判）后：horner_v3u n=8（3 段程序）与 n=72（4 段程序）
统一链全 GREEN+published；回归 scale_sum（统一链 n=60）与 add（冻结链
n=4/8/60）全 GREEN+published；pytest 577 passed。提交态闭合复验
（bd2ed7b 干净树）n=8 GREEN+published，发布包内嵌报告与运行报告逐字节一致。

| run_id | n | 程序段 | outcome | published | 代码态 |
|---|---|---|---|---|---|
| hv3u_step4_n8_20261012 | 8 | — | **RED@three_zone**（失败形态留档） | False | 修复前 |
| hv3u_step4fix_n8_20261012 | 8 | LOAD+COMPUTE+STORE | GREEN | False（发布冲突，§5.1） | 修复后脏树 |
| hv3u_step4fix_n72_20261012_codex | 72 | — | **RED**（VCS 许可证失败，留档 §5.2） | False | 修复后脏树 |
| hv3u_step4fix_n72_20261012_codex_r2 | 72 | LOAD+COMPUTE×2+STORE | GREEN | True | 修复后脏树 |
| hv3u_regress_ss_n60_20261012_codex | 60 | scale_sum 统一链回归 | GREEN | True | 修复后脏树 |
| hv3u_regress_add_frozen_20261012_codex | 4/8/60 | add 冻结链回归 | GREEN | True | 修复后脏树 |
| **hv3u_step4fix_n8_committed_20261012** | 8 | LOAD+COMPUTE+STORE | **GREEN** | **True** | **bd2ed7b 干净树（闭合数据点）** |

## 1. 失败形态记录（修复前，留档）

首跑 run `hv3u_step4_n8_20261012`（已保留，未删未覆盖）：**RED@three_zone**，
比对器报 `(C-H) 布局缺张量 'other'`（arbitrate 段随之失败）。

根因（两条，均属 ADR-061 实现越出其通用设计表述）：

1. **C-H 独立参考硬编码 scale_sum 形态**：`unified_lcs_compare.py` 旧版直取
   布局张量 x/other/alpha 并内联 `alpha*(x+other)` 重算。ADR-061 D6 的设计
   表述为「独立参考（fp32 逐 lane）」并未限定公式——实现窄于设计，遇第二个
   算子即破。
2. **manifest `"op"` 硬编码 `"scale_sum"`**：`unified_lcs_case_gen.py` 旧版
   对 horner case 也写 `"op": "scale_sum"`——provenance 记录错误（静默错标，
   不致 RED 但污染证据链）。

## 2. 鉴别性测试（先于规则修复）

`tests/unit/test_unified_lcs.py` 36→42，新增 `UnifiedLcsCompareHornerTest`
6 项（horner 形态合成 case：x@bank0、c0..c3@banks1/3/4/5、constants@bank7、
y@bank2，formula=`((c0 * x + c1) * x + c2) * x + c3`）：

| 测试 | 钉死方向 |
|---|---|
| horner_green | 修复后形态必须 GREEN（含 input_modes 记录） |
| param_region_swap_red | 两参数区互换 → 必须 RED（叶值错配不被静默） |
| param_form_violation_red | 参数区 line1.. 出现非零（非 stride0 标量驻留形态）→ 必须 RED |
| formula_missing_red | manifest 缺 formula 字段 → 必须 RED（无公式之源不猜） |
| formula_undeclared_leaf_red | 公式引用布局未声明的叶 → 必须 RED |
| extra_input_not_referenced_red | 布局装载了公式未消费的输入 → 必须 RED（装载而未消费不静默放过） |

scale_sum 既有 36 项合成 fixture 原样通过 = 旧形态零回归（测试层）。

## 3. 通用规则修复（3 个工具文件，零名称特判）

commit **bd2ed7b**（5 文件，380+/31-；另含 ADR-061 §7 errata 与测试）：

1. **`unified_lcs_compare.py::_c_h` 重写为公式驱动**：公式取自 manifest
   自包含 `formula` 字段（缺失即响亮 RED，不猜）；经
   `parse_ordered_formula` + `reference_generator.evaluate` 有序 AST fp32
   逐 op 求值——与 V1 链 independent_reference 同一 machinery、与硬件流水
   同序；输入按 role 从 memory_init1 重建（input=全行流；param=stride0
   标量驻留，line0 四 lane 全等 ∧ line1.. 全零**正面形态取证**，越出即 RED）；
   公式叶缺 / 布局多载均响亮 RED。全程无算子名、无张量名特判。
2. **`unified_lcs_case_gen.py`**：`build_unified_case(..., op_name=, formula=)`
   必填（空值装配即拒），manifest 写真实 `"op"`/`"formula"`；CLI 同步
   `--op-name/--formula` 必填。
3. **`run_unified_lcs_verify.py`**：runner 传真实身份
   （`op_name=Path(op_yaml).stem`、`formula=op.formula_str`）。

ADR-061 §7 新增 errata-2026-10-12 三条登记（含测试数 36→42）。

## 4. 修复后实测（统一链全 GREEN）

### 4.1 n=8（3 段程序，提交态闭合数据点）

run `hv3u_step4fix_n8_committed_20261012`【实测】：

- provenance：`git_head=bd2ed7b07cbe7844caa9279e262963cd41196a71`、
  `git_dirty=false`；TB sha256 `3324a33b04bcaf75af310b98e676662ea8908f693d6edcec0875745373b7f7c5`；
  RTL 板端期望 `0x00042200`（VCS 侧不断言该寄存器，口径见 ADR-061 D7）。
- 10/10 stage 全 pass；arbitrate GREEN、0 violations、4 项 limitation
  （C-L/C2 同拍各两条 @546/@8516——ADR-057 errata 空窗口径，登记不遮蔽）。
- 段级：LOAD [12,546] UR 写 12/12 声明行（6 张量×2 行）、末写@487；
  COMPUTE [546,8516] 末写@8460 < ack@8516；STORE [8516,…] HBM 写 2/2 行、
  末写@8672、ack@8714。
- C-L 严格序实证：首读@8408 > release@546 > LOAD 末写@487（余量三个数量级；
  同拍 limitation 仅涉 release==BEGIN，不影响严格大于判定）。
- C-H 三方一致：HBM 输出窗 == golden UR 窗 == 公式有序 AST 独立重算
  （`((c0 * x + c1) * x + c2) * x + c3`，输入=memory_init1 按 role 重建，
  c0..c3 均经 stride0 形态核验）。
- 发布包 `releases/horner_v3u_hv3u_step4fix_n8_committed_20261012` 内嵌报告
  == 运行报告逐字节（sha256 40d1e79d8785b2bc…）。

### 4.2 n=72（4 段程序：LOAD+COMPUTE×2+STORE，跨容量分段）

run `hv3u_step4fix_n72_20261012_codex_r2`【实测】（另：同内容
`hv3u_step4fix_n72_20261012` 亦 GREEN+published）：

- manifest：schema adr061_program_manifest_v1，`op=horner_v3u`、
  `formula=((c0 * x + c1) * x + c2) * x + c3`（修复②provenance 正确）。
- 段级：LOAD [12,854] UR 写 108/108（6 张量×18 行）、末写@799；
  COMPUTE₁ [854,35836] 末写@35768 < ack@35836；
  COMPUTE₂ [35836,42006] 末写@41950 < ack@42006；
  STORE [42006,…] HBM 写 18/18 行、末写@42237、ack@42294。
- arbitrate GREEN、0 violations、6 项 limitation（C-L/C2 同拍 ×3 段间过渡
  @854/@35836/@42006，同上空窗口径）。
- C-L：首读@35626 > release@854 > LOAD 末写@799。
- C-H 三方一致（同 n=8 公式与重建路径）。

### 4.3 回归（用户立法第④步）

| 回归 | run_id | 结果 |
|---|---|---|
| scale_sum 统一链 n=60 | hv3u_regress_ss_n60_20261012_codex | GREEN+published（C-H 走新公式路径，旧形态判决不变） |
| add 冻结链 n=4/8/60 | hv3u_regress_add_frozen_20261012_codex | 三规模 GREEN+published（该路径不引用被改模块） |
| pytest | — | **577 passed** / 7 skipped / 11 xfailed / 148 subtests |

## 5. 事件与留档记录

### 5.1 发布冲突（门禁按设计工作）

run `hv3u_step4fix_n8_20261012`（修复后首个 n=8 GREEN）：发布被
`RuntimeError: 发布路径已存在（run_id 冲突）` 阻断，run_report 记录
`published=false` + publish_error 原文。既有 release
`releases/horner_v3u_hv3u_step4fix_n8_20261012`（同日较早一次执行所留）
**未删除、未覆盖**。同 run_id 不二次发布 = 门禁预期行为，非缺陷。

### 5.2 VCS 许可证失败（留档）

`hv3u_step4fix_n72_20261012_codex`：RED（沙箱内 VCS 许可证连接失败），
published=false，**保留为环境失败档案**（未删）。其后经获准的沙箱外通道
复跑（run `_codex_r2`）GREEN+published。该 RED 与 DUT/编译产物无关。

## 6. Provenance 声明（按用户 2026-10-12 裁定的口径）

**脏树运行**（§4.2、§4.3 全部及修复后首个 n=8）：运行时 provenance 锚定了
op_yaml/run_config/TB/registry 哈希，但**未保存被改 5 个工具/测试文件的源码
哈希**——故**无法从运行记录自证「运行码与提交码完整恒等」**。辅助证据链：
(a) 5 文件 mtime 12:57:02–13:11:17 均早于首个修复后 GREEN 运行（13:16 UTC+8
前后，A.1 核验时点确认）；(b) A.1 时点 `git status` 恰好此 5 文件、无其他
改动与未跟踪文件（gitignored run_configs 除外）；(c) commit bd2ed7b 恰含此
5 文件（380+/31-），提交后工作树干净。局限：mtime 与 status 快照非密码学
绑定——**闭合数据点 = 提交态 n=8 复验**（§4.1：bd2ed7b、dirty=false、
GREEN+published、包内报告一致）。

**n=72 条件复跑条款**（用户裁定原文落实）：n=72 两次 GREEN 均为脏树运行，
其码态依赖上述辅助链；**若之后有影响该执行路径的功能改动，须以提交态
补跑 n=72**（本条款列入步⑤收口待办跟踪）。本轮无后续功能改动，暂不补跑。

## 7. 口径与限制（落账）

- 同步 = TB 辅助（逐段装载/触发/等 done/清中断）；**不宣称 host 自主闭环**，
  STORE 外部完成仍由 TB 屏障佐证（F6 线既知边界，ADR-061 D7 口径不变）。
- 证据范围 = 已测布局（x@bank0/c0..c3@bank1,3,4,5/constants@bank7/y@bank2）、
  单 SMC、n∈{8,72}、RTL 0x00042200 + TB 3324a33b…。**一个 II=6 案例通过
  ≠ 任意 II=6 kernel 已证明**（覆盖三档分层见步⑤收口文档）。
- C-H 新公式路径的已证形态：有序 AST 可解析的逐元素公式 + role∈{input 全行流,
  param stride0 标量驻留} 输入重建；越出（非常量引用、非驻留形态参数、
  归约/矩阵等）即响亮 RED 而非静默近似。
- 修复前后 COMPUTE 发射链零改动（本步只动比对/装配/manifest 与测试）。
