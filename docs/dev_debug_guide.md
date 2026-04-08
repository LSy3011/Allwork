# AIACC 编译器开发与 Debug 标准流程 (V4.0 - ADPC 契约版)

> 本文档是 AIACC 编译器团队的 **权威开发手册**。  
> 覆盖：V4.0 算子工厂开发流、物理等价类分布式验证、Bug 定位标准方法、工具链使用指南。

---

## 目录

1. [编译器架构总览](#1-编译器架构总览)
2. [目录规约](#2-目录规约)
3. [算子开发标准流程](#3-算子开发标准流程)
4. [Bug 定位标准方法](#4-bug-定位标准方法)
5. [工具链速查表](#5-工具链速查表)
6. [已知陷阱与历史 Bug](#6-已知陷阱与历史-bug)
7. [回归门禁与 CI](#7-回归门禁与-ci)

---

## 1. 编译器架构总览

### 1.1 五阶段编译流水线

```
┌──────────────────────────────────────────────────────────────────────┐
│                    AIACC 编译流水线 (5 Stage)                         │
│                                                                      │
│  DSL 表达式                                                           │
│      │                                                               │
│      ▼                                                               │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌────────┐
│  │ Stage A │    │ Stage C │    │ Stage B │    │ Stage D │    │Stage E │
│  │ Frontend│───▶│  SMT    │───▶│ Physical│───▶│  LIR   │───▶│  Sim   │
│  │ DFG Gen │    │ 时序合成│    │ Bank/Addr│   │ Hex 发射│   │ 仿真验证│
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘
│       │              │              │              │              │
│       ▼              ▼              ▼              ▼              ▼
│  stage_a_       stage_c_       stage_b_       acb_bundle/     sim.log
│  initial.json   synthesis.json mapped.json    *.hex           (Layout 0..N)
│                                               layout_cfg.json
└──────────────────────────────────────────────────────────────────────┘
```

> **V4.0 核心变更**：引入 **Layout 隔离路径**。每个算子的不同 Bank 碰撞组合（Fuzzing Layout）将输出到独立的子目录中，确保物理冲突验证互不干扰。

### 1.2 每阶段输入/输出与责任模块

| 阶段 | 输入 | 输出 | 核心模块 | 验证项 |
|------|------|------|----------|--------|
| **A** | SymPy 表达式 | `stage_a_initial.json` | `src/frontend/compiler.py` | 拓扑连通性、数学正确性 |
| **C** | DFG 初图 | `stage_c_synthesis.json` | `src/synthesis/backend.py` + `axioms.py` | 延迟约束、ALU Liveness、Spill Reload 互斥 |
| **B** | 时序 DFG | `stage_b_mapped.json` | `src/orchestrator/colorer.py` + `memory_allocator.py` | Bank 同拍冲突、IQ ID |
| **D** | 物理 DFG | `acb_bundle/*.hex` | `src/driver/emitter.py` + `packer.py` | 文件完整性、常量对齐、指令可解码、max_cycles 自适应 |
| **E** | Hex + 输入数据 | `sim.log` | `sim/smc_simulator/` | S0/S1 输入对比、Golden 对比 |

### 1.3 数据流与关键中间产物

```
stage_a_initial.json    — DFG 节点列表 (trace_id, op_type, metadata)
                          无物理信息，仅逻辑拓扑

stage_c_synthesis.json  — 附加 logic_cycle (SMT 求解的执行周期)
                          无 bank/addr，仅时序

stage_b_mapped.json     — 附加 bank_id, addr, iq_id
                          全量物理信息，可直接驱动 Hex 发射

acb_bundle/
  smc_0/
    iq_fpmul.hex        — FPMUL 指令队列 (NOP + VALID 交替)
    iq_fpadd.hex        — FPADD 指令队列
    iq_ur_r0.hex        — UR-R0 读端口指令
    iq_ur_w0.hex        — UR-W0 写端口指令
    iq_rtovr_3.hex      — RTOVR 路由端口 3
    ur_data/
      bank0.hex         — SRAM Bank 0 初始化数据 (常量+输入)
      bank1.hex ~ bank7.hex
  sim_config.json       — 仿真配置 (IQ映射、Bank映射、max_cycles)
  golden_data.npz       — 输入数据 + Golden 输出

sim.log                 — 仿真器逐周期执行日志
timeline_ascii.txt      — 文本版流水线甘特图
```

---

## 2. 目录规约

### 2.1 工程目录结构

```
aiacc_compiler/
├── src/                          # 编译器源码
│   ├── frontend/                 #   Stage A: 前端 DSL → DFG
│   ├── synthesis/                #   Stage C: SMT 时序求解
│   ├── orchestrator/             #   Stage B: 物理资源分配
│   ├── driver/                   #   Stage D: LIR Hex 发射
│   ├── api/                      #   硬件规格加载
│   ├── ir/                       #   中间表示定义
│   └── tools/                    #   ★ 开发/Debug 工具链
│       ├── dfg_evaluator.py      #     DFG 数学求值 (SSoT)
│       ├── debug_pipeline.py      #     一键 5 阶段 Bug 定位
│       ├── op_fuzz.py             #   ★ 单算子 Bank 碰撞压力测试 (CLI)
│       ├── pipeline_utils.py      #     物理布局隔离与 DFG 动态注入器
│       ├── hex_disassembler.py    #     Hex → 伪指令反汇编
│       ├── cycle_verifier.py      #     逐周期仿真对比
│       ├── standard_pipeline_runner.py  # 标准编译流水线 (V4.0)
│       └── pipeline_verifier.py  #     独立验证套件
│
├── sim/                          # 仿真器
│   └── smc_simulator/
│       ├── core/
│       │   ├── kernel.py         #   并行执行内核
│       │   └── decoder.py        #   指令解码器
│       └── units/
│           ├── fp_mul_unit.py    #   FPMUL 仿真单元
│           └── fp_add_unit.py    #   FPADD 仿真单元
│
├── tests/                        # 测试
│   ├── operators/                #   ★ 算子测试 (按类别组织)
│   │   ├── unary/transcendentals/sin/test_sin.py
│   │   ├── activation/relu/test_relu.py
│   │   ├── binary/math/add/test_add.py
│   │   └── utils/data_generator.py
│   ├── regression/               #   ★ 回归门禁
│   │   ├── run_regression.py
│   │   └── registry.json
│   ├── unit/                     #   单元测试
│   └── integration/              #   集成测试
│
├── build/                        # 编译输出 (镜像 tests 分类)
│   └── operators/
│       ├── unary/transcendentals/sin/
│       ├── activation/relu/
│       └── binary/math/add/
│
├── hw_config/                    # 硬件规格定义
└── docs/                         # 文档
    └── dev_debug_guide.md        # ← 本文件
```

### 2.2 路径约定 (Convention over Configuration)

| 维度 | 约定路径 |
|------|----------|
| **测试脚本 (旧)** | `tests/operators/{category}/{op_name}/test_{op_name}.py` |
| **算子工厂 (新)** | `tests/operators/utils/op_library.py` (录入公式即开发) |
| **Build 产物** | `build/operators/{category}/{op_name}/layout_{id}/` |
| **单算子 Fuzz** | `python3 tests/operators/op_fuzz.py {op_name}` |
| **一键验证** | `debug_pipeline.py --op {op_name} -c {category} --layout {id}` |

**示例：新增 cos 算子**
```
tests/operators/unary/transcendentals/cos/test_cos.py   ← 创建测试
build/operators/unary/transcendentals/cos/              ← 自动生成
registry.json → "cos": {"status": "ready"}              ← 标记就绪
```

---

## 3. 算子标准化开发流程 (V4.0 Factory Standard)

### 3.1 开发步骤 (录入即验证)
在新窗口或新算子开发时，彻底告别手动编写 `test_*.py` 脚本：

```
Step 1: 注册数学定义 
        └─ 修改 tests/operators/utils/op_library.py，填入公式与 Golden 函数

Step 2: 触发全物理覆盖压力测试 (Fuzzing)
        └─ 执行 python3 tests/operators/op_fuzz.py {op_name}

Step 3: 检查覆盖率报告
        └─ 确保所有 Layout 分支 (Collide/Separate) 及其精度均通过
```

### 3.2 路径隔离规约 (Path Isolation)
所有 Factory 测试产物将按 Layout ID 自动隔离，格式如下：
`build/operators/fuzz_diagnostics/{op_name}/layout_{N}/`

每一路 Layout 包含完整的 Stage A-E 产物，方便对特定物理碰撞场景进行回溯。

### 3.3 传统开发 (Legacy Support)
仅在需要开发极其复杂的 **Custom Kernel**（手动拼物理路由）时使用手动脚本，常规算子严禁新建 `test_*.py`。

---

## 4. Bug 定位标准方法 (Diagnostics)

### 4.1 定位决策树
运行 `op_fuzz.py` 失败后，根据首个报错 Layout 进入诊断流：

```
Layout N 失败
  │
  ▼
运行一键验证命令
python3 src/tools/debug_pipeline.py --op {op_name} -c fuzz_diagnostics --layout {N}
  │
  ├─ Stage A/C 失败 ──▶ 检查 DFG 拓扑 (Taylor 阶数是否导致爆炸)
  ├─ Stage B 失败 ────▶ 检查 Bank 碰撞 (Bank 0 是否过度拥堵)
  └─ Stage E 失败 ────▶ 检查数值精度 (64阶泰勒是否覆盖收敛域)
```

---

## 5. 工具链速查表 (Toolbox V4.0)

### 5.1 核心命令
| 功能 | 命令 | 适用场景 |
|------|------|----------|
| **全案压力测试** | `python3 tests/operators/op_fuzz.py {op}` | 算子准入必测 (All Banks) |
| **单路 Layout 诊断** | `debug_pipeline.py --op {op} -c fuzz_diag --layout {n}` | 物理冲突详细追踪 |
| **逐周期精度审计** | `cycle_verifier.py --dir build/.../layout_{n}` | 计算漂移排查 |
| **物理 Hex 反汇编** | `hex_disassembler.py --dir build/.../layout_{n} --compare` | LIR 编解码对齐 |
| **全量回归门禁** | `python3 tests/regression/run_regression.py` | 代码入库前置条件 |

### 4.1 定位决策树

```
测试失败
  │
  ▼
运行 debug_pipeline.py
  │
  ├─ Stage A 失败 ──────────────────▶ §4.2 前端 DFG 问题
  │  "拓扑断裂" / "常量值错误"
  │
  ├─ Stage C 失败 ──────────────────▶ §4.3 SMT 时序问题
  │  "延迟违反" / "Liveness 覆写"
  │
  ├─ Stage B 失败 ──────────────────▶ §4.4 物理分配问题
  │  "Bank 时间冲突" / "iq_id 缺失"
  │
  ├─ Stage D 失败 ──────────────────▶ §4.5 Hex 打包问题
  │  "常量对齐偏差" / "指令解码异常"
  │
  └─ Stage E 失败 ──────────────────▶ §4.6 仿真执行问题
     "S0/S1 偏差" / "UR_WRITE 不匹配"
```

### 4.2 Stage A 失败 — 前端 DFG 问题

**症状：** `拓扑断裂: Node X depends on missing node Y` 或 `常量值错误`

**排查：**
```bash
# 1. 查看 DFG 初图
cat build/operators/.../stage_a_initial.json | python3 -m json.tool | head -50

# 2. 检查前端编译器输出的节点
python3 -c "
import sys; sys.path.append('src')
from frontend import GraphCompiler
import sympy as sp
x = sp.Symbol('x')
formula = ...  # 你的公式
compiler = GraphCompiler()
dfg = compiler.compile({x: {'manual_bank': 2}}, formula, out_name='y')
for n in dfg:
    print(f'{n.trace_id:30s} {n.op_type:10s} deps={n.metadata.get(\"depends_on\", [])}')
"
```

**常见根因：**
- SymPy 表达式展开产生意外的中间节点
- `is_const` 标记遗漏导致常量被当作变量
- `depends_on` 列表中引用了不存在的 trace_id

**修复位置：** `src/frontend/compiler.py`

### 4.3 Stage C 失败 — SMT 时序问题

**症状：** `延迟违反: X(T=5) < Y(T=3) + latency(5)` 或 `Liveness 覆写`

**排查：**
```bash
# 1. 查看时序排布
cat build/operators/.../timeline_ascii.txt

# 2. 检查 SMT 约束是否正确
python3 -c "
import sys; sys.path.append('src')
from api.hw_spec import HardwareSpec
spec = HardwareSpec.load('hw_config')
print('Delays:', spec.delays)
"
```

**常见根因：**
- `axioms.py` 中遗漏了某类节点的延迟约束
- ALU DR 寄存器在前次结果 RTOVR 搬走前被新计算覆写
- 同 Spill 源的 Reload 在同拍发射导致 Bank 冲突 (需 `spill_reload_mutex` 公理)
- RTOVR 数据可见延迟 = T+1，RAW 约束与影子绑定需统一

**修复位置：** `src/synthesis/axioms.py` 或 `src/synthesis/backend.py`

### 4.4 Stage B 失败 — 物理分配问题

**症状：** `Bank 0 时间冲突: X(T=9) & Y(T=9)` (同拍冲突)

**排查：**
```bash
# 查看物理映射结果
python3 -c "
import json
with open('build/operators/.../stage_b_mapped.json') as f:
    for n in json.load(f):
        meta = n.get('metadata', {})
        if meta.get('bank_id') is not None:
            print(f'T={n[\"cycle\"]:3d} Bank={meta[\"bank_id\"]} Addr={meta.get(\"addr\")} {n[\"trace_id\"]}')
" | sort -k1 -t= -n
```

**常见根因：**
- `BalancedBankColorer` 着色冲突 (同 Bank 同拍)
- 同 Spill 源的多个 Reload 被漏保护 (需 `add_spill_reload_mutex_axiom`)
- `MemoryAllocator` 地址碰撞
- `iq_id` 被 pipeline_runner 覆盖 (参见 §6.1)

**修复位置：** `src/orchestrator/colorer.py` 或 `standard_pipeline_runner.py`

### 4.5 Stage D 失败 — Hex 打包问题

**症状：** `常量对齐偏差` 或 `指令解码异常`

**排查：**
```bash
# 1. 反汇编查看指令
python3 src/tools/hex_disassembler.py --dir build/operators/... --iq fpmul

# 2. 对比 DFG 时序
python3 src/tools/hex_disassembler.py --dir build/operators/... --compare

# 3. 验证编解码闭环
python3 tests/unit/test_instruction_codec.py
```

**常见根因：**
- `BitPacker` 位域偏移错误 (Header vs Payload 边界不对齐)
- `FieldMapper` 字段映射遗漏
- Hex 文件名与 `sim_config.json` 中的 IQ 名不一致

**修复位置：** `src/driver/emitter.py` 或 `src/driver/packer.py`

### 4.6 Stage E 失败 — 仿真执行问题

**症状：** `S0 偏差: 预期=0.608931 硬件=0.000000` 或 `UR_WRITE 不匹配`

**排查：**
```bash
# 1. 逐周期详细对比 (最强大的工具)
python3 src/tools/cycle_verifier.py --dir build/operators/...

# 2. 查看 sim.log 原始日志
grep -E "\[MUL-EXEC\]|\[ADD-EXEC\]|\[UR-WRITE\]" build/operators/.../sim.log

# 3. 逐拍审计 (使用模拟器内部解码)
python3 tests/integration/test_e2e_audit.py --case build/operators/.../acb_bundle
```

**常见根因：**
- 模拟器单元缺少 `[OP-EXEC]` 日志输出 (参见 §6.3)
- 常量 Bank 数据未正确初始化 (zeros)
- `eval_math` 中常量分支缺少 `return` (参见 §6.2)
- Hex Lane 0 取位错误 (最低 32bits vs 最高 32bits)

**修复位置：** `sim/smc_simulator/units/*.py` 或 `src/tools/cycle_verifier.py`

---

## 5. 工具链速查表

### 5.1 命令速查

```bash
# ═══════════════════════════════════════════════
#  编译
# ═══════════════════════════════════════════════
# 编译单个算子
python3 tests/operators/unary/transcendentals/sin/test_sin.py

# ═══════════════════════════════════════════════
#  一键 Debug (最常用)
# ═══════════════════════════════════════════════
# 验证已有编译产物
python3 src/tools/debug_pipeline.py --op sin -c unary/transcendentals

# 强制重编译后验证
python3 src/tools/debug_pipeline.py --op sin -c unary/transcendentals --recompile

# 直接指定目录 (兼容旧结构)
python3 src/tools/debug_pipeline.py --dir build/sin8/sin_taylor_high_order

# ═══════════════════════════════════════════════
#  深入分析
# ═══════════════════════════════════════════════
# 逐周期 S0/S1/Data 对比
python3 src/tools/cycle_verifier.py --dir build/operators/unary/transcendentals/sin

# Hex 反汇编 (全部 IQ)
python3 src/tools/hex_disassembler.py --dir build/operators/unary/transcendentals/sin

# Hex 反汇编 (指定 IQ + DFG 交叉参照)
python3 src/tools/hex_disassembler.py --dir ... --iq fpmul --compare

# ═══════════════════════════════════════════════
#  回归门禁
# ═══════════════════════════════════════════════
# 运行所有 ready 算子
python3 tests/regression/run_regression.py

# 按类别过滤
python3 tests/regression/run_regression.py -c unary

# 查看 74 算子全景
python3 tests/regression/run_regression.py --list --status all

# 只验证不编译
python3 tests/regression/run_regression.py --verify-only
```

### 5.2 工具职责矩阵

```
                    Stage A  Stage C  Stage B  Stage D  Stage E
                    (DFG)   (时序)   (Bank)   (Hex)    (仿真)
  ┌───────────────┬────────┬────────┬────────┬────────┬────────┐
  │ debug_pipeline│  ✅    │  ✅    │  ✅    │  ✅    │  ✅    │
  │ (一键定位)     │ 拓扑+  │延迟+   │Bank    │文件+   │S0/S1+  │
  │               │ 数学   │Liveness│冲突    │常量+   │Golden  │
  │               │        │        │        │反汇编  │        │
  ├───────────────┼────────┼────────┼────────┼────────┼────────┤
  │ cycle_verifier│  ✅    │        │  ✅    │        │  ✅    │
  │ (深入对比)     │eval_dfg│        │Bank Hex│        │逐周期  │
  │               │dump    │        │对齐    │        │S0/S1   │
  ├───────────────┼────────┼────────┼────────┼────────┼────────┤
  │ hex_disasm    │        │        │        │  ✅    │        │
  │ (反汇编)      │        │        │        │伪指令  │        │
  │               │        │        │        │DFG参照 │        │
  ├───────────────┼────────┼────────┼────────┼────────┼────────┤
  │ run_regression│  (编译 + debug_pipeline 的批量封装)        │
  │ (回归门禁)     │                                            │
  └───────────────┴────────────────────────────────────────────┘
```

### 5.3 输出解读

**debug_pipeline.py 输出示例 (有 Bug)：**
```
  ✅ [A: DFG 数学与拓扑] PASS
  ✅ [C: SMT 时序与 Liveness] PASS
  ❌ [B: Bank 分配与 IQ 完整性] FAIL (1 errors)
    → Bank 0 时间冲突: spill_x0(T=9) & x0_reload_8(T=10)

  🎯 Bug 定位: 首个失败阶段 = [B: Bank 分配与 IQ 完整性]
  💡 建议: 检查 src/tools/standard_pipeline_runner.py 的物理分配器
```

**cycle_verifier.py 输出示例 (有 Bug)：**
```
  ⏱  [T=  9] UR_WRITE (spill_x0)
       ├─ In  = 0.37079725921259765
       └─ Out = 0.37079725921259765
       ❌ Data: 预期=0.370797  硬件=0.000000
         日志: >>> [UR-WRITE] W0 Bank:0 Addr:0 Data:00000000000000000000000000000000
```

**hex_disassembler.py 输出示例：**
```
  ── IQ: fpmul (smc_0/iq_fpmul.hex, 18 lines, 9 active, 9 NOP) ──
    PC  Hex                    伪指令
    0   0x0000000030000000     NOP (LOOPS=3)
    1   0x0001000000000007     VALID VALID=VALID SRC_PREC=PREC_32 DST_PREC=PREC_32
```

---

## 6. 已知陷阱与历史 Bug

### 6.1 IQ ID 覆盖 Bug

**文件：** `src/tools/standard_pipeline_runner.py`  
**症状：** SMT 分配了正确的 `iq_id`，但物理分配器覆盖了它  
**根因：** 分配器无条件写入 `iq_id`，没有检查是否已存在  
**修复：** `if "iq_id" not in node.metadata: node.metadata["iq_id"] = ...`

### 6.2 eval_math 常量返回值丢失 Bug

**文件：** `src/tools/cycle_verifier.py` (已修复，迁移到 `dfg_evaluator.py`)  
**症状：** 所有常量节点求值结果为 `0.0`  
**根因：** `UR_READ` 常量分支缺少 `return val`，控制流落到函数末尾被覆盖为 `0.0`  
**教训：** 每个分支都必须显式 `val_map[nid] = val; return val`

### 6.3 FPADD 缺少仿真日志

**文件：** `sim/smc_simulator/units/fp_add_unit.py`  
**症状：** `cycle_verifier` 报告 "sim.log 无物理回执"  
**根因：** FPMUL 有 `[MUL-EXEC]` 日志但 FPADD 没有  
**修复：** 在 `evaluate()` 方法中添加 `print(f">>> [ADD-EXEC] Cyc:{cyc} S0:{s0_hex} S1:{s1_hex} R:{result_hex}")`

### 6.4 Hex Lane 取位方向

**约定：** Lane 0 = 128-bit 寄存器的 **最低 32 bits** = `hex_str[-8:]`  
**错误写法：** `hex_str[:8]` (取的是最高位 Lane 3)  
**影响：** 常量值对比全部错乱

### 6.5 Bank 冲突间隔过严 Bug (V2.0 修复)

**文件：** `src/synthesis/axioms.py` + `src/tools/standard_pipeline_runner.py`  
**症状：** 高阶 Taylor 展开 (如 atan2 order≥3) SMT 合成成功但 Bank 验证失败  
**根因：** Bank 冲突约束被设为 `|T_i - T_j| ≥ 2`，但硬件仅要求 `T_i ≠ T_j` (同拍互斥)  
**修复：** `axioms.py` 中 `bank_gap` 改为 `!=`，`runner.py` 验证间隔改为 `== 0`

### 6.6 Spill Reload 无 Bank 保护 Bug (V2.0 修复)

**文件：** `src/synthesis/axioms.py`  
**症状：** `x1_reload_24(T=38) & x1_reload_25(T=38)` Bank 冲突  
**根因：** `add_bank_conflict_axiom` 仅对 `manual_bank` 生效，Spill/Reload 节点无 `manual_bank`  
**修复：** 新增 `add_spill_reload_mutex_axiom`，对同 spill 源的 reload 强制 `T_i ≠ T_j`

### 6.7 Latency 查询大小写 Bug (V2.0 修复)

**文件：** `src/tools/standard_pipeline_runner.py`  
**症状：** FPMUL/FPADD 延迟被 fallback 到 default=1 (实际应为 4)  
**根因：** `dep.op_type.lower()` 查 YAML key (如 `"FPMUL"`)，大小写`不匹配`  
**修复：** 使用归一化 `{k.upper(): v}` 映射

### 6.8 RTOVR Transit 不一致 Bug (V2.0 修复)

**文件：** `src/synthesis/backend.py`  
**症状：** RAW 依赖给 RTOVR 的 transit=0，但影子绑定给了 >=T+1  
**根因：** RTOVR 数据可见延迟硬件确认为 T+1，transit 应为 1  
**修复：** 统一 RTOVR transit 为 1

### 6.9 Emitter max_cycles 硬编码 Bug (V2.0 修复)

**文件：** `src/driver/emitter.py`  
**症状：** Order 7 排布需要 124 拍但 `DEFAULT_MAX_CYCLES=120`  
**修复：** `max_cycles = max(DEFAULT, actual_max_t + 10)` 自动适配

### 6.10 Newton-Raphson vs Taylor 复杂度陷阱
- **对比**：NR 是自回归结构（$y_n$ 依赖两个上级节点），Taylor 是线性倍增结构。
- **后果**：NR 在多输入 Bank 冲突下极易触发 `SMT UNSAT`，即使节点数只有 50。
- **准则**：优先选用 **64 阶倍增泰勒 (Binary Taylor-64)**，它在 AIACC 架构上更易通过物理排布。

### 6.11 编译器状态泄露 (State Leak)
- **发现**：在多轮编译循环中，`GraphCompiler` 如果不重置 `MIROpBuilder`，节点会呈等差数列增长。
- **修复**：`GraphCompiler.compile()` 内部必须强制调用 `self.b = MIROpBuilder()` 重开环境。

### 6.12 泰勒展开收敛域边界问题
- **场景**：`reciprocal(u)` 展开点为 1，收敛域为 (0, 2)。
- **Bug**：在 `softsign(1.0)` 处分母刚好为 2，泰勒发散。
- **修复**：将展开阶数提升至 64 阶，并在 $x=2$ 附近通过偏移展开点或使用更高精度的倍增公式。

### 6.2 eval_math 常量返回值丢失 Bug

**文件：** `src/tools/cycle_verifier.py` (已修复，迁移到 `dfg_evaluator.py`)  
**症状：** 所有常量节点求值结果为 `0.0`  
**根因：** `UR_READ` 常量分支缺少 `return val`，控制流落到函数末尾被覆盖为 `0.0`  
**教训：** 每个分支都必须显式 `val_map[nid] = val; return val`

### 6.3 FPADD 缺少仿真日志

**文件：** `sim/smc_simulator/units/fp_add_unit.py`  
**症状：** `cycle_verifier` 报告 "sim.log 无物理回执"  
**根因：** FPMUL 有 `[MUL-EXEC]` 日志但 FPADD 没有  
**修复：** 在 `evaluate()` 方法中添加 `print(f">>> [ADD-EXEC] Cyc:{cyc} S0:{s0_hex} S1:{s1_hex} R:{result_hex}")`

### 6.4 Hex Lane 取位方向

**约定：** Lane 0 = 128-bit 寄存器的 **最低 32 bits** = `hex_str[-8:]`  
**错误写法：** `hex_str[:8]` (取的是最高位 Lane 3)  
**影响：** 常量值对比全部错乱

### 6.5 Bank 冲突间隔过严 Bug (V2.0 修复)

**文件：** `src/synthesis/axioms.py` + `src/tools/standard_pipeline_runner.py`  
**症状：** 高阶 Taylor 展开 (如 atan2 order≥3) SMT 合成成功但 Bank 验证失败  
**根因：** Bank 冲突约束被设为 `|T_i - T_j| ≥ 2`，但硬件仅要求 `T_i ≠ T_j` (同拍互斥)  
**修复：** `axioms.py` 中 `bank_gap` 改为 `!=`，`runner.py` 验证间隔改为 `== 0`

### 6.6 Spill Reload 无 Bank 保护 Bug (V2.0 修复)

**文件：** `src/synthesis/axioms.py`  
**症状：** `x1_reload_24(T=38) & x1_reload_25(T=38)` Bank 冲突  
**根因：** `add_bank_conflict_axiom` 仅对 `manual_bank` 生效，Spill/Reload 节点无 `manual_bank`  
**修复：** 新增 `add_spill_reload_mutex_axiom`，对同 spill 源的 reload 强制 `T_i ≠ T_j`

### 6.7 Latency 查询大小写 Bug (V2.0 修复)

**文件：** `src/tools/standard_pipeline_runner.py`  
**症状：** FPMUL/FPADD 延迟被 fallback 到 default=1 (实际应为 4)  
**根因：** `dep.op_type.lower()` 查 YAML key (如 `"FPMUL"`)，大小写不匹配  
**修复：** 使用归一化 `{k.upper(): v}` 映射

### 6.8 RTOVR Transit 不一致 Bug (V2.0 修复)

**文件：** `src/synthesis/backend.py`  
**症状：** RAW 依赖给 RTOVR 的 transit=0，但影子绑定给了 >=T+1  
**根因：** RTOVR 数据可见延迟硬件确认为 T+1，transit 应为 1  
**修复：** 统一 RTOVR transit 为 1

### 6.9 Emitter max_cycles 硬编码 Bug (V2.0 修复)
### 6.13 Stage B/C 逻辑倒置风险 (V4.1 同步)
- **背景**：早期代码曾尝试将物理分配（B）前置于时序合成（C），导致 SMT 求解难度剧增。
- **现行标准**：**C (时序) -> B (物理)**。代码注释已于 2026-04-08 全面校准，严禁反向调用。

### 6.14 常量池冗余节点溢出 (V4.1 优化)
- **原因**：重复调用 `_get_const()` 会产生大量相同的 `UR_READ` 节点，撑爆 SMT 搜索空间。
- **修复**：后端已引入 `const_pool` 缓存，所有数值相同的常量在 DFG 层强制合并为单一节点。

---

## 7. 回归门禁与 CI

### 7.1 算子注册表

`tests/regression/registry.json` 管理所有 74 个算子的状态：

| 状态 | 含义 | 数量 |
|------|------|------|
| 🟢 `ready` | 已实现并通过验证 | 回归时执行 |
| ⚪ `pending` | 待实现 | 回归时跳过 |
| 🔴 `broken` | 已知失败 | 回归时报警但不阻断 |

### 7.2 新算子上线 Checklist

```
□ 创建 tests/operators/{category}/{op}/test_{op}.py
□ 编译通过: python3 tests/operators/.../test_{op}.py
□ 5 阶段验证通过: python3 src/tools/debug_pipeline.py --op {op} -c {category}
□ 更新 registry.json: status → "ready"
□ 回归门禁通过: python3 tests/regression/run_regression.py
□ 提交 PR
```

### 7.3 CI 集成命令

```bash
# CI 门禁 (任一失败则 exit 1)
python3 tests/regression/run_regression.py

# 生成报告文件
cat tests/regression/last_report.json
```

---

## 附录 A: 公共求值模块 API

`src/tools/dfg_evaluator.py` 是唯一的 DFG 数学求值引擎 (Single Source of Truth)。

```python
from tools.dfg_evaluator import eval_dfg, parse_deps, verify_dfg_topology

# 从 DFG 顺推数学值
val_map = eval_dfg(dfg_nodes, input_data={"x": 1.5668})
# val_map: {"x_read_1": 1.5668, "x0_pw1": 2.4548..., ...}

# 解析依赖列表 (兼容 list 和 JSON string)
deps = parse_deps(node_metadata)

# 检查拓扑完整性
errors = verify_dfg_topology(dfg_nodes)
```

支持两种输入格式：
- 编译时对象（有 `.trace_id`, `.op_type`, `.metadata` 属性）
- JSON 反序列化后的 dict 列表

---

## 8. 未来优化项 (Future Optimizations)

### 8.1 SMT 子图分解求解 (Sub-graph Decomposition)
- **背景**：高阶算子（如 atan2 Order 7+）节点数多达 80+，全局 `z3.Optimize` 耗时可能超过 300s，严重影响迭代效率。
- **方案**：在 DFG 层进行子图切割（如按 Horner 嵌套分级），分别求解 SMT 时序后进行“缝合”。这能将指数级复杂度降低为线性累加复杂度。

### 8.2 SMT 混合寻优策略 (Hybrid Strategy)
- **背景**：`Solver` 产生的调度过于稀疏，`Optimize` 产生的调度过于紧凑。
- **方案**：引入 `slack` 约束。先用 `Solver` 找到可行解，在此基础上添加基准周期约束（如 `max_t < baseline + 5`），再利用本地启发式规则（如尽量靠后排列）来平衡编译速度与硬件鲁棒性。

### 8.3 验证工具链核心库统一 (Unified Verifier Core)
- **背景**：曾经出现 `runner.py`（1-cycle gap）与 `debug_pipeline.py`（2-cycle gap）在 Bank 判定逻辑上不一致的情况。
- **方案**：将所有物理约束逻辑（Bank、Liveness、Transit）抽取到 `src/tools/verifier_core.py`，实现“判定标准一份代码，两库共享”。

### 8.4 物理分配热修复 (Bank Micro-swap)
- **背景**：当前 Stage B 发现冲突直接报错。
- **方案**：实现微量重分配。如果发现 Bank 同拍冲突，在不改变 `logic_cycle` 的前提下，尝试微调局部节点的 Bank 映射或 Addr 偏移。

---

## 9. V2.1 案例研究：atan2 覆盖性测试修复 (2026-04-07)

| 问题类型 | 根本原因 | 修复手段 |
| :--- | :--- | :--- |
| **SMT UNSAT** | 多操作数 RTOVR 强制 $T+1$ 对立导致死锁 | 松绑为 (S0 == 1, S1 >= 1) 协议，允许错峰搬运 |
| **Stage B Fail** | `debug_pipeline.py` 硬编码了 2 拍 Bank 间隔 | 修正为 1 拍，对齐硬件能力与 SMT 公理 |
| **Stage E Fail** | `Solver` 模式调度由于过于稀疏导致数据在仿真中“失效” | 回归 `Optimize` 模式，确保数据“即产即用”的紧凑性 |
| **Timeout Issue** | Order 7 泰勒展开节点数爆炸 | 缩减为 Order 5 (5 阶)，实现精度与编译速度的最优平衡 |
