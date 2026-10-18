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

## 5. 配对设计问题登记（步③配对前置；用户点②「设计问题单独提出」）

现状矛盾：编译器 mul 形态 case **UR 进/UR 出、无 HBM LOAD/STORE 段**；
手写 mul_scalar = HBM 三段（LDB→COMPUTE→STB）。用户点③要求两边
LOAD/STORE 范围相同——现形态不满足整链配对。选项：

- **A. 统一 LCS 链承载 flat COMPUTE 程序**：三段壳（LDB/STB 自动生成）
  套编译器 flat 计算程序；可行性待查（LCS 链已证形态均为 unroll
  II∈{2,3,6}），若需逐拍仲裁还须 flat 投影规则的独立论证。
- **B. 仅计算窗配对**：WBARRIER 隔离 COMPUTE 段，两边比 T_exec_obs；
  LOAD/STORE 各自分列报告不并账。合用户点④分离原则，但不满足点③
  整链同范围——只能作为补充，不作主对照。
- **C. case 级组合**：手写三段壳结构不变、COMPUTE 段换编译器 flat 程序；
  需侦察 case 格式/比较器对 COMPUTE 段形态的约束。

**留用户裁定**（不阻塞本步②正确性验收）。

另登记配对公平性修剪项（步③执行时落实）：手写侧 255 个复制 α 词整载须裁到
1 词（compute 只读词 0，不裁则 LOAD 段拍数不公平）；两边 n/α/输入字节/SMC 数
一致（编译器侧 n_rows 单位为元素：手写 255 词 = 编译器 1020 元素）。

## 6. 限制

- e2e 绿 = sim-vs-golden 自洽（ADR-023 口径），不证 RTL；正确性接受证据 =
  步② VCS COMPARE。
- 本文所有 II/形态结论限本 yaml 配置 × n=256 元素/SMC 单次探针，不构成
  容量或跨规模声明。
- 性能指标定义沿用 docs/perf_metrics_definition_20261014.md；
  T_exec_obs 恒标上界。
