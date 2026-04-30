from abc import ABC, abstractmethod
from typing import List, Dict, Any
import numpy as np

class BaseOperator(ABC):
    @property
    def element_bits(self) -> int:
        dfg = self.get_dfg()
        for node in dfg:
            p = node.metadata.get("precision") or node.metadata.get("element_bits")
            if p:
                if isinstance(p, int): return p
                p = str(p).lower()
                if "32" in p: return 32
                if "16" in p: return 16
                if "8" in p: return 8
                if "4" in p: return 4
        return 32 

    @property
    @abstractmethod
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        pass

    @abstractmethod
    def get_dfg(self, spec: Any = None) -> List[Any]:
        pass

    @abstractmethod
    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        pass

    @abstractmethod
    def prepare_input(self, n_rows: int) -> Dict[str, np.ndarray]:
        pass

    def get_bank_addr(self, name: str, spec: Any) -> int:
        bank_depth = spec.params.PARAM_UR_BANK_DEPTH if spec else 256
        config = self.tensor_configs.get(name)
        if not config:
            raise ValueError(f"Tensor '{name}' not found in tensor_configs.")
        return config["bank"] * bank_depth

    def build_inputs_meta(self, symbols_map: Dict[str, Any], spec: Any) -> Dict:
        meta = {}
        for name, symbol in symbols_map.items():
            addr_base = self.get_bank_addr(name, spec)
            if isinstance(symbol, list):
                for i, sym in enumerate(symbol):
                    meta[sym] = {"logical_addr": addr_base + i, "stride": 0, "role": "operand"}
            else:
                stride = 1 if self.tensor_configs[name]["role"] == "input" else 0
                meta[symbol] = {"logical_addr": addr_base, "stride": stride, "role": "operand"}
        return meta

    def compile_with_adpc(self, formula, inputs_meta: Dict, out_name: str = "y", out_addr: int = None, spec: Any = None) -> List[Any]:
        """[ADPC V4.2] 全自动编译并注入物理契约，修复中间变量 Stride 丢失问题"""
        from src.frontend.compiler import GraphCompiler
        compiler = GraphCompiler()
        ops = compiler.compile(inputs_meta, formula, out_name=out_name)

        bank_depth = spec.params.PARAM_UR_BANK_DEPTH if spec else 256
        temp_base = 3 * bank_depth 
        
        # --- 🛸 [V4.2 FIX] 同步中间变量读写地址 ---
        sym_to_addr = {}
        
        # 第一遍扫描：分配写地址
        temp_offset = 0
        for op in ops:
            if op.op_type == "UR_WRITE" and op.metadata.get("role") == "intermediate":
                sym_name = op.metadata.get("meta_name")
                if sym_name not in sym_to_addr:
                    sym_to_addr[sym_name] = temp_base + temp_offset
                    temp_offset += 1
                op.metadata["logical_addr"] = sym_to_addr[sym_name]
                op.metadata["stride"] = 1

            elif out_addr is not None and op.op_type == "UR_WRITE" and op.metadata.get("role") == "output":
                op.metadata["logical_addr"] = out_addr
                op.metadata["stride"] = 1
                op.metadata["is_output"] = True

        # 第二遍扫描：同步读地址 (Reload)
        for op in ops:
            if op.op_type == "UR_READ":
                # 编译器为 Reload 生成的 trace_id 包含原始 symbol 名
                # 例如: rt_x0_reload_7_FPMUL...
                for sym_name, addr in sym_to_addr.items():
                    if f"_{sym_name}_" in op.trace_id or op.trace_id.startswith(f"{sym_name}_"):
                        op.metadata["logical_addr"] = addr
                        op.metadata["stride"] = 1
                        break

            # 3. 自动注入 RTOVR 边依赖
            if op.op_type.upper() == "RTOVR":
                prod_ids = op.metadata.get("depends_on", [])
                if prod_ids:
                    prod_id = prod_ids[0]
                    for consumer in ops:
                        if op.trace_id in consumer.metadata.get("depends_on", []):
                            consumer.metadata.setdefault("rtovr_deps", {})[prod_id] = {
                                "dvr": op.metadata.get("dvr", -1),
                                "sel": op.metadata.get("sel", -1)
                            }
        return ops

    def inject_data(self, program: Any, input_data: Dict[str, np.ndarray], vector_lanes: int, spec: Any = None):
        for name, config in self.tensor_configs.items():
            if config["role"] == "output": continue
            addr_base = self.get_bank_addr(name, spec)
            data = input_data.get(name)
            if data is None: continue
            if config["role"] == "input":
                program.inject_vector(addr_base, data)
            elif config["role"] == "param":
                if isinstance(data, (list, np.ndarray)):
                    for i, val in enumerate(data):
                        program.inject_broadcast(addr_base + i, val)
                else:
                    program.inject_broadcast(addr_base, data)
