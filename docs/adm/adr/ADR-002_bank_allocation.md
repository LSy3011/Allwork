# ADR-002: Bank 分配架构 (方案 B+: 约束分离)

## 1. 问题描述
编译器有两个阶段涉及 Bank 分配：SMT（原子内部）和 Layout（全局）。
如果两者都产出"固定 Bank ID"，出了 Bug 无法定位责任方。

## 2. 决策：方案 B+ (约束与分配分离)

**核心原则：SMT 只产出约束，不分配具体 Bank ID。**

### SMT 输出 (约束)
```python
class APB:
    timing: Dict[int, int]           # op_id → cycle (固定)
    exclusions: List[Tuple[str,str]]  # 不能同 Bank 的张量对
    windows: Dict[str, Tuple[int,int]] # 张量占用窗口
    total_cycles: int
    footprint: FootprintMask          # 时序足迹（不含 Bank ID）
```

### Layout 输出 (分配)
```python
class AllocationResult:
    bank_map: Dict[str, int]  # tensor_id → 具体 Bank ID
    # 必须满足 SMT 所有 exclusions
```

## 3. 三层职责切割

| 层 | 决定 | 不决定 | 验证工具 |
|:--- |:--- |:--- |:--- |
| SMT | 操作时序 + Bank 互斥约束 | 具体 Bank ID | SMTOutputVerifier |
| Stitcher | APB 间相位偏移 (DLY) | Bank, 内部时序 | StreamScanner |
| Layout | 所有张量的具体 Bank ID | 时序, DLY | ConstraintChecker |

## 4. 定位 Bug 的三步法
```
仿真不对
├→ SMTOutputVerifier 违反？ → SMT 公理写错
├→ StreamScanner 有冲突？  → Stitcher Phase Shift 不够
└→ ConstraintChecker 违反？→ Layout 没尊重 SMT 约束
```

## 5. 对现有设计的影响
- `se/module_contracts.md`：APB 输出结构移除 `bank_assign`，改为 `exclusions`
- `tfd/phase3_synthesis.md`：SMT 测试改为验证"约束合法性"而非"Bank ID 正确性"
- `tfd/phase5_layout_driver.md`：Layout 测试新增"满足 SMT 约束"检查
- 新增验证工具：`ConstraintChecker`（验证 Layout 输出满足 SMT 约束）
