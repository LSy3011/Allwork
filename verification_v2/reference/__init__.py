"""ADR-056 §C：V1 独立参考端包（实现步②产物）。

独立性条款（§C.1/§C.3，本包对自身硬约束）
------------------------------------------
* **禁止** import `src/` 下任何编译器 lowering / 发射器内部，禁止 import simulator：
  参考端只依赖 Python 标准库 + numpy（《逐步 fp32 求值》执行器）。
* golden 的正确性定义 = 有序语法树的逐步 fp32 求值（§C.2），**不以 simulator 产物
  定义正确性**；sim 终态仅作为锚定对照面（§C.3），经 add 三规模逐位锚定后发生器
  方可用于新公式。
* 语法合同 = ADR-056 §A.2-1 原文条款（Python `ast` 文本级工序，SymPy 之前）。
"""
"""
模块：
    ordered_ast           §A.2-1 有序语法树（保序、取舍、SYNTAX_UNSUPPORTED 诊断）
    reference_generator   §C.2 逐步 fp32 参考发生器 + §C.4 双道数值域检查
"""
