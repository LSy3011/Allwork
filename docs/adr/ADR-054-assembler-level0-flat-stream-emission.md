---
adr_id: ADR-054
title: assembler level-0 平坦流发射（II=1 单槽队列）—— golden 协议对齐，绕开 embed-loop RTL 死路
decided: 2026-09-12
last_verified: 2026-09-12
still_holds: proposed
builds_on: ADR-053（VIN/VOUT 绕行）、ADR-050（smi_iq_iic.v:427 array-replay 同族病）、ADR-051
hco_review: approved-with-notes（2026-09-12 独立逐行复核：6 通过项——帧宽物理事实/显式 RESERVED 唯一正确建模/双数据源+守卫⑬可接受（SSoT 记 DEBT-054-DUALSRC 非阻断）/II=1 边界物理合理/TERM 契约安全/INIT 垫拍相位代数成立；唯一阻断项「golden 全流无 embed≠0 行」假引理已修——硬件死区实为 embed∈{1..6}，embed==7 是 golden append_nop_wait 原生安全哨兵，我方协议有意加严 embed==0；建议①平坦流 INC 域守卫已落实 code+m15 测试（DEBT-054-FLAT-INC-RANGE 防护到位），建议②g14 op 行 code≠0 哨兵已落实，建议③三案非零行指纹记入 §1 取证链（非零行指纹）；待商榷②两处对 golden 偏差记入 §风险 gated）
user_decision: 治理指令「你应该先进行仿真，再进行上板——我说的仿真是 VCS 的仿真」（编译器产物必须先在 v4.22 RTL 上 VCS 实测）
---

# ADR-054 · assembler level-0 平坦流发射（II=1 单槽队列）

> **状态：Implemented（同日 VCS 验收通过）· hco approved-with-notes（2026-09-12，must-fix 已闭环）。**
> **一句话**：编译器历史上对所有队列发「INIT + HDR(level=1, embed) + kernel」loop 形态，
> 但 v4.22 RTL IQ 控制器对 **embed∈{1..6} 的多元素 kernel 流**走 array-replay 分支并卡死
> （`smi_iq_iic.v:441 EN_EMBED && ~(&w_embed)` → `:468 LOOP_PARA_SET` → `:457/:557
> EXE_ARRAY_INST`，ADR-050 LDB 同族病）；golden 的 op/终止行全为 **level-0 平坦流**
> `[op: vld=1, level=0, loops=N, inc][TERM: vld=1, jump=1, code=0]`（其 wait 行用
> **embed==7 安全哨兵**，不进死区——`append_nop_wait`，hco 2026-09-12 订正）。
> 本 ADR 让 assembler 对 **II=1 且单槽 kernel** 的队列（mul 全部队列）改发 golden 平坦形，
> VCS 实测 `multi_inst_stage/compiler_mul/smc_0` **COMPARE PASSED**（8 bank × 256 行 =
> **2048/2048 bit-exact**，独立 python 复核 mismatch=0）——编译器产物首次在真实 RTL 上
> 逐位走通计算算子（阶段 2 里程碑 M1 达成）。

## Context

### 1. 问题：embed-loop 流形在 RTL 上是死路（VCS 实证链，非推断）

编译器 assembler 主线（`src/driver/assembler.py`）对每个队列发：
`INIT(vld=0, loops=l_init-1)` → `HDR(vld=1, level=1, embed=1, loops, inc_embed)` →
kernel 各行（mod-II 折叠）。该形态 Python 模拟器全程绿灯——**但 v4.22 RTL 不接受**。

VCS 取证链（2026-09-12，每步都是 A/B 差分）：

1. **修完帧宽（ADR-053 errata）仍断流**：`compiler_mul` bank2 仅 **行 0-2 复活且 bit-exact、
   行 3-63 全零、64-255 与 golden 同为零**——流只吐 3 行就死了（64 行数据预期）。
2. **H1 杂交（golden 流 + 我方数据）**：把 golden 全部指令流原样灌入、仅数据换编译器的
   → bank2 **253/255 行非零**。变量隔离结论：数据没问题，**死的是我们的指令流形态**。
3. **H3fix 杂交（golden 10 队列 + 我方修帧后的 VIN 三流）**：**251/255 行非零、首个非零在 L4**
   → 我方 VIN 流（修帧后）本身正确，再次把嫌疑钉在其余队列的 HDR/embed 形态上。
4. **平坦化 A/B**：仅把全部队列改发 level-0 平坦流（数据、payload 一律不动）→
   **COMPARE PASSED**，2048/2048 行逐位等。反证充分。

> 非零行指纹（hco 2026-09-12 亲算归档，备后验复算）：`h2_vinfix` bank2 恰 {0,1,2}；
> `h3fix` bank2 251 行 {4..254}；`hybrid` bank2 253 行；终局 `compiler_mul` 8 bank ×
> 256 行全 diff=0，`arith_regression/test_log/multi_stage/batch_compare.log` 存档
> 「8× PASS（256 lines, exact）、pass=1 fail=0」。

硬件机制（hco 2026-09-12 全程 FSM 走查复核）：`smi_iq_iic.v:441` `EN_EMBED && ~(&w_embed)`
⇒ **仅 embed∈{1..6} 进 `:468 LOOP_PARA_SET`** → 多元素 kernel（带 embed_end 行）走
`:457/:557 EXE_ARRAY_INST` array-replay 卡死——与 ADR-050 阶段 2 在 LDB 上实证的同族病
（`assembler.py:207-215` 历史锚点 `:427` 是 FSM case 起始的习惯写法，保留原文不改；
本文起一律用 `:441/:468/:457/:557` 精确节点）。**embed==7 是安全哨兵**：走尾部
jump/LOOP_CYCLE，不进 loop-set（golden `append_nop_wait` 的 wait 行即 embed=7，
`mul_scalar_op.cpp:803-819`；hco 对 `fpga_pkg/sub_8smc_v3_fpga_verify/case/smi_iq_inst/
stage_1/iq_68_vin0.hex` row0 逐字节解码复核 embed=7）。**embed==0 单行 loops 重放**走
`EXE_EMBED0_INST` + `r_embed_lev0_cnt`（`:500/:766-767`），不进 EXE_ARRAY_INST——
这正是平坦形存活的物理根。结构结论：**编译器产物流的 embed 域只允许 {0}**（wait 哨兵
不进编译器产物，垫拍一律 vld=0 NOP 行；守卫⑭判 embed≠0 违例，属对死区 {1..6} 的
**有意加严**）。

### 2. golden 权威协议（全部手写算子流普查，无例外）

golden 每队列只有两个必须的行型（外加可选 0 或多条 NOP 垫拍）：

| 队列 | 行型（VCS 打包解码，body 值随调度） |
|---|---|
| fpmul | `[dly=3 loops=269 code=0x3f]` |
| vin0/vin1 | `[dly=1 loops=269 code=0x0f]` |
| vout0 | `[dly=7 loops=269 code=0x0f]` |
| ur-r0 | `[inc=1 loops=254 code=0x07001]` |
| ur-w0 | `[dly=7 loops=1 code=0]` + `[inc=1 dly=4 loops=254]` + `[dly=2 jump]` |

共性不变量：**op/终止行 level==0；末行 vld=1, jump=1, code=0**（`mul_scalar_op.cpp:821
append_end`）。（**hco 2026-09-12 订正**：golden 并非「全流 embed==0」——`:803-819
append_nop_wait` 原生发 **embed=7 wait 行** `make_instruction(0,0,7,loops,...)`，embed=7
是 FSM 安全哨兵，与死区 embed∈{1..6} 不同（上表为 op 行抽样式列举，未穷举 wait 行）。
另 hco 提示：「golden 协议形」
是**语义形而非字节形**——形状一致但 NOP/wait 标记字节存在合法差异。）行级语义：op 行的
`LOOPS=N-1` 让**同一行**原地重放 N 次（`EXE_EMBED0_INST` level-0 datapath 循环，
非 array-replay），`INC` 提供逐拍地址步进。

### 3. 模拟器的表达盲区（为什么 sim 绿证明不了 RTL 流形态）

Python 模拟器 `IQUnit` 对 level-1 embed 与 level-0 flat **同构建模**（都是按 loops 数 fire
N 次），两种形态 sim 下产出同一组 fire 事件——**sim 维度上二者无差别**。这意味着
「embed 主线 sim 绿」**不能传递任何 RTL 存活信息**，流形态只能由 VCS 仲裁。
教训沉淀：**跨表达形式的等价（同 fire 序列）≠ 物理等价（同 RTL 分支）**；凡涉及
IQ 流形态（header level/loop 拓扑）的决策，验收必须落在 VCS。

### 4. 编译器侧难点：II>1 与多槽 kernel 不能一并平坦化

- **II>1 单槽**（add，iq_II=2）：其 kernel 是 `[BODY][NOP(embed_end=1)]` **两行**，
  「每 2 拍一次」的节拍靠**两行轮流**产生；level-1 的逐拍地址步进也从 HDR 的
  `INC_EMBED` 数值读出（`sim/smc_simulator/control/iq_unit.py:118-124`）。平成单行后
  是连续 1 拍节拍——**每拍都 fire → 地址/数据按 2 倍速错位**（add e2e 实证 Truth Audit FAILED、
  读值 2× 前进）。且该 II=2 embed 形态从未上过 VCS，连"sim 绿"都是孤证。
- **多槽 kernel**（matmul_parallel_n、fma 等多 op 同 IQ）：一行平坦流表达不了
  kernel 内的 op 序列。

故平坦化只收编「II=1 且单槽 kernel」的队列——恰好覆盖 mul（阶段 2 唯一目标算子）的全部队列，
以及 fma_parallel_n 的 shiftup 等非存储单槽队列。

## Decision

### D1：assembler 增「level-0 平坦流」分支（`src/driver/assembler.py`，ADR-054 注释段）

进入条件（三个判据**同时**满足，缺一回落旧 embed 主线）：

```python
if len(kernel_map) == 1 and not is_smi_ds_t_dp and iq_II == 1:
```

- `len(kernel_map)==1`：单槽 kernel（kernel 内仅 1 个 op 行）；
- `not is_smi_ds_t_dp`：SMI_DS_T 配对 datapath 走 ADR-038/043 的 B=2 专用路径，不在本 ADR 动；
- `iq_II==1`：II>1 的节拍问题见 §4，挂 DEBT-054-II2-CADENCE。

发射形态（三行，别无其它）：

```
[abs_t=0        INIT  vld=0, loops=l_init（见 D2 垫拍）]
[abs_t=l_init+1 op 行 vld=1, level=0, loops=loops_val, dly=op.dly&7,
   inc=iq_max_stride（memory IQ；rtovr/ldb/stb/ur-*）否则 0, inc_embed=0]
[abs_t=l_init+2 TERM  vld=1, level=0, loops=0, jump=1, code=0（payload 空）]
```

`loops_val = total_iterations_needed - skip_iterations - 1`（沿用主线上方计算，与 embed 形
同源）。ur-* 队列的 BANK/ADDR 按 `(orig_offset - op_stride * relative_skip) % bank_depth`
**预烘焙进 payload**（relative_skip = 本 op 相对队列起始的迭代偏移），此后地址步进完全交给
RTL 的 INC——与 golden ur-r0 `inc=1` 同构。

**INC 域守卫（hco 2026-09-12 建议①，已落实）**：RTL `w_inc_comp`（`smi_iq_iic.v:245-246`）
是 **4-bit 二进制补码**（合法 -8..+7）。`is_memory_iq and iq_max_stride > 7` 时平坦分支
**立即 ValueError 拒绝发射**（不回落 embed 主线——那是 VCS 未证形态，静默回落更糟）。
当前 memory IQ stride∈{0,1} 守卫未触发；回归测试 `test_m15_flat_inc_range_guard` 锁死此行为。

### D2：相位对齐——INIT 多垫 1 拍

embed 形 HDR 行占 1 拍 issue slot（vld=1 但 `_instruction_valid=False`，纯 loop setup），
body 首 fire 在 `l_init+1` 拍。平坦形删掉 HDR 后若不动 INIT，全流**提前 1 拍**（mul e2e
`other_read` 审计实证：读值恰差一拍）。故分支内把主线上方已发的
`INIT(loops=l_init-1)` **改写为 `loops=l_init`**，并断言该指令确为本队列 INIT
（`trace_id == f"init_{iq_name}"`）——垫拍落在错行等于静默相位漂移。

### D3：TERM 行契约

`vld=1, jump=1, loops=0, level=0, code=0`（payload 空 → packer 补 0）。
照 golden `append_end`（`mul_scalar_op.cpp:821`）逐字段对齐；模拟器侧 IQUnit 对
jump 控制帧的处理（`sim/smc_simulator/control/iq_unit.py:132-137`）与 fire 计数正交，
不引入新行为。守卫⑭（D4）把「仅末行 jump 且 code==0」做成硬断言。

### D4：回归守卫与契约测试

- **守卫⑭ `g14_flat_stream_protocol`**（`tests/unit/test_rtovr_sparse_guards.py`）：
  对产物 build_dir 的**每一** `iq_*.hex`（module 名由文件茎映射 ur-r→UR_READ、
  ur-w→UR_WRITE、rtovr→RTOVR、其余 stem.upper；**code_bits 从 spec.modules 读，禁手抄**）：
  (a) **全流 embed==0**——比硬件死区（embed∈{1..6}，`:441`→`:468`）**有意加严**
  （golden 原生 embed=7 wait 行不进我方产物）；(b) 仅**末行** JUMP=1 且 body==0；
  (c) op 行（vld=1 且 jump=0）**code≠0** 哨兵（hco 建议②，防全零 code 假绿）。
  变异测试 `test_m14_embed_row_detected`：embed 行插入 / 终止行缺失 / 全零 code op 行
  三病理各必被检出。
- **契约测试更新**：`tests/unit/test_multi_smc_basic.py::test_shiftup_physical_instr_emitted`
  原断言 INIT+HDR+BODY 旧形，按本 ADR 改判 INIT+BODY+TERM 且 `assertNotIn("HDR")` +
  header 级断言（INIT vld=0；BODY vld=1/level=0；TERM vld=1/jump=1）。
  embed 旧主线的活跃覆盖由 add e2e（II=2，回落主线）与 matmul/fma 既有用例承担。

### D5：验收（VCS 实体，非 sim）

`verification_v2/tools/package_vcs_case.py --op mul --case compiler_mul` 打包 →
`~/u280_sim_v422/sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh
multi_inst_stage/compiler_mul/smc_0` → sim.log **COMPARE PASSED**；
独立 python 复核 ur_din/ur_dout 比对 **8 bank × 256 行 = 2048/2048 mismatch=0**。
（⚠ 单段 `run_sim_aiacc_top_f.sh` 已腐——gen_ram_4MB XMRE，禁用。）

## Consequences

### 正面

- **编译器产物首次真 RTL 实测通过**：阶段 2-M1（mul 上 VCS）达成，M2 上板解锁。
- 流形态与全部手写 golden 同构——此后「编译器 vs golden」差异只剩调度取值，不再有形制差异。
- 守卫⑭ + 帧宽守卫⑫⑬ 把两个 VCS 取证的失效模式（embed 回潮 / 帧宽回移）变成编译期硬错误。

### 风险 / 债务

- **DEBT-054-MULTISLOT**：多槽 kernel 队列（matmul_parallel_n 等）仍走 embed 主线，
  RTL 存疑（从未 VCS 实测该形态）。阶段 3 重 lowering 时必须连本债一起结。
- **DEBT-054-II2-CADENCE**：II>1 单槽队列（add 的 ur-r0 双行 kernel 族）。候选方向：
  (a) 平坦两行交替（II 拍 loop Î level-0 双行重放语义待 RTL 取证）；(b) 把 II 塞进
  DLY/INC 组合表达；(c) 调度器直接产 II=1（若物理允许）。**任一选项落地前 add 不上 VCS。**
- **相位垫拍（D2）依赖「INIT 恒为本队列首条」**这一隐含序：断言已锁 `trace_id`，
  但若主线未来改 INIT 发射位置，本分支须同步复核。
- fma_parallel_n 的 shiftup 被本分支收编（II=1 单槽）——其 e2e 终值 parity 仍绿
  （sim 同构建模），但该队列的平坦形**未经 VCS 单独实测**；matmul/fma 全流 VCS
  属 DEBT-054-MULTISLOT 同批结清。
- **对 golden 的两处已知偏差（hco 待商榷②，当前配置 VCS PASS 背书，gated）**：
  op 行 **dly=0**（golden vin0/1 发 dly=1、vout0 发 dly=7）；**loops=N-1**
  （golden 用 `loops_pipeline = N-1 + cru_delay_comp + 8`，排干窗多 15 拍）。
  本配置（8-SMC 近端链、mul 单遍）不承重；**长链算子 / 远端 SMC（链尾、SMC_ID 大）
  排干余量变小**——此类新 case 上 VCS 前须按 golden 补偿项复核，不得直接外推
  本 ADR 的参数取值（⑭ 只锁协议形，不锁这几项取值）。
- ~~INC 域~~ **已闭合（合入前 hco 建议①）**：`w_inc_comp` 4-bit 补码（-8..+7）超域风险，
  平坦分支已加显式 ValueError + `test_m15_flat_inc_range_guard` 回归锁。
- **DEBT-054-DUALSRC**（转记自 ADR-053 errata 的 hco 裁决）：模拟器 decoder 与发射侧
  modules.yaml 双数据源治理——SSoT 统一重构（spec 加载器改从 modules 取宽）后续立债，
  当前由守卫⑬ 锁双端同源。
- **版本门提示（hco 待商议③转记）**：⑫⑬ 锁的「CODE[6]=保留位」若未来 RTL 启用为功能位，
  yaml 四处 + 守卫三处须人工升版——属常规 RTL 版本门。

### 不做（本 ADR 范围外）

- 不动 SMI_DS_T 配对 datapath（`is_smi_ds_t_dp` 分支）、stream_beats 显式-N 分支、
  explicit_descriptor 分支、two-gather CRI 分支——四条既有 `continue` 快道原样保留。
- 不改 Python 模拟器对 flat 流的建模（§3：sim 本就同构，无需改；这也再次说明**此类
  变更的验收权在 VCS 不在 sim**）。
- 不做「全自动流形态选择器」：条件判据硬编码三个，够住当前全部用例；推广请新立 ADR。
