# 受限 softmax v1：规格与最小归约实现路径设计（checkpoint ① 交付）

> 2026-10-19，主线令见 memory `softmax-mainline-directive-20261019`。
> 用户裁定（2026-10-19）：**「已有能力冻结，启动受限 softmax；先完成正确实现，再优化主要成本。」**
> 本文档是 checkpoint ① 的可执行设计交付：**规格 + MAX/SUM/EXP/倒数实现路径核查 + 资源与存储评估**，
> 明确「可复用 / 需新增 / 阻断项」。**本文档是设计不是承诺**；实现期的 hw_config/axiom/准入门
> 变更按立法序（ADR-004）另行立 ADR，hco 必经。
>
> 口径纪律（继承 2026-10-14 预检）：「缺」= 通用公式入口（yaml 公式 → MIR → SMT → LIR → hex
> 配置链）缺该项能力；已有专用路径/手写线/simulator 资产单列。「无专用 EXP/DIV/MAX 单元」≠
> 不可实现，只陈明成本在逼近算法设计。证据分级：每条事实带 `文件:行号` 锚；推断与实测分列。

---

## 1. 目标与范围

**最终交付**：用户声明 `softmax(x, axis=-1)` → 编译器生成完整程序（非手写 IQ）：
`HBM 输入 → LOAD → softmax 计算 → STORE → HBM 输出`。

**v1 边界**（首版明确不做的事见 §8）：

| 维度 | v1 取值 |
|---|---|
| 行长 L | 固定、编译期常量；bring-up 从小规模（16 词级）起步，再扩 |
| dtype | fp32（IEEE-754 binary32）输入/输出，中间量 fp32 |
| 归约范围 | 单 SMC、单 chunk、整行归约（跨 SMC 归约单独评估，不套等长分片） |
| 验证序 | RTL 仿真（VCS）先行，后扩规模 + 板端窗口 |
| 性能口径 | 装载/计算执行/搬运/T_total 分列测量（perf_metrics_definition 类型 ② 纪律） |

**单 SMC 是首版边界，不能成为最终性能评价的全部**（主线令原文）。

## 2. 算法语义与数值规格

### 2.1 数值稳定形式（唯一合法语义）

```
m  = max_{i∈[0,L)} x_i          # 行最大值（跨元素归约 + 比较选择）
e_i = exp(x_i − m)              # i∈[0,L)；指数逼近 + 减
s  = Σ_{i∈[0,L)} e_i            # 跨元素归约 + 累加反馈
r  = 1 / s                      # 行内单次倒数（设计决策 D1）
y_i = e_i · r                   # 归一化 = L 次乘，非 L 次除
```

**D1（行倒数 + 逐元素乘，不做逐元素除）**：倒数成本付一次（NR 5 轮），归一化退化为逐元素乘
（FPMUL 已证链）。代价：r 的一次舍入传播到全行（误差预算 §2.3 已计入）；
收益：除法逼近的迭代成本从 L 次降为 1 次。

**结构红利**：s ∈ [1, L] 恒成立（e_i ≤ 1 且 max 元贡献 exp(0)=1 ⇒ s ≥ 1）——
无 0 除风险、无溢出域；倒数的规格化构造（§3.4）靠这个下界成立。

### 2.2 输入域（提案，随逼近方案 W1 数值验证定稿）

沿用 ADR-056 §C.4 双道 AND 纪律：静态区间承诺（yaml 声明域 ⊆ D）+ 装载数据动态核验。

- `x_i ∈ [−2^7, 2^7] = [−128, 128]`，正规 fp32；NaN/Inf/亚正规响亮拒绝
  （SUBNORMAL_BAND 先例，ordered_ast.py 静态道 + dynamic_data_check 动态道）。
- 设计理由：`x_i − m ∈ [−256, 0]`；fp32 下 exp(t) 在 t < −103.3 时 flush 为 0，
  贡献为零——**算法上无害**；逼近精度承诺对 t ∈ [−88, 0] 给出（覆盖正规值域，
  由 halve-and-square 构造 §3.3 天然覆盖 [−256, 0] 全宽）。

### 2.3 三层验收指标（softmax 不看单纯 bit-exact，主线令原文三层）

| 层 | 指标（提案值，W1 数值验证后定稿） | 方法 |
|---|---|---|
| ① 实现正确性 | RTL（VCS）输出 vs **独立实现的同一近似算法、同一舍入顺序**参考：逐位 bit-exact | 独立脚本从 hbm_data 字节自行解码重算（fair_smul64w_ref_check.py 模式：struct 解 fp32 + numpy，不依赖编译器 golden 路径） |
| ② 算法精度 | vs float64 稳定 softmax：rel err ≤ 2⁻¹⁹（≈1.9e-6，对 y_i ≥ 2⁻¹²⁶ 的正规输出）；\|Σŷ − 1\| ≤ L·2⁻²²；非负性（无 NaN/负值）；极端三案 | 同一独立脚本内的第二个参考函数（float64 全链重算）——**两参考同脚本但算法/精度独立**，防「RTL 与自己的近似 golden 一致」假闭环 |
| ③ 性能 | 装载 / 计算执行（T_exec_obs）/ 搬运（LOAD+STORE）/ T_total 四列；不同逼近方案**同一精度门槛**下比较 | perf_marker_extract.py 分列；板端时间属类型 ④ 单列 |

**极端三案**：全等行 → y 均匀 1/L；单主元 → one-hot（他元 exp 下溢为 0 合法）；
|x| 近 128 的大幅度混合行。

**舍入顺序冻结要求**：max 比较序、sum 累加序、exp 多项式求值序（Horner）、NR 迭代式
求值序、规格化 SELECT 级联序，全部写成规格文本（注册表 v3 `FORMULA_SEMANTICS_*`
追加惯例：独立论证先行）。两链（串行累加 vs 加法树分组）若都保留，golden 各自对照
同舍入序独立参考，**精度指标不共享**。

## 3. 硬件实现路径核查（MAX / SUM / EXP / 倒数 × 基础操作）

**总结论：四组件全部存在可拼接的基础操作路径，无硬件阻断项。**
今日核查的**最重要负面结论**：**片上无浮点位重解释原语**——FPTOINT/INTTOFP 是
值转换单元（RTL `fpto_int_array.v:98-113` 实证：FP32→INT16 数值截断 + dst_pos 半字
定位，非位型重解释）；手写线 div/rsqrt 的 magic-number 种子（div_op.h:18、
rsqrt_op.cpp:597,668-672）全部是 **host 侧算好、作为输入数据注入**（x0_tensor /
y0_tensor），不是片上计算。**这直接改判 exp/倒数的实现路线**：全部走纯 {FPMUL,
FPADD, LOGIC} 值运算构造，不依赖任何位技巧。四条路径也都不在当前 unroll 准入门
基础操作集内（§4.3），落地须过立法序。

### 3.1 MAX（比较选择 + 跨 lane 合并 + 广播，全原语存在）

- **元素级运行 max**（每字 4 lane 并行）：FPADD(SUB=1, UPDATE_ST=1) 产状态位 →
  LOGIC SELECT_GT 逐 lane 选大，跨迭代反馈（loop-carried，stride=0 驻留字）。
  生成器侧 lowering 先例 `lower_minmax`（standard_kernels.py:110-118）；
  LOGIC SELECT_GT=optype 5（modules.yaml:107）；sim 模型 logic_unit.py（SELECT_GT=op5）。
  扫描结束后 M=[M0,M1,M2,M3]（每 lane 部分最大）驻留字一。
- **跨 lane 合并**（标量 m）：LOGIC AND 掩码常量字（host 预置
  [0xFFFFFFFF,0,0,0] 等 4 个，stride=0 驻留）抽取 M_j 为 4 个标量字 → 3 次
  标量两两 max（FPADD SUB/ST + SELECT_GT）→ m@lane0。
- **广播 [m,m,m,m]**：UR_WRITE 的 **wbe 16bit 字节写使能**（RTL user_register.v:124
  `w_ur_wbe_0 = i_cru_ur[27:12]` 实证，128bit 词 = 16 字节）×4 次掩码写
  （wbe=0x000F/0x00F0/0x0F00/0xF000，源均为 [m,0,0,0]）拼成四 lane 全同字 →
  之后 stride=0 驻留读。**发射层现无 wbe 路径**（src/ 零命中；字段已声明
  modules.yaml:189、RTL 已支持）——属编译器发射扩展，非 RTL/hw_config 数值变更。
- **⚠ 证据缺口**：LOGIC SELECT 路径在冻结 TB **无 VCS 记录**（验证链已 VCS 算子
  全为纯 {FPMUL,FPADD}；matmul 先例仅 python sim）——故主线令「先 SUM 再 MAX」
  的顺序恰好让 addtree（§3.2）先上 RTL 建立归约证据，LOGIC 随后。
- **判定**：✅ 可行（全原语存在；VCS 证据随最小归约入口补）。

### 3.2 SUM（归约反馈 = matmul 同构先例）

- **基础操作**：FP32ADDTREE5TO1 + accumulate 位（modules.yaml:340-351；
  ADR-029 固件位）。每字 4 lane 求和 + old_i 进位 → lane0 行和 s。
  **手写线全同构先例**：matmul_op.py:410-412（`cumulative=True,
  reduction_per_channel=4, loop_carried_depends_on={"y_0": 4}`，II≥4）；
  sim 模型 fp32_add_tree_unit.py:81-105（old_i 参与）。
- **机制链已通**：DFG metadata → RecMII（ii_analyzer.py:128-163）→ SMT 自环约束
  （ucode_solver.py:449-454）→ accumulate 位 emit（payload_factory.py:104-109）。
- **⚠ 连接受限**：rtovr allowed 矩阵对 addtree 槽位有约束（rtovr_config.yaml:109-118：
  5to1 八槽 allowed 偏 INT8 串链、8to1 只暴露 2 槽）——落线须以 matmul 实际产物
  为模板逐一核实，列入落地前核查项。
- **⚠ 证据缺口**：cumulative addtree 自环在冻结 TB 无 VCS 记录（matmul_parallel_n
  仅 sim 模型 A）——**这正是中期前「最小归约入口」要补的第一证据**。
- **判定**：✅ 可行（机制链最成熟的一段；VCS 证据=最小归约入口的首个交付物）。

### 3.3 EXP（halve-and-square 纯 {+,*}，位技巧改判不可用后的主路线）

- **无专用 EXP 单元**（modules.yaml 24 键全枚举 + grep 零命中 + sim kernel 注册表
  对照，三路证实）；sim 同样无 EXP 模型。
- **片上无位重解释**（§3 顶部负面结论）→ 标准 k·ln2 值域规约（需 2^k 指数位构造）
  **不可行** → 主路线 = **halve-and-square**：
  `exp(t) = (P(t/256))^(2^8)`，t ∈ [−256, 0]
  1. `t8 = t · 2⁻⁸`（一次 FPMUL，常数 0.00390625）→ t8 ∈ [−1, 0]；
  2. `p = P(t8)`：deg 12–15 Horner 多项式逼近 exp on [−1,0]，系数 = host 预置
     常量字（horner_v3u 常量 bank 先例；div 的 const2/5-bank 布局先例）；
  3. 8 次平方（8 × FPMUL）→ exp(t)。
- **误差链（提案值，W1 数值验证定稿）**：256·ε_poly + 8 次平方舍入 ≈ 2⁻²⁰ 级
  （deg-15 时），满足 2⁻¹⁹ 预算有余量；t < −103 时真值低于 fp32 可表示域，
  渐进下溢为 0 = 正确行为（贡献可忽略）。
- **每元素成本**：~13 FPMUL + ~12 FPADD + 1 SUB + 配套路由——**贵于任何已证 map
  算子**，这是主线令「不能直接承诺现有硬件低成本实现」的具体化；优化（减平方次数
  的混合规约、降 deg）排 W3。
- **手写线参照**：op/exp Taylor 10–20 项（exp_op.h:64），但系数注入路径未证实
  且全 op 在 Makefile DISABLED_OP_DIRS（:30-37，非活跃回归，无 VCS 日志留存）；
  v4.19 旧 case `exp_8smc_v3` 用 coeff+const 预置（rel_tol=0.03）说明系数表 exp
  曾在旧 TB 跑过（无 PASS 日志）——**均作参考资产，不作能力证据**。
- **判定**：⚠️ 需新增逼近设计（主线令四核心之二），零件全在已验证能力内，
  无位操作依赖；首个检查点 = deg/误差数值验证（W1）+ 小规模 RTL。

### 3.4 倒数 / 归一化（规格化 + NR 常数种子，全直线型值运算）

- s ∈ [1, L]（§2.1 结构红利）。magic seed 片上不可行（§3.3 同结论），改：
  1. **固定 8 次 halving**（FPMUL ×0.5）产 s₀..s₈；
  2. **SELECT 级联**（FPADD SUB/ST + LOGIC SELECT_GT）选出 s′ = s·2⁻ᵏ ∈ [1,2)
     与同索引的 2⁻ᵏ 常量（9 个 host 预置常量）——**全直线型、数据无关控制流**
     （IQ 是定长指令序列，无变长循环；此性质是本构造成立的前提）；
  3. **NR 5 轮**（r₀=0.75 常量 ⇒ |e₀| ≤ 0.5 ⇒ eₙ₊₁=eₙ² ⇒ 5 轮达 2⁻³²）：
     `r′_{n+1} = r′_n·(2 − s′·r′_n)`，纯 {FPMUL, FPADD(SUB)}；
  4. `r = r′ · 2⁻ᵏ`（一次 FPMUL）。
- **成本**：~50 ops **每行一次**（L=16 摊 ~3 ops/元素，可忽略）。
- **手写 div 参照**：NR 3 轮 + host magic seed + UR 写回读回反馈（div_op.h:11-19,
  div_op_constants.h:37-45；19 cyc/iter）——算法同族；种子来源我们的构造不同
  （常数 0.75 + 规格化，无需位技巧）。
- **判定**：✅ 可行（全值运算、直线型、每行常数成本）。

### 3.5 标量驻留 / 广播（m、s、r 的行内生命周期）

- 驻留读：stride=0 机制编译器全层贯通（yaml role:param → SMT invariant 豁免
  ucode_solver.py:441-444 → 发射 op_mask 门控 assembler.py:249-251 → ADR-056/061
  VCS + 板端 PASS）；手写线同款（div_scalar_op_ur_inst.cpp:15）。
- 广播字构造：§3.1 的 wbe 四写（发射层新增，RTL 已支持）。
- UR 跨段保持已实证（ADR-061:108,131-135 n244 四段）——标量跨段驻留地基已存在。
- **判定**：✅ 可复用（+ 发射层 wbe 新增）。

### 3.6 资源与存储评估

- **UR**：8 bank × 256 词 × 128bit（hw_params.yaml:8-9）；fp32 4 元素/词
  （div_op.h:110-112）→ 行长 L 元素 = ⌈L/4⌉ 词/张量；单 bank 富余（L ≤ 1024）。
- **IQ 墙**：128 行/子队列 × SUB_IQ_NUM=4 各自独立（smi_top.v:22；
  ADR-055:45-49）。⚠ 勿混：PARAM_IQ_DEPTH:256（hw_params.yaml:4）是另一层参数；
  Jianyuan TB 默认 32（div_op_constants.h:47）是旧测试台默认——设计以 128 为准。
- **延迟表**（pipeline_delays.yaml 锁步）：FPADD=4、FPMUL=4、LOGIC=1、
  RtoVR=1、FP32ADDTREE5TO1/8TO1=4。
- **单元计数更正**：modules.yaml 实际登记 **24 个单元键**（含 VIN0/VIN1/VOUT0
  reg_pipe、双 addtree、双 SHIFT、SMI_DS_T、INT8toFP16）——预检「19」口径偏窄；
  「无 EXP/DIV/MAX 专用单元」结论不变（三路枚举 + grep 零命中证实）。
- **布局提案**：bank1=x，bank2=e（y 覆写 e），bank3=常量（掩码/exp 系数/0.5 幂/
  2⁻ᵏ 表/0.75），m/s/r 驻留字×3；5-bank 布局先例 div_op.cpp:102-112。
  容量瓶颈预计在 IQ 行数（unroll 形态行数 ∝ L），不在 UR。

## 4. 编译器落地路径（四层改动面 + 立法序清单）

### 4.1 表达层（需新增，立法序：ADR）

- 现状：yaml `formula` → sympify 原子 Symbol（configurable.py:67-68）；准入门文法
  只认 {+,*}（ordered_ast.py:58 `_BIN_OPS`），函数/比较/下标/求和构造全部
  SYNTAX_UNSUPPORTED（:78-133）。
- 改动：新增归约构造（结构受限的 `Reduce(op, axis)` 或专用行归约入口）+ 逼近
  函数语法（`exp` 作 lowering 目标名）。同步扩展 ordered_ast 参考发生器。
- 资产：legacy 内核库有 `sp.Max/sp.Min → LOGIC SELECT_GT/LS`（standard_kernels.py:108-118）
  与比较 → LOGIC（:124-141）——元素级比较选择 lowering 语义有先例可指；
  跨迭代归约 lowering 无先例（matmul 线全手写 DFG 绕开）。

### 4.2 lowering 层（需新增，机制件全在）

- 归约 lowering 须生成：addtree/LOGIC 节点 + `cumulative=True` metadata +
  `loop_carried_depends_on`（参照 matmul_op.py:394-437 手搭模式将其配置化）。
- 配置链 addtree 特例 `cumulative=False` 单拍规约（standard_kernels.py:33-73）
  不产生 loop_carried——需扩。
- exp/倒数 lowering = Halver/多项式/规格化/NR 子图模板（本文档 §3 的固化）。
- ⚠ 不需要 distance>1 的 loop_carried（ii_analyzer.py:155-156 明示不支持）：
  softmax 段间依赖走异构段（§4.4），段内自环距离 1。

### 4.3 调度层（机制在，准入须立法：ADR）

- loop-carried 通用 SMT 约束已存在（ucode_solver.py:449-454）；RecMII 已有
  （ii_analyzer.py:128-163）；自环 latency≥2 ⇒ 天然 II≥2 ⇒ 满足 unroll 展开
  G-II 门槛（assembler.py:505-510）——不落入 flat。
- **阻断点在 G-op 白名单**：`_V1_COMPUTE_ALLOWED = {FPMUL, FPADD}`
  （assembler.py:17,127-132）把 LOGIC/ADDTREE 响拒。**扩集申请 = 最小归约族
  {FP32ADDTREE5TO1, LOGIC}**（+ 已含的 FPMUL/FPADD）——ADR-056 §A.2-2 规则
  变更，先立 ADR + hco 审查。

### 4.4 发射 / 分段层（最大新增工程量，预检 :73「大幅重构」所指）

- 现状：段链支持多 COMPUTE 段（unified_lcs_case_gen.py:417-423），但全部段
  同源同调度（stage_partition.py:105 + pipeline_runner.py:221-225 逐段分支
  恒等断言）。
- softmax 需要**异构多 COMPUTE 段**（T1 max / T2 exp+sum / T3 normalize，三个
  不同 DFG）：每段独立编译（独立 DFG/SMT/发射）→ 段链拼接 + 段间标量驻留约定。
  段间数据全在 UR（无 HBM 往返），TB 辅助段推进契约（tb_multi_stage_intr_ack_v1）
  已支持多段顺序执行（stage_manifest.py:25-27）。
- **发射新增**：wbe 字段路径（payload_factory；字段已声明、RTL 已支持，
  零 hw_config 数值变更）+ 构造原语（AND 掩码抽取 / wbe 四写广播）的 emission。

### 4.5 验证层（需新增脚本，模式有先例）

- 独立参考：新写 per-case 脚本（fair_smul64w_ref_check.py 模式：hbm_data 字节
  自解码、不依赖编译器 golden；三层验收两个参考函数同居一脚本、算法/精度独立）。
- ADR-056 自动参考发生器只支持 {+,*}（ordered_ast.py:58）——逼近式 softmax 的
  「同近似同舍入参考」不能由它生成，**必须独立脚本**（主线令「避免假闭环」的落点）。
- sim 预检：fpadd/fpmul/logic/addtree(cumulative) 模型全在（kernel.py:91-101），
  e2e python sim 先行，VCS 随后。
- ⚠ 未评估项：多 broadcast/驻留读 sim 退化（configurable.py:297-300 自述）对
  softmax 多驻留读形态的影响——e2e 预检第一步必查。

## 5. 存储与数据流规划（提案）

### 5.1 三趟结构

```
T1 (max):    读 x_i（流式，逐字 4 lane）   → 运行 max M + 跨 lane 合并 m + 广播驻留
T2 (exp+sum):读 x_i + 读 m（stride=0）    → e_i 写 UR 临时区 + addtree 累加 s（驻留）
T3 (norm):   读 e_i + 读 s（stride=0）    → r=1/s（规格化 NR，一次）→ y_i=e_i·r 覆写 e 区
```

x 读 2 趟、e 读 1 趟；e 物化是 normalize 前的全向量中间件（预检 :71 所指）。
**3 趟是 softmax 的算法结构下界**（m 未定型前 exp 不可复算；s 未定型前 normalize
不可算）——优化对象是趟内效率与 UR 复用，不是趟数本身。所有数据相关控制流
已直线化（§3.3/§3.4 的 SELECT 级联），适配 IQ 定长指令模型。

### 5.2 段结构

`LOAD → COMPUTE(T1) → COMPUTE(T2) → COMPUTE(T3) → STORE`（异构三段，§4.4 框架）；
段间无 HBM 往返，标量经 UR 驻留跨段（§3.5）。L ≤ 单段 IQ 容量（行数 ∝ L·II，
exp 链使 II 显著大于已证 map 算子，具体 n_max 实现期实测校准——容量数字以实测为准）。

## 6. 检查点计划对齐（主线令表格落地）

| 阶段 | 承诺/争取 | 本文档对应 |
|---|---|---|
| 10-19/20 | softmax v1 规格 + 路径核查 + 资源评估（本文档）→ 用户裁定 | §1–§5,§7 |
| 中期前（~10-29 冻结） | **先 SUM 再 MAX** 最小归约入口 + lowering + 小规模 RTL 正确性（≥1 个跨元素归约由配置/IR 自动生成；cumulative addtree VCS 首证据）| §4.2/4.3 + ADR（G-op 扩集）|
| 中期后 W1 | exp/倒数逼近 + 独立数值验证（halve-and-square deg 定稿 + NR 轮数定稿 + 精度报告）| §3.3/3.4 + §2.3 |
| 中期后 W2 | 小规模完整 softmax L→C→S 端到端 RTL + 误差验收（未优化基线）| §4.4 + §5 |
| 中期后 W3 | 1–2 项实测驱动优化（归约组织/中间件访问/近似方案/并行度）| §3.2 分组树、§3.3 降成本 |
| 中期后 W4 | 扩行长/批量、回归、板端窗口、材料收口 | §1 范围外推 + 论文表 |

**主线令的立即报告触发条件**——「首轮发现 MAX 或归约反馈缺少可行硬件路径，
应立即报告具体缺口」——**不成立**：§3 结论四路径均有原语拼接；真正的约束是
§4.3 准入门与 §4.4 框架新增面（工程量 + 立法序）与 §3.1/3.2 的 VCS 证据缺口
（最小归约入口恰好补证），均非硬件不可能。

## 7. 风险登记册

| # | 风险 | 等级 | 处置 |
|---|---|---|---|
| R1 | G-op 扩集（{FP32ADDTREE5TO1, LOGIC} 入 unroll 准入集）hco 审查可能要求分层准入 | 中 | ADR 按「最小归约族」申请，不申全单元 |
| R2 | 异构多 COMPUTE 段框架 = 最大新增工程量，中期前可能只够 SUM/MAX 单趟归约 | 中 | 主线令已分层：SUM/MAX=承诺，完整 softmax=争取 |
| R3 | 加法树分组 vs 串行累加舍入序分裂，golden 双轨 | 低 | §2.3 已立规则；v1 只保留串行 |
| R4 | ~~exp 规约位操作接线~~ **已实证不可行**（FPTOINT=值转换，fpto_int_array.v:98-113）；主路线 halve-and-square 无位操作依赖 | 低 | 残余 = deg/误差预算 W1 数值验证 |
| R5 | sim 多驻留读退化（configurable.py:297-300）对 softmax 形态影响未评估 | 中 | e2e 预检第一步；sim 不可信则提前进 VCS 小规模 |
| R6 | exp 每元素 ~26  ops 级，计算成本远高于已证 map 算子 | 中 | 预期内（预检「大幅重构」）；W3 优化；验收看精度门槛下比较 |
| R7 | LOGIC SELECT 冻结 TB 无 VCS 记录 | 中 | SUM 先行；MAX 段落地时 small-scale VCS 补证 |
| R8 | addtree RtoVR allowed 矩阵连接受限（rtovr_config.yaml:109-118）| 中 | 落线以 matmul 实际产物为模板逐一核实 |
| R9 | wbe 发射层新增（payload_factory），范围控制 | 低 | 字段已声明、RTL 已支持；随 softmax ADR 一并审查 |
| R10 | IQ 定长指令：数据相关控制流必须直线化 | 低 | §3 构造已全部直线型（SELECT 级联），设计内闭合 |
| R11 | 手写线 exp/div/rsqrt 全在 DISABLED_OP_DIRS、无 VCS 日志—— tempting 的「先例」其实无证据 | 低 | 本文档全部降级为「参考资产」引用，不作能力证据 |

## 8. v1 不做清单

跨 SMC 归约（单独评估，不套等长逐元素分片）· 变长行长 / ragged · 多 chunk ·
INT8/FP16 · 逐元素除法（D1 已裁）· streaming normalize · 分组加法树（W3 候选）·
k·ln2 位规约路线（位重解释已实证不可用）· 任何「减少几条 INIT 指令」式主线优化
（主线令明禁）。

## 9. 事实锚附录（证据分级）

**实测/源码锚**：
- LOGIC SELECT 族 + 位运算 AND：modules.yaml:98-113（SELECT_GT=5、AND=0）
- wbe 16bit 字节写使能（RTL）：user_register.v:124 `w_ur_wbe_0=i_cru_ur[27:12]`；
  声明 modules.yaml:189；编译器 src/ 现无发射路径（grep 零命中，仅验证工具解析探针日志）
- FPTOINT=值转换非位重解释（RTL）：fpto_int_array.v:98-113（FP32→INT16 截断 + dst_pos 半字）
- magic seed 全为 host 注入：div_op.h:18、rsqrt_op.cpp:597（golden lambda）、:668-672（y0_tensor 输入）
- addtree + accumulate：modules.yaml:340-351；ADR-029；手写实证 matmul_op.py:410-412；
  sim fp32_add_tree_unit.py:81-105
- loop-carried 机制链：ii_analyzer.py:128-163（distance>1 不支持 :155-156）/
  ucode_solver.py:449-454 / payload_factory.py:104-109
- G-op 白名单：assembler.py:17,127-132（落笔时正面复核 `_V1_COMPUTE_ALLOWED={FPMUL,FPADD}`）
- legacy Max lowering：standard_kernels.py:108-118（落笔时正面复核）
- stride=0 全层机制：assembler.py:249-251（op_mask 门控）/ ucode_solver.py:441-444 /
  resource_axioms.py:138；手写 div_scalar_op_ur_inst.cpp:15
- 准入门文法 {+,*}：ordered_ast.py:58,78-133；configurable.py:67-68
- 多段链框架：unified_lcs_case_gen.py:417-423；stage_partition.py:105-165（仅 II≥2 同源段）
- UR 跨段保持：ADR-061:108,131-135
- 独立参考模式：fair_smul64w_ref_check.py（hbm_data 字节自解码，不依赖编译器 golden）
- UR 容量 8×256×128b：hw_params.yaml:8-9；fp32 4 元素/词：div_op.h:110-112
- IQ 墙 128/子队列：smi_top.v:22；ADR-055:45-49（SUB_IQ_NUM=4 各自独立）
- 延迟表：pipeline_delays.yaml:5-11,17（FPADD/FPMUL=4、addtree=4、LOGIC=1、RtoVR=1）
- 24 单元键枚举：modules.yaml:1-402 全键 + grep `exp|div|max|sqrt|reciprocal|softmax` 零命中
- 手写 exp/div/rsqrt 非活跃：Makefile:30-37 DISABLED_OP_DIRS；无 VCS 日志留存
- softmax 手写 op = stub（dtype 直通，数学只在 host golden）：softmax_op.cpp:274-318
- LOGIC SELECT 无 VCS 记录：验证链已 VCS 算子（mul/add/scale_sum/horner_v3u/addcmulu/
  scalar_mul）全为纯 {FPMUL,FPADD}；matmul_parallel_n 仅 sim（预检 :57-59,:83）

**推断/待验证**（落实前正面取证）：
- halve-and-square deg-15 误差链 ≈ 2⁻²⁰ 级（W1 数值验证定稿）
- addtree 落线可连接性（R8，按 matmul 产物模板核实）
- sim 多驻留读退化影响（R5，e2e 预检第一步）
- 多段链拼接的工程量拆分（R2，ADR 阶段细化）
