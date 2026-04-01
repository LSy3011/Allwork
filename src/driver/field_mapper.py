from typing import Dict, List, Any
from ir.mir.mir_op import MIROp
from driver.hw_constants import (
    SEMANTIC_RE, SEMANTIC_WE, SEMANTIC_BANK, SEMANTIC_ADDR,
    SEMANTIC_PREC, SEMANTIC_SRCID
)

class FieldMapper:
    """
    AIACC 物理字段映射器 (V9: SDD 规格驱动版)。
    遵循 ADR-006: 物理语义下沉原则。
    驱动层是无偏见的 (Unbiased)，完全根据 YAML 规格和元数据进行打包。
    """
    @staticmethod
    def map_op_to_payload(op: MIROp, module_spec) -> int:
        payload = 0
        current_pos = 0 
        meta = op.metadata
        
        # 将 encoding 反向映射，以便将 "SUB" 转换为 1
        def resolve_fval(field, user_val) -> int:
            # 1. 尝试从 encoding 表中进行反向语义查找 (如 "SUB" -> 1)
            if isinstance(user_val, str) and field.encoding:
                for k, v in field.encoding.items():
                    if str(v).upper() == user_val.upper():
                        return int(k)
            # 2. 尝试解析为整数
            try:
                return int(user_val)
            except (ValueError, TypeError):
                return field.default

        for field in module_spec.code_fields:
            fname = field.name
            fbits = field.bits
            fval = field.default # 初期预置为规格默认值
            
            # --- 优先级瀑布决议 (Waterfall Priority) ---
            
            # 1. 指令级显式覆盖 (Highest): 匹配 YAML 中的物理字段名 (如 SUB, UPDATE_ST)
            if fname in meta:
                fval = resolve_fval(field, meta[fname])
                
            # 2. 通用语义宏匹配 (Medium): 处理 RE/WE/BANK/ADDR/PREC/SRCID
            elif fname in SEMANTIC_RE or fname in SEMANTIC_WE:
                fval = 1 # 只要硬件定义了这些使能位，当前指令即视作激活
            
            elif fname in SEMANTIC_BANK:
                for k in SEMANTIC_BANK:
                    if k in meta and meta[k] is not None:
                        fval = int(meta[k])
                        break
            
            elif fname in SEMANTIC_ADDR:
                for k in SEMANTIC_ADDR:
                    if k in meta and meta[k] is not None:
                        fval = int(meta[k])
                        break
                
            elif fname in SEMANTIC_PREC:
                prec = "f32"
                for k in SEMANTIC_PREC:
                    if k in meta and meta[k] is not None:
                        prec = meta[k]
                        break
                # PREC_32 映射到 1 (来自 YAML encoding 的常规共识，若 YAML 覆盖则以 YAML 优先)
                fval = 1 if "f32" in str(prec).lower() else 0
                
            elif fname in SEMANTIC_SRCID:
                for k in SEMANTIC_SRCID:
                    if k in meta and meta[k] is not None:
                        fval = int(meta[k])
                        break
            
            # 3. 特殊固定字段处理 (Legacy Support)
            elif fname == "wbe":
                fval = int(meta.get("wbe", 0xFFFF))
            elif "smc_id" in fname:
                fval = int(meta.get("smc_id_en", 0))

            if op.node_id in ["c4_rd", "w_y"]:
                print(f"[DEBUG-FIELDMAPPER] op={op.node_id} field={fname} fval={fval} meta_addr={meta.get('addr')} meta_bank={meta.get('bank_id')}")

            # --- 物理级联与掩码保护 (Physical Marshalling) ---
            fmask = (1 << fbits) - 1
            payload |= ((fval & fmask) << current_pos)
            current_pos += fbits
            
        return payload
