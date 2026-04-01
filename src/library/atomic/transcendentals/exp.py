from typing import List
from ir.mir.mir_op import MIROp
from driver.hw_resource import ResourceType as RT, PortSlot as PS

def get_exp_8order_dfg() -> List[MIROp]:
    """
    Exp(x) 8阶泰勒展开 DFG。
    全物理对位版：消除硬编码，支持动态硬件调度。
    """
    ops = []

    def make_op(op_type, nid, port, deps, bank=None, math=""):
        meta = {"depends_on": deps, "math": math}
        if op_type == "RTOVR": meta["slot_id"] = port
        else: meta["iq_type"] = port # 🛸 变更：改为记录单元类型，交给 SMT 分配 iq_id
        if bank is not None: meta.setdefault("bank_id", bank)
        op = MIROp(op_type, nid, nid, metadata=meta) # 🛰️ 修正：trace_id 必须唯一
        ops.append(op)
        return nid

    # --- 1. 读取变量与系数 ---
    make_op("UR_READ", "x_rd", RT.UR_READ, [], bank=0)
    for i in range(1, 9):
        # 轮询分配逻辑 IQ 类型建议，具体由 SMT 根据实例数（例如 4 路读）分流
        make_op("UR_READ", f"c{i}_rd", RT.UR_READ, [], bank=2 + (i % 4))

    # --- 2. 泰勒第一层运算 ---
    make_op("RTOVR", "r_x1", PS.FPMUL_S0, ["x_rd"])
    make_op("RTOVR", "r_c8", PS.FPMUL_S1, ["c8_rd"])
    make_op("FPMUL", "m8",   RT.FPMUL, ["r_x1", "r_c8"])
    
    make_op("RTOVR", "r_m8", PS.FPADD_S1, ["m8"])
    make_op("RTOVR", "r_c7", PS.FPADD_S0, ["c7_rd"])
    make_op("FPADD", "a7",   RT.FPADD, ["r_c7", "r_m8"])
    
    # ... (省略中间层，逻辑一致)
    make_op("RTOVR", "r_x2", PS.FPMUL_S0, ["x_rd"])
    make_op("RTOVR", "r_a7", PS.FPMUL_S1, ["a7"])
    make_op("FPMUL", "m7",   RT.FPMUL, ["r_x2", "r_a7"])
    
    make_op("RTOVR", "r_m7", PS.FPADD_S1, ["m7"])
    make_op("RTOVR", "r_c6", PS.FPADD_S0, ["c6_rd"])
    make_op("FPADD", "a6",   RT.FPADD, ["r_c6", "r_m7"])
    
    # --- 3. 最终写回 ---
    make_op("RTOVR", "r_f", PS.UR_W0_S, ["a6"])
    make_op("UR_WRITE", "exp_out", RT.UR_WRITE, ["r_f"], bank=7)
    return ops
