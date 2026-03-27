import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from library.atomic.sin import get_sin_8order_dfg

def get_char(name):
    if "FPMUL" in name: return "m"
    if "FPADD" in name: return "a"
    if "RTOVR" in name: return "r"
    if "UR_READ" in name: return "L"
    if "UR_WRITE" in name: return "S"
    return "x"

def run_full_report():
    spec = HardwareSpec.load("hw_config")
    synthesizer = SMTSynthesizer(spec)
    ops = get_sin_8order_dfg()
    result = synthesizer.solve(ops)
    
    if not result:
        print("❌ UNSAT")
        return

    print("\n" + "="*120)
    print("                AIACC V5.0 - SIN 8-ORDER TAYLOR COMPILER SPEC (PHYSICAL TRACE)")
    print("="*120)
    
    # 1. 数学公式与物理常量映射表
    print("\n[数学模型: Horner 展开]")
    print("Formula: sin = x * (1.0 - y * (c3 - y * (c2 - y * c1)))  where y = x^2")
    print("-" * 120)
    print(f"{'物理常量':<12} | {'数学含义':<15} | {'建议 Bank 地址':<15} | {'IQ 路由编号'}")
    print(f"{'c1':<12} | {'1/5040':<15} | {'Bank 0, Addr 10':<15} | {'IQ 201'}")
    print(f"{'c2':<12} | {'1/120':<15} | {'Bank 1, Addr 11':<15} | {'IQ 202'}")
    print(f"{'c3':<12} | {'1/6':<15} | {'Bank 2, Addr 12':<15} | {'IQ 203'}")
    print(f"{'c4':<12} | {'1.0':<15} | {'Bank 3, Addr 13':<15} | {'IQ 201 (Reuse)'}")
    print(f"{'x':<12} | {'输入向量':<15} | {'Bank 0, Addr 0':<15} | {'IQ 200'}")
    print("-" * 120)

    # 2. Timeline 波形 (Slot 分层)
    max_t = max(result.values())
    row_defs = []
    row_defs.append(("FPADD", [o for o in ops if o.op_type == "FPADD"]))
    row_defs.append(("FPMUL", [o for o in ops if o.op_type == "FPMUL"]))
    used_slots = sorted(list(set(o.metadata.get("slot_id") for o in ops if o.op_type == "RTOVR")))
    for s in used_slots:
        row_defs.append((f"RTOVR-{s:<5}", [o for o in ops if o.op_type == "RTOVR" and o.metadata.get("slot_id") == s]))
    ur_r_iqs = sorted(list(set(o.metadata.get("iq_id") for o in ops if o.op_type == "UR_READ")))
    for iq in ur_r_iqs:
        row_defs.append((f"UR-R (IQ{iq})", [o for o in ops if o.metadata.get("iq_id") == iq and o.op_type == "UR_READ"]))
    ur_w_iqs = sorted(list(set(o.metadata.get("iq_id") for o in ops if o.op_type == "UR_WRITE")))
    for iq in ur_w_iqs:
        row_defs.append((f"UR-W (IQ{iq})", [o for o in ops if o.metadata.get("iq_id") == iq and o.op_type == "UR_WRITE"]))

    header = "iq_name         |0"
    for t in range(1, max_t + 5):
        if t % 10 == 0: header += str(t // 10 % 10)
        else: header += "  ."
    print(header)
    for name, o_list in row_defs:
        line = [" . "] * (max_t + 5)
        for o in o_list: line[result[o.node_id]] = f" {get_char(o.op_type)} "
        print(f"{name:<15} |" + "".join(line))
    
    # 3. 详细执行轨迹跟踪 (数学步长对齐)
    print("\n" + "-"*120)
    print(f"{'T (拍数)':<10} | {'物理动作':<15} | {'端子/槽位':<12} | {'数学语义 (公式对应)'}")
    print("-" * 120)
    for t in range(max_t + 1):
        t_ops = [o for o in ops if result[o.node_id] == t]
        for op in t_ops:
            slot = op.metadata.get("slot_id", op.metadata.get("iq_id", "N/A"))
            math = op.metadata.get("math", "Pulse")
            print(f"{t:<10} | {op.op_type:<15} | {str(slot):<12} | {math}")
    print("="*120)

if __name__ == "__main__":
    run_full_report()
