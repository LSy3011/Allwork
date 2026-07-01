# SFU \(Special Function Unit\) 函数使用详解文档

## 指数与对数函数 \(Exponential \& Logarithmic Functions\)

## 三角函数 \(Trigonometric Functions\)

## 反三角函数 \(Inverse Trigonometric Functions\)

## 双曲与反双曲函数 \(Hyperbolic \& Inverse Hyperbolic\)

## 误差函数、倒数与激活函数

## 关键总结 \(Key Summary\)

### SFU 指令类型

- **`SFU_TAYLOR_4X`**: Horner 法则多项式求值 \(最常用\)

- **`SFU_RSQ`**: 倒数平方根硬件指令 \(一步求解\)

### Taylor 系数长度规则

*注: BM1684x 仅支持 FP32 的 ERF*

### 展开位置模式 \(Expansion Position Modes\)

1. **Exp**: $rem = src \- \\lfloor src/\\ln\(2\) \\rfloor \\times \\ln\(2\) \\rightarrow$ 在 `rem` 上展开

2. **Log**: 分子分母法则 \+ 范围归约 $\\rightarrow$ 在 `rem` 上展开

3. **Sin/Cos**: 周期化 $t = src \- \\text\{round\}\(src/2\\pi\) \\times 2\\pi$，然后在 $t^2$ 或 $t$ 上展开

4. **反三角**: 恒等式转换或象限分段 $\\rightarrow$ 在变换后的值上展开

5. **ERF**: 有理逼近 $w = 1/\(0\.5 \\times \|x\| \+ 1\) \\rightarrow$ 在 `w` 上展开

