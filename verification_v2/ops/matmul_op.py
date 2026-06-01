import numpy as np
from typing import List, Dict, Any
from .base import BaseOperator
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS


def _inject_shiftup_broadcast(ops: List[Any], broadcast_names: set, spec: Any) -> List[Any]:
    """ADR-025 D2 / ADR-023 D2(B)：对 broadcast 输入的 UR_READ 后插 SHIFTUP MIROp，
    消费者 RTOVR sel 改为 SHIFTUP_D=29；sim 由 cluster._broadcast_shiftup_fanout 同拍 fan-out。
    复用 configurable._inject_shiftup_broadcast 同款逻辑（单广播源，避开 multi-broadcast 限制）。"""
    from ir.mir.mir_op import MIROp

    if not broadcast_names:
        return ops
    smc_num = 32
    if spec is not None and hasattr(spec, "params"):
        smc_num = getattr(spec.params, "PARAM_SMC_NUM", 32)
    smc_id_en = smc_num - 1
    SHIFTUP_D_SEL = 29
    # sim/smc_simulator/core/kernel.py：SHIFTUP 本地 data = ur-r0 读端口 → 广播源 pin 到 ur-r0
    UR_R0_IQ_ID = 30
    if spec is not None and hasattr(spec, "config"):
        UR_R0_IQ_ID = spec.config.get("iq_registry", {}).get("UR_READ", 30)

    ur_to_shiftup: Dict[str, str] = {}
    new_ops = list(ops)
    for op in ops:
        if (op.op_type.upper() == "UR_READ"
                and op.metadata.get("meta_name") in broadcast_names
                and op.metadata.get("role") == "input"):
            if len(ur_to_shiftup) == 0:
                op.metadata["iq_id"] = UR_R0_IQ_ID  # 单广播源 pin ur-r0
            sup_id = f"shiftup_{op.trace_id}"
            sup_op = MIROp(
                op_type="SHIFTUP", logic_cycle=0, trace_id=sup_id,
                metadata={
                    "depends_on": [op.trace_id],
                    "iq_type": RT.SHIFTUP,
                    "broadcast": 1,
                    "smc_id_en": smc_id_en,
                    "smc_id": 0,
                    "math": f"shiftup_broadcast({op.metadata.get('meta_name')})",
                    "dep_port": "D",
                },
            )
            new_ops.append(sup_op)
            ur_to_shiftup[op.trace_id] = sup_id

    if not ur_to_shiftup:
        return ops
    shiftup_ids = set(ur_to_shiftup.values())
    for op in new_ops:
        if op.trace_id in shiftup_ids:
            continue
        deps = op.metadata.get("depends_on", [])
        if any(d in ur_to_shiftup for d in deps):
            op.metadata["depends_on"] = [
                ur_to_shiftup[d] if d in ur_to_shiftup else d for d in deps
            ]
            if op.op_type.upper() == "RTOVR":
                op.metadata["sel"] = SHIFTUP_D_SEL
    # 重建 rtovr_deps（清旧 stale UR_READ 条目）
    for op in new_ops:
        if "rtovr_deps" in op.metadata:
            op.metadata["rtovr_deps"] = {}
    for op in new_ops:
        if op.op_type.upper() != "RTOVR":
            continue
        prod_ids = op.metadata.get("depends_on", [])
        if not prod_ids:
            continue
        prod_id = prod_ids[0]
        for consumer in new_ops:
            if op.trace_id in consumer.metadata.get("depends_on", []):
                consumer.metadata.setdefault("rtovr_deps", {})[prod_id] = {
                    "dvr": op.metadata.get("dvr", -1),
                    "sel": op.metadata.get("sel", -1),
                }
    return new_ops


class ManualMatmulParallelNOperator(BaseOperator):
    """ADR-025：8-SMC N 维 matmul（output-stationary）。
    广播激活向量 a（SHIFTUP，单源）+ 每 SMC 与本地权重列 b_n 点积
    （SMC 内 FP32ADDTREE5TO1 + 跨 tile cumulative 累加）→ 每 SMC 输出 y_n（partitioned）。
    y_n = Σ_i a[i]·b_n[i]。复用 ManualMatmulOperator 的 SMC 内点积结构（绕 configurable
    4-operand UNSAT），单广播流（避开 multi-broadcast sim 限制）。"""

    parallel_dim = "N"

    def __init__(self, **kwargs):
        pass

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        return {
            "a": {"bank": 0, "offset": 0, "role": "input", "precision": "fp32",
                  "stride": 1, "smc_layout": "broadcast"},
            "b": {"bank": 1, "offset": 0, "role": "input", "precision": "fp32",
                  "stride": 1, "smc_layout": "partitioned"},
            "y": {"bank": 5, "offset": 0, "role": "output", "precision": "fp32",
                  "smc_layout": "partitioned"},
        }

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        rng = np.random.default_rng(0)
        a = rng.uniform(1.0, 5.0, n_rows).astype(np.float32)          # broadcast 1D
        b = rng.uniform(1.0, 5.0, (smc_num, n_rows)).astype(np.float32)  # partitioned 2D
        return {"a": a, "b": b}

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a = input_data["a"]
        b = input_data["b"]
        smc_num = b.shape[0]
        # y_n = 全向量点积 dot(a, b_n)（4-lane addtree + cumulative 跨 tile 累加终值）。
        # shape (smc_num, 1)：每 SMC 单标量输出（stride=0 原地累写），verifier 走 per-SMC 8/8。
        golden = np.array([[np.dot(a, b[n]).astype(np.float32)] for n in range(smc_num)],
                          dtype=np.float32)
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]
        b = input_data["b"]
        a_addr = self.get_bank_addr("a", spec)
        b_addr = self.get_bank_addr("b", spec)
        # broadcast a：只注入 SMC_0（OQ 单源），follower 由 SHIFTUP fan-out 供值
        programs[0].inject_vector(a_addr, a, stride=1)
        # partitioned b：每 SMC 注入自己的列
        for k, prog in enumerate(programs):
            prog.inject_vector(b_addr, b[k], stride=1)

    def get_dfg(self, spec: Any = None) -> List[Any]:
        b = MIROpBuilder()
        # 单 4-lane 点积链：a(broadcast) × b(partitioned) → FPMUL → addtree S0 reduce → y_n
        ar = b.make_op("UR_READ", "a_read", RT.UR_READ, [],
                       meta_name="a", logical_addr=0 * 256 + 0, stride=1, role="input", iq_id=30)
        br = b.make_op("UR_READ", "b_read", RT.UR_READ, [],
                       meta_name="b", logical_addr=1 * 256 + 0, stride=1, role="input", iq_id=31)
        rt_a = b.make_op("RTOVR", "rt_a_FPMUL_S0", PS.FPMUL_S0, [ar])
        rt_b = b.make_op("RTOVR", "rt_b_FPMUL_S1", PS.FPMUL_S1, [br])
        mul = b.make_op("FPMUL", "inl_mul", RT.FPMUL, [rt_a, rt_b], iq_id=3)
        # 单路乘积直喂 addtree（无 matmul_op 的 4 路 RTOVR 对齐冲突，去 temp bank 中转避免吞吐减半）
        rt_sum = b.make_op("RTOVR", "rt_mul_FP32ADDTREE5TO1_S0",
                           PS.FP32ADDTREE5TO1_S0, [mul])
        # cumulative 跨迭代累加 old_i 来自 pipeline 末级（延迟 4）→ 必须 II>=4 否则累加被打断。
        # 自环 loop_carried 边让 IIAnalyzer 取 rec-MII>=4（matmul_op 旧版靠 4 路 FPMUL 资源 II
        # 偶然兜住，单路乘积无此保护，须显式建模递归）。
        y_sum = b.make_op("FP32ADDTREE5TO1", "y_0", RT.FP32ADDTREE5TO1, [rt_sum],
                          cumulative=True, reduction_per_channel=4, iq_id=26,
                          loop_carried_depends_on={"y_0": 4})
        rt_out = b.make_op("RTOVR", "rt_y_0_UR_W0_S", PS.UR_W0_S, [y_sum], sel=26)
        # 输出 = 每 SMC 单标量 dot(a,b_n)：stride=0 原地累写，addr0 终值 = 完整点积
        b.make_op("UR_WRITE", "out_obj_0", RT.UR_WRITE, [rt_out],
                  logical_addr=5 * 256 + 0, stride=0, role="output", is_output=True, iq_id=35)

        ops = b.get_dfg()
        # rtovr_deps 自动补全
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
        # SIMD loop 元数据（256 rows / 4 lanes → 64 迭代）
        for op in ops:
            op.metadata.update({"shape": [256], "inner_loops": 63, "outer_loops": 0,
                                "inner_inc": 1, "outer_inc": 0})
        # ADR-025 D1.2：broadcast 注入 SHIFTUP
        ops = _inject_shiftup_broadcast(ops, {"a"}, spec)
        return ops


class ManualMatmulQOperator(BaseOperator):
    """ADR-026 Stage-1：Matmul_Q（matrix×matrix）缩放实例。
    广播激活行 X[i,:]（左，单源）× 每 SMC 本地权重列 W[:,s] 点积 → Q[i,s]=Σ_k X[i,k]·W[k,s]。
    Stage-1：K=4（单 tile/行）、cols_per_smc=1、多左行 M（每 SMC 产 M 个输出）。
    DS transpose 作 Stage-1b（本轮激活按行对齐预排，不经 DS）。
    每行独立输出（K=4 单 tile 无跨行累加）。"""

    parallel_dim = "N"
    K = 4          # 单 tile 收缩维（4-lane）
    # M ≤ addtree pipeline 深度(latency=4)：II=1 下 row 0..M-1 各 fire 在 4 拍内，
    # old_i 尚未写回 → 每行 old=0 独立。M>4 时 row i 会累加 row(i-4)（dr_d0 距离-4 反馈）。
    # M>4 的正解 = 8 行映射 8 个 addtree 通道(S0..S7)或 UR 部分和，记 Stage-2（ADR-026）。
    M = 4          # 左行数 = 每 SMC 输出数（Stage-1 ≤ pipeline 深度）

    def __init__(self, **kwargs):
        pass

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        return {
            "a": {"bank": 0, "offset": 0, "role": "input", "precision": "fp32",
                  "stride": 1, "smc_layout": "broadcast"},      # 激活：逐行进一个 4-lane 词
            "b": {"bank": 1, "offset": 0, "role": "input", "precision": "fp32",
                  "stride": 1, "smc_layout": "partitioned"},    # 权重列：注入时平铺 M 份，逐行读同值
            "y": {"bank": 5, "offset": 0, "role": "output", "precision": "fp32",
                  "smc_layout": "partitioned"},
        }

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        rng = np.random.default_rng(0)
        # 激活 X[M,K] 广播：flat (M*K,)，第 i 行 = a[i*K:(i+1)*K]
        a = rng.uniform(1.0, 5.0, self.M * self.K).astype(np.float32)
        # 权重 W[:,s]：每 SMC 一列（K 个），平铺 M 份 → (smc_num, M*K)。
        # 平铺让 b-read 逐行(stride1/inner_inc1)读到同一列，且 auditor 用同一 input_data["b"]
        # 建模 → sim/auditor/golden 三者一致（避免 auditor 假阳性）。
        b_col = rng.uniform(1.0, 5.0, (smc_num, self.K)).astype(np.float32)
        b = np.concatenate([np.tile(b_col[s], self.M).reshape(1, -1) for s in range(smc_num)],
                           axis=0).astype(np.float32)        # (smc_num, M*K)
        return {"a": a, "b": b}

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a = input_data["a"]
        b = input_data["b"]
        smc_num = b.shape[0]
        X = a.reshape(self.M, self.K)                       # [M, K]
        # b 已平铺 (smc_num, M*K)，每 SMC 权重列 = 前 K 个
        bcol = b[:, :self.K]                                # (smc_num, K)
        b = bcol
        # Q[s, i] = dot(X[i,:], W[:,s])，每 SMC s 输出 M 个（转置间插：per-SMC 一列结果）。
        # Stage-1 输出布局：每行结果写一个 128-bit 词的 lane0（addtree D0 lane0），lanes 1-3=0。
        # → bank 实际内容 = [Q_i, 0, 0, 0] × M 个词。verifier _read_sim_output 按 4/词解包，
        # 故 golden shape (smc_num, 4*M)：Q[s,i] 落 4*i，其余 0（忠实反映稀疏布局）。
        # 密集 4/词打包 = Stage-2（cols_per_smc=4，4 列填满一词），见 ADR-026 DEBT-026-PACK。
        golden = np.zeros((smc_num, 4 * self.M), dtype=np.float32)
        for s in range(smc_num):
            for i in range(self.M):
                golden[s, 4 * i] = float(np.dot(X[i], b[s]))
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]
        b = input_data["b"]
        a_addr = self.get_bank_addr("a", spec)
        b_addr = self.get_bank_addr("b", spec)
        programs[0].inject_vector(a_addr, a, stride=1)       # 广播激活仅注入 SMC_0（M 行 flat）
        for k, prog in enumerate(programs):
            prog.inject_vector(b_addr, b[k], stride=1)        # 权重列（K 个）；b-read inner_inc=0 逐行复用

    def get_dfg(self, spec: Any = None) -> List[Any]:
        b = MIROpBuilder()
        # 逐行点积：a 行 tile(broadcast, stride4 逐行进) × b 列(stride0 复用) → FPMUL → addtree 4-lane reduce → Q[i,s]
        ar = b.make_op("UR_READ", "a_read", RT.UR_READ, [],
                       meta_name="a", logical_addr=0 * 256 + 0, stride=1, role="input", iq_id=30)
        br = b.make_op("UR_READ", "b_read", RT.UR_READ, [],
                       meta_name="b", logical_addr=1 * 256 + 0, stride=1, role="input", iq_id=31)
        rt_a = b.make_op("RTOVR", "rt_a_FPMUL_S0", PS.FPMUL_S0, [ar])
        rt_b = b.make_op("RTOVR", "rt_b_FPMUL_S1", PS.FPMUL_S1, [br])
        mul = b.make_op("FPMUL", "inl_mul", RT.FPMUL, [rt_a, rt_b], iq_id=3)
        rt_sum = b.make_op("RTOVR", "rt_mul_FP32ADDTREE5TO1_S0", PS.FP32ADDTREE5TO1_S0, [mul])
        # 每行独立输出：无 loop_carried 自环（不跨行累加）；K=4 单 tile 一拍 reduce 完。
        y_sum = b.make_op("FP32ADDTREE5TO1", "y_0", RT.FP32ADDTREE5TO1, [rt_sum],
                          cumulative=False, reduction_per_channel=4, iq_id=26)
        rt_out = b.make_op("RTOVR", "rt_y_0_UR_W0_S", PS.UR_W0_S, [y_sum], sel=26)
        # 每行写自己的输出地址（stride=1 逐行进）
        b.make_op("UR_WRITE", "out_obj_0", RT.UR_WRITE, [rt_out],
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
        # M 行 → inner_loops = M-1；激活/输出逐行推进(inner_inc=1)
        for op in ops:
            op.metadata.update({"shape": [self.M], "inner_loops": self.M - 1, "outer_loops": 0,
                                "inner_inc": 1, "outer_inc": 0})
        ops = _inject_shiftup_broadcast(ops, {"a"}, spec)
        return ops


class ManualMatmulOperator(BaseOperator):
    """
    手动编排的 Matmul DFG。
    通过将中间乘积写入 4 个不同的物理 Bank，然后再在同一拍并行读取，
    完美避开了由于单物理乘法器串行发射与 RTOVR 强对齐选通对齐冲突而导致的 UNSAT 矛盾。
    """
    def __init__(self, **kwargs):
        pass

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        # 定义 8 个输入张量和 1 个输出张量
        return {
            "a0": {"bank": 0, "offset": 0, "role": "input", "precision": "fp32", "stride": 4},
            "a1": {"bank": 0, "offset": 1, "role": "input", "precision": "fp32", "stride": 4},
            "a2": {"bank": 0, "offset": 2, "role": "input", "precision": "fp32", "stride": 4},
            "a3": {"bank": 0, "offset": 3, "role": "input", "precision": "fp32", "stride": 4},
            "b0": {"bank": 1, "offset": 0, "role": "input", "precision": "fp32", "stride": 4},
            "b1": {"bank": 1, "offset": 1, "role": "input", "precision": "fp32", "stride": 4},
            "b2": {"bank": 1, "offset": 2, "role": "input", "precision": "fp32", "stride": 4},
            "b3": {"bank": 1, "offset": 3, "role": "input", "precision": "fp32", "stride": 4},
            "y":  {"bank": 5, "offset": 0, "role": "output", "precision": "fp32"}
        }

    def prepare_input(self, n_rows: int) -> Dict[str, np.ndarray]:
        # 固定 seed 防止 flaky FAIL：cumulative=True 在 tol=5e-04 边界对 FP32 累加输入敏感
        rng = np.random.default_rng(0)
        inputs = {}
        for name in ["a0", "a1", "a2", "a3", "b0", "b1", "b2", "b3"]:
            inputs[name] = rng.uniform(1.0, 5.0, n_rows).astype(np.float32)
        return inputs

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a0 = input_data["a0"]
        a1 = input_data["a1"]
        a2 = input_data["a2"]
        a3 = input_data["a3"]
        b0 = input_data["b0"]
        b1 = input_data["b1"]
        b2 = input_data["b2"]
        b3 = input_data["b3"]

        n_rows = len(a0)
        golden_y = np.zeros(n_rows, dtype=np.float32)
        
        acc0 = 0.0
        acc1 = 0.0
        acc2 = 0.0
        acc3 = 0.0
        
        for iter_idx in range(n_rows // vector_lanes):
            start = iter_idx * vector_lanes
            end   = start + vector_lanes
            
            # [V8.1] 硬件永不清零 old_i (dr_d0)，所以跨迭代持续累加
            acc0 += np.sum(a0[start:end] * b0[start:end])
            acc1 += np.sum(a1[start:end] * b1[start:end])
            acc2 += np.sum(a2[start:end] * b2[start:end])
            acc3 += np.sum(a3[start:end] * b3[start:end])
            
            golden_y[start + 0] = acc0
            golden_y[start + 1] = acc1
            golden_y[start + 2] = acc2
            golden_y[start + 3] = acc3

        return {"y": golden_y, "intermediates": []}

    def get_dfg(self, spec: Any = None) -> List[Any]:
        b = MIROpBuilder()
        
        # 🛸 深度对齐：定义临时物理暂存 Bank，分别写入 4 个空闲物理 Bank（Bank 2, 3, 4, 6）以消除 Bank 冲突
        temp_banks = [2, 3, 4, 6]
        
        # 1. 创建输入读取节点 (a0..a3, b0..b3 全部使用 stride=4，配合对称性破缺 iq_id)
        a_reads = []
        b_reads = []
        for i in range(4):
            ar_iq = 30 + (i % 2) * 2
            br_iq = 31 + (i % 2) * 2
            # a_i in Bank 0, stride 4
            ar = b.make_op(
                "UR_READ", f"a{i}_read", RT.UR_READ, [], 
                meta_name=f"a{i}", logical_addr=0 * 256 + i, stride=4, role="input", iq_id=ar_iq
            )
            # b_i in Bank 1, stride 4
            br = b.make_op(
                "UR_READ", f"b{i}_read", RT.UR_READ, [], 
                meta_name=f"b{i}", logical_addr=1 * 256 + i, stride=4, role="input", iq_id=br_iq
            )
            a_reads.append(ar)
            b_reads.append(br)

        # 2. 创建乘积和暂存节点 (写入中间 Bank 2,3,4,6，且全部使用 stride=4 以保持与输入 a/b 完全一致的读周期步长)
        temp_reads = []
        for i in range(4):
            # (1) RTOVR 搬运
            rt_a = b.make_op("RTOVR", f"rt_a{i}_FPMUL_S0", PS.FPMUL_S0, [a_reads[i]])
            rt_b = b.make_op("RTOVR", f"rt_b{i}_FPMUL_S1", PS.FPMUL_S1, [b_reads[i]])
            
            # (2) FPMUL 乘法
            mul = b.make_op("FPMUL", f"inl_mul{i}", RT.FPMUL, [rt_a, rt_b], iq_id=3)
            
            # (3) 乘积经 RTOVR 搬运到写总线 (让 SMT 决定写向哪个 UR_W_S)
            write_ps = getattr(PS, f"UR_W{i}_S")
            rt_write = b.make_op("RTOVR", f"rt_mul{i}_{write_ps.name}", write_ps, [mul], sel=3)
            
            # (4) 写入中间 Bank (使用 stride=4，并且专门分配到 ur-w1..3 写入通道，避免与 ur-w0 的 stride-1 冲突)
            write_addr = temp_banks[i] * 256 + 0
            write_name = f"temp_write{i}"
            tr_w_iq = 36 + (i % 3)
            b.make_op(
                "UR_WRITE", write_name, RT.UR_WRITE, [rt_write], 
                logical_addr=write_addr, stride=4, role="intermediate", iq_id=tr_w_iq
            )
            
            # (5) 并行读出 (使用 stride=4 并且映射到 ur-r0..3。这样所有 4 个读取通道的步长完全均一为 4)
            tr_iq = 30 + i
            tr = b.make_op(
                "UR_READ", f"temp_read{i}", RT.UR_READ, [write_name], 
                logical_addr=write_addr, stride=4, role="intermediate", iq_id=tr_iq
            )
            temp_reads.append(tr)

        # 3. 创建归约树求和阶段
        rt_sums = []
        for i in range(4):
            rt_sum = b.make_op(
                "RTOVR", f"rt_temp_read{i}_FP32ADDTREE5TO1_S{i}", 
                getattr(PS, f"FP32ADDTREE5TO1_S{i}"), [temp_reads[i]]
            )
            rt_sums.append(rt_sum)

        # 4. FP32ADDTREE5TO1 求和
        y_sum = b.make_op(
            "FP32ADDTREE5TO1", "y_0", RT.FP32ADDTREE5TO1, rt_sums, 
            cumulative=True, reduction_per_channel=4, iq_id=26
        )

        # 5. y_0 输出写回
        rt_out = b.make_op("RTOVR", "rt_y_0_UR_W0_S", PS.UR_W0_S, [y_sum], sel=26)

        b.make_op(
            "UR_WRITE", "out_obj_0", RT.UR_WRITE, [rt_out], 
            logical_addr=5 * 256 + 0, stride=1, role="output", is_output=True, iq_id=35
        )

        ops = b.get_dfg()

        # 🛸 自动补全 RTOVR 增广边依赖 (rtovr_deps)，使 SMT 时序对齐和 anti-overwrite 完全起效
        for op in ops:
            if op.op_type.upper() == "RTOVR":
                prod_ids = op.metadata.get("depends_on", [])
                if prod_ids:
                    prod_id = prod_ids[0]
                    for consumer in ops:
                        if op.trace_id in consumer.metadata.get("depends_on", []):
                            consumer.metadata.setdefault("rtovr_deps", {})[prod_id] = {
                                "dvr": op.metadata.get("dvr", -1),
                                "sel": op.metadata.get("sel", -1)
                            }

        # 🛸 [POST-PROCESS] 注入 SIMD loop 信息以通过后验编译器检查与 Header 审计
        # 对于 256 rows, 4 vector lanes, 对应 64 次迭代 (loops = 63)
        for op in ops:
            op.metadata.update({
                "shape": [256],
                "inner_loops": 63,
                "outer_loops": 0,
                "inner_inc": 1,
                "outer_inc": 0
            })

        for op in ops:
            if op.metadata.get("rtovr_deps"):
                print(f"[DEBUG DFG] Op {op.trace_id} rtovr_deps: {op.metadata['rtovr_deps']}", flush=True)

        return ops
