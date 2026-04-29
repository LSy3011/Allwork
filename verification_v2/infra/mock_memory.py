import numpy as np

class MockProgram:
    class MockAllocator:
        def __init__(self, vector_lanes: int):
            self.vector_lanes = vector_lanes
            # 初始化 8 个 Bank，每行长度为动态 vector_lanes
            self.banks = {i: [np.zeros(vector_lanes, dtype=np.float32) for _ in range(256)] for i in range(8)}
    
    def __init__(self, vector_lanes: int = 4):
        self.vector_lanes = vector_lanes
        self.memory_allocator = self.MockAllocator(vector_lanes)
        self.tensors = {}
        
    def inject_vector(self, logical_addr: int, data: np.ndarray):
        """注入矢量数据"""
        bank_id = logical_addr // 256
        offset = logical_addr % 256
        v_lanes = self.vector_lanes
        num_rows = (len(data) + v_lanes - 1) // v_lanes
        for r in range(num_rows):
            row_data = data[r*v_lanes : (r+1)*v_lanes]
            if len(row_data) < v_lanes:
                row_data = np.pad(row_data, (0, v_lanes - len(row_data)))
            self.memory_allocator.banks[bank_id][offset + r] = row_data

    def inject_broadcast(self, logical_addr: int, scalar: float):
        """将标量广播到整个 SIMD 行"""
        bank_id = logical_addr // 256
        offset = logical_addr % 256
        self.memory_allocator.banks[bank_id][offset] = np.full(self.vector_lanes, scalar, dtype=np.float32)
