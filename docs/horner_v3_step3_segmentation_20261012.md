# horner_v3u 步③自动分段验证报告（2026-10-12）

> 用户令五步③：自动分段验证——按实际产物确定容量边界，选择刚超容量的规模，
> 检查尾段、地址及中间状态。对象 = `horner_v3u`（II=6 Horner 长链）。
> 本步范围 = COMPUTE 多段链（ADR-057 分段器 + 段间契约 tb_multi_stage_intr_ack_v1）；
> 统一 LOAD→COMPUTE→STORE 属步④。
>
> 证据分级：【实测】= 当日真实运行/读取；【源码】；【归档】= sha256 锚定产物。

## 0. 结论

**步③通过**：容量边界经 VCS 级校准（单段 n_max=68，七队列恰 128 行）；刚超容量
规模 n=72 由分段器切为 17+1 两段，尾段/地址/中间状态三项检查全部正面通过；
**跨案例一致性：n=72 的 stage_0 与 n=68 单段程序 18/18 个 IQ hex 逐字节一致**
（分段 = 容量界处截断、前缀程序不变）。响亮拒绝边界两点实测均为 config_check
早拒、零产物发布。未改任何生产代码/通用规则/协议常数。

| run_id | n | 形态 | outcome | published |
|---|---|---|---|---|
| hv3u_step3_n68_20261012 | 68 | 单段恰顶格 | GREEN | True |
| hv3u_step3_n72_20261012 | 72 | 两段 17+1 | GREEN | True |
| hv3u_admit_n1024_20261012 | 1024 | bank 恰满（干跑） | UNVERIFIED@VCS_NOT_RUN（预期终点） | False |
| hv3u_reject_n70_20261012 | 70 | 响拒探针 | RED@config_check | False |
| hv3u_reject_n1028_20261012 | 1028 | 响拒探针 | RED@config_check | False |

## 1. 容量边界：构造预测 → VCS 校准

步①构造预测「单段 n_max=68（未校准）」本轮升级为 **VCS 校准**：

| 检查点 | 预测 | 实测 | 结果 |
|---|---|---|---|
| n=68 束缚队列行数 | L=6·(17+4)+2=128（fpadd/rtovr-3/4/5/6/14/vin0 七队列） | 七队列发射恰 128 行，VCS GREEN | ✅ |
| n=68 发射计数 | fpadd issue=7+1+6·21+1=135、ur-w2 issue=141、vin0 issue=129 | 逐拍全等、残尾取指恰 1 | ✅ |
| n=72 处置 | 超单段容量 → 分段而非响拒（ADR-057 语义） | 2 段 m=17+1；stage_0 七队列 128 行、stage_1 32 行=6·(1+4)+2 | ✅ |
| bank 恰满 n=1024 | 256 行 = bank_depth=256 应准入+分段 | 准入，16 段 288 个 iq 文件（干跑，未跑 VCS） | ✅ |

n=68 单锚 c0=34777，violations=0，三区 GREEN（target_lines=17）。

## 2. 尾段检查（n=72 stage_1）

- 迭代覆盖：stage_manifest `iter_range=[17,18)`、`stage_start=17`、`m=1`——
  恰覆盖末 1 个逻辑迭代（第 17 行 = 元素 68..71）【归档】。
- 形态同构：stage_1 fpadd issue=39/39，与步② n=4（同为 m=1）逐拍同型；
  fetch 32/32+1 残尾；18/18 队列 ok【实测】。
- 行数恒等：stage_1 七条 span=4 队列恰 32 行 = 6·(1+4)+2【归档】。
- 尾段写值：y 仅写 line 17，三区比对 bit-exact（见 §3/§4）。

## 3. 地址检查（payload 与 hex 位级双重证据）

**写地址（跨段连续性）**：stage_0 ur-w2 写 ADDR=0..16（17 写、WBE=0xFFFF）；
stage_1 写 ADDR={17}——两段写集**连续且不相交**，并集恰为 [0,18)=n/4 全目标区
【实测，physical_instructions.json】。hex 位级复核（UR_WRITE 布局
VALID|BANK|ADDR|WBE|smc_id_en，modules.yaml:180-188【源码】）：
stage_1 `iq_ur-w2.hex` row 3 解码 BANK=2/ADDR=17/WBE=0xFFFF/smc_id_en=7，
与 payload 层一致【实测】。

**读地址（三路 x 流滞后结构）**：Horner 三级乘法各需一份 x 流，编译器以
不同**滞后**（lag，单位=迭代）复用同一 bank0：

| 流 | 队列 | lag | stage_0 地址序列（节选） | stage_1 活读 |
|---|---|---|---|---|
| x_read_12（mul1 的 x） | ur-r0 | 0 | 0..16 活 + 17..19 死（尾声） | t=4 ADDR=17 |
| x_read_7（mul2 的 x） | ur-r1 | 2 | 254,255 绕回 + 0..16 活 | t=14 ADDR=17 |
| x_read_3（mul3 的 x） | ur-r0 | 3 | 253..255 绕回 + 0..16 活 | t=24 ADDR=17 |

- **stage_1 三条流的活读全部 ADDR=17 = stage_base_word**（manifest 声明 17）；
  hex 位级复核 `iq_ur-r0.hex` 12 个 VLD 行与 payload 层逐一相符【实测】。
- 死读（prologue 绕回 253..255 / epilogue 越目标区 18..20）由活性排除：
  每流读数恰 = R_q = span+m（stage_0：3+17=20；stage_1：3+1=4），
  活读恰 m 个；死读结果不进任何活写——三区 bit-exact 为其值级证据，
  G-addr LIVE 检查（assembler.py 展开分支，未环绕地址语义）为其静态闸【源码】。
  步①风险 U6（ur-r0 回绕）由此从「风险」转「观测+解释+死读排除」。
- 参数常驻：c0..c3 分置 bank 1/3/4/5，两段均 ADDR=0（stride=0）【实测】。

## 4. 中间状态与跨案例一致性

段间检查（n=72 裁决报告 `inter_stage`）【实测】：

- **C1 段内次序**：stage_0 begin=12≺last_write=34926≺ack=34994；
  stage_1 begin=34994≺last_write=41108≺ack=41158，两段 order_ok。
- **C2 无残留写**：两段 residual_writes=0；stage_0→1 为同拍空窗
  （@34994，ADR-057 errata-2026-10-01 已登记：空窗不称排干证明，
  残留排除由 C1+C3+全时线写归因支撑，不依赖该窗）。
- **C3 地址连续**：stage_0 写集期望 17/观测 17、stage_1 期望 1/观测 1，
  set_equal 双真。
- 单锚：两段各自 18 队列共享同一 c0（34777 / 41055）。

**跨案例一致性（本步新增证据）**：n=72 stage_0 与 n=68 单段的 18 个
`smi_iq_inst/stage_0/*.hex` **sha256 全部相同（18/18）**——分段器在容量界处
截断且不改动前缀程序；n=68 侧 18 项哈希清单已锚定（§8），n=72 侧由
stage_manifest 逐文件 sha256 锚定，抽样比对一致（如 iq_rtovr-3=1a7b2632…）【归档】。

三区终态：n=68 GREEN（17 行目标）；n=72 GREEN（18 行目标，zone1/2/3 零坏）【实测】。

## 5. 响亮拒绝边界（干跑探针）

| 探针 | 边界 | 实测拒绝形态 |
|---|---|---|
| n=70 | n%4≠0 | RED@config_check：`TYPE_LAYOUT_UNSUPPORTED`「n_rows=70 不整除 vector_lanes=4——规模越出 V1 布局约束」 |
| n=1028 | 257 行 > bank_depth=256（刚超 1 行） | RED@config_check：`TYPE_LAYOUT_UNSUPPORTED`「静态消耗 257 words > bank_depth=256（规模越界）」 |

两点均**早拒于装配之前、零产物发布、原因字段明确**；与 n=1024（恰满准入）
夹出 bank 容量界恰在 256/257 行之间【实测】。

## 6. 风险登记处置

- U6（ur-r0 3 槽回绕）→ **关闭为已解释现象**：滞后流 prologue 绕回 + 活性排除
  + R_q=span+m 恒等，值级三区 bit-exact（§3）。
- U7（满槽队列 fpadd slots[0,2,4]、rtovr-14/vin0 6/6 槽）→ n=68 恰顶格 +
  n=72 两段 VCS 全 GREEN，探索证据正面；协议模型 II=6 形态仍属
  「探索证据」口径，v3 追加冻结留用户裁定（步②报告 §4 立场不变）。
- U2（II=6 协议常数期望先行）→ 本步 5 个规模点全部逐拍成立，无新常数引入。

## 7. 步④入口条件

1. 统一 LCS 集成：horner_v3u 经 `run_unified_lcs_verify.py`（ADR-061 入口）
   自动生成完整 LOAD→COMPUTE→STORE 程序并比较外部输出；bank 布局已按
   一 bank 一张量设计（步② §1），预期无「bank 窗交叠」阻断。
2. 规模选择：沿用 n=72（双段形态已取证）作步④主规模，必要时加 n=8 小段对照。
3. 已知口径：段间同拍空窗 errata、II=6 探索证据口径，均随步④结论一并保留。

## 8. 证据锚点与复现

- 释放包：`releases/horner_v3u_hv3u_step3_n{68,72}_20261012/`（README+run_report）。
- n=68 stage_0 哈希清单：`verification_v2/artifacts/horner_v3_step3_20261012/n68_stage0_sha256.txt`（18 项）。
- n=72 逐文件 sha256：stage_manifest（`build/v1_runs/horner_v3u/hv3u_step3_n72_20261012/n72/build/stage_manifest.json`）。
- 位级解码复现：payload 源 = 各 stage `physical_instructions.json`；
  hex 源 = 沙箱 case `hv3u_step2_horner_v3u_hv3u_step3_n72_20261012_n72/smi_iq_inst/stage_*/`
  （case_prefix 沿用步② sed 残留 hv3u_step2，目录名 cosmetic，run_id 与内部锚点正确——登记备查）。
- 响拒探针 run：`build/v1_runs/horner_v3u/hv3u_reject_n{70,1028}_20261012/run_report.json`。
- 复跑入口：`python3 verification_v2/tools/run_v1_verify.py --config <对应 yaml>`（本步全部配置 /tmp/hv3u/，关键参数已录于各 run 目录）。
