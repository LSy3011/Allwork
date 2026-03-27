from typing import List, Dict
from ir.mir.mir_op import MIROp

class BalancedBankColorer:
    """
    AIACC 均衡 Bank 着色器：
    利用启发式算法在 DFG 层面对 Bank 进行指派，以最小化 HSTS 调度中的资源冲突。
    """
    @staticmethod
    def color_dfg(ops: List[MIROp], num_banks: int = 8) -> List[MIROp]:
        """
        自动为 DFG 中的 UR_READ/UR_WRITE 节点分配 bank_id。
        采用 Round-Robin 为主的散列分配策略，优先分散并发加载。
        """
        # 1. 提取所有需要 Bank 资源的节点
        mem_ops = [op for op in ops if op.op_type in ["UR_READ", "UR_WRITE"]]
        
        # 2. 简单的负载均衡指派（Round-Robin）
        # 后续可以升级为基于逻辑依赖深度的“冲突敏感型”指派
        current_bank = 0
        
        for op in mem_ops:
            # 如果是常量系数，我们将其聚集在 Bank 2-5 (模拟典型 AIACC 布局)
            if op.node_id.startswith("c"):
                # 假设常量分布在特定范围，减少对变量 Bank 的挤占
                if "1" in op.node_id: op.metadata["bank_id"] = 2
                elif "2" in op.node_id: op.metadata["bank_id"] = 3
                elif "3" in op.node_id: op.metadata["bank_id"] = 4
                else: op.metadata["bank_id"] = 5
            else:
                # 变量 (x, y, out) 使用 0, 1, 6, 7 均衡扩展
                available_variable_banks = [0, 1, 6, 7]
                op.metadata["bank_id"] = available_variable_banks[current_bank % len(available_variable_banks)]
                current_bank += 1
                
        return ops

    @staticmethod
    def get_auto_mapping(ops: List[MIROp]) -> Dict[str, int]:
        """返回节点到 Bank 的映射关系表"""
        return {op.node_id: op.metadata.get("bank_id") for op in ops if "bank_id" in op.metadata}
