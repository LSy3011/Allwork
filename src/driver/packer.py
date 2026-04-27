from typing import List, Dict, Any

# 🛸 AIACC V6.5 绝对物理真理 (完全对齐 hw_config/specs/units/*.yaml)
ISA_V5_FULL_SPEC = {
    "FPADD": (10, {
        "VALID": (0, 1), "SRC_PREC": (1, 1), "DST_PREC": (2, 1), 
        "UPDATE_ST": (3, 1), "SUB": (4, 1), "SMC_ID": (5, 5)
    }),
    "FPMUL": (8, {
        "VALID": (0, 1), "SRC_PREC": (1, 1), "DST_PREC": (2, 1), "SMC_ID": (3, 5)
    }),
    "UR_READ": (17, {
        "VALID": (0, 1), "BANK": (1, 3), "ADDR": (4, 8), "SMC_ID": (12, 5)
    }),
    "UR_WRITE": (33, {
        "VALID": (0, 1), "BANK": (1, 3), "ADDR": (4, 8), "WBE": (12, 16), "SMC_ID": (28, 5)
    }),
    "RTOVR": (7, {
        "VALID": (0, 1), "SEL": (1, 6)
    }),
}

ISA_V5_HEADER = {
    "INC_EMBED": (0, 6), "INC": (6, 4), "DLY": (10, 3), "COND": (13, 7), 
    "LOOPS": (20, 10), "EMBED_END": (30, 6), "EMBED_LOOPS": (36, 3), 
    "JUMP": (39, 1), "VLD": (40, 1)
}

class BitPacker:
    """
    AIACC 绝对位打包引擎 (V6.5 - YAML-Aligned)。
    实现从逻辑字段到物理比特位的 bit-perfect 映射。
    """
    @staticmethod
    def pack_capsule(unit_type: str, payload_dict: Dict[str, Any], is_body: bool = True, **header_kwargs) -> int:
        u_type = unit_type.upper().replace("-", "_")
        if "UR_READ" in u_type: u_type = "UR_READ"
        if "UR_WRITE" in u_type: u_type = "UR_WRITE"
        
        spec = ISA_V5_FULL_SPEC.get(u_type)
        body_bits, b_fields = spec if spec else (16, {})

        # 1. 打包 Header (41 bits)
        h_val = 0
        for f_name, (start, width) in ISA_V5_HEADER.items():
            # SMIHeader.to_dict() 返回的是大写键名
            val = header_kwargs.get(f_name, 0)
            h_val |= (int(val) & ((1 << width) - 1)) << start

        # 2. 打包 Body
        b_val = 0
        if is_body:
            norm_body = {k.upper(): v for k, v in payload_dict.items()}
            for f_name, (start, width) in b_fields.items():
                f_val = norm_body.get(f_name.upper(), 0)
                b_val |= (int(f_val) & ((1 << width) - 1)) << start

        # 3. 组合封装 (Header 在高位)
        return (h_val << body_bits) | b_val

    @staticmethod
    def get_unit_bits(unit_type: str) -> int:
        u_type = unit_type.upper().replace("-", "_")
        if "UR_R" in u_type: u_type = "UR_READ"
        if "UR_W" in u_type: u_type = "UR_WRITE"
        spec = ISA_V5_FULL_SPEC.get(u_type)
        body_bits = spec[0] if spec else 16
        return 41 + body_bits
