# 复杂结构案例算子：只读静态预检（2026-10-10）

> **任务**：编译器产物→硬件 RTL 闭环验证主线（progress_log §5「结构扩张（新槽形态/II>2 公式）」步）
> 选一个「复杂结构案例」算子——**真正 II>2 或同队列多有效槽的长链公式**。
> 本文 = 只读静态预检：不改任何现有文件；从已有候选筛 1-2 个并逐项预检。
>
> **证据分级标签**（逐条标注，不混称）：
> - 【归档】= 仓库内冻结归档（artifacts/ADR，含 sha256 或正式验收记录）
> - 【源码】= 本预检直接读出的代码/配置文本（文件:行号）
> - 【实测】= 本预检当日（2026-10-10）在内存中运行的求解器/参考门结果
>   （方法同 `a2b_preflight.py`，未写盘、未跑发射链与 VCS；可复现）
> - 【推断】= 由以上证据外推的判断，未独立证实
> - 【未校准】= 容量预测，纪律：仅 scale_sum n_max=240 为已校准点，其余一律不得作准人数值
>
> **预检环境**：repo venv python（SymPy 固定版本，ADR-007 pin）；probe 方法 =
> `get_dfg → apply_rtovr_sparse_bypass → solve_with_units(MII=1, MAX_II=64)`，
> 与 `verification_v2/tools/a2b_preflight.py:56-72` 同路径；
> 方法学正确性由两个已知结果逐格复现背书（见 §2.3 对照）。

---

## 1. 候选清单（ops/*.yaml 全量扫描 + 文献候选）

现存 yaml **38 份**【源码：`ls verification_v2/ops/*.yaml`】；另 `main_verify.py:19-74`
OPERATOR_MAP 注册手搭算子（matmul 族/horner/sin*/manual_selu/unit_*），
其中 `matmul_parallel_n` 显式注册 `ManualMatmulParallelNOperator`（`main_verify.py:35-36`）
覆盖同名 yaml 的自动扫描（`main_verify.py:89-90` 同名跳过）。

### 1.1 公式结构摘要（按 II 分档；II 值来源见 §2）

| 档 | 算子 | 公式（原文） | 计算节点 | 链长 | II |
|---|---|---|---|---|---|
| 已 VCS 验证 | mul | `x * other` | 1M | 1 | 1（flat）【归档】 |
| 已 VCS 验证 | add / add_v1 | `x + alpha * other` | 1M+1A | 2 | 2（unroll，双槽×3）【归档】 |
| 已 VCS 验证 | scale_sum | `alpha * (x + other)` | 1A+1M | 2 | 2（unroll，双槽×3，同 add 包络）【归档】 |
| **主候选** | **horner_v3** | `((c0*x+c1)*x+c2)*x+c3` | 3M+3A | **6 级串行** | **6**【归档 census】【实测复核】 |
| **备选（新配置）** | **horner_quad（暂名）** | `((a*x+b)*x+c)` | 2M+2A | **4 级串行** | **4**【实测】 |
| 同族可选 | sigmoid_taylor / silu_taylor | 3 项 Taylor（Horner 形） | 3M+2~3A | 5-6 | 6【归档】 |
| 同族可选 | cos4 / cosh4 / sinh5 | 4-5 项 Taylor | 4-5M+4-5A | 7-8 | 6/6/7【归档】 |
| 同族可选 | tan7 / tanh7 / div / log / sqrt / rsqrt | 4-8 项 Taylor | ≤8M+7A | ≤12 | 8【归档】 |
| 同族可选 | expm1 / log1p / mish / hardsigmoid / silu_approx | 5-6 项（含 Min/Max 者另计） | ≤6M+5A | ≤9 | 9【归档】 |
| 同族可选 | exp5 / reciprocal | 6-7 项 Taylor | ≤7M+6A | ≤11 | 11/12（A2b）【归档】 |
| 短链可选 | addcmul | `x + value * t1 * t2` | 2M+1A | 3 | 3【归档】 |
| 语法越界 | abs / clip / leaky_relu / relu6 / relu_offset / silu_approx / hardsigmoid / elu / selu / gelu_approx / hardswish / addcdiv | 含 `Abs/Piecewise/Max/Min` | — | — | 4-9 或 UNSAT【归档】 |
| 手搭长线 | matmul_parallel_n | `a0*b0+a1*b1+a2*b2+a3*b3`（addtree 自环） | 1M+addtree | loop-carried | **≥4（rec-MII）**【源码 `matmul_op.py:407-412`】 |
| 文献候选 | ADR-056 §E.3 备选第二公式 | `a*x*x + b*x + c` | 3M+2A | 4 | **UNSAT（全 II≤64）**【实测，§2.4】 |
| 未解（census UNSAT） | addcdiv / elu / gelu_approx / hardswish / log2 / matmul / matmul_parallel_n(cfg) / selu | — | — | — | UNSAT【归档】 |
| 未普查 | scale_sum、add_v1 | — | — | — | 见 §2.2（census 时点缺席） |

结构判读：M=乘法节点数，A=加法节点数；「链长」= 单迭代关键路径上的计算节点数
（add/scale_sum=2；addcmul=3；horner_quad=4；horner_v3=6；tan7=12）。
真正同时满足「II>2 ∧ 长链 ∧ 多有效槽」的族 = **Horner/Taylor 单输入链**；
`a*x*x+b*x+c` 形（多乘积项求和）实测结构性 UNSAT（§2.4），matmul_parallel_n 为
loop-carried 长线但携多 SMC + embed 主线 + 未闭合债务（§3.4）。

---

## 2. II 普查结果复核

### 2.1 普查基线（【归档】）

- 快照：`verification_v2/artifacts/census_iq_shapes_20260926_a1/census3_progress.txt`
  （A1 代码下重跑，36 算子 / 28 解出 / 8 UNSAT；ADR-056-hco-review-20260926.md:91 逐格核对：
  28 = mul(II=1) + fma_parallel_n(II=1) + add(II=2) + 25 个 II≥3：
  II=3×1（addcmul）、II=4×5（abs/clip/leaky_relu/relu6/relu_offset）、II=6×5
  （cos4/cosh4/horner_v3/sigmoid_taylor/silu_taylor）、II=7×1（sinh5）、II=8×6
  （div/log/rsqrt/sqrt/tan7/tanh7）、II=9×5（expm1/hardsigmoid/log1p/mish/silu_approx）、
  II=11×2（exp5/reciprocal））。
- **II>2 算子清单 = 上列 25 个**；其中纯 `{+,*}` 公式（V1 语法子集，见 §3.1）者 16 个
  （addcmul + 全部 Taylor/Horner 族），含 `Max/Min/Piecewise/Abs` 者 9 个。
- A2b 预检【归档 `verification_v2/artifacts/a2b_preflight_20261006/preflight.md`】
  补测 20 个 II>2 候选的束缚队列与 ops/iter（rtovr-14 普遍为束缚队列，6-11 ops/iter），
  n_max 列**用户 2026-10-06 裁定为未校准估计、不得用于准入**（scale_sum 实测 240 vs 估计 63，
  疑元素数/逻辑迭代数单位混用）。

### 2.2 普查盲区（正面取证）

- census 36 行名单【归档】**不含 scale_sum 与 add_v1**——两份 yaml 为 ADR-056 步③-⑤
  新增（`tests/unit/test_admission_l1_l2.py:439` 快照名单沿革注释），census 时点尚未存在。
  故「28/36 解出」的完整口径 = 36 普查 + 2 后增（scale_sum II=2【归档步⑤】、add_v1 同 add）。
- 缺席断言取证路径：`census3_progress.txt` 全文 36 行 + DONE 行；
  `ls ops/*.yaml` 38 行；差集 = {scale_sum, add_v1}。

### 2.3 本预检方法学对照（【实测】，背书 probe 可信度）

| 对照 | 本预检实测 | 归档值 | 判定 |
|---|---|---|---|
| scale_sum.yaml 全路径重解 | II=2，16 队列；双槽队列 = rtovr-14(span3)/ur-r0(span2)/vin0(span2) | `precheck_scale_sum_20260926/README.md` 核心结论 1-2 逐格相同 | ✓ |
| horner_v3.yaml 全路径重解 | II=6 | census II=6 | ✓ |

### 2.4 重要新发现：ADR-056 §E.3 备选第二公式结构性 UNSAT（【实测】）

ADR-056 §E.3 指定「备选第二公式（验收通过后）：`a*x*x + b*x + c`——该公式考同队双计算槽」。
本预检以 §2.3 已背书的方法实测四个变体：

| 探针 | 布局 | 结果 |
|---|---|---|
| `a*x*x + b*x + c` | a/b/c 同 bank1 | **UNSAT（II=4..64 逐档 proven）** |
| `a*x*x + b*x + c` | a/b/c 分置 bank1/3/4 | **UNSAT（同上）** |
| `a*x*w + b*x + c`（w=独立第二输入 bank5，排除 Pow 双源路由假设） | 分 bank | **UNSAT（同上）** |
| `((a*x+b)*x+c)`（Horner 形，同布局对照） | 分 bank | **II=4 SAT** |

判读【推断，根因未分离】：UNSAT 与 bank 布局无关、与 `Pow` 双源无关；
共同形态 = **≥2 个独立乘积项求和**（`a·x·w + b·x`）。addcmul（1 个乘积项）SAT、
matmul 4-operand（4 个乘积项）已知 configurable 结构性 UNSAT
（`matmul_parallel_n.yaml` 头注 + `main_verify.py:33-34` 注释）、
exp5/tan7 等单链 Horner（1 条乘积链）SAT——与「乘积项计数」判据全部一致。
**ADR-056 §E.3 的备选第二公式按现文本不可直接当候选**，需先修 SMT/DFG 层（属公理层工作，
未审计）或改写为 Horner 等价形——但 Horner 改写改变浮点求值顺序，§C.2 保序禁止
（ADR-056 §E.3 原文已禁）。建议将本段作为 ADR-056 §E.3 的 errata 素材回写。
该拒绝是**响亮的**：`pipeline_runner.py:106-107` `raise SynthesisRejected(report)`，
非零退出、无发射、无交付包。

### 2.5 SymPy 规范化与保序风险（【实测】，对候选写法有硬约束）

V1 保序契约（ADR-056 §C.2）以**文本有序语法树**为准（`ordered_ast.py`）；
编译器前端走 SymPy 规范化树。两者求值顺序必须逐节点一致，否则独立参考锚定 bit-exact 失败。

| 公式文本 | SymPy srepr（实测） | 保序判定 |
|---|---|---|
| `a*x*x + b*x + c` | `Add(Mul(a,Pow(x,2)), Mul(b,x), c)`，args 序 `(c, a*x**2, b*x)` | ✗ 双重改序：`x*x→Pow`（`lower_pow` 产 `FPMUL(x,x)`，得 `a·(x·x)`≠文本 `(a·x)·x`）+ 加法重结合 `((c+A)+B)`≠文本 `((A+B)+c)` |
| `alpha*(x+other)`（scale_sum） | `Mul(alpha, Add(other, x))` | ✓ 仅交换律级互换（fp32 加/乘逐位交换安全） |
| `x + alpha*other`（add） | `Add(Mul(alpha,other), x)` | ✓ 同上 |
| `((c0*x+c1)*x+c2)*x+c3`（horner_v3） | `Add(c3, Mul(x, Add(c2, Mul(x, Add(Mul(c0,x), c1)))))` | ✓ 结合性逐节点保持，仅交换律级互换 |

结论：**Horner 族天然保序安全**；多乘积项求和形即使可解也须改写文本或先过 §E.2-2
规范化记录比对——这从根上排除了一批「看似显然」的候选写法。

---

## 3. 五方面预检表

### 3.1 候选 A（主）：horner_v3（II=6，现存 yaml + 准入标记迁移）

**公式**：`((c0*x+c1)*x+c2)*x+c3`（3 乘 + 3 加，6 级串行依赖链，单流式输入 x + 4 驻留参数）。

| 方面 | 结论 | 证据 |
|---|---|---|
| ① 语法 | **支持**。纯 `{+,*}`，无函数/幂/除法/比较；frontend `lower_mul/lower_add`（`standard_kernels.py:7-30`）；参考侧语法合同 `_BIN_OPS={Add,Mult}`（`ordered_ast.py:58`）；文本闸 `_gate_text_formula` 无 USub（负号仅在 test_data 数值，非公式设计）【源码】；`parse_ordered_formula` 实跑 PASS【实测】 | `standard_kernels.py:7-30`；`ordered_ast.py:58`；`configurable.py:112-136` |
| ② 基础操作 | **齐备**。所需单元 = FPMUL/FPADD/RTOVR/VIN0/VIN1/VOUT0/UR_READ/UR_WRITE，全部在 `modules.yaml`（FPMUL:23、FPADD:2、RTOVR:116、VIN0:139、VIN1:149、VOUT0:159、UR_READ:170、UR_WRITE:180）与 `specs/units/{fpmul,fpadd,rtovr,vin0,vin1,vout0,ur_read,ur_write}.yaml` 存在；延迟 FPMUL=FPADD=4（`pipeline_delays.yaml:5-6`）；发射层支持 = add/scale_sum/mul 三包 VCS 已验同族单元【归档】；G-op 闭集 {FPMUL,FPADD}（`assembler.py:16`）⊆ 本公式单元集【源码】 | 见左 |
| ③ 槽结构 | **真·多有效槽 + 长链**【实测 2026-10-10，全 yaml 真路径重解】：II=6，18 队列。**fpadd 3 有效槽 [0,2,4]、fpmul 3 有效槽 [1,3,5]**（三计算槽、偶/奇交替节拍）；**rtovr-14 6/6 满槽、vin0 6/6 满槽**；rtovr-3/4 各 3 槽、rtovr-5/6 各 3 槽；ur-r0 3 槽（x 三次重读）、ur-r1 2 槽；单槽 ×8（rtovr-15/16/37、ur-r2/r3、ur-w2、vin1、vout0）。全局 t∈[1,37]。每队列 ops 数==去重槽数 → 无槽冲突（D1 闸通过【推断·由实测槽集】）。容量墙：128 行/子队列【源码：`u280_sim_v422/smi/smi_top.v:22` + 四处 override :430/454/525/551】——n=60（N_eff=15）时束缚队列 L_q=6·(15+4)+2=**116 ≤ 128 顶格内**【未校准构造预测】 | 实测输出；`smi_top.v:22`；`assembler.py:21` |
| ④ 实际容量 | **n_max^IQ 构造预测 = 68**【未校准】：束缚队列 rtovr-14/vin0（span=4），L_q=6·(N_eff+4)+2≤128 → N_eff≤17 → n=68 恰 128 行顶格、n=72→134 应拒（构造预测，校准方法 = scale_sum 步⑤同法：预测→实测顶格→上一档响拒）。布局界远松（y 单 bank，n≤1024）。分段路径：ADR-057 D1 公式 `L_q(m)=II_q·(m+span_q)+2` 为 II 通式【源码 `ADR-057`】，>68 可走分段（但多段协议模型亦需 II=6 新冻结，见 §5） | 实测 span；`assembler.py:622-633` |
| ⑤ 拒绝原因 | 全链路响亮：未加 `experimental_unroll` → V1 链 package 边界 `Unverified("BRANCH_NOT_UNROLL")`（`run_v1_verify.py:664-670`）；加标记后 G-addr 需 length 声明（缺 → `TYPE_LAYOUT_UNSUPPORTED`，`configurable.py:185-191`）；超容量 → `STATIC_ADMISSION_REJECTED/G-cap`（`assembler.py:628-633`，`test_g3_add_overcapacity_refuses_loudly` 锁定）；域越界 → `TYPE_LAYOUT_UNSUPPORTED/SUBNORMAL_BAND`（`reference_generator.py:341-343`）。**域闸实跑：horner_v3 遗留 test_data（x∈[0.1,1]、c 四点单值）`static_domain_check` ok=True 通过【实测】**——无需改域 | 见左 |

**额外优点**：embed 死区不触及——unroll 分支全行 `embed==0`（`SMIHeader` 默认 embed=0
`lir_op.py:14`；D2 行规格 `assembler.py:602-609` 显式 level=0/loops=0/inc=0/embed_end=0/
inc_embed=0；INIT :523；TERM :617-618）；死区 embed∈{1..6}【归档 ADR-055】不触发。
多槽来自**显式槽序列展开**而非 embed 循环，与任务「确认不走死区」的要求一致。

### 3.2 候选 B（备）：Horner 二次 `((a*x+b)*x+c)`（新配置 yaml，暂名 horner_quad）

| 方面 | 结论 | 证据 |
|---|---|---|
| ① 语法 | 支持（同 3.1-①；纯 {+,*}；实跑 parse PASS）【实测】 | 同 3.1 |
| ② 基础操作 | 齐备（同 3.1-②，单元子集相同） | 同 3.1 |
| ③ 槽结构 | 【实测】II=4，17-18 队列（参数同/分 bank 两布局均 SAT），**9-10 个多槽队列**：fpadd 2 槽、fpmul 2 槽（**双计算槽**），rtovr-14 4/4 满槽、vin0 4/4 满槽，rtovr-3/4/5/6 各 2 槽，ur-r0 2 槽。链长 4（M→A→M→A 串行） | 实测输出 |
| ④ 实际容量 | n_max^IQ 构造预测 ≈ **108**【未校准】（束缚 vin0 span=4：N_eff≤27 → n=108；n=112→L_q=130 拒）。三标准规模 4/8/60 全在界内 | 实测 span；构造预测 |
| ⑤ 拒绝原因 | 同 3.1-⑤；域闸对两组试探参数（含跨零中间值组）均 ok=True【实测】 | `reference_generator.py` 实跑 |

与 A 的取舍：B 的槽形态是 A 的真子集（双槽 vs 三槽、4/4 vs 6/6），治理更干净
（全新 yaml，零迁移；scale_sum/add_v1 先例），但结构性考点少于 A；
且 B 不在任何现存候选名单（census/A2b 未含）——按任务「从已有候选中筛」的口径，B 记为
**变体备选**而非首选。

### 3.3 候选 C（短链兜底）：addcmul（II=3，现存 yaml）

- 结构：II=3【归档 census】，链长 3（M→M→A），3 流式输入（x/t1/t2 分 bank0/1/2）。
  fpmul 2 计算节点 → 双计算槽【推断，未逐槽实测——A2b 未列 addcmul】。
- 语法/G-op 干净【源码公式】；**遗留域 x∈[-1,1] 跨亚正规带，域闸实跑拒绝
  （SUBNORMAL_BAND）【实测】** → 须改安全域（scale_sum 步②先例：test_data 配置改动）。
- 评价：结构丰富度最低（II=3、链 3），且需域改动；列为第三顺位，不进正式推荐。

### 3.4 候选 D（否决）：matmul_parallel_n（II≥4 loop-carried 自环）

唯一**循环携带依赖**候选【源码 `matmul_op.py:407-412`：`loop_carried_depends_on={"y_0": 4}`
令 IIAnalyzer 取 rec-MII≥4】，8/8 bit-perfect @N=8【归档 ADR-025，**仅终值 parity**】。
否决理由（逐条）：

1. **形态错配**：cumulative addtree 自环属「有状态归约」，ADR-057 明文非目标
   （「归约/累加状态」，`ADR-057` 目标与边界节）；V1 准入 = 无状态逐元素配置（ADR-056 §A）。
2. **G-op 硬性拒绝**：DFG 含 FP32ADDTREE5TO1 ∉ {FPMUL,FPADD}
   （`assembler.py:16` + `:126-131` → `STATIC_ADMISSION_REJECTED|BASE_OP_OUT_OF_SCOPE`）。
3. **多 SMC**：parallel_dim=N + SHIFTUP 广播；M3-lite 门 = 单 SMC（progress_log 2026-10-05 节）；
   分段拦截对多 SMC 直接放行原拒绝（`pipeline_runner.py:200-201`）。
4. **发射形态死区**：手搭 DFG 无准入凭据 → embed 主线多槽 kernel → RTL embed∈{1..6}
   状态机自锁死区【归档 ADR-055】；现 8/8 证据为 model-A sim 终值，「对单 SMI 忠实性增信近零」
   （`DEBT-023-inventory.md` 升 Accepted 两道硬门均未过）。
5. **债务未闭合**：DEBT-023-BCAST-TIMING（广播流式时序未知）、DEBT-023-B（sim 非真 fabric）、
   DEBT-023-COMPUTE-ADDR（compute 微指令表 user 未提供）、DEBT-025-MULTITILE（单 tile）
   【归档 `DEBT-023-inventory.md` 全 4 项 open】。

结论：**长线「真 loop-carried II≥4」目标的正确载体，但当前步不可用**；
解锁前提 = RTL shift-fabric 时序表 + 模型 B sim + 广播时序 VCS + 发射层多 SMC/准入重构（A2+ 量级）。

### 3.5 语法越界族（abs/clip/leaky_relu/relu6/relu_offset/hardsigmoid/silu_approx 等，双保险拒绝）

- L1 构造期：`parse_ordered_formula` 拒绝函数调用/比较（`ordered_ast.py`，
  语法合同「函数调用、幂、除法、二元减法、比较/选择全部拒绝」）→ `SYNTAX_UNSUPPORTED`
  （`configurable.py:149-159`，测试 `test_l1_subset_gate_requested_only`）。
- 即便过 L1，DFG 含 LOGIC（SELECT_GT/LS）→ G-op `BASE_OP_OUT_OF_SCOPE`
  （`assembler.py:126-131`，测试 `test_gate_gop_out_of_scope`）。
- **静默放行不可能**：两道独立闸串联，且 `experimental_unroll` 缺席时行为逐比特不变由
  契约测试锁定（`test_admission_l1_l2.py:416-440`）。

---

## 4. 推荐：主候选 horner_v3 + 备选 horner_quad

**主候选 = horner_v3**（现存 `verification_v2/ops/horner_v3.yaml` + 准入迁移）。理由：

1. **结构考点最全**（【实测】）：真 II=6 >2；三计算槽（fpadd/fpmul 各 3 有效槽）=
   「同队多计算槽」的首次 RTL 行使（add/scale_sum 的双槽全在 rtovr/ur/vin 搬运队列，
   计算队列恒单槽【归档 precheck_scale_sum_20260926】）；两条 6/6 满槽队列；6 级串行链；
   4 个驻留参数（scale_sum 仅 1 个驻留 alpha）。
2. **准入路径零代码改动面最小**：纯 {+,*} 语法过 L1【实测】；G-op/G-slot/G-stride/G-dp/G-stage
   静态全过【源码+实测槽集】；域闸**遗留 test_data 原样通过**【实测】——唯一需要的配置改动 =
   加 `experimental_unroll: true` + 各张量 `length`（scale_sum 步⑤同型改动）。
3. **容量故事完整**：n=4/8/60 三标准规模全在界内（L_q≤116），n=68 顶格、n=72 响拒
   （构造预测，待校准）——一次验收同时产出「新形态 GREEN + 新边界 + 新拒绝」三类证据。
4. **保序安全**：SymPy 规范化逐节点保序【实测 §2.5】，独立参考锚定无已知的顺序陷阱。

**备选 = horner_quad（新 yaml）**：治理零迁移（scale_sum 先例），II=4 考点为 A 的子集；
当 A 在 VCS 暴露 II>2 形态问题需降级排查时，B 是天然的中间形态台阶。

**对 ADR-056 §E.3 的修订建议**：备选第二公式 `a*x*x+b*x+c` 实测结构性 UNSAT（§2.4），
应回写 errata 并将「同队双计算槽」考点改由 horner_v3（三计算槽）/horner_quad（双计算槽）承担。

**最大风险点**：**II>2 展开形态的协议模型未证**。协议注册表 v2 的已证边界 =
「II=2 展开形」，`INIT_PAD/TERM_PHASE/ISSUE_TOTAL` 公式虽以 II 为参数成文【源码
`protocol_registry_v2.yaml:28-45,62-69`】，但冻结纪律明文「断言*形式*可继承，
常数值在新形态须独立论证后冻结」+「新形态上的首个常数值只能走探索→独立论证→追加冻结」
（同文件头注与 :85/:94/:105 proven_boundary）；ADR-056 §E.3 更明文「II>2 → 未证槽形态
（展开规则可能要修订）……禁止按一轮即可通过估时」。

---

## 5. 正式 RTL 验证接入步骤预估（主候选 horner_v3）

> 全程**预期零生产代码改动**（配置-only，同 scale_sum 步⑤先例）；「预期」的依据 =
> 八闸门与展开环代码通读【源码】，展开环对 iq_II 通式（`assembler.py:491-634` 无 II==2 假设），
> 但 VCS 首跑若暴露形态问题则可能回到发射规则修订（ADR-056 §E.3 警示），不得预先承诺。

1. **配置接入（唯一改动面）**：`horner_v3.yaml` 加 `experimental_unroll: true` +
   四张量 `length`（x/y=256，c0-c3 各=1 或按 bank 内上界；`configurable.py:185-191` 必填）。
   同步更新快照守卫名单 `tests/unit/test_admission_l1_l2.py:439`
   （`[add, add_v1, scale_sum]` → 加 `horner_v3`，该测试设计即为随新配置更新）。
   ——或走 add_v1 式克隆 yaml（`horner_v3u.yaml`）保留 census yaml 原样；两路均可，推荐就地迁移
   （census 不消费该字段，`census_iq_shapes.py:42` 不传 top_op【归档 ADR-056-hco-review:82】，
   零误报回归不受影响；horner_v3 旧 embed 产物从未 VCS、无冻结基线需要守护）。
2. **§E.2 准入档案**：原始公式文本 + 规范化记录（srepr ↔ 有序树比对，本预检 §2.5 已预跑）
   + 与全部既有 yaml 的结构 diff。
3. **只读预检归档**（scale_sum 2026-09-26 同型）：solve + branch_report + 逐队列 span/R_q/L_q
   预测（本报告 §3.1 已含主体数据，须按归档格式落 `verification_v2/artifacts/`）。
4. **期望先行（新工作量核心）**：II=6 形态的协议常数「探索→独立论证→追加冻结」——
   用 `emit_expected_fire_sequence.py`【源码 :335 `l_q = ii * r_q + 2` 为 II 通式】
   生成期望序列，常数值论证须引 RTL 文本/不变量（非被验 trace），追加冻结入注册表 v3
   （不动 v2 已封记录）。
5. **V1 十段链**【源码 `run_v1_verify.py:99-105`】：config_check → formula_domain_check
   （已预跑 PASS）→ compile_admit（L1/L2 八闸门）→ independent_reference（有序 AST 参考
   锚定）→ artifact_audit → package → **VCS** → native_compare → three_zone → arbitrate。
   三规模 n=4/8/60 + 边界 n=68 顶格 + n=72 响拒（规模值以实测校准为准）。
6. **回归**：add 冻结基线 frozen_regression 通道 + scale_sum 发布包复查 + mul 零字节 +
   pytest 全量（现基线 535，2026-10-08 账）+ census 零误报。
7. **（可选）分段验收**：n>68 经 ADR-057 分段器（`pipeline_runner.py:196-227`，
   D1 公式 II 通式）——但多段仲裁模型亦需 II=6 期望先行，建议单段 GREEN 后再议。

工作量定性：**配置接入 = 小时级；期望先行 + VCS 排障 = 主要不确定项**
（ADR-056 §E.3：禁止按「一轮即可通过」估时）。

---

## 6. 未知项与待校准项清单

| # | 项 | 状态 | 处置 |
|---|---|---|---|
| U1 | **多乘积项求和形 UNSAT 根因**（`a*x*x+b*x+c` / `a*x*w+b*x+c` 全 II≤64 UNSAT；与 matmul 4-operand 同族判据为【推断】） | 未分离（公理层未审计） | 登记；若结构扩张线需要该形态则立项深查 |
| U2 | II>2 展开形态在 RTL 的协议正确性 | 未证（已证边界=II=2） | 期望先行→VCS 裁决；注册表 v3 追加冻结 |
| U3 | horner_v3 n_max=68 | **未校准**（构造预测） | 步⑤同法校准：预测→实测顶格→上一档响拒 |
| U4 | horner_quad n_max≈108 | **未校准**（构造预测） | 同上（若启用 B） |
| U5 | A2b 20 候选的 n_max 列 | 未校准（用户 2026-10-06 裁定，单位疑混） | 统一单位后按逐队列 II_q·R_q+2 重算（a2b 自注待实现） |
| U6 | horner_v3 ur-r0 三槽（x 三读，r_k 最大 3）的 FILL/DRAIN 环绕形状 | 未见于 add（其环绕证据 254/255 为 r_k=2 形） | 进 §D.4 逐拍期望与三区比对设计 |
| U7 | 6/6 满槽队列（rtovr-14/vin0 无气泡槽）的发射相位与 ISSUE 模型 | 已证形态均有气泡或双槽，满槽为新面 | 期望先行重点对象 |
| U8 | horner_quad 参数取值与声明域最终定案 | 两组试探域过闸【实测】，正式值待定 | 接入步①定值后重跑域闸 |
| U9 | addcmul 的逐槽结构（双计算槽判定为【推断】） | A2b 未覆盖 addcmul | 若升级为三顺位候选再补 probe |
| U10 | fma_parallel_n / mul 的 flat 形 ISSUE 层实证 | 独立挂账（progress_log §4；registry :69/:105 明文缺口） | 与本主线并行，不阻塞 |
| U11 | 38 份 yaml 中 add_v1 与 add 的关系定位 | 快照守卫注释（`test_admission_l1_l2.py:420`）记「步④-3 增 add_v1」，公式同 add | 不影响本候选；登记 |

---

## 附：本预检执行过的搜索/实测清单（缺席断言取证纪律）

- 搜索：`grep "II 普查|census" docs/progress_log.md docs/adr/`；`ls ops/*.yaml`（38）；
  `ls verification_v2/artifacts | grep census`（2 目录）；`grep G-slot/G-II/STATIC_ADMISSION_REJECTED src/driver/assembler.py`；
  `grep experimental_unroll src/ verification_v2/ tests/`；`grep -rn DEBT-023 docs/adr/`；
  `find /home/juno1023 -name smi_top.v`（3 份，取 u280_sim_v422 与 ADR 引用同版）。
- 实读：`assembler.py`（全 700 行区段）、`configurable.py`、`pipeline_runner.py:100-230+491-528`、
  `run_v1_verify.py`（STAGE_ORDER/域闸/BRANCH_NOT_UNROLL）、`ordered_ast.py`、`reference_generator.py`
  （域闸判定逻辑）、`standard_kernels.py`、`registry.py`、`modules.yaml`、`pipeline_delays.yaml`、
  `smi_top.v:20-24`、`protocol_registry_v2.yaml`、`census3_progress.txt`、`a2b_preflight.py`+归档、
  ADR-055/056/057、DEBT-023-inventory、progress_log 全文档。
- 实测（in-memory，repo venv，未写盘）：SymPy srepr 四公式；求解器 probe ×8
  （quad×3 布局/形态、quad_two_inputs、horner_quad×3 布局、scale_sum 对照、horner_v3 对照+槽结构）；
  `parse_ordered_formula`+`static_domain_check` ×5（horner_v3、horner_quad×2、tan7、addcmul）。
