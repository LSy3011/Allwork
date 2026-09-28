---
adr_id: ADR-052
title: STB Store-to-HBM 编译器建模（UR → STB(AXI) → 外部 HBM）—— 阶段 1 单描述符最小闭环 + 8-SMC 参数化 emit
decided: 2026-09-10
last_verified: 2026-09-10
still_holds: proposed
builds_on: ADR-050, ADR-031, ADR-022, ADR-023, ADR-008
hco_review: done（2026-09-10，独立上下文审查）→ **APPROVE（附条件）**：2 个阻断项已修复（见 §2「8 位」订正 + DEBT-052-STB-HDR 逐行实测表），pipeline_delays 决断见 D6。全部位级断言经行级独立取证成立。
user_decision: user 2026-09-10「完全可以根据当前已有的手写算子的 load 和 store 的设计，结合当前的 RTL 的设计，把编译器这个部分的内容给完善一下」
---

# ADR-052 · STB Store-to-HBM 编译器建模（UR → STB(AXI) → 外部 HBM）

> **状态：Proposed —— 阶段 1（规格层 + emit 验收）已完成并通过；硅证未做。**
> 2026-09-10：hco 独立审查 APPROVE（附条件）→ 2 个阻断项已修复 → D1–D6 全部落盘 →
> **D5 端到端验收通过**（真 emitter 产出的 `iq_stb.hex` / `iq_stb-axi.hex` 与手写 golden **整数全等**
> + 负向断言翻转）+ **6 个算子回归全绿**（`add`/`matmul`/`relu6`/`gelu_approx`/`silu_approx`/`unit_fpadd`）。
> ⚠ **`still_holds` 维持 `proposed`**：本 ADR 只做到「发码与已通过的 golden 逐位一致」，
> **未跑 VCS、未上板** —— 按本项目惯例（ADR-031 等），升 `Accepted` 必须 VCS 实测。
> 为编译器补上**唯一缺失的搬运方向**：UR → 外部 HBM 写回。
> 属 `hw_config/**` 数值变更 + `src/driver/hw_resource.py` 资源类型变更 → **必调 hco + 写 ADR**（CLAUDE.md routing）。
>
> **一句话**：ADR-050 只补了**读**（HBM → LDB(AXI) → UR），**写**（UR → STB(AXI) → HBM）在编译器里仍是
> 7-bit 占位 stub；但 ADR-050 当时顺手建的 **LSU 通用基建（50b header / `_LSU_UNITS` / 最长前缀匹配）已把
> STB 一并包含**，故本 ADR 的实际改动面远小于 LDB：**只需把 body 从 7b 换真 50b、新增 STBAXI 102b、
> 注册资源、组装分桶加一个词**——编码器、header、前缀匹配三处**一行不用改**。

## Context

### 1. 编译器搬运能力缺口（本 ADR 的全部动机）

| 方向 | RTL | 编译器 develop | 手写微码线 |
|---|---|---|---|
| HBM → UR（LDB/LDBAXI） | ✓ | ✓ ADR-050（50/104b body） | ✓ 已上板 |
| **UR → HBM（STB/STBAXI）** | ✓ | **✗ 7-bit stub** | ✓ 已上板 |
| UR ↔ UR（DS/SMI_DS_T） | ✓ | ✓ ADR-033~046 | — |

`hw_config/modules.yaml:147-153` 现为 `STB: {code_bits: 7, code_fields: [vld(1), slot_id(6)]}` —— 与 RTL
真实 50b CRU 完全不符；`STB_AXI` 条目**根本不存在**。`iq_registry` 里 `STB: 7` **与 `INTTOINT: 7` 冲突**
（同一 registry 内 id 撞号），且 `ResourceType` 无 `STB`/`STB_AXI` 枚举。
STB 的引用点（`packer.py:11,20` 的注释与集合、`system_core.yaml:96`、`modules.yaml:147`、
`master.yaml:88`，另有 `sim/smc_simulator/units/smi_ds_t_unit.py:10` 注释）**无一构成功能路径依赖** ——
hco 独立复核确认（`get_iq_ids("STB")=[]`，撞号在反向寻址中不可见、无既有依赖）
→ 改动面封闭、零回归风险。

### 2. RTL 权威位布局（**行级位选取证** `data_engine/stb_ldb/stb/stb_ur_axi_conv.v:252-315`；打包实现 `iq/modules/stb_iq.cpp:26-56`）

**流 A `cru_stb`（50b，硬件 iq_inst[24]，UR 读侧）**：
```
[0]      vld                1
[15:1]   ur_addr           15   字节地址：bank=[14:12] / depth=[11:4] / byteoff=[3:0]
[23:16]  ur_rd_line_cnt     8   读 UR 行数
[32:24]  line_byte_cnt      9   每行字节数
[38:33]  line_stride        6   UR 行步长（字节步 = <<4）
[42:39]  inst_id            4   完成握手匹配
[47:43]  smc_id_en          5
[49:48]  tid                2
```

**流 B `cru_stbaxi`（102b，硬件 iq_inst[25]，AXI 写侧）**：
```
[0]      vld                1
[4:1]    first_byte_strb    4
[8:5]    last_byte_strb     4
[12:9]   brst               4   → awlen；4-bit 上限 ⇒ 单 burst ≤16 拍（256B / 16 行）
[52:13]  gr_base_addr      40   HBM 字节基址
[92:53]  gr_intlv_addr     40   per-SMC 交织步长（araddr/awaddr = gr_base + smc_id × gr_intlv）
[96:93]  inst_id            4
[101:97] smc_id_en          5
```

**⇒ 上表不是推断，是 RTL 逐行位选**（`stb_ur_axi_conv.v:252/253` 的声明 + `:285-292` / `:308-315` 的
逐字段 `assign`）。两条流的位选**首尾相接、无空隙、恰好填满声明位宽**：

| 流 | RTL 声明 | 位选链（vld → … → 末字段） | 累加 |
|---|---|---|---|
| STB | `reg [49:0] r_stb_inst_array` | `[0]·[15:1]·[23:16]·[32:24]·[38:33]·[42:39]·[47:43]·[49:48]` | 1+15+8+9+6+4+5+2 = **50** ✓ |
| STBAXI | `reg [101:0] r_stbaxi_inst_array` | `[0]·[4:1]·[8:5]·[12:9]·[52:13]·[92:53]·[96:93]·[101:97]` | 1+4+4+4+40+40+4+5 = **102** ✓ |

即 `code_fields` 的**声明序 == 物理位序**（低→高），与 `packer.py` 按声明序累加 `curr_pos` 的语义一致，
**无需任何偏移或反转**。同时 `cru_stb_i` / `cru_stbaxi_i` 直接写入 `r_*_inst_array`（`:273` / `:278`），
证明 CRU 宽度就是 body 宽度（不含 header）。

**与 LDB 的镜像关系（逐位，本 ADR 的核心依据）**：

| | LDB（56b） | STB（50b） | 差异 |
|---|---|---|---|
| [0] | VALID | VALID | — |
| [15:1] | ur_addr | ur_addr | — |
| [23:16] | ur_**wr**_line_cnt | ur_**rd**_line_cnt | 仅语义（写/读）与字段名 |
| [32:24] | line_byte_cnt | line_byte_cnt | — |
| [38:33] | line_stride | line_stride | — |
| [44:39] | const(6) | — | **删** |
| [45] | pre_fill | — | **删** |
| [46] | post_fill | — | **删** |
| [50:47] | inst_id | inst_id[42:39] | **整体下移 8 位** |
| [55:51] | smc_id_en | smc_id_en[47:43] | **整体下移 8 位** |
| — | — | **tid[49:48](2)** | **增** |

即 `STB = LDB − {const(6b), pre_fill(1b), post_fill(1b)} + {tid(2b)}`，**后段整体下移 8 位**。

> ⚠ **是 8 位不是 6 位**（hco 审查订正，2026-09-10）：被删的是 `const`(6) + `pre_fill`(1) + `post_fill`(1)
> = **8 bit**，**不是 `const` 的字段宽 6**。逐位核算：`inst_id` LDB[50:47]→STB[42:39]、`smc_id_en`
> LDB[55:51]→STB[47:43]，两侧均差 8；宽度自洽 `56 − 8 + 2 = 50` ✓。
> **上方位表本身经 RTL 逐行位选验证无误，错的只是本条叙述** —— 但此段是 ADR 自述的「核心依据」，
> doc-first 原则下必须改准，否则后续实现者按「下移 6」写会全盘错位。
`STBAXI(102b) = LDBAXI(104b) − {tid[2b]}`（其余完全一致）。

> ⚠ **STB 天然没有 pre/post fill 与 const** —— LDB 的「阶段 2 fill 语义」在写侧**不存在**，
> 故本 ADR 的分期不含 fill（与 ADR-050 阶段 2 的形状不同）。

### 3. 逐位 golden：手写线的 STB 产物已在 8-SMC RTL case 里通过

`~/u280_sim_v422/test_atomic_ops/multi_inst_stage/sub_8smc_v3/`（README 称"已通过的模板"、脚本注释
`Known-good`）是**标准 Compute 算子的 Load→Compute→Store 三阶段 8-SMC case**（`AIACC_v4.22算子修改注意事项.md`：
"标准 Compute 算子使用 Load -> Compute -> Store 三阶段"；`meta_v3.json: num_smc=8`；`smc_0..smc_7/` 全在）：

```
smi_iq_inst/stage_0/  iq_ldb.hex + iq_ldb_axi.hex                          ← Load
smi_iq_inst/stage_1/  iq_fpmul + iq_fpadd + iq_rtovr-* + iq_ur-r*/w* + VIN0/VIN1/VOUT0  ← Compute
smi_iq_inst/stage_2/  iq_stb.hex + iq_stb_axi.hex                          ← Store  ← 本 ADR 的逐位目标
```

**本 ADR 实测解码 `stage_2/iq_stb.hex`（5 条）**，按上方位布局 + 50b LSU header：

| row | ur_addr | bank | ur_rd_line_cnt | line_byte_cnt | line_stride | smc_id_en | tid | INC | 解读 |
|---|---|---|---|---|---|---|---|---|---|
| 1–4 | 16384 | 4 | 2 | 15 | 1 | 7 | 2 | 16 | 中间结果写回（**DLY=7**；**`COND=26` 仅 row1**，见 DEBT-052-STB-HDR） |
| **5** | **12288** | **3** | **32** | 15 | 1 | 7 | 2 | 16 | **最终输出写回** |

**`stage_2/iq_stb_axi.hex`（5 条）**：row 5 = `brst=15`（16 拍）+ `LOOPS=1` → 2×16 = 32 行 ✓、
`gr_base_addr=12288` ✓、`gr_intlv_addr=0x10000000`（每 SMC 交错 256MB）、`INC=256`（每 loop 步进 256B=16 行）。

**四重独立交叉验证**：
1. row 5 的 `bank3 / 32 行` 对上 `meta_v3.json{out_bank:3, out_words:32}`；
2. `gr_base_addr=12288` 对上 `store_byte_base:12288`；
3. `stage_2/iq_inst_stb_subq0.txt` 的 AXI beat 拆包（13B = beat0 的 12 lane + beat1 的 1 lane，
   byte0→lane4 小端）**独立复现**了同一解码；
4. **RTL 逐行位选**（§2 的 `stb_ur_axi_conv.v:285-292` / `:308-315`）与上表**逐字段同址同宽**，
   且各位段累加恰好等于 `reg [49:0]` / `reg [101:0]` 的声明宽度（无空隙、无重叠）。

→ **位布局理解已被证据锁定，不是推断。**

### 4. 文件宽度约定（验收前必须知道，否则会误判为 bug）

| 单元 | 总位宽 | 手写/权威行宽 | 编译器现行行宽 |
|---|---|---|---|
| LDB | 50+56=106 | 28 字符（`ceil(106/8)*2`，14B） | 27 字符（`ceil(106/4)`） |
| LDB_AXI | 50+104=154 | 40 字符（20B） | 39 字符 |
| **STB** | **50+50=100** | **26 字符**（13B） | — |
| **STB_AXI** | **50+102=152** | **38 字符**（19B） | — |

`lsu_emit.cpp:91` 用**字节**对齐：`total_bytes=(header+code+7)/8`；`emitter.py:66` 用**半字节**：
`hex_chars=(total_bits+3)//4`。二者对 152 恰好一致、对其余差 1 字符。
TB 侧 `generate_data_list_coverage.py:_parse_hex_line_to_int` + `_split_inst_to_beats` 用**自身表中
`iq_bits` 掩码**（不依赖行长）→ **数值等价，纯 padding 差异，非 bug**。
⇒ **验收必须比整数，禁止比字符串。**

**已实测（2026-09-10）**：按 §2 的字段序写反编码器，对 `sub_8smc_v3/stage_2` 的
`iq_stb.hex`(5 行) + `iq_stb_axi.hex`(5 行) 做「解码 → 反编码 → 与**磁盘原文**逐字节比」：
**10/10 行全等**（STB 26 字符 / STBAXI 38 字符，与上表字节对齐列一致）。

> ⚠ **诚实界定这条证据的强度**：解码与反编码走**同一**字段序，故往返对**任意**划分都是恒等 ——
> 它只证明**位宽与字节对齐自洽**（100b→13B、152b→19B 恰等于文件字符数），**不证明字段边界语义**。
> 语义由 §3 的四重独立证据锁定，其中第 4 重（RTL 逐行位选）是决定性的一条。
> 另：本次自检最初报 1 处 ❌，系**手抄期望值漏两位**所致，改为从磁盘读取后期望/实得全等 ——
> 印证「禁止手抄常量，一律读源」。该教训已固化为本 ADR 的验收纪律。

## Decision

### D1：`modules.yaml` —— STB stub(7b) 换真 50b，新增 STB_AXI 102b

按 §2 位布局逐字段写 `code_fields`，顺序与 `pack_cru_stb`/`pack_cru_stbaxi` 的 `put_bits` 次序**严格一致**
（packer 按 `code_fields` 声明序累加 `curr_pos`，声明序即物理位序）。
字段名沿用 LDB 条目的既有大小写习惯（`VALID` 大写触发 payload_factory 的 `VALID→1` 特例；
其余小写与 `stb_iq.h` 的 `para_*` 对应）。`type: transfer`、`latency: 1`（占位，同 LDB：
STB 是变延迟 DMA + done 握手，非固定流水 latency）。

### D2：资源注册（逻辑 id 走"清晰空档"范式，**不**直接写 RTL 槽号）

- `system_core.yaml` + `master.yaml` 的 `iq_registry`：`STB: 7` → **52**（7 与 `INTTOINT: 7` 撞号），
  新增 `STB_AXI: 53`。**逻辑 id，非 RTL 槽号** —— 与 LDB(50/51) 同范式：emit 出
  `iq_stb.hex` / `iq_stb-axi.hex`，TB `generate_data_list_coverage.py: infer_iq_id_from_filename()`
  按 **iq_name** 归位 RTL 槽 **24 / 25**。
- `unit_instances` + `unit_instances_per_smc` 各加 `STB: 1` / `STB_AXI: 1`（单实例流，同 LDB）。
- `src/driver/hw_resource.py`：`ResourceType` 加 `STB` / `STB_AXI`。
- `src/api/hw_spec.py: get_iq_name` 的**两份**单实例名清单各加 `STB`/`STB_AXI`，使名不带 idx 后缀：
  - `:319-321` —— `unit_type` 快路径
  - `:335-337` —— 全局扫描兜底（两份当前内容**逐字相同**：`[…, "LDB", "LDB_AXI"]`）

  ⚠ **必须同改两份**：只改一份时，另一条路径会带出 `stb-0` / `stb_axi-0`，TB 的
  `infer_iq_id_from_filename()` 认不出 → 静默归位失败。（行号已 2026-09-10 逐字核对。）

### D3：`assembler.py` 的 memory-IQ 判定加 `"stb"`

`assembler.py:97`（逐字核对）：
```python
is_memory_iq = ("ur-" in iq_name.lower()) or ("rtovr" in iq_name.lower()) or ("ldb" in iq_name.lower())
```
`:96` 的既有注释已写明该判据的设计意图 —— 「`"ldb"` 子串覆盖 `"ldb"` 与 `"ldb-axi"`」——
同一子串技巧对 `stb`/`stb-axi` 直接适用，加 `or ("stb" in iq_name.lower())` 即可，无需分支。

该判据有**两个消费点**（都靠它开地址步进，漏加即静默错）：
- `:100` + `:114`（level-1 主循环）：`hdr_inc_embed = max(iq_max_stride, 1) if is_memory_iq else 0`，
  以及 `op_mask = 0x1 if (is_memory_iq and op_stride != 0) else 0`
- `:280` + `:288`（held 重放路径）：同式

`inc_embed`/`op_mask` 门控的是 STM 的 level-1 loop 地址步进，对 LSU 系是共用机制（ADR-050 阶段 2 已 VCS 证）。
**漏加 `"stb"` 的后果是多描述符时 STB 地址不步进 —— 且单描述符单 tile 仍绿，属静默错误。**

### D4：`payload_factory.py` / `packer.py` —— **不改**

- `packer.py:14-20` 的 `ISA_V5_HEADER_LSU`(50b) 与 `_LSU_UNITS = {"LDB","LDB_AXI","STB","STB_AXI"}`
  **已包含 STB/STB_AXI**（ADR-050 建基建时一并写入）→ 50b header 自动生效。
- `payload_factory.py:19-28` 的**精确匹配 + 最长前缀兜底**已能区分 `stb` / `stb-axi`
  （同一机制解决了 `ldb` 吞并 `ldb-axi`）→ 无需改动。
- ⚠ **但 D4 的「无需改动」以 D1 为前提** —— 这不是推断，**已实测**（2026-09-10，直接调
  `resolve_iq_type` 喂真实 `modules.yaml`）：

  ```
  modules 里有 STB_AXI 吗： False
  STB 现状：code_bits=7 fields=['vld', 'slot_id']
    resolve_iq_type('stb'    ) -> STB       code_bits=7
    resolve_iq_type('stb-axi') -> STB       code_bits=7   ← 🔴 静默产错固件
    resolve_iq_type('ldb'    ) -> LDB       code_bits=56
    resolve_iq_type('ldb-axi') -> LDB_AXI   code_bits=104  ← 条目存在 → 正确
  ```

  ⇒ 当前 `stb-axi` 精确匹配落空 → 进前缀兜底 → `"stb-axi".startswith("stb-")` 为真
  → **静默返回 7-bit 的 STB stub**（需 102 位处打 7 位，正是 ADR-050 为 LDB 修掉的同一个坑）。
  ⇒ **D1 不只是「补一个缺失条目」，而是堵住一条会静默产错固件的路径。**

### D5：验收方法 —— 整数逐位比对 + 手写 golden

1. 构造一个最小 STB 算子（镜像 `matmul_op.py:1577-1582` 的 LDB/LDB_AXI 对）走 `emit_from_instructions`。
2. 断言产出的 `iq_stb.hex` / `iq_stb-axi.hex` **每行解析为整数后**与目标值相等
   （**不比字符串**，理由见 §4）。
3. 对拍目标：① `sub_8smc_v3/stage_2` 的手写 golden（同参数下）；② `iq_field_diff.py` 把两边都解成
   **字段字典**再比 —— 字段级差异比整数差异可读得多。
4. 负向断言：改动源 bank/偏移，产出必须随之变化（防假绿）。

**字段填充责任划分（逐条核对 `payload_factory.py`，写算子时极易踩）**：

| 字段 | 来源 | 说明 |
|---|---|---|
| `VALID` | **自动** | `payload_factory.py:82-84` 无条件置 1，无需传 |
| `smc_id_en` | **自动 = 7** | `:110-121` 精确匹配 `SMC_ID_EN` → `max(PARAM_SMC_NUM-1, 0)` = 7，**与 golden 的 7 一致** ✓ |
| `tid` | **必须显式传 `tid=2`** | ⚠ **无特例分支**：未传时落 `:149-151` 的 `field.default`，而 `hw_spec.py:22` 定义为 **`default: int = 0`** → 打包成 `tid=0`，**与 golden 的 2 不符、验收直接挂** |
| 其余 body 字段 | 必须显式传 | `ur_addr` / `ur_rd_line_cnt` / `line_byte_cnt` / `line_stride` / `inst_id`；AXI 侧 `gr_base_addr` / `gr_intlv_addr` / `brst` / `first_byte_strb` / `last_byte_strb` |

**先例支持**：`matmul_op.py:1579/1610/1636` 三处 LDB_AXI emit **全部显式传 `tid=0`**，从不依赖默认 ——
STB 照此范式显式传 `tid=2` 即可，**不必也不应**在 `modules.yaml` 里给 `tid` 写 `default: 2`
（那会把「AXI 事务 ID」这个软件策略烧进硬件规格，hco 必驳）。

### D5 前置：内存 dry-run 已通过（2026-09-10，**真 packer，未落盘**）

在**不修改任何仓库文件**的前提下，把 D1 拟定的字段表在内存里构造成 `ModuleSpec`，交给
**生产路径上的真 `BitPacker.pack_capsule`**（非草稿编码器）打包，与手写 golden 逐整数比对：

```
字段宽度自洽：STB 求和=50 ✓   STB_AXI 求和=102 ✓
get_unit_bits(STB)=100 (期望 100)   get_unit_bits(STB_AXI)=152 (期望 152)   ← 50b header 自动选中
STB      row5: got=0x0800000000100238020f206001  exp=0x0800000000100238020f206001  ✅ 整数全等
STB_AXI  row5: got=0x8000080010000e000200000000000006001e01  exp=…6ee1  ✅ 整数全等
```

⇒ **D1 的字段表与字段序，已被生产 packer 独立证实能精确还原 golden**；D5 的验收路径也已提前去风险。
这条证据强于 §4 的往返自检（那条只证位宽自洽，这条走的是真生产代码路径）。

**脚本已固化**：`verification_v2/tools/stb_pack_dryrun.py`（可复现，退出码 0 = 通过）。
它另带一层 **yaml 守卫**，三态区分：

| yaml 现状 | 判定 | 退出码 |
|---|---|---|
| 仍是 7b stub / STB_AXI 不存在 | 「未实现（D1 待做）」 | 0（**非失败**，实现前恒为绿） |
| 与上表逐字段一致 | 「✅ 一致」 | 0 |
| 位宽对但字段不符 | 「❌ 转录漂移」 | **1** |

守卫的作用：dry-run 验的是**代码里这张表**，落盘到 yaml 时若抄错就白验了 —— 守卫直接抓住转录漂移。
（实现前它与「未实现」必须区分开，否则恒红、失去信号价值。）

### D5 验收结果（2026-09-10 已执行，`verification_v2/tools/stb_emit_accept.py`）

走**真 `LIRPacker.emit_from_instructions`**（非直调 packer）产出，对拍手写 golden 第 5 行：

```
[STB]     emitter 产出 iq_stb.hex
   golden(26字)  = 0800000000100238020f206001
   emitted(25字) = 800000000100238020f206001
   ✅ 整数全等   （行宽差 -1 字符 = 半字节对齐 padding）
   字段级：[100b] HDR{ INC=16 VLD=1 }  BODY{ VALID=1 UR_ADDR=12288 UR_RD_LINE_CNT=32
                     LINE_BYTE_CNT=15 LINE_STRIDE=1 SMC_ID_EN=7 TID=2 }

[STB_AXI] emitter 产出 iq_stb-axi.hex
   golden(38字)  = 8000080010000e000200000000000006001e01
   emitted(38字) = 8000080010000e000200000000000006001e01
   ✅ 整数全等   （行宽差 0 字符）
   字段级：[152b] HDR{ INC=256 LOOPS=1 VLD=1 }  BODY{ VALID=1 BRST=15 GR_BASE_ADDR=12288
                     GR_INTLV_ADDR=268435456 SMC_ID_EN=7 }

[负向断言] STB ur_addr 12288→16384：✅ 输出翻转 ／ STB_AXI gr_base_addr 12288→12304：✅ 输出翻转
```

> 🔍 **§4 的宽度预测被实测命中**：实现在**落地前**预测「STB 差 1 字符、STB_AXI 恰好相等」，
> 实测**逐字吻合**（26→25、38→38）。这是对 §4 对齐分析的一次非平凡证伪机会，它没被证伪。

**回归（6/6 全绿，改动未破既有路径）**：`add` · `matmul`（走 LDB 路径，最邻近风险点）·
`relu6` · `gelu_approx` · `silu_approx` · `unit_fpadd`。

### D6：`pipeline_delays.yaml` 补 `STB: 40 / STB_AXI: 1`（hco 审查新增项）

**问题**：初稿的变更清单**漏了 `pipeline_delays.yaml`**。LDB 在 `:20-25` 有
`LDB: 40 / LDB_AXI: 1`，而 `hw_spec.py:92` 的 `self.delays.get(key, 1)` 对缺失项**静默落 1**。

**决断：补，镜像 LDB 形态。** 理由：
1. STB 与 LDB 是**同族变延迟 DMA**（round-trip 非固定流水），风险同构；
2. **阶段 1 该值实际不被消费**（STB 是 DFG sink、无下游消费者；单描述符相位隔离已覆盖 WAR）
   → 补上对阶段 1 **零功能影响、零回归风险**；
3. **但不补会埋雷**：任何后续「STB 居中」的算子（STB 后还有 UR_WRITE/计算）会被 `latency=1`
   **静默欠隔离** —— 与 ADR-050 当初为 LDB 补 40 是同一个失效模式；
4. 与 LDB 先例一致，专家读者不会问「为什么 STB 没有」。

**边界（诚实标注）**：`40` 是**镜像自 LDB 的保守值，未经 STB 自身 VCS 标定**；
且 STB 的 DFG 边方向（`STB → 下游` 而非 LDB 的 `LDB_AXI → LDB`）**本 ADR 未取证** ——
注释里只声明「镜像 LDB 的相位隔离约定」，**不声称**边权语义已与 LDB 逐一对齐。
共享 `DEBT-050-VARLAT`（本 ADR 不新开债务），升 Accepted 时一并换 done-握手事件建模。

### D7：回归守卫（hco 审查建议项，2026-09-10 已落地）

hco 审查建议的 4 条 pytest 已全部落地为 `tests/unit/test_stb_schema_regression.py`（9 个测试），
外加 1 条镜像关系守卫。**每条守卫都对应一个已发生或已实测复现的失效模式**：

| # | 守卫 | 对应的病理 | 来源 |
|---|---|---|---|
| ① | 两份 specs 的 `iq_registry` 无重复 id + 两文件逐值一致 | 改前 `STB: 7` 与 `INTTOINT: 7` 撞号 | hco 建议 |
| ② | `STB`=50b / `STB_AXI`=102b，且 `resolve_iq_type` 四元归位 | `stb-axi` 被 `stb-` 前缀吞成 7b stub（D4 实测） | hco 建议 |
| ③ | `get_iq_name` **两条路径**同值且无 idx 后缀 | 只改一份清单 → 兜底路径返回 `stb_axi-0`（D2） | hco 建议 |
| ④ | AST 读 TB `IQ_NAME_TO_ID`，钉死 `stb→24 / stb_axi→25`，并验 TB 仍做 `'-'→'_'` 归一 | 编译器 id(52/53) ↔ RTL 槽号(24/25) 是**两个命名空间**，靠文件名桥接 | hco 建议（改造） |
| ⑤ | 镜像关系：`STB = LDB − {const,pre_fill,post_fill} + {tid}` 且后段**下移 8 位**；`STB_AXI = LDB_AXI − {tid}` | §2 的核心依据被后续改动悄悄破坏 | 本 ADR 自加 |

> ⚠ **④ 与原建议的偏差（诚实记录）**：hco 原建议是「TB `infer_iq_id_from_filename("iq_stb-axi.hex")==25`」，
> 但该函数**不在编译器仓库内**（只在 `~/u280_sim_v422/generate_data_list_coverage.py`）。
> 故改为 **AST 读表** + `skipUnless(TB 在场)`：不在场则 skip（不在无 TB 的环境里假红），
> 在场则真读表。可移植性与信号价值都强于硬编码 import。
> ⚠ **本守卫的强度边界**：AST 只钉「表里 `stb_axi→25`」+「TB 有 `'-'→'_'` 归一」两个**事实**，
> 未执行 TB 的 `infer_iq_id_from_filename` 函数体（避免拉起 2000 行 TB 的 import 副作用）。
> 桥的**运行时**行为仍由阶段 2 的 VCS 实测覆盖。
>
> ✅ **槽号 24–27 是跨版本稳定 ABI（2026-09-10 实测，本机 6 份 TB 副本）**：
> `stb:24 · stb_axi:25 · ldb:26 · ldb_axi:27` 在**全部 6 份**中逐字相同，
> 覆盖 `u280_sim_v422`(v4.22) · `aiacc_v0.4.20_tb_opt` · `jianyuan_v4.19`(×2) · `release_v4.17_20260506`
> —— 即 **v4.17 → v4.22 五个版本**未变动。这使「钉死常量 24/25」成为正当的守门（而非绑死某份快照）；
> 另：本机 `~/u280_sim_v422` 是 juno1023 自有的独立 checkout，**非** `/home/bupt/u280_sim_v422` 的 symlink
> （两份内容在该表上一致）。

**守卫的守卫：变异测试**。通过的测试 ≠ 能失败的测试 —— 一条永远绿的断言零信号价值却伪装成已覆盖。
`verification_v2/tools/stb_guard_mutation.py` 对每条守卫**注入其病理**并断言确实变红，
**20/20 条符合预期**（含基线 7 条、变异 13 条），不改任何仓库文件：

```
spec 变异 → mock.patch.object 换类属性（深拷贝后改）
yaml 变异 → 临时目录 minimal yaml
hw_spec 变异 → 读**真实源码**字符串替换 + exec（验的是生产判断逻辑本身，非测试的复制品）
```

从这次变异测试里捞到的 3 个**真问题**（全部已修，且都是「守卫本身有缺陷」而非被测代码缺陷）：

1. **手动调测试方法不会触发 `setUpClass`** → `cls.spec` 未设 → 5 条基线全因 `AttributeError` 假红。
   pytest 下是 9 passed，所以这是变异脚本的锅 —— 但它暴露了「基线」这一步不可省。
2. **`mock.patch.multiple(target, spec=...)` 的 `spec` 是 mock 自身保留关键字**（同 `create`/`autospec`），
   会被吞掉 → `ValueError: Must supply at least one keyword argument`。要换的正是类属性 `spec`，撞名必踩
   → 改用 `patch.object`。
3. **测试自身曾依赖 cwd**：`HardwareSpec.load("hw_config")` 按字面路径找文件，pytest 从仓库根跑才碰巧过；
   变异脚本从 `/tmp` 跑即 `KeyError: 'hardware_params'` → 改为**绝对路径** `str(_ROOT / "hw_config")`。

> 变异脚本里另有两处**我的预期写错**（不是代码错），已作为注释固化在该脚本内：
> ③ 的阳性对照 `LDB_AXI` 本不该分叉（变异只删了 STB 系）；③ 必须**两条路径都调**，
> 只调快路径会提前 return、误判「变异未复现」。

**全量回归（2026-09-10，`pytest tests/`）**：**195 passed, 7 skipped, 0 failed**（exit 0，254s）。
7 个 skip 为既有（与本次改动无关）—— 本 ADR 新增的 9 条全部实跑（TB 与手写 golden 均在场），
其 `skipUnless` 兜底是为无 TB/golden 的环境准备，非本机状态。
⚠ 该命令**从仓库根跑**；守卫测试自身已修为 cwd 无关（上文真问题 3）。
### 不做（本 ADR 范围外）

- **2D stride / pre-post fill**：STB 位布局里**没有**这三个字段（§2），不存在此需求。
- **`tid` 语义标定**：手写 golden 里 `tid=2` 常量。阶段 1 照抄 2，语义留 DEBT-052-STB-TID。
- **并发 LDB+STB 端口冲突 axiom**：UR bank 单端口（ADR-008），同 cycle 读写同 bank 被静默丢弃。
  阶段 1 沿用 ADR-050 D3 的相位隔离绕开；并发建模留阶段 2。
- **变延迟 done-握手建模**：沿用 ADR-050 D2（保守上界 latency + 显式相位隔离），
  共享 DEBT-050-VARLAT，本 ADR 不新开。

## 增量分期

- **阶段 1（本 ADR）—— ✅ 已完成（2026-09-10）**：单描述符最小闭环 —— modules/specs/资源注册/
  pipeline_delays/assembler 五处改完，能 emit `iq_stb.hex` + `iq_stb-axi.hex` 且**字段级对齐手写 golden**。
  **出口判据 = D5 的整数逐位比对通过 → 已通过**（见上「D5 验收结果」）；
  **回归守卫 = D7 的 9 测试 + 变异测试 20/20 → 已通过**（每条守卫均被证明可红）。
  ⚠ **但阶段 1 的「硅证」子目标未做**：本阶段只证「发码与已通过的 golden 一致」，
  **未在 VCS 里跑 UR → STB → HBM 的实际搬运**。后者是阶段 2 的前置。
- **阶段 2（deferred）**：多描述符 / 大 tile 分段（复用 LDB 的 `bulk_burst_segments`）+
  8-SMC 交织参数化（`gr_intlv_addr = smc_id × 0x10000000`，`smc_id_en = N-1`）+
  并发 LDB+STB 端口冲突 SMT axiom + 反证测试。
- **阶段 3（deferred）**：变延迟 done-握手事件建模（与 LDB 一并解 DEBT-050-VARLAT）。

## Consequences

### 正面
- **闭环最后一块拼图**：编译器首次具备完整 `HBM → UR → 计算 → UR → HBM` 双向搬运能力。
- **成本远低于 LDB**：LDB 的 10 个死路提交（unrolled 多-LDB 三法皆未通、`brst=0` 死锁、
  LSU IQ 信用门控、DLY≠fetch 间隔）**全部是读侧探路**，写侧直接继承结论；
  且编码器/header/前缀匹配三层基建 ADR-050 已建好。
- **有逐位目标**：不需从 RTL 反推语义，只需对齐一份已通过的产物。

### 风险 / 债务
- **DEBT-052-STB-HDR**：手写 golden 的 header 中段形态未明。**逐行实测**（hco 审查订正，
  2026-09-10；两份文件形态相同）：

  | 行 | iq_stb.hex `DLY/COND` | iq_stb_axi.hex `DLY/COND` | `LOOPS` |
  |---|---|---|---|
  | 1 | 7 / **26** | 7 / **26** | 0 |
  | 2–4 | 7 / 0 | 7 / 0 | 0 |
  | 5 | 0 / 0 | 0 / 0 | **1**（仅 AXI 侧） |

  ⚠ **订正**：初稿写「row1–4 的 `DLY=7 / COND=26`」是错的 —— `COND=26` **只在 row1**，
  row2–4 的 `COND=0`；且 `iq_stb_axi.hex` 的 row1–4 同样带 `DLY=7`（初稿漏记）。
  阶段 1 只对齐 row5 形态的最终输出写回；row1–4 的中间写回形态留待阶段 2 溯源
  （按上表的**真实**形态找规律，勿按错误范围推）。
- **DEBT-052-STB-TID**：`tid` 的**机理已明，仅取值待标定**。
  - STB 侧：`stb_ur_axi_conv.v:292` 取 body `[49:48]` → `:689` 驱动 `axism_m_tid`（AXI master 侧事务 ID）
  - LDB 侧同构：`ldb_ur_axi_conv.v:299` 取 LDBAXI body `[103:102]` → `:889` 驱动 `axism_m_tid`
  - 即 `tid` = **AXI 事务 ID 标签**（STB 走 AWID / LDB 走 ARID），非数据字段。
    手写 golden 恒 `2`，阶段 1 照抄；标定留阶段 2。
- **写侧握手异于读侧**：唯一必须新取证的 RTL 行为。缓解：`smc_top` 的 STB/LDB 共用
  `stb_ldb` 目录与 CRU 宽度表（`smi_iq_cru_widths.vh: CRU_STB_W=50 / CRU_LDB_W=56` 同族），
  且现成 8-SMC case 已在跑 STB → 若阶段 1 字段级对齐通过，该风险基本消除。
- **编译器 `(total_bits+3)//4` 与权威字节对齐差 1 字符**：数值等价，但若将来有工具按**行长**
  严格校验会误报。建议后续统一为字节对齐（本 ADR 不改，避免动 LDB 既有已验证路径）。
