from typing import Dict, Set, List, Union
from pydantic import BaseModel, Field

class BankAddressMap(BaseModel):
    bank_id: int
    allocation: Dict[str, int] = Field(default_factory=dict)
    next_free_addr: int = 0

class MemoryAllocator:
    """
    AIACC 物理内存分配器 [全场景通用版]：
    支持跨算子实例 ID 的物理空间隔离。
    """
    def __init__(self, num_banks: int = 8):
        self.banks: List[BankAddressMap] = [BankAddressMap(bank_id=i) for i in range(num_banks)]

    def allocate(self, node_id: str, bank_id: int, inst_idx: Union[int, str] = 0, is_const: bool = False) -> int:
        bank = self.banks[bank_id]
        
        # 产生唯一的物理键
        logical_key = node_id if is_const else f"{node_id}_{inst_idx}"
        
        if logical_key in bank.allocation:
            return bank.allocation[logical_key]
        
        addr = bank.next_free_addr
        bank.allocation[logical_key] = addr
        bank.next_free_addr += 1
        return addr

    def get_layout(self) -> str:
        res = ["\n" + "="*80]
        res.append("                AIACC V5.0 HYBRID PHYSICAL MEMORY LAYOUT")
        res.append("="*80)
        for b in self.banks:
            if b.allocation:
                res.append(f"BANK {b.bank_id}:")
                sorted_alloc = sorted(b.allocation.items(), key=lambda x: x[1])
                for key, addr in sorted_alloc:
                    res.append(f"  [0x{addr:04X}] | Logic: {key}")
        res.append("="*80)
        return "\n".join(res)
