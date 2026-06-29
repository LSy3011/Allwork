# SMI_DS_T datapath 时序 —— 硬件链路事实与取证

> **性质**：横跨 RTL 设计 + 编译器边权 + VCS 实测的**事实/取证记录**，非决策。
> 决策见 [ADR-040](../adr/ADR-040-smt-single-stream-timing-broadcast-cancels-gather-needs-2n-edge.md)。
> **用途**：`unit_ds_transpose`/matmul 激活广播的时序修法（尤其「SHIFTUP 发射窗口」）的直接依据。
> 后续若换 SHIFTUP 发射窗口、改 `SMI_DS_T` 边权语义，先查本文「data_out 窗口结构」节。
>
> 取证来源：RTL `/home/yq/1_code/aiacc_rtl/smi/smi_ds_t.v`（设计真值）+ VCS 波形
> `sim_output_aiacc_top_f/tb_dst.vcd`（2026-06-29，`unit_ds_transpose`，clk=10ns/拍）+ 编译器产物
> `build/verification_v2/unit_ds_transpose/{compiler_schedule,physical_instructions}.json`。

## 1. 完整 datapath（docx §946）

```
UR read → SHIFTDN(下行 gather) → SMI_DS_T(转置) → SHIFTUP(上行广播) → UR write
```
本文聚焦中段 `SMI_DS_T` 单元 + 其输出到 `SHIFTUP` 的交接时序（bank5=0 的故障落点）。

## 2. RTL 设计确定的流水级数（`smi_ds_t.v`，全部 localparam/计数器写死）

| 量 | 值 | RTL 出处 | 含义 |
|---|---|---|---|
| `PARAM_IN_LINE_NUM` | 8 | 行13 | 内部缓冲行数 = 一次收集的 SMC 行数 |
| `OUT_ROWS` | `LINE_BYTE_CNT/ELEM_BYTE_CNT` = 16/2 = **8** | 行26 | 转置后每指令输出行数（**跟元素宽度走**） |
| `WR_BEATS` | `TILE_BYTES/16` = 128/16 = **8** | 行268 | collect：收齐 8 行才 `ready`（行305-307）|
| `LAST_BEAT` | `OUT_ROWS*2-1` = **15** | 行58 | 配对两指令共输出 **16 beat**（`beat_out` 0→15）|

**三段确定延迟**：
1. **collect** = `B × WR_BEATS = 2×8 = 16` 拍（配对**两 unit 串行**各收 8 beat：u0 `wp`0→7、u1 `wp`0→7；行301-309 + top 行107-110）。⚠ 单 unit 8、配对总 16——**与 PARAM_SMC_NUM/元素宽度无关，仅随 TILE_BYTES/B**（hco 二轮订正，VCS cyc643–658 对账；详见 [ADR-041](../adr/ADR-041-smi-ds-t-shiftup-16beat-stream-and-edge-split.md) D3）。
2. **输出吞吐窗口** = `OUT_ROWS×2 = 16` 拍（`data_out_vld` 持续期，逐拍吐 16 beat；行179-191）。
3. **ready→首 data_out** = 固定 **~3 级寄存器**：`ready`(行307) → `start_out`(行88) → `out_phase`/`do_rd`(行167/72) → `out_vld_d`(行134) → `data_out`(行183)。

> ⚠ **collect 的 8 拍依赖上游 SHIFTDN gather 的数据到达速率**（yaml 注释「主延迟在 gather」即此）；
> SMI_DS_T **单元自身固定级数**只有「ready→首 data_out 的 ~3 级」+「输出窗口 16」。

## 3. data_out 窗口结构（换 SHIFTUP 发射窗口的直接依据）

VCS 实测（`tb_dst.vcd`，cyc=（t−5000ps)/10000）：

| 事件 | cyc | 说明 |
|---|---|---|
| `i_crd_shiftdn` 首非零 | 639 | gather 数据进 SMI_DS_T |
| `data_out_vld = 1`（**窗口开**） | **662** | 输出窗口起点 |
| `data_out` 首个**真数据** | 674 | `40634063...`（cyc662–673 期间 vld=1 但值为零 ⚠ 见 §6 存疑）|
| 真数据持续 | 674–677 | |
| `data_out_vld = 0`（**窗口关**） | 678 | 662–677 共 **16 拍** = `OUT_ROWS×2` ✓ |
| **`o_cru_shiftup` 发射** | **689** | **窗口关后 11 拍**（错窗）|

**SHIFTUP 当前落在 cyc689 = 窗口尾(678)之后 11 拍**；正确应对齐**窗口首 beat**（真数据 674–677，取数+1 拍 → ~673–676 发），并跟随 16 拍逐拍广播。

## 4. 689 发射拍的来源链（编译器 → RTL）

| 层 | `ds_transpose` | `shiftup_ds_t` | 差 | 出处 |
|---|---|---|---|---|
| SMT 解 | t=3 | t=**19** | **16** | `compiler_schedule.json` |
| emit | abs_t=5 | abs_t=**21** | **16** | `physical_instructions.json` |
| RTL | data_out 真数据@674 | o_cru_shiftup@**689** | ~15 | `tb_dst.vcd` |

三层差值恒 **16**，来源 = `src/synthesis/axioms/dependency_axioms.py` `add_latency_axiom`：
```
t_shiftup ≥ t_ds_transpose + spec.delays["SMI_DS_T"]   # +16
```
即 `pipeline_delays.yaml:18` 的 `SMI_DS_T=16` 被当作 ds_transpose→SHIFTUP 的 **RAW latency 边**。

## 5. 根因 + 两处订正

**根因**：`SMI_DS_T=16` 是**输出吞吐窗口**（`OUT_ROWS×2`，§2-3 实锤），却被 `add_latency_axiom` 当成
**RAW latency**（消费者要等 16 拍才发）→ SHIFTUP 排到 ds_transpose+16 = 窗口尾后 → 硬连
`wrd_data=ds_t_data_out`（ADR-039）在发射拍取到**窗口外的零** → payload 空 → `dr_shiftup_out` 零 →
**bank5 dina 全零**。门控全对（`broadcast=1`/`smc_id_en=7`/`SMC_ID=0`，§558），非多-SMC/门控问题。

**订正 A**：`16 = OUT_ROWS×2`（行58 `LAST_BEAT`），**跟元素宽度走**，非 `PARAM_SMC_NUM×2`。
fp32（ELEM=4）→ OUT_ROWS=4、窗口=8，与 SMC 数无关。yaml:18 注释「=PARAM_SMC_NUM\*2」是数值巧合
（OUT_ROWS=8 恰=SMC 数 8）；「16=gather8+broadcast8 来回」的物理解释**作废**——16 是单 SMI_DS_T 内部输出 beat 数。

**订正 B**：`SMI_DS_T` 需拆两个语义量——(a)**首 beat 就绪 latency**（collect + ~3 级，用于 ds_transpose→SHIFTUP
的 RAW 边）；(b)**输出窗口 16**（SHIFTUP 广播要**持续**的拍数 / 资源占用）。yaml 现合成一个数、SMT 取错。

## 6. 存疑（待逐拍核，勿当定论）

- `data_out_vld` 在 cyc662 即拉高，但 `data_out` 真数据要到 cyc674（中间 12 拍 vld=1 而值为零）。RTL
  行179-188 显示 `out_vld_d` 拉高即 `data_out<=bd`，不应有 12 拍零——疑为**前一配对 session 的零行输出**
  或**输入数据内容含零行**，需对 `tb_dst.vcd` 在 cyc660–680 逐拍核 `bd0..3`/`beat_out`/`cur_slot_out` 确认。
  这 12 拍不改变「16=输出窗口」结论，但影响「SHIFTUP 应精确落哪个 beat」。
