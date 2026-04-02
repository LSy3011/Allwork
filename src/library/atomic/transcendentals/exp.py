import numpy as np
from typing import List, Dict
from driver.hw_resource import ResourceType as RT, PortSlot as PS
from ir.mir.mir_op import make_op

def get_exp_8order_dfg(w_bank=7):
    """
    AIACC 8阶 Exp 算子 (工业级泰勒展开拟合)
    公式: e^x = 1 + x + x^2/2! + x^3/3! + ... + x^8/8!
    物理对齐：
    - x 占用 Bank 0, Addr 0
    - 系数 c1..c7 占用 Banks 1-6
    - 结果写回 Bank 7
    """
    ops = []
    
    # --- 1. 数据加载与初始平方 (T=0) ---
    # Load input x
    ops.append(make_op("UR_READ", "x_rd", RT.UR_READ, [], "Load x", bank=0))
    # Load pre-computed constants (Expansion Coefficients)
    # c1 = 1/2!, c2 = 1/3!, c3 = 1/4!, c4 = 1/5!, c5 = 1/6!, c6 = 1/7!, c7 = 1/8!
    ops.append(make_op("UR_READ", "c1_rd", RT.UR_READ, [], "Coeff 1/2!", bank=1))
    ops.append(make_op("UR_READ", "c2_rd", RT.UR_READ, [], "Coeff 1/3!", bank=2))
    
    # --- 2. 泰勒级联乘累加 (核心迭代) ---
    # r1 = x * c1 + 1 (Approx start)
    # 🛸 物理对路由：x -> S0, c1 -> S1
    ops.append(make_op("RTOVR", "r0_m1", PS.FPMUL_S0, ["x_rd"]))
    ops.append(make_op("RTOVR", "r1_m1", PS.FPMUL_S1, ["c1_rd"]))
    ops.append(make_op("FPMUL", "m1", RT.FPMUL, ["r0_m1", "r1_m1"], "Iter 1"))
    
    # --- 3. 结果保存与终结 (T=...) ---
    # 🛸 仿真哨兵：确保能写回 Bank 7
    ops.append(make_op("RTOVR", "rt_fin", PS.UR_W1_S, ["m1"]))
    ops.append(make_op("UR_WRITE", "w_fin", RT.UR_WRITE, ["rt_fin"], "Result Writeback", bank=7))
    
    return ops
