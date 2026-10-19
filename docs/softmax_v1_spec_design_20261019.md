# 受限 softmax v1：规格与最小归约实现路径设计（checkpoint ① 交付，v2 修订）

> 2026-10-19，主线令见 memory `softmax-mainline-directive-20261019`。
> 用户裁定（2026-10-19）：**「已有能力冻结，启动受限 softmax；先完成正确实现，再优化主要成本。」**
>
> **v2 修订记录（2026-10-19 晚，用户复核四点裁定，全部接受）**：
> v1 的「四组件全部可拼接、无阻断项、方案已闭合」口径**撤回**，更正为
> **「部分原语已确认，跨 lane 路径与数值规格待验证」**。具体修订：
> ① MAX 跨 lane 合并/广播构造不成立（LOGIC AND 只留 lane 不搬 lane、零填充会污染
> 全负输入；wbe 是字节写使能不是数据移位——对 `[m,0,0,0]` 分 lane 使能写出的是
> 源数据对应 lane 的 0，得 `[m,0,0,0]` 原样，RTL user_register.v:373 写使能与写数据
> 分接）→ **跨 lane 路径登记为当前真实缺口**（§3.1，不得反推硬件不可能）；
> ② exp halve-and-square 误差预算不成立：用户数值筛查（[−87,0] 200001 个 fp32 点、
> 逐步 fp32 Horner 12/15 阶 Taylor + 8 次平方）最大相对误差 ≈ 3.10×10⁻⁵，
> 超候选门槛 2⁻¹⁹≈1.91×10⁻⁶ ——**该路线按反例否证降级**（不否定所有系数方案），
> 改以常量选择表 k·ln2 规约为候选主路线（§3.3）；
> ③ 规格补欠：FTZ≠渐进下溢、s∈[1,L] 对近似实现须证、halving 级数须由声明最大
> 行长推导并覆盖二次幂端点、单位固定（L=元素数、W=UR 词数）、NR 的 eₙ² 是理想
> 算术关系不作 fp32 实现误差保证（§2）；
> ④「三趟是算法结构下界」**撤回**：在线 softmax（Milakov & Gimelshein 2018，
> arXiv:1805.02867）可融合 max 与分母扫描，三趟仅为本版易验证基线；「缺位重解释
> 故一切 k·ln2 规约不可行」**撤回**：只排除依赖该原语的具体构造，常量选择表不受此限。
> 用户裁决：规格/误差预算=目标暂留暂不定稿；G-op 扩集 ADR=启动设计与复核
> （ADDTREE 与 LOGIC 分别定义支持形态）；中期前=SUM 首证为最低交付、
> MAX 为条件推进项（先解决 lane 合并/广播路径再承诺 RTL 验收）。
> **当前不需要改功能 RTL，不扩大统一入口支持声明。**
>
> 口径纪律（继承 2026-10-14 预检）：「缺」= 通用公式入口缺该项能力；已有专用路径/
> 手写线/simulator 资产单列。「无专用 EXP/DIV/MAX 单元」≠ 不可实现。证据分级：
> 每条事实带 `文件:行号` 锚；实测/推断/筛查结论分列。

---

## 1. 目标与范围

**最终交付**：用户声明 `softmax(x, axis=-1)` → 编译器生成完整程序（非手写 IQ）：
`HBM 输入 → LOAD → softmax 计算 → STORE → HBM 输出`。

**v1 边界**（首版明确不做的事见 §8）：

| 维度 | v1 取值 |
|---|---|
| 行长 L | 固定、编译期常量；**单位固定：L=元素数、W=UR 词数**；bring-up L=16 元素（lane0-only 布局 W=16 词） |
| dtype | fp32（IEEE-754 binary32）输入/输出，中间量 fp32 |
| 归约范围 | 单 SMC、单 chunk、整行归约（跨 SMC 归约单独评估，不套等长分片） |
| 验证序 | 软件数值筛查 → RTL 仿真（VCS）→ 扩规模 + 板端窗口 |
| 性能口径 | 装载/计算执行/搬运/T_total 分列测量（perf_metrics_definition 类型 ② 纪律） |

**单 SMC 是首版边界，不能成为最终性能评价的全部**（主线令原文）。

## 2. 算法语义与数值规格（候选目标，暂不定稿）

### 2.1 数值稳定形式（唯一合法语义）

```
m  = max_{i∈[0,L)} x_i          # 行最大值（跨元素归约 + 比较选择 + lane 合并【缺口】）
e_i = exp(x_i − m)              # 指数逼近 + 减（候选路线 §3.3）
s  = Σ_{i∈[0,L)} e_i            # 跨元素归约 + 累加反馈（§3.2）
r  = 1 / s                      # 行内单次倒数（设计决策 D1）
y_i = e_i · r                   # 归一化 = L 次乘，非 L 次除
```

**D1（行倒数 + 逐元素乘）**：倒数成本付一次，归一化退化为逐元素乘。
代价：r 的一次舍入传播到全行（误差预算 §2.3 计入）。

**结构性质（对精确 exp 成立；对近似实现须逐条验证，§2.3-3）**：
s ∈ [1, L] 的前提是 max 元贡献 exp(0)=1 且其余 e_i ≥ 0——近似多项式 P 须验证
P(0)=1（或 P(0) ≥ 1−ε₀ 的下界）、P 在规约域上恒正、上界 P ≤ 1+ε₁，
以及累加舍入不破坏下界。验证通过前 s≥1 只作设计假设，不作已证结论。

### 2.2 输入域（候选目标）

沿用 ADR-056 §C.4 双道 AND 纪律：静态区间承诺 + 装载数据动态核验。

- 候选：`x_i ∈ [−2^7, 2^7] = [−128, 128]`，正规 fp32；NaN/Inf/亚正规响亮拒绝。
- **下溢语义欠账（必须补齐）**：FPADD/FPMUL 对亚正规中间值是 FTZ 还是渐进下溢，
  RTL 未核实（bounded 项）；**两者不是同一语义**。输出小概率值的误差判据须以
  绝对误差给出（如 y_i < 2⁻¹²⁶ 时按 abs err ≤ 2⁻¹²⁶ 计，或允许精确零并给出理由），
  不得笼统写「flush 为零算法上无害」。

### 2.3 三层验收指标（候选目标，软件筛查后定稿）

| 层 | 指标（候选值） | 方法 |
|---|---|---|
| ① 实现正确性 | RTL（VCS）输出 vs **独立实现的同一近似算法、同一舍入顺序**参考：逐位 bit-exact | 独立脚本从 hbm_data 字节自行解码重算（fair_smul64w_ref_check.py 模式），系数/求值序/舍入/下溢行为先冻结成规格再实现参考 |
| ② 算法精度 | vs float64 稳定 softmax：rel err ≤ 2⁻¹⁹（**候选目标，未证实可达**；y_i ≥ 2⁻¹²⁶ 的正规输出）；\|Σŷ − 1\| ≤ 待定；非负性；极端三案 | 同一独立脚本内第二个参考函数（float64 全链）——两参考算法/精度独立，防假闭环 |
| ③ 性能 | 装载 / T_exec_obs / 搬运 / T_total 四列；不同逼近方案**同一精度门槛**下比较 | perf_marker_extract.py；板端时间属类型 ④ 单列 |

**极端三案**：全等行 → 均匀 1/L；单主元 → one-hot；|x| 近 128 大幅度混合行。
**舍入顺序冻结**：max 比较序、sum 累加序、exp 求值序、NR 迭代序、规格化级联序
全部写成规格文本（注册表 FORMULA_SEMANTICS 追加惯例）。**NR 的 e_{n+1}=e_n² 是
理想算术关系**，fp32 下每轮附 2–3 次舍入，五轮后误差≈数次 ulp 量级而非 2⁻³²，
最终以筛查实测定稿。

## 3. 硬件实现路径核查（v2：部分原语已确认，跨 lane 路径与数值规格待验证）

**已确认原语**（RTL/hw_config 实证）：FPADD(SUB/UPDATE_ST)、FPMUL、LOGIC
（AND/SELECT_GT 等 optype 表 modules.yaml:98-113）、FP32ADDTREE5TO1+accumulate
位（modules.yaml:340-351）、UR stride=0 驻留读全层机制、UR wbe 16bit 字节写使能
（RTL user_register.v:124 `[15:0] w_ur_wbe_0 = i_cru_ur[27:12]`）、FPTOINT/INTTOFP
**值转换**（fpto_int_array.v:98-113：FP32→INT16 数值截断 + dst_pos 半字定位，
**非位重解释**）。
**无专用 EXP/DIV/MAX 单元**（modules.yaml 24 键枚举 + grep 零命中 + sim 注册表
对照，三路证实；v1 的「19 单元」口径更正为 24 键）。
**片上无浮点位重解释原语**（同上 RTL + 手写线 magic seed 全为 host 注入数据：
rsqrt_op.cpp:668-672 y0_tensor、div_op.h:18）——仅排除依赖该原语的具体构造。

### 3.1 MAX —— 跨 lane 合并/广播 = 当前真实路径缺口

- **元素级比较选择**（lane 内）已确认可行：FPADD(SUB=1, UPDATE_ST=1) 产状态位 →
  LOGIC SELECT_GT；生成器先例 standard_kernels.py:110-118；sim logic_unit.py。
  ⚠ 该路径在冻结 TB 无 VCS 记录（已 VCS 算子全为纯 {FPMUL,FPADD}）。
- **【缺口】lane 间移动/重排**：v1 的两处构造均不成立（用户复核）——
  LOGIC AND 掩码只在原位保留 lane（`[M0,M1,M2,M3] AND lane1_mask = [0,M1,0,0]`，
  不对齐 lane0，且零填充会污染全负输入的 max）；wbe 只选目的字节、不搬源数据
  （user_register.v:373 写使能与写数据分接）。全原语表内未发现 lane 重排单元
  （RtoVR=整词路由、VIN=reg_pipe、FPTOINT/FPTOFP 的 pos 字段=lane 内半字定位）。
- **P0 标量自环 mask 合法性已核**（RtoVR.v 稀疏分支）：LOGIC_S0/S1 允许集含
  VIN0_D/VIN1_D(11/12)、LOGIC_D(10) 自环、FPADD_D(4)、FPMUL_D(3)（:249-250）；
  LOGIC_ST ∈ {INTADD_ST(2), FPADD_ST(5), VIN0_D, VIN1_D}（:251）。⇒ 运行 max 的
  全标量比较-选择链（FPADD SUB/UPDATE_ST → LOGIC SELECT，m 经 LOGIC_D 自环
  进位）在冻结 TB 全合法，与 SUM 同走串行自环路线（RecMII = 4+1+1+1 = 7：
  LOGIC(1)→RtoVR(1)→FPADD(4) 比较回路 + LOGIC 自环 2）。
- **候选替代（待有界取证，不预先承诺）**：
  - **P0 lane0-scalar 布局**：输入在 HBM 预置为每词 1 元素（lane0，其余填 0），
    全部趟次 lane0 标量运算；逐项 LOGIC AND 掩码清污（exp 常数项会污染 lane1-3，
    必须每阶段掩码）；addtree/串行 FPADD 累加只吃 lane0。
    代价 ≈ 4× 算术与 HBM 词量（正确性优先可接受，W3 优化项）；
    布局变换属编译期数据准备，不动硬件。**v1 可行性以此路径为基线评估对象**。
  - **P1 packed 布局**：需 lane 合并原语——SMI_DS_T 是 cross-SMC transpose
    （modules.yaml:390-401），属跨 SMC 机制，能否作 lane 重排载体未证实；
    手写线 Mean-dim 目录存在（DISABLED_OP_DIRS 内）可作参照。
  - **取证用例（用户指定，无论哪条路径都必须覆盖）**：全负输入、最大值分别位于
    四个 lane、最大值位于首词/末词。
- **判定**：⚠️ **条件推进**（用户裁定）：先解决 lane 合并/广播路径，再承诺 RTL 验收。

### 3.2 SUM（首证 = 最低交付；五取证已核，结论改判：addtree 冻结 TB 不可达 fp32 → 串行 FPADD 自环）

**五取证结论（2026-10-19 RTL 正面取证，u280_sim_v422 冻结树 = TB 3324a33b 源树）**：

- **① addtree RtoVR 连接：否证（fp32 不可达）**。冻结 TB 实编译稀疏 mask 分支
  （run_sim_aiacc_top_f_multi_stage.sh:119 `+define+USE_RTOVR_SPARSE_MASK`）：
  RtoVR.v:262-269 `OUT_FROM_IN_EN_24..31 = bits 18..25` ⇒ FP32ADDTREE5TO1_S0..S7
  **只能**收 FP16MUL_D0..D7；5to1_D（in 26/27）只能喂 8to1_S0/S1（RtoVR.v:270-271）；
  UR_WRITE 源 mask 0x30002000 = {VOUT0_D(13), 8to1_D(28), SHIFTUP_D(29)}（RtoVR.v:273-276）
  **不含 5to1_D**。⇒ 5to1 在冻结 TB 是 INT8 MAC 链专用累加段
  （INT8TOFP16→FP16MUL→5to1→8to1→UR_WRITE 硬串联），fp32 值无合法路径进入
  （必经 INT8TOFP16/FP16MUL 会摧毁 fp32 语义）。rtovr_config.yaml:109-118 与 RTL 一致。
  matmul_op.py:410-412 先例属该 INT8 链语境，**不能外推到 fp32 SUM**。
- **② 累加初值**：`old_i = (inst_valid_eff && accumulate) ? add_result_stage4[i] : 0`
  （fp32_adder_tree_final_pipeline_5to1.v:146-148）；stage4 寄存器仅 rst_n 清零、
  被下一 valid 结果覆盖（:498-504）。⇒ **accumulate=0 的首迭代即零种子**。
- **③ 反馈依赖**：自环距离 1；stage4 在 issue+3（valid_pipe[2]）锁存 ⇒ II≥4 时进位
  已就绪（与 matmul `loop_carried_depends_on={"y_0":4}` 同构）；ii_analyzer.py:155
  distance>1 不支持——softmax 距离 1 够用。
- **④ 末次写回**：dr_d0={add_result[0..3]}、dr_d1={[4..7]}（:518-521）；但稀疏约束下
  5to1_D 不可直写 UR——须先过 8to1（in 26/27→OUT_32/33，out 28→UR_WRITE）。
- **⑤ 行间状态清理**：RTL 无显式复位原语；契约 = **每行首迭代 accumulate=0**
  （忽略 stage4 残留），行间天然隔离。
- **⑥ 舍入模式**：i_rounding_mode 全 SMC 硬连 3'b000 = **RNE**
  （aiacc_top_f.v:1919、:2044）——独立参考与误差筛查必须按 RNE。

**fp32 SUM 冻结 TB 唯一合法载体 = 串行 FPADD 自环**（稀疏 mask 内逐项合法）：

- FPADD_S0/S1 允许集 0x1FDB 含 **FPADD_D(4) 自环**与 **VIN0_D/VIN1_D(11/12)**
  （RtoVR.v:243-244；yaml:90-91）；FPADD_D→VOUT0_D→UR_WRITE ✓（:254→:273-276）。
- 数据路径：T2 exp 末级 FPMUL_D **直送** FPADD_S0（FPMUL_D(3) ∈ 0x1FDB ✓，免 UR
  回读）；进位 s：FPADD_D→RtoVR→FPADD_S1；RecMII = FPADD(4)+RtoVR(1) = **5**。
- 零种子/行间清理：FPADD 无内部累加器——行首迭代 S1 改接 host 预置**零常量字**
  （UR 常驻 stride=0 读），后续迭代 S1 接 FPADD_D；行间清理天然，无残留状态。
- sim 先例：exp 手写线 RTOVR_IN_DR_FPMUL_D 自环（sim only）；**VCS 无自环先例
  = 本证新 territory**，e2e 首验即此。

- **判定**：⚠️ 载体改判（addtree→串行 FPADD 自环），路径合法性已 RTL 实证；
  **VCS 首证对象 = L=16 元素 lane0-only 小例，II=5**；预期收益：e2e 链路
  （VIN 入→FPADD 自环→VOUT0 出）全 sparse-legal，sim/VCS 语义差风险低
  （dense sim ⊇ sparse，合法路径两者同）。

### 3.3 EXP（v2：常量选择表 k·ln2 规约为候选主路线；平方路线按用户筛查反例降级）

- **候选主路线（纯值运算，无位重解释依赖）**：
  `exp(t) = 2^k · P(r)`，`k = round(t·log2e)`（FPMUL + **FPTOINT 值转换**，RTL 已证）、
  `r = t − INTTOFP(k)·ln2`（INTTOFP 值转换 + FPMUL + FPADD-SUB），r ∈ [−ln2/2, ln2/2]；
  `2^k = 2^{k_hi}·2^{k_lo}` 两级常量选择表（SELECT 级联取 host 预置常量，
  无数据相关寻址需求——UR 地址流是静态步进，表查找只能以选择级联表达）；
  `P` = 规约域上 deg≈9–10 多项式（Horner，系数 host 预置，horner const bank 先例）。
  **无平方放大**：误差 ≈ 多项式求值舍入 ~2⁻²² 级 + 规约/末乘各 1 ulp，有机会满足
  2⁻¹⁹ 候选门槛——**须筛查证实，不预先声明**。
  待核子项：FPTOINT 舍入模式（截断/RNE 影响 k 取值与 r 域）、负数 t·log2e 的
  符号处理（INTTOFP src_sign 字段存在）、级联表规模随域宽。
- **被否证路线（留档）**：halve-and-square（t/256 + deg12/15 Taylor + 8 次平方）
  用户筛查 max rel err ≈ 3.10×10⁻⁵ > 2⁻¹⁹ ——平方链放大早期舍入是该形态的固有
  成本。**此为该候选的反例证据，不否定所有系数方案**；若后续换系数/换阶数/换
  融合方式，须冻结系数、运算顺序、逐步舍入及下溢行为后重测。
- **成本（重算，用户口径）**：Horner d 阶 = d 乘 + d 加；主路线合计约
  **35–45 次算术操作/元素**（规约 3–4 + 多项式 ~20 + 表级联 ~15–20 + 末乘 1），
  以最终冻结方案为准。
- **手写线参照**：op/exp Taylor（无系数注入路径、DISABLED_OP_DIRS、无 VCS 日志）；
  v4.19 旧 case exp_8smc_v3 用 coeff 预置（rel_tol=0.03、无 PASS 日志）——
  均降级「参考资产」，不作能力证据。
- **判定**：⚠️ 需新增逼近设计；**软件数值验证提前**（用户裁定第 4 条），
  不等中期后。

### 3.4 倒数 / 归一化（规格化 + NR；fp32 实现误差不借理想关系承诺）

- 设计假设 s ∈ [1, L]（§2.1 待证条款）。构造（全直线型、纯值运算）：
  ①固定级数 halving（FPMUL ×0.5）+ SELECT 级联规格化 s′ ∈ [1,2) 并同索引选出
  2⁻ᵏ 常量；**级数由声明的最大行长推导并覆盖二次幂端点**：级数 =
  ⌊log2(L_max·(1+ε₁))⌋+1（L_max=1024 时 s 可达 2¹⁰，级数 11 级 2⁰..2¹⁰，
  端点 s=1024 → s′=1 必须命中）；
  ②NR：`r′_{n+1} = r′_n·(2 − s′·r′_n)`，r₀=0.75 常量 ⇒ 理想 |e₀|≤0.5、
  eₙ₊₁=eₙ²；**fp32 实现误差以筛查实测为准**（理想 5 轮 2⁻³² 不可直接引用）；
  ③r = r′·2⁻ᵏ。
- 成本 ~50 ops/行一次。
- **判定**：✅ 构造可行（值运算、直线型）；误差与级数随数值筛查定稿。

### 3.5 标量驻留（跨段保持已证；四 lane 广播与 3.1 同缺口）

- stride=0 驻留读全层贯通（assembler.py:249-251 op_mask 门控、ucode_solver.py:441-444
  invariant 豁免、resource_axioms.py:138；板端 PASS 先例）；UR 跨段保持
  （ADR-061:108,131-135）。
- **标量供四 lane 使用**：与 §3.1 同一缺口——P0 布局下 lane0 单 lane 使用**不需要**
  广播（x/m/e/s/y 全 lane0）；P1 packed 布局才需要 [m,m,m,m] 广播。
- **判定**：P0 下 ✅ 可复用；P1 下随 lane 缺口条件推进。

### 3.6 资源与存储评估

- UR 8 bank × 256 词 × 128bit（hw_params.yaml:8-9）；fp32 4 元素/词（div_op.h:110-112）。
  P0 布局：W = L（每词 1 元素）；L=16 元素 bring-up 用 16 词。容量富余。
- IQ 墙 128 行/子队列 × SUB_IQ_NUM=4 各自独立（smi_top.v:22；ADR-055:45-49）。
  ⚠ 勿混 PARAM_IQ_DEPTH:256（hw_params.yaml:4）与 Jianyuan TB 默认 32
  （div_op_constants.h:47）。
- 延迟表（pipeline_delays.yaml 锁步）：FPADD=4、FPMUL=4、LOGIC=1、RtoVR=1、
  addtree=4。
- 布局提案（P0）：bank1=x（lane0-only）、bank2=e（y 覆写 e）、bank3=常量
  （多项式系数/选择表/0.75/mask）、m/s/r 驻留字；容量瓶颈预计在 IQ 行数不在 UR，
  实测校准。

## 4. 编译器落地路径（四层改动面 + 立法序清单）

### 4.1 表达层（需新增，立法序：ADR）

- 现状：yaml formula → 原子 Symbol（configurable.py:67-68）；准入门文法只认 {+,*}
  （ordered_ast.py:58）；函数/比较/下标/求和构造全拒（:78-133）。
- 改动：结构受限的归约入口 + 逼近函数语法；ordered_ast 参考发生器同步扩展。

### 4.2 lowering 层（需新增，机制件全在）

- 归约 lowering 生成 addtree/LOGIC 节点 + cumulative/loop_carried metadata
  （matmul_op.py:394-437 手搭模式配置化）；配置链 addtree 特例 cumulative=False
  （standard_kernels.py:33-73）需扩。exp/倒数 lowering = §3.3/§3.4 子图模板。

### 4.3 调度层（机制在；准入 ADR **启动设计与复核**，用户裁定）

- loop-carried SMT 约束已存在（ucode_solver.py:449-454）；RecMII 已有；自环
  latency≥2 ⇒ 天然 II≥2 ⇒ 满足 unroll G-II（assembler.py:505-510）。
- G-op 白名单 `_V1_COMPUTE_ALLOWED={FPMUL,FPADD}`（assembler.py:17,127-132）。
  **ADR 须分开定义 ADDTREE 与 LOGIC 的具体支持形态**（用户裁定：不是把单元名
  加进白名单就算准入成立）——ADDTREE：cumulative 形态、reduction_per_channel、
  初值/清理语义、连接约束；LOGIC：允许的 optype 子集、与 FPADD ST 位的配对形态、
  掩码常量形态。

### 4.4 发射 / 分段层（最大新增工程量）

- 异构多 COMPUTE 段框架缺（段链现状 unified_lcs_case_gen.py:417-423 全部段同源
  同调度 stage_partition.py:105）；须每段独立编译 + 段链拼接 + 段间标量驻留约定；
  TB 辅助多段推进契约已支持（stage_manifest.py:25-27）。
- 若 v1 采 P0 布局，**无需 wbe 广播发射新增**（v1 该项撤回）；P1 才需要。

### 4.5 验证层（需新增脚本，模式有先例）

- 独立参考脚本（fair_smul64w_ref_check.py 模式）；ADR-056 自动参考发生器只支持
  {+,*}（ordered_ast.py:58）——同近似同舍入参考必须独立脚本。
- sim 预检：fpadd/fpmul/logic/addtree(cumulative) 模型在（kernel.py:91-101）；
  ⚠ 多驻留读 sim 退化（configurable.py:297-300）影响未评估，e2e 第一步必查。

## 5. 存储与数据流规划（v2：三趟 = 本版易验证基线，非算法下界）

### 5.1 基线结构（P0 布局）

```
T1 (max):    读 x_i（lane0 标量流）     → 运行 max m（FPADD ST + LOGIC SELECT 标量反馈）
T2 (exp+sum):读 x_i + 读 m（stride=0）  → e_i 写 UR + 掩码清污 + addtree 累加 s
T3 (norm):   读 e_i + 读 s（stride=0）  → r=1/s（规格化 NR，一次）→ y_i=e_i·r 覆写 e 区
```

**「三趟是算法结构下界」已撤回**：在线 softmax（arXiv:1805.02867）融合 max 与
分母扫描为单趟，可降访存；是否适合本硬件（lane 组织、反馈成本）列入 W3 评估。
v1 采三趟因其**最易验证**（每趟语义独立、验收可分层），不因其是下界。

### 5.2 段结构

`LOAD → COMPUTE(T1) → COMPUTE(T2) → COMPUTE(T3) → STORE`（异构三段，§4.4 框架）；
段间无 HBM 往返，标量经 UR 驻留跨段。

## 6. 执行顺序（用户 2026-10-19 裁定版）

1. **本文档修订**（半天–一天）：无阻断口径→部分原语已确认；跨 lane 与数值规格
   登记待验证。✅ 本轮落地（v2）。
2. **SUM 主线**：✅ 五取证 RTL 已核（§3.2：addtree fp32 不可达=否证，串行 FPADD
   自环=唯一合法载体，II=5，RNE 硬连）→ 配置化生成（Manual-DFG 先例路径，绕开
   准入门立法序；公式入口 Σ 归 ADR）→ 小规模 VCS L=16（**最低交付**）。
3. **MAX 有界路径取证**：lane 合并/广播路径（P0/P1 评估），用例必覆盖
   全负输入、最大值在四个 lane 各一次、首词/末词。
4. **EXP/倒数软件数值筛查提前**：冻结系数/顺序/舍入/下溢行为后测；输入域与
   2⁻¹⁹ 维持候选目标不冻结。
5. G-op 扩集 ADR：ADDTREE/LOGIC 分别定义支持形态，hco 复核。
6. 中期后 W1–W4 按主线令表推进（W2 端到端基线、W3 实测优化、W4 扩规模板端）。

**当前不改功能 RTL；不扩大统一入口支持声明。**

## 7. 风险登记册（v2）

| # | 风险 | 等级 | 处置 |
|---|---|---|---|
| R0 | **跨 lane 合并/广播无已证原语**（MAX 路径缺口） | **高** | P0 lane0-only 布局为基线评估对象；P1 待 DS_T 等取证；用户指定用例集 |
| R1 | G-op 扩集 ADR 需分单元定义支持形态 | 中 | 形态清单随 ADR；hco 复核 |
| R2 | 异构多 COMPUTE 段框架新增工程量 | 中 | SUM/MAX 首证先行，完整 softmax 争取项 |
| R3 | 加法树/串行两形态舍入序分裂，golden 双轨 | 低 | v1 只保留一种并冻结顺序 |
| R4 | exp 误差预算未成立（筛查反例已否证平方路线） | **高** | 软件筛查提前（步骤 4）；表规约路线待筛查证实 |
| R5 | sim 多驻留读退化影响未评估 | 中 | e2e 预检第一步 |
| R6 | exp 每元素 ~35–45 ops，计算成本远高于已证 map 算子 | 中 | 预期内；W3 优化；同精度门槛比较 |
| R7 | LOGIC SELECT / cumulative addtree 冻结 TB 均无 VCS 记录 | 中 | SUM 首证补 addtree；MAX 落地时补 LOGIC |
| R8 | ~~addtree RtoVR 连接受限~~→**已实证否证**：稀疏 mask 下 addtree=INT8 链专用，fp32 不可达 | 已闭合 | SUM/MAX 改串行自环（§3.2 实证）；addtree 留待未来 TB 放宽 |
| R9 | P0 布局 4× 算术/HBM 词量 + 逐阶段掩码成本 | 低 | v1 正确性优先接受；W3 评估 packed |
| R10 | IQ 定长指令：数据相关控制流必须直线化 | 低 | §3.3/§3.4 全部 SELECT 级联直线化 |
| R11 | 手写线 exp/div/rsqrt 非活跃资产无 VCS 日志 | 低 | 一律「参考资产」不作能力证据 |
| R12 | FPTOINT 舍入模式/负数符号语义未核实 | 中 | 筛查前 bounded RTL/sim 核实 |
| R13 | s∈[1,L] 对近似实现待证（P(0)/恒正/上界/累加舍入） | 中 | 数值筛查合同条款 |

## 8. v1 不做清单

跨 SMC 归约（单独评估）· 变长行长/ragged · 多 chunk · INT8/FP16 · 逐元素除法 ·
streaming normalize · 分组加法树（W3 候选）· 位重解释规约路线（该原语已证缺席）·
在线 softmax 融合（W3 评估）· packed 布局（P1 条件推进）· 功能 RTL 修改 ·
扩大统一入口支持声明 · 任何「减少几条 INIT 指令」式主线优化。

## 9. 事实锚附录（证据分级）

**实测/源码锚**：
- LOGIC optype 表：modules.yaml:98-113（AND=0、SELECT_GT=5 等）；PRECISION 位 :113
- wbe 16bit 字节写使能：user_register.v:124（`[15:0] w_ur_wbe_0`）；写使能与写数据
  分接 user_register.v:373（v1 广播构造不成立的 RTL 依据，用户复核提供）
- FPTOINT=值转换：fpto_int_array.v:98-113（FP32→INT16 截断 + dst_pos 半字定位）
- magic seed 全为 host 注入：rsqrt_op.cpp:668-672（y0_tensor）、div_op.h:18
- addtree+accumulate：modules.yaml:340-351；matmul_op.py:410-412；sim fp32_add_tree_unit.py:81-105
- loop-carried 链：ii_analyzer.py:128-163（distance>1 不支持 :155）/ucode_solver.py:449-454/
  payload_factory.py:104-109
- G-op 白名单：assembler.py:17,127-132（v2 落笔复核）
- stride=0 全层：assembler.py:249-251/ucode_solver.py:441-444/resource_axioms.py:138；
  手写 div_scalar_op_ur_inst.cpp:15
- 准入门文法：ordered_ast.py:58,78-133；configurable.py:67-68
- 多段链框架：unified_lcs_case_gen.py:417-423；stage_partition.py:105
- UR 跨段保持：ADR-061:108,131-135
- UR 8×256×128b：hw_params.yaml:8-9；IQ 墙 smi_top.v:22；延迟 pipeline_delays.yaml:5-11,17
- 24 单元键枚举 + 无 EXP/DIV/MAX：modules.yaml:1-402 + grep 零命中 + sim kernel.py:91-101
- 手写 exp/div/rsqrt 非活跃：Makefile:30-37 DISABLED_OP_DIRS；softmax 手写 op=stub
  （softmax_op.cpp:274-318 数学全在 host golden）
- SMI_DS_T = cross-SMC transpose：modules.yaml:390-401（P1 候选载体，未证实）
- 用户 exp 筛查（2026-10-19）：[−87,0] 200001 fp32 点、逐步 fp32 Horner deg12/15
  Taylor + 8 平方，max rel err ≈ 3.10×10⁻⁵ > 2⁻¹⁹ ——【筛查证据：否证该候选，
  不否定所有系数方案】
- SUM 五取证 RTL 锚（2026-10-19，u280_sim_v422 冻结树）：
  - 稀疏 mask 实编译：run_sim_aiacc_top_f_multi_stage.sh:119（+define+USE_RTOVR_SPARSE_MASK）
  - addtree 输入锁定 FP16MUL_D0..D7：RtoVR.v:262-269（OUT_FROM_IN_EN_24..31 = bits 18-25）；
    yaml rtovr_config.yaml:109-116 一致
  - 5to1_D 只能喂 8to1：RtoVR.v:270-271；UR_WRITE 源={VOUT0_D,8to1_D,SHIFTUP_D}：RtoVR.v:273-276
  - 元素级 S 口允许集 0x1FDB（含自环 FPADD_D/FPMUL_D/LOGIC_D、VIN0_D/VIN1_D）：
    RtoVR.v:238-250（FPADD_S=243-244、LOGIC_S0/S1=249-250、LOGIC_ST=251、VIN=252-253、VOUT0=254）
  - addtree accumulate/初值/清理：fp32_adder_tree_final_pipeline_5to1.v:146-148（old_i 选择）、
    :498-504（stage4 锁存/仅 rst_n 清零）、:518-521（d0/d1 打包）、:72-75（smc_id_en/inst_valid）
  - 舍入 RNE 硬连：data_engine/aiacc_top_f.v:1919、:2044（i_rounding_mode(0)）
  - 实例化点：data_engine/SMC_ALL.sv:761-768（5to1，i_rounding_mode 透传）

**推断/待验证**（落实前正面取证）：
- 表规约 exp 路线误差 ≈2⁻²² 级有机会满足 2⁻¹⁹（筛查证实前不作结论）
- FPTOINT 舍入模式与负数符号语义（R12）
- addtree 落线可连接性（R8）、累加初值/行间清理语义（§3.2 清单）
- P0 布局掩码纪律的完备性（每个多项式阶段后 lanes1-3 归零，e2e 预检验证）
- DS_T 可否作 lane 重排载体（R0/P1）
- sim 多驻留读退化影响（R5）
