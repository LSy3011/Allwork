"""
AIACC 算子构造脚手架 (V5.0 Compliant)
本模板强制对齐 MIROpBuilder 接口，确保物理元数据不发生覆盖或冲突。
"""
from typing import List
from ir.mir.mir_op import MIROp
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS

def get_your_op_dfg() -> List[MIROp]:
    """工业级算子定义建议"""
    b = MIROpBuilder()
    
    # 1. 📥 物理加载 (Bank 由测试用例指定)
    x = b.make_op("UR_READ", "x_rd", RT.UR_READ, [], "Load input x", bank=0)
    y = b.make_op("UR_READ", "y_rd", RT.UR_READ, [], "Load input y", bank=1)
    
    # 2. 🛰️ 影子路由 (RTOVR 必须使用 PS 枚举作为第三参数)
    r0 = b.make_op("RTOVR", "r0_op", PS.FPADD_S0, [x], "Route x to S0")
    r1 = b.make_op("RTOVR", "r1_op", PS.FPADD_S1, [y], "Route y to S1")
    
    # 3. ⚡ 计算 (ALU 必须使用 RT 枚举作为第三参数)
    # 如需做减法，传入 SUB=1
    res = b.make_op("FPADD", "res_op", RT.FPADD, [r0, r1], "Operation Logic")
    
    # 4. 📤 物理写回 (UR_W1_S 锚定在 Slot 36)
    rt_fin = b.make_op("RTOVR", "rt_fin", PS.UR_W1_S, [res], "Route to bank")
    b.make_op("UR_WRITE", "w_fin", RT.UR_WRITE, [rt_fin], "Write output", bank=7)
    
    return b.get_dfg()
