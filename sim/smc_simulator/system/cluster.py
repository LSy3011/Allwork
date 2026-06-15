import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from smc_simulator.core.kernel import ParallelKernel
from aiacc_hardware.src.spec_loader import SpecLoader

@dataclass
class TransferRequest:
    src_cluster_id: int
    src_bank: int
    src_addr: int
    dst_cluster_id: int
    dst_bank: int
    dst_addr: int
    data: Optional[int] = None
    status: str = "PENDING" # PENDING, TRANSFERRING, COMPLETED, FAILED

class BusArbiter:
    """
    Simple Bus Arbiter for inter-cluster communication.
    
    Current Model:
    - Shared Bus (Single channel)
    - Throughput: 1 transfer per cycle
    - Latency: configurable (default 10 cycles)
    """
    def __init__(self, latency: int = 10):
        self.latency = latency
        self.queue: List[TransferRequest] = []
        self.active_transfers: List[Dict[str, Any]] = [] # {'req': req, 'cycles_left': n}
        self.logger = logging.getLogger("BusArbiter")

    def request_transfer(self, req: TransferRequest):
        self.queue.append(req)
        self.logger.info(f"[Bus] Request received: Cluster {req.src_cluster_id} -> Cluster {req.dst_cluster_id}")

    def tick(self) -> List[TransferRequest]:
        completed = []
        
        # 1. Process active transfers
        remaining_transfers = []
        for transfer in self.active_transfers:
            transfer['cycles_left'] -= 1
            if transfer['cycles_left'] <= 0:
                req = transfer['req']
                req.status = "COMPLETED"
                completed.append(req)
                self.logger.info(f"[Bus] Transfer completed: Cluster {req.src_cluster_id} -> Cluster {req.dst_cluster_id}")
            else:
                remaining_transfers.append(transfer)
        self.active_transfers = remaining_transfers

        # 2. Schedule new transfers if bus is free (assuming single channel for now)
        # For simplicity, allow infinite parallelism in this prototype unless constrained
        # Let's say max 1 new transfer per cycle initiates
        if self.queue:
            req = self.queue.pop(0)
            req.status = "TRANSFERRING"
            self.active_transfers.append({'req': req, 'cycles_left': self.latency})
            self.logger.info(f"[Bus] Started transfer: Cluster {req.src_cluster_id} -> Cluster {req.dst_cluster_id}")
            
        return completed

class SystemCluster:
    """
    Multi-Kernel System Cluster (ADR-018 v7 + ADR-019 v6).
    - Contains multiple ParallelKernel instances; each kernel binds to its smc_id
    - 跨 SMC chain 互联（SHIFTUP 上行 / SHIFTDN 下行；OQ-7 ring 闭合留 Stage 3+）
    - BusArbiter 死代码原型保留 (ADR-018 D6) 但不复活；AXI 通路独立 ADR-021+
    """
    def __init__(self, num_kernels: int = 2):
        self.loader = SpecLoader()
        self.kernels: Dict[int, ParallelKernel] = {}
        for i in range(num_kernels):
            k = ParallelKernel(self.loader, smc_id=i)
            k.logger = logging.getLogger(f"Kernel-{i}")
            self.kernels[i] = k

        # ADR-023 D6：SHIFTDN smc_id_en fallback 按实际 cluster 规模派生（替代硬编码 31，避免 8-SMC 越界）
        for k in self.kernels.values():
            if 'shiftdn' in k.units:
                k.units['shiftdn'].smc_id_en_fallback = num_kernels - 1

        self.bus = BusArbiter()
        self.cycle_count = 0
        self.logger = logging.getLogger("SystemCluster")

        # ADR-034 Stage-I 2b/S2-D1：SMI_DS_T 指令驱动 + 真 step-driven 单元（cluster 级单实例）
        self.smi_ds_t_iq = None
        self.smi_ds_t_cfg = None
        self.smi_ds_t_unit = None      # S2-D1：表596 接口真单元（替 apply_smi_ds_t backdoor）
        self.smi_ds_t_started = False  # gather+transpose 已触发（= 2b 的 fired）
        self.smi_ds_t_done = False     # INST_DONE：DATA_OUT 全部 scatter 完
        self.smi_ds_t_fired = False    # 兼容别名（= started）

    def _propagate_chain_inputs(self):
        """
        ADR-019 D5：跨 SMC chain wire 互联（在 kernel.step() 之前注入上游 chain output）
        - SHIFTUP（向上）：SMC_{k+1}.chain_inputs['shiftup'] <- SMC_k.units['shiftup'].cru_o
        - SHIFTDN（向下）：SMC_k.chain_inputs['shiftdn']   <- SMC_{k+1}.units['shiftdn'].cru_o
        - SMC_0 / SMC_{N-1} 链头/链尾 chain_input 保持 0（线性 chain，非 ring；OQ-7 P2）
        """
        n = len(self.kernels)
        for k in range(n - 1):
            up_o = self.kernels[k].units['shiftup'].get_chain_output()
            self.kernels[k + 1].chain_inputs['shiftup'] = up_o

            dn_o = self.kernels[k + 1].units['shiftdn'].get_chain_output()
            self.kernels[k].chain_inputs['shiftdn'] = dn_o

    def _broadcast_shiftup_fanout(self):
        """
        ADR-023 D2(B)：SHIFTUP broadcast 同拍单源 fan-out。
        kernel.step() 已 evaluate+tick，SMC_0 的 shiftup.dr_d = 本拍 ur-r0 读出的广播 a[i]（单源）。
        broadcast 时把该值同拍覆盖到所有使能 SMC（0 < k <= smc_id_en）的 dr_d / cru_o，
        **abolish `_propagate_chain_inputs` 的逐级 1-cycle 相对延迟**（Model A 时序不忠实中转：
        删 stagger 后必须让 a[i] 无相对延迟到达所有使能 SMC，否则 lockstep FPMUL 读到错位 stale a）。
        k > smc_id_en 不动（保持 smc_id_en 门控；对应 D4 gating 单测）。
        """
        head = self.kernels.get(0)
        if head is None:
            return
        head_su = head.units['shiftup']
        if not getattr(head_su, 'bcast_active', False):
            return
        smc_id_en = head_su.bcast_smc_id_en
        if not isinstance(smc_id_en, int):
            return
        bcast_val = head_su.dr_d            # SMC_0 单源广播值（a[i]）
        for k, kernel in self.kernels.items():
            if 0 < k <= smc_id_en:
                su = kernel.units['shiftup']
                su.dr_d = bcast_val
                su.cru_o = bcast_val

    def apply_smi_ds_t(self, cfg: dict):
        """ADR-033 Stage-A：SMI_DS_T 跨-SMC 转置 datapath（model-A 整链原子段，hco B-BLOCK-1 裁定）。

        在 cluster.run 之前一次性执行 `UR read — SHIFTDN gather — SMI_DS_T transpose —
        SHIFTUP scatter — UR write` 的数值功能：
          - **gather**：从 cfg['gather_smc_cnt'] 个 SMC 各读 src_bank/src_addr 的列对齐激活（非转置间插存）；
          - **transpose**：SmiDsTUnit 把 gather_smc_cnt×lanes 的 Tile 转置为 lanes×gather_smc_cnt（行对齐）；
          - **scatter/write**：把恢复的行对齐激活写回 SMC_0 的 dst_bank/dst_addr（供后续 SHIFTUP 广播读）。

        忠实度边界（model-A，hco caveat）：本方法**不建模** SHIFTDN/SHIFTUP/转置的逐拍时序与
        COND 就绪同步（DEBT-033-DST-CTRL / NEEDS-USER-FACT-033-DSTDONE）；只保证终值数值正确 +
        gather-before-scatter 序约束（SmiDsTUnit.transpose() gather 未到齐即抛错）。
        """
        from smc_simulator.units.smi_ds_t_unit import SmiDsTUnit, _lane_codec
        gather_cnt = int(cfg["gather_smc_cnt"])
        lanes = int(cfg["lanes"])
        src_bank, src_addr = int(cfg["src_bank"]), int(cfg["src_addr"])
        dst_bank, dst_addr = int(cfg["dst_bank"]), int(cfg["dst_addr"])
        # ADR-035 D2：element_bits 选 codec；word_lanes 由其推导（128b 词 / 元素位宽）
        element_bits = int(cfg.get("element_bits", 32))
        word_lanes = int(cfg.get("word_lanes", 128 // element_bits))
        lanes_from_word, word_from_lanes = _lane_codec(element_bits)
        if gather_cnt > len(self.kernels):
            raise ValueError(f"SMI_DS_T gather_smc_cnt={gather_cnt} > num_kernels={len(self.kernels)}")

        unit = SmiDsTUnit(smc_num=gather_cnt, lanes=lanes,
                          word_lanes=word_lanes, element_bits=element_bits)
        # gather：逐 SMC 读列对齐激活（SHIFTDN gather 的 model-A 数值等价）
        for s in range(gather_cnt):
            word = self.kernels[s].ur_bank.storage[src_bank][src_addr]
            unit.gather(s, lanes_from_word(word, lanes))
        # transpose：gather 全到齐才出（未到齐抛错 = gather-before-scatter 硬约束）
        rows = unit.transpose()        # rows[m] = 行对齐激活 X[m,:]（gather_cnt 个元素）
        # scatter/write：行对齐激活写回 SMC_0 dst（SHIFTUP scatter 的 model-A 数值等价）。
        # 行宽 gather_cnt 可 > word_lanes（如 fp32 K=8 = 2 词）→ 每行拆 ceil(K/word_lanes) 个连续词。
        nword = (gather_cnt + word_lanes - 1) // word_lanes
        for m in range(lanes):
            for w in range(nword):
                chunk = rows[m][w * word_lanes:(w + 1) * word_lanes]
                self.kernels[0].ur_bank.storage[dst_bank][dst_addr + m * nword + w] = \
                    word_from_lanes(chunk)
        self.logger.info(
            f"[SMI_DS_T] gather {gather_cnt} SMC × {lanes} lanes → transpose → "
            f"scatter {lanes} rows × {nword} words to SMC_0 bank{dst_bank}@{dst_addr}")

    def setup_smi_ds_t_trigger(self, hex_path: str, cfg: dict):
        """ADR-034 Stage-I 2b + S2-D1：装载 SMI_DS_T 指令流 + 建真 step-driven 单元
        （cluster 级单实例，known-risk ②：SMI 非 per-SMC，全局单次非每 kernel）。

        - **2b 触发**：转置由 SMI_DS_T 指令 VLD 触发（替 run-前无条件 apply）。
        - **ADR-036 S2 流式 step-driven**：建一等 `SmiDsTUnit`（表596 接口寄存器 DATA_OUT/
          DATA_OUT_VLD/INST_DONE），数据经 `evaluate({valid,tile})→tick()（固定 latency pipeline，
          每拍发 1 行词 DATA_OUT）` 流过单元，**替 apply_smi_ds_t 的 storage-to-storage backdoor**。
          latency 取 `hw_spec.delays["SMI_DS_T"]`(=16)，与 SMT 的 a_read=t[transpose]+latency 双侧单源
          耦合（hco ac50587 实证：任何解耦 → e2e exit=1 loud-fail，非静默错）。
        vld=0（破坏微指令）→ 不触发 → a_read 读未转置 bank0 → e2e FAIL（指令驱动可证伪）。"""
        from smc_simulator.control.iq_unit import IQUnit
        from smc_simulator.core.decoder import InstructionDecoder
        from smc_simulator.units.smi_ds_t_unit import SmiDsTUnit
        # 从 HardwareSpec.modules（与 emitter 同一 SSoT = hw_config/modules.yaml）建解码器：
        # spec_loader.get_unit("SMI_DS_T") 返 None（无 specs/units/smi_ds_t.yaml）→ payload_width=0
        # → VLD 偏移错位（读 bit40 而非 bit45）→ 永不触发。用 modules 的 5bit code_fields 校准。
        module = None
        if self.kernels:
            hw_spec = getattr(next(iter(self.kernels.values())), "hw_spec", None)
            module = hw_spec.modules.get("SMI_DS_T") if hw_spec else None
        decoder = InstructionDecoder(module) if module else None
        iq = IQUnit('smi_ds_t', decoder=decoder, en_inc=False)
        iq.load_hex(hex_path)
        self.smi_ds_t_iq = iq
        self.smi_ds_t_cfg = dict(cfg)
        # ADR-035 D2：element_bits 选 fp16/fp32 codec；word_lanes 由其推导（128b 词 / 元素位宽）
        element_bits = int(cfg.get("element_bits", 32))
        word_lanes = int(cfg.get("word_lanes", 128 // element_bits))
        # ADR-036 S2：固定 latency（转置触发→第一行词）取 pipeline_delays.yaml 的 SMI_DS_T（=16），
        # 与 SMT 排程的 a_read=t[transpose]+SMI_DS_T_lat 对齐（scatter 写 bank0 先于 a_read 读）。
        smi_lat = int(module.get("latency", 0)) if isinstance(module, dict) else 0
        if hw_spec is not None and hasattr(hw_spec, "delays"):
            smi_lat = int(hw_spec.delays.get("SMI_DS_T", smi_lat))
        self.smi_ds_t_unit = SmiDsTUnit(
            smc_num=int(cfg["gather_smc_cnt"]), lanes=int(cfg["lanes"]),
            word_lanes=word_lanes, element_bits=element_bits, latency=smi_lat)
        self.smi_ds_t_started = False
        self.smi_ds_t_done = False
        self.smi_ds_t_fired = False

    def _audit_smi_ds_t(self, unit: str, tag: str, smc_id: int, port: int,
                        bank: int, addr: int, val: int):
        """ADR-034 S2-D1b（hco a681b91 订正）：把 SMI_DS_T datapath 的 **UR 端点**（gather 源读 / scatter 目的写）
        打成 AUDIT-DATA，**Unit 归属 = 实际访存单元 UR-READ/UR-WRITE**（表596：SMI_DS_T 仅 CRD_SHIFTDN 输入、
        无 UR 端口；bank4 读 = SMC_s 的 UR-read 占其 R 端口、bank0 写 = SMC_0 的 UR-write 占 W 端口+参与 bank 冲突）。
        Tag 标 DST-GATHER/DST-SCATTER 区分于 compute UR 访存；Port 为 model-A 占位（RTL 端口由调度定）。
        SHIFTDN/SHIFTUP 搬运段未建模（debt），故只发 UR 端点事件。使 sim log 可与 VCS **UR 端点逐事件 diff**。"""
        print(f">>> [AUDIT-DATA] SMC_ID:{smc_id} Cyc:{self.cycle_count} Unit:{unit} "
              f"Tag:{tag} Port:{port} Bank:{bank} Addr:{addr} V0:{val & ((1 << 128) - 1):032x}", flush=True)

    def _tick_smi_ds_t_trigger(self):
        """S2-D1 真 step-driven：kernels step 前驱动 SMI_DS_T 单元。
        - 未触发：evaluate SMI_DS_T IQ；VLD 帧 active(exec_active) → 经 SHIFTDN gather 各 SMC 列对齐
          UR 行 latch 进单元 CRD_SHIFTDN，execute() 转置（gather-before-scatter gate）。
        - 已触发未完成：每拍 emit_step() 发一个 DATA_OUT 词 → scatter 写 SMC_0 bank0；INST_DONE 即完成。
        调度保证 t[transpose] < t[a_read]，scatter 逐词写先于 a_read 逐行读。
        S2-D1b：gather/scatter 端点 emit AUDIT-DATA 事件（sim-VCS diff 可观测）。"""
        unit = self.smi_ds_t_unit
        if unit is None or self.smi_ds_t_done:
            return
        cfg = self.smi_ds_t_cfg
        if not self.smi_ds_t_started:
            iq = self.smi_ds_t_iq
            iq.evaluate({}, cycle=self.cycle_count)
            if not getattr(iq, "exec_active", False):
                iq.tick()
                return
            # CRD_SHIFTDN：gather 各 SMC 列对齐 UR 行（model-A：SHIFTDN-delivered Tile = 读各 SMC SRC_BANK）。
            # codec 随精度（element_bits）：fp16 16-bit lane / fp32 32-bit lane（ADR-035 D2）。
            sb, sa = int(cfg["src_bank"]), int(cfg["src_addr"])
            gc, ln = int(cfg["gather_smc_cnt"]), int(cfg["lanes"])
            # ADR-036 S3-a：real-gather（cfg 带 src_read_port）→ 从各 SMC 的 ds_src 捕获寄存器取数
            #   （= 真固件 UR 读指令把 bank{src_bank} 列对齐激活读入该端口、由 set_dst_src_capture 快照），
            #   **删 storage[sb][sa] 直读 backdoor**。SHIFTDN 下行搬运段仍未建模（DEBT-S4）。
            # 非 real-gather（None）→ 维持 model-A storage 直读（A/B 护栏的 OFF 侧）。
            src_port = cfg.get("src_read_port")
            cols = []
            for s in range(gc):
                if src_port is not None:
                    word = self.kernels[s]._dst_src_capture_data  # 真端口读快照（无 backdoor）
                else:
                    word = self.kernels[s].ur_bank.storage[sb][sa]  # model-A storage backdoor
                # S2-D1b（hco）：bank{src_bank} 读 = SMC_s 的 UR-read（占其 R 端口）；SHIFTDN 下行搬运未建模(debt)
                self._audit_smi_ds_t("UR-READ", "DST-GATHER", s, int(src_port or 0), sb, sa, word)
                cols.append(unit._lanes_from_word(word, ln))
            # ADR-036 S2：流式触发（evaluate，无 gather-gate），转置行词经固定 latency pipeline 逐拍出。
            unit.evaluate({"valid": 1, "tile": cols}, cycle=self.cycle_count)
            # 转置内部无对外可观测信号（表596 仅 DATA_OUT/INST_DONE）→ 非数据 marker（不进 AUDIT-DATA，diff 脚本天然过滤）
            self.logger.info(
                f"[SMI_DS_T] Cyc{self.cycle_count} TRANSPOSE shape {gc}x{ln} (streaming lat={unit.latency})")
            self.smi_ds_t_started = True
            self.smi_ds_t_fired = True
            iq.tick()
            self.logger.info(
                f"[SMI_DS_T] instruction-driven transpose started at cycle {self.cycle_count}")
        # scatter：tick 固定 latency pipeline；DATA_OUT_VLD 拍发一个 DATA_OUT 词 → 写 SMC_0 bank0。
        # latency 窗口内 tick() 返 None（气泡，不写），到点后每拍发一行词（线速）。
        emit = unit.tick()
        if emit is not None:
            off, word = emit
            db, da = int(cfg["dst_bank"]), int(cfg["dst_addr"])
            self.kernels[0].ur_bank.storage[db][da + off] = word
            # S2-D1b（hco）：bank0 写 = SMC_0 的 UR-write（占 W 端口、参与 bank 冲突）；SHIFTUP 上游搬运未建模(debt)
            self._audit_smi_ds_t("UR-WRITE", "DST-SCATTER", 0, 0, db, da + off, word)
        if unit.inst_done:
            self.smi_ds_t_done = True
            self.logger.info(f"[SMI_DS_T] INST_DONE at cycle {self.cycle_count}")

    def step(self):
        # 0. ADR-034 2b: SMI_DS_T 指令驱动触发（kernels step 前，bank0 写先于 a_read 读）
        self._tick_smi_ds_t_trigger()

        # 1. ADR-019 D5: 跨 SMC chain 注入（上游 chain output → 本 kernel chain_inputs）
        self._propagate_chain_inputs()

        # 2. Step all kernels
        for k_id, kernel in self.kernels.items():
            try:
                kernel.step()
            except Exception as e:
                self.logger.error(f"Kernel {k_id} error: {e}")

        # 2.5 ADR-023 D2(B): SHIFTUP broadcast 同拍单源 fan-out（kernel.step 后，dr_d 已 tick）
        self._broadcast_shiftup_fanout()

        # 3. Step Bus
        completed_transfers = self.bus.tick()
        
        # 3. Handle completed transfers (Write to Destination Kernel)
        for req in completed_transfers:
            target_kernel = self.kernels.get(req.dst_cluster_id)
            if target_kernel:
                # Direct memory access to target UR (Backdoor write)
                # In real HW, this would go through a write port or DMA engine
                if req.data is None:
                    # If data wasn't pre-fetched, read it now (Magic instantaneous read if not modeled)
                    src_kernel = self.kernels.get(req.src_cluster_id)
                    req.data = src_kernel.read_ur_bank(req.src_bank, req.src_addr)
                
                # Write to destination
                # We need a way to write to UR without going through the pipeline if we want to simulate DMA
                # ParallelKernel doesn't expose raw storage write easily except load_hex.
                # Let's add a helper or access storage directly.
                target_kernel.ur_bank.storage[req.dst_bank][req.dst_addr] = req.data
                self.logger.info(f"[System] DMA Write: Kernel {req.dst_cluster_id} Bank {req.dst_bank} Addr {req.dst_addr} <= {req.data:x}")

        self.cycle_count += 1

    def transfer(self, src_id: int, src_bank: int, src_addr: int, dst_id: int, dst_bank: int, dst_addr: int):
        """Initiate a transfer (External DMA controller behavior)"""
        # Read data immediately for simplicity in this model, or defer it
        src_kernel = self.kernels.get(src_id)
        data = src_kernel.read_ur_bank(src_bank, src_addr)
        
        req = TransferRequest(
            src_cluster_id=src_id, src_bank=src_bank, src_addr=src_addr,
            dst_cluster_id=dst_id, dst_bank=dst_bank, dst_addr=dst_addr,
            data=data
        )
        self.bus.request_transfer(req)

    def run(self, cycles: int):
        self.logger.info(f"System Cluster Run for {cycles} cycles")
        for _ in range(cycles):
            self.step()
