# HCO 审查报告 · ADR-056 修订稿 v2（V1 支持范围定义与受限通用准入规则）

> 归档说明：本文件是 2026-09-26 hco（以 general-purpose 代理 + `.claude/agents/hco.md` 角色文件方式运行）
> 对 ADR-056 v2 的完整审查报告原文。must-fix M1–M5 与 notes 已全部落入 ADR-056 v3 本体；
> 本文件留存裁决依据与回源清单。审查用时报表见文末。

审查对象：/home/juno1023/aiacc_compiler/docs/adr/ADR-056-v1-scope-constrained-general-admission.md
审查基准：稿内每条引证均回源（代码/YAML/快照/RTL），未采信稿内转述；另对本稿三个待决项各做独立取证。RTL 沙箱 /home/juno1023/u280_sim_v422 只读全程遵守。

## 一、总体判决

**APPROVE-with-notes： must-fix M1–M5 修订后方可进入实现。**

全部 must-fix 均为文档层修订与清单补列（改稿文/补落点/写成文规则），不需要推翻任何机制设计；三层准入模型、八闸门、§C 独立参考方向、§D 期望先行链、§E/§F 验收结构在机制层面均成立，且与已核对的代码/YAML/快照/RTL 事实相容。用户五条硬约束逐条核对：不放宽既有守卫 ✓（G-stride 按 errata I1 现形、wrap 保守 ≥depth 同拒）；不容忍静默回落 ✓（§B.2 明禁失败回落 embed）；FILL 254/255 环绕不被误杀 ✓（§F.5 受控允许 + G-addr 仅 LIVE 越界拒绝，add 实测 R_q≪256 不触发）；FP32 容差不计通过 ✓（§F.1 明文）；add 迁移零字节 ✓（§B.3）。本稿不涉及 hw_config 数值变更。

## 二、must-fix 表（修订后方可实现）

| # | 内容 | 落点 | 修法 |
|---|---|---|---|
| M1 | **§B.2 G-II 措辞与代码不等价**。ADR 写「iq_II<2 不是失败而是分流，归 ADR-054 平坦压缩分支」，但 `src/driver/assembler.py:314` 平坦分支是**双条件** `len(kernel_map)==1 and not is_smi_ds_t_dp and iq_II==1`；II=1 但多槽（或 smi-ds-t）的算子现状是落入 `:355-366` 请求分支后按 `iq_II<2` **响亮拒绝**（:363-366），不是分流平坦。按现措辞实现会与现状行为的边界正面冲突。 | 送审稿 §B.2 G-II | 改写为：「`iq_II==1` 且单槽且非 smi-ds-t → 分流平坦分支（assembler.py:314 双条件，mul 已证路径）；`iq_II==1` 其余形态 → 与 `iq_II<2` 同例响亮拒绝」。引用处补上 :314 行号。 |
| M2 | **§B.4 触点清单漏列 §A.2-1 原始文本 AST 闸的落点**。「触点诚实清单」目前 6 条不含一元取负语法闸的实现位置；`verification_v2/ops/configurable.py:35` 现只有 `sp.sympify`（SymPy 会把 `-x` 规整为 `Mul(-1,x)`，稿内自己也指明了改写后不可区分），故必须新增文本级 `ast.parse` 工序，且它有独立契约测试需求（`x+(-2.5)` 允许 / `-x`、`-alpha`、`-(x+y)` 拒绝）。清单自称「不是只有一处」却漏了这批新工序，实现期必漏估。 | 送审稿 §B.4 表 | 表内增一行：「原始公式文本 AST 闸（§A.2-1）——configurable.py 前端新工序 + 契约测试（负字面量允许 / 表达式取负四类拒绝）」。 |
| M3 | **§C.3 缺新参考发生器的自身锚定工序**。已实证旧链 golden = Python simulator 终态 dump（`sim/smc_simulator/tools/physical_runner.py:143-144` dump_banks → package_vcs_case.py:15 以 output_bank 为 golden 侧），§C.3 改向正确；但 C.2 参考发生器替代 sim-golden 后自身无外部锚：它的第一个服务对象是新公式，出错无人发现。 | 送审稿 §C.3 / §F | 加一条验收前置：C.2 发生器先对 **add 公式**输出 golden，与现行 sim 中转 golden **逐位一致**（三规模），不一致即 RED；锚定通过后方允许用于 scale_sum 及后续公式。 |
| M4 | **§D.4 投影规则缺 FETCH/ISSUE 双层语义定义，「压缩形 LOOPS 重复 vs 展开形逐行」未规定映射**。本审查已从 RTL 定案（详见待决项③意见）：压缩行 = 1 次取指 + (w_loops+1) 拍内部回放；若投影函数把压缩行误建模为「每迭代 1 fetch」则对 G-II 平坦分流的合法算子系统性判红/判绿。且 flat op 行的 ISSUE 层逐拍形态迄今无任何 VCS 探针证据（mul M1 仅有终值 COMPARE）——D.4 自承的「探针/监测自验」清单未点名此缺口。 | 送审稿 §D.4 | 成文双层投影：FETCH 层 1:1 对指令行（信号锚 `w_rdi_addr_valid`，smi_iq.v:165）；ISSUE 层压缩行展开为 w_loops+1 拍（信号锚 `w_dly_data_valid`，smi_iq.v:170 实存）；INIT 行 FETCH=1、ISSUE=l_init+1 拍（A1 c1−c0=4 已实证）；TERM/JUMP 沿用 __probes 残尾语义；气泡行各 1+1。并把「flat 形 ISSUE 层逐拍断言」列入 D.4 自验清单与 §F.1 门槛。 |
| M5 | **§F.5 `length` 字段的单位与静态语义未定**。§F.5 定义「张量声明区 = [offset, offset+length)」但 (a) 单位未写（UR word = 128-bit 还是 fp32 元素？地址域 254/255 是 word 单位）；(b) 与三规模动态性的关系未定——add 三规模 8/60/4 共用一份 YAML，若 length 为静态值则其语义必是「分配上界」，§F.2 的「布局容量界」才有良定义；(c) 缺省行为未定。 | 送审稿 §F.5 / §A.2-4 | 成文：length 单位 = bank 内 UR word；语义 = 该张量在 bank 内静态分配上界；`experimental_unroll: true` 算子 length 必填，缺省或实际消耗（n_rows/4 words）越出 → 诊断拒绝；对未请求算子的存量 36 份 YAML 不做要求（向后兼容由 §B.4-5 锁定）。G-addr 在「最大声明规模」上做 LIVE 越界拒绝。 |

## 三、notes（非阻断，建议采纳）

1. **§D.3 冻结机制的「冻结清单」未枚举**：哪些相位常数属于冻结对象未列明。建议冻结表至少枚举：INIT 垫拍公式（`l_init = skip·II + 1`，+1 的来源见 ADR-055 M7）、TERM 相位公式（`l_init+1+II·R_q`）、残尾取指 __probes 语义、(i)–(iv) 断言形式。载体建议 tools 侧版本化注册表（不放 hw_config——它是协议模型常数而非硬件事实），冻结记录（名称/值/RTL 推导出处行号/适用形态边界）以 sha256 入库且**先于**首次验收运行；这同时堵住「探索期先看 trace 再回 RTL 找支持」的 confirmation-bias 通道。
2. **add/scale_sum 张量集描述漏列 constants 条目**：`ops/add.yaml` 与 `ops/scale_sum.yaml` 均含 `constants(bank7, constant, 0)` 常驻条目；§2 写「三张量」、§E.1 写「同构」均未列。不改结论，建议措辞补全以免后续 diff 工具把 bank7 行当差异点。
3. **§A.2-1 一元正号 `+x` 未表态**：`ast.parse` 下 `+x` 是 `UnaryOp(UAdd)`，建议与一元取负同条款明确（允许或拒绝均可，成文即可）。
4. **伪造字段检测需要闭集校验**：configurable.py 加载是 `yaml.safe_load` 裸 dict（:15），拼错的 `experimental_unrol` 会被静默忽略。§B.4-4 已列「伪造字段检测」是对的；建议实现为键集合 allowlist 校验（allowlist 从存量 YAML 键并入集冻结构成），未请求算子的存量文件不受影响。
5. **§D.2 SOLVER_UNSAT / SOLVER_UNKNOWN 分列需 solver 侧三态传递**：现求解失败路径对外是单一失败信号，z3 的 unsat/unknown 原文需沿 verdict json 带出，实现时验证两代码确实可分别触发。
6. **FILL/DRAIN 读可能落入同 bank 邻居声明区**：紧排布局下 FILL 环绕地址（如 255）可能恰属同 bank 另一张量声明区——读不污染输出，三区机制不抓读侧，属允许行为，但建议在 §F.5 加一句明示，避免后续审查反复。
7. 发现的额外名称弱耦合点（auditor.py:590 trace_id 子串启发式、main_verify.py:77 CATEGORIES、census_iq_shapes.py:42 不传 top_op、test_emit_expected_fire_sequence.py 与 run_add_*.sh 硬编码 `--op add`）均不构成准入触点，无需改动；更名 add 时体检即可。`A1_UNROLL_ALLOWLIST` 值域 "experimental"/"verified" 在 assembler 外无消费方，删除安全。

## 四、三个待决项的独立意见

### ① §C.4 输入数值域（hco 建议案，可直接定稿）

**允许域定义**：`D = {+0, −0} ∪ { v ∈ binary32 : 2^(−126) ≤ |v| ≤ (2−2^(−23))·2^127 }`——即排除 ±inf、NaN、全部亚正规；±0 允许。排除亚正规的理由必须写进文档：RTL FPADD/FPMUL 是否 flush-to-zero/是否支持亚正规**从未被验证**（VCS 证据全部来自 [−10,15] 量级数据），V1 域内规避该未证行为比验证它便宜；若后续放宽须先补 RTL FTZ 行为专项。

**检查工序＝双道且两道都过**：
- (a) 编译期静态：对 §C.2 有序语法树逐节点做 IEEE-aware 区间传播（每节点向外 1 ulp 舍入；输入区间为声明的采样域）。拒止规则：任一内部节点区间越出 ±FLT_MAX → 溢出风险拒；区间与 (0, 2^−126)∪(−2^−126, 0) 相交且不能静态证明恰为单点 {0} → 亚正规风险拒。常量/参数在装载时先 RTNE 舍入为 binary32，舍入后须在 D 内。
- (b) 装载数据核验：§C.2 参考发生器在**实际装载数据**上对有序树逐步执行（这本就是 golden 产生工序），逐节点精确判定在 D 内——无区间保守性，是实际数据的完备判定。
- 关系：AND，非 OR。静态道失败但动态道通过 → **仍拒**（诊断建议缩小 test_data.range），这防止「换一批合规采样就翻案」；静态道的价值是把验收从「采样碰巧安全」提升为「采样域内可证安全」。用户指出的「全正数相加溢出」「混合号抵消至 0/亚正规」两例分别由 (a) 的两条拒止规则覆盖；「仅查 test_data.range 不充分」的批评由 (b) 的逐节点核验闭合。

**失败行为**：§C.4-3 已写对（TYPE_LAYOUT_UNSUPPORTED + 不假绿），补：诊断必须带语法树节点路径与区间记录；测试数据生成器加装前域内过滤+越域样本立即响亮失败而非裁断。

**V1 边界建议**：中间结果上界以区间传播给出即可（不必手算全局公式）；对采样域建议默认压到 |x| ≤ 2^16（3 节点树中间值裕量充足，add 现行 [−10,10]/[−5,15]×2.5 远在内）；对 8/60/4 三规模均适用。

### ② §D.3 相位常数冻结机制

机制方向**成立，且已有真实锚点**：「期望先行」不是新承诺而是 A1 既有机制的延续——`emit_expected_fire_sequence.py` 不 import assembler 任何内部、地址刻意写成与 assembler 不同式、l_init/R_q/槽序由各队列 t 集合独立重算且不读产物 INIT 行；`check_iq_probe_trace.py` 从 schedule 反算相位与 trace 实测比对。期望（schedule+公式）与观测（RTL trace）的产物链路独立，循环论证在主链上已被结构封死。

残留缺口三处（均不推翻机制）：(1) 冻结对象未枚举、冻结载体未定——见 notes-1，这是机制落地前必须补的最后一笔；(2) 继承边界：`l_init = skip·II+1` 公式族在 A1 只被 II=2 两类槽形态实证，§E.3 已诚实承认 II>2 未证——冻结表必须给每个常数登记「已证形态边界」，新形态上的首个值只能走探索→独立论证→追加冻结，不得直接引用；(3) 反向通道对称性：「trace 不符不许回改常数」正确，还应明写「不得通过改动 schedule 侧的输入拟合」——schedule 是 solver 产物、属合法上游，但同一验收运行内的 schedule 一旦变更，期望全部随动，须在协议中写清变更即全新运行记录。

结论：**机制可验收，冻结清单 + 入库时序两条成文后即闭合。**

### ③ §D.4 统一事件格式、投影规则与全局监测

**本审查新增的 RTL 实证（非稿内转述）**，直接决定投影规则的正确建模：

- `smi_iq_iic.v:253` 与 `:262`：`w_loops` 与 `w_embed_lev_loop` 是**同一 LOOPS 字段的两位名**，因此带 loops 的行为两类：level-0 行（w_embed==0，`EMBED_LOOPS` 域=0，见 ADR-055 E7）→ **EXE_EMBED0_INST** 数组回放（组合转移 :443-471，顺序体 :662 起）；level=7 哨兵行（w_embed==7）且 loops≥1 → **LOOP_CYCLE**（:483-488、:1194-1216）。两条路径共同不变量：**1 次取指 + (w_loops+1) 拍内部回放；回放拍 `rdi_addr_valid ≤ 0`（:1198/:1200）、`dly_data_valid` 每拍脉冲；末拍 `rdi_addr_valid ≤ 1` 取下一行（:1209-1212）**。
- 展开行（level=0, loops=0）：每行 1 fetch + 1 拍 issue，严格连续——A1 断言 (i)(ii) 已实证。
- **INIT 行（level=0, loops=l_init=3）走同一 EXE_EMBED0_INST 回放，回放拍数 = l_init+1 = 4**：A1 实测 c1−c0=4=l_init+1 正是该机制的逐拍证据（INIT 仅 1 次 addr_valid、间隙恰 4 拍）。即压缩回放机制本身已有 A1 实证，「垫拍与槽数无关」由此获得 RTL 级解释。
- **但 flat op 行（loops=N−1, level=0）的 ISSUE 层形态——N 拍脉冲、地址逐拍按 INC 推进——从未被 VCS 逐拍探针实证**（mul M1 只有终值 COMPARE 2048/2048；A1 探针仅 u_iq_16/19 且只做 FETCH 层断言）。任何一个 II=1 新公式（G-II 分流）都会走该形态——这是 must-fix M4 的实证依据。

**投影可证伪性评估**：(cycle|order, iq, kind, payload) 框架正确；kind 必须至少两级 {FETCH, ISSUE}，否则压缩形无忠实映射（M4）。全局活动监测用 FETCH 层信号（`w_rdi_addr_valid`，smi_iq.v:165）逻辑上闭合：内部回放不取指，而任何队列的首拍活动必然是取指——**「并集外队列取指脉冲即 RED」成立的前提是首拍必被捕获，探针自验清单应含一条「人为触发一次并集外 fetch 并确认被 RED 捕获」的对抗用例**。残尾取指投影仅限并集内队列（A1 __probes 语义），须写入规则。

**信号选点与 RTL 一致性**：四信号（w_rdi_addr_valid / w_rdi_addr / w_rdi_data_valid / w_rdi_data，smi_iq.v:165-168）核对一致；ISSUE 层建议补锚 `w_dly_data_valid`（smi_iq.v:170 实存）。**布针须跨三族实例层级**：平铺显式实例 `u_iq_0..27/92/93`（现探针 `dut.u_smi_top.u_iq_16/19` 即此族，tb_aiacc_top_v04_f_multi_stage.v:2442-2485）、generate 域 `gen_iq[28..66].u_iq`、`gen_iq_bridge[68..70].u_iq`；stub 位（67、71-91，无 smi_iq 实例即无 w_rdi_* 信号）显式排除并记录。故「全部 IQ」工程口径 = SMI_IQ_CNT=94（smi_top.v:21）减去 stub；容量墙 128（smi_top.v:22）无误。**监测盲区不可自知**——布针枚举须由工具自动生成三族列表并与 smi_top 实例核对，漏针即测试设施自身 RED。

**cycle 域成文要求**：TB 计数器 `iq_probe_cycle` 复位起 0、每拍 +1、rst_n 后启动（tb:2453-2458）已可锚定；调度事件只有相对 t，对齐锚 = 每队列首个 FETCH（现行 checker 做法）+ l_init 相位断言，写入规则即可。

## 五、§B.4 触点核对结论（全库检索）

六条触点全部真实存在、引证准确：

1. assembler.py:10-14 ✓（:10 白名单本体、:11 闭集本体、:12-14 导入期自检——稿引 :12-14 准确）。
2. assembler.py:355-366 ✓（:358 判定入口、:359-362 smi-ds-t 拒绝、:363-366 iq_II<2 拒绝）；另核 :285-294 kernel_map 槽冲突 raise、:379-392 stride/wrap 守卫、:464-474 容量 raise、:475/:479 branch_report——均与稿内职能描述一致。**但 G-II 的现态行为出口在 :314 双条件（M1 依据），稿未引。**
3. pipeline_runner.py ✓（:6 import、:15-27 normalize_top_op、`hw_parity_` 前缀闭集、:136-144 M2 兜底、:145-157 M3 双向响亮失败、:160-161 传参、:186-191 branch_report 落地）。
4. configurable.py + YAML ✓（:15 safe_load 裸 dict、:35 sympify 唯一文本通道——AST 闸是纯新增工作=M2 依据；schema 无校验→伪造字段检测确有必要）。
5. 默认行为零改动 ✓（embed 主线不读任何新字段；census_iq_shapes.py:42 不传 top_op，天然绕过 M3 闸——设计内）。
6. 测试面 ✓（test_rtovr_sparse_guards.py: g14b:219、g3_add:473、超容量响亮拒绝:479、g14_add_unroll_stream_protocol:512、m16-m20:657-757；test_smt_hw_parity.py:67 `hw_parity_` 前缀渠道）。

**未遗漏真实准入触点**；弱耦合点见 notes-7。

## 六、稿内引证 vs 源·核对汇总（无漂移）

- assembler.py:10 / :12-14 / :285-294 / :355-366 / :359-362 / :465-474 — 全部一致。
- main_verify.py:88-94（按 yaml 文件名建 OPERATOR_MAP）— 一致。
- census 快照 census3_progress.txt：36 行 + DONE，8 UNSAT（addcdiv/elu/gelu_approx/hardswish/log2/matmul/matmul_parallel_n/selu）；28 解出 = mul(II=1,fma) + fma_parallel_n(II=1) + add(II=2 17 embed) + 25 个 II≥3（II=3:1、II=4:5、II=6:5、II=7:1、II=8:6、II=9:5、II=11:2——25 逐格与 §2 一致）；§G 的 27→25 订正成立。
- A1 验收文档：三规模 l_init 恒 3、c1−c0=4、TERM ur-r0=8=4+2·2 / ur-r3=12=4+2·4、FILL ADDR 254/255、残尾 __probes、mul 零字节 13/13、mul VCS 回归 PASSED——全部与 §2/§F.5/§G 引用一致；mul「VCS 非硅证」订正成立。
- ADR-055 errata I1（:493「仅 stride≠0 子集一致」+ assembler.py:379-383 + add ur-r3 混合 stride 动机）— §B.2 G-stride 引证准确，且措辞遵守了「引用 R_q 公式不得省略 eff_num_iter 来源限定」（§F.5 合规）。
- 四信号 smi_iq.v:165-168 ✓；容量 smi_top.v:22 =128 ✓（:21 =94）。
- golden 中转 §C.3 担忧属实（physical_runner.py:143-144 + package_vcs_case.py:15）。
- 瑕疵仅两处：M1 的 G-II 措辞、notes-2 的 constants bank7 漏列。

## 七、实际核对过的源文件清单

**送审/规范文档**：
- /home/juno1023/aiacc_compiler/docs/adr/ADR-056-v1-scope-constrained-general-admission.md（全文）
- /home/juno1023/aiacc_compiler/docs/adr/ADR-055-assembler-explicit-slot-unroll-emission.md（D2/R_q 公式段、断言段、errata 表 E1-E9/I1-I2、must-fix M1-M7）

**编译器代码**：
- /home/juno1023/aiacc_compiler/src/driver/assembler.py（:10-14、:103-105、:285-294、:314、:355-366、:379-392、:399-474）
- /home/juno1023/aiacc_compiler/verification_v2/infra/pipeline_runner.py（:6、:15-27、:136-191、:251、:255-271）
- /home/juno1023/aiacc_compiler/verification_v2/main_verify.py（:76-94）
- /home/juno1023/aiacc_compiler/verification_v2/ops/configurable.py（:15-44、:206-268、:297-325）
- /home/juno1023/aiacc_compiler/verification_v2/infra/auditor.py（:590）
- /home/juno1023/aiacc_compiler/verification_v2/tools/census_iq_shapes.py（:42）
- /home/juno1023/aiacc_compiler/verification_v2/tools/emit_expected_fire_sequence.py（全文）
- /home/juno1023/aiacc_compiler/verification_v2/tools/three_zone_compare.py（全文）
- /home/juno1023/aiacc_compiler/verification_v2/tools/check_iq_probe_trace.py（读毕）
- /home/juno1023/aiacc_compiler/sim/smc_simulator/tools/physical_runner.py（:143-144）、package_vcs_case.py（:15）

**YAML / 测试 / 快照**：
- /home/juno1023/aiacc_compiler/verification_v2/ops/add.yaml、scale_sum.yaml、mul.yaml
- /home/juno1023/aiacc_compiler/tests/unit/test_rtovr_sparse_guards.py、tests/integration/test_smt_hw_parity.py:67、tests/unit/test_emit_expected_fire_sequence.py
- /home/juno1023/aiacc_compiler/verification_v2/artifacts/census_iq_shapes_20260926_a1/census3_progress.txt（逐行）
- /home/juno1023/aiacc_compiler/verification_v2/artifacts/e2e_add_20260926/A1_VCS_acceptance_20260926.md

**RTL 沙箱（只读）**：
- /home/juno1023/u280_sim_v422/smi/smi_iq.v（:155-175 四信号 + w_dly_data_valid:170）
- /home/juno1023/u280_sim_v422/smi/smi_top.v（:21-22 计数/容量；实例化三族层级）
- /home/juno1023/u280_sim_v422/smi/smi_iq_iic.v（:111-114 状态、:143/170/253/262 寄存器与位域、:425-495 组合转移、:587-720 顺序 FETCH_DECODE/嵌入态、:1140-1230 LOOP_CYCLE/JUMP_ADDR）
- /home/juno1023/u280_sim_v422/sim_multi_stage/tb_aiacc_top_v04_f_multi_stage.v（:2442-2485 现有两队列探针）
