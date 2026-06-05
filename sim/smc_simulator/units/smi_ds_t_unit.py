"""SMI_DS_T（Data Shuffle_Transpose）跨-SMC 转置中心模块。

ADR-033 Stage-A（model-A 整链原子段）。忠实设计文档 v0.4.19 表596-598 + [seq1037-1051]：
  - 输入 = 对所有 SMC 进行 UR 读、经 **SHIFTDN gather** 传回 SMI 的 Tile
    （`PARAM_IN_LINE_NUM=PARAM_SMC_NUM` 行 × `PARAM_IN_LINE_BYTE_CNT=PARAM_UR_BYTE_CNT` 列）；
  - **转置**为 `PARAM_UR_BYTE_CNT` 行 × `PARAM_SMC_NUM` 列的 Tile；
  - 输出经 **SHIFTUP scatter** 广播 → 形成
    `UR read — SHIFTDN — SMI_DS_T — SHIFTUP — UR write` 的数据整理完整 datapath。

与 DS（表585-588，每-SMC、挂 STB↔LDB、仅单 SMC 内 UR→UR）**是两个不同模块**——本模块是
SMI 中心单实例、跨-SMC（输入来自 SHIFTDN gather、输出去 SHIFTUP scatter）。详见 ADR-033 / 分歧台账 D-01。

Stage-A 取 model-A：**只建模转置数值功能 + gather 完成序约束**（hco B-BLOCK-1 裁定整链原子段）。
interleave（intlv_*）4 并发时序 / CRI 字段位宽 / INST_DONE 握手 = DEBT-033-DST-CTRL / -FIELDS /
NEEDS-USER-FACT-033-DSTDONE，本轮 defer。
"""
from typing import List, Optional, Sequence


class SmiDsTUnit:
    """SMI 中心跨-SMC 转置单元（非每-SMC，无 SMC_ID）。

    用法（cluster 模型 A 原子段）：
      reset() → 对每个 SMC gather(smc_id, ur_row) → transpose() 返回行对齐 Tile。
    transpose() 在 gather 未到齐时抛错（gate: gather-before-scatter ordering）。
    """

    def __init__(self, smc_num: int, lanes: int = 4):
        """smc_num = PARAM_IN_LINE_NUM = PARAM_SMC_NUM；
        lanes = PARAM_IN_LINE_BYTE_CNT / ELEMENT_BYTE_NUM（fp32: 16/4 = 4，即每 UR 行的元素数）。"""
        if smc_num < 1:
            raise ValueError(f"smc_num must be >= 1, got {smc_num}")
        if lanes < 1:
            raise ValueError(f"lanes must be >= 1, got {lanes}")
        self.smc_num = smc_num
        self.lanes = lanes
        self.reset()

    def reset(self):
        # gather 缓冲：_buf[smc_id] = 该 SMC 的 UR 行（lanes 个元素）；None = 尚未 gather
        self._buf: List[Optional[List]] = [None] * self.smc_num
        self._out: Optional[List[List]] = None

    def gather(self, smc_id: int, row: Sequence):
        """SHIFTDN gather：收一个 SMC 的一行 UR 数据（lanes 个元素）。"""
        if not (0 <= smc_id < self.smc_num):
            raise ValueError(f"smc_id {smc_id} out of range [0,{self.smc_num})")
        row = list(row)
        if len(row) != self.lanes:
            raise ValueError(f"gather row from SMC {smc_id} has {len(row)} elems, expected lanes={self.lanes}")
        self._buf[smc_id] = row

    def is_ready(self) -> bool:
        """是否所有 SMC 的 gather 都到齐。"""
        return all(r is not None for r in self._buf)

    def missing(self) -> List[int]:
        """尚未 gather 的 SMC id 列表。"""
        return [i for i, r in enumerate(self._buf) if r is None]

    def transpose(self) -> List[List]:
        """gather 全到齐后转置并返回行对齐 Tile（lanes 行 × smc_num 列）。

        **未到齐则抛 RuntimeError**——把「scatter 必须等全部 gather 完成」钉成硬约束
        （hco B-BLOCK-1 / D6 gather-before-scatter ordering），防 model-A 静默乱序读到 stale/零。
        """
        if not self.is_ready():
            raise RuntimeError(
                f"SMI_DS_T transpose() called before gather complete: missing SMC {self.missing()}")
        self._out = self.transpose_tile(self._buf)
        return self._out

    @staticmethod
    def transpose_tile(tile: Sequence[Sequence]) -> List[List]:
        """纯转置（无状态、可独立验证）：tile[smc][lane] → out[lane][smc]。

        忠实文档 [seq1037-1039]：输入 Tile = SMC_NUM 行 × UR_BYTE 列，转置为 UR_BYTE 行 × SMC_NUM 列。
        每个输出行（smc_num 个元素）= 从所有 SMC 各取同一 lane → 一条行对齐数据，供 SHIFTUP 广播。
        """
        n = len(tile)
        if n == 0:
            return []
        w = len(tile[0])
        for s, r in enumerate(tile):
            if len(r) != w:
                raise ValueError(f"ragged tile: row {s} has {len(r)} elems, expected {w}")
        return [[tile[s][l] for s in range(n)] for l in range(w)]
