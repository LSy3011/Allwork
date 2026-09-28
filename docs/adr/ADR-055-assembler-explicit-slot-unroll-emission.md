---
adr_id: ADR-055
title: assembler 显式槽序列展开发射（A1：II≥2 elementwise，单 stage，容量/槽冲突拒绝）
decided: 2026-09-24
last_verified: 2026-09-26
still_holds: **A1 已验收（2026-09-26）**——VCS 三规模（n_rows=8/60/4）FULL GREEN：原生 COMPARE PASSED（8 bank × 256 行 exact ×3）、三区比对全绿（target 区 2/15/1 行 = vout0 有效写，非目标区零违例）、逐拍 assert (i)–(iv) 全过 + 填充/排干拍点名 RTL 实锤；mul 零字节 diff（13/13 byte-equal）+ mul VCS 回归 PASSED + census 36/36 零误报 + pytest 226 passed。证据包 `verification_v2/artifacts/e2e_add_20260926/A1_VCS_acceptance_20260926.md`。A2（stage 分段/真多槽）未验收、不在本状态覆盖内
builds_on: ADR-054（level-0 平坦流 + embed 死区证据链）、ADR-053（VIN/VOUT 绕行）、ADR-050（array-replay 同族病）
hco_review: 2026-09-24 approve-with-notes（must-fix M1–M7、errata E1–E9 已落入本稿；hco 明确「按 must-fix 修订后方可进入实现」）
user_decision: 2026-09-24 两轮评审——①批准启动 A1；②五点订正（顶层算子白名单识别 / 预检表分列与「预测≠实测」/ 地址与填充排干契约先行 / 收紧「已证明」措辞与相位证据来源 / 复现材料覆盖未跟踪文件）；③决策表：N=8 首证 → N=60 容量内验证 → N=4 首尾边界；D（JUMP 侦察）与求解状态分类穿插执行、不阻塞主线
---

# ADR-055 · assembler 显式槽序列展开发射（A1 范围）

> **状态：A1 已验收（2026-09-26）。** hco must-fix M1–M7 + errata E1–E9 全部落码并通过全部既定门槛：
> VCS 三规模（n=8/60/4）原生 COMPARE PASSED + 三区比对全绿 + 逐拍 (i)–(iv) 全过（含首 BODY 拍、
> BODY 严格连续、TERM 拍、垫拍与槽数无关）+ 填充/排干拍点名 RTL 实锤（不再引用 mul 作依据）；
> 门控回归：mul 零字节 diff + mul VCS 回归 PASSED + census 36/36 零误报 + pytest 226。
> 证据包：`verification_v2/artifacts/e2e_add_20260926/A1_VCS_acceptance_20260926.md`。
> **A1 关闭 II2-CADENCE 子项；MULTISLOT / 跨 stage 债（A2 范围）不在本验收覆盖内。**
> **一句话**：把 ADR-054 的「II=1 单槽 + LOOPS 压缩」泛化为「**II≥2 的槽序列显式展开**」——
> 每队列发 `INIT ; 槽序列逐槽重复 R_q 遍（有效 op 行 vld=1 / 气泡行 vld=0）; TERM(jump)`，
> 槽序列长度恒等于 `iq_II`（空槽补 vld=0 NOP）；**槽冲突或容量超限一律显式拒绝，
> 绝不回落 embed 主线**。mul 已证压缩路径与全部既有专用分支原样保留。
> **范围铁律：A1 只交显式展开 + 容量拒绝 + 单 stage 验证；stage 规划、跨段接续、
> 真正多槽重载用例全部属 A2，不在本 ADR。**

## Context

### 1. 证据基线（全部带锚点；hco 2026-09-24 逐条回源复核）

- ADR-054（2026-09-12）已证：mul 全队列 level-0 平坦流 VCS **COMPARE PASSED**（2048/2048 bit-exact）。
- **embed 死区机理（本稿更正归因，结论强于 v2）**：`FETCH_DECODE` 的
  `if(EN_EMBED && ~(&w_embed))`（`smi_iq_iic.v:441`）为真时，内层
  `if(~(|w_embed))`（embed==0）走 lev/end 计数逻辑，**`else`（embed∈{1..6}）只做一件事**：
  `r_next_state = LOOP_PARA_SET`（`:468`）。而 `LOOP_PARA_SET` 的入口条件是
  `if(rdi_data_valid && ~(|w_embed))`（`:529`）——embed∈{1..6} 时**该条件永假、且该状态无 else**，
  故 `r_next_state` 保持旧值、**永久自锁在 `LOOP_PARA_SET`**。
  ⚠ **更正**：`EXE_ARRAY_INST`（`:557`）**不在** embed≠0 的路径上（v2 归因有误）。
  死区不是「序列错一拍」，而是**状态机锁死**——这加强而非削弱死区结论。
  embed==7 因 `~(&w_embed)`=0 整块跳过、走尾部 jump/循环判定 → 仍是**安全哨兵**
  （golden `append_nop_wait` 原生发 embed=7）；我方协议有意加严为**全流 embed==0**。
- 普查基线（2026-09-12，`verification_v2/tools/census_iq_shapes.py`；**36 算子 / 28 解出 / 8 未解**）：
  除 mul / fma_parallel_n 外 **26 个解出算子的产物被报告包含已知问题协议形态（embed 主线），
  不计入 RTL 已验证覆盖**（措辞限定：是否全部卡死、是否仅此一处问题，需逐项验证）。
- **IQ 容量墙**：每队列每子队列 **128 行**（`smi_top.v:22 SMI_IQ_INSTQ_DEPTH=128`，
  四处实例 override `smi_top.v:430/454/525/551`；`smi_iq_inst_queen.v:107`
  `reg r_inst [0:SUB_IQ_NUM-1][0:INSTQ_DEPTH-1]`，`SUB_IQ_NUM=4` @`:27` —— **每子队列独立 128，非共享**；
  `smi_iq_iic.v:300-303`/`:320-322` 执行按 `rdi_iqsid` 单选子队列 + 越界检查）。
  一条流 = 一个子队列 = 一个 stage ≤128 行。
- LOOPS 字段 10-bit，max=1023（`packer.py:5-6`）；ADR-054 语义 `loops=N−1`
  → 单行最多总执行 **1024 次（含首次）**。
- **JUMP 回跳不作为本 ADR 依赖**：`smi_top.v:439/463/529/555` 四处 `.inst_jump_in(32'h0)` 恒零，
  条件比较无迭代驱动源；`smi_iq_iic.v:243 w_jump_comp`（`:242` 是 `w_jump_minus_comp`）
  为编码字段重构（`{1'b1, ~field[6:0]+1}`），非普通 8-bit 补码。
  JUMP 有限循环可实现性降级为独立静态侦察（D7），本 ADR 仅使用 TERM 行的 jump 终止语义
  （ADR-054 D3 已证）。

### 2. 容量与重复次数的真实公式（已回源核对；hco 反算一致）

- 重复次数 **`R_q = N_eff,q + (⌊t_max,q/II_q⌋ − ⌊t_min,q/II_q⌋)`**
  （`assembler.py:86 skip_iterations = t_min//II`；**`assembler.py:258-259`**
  `total_iterations_needed = max_t_global//iq_II + eff_num_iter`，`loops_val = total − skip − 1`）。
  **普适性限定**：`eff_num_iter` 一般等于 `num_iterations`，但 `is_smi_ds_t_dp` 时被钉为 **2**
  （`assembler.py:254-257`）。A1(add) 不触发该分支，故对 add 为 `+N_eff`；
  **引用该式时不得省略 `eff_num_iter` 的来源限定**。
- 未压缩行数 **`L_q = iq_II·R_q + 2`**（`iq_II` = 槽序列长度，**含 vld=0 气泡槽**——
  主线 `for k in range(iq_II)` 显式发 `nop_{iq}_{k}` 行）；仅调度跨度项为 0 时退化为 `II·N+2`。
- **一切容量判断以最终产物实测为准**；本文出现的 `L_q` 一律标注「构造预测」，不得当作实测。

### 3. 预检测量（add，2026-09-24；**预测性质，非最终产物实测**）

方法：`main_verify.py --op add --mode e2e --n-rows {8,60,4}` 三次独立运行，
逐次拷贝 `build/verification_v2/add/physical_instructions.json` 后解析逐队列结构。
归档目录 `verification_v2/artifacts/preflight_add_20260924/`（仓库内，**不落 /tmp**——
本环境 /tmp 会被周期性清理，实测已发生一次数据丢失）。

> ✅ **归档状态（hco E9 —— 2026-09-24 已补齐）**：目录现含三规模 log+JSON 各两份（exit=0）、
> 解析器 `parse_preflight.py` + 输出 `parse_output_20260924.txt`、`README.md`（命令/环境/列义）、
> `git_status_porcelain_20260924.txt` + `git_head_20260924.txt` + `untracked_list_20260924.txt`（20 项）
> + `sha256_snapshot_20260924.txt`（35 项 = 20 未跟踪 + 15 已跟踪已修改）+ 可恢复副本
> `untracked_snapshot_20260924.tar.gz`。下表 `R_q`/`L_q` 与「16 队列」自此可作已归档证据引用。

**测量有效性与其边界**：`loops_val` 与 kernel 槽序列在分支判定**之前**计算（assembler 共用上游），
故现行 embed 产物的 `R_q`、槽占用可**精确预测**展开形行数；
~~最终展开 hex 的实际行数仍待回填~~ —— **2026-09-26 已回填（见表末三列）**，
构造预测与实测逐格相符者即升级为实测。

| n_rows | num_iterations | 队列数 | 槽数 | 双有效槽队列 | R_q 范围 | 预计展开行数 L_q（构造预测） | hex 实际行数 |
|---|---|---|---|---|---|---|---|
| 8  | 2  | 16 | 全 2 | rtovr-14 / ur-r3 / vin0 | 2–5 | ≤12 | **12**（rtovr-14；ur-r3/vin0=10，单槽=6；三方一致 16/16，总 110 行） |
| 60 | 15 | 16 | 全 2 | 同上 | 15–18 | **38**（最大队列 rtovr-14） | **38**（rtovr-14 实测与预测逐字相符；ur-r3/vin0=36，单槽=32；16/16，总 526 行） |
| 4  | 1  | 16 | 全 2 | 同上 | 1–4 | ≤10 | **10**（rtovr-14；ur-r3/vin0=8，单槽=4；16/16，总 78 行） |

**实测回填证据（2026-09-26）**：`bash verification_v2/tools/run_add_e2e_scales.sh` 三规模
全绿（e2e/parse(三方一致判定)/独立检查器/packager 各 exit=0），归档
`verification_v2/artifacts/e2e_add_20260926/{n8,n60,n4}/` 与 `n{8,60,4}_parse.txt`。
「三方一致」= json 行数 = hex 行数 = `II·R_q+2`（II 取 `compiler_schedule.json`，
R_q 由逐队列 t 集合反算），并断言恰 1 TERM、有 INIT、无 HDR、BODY 行数 = II·R_q。
实际迭代数 = 2 / 15 / 1（= num_iterations，无隐藏放大）。
n=60 最大队列 38 ≪ 128 容量墙，**未经任何守卫豁免**；n=256（num_iterations=64）
展开形 ur-r3 L_q=134 > 128，D3 按设计响亮拒绝（DEBT-055-CAPACITY，
`test_g3_add_overcapacity_refuses_loudly` 锁定）。
VCS case 已打包：`compiler_add_{n8,n60,n4}`——VCS 结果仍属待填。

（`num_iterations = n_rows // vector_lanes`，add fp32 vec4 → `vector_lanes=4`；
`pipeline_runner.py:31` 定义、`:114` 计算。`compiler_schedule.json` 抽样反算一致：
rtovr-14 两 op t=3/8、N_eff=2 → `hdr_rtovr-14 loops=4` → R_q=5 = 2+3 ✓。）

直接设计后果：

1. **闸门条件措辞更正（hco 建议）**：`iq_II >= 2` 时 ADR-054 平坦分支**恒不适用**
   （它要求 `iq_II == 1`），故「`len(kernel_map)==1` 会把 add 拒掉」的说法不准。
   准确表述：**展开分支必须接受 1 或 2 个被占用的槽**；`len(kernel_map)` 既非必要也非充分的
   闸门依据，真正的闸门是**顶层算子白名单**（D0）。
2. **span≠0 是常态**：rtovr-14 span=3（三个规模下稳定）；naive `II·N+2`=32 vs 预测 L_q=38。
3. add 为 vec4；**mul 规模（n_rows=256 → 64 迭代）时 rtovr-14 的 L_q≈2·67+2=136 > 128**
   → N=60 内舒服，全 bank 规模须等 A2 分段。
4. 队列构成随规模/调度变化（普查 num_iterations=1 时 add 为 17 队列、本测量 16 队列）——
   **队列集合是调度函数，必须逐次实测，不得写死。**

### 4. kernel_map 槽冲突隐患

`assembler.py:261` `kernel_map = {op.global_t % iq_II: op}` 为字典推导——两 op 落入同槽时
**静默覆盖**前一个 op。展开形把槽序列直接物化为行，冲突后果从「sim 里丢一拍」升级为
「产物少一行有效指令」。必须显式拒绝（D1）。
**该检测同时补到 embed 主线**（现存静默覆盖不止影响新分支；hco 建议）。

### 5. 本 ADR 提出的构造及其适用范围（措辞限定）

已证活流共性 = **embed==0 + 行按发射序逐拍执行 + vld=0 行=气泡**（mul 平坦 / LDB 显式N排 /
shiftup 16 行 TB 实证）；唯一证死形态 = embed∈{1..6} 请硬件做 array 循环（且为锁死，见 §1）。
循环只剩两个已证住所：单行 LOOPS 压缩（mul 形态）与编译期展开。
**本 ADR 提出的是把 mul 退化点推广的构造；A1 只验证 add 所覆盖的槽形态**
（单有效槽+气泡、以及双有效槽各一种），**不主张「对任意槽序列已成立」**。

**边界声明**：「一行对应一拍」在 mul（单队列 3 行）与 shiftup（16 行 TB）上成立；
对 add 的 16 队列 × II=2 × 多遍展开形态，**逐拍相位仍未经取证**——
A1 的 bit-exact 只证明功能与数据路径正确，**不证明节拍**（见 D4/D5）。

**填充/排干是 A1 的全新面（hco must-fix M6a）**：mul 平坦形单 op 即自身 `t_min`
→ `relative_skip ≡ 0`（`assembler.py:86/300`），**从无填充/排干拍**。
故展开形的填充/排干语义**没有任何既有 VCS 背书**，其正确性由 A1 首次承担，
不得以「mul 已证」类比。

## Decision

### D0：范围、顶层算子门控与白名单语义

**新增「显式展开分支」**，与 ADR-054 压缩分支并列（均位于各专用快道 `continue` 之后）：

| 条件 | 去向 |
|---|---|
| `is_smi_ds_t_dp` / stream_beats / explicit_descriptor / two-gather CRI | 既有专用分支，不动 |
| `iq_II == 1 and len(kernel_map) == 1` | **ADR-054 压缩平坦形（mul 路径），不动** |
| `iq_II >= 2` 且 **规范化后的顶层算子 ∈ 白名单** | **本 ADR 展开分支**（槽冲突/容量超限 → 拒绝） |
| 其他一切 | 既有 embed 主线，不动（已知问题形态，按 P0 口径不计入 RTL 覆盖） |

**门控必须识别顶层算子身份，禁止推断**：

- `PhysicalAssembler.assemble()` 现签名只有 `(lir_stream, II, num_iterations)`
  （**`assembler.py:25`**），**无顶层算子身份**；add 的 ur-/vin/vout/rtovr 队列的 `op_type`
  并不都是 `"add"`（ur-r3 两条 `UR_READ` 等，`compiler_schedule.json` 实测）。
- 新增显式入参 **`top_op: Optional[str]`**，由编译入口传入可信标识。
  **禁止**靠 trace 名称、`op_type` 是否含 `FPADD`、unit_id 等底层微指令特征推断顶层算子。
- 白名单常量留在 assembler（`A1_UNROLL_ALLOWLIST`，不碰 hw_config）；测试与普查工具的
  调用点显式传 `top_op`（或 `None` = 不启用展开分支）。

**接线两个静默陷阱（hco must-fix M2/M3，实现必须先修）**：

- **M2 取值时点**：`pipeline_runner.run_full_stack(..., op_name=None)`（`:21`）的兜底赋值在
  `:140-141`，**晚于** `:120` 的 `assemble()` 调用。若在 `:120` 直接传 `top_op=op_name`，
  凡调用方未显式传 `op_name` 者一律得 `None` → **静默落回 embed 主线**。
  修法：把兜底赋值提到 `:120` 之前，或在 `:120` 直接传 `operator.__class__.__name__.lower()`。
- **M3 键规范化**：`tests/integration/test_smt_hw_parity.py:67` 传
  `op_name=f"hw_parity_{op_name}"`（该文件 `:102`/`:114` 跑的正是 add！），
  精确匹配下 `"hw_parity_add" ∉ {"add"}` → 又一条静默落回 embed 的路径，
  而 `hw-parity` 是 CLAUDE.md 定的「新算子物理验收」通道——**A1 验收会在不知情下测错产物**。
  修法：定义算子身份**规范化契约**（剥离已知前缀，如 `hw_parity_`；或改由调用方传显式 id），
  并对「应命中白名单却未命中」**loud-fail**（raise），不得静默降级。

**白名单语义与扩张政策（避免「先通过才能启用」的循环）**：

- 条目带状态：**`experimental`（实验启用，允许发射以取证）→ `verified`（VCS 通过后正式纳入）**。
  取证前即可发射，故不存在循环依赖。**状态字符串须为闭集枚举并在导入期校验**
  （否则一个拼写错即静默降级，hco 建议）。
- A1 初值 `{"add": "experimental"}`；**「add」应表述为「本轮唯一待验证的新增算子」**，
  不写成「唯一被 VCS 验证的算子」。
- 扩张规则：每新增一个 `experimental` 算子，须先向其发出该算子的 VCS 取证计划；
  取得三区报告后方可转 `verified`。扩张属「扩大算子验证」阶段，不在 A1 内。

**「分支确已生效」自证（hco must-fix M4，本 ADR 最关键假绿口）**：
sim 对 embed/展开两形态**同构建模**（`assembler.py:280` 注释自陈「差别只在 RTL」），
故 M2/M3 这类静默回落会让 `--op add --mode e2e` **全绿**。而现存守卫⑭
`g14_flat_stream_protocol`（`tests/unit/test_rtovr_sparse_guards.py:173`）**只跑 mul**
（`test_g14` @`:387` 硬编码 `--op mul` + `build/.../mul` 路径），对 add 零覆盖。
故 D4(a) 的「产物审计」必须**编码为强制跑点**，不得停留在文字。

A1 明确不做：stage 规划与分段、多槽重载用例（长 op 序列同 IQ）、矩阵类、LDB/STB、
性能优化、hw_config 任何数值变更。

**「其他算子产物零字节变化」是待回归验证的目标，不是既成事实**——列入 A1 验收项（D5 附加门槛）。

### D1：槽序列构造与冲突拒绝

展开分支内按槽构造，**不得**使用字典推导覆盖：

```
slots[k], k ∈ [0, iq_II)：
  - 恰一个 op 落入槽 k（global_t % iq_II == k） → 有效行
  - 无 op 落入槽 k                            → vld=0 气泡行
  - ≥2 个 op 落入槽 k                         → ValueError（显式拒绝，禁止覆盖）
构造完成后断言 len(slots) == iq_II。
```

分支判定用**显式 if/elif 链内 raise**，**不得有任何 try/except 或「失败即落 embed」的兜底**。

### D2：展开产物形态、地址契约与填充/排干语义（契约先行）

**产物形态（hco must-fix M5：行规格须补全 `abs_t` 与 `embed_end`/`inc_embed`）**：

```
INIT  (vld=0, level=0, loops=l_init_pad, embed_end=0, inc_embed=0)
      # l_init_pad := l_init = skip·iq_II + 1（assembler.py:87；平坦形 :294 正取此值）。
      # 主线 INIT 为 loops=l_init−1（:237）——差异来源 = 平坦/展开形无 HDR 占位拍，
      # 需多垫 1 拍以保持首 fire 相位（ADR-054 实证：否则全流提前 1 拍）。
      # 多行展开下的相位对应仍属待验证假设（D4 b-2 / D5 裁决）。
展开槽序列 × R_q 遍（j = 0..R_q−1）：
  有效 op：vld=1, level=0, loops=0, inc=0, dly=（域校验后编码，见下）,
           embed_end=0, inc_embed=0, abs_t = l_init+1+k+j·iq_II
  气泡：  vld=0, level=0, loops=0, inc=0, embed_end=0, inc_embed=0, payload={},
           abs_t = l_init+1+k+j·iq_II
TERM  (vld=1, level=0, loops=0, jump=1, code=0, payload={},
       embed_end=0, inc_embed=0, abs_t = l_init+1+iq_II·R_q)   # ADR-054 D3 契约不变
```

- **`abs_t` 必须显式给定**：否则 TERM 会被 emitter 的 abs_t 排序排到中间（`emitter.py:62/138`）。
- **`embed_end=0` 全行**：主线对 `k==iq_II-1` 无条件置 `embed_end=1`（`assembler.py:366`，含 nop 行）——
  展开形**照抄即错**（每遍末行都会触发 `:441-461` 嵌套 loop-end 判定）。
- **`inc_embed=0` 全行**：主线 body 行为 `inc_embed = op_mask`（`assembler.py:343/347`，
  memory IQ 且 stride≠0 → 1）。展开形 level-0 行若置 1 当前**无害**（`smi_iq_iic.v:679`
  在 `case(w_embed)==0` 下清零 lev1..6 计数，故 `w_lev1_addr_offset`@`:265` 恒 0），
  但这是**依赖副作用的安全**，规格写死 0，并加实现注记「该行不得走 op_mask 逻辑」。

**地址/相位契约（必须先定义；检查器只负责检验契约，不能替代契约）**：

记 `skip = t_min_iq // iq_II`（`assembler.py:86`），`orig_offset_k = layout.resolve(logical_addr_k).offset`：

1. **槽重复序号** `j ∈ [0, R_q)` ↔ 全局调度迭代 `τ(j) = skip + j`。
2. **op 的调度迭代偏移** `relative_skip_k = t_k // iq_II − skip`（≥0；
   现行实现 `assembler.py:300`；`:299` 是 `iq_start_t`）。
3. **逻辑数据迭代号**：op 第 `i` 次数据访问地址 = `orig_offset_k + stride_k·i`；展开形第 j 遍的槽 k
   行访问 `i = j − relative_skip_k`。
   **烘焙地址（本稿更正，hco must-fix M1）**：
   ```
   A_k        = (orig_offset_k − stride_k · relative_skip_k) mod bank_depth
   ADDR(k, j) = (A_k + stride_k · j) mod bank_depth
              = (orig_offset_k + stride_k · (j − relative_skip_k)) mod bank_depth
   ```
   ⚠ **不得使用 `iq_max_stride`**：v2 写的 `A_k + j·iq_max_stride` 与同句前半
   `logical_addr_k + stride_k·i` **互斥**（两者相等仅当 `stride_k == iq_max_stride`）。
   `iq_max_stride`（`assembler.py:89-92`）是**压缩形单 INC 寄存器的遗留**（`:303` 用它填 `inc=`）。
   add 恰好全 stride=1（ur-r3/r0/w0）故不发作，但作为契约它会在 A2 的任意多 stride
   memory IQ 上**静默错址**。
   **前置守卫**：同一 IQ 内所有 memory op 的 `stride` 必须相等，否则拒绝
   （压缩形同样隐含依赖此前提，此处显式化）。
4. **三段有效性**：
   - `j < relative_skip_k`（**填充阶段**）：该行照发（硬件上该单元确被 issue），地址落在
     op 逻辑起点之前的预卷区。**这是 A1 的全新面，无既有 VCS 背书**（见 §5）。
   - `relative_skip_k ≤ j < relative_skip_k + N_eff`（**有效阶段**）：正常数据访问。
   - `j ≥ relative_skip_k + N_eff`（**排干阶段**）：地址越过数据区尾。
   - 对 `stride==0` 的驻留型 op，填充/排干阶段地址恒等于 `orig_offset`，不存在越界。
5. **越界语义**（hco 2026-09-24 裁定，本稿采纳）：
   - 烘焙期施加 `mod bank_depth` = **bank 内合法环绕**：结构上成立——UR_READ 的 `ADDR` 为
     **8 bit**（=256=bank_depth）、`BANK` 为**独立 3 bit 字段**（`hw_config/modules.yaml:176-177`），
     且 RTL 地址算式 `w_operate_micro_addr = base + Σ inc·cnt`（`smi_iq_iic.v:264/272`）
     **不携带 bank** → **INC 步进数学上不可能跨 bank**。
   - ⚠ **撤回 v2 的 golden 引证**（hco must-fix M6b）：v2 称「golden ur-r0 `inc=1 loops=254`
     over depth=256 同样环绕」。**已证伪**：golden `mul_scalar_boundary_fp32_w255` 的
     `iq_ur-r0.hex` 行 1 解码为 `INC=1 LOOPS=254 BANK=0 ADDR=0`（`iq_field_diff.py --dump`）
     → 地址 0..254，**不环绕**（该 case 名即 255 词边界）；扫描仓库内 67 份含 `iq_ur-r0.hex`
     的 case，**无一 ADDR+INC·LOOPS ≥ 256**。故「环绕」目前**无 golden 实例**，仅余结构论证。
   - **域的边界规则**：判据措辞由「地址不再唯一」更正为**别名判据**（步长均匀时 R_q 个地址
     仍两两相异，`== bank_depth` 时原判据不成立）。A1 取**保守规则**：
     **`stride_k · R_q ≥ bank_depth` → 拒绝**（`>` 与 `==` 同拒），
     理由：恰好整圈意味着首/尾地址在模意义上重回本 tensor 活动区，填充/排干拍「由哪个 j 产生」
     不再可由地址判别；A1 的 add 离此边界极远，保守零成本。规则**按读写方向分列**：
     读（UR_READ）是「无害但语义歧义」，写（UR_WRITE）是「**覆盖他 tensor**」的硬错
     （`ur-w0` 若空间 >bank 必须硬拒）。
     DEBT-055-WRAP-AMBIG **收窄为「`== bank_depth` 的许可性判定」，留 A2**。
6. **字段域先在编码前校验**：`dly ∈ [0,7]`（`BITS_DLY=3`）、`loops/INIT loops ∈ [0,1023]`——
   越界一律 `ValueError`。**禁止以 `& 0x7` 之类的静默掩码代替域检查**
   （现行 `assembler.py:301` 属实现细节，展开分支改为显式校验后编码）。
   **INC 域限定（hco errata E6）**：4-bit 二进制补码 **仅对 compute IQ 成立**；
   LSU（IQ 24-27 / LDB·LDBAXI·STB·STBAXI）为 **13 bit**（`smi_iq.v:58`；
   `packer.py:15-19 ISA_V5_HEADER_LSU`）。ADR-054 的 `iq_max_stride > 7` 守卫
   （`assembler.py:286-290`）对 LSU **过严**（安全方向，保留）；域校验表须按 IQ 类型分列。

**INC**：展开分支恒 `inc=0`（地址全部烘焙）；ADR-054 的 INC 域守卫继续只作用于压缩分支。

### D3：容量拒绝

发射前对每队列**实算 `len(emitted_rows)`**（而非重算公式，防公式/实现漂移；
hco 建议），与 `L_q = iq_II·R_q + 2` 的构造预测互校；任一队列实算行数 `> 128` → `ValueError`
（指明队列名与数值），挂 **DEBT-055-CAPACITY**（A2 分段的前置）。**永不静默回落 embed 主线。**

### D4：守卫⑭升级与相位证据来源（区分静态契约与 RTL 相位）

- **(a) 产物审计（必须编码为强制跑点，不可停留在文字）**：
  ① 全流 `level == 0`（**读 hex 字段 `EMBED_LOOPS`** = `SMIHeader.level`，`lir_op.py:33`；
  ⚠ **不得读 `header["EMBED"]`**——`packer.py:4-8` 无 EMBED 位，`SMIHeader.embed` 是
  **从未进 packer** 的字段，按它取值会**恒真假绿**；另注意 `INC_EMBED = inc_embed or embed`
  @`lir_op.py:27`，hex 侧字段名一律以 `packer.py` 的 `ISA_V5_HEADER` 为准）；
  ② 恰一条 TERM 且居末；③ 各字段合法域；④ **逐队列实际行数 == L_q 预算**；
  ⑤ **M4 闸门**：把 `g14_flat_stream_protocol` 泛化并**在 add 上强制跑**
  （新增 `test_g14_add`，不替换 mul 用的 `test_g14`），并记录 add 的**实际选中分支**
  （展开 / embed 回落各几队列）。
- **(b-1) 静态契约对照**：独立工具 `verification_v2/tools/emit_expected_fire_sequence.py`，
  输入 = solver 调度 + hw_config，**不 import assembler 任何内部函数**，
  独立推导每行的 `iq / abs_t / vld / level / loops / inc / dly / BANK / ADDR`，与产物逐行比对。
  **该对照验证的是「发射契约」（次数/顺序/地址/相位编码），比较对象是产物行序，不是 RTL 事件。**
  hco 提醒相关性误差：**工具须从 tensor 布局 + 梯形图独立推
  `(orig_offset_k + stride_k·(j − r_k)) mod 256`，再与产物逐行 (BANK, ADDR) 比对**——
  这样才抓得住 M1 类错误（两边同抄 `iq_max_stride` 就两边同错）。
- **(b-2) RTL 相位证据**：任何关于「哪一拍发生什么」的结论，必须来自 **VCS trace**
  （TB 捕获 fire/valid 事件或波形）。**A1 的三区 bit-exact 报告不构成逐拍相位证明**。
  hco 主张**将逐拍 trace 从「如需主张」上调为 A1 验收项**（本 ADR 采纳）：
  对一个多 op 队列（ur-r3 或 rtovr-14）与一个单有效槽队列，逐拍抓 row-index / `rdi_addr` /
  `rdi_data_valid`，断言 (i) 槽 0 首行落在 `l_init+1` 拍；(ii) `iq_II·R_q` 行**严格连续无空隙**；
  (iii) TERM 落在 `l_init+1+iq_II·R_q` 拍；(iv) 单槽队列同法，验证垫拍与槽数无关。
- **(c) 变异测试**：槽冲突必拒、气泡被误置 vld=1 必被抓、行数超预算必拒、TERM 缺失必被抓
  （在 `tests/unit/test_rtovr_sparse_guards.py` 的 m14/m15 同文件新增）。

### D5：验收（VCS 实体；三区比对报告）

执行顺序（`--n-rows` 控制；`num_iterations` 以运行实测记录）：

1. **N=8 首证** → 2. **N=60 容量内验证**（预测最大 L_q=38）→ 3. **N=4 首尾边界**
（INIT 相位与排干边界专项；R_q=1 时展开形退化为最小流）。

**三区比对报告**（每个规模必附，缺一不可）：

- ① 预期有效输出区域的 bit-exact 结果（区域行数明示）；
- ② 全 bank（2048 行）比对结果（沿用 mul 口径；2048 = 8·256，`hw_params.yaml:8-9`）；
- ③ 非目标区域是否保持预期值——小 N 用例尤其防止「大片未写区域相等」掩盖计算覆盖不足。

**填充/排干拍点名（hco 建议）**：报告须显式指出哪一拍属填充、哪一拍属排干
（预测：N=8 的 `x_read_1` 有 `r_k=2` 填充拍；`alpha_read_3` 有 `r_k=0`、span=2 → 排干 2 拍），
否则越界/填充语义仍是空白。

比对脚本复用 mul 的独立比对方法（直接读 hex，不经 assembler）。

附加门槛：**mul 回归不破** + pytest 全绿 + 守卫全绿 + **其他算子产物零字节变化的 diff 审计**
（D0 目标项的验证）+ **D4(a) ⑭-on-add 分类记录** + **D4(b-2) trace 结果**。

**本文所有 VCS 结果与 hex 实际行数一律留空待回填，不预填结论。**

### D6：基线快照与可复现性

A1 起步时重跑普查并入库快照（目录 `verification_v2/artifacts/`，**不落 /tmp**），须含：

- **代码状态**：`commit` + **`git status --porcelain` 原样输出** + 工作区 diff 内容哈希；
  但 diff 哈希**不覆盖未跟踪文件**，而本仓库相关未跟踪文件**远不止 v2 所列**
  （hco E8；实测 20 项），须**由 `git status --porcelain` 自动生成清单**而非手列，至少含：
  `tests/unit/test_rtovr_sparse_guards.py`（**⑭ 守卫本体**）、`tests/unit/test_stb_schema_regression.py`、
  `verification_v2/tools/` 下 10 个脚本（`census_iq_shapes` / `decode_iq_stream` / `gen_rtovr_allowed` /
  `iq_field_diff` / `package_vcs_case` / `stb_emit_accept` / `stb_guard_mutation` / `stb_pack_dryrun` /
  `validate_rtovr_golden_parity` 等）、`src/frontend/rtovr_bypass.py`、
  `hw_config/specs/units/{vin0,vin1,vout0}.yaml`、`sim/smc_simulator/units/reg_pipe_unit.py`、
  `docs/adr/ADR-052/053/054/055*.md`。
  快照**必须附每项的内容哈希（sha256）与可恢复副本（tar）**。
- **运行命令**、**hw_config 版本**、**solver 参数**（MII / MAX_II / 超时）、逐算子结果表。
- **普查快照入库**：`census_iq_shapes.py` 的输出（含 `--op add` 的 17 队列原始行——注意这是
  普查工作负载 `num_iterations=1` 下的 17 队列，**不等于** §3 预检三规模的 16 队列，两者不得混引）
  必须落盘归档——现行输出 `/tmp/census3_progress.txt`（`census_iq_shapes.py:17`）已被清理，
  重建前 §1 的普查数字与 D7 的 8 未解清单均无法比对。
  ✅ **2026-09-24 已入库**：`verification_v2/artifacts/census_iq_shapes_20260924/`
  （stdout 全量 + 36 算子汇总表 + README；28 解出 / 8 UNSAT 集合不变，
  唯一漂移 = mish II=11→9，引用以快照为准）。

### D7：并行短动作（穿插执行，不阻塞主线）

- **D（JUMP 有限循环可实现性）静态侦察**，时间盒半天，检查清单：
  ① `inst_jump_in=0` 下 `w_jump_judge`/`w_jump_symbol` 还能否产生「循环 N 次后退出」；
  ② `r_jump_code` / `jump_len` / overflow-underflow 计数器的驱动来源；
  ③ 每次回跳推进数据地址的通路是否存在；④ LOOPS 之外是否还有独立循环计数源。
  **找不到「有限退出 + 地址推进」机制即关闭，不投 TB。**

  > **D 静态侦察结论（2026-09-24，bypass 通行，提早完成，不投 TB）**——逐项回 `smi_iq_iic.v`：
  > ① **退出源不存在**。jump 比较三路（GT/EQ/LT，`:354-408`）输入 `inst_jump_in` 四处实例
  > 恒 `32'h0`（`smi_top.v:439/463/529/555`）→ 比较结果只由行内常量 `w_jump_compare_val`
  > 决定，**不随迭代变化**；真 → `r_jump_code = w_jump_comp`（回跳），假 → `8'd1`（顺序+1）。
  > 队空终止 `w_siq_is_empty = (r_inst_siq_cnt+1) >= inst_amount_siqX`（`:300-303`）依赖计数
  > **增**到装载行数，而每跳 `r_inst_siq_cnt += r_jump_code`（`:815/:824/:1189`），纯回跳段
  > 每 pass 净增量 = 0，**永远达不到**；越界只触发 `error_iic`（`:305-315` overflow/underflow
  > 标志，上抛 `smi_iq.v:172/304`），是**错误不是退出**。判据 `2'b11`（`w_jump_last`，`:263`）
  > 为行内静态字段，首遇即终（即 TERM 语义），不构成「N 次后」。
  > ② **无跨跳单调地址推进**。地址偏移 `w_lev0_addr_offset = w_inc * r_embed_lev0_cnt`
  > （`:263` 区连赋值），`r_embed_lev0_cnt` 只在**单行 repeat** 的 `LOOPS_CTRL` 内
  > `:787-791` 递增并对 `(loops+1)` 回卷；跳回复放后计数按自身相位继续周期循环——
  > 没有任何状态把「第几次跳迭代」映射到地址，重放行每 pass 触碰**同一地址集**。
  > ③ **回跳有 1 拍气泡**：`JUMP_ADDR`（`:576-585/:1180-1189`）送数暂停一拍
  > （注释 `do not send jump data to SMC`）。
  > ④ **LOOPS 之外无独立循环计数源**：`r_loops_cnt`（`LOOP_CYCLE :586-598`，单行 repeat 专用）
  > 与 `r_embed_lev*_cnt`（embed 死区路径，A1 不用）；`r_inst_siq_cnt` 是装载进度计数非循环控制。
  > **结论：JUMP 只能表达「顺序+1」或「恒回跳死循环」或「立即终止（judge=11）」三种静态行为，
  > 无法表达有限循环 → D 关闭；A1/A2 循环住所 = 编译期展开（主线）+ 单行 LOOPS repeat。**

- **8 个求解失败限时分类**（addcdiv/elu/gelu_approx/hardswish/log2/matmul/matmul_parallel_n/selu），
  时间盒半天，产出**两列表**（路径 × 结果**互不互斥**）：

  | 路径 | 求解结果 |
  |---|---|
  | configurable / manual / 其他专用路径 | SAT / **范围内全部 UNSAT** / UNKNOWN 或 timeout / 异常 / 未调用求解器 |

  表述限定域：「在指定 lowering、约束模型与 II 搜索范围内，每个候选均由求解器返回 UNSAT」——
  **不证明算子在所有实现方式下不可调度**。达到时间上限记未完成状态。

## Consequences

### 正面

- **A1 若通过，将证明**：add 配置下 **II=2、包含「单有效槽+气泡」与「双有效槽」两类队列的
  单 stage 展开发射**取得独立 RTL 正确性证据（已覆盖基本多槽机制），
  且超容量情形明确拒绝、mul 保持通过。
- 拒绝机制（D1 槽冲突 / D3 容量 / D2-6 域校验）杜绝「静默生成已知问题形态」类假绿；
  M4 闸门杜绝「静默回落 embed 却全绿」类假绿。
- 三区报告 + 独立对照工具 + 逐拍 trace + 基线快照 = 论文验证章的可复现材料。

### 风险 / 债务

- **INIT 垫拍推广假设可能证伪**：mul 的 +1 规则平移到多行展开未经证明；
  且展开形 INIT 后接的是 `loops=0` 槽行（`EXE_EMBED0_INST → FETCH_DECODE`），
  与 mul 平坦形（INIT 后接 `loops>0` op 行）是**不同状态迁移**，垫拍**数值**不能仅凭类比成立。
  若 N=4 边界用例或 D4(b-2) trace 证伪，回本 ADR 修订垫拍规则（不静默改代码）。
- **填充/排干为新面**（§5）：无既有 VCS 背书，A1 首次承担。
- span 使 L_q 偏离 naive 公式（预测 rtovr-14 偏差 +6 行）；容量判断只能信最终实测。
- **挂账**：DEBT-054-MULTISLOT（A1 只覆盖 ≤2 槽占用；**A2 的新增价值 = 跨容量分段 +
  另一算子上更复杂的序列与相位验证**）、DEBT-054-DUALSRC、gated golden 两偏差、
  DEBT-055-CAPACITY（新增）、DEBT-055-WRAP-AMBIG（新增，已收窄为「`== bank_depth` 许可性判定」，
  留 A2）。
- A2 的多槽用例优先选**已有可调度证据**者（如 manual 路径 8/8 sim bit-perfect 的
  `matmul_parallel_n`），但**不得仅凭 sim 通过预先承诺其 VCS 结果**。

### 不做（本 ADR 范围外）

- stage 规划 / 跨段分段 / 段间状态保留（A2）；多槽重载用例实收（A2）；
- LDB/STB 链路、矩阵类算子、JUMP 语义依赖、性能优化（C）；
- hw_config 任何数值变更（本 ADR 零 yaml 改动）；Python 模拟器建模改动（同构建模已足够）。

---

## 附录：v2 → v3 更正清单（hco 2026-09-24 复核意见）

**errata（与源码不符，已逐条回源订正）**

| # | v2 表述 | v3 更正 |
|---|---|---|
| E1 | `total_iterations_needed` @`assembler.py:240-241` | 实际 **`:258-259`**；且为 `max_t_global//iq_II + eff_num_iter`，`eff_num_iter` 在 `is_smi_ds_t_dp` 时钉 2（`:254-257`） |
| E2 | `relative_skip_k` @`:299` | 实际 **`:300`**（`:299` 是 `iq_start_t`） |
| E3 | `assemble()` @`:24` | 实际 **`:25`** |
| E4 | `w_jump_comp` @`smi_iq_iic.v:242` | 实际 **`:243`**（`:242` 是 `w_jump_minus_comp`） |
| E5 | 死区链 `:441→:468→:457/:557` | **机制归因更正**：`:557 EXE_ARRAY_INST` **不在** embed≠0 路径上；真实机制 = `LOOP_PARA_SET`（`:528-575`）入口条件以 `~(\|w_embed)` 为前提、embed∈{1..6} 时永假且**无 else → 自锁**（结论加强：锁死，非序列错） |
| E6 | 「INC ∈ [−8,7]（4-bit 补码）」普适 | **仅 compute IQ**；LSU（IQ 24-27）13 bit（`smi_iq.v:58`；`packer.py:15-19`） |
| E7 | D4(a)「全流 embed==0」按 `EMBED` 取值可核 | 序列化键名是 **`EMBED_LOOPS`**(=`SMIHeader.level`)；`SMIHeader.embed` 从未进 packer → 按 `EMBED` 取值**恒真假绿** |
| E8 | 未跟踪文件清单（4 类） | **非穷尽**（实测 20 项，含 ⑭ 守卫本体与 10 个 tools 脚本）→ 改由 `git status --porcelain` 自动生成 |
| E9 | §3 称归档含「命令、源文件哈希」及三次运行 | 实测归档**仅 2 文件**（nrows8）→ **2026-09-24 已补齐**：三规模 log+JSON、解析器+输出、README、git 快照、35 项 sha256 与 tar 副本全部入库（见 §3 归档状态块） |

**must-fix（已落文本）**

| # | 内容 | 落点 |
|---|---|---|
| M1 | 地址式不得用 `iq_max_stride`，改 `A_k + stride_k·j`；补「同 IQ stride 必须相等」前置守卫 | D2-3 |
| M2 | `top_op` 取值时点：`op_name` 在 `pipeline_runner.py:120` 可能仍为 `None` | D0 接线陷阱 |
| M3 | 白名单键规范化：`hw_parity_add` 不命中 + 应命中未命中须 loud-fail | D0 接线陷阱 |
| M4 | 「分支确已生效」闸门：⑭ 泛化并强制跑在 add 上 + 逐队列行数 == L_q | D4(a)⑤ / D0 |
| M5 | 行规格补 `abs_t`、`embed_end=0`、`inc_embed=0`（含不得走 op_mask 的注记） | D2 产物形态 |
| M6 | 撤回/补证两处引证：(a) mul 平坦形无填充排干拍 → 填充是新面；(b) golden 环绕引证已证伪（ADDR=0） | §5 / D2-5 |
| M7 | 定义 `l_init_pad := skip·iq_II + 1` 并说明与主线 `l_init−1` 的差异来源 | D2 产物形态 |

**已采纳的建议（非阻断）**：别名判据 + `≥ bank_depth` 保守拒绝 + 读写分列（D2-5）；
白名单状态闭集导入期校验（D0）；普查工具传真实算子名并输出实际选中分支（D0/D4）；
容量判断对 `len(emitted_rows)` 而非重算公式（D3）；逐拍 trace 上调为 A1 验收项（D4 b-2）；
`len(kernel_map) != len(ops)` 冲突检测补到 embed 主线（§4）；§3 闸门措辞更正（§3-1）。

---

## 附录 B：A1 实现落点与实现期 errata（2026-09-24，用户批准后落码）

**实现期 errata（回源订正，须 hco 复核）**

| # | 本文原行文 | 落码版本 | 理由 |
|---|---|---|---|
| I1 | D2-3 前置守卫：「同一 IQ 内所有 memory op 的 `stride` 必须相等，否则拒绝」 | **仅要求「`stride≠0` 子集一致」**（`assembler.py:379-383`） | 原行文与 A1 自证目标 add **直接冲突**：`ur-r3 = {alpha_read_3 (t=2, stride=0, la=768 驻留), x_read_1 (t=7, stride=1, la=0)}` → 按原行文 add 无法展开。驻留型（stride=0）地址恒 `orig_offset`、不参与步进，故豁免；步进型之间的不一致仍是硬错。环绕守卫（`stride·R_q ≥ bank_depth`，`:385-392`，stride=0 豁免）随之按同一口径。 |
| I2 | D4(b-1)「输入 = solver 调度 + hw_config」 | `compiler_schedule.json` **追加** `stride` / `logical_addr` / `dly` / `op_type`（`pipeline_runner.py:255-273`） | 地址契约的两个输入必须与 `t` 一同落盘，否则检查器只能回读产物 payload（同源、零信号）。纯附加字段：既有读者（`auditor.py:520`、`timing_diff.py:50`、g14b）只取 `t` / `iq`。 |

**实现落点（全部比整数口径；见 D2/D3/D4 契约）**

| 契约 | 落点 |
|---|---|
| 白名单 + 状态闭集导入期校验 | `assembler.py:10-14`（`A1_UNROLL_ALLOWLIST = {"add": "experimental"}`） |
| D1 槽冲突显式拒绝（三路生效） | `assembler.py:285-294` |
| D2 展开分支（INIT 垫拍 / 槽序列 / 地址烘焙 / 域校验 / TERM） | `assembler.py:358-476`（地址 `:438-443`；`inc_embed` 写死 0，不走主线 op_mask） |
| D2 前置守卫（same-stride 收窄 + 环绕保守拒绝） | `assembler.py:379-392` |
| D3 实算行数互校 + 容量硬拒（DEBT-055-CAPACITY） | `assembler.py:465-474` |
| M4 分支证物 `branch_report` → `branch_report.json` | `assembler.py:475`/`:479`；`pipeline_runner.py:189-190` |
| M2/M3 接线 + 归一化 + 双向响亮失败 | `pipeline_runner.py:12-26`（`normalize_top_op`）、`:136-161` |
| D4(a)⑤ g14b 泛化 + add 强制跑点 | `tests/unit/test_rtovr_sparse_guards.py:219`（`g14b_unroll_stream_audit`）、`:496`（`test_g14_add_unroll_stream_protocol`） |
| D4(c) 变异（槽冲突/气泡 vld/容量/步长/环绕） | `tests/unit/test_rtovr_sparse_guards.py:641-741`（m16–m20） |
| D4(b-1) 独立事件检查器 | `verification_v2/tools/emit_expected_fire_sequence.py`（不 import assembler；地址刻意写成 `orig_offset + stride·(j−r_k)` 形式） |
| D4(b-1) 检查器自证（契约纯函数 + 6 变异） | `tests/unit/test_emit_expected_fire_sequence.py` |
| §3 实测行数列产出器 | `verification_v2/tools/parse_e2e_scale.py` |
