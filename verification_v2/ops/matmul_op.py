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


class ManualMatmulQ2Operator(BaseOperator):
    """ADR-026 Stage-2a：Matmul_Q cols_per_smc=4（密集 4/词打包，解 DEBT-026-PACK）。

    **交付机制（BROADCAST=True / BCAST_MODE="direct"，真广播，ADR-028）**：**单** a-read
    （broadcast）→ inject 后唯一 SHIFTUP（驻留 DR_SHIFTUP_D）→ COLS 个 FPMUL_S0 RTOVR
    **全消费这一个广播源**（时间维 scatter，Q-b）× 4 权重列 → 4 FPMUL → 4 temp-bank
    往返重定时 → **单 addtree 4 通道 S0..S3** → D0 dense [Q0,Q1,Q2,Q3] 一词 → bank5。
    `--op matmul_q2 --mode e2e` 8 SMC 全 bit-perfect。**SHIFTUP 广播指令在固件中**（非
    model-A 预分发）。

    **可解的关键（ADR-028 axiom 放宽，user RTL 裁定 Q-a 背书）**：原「单广播 a → 4 列」
    撞 SMT UNSAT 的真根因 = `dependency_axioms` 的 RTOVR 锁存约束 `t_latch == prod.t+lat`
    把一个 SHIFTUP 的**全部消费者锁存钉死同一拍** → N 个 RTOVR 同拍写 FPMUL_S0 端口冲突。
    user 确认 DR_SHIFTUP_D 是**驻留**广播值、硬件支持 N 个 RTOVR 在驻留窗口内不同拍各读
    （Q-a），故对 broadcast SHIFTUP 放宽为锁存**区间** [prod.t+lat, prod.t+lat+II-1]，
    各列错拍 scatter → SAT。普通 producer 仍严格 ==。**非 model-A、不碰 sim-core**。

    **单 addtree 4 通道（hco 2026-06-01 重裁，推翻原 B1）**：addtree 有 8 个 per-lane
    独立累加器通道（`fp32_add_tree_unit.py:112-145` `add_result[i]=Si[0..3]+old_i`，
    old_i=输出寄存器 lane i；spec `fp32addtree5to1.yaml:43-48 ACC_CHANNEL count=8` 背书），
    一个 VALID 一拍并行算全 lane 不串扰。原 B1「4 列共用 old_i 互相覆盖」因果倒置。
    债务 DEBT-026-ADDTREE-8CH-RTL：8 通道独立性仅 sim+spec 背书，无 RTL 源，升 Accepted 需 VCS。

    **诊断开关**（BCAST_MODE）：direct=交付真广播路径；indep/buffer/model-A(BROADCAST=False)
    留作历史对照（前两者证伪：buffer SMT-UNSAT、indep sim 多广播 gap；见 ADR-026/027）。
    Stage-2a 聚焦 4-col packing：M=1 单左行（M>4 多行映射 8 通道是 Stage-2b）。
    """

    parallel_dim = "N"
    K = 4          # 单 tile 收缩维（4-lane）
    COLS = 4       # cols_per_smc（真 Matmul_Q 每 SMC 4 列）
    M = 1          # 左行数（Stage-2a 聚焦 4-col 密集打包；M>4 是 Stage-2b）
    BUF_BANK = 7   # 仅 buffer 诊断路径用
    BROADCAST = True   # 交付=True（真广播 direct，ADR-028）；False=model-A 预分发（历史对照）

    def __init__(self, **kwargs):
        pass

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        return {
            "a": {"bank": 0, "offset": 0, "role": "input", "precision": "fp32",
                  "stride": 4, "smc_layout": "broadcast"},     # 激活行 K=4（一个 4-lane 词）
            "b": {"bank": 1, "offset": 0, "role": "input", "precision": "fp32",
                  "stride": 4, "smc_layout": "partitioned"},   # 权重 COLS 列 × K（每列一词）
            "y": {"bank": 5, "offset": 0, "role": "output", "precision": "fp32",
                  "smc_layout": "partitioned"},                # 输出 COLS 个/词（dense）
        }

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        rng = np.random.default_rng(0)
        # 激活 X[M,K] 广播：flat (M*K,)
        a = rng.uniform(1.0, 5.0, self.M * self.K).astype(np.float32)
        # 权重 W：每 SMC COLS 列，每列 K 个 → (smc_num, COLS*K)。
        # 列 c = b[k, c*K:(c+1)*K]（连续存为第 c 个 4-lane 词）。
        b = rng.uniform(1.0, 5.0, (smc_num, self.COLS * self.K)).astype(np.float32)
        # per-列 key b0..b{COLS-1}：供 auditor 按 distinct meta_name 建模每列 expected
        # （否则单 "b" key + 单 slice → 把 4 列都当 col0 → rigid-audit 假阳性）。
        out = {"a": a, "b": b}
        for c in range(self.COLS):
            out[f"b{c}"] = b[:, c * self.K:(c + 1) * self.K].copy()   # (smc_num, K)
        return out

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a = input_data["a"]
        b = input_data["b"]
        smc_num = b.shape[0]
        X = a.reshape(self.M, self.K)                         # [M, K]
        # 每 SMC COLS 列点积：Q[s,i,c]=dot(X[i],W[:,c])，密集 4/词（一行一词，COLS lane）。
        # golden shape (smc_num, M*4)：行 i 的 COLS 个结果落 word i 的 lane 0..COLS-1。
        golden = np.zeros((smc_num, 4 * self.M), dtype=np.float32)
        for s in range(smc_num):
            W = b[s].reshape(self.COLS, self.K)               # [COLS, K]
            for i in range(self.M):
                for c in range(self.COLS):
                    golden[s, 4 * i + c] = float(np.dot(X[i], W[c]))
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]
        b = input_data["b"]
        a_addr = self.get_bank_addr("a", spec)
        b_addr = self.get_bank_addr("b", spec)
        if self.BROADCAST:
            programs[0].inject_vector(a_addr, a, stride=1)     # 广播激活仅注入 SMC_0
        else:
            # 诊断模式：a 复制 COLS 份到每 SMC bank0（COLS 个 4-lane 词）
            a_rep = np.tile(a, self.COLS).astype(np.float32)
            for prog in programs:
                prog.inject_vector(a_addr, a_rep, stride=1)
        for k, prog in enumerate(programs):
            prog.inject_vector(b_addr, b[k], stride=1)         # COLS 列权重（COLS*K 个）

    def get_dfg(self, spec: Any = None) -> List[Any]:
        b = MIROpBuilder()
        buf_base = self.BUF_BANK * 256
        # 交付路径 = "direct"（ADR-028：单广播源 a 喂 COLS 列，靠 broadcast-SHIFTUP RTOVR
        # 采样放宽 axiom 解 SAT）。其余 mode 留作诊断/对照（model-A 预分发 / indep / buffer）。
        BCAST_MODE = getattr(self, "BCAST_MODE", "direct")
        ar_shared = None
        if self.BROADCAST and BCAST_MODE == "direct":
            # ── ADR-028 忠实路径：**单** a-read（broadcast）→ inject 后唯一 SHIFTUP；
            #   COLS 个 FPMUL_S0 RTOVR 全消费它（驻留 DR_SHIFTUP_D，Q-a 硬件支持多读）。
            #   原 strict 采样 axiom 把这 COLS 个 RTOVR 钉死同拍 → UNSAT；放宽后各列错拍读 → SAT。
            ar_shared = b.make_op("UR_READ", "a_read", RT.UR_READ, [],
                                  meta_name="a", logical_addr=0 * 256 + 0, stride=4,
                                  role="input", iq_id=30)
        elif self.BROADCAST and BCAST_MODE == "indep":
            pass  # 4 个独立广播 a-read 在循环内建（各自 SHIFTUP，不共享源）—— sim 多广播缺口诊断
        elif self.BROADCAST:
            # ── buffer 模式（已证 UNSAT，留作对照）──
            ar = b.make_op("UR_READ", "a_read", RT.UR_READ, [],
                           meta_name="a", logical_addr=0 * 256 + 0, stride=4, role="input", iq_id=30)
            rt_abuf = b.make_op("RTOVR", "rt_abuf_UR_W3_S", PS.UR_W3_S, [ar])
            b.make_op("UR_WRITE", "a_buf_write", RT.UR_WRITE, [rt_abuf],
                      logical_addr=buf_base + 0, stride=4, role="intermediate", iq_id=39)

        # ── 2. 各列：本地 a-read（破对称） × 权重列 → FPMUL → 写中间 temp bank ──
        # temp-bank 往返（mirror ManualMatmulOperator）：4 个串行 FPMUL 的乘积写 4 个
        # 不同 temp bank，再并行读回 → 把 4 路串行乘积「重定时」成一拍对齐的 4 通道读，
        # 喂单 addtree。直接 mul→addtree_S{c} 会让 4 路串行输出对不齐单 addtree 发射拍
        # → 结构性 UNSAT（v1 实测 II=4..64 全 UNSAT）。
        temp_banks = [2, 3, 4, 6]
        temp_reads = []
        for c in range(self.COLS):
            # a-source 选择：direct=共享单广播 ar（交付）；indep/buffer/model-A=诊断或对照。
            if self.BROADCAST and BCAST_MODE == "direct":
                a_loc = ar_shared      # COLS 列共消费唯一广播 a-read（→ 唯一 SHIFTUP_D）
            elif self.BROADCAST and BCAST_MODE == "indep":
                # 4 个独立广播 a-read：各 meta_name="a" → inject 各建自己的 SHIFTUP →
                # 各喂自己的 FPMUL_S0（不共享 SHIFTUP_D 源 → 不撞强对齐）。
                # **全部 pin r0**（iq_id=30）：sim shiftup `data`=get_read_data(0)（port0），
                # 4 个广播 a-read 必须都在 r0 才能让各 SHIFTUP 读到 a（否则 dr_d 取空 port0=0）。
                a_loc = b.make_op("UR_READ", f"a_loc{c}", RT.UR_READ, [],
                                  meta_name="a", logical_addr=0 * 256 + 0, stride=4,
                                  role="input", iq_id=30)
            elif self.BROADCAST:
                a_loc = b.make_op("UR_READ", f"a_loc{c}", RT.UR_READ, ["a_buf_write"],
                                  meta_name="abuf", logical_addr=buf_base + 0, stride=4,
                                  role="intermediate", iq_id=30 + c)
            else:
                # 交付路径（BROADCAST=False，model-A 预分发）：直接读 bank0 第 c 个复制词
                # （mirror matmul 的 4 distinct input 读 → SAT）。
                a_loc = b.make_op("UR_READ", f"a_loc{c}", RT.UR_READ, [],
                                  meta_name="a", logical_addr=0 * 256 + c, stride=4,
                                  role="input", iq_id=30 + (c % 2) * 2)
            # 权重列 c：bank1 第 c 个词。direct/indep 路径 r0 留给广播 a → 权重用 r1..r3；
            # 其余路径用与 matmul 同款 31/33 双端口（已证 SAT）。
            _bcast_r0 = self.BROADCAST and BCAST_MODE in ("direct", "indep")
            w_iq = 31 + (c % 3) if _bcast_r0 else 31 + (c % 2) * 2
            # meta_name=f"b{c}"：auditor 据此读 input_data["b{c}"]=列 c（避假阳性）；
            # 实际地址走 logical_addr，与单 "b" 块注入布局一致（sim 读不变）。
            w_c = b.make_op("UR_READ", f"w{c}_read", RT.UR_READ, [],
                            meta_name=f"b{c}", logical_addr=1 * 256 + c, stride=4,
                            role="input", iq_id=w_iq)
            rt_a = b.make_op("RTOVR", f"rt_a{c}_FPMUL_S0", PS.FPMUL_S0, [a_loc])
            rt_w = b.make_op("RTOVR", f"rt_w{c}_FPMUL_S1", PS.FPMUL_S1, [w_c])
            mul = b.make_op("FPMUL", f"inl_mul{c}", RT.FPMUL, [rt_a, rt_w], iq_id=3)
            # 乘积经 RTOVR 搬运到写总线 → 写 temp bank（stride=4 均一读周期）
            write_ps = getattr(PS, f"UR_W{c}_S")
            rt_write = b.make_op("RTOVR", f"rt_mul{c}_{write_ps.name}", write_ps, [mul], sel=3)
            twrite_name = f"temp_write{c}"
            twrite_addr = temp_banks[c] * 256 + 0
            b.make_op("UR_WRITE", twrite_name, RT.UR_WRITE, [rt_write],
                      logical_addr=twrite_addr, stride=4, role="intermediate",
                      iq_id=36 + (c % 3))
            # 并行读回（ur-r0..r3 均一 stride=4）
            tr = b.make_op("UR_READ", f"temp_read{c}", RT.UR_READ, [twrite_name],
                           logical_addr=twrite_addr, stride=4, role="intermediate", iq_id=30 + c)
            temp_reads.append(tr)

        # ── 3. 单 addtree 4 通道 → D0 dense [Q0,Q1,Q2,Q3] ─────────────
        rt_sums = []
        for c in range(self.COLS):
            rt_sum = b.make_op("RTOVR", f"rt_temp_read{c}_FP32ADDTREE5TO1_S{c}",
                               getattr(PS, f"FP32ADDTREE5TO1_S{c}"), [temp_reads[c]])
            rt_sums.append(rt_sum)
        # cumulative=False：K=4 单 tile，每通道一拍 reduce 完；M=1 无跨行复用。
        y_sum = b.make_op("FP32ADDTREE5TO1", "y_0", RT.FP32ADDTREE5TO1, rt_sums,
                          cumulative=False, reduction_per_channel=4, iq_id=26)
        rt_out = b.make_op("RTOVR", "rt_y_0_UR_W0_S", PS.UR_W0_S, [y_sum], sel=26)
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
        # M=1：单迭代（inner_loops=0）。COLS 列在一次迭代内并行/串行打包。
        for op in ops:
            op.metadata.update({"shape": [self.M], "inner_loops": self.M - 1, "outer_loops": 0,
                                "inner_inc": 1, "outer_inc": 0})
        if self.BROADCAST:
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


class ManualFp16MatmulOperator(BaseOperator):
    """ADR-032 S1：FP16 推理 matmul 最小通路（单 SMC，单 tile K=8，M=1）。

    数据通路（v0.4.19 表512-520）：
      a(8×FP16 激活行) → FP16MUL_S0；b(8×FP16 权重列) → FP16MUL_S4567(load_s4567 选 S4)
      → FP16MUL 32 乘出 D0/D1（各 4×FP32，= a[0..3]*b[0..3] / a[4..7]*b[4..7]）
      → RTOVR D0→ADDTREE8TO1_S0、D1→ADDTREE8TO1_S1
      → FP32ADDTREE8TO1 把 8 个 FP32 平衡树求和 → 1 标量 = Σ_{k=0..7} a[k]·b[k]（K=8 点积）
      → 写 bank5 lane0。

    精度：FP16 乘 / FP32 累加（FP16×FP16→FP32 精确，22bit 乘积 ≤ FP32 23bit 尾数）。
    单 SMC（无 parallel_dim）；连续打包、单份寄存器、不 cumulative。
    债务：DEBT-FP16-DBLBUF / -MULTITILE（S2）；满 32 乘 8-D 全用（S2）；8-SMC 广播（S3）。
    """

    K = 8   # 单 tile 收缩维 = FP16MUL 一对源(S0×S4)的 8 lane

    def __init__(self, **kwargs):
        pass

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        return {
            "a": {"bank": 0, "offset": 0, "role": "input", "precision": "fp16", "stride": 1},
            "b": {"bank": 1, "offset": 0, "role": "input", "precision": "fp16", "stride": 1},
            # 输出 FP32：shape [4] 强制读回端按 1 词 4×FP32 解包（read-back 固定 fp32 4-lane），
            # 否则默认 (iters, vector_lanes=8) 会按 fp16 8-lane 误读。lane0=点积，lane1-3=0。
            "y": {"bank": 5, "offset": 0, "role": "output", "precision": "fp32", "shape": [4]},
        }

    def prepare_input(self, n_rows: int) -> Dict[str, np.ndarray]:
        rng = np.random.default_rng(0)
        a = rng.uniform(1.0, 5.0, self.K).astype(np.float16)
        b = rng.uniform(1.0, 5.0, self.K).astype(np.float16)
        return {"a": a, "b": b}

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a = input_data["a"].astype(np.float16)
        b = input_data["b"].astype(np.float16)
        f32 = np.float32
        # FP16×FP16→FP32 精确（= numpy float32 乘，单次舍入），逐 lane
        p = (a.astype(np.float32) * b.astype(np.float32))     # 8 个 FP32 乘积
        # 镜像 fp32addtree8to1_unit._reduce8 的平衡树求和顺序（D0=p[0..3], D1=p[4..7]）
        l0 = f32(p[0] + p[1]); l1 = f32(p[2] + p[3])
        l2 = f32(p[4] + p[5]); l3 = f32(p[6] + p[7])
        m0 = f32(l0 + l1); m1 = f32(l2 + l3)
        s = f32(m0 + m1)
        golden = np.zeros(4, dtype=np.float32)
        golden[0] = s
        return {"y": golden, "intermediates": []}

    def get_dfg(self, spec: Any = None) -> List[Any]:
        b = MIROpBuilder()
        # 激活行（广播源）与权重列（本地源）各一个 128-bit 词（8×FP16）。
        # precision="fp16" 驱动 element_bits=16 → MockProgram/emitter 按 fp16 8-lane 打包输入。
        ar = b.make_op("UR_READ", "a_read", RT.UR_READ, [],
                       meta_name="a", logical_addr=0 * 256 + 0, stride=1,
                       role="input", iq_id=30, precision="fp16")
        br = b.make_op("UR_READ", "b_read", RT.UR_READ, [],
                       meta_name="b", logical_addr=1 * 256 + 0, stride=1,
                       role="input", iq_id=31, precision="fp16")
        # 激活 → FP16MUL_S0（广播源槽 0）；权重 → FP16MUL_S4567（本地共享总线，low_id 选 S4）
        rt_a = b.make_op("RTOVR", "rt_a_FP16MUL_S0", PS.FP16MUL_S0, [ar])
        rt_b = b.make_op("RTOVR", "rt_b_FP16MUL_S4567", PS.FP16MUL_S4567, [br])
        mul = b.make_op("FP16MUL", "inl_fp16mul", RT.FP16MUL, [rt_a, rt_b], iq_id=18)
        # FP16MUL 双输出 D0/D1 → addtree8to1 的 S0/S1（dep_port 选源 D0=18 / D1=19）
        rt_d0 = b.make_op("RTOVR", "rt_d0_FP32ADDTREE8TO1_S0", PS.FP32ADDTREE8TO1_S0,
                          [mul], dep_port="D0")
        rt_d1 = b.make_op("RTOVR", "rt_d1_FP32ADDTREE8TO1_S1", PS.FP32ADDTREE8TO1_S1,
                          [mul], dep_port="D1")
        # 8 个 FP32 求和 → 1 标量（dst_id=0 → 写 D lane0）。单 tile 不 cumulative。
        y_sum = b.make_op("FP32ADDTREE8TO1", "y_0", RT.FP32ADDTREE8TO1, [rt_d0, rt_d1],
                          cumulative=False, reduction_per_channel=8, iq_id=28)
        rt_out = b.make_op("RTOVR", "rt_y_0_UR_W0_S", PS.UR_W0_S, [y_sum], sel=28)
        b.make_op("UR_WRITE", "out_obj_0", RT.UR_WRITE, [rt_out],
                  logical_addr=5 * 256 + 0, stride=1, role="output", is_output=True, iq_id=35)

        ops = b.get_dfg()
        # RTOVR 增广边依赖（anti-overwrite / 时序对齐），与 ManualMatmulOperator 同款
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
        # 单 tile 单次发射：无 loop。
        for op in ops:
            op.metadata.update({"shape": [1], "inner_loops": 0, "outer_loops": 0,
                                "inner_inc": 0, "outer_inc": 0})
        return ops


class ManualFp16MatmulQOperator(BaseOperator):
    """ADR-032 S2：FP16 推理 matmul 满 32 乘（单 SMC，K=8，4 列稠密）。

    **角色互换**（ADR-032 S2 NEEDS-USER-FACT）：4 个不同权重列放 S0-S3（各独立 DVR 端口），
    广播激活放 S4-S7（共享 S4567 单端口，同值无需 low_id 区分；kernel 喂 s4=s5=s6=s7）。
      D0/D1=S0×S4=w0⊙act、D2/D3=S1×S5=w1⊙act、D4/D5=S2×S6=w2⊙act、D6/D7=S3×S7=w3⊙act
      → 1 个 FP16MUL 指令算全 32 乘 → 8 个 D → 4 条 FP32ADDTREE8TO1（dst_id 0/1/2/3，SMT
      串行化于单元 iq28）→ Q[c]=dot(act, w_c) → bank5 一词稠密 4 lane [Q0,Q1,Q2,Q3]。
    点积可交换 → 与硬件钦定布局（激活→S0-S3 广播 / 权重→S4-S7 本地）数学等价。
    fidelity 缺口（S0-S3 能否装 4 不同值 / faithful S4-S7 本地加载）见 ADR-032 S2 DEBT。
    单 tile 不 cumulative；双缓冲 defer（DEBT-FP16-DBLBUF）。
    """

    K = 8     # 收缩维（FP16MUL 一对源 8 lane）
    # COLS=4 满 32 乘：激活走 SHIFTUP（broadcast，resident）解耦其 read 时序 → 4 权重各占
    # 1 read 端口/1 bank 同拍读，激活在更早拍读 ur-r0（释放端口）。4 列 × 8 lane × (低/高半)
    # = 32 FP16 乘/指令。COLS=3 时退化为直读激活（不经 SHIFTUP，4 read 恰满 4 端口）。见 ADR-032 S2。
    COLS = 4

    def __init__(self, **kwargs):
        pass

    # 各权重列放**互异 bank**（b0→bank1 … b3→bank4）：FP16MUL strict latch 强制全输入同拍
    # read，同 bank 同拍 2 read → Bank 冲突 UNSAT（实测 unsat-core）。激活 bank0。
    WBANK = [1, 2, 3, 4]   # COLS 个权重列的 bank

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        cfg = {
            "a": {"bank": 0, "offset": 0, "role": "input", "precision": "fp16", "stride": 1},
            "y": {"bank": 5, "offset": 0, "role": "output", "precision": "fp32", "shape": [4]},
        }
        for c in range(self.COLS):
            cfg[f"b{c}"] = {"bank": self.WBANK[c], "offset": 0, "role": "input",
                            "precision": "fp16", "stride": 1}
        return cfg

    def prepare_input(self, n_rows: int) -> Dict[str, np.ndarray]:
        rng = np.random.default_rng(0)
        a = rng.uniform(1.0, 5.0, self.K).astype(np.float16)             # 激活 1 行 K=8
        out = {"a": a}
        for c in range(self.COLS):
            out[f"b{c}"] = rng.uniform(1.0, 5.0, self.K).astype(np.float16)  # 权重列 c，K=8
        return out

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a = input_data["a"].astype(np.float16)
        f32 = np.float32

        def dot8(act, w):
            p = (act.astype(np.float32) * w.astype(np.float32))   # 8 个 FP32 乘积（精确）
            l0 = f32(p[0] + p[1]); l1 = f32(p[2] + p[3])
            l2 = f32(p[4] + p[5]); l3 = f32(p[6] + p[7])
            return f32(f32(l0 + l1) + f32(l2 + l3))               # 镜像 _reduce8 平衡树

        golden = np.zeros(4, dtype=np.float32)
        for c in range(self.COLS):
            golden[c] = dot8(a, input_data[f"b{c}"].astype(np.float16))
        return {"y": golden, "intermediates": []}

    def get_dfg(self, spec: Any = None) -> List[Any]:
        b = MIROpBuilder()
        # 激活（广播）1 word + 4 权重列（各 1 word，bank1 word c）。precision=fp16 驱动 8-lane 打包。
        ar = b.make_op("UR_READ", "a_read", RT.UR_READ, [],
                       meta_name="a", logical_addr=0 * 256 + 0, stride=1,
                       role="input", iq_id=30, precision="fp16")
        # COLS 个权重列各 1 个 UR read：互异 read 端口（31..）+ 互异 bank（WBANK[c]）。
        # 激活端口 30 + COLS 权重 = COLS+1 个同拍 read，须 ≤4 端口且各占不同 bank（故 COLS≤3）。
        w_reads = []
        for c in range(self.COLS):
            wc = b.make_op("UR_READ", f"w{c}_read", RT.UR_READ, [],
                           meta_name=f"b{c}", logical_addr=self.WBANK[c] * 256 + 0, stride=1,
                           role="input", iq_id=30 + c, precision="fp16")  # ur-r0..3（激活走 SHIFTUP 错拍占 ur-r0）
            w_reads.append(wc)

        # RTOVR：权重列 c → FP16MUL_S{c}（S0-S2）；激活 → FP16MUL_S4567（广播 s4=s5=s6=s7）。
        rt_w = [b.make_op("RTOVR", f"rt_w{c}_FP16MUL_S{c}", getattr(PS, f"FP16MUL_S{c}"), [w_reads[c]])
                for c in range(self.COLS)]
        rt_act = b.make_op("RTOVR", "rt_act_FP16MUL_S4567", PS.FP16MUL_S4567, [ar])

        # FP16MUL：dep 顺序 [w0, act, w1, w2(...)] 对齐 modules.yaml inputs [S0,S4567,S1,S2,S3]。
        mul_deps = [rt_w[0], rt_act] + [rt_w[c] for c in range(1, self.COLS)]
        mul = b.make_op("FP16MUL", "inl_fp16mul", RT.FP16MUL, mul_deps, iq_id=18)

        # 8 个 D → COLS 条 ADDTREE8TO1（tree_c 读 D{2c}/D{2c+1} → dst_id=c）。
        # tree 间**顺序依赖链**（tree_c 依赖 prev_tree）：COLS 条 tree 分写同一输出寄存器的
        # COLS 个 lane（部分 lane 累积），rt_out 依赖末条即读全 lane 齐。
        # ⚠ II 优化尝试（去链化紧排 → II 4→6）已验证**会破**：tight schedule 下 SMT 的
        #   producer-resident 窗口柔性 latch 选的拍，sim（决定论 cycle 再生）无法复现 → addtree
        #   读到 s0=s1=0（D-pair 未配送，实测全 lane=0）。根因 = DEBT-032-RESIDENT-TIMING
        #   （resident 窗口 sim 忠实性未验）+ sim addtree8to1 back-to-back invocation 流水未建模。
        #   链化（每边 +addtree_lat=3，非 overlap）是当前 **sim-忠实**的保守解（II=10 但正确）。
        #   降 II 至硬下界 4 须先闭合上述 DEBT（VCS + sim 流水建模）。见 ADR-032 S2。
        trees = []
        prev_tree = None
        for c in range(self.COLS):
            rt_d_lo = b.make_op("RTOVR", f"rt_d{2*c}_FP32ADDTREE8TO1_S0", PS.FP32ADDTREE8TO1_S0,
                                [mul], dep_port=f"D{2*c}")
            rt_d_hi = b.make_op("RTOVR", f"rt_d{2*c+1}_FP32ADDTREE8TO1_S1", PS.FP32ADDTREE8TO1_S1,
                                [mul], dep_port=f"D{2*c+1}")
            tdeps = [rt_d_lo, rt_d_hi] + ([prev_tree] if prev_tree else [])
            tree = b.make_op("FP32ADDTREE8TO1", f"y_{c}", RT.FP32ADDTREE8TO1, tdeps,
                             cumulative=False, reduction_per_channel=8, dst_id=c, DST_REG=c, iq_id=28)
            trees.append(tree)
            prev_tree = tree

        # 单一输出 RTOVR 读末条 tree 的 D（链化保末条最后执行 → COLS lane 齐）→ bank5 稠密。
        rt_out = b.make_op("RTOVR", "rt_y_UR_W0_S", PS.UR_W0_S, [prev_tree], sel=28)
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
        for op in ops:
            op.metadata.update({"shape": [1], "inner_loops": 0, "outer_loops": 0,
                                "inner_inc": 0, "outer_inc": 0})
        # COLS=4 满 32 乘：激活走 SHIFTUP（broadcast/resident）—— 解耦激活 read 时序，使 4 个
        # 权重 read 在 compute-1 同拍各占 1 端口/1 bank，激活在更早拍读 ur-r0（释放端口）。
        # rt_act（→FP16MUL_S4567）经 helper 改消费 SHIFTUP_D（sel=29），producer-resident 放宽
        # （dependency_axioms SHIFTUP+broadcast）允许其在驻留窗口内采样。COLS≤3 不需 SHIFTUP。
        if self.COLS >= 4:
            ops = _inject_shiftup_broadcast(ops, {"a"}, spec)
        return ops
