from typing import List, Dict, Set
from ir.mir.mir_op import MIROp

class HSTSStitcher:
    """
    异构算子缝合器 (Heterogeneous Stitcher)。
    负责将多个独立算子的物理足迹在统一的时间轴上进行“无冲突咬合”。
    """
    def __init__(self):
        self.final_stream: List[MIROp] = []
        self.occupied_t_iq: Dict[int, Set[int]] = {}   # {global_t: set(iq_ids)}
        self.occupied_t_bank: Dict[int, Set[int]] = {} # {global_t: set(bank_ids)}
        self.current_max_t = 0
        self.op_instances = []

    def _check_conflict(self, stream: List[MIROp], synthesis_res: Dict[str, int], offset: int) -> bool:
        """检查在该偏移量下是否存在 IQ 发射或 Bank 访问冲突"""
        for op in stream:
            # 使用 SMT 求解出的 t
            local_t = synthesis_res[op.node_id]
            global_t = local_t + offset
            
            # 1. 检查 IQ 冲突
            if global_t in self.occupied_t_iq:
                if "iq_id" in op.metadata and op.metadata["iq_id"] in self.occupied_t_iq[global_t]:
                    return True
            
            # 2. 检查 Bank 冲突
            if global_t in self.occupied_t_bank:
                if "bank_id" in op.metadata and op.metadata["bank_id"] in self.occupied_t_bank[global_t]:
                    return True
        return False

    def add_operator(self, name: str, stream: List[MIROp], synthesis_res: Dict[str, int]) -> int:
        offset = 0
        while self._check_conflict(stream, synthesis_res, offset):
            offset += 1
            
        instance_name = f"{name}_{len(self.op_instances)}"
        self.op_instances.append(instance_name)
        
        for op in stream:
            local_t = synthesis_res[op.node_id]
            global_t = local_t + offset
            
            # 注入时序信息
            op.metadata["t"] = local_t
            op.metadata["global_t"] = global_t
            op.metadata["instance_name"] = instance_name
            
            # 更新全局占用
            if "iq_id" in op.metadata:
                self.occupied_t_iq.setdefault(global_t, set()).add(op.metadata["iq_id"])
            if "bank_id" in op.metadata:
                self.occupied_t_bank.setdefault(global_t, set()).add(op.metadata["bank_id"])
            
            self.final_stream.append(op)
            self.current_max_t = max(self.current_max_t, global_t)
            
        print(f"HSTS: Operator [{name}] stitched safely at offset {offset}.")
        return offset

    def get_final_stream(self) -> List[MIROp]:
        return sorted(self.final_stream, key=lambda x: x.metadata["global_t"])
