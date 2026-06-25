import os
import numpy as np
from typing import List, Dict, Any
from .base import BaseOperator
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS


def dst_real_gather_enabled() -> bool:
    """ADR-036 S3：env flag 控制 SMI_DS_T gather 是否走真固件 UR 读端口（替 cluster storage backdoor）。
    ON → ds_src_read 进 emit/sim（真 bank{SRC_BANK} 读指令、solver 分配空闲端口）+ cluster 经读端口捕获
    寄存器 gather + scatter 经 SHIFTUP 广播缓冲直喂；OFF → structural_only + storage 直读 + bank0 staging
    （model-A backdoor 残余，仅留作 A/B 护栏对照）。
    **ADR-036 S3-c block-1（2026-06-25）：默认翻 ON**（真 datapath）——4 个受影响算子（matmul_q_dst /
    _k8 / fp16_matmul_q_dst / fp16_matmul_full_dst）ON 全 e2e exit=0（fp32 rigid auditor 已建模 ON：
    SMI_DS_T datapath 端点 value-exempt，auditor.py）。OFF 须显式 `AIACC_DST_REAL_GATHER=0`。
    护栏：ON vs OFF 逐 bit A/B diff 须一致（hco APPROVE-with-notes，保守模型 b，DEBT-036-DST-SRC-PORT）。
    flag + OFF backdoor 路径删除待 VCS（升 Accepted）。"""
    return os.environ.get("AIACC_DST_REAL_GATHER", "1").lower() in ("1", "true", "on", "yes")


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
                # DEBT-032（ADR-037）：标记广播源，供 pipeline_runner._broadcast_capture_config
                # 派生 sim 的 SHIFTUP 独立捕获寄存器 config（port/bank）。修法在 sim 层（hco H2 判定：
                # 同端口权重读 prologue 伪读 clobber port0 锁存器是 sim model-A artifact），**不加任何 SMT
                # 约束**（H1 端口独占方案已证伪 UNSAT 并否决，见 ADR-037 Rejected 段）。
                op.metadata["broadcast_source"] = True
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


def _inject_smi_ds_t_transpose(ops: List[Any], cfg: Dict[str, int], spec: Any) -> List[Any]:
    """ADR-034 D3/D4 Stage-I 2a：把跨-SMC 转置前缀作 DFG 真节点注入——
    `ds_gather(SHIFTDN) → ds_transpose(SMI_DS_T) → [现有 broadcast SHIFTUP 作 scatter] → a_read`。

    令 broadcast 激活读 `a_read` 依赖 `ds_transpose`，于是 SMT 看见 **scatter(广播 SHIFTUP)
    在 transpose 之后、transpose 在 gather 之后** 的依赖边（ADR-033 B-BLOCK-1 (a) 路径）。
    复用现有 `shiftup_a_read`（广播）作 scatter，**不另建第二个 SHIFTUP**（避开 sim 单 shiftup
    实例 + multi-broadcast 限制）。

    **2a 边界（hco D5 caveat）**：sim 数值由指令驱动的流式 SMI_DS_T 单元提供（ADR-034 2b
    起替 ADR-033 的 `cluster.apply_smi_ds_t` run-前 model-A backdoor，该 backdoor 已删 S3-c）——
    `ds_transpose`(iq40) 经 `get_iq_name`→"smi_ds_t-0"，sim 无同名 IQ → `load_program` 跳过（不扰动）；
    `ds_gather`(SHIFTDN) 单次 fire、smc_id_en 门控 gather_cnt，不写 compute 输入 bank0。本注入只验「结构」
    （DFG 拓扑 + SMT 依赖边 + emit 表598 微指令进流），step-driven 真数据流是 2b/Stage-II。"""
    from ir.mir.mir_op import MIROp

    smc_num = 32
    bank_depth = 256
    if spec is not None and hasattr(spec, "params"):
        smc_num = getattr(spec.params, "PARAM_SMC_NUM", 32)
        bank_depth = getattr(spec.params, "PARAM_UR_BANK_DEPTH", 256)
    gather_cnt = int(cfg.get("gather_smc_cnt", smc_num))
    src_bank = int(cfg.get("src_bank", 4))
    src_addr = int(cfg.get("src_addr", 0))
    shiftdn_iq, smi_ds_t_iq = 34, 40
    ur_read_iq = 30
    if spec is not None and hasattr(spec, "config"):
        reg = spec.config.get("iq_registry", {})
        shiftdn_iq = reg.get("SHIFTDN", 34)
        smi_ds_t_iq = reg.get("SMI_DS_T", 40)
        ur_read_iq = reg.get("UR_READ", 30)

    a_read = next((o for o in ops
                   if o.op_type.upper() == "UR_READ"
                   and o.metadata.get("meta_name") == "a"
                   and o.metadata.get("role") == "input"), None)
    if a_read is None:
        return ops

    # 单次 prologue 元数据（known-risk ①：转置一次产 M 行，非随 a_read 的 M-loop 复发）
    prologue = {"shape": [1], "inner_loops": 0, "outer_loops": 0,
                "inner_inc": 0, "outer_inc": 0}
    # ADR-036 S1：5 段 DFG 首段 = UR_read 列对齐（每 SMC_s 读本地 bank{src_bank} 的列 s）。
    # 令 ds_gather 依赖它 → 完整链 ds_src_read(UR_READ)→ds_gather(SHIFTDN)→ds_transpose(SMI_DS_T)
    # →shiftup_a_read(SHIFTUP broadcast)→rt_a(RTOVR)→…→out(UR_WRITE)。数值由指令驱动的流式
    # SMI_DS_T 单元提供（ds_transpose 节点 emit 成 smi_ds_t hex → physical_runner.setup_smi_ds_t_trigger
    # → _tick_smi_ds_t_trigger：OFF 从各 SMC storage[src_bank] 直读 gather + transpose_tile + scatter
    # bank0；ADR-034 2b 起替 ADR-033 的 apply_smi_ds_t run-前原子 backdoor，该 backdoor 已删 S3-c）。
    # 本节点对 SMT 提供「列对齐读」依赖锚点，使排程把整条转置 datapath 串在 compute 之前。
    # structural_only：S1/S2 期间本节点仅作 SMT 排程锚点、**不进 emit/sim**（pipeline_runner emit 前按此
    # flag 过滤），避免被 solver 重指派到真 ur-r 执行而与流式单元的 gather 读冲突。S3-a real-gather ON
    # 后转为真 bank 读指令（下方）。
    # ADR-036 S3-a：real-gather ON → ds_src_read 进 emit/sim（真 bank{SRC_BANK} 读指令），且**不硬 pin
    # iq_id**——4 个 UR 读端口已被 fp16 compute（4 权重 + 广播激活）占满（ADR-037 实测全 UNSAT），无空闲
    # ur-rN 可静态 pin（hco BLOCK 提案 A）。ds_src_read 是单次 prologue（shape=[1]），让 solver 在转置
    # prologue 窗口分配一个空闲端口；其实际落的端口由 pipeline_runner post-solve 回读 → patch 进
    # smi_ds_t cfg(src_read_port) + dst_src_capture（镜像 ADR-037 _broadcast_capture_config）。
    # OFF（显式 =0；S3-c block-1 起默认 ON）→ structural_only=True（仅 SMT 锚点、不进 emit）+ iq_id=ur_read_iq，维持 model-A backdoor。
    real_gather = dst_real_gather_enabled()
    ds_src_meta = {
        "depends_on": [], "iq_type": RT.UR_READ,
        "meta_name": "ds_src", "role": "input", "stride": 0,
        "logical_addr": src_bank * bank_depth + src_addr,
        # 契约修复（user 2026-06-25「文档设计为准」）：gather 读只发在持列的 gather_cnt 个 SMC
        #（SMC_ID<=gather_cnt-1），非全 8 SMC。旧版无显式 smc_id_en → payload_factory 默认 PARAM_SMC_NUM-1=7
        # → K=4 时 SMC4-7 读空 bank 出零（user log 见）。显式 smc_id_en=gather_cnt-1 杜绝伪读零。
        "smc_id_en": max(gather_cnt - 1, 0),
        "math": "smi_ds_t_src_read(col-aligned X[:,s] on SMC_s)", **prologue,
    }
    if real_gather:
        ds_src_meta["ds_src_read"] = True  # post-solve 回读端口的标记（mirror broadcast_source）
    else:
        ds_src_meta["iq_id"] = ur_read_iq
        ds_src_meta["structural_only"] = True
    ds_src_read = MIROp(
        op_type="UR_READ", logic_cycle=0, trace_id="ds_src_read",
        metadata=ds_src_meta,
    )
    # 硬件契约（user 2026-06-25 + shiftdn.yaml + hco APPROVE-with-notes）：SHIFTDN 源 = DVR_SHIFTDN_S
    # （producers_dvr slot 34，经 RtoVR 路由），语义 `CRD_SHIFTDN_O <- DVR_SHIFTDN_S`。故 UR_read → SHIFTDN
    # 之间必有 RTOVR 把 ur 读出路由到 SHIFTDN_S。AIACC 里 RTOVR 不是被调度的节点（ucode_solver 排除出
    # compute_ops、无 t_var），而是消费者上的 rtovr_deps 边——dependency_axioms 的 rtovr_deps 分支建 t_latch
    # 串进调度。故 depends_on 保持指真生产者 ds_src_read（**勿改成指 RTOVR 节点**，否则 t_c=-1 悬空），加
    # rtovr_deps：dvr=SHIFTDN_S(34) 目的端口；sel=源=UR_READ_{port}_D（=30+port）。ON 端口动态 → sel
    # post-solve patch（pipeline_runner._dst_src_capture_config，mirror src_read_port）；先填 -1 占位（同
    # b_read 惯例，payload_factory 对 UR 读源兜底解析）。SHIFTUP 不对称：其源经 data 字段直读、前无此 RTOVR。
    # RTOVR 节点（emit 真路由 IQ：UR_read → SHIFTDN_S，mirror matmul rt_b 经 slot_id emit rtovr-N）。
    # sel=-1 占位：payload_factory 对 UR 读源兜底解析为 UR_READ_{port}_D（同 b_read）。
    rt_ds_src_meta = {
        "depends_on": ["ds_src_read"], "slot_id": PS.SHIFTDN_S, "sel": -1, "dep_port": "D",
        "smi_ds_t_route": True,   # SMI_DS_T gather 路由端点：rigid compute 审计值豁免（block-1，ON）
        "math": "rtovr_route(UR_read → DVR_SHIFTDN_S)", **prologue,
    }
    if not real_gather:
        rt_ds_src_meta["structural_only"] = True   # OFF：mirror ds_src_read，仅 SMT 锚点不进 emit
    rt_ds_src = MIROp(
        op_type="RTOVR", logic_cycle=0, trace_id="rt_ds_src_SHIFTDN_S",
        metadata=rt_ds_src_meta,
    )
    ds_gather = MIROp(
        op_type="SHIFTDN", logic_cycle=0, trace_id="ds_gather",
        metadata={
            # depends_on 指 RTOVR 节点（mirror matmul mul.depends_on=[rt_a]）；rtovr_deps 让
            # dependency_axioms 重定向到真生产者 ds_src_read + 建 t_latch（否则 RTOVR 无 t_var → t_c=-1）。
            "depends_on": ["rt_ds_src_SHIFTDN_S"], "iq_type": RT.SHIFTDN, "iq_id": shiftdn_iq,
            # 契约修复（user 2026-06-25「文档设计为准」+ docx para 2208-2213）：SHIFTDN 功能
            #   broadcast==0 → 只 SMC_ID==smc_id_en 注入 DVR_SHIFTDN_S（单 SMC）；
            #   broadcast==1 → SMC_ID<=smc_id_en 全注入。gather 须收集全 gather_cnt 个 SMC 的列
            #   （doc para 4664「共 PARAM_SMC_NUM 行」）→ **broadcast=1**（旧版 =0 只 gather 1 个 SMC=错；
            #   sim 抓不到——shift_dn_unit 忽略 broadcast/smc_id_en 走 fallback chain + gather 走 model-A
            #   _tick_smi_ds_t_trigger 直读各 SMC，故 sim bit-perfect 但固件错位，仅 VCS 暴露）。
            "smc_id_en": max(gather_cnt - 1, 0), "smc_id": 0, "broadcast": 1,
            "rtovr_deps": {"ds_src_read": {"dvr": int(PS.SHIFTDN_S.value), "sel": -1}},
            "math": "smi_ds_t_gather(cross-smc columns via DVR_SHIFTDN_S)", **prologue,
        },
    )
    ds_transpose = MIROp(
        op_type="SMI_DS_T", logic_cycle=0, trace_id="ds_transpose",
        metadata={
            "depends_on": ["ds_gather"], "iq_type": RT.SMI_DS_T, "iq_id": smi_ds_t_iq,
            "VALID": 1, "intlv_en": 0, "intlv_grp": 0, "intlv_in": 0, "intlv_out": 0,
            "math": "smi_ds_t_transpose(gather_cnt x lanes)", **prologue,
        },
    )
    # scatter-after-transpose：现有 broadcast shiftup 消费 a_read → 令 a_read 依赖转置
    deps = list(a_read.metadata.get("depends_on", []))
    if "ds_transpose" not in deps:
        deps.append("ds_transpose")
    a_read.metadata["depends_on"] = deps

    return list(ops) + [ds_src_read, rt_ds_src, ds_gather, ds_transpose]


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


class ManualMatmulQDsTOperator(ManualMatmulQOperator):
    """ADR-033 Stage-A：Matmul_Q 经 SMI_DS_T 跨-SMC 转置 datapath（替掉 matmul_q 的 data-prep 假转置）。

    激活 X[M,K] **列对齐存**（非转置间插）：SMC_s（s<K）bank{SRC_BANK} 持列 s = [X[0,s]..X[M-1,s]]。
    run 中指令驱动流式 SMI_DS_T 单元（ds_transpose hex → setup/_tick_smi_ds_t_trigger）：gather K 个
    SMC 的列 → transpose_tile 转置 → 行对齐 X[m,:] 经 scatter（OFF bank0 / ON SHIFTUP 广播缓冲）→
    后续 SHIFTUP 广播 + 每 SMC dot(X[m,:],W[:,s])。**compute 与 matmul_q 完全一致**
    （继承 get_dfg/get_golden/prepare_input，K=4 单 addtree 词、无 cumulative），唯一新增 = 跨-SMC 转置前端。

    缩放选择：gather 跨 K 个 SMC（=4），广播到全 8 SMC；更忠实的 K=SMC_NUM=8 实例需 cumulative
    addtree 2 词，留 Stage-A'。忠实度边界（model-A，hco caveat）：不验跨-SMC gather→transpose→scatter
    时序（DEBT-033-DST-CTRL / NEEDS-USER-FACT-033-DSTDONE）。详见 ADR-033 D5 / 分歧台账 D-01。

    ⚠ ADR-036 S1 scenario-A（K=8/M=4）的 bit-perfect compute 经实测撞死结构墙（见会话报告 +
    DEBT-036-K8COMPUTE）：① 2 词广播撞单 SHIFTUP 单源(ur-r0 硬接)→SMT UNSAT；② 单广播+cumulative
    需逐行 reset = DEBT-026 Stage-2。K=8 bit-perfect 留 S3（需 S2 流式单元 + K>4/M>1 compute 方案）。
    本类暂留 K=4 Stage-A（working 8/8）；5 段 DFG + SMI_DS_T latency 已在共享注入器就位。"""

    SRC_BANK = 4   # 列对齐激活存放 bank（非转置间插）；bank0/1/5 已被 a/b/y 占

    def smi_ds_t_config(self, spec: Any = None) -> Dict[str, int]:
        # gather K 个 SMC（每 SMC 持一列、lanes=M 个元素）→ 转置 M×K → 行对齐写回 SMC_0 bank0
        return {"src_bank": self.SRC_BANK, "src_addr": 0,
                "dst_bank": 0, "dst_addr": 0,
                "gather_smc_cnt": self.K, "lanes": self.M}

    def get_dfg(self, spec: Any = None) -> List[Any]:
        # ADR-034 Stage-I 2a：在 matmul_q 计算 DFG 前注入 SMI_DS_T 转置指令节点
        # （ds_gather→ds_transpose→现有 broadcast SHIFTUP broadcast）。数值走指令驱动流式单元（2b）。
        ops = super().get_dfg(spec)
        return _inject_smi_ds_t_transpose(ops, self.smi_ds_t_config(spec), spec)

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]
        b = input_data["b"]
        X = a.reshape(self.M, self.K).astype(np.float32)        # [M, K]
        b_addr = self.get_bank_addr("b", spec)
        bank_depth = self._get_hw_param(spec, "PARAM_UR_BANK_DEPTH", 256)
        src_logical = self.SRC_BANK * bank_depth
        for k, prog in enumerate(programs):
            prog.inject_vector(b_addr, b[k], stride=1)           # 权重列（同 matmul_q）
            if k < self.K:
                col = X[:, k].copy()                              # 列 k = [X[0,k]..X[M-1,k]]
                prog.inject_vector(src_logical, col, stride=1)    # SMC_k bank{SRC_BANK} 持列 k（非转置间插）
            # 不注入 bank0：run 中流式 SMI_DS_T 单元（_tick_smi_ds_t_trigger）把行对齐激活 scatter 到 SMC_0 bank0


class ManualMatmulQDsTK8Operator(ManualMatmulParallelNOperator):
    """ADR-033 Stage-A'：SMI_DS_T **K=SMC_NUM=8 全 gather + cumulative addtree**（完全对齐文档 tile 维度）。

    M=1、K=8：激活向量 8 元素 **列对齐存全 8 个 SMC**（每 SMC 持 1 元素 a[s]）→ 指令驱动流式 SMI_DS_T
    单元 gather 8×1 → transpose_tile 转置 1×8 → 恢复 8 元素行对齐向量 scatter 写 SMC_0 bank0（2 词）→ 复用 ParallelN
    cumulative compute（K=8 = 2 词，FP32ADDTREE5TO1 跨词 accumulate 累加，loop_carried II≥4）→
    每 SMC 输出 dot(a, W[:,s])。

    比 Stage-A（matmul_q_dst，K=4 gather 4 SMC、单 addtree 词无 cumulative）更忠实：**gather 全 8 SMC +
    cumulative reduce**，对齐文档 [seq1037-1039] 输入 SMC_NUM×UR_BYTE → 输出 UR_BYTE×SMC_NUM（此处用 1 lane）。
    忠实度边界同 Stage-A（model-A 不验时序，升 Accepted 待 VCS）。详见 ADR-033 Stage-A'。"""

    K = 8
    SRC_BANK = 4

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        # 固定 K=8（忽略 harness 传入 n_rows），与 get_dfg 的 inner_loops 一致
        rng = np.random.default_rng(0)
        a = rng.uniform(1.0, 5.0, self.K).astype(np.float32)               # 8 元素激活向量
        b = rng.uniform(1.0, 5.0, (smc_num, self.K)).astype(np.float32)    # 每 SMC 权重列 8 元素
        return {"a": a, "b": b}

    def smi_ds_t_config(self, spec: Any = None) -> Dict[str, int]:
        # gather 全 K=8 个 SMC（每 SMC lanes=1 元素）→ 转置 1×8 → 写回 SMC_0 bank0（2 词）
        return {"src_bank": self.SRC_BANK, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
                "gather_smc_cnt": self.K, "lanes": 1, "word_lanes": 4}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]
        b = input_data["b"]
        b_addr = self.get_bank_addr("b", spec)
        bank_depth = self._get_hw_param(spec, "PARAM_UR_BANK_DEPTH", 256)
        src_logical = self.SRC_BANK * bank_depth
        for k, prog in enumerate(programs):
            prog.inject_vector(b_addr, b[k], stride=1)                      # 权重列（8 元素 = 2 词）
            if k < self.K:
                prog.inject_vector(src_logical, np.array([a[k]], dtype=np.float32), stride=1)  # SMC_k 持 a[k]
            # 不注入 bank0：run 中流式 SMI_DS_T 单元 scatter 写恢复的 8 元素向量（2 词）

    def get_dfg(self, spec: Any = None) -> List[Any]:
        # 复用 ParallelN cumulative 点积链，但 K=8（inner_loops = K//4-1 = 1，2 词跨词累加）
        b = MIROpBuilder()
        ar = b.make_op("UR_READ", "a_read", RT.UR_READ, [],
                       meta_name="a", logical_addr=0 * 256 + 0, stride=1, role="input", iq_id=30)
        br = b.make_op("UR_READ", "b_read", RT.UR_READ, [],
                       meta_name="b", logical_addr=1 * 256 + 0, stride=1, role="input", iq_id=31)
        rt_a = b.make_op("RTOVR", "rt_a_FPMUL_S0", PS.FPMUL_S0, [ar])
        rt_b = b.make_op("RTOVR", "rt_b_FPMUL_S1", PS.FPMUL_S1, [br])
        mul = b.make_op("FPMUL", "inl_mul", RT.FPMUL, [rt_a, rt_b], iq_id=3)
        rt_sum = b.make_op("RTOVR", "rt_mul_FP32ADDTREE5TO1_S0", PS.FP32ADDTREE5TO1_S0, [mul])
        y_sum = b.make_op("FP32ADDTREE5TO1", "y_0", RT.FP32ADDTREE5TO1, [rt_sum],
                          cumulative=True, reduction_per_channel=4, iq_id=26,
                          loop_carried_depends_on={"y_0": 4})
        rt_out = b.make_op("RTOVR", "rt_y_0_UR_W0_S", PS.UR_W0_S, [y_sum], sel=26)
        b.make_op("UR_WRITE", "out_obj_0", RT.UR_WRITE, [rt_out],
                  logical_addr=5 * 256 + 0, stride=0, role="output", is_output=True, iq_id=35)

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
        inner_loops = self.K // 4 - 1   # K=8 → 1（2 词累加）
        for op in ops:
            op.metadata.update({"shape": [self.K], "inner_loops": inner_loops, "outer_loops": 0,
                                "inner_inc": 1, "outer_inc": 0})
        ops = _inject_shiftup_broadcast(ops, {"a"}, spec)
        # ADR-034 Stage-I 2a：注入 SMI_DS_T 转置指令节点（K8 全 gather 8 SMC）
        ops = _inject_smi_ds_t_transpose(ops, self.smi_ds_t_config(spec), spec)
        return ops


class ManualMatmulQDsTFullOperator(ManualMatmulQDsTK8Operator):
    """ADR-036 S3：scenario-A K=8、M>4（破 matmul_q 的 pipeline-depth M≤4 hack）。

    **多通道结构**（照 ManualMatmulOperator，user 2026-06-11 裁定）：M 行 → addtree 的 **M 个独立
    lane（S0..S{M-1}）**，每 lane 连续 cumulative 累加该行的 K-dot（acc=1），**acc=0 只是初始 dr_d=0**
    （addtree 8 lane 各自 old_i，互不串扰——无「逐行 reset」那回事）。K=8=2 tile = 内循环 cumulative。

    本 increment 先**隔离验 compute**：激活 X[m,:8] 直注各 SMC bank0（绕转置/广播）、每 SMC 与本地
    权重列 W[:,s] 点积 → Q[m,s]。SMI_DS_T 转置前端 + 广播-to-all 下一步接。M≤8（addtree lane 数）。"""

    M = 4          # 4 行 = 4 通道（addtree S0..S3，破 M≤4 的真正含义：通道而非 pipeline 巧合）

    def smi_ds_t_config(self, spec: Any = None):
        # ADR-036 S3-c triage：本算子是 compute 隔离 increment，get_dfg 直注 bank0（绕转置/广播前端），
        # **不产 smi-ds-t 转置指令**。若沿用 K8 父类的 cfg → pipeline_runner patch smi_ds_t 进 sim_config
        # → physical_runner 见 cfg 但无 smi-ds-t hex → 落 apply_smi_ds_t fallback，gather src_bank=4 写回
        # SMC_0 bank0 words[0,1]，clobber 直注的 X[0]（m=0 通道）→ SMC_0 y[0]=0（7/8 既存 bug）。
        # 返 None 杜绝 fallback：cfg 为假即跳过整段 smi_ds_t patch（pipeline_runner:125 `if cfg`）。
        return None

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        rng = np.random.default_rng(0)
        a = rng.uniform(1.0, 5.0, self.M * self.K).astype(np.float32)        # X[M,K] flat
        b = rng.uniform(1.0, 5.0, (smc_num, self.K)).astype(np.float32)      # W[:,s] 每 SMC 8 元素
        return {"a": a, "b": b}

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a = input_data["a"]; b = input_data["b"]
        smc_num = b.shape[0]
        X = a.reshape(self.M, self.K)
        W = b[:, :self.K]
        # Q[s, m] = dot(X[m,:], W[s,:])；M 行落 addtree 4 lane（D0 dense [Q0..Q3]）
        golden = np.zeros((smc_num, self.M), dtype=np.float32)
        for s in range(smc_num):
            for m in range(self.M):
                golden[s, m] = np.dot(X[m], W[s]).astype(np.float32)
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]; b = input_data["b"]
        X = a.reshape(self.M, self.K).astype(np.float32)        # [M, K=8]
        bank_depth = self._get_hw_param(spec, "PARAM_UR_BANK_DEPTH", 256)
        for s, prog in enumerate(programs):
            # 激活 X[m,:8] 直注各 SMC bank0 words[2m,2m+1]（隔离验 compute；转置/广播下一步）
            for m in range(self.M):
                prog.inject_vector(0 * bank_depth + 2 * m, X[m].copy(), stride=1)
            # 权重 W[:,s] = 8 元素 = 2 词 → bank1 words[0,1]
            prog.inject_vector(1 * bank_depth + 0, b[s].copy(), stride=1)

    def get_dfg(self, spec: Any = None) -> List[Any]:
        b = MIROpBuilder()
        temp_banks = [2, 3, 4, 6]   # 4 通道暂存（避 FPMUL/RTOVR 对齐冲突，同 ManualMatmulOperator）
        temp_reads = []
        # M 通道：channel m = 行 m。a_m 读 X[m,tile]（bank0 base=2m, 内循环 2 tile）、
        # b_m 读 W[tile]（bank1 base=0, 同列共享）→ FPMUL → temp → addtree S{m} lane m。
        for m in range(self.M):
            a_m = b.make_op("UR_READ", f"a{m}_read", RT.UR_READ, [],
                            meta_name=f"a{m}", logical_addr=0 * 256 + 2 * m, stride=1,
                            role="input", iq_id=30 + (m % 2) * 2)
            b_m = b.make_op("UR_READ", f"b{m}_read", RT.UR_READ, [],
                            meta_name=f"b{m}", logical_addr=1 * 256 + 0, stride=1,
                            role="input", iq_id=31 + (m % 2) * 2)
            rt_a = b.make_op("RTOVR", f"rt_a{m}_FPMUL_S0", PS.FPMUL_S0, [a_m])
            rt_b = b.make_op("RTOVR", f"rt_b{m}_FPMUL_S1", PS.FPMUL_S1, [b_m])
            mul = b.make_op("FPMUL", f"inl_mul{m}", RT.FPMUL, [rt_a, rt_b], iq_id=3)
            write_ps = getattr(PS, f"UR_W{m}_S")
            rt_write = b.make_op("RTOVR", f"rt_mul{m}_{write_ps.name}", write_ps, [mul], sel=3)
            twrite_addr = temp_banks[m] * 256 + 0
            b.make_op("UR_WRITE", f"temp_write{m}", RT.UR_WRITE, [rt_write],
                      logical_addr=twrite_addr, stride=1, role="intermediate", iq_id=36 + (m % 3))
            tr = b.make_op("UR_READ", f"temp_read{m}", RT.UR_READ, [f"temp_write{m}"],
                           logical_addr=twrite_addr, stride=1, role="intermediate", iq_id=30 + m)
            temp_reads.append(tr)
        # addtree M 通道：lane m = Σ_tile reduce(X[m,tile]·W[tile])（cumulative 跨 2 tile）
        rt_sums = []
        for m in range(self.M):
            rt_sums.append(b.make_op("RTOVR", f"rt_temp_read{m}_FP32ADDTREE5TO1_S{m}",
                                     getattr(PS, f"FP32ADDTREE5TO1_S{m}"), [temp_reads[m]]))
        y_sum = b.make_op("FP32ADDTREE5TO1", "y_0", RT.FP32ADDTREE5TO1, rt_sums,
                          cumulative=True, reduction_per_channel=4, iq_id=26,
                          loop_carried_depends_on={"y_0": 4})
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
        # 单层内循环 = K/4=2 tile（cumulative 跨 tile 累加）。M 行 = addtree 4 lane（非迭代维），
        # 故无外循环。输出 = 1 个 4-lane 词 [Q0,Q1,Q2,Q3]（M 行 dense 落 D0 lane）。
        inner = self.K // 4 - 1   # =1（2 tile）
        for op in ops:
            op.metadata.update({"shape": [self.K // 4], "inner_loops": inner, "outer_loops": 0,
                                "inner_inc": 1, "outer_inc": 0})
        # 输出 stride=0：2 tile 内原地，cumulative 终值 = Q（addtree 4 lane）
        out = next(o for o in ops if o.trace_id == "out_obj_0")
        out.metadata.update({"inner_inc": 0, "outer_inc": 0, "stride": 0})
        # 本 increment 隔离验 compute：激活直注各 SMC bank0（无广播/转置前端）
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


class ManualFp16MatmulParallelNOperator(ManualFp16MatmulQOperator):
    """ADR-032 S3：FP16 matmul 8-SMC N 维广播（output-stationary，满 32 乘/SMC）。

    广播激活（SHIFTUP 单源 → fan-out 到 8 SMC）× 每 SMC **本地** COLS 权重列（partitioned）
    → 每 SMC 算自己的 COLS 个 K=8 点积 → 各 SMC bank5 稠密 [Q0..Q{COLS-1}]（partitioned）。
    **复用 S2（ManualFp16MatmulQOperator）的 SMC 内 DFG**（满 32 乘 1 FP16MUL → 8 D →
    COLS 条 ADDTREE8TO1 链化）—— 单 SMI 全局指令流 lockstep 复制 + SHIFTUP 跨-SMC 广播
    （mirror ManualMatmulParallelNOperator）。golden (smc_num, COLS)，per-SMC 8/8 bit-perfect。
    """

    parallel_dim = "N"

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        cfg = {
            "a": {"bank": 0, "offset": 0, "role": "input", "precision": "fp16",
                  "stride": 1, "smc_layout": "broadcast"},
            "y": {"bank": 5, "offset": 0, "role": "output", "precision": "fp32",
                  "shape": [4], "smc_layout": "partitioned"},
        }
        for c in range(self.COLS):
            cfg[f"b{c}"] = {"bank": self.WBANK[c], "offset": 0, "role": "input",
                            "precision": "fp16", "stride": 1, "smc_layout": "partitioned"}
        return cfg

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        rng = np.random.default_rng(0)
        a = rng.uniform(1.0, 5.0, self.K).astype(np.float16)              # broadcast 1D（K=8）
        out = {"a": a}
        for c in range(self.COLS):
            out[f"b{c}"] = rng.uniform(1.0, 5.0, (smc_num, self.K)).astype(np.float16)  # partitioned 2D
        return out

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        a = input_data["a"].astype(np.float16)
        f32 = np.float32

        def dot8(act, w):
            p = (act.astype(np.float32) * w.astype(np.float32))
            l0 = f32(p[0] + p[1]); l1 = f32(p[2] + p[3])
            l2 = f32(p[4] + p[5]); l3 = f32(p[6] + p[7])
            return f32(f32(l0 + l1) + f32(l2 + l3))

        smc_num = input_data["b0"].shape[0]
        golden = np.zeros((smc_num, 4), dtype=np.float32)   # (smc_num, COLS) per-SMC 4 点积
        for s in range(smc_num):
            for c in range(self.COLS):
                golden[s, c] = dot8(a, input_data[f"b{c}"][s].astype(np.float16))
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]
        a_addr = self.get_bank_addr("a", spec)
        programs[0].inject_vector(a_addr, a, stride=1)        # 广播 a 仅注入 SMC_0（SHIFTUP fan-out）
        for c in range(self.COLS):
            b_addr = self.get_bank_addr(f"b{c}", spec)
            for k, prog in enumerate(programs):
                prog.inject_vector(b_addr, input_data[f"b{c}"][k], stride=1)  # 权重列 c per-SMC

    # get_dfg 继承 ManualFp16MatmulQOperator（单 SMC 内 DFG + SHIFTUP 广播）；多 SMC emit 复制。


class ManualFp16MatmulQDsTOperator(ManualFp16MatmulParallelNOperator):
    """ADR-035：SMI_DS_T 跨-SMC 转置落到**文档钦定的 FP16 matmul 路径**（fp16_matmul_n + 转置前端）。

    镜像 `ManualMatmulQDsTK8Operator`（FP32 K=8），但 compute 走 **FP16MUL（32 乘/指令）+ FP32ADDTREE8TO1**
    （表33：FP16 矩阵乘法单元 1024，无 FP32 matmul 单元）。激活 K=8 **列对齐存全 8 SMC**（每 SMC 持 1 个
    **FP16** 元素 a[s]）→ `SMI_DS_T` gather 8×1 → 转置 1×8 → 恢复 8 元素行对齐写回 SMC_0 bank0
    （**FP16 8 元素 = 1 个 128b 词**，vs FP32 = 2 词）→ SHIFTUP 广播 → 每 SMC dot8(a, W[:,c]) 4 列。

    转置精度泛化（ADR-035 D2）：`smi_ds_t_config` 携 `element_bits=16` → cluster 选 fp16 codec
    （8 lane × 16-bit）。byte 转置 ≡ element 转置（lanes=1 每元素不跨词边界，BLOCK-1 不变式）。
    转置只搬不算 → golden 与 fp16_matmul_n 同（继承 get_golden / prepare_input）。"""

    SRC_BANK = 6   # 列对齐 FP16 激活存放 bank（避让 a=0 / b0-3=1-4 / y=5；hco NOTE-3）

    def smi_ds_t_config(self, spec: Any = None) -> Dict[str, int]:
        # gather 全 K=8 个 SMC（每 SMC lanes=1 个 FP16 元素）→ 转置 1×8 → 写回 SMC_0 bank0（FP16 1 词）
        return {"src_bank": self.SRC_BANK, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
                "gather_smc_cnt": self.K, "lanes": 1, "element_bits": 16, "word_lanes": 8}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]                                      # K=8 FP16 激活向量
        bank_depth = self._get_hw_param(spec, "PARAM_UR_BANK_DEPTH", 256)
        src_logical = self.SRC_BANK * bank_depth
        for c in range(self.COLS):
            b_addr = self.get_bank_addr(f"b{c}", spec)
            for k, prog in enumerate(programs):
                prog.inject_vector(b_addr, input_data[f"b{c}"][k], stride=1)   # 权重列 c per-SMC
        for k, prog in enumerate(programs):
            if k < self.K:
                prog.inject_vector(src_logical, np.array([a[k]], dtype=np.float16), stride=1)  # SMC_k 持 a[k]
            # 不注入 bank0：SMI_DS_T 转置后写回行对齐激活（供 SHIFTUP 广播读）

    def get_dfg(self, spec: Any = None) -> List[Any]:
        # fp16_matmul_n 的 SMC 内 DFG（FP16MUL 满 32 乘 + SHIFTUP 广播）+ SMI_DS_T 转置前端注入
        ops = super().get_dfg(spec)
        return _inject_smi_ds_t_transpose(ops, self.smi_ds_t_config(spec), spec)


class ManualFp16MatmulFullOperator(ManualFp16MatmulParallelNOperator):
    """ADR-036 scenario-A 的 **FP16 流式实现**：M=H>1 行矩阵激活 × K=8，8-SMC N 维广播。

    **走文档钦定的 matmul 路径 + 文档钦定的「逐行广播」流式形态**（ADR-035：无 FP32 matmul 单元；
    ADR-036 D4 / para1239：矩阵激活转成行对齐后**逐行广播**，M 行走流水时间维而非端口空间维）。

    复用 `fp16_matmul_n` 的 SMC 内 DFG（1 FP16MUL → 8 D → COLS=4 条 ADDTREE8TO1 链 → 4 列点积），
    外加 **M 行外循环**：广播激活 `a_read`（ur-r0，SHIFTUP 单源）逐迭代 +1 词读 X[m,:]、每拍
    `_broadcast_shiftup_fanout` 把第 m 行同拍 fan-out 全 SMC、compute 复发产 Q[m,0..3]、输出 +1 词；
    本地权重列 inner_inc=0 复读不变。K=8 一拍 8→1 reduce，**无 cumulative**。

    **与空间堆叠（4 行塞 S0-S3）的本质区别**：那是端口空间维（撞 IQ_UR_READ count=4 → M≤3 假限）；
    本算子 M 走时间维（流水复发），**M 任意 → 真破 FP32 matmul_q 的 M≤4 pipeline-depth hack**。

    本 increment（A）**隔离验流式 compute**：M 激活行注 SMC_0 bank0 的 M 个词（SHIFTUP 广播源），
    绕 SMI_DS_T 转置前端（Increment B 接，mirror fp16_matmul_q_dst）。输出 per-SMC M 词、word m =
    第 m 行的 [Q0..Q3]（COLS=4 lane）。golden (smc_num, M*4) 对齐 readback word 序。

    ✅ **DEBT-032-RESIDENT-TIMING 已修（ADR-037，2026-06-11，hco H2 判定）**：根因=广播激活 pin 到 ur-r0，
    但同端口权重读的 II prologue 伪读在 SHIFTUP 捕获前覆盖 port0 锁存器 → 广播 0。hco 读 spec 表542-544 判定
    这是 sim model-A artifact（SHIFTUP 物理读指令字 `data` 字段、非 live UR 端口）。修法=给 SHIFTUP 广播源独立
    捕获寄存器（a_read 拍快照、hold 到广播），消 flaky：fp16_matmul_full / fp16_matmul_n 各 4/4 cold-build 确定性
    8/8 bit-perfect。绝对拍忠实度待 user RTL 流式时序表 + VCS（ADR-037 升 Accepted 门）。
    """

    M = 8   # >4：真破 M≤4（M 行流式广播，无端口/pipeline-depth 硬限）

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        cfg = super().tensor_configs
        cfg["a"]["shape"] = [self.M, self.K]     # M 广播激活行
        cfg["y"]["shape"] = [self.M * 4]         # M 词 × COLS=4 lane（readback 连续 fp32）
        return cfg

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        rng = np.random.default_rng(0)
        # 激活 **flat [M*K]**：框架 num_loop_iters = len(第一个input) // vector_lanes(fp16=8) = M*K//8 = M。
        # 每迭代 1 词（K=8 fp16）= 第 m 行 → SHIFTUP 逐行广播。
        a = rng.uniform(1.0, 5.0, self.M * self.K).astype(np.float16)
        out = {"a": a}
        for c in range(self.COLS):
            out[f"b{c}"] = rng.uniform(1.0, 5.0, (smc_num, self.K)).astype(np.float16)  # partitioned 列
        return out

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        X = input_data["a"].astype(np.float16).reshape(self.M, self.K)   # flat → [M, K]
        f32 = np.float32

        def dot8(act, w):
            p = (act.astype(np.float32) * w.astype(np.float32))
            l0 = f32(p[0] + p[1]); l1 = f32(p[2] + p[3])
            l2 = f32(p[4] + p[5]); l3 = f32(p[6] + p[7])
            return f32(f32(l0 + l1) + f32(l2 + l3))

        smc_num = input_data["b0"].shape[0]
        # readback word 序：word m = 第 m 行 COLS 列 → flat 索引 m*4+c = Q[m,c]
        golden = np.zeros((smc_num, self.M * 4), dtype=np.float32)
        for s in range(smc_num):
            for m in range(self.M):
                for c in range(self.COLS):
                    golden[s, m * 4 + c] = dot8(X[m], input_data[f"b{c}"][s].astype(np.float16))
        return {"y": golden, "intermediates": []}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        a = input_data["a"]                       # flat [M*K]
        a_addr = self.get_bank_addr("a", spec)
        # 广播 M 行只注入 SMC_0（SHIFTUP fan-out）：flat M*K = M 个连续词（word m = 第 m 行）
        programs[0].inject_vector(a_addr, a, stride=1)
        for c in range(self.COLS):
            b_addr = self.get_bank_addr(f"b{c}", spec)
            for k, prog in enumerate(programs):
                prog.inject_vector(b_addr, input_data[f"b{c}"][k], stride=1)  # 本地权重列 c（不随迭代变）

    def get_dfg(self, spec: Any = None) -> List[Any]:
        # fp16_matmul_n 的 SMC 内 DFG（1 FP16MUL + 4 ADDTREE8TO1 链 + SHIFTUP 广播）。
        # M 行循环由框架 num_loop_iters（=len(a)//vector_lanes=M）驱动；地址递增靠 stride：
        #   a_read stride=1（广播源 ur-r0 逐迭代 +1 词读第 m 行）、权重 stride=0（复读不变）、
        #   输出 stride=1（逐迭代 +1 词写第 m 行 [Q0..Q3]）。cumulative=False → 每行独立终值。
        ops = super().get_dfg(spec)
        for op in ops:
            ot = op.op_type.upper()
            mn = op.metadata.get("meta_name")
            if ot == "UR_READ" and mn is not None and mn.startswith("b"):
                op.metadata["stride"] = 0             # 权重列每迭代复读同一词
            # a_read（mn=="a"）+ out_obj_0 保持 stride=1（super 已设）→ 逐迭代前进
        return ops


class ManualFp16MatmulFullDsTOperator(ManualFp16MatmulFullOperator):
    """ADR-035/036 Increment B：fp16_matmul_full 的流式 compute + SMI_DS_T 转置前端
    （mirror `ManualFp16MatmulQDsTOperator`，但 lanes=M>1 多行而非单 token）。

    与 `fp16_matmul_full`（Increment A，M 激活行直注 SMC_0 bank0）的区别：M×K 激活**列对齐存全 8 SMC**
    （SMC_s bank{SRC_BANK} 持 1 词 = 列 s 的 M 个 fp16 `[X[0,s]..X[M-1,s]]`）→ `SMI_DS_T` gather K×M Tile
    → 转置 M×K → M 行各 1 词写回 SMC_0 bank0 addr 0..M-1（word m = 行 m）→ full 的逐行 SHIFTUP 广播流式 compute。

    转置是**单次** prologue（`_inject_smi_ds_t_transpose` 的 shape=[1]：一次产全 M 行，非随 a_read 的 M-loop 复发）。
    数值核走 **ADR-036 S2 流式 stepped 单元**（`SmiDsTUnit.evaluate/tick`，由 `setup_smi_ds_t_trigger`
    指令 VLD 驱动、固定 latency=16 pipeline、scatter 逐行 DATA_OUT；**非** `apply_smi_ds_t` gather-gate backdoor，
    后者仅无指令流时 fallback）。element_bits=16 fp16 codec。**首次行使 lanes=M>1 多行转置**
    （q_dst 是 lanes=1 单 token）；`transpose_tile` out[m]=[X[m,0..K-1]]，**lanes=8 已验**（lanes∈(1,8) 未单独验）。

    ⚠ **边界（硬依赖 M=K=8，未支持即静默失效，扩展前须先补）**：fp16 codec 一个 128b 词最多 8 个 fp16，
    `smi_ds_t_config` 传 lanes=M、gather 只读单词。故 **M>8 时 lane≥8 越 128b 词边界静默丢弃**、**K>8（列跨词）
    未覆盖**、**M<8 codec 支持但未验**。当前 M=K=word_lanes=8「1 列=1 词、1 行=1 词」整除恰好成立。
    ⚠ **时序忠实度缺口（ADR-036 still proposed）**：数值核虽已是流式 stepped 单元，但 **latency=16 是占位拍数
    （未经 VCS 校准）**，且 **SHIFTDN 逐拍 drain / interleave-stagger / INST_DONE 多并发握手 / COND 数据就绪同步
    仍未建模**（DEBT-033-DST-CTRL / DEBT-S4 / 文档 D-07 seq990）。8/8 bit-perfect 仅**终值 parity**，对跨-SMC
    逐拍时序忠实度增信近零，升 Accepted 需 VCS。DEBT-032 已修（ADR-037）→ 流式 compute 稳定，转置前端方可叠加。"""

    SRC_BANK = 6   # 列对齐 FP16 激活存放 bank（避让 a=0 / b0-3=1-4 / y=5；mirror q_dst NOTE-3）

    def smi_ds_t_config(self, spec: Any = None) -> Dict[str, int]:
        # gather K=8 个 SMC（各 lanes=M 个 fp16 = 1 词）→ 转置 M×K → 写回 SMC_0 bank0 的 M 个词（每行 1 词 fp16）
        return {"src_bank": self.SRC_BANK, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
                "gather_smc_cnt": self.K, "lanes": self.M, "element_bits": 16, "word_lanes": 8}

    def inject_data_per_smc(self, programs: List[Any], input_data: Dict[str, np.ndarray],
                            vector_lanes: int, spec: Any = None):
        X = input_data["a"].astype(np.float16).reshape(self.M, self.K)   # flat M*K → [M, K]
        bank_depth = self._get_hw_param(spec, "PARAM_UR_BANK_DEPTH", 256)
        src_logical = self.SRC_BANK * bank_depth
        for c in range(self.COLS):
            b_addr = self.get_bank_addr(f"b{c}", spec)
            for k, prog in enumerate(programs):
                prog.inject_vector(b_addr, input_data[f"b{c}"][k], stride=1)   # 本地权重列 c per-SMC
        for s, prog in enumerate(programs):
            if s < self.K:
                # SMC_s 持列 s = [X[0,s]..X[M-1,s]]（M 个 fp16 = 1 词）；不注入 bank0（转置写回）
                prog.inject_vector(src_logical, X[:, s].copy(), stride=1)

    def get_dfg(self, spec: Any = None) -> List[Any]:
        # full 的 M 行流式 DFG（FP16MUL + 4 ADDTREE8TO1 + SHIFTUP 广播）+ SMI_DS_T 转置前端注入
        ops = super().get_dfg(spec)
        return _inject_smi_ds_t_transpose(ops, self.smi_ds_t_config(spec), spec)
