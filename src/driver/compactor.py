from typing import List, Dict, Tuple

class Compactor:
    """
    AIACC 极致时空压缩引擎 (v8: 多维张量感知版)。
    支持硬件级 7 层循环嵌套 (EMBED 0-6)。
    """
    @staticmethod
    def compress_iq_stream(stream: List[Tuple[int, int, bool]], iq_conf) -> List[Dict]:
        # Step 1: 1D 基础折叠 (最内层)
        packets = Compactor._pass1_linear_folding(stream, iq_conf)
        if len(packets) < 2: return packets
        
        # Step 2: 7D 步进探测 (寻找递归 Stride)
        # 我们寻找整齐划一的嵌套结构
        dim_chain = Compactor._detect_strides(packets)
        
        if not dim_chain:
            return packets
            
        # Step 3: 根据探测到的 Chain 发射 Header 序列
        # dim_chain 格式: [(loops, stride, depth), ...]
        res = []
        for loops, stride, depth in reversed(dim_chain):
            # 只有 depth > 0 的才是 Header
            if depth > 0:
                header = {
                    "t": packets[0]["t"],
                    "payload": 0,
                    "loops": loops,
                    "dly": 0,
                    "inc": stride,
                    "embed": depth,
                    "embed_end": 0
                }
                res.append(header)
        
        # 最后加上 Body (最内层指令)
        body = packets[0].copy()
        # 设置 EMBED_END 掩码 (所有探测到的维度都在此结束)
        mask = 0
        for _, _, depth in dim_chain:
            if depth > 0:
                mask |= (1 << (depth - 1))
        body["embed_end"] = mask
        res.append(body)
        
        return res

    @staticmethod
    def _detect_strides(packets: List[Dict]) -> List[Tuple[int, int, int]]:
        """
        探测指令包序列中的 N 维步进规律。
        返回: [(count_dim0, stride_dim0, 0), (count_dim1, stride_dim1, 1), ...]
        """
        # 我们假设最内层 (d0) 已经通过 1D 折叠成了一个 packet 的 loops
        # 现在我们要看这个 packet 是不是在重复。
        # 简化版：我们假设整个序列是一个完美的 N-D 张量
        chain = []
        
        # 1. 记录最内层 (EMBED 0)
        # 注意：d0 的 loops 是由 Pass1 计算出来的。
        # 这里记录的是它“自身”的次数，其实不需要记入 chain 的升维部分，直接体现在 Body 即可。
        
        curr_seq = packets
        depth = 1
        while len(curr_seq) > 1 and depth <= 6:
            # 寻找序列的最小循环节
            # 如果序列整体能被平分为 N 份，且每份全等（payload 呈 stride 变化）
            found_dim = False
            for factor in range(2, len(curr_seq) + 1):
                if len(curr_seq) % factor == 0:
                    block_size = len(curr_seq) // factor
                    pattern = curr_seq[:block_size]
                    stride = curr_seq[block_size]["payload"] - pattern[0]["payload"]
                    
                    # 校验全等性
                    match = True
                    for k in range(1, factor):
                        start = k * block_size
                        for offset in range(block_size):
                            if curr_seq[start+offset]["payload"] != pattern[offset]["payload"] + stride * k or \
                               curr_seq[start+offset]["loops"] != pattern[offset]["loops"]:
                                match = False; break
                        if not match: break
                    
                    if match:
                        # 命中了！将此维度记录
                        chain.append((factor - 1, stride, depth))
                        curr_seq = pattern # 缩减到上一层继续识别
                        depth += 1
                        found_dim = True
                        break
            if not found_dim: break
            
        return chain

    @staticmethod
    def _pass1_linear_folding(stream, iq_conf) -> List[Dict]:
        """与 V5/V6/V7 逻辑一致。兼容 tuple 和 dict 输入。"""
        if not stream: return []
        
        # 归一化：兼容 tuple (t, payload, flag) 和 dict {"t":, "payload":} 两种格式
        normalized = []
        for item in stream:
            if isinstance(item, dict):
                normalized.append((item["t"], item["payload"], False))
            else:
                normalized.append(item)
        stream = normalized
        
        res = []
        i = 0
        next_expected_t = 0 # 🛰️ 全局物理对位点
        max_loops = (1 << iq_conf.BITS_LOOPS) - 1 if iq_conf.EN_LOOPS else 0
        
        while i < len(stream):
            t_curr, p_curr, _ = stream[i]
            
            # --- 🛸 核心修复：物理气泡注入 (Gap Filling) ---
            # 如果 SMT 算出的点火时间 t_curr 超过了预期时间，说明中间有气泡。
            # 补齐有效位 VALID=0 的物理指令。
            while next_expected_t < t_curr:
                res.append({
                    "t": next_expected_t, 
                    "payload": 0, 
                    "loops": 0, "dly": 0, "inc": 0, 
                    "embed": 0, "embed_end": 0,
                    "valid": 0  # 明确标记为 NOP
                })
                next_expected_t += 1
            
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
                                loops += 1; j += 1
                            else: break
            
            if loops == 0 and iq_conf.EN_LOOPS:
                j = i + 1
                while j < len(stream) and loops < max_loops:
                    t_j, p_j, _ = stream[j]
                    if t_j == t_curr + (j - i) and p_j == p_curr:
                        loops += 1; j += 1
                    else: break
            
            cmd = {
                "t": t_curr, "payload": p_curr, 
                "loops": loops, "dly": 0, "inc": stride, 
                "embed": 0, "embed_end": 0,
                "valid": 1 # 物理点火
            }
            i += (loops + 1)
            # 物理时间轴推进包含循环占据的周期
            next_expected_t = t_curr + (loops + 1)
            res.append(cmd)
            
        return res
