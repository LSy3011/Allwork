from typing import List, Dict, Any

# 🛸 唯一保留的静态定义：IQ Header (由于其结构在 isa_v5 层面是全局锁死的)
ISA_V5_HEADER = {
    "INC_EMBED": (0, 6), "INC": (6, 4), "DLY": (10, 3), "COND": (13, 7),
    "LOOPS": (20, 10), "EMBED_END": (30, 6), "EMBED_LOOPS": (36, 3),
    "JUMP": (39, 1), "VLD": (40, 1)
}
ISA_V5_HEADER_WIDTH = 41

# ADR-050：LSU 单元（LDB/LDB_AXI/STB/STB_AXI，RTL iq_inst[24-27]）header=50bit，与 compute 41bit
#   唯一差异 = INC 字段 4→13bit（带符号循环地址步进，须步进 15bit UR micro-addr / 40bit AXI 地址；
#   hco RTL 取证 smi_iq.v:58「BITS_INC = (IQ 24|25|26|27)?13:4」）。其上字段 offset 整体 +9、无新增字段。
ISA_V5_HEADER_LSU = {
    "INC_EMBED": (0, 6), "INC": (6, 13), "DLY": (19, 3), "COND": (22, 7),
    "LOOPS": (29, 10), "EMBED_END": (39, 6), "EMBED_LOOPS": (45, 3),
    "JUMP": (48, 1), "VLD": (49, 1)
}
ISA_V5_HEADER_LSU_WIDTH = 50
_LSU_UNITS = {"LDB", "LDB_AXI", "STB", "STB_AXI"}

def _iq_header_for(u_type: str):
    """ADR-050：按 unit_type 选 IQ header 变体（LSU 50bit / compute 41bit）。"""
    if u_type in _LSU_UNITS:
        return ISA_V5_HEADER_LSU, ISA_V5_HEADER_LSU_WIDTH
    return ISA_V5_HEADER, ISA_V5_HEADER_WIDTH

class BitPacker:
    """
    AIACC 绝对位打包引擎 (V7.0 - SSoT 规格对齐版)。
    实时从 HardwareSpec 提取位偏移，拒绝任何形式的硬编码字典。
    """
    @staticmethod
    def pack_capsule(spec: Any, unit_type: str, payload_dict: Dict[str, Any], is_body: bool = True, micro_variant: str = None, **header_kwargs) -> int:
        u_type = unit_type.upper().replace("-", "_")
        # 兼容性映射
        if "UR_R" in u_type: u_type = "UR_READ"
        if "UR_W" in u_type: u_type = "UR_WRITE"

        # 🛸 核心：从传入的 spec 中动态获取模块定义
        if u_type not in spec.modules:
            body_bits = 16
            b_fields = []
        else:
            module = spec.modules[u_type]
            # ADR-032 D2：多微指令单元按 micro_variant 选组（body 宽度/字段随组变，8/7/9bit）；
            # 缺省/单微指令单元 → 顶层 code_bits/code_fields（向后兼容）。
            grp = (getattr(module, "micro_groups", None) or {}).get(micro_variant) if micro_variant else None
            body_bits = grp.code_bits if grp else module.code_bits
            b_fields = grp.code_fields if grp else module.code_fields

        # 1. 打包 Header（ADR-050：LSU=50bit / compute=41bit，按 unit_type 选变体）
        header_map, _hw = _iq_header_for(u_type)
        h_val = 0
        norm_header = {k.upper(): v for k, v in header_kwargs.items()}
        for f_name, (start, width) in header_map.items():
            val = norm_header.get(f_name, 0)
            h_val |= (int(val) & ((1 << width) - 1)) << start
        
        # 2. 打包 Body (动态推导偏移)
        b_val = 0
        if is_body:
            norm_body = {k.upper(): v for k, v in payload_dict.items()}
            curr_pos = 0
            for field in b_fields:
                f_name = field.name.upper()
                f_width = field.bits
                f_val = norm_body.get(f_name, 0)
                b_val |= (int(f_val) & ((1 << f_width) - 1)) << curr_pos
                curr_pos += f_width

        # 3. 组合封装 (Header 在高位)
        return (h_val << body_bits) | b_val

    @staticmethod
    def get_unit_bits(spec: Any, unit_type: str, micro_variant: str = None) -> int:
        u_type = unit_type.upper().replace("-", "_")
        if "UR_R" in u_type: u_type = "UR_READ"
        if "UR_W" in u_type: u_type = "UR_WRITE"

        _, hdr_w = _iq_header_for(u_type)
        if u_type not in spec.modules:
            return hdr_w + 16
        module = spec.modules[u_type]
        # ADR-032 D2：多微指令单元按 variant 取 body 宽度（emitter 算 IQ 流偏移须传对 variant）。
        grp = (getattr(module, "micro_groups", None) or {}).get(micro_variant) if micro_variant else None
        return hdr_w + (grp.code_bits if grp else module.code_bits)
