# 模块接口契约 (SE 专属)

> 定义模块之间的通信边界。每个模块只通过这里定义的接口互相调用。

## 1. api → synthesis 接口
```python
# synthesis 从 api 获取的唯一输入
spec: HardwareSpec    # 物理参数
ops: List[MIROp]      # MIR 指令流

# synthesis 返回的唯一输出
class APB:
    schedule: Dict[int, int]      # op_id → cycle
    bank_assign: Dict[int, int]   # tensor_id → bank_id
    total_cycles: int
    footprint: FootprintMask
    status: str                   # "sat" | "unsat"
```

## 2. synthesis → orchestrator 接口
```python
# orchestrator 接收多个 APB 进行缝合
apb_list: List[APB]

# orchestrator 返回缝合后的 MIR 流
class StitchedResult:
    mir_stream: List[MIROp]       # 带绝对时间戳
    dly_map: Dict[int, int]       # op_id → dly_value
    scan_report: ScanReport       # R-R/W-W/Port 冲突扫描
```

## 3. orchestrator → layout 接口
```python
# layout 接收带时间戳的 MIR 流
scheduled_mir: List[MIROp]

# layout 返回 Bank 绑定
class AllocationResult:
    bank_map: Dict[str, int]      # tensor_id → bank_id
    spilled: List[str]            # 溢写到主存的张量
```

## 4. layout → driver 接口
```python
# driver 接收带 Bank 地址的 MIR，输出 LIR 二进制
class EmissionResult:
    lir_stream: List[LIROp]       # 物理指令流
    binary: bytes                 # 41-bit 打包
```

## 5. 接口原则
- **单向数据流**：数据只从上游流向下游，不回流
- **不可变输入**：每个模块不得修改上游传入的对象
- **自诊断**：每个模块发生错误时，返回 `diagnostics` 字段说明原因
