from typing import List
from ir.mir.mir_op import MIROp

def get_cos_8order_dfg() -> List[MIROp]:
    """
    Cos(x) 8阶泰勒展开 DFG。
    结构：1 - x^2 * (c1 - x^2 * (c2 - x^2 * (c3 - x^2 * c4)))
    """
    IQ_MUL, IQ_ADD = 100, 101
    SLOT_FPMUL_S0, SLOT_FPMUL_S1 = 3, 4
    SLOT_FPADD_S0, SLOT_FPADD_S1 = 5, 6
    SLOT_WDATA_3, SLOT_WDATA_2 = 38, 37

    ops = []

    def make_op(op_type, nid, iq_or_slot, deps, math="", bank=None):
        meta = {"depends_on": deps, "math": math}
        if op_type == "RTOVR": meta["slot_id"] = iq_or_slot
        else: meta["iq_id"] = iq_or_slot
        if bank is not None: meta["bank_id"] = bank
        op = MIROp(op_type, 0, f"trace_{nid}", metadata=meta)
        op.node_id = nid
        ops.append(op)
        return nid

    # 1. y = x^2
    make_op("UR_READ",  "x_rd",        200, [], "Load x", bank=0)
    make_op("RTOVR",    "r0_m1",       SLOT_FPMUL_S0, ["x_rd"])
    make_op("RTOVR",    "r1_m1",       SLOT_FPMUL_S1, ["x_rd"])
    make_op("FPMUL",    "m1",          IQ_MUL, ["r0_m1", "r1_m1"], "y=x*x")
    make_op("RTOVR",    "rt_y_save",   SLOT_WDATA_3, ["m1"])
    make_op("UR_WRITE", "w_y",         204, ["rt_y_save"], bank=1)

    # 2. 中间链逻辑与 Sin 类似 (嵌套乘加)
    make_op("UR_READ",  "y_re1",       200, ["w_y"], "y_re1", bank=1)
    make_op("UR_READ",  "c4_rd",       201, [], "Load c4", bank=5)
    make_op("RTOVR",    "r0_m2",       SLOT_FPMUL_S0, ["y_re1"])
    make_op("RTOVR",    "r1_m2",       SLOT_FPMUL_S1, ["c4_rd"])
    make_op("FPMUL",    "m2",          IQ_MUL, ["r0_m2", "r1_m2"], "a=y*c4")
    
    make_op("RTOVR",    "rt_m2_a1",    SLOT_FPADD_S1, ["m2"])
    make_op("UR_READ",  "c3_rd",       202, [], "Load c3", bank=4)
    make_op("RTOVR",    "r0_a1",       SLOT_FPADD_S0, ["c3_rd"])
    make_op("FPADD",    "a1",          IQ_ADD, ["r0_a1", "rt_m2_a1"], "b=c3-a")

    make_op("UR_READ",  "y_re2",       201, ["w_y"], "y_re2", bank=1)
    make_op("RTOVR",    "rt_a1_m3",    SLOT_FPMUL_S1, ["a1"]) 
    make_op("RTOVR",    "r0_m3",       SLOT_FPMUL_S0, ["y_re2"])
    make_op("FPMUL",    "m3",          IQ_MUL, ["r0_m3", "rt_a1_m3"], "c=y*b")

    make_op("RTOVR",    "rt_m3_a2",    SLOT_FPADD_S1, ["m3"])
    make_op("UR_READ",  "c2_rd",       203, [], "Load c2", bank=3)
    make_op("RTOVR",    "r0_a2",       SLOT_FPADD_S0, ["c2_rd"])
    make_op("FPADD",    "a2",          IQ_ADD, ["r0_a2", "rt_m3_a2"], "d=c2-c")

    make_op("UR_READ",  "y_re3",       202, ["w_y"], "y_re3", bank=1)
    make_op("RTOVR",    "rt_a2_m4",    SLOT_FPMUL_S1, ["a2"])
    make_op("RTOVR",    "r0_m4",       SLOT_FPMUL_S0, ["y_re3"])
    make_op("FPMUL",    "m4",          IQ_MUL, ["r0_m4", "rt_a2_m4"], "e=y*d")

    # 3. Final Cos: 1 - e
    make_op("RTOVR",    "rt_m4_a3",    SLOT_FPADD_S1, ["m4"])
    make_op("UR_READ",  "c1_rd",       201, [], "Load 1", bank=2)
    make_op("RTOVR",    "r0_a3",       SLOT_FPADD_S0, ["c1_rd"])
    make_op("FPADD",    "a3",          IQ_ADD, ["r0_a3", "rt_m4_a3"], "cos_out")

    # 写回
    make_op("RTOVR",    "rt_fin",      SLOT_WDATA_2, ["a3"])
    make_op("UR_WRITE", "w_fin",       204, ["rt_fin"], bank=7)

    return ops
