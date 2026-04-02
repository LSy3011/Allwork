# 模块接口契约 (SE 专属, ADR-002 更新)

> 定义模块之间的通信边界。每个模块只通过这里定义的接口互相调用。

## 1. api → synthesis 接口
```python
spec: HardwareSpec
ops: List[MIROp]

# APB 输出：时序 + 约束（不含具体 Bank ID）[ADR-002]
class APB:
    timing: Dict[str, int]           # trace_id → cycle
    iq_map: Dict[str, int]           # trace_id -> 物理 IQ ID (由 SMT 动态分配)
    exclusions: List[Tuple[str,str]] # 不能同 Bank 的张量对 (TensorID)
    windows: Dict[str, Tuple[int,int]] # tensor_id → (start_cycle, end_cycle)
    total_cycles: int
    footprint: FootprintMask         # 物理时序足迹
    status: str                      # "sat" | "unsat"

### 契约 011: 时序稳定性协议 (Regression Guard)
1. **合成器义务**: 在输出 APB 前，必须通过 `RegressionManager` 进行黄金时序核验。
2. **约束**: 若当前解的 Total Cycles 超过 Golden 快照，必须标记为性能退化（REGRESSION）。
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

## 4. 编排层 (Orchestrator) 契约 [NEW]
### 契约 030: 零间隙物理审计协议 (ADR-005)

1. **编排器义务**: 
   - 必须通过 `SpecRenderer` 输出 SECTION 0-3 的全量机器状态。
   - 对每个物理拍 (Cycle) 必须提供原子脉冲日志 (Atomic Pulse Log)。
2. **输出要求**:
   - 支持 Gapless Audit Mode，IDLE 拍也必须显式占位以防对齐偏移。

## 5. layout → driver 接口 (LIR Packer v5)
```python
class EmissionResult:
    lir_stream: List[LIROp]
    iq_bin_map: Dict[str, bytes]    # iq_id → binary_image
    compression_ratio: float
```

## 6. 接口原则
- **单向数据流**：上游→下游，不回流
- **不可变输入**：不得修改上游对象
- **约束传递**：SMT 的 exclusions 必须透传到 Layout [ADR-002]
- **自诊断**：错误时返回 diagnostics

## 7. 验证工具链 [ADR-002]
## 驱动层 (Driver) 与 库 (Library) 交互契约

### 契约 020: 物理属性点火协议 (ADR-006)

1. **库 (Library) 义务**：
   - **显式属性映射**: 必须在 `MIROp.metadata` 中显式提供硬件 Spec (YAML) 对应的物理控制字段。
   - **语义对齐**: 属性名必须与 `unit.yaml` 的 `code_fields.name`（如 `SUB`, `SRC_PREC`）严格一致，禁止缩写。

2. **驱动 (Driver) 义务**：
   - **无偏见打包原理**: 驱动程序回归为 Mechanical Bit-Packer，严禁包含基于算子名的推断。
   - **Waterfall Priority**: 位填充优先级遵循：显式覆盖 (Explicit) > 通用语义 (Semantic) > 规格默认 (Default)。
   - **物理完整性**: 必须验证 metadata 注入的字段是否在硬件 Spec 闭包内，非法字段需报错。

3. **数据流归属权**：
   - 指令的“灵魂”由库定义（属性值）。
   - 指令的“肉体”由驱动拼装（比特偏移、掩码、补零）。

## 目录分工速查 (ADM 视角)
| 阶段 | 验证器 | 验证什么 |
|:--- |:--- |:--- |
| Post-SMT | SMTOutputVerifier | timing + exclusions 逻辑合法 |
| Post-Stitch | StreamScanner | 缝合后无 R-R/W-W 冲突 |
| Post-Layout | **ConstraintChecker** | bank_map 满足所有 exclusions |
