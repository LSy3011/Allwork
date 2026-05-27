---
adr_id: ADR-019
title: 补齐 SHIFTUP / SHIFTDN 跨 SMC 字段（hw_config yaml + sim 对齐硬件 spec）
decided: 2026-05-27
last_verified: 2026-05-27
still_holds: accepted
---

# ADR-019 · SHIFTUP / SHIFTDN 跨 SMC 字段补齐

> **状态：Accepted**（v6，hco 四审通过）。是 [ADR-018](ADR-018-multi-smc-scheduling-design.md) **任何 Stage** 的硬前置。本会话仅产文档；下一会话进入实施。
>
> **演进**：v1 整删现字段 → v2 hco 二审保留 SRC_PREC → v3 user OQ-4/5 裁定 → v4 hco 三审 11 项必改 → v5 OQ-8 (i) 同步发射 → **v6（本版）hco 四审通过 Accepted**

## Context

### 触发

ADR-018 起草过程中（2026-05-27），user 给出 SHIFTUP / SHIFTDN 硬件 spec 摘录（详见 v3 Context；不重复）。

### user OQ 全部裁定（2026-05-27）

| OQ | 答案 | 影响本 ADR |
|---|---|---|
| OQ-1 SHIFTUP 链起点 SMC | 任意 SMC 可发起；**数据源固定 SMC_0**；向上单向 chain | D3 |
| OQ-2 沿途 IQ slot 占用 | smc_id_en 匹配沿途；SMC_ID ≤ smc_id_en 每 SMC 解码并消耗自己 IQ slot | D1/D2 iq_slot_consumption |
| OQ-4 现 shift yaml 字段 | 保留 MODE/AMOUNT/SRC_PREC/UPDATE_ST | D1/D2 |
| OQ-5 SHIFTDN broadcast | 确认无 broadcast 字段 | D2 |
| OQ-8 IQ slot 消耗时机 | **(i) 同步发射 t=t0** — 所有沿途 SMC 同 cycle 各占自己 IQ slot | D1/D2 iq_slot_consumption + ADR-018 D3.e/D5.c |

### hco 三审挖出的关键 gap（v3 未覆盖）

| Gap | 来源 |
|---|---|
| `hw_config/iq_config.yaml` **零 IQ_SHIFTUP/SHIFTDN 行**（grep 全 84 行 0 命中）| hco 三审 A1 |
| `hw_config/modules.yaml` **零 SHIFTUP/SHIFTDN 行**（v2 已确认，但 ADR 实施清单遗漏）| hco 三审 D1 |
| 字段命名 `SMC_ID_EN` 大写 vs modules.yaml 既有 `smc_id_en` 小写 → FieldMapper 撞 key | hco 三审 B3 |
| `specs/units/shiftdn.yaml` outputs 仅 D（无 rtovr_src_id），ADR-019 v3 删 D 时缺 grep 验证 | hco 三审 F6 |
| N=1 退化语义未定义（SMC_0 cru_shiftup_i 默认值） | hco 三审 D2 |
| SMC_0 "专用 chain head 入口" 是 hco 推断不是 user 明说 | hco 三审 E1 |

## Decision

### D1 · `hw_config/specs/units/shiftup.yaml`

```yaml
unit_name: SHIFTUP
latency: 1                              # 与 pipeline_delays.yaml:16 对齐
code_bits: 22                           # 新增：VALID(1)+BROADCAST(1)+SMC_ID_EN(5)+MODE(4)+AMOUNT(8)+SRC_PREC(2)+UPDATE_ST(1)
attributes:
  throughput: 1
  desc: Cross-SMC chain shift (upward), supports broadcast and point-to-point modes
  cross_smc_chain: true
  # 推断（hco 三审 E1）：SMC_0 物理上接专用 chain head 入口；若硬件 RTL 实为 ring 拓扑（OQ-7 P2）则触发 ADR-020 重做
  cross_smc_data_source: SMC_0
  chain_direction: ascending
ports:
  inputs:
    - name: CRU_SHIFTUP_I
      width: 128
      cross_smc_src: true
  outputs:
    - name: CRU_SHIFTUP_O
      width: 128
      cross_smc_dst: true
    - name: DR_SHIFTUP_D
      width: 128
      type: data
      rtovr_src_id: 29
micro_instructions:
  - field: VALID
    width: 1
    default: 0
    encoding: { 0: INVALID, 1: VALID }
  - field: broadcast                    # 小写对齐 modules.yaml 既有 smc_id_en 惯例（hco 三审 B3）
    width: 1
    default: 0
    encoding: { 0: P2P, 1: BROADCAST }
  - field: smc_id_en                    # 小写
    width: 5
    desc: "broadcast=0 时为目标 SMC ID；broadcast=1 时为广播 mask 上界（SMC_ID ≤ smc_id_en 暴露 DR_SHIFTUP_D）"
  # OQ-4 保留现 yaml 字段
  - field: MODE
    width: 4
  - field: AMOUNT
    width: 8
  - field: SRC_PREC
    width: 2
    default: 1
    encoding: { 0: PREC_16, 1: PREC_32 }
  - field: UPDATE_ST
    width: 1
    default: 1
    encoding: { 0: 'NO', 1: 'YES' }
behavioral_semantics:
  chain_propagation: "SMC_ID ≤ smc_id_en: CRU_SHIFTUP_O ← CRU_SHIFTUP_I"
  endpoint_expose: "broadcast=0 & SMC_ID == smc_id_en: DR_SHIFTUP_D ← CRU_SHIFTUP_O"
  broadcast_expose: "broadcast=1 & SMC_ID ≤ smc_id_en: DR_SHIFTUP_D ← CRU_SHIFTUP_O"
  chain_stage_latency: 1
  iq_slot_consumption: "SMC_ID ≤ smc_id_en 沿途每 SMC 在 t=t0（同步发射，OQ-8 (i)）各消耗自己的 SHIFTUP IQ slot"
```

### D2 · `hw_config/specs/units/shiftdn.yaml`

```yaml
unit_name: SHIFTDN
latency: 1
code_bits: 19                           # VALID(1)+smc_id_en(5)+MODE(4)+AMOUNT(8)+UPDATE_ST(1)
attributes:
  cross_smc_chain: true
  # 推断（同 E1）：SHIFTDN 起点 = SMC_{smc_id_en}（SMT 解出），数据从起点本地 DVR_SHIFTDN_S 取
  cross_smc_data_source: dynamic
  chain_direction: descending
ports:
  inputs:
    - name: CRU_SHIFTDN_I
      width: 128
      cross_smc_src: true
    - name: DVR_SHIFTDN_S
      width: 128
  outputs:
    - name: CRU_SHIFTDN_O
      width: 128
      cross_smc_dst: true
    # 保留现 yaml 的 D output 端口（hco 三审 F6 推荐 grep 算子 yaml 确认 SHIFTDN_D 零引用再删；本 ADR 不删，留 errata 实施前 verify）
    - name: D
      width: 128
      type: data
micro_instructions:
  - field: VALID
    width: 1
  - field: smc_id_en                    # 小写
    width: 5
    desc: "起点 SMC ID（SMC_ID == smc_id_en 从本地 DVR_SHIFTDN_S 取数；SMC_ID < smc_id_en 从 CRU_SHIFTDN_I 取数）"
  # OQ-5 user 确认：SHIFTDN 无 BROADCAST 字段
  # OQ-4 保留现字段
  - field: MODE
    width: 4
  - field: AMOUNT
    width: 8
  - field: UPDATE_ST
    width: 1
    default: 1
behavioral_semantics:
  start_point: "SMC_ID == smc_id_en: CRU_SHIFTDN_O ← DVR_SHIFTDN_S"
  chain_propagation: "SMC_ID < smc_id_en: CRU_SHIFTDN_O ← CRU_SHIFTDN_I"
  chain_stage_latency: 1
  iq_slot_consumption: "SMC_ID ≤ smc_id_en 沿途每 SMC 在 t=t0（同步发射，OQ-8 (i)）各消耗自己的 SHIFTDN IQ slot"
```

### D3 · `sim/smc_simulator/units/shift_up_unit.py` 重构

```python
def evaluate(self, inputs, cycle):
    ctrl = inputs.get('ctrl', 0)
    smc_id = inputs.get('smc_id', 0)             # 本 SMC 的 ID（cluster 注入）；N=1 退化默认 0
    chain_in = inputs.get('cru_shiftup_i', 0)    # 来自 SMC_{smc_id-1}；N=1 退化默认 0（hco 三审 D2）
    local_data = inputs.get('data', 0)           # 仅 SMC_0 用（chain head 数据源）；N=1 退化仍取本地 R0 维持向后兼容

    instr = self.decoder.decode(ctrl)
    if instr.VALID != "VALID":
        self._next_cru_o = self.cru_o
        return

    smc_id_en = instr.smc_id_en                  # 小写
    broadcast = instr.broadcast                  # 小写

    if smc_id <= smc_id_en:
        if smc_id == 0:
            self._next_cru_o = local_data
        else:
            self._next_cru_o = chain_in
    else:
        self._next_cru_o = self.cru_o

    if (broadcast == 0 and smc_id == smc_id_en) or (broadcast == 1 and smc_id <= smc_id_en):
        self._next_dr_d = self._next_cru_o
```

**N=1 退化兼容（hco 三审 D2）**：
- `inputs['smc_id']` 缺省 0（单 Kernel 模式）
- `inputs['cru_shiftup_i']` 缺省 0
- `inputs['data']` 仍取 kernel.py:500 hardcoded `self.ur_bank.get_read_data(0)`，保持现 N=1 hw-parity 测试不破

**`kernel.py:498-505` call-site 同步改造**：传入 `smc_id` + `cru_shiftup_i` + `data`。

### D4 · `sim/smc_simulator/units/shift_dn_unit.py` 对称重构

```python
def evaluate(self, inputs, cycle):
    ctrl = inputs.get('ctrl', 0)
    smc_id = inputs.get('smc_id', 0)
    chain_in = inputs.get('cru_shiftdn_i', 0)
    dvr_s = inputs.get('dvr_shiftdn_s', 0)

    instr = self.decoder.decode(ctrl)
    if instr.VALID != "VALID":
        self._next_cru_o = self.cru_o
        return

    smc_id_en = instr.smc_id_en
    if smc_id == smc_id_en:
        self._next_cru_o = dvr_s
    elif smc_id < smc_id_en:
        self._next_cru_o = chain_in
    else:
        self._next_cru_o = self.cru_o
```

**N=1 退化**：cru_shiftdn_i 默认 0；dvr_shiftdn_s 仍从本 SMC RtoVR 出口取（kernel.py:504 现行为）。

### D5 · `sim/smc_simulator/system/cluster.py` 互联补齐

```python
class SystemCluster:
    def step(self):
        for kernel in self.kernels:
            kernel.tick()
        for k in range(len(self.kernels) - 1):
            self.kernels[k+1].set_chain_input('shiftup', self.kernels[k].get_chain_output('shiftup'))
            self.kernels[k].set_chain_input('shiftdn', self.kernels[k+1].get_chain_output('shiftdn'))
        # OQ-7（P2 推迟）：是否闭合 ring；Stage 3+ 评估
```

**`BusArbiter` 死代码原型不复活**；AXI 通路独立 ADR-021+。

### D6 · `--mode hw-parity` 走 SystemCluster

由 [ADR-018](ADR-018-multi-smc-scheduling-design.md) D5 承担对接。本 ADR 只保 SystemCluster N=1 退化兼容 + chain wire 接口就位。

### D7 · 同步改的其他 yaml（**hco 三审 D1 新增**）

#### D7.a · `hw_config/iq_config.yaml` 新增 IQ_SHIFTUP / IQ_SHIFTDN 节

```yaml
iq_config:
  # ... 现有条目 ...
  IQ_SHIFTUP:
    module: SHIFTUP
    count: 1
    EN_LOOPS: false
    EN_DLY: false
    EN_INC: false
    EN_COND: false
    BITS_EMBED: 0
    BITS_EMBED_END: 0
    BITS_CODE: 22                       # 与 specs/units/shiftup.yaml code_bits 对齐
  IQ_SHIFTDN:
    module: SHIFTDN
    count: 1
    BITS_CODE: 19
```

#### D7.b · `hw_config/modules.yaml` 新增 SHIFTUP / SHIFTDN 模块行

modules.yaml 当前**完全无 shift 行**。补行以让 `add_resource_constraint` 能 lookup 单元 instance：

```yaml
modules:
  # ... 现有条目 ...
  SHIFTUP:
    type: cross_smc_chain
    latency: 1
    inputs: [CRU_SHIFTUP_I]
    outputs: [CRU_SHIFTUP_O, DR_SHIFTUP_D]
    code_bits: 22
    # code_fields 由 specs/units/shiftup.yaml 加载（hw_spec.py:167-176）
  SHIFTDN:
    type: cross_smc_chain
    latency: 1
    inputs: [CRU_SHIFTDN_I, DVR_SHIFTDN_S]
    outputs: [CRU_SHIFTDN_O, D]
    code_bits: 19
```

#### D7.c · `specs/system/system_core.yaml` unit_instances 维度调整

由 [ADR-018 D1](ADR-018-multi-smc-scheduling-design.md) 承担：`unit_instances` → `unit_instances_per_smc`；SHIFTUP=1 / SHIFTDN=1 不动（含义改为 per-SMC 一个）。

### D8 · 不动的部分

- `iq_registry` (SHIFTUP=29, SHIFTDN=34) 不动
- `src/driver/hw_resource.py` IQ enum 维度扩展由 ADR-018 D1 承担（hco 三审 F4）
- ISA header 41-bit 布局不动
- `pipeline_delays.yaml SHIFTUP/SHIFTDN=1` 不动

## Rejected

- 合并到 ADR-018 ⚠ — user 显式要求独立
- 整删现 yaml 字段（v1） ⚠ — user OQ-4 否决
- 加 SHIFTDN BROADCAST 字段（v2） ⚠ — user OQ-5 确认无
- 大写字段名 `SMC_ID_EN` / `BROADCAST`（v3） ⚠ — hco 三审 B3：与 modules.yaml 既有惯例冲突
- 不补 iq_config.yaml / modules.yaml 节（v3） ⚠ — hco 三审 A1/D1：SHIFTUP/SHIFTDN 整条 IQ 通路在 iq_config 层就缺失
- 删 SHIFTDN outputs.D（v3） ⚠ — hco 三审 F6：实施前需 grep 算子 yaml 确认零引用；本 ADR 保留 D，留 errata 验证后再删
- sim 改造不保 N=1 退化（v3） ⚠ — hco 三审 D2：所有 hw-parity 测试 N=1 退化是关键回归 gate
- 复活 `BusArbiter` ⚠ — 独立 ADR-021+
- 改 ISA header ⚠ — 跨 SMC 在微指令体内

## Open Questions（仅剩 P2，不阻塞实施）

### OQ-7 (P2) · chain 是否闭合 ring

Stage 3+ 评估。

## Consequences

- ✅ yaml + sim + iq_config + modules 与硬件 spec 全面对齐
- ✅ user 全部 OQ 裁定合入；hco 三审 11 项可自决修订合入
- ✅ N=1 退化兼容显式约束，hw-parity 现测试不破
- ✅ 字段命名小写对齐 modules.yaml 既有惯例，杜绝 FieldMapper 撞 key
- ⚠ 字段编码总位宽变化：SHIFTUP 22 bit / SHIFTDN 19 bit，需 hco 四审 sign-off
- ⚠ `kernel.py:498-505` SHIFTUP/SHIFTDN call-site 是接口破坏点，必须随 D3/D4 同步改
- ⚠ SHIFTDN outputs.D 删除留实施前 grep 验证（默认不删）
- 🔒 **binding**：
  - 本 ADR 实施时**必须** hco subagent 四审通过
  - sim 改造完成后跑 hw-parity 矩阵 N=1 退化全 PASS（horner / matmul / cos4）
  - 字段宽度 + 命名小写 + iq_config 新增节三处必须随 D1/D2/D7 同步落地
  - SHIFTDN_D 删除前必须 grep 算子 yaml 零引用证据
  - SMC_0 "专用 chain head 入口" 是 hco 推断，硬件 RTL 验证若反证（ring 拓扑）触发 ADR-020 重做

## Validation matrix（实施阶段）

- `python3 -m pytest tests/unit/ -v` → 全 PASS
- `python3 verification_v2/main_verify.py --op {horner,matmul,cos4} --mode hw-parity --quiet --no-review` → PASS（N=1 退化兼容）
- 新增 `sim/test_cross_smc_shift.py`：
  - 2 SMC SHIFTUP P2P (smc_id_en=1, broadcast=0)：SMC_0 本地数据第 1 拍流到 SMC_1 的 DR_SHIFTUP_D
  - 4 SMC SHIFTUP broadcast (smc_id_en=3, broadcast=1)：第 k 拍每 SMC_k 暴露 DR_SHIFTUP_D
  - 2 SMC SHIFTDN (smc_id_en=1)：SMC_1 起点 → SMC_0 第 1 拍收到
- 字段命名 grep gate：`grep -rn "SMC_ID_EN\|BROADCAST" hw_config/ src/`（大写应零命中除注释）

## References

- ADR-018 v7（**本 ADR 是其所有 Stage 硬前置**）
- ADR-004 文档先行
- hco subagent v1 + v2 + v3 + v4 四审报告（in-conversation 2026-05-27）
- user 2026-05-27 SHIFTUP / SHIFTDN 硬件 spec 摘录 + 全 OQ 答案
- `hw_config/specs/units/{shiftup,shiftdn}.yaml`（D1/D2 待改）
- `hw_config/iq_config.yaml`（D7.a 新增节）
- `hw_config/modules.yaml`（D7.b 新增行）
- `hw_config/pipeline_delays.yaml:16-17`（latency=1 source of truth）
- `sim/smc_simulator/units/{shift_up_unit,shift_dn_unit}.py`（D3/D4）
- `sim/smc_simulator/core/kernel.py:498-505`（call-site 接口破坏点）
- `sim/smc_simulator/system/cluster.py`（D5；BusArbiter 不复活）
- `src/api/hw_spec.py:160-178`（FieldMapper 加载路径）+ `:88-110`（pickle cache mtime 守，schema 改自动 invalidate）
