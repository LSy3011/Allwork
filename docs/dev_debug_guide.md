# AIACC 编译器开发与 Debug 标准流程 (V2.0)

> 本文档是 AIACC 编译器团队的 **权威开发手册**。  
> 覆盖：算子开发全流程、Bug 定位标准方法、工具链使用指南、目录规约。

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
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘    └────────┘
│       │              │              │              │              │
│       ▼              ▼              ▼              ▼              ▼
│  stage_a_       stage_c_       stage_b_       acb_bundle/     sim.log
│  initial.json   synthesis.json mapped.json    *.hex
│                                               sim_config.json
└──────────────────────────────────────────────────────────────────────┘
```

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
│       ├── debug_pipeline.py     #     一键 5 阶段 Bug 定位
│       ├── cycle_verifier.py     #     逐周期仿真对比
│       ├── hex_disassembler.py   #     Hex → 伪指令反汇编
│       ├── standard_pipeline_runner.py  # 标准编译流水线
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
| **测试脚本** | `tests/operators/{category}/{op_name}/test_{op_name}.py` |
| **Build 产物** | `build/operators/{category}/{op_name}/` |
| **Debug 命令** | `debug_pipeline.py --op {op_name} -c {category}` |
| **回归注册** | `tests/regression/registry.json` → status: `ready` |

**示例：新增 cos 算子**
```
tests/operators/unary/transcendentals/cos/test_cos.py   ← 创建测试
build/operators/unary/transcendentals/cos/              ← 自动生成
registry.json → "cos": {"status": "ready"}              ← 标记就绪
```

---

## 3. 算子开发标准流程

### 3.1 开发步骤

```
Step 1: 创建测试脚本
         │
         ▼
Step 2: 实现前端 DFG 构建
         │
         ▼
Step 3: 运行编译 + 仿真
         │
         ▼
Step 4: 用 debug_pipeline 验证 5 阶段
         │
    ┌────┴────┐
    │ PASS    │ FAIL → 进入 Bug 定位流程 (§4)
    ▼         │
Step 5: 标记 registry.json status=ready
         │
         ▼
Step 6: 运行回归门禁确认无回归
```

### 3.2 Step 1 — 创建测试脚本

在约定路径创建测试文件：

```python
# tests/operators/unary/transcendentals/cos/test_cos.py
import sys
import numpy as np
from pathlib import Path
import sympy as sp

ws = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ws / "src"))

from tools.standard_pipeline_runner import StandardPipelineRunner
from frontend import GraphCompiler


def get_frontend_dfg():
    compiler = GraphCompiler()
    x = sp.Symbol('x')
    # cos(x) ≈ 1 - x²/2 + x⁴/24 - x⁶/720
    formula = 1.0 - x**2 * (0.5 - x**2 * (0.0416666667 - x**2 * 0.001388889))
    inputs_meta = {x: {"manual_bank": 2, "role": "operand_a"}}
    return compiler.compile(inputs_meta, formula, out_name="y")


def test_cos():
    runner = StandardPipelineRunner(
        hw_config="hw_config",
        build_root="build/operators/unary/transcendentals"  # ← 约定路径
    )
    sys.path.insert(0, str(ws / "tests" / "operators"))
    from utils.data_generator import ArithTestDataGenerator

    gen_data = ArithTestDataGenerator.generate("cos", mode="fuzz", size=4)
    test_x = gen_data["ch_0"]

    success = runner.run_full_pipeline(
        op_name="cos",          # ← Build 子目录名
        get_dfg_fn=get_frontend_dfg,
        input_data={"x": test_x},
        golden_y=np.cos(test_x)
    )
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    test_cos()
```

### 3.3 Step 2 — 运行编译

```bash
python3 tests/operators/unary/transcendentals/cos/test_cos.py
```

### 3.4 Step 3 — 一键验证

```bash
python3 src/tools/debug_pipeline.py --op cos -c unary/transcendentals
```

### 3.5 Step 4 — 注册到回归

编辑 `tests/regression/registry.json`：
```json
"cos": {"description": "cos(x) 泰勒展开", "status": "ready"}
```

### 3.6 Step 5 — 确认无回归

```bash
python3 tests/regression/run_regression.py
```

---

## 4. Bug 定位标准方法

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
**根因：** `dep.op_type.lower()` 查 YAML key (如 `"FPMUL"`)，大小写不匹配  
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
