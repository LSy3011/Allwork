# 公平对照组合程序设计：scalar_mul 64 词/SMC × 8 SMC（2026-10-18）

依据：用户 2026-10-18 裁定（`scalar_mul_config_acceptance_plan_20261018.md` §5）——
**选 C 为主线：共用 LOAD/STORE 三段框架，分别放入手写和编译器的 COMPUTE 段**；
回答「同样的标量乘任务，自动生成与手写实现各有什么代价」。

**验收口径（用户原文，置顶防漂移）：这是公平对照实验的组合程序，不是新增的
公式驱动统一 LCS 能力。** 本组合不进入统一 LCS 入口、不声明任何 flat 逐拍契约、
不套用 v3 unroll 常数。

## 1. 规模与 lane 前提（指令 1 + 用户补充）

- 每 SMC 256 元素 = **64 UR word**（16B/词、4 lane fp32）；双方均 **8 SMC**；
  255 词不作首个对照点（仅留作后续容量/长度兼容性实验）。
- **lane 前提写入双方输入规格**：手写 `scalar[i%4]` 允许四 lane 不同系数；
  本轮固定四 lane 全为 **2.5**（`0x40200000`），即通常意义的单一标量乘。
  生成器 side 由 `scalar` 缓冲全 2.5 填充保证；编译器 side 由 yaml
  `test_data.alpha: [2.5]` 保证；独立参考按单标量计算。

## 2. 冻结公共框架（指令 2）

框架来源 = 手写生成器（`run_rtl_test_v3.sh` 步 1–3 等价手工序列，
`gen_mul_scalar_lsu_case` argv=`(<case> <scratch_sram> 20261018 fp32 64)`，
**不跑其步 4–5 自带 sim**，sim 一律走受控 staged 入口）。
以下项两侧**逐字节一致**（同一批文件，非各自重生成）：

| 冻结项 | 取值/来源 |
|---|---|
| 输入字节 | 同一次生成（seed=20261018，x∈[-2,2] fp32 均匀，α 全 2.5）；hbm_data 8 通道文件两侧共用 |
| UR 布局 | x→bank0 offset0（64w）；α→bank1 offset0（64w 复制）；y→bank2 offset0（64w） |
| HBM 布局 | 每 SMC 通道（gr_intlv=0x10000000）：x@byte0（1024B）、α@byte1024（1024B）、y@byte2048（1024B）——以生成后 meta_v3.json 实测为准登记 |
| LOAD（stage_0） | LDB×2 + LDBAXI×4（bulk 64w），同一 hex 文件 |
| STORE（stage_2） | STB×1 + STBAXI×2，同一 hex 文件 |
| golden | 生成器逐 SMC `output_bank/bank2_final.hex`（256 行=64 有效+零尾），两侧共用 |
| RTL/TB | v4.22 源树 + 主线 TB sha256 3324a33b…f7c5（跑前核，F6 冻结清单成员，永不改） |
| 同步/计时 | 同一 multi_stage TB 的 stage 驱动 + WBARRIER；同一 `perf_marker_extract.py` 口径 |
| 运行入口 | `run_multi_stage_staged.sh`（flock+备份+staging+恢复；本 case 含 hbm_data 适用） |

**唯一允许差异面** = stage_1（COMPUTE）指令文件集 + 其 data_list 条目
（「必需的队列使能信息」）。

## 3. 两 COMPUTE 变体（差异清单，指令 2 末句）

| 项 | 变体 A：手写 | 变体 B：编译器 |
|---|---|---|
| 来源 | 生成器原生 stage_1 | `scalar_mul.yaml`（α bank **3→1** 配置-only 修正后）e2e 产物 |
| 队列集 | 13 队列：68_vin0/69_vin1/70_vout0/fpmul/rtovr-14,-15,-16,-35,-3,-4/ur-r0,-r1,-w0 | 13 队列：编译器 flat 发射集（以产物清单为准登记） |
| 发射形态 | v3 bulk 微码（INIT+流式 BODY） | level-0 平坦压缩流 INIT(LOOPS=5)+BODY(VLD=1,LOOPS=63=64 拍,SMC_ID_EN=7)+TERM(JUMP=1) |
| 步②状态 | r2 形态 255 词已 VCS 5/5（本 64 词框架本次首验） | 64 词已 VCS GREEN（2026-10-18，α bank3 版）；**bank1 版须重跑步②** |

差异登记以构建后实测为准（行数/队列名/INST_LINES 逐项列表入归档 README）。

## 4. α 装载裁定（指令 3）——**双侧共同整载，不裁**

指令 3 前置问「单词 LOAD 描述符是否为已验证合法形态」。正面取证结果：

- 手写 r2（255 词，VCS 5/5 + 板端史）：α 按 255 词**复制整载**；
- 统一 LCS scale_sum n16（VCS GREEN + 板端 PASS）：`program_manifest.json`
  `hbm_layout` 示 **alpha lines=4 = n_lines 整载复制**（与 x 同形，
  byte_base=12288, bytes=64）——param 装载惯例即整载；
- 现有证据库中**不存在单词（1 词）LOAD 描述符的已验证先例**。

按指令 3 既定分支（「若仍需 guard 搬运，就两边采用同样的 guard」之同构处理）：
**两侧共同保留 64 词 α 复制整载**。公平性由构造保证（LOAD 文件两侧同一），
且装载成本差异不进入 COMPUTE 段对照（LOAD 成本双侧相同、单独报告）。
单词描述符形态留作未来独立验证项，不在本轮引入未验证形态。

## 5. 正确性验收（指令 4，先于一切取数）

两条完整链各自独立验收，全部满足才算 GREEN：

1. **真实 LOAD→COMPUTE→STORE**：HBM 预载 → stage_0 → stage_1 → stage_2 →
   HBM 回写，无任何 $readmemh 数据面捷径；
2. **逐 SMC 比较 8/8、零 SMC 级 SKIP**：compare 工具语义已钉死
   （`compare_ur_to_golden_multi_stage.py`：枚举 case 下所有 `smc_<n>`，
   无 output_bank 才 SKIP；两变体 smc_0..7 均含 bank2 golden ⇒ 8/8 全比对）。
   **前科登记**：步② compiler_scalar_mul 的「COMPARE PASSED」实为
   `total_smc_runs=1`（smc_0 单侧），SMC_ID_EN=7 不得推定 8 路均已比较——
   本组合以目录枚举为准；
3. **逐 bank 语义**：bank2 全 256 行比对（64 有效 + **零尾**兼作 UR 非目标写检查）；
   bank0/1 等无 golden 按工具惯例 SKIP 并逐条列入报告；
4. **独立参考**：从 hbm_data 字节独立计算 fp32(2.5·x) 与 dump 比对
   （不依赖任一侧 golden 文件）；
5. **HBM 窗检查**：verify_ddr_v3 式逐 SMC 输出窗（@store_byte_base，64w）+
   staging 备份差分证窗外零写（非目标写检查保留）；
6. 判红即停：任一变体任一项失败 → 不取数，先回到框架或该变体定位。

## 6. 取数计划（指令 5/6，正确性全绿后执行）

同一口径双侧报告（定义沿用 `perf_metrics_definition_20261014.md`）：

1. **指令行数**：stage_1 各 hex 行数与队列数（LOAD/STORE 双侧相同，报一次）；
2. **装载成本**：stage_0 窗口（触发→LOAD 完成锚），双侧相同则核验相同；
3. **触发至目标末写观测窗口**：stage_1 触发 → 目标 bank2 末写（T_exec_obs 类，
   探针可观测为准）；
4. **同步开销**：WBARRIER 等待（waited 拍数）逐项；
5. **完整程序时长**：三段 + 屏障全程。

**口径纪律**：屏障释放作结束锚 = **上界**，不称纯计算周期；flat 执行投影未验证，
本轮仅做**明确限定的事件与数值对照**（指令行数/窗口拍数/等待拍数），
不套用 v3 unroll 常数，不宣称 flat 逐拍契约已补齐；
TB 拍数 ≠ 板端时序（段拍 ~95-97% 系 TB AXI 装载行为，perf §5 已实证），
报告三段分离：调度发射规模 / TB 执行行为 / 工具链墙钟。

## 7. 风险与开放项

- **编译器 COMPUTE 作中段（stage_1）集成**：compiler_scalar_mul 原 case 以
  stage_0 单段运行，本组合为中段——TERM(JUMP=1) 与 TB stage 驱动/屏障的
  交互以验收运行为准，不先验断言；
- **零尾对齐**：golden bank2 为 256 行（64 有效+零尾），要求两侧 COMPUTE
  均不越 64 词写 bank2（编译器 ur-w 描述符 words=64 已核，运行验证）；
- **生成器污染面**：gen argv[2] 会写全局 sram——本轮一律传 **scratch 目录**，
  再拷入 case/hbm_data/；全局 sram 仅经 staging wrapper 受控触达；
- **手写生成器不改码**：LSU_WC=64 经 argv[5] 传入即得 64 词框架
  （`default_compute_desc(words)`，input/scalar 同 64 词），α 整载保留，
  零生成器补丁。

## 8. 限制

- 本组合的一切结论限：该框架 × 64 词/SMC × 8 SMC × fp32 × α=2.5 ×
  seed=20261018 × v4.22 RTL/TB；不外推容量、不外推其他公式/规模；
- 两变体 GREEN ≠ 编译器 flat 形态获统一 LCS 准入；步③容量校准仍开放；
- TB 层窗口拍数不归因 II/硬件纯计算能力（TB 装载主导，perf §5）；
- 板端执行不在本轮范围（板端两包继续等窗口）。
