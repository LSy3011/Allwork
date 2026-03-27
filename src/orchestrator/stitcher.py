import copy
from typing import List, Dict, Tuple
from ir.mir.mir_op import MIROp
from .footprint import FootprintMask
from .factory import FootprintFactory

class HSTSStitcher:
    """
    AIACC HSTS 缝合器：将多个已合成的算子物理流合并为一个全局指令流。
    核心原则：物理资源绝对互斥。
    """
    def __init__(self):
        self.global_mask = FootprintMask()
        self.global_ops: List[MIROp] = []
        self.max_t = 0

    def add_operator(self, name: str, ops: List[MIROp], smt_result: Dict[str, int]) -> int:
        """
        向全局流中安全插入一个算子。
        返回该算子被安置的起始 Phase (Offset)。
        """
        # 1. 提取该算子的足迹
        op_mask = FootprintFactory.from_smt_result(ops, smt_result)
        
        # 2. 搜索最早的安全合入点
        # 初始步进为 1 拍周期，直到无物理冲突 (IQ/BANK/SLOT)
        offset = 0
        while self.global_mask.conflicts_with(op_mask, offset=offset):
            offset += 1
            
        # 3. 确定入场！合并足迹到全局表 (带原子计数功能)
        self.global_mask.merge(op_mask, offset=offset)
        
        # 4. 克隆并平移算子的物理坐标
        for op in ops:
            new_op = copy.deepcopy(op)
            t_rel = smt_result[op.node_id]
            # 记录平移后的全局时间与原始相对时间
            new_op.metadata["t"] = t_rel
            new_op.metadata["global_t"] = t_rel + offset
            new_op.metadata["instance_name"] = name
            self.global_ops.append(new_op)
            self.max_t = max(self.max_t, t_rel + offset)
            
        print(f"HSTS: Operator [{name}] stitched safely at offset {offset}.")
        return offset

    def get_final_stream(self) -> List[MIROp]:
        """返回有序的全局指令流"""
        return sorted(self.global_ops, key=lambda x: x.metadata["global_t"])
