from typing import Dict, List
from ir.mir.mir_op import MIROp

class FieldMapper:
    """
    AIACC 逻辑到物理字段映射器。
    根据 modules.yaml 描述，将指令元数据转换为 Payload 整数。
    """
    @staticmethod
    def map_op_to_payload(op: MIROp, module_spec) -> int:
        payload = 0
        current_pos = 0 
        
        # Pydantic 对象访问属性
        fields = module_spec.code_fields
        meta = op.metadata
        
        for field in fields:
            # field 也是 Pydantic 对象 (ModuleField)
            fname = field.name
            fbits = field.bits
            fval = 0
            
            # --- 映射逻辑 ---
            if fname in ["re", "we", "vld"]: fval = 1
            elif fname in ["rbank", "wbank", "bank_id", "bank", "p_bank_id"]: 
                fval = int(meta.get("bank_id", meta.get("bank", meta.get("p_bank_id", 0))))
            elif fname in ["raddr", "waddr", "p_addr", "address"]: 
                fval = int(meta.get("p_addr", meta.get("address", 0)))
            elif fname == "src_prec" or fname == "dst_prec": 
                prec = meta.get("precision", meta.get("src_prec", meta.get("dst_prec", "f32")))
                fval = 1 if "f32" in str(prec).lower() else 0
            elif fname == "add_or_sub": 
                fval = 1 if "SUB" in op.op_type.upper() else 0
            elif fname == "dr_index" or fname == "src_id":
                fval = int(meta.get("p_src_id", meta.get("slot_id", 0)))
            elif fname == "up_stat_en": fval = 1
            elif fname == "smc_id_en": fval = int(meta.get("smc_id_en", 0))
            elif fname == "wbe": fval = int(meta.get("wbe", 0xFFFF))
            
            payload |= ((fval & ((1 << fbits) - 1)) << current_pos)
            current_pos += fbits
            
        return payload
