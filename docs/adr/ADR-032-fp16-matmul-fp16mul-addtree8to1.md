---
adr_id: ADR-032
title: FP16 推理 matmul 高吞吐通路（FP16MUL + FP32ADDTREE8TO1）—— S0 设计
decided: 2026-06-04
last_verified: 2026-06-04
still_holds: proposed
builds_on: ADR-031, ADR-025, ADR-023
---

# ADR-032 · FP16 推理 matmul 通路（FP16MUL + FP32ADDTREE8TO1）

> **状态：Proposed（feature 级新功能，S0 设计阶段，doc-first per ADR-004）**。user 定目标精度 = FP16 推理 matmul（FP16 乘 / FP32 累加）。用硬件 FP16MUL（32 FP16 乘/指令）+ FP32ADDTREE8TO1（8→1 reduce）替代当前 FPMUL(4×FP32)+ADDTREE5TO1，目标 ~8× 乘法吞吐。权威源 = 设计文档 v0.4.19。hco 评审（agentId a768d375631d89661）APPROVE 方向 + 标 3 阻断 + NEEDS-USER-FACT。

## Context
- **基线**：当前 matmul（`matmul_op.py` ManualMatmul* 系列）= FPMUL(4 FP32 lane)→FP32ADDTREE5TO1(4→1+cumulative II>=4)。8-SMC 广播 matmul_parallel_n 8/8 bit-perfect（ADR-025）。FP32 精度。
- **目标**：FP16 推理（LLAMA2 类，文档表79/711 举例 Matmul_Q/K/V/QK/QKV/FC，K=4096 大收缩维）。
- **scaffolding 已在**（hco 核实）：`hw_resource.py:53-67` 有 FP16MUL_S0..S3/S4567 PortSlot + ResourceType + RtoVR D0-D7（src_id 18-25）；master 有 FP16MUL/ADDTREE8TO1 functional_unit；`pipeline_delays.yaml` 有延迟。

## 硬件事实（v0.4.19，权威）
### FP16MUL（表512-514，功能 5.3.3）
- 输入 S0-S7，各 8×FP16(128bit)；输出 D0-D7，各 4×FP32(128bit)。
- `D0[x]=S0[x]*S4[x], D1[x]=S0[x+4]*S4[x+4]`（x=0..3）；同理 S1×S5→D2/D3、S2×S6→D4/D5、S3×S7→D6/D7。= **4 对×8 lane = 32 FP16 乘/指令，FP32 出**。
- **latency = 4**（user 2026-06-04 裁定；`pipeline_delays.yaml` 现写 2 是**错的**，需修 2→4）。
- **3 微指令**：表512 CRU_FP16MUL（compute，vld+src_0_id(1)+src_1_id(1)+smc_id_en(5)=8bit）/ 表513 S0123（load S0-S3，vld+src3210_high_id(1)+smc_id_en(5)=7bit）/ 表514 S4567（load S4-S7，vld+src7654_high_id(1)+src7654_low_id(2)+smc_id_en(5)=9bit）。
- 寄存器文件每源 2 份（src_*_id 选）——load/compute ping-pong 双缓冲，支撑 II=1。

### FP32ADDTREE8TO1（表520，功能）
- 输入 S0/S1，各 4×FP32；输出 D[x]，x=0..7。
- `D[dst_id] = sum(S0[0..3], S1[0..3])` = **8 个 FP32 求和 → 1 标量**，写 D[dst_id]（表520 dst_id(2)）。latency 3。

### 数据通路（接线正解，hco 阻断 #2/#5 已解）
K=8 点积 = 1 FP16MUL（S0×S4→D0,D1）+ 1 ADDTREE8TO1（S0=D0,S1=D1→sum8）。FP16MUL 一指令 4 对 → 8 个 D → **4 条 ADDTREE8TO1 → 4 个 K=8 点积并行**。大 K = cumulative 跨 tile 累加（mirror 当前 matmul 自环 loop_carried）。

## Decision

### D1. 改正 master spec FP16MUL（阻断 #1，doc-first 第一步）
`aiacc_v2.4_master.yaml` 当前把 FP16MUL 建成 FPMUL 翻版（S0/S1 两输入 + micro=VALID/SRC_PREC/DST_PREC/smc_id_en），**错**。改成 v0.4.19：S0-S7 八源、D0-D7、latency 4（latency 本就 4 不变，结构改）、3 微指令。同步 `pipeline_delays.yaml` FP16MUL 2→4。

### D2. 扩 schema 支持「单元多微指令」（阻断 #3，hco 选项 b，**已实施 2026-06-04**）
**SCOPING 裁定（hco agentId a42a43da2f317e6a3）= (b) 必须建模 load 微指令。** 硬依据：FPMUL compute 直读 DVR（RtoVR live 总线，无 load 微指令）；**FP16MUL compute 读 `DR_FP16MUL_S*` 寄存器堆（非 DVR），该寄存器堆仅 load 微指令 valid 时从 DVR 写入** → RTOVR 只到 DVR、跨不过 DVR→DR 锁存 → option (a) 不成立。最小 emit 集 = `{compute, load_s4567}`（S4-S7 本地源必 load）；`load_s0123`（S0-S3 广播源）是否 emit 待 DR_S0123 驻留语义（NEEDS-USER-FACT #1）。

**实施（91 pass / 6 skip，4 个 8-SMC e2e 不破）**：
- `hw_spec.py`：加 `MicroGroup` 子模型 + `ModuleSpec.micro_groups: Optional[Dict]`（**绝不动 `code_bits:int` 签名**，纯 additive）；单微指令单元（30+ 现存）`micro_groups=None` 走顶层 code_fields，零影响。
- modules.yaml：加 FP16MUL（顶层 code_bits=8 镜像 compute 组 + `micro_groups` 载 compute(8)/load_s0123(7)/load_s4567(9)）。
- master：改正 FP16MUL micro（原 FPMUL copy-paste 的 SRC_PREC/DST_PREC→真 compute vld+src_0_id+src_1_id+smc_id_en；input_source S0-S7）。
- `pipeline_delays.yaml`：FP16MUL 2→4。
- payload_factory.build + packer.pack_capsule/get_unit_bits：加 `micro_variant` 参数选组（缺省→顶层，向后兼容）。
- 测试 `test_fp16mul_schema.py`（4 用例：3 组加载/variant 选组/packer 偏移/单微指令兼容）。
- **DEBT-FP16-SIMLOADER（未做，S1 还）**：第三条加载链 `sim/aiacc_hardware/src/spec_loader.py` 独立读 units yaml、不认 micro_groups dict —— 仅 sim 跑 FP16MUL 时（S1）需同步，本批不阻塞（sim FP16MUL 单元尚未建）。
- **latency 4 caveat**：user 裁定单元 latency=4（master/modules/pipeline_delays 已对齐）；hco 存疑「4 是否为 FP16MUL→addtree 通路延迟而非单元」——以 user 裁定为准，VCS 时复核（NEEDS-USER-FACT #2）。

### D3. 分阶段实施
| 阶段 | 内容 | 验收 |
|---|---|---|
| S0 | 本 ADR + D1 改正 master + D2 schema 决策 | hco APPROVE |
| S1 | 单 SMC 单 tile **K=8**（1 FP16MUL pair D0/D1 + 1 ADDTREE8TO1）、单份寄存器、连续打包、不 cumulative；sim 新单元 fp16mul + fp32addtree8to1；FP16 golden（np.float16 乘/float32 累加） | `--op fp16_matmul_min` e2e bit-perfect + TimingVerifier 无 Bank 冲突 + ≥1 可证伪单测 |
| S2 | 满 32 乘（8 D 全用 + 4 ADDTREE8TO1）+ 双缓冲建模 + **II/时序实测 vs FPMUL 基线**（收益落地点） | 时序对比报告 |
| **S2 ✅ 2026-06-04** | **`--op fp16_matmul_q` 满 32 乘 e2e bit-perfect（4 列 Q0-Q3 全对，II=10）。角色互换 + 激活走 SHIFTUP + FP16MUL producer-resident axiom 放宽。双缓冲 defer。** | **达成（hco 待审 axiom + modules.yaml inputs）** |
| S3 | 8-SMC 广播 mirror matmul_parallel_n 换 FP16MUL 通路 | 8/8 bit-perfect |
| S4 | VCS | — |

### S1 实施备注（2026-06-04 摸底，checkpoint，下次冷启动从此接）
**S1 不是 greenfield —— 有按旧错 spec 建的 sim 代码要重写。** 现状：
- `sim/smc_simulator/units/fp16_mul_unit.py`（`FP16MulUnit`，已注册 kernel.py:94/evaluate:492）是**旧错结构**：只建模 `regs_s0×regs_s1→D0`（D1-7 恒 0，get_outputs:88），kernel **只喂 s0-s3**（缺 s4-s7）。真结构 = S0-S7→D0-D7：`D0[x]=S0[x]*S4[x], D1[x]=S0[x+4]*S4[x+4]`…（4 对×8 lane=32 乘）。
- `sim/.../fp32addtree8to1_unit.py` 已部分实现（注释写"8 个 FP16 对"可疑，须核对 spec = 8 FP32→1）。
- `hw_config/specs/units/fp16mul.yaml`（sim SpecLoader 第三链读此，非 master）是旧 spec（S0/S1+SRC_PREC/DST_PREC），须改正（DEBT-FP16-SIMLOADER）。
- **精度 gotcha**：`fp_utils.fp16_mul_hw` 是 FP16×FP16→**FP16**；真 FP16MUL 是 FP16×FP16→**FP32**。须新写 `fp16_mul_to_fp32`（FP16 操作数转 FP32 再 fp32_mul_hw），且 **golden 须与之逐 bit 一致**否则 bit-perfect 挂。
- 硬编码 `/home/zhangjun/...` sys.path.append 在 ~10 sim unit（无害，现有 e2e 都过）。

**S1 子步**：S1-1 `fp16_mul_to_fp32`+重写 FP16MulUnit（32 乘）→ S1-2 核 fp32addtree8to1_unit → S1-3 kernel 喂 s4-s7+路由 D0-D7 → S1-4 DFG 算子 ManualFp16Matmul（load 微指令+compute+addtree8to1，参 matmul_op.py ManualMatmulQOperator）→ S1-5 FP16 golden（np.float16 乘/float32 累加，须对齐 sim 舍入）→ S1-6 op yaml+e2e。范式照搬 `matmul_op.py:180-285`（单 tile 无 cumulative）。

### S1 实施完成（2026-06-04，e2e bit-perfect，hco APPROVE-with-notes agentId abf2114e69920c671）
**`--op fp16_matmul_min --mode e2e` exit=0，Golden=71.37392 = SMC=71.37392，Diff=0.000000；98 pytest pass（+7 可证伪单测）；fp32 matmul e2e 未破。** 通路：a(8×FP16)→FP16MUL_S0、b(8×FP16)→FP16MUL_S4567 → FP16MUL 出 D0/D1 → addtree8to1 S0/S1 → 8 FP32 平衡树求和 → bank5 lane0。

落地件：
- **S1-1** `fp_utils.fp16_mul_to_fp32`（FP16×FP16→FP32 精确）+ 重写 `FP16MulUnit` 为真结构 S0-S7→D0-D7（32 乘，regs[0..7]）。
- **S1-2** 重写 `fp32addtree8to1_unit._reduce8`（旧错把 S0/S1 当 FP16 半字拼接 → 改 S0/S1 各 4×FP32、8 个 FP32 **平衡加法树**求和）。
- **S1-3** kernel 喂 s4（DVR_FP16MUL_S4567，S1 只用 S0×S4 一对）+ D0-D7 路由（已在）。
- **S1-4** `ManualFp16MatmulOperator`（matmul_op.py）+ 注册 `fp16_matmul_min`；DFG 用 dep_port="D0"/"D1" 选 FP16MUL 双输出源。
- **S1-5/6** golden（numpy float32 镜像 sim 求和序）；无 op yaml（手搭 operator，同 matmul_q）。

**4 处 hw_config 契约修复（hco 全 APPROVE，均为修 bug/正确建模，非行为变更）**：
1. **`vld`→`VALID`**（master + modules FP16MUL 全 micro 组）：payload_factory valid 注入按 `field.name.upper()=="VALID"` 匹配，`vld`→"VLD" 漏命中 → **compute VALID 位从未置 1，FP16MUL 永不触发**（emit body 缺 VALID、sim 解码 INVALID、D0/D1 恒 0）。重命名修复（bit 宽/encoding 不变，与 ADR-031 SMC_ID→smc_id_en 同性质命名归一）。**ADR-032 S0 宣称的「FPMUL/FP16MUL 固件 bit 级已验 smc_id_en=7」只验了 smc_id_en，漏验 vld → 本 bug 潜伏至 S1 e2e 才暴露。**
2. **FP16MUL inputs `S0123`→`S0`**：ucode_solver 按 inputs 位置序查 dvr_map 解 RTOVR 选通槽，dvr_map 键是 {S0..S3,S4567}，"S0123"(load 总线名) 无键 → rt_a 误落 slot0。改 compute 选通槽名 S0。
3. **sim 第三链 fp16mul.yaml**（**注：`sim/aiacc_hardware/specs` 是 symlink → hw_config/specs，第三链=同一文件**）：compute 微指令旧 FPMUL 翻版(VALID+SRC_PREC+DST_PREC=3bit)→ v0.4.19 真 compute(VALID+src_0_id+src_1_id+smc_id_en=8bit)；inputs 补 S0-S7。payload 宽 8 与 master 一致（防 sim decoder header 偏移）。**闭合 DEBT-FP16-SIMLOADER**。
4. **fp32addtree8to1.yaml latency 4→3**：ADR-031 漏改的第三链，同步到权威值 3（modules+pipeline_delays）。

**DEBT-FP16-AUDITOR**：rigid auditor 的 actual 值解码硬编码 FP32（auditor.py `struct.unpack('>f')`+LSB32），无法解码 FP16 输入/混合中间值 → 对 fp16 路径**系统性假阳性**。已在 `audit_truth_alignment` 顶部对 element_bits=16 路径**显式跳过 + 大声告警**（非静默），正确性由最终 golden parity（bit-perfect）背书。S2+ 若要逐-op 审计需让 auditor 按 element_bits 解 lane。

### S2 实施完成（2026-06-04，满 32 乘 e2e bit-perfect，hco 待审）
**`--op fp16_matmul_q --mode e2e` exit=0，4 列 Q0=71.37/Q1=66.18/Q2=84.16/Q3=79.02 全 Diff=0；II=10；100 pytest pass（+3 可证伪单测）；matmul/matmul_q2/fp16_matmul_min 无回归。** `ManualFp16MatmulQOperator`（K=8, COLS=4）：1 个 FP16MUL 指令算全 **32 乘** → 8 个 D → 4 条 FP32ADDTREE8TO1（dst_id 0-3，链化串行于单元 iq28）→ bank5 稠密 [Q0..Q3]。

**关键设计（用户裁定：角色互换 + 双缓冲 defer）**：
- **角色互换**：4 个不同权重列 → S0-S3（各独立 DVR 端口），广播激活 → S4-S7（共享 S4567 单端口，kernel 喂 s4=s5=s6=s7 同值）。`D{2c}/D{2c+1}=S{c}×S{c+4}=w_c⊙act` → Q[c]=dot(w_c,act)。点积可交换 → 与硬件钦定布局（激活广播 S0-S3 / 权重本地 S4-S7）数学等价。
- **激活走 SHIFTUP**（COLS=4 必需）：满 32 乘需 4 权重 + 1 激活 = 5 个 UR read，但 FP16MUL **strict latch 强制全输入同拍 read** + 仅 4 个 UR read 端口 → 直读必 UNSAT。激活经 `_inject_shiftup_broadcast`（resident SHIFTUP_D）**解耦其 read 时序**（早拍读 ur-r0），4 权重在 compute-1 同拍各占 1 端口/1 bank（**权重必放互异 bank** b0-b3→bank1-4，否则同 bank 同拍 read = Bank 冲突 UNSAT，实测 unsat-core 证）。
- **FP16MUL producer-resident axiom 放宽（`dependency_axioms.py`）**：FP16MUL 的 D0-D7 在 DR 寄存器堆**常驻**（spec「每源 2 份」，同 DR_SHIFTUP_D 性质）。满 32 乘 = 8 个 RTOVR 读同一 FP16MUL，4 个抢 addtree8to1 的 S0 端口、4 个抢 S1 → 须**错拍采样常驻 D**。原 strict `t_latch==prod.t+lat` 钉死同拍 → 端口冲突必 UNSAT。故把 `prod_is_bcast_shiftup` 推广为 `prod_is_resident`（+ FP16MUL），放宽为驻留窗口区间 `[prod.t+lat, prod.t+lat+II-1]`（**复用 ADR-028 同款机制**，仅加 FP16MUL 一种 producer，不动 SHIFTUP 既有逻辑；matmul/matmul_q2 无回归）。
- **4 addtree 链化**：4 条 tree 分写同一输出寄存器 4 lane（部分 lane 累积）。SMT 调度序 ≠ 循环序 → 若 rt_out 只依赖某一条 tree，其余 lane 可能在读后才写（实测 Q2=0）。加 tree 间顺序依赖链 → rt_out 依赖末条即读全 lane 齐。代价：II 4→10（addtree 串行 latency 主导）。

**时序对比报告（II/吞吐 vs FPMUL 基线 matmul_q2）**：
| 指标 | fp16_matmul_q（本通路） | matmul_q2（FPMUL 基线） | 说明 |
|---|---|---|---|
| 乘法指令数（产 4 列） | **1 FP16MUL** | 4 FPMUL | FP16MUL 一指令 32 乘 |
| **MAC / 乘法指令** | **32**（4 列×8 lane） | 4（4 FP32 lane） | **8× MAC 密度**（ADR 钦定收益） |
| 收缩维 K | 8 | 4 | FP16 一对源即 K=8 |
| reduce 单元 latency | ADDTREE8TO1 = 3 | ADDTREE5TO1 = 4 | 8to1 reduce 更快 |
| II | 10 | 6 | fp16 II 高 = 4 条 addtree **链化串行**主导 |
| sim 总 cycle | 91 | 87 | 相当（fp16 K 翻倍 + 8× MAC 密度） |
| #DFG ops | 26 | 37 | fp16 省去 temp-bank 重定时（D 常驻直喂 addtree） |

**结论**：FP16MUL 在乘法阶段达成 **8× MAC/指令**（32 vs 4），坐实 ADR-032 吞吐目标；省去 matmul_q2 的 temp-bank 往返（D 输出常驻直喂 addtree）。当前端到端 II=10 受 **4 条 ADDTREE8TO1 链化串行**主导（非乘法瓶颈）—— 收益落在乘法密度，reduce 串行是下一优化点（并行 reduce / 双缓冲，**DEBT-FP16-DBLBUF / DEBT-FP16-ADDTREE-SERIAL** defer）。

**S2 新增/确认债务**：
- **DEBT-FP16-ADDTREE-SERIAL**：4 条 addtree 链化串行（II=10）为正确性（输出 lane 收集）所需的保守解；并行 reduce 或多通道一拍收集可降 II。
- **DEBT-FP16-DBLBUF**（defer，用户裁定）：寄存器每源 2 份 src_*_id ping-pong 支撑 II=1，需 NEEDS-USER-FACT #4（三微指令发射时序）。
- **NEEDS-USER-FACT #7（角色互换前提）**：硬件 S0-S3 能否装 4 个**不同**值（load_s0123 仅 src3210_high_id 1bit，可能广播组加载）。若 S0-S3 只能广播加载，则角色互换不成立、须走 faithful S4-S7 本地路径（阻塞于微指令消歧 fact，DEBT-FP16-LOADDECODE）。**当前 sim 对 S0-S3 按 4 独立寄存器建模（kernel 分端口喂），VCS 须验。**
- **hco 审：APPROVE-with-notes（agentId a73026e9e1224f650，proposed 维持不升 Accepted）**：`modules.yaml` FP16MUL inputs 扩 `[S0,S4567,S1,S2,S3]`（加法式）+ `dependency_axioms.py` producer-resident 推广含 FP16MUL（axiom 变更）。两处软件/SMT 层逻辑自洽（纯可行域扩大 + positional 纯索引）、SHIFTUP 零回归。hco 补登债务（见下）+ 要求加 FP16MUL resident SMT 反证测试（已加 `test_smt_adversarial.py`）。
- **DEBT-032-RESIDENT-TIMING**：producer-resident 放宽的「驻留窗口 = prod.t+lat..+II-1」是 sim/SMT 假设，无 RTL broadcast/resident 流式时序表（同源 DEBT-023-BCAST-TIMING / DEBT-028）。VCS 跑出 8 RTOVR 各采正确 D 前不得升 Accepted。
- **DEBT-032-ADDR-INTERCHANGE**：S2 角色互换 sim 通路 bit-perfect 不保 RTL 寻址一致，绑定 NEEDS-USER-FACT #7。
- **NEEDS-USER-FACT-032-D-RESIDENT（hco 标，升 Accepted 阻断）**：FP16MUL D0-D7 是否在 DR 寄存器堆**常驻至下次 compute**、支持驻留窗口内多 RTOVR 错拍读？放宽的唯一物理前提——spec「每源 2 份」描述的是**输入 source 寄存器双缓冲**，外推到**输出 D 常驻可错拍读**是跨字段推断，RTL 无 D 输出驻留时序表佐证。若 RTL 是 D 输出 1-拍瞬态 → 放宽产 RTL 不可实现调度 → VCS 必挂。

## Consequences
- **正面**：~8× 乘法吞吐（32 vs 4 MAC/指令）+ ADDTREE8TO1 latency 3 vs ADDTREE5TO1 4 → cumulative rec-MII 4→3；对齐硬件钦定的「加法树 matmul」路径（V1.0 不支持 MAC matmul）。
- **精度**：FP16 乘/FP32 累加，非 FP32-exact，仅 FP16 推理适用（user 已定目标）。
- **债务**：DEBT-FP16-DBLBUF（S1 单份寄存器，S2 还双缓冲）/ DEBT-FP16-LAYOUT（S1 连续打包，S2+ 转置间插需表78 B 因子）/ DEBT-FP16-MULTITILE（S2+ 多 tile cumulative；**含 S2 满 32 乘补 inputs S1/S2/S3 端口时复核 ucode_solver positional 对齐**）/ ~~DEBT-FP16-SCHEMA~~（未走逃生门）/ ~~DEBT-FP16-SIMLOADER~~（S1 闭合）。
- **S1 新增债务（hco abf2114e69920c671 要求显式落账）**：
  - **DEBT-FP16-LOADDECODE**：sim 不解码 load_s0123(7bit)/load_s4567(9bit) 微指令——sim 用 RtoVR 数据总线直接锁存 regs[0..7]，load 微指令的 src*_high/low_id/smc_id_en 字段在 sim **完全旁路**。e2e 对 load 微指令字段正确性增信为零；VCS 须验 low_id/high_id 路由。
  - **DEBT-FP16-ADDTREE-ORDER**：FP32ADDTREE8TO1 的 8 输入求和顺序用**平衡加法树**（依 "AddTree" 命名假设，无 RTL C 参考）。golden 镜像同序 → e2e bit-perfect 是 **sim-vs-golden 自洽，对 VCS 忠实性增信近零**（FP32 加法非结合，不同拓扑产不同 LSB）。**不可据此升 Accepted。**
  - **DEBT-FP16-AUDITOR**：见上（rigid auditor FP32-only 解码，fp16 路径显式跳过）。
- **NEEDS-USER-FACT 残留**：#4 三微指令 load↔compute 发射时序 / IQ 占用 / overlap（II 下界，阻塞 S2 吞吐）；**#5（S1 新增）RTL 表520 或 RTL 是否钦定 addtree8to1 8 输入归约拓扑（平衡树 vs 链式 vs 其他配对）——决定 DEBT-FP16-ADDTREE-ORDER 是否需改序**；**#6 确认 FP16MUL latency=4 是单元 pipeline depth 而非整条 FP16MUL→FP32 通路延迟（与 FP32ADDTREE8TO1=3 串联时勿重复计延迟）**。

## 关联
- hco 评审（agentId a768d375631d89661）：方向 APPROVE，3 阻断（master 错/接线/schema）+ NEEDS-USER-FACT；S1 最小范围建议本 ADR D3 采纳。
- user RTL 裁定：目标精度 FP16 推理（2026-06-04）；FP16MUL latency=4（2026-06-04）。
- 设计文档 v0.4.19 表512-520/78/79/711；提取工具 `verification_v2/tools/spec_doc_align.py`（注：仅抓「上行微指令表」，表78/79 功能表抓不到，本 ADR 接线靠手读 docx XML 补）。
