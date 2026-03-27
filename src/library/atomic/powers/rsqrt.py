from typing import List
from ir.mir.mir_op import MIROp

def get_rsqrt_newton_dfg() -> List[MIROp]:
    """
    Rsqrt(x) Newton-Raphson 1-iteration.
    y = 0.5 * y0 * (3.0 - x * y0^2)
    """
    IQ_MUL, IQ_ADD = 100, 101
    S_FPMUL0, S_FPMUL1 = 3, 4
    S_FPADD0, S_FPADD1 = 5, 6
    S_WDATA = 38
    ops = []

    def make_op(op_type, nid, port, deps, bank=None, math=""):
        meta = {"depends_on": deps, "math": math}
        if op_type == "RTOVR": meta["slot_id"] = port
        else: meta["iq_id"] = port
        if bank is not None: meta.setdefault("bank_id", bank)
        op = MIROp(op_type, 0, nid, metadata=meta)
        ops.append(op)
        return nid

    make_op("UR_READ",  "x_rd",     200, [], bank=0)
    make_op("UR_READ",  "y0_rd",    201, [], bank=1, math="Init guess")
    
    make_op("RTOVR",    "r_y0_0",   S_FPMUL0, ["y0_rd"])
    make_op("RTOVR",    "r_y0_1",   S_FPMUL1, ["y0_rd"])
    make_op("FPMUL",    "y0_sq",    IQ_MUL, ["r_y0_0", "r_y0_1"])
    
    make_op("RTOVR",    "r_x",      S_FPMUL0, ["x_rd"])
    make_op("RTOVR",    "r_y0sq",   S_FPMUL1, ["y0_sq"])
    make_op("FPMUL",    "x_y0sq",   IQ_MUL, ["r_x", "r_y0sq"])
    
    make_op("UR_READ",  "c3_rd",    202, [], bank=2, math="Const 3.0")
    make_op("RTOVR",    "r_c3",     S_FPADD0, ["c3_rd"])
    make_op("RTOVR",    "r_xy0sq",  S_FPADD1, ["x_y0sq"])
    make_op("FPADD",    "diff",     IQ_ADD, ["r_c3", "r_xy0sq"])
    
    make_op("UR_READ",  "c05_rd",   203, [], bank=3, math="Const 0.5")
    make_op("RTOVR",    "r_y0f",    S_FPMUL0, ["y0_rd"])
    make_op("RTOVR",    "r_diff",   S_FPMUL1, ["diff"])
    make_op("FPMUL",    "temp",     IQ_MUL, ["r_y0f", "r_diff"])
    
    make_op("RTOVR",    "r_temp",   S_FPMUL0, ["temp"])
    make_op("RTOVR",    "r_c05",    S_FPMUL1, ["c05_rd"])
    make_op("FPMUL",    "final_y",  IQ_MUL, ["r_temp", "r_c05"])
    
    make_op("RTOVR",    "r_f",      S_WDATA, ["final_y"])
    make_op("UR_WRITE", "out",      204, ["r_f"], bank=7)
    return ops
