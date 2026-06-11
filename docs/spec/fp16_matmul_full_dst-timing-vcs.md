# fp16_matmul_full_dst 时序执行预期（VCS 对照参考）

> **用途**：VCS 测试 `fp16_matmul_full_dst` 时对照执行时序与数据流，明确**哪些可逐拍对齐 RTL、哪些是 model-A 不要对**。
> **来源**：实际编译/仿真产物 `build/verification_v2/fp16_matmul_full_dst/`（`compiler_schedule.json` II=10 + `sim_config.json` + sim log 端点）。
> **算子**：FP16, **M=8 行流式** × K=8 收缩维, 8-SMC N 维广播 + SMI_DS_T 跨-SMC 转置前端。
> **演进线定位**：ADR-033(gather-gate) → ADR-034(指令驱动) → **ADR-036 S2(流式 stepped 转置核，full_dst 站这里)**。
> **忠实度边界一句话**：数值终值 + 数据流空间结构 + 依赖序 + compute 段相对 latency = **可对 RTL**；SMI_DS_T 级 latency / SHIFTDN 逐拍 drain / COND 协调 / 绝对拍 = **model-A，VCS 校准前勿逐拍对**。

---

## 0. 跑法 + 期望结果

```bash
python3 verification_v2/main_verify.py --op fp16_matmul_full_dst --mode e2e
# 期望: PASSED y: All 8 SMC outputs bit-perfect (atol=0.005), exit=0
# 完整逐拍 sim log: AIACC_SIM_DEBUG=1 前缀（默认只打有效指令+数据）
```

数值语义：`Q[s][m,c] = Σ_{k=0..7} X[m,k] · W_c[k,s]`（FP16 乘 → FP32 8→1 加法树 reduce，无 cumulative）。
- `X` = M=8 行 × K=8 激活矩阵；`W_c` = SMC_s 本地第 c 列权重（c=0..3）。
- 输出：每 SMC_s 持 `Q[s]` = M=8 行 × COLS=4 列 = 32 个 fp32（readback word m·4+c）。

---

## 1. 关键参数

| 项 | 值 | 说明 |
|---|---|---|
| M / K / COLS | 8 / 8 / 4 | 8 激活行 × 8 收缩维 × 4 输出列；FP16 → 1 行 = **1 个 128b 词**（8×16b） |
| SMI_DS_T cfg | `gather_smc_cnt=8, lanes=8, src_bank=6, dst_bank=0, element_bits=16, word_lanes=8` | gather 8 SMC × 8 lane → 转置 8×8 → 写 SMC_0 bank0 的 M=8 词 |
| broadcast_capture | `{port:0, bank:0}` | DEBT-032 修法（ADR-037）：广播源 ur-r0 读 bank0 时快照、防同端口异 bank clobber |
| bank 布局 | a=bank0(转置落点/广播源) · b0-3=bank1-4(权重,partitioned) · y=bank5(输出,partitioned) · **SRC_BANK=6**(列对齐激活源) | |
| II / max_cycles | **10** / 231 | II=10 是保守解（见 §6），硬下界 4 |

---

## 2. 空间数据流（5 段 datapath，文档 para970：UR read — SHIFTDN — SMI_DS_T — SHIFTUP — UR write）

```
① 列对齐激活 X[8,8] @ bank6（全 8 SMC 各持 1 列）
   SMC_s bank6@0 = [X[0,s] X[1,s] … X[7,s]]   （8 fp16 = 1 词）
        │
        ▼  ② SHIFTDN gather（8 列经下行链汇聚 → SMI 中心）
   SMI_DS_T 8×8 Tile
        │
        ▼  ③ SMI_DS_T transpose（列对齐 → 行对齐，out[m]=tile[*][m]=X[m,:]）
   8 行各 1 词
        │
        ▼  ④ SHIFTUP scatter（DATA_OUT 逐词 + DATA_OUT_VLD → 写 SMC_0 bank0@0..7）
   SMC_0 bank0@m = X[m,:]
        │
        ▼  ⑤ a_read(ur-r0) 逐行读 bank0@m + SHIFTUP 广播 fan-out → 全 8 SMC
   每 SMC 拿到行 X[m,:]（广播）
        │
   ⑥ FP16MUL（X[m,:] ⊙ 本地权重 W_c[:,s]）→ FP32ADDTREE8TO1（8→1 reduce）
        │
        ▼  ⑦ UR write(ur-w0) → SMC_s bank5@(m·4+c) = Q[s][m,c]（per-SMC partitioned）
```

> ⚠ ①②③④ 由 **SMI_DS_T 指令（`iq_smi-ds-t-0.hex`，表598 微指令 vld/intlv_*）VLD 触发**（ADR-034 2b 指令驱动），
> 数值核走 **ADR-036 S2 流式 stepped 单元**（`SmiDsTUnit.evaluate/tick`，固定 latency pipeline、逐行 DATA_OUT；
> **非** `apply_smi_ds_t` gather-gate backdoor）。
> 端点归属（hco 订正）：bank6 读 = **SMC_s 的 UR-read**、bank0 写 = **SMC_0 的 UR-write**（**非** SMI_DS_T；表596 SMI_DS_T 无 UR 端口）。

---

## 3. 实际执行时间线（sim log 逐拍，**跨 cold-build 稳定**）

> 3 次 cold-build 实测执行拍一致（gather=41/scatter=57-64/a_read=62-146/write=96-166）；逻辑 t 即便 Z3 多解漂移，INIT staggering 也归一到稳定执行拍。
> ⚠ **这些绝对 cycle 是 model-A sim 的，建立在 SMI_DS_T latency=16 等假设上；RTL 若 latency≠16 则绝对拍不同**（见 §5）。

| Cyc | 事件 | 端点 | 数据（解码示例） |
|---|---|---|---|
| **41** | ① SMI_DS_T gather | UR-READ `DST-GATHER` bank6@0（×SMC_0..7）| SMC_s = X[:,s]，如 SMC_0=`[3.55,3.17,4.45,3.46,1.54,3.29,1.34,2.80]` |
| 41→57 | ③ transpose | （SMI_DS_T latency=16；41+16=57）| — |
| **57-64** | ④ scatter（逐行 1 词/拍）| UR-WRITE `DST-SCATTER` bank0@0..7（SMC_0）| @0=X[0,:]=`[3.55,2.08,1.16,1.07,4.25,4.65,3.43,3.92]` … @7=X[7,:] |
| **62-146** | ⑤ a_read + 权重读（流式 M=8 行）| UR-READ `READ` bank0(激活)/bank1-4(权重)| @bank0 读到 ④ 写的 X[m,:] |
| **77-165** | ⑥ RTOVR 选通 → FP16MUL → ADDTREE | RTOVR `ROUTE` | — |
| **96-166** | ⑦ 输出写回（逐行）| UR-WRITE `WRITE` bank5@(m·4+c)（SMC_s）| SMC_0 行0 = `[70.21,68.00,76.42,77.99]` |

整体：Cyc41 首 gather → Cyc166 末输出 → 排空到 max_cycles=231。

---

## 4. compute 段单元 latency（**真硬件值，可对 RTL**）

| 单元 | latency | 出处 | 排程体现 |
|---|---|---|---|
| UR_READ / UR_WRITE | 1 | `hw_config/pipeline_delays.yaml` | a_read → RTOVR +1 |
| RTOVR | 1 | 同 | RTOVR → FP16MUL +1 |
| SHIFTUP / SHIFTDN | 1 / hop | 同 | 广播 +1 |
| **FP16MUL** | **4** | **ADR-032**（user 2026-06-04 裁定，原 2 改 4）| FP16MUL 输入 → 输出 +4 |
| **FP32ADDTREE8TO1** | **3** | `pipeline_delays.yaml` | **tree-to-tree 链每 +3**（y_0→y_1→y_2→y_3） |

依赖序（**VCS 必满足**）：`gather < transpose < scatter < a_read < shiftup_broadcast < FP16MUL < ADDTREE < write` —— 严格有序，可逐项对 RTL。

---

## 5. ⚠ VCS 对齐：契约 vs model-A 近似（**对哪些、不要对哪些**）

| 维度 | 当前 sim (model-A) | VCS / RTL 真相 | 对齐建议 |
|---|---|---|---|
| **数据流空间结构**（§2 五段）| 列对齐→gather→转置→scatter→广播→compute | 同 | ✅ **逐项对**（结构契约）|
| **端点值**（gather/scatter/output 数据）| sim log `DST-GATHER`/`DST-SCATTER`/`WRITE` 有值 | SMC_s UR-read / SMC_0 UR-write 端点 | ✅ **diff 值 + SMC_ID + bank/addr** |
| **依赖序** | gather<transpose<scatter<read<mul<tree<write | 同 | ✅ **对序** |
| **compute 段相对 latency**（FP16MUL=4 / ADDTREE=3 链 / RTOVR/UR=1）| §4 真值 | 同（pipeline_delays / ADR-032）| ✅ **对相对拍 gap** |
| **SMI_DS_T 级 latency=16** | `= PARAM_SMC_NUM×2`（para1194/1237 的「**广播读延迟**」量，被 ADR-036 **Q2 假设借作**「转置 latency」）| 待 VCS 实测 | ❌ **不要对这 16 拍绝对值**；VCS 校准后回填 |
| **SHIFTDN 逐拍 drain** | gather 当 1 拍原子（unit lat=1），8-SMC drain **欠建模**，其延迟被借进上面的 16 | SHIFTDN 链逐拍 drain（~8 拍）+ interleave 2 拍 stagger（para961）| ✅ diff 端点值；❌ **SHIFTDN 事件数/逐拍时序** |
| **scatter 速率** | 1 词/拍逐行（Cyc57-64 线速）| SHIFTUP DATA_OUT + SMC_0 UR-write | ✅ diff 值/addr；❌ 绝对拍 |
| **COND 数据就绪同步** | **无**（ADR-036 Q3：user 裁定用固定-latency 静态排程替代；链全固定延迟 → 编译期排 `t≥t[p]+lat`）| RTL 即便用 COND，固定延迟 → 拍数应一致 | ⚠ **缺口实质 = latency 16 校准对不对**（不是缺 COND 机制）|
| **绝对 cycle** | 跨 cold-build 稳定（41/57/62/96…），但建立在 latency=16 假设 | RTL 绝对拍由真 latency 决定 | ❌ **latency≠16 则全平移**；先对相对结构 |
| **smc_id_en** | sim 全盲（8/8 是 sim-vs-golden 自洽）| 真 8-SMC 各分支 | ❌ **VCS 必验 SMC_1..7 各算对** |
| **转置粒度** | element（fp16 lane）| **byte**（para1037）| fp16 每元素 2byte 不跨 lane → 等价（ADR-035 BLOCK-1 不变式，**非** q_dst 文档表）|
| **bank0 同拍 R+W** | scatter(1词/拍) 与 a_read 可能同拍 R+W bank0（实测 Cyc62 同 Port:0 同 bank 异 addr 既读又写）| COND 同步无同拍 R+W | ⚠ **此同拍 R+W 是 model-A scatter/read 同速率的人为产物——单端口 SRAM 物理一拍做不到、RTL 不会出现 → 不是契约、不可对**；diff harness 须豁免、勿误判 ADR-008 |

**一句话**：和 VCS 对齐 **§2/§3 空间数据流 + 端点值 + §4 依赖序与 compute 相对 latency**；**不要**逐拍对齐 SMI_DS_T latency=16 / SHIFTDN drain / scatter 绝对拍 / COND —— 那些是 model-A 占位，VCS 才是真相（ADR-036 验收②）。

---

## 6. II=10 说明（**不是性能 bug，别误读**）

- matmul_q_dst 的 **II=1** 是 M=4/K=4 FP32、**每行独立无累加** 的场景，**不可与本算子比**。
- fp16_matmul_full_dst 有 **cumulative 跨词累加**（loop_carried 自环边）→ **rec-MII 下界约 4**（FP16MUL/ADDTREE pipeline 深度推断，**未经 SMT 单独证**；代码实证去链化 II 4→6 即破，累加被打断则错）。
- **II=10 是 `matmul_op.py:1176-1181` 记录的工程决策**：4 条 ADDTREE8TO1 tree **链化**（每边 +addtree_lat=3，非流水重叠）的保守解，**为规避 DEBT-032-RESIDENT-TIMING（sim 无法复现 SMT 柔性 latch 拍）而选**——是"sim 忠实度倒逼的保守 II"，非 Z3 次优。降至硬下界 4 须先闭合 DEBT（VCS + sim 流水建模）。

---

## 7. 固件文件（VCS 加载，`build/verification_v2/fp16_matmul_full_dst/`）

- 全局指令流（8-SMC lockstep 共享）：`iq_ur-r0.hex`(a_read+ds_src_read) · `iq_ur-r1/r2/r3.hex`(权重读) · `iq_shiftdn.hex`(ds_gather) · **`iq_smi-ds-t-0.hex`**(SMI_DS_T 转置，表598) · `iq_shiftup.hex`(广播) · `iq_fp16mul.hex` · `iq_fp32addtree8to1.hex` · `iq_rtovr-{19..23,32,33,35}.hex` · `iq_ur-w0.hex`
- 每-SMC 数据：`smc_0..7/ur_data`（列对齐激活在 **bank6**、权重 bank1-4；**bank0 不注入**，转置写回）
- 加载映射 + cfg：`sim_config.json`（`iq_init` + `smi_ds_t` + `broadcast_capture` + `per_smc_ur_init`）
- golden 比对：`golden_data.npz`（`y` shape (8,32)）+ `smc_k/output_bank/`（bank5）

---

## 8. 升 Accepted 的 VCS 门（ADR-036/037）

VCS 实测须回填/校准：
1. **SMI_DS_T latency 真值**（替 Q2 假设的 16）；SHIFTDN drain 逐拍事件序 + interleave stagger（DEBT-S4-SHIFTDN-DRAIN）。
2. **绝对拍** = 真 latency 决定（本文 41/57/62/96 是 model-A 占位）。
3. **SMC_1..7 各分支算对**（sim 对 smc_id_en 全盲）。
4. **COND/INST_DONE 握手**（若 RTL 用，确认固定延迟下拍数与静态排程一致）。
5. **绝对拍忠实度**（ADR-037 升 Accepted 门 = SMI→CRU_SHIFTUP 流式注入时序表 + VCS）。
