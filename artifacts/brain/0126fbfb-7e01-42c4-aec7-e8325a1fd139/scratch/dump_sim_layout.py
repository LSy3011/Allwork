import sys, os
sys.path.append(os.path.join(os.getcwd(), 'src'))
sys.path.append(os.path.join(os.getcwd(), 'sim'))

from aiacc_hardware.src.spec_loader import SpecLoader
from sim.smc_simulator.core.decoder import InstructionDecoder

def dump_decoder_layouts():
    sl = SpecLoader()
    spec = sl.get_system_core()
    
    print("=== [SIMULATOR DECODER LAYOUTS] ===")
    
    units = ["UR_READ", "UR_WRITE", "FPADD", "FPMUL", "RTOVR"]
    for uname in units:
        print(f"\nUnit: {uname}")
        u_spec = sl.get_unit(uname)
        if not u_spec:
            print("  NOT FOUND IN SPEC")
            continue
            
        decoder = InstructionDecoder(u_spec)
        print(f"  Payload Width: {decoder.payload_width}")
        print(f"  Layout:")
        # Sort layout by offset
        sorted_layout = sorted(decoder._layout.items(), key=lambda x: x[1]['offset'])
        for name, info in sorted_layout:
            print(f"    - {name:<15}: Offset {info['offset']:<3} | Width {info['mask'].bit_length()}")

if __name__ == "__main__":
    dump_decoder_layouts()
