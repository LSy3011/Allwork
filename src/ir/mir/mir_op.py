from typing import List, Dict, Any
from ..base import IRNode

class MIROp(IRNode):
    """
    MIR (Mid-level IR): 描述脉冲流。
    具有逻辑拍数，但尚未确定最终 LIR 绝对时间。
    """
    def __init__(self, op_type: str, logic_cycle: int, trace_id: str,
                 operands: List[str] = None, results: List[str] = None,
                 metadata: Dict[str, Any] = None):
        super().__init__(trace_id=trace_id, metadata=metadata)
        self.op_type = op_type
        self.logic_cycle = logic_cycle  # 相对该算子起始的时间 (0, 1, 2...)
        self.operands = operands or []
        self.results = results or []

    def __repr__(self):
        return f"MIROp({self.op_type} trace={self.trace_id[:8]} cycle={self.logic_cycle})"
