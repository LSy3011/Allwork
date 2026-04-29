import numpy as np
from typing import List, Dict, Any
from .base import BaseOperator
from sim.tests.helpers import make_op

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
            "coeffs": {"bank": self.BANK_COEFF, "role": "param", "shape": (len(self.coeffs), -1)},
            "y":      {"bank": self.BANK_OUT,   "role": "output"}
        }

    def get_dfg(self, spec: Any = None) -> List[Any]:
        bank_depth = spec.params.PARAM_UR_BANK_DEPTH if spec else 256
        
        # 逻辑地址计算基准
        ADDR_X = self.BANK_IN * bank_depth
        ADDR_C = self.BANK_COEFF * bank_depth
        ADDR_Y = self.BANK_OUT * bank_depth

        import sympy as sp
        from src.frontend.compiler import GraphCompiler

        x = sp.Symbol('x')
        c = [sp.Symbol(f'c{i}') for i in range(len(self.coeffs))]
        
        # Horner 公式: ((c0*x + c1)*x + c2) ... + cn
        formula = c[0]
        for i in range(1, len(self.coeffs)):
            formula = formula * x + c[i]

        inputs_meta = {
            x: {"logical_addr": ADDR_X, "stride": 1, "role": "operand"}
        }
        for i in range(len(self.coeffs)):
            inputs_meta[c[i]] = {"logical_addr": ADDR_C + i, "stride": 0, "role": "operand"}

        compiler = GraphCompiler()
        ops = compiler.compile(inputs_meta, formula, out_name="y")
        
        # 补齐前端生成的 UR_WRITE 的目标地址
        for op in ops:
            if op.op_type == "UR_WRITE" and op.metadata.get("role") == "output":
                op.metadata["logical_addr"] = ADDR_Y
                op.metadata["stride"] = 1

        # === RTOVR Edge 标记注入（Step 1）===
        nodes_by_id = {op.trace_id: op for op in ops}
        for op in ops:
            if op.op_type.upper() == "RTOVR":
                rtovr_prod_ids = op.metadata.get("depends_on", [])
                if not rtovr_prod_ids:
                    continue
                rtovr_prod_id = rtovr_prod_ids[0]
                dvr = op.metadata.get("dvr", -1)
                sel = op.metadata.get("sel", -1)
                # 将信息注入消费者节点
                for consumer in ops:
                    if op.trace_id in consumer.metadata.get("depends_on", []):
                        consumer.metadata.setdefault("rtovr_deps", {})[rtovr_prod_id] = {
                            "dvr": dvr, "sel": sel
                        }

        return ops

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        data_x = input_data["x"]
        
        # 动态 Golden 计算 (使用 numpy 多项式求值)
        # 注意: coeffs 顺序为 [cn, ..., c0]，numpy.polyval 也是这个顺序
        golden_y = np.polyval(self.coeffs, data_x)
        
        # 审计追踪 (仅追踪 Lane 0)
        golden_intermediates = []
        num_iterations = len(data_x) // vector_lanes
        for iter_idx in range(num_iterations):
            x_val = data_x[iter_idx * vector_lanes]
            golden_intermediates.append({"iter": iter_idx, "x": float(x_val)})
            
        return {"y": golden_y, "intermediates": golden_intermediates}

    def inject_data(self, program: Any, input_data: Dict[str, np.ndarray], vector_lanes: int, spec: Any = None):
        # 🛸 动态 Bank 深度对齐：彻底清除 256 硬编码
        bank_depth = spec.params.PARAM_UR_BANK_DEPTH if spec else 256
        
        data_x = input_data["x"]
        program.inject_vector(self.BANK_IN * bank_depth, data_x)
        # 广播系数到所有 Lane
        for i, val in enumerate(self.coeffs):
            program.inject_broadcast(self.BANK_COEFF * bank_depth + i, val)

    def prepare_input(self, n_rows: int) -> Dict[str, np.ndarray]:
        data_x = np.linspace(0.1, 1.6, n_rows).astype(np.float32)
        return {"x": data_x}
