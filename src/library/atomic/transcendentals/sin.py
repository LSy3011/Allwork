from typing import List
from ir.mir.mir_op import MIROp
from driver.hw_resource import ResourceType as RT, PortSlot as PS

def get_sin_8order_dfg() -> List[MIROp]:
    """
    Exp(x) 8阶泰勒展开 DFG。
    全物理对位版：支持通过 SMT 自动分发到 FPMUL-0/1 或 FPADD-0/1。
    """
    ops = []

    def make_op(op_type, nid, iq_or_slot, deps, math="", bank=None, **meta_args):
        meta = {"depends_on": deps, "math": math}
        meta.update(meta_args)
        if op_type == "RTOVR":
            meta["slot_id"] = iq_or_slot
        else:
            meta["iq_type"] = iq_or_slot # 🛰️ 变更：标记资源类型而非固定 ID
        
        if bank is not None: meta["bank_id"] = bank
            
        op = MIROp(op_type, nid, nid, metadata=meta) # 🛰️ 修正：trace_id 必须唯一
        ops.append(op)
        return nid

    # --- 1. Step: y = x^2 ---
    make_op("UR_READ",  "x_rd",        RT.UR_READ, [], "Load x", bank=0)
    make_op("RTOVR",    "r0_m1",       PS.FPMUL_S0, ["x_rd"])
    make_op("RTOVR",    "r1_m1",       PS.FPMUL_S1, ["x_rd"])
    make_op("FPMUL",    "m1",          RT.FPMUL, ["r0_m1", "r1_m1"], "y=x*x", SRC_PREC="PREC_32", DST_PREC="PREC_32")
    make_op("RTOVR",    "rt_y_save",   PS.UR_W0_S, ["m1"])
    make_op("UR_WRITE", "w_y",         RT.UR_WRITE, ["rt_y_save"], bank=1)

    # --- 2. Step: a = y * c1 ---
    make_op("UR_READ",  "y_re1",       RT.UR_READ, ["w_y"], "y_re1", bank=1)
    make_op("UR_READ",  "c1_rd",       RT.UR_READ, [], "Load c1", bank=2)
    make_op("RTOVR",    "r0_m2",       PS.FPMUL_S0, ["y_re1"])
    make_op("RTOVR",    "r1_m2",       PS.FPMUL_S1, ["c1_rd"])
    make_op("FPMUL",    "m2",          RT.FPMUL, ["r0_m2", "r1_m2"], "a=y*c1", SRC_PREC="PREC_32", DST_PREC="PREC_32")
    
    # --- 3. Step: b = c2 - a ---
    make_op("RTOVR",    "rt_m2_a1",    PS.FPADD_S1, ["m2"])
    make_op("UR_READ",  "c2_rd",       RT.UR_READ, [], "Load c2", bank=3)
    make_op("RTOVR",    "r0_a1",       PS.FPADD_S0, ["c2_rd"])
    make_op("FPADD",    "a1",          RT.FPADD, ["r0_a1", "rt_m2_a1"], "b=c2-a", SUB="SUB", SRC_PREC="PREC_32", DST_PREC="PREC_32")

    # --- 4. Step: c = y * b ---
    make_op("UR_READ",  "y_re2",       RT.UR_READ, ["w_y"], "y_re2", bank=1)
    make_op("RTOVR",    "rt_a1_m3",    PS.FPMUL_S1, ["a1"]) 
    make_op("RTOVR",    "r0_m3",       PS.FPMUL_S0, ["y_re2"])
    make_op("FPMUL",    "m3",          RT.FPMUL, ["r0_m3", "rt_a1_m3"], "c=y*b", SRC_PREC="PREC_32", DST_PREC="PREC_32")

    # --- 5. Step: d = c3 - c ---
    make_op("RTOVR",    "rt_m3_a2",    PS.FPADD_S1, ["m3"])
    make_op("UR_READ",  "c3_rd",       RT.UR_READ, [], "Load c3", bank=4)
    make_op("RTOVR",    "r0_a2",       PS.FPADD_S0, ["c3_rd"])
    make_op("FPADD",    "a2",          RT.FPADD, ["r0_a2", "rt_m3_a2"], "d=c3-c", SUB="SUB", SRC_PREC="PREC_32", DST_PREC="PREC_32")

    # --- 6. Step: e = y * d ---
    make_op("UR_READ",  "y_re3",       RT.UR_READ, ["w_y"], "y_re3", bank=1)
    make_op("RTOVR",    "rt_a2_m4",    PS.FPMUL_S1, ["a2"])
    make_op("RTOVR",    "r0_m4",       PS.FPMUL_S0, ["y_re3"])
    make_op("FPMUL",    "m4",          RT.FPMUL, ["r0_m4", "rt_a2_m4"], "e=y*d", SRC_PREC="PREC_32", DST_PREC="PREC_32")

    # --- 7. Step: f = 1 - e ---
    make_op("RTOVR",    "rt_m4_a3",    PS.FPADD_S1, ["m4"])
    make_op("UR_READ",  "c4_rd",       RT.UR_READ, [], "Load c4", bank=5)
    make_op("RTOVR",    "r0_a3",       PS.FPADD_S0, ["c4_rd"])
    make_op("FPADD",    "a3",          RT.FPADD, ["r0_a3", "rt_m4_a3"], "f=1-e", SUB="SUB", SRC_PREC="PREC_32", DST_PREC="PREC_32")

    # --- 8. Step: sin = x * f ---
    make_op("UR_READ",  "x_f_rd",      RT.UR_READ, [], "Load x_fin", bank=0)
    make_op("RTOVR",    "r0_m5",       PS.FPMUL_S0, ["x_f_rd"])
    make_op("RTOVR",    "rt_a3_m5",    PS.FPMUL_S1, ["a3"])
    make_op("FPMUL",    "m5",          RT.FPMUL, ["r0_m5", "rt_a3_m5"], "sin=x*f", SRC_PREC="PREC_32", DST_PREC="PREC_32")

    # 写回
    make_op("RTOVR",    "rt_fin",      PS.UR_W1_S, ["m5"])
    make_op("UR_WRITE", "w_fin",       RT.UR_WRITE, ["rt_fin"], bank=7)

    return ops
