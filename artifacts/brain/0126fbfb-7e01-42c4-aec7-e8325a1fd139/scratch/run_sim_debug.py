import sys
import os
import json

# Setup paths
ws_root = os.getcwd()
sys.path.append(os.path.join(ws_root, "src"))
sys.path.append(os.path.join(ws_root, "sim"))

from api.hw_spec import HardwareSpec
from smc_simulator.core.decoder import InstructionDecoder
from smc_simulator.control.iq_unit import IQUnit

def run_debug_sim(build_dir):
    config_path = os.path.join(build_dir, "sim_config.json")
    with open(config_path, "r") as f:
        config_data = json.load(f)
    
    iq_init = config_data.get("iq_init", {})
    spec = HardwareSpec.load("hw_config")
    
    units = {}
    for iq_name_orig, hex_rel_path in iq_init.items():
        # Determine the physical module type for spec lookup
        unit_type = None
        upper_name = iq_name_orig.upper()
        if "UR-R-" in upper_name:
            unit_type = "UR_READ"
        elif "UR-W-" in upper_name:
            unit_type = "UR_WRITE"
        elif "RTOVR" in upper_name:
            unit_type = "RTOVR"
        else:
            unit_type = upper_name
            
        u_spec = spec.modules.get(unit_type)
        if not u_spec:
             # Scans
             for m_name in spec.modules.keys():
                 if m_name in upper_name:
                     u_spec = spec.modules[m_name]
                     break
        
        if not u_spec:
            print(f"[WARN] Unknown module for IQ: {iq_name_orig} (guessed type: {unit_type})")
            continue
            
        decoder = InstructionDecoder(u_spec, unit_name=iq_name_orig, system_spec=spec.config)
        iq = IQUnit(iq_name_orig, decoder=decoder)
        
        hex_full_path = os.path.join(build_dir, hex_rel_path)
        iq.load_hex(hex_full_path)
        units[iq_name_orig] = iq

    print("\n" + "="*20 + " STARTING FIELD ANALYSIS SIMULATION " + "="*20)
    for cycle in range(50):
        print(f"\n>>> Cycle {cycle} <<<")
        for name, iq in units.items():
            if not iq.terminated:
                iq.evaluate(cycle=cycle)
        
        for name, iq in units.items():
            if iq.is_valid():
                 curr_instr = iq.get_current_instruction()
                 # Trigger prints
                 iq.decoder.decode(curr_instr)
                 
        for name, iq in units.items():
            if not iq.terminated:
                iq.tick()
        
        if all(iq.terminated for iq in units.values()):
            print("All units terminated.")
            break

if __name__ == "__main__":
    run_debug_sim("build/alignment/horner_3rd")
