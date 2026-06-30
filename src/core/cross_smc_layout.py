"""跨-SMC 分散布局模型（按架构设计文档 v0.4.19 §1066-1107 规划实现）。

硬件模型（§1066）：每个 SMC 有独立 banks；所有 SMC 中相同地址的 Sub_UR 构成一个 UR，
UR 是由多个**间插的地址空间**构成。一个逻辑张量的 **D3 维展开到多个 SMC**（每 SMC 持一片）。

本模块实现 D3 维的两种展开（§1069-1075）+ Transformer 1D/2D 典型存储格式（§1090-1099），
把原本各算子在 `inject_data_per_smc` 里手搓的跨-SMC 分散，收编成文档钦定的一等模型：
  · 连续展开（§1071）：SMC_x 持 Tensor[x·⌈D3/N⌉ : (x+1)·⌈D3/N⌉]
  · 间插展开（§1073）：SMC_x 持 B 个连续元素的块、按 SMC round-robin（块跨度 B·N），间插因子 B
N = PARAM_SMC_NUM。这是大 K 能放进「每 SMC 独立 32KB」的布局基础（K=4096 分 8 SMC → 每 512）。

⚠ 本模块只建**分散布局的元素↔(SMC, 片内序号)映射**（storage placement）+ roundtrip 自洽；
与 sim/emitter 的 per-SMC bank 地址整合（让算子声明格式即自动放置）= 下一步。
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple

import numpy as np


class Unroll(Enum):
    CONTIGUOUS = "连续展开"   # §1071
    INTERLEAVE = "间插展开"   # §1073


@dataclass(frozen=True)
class CrossSmcLayout:
    """D3 维跨-SMC 分散布局（§1069-1075）。smc_num=PARAM_SMC_NUM。"""
    smc_num: int
    mode: Unroll
    B: int = 1                 # 间插因子（仅 INTERLEAVE 用；典型 1/2/2·UR_BYTE_CNT/ELEM_BYTE）

    def __post_init__(self):
        if self.smc_num < 1:
            raise ValueError("smc_num 须 ≥1")
        if self.mode is Unroll.INTERLEAVE and self.B < 1:
            raise ValueError("间插因子 B 须 ≥1")

    # ---- 单元素 placement：全局 D3 序号 → (SMC, 片内 D3_sub 序号) ----
    def placement(self, d3: int, D3: int) -> Tuple[int, int]:
        if not (0 <= d3 < D3):
            raise IndexError(f"d3={d3} 越界 [0,{D3})")
        N = self.smc_num
        if self.mode is Unroll.CONTIGUOUS:
            chunk = -(-D3 // N)            # ceil(D3/N)，§1071
            return d3 // chunk, d3 % chunk
        # INTERLEAVE §1073：块大小 B、按 SMC round-robin、块跨度 B·N
        B = self.B
        block = d3 // B                   # 第几个 B-块
        smc = block % N
        y = block // N                    # 该 SMC 的第几个块
        b = d3 % B                         # 块内偏移
        return smc, y * B + b

    # ---- 分散：1D 张量 → 每 SMC 一片（List[np.ndarray]，长度 smc_num）----
    def distribute(self, tensor: np.ndarray) -> List[np.ndarray]:
        D3 = len(tensor)
        buckets: List[List] = [[] for _ in range(self.smc_num)]
        # 按 D3_sub 序保序：先算每元素 (smc, sub)，再按 sub 排进桶
        placed: List[List[Tuple[int, int]]] = [[] for _ in range(self.smc_num)]
        for d3 in range(D3):
            smc, sub = self.placement(d3, D3)
            placed[smc].append((sub, d3))
        for s in range(self.smc_num):
            for sub, d3 in sorted(placed[s]):
                buckets[s].append(tensor[d3])
        return [np.array(b, dtype=tensor.dtype) for b in buckets]

    # ---- gather：每 SMC 片 → 复原 1D 张量（roundtrip 逆）----
    def gather(self, per_smc: List[np.ndarray], D3: int) -> np.ndarray:
        if len(per_smc) != self.smc_num:
            raise ValueError(f"per_smc 须 {self.smc_num} 片，得 {len(per_smc)}")
        out = np.empty(D3, dtype=per_smc[0].dtype)
        for d3 in range(D3):
            smc, sub = self.placement(d3, D3)
            out[d3] = per_smc[smc][sub]
        return out

    def per_smc_len(self, D3: int) -> List[int]:
        lens = [0] * self.smc_num
        for d3 in range(D3):
            lens[self.placement(d3, D3)[0]] += 1
        return lens


# ---- Transformer 1D 张量典型存储格式（§1090-1092）----
def ur_vec_norm(smc_num: int) -> CrossSmcLayout:
    """§1090 非转置：全部存一个 Sub_UR（SMC_0）。= 连续展开退化（chunk=D3，全落 SMC_0）。
    注：连续展开 chunk=ceil(D3/N) 时 SMC_0 只拿 chunk 个；真 Norm = 不展开。用 smc_num=1 表达。"""
    return CrossSmcLayout(smc_num=1, mode=Unroll.CONTIGUOUS)


def ur_vec_tran_succ(smc_num: int) -> CrossSmcLayout:
    """§1091 转置连续 UR_Vec_Tran_Succ_D3inner：W 维 D3 连续展开。"""
    return CrossSmcLayout(smc_num=smc_num, mode=Unroll.CONTIGUOUS)


def ur_vec_tran_inte(smc_num: int, ur_byte_cnt: int = 16, element_byte: int = 2) -> CrossSmcLayout:
    """§1092 转置间插 UR_Vec_Tran_Inte_D3inner：W 维 D3 间插展开，典型 B=2·UR_BYTE_CNT/ELEMENT_BYTE。"""
    B = 2 * ur_byte_cnt // element_byte
    return CrossSmcLayout(smc_num=smc_num, mode=Unroll.INTERLEAVE, B=B)
