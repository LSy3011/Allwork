# AIACC V5.0 架构基线 (ADM 专属 - 冻结文档)

> 本文档是从 800 行设计文档中提炼的核心精华。一旦冻结，内容不再修改。
> 所有后续架构变更通过 `adr/ADR-NNN.md` 增量叠加。

## 1. 编译流水线 (11-Pass Pipeline)
HIR → AddrColoring → SMTSynthesis → HSTSStitching → BinaryEmission

## 2. 硬件物理底座
- 8-Bank UR 存储
- 4R/4W 并发总线
- Rtovr 非全连接路由 (详见 ADR-001)
- 数据源: `hw_config/hw_params.yaml`

## 3. SMT 六大公理
1. RAW 延时依赖
2. Bank R-R 冲突
3. Bank W-W 冲突
4. UR 带宽并发
5. Rtovr 路由通断 (ADR-001)
6. Rtovr 端口互斥 (ADR-001)

## 4. HSTS 相位缝合
- 四维足迹掩码: (Cycle, Bank, Port, Unit)
- 相位滑动消解冲突
- DLY 气泡填充 (bits_dly=4, 最大 15)

## 5. 算子分类
- Atomic: sin, exp, log (SMT 全量合成)
- Composite: atan2, gelu (HSTS 缝合)
- Algebraic: add, mul, relu (启发式)

---
*基线版本: V5.0 | 冻结日期: 待定 | 后续变更: 见 adr/ 目录*
