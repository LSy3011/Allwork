# 模块接口契约 (SE 专属, ADR-002 更新)

> 定义模块之间的通信边界。每个模块只通过这里定义的接口互相调用。

## 1. api → synthesis 接口
```python
spec: HardwareSpec
ops: List[MIROp]

# APB 输出：时序 + 约束（不含具体 Bank ID）[ADR-002]
class APB:
    timing: Dict[int, int]           # op_id → cycle
    exclusions: List[Tuple[str,str]] # 不能同 Bank 的张量对
    windows: Dict[str, Tuple[int,int]] # tensor_id → (start_cycle, end_cycle)
    total_cycles: int
    footprint: FootprintMask         # 时序足迹
    status: str                      # "sat" | "unsat"
```

## 2. synthesis → orchestrator 接口
```python
apb_list: List[APB]

class StitchedResult:
    mir_stream: List[MIROp]         # 带绝对时间戳
    dly_map: Dict[int, int]         # op_id → dly_value
    merged_exclusions: List[Tuple[str,str]]  # 合并后的全局互斥约束
    scan_report: ScanReport         # 冲突扫描
```

## 3. orchestrator → layout 接口 [ADR-002 核心变更]
```python
scheduled_mir: List[MIROp]
exclusions: List[Tuple[str,str]]    # 来自 SMT 的互斥约束

class AllocationResult:
    bank_map: Dict[str, int]        # tensor_id → 具体 Bank ID
    spilled: List[str]
    # 断言: bank_map 必须满足所有 exclusions
```

## 4. layout → driver 接口 (LIR Packer v5)
```python
class EmissionResult:
    lir_stream: List[LIROp]
    iq_bin_map: Dict[str, bytes]    # iq_id → binary_image
    compression_ratio: float
```

## 5. 接口原则
- **单向数据流**：上游→下游，不回流
- **不可变输入**：不得修改上游对象
- **约束传递**：SMT 的 exclusions 必须透传到 Layout [ADR-002]
- **自诊断**：错误时返回 diagnostics

## 6. 验证工具链 [ADR-002]
| 阶段 | 验证器 | 验证什么 |
|:--- |:--- |:--- |
| Post-SMT | SMTOutputVerifier | timing + exclusions 逻辑合法 |
| Post-Stitch | StreamScanner | 缝合后无 R-R/W-W 冲突 |
| Post-Layout | **ConstraintChecker** | bank_map 满足所有 exclusions |
