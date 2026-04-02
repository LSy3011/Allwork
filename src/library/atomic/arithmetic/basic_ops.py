from typing import List
from ir.mir.mir_op import MIROp
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS

def get_add_dfg() -> List[MIROp]:
    """基础加法 (V5.4-Factory): 移除硬编码 Bank，完全信赖 SMT。"""
    b = MIROpBuilder()
    # 🛸 统一 ID 为 x_rd, y_rd
    x = b.make_op("UR_READ", "x_rd", RT.UR_READ, [], "LOAD X")
    y = b.make_op("UR_READ", "y_rd", RT.UR_READ, [], "LOAD Y")
    
    r0 = b.make_op("RTOVR", "r0_add", PS.FPADD_S0, [x])
    r1 = b.make_op("RTOVR", "r1_add", PS.FPADD_S1, [y])
    
    res = b.make_op("FPADD", "res_add", RT.FPADD, [r0, r1])
    
    rt_fin = b.make_op("RTOVR", "rt_fin", PS.UR_W1_S, [res])
    b.make_op("UR_WRITE", "w_fin", RT.UR_WRITE, [rt_fin], bank=7)
    return b.get_dfg()

def get_mul_dfg() -> List[MIROp]:
    """基础乘法 (V5.4-Factory)。"""
    b = MIROpBuilder()
    x = b.make_op("UR_READ", "x_rd", RT.UR_READ, [], "LOAD X")
    y = b.make_op("UR_READ", "y_rd", RT.UR_READ, [], "LOAD Y")
    
    r0 = b.make_op("RTOVR", "r0_mul", PS.FPMUL_S0, [x])
    r1 = b.make_op("RTOVR", "r1_mul", PS.FPMUL_S1, [y])
    
    res = b.make_op("FPMUL", "res_mul", RT.FPMUL, [r0, r1])
    
    rt_fin = b.make_op("RTOVR", "rt_fin", PS.UR_W1_S, [res])
    b.make_op("UR_WRITE", "w_fin", RT.UR_WRITE, [rt_fin], bank=7)
    return b.get_dfg()

def get_reciprocal_dfg() -> List[MIROp]:
    """倒数迭代 (V5.4-Factory): 对齐测试脚本 ID。"""
    b = MIROpBuilder()
    # 🛸 统一 ID 对齐 test_arith_bank_fuzz.py
    x = b.make_op("UR_READ", "x_rd", RT.UR_READ, [])
    y0 = b.make_op("UR_READ", "y0_rd", RT.UR_READ, [])
    c2 = b.make_op("UR_READ", "c2_rd", RT.UR_READ, [])
    
    r_x = b.make_op("RTOVR", "r_x", PS.FPMUL_S0, [x])
    r_y0_1 = b.make_op("RTOVR", "r_y0_1", PS.FPMUL_S1, [y0])
    xy0 = b.make_op("FPMUL", "xy0", RT.FPMUL, [r_x, r_y0_1])
    
    r_c2 = b.make_op("RTOVR", "r_c2", PS.FPADD_S0, [c2])
    r_xy0 = b.make_op("RTOVR", "r_xy0", PS.FPADD_S1, [xy0])
    h = b.make_op("FPADD", "h_step", RT.FPADD, [r_c2, r_xy0], SUB=1)
    
    r_y0_2 = b.make_op("RTOVR", "r_y0_2", PS.FPMUL_S0, [y0])
    r_h = b.make_op("RTOVR", "r_h", PS.FPMUL_S1, [h])
    res = b.make_op("FPMUL", "res_y", RT.FPMUL, [r_y0_2, r_h])
    
    rt_fin = b.make_op("RTOVR", "rt_fin", PS.UR_W1_S, [res])
    b.make_op("UR_WRITE", "w_fin", RT.UR_WRITE, [rt_fin], bank=7)
    return b.get_dfg()
