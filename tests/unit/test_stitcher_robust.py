import unittest
import copy
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from library.atomic.sin import get_sin_8order_dfg
from orchestrator.stitcher import HSTSStitcher

def render_atomic_trace(final_stream):
    max_t = max(op.metadata["global_t"] for op in final_stream)
    combined_len = max_t + 5
    
    # 动态发现所有被占用的 Slot
    active_slots = sorted(list(set(op.metadata.get("slot_id") for op in final_stream if op.metadata.get("slot_id") is not None)))
    
    print("\n" + "="*160)
    print("                AIACC V5.0 HSTS ATOMIC TRACE: TRIPLE SIN PIPELINE (BIT-PERFECT REVIEW)")
    print("="*160)
    print("Symbols: [A]=Sin_A, [B]=Sin_B, [C]=Sin_C  |  Format: [Inst][LogicID]")
    print("-" * 160)
    
    # 定义必须复核的物理资源行
    row_defs = [
        ("FPMUL IQ-100",  "FPMUL",    100),
        ("FPADD IQ-101",  "FPADD",    101),
        ("UR-R IQ-200",   "UR_READ",  200),
        ("UR-R IQ-201",   "UR_READ",  201),
        ("UR-R IQ-202",   "UR_READ",  202),
        ("UR-R IQ-203",   "UR_READ",  203),
        ("UR-W IQ-204",   "UR_WRITE", 204),
    ]
    # 动态加入所有 Slot
    for sid in active_slots:
        row_defs.append((f"Slot-{sid:<5} (RT)", "RTOVR", sid))

    header = "Resource ID     |"
    for t in range(combined_len):
        if t % 5 == 0: header += f"{t:<5}"
    print(header)
    
    tag_map = {"sin_A": "A", "sin_B": "B", "sin_C": "C"}
    
    for row_name, op_type, res_id in row_defs:
        line = [" .   "] * combined_len
        for t in range(combined_len):
            occupants = [op for op in final_stream if op.metadata["global_t"] == t and op.op_type == op_type]
            
            valid = []
            for op in occupants:
                if op.op_type == "RTOVR":
                   if op.metadata.get("slot_id") == res_id: valid.append(op)
                else:
                   if op.metadata.get("iq_id") == res_id: valid.append(op)
            
            if len(valid) > 1: 
                line[t] = "[#]  " # 冲突
            elif len(valid) == 1:
                op = valid[0]
                inst = tag_map.get(op.metadata["instance_name"], "X")
                # 提取逻辑节点名称，去除 trace_ 前缀
                nid = op.node_id.replace("trace_", "")
                # 进一步缩写方便显示
                nid_abbr = nid[:3]
                line[t] = f"{inst}{nid_abbr:<3} "
                
        print(f"{row_name:<15} |" + "".join(line))
    print("-" * 160)

class TestRobustStitcher(unittest.TestCase):
    def test_triple_sin_stitching(self):
        spec = HardwareSpec.load("hw_config")
        synthesizer = SMTSynthesizer(spec)
        stitcher = HSTSStitcher()
        
        ops = get_sin_8order_dfg()
        smt_res = synthesizer.solve(ops, mode="extreme")
        self.assertIsNotNone(smt_res)
        
        stitcher.add_operator("sin_A", ops, smt_res)
        stitcher.add_operator("sin_B", ops, smt_res)
        stitcher.add_operator("sin_C", ops, smt_res)
        
        final_stream = stitcher.get_final_stream()
        render_atomic_trace(final_stream)

if __name__ == "__main__":
    unittest.main()
