from typing import List
from ir.mir.mir_op import MIROp
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS

def get_sin_8order_dfg() -> List[MIROp]:
    """
    AIACC Sin 算子 (ADPC V3.3 物理保真版)
    算法：泰勒展开 sin(x) ≈ x * (1 - x^2 * (1/3! - x^2 * (1/5! - x^2 * 1/7!)))
    
    🛸 物理特性注意：
    AIACC 为 Pulse-Pass 架构，多次引用的中间变量 (x, x^2) 必须显式写回并在使用处重读。
    """
    b = MIROpBuilder()
    
    # 🛸 [Standard V3.0] 初始角色与常数固化
    # 我们标记主输入 x 为 operand_a
    x_init = b.make_op("UR_READ", "x_init", RT.UR_READ, [], "LOAD X", role="operand_a")
    
    # 常数固化（泰勒系数）
    c1 = b.make_op("UR_READ", "c1_const", RT.UR_READ, [], "1/7!", is_const=True, value=0.0001984127)
    c2 = b.make_op("UR_READ", "c2_const", RT.UR_READ, [], "1/5!", is_const=True, value=0.0083333333)
    c3 = b.make_op("UR_READ", "c3_const", RT.UR_READ, [], "1/3!", is_const=True, value=0.1666666667)
    c4 = b.make_op("UR_READ", "c4_const", RT.UR_READ, [], "1.0",  is_const=True, value=1.0)
    
    # --- Step 1: y = x^2 并保存到 Bank ---
    r0_y1 = b.make_op("RTOVR", "r0_y1", PS.FPMUL_S0, [x_init])
    r1_y1 = b.make_op("RTOVR", "r1_y1", PS.FPMUL_S1, [x_init])
    y_val = b.make_op("FPMUL", "y_sq", RT.FPMUL, [r0_y1, r1_y1], "y=x*x")
    
    # 💾 物理保鲜：必须存入 Bank 1，否则后续读不到
    rt_y_save = b.make_op("RTOVR", "rt_y_save", PS.UR_W0_S, [y_val])
    w_y = b.make_op("UR_WRITE", "w_y", RT.UR_WRITE, [rt_y_save], bank=1)

    # --- Step 2: a = y * c1 ---
    y_re1 = b.make_op("UR_READ", "y_re1", RT.UR_READ, [w_y], "y_re1", bank=1)
    r0_a = b.make_op("RTOVR", "r0_a", PS.FPMUL_S0, [y_re1])
    r1_a = b.make_op("RTOVR", "r1_a", PS.FPMUL_S1, [c1])
    a = b.make_op("FPMUL", "a_val", RT.FPMUL, [r0_a, r1_a], "a=y*c1")
    
    # --- Step 3: b = c2 - a ---
    r0_b = b.make_op("RTOVR", "r0_b", PS.FPADD_S0, [c2])
    r1_b = b.make_op("RTOVR", "r1_b", PS.FPADD_S1, [a])
    b_val = b.make_op("FPADD", "b_val", RT.FPADD, [r0_b, r1_b], "b=c2-a", SUB=1)

    # --- Step 4: c = y * b ---
    y_re2 = b.make_op("UR_READ", "y_re2", RT.UR_READ, [w_y], "y_re2", bank=1)
    r0_c = b.make_op("RTOVR", "r0_c", PS.FPMUL_S0, [y_re2])
    r1_c = b.make_op("RTOVR", "r1_c", PS.FPMUL_S1, [b_val])
    c = b.make_op("FPMUL", "c_val", RT.FPMUL, [r0_c, r1_c], "c=y*b")

    # --- Step 5: d = c3 - c ---
    r0_d = b.make_op("RTOVR", "r0_d", PS.FPADD_S0, [c3])
    r1_d = b.make_op("RTOVR", "r1_d", PS.FPADD_S1, [c])
    d = b.make_op("FPADD", "d_val", RT.FPADD, [r0_d, r1_d], "d=c3-c", SUB=1)

    # --- Step 6: e = y * d ---
    y_re3 = b.make_op("UR_READ", "y_re3", RT.UR_READ, [w_y], "y_re3", bank=1)
    r0_e = b.make_op("RTOVR", "r0_e", PS.FPMUL_S0, [y_re3])
    r1_e = b.make_op("RTOVR", "r1_e", PS.FPMUL_S1, [d])
    e = b.make_op("FPMUL", "e_val", RT.FPMUL, [r0_e, r1_e], "e=y*d")

    # --- Step 7: f = 1 - e ---
    r0_f = b.make_op("RTOVR", "r0_f", PS.FPADD_S0, [c4])
    r1_f = b.make_op("RTOVR", "r1_f", PS.FPADD_S1, [e])
    f = b.make_op("FPADD", "f_val", RT.FPADD, [r0_f, r1_f], "f=1-e", SUB=1)

    # --- Step 8: sin = x * f ---
    # 🛸 因为 x 最初是从 Bank 0 读的，这么久过去了，必须重读以保证信号强度
    x_re_fin = b.make_op("UR_READ", "x_re_fin", RT.UR_READ, [], "RE-LOAD X", role="operand_a")
    r0_s = b.make_op("RTOVR", "r0_s", PS.FPMUL_S0, [x_re_fin])
    r1_s = b.make_op("RTOVR", "r1_s", PS.FPMUL_S1, [f])
    res = b.make_op("FPMUL", "sin_res", RT.FPMUL, [r0_s, r1_s], "sin=x*f")

    # 🛸 [Standard V3.0] 输出锚定
    rt_fin = b.make_op("RTOVR", "rt_fin", PS.UR_W1_S, [res])
    b.make_op("UR_WRITE", "w_fin", RT.UR_WRITE, [rt_fin], 
              bank=7, is_output=True, meta_name="sin_result")

    return b.get_dfg()
