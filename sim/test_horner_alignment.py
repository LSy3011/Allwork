import sys, os, json
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from synthesis.ucode_solver import UCodeSynthesizer
from api.hw_spec import HardwareSpec
from sim.tests.helpers import make_op
from sim.test_complex_synthesis import print_pipeline_timeline

# 🛸 [NEW] 导入物理驱动层组件
from driver.assembler import PhysicalAssembler, PhysicalInstruction
from driver.emitter import LIRPacker
from typing import List, Dict, Any
import numpy as np

# 🛸 [NEW] 导入数据生成器
ws_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ws_root, "tests", "operators"))
from utils.data_generator import ArithTestDataGenerator

class MockProgram:
    class MockAllocator:
        def __init__(self):
            # 初始化 8 个 Bank，每个 Bank 256 行，每行 4 个 Lane (SIMD)
            self.banks = {i: [np.zeros(4, dtype=np.float32) for _ in range(256)] for i in range(8)}
    def __init__(self):
        self.memory_allocator = self.MockAllocator()
        self.tensors = {}
        
    def inject_vector(self, logical_addr: int, data: np.ndarray):
        """注入矢量数据 (如 x)"""
        bank_id = logical_addr // 256
        offset = logical_addr % 256
        # 填充行数据
        num_rows = (len(data) + 3) // 4
        for r in range(num_rows):
            row_data = data[r*4 : (r+1)*4]
            # 如果不足 4 个，补齐
            if len(row_data) < 4:
                row_data = np.pad(row_data, (0, 4 - len(row_data)))
            self.memory_allocator.banks[bank_id][offset + r] = row_data

    def inject_broadcast(self, logical_addr: int, scalar: float):
        """将标量广播到整个 SIMD 行 (如系数 c)"""
        bank_id = logical_addr // 256
        offset = logical_addr % 256
        self.memory_allocator.banks[bank_id][offset] = np.full(4, scalar, dtype=np.float32)

def print_physical_instructions(instructions: List[PhysicalInstruction]):
    print("\n" + "="*40 + " SIMD-ALIGNED HORNER TAYOR DUMP (4 ROWS) " + "="*40)
    current_iq = None
    for instr in instructions:
        if instr.iq_name != current_iq:
            current_iq = instr.iq_name
            print(f"\n--- Unit: {current_iq} ---")
        h = instr.header
        h_str = f"VLD:{h.vld}|EMB:{h.embed}|E_END:{h.embed_end}|LOOP:{h.loops}|INC:{h.inc}"
        if instr.payload:
            p_items = []
            keys = sorted(instr.payload.keys())
            for k in ["SMC_ID", "VALID"]:
                if k in keys: keys.remove(k); keys.insert(0, k)
            for k in keys:
                v = instr.payload[k]
                if k.endswith("_PREC"):
                    label = "FP32" if v == 1 else "FP16"
                    p_items.append(f"{k}:{label}")
                elif v != 0 or k in ["VALID", "SMC_ID", "SEL", "ADDR", "BANK"]:
                    p_items.append(f"{k}:{v}")
            b_str = "|".join(p_items)
        else: b_str = "NOP"
        stage_label = f"[{instr.stage}]" if instr.stage != "BODY" else f"[{instr.local_id}]"
        print(f"{instr.iq_name:<14} {stage_label:<7} : HEAD:[{h_str}] | BODY:[{b_str}] /* Op: {instr.trace_id} */ ")

def test_horner_production_alignment():
    print("\n" + "="*20 + " SIMD 3rd-Order Horner Taylor: 4-Row Test " + "="*20)
    spec = HardwareSpec.load("hw_config")
    
    # 🛸 [NEW] 准备 16 个元素 (4 行 x 4 通道)
    n_rows = 4
    data_x = np.linspace(0.1, 1.6, n_rows * 4).astype(np.float32)
    coeffs = [-0.166666, 0.0, 1.0, 0.0]
    
    # 2. 构造 DFG
    ops = [
        make_op("UR_READ", "rd_x", logical_addr=0),
        make_op("RTOVR",   "rt_x1", deps=["rd_x"]),
        make_op("RTOVR",   "rt_x2", deps=["rd_x"]),
        make_op("RTOVR",   "rt_x3", deps=["rd_x"]),
        
        make_op("UR_READ", "rd_c3", logical_addr=256),
        make_op("RTOVR",   "rt_c3", deps=["rd_c3"]),
        make_op("UR_READ", "rd_c2", logical_addr=257),
        make_op("RTOVR",   "rt_c2", deps=["rd_c2"]),
        make_op("UR_READ", "rd_c1", logical_addr=258),
        make_op("RTOVR",   "rt_c1", deps=["rd_c1"]),
        make_op("UR_READ", "rd_c0", logical_addr=259),
        make_op("RTOVR",   "rt_c0", deps=["rd_c0"]),

        make_op("FPMUL",   "mul1",  deps=["rt_c3", "rt_x1"]), 
        make_op("RTOVR",   "rt_m1", deps=["mul1"]),
        make_op("FPADD",   "add1",  deps=["rt_m1", "rt_c2"]), 
        make_op("RTOVR",   "rt_a1", deps=["add1"]),
        
        make_op("FPMUL",   "mul2",  deps=["rt_a1", "rt_x2"]), 
        make_op("RTOVR",   "rt_m2", deps=["mul2"]),
        make_op("FPADD",   "add2",  deps=["rt_m2", "rt_c1"]), 
        make_op("RTOVR",   "rt_a2", deps=["add2"]),
        
        make_op("FPMUL",   "mul3",  deps=["rt_a2", "rt_x3"]), 
        make_op("RTOVR",   "rt_m3", deps=["mul3"]),
        make_op("FPADD",   "add3",  deps=["rt_m3", "rt_c0"]), 
        make_op("RTOVR",   "rt_a3", deps=["add3"]),
        
        make_op("UR_WRITE", "wr_y", deps=["rt_a3"], logical_addr=512)
    ]
    
    # 3. 调度与装配
    synth = UCodeSynthesizer(spec)
    result = synth.solve_with_units(ops, MII=5, MAX_II=15)
    if not result: return
    schedule, II, unit_map = result["schedule"], result["II"], result.get("unit_assignments", {})
    for op in ops:
        op.metadata["global_t"] = schedule[op.trace_id]
        op.metadata["unit_id"] = unit_map.get(op.trace_id)

    assembler = PhysicalAssembler(spec)
    # 🛸 [NEW] 设定循环次数为 4，硬件 Header 将自动生成 LOOP:3
    physical_instrs = assembler.assemble(ops, II=II, num_iterations=n_rows)

    # 4. 数据注入
    program = MockProgram()
    program.inject_vector(0, data_x)        # x 填入 Bank 0, 占 4 行
    program.inject_broadcast(256, coeffs[0]) 
    program.inject_broadcast(257, coeffs[1]) 
    program.inject_broadcast(258, coeffs[2]) 
    program.inject_broadcast(259, coeffs[3]) 
    
    # 5. 导出
    print_physical_instructions(physical_instrs)
    output_dir = "build/alignment/horner_3rd"
    packer = LIRPacker("hw_config")
    packer.emit_from_instructions(physical_instrs, output_dir, program=program)
    # 6. 导出调度对齐参考
    schedule_dump = {
        "II": II,
        "ops": {op.trace_id: {"t": int(op.metadata["global_t"]), "iq": op.metadata["unit_id"]} for op in ops}
    }
    with open(os.path.join(output_dir, "compiler_schedule.json"), "w") as f:
        json.dump(schedule_dump, f, indent=2)
    
    print(f"\n[DONE] 4-Row SIMD Test Completed. (Check bank0.hex for 4 lines of data)")

if __name__ == "__main__":
    test_horner_production_alignment()
