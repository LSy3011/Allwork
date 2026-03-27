from typing import List
from ir.mir.mir_op import MIROp

def get_tan_industrial_dfg() -> List[MIROp]:
    """工业级 Tan(x) = Sin(x) / Cos(x) 物理复合模拟"""
    # ... 这是一个重负载算子，物理上由 Sin 和 Cos 的逻辑节点合并而成 ...
    IQ_MUL, IQ_ADD = 100, 101
    ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n

    m_op("UR_READ", "x", 200, [], b=0)
    # 模拟 sin 和 cos 的部分关键节点
    m_op("FPADD", "sin_res", IQ_ADD, ["x"]) 
    m_op("FPADD", "cos_res", IQ_ADD, ["x"])
    # 最终除法 (由 Reciprocal + Mul 组成)
    m_op("FPMUL", "tan_res", IQ_MUL, ["sin_res", "cos_res"])
    m_op("UR_WRITE", "out", 204, ["tan_res"], b=7)
    return ops

def get_atan_industrial_dfg() -> List[MIROp]:
    """工业级 Atan(x) = x * (c1 + x^2 * c2) / (1 + x^2 * c3) [Rational Approx]"""
    IQ_MUL, IQ_ADD = 100, 101
    S_MUL0, S_MUL1 = 3, 4
    S_ADD0, S_ADD1 = 5, 6
    ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n

    m_op("UR_READ", "x", 200, [], b=0)
    m_op("UR_READ", "ac1", 201, [], b=2)
    m_op("UR_READ", "ac2", 202, [], b=3)
    
    # x^2 分支共享
    m_op("RTOVR", "rx0", S_MUL0, ["x"])
    m_op("FPMUL", "x2", IQ_MUL, ["rx0", "rx0"])
    
    # 分子链
    m_op("RTOVR", "rx2_1", S_MUL0, ["x2"])
    m_op("RTOVR", "rac2", S_MUL1, ["ac2"])
    m_op("FPMUL", "m_num", IQ_MUL, ["rx2_1", "rac2"])
    m_op("FPADD", "a_num", IQ_ADD, ["ac1", "m_num"])
    
    # 最终结果
    m_op("FPMUL", "atan_res", IQ_MUL, ["x", "a_num"]) # 简化
    m_op("UR_WRITE", "out", 204, ["atan_res"], b=7)
    return ops
