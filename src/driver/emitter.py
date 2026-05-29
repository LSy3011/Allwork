import os, json, struct, numpy as np, copy
from collections import deque
from pathlib import Path
from typing import List, Dict, Tuple, Any, Optional, TYPE_CHECKING

from api.hw_spec import HardwareSpec
from driver.packer import BitPacker
from ir.lir.lir_op import PhysicalInstruction, SMIHeader

if TYPE_CHECKING:
    from ir.mir.mir_op import MIROp

# 从 driver/hw_constants.py inline 的唯一存活常量（其余符号 driver-wide 零使用，已随 hw_constants 一并删除）
DEFAULT_MAX_CYCLES = 120

class LIRPacker:
    """
    AIACC AI-COMPILER EMITTER (V5.1: Fully Decoupled Serialization).
    """
    def __init__(self, config_dir: str = "hw_config", verbosity: int = 2):
        self.hw_spec = HardwareSpec.load(config_dir)
        self.verbosity = verbosity

    def emit_from_instructions(
        self,
        instructions: List[PhysicalInstruction],
        case_root: str,
        program=None,
        smc_num: int = None,
        *,
        lir_stream: Optional[List["MIROp"]] = None,
    ):
        """
        ADR-018 v7 D5.a/d + ADR-020 v6 入口。

        Args:
            smc_num: None / 1 → 单 SMC 路径（旧 smc_0/ layout）；>=2 → 多 SMC 路径（smc_{k}/ layout）
            lir_stream: ADR-020 v6 per-SMC stagger DFG 拓扑入口。keyword-only.
                        smc_num >= 2 时必传（fail-fast，禁止 silent fallback 到 delta=0）;
                        smc_num is None/1 时忽略.
        """
        if smc_num is None or smc_num == 1:
            return self._emit_single_smc(instructions, case_root, program)
        if lir_stream is None:
            raise ValueError(
                "[ADR-020 v6] _emit_multi_smc requires lir_stream for per-SMC stagger; "
                "pipeline_runner.py must pass lir_stream= keyword (List[MIROp])."
            )
        delta_per_op, delta_per_smc = self._compute_per_smc_stagger(
            lir_stream, smc_num, chain_stage_latency=1
        )
        return self._emit_multi_smc(
            instructions, case_root, program, smc_num, delta_per_op, delta_per_smc
        )

    def _emit_single_smc(self, instructions: List[PhysicalInstruction], case_root: str, program=None):
        """N=1 单 SMC emit 路径（ADR-018 v7 D5.a 向后兼容）。"""
        root = Path(case_root)
        smc0 = root / "smc_0"
        ur_dir, bank_dir, analysis = smc0/"ur_data", smc0/"output_bank", smc0/"analysis"
        for d in [smc0, ur_dir, bank_dir, analysis]: os.makedirs(d, exist_ok=True)

        for old_hex in smc0.glob("iq_*.hex"):
            old_hex.unlink()

        (root / "sim_trace.log").touch()

        iq_buckets: Dict[str, List[PhysicalInstruction]] = {}
        for instr in instructions:
            iq_buckets.setdefault(instr.iq_name, []).append(instr)

        iq_init = {}
        for iq_name, stream in iq_buckets.items():
            stream.sort(key=lambda x: x.abs_t)
            lookup_type = self._get_lookup_type(iq_name)
            total_bits = BitPacker.get_unit_bits(self.hw_spec, lookup_type)
            hex_chars = (total_bits + 3) // 4

            hex_lines = []
            for instr in stream:
                is_body_stage = (instr.stage == "BODY")
                val = BitPacker.pack_capsule(
                    spec=self.hw_spec,
                    unit_type=lookup_type,
                    payload_dict=instr.payload,
                    is_body=is_body_stage,
                    **instr.header.to_dict()
                )
                hex_lines.append(f"{val:0{hex_chars}x}")

            hex_file_name = f"iq_{iq_name}.hex"
            with open(smc0 / hex_file_name, "w") as f:
                f.write("\n".join(hex_lines) + "\n")

            iq_init[iq_name.replace("_", "-")] = f"smc_0/{hex_file_name}"

        # 🛸 [V5.1 FIX] 动态获取 Bank 数量，不再死磕 range(8)
        bank_num = getattr(self.hw_spec.params, "PARAM_UR_BANK_NUM", 8)
        sim_conf = {
            "smc_id": 0,
            "iq_init": iq_init,
            "ur_init": {str(b): f"smc_0/ur_data/bank{b}.hex" for b in range(bank_num)},
            "max_cycles": DEFAULT_MAX_CYCLES,
            "tensors": []
        }

        if program:
            self._handle_program_data(program, ur_dir, sim_conf)

        with open(root / "sim_config.json", "w") as f:
            json.dump(sim_conf, f, indent=2)

    def _emit_multi_smc(
        self,
        instructions: List[PhysicalInstruction],
        case_root: str,
        program,
        smc_num: int,
        delta_per_op: Optional[Dict[Tuple[str, int], int]] = None,
        delta_per_smc: Optional[Dict[int, int]] = None,
    ):
        """
        N>1 多 SMC emit 路径（ADR-018 v7 D5.a/d + ADR-020 v6 per-SMC stagger）。
        - 同一份 instructions 复制到 smc_{k}/ k ∈ [0, smc_num)
        - payload 中 SMC_ID 字段填本机 k（ADR-018 D5.a）
        - LOGIC SMC_ID 4-bit 安全 fail-fast（smc_num > 16 时 LOGIC 会撞 mask 上界）
        - smc_id_en 字段不动（由 SMT solver 解出后已注入 instr.payload；D5.b 阶段完整支持）
        - broadcast 多 hex 分发（沿途 SMC_0..smc_id_en 同 cycle emit + SMC_k k>0 data 填 0）由 D5.c 阶段实现
        - sim_config.json 扩展 per_smc layout（D5.d）
        - ADR-020 v6 per-SMC stagger: SMC_k INIT 行 loops 减 delta_per_smc[k]，整 IQ stream
          提前 d_smc cycle 启动 BODY（以 SMC_{smc_id_en} 为基准；详见 ADR-020 v6 §D.1' 落锤 prescription）
        """
        root = Path(case_root)
        bank_num = getattr(self.hw_spec.params, "PARAM_UR_BANK_NUM", 8)

        # 准备 per-SMC sim_config 元数据
        sim_conf = {
            "smc_num": smc_num,
            "smc_id_list": list(range(smc_num)),
            "per_smc_iq_init": [],     # List[Dict[iq_name, hex_path]]，index = smc_id
            "per_smc_ur_init": [],     # List[Dict[bank_id, hex_path]]
            "max_cycles": DEFAULT_MAX_CYCLES,
            "tensors": []
        }

        (root / "sim_trace.log").touch()

        # 按 iq_name 分桶（所有 SMC 共享同一份 instruction 集，按 SMC_ID 在 payload 注入区分）
        iq_buckets: Dict[str, List[PhysicalInstruction]] = {}
        for instr in instructions:
            iq_buckets.setdefault(instr.iq_name, []).append(instr)

        for smc_id in range(smc_num):
            smc_dir = root / f"smc_{smc_id}"
            ur_dir = smc_dir / "ur_data"
            for d in [smc_dir, ur_dir, smc_dir/"output_bank", smc_dir/"analysis"]:
                os.makedirs(d, exist_ok=True)
            for old_hex in smc_dir.glob("iq_*.hex"):
                old_hex.unlink()

            # ADR-020 v6 D.1' (SMC_0 基准变体): 全 cluster baseline shift 后, SMC_k 整 IQ stream
            # INIT loops 加 smc_shift = max_dsmc - delta_per_smc[k]. SMC_0 加 0 (baseline 不动),
            # SMC_smc_id_en 加 max_dsmc (最晚启动). 物理含义: SMC_k 比 SMC_0 晚 k×cs_lat cycle
            # 启动 BODY, 与 chain 1-cycle 串接需求对齐 (HCO §"等价基准" 方案;
            # SMC_smc_id_en 基准减法在 ur-r0 INIT loops=0 边界 fail-fast).
            max_dsmc = max((delta_per_smc or {0: 0}).values()) if delta_per_smc else 0
            d_smc = (delta_per_smc or {}).get(smc_id, 0)
            smc_shift = max_dsmc - d_smc

            iq_init = {}
            for iq_name, stream in iq_buckets.items():
                stream.sort(key=lambda x: x.abs_t)
                lookup_type = self._get_lookup_type(iq_name)
                total_bits = BitPacker.get_unit_bits(self.hw_spec, lookup_type)
                hex_chars = (total_bits + 3) // 4

                # LOGIC 4-bit SMC_ID 安全检查（modules.yaml line 67 LOGIC SMC_ID = 4 bit）
                self._check_smc_id_field_width(lookup_type, smc_id)

                hex_lines = []
                for instr in stream:
                    # ADR-018 v7 D5.c: SHIFTUP/SHIFTDN chain 分发——smc_id > smc_id_en 跳过 emit
                    # 非沿途 SMC 不持有该 chain 指令 (IQ 流不增加此行；PC 独立推进同步)
                    if self._should_skip_chain_instr(instr, smc_id):
                        continue

                    is_body_stage = (instr.stage == "BODY")
                    # ADR-020 v6 D.1' (SMC_0 基准): INIT loops 加 smc_shift
                    # SMC_0 smc_shift=0 不动 (hex 字节级与 v5.1 baseline 一致 when 非 chain);
                    # SMC_smc_id_en smc_shift=max_dsmc 最晚启动. LOOPS 10-bit max 1023 远大于
                    # 典型 stagger ≤ 31, 不会溢出 (HCO §SMI 兼容性验证).
                    header_eff = instr.header
                    if instr.stage == "INIT" and smc_shift > 0:
                        header_eff = copy.copy(instr.header)
                        header_eff.loops = instr.header.loops + smc_shift
                    # 注入本 SMC 的 SMC_ID 字段 + D5.c 沿途 SHIFTUP broadcast 的 SMC_k>0 data 清 0
                    payload_per_smc = self._inject_smc_id(instr.payload, lookup_type, smc_id)
                    payload_per_smc = self._zero_broadcast_data_for_followers(
                        payload_per_smc, instr, smc_id
                    )
                    val = BitPacker.pack_capsule(
                        spec=self.hw_spec,
                        unit_type=lookup_type,
                        payload_dict=payload_per_smc,
                        is_body=is_body_stage,
                        **header_eff.to_dict()
                    )
                    hex_lines.append(f"{val:0{hex_chars}x}")

                hex_file_name = f"iq_{iq_name}.hex"
                with open(smc_dir / hex_file_name, "w") as f:
                    f.write("\n".join(hex_lines) + "\n")

                iq_init[iq_name.replace("_", "-")] = f"smc_{smc_id}/{hex_file_name}"

            sim_conf["per_smc_iq_init"].append(iq_init)
            sim_conf["per_smc_ur_init"].append(
                {str(b): f"smc_{smc_id}/ur_data/bank{b}.hex" for b in range(bank_num)}
            )

            if program:
                # ADR-018 v7 D5.d task #18 sub-7: 多 SMC per-program 数据注入。
                # program 可为 (a) 单 MockProgram (legacy，仅 SMC_0 注入；其余空 bank)；
                # (b) List[MockProgram] 长度 = smc_num，每 SMC 独立 inject。
                if isinstance(program, list):
                    per_smc_program = program[smc_id]
                    # sim_conf.tensors 只填一次（共享 metadata：layout 各 SMC 同 logical_addr）
                    target_conf = sim_conf if smc_id == 0 else {"tensors": []}
                    self._handle_program_data(per_smc_program, ur_dir, target_conf)
                elif smc_id == 0:
                    self._handle_program_data(program, ur_dir, sim_conf)
                else:
                    # legacy 单 program 模式：其他 SMC 给空 bank.hex（默认 0），保持 layout 一致
                    for b_id in range(bank_num):
                        (ur_dir / f"bank{b_id}.hex").touch()

        with open(root / "sim_config.json", "w") as f:
            json.dump(sim_conf, f, indent=2)

    @staticmethod
    def _compute_per_smc_stagger(
        lir_stream: List["MIROp"],
        smc_num: int,
        chain_stage_latency: int = 1,
    ) -> Tuple[Dict[Tuple[str, int], int], Dict[int, int]]:
        """
        ADR-020 v6 per-SMC stagger 算法（按 §D.1' 落锤 prescription + v5.1 §实施算法）.

        Returns:
            delta_per_op[(trace_id, smc_k)] : 该 op 在 SMC_k 上相对 SMC_{smc_id_en}
                提前的 cycle 数（≥ 0）. 非 chain 算子全 0.
            delta_per_smc[smc_k] : max over all op delta，该 SMC 整 IQ stream
                INIT loops 应减的 cycle 数.

        算法:
            Pass 1 forward topo:
                对每个 consumer 处理 depends_on / rtovr_deps 边:
                  继承 producer 的 delta (max);
                  若 producer 是 SHIFTUP/SHIFTDN 且 rtovr_deps 边:
                    delta[(consumer, k)] = max(.., (smc_id_en - k) * cs_lat)
            Pass 2 backward BFS:
                对 b/c 通路 partitioned UR_READ producer 反向传播 stagger
                (consumer.depends_on producer 必须配套提前发射).

        参数:
            lir_stream: 含 metadata.{depends_on, rtovr_deps, smc_id_en, op_type}
                        的 op list (MIROp 或 LIROp，duck-typed).
            smc_num: cluster SMC 数量.
            chain_stage_latency: ADR-018 D3.b chain 每跳延迟 (默认 1).
        """
        if smc_num <= 1:
            return ({}, {})

        op_by_id = {op.trace_id: op for op in lir_stream}
        delta_per_op: Dict[Tuple[str, int], int] = {
            (op.trace_id, k): 0 for op in lir_stream for k in range(smc_num)
        }

        # Topo sort (Kahn's): edges = depends_on + rtovr_deps
        in_degree = {op.trace_id: 0 for op in lir_stream}
        children: Dict[str, List[str]] = {op.trace_id: [] for op in lir_stream}
        for op in lir_stream:
            preds = list(op.metadata.get("depends_on", []))
            preds += list(op.metadata.get("rtovr_deps", {}).keys())
            for prod_id in preds:
                if prod_id in in_degree:
                    in_degree[op.trace_id] += 1
                    children[prod_id].append(op.trace_id)
        queue = deque(tid for tid, d in in_degree.items() if d == 0)
        topo: List[str] = []
        in_deg = dict(in_degree)
        while queue:
            tid = queue.popleft()
            topo.append(tid)
            for child in children[tid]:
                in_deg[child] -= 1
                if in_deg[child] == 0:
                    queue.append(child)
        if len(topo) != len(lir_stream):
            raise RuntimeError(
                f"[ADR-020 v6] DFG has cycle (topo {len(topo)} != ops {len(lir_stream)})"
            )

        # Pass 1 forward topo
        for tid in topo:
            op = op_by_id[tid]
            # 继承 depends_on producer 的 delta
            for prod_id in op.metadata.get("depends_on", []):
                if prod_id not in op_by_id:
                    continue
                for k in range(smc_num):
                    d = delta_per_op[(prod_id, k)]
                    if d > delta_per_op[(tid, k)]:
                        delta_per_op[(tid, k)] = d
            # 继承 rtovr_deps producer 的 delta + 直接 chain stagger 入口约束
            for prod_id in op.metadata.get("rtovr_deps", {}).keys():
                if prod_id not in op_by_id:
                    continue
                producer = op_by_id[prod_id]
                for k in range(smc_num):
                    d = delta_per_op[(prod_id, k)]
                    if d > delta_per_op[(tid, k)]:
                        delta_per_op[(tid, k)] = d
                if producer.op_type.upper() in ("SHIFTUP", "SHIFTDN"):
                    smc_id_en = producer.metadata.get("smc_id_en", 0)
                    if isinstance(smc_id_en, int) and smc_id_en > 0:
                        for k in range(min(smc_num, smc_id_en + 1)):
                            d = (smc_id_en - k) * chain_stage_latency
                            if d > delta_per_op[(tid, k)]:
                                delta_per_op[(tid, k)] = d

        # Pass 2 backward BFS (b/c partitioned 通路反传)
        worklist = deque()
        in_worklist = set()
        for tid in topo:
            if any(delta_per_op[(tid, k)] > 0 for k in range(smc_num)):
                worklist.append(tid)
                in_worklist.add(tid)
        while worklist:
            cons_id = worklist.popleft()
            in_worklist.discard(cons_id)
            consumer = op_by_id[cons_id]
            for prod_id in consumer.metadata.get("depends_on", []):
                if prod_id not in op_by_id:
                    continue
                changed = False
                for k in range(smc_num):
                    if delta_per_op[(cons_id, k)] > delta_per_op[(prod_id, k)]:
                        delta_per_op[(prod_id, k)] = delta_per_op[(cons_id, k)]
                        changed = True
                if changed and prod_id not in in_worklist:
                    worklist.append(prod_id)
                    in_worklist.add(prod_id)

        # Reduce: delta_per_smc[k] = max over all op
        delta_per_smc: Dict[int, int] = {}
        for k in range(smc_num):
            delta_per_smc[k] = max(
                (delta_per_op[(tid, k)] for tid in op_by_id),
                default=0,
            )

        return delta_per_op, delta_per_smc

    def _check_smc_id_field_width(self, lookup_type: str, smc_id: int):
        """
        ADR-018 v7 D5.a binding：LOGIC SMC_ID 4-bit 特例 fail-fast。
        其他单元 SMC_ID 5-bit 容纳 0..31 没问题；LOGIC 4-bit 上限 15。
        """
        u_type = lookup_type.upper().replace("-", "_")
        if "UR_R" in u_type: u_type = "UR_READ"
        if "UR_W" in u_type: u_type = "UR_WRITE"
        if u_type not in self.hw_spec.modules:
            return
        module = self.hw_spec.modules[u_type]
        for field in module.code_fields:
            if field.name.upper() == "SMC_ID":
                max_val = (1 << field.bits) - 1
                if smc_id > max_val:
                    raise ValueError(
                        f"[ADR-018 D5.a] unit '{u_type}' SMC_ID field width = {field.bits} bit "
                        f"(max {max_val}); 但 emitter 要求 SMC_ID = {smc_id}，"
                        f"silent truncate 会导致 hex 错误。增加 SMC_ID 字段位宽或重新设计算子分配。"
                    )
                break

    def _should_skip_chain_instr(self, instr: PhysicalInstruction, smc_id: int) -> bool:
        """
        ADR-018 v7 D5.c：SHIFTUP/SHIFTDN chain 指令在 smc_id > smc_id_en 时跳过 emit。
        非沿途 SMC 不持有该 chain 指令，IQ stream 自然不增加此行；PC 独立推进保证沿途同步。
        - 非 SHIFTUP/SHIFTDN op 不影响（return False）
        - smc_id_en 缺省 PARAM_SMC_NUM-1（全沿途，等价不跳过）
        """
        if instr.op_type.upper() not in ('SHIFTUP', 'SHIFTDN'):
            return False
        # payload 中 smc_id_en 字段：modules.yaml lowercase 定义，但
        # UnitPayloadFactory 统一 upper-case key 产 payload（ADR-018 D5 task #18 sub-4）。
        # 双侧兼容查（避免 silent fallback 到 PARAM_SMC_NUM-1 引入静默 over-broadcast）。
        smc_id_en = instr.payload.get('smc_id_en')
        if smc_id_en is None:
            smc_id_en = instr.payload.get('SMC_ID_EN')
        if smc_id_en is None:
            # 缺省：全沿途广播（PARAM_SMC_NUM - 1）
            smc_id_en = self.hw_spec.params.PARAM_SMC_NUM - 1
        return smc_id > int(smc_id_en)

    def _zero_broadcast_data_for_followers(
        self, payload: Dict[str, Any], instr: PhysicalInstruction, smc_id: int
    ) -> Dict[str, Any]:
        """
        ADR-018 v7 D5.c：SHIFTUP broadcast 沿途 SMC_k (k>0) 的 data 字段填 0。
        硬件 spec 规定：SMC_k>0 的 CRU_SHIFTUP_I 接上游不接本地 data，故 data 字段值无关。
        显式约定填 0 避免 silent bug（如 emitter 错把上一个 SMC 的 data 复制过来）。

        - 仅对 SHIFTUP op 且 broadcast=1 且 smc_k>0 触发
        - SHIFTDN 无 broadcast 字段（OQ-5 确认），不适用
        - SHIFTUP broadcast=0 (P2P)：data 来源仍是 SMC_0 chain head，逻辑同 broadcast=1，
          沿途 SMC_k>0 的 data 也无意义，统一清 0
        """
        if instr.op_type.upper() != 'SHIFTUP':
            return payload
        if smc_id == 0:
            return payload
        # SMC_k > 0 SHIFTUP：data 字段填 0
        if 'data' in payload or 'DATA' in payload:
            new_payload = dict(payload)
            if 'data' in new_payload:
                new_payload['data'] = 0
            if 'DATA' in new_payload:
                new_payload['DATA'] = 0
            return new_payload
        return payload

    def _inject_smc_id(self, payload: Dict[str, Any], lookup_type: str, smc_id: int) -> Dict[str, Any]:
        """
        ADR-018 v7 D5.a：复制 payload 并注入 SMC_ID 字段（深拷贝防止跨 SMC 污染）。
        仅当 module.code_fields 包含 SMC_ID 字段时才注入；smc_id_en 字段不动（D5.b 处理）。
        """
        u_type = lookup_type.upper().replace("-", "_")
        if "UR_R" in u_type: u_type = "UR_READ"
        if "UR_W" in u_type: u_type = "UR_WRITE"
        if u_type not in self.hw_spec.modules:
            return payload

        module = self.hw_spec.modules[u_type]
        has_smc_id = any(f.name.upper() == "SMC_ID" for f in module.code_fields)
        if not has_smc_id:
            return payload

        new_payload = copy.deepcopy(payload)
        new_payload["SMC_ID"] = smc_id
        return new_payload

    def _get_lookup_type(self, iq_name: str) -> str:
        """
        [Phase 3] 代理至 UnitPayloadFactory 处理，摒弃硬编码字典。
        """
        from src.driver.payload_factory import UnitPayloadFactory
        return UnitPayloadFactory.resolve_iq_type(iq_name, self.hw_spec)

    def _handle_program_data(self, program, ur_dir, sim_conf):
        """处理 Bank 数据初始化与 Tensor 元数据映射。"""
        v_lanes = program.vector_lanes
        element_bits = getattr(program, "element_bits", 32)
        
        # 🛸 [V6.7 FIX] 动态选择打包格式与移位参数，支持 FP32/FP16/INT8 等全精度类型，彻底拆除硬件打包硬编码
        if element_bits == 32:
            pack_fmt, unpack_fmt, bit_shift, hex_len_factor = '<f', '<I', 32, 8
        elif element_bits == 16:
            # Python 3.6+ 支持 '<e' 表达 16-bit 浮点数，'<H' 表达 16-bit 无符号短整型
            pack_fmt, unpack_fmt, bit_shift, hex_len_factor = '<e', '<H', 16, 4
        elif element_bits == 8:
            pack_fmt, unpack_fmt, bit_shift, hex_len_factor = '<b', '<B', 8, 2
        else:
            pack_fmt, unpack_fmt, bit_shift, hex_len_factor = '<f', '<I', 32, 8 # fallback

        for b_id, storage in program.memory_allocator.banks.items():
            with open(ur_dir / f"bank{b_id}.hex", "w") as f:
                for entry in storage:
                    # 假定每个 word 是 v_lanes 个元素
                    # 🛸 动态打包各精度向量
                    hex_val = 0
                    for i in range(v_lanes):
                        val_bits = struct.unpack(unpack_fmt, struct.pack(pack_fmt, entry[v_lanes-1-i]))[0]
                        hex_val = (hex_val << bit_shift) | val_bits
                    f.write(f"{hex_val:0{v_lanes*hex_len_factor}x}\n")
        
        for name, obj in program.tensors.items():
            sim_conf["tensors"].append({
                "name": name,
                "bank": obj.bank,
                "addr": obj.offset,
                "shape": list(obj.shape),
                "role": obj.role
            })
