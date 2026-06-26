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
from typing import List, Sequence


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

    用法（ADR-036 S2 流式 step-driven）：
      reset() → evaluate({'valid':1,'tile':cols}) 触发整-tile 转置（核 = transpose_tile 静态纯函数）
      → 每拍 tick() 经固定 latency pipeline 发一个 DATA_OUT 行词 → get_outputs() 读接口寄存器。
    （ADR-033 的 gather/transpose 原子 gate 方法已随 apply_smi_ds_t backdoor 删，S3-c。）
    """

    def __init__(self, smc_num: int, lanes: int = 4, word_lanes: int = 4, element_bits: int = 32,
                 latency: int = 0, pairs: int = 2):
        """smc_num = PARAM_IN_LINE_NUM = PARAM_SMC_NUM；
        lanes = PARAM_IN_LINE_BYTE_CNT / ELEMENT_BYTE_NUM（fp32: 16/4 = 4，即每 UR 行的元素数）；
        element_bits = 元素位宽（fp32=32 / fp16=16，ADR-035 D2 codec dispatch）；
        word_lanes = 每个 128-bit DATA_OUT 词的元素数（= 128//element_bits：fp32=4 / fp16=8）——
        表596 DATA_OUT=128b，一个转置行（smc_num 元素）拆 ceil(smc_num/word_lanes) 个词发出（para959）。
        latency = ADR-036 S2 流式接口：转置触发到第一个 DATA_OUT 词的固定延迟拍数（取
        pipeline_delays.yaml 的 SMI_DS_T；0 = 现状即时，Phase B 设 PARAM_SMC_NUM*2=16）。
        pairs = ADR-038（hco BLOCK#2）：配对槽数 = RTL smi_ds_t.v 的 issue_cnt 门（V1.0 无条件
        配对 → pairs=2）。镜像 start_out=both_issued(issue_cnt==pairs)：须填满 pairs 个 slot
        （lo+hi）才出 DATA_OUT；仅 lo slot ready 时 hi_ready=0 → 不出数，堵 model-A 单 tile 假绿。"""
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
        self.pairs = max(1, int(pairs))   # ADR-038：镜像 RTL issue_cnt 配对门（V1.0=2）
        self._lanes_from_word, self._word_from_lanes = _lane_codec(element_bits)
        # ADR-034 S2-D1：表596 接口寄存器（替 apply_smi_ds_t backdoor 的真 step-driven 实体）
        self.data_out = 0          # 表596 DATA_OUT(128b)：当前发出的转置行词
        self.data_out_vld = 0      # 表596 DATA_OUT_VLD：本拍 DATA_OUT 有效
        self.inst_done = 0         # 表596 INST_DONE：一条指令的全部输出发完
        self.reset()

    def reset(self):
        # ADR-036 S2 流式接口状态：转置后行词 FIFO + 固定 latency 倒计时
        self._out_words: List = []     # 转置产出的 (off, word) 待发队列（经 latency 后逐拍出）
        self._latency_ctr = -1         # -1 = 空闲未触发；>=0 = latency 倒计时中
        self._stream_started = False   # 本条指令的转置已触发
        self._slots: List = []         # ADR-038：累积的配对 tile（lo, hi, ...）；满 pairs 才触发
        self.data_out = 0
        self.data_out_vld = 0
        self.inst_done = 0

    # ── ADR-036 S3-c：删 ADR-034 S2-D1 step-driven 接口（load_tile/execute/emit_step/busy）──
    #   已被本节 ADR-036 S2 流式接口（evaluate/tick/get_outputs）取代、仅旧测试调用、无 production 路径，
    #   死代码移除（user 2026-06-15 裁定「先删纯死 gate 方法」）。转置数值核仍由 transpose_tile（静态纯核，
    #   下方）+ S2 流式 evaluate/tick 覆盖。
    #   ── 续 S3-c（2026-06-25）：apply_smi_ds_t backdoor 已删 → 其 gather/transpose/is_ready/missing
    #   gate 方法本轮一并删（matmul_q_dst_full 已去误继承的 smi_ds_t_config、不再经 fallback）。

    # ── ADR-036 S2：流式 stepped 单元接口（evaluate/tick/get_outputs，固定 latency pipeline）──
    #   对标 units/fp32addtree8to1_unit.py 的 evaluate→tick→get_outputs 约定。语义：转置是
    #   burst（输入整 tile→transpose_tile 纯核，无 gather-gate）→ 行词装 FIFO，经 `latency` 拍
    #   固定延迟后**每拍发 1 个 DATA_OUT 词**（线速行片输出）。真 SHIFTDN 逐拍 drain 列片 = DEBT-S4，
    #   本阶段输入整 tile 提供、由 latency pipeline 建模时序（S4 VCS 校准精确拍数）。
    def evaluate(self, inputs: dict, cycle: int = 0):
        """流式驱动：inputs={'valid':bool, 'tile':columns}。每个 valid tile = 一次 issue 填一个 slot
        （lo, hi, ...）。**ADR-038（镜像 RTL start_out）：须填满 pairs(=2) 个 slot 才触发出数**——
        仅 lo slot 时 hi_ready=0 → start_out=0 → 不出 DATA_OUT（堵 model-A 单 tile 假绿）。
        触发后转置 lo slot 整 tile → 装填行词 FIFO + 启动 latency 倒计时；已触发则忽略重复 valid（幂等）。"""
        if self._stream_started or not inputs.get("valid"):
            return
        tile = inputs.get("tile")
        if tile is None:
            return
        self._slots.append(tile)
        if len(self._slots) < self.pairs:         # lo 已填、hi 未到 → hi_ready=0，不触发
            return
        # 填满 pairs 个 slot（issue_cnt==pairs）→ start_out。lo slot(slots[0]) 决定输出行；
        # hi slot 仅满足配对门（unit_ds_transpose hi 不进 golden；真 matmul hi 第二半列 = DEBT-038-B2-GATHER）。
        lo_tile = self._slots[0]
        rows = self.transpose_tile(lo_tile)       # 纯转置核，无 is_ready/raise gate
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

    # ── ADR-036 S3-c：删 gather/is_ready/missing/transpose（实例 gate 方法）──────────────
    #   这 4 个方法是 apply_smi_ds_t model-A backdoor 的 gather-before-scatter gate 实现，随 backdoor
    #   一并删除。run 中真 datapath 走流式 evaluate/tick（转置核 = transpose_tile 静态纯函数，无 gate）。

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
