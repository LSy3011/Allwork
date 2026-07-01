# TableGen 的语法

TableGen 语法的官方文档链接：[https://llvm.org/docs/TableGen/ProgRef.html](https://llvm.org/docs/TableGen/ProgRef.html) （附录可以直接参考这个链接）

## 1.1 简介

TableGen 的目的是根据源文件中的信息生成复杂的输出文件，这些源文件比输出文件更容易编码，并且随着时间的推移也更容易维护和修改。该信息以涉及类和记录的声明式样式进行编码，然后由 TableGen 处理。这些输出文件通常是 `.inc` 文件，源文件的一般是 `.td` 文件。具体的内容可以查看上方的链接。

### 1.1.1 概念

`TableGen` 源文件主要包含 `抽象记录（abstract records）` 和 `具体记录（concrete records）` 两类元素，其中抽象记录在文档中被称为 `“类”（classes，与 C++ 类不同，无直接映射关系）`，具体记录则常简称为 ` “记录”（records）`，不过 ` “record”` 一词有时会同时指代这两者，需结合上下文区分。类和具体记录都有唯一名称，该名称可由程序员设定或由 `TableGen` 生成，它们均关联着带值的字段列表，且可拥有可选的父类列表（“父类” 既可以指类的父类，也能指具体记录继承的类，这种非标准用法源于 `TableGen` 对类和具体记录的相似处理方式）。字段的具体含义完全由 `后端（backend）` 及使用后端输出的程序决定，`TableGen` 本身不赋予其意义。

后端会处理 `TableGen` 解析器构建的部分具体记录，并生成输出文件，这些文件通常是 C++ 的.inc 文件，供需要记录数据的程序包含，当然后端也能生成任意类型的输出文件。以 LLVM 代码生成器这类复杂场景为例，可能存在大量具体记录，且部分记录的字段数量超乎预期，进而导致输出文件体积较大。

为降低 `TableGen` 文件的复杂度，类被用于抽象记录字段组。例如，部分类可抽象机器寄存器文件的概念，其他类可抽象指令格式，还有类能抽象单个指令。`TableGen` 支持类的任意层次结构，使得两个概念的抽象类可共享第三个超类，以从这两个原始概念中抽象出共同的 “子概念”。此外，具体记录（或其他类）可将某个类作为父类并传递 `模板参数（template arguments）`，父类字段能通过这些模板参数进行自定义初始化，即不同的记录或类可向同一父类传递不同的模板参数，避免了为模板参数的每个组合都单独定义类的情况。

类和具体记录都可能包含未初始化的字段，未初始化的值用 “`？`” 表示，类中的未初始化字段通常期望在被具体记录继承时填充，但具体记录的某些字段也可能保持未初始化状态。TableGen 还提供了 `“多类”（multiclasses）` 来集中收集一组记录定义，多类类似宏，可通过 “调用” 一次性定义多个具体记录，并且多类能继承其他多类，从而继承父多类的所有定义。

## 1.2 Source Files

TableGen 源文件是纯 `ASCII` 文本文件。这些文件可以包含语句、注释和空行。`TableGen` 文件的标准文件扩展名是 `.td`。

TableGen 文件可能会变得非常大，因此有一种包含机制，允许一个文件包含另一个文件的内容。这允许将大文件分解为较小的文件，并且还提供了一种简单的库机制，其中多个源文件可以包含相同的库文件。

TableGen 支持一个简单的预处理器，该预处理器可用于对 `.td` 文件的某些部分进行条件化。

## 1.3 词法分析

此处使用的词法和语法表示法旨在模仿 Python 的表示法。特别是，对于词法定义，生产在字符级别运行，元素之间没有隐含的空格。语法定义在令牌级别运行，因此令牌之间存在隐含的空格。

TableGen 支持 BCPL 样式的注释 （`//``...`） 和可嵌套的 C 样式的注释 （`/* ...*/`）。TableGen 还提供了简单的预处理工具。

在打印文件以供审阅时，可以在文件中自由使用换页符以生成分页符。

以下是基本的标点符号：

```
- + [ ] { } ( ) < > : ; . ... = ? #
```

### 1.3.1 Literals

数字文本的格式可以参考如下：

```go
**TokInteger    ** ::=  **DecimalInteger** | **HexInteger** | **BinInteger**** **//整数常量可以是十进制、十六进制或二进制中的任意一种
**DecimalInteger** ::=  ["+" | "-"] ("0"..."9")+ //十进制整数，前缀为+/-，不可同时出现，最后的+号代表一个或多个，但是不能前导为0
**HexInteger    ** ::=  "0x" ("0"..."9" | "a"..."f" | "A"..."F")+ //十六进制整数，前缀必须为0x，0X不正确，而且要注意后面字符的范围
**BinInteger    ** ::=  "0b" ("0" | "1")+ //二进制整数，前缀必须为0b，0B不正确，而且要注意后面字符的范围
```

字符串文本的格式可以参考如下：

```go
**TokString** ::=  '"' (non-'"' characters and escapes) '"' //字符串
**TokCode  ** ::=  "[{" (text not containing "}]") "}]" //代码块
```

字符串的定界符是由双引号 `"` 包裹，内容可以是除双引号 `"` 之外的任意字符，它允许转义序列。

当前实现接受以下转义序列：`\\ \' \" \t \n`。

代码块是由 `[{"` 开头，`"}]` 结尾，其内容可以是任意文本，但不能包含 `"}]"`。

### 1.3.2 Identifiers

TableGen 允许 `TokIdentifier` 以整数开头，且区分大小写。

```go
**ualpha       ** ::=  "a"..."z" | "A"..."Z" | "_"  //字母字符
**TokIdentifier** ::=  ("0"..."9")* **ualpha** (**ualpha** | "0"..."9")* //标识符
**TokVarName   ** ::=  "$" **ualpha** (**ualpha** |  "0"..."9")* //变量名
```

TableGen 有以下保留关键字，不能用作标识符：

** assert**`    bit         bits       ` **class** `       ` **code** `dag        ` **def** `        dump     `

** else**`    falseforeach   defm        defset      defvar        field` **if** `        ` **in** `      `

`include     int        letlist    multiclass    string        then          true   `

### 1.3.3 Bang operators

TableGen 提供了具有多种用途的 bang operator。

```yaml
**BangOperator** ::=  one of
                  !add         !and         !cast        !con         !dag
                  !div         !empty       !eq          !exists      !filter
                  !find        !foldl       !foreach     !ge          !getdagarg
                  !getdagname  !getdagop    !gt          !head        !if
                  !initialized !instances   !interleave  !isa         !le
                  !listconcat  !listflatten !listremove  !listsplat   !logtwo
                  !lt          !match       !mul         !ne          !not
                  !or          !range       !repr        !setdagarg   !setdagname
                  !setdagop    !shl         !size        !sra         !srl
                  !strconcat   !sub         !subst       !substr      !tail
                  !tolower     !toupper     !xor
```

可以查看如下链接：[https://llvm.org/docs/TableGen/ProgRef.html#appendix-a-bang-operators](https://llvm.org/docs/TableGen/ProgRef.html#appendix-a-bang-operators) 去查看每个 bang operator 的相关描述。

cond operator 的定义如下：

```
**CondOperator** ::=  !cond
```

### 1.3.4 Include files

TableGen 具有 include 机制，包含文件的内容在词法上替换 `include` 指令，然后像最初在主文件中一样进行解析。

```
**IncludeDirective** ::=  "include" **TokString** //其中文件路径由TokString表示，关键词为include，不带#
```

接下来的代码定义了 TableGen 语言中的预处理指令，类似于 C/C++ 中的预处理机制，用于在编译（解析）前对代码进行文本替换或条件过滤。

```
**PreprocessorDirective** ::=  "#define" | "#ifdef" | "#ifndef"
```

`#define` 是定义宏（类似文本替换);`#ifdef` 表示如果宏已定义则执行后续代码；`#ifndef` 则表示如果宏未定义则执行后续代码。

## 1.4 类型

TableGen 语言是静态类型的，使用简单但完整的类型系统。类型用于检查错误、执行隐式转换以及帮助界面设计人员限制允许的输入。每个值都需要具有关联的类型。TableGen 支持低级类型（例如 `bit`）和高级类型（例如 `dag`）的混合。这种灵活性使您能够方便、紧凑地描述各种记录。

```python
**Type   ** ::=  "bit" | "int" | "string" | "dag" | "code"
            | "bits" "<" **TokInteger** ">"
            | "list" "<" **Type** ">"
            | **ClassID****ClassID** ::=  **TokIdentifier**
```

`bit` 是可以是 0 或 1 的布尔值；`int` 类型表示一个简单的 64 位整数值，例如 5 或 -42；`string` 类型表示任意长度的有序字符序列；关键字 `code` 是 `string` 的别名，可用于指示为 code 的字符串值。

`bits<` _n_ `>` 类型是任意长度 _n_ 的固定大小整数，被视为单独的位，这些位可以单独访问。这种类型的字段可用于表示指令作代码、寄存器号或地址模式/寄存器/位移，字段的位可以单独设置，也可以作为子字段设置，例如，在指令地址中，可以单独设置寻址模式、基址寄存器号和位移。

`list<` _type_ `>` 此类型表示一个列表，其元素属于尖括号中指定的类型。元素类型是任意的;它甚至可以是另一种列表类型。列表元素从 0 开始索引。

此类型表示节点的可嵌套有向无环图 （DAG）。每个节点都有一个_运算符_和零个或多个_参数 _（或_作数 _）。参数可以是另一个 `DAG` 对象，允许任意节点和边树。例如，DAG 用于表示代码模式，供代码生成器指令选择算法使用。

`ClassID` 在类型上下文中指定类名表示定义值的类型必须是指定类的子类。这与 `list<` _type_ `>` 类型结合使用很有用;例如，要将列表的元素约束到公共基类（例如，`list<Register>` 只能包含从 `Register` 类派生的定义）。`ClassID` 必须命名先前声明或定义的类。

## 1.5 Values and Expression

`TableGen` 语句中有许多上下文需要 `values`。一个常见的示例是在 `record` 的定义中，其中每个字段都由名称和可选值指定。`TableGen` 在构建值表达式时允许合理数量的不同形式。这些形式允许以应用程序自然的语法编写 `TableGen` 文件。

```go
**Value        ** ::=  **SimpleValue** **ValueSuffix***
                  | **Value** "#" [**Value**]
**ValueSuffix  ** ::=  "{" **RangeList** "}"
                  | "[" **SliceElements** "]"
                  | "." **TokIdentifier****RangeList    ** ::=  **RangePiece** ("," **RangePiece**)*
**RangePiece   ** ::=  **TokInteger**
                  | **TokInteger** "..." **TokInteger**
                  | **TokInteger** "-" **TokInteger**
                  | **TokInteger** **TokInteger****SliceElements** ::=  (**SliceElement** ",")* **SliceElement** ","?
**SliceElement ** ::=  **Value**
                  | **Value** "..." **Value**
                  | **Value** "-" **Value**
                  | **Value** **TokInteger**
```

### 1.5.1 Simple values

**SimpleValue** 有多种形式。

```
**SimpleValue ** ::=  **SimpleValue1**
                 | **SimpleValue2**
                 | **SimpleValue3**
                 | **SimpleValue4**
                 | **SimpleValue5**
                 | **SimpleValue6**
                 | **SimpleValue7**
                 | **SimpleValue8**
                 | **SimpleValue9**
```

```
**SimpleValue1** ::=  **TokInteger** | **TokString**+ | **TokCode**
```

Value 可以是整数文本、字符串文本或代码文本。多个相邻的字符串文本与 C/C++ 中一样连接;简单值是字符串的连接。代码文本变为字符串，然后与它们无法区分。

```
**SimpleValue2** ::=  "true" | "false"
```

`true` 和 `false` 文字本质上是整数值 1 和 0 的语法糖。当布尔值用于字段初始化、位序列，if 语句时，它们提高了 TableGen 文件的可读性，解析时，这些 Literals 将转换为整数。

```
**SimpleValue3** ::=  "?"
```

问号表示未初始化的值。

```
**SimpleValue4** ::=  "{" [**ValueList**] "}"
**ValueList   ** ::=  **ValueListNE**
**ValueListNE ** ::=  **Value** ("," **Value**)*
```

这时 Value 表示一个位序列，可用于初始化 `bits<` _n_ `>` 字段（注意大括号）。执行此操作时，这些值必须表示总共 _n_ 位。

```
**SimpleValue5** ::=  "[" **ValueList** "]" ["<" **Type** ">"]
```

这时 Value 是列表初始值设定项（注意括号），括号中的值是列表的元素。可选的 **Type** 可用于指示特定的元素类型;否则，将从给定的值推断元素类型。TableGen 通常可以推断类型，但有时当值为空列表 （`[]`） 时不能推断。

```go
**SimpleValue6** ::=  "(" **DagArg** [**DagArgList**] ")"
**DagArgList  ** ::=  **DagArg** ("," **DagArg**)*
**DagArg      ** ::=  **Value** [":" **TokVarName**] | **TokVarName**
```

这表示 DAG 初始值设定项（注意括号）。第一个 **DagArg** 称为 DAG 的“运算符”，并且必须是 `record`。

```
**SimpleValue7** ::=  **TokIdentifier**
```

标识符（`TokIdentifier`）引用不同作用域的实体。

**引用类定义中声明的模板参数**

```
class Foo <int Bar> {  // 模板参数Bar
  int Baz = Bar;  // 引用模板参数Bar的值
}

def Inst : Foo<10>;  // 实例化时Bar=10，Baz=10
```

**引用类体内部定义的字段**

```java
class Foo {
  int Bar = 5;  // 类本地字段Bar
  int Baz = Bar;  // 引用本地字段Bar
}

int Val = Foo<>.Bar;  // 匿名实例化，获取Bar=5
```

**引用已定义的记录（****def****）名称**

```
def Bar : SomeClass {  // 记录定义Bar
  int X = 5;
}

def Foo {
  SomeClass Baz = Bar;  // 引用记录Bar
}
```

**引用记录体内部定义的字段**

```
def Foo {
  int Bar = 5;  // 记录本地字段Bar
  int Baz = Bar;  // 引用本地字段Bar
}

int Val = Foo.Bar;  // 通过记录名访问字段
```

可以采用相同的方式访问从 `record` 的父类继承的字段。

**引用多类（****multiclass****）定义中的模板参数**

```
multiclass Foo <int Bar> {  // 多类模板参数Bar
  def : SomeClass<Bar>;  // 使用模板参数Bar
}

def Inst : Foo<20>;  // 实例化时Bar=20
```

**引用通过****defvar****/****defset****定义的变量**

```
defvar GlobalVar = 100;  // 全局变量

def Record {
  defset localVar = 20;  // 记录内局部变量
  int Val = localVar;
}
```

**引用 foreach 循环中的迭代变量**

```
foreach i = 0...5 in {  // 迭代变量i
  def Foo#i : Instruction {
    int Opcode = i;  // 使用迭代变量i
  }
}
```

```
**SimpleValue8** ::=  **ClassID** "<" **ArgValueList** ">"
```

此表单创建一个新的匿名记录定义（就像由一个未命名的 `def`，继承自指定类并传递模板参数），`value` 就是该记录，可以使用后缀获取记录的字段。

```go
**SimpleValue9** ::=  **BangOperator** ["<" **Type** ">"] "(" **ValueListNE** ")"
                 | **CondOperator** "(" **CondClause** ("," **CondClause**)* ")"
**CondClause  ** ::=  **Value** ":" **Value**
```

`bang operators` 提供其他简单值不可用的函数。除 `！cond` 的情况外，`bang operators` 采用括号中的参数列表，并对这些参数执行某些功能，从而为该 `bang operators` 生成一个值。`！cond` 运算符采用由冒号分隔的参数对的列表。Type 仅接受某些 `bang operators`，并且不得为 `code`。

### 1.5.2 Suffixed values

**SimpleValue** 可以使用某些后缀指定，后缀的目的是获取主值的子值，以下是某些主值的可能后缀。

1. 位提取操作（大括号 `{}`）
   1. 单比特提取语法格式为 `value{bit}`。

```
defvar x = 0b10101010;  // 二进制10101010（十进制170）
int bit7 = x{7};        // 提取第7位（最高位），结果1
```

```
defvar x = 0b1111000011110000;  // 16位值
int bits8_15 = x{8...15};       // 提取第8-15位，值为0b11110000
int reversed = x{15...8};       // 反转顺序，值为0b00001111
```

1. 列表切片操作（方括号 `[]`）
   1. 单元素索引语法格式为 `value[i]`，获取列表中第 i 个元素（0-based 索引）。

   ```
   ```

defvar list = [10, 20, 30];
int elem1 = list[1];  // 获取第 1 个元素，结果 20

```
	
	1. 单元素切片语法格式为`value[i,]`，末尾逗号不可省略。

```

defvar list = [10, 20, 30];
list single = list[1,];  // 结果[20]，类型为 list<int>

```



```

defvar nums = [1,2,3,4,5,6,7,8];
list slice = nums[4...7,17,2...3,4];
// 等效于 nums[4], nums[5], nums[6], nums[7], nums[17]（越界忽略）, nums[2], nums[3], nums[4]
// 结果：[5,6,7,8,3,4,5]

```

1. 记录字段访问（点号`.`）`value.field`获取记录值中指定字段的值。

```java
class Reg {
  int Width = 32;
  string Name = "X0";
}

def reg : Reg;
int w = reg.Width;     // 获取Width字段，结果32
string n = reg.Name;   // 获取Name字段，结果"X0"
```

### 1.5.3 The paste operator

粘贴运算符 （`#`） 是 TableGen 表达式中唯一可用的中缀运算符。它允许您连接字符串或列表，但具有一些不寻常的功能。在 **Def** 或 **Defm** 语句，在这种情况下，它必须构造一个字符串。如果作数是未定义的名称 （**TokIdentifier**） 或全局 **Defvar** 或 **Defset** 的名称，则将其视为逐字字符串。不使用全局名称的值。paste 运算符可用于所有其他值表达式，在这种情况下，它可以构造字符串或列表。相当奇怪，但与前一种情况一致，如果右侧操作数是未定义名称或全局名称，则将其视为逐字字符串。左侧操作数被正常处理。<u>附录 B：粘贴运算符示例 </u>提供了粘贴运算符的行为示例。

## 1.6 Statements

以下语句可能出现在 TableGen 源文件的顶层。

```
**TableGenFile** ::=  (**Statement** | **IncludeDirective**
                 | **PreprocessorDirective**)*
**Statement   ** ::=  **Assert** | **Class** | **Def** | **Defm** | **Defset** | **Deftype**
                 | **Defvar** | **Dump**  | **Foreach** | **If** | **Let** | **MultiClass**
```

### 1.6.1 class — define an abstract record class

`class` 语句定义其他类和记录可以从中继承的抽象记录类。

```go
**Class          ** ::=  "class" **ClassID** [**TemplateArgList**] **RecordBody** //ClassID：类名；RecordBody：类的主体
**TemplateArgList** ::=  "<" **TemplateArgDecl** ("," **TemplateArgDecl**)* ">" //[TemplateArgList]：可选的模板参数列表（用<>包裹）
**TemplateArgDecl** ::=  **Type** **TokIdentifier** ["=" **Value**] //TemplateArgDecl：模板参数声明（可多个，用逗号分隔）；Type：参数类型；TokIdentifier：参数名称；["=" Value]：可选的默认值
```

类可以通过“模板参数”列表进行参数化，其值可以在类的记录正文中使用。每次类被另一个类或记录继承时，都会指定这些模板参数。

如果未使用 `=` 为模板参数分配默认值，则该参数未初始化（具有 “value” `？`），并且必须在继承类时在模板参数列表中指定（必需参数）。如果为参数分配了默认值，则无需在参数列表中指定该参数（可选参数）。在声明中，所有必需的模板参数必须位于任何可选参数之前。模板参数默认值从左到右进行评估。

#### 1.6.1.1 Record Bodies

Record Bodies 同时出现在 class 和 record 的定义中。Record Bodies 可以包含父类列表，该列表指定当前 class 或 record 从中继承字段的类。此类类称为类或记录的父类。记录正文还包括定义的主体，该主体包含类或记录的字段的规范。

```go
**RecordBody           ** ::=  **ParentClassList** **Body**
**ParentClassList      ** ::=  [":" **ParentClassListNE**]
**ParentClassListNE    ** ::=  **ClassRef** ("," **ClassRef**)*
**ClassRef             ** ::=  (**ClassID** | **MultiClassID**) ["<" [**ArgValueList**] ">"]
**ArgValueList         ** ::=  **PostionalArgValueList** [","] **NamedArgValueList**
**PostionalArgValueList** ::=  [**Value** {"," **Value**}*]
**NamedArgValueList    ** ::=  [**NameValue** "=" **Value** {"," **NameValue** "=" **Value**}*]
```

包含 **MultiClassID** 的 **ParentClassList** 仅在 `defm` 语句的类列表中有效。在这种情况下，`ID` 必须是 `multiclass` 的名称。

参数值可以以两种形式指定：

- 位置参数：位置参数是按顺序匹配模板参数。

```
class C<int X, string S>;
def R : C<10, "test">;  // X=10, S="test"
```

- 命名参数：命名参数是通过名称匹配模板参数（可打乱顺序）。

```
def R : C<S="test", X=10>;  // 等效于上例
```

当混合使用时，位置参数必须在前，且无论以何种方式（命名或位置）指定，参数都只能指定一次。

```python
class C<int X, int Y, string S>;
def R : C<10, S="test", Y=20>;  // 合法：X=10, Y=20, S="test"
def R : C<10, X=20>;           // 非法：X被重复赋值
```

```go
Body     ::=  ";" | "{" BodyItem* "}"**
**BodyItem ::=  Type TokIdentifier ["=" Value] ";"           // 字段定义**
**             | "let" TokIdentifier ["{" RangeList "}"] "=" Value ";"  // 字段重置**
**             | "defvar" TokIdentifier "=" Value ";"       // 临时变量**
**             | Assert                                      // 断言
```

字段定义规则为：必须显式指定字段类型，`TableGen` 不支持类型推断，未指定初始值的字段为未初始化状态，实例化时需赋值。

```
class Register {
  int Width = 32;    // 初始化字段
  string Name;       // 未初始化字段，需在实例化时赋值
}
```

`let ` 语句可重置当前类或父类的字段，支持对 `bit<n>` 字段的部分比特位重置。例如：

```javascript
def R : Register {
  let Name = "X0";          // 重置字符串字段
  let Width = 64;           // 重置整数字段
  let Flags{0...3} = 0xF;   // 仅重置Flags字段的低4位
}
```

`defvar` 语句仅可定义在记录体内使用的临时变量，不成为 `class / record` 的字段。例如：

```
class Calc {
  defvar temp = 10 + 5;     // 临时变量
  int result = temp * 2;    // 使用临时变量计算
}
```

继承与模板参数展开原则：当类 `C2` 继承自 `C1` 时，`C2` 获取 `C1` 的所有字段定义，且 `C2` 传递给 `C1` 的模板参数会替换 `C1` 定义中的抽象字段，再合并到 `C2` 中。例如：

```java
class C1<int X> {
  int field = X * 2;
}
class C2 : C1<5> {  // 传递X=5
  int newField = field + 10;  // field被展开为10，newField=20
}
```

展开后 `C2` 等效于：

```
class C2 {
  int field = 10;
  int newField = 20;
}
```

### 1.6.2 def — define a concrete record

`def` 语句定义新的具体的 `record`。

```
**Def      ** ::=  "def" [**NameValue**] **RecordBody**  // Def 的语法结构，其中[]的部分为可选部分
**NameValue** ::=  **Value** (parsed in a special mode)
```

`NameValue` 是可选的。如果指定，则以特殊模式解析它，其中未定义（无法识别）的标识符被解释为文本字符串。特别是，全局标识符被视为无法识别。其中包括由 `defvar` 和 `defset` 定义的全局变量。记录名称可以是 null 字符串。

如果 `NameValue` 不存在，则记录为匿名记录。匿名记录的最终名称未指定，但全局唯一。

如果 `def` 出现在 `multiclass` 中，则会发生特殊处理。

`record` 可以通过指定 **ParentClassList** 子句。父类中的所有字段都将添加到记录中。如果两个或多个父类提供相同的字段，则记录将以最后一个父类的字段值结束。

作为特殊情况，`record` 的名称可以作为模板参数传递给该记录的父类。

```
class A <dag d> {
  dag the_dag = d;
} 

def rec1 : A<(ops rec1)>; //定义记录 rec1，它继承自类 A;实例化父类 A 时，传递了模板参数 (ops rec1)。
```

### 1.6.3 Examples : class and reports

下面是一个简单的 `TableGen` 文件，其中包含一个 `class` 和两个 `reports` 定义。

```java
class C {
  bit V = true;
} //定义了class C；有bit的变量v，初始化状态为true；

def X : C;
def Y : C {
  let V = false;
  string Greeting = "Hello!";
} //使用 C 作为它们的父类。因此它们都继承了 V 字段。Y 会覆盖继承的 V 字段，将其设置为 false。
//record Y 还定义了另一个string类型的变量Greeting，该字段初始化为 “Hello！” 的字段。
```

`Class` 可用于将 `multiple records` 的公共功能隔离在一个位置。类可以将公共字段初始化为默认值，但从该类继承的记录可以覆盖默认值。TableGen 支持参数化类和非参数化类的定义。参数化类指定变量声明列表，这些声明可以选择具有默认值，当 `class` 被指定为另一个 `class` 或 `record` 的父类时，这些声明将被参数实例化。

```python
class FPFormat <bits<3> val> {
  bits<3> Value = val;
}

def NotFP      : FPFormat<0>;
def ZeroArgFP  : FPFormat<1>;
def OneArgFP   : FPFormat<2>;
def OneArgFPRW : FPFormat<3>;
def TwoArgFP   : FPFormat<4>;
def CompareFP  : FPFormat<5>;
def CondMovFP  : FPFormat<6>;
def SpecialFP  : FPFormat<7>;
```

`FPFormat` 类的用途是充当一种枚举类型。它提供一个变量 `Value`，Value 包含一个 3 位数字。其模板参数 `val` 用于设置 `Value` 。八条记录中的每一条都使用 `FPFormat` 作为其父类进行定义。枚举值作为模板参数在尖括号中传递。每条记录都将在 `Value` 字段中固有相应的枚举值。

下面是一个更复杂的带有模板参数的类示例。首先，我们定义一个类似于上面的 `FPFormat` 类的类。它接受一个模板参数，并使用它来初始化一个名为 `Value` 的。然后，我们定义四个 `records`，这些 `records` 继承 `Value` 字段及其四个不同的整数值。

```
class ModRefVal <bits<2> val> {
  bits<2> Value = val;
}

def None   : ModRefVal<0>;
def Mod    : ModRefVal<1>;
def Ref    : ModRefVal<2>;
def ModRef : ModRefVal<3>;
```

假设我们想独立检查 `Value` 字段的两个位。我们可以定义一个类，该类接受 `ModRefVal` 记录作为模板参数，并将其 value 转换为两个字段，每个字段 1 位。然后我们可以定义继承自 `ModRefBits 的 Bits` 中获取两个字段，一个字段对应 作为模板参数传递的 `ModRefVal` 记录。

```java
class ModRefBits <ModRefVal mrv> {
  // Break the value up into its bits, which can provide a nice
  // interface to the ModRefVal values.
  bit isMod = mrv.Value{0};
  bit isRef = mrv.Value{1};
}

// Example uses.
def foo   : ModRefBits<Mod>;
def bar   : ModRefBits<Ref>;
def snork : ModRefBits<ModRef>;

//使用llvm-tblge打印输出结果
def bar {      // Value
  bit isMod = 0;
  bit isRef = 1;
}
def foo {      // Value
  bit isMod = 1;
  bit isRef = 0;
}
def snork {      // Value
  bit isMod = 1;
  bit isRef = 1;
}
```

### 1.6.4 let — override fields in classes or records

`let` 语句收集一组字段值（有时称为 `bindings`）并将它们应用于 `let` 范围内语句定义的所有 `class` 和 `record`。let 语句的核心作用就是临时修改 class 和 record 的字段值。

```go
**Let    ** ::=   "let" **LetList** "in" "{" **Statement*** "}"
            | "let" **LetList** "in" **Statement**
**LetList** ::=  **LetItem** ("," **LetItem**)*
**LetItem** ::=  **TokIdentifier** ["<" **RangeList** ">"] "=" **Value**  //TokIdentifier：变量名（如 x, max_value）["<" RangeList ">"]：可选的范围约束
```

**LetList** 中的字段名称必须命名语句中定义的 `class` 和 `record` 继承的 `class` 中的字段。在记录从其父类继承所有字段后 ，字段值将应用于 `class` 和 `record`。因此，`let` 的作用是覆盖继承的字段值。`let` 不能覆盖模板参数的值。

当需要在多个记录中覆盖几个字段时，顶层 `let` 语句通常很有用。下面是两个示例。请注意，`let` 语句可以嵌套。

```bash
let isTerminator = true, isReturn = true, isBarrier = true, hasCtrlDep = true in
  def RET : I<0xC3, RawFrm, (outs), (ins), "ret", [(X86retflag 0)]>;

let isCall = true in
  // All calls clobber the non-callee saved registers...
  let Defs = [EAX, ECX, EDX, FP0, FP1, FP2, FP3, FP4, FP5, FP6, ST0,
              MM0, MM1, MM2, MM3, MM4, MM5, MM6, MM7, XMM0, XMM1, XMM2,
              XMM3, XMM4, XMM5, XMM6, XMM7, EFLAGS] in {
    def CALLpcrel32 : Ii32<0xE8, RawFrm, (outs), (ins i32imm:$dst, variable_ops),
                           "call\t${dst:call}", []>;
    def CALL32r     : I<0xFF, MRM2r, (outs), (ins GR32:$dst, variable_ops),
                        "call\t{*}$dst", [(X86call GR32:$dst)]>;
    def CALL32m     : I<0xFF, MRM2m, (outs), (ins i32mem:$dst, variable_ops),
                        "call\t{*}$dst", []>;
  }
```

请注意，顶层 `let` 不会覆盖 `class` 或 `record` 本身中定义的字段。顶层 `let` 的作用域：仅影响未在 `class/ record` 自身显式定义的字段。优先级顺序：记录自身定义的字段 > 顶层 `let` > 类默认值。

### 1.6.5 multiclass — define multiple records

虽然带有模板参数的 `class` 是消除多条记录之间通用性的好方法，但 `multiclass` 允许一种一次定义多条记录的便捷方法。例如，考虑一个 3 地址的指令体系结构，其指令有两种格式：`reg = reg op reg` 和 `reg = reg op imm`（例如，SPARC）。我们想在一个地方指定这两种常见格式的存在，然后在另一处指定所有操作是什么。`multiclass` 和 `defm` 语句实现此目标。您可以将 `multiclass` 视为宏或扩展为多条 `record` 的模板。

```
**MultiClass         ** ::=  "multiclass" **TokIdentifier** [**TemplateArgList**]
                         **ParentClassList**
                         "{" **MultiClassStatement**+ "}"
**MultiClassID       ** ::=  **TokIdentifier****MultiClassStatement** ::=  **Assert** | **Def** | **Defm** | **Defvar** | **Foreach** | **If** | **Let**
```

与常规类一样，`multiclass` 具有名称并且可以接受模板参数。`multiclass` 可以从其他 `multiclasses` 继承，这会导致其他 `multiclasses` 被扩展并有助于继承 `multiclass` 中的记录定义。`multiclass` 的主体包含一系列定义记录的语句，使用 **Def** ，**Defm** ，`Def` 和 **Let** 语句可用于分解出更常见的元素，也可以使用 **If** 和 **Assert** 语句。

同样与常规类一样，multiclass 具有隐式模板参数 `NAME`。当在 `multiclass` 中定义命名（非匿名）记录并且记录的名称不包括模板参数 `NAME` 的使用时，会自动将此类使用添加到前面添加到名称中。也就是说，以下内容在 multiclass 中是等效的：

```
**def** Foo ...
**def** NAME _# Foo ..._
```

当多类被多类定义外部的 `defm` 语句“实例化”或“调用”时，将创建多类中定义的记录。多类中的每个 `def` 语句都会生成一条记录。与顶层 `def` 语句一样，这些定义可以从多个父类继承。

### 1.6.6 defm — invoke multiclasses to define multiple records

定义多类后，您可以使用 `defm` 语句“调用”它们并处理这些 `multiple` 中的多个记录定义。这些记录定义由 `def` 指定语句，以及间接通过 `defm` 语句。

```
**Defm** ::=  "defm" [**NameValue**] **ParentClassList** ";" //[**NameValue**]这部分是可选的
```

**ParentClassList** 是一个冒号，后跟至少一个 multiclass 和任意数量的 `regular` 类的列表。`multiclasses` 必须位于常规类之前。请注意，`defm` 没有 body。此语句直接通过 `def` 语句或通过 `defm` 语句。这些 `record` 还接收父类列表中包含的任何常规类中定义的字段。这对于向 `defm` 创建的所有记录添加一组通用字段非常有用。

该名称以 `def` 使用的相同特殊模式进行解析。如果不包含该名称，则提供未指定但全局唯一的名称。也就是说，以下示例以不同的名称结束：

```
defm    : SomeMultiClass<...>;   // A globally unique name.
defm "" : SomeMultiClass<...>;   // An empty name.
```

`defm` 语句可以在多类主体中使用。发生这种情况时，第二个变体等效于：

```
defm NAME : SomeMultiClass<...>;
```

更一般地说，当 `defm` 出现在多类中并且其名称不包括隐式模板参数 `NAME` 的使用时，将自动预置 `NAME`。也就是说，以下内容在多类中是等效的：

```
defm Foo        : SomeMultiClass<...>;
defm NAME _# Foo : SomeMultiClass<...>;_
```

### 1.6.7 Examples: multiclasses and defms

下面是一个使用 `multiclass` 和 `defm` 的简单示例。考虑一个 3 地址指令架构，其指令有两种格式： `reg = reg op reg` 和 `reg = reg op imm`。SPARC 就是这种体系结构的一个示例。

```bash
def ops; // 定义操作符类型
def GPR; // 定义通用寄存器类型
def Imm; // 定义立即数类型
class inst <int opc, string asmstr, dag operandlist>; // 定义指令基类：包含操作码、汇编字符串和操作数列表

multiclass ri_inst <int opc, string asmstr> {
  def _rr : inst<opc, !strconcat(asmstr, " $dst, $src1, $src2"),
                   (ops GPR:$dst, GPR:$src1, GPR:$src2)>;
  def _ri : inst<opc, !strconcat(asmstr, " $dst, $src1, $src2"),
                   (ops GPR:$dst, GPR:$src1, Imm:$src2)>;
}

// Define records for each instruction in the RR and RI formats.
defm ADD : ri_inst<0b111, "add">;
defm SUB : ri_inst<0b101, "sub">;
defm MUL : ri_inst<0b100, "mul">;
```

每次使用 `ri_inst` 多类都会定义两条记录，一条记录的 `_rr` 后缀和一个带有 `_ri` 的后缀。回想一下，`defm` 的名称 的 MultiClass 的 that multiclass 的 MultiClass 中。因此，生成的定义被命名为：

```
ADD_rr, ADD_ri
SUB_rr, SUB_ri
MUL_rr, MUL_ri
```

如果没有 `multiclass` 功能，则必须按如下方式定义说明。

```python
def ops;
def GPR;
def Imm;
class inst <int opc, string asmstr, dag operandlist>;

class rrinst <int opc, string asmstr>
  : inst<opc, !strconcat(asmstr, " $dst, $src1, $src2"),
           (ops GPR:$dst, GPR:$src1, GPR:$src2)>;

class riinst <int opc, string asmstr>
  : inst<opc, !strconcat(asmstr, " $dst, $src1, $src2"),
           (ops GPR:$dst, GPR:$src1, Imm:$src2)>;

// Define records for each instruction in the RR and RI formats.
def ADD_rr : rrinst<0b111, "add">;
def ADD_ri : riinst<0b111, "add">;
def SUB_rr : rrinst<0b101, "sub">;
def SUB_ri : riinst<0b101, "sub">;
def MUL_rr : rrinst<0b100, "mul">;
def MUL_ri : riinst<0b100, "mul">;
```

可以在多类中使用 `defm` 来“调用”其他多类，并创建这些多类中定义的记录以及当前多类中定义的记录。在以下示例中，`basic_s basic_p MultiClasses ` 都包含引用 ` basic_r multiclass` 中。`basic_r multiclass` 仅包含 `def` 语句。

```typescript
class Instruction <bits<4> opc, string Name> {
  bits<4> opcode = opc;
  string name = Name;
}
//basic_r 生成两种指令格式（寄存器 - 寄存器、寄存器 - 内存）
multiclass basic_r <bits<4> opc> {
  def rr : Instruction<opc, "rr">;
  def rm : Instruction<opc, "rm">;
}

multiclass basic_s <bits<4> opc> {
  defm SS : basic_r<opc>;
  defm SD : basic_r<opc>;
  def X : Instruction<opc, "x">;
}

multiclass basic_p <bits<4> opc> {
  defm PS : basic_r<opc>;
  defm PD : basic_r<opc>;
  def Y : Instruction<opc, "y">;
}

defm ADD : basic_s<0xf>, basic_p<0xf>;
```

最终的 `defm` 创建以下记录，其中 5 条记录来自 `basic_s multiclass` ，5 条记录来自 `basic_p multiclass`。

```
ADDSSrr, ADDSSrm
ADDSDrr, ADDSDrm
ADDX
ADDPSrr, ADDPSrm
ADDPDrr, ADDPDrm
ADDY
```

除了 `multiclasses` 之外，顶层和多类中的 `defm` 语句还可以从常规类继承。规则是常规类必须在 multiclasses 之后列出，并且必须至少有一个 `multiclass`。

```javascript
class XD {
  bits<4> Prefix = 11;
}
class XS {
  bits<4> Prefix = 12;
}
class I <bits<4> op> {
  bits<4> opcode = op;
}

multiclass R {
  def rr : I<4>;
  def rm : I<2>;
}

multiclass Y {
  defm SS : R, XD;    // First multiclass R, then regular class XD.
  defm SD : R, XS;
}

defm Instr : Y;
```

此示例将创建四条记录，此处按字母顺序显示其字段。

```
def InstrSDrm {
  bits<4> opcode = { 0, 0, 1, 0 };
  bits<4> Prefix = { 1, 1, 0, 0 };
}

def InstrSDrr {
  bits<4> opcode = { 0, 1, 0, 0 };
  bits<4> Prefix = { 1, 1, 0, 0 };
}

def InstrSSrm {
  bits<4> opcode = { 0, 0, 1, 0 };
  bits<4> Prefix = { 1, 0, 1, 1 };
}

def InstrSSrr {
  bits<4> opcode = { 0, 1, 0, 0 };
  bits<4> Prefix = { 1, 0, 1, 1 };
}
```

也可以在 `multiclasses ` 中使用 `let` 语句，从而提供另一种从记录中排除共性的方法，尤其是在使用多个级别的 `multiclass` 实例化时。

```javascript
multiclass basic_r <bits<4> opc> {
  let Predicates = [HasSSE2] in {
    def rr : Instruction<opc, "rr">;
    def rm : Instruction<opc, "rm">;
  }
  let Predicates = [HasSSE3] in
    def rx : Instruction<opc, "rx">;
}

multiclass basic_ss <bits<4> opc> {
  let IsDouble = false in
    defm SS : basic_r<opc>;

  let IsDouble = true in
    defm SD : basic_r<opc>;
}

defm ADD : basic_ss<0xf>;
```

### 1.6.8 defset — create a definition set

`defset` 语句用于将一组 `record` 收集到全局记录列表中。

```
**Defset** ::=  "defset" **Type** **TokIdentifier** "=" "{" **Statement*** "}"
```

通过 `def` 和 `defm` 在大括号内定义的所有记录都照常定义，并且它们也被收集在给定名称 （**TokIdentifier）** 的全局列表中。

指定的类型必须是 `list<` _class_ `>`，其中_class_ 是某个记录类。 `defset` 语句可以嵌套。内部 `defset` 将记录添加到自己的集中，所有这些记录也添加到外部集中。

在初始化表达式中使用 `ClassID<...>` 语法未收集到集合中。

### 1.6.9 deftype — define a type

`deftype` 语句定义类型。该类型可以在定义后面的整个语句中使用。

```
**Deftype** ::=  "deftype" **TokIdentifier** "=" **Type** ";"
```

`=` 左侧的标识符定义为类型名称，实际类型由 `=` 右侧的类型表达式给出。

目前，仅支持将原始类型和类型别名作为源类型，_ _并且 `deftype` 语句只能出现在顶层。

### 1.6.10 defvar — define a variable

`defvar` 语句定义全局变量。其值可用于定义后面的整个语句。

```
**Defvar** ::=  "defvar" **TokIdentifier** "=" **Value** ";"
```

`=` 左侧的标识符定义为全局变量，其值由 `=` 右侧的值表达式给出，变量的类型会自动推断。

定义变量后，不能将其设置为其他值。

在顶级 `foreach` 中定义的变量在每次循环迭代结束时超出范围，因此它们在一次迭代中的值在下一次迭代中不可用。以下 `defvar` 将不起作用：

```
defvar i = !add(i, 1); //defvar 用于全局变量只能在文件顶层定义，不能在循环内修改。
```

还可以在 `record` 正文中使用 `defvar` 定义变量。

### 1.6.11 foreach — iterate over a sequence of statements

`foreach` 语句循环访问一系列语句，在一系列值上改变变量。

```go
**Foreach        ** ::=  "foreach" **ForeachIterator** "in" "{" **Statement*** "}"
                    | "foreach" **ForeachIterator** "in" **Statement**
**ForeachIterator** ::=  **TokIdentifier** "=" ("{" **RangeList** "}" | **RangePiece** | **Value**)
```

`foreach` 的主体是一系列带大括号的语句或没有大括号的单个语句。对于范围列表、范围片段或单个值中的每个值，这些语句将重新计算一次。在每次迭代中，**TokIdentifier** 变量将设置为该值，并且可以在语句中使用。

Foreach 循环可以嵌套，且每次迭代创建新变量，迭代结束后变量销毁。

```
foreach i = [0, 1, 2, 3] in {
  def R#i : Register<...>;
  def F#i : Register<...>;
}
```

此循环定义名为 `R0`、`R1`、`R2` 和 `R3` 以及 `F0`、`F1`、`F2` 和 `F3` 的 `record`。

### 1.6.12 dump — print messages to stderr

`dump` 语句将 `input` 字符串打印到标准错误输出。它用于调试目的。该语句有两个作用：在顶层，将立即打印消息；在 `record/class/multiclass` 中，`dump` 在包含 `record` 的每个实例化点进行评估。

```
**Dump** ::=  "dump" **Value** ";"
```

**Value** 是任意字符串表达式。例如，它可以与 `！repr` 结合使用，以调查传递给多类的值：

```
multiclass MC<dag s> {
  dump "s = " # !repr(s);
}
```

### 1.6.13 if — select statements based on a test

`if` 语句是判断语句，它根据表达式的值，选择相应的两个语句组之一。

```go
**If    ** ::=  "if" **Value** "then" **IfBody**
           | "if" **Value** "then" **IfBody** "else" **IfBody**
**IfBody** ::=  "{" **Statement*** "}" | **Statement**
```

对 `value` 表达式进行计算。如果计算结果为 `true`，则 `then` 处理 `reserved word`。否则，如果存在 `else` ，则处理 `else` 后面的语句。如果值为 `false` 且没有 `else` ，则不处理任何语句。

由于 `then` 语句周围的大括号是可选的，因此此语法规则与 “dangling else” 子句具有通常的歧义，并且以通常的方式解决：在类似 `if v1 then if v2 then {...} else {...} then ` 语句的情况下， `else` 与内部 `if ` 关联，而不是与外部 `if` 关联。

```
if v1 then 
  if v2 then 
    def A : Inst; 
  else 
    def B : Inst; //这个else是与if v2 then关联的
```

`if` 语句也可以在 `record ` **Body** ` ` 中使用。

### 1.6.14 assert — check that a condition is true

`assert` 语句检查布尔条件以确保它为 `true`，如果不是，则打印错误消息。

```
**Assert** ::=  "assert" **Value** "," **Value** ";"
```

第一个 **Value** 是布尔条件。如果为 true，则语句不执行任何作。如果条件为 false，则打印非致命错误消息。第二个 **Value** 是一条消息，可以是任意字符串表达式。它作为注释包含在错误消息中。`assert` 语句的确切行为取决于其位置。

在顶层，会立即检查 `assertion`；在 `record` 定义中，将保存语句，并在完全构建 `record` 后检查所有 `assertion`；在类定义中，`assertion` 由从继承的所有子类和记录保存和继承，当记录完全构建时，将检查 `assertion`；在多类定义中，`assertion` 与 `muliticlass` 的其他组件一起保存，然后每次使用 `defm` 实例化多类时进行检查。

在 `TableGen` 文件中使用 `assertion` 可以简化 `TableGen` 后端中的记录检查。下面是两个类定义中的 `assertion` 示例：

```python
class PersonName<string name> {
  assert !le(!size(name), 32), "person name is too long: " # name;!le(a, b)：判断 a ≤ b
  string Name = name;
}

class Person<string name, int age> : PersonName<name> {
  assert !and(!ge(age, 1), !le(age, 120)), "person age is invalid: " # age;!ge(a, b)：判断 a ≥ b
  int Age = age;
}

def Rec20 : Person<"Donald Knuth", 60> {
  ...
}
```

## 1.7 Additional Details

### 1.7.1 Directed acyclic graphs（DAGs）

有向无环图可以直接在 TableGen 中使用 `DAG` 数据类型。`DAG` 节点由一个运算符和零个或多个参数（或作数）组成。每个参数可以是任何所需的类型。通过使用另一个 `DAG` 节点作为参数，可以构建 `DAG` 节点的任意图形。

`dag` 实例的语法为：`( ` _operator_ ` ` _argument1_ `, ` _argument2_ `, … )`，运算符必须存在，并且必须是 `record`，可以有零个或多个参数，用逗号分隔。

运算符和参数可以有三种格式：

`Value` 可以是任何 `TableGen` 值。_ _`name`（如果存在）必须是 **TokVarName**，以美元符号 （`$`） 开头。名称的目的是在 `DAG` 中标记具有特定含义的运算符或参数，或者将一个 `DAG` 中的参数与另一个 `DAG` 中名称相似的参数相关联。

以下 bang 运算符对于使用 `DAG` 非常有用：`！con，！dag，！empty，！foreach，！getdagarg，！getdagname，！getdagop，！setdagarg，！setdagname，！setdagop，！size`.

### 1.7.2 Defvar in a record body

除了定义全局变量之外，`defvar` 语句还可以在类或记录定义的 **Body** 中使用来定义局部变量。`class` 或 `multiclass` 的模板参数可以是在 `value` 表达式中使用。变量的范围从 `defvar` 语句添加到正文的末尾。不能在其范围内将其设置为其他值。`defvar` 语句还可以在 `foreach` 的语句列表中使用，从而建立范围。

TableGen 中变量作用域的隐藏规则，即内层作用域的同名变量会屏蔽外层作用域的同名变量，规则如下：

- 记录体隐藏全局变量

```java
defvar GlobalV = 10;  // 全局变量

def Record {
  int V = 20;  // 记录体内层作用域定义同名变量
  int UseV = V;  // 使用内层变量 (20)
}

// 全局作用域中，GlobalV 仍为 10
```

- 记录体隐藏模板参数

```java
class C<int V> {  // 模板参数 V
  let V = 99 in {  // 记录体中使用let覆盖V，隐藏模板参数
    int UseV = V;  // 使用覆盖后的V (99)
  }
}

def Inst : C<5>;  // 实例化时传递 V=5，但被内部 let 覆盖
```

- 模板参数隐藏全局变量

```java
defvar GlobalV = 100;  // 全局变量

class C<int GlobalV> {  // 模板参数隐藏全局V
  int UseV = GlobalV;  // 使用模板参数 (由实例化决定)
}

def Inst : C<5>;  // 实例化时，UseV = 5 (而非全局的100)
```

- foreach 隐藏外部变量

```java
defvar GlobalV = 5;  // 全局变量

def Record {
  int OuterV = 10;  // 记录级变量
  
  foreach OuterV = [1, 2, 3] in {  // foreach隐藏外部OuterV
    int UseV = OuterV;  // 使用foreach中的OuterV (1/2/3)
  }
}
```

在 `foreach` 中定义的变量在每次循环迭代结束时超出范围，因此它们在一次迭代中的值在下一次迭代中不可用。以下 `defvar` 将不起作用：

```
defvar i = !add(i, 1)
```

### 1.7.3 How records are built

在构建记录时，`TableGen` 将执行以下步骤。类只是抽象记录，因此执行相同的步骤。

1. 构建记录名称 （**NameValue**） 并创建一个空记录。
2. 从左到右解析 **ParentClassList** 中的父类，从上到下访问每个父类的祖先类。

   1. 将父类中的字段添加到记录中。
   2. 将模板参数替换为这些字段。
   3. 将父类添加到记录的继承类列表中。
3. 将任何顶层 `let` 绑定应用于 `record`。顶层 `let` 绑定仅适用于继承的字段。
4. 解析 `record` 的正文。

   1. 将任何字段添加到记录中。
   2. 根据本地 `let` 语句修改字段的值。
   3. 定义任何 `defvar` 变量。
5. 遍历所有字段以解析任何字段间引用。
6. 将记录添加到最终记录列表中。

由于在应用 `let` 绑定（步骤 3）后解析字段之间的引用（步骤 5）， 因此 `let` 语句具有不寻常的功能。 例如：

```python
class C <int x> {
  int Y = x;
  int Yplus1 = !add(Y, 1);
  int xplus1 = !add(x, 1);
}

let Y = 10 in {
  def rec1 : C<5> {
  }
}

def rec2 : C<5> {
  let Y = 10;
}
```

在这两种情况下，一种是使用顶层 `let` 来绑定 `Y`，另一种是本地 `let` 执行相同的操作，结果是：

```python
def rec1 {      // C
  int Y = 10;
  int Yplus1 = 11;
  int xplus1 = 6;
}
def rec2 {      // C
  int Y = 10;
  int Yplus1 = 11;
  int xplus1 = 6;
}
```

## 1.8 Using Classes as Subroutines

如 `Simple values` 中所述，可以在表达式中调用类并传递模板参数。这会导致 TableGen 创建从该类继承的新匿名记录，而且 `record` 接收类中定义的所有字段，此功能可以用作简单的子例程工具。

该类可以使用模板参数来定义各种变量和字段，这些变量和字段最终位于匿名记录中。然后，可以在调用该类的表达式中检索这些字段，如下所示，假设字段 `ret` 包含子例程的最终值。

```
int Result = ... CalcValue<arg>.ret ...;
```

`CalcValue` 类使用模板参数 `arg` 调用。它计算 `ret` 字段的值，然后在 `Result` 字段初始化的“point of call”检索该值。在此示例中创建的匿名记录除了携带 `result` 值之外，没有其他用途。

下面是一个实际示例，类 `isValidSize` 确定指定的字节数是否表示有效的数据大小。`bit ret` 设置得当。字段 `ValidSize` 通过调用具有数据大小的 `isValidSize` 并从生成的匿名记录中检索 `ret` 字段来获取其初始值。

```yaml
class isValidSize<int size> {
  bit ret = !cond(!eq(size,  1): 1,   //！cond为多路条件判断；！eq判断相等；该句意思为：如果 size == 1，返回 1 (true)
                  !eq(size,  2): 1,
                  !eq(size,  4): 1,
                  !eq(size,  8): 1,
                  !eq(size, 16): 1,
                  true: 0);
}

def Data1 {
  int Size = ...;
  bit ValidSize = isValidSize<Size>.ret;
}
```

## 1.9 Preprocessing Facilities

嵌入在 ` TableGen` 中的预处理器仅用于简单的条件编译。它支持以下指令，这些指令在某种程度上是非正式指定的。

```go
**LineBegin             ** ::=  beginning of line //行的开始位置
**LineEnd               ** ::=  newline | return | EOF  //行的结束（换行符、回车符或文件结束符）
**WhiteSpace            ** ::=  space | tab  //空格或制表符
**CComment              ** ::=  "/*" ... "*/"  //C 风格注释（/* ... */）
**BCPLComment           ** ::=  "//" ... **LineEnd** //BCPL 风格注释（// ...）
**WhiteSpaceOrCComment  ** ::=  **WhiteSpace** | **CComment**  //空格或 C 风格注释
**WhiteSpaceOrAnyComment** ::=  **WhiteSpace** | **CComment** | **BCPLComment** //空格或任意类型注释
**MacroName             ** ::=  **ualpha** (**ualpha** | "0"..."9")* //宏名称（以大写字母开头，后面可跟字母或数字）
**PreDefine             ** ::=  **LineBegin** (**WhiteSpaceOrCComment**)* 
                            "#define" (**WhiteSpace**)+ **MacroName**
                            (**WhiteSpaceOrAnyComment**)* **LineEnd**
**PreIfdef              ** ::=  **LineBegin** (**WhiteSpaceOrCComment**)* //
                            ("#ifdef" | "#ifndef") (**WhiteSpace**)+ **MacroName**
                            (**WhiteSpaceOrAnyComment**)* **LineEnd**
**PreElse               ** ::=  **LineBegin** (**WhiteSpaceOrCComment**)*
                            "#else" (**WhiteSpaceOrAnyComment**)* **LineEnd**
**PreEndif              ** ::=  **LineBegin** (**WhiteSpaceOrCComment**)*
                            "#endif" (**WhiteSpaceOrAnyComment**)* **LineEnd**
```

可以在 TableGen 文件中的任何位置定义 **MacroName**。名称没有值;只能测试它以查看它是否已定义。

宏测试区域以 `#ifdef` 或 `#ifndef` 指令开头。如果宏名称是 defined （`#ifdef`） 或 undefined （`#ifndef`），则指令与相应 `#else` 之间的源代码或 `#endif` 将被处理。如果测试失败，但存在 `#else` 子句，则处理 `#else` 和 `#endif` 之间的源代码。如果测试失败且没有 `#else` 子句，则不会处理测试区域中的源代码。

测试区域可以嵌套，但必须正确嵌套。`Region` 必须在一个文件中开始且结束，所以一定具有 `#endif`。
