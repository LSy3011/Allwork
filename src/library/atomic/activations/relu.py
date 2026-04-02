import numpy as np
from typing import List
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS
from ir.mir.mir_op import MIROp

def get_relu_dfg() -> List[MIROp]:
    """
    AIACC ReLU 算子 (工业级精修版)
    逻辑与物理对位：FPADD -> LOGIC (SELECT_GT)
    """
    b = MIROpBuilder()
    
    # 1. 数据加载
    x = b.make_op("UR_READ", "x_rd", RT.UR_READ, [], "Load x", bank=0)
    z = b.make_op("UR_READ", "zero_rd", RT.UR_READ, [], "Zero Const", bank=1)
    
    # 2. 状态生成：FPADD 比较 (Update ST)
    # 🛸 规则字段：UPDATE_ST=1 (关键！)
    b.make_op("RTOVR", "r0_a1", PS.FPADD_S0, [x])
    b.make_op("RTOVR", "r1_a1", PS.FPADD_S1, [z])
    b.make_op("FPADD", "cmp_relu", RT.FPADD, ["r0_a1", "r1_a1"], 
              "Compare x with zero", UPDATE_ST=1, SUB=1)
    
    # 3. 逻辑选择：LOGIC SELECT_GT
    b.make_op("RTOVR", "r0_sel", PS.LOGIC_S0, [x])  # S0: 候选 A (x)
    b.make_op("RTOVR", "r1_sel", PS.LOGIC_S1, [z])  # S1: 候选 B (0)
    
    # 🛸 关键物理对齐：LOGIC 的 st 端口订阅 FPADD 的 ST 端子 (标志位)
    b.make_op("RTOVR", "st_sel", PS.LOGIC_ST, ["cmp_relu"], dep_port="ST")
    
    # 🛸 规则字段：OP_TYPE="SELECT_GT" (如果 GT 为 1, 则选 S0, 否则选 S1)
    # 🛸 精度字段：PRECISION="PREC_32" (对齐 logic.yaml 里的 encoding)
    b.make_op("LOGIC", "select_relu", RT.LOGIC, ["r0_sel", "r1_sel", "st_sel"], 
              "Perform ReLU Selection", OP_TYPE="SELECT_GT", PRECISION="PREC_32", VALID=1)
    
    # 4. 结果写回
    rt_fin = b.make_op("RTOVR", "rt_fin", PS.UR_W1_S, ["select_relu"])
    b.make_op("UR_WRITE", "w_fin", RT.UR_WRITE, [rt_fin], "ReLU Result", bank=7)
    
    return b.get_dfg()
