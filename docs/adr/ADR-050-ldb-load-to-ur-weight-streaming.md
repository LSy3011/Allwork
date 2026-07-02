---
adr_id: ADR-050
title: LDB Load-to-UR 权重流式编译器建模（外部 HBM → LDB(AXI) → UR）—— 阶段 1 单-SMC 连续 tile
decided: 2026-07-02
last_verified: 2026-07-02
still_holds: proposed（阶段 1 待 VCS：SMC0 预载 HBM tile → LDB load → matmul bank5 bit-match；NEEDS-USER-FACT-050-* 逐条闭合）
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
- [ ] 🔴 **4b. LSU IQ header gap（新 blocker，须 RTL 取证）**：编译器 IQ header 固定 41bit（`packer.py:4 ISA_V5_HEADER`，VLD@bit40，全局锁死）。TB IQ_LAYOUT_MAP 期望 **LDB=106bit=50 header+56 CRU**、**LDB-AXI=154=50+104**（LSU header=50bit ≠ compute 41bit，多 9bit）。验证：FP16MUL 41+8=49✓/SHIFTUP 41+135=176✓/UR_READ 41+17=58✓ 全对，但 LSU 差 9bit。**须 RTL 取证 LSU 50bit header 的精确字段组成**（哪 9 bit：loop/dly/inc/cond 差异？STB 同理 stub 7bit vs 真 body），再定 packer 出 LSU header 变体。**猜错=VCS bit 错位。下一步先解此 gap（疑另一轮 hco / smi_iq LSU header RTL）。**
- [ ] **5. DFG/frontend**：加 2 op —— `ldb_axi_op`(LDB_AXI, gr_base_addr=0/brst=lines) + `ldb_op`(LDB, ur_addr=bank1 base/ur_wr_line_cnt=K//8) deps=[ldb_axi_op]；`b_read` deps 加 `ldb_op`（RAW，权重 ready 后读）。LDB 一次性（inner_loops=0）填 bank1[0..K//8-1]，compute 循环随后。⚠ 变延迟 → 相位隔离（保守大 latency 或 DFG 边强制 LDB 先于 b_read），非精确 add_latency_axiom。
- [ ] **6. HBM hex 生成**：weight tile 按 128b/line（8 fp16/line）打包 `memory_init0.hex`（line k↔byte k×16）；放 aiacc_rtl/data_engine/sram/ 或 g_hbm_init_path 指向。
- [ ] **7. 算子 + harness**：ManualFp16MatmulK16LdbOperator（bank1 不 inject、改 LDB load + 复用 K16 matmul）；VCS 预载 HBM hex + bank5 bit-match + 负向断言（不预载→变）。

## 验收（阶段 1 升 Accepted）
- [ ] VCS：SMC0 预载 K=16 tile 进 HBM → LDB load bank1 → matmul → bank5 bit-match golden + 负向断言（不预载→变）。
- [ ] modules/ResourceType/IQ 映射三处一致；pytest 无回归。
- [ ] NEEDS-USER-FACT-050-URADDR / -PORT 至少这两条确认（阶段 1 直接依赖）。
