from typing import List, Dict, Tuple

class Compactor:
    """
    AIACC 压缩引擎 (v3: NOP-Specific Optimization)。
    严格遵循：NOP 指令 DLY 字段无效，长空隙必须由 NOP-LOOP 承接。
    """
    @staticmethod
    def compress_iq_stream(iq_stream: List[Tuple[int, int, bool]], iq_conf) -> List[Dict]:
        if not iq_stream: return []
        
        compressed = []
        max_loops = (1 << iq_conf.BITS_LOOPS) - 1 if iq_conf.EN_LOOPS else 0
        max_dly = (1 << iq_conf.BITS_DLY) - 1 if iq_conf.EN_DLY else 0
        
        i = 0
        while i < len(iq_stream):
            t_curr, payload_curr, _ = iq_stream[i]
            
            # --- 1. 业务循环合并 ---
            loops = 0
            while (i + 1 < len(iq_stream) and 
                   iq_stream[i+1][1] == payload_curr and 
                   iq_stream[i+1][0] == t_curr + loops + 1 and
                   loops < max_loops):
                loops += 1
                i += 1
            
            t_end = t_curr + loops
            dly = 0
            
            # --- 2. 衔接 Gap 处理 ---
            if i + 1 < len(iq_stream):
                t_next, _, _ = iq_stream[i+1]
                gap = t_next - (t_end + 1)
                
                if gap > 0:
                    # A. 如果是正常业务指令结尾，能带 DLY 吗？ 
                    # 假设非 NOP 阶段可以带 DLY 以微调 1-15 拍
                    if iq_conf.EN_DLY and gap <= max_dly and payload_curr != 0:
                        dly = gap
                        gap = 0
                    
                    # B. 暴力 NOP-LOOP 填充 (Gap > 15 或 Dly 不可用时)
                    if gap > 0:
                        # 先压入前面的指令
                        compressed.append({"payload": payload_curr, "loops": loops, "dly": dly, "t": t_curr})
                        
                        while gap > 0:
                            # 核心修复：NOP 指令 Dly 无效，1 个 NOP 字占 1 拍，通过 LOOP 拉长
                            # 每一行 NOP 最多填 max_loops + 1 拍
                            fill_loops = min(max_loops, gap - 1)
                            compressed.append({"payload": 0, "loops": fill_loops, "dly": 0, "t": -1}) 
                            gap -= (1 + fill_loops)
                        
                        i += 1
                        continue # 已完美填坑
            
            compressed.append({"payload": payload_curr, "loops": loops, "dly": dly, "t": t_curr})
            i += 1
            
        return compressed
