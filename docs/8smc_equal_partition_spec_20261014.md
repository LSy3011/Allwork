# 8-SMC 等长分片配置 / manifest 扩展 / 全目标完成条件 定义稿（五步令步②，2026-10-14）

> 任务来源：用户 2026-10-14 五步令步②——「定义等长分片配置、manifest及全目标完成
> 条件 → 明确 n_total/n_per/active_smcs，缺目标结果必须失败」；补充裁定：「首验
> 数据应使不同 SMC 的期望输出可区分，避免 8 路复制同一片数据仍然全绿。manifest
> 不仅要记录活动 SMC，还要校验它与指令使能、输入和输出文件一致」。
> 性质：**定义稿**（不授权实现）。落地顺序：本稿冻结 → 步③ 实现（case-gen/compare/
> manifest 校验）按既有规则流程；LOAD/STORE 使能 0→7 与生成器守卫放开按规则变更
> 流程（记录失败形态→加鉴别测试→改规则→回归），不得静默绕过。
> 依据：预检 v2（docs/precheck_8smc_equal_partition_20261014.md §2.5 解码矩阵）。

## 1. 分片配置定义（scheme: `equal_8smc_v1`）

| 字段 | 定义 | 约束 |
|---|---|---|
| n_total | 算子输入总元素数（fp32） | **n_total % 32 == 0**（8 SMC × 4 lane 整除，无尾块） |
| n_per | 每 SMC 分片元素数 = n_total / 8 | n_per ≤ 该算子**每-SMC** 已校准容量（scale_sum 240 / horner_v3u 68 / addcmulu 156 / add 240【未校准】）；n_per % 4 == 0（由上条推出） |
| active_smcs | 活动 SMC 列表，首验 = [0,1,2,3,4,5,6,7] | 与指令使能/输入/输出文件一致性由 §2.3 闸强制 |
| 迭代数 | 每 SMC = ceil(n_per/4)（由编译器以 n'=n_per 重新编译得到，F10） | 不手改指令 |
| UR 地址 | 每 SMC 对称同构：同 bank、同 line0、同窗形；内容 = 本 SMC 切片 | 地址规划逻辑零改动（预检 §3.2） |
| HBM 地址 | 8 通道**同偏移、不同内容**：channel k 的 x/other/alpha/constants/y 窗 byte_base 与单 SMC 案例相同 | 泄漏面 = 各 SMC 仅写本通道 out 窗（§3 C4） |
| 首证规模 | **n_total = 128，n_per = 16**（scale_sum，步③） | 第二规模步④再定 |

**可区分性硬要求（用户裁定）**：8 个 SMC 的输入切片必须使**期望输出两两可区分**——
判据：任意两 SMC 的 golden 输出窗不得全等（compare 前置自检，§3 C2）。禁止 8 路
复制同一数据（那种形态下跨片错置不可检出，验收无效）。

## 2. Manifest 扩展（schema v2）

### 2.1 版本

- 新 id：`ulcs_program_manifest_v2_8smc`（v1 `adr061_program_manifest_v1` 保留，
  单 SMC 案例不受影响；演进记录入本文与 schema 闸）。

### 2.2 字段变更

| 字段 | v1 | v2 |
|---|---|---|
| `n` | 总元素数（单 SMC 口径） | = n_total（语义不变，总量口径） |
| `partition` | 无 | `{scheme:"equal_8smc_v1", n_total, n_per, smc_count:8}` **必填** |
| `stages[].active_smc` | int 0 | **改为 `active_smcs: [0..7]`**（LOAD/COMPUTE/STORE 各段均列；改名防静默误读） |
| `hbm_region_sha256` | 每 tensor 单值 | 每 tensor **per-SMC 8 值**（`{tensor: {smc_k: sha}}`，通道同偏移不同内容，哈希必须可区分——与 C2 互证） |
| `golden` | 隐式 smc_0/output_bank | **显式枚举** `golden_files: [smc_k/output_bank/bank{b}_final.hex …]` + 每文件 sha256 |
| `sentinel` | smc_1..7 哨兵政策 | 8-SMC 模式**全段无哨兵**（UR 预载仍哨兵＝LOAD 自 HBM 取数，canary 保留）；该节改写为「无 smc_1..7 数据哨兵」 |
| `sync_model` | "tb_assisted" | 维持；limitations 按 §3 C3 分两档（探索/正式验收） |

### 2.3 一致性校验规则（manifest 闸必须逐项强制）

| # | 校验 | 工具依据 | 失败语义 |
|---|---|---|---|
| M1 | manifest.active_smcs ↔ **解码指令使能**：LOAD/STORE 段 body VALID=1 行 en 集合按阈值语义覆盖全部 active_smcs（8 目标=en=7）；COMPUTE 段 en=7 | 步①解码器 decode_case_smc_enablement.py --json | 不一致即拒收 |
| M2 | manifest ↔ **输入文件**：8 通道 memory_init/UR 预载齐 8 份、逐份 sha256 与 hbm_region_sha256 一致 | case-gen 产物 | 缺/不符即拒收 |
| M3 | manifest ↔ **golden 文件**：golden_files 枚举的每份存在且 sha256 一致 | 同上 | 缺/不符即拒收 |
| M4 | 可区分性：8 份 golden 输出窗两两不全等 | 同 C2 | 拒收（配置无效） |
| M5 | n_total % 32 == 0 ∧ n_per == n_total/8 ∧ n_per ≤ 算子每-SMC 容量 | 配置 | 拒收 |

## 3. 全目标完成条件（验收判据）

| # | 条件 | 说明 |
|---|---|---|
| C1 | **数据面 8/8 bit-exact**：每个 active SMC 的 hbm_result 输出窗 == 各自 golden；UR dump 与 golden 三区一致（现有 Z3 三区/C-H 逻辑逐 SMC 循环） | **任一目标结果文件缺席 = RED**（沿用现有「缺 HBM dump 即 err」语义并扩到 output_bank/ur dump；**runner「缺席即 SKIP」不是验收规则**，compared_smc 必须 == 8，零 SKIP） |
| C2 | **可区分性前置**：8 份 golden 两两不全等（M4 的运行时复查） | 不满足则整案无效（防 8 路复制假绿） |
| C3 | **段间保障（两档）**：探索运行 = 现状 TB（WBARRIER 仅 SMC0）+ 报告标注「smc_1..7 落位未被屏障监测」；**正式验收 = 全目标完成监测**——WBARRIER/S4 掩码覆盖全部 active_smcs（路径 A，原树 TB 改动，**须用户单独授权** + TB 版本化冻结），或正面证明现有汇总完成信号以全部目标为条件（路径 B，举证责任在实施方） | 用户裁定②：终值碰巧正确不证明段间保障成立；LOAD→COMPUTE 全 SMC 数据就绪、COMPUTE→STORE 各 SMC 结果可读、末段 dump 等全目标输出 |
| C4 | **泄漏面反转**：单 SMC 模式的「smc_1..7 标记区不变」改为逐 SMC「仅写本通道 out 窗、窗口外 canary 不变」 | 错置分片/越界写在此层暴露 |
| C5 | **零 WARN 升级**：intid delta≠1、轮询超时、哨兵命中等既有 WARN 级信号在 8-SMC 验收中维持既有语义，不新增豁免 | 与 C1 独立 |

### 负控要求（步④预埋，验收前必须演示）

- N1：错置一个 SMC 的输入分片（如 smc_3 与 smc_5 输入对调）→ C1/C4 必须 RED 且报对 SMC 号；
- N2：删除一个 SMC 的结果文件 → C1 必须 RED（缺席即失败，非 SKIP）；
- N3：8 路复制同一输入（违反可区分性）→ M4/C2 必须拒收。

## 4. 边界声明

1. 本稿只定义配置/manifest/判据；**实现（case-gen ×8、compare ×8、manifest 闸、
   使能 0→7、守卫放开）在步③**，按规则变更流程逐项进行。
2. 不扩大到尾块（n_total%32≠0）、归约/跨 SMC 数据交换、多 chunk；分段（n_per 超
   容量）不在首验范围，第二规模若涉及须另立项。
3. 总量容量上限 8×240=1920（scale_sum）为【推断】未校准——本稿不声明任何超出
   每-SMC 已校准值的规模结论。
4. C3 路径 A（TB 改动）未获授权前，一切 8-SMC 运行结果按**探索运行**口径报告，
   不称正式验收通过。
5. 板上等待策略/bitstream 不继承 F6 仿真结论（路线图既有口径）；本稿全部判据
   为 VCS 侧，板端复用时逐条重核。

## 5. 锚点

| 结论 | 锚 |
|---|---|
| 逐段使能实证（LOAD/STORE=SMC0、COMPUTE=广播） | precheck_8smc_equal_partition_20261014.md §2.5 解码矩阵 |
| 使能改动面与守卫放开流程 | 同上 §3.3；ldb_review_case_gen.py:116-117 |
| manifest v1 现状字段 | ulcs_scale_sum_adr061_accept2_n16_n16/program_manifest.json |
| compare 现状（smc_0 主比对 + smc_1..7 标记泄漏面 + 缺席即 RED 先例） | unified_lcs_compare.py:95-100、:156、:265-283 |
| 每-SMC 容量校准值 | thesis_experiment_table_20261013.md 主表 |
| WBARRIER 仅 SMC0 / 屏障位置 | precheck v2 F9；TB :2992、:3421-3440 |

## 6. errata / 执行状态（2026-10-14 A′ 后补记）

1. **intlv 参数化落地（用户裁定 A′）**：`equal_8smc_v1` ⇒ `smc_id_en=7` **且**
   `GR_INTLV_ADDR = 通道跨度 0x10000000`（**绑定分片模式，不成为全局 en=7 规则**：
   其他合法布局步长可不同）。机制 `addr_k = gr_base + k·intlv`，atcbmc300 按地址
   译码选通道（`noc/rtl/include/atcbmc300_config.vh:172-201`）。
   实现：`unified_lcs_case_gen._partition_transport` / `_check_v2_axi_windows`
   （40bit 字段无截断 + 逐 SMC 地址窗包含）+ manifest `partition.channel_stride`
   / `addr_rule` + 校验器 M5/M6 + 单元测试 `TestIntlvBinding`（9 项）。
2. **首跑 RED 归因勘误**：初判「en=7 广播坍缩/分片设计被推翻」**已撤回**；真因
   是指令参数 intlv=0（8 个 master 同访问低地址）。归档
   `verification_v2/artifacts/8smc_first_red_intlv0_20261014/`。预检中「每个 SMC
   天然连接自己的独立 HBM 通道」表述**撤回**（正确机制见上条）。
3. **A′ 运行结果**：`8smc_intlv_n128_r2` `outcome=GREEN published=True`
   （PM-CHK + VCS + 8/8 三区/C-H + 仲裁 v2 逐 SMC/逐通道写账全绿），归档
   `verification_v2/artifacts/8smc_intlv_n128_20261014/`。**签收上限不变**：只作
   「正确路由与 8-SMC 数据结果的探索证据」，**不升级**为正式验收。
4. **§3 C3 路径 B 举证稿已交**：`docs/8smc_last_smc_completion_semantics_20261014.md`
   （CRD 链结构 = 8 目标 AND 归约 + 运行时脉冲粒度=指令批次），**待用户裁定**；
   路径 A（TB 加全目标监测）仍未授权。
5. **§3 C1 部分实现补齐**：程序仲裁器 v2 分支（逐 SMC LOAD 写账、逐通道 STORE
   写账、未知 schema/`smc_count` 篡改响亮 RED），读探针仅覆盖 SMC0 一点以
   limitation 显式登记（不冒充逐 SMC 消费证据）。
