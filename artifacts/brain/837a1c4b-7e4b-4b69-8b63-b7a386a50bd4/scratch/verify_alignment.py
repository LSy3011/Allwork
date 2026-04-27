import sys, os
sys.path.append(os.path.join(os.getcwd(), 'src'))
sys.path.append(os.path.join(os.getcwd(), 'sim'))

from api.hw_spec import HardwareSpec
from sim.smc_simulator.core.decoder import InstructionDecoder
from driver.packer import ISA_V5_HEADER, BitPacker, ISA_V5_FULL_SPEC

def verify_alignment():
    print("=== [ALIGNMENT VERIFICATION] ===")
    spec = HardwareSpec.load("hw_config")
    
    # Check simulator's header order
    dummy_decoder = InstructionDecoder(spec.modules["UR_READ"])
    print("\n[SIMULATOR HEADER ORDER]")
    for name, width in dummy_decoder._header_layout:
        print(f"  - {name}: {width}")
        
    print("\n[PACKER HEADER ORDER]")
    for name, (start, width) in ISA_V5_HEADER.items():
        print(f"  - {name}: {width}")
    
    for unit_name in ISA_V5_FULL_SPEC.keys():
        print(f"\n" + "="*40)
        print(f"Checking Unit: {unit_name}")
        print("="*40)
        
        if unit_name not in spec.modules:
            print(f"SKIPPING {unit_name}: Not found in hardware spec.")
            continue
            
        unit_mod_spec = spec.modules[unit_name]
        decoder = InstructionDecoder(unit_mod_spec)
        sim_layout = decoder._layout
        payload_width = decoder.payload_width
        
        print("\n[HEADER ALIGNMENT]")
        print(f"{'Field':<15} | {'Packer Offset':<15} | {'Sim Offset':<15} | {'Match'}")
        print("-" * 60)
        for name, (start, width) in ISA_V5_HEADER.items():
            packer_abs_offset = start + payload_width
            sim_field = sim_layout.get(name)
            sim_offset = sim_field['offset'] if sim_field else "MISSING"
            match = "OK" if packer_abs_offset == sim_offset else "MISMATCH"
            print(f"{name:<15} | {packer_abs_offset:<15} | {sim_offset:<15} | {match}")

        print(f"\n[BODY ALIGNMENT - {unit_name}]")
        packer_body_spec = ISA_V5_FULL_SPEC[unit_name][1]
        
        print(f"{'Field':<15} | {'Packer Offset':<15} | {'Sim Offset':<15} | {'Match'}")
        print("-" * 60)
        for name, (start, width) in packer_body_spec.items():
            sim_field = sim_layout.get(name)
            sim_offset = sim_field['offset'] if sim_field else "MISSING"
            match = "OK" if start == sim_offset else "MISMATCH"
            print(f"{name:<15} | {start:<15} | {sim_offset:<15} | {match}")

if __name__ == "__main__":
    verify_alignment()
