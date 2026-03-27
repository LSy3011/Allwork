from typing import List
from ir.mir.mir_op import MIROp

def get_exp_8order_dfg() -> List[MIROp]:
    """
    Exp(x) 8阶泰勒展开 DFG。
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

    make_op("UR_READ", "x_rd", 200, [], bank=0)
    for i in range(1, 9):
        make_op("UR_READ", f"c{i}_rd", 200 + (i % 4), [], bank=2 + (i % 4))

    make_op("RTOVR", "r_x1", S_FPMUL0, ["x_rd"])
    make_op("RTOVR", "r_c8", S_FPMUL1, ["c8_rd"])
    make_op("FPMUL", "m8",   IQ_MUL, ["r_x1", "r_c8"])
    
    make_op("RTOVR", "r_m8", S_FPADD1, ["m8"])
    make_op("RTOVR", "r_c7", S_FPADD0, ["c7_rd"])
    make_op("FPADD", "a7",   IQ_ADD, ["r_c7", "r_m8"])
    
    make_op("RTOVR", "r_a7", S_FPMUL1, ["a7"])
    make_op("RTOVR", "r_x2", S_FPMUL0, ["x_rd"])
    make_op("FPMUL", "m7",   IQ_MUL, ["r_x2", "r_a7"])
    
    make_op("RTOVR", "r_m7", S_FPADD1, ["m7"])
    make_op("RTOVR", "r_c6", S_FPADD0, ["c6_rd"])
    make_op("FPADD", "a6",   IQ_ADD, ["r_c6", "r_m7"])
    
    make_op("RTOVR", "r_f", S_WDATA, ["a6"])
    make_op("UR_WRITE", "exp_out", 204, ["r_f"], bank=7)
    return ops
