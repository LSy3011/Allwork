import sys
import numpy as np
from pathlib import Path

# 设置搜索路径以引用 src 目录
ws = Path.cwd()
sys.path.append(str(ws / "src"))

from tools.standard_pipeline_runner import StandardPipelineRunner
from ir.mir.builder import MIROpBuilder

from driver.hw_resource import ResourceType as RT, PortSlot as PS

def get_fpadd_logic_dfg():
    """
    构造测试 DFG: y = (x + 1.0) > 0.0
    验证：FPADD (src) -> LOGIC (dest) 的物理链路与时序。
    """
    b = MIROpBuilder()
    
    # 1. 输入数据 x (通过 UR_READ 加载)
    x_in = b.make_op("UR_READ", "x_in", RT.UR_READ, [], role="operand_a")
    c_one = b.make_op("UR_READ", "c_one", RT.UR_READ, [], is_const=True, value=1.0, role="constant")
    c_zero = b.make_op("UR_READ", "c_zero", RT.UR_READ, [], is_const=True, value=0.0, role="constant")
    
    # 2. 运算: p1 = x + 1.0 (FPADD) 且【必须】设置 UPDATE_ST=1
    p1 = b.make_op("FPADD", "p1", RT.FPADD, [
        b.make_op("RTOVR", "p1_a0", PS.FPADD_S0, [x_in]),
        b.make_op("RTOVR", "p1_a1", PS.FPADD_S1, [c_one])
    ], UPDATE_ST=1)
    
    # 3. 运算: y = SELECT_GT(true_val, false_val)
    # 🛸 [物理对齐]：SELECT_GT 会检查 ST 寄存器。
    # 如果 ST 位显示结果为正 (GT)，则输出 S0 (1.0)，否则输出 S1 (0.0)
    res = b.make_op("LOGIC", "res", RT.LOGIC, [
        b.make_op("RTOVR", "res_l0", PS.LOGIC_S0, [c_one]),
        b.make_op("RTOVR", "res_l1", PS.LOGIC_S1, [c_zero]),
        b.make_op("RTOVR", "res_lst", PS.LOGIC_ST, [p1], dep_port="ST")
    ], OP_TYPE="SELECT_GT")
    
    # --- [OUTPUT] ---
    b.make_op("UR_WRITE", "out", RT.UR_WRITE, [
        b.make_op("RTOVR", "fout", PS.UR_W1_S, [res])
    ], role="output", meta_name="y")
    
    dfg = b.get_dfg()
    print(f"\n[DEBUG] Generated DFG with {len(dfg)} nodes.")
    for n in dfg:
        # 🛸 对位补丁：为 FPADD 和 LOGIC 强制锁定 32 位精度
        if n.op_type in ["FPADD", "LOGIC"]:
            n.metadata["PRECISION"] = "PREC_32"
        print(f"  - Node:{n.trace_id} ({n.op_type}) Metadata:{n.metadata.get('iq_id', 'None')}")
    return dfg

def test_fpadd_logic_pipeline():
    """
    AIACC FPADD-LOGIC 联动回归测试 (V6.0)。
    验证：时空排布是否支持多单元级联。
    """
    runner = StandardPipelineRunner(hw_config="hw_config", build_root="build/standard")

    # 1. 准备数据场景 (x = 2.0, 物理语义对齐：FPADD(x, 1.0) 且 ST 更新)
    # 因为 2.0 > 1.0, 所以 GT=1, LOGIC SELECT_GT 将选择 S0 (1.0)
    test_x = 2.0
    input_data = {"x": test_x}
    # 🛸 [物理对齐]：在此处的 hardware 契约下，ST 位是 S0 与 S1 的比较结果
    golden_y = 1.0 if test_x > 1.0 else 0.0

    # 2. 启动 5-Stage 标准化生产线
    success = runner.run_full_pipeline(
        op_name="fpadd_logic_cascade",
        get_dfg_fn=get_fpadd_logic_dfg,
        input_data=input_data,
        golden_y=np.array([golden_y] * 4) # 广播对齐矢量宽度
    )

    if success:
        print("\n✅ FPADD-LOGIC Cascade Verification: PASSED")
    else:
        print("\n❌ FPADD-LOGIC Cascade Verification: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    test_fpadd_logic_pipeline()
