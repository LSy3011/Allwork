# F6 设计评审：LSU 提前完成竞态与完成契约（v3.1 边界钉死稿）

**状态**：设计评审稿 v3.1（2026-10-08，按用户第三轮裁决 + **四步执行令**
修订）。用户已批准继续采用 D1 双基线方案、进入定向测试与隔离实验实现的
准备；**契约与测试冻结前仍不改功能 RTL**。范围保持 IQ24–27；IQ92、STORE
外部完成及通用软恢复不纳入本次。

**v3.1 变更记录**（四步执行令，逐条对应）：

1. **步①错误源延迟审计已完成**（§4.3 审计表）：两个枚举错误源均与信用
   计数**同块同拍注册（相对延迟 0）**；设计规则 R-DELAY-0 钉死后续接入。
2. **步②恢复边界固定**（§4.5 E4 重写）：FAILED 持续锁定；**cfg_rst 不得
   单独解除锁定**，仅 rst_n 解锁（rst_n 触达信用源，R10）；**未经证明的
   静默等待或信用屏蔽均不作为安全恢复依据**（v3 的 (a)/(b) 待择项删除）；
   在飞期间 cfg_rst 视同 FAILED。
3. **步③隔离副本**：实际源码+编译依赖+哈希副本建立（不只清单）；
   **mtime 不作为「历史未修改」证明**——`lsu_complete.v` 与
   `.pre_sync_reset` 备份的一致性改为 **sha256 逐字节证明**（R13）；
   首版清单漏 smc/ 编译依赖，已修正扩围。
4. **步④定向测试**：TB-A（lsu_complete 契约级）先行实现并对旧行为
   基线复现原始竞态；**快照采样拍钉死为唯一拍**（§4.1，删除「静默窗内
   任一拍」的实现歧义）；T-FAILLOCK/T-INTFULL 属 TB-B（ctrl_regs 级）。

**v3 变更记录**（相对 v2，逐条对应第三轮裁决）：

1. **Q6 定案**（裁决 1）：错误时 **intid 继续自增**（v2 建议的「停自增」
   撤回——`intr_ro=(intid_hw_r != intid_sw_r)`，停自增会令等中断的 host
   永远收不到错误通知）。最小方案：intid 作为通知事件序号、同一失败只
   通知一次、置 int_err、done 位保持 0、同拍错误优先、int_q_full 可观测。
   见 §4.5 E3。
2. **Q7 定案**（裁决 2）：**准入拒绝**（「响亮挂死」不作契约）。检查必须
   覆盖**实际进入实验的最终指令包**（不止 case_gen 一道），报告
   队列/行/字段/原因；**IQ92 取「限制修复范围」**——信用源未审计
   （R12），不得据四队列证据宣称五队列支持。见 §4.2、§6。
3. **边界① 同拍错误不得混入成功**（裁决 3.1）：成功联言第四项扩展为
   「已锁存错误 ∧ 当拍检出错误」双排除；错误检测流水线延迟审计表列为
   实施准备必做；新增 T-ERRSYN / T-ERRLATE。见 §4.3、§4.5 E1'。
4. **边界② FAILED 必须挡住新 START**（裁决 3.2）：FAILED 持续锁存，
   排队中与新写的 START 均不得执行，直至恢复条件满足（不靠 host 自律）；
   **新结构性发现 R10：cfg_rst 不触达信用源**（u_smc 仅 rst_n）——
   「cfg_rst 后必然安全恢复」**不得写成已证结论**，恢复路径与
   T-RSTLATE 测试见 §4.5 E4。
5. **边界③ N 与 sid 作为同一载荷交付**（裁决 3.3）：inst_queen 注册
   `{dispatch_closed_valid, batch_sid, N}` 单拍脉冲；附末条指令/空批/
   连续不同 sid 三张逐拍表 + S 快照逐拍表。见 §4.4。
6. 首批验证优先级（裁决列明）落入 §9.0：武装前信用已返回；空批/末笔
   信用/错误同拍；FAILED 后排队与新写 START 均不执行；复位后迟到信用
   不污染新批。
7. 评审新发现（本轮回源）：R10 cfg_rst 不触达信用源；R11 int_q_full
   语义=8bit 通知序号回卷保护；R12 IQ92 信用源=u_smc `ds_crd_ds_o`
   6bit 链（未审计）。见 §8。

**前序文档**：RECON（`artifacts/m3lite_gate3_sync_recon_20261006/RECON.md`）、
任务2收口文档、ADR-058/059/060+errata①–⑩。

**措辞纪律**：机制结论限本沙箱 RTL 源码与 VCS TB 观测；不改写、不外推
第三方（InchiTech/Synopsys）IP 内部质量断言。

---

## 1. 问题定格（v1 保留，行号 2026-10-08 复核）

完成判定链三段（`smi/`）：

1. **分发完成脉冲**：`smi_iq_inst_queen.v` WAIT_DONE 态见
   `w_prog_done`（非 EXTRA_DATA 时即 `w_siq_data_aligned`：
   `r_rd_inst_cnt[sid] >= r_wr_inst_cnt[sid]`，:405-413）即
   `rdi_done<=1`（:441-442）——语义=**本 sid 全部指令已读出 inst_queen
   进入 dly_queen**，与执行无关。空队列（wr=0）时 `0>=0` 立即成立。
   ⚠ `r_wr_inst_cnt[i]` 在 `rdi_done && rdi_iqeid==i` **清零**
   （:176-178）——N 采样窗口约束（R9），交付机制见 §4.4。
2. **LSU 排空门**：`smi_iq_lsu_complete.v`（全 45 行）IDLE 见
   `rdi_done_dispatch` 转 WAIT_DRAIN；采到 `outstanding_inst_num==0` 即
   `rdi_done_out`（:34-38）。**竞态**：末条指令读出到 dly_queen
   `inst_valid` 使 `r_executing_inst_cnt` 递增之间存在流水线空窗。
   5 实例挂 IQ24/25/26/27/92（`smi_top.v:514-517`/`:592-621`），其余
   123 队列 bypass（:623-631）。
3. **批聚合**：`smi_ctrl_regs.v` `done_match[gi]=rdi_done_bus[gi] &
   active_en[gi] & (rdi_iqeid==active_sid)`（:160-165）→ `batch_done`
   （:170-172）→ `intid_hw` 自增（:281-286）+ `smi_iq_done_r`（:292-293）。
   软件可见出口仅 0x004/0x024（RECON §1 穷举）。

**启动路径**（批起点的事实基础）：host 写 0x020 只是**入队** START 队列
（:325-337，深度 4）；真正发射只在自动弹出（:304-312：
`!glue_busy && start_cnt!=0` → 同拍置
`active_en_r/active_sid_r/glue_busy=1/rdi_start=1`）。**全局一批在飞**，
批间由 glue_busy 串行。`rdi_start` 是**注册脉冲、每批恰一次**。

**实测时序**（门②，probe_cycle）：L1 dispatch@148→batch_done@149→done@150，
@150 outstanding=1，UR 写 166..181，CRD_LDB@183（通知先于首写 16/末写
31/信用 33 拍）；L2 通知@270=末写@270，CRD@272。store 侧信用不对称
（门③）：CRD_STB −7 / CRD_STBAXI −3，均先于 B 响应。

**设计固有性**：与 `.pre_sync_reset` 备份（2026-07-21）逐字节一致。

**危害半径**：观测层（完成先于落位）+ 编排层（START 自动链接把提前 done
传递链式批）+ 错误混叠（§8 R1/R6/R7/R8）。

---

## 2. 四个完成层次（v1 保留，L2 措辞按第二轮裁决 3 收紧）

| 层 | 定义 | 必须回答的问题 | 现状回答 | 缺口 |
|---|---|---|---|---|
| **L0 分发完成** | 本批全部指令离开 inst_queen 进入 dly_queen | 是否仍有未进入执行计数的指令？ | `rdi_done_dispatch` | 不提供「末指令已进入计数」信息；就是竞态武装点 |
| **L1 LOAD 完成** | 本批全部 LOAD 的目标 UR 写已落实 | 全部目标 UR 写是否已落实？ | `crd_ldb_o/crd_ldbaxi_o` **结构性在末次 UR 写之后**（`ldb_ur_axi_conv.v:1384`/:1461-1466/:1477-1490） | 信用语义完好；缺口只在排空判定的基线/武装 |
| **L2 STORE 完成** | 本批全部写响应已握手、错误已处理 | 全部写响应是否握手？错误是否被处理？ | `crd_stb_o`=UR 读侧末拍被 AXIS 消费；`crd_stbaxi_o`=burst 被 DW_axi_gm **受理**（`i_saccept`）。**当前查到的批完成判定链没有接入能够保证全部写响应完成的对账机制**（§5） | 结构性缺口（F6-C 独立立项）；错误路径存在但被丢弃（R7），完成不等错误 |
| **L3 软件可见完成** | host 经 0x004/0x024 获得的完成指示 | 哪些保证最终反映到中断/完成寄存器？ | batch_done 聚合 | 保证 = min(各队列层保证)；错误与成功在 intid 上不可区分（R6） |

**钉在首页的认识（裁决）**：修复排空判定只把 L1 提到满额、L2 提到
**既有信用的语义上限**；即使 F6-A 全部通过，也**不能宣称 STORE 外部
完成**（写响应全握手）。

---

## 3. RECON §4 A/B 评估与 v1 快照方案的反例（v2 起保留）

### 3.1 候选 A/B 的陷阱

- **A（seen-nonzero 电平武装）**：空队列死锁（需 N=0 旁路）；纯电平武装
  不可区分「尚未升起」与「已升已落」。
- **B（全量计数相等）**：批间稳态 0==0；64bit 计数器跨批自由滚动、不含
  批边界信息。
- 共同根因（仍成立）：**排空判定缺「本批应计指令数 N」与「本批基线」
  两个输入**。

### 3.2 v1 快照时点的反例（第二轮裁决 1，已撤回 v1 结论）

v1 的 C-DRAIN 在 **dispatch-done 拍**取 `r_crd_inst_cnt` 快照 base，判据
`(crd − base) == N`。反例：**武装前已返回的信用被漏计**——本批 N=3，
dispatch-done 前已回 2 笔，快照后只剩 1 笔可增，增量永远达不到 3 →
**永不完成**。该形态不是病态构造：LSU 行读出 ~2-3 拍/行而 AXI 执行
≥15 拍，**批行数稍大（如 60 行）时大部分信用天然在 dispatch-done 前
返回**；DS（IQ92，UR↔UR 快路径）更甚。⇒ **基线必须前移到批起点**；
「已升已落天然入账」仅当基线早于本批首笔可能信用时才成立。

---

## 4. 契约 C-F6 v3

### 4.1 C-BASE（批起点与基线；v3 补 S 快照逐拍表与静默窗精化）

- **批起点 S**：ctrl_regs 自动弹出拍（`glue_busy`/`active_en_r`/`rdi_start`
  同拍置位，:304-312）。注册脉冲、每批恰一次、全局一批在飞——**无并发批
  基线歧义**。
- **基线快照**：对每 LSU 队列锁存
  `cru_base[q] <= r_cru_inst_cnt[q]`、`crd_base[q] <= r_crd_inst_cnt[q]`
  （dly_queen :109-110，64bit，smi_rst_n 域）。计数器需经新增内部连线
  暴露（非 CSR）。
- **静默窗与采样拍钉死（v3.1，执行令步④）**：首笔 `inst_valid` ≥ S+3
  （S → 队列 rdi_start S+1 → inst_queen WAIT_START→start_new→读存储 →
  dly_queen IDLE→DECODE→发射），首个信用不早于首 `inst_valid` 之后 ⇒
  **区间 [S, S+3) 内 cru/crd 计数器静止**。**采样拍唯一钉死**：
  lsu_complete 在**观察到本队列 start 脉冲的当拍 posedge** 采样基线
  （start 脉冲于 S+1 可见 ⇒ 采样 posedge = S+1 拍末，记 SAMP=S+1）——
  不保留「静默窗内任一拍」的实现歧义；静默窗 [S,S+3) 是该唯一定点
  **有效性**的证明（SAMP 处计数器与 S 拍等值），不是实现自由度。
- **S 快照逐拍表（裁决 3.3，正常批 N>0）**：

  | 拍 | 事件 | 关键寄存器（拍末值） |
  |---|---|---|
  | t0 | host 写 0x020，仅入队 | start_cnt: n→n+1 |
  | … | 排队（glue_busy=1 时等待在飞批完成） | — |
  | **S** | 自动弹出条件成立：`!glue_busy && start_cnt!=0`；同拍 posedge 置 active_en_r/active_sid_r/glue_busy/rdi_start；**基线快照窗开启** | glue_busy=1, rdi_start=1 |
  | S+1 | rdi_start 到达 inst_queen/lsu_complete；**基线快照（唯一采样拍 SAMP=S+1 拍末 posedge，钉死）** | cru_base/crd_base 锁存 |
  | S+2 | inst_queen 读存储，首行进 dly_queen | — |
  | ≥S+3 | 首个 `inst_valid`；首个信用在其后 | cru 开始 +1 |

- **同拍归属（v3 重述）**：信用的「计数更新拍」落在静默窗内不可能
  （下界证明）；故「属本批 ⇔ 计数更新拍 ≥ S」无歧义——区间内唯一可能
  的计数扰动是**前批/复位残留**，由下面的批起点不变量与 E4 恢复路径
  分别兜底。
- **批起点不变量（前批残留归属）**：快照拍要求每 LSU 队列
  `crd_base==cru_base`（前批已结清）。该不变量**由 C-F6 自身建立**
  （修后排空才放行下一批），并在每批快照时**防御性检查**：违反即置
  err、拒绝武装、响亮失败（不得静默把残留计入新批）。错误中止的批
  （E4）之后必须经恢复路径重建不变量才允许新批。
- **复位归属**：软复位 `smi_rst_n = rst_n & ~cfg_rst_pulse`（smi_top.v:232）
  相干冲刷 **SMI 域**队列/计数器/完成 FSM——SMI 侧计数归零后不变量
  平凡成立。⚠ **cfg_rst 不触达信用源单元**（R10），复位后在飞信用
  可能迟到返回——恢复路径的完整语义见 E4，**不得**仅凭 SMI 计数归零
  宣称安全恢复。

### 4.2 C-N（N 的单位 + Q7 准入门定案）

**N ≠ 天然的 `r_wr_inst_cnt[sid]`。** 该计数是 **wri_vld 写次数**
（inst_queen :168-174，不看行内容），仅在下列一一对应成立时才能作为
信用目标数：

| 形态 | wr 计 | inst_valid | credit | 1:1 | 处置（契约） |
|---|---|---|---|---|---|
| M3-lite LSU 描述符行（LDB/LDBAXI/STB/STBAXI，VLD=1，LOOPS=0） | n | n | n | ✓（实测 LDB 2/2、STB 1/1，门②③） | **支持** |
| VLD=0 行 | 计 1 | 发 1（dly_queen 发射不查 vld，:245） | 0（单元不执行） | ✗ | **准入门拒绝** |
| LOOPS>0（LSU 行） | 计 1 | 1 | 未审计（单元内部迭代与信用对应未取证） | ? | **排除**（M3-lite 实测 LSU LOOPS 恒 0；非 0 即拒） |
| INIT/TERM 行混入 LSU 队列 | 未在 M3-lite 观察到 | — | — | ? | **排除**（出现即拒） |
| EXTRA_DATA 队列（IQ14 等） | — | — | — | 范围外 | LSU 五队列 EXTRA_DATA=0（宏参数），jump-end 路径不激活 |
| 相邻相同行（inst_keep=1） | 各计 1 | 各发 1（dly_queen 每行恰一次 inst_valid，rd_cnt 单调） | 取决于单元 | 条件成立 | 支持，依赖核心假设 A-credit |

**核心假设 A-credit**（全部计数的根基，可证伪）：**单元对每个接受的
`inst_valid` 返回恰好一个信用**。内建自检=dly_queen 的 id 对账
（:172 重写 inst_id 为队序号、:193 失配/超发置 err）；漏信用表现为
永不完成（响亮方向），多/乱信用表现为 err。

**Q7 准入门（第三轮裁决 2，定案）**：

- **检查对象 = 实际进入实验/仿真的最终指令包**，覆盖一切入口：
  case_gen 生成、手写产物、历史包、任何旁路——**不得只挂在生成器
  一道**（生成器之外的入口可绕过）。
- **拒绝即响亮**：报告**队列号/行号/字段/原因**；未通过检查的包
  **不进入硬件启动**，且属 **F6-A 验证范围之外**——不得运行后宣称
  覆盖。
- **IQ92 专项（定案：限制修复范围）**：IQ92 信用源 = u_smc
  `ds_crd_ds_o`（R12），「一 inst_valid ↔ 一信用」对应与行形态
  **未审计**。**不得据 IQ24-27 四个搬运队列的证据宣称五队列均支持。**
  本轮裁决二选一取**限制修复范围**：F6-A v1 支持范围 =
  **IQ24/25/26/27**；IQ92 的 lsu_complete 实例**保留现有行为**
  （模块级参数/per-instance 使能，见 §6），DS 审计列为未来若主张
  IQ92 支持时的前置立项。

### 4.3 C-DRAIN（完成条件与同拍计数语义；v3 边界①扩展）

**完成 ⇔ 联言**（全部对**已注册值**在 posedge 求值；v3 扩展第四项）：

```
done_q ⇔ dispatch_closed[q]                      // 分发关闭（载荷已捕获，§4.4）
       ∧ (r_cru_inst_cnt[q] − cru_base[q]) == N_q     // 应发全部发出
       ∧ (r_crd_inst_cnt[q] − crd_base[q]) == N_q     // 信用全部返回
       ∧ (err_sticky[q] == 0) ∧ (err_detect_now[q] == 0)
          // 无已锁存错误 ∧ 无当拍检出错误（边界①）
```

- **边界①（第三轮裁决 3.1）同拍错误排除**：仅读已注册 err_sticky 会留
  窗口——若某错误源从触发到 err_sticky 可见的延迟**大于**其对应信用
  使计数达标的延迟，则存在「计数已齐而错误未注册」的假成功拍。契约
  要求：
  1. **双排除**：联言同时排除已锁存错误（err_sticky）与**求值当拍组合
     检出**的错误（err_detect_now：本拍信用携带的单元错误位、本拍 id
     失配/超发）；
  2. **延迟审计表（执行令步①，2026-10-08 已完成，行号已复核）**——
     判据 = **err_sticky 可见不晚于其对应信用使计数达标可见**：

     | # | 错误源 | 触发事件（拍 T） | 检测/注册点 | err 可见拍 | 对应计数可见拍 | 相对延迟 | 结论 |
     |---|---|---|---|---|---|---|---|
     | 1 | dly_queen id 失配 | 信用脉冲（`w_crd_inst_done=crd_i[4]`，:114） | `:190-195`：err 与 `r_crd_inst_cnt` **同一 always 块、同一 `if(w_crd_inst_done)` 分支**内 NBA | T+1 | T+1（:190-191 同块） | **0（共注册）** | ✓ 联言读注册值不可能漏 |
     | 2 | dly_queen 超发（`crd>cru`） | 同上 | :193 同块 | T+1 | T+1 | 0 | ✓（覆盖注记见下） |
     | 3 | 单元错误位 bit6（STBAXI/LDBAXI 的 SLVERR/DECERR，stb_ur_axi_conv.v:283 聚合） | 随信用脉冲同拍到达（:1211 信用生成携带） | E2 接入点——**设计规则强制挂入同一 posedge**（见 R-DELAY-0） | T+1 | T+1 | 0（规则强制） | ✓ 接入后共注册 |
     | 4 | 批级跨队列错误 | 任一队列 err 置位 | batch 聚合（ctrl_regs） | 粘滞保持 | — | 批完成须全部队列 done_q；错误队列自身联言永不成立 ⇒ 批永不完成（响亮方向），无跨队列假成功窗口 | ✓ |

     **设计规则 R-DELAY-0**：任何进入联言的错误输入，必须在「其触发
     信用使计数更新」的**同一 posedge** 完成注册（即并入源 1/2 所在
     的 `if(w_crd_inst_done)` 块或其等价点）；未来新增错误源接入前，
     须先在本表补一行并通过同等审计。
     **源 2 覆盖注记**（非延迟缺口，如实落账）：:193 超发判定用**增量前**
     值比较——首笔超发信用若 id 恰顺序匹配（如迟到信用 id 与新批当前
     计数 mod 16 相撞，T-RSTLATE 类），当拍不置位、其后首笔信用必置位；
     该 1/16 漏检窗口**不作为安全机制**，安全由批起点不变量（C-BASE）
     与恢复边界（E4）承担，簿记自检仅作诊断。
     **err_detect_now 语义据此收紧**：枚举源均已共注册，err_detect_now
     作为**防御项**保留 = 各源当拍组合检测条件的 OR（与 err_sticky 置位
     条件同源），消除任何未来源的单拍窗口；实现时不得引入比信用计数
     更晚注册的新错误路径。
  3. 测试：T-ERRSYN（末笔信用与错误同拍）、T-ERRLATE（目标计数已齐
     时错误到达），见 §9.0。
- **同拍发射/信用**：`inst_valid` 与信用脉冲同拍时，两计数同拍更新、
  下一拍同被观察；联言对注册值求值，无组合竞争。
- **第 N 笔信用同拍**：t 拍脉冲 → t+1 起 `r_crd` 更新可见 → done 最早
  在结束 t+1 的 posedge 注册 ⇒ **绝不早于本批真实完成条件**（对 LOAD，
  信用结构性晚于末写，故 done 严格晚于末写）。
- **计数回绕**：64bit、单调、模减合法；2^64 拍在任何仿真/硬件寿命内
  不可达（~585 年 @1GHz），回绕非问题但按模 2^64 定义减法。
- **每拍至多一信用/一发射/队列**：信用总线单 done 位、发射单 inst_valid，
  结构性成立。
- **N==0（空批，v3 按载荷管线重述）**：dispatch 关闭载荷捕获后联言
  即刻成立（cru/crd 增量为 0，无需等待信用）→ **首个求值拍即完成**。
  这是对旧路径的**时序变更点**，按兼容用例验收（§9）。

### 4.4 C-PAYLOAD（v3 新增：N 与 sid 作为同一载荷交付，边界③）

第三轮裁决 3.3：N 与 sid 必须作为一个**已注册载荷**交付，生产者/消费者/
有效脉冲的逐拍关系写死，不再以「同拍」字样带过。

**载荷定义**：`{dispatch_closed_valid[q], batch_sid[q], N[q]}`。

- **生产者**（inst_queen[q]）：在 `rdi_done` 脉冲拍（下表 d+1）的
  **同一 posedge**（d+1→d+2，即 `r_wr_inst_cnt[q]` 清零 :176-178 的
  同一拍，NBA 语义读清前值——R9 窗口）注册载荷；
  `dispatch_closed_valid` 为**恰一拍**脉冲（随 rdi_done 撤离而清零）。
- **载荷宽度钉死（v3.1，行号复核）**：N = `r_wr_inst_cnt`，**8bit**
  （:108；既有 `inst_amount_siq0..3` 输出 :72-75/:142-145 同宽，IQ
  容量墙 128 行 ⇒ 8bit 不溢出）；batch_sid = `rdi_iqeid`，**2bit**
  （:87）。
- **消费者**（lsu_complete[q]）：valid 拍（d+2）捕获 {batch_sid, N} 并
  置 dispatch_closed；**已关闭状态再收 valid ⇒ err**（防御，结构性
  不应发生——全局一批在飞 + glue_busy 互斥，见连续批表）。
- **sid 一致性**：载荷 batch_sid 必须与 ctrl_regs 的 active_sid_r 一致
  （实现时连线即同源：rdi_iqeid）；完成上送沿用既有
  `done_match`（:160-165）sid 匹配。

**逐拍表 1：正常批（含末条指令）**

| 拍 | inst_queen[q]（生产者） | 载荷/消费者 |
|---|---|---|
| d | WAIT_DONE，`w_prog_done`=1（组合，r_rd>=r_wr，:405-413） | — |
| d→d+1 posedge | `rdi_done<=1`（:441-442） | — |
| **d+1** | **rdi_done=1（dispatch-done 脉冲拍）**；`r_wr_inst_cnt[q]`=N 仍可读 | — |
| d+1→d+2 posedge | `r_wr_inst_cnt[q]<=0`（:176-178）；**同拍注册载荷**（NBA 读清前值 N） | dispatch_closed_valid:=1, batch_sid:=q, N:=N |
| **d+2** | valid 恰一拍 | **lsu_complete 捕获 {sid,N}**，dispatch_closed:=1 |
| d+2→d+3 posedge | valid 清零 | 捕获完成；联言自此可对 N 求值 |

**逐拍表 2：空批（wr=0）**

| 拍 | 事件 |
|---|---|
| e | inst_queen 进入 WAIT_DONE；`0>=0` 组合即真（:405-413） |
| e→e+1 | `rdi_done<=1` |
| e+1→e+2 | 载荷注册，N=0（清前值即 0） |
| e+2 | lsu_complete 捕获；联言（增量 0==0 ∧ 无错）在下个求值拍成立 → 完成（§4.3 时序变更点） |

**逐拍表 3：连续不同 sid 批（k: sid=a → k+1: sid=b）**

| 拍 | 事件 |
|---|---|
| c_k | 批 k 载荷被 lsu_complete 捕获 |
| ≥c_k+1 | 批 k 联言成立 → done_q → batch_done → **glue_busy 清除** |
| S_{k+1} ≥ batch_done_k+1 | 自动弹出条件重新求值（!glue_busy）→ 批 k+1 起点 |
| ≥S_{k+1}+3 | 批 k+1 首个 inst_valid（静默窗保证基线干净） |
| d_{k+1}+2 ≫ c_k | 批 k+1 载荷捕获——与批 k 载荷**不可能重叠**（捕获是先完成、完成才放行下批的结构先序） |

### 4.5 C-ERR（错误语义；v3 按 Q6 定案与边界①②重写）

现状取证（v2 起保留，R6/R7/R8 展开）：

- dly_queen 簿记 err（id 失配/信用超发，:193）→ `ir_err`（五队列 OR，
  smi_top.v:388）→ **只令 intid_hw 自增**（:281-286），**不置 int_err**
  （int_err 仅 start_q_full/int_q_full 置位，:278-284）→ **错误中断与
  成功完成在 host 侧完全不可区分**（R6）；`glue_busy` 不清
  （:289-294 仅 batch_done 清）→ **批楔死、START 队列停摆直至 cfg_rst**
  （R8）。
- **单元错误位被丢弃**：信用链 7bit `{err[6],inst_id[5:2],done[1],valid[0]}`
  （crd_stbaxi_o :71/:1211），smi_top 重映射
  `crd_*_iq_i = {i_crd[1], i_crd[5:2]}`（:494-497）**丢掉 bit6**——AXI
  SLVERR/DECERR（stb_ur_axi_conv.v:283）永不到达完成判定链（R7）。
  STB/LDB 链 6bit 顶端补 0（aiacc_top_f.v:1858/:1860）= 该两单元无错误
  位；STBAXI/LDBAXI 有；DS 链 6bit 无错误位（R12）。

契约（不得经计数相等形成成功；区分错误与成功；定义退出）：

- **E1**：`err_sticky[q]` = 本批内 (dly_queen err) ∨ (信用单元错误位)
  的粘滞 OR（批起点清零）。err_sticky=1 ⇒ 联言永不成立——**计数恰好
  相等也不能形成成功**。
- **E1'（v3，边界①）**：`err_detect_now[q]` = 求值当拍组合检出的错误；
  联言第四项 = `err_sticky==0 ∧ err_detect_now==0`；错误源流水线延迟
  审计表为实施准备必做（§4.3 边界①）。
- **E2**：单元错误位接入——`crd_*_iq_i` 重映射保留 bit6 至完成判定
  （STBAXI/LDBAXI 实错误；STB/LDB 恒 0）。属完成逻辑内部接线，
  **不改寄存器编码**。
- **E3（v3，Q6 定案：保留通知语义的最小方案）**：
  - **intid 继续自增**：`intr_ro=(intid_hw_r != intid_sw_r)`——intid 是
    中断通知的**事件序号**，停自增会令等中断的 host 永远收不到错误
    通知（裁决 1 否决 v2 建议）。错误终止**也自增一次**，语义 =
    「发生了一个需要 host 处置的事件」。
  - **同一失败只通知一次**：错误通知由 err_sticky 的**上升沿**触发
    （参考 ir_err_evt 边沿检测 :120）；粘滞错误保持期间不得重复自增
    intid。
  - **错误与成功可区分**：错误事件置 `int_err`（0x004[1]，现有位、
    语义扩展），`smi_iq_done_r`（0x024）对应位**保持 0**；host/验证
    协议 = **先查 int_err，再查 done**。**明文条款：「intid 增加 ≠
    一批成功完成」。**
  - **同拍错误优先**：完成条件与错误同拍成立时，**只登记错误**（不得
    同拍登记成功）——由 §4.3 联言双排除结构性保证（err_detect_now
    使联言当拍不成立），通知路径亦按错误优先实现。
  - **int_q_full 可观测（R11）**：通知序号空间满
    （`(intid_hw_r+1)==intid_sw_r`，:118/:122，即 255 条未消费）时，
    现状 :282 置 int_err 并抑制该次自增。该路径**必须有测试覆盖**
    （T-INTFULL），不得静默丢失通知。
- **E4（v3.1，边界②按执行令步②固定：FAILED 持续锁定，恢复唯 rst_n）**：
  - **FAILED 持续锁存**：批进入 FAILED ⇒ failed_latch 置位并保持；
    锁存期间 **START 队列中已排队项与新写 0x020 均不得触发自动弹出**
    （弹出条件追加 `!failed_latch`）——**不靠 host 自律**；被拒绝的
    START 必须可观测（状态位/丢弃计数，不得静默吞掉后佯装执行）。
  - **置位条件（两个）**：(i) 任一 LSU 队列 err_sticky 上升沿（错误
    终止）；(ii) **在有批在飞（glue_busy=1 或 START 队列非空）期间发生
    cfg_rst**——在飞事务已被信用源侧受理（R10：cfg_rst 不触达信用源），
    其迟到信用不可辨认（dly_queen 复位后 id 序号重计，:172），故在飞
    cfg_rst 视同 FAILED。**cfg_rst 于无批在飞时仍是合法配置操作**，
    不置锁（兼容性）。
  - **解锁条件（唯一）**：**rst_n 硬复位**。**cfg_rst 不得单独解除
    锁定**（执行令原文）。理据：rst_n 是唯一触达信用源单元的复位
    （u_smc.rst_n，aiacc_top_f.v:1918）——rst_n 后信用源在飞状态被
    冲刷，不再可能产生旧批信用；该性质可在 TB 直接验证（T-RSTLATE-3）。
  - **明文排除**：**未经证明的静默等待（quiesce window）或信用屏蔽
    （credit masking）均不作为安全恢复依据**（执行令原文）——v3 的
    恢复路径待择项 (a)/(b) 删除，首版不引入。
  - **T-RSTLATE 验收语义**（执行令原文「迟到信用不能触发新批或假
    成功」）：
    1. 批在飞 → cfg_rst ⇒ failed_latch 置位；其后新写 START 不执行；
    2. cfg_rst 后注入迟到信用 ⇒ **不得触发新批、不得形成假成功**
       （无批武装，联言不求值；计数器即使被拨动也无完成路径）；
    3. rst_n ⇒ failed_latch 清除、计数归零，其后新批正常 GREEN
       （恢复有效性正面证据）。
  - **E5**：错误语义进验收（§9 T-ERR 系列），不留作债务。

---

## 5. STORE 外部完成缺口（分立处理，措辞按第二轮裁决 3 收紧）

**取证**（`data_engine/stb_ldb/stb/`）：

- B 响应终止于 DW_axi_gm（Synopsys IP）；其 GIF 响应侧在 `stb_top.v`
  实例化时 `svalid`/`sdata`/`sid`/`slast` 悬空（:216-219）、`mready` 绑 1
  （:222）。**当前查到的批完成判定链没有接入能够保证全部写响应完成的
  对账机制**；该表述**不**断言 DW_axi_gm 内部无事务跟踪（响应端口悬空
  不足以证明 IP 内部无跟踪）——但就**本判定链可接入的信号**而言，
  B 完成不可见。
- 仅 `sresp` 经 `axi_err`（stb_ur_axi_conv.v:283）进信用错误位（见 R7，
  当前被丢弃）；crd_stbaxi 的 done 产生于 `i_saccept`（burst 受理）。
- 实测（门③ C7，两轮同值）：CRD_STB 先于末次 HBM 写 7 拍、CRD_STBAXI
  先 3 拍，均先于 B 响应。直连分接 `w_axi_err` 被注释禁用
  （stb_top.v:120-122）。

**结论**：真「STORE 外部完成」需独立候选 **F6-C**（**独立立项，不捆绑**）。
F6-A 即使通过，只证明所覆盖队列达到**既有信用的语义上限**，**不得宣称
STORE 外部完成**；TB 侧 S4 屏障纪律继续兜底。**F6-A 的目标限定为
「本批发射与信用正确对账」（第三轮裁决重申）。**

---

## 6. 选项与决策框架（v3 更新）

| 选项 | 内容 | 状态 |
|---|---|---|
| **F6-0** | 不改 RTL；TB 辅助闭环+限制落账为论文口径 | 保底（继续作为中期保底成果） |
| **F6-A** | C-F6 v3（C-BASE/C-N/C-DRAIN/C-PAYLOAD/C-ERR） | **D1 双基线方案获准继续；进入定向测试与隔离实验实现准备；契约与测试冻结前不改功能 RTL** |
| **F6-B** | dly_queen 内批边界感知对账 | 不推荐（批语义渗入执行层） |
| **F6-C** | B 响应对账（真 STORE 外部完成） | 独立立项，不捆绑 |

F6-A 改动面（设计级估计，实施前再核）：`inst_queen`（载荷注册
§4.4——`inst_amount` 已存在，加选通与 valid）；`dly_queen`/`smi_iq`
（计数器与 err 暴露连线）；`lsu_complete`（重写为静默窗基线+载荷捕获+
联言，**模块级参数/per-instance 使能——IQ24-27 启用新判据，IQ92 实例
保留旧行为**（Q7 范围限定））；`smi_top`（连线与错误位保留）；
`smi_ctrl_regs`（E3 错误通知接入 int_err、E4 failed_latch 与 START
弹出封锁）。

---

## 7. 影响面评估（v3 修订）

1. **功能面**：IQ24/25/26/27 四队列完成语义变更（**IQ92 保留旧行为**，
   Q7 范围限定）。验收口径=**「绝不早于本批真实完成条件」**（撤回 v1
   「完成只晚不早」的 blanket 论证——`N==0` 首个求值拍完成等路径可能
   改变旧时序，逐用例核对）。
2. **上板路径**：llama r2 走同一 batch_done 路径；**旧板端 PASS 不继承
   给修改后的 RTL**。手写线三处经验避险（RECON §8）修复后是否可退役
   另行评估，不得默认退役。
3. **START 链式批**：修复消除「提前 done 传递链式批」污染通道；E4 的
   failed_latch 与 START 封锁改变错误后行为，回归须覆盖。
4. **版本与权属（第二轮裁决 5）**：
   - 实验基线=**冻结实际用于 VCS 的 RTL 及其依赖、参数与哈希**
     （`u280_sim_v422` 无 git：以 sha256+mtime 清单记账，含 `smi/` 全目录、
     `data_engine/` 相关子树、TB 与编译参数）；在**隔离副本**中准备实验。
     本轮落地：冻结清单 `verification_v2/artifacts/f6_rtl_freeze_20261008/`
     （139 文件，首版 79 文件漏 smc/ 已扩围修正）；**隔离副本已建立并
     逐哈希校验**（2026-10-09 前完成）：
     `/home/juno1023/u280_sim_v422_f6a_20261008/`，139/139 一致
     （`ISOLATE_VERIFY.log` ALL-OK）；副本内实验不得回写原始树。
   - 不以「本仓库版本」笼统指代沙箱状态。
   - 本地技术评审不等上游；但实验修改**不代表**拥有对外分发或正式替换
     交付版本的授权——**正式合入、交付或上板前**，核对项目已有授权与
     上游（InchiTech）版本；`.pre_sync_reset` 备份提示硬件方曾维护
     `smi_iq_lsu_complete.v`。
5. **复位与异常**：SMI 域软复位相干冲刷（C-BASE）；**cfg_rst 不触达
   信用源（R10）**，恢复路径按 E4 未定案声明处理；outstanding 2bit
   饱和视图不再被新判据消费。

---

## 8. 评审发现清单（R1-R9 保留 + v3 新增 R10-R12）

- **R1**：`ir_err` 与 `batch_done` 共享 `intid_hw` 自增（:281-286）。
  裁决：**独立登记，不扩大寄存器接口改动**；错误语义经 C-ERR 落地
  （E3 Q6 定案后，共享自增成为**特性**：通知序号统一编码「有事件」，
  区分靠 int_err/done_r）。
- **R2**：dly_queen err 汇 ir_err，但不阻断完成——由 E1/E1' 关闭。
- **R3**：`w_axi_err` 直连分接禁用（stb_top.v:120-122）；F6-C 若立项
  须先恢复/替代观测。
- **R4**（已核销，v3 限定）：SMI 域复位滞留疑虑不成立（smi_rst_n 含
  cfg_rst_pulse）；**但仅覆盖 SMI 域**，系统级恢复见 R10/E4。
- **R5**：outstanding 2bit 饱和视图对零检测无损。
- **R6**：`int_err` **不被 ir_err 置位**（仅 start_q_full/int_q_full，
  :278-284）——错误中断与成功完成 host 侧不可区分 ⇒ E3 实施时接入。
- **R7**：信用单元错误位（7bit bit6）在 smi_top 重映射 :494-497
  **被丢弃**；ir_err 仅反映 dly_queen 簿记自检 ⇒ E2。
- **R8**：错误后 `glue_busy` 不清 ⇒ 批楔死、START 停摆至 cfg_rst ⇒
  E4 持续闭锁+恢复路径。
- **R9**：`r_wr_inst_cnt[i]` 于 `rdi_done && rdi_iqeid==i` 清零
  （inst_queen :176-178）⇒ 载荷注册须不晚于该清零 posedge（§4.4
  逐拍表 1，NBA 读清前值）。
- **R10**（v3）：**cfg_rst 不触达信用源**——`u_smc`（SMC_ALL，含
  STB/LDB/DS 引擎）复位仅 `rst_n`（aiacc_top_f.v:1916-1918，端口表
  无 cfg_rst）；cfg_rst 由 SMI 内部产生、经 `o_cfg_rst` 上行
  （:1900）且 TB 悬空（tb:1655）。⇒ 复位后在飞信用/B 响应可迟到
  返回；E4「不得宣称 cfg_rst 后必然安全恢复」的事实基础。
- **R11**（v3）：`int_q_full = ((intid_hw_r + 8'd1) == intid_sw_r)`
  （:118/:122）——「中断队列满」= 8bit 通知序号空间回卷保护
  （255 条未消费即满）；满时 :282 置 int_err 并抑制自增。⇒ E3
  可观测性条款与 T-INTFULL。
- **R12**（v3）：IQ92 信用源 = `u_smc` 实例的 `ds_crd_ds_o`
  （aiacc_top_f.v:1974），6bit 链 `crd_ds_chain`（:463/:468，
  `wire [5:0]`，**无错误位**），跨 SMC 级联（:2097）。DS 引擎内部
  「一 inst_valid ↔ 一信用」对应与行形态**未审计** ⇒ Q7 范围限定
  （IQ92 保留旧行为）的事实基础。
- **R13**（v3.1）：`smi_iq_lsu_complete.v` 与其 `.pre_sync_reset` 备份
  **sha256 逐字节一致**（均 =
  `40b4573d20659c8767cd7caef8decf67ee90016d2083452bd9077a073cea149c`）。
  这是该文件「当前内容=备份时点内容」的**正面哈希证据**；清单 mtime
  列仅为元数据，不作历史断言（执行令步③口径）。

---

## 9. 定向验证计划（v3：首批优先级按第三轮裁决列明）

**9.0 首批验证优先级（裁决原文落入）**：

1. **T-PREARM**：武装前**部分或全部**信用已返回（3.2 反例类）——
   构造批使 dispatch-done 前已有信用返回（大描述符数批：读出 ~2-3
   拍/行、AXI ≥15 拍，60 行级批中大部分信用先于 dispatch-done；或
   lsu_complete 单元级定向激励）。验收：v1 快照语义（基线在
   dispatch-done）**永不完成→超时 RED**；C-BASE 方案 **GREEN**。
2. **T-EMPTY / T-LASTCRD / T-ERRSYN**：
   - T-EMPTY：空批——载荷 N=0，首个求值拍完成（兼容用例，两版可皆
     GREEN，但新版时序须与逐拍表 2 一致）；
   - T-LASTCRD：最后一笔信用——done 不早于末信用计数可见拍
     （绝不早于真实完成条件的边界实例）；
   - T-ERRSYN：**末笔信用与错误同拍**——错误优先，不得登记成功。
3. **T-FAILLOCK**：FAILED 后，**队列中已排队 START 与新写 0x020 均
   不得执行**（观测 glue 不弹出、无新 rdi_start、拒绝可观测）；恢复
   （cfg_rst）后新批方可执行。
4. **T-RSTLATE**（v3.1 按执行令步②重写，三子项）：**复位后迟到信用
   不能触发新批或假成功**——①批在飞时 cfg_rst ⇒ failed_latch 置位，
   其后新写 START 不执行；②cfg_rst 后注入迟到信用 ⇒ 无批武装、无
   联言求值、无 done；③rst_n ⇒ 锁清除、计数归零，新批正常 GREEN。
   TB-B（ctrl_regs 级）承担。
5. **T-ERRLATE**：目标计数已齐（联言前三项成立）的当拍/之后错误
   到达——同拍：错误优先不登记成功；其后到达：属完成判定后的错误
   事件，按 E3 独立通知一次（语义见延迟审计表结论）。
6. **T-INTFULL**：int_q_full 路径（构造 host 不消费 255 条通知）——
   int_err 置位、自增抑制可观测，无静默丢失。

**9.1 既有分级计划（v2 保留，口径不变）**：

- **测试分级**：**竞态复现用例**须旧版 RED/新版 GREEN；**兼容用例**
  （空批、复位、常规形态）允许两版皆 GREEN。所有「绝不早于真实完成
  条件」断言以**注册后时序**表述。
- **T-RACE**：门② L1/L2 原反例形态（旧版可复现提前 16/31/33 拍）。
- **T-FORM**：支持形态 GREEN；**拒绝形态响亮失败**——VLD=0 行、LSU
  LOOPS>0、INIT/TERM 混入。**v3 扩展**：拒绝检查须覆盖**最终指令包
  的一切入口**（case_gen 之外：手写包、历史包直接注入），拒绝报告
  含队列/行/字段/原因。
- **T-ERR**：信用丢失→不完成+超时响亮；id 失配/超发→err_sticky 阻断、
  int_err 置位、done_r 保持 0、intid 恰自增一次；单元错误位
  （STBAXI 注入 SLVERR）→同上；FAILED 锁存与恢复（T-FAILLOCK/
  T-RSTLATE）。
- **T-BOUNDARY**：静默窗扫描（快照拍在 [S,S+3) 内平移等价）；同拍
  发射+信用；软复位中途中止批；批起点不变量注入破坏→响亮拒绝；
  连续不同 sid 批（逐拍表 3）载荷不重叠。
- **回归与复验**：门②③重跑（waited 等实测常数按新时序**重新登记**，
  不得沿用旧常数冒充不变量）；START 链 ≥3 批无交叠；pytest 全量
  （当前 535）只增不减；llama r2 VCS 等价用例。
  **板级复验另行裁决**：VCS 全绿后提交 RTL 差异、版本指纹、影响路径、
  回归结果、构建与回滚方案，再定板级窗口；**不因 VCS 全绿自动上板**。

---

## 10. 裁决记录与实施准备项（v3 更新）

**裁决记录（三轮全部落定）**：

1. ~~F6-A 批准与否~~ → **已答**：方向批准；D1 双基线首选；第三轮确认
   「进入定向测试与隔离实验实现的准备；边界先落入契约和测试，再改
   功能 RTL」（本稿即边界落地）。
2. ~~R1 处置~~ → **已答**：独立登记、不改编码；错误语义经 C-ERR 落地。
   ~~Q6~~ → **第三轮已答**：intid **继续自增**作通知事件序号；同一失败
   只通知一次；int_err+done=0 区分；同拍错误优先；int_q_full 入测试
   （E3）。
3. ~~F6-C~~ → **已答**：独立立项不捆绑。
4. ~~板级复验~~ → **已答**：另行裁决，提交材料清单见 §9.1；旧 PASS
   不继承。
5. ~~版本权属~~ → **已答**：本地冻结基线可用于实验（隔离副本+哈希
   清单）；上游确认为正式合入/交付/上板前提。本轮落地冻结清单。
6. ~~Q7~~ → **第三轮已答**：**准入拒绝**；检查覆盖最终指令包一切入口，
   报告队列/行/字段/原因；**IQ92 限制修复范围**（四队列支持、IQ92
   保留旧行为、DS 审计为未来前置）。
7. ~~只读核验之后的路径~~ → **第四轮已答（2026-10-09）**：**先补齐错误
   处理与复位锁定的只读核验**（错误来源及延迟、同拍错误与 done 的
   优先级、cfg_rst 清理范围、在飞状态判断、rst_n 是否覆盖旧信用来源；
   继续区分旧行为与新增契约要求）；随后**分两阶段实现**——阶段一修
   提前完成（计数对账+批次载荷+规定采样时序，验证正常完成路径与
   compat，**仅签收「提前完成修复」，不宣称 TB-A/TB-B fixed 全
   PASS**），阶段二补齐错误与 FAILED 语义（E1-E5、R-DELAY-0、恢复
   边界）后执行完整 fixed 验收与既定回归。**最终按完整 C-F6 v3.1
   契约验收；拆分的是实现与取证顺序，不是降低验收要求**。阶段一
   接口设计须容纳后续错误抑制与锁定，避免做好后推翻。三处措辞
   收紧：①「N=1 竞态」限已核实的发射/采样时序与初始状态，不推广
   到未核实执行路径；②host 毫秒级等待仅解释旧测试未暴露问题，无
   板上直接时序证据不断言「真实写入必然已完成、因果无害」；③问题
   集中在完成判定链，但修复需接入发射数/信用/批次信息，不提前断言
   改动全部只在 lsu_complete 内。不重启大方案讨论、不必每步等批准；
   原树、冻结基线、IQ92、STORE 外部完成保持不动。

**实施准备项（非裁决问题，进入实现前按序完成）**：

1. ✅ **已完成（v3.1）** 错误源流水线延迟审计表（边界① §4.3）：
   枚举错误源全部与信用计数**同块同拍注册**（相对延迟 0）；
   R-DELAY-0 钉死后续接入规则。
2. ✅ **已择定（v3.1，按执行令步②固定）** 恢复路径（边界② E4）：
   **rst_n 唯一解锁**（cfg_rst 不触达信用源，R10）；未经证明的
   静默等待/信用屏蔽**排除**为恢复依据（v3 的 (a)/(b) 待择项删除）；
   T-RSTLATE 三子项见 §9.0-4。
3. ✅ **已完成（v3.1）** 冻结清单（139 文件）+ 隔离副本建立并
   逐哈希校验（§7.4，`f6_rtl_freeze_20261008/`，副本
   `/home/juno1023/u280_sim_v422_f6a_20261008/`）。
4. ✅ **TB-A 已实现，legacy 基线 AS-EXPECTED**（2026-10-09，
   执行令步④前半）：C1 原始竞态复现（done 早于末信用可见 8 拍）、
   C5 同拍错误漏放复现（err 于 145 可见后旧模块仍在 146 放行 done——
   旧模块无错误输入），
   C2/C3/C4/C6 PASS；归档
   `verification_v2/artifacts/f6a_unit_legacy_baseline_20261009/`。
   ✅ **TB-B 已实现，legacy 基线 AS-EXPECTED**（2026-10-09，执行令步④
   后半；DUT=隔离副本内整个 smi_top 壳，host 侧全走真实 AXI，信用在
   u_smc 边界端口注入并逐笔记录）：
   B1 T-FAILLOCK 三违约全命中（R6：err 仅 intid+1 不置 int_err；
   cfg_rst 清锁后新批照样发射；1 指令批零真信用竞态完成 eid=2）；
   B2 T-INTFULL PASS（R11 机制完好：50→255→int_err+intr）；
   B3 T-RSTLATE 字面序 PASS（诚实注记：旧信用先于新 START 时 legacy
   形态为 executing 下溢静默楔死，host 可观测面与锁定不可区分，鉴别
   归 B4/B5）；
   B4 1 指令批竞态抢先完成（intr 早于旧信用 13 拍）；
   **B5 2 指令批真·归因假成功实证**（两笔 cfg_rst 前批次的旧信用
   id=0/1 凑足 cru=2，intr 晚于第二笔旧信用 4 拍——R10 危害在集成
   壳内复现）；普遍观察：legacy 上一切 N=1 批完成都是竞态完成
   （对照批 D 的 intr 也早于其真信用 2 拍）。归档
   `verification_v2/artifacts/f6b_smi_legacy_baseline_20261009/`
   （实测报告+三份原始日志+12 项 sha256）。
   步④至此全部完成；其后为隔离副本内最小 RTL 修复（不重启大方案讨论）。
5. （未来若主张 IQ92 支持）DS 信用语义审计（R12）。
6. ✅ **只读核验完成（2026-10-09，用户只读核验令步①-④全部执行；
   原树与冻结基线全程未动，未改任何 RTL）**：

   **步① 旧 RTL 完成条件**：版本确认 4.22（`PARAM_RTL_VERSION=
   32'h0004_2200`，与板证据一致）。完成链：inst_queen `rdi_done`
   （批末指令**离开** inst_queen 时单拍脉冲）→ lsu_complete
   IDLE→WAIT_DRAIN → 首个 `outstanding==0` 采样拍即置 done。
   **lsu_complete 不持有 N、不做信用对账**（45 行全模块通读）。
   空窗结构证实：dispatch 先于 dly_queen 发射注册（iic 转运+mem
   读出+FSM ≥1 拍），WAIT_DRAIN 首采样可撞见 0。

   **步② 以前为什么通过（mul_scalar v422 包 host runner 通读）**：
   stage_2 的 STB=IQ24、STBAXI=IQ25 均 **INST_LINES=1（N=1）**
   （data_list 钉死 IQ_ID）→ 按步③结构必然性，板上该两队列的完成
   同为竞态完成（**强推断**：RTL 逐字节同源+版本 0x42200 一致+集成
   实证；非板上直接观测）。通过原因=**host 时标遮蔽**：轮询粒度
   `poll_s=10ms`、CSR settle 1ms、DMA 回读 ms 级，对 ns 级竞态窗口
   （单元 8 拍、集成 2-13 拍）有 5-6 个数量级余量——这解释
   **为什么旧测试未暴露问题**；但无板上直接时序证据，**不断言**
   「真实写入必然已完成、因果无害」（第四轮裁决收紧②）。runner
   **从不检查提前 done**
   （`wait_stage_done` 仅 `done&0x1`；唯一交叉检查 int_delta≠1 为
   非致命 WARN，且竞态完成恰好 intid+1，连 WARN 都不触发）。
   仿真报错与板上 PASS 不矛盾：裁决器按契约钉拍，host 按时标轮询。

   **步③ C1 逐拍复核**（trace TB 新增 `f6a_unit/tb_f6a_c1_trace.v`，
   激励与归档 case_c1 逐语句一致；归档文件全部未动）：TB 语义核验
   六项全过（V1 DUT 冻结字节级真实 / V2 outstanding 公式与 dly_queen
   :174 逐字符一致 / V3 posedge NBA 采样时序逐拍等价 / V4 同拍发射+
   信用保持语义一致 / V5 dispatch 单拍脉冲一致 / V6 激励可达性——
   集成壳实测更强形态）；诚实注记 Δ1（TB 钳位 vs 真实下溢楔死，C1
   不触达）Δ2（cru/crd/err 端口 legacy 忽略）均惰性。**复现逐字段
   一致**：done@22、末信用可见@30、**done 早 8 拍**；决定拍铁证——
   done 置位 posedge 上 `cru=1 ∧ crd=1`，批的指令 2、3 尚未发射。
   NBA 采样语义推论：同拍注册也救不了，逃逸唯一途径是 `exec_cnt>0`
   严格早于首采样拍。在已核实的发射/采样时序（dispatch→dly_queen
   发射注册 transit ≥1 拍）与已核实的初始状态（批开始无在飞残留、
   exec_cnt=0）下，N=1 批的首个 WAIT_DRAIN 采样必见 0 → 竞态完成；
   **该结论不推广到未核实的执行路径**（非零残留初始态、其他 queen
   /转运配置等；第四轮裁决收紧①）。归档
   `verification_v2/artifacts/f6a_c1_trace_20261009/`（5 项 sha256）。

   **步④ 对照新旧规格分类**：
   - **已证实的旧实现缺陷**：①提前完成竞态（三层证据：冻结字节级
     结构+单元级确定性重建×2+集成级归档载体）；②N=1 批在已核实
     时序与初始状态下竞态完成（NBA 采样语义，同拍注册不救；适用
     边界见步③段末，不推广到未核实路径——第四轮裁决收紧①）。
   - **C-F6 新增要求**（旧 RTL 从未声称满足，不构成回归）：C-N 计数
     完成、C-DRAIN 信用对账、C-PAYLOAD 载荷、C-ERR（E1-E5）、E4
     FAILED 锁定、SAMP=S+1 钉死；R6（int_err 不置位）如实记录为旧
     行为，E3 是新要求。
   - **尚待确认**：错误处理与复位锁定的补齐核验（第四轮裁决：继续
     执行，为当前任务）；IQ92 与 STORE 外部完成仍范围外；板上竞态
     完成为 RTL 同源推断，直接观测需板端探针（未做）。
   - **修改范围初判**（read-only 结论，未实施；第四轮裁决收紧③）：
     问题集中在完成判定链，但修复需接入发射数（dly_queen cru）、
     信用（crd）与批次信息（inst_queen N+sid 载荷），**不提前断言
     缺陷及改动全部只在 lsu_complete 内**，改动面以第一阶段设计为
     准。实施路径已裁决（§10 裁决记录 7）：阶段一=提前完成（计数
     对账+批次载荷+规定采样时序，仅签收「提前完成修复」，不宣称
     TB-A/TB-B fixed 全 PASS）；阶段二=E1-E5+R-DELAY-0+恢复边界，
     其后完整 fixed 验收与既定回归；**最终按完整 C-F6 v3.1 契约
     验收，拆分不降低验收要求**；阶段一接口设计须容纳后续错误
     抑制与锁定。
7. ✅ **只读核验②完成（2026-10-09，第四轮裁决第 1 项：错误处理与
   复位锁定；纯源码核验无新仿真，引用文件 8/8 哈希复核等于冻结
   清单）**。五问逐项落案（每项区分旧行为与 C-F6 新增要求）：

   **Q1 错误来源及延迟**：
   - 唯一接入完成链的错误源 = dly_queen `err`（:184-202）：信用到达
     拍注册，条件 `(crd[3:0]!=w_crd_inst_id)||(crd>cru)`（**预增**值
     比较）→ 对该源 **R-DELAY-0 成立**（与信用计数同 posedge）。
     err 粘性置位，仅 smi_rst_n 清（cfg_rst 可清，见 Q3）。
   - 链路：IQ24-27/92 err → smi_top :388 `ir_err` OR → ctrl_regs
     `ir_err_in` → 边沿检测 `ir_err_evt`（:120，1 拍延迟）→ 与
     batch_done 同一入口仅令 **intid_hw+1**（:281-286；信用事件→
     intid 共 2 posedge）。**int_err 不由 ir_err 置位**（仅
     start_q_full_evt :279 / int_q_full :282）——B1 实测
     （intid_err=1∧interr_err=0）的源码级印证，R6 成立。
   - **源码观察①（检出 off-by-one）**：越界判定用预增 `crd>cru`，
     **首笔越界信用（crd==cru 时到达）不触发 err**，第二笔起才检出；
     与 B5 实测（两笔旧信用被静默吸收、无 err）一致。
   - **源码观察②（错误通知屏蔽）**：err 粘性 + OR 后单一边沿检测 ⇒
     任一 LSU 队列 err 置位期间，**所有后续错误（含其他队列）在
     smi_rst_n 复位前不再产生通知边沿**（静默）。
   - 死路登记：IQ0-23/28-66/桥 err → `iq_err_sink[]` 声明后**无任何
     消费者**（:260/:583 正面取证）；`err_ds_t`（IQ93）同；dly_queen
     `error_inst_queen` 向量在 smi_iq 实例化中**未接线**（:327-348）。
   - 契约侧：E1-E5、err→FAILED 锁存、同拍双排除均为新增要求；
     off-by-one 的正确边界（`crd>=cru` 时到达即越界）与屏蔽效应处置
     归入阶段二设计输入。

   **Q2 同拍错误与 done 的优先级**：
   - 单元级：lsu_complete 无错误输入，done 无条件（C5 已实证）。
   - 系统级（ctrl_regs :281-297）：`ir_err_evt||batch_done` 同一
     intid++ 入口——**同拍仅一次自增**；batch_done 照常置
     done_r/eid、弹 start_q；错误不置任何标记。**同拍错误+完成 →
     host 见 done=1、intid+1、int_err=0，与干净成功不可区分**。
     旧实现无优先级、无排除。
   - 契约侧：E4 同拍错误优先+done 保持 0 为新增要求。

   **Q3 cfg_rst 清理范围**：
   - ctrl_regs（raw rst_n 域，smi_top :373）：CFG[1] 写 → 单拍
     cfg_rst_pulse + **全部可变状态清零**（:239-260 清单与 :204-228
     复位清单逐项等同：intid_hw/sw、int_err、done_r/eid、active_en
     /sid、glue_busy、done_bitmap、start_q 全项、iq_en、cfg_en）。
   - smi_rst_n（:232）单拍低扇出：bar_packer(:343)、dispatcher(:359)、
     全部 smi_iq（含 IQ24-27/92）、lsu_complete×5(:592-621)、
     ds_t(:639)——**dly_queen 的 cru/crd/executing/err 全清**（B1
     cfg_rst 后新批发射、B3 下溢楔死、B5 旧信用归因均实证一致）。
     **附带效应：粘性 err 被清 → 错误通知在 cfg_rst 后重新武装。**
   - **不触达**：u_axi_wrapper/DW_axi_gs（raw rst_n，:399）、u_smc
     及其信用源（R10；o_cfg_rst 全机唯一消费者为 TB——全树 grep
     正面取证仅 tb_aiacc_top_v04_f_multi_stage.v）。
   - 契约侧：E4「在飞 cfg_rst 视同 FAILED + rst_n 唯一解锁」为新增
     要求；R10 源码复核成立。

   **Q4 在飞状态判断**：
   - host 可读寄存器全集（read mux :366-376 + default 0）：CFG[0]=
     cfg_en、INT{intid_sw,intid_hw,int_err,intr_ro}、IQ_EN×4 回显、
     START sid 回显、DONE{done,eid}、RTL_VERSION。**没有任何
     busy/在飞/outstanding/credit 计数可读**；glue_busy/active_en/
     done_bitmap 存在但无 host 通道。
   - 契约侧：FAILED 可观测性（int_err 置位+done 保持 0）为新增
     要求（E3/Q6）；在飞判断由契约内 ctrl_regs glue 状态承担，
     无需新增 host 寄存器（阶段二设计若另定再议）。

   **Q5 rst_n 是否覆盖旧信用来源**：
   - 链路逐跳直接读码：aiacc_top_f :1916-1918 `u_smc.rst_n(rst_n)`
     （唯一复位，无 cfg_rst）→ SMC_ALL.sv :518 `u_stb.rst_n(rst_n)` →
     stb_top → **stb_ur_axi_conv.v :804 GEN_CRD_STB_O**（negedge
     rst_n 异步复位：crd_stb_o<=0+valid/busy 全清）、**:1161
     GEN_CRD_STBAXI_O**（:1164 同型）、**ldb_ur_axi_conv.v :1482**
     （crd_ldb_o）、**:1000-1001**（crd_ldbaxi_o）——四路信用输出
     寄存器及其跟踪状态**全部直接读到复位分支**，无推断残留。
   - **结论：rst_n 覆盖信用源**。rst_n 后 STB/STBAXI/LDB/LDBAXI 信用
     发生器无残留、输出寄存器归零（复位释放不产生伪信用脉冲）。
     E4「rst_n 唯一解锁」因此自洽：rst_n 同清完成机（smi 侧）与
     信用源（u_smc 侧），解锁后无旧信用滞留。cfg_rst 不覆盖信用源
     （R10）的危害已由 B5 实证。
   - 范围外：DS（IQ92）信用源 u_smc.ds_crd_ds_o 6bit 链未审计
     （R12 保留）。

   **对步④三分类的追加**：
   - 旧行为新增登记（源码+既有实测一致）：err→intid 通知但 int_err
     不置位（R6）；err 检出 off-by-one（首笔越界不触发）；粘性 err
     +OR 边沿 ⇒ 后续错误静默（复位前）；同拍 err+done 单自增且
     done 照常置位（成功外观吞错误）；cfg_rst 全清 smi 侧（含粘性
     err→通知重新武装）；IQ0-23/28-66/桥/93 err 与 error_inst_queen
     全为死路。
   - C-F6 新增要求（旧 RTL 无对应机制）：E1-E5、同拍错误优先、
     FAILED 锁存挡新 START、E4 rst_n 唯一解锁的锁存实现、错误
     边界 off-by-one 的正确语义。
   - 尚待确认：DS 信用链（R12，范围外）；板上行为（需探针，未做）。
     本轮无新增「尚待确认」源码问题——五问全部以直接读码闭环。

## 11. 阶段一实现记录（2026-10-09，用户阶段一令验收）

授权原文：「同意按既定范围进入阶段一实现，不再增加方案讨论。本轮只验收
『提前完成修复』，错误抑制、通知与 FAILED 锁定留在阶段二。」实现范围 =
C-BASE/C-N/C-DRAIN/C-PAYLOAD（C-ERR/E4/恢复边界留阶段二），支持范围
IQ24–27（IQ92 保持 legacy 实例，R12）。

### 11.1 实现清单（隔离副本，6 文件；全树 diff 正面证据仅此）

- **新增 smi/smi_iq_lsu_complete_f6.v**：SAMP=start_pulse posedge 快照
  cru_base/crd_base（C-BASE，批起点不变量 cru≠crd → 拒绝武装）；
  dispatch_closed_valid 脉冲捕获载荷 {sid,N}（C-PAYLOAD）；完成条件
  `dispatch_closed ∧ !err_sticky ∧ !err_now ∧ (cru−cru_base)==N ∧
  (crd−crd_base)==N`（64 位模减；N==0 首个求值拍完成）（C-DRAIN）；防御性
  异常（双重载荷/基线违例）=**阻止完成**（本阶段无 err 输出端口，对外
  只能由 host/外部超时机制发现——措辞按 §11.5 措辞勘误收紧）。
- smi_iq_dly_queen.v：+cru_cnt_o/crd_cnt_o/err_now_o（纯暴露，零逻辑改动）。
- smi_iq_inst_queen.v：+载荷三联输出，r_wr_inst_cnt 清零 posedge NBA
  清零前读捕获（R9 窗）；原 FSM 未动。
- smi_iq.v：6 总线透传。smi_top.v：6 总线 + IQ24–27 换 F6 实例
  （start_pulse=csr_rdi_start&&rdi_active_en[ID]，err_sticky=四路 err），
  IQ92 不动。smi_rtl.f：+新文件。
- **未动**：smi_iq_lsu_complete.v（40b4573d…149c 冻结）、smi_ctrl_regs.v、
  原树、冻结清单、已归档 legacy 证据。
- 归档：`verification_v2/artifacts/f6_phase1_rtl_20261009/`（diffs+
  指纹+范围证据+SHA256SUMS）。

### 11.2 TB-A 单元验收（fixed，AS-EXPECTED 六用例全 PASS）

- **C1 原始竞态转绿**：legacy done=22（dispatch 空窗放行，末信用可见=30，
  −8 拍）→ fixed done=32=lcv+2（在当前注册级数、接口与采样约定下的
  最早合法拍——NBA 论证见 f6a_unit_check.py 勘误块；不称「物理最紧」）。
- **兼容逐拍一致**：C2 66 / C3 86 / C4 116 / C6 180+196 与 legacy 归档
  bit 一致——合法路径时序未被改动。
- C5 同拍错误：done −1（阻断）vs legacy 146（漏放）。
- 归档：`artifacts/f6a_unit_fixed_p1_20261009/`。
- **钉拍勘误（判据非 DUT）**：冻结钉拍 C1/C2/C4==1、C3==2、C6==1/1 系契约
  →TB 监测口径翻译偏差一拍，已勘误为 ==2/==3/==2/==2（三重独立证明：
  契约 §4.3+归档 C1 trace t=lcv；NBA 不可能性（计数器-only 冻结接口）；
  legacy 归档实测恰 2/3/2/2/2）。期望矩阵与 legacy >= 判据不变，勘误记录
  于 f6a_unit_check.py 头部。

### 11.3 TB-B 壳级验收（fixed_p1 首跑=AS-EXPECTED，对照首跑前冻结矩阵）

- **B1**：doneC 1→0、eidC 2→0（壳内竞态修复实证）；intid_prerstn 1→0
  （无假完成通知；cfg_rst 清 intid 后 legacy 唯一的 rst_n 前通知即 C 假完成）。
  遗留 RED=interr_err（R6/E3）+iss_postcfgrst（E4），恰为判据 fail 清单。
- **B4**：intr_cyc 3374→3392，从早于旧信用 13 拍（纯竞态）移至旧信用可见
  +5（计数闸住）；first_issue/late_cyc 两版逐拍一致。遗留 RED=归因假成功
  doneC=1（E4/R10）。
- **B5**：与 legacy 逐字段 bit 一致（N=2 本就归因形态），完成严格在第二笔
  旧信用后；遗留 RED 同 E4。
- **B2**：bit 一致（260 空批系 ctrl_regs 内部退化路径，不触达）。
- **B3**：判据字段 bit 一致（字面 PASS 保持）；信息项 intr_cyc +6
  （3231→3237）=D 恢复批从竞态完成转为真信用完成，修复在楔死共存路径
  同样生效的直接证据。
- 归档：`artifacts/f6b_smi_fixed_p1_20261009/`。legacy 模式哈希守卫按设计
  拒绝改后副本（exit 6），legacy 证据唯一载体为归档基线。

### 11.4 阶段二 RED 清单（本阶段刻意不修，不得计作 fixed 通过）

1. E3/R6：错误通知不置 int_err（B1 interr_err=0）。
2. E4 FAILED 锁存：错误后新 START 不挡（B1 iss_postcfgrst=1、B4/B5
   iss_p2>0）；cfg_rst 后旧信用归因新批假成功（B4/B5 doneC=1）。
3. R10 系统恢复：cfg_rst 不触达信用源；复位清零≠在飞事务安全已证（保留
   边界，未关闭）。
4. crd>=cru 同拍判据：保留边界，待阶段二定向测试（当前实现对 cru/crd
   对称要求 ==N，未采用 crd>=cru 捷径）。
5. R-DELAY-0 布线（err_now 当前由 dly_queen 组合暴露，与信用同拍）、
   dly_queen 检出 off-by-one、executing 下溢楔死：均未动。

### 11.5 复核补丁：重复载荷同拍漏洞（2026-10-10，用户复核裁定）

**发现（用户 diff 复核）**：原实现中「重复载荷 → arm_err<=1」与「完成判定
读 arm_err」在同拍求值时存在 NBA 竞态——本拍检测到重复载荷，arm_err 要
下一拍才生效，完成判定仍读旧值 0 ⇒ 「重复载荷恰逢计数达标」会同时记异常
又发 done，与「重复载荷拒绝完成」的注释语义矛盾。

**修复**（smi_iq_lsu_complete_f6.v，改动仅该文件）：
`wire w_payload_anom_now = dispatch_closed_valid && dispatch_closed;`
——当拍协议异常显式并入完成排除条件（协议异常对完成有明确优先级，本拍
即阻断）；arm_err 锁存路径保留（后续拍继续阻断）。start_pulse 重新武装
语义与「同拍 start∧payload 时 start 优先」在头部注释补记。

**定向测试 C7（TB-A，判据首跑前冻结）**：N=1 批末信用使计数达标，重复
载荷窗自末信用撤离拍起连开 3 拍、覆盖完成判定首次通过 posedge 的两种
口径可能；后接恢复批正控。
- **变异核验（测试牙齿实证）**：移除排除项的变异 DUT 上 C7
  done1=218（异常与完成同拍放行，漏洞复现）→ overall=MISMATCH；
  补丁 DUT 上 done1=−1（阻断）∧ 恢复批 done2=239/d2=2（不误锁死）
  → fixed 七用例 overall=AS-EXPECTED。
- legacy 模式 C7=VIOLATION（忽略载荷必然放行，done1=218）与矩阵一致；
  C1-C6 两模式全部逐拍数值与 2026-10-09 归档一致（补丁零意外回归）。
- TB-B fixed_p1 复跑 overall=AS-EXPECTED，FACT/INJ 与 2026-10-09 归档
  **逐行 bit 一致**（壳级不触发该异常路径）；run_f6b.sh provenance 段
  补记修复模块 sha256（只打印不参与 legacy 硬检）。
- 补丁后 DUT sha256=a84d82d3…e05b；归档三件套 2026-10-10 版已封
  （f6_phase1_rtl_20261010 含评审态→补丁态单文件 diff）。

**措辞勘误（用户复核裁定，live 文档已改；2026-10-09 归档 README 为封存
历史件不改，措辞以本条为准）**：
1. 「arm_err → 永不完成＝响亮失败」→ 应为「**阻止完成；当前模块无错误
   输出端口，对外只能由 host/外部超时机制发现**」。契约层「拒绝形态响亮
   失败」（§4.1/§9 T-FORM）是目标要求，阶段一模块本身不实现上报通道。
2. 「done=32 是物理最紧」→ 应为「**在当前注册级数、接口与采样约定下的
   最早合法拍**」。
3. 维持区分：B4 完成延后只证明计数等待生效，**不代表批次归属安全**
   （旧信用归因属阶段二 E4，已登记 RED）。

---

## 12. 阶段二设计（2026-10-10，用户五项令 #2；RTL 改动前冻结）

依据（用户 2026-10-10 令原文摘录）：「F6 阶段二→错误及时检出与传播、
错误通知、FAILED 锁定、禁止 cfg_rst 单独解除失败锁定→已登记错误用例
按契约通过，错误不能变成成功」「范围锁死：IQ24–27；本拍错误和粘滞错误
都能阻断成功；错误通知一次，host 能区分失败；FAILED 后排队 START、新写
START 都不能执行；不实现通用软恢复，不纳入 IQ92、STORE B 响应对账或
其他 IP 修复」「2–3 个工作日检查点，作为预算而非完成承诺」。

### 12.1 改动面（隔离副本 4 文件 + TB-B 三件套；F6 模块不动）

| 文件 | 改动 | 契约项 |
|---|---|---|
| smi_iq_dly_queen.v | err/err_now_o 检出条件扩三源并修 off-by-one（见 12.2） | E1/E1'/E2 |
| smi_iq.v | 新增 1bit 端口 `crd_err_i` 透传至 dly_queen | E2 |
| smi_top.v | 信用重映射保留 bit6；新增 `lsu_err` 四队列 OR 接入 ctrl_regs | E2/E3/E4 |
| smi_ctrl_regs.v | int_err 置位（E3）、failed_latch（E4）、0x004[2] 状态位 | E3/E4 |
| smi_iq_lsu_complete_f6.v | **不动**（阶段一补丁态 a84d82d3 保持） | — |
| smi_iq_lsu_complete.v / IQ92 实例 | **不动**（R12；DS 链错误位补 0，行为面见 12.6） | — |

### 12.2 E1/E1'/E2：错误检出与传播（dly_queen）

现状（§4.5 取证）：`crd_i` 5bit `{done,id[3:0]}`，单元错误位 bit6 在
smi_top 重映射处被丢（R7）；err 检出条件
`(r_crd_inst_cnt[3:0] != w_crd_inst_id) || (r_crd_inst_cnt > r_cru_inst_cnt)`
为**预增**比较——首笔越界信用（crd==cru 时再收一笔）不触发（源码观察①，
§11.4 RED 项 5）。

设计：

1. **E2 接线**：信用链 7bit 的 bit6 以**独立 1bit 端口** `crd_err_i`
   经 smi_iq 透传进 dly_queen（不拓宽 `crd_i`——`{done,id}` 语义与
   29 处非 LSU 绑 0 实例不动；非 LSU 宏内绑 `1'b0`，IQ92 显式绑
   `1'b0` 并注记 R12）。STB/LDB 真实链 bit6 恒 0、STBAXI/LDBAXI 实
   错误（§4.5 取证），接线队列无关。
2. **检出条件扩三源**（err 注册块与 err_now_o 同一条件）：
   `crd_err_i || id失配 || 越界`。
3. **off-by-one 修复**：越界判据 `>` → `>=`（预增）。安全性论证：
   合法流中第 k 笔信用（0 起）到达时恒有 cru≥k+1、crd==k ⇒ crd<cru，
   `>=` 永不误伤；crd==cru（队列已排空）时再收信用恒为协议违例（无
   在飞指令不可能有真完成），应即检出。不修的危害：首笔违例信用被静默
   吸收后 crd=cru+1 污染基线，**下一笔合法信用反而触发假错误**
   （deferred false error）——检出点错位，违反「错误及时检出」。
4. **E1 批局部清零由 E4 全覆盖（设计决定）**：任何 err_sticky 沿 ⇒
   FAILED 锁存（12.4）⇒ 不再有新批武装；err 唯一清除 = rst_n（与锁存
   同步）。批起点清零路径不实现、也不需要——锁存使「下一批」在错误
   后根本不存在，直到 rst_n。

R-DELAY-0 审计表（§4.3 边界①要求，阶段二全源）：

| 错误源 | 路径 | 对完成阻断的延迟 | 同拍竞态边界 |
|---|---|---|---|
| dly_queen id 失配/越界 | 与信用同 posedge 注册（err）+ 组合（err_now_o） | 0 拍（C-DRAIN 双排除） | 信用与完成同拍：err_now 当拍排除（阶段一已验 C5） |
| 单元错误位 bit6（E2） | 与信用同总线同拍到 dly_queen，入同一检出条件 | 0 拍 | 同上 |
| err → ir_err → ctrl_regs 通知 | 组合 OR + 次拍 intid/int_err 注册 | 通知 1 拍（完成阻断不依赖通知） | — |
| err → failed_latch（E4 条件 i） | 注册 err 边沿 ⇒ 次拍置锁 | 置锁 1 拍；弹出门追加 `!lsu_err_in` 电平项，**错误可见首拍即挡新弹出**，不留 1 拍窗 | 错误与 START 弹出同拍（NBA）：弹出读旧锁存值——该拍弹出如属同队列，完成仍被 C-DRAIN err 排除阻断；跨队列属不可消除同拍竞态，host 由 int_err+failed 获知 FAILED 态 |
| cfg_rst 在飞（E4 条件 ii） | cfg_rst 写被受理当拍置锁（NBA 读清前旧值） | 次拍生效；cfg_rst 分支与弹出块互斥（同 always if/else），无同拍滑出 | — |

### 12.3 E3：错误通知（ctrl_regs）

- `lsu_err_evt = lsu_err_in & ~lsu_err_d1`（LSU 四队列 OR 边沿，**不含
  IQ92 err_ds**——R12：IQ92 保留旧行为，其错误仍只 intid 自增、不置
  int_err、不置锁）。
- 通知块（ctrl_regs :281-286 处）追加：`if (lsu_err_evt) int_err_r <= 1'b1;`
  ——intid 自增路径不动（Q6 定案）；int_err 在「同拍 int_clr 写」之后
  赋值（同 always 后者优先）⇒ 错误不可被同拍清除掩盖。
- 同拍错误∧他队列完成：单次 intid++ 且 int_err=1 = 通知记为错误（契约
  「同拍错误优先…通知路径亦按错误优先」）；0x024 done 位由 batch_done
  路径独立登记不受影响。本形态为结构分析结论，非定向测试项（B 用例无
  双队列同拍激励）。
- done 保持 0：结构性成立（err 批完成被 C-DRAIN 排除 ⇒ batch_done 不
  成立 ⇒ 0x024 不置位）。

### 12.4 E4：FAILED 锁存（ctrl_regs）

- `failed_latch`：**置位两条件** (i) `lsu_err_evt`；(ii) cfg_rst 写被
  受理且（`glue_busy==1` 或 `start_cnt!=0`）（在飞 cfg_rst 视同 FAILED，
  cfg_rst 分支内以 NBA 读清前旧值判定）。无批在飞的 cfg_rst 不置锁
  （兼容性，契约明文）。
- **唯一解锁 = rst_n**（!rst_n 分支清零）；cfg_rst 全可变清零块
  **不含** failed_latch（排除即「cfg_rst 不得单独解除锁定」的代码化）；
  int_clr 写（0x004[7]/[1]）不触达。
- **弹出门**：`if (!glue_busy && !failed_latch && !lsu_err_in &&
  (start_cnt != 3'd0))`——排队项与新写 0x020 均不触发弹出（新写仍入队，
  队列满走既有 start_q_full_evt 路径，非静默执行）。
- **可观测**：0x004[2] = failed_latch（原恒定 0 填充位，v0.4.19 未分配
  位[6:2] 内取其一；E2「不改寄存器编码」条款限信用接线，E4 状态位为
  契约「状态位/丢弃计数」二选一之明文凭据）。

### 12.5 T-RSTLATE 语义推导更新（判据演进的核心）

契约 §4.5 T-RSTLATE 验收 2：「cfg_rst 后注入迟到信用 ⇒ 不得触发新批、
不得形成假成功（无批武装，联言不求值；计数器即使被拨动也无完成路径）」。
阶段一矩阵按「迟到信用静默吸收」推导出 intid_pX==0/intr_cyc==−1。

off-by-one 修复后（12.2-3），迟到信用（crd==cru==0 时再收信用）**成为
被检出的错误**：err ⇒ 通知一次（intid+1、int_err=1、intr）+ 锁存保持。
契约验收 2 仍满足（无新批、无假成功；锁存挡弹出），额外可观测 = 超出
契约最低要求的**加强**（R10 危害由「被锁存挡住」升级为「被检出并上报」，
与「错误及时检出」范围项一致）。判据据此演进（12.6），不是降低标准。

### 12.6 判据演进与矩阵重冻结（2026-10-10 首跑前冻结；演进逐项表）

演进只升不降：所有阶段一判据保留（数值不变者照留），仅「静默吸收」
推导值被「检出上报」推导值替换，并补 E3/E4 观测项。

| 用例 | 旧判据（阶段一冻结） | 新判据（本冻结） | 理由 |
|---|---|---|---|
| B1 | （9 项） | += failed_err==1、failed_postrst==1 | E4 可观测 |
| B3 | intid_p3==0 | **intid_p3==1** += interr_p3==1、failed_p3==1 | 12.5 |
| B4 | intid_p4==0、intr_cyc==−1 | **intid_p4==1、intr_cyc>late_cyc** += interr_p4==1、failed_p4==1 | 12.5 |
| B5 | intid_p5==0、intr_cyc==−1 | **intid_p5==1、intr_cyc>late1_cyc** += interr_p5==1、failed_p5==1 | 12.5 |
| B6（新增 T-UNITERR） | — | 单元错误位信用（id 正确+bit6=1）⇒ err/通知/锁定/拒完成；rst_n 恢复 | E2 定向 |

**矩阵（6 用例）**：
`legacy: {F,T,F,F,F,F}`　`fixed_p1: {F,T,F,F,F,F}`　`fixed_p2: {T,T,T,T,T,T}`

**B3 翻转定性**：legacy/fixed_p1 的 B3 由 PASS 翻 VIOLATION 是**判据
演进非 DUT 行为变化**——阶段一「静默吸收=字面 PASS」在阶段二「及时
检出」标准下即违约（E1）；2026-10-09/10 归档（判据 v1）封存不动，
新证据以判据 v2 名义产出并互注。B3 legacy 的 executing 下溢楔死机制
（§9 诚实注记）不变——新判据下它同时是「未检出迟到信用」的违约载体。
IQ92 行为面注记：off-by-one 修复在共享 dly_queen 内，IQ92 实例继承
检出条件变化（其 err_ds 仍仅 intid 旧路径）；R12 保留——不对 IQ92 信用
链正确性作任何断言。

### 12.7 测试与运行编排（先验证判据，再改 RTL）

1. TB-B 改造：B1/B3/B4/B5 既有 0x004 读字提取 w[1]/w[2] 新字段
   （无新 AXI 流量、无激励时序变化）；新增 B6 T-UNITERR 用例。
2. f6b_check.py：判据 v2 + 三矩阵 + B6 judge（本文件与 12.6 为冻结
   载体）；模式 fixed_p2 取代从未运行的 fixed。
3. run_f6b.sh：fixed_p1 增哈希钉（对照
   f6_phase1_rtl_20261010/BUILD_FINGERPRINT.txt 五文件），阶段二改动
   后按设计拒绝（exit 6，同 legacy 模式纪律）；legacy 模式支持
   `F6B_ISO=/home/juno1023/u280_sim_v422` 指向原树（只读，不改）。
4. **先跑 legacy（原树）与 fixed_p1（当前副本）验证新判据/TB 本身**，
   再动 RTL；fixed_p2 全 PASS 为目标；pytest 不受影响（无 python 产品
   代码改动）；TB-A（DUT=未动的 F6 模块）复跑 fixed 作连续性证据。

### 12.8 不做清单（范围锁死复述）

IQ92 信用链审计与行为变更（R12 保留）；STORE B 响应对账（F6-C 范围）；
通用软恢复/quiesce window/信用屏蔽（契约明文排除）；executing 下溢
楔死本身不修（host 可观测危害已由锁存+rst_n 覆盖，内部污染 rst_n 清
除，保留为已知形态）；错误注入恢复「延迟→恢复→GREEN」新通道（沿用
rst_n 唯一解锁）。

---

## 13. 阶段二实现记录（2026-10-10，用户五项令 #2 验收）

授权原文：「先修阶段一重复载荷同拍漏洞并复跑，再启动限定范围的 F6 阶段二」；
范围锁死：IQ24–27；本拍错误和粘滞错误都能阻断成功；错误通知一次，host 能
区分失败；FAILED 后排队 START、新写 START 都不能执行；不实现通用软恢复，
不纳入 IQ92、STORE B 响应对账或其他 IP 修复。设计 = §12（RTL 改动前冻结）。
实现与验收同日完成（检查点预算 2–3 工作日内）。

### 13.1 实现清单（隔离副本 4 文件改动；p1→p2 全树 diff 正面证据仅此）

- smi_iq_dly_queen.v：新增 `input crd_err_i`；err_now_o/err 寄存器改三源
  检出 `crd_err_i || id失配 || (r_crd_inst_cnt >= r_cru_inst_cnt)`
  （off-by-one `>`→`>=`，§12.2 无假阳论证：合法信用 k（0 起始）恒有
  cru≥k+1 ⇒ crd==k < cru 不触发；同拍注册，R-DELAY-0）。
- smi_iq.v：crd_err_i 穿线。smi_top.v：SMI_IQ_LSU_INST 第 7 参 ERRB
  （24–27 接 i_crd_stb/stbaxi/ldb/ldbaxi[6]，IQ92 显式 1'b0=R12）；两处
  generate 块显式 `.crd_err_i(1'b0)`；`lsu_err=err_stb|err_stbaxi|err_ldb|
  err_ldbaxi` 接 ctrl_regs.lsu_err_in。
- smi_ctrl_regs.v：E3（lsu_err_evt=lsu_err_in&~lsu_err_d1；置于 int_clr
  写路径之后，同拍清除不可掩盖；同拍错误∧完成=单次 intid++ 记错误）；
  E4（failed_latch：置位=lsu_err_evt 或 在飞（glue_busy∨start_cnt≠0）
  cfg_rst——NBA 读清前旧值；唯一解锁 rst_n；弹出闸
  `!glue_busy && !failed_latch && !lsu_err_in && start_cnt≠0`，
  `!lsu_err_in` 电平项关闭锁存生效前 1 拍窗；0x004[2]=failed 可观测）。
- **未动**：smi_iq_lsu_complete_f6.v（a84d82d3…e05b）、smi_iq_lsu_complete.v、
  smi_iq_inst_queen.v、smi_rtl.f（§11 阶段一态指纹逐一比对一致）。
- **改动面正面证据**：重建阶段一树（reconstruct_p1.sh，8/8 指纹 ALL-OK）
  与当前副本 `diff -rq` = **恰 4 文件**（CHANGE_SCOPE_P1_TO_P2.txt）。
- 归档：`artifacts/f6_phase2_rtl_20261010/`（累计 diff+p1→p2 diff+指纹+
  范围证据+SHA256SUMS 已封）。

### 13.2 TB-B 三模式验收（判据 v2，全部首跑 AS-EXPECTED）

期望矩阵（§12.6，首跑前冻结于 f6b_check.py）：legacy {F,T,F,F,F,F}、
fixed_p1 {F,T,F,F,F,F}、fixed_p2 {T,T,T,T,T,T}。

- **legacy（原树只读）**：B1/B3/B4/B5/B6 VIOLATION、B2 PASS——与矩阵一致；
  竞态机制与 2026-10-09 归档逐拍一致（B4 intr=3374 vs credit=3387 −13 拍、
  B5 intr=credit2+4）。
- **fixed_p1（重建树，8/8 指纹）**：与矩阵一致——B1 fail 清单缺 doneC
  （阶段一竞态修复签名）、B4/B5 intr>late 成立、B6 doneA6=1（E2 孔实证）。
- **fixed_p2（阶段二 RTL）：B1–B6 全 PASS 首跑**。关键 FACT：
  B1 `interr_err=1 failed_err=1 iss_blk=0 doneB=0 iss_postcfgrst=0 doneC=0
  failed_postrst=1 doneD=1 failed_postrstn=0`（E3+E4+rst_n 唯一解锁全链）；
  B3 `intid_p3=1 interr_p3=1 failed_p3=1`（T-RSTLATE 检出上报，§12.5）；
  B4 `intr_cyc=3391 > late_cyc=3387`（1 指令批竞态→归因）；
  B5 `intid_p5=1`（两笔旧信用**一次**通知=notify-once）∧ doneC=0（R10 危害
  被锁存阻断）；B6 `doneA6=0`（**「计数恰好相等也不能形成成功」直接证据**：
  crd−base==1==N，错误排除胜出）。
- **判据演进定性**：legacy/fixed_p1 的 B3/B4/B5 由 v1 PASS 翻 v2 VIOLATION 是
  **判据演进非 DUT 行为变化**（§12.6）；v1 归档封存未动。
- **门禁实证**：run_f6b.sh fixed_p1 指向阶段二树 → exit=6 拒绝（防误复测）。
- 归档：`artifacts/f6b_smi_fixed_p2_20261010/`（三模式日志+README+SHA256SUMS）。

### 13.3 TB-A 连续性

f6a_unit fixed 模式 7/7 PASS AS-EXPECTED；DUT=F6 模块 a84d82d3…e05b
（阶段二未触）；C1 done=lcv+2、C2/C3/C4/C6 兼容数值、C7 重复载荷同拍阻断，
与 2026-10-10 阶段一归档逐拍一致。

### 13.4 签收口径与遗留（照录裁决措辞，不扩大）

- 签收：**「隔离 RTL 中，IQ24–27 在已验证合法形态和正常批次边界下，提前完成
  竞态修复通过定向验证；错误检出/通知/FAILED 锁定通过定向验证。」**
- 当前证据**不支持**「完整 LSU 完成契约已修复」或「可替换原版上板」。
- **遗留（五项令 #3，信用注入 TB 不能替代真实单元验证）**：门② LDB L1/L2
  真实 LOAD 路径、门③ M3-lite 真实 STORE 路径回归**尚未在本修复 RTL 上执行**。
- STORE 最终完成仍须保留现有验证屏障，不宣称 host 闭环已解决（用户 2026-10-10）。
- IQ92（R12）保留旧行为；executing 下溢楔死不修（§12.8）；NBA 同拍弹出边界
  文档化（§12.2 表末行）；通用软恢复/quiesce/信用屏蔽不做（范围锁死）。

### 13.5 复核裁定 errata（2026-10-10 第四轮，签收口径降级 + 四缺口登记）

**裁定原文（节录）**：「可以准备第3项真实单元回归，但阶段二暂宜记为
『所列 TB 用例通过』，还不能无条件签收 E1–E5 全闭合。」「当前不用推翻
阶段二成果，也不用扩大范围。先补控制层错误优先、错误通知和协议异常这
几个明确缺口，再进入真实路径验收。B1–B6 全 PASS 是有价值的证据，但其
覆盖范围不能代替这些尚未被激发的场景。」

**口径降级**：§13.2 的 fixed_p2 B1–B6 全 PASS 记为「**所列 TB 用例通过**
（判据 v2）」；**E1–E5 不记全闭合**。已归档
f6b_smi_fixed_p2_20261010/f6_phase2_rtl_20261010 为封存历史件（判据 v2、
TB 哈希在册），措辞以本条与 §14 为准。

**四缺口（逐项 → §14 处置）**：
1. **控制层错误同拍优先**：`if (batch_done) smi_iq_done_r <= 1` 未排除
   错误——错误与 batch_done 真正同拍时仍可能同时置错误位和成功完成位。
   （F6 模块层阻断不能替代控制层明确优先级。）→ §14.1 + B7 定向。
2. **LSU 错误通知嵌套遮蔽**：E3 置位代码位于
   `if (cfg_en_r && (ir_err_evt || batch_done))` 内——其他错误源（如
   IQ92）持高 ir_err 后，新 lsu_err_evt 无 ir_err 新边沿 ⇒ FAILED 锁住
   但本次 LSU 错误无通知/无 int_err。→ §14.1（lsu_err_evt 独立参与 +
   逐源边沿）+ B8 定向。
3. **协议异常上报未接入**：F6 模块 arm_err 无输出端口——重复载荷/基线
   违例不能触发系统 FAILED（阶段一措辞勘误已登记「只能由 host/外部
   超时机制发现」）。→ §14.1（proto_err_o 链接入）+ TB-A C7 proto 字段
   + B9 定向。
4. **crd>=cru 同拍边界证明缺**：「合法信用 k 恒有 cru≥k+1」原仅有注释
   论证，需证明所支持真实单元不会在旧计数相等时对本拍新发射立即返回
   合法信用。→ §14.2 信用源因果链代码审计。
附带登记：**0x004[2] FAILED 是软件可见接口扩展**（v0.4.19 §5.4.2.1 中
[6:2] 未分配/恒定 0）——语义与版本兼容性登记见 §14.3。
另：阶段二收口口头汇报曾漏列 smi_iq.v（实际 p1→p2 恰 4 文件之一）；
记录层（§13.1 与 f6_phase2_rtl_20261010/README 表）四文件齐全，无需
更正，仅在此登记此口头笔误。

## 14. 缺口修复轮实现记录（2026-10-10，复核裁定四缺口关闭）

裁定依据：§13.5 第四轮复核——「先补控制层错误优先、错误通知和协议异常
这几个明确缺口，再进入真实路径验收」。判据 v3（B1–B6 判据同 v2 不变 +
新增 B7/B8/B9 判据）先于本轮运行冻结于 f6b_check.py；三模式期望矩阵
（legacy/fixed_p1：B7/B8=VIOLATION、B9=SKIP；fixed_p2：全 PASS）同步冻结。
RTL 归档 = `f6_phase2b_rtl_20261010/`（p2→p2b 恰 3 文件）；验收归档 =
`f6b_smi_fixed_p2b_20261010/`。

### 14.1 三处 RTL 修复（隔离副本 p2→p2b：smi_iq_lsu_complete_f6.v / smi_top.v / smi_ctrl_regs.v）

**缺口 #2 控制层错误同拍优先（smi_ctrl_regs.v）**：`batch_done` 分支内
done/eid 登记包 `if (!lsu_err_any)`——求值拍任一 LSU 源错误电平可见 ⇒
不登记成功完成；批账务（glue_busy 清 / active 清 / done_bitmap 清 /
START 队列出队）照常退役，避免二次楔死。通知入口的 `batch_done` 项同步
加 `!lsu_err_any`——被抑制的完成不产生成功通知（错误已在其边沿通知
一次，同一失败只通知一次）。

**缺口 #3 通知遮蔽（smi_top.v + smi_ctrl_regs.v）**：`lsu_err` 由 1-bit
OR 改 **4-bit 逐源向量**（位序登记：[0]=IQ24/stb [1]=IQ25/stbaxi
[2]=IQ26/ldb [3]=IQ27/ldbaxi）；ctrl_regs 内逐源边沿检出
（`lsu_err_rise = lsu_err_in & ~lsu_err_d1`，`lsu_err_evt = |lsu_err_rise`，
`lsu_err_any = |lsu_err_in`）。通知入口条件 =
`ir_err_evt || lsu_err_evt || (batch_done && !lsu_err_any)`——LSU 错误
事件独立参与判定，不再嵌套于 `ir_err_evt || batch_done` 内：非 LSU 源
（如 IQ92）持高 ir_err 期间新到的 LSU 错误仍产生通知（裁定复现场景）；
任一 LSU 源持高亦不遮蔽他源新边沿（B8 场景）。单 if 块入口 ⇒ 每拍至多
一次 intid++（notify-once 由构造保证）。pop 闸 `!lsu_err_in` 改
`!lsu_err_any`（4-bit 化适配，语义不变：错误可见首拍即挡新弹出）。

**缺口 #4a 协议异常上报（smi_iq_lsu_complete_f6.v + smi_top.v）**：F6 模块
新增输出 `proto_err_o = arm_err`（基线违例锁存 | 重复载荷锁存，电平直出）；
smi_top 逐源并入 lsu_err 向量（`err_x | proto_err_x`）⇒ 协议异常经 E3
通知 + E4 FAILED 锁存，不再「只能由 host/外部超时机制发现」。**不并入
ir_err**（通知走 lsu_err_evt 独立路径，无需重复触发；ir_err 维持旧组成）。
重新武装语义不变：合法新批 start_pulse 按 w_base_violation 重写 arm_err
可清除；FAILED 锁存期间 pop 闸挡死新 start_pulse，故锁存后 rst_n 前
proto_err_o 不撤。

**B7 激励构造修正（诚实登记）**：判据 v3 首跑用两 task 背靠背调用（接受
拍间距 +2），实测 IQ26 错误注册比批 A 完成**晚 1 拍**（doneA7=1/
intid7=2）——未达成裁定要求的「真同拍」。修正为融合任务
`credit24_26_aligned`（交错驱动，接受拍间距 +1）：done 注册 = 完成信用
接受后 m+2 拍（lcv+2 契约时序）、粘滞 err 注册 = 错误信用接受后 m+1 拍，
间距 +1 对齐且与绝对延迟 m 无关。**判据期望未动**（激励构造修正非判据
变更）；修正后 legacy/fixed_p1 对照复跑结论与首跑逐行一致。

### 14.2 缺口 #4b：crd>=cru 同拍边界——信用源因果链代码审计（无 RTL 改动）

裁定要求：「证明所支持真实单元不会在旧计数相等时，对本拍新发射立即返回
合法信用；不能仅靠注释」。审计结论（代码级，非形式证明）：

**因果链（逐跳行号钉死）**：每笔信用 = conv 对其**本地指令数组**中一条
指令的完成（STB_DONE/LDB_DONE）所触发；本地数组在 IQ 发射后经
`cru_stb_i` 路径填充——先有发射计数，后有该指令完成，再有信用。
- STB 链：stb_ur_axi_conv.v :775-779（完成产生）→ :803-862（`crd_stb_o`
  注册输出）；STBAXI 链：:1160-1223（`crd_stbaxi_o`，错误聚合
  :1211/:1215/:1219/:1223）。
- LDB 链：ldb_ur_axi_conv.v :106（LDB_DONE localparam）→ :1461-1465
  （完成产生）→ :1477-1536（`crd_ldb_o`）；LDBAXI 链：:996+
  （GEN_CRD_LDBAXI_O）。
- 链尾：aiacc_top_f.v :464-471 链式拼接，末端 `crd_stb_chain[NUM_SMC]
  = 6'd0`（:471 常数）——**无幻影注入点**；链节连接 :1858-1860 /
  :1994-2025 / :2117-2146。

**推论**：合法信用 k 可见 ⇒ 第 k 条指令发射已先于完成发生 ⇒ `cru≥k+1`
至少提前 2 拍成立 ⇒ `crd>=cru` 越界判据（错误检出）对合法信用**不会误报**。
**边界**：审计级结论；信用源微架构变更（conv/链结构改动）须重新审计。
DS（IQ92）链未审计（R12 保留，不在本判据服务范围——该判据只守 IQ24-27）。

### 14.3 附带登记：0x004[2] FAILED 软件可见接口扩展

- **语义**：0x004（INT 寄存器）[2] = FAILED 锁存，只读；置位 = 任一 LSU
  源错误新边沿（lsu_err_evt）；**唯一解锁 = rst_n**（cfg_rst 写 0x000[1]
  不清——E4/R10）；host 写 0x004 清 intid/int_err 不影响 [2]。
- **版本兼容性**：v0.4.19 §5.4.2.1 中 [6:2] 未分配/恒定 0——旧 host 读
  该位恒得 0，现可能得 1。忽略 [2] 的旧 host 功能不受影响（但本就无
  失败可见性——Q6 前 intid 自增不区分成败）；要利用失败可见性的 host
  须新增轮询 [2]（或 int_err [1]）。**PARAM_RTL_VERSION 未升**
  （0x0004_2200）——是否升位留用户裁定（隔离副本本就不上板，版本位
  语义影响仅限仿真环境内）。
- **同批新语义一并登记**：锁存期间 START 队列弹出被挡（排队项与新写
  0x020 均不发射，0x004[2] 可观测）；新写仍入队（满走既有
  start_q_full_evt 置 int_err）。

### 14.4 判据 v3 三模式验收（全部 AS-EXPECTED，exit 0）

| 用例 | legacy | fixed_p1 | fixed_p2（=p2b） |
|---|---|---|---|
| B1–B6 | 同 v2 矩阵（B2 PASS，余 VIOLATION）✓预期 | 同 v2 ✓预期 | 全 PASS |
| B7 T-ERRPRIO | VIOLATION ✓预期 | VIOLATION ✓预期 | **PASS** |
| B8 T-ERRMASK | VIOLATION ✓预期 | VIOLATION ✓预期 | **PASS** |
| B9 T-PROTOERR | SKIP ✓预期（无 force 目标） | SKIP ✓预期 | **PASS** |

fixed_p2 FACT 行（机制级证据，归档 sim.log 原文）：
- B7：`intid7=1 interr7=1 failed7=1 doneA7=0 iss_p7=0 doneC7=0 failed7b=1
  doneD7=1 eidD7=1`——真同拍注入（aligned head@4011/tail@4012）：done
  保持 0、通知恰一次记错误、锁存挡 C、cfg_rst 不解锁、rst_n 恢复。
- B8：`intid_a=1 intid_b=1 intid_c=2`——同源重复不重复计数（notify-once）、
  他源持高期间新错误仍通知（遮蔽关闭）。
- B9：`intid9=1 interr9=1 failed9=1 iss_p9=0 doneC9=0 failed9b=1 doneD9=1`
  ——proto_err 经 lsu_err 向量触发 E3+E4 全链；force 仅诊断注入。

### 14.5 TB-A 连续性 + 运行器硬化登记

f6a_unit fixed 7/7 AS-EXPECTED：C1–C6 与既往归档逐拍一致（本轮回合未触
dly_queen/inst_queen/F6 完成判定逻辑——p2→p2b 三文件diff 可证）；
**C7 扩展字段 `proto=218`**（重复载荷异常拍 proto_err_o 置位，≥0 判据
成立）——#4a「异常产生机制」一端的直接证据，与 B9 force 诊断端互为两端。

**运行器硬化（本轮发现即修）**：run_f6a_unit.sh 原不重建 work 目录且裁决
输出仅走 stdout——归档前发现 work_fixed/check.txt 系 2026-10-09 旧档滞留
（差一刻误归档为「连续性证据」）。已对齐 run_f6b.sh 纪律：每轮
`rm -rf work_<mode>` + 裁决 `| tee check.txt` + `exit ${PIPESTATUS[0]}`；
归档内 TB-A 三文件为硬化后重跑产物（exit 0）。

### 14.6 签收口径与遗留（不扩大）

**可签**（本轮新得）：复核裁定四缺口均有定向关闭证据——#2=B7 真同拍
抑制、#3=B8 逐源边沿 + 独立通知项、#4a=B9 + TB-A C7 proto 两端、
#4b=§14.2 行号钉死审计；0x004[2] 接口扩展已登记（§14.3）。
**留用户裁定**：是否将阶段二口径从「所列 TB 用例通过」恢复为「E1–E5
全闭合」（本代理不自行升级）。
**仍不支持**：「完整 LSU 完成契约已修复」「可替换原版上板」。
**遗留清单**：IQ92 保留旧行为（R12）；DS 链未审计；STORE 外部完成
对账（F6-C）范围外；host 完成协议与反压恢复仍开放（门③ errata ⑦
措辞裁定沿用）；信用源微架构变更须重审 §14.2；B9 的 force 注入不断言
协议异常可由信用通道物理产生。

## 15. 真实单元回归记录（2026-10-10，复核裁定步⑤；用户五项令 #3 验收）

裁定原文（2026-10-10 复核）：「全树沙箱可以同步准备……上述局部问题
关闭后，先跑 LDB L1，再 L2，最后跑 M3-lite 正向及必要负控。」
四缺口关闭（§14）后执行，全链在 p2b 全树沙箱
`u280_sim_v422_f6full_20261010`（原树只读；覆盖白名单恰 7 文件，
sha256 逐项恰等于 f6_phase2b_rtl BUILD_FINGERPRINT）。
归档：`verification_v2/artifacts/f6_realunit_regression_20261010/`
（29 项 sha256 已封）。

### 15.1 结果总表

| 项 | 结果 | 关键证据 |
|---|---|---|
| LDB L1（compiler_ldb_review_l1_f6p2b） | compare exit=0 全 PASS | **B5c：max写@181 ≺ INTR_ASSERT@186，gap=+5** |
| LDB L2（33 行尾突发） | compare exit=0 全 PASS | B5b 33/33；B5c：max写@270 ≺ INTR@275，gap=+5 |
| M3-lite 五腿 | ALL-LEGS-AS-EXPECTED | 逐腿与 stock 对照同值（下表） |
| 第六腿 us3bp（n=150+全屏障） | GREEN（red=0 warn=6） | 注入链与 stock 逐拍一致；完成时序修正显形 |

五腿对照 stock 归档 m3lite_gate3_respacct_20261008：positive GREEN
（red=0 warn=4）、negative NEGATIVE-CONTROL LIVE（red=6 同清单）、
bp fatal_expected respacct PASS（**@12275000ps 同拍**；信息性 red=22
同值）、to_wb PASS（@9495000ps 同拍）、to_s4 PASS（@12275000ps 同拍）。

### 15.2 与 stock RTL 的差异及机制归因（逐项，未预烤）

**① LDB B5c 反例关闭（红→绿）**：stock 门②反例「L1 通知≺写 31 拍」
（门②不记全绿的根因）在 p2b 同型用例下转为末次写严格先于通知
（L1/L2 gap 均 +5）。机制 = 阶段一 C-DRAIN 计数对账将 done 移至本批
真实信用齐（lcv+2）之后。TB-B B4/B6 信用注入结论在真实 LOAD 路径
得到验证。

**② us3bp 注入链一致 + STORE 完成时序修正显形**：inject=1172 <
risen=1192 < release=1321 ≤ drained=1321、delay=129 / stall=149、
C0 无 RESPACCT 误报、gap_stb=−7 / gap_stbaxi=−3——全部与 stock 同值。
唯一时序差：stage_2（STORE 批）INTR stock@1159 → p2b@1191（+32）、
done/S4/dump 1219 → 1257（+38）。机制：stock@1159 早于真实信用
CRD_STB@1184/CRD_STBAXI@1188 共 25–29 拍——**legacy 竞态完成在真实
STORE 路径上的实况**（与 TB-A C1 / TB-B B4 互证）；p2b@1191 晚于末笔
真信用 +3 拍（lcv+2 done + intr 一级注册），恰为契约时序。stage_0/1
INTR（264/954）两侧一致：LOAD/COMPUTE 批信用返回快、legacy 竞态
余量近似零，**竞态余量以 STORE 路径最大**（信用穿越 AXI 写+B 响应
全程），与 ① 同源。+32→+38 差含 TB 20 拍轮询相位分量。数据面零代价：
C6/C8 全过，S4 release/dump 严格晚于末写与末 B 响应（1257 > 1191）。

**③ 其余各腿 bit 一致**：见 15.1 对照；RESPACCT 16 拍防抖在合法 us3
反压下零误报。

### 15.3 限制落账

- bp 套件腿 sim.log 被 us3bp 复跑覆盖（同 case 目录复用，未先拷贝）。
  替代三足：fatal_expected 判据冻结（sha256 入 reports 清单）+ to_s4
  同 @12275000ps 互证 + stock 归档同时间戳。教训：复用 case 目录前
  先拷 per-leg sim.log（stock 当年即先拷后跑）。
- us3bp 的 S4-waited 语义不适用限制沿用（bresp tap 在 ds2 边界）。
- 本回归不含 gate2 完整件（裁定清单仅 LDB L1/L2 + M3-lite；L1 即门②
  B5c 反例同源用例）。
- 结论限 TB/模型层：真实单元 = 仿真 RTL 真实 LDB/STB 路径，非上板；
  「延迟→恢复→GREEN」为合法注入点 TB 实证，不外推硬件健壮性；
  STORE host 闭环保留既有验证屏障，不因本回归宣称已解决。

### 15.4 五项令 #3 签收口径

「信用注入测试结论得到真实 LOAD/STORE 路径验证」——**成立**（以
15.2①② 为据）：竞态抑制（通知移至真信用后）与恢复时序均在真实
路径复现且方向正确，负控/诊断通道与 stock 逐拍一致。E1–E5 是否恢复
全闭合口径仍留用户裁定（§14.6）；「可替换原版上板」不支持。
