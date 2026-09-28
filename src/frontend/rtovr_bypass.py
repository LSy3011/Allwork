"""
ADR-053 D4：RtoVR 稀疏连通性确定性绕行 lowering pass（MIR→MIR，非 SMT 搜索）。

物理事实（RtoVR.v USE_RTOVR_SPARSE_MASK，hco 逐行取证 + VCS 实证 51/51 算子 472/680 条被屏蔽）：
- UR 数据进计算单元**只能**经 VIN0/VIN1（out14/15 掩码 = {30,31,32,33} 即 4 个 UR 读口）；
- 计算结果回 UR **只能**经 VOUT0（out35-38 掩码 = {13,28,29}，通用计算源仅 13=VOUT0_D；
  28=FP32ADDTREE8TO1_D / 29=SHIFTUP_D 是各自的合法直连，不需绕行）；
- 计算→计算直连掩码（out0-12 = 0x1FDB）**含** {11,12}=VIN0_D/VIN1_D，不含 {30..33}=UR。

确定性改写（无搜索空间，ADR-053 D4「不做成 SMT 连通性搜索」）：
- Class A：`UR_READ →(rt)→ compute 第 i 个操作数`
        ⇒ `UR_READ →(rt1, slot=VIN{i}_S=14/15)→ VIN{i} →(rt2, slot=原 slot)→ compute`
  VIN 按操作数序号 i 派发（i=0→VIN0、i=1→VIN1），与 golden 24 条 select 的
  S0←VIN0_D(11) / S1←VIN1_D(12) 形态一致。i≥2 → 硬错误（硬件只有 2 个 VIN 口）。
- Class B：`compute →(rt)→ UR_WRITE`
        ⇒ `compute →(rt1, slot=VOUT0_S=16)→ VOUT0 →(rt2, slot=原 35-38)→ UR_WRITE`

延迟（hco Q1：禁止硬编码 +2/+4）：本 pass 只改拓扑，延迟由改写后 DFG 结构性推出——
每条新 RTOVR 边吃 dependency_axioms 的 latch+1（:163-167），VIN/VOUT 节点各吃
pipeline_delays 的 1 拍（IPR.sv:87/OPR.sv:87）。净效果单向 2→4 拍，无需任何常量。

容量（hco Q2）：VIN0/VIN1/VOUT0 各 1 实例，resource_axioms.add_modulo_constraint
按 (u_type, smc_id) 分组对单实例单元自动施加 ≤1/拍 PbLe —— pass 不需额外编码，
II 由求解器自动抬升（golden 本身就是错拍复用）。

smc_id_en（hco B3，关键）：payload_factory.py:95 显式 metadata 先于 :110-121 缺省分支
（缺省发 PARAM_SMC_NUM-1=7）。原裁决发 31（据信 golden=0x3f）；**2026-09-11 errata**：
全机 20 份 golden vin hex 普查显示 19/20 发 7（body 低 6 位 0x0f，含全部 mul_scalar
上板 PASS 用例），仅 sub_8smc_v3_fpga_verify 一份发 31（0x3f）——D7 验收目标
mul_scalar 的 golden 一律为 7。故改为显式发 `PARAM_SMC_NUM-1`（与 ADR-031 全单元
约定同源同值，功能上与 31 等价：阈值语义 en>=smc_id 全覆盖 8 SMC）。
仍**必须**显式写 metadata（B3 机制不变），绝不可改缺省分支。

阶段 1 处置策略（ADR-053 增量分期：出口判据 = mul_scalar 逐位对 golden + 全部 select 合法）：
- 本 pass 拥有的两类边（UR↔compute 全路径）：非法且不可绕行 → RtoVRBypassError 硬错误
  （把「472 条被屏蔽」变编译期硬错误，不再假绿）。
- 非本 pass 拥有的非法边（SHIFTUP→compute 广播、SHIFTDN 链等）：**警告并保留**，
  记 DEBT-053-BCAST-ROUTE（广播需经 UR 中转重新 lowering，属阶段 2+；保留是为不破坏
  fma_parallel_n/matmul_parallel_n 现有 dense-sim 回归——它们的 8/8 对这些边本就是假绿，
  D6 模拟器掩码上线后会如实变红，届时以 DEBT 跟踪，不在本 pass 里静默掩盖）。
- **INT8 MAC 串链 deferred 区（2026-09-11 全量回归后补入）**：consumer 槽 ∈ out17-33
  （INT8TOFP16_S/FP16MUL_S/ADD5TO1_S/ADD8TO1_S）或生产者 sel ∈ 14-27（MAC 链产物）的
  非法边同样**警告并保留**而非硬错误——ADR 明示「只建模掩码、不调度该管」（阶段 3），
  硬错误会把阶段 3 对象（matmul_q_dst/fp16_matmul 全家）堵死在编译期；保留后经
  D6 掩码在 sim 端诚实置 0（真合法路径：UR→VIN→INTADD→INT8TOFP16→FP16MUL→ADD5→ADD8→UR）。

幂等：合法性检查先于改写，pass 生成的 rt1/rt2 天然合法 → 二次运行为 no-op。
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from ir.mir.mir_op import MIROp
from driver.hw_resource import ResourceType as RT, PortSlot as PS


class RtoVRBypassError(RuntimeError):
    """ADR-053 D4 阶段 1 硬错误：UR↔compute 路径在稀疏掩码下非法且不可绕行。"""


# 阶段 3 deferred 区（ADR-053「不建模 INT8 MAC 串链的调度」）：
# - consumer 槽 17-33：INT8TOFP16_S0/S1=17,18；FP16MUL_S0-S3=19-22、S4567=23；
#   FP32ADDTREE5TO1_S0-S7=24-31；FP32ADDTREE8TO1_S0/S1=32,33。
# - 生产者 sel 14-27：INT8TOFP16_D0-3=14-17；FP16MUL_D0-7=18-25；ADD5TO1_D0/D1=26,27。
# 编号以 RtoVR.v OUT_/IN_IDX 声明为权威（D1 邻接表同源），禁手改语义。
DEFERRED_CONSUMER_SLOTS = frozenset(range(17, 34))
DEFERRED_PRODUCER_SELS = frozenset(range(14, 28))


def _slot_int(slot_val: Any) -> Optional[int]:
    """slot_id 可能是 IntEnum(PS.xxx) 或 int（post-SMT 重推导后）。统一转 int。"""
    if slot_val is None:
        return None
    if isinstance(slot_val, int):
        return int(slot_val)
    v = getattr(slot_val, "value", None)
    return int(v) if isinstance(v, int) else None


def apply_rtovr_sparse_bypass(ops: List[MIROp], spec: Any) -> List[MIROp]:
    """
    对 DFG 执行确定性绕行改写，返回改写后的新 ops 列表（原 list 对象不被增删，
    但 consumer 的 metadata["depends_on"]/["rtovr_deps"] 会被就地更新——与
    configurable._inject_shiftup_broadcast 同一约定）。

    Args:
        ops:  operator.get_dfg() 的输出（RTOVR 边已以 RTOVR 节点形式存在）
        spec: HardwareSpec（读 get_rtovr_connectivity / get_dr_map / get_dvr_map）

    Returns:
        改写后的 ops（含新增 VIN/VOUT/rt1/rt2 节点，已删除被替换的旧 rt）

    Raises:
        RtoVRBypassError: 邻接表缺失（fail-closed），或 UR↔compute 边非法且不可绕行。
    """
    allowed = spec.get_rtovr_connectivity()
    # VIN/VOUT 指令 smc_id_en：显式发 PARAM_SMC_NUM-1（禁手抄常量，一律读源；
    # errata 见文件头注释——golden 普查 19/20=7，含全部 mul_scalar 上板用例）。
    _params = getattr(spec, "params", None)
    smc_id_en = int(getattr(_params, "PARAM_SMC_NUM", 1)) - 1
    if not allowed:
        # fail-closed（hw_spec.is_rtovr_select_legal 同款哲学）：邻接表缺失 = 配置事故，
        # 宁可全部硬错误也不退回 dense 假设假绿。
        raise RtoVRBypassError(
            "ADR-053 D4: rtovr allowed 邻接表缺失（rtovr_config.yaml 无 allowed 段？）——"
            "拒绝在连通性未知下 lowering。请运行 verification_v2/tools/gen_rtovr_allowed.py")

    dr_map = spec.get_dr_map()    # unit -> port -> sel（生产者侧，consumers_dr）
    dvr_map = spec.get_dvr_map()  # unit -> port -> out_slot（消费者侧，producers_dvr）

    # UR_READ 生产者的候选 sel 集合（solver 事后才分配具体读口 30-33 → 阶段 1 用集合判定；
    # 现行掩码中所有面向 UR 的槽（14/15/34）都接纳全部 4 个口，∃ 判定无风险）。
    ur_sels: Set[int] = {
        int(sel) for unit, ports in dr_map.items() if unit.upper().startswith("UR_READ")
        for sel in ports.values()
    }
    vin_sel = {0: int(dr_map["VIN0"]["D"]), 1: int(dr_map["VIN1"]["D"])}  # 11 / 12
    vout_sel = int(dr_map["VOUT0"]["D"])                                  # 13
    vin_slot = {0: int(PS.VIN0_S), 1: int(PS.VIN1_S)}                     # 14 / 15
    vout_slot = int(PS.VOUT0_S)                                           # 16

    ops = list(ops)
    by_id: Dict[str, MIROp] = {op.trace_id: op for op in ops}

    def producer_sels(pid: str, dep_port: str) -> Set[int]:
        """生产者在这类边上可能取的 sel 集合（UR=读口集合；单实例单元=类型级单值）。"""
        return _producer_sels(pid, dep_port, by_id, dr_map, ur_sels)

    def is_legal(slot: int, sels: Set[int]) -> bool:
        return bool(allowed.get(slot, frozenset()) & sels)

    # ---- 1. 收集全部 RTOVR 边 (rt, producer_id, consumer, dep_index) ----
    edges: List[Tuple[MIROp, str, MIROp, int]] = []
    for op in ops:
        if op.op_type.upper() != "RTOVR":
            continue
        prods = op.metadata.get("depends_on", [])
        if not prods:
            continue
        for cons in ops:
            if cons.op_type.upper() == "RTOVR":
                continue
            for i, d in enumerate(cons.metadata.get("depends_on", [])):
                if d == op.trace_id:
                    edges.append((op, prods[0], cons, i))

    # ---- 2. 逐边分类与改写 ----
    new_nodes: List[MIROp] = []
    rewired_rts: Set[str] = set()   # 被改写过的旧 rt（事后按「无引用」删除）
    illegal_kept: Set[Tuple[str, str, int]] = set()   # 非拥有类非法边（警告去重）
    n_rewritten = 0

    for rt, pid, cons, i in edges:
        p = by_id.get(pid)
        ptype = p.op_type.upper() if p else "?"
        ctype = cons.op_type.upper()
        dep_port = rt.metadata.get("dep_port", "D")
        slot = _slot_int(rt.metadata.get("slot_id"))
        if slot is None:
            # 缺显式 slot（手搭 DFG）：用索引法从 consumer 端口推导（同 ucode_solver:528-530）
            unit_info = spec.modules.get(ctype, None)
            inputs = getattr(unit_info, "inputs", []) if unit_info is not None else []
            if i < len(inputs):
                p_name = str(inputs[i]).split("-")[-1]
                slot = dvr_map.get(ctype, {}).get(p_name)
            if slot is None:
                raise RtoVRBypassError(
                    f"ADR-053 D4: RTOVR {rt.trace_id} 无 slot_id 且索引法推导失败 "
                    f"(consumer={cons.trace_id} dep#{i})——无法做连通性判定，拒绝放行")

        sels = producer_sels(pid, dep_port)
        # 显式 sel 并入判定（手搭 DFG 的 RTOVR 常带显式 sel，如 matmul 的 addtree=26/27；
        # 与事后断言同款规则——否则端口名不匹配（D vs D0/D1）时 sels 推导为空，
        # 会把 deferred 区的边误判为「非 deferred 非法」而硬错误）。
        # 例外（2026-09-11 hardsigmoid 回归取证）：UR_READ 生产者的显式 sel 若指向
        # 计算单元输出（如 4=FPADD_D/10），是常量注入沿用原边 select 的陈旧 metadata
        #（configurable 的 cinl_*/inl_* 节点）——UR 读口物理上只能发 30-33，陈旧 sel
        # 会造成「∃ 合法」假阳、跳过绕行，发射期 solver 改派真实读口后即被掩码置 0
        #（实测 (6,31)/(6,33)/(12,30)/(12,33) 四条）。故 UR_READ 一律只用 ur_sels。
        _explicit = rt.metadata.get("sel", -1)
        if isinstance(_explicit, int) and _explicit >= 0 and "UR_READ" not in ptype:
            sels = set(sels) | {int(_explicit)}
        if is_legal(slot, sels):
            continue  # 合法直连（compute→compute、UR→SHIFTDN、ADD8/SHIFTUP→UR_WRITE…）原样保留

        is_ur_producer = "UR_READ" in ptype
        is_uw_consumer = "UR_WRITE" in ctype

        if is_ur_producer and not is_uw_consumer:
            # ---- Class A：UR_READ → compute[dep_i]，经 VIN{dep_i} 绕行 ----
            if slot in DEFERRED_CONSUMER_SLOTS:
                # INT8 MAC 串链（out17-33）是阶段 3 deferred 区（ADR-053「不调度该管」）——
                # UR 直达这些槽物理非法但 VIN 也到不了，不属本 pass 拥有类：
                # warn+keep（同 else 分支），由 D6 掩码过滤在 sim 端诚实置 0。
                key = (ptype, ctype, slot)
                if key not in illegal_kept:
                    illegal_kept.add(key)
                    print(f"[ADR-053-WARN] UR_READ→{ctype}(slot={slot}) 属 INT8 MAC 串链 "
                          f"deferred 区（out17-33，阶段 3 调度）——非法边保留，"
                          f"sim 将按掩码置 0（DEBT-053-BCAST-ROUTE）")
                continue
            if i >= 2:
                raise RtoVRBypassError(
                    f"ADR-053 D4: {cons.trace_id} 第 {i} 个操作数源自 UR_READ，但硬件只有 "
                    f"VIN0/VIN1 两个入口（out14/15）——>2 个 UR 操作数的算子阶段 1 不支持")
            vs, vsel = vin_slot[i], vin_sel[i]
            if not is_legal(vs, ur_sels) or vsel not in allowed.get(slot, frozenset()):
                raise RtoVRBypassError(
                    f"ADR-053 D4: UR_READ→{cons.trace_id}(slot={slot}) 非法且 VIN{i} 不可达 "
                    f"(VIN{i}_S 掩码∩UR={sorted(allowed.get(vs, ()))}, "
                    f"目标槽掩码={sorted(allowed.get(slot, ()))} 需含 VIN{i}_D={vsel})")
            relay_type = "VIN0" if i == 0 else "VIN1"
            rt1 = MIROp(op_type="RTOVR", logic_cycle=0,
                        trace_id=f"{rt.trace_id}__vinin"[:64],
                        metadata={"depends_on": [pid],
                                  "slot_id": PS.VIN0_S if i == 0 else PS.VIN1_S,
                                  "dep_port": dep_port,
                                  "sel": rt.metadata.get("sel", -1),
                                  "math": f"bypass_in({rt.trace_id})"})
            relay = MIROp(op_type=relay_type, logic_cycle=0,
                          trace_id=f"{rt.trace_id}__{relay_type.lower()}"[:64],
                          metadata={"depends_on": [rt1.trace_id],
                                    "iq_type": RT.VIN0 if i == 0 else RT.VIN1,
                                    "smc_id_en": smc_id_en,  # hco B3 显式路径；errata 后 =N-1
                                    "math": f"vin_relay({pid}→{cons.trace_id})"})
            rt2 = MIROp(op_type="RTOVR", logic_cycle=0,
                        trace_id=f"{rt.trace_id}__vinout"[:64],
                        metadata={"depends_on": [relay.trace_id], "slot_id": slot,
                                  "dep_port": "D", "sel": vsel,
                                  "math": f"bypass_out({rt.trace_id})"})
            cons.metadata["depends_on"][i] = rt2.trace_id
            new_nodes += [rt1, relay, rt2]
            rewired_rts.add(rt.trace_id)
            n_rewritten += 1

        elif is_uw_consumer and not is_ur_producer:
            # ---- Class B：compute → UR_WRITE，经 VOUT0 绕行 ----
            if not is_legal(vout_slot, sels):
                if sels & DEFERRED_PRODUCER_SELS:
                    # MAC 链产物（sel 14-27：INT8TOFP16_D/FP16MUL_D/ADD5TO1_D）回写
                    # 属阶段 3（须走 ADD8 链或 INTADD 桥）——warn+keep，同 deferred 区处理。
                    key = (ptype, ctype, slot)
                    if key not in illegal_kept:
                        illegal_kept.add(key)
                        print(f"[ADR-053-WARN] {ptype}(sel={sorted(sels)})→UR_WRITE(slot={slot}) "
                              f"的源属 MAC 链 deferred 区（阶段 3）——非法边保留，"
                              f"sim 将按掩码置 0（DEBT-053-BCAST-ROUTE）")
                    continue
                raise RtoVRBypassError(
                    f"ADR-053 D4: {ptype}→{cons.trace_id}(slot={slot}) 非法且 VOUT0_S 不接纳 "
                    f"该源（sel={sorted(sels)}，VOUT0_S 掩码={sorted(allowed.get(vout_slot, ()))}）——"
                    f"该结果物理上无法回写 UR（如 FP16MUL_D* 必须走加法树链）")
            if vout_sel not in allowed.get(slot, frozenset()):
                raise RtoVRBypassError(
                    f"ADR-053 D4: 回写槽 {slot} 掩码 {sorted(allowed.get(slot, ()))} 不含 "
                    f"VOUT0_D={vout_sel}——非 UR_WRITE 标准回写槽？")
            rt1 = MIROp(op_type="RTOVR", logic_cycle=0,
                        trace_id=f"{rt.trace_id}__voutin"[:64],
                        metadata={"depends_on": [pid], "slot_id": PS.VOUT0_S,
                                  "dep_port": dep_port,
                                  "sel": rt.metadata.get("sel", -1),
                                  "math": f"bypass_in({rt.trace_id})"})
            relay = MIROp(op_type="VOUT0", logic_cycle=0,
                          trace_id=f"{rt.trace_id}__vout0"[:64],
                          metadata={"depends_on": [rt1.trace_id],
                                    "iq_type": RT.VOUT0,
                                    "smc_id_en": smc_id_en,  # hco B3 显式路径；errata 后 =N-1
                                    "math": f"vout_relay({pid}→{cons.trace_id})"})
            rt2 = MIROp(op_type="RTOVR", logic_cycle=0,
                        trace_id=f"{rt.trace_id}__voutout"[:64],
                        metadata={"depends_on": [relay.trace_id], "slot_id": slot,
                                  "dep_port": "D", "sel": vout_sel,
                                  "math": f"bypass_out({rt.trace_id})"})
            cons.metadata["depends_on"][i] = rt2.trace_id
            new_nodes += [rt1, relay, rt2]
            rewired_rts.add(rt.trace_id)
            n_rewritten += 1

        else:
            # 非本 pass 拥有的非法边（SHIFTUP→compute 广播、UR_READ→UR_WRITE spill 直搬、
            # SHIFTDN/INT8 链等）：阶段 1 警告保留，DEBT-053-BCAST-ROUTE 跟踪。
            key = (ptype, ctype, slot)
            if key not in illegal_kept:
                illegal_kept.add(key)
                print(f"[ADR-053-WARN] 稀疏掩码非法边保留（非绕行 pass 拥有类，DEBT-053-BCAST-ROUTE）："
                      f"{ptype} --sel={sorted(sels)}--> slot {slot} ({ctype})，"
                      f"掩码={sorted(allowed.get(slot, ()))}")

    # ---- 3. 组装新 ops：删除「被改写且不再被任何 op 引用」的旧 rt，追加新节点 ----
    referenced: Set[str] = set()
    for o in ops:
        for d in o.metadata.get("depends_on", []):
            referenced.add(d)
    delete_rt = {rid for rid in rewired_rts if rid not in referenced}
    out_ops = [op for op in ops if op.trace_id not in delete_rt] + new_nodes

    # ---- 4. 全量重建 rtovr_deps（canonical：base.py:170-179 / configurable.py:185-202 同算法）----
    for op in out_ops:
        if "rtovr_deps" in op.metadata:
            op.metadata["rtovr_deps"] = {}
    by_id = {op.trace_id: op for op in out_ops}
    for op in out_ops:
        if op.op_type.upper() != "RTOVR":
            continue
        prods = op.metadata.get("depends_on", [])
        if not prods:
            continue
        pid = prods[0]
        for cons in out_ops:
            if op.trace_id in cons.metadata.get("depends_on", []):
                cons.metadata.setdefault("rtovr_deps", {})[pid] = {
                    "dvr": op.metadata.get("dvr", -1),
                    "sel": op.metadata.get("sel", -1),
                }

    # ---- 5. 事后断言（ADR-053 D4）：pass 生成的每条 (slot, sel) 必须置位于邻接表 ----
    for op in new_nodes:
        if op.op_type.upper() != "RTOVR":
            continue
        slot = _slot_int(op.metadata.get("slot_id"))
        sels = _producer_sels(op.metadata["depends_on"][0],
                              op.metadata.get("dep_port", "D"), by_id, dr_map, ur_sels)
        # rt 的显式 sel（pass 自己写的）优先于类型级推导参与断言
        explicit = op.metadata.get("sel", -1)
        if isinstance(explicit, int) and explicit >= 0:
            sels = {int(explicit)} | sels
        if not is_legal(slot, sels):
            raise RtoVRBypassError(
                f"ADR-053 D4 事后断言失败：pass 生成的 {op.trace_id} (slot={slot}, "
                f"sel={sorted(sels)}) 不在邻接表 {sorted(allowed.get(slot, ()))} ——"
                f"pass 自身逻辑 bug，拒绝放行")

    print(f"[ADR-053] rtovr 稀疏绕行：改写 {n_rewritten} 条边，"
          f"新增 {len(new_nodes)} 节点，删除 {len(delete_rt)} 旧 RTOVR；"
          f"保留非法边（非拥有类）{len(illegal_kept)} 类")
    return out_ops


def _producer_sels(pid: str, dep_port: str, by_id: Dict[str, MIROp],
                   dr_map: Dict[str, Any], ur_sels: Set[int]) -> Set[int]:
    """生产者在这类边上可能取的 sel 集合（UR=读口集合；单实例单元=类型级单值）。"""
    p = by_id.get(pid)
    ptype = p.op_type.upper() if p else ""
    if "UR_READ" in ptype:
        return set(ur_sels)
    entry = dr_map.get(ptype, {})
    if dep_port in entry:
        return {int(entry[dep_port])}
    if "D" in entry:
        return {int(entry["D"])}
    return set()  # 未知生产者 → 空集，后续按非法处理（fail-closed）
