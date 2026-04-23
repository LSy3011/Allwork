import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from synthesis.ucode_solver import UCodeSynthesizer
from synthesis.encoder import SMIEncoderTruth
from api.hw_spec import HardwareSpec
from tests.helpers import make_op, MockSpec
from sim.test_complex_synthesis import print_pipeline_timeline

def test_horner_3rd_alignment():
    print("\n" + "="*20 + " 3rd-Order Horner Chain Alignment Test (with RTOVR) " + "="*20)
    
    # 1. 资源：充分的 RTOVR 实例
    spec = MockSpec(
        instances={"FPMUL": 1, "FPADD": 1, "UR_READ": 4, "UR_WRITE": 4, "RTOVR": 16},
        delays={"FPMUL": 4, "FPADD": 4, "UR_READ": 1, "UR_WRITE": 1, "RTOVR": 1}
    )
    
    # 2. 构造 DFG: 每阶独立读取 x 以避免跨迭代覆盖
    ops = [
        # Stage 1: c3*x1 + c2
        make_op("UR_READ", "rd_x1"),
        make_op("RTOVR",   "rt_x1", deps=["rd_x1"]),
        make_op("FPMUL",   "mul1",  deps=["rt_x1"]), 
        make_op("RTOVR",   "rt_m1", deps=["mul1"]),
        make_op("FPADD",   "add1",  deps=["rt_m1"]), 
        make_op("RTOVR",   "rt_a1", deps=["add1"]),
        
        # Stage 2: (ans1)*x2 + c1
        make_op("UR_READ", "rd_x2"),
        make_op("RTOVR",   "rt_x2", deps=["rd_x2"]),
        make_op("FPMUL",   "mul2",  deps=["rt_a1", "rt_x2"]), 
        make_op("RTOVR",   "rt_m2", deps=["mul2"]),
        make_op("FPADD",   "add2",  deps=["rt_m2"]), 
        make_op("RTOVR",   "rt_a2", deps=["add2"]),
        
        # Stage 3: (ans2)*x3 + c0
        make_op("UR_READ", "rd_x3"),
        make_op("RTOVR",   "rt_x3", deps=["rd_x3"]),
        make_op("FPMUL",   "mul3",  deps=["rt_a2", "rt_x3"]), 
        make_op("RTOVR",   "rt_m3", deps=["mul3"]),
        make_op("FPADD",   "add3",  deps=["rt_m3"]), 
        make_op("RTOVR",   "rt_a3", deps=["add3"]),
        
        make_op("UR_WRITE", "wr_y", deps=["rt_a3"])
    ]
    
    # 3. 调度
    synth = UCodeSynthesizer(spec)
    result = synth.solve_with_units(ops, MII=3, MAX_II=10)
    
    if not result:
        print("UNSAT: 资源或依赖冲突")
        return

    schedule = result["schedule"]
    unit_map = result["unit_assignments"]
    II = result["II"]

    # 4. 输出物理时序图
    print_pipeline_timeline(ops, schedule, II)
    
    # 5. 输出 TRUTH-ALIGNED MICRO-INSTRUCTION DUMP
    encoder = SMIEncoderTruth(ii=II)
    dump_str = encoder.dump_truth_aligned(ops, schedule, unit_map, loop_count=20, base_stride=1)
    print("\n" + dump_str)
    print("\n" + "="*80)

if __name__ == "__main__":
    test_horner_3rd_alignment()
