# AIACC V5.0 开发路线图 (ADM 专属)

> 完整版见 `smi_iq/aiacc_refactor/docs/v5_dev_roadmap.md`
> 本文件为精简速查版，按阶段列出里程碑和验收命令。

## Phase 1: 硬件契约层 (1-2天)
- **M01**: `HardwareSpec.load()` 五源解析
- **M02**: Pydantic 校验 + connectivity 合法性

## Phase 2: 三层 IR (2-3天)
- **M03**: `IRNode` 基类 (trace_id 贯穿)
- **M04**: `HIRProgram` 算子图
- **M05**: `MIROp` + `FootprintMask`
- **M06**: `LIROp` 41-bit 编解码

## Phase 3: 合成引擎 (3-5天) ★核心
- **M07**: Z3 环境验证
- **M08**: 六大物理公理 (含 Rtovr, ADR-001)
- **M09**: Z3 求解驱动 + UNSAT 诊断
- **M10**: MIR→Z3 自动翻译
- **M11**: APB 缓存 (hw_spec hash 驱动)

## Phase 4: 编排层 (3-4天)
- **M12**: FootprintMask (Bank+Port+Unit 掩码)
- **M13**: AdvancedResTable 资源表
- **M14**: Stitcher 相位缝合 + Port 冲突消解
- **M15**: 全 Bank 压力测试

## Phase 5: 空间与驱动 (2-3天)
- **M16**: 生命周期分析与 Slot 冲突审计 (HSTSVerifier)
- **M17**: 8-Bank 着色分配与物理寻址 (BalancedBankColorer)
- **M18**: 多维循环拓扑折叠与 INC 注入 (Compactor v2)
- **M19**: 41-bit 物理预算平衡微码封装 (BitPacker v5)
- **M19B**: NOP-Loop 极致空隙压缩 (Sprint 5 Final)

## Phase 6: 集成 (5-7天)
- **M20-M21**: 原子/组合算子模板
- **M22**: PassManager 集成
- **M23-M24**: 审计快照 + 不变式守卫
- **M25**: sin/atan2 E2E bit-perfect ★最终Boss
