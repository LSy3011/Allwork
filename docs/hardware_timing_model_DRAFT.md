# AIACC 硬件时序模型 + 时序验证策略（DRAFT，待 ADR-038 正式化）

> **状态：user-confirmed facts（2026-06-26）+ 三方复核（主 AI + hco + Codex）一致，但尚未落 ADR/代码。**
> 这是 cycle-accurate 时序工作的公理地基。新窗口（Claude 或 Codex）接「时序验证 / 方案 C / ADR-038」前先读本文。
> 正式化走 ADR-038（hco 裁：窄 scope，见末段）。

---

## 一、硬件时序模型（user 作为硬件 authority 给定，当公理）

1. **SMI = 多个独立 IQ 队列**（per unit：`ur-r0`/`ur-r1`/`fpmul`/`shiftup`/... 各一条 hex 微指令流）**+ 8 个串行 SMC**。
2. **每条 IQ 的指令串行穿过 8 个 SMC**：同一指令在 **SMC_k 第 `(发射拍 + k)` 拍执行**（1 latency/hop 传播）。
3. **smc_id_en >= SMC_ID** 才在该 SMC 执行（已实现 sim 门控：`kernel.py` 真读/写路径 line ~615/626）。
4. **shiftup / shiftdn 特殊**：除走指令外，还**跨-SMC 传数据**，1 latency/hop。
5. 每个 SMC 内**单元 latency 确定**（`hw_config/pipeline_delays.yaml`）；SMC 间传递 = 1 latency。
6. **`SMI_DS_T = 16 = shiftdn gather 8 hop + shiftup broadcast 8 hop`**（从 1/hop 一阶推出，非旧 bulk 假设；转置级近 0 额外，para1039「读完立即输出」）。
   - ⚠ 待确认：16 = 8+0+8 还是 8+X+(8−X)？（ADR-036 Q2 真空白，VCS 校）

## 二、关键推论：偏移相消（三方一致认物理正确）

- 每个 SMC_k 的所有指令**统一偏移 k 拍** → **SMC 内相对时序不变**；per-SMC 偏移是 sim/lowering 层的事。
- **广播边（shiftup，SMC_0 源 → 所有 SMC）**：producer 偏移 0、consumer 偏移 k、数据涟漪也 +k → **偏移相消**，SMT 逻辑时间线只需单元 latency。
  - **这是 ADR-021 删 chain fold 后 fma 仍 bit-perfect 的真因**（ADR-021 自己归因于已被 ADR-023 删除的 emitter INIT stagger，那解释失效；偏移相消才对）。
- **gather 边（shiftdn，各 SMC → 固定汇点）**：源偏移不同、汇点固定 → **不相消，chain latency 真实**。
  - 现由 `SMI_DS_T=16` 聚合吸收。**勿再在 SMT 边权加 hop latency → double-count**（hco/Codex 双方 BLOCK）。
- **cross-SMC 模式 ≥ 4 类**（Codex 补，勿当只 3 类）：① 广播 ② gather-reduce ③ 串联 concat（SHIFTDN 纯搬运，ADR-025，当前无活算子）④ SMI_DS_T datapath（gather→中心转置→broadcast）⑤ P2P single-source relay（broadcast=0 单点）。

## 三、元原则（user 裁定）

**AIACC 时序确定性（静态排程、无动态 stall/COND），编译器核心交付物 = 时序排布 → 验证必须验时序，光数值对不行。**
（之前「sim 只验数值、时序全交 VCS」对本架构是错判。）
但元教训仍立：**sim-vs-golden 只证自洽、不证 RTL 忠实；VCS 是唯一第三方 oracle。**

## 四、时序验证策略：方案 A / B / C

| 方案 | 内容 | 裁定 |
|---|---|---|
| A | sim 只验数值，时序全交 VCS | 弃（没验编译器核心） |
| B | 重做 cluster+SMT 为值级逐拍 cycle-accurate sim | 搁置/窄。hco+Codex：compute 逐-SMC 偏移违 ADR-022/023/036-S4（gated VCS）；SMT 拆 gather latency double-count。多月工程 |
| **C（推荐）** | 编译器产**逐拍期望时间线**（global_t + per-SMC 偏移 k + chain latency，用本模型算每拍 cycle+期望值）→ VCS 出波形 → parser 抽**有效拍**事件 → diff 找**第一处发散**(op/cycle/unit/SMC) | **对症「VCS 难调试」真痛点；不改 RTL；比 B 小一个数量级** |

### 方案 C 细节
- **现成拼图**：SMT schedule(global_t) + rigid auditor `_evaluate_expected_values`(期望值) + AUDIT-DATA 格式(SMC_ID/Cyc/Unit/Tag/Bank/Addr/V0) + `export_golden_hex`。
- **要新增**：① 编译器产逐拍期望时间线；② VCS 波形 parser；③ diff 工具（复用 auditor 逻辑）。
- **「好分析」3 前提**：① 只抽**有效拍**事件（用 RTL valid/enable 信号，几百万采样→~每 op 一事件）② 信号名→(单元,SMC,port) 映射（一次性）③ 期望 cycle 算对（时序模型对，否则全线错位假发散）。**先在 known-good case 把 diff 调全绿**（验证 parser+模型+排程一致），再信失败 case 定位。
- **RTL 改动 = 零**（信号现成，解析现有波形即可）。

### 下一步 = PoC（待执行）
最简 `unit_fpadd`（纯 compute、无 cross-SMC，已有固件+golden+期望值）：
1. **user**：`build/verification_v2/unit_fpadd/` 的 `iq_*.hex` + `ur_data` 喂 VCS、跑、开波形 dump，提供波形。
2. **AI**：建最小「期望时间线 + 波形 parser + diff」，对 unit_fpadd 跑。
3. 一起看 diff 是否真指到 op/cycle/unit、是否比看波形强。全绿+清晰 → C 成立，加 `unit_ds_transpose`(cross-SMC)；满屏噪音 → 早暴露止损。

## 五、卡 user 的未决事实
- 波形格式 = **VCD（纯文本、直接 parse）还是 FSDB（需 Verdi license）**？决定 parser 怎么写。
- SMI_DS_T 转置级**确切 latency**（16 拆法）。
- 有无要接入的**串联 matmul（SHIFTDN concat）**算子。

## 六、ADR-038 范围（hco 裁，若开）
**窄 scope**：只固化「偏移相消模型 + ≥4 类 cross-SMC 分类表 + sim 渲染层逐拍（守 ADR-036 S4 方案 A 边界）」。
**BLOCK 项作 gated-on-VCS 债务**：compute 逐-SMC 偏移执行 / SMT 拆 gather latency。
`still_holds: proposed`，升 Accepted 卡 VCS 逐拍实测。**「cycle-accurate」标签在 VCS 实测前是 aspiration 非事实。**

## 七、本 session 已落地的时序相关 commit（注意：lockstep 上的事件层 cosmetic）
- `2ce6c89` DST-SHIFTDN 逐拍渲染 · `4113e6b` smc_id_en sim 门控 · `c1ef692`/`4fa66dc` UR→SHIFTDN RTOVR slot34 · `d6c63ae` SHIFTDN gather broadcast=1
- ⚠ **逐拍渲染是事件层 cosmetic**：值仍 trigger 时一次性读齐，**不真验时序**（Codex 实证）。方案 C/B 下可能回退或重做。
