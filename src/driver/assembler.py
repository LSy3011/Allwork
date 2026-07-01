import copy
from typing import List, Dict, Any
from ir.lir.lir_op import LIROp, PhysicalInstruction, SMIHeader
from api.hw_spec import HardwareSpec

class PhysicalAssembler:
    """
    AIACC 物理装配器 (V5.1 - 零硬编码版)。
    """
    def __init__(self, hw_spec: HardwareSpec, verbosity: int = 2):
        self.hw_spec = hw_spec
        self.verbosity = verbosity

    def _get_clean_id(self, node: Any) -> str:
        if isinstance(node, str): return node
        return getattr(node, "trace_id", str(node))

    def _get_registry_val(self, key: str) -> int:
        """从注册表中提取基地址，若缺失则抛出异常，强制要求规格对齐"""
        val = self.hw_spec.config.get("iq_registry", {}).get(key)
        if val is None:
            raise ValueError(f"CRITICAL: Resource key '{key}' missing in iq_registry. Please update hardware spec.")
        return val

    def assemble(self, lir_stream: List[LIROp], II: int = 1, num_iterations: int = 1) -> List[PhysicalInstruction]:
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

            is_memory_iq = ("ur-" in iq_name.lower()) or ("rtovr" in iq_name.lower())
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
            kernel_map: Dict[int, Any] = {op.metadata["global_t"] % iq_II: op for op in ops}
            for k in range(iq_II):
                if k in kernel_map:
                    op = kernel_map[k]
                    op_stride = int(op.metadata.get("stride", 0))
                    logical_addr = op.metadata.get("logical_addr", 0)
                    iq_start_t = min(o.metadata.get("global_t", 0) for o in ops)
                    relative_skip = op.metadata.get("global_t", 0) // iq_II - iq_start_t // iq_II
                    addr = (logical_addr - op_stride * relative_skip)
                    
                    op_mask = 0x1 if (is_memory_iq and op_stride != 0) else 0
                    header = SMIHeader(vld=1, inc=0, dly=0, inc_embed=op_mask)
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
