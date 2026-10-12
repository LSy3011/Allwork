# 任务2收口：DS2 写响应注入与监测

**状态**：已裁决（2026-10-08，用户四问全答）。本文档由原「F6 提案（矩阵线）」
按裁决修订定稿；**不含任何矩阵 RTL 变更**。

## 0. 裁决记录（2026-10-08）

1. **命名**：本线定名「任务2收口：DS2 写响应注入与监测」，**不再使用 F6 名义**
   （不用 F6b，避免继续混淆）。**F6 专属 LSU 线**（提前完成竞态及完成契约修复），
   另行立项（见 §8）。矩阵线收口不代表 LSU 提前完成问题已解决。
2. **选项 B 批准（带两处论证修订）**：不改矩阵 RTL，增加 TB 诊断检查。
   - 修订①（措辞）：删除「协议合法环境下无功能缺陷」「只能由 force 制造」
     的绝对表述，改为：**现有证据支持本次异常由边界注入造成；合法源端反压
     用例已恢复通过；尚无证据要求修改矩阵 RTL。** 单次合法注入与当前源码
     分析不构成所有事务、复位及配置组合的证明。
   - 修订②（§4 技术错误）：原稿「pending=1 时无 forward、无减账」与 RTL
     不符——`:413 slv_bvalid=(pending_bvalid|ds_bvalid)&~bresp_cnt_empty`
     表明 pending=1 且计数非空时**缓存响应仍可上送并在握手时减账**。§4 已
     围绕「响应接收、缓存、交付与计数守恒」重写。
   - 检查命名：「**响应账目异常持续检查**」，不称「已证明的不可能态」。
3. **阈值与处置**：连续 **16 个有效采样拍**；正式验证用 `$fatal`。
   **16 拍是诊断防抖阈值，不是协议允许延迟上界**（删除原稿「一拍响亮失败」
   说法）。契约细节见 §5。
4. **LSU F6 立即立项**：先以 1–2 个工作日完成设计评审，**暂不授权改 RTL**。
   关键认识：修复 WAIT_DRAIN 提前采零**并不自动得到 STORE 外部完成保证**
   （已实测 STB/STBAXI 信用早于末写和 B 响应）。详见 §8 与 F6 设计文档。

## 1. 结论（按修订①措辞）

- 现有证据支持：round6/round7 观测到的 ds2 写响应异常（S4 持死、bresp 0/1）
  **由边界线注入造成**（force `bmc_ds2_bready` 制造桥/矩阵分裂视图 → 幻影
  上送 → 孤儿 B 重复捕获死锁，三级证据链见 §2）。
- 合法源端反压用例（us3bp n=150，注入点 `de_stb_bready[0]`）**已恢复通过**
  （delay=129 拍，无楔死，比对 GREEN）。
- **尚无证据要求修改矩阵 RTL。** 单次合法注入与当前源码分析**不构成**所有
  事务、复位及配置组合的证明；残留不确定性由常驻监测（§5）兜底。
- 措辞边界（用户 2026-10-07 裁定，对称适用）：结论**限于 TB/模型层**，
  不外推「硬件已证健壮」。

## 2. 证据链（三级，全部归档冻结）

| 级 | 证据 | 位置 |
|---|---|---|
| 原始异常 | round6 faultinjection drive2/3：S4 持死 4600+ 拍零握手、writes 16/16 而 bresp 0/1、`$fatal@62385000ps`；FSDB 物证 `round6_faultinjection/wedge_bstall_drive3_awarmed/` | `verification_v2/artifacts/m3lite_gate3_round6_faultinjection_20261007/` |
| 机制实证 | BPDBG 探针：errata ⑧ 三项可证伪预测全中——释放后 64/64 拍不动点 `{bmc_ds2_bvalid=1, bready=0, brg_fifo_empty=0, mtx_pending=1, mtx_slv_bvalid=0, mtx_cnt_empty=1}`；「桥 FIFO 捕获失效」子假说证伪 | `verification_v2/artifacts/m3lite_gate3_bpdbg_20261008/`（TB 指纹 `95382ceb…030b`） |
| 正反对照 | us3bp n=150（合法注入点 `de_stb_bready[0]`）：inject@1172 < bvalid risen@1192 < release@1321 ≤ drained@1321，delay=129 拍，释放后 64 拍窗口矩阵 ds2 三态 pending=0 无楔死，比对 GREEN | `verification_v2/artifacts/m3lite_gate3_us3bp_20261008/`（TB 指纹 `355d48d4…52a54`） |

## 3. 行级根因（行号 2026-10-08 grep 复核）

**矩阵** `data_engine/noc/rtl/atcbmc300_ds_wdata_bresp_ctrl.v`（第三方 Andes IP）：

- `:412` `ds_bready = ~pending_bvalid` —— ds 侧反压输出，仅由 pending 驱动
- `:413` `slv_bvalid = (pending_bvalid | ds_bvalid) & ~bresp_cnt_empty` ——
  上送（缓存优先），受账目否决
- `:414-415` `slv_bid`/`slv_bresp` 选择（pending 优先于线上）
- `:416` `slave_bready = mst_bready[slv_bid[3:0]] & ~bresp_cnt_empty` ——
  交付确认，受账目否决
- `:422` `pending_bvalid <= (pending_bvalid | ds_bvalid) & ~slave_bready` ——
  捕获只认 `ds_bvalid`，不采样 `ds_bready`
- `:429` payload 锁存守卫 `ds_bvalid & ~slave_bready & ~pending_bvalid`
- `:651` `last_wr`（W burst 末拍入账点）；`:675` generate `outstanding_fifo`
  分支——ds2 实例 `OUTSTANDING_DEPTH = ATCBMC300_SLV2_FIFO_DEPTH = 8 > 1`
  （`atcbmc300_config.vh:174`、`atcbmc300.v:19400-19405`），此分支激活
- `:696` `bresp_cnt` 簿记（`last_wr` 独占 +1；`slv_bvalid & slave_bready &
  ~last_wr` −1）；`:700` `bresp_cnt_empty = ~|bresp_cnt`

**桥** `data_engine/sram/atcrambrg300.v`：

- `:344` `bresp_fifo_wr = ~(bresp_fifo_empty & bready) & bresp_valid` ——
  空且 bready=1 才直通；bready=0 时强制入队
- `:345` `bresp_fifo_rd = ~bresp_fifo_empty & bready & bvalid` ——
  严格按线网 bready 出队
- `:346` `bvalid = ~bresp_fifo_empty | bresp_valid` —— FIFO 非空则持高

**master 侧** `DW_axi_gm_core.v:455`/`:508`/`:511`：bready **电平持有**非窗口
脉冲——合法反压注入（errata ⑧ 核定 `de_stb_bready[0]`）的成立依据。

**死锁时序**（errata ⑧ 假说，BPDBG 全中）：

1. force `bmc_ds2_bready=0`：桥生成 B，`:344` 因 bready=0 入 FIFO，`:346`
   持 bvalid=1（孤儿诞生）；桥认为未握手。
2. 矩阵 `:422` 只认 `ds_bvalid=1`：此刻 cnt=1（last_wr 已 +1）且
   mst_bready[3]=1（电平持有）→ slave_bready=1 → **直通幻影上送**
   （pending 不 latch、payload 不锁），`:696` cnt 1→0。
   同一响应一端消费、一端留存（分裂视图）。
3. T+1：孤儿 ds_bvalid 仍 1 而 cnt 已空 → `:416`/`:413` 双重否决 →
   `:422` latch pending=1、`:429` 锁存孤儿 payload。
4. **不动点**：pending=1 ⇒ `:412` ds_bready=0 ⇒ 桥 `:345` 永不排空；
   cnt_empty=1 ⇒ `:416` slave_bready 恒 0 ⇒ `:422` pending 恒 1。自锁，
   与 force 是否解除无关（BPDBG rel+0..rel+63 恒定实测；pending 自 ~1192
   持 1，与幻影上送 ~1191 时序吻合）。

## 4. 响应接收、缓存、交付与计数守恒（按修订②重写）

账目定义：`bresp_cnt` = 已进入 ds 侧的完整 W burst 数（`last_wr` +1）
− 已向上游 master 交付的 B 数（交付握手 −1）。守恒来自三个对应关系：

1. **接收⇒入账**：从机只在收齐完整 W burst 后生成 B；该 burst 的 `last_wr`
   已在 ds 侧 +1。故 `ds_bvalid=1` 时，该响应在账上已有对应条目（cnt≥1）。
2. **缓存唯一**：pending 只一格；`:429` 的 `~pending_bvalid` 守卫使同一
   `ds_bvalid` 不被重复锁存；pending=1 时 `:412` ds_bready=0，ds 侧暂停
   接收新响应。
3. **交付⇒销账**（两种形态，`:413`/`:414-415`/`:416`/`:422` 联立）：
   - **直通**（pending=0）：线上 `ds_bvalid` 的响应当拍上送；握手
     （slave_bready=1）即交付，cnt −1，pending 保持 0；
   - **缓存交付**（pending=1）：**上送的是 pending 缓存的响应**
     （`slv_bid`/`slv_bresp` 选 pending）；握手即交付，cnt −1，同拍 `:422`
     pending 清零（`~slave_bready=0`）。——原稿「pending=1 时无 forward、
     无减账」错误，已按此修订。
   两种形态下，每次交付握手恰好销账 1，且被交付的响应都来自 ds 侧的真实
   接收（直通=当拍线上；缓存=此前 `:429` 锁存）。

**边界 force 如何打破守恒**：force 使桥侧 bready=0——桥未交付却持 bvalid；
矩阵 `:422` 不采 bready，直通形态把这次「源端未交付的呈现」当作有效接收
上送并销账（cnt 1→0）。**接收与交付的对应被外力打破，账目少记一笔仍在
桥侧留存的响应。** 孤儿释放后再次呈现 ds_bvalid=1，此时 cnt=0 →
`:413`/`:416` 否决 → `:422`/`:429` 锁入 pending → **pending=1 &
cnt_empty=1 的稳定异常**：缓存中有待交付响应，账上无可销条目，交付被
否决永久阻断。

**残余不确定性声明**：上述为源码级机制分析 + 三级实证，覆盖本次观测的
事务形态；未穷举所有事务交织、复位及配置组合（裁决修订①）。这正是以
常驻监测（§5）兜底、而非宣告完备的原因。

## 5. 监测设计：响应账目异常持续检查（已裁决契约）

- **探针**（BPDBG 已验证路径）：
  `dut.u_atcbmc300.ds2_ctrl.ds_wdata_bresp.pending_bvalid`、
  `…．bresp_cnt_empty`；诊断附加 `…．outstanding_fifo.bresp_cnt`、
  `…．pending_bid`、边界线 `bmc_ds2_bvalid`/`bmc_ds2_bready`。
- **采样语义**：`posedge clk`（与 ds2 逻辑同域）；**有效采样拍** =
  复位释放后（`rst_n===1`）的采样拍。
- **命中条件**：`pending_bvalid===1 && bresp_cnt_empty===1`。
- **计数规则**：复位期间清零；命中连续累加；**条件消失立即清零**。
- **触发**：**第 16 个连续命中拍**。⚠ **16 是诊断防抖阈值，不是协议允许
  延迟上界**——它不承诺 15 拍内的同态持续是合法的，只是把瞬态毛刺与
  稳定异常分闸。
- **触发动作**：先输出一行诊断（cycle、pending_bvalid、bresp_cnt、
  bmc_ds2_bvalid/bready、pending_bid），再以专属标记
  `[G3_RESPACCT][FATAL]` 执行 `$fatal`。
- **编排器契约**（`m3lite_gate3_compare.py`）：
  - 识别 `[G3_RESPACCT][FATAL]` 为**专属失败标记**；
  - bp 腿（边界注入负控）**期望**其命中（替代原「S4 持死至超时」期望）；
  - 其余各腿出现即 **RED 且不发布**；
  - **不得**被 timeout 腿的「预期屏障超时」通道吸收误认。

## 6. 回归期望修订（裁决③附带，2026-10-08 已实施）

新增检查会让旧非法注入腿**提前结束**（~幻影后 16 拍，而非原 S4 超时
62385000ps），因此：

- **不能**同时要求「旧死锁一直保持到原超时」——**bp 腿与 to_s4 腿**
  （两者均使用同一边界注入）期望改登记为 `fatal_expected respacct`
  （专属标记存在 + 其后零 dump）；to_s4 的 `+G3_S4_TIMEOUT=50` 保留但不再
  到达。**限制落账：S4BARRIER TIMEOUT 通道自此失去在套激发**（通道保留
  未压实；WBARRIER TIMEOUT 通道由 to_wb 腿继续覆盖）。
- **负控准确命中新报警**由 bp 腿（套内故障注入腿）承担，不另设独立 case；
- **合法反压**（us3bp n150）与**正常运行**（positive）**不误报**——
  比对器 C0 新增「无 [G3_RESPACCT][FATAL]」断言（positive/negative 全模式）；
  屏障超时分支新增「RESPACCT 不得被预期超时通道吸收」反向断言；
- 历史长时间死锁证据（round6 FSDB、bpdbg 归档）**原样冻结保留**，不作
  回归比对基线。

**实施清单（2026-10-08）**：TB `tb_aiacc_top_v04_f_multi_stage.v` 新增
RESPACCT 常驻检查块（always-on 无 plusarg；指纹 `3324a33b…f7c5`）；
`m3lite_gate3_compare.py` 新增 `RESPACCT_RE` + `--expect-timeout respacct`
分支 + 两处防误报/防吸收断言；`m3lite_gate3_run.sh` bp/to_s4 腿期望改判
respacct；`tests/test_m3lite_gate3.py` +5（负控命中/命中后 dump 红/未命中
红/防吸收红/正向误报红），gate3 55/55、全量 pytest 535。

## 7. 风险与限制

- 措辞裁定（2026-10-07）对称适用：不外推「硬件已在任意环境证健壮」；
  本文结论限 TB/模型层。
- §4 为源码机制分析 + 定点实证，非形式验证全枚举；noc 配置变更
  （`SLV2_FIFO_DEPTH`、端口映射、桥模型替换）后 §4/§5 需复核。
- `u280_sim_v422` 无 git：TB 变更以 sha256+mtime 记账；bupt 目录只读。
- 选项 C（RTL fail-open 冲刷）**用户未选**，理由记录：服务对象仅是已退役
  的非法注入环境；分叉第三方 IP；挂死换伪响应生产语义更差；ds2 为 llama r2
  上板同路径，功能变更触发全量回归+板级复验。

## 8. LSU 线 F6（另行立项，2026-10-08 裁决④）

- **F6 = LSU 提前完成竞态及完成契约修复**。先以 1–2 个工作日完成设计评审，
  **暂不授权改 RTL**。
- 必须纠正的认识：修复 WAIT_DRAIN 提前采零**并不自动得到 STORE 外部完成
  保证**——STB/STBAXI 信用实测早于末写与 B 响应（门③ C-信用实测
  CRD_STB −7 / STBAXI −3）。
- 设计评审至少按四个完成层次分别定义（详见 F6 设计文档）：

| 完成层次 | 必须回答的问题 |
|---|---|
| 分发完成 | 是否仍有尚未进入执行计数的指令？ |
| LOAD 完成 | 全部目标 UR 写是否已落实？ |
| STORE 完成 | 对应写响应是否全部握手，错误是否被处理？ |
| 软件可见完成 | 哪些保证最终反映到中断/完成寄存器？ |

- 不得直接采用 `seen-nonzero` 或计数相等作为完整答案：前者有空队列及
  已完成事务问题；后者仍可能在指令进入计数前出现 0==0。
- 近期执行顺序：**矩阵提案订正与监测收口 → F6 完成契约设计、影响评估 →
  决定是否实施 RTL 修复**。中期成果继续保留「TB 辅助的完整数据链路验证」
  口径；不让开放式 RTL 调试吞掉剩余计划；不提前宣称 host 可执行闭环。
