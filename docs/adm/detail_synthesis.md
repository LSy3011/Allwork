# SMT 合成引擎 (ADM 详细规格 - 2/3)

> 来源：设计全书 §6。定义 Z3 决策空间与六大物理公理。

## 1. Z3 决策变量
- $T_i \in [0, MaxCycle]$: 绝对发射拍数
- $B_k \in [0, 7]$: 张量绑定 Bank ID
- $A_k \in [0, BankDepth]$: Bank 内偏移

## 2. 六大物理公理

### 公理 1: RAW 时序依赖
```python
solver.add(T_j >= T_p + hw_spec.unit_latency[p.op_type])
```

### 公理 2A: R-R Bank 冲突
每 Bank 每拍最多 1 个 UR-R。
```python
solver.add(z3.Sum(reads_at_bank_t) <= 1)
```

### 公理 2B: W-W Bank 冲突
每 Bank 每拍最多 1 个 UR-W。

### 公理 3: UR 总线路数
```python
solver.add(z3.Sum(total_reads_at_t) <= hw_spec.ur_read_ports)  # =4
solver.add(z3.Sum(total_writes_at_t) <= hw_spec.ur_write_ports) # =4
```

### 公理 4: 计算单元实例
```python
solver.add(z3.Sum(ops_of_type_at_t) <= max_inst)
```

### 公理 5: 无 FIFO 输出绑定
每个输出必须写回合法 Bank。

### 公理 6: RtoVR-ALU 刚性同步
```python
solver.add(T_alu == T_rtovr + 1)  # 无弹性气泡
```

> **简化说明**：RtoVR 为分布式架构(40路独立IQ)，
> Target Slot 无需额外互斥约束，仅受 UR-R 总线宽度限制。

## 3. 优化目标
```python
objective = solver.minimize(z3.Max([T_i for i in all_ops]))
```

## 4. 压力测试接口
```python
forced_map = {"tensor_A": 0, "tensor_B": 0}
solver.add(B_k == forced_map[k])  # 强制注入
```
- 强制同 Bank → Z3 被迫错拍 → DLY 自动生成
- MaxCycle 太小 → UNSAT（验证鲁棒性）
