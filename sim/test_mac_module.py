import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from api.hw_spec import HardwareSpec
from ir.mir.mir_op import MIROp
from synthesis.backend import SMTSynthesizer
from core.ii_analyzer import IIAnalyzer


def main():
    hw_config_dir = os.path.join(os.path.dirname(__file__), '..', 'hw_config')
    print(f"Loading hardware config from: {hw_config_dir}")
    spec = HardwareSpec.load(hw_config_dir)

    # 构造 a * b + c 的 DFG
    ops = [
        MIROp(op_type="UR_READ",  logic_cycle=0, trace_id="read_a",  metadata={}),
        MIROp(op_type="UR_READ",  logic_cycle=0, trace_id="read_b",  metadata={}),
        MIROp(op_type="UR_READ",  logic_cycle=0, trace_id="read_c",  metadata={}),
        MIROp(op_type="RTOVR",    logic_cycle=1, trace_id="rt_a",    metadata={"depends_on": ["read_a"]}),
        MIROp(op_type="RTOVR",    logic_cycle=1, trace_id="rt_b",    metadata={"depends_on": ["read_b"]}),
        MIROp(op_type="RTOVR",    logic_cycle=1, trace_id="rt_c",    metadata={"depends_on": ["read_c"]}),
        MIROp(op_type="FPMUL",    logic_cycle=2, trace_id="mul_ab",  metadata={"depends_on": ["rt_a", "rt_b"]}),
        MIROp(op_type="RTOVR",    logic_cycle=3, trace_id="rt_mul",  metadata={"depends_on": ["mul_ab"]}),
        MIROp(op_type="FPADD",    logic_cycle=4, trace_id="add_c",   metadata={"depends_on": ["rt_mul", "rt_c"]}),
        MIROp(op_type="RTOVR",    logic_cycle=5, trace_id="rt_add",  metadata={"depends_on": ["add_c"]}),
        MIROp(op_type="UR_WRITE", logic_cycle=6, trace_id="write_out", metadata={"depends_on": ["rt_add"]}),
    ]

    synth = SMTSynthesizer(spec)

    # 静态分析理论 MII
    analyzer = IIAnalyzer(spec)
    mii = analyzer.compute(ops)

    print("\n" + "=" * 60)
    print(f"【模调度】MII={mii}，MAX_II=20  (串行自动兼容)")
    print("=" * 60)

    result = synth.solve(ops, MII=mii, MAX_II=20)

    if result:
        schedule = result["schedule"]
        ii = result["II"]
        print(f"\n调度成功！实际 II = {ii}")
        print("各算子发射时间：")
        for op_id, cycle in sorted(schedule.items(), key=lambda x: x[1]):
            print(f"  {op_id:15s}: T = {cycle}")
    else:
        print("调度失败 (全范围 UNSAT)。")


if __name__ == "__main__":
    main()
