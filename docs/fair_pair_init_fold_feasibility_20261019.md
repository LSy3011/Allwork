# INIT→DLY 合并可行性检查点：flat 平坦流初始化行与首条操作合并（2026-10-19）

依据：用户 2026-10-19 令——**值得优化，但只适合做一次有界的编译器优化实验，
不应现在直接删除 11 条 INIT**；额外成本已证来自统一发射模板的 INIT 行（四步序②
静态拆解，`docs/fair_pair_smul64w_instr_decomposition_20261018.md`），非调度失败
或计算单元利用率差。本文档 = **1–2 工作日可行性检查点**的裁定记录：①逐队列语义
比较 → ②结构规则 → ③一个候选变换 → ④小规模+64w/255w 验收 → 决策门。

**纪律（用户令原文转引）**：实验完成前**不预先承诺消除全部 195 拍**；TB 装载收益
**不直接当作板端加速比**；需要改硬件、扩大到通用 flat 协议重构、或出现难以解释的
相位耦合 → 本轮停止，保留现有正确版本。

## ① 逐队列语义比较：INIT+BODY vs BODY′（实测解码，非源码推断）

数据：公平对照两变体 stage_1 原始 hex 逐行位解码（ISA v5 头布局，packer.py:4-8），
64w 与 255w 两规模全 13 队列。**相位字段（INIT LOOPS、dly）两规模逐队列相同**；
LOOPS 计数字段随规模缩放（64w L63/L78 ↔ 255w L254/L269），与既有「INIT 载荷
n_rows-不变」结论一致。

| 队列 | A 手写 BODY′ 行（vld/jump/loops/**dly**） | B 编译器 INIT(loops=l_init) | 合并所需 dly = l_init+1 | ≤7 可合并？ |
|---|---|---|---|---|
| vin0 | 1/0/L78/**d1** | L3 | 4 | ✅ |
| vin1 | 1/0/L78/**d1** | L3 | 4 | ✅ |
| vout0 | 1/0/L78/**d7** | L10 | 11 | ❌ 超 3bit 域 |
| fpmul | 1/0/L78/**d3** | L5 | 6 | ✅ |
| rtovr-14 | 1/0/L63/**d1** | L2 | 3 | ✅ |
| rtovr-15 | 1/0/L63/**d1** | L2 | 3 | ✅ |
| rtovr-16 | 1/0/L78/**d6** | L9 | 10 | ❌ 超域 |
| rtovr-35 | [1/0/L0/**d7**/code0] + [1/0/L78/d0]（A 亦 3 行，行差=0） | L11 | 12 | ❌ 超域（且零行差） |
| rtovr-3 | 1/0/L78/**d2** | L4 | 5 | ✅ |
| rtovr-4 | 1/0/L78/**d2** | L4 | 5 | ✅ |
| ur-r0 | 1/0/L63/**d0**（body 与 B 逐字节同） | L1 | 2 | ✅⚠首验 |
| ur-r1 | 1/0/L63/**d0**（body 与 B 逐字节同） | L1 | 2 | ✅⚠首验 |
| ur-w0 | [1/0/L3/**d7**/code0] + [1/0/L63/**d4**] + TERM(**d2**)（A 亦 3 行，行差=0） | L12 | 13 | ❌ 超域（且零行差） |

语义模型（双源核对）：

- **编译器 INIT 行** = vld=0 NOP 垫拍，LOOPS=l_init（assembler.py:218-220 由 SMT
  global_t 推出，:373-375 发射 loops=l_init−1，:443 平坦分支 +1 垫拍对齐 embed 形
  HDR 占位——mul e2e other_read 审计 VCS 实证差一拍校准）。占 **l_init+1 个发射拍**，
  不携带任何数据面效果（vld=0，后端见 VLD=0 不动作）。
- **手写 BODY′** = 相位直接编入头部 **3bit DLY 字段**（d1/d2/d3/d6/d7 实测值），
  无独立 INIT 行；`compute_start_delay_insts=0`（mul_scalar_op.cpp:1184 默认）
  ⇒ `append_nop_wait`（:803-819）零行发射，与实测 2 行形态一致。
- **关键实证**：手写侧 dly≠0 在 vin/vout/fpmul/rtovr/ur-w0 上均 VCS GREEN（本对照
  A 侧即证据）——**dly 机制本身在所有这些队列类型上已被 RTL 接受**。

## 合并等价性证明（三源闭合）

**命题**：`[INIT(vld=0, loops=L)] + [BODY(dly=0)]` ≡ `[BODY(dly=L+1)]`（首 fire
相位逐拍相同，其余字段不动）。

1. **RTL（冻结树 u280_sim_v422_f6full_20261010）**：
   - `smi_iq_iic.v:695-699`：level-0 行 dly **仅附首 fire**
     （`(r_wr_embed0_inst_cnt=='d0) && (r_embed_lev0_cnt=='d0)` 时附 w_dly，
     后续 fire 置 0）——dly 是一次性首 fire 延迟，非逐迭代间隔。
   - `smi_iq_dly_queen.v:479-511`：dly=k ⇒ INST_DECODE→WAIT_DLY(cnt=k−1)→第 k+1
     拍发射 ⇒ **相对 dly=0 基线晚 k 拍**。
   - INIT NOP 行：loops=L ⇒ L+1 次无效发射，各占 1 发射拍 ⇒ BODY 首 fire 晚 L+1 拍。
   - 两式相等 ⇔ **dly = L+1**。
2. **Python sim（model A）**：`iq_unit.py:144-149,248-256` dly 一次性
   （_dly_finished_marker 机制，行 L0 溢出才复位），首 fire 在取指后第 k 拍；
   NOP 行占 loops+1 拍。同样 **dly = L+1** 闭合。两模型一致。
3. **VCS 先例**：A 侧 fpmul d3 / vin d1 / vout d7 / rtovr d1,d2,d6,d7 / ur-w0
   d7,d4,d2 全 GREEN ⇒ 这些队列类型上 dly≠0 已被 RTL 实际接受；EN_DLY=1 为
   smi_iq.v:60 全实例缺省（smi_top.v 无覆盖）。

**逐项行为核对（用户令③检查单）**：
- 首个有效操作：BODY fire0 时刻不变（l_init+1，两形相同）✓
- 重复次数：BODY loops 字段不动 ✓
- 地址序列：INC 地址合成在 iic 按迭代计数推进，dly 只在 dly_queen 内延迟发射、
  不改变已合成地址流 ✓
- 跨队列相位：每队列首 fire 时刻各自不变 ⇒ 相对相位零扰动（**无跨队列耦合引入**）✓
- 末次写回：末次 fire 相位 = 首 fire + loops，首 fire 不变 ⇒ 不变 ✓
- 终止行为：TERM 行（jump=1）不动；行数减一只前移 TERM 的行内索引，jump 相对
  寻址不受影响 ✓

**首验风险登记**：
- 编译器发射 dly≠0 = **史上首次**（全 releases/build physical_instructions.json
  扫描：非零 dly 行数 = 0）；既有先例全来自手写生成器。
- **ur-r0/ur-r1（UR READ）dly≠0 无任何先例**（手写 ur-r 恒 d0；ur-w0 有先例但属
  IQ20-23 写类，ur-r 属 IQ16-19 读类）。两队列同 smi_iq 模块同 EN_DLY=1 缺省，
  风险低但非零。**红则首刀二分：排除 ur-r0/r1 重跑**。
- 相位耦合警戒（用户令）：INIT 值集 = 调度产物（曾观测 yaml length=1020 时值集
  变化，属调度伪影非 n_rows 耦合）；本规则按发射时刻逐队列判定，对值集漂移天然
  稳健，不引入新的耦合通道。

## ② 结构规则（候选变换的准入条件）

仅作用于 **ADR-054 flat 平坦分支**（assembler.py:430：`len(kernel_map)==1
and not is_smi_ds_t_dp and iq_II==1`）：

```
fold  ⟺  AIACC_FLAT_INIT_FOLD=1（实验开关，默认关）
        ∧ l_init + 1 + op_dly ≤ 7      （BITS_DLY=3 域；op_dly 现行调度恒 0）
fold 时：摘除 INIT 行，BODY dly ← l_init + 1 + op_dly，abs_t 簿记不变
否则  ：原 [INIT(loops=l_init), BODY(dly=op_dly), TERM] 三行发射逐字不动
```

- 无算子名/队列名特判（条件只含调度量 l_init、op_dly 与环境开关）✓
- II>1 展开（ADR-056）与分段路径不经此分支，零影响 ✓
- 条件不满足自动回落原发射方式 ✓
- **零 hw_config 变更**：packer 对全部队列无条件落位 DLY 字段（isa_v5 头全局锁死，
  packer.py:4-8）；RTL EN_DLY=1 全实例缺省；iq_config.yaml 的 EN_DLY 声明经全仓
  扫描**零消费者**（纯文档性字段，sim 仅消费 EN_INC）——无需立法序。
- branch_report 值保持 "flat" 不变（fold 在 physical_instructions.json 中可直接
  观测：INIT 缺席 + BODY dly≠0），下游守卫零扰动。

## ③ 候选变换（实现态）

assembler.py flat 分支内：INIT patch（:443）替换为「fold 判定 → pop INIT 行 +
BODY dly=l_init+1+op_dly / 否则原 patch」。默认关（AIACC_FLAT_INIT_FOLD 环境变量），
**基线（未优化）发射保持默认**——650 pytest 默认零漂移；fold 路径以环境变量开启
验收。性能比较继续使用同一 TB（sha 3324a33b）与同一计时口径
（perf_marker_extract.py，span=load+cfg78+trig→done 闭合）。

**静态投影（非承诺，待④实测）**：两规模 fold 9 队列 → B 侧 COMPUTE
39→30 逻辑行、54→41 beats（−13 beats = **−169 拍**投影）；对 A 残余差 2 行
（vout0、rtovr-16）/ 2 beats / +26 拍。trig→done 投影不变（相位保留）。

## ④ 验收计划与结果（运行后回填）

- [x] pytest 默认（fold 关）：656 passed（含新增 6 项 dly 单测；原 650 项零漂移，
  基线保护）— 2026-10-19 实测
- [x] pytest AIACC_FLAT_INIT_FOLD=1：656 passed（含新增 6 项 dly 单测
  tests/unit/test_iq_unit_dly.py），fold 路径回归零红；旧契约测试
  test_shiftup_physical_instr_emitted 已改环境感知（双模式各 32/32）
- [x] e2e sim：scalar_mul fold 开三规模全 GREEN（ALL PHYSICAL CHECKS PASSED +
  Truth Audit SUCCESS）：--n-rows 64 / 256 / 1020（2026-10-19）
- [x] **潜伏 sim dly bug 修复**（本检查点暴露）：iq_unit.py evaluate() 在计数减到 0
  的下一拍先于 tick 收尾重新初始化 → dly≥1 行永不 fire；该路径史上零演练（编译器
  从未发 dly≠0）。修后 INIT 形 ≡ 折叠形 fire 序列逐拍相同（6 项单测锁定）
- [x] 打包三案例（package_vcs_case.py）：compiler_scalar_mul_{16w,64w,255w}_fold；
  13 队列总行数 39→**30**（9 队列 2 行 + 4 队列 3 行），逐队列 dly 值与①矩阵一致
  （vin0/1 d4、fpmul d6、rtovr-14/15 d3、rtovr-3/4 d5、ur-r0/r1 d2；
  vout0/rtovr-16/rtovr-35/ur-w0 保留 INIT 三行）
- [x] VCS 小规模（16w）：首尾边界 + fold 生效核验 — A/B-fold 两腿 GREEN（见④结果）
- [x] VCS 64w：8/8 零 SKIP、verify_ddr、独立参考、非目标写、实测行/beats/拍节省 ✓
- [x] VCS 255w：同上 ✓
- [x] mul 回归（flat 主客）：mul e2e fold 开 GREEN；既有展开路径经 fold 开 pytest
  全量覆盖（add 展开用例含其中）
- [x] 未优化基线保留：默认关 + 64w/255w 归档已封存（SHA256SUMS）

## 决策门（用户令原文）

- 如果只是少数字段可证明的合并，继续实现并验收。
- 如果需要修改硬件、扩大到通用 flat 协议重构，或出现难以解释的相位耦合，
  本轮停止，保留现有正确版本。

**①②③ 阶段结论**：合并 = 「摘除 INIT 行 + BODY dly 字段写 l_init+1+op_dly」，
属**少数字段可证明的合并**（等价性三源闭合：RTL 两模块 + sim + VCS 先例）；
零硬件变更、零 hw_config 变更、零通用协议重构。→ 进入④验收。

（④结果与最终裁定：验收运行后回填。）

## ④ 验收结果（2026-10-19 回填，四腿 VCS 全 GREEN）

**运行记录**：staged runner 四腿全部 `COMPARE PASSED` + `[STAGED-RUN] 总判：GREEN`
（HBM 备份/装载/恢复逐文件校验 8/8）；判据与 2026-10-18 公平对照完全同口径
（同一 TB、同一 staging 入口、同一 perf_marker_extract span）。

| 变体 | COMPARE | refcheck post | verify_ddr | stage_1 装载拍 | stage_1 beats | T_total |
|---|---|---|---|---|---|---|
| 16w A 基线（手写） | 8/8，0 SKIP | PASS 8/8 | （A 腿未跑，同口径以 refcheck 为准） | 507 | 39 | 1215 |
| 16w B-fold | 8/8，0 SKIP | PASS 8/8（窗==IEEE ∧ 窗外零改写） | 8/8 bit-exact（16 words） | 533 | 41 | 1241 |
| 64w B-fold | 8/8，0 SKIP | PASS 8/8 | 8/8 bit-exact（64 words） | 533 | 41 | 1739 |
| 255w B-fold | 8/8，0 SKIP | PASS 8/8 | 8/8 bit-exact（255 words） | 533 | 41 | 2687 |

对照 2026-10-18 归档基线（B 未折叠：COMPUTE 39 hex 行 / stage_1 54 beats /
装载 702 拍；T_total 64w=1908、255w=2856）：

- **行数**：COMPUTE 39→30（−9，与①矩阵 9 队列折叠逐行一致；4 保留队列
  vout0/rtovr-16/rtovr-35/ur-w0 三行不变）
- **beats**：54→41（−13，与静态投影逐 beat 一致）
- **装载拍**：702→533（**−169，实测，与投影 −169 逐拍一致**；跨规模绝对恒定）
- **T_total**：64w 1908→1739（−169，−8.9%）；255w 2856→2687（−169，−5.9%）
- **相位零扰动**：三 stage trig→done 全变体逐项相同（16w 144/106/68；
  64w 334/144/182；255w 942/334/410），stage_0/stage_2 装载拍亦全同
  （104/52；208/104；156/78）——折叠仅作用于 stage_1 COMPUTE 装载面
- **逐队列 beats 归因**（16w A vs B-fold）：13 队列中 11 队列逐项相同；唯一
  差异 = rtovr-16 (2→3) 与 vout0 (2→3)，即两处超 3bit DLY 域的结构性保留余量
  ⇒ 对 A 残余差恰为 **+2 beats / +26 拍**（64w 1739 vs A 1713 = +1.5%；
  255w 2687 vs A 2662 ≈ +0.9%，±1 拍属触发夹逼粒度）
- **非目标写**：refcheck 8/8"窗外零改写"三规模全过
- **未优化基线保留**：AIACC_FLAT_INIT_FOLD 默认关，默认模式 pytest 656 passed
  零漂移；64w/255w 归档（SHA256SUMS）未动

**纪律重申（用户令）**：−169 拍是 **TB 指令装载拍实测差**（四腿同口径），
**不写作板端加速比**；残余 +2 beats 是 DLY 3bit 域的结构性保留，非调度失败；
本轮不承诺消除全部 195 拍——实为 195 中消除 169、保留 26。

**最终裁定（按用户决策门第 1 支）**：合并 = 摘除 INIT 行 + BODY dly 字段
写 l_init+1+op_dly，属**少数字段可证明的合并**；零硬件变更、零 hw_config
变更、零通用协议重构、跨队列相位逐项可核且无一解释不了 ⇒ **继续实现并验收**
（已完成）。fold 以环境开关形式随编译器落地（默认关），是否转为默认发射与
永久化另立 ADR 裁定。


## 2026-10-19 Codex 接力检查点（未完成 RTL 验收）

- 16w 手写框架已生成；临时 α=2.5 生成器头文件恢复为1.25，SHA256
  `35376661a1903f5150932c38028b27b701efa5f35cd469f3ea4833e9ce3d9caf`。
- 16/64/255w compfold 配对案例已建立：各13队列/30行；每规模85个公共
  数据与LOAD/STORE文件逐字节相等。16w预运行独立IEEE参考8/8通过。
- 生成器打印的默认DDR参数与meta不同，实际meta布局仍为x@0、alpha@4096、
  输出@8192；独立参考按实际文件检查。首次生成未预建scratch目录，生成器
  未报写文件失败；已创建scratch后以同一已编译程序重生成并核验输入，
  未接触全局HBM初始化目录。
- 新DLY单测的sys.path曾污染编译器core包，定向组合5项导入失败；删除
  `sim/smc_simulator`路径，只保留其父目录后fold-ON定向38/38通过。
- 16w手写基线VCS在沙箱内因许可证连接失败退出，staging恢复8/8核验通过。
  沙箱外审批服务另报模型容量不足，命令未执行；不是数值RED。
- 四次VCS、性能差值验证与最终提交仍待完成。-169拍是预测，不能写成实测。
  fold开关继续默认关闭；当前Python测试不替代RTL首证。
- 断点归档：`verification_v2/artifacts/init_fold_handoff_20261019/`；
  `resume_vcs.sh`为用户终端续跑入口，每腿失败即停，新日志目录保留旧件。

> **勘误关联（2026-10-19 晚，四腿完成后补记）**：上文"四次VCS…仍待完成 /
> -169拍是预测"已被本档「④ 验收结果」supersede——四腿 VCS 全 GREEN，
> −169 拍为实测且与预测一致；DLY 单测 sys.path 修正与 16w scratch 预建
> 两处断点均已在最终树中生效（`tests/unit/test_iq_unit_dly.py` 仅注入
> `sim/` 父目录；16w 框架 hbm_data=scratch 8 文件拷贝）。
