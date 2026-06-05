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
        from smc_simulator.units.smi_ds_t_unit import (
            SmiDsTUnit, fp32_lanes_from_word, word_from_fp32_lanes,
        )
        gather_cnt = int(cfg["gather_smc_cnt"])
        lanes = int(cfg["lanes"])
        src_bank, src_addr = int(cfg["src_bank"]), int(cfg["src_addr"])
        dst_bank, dst_addr = int(cfg["dst_bank"]), int(cfg["dst_addr"])
        if gather_cnt > len(self.kernels):
            raise ValueError(f"SMI_DS_T gather_smc_cnt={gather_cnt} > num_kernels={len(self.kernels)}")

        unit = SmiDsTUnit(smc_num=gather_cnt, lanes=lanes)
        # gather：逐 SMC 读列对齐激活（SHIFTDN gather 的 model-A 数值等价）
        for s in range(gather_cnt):
            word = self.kernels[s].ur_bank.storage[src_bank][src_addr]
            unit.gather(s, fp32_lanes_from_word(word, lanes))
        # transpose：gather 全到齐才出（未到齐抛错 = gather-before-scatter 硬约束）
        rows = unit.transpose()        # rows[m] = 行对齐激活 X[m,:]（gather_cnt 个元素）
        # scatter/write：行对齐激活写回 SMC_0 dst（SHIFTUP scatter 的 model-A 数值等价）
        for m in range(lanes):
            self.kernels[0].ur_bank.storage[dst_bank][dst_addr + m] = word_from_fp32_lanes(rows[m])
        self.logger.info(
            f"[SMI_DS_T] gather {gather_cnt} SMC × {lanes} lanes → transpose → "
            f"scatter {lanes} rows to SMC_0 bank{dst_bank}@{dst_addr}")

    def step(self):
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
