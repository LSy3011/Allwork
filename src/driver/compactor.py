from typing import List, Dict, Tuple

class Compactor:
    """
    AIACC 极致时空压缩引擎 (v5: 指令层嵌套版)。
    支持基于 1D INC, LOOPS, DLY 基础上的 2D/3D EMBED 嵌套合成。
    """
    @staticmethod
    def compress_iq_stream(stream: List[Tuple[int, int, bool]], iq_conf) -> List[Dict]:
        """
        输入: (time, payload, is_rigid)
        输出: 具备 loops, dly, inc, embed, embed_end 属性的指令包合集
        """
        # Pass 1: 1D 线性折叠 (INC / Plain Loops)
        packets = Compactor._pass1_linear_folding(stream, iq_conf)
        
        # Pass 2: 2D 镜像折叠 (EMBED 合成预留位)
        # 如果 iq_conf.BITS_EMBED > 0，这里我们可以尝试对 Packet 序列执行拓扑合一
        final_packets = Compactor._pass2_semantic_folding(packets, iq_conf)
        
        return final_packets

    @staticmethod
    def _pass1_linear_folding(stream: List[Tuple[int, int, bool]], iq_conf) -> List[Dict]:
        """
        实现 1D INC 剥离与合并逻辑 (基于 V4 原型)。
        """
        if not stream: return []
        res = []
        i = 0
        max_loops = (1 << iq_conf.BITS_LOOPS) - 1 if iq_conf.EN_LOOPS else 0
        while i < len(stream):
            t_curr, p_curr, _ = stream[i]
            loops, stride = 0, 0
            if iq_conf.EN_INC and i + 1 < len(stream):
                t_next, p_next, _ = stream[i+1]
                if t_next == t_curr + 1:
                    stride = p_next - p_curr
                    if abs(stride) < (1 << (iq_conf.BITS_INC - 1)):
                        j = i + 1
                        while j < len(stream) and loops < max_loops:
                            t_j, p_j, _ = stream[j]
                            if t_j == t_curr + (j - i) and p_j == p_curr + stride * (j - i):
                                loops += 1
                                j += 1
                            else: break
            if loops == 0 and iq_conf.EN_LOOPS:
                j = i + 1
                while j < len(stream) and loops < max_loops:
                    t_j, p_j, _ = stream[j]
                    if t_j == t_curr + (j - i) and p_j == p_curr:
                        loops += 1
                        j += 1
                    else: break
            cmd = {"t": t_curr, "payload": p_curr, "loops": loops, "dly": 0, "inc": stride, "embed": 0, "embed_end": 0}
            i += (loops + 1)
            if i < len(stream) and iq_conf.EN_DLY and loops == 0:
                gap = stream[i][0] - (t_curr + 1)
                max_dly = (1 << iq_conf.BITS_DLY) - 1
                if 0 < gap <= max_dly: cmd["dly"] = gap
            res.append(cmd)
        return res

    @staticmethod
    def _pass2_semantic_folding(packets: List[Dict], iq_conf) -> List[Dict]:
        """
        实现多维嵌套折叠 (ADR-005: Topology Folding)。
        算法：
           1. 检查两个相邻的 1D Packet (P, P+1) 的 loops 是否一致。
           2. 检查它们的 payload_base 之差 (Inter-row Stride) 是否在该 IQ 允许的 INC 或 IMAGE 范围内。
        """
        if not packets or not getattr(iq_conf, "BITS_EMBED", 0):
            return packets
            
        res = []
        i = 0
        while i < len(packets):
            curr = packets[i]
            # 搜索重复模式 (如 2D 卷积的一行)
            match_count = 0
            if i + 1 < len(packets):
                next_p = packets[i+1]
                if curr["loops"] == next_p["loops"] and curr["inc"] == next_p["inc"]:
                    stride = next_p["payload"] - curr["payload"]
                    # 这是一个候选 2D 结构！
                    # 我们尝试计算在此模式下有多少行重复
                    j = i + 1
                    while j < len(packets):
                        if packets[j]["loops"] == curr["loops"] and \
                           packets[j]["inc"] == curr["inc"] and \
                           packets[j]["payload"] == curr["payload"] + stride * (j - i):
                            match_count += 1
                            j += 1
                        else: break
            
            if match_count > 0:
                # --- 注入 EMBED 1 (Header) ---
                # 在真实硬件中，Header 应该描述图像的 Stride/Pad。
                # 由于这是 LIR 层，我们暂时将它标记为一个配置指令。
                header_cmd = {
                    "t": curr["t"],
                    "payload": 0, # 这里应根据模块填充 Stride meta
                    "loops": match_count, # 嵌套循环次数
                    "dly": 0,
                    "inc": stride, # 外部 Stride
                    "embed": 1,
                    "embed_end": 0
                }
                res.append(header_cmd)
                # 注入 EMBED 0 (Body) 指令，注意它的 loops 仍然是原来的 (如 7)
                body_cmd = curr.copy()
                body_cmd["embed"] = 0
                body_cmd["embed_end"] = 1 # 标记本层(L1+L0)在该指令后可能结束
                res.append(body_cmd)
                
                i += (match_count + 1)
            else:
                res.append(curr)
                i += 1
        return res
