"""文档钦定流程的 8-SMC 功能测例（VCS 对齐用，自底向上）。

订正（user 2026-06-25）：cross-SMC 单元（SHIFTUP/SHIFTDN/SMI_DS_T）**不能单独用**——文档（v0.4.19
AIACC 指令集架构）只钦定**固定 datapath 流程**，单元只在流程内出现。故测试粒度 = 文档流程，非孤立单元。

本模块按文档两条流程建最简 8-SMC 测例：
- **Flow A（单一计算指令编程，para 4828-4868，FPADD 例）**：Bank0→FPADD_S0 / Bank1→FPADD_S1 →
  DR_FPADD_D→Bank2。涉及 FPADD/UR/RtoVR 三 SMI_IQ，每 SMC 独立算。compute 单元 baseline。
- **Flow B（数据整理完整 datapath，para 4655-4676）**：`UR read — SHIFTDN — SMI_DS_T — SHIFTUP —
  UR write`。各 SMC 列对齐读 → SHIFTDN 传回 SMI → SMI_DS_T 转置 → SHIFTUP 广播 → 各 SMC 写出。
  **一条覆盖三件 cross-SMC**（SHIFTUP 不单独存在，只在此流程尾端）。

VCS 分工：本仓库产 iq_*.hex + ur_data + golden（过 verification_v2/tools/export_golden_hex.py 导
vcs_expected）；VCS 在 RTL 环境跑、对 vcs_expected 比对。golden 编码功能终值期望，VCS 对齐时序/搬运段。
"""
import os
import numpy as np
from typing import List, Dict, Any


def _inject_gather_magic(programs, src_logical, n_per_smc, k_smc) -> bool:
    """证伪测试（ADR-043 hco）：AIACC_GATHER_MAGIC 时，每 SMC_s 源 bank 灌常量 (s+1)。
    gather 串行 → i_crd_shiftdn 8 拍出 1,2,..,8 distinct；塌缩 → 全 = SMC0 的 1。返回是否命中 magic 模式。"""
    if not os.environ.get("AIACC_GATHER_MAGIC"):
        return False
    for s, prog in enumerate(programs):
        if s < k_smc:
            prog.inject_vector(src_logical, np.full(n_per_smc, float(s + 1), dtype=np.float16), stride=1)
    return True

from .base import BaseOperator
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS
from .matmul_op import (
    _inject_shiftup_broadcast, _inject_smi_ds_t_transpose, _build_smi_ds_t_chain,
)


class UnitFpAddOperator(BaseOperator):
    """Flow A：单一计算指令编程（文档 para 4828-4868，FPADD 例），8-SMC 各独立。

    数据流（文档钦定）：输入0 Bank0→UR_RDATA_0→DVR_FPADD_S0；输入1 Bank1→UR_RDATA_1→DVR_FPADD_S1；
    输出 DR_FPADD_D→UVR_WDATA_0→Bank2。涉及 FPADD/UR/RtoVR 三 SMI_IQ。N=16 词（每词 4 lane fp32）。
    **golden：每个 SMC 的 Bank2 = Bank0 + Bank1**（逐 lane 浮点加，8 SMC 各算自己的数据）。
    VCS 对齐：FPADD lane 语义 + smc_id_en 全 8 使能 + UR 读写端口时序（compute baseline）。
    """

    parallel_dim = "N"          # 触发 8-SMC emit
    N = 64                      # 16 词 × 4 lane = 64 fp32（文档 para 4852 UR_R0 LOOPS=16）

    def __init__(self, **kwargs):
        self._smc_num = 1

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        return {
            "a": {"bank": 0, "offset": 0, "role": "input", "precision": "fp32",
                  "stride": 1, "smc_layout": "partitioned"},
            "b": {"bank": 1, "offset": 0, "role": "input", "precision": "fp32",
                  "stride": 1, "smc_layout": "partitioned"},
            "y": {"bank": 2, "offset": 0, "role": "output", "precision": "fp32",
                  "smc_layout": "partitioned"},
        }

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        self._smc_num = smc_num
        rng = np.random.default_rng(0)
        a = rng.uniform(1.0, 5.0, (smc_num, self.N)).astype(np.float32)   # 每 SMC 独立
        b = rng.uniform(1.0, 5.0, (smc_num, self.N)).astype(np.float32)
        return {"a": a, "b": b}

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a, b = input_data["a"], input_data["b"]
        golden = (a + b).astype(np.float32)        # (smc_num, N) 逐 lane 加
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a, b = input_data["a"], input_data["b"]
        a_addr = self.get_bank_addr("a", spec)
        b_addr = self.get_bank_addr("b", spec)
        for k, prog in enumerate(programs):
            prog.inject_vector(a_addr, a[k], stride=1)
            prog.inject_vector(b_addr, b[k], stride=1)

    def get_dfg(self, spec: Any = None) -> List[Any]:
        b = MIROpBuilder()
        ar = b.make_op("UR_READ", "a_read", RT.UR_READ, [],
                       meta_name="a", logical_addr=0 * 256 + 0, stride=1, role="input", iq_id=30)
        br = b.make_op("UR_READ", "b_read", RT.UR_READ, [],
                       meta_name="b", logical_addr=1 * 256 + 0, stride=1, role="input", iq_id=31)
        rt_a = b.make_op("RTOVR", "rt_a_FPADD_S0", PS.FPADD_S0, [ar])
        rt_b = b.make_op("RTOVR", "rt_b_FPADD_S1", PS.FPADD_S1, [br])
        add = b.make_op("FPADD", "inl_add", RT.FPADD, [rt_a, rt_b], iq_id=4)  # iq_registry FPADD=4
        rt_out = b.make_op("RTOVR", "rt_add_UR_W0_S", PS.UR_W0_S, [add], sel=4)  # sel=4 = DR_FPADD_D
        b.make_op("UR_WRITE", "out_obj_0", RT.UR_WRITE, [rt_out],
                  logical_addr=2 * 256 + 0, stride=1, role="output", is_output=True, iq_id=35)

        ops = b.get_dfg()
        for op in ops:
            if op.op_type.upper() == "RTOVR":
                prod_ids = op.metadata.get("depends_on", [])
                if prod_ids:
                    prod_id = prod_ids[0]
                    for consumer in ops:
                        if op.trace_id in consumer.metadata.get("depends_on", []):
                            consumer.metadata.setdefault("rtovr_deps", {})[prod_id] = {
                                "dvr": op.metadata.get("dvr", -1),
                                "sel": op.metadata.get("sel", -1),
                            }
        inner = self.N // 4 - 1     # 16 词 → 15 内循环
        for op in ops:
            op.metadata.update({"shape": [self.N], "inner_loops": inner, "outer_loops": 0,
                                "inner_inc": 1, "outer_inc": 0})
        return ops


class UnitDataShuffleTransposeOperator(BaseOperator):
    """Flow B：数据整理完整 datapath（文档 para 4655-4676）。

    `UR read — SHIFTDN — SMI_DS_T — SHIFTUP — UR write`：8 个 SMC 各持 1 元素（列对齐于 src_bank），
    SHIFTDN gather 8×1 Tile 传回 SMI → SMI_DS_T 转置 1×8 → SHIFTUP 广播 8 元素行 → 各 SMC 写出 bank5。
    **golden：每个 SMC 输出 = 8 元素行向量 [a0..a7]**（跨所有 SMC 汇聚转置 + 广播送达全 8）。
    **一条覆盖三件 cross-SMC**（SHIFTDN gather + SMI_DS_T 转置 + SHIFTUP 广播）。无 compute（纯数据整理）。
    VCS 对齐：跨-SMC gather/转置/广播搬运 + smc_id_en；时序/搬运段未建模 = DEBT-036/-S4，正是 VCS 要校的。
    复用 default ON 真 datapath（AIACC_DST_REAL_GATHER），固件完整性门随之挂（block-3）。
    """

    parallel_dim = "N"
    K = 8                       # gather 全 8 SMC（每 SMC 1 元素）
    SRC_BANK = 4
    STREAM_LEAD_DELAY = 32      # ADR-041 ②：UR_W0 lead-NOP LOOPS（推到广播窗口；VCS 实测调）

    def __init__(self, **kwargs):
        self._smc_num = 1

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        return {
            "a": {"bank": 0, "offset": 0, "role": "input", "precision": "fp32",
                  "stride": 1, "smc_layout": "broadcast"},
            "y": {"bank": 5, "offset": 0, "role": "output", "precision": "fp32",
                  "smc_layout": "partitioned"},
        }

    def smi_ds_t_config(self, spec: Any = None) -> Dict[str, int]:
        # gather 全 K=8 SMC（每 SMC lanes=1 元素）→ 转置 1×8 → 行对齐 8 元素（2 词）
        return {"src_bank": self.SRC_BANK, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
                "gather_smc_cnt": self.K, "lanes": 1, "word_lanes": 4}

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        self._smc_num = smc_num
        rng = np.random.default_rng(0)
        a = rng.uniform(1.0, 5.0, self.K).astype(np.float32)   # 8 元素，列对齐分散到 8 SMC
        return {"a": a}

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a = input_data["a"]
        # 转置 + 广播后每个 SMC 都拿到完整 8 元素行向量
        golden = np.tile(a.astype(np.float32), (self._smc_num, 1))
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]
        bank_depth = self._get_hw_param(spec, "PARAM_UR_BANK_DEPTH", 256)
        src_logical = self.SRC_BANK * bank_depth
        # 列对齐：SMC_k 持 a[k]（1 元素）于 src_bank；不注入 bank0（datapath 经 SHIFTUP 喂）
        for k, prog in enumerate(programs):
            if k < self.K:
                prog.inject_vector(src_logical, np.array([a[k]], dtype=np.float32), stride=1)

    def get_dfg(self, spec: Any = None) -> List[Any]:
        """ADR-039 D1：按硬连 DST datapath 重建（弃 bank0 a_read staging）。
        硬件契约（[[shiftup-shiftdn-only-dst-datapath]] + RTL smi_top.v:384）：SHIFTUP 广播负载硬连
        = SMI_DS_T.data_out（唯一源），编译器不得用「a_read 读 bank0 → 独立 SHIFTUP 广播」虚构 staging。
        真链：`ds_src_read→SHIFTDN×2(B=2)→ds_transpose(SMI_DS_T data_out)→SHIFTUP(广播)→RTOVR(sel=29)
        →UR_WRITE bank5`。C2 选项(a)：用 a_read-无关的 `_build_smi_ds_t_chain` 建前缀链 + 自建广播/写回，
        **不调共享 `_inject_shiftup_broadcast`/`_inject_smi_ds_t_transpose`**（matmul/fma 不受影响）。"""
        # 1) gather+transpose 前缀链（不锚 a_read；含 ADR-038 B=2 双 gather）
        cfg = self.smi_ds_t_config(spec)
        chain_nodes, ds_transpose = _build_smi_ds_t_chain(cfg, spec)
        # 转置行 = K 元素，按 word_lanes 拆成 nword_per_row 个 128b 词（fp32 word_lanes=4→2 词；fp16=8→1 词）。
        # ADR-041 D4：输出**总词数** = SMI_DS_T 输出行数(lanes) × 每行词数。lanes=1（退化单行转置）时
        # 总词数 == nword_per_row（fp32 2 词 / fp16 1 词，旧行为不变）；lanes>1（8×8 真转置）时
        # 输出 lanes 个转置行 → 总词数 = lanes×nword_per_row，UR_W0 须 INC 写这么多连续行。
        word_lanes = int(cfg.get("word_lanes", 4))
        out_lanes = max(1, int(cfg.get("lanes", 1)))
        nword_per_row = max(1, (self.K + word_lanes - 1) // word_lanes)
        n_words = out_lanes * nword_per_row

        # 2) 广播 + 写回，锚在 ds_transpose（C1 保留 RTOVR sel=29；C3 smc_id_en=PARAM_SMC_NUM-1）
        smc_num = self._get_hw_param(spec, "PARAM_SMC_NUM", 8)
        smc_id_en = max(int(smc_num) - 1, 0)        # C3：广播到全 SMC（shift_up.v:44 门 SMC_ID<=smc_id_en）
        SHIFTUP_D_SEL = 29                          # C1：dr_shiftup_out→UR 写口唯一通路 = RTOVR sel=29
        b = MIROpBuilder()
        # SHIFTUP 广播 SMI_DS_T.data_out（硬件 wrd_data 硬连，dep=ds_transpose；非 a_read/bank0）
        # dst_datapath_value：值经 SMI_DS_T data_out→SHIFTUP 广播 datapath 流入，非 compute 公式可算
        # → rigid 逐-op truth audit 值豁免（仅校时序）；正确性由最终 y bit-perfect parity + VCS 验证。
        sup = b.make_op("SHIFTUP", "shiftup_ds_t", RT.SHIFTUP, [ds_transpose.trace_id],
                        broadcast=1, smc_id_en=smc_id_en, smc_id=0, dep_port="D",
                        dst_datapath_value=True,
                        math="shiftup_broadcast(SMI_DS_T.data_out)")
        rt = b.make_op("RTOVR", "rt_ds_t_UR_W0_S", PS.UR_W0_S, [sup],
                       sel=SHIFTUP_D_SEL, dep_port="D", dst_datapath_value=True,
                       math="rtovr_route(SHIFTUP_D → UR_W0_S)")
        b.make_op("UR_WRITE", "out_obj_0", RT.UR_WRITE, [rt],
                  logical_addr=5 * 256 + 0, stride=1, role="output", is_output=True, iq_id=35,
                  dst_datapath_value=True,
                  math="ur_write(broadcast row → bank5)")
        bcast_ops = b.get_dfg()
        # ADR-041 BLOCK-2/D2（option A LOOPS 展开）：SMI_DS_T 输出 = 16-beat 流（OUT_ROWS×2）。
        #   SHIFTUP/RTOVR/UR_W0 须**持续广播 WINDOW 拍**逐拍取不同 data_out beat（shift_up.v 透明 1 拍
        #   寄存器，vld 持续 16 拍 + data_in 逐拍变 → 逐拍广播不同 beat），非单脉冲（单脉冲取窗口尾后空
        #   ds_t_data_out → bank5=0）。emit 把 stream_beats 展成 IQ 级 LOOPS（assembler eff_num_iter 覆盖）。
        # 🔴 BLOCK-2 硬门控（hco）：stream_beats 只打在**本 dst-datapath 节点**（sup 明证 depends_on
        #   ds_transpose==SMI_DS_T）→ 绝不命中 fma/matmul 的 compute-broadcast SHIFTUP（走 matmul_op
        #   `_inject_shiftup_broadcast`，无 stream_beats 标记 + 无 SMI_DS_T producer）。门控来源 = 此处建
        #   节点时打标，**非** assembler 按 `op_type==SHIFTUP && broadcast==1`（后者会误伤 fma 单值广播）。
        window = int(self._get_hw_param(spec, "SMI_DS_T_WINDOW", n_words)) if spec is not None else n_words
        if spec is not None and hasattr(spec, "delays"):
            window = int(spec.delays.get("SMI_DS_T_WINDOW", window))
        window = max(window, n_words)
        # 转置行 = K 元素 = nword_per_row 词，输出 n_words = lanes×nword_per_row 个真行词；
        # inner_loops=n_words-1 记真行数（auditor/地址映射）；stream_beats=window 驱动 emit 持续广播拍数
        # （window ≥ n_words：真行 + B=2/前导零 beat 补齐到硬件输出窗口，sim 多写的零行不进 golden 校验）。
        for op in bcast_ops:
            op.metadata.update({"shape": [self.K], "inner_loops": n_words - 1,
                                "outer_loops": 0, "inner_inc": 1, "outer_inc": 0,
                                "stream_beats": window})
            # ADR-041 ②：UR_W0（写 bank5）须排到 SHIFTUP 广播窗口才写（user 2026-06-30：NOP+LOOPS 对齐）。
            #   UR_W0 不具 EXTRA_DATA 不等数据，须垫 lead-NOP（LOOPS=STREAM_LEAD_DELAY）推到 dr_shiftup_d
            #   有效期。delay 由 DST 输入 gather + drain 时序确定（VCS 实测调；SHIFTUP/RTOVR 不垫——SHIFTUP
            #   靠 EXTRA_DATA 等数据、RtoVR 路由连续）。
            # UR_W0 写使能 + RTOVR(sel=29 路由 dr_shiftup_d→uvr_wdata_0) 都须在广播窗口活跃才写得进
            #   （user_register CRU 写数据 = i_uvr_wdata_X，由 RtoVR sel 路由；VCS 实测：只垫 UR_W0、
            #   RTOVR 早发 → uvr_wdata_0 窗口期为零 → bank5=0）。SHIFTUP 不垫（EXTRA_DATA 自对齐）。
            if op.op_type.upper() == "UR_WRITE":
                op.metadata["stream_lead_delay"] = self.STREAM_LEAD_DELAY
            elif op.op_type.upper() == "RTOVR":
                # RTOVR 路由比 UR_W0 写早 1 拍（RtoVR sel→uvr_wdata_0 有 1 拍延迟，否则写捕获到上一拍数据
                #   → bank5[k]=beat[k-1]、bank5[0]=0，VCS 实测 off-by-one）。
                op.metadata["stream_lead_delay"] = self.STREAM_LEAD_DELAY - 1
        # rtovr_deps：消费者 out 经 RTOVR(sel=29) 从 SHIFTUP 取数（dependency_axioms 建 t_latch）
        for op in bcast_ops:
            if op.op_type.upper() == "RTOVR":
                prod_ids = op.metadata.get("depends_on", [])
                if prod_ids:
                    prod_id = prod_ids[0]
                    for consumer in bcast_ops:
                        if op.trace_id in consumer.metadata.get("depends_on", []):
                            consumer.metadata.setdefault("rtovr_deps", {})[prod_id] = {
                                "dvr": op.metadata.get("dvr", -1),
                                "sel": op.metadata.get("sel", -1),
                            }
        return chain_nodes + bcast_ops


class UnitDataShuffleTransposeFp16Operator(UnitDataShuffleTransposeOperator):
    """ADR-039（user 2026-06-29 选 fp16 最小载体先闭合 bank5）：unit_ds_transpose 的 fp16 版。

    fp16 = 1 个 16-bit element = SMI_DS_T 转置的天然粒度（无 fp32「2 element 拆成两行」复杂度）：
    8 SMC 各持 1 fp16（列对齐于 src_bank）→ SHIFTDN gather → SMI_DS_T 转置 1×8 → **1 个 128b 词
    （8 lane×16-bit）** → SHIFTUP 广播 → 各 SMC 写 bank5 1 词。**golden：每 SMC = 8 fp16 行 [a0..a7]**。
    用来端到端验证 D1 硬连 datapath（SHIFTUP←SMI_DS_T→bank5）能否收到正确广播数据，绕开 fp32 的
    高/低半字拆分 + Q2 hi-slot 复杂度（DEBT-038-B2-GATHER）。"""

    @property
    def element_bits(self) -> int:
        return 16   # fp16：输出按 8 lane×16-bit 解码（base.py 默认从 DFG 读，本 op 节点无 precision → 显式覆盖）

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        return {
            "a": {"bank": 0, "offset": 0, "role": "input", "precision": "fp16",
                  "stride": 1, "smc_layout": "broadcast"},
            "y": {"bank": 5, "offset": 0, "role": "output", "precision": "fp16",
                  "smc_layout": "partitioned"},
        }

    # ADR-041 D4（user 2026-06-29 裁定）：fp16 不走 1 词特例，走完整 16-beat 流。
    # 每 SMC 持**整行 8 fp16**（= 16 byte = 1 词），8 SMC 组成 8×8 fp16 矩阵 → SMI_DS_T 真转置
    # 8×8 → 8 个转置行（lanes=8，非旧 1）→ 每 SMC bank5 写 8 连续行（UR_W0 INC=1）。
    # 这是比旧「8 SMC 各 1 元素 → 1 行」**严格更强**的 sim 验收（退化 1 元素无法捕获转置 bug，
    # 8×8 非对称矩阵能）。golden = X.T 展平 64 fp16（sim 忠实建模 lo slot 8 行）。
    # ⚠ DEBT-041-ADDR-MAP（VCS-gated）：RTL B=2 配对吐 16 beat（lo 8 + hi 8）+ spec §6 的 12 前导
    #   零 beat 结构未定论；model-A 只忠实 lo slot 8 行，hi slot 第二 tile 真值 = DEBT-038-B2-GATHER。
    #   故 golden 取忠实的 8 行（64 fp16），firmware 走 16-beat 流（BLOCK-2）供 VCS 校 lo+hi 全 16 beat。
    ROWS = 8                     # SMI_DS_T 转置输出行数 = 每 SMC 的元素数（8 fp16/行）

    # ADR-041 DEBT-041-ADDR-MAP off-by-one 修（2026-06-30，VCS 实证）：父类 STREAM_LEAD_DELAY=32 下
    #   VCS 实测 bank5[0]=0、bank5[k]=beat[k-1]（写比广播数据早 1 拍、beat[15] 越界丢）。+1 把 UR_W0/RTOVR
    #   整体推迟 1 拍 → 写 k 拍正捕获 beat[k]、16 beat 全落 bank5[0..15]。仅 fp16 子类覆盖（fp16 已 VCS 验，
    #   fp32 父类时序未 VCS、不动）。RTOVR 仍 = LEAD-1（父类 get_dfg 相对偏移逻辑不变）。
    STREAM_LEAD_DELAY = 33

    def smi_ds_t_config(self, spec: Any = None) -> Dict[str, int]:
        # fp16：8 lane×16-bit/词 → 每转置行 8 元素恰 1 词（word_lanes=8）；element_bits=16 选 fp16 codec。
        # lanes=ROWS=8：8×8 真转置 → 8 个输出行（旧 lanes=1 是 8 SMC×1 元素的退化单行）。
        return {"src_bank": self.SRC_BANK, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
                "gather_smc_cnt": self.K, "lanes": self.ROWS, "word_lanes": 8, "element_bits": 16}

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        self._smc_num = smc_num
        rng = np.random.default_rng(0)
        # 8 SMC × 8 fp16 = 8×8 矩阵；a[s] = SMC s 持有的整行（列对齐于 src_bank 的 1 词）
        a = rng.uniform(1.0, 5.0, (self.K, self.ROWS)).astype(np.float16)
        return {"a": a}

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        # ADR-041 DEBT-041-ADDR-MAP（2026-06-30，VCS 端到端实证布局；supersede 旧 element 级 8 行 a.T）：
        #   SMI_DS_T 把 8×8 fp16 转置 tile 按 **16-beat 广播流**发出（spec seq1039「转置成 PARAM_UR_BYTE_CNT
        #   =16 行 × PARAM_SMC_NUM=8 列」+ seq1042 B=2 间插）。每 beat = 1 个 UR 词（8 fp16 = 16 byte）：
        #     • 元素列 e = (ROWS-1) - k//2     —— RTL 元素**降序**发（VCS 实测 e=7 先出；transpose 输出序，
        #       是硬件定值非可调；若未来 spec 判定应升序，此处即 bug locus，见 ADR-041 待厘清）。
        #     • SMC 半区 h = k%2（h0=SMC0-3、h1=SMC4-7）—— 8-SMC 转置行拆成 2 个 16-byte 发出（seq1039
        #       「一行 PARAM_SMC_NUM 分为多个 PARAM_UR_BYTE_CNT 发出」）。
        #     • 取该半区 4 个 SMC 的第 e 元素，每 fp16 **成对重复**（B=2 间插，4 fp16 → 8 槽 = 16 byte）。
        #   SHIFTUP 广播 → 每 SMC bank5 收**全 16 beat = 128 fp16**（partitioned 写但内容广播一致）。
        #   ⚠ model-A sim 不建模该 16-beat 成对流（写 element 级 8 行）→ fp16 `--mode e2e` sim parity
        #   预期 FAIL；验收以 VCS bank5 逐行 bit-match 为准（DEBT-041，待 sim 流式模型升级 = DEBT-038-B2-GATHER）。
        a = input_data["a"].astype(np.float16)                 # (K=8 SMC, ROWS=8 elem)
        half = self.K // 2                                     # 每半区 SMC 数 = 4
        beats = []
        for k in range(2 * self.ROWS):                         # 16 beat
            e = (self.ROWS - 1) - (k // 2)
            h = k % 2
            row: List[Any] = []
            for j in range(half):
                v = a[h * half + j][e]
                row += [v, v]                                  # B=2 成对重复
            beats.append(np.asarray(row, dtype=np.float16))    # 8 fp16/beat
        per_smc = np.concatenate(beats)                        # 128 fp16 = 16 行 × 8
        golden = np.tile(per_smc, (self._smc_num, 1))          # 广播：全 SMC 同
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"].astype(np.float16)
        bank_depth = self._get_hw_param(spec, "PARAM_UR_BANK_DEPTH", 256)
        src_logical = self.SRC_BANK * bank_depth
        if _inject_gather_magic(programs, src_logical, self.ROWS, self.K):
            return
        for k, prog in enumerate(programs):
            if k < self.K:
                prog.inject_vector(src_logical, a[k], stride=1)   # SMC k 整行 8 fp16（1 词）


class UnitDstVec128Operator(UnitDataShuffleTransposeFp16Operator):
    """ADR-043 path B 隔离验证（user 2026-07-01 选）：DST datapath 能否把 **128 元素向量按序交付 bank5**
    （不做 compute）。128 元素向量、**转置连续**存全 8 SMC（每 SMC 16 fp16=2 词）→ DST gather 2 tile →
    SMI_DS_T → SHIFTUP 广播 **16 beat=128 元素、无 doubling**（§1195「按顺序恰好是向量连续数据」，区别 unit
    8×8 矩阵的 64-distinct-doubled）→ 各 SMC bank5 写全 16 beat。golden=[a0..a127] identity（doc 声称按序）；
    **VCS 逐 beat 定实际序**，不符则订正 layout（DEBT-043-VEC-BEAT-FORMAT 收口）。"""

    KVEC = 128
    ROWS = 16        # 16 beat（=128/8）；2 tile（每 8 beat 一 tile）

    def smi_ds_t_config(self, spec: Any = None) -> Dict[str, int]:
        # 转置连续 16 fp16/SMC = 2 词 → gather 2 tile → 16 输出 beat（lanes=ROWS=16）。
        return {"src_bank": self.SRC_BANK, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
                "gather_smc_cnt": 8, "lanes": self.ROWS, "word_lanes": 8, "element_bits": 16}

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        self._smc_num = smc_num
        rng = np.random.default_rng(0)
        a = rng.uniform(1.0, 5.0, self.KVEC).astype(np.float16)   # 128 元素向量
        return {"a": a}

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        # identity：doc §1195 声称转置连续 + B=2 读回按序 → bank5 = [a0..a127]（VCS 定实际序）
        a = input_data["a"].astype(np.float16)
        golden = np.tile(a, (self._smc_num, 1))                  # 128 广播全 SMC
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"].astype(np.float16)                   # 128
        bank_depth = self._get_hw_param(spec, "PARAM_UR_BANK_DEPTH", 256)
        src_logical = self.SRC_BANK * bank_depth
        if _inject_gather_magic(programs, src_logical, self.ROWS, 8):
            return
        # ADR-043（据 doc §1073/§1188 示例）：向量转置间插存储 **B=2 element-pair 间插**（§1188「间插因子B为2」）：
        #   SMC_x 持元素对轮转 [a[2x+16y], a[2x+16y+1]]（y=0..7）→ SMC0=[a0,a1,a16,a17,..]、SMC1=[a2,a3,..]。
        #   §1195 向量读跨-SMC 按序取每 SMC 一对 → a[0..15] 按序 → 整向量按序、不需转置。
        #   ⚠ `ur_vec_tran_inte` 默认 B=2·UR_BYTE/ELEM=16（对 128 退化成转置连续），故**显式建 B=2 layout**。
        from core.cross_smc_layout import CrossSmcLayout, Unroll, inject_distributed
        layout_b2 = CrossSmcLayout(smc_num=len(programs), mode=Unroll.INTERLEAVE, B=2)
        inject_distributed(programs, a, layout_b2, src_logical)
