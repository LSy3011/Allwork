from abc import ABC, abstractmethod
from typing import List, Dict, Any
import numpy as np

class BaseOperator(ABC):
    @property
    def element_bits(self) -> int:
        """从 DFG 节点元数据中自动推导数据位宽"""
        dfg = self.get_dfg()
        for node in dfg:
            # 查找 metadata 中的精度申明
            p = node.metadata.get("precision") or node.metadata.get("element_bits")
            if p:
                if isinstance(p, int): return p
                p = str(p).lower()
                if "32" in p: return 32
                if "16" in p: return 16
                if "8" in p: return 8
                if "4" in p: return 4
        return 32 # 默认 FP32

    @property
    @abstractmethod
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        """返回该算子的 Tensor 布局配置，如 {'x': {'bank': 0, 'role': 'input'}, ...}"""
        pass

    @abstractmethod
    def get_dfg(self, spec: Any = None) -> List[Any]:
        """返回 make_op 产生的算子列表"""
        pass

    @abstractmethod
    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        """
        返回 Golden 结果和中间变量。
        """
        pass

    @abstractmethod
    def inject_data(self, program: Any, input_data: Dict[str, np.ndarray], vector_lanes: int, spec: Any = None):
        """将测试数据注入 MockProgram"""
        pass

    @abstractmethod
    def prepare_input(self, n_rows: int) -> Dict[str, np.ndarray]:
        """准备输入数据"""
        pass
