from typing import List
from driver.assembler import PhysicalInstruction

def print_physical_instructions(instructions: List[PhysicalInstruction], show_model: bool = False):
    if not show_model: return
    print("\n" + "="*40 + " PHYSICAL INSTRUCTION DUMP (MODEL-DEBUG) " + "="*40)
    current_iq = None
    for instr in instructions:
        if instr.iq_name != current_iq:
            current_iq = instr.iq_name
            print(f"\n--- Unit: {current_iq} ---")
        h = instr.header
        h_str = f"VLD:{h.vld}|EMB:{h.level}|E_END:{h.embed_end}|LOOP:{h.loops}|INC:{h.inc}|INC_E:{h.inc_embed}"
        
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

