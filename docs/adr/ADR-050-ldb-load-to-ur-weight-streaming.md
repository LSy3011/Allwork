---
adr_id: ADR-050
title: LDB Load-to-UR 权重流式编译器建模（外部 HBM → LDB(AXI) → UR）—— 阶段 1 单-SMC 连续 tile
decided: 2026-07-02
last_verified: 2026-07-02
still_holds: yes（阶段 1 VCS 贯通 2026-07-02：HBM→LDB(AXI)→UR→K16 matmul，bank5 lane0=155.14395 bit-match golden + 负向断言 dummy HBM→-2689.73 铁证。commit bf11012）
builds_on: ADR-022, ADR-023, ADR-031, ADR-008, ADR-025, ADR-049
hco_review: hco 定 + 放行（2026-07-02，RTL 行级取证 ldb_ur_axi_conv.v/smi_iq.v/user_register.v/tb_aiacc_top_v04_f.v；2 阻断项 + 2 待 user 决断）
user_decision: user 2026-07-02「数据的加载在架构中，就是通过 load store 来进行的」+「直接攻 load/store 权重流式」
---

# ADR-050 · LDB Load-to-UR 权重流式（阶段 1 单-SMC 连续 tile）

> **一句话**：编译器至今零「外部内存→UR」load 路径（只有 UR 内 UR_READ/UR_WRITE）。真大 K/全矩阵 matmul 权重
> 8–33MB ≫ 32KB/SMC UR，必须从外部 HBM 经 **LDB（Load Buffer，AXI 主口读 HBM → 写 UR）** 流式载入。本 ADR
> 据 hco RTL 取证建 LDB 编译器模型，阶段 1 交付单-SMC 连续 tile load→matmul VCS bit-match。

## Context（hco RTL 行级取证）

- RTL data_engine 完整：`data_engine/stb_ldb/ldb/`（LDB load）、`stb/`（STB store）、`ds/`（UR↔UR shuffle）、`DW_axi_gm`（AXI 主口）。
- VCS 可行：`tb_aiacc_top_v04_f.v:1400-1459` per-SMC HBM SRAM（`ae350_ram_subsystem`/`ram65536x128`=1MB），`$readmemh(g_hbm_init_path[smc], mem)` 预载 → **可灌权重、VCS bit-match**。
- LDB 是**变延迟 DMA + done 握手**（UR 写只在 AXI 数据返回后发生，`ldb_ur_axi_conv.v:1421-1451`）——**不是固定流水 latency**，现有 add_latency_axiom（读 spec.delays 常数）不适用。OUTSTANDING=4。

### LDB CRU 两流字段（RTL 行级，`ldb_ur_axi_conv.v:271-303` + `smi_iq_cru_widths.vh:104-113`）
**流 A `cru_ldb`（56b，硬件 iq_inst[26]，UR 写侧）**：[0]valid · [15:1]ur_addr(15b 字节地址,bank=[14:12]/depth=[11:4]/byteoff=[3:0]) · [23:16]ur_wr_line_cnt(8b) · [32:24]line_byte_cnt(9b) · [38:33]line_stride(6b,字节步=<<4) · [44:39]const(6b) · [45]pre_fill · [46]post_fill · [50:47]inst_id(4b) · [55:51]smc_id_en(5b)。

**流 B `cru_ldbaxi`（104b，硬件 iq_inst[27]，AXI 读侧）**：[0]valid · [4:1]first_byte_strb · [8:5]last_byte_strb · [12:9]brst(4b,→arlen) · [52:13]gr_base_addr(40b,HBM 字节基址) · [92:53]gr_intlv_addr(40b,per-SMC 交织步长) · [96:93]inst_id · [101:97]smc_id_en · [103:102]tid。AXI 固定 msize=16B/burst=INCR。

**握手**：crd_ldb(6b:valid/done/inst_id) + crd_ldbaxi(7b:valid/done/inst_id/err)。两流须**同时发射**（ldbaxi 取数、ldb 写 UR 消费之）。

### TB IQ 名→槽集成表（`aiacc_rtl/generate_data_list_coverage.py` IQ_LAYOUT_MAP，权威）
- **slot 26 iq_name="LDB"** bit_len=106（=56b CRU body + ~50b IQ header）byte[228:241]
- **slot 27 iq_name="LDB-AXI"** bit_len=154（=104b CRU body + ~50b header）byte[242:261]
- （对照 slot 24 STB bit_len=100 / 25 STB-AXI 152 / 92 DS 80 / 93 SMI_DS_T 46）
- RTL 侧 `smi_iq.v` PARA_IQ_ID 26/27 = LDB/LDB-AXI，`smi_top.v:677-678` `o_cru_ldb_r`/`o_cru_ldb_axi_r`，dispatcher 地址范围 iq_hit[26/27]。
- **emit 集成**：编译器出 `iq_ldb.hex`（name "ldb"）+ `iq_ldb-axi.hex`（name "ldb-axi"），TB 按 iq_name 归位槽 26/27（同 fp16mul-s0123/s4567 '-'→'_' 归一化范式，复用 micro_variant 分桶机制）。IQ header ~50b（含 EN_LOOPS/DLY 等，LSU 比 compute 单元多）。

### AXI→HBM 映射（`ldb_ur_axi_conv.v:390-424` + `ae350_ram_subsystem.v:68-71`）
`araddr = gr_base_addr + smc_id × gr_intlv_addr`；per-SMC **独立** 1MB HBM；mem 索引 = `araddr>>4`（16B/line）；**hex 第 k 行 = 字节地址 k×16**。单-SMC(SMC0) gr_intlv 无关、araddr=gr_base。

## Decision

### D1：新增 LDB / LDBAXI 单元（hw_config 三文件 + ResourceType + IQ 映射）
- modules.yaml + specs/units + master：`LDB`（iq_inst[26], 56b, 字段如上）、`LDBAXI`（iq_inst[27], 104b）。
- `src/driver/hw_resource.py` 加 ResourceType LDB/LDBAXI；hw_constants IQ base 26/27。
- 🔴 **阻断项修**：编译器 iq_registry 现 `STB:7`（与硬件 iq_inst[24] 不符）且无 LDB → 建 logical→iq_inst[26/27] 映射，否则 emit 落错 slot X 掉。LDB 两流分桶 emit（同 fp16mul-s0123/s4567 范式）：`iq_ldb.hex` + `iq_ldbaxi.hex`。

### D2：LDB 变延迟 → 阶段 1 用「保守上界 latency + 显式相位隔离」（user 拍板默认）
LDB 无固定 latency（DMA+done）。阶段 1 **不建模握手事件**，改用：LDB 排在 compute 前、显式约束 **LDB done 后 compute 才读同 bank**（保守上界拍数 = HBM round-trip 上界，emit 侧垫够）。标 **DEBT-050-VARLAT**（阶段 2 换 done-握手事件建模）。契合 ADR-040「时序确定性」——阶段 1 用相位隔离换确定性，非逐拍标定。

### D3：单端口 bank 冲突（ADR-008 延伸）→ 阶段 1 相位隔离绕开
`user_register.v:337-373` UR bank 单端口，LDB 写口与 compute 读口同 cycle 访同 bank → CRU 访问被**静默丢弃**（硬件不仲裁、靠软件保证）。阶段 1 靠 D2 相位隔离（LDB 全写完再 compute）绕开；**阶段 2 并发时必须新增 SMT axiom**（LDB-write ↔ same-bank compute-access 同 cycle 互斥）+ 反证测试。

### D4：emit HBM 预载 hex
权重 tile 按 128b/line 打包写 `memory_init0.hex`（SMC0），line k ↔ byte k×16；LDB gr_base_addr=0（小 tile）。VCS 经 g_hbm_init_path[0] 预载，无需改 TB。

## 增量分期
- **阶段 1（本 ADR）**：单-SMC、16B 对齐、连续 tile（K=16 fp16=32B=2 line）、无 fill（pre/post=0, strb=0 全有效）、无交织、LDB→bank1→复用 ManualFp16MatmulK16Operator→VCS bank5 bit-match。含负向断言（不预载→bank5 非期望，防假绿）。
- **阶段 2（deferred）**：2D stride/line 语义、pre/post fill、多-tile 大 K、per-SMC 交织(gr_intlv)、并发 LDB+compute（端口冲突 axiom）、done-握手变延迟建模。

## NEEDS-USER-FACT（升 Accepted 逐条闭合）
- 050-URADDR：15b ur_addr→(bank,depth) 解码是否 ASIC 权威（RTL 读出 bank=[14:12]/depth=[11:4]，非仅 FPGA-IP 分支）。
- 050-LINE2D：ur_wr_line_cnt/line_byte_cnt/line_stride 的 2D tile 语义（阶段 2）。
- 050-FILL：const/pre_fill/post_fill 编码表。
- 050-STRB：first/last_byte_strb burst 边界语义。
- 050-PORT：端口共享是纯软件保证还是硬件 stall/仲裁（决定阶段 2 硬 axiom vs stall 建模）。

## 风险
- URA vs BRAM_PORTA：LDB 在 ASIC 走哪个 UR 写通道（BRAM_PORTA 注释标「仅 FPGA-IP 初始化」）→ **VCS 首跑前逐字确认**。
- AXI 突发对齐：小 tile 务必 16B 对齐、strb 全有效，否则 <16B fill 分支介入改数据。
- axism/DS 反压：LDB 数据路径不经 DS（连续 load），但 VCS 首跑确认 axism 未被反压。

## 阶段 1 实现清单（契约已全闭合，可直接实现）
- [x] **1. hw_config**（commit c4e4bca）：modules.yaml 加 `LDB`（56b,10 字段）+ `LDB_AXI`（104b,9 字段），字段位和==code_bits 校验过。sim 不建模 LDB（同 q_dst 盲，VCS 为准）。
- [x] **2. iq_registry**（c4e4bca）：LDB:50 / LDB_AXI:51。⚠ **config 的 system_core 取自 master yaml（hw_spec.py:186 master 优先）→ master + system_core 两处都改**。get_iq_name 加 LDB/LDB_AXI 单名特例 → assembler '_'→'-' → iq_ldb.hex/iq_ldb-axi.hex → TB slot 26/27。
- [x] **3. ResourceType**（c4e4bca）：加 LDB / LDB_AXI。
- [x] **4. payload_factory**（免）：全动态按 code_fields 从 op.metadata 取值，VALID/smc_id_en 自动。emit 分桶天然（get_iq_name→assembler→iq_{name}.hex），无需 micro_variant。**只要 DFG op 带对字段名即可，无需改 payload_factory。**
- [ ] **4b. LSU IQ header 变体（hco RTL 取证已解，`smi_iq.v:58,86`）**：LSU（slot 24/25/26/27）header=**50bit**，与 compute 41bit **唯一差异 = INC 字段 4→13bit（+9）**，其上字段 offset 整体 +9、无新增字段。LSU header 逐字段（offset:width）：INC_EMBED 0:6 / **INC 6:13** / DLY 19:3 / COND 22:7 / LOOPS 29:10 / EMBED_END 39:6 / EMBED_LOOPS 45:3 / JUMP 48:1 / VLD 49:1 = 50。packer 需按 unit_type 选 header 变体（compute 41 / LSU 50）。阶段 1 单次搬运：INC/INC_EMBED/LOOPS/EMBED*/COND 全 0，VLD=1，body 各字段填值。⚠ modules.yaml STB stub=7bit 是错的（真 body=50，CRU_STB_W=50）——阶段 1 不实现 STB 可留 DEBT-050-STB-STUB。无 NEEDS-USER-FACT（三方 bit_len 吻合）。
- [ ] **5. DFG/frontend**：加 2 op —— `ldb_axi_op`(LDB_AXI, gr_base_addr=0/brst=lines) + `ldb_op`(LDB, ur_addr=bank1 base/ur_wr_line_cnt=K//8) deps=[ldb_axi_op]；`b_read` deps 加 `ldb_op`（RAW，权重 ready 后读）。LDB 一次性（inner_loops=0）填 bank1[0..K//8-1]，compute 循环随后。⚠ 变延迟 → 相位隔离（保守大 latency 或 DFG 边强制 LDB 先于 b_read），非精确 add_latency_axiom。
- [ ] **6. HBM hex 生成**：weight tile 按 128b/line（8 fp16/line）打包 `memory_init0.hex`（line k↔byte k×16）；放 aiacc_rtl/data_engine/sram/ 或 g_hbm_init_path 指向。
- [x] **7. 算子 + harness**（commit 02b0f0e/27034af）：ManualFp16MatmulK16LdbOperator（fp16_matmul_k16_ldb，bank1 不 inject + prepare_hbm_hex + 复用 K16 matmul）；VCS harness = 权重写 aiacc_rtl/data_engine/sram/memory_init1.hex（SMC0=gsi+1，tb:2087）+ cp build 到 test_atomic_ops。

## VCS 调试进度（2026-07-02，阶段 1 未完）
**读路径全链已硅证 ✓（最难的 AXI/NOC/HBM 连通性）**：
- HBM 预载 ✓（`[HBM] smc0 <- memory_init1.hex`，mem[0/1]=权重）。
- 修 2 bug（commit 27034af）：① resolve_iq_type 前缀吞并（ldb-axi 误当 LDB 56b→修精确匹配优先，iq_ldb-axi.hex 现 154bit✓）；② 相位隔离（pipeline_delays LDB=40 保守上界，b_read@t43）。
- `o_cru_ldb_axi_r` VALID=1@cyc1912 → `ldb_arvalid`@1915 → **`ldb_rvalid`@1920（HBM 数据返回）** ✓✓。

✅ **阶段 1 VCS 贯通（2026-07-02，commit bf11012）**：
- 写侧收尾两处（DFG）：① **两 CRU 同拍发射**（去 ldb→ldb_axi dep，均无依赖 → SMT 同排 t=0；错开时写指令随 latency 被挤出 fetch）；② **line_byte_cnt 是总字节数**（非每行）——16 时 beat1 塌成 we=0x0001，设 16×lines → we=0xffff 全写（VCS 实证，闭合 NEEDS-USER-FACT-050-LINE2D 连续-tile 情形）。
- 全链硅证：HBM 预载 → o_cru_ldb_axi_r/o_cru_ldb_r VALID@同拍 → ldb_arvalid → ldb_rvalid(HBM 数据) → ur_we=0xffff×2 写满 bank1 → K16 matmul → **bank5 lane0=155.14395 bit-match golden** ✓。
- 负向断言：dummy HBM → bank5=-2689.73 + bank1=dummy → 铁证权重来自 LDB/HBM 非残留。
- 已验 NEEDS-USER-FACT：URADDR(bank=[14:12] user 确认)、LINE2D(line_byte_cnt=总字节, VCS 证)、STRB(first/last_byte_strb=0 对连续对齐 tile 即全字节, VCS 证)、PORT(相位隔离绕开)。仍 deferred: FILL/多-tile 交织/并发(阶段 2)。

## 阶段 2 进度
### ✅ 多-tile 大 K 单 LDB（2026-07-02，commit 58a4f3f）
`ManualFp16MatmulK128LdbOperator`（K=128=16 tile）单条 LDB 载 16 line（line_byte_cnt=256）→ matmul 累加 16 tile → **VCS bank5=1246.8252 bit-match**。证 LDB 多-tile 扩展。**单 LDB 上限 K≤248**（line_byte_cnt 9bit=511B=31 line）。

### 🟡 循环 LDB（K>248 / 权重≫UR 流式）——契约已全取证（hco 第3轮），剩 HBM 同步策略 user 决断
单 LDB 装不下 K>248 → **循环 LDB + 驻留 buffer** 流式（O(1) UR）：loop num_tiles，每迭代 LDB 载 tile_i(1 line) 进 bank1[0] 驻留 buffer、matmul 读+累加，下迭代覆盖。
**INC 契约（hco RTL 行级取证 smi_iq_iic.v:228,236,256）**：`micro_addr = min_base_addr + INC×loop_cnt`（同域直加、无缩放）；gr_base_addr 无缩放直连 AXI 字节地址（ldb_ur_axi_conv.v:295→393→893）→ **每迭代 +16B ⇒ INC=16**。INC 13bit 带符号(packer LSU 已 [6:13])。**INC_EMBED(body)=0x01**(bit0 选 level-1；HDR 的被 RTL 忽略)。**LOOPS=num_tiles-1**(10bit 上限 num_tiles≤1024=K≤8192)。
| CRU | stride(meta) | HDR.INC | body.INC_EMBED | LOOPS |
|---|---|---|---|---|
| LDB_AXI(51) 步 gr_base | 16 | 16 | 0x01 | num_tiles-1 |
| LDB(50) 步 ur_addr(驻留) | 0 | 0 | 0 | num_tiles-1 |
**实现三步**：① assembler.py:94 `is_memory_iq` 加 `"ldb"` 子串匹配即够（复用 UR level-1 embed-loop，已 VCS 证明；body op_mask=0x1 when stride!=0 → LDB_AXI 开、LDB 关；`hdr_inc_embed=max(stride,1)` 对 stride=16 语义错但 RTL 忽略 HDR inc_embed → 无害，建议显式写 0x1）。② DFG：LDB/LDB_AXI inner_loops=num_tiles-1、stride 如上、b_read 驻留读 bank1[0]。③ **SMT 加 WAR 边 `LDB_{i+1}←compute_i`**（单驻留 buffer 强制逐 tile 串行，outstanding=4 不能 pipeline）。
🔴 **HBM 同步策略（决策 (a)，AI 自决 2026-07-02，user 暂离；同 stage-1 bring-up 套路 + hco 推荐序）**：HBM/AXI 读延迟**非编译期固定**。**首期 (a) 保守大固定 II bring-up**（覆盖 HBM 往返上界 + WAR 串行）过 VCS bit-match，标 **DEBT-050-VARLAT「非硅安全」**；**升 Accepted 前必转 (b) done-握手事件建模**（COND/crd，硅安全）。hco 判：HBM 往返是「时序要算不要调」的**合法例外**（该用握手非算拍），(a)→(b) 序成立。user 回来可 override 到直接 (b)。
载体：K32-stream（4 tile 驻留 1-line buffer）。

### 🟠 循环 LDB 实现进度（2026-07-02，commit 800b482，emit 正确/VCS 执行 bug 待续）
**三步已落**：①assembler is_memory_iq 加 "ldb"（pytest 186 安全，单-shot 不受影响）②K16 get_dfg 加 EMIT_LDB_STREAM 分支（LDB_AXI stride=16/LDB stride=0 驻留/b_read stride=0 驻留/两 CRU 同拍均挂 WAR loop-carried←b_read:8/LDB inner_loops 保留）③ManualFp16MatmulK32StreamLdbOperator（fp16_matmul_k32_stream_ldb）。
**emit 验证正确**：iq_ldb-axi HDR LOOPS=3+INC=16、body INC_EMBED=0x01；iq_ldb HDR LOOPS=3+ur_addr=4096 驻留；两 CRU 同拍 t=10；II=48（WAR 强制）。
🔴 **VCS 执行 bug 根因定位（波形，2026-07-02）**：`iq_inst_vld` **bit26(LDB)/bit27(LDBAXI) 只在起始 2 拍(cyc15065-67=INIT+HDR)置高、之后不 replay**；bit2(fp16mul)10 次、bit17(ur-r1/b_read)9 次贯穿循环。→ **LDB IQ 处理完 INIT+HDR 就停、不 replay kernel body**（body slot10 从不 fetch → o_cru_ldb 恒 0 → bank1=0）。
**关键对照**：ldb 与 ur-r1 hex **结构完全相同**（INIT/HDR LOOPS=3/EMBED_LOOPS=1/EMBED_END@line49，仅 body slot 12 vs 4），但 ur-r1 循环正常、ldb 不循环（ldb INIT LOOPS=0 skip=0 vs ur-r1 INIT LOOPS=48）。→ **SMI 对 LSU IQ(slot 24-27) 的 loop-replay 机制 ≠ UR/compute IQ**（同结构不同行为）。这解释单-shot(LOOPS=0 无需 replay)能发、循环(LOOPS=3 需 replay)不发。
**下一步 = hco 第 4 轮**：取证 smi_iq LSU IQ(24-27) 的 loop/EMBED replay 机制。

### ✅ hco 第 4 轮根因（2026-07-02，RTL 行级）+ 方案 z
**根因非 emit、在 RTL**：LSU IQ(24-27) 走 `smi_iq_dly_queen.v:148` 的 **outstanding/done 信用门控发射变体**（4 深 `r_executing_inst_cnt`：每发射++（含 NOP replay）、只有真 LDB 回 `crd_i[4]=done` 才--）。循环 kernel 的 NOP replay 吃信用不还 → 信用耗尽 → `dly_queen_afull=1` → `smi_iq_iic` 所有 loop-replay 分支被 `~afull` 冻结 → body 永不 fetch。UR/compute IQ 走 free-run 无门控变体（crd_i=0）故循环正常。单-shot 能发=发射条数≤4。iic 循环状态机 LSU/compute **无差异**（分叉全来自 dly_queen 变体）。COND 否决（EN_COND=0 编译期常量）、INIT stagger 无关。
**方案 z（hco 推荐）**：unrolled 多条单-shot LDB（每 tile LOOPS=0，绕 replay 通路、每条真 LDB 回 done 自 drain 信用）。已落码（EMIT_LDB_STREAM 改 unrolled，commit 见 git）。
🟠 **VCS 未通（诊断完整，DEBT-050-UNROLL）**：波形追 4 条 ldb done 时序定位——**LDB_AXI FSM 单描述符寄存器**：背靠背发射（o_cru_ldb_axi 连续拍 1912/13/14）被覆盖 → 只**末条**发 AXI 读（arvalid 1 次 @1915→rvalid@1920→写 bank1[末]@1927）→ 只 1 tile 写、bank5 部分。
**根本约束冲突（新发现）**：unrolled 多-LDB 需**同时**满足：(a) tile 内 ldb_axi_i/ldb_i **同拍**（stage-1 证：错拍→写 FSM 不 latch→bank1=0）；(b) tile 间**间隔**（LDB_AXI 单描述符 reg，背靠背覆盖）。当前 dep 机制**难兼得**：无 dep → 满足(a)违(b)（4 条同排背靠背→只末条写）；串行 dep → 满足(b)违(a)（ldb_axi/ldb 被拉不同拍→bank1 全空）。
**修向（DEBT-050-UNROLL，已试 3 法皆未通）**：
- ① no-dep：满足同拍、背靠背覆盖 → 只 bank1[末] 写（bank5 部分 80.14）。
- ② 串行 dep（latency 40）：满足间隔、破 ldb_axi/ldb 同拍（拉到不同拍）→ bank1 全空（bank5=0）。
- ③ **修向(x) per-op DLY**（用户要求，assembler 已加 per-op DLY infra，body 读 op.metadata['dly']）：LDB DLY=3 正确 emit 但 **VCS 结果不变**（仍只 bank1[末]）——DLY 延迟指令**效果**、非 SMI 对下条 fetch/描述符到达间隔，LDB_AXI 单描述符仍背靠背覆盖。
**下一步**：wave-trace DLY 实际效果（确认为何不 spacing）/ RTL done-COND 串行（EN_COND 编译期常量=0，需改 RTL）/ 大 II 使各 LDB 落独立 fetch 窗（需 compute 也大 II 或 LDB 独立 IQ 调度）。per-op DLY assembler 机制已留 infra（默认 0 无副作用）。
**对齐理解**：`outstanding=4` = 4 条在途上限（信用），非总数封顶（done 回流可发>4、节流非封顶）；LDB 单描述符 reg 才是背靠背覆盖的真因。
**DEBT-050-LSU-LOOP**：真 O(1) UR 循环 LDB 流式需 RTL 债务解（信用不计 NOP / iq_II=1 消 NOP）。当前 unrolled 多-LDB 是 UR-bounded（N line，K≤~1 bank）非 O(1)。

### 其余阶段 2（deferred）
2D stride/line 语义、pre/post fill、per-SMC 交织(gr_intlv 真跨-SMC 分散)、并发 LDB+compute(端口冲突 SMT axiom, ADR-008 延伸)、LDB done-握手变延迟建模。

## 验收（阶段 1 升 Accepted）
- [ ] VCS：SMC0 预载 K=16 tile 进 HBM → LDB load bank1 → matmul → bank5 bit-match golden + 负向断言（不预载→变）。
- [ ] modules/ResourceType/IQ 映射三处一致；pytest 无回归。
- [ ] NEEDS-USER-FACT-050-URADDR / -PORT 至少这两条确认（阶段 1 直接依赖）。
