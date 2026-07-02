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
    SMI_DS_T = "SMI_DS_T"   # ADR-034: 跨-SMC 转置（SMI 中心单实例，指令驱动）
    LDB = "LDB"             # ADR-050: Load-Buffer UR 写侧（iq_inst[26]，56b CRU）
    LDB_AXI = "LDB_AXI"     # ADR-050: Load-Buffer AXI 读侧（iq_inst[27]，104b CRU）

class PortSlot(IntEnum):
    """
    RtoVR 网格物理插槽占位符。
    映射到 constants.py 中的物理编号。
    
    🛸 [V4.0 更新]：对齐 INT8TOFP16 和多字并行接口。
    """
    # --- 消费者 (ALU 输入插槽 / RtoVR Destinations) ---
    INTADD_S0 = 0
    INTADD_S1 = 1
    INTADD_S2 = 2
    FPMUL_S0  = 3
    FPMUL_S1  = 4
    FPADD_S0  = 5
    FPADD_S1  = 6
    FPTOFP_S  = 7
    INTTOINT_S = 8
    FPTOINT_S = 9
    INTTOFP_S = 10
    LOGIC_S0  = 11
    LOGIC_S1  = 12
    LOGIC_ST  = 13
    VIN0_S    = 14
    VIN1_S    = 15
    VOUT0_S   = 16
    INT8TOFP16_S0 = 17
    INT8TOFP16_S1 = 18
    FP16MUL_S0 = 19
    FP16MUL_S1 = 20
    FP16MUL_S2 = 21
    FP16MUL_S3 = 22
    FP16MUL_S4567 = 23
    FP32ADDTREE5TO1_S0 = 24
    FP32ADDTREE5TO1_S1 = 25
    FP32ADDTREE5TO1_S2 = 26
    FP32ADDTREE5TO1_S3 = 27
    FP32ADDTREE5TO1_S4 = 28
    FP32ADDTREE5TO1_S5 = 29
    FP32ADDTREE5TO1_S6 = 30
    FP32ADDTREE5TO1_S7 = 31
    FP32ADDTREE8TO1_S0 = 32
    FP32ADDTREE8TO1_S1 = 33
    SHIFTDN_S = 34
    UR_W0_S   = 35
    UR_W1_S   = 36
    UR_W2_S   = 37
    UR_W3_S   = 38
    
    # --- 生产者 (ALU 输出端口 / RtoVR Sources) ---
    INTADD_D0 = 0
    INTADD_D1 = 1
    INTADD_ST = 2
    FPMUL_D   = 3
    FPADD_D   = 4
    FPADD_ST  = 5
    FPTOFP_D  = 6
    INTTOINT_D = 7
    FPTOINT_D = 8
    INTTOFP_D = 9
    LOGIC_D   = 10
    VIN0_D    = 11
    VIN1_D    = 12
    VOUT0_D   = 13
    INT8TOFP16_D0 = 14
    INT8TOFP16_D1 = 15
    INT8TOFP16_D2 = 16
    INT8TOFP16_D3 = 17
    FP16MUL_D0 = 18
    FP16MUL_D1 = 19
    FP16MUL_D2 = 20
    FP16MUL_D3 = 21
    FP16MUL_D4 = 22
    FP16MUL_D5 = 23
    FP16MUL_D6 = 24
    FP16MUL_D7 = 25
    FP32ADDTREE5TO1_D0 = 26
    FP32ADDTREE5TO1_D1 = 27
    FP32ADDTREE8TO1_D = 28
    SHIFTUP_D = 29
    UR_R0_D   = 30
    UR_R1_D   = 31
    UR_R2_D   = 32
    UR_R3_D   = 33


# ====================================================================
# ADR-018 v7 D1: 多 SMC enum 维度扩展 helper
# 现 enum 值是 base id（全局 RtoVR/IQ 命名空间，单 SMC 视角）；
# per-SMC 资源访问通过 helper 计算 unique key，不破现 enum 数值。
# 旧 caller 显式传 smc_id=0 → 行为与单 SMC 模式等价。
# ====================================================================

def get_per_smc_resource_id(enum_val, smc_id: int = 0, smc_num: int = 32) -> int:
    """
    返回 per-SMC 唯一的资源 ID（用于 axiom key / cluster 分发）。

    实现：enum_val 作为本 SMC 内的 base offset，叠加 smc_id × smc_num_units_stride。
    当前最简策略：(smc_id, enum_val) 用 (smc_id * 1000 + enum_val) 编码——
        - 1000 远大于现 enum 最大值（38）保证不冲突
        - 32 SMC × 1000 = 32000，远小于 SMT Int 32-bit 上限
    未来如需改 encoding（如真正分配 per-SMC IQ pool），改此函数即可。

    Args:
        enum_val: RtoVRInputSource / RtoVROutput / IQ 等 enum 的值（int 或 IntEnum 成员）
        smc_id: SMC index ∈ [0, smc_num)
        smc_num: 总 SMC 数量（默认 32 = PARAM_SMC_NUM）

    Returns:
        唯一 per-SMC 资源 ID
    """
    if not (0 <= smc_id < smc_num):
        raise ValueError(f"smc_id={smc_id} out of range [0, {smc_num})")
    val = int(enum_val) if hasattr(enum_val, '__int__') else enum_val
    return smc_id * 1000 + val
