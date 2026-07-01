# LLVM Toy语言

# Ch1\-\-Toy语言和抽象语法树AST

运行代码：

```Bash
~/llvm-project/build$ for test in $(find ~/llvm-project/mlir/test/Examples/Toy/Ch1 -name "*.toy"); do
>   echo "===== Testing $test ====="
>   ~/llvm-project/build/bin/toyc-ch1 $test -emit=ast
>   echo
> done
```

运行结果：

```Bash
===== Testing /home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy =====
  Module:
    Function
      Proto 'multiply_transpose' @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:4:1
      Params: [a, b]
      Block {
        Return
          BinOp: * @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:5:25
            Call 'transpose' [ @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:5:10
              var: a @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:5:20
            ]
            Call 'transpose' [ @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:5:25
              var: b @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:5:35
            ]
      } // Block
    Function
      Proto 'main' @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:8:1
      Params: []
      Block {
        VarDecl a<> @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:11:3
          Literal: <2, 3>[ <3>[ 1.000000e+00, 2.000000e+00, 3.000000e+00], <3>[ 4.000000e+00, 5.000000e+00, 6.000000e+00]] @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:11:11
        VarDecl b<2, 3> @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:15:3
          Literal: <6>[ 1.000000e+00, 2.000000e+00, 3.000000e+00, 4.000000e+00, 5.000000e+00, 6.000000e+00] @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:15:17
        VarDecl c<> @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:19:3
          Call 'multiply_transpose' [ @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:19:11
            var: a @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:19:30
            var: b @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:19:33
          ]
        VarDecl d<> @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:22:3
          Call 'multiply_transpose' [ @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:22:11
            var: b @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:22:30
            var: a @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:22:33
          ]
        VarDecl e<> @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:25:3
          Call 'multiply_transpose' [ @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:25:11
            var: c @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:25:30
            var: d @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:25:33
          ]
        VarDecl f<> @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:28:3
          Call 'multiply_transpose' [ @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:28:11
            var: a @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:28:30
            var: c @/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/ast.toy:28:33
          ]
      } // Block

===== Testing /home/fhj/llvm-project/mlir/test/Examples/Toy/Ch1/empty.toy =====
Parse error (4, 0): expected 'def' in prototype but has Token -1
  Module:

```

关于运行结果中的 @/home/fhj/llvm\-project/mlir/test/Examples/Toy/Ch1/ast\.toy:xx:xx的解释，其中xx：xx所表达的含义是行号：起始的字符在该行中的位置（需要包括空格，空格也算一个字符），换言之，就是这个字符是这行的第几个字符。用结果中的Proto 'multiply\_transpose' @/home/fhj/llvm\-project/mlir/test/Examples/Toy/Ch1/ast\.toy:4:1举个例子，以下是部分ast\.toy文件：

```Python
# RUN: toyc-ch1 %s -emit=ast 2>&1 | FileCheck %s

# User defined generic function that operates on unknown shaped arguments.
def multiply_transpose(a, b) {
  return transpose(a) * transpose(b);
}

def main() {
  # Define a variable `a` with shape <2, 3>, initialized with the literal value.
  # The shape is inferred from the supplied literal.
  var a = [[1, 2, 3], [4, 5, 6]];
  # b is identical to a, the literal array is implicitly reshaped: defining new
  # variables is the way to reshape arrays (element count in literal must match
  # the size of specified shape).
  var b<2, 3> = [1, 2, 3, 4, 5, 6];

  # This call will specialize `multiply_transpose` with <2, 3> for both
  # arguments and deduce a return type of <3, 2> in initialization of `c`.
  var c = multiply_transpose(a, b);
  # A second call to `multiply_transpose` with <2, 3> for both arguments will
  # reuse the previously specialized and inferred version and return `<3, 2>`
  var d = multiply_transpose(b, a);
  # A new call with `<3, 2>` for both dimension will trigger another
  # specialization of `multiply_transpose`.
  var e = multiply_transpose(c, d);
  # Finally, calling into `multiply_transpose` with incompatible shapes
  # (<2, 3> and <3, 2>) will trigger a shape inference error.
  var f = multiply_transpose(a, c);
}
```

因为`Proto`节点记录的是整个函数原型的声明位置，而原型以`def`关键字开头，因此标注从`def`的起始位置开始，即第四行第一个字符。

# Ch2\-\-基本的MLIR表示

将toy语言生成初始mlir并保存的运行代码：

```Plain Text
~/llvm-project/build/bin/toyc-ch2   ~/llvm-project/mlir/test/Examples/Toy/Ch2/codegen.toy   -emit=mlir   -mlir-print-debuginfo   2> original.mlir
```

将生成的 MLIR 重新输入编译器，这里叫做RoundTrip（往返转换）验证，是一种重要的测试手段，其核心目的是确保编译过程的可逆性和语义一致性。:

```Plain Text
~/llvm-project/build$ ~/llvm-project/build/bin/toyc-ch2   original.mlir   -emit=mlir -mlir-print-debuginfo  2> roundtrip.mlir
diff original.mlir roundtrip.mlir
```

运行结果：因为两个\.mlir文件是一样的，因此运用diff输出的结果为空。

接着我们可以使用mlir\-tblgen研究生成的 C\+\+ 代码，运行代码如下：

```JavaScript
*# 生成方言声明*
~/llvm-project/build/bin/mlir-tblgen \
  -gen-dialect-decls \
  ~/llvm-project/mlir/examples/toy/Ch2/include/toy/Ops.td \
  -I ~/llvm-project/mlir/include/

*# 生成操作定义*
~/llvm-project/build/bin/mlir-tblgen \
  -gen-op-defs \
  ~/llvm-project/mlir/examples/toy/Ch2/include/toy/Ops.td \
  -I ~/llvm-project/mlir/include/
```

# Ch3\-\-高级语言转换到MLIR

本章中主要介绍了两种实现模式匹配转换的方式，一种是命令式，C\+\+模式匹配和重写；另一种是声明性的，基于规则的模型匹配和重写。以下的指令主要展示了优化前后的MLIR。

运行代码：

```Shell
~/llvm-project/build$ for test in $(find ~/llvm-project/mlir/test/Examples/Toy/Ch3 -name "*.toy"); do
>   echo "===== Original MLIR ($test) ====="
>   ~/llvm-project/build/bin/toyc-ch3 $test -emit=mlir
>   echo
>   echo "===== Optimized MLIR ($test) ====="
>   ~/llvm-project/build/bin/toyc-ch3 $test -emit=mlir -opt
>   echo
> done

```

运行结果：

```Java
===== Original MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/transpose_transpose.toy) =====
module {
  toy.func @transpose_transpose(%arg0: tensor<*xf64>) -> tensor<*xf64> {
    %0 = toy.transpose(%arg0 : tensor<*xf64>) to tensor<*xf64>
    %1 = toy.transpose(%0 : tensor<*xf64>) to tensor<*xf64>
    toy.return %1 : tensor<*xf64>
  }
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %1 = toy.reshape(%0 : tensor<2x3xf64>) to tensor<2x3xf64>
    %2 = toy.generic_call @transpose_transpose(%1) : (tensor<2x3xf64>) -> tensor<*xf64>
    toy.print %2 : tensor<*xf64>
    toy.return
  }
}

===== Optimized MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/transpose_transpose.toy) =====
module {
  toy.func @transpose_transpose(%arg0: tensor<*xf64>) -> tensor<*xf64> {
    toy.return %arg0 : tensor<*xf64>
  }
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %1 = toy.generic_call @transpose_transpose(%0) : (tensor<2x3xf64>) -> tensor<*xf64>
    toy.print %1 : tensor<*xf64>
    toy.return
  }
}

===== Original MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/codegen.toy) =====
module {
  toy.func @multiply_transpose(%arg0: tensor<*xf64>, %arg1: tensor<*xf64>) -> tensor<*xf64> {
    %0 = toy.transpose(%arg0 : tensor<*xf64>) to tensor<*xf64>
    %1 = toy.transpose(%arg1 : tensor<*xf64>) to tensor<*xf64>
    %2 = toy.mul %0, %1 : tensor<*xf64>
    toy.return %2 : tensor<*xf64>
  }
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %1 = toy.reshape(%0 : tensor<2x3xf64>) to tensor<2x3xf64>
    %2 = toy.constant dense<[1.000000e+00, 2.000000e+00, 3.000000e+00, 4.000000e+00, 5.000000e+00, 6.000000e+00]> : tensor<6xf64>
    %3 = toy.reshape(%2 : tensor<6xf64>) to tensor<2x3xf64>
    %4 = toy.generic_call @multiply_transpose(%1, %3) : (tensor<2x3xf64>, tensor<2x3xf64>) -> tensor<*xf64>
    %5 = toy.generic_call @multiply_transpose(%3, %1) : (tensor<2x3xf64>, tensor<2x3xf64>) -> tensor<*xf64>
    toy.print %5 : tensor<*xf64>
    toy.return
  }
}

===== Optimized MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/codegen.toy) =====
module {
  toy.func @multiply_transpose(%arg0: tensor<*xf64>, %arg1: tensor<*xf64>) -> tensor<*xf64> {
    %0 = toy.transpose(%arg0 : tensor<*xf64>) to tensor<*xf64>
    %1 = toy.transpose(%arg1 : tensor<*xf64>) to tensor<*xf64>
    %2 = toy.mul %0, %1 : tensor<*xf64>
    toy.return %2 : tensor<*xf64>
  }
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %1 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %2 = toy.generic_call @multiply_transpose(%0, %1) : (tensor<2x3xf64>, tensor<2x3xf64>) -> tensor<*xf64>
    %3 = toy.generic_call @multiply_transpose(%1, %0) : (tensor<2x3xf64>, tensor<2x3xf64>) -> tensor<*xf64>
    toy.print %3 : tensor<*xf64>
    toy.return
  }
}

===== Original MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/ast.toy) =====
module {
  toy.func @multiply_transpose(%arg0: tensor<*xf64>, %arg1: tensor<*xf64>) -> tensor<*xf64> {
    %0 = toy.transpose(%arg0 : tensor<*xf64>) to tensor<*xf64>
    %1 = toy.transpose(%arg1 : tensor<*xf64>) to tensor<*xf64>
    %2 = toy.mul %0, %1 : tensor<*xf64>
    toy.return %2 : tensor<*xf64>
  }
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %1 = toy.constant dense<[1.000000e+00, 2.000000e+00, 3.000000e+00, 4.000000e+00, 5.000000e+00, 6.000000e+00]> : tensor<6xf64>
    %2 = toy.reshape(%1 : tensor<6xf64>) to tensor<2x3xf64>
    %3 = toy.generic_call @multiply_transpose(%0, %2) : (tensor<2x3xf64>, tensor<2x3xf64>) -> tensor<*xf64>
    %4 = toy.generic_call @multiply_transpose(%2, %0) : (tensor<2x3xf64>, tensor<2x3xf64>) -> tensor<*xf64>
    %5 = toy.generic_call @multiply_transpose(%2, %3) : (tensor<2x3xf64>, tensor<*xf64>) -> tensor<*xf64>
    %6 = toy.transpose(%0 : tensor<2x3xf64>) to tensor<*xf64>
    %7 = toy.generic_call @multiply_transpose(%6, %3) : (tensor<*xf64>, tensor<*xf64>) -> tensor<*xf64>
    toy.return
  }
}

===== Optimized MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/ast.toy) =====
module {
  toy.func @multiply_transpose(%arg0: tensor<*xf64>, %arg1: tensor<*xf64>) -> tensor<*xf64> {
    %0 = toy.transpose(%arg0 : tensor<*xf64>) to tensor<*xf64>
    %1 = toy.transpose(%arg1 : tensor<*xf64>) to tensor<*xf64>
    %2 = toy.mul %0, %1 : tensor<*xf64>
    toy.return %2 : tensor<*xf64>
  }
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %1 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %2 = toy.generic_call @multiply_transpose(%0, %1) : (tensor<2x3xf64>, tensor<2x3xf64>) -> tensor<*xf64>
    %3 = toy.generic_call @multiply_transpose(%1, %0) : (tensor<2x3xf64>, tensor<2x3xf64>) -> tensor<*xf64>
    %4 = toy.generic_call @multiply_transpose(%1, %2) : (tensor<2x3xf64>, tensor<*xf64>) -> tensor<*xf64>
    %5 = toy.transpose(%0 : tensor<2x3xf64>) to tensor<*xf64>
    %6 = toy.generic_call @multiply_transpose(%5, %2) : (tensor<*xf64>, tensor<*xf64>) -> tensor<*xf64>
    toy.return
  }
}

===== Original MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/trivial_reshape.toy) =====
module {
  toy.func @main() {
    %0 = toy.constant dense<[1.000000e+00, 2.000000e+00]> : tensor<2xf64>
    %1 = toy.reshape(%0 : tensor<2xf64>) to tensor<2x1xf64>
    %2 = toy.reshape(%1 : tensor<2x1xf64>) to tensor<2x1xf64>
    %3 = toy.reshape(%2 : tensor<2x1xf64>) to tensor<2x1xf64>
    toy.print %3 : tensor<2x1xf64>
    toy.return
  }
}

===== Optimized MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/trivial_reshape.toy) =====
module {
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00], [2.000000e+00]]> : tensor<2x1xf64>
    toy.print %0 : tensor<2x1xf64>
    toy.return
  }
}

===== Original MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/scalar.toy) =====
module {
  toy.func @main() {
    %0 = toy.constant dense<5.500000e+00> : tensor<f64>
    %1 = toy.reshape(%0 : tensor<f64>) to tensor<2x2xf64>
    toy.print %1 : tensor<2x2xf64>
    toy.return
  }
}

===== Optimized MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/scalar.toy) =====
toyc-ch3: /home/fhj/llvm-project/mlir/lib/IR/BuiltinAttributes.cpp:1253: mlir::DenseElementsAttr mlir::DenseElementsAttr::reshape(mlir::ShapedType): Assertion `newType.getNumElements() == curType.getNumElements() && "expected the same number of elements"' failed.
Aborted (core dumped)

===== Original MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/empty.toy) =====
Parse error (4, 0): expected 'def' in prototype but has Token -1
module {
}

===== Optimized MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch3/empty.toy) =====
Parse error (4, 0): expected 'def' in prototype but has Token -1
module {
}

```

# Ch4\-\-dialect和operation接口

MLIR 中的接口类似于面向对象编程中的接口概念，允许操作\(Operations\)声明它们支持的功能，而无需继承特定类。其主要目的是使操作更通用、可重用；支持多态行为；实现操作无关的转换和优化。

关键接口类型：

- `CallOpInterface`：函数调用操作接口

- `ShapeInference`：形状推断接口

- `CastOpInterface`：类型转换接口

运行代码：

```Bash
~/llvm-project/build$ for test in $(find ~/llvm-project/mlir/test/Examples/Toy/Ch4 -name "*.toy" ! -name "empty.toy"); do
>   echo "===== Processing $test ====="
>   ~/llvm-project/build/bin/toyc-ch4 $test -emit=mlir -opt
>   echo
> done
```

运行结果：

```JavaScript

===== Processing /home/fhj/llvm-project/mlir/test/Examples/Toy/Ch4/transpose_transpose.toy =====
module {
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    toy.print %0 : tensor<2x3xf64>
    toy.return
  }
}

===== Processing /home/fhj/llvm-project/mlir/test/Examples/Toy/Ch4/codegen.toy =====
module {
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %1 = toy.transpose(%0 : tensor<2x3xf64>) to tensor<3x2xf64>
    %2 = toy.mul %1, %1 : tensor<3x2xf64>
    toy.print %2 : tensor<3x2xf64>
    toy.return
  }
}

===== Processing /home/fhj/llvm-project/mlir/test/Examples/Toy/Ch4/ast.toy =====
module {
  toy.func @main() {
    toy.return
  }
}

===== Processing /home/fhj/llvm-project/mlir/test/Examples/Toy/Ch4/trivial_reshape.toy =====
module {
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00], [2.000000e+00]]> : tensor<2x1xf64>
    toy.print %0 : tensor<2x1xf64>
    toy.return
  }
}

===== Processing /home/fhj/llvm-project/mlir/test/Examples/Toy/Ch4/scalar.toy =====
toyc-ch4: /home/fhj/llvm-project/mlir/lib/IR/BuiltinAttributes.cpp:1253: mlir::DenseElementsAttr mlir::DenseElementsAttr::reshape(mlir::ShapedType): Assertion `newType.getNumElements() == curType.getNumElements() && "expected the same number of elements"' failed.
Aborted (core dumped)

```



# Ch5\-\-降级到低级方言

本章主要讲述了如何将 Toy 方言操作转换为更低级的方言（如 Affine、MemRef）。实际的转换流程为：

1. 准备转换目标：定义合法的目标方言。

2. 注册重写模式：实现操作转换逻辑。

3. 应用类型转换：将张量类型转换为内存引用。

4. 执行部分降低：逐步转换操作，保留关键操作。

5. 清理：移除不再需要的操作。

运行代码：

```Bash
~/llvm-project/build$ for test in $(find ~/llvm-project/mlir/test/Examples/Toy/Ch5 -name "*.mlir"); do
>   echo "===== Control Flow MLIR ($test) ====="
>   ~/llvm-project/build/bin/toyc-ch5 $test --emit=mlir-affine
>   echo
> done
```

运行结果：

```JavaScript
===== Control Flow MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch5/affine-lowering.mlir) =====
module {
  func.func @main() {
    %cst = arith.constant 6.000000e+00 : f64
    %cst_0 = arith.constant 5.000000e+00 : f64
    %cst_1 = arith.constant 4.000000e+00 : f64
    %cst_2 = arith.constant 3.000000e+00 : f64
    %cst_3 = arith.constant 2.000000e+00 : f64
    %cst_4 = arith.constant 1.000000e+00 : f64
    %alloc = memref.alloc() : memref<3x2xf64>
    %alloc_5 = memref.alloc() : memref<3x2xf64>
    %alloc_6 = memref.alloc() : memref<2x3xf64>
    affine.store %cst_4, %alloc_6[0, 0] : memref<2x3xf64>
    affine.store %cst_3, %alloc_6[0, 1] : memref<2x3xf64>
    affine.store %cst_2, %alloc_6[0, 2] : memref<2x3xf64>
    affine.store %cst_1, %alloc_6[1, 0] : memref<2x3xf64>
    affine.store %cst_0, %alloc_6[1, 1] : memref<2x3xf64>
    affine.store %cst, %alloc_6[1, 2] : memref<2x3xf64>
    affine.for %arg0 = 0 to 3 {
      affine.for %arg1 = 0 to 2 {
        %0 = affine.load %alloc_6[%arg1, %arg0] : memref<2x3xf64>
        affine.store %0, %alloc_5[%arg0, %arg1] : memref<3x2xf64>
      }
    }
    affine.for %arg0 = 0 to 3 {
      affine.for %arg1 = 0 to 2 {
        %0 = affine.load %alloc_5[%arg0, %arg1] : memref<3x2xf64>
        %1 = arith.mulf %0, %0 : f64
        affine.store %1, %alloc[%arg0, %arg1] : memref<3x2xf64>
      }
    }
    toy.print %alloc : memref<3x2xf64>
    memref.dealloc %alloc_6 : memref<2x3xf64>
    memref.dealloc %alloc_5 : memref<3x2xf64>
    memref.dealloc %alloc : memref<3x2xf64>
    return
  }
}

===== Control Flow MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch5/shape_inference.mlir) =====
module {
  func.func @main() {
    %cst = arith.constant 6.000000e+00 : f64
    %cst_0 = arith.constant 5.000000e+00 : f64
    %cst_1 = arith.constant 4.000000e+00 : f64
    %cst_2 = arith.constant 3.000000e+00 : f64
    %cst_3 = arith.constant 2.000000e+00 : f64
    %cst_4 = arith.constant 1.000000e+00 : f64
    %alloc = memref.alloc() : memref<3x2xf64>
    %alloc_5 = memref.alloc() : memref<3x2xf64>
    %alloc_6 = memref.alloc() : memref<2x3xf64>
    affine.store %cst_4, %alloc_6[0, 0] : memref<2x3xf64>
    affine.store %cst_3, %alloc_6[0, 1] : memref<2x3xf64>
    affine.store %cst_2, %alloc_6[0, 2] : memref<2x3xf64>
    affine.store %cst_1, %alloc_6[1, 0] : memref<2x3xf64>
    affine.store %cst_0, %alloc_6[1, 1] : memref<2x3xf64>
    affine.store %cst, %alloc_6[1, 2] : memref<2x3xf64>
    affine.for %arg0 = 0 to 3 {
      affine.for %arg1 = 0 to 2 {
        %0 = affine.load %alloc_6[%arg1, %arg0] : memref<2x3xf64>
        affine.store %0, %alloc_5[%arg0, %arg1] : memref<3x2xf64>
      }
    }
    affine.for %arg0 = 0 to 3 {
      affine.for %arg1 = 0 to 2 {
        %0 = affine.load %alloc_5[%arg0, %arg1] : memref<3x2xf64>
        %1 = arith.mulf %0, %0 : f64
        affine.store %1, %alloc[%arg0, %arg1] : memref<3x2xf64>
      }
    }
    toy.print %alloc : memref<3x2xf64>
    memref.dealloc %alloc_6 : memref<2x3xf64>
    memref.dealloc %alloc_5 : memref<3x2xf64>
    memref.dealloc %alloc : memref<3x2xf64>
    return
  }
}

===== Control Flow MLIR (/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch5/invalid.mlir) =====
/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch5/invalid.mlir:8:8: error: 'toy.print' op requires zero results
  %0 = "toy.print"()  : () -> tensor<2x3xf64>
       ^
/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch5/invalid.mlir:8:8: note: see current operation: %0 = "toy.print"() : () -> tensor<2x3xf64>
Error can't load file /home/fhj/llvm-project/mlir/test/Examples/Toy/Ch5/invalid.mlir

```

# Ch6\-\-降低到LLVM并生成代码

运行代码（\-emit=mlir  \-\-print\-after\-all）：

```TypeScript
~/llvm-project/build$ ~/llvm-project/build/bin/toyc-ch6 ~/llvm-project/mlir/test/Examples/Toy/Ch6/llvm-lowering.mlir --emit=mlir --print-after-all
```

运行结果：

```Plain Text
module {
  toy.func @main() {
    %0 = toy.constant dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>
    %1 = toy.transpose(%0 : tensor<2x3xf64>) to tensor<3x2xf64>
    %2 = toy.mul %1, %1 : tensor<3x2xf64>
    toy.print %2 : tensor<3x2xf64>
    toy.return
  }
}
```

运行代码（\-emit=mlir\-affine \-\-print\-after\-all）：

```Plain Text
~/llvm-project/build/bin/toyc-ch6 ~/llvm-project/mlir/test/Examples/Toy/Ch6/llvm-lowering.mlir --emit=mlir-affine --print-after-all
```

运行结果：

```Plain Text
module {
  func.func @main() {
    %cst = arith.constant 6.000000e+00 : f64
    %cst_0 = arith.constant 5.000000e+00 : f64
    %cst_1 = arith.constant 4.000000e+00 : f64
    %cst_2 = arith.constant 3.000000e+00 : f64
    %cst_3 = arith.constant 2.000000e+00 : f64
    %cst_4 = arith.constant 1.000000e+00 : f64
    %alloc = memref.alloc() : memref<3x2xf64>
    %alloc_5 = memref.alloc() : memref<3x2xf64>
    %alloc_6 = memref.alloc() : memref<2x3xf64>
    affine.store %cst_4, %alloc_6[0, 0] : memref<2x3xf64>
    affine.store %cst_3, %alloc_6[0, 1] : memref<2x3xf64>
    affine.store %cst_2, %alloc_6[0, 2] : memref<2x3xf64>
    affine.store %cst_1, %alloc_6[1, 0] : memref<2x3xf64>
    affine.store %cst_0, %alloc_6[1, 1] : memref<2x3xf64>
    affine.store %cst, %alloc_6[1, 2] : memref<2x3xf64>
    affine.for %arg0 = 0 to 3 {
      affine.for %arg1 = 0 to 2 {
        %0 = affine.load %alloc_6[%arg1, %arg0] : memref<2x3xf64>
        affine.store %0, %alloc_5[%arg0, %arg1] : memref<3x2xf64>
      }
    }
    affine.for %arg0 = 0 to 3 {
      affine.for %arg1 = 0 to 2 {
        %0 = affine.load %alloc_5[%arg0, %arg1] : memref<3x2xf64>
        %1 = arith.mulf %0, %0 : f64
        affine.store %1, %alloc[%arg0, %arg1] : memref<3x2xf64>
      }
    }
    toy.print %alloc : memref<3x2xf64>
    memref.dealloc %alloc_6 : memref<2x3xf64>
    memref.dealloc %alloc_5 : memref<3x2xf64>
    memref.dealloc %alloc : memref<3x2xf64>
    return
  }
}
```

运行代码（\-emit=mlir\-llvm \-\-print\-after\-all）：

```Plain Text
~/llvm-project/build/bin/toyc-ch6 ~/llvm-project/mlir/test/Examples/Toy/Ch6/llvm-lowering.mlir --emit=mlir-llvm --print-after-all
```

运行结果：

```YAML
module {
  llvm.func @free(!llvm.ptr)
  llvm.mlir.global internal constant @nl("\0A\00") {addr_space = 0 : i32}
  llvm.mlir.global internal constant @frmt_spec("%f \00") {addr_space = 0 : i32}
  llvm.func @printf(!llvm.ptr, ...) -> i32
  llvm.func @malloc(i64) -> !llvm.ptr
  llvm.func @main() {
    %0 = llvm.mlir.constant(6.000000e+00 : f64) : f64
    %1 = llvm.mlir.constant(5.000000e+00 : f64) : f64
    %2 = llvm.mlir.constant(4.000000e+00 : f64) : f64
    %3 = llvm.mlir.constant(3.000000e+00 : f64) : f64
    %4 = llvm.mlir.constant(2.000000e+00 : f64) : f64
    %5 = llvm.mlir.constant(1.000000e+00 : f64) : f64
    %6 = llvm.mlir.constant(3 : index) : i64
    %7 = llvm.mlir.constant(2 : index) : i64
    %8 = llvm.mlir.constant(1 : index) : i64
    %9 = llvm.mlir.constant(6 : index) : i64
    %10 = llvm.mlir.zero : !llvm.ptr
    %11 = llvm.getelementptr %10[%9] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    %12 = llvm.ptrtoint %11 : !llvm.ptr to i64
    %13 = llvm.call @malloc(%12) : (i64) -> !llvm.ptr
    %14 = llvm.mlir.poison : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %15 = llvm.insertvalue %13, %14[0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %16 = llvm.insertvalue %13, %15[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %17 = llvm.mlir.constant(0 : index) : i64
    %18 = llvm.insertvalue %17, %16[2] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %19 = llvm.insertvalue %6, %18[3, 0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %20 = llvm.insertvalue %7, %19[3, 1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %21 = llvm.insertvalue %7, %20[4, 0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %22 = llvm.insertvalue %8, %21[4, 1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %23 = llvm.mlir.constant(3 : index) : i64
    %24 = llvm.mlir.constant(2 : index) : i64
    %25 = llvm.mlir.constant(1 : index) : i64
    %26 = llvm.mlir.constant(6 : index) : i64
    %27 = llvm.mlir.zero : !llvm.ptr
    %28 = llvm.getelementptr %27[%26] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    %29 = llvm.ptrtoint %28 : !llvm.ptr to i64
    %30 = llvm.call @malloc(%29) : (i64) -> !llvm.ptr
    %31 = llvm.mlir.poison : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %32 = llvm.insertvalue %30, %31[0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %33 = llvm.insertvalue %30, %32[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %34 = llvm.mlir.constant(0 : index) : i64
    %35 = llvm.insertvalue %34, %33[2] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %36 = llvm.insertvalue %23, %35[3, 0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %37 = llvm.insertvalue %24, %36[3, 1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %38 = llvm.insertvalue %24, %37[4, 0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %39 = llvm.insertvalue %25, %38[4, 1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %40 = llvm.mlir.constant(2 : index) : i64
    %41 = llvm.mlir.constant(3 : index) : i64
    %42 = llvm.mlir.constant(1 : index) : i64
    %43 = llvm.mlir.constant(6 : index) : i64
    %44 = llvm.mlir.zero : !llvm.ptr
    %45 = llvm.getelementptr %44[%43] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    %46 = llvm.ptrtoint %45 : !llvm.ptr to i64
    %47 = llvm.call @malloc(%46) : (i64) -> !llvm.ptr
    %48 = llvm.mlir.poison : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %49 = llvm.insertvalue %47, %48[0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %50 = llvm.insertvalue %47, %49[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %51 = llvm.mlir.constant(0 : index) : i64
    %52 = llvm.insertvalue %51, %50[2] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %53 = llvm.insertvalue %40, %52[3, 0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %54 = llvm.insertvalue %41, %53[3, 1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %55 = llvm.insertvalue %41, %54[4, 0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %56 = llvm.insertvalue %42, %55[4, 1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %57 = llvm.mlir.constant(0 : index) : i64
    %58 = llvm.mlir.constant(0 : index) : i64
    %59 = llvm.extractvalue %56[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %60 = llvm.mlir.constant(3 : index) : i64
    %61 = llvm.mul %57, %60 overflow<nsw, nuw> : i64
    %62 = llvm.add %61, %58 overflow<nsw, nuw> : i64
    %63 = llvm.getelementptr inbounds|nuw %59[%62] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    llvm.store %5, %63 : f64, !llvm.ptr
    %64 = llvm.mlir.constant(0 : index) : i64
    %65 = llvm.mlir.constant(1 : index) : i64
    %66 = llvm.extractvalue %56[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %67 = llvm.mlir.constant(3 : index) : i64
    %68 = llvm.mul %64, %67 overflow<nsw, nuw> : i64
    %69 = llvm.add %68, %65 overflow<nsw, nuw> : i64
    %70 = llvm.getelementptr inbounds|nuw %66[%69] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    llvm.store %4, %70 : f64, !llvm.ptr
    %71 = llvm.mlir.constant(0 : index) : i64
    %72 = llvm.mlir.constant(2 : index) : i64
    %73 = llvm.extractvalue %56[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %74 = llvm.mlir.constant(3 : index) : i64
    %75 = llvm.mul %71, %74 overflow<nsw, nuw> : i64
    %76 = llvm.add %75, %72 overflow<nsw, nuw> : i64
    %77 = llvm.getelementptr inbounds|nuw %73[%76] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    llvm.store %3, %77 : f64, !llvm.ptr
    %78 = llvm.mlir.constant(1 : index) : i64
    %79 = llvm.mlir.constant(0 : index) : i64
    %80 = llvm.extractvalue %56[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %81 = llvm.mlir.constant(3 : index) : i64
    %82 = llvm.mul %78, %81 overflow<nsw, nuw> : i64
    %83 = llvm.add %82, %79 overflow<nsw, nuw> : i64
    %84 = llvm.getelementptr inbounds|nuw %80[%83] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    llvm.store %2, %84 : f64, !llvm.ptr
    %85 = llvm.mlir.constant(1 : index) : i64
    %86 = llvm.mlir.constant(1 : index) : i64
    %87 = llvm.extractvalue %56[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %88 = llvm.mlir.constant(3 : index) : i64
    %89 = llvm.mul %85, %88 overflow<nsw, nuw> : i64
    %90 = llvm.add %89, %86 overflow<nsw, nuw> : i64
    %91 = llvm.getelementptr inbounds|nuw %87[%90] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    llvm.store %1, %91 : f64, !llvm.ptr
    %92 = llvm.mlir.constant(1 : index) : i64
    %93 = llvm.mlir.constant(2 : index) : i64
    %94 = llvm.extractvalue %56[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %95 = llvm.mlir.constant(3 : index) : i64
    %96 = llvm.mul %92, %95 overflow<nsw, nuw> : i64
    %97 = llvm.add %96, %93 overflow<nsw, nuw> : i64
    %98 = llvm.getelementptr inbounds|nuw %94[%97] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    llvm.store %0, %98 : f64, !llvm.ptr
    %99 = llvm.mlir.constant(0 : index) : i64
    %100 = llvm.mlir.constant(3 : index) : i64
    %101 = llvm.mlir.constant(1 : index) : i64
    llvm.br ^bb1(%99 : i64)
  ^bb1(%102: i64):  // 2 preds: ^bb0, ^bb5
    %103 = llvm.icmp "slt" %102, %100 : i64
    llvm.cond_br %103, ^bb2, ^bb6
  ^bb2:  // pred: ^bb1
    %104 = llvm.mlir.constant(0 : index) : i64
    %105 = llvm.mlir.constant(2 : index) : i64
    %106 = llvm.mlir.constant(1 : index) : i64
    llvm.br ^bb3(%104 : i64)
  ^bb3(%107: i64):  // 2 preds: ^bb2, ^bb4
    %108 = llvm.icmp "slt" %107, %105 : i64
    llvm.cond_br %108, ^bb4, ^bb5
  ^bb4:  // pred: ^bb3
    %109 = llvm.extractvalue %56[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %110 = llvm.mlir.constant(3 : index) : i64
    %111 = llvm.mul %107, %110 overflow<nsw, nuw> : i64
    %112 = llvm.add %111, %102 overflow<nsw, nuw> : i64
    %113 = llvm.getelementptr inbounds|nuw %109[%112] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    %114 = llvm.load %113 : !llvm.ptr -> f64
    %115 = llvm.extractvalue %39[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %116 = llvm.mlir.constant(2 : index) : i64
    %117 = llvm.mul %102, %116 overflow<nsw, nuw> : i64
    %118 = llvm.add %117, %107 overflow<nsw, nuw> : i64
    %119 = llvm.getelementptr inbounds|nuw %115[%118] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    llvm.store %114, %119 : f64, !llvm.ptr
    %120 = llvm.add %107, %106 : i64
    llvm.br ^bb3(%120 : i64)
  ^bb5:  // pred: ^bb3
    %121 = llvm.add %102, %101 : i64
    llvm.br ^bb1(%121 : i64)
  ^bb6:  // pred: ^bb1
    %122 = llvm.mlir.constant(0 : index) : i64
    %123 = llvm.mlir.constant(3 : index) : i64
    %124 = llvm.mlir.constant(1 : index) : i64
    llvm.br ^bb7(%122 : i64)
  ^bb7(%125: i64):  // 2 preds: ^bb6, ^bb11
    %126 = llvm.icmp "slt" %125, %123 : i64
    llvm.cond_br %126, ^bb8, ^bb12
  ^bb8:  // pred: ^bb7
    %127 = llvm.mlir.constant(0 : index) : i64
    %128 = llvm.mlir.constant(2 : index) : i64
    %129 = llvm.mlir.constant(1 : index) : i64
    llvm.br ^bb9(%127 : i64)
  ^bb9(%130: i64):  // 2 preds: ^bb8, ^bb10
    %131 = llvm.icmp "slt" %130, %128 : i64
    llvm.cond_br %131, ^bb10, ^bb11
  ^bb10:  // pred: ^bb9
    %132 = llvm.extractvalue %39[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %133 = llvm.mlir.constant(2 : index) : i64
    %134 = llvm.mul %125, %133 overflow<nsw, nuw> : i64
    %135 = llvm.add %134, %130 overflow<nsw, nuw> : i64
    %136 = llvm.getelementptr inbounds|nuw %132[%135] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    %137 = llvm.load %136 : !llvm.ptr -> f64
    %138 = llvm.fmul %137, %137 : f64
    %139 = llvm.extractvalue %22[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %140 = llvm.mlir.constant(2 : index) : i64
    %141 = llvm.mul %125, %140 overflow<nsw, nuw> : i64
    %142 = llvm.add %141, %130 overflow<nsw, nuw> : i64
    %143 = llvm.getelementptr inbounds|nuw %139[%142] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    llvm.store %138, %143 : f64, !llvm.ptr
    %144 = llvm.add %130, %129 : i64
    llvm.br ^bb9(%144 : i64)
  ^bb11:  // pred: ^bb9
    %145 = llvm.add %125, %124 : i64
    llvm.br ^bb7(%145 : i64)
  ^bb12:  // pred: ^bb7
    %146 = llvm.mlir.addressof @frmt_spec : !llvm.ptr
    %147 = llvm.mlir.constant(0 : index) : i64
    %148 = llvm.getelementptr %146[%147, %147] : (!llvm.ptr, i64, i64) -> !llvm.ptr, !llvm.array<4 x i8>
    %149 = llvm.mlir.addressof @nl : !llvm.ptr
    %150 = llvm.mlir.constant(0 : index) : i64
    %151 = llvm.getelementptr %149[%150, %150] : (!llvm.ptr, i64, i64) -> !llvm.ptr, !llvm.array<2 x i8>
    %152 = llvm.mlir.constant(0 : index) : i64
    %153 = llvm.mlir.constant(3 : index) : i64
    %154 = llvm.mlir.constant(1 : index) : i64
    llvm.br ^bb13(%152 : i64)
  ^bb13(%155: i64):  // 2 preds: ^bb12, ^bb17
    %156 = llvm.icmp "slt" %155, %153 : i64
    llvm.cond_br %156, ^bb14, ^bb18
  ^bb14:  // pred: ^bb13
    %157 = llvm.mlir.constant(0 : index) : i64
    %158 = llvm.mlir.constant(2 : index) : i64
    %159 = llvm.mlir.constant(1 : index) : i64
    llvm.br ^bb15(%157 : i64)
  ^bb15(%160: i64):  // 2 preds: ^bb14, ^bb16
    %161 = llvm.icmp "slt" %160, %158 : i64
    llvm.cond_br %161, ^bb16, ^bb17
  ^bb16:  // pred: ^bb15
    %162 = llvm.extractvalue %22[1] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    %163 = llvm.mlir.constant(2 : index) : i64
    %164 = llvm.mul %155, %163 overflow<nsw, nuw> : i64
    %165 = llvm.add %164, %160 overflow<nsw, nuw> : i64
    %166 = llvm.getelementptr inbounds|nuw %162[%165] : (!llvm.ptr, i64) -> !llvm.ptr, f64
    %167 = llvm.load %166 : !llvm.ptr -> f64
    %168 = llvm.call @printf(%148, %167) vararg(!llvm.func<i32 (ptr, ...)>) : (!llvm.ptr, f64) -> i32
    %169 = llvm.add %160, %159 : i64
    llvm.br ^bb15(%169 : i64)
  ^bb17:  // pred: ^bb15
    %170 = llvm.call @printf(%151) vararg(!llvm.func<i32 (ptr, ...)>) : (!llvm.ptr) -> i32
    %171 = llvm.add %155, %154 : i64
    llvm.br ^bb13(%171 : i64)
  ^bb18:  // pred: ^bb13
    %172 = llvm.extractvalue %56[0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    llvm.call @free(%172) : (!llvm.ptr) -> ()
    %173 = llvm.extractvalue %39[0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    llvm.call @free(%173) : (!llvm.ptr) -> ()
    %174 = llvm.extractvalue %22[0] : !llvm.struct<(ptr, ptr, i64, array<2 x i64>, array<2 x i64>)>
    llvm.call @free(%174) : (!llvm.ptr) -> ()
    llvm.return
  }
}
```

运行代码（\-emit=llvm \-\-print\-after\-all）：

```Plain Text
~/llvm-project/build$ ~/llvm-project/build/bin/toyc-ch6 ~/llvm-project/mlir/test/Examples/Toy/Ch6/llvm-lowering.mlir --emit=llvm --print-after-all
```

运行结果：

```YAML
; ModuleID = 'LLVMDialectModule'
source_filename = "LLVMDialectModule"
target datalayout = "e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:32:64-S128"
target triple = "x86_64-unknown-linux-gnu"

@nl = internal constant [2 x i8] c"\0A\00"
@frmt_spec = internal constant [4 x i8] c"%f \00"

declare !dbg !3 void @free(ptr)

declare !dbg !6 i32 @printf(ptr, ...)

declare !dbg !7 ptr @malloc(i64)

define void @main() !dbg !8 {
  %1 = call ptr @malloc(i64 48), !dbg !9
  %2 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } poison, ptr %1, 0, !dbg !9
  %3 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %2, ptr %1, 1, !dbg !9
  %4 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %3, i64 0, 2, !dbg !9
  %5 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %4, i64 3, 3, 0, !dbg !9
  %6 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %5, i64 2, 3, 1, !dbg !9
  %7 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %6, i64 2, 4, 0, !dbg !9
  %8 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %7, i64 1, 4, 1, !dbg !9
  %9 = call ptr @malloc(i64 48), !dbg !10
  %10 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } poison, ptr %9, 0, !dbg !10
  %11 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %10, ptr %9, 1, !dbg !10
  %12 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %11, i64 0, 2, !dbg !10
  %13 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %12, i64 3, 3, 0, !dbg !10
  %14 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %13, i64 2, 3, 1, !dbg !10
  %15 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %14, i64 2, 4, 0, !dbg !10
  %16 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %15, i64 1, 4, 1, !dbg !10
  %17 = call ptr @malloc(i64 48), !dbg !11
  %18 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } poison, ptr %17, 0, !dbg !11
  %19 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %18, ptr %17, 1, !dbg !11
  %20 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %19, i64 0, 2, !dbg !11
  %21 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %20, i64 2, 3, 0, !dbg !11
  %22 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %21, i64 3, 3, 1, !dbg !11
  %23 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %22, i64 3, 4, 0, !dbg !11
  %24 = insertvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %23, i64 1, 4, 1, !dbg !11
  %25 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %24, 1, !dbg !11
  %26 = getelementptr inbounds nuw double, ptr %25, i64 0, !dbg !11
  store double 1.000000e+00, ptr %26, align 8, !dbg !11
  %27 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %24, 1, !dbg !11
  %28 = getelementptr inbounds nuw double, ptr %27, i64 1, !dbg !11
  store double 2.000000e+00, ptr %28, align 8, !dbg !11
  %29 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %24, 1, !dbg !11
  %30 = getelementptr inbounds nuw double, ptr %29, i64 2, !dbg !11
  store double 3.000000e+00, ptr %30, align 8, !dbg !11
  %31 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %24, 1, !dbg !11
  %32 = getelementptr inbounds nuw double, ptr %31, i64 3, !dbg !11
  store double 4.000000e+00, ptr %32, align 8, !dbg !11
  %33 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %24, 1, !dbg !11
  %34 = getelementptr inbounds nuw double, ptr %33, i64 4, !dbg !11
  store double 5.000000e+00, ptr %34, align 8, !dbg !11
  %35 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %24, 1, !dbg !11
  %36 = getelementptr inbounds nuw double, ptr %35, i64 5, !dbg !11
  store double 6.000000e+00, ptr %36, align 8, !dbg !11
  br label %37, !dbg !10

37:                                               ; preds = %55, %0
  %38 = phi i64 [ 0, %0 ], [ %56, %55 ], !dbg !10
  %39 = icmp slt i64 %38, 3, !dbg !10
  br i1 %39, label %40, label %57, !dbg !10

40:                                               ; preds = %37
  br label %41, !dbg !10

41:                                               ; preds = %44, %40
  %42 = phi i64 [ 0, %40 ], [ %54, %44 ], !dbg !10
  %43 = icmp slt i64 %42, 2, !dbg !10
  br i1 %43, label %44, label %55, !dbg !10

44:                                               ; preds = %41
  %45 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %24, 1, !dbg !10
  %46 = mul nuw nsw i64 %42, 3, !dbg !10
  %47 = add nuw nsw i64 %46, %38, !dbg !10
  %48 = getelementptr inbounds nuw double, ptr %45, i64 %47, !dbg !10
  %49 = load double, ptr %48, align 8, !dbg !10
  %50 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %16, 1, !dbg !10
  %51 = mul nuw nsw i64 %38, 2, !dbg !10
  %52 = add nuw nsw i64 %51, %42, !dbg !10
  %53 = getelementptr inbounds nuw double, ptr %50, i64 %52, !dbg !10
  store double %49, ptr %53, align 8, !dbg !10
  %54 = add i64 %42, 1, !dbg !10
  br label %41, !dbg !10

55:                                               ; preds = %41
  %56 = add i64 %38, 1, !dbg !10
  br label %37, !dbg !10

57:                                               ; preds = %37
  br label %58, !dbg !9

58:                                               ; preds = %77, %57
  %59 = phi i64 [ 0, %57 ], [ %78, %77 ], !dbg !9
  %60 = icmp slt i64 %59, 3, !dbg !9
  br i1 %60, label %61, label %79, !dbg !9

61:                                               ; preds = %58
  br label %62, !dbg !9

62:                                               ; preds = %65, %61
  %63 = phi i64 [ 0, %61 ], [ %76, %65 ], !dbg !9
  %64 = icmp slt i64 %63, 2, !dbg !9
  br i1 %64, label %65, label %77, !dbg !9

65:                                               ; preds = %62
  %66 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %16, 1, !dbg !9
  %67 = mul nuw nsw i64 %59, 2, !dbg !9
  %68 = add nuw nsw i64 %67, %63, !dbg !9
  %69 = getelementptr inbounds nuw double, ptr %66, i64 %68, !dbg !9
  %70 = load double, ptr %69, align 8, !dbg !9
  %71 = fmul double %70, %70, !dbg !9
  %72 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %8, 1, !dbg !9
  %73 = mul nuw nsw i64 %59, 2, !dbg !9
  %74 = add nuw nsw i64 %73, %63, !dbg !9
  %75 = getelementptr inbounds nuw double, ptr %72, i64 %74, !dbg !9
  store double %71, ptr %75, align 8, !dbg !9
  %76 = add i64 %63, 1, !dbg !9
  br label %62, !dbg !9

77:                                               ; preds = %62
  %78 = add i64 %59, 1, !dbg !9
  br label %58, !dbg !9

79:                                               ; preds = %58
  br label %80, !dbg !12

80:                                               ; preds = %95, %79
  %81 = phi i64 [ 0, %79 ], [ %97, %95 ], !dbg !12
  %82 = icmp slt i64 %81, 3, !dbg !12
  br i1 %82, label %83, label %98, !dbg !12

83:                                               ; preds = %80
  br label %84, !dbg !12

84:                                               ; preds = %87, %83
  %85 = phi i64 [ 0, %83 ], [ %94, %87 ], !dbg !12
  %86 = icmp slt i64 %85, 2, !dbg !12
  br i1 %86, label %87, label %95, !dbg !12

87:                                               ; preds = %84
  %88 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %8, 1, !dbg !12
  %89 = mul nuw nsw i64 %81, 2, !dbg !12
  %90 = add nuw nsw i64 %89, %85, !dbg !12
  %91 = getelementptr inbounds nuw double, ptr %88, i64 %90, !dbg !12
  %92 = load double, ptr %91, align 8, !dbg !12
  %93 = call i32 (ptr, ...) @printf(ptr @frmt_spec, double %92), !dbg !12
  %94 = add i64 %85, 1, !dbg !12
  br label %84, !dbg !12

95:                                               ; preds = %84
  %96 = call i32 (ptr, ...) @printf(ptr @nl), !dbg !12
  %97 = add i64 %81, 1, !dbg !12
  br label %80, !dbg !12

98:                                               ; preds = %80
  %99 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %24, 0, !dbg !11
  call void @free(ptr %99), !dbg !11
  %100 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %16, 0, !dbg !10
  call void @free(ptr %100), !dbg !10
  %101 = extractvalue { ptr, ptr, i64, [2 x i64], [2 x i64] } %8, 0, !dbg !9
  call void @free(ptr %101), !dbg !9
  ret void, !dbg !13
}

!llvm.dbg.cu = !{!0}
!llvm.module.flags = !{!2}

!0 = distinct !DICompileUnit(language: DW_LANG_C, file: !1, producer: "MLIR", isOptimized: true, runtimeVersion: 0, emissionKind: LineTablesOnly)
!1 = !DIFile(filename: "llvm-lowering.mlir", directory: "/home/fhj/llvm-project/mlir/test/Examples/Toy/Ch6")
!2 = !{i32 2, !"Debug Info Version", i32 3}
!3 = !DISubprogram(name: "free", linkageName: "free", scope: !1, file: !1, type: !4, spFlags: DISPFlagOptimized)
!4 = !DISubroutineType(cc: DW_CC_normal, types: !5)
!5 = !{}
!6 = !DISubprogram(name: "printf", linkageName: "printf", scope: !1, file: !1, type: !4, spFlags: DISPFlagOptimized)
!7 = !DISubprogram(name: "malloc", linkageName: "malloc", scope: !1, file: !1, type: !4, spFlags: DISPFlagOptimized)
!8 = distinct !DISubprogram(name: "main", linkageName: "main", scope: !1, file: !1, line: 3, type: !4, scopeLine: 3, spFlags: DISPFlagDefinition | DISPFlagOptimized, unit: !0)
!9 = !DILocation(line: 6, column: 8, scope: !8)
!10 = !DILocation(line: 5, column: 8, scope: !8)
!11 = !DILocation(line: 4, column: 8, scope: !8)
!12 = !DILocation(line: 7, column: 3, scope: !8)
!13 = !DILocation(line: 8, column: 3, scope: !8)
```

# Ch7\-\-添加struct类型

运行代码：

```Plain Text
~/llvm-project/build/bin/toyc-ch7 ~/llvm-project/mlir/test/Examples/Toy/Ch7/struct-codegen.toy --emit=mlir --print-after-all
```

运行结果：

```Go
module {
  toy.func private @multiply_transpose(%arg0: !toy.struct<tensor<*xf64>, tensor<*xf64>>) -> tensor<*xf64> {
    %0 = toy.struct_access %arg0[0] : !toy.struct<tensor<*xf64>, tensor<*xf64>> -> tensor<*xf64>
    %1 = toy.transpose(%0 : tensor<*xf64>) to tensor<*xf64>
    %2 = toy.struct_access %arg0[1] : !toy.struct<tensor<*xf64>, tensor<*xf64>> -> tensor<*xf64>
    %3 = toy.transpose(%2 : tensor<*xf64>) to tensor<*xf64>
    %4 = toy.mul %1, %3 : tensor<*xf64>
    toy.return %4 : tensor<*xf64>
  }
  toy.func @main() {
    %0 = toy.struct_constant [dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>, dense<[[1.000000e+00, 2.000000e+00, 3.000000e+00], [4.000000e+00, 5.000000e+00, 6.000000e+00]]> : tensor<2x3xf64>] : !toy.struct<tensor<*xf64>, tensor<*xf64>>
    %1 = toy.generic_call @multiply_transpose(%0) : (!toy.struct<tensor<*xf64>, tensor<*xf64>>) -> tensor<*xf64>
    toy.print %1 : tensor<*xf64>
    toy.return
  }
}
```



