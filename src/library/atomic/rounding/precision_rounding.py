from typing import List
from ir.mir.mir_op import MIROp

def get_floor_industrial_dfg() -> List[MIROp]:
    IQ_CVT = 105
    S_CVT0 = 20
    ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n

    m_op("UR_READ", "x", 200, [], b=0)
    m_op("RTOVR", "rx_f32", S_CVT0, ["x"])
    m_op("FPCVT", "i32_val", IQ_CVT, ["rx_f32"], math="F32_TO_I32")
    m_op("RTOVR", "ri32", S_CVT0, ["i32_val"])
    m_op("FPCVT", "floor_res", IQ_CVT, ["ri32"], math="I32_TO_F32")
    m_op("UR_WRITE", "out", 204, ["floor_res"], b=7)
    return ops

def get_ceil_industrial_dfg() -> List[MIROp]:
    IQ_ADD, IQ_CVT = 101, 105
    ops = []
    def m_op(t, n, p, d, b=None, math=""):
        meta = {"depends_on": d, "math": math}
        if t == "RTOVR": meta["slot_id"] = p
        else: meta["iq_id"] = p
        if b is not None: meta["bank_id"] = b
        op = MIROp(t, 0, n, metadata=meta); ops.append(op); return n

    m_op("UR_READ", "x", 200, [], b=0)
    m_op("UR_READ", "bias", 201, [], b=1, math="0.999999")
    m_op("FPADD", "sum", IQ_ADD, ["x", "bias"])
    m_op("FPCVT", "i32_val", IQ_CVT, ["sum"], math="F32_TO_I32")
    m_op("FPCVT", "ceil_res", IQ_CVT, ["i32_val"], math="I32_TO_F32")
    m_op("UR_WRITE", "out", 204, ["ceil_res"], b=7)
    return ops
