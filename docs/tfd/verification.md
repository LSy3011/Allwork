# 九大验证支柱 (TFD 专属)

## SMT 验证 (5 支柱)
1. **SMTOutputVerifier**: 独立于 Z3 的公理回溯验证
2. **Optimality Bracketing**: N-1 UNSAT 夹逼最优性
3. **Brute Force Oracle**: 小案例穷举对比
4. **Stress UNSAT**: 强制 Bank/Port 压力测试
5. **Golden APB**: 拍数回归基准线

## HSTS 验证 (4 支柱)
6. **StreamScanner**: 全量 LIR 物理扫描(R-R/W-W/Port 冲突)
7. **DLY Exact**: 气泡值精确断言
8. **Propagation**: R-R 延迟下游传播验证
9. **Sim Parity**: 仿真器 Bit-Perfect 对比

## 调试决策树
```
仿真结果不对
├→ StreamScanner 有报告？ YES → 支柱 6/7/8
└→ NO → SMTOutputVerifier 有报告？ YES → 支柱 1
        └→ NO → Golden 拍数变了？ YES → 支柱 2/5
                └→ NO → BinaryEmitter 打包出错
```
