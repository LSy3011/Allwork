from typing import List
from ir.mir.mir_op import MIROp

def get_rsqrt_double_newton_dfg() -> List[MIROp]:
    """工业级 Rsqrt(x) 二次 Newton 迭代."""
    IQ_MUL, IQ_ADD = 100, 101
    S_MUL0, S_MUL1 = 3, 4
    S_ADD0, S_ADD1 = 5, 6
    S_WDATA = 38
    ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n

    m_op("UR_READ", "x", 200, [], b=0)
    m_op("UR_READ", "y0", 201, [], b=1)
    m_op("UR_READ", "c15", 202, [], b=2)
    m_op("UR_READ", "c05", 203, [], b=3)

    m_op("RTOVR", "rx0", S_MUL0, ["x"])
    m_op("RTOVR", "ry0_1", S_MUL1, ["y0"])
    m_op("FPMUL", "x_y0", IQ_MUL, ["rx0", "ry0_1"])
    m_op("RTOVR", "rxy0", S_MUL0, ["x_y0"])
    m_op("RTOVR", "ry0_2", S_MUL1, ["y0"])
    m_op("FPMUL", "x_y0_sq", IQ_MUL, ["rxy0", "ry0_2"])
    m_op("FPADD", "h1", IQ_ADD, ["c15", "x_y0_sq"])
    m_op("RTOVR", "rh1", S_MUL0, ["h1"])
    m_op("RTOVR", "ry0_3", S_MUL1, ["y0"])
    m_op("FPMUL", "y1", IQ_MUL, ["rh1", "ry0_3"])
    
    m_op("RTOVR", "rx1", S_MUL0, ["x"])
    m_op("RTOVR", "ry1_1", S_MUL1, ["y1"])
    m_op("FPMUL", "x_y1", IQ_MUL, ["rx1", "ry1_1"])
    m_op("RTOVR", "rxy1", S_MUL0, ["x_y1"])
    m_op("RTOVR", "ry1_2", S_MUL1, ["y1"])
    m_op("FPMUL", "x_y1_sq", IQ_MUL, ["rxy1", "ry1_2"])
    m_op("FPADD", "h2", IQ_ADD, ["c15", "x_y1_sq"])
    m_op("RTOVR", "rh2", S_MUL0, ["h2"])
    m_op("RTOVR", "ry1_3", S_MUL1, ["y1"])
    m_op("FPMUL", "y_fin", IQ_MUL, ["rh2", "ry1_3"])
    
    m_op("RTOVR", "rf", S_WDATA, ["y_fin"])
    m_op("UR_WRITE", "out", 204, ["rf"], b=7)
    return ops

def get_sqrt_industrial_dfg() -> List[MIROp]:
    """工业级 Sqrt(x) = x * Double-Newton-Rsqrt(x)"""
    IQ_MUL = 100
    S_MUL0, S_MUL1 = 3, 4
    S_WDATA = 38
    ops = get_rsqrt_double_newton_dfg()
    rsqrt_node = ops[-2].node_id
    
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.insert(-2, op); return n

    m_op("RTOVR", "rx_final", S_MUL0, ["x"])
    m_op("RTOVR", "rrsqrt", S_MUL1, [rsqrt_node])
    m_op("FPMUL", "sqrt_res", IQ_MUL, ["rx_final", "rrsqrt"])
    ops[-2].metadata["depends_on"] = ["sqrt_res"]
    return ops

def get_cbrt_industrial_dfg() -> List[MIROp]:
    """工业级 Cbrt(x) 立方根 Newton 迭代"""
    IQ_MUL, IQ_ADD = 100, 101
    S_MUL0, S_MUL1 = 3, 4
    ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n

    m_op("UR_READ", "x", 200, [], b=0)
    m_op("UR_READ", "y0", 201, [], b=1, math="Cbrt seed")
    m_op("RTOVR", "ry0", S_MUL0, ["y0"])
    m_op("FPMUL", "y2", IQ_MUL, ["ry0", "ry0"]) 
    m_op("RTOVR", "ry2", S_MUL1, ["y2"])
    m_op("FPMUL", "y3", IQ_MUL, ["ry0", "ry2"]) 
    m_op("RTOVR", "rf", 38, ["y3"])
    m_op("UR_WRITE", "out", 204, ["rf"], b=7)
    return ops
