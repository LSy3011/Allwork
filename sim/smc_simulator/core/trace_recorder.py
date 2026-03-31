import os
import numpy as np
from typing import Dict, List, Any

class TraceRecorder:
    """
    AIACC V5.13 高保真物理审计追踪器 (Audit Wall)。
    100% 对齐 aiacc_sin_physical_timeline.txt 理论时序样式。
    """
    def __init__(self):
        self.history = {} # name -> { cycle -> {is_firing, latency} }
        self.last_pcs = {} # name -> int
        self.max_cycle = 0
        self.is_enabled = False

    def enable(self): self.is_enabled = True
    def record_cycle(self, cycle: int, iqs: Dict[str, Any], *args, **kwargs):
        self.record(cycle, iqs)

    def record(self, cycle: int, iqs: Dict[str, Any], regs=None, smc=None):
        if not self.is_enabled: return
        self.max_cycle = max(self.max_cycle, cycle)
        
        for name, iq in iqs.items():
            if name not in self.history: 
                self.history[name] = {}
                self.last_pcs[name] = -1
            
            # --- 🛸 核心：物理点火脉冲探测 (Edge Trigger) ---
            curr_pc = getattr(iq, 'pc', 0)
            is_fetching_dly = getattr(iq, 'delay_active', False)
            
            # 只有当 PC 发生真正跳转且不是在延时等待中，才判定为新指令点火 (Firing)
            # 并且必须确保这一拍解出的指令不是 NOP (通过 _instruction_valid 判定)
            is_valid = getattr(iq, 'is_valid', lambda: False)()
            
            is_firing = is_valid and (curr_pc != self.last_pcs[name]) and not is_fetching_dly
            
            if is_firing:
                self.history[name][cycle] = {
                    'head': True,
                    'latency': getattr(iq, 'latency', 1)
                }
                self.last_pcs[name] = curr_pc
            elif is_fetching_dly:
                # 记录处于延时态
                self.history[name][cycle] = {'dly': True}

    def save_to_timeline_ascii(self, file_path: str):
        if not self.history: return
        def get_symbols(unit_name):
            u = unit_name.upper()
            if "MUL" in u:   return 'm', '*'
            if "ADD" in u:   return 'a', '+'
            if "RTOVR" in u: return 'r', '-'
            if "UR-R" in u:  return 'L', '~'
            if "UR-W" in u:  return 'S', '~'
            return 'x', '='

        lines = ["AIACC PHYSICAL AUDIT WALL (PULSE SYNCHRONIZED)"]
        header = f"{'iq_name':<15} |"
        for cyc in range(self.max_cycle + 1):
            if cyc % 10 == 0: header += f"{cyc:<3}"
            else: header += " . "
        lines.append(header)
        
        sorted_units = sorted(self.history.keys())
        matrix = {u: [" . "] * (self.max_cycle + 1) for u in sorted_units}
        
        for unit in sorted_units:
            head_char, tail_char = get_symbols(unit)
            for cyc, state in self.history[unit].items():
                if state.get('head'):
                    matrix[unit][cyc] = f" {head_char} "
                    lat = state.get('latency', 1)
                    # 填充物理时延尾迹 (Tail Propagation)
                    for t in range(1, lat):
                        if cyc + t <= self.max_cycle:
                            # 尾迹不能覆盖未来的点火点
                            if not self.history[unit].get(cyc + t, {}).get('head'):
                                matrix[unit][cyc+t] = f" {tail_char} "
                elif state.get('dly'):
                    # 如果是在等待延迟（如 Compactor 产生的 dly），画 d
                    if matrix[unit][cyc] == " . ":
                        matrix[unit][cyc] = " d "
                        
        for unit in sorted_units:
            row_str = "".join(matrix[unit])
            # 🛸 只打印活跃单元 (非空行)
            if " . " * (self.max_cycle + 1) != row_str:
                lines.append(f"{unit:<15} |{row_str}")

        lines.append("-" * len(header))
        occ_line = f"{'occupancy %':<15} |"
        for cyc in range(self.max_cycle + 1):
            act = sum(1 for k in sorted_units if matrix[k][cyc] != " . ")
            pct = int((act / len(sorted_units)) * 100) if sorted_units else 0
            if pct == 0: occ_line += " . "
            else: occ_line += f"{pct:>2} "
        lines.append(occ_line)

        with open(file_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"📊 [TRACE-SUCCESS] Pulse Audit diagram locked at: {file_path}")
