import copy
import os
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
from ir.lir.lir_op import LIROp, PhysicalInstruction, SMIHeader
from api.hw_spec import HardwareSpec

# ============ ADR-056 §B：L1/L2 受限通用准入（取代 ADR-055 A1 名称白名单） ============
# 准入凭据由配置面承载：YAML `experimental_unroll: true` 声明请求（L1，仅请求非能力证明）
# → pipeline_runner 物化为 UnrollAdmission 传入 assemble()；None = 未请求，全程零行为变化。
# L2 = §B.2 八闸门（G-op/G-II/G-slot/G-stride/G-addr/G-cap/G-stage/G-dp），任一失败 →
# STATIC_ADMISSION_REJECTED；禁止任何「失败即回落 embed」兜底（ADR-055 D1 铁律）。
# 名称级白名单 A1_UNROLL_ALLOWLIST 已删（ADR-056 §B.4 触点 1：名称不再构成准入依据；
# hco notes-7 复核其值域闭集在 assembler 外无消费方，删除安全）。

# G-op（§B.2-1 / §A.2-2）：V1 准许的计算单元闭集。
_V1_COMPUTE_ALLOWED = frozenset({"FPMUL", "FPADD"})

# IQ 容量墙（G-cap / ADR-057 D1 分段器的公共依据）：RTL smi_top.v:22
# SMI_IQ_INSTQ_DEPTH=128。唯一来源即本常量——分段器 import 此值，
# 禁止第二处字面量，防容量判定双源漂移。
SMI_IQ_INSTQ_DEPTH = 128

# 计算单元族全集（hw_config/modules.yaml 单元清单中的计算类）。用于 G-op 判定：
# op_type ∈ 本族 且 ∉ _V1_COMPUTE_ALLOWED → BASE_OP_OUT_OF_SCOPE。
# SHIFTUP/SHIFTDN/LDB/STB/VIN/VOUT/UR_*/RTOVR/SMI_DS_T 属搬运或端口单元，不在本族，
# 不进入准入语义（其行为由既有搬运路径覆盖）。
_COMPUTE_FAMILY = frozenset({
    "FPMUL", "FPADD", "INTADD", "LOGIC", "FPTOFP", "FPTOINT", "INTTOFP", "INTTOINT",
    "FP32ADDTREE5TO1", "FP32ADDTREE8TO1", "FP16MUL", "INT8TOFP16", "MAC",
})


@dataclass(frozen=True)
class UnrollAdmission:
    """ADR-056 §B.1 准入凭据：YAML `experimental_unroll: true` 声明请求的物化对象。

    tensor_regions: {tensor_name: (bank, offset, length)} —— 张量声明区，bank 内 UR word
    区间 [offset, offset+length)，G-addr（§F.5）的包含判定域；length 语义 = 静态分配
    上界（hco M5）。构造期即全量校验（frozen 不可变）：字段为 int（bool 拒）、length > 0、
    offset ≥ 0、同 bank 声明区两两不交（布局冲突 → TYPE_LAYOUT_UNSUPPORTED）。
    """
    op_name: str
    tensor_regions: Dict[str, Tuple[int, int, int]]

    def __post_init__(self):
        if not self.tensor_regions:
            raise ValueError(
                "[TYPE_LAYOUT_UNSUPPORTED] UnrollAdmission 要求非空 tensor_regions"
                "（准入算子必须显式声明全部张量区，ADR-056 §F.5）")
        by_bank: Dict[int, List[Tuple[str, int, int]]] = {}
        for name, region in self.tensor_regions.items():
            if (not isinstance(name, str) or not name
                    or not isinstance(region, tuple) or len(region) != 3):
                raise ValueError(
                    f"[TYPE_LAYOUT_UNSUPPORTED] 张量声明区形态非法：{name!r} → {region!r}"
                    f"（须为 name: (bank, offset, length)）")
            bank, offset, length = region
            for field, val in (("bank", bank), ("offset", offset), ("length", length)):
                if not isinstance(val, int) or isinstance(val, bool):
                    raise ValueError(
                        f"[TYPE_LAYOUT_UNSUPPORTED] 张量 '{name}' 声明区字段 {field}={val!r}"
                        f" 非 int（bool 亦拒），ADR-056 §F.5")
            if offset < 0 or length <= 0:
                raise ValueError(
                    f"[TYPE_LAYOUT_UNSUPPORTED] 张量 '{name}' 声明区 "
                    f"[{offset}, {offset + length}) 非法（offset≥0 且 length>0）")
            by_bank.setdefault(bank, []).append((name, offset, length))
        # 同 bank 声明区两两不交（§A.2-4 布局冲突诊断的准入侧落地）
        for bank, entries in by_bank.items():
            entries_sorted = sorted(entries, key=lambda e: e[1])
            for (n_a, o_a, l_a), (n_b, o_b, l_b) in zip(entries_sorted, entries_sorted[1:]):
                if o_b < o_a + l_a:
                    raise ValueError(
                        f"[TYPE_LAYOUT_UNSUPPORTED] bank {bank} 声明区重叠："
                        f"'{n_a}'=[{o_a}, {o_a + l_a}) 与 '{n_b}'=[{o_b}, {o_b + l_b})"
                        f"——布局冲突，显式拒绝（ADR-056 §A.2-4）")

    def find_region(self, bank: int, offset: int) -> Optional[Tuple[str, int, int, int]]:
        """含 (bank, offset) 的声明区 → (name, bank, offset, length)；无命中返回 None。

        同 bank 区已在 __post_init__ 证两两不交，故命中至多一个。
        """
        for name, (b, roff, rlen) in self.tensor_regions.items():
            if b == bank and roff <= offset < roff + rlen:
                return (name, b, roff, rlen)
        return None


class PhysicalAssembler:
    """
    AIACC 物理装配器 (V5.1 - 零硬编码版)。
    """
    def __init__(self, hw_spec: HardwareSpec, verbosity: int = 2):
        self.hw_spec = hw_spec
        self.verbosity = verbosity
        # ADR-055 M4：逐队列实际选中分支（flat/unroll/embed/stream_beats/dst_two_cri/
        # dst_explicit/explicit_descriptor）——守卫⑭泛化后据此断言 add 全队列走 unroll，
        # 杜绝「应展开却静默回落 embed」假绿（sim 对两形态同构建模，e2e 会全绿）。
        self.branch_report: Dict[str, str] = {}

    def _get_clean_id(self, node: Any) -> str:
        if isinstance(node, str): return node
        return getattr(node, "trace_id", str(node))

    def _get_registry_val(self, key: str) -> int:
        """从注册表中提取基地址，若缺失则抛出异常，强制要求规格对齐"""
        val = self.hw_spec.config.get("iq_registry", {}).get(key)
        if val is None:
            raise ValueError(f"CRITICAL: Resource key '{key}' missing in iq_registry. Please update hardware spec.")
        return val

    def _l2_stream_level_gates(self, lir_stream: List[LIROp],
                               admission: UnrollAdmission) -> None:
        """ADR-056 §B.2 流级 L2 闸门（assemble 开头一次判定，先于任何发射）：

        - G-op（§B.2-1）：DFG 计算节点单元 ⊆ {FPMUL, FPADD}（§A.2-2 的 assembler 侧复核）。
          命中计算族内越集单元 → 双标签 STATIC_ADMISSION_REJECTED|BASE_OP_OUT_OF_SCOPE
          （§D.2：BASE_OP_OUT_OF_SCOPE 的触发点正是「含 {fpadd,fpmul} 外计算单元」）。
        - G-stage（§B.2-7）：单 stage。src 现无 stage_id 生产者（契约虚设闸门）——一旦
          未来出现 stage 切分元数据即在此响亮拒绝，诊断指向 A2a。
        其余六闸门（G-II/G-slot/G-stride/G-addr/G-cap/G-dp）是队列级判定，在 IQ 循环内
        现有点位执行。
        """
        for op in lir_stream:
            m_type = str(op.op_type).upper().strip()
            if m_type in _COMPUTE_FAMILY and m_type not in _V1_COMPUTE_ALLOWED:
                raise ValueError(
                    f"[STATIC_ADMISSION_REJECTED|BASE_OP_OUT_OF_SCOPE] 准入算子 "
                    f"'{admission.op_name}' 含范围外计算单元 {m_type}"
                    f"（op={self._get_clean_id(op)}）：V1 基础操作集 = "
                    f"{sorted(_V1_COMPUTE_ALLOWED)}（ADR-056 §A.2-2）——显式拒绝")
            stage_id = op.metadata.get("stage_id")
            if stage_id not in (None, 0):
                raise ValueError(
                    f"[STATIC_ADMISSION_REJECTED/G-stage] 准入算子 '{admission.op_name}'"
                    f" 出现 stage 切分（op={self._get_clean_id(op)} stage_id={stage_id!r}）："
                    f"V1 单 stage，跨 stage 分段属 A2a 非目标——显式拒绝"
                    f"（ADR-056 §B.2-7 / §A.3）")

    def assemble(self, lir_stream: List[LIROp], II: int = 1, num_iterations: int = 1,
                 admission: Optional[UnrollAdmission] = None,
                 stage_start: int = 0) -> List[PhysicalInstruction]:
        # ADR-056 §B.1：admission = L1 请求 + L2 判定凭据（runner 由 YAML
        #   `experimental_unroll: true` 物化）；None = 未请求 → 不启用展开分支
        #   （census/旧调用点/其余算子零行为变化，§B.3 兼容语义）。
        # ADR-057 D2（验收① 最小分段侦察）：stage_start = 段起始逻辑迭代 s——
        #   段内第 j 次重复的全局迭代 = s + j（地址按 orig_offset + stride·(s+j−r_k)
        #   烘焙，LIVE 判定与 G-addr 声明区检查以全局迭代计）。仅展开分支消费；
        #   默认 0 = 单段全量，既有行为逐字节不变。num_iterations 语义随之收窄为
        #   「本段迭代数 m」（R_q = span_q + m 由调度槽时自动继承，无需新参数）。
        if not isinstance(stage_start, int) or isinstance(stage_start, bool) or stage_start < 0:
            raise ValueError(f"[STATIC_ADMISSION_REJECTED/G-stage] ADR-057 stage_start 必须为非负整数: {stage_start!r}")
        if stage_start != 0 and (admission is None or II < 2):
            raise ValueError(
                "ADR-057 D2：stage_start≠0 仅在 unroll 准入分支有意义（embed/平坦"
                "分支不消费段偏移）——显式拒绝，防静默忽略")
        if admission is not None:
            self._l2_stream_level_gates(lir_stream, admission)
        trace_to_slot = {}
        trace_to_type = {}
        
        for op in lir_stream:
            tid = self._get_clean_id(op)
            u_id = op.metadata.get("unit_id", "").lower()
            if not u_id: continue
            
            # 🛸 [V5.1 FIX] 移除 30/35/100 等魔法数字
            if "ur-r" in u_id:
                idx = int(u_id.split("-r")[-1].replace("-", ""))
                phys_id = self._get_registry_val("UR_READ") + idx
            elif "ur-w" in u_id:
                idx = int(u_id.split("-w")[-1].replace("-", ""))
                phys_id = self._get_registry_val("UR_WRITE") + idx
            elif "rtovr" in u_id:
                idx = int(u_id.split("-")[-1])
                phys_id = self._get_registry_val("RTOVR") + idx
            else:
                phys_id = op.metadata.get("iq_id") or op.metadata.get("slot_id")
                if phys_id is None:
                    phys_id = self._get_registry_val(u_id.upper())
                phys_id = int(phys_id)

            trace_to_slot[tid] = phys_id
            trace_to_type[tid] = str(op.op_type).upper().strip()
        
        iq_buckets: Dict[str, List[LIROp]] = {}
        for op in lir_stream:
            m_type = str(op.op_type).upper().strip()
            tid = self._get_clean_id(op)
            phys_id = trace_to_slot.get(tid, 0)
            if "RTOVR" in m_type:
                base_rt = self._get_registry_val("RTOVR")
                iq_name = f"rtovr-{phys_id - base_rt}"
            else:
                iq_name = self.hw_spec.get_iq_name(phys_id, m_type).upper()
                iq_name = iq_name.replace("UR-R-", "UR-R").replace("UR-W-", "UR-W")
                iq_name = iq_name.replace("_", "-").lower()
                # ADR-047 D1：多微指令单元（FP16MUL）的 load 微指令走**独立 IQ 流**（RTL iq_inst[3]/[4]，
                #   generate_data_list_coverage IQ_NAME_TO_ID fp16mul_s0123→3 / fp16mul_s4567→4）。按
                #   micro_variant 后缀分桶 → emitter 产 iq_fp16mul-s0123.hex/-s4567.hex（RTL 归一化 '-'→'_'）。
                #   compute/缺省不后缀（保 iq_fp16mul.hex=trigger iq_inst[2]，向后兼容其他单元）。
                mv = op.metadata.get("micro_variant")
                if mv and mv.startswith("load_"):
                    iq_name = f"{iq_name}-{mv[len('load_'):]}"   # fp16mul-s0123 / fp16mul-s4567
            iq_buckets.setdefault(iq_name, []).append(op)
            
        physical_instructions = []
        for iq_name, ops in iq_buckets.items():
            # ADR-042 BLOCK-3：SMI_DS_T 数据通路 IQ（smi-ds-t + 喂它的 SHIFTDN gather）须以**紧凑局部 II**
            #   发射，与 compute II 解耦。RTL pair 引擎（smi_ds_t.v）需 2 CRI 相邻（前后两拍）+ 2 tile/16 拍
            #   gather 才 hi_ready；compute II=10 会把 B=2 配对拉到 10 拍 → hi slot 饿死 → data_out_vld 恒 0
            #   → bank5=0（VCS 实证，hco 2026-07-01）。unit_ds_transpose（II=2）正因 II 小而 VCS 8/8。
            #   故这些 IQ 用 iq_II=DST_REGION_II（=2，mirror unit）而非全局 compute II。其余 IQ 不变。
            is_smi_ds_t_dp = ("smi-ds-t" in iq_name.lower()
                              or any(op.metadata.get("smi_ds_t_gather") for op in ops))
            DST_REGION_II = 2
            iq_II = DST_REGION_II if is_smi_ds_t_dp else II
            t_min_iq = min(op.metadata.get("global_t", 0) for op in ops)
            skip_iterations = t_min_iq // iq_II
            l_init = (skip_iterations * iq_II) + 1

            iq_max_stride = 0
            for op in ops:
                s = int(op.metadata.get("stride", 0))
                if s > iq_max_stride: iq_max_stride = s

            # ADR-050 阶段2：LDB/LDB_AXI 循环时也走 memory-IQ level-1 embed-loop 地址步进（INC 步进
            #   gr_base_addr/ur_addr，hco 取证复用 UR 同构，已 VCS 证）。body op_mask=0x1 when stride!=0
            #   → LDB_AXI(stride=16) 开步进、LDB(stride=0) 驻留。"ldb" 子串覆盖 "ldb" 与 "ldb-axi"。
            # ADR-052：加 "stb" —— 子串覆盖 "stb" 与 "stb-axi"（同 ":96" 对 ldb 的说明）。
            #   漏加的后果：多描述符时 STB 地址不步进，且单描述符单 tile 仍绿 → **静默错误**。
            is_memory_iq = (("ur-" in iq_name.lower()) or ("rtovr" in iq_name.lower())
                            or ("ldb" in iq_name.lower()) or ("stb" in iq_name.lower()))
            # RTL 忽略 HDR 的 inc_embed（hco：只 body 的 w_inc_embed 门控 level offset）→ 此处值 don't-care，
            #   保留 max(stride,1) 不动既有 UR 路径。真正生效的是 body op_mask（:247，stride!=0→0x1 选 level-1）。
            hdr_inc_embed = max(iq_max_stride, 1) if is_memory_iq else 0

            # ADR-041 DEBT-041-STREAM-DLY 修（2026-06-30，照 TB tb_aiacc_top_shift IQ14 结构）：dst-datapath
            #   stream IQ（SHIFTUP/RTOVR/UR_W0，带 stream_beats）emit **N 条显式相同指令、无 INIT/HDR loop 外壳**
            #   —— RTL IQ14 具 EXTRA_DATA（smi_top.v:370/384：WRD 硬连 SMI_DS_T data_out），rdi_start 后**顺序
            #   执行 N 条、每条等 WRD 就绪 pop 一个 data_out beat → N 拍自动对齐窗口**。TB 实证：16 条相同
            #   SHIFTUP_IQ_DATA_REF（LOOPS=0、无 loop）。**绝不能用 1 条+HDR LOOPS 循环**（LOOP_CYCLE 原地保持
            #   不重 pop WRD → 16 次广播同一/stale beat → bank5=0，VCS 实测）。逐 beat 地址 = logical_addr+k×stride
            #   （UR_W0 stride=1 逐行 bank5[0..15]；SHIFTUP stride=0 常驻，data 字段被 WRD 覆盖）。
            stream_beats0 = max((int(op.metadata.get("stream_beats", 0)) for op in ops), default=0)
            if stream_beats0 > 0:
                op = ops[0]
                op_stride = int(op.metadata.get("stride", 0))
                logical_addr0 = int(op.metadata.get("logical_addr", 0))
                op_mask = 0x1 if (is_memory_iq and op_stride != 0) else 0
                is_ur_iq = "ur-" in iq_name.lower()
                layout = None
                if is_ur_iq:
                    from src.core.memory_layout import PhysicalMemoryLayout
                    layout = PhysicalMemoryLayout.from_spec(self.hw_spec)
                # ADR-041 ②（DEBT-041-STREAM-DLY 相位对齐，user 2026-06-30）：UR_W0（不具 EXTRA_DATA、不等数据）
                #   须排到 SHIFTUP 广播窗口（dr_shiftup_d 有效期）才写，否则在数据到达前写零。SHIFTUP 靠
                #   EXTRA_DATA 运行时等过 ~32 拍输入 gather 阶段，UR_W0 没等 → 早 ~32 拍。DLY 仅 0-7 拍不够，
                #   故**垫一条 NOP（vld=0）带 LOOPS=stream_lead_delay** 把 16 条写指令推到广播窗口（NOP 支持
                #   LOOPS，loop N 次 = 延迟 N+1 拍；user 裁定）。delay 由 producer DST 时序确定（VCS 实测调）。
                lead_delay = int(op.metadata.get("stream_lead_delay", 0))
                base_abs = 0
                if lead_delay > 0:
                    physical_instructions.append(PhysicalInstruction(
                        iq_name=iq_name, abs_t=0, trace_id=f"leadnop_{iq_name}",
                        header=SMIHeader(vld=0, loops=lead_delay), payload={},
                        stage="BODY", op_type=str(ops[0].op_type)))
                    base_abs = lead_delay + 1
                for k in range(stream_beats0):
                    header = SMIHeader(vld=1, inc=0, dly=0, inc_embed=op_mask)
                    payload = self._resolve_physical_payload(op, trace_to_slot, trace_to_type)
                    if layout is not None:
                        addr_k = logical_addr0 + op_stride * k
                        orig_bank, orig_offset = layout.resolve(int(addr_k))
                        payload["BANK"] = orig_bank
                        payload["ADDR"] = orig_offset % layout.bank_depth
                    physical_instructions.append(PhysicalInstruction(
                        iq_name=iq_name, abs_t=base_abs + k, header=header, payload=payload,
                        stage="BODY", local_id=k, op_type=str(ops[0].op_type),
                        trace_id=op.trace_id if k == 0 else f"{op.trace_id}_b{k}"))
                self.branch_report[iq_name] = "stream_beats"
                continue

            # ADR-044 Q4（hco）：two-separated-gather 的 SMI_DS_T CRI 必须 **early**（issue_cnt=2 先于
            #   gather0 写 beat 到达，否则 slot 未 open → wr_vld 门掉全部 gather 写 → data_out 永 0）。
            #   CRI-issue（early，开 T0/T1）与 data_out→SHIFTUP（晚，engine 收齐后自发）是**独立事件**，
            #   单 ds_transpose 节点混成一个 → 其 global_t(晚，驱动 shiftup RAW 边) 不能用来定 CRI abs_t。
            #   故 two_word 时 CRI 固定早发 abs_t 1/2（slot_en 一旦 latch 持续到 session 末，覆盖后续 gather 窗）。
            if ("smi-ds-t" in iq_name.lower()
                    and any(op.metadata.get("dst_two_gather_sep") for op in ops)):
                tr = next(op for op in ops if op.metadata.get("dst_two_gather_sep"))
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=0, trace_id=f"init_{iq_name}",
                    header=SMIHeader(vld=0, loops=0), payload={},
                    stage="INIT", op_type=str(ops[0].op_type)))
                cri_payload = self._resolve_physical_payload(tr, trace_to_slot, trace_to_type)
                for j in range(2):   # 2 条相邻 CRI 开 T0/T1（issue_cnt→2）
                    physical_instructions.append(PhysicalInstruction(
                        iq_name=iq_name, abs_t=1 + j, header=SMIHeader(vld=1, inc=0, dly=0, inc_embed=0),
                        payload=cri_payload, stage="BODY", op_type=str(ops[0].op_type),
                        trace_id=tr.trace_id if j == 0 else f"{tr.trace_id}_cri{j}"))
                self.branch_report[iq_name] = "dst_two_cri"
                continue

            # ADR-044 D1（hco Q2）：DST 两-gather 链显式 abs_t emit —— 两 gather 隔 2N，标准 II-kernel
            #   （kernel_map = global_t%iq_II）会把间隔 2N 的两 op 撞进同槽丢一个（19%2==37%2）。故带
            #   dst_explicit_emit 标记的 op（ds_src_read/hi + rt_ds_src/hi + ds_gather/1）按各自 **global_t**
            #   逐条落 abs_t（+2 对齐标准 body 偏移 INIT@0+HDR@1）、NOP(vld=0)填隙，不进 II-kernel。三条 IQ
            #   （ur-r/rtovr-34/shiftdn）源于同一套 t_vars → abs_t 自然对齐。RTOVR route 用其 latch global_t。
            if any(op.metadata.get("dst_explicit_emit") for op in ops):
                DST_ABS_OFFSET = 2   # INIT@0 + HDR-equiv@1 → body from abs_t=global_t+2（VCS 逐拍可微调）
                op_by_t = {int(op.metadata.get("global_t", 0)): op for op in ops}
                max_gt = max(op_by_t)
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=0, trace_id=f"init_{iq_name}",
                    header=SMIHeader(vld=0, loops=0), payload={},
                    stage="INIT", op_type=str(ops[0].op_type)))
                is_ur_iq = "ur-" in iq_name.lower()
                layout = None
                if is_ur_iq:
                    from src.core.memory_layout import PhysicalMemoryLayout
                    layout = PhysicalMemoryLayout.from_spec(self.hw_spec)
                for gt in range(0, max_gt + 1):
                    abs_t_val = gt + DST_ABS_OFFSET
                    if gt in op_by_t:
                        op = op_by_t[gt]
                        payload = self._resolve_physical_payload(op, trace_to_slot, trace_to_type)
                        if layout is not None and op.op_type.upper() == "UR_READ":
                            logical_addr = int(op.metadata.get("logical_addr", 0))
                            orig_bank, orig_offset = layout.resolve(logical_addr)
                            payload["BANK"] = orig_bank
                            payload["ADDR"] = orig_offset % layout.bank_depth
                        physical_instructions.append(PhysicalInstruction(
                            iq_name=iq_name, abs_t=abs_t_val, header=SMIHeader(vld=1, inc=0, dly=0, inc_embed=0),
                            payload=payload, stage="BODY", op_type=str(ops[0].op_type), trace_id=op.trace_id))
                    else:
                        physical_instructions.append(PhysicalInstruction(
                            iq_name=iq_name, abs_t=abs_t_val, header=SMIHeader(vld=0), payload={},
                            stage="BODY", op_type=str(ops[0].op_type), trace_id=f"nop_{iq_name}_{gt}"))
                self.branch_report[iq_name] = "dst_explicit"
                continue

            # ADR-050 DEBT-050-KGT128：多描述符 LDB/LDBAXI **显式-N emit**（hco 字段级定论，走结构 B）。
            #   绕开下方 memory-IQ mod-II 折叠（:242 kernel_map=global_t%iq_II）+ level-1 loop（:230-234）
            #   —— 那会把 N 个 chunk 撞进循环 kernel，RTL `smi_iq_iic.v:427 LOOP_PARA_SET` 走多元素
            #   array-replay 分支卡死 → dly_data_valid 恒 0 → 连 chunk0 都不 fire（bank1 全空，VCS 实证）。
            #   本路：INIT@0(vld=0) + N 条 BODY(vld=1, **level=0**, loops=0, inc_embed=0, 连续 abs_t) +
            #   尾 E_END(vld=0, embed_end=1) = N+2 行。RTL smi_iq_iic.v:400 `~(|w_embed)`→level-0 datapath
            #   每条 fire 一次(:652 dly_data_valid<=1)+rdi_addr+1 顺序取 → 各描述符独立入 4 深 FIFO
            #   （ldb_ur_axi_conv.v:238）、按 emit(chunk_idx) 序位置配对。红线：任何行 level≠0 都会重回
            #   array-replay 死路；地址已 baked 进 payload → inc_embed 必须 0（stride=0）。
            if any(op.metadata.get("explicit_descriptor") for op in ops):
                chunk_ops = sorted(ops, key=lambda o: int(o.metadata.get("chunk_idx", 0)))
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=0, trace_id=f"init_{iq_name}",
                    header=SMIHeader(vld=0, level=0, loops=0), payload={},
                    stage="INIT", op_type=str(ops[0].op_type)))
                for c, op in enumerate(chunk_ops):
                    payload = self._resolve_physical_payload(op, trace_to_slot, trace_to_type)
                    physical_instructions.append(PhysicalInstruction(
                        iq_name=iq_name, abs_t=1 + c,
                        header=SMIHeader(vld=1, level=0, loops=0, inc=0, dly=0, inc_embed=0),
                        payload=payload, stage="BODY", local_id=c,
                        op_type=str(ops[0].op_type), trace_id=op.trace_id))
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=1 + len(chunk_ops), trace_id=f"eend_{iq_name}",
                    header=SMIHeader(vld=0, level=0, loops=0, embed_end=1), payload={},
                    stage="BODY", op_type=str(ops[0].op_type)))
                self.branch_report[iq_name] = "explicit_descriptor"
                continue

            physical_instructions.append(PhysicalInstruction(
                iq_name=iq_name, abs_t=0, trace_id=f"init_{iq_name}",
                header=SMIHeader(vld=0, loops=l_init - 1), payload={},
                stage="INIT", op_type=str(ops[0].op_type)
            ))

            max_t_global = max(op.metadata.get("global_t", 0) for op in ops)
            # ADR-038/039：SMI_DS_T B=2 配对 datapath（SMI_DS_T 指令 IQ + 喂它的 SHIFTDN gather IQ）须
            # 始终做 **2 slot（B=2）** 迭代——issue_cnt==2 才出数 + 两 slot 各须 8 beat 填满。这与输出词数
            # （num_iterations）**无关**：fp16(8 元素=1 词)num_iterations=1 → 只 1 issue + gather 只送 1 slot
            # → hi 槽 open 不 fill → hi_ready=0 → 永不出数（VCS tb_dst_fp16 实证）。fp32(2 词)是
            # num_iterations=2 巧合凑够。
            # 🔴 ADR-043 修（2026-07-01，vec128 32 拍 gap 根因）：原 `max(num_iterations,2)` **不是**解耦——它随
            #   输出词数 scale UP：vec128（128 元素→num_iter=16）→ gather IQ loop 16 次 → SHIFTDN vld_in held 34 拍
            #   → shift_down.v:46 只在 vld_in=0 转发 → 链 34 拍不转发 → SMI_DS_T 用终端 SMC0 单 word 填满 fire →
            #   跨-SMC 塌缩（per-SMC vld_in trace 定死）。真解耦 = **固定 B=2**（gather/CRI 只需 2：ds_gather/ds_gather_1
            #   两 tile + 2 CRI 配对；输出 16 beat 由 SMI_DS_T 引擎内部 beat_rd 产、非 gather loop）。unit（num_iter=8）
            #   原 held 6 拍 work，改固定 2 后 held 更短仍 work（不破）。输出词数走 ur-w0 stream_beats、不受本改影响。
            # （is_smi_ds_t_dp / iq_II 已在 IQ 循环头计算；stream IQ 已在上方 N-条显式分支提前 continue。）
            if is_smi_ds_t_dp:
                eff_num_iter = 2   # B=2 配对固定，与输出词数（num_iterations）解耦
            else:
                eff_num_iter = num_iterations
            total_iterations_needed = (max_t_global // iq_II) + eff_num_iter
            loops_val = total_iterations_needed - skip_iterations - 1

            # ADR-055 D1：kernel_map 显式构造 + 槽冲突拒绝（取代字典推导覆盖）。
            #   原 `{t % iq_II: op}` 对同槽多 op **静默覆盖丢 op**——对展开/平坦/embed 三路
            #   一律显式拒绝（hco 建议同步加在主线）。
            kernel_map: Dict[int, Any] = {}
            for _slot_op in ops:
                _k = int(_slot_op.metadata["global_t"]) % iq_II
                if _k in kernel_map:
                    raise ValueError(
                        f"ADR-055 D1 槽冲突：iq={iq_name} 槽 {_k} 被 "
                        f"{kernel_map[_k].trace_id}(t={kernel_map[_k].metadata.get('global_t')}) 与 "
                        f"{_slot_op.trace_id}(t={_slot_op.metadata.get('global_t')}) 同时占用——"
                        f"显式拒绝，禁止静默覆盖")
                kernel_map[_k] = _slot_op

            # ADR-054（2026-09-12，VCS 取证链 + hco 独立复核）：golden 协议 = 「level-0 平坦流」——
            #   每队列 [INIT? vld=0][op 行: vld=1, level=0, loops=total-1, inc=逐拍步长][jump 终止行:
            #   vld=1, jump=1, code=0]。硬件死区 = **embed∈{1..6}**：smi_iq_iic.v:441
            #   `EN_EMBED && ~(&w_embed)` 才进 :468 LOOP_PARA_SET → 多元素 kernel 走
            #   :457/:557 EXE_ARRAY_INST array-replay 卡死（与 ADR-050 LDB 同族病）；我方主线
            #   「HDR(level=1, embed=1)+kernel」正命中此区，VCS 实证：compiler_mul bank2 只写出
            #   前 3 行即断流（0-2 bit-exact、3-63 全空），H1（golden 流+我方数据 253/255 行）
            #   反证 level-0 平坦形可行。**embed==7 是安全哨兵**（走尾部 jump/LOOP_CYCLE，不进
            #   loop-set）——golden 的 append_nop_wait（mul_scalar_op.cpp:803-819）原生发
            #   embed=7 wait 行（hco 对 golden iq_68_vin0.hex row0 逐字节复核 embed=7）；我方
            #   协议有意加严为**全流 embed==0**（wait 由 vld=0 NOP 行承担，守卫⑭锁死）。
            #   故**II=1 且单槽 kernel**
            #   改发平坦形（mul 全队列皆然）；复杂情形仍走原 embed 主线：
            #   • 多槽（II>1 kernel 多 op，matmul 等）——DEBT-054-MULTISLOT；
            #   • II>1 单槽（add：IQ kernel = [BODY][NOP(embed_end)] 双行，节拍靠 2 行轮流产生）——
            #     DEBT-054-II2-CADENCE（平坦单行为连续 1 拍节拍，无法表达每-II-拍一次；add e2e
            #     实证双读/数据错位；且该形态在 VCS 从未实测，旧 embed 只在 sim 绿、RTL 存疑）。
            #   sim 对两形态同构建模（mul 下 64 次 fire 数一致），e2e 应同绿；差别只在 RTL（VCS arbiter）。
            if len(kernel_map) == 1 and not is_smi_ds_t_dp and iq_II == 1:
                # INC 域守卫（hco 2026-09-12 建议①）：RTL w_inc_comp（smi_iq_iic.v:245-246）
                #   为 4-bit 二进制补码（合法 -8..+7）；iq_max_stride 恒 ≥0（上方 max 聚合），
                #   >7 即超域。显式 error，绝不静默发 RTL 无法表达的 INC（回落 embed 主线是
                #   VCS 未证形态，静默回落更糟）。
                if is_memory_iq and iq_max_stride > 7:
                    raise ValueError(
                        f"ADR-054 平坦流 INC 超域：iq={iq_name} iq_max_stride={iq_max_stride} > 7 "
                        f"（RTL smi_iq_iic.v:245-246 w_inc_comp 4-bit 二进制补码，合法 -8..+7）。"
                        f"拒绝静默发射——DEBT-054-FLAT-INC-RANGE 挂账")
                # 相位对齐：embed 形 HDR 占位 1 拍（vld=1 但 _instruction_valid=False，纯 loop setup），
                # body 首 fire 在 l_init+1 拍；平坦形无 HDR → INIT 多垫 1 拍，保持首 fire 相位不变
                # （否则全流提前 1 拍——mul e2e other_read 审计实证差一拍）。
                op = next(iter(kernel_map.values()))
                op_stride = int(op.metadata.get("stride", 0))
                logical_addr = op.metadata.get("logical_addr", 0)
                iq_start_t = min(o.metadata.get("global_t", 0) for o in ops)
                relative_skip = op.metadata.get("global_t", 0) // iq_II - iq_start_t // iq_II
                op_dly = int(op.metadata.get("dly", 0)) & 0x7
                # ===== INIT→DLY 合并（有界优化实验，用户 2026-10-19 令；默认关）=====
                #   待 RTL 首证的等价性假设（docs/fair_pair_init_fold_feasibility_20261019.md）：
                #   [INIT(vld=0, loops=L)] + [BODY(dly=0)] ≡ [BODY(dly=L+1)]——
                #   RTL smi_iq_iic.v:695-699 dly 仅附首 fire（后续 fire 置 0）+
                #   smi_iq_dly_queen.v:479-511 dly=k ⇒ 发射相对 dly=0 基线晚 k 拍；
                #   sim iq_unit.py:144-149,248-256 同构。首 fire 相位、重复次数、
                #   地址序列、跨队列相位、末次写回、终止行为仍须 VCS 核验。
                #   （2026-10-19 四腿 VCS 已核验全绿：16w A/B、64w B、255w B，见同文档 ④）
                #   准入：实验开关 ∧ fold_dly ≤ 7（BITS_DLY=3 域）；不满足 → 原三行发射。
                fold_dly = l_init + 1 + op_dly
                fold_init = (os.environ.get("AIACC_FLAT_INIT_FOLD", "0") == "1"
                             and fold_dly <= 7)
                assert physical_instructions[-1].trace_id == f"init_{iq_name}", \
                    "INIT 合并/垫拍须落在本队列 INIT 行"
                if fold_init:
                    physical_instructions.pop()  # 摘除 INIT 行：相位改由 BODY dly 承担
                    body_dly = fold_dly
                else:
                    physical_instructions[-1].header.loops = l_init  # 主线上方已发 INIT(loops=l_init-1)
                    body_dly = op_dly
                header = SMIHeader(vld=1, level=0, loops=loops_val,
                                   inc=iq_max_stride if is_memory_iq else 0,
                                   dly=body_dly, inc_embed=0)
                payload = self._resolve_physical_payload(op, trace_to_slot, trace_to_type)
                if "ur-" in iq_name.lower():
                    from src.core.memory_layout import PhysicalMemoryLayout
                    layout = PhysicalMemoryLayout.from_spec(self.hw_spec)
                    orig_bank, orig_offset = layout.resolve(int(logical_addr))
                    payload["BANK"] = orig_bank
                    payload["ADDR"] = (orig_offset - op_stride * relative_skip) % layout.bank_depth
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=l_init + 1, header=header, payload=payload,
                    stage="BODY", local_id=0, op_type=str(ops[0].op_type), trace_id=op.trace_id))
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=l_init + 2, trace_id=f"term_{iq_name}",
                    header=SMIHeader(vld=1, level=0, loops=0, jump=1), payload={},
                    stage="TERM", op_type=str(ops[0].op_type)))
                self.branch_report[iq_name] = "flat"
                continue

            # ============ ADR-056 §B.2：显式槽序列展开发射（L1/L2 准入门控） ============
            # G-II（hco M1 修订）：iq_II>=2 且 admission 非 None 进本闸门链；
            #   iq_II==1 且单槽且非 smi-ds-t 已在上游 :314 分流 ADR-054 平坦分支
            #   （mul 已证路径，G-II 合法分流）；iq_II==1 其余形态在下方 G-II 出口响拒。
            # 铁律（ADR-055 D1 原样生效）：显式 if/elif 链内 raise，禁止任何
            #   「失败即回落 embed」兜底。任一闸门失败 → STATIC_ADMISSION_REJECTED。
            if admission is not None:
                # G-dp（§B.2-8）：SMI_DS_T datapath IQ → 拒绝（沿用现拒绝点位）。
                if is_smi_ds_t_dp:
                    raise ValueError(
                        f"[STATIC_ADMISSION_REJECTED/G-dp] 准入算子 '{admission.op_name}'"
                        f" 出现 SMI_DS_T datapath IQ '{iq_name}'（eff_num_iter 钉 2 专用"
                        f"路径）——越出 V1 展开包络，显式拒绝")
                # G-II（§B.2-2）：iq_II==1 且非单槽平坦形态 → 响拒（不得设想为 II<2 一律
                #   分流平坦；单槽 II=1 已在 :314 先行分流，到不了这里）。
                if iq_II < 2:
                    raise ValueError(
                        f"[STATIC_ADMISSION_REJECTED/G-II] 准入算子 '{admission.op_name}'"
                        f" 在 iq={iq_name} 得 iq_II={iq_II}<2 且非单槽——越出 V1 展开包络"
                        f"（单槽 II=1 归 ADR-054 平坦分支），显式拒绝")
                # D1：槽序列（碰撞已在 kernel_map 构造时拒绝）；气泡槽 = None
                slots = [kernel_map.get(_k) for _k in range(iq_II)]
                assert len(slots) == iq_II, "D1 构造完成断言：len(slots) == iq_II"

                # D2 前置守卫 + 布局
                layout = None
                if is_memory_iq:
                    from src.core.memory_layout import PhysicalMemoryLayout
                    layout = PhysicalMemoryLayout.from_spec(self.hw_spec)
                    # ⚠ 行文笔录更：v3 行文「同 IQ 所有 memory op stride 相等」与自证目标 add
                    #   冲突（ur-r3 = {alpha_read_3 stride=0 驻留, x_read_1 stride=1}）；按 D2-4
                    #   已预设的驻留语义收窄为「stride≠0 子集一致」。须回写 ADR 附录（实现 errata）。
                    nz_strides = {int(o.metadata.get("stride", 0)) for o in ops} - {0}
                    if len(nz_strides) > 1:
                        raise ValueError(
                            f"ADR-055 D2 前置守卫：iq={iq_name} 步进型 memory op 步长不一致 "
                            f"{sorted(nz_strides)}（驻留型 stride=0 豁免）——显式拒绝")
                    R_q_guard = loops_val + 1
                    for _wop in ops:
                        _s = int(_wop.metadata.get("stride", 0))
                        if _s != 0 and _s * R_q_guard >= layout.bank_depth:
                            raise ValueError(
                                f"ADR-055 D2 环绕保守拒绝：iq={iq_name} op={_wop.trace_id} "
                                f"stride·R_q = {_s}·{R_q_guard} = {_s * R_q_guard} >= "
                                f"bank_depth={layout.bank_depth}（UR_WRITE 覆盖他 tensor 为硬错，"
                                f"UR_READ 为别名歧义；== 同拒）——DEBT-055-WRAP-AMBIG 留 A2")
                # D2-6 域校验：INIT loops（BITS_LOOPS=10）
                if not (0 <= l_init <= 1023):
                    raise ValueError(
                        f"ADR-055 D2 域校验：iq={iq_name} INIT loops=l_init={l_init} 越 [0,1023]")

                # INIT 垫拍：同平坦形 :294（无 HDR 占位拍 → 多垫 1 拍保持首 fire 相位）
                physical_instructions[-1].header.loops = l_init
                assert physical_instructions[-1].trace_id == f"init_{iq_name}", \
                    "相位垫拍须落在本队列 INIT 行"

                R_q = loops_val + 1
                skip = t_min_iq // iq_II
                emitted_rows = 1   # INIT 已在主线上方发射
                for j in range(R_q):
                    for k in range(iq_II):
                        abs_t_val = l_init + 1 + k + j * iq_II
                        slot_op = slots[k]
                        if slot_op is None:
                            # D2 气泡行：vld=0 全零域
                            physical_instructions.append(PhysicalInstruction(
                                iq_name=iq_name, abs_t=abs_t_val,
                                trace_id=f"nop_{iq_name}_{j}_{k}",
                                header=SMIHeader(vld=0, level=0, loops=0, inc=0, dly=0,
                                                 embed_end=0, inc_embed=0),
                                payload={}, stage="BODY", local_id=k,
                                op_type=str(ops[0].op_type)))
                            emitted_rows += 1
                            continue
                        stride_k = int(slot_op.metadata.get("stride", 0))
                        relative_skip_k = int(slot_op.metadata.get("global_t", 0)) // iq_II - skip
                        dly_raw = slot_op.metadata.get("dly")
                        dly_val = int(dly_raw) if dly_raw is not None else 0
                        # D2-6 域校验：禁止 `& 0x7` 式静默掩码（主线 :301/:346 保留不动）
                        if not (0 <= dly_val <= 7):
                            raise ValueError(
                                f"ADR-055 D2 域校验：iq={iq_name} op={slot_op.trace_id} "
                                f"dly={dly_val} 越 [0,7]（BITS_DLY=3）")
                        payload = self._resolve_physical_payload(
                            slot_op, trace_to_slot, trace_to_type)
                        if layout is not None and "ur-" in iq_name.lower():
                            # D2 地址契约（hco M1 更正式）：
                            #   A_k       = (orig_offset_k − stride_k·relative_skip_k) mod depth
                            #   ADDR(k,j) = (A_k + stride_k·j) mod depth
                            # ⚠ 禁止 iq_max_stride（压缩形单 INC 寄存器遗留，仅 stride_k ==
                            #   iq_max_stride 时碰巧相等）；展开形 inc 恒 0，地址逐行烘焙。
                            logical_addr = int(slot_op.metadata.get("logical_addr", 0))
                            bank_k, orig_offset_k = layout.resolve(logical_addr)
                            # ADR-056 §F.5 G-addr（先未取模判定，再编码）：LIVE 迭代
                            #   （0 ≤ j_log < N_eff，j_log = j − relative_skip_k，§F.5 v4
                            #   定义 p=j 为展开重复序号、r_k=relative_skip_k）的读/写地址先以
                            #   **未取模**形式 orig_offset + stride·j_log 判定 ⊆ 声明区，
                            #   越出即拒——mod depth 仅作用于编码层，只对 FILL/DRAIN 环绕
                            #   有意义（A1 254/255 即 FILL），不得用取模后地址过合法性检查。
                            #   FILL/DRAIN 跳过本静态判定：其 mod 环绕为受控允许（读侧邻居
                            #   落入允许；写侧无豁免属三区 zone3 语义，由运行时三区比对承担，
                            #   非本静态闸门）。N_eff = num_iterations（本分支 G-dp 已拒
                            #   smi_ds_t，eff_num_iter 恒等于 num_iterations）。
                            # ADR-057 D2：分段时 num_iterations=本段 m，LIVE 局部判定不变
                            #   （j_logical∈[0,m)），合法性以全局迭代 s+j_logical 的
                            #   未取模地址对**完整张量声明区**判定。
                            if admission is not None:
                                j_logical = j - relative_skip_k
                                if 0 <= j_logical < num_iterations:
                                    unwrapped = orig_offset_k + stride_k * (stage_start + j_logical)
                                    region = admission.find_region(bank_k, orig_offset_k)
                                    if region is None:
                                        raise ValueError(
                                            f"[STATIC_ADMISSION_REJECTED/G-addr] "
                                            f"iq={iq_name} op={slot_op.trace_id}：基址 "
                                            f"(bank={bank_k}, offset={orig_offset_k}) 未命中任何"
                                            f"张量声明区——准入要求每个 memory op 落在 YAML 声明"
                                            f"的 tensor 区内（ADR-056 §F.5），显式拒绝")
                                    r_name, _, r_off, r_len = region
                                    if not (r_off <= unwrapped < r_off + r_len):
                                        raise ValueError(
                                            f"[STATIC_ADMISSION_REJECTED/G-addr] "
                                            f"iq={iq_name} op={slot_op.trace_id} "
                                            f"j_logical={j_logical}：LIVE 未取模地址 "
                                            f"(bank={bank_k}, offset={unwrapped}) 越出声明区 "
                                            f"'{r_name}'=[{r_off}, {r_off + r_len})——mod 仅作用于"
                                            f"编码层，不作越界豁免（ADR-056 §F.5），显式拒绝")
                            addr_j = ((orig_offset_k - stride_k * relative_skip_k)
                                      + stride_k * (stage_start + j)) % layout.bank_depth
                            payload["BANK"] = bank_k
                            payload["ADDR"] = addr_j
                        # D2 行规格铁律：全行 level=0 / loops=0 / inc=0 / embed_end=0 / inc_embed=0。
                        # ⚠ inc_embed 写死 0，**不得走主线 :343/:347 的 op_mask 逻辑**——level-0 下
                        #   置 1 当前无害（smi_iq_iic.v:679 对 embed==0 清零 lev1..6 计数），
                        #   但那是依赖副作用的安全。
                        physical_instructions.append(PhysicalInstruction(
                            iq_name=iq_name, abs_t=abs_t_val,
                            header=SMIHeader(vld=1, level=0, loops=0, inc=0, dly=dly_val,
                                             embed_end=0, inc_embed=0),
                            payload=payload, stage="BODY", local_id=k,
                            op_type=str(ops[0].op_type), trace_id=slot_op.trace_id))
                        emitted_rows += 1
                # D2 TERM：ADR-054 D3 契约不变（vld=1, level=0, jump=1, code=0）
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=l_init + 1 + iq_II * R_q,
                    trace_id=f"term_{iq_name}",
                    header=SMIHeader(vld=1, level=0, loops=0, jump=1,
                                     embed_end=0, inc_embed=0),
                    payload={}, stage="TERM", op_type=str(ops[0].op_type)))
                emitted_rows += 1

                # D3：实算行数与构造预测互校（防公式/实现漂移），容量硬拒——永不回落 embed
                L_q_pred = iq_II * R_q + 2
                if emitted_rows != L_q_pred:
                    raise ValueError(
                        f"ADR-055 D3 互校失败：iq={iq_name} 实算行数 {emitted_rows} != "
                        f"构造预测 L_q=iq_II·R_q+2={L_q_pred}")
                if emitted_rows > SMI_IQ_INSTQ_DEPTH:
                    raise ValueError(
                        f"[STATIC_ADMISSION_REJECTED/G-cap] ADR-055 D3 容量拒绝："
                        f"iq={iq_name} 实算行数 {emitted_rows} > {SMI_IQ_INSTQ_DEPTH} "
                        f"（IQ 容量墙 smi_top.v:22 SMI_IQ_INSTQ_DEPTH）——DEBT-055-CAPACITY，"
                        f"A2 分段前置")
                self.branch_report[iq_name] = "unroll"
                continue
            # ================== ADR-056 展开分支结束 ==================

            self.branch_report[iq_name] = "embed"
            physical_instructions.append(PhysicalInstruction(
                iq_name=iq_name, abs_t=l_init, trace_id=f"hdr_{iq_name}",
                header=SMIHeader(vld=1, level=1, loops=loops_val, inc=iq_max_stride, inc_embed=hdr_inc_embed), 
                payload={}, stage="HDR", op_type=str(ops[0].op_type)
            ))
            
            # ADR-041 DEBT-041-STREAM-DLY（hco 二审 catch，**VCS-gated，不在此修**）：D2 要求 stream IQ
            #   产「同一窗口内连续拍 DLY=1」。本想用 1-slot kernel（真 op 不补 nop 到 II → HDR loop 每拍回跳
            #   = 连续）实现，但实测：model-A sim 不忠实建模 SMI_DS_T 16 拍连续 data_out 流（feed 逐拍填充
            #   时序非真），连续 beat 会在 scatter 填 feed 前就读 → 取零、sim 全 0 FAIL。即此修法无法在
            #   现 sim 验证（sim 时序盲），强行落只会换一种假绿/真退化。故保持 II-slot kernel（II-spaced
            #   strided），DEBT-041-STREAM-DLY 留待 VCS + sim 流式模型升级（DEBT-038-B2-GATHER/spec §6）一并解。
            # kernel_map 已前置到平坦流分支判定处（两路共用同一份）
            for k in range(iq_II):
                if k in kernel_map:
                    op = kernel_map[k]
                    op_stride = int(op.metadata.get("stride", 0))
                    logical_addr = op.metadata.get("logical_addr", 0)
                    iq_start_t = min(o.metadata.get("global_t", 0) for o in ops)
                    relative_skip = op.metadata.get("global_t", 0) // iq_II - iq_start_t // iq_II
                    addr = (logical_addr - op_stride * relative_skip)
                    
                    op_mask = 0x1 if (is_memory_iq and op_stride != 0) else 0
                    # ADR-050 修向(x)：per-op DLY（BITS_DLY=3，0-7 拍发射延迟）——unrolled 多-LDB 用 DLY 拉开
                    #   同-IQ 背靠背描述符（LDB_AXI 单描述符 reg 否则被覆盖），使每条 AXI 读在下条前发出。
                    op_dly = int(op.metadata.get("dly", 0)) & 0x7
                    header = SMIHeader(vld=1, inc=0, dly=op_dly, inc_embed=op_mask)
                    payload = self._resolve_physical_payload(op, trace_to_slot, trace_to_type)
                    
                    # [Phase 2 SSoT] 统一使用 PhysicalMemoryLayout 解析地址
                    is_ur_iq = "ur-" in iq_name.lower()
                    if is_ur_iq:
                        from src.core.memory_layout import PhysicalMemoryLayout
                        layout = PhysicalMemoryLayout.from_spec(self.hw_spec)
                        orig_bank, orig_offset = layout.resolve(int(logical_addr))
                        comp_offset = (orig_offset - op_stride * relative_skip) % layout.bank_depth
                        payload["BANK"] = orig_bank
                        payload["ADDR"] = comp_offset
                    trace_id = op.trace_id
                else:
                    header = SMIHeader(vld=0, inc=0, dly=0, inc_embed=0)
                    payload = {}
                    trace_id = f"nop_{iq_name}_{k}"

                abs_t_val = l_init + 1 + k
                if k == iq_II - 1: header.embed_end = 1
                physical_instructions.append(PhysicalInstruction(
                    iq_name=iq_name, abs_t=abs_t_val, header=header, payload=payload,
                    stage="BODY", local_id=k, op_type=str(ops[0].op_type), trace_id=trace_id
                ))
        return physical_instructions

    def _resolve_physical_payload(self, op: Any, trace_to_slot: Dict[str, int], trace_to_type: Dict[str, str]) -> Dict[str, Any]:
        """
        [Phase 3] 代理至 UnitPayloadFactory 处理，摒弃硬编码字典。
        """
        from src.driver.payload_factory import UnitPayloadFactory
        return UnitPayloadFactory.build(op, trace_to_slot, trace_to_type, self.hw_spec)
