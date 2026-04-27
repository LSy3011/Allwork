import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from synthesis.ucode_solver import UCodeSynthesizer
from api.hw_spec import HardwareSpec
from tests.helpers import make_op
from sim.test_complex_synthesis import print_pipeline_timeline

# 🛸 [NEW] 导入物理驱动层组件
from driver.assembler import PhysicalAssembler
from driver.emitter import LIRPacker

def test_horner_3rd_alignment():
    print("\n" + "="*20 + " 3rd-Order Horner Chain Alignment Test (Physical Backend) " + "="*20)
    
    # 1. 资源：加载真实的硬件规格
    spec = HardwareSpec.load("hw_config")
    
    print("\n[SPEC CHECK] Resource Mapping Audit:")
    for res in ["FPMUL", "FPADD", "UR_READ", "UR_WRITE", "RTOVR"]:
        ids = spec.get_iq_ids(res)
        delay = spec.delays.get(res, "?")
        print(f"  - {res:<10} : IDs={ids}, Delay={delay}")
    
    # 2. 构造 DFG
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
    
    # 3. 调度 (SMT Solving)
    synth = UCodeSynthesizer(spec)
    result = synth.solve_with_units(ops, MII=3, MAX_II=10)
    
    if not result:
        print("UNSAT: 资源或依赖冲突")
        return

    schedule = result["schedule"]
    II = result["II"]

    # 🛸 [NEW] 4. 对接物理后端：元数据注入与动态数据规模 n
    n_elements = 2  # 验证 n=2 的极简场景
    for op in ops:
        op.metadata["global_t"] = schedule[op.trace_id]
        pass

    # 🛸 [NEW] 5. 物理装配 (Assembling)
    # 预测总延迟: L + (n-1)*II
    static_latency = max(schedule.values()) + 1 # 粗略估算 (未含最后一个单元的延迟)
    predicted_total = static_latency + (n_elements - 1) * II
    
    print(f"\n[BACKEND PREDICTION] Data Scale n={n_elements}, II={II}")
    print(f"  - Static Latency (L): ~{static_latency} cycles")
    print(f"  - Predicted Total: {predicted_total} cycles")

    assembler = PhysicalAssembler(spec)
    physical_instrs = assembler.assemble(ops, II=II, num_iterations=n_elements)

    # 🛸 [NEW] 6. 调试输出：解析字段确认
    packer = LIRPacker("hw_config")
    packer.dump_verbose(physical_instrs)
    
    # 7. 输出物理时序图 (Legacy Support)
    print_pipeline_timeline(ops, schedule, II)
    
    print("\n" + "="*80)

if __name__ == "__main__":
    test_horner_3rd_alignment()
