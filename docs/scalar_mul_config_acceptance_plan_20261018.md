# scalar_mul 配置记录与验收计划（2026-10-18）

依据：用户 2026-10-15 令四点——①新增「标量乘」公式配置、不新增算子名称特判，
先核对手写实现实际操作数顺序与 lane 语义；②先判断是否需要扩展契约：落在既有
语法/准入/II=1 发射范围内则只记录配置与验收计划，不开大型 ADR，涉及不支持形态
则单独提设计问题；③先验证正确性再配对取数（两边同输入字节/α/元素数/SMC 数/
LOAD/STORE 范围/RTL/TB，从小规模开始）；④计算窗与完整执行成本分开报告。

本文为点②要求的「记录配置与验收计划」，非 ADR。

## 1. 手写侧语义核对（用户点①前置）

来源（只读）：`~/Jianyuan/aiacc/SMI_IQ/op/mul_scalar/mul_scalar_op_ur_inst.cpp:13-21`
与 `load_store_v3/mul_scalar/mul_scalar_lsu_op.cpp`、手写 case
`smi_iq_inst/stage_1/iq_inst_69_vin1_subq0.txt`。

- port0 = 输入 x：`addr_word = para_input_addr + step`（流式步进）；
- **port1 = 标量 α：`addr_word = para_other_addr` 无步进，原注释
  "fixed address (true scalar semantics)"** —— 固定地址 stride=0 驻留；
- 4 lane fp32 / 16B 词，α 在每词内复制 ×4；golden 读 `scalar[i%4]`；
- 手写 LOAD 把 255 个复制 α 词整载 UR bank1，但 compute 只读词 0
  （装载不对称，配对时须裁到 1 词——见 §5）。

结论：两边语义可严格对齐为 y_i = fp32(α·x_i)，α 单标量驻留、x 流式、
4 lane —— 与编译器 param 角色（stride=0）机制同构。

## 2. 配置（verification_v2/ops/scalar_mul.yaml，配置-only 零代码改动）

- `formula: "alpha * x"`；张量：x(bank0,input,stride=1) /
  alpha(bank3,param,stride=0 驻留,消耗 1 word) / y(bank2,output) /
  constants(bank7)；布局与 mul.yaml 同构（流式 other 换成 param α）。
- test_data 安全域同 scale_sum §C.4-2：x∈[1,2]、α=2.5 → y∈[2.5,5]，
  ⊥ 亚正规带且 ⊥ ±FLT_MAX。
- **不声明 experimental_unroll**（实证其为空操作，见 §3）——避免暗示走了
  unroll 准入链。

## 3. 路径判定（2026-10-18 探针，全实证）

| 探针 | 方法 | 结果 |
|---|---|---|
| legacy 路径是否接受 param 角色 | 无标记 e2e | 绿，golden 2.5·x 逐位对账通过 |
| SMT 分配 II | e2e 日志 [II-ANALYSIS] | **II=1**（FPMUL count=1 lat=4 全流水 II_res=1，MII=1 SAT） |
| 发射形态 | physical_instructions.json 解码 | level-0 平坦压缩流：INIT(LOOPS=5) + BODY(VLD=1,**LOOPS=63**=64 拍,SMC_ID_EN=7) + TERM(JUMP=1)，13 队列 ×3 行 |
| 与 mul 已证形态关系 | 结构对照 | 同型（mul：II=1 flat 13 行 ✅VCS，ADR-054 阶段2-M1 compiler_mul COMPARE PASSED 2048/2048） |
| experimental_unroll 是否影响产物 | 开/关两轮 e2e 全产物 sha256 对照（35 项） | **逐字节一致 ⇒ 空操作** |
| 终版 yaml 产物 | 复跑 e2e 对照 | 与探针轮逐字节一致 |

**判定（用户点②）**：完全落在既有语法、准入与 II=1 发射范围内（mul 先例形态），
**不需要扩展契约、不需要大型 ADR**。`role:"param"` 的 stride=0 驻留机制由
ADR-056 §A.2-4 已覆盖；公式前端 Mul(alpha, x) 与 scale_sum 同族。

⚠ 边界注记：本形态（II=1 flat/压缩行）在 v3 注册表 unroll 记录的
proven_boundary 之外（PROJ_FETCH_UNROLL/PROJ_ISSUE_UNROLL 明确排除 flat 与
压缩行）；其验收依据是 **mul 先例证据类**（ADR-054，VCS COMPARE），不是 v3
unroll 记录。若未来要对本形态做协议层逐拍仲裁，须按冻结语义走
「探索 → 独立论证 → 追加冻结」，不得引用 v3 unroll 常数外推。

## 4. 验收计划（五步序适配，Correctness 先行）

- **步① 静态确认**（待做）：公式三层签名——文本 `alpha * x` == 有序 AST
  Mul(alpha, x) == 参考求值序 [mul]（单节点，一次 fp32 乘，一次舍入）；
  张量/stride/布局表；安全域论证（§2）。产出静态确认文档。
- **步② 小规模 VCS**（✅GREEN，2026-10-18）：case `compiler_scalar_mul`
  打包（`package_vcs_case.py`，13 IQ + ur_data + output_bank；
  n=256 元素=64 词/SMC，SMC_ID_EN=7 八 SMC）；
  TB sha256 3324a33b…f7c5 跑前核未漂移；裸运行器 exit=0，
  **SIM PASSED + COMPARE PASSED 首跑即绿**；非空转核验（8 bank×256 行
  新 dump、bank0=x 递增、bank2=y=2.5·x 与 e2e golden 样值一致）。
  **该 case 形态 UR 进/UR 出（TB $readmemh 预载 UR bank，零 HBM 触达）
  ——不适用 staging wrapper（无全局 sram 污染面），用裸运行器**。
  证据归档 `verification_v2/artifacts/scalar_mul_probe_20261018/`
  （10 项 sha256）。签收口径：该 yaml 配置 × n=256 元素/SMC × 8 SMC
  VCS COMPARE PASSED，限此配置此规模，不构成容量声明。
- **步③ 容量边界**（待做，VCS 实测口径）：平坦流行数与 n 无关（恒 3 行），
  约束 = UR bank 深度（x/y ≤256 词/SMC）与 LOOPS 字段宽；
  **n_max 以 VCS 实测为准，构造预测禁用于准入**。与手写 255 词/chunk 的
  相容性：255 词 = 1020 元素 ≤ 1024（256 词 bank），迭代数 255、LOOPS=254
  ——待实测，不作准用依据。
- **步④ 协议层仲裁**（条件项，本期不做）：见 §3 边界注记。
- **步⑤ 收口**（待做）：注册表 v3 按追加冻结惯例补
  FORMULA_SEMANTICS_SCALAR_MUL（公式文本/求值序/独立参考 +
  proven_boundary = 该 yaml × 已测规模集合；独立论证文档先行，
  引用 mul 先例证据类而非 v3 unroll 记录）。

## 5. 配对设计问题——**已裁定（2026-10-18 用户）**

> **选 C 作为主线：共用 LOAD/STORE 三段框架，分别放入手写和编译器的
> COMPUTE 段；B 作为其中的计算窗指标，A 暂不启动。**
> 「这是公平对照实验的组合程序，**不是**新增的公式驱动统一 LCS 能力。」

执行指令（用户原文六点）：

1. **先做小规模配对**：沿用已验证的每 SMC 256 元素（64 UR word），双方均
   8 SMC；**暂不以 255 词作为首个对照点**（255 词只作后续容量与长度兼容性
   实验）。
2. **冻结公共框架**：双方相同输入字节、α、UR/HBM 地址、LOAD/STORE 指令、
   RTL/TB、同步与计时机制；仅替换计算段及其必需队列使能信息，**记录全部
   差异**。
3. **α 装载裁剪两边一致**：双方确实只读固定地址一词 α 才可共同缩为一词；
   先确认单词 LOAD 描述符是已验证合法形态；若仍需 guard 搬运，两边同样
   guard，不能只优化一侧。
4. **两条完整链先验正确性**：真实 LOAD→COMPUTE→STORE，**逐 SMC 比较、
   8/8、零 SKIP**；独立参考与非目标写检查保留。现有「SMC_ID_EN=7＋
   COMPARE PASSED」**须核实实际比较目标列表**，不能只凭使能字段推定 8 路
   均已比较（步② compiler_scalar_mul 的 compare 目标 = smc_0 单侧 golden，
   已登记待核）。
5. **正确后再取数**：同报指令行数、装载成本、触发至目标末写观测窗口、
   同步开销、完整程序时长；屏障释放作结束锚 = 上界，不称纯计算周期；
   双方同一口径。
6. **flat 执行投影未验证**：本轮做明确限定的事件与数值对照，不套用 v3
   unroll 常数，不宣称 flat 逐拍契约已补齐。

**lane 前提（用户补充，写入双方输入规格）**：手写 `scalar[i%4]` 允许四 lane
不同系数；本轮固定四 lane 全为 2.5，才是通常意义的单一标量乘。

选项 A 留作后续产品能力扩展，不阻塞当前公平对照；B 降为 §5 取数中的
计算窗指标。

**C 主线执行收口（2026-10-18 当日完成）**：设计
`docs/fair_pair_scalar_mul_64w_design_20261018.md`；结果归档
`verification_v2/artifacts/fair_pair_smul64w_20261018/`（15 项 sha256）。
要点：①框架=手写生成器 64 词（α 单行补丁 2.5 生成后即还原，树零漂移；
scratch 隔离全局 sram）；②α 装载裁定=双侧共同整载（证据库无单词描述符
先例：r2 255w 整载、LCS n16 α=4 行整载）；③yaml α bank 3→1 配置-only
重对齐 + 步②复验 PASSED（compiler_scalar_mul_b1）；④两变体指令 4 全绿
（COMPARE 真 8/8 零 SKIP——步② smc_0 单侧前科关闭、verify_ddr 8/8、
独立参考先互校后作判据、非目标写零）；⑤取数：COMPUTE 行数 28 vs 39、
装载 39 vs 54 beats（+195 拍全在指令装载面）、trig→done 两侧同值 144 拍
（上界代理）、共享段实测同值、T_total 1713 vs 1908（+11.4%）——
scoped 事件对照口径，不套 v3 unroll 常数、不归因硬件计算能力。

原登记（留存）：编译器 mul 形态 case UR 进/UR 出、无 HBM LOAD/STORE 段；
手写 mul_scalar = HBM 三段。配对公平性修剪项：手写侧 255 个复制 α 词整载
须按指令 3 处理；编译器侧 n_rows 单位为元素（手写 255 词 = 编译器 1020
元素）。

## 6. 限制

- e2e 绿 = sim-vs-golden 自洽（ADR-023 口径），不证 RTL；正确性接受证据 =
  步② VCS COMPARE。
- 本文所有 II/形态结论限本 yaml 配置 × n=256 元素/SMC 单次探针，不构成
  容量或跨规模声明。
- 性能指标定义沿用 docs/perf_metrics_definition_20261014.md；
  T_exec_obs 恒标上界。
