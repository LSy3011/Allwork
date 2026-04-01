import os
import numpy as np
from typing import Dict, List, Any

class TraceRecorder:
    """
    AIACC V5.13 经典语义渲染版 TraceRecorder (100% 样式兼容)。
    对齐旧版符号：a/+/m/*/L/S/r/~，支持物理时延铺展。
    """
    def __init__(self):
        self.history = {} # unit_name -> [ {cycle_data} ]
        self.max_cycle = 0
        self.is_enabled = False

    def enable(self): self.is_enabled = True

    def record_cycle(self, cycle: int, iqs: Dict[str, Any], *args, **kwargs):
        """[Legacy Kernel Interface] 映射至标准的 record 方法。"""
        self.record(cycle, iqs)

    def record(self, cycle: int, iqs: Dict[str, Any], regs=None, smc=None):
        if not self.is_enabled: return
        self.max_cycle = max(self.max_cycle, cycle)
        
        for name, iq in iqs.items():
            if name not in self.history: self.history[name] = []
            
            # 捕获物理执行状态与单元时延
            state = {
                'cycle': cycle,
                'is_valid': getattr(iq, 'is_valid', lambda: False)(),
                'is_fetching_dly': getattr(iq, 'delay_active', False),
                'latency': getattr(iq, 'latency', 1),
                'raw_hex': iq.memory[iq.pc] if iq.pc < len(iq.memory) else 0
            }
            self.history[name].append(state)

    def save_to_timeline_ascii(self, file_path: str):
        if not self.history: return
        
        # 1. 语义映射
        def get_symbols(unit_name):
            u = unit_name.upper()
            if "MUL" in u:   return 'm', '*'
            if "ADD" in u:   return 'a', '+'
            if "RTOVR" in u: return 'r', '-'
            if "UR-R" in u:  return 'L', '~'
            if "UR-W" in u:  return 'S', '~'
            return 'X', '='

        # 2. 构造缓冲区
        lines = []
        header = f"{'iq_name':<15} |"
        for cyc in range(self.max_cycle + 1):
            if cyc % 10 == 0: header += f"{cyc:<3}"
            else: header += " . "
        lines.append(header)
        
        sorted_units = sorted(self.history.keys())
        # 创建时序矩阵
        matrix = {u: ["."] * (self.max_cycle + 1) for u in sorted_units}
        active_counts = [0] * (self.max_cycle + 1)
        
        for unit in sorted_units:
            head_char, tail_char = get_symbols(unit)
            
            for state in self.history[unit]:
                cyc = state['cycle']
                if cyc > self.max_cycle: continue
                
                # 物理点火触发完整覆盖
                if state['is_valid'] or state['is_fetching_dly']:
                    lat = state['latency']
                    # 绘制首拍
                    if matrix[unit][cyc] == ".":
                        matrix[unit][cyc] = head_char
                        active_counts[cyc] += 1
                        
                    # 根据物理时延填充延续拍
                    for offset in range(1, lat):
                        target_c = cyc + offset
                        if target_c <= self.max_cycle:
                            if matrix[unit][target_c] == ".":
                                matrix[unit][target_c] = tail_char
                                active_counts[target_c] += 1
                                
        # 3. 渲染矩阵
        for unit in sorted_units:
            row_str = " ".join(matrix[unit])
            lines.append(f"{unit:<15} | {row_str} ")
        
        # 4. 统计占用率 (Occupancy)
        lines.append("-" * len(header))
        total_ports = len(sorted_units) if sorted_units else 1
        occ_line = f"{'occupancy %':<15} |"
        for cyc in range(self.max_cycle + 1):
            occ = int((active_counts[cyc] / total_ports) * 100)
            if occ == 0: occ_line += " . "
            elif occ >= 100: occ_line += " 99"
            else: occ_line += f"{occ:>2} "
        lines.append(occ_line)

        with open(file_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        
        # 🛸 物理硬探针 (Physical Probe)
        try:
            with open("/tmp/aiacc_final_trace.txt", "w") as f:
                f.write("\n".join(lines) + "\n")
            print(f"📊 [PROBE-SUCCESS] Evidence written to /tmp/aiacc_final_trace.txt")
        except:
            print(f"❌ [PROBE-FAIL] Cannot write probe.")
            
        print(f"📊 [TRACE-SAVE] Standard Legacy Style saved to: {os.path.abspath(file_path)}")
