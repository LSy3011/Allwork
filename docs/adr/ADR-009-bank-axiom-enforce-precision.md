---
adr_id: ADR-009
title: SMT 层补齐 Bank 冲突约束（兑现 ADR-008）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-009 · SMT 层补齐 Bank 冲突约束

## Context

[ADR-008](ADR-008-bank-conflict-precision.md) 确立 Bank 冲突精确语义（R-R / R-W / W-W 同 Bank 同 cycle 全禁），但记录了两层代码不一致：

- `backend.py:TimingVerifier`（post-check） ✅ 严格按 ADR-008
- `resource_axioms.add_bank_conflict_axiom` ❌ 只禁同类型，R+W 未禁

> 注：ADR-008 起草时误判 ucode_solver 完全未调 bank_conflict axiom，本 ADR 起草过程中复核发现 ucode_solver 通过 `add_resource_constraint` 间接调用了该 axiom（resource_axioms.py:25-32），ADR-008 末尾已加 errata。所以**真实不一致只有"axiom 自身太松"这一层**。

但 axiom 太松依然意味着依赖 TimingVerifier 兜底——这是与 [ADR-006](ADR-006-smt-latch-lifetime-upper-bound.md) latch lifetime 完全相同的 pattern：axiom 漏 + Verifier 兜底。Phase 2 的任务就是把 axiom 补齐。

## Decision

**1. 修 `src/synthesis/axioms/resource_axioms.py:add_bank_conflict_axiom`**：

在现有"同类型互斥"两段（W-W 和 R-R）之外，**新增一段"跨类型 W↔R 互斥"**——同 Bank 同 modulo slot 的任意 (W, R) 对都不允许。保留 R-R 同地址豁免（编译期广播读优化），但**跨类型 W↔R 无地址豁免**（W 与 R 即使巧合 addr 相同，语义不同，物理上仍是两个端口同 cycle 访问）。

修改要点同时覆盖 `is_legacy` 分支和 non-legacy 分支。

**2. ucode_solver e2e 主路径无需新增调用**：

ucode_solver:215 已通过 `axioms.add_resource_constraint(...)` 间接调用 `add_bank_conflict_axiom`（resource_axioms.py:25-32）。修了 axiom 自身，主路径自动生效。

**3. R-R 同地址豁免**：保留。

ADR-008 字面"任意两端口全禁"是硬件 RTL 层面单端口 SRAM 的物理约束；R-R 同地址在编译器层是 CSE / 共享读优化——编译器把两个相同 (bank, addr) 的 R 合并为一次硬件读 + 数据 fanout 到多消费者。**硬件层面实际只有 1 个端口访问**，因此保留豁免不违反 ADR-008。

**4. W↔R 跨类型无地址豁免**：明确无豁免。

user 2026-05-15 确认：AIACC UR Bank 硬件 RTL **无 write-forwarding 机制**，同 cycle 同 bank 同 addr 的 W + R 是物理冲突。R-R 同地址豁免不可类推到 W↔R——这条只是为了 R-R 编译期共享读优化，与硬件无关。

## Rejected

- **不修，依赖 Verifier 兜底** ⚠
  - why：违反 ADR-008 binding 中 SMT 应是 source of truth 的原则；同 ADR-006 reject 理由
- **同时移除 R-R 同地址豁免** ⚠
  - why：豁免对应编译期合并读优化，硬件层面不真触发两端口；移除会让大量生产 yaml 算子 SMT UNSAT，且无物理依据
- **加新 axiom `add_bank_full_mutex_axiom` 与现 axiom 并存** ⚠
  - why：避免与现有 axiom 命名混淆，直接修主 axiom 更清晰（同 ADR-006 reject 理由）

## Consequences

- ✅ SMT、Verifier、ADR-008 三层一致；ucode_solver e2e 主路径终于有 Bank 冲突约束
- ⚠ 已有 yaml 算子若曾隐性依赖"R+W 同 Bank 同 cycle 不被 SMT 拒"，新约束会触发 UNSAT；必须跑完整验证矩阵（horner / activation 13/13 / matmul / hw-parity / smt-unit）
- ⚠ R-R 同地址豁免保留——若未来发现硬件级也禁（即广播读不是硬件特性而是错误假设），需要独立 ADR 拿掉
- ⚠ 理论上 O(N²) 同 unit 类型对枚举可能略增 SMT 求解开销（与现有 `add_alu_liveness_axiom` 同量级，可忽略）
- ⚠ **已知 silent gap**（沿袭旧行为，本 ADR 不修）：op 若同时缺 `manual_bank` / `bank_id` / `logical_addr` 三个 metadata，`get_op_bank_and_addr` 返回 `bid=None`，该 op 被静默跳过所有 bank 约束（包括 W-W / R-R / W↔R）。生产 DFG 中应不存在此类 op（RTOVR 与早期未 patch 中间节点除外），但语义层面是 silent gap——HCO 在审查时应核对 op metadata 完备性
- 🔒 **binding**：
  - 修复后任何新算子触发 Bank UNSAT，**不应**通过加 metadata 绕开 axiom——应当反思是否真冲突
  - 若 e2e 验证发现生产 yaml 回归，**不接受简单移除新约束**——先看是否能调整 yaml 的 manual_bank layout
  - R-R 同地址豁免的实现必须使用编译期已知的 `logical_addr` metadata，不能依赖 SMT 求出来的运行时地址
