import numpy as np
from typing import Dict, List, Any

# 🛸 算子数学属性注册表 (Decoupled Specs)
# 每一个新算子只需在这里增加一行配置，无需修改生成器核心逻辑。
OP_DATA_SPECS = {
    # 默认配置 (Default)
    "default": {
        "range": (-50.0, 50.0),
        "seeds": [0.0, -0.0, np.nan, np.inf, -np.inf, 1e-10, -1e-10]
    },
    "sin": {
        "range": (-1.5707, 1.5707),
        "seeds": [0.0, -0.0, 1.5707, -1.5707, 0.7854, -0.7854, 1e-4, -1e-4]
    },
    "relu": {
        "range": (-20.0, 20.0),
        "seeds": [0.0, -0.0, 1e-5, -1e-5, 5.0, -5.0]
    },
    "rec": {
        "range": (0.1, 100.0), # 倒数通常不测负数（如果硬件只支持正数域）
        "seeds": [0.1, 1.0, 10.0, 100.0, 1e-3]
    }
}

class ArithTestDataGenerator:
    """
    AIACC 算子测试数据工业级工厂 (V4.0 - Config Driven)。
    通过策略模式将算子数学特性与生成逻辑解耦。
    """
    
    @staticmethod
    def generate(op_type: str, mode: str = "fuzz", size: int = 4) -> Dict[str, np.ndarray]:
        spec = OP_DATA_SPECS.get(op_type, OP_DATA_SPECS["default"])
        
        if mode == "fuzz":
            return ArithTestDataGenerator._get_random_fuzz(op_type, spec, size)
        elif mode == "boundary":
            return ArithTestDataGenerator._get_boundary_sweep(op_type, spec, size)
        else:
            raise ValueError(f"Unsupported data mode: {mode}")

    @staticmethod
    def _get_random_fuzz(op_type: str, spec: dict, size: int) -> Dict[str, np.ndarray]:
        low, high = spec["range"]
        data = {
            f"ch_{i}": np.random.uniform(low, high, size).astype(np.float32)
            for i in range(4)
        }
        
        # 针对 Rec 的关联初值特殊逻辑 (保留最小必要的硬编码)
        if op_type == "rec":
            with np.errstate(divide='ignore', invalid='ignore'):
                data["ch_1"] = (1.0 / data["ch_0"] + np.random.uniform(-0.01, 0.01, size)).astype(np.float32)
        
        return data

    @staticmethod
    def _get_boundary_sweep(op_type: str, spec: dict, size: int) -> Dict[str, np.ndarray]:
        seeds = spec["seeds"]
        
        def fill_vec(seed_list, offset, target_size):
            shifted_seeds = seed_list[offset % len(seed_list):] + seed_list[:offset % len(seed_list)]
            repeat_count = (target_size // len(shifted_seeds)) + 1
            return np.array((shifted_seeds * repeat_count)[:target_size]).astype(np.float32)

        data = {
            f"ch_{i}": fill_vec(seeds, i * 3, size) # 每个通道偏移 3 位
            for i in range(4)
        }

        if op_type == "rec":
            with np.errstate(divide='ignore', invalid='ignore'):
                data["ch_1"] = (1.0 / data["ch_0"]).astype(np.float32)
                # 简单清洗
                mask = np.isnan(data["ch_1"]) | np.isinf(data["ch_1"])
                data["ch_1"][mask] = 0.5

        return data
