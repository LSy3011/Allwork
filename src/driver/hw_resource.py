from enum import Enum, IntEnum

class ResourceType(Enum):
    """
    AIACC 硬件资源物理分类 (逻辑占位符)。
    DFG 在定义时使用这些符号，由 SMT 后端动态映射到物理 IQ ID。
    """
    UR_READ = "UR_READ"
    UR_WRITE = "UR_WRITE"
    RTOVR = "RTOVR"
    FPMUL = "FPMUL"
    FPADD = "FPADD"
    INTADD = "INTADD"
    LOGIC = "LOGIC"
    FPTOFP = "FPTOFP"
    FPTOINT = "FPTOINT"
    INTTOFP = "INTTOFP"
    INTTOINT = "INTTOINT"
    INT8TOFP16 = "INT8TOFP16"
    FP16MUL = "FP16MUL"
    FP32ADDTREE5TO1 = "FP32ADDTREE5TO1"
    FP32ADDTREE8TO1 = "FP32ADDTREE8TO1"
    SHIFTUP = "SHIFTUP"
    SHIFTDN = "SHIFTDN"

class PortSlot(IntEnum):
    """
    RtoVR 网格物理插槽占位符。
    映射到 constants.py 中的物理编号。
    
    🛸 [V4.0 更新]：对齐 INT8TOFP16 和多字并行接口。
    """
    # --- 消费者 (Input Side / RtoVR Destinations) ---
    INTADD_S0 = 0
    INTADD_S1 = 1
    INTADD_S2 = 2
    FPMUL_S0  = 3
    FPMUL_S1  = 4
    FPADD_S0  = 5
    FPADD_S1  = 6
    LOGIC_S0  = 11
    LOGIC_S1  = 12
    LOGIC_ST  = 13
    INT8TOFP16_S0 = 17
    INT8TOFP16_S1 = 18
    UR_W0_S   = 35
    UR_W1_S   = 36
    UR_W2_S   = 37
    UR_W3_S   = 38
    
    # --- 生产者 (Output Side / RtoVR Sources) ---
    INTADD_D0 = 0
    INTADD_D1 = 1
    INTADD_ST = 2
    FPMUL_D   = 3
    FPADD_D   = 4
    FPADD_ST  = 5
    LOGIC_D   = 10
    INT8TOFP16_D0 = 14
    INT8TOFP16_D1 = 15
    INT8TOFP16_D2 = 16
    INT8TOFP16_D3 = 17
    UR_R0_D   = 30
    UR_R1_D   = 31
    UR_R2_D   = 32
    UR_R3_D   = 33
