# AIACC load_store_v3 框架与 mul_scalar / select_int 算子实现与验证总结

> 覆盖时间:2026-05 ~ 2026-09-07 的算子实现与迭代;整理于 2026-09-09。
>
> **目录说明**:本仓库 `op/` 只归档与 mul_scalar / select_int 直接相关的代码子集(含 2026-09-07 最新迭代),
> 权威全量源码在上游仓库 `LSy3011/Jianyuan`(`aiacc/SMI_IQ/…`,仿真/验证日志也在其 `load_store_v3/out/` 下)。
> `op/load_store/` ↔ 上游 `aiacc/SMI_IQ/load_store_v3/`;`op/实现/` ↔ 上游 `aiacc/SMI_IQ/op/`。

---

## 1. 工作目标

把 AIACC 算子从「软件发码 + RTL 仿真」推进到「U280 FPGA 上完整执行」,核心是把整条数据通路做扎实:

```text
算子参数与输入数据
  -> 生成 HBM 数据、IQ 指令、golden
  -> Load:  HBM/DDR -> UR
  -> Compute:UR -> RtoVR -> 计算单元(FPMUL 等) -> UR
  -> Store: UR -> HBM/DDR
  -> Host 回读 / RTL dump
  -> 与 golden 做 bit-exact 比对(逐 16B word)
```

两个主打算子:**mul_scalar**(标量乘,多 SMC 流水)与 **select_int**(部分元素选择)。
它们同时出现在两条代码线里:一是集成进 aiacc 的软件算子本体,二是独立验证框架里的
「lsu 子类」形态。

---

## 2. 两条代码线的架构

### 2.1 集成算子线:`SMI_IQ/op/<op>/`(本仓库快照 = `op/实现/`)

软件算子本体 + 微指令生成。一个算子按职责拆成固定几个文件:

| 文件 | 职责 |
|------|------|
| `<op>.cpp / .h` | 算子语义、`compile()` 主流程、`get_iq_files()` 排程 |
| `<op>_ur_inst.*` | UR 读/写指令段(load/store 微指令的组装入口) |
| `<op>_rtovr_inst.*` | RtoVR 路由/搬移指令段 |
| `<op>_compute_inst.*` | Compute(如 FPMUL CRU)指令段 |
| `<op>_constants.h` | 端口号 / IQ 号 / 时序常量 |
| `ur_code_extractor.h` | 从 UR 指令中抽取读写访问序列的辅助头 |

本仓库快照含个人主导的 `mul_scalar` / `cos` / `neg` / `select_int`;
上游全量还含 `mul` / `bitwise_and` / `add` / `sub` / `div` / `exp` / `pow` / `rsqrt` / `softmax` 等。

### 2.2 独立验证框架线:`SMI_IQ/load_store_v3/`(本仓库快照 = `op/load_store/`)

把 Load→Compute→Store 全链路抽象成公共基类 `LsuOpBase`,算子子类只写「compute IQ + golden + 端口配置」,
即可一键生成 8-SMC 的 RTL 用例并做 DDR 写回真比对:

```
LsuOpBase(公共:Load/Store 微指令、HBM 数据、case 组装、meta)
   ├── MulScalarLsuOp  (mul_scalar 的验证子类;本快照内有)
   ├── SelectIntLsuOp  (select_int 的验证子类;本快照内有)
   ├── MulLsuOp        (上游仓库内)
   └── BwAndLsuOp      (上游仓库内)
```

---

## 3. load_store_v3 框架

### 3.1 数据流与地址布局

- 1 word = 1 UR line = 128 bit = 16 B;UR bank 深 256 line;输入按 `inputs` 迭代顺序定 `gr_base`,
  store 落在所有输入之后(落点写入 `meta_v3.json` 的 `store_byte_base`)。
- 仿真有 **8 个物理独立 HBM**,靠 NoC 分窗:`SMC_n 实际地址 = gr_base + n × 0x10000000`,互不干扰。
- 输入数据由框架生成:`std::mt19937 rng(seed + smc_id*1000)`,每 SMC 数据互异;
  数值范围约 ±1e-20 ~ ±2e20(跨数量级随机,覆盖正负/极大/极小,不含 inf/NaN/denormal 输入)。
  换 `--seed` 即整套换数据。

### 3.2 v2 → v3 的本质区别

| 维度 | v2 | v3 |
|------|----|----|
| Load/Store 发码 | `ur_wr/rd_line_cnt=1`,靠 IQ 外壳 loops 重复 | **原生 bulk**:`ur_wr/rd_line_cnt=word_count`,一条 LDB/STB 搬完多行 |
| LDBAXI/STBAXI | 单拍多发 | **burst 分段**:16 拍主体 + 余数段 |
| case 版式 | v0.4.20 `smi_iq_inst/stage_*` | 双版式:4.19 flat(合并写 `smc_0/iq_*.hex`)+ 4.20/4.21 multi-stage(见 3.4) |
| 验证 | UR dump 比对 | UR 比对 + **DDR 写回真比对** + numpy 独立三方校验 |

### 3.3 2026-09-07 最新迭代(lsu_op_base,工作区版本已同步到本仓库)

- `bulk_burst_segments()`:末尾整段为 4/8/12 个整 16 拍 burst 时,LDBAXI/STBAXI 会有
  outstanding 未收口的风险 —— 改成「16 拍 + 8 拍 + 8 拍」三段收尾(字节数不变)。
- 两个连续 1-beat LDBAXI 不可靠(后一个可能没搬进 UR 就 complete):bulk 模式下
  `word_count==1` 的输入实际搬 **2 word**(多搬的 guard line 由 4KB 对齐保证安全,
  逻辑形状/计算量/golden 不变)。
- `generate_hbm_data()` 重构为逐端口填充,新增两个虚钩子供子类定制:
  - `fill_input_port_data(name, port, smc_id, rng, out)` —— 按端口名定制数据
    (mul_scalar 用它实现 fp32/fp16 数据 + scalar 广播,见 §4.4);
  - `write_extra_meta_json(os)` —— 追加自定义 meta 字段(dtype / valid_output_bytes 等)。
- `assemble_full_case()` 改为**双版式同时产出**:
  - `smi_iq_inst/stage_{0,1,2}/`(load/compute/store 分目录)+ `stages.md` —— 供
    4.20/4.21 multi-stage TB 消费;compute 为空时**不生成空中间 stage**
    (TB 会等每个非末 stage 的 done/intr,空 stage 会超时);
  - 同时保留 4.19 flat 版式合并(`smc_*/iq_*.hex`)兼容旧 runner。

### 3.4 一键 runner:`run_rtl_test_v3.sh`(2026-09-07 迭代)

自动探测 `RTL_DIR/sim_multi_stage` 是否存在 → 选 multi-stage 或 legacy 4.19 版式。
算子扩展为 `mul | bitwise_and | mul_scalar | select_int`(后两者带参数):

```bash
# 5 步:g++ 编译 gen → gen 生成微指令+HBM+golden+meta → data_list → RTL 仿真(UR 比对) → DDR 真比对
RTL_DIR=/path/to/rtl ./run_rtl_test_v3.sh mul_scalar --seed 42            # 默认 fp32,255 words
MUL_SCALAR_DTYPE=fp16 LSU_WC=255 ./run_rtl_test_v3.sh mul_scalar --seed 42
SELECT_INT_INPUT_WORDS=96 SELECT_INT_OUTPUT_WORDS=32 SELECT_INT_DTYPE=int32 \
    ./run_rtl_test_v3.sh select_int --seed 42
```

环境变量:`LSU_WC`(word 数,≤255)、`MUL_SCALAR_DTYPE`、`SELECT_INT_INPUT_WORDS` /
`SELECT_INT_OUTPUT_WORDS` / `SELECT_INT_SOURCE_OFFSET_WORDS` / `SELECT_INT_DTYPE` /
`SELECT_INT_OUTPUT_BYTES`。

### 3.5 判定机制(独立于 C++ golden 的三方校验)

- **UR 比对**:RTL TB 内部把 UR 各 bank dump 与期望比对。
- **DDR 真比对**(`verify_ddr_v3.py`):读 `meta_v3.json`,对每 SMC 取
  `hbm_result_smc_X.hex` 在 `store_byte_base` 落点的 `out_words` 行,
  与 golden 逐 16B word **bit-exact** 比对 —— 证明「算出来了且真的写回了 DDR」。
- **numpy 三方校验**:从 DDR dump **独立读输入重算**,要求 `numpy == C++ golden == DDR 写回`。
  - mul 的 FTZ 对齐:RTL fp32 乘法把非规格化结果按符号刷成 ±0,跨数量级随机数据频繁触发,
    因此 C++ golden 与 numpy 参考同样做 FTZ,三方在大范围下才一致(golden 更贴硬件真值)。
  - 破坏性测试(篡改 golden / DDR / 输入)均能被抓到,证明校验链路有效。

---

## 4. mul_scalar 算子(重点)

### 4.1 软件语义与数据通路

对每个输入 word 与标量 word 做逐元素乘法(8 SMC 并行,`smc_id_en=7`):

```text
UR r0(r1) ──RtoVR──> VIN0(VIN1) ──> FPMUL(CRU) ──> VOUT0 ──RtoVR──> UR 写回 w0
   (读标量/读输入)          (v4.22 桥寄存器)                (v4.22 桥寄存器)
```

支持 FP32 / FP16:`scalar` 端口按 word 广播同一标量(FP16 时 1 word 内 8 个 u16 全同)。

### 4.2 4.19 / 4.21 版本(直连路线,提交版)

FPMUL 输入/输出在稀疏 RtoVR 上**直连**端口:`UR_RDATA(30/31) → FPMUL_S0/S1(3/4)`、
`FPMUL_D → UVR_WDATA(35)`,排程为 pow 风格:
UR 读 loops=compute;路由 dly=1(读侧)/ dly=6(写回侧);FPMUL dly=2;写回 dly=7。
该路线在 4.19/4.21 仿真验证通过(见 §7 时间线)。

### 4.3 v4.22 FPGA 适配(2026-09-07 迭代,已同步本仓库)

**背景**:FPGA 上的稀疏 RtoVR **无法**把 UR/FPMUL 直接路由到端口 3/4/35;
v4.22 RTL 为此补齐了 VIN0/VIN1/VOUT0 三个**寄存器桥**及其控制入口
(原保留 IQ 槽位 68/69/70)。算子因此改为经桥走:

| 路由(源 → 目的) | 端口 | 说明 |
|------|------|------|
| `UR_RDATA_0 → VIN0_S` | 30 → 14 | 输入(第一源)进 VIN0 |
| `UR_RDATA_1 → VIN1_S` | 31 → 15 | 标量进 VIN1 |
| `VIN0_D → FPMUL_S0` | 11 → 3 | VIN0 寄存出到 FPMUL |
| `VIN1_D → FPMUL_S1` | 12 → 4 | VIN1 寄存出到 FPMUL |
| `FPMUL_D → VOUT0_S` | 3 → 16 | 乘结果进 VOUT0 |
| `VOUT0_D → UVR_WDATA` | 13 → 35 | VOUT0 寄存出到 UR 写回 |

新增常量:`RTOVR_IN_DR_VIN0_D=11 / VIN1_D=12 / VOUT0_D=13`,`RTOVR_OUT_DVR_VIN0_S=14 /
VIN1_S=15 / VOUT0_S=16`,`MUL_SCALAR_IQ_ID_VIN0=68 / VIN1=69 / VOUT0=70`(code bits = 7)。

发码结构变化(相对 4.2):
- 新增 3 个 IQ 队列文件 `iq_68_vin0 / iq_69_vin1 / iq_70_vout0`(`para_width_bits=48`,
  control = `(smc_id_en<<1)|1`);
- **loops 拆两档**:`loops_source`(VIN0/VIN1,随源步进)+ `loops_pipeline`
  (= 步数-1 + CRU 级联补偿 + `kFpgaPipelineDrain=8`,覆盖两级 VIN、FPMUL、VOUT0
  与 SMC CRU 级联的排空);
- 各段 dly 细化:VIN 路由 dly=1;FPMUL_S0/S1 dly=2;VOUT0_S dly=6
  (比 pow 原排程早一拍启动,防止首拍结果被流水复位 0 覆盖);UVR 段先插一条 dly=7 wait;
- FPMUL 主指令 dly=2 → **dly=3**;UR 读段 loops → `loops_source`;
- UR 写回:**4 拍预热**(首拍 dly=7)→ 主写回 loops_write、**dly=4**、带地址自增;
- 每个队列段尾追加一条 code=0 的 **end 归零指令**(`append_end`,写回段 dly=2),
  避免段尾残留被下一级队列错误重放。

### 4.4 FPGA case 定稿:`mul_scalar/finalize_mul_scalar_fpga_case.py`(2026-08-27)

把「RTL 仿真已通过」的 compute stage 改造成 FPGA 版:

1. 把旧版占位队列文件 `iq_rtovr-40/41/42.hex/.txt` **重命名**为 FPGA 名
   `iq_68_vin0 / iq_69_vin1 / iq_70_vout0`,校验 `IQ_ID` 与 data_list 一一对应
   (控制行提到 data_list 最前);
2. UR 写回首条 wait 指令由 RTL 版 `02000060380000000000` 前移两拍改为
   `02000020380000000000`(bitstream 物理流水更深)。

用法:`python3 finalize_mul_scalar_fpga_case.py <case_dir> [--stage 1] [--subq 0]`。

> 2026-09-07 的新发码已直接产出 `iq_68/69/70` 命名(不再有 40/41/42 占位),
> finalize 脚本保留用于把旧格式 case 归一、并对齐上板时序。

### 4.5 验证子类 `mul_scalar_lsu_op`(dtype 钩子的消费方)

- 数据填充:fp32 用 `uniform(-2,2)`;fp16 经 `float32_to_float16`;`scalar` 端口广播;
- **fp16 golden 按 RTL 非 IEEE 行为建模**:输入为零/denormal/最小规格化数时直接给
  `±0`(符号 = 两输入符号异或),其余按 fp16 乘积转换 —— 与硬件一致才能 bit-exact;
- 覆写 `write_extra_meta_json` 输出 `dtype` 与 `valid_output_bytes`(供 verify 按实际
  有效字节比对)。

---

## 5. select_int 算子

部分元素选择:从 `input_words` 的输入里按源偏移选出 `output_words` 写回,支持
fp32 / fp16 / int32(可选 `SELECT_INT_OUTPUT_BYTES` 精确控制有效字节)。
设计上用于处理「真实模型中只取部分元素」的模式(如 varlen/partial-element 场景)。
4.19/4.21 时代即已实现;本次迭代(2026-09)未改动其发码,只经 runner 补了参数化入口。
详细设计见上游 `aiacc/doc/select_int_partial_element_design_v4.19.md`(未归档本仓库)。

---

## 6. llama 真实模型用例提取:`extract_llama_intermediate_cases.py`(2026-07-08)

不跑整个融合算子,而是把「映射到单算子」的中间张量物化成独立验证 case:

- `varlen_fwd`:QK score → `mul_scalar(softmax_scale)` 段;
- `reshape_and_cache_flash`:slot_mapping / index_put 缓存状态检查。

`--run-rtl` 时把 QK-score scale 张量送进 load_store_v3 mul_scalar 的 RTL 流。
llama 长序列按 chunk 切分(如 5160 chunks),由 v4.22 仿真沙箱批量跑(§7)。

---

## 7. 验证结果时间线(证据均为本地日志,含失败记录)

| 日期 | 环境 | 内容 | 结果 | 证据(out/ 下文件) |
|------|------|------|------|------|
| 2026-06 下旬 | 4.19 | mul / bitwise_and 多种子 8 SMC | 8/8 bit-exact(种子 1,2,777,…) | README 记录(上游) |
| 2026-06-26 | 4.21 multi-stage TB | mul_scalar 8smc(4.19 数据转 4.21) | **8/8 PASS**(UR) | `mul_scalar_8smc_v3_421_multistage.log` |
| 2026-06-26 | 4.21 verification_v2 | 同上,4.19 flat 版式直搬 4.21 | 0/8 FAIL → 弃 flat 版式,改 multi-stage | `mul_scalar_8smc_v3_421_converted_rtl.log` |
| 2026-06-26 | 4.21 | llama mul_scalar 片段 | 仿真正常跑完 | `mul_scalar_llama_421_multistage.log` |
| 2026-08-28 | v4.22 RTL(u280_sim_v422) | fresh-seed 8smc mul_scalar | 缺 DDR dump 未过(过程记录,后同结构通过) | `mul_scalar_v422_fresh_seed2026082801*_console.log` |
| 2026-09-04 | v4.22 RTL | llama v422 **r1 smoke**(chunk0000/5160) | **PASS,8 SMC DDR==golden**(255 words) | `llama_mul_scalar_v422_r1_smoke_20260904/` summary.tsv |
| 2026-09-04 | v4.22 RTL | llama v422 **r2 初版**(5 chunk) | FAIL 5/5(sim_rc=ddr_rc=1,~0.4s 即败,case 级问题) | `…_r2_20260904/` 与 `…_retry/` summary.tsv |
| 2026-09-04 | v4.22 RTL | llama v422 **r2 escalated**(时序上探) | **PASS 5/5**(末 chunk 仅部分 SMC 有有效数据) | `…_r2_20260904_escalated/` summary.tsv |
| 2026-09-04 | **U280 板(zwz 服务器,XDMA)** | llama r2 v422 8SMC 5 chunks **真实上板** | **5/5 chunk CASE PASS,40/40 SMC DDR bit-exact 255 lines**;另做 first/second 两轮 reset 重复性检查均 PASS | `u280_fpga_packages/mul_scalar_llama_r2_v422_8smc_5chunks_rtl_pass_20260904_173154.tar.gz`(sha256 2d2009b0…d2d) |
| 2026-09-07 | v4.22 RTL | 当前工作区代码 8smc 回归 | **VCS license 中断,未完成**(待重跑) | `mul_scalar_8smc_v3_rtl.log` |

要点:

- 发码侧 v4.22「VIN0/VIN1/VOUT0 桥」结构最早于 2026-08-26~09-04 在 v4.22 仿真沙箱
  (`/home/juno1023/u280_sim_v422`)验证;9/4 的 r1/r2 PASS 产物与 9/7 工作区代码
  生成的产物逐文件同名同构。
- **U280 板端已实测通过**(2026-09-04):llama round-2 decode 真实 `QK score × softmax_scale`
  (`_vllm_fa2_C::varlen_fwd`,qk_shape=1,32,1148,scale=0.08838834764831845)5 个 chunk,
  fp32、8 SMC,RTL_VERSION=0x00042200、IQ_PACKER_CAP=0x49515031(IQP1)。
  每 chunk 三阶段装载(stage_0 load 2 IQ order [26,27] / stage_1 compute 13 IQ order 含 68/69/70 /
  stage_2 store 2 IQ order [24,25]),逐 chunk `CASE PASS compared_smc=8`,汇总
  `LLAMA_R2_U280_SUMMARY pass=5 fail=0 total=5`。这是**真实模型算子链片段在 FPGA 原型上的端到端实测**,
  而非纯随机数据用例。
- 9/7 迭代(§3.3/§4.3)是面向 U280 bitstream 物理流水的再对齐(写回预热、dly、
  burst 收尾、guard line),**改动后尚未完成仿真回归**(9/7 尝试被 license 中断);
  该轮改动不改变上板包内容(上板包为 9/4 由 RTL 全通过的 case 原样打包,instruction_patch=none)。
- U280 上板的打包/交付流程、版本门(RTL_VERSION)、板上结果判定标准见上游交付文档
  (`~/AIACC_U280算子上板运行交付流程.md`、`~/AIACC_v4.22算子修改注意事项.md` 等,未归档本仓库)。

---

## 8. 本仓库 op/ 快照与上游对应关系

| 本仓库(op/) | 上游(LSy3011/Jianyuan,权威) | 说明 |
|------|------|------|
| `op/load_store/lsu_op_base.{h,cpp}` | `aiacc/SMI_IQ/load_store_v3/lsu_op_base.{h,cpp}` | 2026-09-07 工作区版(未提交上游) |
| `op/load_store/run_rtl_test_v3.sh` | 同上目录 | 同上 |
| `op/load_store/extract_llama_intermediate_cases.py` | 同上目录 | 新增脚本 |
| `op/load_store/mul_scalar/` | `…/load_store_v3/mul_scalar/` | lsu 子类 + gen + **finalize_mul_scalar_fpga_case.py(新增)** |
| `op/load_store/select_int/` | `…/load_store_v3/select_int/` | lsu 子类 + gen |
| `op/实现/mul_scalar/` | `…/SMI_IQ/op/mul_scalar/` | mul_scalar_op.cpp / constants.h 为 2026-09-07 工作区版 |
| `op/实现/{cos,neg,select_int}/` | `…/SMI_IQ/op/{cos,neg,select_int}/` | 与上游一致 |
| — | `…/load_store_v3/{mul,bitwise_and,verify_ddr_v3.py,README.md,out/}` | 框架其余部分与全部日志在上游,未镜像 |
| — | `…/SMI_IQ/op/{add,sub,div,exp,pow,rsqrt,softmax,…}` | 其余算子,未镜像 |

---

## 9. 复现(需上游仓库与对应 RTL 环境)

```bash
# 0) 环境:Jianyuan 仓库 + RTL 目录(4.19:Jianyuan-release-v4.19;4.22:u280_sim_v422)
cd aiacc/SMI_IQ/load_store_v3

# 1) 随机 8-SMC 用例(默认 fp32 × 255 words)
RTL_DIR=<rtl> ./run_rtl_test_v3.sh mul_scalar --seed 42

# 2) fp16 / 自定义宽度
RTL_DIR=<rtl> MUL_SCALAR_DTYPE=fp16 LSU_WC=128 ./run_rtl_test_v3.sh mul_scalar --seed 42

# 3) select_int(96 输入选出 32)
RTL_DIR=<rtl> SELECT_INT_INPUT_WORDS=96 SELECT_INT_OUTPUT_WORDS=32 \
    ./run_rtl_test_v3.sh select_int --seed 42

# 4) RTL 已通过 case → FPGA v4.22 定稿
python3 mul_scalar/finalize_mul_scalar_fpga_case.py <case_dir>/smi_iq_inst --stage 1

# 5) llama 真实模型中间张量提取(--run-rtl 直接送 RTL 流)
python3 extract_llama_intermediate_cases.py --help
```

判定标准:脚本退出码 0 + `V3-DDR-VERIFY Summary: pass=8 fail=0` +
逐 SMC `DDR==golden` 行齐备。
