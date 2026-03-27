from typing import List
from ir.mir.mir_op import MIROp

def get_add_dfg() -> List[MIROp]:
    IQ_ADD = 101; S_ADD0, S_ADD1 = 5, 6; ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n
    m_op("UR_READ", "x", 200, [], b=0); m_op("UR_READ", "z", 201, [], b=1)
    m_op("RTOVR", "rx", S_ADD0, ["x"]); m_op("RTOVR", "rz", S_ADD1, ["z"])
    m_op("FPADD", "sum", 101, ["rx", "rz"]); m_op("UR_WRITE", "out", 204, ["sum"], b=7)
    return ops

def get_mul_dfg() -> List[MIROp]:
    IQ_MUL = 100; S_MUL0, S_MUL1 = 3, 4; ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n
    m_op("UR_READ", "x", 200, [], b=0); m_op("UR_READ", "z", 201, [], b=1)
    m_op("RTOVR", "rx", S_MUL0, ["x"]); m_op("RTOVR", "rz", S_MUL1, ["z"])
    m_op("FPMUL", "prod", 100, ["rx", "rz"]); m_op("UR_WRITE", "out", 204, ["prod"], b=7)
    return ops

def get_reciprocal_industrial_dfg() -> List[MIROp]:
    IQ_MUL, IQ_ADD = 100, 101; ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n
    m_op("UR_READ", "x", 200, [], b=0); m_op("UR_READ", "ry0", 201, [], b=1); m_op("UR_READ", "rc2", 202, [], b=2)
    m_op("FPMUL", "x_y0", IQ_MUL, ["x", "ry0"]); m_op("FPADD", "h", IQ_ADD, ["rc2", "x_y0"])
    m_op("FPMUL", "y_fin", IQ_MUL, ["ry0", "h"]); m_op("UR_WRITE", "out", 204, ["y_fin"], b=7)
    return ops

def get_div_industrial_dfg() -> List[MIROp]:
    ops = get_reciprocal_industrial_dfg(); rec_out = ops[-2].node_id
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.insert(-2, op); return n
    m_op("UR_READ", "ina", 200, [], b=3); m_op("FPMUL", "div_res", 100, ["ina", rec_out])
    ops[-2].metadata["depends_on"] = ["div_res"]
    return ops
