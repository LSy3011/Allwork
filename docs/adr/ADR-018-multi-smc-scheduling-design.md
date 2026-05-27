---
adr_id: ADR-018
title: 多 SMC 调度设计骨架（路径 C，SMT 模型加 SMC 维度）
decided: 2026-05-27
last_verified: 2026-05-27
still_holds: accepted
---

# ADR-018 · 多 SMC 调度设计骨架

> **状态：Accepted**（v7，hco 四审预批 1-3 阻断 + 4-6 轻度修订全部合入）。本会话仅产文档；下一会话进入 Stage 1 实施。
>
> **演进**：v1 4 项 P0 全未决 → v2 选项 Z → v3 hco 二审挖出 3 误判 → v4 user OQ-1~6 裁定 → v5 hco 三审 11 项必改 → v6 user OQ-8 = (i) 同步发射 → **v7（本版）hco 四审 C1/C2/C4 + D-1/D-2/D-3 全部修订，Accepted**

## Context

### 触发与范围

user 2026-05-27「多 SMC 的 matmul」→ 路径 C：SMT 调度器感知 32 SMC。本会话仅产 ADR，代码留下一会话。

### 现状（编译器与硬件断层）

| 层 | 多 SMC 就绪度 | 证据 |
|---|---|---|
| ISA 指令字段 | ⚠ **三类命名并存** | (i) SMC_ID 5-bit 大写（FPADD/FPMUL/UR_READ/UR_WRITE 行 14/33/86/98）；(ii) **SMC_ID 4-bit 大写**（LOGIC 行 67，**特例**）；(iii) smc_id_en 5-bit 小写（INTADD/FPTO*/FP32ADDTREE/INT8toFP16/SHIFTUP/SHIFTDN 行 123/135/147/161/173/182/190/199 + ADR-019 D1/D2） |
| 跨 SMC 通信原语 | ✅ 硬件已定义 | SHIFTUP(含 broadcast) + SHIFTDN(无 broadcast，OQ-5) chain；yaml + sim 落后 → [ADR-019](ADR-019-shift-cross-smc-yaml-sim-alignment.md) |
| 硬件参数 | ✅ 已就绪 | PARAM_SMC_NUM=32、PARAM_SMC_AXI_EN=1 |
| 仿真器集群层 | ⚠ **死代码原型** | `BusArbiter` + `TransferRequest` 在 main pipeline 零引用；不复活 |
| `HardwareSpec` schema | ⚠ 半就绪 | PARAM_SMC_NUM 在但 axiom 零消费 |
| `iq_config.yaml` + `modules.yaml` | ⚠ **SHIFTUP/SHIFTDN 整节零行**（hco 三审 A1/D1） | 由 ADR-019 D7 补 |
| `src/driver/hw_resource.py` | ⚠ RtoVR/IQ enum 硬编码 SMC-blind（hco 三审 F4） | 本 ADR D1 同步改 |
| SMT 调度 axiom | ❌ SMC-blind | bank/iq 按单维 key |
| 驱动 emitter | ❌ 写死单 SMC | `emitter.py:63 "smc_id": 0` |
| 主 pipeline ↔ SystemCluster 对接 | ❌ 未对接 | 本 ADR D5 承担 |

### user OQ 全部裁定（OQ-8 待答见 §Open Questions）

| 编号 | 答案 |
|---|---|
| Q-P0-1 ~ Q-P0-4 | per-SMC-per-IQ / chain / 不考虑 AXI / 选项 Z |
| OQ-1 SHIFTUP 链起点 | 任意 SMC 可发起；**数据源固定 SMC_0**；向上单向 |
| OQ-2 沿途 IQ slot | smc_id_en 匹配；SMC_ID ≤ smc_id_en 沿途每 SMC 解码消耗自己 IQ slot；**消耗时机 OQ-8 待答** |
| OQ-3 OUTSTANDING_INST_NUM | user 推迟，本 ADR 不实施 |
| OQ-4 现 shift yaml 字段 | 保留 |
| OQ-5 SHIFTDN broadcast | 无 broadcast 字段 |
| OQ-6 SMT axiom 守 broadcast 唯一源 | (b) 双层保险 |
| OQ-7 chain ring | P2 推迟 Stage 3+ |
| OQ-8 IQ slot 消耗时机 | **(i) 同步发射 t=t0**：所有沿途 SMC 在指令发起同 cycle 各占 IQ slot |

## Decision

### D1 · `HardwareSpec` schema + `hw_resource.py` enum 演进

```python
class ResourceConstraints(BaseModel):
    unit_instances_per_smc: Dict[str, int]   # 替换原全局 dict
    smc_shared_units: Set[str] = set()       # 当前为空

    @property
    def global_unit_instances(self) -> Dict[str, int]:
        return {k: (v if k in smc_shared_units else v * smc_num)
                for k, v in unit_instances_per_smc.items()}
```

`hw_spec.get_iq_ids` 新增 `smc_id: int` 参数；`HardwareSpec.assert_single_smc_mode()` fail-fast helper。

**`src/driver/hw_resource.py` enum 维度扩展（hco 三审 F4）**：
- `RtoVRInputSource` / `IQ` enum 当前 SMC-blind（如 `SHIFTUP_D = 29` 是全局 enum）
- 多 SMC 化后必须按 `(smc_id, enum_value)` 双 key 访问
- 方案：保留 enum 作 base id，新增 helper `get_per_smc_resource_id(enum_val, smc_id)`；旧 caller 显式传 `smc_id=0` 临时兼容
- enum 文件保持向后兼容（不改既有 int 值），只加访问 API

**`cross_smc_chain` 单元**（SHIFTUP/SHIFTDN）不进 `smc_shared_units`；本地实例 + I/O 跨 SMC 互联。

### D2 · Axiom 维度扩展（最小集）

| Axiom | 当前 key | 多 SMC key |
|---|---|---|
| `add_bank_conflict_axiom` | `bank_id` | `(smc_id, bank_id)` |
| `add_modulo_constraint` | `u_type` | `(u_type, smc_id)` |
| `add_spill_reload_mutex_axiom` | `spill_id` | `(smc_id, spill_id)` |
| `add_iq_latch_protect_axiom` | latch 资源单维 | `(smc_id, latch_resource)` |
| `add_side_effect_phase_axiom` | phase 单维 | `(smc_id, phase)` |

**SHIFTUP/SHIFTDN 单元独占（hco 三审 C2）**：D2 `add_modulo_constraint` 多 SMC key 自然承担 "SHIFTUP/SHIFTDN unit per-SMC 独占" 约束。不需要独立 axiom（v4 D3.c 已删，见 §Rejected）。

### D3 · 新增 axiom

#### D3.a · ~~`add_smc_outstanding_axiom`~~（**OQ-3 推迟**）

未来 ADR 评估。Stage 1 不写。

#### D3.b · `add_cross_smc_chain_raw_axiom`（**Stage 2 必做**）

SHIFTUP / SHIFTDN chain 跨 SMC RAW dep。`chain_stage_latency=1`。

**公式（hco 三审 C1 加 guard）**：

```
SHIFTUP:
  ∀ chain op, ∀ k ∈ [0, smc_id_en]:
    Implies(k <= smc_id_en,
            t_DR_SHIFTUP_D_at_SMC_k >= t_issue + k * chain_stage_latency)
```

```
SHIFTDN:
  ∀ chain op, ∀ k ∈ [0, smc_id_en]:
    Implies(k <= smc_id_en,
            t_CRU_SHIFTDN_O_at_SMC_k >= t_issue_at_SMC_{smc_id_en} + (smc_id_en - k) * chain_stage_latency)
```

**z3 处理**：smc_id_en 是 SMT Int 变量（OQ-1 任意 SMC 可发起 + emitter 决定具体 SMC），axiom 用 `z3.Implies` + Int 算术合法。复杂度 O(SMC_NUM × chain ops) 在可接受范围。

Stage 2 实施前需 [ADR-019](ADR-019-shift-cross-smc-yaml-sim-alignment.md) 完整 Accepted。

#### D3.c · ~~`add_broadcast_route_mutex_axiom`~~（**hco 三审 C2 删除**）

v4 提议守 "RtoVR-29 入口最多 1 个 source" 是错位裁定：`rtovr_config.yaml:34 consumers_dr.29 = SHIFTUP_D` 是 RtoVR-29 入口的**唯一** source（不存在多源争用）。真正约束 = "SHIFTUP 单元 per-SMC 独占同时刻 1 个 op"，由 D2 `add_modulo_constraint` 多 SMC key 自然承担。

#### D3.d · `add_broadcast_unique_source_axiom`（**Stage 1 必做**，OQ-6 b）

公式（hco 三审 B1/C3 明确语义）：

```
∀ cycle slot s ∈ [0, II):
  At-Most-1(op active in s where op.unit == SHIFTUP and op.broadcast == 1)
```

**语义关键（hco 三审 B1 加注）**：D5.c 把同一 broadcast op 复制到 SMC_0..smc_id_en 各份 hex 时，在 SMT 模型层**仍是 1 个逻辑 op**（emitter 复制是 lowering 阶段，不映射回多个 SMT 变量）；axiom 守的是"逻辑 broadcast op 在同 IQ_SHIFTUP cycle slot 同时刻至多 1 个"。

#### D3.e · `add_chain_iq_propagation_axiom`（**Stage 1 必做**，OQ-2 + OQ-8）

user OQ-8 答 **(i) 同步发射 t=t0**：所有沿途 SMC（SMC_0..smc_id_en）在指令发起**同 cycle** 各占用自己 IQ slot。

公式：

```
∀ chain op (SHIFTUP/SHIFTDN), ∀ k ∈ [0, SMC_NUM-1]:
  Implies(k <= smc_id_en,
          IQ_chain_slot(smc=k, t=op.t_issue) is occupied by op)
  // 即对沿途每 SMC_k：其本机 chain IQ 在 t=op.t_issue cycle 与本 SMC 其他同 IQ 类 op 互斥
```

**z3 处理**：对每条 chain op，沿 SMC_0..SMC_NUM-1 枚举 + Implies guard；axiom 注册到 modulo collision pool 时 key 用 `(chain_iq_type, smc_id, op.t_issue % II)`。复杂度 O(SMC_NUM × chain ops) 与 D3.b 同量级，可接受。

**资源池复用（hco 四审 C1）**：D3.e **不引入新资源池**，复用 D2 `add_modulo_constraint` 的 modulo collision pool；axiom 内部 register N=smc_id_en+1 个 `(smc_k, t)` 占位（k ∈ [0, smc_id_en]），每个占位与本 SMC 其他同类型 op 互斥。

**时间锚 (hco 四审 C4)**：D3.e 公式中 `op.t_issue` 是**编译器 schedule 决定的指令发射 cycle**（同步发射，OQ-8 (i)）；SHIFTUP / SHIFTDN 统一使用此锚（与是否 chain physical 起点无关）。**与 D3.b chain RAW 公式中的 `t_issue_at_SMC_{smc_id_en}` 是两个独立时间维度**：
- D3.e 守 **IQ slot 占用**：所有沿途 SMC 在编译期统一 `t=op.t_issue` cycle 占自己 IQ slot
- D3.b 守 **chain 数据传播**：物理数据从起点 SMC（SHIFTUP 是 SMC_0；SHIFTDN 是 SMC_{smc_id_en}）开始按 `chain_stage_latency=1` 拍逐 SMC 传播，消费者 SMC 在 t+chain_offset 才能消费

两个 axiom 协同：D3.e 守"沿途 SMC 同步发射指令"，D3.b 守"数据沿 chain 传播延迟"。

**与 D5.c emitter 自然吻合**：emitter 在 SMC_0..smc_id_en 每份 hex 同 cycle emit 同一 SHIFTUP 指令 ↔ axiom 守的就是这个 cycle 的 per-SMC IQ slot 互斥。

### D4 · 调度模式：选项 Z 两阶段

#### Stage 1 · X · Output-stationary by N

- 调度本质：SPMD + 1 个 broadcast 原语
- 数据流：A 每行 SHIFTUP `broadcast=1, smc_id_en=31`；每 SMC 持 B 的 N/32 列、算 C 的 N/32 列
- 算子库：matmul yaml 加 `parallel_dim: N`
- SMT 改动：D1 + D2 + **D3.d + D3.e**（D3.a 推迟，D3.c 删除）
- 适用：大 N matmul
- **强依赖 ADR-019 完整 Accepted**

#### Stage 2 · Y · K-stationary chain reduce

- 调度本质：SPMD + 跨 SMC chain RAW dep
- 数据流：每 SMC 持 K/32 段 A/B 做 partial dot；SHIFTDN chain 从 SMC_31 → SMC_0 累加
- 算子库：matmul yaml 加 `parallel_dim: K`
- SMT 改动：Stage 1 全部 + **D3.b（cross-SMC chain RAW axiom）**
- 适用：大 K matmul

#### Stage 3（推迟）· 真 MIMD + ring 评估

留 ADR-020+。

### D4.5 · 算子 yaml schema 扩展（hco 四审 D-3）

`verification_v2/ops/*.yaml` 需新增 `parallel_dim` 字段表达多 SMC partition：

```yaml
# 例：matmul_parallel_n.yaml
name: matmul
parallel_dim: N             # Stage 1 X 模式
formula: ...
tensors: ...

# 例：matmul_parallel_k.yaml
name: matmul
parallel_dim: K             # Stage 2 Y 模式
chain_reduce_depth: 31      # 与 PARAM_SMC_NUM-1 对齐
formula: ...
tensors: ...
```

Pydantic schema（建议位置 `src/api/op_spec.py` 或类似）：

```python
class OpYaml(BaseModel):
    name: str
    parallel_dim: Optional[Literal["N", "K", None]] = None  # None = 单 SMC 模式（向后兼容）
    chain_reduce_depth: Optional[int] = None  # 仅 parallel_dim=K 时有效
    formula: str
    tensors: Dict[str, TensorSpec]
```

向后兼容：现有算子 yaml 不含 `parallel_dim` 字段默认 None → 单 SMC 模式不变。

### D5 · 驱动与仿真器对接（emitter 注入策略三分）

#### D5.a · `SMC_ID` 字段注入（hex 复制时填本机 id）

适用单元（**hco 三审 B2 + LOGIC 4-bit 特例**）：
- FPADD / FPMUL / UR_READ / UR_WRITE（行 14/33/86/98）→ SMC_ID 5-bit
- **LOGIC（行 67）→ SMC_ID 4-bit**：emitter 写入时**必须 mask 高位**避免 truncate

emitter emit N 份 hex（`smc_{k}/.hex`）；每份 hex 的 SMC_ID 字段填本机 id k。非 SMT 变量。

#### D5.b · `smc_id_en` 字段（SMT 解出 + emitter 注入）

适用单元：INTADD / FPTO* / FP32ADDTREE / INT8toFP16 / **SHIFTUP / SHIFTDN**（modules.yaml 行 123+ + ADR-019 D1/D2）

`smc_id_en` 是 SMT Int 变量，由 partition strategy 决定（如 SHIFTUP broadcast → smc_id_en=31；SHIFTDN K-reduce → 起点 SMC_31）。

#### D5.c · broadcast 指令的多 SMC hex 分发（OQ-1 + OQ-2）

- **数据源**：SMC_0（OQ-1）；SMC_0 hex 在 broadcast 同 cycle 须有 data 准备（如 UR_READ）
- **指令源**：编译期决定（通常 SMC_0）
- **沿途 IQ 消耗（OQ-2）**：emitter 必须在 SMC_0..smc_id_en 的**每份 hex** 里同 cycle 出现该 SHIFTUP 指令；不在 chain 上（>smc_id_en）的 SMC 同 cycle NOP
- **沿途 SMC 的 data 字段填 0（hco 三审 E2）**：SMC_k (k>0) 的 broadcast SHIFTUP hex 中 data 字段填 0（硬件忽略，因为它们的 `cru_shiftup_i` 接上游不接本地 data）；只 SMC_0 hex 的 data 字段携带真实数据
- **唯一源约束**：OQ-6 (b) — emitter 层 + SMT axiom D3.d 双层保险
- **emitter lowering 时机（hco 四审 C2）**：emitter lowering 在 **SMT-solve 之后**执行（不是 SMT-solve 之前 codegen）。流程：(1) SMT 解出 smc_id_en 值（D5.b）+ 整张 schedule；(2) emitter 按解出的 smc_id_en 在 SMC_0..smc_id_en 的 hex 里同 cycle emit 该 SHIFTUP 指令；(3) SMC_{smc_id_en+1}..SMC_31 的 hex 同 cycle 不 emit（hex 流跳到下一指令或 VALID=0 NOP）。**拒选**：emitter 总是复制到全 32 份 + 硬件层 `if SMC_ID > smc_id_en` 丢弃——浪费 IQ slot 在 NOP，违反 OQ-2 "SMC_ID ≤ smc_id_en 沿途消耗" 语义边界

#### D5.d · sim_config.json layout

- 从单 `smc_0/` 扩到 `smc_{k}/` 目录数组
- `--mode hw-parity` 切换 `SystemCluster(num_kernels=N)` 路径
- **N=1 退化兼容验证**：本 ADR 实施前先跑 N=1 baseline 全 PASS 再加 N>1（hco 三审 F1）

### D6 · 不动的部分

- ADR-008 / ADR-009 Bank 冲突精度结论 holds（key 扩 `(smc_id, bid)` 不改物理语义）
- ADR-006 / ADR-016 / ADR-017 latch / verifier / solver 合并结论不动
- HSTS / RTOVR per-SMC 本地 crossbar 不动
- ISA header 41-bit 布局不动
- `BusArbiter` 死代码原型不复活；AXI 独立 ADR-021+

## Open Questions（仅剩 P2 一项）

### OQ-7 (P2) · chain 是否闭合 ring

Stage 3+ 评估。

## Rejected

- (a) 仅 matmul yaml 升级真矩阵乘 ⚠ — user 不选
- (b) 仅 emitter 数据并行 ⚠ — user 不选
- 直接机械替换不写 ADR ⚠ — 违反 ADR-004
- 复活 HSTS ⚠ — compactor 已取代
- 本会话直接产可执行 plan ⚠ — user 要求文档先
- (v1) lockstep first ⚠ — 无加速
- Free MIMD 作 Stage 1 ⚠ — z3 爆
- (v2) Stage 1 独立于 ADR-019 ⚠ — hco 二审推翻
- (v2) outstanding 自行推断 per-SMC ⚠ — hco 二审推翻；OQ-3 推迟
- enforce 对称性 axiom ⚠ — hco 二审 C2
- 复活 BusArbiter 作 AXI ⚠ — 独立 ADR-021+
- AXI 带宽建模 ⚠ — Q-P0-3 defer
- 加 SHIFTDN BROADCAST 字段 ⚠ — OQ-5 否决
- **(v4) D3.c `add_broadcast_route_mutex_axiom`** ⚠ — hco 三审 C2：rtovr-29 在 consumers 侧本就唯一源；真正约束由 D2 `add_modulo_constraint` 多 SMC key 自然承担
- **(v4) D3.b 公式无 guard** ⚠ — hco 三审 C1：`smc_id_en - k < 0` 时 axiom 应被 `Implies(k ≤ smc_id_en, ...)` 守
- **(v4) D5.a 适用清单遗漏 LOGIC 4-bit 特例** ⚠ — hco 三审 B2
- **(v4) D5.c 不约定沿途 SMC data 字段值** ⚠ — hco 三审 E2：必须显式 SMC_k (k>0) data 填 0
- **OQ-8 (ii) 沿途到达 staggered IQ 消耗** ⚠ — user OQ-8 选 (i) 同步发射；(ii) 永久 rejected

## Consequences

- ✅ 编译器与硬件契约 / 仿真器在多 SMC 维度的断层有完整弥合路线
- ✅ 4 项 P0 + OQ-1~7 全部裁定（OQ-8 留 P0 user-clarify）；hco 三审 11 项可自决修订全部合入
- ✅ ADR-008 / 009 / 016 / 017 不冲突
- ✅ ADR-019 v4 yaml + sim + iq_config + modules 全面对齐，N=1 退化兼容
- ✅ axiom 公式加 guard、emitter 三段策略完整、enum 维度扩展显式
- ⚠ Stage 1 工作量：D1（schema + hw_resource enum）+ D2（5 axiom 维度扩展）+ D3.d + D3.e（待 OQ-8）+ D5 三段改造 + ADR-019 v4 完整 Accepted ≈ 2-3 会话
- ⚠ Stage 2 工作量：Stage 1 + D3.b chain RAW ≈ 1-2 会话
- ⚠ ADR-008 / 009 errata 草稿（hco 四审 D-2，待 Stage 1 实施提交时回填到对应 ADR 文件）：
  - **ADR-008 errata**："Bank 冲突 key 在多 SMC 模式扩展为 (smc_id, bid)；R-R / W-W / W↔R 三段语义在跨 SMC 不适用（不同 SMC 同 bank_id 同 addr 是不同物理 Bank，应 SAT 不冲突）。物理语义不变。"
  - **ADR-009 errata**："`add_bank_conflict_axiom` 实施 key 从 `bid` 扩为 `(smc_id, bid)`；R-R 同地址豁免、W↔R 无豁免规则均限于同 SMC 内成立。" 
- ⚠ `docs/hco/assumptions.md` 新增 A10 措辞澄清 + cross-SMC chain 拓扑条目
- ⚠ ADR-010 / 011 phase axiom 在 32 SMC 下 Stage 1 实施时重跑 A/B 实测（hco 二审 C1）
- 🔒 **binding**：
  - Proposed → Accepted **必须** hco 四审通过（OQ-8 已答）
  - Stage 1 前 ADR-019 v4 完整 Accepted
  - Stage 2 前 Stage 1 完成
  - 任何"绕开 D3.b chain RAW axiom 直接 emit chain 指令"违反契约
  - 任何"emitter 32 SMC 同发起 broadcast 不走 D5.c + D3.d 双守"违反契约
  - 任何"SMC_k (k>0) broadcast hex data 字段填非 0"违反契约
  - 任何"emitter 对 LOGIC 写 5-bit SMC_ID 不 mask 高位"违反契约
  - 选项 Z 回退到其他方向需新 ADR 论证
  - lockstep first / Stage 1 独立 ADR-019 / outstanding per-SMC 自推断 / D3.c rtovr-29 multi-source / D3.b 无 guard / D5.a 缺 LOGIC 特例 / D5.c 不约定 data 填 0 均永久 rejected
  - `BusArbiter` 不在本 ADR scope 复活

## Validation matrix（实施阶段）

### Stage 1 完成时

- `python3 -m unittest tests.unit.test_smt_adversarial -v` → 原 13 + 新 case 全 PASS
  - `test_cross_smc_bank_non_conflict`：(smc=0, bank=3) vs (smc=1, bank=3) 同时刻 → SAT
  - `test_same_smc_bank_conflict_still_unsat`：同 SMC 同 Bank → UNSAT
  - `test_broadcast_unique_source`：两 broadcast 同时刻 → UNSAT（D3.d）
  - `test_chain_iq_propagation`：沿途 SMC_0..smc_id_en 在 t=t0 同步占用 SHIFTUP IQ slot（OQ-8 (i)）；与本地同 IQ 类 op 冲突 → UNSAT
  - `test_logic_smc_id_4bit_mask`：LOGIC 单元写 SMC_ID=17 (>15) → 编译器/emitter 应 fail-fast 或 mask（验 D5.a 4-bit 特例）
- `tests/unit/test_hw_spec.py`：`test_get_iq_ids_per_smc` + `test_per_smc_resource_id_helper`（验 hw_resource.py 扩展）
- **`add_side_effect_phase_axiom` A/B 实测**（hco 二审 C1）：32 SMC 下 net effect ≥ 单 SMC 不退化
- e2e 不退化：horner / cos4 / activation 13/13 / matmul (单 SMC) 全 PASS
- N=1 hw-parity baseline：horner / matmul / cos4 全 PASS（**Stage 1 实施前 gate**）
- 新 e2e：`matmul --parallel_dim=N` 32 SMC SAT + hw-parity

### Stage 2 完成时

- `test_cross_smc_chain_raw_respected`：SHIFTDN chain RAW 在 SMT 满足
- `test_chain_latency_propagation_with_guard`：smc_id_en < k 时 axiom 被 Implies guard 跳过
- `test_chain_latency_propagation`：chain offset N → 延迟 N × stage_latency
- `matmul --parallel_dim=K` 32 SMC SAT + hw-parity

## References

- ADR-019 v6 SHIFTUP/SHIFTDN 跨 SMC yaml + sim 补齐（**本 ADR 所有 Stage 硬前置**）
- ADR-004 文档先行
- hco subagent v1 + v2 + v3 + v4 四审报告（in-conversation 2026-05-27）
- ADR-006 / ADR-016 / ADR-017 latch / verifier / solver
- ADR-008 / ADR-009 Bank 冲突精度（Accepted 后回填 errata）
- ADR-010 / ADR-011 phase axiom（Stage 1 实施时 A/B 实测 gate）
- `docs/hco/assumptions.md` A10 / `docs/hco/detail_hw_isa_contract.md` § 1.1-1.5 / `docs/hco/detail_hw_spec.md` § 7
- `hw_config/modules.yaml` 行 14/33/86/98（SMC_ID 5-bit）+ 行 67（LOGIC SMC_ID 4-bit 特例）+ 行 123/135/147/161/173/182/190/199（smc_id_en 5-bit 小写）
- `hw_config/rtovr_config.yaml:34`（consumers_dr.29 = SHIFTUP_D 唯一源）
- `src/api/hw_spec.py:88-110,160-178`（pickle cache 自动 invalidate + FieldMapper 加载）
- `src/synthesis/axioms/{resource,physical,dependency}_axioms.py`
- `src/synthesis/ucode_solver.py`
- `src/driver/emitter.py:63` + `src/driver/hw_resource.py:23,24,68,104`（SMC-blind enum）
- `sim/smc_simulator/system/cluster.py`（BusArbiter 不复活）
- `sim/smc_simulator/units/{shift_up,shift_dn}_unit.py` + `sim/smc_simulator/core/kernel.py:498-505`（ADR-019 v4 D3/D4 改造）
- user 2026-05-27 硬件 spec + Q-P0 4/4 + OQ-1~6 + OQ-8 全部裁定（仅 OQ-7 P2 推迟）
