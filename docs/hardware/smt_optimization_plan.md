# SMT 公理层优化变更计划

> 版本：V1.0  日期：2026-04-29  
> 前提：**当前公理语义正确**，目标是在等价条件下压缩变量数量和约束规模。

---

## 1. 核心优化：RTOVR 节点 → DFG Edge 抽象

### 1.1 理论依据

RTOVR 的所有参数在 DFG 构造时已静态确定：
- DVR 编号 = 物理身份（hardwired 到目标输入端口）
- SEL 编号 = 采样源（由数据流边确定）
- Latency = 1（常量）

因此 `t_vars[rtovr]` 不是自由变量，严格满足：

```
t_rtovr = t_consumer - 1
```

可以从 SMT 中消除，替换为消费者的增广时序约束。

### 1.2 等价变换

**当前（RTOVR 作为节点）：**
```
producer ──lat_p──► RTOVR ──1──► consumer

约束：
  t_rtovr == t_prod + lat_p          (add_rtovr_alignment_axiom, 严格模式)
  t_consumer == t_rtovr + 1          (add_shadow_binding_axiom)
  t_rtovr <= t_prod + II - 1         (add_anti_overwrite_axiom, 正确版)
```

**优化后（RTOVR 作为增广边）：**
```
producer ──────────────────────► consumer

约束：
  下界：t_consumer >= t_prod + lat_p + 1   (合并 latency + RTOVR relay)
  上界：t_consumer <= t_prod + II          (防止生产者 latch 被覆盖前采样)
```

### 1.3 规模收益

| 指标 | 当前 | 优化后 | 收益 |
|------|------|--------|------|
| SMT 整数变量 | K+N（含N个RTOVR） | K（仅计算节点） | -N 个变量 |
| RTOVR 相关公理 | 5个公理函数 | 2条不等式/边 | 约束数 ~60% |
| RTOVR 资源约束 | PbLe模冲突池 | **消除** | DVR由消费者隐含 |

---

## 2. 次级优化：冗余公理清理

### 2.1 `add_iq_alignment_axiom` 过滤 RTOVR

RTOVR 的 DVR 编号唯一，不存在两个 op 共享同一 DVR 的情况，相位对齐约束对 RTOVR 是 trivially-true 的无效约束。

```python
# physical_axioms.py: add_iq_alignment_axiom
for i in range(len(ops)):
    for j in range(i + 1, len(ops)):
        op_i, op_j = ops[i], ops[j]
        # [OPT] RTOVR DVR slot 唯一，无需相位约束
        if op_i.op_type == "RTOVR" or op_j.op_type == "RTOVR":
            continue
        ...
```

### 2.2 移除 `add_side_effect_phase_axiom`

该公理对 UR_READ/UR_WRITE 强制 `phase_i == phase_j`（共享 IQ 时），与 `add_iq_alignment_axiom` 完全重叠，后者是前者的超集。

**操作**：从 `ucode_solver.py` 调用链中移除 `add_side_effect_phase_axiom`。

### 2.3 RTOVR 资源分组细化

当前 `add_modulo_constraint` 将所有 RTOVR 归为同一资源池，施加跨 DVR slot 的模冲突约束。实际上 39 个 DVR slot 完全独立并发，无任何资源竞争。

优化后：RTOVR 节点消除后，该问题自动解决。若保留 RTOVR 节点，则需按 DVR 编号独立分组。

---

## 3. 实现方案

### 3.1 DFG 层：标记 RTOVR 边

在 DFG 构造时，为经过 RTOVR 的边添加 `via_rtovr=True` 标记：

```python
# ir/mir/mir_op.py 或 DFG 构造处
# 当一条边 prod → consumer 经过 RTOVR 时：
consumer.metadata["rtovr_deps"] = {
    prod_id: {
        "dvr": dvr_number,   # 来自 rtovr_config.yaml producers_dvr
        "sel": sel_number,   # 来自 rtovr_config.yaml consumers_dr
    }
}
```

### 3.2 dependency_axioms.py：增广延迟公理

```python
@staticmethod
def add_latency_axiom(solver, ops, t_vars, II, spec, **kwargs):
    """
    RAW 依赖公理（含 RTOVR 增广延迟）。
    对于经过 RTOVR 中继的边，自动附加 +1 relay latency 和反覆盖上界。
    """
    for op in ops:
        # 普通 RAW 依赖
        for dep_id in op.metadata.get("depends_on", []):
            producer = next((o for o in ops if o.trace_id == dep_id), None)
            if not producer:
                continue
            lat = spec.delays.get(producer.op_type.upper(), 1)
            label = f"lat_{op.trace_id}_{dep_id}"
            DependencyAxioms._add_constraint(
                solver, t_vars[op.trace_id] >= t_vars[dep_id] + lat, label
            )

        # RTOVR 增广边约束（替代 5 个物理公理）
        for prod_id, rtovr_info in op.metadata.get("rtovr_deps", {}).items():
            producer = next((o for o in ops if o.trace_id == prod_id), None)
            if not producer:
                continue
            lat_p = spec.delays.get(producer.op_type.upper(), 1)
            
            # 下界：RAW + RTOVR relay (lat_p + 1)
            DependencyAxioms._add_constraint(
                solver,
                t_vars[op.trace_id] >= t_vars[prod_id] + lat_p + 1,
                f"rtovr_lower_{op.trace_id}_{prod_id}"
            )
            # 上界：生产者 latch 在下一次发射前必须采样完成
            # t_consumer - 1 < t_prod + II  →  t_consumer <= t_prod + II
            DependencyAxioms._add_constraint(
                solver,
                t_vars[op.trace_id] <= t_vars[prod_id] + II,
                f"rtovr_upper_{op.trace_id}_{prod_id}"
            )
```

### 3.3 ucode_solver.py：移除 RTOVR 节点，简化公理调用

```python
def _solve_for_ii_with_units(self, ops, II):
    # 过滤掉 RTOVR 节点（已抽象为边）
    compute_ops = [op for op in ops if op.op_type.upper() != "RTOVR"]
    
    solver = z3.Solver()
    t_vars = {op.trace_id: z3.Int(f"t_{op.trace_id}") for op in compute_ops}
    u_vars = {op.trace_id: z3.Int(f"u_{op.trace_id}") for op in compute_ops}

    for op in compute_ops:
        solver.add(t_vars[op.trace_id] >= 0)
        u_type = self._get_resource_type(op.op_type)
        valid_iqs = self.res_to_iqs.get(u_type, [0])
        solver.add(z3.Or([u_vars[op.trace_id] == iq_id for iq_id in valid_iqs]))

    # 简化后的公理调用（移除所有 RTOVR 专属公理）
    axioms.add_latency_axiom(solver, compute_ops, t_vars, II, self.hw_spec)  # 含 rtovr_deps
    axioms.add_resource_constraint(solver, compute_ops, t_vars, II, self.hw_spec, u_vars=u_vars)
    axioms.add_bank_conflict_axiom(solver, compute_ops, t_vars, II, self.hw_spec)
    axioms.add_iq_alignment_axiom(solver, compute_ops, t_vars, II, self.hw_spec, u_vars=u_vars)
    axioms.add_symmetry_breaking_axiom(solver, compute_ops, t_vars, II, self.hw_spec, u_vars=u_vars)
    axioms.add_alu_liveness_axiom(solver, compute_ops, t_vars, II, self.hw_spec, u_vars=u_vars)
    axioms.add_iq_latch_protect_axiom(solver, compute_ops, t_vars, II, self.hw_spec, u_vars=u_vars)

    # 移除的公理（已被增广边替代或冗余）：
    # ❌ add_rtovr_alignment_axiom   → 合并进 add_latency_axiom 下界
    # ❌ add_anti_overwrite_axiom    → 合并进 add_latency_axiom 上界
    # ❌ add_shadow_binding_axiom    → 合并进 add_latency_axiom 下界
    # ❌ add_physical_binding_axiom  → DVR 静态，无需 SMT 约束
    # ❌ add_rtovr_crossbar_axiom    → DVR 消费者唯一，无需槽位互斥
    # ❌ add_side_effect_phase_axiom → 被 add_iq_alignment_axiom 完全包含
```

---

## 4. 验证策略（反向验证为最终标准）

### 4.1 验证层次

```
Layer 3: 物理仿真 (sim/smc_simulator)
    ↑ 最终真相，bit-level 正确性
Layer 2: validate_physical_schedule (ucode_solver.py)
    ↑ 物理审计，检查时序/资源冲突
Layer 1: SMT SAT (z3 solver)
    ↑ 充分条件，约束过松时允许假正例
```

**关键原则**：SMT 是充分条件，物理验证是必要条件。优化后的 SMT 需要使 Layer 1 的解空间 ⊆ Layer 2 通过的空间。

### 4.2 `validate_physical_schedule` 更新

优化后 RTOVR 不再有 `t_vars`，验证器需从消费者的时间反推 RTOVR 采样时刻：

```python
def validate_physical_schedule(self, ops, schedule, unit_map, II):
    nodes_by_id = {op.trace_id: op for op in ops}
    
    # 对每个计算节点，检查其 rtovr_deps 的时序
    for op in ops:
        if op.op_type.upper() == "RTOVR":
            continue  # RTOVR 已抽象，跳过
        
        t_consumer = schedule[op.trace_id]
        
        for prod_id, rtovr_info in op.metadata.get("rtovr_deps", {}).items():
            prod = nodes_by_id.get(prod_id)
            if not prod:
                continue
            
            lat_p = self.hw_spec.delays.get(prod.op_type.upper(), 1)
            t_prod = schedule[prod_id]
            t_rtovr_inferred = t_consumer - 1  # RTOVR 在消费者前 1 拍发射
            
            # 检查 1：RTOVR 采样时生产者 latch 已就绪
            if t_rtovr_inferred < t_prod + lat_p:
                print(f"[VERIFIER-FAIL] RTOVR undersampling: "
                      f"{op.trace_id} reads before {prod_id} ready")
                return False
            
            # 检查 2：RTOVR 采样时生产者 latch 尚未被覆盖
            if t_rtovr_inferred >= t_prod + II:
                print(f"[VERIFIER-FAIL] RTOVR overwrite: "
                      f"{op.trace_id} reads after {prod_id} latch overwritten")
                return False
        
        # 其余审计（模冲突、相位）保持不变
        ...
```

### 4.3 等价性测试（迁移验证）

迁移前后必须在相同 DFG 上得到等价或更优的结果：

```bash
# 测试脚本：tests/test_rtovr_abstraction.py

# Step 1: 记录优化前的基准
python3 verification_v2/main_verify.py --op horner --n-rows 16 > baseline.log
# 期望：II=N（某个基准值），PASS

# Step 2: 应用优化后重新测试
# 期望：II <= N（不应变大），PASS

# Step 3: 数值对比
# 仿真输出的 golden_data.npz 与基准必须 bit-exact 一致
```

### 4.4 测试矩阵

| 测试用例 | 验证目标 | 通过标准 |
|---------|---------|---------|
| `horner --n-rows 16` | RTOVR 增广边正确性 | II ≤ 基准，仿真 PASS |
| 单 RTOVR 链（prod→RTOVR→ALU） | 最小时序单元 | `t_alu == t_prod + lat + 1` |
| 双 RTOVR 到同一消费者（S0, S1） | 两条独立增广边 | 两个上下界均满足 |
| 生产者 latch 满窗口（t_rtovr = t_prod + II - 1） | 上界极限 | 合法，PASS |
| 生产者 latch 超窗口（t_rtovr = t_prod + II） | 上界违例 | SMT UNSAT 或 Verifier FAIL |

---

## 5. 迁移步骤（有序执行）

```
Step 1: DFG 层添加 rtovr_deps 标记（不修改 SMT）
  → 验证：所有 DFG 构造正确标注 via_rtovr 边
  → 测试：DFG 单元测试，与现有 depends_on 对比

Step 2: dependency_axioms.py 添加增广边逻辑（并行保留旧公理）
  → 验证：两套公理同时运行，结果一致
  → 测试：II 值不变，schedule 等价

Step 3: ucode_solver.py 移除 RTOVR 节点，禁用 6 个旧公理
  → 验证：仅增广边运行
  → 测试：horner 全量回归，II ≤ 基准

Step 4: validate_physical_schedule 更新为反推验证
  → 验证：verifier 能捕获上界/下界违例
  → 测试：故意注入违例 DFG，verifier 必须拒绝

Step 5: 清理旧代码（physical_axioms.py 中 RTOVR 专属公理）
```

---

*Last Updated: 2026-04-29*  
*AIACC SMT Optimization Team*
