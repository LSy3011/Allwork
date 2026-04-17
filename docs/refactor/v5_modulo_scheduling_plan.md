# AIACC V5.0 模调度架构重构计划 (Modulo Scheduling Refactor Plan)

## 0. 背景与目标
V1 架构采用“贪心/Makespan SMT + 启发式 Compactor 后处理折叠”的方案，导致 SMT 求解规模随输入数据量膨胀，且各 IQ 队列的 `Setup - Payload - End` 循环头注入容易导致时钟错位。
**V5.0 目标**：彻底拥抱**模调度 (Modulo Scheduling)** 与**内核切片 (Kernel Slice)**，实现任意阶公式 × 任意批次输入的 100% 满载流水线（Bit-exact, Cycle-accurate）。

---

## 1. 核心架构跃迁点
- **SMT 目标转换**：从 `minimize(max_t)` 变为 `minimize(II)` (找寻最小 Initiation Interval)。
- **求解维度折叠**：只需解出单次内核迭代（阶数为 N 带来的 ~7N 个操作）的时序，变量不再随 `LOOPS` 放大。
- **取消 Compactor 启发式预测**：废除依靠序列检测来生成 Loop 的逻辑，Loop 直接由 SMT 的 Phase Mapping 绝对主导。
- **硬件 Setup 同步协议**：所有的 IQ 必须全局对齐，第一拍全部发射 `EMBED_LOOPS` 配置指令，最后一次发射携带 `EMBED_END` 掩码。

---

## 2. 实施任务清单 (Task List)

### 阶段一：DFG 与约束层 (Synthesis Layer) 重构
- [ ] **Task 1.1: 模调度变量抽象**
  - 在 SMT 建立三维变量系：`t[op]` (绝对时间), `k[op]` (发射批次), `phase[op] = t[op] % II` (内核相位)。
  - 显式化边的路由：定义 `t_route[edge]` 并约束 `t_route == t[src] + L`。
- [ ] **Task 1.2: 模调度公理集重写 (`axioms_modulo.py`)**
  - **C1 资源互斥**：相同硬件单元（如 FPMUL）的 `phase` 必须互异 (`Distinct`)。
  - **C2 路由前置**：同上，`t[dst] == t_route + 1` (满载情况下)。
  - **C3 Bank 静态互斥**：利用在 `MemoryAllocator` 中早已定好的 `bank_id`，若属于同一 Bank 则 `phase` 互异。
- [ ] **Task 1.3: SMT 内核调度器实现 (`modulo_backend.py`)**
  - 新增核心循环：`II` 从资源下界（如乘法节点数）开始递增，若 `checker() == sat` 立即返回并冻结调度，确保最高吞吐。

### 阶段二：内核切片与全队列同步 (Driver/Emitter Layer)
- [ ] **Task 2.1: 构建内核切片生成器 (`kernel_builder.py`)**
  - 读取 SMT 结果中的 `phase`，构建一个深度为 `II` 的 2D Array（行=时间槽，列=各 IQ）。
  - 处理 `DLY`：通过前序指令之间的空槽数直接填入指令级 `DLY`，废弃复杂的物理气泡探测。
- [ ] **Task 2.2: 协议层硬编码约束 (`loop_protocol_enforcer.py` 替代 `compactor.py`)**
  - 强制全 IQ 第一拍：插入 `[EMBED=L1, LOOPS=batch+slack]`。
  - 强制全 IQ 最终拍打标：将切片中最后一个物理有效指令自动追加 `EMBED_END=1`。
  - 处理**冗余排空**：将最先完成其工作的单元（如 UR_READ）的 LOOPS 或结尾状态通过 Padding 补齐。
- [ ] **Task 2.3: 精确地址增量映射**
  - 保留 `isa_v5_loop_spec.md` 中的增量公式，保证 Tensor 每次进入新的循环迭代时 `INC` 正确累加。

### 阶段三：Orchestrator 与 Verifier 升级
- [ ] **Task 3.1: 模调度全生命周期验证 (`verifier.py`)**
  - 添加 `Setup` 单拍周期的损耗校验：`Total_Cycles = (1 Setup) + LOOPS * II`。
  - 引入内核稳态横截面检查，确保 `FPMUL`、`FPADD` 等重型计算槽位在 `T > Startup` 时刻的活跃度为 100%。

---

## 3. 切入策略 (Step-by-Step)
为了保证迭代稳定性并快速反馈：
1. **优先开刀点**：在 `src/synthesis` 创建完全并行的 `modulo_backend.py` 试验田。
2. **测试驱动**：采用一个干净的 3 阶 Horner 多项式计算作为黄金测试例（`test_modulo_compiler.py`）。
3. **废弃债务**：一旦跑通第一条，我们将全面清理 `compactor.py` (V1 最大的维护包袱)。
