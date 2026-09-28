---
adr_id: ADR-053
title: RtoVR 稀疏连通性建模 + VIN0/VIN1/VOUT0 绕行单元 —— 编译器从"全连接假设"到"RTL 稀疏路由"
decided: 2026-09-11
last_verified: 2026-09-11
still_holds: proposed
builds_on: ADR-031, ADR-052, ADR-050, ADR-008
hco_review: approved-with-notes（2026-09-11：3 阻断项 B1/B2/B3 已修入本文 §3/D4/D5；裁决 = reg_pipe 新增 + 阶段 1 smc_id_en=31；Q1 延迟口径订正为往返 +4）
user_decision: user 2026-09-11「我觉得可以做b，这个和RTL的实现是一样的，你直接补一个就成」（选定路线 B：建模稀疏连通性 + 绕行单元，非路线 A 手写绕行）
---

# ADR-053 · RtoVR 稀疏连通性建模 + VIN0/VIN1/VOUT0 绕行单元

> **状态：Proposed —— 本文档为设计定稿，实现未开始。**
> **一句话**：编译器把 RtoVR 当全连接交叉开关用（任意输入可路由到任意输出），但 v4.22 RTL 在
> `USE_RTOVR_SPARSE_MASK` 下是一张 **40 输出 × 34 输入的稀疏掩码表**；编译器发出的每一条
> `UR_RDATA→计算口`、`计算口→UVR_WDATA` 直连 select 都被掩码**屏蔽为零**。VCS 实证 **51/51 算子全部中招
> （472/680 条有效 select 被屏蔽）**，且编译器与 Python 模拟器共用同一错误假设 → 「30/34 PASS」是**结构性假绿**。
> 本 ADR 把 RtoVR 连通性**建模为配置数据**（邻接表），并新增 **VIN0/VIN1/VOUT0 三个绕行寄存器单元**，
> 让 UR↔计算 的数据传输按 RTL 唯一合法路径走：`UR →(RtoVR out14/15)→ VIN →(out3..6)→ 计算 →(out16)→ VOUT →(out35)→ UR`。
> 属 `hw_config/**` 数值/结构变更 + `src/driver/hw_resource.py` 资源类型变更 + `src/synthesis/**` 路由变更 → **必调 hco + 写 ADR**（CLAUDE.md routing）。

## Context

### 1. 问题：编译器的"全连接假设"在 RTL 上整体不成立（VCS 实证，非推断）

**三层证据**（详见 `~/rtovr_mask_probe/`，记忆 `compiler-rtovr-dense-vs-sparse-mismatch`）：

**① VCS 差分 TB（30/30 通过）**：`~/rtovr_mask_probe/tb_rtovr_mask.v` 独立例化
`u280_sim_v422/smc/RtoVR/RtoVR.v`（60KB，`module RtoVR` 在 :45），**同一 TB 编译两轮**
（带/不带 `+define+USE_RTOVR_SPARSE_MASK`），期望值**人工硬编码**（不从 RTL 掩码推导，防自证）：
- `out3 FPMUL_S0 sel=30(UR0)`：SPARSE 轮输出 **0x0**，DENSE 轮输出 `din` —— 直连被屏蔽
- `out35 UVR_WDATA_0 sel=3(FPMUL_D)`：SPARSE 轮输出 **0x0** —— **连结果写回 UR 都被屏蔽**
- `out14 VIN0_S sel=30(UR0)`：两轮都通 —— 绕行通路合法

**② 全量普查**（`decode_compiler_rtovr.py`）：解码编译器真实 emit 的 `iq_rtovr-*.hex`（RTOVR body 7b，
`bit0=VALID`、`bit[6:1]=SEL`，位序经自洽性验证）：
```
51 个算子目录   有效 select 680 条   被屏蔽 472 条   涉及 51 个算子
「0 屏蔽」的算子数 = 0   ← 无一幸免
```

**③ 假绿活标本**：develop HEAD 现跑 `--op add`，emit 与历史逐字相同、4/5 条被屏蔽，而编译器当场自报
`ALL PHYSICAL CHECKS PASSED`（`pipeline_runner.py` 用 Python 模拟器验，**模拟器同样无掩码过滤**，
`sim/smc_simulator/datapath/rto_vr.py:35-43` 的 `val = sources.get(src_id, 0)` 全连接放行）。

> **假绿的结构性根因**：编译器（`ucode_solver.py`）与模拟器（`rto_vr.py`）**共用同一个错误的全连接假设**，
> 二者自洽地"通过"，但都与 RTL 不符。这就是「编译器偏通用 vs 产物上不了硬件」这一困惑的实质——
> **不是编译器该更通用，是它的硬件模型漏了"连通性"这个维度**。

### 2. RTL 权威：40 张稀疏掩码（`RtoVR.v` `ifdef USE_RTOVR_SPARSE_MASK` 分支，已提取为 set-bits）

每张掩码是一个 64-bit 数，bit-i=1 表示「允许输入 i 路由到该输出」。`RtoVR.v` 的 mux 逻辑是
`mux_result = out_mask[sel] ? input[sel] : 0` —— **被屏蔽即输出 0（不是直通、不是报错）**。
完整 40 张掩码经 `parse_masks()` 提取（`doc_table_crosscheck.py`），按结构分四组：

| 输出组 | out 索引 | 掩码 | 允许输入（sel） | 结构语义 |
|---|---|---|---|---|
| **13 个计算单元 S 口** | 0–12（INTADD_S0/S1/S2, FPMUL_S0/S1, FPADD_S0/S1, FPTOFP_S, INTTOINT_S, FPTOINT_S, INTTOFP_S, LOGIC_S0/S1） | `0x1FDB` | {0,1,3,4,6,7,8,9,10,11,12} = 各计算 D 输出 + **VIN0_D(11) + VIN1_D(12)** | 计算单元的操作数来源；**禁 UR(30-33)、禁 ST(2,5)、禁 VOUT0_D(13)** |
| **LOGIC_ST** | 13 | `0x1824` | {2,5,11,12} = INTADD_ST, FPADD_ST, VIN0_D, VIN1_D | 走档汇聚 |
| **VIN0_S / VIN1_S** | 14, 15 | `0x3C0000000` | **{30,31,32,33} = UR_RDATA_0..3** | **UR 数据唯一入口** |
| **VOUT0_S** | 16 | `0x7FF` | {0..10} = 各计算 D 输出（含 ST） | 计算结果唯一汇聚 |
| **INT8 MAC 串链** | 17,18 → 19–22 → 24–31 → 32,33 | 单 bit | 17={0}, 18={1}; 19–22={14..17}; 24–31={18..25}; 32={26}, 33={27} | **零自由度的硬编码乘累加管**（INT8TOFP16→FP16MUL→ADD5→ADD8） |
| **FP16MUL_S4567** | 23 | 单 bit | {29} = SHIFTUP_D | 广播移位馈乘加 |
| **SHIFTDN_S** | 34 | `0x3C0000000` | {30,31,32,33} = UR_RDATA | 结果收集 |
| **UVR_WDATA_0..3** | 35–38 | `0x30002000` | **{13,28,29} = VOUT0_D, ADD8_D, SHIFTUP_D** | **回写 UR 的唯一通路** |
| RESERVED | 39 | `0x0` | ∅ | — |

> **编译器当前的两类致命直连**（普查 472 条的构成）：
> - `UR_RDATA_x(30-33) → 计算口 S(0-12)`：掩码 `0x1FDB` 的 bit30-33 = 0 → **屏蔽**（应走 VIN）
> - `计算 D(3,4,..) → UVR_WDATA(35-38)`：掩码 `0x30002000` 只允许 {13,28,29} → **屏蔽**（应走 VOUT）

### 3. 官方文档连接表（数据源，与 RTL 858/862 位互证）

用户提供的硬件设计文档《RtoVR 连接表》（41 输出列 × 36 输入行，含 MIN0/MIN1 占位行列）。
已转录为 `~/rtovr_mask_probe/rtovr_conn_table.tsv` 并与 RTL 掩码逐位交叉核对
（`doc_table_crosscheck.py`）。⚠ **hco 审查订正（2026-09-11，B1）**：该 tsv 存在行宽漂移
（28/34 数据行只有 40 bit，脚本在宽度检查处即退出、走不到逐位比对），ADR 早前引用的
「858/862 位一致」出自自动对齐的中间版本，**当前 artifact 不可直接复算**。落盘 D1 `allowed`
前必须先把 crosscheck 修到能跑通 bit-compare 并全绿（守卫⑩ 的行宽 CI 防回退）。
**但注意：D1 的权威数据源本来就是 RTL 掩码本身 + D8① 的 AST 逐位守卫，文档表仅作旁证**——
此转录问题不阻塞设计，只暂时削弱「文档互证」叙事。表结构与 §2 掩码同构，且额外揭示：
INTADD_D0/D1 除计算口外还独家可入 INT8TOFP16_S0/S1（out17={0}, out18={1}）。

> ⚠ **命名差异（实现时必读）**：文档表/RTL 用 `UR_RDATA_x`（输入 30-33）与 `UVR_WDATA_x`（输出 35-38）；
> 编译器 `rtovr_config.yaml` 用 `UR_READ_x_D`（consumers 30-33）与 `UR_WRITE_x_D`（producers 35-38）。
> **编号一致、命名不同**。邻接表以**编号**为权威，名字仅作注释，避免转录歧义。

### 4. golden 修复蓝图：手写算子已给出唯一合法路由（24 条 select，对照 RTL 掩码 0 屏蔽）

`sub_8smc_v3/smi_iq_inst/stage_1/`（已上板的 mul/sub 算子）的 RtoVR 指令解码
（`decode_rtovr` 同款逻辑，CB=7）——**这就是编译器要复现的路由**：

| out | 单元 | 有效条数 | sel（源） | 方向 |
|---|---|---|---|---|
| 14 | VIN0_S | 4 | `UR2(32)`, `UR0(30)` | **UR → VIN** |
| 15 | VIN1_S | 4 | `UR1(31)` | **UR → VIN** |
| 3 | FPMUL_S0 | 2 | `VIN0_D(11)` | VIN → 计算 |
| 4 | FPMUL_S1 | 2 | `VIN1_D(12)` | VIN → 计算 |
| 5 | FPADD_S0 | 2 | `VIN0_D(11)` | VIN → 计算 |
| 6 | FPADD_S1 | 2 | `VIN1_D(12)` | VIN → 计算 |
| 16 | VOUT0_S | 4 | `FPMUL_D(3)`, `FPADD_D(4)` | 计算 → VOUT |
| 35 | UVR_WDATA_0 | 4 | `VOUT0_D(13)` | **VOUT → UR** |

合计 **24 条 select，对照 §2 掩码 0 屏蔽**（实证，非推断）。完整回路：

```
UR_RDATA ──(out14/15)──> VIN0/VIN1 ──[IPR +1拍]──> VIN0_D(11)/VIN1_D(12) ──(out3/4/5/6)──> FPMUL/FPADD
FPMUL_D(3)/FPADD_D(4) ──(out16)──> VOUT0 ──[OPR +1拍]──> VOUT0_D(13) ──(out35)──> UVR_WDATA_0 ──> UR
```

### 5. VIN/VOUT 物理通路：IPR/OPR 各 1 拍 + 2 次额外 RtoVR 穿越（hco Q1 订正「+2」口径）

- **IQ 队列**：`IQ68→cru_vin0`、`IQ69→cru_vin1`、`IQ70→cru_vout0`（`smi/smi_top.v:788-790`：
  `o_cru_vin0_r <= iq_inst_vld[68] ? iq_inst[68][CRU_VIN_W-1:0] : 0` 等三条）。
- **CRU 位宽 6 位**（≠ RtoVR 的 7 位）：`CRU_VIN_W=6`/`CRU_VOUT_W=6`（`smi_iq_cru_widths.vh:30-31`；
  `smi_top.v:276-278` 的 `wire [5:0] dbg_smi_cru_vin0/vin1/vout0` 佐证）。
  位布局 = `{smc_id_en[5:1], vld[0]}`（**body[0]=vld，body[5:1]=smc_id_en**）。
- **per-SMC 门控**：`w_vld_eff = w_vld_raw && (w_smc_id_en >= SMC_ID)`（`IPR.sv`/`OPR.sv`）。
- **各 1 拍延迟**：`dr_vin0_d <= dvr_vin0_s;  // One cycle delay`（`IPR.sv:87`、`OPR.sv:87`）。
  ⚠ **hco 审查订正（Q1，延迟口径）**：绕行链的完整延迟**不只是 IPR+OPR 的 +2**——绕行还多了
  **2 次 RtoVR 穿越**（RtoVR 输出带锁存：`RtoVR.v:152` `OUTPUT_PIPE_STAGE=1` + `:737-747` vld 锁存器；
  编译器模型里每跳 RTOVR 边计 +1，见 `dependency_axioms.py:163-167` `t_consumer==t_latch+1`）。
  精确口径：单方向 UR→计算 = 直连 1 次穿越 → 绕行「2 次穿越 + IPR」= **1→3 拍**；计算→UR 同理；
  **往返合计比假想直连多 +4 拍**（IPR+OPR 各 1 + 额外 2 次 RtoVR 各 1）。「+2 周期」直觉只命中
  IPR/OPR 部分，漏算了 2 次额外 RtoVR 锁存。由于直连在硅上从未可用，这主要是建模口径问题，
  但 **D4 的延迟必须由改写后 DFG 结构性推出，禁止硬编码 +2 常数**（硬编码会少算 2 拍）。
- **golden 的 VIN/VOUT 指令**（emit 目标，`stage_1/iq_68/69/70`）：body 低位 `0x3f = 0b111111 = {smc_id_en=31, vld=1}`。
  ⚠ **errata（2026-09-11，D7 验收时全机普查订正）**：上述 0x3f 仅命中 `sub_8smc_v3_fpga_verify`
  一份（即 §4 blueprint 的提取源）。全机 20 份 golden `iq_68_vin0.hex` 普查：**19/20 发 7**
  （body 低 6 位 `0x0f`），含**全部** mul_scalar 上板 PASS 用例（r2 板端证据链、全部 boundary
  用例、non_llama 模板）。D7 验收目标是 mul_scalar → **D4 pass 实发 `PARAM_SMC_NUM-1`=7**
  （与 ADR-031 全单元约定同源同值；阈值语义 `en>=smc_id` 下与 31 功能等价，8 SMC 全覆盖）。
  原 hco「照 golden 发 31」裁决所据的 golden 记忆即此处误记，裁决随本 errata 修订为发 7。

### 6. 编译器现状：VIN/VOUT 被当成"免费端口"而非独立单元

| 位置 | 现状 | 后果 |
|---|---|---|
| `hw_config/rtovr_config.yaml` | `rtovr_connectivity` 只有 `consumers_dr`(0-33) / `producers_dvr`(0-37) **名字映射**，**无邻接信息** | 全连接假设的载体 |
| `src/synthesis/ucode_solver.py:327` | `compute_ops = [op for op in ops if op.op_type.upper() != "RTOVR"]` | **RTOVR 被排除出 SMT 求解** |
| `src/synthesis/axioms/resource_axioms.py:58-60` | 注释「[SMT-REFACTOR] 已经剥离了 RTOVR 的 SMT 物理搜索，直接放行」 | 连通性约束被**刻意删除** |
| `src/driver/hw_resource.py` | `PortSlot` 有 `VIN0_S=14/VIN1_S=15/VOUT0_S=16`(:53-55)、`VIN0_D=11/VIN1_D=12/VOUT0_D=13`(:91-93)；但 **`ResourceType` 无 VIN/VOUT 单元**(:3-29) | 编译器知道 VIN/VOUT 是 RtoVR 端口号，**不知道它们是独立 1 拍寄存器单元（IPR/OPR），更不知道有 IQ68/69/70 队列** |
| `hw_config/modules.yaml` | 无 VIN0/VIN1/VOUT0 单元定义 | 无法 emit `iq_68/69/70` |
| `sim/smc_simulator/datapath/rto_vr.py:35-43` | 无掩码过滤 | 模拟器假绿 |
| `hw_config/pipeline_delays.yaml:60-62` | `VIN0:0 / VIN1:0 / VOUT0:0` 占位 | 延迟应按 RTL 改为 **1** |

## Decision

> **设计哲学**（回应「编译器该不该装这么多硬件细节」）：本 ADR 把连通性建模为 **`hw_config` 里的配置数据
> （邻接表），不是调度器代码里的硬编码**。调度器/SMT 代码保持通用（读表约束），硬件特殊性放在声明式配置里
> —— 这正是「代码通用、配置忠实」的分层，与 LLVM TableGen / CGRA 编译器（稀疏互连路由是标准调度约束）同构。
> 「产物上不了硬件」的病根是模型**漏了连通性维度**，不是模型太忠实。

### D1：`rtovr_config.yaml` 加邻接表（数据源 = 官方文档表 ⊕ RTL 掩码互证）

在 `rtovr_connectivity` 下新增 `allowed` 段，**以输出（producers_dvr 编号）为 key，值为允许的输入
（consumers_dr 编号）列表**，编号为权威、名字作注释。内容**逐位等于 §2 的 RTL 掩码**
（**直接以 RTL 为权威源生成**；文档表互证待 DEBT-053-TRANSCRIPTION 修复后恢复为旁证，见 §3 订正）。
结构骨架：

```yaml
rtovr_connectivity:
  consumers_dr: {0: INTADD_D, ..., 30: UR_READ_0_D, ...}   # 既有，不动
  producers_dvr: {0: INTADD_S0, ..., 35: UR_WRITE_0_D, ...} # 既有，不动
  # ↓ 新增：稀疏邻接表（每个输出允许哪些输入）
  allowed:
    3:  [0,1,3,4,6,7,8,9,10,11,12]   # FPMUL_S0：计算D + VIN0_D(11) + VIN1_D(12)；禁 UR
    14: [30,31,32,33]               # VIN0_S：仅 UR_RDATA_0..3（UR 唯一入口）
    16: [0,1,2,3,4,5,6,7,8,9,10]    # VOUT0_S：各计算D（含ST）汇聚
    35: [13,28,29]                  # UR_WRITE_0_D(UVR_WDATA_0)：仅 VOUT0_D/ADD8_D/SHIFTUP_D
    # ... 40 项全列（与 RTL 掩码逐位一致）
```

`hw_spec.py` 新增 `get_rtovr_connectivity() -> dict[int, frozenset[int]]`（out→允许 in 集合），
供调度器与校验器消费。**换目标硬件 = 换这张表，代码不变。**

> ⚠ **诚实边界（hco B1 订正后）**：`allowed` 表的正确性 = 「**直接以 RTL 掩码为权威源生成**，
> 再由 D8① 的 AST 守卫逐位兜底」。文档表互证当前不可用（tsv 行宽漂移，见 §3 与
> DEBT-053-TRANSCRIPTION），修复后作为旁证恢复；落盘前仍须确保 40 项与 RTL 掩码**逐位全等**。

### D2：`modules.yaml` + `master.yaml` 双处新增 VIN0/VIN1/VOUT0 单元（**两处必须同步**）

照 FPMUL 的单元定义格式，新增三个绕行寄存器单元。**`master.yaml` 优先于 `units/*.yaml`**
（`hw_spec.py:200`）—— 两处必须同步，否则静默失效（隐藏地雷②，ADR-031 同款教训）：

```yaml
  VIN0:   # IPR 输入寄存器（RtoVR out14 → IPR → in11）
    type: reg_pipe           # 新增类型：vld 门控流水寄存器
    latency: 1               # IPR.sv:87 "One cycle delay"
    inputs:  [DVR-VIN0-S]    # RtoVR producers_dvr[14]
    outputs: [DR-VIN0-D]     # RtoVR consumers_dr[11]
    code_bits: 6             # CRU_VIN_W=6（smi_iq_cru_widths.vh:30）
    code_fields:
      - {name: VALID, bits: 1}        # body[0]
      - {name: smc_id_en, bits: 5}    # body[5:1]；golden 发 31（全广播）
  VIN1:   {同上，inputs:[DVR-VIN1-S](15), outputs:[DR-VIN1-D](12)}
  VOUT0:  {同上，inputs:[DVR-VOUT0-S](16), outputs:[DR-VOUT0-D](13), code_bits: 6}  # OPR 输出寄存器
```

`pipeline_delays.yaml:60-62` 的 `VIN0/VIN1/VOUT0: 0` → **改为 1**（按 IPR/OPR 实测 1 拍）。

> ⚠ **errata（2026-09-12，VCS 取证订正）——VIN/VOUT IQ 帧宽是 7 不是 6，`CRU_VIN_W=6` 只是
> 数据通路提取宽度**：上方 sketch 的 `code_bits: 6`（所据 `smi_iq_cru_widths.vh:30-31` 的
> `CRU_VIN_W=6`）与 D5-③ 的 `BITS_CODE=6` **均错，已按本 errata 修**。同一份 vh :16 明载
> 「IQ68..69.70 use CODE[5:0] for VIN0/VIN1/VOUT0; **CODE[6] is reserved**」——IQ 帧 =
> 41(header) + **7**(CODE)，header 须落 bit **[47:7]**、VLD 在 **bit47**；而 `CRU_VIN_W=6`
> 只是 `smi_top.v:788 iq_inst[68][CRU_VIN_W-1:0]` 的**数据通路**提取宽度（取 body[5:0]、
> 丢保留位 body[6]）。曾按 6 建模 → header 落 [46:6] → RTL 在 bit47 读 vld=0 → 全 VIN/VOUT
> 行 header 无效，VCS 实测 compiler_mul bank2 全零（golden 流同数据通过；golden C++
> `mul_scalar_op_constants.h MUL_SCALAR_VIN_VOUT_CODE_BITS=7` 一致）。
> **修订内容**：`modules.yaml` / `master.yaml` / `specs/units/{vin0,vin1,vout0}.yaml` /
> `iq_config.yaml` 四处 `code_bits|BITS_CODE` 改 **7**，且 code_fields/micro_instructions
> **显式登记 `RESERVED(1bit, default 0)`（body[6]）**——有两点约束：(a) `test_codebits_consistency`
> 要求字段和==code_bits，隐式保留会红；(b) **模拟器 decoder payload = specs 微指令字段和**
> （`sim/smc_simulator/core/decoder.py:51-54`），与发射侧 `modules.yaml code_bits` 是**双数据源、
> 不自动同源**——只修发射侧会令 sim 侧 VLD 读 bit46=0 → `IQUnit.exec_active=0` →
> `kernel.py controls=0` → RegPipeUnit 全链路死（e2e 回归实证全零，必须先修 sim 侧再回归）。
> 守卫锁定：`g12_vin_frame_vld`（emitted VIN hex 每行 bit47=1，帧左移 1 位必检出）+
> `g13_sim_decoder_frame_width`（模拟器 decoder payload=7 且 VLD offset=47，双端同源）。
> 已落地当日：三侧 yaml + iq_config 同步登记，守卫全绿。
>
> ✅ **hco 复核（2026-09-12，独立逐行复核 RTL/golden/四侧 yaml/守卫）：通过**。裁决：显式
> `RESERVED` 登记是**唯一正确建模**（ADR-029/031 既有回归门 `test_codebits_consistency`
> 强制 code_bits==字段和，隐式保留位必红，且丢「bit6 是硬件保留位」的文档位）；双数据源 +
> 守卫⑬ 方案可接受，**SSoT 统一（sim 规格加载器改从 modules 侧取宽）记 DEBT-054-DUALSRC，
> 非阻断**。补强证据（hco 另掘两处，比 vh:16 更硬）：`smi/smi_iq.v:40` IQ28..91 的
> `BIT_CODE=7` 参数内联注释「48-bit ABI slot; IQ68-70 use CODE[5:0], bit6 reserved」；
> `:85-86` header 字段序 VLD(1)+JUMP(1)+EMBED(3)+EMBED_END(6)+LOOPS(10)+COND(7)+DLY(3)+
> INC(4)+INC_EMBED(6)=41 且 `WR_INST_WIDTH = 41+BIT_CODE` → 帧 48bit、VLD 为 header
> MSB = bit47。

> ✅ **hco 裁决（2026-09-11）：批准新增 `reg_pipe`**。事实面：`ModuleSpec.type` 是自由 `str`
> （`hw_spec.py:40`），全仓**无任何代码按 module.type 派发行为**（只有 op_type/unit_type/res_type
> 派发），新增取值的爆炸半径≈0；唯一可考虑复用的 `transfer` 桶装着 SHIFTUP/SHIFTDN
> （跨 SMC 搬运、IQ 语义迥异），塞入会产生误分类引力，故语义精确优先。
> **但注意**：`type` 字段无任何强制力，真正防漂移的是 D8② 的 master↔modules 双写同步守卫，
> 别指望 `reg_pipe` 本身约束任何东西。另 hco 订正本文早前的过度谨慎：新增 `type` 取值
> **不构成 pydantic schema 变更**（str 自由字段）；hco 触发条件本就由「新增硬件单元类 +
> pipeline_delays 数值变更」充分满足。

### D3：`hw_resource.py` `ResourceType` 加 `VIN0/VIN1/VOUT0`

在 `:29`（`STB_AXI` 之后）加三枚举。注意与既有 `PortSlot.VIN0_S/VIN1_S/VOUT0_S`(:53-55) /
`VIN0_D/VIN1_D/VOUT0_D`(:91-93) 命名**不冲突**（不同 Enum：一个是单元类型，一个是 RtoVR 端口号）。

### D4：调度器确定性绕行路由（**非 SMT 搜索**，做成 lowering pass）

RtoVR 的绕行是**确定性的**——UR 数据只能经 VIN0/VIN1 进计算（out14/15 掩码只有 UR）、计算结果只能经
VOUT0 回 UR（out35-38 掩码只有 {13,28,29}）。**没有搜索空间**，故不引入 SMT 连通性搜索
（避免重开 ADR-013 删掉的 `add_rtovr_crossbar_axiom` 坟场），而是做成**确定性 lowering pass**：

- **UR_READ → compute**：在 DFG 中把 `UR_RDATA_x → compute_S` 的边改写为
  `UR_RDATA_x →(out14/15) VIN0/VIN1 →(VIN0_D/VIN1_D) compute_S`，即**插入一个 VIN 中继节点**。
  模板照 `verification_v2/ops/configurable.py:_inject_shiftup_broadcast`（DFG 构建期在两节点间插搬运单元的现成骨架）。
- **compute → UR_WRITE**：把 `compute_D → UVR_WDATA` 改写为
  `compute_D →(out16) VOUT0 →(VOUT0_D) UVR_WDATA`，插入一个 VOUT 中继节点。
- **延迟（hco Q1：禁止硬编码常数）**：绕行链插入后，延迟由**改写后 DFG 结构性推出**——每条新
  RTOVR 边吃现有 +1（`dependency_axioms.py:163-167`），VIN/VOUT 节点各吃 `latency:1`。
  净效果：单方向 1→3 拍、往返 +4（见 §5 订正）。**严禁在 dependency_axioms 里硬编码 +2/+4**。
- **VIN0/VIN1 只有 2 个口**：多操作数算子（>2 个 UR 源）须分时复用 VIN0/VIN1 —— 这是**新的 II 约束**，
  在 pass 里以「同一拍最多 2 个 VIN 注入」为容量约束处理。
- **VOUT0 只有 1 个口（hco Q2 补充，对称约束）**：回写侧全部通用计算结果只能经
  out16 → VOUT0_D(13) → UVR_WDATA（out35-38 掩码 {13,28,29} 中 28=ADDTREE8TO1_D、29=SHIFTUP_D
  是专用源）。同一拍两个计算单元同时出结果 → VOUT0 冲突 → **对称 II 约束：同一拍最多 1 个
  VOUT0 回写**（golden 本身就是错拍的：out16 的 sel=3×2 与 sel=4×2 落在不同行）。pass 必须
  同时施加 VIN(≤2/拍) 与 VOUT(≤1/拍) 两个容量约束。

> **连通性合法性校验**：pass 生成每条 `(out, sel)` 后，用 D1 的 `get_rtovr_connectivity()` 做**事后断言**
> （每条 select 必须置位于邻接表）—— 把 §1 的「472 条被屏蔽」变成**编译期硬错误**，不再假绿。
>
> ⚠ **errata（2026-09-11，全量回归取证补入）——UR_READ 生产者的显式 sel 不可信**：
> configurable 常量注入的 RTOVR 节点（`cinl_*`/`inl_*`）会**沿用原计算边的 select** 作显式
> `metadata["sel"]`（如 FPADD_D=4）。合法性判定若把这种陈旧 sel 并入 ∃ 集合，`allowed[6]∋4`
> 即假阳「合法」而跳过绕行；发射期 solver 改派真实 UR 读口（30-33）后该边即被掩码置 0
> （实证：hardsigmoid hw-parity 四条 `(6,31)/(6,33)/(12,30)/(12,33)` 全 0）。修订规则：
> **UR_READ 生产者的候选 sel 集合恒为 {30,31,32,33}，显式 sel 一律忽略**（UR 读口物理上
> 只能发 30-33）；非 UR 生产者仍保留显式 sel 并入（matmul addtree=26/27 端口名不匹配场景）。
> 回归守卫⑪（`test_g11_no_direct_ur_edge_survives_pass`）锁定：pass 后 UR_READ 直连边
> 只允许落 14/15/34 三个 UR 槽。

### D5：emit VIN/VOUT 指令（IQ68/69/70）

**联动改动四处（hco B2：缺一不可，否则 `get_iq_ids`/emitter 静默失效）**：

1. `specs/system/system_core.yaml`：`iq_registry` 新增 `VIN0/VIN1/VOUT0` 逻辑 id（走 ADR-052 D2
   「清晰空档」范式，**不直接写 RTL 槽号 68/69/70**；TB 按文件名归位）；**且
   `resource_constraints.unit_instances` 必须同步补 VIN0/VIN1/VOUT0 实例数**
   （`hw_spec.py:242-264` 的 `get_iq_ids` 需要 registry base + instances count 两者齐备才返回 id）。
2. `master.yaml` 镜像同步（同 D2 双写纪律，master 优先于 units/*.yaml）。
3. `hw_config/iq_config.yaml`：新增 `IQ_VIN0/IQ_VIN1/IQ_VOUT0` 的 `IQConfig` 条目
   （`BITS_CODE=6`，EN_LOOPS/BITS_LOOPS 等字段照现有 IQ_FPMUL 条目格式）——emitter 组帧依赖它。
   ⚠ **errata（2026-09-12，同 D2 订正）**：`BITS_CODE` 实为 **7**（含 CODE[6] 保留位），已修。

**smc_id_en 取值与落地机制（hco 裁决：阶段 1 发 31，DEBT 挂账）**：

- ✅ **裁决：阶段 1 发 31**（照唯一上过板的 golden）。功能等价性经 hco 按 `IPR.sv:76` 验证：
  N=8 时 `31≥SMC_ID ⇔ 7≥SMC_ID` 对 id∈[0,7] 全真。与 ADR-031 不冲突（其统一约定 scope 是
  compute 单元，VIN/VOUT 当时不在 spec）；`DEBT-053-SMC-ID-EN` 挂账，留 VCS 阶段连同
  ADR-031 升 Accepted 时统一拍板。附带：31 对 N≤32 免维护，7 绑定 N=8。
- **落地机制（hco B3，关键）**：`payload_factory.py:110-121` 的 `SMC_ID_EN` 特例按**字段名**生效，
  凡 op 未显式提供 metadata 一律发 `PARAM_SMC_NUM-1=7`——VIN/VOUT 若裸走缺省分支会**静默发出 7**
  而非 31。**必须由 D4 lowering pass 在插入 VIN/VOUT 节点时显式写
  `op.metadata["smc_id_en"]=31`**（显式 metadata 先于缺省分支命中，SHIFTUP 同款路径）；
  **绝不可改 payload_factory 缺省分支**（会波及全部 compute 单元）。守卫⑧ 单测锁定此行为。

### D6：模拟器加掩码过滤 + VIN/VOUT 单元 class（**修假绿的关键**）

- `sim/smc_simulator/datapath/rto_vr.py:35-43`：在 `val = sources.get(src_id, 0)` 前加
  `if src_id not in allowed[out_slot]: val = 0`（读 D1 的同一张邻接表）—— **让模拟器与 RTL 一致地屏蔽**。
- 新增 `VinUnit`/`VoutUnit`（1 拍 vld 门控寄存器，`smc_id_en` per-SMC 门控），在 `kernel.py` 注册。
- **不修模拟器 = 假绿继续**：编译器修对了但模拟器还按全连接验，仍会假绿。这是本 ADR 不可省的一半。

### D7：验收方法 —— 编译器产物 vs 手写 golden 逐位对齐 + 物理合法性断言

1. **逐位对齐**：编译器重新生成 `mul_scalar`（或 `add`），其 `iq_rtovr-{3,4,5,6,14,15,16,35}.hex` 的
   **每条 select** 与 `sub_8smc_v3/stage_1` 的 golden **逐位一致**（§4 的 24 条）。
   ⚠ **errata（2026-09-11 验收落地时细化）**：UR 源口（sel∈{30,31,32,33}）的具体选取是调度
   自由——§4 blueprint 自身 out14 就出现 UR0/UR2 两种取值；编译器前端按 sympy 规范序接
   S0/S1（`x*other`→S0←other），与手写 golden 的源序接线相差一个 30↔31 对调。故判据①的
   操作性定义为：**存在 {30..33} 的全局一致置换 σ 使 σ(编译器集合)==golden 集合**（穷举 4!，
   比整数）；σ 之外任何一位不同即 FAIL。唯一合法源（3/4/16/35 → 11/12/3/13）无此自由度，
   仍逐位硬等。**mul 实测：6 条经 σ={30↔31} 逐位一致**（`validate_rtovr_golden_parity.py`）。
2. **物理合法性断言**（新增 `validate_physical_schedule` 第 8 维）：对编译器 emit 的所有
   `iq_rtovr-*.hex`，解码每条 `(out, sel)`，断言 `sel ∈ allowed[out]`（用 `decode_compiler_rtovr.py` 改造，
   该脚本已对 51 算子做过同款判定）。**目标：被屏蔽数从 472 降到 0。**
3. **VIN/VOUT 指令对齐**：`iq_vin0/vin1/vout0.hex` 的 vld body 与 golden 的 `iq_68/69/70.hex`
   **逐位一致**（errata 后 = `0x0f`，见上；vld 时序按调度）。**已落地（2026-09-11）：三份全 0x0f 逐位等。**
4. **负向断言**：改动一条 select 使其违反邻接表，校验器必须报错（防校验器假绿）。

> ⚠ **验收 ≠ 上板**：D7 只证「编译器产物与已上板 golden 逐位一致 + 不再发非法 select」。
> **VCS 实测与 FPGA 上板是后续阶段**（复用 `yanzheng.txt` 的 LLAMA→三阶段 RTL 闭环）。

### D8：回归守卫（pytest，每条对应一个已实证的失效模式）

| # | 守卫 | 对应失效 |
|---|---|---|
| ① | `get_rtovr_connectivity()` 40 项与 `RtoVR.v` 掩码逐位一致（AST 读 RTL） | 邻接表转录漂移 |
| ② | `master.yaml` ↔ `units/*.yaml` ↔ `iq_config.yaml` ↔ `system_core.yaml`(registry+instances) **四向**同步校验（hco 扩展：VIN/VOUT 涉及四处，任一不同步即静默失效） | 隐藏地雷②（双处不同步静默失效） |
| ③ | 编译器 emit 的所有 `(out, sel)` 满足邻接表（对 `add`/`mul_scalar`） | §1 的 472 条屏蔽回归 |
| ④ | 模拟器 `rto_vr.py` 屏蔽行为与 RTL 一致（注入一条非法 select 断言输出 0） | 模拟器假绿回归 |
| ⑤ | VIN/VOUT `code_bits=6` 且 `pipeline_delays=1` | 位宽/延迟回退 |
| ⑥ | 变异测试：对每条守卫注入病理，断言确实变红 | 「永绿断言零信号」（ADR-052 D7 同款纪律） |
| ⑦ | VOUT0 容量负向用例：同一拍两条 compute_D→out16 必须被 pass 或 validator 拒绝（hco Q2 补） | VOUT0 单口冲突静默漏检 |
| ⑧ | payload_factory 单测：带 `metadata.smc_id_en=31` 的 VIN/VOUT op 发出 31；并固化「缺省分支不被 VIN/VOUT 命中」（hco B3 防回退） | smc_id_en 静默发 7 |
| ⑨ | 双世界守卫：golden 24 条 select 在 dense（全 1）与 sparse 掩码下均合法（恒真，但锁住「编译器只发两世界交集合法码」不变量，hco Q3 补） | 未来有人拿 dense 行为放松约束 |
| ⑩ | `rtovr_conn_table.tsv` 行宽一致性 CI：每行 bit 数 == 表头列数（hco B1 补，直接防住本次 28 行漂移失效模式） | 文档表转录漂移 |

## 增量分期

- **阶段 1（本 ADR）**：D1（邻接表）+ D2/D3（单元 + 资源）+ D4（绕行 pass）+ D5（emit）+ D6（模拟器）
  + D7（验收：mul_scalar 逐位对 golden + 屏蔽数 472→0）+ D8（守卫）。
  **出口判据 = 编译器 mul_scalar 产物与 golden 24 条 select 逐位一致，且全部 select 物理合法。**
  ⚠ 阶段 1 **不跑 VCS、不上板**（同 ADR-052 阶段 1 的纪律：先证「发码与已通过的 golden 一致」）。
- **阶段 2（deferred）**：VCS 实测（复用 `~/rtovr_mask_probe` TB 思路，对编译器产物跑 RTL）+
  FPGA 上板（mul_scalar/select_int，LLAMA 数据三阶段闭环）。
- **阶段 3（deferred）**：>2 操作数算子的 VIN 分时复用调度优化；INT8 MAC 串链（out17-33）的编译支持
  （本 ADR 只建模掩码、不调度该管）。

## Consequences

### 正面
- **修掉 51/51 算子全死的根因**：编译器产物从「RTL 上整体为零」修复为「走 RTL 唯一合法路径」。
- **连通性成为一等配置**：邻接表是数据不是代码，换目标硬件零改代码；论文级贡献（编译器-RTL ABI 对齐）。
- **假绿闭环被打破**：D6 让模拟器与 RTL 一致屏蔽，D7② 让非法 select 成为编译期硬错误 ——
  「ALL PHYSICAL CHECKS PASSED」从此可信。
- **有逐位验收基准**：golden 24 条 select 是已上板的合法路由范式，验收无需从 RTL 反推语义。

### 风险 / 债务
- **DEBT-053-TRANSCRIPTION**（hco B1 升级）：文档表 tsv 存在行宽漂移（28/34 行少 1 bit，hco 实跑复现，
  crosscheck 走不到 bit-compare），「858/862 位一致」当前不可从 artifact 复算；落盘 `allowed` 前
  必须先把 crosscheck 修通到 bit-compare 全绿，并找原始文档表复核 UR-RDATA-2/3 行。权威源是
  RTL 掩码 + 守卫①，此项只影响文档互证叙事，不阻塞设计。
- **DEBT-053-VIN-VOUT-CAPACITY**（hco Q2 泛化，原 VIN-CAPACITY）：VIN0/VIN1 仅 2 口（同拍 ≤2 注入）、
  VOUT0 仅 1 口（同拍 ≤1 回写），多操作数/多结果算子的分时复用调度是新增复杂度；阶段 1 只处理
  ≤2 入 + ≤1 出/拍的算子（mul_scalar/add/select_int 全覆盖），更复杂的留阶段 3。
- **DEBT-053-SMC-ID-EN**：~~VIN/VOUT 阶段 1 发 31（hco 已裁决：照 golden + 功能等价，经 `IPR.sv:76`
  验证）~~ **errata 闭合（2026-09-11）**：golden 普查 19/20=7（见上），裁决修订为**显式发
  `PARAM_SMC_NUM-1`=7**——与 ADR-031 compute 约定同源同值，「统一问题」在发码层面自动消解；
  VCS 阶段仍需在硅上各 SMC 跑出正确（连同 ADR-031 升 Accepted 一并拍板）。
  实现走 D4 pass 显式 metadata 路径（hco B3），守卫⑧ 锁定发 7 而非 31。
- **DEBT-053-SYNTH-DEFINE**（hco Q3 新增）：`USE_RTOVR_SPARSE_MASK` 仅在 VCS 仿真脚本
  （`run_sim_aiacc_top_f.sh:366`）证实开启，**FPGA 综合流的 define 状态未证**（bupt 只读未查）。
  因 dense=全 1 掩码，「稀疏合法 ⇒ dense 必合法」，本 ADR 让编译器发稀疏合法码是两个世界交集的
  安全选择，方向无风险；但未来勿拿 dense 行为反推编译器约束。
- **+4 拍往返延迟的调度影响**（hco Q1 订正后口径：单方向 1→3 拍）：绕行链拉长关键路径，
  可能抬高部分算子的 II；阶段 1 接受（正确性优先），性能优化留后续。
- **改动面**：约 30 文件（hw_config×6 含 iq_config.yaml + schema×2 + DFG/调度×N + emit×少量 +
  模拟器×3 + 测试/文档），其中 DFG 手搭算子（`verification_v2/ops/*.py`）接绕行链是最大工作量。

### 不做（本 ADR 范围外）
- **不引入 SMT 连通性搜索**（绕行是确定性的，重开 crossbar axiom 坟场是大坑，ADR-013 已清）。
- **不改 RTOVR 的 7 位 body 格式**（VALID+SEL 不变，只改 sel 的取值合法性）。
- **不建模 INT8 MAC 串链的调度**（只把它的掩码录入邻接表，编译支持留阶段 3）。
- **不动 LDB/STB**（搬运单元不过 RtoVR 计算网络，ADR-050/052 的成果不受影响）。
