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
## 驱动层 (Driver) 与 库 (Library) 交互契约

### 契约 020: 物理属性点火协议 (ADR-006)

1. **库 (Library) 义务**：
   - 必须通过 `MIROp.metadata` 提供硬件 Spec (YAML) 中定义的物理控制属性。
   - 属性名必须与 YAML 中的 `field` 名（如 `SUB`, `SRC_PREC`, `UPDATE_ST`）严格一致，禁止使用自定义缩写。
   - 对于 `FPADD`/`FPMUL` 等算子，不仅要指定操作数，还需显式配置其计算模式比特。

2. **驱动 (Driver) 义务**：
   - **禁止猜测驱动**：后端驱动不得根据算子字符串名（如 `FPSUB`）进行隐式属性补偿。
   - **自动化映射**：驱动层必须支持 `Metadata-to-Bit` 的自动查找映射协议。
   - **物理完备性检查**：如果 `metadata` 中存在硬件 Spec 中未定义的字段，驱动应在 `lint` 阶段报错或通过审计日志预警。

3. **数据流归属权**：
   - 指令的“灵魂”由库定义（属性值）。
   - 指令的“肉体”由驱动拼装（比特偏移、掩码、补零）。

## 目录分工速查 (ADM 视角)
| 阶段 | 验证器 | 验证什么 |
|:--- |:--- |:--- |
| Post-SMT | SMTOutputVerifier | timing + exclusions 逻辑合法 |
| Post-Stitch | StreamScanner | 缝合后无 R-R/W-W 冲突 |
| Post-Layout | **ConstraintChecker** | bank_map 满足所有 exclusions |
