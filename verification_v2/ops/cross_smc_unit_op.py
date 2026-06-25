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
import numpy as np
from typing import List, Dict, Any

from .base import BaseOperator
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS
from .matmul_op import _inject_shiftup_broadcast, _inject_smi_ds_t_transpose


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
        b = MIROpBuilder()
        # a_read = 广播读转置结果（SHIFTUP 喂）；8 元素 = 2 词 → inner_loops=1
        ar = b.make_op("UR_READ", "a_read", RT.UR_READ, [],
                       meta_name="a", logical_addr=0 * 256 + 0, stride=1, role="input", iq_id=30)
        rt = b.make_op("RTOVR", "rt_a_UR_W0_S", PS.UR_W0_S, [ar])  # sel→SHIFTUP_D=29 by inject
        b.make_op("UR_WRITE", "out_obj_0", RT.UR_WRITE, [rt],
                  logical_addr=5 * 256 + 0, stride=1, role="output", is_output=True, iq_id=35)

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
        # 转置行 = 8 元素 = 2 词，inner_loops=1
        for op in ops:
            op.metadata.update({"shape": [self.K], "inner_loops": self.K // 4 - 1,
                                "outer_loops": 0, "inner_inc": 1, "outer_inc": 0})
        ops = _inject_shiftup_broadcast(ops, {"a"}, spec)
        ops = _inject_smi_ds_t_transpose(ops, self.smi_ds_t_config(spec), spec)
        return ops
