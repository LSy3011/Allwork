import os
from pathlib import Path
from typing import List, Dict, Tuple

from api.hw_spec import HardwareSpec
from driver.field_mapper import FieldMapper
from driver.compactor import Compactor
from driver.packer import BitPacker

class LIRPacker:
    """
    AIACC 二进制驱动生成器 (v7: 动态字节对齐版)。
    """
    def __init__(self, config_dir: str = "hw_config"):
        self.hw_spec = HardwareSpec.load(config_dir)

    def emit_from_stream(self, final_stream: List, output_dir: str):
        os.makedirs(output_dir, exist_ok=True)
        print(f"\n[EMITTER] Packing {len(final_stream)} onto Byte-Aligned Capsules (Header 41-bit + Dynamic CODE)...")
        
        module_streams: Dict[str, List[Tuple[int, int, bool]]] = {}
        for op in final_stream:
            m_name = op.op_type.upper()
            iq_name = f"IQ_{m_name}"
            if iq_name not in self.hw_spec.iqs: continue
            
            module_spec = self.hw_spec.modules.get(m_name)
            if not module_spec: continue
            
            payload = FieldMapper.map_op_to_payload(op, module_spec)
            module_streams.setdefault(iq_name, []).append((op.metadata["global_t"], payload, False))
            
        total_capsules = 0
        for iq_name, stream in module_streams.items():
            iq_conf = self.hw_spec.iqs[iq_name]
            total_bits = 41 + iq_conf.BITS_CODE
            bytes_per_op = (total_bits + 7) // 8
            
            stream.sort(key=lambda x: x[0])
            compressed = Compactor.compress_iq_stream(stream, iq_conf)
            
            bin_data = bytearray()
            for cmd in compressed:
                val = BitPacker.pack_capsule(
                    iq_name=iq_name, 
                    iq_conf=iq_conf, 
                    payload=cmd["payload"], 
                    loops=cmd["loops"], 
                    dly=cmd["dly"]
                )
                bin_data.extend(BitPacker.pack_to_bytes(val, total_bits))
                
            bin_file = Path(output_dir) / f"{iq_name.lower()}.bin"
            with open(bin_file, "wb") as f:
                f.write(bin_data)
                
            ratio = 1.0 - len(compressed)/len(stream) if len(stream) > 0 else 0
            print(f"  > [{iq_name:<12}] Capsules: {len(compressed):<4} (Width: {total_bits} bits, Storage: {bytes_per_op} B/op)")
            total_capsules += len(compressed)

        print(f"\n✅ EMISSION COMPLETE. Byte-Aligned Binaries Locked at {output_dir}")
