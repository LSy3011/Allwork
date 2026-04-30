import numpy as np
import sympy as sp
from typing import List, Dict, Any
from .base import BaseOperator

class HornerOperator(BaseOperator):
    # 🛸 布局定义：唯一真理来源
    BANK_IN    = 0
    BANK_COEFF = 1
    BANK_OUT   = 2

    def __init__(self, coeffs: List[float]):
        self.coeffs = coeffs # [c_n, c_n-1, ..., c0]
        self.order = len(coeffs) - 1

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        return {
            "x":      {"bank": self.BANK_IN,    "role": "input"},
            "coeffs": {"bank": self.BANK_COEFF, "role": "param"},
            "y":      {"bank": self.BANK_OUT,   "role": "output"}
        }

    def get_dfg(self, spec: Any = None) -> List[Any]:
        # 1. 定义符号
        x = sp.Symbol('x')
        c = [sp.Symbol(f'c{i}') for i in range(len(self.coeffs))]
        
        # 2. 纯数学定义 (Horner 公式)
        formula = c[0]
        for i in range(1, len(self.coeffs)):
            formula = formula * x + c[i]

        # 3. 利用基类工具全自动构建 DFG 并注入物理契约
        inputs_meta = self.build_inputs_meta({"x": x, "coeffs": c}, spec)
        return self.compile_with_adpc(formula, inputs_meta, out_addr=self.get_bank_addr("y", spec), spec=spec)

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        data_x = input_data["x"]
        golden_y = np.polyval(self.coeffs, data_x)
        
        golden_intermediates = []
        num_iterations = len(data_x) // vector_lanes
        for iter_idx in range(num_iterations):
            x_val = data_x[iter_idx * vector_lanes]
            golden_intermediates.append({"iter": iter_idx, "x": float(x_val)})
            
        return {"y": golden_y, "intermediates": golden_intermediates}

    def prepare_input(self, n_rows: int) -> Dict[str, np.ndarray]:
        """准备输入数据，注意系数也作为 'param' 返回，以便基类自动注入"""
        data_x = np.linspace(0.1, 1.6, n_rows).astype(np.float32)
        return {
            "x": data_x,
            "coeffs": self.coeffs
        }
