"""SMI_DS_T（Data Shuffle_Transpose）跨-SMC 转置中心模块。

ADR-033 Stage-A（model-A 整链原子段）。忠实设计文档 v0.4.19 表596-598 + [seq1037-1051]：
  - 输入 = 对所有 SMC 进行 UR 读、经 **SHIFTDN（下行收集）** 传回 SMI 的 Tile
    （`PARAM_IN_LINE_NUM=PARAM_SMC_NUM` 行 × `PARAM_IN_LINE_BYTE_CNT=PARAM_UR_BYTE_CNT` 列）；
  - **转置**为 `PARAM_UR_BYTE_CNT` 行 × `PARAM_SMC_NUM` 列的 Tile；
  - 输出经 **SHIFTUP broadcast** 广播 → 形成
    `UR read — SHIFTDN — SMI_DS_T — SHIFTUP — UR write` 的数据整理完整 datapath。

与 DS（表585-588，每-SMC、挂 STB↔LDB、仅单 SMC 内 UR→UR）**是两个不同模块**——本模块是
SMI 中心单实例、跨-SMC（输入来自 SHIFTDN（下行收集）、输出去 SHIFTUP broadcast）。详见 ADR-033 / 分歧台账 D-01。

Stage-A 取 model-A：**只建模转置数值功能 + gather 完成序约束**（hco B-BLOCK-1 裁定整链原子段）。
interleave（intlv_*）4 并发时序 / CRI 字段位宽 / INST_DONE 握手 = DEBT-033-DST-CTRL / -FIELDS /
NEEDS-USER-FACT-033-DSTDONE，本轮 defer。
"""
import struct
from typing import List, Optional, Sequence


def fp32_lanes_from_word(word: int, lanes: int) -> List[float]:
    """128-bit UR 词 → lanes 个 fp32（lane l 在 bit [l*32:l*32+32]，标准 IEEE754 位模式）。
    与 emitter `_handle_program_data` 打包 + `_read_sim_output` 解包约定一致。"""
    out = []
    for l in range(lanes):
        bits = (word >> (l * 32)) & 0xFFFFFFFF
        out.append(struct.unpack("<f", struct.pack("<I", bits))[0])
    return out


def word_from_fp32_lanes(vals: Sequence[float]) -> int:
    """lanes 个 fp32 → 128-bit UR 词（lane l 在 bit [l*32]）。"""
    w = 0
    for l, v in enumerate(vals):
        bits = struct.unpack("<I", struct.pack("<f", float(v)))[0]
        w |= (bits & 0xFFFFFFFF) << (l * 32)
    return w


# ── ADR-035 D2：FP16 codec（8 lane × 16-bit / 128b 词，元素字节连续打包）──────────
def fp16_lanes_from_word(word: int, lanes: int) -> List[float]:
    """128-bit UR 词 → lanes 个 fp16（lane l 在 bit [l*16:l*16+16]，IEEE754 half）。
    每元素 2 byte 连续占 1 lane（byte 转置 ≡ element 转置的前提，ADR-035 BLOCK-1 不变式）。"""
    out = []
    for l in range(lanes):
        bits = (word >> (l * 16)) & 0xFFFF
        out.append(struct.unpack("<e", struct.pack("<H", bits))[0])
    return out


def word_from_fp16_lanes(vals: Sequence[float]) -> int:
    """lanes 个 fp16 → 128-bit UR 词（lane l 在 bit [l*16]，IEEE754 half）。"""
    w = 0
    for l, v in enumerate(vals):
        bits = struct.unpack("<H", struct.pack("<e", float(v)))[0]
        w |= (bits & 0xFFFF) << (l * 16)
    return w


def _lane_codec(element_bits: int):
    """按 element_bits 选 (lanes_from_word, word_from_lanes) codec（ADR-035 D2）。"""
    if element_bits == 16:
        return fp16_lanes_from_word, word_from_fp16_lanes
    if element_bits == 32:
        return fp32_lanes_from_word, word_from_fp32_lanes
    raise ValueError(f"unsupported element_bits={element_bits}（仅 16/32）")


class SmiDsTUnit:
    """SMI 中心跨-SMC 转置单元（非每-SMC，无 SMC_ID）。

    用法（cluster 模型 A 原子段）：
      reset() → 对每个 SMC gather(smc_id, ur_row) → transpose() 返回行对齐 Tile。
    transpose() 在 gather 未到齐时抛错（gate: gather-before-scatter ordering）。
    """

    def __init__(self, smc_num: int, lanes: int = 4, word_lanes: int = 4, element_bits: int = 32,
                 latency: int = 0):
        """smc_num = PARAM_IN_LINE_NUM = PARAM_SMC_NUM；
        lanes = PARAM_IN_LINE_BYTE_CNT / ELEMENT_BYTE_NUM（fp32: 16/4 = 4，即每 UR 行的元素数）；
        element_bits = 元素位宽（fp32=32 / fp16=16，ADR-035 D2 codec dispatch）；
        word_lanes = 每个 128-bit DATA_OUT 词的元素数（= 128//element_bits：fp32=4 / fp16=8）——
        表596 DATA_OUT=128b，一个转置行（smc_num 元素）拆 ceil(smc_num/word_lanes) 个词发出（para959）。
        latency = ADR-036 S2 流式接口：转置触发到第一个 DATA_OUT 词的固定延迟拍数（取
        pipeline_delays.yaml 的 SMI_DS_T；0 = 现状即时，Phase B 设 PARAM_SMC_NUM*2=16）。"""
        if smc_num < 1:
            raise ValueError(f"smc_num must be >= 1, got {smc_num}")
        if lanes < 1:
            raise ValueError(f"lanes must be >= 1, got {lanes}")
        # ADR-035 BLOCK-2：word_lanes 与 element_bits 不得矛盾（128b 词 / 元素位宽）
        if word_lanes != 128 // element_bits:
            raise ValueError(
                f"word_lanes={word_lanes} 与 element_bits={element_bits} 矛盾"
                f"（须 word_lanes == 128//element_bits == {128 // element_bits}）")
        self.smc_num = smc_num
        self.lanes = lanes
        self.word_lanes = word_lanes
        self.element_bits = element_bits
        self.latency = max(0, int(latency))
        self._lanes_from_word, self._word_from_lanes = _lane_codec(element_bits)
        # ADR-034 S2-D1：表596 接口寄存器（替 apply_smi_ds_t backdoor 的真 step-driven 实体）
        self.data_out = 0          # 表596 DATA_OUT(128b)：当前发出的转置行词
        self.data_out_vld = 0      # 表596 DATA_OUT_VLD：本拍 DATA_OUT 有效
        self.inst_done = 0         # 表596 INST_DONE：一条指令的全部输出发完
        self.reset()

    def reset(self):
        # gather 缓冲（CRD_SHIFTDN）：_buf[smc_id] = 该 SMC 的 UR 行（lanes 个元素）；None = 尚未 gather
        self._buf: List[Optional[List]] = [None] * self.smc_num
        self._out: Optional[List[List]] = None
        # DATA_OUT 发送队列：[(dst_word_offset, word_value)]
        self._emit_queue: List = []
        # ADR-036 S2 流式接口状态：转置后行词 FIFO + 固定 latency 倒计时
        self._out_words: List = []     # 转置产出的 (off, word) 待发队列（经 latency 后逐拍出）
        self._latency_ctr = -1         # -1 = 空闲未触发；>=0 = latency 倒计时中
        self._stream_started = False   # 本条指令的转置已触发
        self.data_out = 0
        self.data_out_vld = 0
        self.inst_done = 0

    # ── ADR-034 S2-D1：真 step-driven 数据流（表596 接口）──────────────────────
    def load_tile(self, columns: Sequence[Sequence]):
        """CRD_SHIFTDN：latch 经 SHIFTDN（下行收集） 传回的 Tile（columns[smc] = 该 SMC 的 UR 行 lanes 元素）。
        逐 SMC 走 gather()（行宽 / 越界校验 = gather gate）。"""
        self.reset()
        for s, row in enumerate(columns):
            self.gather(s, row)

    def execute(self) -> int:
        """gather 到齐 → 转置 → 构建 DATA_OUT 发送队列（一行 smc_num 元素拆 ceil/word_lanes 个 128b 词）。
        未到齐抛 RuntimeError（gather-before-scatter gate，表596 语义）。返回待发 word 数。"""
        rows = self.transpose()    # 内部 is_ready gate：未到齐抛错
        nword = (self.smc_num + self.word_lanes - 1) // self.word_lanes
        self._emit_queue = []
        for m in range(self.lanes):
            for w in range(nword):
                chunk = rows[m][w * self.word_lanes:(w + 1) * self.word_lanes]
                self._emit_queue.append((m * nword + w, self._word_from_lanes(chunk)))
        self.inst_done = 0
        return len(self._emit_queue)

    def emit_step(self):
        """发一个 DATA_OUT 词（表596 DATA_OUT/DATA_OUT_VLD 拉高）；队空则 INST_DONE 拉高。
        返回 (dst_word_offset, word_value) 或 None（已发完）。"""
        if not self._emit_queue:
            self.data_out_vld = 0
            self.inst_done = 1
            return None
        off, word = self._emit_queue.pop(0)
        self.data_out = word
        self.data_out_vld = 1
        if not self._emit_queue:
            self.inst_done = 1
        return off, word

    def busy(self) -> bool:
        """是否还有 DATA_OUT 待发（scatter 进行中）。"""
        return bool(self._emit_queue)

    # ── ADR-036 S2：流式 stepped 单元接口（evaluate/tick/get_outputs，固定 latency pipeline）──
    #   对标 units/fp32addtree8to1_unit.py 的 evaluate→tick→get_outputs 约定。语义：转置是
    #   burst（输入整 tile→transpose_tile 纯核，无 gather-gate）→ 行词装 FIFO，经 `latency` 拍
    #   固定延迟后**每拍发 1 个 DATA_OUT 词**（线速行片输出）。真 SHIFTDN 逐拍 drain 列片 = DEBT-S4，
    #   本阶段输入整 tile 提供、由 latency pipeline 建模时序（S4 VCS 校准精确拍数）。
    def evaluate(self, inputs: dict, cycle: int = 0):
        """流式驱动：inputs={'valid':bool, 'tile':columns}。valid 触发时转置整 tile（无 gate）→
        装填行词 FIFO + 启动 latency 倒计时。非触发拍无副作用（幂等，已触发则忽略重复 valid）。"""
        if self._stream_started or not inputs.get("valid"):
            return
        tile = inputs.get("tile")
        if tile is None:
            return
        rows = self.transpose_tile(tile)          # 纯转置核，无 is_ready/raise gate
        nword = (self.smc_num + self.word_lanes - 1) // self.word_lanes
        self._out_words = []
        for m in range(self.lanes):
            for w in range(nword):
                chunk = rows[m][w * self.word_lanes:(w + 1) * self.word_lanes]
                self._out_words.append((m * nword + w, self._word_from_lanes(chunk)))
        self._latency_ctr = self.latency          # 固定延迟倒计时（0=下拍即出）
        self._stream_started = True
        self.inst_done = 0

    def tick(self):
        """推移固定 latency pipeline：倒计时未到→DATA_OUT_VLD=0（气泡）；到点后每拍 pop 1 词→
        DATA_OUT/DATA_OUT_VLD；FIFO 空→INST_DONE。返回本拍发的 (off,word) 或 None。"""
        if not self._stream_started:
            self.data_out_vld = 0
            return None
        if self._latency_ctr > 0:                 # 仍在固定延迟窗口内（气泡，无输出）
            self._latency_ctr -= 1
            self.data_out_vld = 0
            return None
        if not self._out_words:                   # 已发完
            self.data_out_vld = 0
            self.inst_done = 1
            return None
        off, word = self._out_words.pop(0)        # 线速：每拍发 1 行词
        self.data_out = word
        self.data_out_vld = 1
        if not self._out_words:
            self.inst_done = 1
        return off, word

    def get_outputs(self) -> dict:
        """表596 接口寄存器快照（供 cluster 读 DATA_OUT → scatter）。"""
        return {"DATA_OUT": self.data_out, "DATA_OUT_VLD": self.data_out_vld,
                "INST_DONE": self.inst_done}

    def gather(self, smc_id: int, row: Sequence):
        """SHIFTDN（下行收集）：收一个 SMC 的一行 UR 数据（lanes 个元素）。"""
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
