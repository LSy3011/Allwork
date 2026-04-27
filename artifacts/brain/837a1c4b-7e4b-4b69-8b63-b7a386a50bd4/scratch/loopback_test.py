import sys, os
import numpy as np

# Set up paths
sys.path.append(os.path.join(os.getcwd(), 'src'))
sys.path.append(os.path.join(os.getcwd(), 'sim'))

from ir.lir.lir_op import PhysicalInstruction, SMIHeader
from driver.packer import BitPacker
from sim.smc_simulator.core.decoder import InstructionDecoder
from api.hw_spec import HardwareSpec

def test_bit_loopback():
    print("=== [BIT-LEVEL LOOPBACK TEST] ===")
    spec = HardwareSpec.load("hw_config")
    
    # 1. Prepare a complex instruction
    header = SMIHeader(
        vld=1, 
        jump=0, 
        embed=1, 
        level=1, 
        loops=123, 
        dly=2, 
        inc=-1, # Signed test
        embed_end=1
    )
    
    payload = {
        "VALID": 1,
        "BANK": 3,
        "ADDR": 45,
        "SMC_ID": 31
    }
    
    instr = PhysicalInstruction(
        iq_name="UR-R-0",
        header=header,
        payload=payload,
        abs_t=10,
        stage="BODY"
    )
    
    # 2. Encode using compiler's BitPacker
    # LIRPacker._get_lookup_type("UR-R-0") -> "UR_READ"
    encoded_val = BitPacker.pack_capsule(
        unit_type="UR_READ",
        payload_dict=instr.payload,
        is_body=True,
        **instr.header.to_dict()
    )
    
    print(f"Encoded Value: {hex(encoded_val)}")
    
    # 3. Decode using simulator's InstructionDecoder
    ur_mod_spec = spec.modules["UR_READ"]
    decoder = InstructionDecoder(ur_mod_spec)
    decoded_instr = decoder.decode(encoded_val)
    
    # 4. Compare fields
    discrepancies = []
    
    # Check Header fields
    header_fields = instr.header.to_dict()
    for field, expected in header_fields.items():
        # Decoder uses uppercase keys for fields
        # Note: EMBED_LOOPS in decoder might be named differently?
        # decoder._layout has 'EMBED_LOOPS'
        actual = getattr(decoded_instr, field, None)
        if actual is None:
            # Try to get from _fields dict
            actual = decoded_instr._fields.get(field)
            
        if actual != expected:
            discrepancies.append(f"Header Field {field}: Expected {expected}, Got {actual}")

    # Check Payload fields
    for field, expected in payload.items():
        actual = getattr(decoded_instr, field, None)
        if actual is None:
             actual = decoded_instr._fields.get(field)
        if actual != expected:
            discrepancies.append(f"Payload Field {field}: Expected {expected}, Got {actual}")

    if not discrepancies:
        print("\n✅ [SUCCESS] Bit-Level Loopback Passed! Compiler and Simulator are perfectly aligned.")
    else:
        print("\n❌ [FAILURE] Alignment Discrepancies Found:")
        for d in discrepancies:
            print(f"  - {d}")

if __name__ == "__main__":
    test_bit_loopback()
