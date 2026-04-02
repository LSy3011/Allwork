from typing import List
from ir.mir.mir_op import MIROp
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS

def _get_logic_base_dfg(op_type: int, name: str, is_binary: bool = True) -> List[MIROp]:
    """LOGIC 单元基础 DFG 构造器"""
    b = MIROpBuilder()
    
    # 🛸 [Standard V4.0] 语义角色
    x = b.make_op("UR_READ", "x_rd", RT.UR_READ, [], "LOAD X", role="operand_a")
    
    deps = []
    r0 = b.make_op("RTOVR", "r0_log", PS.LOGIC_S0, [x])
    deps.append(r0)
    
    if is_binary:
        y = b.make_op("UR_READ", "y_rd", RT.UR_READ, [], "LOAD Y", role="operand_b")
        r1 = b.make_op("RTOVR", "r1_log", PS.LOGIC_S1, [y])
        deps.append(r1)
    
    # 🛸 规格对齐：使用 Master Spec 定义的 OP_TYPE
    res = b.make_op("LOGIC", f"res_{name}", RT.LOGIC, deps, OP_TYPE=op_type)
    
    # 🛸 [Standard V4.0] 输出锚定
    rt_fin = b.make_op("RTOVR", "rt_fin", PS.UR_W1_S, [res])
    b.make_op("UR_WRITE", "w_fin", RT.UR_WRITE, [rt_fin], 
              bank=7, is_output=True, meta_name=f"{name}_result")
    
    return b.get_dfg()

def get_and_dfg(): return _get_logic_base_dfg(0, "and")
def get_or_dfg():  return _get_logic_base_dfg(1, "or")
def get_xor_dfg(): return _get_logic_base_dfg(2, "xor")
def get_not_dfg(): return _get_logic_base_dfg(3, "not", is_binary=False)
