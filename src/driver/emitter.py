import os
from pathlib import Path
from typing import List, Dict, Tuple

from api.hw_spec import HardwareSpec
from driver.field_mapper import FieldMapper
from driver.compactor import Compactor
from driver.packer import BitPacker

class LIRPacker:
    """
    AIACC 二进制驱动生成器 (v5: 位预算感知版)。
    """
    def __init__(self, config_dir: str = "hw_config"):
        self.hw_spec = HardwareSpec.load(config_dir)

    def emit_from_stream(self, final_stream: List, output_dir: str):
        os.makedirs(output_dir, exist_ok=True)
        print(f"\n[EMITTER] Packing {len(final_stream)} onto 41-bit micro-isa (Budget: 30-bit Cargo)...")
        
        module_streams: Dict[str, List[Tuple[int, int, bool]]] = {}
        for op in final_stream:
            m_name = op.op_type.upper()
            iq_name = f"IQ_{m_name}"
            if iq_name not in self.hw_spec.iqs: continue
                
            module_spec = self.hw_spec.modules.get(m_name)
            if not module_spec: continue
            
            payload = FieldMapper.map_op_to_payload(op, module_spec)
            module_streams.setdefault(iq_name, []).append((op.metadata["global_t"], payload, False))
            
        total_compressed = 0
        for iq_name, stream in module_streams.items():
            iq_conf = self.hw_spec.iqs[iq_name]
            stream.sort(key=lambda x: x[0])
            
            # --- 物理冲突安全系数 ---
            # 如果 BITS_CODE 本身已经接近 30，强制减小 Compactor 的循环探测窗口
            # 这是一个隐含的硬件物理限制
            if iq_conf.BITS_CODE + iq_conf.BITS_LOOPS > 30:
                print(f"  ⚠️ Warning: [{iq_name}] Payload ({iq_conf.BITS_CODE}b) + Loop ({iq_conf.BITS_LOOPS}b) exceeds 30-bit Cargo! Scaling down aggregation.")
            
            compressed = Compactor.compress_iq_stream(stream, iq_conf)
            
            bin_data = bytearray()
            for cmd in compressed:
                # 调用 LIR Packer 5.0，整合 JUMP, EMBED 等高级元数据
                # 目前默认 JUMP=0, EMBED=0，可在未来扩展 DFG 逻辑时开启
                val = BitPacker.pack_41bit(
                    iq_name=iq_name, 
                    iq_conf=iq_conf, 
                    payload=cmd["payload"], 
                    loops=cmd["loops"], 
                    dly=cmd["dly"]
                )
                bin_data.extend(BitPacker.uint64_to_bin_bytes(val))
                
            bin_file = Path(output_dir) / f"{iq_name.lower()}.bin"
            with open(bin_file, "wb") as f:
                f.write(bin_data)
                
            ratio = 1.0 - len(compressed)/len(stream) if len(stream) > 0 else 0
            print(f"  > [{iq_name:<12}] Capsules: {len(compressed):<4} (Compression: {ratio:.1%})")
            total_compressed += len(compressed)

        print(f"\n✅ EMISSION COMPLETE. Microcode Solidified at {output_dir}")
