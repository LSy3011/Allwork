# SMT 公理层正确性分析

> 版本：V1.0  日期：2026-04-29  
> 基于 `src/synthesis/axioms/` 模块与硬件 `rtovr_config.yaml` 交叉审计

---

## 1. 硬件物理模型（基准真相）

### 1.1 执行单元清单

| 单元类型 | 实例数 | Latency | 输入/输出结构 |
|----------|--------|---------|--------------|
| UR_READ  | 4      | 1       | 输出锁存 reg（无 FIFO） |
| UR_WRITE | 4      | 1       | 输出锁存 reg（无 FIFO） |
| FPADD    | 1      | 4       | 输出锁存 reg（无 FIFO） |
| FPMUL    | 1      | 4       | 输出锁存 reg（无 FIFO） |
| INTADD   | 1      | 1       | 输出锁存 reg（无 FIFO） |
| RTOVR    | 39     | 1       | Crossbar 纯路由，无计算 |

> **关键约束**：所有非 RTOVR 单元的输出锁存 **无 FIFO**，同一单元下一次发射时直接覆盖。

### 1.2 Bank 冲突规则

- 共 8 个 Bank，UR_READ / UR_WRITE 可访问任意 Bank（地址决定归属）
- 同一 cycle，同一 Bank：
  - 同读（R-R）→ **非法**
  - 同写（W-W）→ **非法**
  - 读写（R-W）→ **非法**（注：此处依实际硬件确认）
- Bank 分配在 SMT 阶段由 `metadata["manual_bank"]` 静态标注

### 1.3 调度执行模型

- 所有 IQ **同时并行发射**：在任意 cycle `c`，凡调度时间满足 `t_op mod II == c mod II` 的操作均同时执行
- 无全局 stall，无动态气泡，纯静态调度

---

## 2. RTOVR Crossbar 精确模型

### 2.1 拓扑定义（来自 `hw_config/rtovr_config.yaml`）

```
producers_dvr（DVR 编号 = RTOVR 的物理身份）：
  DVR  0 → INTADD.S0     DVR  1 → INTADD.S1    DVR  2 → INTADD.S2
  DVR  3 → FPMUL.S0      DVR  4 → FPMUL.S1
  DVR  5 → FPADD.S0      DVR  6 → FPADD.S1
  DVR  7 → FPTOFP.S      DVR  8 → INTTOINT.S   DVR  9 → FPTOINT.S
  DVR 10 → INTTOFP.S     DVR 11 → LOGIC.S0     DVR 12 → LOGIC.S1
  DVR 13 → LOGIC.ST      ...
  DVR 35 → UR_WRITE_0.D  DVR 36 → UR_WRITE_1.D
  DVR 37 → UR_WRITE_2.D  DVR 38 → UR_WRITE_3.D

consumers_dr（SEL 编号 = 采样源）：
  SEL  0 = INTADD_D       SEL  3 = FPMUL_D
  SEL  4 = FPADD_D        SEL  5 = FPADD_ST
  SEL 10 = LOGIC_D        SEL 29 = SHIFTUP_D
  SEL 30 = UR_READ_0_D   SEL 31 = UR_READ_1_D
  SEL 32 = UR_READ_2_D   SEL 33 = UR_READ_3_D
```

### 2.2 RTOVR 指令语义

一条 RTOVR 指令 = `(DVR 编号, SEL 编号)`：

- **DVR 编号**：RTOVR 单元的物理身份，hardwired 到特定目标输入端口，**静态确定，非求解变量**
- **SEL 编号**：选择采样哪个生产者的输出 latch，**由 DFG 数据流边静态确定**

### 2.3 完整时序链

```
cycle  t_prod              : 生产者单元发射
cycle  t_prod + lat_prod   : 生产者输出 latch 就绪（RTOVR 最早可采样）
cycle  t_rtovr             : RTOVR 发射，采样 SEL 指向的输出 latch
                             约束：t_prod + lat_prod <= t_rtovr < t_prod + II
cycle  t_rtovr + 1         : 数据写入 DVR 指向的消费者输入 latch
cycle  t_consumer          : 消费者单元发射，读取输入 latch
                             约束：t_consumer == t_rtovr + 1（精确，输入 latch 无保持）
```

**总端到端延迟（最小）**：`lat_prod + 1 (RTOVR) + 0 (latch ready) = lat_prod + 1` 拍后消费者可发射

### 2.4 RTOVR 资源约束

- 39 个 DVR slot 互相独立，**支持全部并发**（同一 cycle 内所有 DVR 均可同时活跃）
- 同一 DVR slot 在同一 `t mod II` 槽内只能发射一次（模冲突约束，但每 DVR 对应唯一 RTOVR op，通常自动满足）

---

## 3. 现有公理层审计

### 3.1 公理模块索引

| 模块 | 文件 | 主要公理 |
|------|------|---------|
| DependencyAxioms | `dependency_axioms.py` | RAW latency 约束 |
| ResourceAxioms   | `resource_axioms.py`  | 模冲突、Bank 冲突、Spill 互斥 |
| PhysicalAxioms   | `physical_axioms.py`  | RTOVR 对齐、影子绑定、反覆盖、锁存保护 |
| SpecialAxioms    | `special_axioms.py`   | 占位（未实现） |

---

## 4. 问题清单

### 🔴 P1 — `add_anti_overwrite_axiom`：覆盖窗口上界计算错误

**位置**：`physical_axioms.py:74-84`

**现有约束（错误）**：
```python
data_ready = t_vars[op.trace_id] + lat - 1
# 对每个消费者 cons：
t_vars[cons.trace_id] - data_ready <= II
# 展开：t_cons <= t_prod + lat - 1 + II
#      即：t_rtovr 最晚在 t_prod + lat + II - 1 发射
```

**物理真相**：
- 生产者在 `t_prod + II` 再次发射时，**输出 latch 被覆盖**
- RTOVR 必须在覆盖前采样：`t_rtovr < t_prod + II`

**正确约束**：
```python
# RTOVR（消费者）必须严格在生产者下一拍发射前完成采样
t_vars[rtovr.trace_id] < t_vars[producer.trace_id] + II
# 等价（整数）：
t_vars[rtovr.trace_id] <= t_vars[producer.trace_id] + II - 1
```

**影响**：当前约束多放宽了 `lat - 1` 个周期（FPMUL lat=3 → 多 2 拍），允许本应非法的调度通过 SMT 验证，最终在硬件仿真时出现数据错误。

---

### 🔴 P2 — `add_physical_binding_axiom`：DVR 以 Implies 链建模，性能差且语义错

**位置**：`physical_axioms.py:115-156`

**现有逻辑**：
```python
# 用 Implies 做条件绑定：若消费者 op 分配到 iq_id，则 RTOVR 绑到对应 DVR slot
z3.Implies(u_vars[op.trace_id] == iq_id,
           u_vars[producer.trace_id] == base_rt + target_slot)
```

**问题**：
1. DVR 编号是静态的（由硬件 YAML 确定），RTOVR 的 `u_vars` 不应是自由变量
2. Implies 链让求解器把 DVR 当成候选搜索，大幅扩大搜索空间
3. 若消费者未分配到任何已知 iq_id，RTOVR 的 `u_vars` 约束为空，变成完全自由变量

**正确约束**：
```python
# RTOVR 的 DVR 编号在 DFG 构造时就已确定，直接硬约束
dvr_number = op.metadata["dvr"]  # 从 DFG node metadata 读取
solver.add(u_vars[rtovr.trace_id] == dvr_number)
```

---

### 🔴 P3 — SEL 有效性约束完全缺失

**位置**：`physical_axioms.py`（无对应约束）

**缺失的约束**：

```
RTOVR 的 SEL 字段指定从哪个生产者输出 latch 采样。
需要保证：
  1. SEL 对应的生产者在 t_rtovr 时刻输出 latch 已就绪
     → t_rtovr >= t_prod + lat_prod
  2. SEL 对应的生产者在 t_rtovr 时刻输出 latch 尚未被覆盖
     → t_rtovr < t_prod + II   （P1 的正确版本）
```

当前 `add_rtovr_alignment_axiom` 用等式 `t_rtovr == t_prod + lat` 覆盖了约束 1（最严格情况），但：
- 在允许弹性时序（`mode != "strict"`）时，等式被放松，下界约束消失
- 约束 2 完全依赖 P1 的错误公理，存在漏洞

---

### 🟡 P4 — `add_iq_alignment_axiom`：对 RTOVR 施加无意义的相位约束

**位置**：`physical_axioms.py:297-315`

**问题**：
- 该公理对所有共享同一 IQ（u_vars 相同）的操作强制 `phase_i == phase_j`
- RTOVR 的 DVR 编号是唯一的，不存在两个 op 共享同一 DVR slot 的情况
- 即使在 RTOVR 上施加此约束，条件 `u_vars[i] == u_vars[j]` 永远为 False（两个不同 RTOVR 的 DVR 不同），约束是 trivially true 的无效约束，浪费求解器资源

**修复**：
```python
# 在约束生成前过滤 RTOVR
if op_i.op_type == "RTOVR" or op_j.op_type == "RTOVR":
    continue
```

---

### 🟡 P5 — `add_rtovr_crossbar_axiom`：仅取第一消费者，槽位互斥检测不完整

**位置**：`physical_axioms.py:185-187`

**现有代码**：
```python
t_fire_i = t_vars[cons_i[0].trace_id]  # ⚠️ 仅取第一个消费者
t_fire_j = t_vars[cons_j[0].trace_id]
```

**问题**：若一个 RTOVR 的输出被多个下游单元消费（广播场景），仅用第一个消费者的时间计算生命周期，其余消费者的有效窗口被忽略，可能允许槽位在某消费者还未读取时已被覆盖。

**正确逻辑**：取所有消费者中最晚的发射时间作为 `t_fire`：
```python
t_fire_i = z3.Maximum([t_vars[c.trace_id] for c in cons_i])
```

---

### 🟡 P6 — `add_modulo_constraint`：RTOVR 资源分组粒度太粗

**位置**：`resource_axioms.py:37-64`

**现有分组**：按 `op_type == "RTOVR"` 聚合为一组，所有 RTOVR ops 共享一个模冲突池。

**物理真相**：DVR 0-38 是 39 个完全独立的物理单元，互相之间**无任何资源竞争**。

**影响**：
- 模冲突池把不同 DVR slot 的 RTOVR 视为竞争关系
- `PbLe` 约束要求每个模槽至多 1 个 RTOVR 操作
- 实际硬件允许 39 个 RTOVR 在同一 cycle 全部并发
- 导致 II 被虚假抬高（求解器认为 RTOVR 有资源约束，实际没有）

**正确分组**：每个 RTOVR op 的资源 = 其 DVR 编号，单独成组，无跨组冲突。

---

### 🟡 P7 — `add_side_effect_phase_axiom` 与 `add_iq_alignment_axiom` 冗余

**位置**：`physical_axioms.py:29-57` 和 `297-315`

两个公理均对 UR_READ / UR_WRITE 施加 `phase_i == phase_j`（当共享 IQ 时）。  
`add_iq_alignment_axiom` 是全集，`add_side_effect_phase_axiom` 是子集（仅限副作用单元），后者被前者完全包含。

**建议**：移除 `add_side_effect_phase_axiom`，或仅在不调用 `add_iq_alignment_axiom` 时启用，避免重复约束拖慢求解速度。

---

## 5. 公理正确性总结矩阵

| 公理 | 完备性 | 可靠性 | 性能 | 优先级 |
|------|--------|--------|------|--------|
| `add_latency_axiom`            | ✅ | ✅ | ✅ | — |
| `add_modulo_constraint`        | ✅ | ✅ | 🟡 RTOVR 分组错 | P6 |
| `add_bank_conflict_axiom`      | ✅ | ✅ | ✅ | — |
| `add_rtovr_alignment_axiom`    | ✅ | ✅ | ✅ | — |
| `add_anti_overwrite_axiom`     | ✅ | 🔴 窗口 +lat-1 | ✅ | **P1** |
| `add_shadow_binding_axiom`     | ✅ | ✅ | ✅ | — |
| `add_physical_binding_axiom`   | 🔴 SEL 缺失 | 🔴 DVR 非静态 | 🔴 Implies 链 | **P2/P3** |
| `add_rtovr_crossbar_axiom`     | 🟡 仅取 cons[0] | 🟡 | ✅ | P5 |
| `add_iq_latch_protect_axiom`   | ✅ | ✅ | 🟡 O(N²) | — |
| `add_alu_liveness_axiom`       | ✅ | ✅ | 🟡 O(N²) | — |
| `add_iq_alignment_axiom`       | ✅ | 🟡 含 RTOVR | 🟡 冗余 | P4/P7 |
| `add_side_effect_phase_axiom`  | ✅ | ✅ | 🟡 冗余 | P7 |
| `add_symmetry_breaking_axiom`  | ✅ | ✅ | ✅ | — |

---

## 6. 修复优先级路线图

```
阶段 1（正确性修复，影响调度合法性）：
  P1 — 修正 anti_overwrite 窗口上界
  P2 — 将 RTOVR DVR 改为静态硬约束
  P3 — 补充 SEL 采样窗口下界约束

阶段 2（性能优化，影响求解速度和 II 可达性）：
  P6 — RTOVR 按 DVR slot 独立建模（解除虚假资源竞争）
  P4 — IQ alignment 过滤 RTOVR
  P7 — 去除冗余的 side_effect_phase 公理

阶段 3（鲁棒性改进）：
  P5 — crossbar 槽位互斥取 max(cons) 而非 cons[0]
```

---

*Last Updated: 2026-04-29*  
*审计人：Antigravity × AIACC Compiler Team*
