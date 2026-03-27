from typing import List
from ir.mir.mir_op import MIROp

# 复用 Gelu, Softplus...
def get_gelu_industrial_dfg() -> List[MIROp]:
    IQ_MUL, IQ_ADD = 100, 101; ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta={"depends_on": d, "math": math}
        if t=="RTOVR": meta["slot_id"]=p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n
    m_op("UR_READ", "x", 200, [], b=0)
    m_op("FPMUL", "x3", IQ_MUL, ["x", "x"]); m_op("FPMUL", "tanh_out", IQ_MUL, ["x3"])
    m_op("FPMUL", "res", IQ_MUL, ["x", "tanh_out"]); m_op("UR_WRITE", "out", 204, ["res"], b=7)
    return ops

def get_mish_industrial_dfg() -> List[MIROp]:
    """工业级 Mish(x) = x * Tanh(Softplus(x))"""
    # 物理深度之王
    IQ_MUL = 100
    ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta={"depends_on": d, "math": math}
        if t=="RTOVR": meta["slot_id"]=p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n

    m_op("UR_READ", "x", 200, [], b=0)
    # Mish = x * tanh(ln(1+exp(x)))
    m_op("FPMUL", "exp_x", IQ_MUL, ["x"], math="exp poly")
    m_op("FPMUL", "sp_ln", IQ_MUL, ["exp_x"], math="softplus ln poly")
    m_op("FPMUL", "t_out", IQ_MUL, ["sp_ln"], math="tanh poly")
    m_op("FPMUL", "mish_res", IQ_MUL, ["x", "t_out"])
    
    m_op("UR_WRITE", "out", 204, ["mish_res"], b=7)
    return ops

def get_elu_industrial_dfg() -> List[MIROp]:
    """工业级 Elu(x) = alpha * (exp(x) - 1) if x < 0 else x"""
    IQ_MUL, IQ_ADD, IQ_SEL = 100, 101, 103
    ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta={"depends_on": d, "math": math}
        if t=="RTOVR": meta["slot_id"]=p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n
    m_op("UR_READ", "x", 200, [], b=0)
    m_op("UR_READ", "alpha", 201, [], b=2)
    m_op("FPMUL", "exp_x", IQ_MUL, ["x"], math="exp poly")
    m_op("FPADD", "em1", IQ_ADD, ["exp_x"], math="exp-1")
    m_op("FPMUL", "elu_neg", IQ_MUL, ["alpha", "em1"])
    m_op("FPSEL", "elu_res", IQ_SEL, ["x", "elu_neg", "x"], math="x < 0 check")
    m_op("UR_WRITE", "out", 204, ["elu_res"], b=7)
    return ops
