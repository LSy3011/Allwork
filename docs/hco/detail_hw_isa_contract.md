# AIACC V5.0 物理指令契约 (ISA Authoritative Contract)

本文件定义了编译器的“单一真相源 (Single Source of Truth)”。所有算子库 (Library)、驱动 (Driver) 及仿真器 (Simulator) 必须遵循此命名约定，禁止在代码中使用非标字段。

## 1. 核心算子物理布局

### 1.1 浮点加减 (FPADD)
- **单元标识**: `FPADD`
- **比特布局**: 
  - `VALID` [1 bit]: 0:INVALID, 1:VALID
  - `SRC_PREC` [1 bit]: 0:PREC_16, 1:PREC_32
  - `DST_PREC` [1 bit]: 0:PREC_16, 1:PREC_32
  - `UPDATE_ST` [1 bit]: 0:NO, 1:YES
  - `SUB` [1 bit]: 0:ADD, 1:SUB
  - `SMC_ID` [5 bits]: 执行 SMC ID

### 1.2 浮点乘法 (FPMUL)
- **单元标识**: `FPMUL`
- **比特布局**: 
  - `VALID` [1 bit]: 0:INVALID, 1:VALID
  - `SRC_PREC` [1 bit]: 0:PREC_16, 1:PREC_32
  - `DST_PREC` [1 bit]: 0:PREC_16, 1:PREC_32
  - `SMC_ID` [5 bits]: 执行 SMC ID

### 1.3 统一寄存器读取 (UR_READ)
- **单元标识**: `UR_READ`
- **比特布局**: 
  - `VALID` [1 bit]: 使能
  - `BANK` [3 bits]: 物理 Bank ID (0-7)
  - `ADDR` [8 bits]: 物理地址 (0-255)
  - `SMC_ID` [5 bits]: 执行 SMC ID

### 1.4 统一寄存器写入 (UR_WRITE)
- **单元标识**: `UR_WRITE`
- **比特布局**: 
  - `VALID` [1 bit]: 使能
  - `BANK` [3 bits]: 物理 Bank ID (0-7)
  - `ADDR` [8 bits]: 物理地址 (0-255)
  - `wbe` [16 bits]: 写掩码 (Write Byte Enable)
  - `SMC_ID` [5 bits]: 执行 SMC ID

### 1.5 数据路由 (RTOVR)
- **单元标识**: `RTOVR`
- **比特布局**: 
  - `VALID` [1 bit]: 使能
  - **`SEL`** [6 bits]: 物理源端口 ID (0-63)

## 2. 映射规范 (Mapping Protocol)

1. **算子库**：在 `make_op` 中注入 metadata 时，必须匹配上述“比特布局”一栏中的全大写名称（如 `SUB`, `SEL`）。
2. **驱动层**：`FieldMapper` 必须优先按照上述真名执行 spec 反射映射。
3. **仿真器**：`Decoder` 已经基于此协议实现，严禁修改。

---
*HCO 备注：任何偏离此契约的代码变更均为 Invalid。*
