---
adr_id: ADR-035
title: SMI_DS_T 转置 datapath 落到文档推荐的 FP16 matmul 路径（fp16_matmul_q_dst）
decided: 2026-06-10
last_verified: 2026-06-10
still_holds: proposed
builds_on: ADR-032, ADR-033, ADR-034, ADR-025
---

# ADR-035 · SMI_DS_T 转置落到 FP16 matmul 路径

> **状态：Proposed（doc-first per ADR-004，新算子 + SMI_DS_T datapath 精度泛化 → 过 hco）**。
> user 订正：matmul 用 FP16MUL 才是文档推荐的硬件路径。本会话的 SMI_DS_T 转置建在 **FP32** 的
> `matmul_q`（`matmul_q_dst`/`_k8`）上——那是为先跑通转置机制、隔离精度变量。本 ADR 把 SMI_DS_T
> 转置 datapath 落到**文档钦定的 FP16 matmul 路径**（新算子 `fp16_matmul_q_dst`）。

## Context

### 文档：matmul 的硬件专用计算单元是 FP16（非 FP32）
- **表33（para25「各种精度和各种计算算子的计算单元数量」）**：FP16 向量单元 32×8=256 / FP32 向量单元
  32×4=128 / **FP16 矩阵乘法单元 32×32=1024** / **无 FP32 矩阵乘法单元**。即硬件 matmul 专用路径 = FP16。
- **FP16MUL（表510-514）= matmul 专用乘法器**（hco NOTE-1：仅做 element-wise 乘，**累加在 FP32ADDTREE8TO1**，
  非单元内含累加的 MAC）：PARAM_DR_FP16MUL_CNT=32 个乘法器、8 输出 D0-D7，`S0-S3 来自广播 × S4-S7 来自本地`
  （para171-178）→ matmul 的「广播激活×本地权重」逐元素乘；一条指令出 D0-D7 = `numpy.multiply(S_bcast, S_local)`
  （para204-219），8 个 FP32 乘积 → FP32ADDTREE8TO1 累加。FP32 FPMUL 是通用向量乘（1 乘→4-lane addtree），非 matmul 专用。
- 结论：**FP16MUL 是文档推荐的 matmul 高吞吐路径（32 乘/指令）**，FP32 FPMUL 不是矩阵乘单元。

### 现状：两条路径并存
- **FP32 路径**（FPMUL+FP32ADDTREE5TO1）：`matmul`/`matmul_q`/`matmul_parallel_n`——参考/全精度，1 乘/指令。
  **SMI_DS_T 转置建在此（`matmul_q_dst` K=4 / `matmul_q_dst_k8` K=8，ADR-033/034）**。
- **FP16 路径**（FP16MUL+FP32ADDTREE8TO1，32 乘/指令）：`fp16_matmul_min`/`fp16_matmul_q`/`fp16_matmul_n`（ADR-032）——
  文档推荐的推理 matmul（FP16 乘 + FP32 累加 = 标准混合精度）。**但无 SMI_DS_T 转置前端**。
- 缺口：SMI_DS_T 转置（真实推理需要的跨-SMC 激活整理）尚未与 FP16 matmul 路径接通。

## Decision

### D1. 新算子 `fp16_matmul_q_dst` = FP16 N-broadcast matmul + SMI_DS_T 转置前端
- 继承 `ManualFp16MatmulParallelNOperator`（fp16_matmul_n：8-SMC FP16 N 广播，satisfies 满 32 乘/SMC），
  **镜像 `matmul_q_dst_k8` 继承 `matmul_parallel_n` 的模式**。
- datapath：**列对齐 FP16 激活**（每 SMC 持激活向量的一段）→ **SMI_DS_T gather+转置** → 行对齐激活写回
  SMC_0 bank0 → **SHIFTUP 广播** → 每 SMC **FP16MUL** 与本地权重列点积 → FP32ADDTREE8TO1 → per-SMC Q。
- K=8（FP16MUL 一对源 8 lane）：激活 8 元素**列对齐存全 8 SMC**（每 SMC 持 1 元素 a[s]，mirror
  matmul_q_dst_k8 的 lanes=1 gather）→ gather 8×1 → 转置 1×8 → 恢复 8 元素行对齐（**FP16: 8 lane = 1 词**，
  vs FP32 8 元素 = 2 词）→ 写 SMC_0 bank0 1 词 → fp16_matmul_n compute。

### D2. SMI_DS_T 数据路径泛化到 FP16（精度参数化）——**hco BLOCK-1/BLOCK-2 收口**
> ⚠ `SmiDsTUnit.execute()` + cluster trigger 现用 `word_from_fp32_lanes`（硬编码 fp32，4 lane/128b 词）。
> FP16 激活 = 8 lane × 16-bit / 128b 词。需把 word 打包/解包按精度参数化。

**硬件转置 = byte 粒度（hco BLOCK-1 订正，非 element 粒度）**：
- 文档明证：para1037「转置为 **PARAM_UR_BYTE_CNT 行** × PARAM_SMC_NUM 列」+ 表597 `PARAM_IN_LINE_BYTE_CNT
  =PARAM_UR_BYTE_CNT`（=16 byte）——硬件按 **byte 位置 ↔ SMC 位置** 互换，对 element 无感知。
- **byte 转置 ≡ element 转置的不变式**（para1237-1238：matmul 上层按 `PARAM_UR_BYTE_CNT/ACTIVATION_BYTE_CNT`
  折算 element 行数）：**当①每元素字节连续 ②element_byte 对齐 ③元素不跨 128b 词边界**时，byte 转置等价 element
  转置（同元素的 N 字节转置后落相邻 N 输出行同列，不拆元素）。FP16 = 16/2 = 8 元素/词、FP32 = 16/4 = 4 元素/词。
- **本算子 lanes=1（每 SMC 持 1 fp16 元素）满足不变式**：元素不跨 SMC、不跨词边界 → byte 转置不拆元素。
  D6 加可证伪门钉死此不变式（FP16 每元素 2 byte 连续、8 元素恰 1 词 16 byte 不跨边界）。

**codec 泛化（精度参数化，BLOCK-2：单一 dispatch 参数防二义）**：
- 补 `fp16_lanes_from_word(word, lanes)` / `word_from_fp16_lanes(vals)`（16-bit IEEE754 half，元素字节连续打包）。
- `SmiDsTUnit` 加 `element_bits` 参数（默认 32 向后兼容）选 codec；**`word_lanes` 由 `element_bits` 推导**
  （`word_lanes == 128 // element_bits`：fp16=8 / fp32=4），构造器 `assert word_lanes == 128 // element_bits`
  防传入矛盾值（BLOCK-2）。**transpose_tile 核精度无关**（重排元素列表、不碰位宽），不改。
- cluster `setup_smi_ds_t_trigger` / `_tick_smi_ds_t_trigger` 据 cfg `element_bits` 选 codec gather/scatter；
  `apply_smi_ds_t`（fallback）同步泛化。
- **DEBT-035-GRANULARITY（hco 收窄范围）**：byte-vs-element 粒度文档已答（para1037 byte + para1237 element 折算）。
  **真未知仅 lanes>1（一个 SMC 持多个 FP16 元素）时 byte-shuffle 是否按 ELEMENT_BYTE 分组**——lanes=1 下两者
  重合不阻断；本 ADR 只做 lanes=1，多元素/SMC 的 FP16 分组 gated on RTL/user。

### D3. 列对齐 FP16 激活 layout
- K=8 激活向量列对齐：SMC_s（s<8）的 SRC_BANK 持 a[s]（1 FP16 元素，词 lane0）。
- 权重 b0..b3 per-SMC（同 fp16_matmul_n）。bank0 不注入（SMI_DS_T 写回行对齐激活）。
- SRC_BANK 避让 a/b0-3/y 占用的 bank（fp16_matmul_n: a=bank0, b0-3=bank1-4, y=bank5 → SRC_BANK=6 或 7）。

### D4. DFG 注入复用 `_inject_smi_ds_t_transpose`（精度无关）
- `fp16_matmul_q_dst.get_dfg` = `super().get_dfg(spec)`（fp16_matmul_n 的 SMC 内 DFG + SHIFTUP 广播）
  + `_inject_smi_ds_t_transpose(ops, cfg, spec)`（加 ds_gather(SHIFTDN)→ds_transpose(SMI_DS_T)，a_read 依赖转置）。
- 注入 helper 已精度无关（只加 SHIFTDN/SMI_DS_T 节点 + 重写 a_read deps），**一行不改**。

### D5. golden = FP16 混合精度（FP16 乘 + FP32 累加）
- 复用 fp16_matmul_n 的 `dot8`（FP16→FP32 精确乘 + 镜像 _reduce8 平衡树 FP32 累加）。激活经转置恢复后
  数值与直存一致（转置只搬不算），故 golden 与 fp16_matmul_n 同（per-SMC dot8(a, w_c)）。

### D6. 可证伪验收门
- `test_fp16_word_codec_roundtrip`：`word_from_fp16_lanes`/`fp16_lanes_from_word` 8 lane × 16-bit 往返一致
  （含负数 + **跨 byte 错位非对称值**如 0x3C00/0x4000 相邻，hco 建议——验不串字节）；**反证**：用 fp32 codec
  （4 lane）解 fp16 词 → lane 数/值错。
- `test_smi_ds_t_fp16_no_split_invariant`（**hco BLOCK-1 / sim element-packing 不变式门**）：每 fp16 元素占独立
  2-byte 对齐 lane、抠掉任一 lane 仅清零该元素其余不变（无重叠/串字节）。**evaluator caveat 订正**：sim 纯 element
  粒度（`transpose_tile` 不做 byte shuffle），此门**只锁 sim 打包正确、不验 RTL byte 转置**；真实 byte-shuffle
  （及 lanes>1 的 byte≡element 等价）VCS-gated（DEBT-035-GRANULARITY / NEEDS-USER-FACT-035-MULTILANE）。
- `test_smi_ds_t_fp16_transpose`：SmiDsTUnit(element_bits=16) gather 8 SMC × 1 fp16 → 转置 1×8 →
  DATA_OUT 1 词 8 lane = 行对齐激活（非对称值杜绝对称巧合）。
- `test_smi_ds_t_codec_param_consistency`（**hco BLOCK-2 门**）：`SmiDsTUnit(element_bits=16, word_lanes=4)`
  矛盾值 → 构造器 assert 抛错（`word_lanes != 128//element_bits`）。
- `test_fp16_matmul_q_dst_dfg`：DFG 含 SMI_DS_T 节点（拓扑 SHIFTDN→SMI_DS_T→a_read→SHIFTUP），FP16MUL compute。
- `test_fp16_matmul_q_dst_e2e`：`--op fp16_matmul_q_dst --mode e2e` 8/8 bit-perfect；**反证**：破坏 SMI_DS_T
  vld=0 → FAIL（转置不触发，激活行对齐错）。
- **回归**：matmul_q_dst/_k8（FP32 SMI_DS_T，element_bits 默认 32）不破；fp16_matmul_* 不破；全量 pytest。
- **NOTE-3 落地核对**：SRC_BANK（列对齐 fp16 激活）与 dst bank0 + a/b0-3/y 占用 bank 无同拍读写冲突（ADR-008）。

## Consequences

### 正面
- SMI_DS_T 转置接通文档钦定的 FP16 matmul 路径——真实推理 matmul 形态（FP16 32 乘/指令 + 跨-SMC 激活整理）。
- SMI_DS_T 数据路径精度泛化（element_bits）后，FP32/FP16 两条都走同一转置核 + 单元接口（S2-D1 表596）。
- 验证 SMI_DS_T 转置对 FP16 激活（2-byte 元素、8 lane/词）正确——比 FP32（4 lane）更接近文档 PARAM_UR_BYTE_CNT 满载。

### 风险 / 债务
- **DEBT-035-GRANULARITY**：byte-vs-element 转置粒度（D2 NEEDS-USER-FACT）——model-A 取 element 粒度，若 RTL
  byte 粒度 shuffle，FP16 layout 需调；gated on VCS/user。
- **逐拍时序仍 model-A**：承接 ADR-034 S2-D1 边界（DEBT-034-TRIGGER-TIMING / -LOOPS-REDUNDANT）——FP16 版同样
  逐拍 scatter 同速率对齐、SHIFTDN 真 drain 未建，VCS-gated。
- **FP16 精度边界**：FP16 乘 + FP32 累加的舍入，golden 镜像 _reduce8 平衡树——与 fp16_matmul_n 同口径，atol 沿用。

## Open questions / NEEDS-USER-FACT
1. ~~**035-GRANULARITY**（byte vs element）~~ **doc-resolved**（hco）：硬件 byte 粒度（para1037），matmul 上层按
   `PARAM_UR_BYTE_CNT/ACTIVATION_BYTE_CNT` 折算 element（para1237-1238）；lanes=1 下 byte≡element 转置（D2 不变式）。
2. **NEEDS-USER-FACT-035-MULTILANE**（收窄后真未知）：lanes>1（一个 SMC 持多个 FP16 元素）时 byte-shuffle 是否按
   ELEMENT_BYTE 分组？本 ADR 只做 lanes=1（不触发），多元素/SMC 的 FP16 分组 gated on RTL/user。

## HCO 复核（agentId `ab9eccd865e8f82e4`）
**APPROVE-with-notes**。5 主张全 ACCEPT（主张2 CHALLENGE-but-safe）：FP16 是文档钦定 matmul 路径（表33 1024 FP16
matmul 单元、无 FP32 matmul）✓ / lane 数 fp16=8·fp32=4、词数 fp16 1 词·fp32 2 词、1×8 转置维度全对无写反 ✓ /
继承结构镜像 matmul_q_dst_k8 ✓ / 未引入 ADR-024 已证伪的 K 维 reduce ✓。**未发现「FP32 假设套 FP16」实质 bug**。
2 BLOCK 已收口：BLOCK-1（转置 byte 粒度订正 + 不变式门 D6）/ BLOCK-2（codec 参数二义 → element_bits 单一 dispatch
+ `word_lanes==128//element_bits` assert）。NOTE-1（FP16MUL 乘非 MAC，累加在 addtree）已订正措辞。GRANULARITY 债务收窄
到 lanes>1。`still_holds: proposed`（逐拍时序承接 ADR-034 S2-D1 仍 VCS-gated）。

## References
- v0.4.19 docx：表33（FP16 matmul 1024 单元）/ 表510-514（FP16MUL）/ 表596-598（SMI_DS_T）/ para204-219（FP16MUL MAC）
- [ADR-032](ADR-032-fp16-matmul-fp16mul-addtree8to1.md)（FP16 matmul 路径）/ [ADR-033](ADR-033-smi-ds-t-cross-smc-transpose-datapath.md)
  + [ADR-034](ADR-034-smi-ds-t-instruction-driven-unit.md)（SMI_DS_T 指令驱动 + S2-D1 真 step-driven）
- 代码触点：`verification_v2/ops/matmul_op.py`（新 `ManualFp16MatmulQDsTOperator` + `_inject_smi_ds_t_transpose` 复用）/
  `verification_v2/main_verify.py`（注册 `fp16_matmul_q_dst`）/ `sim/smc_simulator/units/smi_ds_t_unit.py`（fp16 codec + element_bits）/
  `sim/smc_simulator/system/cluster.py`（trigger 按 element_bits 选 codec）
