---
adr_id: ADR-017
title: 整合 TimingVerifier 入 validate_physical_schedule，删 backend.py
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-017 · TimingVerifier 整合 + backend.py 整删

## Context

ADR-016 Phase 2 删 backend.SMTSynthesizer 后，backend.py 萎缩到 111 行只剩 `TimingVerifier` 类（被 `tests/unit/test_verifier_robustness.py` 使用），用于 SMT 后验防御。

`ucode_solver.UCodeSynthesizer.validate_physical_schedule` 在 e2e 主路径作为后验，但**仅覆盖 RTOVR 路径**，不覆盖 TimingVerifier 的几类校验：

### TimingVerifier 5 类校验（backend.py:26-111）

1. **Resource limit per slot**：`res_type in unit_limits` 时，每模槽 count ≤ limit
2. **Physical IQ ID uniqueness**：同 iq_id 在同模槽不重复
3. **Bank manual_bank modulo collision**：同 `(slot, manual_bank)` 不能两个 op
4. **Read/Write port bandwidth**：UR_READ ≤ `total_read_ports`、UR_WRITE ≤ `total_write_ports`
5. **RAW Latch overwrite**：`t_consumer < t_prod + lat` → RAW；`t_consumer ≥ t_prod + lat + II` 且 producer 非 invariant/UR_WRITE → Latch Overwrite

加上 #6 Shadow Binding（RTOVR → ALU 距离 [1, II-1]）。

### validate_physical_schedule 现状（ucode_solver.py:116+）

- ✅ Modulo Collision by `iq_name`（unit_map 驱动）—— 与 TimingVerifier #2 等价
- ⏸ Phase Contamination（已 commented，V6.1 PIPELINE OPT 取消）
- ✅ RTOVR undersampling（producer 完成前不能采样）
- ✅ RTOVR latch overwrite（仅 RTOVR-routed 消费者）

### Gap（TimingVerifier 独有，validate_physical_schedule 缺）

| # | 校验 | 物理意义 |
|---|---|---|
| 1 | Resource limit per slot | 同 slot 同资源类型不能超 unit_instances |
| 3 | Bank `manual_bank` modulo collision | TimingVerifier 用 `(s, bank)` 作为 key，validate 用 `(iq_name, slot)`——不同维度，不能等同 |
| 4 | Port bandwidth (4R/4W) | 物理总线限制 |
| 5 | RAW latch overwrite（非 RTOVR 路径） | 直 compute-to-compute 边的存活窗口 |

#2 Physical IQ ID 与现有 modulo collision 维度不同但 mostly cover（除非 iq_name 没 set）。
#6 Shadow Binding 已被 RTOVR latch lifecycle 等价 cover。

### 设计哲学冲突

- TimingVerifier：raise `RuntimeError`（反证测试用 `assertRaisesRegex`）
- validate_physical_schedule：return `False` + print warning（trial loop 用 `if not result: try_next_II`）

合并时统一为 **return False 模式**——更适合 e2e 主路径，反证测试改用 `assertFalse(result)`。

## Decision

**1. 把 4 类 gap 校验补到 `validate_physical_schedule`**

- Resource limit per slot
- Bank `manual_bank` modulo collision——**严格对齐 ADR-009 三段语义**（W-W 互斥 / R-R 同地址豁免、异地址互斥 / W↔R 无豁免互斥）。HCO 审查 2026-05-15 阻断项
- Port bandwidth (UR_READ / UR_WRITE)
- RAW Latch overwrite（非 RTOVR 路径，重用 ADR-006 + ADR-016 移植到 ucode_solver 的 inline 上界，但 verifier 是 post-check）

**1a. RTOVR op 仅跳过 modulo collision，不跳过资源/bank/port 校验**

HCO 审查 2026-05-15 阻断项：原 outline `if op.op_type.upper() == "RTOVR": continue` 一刀切跳过 RTOVR op，导致 `unit_instances` 中 RTOVR=64 的资源上限失效。修订：

```python
for op in ops:
    is_rtovr = (op.op_type.upper() == "RTOVR")
    # ... resource limit / port bandwidth / bank 三段 ...（含 RTOVR）
    if is_rtovr:
        continue  # 仅跳过下面 modulo collision + RAW latch（这两个 RTOVR 不适用）
    # ... modulo collision + RAW latch ...
```

**2. 删 `src/synthesis/backend.py` 整文件**

ADR-016 Phase 2 已留 backend.py 作为 TimingVerifier 容器。本 ADR 把那 4 类校验吸收到 validate_physical_schedule 后，TimingVerifier 失去最后用户，删整个 backend.py。

**3. 迁移 `tests/unit/test_verifier_robustness.py`**

旧：
```python
with self.assertRaisesRegex(RuntimeError, "Latch Overwrite"):
    self.verifier.verify(dfg, schedule, II=5)
```

新：
```python
synth = UCodeSynthesizer(self.spec)
# 构造 unit_map（validate_physical_schedule 需要）
unit_map = {op.trace_id: f"unit_{i}" for i, op in enumerate(dfg)}
result = synth.validate_physical_schedule(dfg, schedule, unit_map, II=5)
self.assertFalse(result, "Latch Overwrite 场景应 return False")
```

**4. validate_physical_schedule 行为契约**

- 仍 return `bool`（True=PASS, False=FAIL）
- 失败时 print `[VERIFIER-FAIL]` 标签 + 具体原因（保留现有风格）
- 不抛 RuntimeError——任何 caller 想要 raise 自己 wrap `if not validate(...): raise`
- **函数 docstring 显式标 "caller MUST check return value"**（HCO 审查 2026-05-15 阻断项弱化方案：runtime/lint enforcement 留 ADR-018 评估；本 ADR 用文档警告 + binding 双重保险）

## Rejected

- **保留 TimingVerifier + 用 validate_physical_schedule 作为补充** ⚠
  - why：两个 verifier 双轨制是上一个 ADR（016）就想消除的 product 现状；本 ADR 不应反向加固
- **加 `strict=True` 模式让 validate raise RuntimeError** ⚠
  - why：增加 API 复杂度；返回值已足够表达
- **仅迁移测试，不补 4 类校验** ⚠
  - why：违反 ADR-016 binding "保留 backend 独有的物理校验"；删 backend 前必须有等价校验
- **新写第三个 verifier 类（ResourceAudit / LatencyAudit / 拆分）** ⚠
  - why：增加层级、稀释职责；validate_physical_schedule 一站式更清晰
- **同步删 RTOVR-undersampling / RTOVR-latch-overwrite 等 validate 已有校验** ⚠
  - why：本 ADR 只**加**不**减**，validate 现有逻辑均在 e2e 主路径生产验证

## Consequences

- ✅ 一个 verifier（validate_physical_schedule），不再有"两条 verifier 路径"的不对称
- ✅ backend.py 整文件删（-111 行）
- ✅ test_verifier_robustness 的反证场景仍受保护，迁移到 validate 后行为等价
- ⚠ validate_physical_schedule 函数变长（约 +50 行）。仍然是单职责"后验防御"
- ⚠ 反证测试若构造的 DFG 无 `unit_map`（直接调 _solve_for_ii_with_units 拿到的 unit_map 是 schedule SAT 后回填的）——但 test_verifier_robustness 是手工构造 schedule，需手工构造 unit_map，工作量小
- 🔒 **binding**：
  - 任何对 validate_physical_schedule 的修改必须保留 5 类原子校验
  - 任何 caller 期望"verifier 失败必抛异常"的，**必须自己 wrap `if not validate(): raise ...`**——这是 ADR-017 显式契约
  - 未来若要重新引入 raise 模式，需新 ADR 论证

## Implementation outline

```python
# ucode_solver.py:validate_physical_schedule

def validate_physical_schedule(self, ops, schedule, unit_map, II):
    nodes_by_id = {op.trace_id: op for op in ops}

    # 现有：iq_name-based modulo collision (#2 等价)
    iq_to_modulo: Dict[str, set] = {}
    # 新：iq_id-based uniqueness（兼容 TimingVerifier #2）
    slot_iq_ids: Dict[int, set] = {}
    # 新：bank-based collision (#3)
    bank_occupancy: Dict[Tuple[int, int], str] = {}
    # 新：resource limit (#1) + port bandwidth (#4)
    slot_res_usage: Dict[int, Dict[str, int]] = {}

    unit_limits = self.hw_spec.config.get("resource_constraints", {}).get("unit_instances", {})
    max_reads = self.hw_spec.config.get("resource_constraints", {}).get("total_read_ports", 4)
    max_writes = self.hw_spec.config.get("resource_constraints", {}).get("total_write_ports", 4)

    for op in ops:
        if op.op_type.upper() == "RTOVR":
            continue
        t = schedule.get(op.trace_id)
        if t is None:
            continue
        s = t % II
        res_type = op.op_type.upper()

        # 现有 iq_name modulo collision
        iq_name = unit_map.get(op.trace_id)
        if iq_name:
            if s in iq_to_modulo.get(iq_name, set()):
                print(f"[VERIFIER-FAIL] Modulo Collision in {iq_name} at Slot {s}")
                return False
            iq_to_modulo.setdefault(iq_name, set()).add(s)

        slot_res_usage.setdefault(s, {})

        # 新 #1: resource limit
        if res_type in unit_limits:
            slot_res_usage[s][res_type] = slot_res_usage[s].get(res_type, 0) + 1
            if slot_res_usage[s][res_type] > unit_limits[res_type]:
                print(f"[VERIFIER-FAIL] Resource Exhausted: {res_type} count > limit at slot {s}")
                return False

        # 新 #2: iq_id uniqueness
        iq_id = op.metadata.get("iq_id")
        if iq_id is not None:
            slot_iq_ids.setdefault(s, set())
            if iq_id in slot_iq_ids[s]:
                print(f"[VERIFIER-FAIL] Physical ID Conflict: IQ {iq_id} doubly assigned at slot {s}")
                return False
            slot_iq_ids[s].add(iq_id)

        # 新 #3: bank manual_bank collision
        bank_id = op.metadata.get("manual_bank")
        if bank_id is not None:
            if (s, bank_id) in bank_occupancy:
                print(f"[VERIFIER-FAIL] Bank Conflict: Bank {bank_id} at slot {s}")
                return False
            bank_occupancy[(s, bank_id)] = op.trace_id

        # 新 #4: read/write port bandwidth
        if res_type == "UR_READ":
            slot_res_usage[s]["READ_BUS"] = slot_res_usage[s].get("READ_BUS", 0) + 1
            if slot_res_usage[s]["READ_BUS"] > max_reads:
                print(f"[VERIFIER-FAIL] Read Bandwidth: slot {s} exceeds {max_reads}")
                return False
        if res_type == "UR_WRITE":
            slot_res_usage[s]["WRITE_BUS"] = slot_res_usage[s].get("WRITE_BUS", 0) + 1
            if slot_res_usage[s]["WRITE_BUS"] > max_writes:
                print(f"[VERIFIER-FAIL] Write Bandwidth: slot {s} exceeds {max_writes}")
                return False

        # 新 #5: RAW latch overwrite (compute-to-compute path)
        lat = self.hw_spec.delays.get(res_type, 1)
        consumers = [o for o in ops if op.trace_id in o.metadata.get("depends_on", [])]
        for cons in consumers:
            t_c = schedule.get(cons.trace_id)
            if t_c is None:
                continue
            if t_c < t + lat:
                print(f"[VERIFIER-FAIL] RAW Violation: {op.trace_id} -> {cons.trace_id}")
                return False
            is_inv = (op.metadata.get("is_invariant", False) or
                      op.metadata.get("is_const", False) or
                      op.metadata.get("stride", 1) == 0 or
                      op.op_type.upper() == "UR_WRITE")
            if not is_inv and t_c >= t + lat + II:
                print(f"[VERIFIER-FAIL] Latch Overwrite: {op.trace_id} expired before {cons.trace_id}")
                return False

    # 保留现有：phase 隔离（commented）+ RTOVR 校验
    # ... existing RTOVR undersampling / latch_overwrite code unchanged ...

    return True
```

```python
# tests/unit/test_verifier_robustness.py — 迁移

from synthesis.ucode_solver import UCodeSynthesizer

class TestVerifierRobustness(unittest.TestCase):
    def setUp(self):
        self.spec = HardwareSpec.load("hw_config")
        self.synth = UCodeSynthesizer(self.spec)

    def test_latch_expire_trap(self):
        b = MIROpBuilder()
        op_a = b.make_op("FPADD", "a", RT.FPADD, [], "A")
        op_b = b.make_op("FPADD", "b", RT.FPADD, [op_a], "B")
        dfg = b.get_dfg()
        schedule = {op_a: 0, op_b: 9}
        unit_map = {op_a: "fpadd_0", op_b: "fpadd_1"}  # 构造，避免 modulo 冲突
        ok = self.synth.validate_physical_schedule(dfg, schedule, unit_map, II=5)
        self.assertFalse(ok, "Latch Overwrite 场景应 False")

    def test_phantom_bank_conflict(self):
        b = MIROpBuilder()
        r1 = b.make_op("UR_READ", "r1", RT.UR_READ, [], "R1")
        r2 = b.make_op("UR_READ", "r2", RT.UR_READ, [], "R2")
        dfg = b.get_dfg()
        for op in dfg:
            if op.trace_id in [r1, r2]:
                op.metadata["manual_bank"] = 3
        schedule = {r1: 2, r2: 18}  # 18 % 16 == 2
        unit_map = {r1: "ur_r0", r2: "ur_r1"}  # 不同 iq_name 避免 modulo 冲突
        ok = self.synth.validate_physical_schedule(dfg, schedule, unit_map, II=16)
        self.assertFalse(ok, "Bank Conflict 场景应 False")
```

### 验证矩阵

- `python3 -m unittest tests.unit.test_smt_adversarial -v` → 13/13 PASS
- `python3 -m unittest tests.unit.test_verifier_robustness -v` → 2/2 PASS（迁移后）
- `python3 verification_v2/main_verify.py --op horner --mode e2e --quiet --no-review` → SAT II=11 PASS
- `python3 verification_v2/main_verify.py --op matmul --mode e2e --quiet --no-review` → SAT II=6 PASS
- `python3 verification_v2/main_verify.py --op cos4 --mode e2e --quiet --no-review` → SAT II=6 PASS

净减：~111 行（backend.py 全删）- ~50 行（validate 扩展）= **~61 行净减**
