# 量化部分（python/calibration）

1\.文件结构说明

### calibration/ 主目录（共13个核心文件）

## utils\.py

这段代码主要定义了芯片与浮点类型的映射关系，以及一些辅助工具函数，用于处理模型量化过程中与芯片类型、操作融合相关的逻辑。

### `FLOAT_MAP`浮点类型映射表：

```JSON
FLOAT_MAP = {
    "bm1684x": "F16",
    "bm1684": "F32",
    "cv183x": "BF16",
    "cv182x": "BF16",
    "cv181x": "BF16",
    "cv180x": "BF16",
    "bm1688": "F16",
    "cv186x": "F16",
    "bm1690": "F16",
    "mars3":  "BF16"
}
```

### `chip_support_mix_fp_type`芯片支持的混合精度类型

```JSON
chip_support_mix_fp_type = {
    "bm1684x": ["F16", "F32"],
    "bm1688": ["F16", "F32"],
    "cv186x": ["F16", "F32"],
    "bm1684": ["F32"],
    "cv183x": ["BF16"],
    "cv182x": ["BF16"],
    "cv181x": ["BF16"],
    "cv180x": ["BF16"],
    "mars3":  ["BF16"]
}
```

主要用于字符串处理和正则匹配，用于解析量化工具中常见的操作名称和参数格式:

```Python
import re

def parse_method_list(input_str):
    """
    将以逗号分隔的方法字符串拆分为列表。
    用于解析用户输入的多个方法名称（如量化损失计算方法）。
    
    Args:
        input_str: 以逗号分隔的方法字符串（如 "cos,sqnr,snr"）
        
    Returns:
        list: 拆分后的方法列表（如 ["cos", "sqnr", "snr"]）
    """
    return input_str.split(',')

def is_fuseop(op_name):
    """
    判断操作名称是否为融合操作格式（如 fused["Conv,Relu"]）。
    融合操作指多个基础操作被合并为一个复合操作的情况。
    
    Args:
        op_name: 操作名称字符串
        
    Returns:
        bool: 若为融合操作格式则返回 True，否则返回 False
    """
    # 正则匹配规则：以"fused["开头，中间是双引号包裹的内容，以"]"结尾
    return re.match(r'^fused\[".*?"\]$', op_name)

def split_fuseop(op_name):
    """
    从融合操作名称中提取被包裹的原始操作字符串。
    非融合操作则直接返回原名称。
    
    Args:
        op_name: 操作名称字符串（可能是融合操作或普通操作）
        
    Returns:
        str: 若为融合操作，返回双引号内的原始操作字符串（如 "Conv,Relu"）；
             否则返回原操作名称
    """
    if is_fuseop(op_name):
        # 正则提取双引号内的内容（排除双引号本身）
        new_ops = re.findall(r'"([^"]+)"', op_name)
        return new_ops[0]  # 返回第一个匹配结果（融合操作通常仅包含一组）
    else:
        return op_name
```

## data\_selector\.py

`DataSelector` 类的核心逻辑可抽象为「**数据来源加载 → 数据筛选与打乱 → 格式合法性校验 → 数据导出**」四大核心流程，聚焦 “统一输入数据管理”，屏蔽不同数据格式、来源的差异，确保输出符合模型要求的标准化数据列表。

### 一、核心逻辑抽象（分层拆解）

1. 数据来源适配层：多渠道加载数据

- 核心目标：支持两种输入来源，兼容 “直接读目录” 和 “读现成列表” 场景

- 逻辑规则：

    - 优先加载 `data_list_file`（文件每行一个数据项），支持按 `num` 截取前 N 条

    - 无列表文件时，从 `dataset` 目录（含子目录）扫描数据

    - 必须指定任一来源，否则报错；加载后数据列表不能为空

2. 数据筛选与排序层：标准化候选数据

- 核心目标：从目录中筛选有效数据，保证选择结果可复现

- 逻辑规则：

    - 筛选规则：仅保留 `npz`、`npy`、图片（jpg/bmp/png/jpeg/jfif）格式文件

    - 排序与打乱：先对文件路径排序，再用固定种子（1684）打乱，确保每次运行结果一致

    - 数量控制：`num=0` 取全部数据，`num>0` 取前 N 条（若 N 超过总数则取全部）

3. 格式校验层：严格保证数据合法性

- 核心目标：避免混合格式导致模型输入异常，仅允许单一类型数据

- 逻辑规则：

    - 格式识别：通过文件后缀（不区分大小写）判断类型（npz/npy/ 图片）

    - 多输入处理：非 npz 格式支持逗号分隔多文件（如 `a.npy,b.npy`），需逐个校验

    - 类型唯一性：同一批数据只能是一种类型（npz /npy/ 图片），混合类型直接报错

    - 非法拦截：存在不支持的文件格式时，抛出异常并提示非法文件

4. 数据导出层：输出标准化列表

- 核心目标：将筛选后的有效数据列表持久化，方便后续使用

- 逻辑规则：将 `data_list`（每个元素为单个数据项 / 逗号分隔多数据项）按 “一行一个” 写入目标文件

### 二、核心约束与设计原则

1. 格式约束：仅支持 3 类输入格式，且单一批次仅允许一种类型

2. 兼容性：文件名大小写不敏感（通过 `lower()` 统一处理）

3. 可复现性：目录选数据时固定随机种子，避免每次运行结果不一致

4. 容错性：加载前校验数据非空，加载中拦截非法格式，及时抛出明确异常

### 三、代码解析

```Python
#!/usr/bin/env python3
# ==============================================================================
#
# Copyright (C) 2022 Sophgo Technologies Inc.  All rights reserved.
#
# TPU-MLIR is licensed under the 2-Clause BSD License except for the
# third-party components.
#
# ==============================================================================

import random
import pathlib

# 支持的输入数据格式场景说明：
# case 0: 单输入 + jpg格式（如 a.jpg、b.jpg）
# case 1: 单输入 + npy格式（如 a.npy、b.npy）
# case 2: 单/多输入 + npz格式（如 a.npz、b.npz，npz本身可包含多个数组）
# case 3: 多输入 + 多个npy格式（用逗号分隔，如 a.npy,b.npy、c.npy,d.npy）
# case 4: 多输入 + 多个图片格式（用逗号分隔，如 a.jpg,b.jpg、c.jpg,d.jpg）


class DataSelector:
    """
    数据选择器类，用于加载、筛选和验证模型输入数据。
    支持从数据集目录随机选择数据，或从列表文件读取数据，同时校验输入格式合法性。
    """

    def __init__(self, dataset: str, num: int = 0, data_list_file: str = None):
        """
        初始化数据选择器，加载输入数据列表。
        优先从 data_list_file 读取数据，若未指定则从 dataset 目录随机选择。

        Args:
            dataset: 数据集目录路径（当 data_list_file 未指定时使用）
            num: 选择的数据数量（0 表示选择全部数据，默认 0）
            data_list_file: 数据列表文件路径（文件中每行存储一个输入数据项，可选）

        Raises:
            RuntimeError: 未指定 dataset 且未提供 data_list_file，或加载后数据列表为空
        """
        self.data_list = []  # 存储最终筛选后的输入数据列表（每个元素为单个数据项/逗号分隔的多数据项）

        # 场景1：从数据列表文件读取数据
        if data_list_file:
            with open(data_list_file, 'r') as f:
                # 读取文件所有行，去除每行首尾空格/换行符后加入列表
                for line in f.readlines():
                    self.data_list.append(line.strip())
            # 若指定了数量且小于列表长度，截取前 num 个数据
            if num > 0 and num < len(self.data_list):
                self.data_list = self.data_list[:num]
        
        # 场景2：从数据集目录随机选择数据
        elif dataset:
            self.data_list = self._random_select(dataset, num)
        
        # 异常场景：未指定任何数据来源
        else:
            raise RuntimeError("Please specific dataset path by --dataset")
        
        # 校验数据列表非空
        if len(self.data_list) == 0:
            raise RuntimeError("There is no inputs")

        # 标记数据类型（是否全为 npz/npy/图片格式）
        self.all_npz, self.all_npy, self.all_image = False, False, False
        # 校验数据列表格式合法性
        self._check_data_list()

    def _check_data_list(self):
        """
        校验数据列表中所有文件的格式合法性，确保仅包含一种输入类型（npz/npy/图片）。

        Raises:
            RuntimeError: 存在不支持的文件格式，或同时包含多种输入类型
        """
        for file in self.data_list:
            # 处理 npz 格式（单个 npz 文件视为一个数据项）
            if self.is_npz(file):
                self.all_npz = True
            # 处理非 npz 格式（可能是逗号分隔的多文件，如 npy/图片）
            else:
                # 按逗号分割多文件，去除每个文件的首尾空格
                inputs = file.split(',')
                inputs = [s.strip() for s in inputs]
                # 逐个校验分割后的文件格式
                for i in inputs:
                    if self.is_npy(i):
                        self.all_npy = True
                    elif self.is_image(i):
                        self.all_image = True
                    else:
                        raise RuntimeError("File illegal:{}".format(file))
            
            # 统计已标记的有效数据类型数量（npz/npy/图片）
            num_type = self.all_npz + self.all_image + self.all_npy
            # 仅支持一种输入类型，否则抛出异常
            if num_type != 1:
                raise RuntimeError("Only support one input type: npy/npz/image")

    def _random_select(self, dataset_path, num):
        """
        从数据集目录（含子目录）中筛选支持的文件，随机打乱后选择指定数量的数据。

        Args:
            dataset_path: 数据集目录路径
            num: 要选择的数据数量（0 表示选择全部）

        Returns:
            list: 筛选并打乱后的文件路径列表
        """
        full_list = []
        # 遍历目录及子目录下的所有文件
        for file in pathlib.Path(dataset_path).glob('**/*'):
            name = str(file)
            # 筛选支持的文件格式（npz/npy/图片）
            if self.is_npz(name) or self.is_npy(name) or self.is_image(name):
                full_list.append(name)
        
        # 对文件列表排序，固定随机种子（保证结果可复现），然后打乱顺序
        full_list = sorted(full_list)
        random.seed(1684)  # 固定种子，每次运行选择结果一致
        random.shuffle(full_list)
        
        # 确定最终选择的数量（若指定数量大于文件总数，则选择全部）
        num = num if len(full_list) > num else len(full_list)
        if num == 0:  # 若 num 为 0，选择所有文件
            num = len(full_list)
        
        return full_list[:num]  # 返回前 num 个文件路径

    def is_npz(self, filename: str):
        """判断文件是否为 npz 格式（不区分大小写）"""
        return filename.lower().split('.')[-1] == 'npz'

    def is_npy(self, filename: str):
        """判断文件是否为 npy 格式（不区分大小写）"""
        return filename.lower().split('.')[-1] == 'npy'

    def is_image(self, filename: str):
        """判断文件是否为支持的图片格式（jpg/bmp/png/jpeg/jfif，不区分大小写）"""
        file_suffix = filename.lower().split('.')[-1]
        return file_suffix in ['jpg', 'bmp', 'png', 'jpeg', 'jfif']

    def _print(self):
        """（未使用）打印图片列表，按索引展示每个图片路径"""
        for i, img in enumerate(self.image_list):
            print(" <{}> {}".format(i, img))

    def dump(self, file):
        """
        将筛选后的 data_list 数据列表写入指定文件，每行一个数据项。

        Args:
            file: 输出文件路径
        """
        with open(file, 'w') as f:
            for input in self.data_list:
                f.write(input + '\n')
```

## kld\_calibrator\.py

### 一、BaseKldCalibrator

`BaseKldCalibrator` 类的核心原理是**通过 KL 散度（Kullback\-Leibler Divergence）衡量概率分布差异，找到最优量化阈值**，实现对张量数据的高效量化校准。

量化的核心矛盾是 “用有限的离散值（如 int8 的 \- 128\~127）近似连续的浮点值”，需要找到一个阈值，使得量化后的数据分布尽可能接近原始分布（减少精度损失）。KL 散度恰好用于衡量两个概率分布的差异，因此被用来指导阈值选择：

1. **数据分布建模**：将输入张量的绝对值（非零值）转换为直方图（`histogram`方法），用 “分箱频数” 描述原始数据的概率分布。

    - 例：若张量值范围为 \[0, 10\]，分 10 个 bin，则每个 bin 对应 \[0,1\)、\[1,2\)…\[9,10\] 的数值计数，形成原始分布 P。

2. **分布差异度量**：目标是将原始分布 P “压缩” 到更少的分箱（如 int8 对应 128 个 bin），形成量化后的分布 Q。通过 KL 散度计算 P 和 Q 的差异（`kld_threshold`方法调用外部库实现）。

    - KL 散度越小，说明 Q 对 P 的近似越好（量化损失越小）。

3. **最优阈值求解**：遍历可能的阈值（本质是截断原始分布的上界），找到使 KL 散度最小的阈值。这个阈值既能覆盖原始数据的主要分布，又能通过有限分箱精准近似。

```Python
class BaseKldCalibrator:
    """
    基于KL散度（Kullback-Leibler Divergence）的基础校准器类。
    用于计算张量的直方图分布，并通过KL散度找到最优量化阈值，为模型量化提供基础支持。
    """

    def __init__(self, math_lib_path=calibration_math_path):
        """
        初始化KL散度校准器，加载外部数学计算库。

        Args:
            math_lib_path: 外部KL散度计算库（如C++编译的.so文件）的路径，
                           默认为全局变量calibration_math_path指定的路径
        """
        # 加载外部动态链接库（含KL散度计算实现）
        self.calib_lib = CDLL(math_lib_path)
        # 设置外部函数的返回值类型为c_float（确保类型匹配）
        self.calib_lib.kl_diversity.restype = c_float          # 单个KL散度计算函数
        self.calib_lib.kl_diversity_hist.restype = c_float     # 基于直方图的KL散度阈值计算函数

    def histogram(self, ndarray, abs_max, bin_num):
        """
        计算输入张量的绝对值直方图，用于后续KL散度计算。

        核心逻辑：将张量的非零绝对值映射到指定数量的 bins 中，统计每个 bin 的频数。

        Args:
            ndarray: 输入的 numpy 张量（待统计的激活值/权重等）
            abs_max: 直方图的最大值范围（用于归一化张量值）
            bin_num: 直方图的分箱数量

        Returns:
            hist: 形状为 (bin_num,) 的整数数组，每个元素表示对应 bin 的频数
            width: 每个 bin 的宽度（即相邻 bin 之间的间隔）
        """
        # 取张量的绝对值并展平为一维数组，过滤掉0值（避免0对分布的干扰）
        t = np.abs(ndarray.flatten())
        t = t[t != 0]

        # 计算每个 bin 的宽度：总范围为 [0, abs_max]，分为 (bin_num-1) 个间隔
        width = abs_max / (bin_num - 1)

        if t.size > 0:
            # 对非零值进行分箱：
            # 1. 将值归一化到 [0, bin_num-1] 范围（通过 t/width + 0.5 实现四舍五入）
            # 2. 用floor取整数索引，确保落在 [0, bin_num-1] 区间内
            # 3. 统计每个 bin 的频数（density=False 表示返回实际计数）
            hist, _ = np.histogram(
                np.floor(t / width + 0.5),  # 计算每个值对应的 bin 索引
                bins=bin_num,
                range=(0, bin_num - 1),     # 限制 bin 范围
                density=False
            )
        else:
            # 若所有值均为0，则返回全零直方图
            hist = np.zeros(bin_num)

        # 将直方图转换为int32类型（匹配外部C库的参数类型）
        hist = hist.astype(np.int32)
        return hist, width

    def kld_threshold(self, hist, width, bin_num, dst_bins):
        """
        调用外部库通过KL散度计算最优量化阈值。

        核心逻辑：通过最小化原始直方图与目标分箱（如量化后的 bins）之间的KL散度，
        找到最佳阈值，平衡量化精度损失与表示范围。

        Args:
            hist: 由 histogram() 方法生成的直方图数组（int32类型）
            width: 每个 bin 的宽度（float）
            bin_num: 原始直方图的分箱数量（整数）
            dst_bins: 目标分箱数量（如量化后的位数对应的 bins，如int8对应128）

        Returns:
            threshold: 计算得到的最优量化阈值（float）
        """
        # 调用外部C库函数计算KL散度阈值：
        # 1. 将numpy数组转换为C兼容的指针类型（POINTER(c_int)）
        # 2. 传递其他参数（宽度、原始分箱数、目标分箱数）
        # 3. 返回计算得到的阈值
        threshold = self.calib_lib.kl_diversity_hist(
            hist.ctypes.data_as(POINTER(c_int)),  # 直方图数据（C指针）
            c_float(width),                       # bin宽度（C float）
            c_longlong(bin_num),                  # 原始分箱数（C long long）
            c_longlong(dst_bins)                  # 目标分箱数（C long long）
        )
        return threshold
```

### 二、CalibrationTable

`CalibrationTable` 类的核心功能是**管理模型量化过程中产生的校准表文件**，抽象逻辑可概括为 “**结构化数据的读写与更新。**

```Python
class CalibrationTable:
    """校准表管理类，用于解析、存储、更新和导出模型量化所需的阈值参数"""

    def __init__(self, table):
        """初始化时解析校准表文件，获取表头和阈值映射
        
        Args:
            table: 校准表文件路径
        """
        # 调用parse方法解析文件，返回表头和阈值字典，分别存储为实例变量
        self.headers, self.thresholds_map = self.parse(table)

    def parse(self, table):
        """解析校准表文件，提取表头注释和算子的量化参数
        
        Args:
            table: 校准表文件路径
            
        Returns:
            headers: 表头注释列表（以#开头的行）
            thresholds_map: 算子量化参数字典，格式为 {op_name: [threshold, min, max]}
        """
        thresholds_map = dict()  # 存储算子与量化参数的映射
        headers = []             # 存储表头注释
        
        # 打开校准表文件并逐行读取
        with open(table, 'r') as f:
            for line in f.readlines():
                line = line.strip()  # 去除首尾空白字符（空格、换行等）
                
                # 跳过空行
                if len(line) == 0:
                    continue
                
                # 处理表头注释行（以#开头）
                if line.startswith('#'):
                    # 遇到特殊标记行（#asym_op或#int4_th）时停止解析表头
                    if line.startswith('#asym_op') or line.startswith('#int4_th'):
                        break
                    # 将普通注释行加入表头列表
                    headers.append(line)
                    continue
                
                # 处理数据行（格式：op_name threshold min max）
                fields = line.split(' ')  # 按空格分割字段
                
                # 校验数据格式（必须包含4个字段）
                if len(fields) != 4:
                    print("Table format should be 'op_name, threshold, min, max'")
                    raise RuntimeError(f"Error with parse '{line}' in {table}")
                
                # 提取字段并转换为对应类型
                op_name, threshold, _min, _max = fields
                # 存储到字典：键为算子名，值为[阈值, 最小值, 最大值]（均为float类型）
                thresholds_map[op_name] = [float(threshold), float(_min), float(_max)]
        
        return headers, thresholds_map

    def dump(self, dest_table):
        """将当前校准数据（表头+阈值）导出到目标文件
        
        Args:
            dest_table: 目标输出文件路径
        """
        with open(dest_table, "w") as f:
            # 先写入所有表头注释
            for line in self.headers:
                f.write(line + "\n")
            # 再写入每个算子的量化参数（保留7位小数）
            for op_name, params in self.thresholds_map.items():
                threshold, min_val, max_val = params
                f.write(f"{op_name} {threshold:.7f} {min_val:.7f} {max_val:.7f}\n")

    def update_to(self, dest_table, target_op, new_threshold):
        """更新指定算子的阈值，并导出到目标文件（其他参数保持不变）
        
        Args:
            dest_table: 目标输出文件路径
            target_op: 需要更新的算子名称
            new_threshold: 新的阈值
        """
        with open(dest_table, "w") as f:
            # 写入表头注释
            for line in self.headers:
                f.write(line + "\n")
            # 遍历所有算子，更新目标算子的阈值
            for op_name, params in self.thresholds_map.items():
                if op_name == target_op:
                    # 目标算子：使用新阈值，保留原min和max
                    min_val, max_val = params[1], params[2]
                    f.write(f"{op_name} {new_threshold:.7f} {min_val:.7f} {max_val:.7f}\n")
                else:
                    # 非目标算子：保持原参数
                    threshold, min_val, max_val = params
                    f.write(f"{op_name} {threshold:.7f} {min_val:.7f} {max_val:.7f}\n")
```

### 三、SimpleTuner

`SimpleTuner` 类的核心逻辑是 **MLIR 模型量化阈值自动调优**，核心目标是通过校准数据搜索每个算子的最优量化阈值，在保证量化精度（最小化与 FP32 模型的误差）的同时，兼顾模型性能，整体遵循 “数据准备→依赖解析→阈值搜索→结果输出” 的流程。

- **核心功能**：为 MLIR 模型的每个算子（输入算子除外）自动优化量化阈值，量化逻辑为 “基于阈值的数值映射”（通过 `import_quant_bias` 实现）。

- **核心依赖**：

    1. 数据层面：校准数据（图像 / NPZ / 混合类型）、数据选择器（`DataSelector`）、预处理 / 后处理操作（`ppa_list`）。

    2. 模型层面：MLIR 解析器（`MlirParser`）、原始 FP32 模型（`module`，生成参考输出）、量化模型（`module_dq`，权重假量化）。

    3. 工具层面：校准表（`CalibrationTable`，存储初始 / 优化后阈值）、误差评估函数（`cosine_sim` 余弦相似度、相对误差）。

```Python
class SimpleTuner:
    """
    MLIR模型量化阈值自动调优器
    核心功能：遍历模型算子，通过校准数据搜索最优量化阈值，平衡量化精度与性能
    依赖：MLIR解析器、校准表、数据选择器、量化工具函数
    """

    def __init__(self, args, ds: DataSelector, ppa_list, abs_max_dict):
        """
        初始化调优器
        Args:
            args: 配置参数（包含mlir文件路径、调优轮数、校准表路径等）
            ds: 数据选择器实例（提供校准数据列表）
            ppa_list: 预处理/后处理操作列表（针对图像数据）
            abs_max_dict: 各算子张量的绝对最大值字典（用于阈值搜索边界）
        """
        self.args = copy.deepcopy(args)  # 深拷贝参数，避免修改原始配置
        self.start_time = time.time()  # 记录调优开始时间
        self.abs_max_dict = abs_max_dict  # 算子绝对最大值字典
        # 调优轮数不超过数据总数
        self.args.tune_num = min(len(ds.data_list), args.tune_num)
        self.tuned_op_list = []  # 已完成调优的算子列表
        self.data_list = ds.data_list  # 校准数据列表
        self.ppa_list = ppa_list  # 预处理/后处理操作列表
        self.debug_cmd = parse_debug_cmd(args.debug_cmd)  # 解析调试命令（如指定调优步数、输入校准表等）
        self.fuseop_list = {}  # 融合算子映射表（key:子算子名，value:融合算子名）
        
        # 打印调试命令（若有）
        if self.debug_cmd:
            print('debug_cmd:', self.debug_cmd)
        
        # 加载校准表（优先使用调试命令指定的路径）
        if 'input_calibration_table' in self.debug_cmd:
            self.threshold_table = CalibrationTable(self.debug_cmd['input_calibration_table'] + ".1")
        else:
            self.threshold_table = CalibrationTable(args.calibration_table + ".1")
        
        # 加载MLIR模型（原始精度和量化后模型）
        self.module = module()  # 原始FP32模型（用于生成参考输出）
        self.module.load(args.mlir_file)
        self.parser = MlirParser(args.mlir_file)  # MLIR解析器（获取算子信息、输入输出等）
        
        # 构建融合算子列表
        for op_name in self.parser.get_op_name_list():
            fuseop_list_append(op_name, self.fuseop_list)
        
        # 从MLIR解析器获取模型基本信息
        self.batch_size = self.parser.get_batch_size()  # 批处理大小
        self.input_num = self.parser.get_input_num()  # 输入张量个数
        self.ds = ds  # 数据选择器实例
        
        # 图像数据特殊处理：补齐到批处理大小的整数倍
        if ds.all_image:
            n = self.args.tune_num % self.batch_size
            if n != 0:
                # 用最后一条数据补齐
                for i in range(self.batch_size - n):
                    self.data_list.append(self.data_list[-1])
                    self.args.tune_num += 1
            # 调整调优轮数为批数
            self.args.tune_num = self.args.tune_num // self.batch_size
        
        # 初始化日志器（调试模式为DEBUG级别，否则为INFO）
        log_level = "DEBUG" if 'debug_log' in self.debug_cmd else "INFO"
        self.logger = setup_logger('auto_tune', log_level=log_level)
        
        # 调优步数（默认21步，可通过调试命令覆盖）
        self.tune_steps = 21
        if 'tune_steps' in self.debug_cmd:
            self.tune_steps = int(self.debug_cmd['tune_steps'])
        
        # 加载量化模型（权重假量化）
        self.module_dq = module()
        self.module_dq.load(args.mlir_file)
        self.module_dq.fake_quant_weight()
        
        # 加载网络输入数据（构建量化输入和参考输入字典）
        self.load_net_input()
        self.dot = None  # 绘图对象（默认关闭，用于可视化算子依赖）
        # self.dot = gz.Digraph()  # 启用则生成算子依赖图

    def print_dbg(self, *para):
        """调试日志打印（封装logger.debug）"""
        tmp = [str(item) for item in para]
        tmpStr = ' '.join(tmp)
        self.logger.debug(tmpStr)

    def print_info(self, *para):
        """普通日志打印（封装logger.info）"""
        tmp = [str(item) for item in para]
        tmpStr = ' '.join(tmp)
        self.logger.info(tmpStr)

    def load_net_input(self):
        """
        加载校准数据到输入字典
        构建两个字典：
            self.dq_activations: 量化模型输入（键：调优轮次，值：{算子名: (张量数据, 引用计数)}）
            self.ref_activations: 参考模型输入（同上，用于计算量化误差）
        支持三种数据类型：npz文件（单条/批量）、图像文件、混合类型
        """
        self.dq_activations = {}  # 量化模型输入字典
        self.ref_activations = {}  # 参考模型输入字典
        inp_ref_dict = {}  # 输入算子的引用计数字典
        
        # 初始化输入算子的引用计数（从MLIR解析器获取）
        for input in self.module.input_names:
            inp_ref_dict[input] = self.parser.get_use_count_by_op_name(input)
        
        # 批量输入存储（图像数据用列表，npz用字典）
        if self.ds.all_image:
            batched_inputs = self.input_num * ['']
        else:
            batched_inputs = {}
        
        idx, tune_idx = 0, 0  # idx：图像数据批计数，tune_idx：调优轮次计数
        # 初始化当前轮次的输入字典
        self.dq_activations[tune_idx] = {}
        self.ref_activations[tune_idx] = {}
        only_one = len(self.module.input_names) == 1  # 是否只有一个输入算子
        
        print(f'prepare data from {len(self.data_list)}')
        for data in self.data_list:
            # 超过调优轮数上限则停止
            if len(self.ref_activations) > self.args.tune_num + 1:
                break
            
            # 处理npz格式数据
            if self.ds.all_npz:
                x = np.load(data)  # 加载npz文件
                if only_one:
                    # 单输入场景：校验npz文件只有一个键
                    assert (len(x.files) == 1)
                    n0 = self.module.input_names[0]  # 模型输入算子名
                    n1 = x.files[0]  # npz文件中的键名
                    
                    if x[n1].shape[0] > 1:
                        # 批量数据：直接存入输入字典
                        self.dq_activations[tune_idx][n0] = [x[n1], inp_ref_dict[n0]]
                        self.ref_activations[tune_idx][n0] = [x[n1], inp_ref_dict[n0]]
                    else:
                        # 单条数据：拼接成批量
                        batched_inputs[n1] = (np.concatenate(
                            [batched_inputs[n1], x[n1].astype(np.float32)], axis=0)
                                            if n1 in batched_inputs else x[n1].astype(np.float32))
                        # 达到批处理大小则存入输入字典
                        if batched_inputs[n1].shape[0] >= self.batch_size:
                            self.dq_activations[tune_idx][n0] = [
                                batched_inputs[n1][:self.batch_size], inp_ref_dict[n0]
                            ]
                            self.ref_activations[tune_idx][n0] = [
                                batched_inputs[n1][:self.batch_size], inp_ref_dict[n0]
                            ]
                            batched_inputs.pop(n1)  # 清空已处理的批量数据
                        else:
                            continue  # 未达批大小，继续拼接
                else:
                    # 多输入场景：遍历所有输入算子
                    batch_size = 0
                    for input in self.module.input_names:
                        assert (input in x)  # 校验npz文件包含该输入算子
                        if x[input].shape[0] > 1:
                            # 批量数据：直接存入
                            self.dq_activations[tune_idx][input] = [x[input], inp_ref_dict[input]]
                            self.ref_activations[tune_idx][input] = [x[input], inp_ref_dict[input]]
                            batch_size = self.batch_size
                        else:
                            # 单条数据：拼接成批量
                            batched_inputs[input] = (np.concatenate(
                                [batched_inputs[input], x[input].astype(np.float32)], axis=0) if input
                                                    in batched_inputs else x[input].astype(np.float32))
                            batch_size = batched_inputs[input].shape[0]
                            # 达到批处理大小则存入
                            if batched_inputs[input].shape[0] >= self.batch_size:
                                real_batch_size = self.parser.get_op_by_op_name(input).shape[0]
                                self.dq_activations[tune_idx][input] = [
                                    batched_inputs[input][:real_batch_size], inp_ref_dict[input]
                                ]
                                self.ref_activations[tune_idx][input] = [
                                    batched_inputs[input][:real_batch_size], inp_ref_dict[input]
                                ]
                                batched_inputs.pop(input)
                    # 未达批大小则继续拼接
                    if batch_size < self.batch_size:
                        continue
            
            # 处理图像格式数据
            elif self.ds.all_image:
                idx += 1
                inputs = data.split(',')  # 按逗号分割多输入图像路径
                inputs = [s.strip() for s in inputs]
                assert (self.input_num == len(inputs))  # 校验输入个数匹配
                
                # 遍历所有输入，拼接图像路径
                for i in range(self.input_num):
                    batched_inputs[i] += '{},'.format(inputs[i])
                    # 达到批处理大小则执行预处理
                    if idx == self.batch_size:
                        x = self.ppa_list[i].run(batched_inputs[i][:-1])  # 预处理（去掉末尾逗号）
                        name = self.ppa_list[i].input_name  # 输入算子名
                        self.dq_activations[tune_idx][name] = [x, inp_ref_dict[name]]
                        self.ref_activations[tune_idx][name] = [x, inp_ref_dict[name]]
                # 重置批计数和批量输入存储
                if idx == self.batch_size:
                    idx = 0
                    batched_inputs = self.input_num * ['']
                else:
                    continue
            
            # 处理混合类型数据（单条数据，非npz/图像）
            else:
                self.dq_activations[tune_idx] = {}
                self.ref_activations[tune_idx] = {}
                inputs = data.split(',')  # 分割多输入路径
                inputs = [s.strip() for s in inputs]
                assert (self.input_num == len(inputs))  # 校验输入个数
                
                # 遍历输入，加载数据并存入字典
                for name, input in zip(self.module.input_names, inputs):
                    x = np.load(input)
                    self.dq_activations[tune_idx][name] = [x, inp_ref_dict[name]]
                    self.ref_activations[tune_idx][name] = [x, inp_ref_dict[name]]
            
            # 当前轮次数据加载完成，切换到下一轮次
            tune_idx += 1
            self.dq_activations[tune_idx] = {}
            self.ref_activations[tune_idx] = {}
        
        # 删除最后一轮空数据（若未加载成功）
        if len(self.ref_activations[tune_idx]) == 0:
            self.ref_activations.pop(tune_idx)
        
        # 最终调优轮数取配置值和实际加载数据数的较小值
        self.args.tune_num = min(self.args.tune_num, len(self.ref_activations))
        assert self.args.tune_num > 0  # 确保至少有一轮调优数据

    def get_input_tensor(self, i, op_name):
        """
        获取量化模型的输入张量
        Args:
            i: 调优轮次索引
            op_name: 算子名
        Returns:
            张量数据（np.ndarray），不存在则打印错误并返回None
        """
        if op_name in self.dq_activations[i]:
            return self.dq_activations[i][op_name][0]
        print('error, idx:{} op_name:{} not in dq_activations'.format(i, op_name))
        return None

    def clear_input_tensor(self, i, op_name, node_label):
        """
        清理量化模型的输入张量（基于引用计数）
        当引用计数为0时删除张量，否则引用计数减1
        Args:
            i: 调优轮次索引
            op_name: 目标算子名
            node_label: 算子可视化标签（用于记录清理过程）
        """
        if i == 0:
            node_label[0] += '\nclear_input_tensor {}\'s input'.format(op_name)
        
        # 获取目标算子的前驱算子（输入算子）
        input_ops = self.parser.get_pre_op_by_op_name(op_name)
        for input_op in input_ops:
            if input_op in self.dq_activations[i]:
                # 引用计数为1：删除张量
                if self.dq_activations[i][input_op][1] == 1:
                    self.dq_activations[i].pop(input_op)
                    if i == 0:
                        node_label[0] += '\npop its input:{}'.format(input_op)
                # 引用计数>1：减1
                else:
                    self.dq_activations[i][input_op][1] -= 1
                    tmp = self.dq_activations[i][input_op][1]
                    if i == 0:
                        node_label[0] += '\ndec {}\'s refcount:{} > {}'.format(
                            input_op, tmp + 1, tmp)

    def clear_ref_tensor(self, i, evaled_op, node_label):
        """
        清理参考模型的张量（基于引用计数）
        逻辑与clear_input_tensor一致，针对ref_activations字典
        Args:
            i: 调优轮次索引
            evaled_op: 目标算子名
            node_label: 算子可视化标签
        """
        if i == 0:
            node_label[0] += '\nclear_ref_tensor {}\'s input'.format(evaled_op)
        
        # 清理网络输出残留（引用计数为0时删除）
        if self.ref_activations[i][evaled_op][1] == 0:
            self.ref_activations[i].pop(evaled_op)
        
        # 获取前驱算子并处理引用计数
        input_ops = self.parser.get_pre_op_by_op_name(evaled_op)
        for input_op in input_ops:
            if input_op in self.ref_activations[i]:
                if self.ref_activations[i][input_op][1] == 1:
                    self.ref_activations[i].pop(input_op)
                    if i == 0:
                        node_label[0] += '\npop its input:{}'.format(input_op)
                else:
                    self.ref_activations[i][input_op][1] -= 1
                    tmp = self.ref_activations[i][input_op][1]
                    if i == 0:
                        node_label[0] += '\ndec {}\'s refcount:{} > {}'.format(
                            input_op, tmp + 1, tmp)

    def gen_input_tensor(self, i, input_tensor_of_evaled_op, node_label):
        """
        生成量化模型（module_dq）的输入张量（递归依赖前驱算子）
        核心逻辑：先量化前驱算子输出，再执行当前算子得到量化输出，记录余弦相似度
        Args:
            i: 调优轮次索引（对应不同的校准数据）
            input_tensor_of_evaled_op: 目标输入算子名（需要生成张量的算子）
            node_label: 可视化标签列表（记录生成过程，用于绘图）
        Returns:
            bool: 生成成功返回True，失败返回False
        """
        # 调试模式下记录生成日志（仅第0轮）
        if i == 0:
            tmp = 'gen_input_tensor:{}'.format(input_tensor_of_evaled_op)
        
        # 张量已存在于量化输入字典，直接返回成功
        if input_tensor_of_evaled_op in self.dq_activations[i]:
            if i == 0:
                tmp += '\nit exsit'
                node_label[0] += '\n{}'.format(tmp)
            return True

        # 获取目标算子的前驱算子（输入依赖）
        input_ops = self.parser.get_pre_op_by_op_name(input_tensor_of_evaled_op)
        for input_op in input_ops:
            # 获取前驱算子的张量数据（量化模型输入）
            data = self.get_input_tensor(i, input_op)
            if data is None:  # 数据获取失败，返回False
                return False
            
            # 读取前驱算子的量化阈值，对数据进行量化
            threshold = self.threshold_table.thresholds_map[input_op][0]
            data_q = import_quant_bias(data, threshold)  # 量化函数（自定义工具）
            # 计算量化前后的余弦相似度（评估量化损失）
            cos_sim = cosine_sim(data, data_q)
            # 将量化后的数据设置到量化模型中
            self.module_dq.set_tensor(input_op, data_q, data_q.shape)
            
            # 调试模式下记录量化信息
            if i == 0:
                tmp += '\nits input:{} import_quant_bias, th:{}, cos:{:.4f}'.format(
                    input_op, threshold, cos_sim)
        
        # 若存在前驱算子，执行当前算子生成输出张量
        if len(input_ops) > 0:
            # 调用量化模型的当前算子，得到量化输出
            value = self.module_dq.invoke_at(input_tensor_of_evaled_op)
            # 获取参考模型（FP32）的输出张量（用于计算量化误差）
            target_fp32_activations = self.get_ref_tensor(i, input_tensor_of_evaled_op)
            # 计算量化输出与FP32输出的余弦相似度
            cos_sim = cosine_sim(target_activations, target_fp32_activations)
            
            # 累计当前层的余弦相似度（用于后续调试分析）
            if input_tensor_of_evaled_op in self.layer_cos_sim:
                self.layer_cos_sim[input_tensor_of_evaled_op] += cos_sim
            else:
                self.layer_cos_sim[input_tensor_of_evaled_op] = cos_sim
            
            # 获取当前算子的引用计数（后续用于张量清理）
            count = self.parser.get_use_count_by_op_name(input_tensor_of_evaled_op)
            # 将生成的量化张量存入字典（复制数据避免引用冲突）
            self.dq_activations[i][input_tensor_of_evaled_op] = [value.copy(), count]
            
            # 调试模式下记录算子执行信息
            if i == 0:
                tmp += '\nrun it, refcount:{}, cos:{}\n'.format(count, cos_sim)
        
        # 调试模式下将日志写入可视化标签
        if i == 0:
            node_label[0] += '\n{}'.format(tmp)
        return True

    def get_ref_tensor(self, i, evaled_op):
        """
        获取参考模型（FP32）的张量数据
        Args:
            i: 调优轮次索引
            evaled_op: 目标算子名
        Returns:
            np.ndarray: 张量数据；不存在则打印错误并返回None
        """
        if evaled_op in self.ref_activations[i]:
            return self.ref_activations[i][evaled_op][0]
        print('error, idx:{} evaled_op:{} not in ref_activations'.format(i, evaled_op))
        return None

    def gen_ref_tensor(self, i, op_name, node_label):
        """
        生成参考模型（FP32）的张量数据（递归依赖前驱算子）
        核心逻辑：基于FP32模型执行算子，生成无量化损失的参考输出，用于后续误差计算
        Args:
            i: 调优轮次索引
            op_name: 目标算子名（需要生成参考张量的算子）
            node_label: 可视化标签列表（记录生成过程）
        """
        # 调试模式下记录生成日志（仅第0轮）
        if i == 0:
            tmp = 'gen_ref_tensor:{}'.format(op_name)
        
        # 张量已存在于参考输入字典，直接返回
        if op_name in self.ref_activations[i]:
            if i == 0:
                tmp += '\nit exsit'
                node_label[0] += '\n{}'.format(tmp)
            return
        
        # 获取目标算子的前驱算子（输入依赖）
        input_ops = self.parser.get_pre_op_by_op_name(op_name)
        # 处理融合算子：若当前算子是融合算子的子算子且无输入，用融合算子的输入替代
        if op_name in self.fuseop_list and input_ops == []:
            fused_op_name = self.fuseop_list[op_name]
            input_ops = self.parser.get_pre_op_by_op_name(fused_op_name)
        
        # 遍历前驱算子，设置FP32数据到参考模型
        for input_op in input_ops:
            # 获取前驱算子的FP32张量和引用计数
            data = self.ref_activations[i][input_op][0]
            refcount = self.ref_activations[i][input_op][1]
            # 调试模式下记录前驱算子信息
            if i == 0:
                tmp += '\nits input:{}, refcount:{}'.format(input_op, refcount)
            # 将FP32数据设置到参考模型
            self.module.set_tensor(input_op, data, data.shape)
        
        # 若存在前驱算子，执行当前算子生成FP32输出
        if len(input_ops) > 0:
            # 调用参考模型的当前算子，得到FP32输出
            value = self.module.invoke_at(op_name)
            # 获取当前算子的输出算子列表（需要保存生成的张量）
            outputs = self.parser.get_outputs_by_op_name(op_name)
            # 处理融合算子：若当前算子无输出，用融合算子的输出替代
            if outputs is None and op_name in self.fuseop_list:
                fused_op_name = self.fuseop_list[op_name]
                outputs = self.parser.get_outputs_by_op_name(fused_op_name)
            
            # 计算输出算子的总引用计数（所有输出算子的引用计数之和）
            count = 0
            for o_ in outputs:
                count += self.parser.get_use_count_by_op_name(o_)
            # 将FP32张量存入参考输入字典（所有输出算子共享该张量）
            for o_ in outputs:
                self.ref_activations[i][o_] = [value.copy(), count]
            
            # 调试模式下记录算子执行信息
            if i == 0:
                tmp += '\ninvoke_at:{}, refcount:{}'.format(op_name, count)
        
        # 调试模式下将日志写入可视化标签
        if i == 0:
            node_label[0] += '\n{}'.format(tmp)

    def calc_distance(self, evaled_op, threshold):
        """
        计算指定阈值下的量化误差（平均相对误差）和平均余弦相似度
        核心逻辑：遍历所有调优轮次，对输入量化后执行算子，对比量化输出与FP32输出的差异
        Args:
            evaled_op: 待评估算子名（当前正在调优的算子）
            threshold: 待测试的量化阈值
        Returns:
            tuple: (平均相对误差, 平均余弦相似度)；失败返回(None, None)
        """
        distance = 0  # 累计相对误差
        total_cosine_similarity = 0  # 累计余弦相似度
        
        # 遍历所有调优轮次（不同校准数据）
        for idx in range(self.args.tune_num):
            # 遍历当前算子的所有前驱输入算子
            for input in self.parser.get_pre_op_by_op_name(evaled_op):
                # 调试模式下记录量化信息（仅第0轮）
                if idx == 0:
                    self.print_dbg('{}\'s input:{} import_quant_bias, th:{}'.format(
                        evaled_op, input, threshold))
                
                # 根据调试命令选择输入数据（量化输入或FP32输入）
                if 'not_use_fp32_tensor_as_ref' in self.debug_cmd:
                    value = self.get_input_tensor(idx, input)
                else:
                    value = self.get_ref_tensor(idx, input)
                
                if value is None:  # 数据获取失败，返回错误
                    print('error, calc_distance get tensor fail')
                    return None, None
                
                # 对输入数据进行量化（使用当前测试阈值）
                value = import_quant_bias(value, threshold)
                # 将量化后的数据设置到量化模型
                self.module_dq.set_tensor(input, value, value.shape)
            
            # 执行量化模型的当前算子，得到量化输出
            target_activations = self.module_dq.invoke_at(evaled_op)
            # 获取参考模型的FP32输出
            target_fp32_activations = self.get_ref_tensor(idx, evaled_op)
            
            # 计算并累计余弦相似度（评估量化精度）
            total_cosine_similarity += cosine_sim(target_activations, target_fp32_activations)
            # 计算相对误差（L2范数差异 / L1范数参考值）
            diff = target_fp32_activations.flatten() - target_activations.flatten()
            norm_2 = np.linalg.norm(diff)  # 差异的L2范数
            norm_1 = np.linalg.norm(target_fp32_activations.flatten(), ord=1)  # 参考值的L1范数
            distance += norm_2 / norm_1
        
        # 返回平均相对误差和平均余弦相似度（除以调优轮次）
        return distance / self.args.tune_num, total_cosine_similarity / self.args.tune_num

    def find_better_threshold(self, evaled_op, tuned_op, node_label):
    """
    搜索指定算子（tuned_op）的最优量化阈值，通过最小化量化误差优化阈值
    核心逻辑：在阈值范围内测试不同取值，选择使量化输出与FP32输出误差最小的阈值
    
    Args:
        evaled_op: 评估算子（依赖tuned_op输出的算子，用于计算量化误差）
        tuned_op: 待调优阈值的目标算子
        node_label: 可视化标签列表（记录调优过程，用于生成算子依赖图）
    
    Returns:
        bool: 调优成功返回True，失败返回False
    """
    prev_distance = -1  # 上一轮测试的误差（初始值-1表示未开始测试）
    # 获取初始阈值（从校准表中读取）
    threshold = self.initial_threshold[tuned_op][0]
    # 获取当前算子张量的绝对最大值（作为阈值搜索的上边界，避免量化溢出）
    abs_max = self.abs_max_dict[tuned_op]
    
    # 获取调优算子在模型中的索引（用于日志输出）
    if tuned_op in self.parser.get_op_name_list():
        op_no = self.parser.get_op_name_list().index(tuned_op)
    else:
        op_no = 0
    
    # 打印调优初始信息（算子索引、名称、初始阈值、绝对最大值等）
    self.print_dbg('>>>tuned_op_idx:', op_no, ', tuned_op:', tuned_op, ', threshold:',
                   threshold, 'abs_max:', abs_max, ', evaled_op:', evaled_op)
    
    # 若初始阈值已大于绝对最大值，无需调优（避免量化时数据溢出）
    if threshold > abs_max:
        self.print_dbg('waring, threshold > abs_max, do not tune the threshold')
    
    # 阈值搜索的下边界（默认使用初始阈值，调试模式可放宽至初始阈值的1/3）
    th_min = threshold
    if 'find_lower_th' in self.debug_cmd:
        th_min = threshold / 3
    
    # 初始化最优阈值和相似度（默认使用初始阈值）
    best_threshold = threshold
    best_cos_sim = 0  # 最优阈值对应的余弦相似度
    init_cos_sim = 0  # 初始阈值对应的余弦相似度
    
    # 获取评估算子的前驱算子（用于调试模式下生成输入张量）
    pre_ops = self.parser.get_pre_op_by_op_name(evaled_op)
    # 调试模式：若不使用FP32参考张量，则预先生成量化模型的输入
    for idx in range(self.args.tune_num):
        if 'not_use_fp32_tensor_as_ref' in self.debug_cmd:
            for pre_op in pre_ops:
                # 生成输入张量失败则返回False
                if not self.gen_input_tensor(idx, pre_op, node_label):
                    return False
    
    # 阈值搜索的最小有效范围（小于此值则不进行搜索，避免无效计算）
    min_tuned_diff = 0.01
    if 'min_tuned_diff' in self.debug_cmd:
        min_tuned_diff = self.debug_cmd['min_tuned_diff']
    
    # 计算搜索范围和步长
    diff = abs(abs_max - th_min)  # 搜索范围的总宽度
    step = (abs_max - th_min) / self.tune_steps  # 每步的增量（默认21步）
    ranges = range(self.tune_steps + 1)  # 遍历的步数范围（0到tune_steps，含首尾）
    
    # 仅当步长有效且范围足够大时，执行阈值搜索
    if step > 0 and diff > min_tuned_diff:
        r = 1  # 搜索轮数（默认1轮）
        # 调试模式：开启二次精细搜索（在第一次最优阈值附近进一步优化）
        if 'find_lower_th' in self.debug_cmd:
            r = 2
        
        for n in range(r):
            # 二次搜索：调整范围为第一次最优阈值的±step，缩小步长精细搜索
            if n == 1 and 'find_lower_th' in self.debug_cmd:
                th_min = best_threshold - step  # 新的下边界
                th_max = best_threshold + step  # 新的上边界
                diff = abs(th_max - th_min)
                times = self.tune_steps // 2  # 精细搜索的步数（原步数的一半）
                step = (th_max - th_min) / times  # 精细步长
                ranges = range(times + 1)[1:-1]  # 排除首尾，只搜索中间范围
            
            # 遍历所有测试阈值
            for i in ranges:
                cur_threshold = th_min + step * i  # 当前测试的阈值
                # 计算当前阈值下的平均误差和余弦相似度
                cur_distance, cur_cos_sim = self.calc_distance(evaled_op, cur_threshold)
                
                # 计算失败则返回False
                if cur_distance is None and cur_cos_sim is None:
                    return False
                
                # 第一次测试：记录初始误差和相似度
                if prev_distance == -1:
                    self.print_dbg("### tuning i:{}, tuned_op_idx:{}, tuned_op:{}, threshold:"
                                   "{:5f}, distance: {}".format(i, op_no, tuned_op,
                                                                cur_threshold, cur_distance))
                    prev_distance = cur_distance
                    init_cos_sim = cur_cos_sim
                    continue
                # 找到更优阈值：当前误差小于上一轮误差（优先保证精度）
                elif cur_distance < prev_distance:
                    prev_distance = cur_distance
                    best_threshold = cur_threshold  # 更新最优阈值
                    best_cos_sim = cur_cos_sim  # 更新最优相似度
    
    # 调试模式：清理量化模型的输入张量（仅当不使用FP32参考时）
    for idx in range(self.args.tune_num):
        if 'not_use_fp32_tensor_as_ref' in self.debug_cmd:
            self.clear_input_tensor(idx, tuned_op, node_label)
    
    # 记录调优结果到可视化标签
    if step > 0:
        if threshold != best_threshold:
            # 找到更优阈值：记录阈值变化、绝对最大值、相似度变化
            node_label[
                0] += '\ntune, find:{} th:{:.4f}->{:.4f}, abs_max:{:.4f}, cos_sim:{:.3f}->{:.3f}'.format(
                    tuned_op, threshold, best_threshold, abs_max, init_cos_sim, best_cos_sim)
        else:
            # 未找到更优阈值：记录初始阈值和相似度
            node_label[
                0] += '\ntune:{} no better th, init th:{}, cos_sim:{:.3f}->{:.3f}'.format(
                    tuned_op, threshold, init_cos_sim, best_cos_sim)
    else:
        # 无有效搜索范围：不调优
        node_label[0] += '\nnot tune {}, init th:{}'.format(tuned_op, threshold)
    
    # 仅当新最优阈值大于当前校准表中的阈值时更新（避免阈值过小导致量化溢出）
    if best_threshold > self.threshold_table.thresholds_map[tuned_op][0]:
        node_label[0] += '\nupdate {} th: {}>{}'.format(
            tuned_op, self.threshold_table.thresholds_map[tuned_op][0], best_threshold)
        self.threshold_table.thresholds_map[tuned_op][0] = best_threshold
    
    return True
    
    def isAllInputTuned(self, evaled_op):
    """
    检查当前评估算子（evaled_op）的所有前驱输入算子是否已完成阈值调优
    用于确保调优顺序符合算子依赖关系（先调优输入，再调优输出）
    
    Args:
        evaled_op: 待检查的评估算子
    
    Returns:
        bool: 所有前驱算子均已调优返回True，否则返回False
    """
    # 获取评估算子的所有前驱输入算子
    pre_ops = self.parser.get_pre_op_by_op_name(evaled_op)
    # 检查每个前驱算子是否在已调优列表中
    for tuned_op in pre_ops:
        if tuned_op not in self.tuned_op_list:
            return False
    return True
    
    def run(self):
    """
    执行自动调优主流程：遍历模型所有算子，按依赖顺序调优阈值，最终返回优化后的阈值字典
    核心逻辑：
    1. 按拓扑顺序遍历算子，确保先调优输入算子，再调优输出算子
    2. 对每个算子生成参考输出（FP32）和量化输出，计算误差并搜索最优阈值
    3. 支持调试模式：生成余弦相似度可视化图和算子依赖图
    """
    self.layer_cos_sim = {}  # 记录各层量化输出与FP32输出的余弦相似度
    all_tensors = self.parser.get_op_name_list()  # 获取模型所有算子名称列表
    # 保存初始阈值（用于后续对比调优效果）
    self.initial_threshold = copy.deepcopy(self.threshold_table.thresholds_map)
    
    # 初始化进度条（显示调优进度）
    pbar = tqdm(all_tensors, total=len(all_tensors), position=0, leave=True)
    for i, evaled_op in enumerate(all_tensors):
        pbar.set_description("tune op: {}".format(evaled_op))  # 更新进度条描述
        pbar.update(1)  # 推进进度条
        
        # 获取当前算子类型（如卷积、激活等）
        type = self.parser.get_op_type_by_op_name(evaled_op)
        if type is None:  # 跳过无类型的算子
            continue
        
        # 初始化当前算子的可视化标签（记录调优信息）
        node_label = ['idx:{}  name:{}  type:{}\n'.format(i, evaled_op, type.split('.')[-1])]
        
        # 跳过输入算子（输入通常不量化，无需调优）
        if type == 'top.Input':
            if self.dot is not None:  # 若启用绘图，标记输入节点
                self.dot.node(evaled_op, node_label[0], shape='box')
            continue
        
        # 获取当前算子的前驱输入算子（用于构建依赖关系）
        pre_ops = self.parser.get_pre_op_by_op_name(evaled_op)
        # 若启用绘图，添加当前算子与前驱算子的依赖边
        if self.dot is not None:
            for pre_op in pre_ops:
                evaled_op = split_fuseop(evaled_op)  # 处理融合算子
                self.dot.edge(pre_op, evaled_op, label=pre_op)
        
        # 为所有调优轮次生成当前算子的FP32参考输出（用于计算量化误差）
        for idx in range(self.args.tune_num):
            evaled_op = split_fuseop(evaled_op)  # 处理融合算子
            self.gen_ref_tensor(idx, evaled_op, node_label)
        
        # 情况1：当前算子的所有输入均已调优，选择第一个输入再次优化（精细调优）
        if self.isAllInputTuned(evaled_op):
            # 对第一个输入算子搜索更优阈值
            ret = self.find_better_threshold(evaled_op, pre_ops[0], node_label)
            if not ret:  # 调优失败则中断流程
                break
            self.tuned_op_list.append(pre_ops[0])  # 标记为已调优
            
            if self.dot is not None:  # 若启用绘图，标记当前节点
                self.dot.node(evaled_op, node_label[0], shape='box')
            
            # 清理当前算子的参考张量（释放内存）
            for idx in range(self.args.tune_num):
                self.clear_ref_tensor(idx, evaled_op, node_label)
            continue
        
        # 情况2：存在未调优的输入算子，依次调优所有未调优的输入
        faild = False
        for tuned_op in pre_ops:
            # 只调优未处理过的输入算子
            if tuned_op not in self.tuned_op_list:
                # 搜索当前输入算子的最优阈值
                ret = self.find_better_threshold(evaled_op, tuned_op, node_label)
                if not ret:  # 调优失败则标记并中断
                    faild = True
                    break
                self.tuned_op_list.append(tuned_op)  # 标记为已调优
        if faild:  # 任一输入调优失败则中断整体流程
            break
        
        # 清理当前算子的参考张量（释放内存）
        for idx in range(self.args.tune_num):
            self.clear_ref_tensor(idx, evaled_op, node_label)
        
        # 调试模式：打印当前缓存的张量信息（引用计数等，用于内存优化）
        self.print_dbg('>>>>buffered_tensors info:')
        self.print_dbg('dq_activations keys:', list(self.dq_activations[0].keys()))
        for op_name in self.dq_activations[0]:
            self.print_dbg('op:', op_name, ' refcount:', self.dq_activations[0][op_name][1])
        self.print_dbg('ref_activations keys:', list(self.ref_activations[0].keys()))
        for op_name in self.ref_activations[0]:
            self.print_dbg('op:', op_name, ' refcount:', self.ref_activations[0][op_name][1])
        
        # 若启用绘图，标记当前算子节点
        if self.dot is not None:
            self.dot.node(evaled_op, node_label[0], shape='box')
    
    pbar.close()  # 关闭进度条
    # 打印调优总耗时
    print('auto tune end, run time:{}'.format(time.time() - self.start_time))
    
    # 调试模式：生成各层余弦相似度的可视化图表
    if 'print_debug_info' in self.debug_cmd:
        index_list = []
        cos_tensor = []
        for i, layer in enumerate(self.layer_cos_sim):
            index_list.append('{}_{}'.format(i, layer))  # 层索引+名称
            # 计算平均余弦相似度（除以调优轮次）
            cos_tensor.append(self.layer_cos_sim[layer] / self.args.tune_num)
        index_list = np.array(index_list)
        cos_tensor = np.array(cos_tensor)
        save_tensor_subplot(cos_tensor, index_list, 'layer_cos_sim', 'layer_cos_sim')
    
    # 若启用绘图，生成算子依赖图（SVG格式）
    if self.dot is not None:
        self.dot.render(filename='auto_tune_result', directory='./', view=False)
        os.system('dot -Tsvg ./auto_tune_result -o ./auto_tune_result.svg')
    
    # 构建优化后的阈值字典并返回
    tuned_th_dict = {
        i: self.threshold_table.thresholds_map[i][0]
        for i in self.threshold_table.thresholds_map
    }
    return tuned_th_dict
```





## mix\_precision\.py

```Python
def find_all_pre_layers(all_pre_layers, op_name, parser, exist_ref_layers=None):
    """
    递归查找指定操作的所有前驱层
    
    Args:
        all_pre_layers (list): 存储所有前驱层的列表
        op_name (str): 当前操作名称
        parser (MlirParser): MLIR解析器实例
        exist_ref_layers (list, optional): 已存在的参考层列表，用于限制递归范围
    """
    # 获取当前操作的直接前驱操作
    pre_layers = parser.get_pre_op_by_op_name(op_name)
    
    if len(pre_layers) > 0:
        # 遍历每个直接前驱操作
        for pre_layer in pre_layers:
            # 如果前驱层不在结果列表中，则添加
            if pre_layer not in all_pre_layers:
                all_pre_layers.append(pre_layer)
            
            # 如果存在参考层列表且当前前驱层在其中，则继续递归查找
            if exist_ref_layers is not None:
                if pre_layer in exist_ref_layers:
                    find_all_pre_layers(all_pre_layers, pre_layer, parser, exist_ref_layers)
            else:
                # 如果没有参考层列表限制，则无条件继续递归查找
                find_all_pre_layers(all_pre_layers, pre_layer, parser, exist_ref_layers)
    else:
        # 如果没有前驱层，将当前操作本身添加到结果列表
        if op_name not in all_pre_layers:
            all_pre_layers.append(op_name)

```



```Python
class MixQuantModel:
    """
    混合量化模型类，用于加载和推理量化后的MLIR模型
    
    Attributes:
        fp32_mlir (str): FP32精度的MLIR模型文件路径
        chip (str): 目标芯片类型
        calib_table (str): 校准表文件路径
        mix_table (str): 混合量化配置表路径
        mode (str): 模型运行模式（INT4, INT8, 或其他浮点类型）
        quanted_mlir_file (str): 量化后的MLIR模型文件路径
        module (pymlir.module): MLIR模块实例
        parser (MlirParser): MLIR解析器实例
        weight_file (str): 权重文件路径
    """
    
    def __init__(self, fp32_mlir, chip: str, calib_table: str = None, mix_table: str = None, fp_type: str = 'auto'):
        """
        初始化混合量化模型
        
        Args:
            fp32_mlir (str): FP32精度的MLIR模型文件路径
            chip (str): 目标芯片类型
            calib_table (str, optional): 校准表文件路径
            mix_table (str, optional): 混合量化配置表路径
            fp_type (str, optional): 浮点类型，默认为'auto'
        """
        self.fp32_mlir = fp32_mlir
        self.chip = chip
        self.calib_table = None
        self.mix_table = None
        
        # 根据输入参数确定模型运行模式
        if calib_table and fp_type == "int4":
            self.mode = "INT4"
            self.calib_table = calib_table
            self.mix_table = mix_table
        elif calib_table:
            self.mode = "INT8"
            self.calib_table = calib_table
            self.mix_table = mix_table
        else:
            # 如果没有校准表，则使用浮点模式
            if fp_type == 'auto':
                self.mode = FLOAT_MAP[chip]  # 根据芯片类型自动选择浮点类型
            else:
                self.mode = fp_type
                # 检查芯片是否支持指定的浮点类型
                if fp_type not in chip_support_mix_fp_type[self.chip]:
                    print(f'参数错误，fp_type:{fp_type} 不被 {chip} 支持')
                    exit(1)
        
        # 生成量化后的MLIR文件名
        self.quanted_mlir_file = f'{fp32_mlir}.{"mix" if mix_table else self.mode}.tune.mlir'
        
        # 执行MLIR量化降低
        mlir_lowering(
            self.fp32_mlir, 
            self.quanted_mlir_file, 
            self.mode, 
            self.chip, 
            1, 1,
            self.calib_table, 
            False, 
            self.mix_table
        )
        
        # 加载量化后的MLIR模块
        self.module = pymlir.module()
        self.module.load(self.quanted_mlir_file)
        
        # 创建MLIR解析器
        self.parser = MlirParser(self.quanted_mlir_file)
        self.weight_file = self.parser.module_weight_file
    
    def infer(self, data: list, global_compare_layers: list = None):
        """
        执行模型推理
        
        Args:
            data (list): 输入数据列表
            global_compare_layers (list, optional): 需要比较的层列表
        
        Returns:
            dict: 输出结果字典
        """
        # 设置输入张量
        for k, v in zip(self.module.input_names, data):
            self.module.set_tensor(k, v, v.shape)
        
        # 执行推理
        self.module.invoke()
        
        outputs = {}
        # 如果没有指定比较层，则获取所有输出层
        if global_compare_layers is None:
            for name in self.module.output_names:
                outputs[name] = self.module.get_tensor(name).copy()
        else:
            # 只指定的比较层
            for name in global_compare_layers:
                outputs[name] = self.module.get_tensor(name).copy()
        
        return outputs
    
    def infer_from(self, top_op_name, input_data_dict: dict, extra_input_data_dict: dict,
                   global_compare_layers: list = None):
        """
        从指定操作开始执行推理
        
        Args:
            top_op_name (str): 开始推理的操作名称
            input_data_dict (dict): 输入数据字典
            extra_input_data_dict (dict): 额外输入数据字典
            global_compare_layers (list, optional): 需要比较的层列表
        
        Returns:
            dict: 输出结果字典
        """
        # 设置主要输入数据
        for k in input_data_dict:
            self.module.set_tensor_from_int(k, input_data_dict[k], input_data_dict[k].shape)
            print(f'infer_from 设置张量:{k}')
            
            # 获取当前张量的下一个操作
            next_ops = self.parser.get_next_op_by_op_name(k)
            print(f'infer_from {k} 的下一个操作:{next_ops}')
            
            # 如果下一个操作是Cast操作，则立即执行
            for next_op in next_ops:
                op = self.parser.get_op_by_op_name(next_op)
                if op.type == "tpu.Cast":
                    print(f'在CastOp:{next_op} 处执行')
                    self.module.invoke_at(next_op)
        
        # 设置额外输入数据
        for k in extra_input_data_dict:
            print(f'infer_from 设置额外张量:{k}')
            self.module.set_tensor_from_int(k, extra_input_data_dict[k], extra_input_data_dict[k].shape)
            
            # 获取当前张量的下一个操作
            next_ops = self.parser.get_next_op_by_op_name(k)
            print(f'infer_from {k} 的下一个操作:{next_ops}')
            
            # 如果下一个操作是Cast操作，则立即执行
            for next_op in next_ops:
                op = self.parser.get_op_by_op_name(next_op)
                if op.type == "tpu.Cast":
                    print(f'在CastOp:{next_op} 处执行')
                    self.module.invoke_at(next_op)
        
        # 从指定操作开始执行推理
        print(f'从 {top_op_name} 开始执行')
        self.module.invoke_from(top_op_name)
        
        outputs = {}
        # 如果没有指定比较层，则获取所有输出层
        if global_compare_layers is None:
            for name in self.module.output_names:
                outputs[name] = self.module.get_tensor(name).copy()
        else:
            # 获取指定的比较层
            for name in global_compare_layers:
                outputs[name] = self.module.get_tensor(name).copy()
        
        return outputs
    
    def clean(self):
        """清理资源，删除临时文件"""
        try:
            # 删除MLIR模块
            del self.module
            # 删除量化后的MLIR文件
            os.remove(self.quanted_mlir_file)
            # 删除权重文件
            os.remove(self.weight_file)
        except Exception as e:
            print(f"清理资源时发生错误: {e}")
```



```Python
class MixPrecSearcher:
    """
    混合精度搜索器类，用于自动搜索最优的混合精度量化配置
    
    Attributes:
        args: 命令行参数
        fp32_mlir: FP32精度的MLIR模型文件路径
        calib_table: 校准表文件路径
        loss_table: 损失表文件路径
        quantize_table: 量化表文件路径
        chip: 目标芯片类型
        mix_mode: 混合精度模式
        parser: MLIR解析器实例
        batch_size: 批处理大小
        input_num: 输入数量
        num_sample: 样本数量
        debug_cmd: 调试命令
        logger: 日志记录器
        ref_activations: 参考激活值字典
        int8_activations: INT8激活值字典
        post_process_path: 后处理脚本路径
        post_process_name: 后处理模块名称
    """
    
    def __init__(self, args):
        """
        初始化混合精度搜索器
        
        Args:
            args: 命令行参数
        """
        self.args = args
        self.fp32_mlir = args.mlir_file
        self.calib_table = args.calibration_table
        
        # 初始化可选参数
        if 'loss_table' in args:
            self.loss_table = args.loss_table
        if 'quantize_table' in args:
            self.quantize_table = args.quantize_table
            
        self.chip = args.chip
        
        # 确定混合精度模式
        if args.fp_type == 'auto':
            self.mix_mode = FLOAT_MAP[self.chip]
        else:
            self.mix_mode = args.fp_type
            # 检查芯片是否支持指定的浮点类型
            if args.fp_type not in chip_support_mix_fp_type[self.chip]:
                print(f'参数错误，fp_type:{args.fp_type} 不被 {self.chip} 支持')
                exit(1)
        
        # 初始化MLIR解析器
        self.parser = MlirParser(args.mlir_file)
        self.batch_size = self.parser.get_batch_size()
        self.input_num = self.parser.get_input_num()
        self.num_sample = 0
        
        # 解析调试命令
        self.debug_cmd = parse_debug_cmd(args.debug_cmd)
        log_level = "DEBUG" if 'debug_log' in self.debug_cmd else "INFO"
        
        # 初始化日志记录器
        if '_logger' in args:
            self.logger = args._logger
        else:
            try:
                if args.inference_num > 0:
                    self.logger = logger('SensitiveLayerSearcher', log_level=log_level)
            except:
                self.logger = logger('MixPrecSearcher', log_level=log_level)
        
        # 初始化输入数据
        self._init_inputs(args)
        
        # 初始化后处理
        try:
            if args.post_process is not None:
                self.post_process_path = args.post_process
                if self.post_process_path.find('/') != -1:
                    self.post_process_name = self.post_process_path.split('/')[-1]
                else:
                    self.post_process_name = self.post_process_path
                self.post_process_name = self.post_process_name[:-3]
            else:
                self.post_process_path = None
                self.post_process_name = None
        except:
            self.post_process_path = None
            self.post_process_name = None
    
    def disable_print(self):
        """禁用打印输出（用于静默模式）"""
        if 'debug_log' not in self.debug_cmd:
            self.stdout = sys.stdout
            sys.stdout = open(os.devnull, 'w')
    
    def enable_print(self):
        """启用打印输出"""
        if 'debug_log' not in self.debug_cmd:
            sys.stdout.close()
            sys.stdout = self.stdout
    
    def _init_inputs(self, args):
        """
        初始化输入数据
        
        Args:
            args: 命令行参数
        """
        self.ref_activations = {}  # 参考激活值（FP32）
        tune_idx = 0
        self.ref_activations[tune_idx] = {}
        
        # 获取输入操作名称
        input_names = [op.name for op in self.parser.inputs]
        
        # 初始化数据选择器
        ds = DataSelector(args.dataset, args.input_num, args.data_list)
        ppa_list = []
        
        # 处理不同类型的数据集
        if ds.all_image:
            # 处理图像数据
            for i in range(self.input_num):
                ppa = preprocess()
                ppa.load_config(self.parser.get_input_op_by_idx(i))
                ppa_list.append(ppa)
            
            # 调整数据列表长度以匹配批处理大小
            n = len(ds.data_list) % self.batch_size
            if n != 0:
                for i in range(self.batch_size - n):
                    ds.data_list.append(ds.data_list[-1])
            
            self.num_sample = len(ds.data_list) // self.batch_size
            batched_idx = 0
            batched_inputs = self.input_num * ['']
            
            for data in ds.data_list:
                inputs = data.split(',')
                inputs = [s.strip() for s in inputs]
                assert (len(inputs) == self.input_num)
                
                batched_idx += 1
                for i, input in enumerate(input_names):
                    batched_inputs[i] += '{},'.format(inputs[i])
                    if batched_idx == self.batch_size:
                        # 预处理批量输入
                        x = ppa_list[i].run(batched_inputs[i][:-1])
                        count = self.parser.get_user_count_by_op_name(input)
                        self.ref_activations[tune_idx][input] = [x, count]
                
                if batched_idx == self.batch_size:
                    tune_idx += 1
                    batched_idx = 0
                    batched_inputs = self.input_num * ['']
                    self.ref_activations[tune_idx] = {}
        
        elif ds.all_npy:
            # 处理NPY文件
            self.num_sample = len(ds.data_list)
            self.input_data_buffer = [[] for i in range(self.num_sample)]
            
            for data in ds.data_list:
                inputs = data.split(',')
                inputs = [s.strip() for s in inputs]
                assert (len(inputs) == self.input_num)
                
                for name, npy in zip(input_names, inputs):
                    x = np.load(npy)
                    count = self.parser.get_user_count_by_op_name(name)
                    self.ref_activations[tune_idx][name] = [x, count]
                
                tune_idx += 1
                self.ref_activations[tune_idx] = {}
        
        elif ds.all_npz:
            # 处理NPZ文件
            self.num_sample = len(ds.data_list)
            self.input_data_buffer = [[] for i in range(self.num_sample)]
            input_names = [op.name for op in self.parser.inputs]
            
            for data in ds.data_list:
                npz = np.load(data)
                for name in input_names:
                    count = self.parser.get_user_count_by_op_name(name)
                    self.ref_activations[tune_idx][name] = [npz[name], count]
                
                tune_idx += 1
                self.ref_activations[tune_idx] = {}
        
        else:
            raise RuntimeError("数据集格式不正确")
        
        # 初始化INT8激活值字典（深拷贝FP32参考值）
        self.int8_activations = copy.deepcopy(self.ref_activations)
    
    def _gen_mix_table(self, mix_ops):
        """
        生成混合量化配置表
        
        Args:
            mix_ops: 需要使用混合精度的操作列表
        
        Returns:
            str: 配置文件路径
        """
        target_file = "tmp_mix_table.txt"
        with open(target_file, 'w') as f:
            for mix_op in mix_ops:
                f.write("{} {}\n".format(mix_op, self.mix_mode))
        return target_file
    
    def _gen_mix_table_4_8(self, mix_ops, mix_mode):
        """
        生成INT4/INT8混合量化配置表
        
        Args:
            mix_ops: 需要使用混合精度的操作列表
            mix_mode: 混合精度模式
        
        Returns:
            str: 配置文件路径
        """
        target_file = "tmp_mix_table.txt"
        with open(target_file, 'w') as f:
            for mix_op in mix_ops:
                f.write("{} {}\n".format(mix_op, mix_mode))
        return target_file
    
    def _cal_sqnr(self, signal_raw, signal_dequant, remove_zero=False):
        """
        计算信号量化噪声比(SQNR)
        
        Args:
            signal_raw: 原始信号
            signal_dequant: 反量化信号
            remove_zero: 是否移除零值
        
        Returns:
            float: SQNR值
        """
        # SQNR是非对称的，不能交换signal_raw和signal_dequant的顺序
        raw = signal_raw.flatten()
        dequant = signal_dequant.flatten()
        
        if remove_zero:
            idx = dequant != 0
            raw = raw[idx]
            dequant = dequant[idx]
        
        noise = raw - dequant
        
        avg_raw = np.sum(raw) / raw.size
        avg_noise = np.sum(noise) / noise.size
        
        raw_zero_mean = raw - avg_raw
        noise_zero_mean = noise - avg_noise
        
        var_raw_zero_mean = np.sum(np.square(raw_zero_mean))
        var_noise_zero_mean = np.sum(np.square(noise_zero_mean))
        
        if var_noise_zero_mean == 0.0:
            return math.inf
        
        sqnr = 10 * np.log10(var_raw_zero_mean / var_noise_zero_mean)
        
        return sqnr
    
    def _sqnr_loss(self, preds, gt_preds, layers_rate):
        """
        计算基于SQNR的损失
        
        Args:
            preds: 预测结果
            gt_preds: 参考结果
            layers_rate: 各层权重
        
        Returns:
            float: 损失值
        """
        ret = 0
        cnt, i = 0, 0
        
        for name1, name2 in zip(gt_preds, preds):
            a = gt_preds[name1]
            b = preds[name2]
            
            if a.dtype != b.dtype or a.shape != b.shape:
                raise RuntimeError(f"计算损失失败: {name1} vs {name2}")
            
            loss = self._cal_sqnr(a, b)
            if not math.isinf(loss):
                ret += -loss * a.size * layers_rate[i]
                cnt += a.size
                i += 1
        
        ret = ret / sum(layers_rate)
        
        if ret == 0 and cnt == 0:
            return -math.inf
        else:
            return ret / cnt
    
    def _cos_loss(self, preds, gt_preds, layers_rate):
        """
        计算基于余弦相似度的损失
        
        Args:
            preds: 预测结果
            gt_preds: 参考结果
            layers_rate: 各层权重
        
        Returns:
            float: 损失值
        """
        cos, i = 0, 0
        
        for name1, name2 in zip(gt_preds, preds):
            a = gt_preds[name1]
            b = preds[name2]
            
            if a.dtype != b.dtype or a.shape != b.shape:
                raise RuntimeError(f"计算损失失败: {name1} vs {name2}")
            
            cos += layers_rate[i] * cos_sim(a.reshape(-1), b.reshape(-1))
            i += 1
        
        return cos / sum(layers_rate)
    
    def _snr_loss(self, preds, gt_preds, layers_rate):
        """
        计算基于SNR的损失
        
        Args:
            preds: 预测结果
            gt_preds: 参考结果
            layers_rate: 各层权重
        
        Returns:
            float: 损失值
        """
        snr, i = 0, 0
        
        for name1, name2 in zip(gt_preds, preds):
            a = gt_preds[name1]
            b = preds[name2]
            
            if a.dtype != b.dtype or a.shape != b.shape:
                raise RuntimeError(f"计算损失失败: {name1} vs {name2}")
            
            a = a.flatten()
            b = b.flatten()
            
            noise_power = np.power(b-a, 2).sum(axis=-1)
            signal_power = np.power(a, 2).sum(axis=-1)
            
            snr = layers_rate[i] * (noise_power) / (signal_power + 1e-7)
            i += 1
        
        return snr / sum(layers_rate)
    
    def _loss(self, preds, gt_preds, layers_rate=None, type='cos'):
        """
        计算损失的统一接口
        
        Args:
            preds: 预测结果
            gt_preds: 参考结果
            layers_rate: 各层权重
            type: 损失类型 ('cos', 'sqnr', 'snr')
        
        Returns:
            float: 损失值
        """
        assert type in ['cos', 'sqnr', 'snr']
        
        if layers_rate is None:
            layers_rate = len(preds) * [1]
        
        if type == 'cos':
            return self._cos_loss(preds, gt_preds, layers_rate)
        elif type == 'sqnr':
            return self._sqnr_loss(preds, gt_preds, layers_rate)
        elif type == 'snr':
            return self._snr_loss(preds, gt_preds, layers_rate)
    
    def get_input_fp32_tensor(self, i, op_name):
        """
        获取FP32输入张量
        
        Args:
            i: 样本索引
            op_name: 操作名称
        
        Returns:
            np.ndarray: FP32张量
        """
        if op_name in self.ref_activations[i]:
            return self.ref_activations[i][op_name][0]
        self.exception_occur(f'错误，索引:{i} 操作名称:{op_name} 不在ref_activations中')
    
    def exception_occur(self, info):
        """
        异常处理
        
        Args:
            info: 异常信息
        """
        if 'debug_log' in self.debug_cmd:
            print(info)
        else:
            raise Exception(info)
    
    def get_input_int8_tensor(self, i, op_name, ret_fp32=False):
        """
        获取INT8输入张量
        
        Args:
            i: 样本索引
            op_name: 操作名称
            ret_fp32: 是否返回FP32值
        
        Returns:
            np.ndarray: INT8或FP32张量
        """
        if op_name in self.int8_activations[i]:
            if ret_fp32:
                return self.int8_activations[i][op_name][2]
            else:
                return self.int8_activations[i][op_name][0]
        self.exception_occur(f'错误，索引:{i} 操作名称:{op_name} 不在int8_activations中')
    
    def layer_used_by_successor_op(self, pre_layer, op_name, parser):
        """
        检查前驱层是否被后续操作使用
        
        Args:
            pre_layer: 前驱层名称
            op_name: 当前操作名称
            parser: MLIR解析器
        
        Returns:
            bool: 是否被使用
        """
        check = False
        for op in parser.ops:
            if check:
                if pre_layer in op.opds:
                    return True
            if op_name == op.name:
                check = True
        return False
    
    def clear_tensor(self, i, op_name, data_dict, parser, all_pre_layers):
        """
        清理张量数据
        
        Args:
            i: 样本索引
            op_name: 操作名称
            data_dict: 数据字典
            parser: MLIR解析器
            all_pre_layers: 所有前驱层
        """
        for layer in all_pre_layers:
            if not self.layer_used_by_successor_op(layer, op_name, parser):
                if layer in data_dict[i]:
                    if data_dict[i][layer][1] <= 1:
                        data_dict[i].pop(layer)
                        if i == 0:
                            self.logger.print_dbg(f'删除 {layer}')
                    else:
                        data_dict[i][layer][1] -= 1
                        if i == 0:
                            self.logger.print_dbg(f'减少 {layer} 引用计数')
        
        self.logger.print_dbg('数据字典状态:')
        for layer in data_dict[0]:
            self.logger.print_dbg(f'层:{layer} 存在，引用计数:{data_dict[0][layer][1]}')
    
    def gen_ref_tensor(self, i, op_name, data_dict, model, is_int8_data=False):
        """
        生成参考张量
        
        Args:
            i: 样本索引
            op_name: 操作名称
            data_dict: 数据字典
            model: 模型实例
            is_int8_data: 是否为INT8数据
        
        Returns:
            bool: 是否成功生成
        """
        if op_name in data_dict[i]:
            return False
        
        # 获取当前操作的输入操作
        input_ops = model.parser.get_pre_op_by_op_name(op_name)
        
        for input_op in input_ops:
            data = data_dict[i][input_op][0]
            if data is None:
                raise Exception(f"{op_name} 的输入:{input_op} 不存在")
            
            # 设置输入张量
            if is_int8_data:
                model.module.set_tensor_from_int(input_op, data, data.shape)
            else:
                model.module.set_tensor(input_op, data, data.shape)
        
        if len(input_ops) > 0:
            # 执行当前操作
            value = model.module.invoke_at(op_name).copy()
            self.logger.print_dbg(f'在 {op_name} 处执行')
            
            fp32_v = None
            if is_int8_data:
                fp32_v = model.module.get_fp32_tensor(op_name)
            
            # 获取用户引用计数
            count = model.parser.get_user_count_by_op_name(op_name)
            
            # 存储生成的张量
            if fp32_v is None:
                data_dict[i][op_name] = [value.copy(), count]
            else:
                data_dict[i][op_name] = [value.copy(), count, fp32_v]
        
        return True
    
    def visual_tensor_diff(self, name, cos, int8_out, fp32_out):
        """
        可视化张量差异
        
        Args:
            name: 可视化名称
            cos: 余弦相似度
            int8_out: INT8输出
            fp32_out: FP32输出
        
        Returns:
            str: 可视化文件路径
        """
        data_size = fp32_out.size
        max_sampling = 10000
        
        if 'max_sampling' in self.debug_cmd:
            max_sampling = int(self.debug_cmd['max_sampling'])
        
        # 采样以避免图形过于复杂
        if data_size > max_sampling:
            step = data_size // max_sampling
        else:
            step = 1
        
        index = np.arange(data_size)
        index = index[::step]
        rows = 2
        
        # 创建子图
        fig = make_subplots(
            rows=rows,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.1,
            subplot_titles=(
                "参考值 vs 目标值",
                f"参考值 - 目标值   余弦相似度:{cos:.6f}"
            )
        )
        
        # 添加参考值曲线
        fig.add_trace(go.Scattergl(
            y=fp32_out.reshape([-1])[::step],
            x=index,
            name='参考值',
            mode='lines+markers',
            marker={"size": 6, "symbol": 300},
            line={"width": 1}
        ), row=1, col=1)
        
        # 添加目标值曲线
        fig.add_trace(go.Scattergl(
            y=int8_out.reshape([-1])[::step],
            x=index,
            name='目标值',
            mode='lines+markers',
            marker={"size": 6, "symbol": 304, "opacity": 0.8},
            line={"width": 1}
        ), row=1, col=1)
        
        # 添加差异曲线
        fig.add_trace(go.Scattergl(
            y=(fp32_out - int8_out).reshape([-1])[::step],
            x=index,
            name='差异',
            line={"width": 1}
        ), row=2, col=1)
        
        # 更新布局
        fig.update_layout(height=400 * rows, width=900)
        fig.update_layout(margin=dict(l=5, r=10, t=20, b=0))
        fig.update_layout(legend=dict(yanchor="top", y=0.95, xanchor="right", x=0.95))
        
        # 保存可视化结果
        tmpstr = f'./tensor_diff_fp32_vs_int8/{name.replace("/", "_")}'
        if 'dump_tensor_diff_by_html' in self.debug_cmd:
            fig.write_html(tmpstr + '.html')
            return tmpstr + '.html'
        fig.write_image(tmpstr + '.png')
        return tmpstr + '.png'
    
    def get_extra_input_tensor(self, op_name, parser):
        """
        获取额外的输入张量
        
        Args:
            op_name: 操作名称
            parser: MLIR解析器
        
        Returns:
            set: 额外输入张量集合
        """
        first_seg, second_seg, extra_input = [], [], []
        op_names = parser.get_op_name_list()
        
        # 找到操作在列表中的位置
        for i, name in enumerate(op_names):
            if op_name == name:
                first_seg = op_names[:i]
                second_seg = op_names[i + 1:]
                break
        
        # 查找额外输入
        for name in second_seg:
            extra_input.extend([i for i in parser.get_pre_op_by_op_name(name) if i in first_seg])
        
        return set(extra_input)
    
    def clear_ref_tensor(self, op_name, parser, data_dict):
        """
        清理参考张量
        
        Args:
            op_name: 操作名称
            parser: MLIR解析器
            data_dict: 数据字典
        """
        all_pre_layers = []
        find_all_pre_layers(all_pre_layers, op_name, parser, list(data_dict[0].keys()))
        
        for idx in range(self.num_sample):
            self.clear_tensor(idx, op_name, data_dict, parser, all_pre_layers)
    
    def clear_ref_tensor2(self, op_name, top_parser, int8_parser, data_dict):
        """
        清理参考张量（双解析器版本）
        
        Args:
            op_name: 操作名称
            top_parser: TOP解析器
            int8_parser: INT8解析器
            data_dict: 数据字典
        """
        all_pre_layers = []
        find_all_pre_layers(all_pre_layers, op_name, top_parser, list(data_dict[0].keys()))
        
        all_pre_layers2 = []
        find_all_pre_layers(all_pre_layers2, op_name, int8_parser, list(data_dict[0].keys()))
        
        # 合并并去重
        all_pre_layers.extend(all_pre_layers2)
        all_pre_layers = set(all_pre_layers)
        
        # 检查哪些层被使用
        all_pre_layers_used = len(all_pre_layers) * [False]
        for i, layer in enumerate(all_pre_layers):
            if self.layer_used_by_successor_op(layer, op_name, top_parser):
                all_pre_layers_used[i] = True
            if self.layer_used_by_successor_op(layer, op_name, int8_parser):
                all_pre_layers_used[i] = True
        
        # 清理未使用的层
        for i in range(self.num_sample):
            for used, layer in zip(all_pre_layers_used, all_pre_layers):
                if not used:
                    if layer in data_dict[i]:
                        if data_dict[i][layer][1] <= 1:
                            data_dict[i].pop(layer)
                            if i == 0:
                                self.logger.print_dbg(f'删除 {layer}')
                        else:
                            data_dict[i][layer][1] -= 1
                            if i == 0:
                                self.logger.print_dbg(f'减少 {layer} 引用计数')
        
        self.logger.print_dbg('数据字典状态:')
        for layer in data_dict[0]:
            self.logger.print_dbg(f'  层:{layer} 存在，引用计数:{data_dict[0][layer][1]}')
    
    def collect_op_input_tensor(self, idx, op_name, extra_input, fp_layer_list=None):
        """
        收集操作的输入张量
        
        Args:
            idx: 样本索引
            op_name: 操作名称
            extra_input: 额外输入
            fp_layer_list: FP层列表
        
        Returns:
            tuple: 输入数据字典和额外输入数据字典
        """
        input_data_dict = {}
        
        # 收集主要输入
        for i, input in enumerate(self.parser.get_pre_op_by_op_name(op_name)):
            self.logger.print_dbg(f'{op_name} 的主要输入{i}:{input}')
            
            # 根据层类型选择合适的输入数据
            if fp_layer_list is not None and input in fp_layer_list:
                input_data_dict[input] = self.get_input_fp32_tensor(idx, input)
            else:
                input_data_dict[input] = self.get_input_int8_tensor(idx, input)
            
            # 如果输入在额外输入中，则从额外输入中移除
            if input in extra_input:
                extra_input.remove(input)
        
        # 收集额外输入
        extra_input_data_dict = {}
        for i, input in enumerate(extra_input):
            self.logger.print_dbg(f'{op_name} 的其他输入{i}:{input}')
            
            # 根据层类型选择合适的输入数据
            if fp_layer_list is not None and input in fp_layer_list:
                extra_input_data_dict[input] = self.get_input_fp32_tensor(idx, input)
            else:
                extra_input_data_dict[input] = self.get_input_int8_tensor(idx, input)
        
        return input_data_dict, extra_input_data_dict
    
    def extract_global_layers(self):
        """
        提取全局比较层
        
        Returns:
            tuple: 全局比较层列表、层权重和所有前驱层
        """
        global_compare_layers = None
        layers_rate = None
        all_pre_layers = None
        
        if self.args.global_compare_layers != '':
            # 解析全局比较层
            global_compare_layers = [i.strip() for i in self.args.global_compare_layers.split(',')]
            
            # 解析层权重
            layers_rate = [float(i.split(':')[-1].strip()) for i in global_compare_layers if ':' in i]
            
            # 提取层名称（去掉权重部分）
            global_compare_layers = [i.split(':')[0].strip() for i in global_compare_layers]
        
        if global_compare_layers is not None:
            # 查找所有前驱层
            all_pre_layers = []
            for layer in global_compare_layers:
                pre_layers = []
                find_all_pre_layers(pre_layers, layer, self.parser)
                all_pre_layers.extend(pre_layers)
            
            all_pre_layers = set(all_pre_layers)
            
            # 检查权重是否有效
            if len(layers_rate) > 0:
                if len(global_compare_layers) != len(layers_rate):
                    print('global_compare_layers 格式错误')
                    exit(1)
                if 1 != sum(layers_rate):
                    print('global_compare_layers 权重错误')
                    exit(1)
            else:
                # 如果没有指定权重，则使用均匀权重
                layers_rate = len(global_compare_layers) * [1]
        
        return global_compare_layers, layers_rate, all_pre_layers
    
    def run_model(self, model, float_type, global_compare_layers, layers_rate, predictions_gt):
        """
        运行模型
        
        Args:
            model: 模型实例
            float_type: 是否为浮点模式
            global_compare_layers: 全局比较层
            layers_rate: 层权重
            predictions_gt: 参考预测结果
        
        Returns:
            float: 平均余弦相似度
        """
        outputs_cos = 0
        
        if float_type:
            self.disable_print()
            self.logger.print_info(f"运行浮点模式: {self.fp32_mlir}")
        else:
            self.logger.print_info(f"运行INT8模式: {self.fp32_mlir}")
        
        for idx in range(self.num_sample):
            # 准备输入数据
            data = []
            for name in list(self.ref_activations[idx].keys()):
                data.append(self.ref_activations[idx][name][0])
            
            # 执行推理
            outputs = model.infer(data, global_compare_layers)
            
            # 执行后处理（如果有）
            if self.post_process_path:
                module_path = self.post_process_path
                module_name = self.post_process_name
                
                # 动态导入后处理模块
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                modulevar = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(modulevar)
                
                outputs = modulevar.PostProcess(outputs)
            
            # 存储参考结果或计算损失
            if float_type:
                predictions_gt.append(outputs)
            else:
                outputs_cos += self._loss(outputs, predictions_gt[idx], layers_rate)
        
        # 计算平均余弦相似度
        outputs_cos = outputs_cos / self.num_sample
        
        return outputs_cos
    
    def run_model_new(self, model, float_type, global_compare_layers, layers_rate, predictions_gt, sample_num=1, loss_method=['cos', 'snr']):
        """
        运行模型（新版本）
        
        Args:
            model: 模型实例
            float_type: 是否为浮点模式
            global_compare_layers: 全局比较层
            layers_rate: 层权重
            predictions_gt: 参考预测结果
            sample_num: 样本数量
            loss_method: 损失计算方法
        
        Returns:
            tuple: 余弦损失和SNR损失
        """
        outputs_cos = 0
        outputs_snr = 0
        
        if float_type:
            self.disable_print()
            self.logger.print_info(f"运行浮点模式: {self.fp32_mlir}")
        else:
            self.logger.print_info(f"运行INT8模式: {self.fp32_mlir}")
        
        # 确定实际运行的样本数量
        num = 1 if sample_num == -1 else self.num_sample
        
        for idx in range(num):
            # 准备输入数据
            data = []
            for name in list(self.ref_activations[idx].keys()):
                data.append(self.ref_activations[idx][name][0])
            
            # 执行推理
            outputs = model.infer(data, global_compare_layers)
            
            # 执行后处理（如果有）
            if self.post_process_path:
                module_path = self.post_process_path
                module_name = self.post_process_name
                
                # 动态导入后处理模块
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                modulevar = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(modulevar)
                
                outputs = modulevar.PostProcess(outputs)
            
            # 存储参考结果或计算损失
            if float_type:
                predictions_gt.append(outputs)
            else:
                if 'snr' in loss_method:
                    outputs_snr += self._loss(outputs, predictions_gt[idx], layers_rate, type='snr')
                if 'cos' in loss_method:
                    outputs_cos += self._loss(outputs, predictions_gt[idx], layers_rate)
        
        # 计算平均损失
        outputs_cos = 1 - outputs_cos / num
        outputs_snr = outputs_snr / num
        
        # 根据损失方法返回相应的结果
        if 'cos' in loss_method and 'snr' in loss_method:
            return outputs_cos, outputs_snr
        elif 'cos' in loss_method:
            return outputs_cos
        else:
            return outputs_snr
    
    def run_model_fast(self, model, float_type, global_compare_layers, layers_rate, predictions_gt, count):
        """
        快速运行模型（单样本）
        
        Args:
            model: 模型实例
            float_type: 是否为浮点模式
            global_compare_layers: 全局比较层
            layers_rate: 层权重
            predictions_gt: 参考预测结果
            count: 样本索引
        
        Returns:
            tuple: 余弦损失和SNR损失
        """
        outputs_cos = 0
        outputs_snr = 0
        
        if float_type:
            self.disable_print()
            self.logger.print_info(f"运行浮点模式: {self.fp32_mlir}")
        else:
            self.logger.print_info(f"运行INT8模式: {self.fp32_mlir}")
        
        # 准备输入数据
        data = []
        for name in list(self.ref_activations[count].keys()):
            data.append(self.ref_activations[count][name][0])
        
        # 执行推理
        outputs = model.infer(data, global_compare_layers)
        
        # 执行后处理（如果有）
        if self.post_process_path:
            module_path = self.post_process_path
            module_name = self.post_process_name
            
            # 动态导入后处理模块
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            modulevar = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(modulevar)
            
            outputs = modulevar.PostProcess(outputs)
        
        # 存储参考结果或计算损失
        if float_type:
            predictions_gt.append(outputs)
        else:
            outputs_snr += self._loss(outputs, predictions_gt[count], layers_rate, type='snr')
            outputs_cos += self._loss(outputs, predictions_gt[count], layers_rate)
        
        # 计算损失
        outputs_cos = 1 - outputs_cos
        outputs_snr = outputs_snr
        
        return outputs_cos, outputs_snr
    
    def run_model_loss_snr(self, model, float_type, global_compare_layers, layers_rate, predictions_gt):
        """
        运行模型并计算SNR损失
        
        Args:
            model: 模型实例
            float_type: 是否为浮点模式
            global_compare_layers: 全局比较层
            layers_rate: 层权重
            predictions_gt: 参考预测结果
        
        Returns:
            float: 平均SNR损失
        """
        outputs_cos = 0
        
        if float_type:
            self.disable_print()
            self.logger.print_info(f"运行浮点模式: {self.fp32_mlir}")
        else:
            self.logger.print_info(f"运行INT8模式: {self.fp32_mlir}")
        
        for idx in range(self.num_sample):
            # 准备输入数据
            data = []
            for name in list(self.ref_activations[idx].keys()):
                data.append(self.ref_activations[idx][name][0])
            
            # 执行推理
            outputs = model.infer(data, global_compare_layers)
            
            # 执行后处理（如果有）
            if self.post_process_path:
                module_path = self.post_process_path
                module_name = self.post_process_name
                
                # 动态导入后处理模块
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                modulevar = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(modulevar)
                
                outputs = modulevar.PostProcess(outputs)
            
            # 存储参考结果或计算损失
            if float_type:
                predictions_gt.append(outputs)
            else:
                outputs_cos += self._loss(outputs, predictions_gt[idx], layers_rate, type='snr')
        
        # 计算平均SNR损失
        outputs_cos = outputs_cos / self.num_sample
        
        return outputs_cos
    
    def get_fixed_float_layers(self, model, global_compare_layers, layers_rate, predictions_gt):
        """
        获取固定为浮点的层
        
        Args:
            model: 模型实例
            global_compare_layers: 全局比较层
            layers_rate: 层权重
            predictions_gt: 参考预测结果
        
        Returns:
            list: 浮点层列表
        """
        float_ops = []
        self.logger.print_info(f"运行INT8模式: {self.fp32_mlir}")
        
        # 运行一次推理以初始化
        for idx in range(1):
            data = []
            for name in list(self.ref_activations[idx].keys()):
                data.append(self.ref_activations[idx][name][0])
            outputs = model.infer(data, global_compare_layers)
        
        # 检查所有张量的量化信息
        for op in model.module.all_tensor_names:
            qinfo = model.module.get_tensor_qinfo(op)
            if qinfo.dtype != "I8" and qinfo.dtype != "U8":
                float_ops.append(op)
        
        return float_ops
    
    def get_full_op_list(self, float_model, int8_model, fp_op_names, int8_op_names):
        """
        获取完整的操作列表
        
        Args:
            float_model: 浮点模型
            int8_model: INT8模型
            fp_op_names: 浮点操作名称列表
            int8_op_names: INT8操作名称列表
        
        Returns:
            dict: 完整的操作字典
        """
        full_op_list, cur_fp_op_idx = {}, 0
        
        for int8_op in int8_op_names:
            # 查找INT8操作在浮点操作列表中的位置
            pos = fp_op_names.index(int8_op) if int8_op in fp_op_names else -1
            
            if -1 == pos:
                # 如果INT8操作不在浮点操作列表中，直接添加
                full_op_list[int8_op] = int8_model.parser.get_op_by_op_name(int8_op)
            else:
                # 添加从上次浮点位置到当前INT8操作位置之间的所有浮点操作
                for i in range(cur_fp_op_idx, pos + 1):
                    full_op_list[fp_op_names[i]] = float_model.parser.get_op_by_op_name(fp_op_names[i])
                cur_fp_op_idx = pos + 1
        
        print('完整操作列表:', list(full_op_list.keys()))
        return full_op_list
    
    def cal_avg_layer_cos(self, op):
        """
        计算层的平均余弦相似度
        
        Args:
            op: 操作对象
        
        Returns:
            float: 平均余弦相似度
        """
        layer_cos = 0
        
        for idx in range(self.num_sample):
            # 获取INT8和FP32的输出
            int8_out = self.get_input_int8_tensor(idx, op.name, True)
            fp32_out = self.get_input_fp32_tensor(idx, op.name)
            
            # 计算余弦相似度
            cos = cos_sim(int8_out, fp32_out)
            layer_cos += cos
            
            # 如果需要可视化张量差异
            if 'visual_tensor' in self.debug_cmd and (
                    op.name in self.debug_cmd['visual_tensor'] or self.debug_cmd['visual_tensor'] == 'all'):
                plot_path = self.visual_tensor_diff(f'int8_model_{op.name}_{idx}', cos, int8_out, fp32_out)
                if idx == 0:
                    self.dot_log.add_node_label(op.name, '启用张量差异可视化，请点击查看')
                    self.dot_log.add_node_attr(op.name, 'URL', plot_path)
        
        # 计算平均余弦相似度
        avg_cos = layer_cos / self.num_sample
        self.dot_log.add_node_label(op.name, f'INT8层余弦相似度:{avg_cos:.6f}')
        
        return avg_cos
    
    def gen_int8_tensor(self, op, int8_op_names, int8_model, fp_layer_list):
        """
        生成INT8张量
        
        Args:
            op: 操作对象
            int8_op_names: INT8操作名称列表
            int8_model: INT8模型
            fp_layer_list: FP层列表
        
        Returns:
            bool: 是否有INT8后续操作
        """
        have_int8_next = False
        
        if op.name in int8_op_names:
            # 检查是否有INT8后续操作
            for next_int8_op in int8_model.parser.get_next_op_by_op_name(op.name):
                if next_int8_op not in fp_layer_list:
                    have_int8_next = True
                    break
            
            if have_int8_next:
                # 为每个样本生成INT8张量
                for idx in range(self.num_sample):
                    ret = self.gen_ref_tensor(idx, op.name, self.int8_activations, int8_model, True)
                if ret:
                    self.dot_log.add_node_label(op.name, '生成INT8张量')
            else:
                self.dot_log.add_node_label(op.name, f'后续层是{self.mix_mode}，不需要生成INT8张量')
        
        return have_int8_next
    
    def gen_fp_tensor(self, op, fp_op_names, float_model):
        """
        生成浮点张量
        
        Args:
            op: 操作对象
            fp_op_names: 浮点操作名称列表
            float_model: 浮点模型
        """
        if op.name in fp_op_names:
            # 为每个样本生成浮点张量
            for idx in range(self.num_sample):
                ret = self.gen_ref_tensor(idx, op.name, self.ref_activations, float_model)
            if ret:
                self.dot_log.add_node_label(op.name, f'生成{self.mix_mode}张量')
    
    def skip_op(self, op, top_ops, have_int8_next, fp_layer_list, all_pre_layers):
        """
        检查是否需要跳过当前操作
        
        Args:
            op: 操作对象
            top_ops: TOP操作字典
            have_int8_next: 是否有INT8后续操作
            fp_layer_list: FP层列表
            all_pre_layers: 所有前驱层
        
        Returns:
            bool: 是否需要跳过
        """
        if op.name not in top_ops:
            self.dot_log.add_node_label(op.name, '操作不在TOP dialect中')
            return True
        
        if op.type in SKIP_OPERATION:
            pre_layers = self.parser.get_pre_op_by_op_name(op.name)
            for pre_layer in pre_layers:
                if pre_layer in fp_layer_list:
                    self.dot_log.add_node_label(op.name, f'将{op.name}添加到FP层列表')
                    fp_layer_list.append(op.name)
                    break
            self.dot_log.add_node_label(op.name, f'遇到量化跳过操作，类型:{op.type}，继续')
            return True
        
        if op.name in fp_layer_list:
            self.dot_log.add_node_label(op.name, f'操作是{self.mix_mode}层，继续')
            return True
        
        if not have_int8_next:
            self.dot_log.add_node_label(op.name, '不需要调用gen_ref_tensor，继续')
            return True
        
        if all_pre_layers is not None and op.name not in all_pre_layers:
            self.dot_log.add_node_label(op.name, '不在比较层的所有前驱层中，继续')
            return True
        
        return False
    
    def check_input(self, op, next_top_ops, fp_layer_list):
        """
        检查输入是否有效
        
        Args:
            op: 操作对象
            next_top_ops: 下一个TOP操作列表
            fp_layer_list: FP层列表
        
        Returns:
            bool: 输入是否有效
        """
        fp_layer_list_copy = fp_layer_list.copy()
        fp_layer_list_copy.append(op.name)
        
        tmp = ','.join(next_top_ops)
        fp_layer_list_copy.extend(next_top_ops)
        
        # 生成混合量化配置表
        mix_table = self._gen_mix_table(fp_layer_list_copy)
        mix_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table, mix_table, self.args.fp_type)
        
        # 检查输入是否存在
        for i, input in enumerate(mix_model.parser.get_pre_op_by_op_name(op.name)):
            if input in fp_layer_list_copy:
                if input not in self.ref_activations[0]:
                    return False
            else:
                if input not in self.int8_activations[0]:
                    return False
        
        return True
    
    def set_curr_layer_and_the_next_layer_mix(self, op, next_top_ops, fp_layer_list, global_compare_layers, layers_rate, predictions_gt):
        """
        设置当前层和下一层为混合精度
        
        Args:
            op: 操作对象
            next_top_ops: 下一个TOP操作列表
            fp_layer_list: FP层列表
            global_compare_layers: 全局比较层
            layers_rate: 层权重
            predictions_gt: 参考预测结果
        
        Returns:
            tuple: 输出余弦相似度和混合模型
        """
        outputs_cos = 0
        
        # 将当前层添加到FP层列表
        fp_layer_list.append(op.name)
        
        tmp = ','.join(next_top_ops)
        self.dot_log.add_node_label(op.name, f'余弦相似度过低，将{self.mix_mode}设置到{op.name}和下一个TOP操作:{tmp}')
        
        # 将下一个TOP操作添加到FP层列表
        fp_layer_list.extend(next_top_ops)
        
        # 生成混合量化配置表
        mix_table = self._gen_mix_table(fp_layer_list)
        mix_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table, mix_table, self.args.fp_type)
        
        # 获取额外输入张量
        extra_input = self.get_extra_input_tensor(op.name, self.parser)
        
        for idx in range(self.num_sample):
            # 收集输入数据
            input_data_dict, extra_input_data_dict = self.collect_op_input_tensor(
                idx, op.name, extra_input, fp_layer_list)
            
            tmp1 = ','.join(list(input_data_dict.keys()))
            tmp2 = '' if len(extra_input_data_dict) == 0 else ',额外输入数据字典:' + ','.join(
                list(extra_input_data_dict.keys()))
            
            if idx == 0:
                self.dot_log.add_node_label(op.name, f'输入数据字典:{tmp1}{tmp2}，调用infer_from')
            
            # 从当前操作开始执行推理
            outputs = mix_model.infer_from(op.name, input_data_dict, extra_input_data_dict, global_compare_layers)
            
            # 获取混合精度层的FP32输出
            mix_layer_out = mix_model.module.get_fp32_tensor(op.name)
            fp32_out = self.get_input_fp32_tensor(idx, op.name)
            
            # 计算余弦相似度
            cos = cos_sim(mix_layer_out.reshape(-1), fp32_out.reshape(-1))
            
            if idx == 0:
                self.dot_log.add_node_label(op.name, f'第一个{self.mix_mode}层:{op.name} 余弦相似度:{cos:.6f}')
            
            # 计算输出损失
            outputs_cos += self._loss(outputs, predictions_gt[idx], layers_rate)
            
            # 更新INT8激活值字典
            for next_top_op in next_top_ops:
                count = self.parser.get_user_count_by_op_name(next_top_op)
                self.int8_activations[idx][next_top_op] = [None, count, None]
                
                # 获取下一个操作
                next_ops = mix_model.parser.get_next_op_by_op_name(next_top_op)
                print(f'{next_top_op}的下一个操作:', ','.join(next_ops))
                
                for next_op in next_ops:
                    if mix_model.parser.get_op_by_op_name(next_op).type == "tpu.Cast":
                        if idx == 0:
                            self.dot_log.add_node_label(op.name, f'使用{next_op}替换{next_top_op}')
                        self.int8_activations[idx][next_top_op][0] = mix_model.module.get_tensor(next_op).copy()
                        self.int8_activations[idx][next_top_op][2] = mix_model.module.get_fp32_tensor(next_op)
        
        # 计算平均输出余弦相似度
        outputs_cos = outputs_cos / self.num_sample
        
        return outputs_cos, mix_model
    
    def search_mix_layer(self, float_model, int8_model, layer_cos_list, predictions_gt, fp_layer_list,
                      global_compare_layers, layers_rate, all_pre_layers, all_int8_cos):
        """
        搜索混合精度层
        
        Args:
            float_model: 浮点模型
            int8_model: INT8模型
            layer_cos_list: 层余弦相似度列表
            predictions_gt: 参考预测结果
            fp_layer_list: FP层列表
            global_compare_layers: 全局比较层
            layers_rate: 层权重
            all_pre_layers: 所有前驱层
            all_int8_cos: 所有INT8层的余弦相似度
        
        Returns:
            float: 输出余弦相似度
        """
        outputs_cos = 0
        
        # 初始化网络图日志
        self.dot_log = net_dot_log('mix_prec_result', int8_model.parser, self.logger)
        self.dot_log.add_new_log_region(f'计算余弦相似度并设置{self.mix_mode}层')
        
        # 获取TOP操作名称列表
        top_op_names = self.parser.get_op_name_list()
        
        # 设置最大FP32层数量
        max_fp32_layer_num = len(top_op_names) // 4
        
        # 获取浮点和INT8操作名称列表
        fp_op_names = float_model.parser.get_op_name_list()
        top_ops = {op.name: op for op in self.parser.ops}
        
        # 重新创建INT8模型
        int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table)
        int8_op_names = int8_model.parser.get_op_name_list()
        
        # 获取完整的操作列表
        full_op_list = self.get_full_op_list(float_model, int8_model, fp_op_names, int8_op_names)
        
        try:
            # 使用进度条遍历所有操作
            pbar = tqdm(list(full_op_list.values()))
            for op in pbar:
                pbar.set_description(f"处理 {op.name}")
                
                # 生成INT8张量
                have_int8_next = self.gen_int8_tensor(op, int8_op_names, int8_model, fp_layer_list)
                
                # 生成浮点张量
                self.gen_fp_tensor(op, fp_op_names, float_model)
                
                # 检查是否需要跳过当前操作
                if self.skip_op(op, top_ops, have_int8_next, fp_layer_list, all_pre_layers):
                    continue
                
                # 计算平均层余弦相似度
                avg_cos = self.cal_avg_layer_cos(op)
                layer_cos_list.append((op.name, avg_cos))
                
                # 如果余弦相似度低于阈值，则将当前层和下一层设置为混合精度
                if avg_cos < self.args.min_layer_cos:
                    next_top_ops = self.parser.get_next_op_by_op_name(op.name)
                    
                    # 检查输入是否有效
                    # have_input = self.check_input(op, next_top_ops, fp_layer_list)
                    # if not have_input:
                    #     continue
                    
                    # 设置当前层和下一层为混合精度
                    outputs_cos, mix_model = self.set_curr_layer_and_the_next_layer_mix(
                        op, next_top_ops, fp_layer_list, global_compare_layers, layers_rate, predictions_gt)
                    
                    self.dot_log.add_node_label(op.name, f'当前输出余弦相似度:{outputs_cos:.6f}')
                    
                    # 清理混合模型
                    mix_model.clean()
                    
                    # 如果输出余弦相似度高于预期值，则成功
                    if outputs_cos > self.args.expected_cos:
                        self.dot_log.add_node_label(op.name,
                                                    f'任务成功，当前余弦相似度高于预期值:{self.args.expected_cos}')
                        break
                    
                    # 如果FP层数量超过最大值，则失败
                    if len(fp_layer_list) > max_fp32_layer_num:
                        self.dot_log.add_node_label(op.name,
                                                    f'任务失败，{self.mix_mode}层数量超过最大值')
                        break
                    
                    # 如果输出余弦相似度低于所有INT8层的余弦相似度，则撤销当前设置
                    if outputs_cos < all_int8_cos:
                        fp_layer_list.remove(op.name)
                        for i in next_top_ops:
                            fp_layer_list.remove(i)
                        self.dot_log.add_node_label(op.name,
                                                    f'遇到输出余弦相似度:{outputs_cos:.6f} < 所有INT8层余弦相似度:{all_int8_cos:.6f}')
                        
                        # 从INT8激活值字典中移除下一个TOP操作
                        for idx in range(self.num_sample):
                            for next_top_op in next_top_ops:
                                self.int8_activations[idx].pop(next_top_op)
                
                # 清理参考张量
                self.clear_ref_tensor(op.name, self.parser, self.ref_activations)
                self.clear_ref_tensor2(op.name, self.parser, int8_model.parser, self.int8_activations)
        
        except Exception as err:
            self.logger.print_info(f'发生异常: {str(err)}')
            pass
        
        # 生成网络图
        self.dot_log.gen_dot_graph()
        
        # 清理模型
        int8_model.clean()
        float_model.clean()
        
        # 启用打印输出
        self.enable_print()
        
        return outputs_cos
    
    def print_log_info(self, layer_cos_list, fp_layer_list, all_int8_cos, outputs_cos, t0):
        """
        打印日志信息
        
        Args:
            layer_cos_list: 层余弦相似度列表
            fp_layer_list: FP层列表
            all_int8_cos: 所有INT8层的余弦相似度
            outputs_cos: 输出余弦相似度
            t0: 开始时间
        """
        self.logger.print_info('>>>运行结果:')
        
        # 按余弦相似度排序
        layer_cos_list = sorted(layer_cos_list, key=lambda x: x[1], reverse=False)
        
        # 写入损失表
        with open(self.loss_table, "w") as f:
            f.write(f"# 生成时间: {datetime.datetime.now()}\n")
            f.write(f"# 样本数量: {self.num_sample}\n")
            f.write(f"# 芯片: {self.chip}  混合模式: {self.mix_mode}\n")
            f.write("###\n")
            
            for idx, layer in enumerate(layer_cos_list):
                loss_msg = f"No.{idx:<4}: 层: {layer[0]:<50}\t\t\t余弦相似度: {layer[1]:.6f}"
                f.write(f"{loss_msg}\n")
                self.logger.print_info(loss_msg)
        
        # 写入量化表
        with open(self.quantize_table, "w") as f:
            f.write(f"# 生成时间: {datetime.datetime.now()}\n")
            f.write(f"# 样本数量: {self.num_sample}\n")
            f.write(f"# 芯片: {self.chip}  混合模式: {self.mix_mode}\n")
            f.write(f"# {self.mix_mode}层数量: {len(fp_layer_list)}\n")
            f.write("###\n")
            f.write("# 操作名称   量化模式\n")
            
            for layer in fp_layer_list:
                f.write(f"{layer} {self.mix_mode}\n")
        
        # 打印结果摘要
        self.logger.print_info(f'INT8输出余弦相似度:{all_int8_cos:.6f} (原始)')
        self.logger.print_info(f"混合模型输出余弦相似度:{outputs_cos:.6f}")
        self.logger.print_info(f"混合量化表输出到 {self.quantize_table}")
        self.logger.print_info(f"总时间:{time.time() - t0:.2f}秒")
    
    def run(self):
        """
        运行混合精度搜索
        """
        t0 = time.time()
        layer_cos_list, predictions_gt, fp_layer_list = [], [], []
        
        # 创建张量差异可视化目录
        os.system('rm -rf tensor_diff_fp32_vs_int8;mkdir -p tensor_diff_fp32_vs_int8/')
        
        # 提取全局比较层
        global_compare_layers, layers_rate, all_pre_layers = self.extract_global_layers()
        
        # 创建浮点模型并运行以获取参考结果
        float_model = MixQuantModel(self.fp32_mlir, self.chip)
        _ = self.run_model(float_model, True, global_compare_layers, layers_rate, predictions_gt)
        
        # 创建INT8模型并运行
        int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table)
        outputs_cos = self.run_model(int8_model, False, global_compare_layers, layers_rate, predictions_gt)
        
        # 如果INT8模型已经满足预期，则不需要混合精度
        if outputs_cos > self.args.expected_cos:
            float_model.clean()
            int8_model.clean()
            self.enable_print()
            self.logger.print_info(
                f'任务成功，当前INT8余弦相似度:{outputs_cos} 高于预期值:{self.args.expected_cos}，不需要混合精度')
            exit(0)
        
        # 保存所有INT8层的余弦相似度
        all_int8_cos = outputs_cos
        
        # 搜索混合精度层
        outputs_cos = self.search_mix_layer(float_model, int8_model, layer_cos_list, predictions_gt, fp_layer_list,
                                         global_compare_layers, layers_rate, all_pre_layers, all_int8_cos)
        
        # 打印日志信息
        self.print_log_info(layer_cos_list, fp_layer_list, all_int8_cos, outputs_cos, t0)
    
    def run_bias_correction(self):
        """
        运行偏置校正
        """
        self.logger.print_info("开始偏置校正")
        t0 = time.time()
        layer_cos_list = list()
        predictions_gt = list()
        
        # 获取权重文件名
        weight_file_name = MlirParser(self.fp32_mlir).module_weight_file
        
        # 创建张量差异可视化目录
        os.system('rm -rf tensor_diff_fp32_vs_int8;mkdir -p tensor_diff_fp32_vs_int8/')
        
        # 设置所有层为浮点模式
        self.logger.print_info(f"运行浮点模式: {self.fp32_mlir}")
        self.disable_print()
        
        # 创建浮点模型
        float_model = MixQuantModel(self.fp32_mlir, self.chip)
        
        # 提取全局比较层
        global_compare_layers = None
        layers_rate = None
        all_pre_layers = None
        
        if self.args.global_compare_layers != '':
            global_compare_layers = [i.strip() for i in self.args.global_compare_layers.split(',')]
            layers_rate = [float(i.split(':')[-1].strip()) for i in global_compare_layers if ':' in i]
            global_compare_layers = [i.split(':')[0].strip() for i in global_compare_layers]
            
            all_pre_layers = []
            for layer in global_compare_layers:
                pre_layers = []
                find_all_pre_layers(pre_layers, layer, self.parser)
                all_pre_layers.extend(pre_layers)
            all_pre_layers = set(all_pre_layers)
            
            if len(layers_rate) > 0:
                if len(global_compare_layers) != len(layers_rate):
                    print('global_compare_layers 格式错误')
                    exit(1)
                if 1 != sum(layers_rate):
                    print('global_compare_layers 权重错误')
                    exit(1)
            else:
                layers_rate = len(global_compare_layers) * [1]
        
        # 运行浮点模型以获取参考结果
        for idx in range(self.num_sample):
            data = []
            for name in list(self.ref_activations[idx].keys()):
                data.append(self.ref_activations[idx][name][0])
            outputs = float_model.infer(data, global_compare_layers)
            predictions_gt.append(outputs)
        
        # 设置所有层为INT8模式
        self.logger.print_info(f"运行INT8模式: {self.fp32_mlir}")
        outputs_cos = 0
        
        # 创建INT8模型
        int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table)
        
        # 运行INT8模型
        for idx in range(self.num_sample):
            data = []
            for name in list(self.ref_activations[idx].keys()):
                data.append(self.ref_activations[idx][name][0])
            outputs = int8_model.infer(data, global_compare_layers)
            outputs_cos += self._loss(outputs, predictions_gt[idx], layers_rate)
        
        # 计算平均余弦相似度
        outputs_cos = outputs_cos / self.num_sample
        all_int8_cos = outputs_cos
        
        # 如果INT8模型已经满足预期，则不需要偏置校正
        if outputs_cos > self.args.expected_cos:
            float_model.clean()
            int8_model.clean()
            self.enable_print()
            self.logger.print_info(
                f'任务成功，当前INT8余弦相似度:{outputs_cos:.6f} 高于预期值:{self.args.expected_cos:.6f}，不需要混合精度')
            exit(0)
        
        # 初始化变量
        max_outputs_cos = all_int8_cos
        max_layer_cos, ret = None, None
        
        # 清空FP层列表和层余弦相似度列表
        fp_layer_list = []
        layer_cos_list.clear()
        
        # 初始化网络图日志
        self.dot_log = net_dot_log('bias_correction', int8_model.parser, self.logger)
        self.dot_log.add_new_log_region('计算余弦相似度并运行偏置校正')
        
        # 获取浮点和INT8操作名称列表
        fp_op_names = float_model.parser.get_op_name_list()
        top_ops = {op.name: op for op in self.parser.ops}
        
        # 重新创建INT8模型
        int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table)
        int8_op_names = int8_model.parser.get_op_name_list()
        
        # 获取完整的操作列表
        full_op_list, cur_fp_op_idx = {}, 0
        for int8_op in int8_op_names:
            pos = fp_op_names.index(int8_op) if int8_op in fp_op_names else -1
            if -1 == pos:
                full_op_list[int8_op] = int8_model.parser.get_op_by_op_name(int8_op)
            else:
                for i in range(cur_fp_op_idx, pos + 1):
                    full_op_list[fp_op_names[i]] = float_model.parser.get_op_by_op_name(fp_op_names[i])
                cur_fp_op_idx = pos + 1
        
        print('完整操作列表:', list(full_op_list.keys()))
        
        # 使用进度条遍历所有操作
        pbar = tqdm(list(full_op_list.values()))
        for op in pbar:
            pbar.set_description(f"处理 {op.name}")
            
            have_int8_next = False
            
            if op.name in int8_op_names:
                # 检查是否有INT8后续操作
                for next_int8_op in int8_model.parser.get_next_op_by_op_name(op.name):
                    if next_int8_op not in fp_layer_list:
                        have_int8_next = True
                        break
                
                if have_int8_next:
                    # 为每个样本生成INT8张量
                    for idx in range(self.num_sample):
                        ret = self.gen_ref_tensor(idx, op.name, self.int8_activations, int8_model, True)
                    if ret:
                        self.dot_log.add_node_label(op.name, '生成INT8张量')
                else:
                    self.dot_log.add_node_label(op.name, f'后续层是{self.mix_mode}，不需要生成INT8张量')
            
            # 生成浮点张量
            if op.name in fp_op_names:
                for idx in range(self.num_sample):
                    ret = self.gen_ref_tensor(idx, op.name, self.ref_activations, float_model)
                if ret:
                    self.dot_log.add_node_label(op.name, f'生成{self.mix_mode}张量')
            
            # 检查是否需要跳过当前操作
            if op.name not in top_ops:
                self.dot_log.add_node_label(op.name, '操作不在TOP dialect中')
                continue
            
            if op.type in SKIP_OPERATION:
                pre_layers = self.parser.get_pre_op_by_op_name(op.name)
                for pre_layer in pre_layers:
                    if pre_layer in fp_layer_list:
                        self.dot_log.add_node_label(op.name, f'将{op.name}添加到FP层列表')
                        fp_layer_list.append(op.name)
                        break
                self.dot_log.add_node_label(op.name, f'遇到量化跳过操作，类型:{op.type}，继续')
                continue
            
            if op.name in fp_layer_list:
                self.dot_log.add_node_label(op.name, f'操作是{self.mix_mode}层，继续')
                continue
            
            if not have_int8_next:
                continue
            
            if op.name not in top_ops:
                self.dot_log.add_node_label(op.name, '操作仅在INT8模型中')
                continue
            
            if all_pre_layers is not None and op.name not in all_pre_layers:
                self.dot_log.add_node_label(op.name, '不在比较层的所有前驱层中，继续')
                continue
            
            # 获取TOP操作
            top_op = top_ops[op.name]
            layer_cos, int8_mean, fp32_mean = 0, 0, 0
            
            # 计算层余弦相似度和均值
            for idx in range(self.num_sample):
                int8_out = self.get_input_int8_tensor(idx, top_op.name, True)
                fp32_out = self.get_input_fp32_tensor(idx, top_op.name)
                
                int8_mean += np.mean(int8_out, axis=(0, 2, 3))
                fp32_mean += np.mean(fp32_out, axis=(0, 2, 3))
                
                layer_cos += cos_sim(int8_out, fp32_out)
            
            # 计算原始层余弦相似度
            old_layer_cos = layer_cos / self.num_sample
            print(f'原始层余弦相似度:{old_layer_cos:.6f}')
            
            # 如果余弦相似度低于阈值且是卷积层，则进行偏置校正
            if old_layer_cos < self.args.min_layer_cos and top_op.type == 'top.Conv' and len(top_op.opds) > 2:
                weight_file_dict = {}
                weight_file = np.load(weight_file_name)
                os.system(f'cp -f {weight_file_name} {weight_file_name}_tmpbk')
                
                # 加载权重文件
                for k in weight_file:
                    weight_file_dict[k] = weight_file[k]
                
                # 计算均值差异
                mean_diff = (fp32_mean - int8_mean) / self.num_sample
                # print(f'操作:{top_op.name}, 均值差异:', mean_diff[:32])
                
                # 调整偏置
                bias_float = weight_file_dict[top_op.opds[2]]
                bias_float += mean_diff
                weight_file_dict[top_op.opds[2]] = bias_float
                
                # 保存旧权重文件并保存新权重文件
                os.system(f'rm -f {weight_file_name}')
                np.savez(weight_file_name, **weight_file_dict)
                
                # 创建混合偏置模型
                mix_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table)
                
                # 获取额外输入张量
                extra_input = self.get_extra_input_tensor(top_op.name, int8_model.parser)
                
                layer_cos, outputs_cos = 0, 0
                
                # 重新运行模型并计算新的余弦相似度
                for idx in range(self.num_sample):
                    input_data_dict, extra_input_data_dict = self.collect_op_input_tensor(
                        idx, top_op.name, extra_input, fp_layer_list)
                    
                    outputs = mix_model.infer_from(top_op.name, input_data_dict, extra_input_data_dict,
                                                   global_compare_layers)
                    
                    mix_layer_out = mix_model.module.get_fp32_tensor(top_op.name)
                    fp32_out = self.get_input_fp32_tensor(idx, top_op.name)
                    
                    layer_cos += cos_sim(mix_layer_out, fp32_out)
                    outputs_cos += self._loss(outputs, predictions_gt[idx], layers_rate)
                
                # 计算平均余弦相似度
                layer_cos = layer_cos / self.num_sample
                outputs_cos = outputs_cos / self.num_sample
                
                # 如果偏置校正后性能下降，则撤销更改
                if outputs_cos < max_outputs_cos or (max_layer_cos is not None and layer_cos < max_layer_cos):
                    os.system(f'cp -f {weight_file_name}_tmpbk {weight_file_name}')
                    print(
                        f'操作:{top_op.name} 偏置校正撤销被撤销，层余弦相似度:{layer_cos:.6f}，输出余弦相似度:{outputs_cos:.6f}')
                else:
                    print(
                        f'操作:{top_op.name} 偏置校正已应用，层余弦相似度:{layer_cos:.6f}，输出余弦相似度:{outputs_cos:.6f}')
                
                # 记录偏置校正结果
                layer_cos_list.append((top_op.name, old_layer_cos, layer_cos, outputs_cos))
                
                # 更新最大输出余弦相似度和最大层余弦相似度
                max_outputs_cos = outputs_cos if outputs_cos > max_outputs_cos else max_outputs_cos
                max_layer_cos = layer_cos if max_layer_cos is None or layer_cos > max_layer_cos else max_layer_cos
                
                # 如果当前性能已经满足预期，则提前退出
                if max_outputs_cos > self.args.expected_cos:
                    print(f'任务成功，当前余弦相似度高于预期值:{self.args.expected_cos:.6f}')
                    break
            
            # 清理参考张量
            self.clear_ref_tensor(top_op.name, self.parser, self.ref_activations)
            self.clear_ref_tensor(top_op.name, int8_model.parser, self.int8_activations)
        
        # 生成网络图
        self.dot_log.gen_dot_graph()
        
        # 清理模型
        int8_model.clean()
        float_model.clean()
        
        # 启用打印输出
        self.enable_print()
        
        # 打印结果摘要
        self.logger.print_info('>>>运行结果:')
        self.logger.print_info(f'INT8输出余弦相似度:{all_int8_cos:.6f} (原始)')
        self.logger.print_info('偏置校正统计:')
        
        for item in layer_cos_list:
            self.logger.print_info(
                f'  操作:{item[0]}, 原始层余弦相似度:{item[1]:.6f}, 新层余弦相似度:{item[2]:.6f}, 新输出余弦相似度:{item[3]:.6f}')
        
        self.logger.print_info(f'最佳混合模型输出余弦相似度:{max_outputs_cos:.6f}')
        self.logger.print_info(f"总时间:{time.time() - t0:.2f}秒")
    
    def find_successor(self, op):
        """
        查找操作的后继操作
        
        Args:
            op: 操作名称
        
        Returns:
            list: 后继操作列表
        """
        # 支持 Conv -> Relu -> Conv 或 Conv -> Conv 模式
        result = []
        nxt_op = self.parser.get_next_op_by_op_name(op)
        
        for node in nxt_op:
            if self.parser.get_op_type_by_op_name(node) in ['top.Relu', 'top.PRelu', 'top.LeakyRelu']:
                nxt_nxt_op = self.parser.get_next_op_by_op_name(node)
                for _node in nxt_nxt_op:
                    if self.parser.get_op_type_by_op_name(_node) == 'top.Conv':
                        result.append(_node)
                    else:
                        return []
            elif self.parser.get_op_type_by_op_name(node) == 'top.Conv':
                result.append(node)
            else:
                return []
        
        return result
    
    def converged(self, cur_weight, prev_weight, threshold=1e-4):
        """
        检查权重是否收敛
        
        Args:
            cur_weight: 当前权重
            prev_weight: 先前权重
            threshold: 收敛阈值
        
        Returns:
            bool: 是否收敛
        """
        norm_sum = 0
        norm_sum += np.linalg.norm(cur_weight[0] - prev_weight[0])
        norm_sum += np.linalg.norm(cur_weight[1] - prev_weight[1])
        
        self.logger.print_info(f'>>>损失:{norm_sum}')
        
        return norm_sum < threshold
    
    def weight_equalization(self):
        """
        执行权重均衡
        """
        # 获取权重文件名
        weight_file_name = MlirParser(self.fp32_mlir).module_weight_file
        
        # 加载MLIR模块
        module = pymlir.module()
        module.load(self.fp32_mlir)
        
        # 创建张量差异可视化目录
        os.system('rm -rf tensor_diff_fp32_vs_int8;mkdir -p tensor_diff_fp32_vs_int8/')
        
        # 获取所有操作
        all_ops = self.parser.ops
        
        for op in all_ops:
            # 只处理卷积层
            if self.parser.get_op_type_by_op_name(op.name) == 'top.Conv':
                # 查找后继操作
                succ = self.find_successor(op.name)
                
                if len(succ) != 1:
                    continue
                
                iter = 1
                weight_file_dict = {}
                
                while True:
                    # 加载权重文件
                    weight_file = np.load(weight_file_name)
                    for k in weight_file:
                        weight_file_dict[k] = weight_file[k]
                    
                    # 获取第一个卷积层的权重
                    weight_first = weight_file[op.opds[1]]
                    weight_first_shape = module.get_tensor(op.opds[1]).shape
                    
                    if weight_first.shape != weight_first_shape:
                        weight_first = weight_first.reshape(weight_first_shape)
                    
                    new_weight_first = copy.deepcopy(weight_first)
                    
                    # 获取第一个卷积层的偏置（如果有）
                    if len(op.opds) > 2:
                        bias_first = weight_file[op.opds[2]]
                        new_bias_first = copy.deepcopy(bias_first)
                    
                    # 获取第二个卷积层的名称和权重
                    next_op_name = succ[0]
                    next_op = self.parser.get_op_by_op_name(next_op_name)
                    
                    weight_second = weight_file[next_op.opds[1]]
                    weight_second_shape = module.get_tensor(next_op.opds[1]).shape
                    
                    if weight_second.shape != weight_second_shape:
                        weight_second = weight_second.reshape(weight_second_shape)
                    
                    new_weight_second = copy.deepcopy(weight_second)
                    
                    # 计算组数
                    num_group = weight_first.shape[0] // weight_second.shape[1]
                    self.logger.print_info(f'跨层权重均衡: {op.name} --- {next_op.name} 组数: {num_group} 迭代: {iter}')
                    
                    # 计算每组的通道数
                    group_channels_i = weight_first.shape[0] // num_group
                    group_channels_o = weight_second.shape[0] // num_group
                    
                    # 对每组进行权重均衡
                    for g in range(num_group):
                        c_start_i = g * group_channels_i
                        c_end_i = (g + 1) * group_channels_i
                        weight_first_group = weight_first[c_start_i:c_end_i]
                        
                        c_start_o = g * group_channels_o
                        c_end_o = (g + 1) * group_channels_o
                        weight_second_group = weight_second[c_start_o:c_end_o]
                        
                        # 对每个通道进行权重均衡
                        for ii in range(weight_second_group.shape[1]):
                            range_1 = np.abs(weight_first_group)[ii].max()
                            range_2 = np.abs(weight_second_group)[:, ii].max()
                            
                            if range_1 < 1e-6:
                                range_1 = 0.
                            if range_2 < 1e-6:
                                range_2 = 0.
                            
                            # 计算缩放因子
                            s = range_1 / np.sqrt(range_1 * range_2)
                            
                            if np.isinf(s) or np.isnan(s):
                                s = 1.0
                            
                            # 应用缩放因子
                            new_weight_first[c_start_i + ii] /= s
                            new_weight_second[c_start_o:c_end_o, ii] *= s
                            
                            # 如果有偏置，也应用缩放因子
                            if len(op.opds) > 2:
                                new_bias_first[c_start_i + ii] /= s
                    
                    # 检查是否收敛
                    if self.converged([weight_first, weight_second], [new_weight_first, new_weight_second]):
                        break
                    
                    iter += 1
                    
                    # 更新权重
                    weight_file_dict[op.opds[1]] = new_weight_first
                    weight_file_dict[next_op.opds[1]] = new_weight_second
                    
                    # 如果有偏置，也更新偏置
                    if len(op.opds) > 2:
                        weight_file_dict[op.opds[2]] = new_bias_first
                    
                    # 保存新权重文件
                    os.system(f'rm -f {weight_file_name}')
                    np.savez(weight_file_name, **weight_file_dict)
                    
                    # 清空权重文件字典
                    weight_file_dict.clear()
```

## search\_qtable\.py

这段代码主要实现了一个用于搜索量化表（Qtable）的功能，旨在通过不同方法生成阈值、分析敏感层，以确定模型中哪些层适合量化，哪些层应保持浮点精度，从而优化模型的量化效果。以下是对代码逻辑的详细分析：

1. 类定义和初始化

`SearchQtable` 类封装了搜索量化表的相关操作。

- **初始化函数 ****`init`**：

    - 接收命令行参数 `args`、数据选择器 `selector` 和调优数据集 `tune_ds`。

    - 初始化一系列属性，包括 MLIR 文件路径、芯片类型、校准表名称等。

    - 加载 MLIR 模块并创建 `MlirParser` 实例。

    - 初始化 `MixPrecSearcher` 实例，并配置日志和网络可视化相关设置。

    - 解析量化方法列表和调试命令。

2. 生成多阈值

**`gen_multiple_thresholds`**** 方法**：

- 针对不同的量化方法（如 `MAX`、`Percentile9999`、`MSE`），为每一层生成阈值。

- 对于每种方法：

    - 创建 `ActivationCalibrator` 实例，并根据方法设置调试命令。

    - 收集激活数据并计算阈值。

    - 将阈值写入校准表文件。

    - 如果是调优模式，使用 `SimpleTuner` 进一步调整阈值。

- 最终返回一个字典，包含每种量化方法对应的层阈值字典。

3. 检查层名称

**`check_layer_names`**** 方法**：

- 从所有操作名称中筛选出有效的层名称，排除特定类型（如 `Coeff`、`Accuracy`）和不存在的层。

- 检查筛选后的层名称是否在阈值字典中存在，若不存在则报错退出。

- 返回有效的层名称列表。

4. 搜索无需量化的层类型

**`search_layer_type_no_need_quant`**** 方法**：

- 遍历所有层类型，对于每种类型，构建一个混合量化表，将该类型的层设置为浮点，其他层为量化。

- 运行混合量化模型并计算相似度。

- 如果相似度低于某个阈值，则认为该层类型是敏感的，将其添加到敏感层类型列表中。

- 返回敏感层类型列表。

5. 搜索敏感层

**`search_sensitve_layer`**** 方法**：

- 对于每一层，尝试不同的量化方法和阈值，评估量化后的模型与浮点模型的输出相似度（如余弦相似度）。

- 使用 `MixQuantModel` 运行模型并获取输出相似度。

- 记录每层在不同量化方法下的最佳相似度、最佳阈值和最佳量化方法。

- 最终返回敏感层分析字典、新的校准表名称和损失字典。

6. 搜索 INT4 敏感层

**`search_sensitve_layer_int4`**** 方法**：

- 针对 INT4 量化模式，对指定类型（如 `top.Conv`、`top.MatMul`）的层进行敏感度分析。

- 构建 INT4 和 INT8 混合量化表，运行模型并记录每层的输出相似度和信噪比。

- 返回损失字典。

7. 快速搜索敏感层

**`search_sensitve_layer_fast`**** 方法**：

- 基于已有的敏感层列表，快速搜索其他敏感层。

- 运行模型并获取每层的输出相似度和信噪比，根据排名选择前 5 层作为新的敏感层。

- 更新敏感层列表并重新评估模型，直到满足停止条件。

- 返回损失字典。

8. 分析敏感层

**`analysis_sensitive_layers`**** 方法**：

- 根据敏感层分析字典，按照损失值排序。

- 根据 `max_float_layers` 参数，选择一定数量的敏感层作为浮点层。

- 考虑卷积层和其相邻的 Scale 层，一起添加到浮点层列表中。

- 返回浮点层列表。

9. 聚类分析

- **`cluster`**** 方法**：使用 KMeans 聚类算法对损失字典中的数据进行聚类，选择聚类中心最大的簇对应的层作为选定层。

- **`auto_select_clusters`**** 方法**：通过轮廓系数自动选择最佳聚类数。

- **`find_best_eps`**** 方法**：自动选择 DBSCAN 聚类算法中的最佳 `eps` 参数。

- **`cluster_4_8`**** 方法**：使用 DBSCAN 算法对 INT4 和 INT8 混合量化的损失数据进行聚类，并根据聚类中心排序返回聚类结果。

10. 移除文件中的行

**`remove_lines_from_file`**** 方法**：从指定文件中删除与给定内容匹配的行。

11. 调整量化表

**`adjust_qtable`**** 方法**：根据当前模型的输出相似度和最大浮点层数量限制，调整浮点层列表，以达到期望的输出相似度。

12. 打印日志信息

- **`print_log_info`**** 方法**：打印混合量化模型的运行结果，包括每层的余弦相似度、浮点层列表、INT8 模型和混合模型的输出余弦相似度等，并将浮点层列表写入量化表文件。

- **`print_log_info_4_8`**** 方法**：类似 `print_log_info`，但针对 INT4 和 INT8 混合量化模式。

13. 主运行方法

- **`run`**** 方法**：

    - 生成不同量化方法的阈值字典。

    - 运行浮点模型和 INT8 模型，获取输出相似度。如果 INT8 模型输出相似度高于期望，则无需混合量化，程序退出。

    - 检查层名称，搜索无需量化的层类型，筛选出敏感层。

    - 搜索敏感层并分析，生成最终的混合量化模型并打印相关信息。

- **`run_4_8`**** 方法**：专门针对 INT4 和 INT8 混合量化模式的运行方法，包括分析敏感层、聚类、调整量化表并打印结果。

- **`run_fast`**** 方法**：快速搜索敏感层的运行方法，通过迭代选择敏感层，直到满足停止条件，并打印结果。

## gen\_data\_list\.py

该代码是一个**数据集随机采样工具**，功能是从指定目录（包括子目录）中递归收集所有文件路径，随机打乱后选取指定数量的文件路径，写入输出文件中，生成一份随机的数据集列表（常用于机器学习中划分训练 / 验证数据）。

1. 依赖于参数解析

- 核心依赖：`argparse`用于解析命令行参数，`pathlib.Path`用于便捷的文件路径操作，`random`用于随机打乱文件列表。

- 参数定义：通过`argparse`定义 3 个必选参数：

    - `dataset`：数据集根目录（需要从中收集文件的文件夹）；

    - `num`：需要选取的文件数量；

    - `output`：输出文件路径（用于保存选取的文件路径列表）。

2. 核心流程

##### 步骤 1：收集所有文件路径

```Plain Text
for file_path in Path(args.dataset).glob('**/*'):
    if file_path.is_file():
        full_list.append(str(file_path))
```

- 功能：递归遍历`dataset`目录下的所有文件（包括子目录中的文件）。

    - `Path(args.dataset).glob('**/*')`：`**`表示递归匹配所有子目录，`*`匹配所有文件名，因此会遍历目录下所有层级的文件和文件夹；

    - `file_path.is_file()`：过滤出文件（排除文件夹），确保列表中只包含文件路径；

    - `str(file_path)`：将`Path`对象转换为字符串路径，便于后续写入文件。

##### 步骤 2：随机打乱文件列表

```Plain Text
random.shuffle(full_list)
```

- 功能：原地打乱`full_list`中的文件路径顺序，确保选取的文件具有随机性（避免因文件系统默认排序导致的采样偏差，比如按名称排序可能集中选取某一类文件）。

##### 步骤 3：确定采样数量并写入输出文件

```Python
num = int(args.num) if int(args.num) > 0 else len(full_list)
with open(args.output, 'w') as fp:
    for i in range(num):
        fp.write(full_list[i] + "\n")
```

- 采样数量处理：若`num`为正数，则选取`num`个文件；若`num`≤0，则选取所有文件（适用于需要全部数据的场景）；

- 写入文件：将前`num`个随机打乱的文件路径写入`output`文件，每个路径占一行（生成的列表可直接用于后续读取数据，如训练时按列表加载文件）。

## shape\_ops\.py

这段代码是一个针对 MLIR（一种中间表示格式）文件中与 "形状操作"（shape operations）相关的处理工具，主要功能是识别 MLIR 模块中所有参与形状计算的操作，并生成一个量化配置表，指定这些操作的量化模式。以下是结合代码的详细解释：

### 整体背景

代码来自 TPU\-MLIR 工具链（Sophgo Technologies 针对 TPU 优化的工具），用于处理模型量化过程中与 "形状计算" 相关的操作。在模型部署中，形状计算（如获取张量形状、重塑张量等）通常需要保持较高精度，该工具通过识别这些操作并生成量化配置，确保其量化模式符合硬件要求。

### 核心类：`ShapeOps`

该类封装了与形状操作识别和量化表生成相关的逻辑，主要包含初始化方法、量化表生成方法、形状操作扩展方法和主执行方法。

1. 初始化方法 `init`

```Python
def __init__(self, args):
    self.args = args
    self.fp32_mlir = args.mlir_file  # MLIR文件路径
    self.chip = args.chip  # 目标芯片型号
    self.module = pymlir.module()  # 加载MLIR模块（pymlir是MLIR的Python绑定）
    self.module.load(args.mlir_file)
    self.parser = MlirParser(args.mlir_file)  # 自定义MLIR解析器，用于解析操作依赖、类型等
    self.quantize_table = self.parser.module_name+"_shape_ops"  # 量化表文件名
    # 设置混合精度模式（根据芯片支持的类型或用户输入）
    if args.fp_type == 'auto':
        self.mix_mode = FLOAT_MAP[self.chip]
    else:
        self.mix_mode = args.fp_type
        if args.fp_type not in chip_support_mix_fp_type[self.chip]:
            print('参数错误，fp_type不被芯片支持')
            exit(1)
```

- 作用：初始化工具所需的参数（如 MLIR 文件、芯片型号），加载 MLIR 模块，创建 MLIR 解析器，并确定量化模式（混合精度类型）。

- 关键依赖：`pymlir`用于操作 MLIR 模块，`MlirParser`用于解析 MLIR 中的操作信息（如操作类型、依赖关系）。

2. 量化表生成方法 `gen_qtable`

```Python
def gen_qtable(self, fp_layer_list):with open(self.quantize_table, "w") as f:
        f.write("# 生成时间: {}\n".format(datetime.datetime.now()))
        f.write("# 芯片: {}  混合模式: {}\n".format(self.chip, self.mix_mode))
        f.write("# {}类型的层数量: {}\n".format(self.mix_mode, len(fp_layer_list)))
        f.write("###\n")
        f.write("# 操作名   量化模式\n")for layer in fp_layer_list:
            f.write("{} {}\n".format(layer, self.mix_mode))
```

- 作用：根据识别出的形状操作列表，生成一个量化配置表（文本文件）。

- 内容：包含生成时间、芯片型号、量化模式，以及每个形状操作对应的量化模式（用于指导后续量化过程）。

3. 形状操作扩展方法 `find_forward_all_input_shapes`

```Python
def find_forward_all_input_shapes(self, shape_ops):
    found_new_shape_ops = False
    for op in shape_ops:
        next_ops = self.parser.get_next_op_by_op_name(op)  # 获取当前操作的后续操作
        for op_ in next_ops:
            if op_ in shape_ops:
                continue
            # 检查后续操作的所有前置操作是否都是形状操作
            pre_ops = self.parser.get_pre_op_by_op_name(op_)
            all_shape_inputs = True
            for op__ in pre_ops:
                if op__ not in shape_ops:
                    all_shape_inputs = False
                    break
            if all_shape_inputs:
                shape_ops.append(op_)  # 若所有前置都是形状操作，则当前操作也加入形状操作列表
                found_new_shape_ops = True
    return found_new_shape_ops, shape_ops
```

- 作用：从已识别的形状操作出发，向前传播（沿数据流方向）扩展形状操作列表。

- 逻辑：如果一个操作的**所有前置操作都是形状操作**，则该操作也被视为形状操作（因为它的输入完全由形状操作决定，输出也与形状相关）。

4. 主执行方法 `run`

该方法是核心逻辑，用于识别所有与形状计算相关的操作，步骤如下：

##### 步骤 1：定义关键操作类型

```Python
# 形状生成器：直接产生形状信息的操作（如获取张量形状、大小）
shape_generator = ['top.Shape', 'top.Size']
# 形状消费者：使用形状信息的操作（如重塑张量、插值）
shape_consumer = ['top.Reshape', 'top.Interp']
# 透明操作：不改变形状信息传递的操作（如拼接、切片，仅传递形状相关数据）
transparent_ops = ['top.Concat', 'top.Slice', 'top.Tile', 'top.Split', 'top.Pack', 'top.Repeat']

# 辅助判断函数
is_generator = lambda op_name: self.parser.get_op_type_by_op_name(op_name) in shape_generator
is_consumer = lambda op_name: self.parser.get_op_type_by_op_name(op_name) in shape_consumer
is_transparent = lambda op_name: self.parser.get_op_type_by_op_name(op_name) in transparent_ops
is_input = lambda op_name: op_name in [op.name for op in inputs]  # 是否为模型输入
is_output = lambda op_name: op_name in outputs  # 是否为模型输出
```

##### 步骤 2：初始化形状操作列表

```Python
# 初始形状操作列表：所有"形状生成器"类型的操作
shape_ops = [x.name for x in self.parser.ops if is_generator(x.name)]
```

##### 步骤 3：扩展形状操作列表

通过循环不断扩展`shape_ops`，直到没有新操作加入：

```Python
while True:
    no_new_shape_op = True  # 标记是否有新操作加入
    # 1. 向后扩展：处理当前形状操作的后续操作
    for op in shape_ops:
        if is_output(op):
            continue
        next_ops = self.parser.get_next_op_by_op_name(op)  # 后续操作
        for op_ in next_ops:
            if op_ in shape_ops:
                continue
            if is_output(op_):  # 输出操作加入（形状信息可能传递到输出）
                shape_ops.append(op_)
                no_new_shape_op = False
            elif is_consumer(op_):  # 形状消费者不加入（仅使用形状，自身不是形状计算）
                continue
            elif is_transparent(op_):  # 透明操作加入（传递形状信息）
                shape_ops.append(op_)
                no_new_shape_op = False

    # 2. 向前扩展：处理当前形状操作的前置操作
    for op in shape_ops:
        pre_ops = self.parser.get_pre_op_by_op_name(op)  # 前置操作
        for op in pre_ops:
            if op in shape_ops or is_generator(op):
                continue
            if is_transparent(op) or is_input(op):  # 透明操作或输入加入（输入可能是形状的源头）
                no_new_shape_op = False
                shape_ops.append(op)

    # 3. 通过find_forward_all_input_shapes进一步扩展
    new_op, shape_ops = self.find_forward_all_input_shapes(shape_ops)
    if new_op:
        no_new_shape_op = False

    # 若没有新操作加入，退出循环
    if no_new_shape_op:
        break
```

- 逻辑：从初始的 "形状生成器" 出发，通过**向后扩展**（包含传递形状的透明操作、输出）和**向前扩展**（包含输入、上游透明操作），以及`find_forward_all_input_shapes`的补充扩展，最终捕获所有参与形状计算的操作。

##### 步骤 4：生成量化表

```Python
self.gen_qtable(shape_ops)
```

将识别出的所有形状操作写入量化表，指定其量化模式。



## search\_threshold\.py

这段代码是一个用于模型量化过程中**搜索最优量化阈值**的工具，核心功能是通过多种量化阈值计算方法生成校准表，评估不同方法的量化效果，并选择最优结果。以下结合代码细节进行解释：

### 整体背景

在模型从浮点（FP32）量化到整数（如 INT8、INT4）的过程中，需要确定 “量化阈值”（用于将浮点范围映射到整数范围的关键参数）。不同的阈值计算方法（如 KL 散度、最大值、百分位数等）会影响量化精度，该工具通过对比多种方法的量化效果，选择最优阈值方案，生成最终校准表（用于指导后续量化）。

### 核心类：`SearchThreshold`

该类封装了阈值计算、校准表生成、量化效果评估和最优方法选择的逻辑，主要包含初始化方法、多阈值生成方法和主执行方法。

1. 初始化方法 `init`

```Python
def __init__(self, args, selector, tune_ds):
    self.args = args  # 命令行参数（如MLIR文件路径、芯片型号等）
    self.fp32_mlir = args.mlir_file  # 浮点模型的MLIR文件路径
    self.chip = args.chip  # 目标芯片型号
    self.cali_table_name = args.calibration_table  # 校准表文件名
    self.module = pymlir.module()  # 加载MLIR模块（pymlir为MLIR的Python绑定）
    self.module.load(args.mlir_file)
    self.parser = MlirParser(args.mlir_file)  # 自定义MLIR解析器（解析操作、依赖等）
    self.selector = selector  # 数据选择器（用于选择校准数据）
    self.tune_ds = tune_ds  # 调优数据集（用于阈值调优）
    self.benchmark_method = args.benchmark_method  # 量化效果评估方法（如cos相似度、SNR）
    args.loss_table = ""  # 禁用损失表
    if args.input_num > args.inference_num:  # 限制输入数据量
        args.input_num = args.inference_num
    self.mix_prec = MixPrecSearcher(args)  # 混合精度搜索器（用于评估量化效果）
    self.debug_cmd = parse_debug_cmd(args.debug_cmd)  # 解析调试命令
```

- 作用：初始化工具所需的参数、模型资源（MLIR 模块、解析器）、数据相关组件（选择器、调优数据集）和评估组件（混合精度搜索器），为后续阈值计算和评估做准备。

2. 多阈值生成方法 `gen_multiple_thresholds_new`

该方法是核心，用于通过多种量化方法计算阈值，并生成对应的校准表。步骤如下：

##### 步骤 1：收集激活值并计算初始阈值

```Python
# 创建激活校准器，用于收集激活值统计信息并计算阈值
calibrator = ActivationCalibrator(self.args, self.selector, self.tune_ds)
# 并行计算多种阈值（不同方法的阈值、绝对值最大阈值、缩放因子等）
thresholds_map, thresholds_map_absmax, thresholds_map_scale, thresholds_map_zp, _, _, _, _, thresholds, thresholds4 = calibrator.activation_collect_and_calc_th_parallel()
calibrator._clean_resource()  # 清理资源
```

- `ActivationCalibrator`：核心组件，负责从输入数据中收集模型各层的激活值（张量输出），并基于不同方法（如 KL 散度、最大值等）计算量化阈值。

- `activation_collect_and_calc_th_parallel`：并行执行激活值收集和阈值计算，提高效率。

##### 步骤 2：为每种量化方法生成校准表

```Python
# 遍历所有量化方法（KL、MAX、Percentile9999、MSE）
for i, method_name in enumerate(quantize_method_list):
    thresholds_map = thresholds[method_name]  # 当前方法的阈值映射（操作名->阈值）
    op_layers = self.parser.get_op_name_list()  # 所有操作名列表
    # 校准表文件名（包含方法名）
    cali_table = self.args.calibration_table + "_" + method_name
    if self.args.tune_num > 0:
        cali_table += ".1"  # 若有调优，加后缀
    
    # 写入校准表
    with open(cali_table, 'w') as f:
        # 写入元信息（版本、时间、方法等）
        f.write("# mlir version: {}\n".format(pymlir.__version__))
        f.write("# mlir: {}\n".format(self.args.mlir_file))
        f.write("# genetated time: {}\n".format(datetime.datetime.now()))
        f.write("# quantization threshold calculation method: {}\n".format(method_name))
        # ...（其他元信息）
        f.write("# op_name    threshold    min    max\n")  # 表头
        
        # 遍历所有操作，写入阈值、最小值、最大值
        for i, op_name in enumerate(op_layers):
            outputs = self.parser.get_outputs_by_op_name(op_name)  # 操作的输出
            for out in outputs:
                if out not in thresholds_map:
                    continue  # 跳过无用输出
                # 处理FP8调试模式的特殊情况
                if 'fp8' in self.debug_cmd:
                    threshold = thresholds_map[out]
                    min_value, max_value = -threshold*128.0/127.0, threshold  # FP8范围计算
                    f.write("{} {:.7f} {:.7f} {:.7f}\n".format(out, threshold, min_value, max_value))
                else:
                    # 处理常规情况（如INT8）
                    threshold = thresholds_map[out] if out in thresholds_map else 1.0
                    # 获取激活值的最小/最大值（用于量化范围计算）
                    if out in calibrator._activations_statistics:
                        min_value, max_value, *_ = calibrator._activations_statistics[op_name]
                    else:
                        min_value, max_value = -1,1
                    # 处理异常阈值（避免零或NaN）
                    if threshold <= 1e-5 or np.isnan(threshold):
                        threshold = 1e-5
                        self.mix_prec.logger.print_info("WARNING: layer {} threshold is zero...".format(op_name))
                    f.write("{} {:.7f} {:.7f} {:.7f}\n".format(op_name, threshold, min_value, max_value))
        
        # 处理INT4调试模式的阈值
        if 'int4' in self.debug_cmd:
            thresholds_map4 = thresholds4[method_name]  # INT4的阈值映射
            f.write("\n#int4_th\n")
            for i, op_name in enumerate(op_layers):
                outputs = self.parser.get_outputs_by_op_name(op_name)
                for out in outputs:
                    if out not in thresholds_map:
                        continue
                    threshold = thresholds_map4[out]
                    # 处理异常阈值
                    if threshold <= 1e-5 or np.isnan(threshold):
                        threshold = 1e-5
                        self.mix_prec.logger.print_info("WARNING: layer {} threshold is zero...".format(out))
                    min_value, max_value = -threshold*8.0/7.0, threshold  # INT4范围计算
                    f.write("{} {:.7f} {:.7f} {:.7f}\n".format(out, threshold, min_value, max_value))
```

- 校准表内容：包含元信息（如生成时间、量化方法）和具体数据（操作名、阈值、激活值的最小 / 最大值），用于后续量化时将浮点值映射到整数范围。

- 特殊处理：针对 FP8、INT4 的调试模式，调整阈值计算逻辑（因不同精度的整数范围不同）。

##### 步骤 3：阈值调优（针对 KL 方法）

```Python
# 若需要调优且当前方法是KL，进行阈值微调
if calibrator.args.tune_num <= 0 or 'int4' in self.debug_cmd or method_name != 'KL':
    continue

# 创建简单调优器，基于调优数据集优化阈值
tunner = SimpleTuner(calibrator.args, calibrator.tune_ds, calibrator.ppa_list, thresholds_map_absmax)
thresholds_map = tunner.run()  # 得到调优后的阈值

# 生成调优后的校准表
cali_table += "_tune"
with open(cali_table, 'w') as f:
    # 写入元信息（包含调优次数）
    f.write("# tune number: {}\n###\n".format(self.args.tune_num))
    f.write("# op_name    threshold    min    max\n")
    for i, op_name in enumerate(op_layers):
        op_name = split_fuseop(op_name)  # 拆分融合操作名
        threshold = thresholds_map[op_name]
        # 处理异常阈值
        if threshold <= 1e-5:
            threshold = 1e-5
            self.mix_prec.logger.print_info("WARNING: layer {} threshold is zero...".format(op_name))
        min_value, max_value, *_ = calibrator._activations_statistics[op_name]
        f.write("{} {:.7f} {:.7f} {:.7f}\n".format(op_name, threshold, min_value, max_value))
```

- 调优逻辑：对于 KL 方法（常用但可能需要微调），使用`SimpleTuner`基于调优数据集进一步优化阈值，减少量化误差，生成`_tune`后缀的校准表。

3. 主执行方法 `run_search_calitable`

该方法协调阈值生成、量化效果评估和最优方法选择，步骤如下：

##### 步骤 1：初始化模型和量化方法

```Python
float_model = MixQuantModel(self.fp32_mlir, self.chip)  # 浮点模型（基准模型）
predictions_gt = []  # 用于存储基准模型的输出（作为参考）
# 提取全局对比层（用于评估量化误差的关键层）、层速率等
global_compare_layers, layers_rate, _ = self.mix_prec.extract_global_layers()
# 待评估的量化阈值计算方法
quantize_method_list = ["KL","MAX", "Percentile9999","MSE"]
self.mix_prec.logger.print_info("Quantization threshold calculation method: {}".format(quantize_method_list))
# 生成所有方法的校准表
self.gen_multiple_thresholds_new(quantize_method_list)
```

##### 步骤 2：评估各方法的量化效果

```Python
result = {}  # 存储各方法的量化效果分数
# 运行浮点模型，获取基准输出（用于对比）
_ = self.mix_prec.run_model(float_model, True, global_compare_layers, layers_rate, predictions_gt)

# 遍历每种方法，评估量化模型的效果
for method in quantize_method_list:
    # 确定当前方法的校准表路径
    if method == 'KL':
        cali_table = self.cali_table_name + "_" + method + "_tune"  # KL使用调优后的表
    else:
        cali_table = self.cali_table_name + "_" + method + ".1"
    # 复制校准表到临时文件（便于加载）
    new_cali_table_name = "new_cali_table.txt"
    with open(cali_table, 'r') as file:
        data = file.read()
    with open(new_cali_table_name, 'w') as file:
        file.write(data)
    
    # 创建基于当前校准表的量化模型（INT8）
    int8_model = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table_name)
    
    # 评估量化模型与浮点模型的相似度
    if self.benchmark_method == 'cos':
        # 计算余弦相似度（分数越高，效果越好）
        outputs = self.mix_prec.run_model(int8_model, False, global_compare_layers, layers_rate, predictions_gt)
    elif self.benchmark_method == 'snr':
        # 计算信噪比（1 - SNR损失，分数越高，效果越好）
        outputs = 1 - self.mix_prec.run_model_loss_snr(int8_model, False, global_compare_layers, layers_rate, predictions_gt)
    result[method] = outputs  # 存储分数
```

- 评估逻辑：以浮点模型输出为基准，通过余弦相似度（cos）或信噪比（SNR）衡量量化模型（INT8）的输出误差，分数越高表示量化效果越好。

##### 步骤 3：选择最优方法并生成最终校准表

```Python
# 选择分数最高的方法作为最优方法
optimal_method = max(result.items(), key=lambda item: item[1])
optimal_method_name = optimal_method[0]
optimal_value = optimal_method[1]
self.mix_prec.logger.print_info("{} similarity scores: {}".format(self.benchmark_method, result))
self.mix_prec.logger.print_info("optimal_method_name: {}, optimal_value: {}".format(optimal_method_name, optimal_value))

# 复制最优方法的校准表作为最终结果
if optimal_method_name == 'KL':
    cali_table = self.cali_table_name + "_" + optimal_method_name + "_tune"
else:
    cali_table = self.cali_table_name + "_" + optimal_method_name + ".1"
new_cali_table_name = self.cali_table_name  # 最终校准表文件名
with open(cali_table, 'r') as file:
    data = file.read()
with open(new_cali_table_name, 'w') as file:
    file.write(data)
self.mix_prec.logger.print_info("success")
```



## sensitive\_layer\.py

这段代码是 TPU\-MLIR 工具链中用于**敏感层搜索与混合精度量化优化**的核心工具，核心功能是解决全 INT8 量化后模型精度不达标的问题 —— 通过识别对量化敏感的层（量化后精度损失大的层），为其分配更优的量化阈值或保留高精度模式，最终生成平衡精度与性能的混合精度量化表。

### 整体背景

当模型全量 INT8 量化后精度下降过多时，并非所有层对量化的敏感度都相同：部分层（如关键卷积、激活层）量化后损失大（敏感层），部分层（如简单运算层）量化后损失小。该工具通过逐一分析各层的量化敏感性，为敏感层选择最优量化阈值或保留高精度，非敏感层保持 INT8 量化，实现 “混合精度” 优化。

### 核心类：`SensitiveLayer`

类封装了敏感层识别、阈值优化、混合精度表生成的完整逻辑，关键方法按执行流程拆解如下：

1. 初始化方法 `init`

```Python
def __init__(self, args, selector, tune_ds):
    self.args = args  # 命令行参数（模型路径、芯片型号、期望精度等）
    self.fp32_mlir = args.mlir_file  # 浮点模型MLIR文件
    self.chip = args.chip  # 目标TPU芯片
    self.cali_table_name = args.calibration_table  # 初始校准表路径
    self.cali_table = CalibrationTable(self.cali_table_name)  # 校准表操作实例
    self.module = pymlir.module()  # 加载MLIR模块
    self.module.load(args.mlir_file)
    self.parser = MlirParser(args.mlir_file)  # MLIR解析器（解析层名、类型、依赖）
    self.selector = selector  # 校准数据选择器
    self.tune_ds = tune_ds  # 调优数据集
    args.input_num = args.inference_num  # 统一输入数据量
    args.loss_table = ""  # 禁用损失表
    self.mix_prec = MixPrecSearcher(args)  # 混合精度搜索器（评估量化效果）
    # 生成敏感层搜索结果的可视化日志（dot图）
    self.mix_prec.dot_log = net_dot_log('sensitive_layer_search_result', self.parser, self.mix_prec.logger)
```

- 核心作用：初始化模型资源、数据组件、评估工具，为后续敏感层搜索和阈值优化做准备。

2. 多方法阈值生成 `gen_multiple_thresholds`

为 3 种量化方法（MAX、Percentile9999、KL）计算各层阈值，生成阈值字典（用于后续敏感层测试）。

```Python
def gen_multiple_thresholds(self, all_op_names, quantize_method_list):
    layer_th_dicts = {}  # 存储格式：{方法名: {层名: [最大绝对值, 量化阈值]}}
    for method_name in quantize_method_list:
        tmp_th_dict = {}
        # 初始化激活校准器，为不同方法设置调试标记
        calibrator = ActivationCalibrator(self.args, self.selector, self.tune_ds)
        if method_name == "MAX":
            calibrator.debug_cmd = 'use_max'
        elif method_name == "Percentile9999":
            calibrator.debug_cmd = 'use_percentile9999'
        
        # 收集激活值并计算阈值
        thresholds_map, thresholds_map_absmax, _, _, _, _, _, _ = calibrator.activation_collect_and_calc_th()
        calibrator._clean_resource()
        
        # 生成当前方法的校准表（含阈值、激活值范围）
        cali_table = self.args.calibration_table + "_" + method_name
        # ...（写入校准表逻辑，同之前工具，包含层名、阈值、min/max）
        
        # 对KL方法进行阈值调优（提升精度）
        if self.args.tune_num > 0 and method_name == "KL":
            tunner = SimpleTuner(...)
            thresholds_map = tunner.run()  # 调优后的阈值
        
        # 整理当前方法的阈值字典（层名→[最大绝对值, 调优后阈值]）
        for op_name in all_op_names:
            if op_name in thresholds_map:
                tmp_th_dict[op_name] = [thresholds_map_absmax[op_name], thresholds_map[op_name]]
        layer_th_dicts[method_name] = tmp_th_dict
    return layer_th_dicts
```

- 核心输出：`layer_th_dicts`，包含每种量化方法下所有层的 “最大激活值” 和 “最优阈值”，为敏感层测试提供阈值备选。

3. 层名筛选与校验 `check_layer_names`

筛选有效层并校验层名一致性，避免无效层（如 Coeff、Accuracy）或层名不匹配导致后续报错

```Python
def check_layer_names(self, all_op_names, int8_model, layer_th_dicts, quantize_method_list):
    layer_names = []
    ignored_layers = ["Coeff", "Accuracy"]  # 忽略无需量化的层
    # 筛选有效层（排除忽略层、无效层）
    for layer_name in all_op_names:
        layer_proto = int8_model.parser.get_op_by_op_name(layer_name)
        if layer_proto and layer_proto.type not in ignored_layers:
            layer_names.append(layer_name)
    # 校验层名是否在阈值字典中（确保阈值与层名匹配）
    for layer_name in layer_names:
        if layer_name not in layer_th_dicts[quantize_method_list[0]]:
            self.mix_prec.logger.print_dbg("层名不匹配，退出")
            exit(1)
    return layer_names
```

- 核心作用：确保后续敏感层搜索仅针对 “需要量化且有有效阈值” 的层，避免无效计算。

4. 阈值更新工具 `set_layer_new_th`

为单个层更新量化阈值，并生成新的校准表（用于混合精度模型加载）。

```Python
def set_layer_new_th(self, model, layer_name, value):
    # 更新校准表中目标层的阈值
    self.cali_table.thresholds_map[layer_name][0] = float(value)
    new_cali_table_name = "new_cali_table.txt"
    self.cali_table.update_to(new_cali_table_name, layer_name, float(value))
    return new_cali_table_name
```

- 核心作用：为敏感层测试提供 “快速调整阈值→生成新校准表” 的工具，无需重新计算所有层阈值。

5. 核心：敏感层搜索 `search_sensitve_layer`

逐一测试各层的量化敏感性，为每个层选择最优量化方法和阈值，是整个工具的核心逻辑。

```Python
def search_sensitve_layer(self, layer_names, quantize_method_list, float_model, int8_model, layer_th_dicts, global_compare_layers, layers_rate, predictions_gt):
    fp_layer_list = layer_names.copy()  # 初始假设所有层为高精度（后续逐个测试）
    modified_layers = {}  # 记录每层的测试状态：{层名: [已试方法数, 最小损失, 最优阈值, 最优方法]}
    sensitive_layer_analysis_dict = {}  # 最终敏感层分析结果：{层名: [精度损失, 层类型]}
    
    # 遍历每个层，测试其量化敏感性
    for layer_name in layer_names:
        layer_type = self.parser.get_op_type_by_op_name(layer_name)
        self.mix_prec.logger.print_info(f"开始处理层：{layer_name}（类型：{layer_type}）")
        fp_layer_list.remove(layer_name)  # 临时将当前层设为INT8量化（测试其影响）
        ret = False
        
        # 循环尝试所有量化方法，找到最优阈值
        while not ret:
            # 第一次测试：使用第一种量化方法（如MAX）
            if layer_name not in modified_layers:
                method = quantize_method_list[0]
                new_th = layer_th_dicts[method][layer_name][1]  # 当前方法的阈值
                new_cali_table = self.set_layer_new_th(int8_model, layer_name, new_th)
                # 生成混合精度模型（仅当前层INT8，其余高精度）
                mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table, mix_table)
                # 计算精度损失（1 - 余弦相似度，损失越小越好）
                outputs_cos_loss = 1 - self.mix_prec.run_model(mixmodel, False, ...)
                # 记录初始状态
                modified_layers[layer_name] = [1, outputs_cos_loss, new_th, method]
            
            # 尝试剩余量化方法
            elif modified_layers[layer_name][0] < len(quantize_method_list):
                method_idx = modified_layers[layer_name][0]
                method = quantize_method_list[method_idx]
                new_th = layer_th_dicts[method][layer_name][1]
                new_cali_table = self.set_layer_new_th(int8_model, layer_name, new_th)
                # 重新生成模型并计算损失
                mixmodel = MixQuantModel(...)
                outputs_cos_loss = 1 - self.mix_prec.run_model(...)
                # 更新最优状态（损失更小则替换）
                if outputs_cos_loss < modified_layers[layer_name][1]:
                    modified_layers[layer_name][1] = outputs_cos_loss
                    modified_layers[layer_name][2] = new_th
                    modified_layers[layer_name][3] = method
                modified_layers[layer_name][0] += 1
            
            # 所有方法尝试完毕，记录最优结果
            elif modified_layers[layer_name][0] == len(quantize_method_list):
                # 记录当前层的最优损失、最优阈值、最优方法
                best_loss = modified_layers[layer_name][1]
                best_th = modified_layers[layer_name][2]
                best_method = modified_layers[layer_name][3]
                sensitive_layer_analysis_dict[layer_name] = [best_loss, layer_type]
                self.mix_prec.logger.print_info(f"层{layer_name}最优：阈值{best_th}，方法{best_method}，损失{best_loss}")
                ret = True
        
        fp_layer_list.append(layer_name)  # 恢复当前层为高精度，继续测试下一层
    
    return sensitive_layer_analysis_dict, new_cali_table
```

- 核心逻辑：**控制变量法**—— 每次仅将一个层设为 INT8 量化（其余层高精度），通过尝试多种量化方法，计算该层量化后的精度损失，损失越大说明该层对量化越敏感。

6. 敏感层分析与高精度层选择 `analysis_sensitive_layers`

对搜索结果排序，按精度损失优先级选择需要保留高精度的层（避免全高精度导致性能下降）。

```Python
def analysis_sensitive_layers(self, sensitive_layer_analysis_dict):
    set_fp_layer_list = []  # 最终保留高精度的层列表
    # 按精度损失逆序排序（损失越大，敏感度越高，优先保留高精度）
    sorted_sensitive_layers = sorted(sensitive_layer_analysis_dict.items(), key=lambda x: x[1][0], reverse=True)
    
    for idx, (layer_name, [loss, layer_type]) in enumerate(sorted_sensitive_layers):
        self.mix_prec.logger.print_info(f"第{idx}敏感层：{layer_name}，损失{loss}，类型{layer_type}")
        # 按用户设定的最大高精度层数量选择
        if self.args.max_float_layers > len(set_fp_layer_list):
            set_fp_layer_list.append(layer_name)
            # 关联层处理：Conv和Scale层需同时保留高精度（避免单独量化导致精度损失）
            if layer_type == "top.Conv":
                for next_op in self.parser.get_next_op_by_op_name(layer_name):
                    if self.parser.get_op_type_by_op_name(next_op) == "top.Scale":
                        set_fp_layer_list.append(next_op)
            elif layer_type == "top.Scale":
                for pre_op in self.parser.get_pre_op_by_op_name(layer_name):
                    if self.parser.get_op_type_by_op_name(pre_op) == "top.Conv":
                        set_fp_layer_list.append(pre_op)
    return set_fp_layer_list
```

- 核心逻辑：优先将 “损失最大的敏感层” 设为高精度，同时处理 Conv\-Scale 关联层（二者依赖紧密，单独量化会叠加损失），平衡精度和性能。

7. 主执行流程 `run`

串联所有方法，完成从 “全 INT8 精度校验” 到 “混合精度生成” 的完整流程：

```Python
def run(self):
    t0 = time.time()
    # 步骤1：测试全INT8模型精度（若达标则直接退出，无需混合精度）
    float_model = MixQuantModel(self.fp32_mlir, self.chip)  # 浮点基准模型
    int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.cali_table_name)  # 全INT8模型
    _ = self.mix_prec.run_model(float_model, True, ...)  # 浮点模型推理（获取基准输出）
    all_int8_cos = self.mix_prec.run_model(int8_model, False, ...)  # 全INT8精度
    if all_int8_cos > self.args.expected_cos:
        self.mix_prec.logger.print_info("全INT8精度达标，无需混合精度")
        exit(0)
    
    # 步骤2：生成3种方法的阈值字典
    all_op_names = self.parser.get_op_name_list()
    all_op_names = self.get_no_fused_tensors(all_op_names)  # 拆分融合层（确保层独立）
    quantize_method_list = ["MAX", "Percentile9999", "KL"]
    layer_th_dicts = self.gen_multiple_thresholds(all_op_names, quantize_method_list)
    
    # 步骤3：筛选并校验有效层名
    layer_names = self.check_layer_names(all_op_names, int8_model, layer_th_dicts, quantize_method_list)
    
    # 步骤4：搜索敏感层（逐一测试每层量化损失）
    sensitive_layer_dict, new_cali_table = self.search_sensitve_layer(...)
    
    # 步骤5：分析敏感层，选择高精度层
    set_fp_layer_list = self.analysis_sensitive_layers(sensitive_layer_dict)
    
    # 步骤6：生成最终混合精度模型和量化表
    mix_table = self.mix_prec._gen_mix_table(set_fp_layer_list)  # 混合精度配置表
    mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table, mix_table)
    final_cos = self.mix_prec.run_model(mixmodel, False, ...)  # 最终混合精度精度
    self.print_log_info(..., set_fp_layer_list, all_int8_cos, final_cos, t0)  # 输出结果
    
    return 'success'
```







## learning\_quant\.py

这段代码是 TPU\-MLIR 工具链中用于**量化参数学习**的核心脚本，支持多种先进的量化优化算法（如 LSQ、AdaRound、GPTQ 等），通过学习的方式调整量化参数（如尺度因子、权重量化阈值等），以最小化量化误差，提升量化模型（如 INT8/INT4）的精度。

### 整体背景

在模型量化中，传统静态校准（如基于直方图的阈值计算）可能无法最优适配模型特性，导致精度损失。而通过**学习式量化**（Learning\-based Quantization），可以利用训练数据动态调整量化参数（如尺度、权重量化方式），使量化模型的输出尽可能接近浮点模型，从而提升精度。这段代码正是实现了这一过程，支持多种学习策略，适配不同的量化需求。

### 核心功能与流程

代码的核心逻辑是：根据用户指定的 “学习目标”（如学习尺度因子、优化权重量化等），加载浮点模型、校准表和数据集，通过特定的学习算法调整量化参数，最终生成优化后的校准表或权重配置。

1. 命令行参数解析

首先通过`argparse`解析用户输入的参数，涵盖量化学习的关键配置，主要参数说明如下：

- 模型与数据相关：`mlir_file`（FP32 模型的 MLIR 文件）、`dataset`（数据集路径）、`input_num`（用于学习的样本数）等；

- 学习配置：`epoch`（学习轮数）、`mini_batch`（批大小）、`lr`（学习率）、`lr_scheduler`（学习率调度器）、`opt`（优化器，如 SGD/ADAM）等；

- 量化目标：`target`（指定学习策略，如`Scale`/`AdaWeight`/`GptWeight`等）；

- 输出配置：`output_calibration_table`（优化后的校准表路径）等。

2. 初始化与准备工作

```Python
# 检查芯片支持（目前仅支持bm1684x和bm1688）
if args.chip not in ["bm1684x", "bm1688"]:
    print("仅支持bm1684x和bm1688！")
    sys.exit(1)

# 初始化日志、线程池（用于并行处理）
loger = logging()
pool = ThreadPool(args.threads)

# 初始化尺度搜索器、校准表（加载初始校准表，准备输出）
scale_searcher = LearningScale(args)
scale_searcher.loger = loger
cali_table = CaliTable(args.calibration_table, args.output_calibration_table)
scale_searcher.orig_scales8 = cali_table.table  # 初始INT8尺度
scale_searcher.orig_scales4 = cali_table.table4  # 初始INT4尺度

# 准备输入数据（加载、预处理数据集）
all_inputs = learning_inputs(scale_searcher.parser, args)
num_sample = all_inputs.prepare(args.input_num)  # 准备指定数量的样本
```

- 核心准备：加载初始校准表（包含静态校准得到的量化参数）、准备训练数据（用于学习过程中的误差计算）、初始化日志和并行处理池。

3. 学习策略选择与执行

根据`args.target`选择不同的量化学习策略，每种策略对应一个专门的类（如`LearningScale`对应尺度学习，`LearningGptqWeight`对应 GPTQ 权重量化等），以下是各策略的核心逻辑：

##### （1）学习尺度因子（`target=Scale`）

对应`LearningScale`类，实现**LSQ（Learned Step Size Quantization）** 算法，通过学习优化激活值的量化尺度因子，最小化量化模型与浮点模型的输出差异。

```YAML
if learn_scale:
    # 准备参考张量（浮点模型的输出，作为学习目标）
    scale_searcher.num_sample = num_sample
    scale_searcher.ref_tensors = ref_tensors(scale_searcher, all_inputs, loger)
    
    # 初始化学习率调度器和优化器
    scheduler = LrScheduler(args.lr, scale_searcher.num_sample, args.lr_scheduler)
    if args.opt == 'SGD':
        scale_searcher.init_sgd(scheduler, args.momentum, args.nesterov, args.weight_decay)
    else:
        scale_searcher.init_adam(scheduler, 0.9, 0.999, args.weight_decay)
    
    # 执行尺度学习
    scale_searcher.learning()
    
    # 更新校准表并写入输出
    cali_table.update(scale_searcher.new_scales)
    cali_table.write()
```

- 核心逻辑：以浮点模型输出为 “参考目标”，通过反向传播优化激活量化的尺度因子，使量化模型输出尽可能接近参考值。

##### （2）AdaWeight 学习（`target=AdaWeight`）

对应`LearningAdaWeight`类，实现**AdaRound**算法，通过学习权重的量化偏移（rounding），减少权重量化误差。

```Plain Text
if learn_adaweight:
    scheduler = LrScheduler(args.lr, num_sample*args.epoch, args.lr_scheduler)
    weight_searcher = LearningAdaWeight(args)
    weight_searcher.loger = loger
    weight_searcher.scales = cali_table.table  # 加载初始尺度
    weight_searcher.ref_tensors = ref_tensors(weight_searcher, all_inputs, loger)  # 参考张量
    weight_searcher.init_opt(scheduler, args.momentum, args.nesterov, args.weight_decay)  # 优化器
    weight_searcher.learning()  # 执行权重量化优化
```

- 核心逻辑：权重量化时，通过学习调整 “四舍五入” 的偏移（而非简单四舍五入），使量化权重的输出误差最小化。

##### （3）GPTQ 权重学习（`target=GptWeight`）

对应`LearningGptqWeight`类，实现**GPTQ（Gradient\-based Post\-training Quantization）** 算法，适用于 Transformer 等大模型的权重量化，通过梯度下降优化量化权重。

```Plain Text
if learn_gptweight:
    weight_searcher = LearningGptqWeight(args)
    weight_searcher.loger = loger
    weight_searcher.scales = cali_table.table
    weight_searcher.ref_tensors = ref_tensors(weight_searcher, all_inputs, loger)
    # 过滤无需量化的层（固定为浮点）
    fix_float = get_fixed_float_layers(args.mlir_file, 'INT8', args.chip, args.calibration_table, args.qtable, weight_searcher.ref_tensors)
    weight_searcher.filter_fixed_floats(fix_float)
    weight_searcher.learning()  # 执行GPTQ权重优化
```

- 核心逻辑：针对大模型权重，通过高效的梯度计算优化量化误差，支持部分层保留浮点精度。

##### （4）双阈值学习（`target=EasyQuant`）

对应`EasyQuant`类，同时优化激活和权重的量化阈值，平衡二者的量化误差。

```Plain Text
if learn_dualthreshold:
    equant_searcher = EasyQuant(args)
    equant_searcher.loger = loger
    equant_searcher.scales = cali_table.table
    equant_searcher.ref_tensors = ref_tensors(equant_searcher, all_inputs, loger)
    equant_searcher.learning()  # 学习激活和权重的双阈值
    cali_table.update(equant_searcher.scales4, True)  # 更新INT4阈值
    cali_table.write()
```

##### （5）其他策略（`LossAwareQuant`/`Comq`）

- `LossAwareQuant`（LAPQ）：基于损失感知的量化，直接优化模型的任务损失（如分类损失）而非仅输出误差；

- `Comq`：组合量化策略，融合多种量化优化方法以进一步提升精度。

4. 资源清理与日志结束

学习完成后，清理参考张量等资源，结束日志记录：

```Python
loger.end()
```

### 核心价值

这段代码通过集成多种先进的学习式量化算法，解决了传统静态校准精度不足的问题。其核心优势在于：

1. **灵活性**：支持多种量化策略（尺度、权重、双阈值等），适配不同模型和硬件需求；

2. **高精度**：通过学习优化量化参数，使量化模型输出更接近浮点模型，显著提升精度；

3. **工程化**：结合 TPU\-MLIR 工具链，直接生成适配目标芯片的校准表，便于后续部署。



## smoothquant\.py

这段代码是 **SmoothQuant（平滑量化）算法的实现**，核心用于模型量化前调整 LayerNorm\+MatMul 结构的权重和激活尺度，减少量化误差，适配 MLIR 格式模型的优化流程。

### 一、核心功能概述

代码围绕 MLIR 模型展开，通过筛选特定算子（LayerNorm 后接 MatMul），计算激活和权重的尺度，按比例调整权重参数，最终保存优化后的权重，为后续量化（如 INT8 量化）做准备。

### 二、关键模块与方法解析

1. 初始化（**init**）

- 加载 MLIR 模型文件和解析器（MlirParser），获取模型输入数量、批次大小等信息。

- 处理输入数据列表（来自 DataSelector），若为图片输入则补齐批次（确保能被 batch\_size 整除）。

- 存储关键参数：alpha（平滑系数，0\-1 之间，平衡激活和权重的调整比例）。

2. 输入与权重加载

##### （1）init\_ppa

- 初始化输入预处理配置（preprocess），按模型输入算子的配置加载预处理规则（仅图片输入时使用）。

##### （2）load\_net\_weights

- 从 MLIR 解析器指定的路径加载权重文件（\.npz 格式），存储为字典格式（key 为权重名，value 为 numpy 数组）。

##### （3）load\_net\_inputs

- 处理两种输入类型：npz 文件（直接加载张量）和图片（需通过 preprocess 预处理）。

- 按批次拼接输入数据，生成批次化的输入张量字典（key 为批次索引，value 为输入名 \- 张量映射）。

- 最终修正输入批次数量，确保至少有一个有效批次。

3. 目标算子筛选

##### （1）get\_smooth\_op\_names

- 筛选需平滑的算子：仅保留 **LayerNorm 类型**，且后续所有算子（跳过形状变换）均为 **MatMul 类型** 的 LayerNorm 算子。

- 这类结构是 SmoothQuant 的优化目标，因 LayerNorm 输出激活范围与 MatMul 权重范围易不匹配，量化时误差较大。

```Python
def get_smooth_op_names(self):
    self.smooth_op_names = []
    for op in self.all_op_names:
        # 第一步：筛选 LayerNorm 算子
        if self.parser.get_op_type_by_op_name(op) == 'top.LayerNorm':
            # 第二步：跳过形状变换，找真正的后续算子
            users = self.skip_reshape_like_users(op)
            # 第三步：验证所有后续算子都是 MatMul
            for user in users:
                if self.parser.get_op_type_by_op_name(user) != 'top.MatMul':
                    break
            else: # 所有后续算子都是 MatMul，才纳入优化
                self.smooth_op_names.append(op)
```

##### （2）skip\_reshape\_like\_users

- 辅助筛选算子：跳过 Slice、Reshape、Permute 等形状变换算子，找到 LayerNorm 真正的后续算子（确保是 MatMul）。

- 处理 Concat 算子的特殊情况，优先遍历以保证筛选准确性。

```Python
def skip_reshape_like_users(self, op):
    candidates = self.parser.get_next_op_by_op_name(op)
    while candidates:
        user = candidates.pop()
        user_type = self.parser.get_op_type_by_op_name(user)
        # 跳过不改变最后一维的形状变换（Slice/Reshape/Permute等）
        if user_type in ['top.Slice', 'top.Squeeze', 'top.Reshape']:
            input_shape = self.module.get_tensor(user_op.opds[0]).shape
            output_shape = self.module.get_tensor(user).shape
            if input_shape[-1] == output_shape[-1]: # 特征维度不变，继续找下一级
                users2 = self.parser.get_next_op_by_op_name(user)
                add_candidates(users2)
        else:
            users.append(user) # 非形状变换，记录为真正后续算子
```

4. 尺度收集与平滑量化

##### （1）collect\_activation\_scales

- 遍历所有输入批次，执行模型推理（module\.invoke \(\)），获取目标 LayerNorm 算子的输出激活值。

- 计算激活值的尺度：按最后一维（特征维度）取绝对值最大值，存储为激活尺度字典。

```Python
def collect_activation_scales(self):
    self.activation_scales = {}
    for idx in range(self.args.input_num): # 遍历所有输入批次
        # 1. 设置当前批次输入张量
        for name in self.module.input_names:
            self.module.set_tensor(name, self.input_tensors[idx][name], ...)
        # 2. 执行模型推理，得到激活值
        self.module.invoke()
        # 3. 计算目标 LayerNorm 的激活尺度
        for op_name in self.smooth_op_names:
            outputs = self.parser.get_outputs_by_op_name(op_name)
            for output in outputs:
                activation = self.module.get_tensor(output).copy() # 获取 LayerNorm 输出激活
                activation = activation.reshape(-1, activation.shape[-1]) # 展平为 (样本数, 特征数)
                scales = np.abs(activation).max(axis=0) # 按特征维度取最大绝对值（尺度）
                # 累计所有批次的最大尺度（确保覆盖所有输入场景）
                if output not in self.activation_scales:
                    self.activation_scales[output] = scales
                else:
                    self.activation_scales[output] = np.maximum(scales, self.activation_scales[output])
```

##### （2）collect\_weight\_scales

- 提取目标 LayerNorm 后续 MatMul 算子的权重，按第一维（输入特征维度）取绝对值最大值，存储为权重尺度字典。

```Python
def collect_weight_scales(self):
    self.weight_scales = {}
    for op_name in self.smooth_op_names: # 遍历目标 LayerNorm
        users = self.skip_reshape_like_users(op_name) # 找到对应的 MatMul 算子
        for user in users:
            assert self.parser.get_op_type_by_op_name(user) == 'top.MatMul'
            user_op = self.parser.get_op_by_op_name(user)
            user_weight = self.module_weights[user_op.opds[1]] # 获取 MatMul 的权重
            # 按第一维（输入特征维度）取最大绝对值（权重尺度）
            user_weight_scale = np.abs(user_weight).max(axis=1)
            if op_name not in self.weight_scales:
                self.weight_scales[op_name] = user_weight_scale
            else:
                self.weight_scales[op_name] = np.maximum(user_weight_scale, self.weight_scales[op_name])
```

##### （3）smoothquant（核心逻辑）

- 计算平滑尺度：`smooth_scale = 激活尺度^alpha / 权重尺度^(1-alpha)`，处理无穷大 / NaN 异常值（设为 1）。

- 调整权重：

    1. 缩小 LayerNorm 的权重和偏置（除以 smooth\_scale）。

    2. 放大后续 MatMul 的权重（乘以 smooth\_scale，确保矩阵乘法结果不变）。

- 保存调整后的权重到原 \.npz 文件（覆盖原文件）。

```Python
def smoothquant(self):
    for op_name in self.smooth_op_names:
        # 1. 获取当前 LayerNorm 的激活尺度和对应 MatMul 的权重尺度
        weight_scale = self.weight_scales[op_name]
        activation_scale = self.activation_scales[op_name]
        # 2. 计算平滑尺度：平衡激活和权重（alpha 是调节系数）
        smooth_scale = activation_scale ** self.alpha / weight_scale ** (1 - self.alpha)
        smooth_scale[np.isinf(smooth_scale)] = 1 # 处理异常值
        smooth_scale[np.isnan(smooth_scale)] = 1
        
        # 3. 调整 LayerNorm 的权重和偏置（缩小 smooth_scale 倍）
        opds = self.parser.get_opds_by_op_name(op_name) # opds[1]是权重，opds[2]是偏置
        self.module_weights[opds[1]] /= smooth_scale # LayerNorm 权重缩小
        if len(opds) > 2:
            self.module_weights[opds[2]] /= smooth_scale # LayerNorm 偏置同步缩小
        
        # 4. 调整后续 MatMul 的权重（放大 smooth_scale 倍）
        smooth_scale = smooth_scale.reshape(-1, 1) # 适配矩阵乘法维度
        users = self.skip_reshape_like_users(op_name)
        for user in users:
            user_opds = self.parser.get_opds_by_op_name(user)
            matmul_weight = self.module_weights[user_opds[1]]
            if matmul_weight.ndim == 1:
                # 处理一维权重的广播逻辑
                repeat_num = matmul_weight.shape[0] // smooth_scale.shape[0]
                tmp_scale = np.repeat(smooth_scale, repeat_num, axis=1).flatten()
                matmul_weight *= tmp_scale
            else:
                matmul_weight *= smooth_scale # 二维权重直接乘（广播机制）
        
        # 5. 保存调整后的权重（覆盖原文件）
        os.system(f'rm -f {self.parser.module_weight_file}')
        np.savez(self.parser.module_weight_file, **self.module_weights)
```

5. 执行入口（run）

- 按流程串联所有步骤：初始化预处理 → 加载权重 → 加载输入 → 筛选目标算子 → 收集尺度 → 执行平滑量化。

- 若未筛选到目标算子或 alpha=0（不调整），则跳过平滑步骤。

```Python
def run(self):
    if self.data_selector.all_image:
        self.init_ppa() # 图片输入初始化预处理
    self.load_net_weights() # 加载权重
    self.load_net_inputs() # 加载输入
    self.get_smooth_op_names() # 筛选目标算子
    if len(self.smooth_op_names) and self.alpha != 0:
        self.collect_weight_scales() # 收集权重尺度
        self.collect_activation_scales() # 收集激活尺度
        self.smoothquant() # 执行权重调整
```

### 三、关键特性与适用场景

- **输入兼容性**：支持 npz 张量文件和图片输入，自动处理批次对齐。

- **针对性优化**：仅优化 LayerNorm\+MatMul 结构，贴合 Transformer 类模型的常见架构。

- **参数可控**：alpha 参数可调节，alpha=0 不调整，alpha=1 仅调整激活，0\.5 平衡两者。

- **依赖工具**：基于 pymlir（MLIR 模型操作）、numpy（张量计算）、tqdm（进度条），需配合自定义的 DataSelector、MlirParser 工具类。



## transformer\_pattern\.py

### 一、代码整体原理

文件，自动识别模型所属的网络结构（如 BERT、ViT、YOLO、OpenCLIP 等），根据不同模型的精度敏感特性筛选出需要保留浮点（FP）计算的层，最终生成量化配置表（qtable），指导后续量化过程中哪些层采用混合精度计算，哪些层进行量化，从而在性能与精度间取得平衡。

整体逻辑可分为 5 个核心步骤：

1. **初始化配置**：加载输入参数（如 MLIR 文件路径、芯片型号、量化模式等），初始化 MLIR 解析器和日志工具，确定量化规则（如部分量化的算子范围、混合精度类型）。

2. **提取算子序列**：解析 MLIR 模型，提取所有算子的类型及执行顺序，形成 “算子类型序列”（模型结构的 “指纹”）。

3. **模型结构匹配**：将提取的算子序列与预定义的模型模块模板（如`sub_blocks`中的 BERT 块、ViT 块）进行比对，识别模型所属类型（如 “这是一个 ViT 模型”）。

4. **筛选浮点层**：根据匹配到的模型类型，依据该模型的精度敏感特性（如激活层、后处理层对量化敏感），筛选出需要保留浮点计算的算子。

5. **生成量化表**：将筛选出的浮点层写入量化配置表（qtable），明确每个层的量化模式（如混合精度类型）。

### 二、重点代码具体分析

1. 全局变量：模型结构模板定义

```Python
sub_blocks = {
    "eva_block":['top.Add', 'top.LayerNorm', ...],  # EVA模型的核心模块算子序列
    "bert_block":['top.Add', 'top.LayerNorm', ...],  # BERT模型的核心模块算子序列
    # 其他模型模块...
}
openclip_blocks = {
    'openclip_vision_block': [...],  # OpenCLIP视觉模块算子序列
    'openclip_text_block': [...],    # OpenCLIP文本模块算子序列
    'l2_norm_block': [...]           # L2归一化模块算子序列
}
yolo_post_blocks = {
    "yolov5": [...],  # YOLOv5后处理模块算子序列
    # 其他YOLO版本...
}
N_mode = ['top.Relu', 'top.MaxPool', ...]  # N模式下需要量化的算子
H_mode = ['top.Conv', 'top.MatMul', ...]   # H模式下需要量化的算子
```

**功能与原理**：这些全局变量是**模型结构的 “特征模板”**，每个 key 对应一种模型模块（如`bert_block`对应 BERT 的 Transformer 块），value 是该模块中算子类型的执行顺序列表。

- 原理：不同模型的核心模块（如 Transformer 块、YOLO 后处理块）有固定的算子执行逻辑（例如 BERT 块必然包含 “Add→LayerNorm→MatMul” 的序列），通过比对模型的算子序列与这些模板，可快速识别模型类型。

- 特殊处理：`N_mode`和`H_mode`定义了 “部分量化” 模式下需要量化的算子范围（不在列表中的算子将保留浮点），用于未匹配到预定义模型时的兜底量化规则。

2. MatchPattern 类初始化（\_\_init\_\_方法）

```Python
class MatchPattern:
    def __init__(self, args):
        self.args = args
        self.fp32_mlir = args.mlir_file  # MLIR文件路径
        self.chip = args.chip  # 目标芯片型号
        self.cali_table_name = args.calibration_table  # 校准表路径
        self.module = pymlir.module()
        self.module.load(args.mlir_file)  # 加载MLIR模块
        self.parser = MlirParser(args.mlir_file)  # 初始化MLIR解析器
        self.cali_method = args.cali_method  # 校准方法
        self.log_level = "DEBUG" if args.debug_log else "INFO"
        self.logger = logger('Match_Pattern', log_level=self.log_level)  # 日志工具

        # 确定需要量化的算子（部分量化模式）
        if self.args.part_quantize == 'N_mode':
            self.quantize_ops = N_mode
        elif self.args.part_quantize == 'H_mode':
            self.quantize_ops = H_mode
        elif self.args.part_quantize == 'custom_mode':
            self.quantize_ops = ['top.' + op for op in self.args.custom_operator]
        else:
            pass

        # 确定量化表路径
        if '/' in self.cali_table_name:
            last_index = self.cali_table_name.rfind('/')
            self.quantize_table = self.cali_table_name[:last_index + 1] + "qtable"
        else:
            self.quantize_table = "qtable"

        # 确定混合精度类型
        if args.fp_type == 'auto':
            self.mix_mode = FLOAT_MAP[self.chip]  # 自动匹配芯片支持的精度
        else:
            self.mix_mode = args.fp_type
            if args.fp_type not in chip_support_mix_fp_type[self.chip]:
                self.logger.print_info(f'参数错误，fp_type:{args.fp_type}不支持{self.chip}')
                exit(1)
```

**功能与原理**：初始化方法是 “环境准备阶段”，主要完成参数解析、工具初始化和量化规则配置。

- **参数解析**：接收命令行参数（如`args.mlir_file`是输入的 MLIR 模型路径，`args.chip`是目标芯片型号），为后续处理提供基础配置。

- **MLIR 加载与解析**：通过`pymlir.module().load()`加载 MLIR 模型，通过`MlirParser`创建解析器，用于后续提取算子名称、类型、前后依赖关系等信息。

- **部分量化配置**：根据`part_quantize`参数确定需要量化的算子列表（`quantize_ops`），例如`N_mode`对应预定义的`N_mode`列表，`custom_mode`则将用户输入的算子添加前缀`top.`后作为量化范围。

- **混合精度校验**：根据芯片型号校验`fp_type`（混合精度类型）是否支持，不支持则报错退出，确保量化配置符合硬件限制。

- **量化表路径生成**：基于校准表路径生成量化表（qtable）的保存路径，默认在当前目录或校准表同级目录下。

3. 量化表生成方法（gen\_qtable）

```Python
def gen_qtable(self, fp_layer_list, flag):
    self.logger.print_info(f'量化表已生成：{self.quantize_table}')
    with open(self.quantize_table, "w") as f:
        f.write("# 生成时间: {}\n".format(datetime.datetime.now()))
        f.write("# 芯片: {}  混合精度: {}\n".format(self.chip, self.mix_mode))
        f.write("# {}精度层数量: {}\n".format(self.mix_mode, len(fp_layer_list)))
        if self.args.part_quantize and flag == 0:
            f.write("# 部分量化模式\n")
        f.write("###\n")
        f.write("# op_name   量化模式\n")
        for layer in fp_layer_list:
            f.write("{} {}\n".format(layer, self.mix_mode))
```

**功能与原理**：将筛选出的浮点层列表写入量化配置表，作为后续量化工具的输入。

- **文件格式**：包含元信息（生成时间、芯片型号、混合精度类型）和核心配置（算子名 \+ 量化模式）。元信息用于追溯配置来源，核心配置明确每个算子的量化规则（此处`mix_mode`即该算子采用的混合精度类型）。

- **参数****`flag`****的作用**：`flag=0`表示未匹配到预定义模型，此时若开启部分量化，需在文件中标记 “部分量化模式”，便于后续排查问题。

4. 核心执行方法（run）

`run`方法是整个逻辑的核心，包含 “提取算子序列→模型匹配→筛选浮点层→生成量化表” 的完整流程，分步骤解析如下：

#### （1）提取模型算子序列

```Python
all_tensors = self.parser.get_op_name_list()  # 所有算子的名称列表（按执行顺序）
num_tensors = len(all_tensors)
type_tensors = []
for i in range(num_tensors):
    op_type = self.parser.get_op_type_by_op_name(all_tensors[i])  # 获取算子类型（如'top.Add'）
    type_tensors.append(op_type)
type_tensors_str = ''.join(map(str, type_tensors))  # 算子类型拼接成字符串（用于快速匹配）
```

**功能与原理**：将 MLIR 模型中所有算子的类型按执行顺序提取，形成 “算子类型序列”，作为模型结构的 “指纹”。

- 例如：若模型执行顺序为 “Add→LayerNorm→MatMul”，则`type_tensors`为`['top.Add', 'top.LayerNorm', 'top.MatMul']`，`type_tensors_str`为`'top.Addtop.LayerNormtop.MatMul'`。

- 后续通过判断预定义模板的算子序列字符串是否为`type_tensors_str`的子串，即可快速匹配模型类型。

#### （2）模型结构匹配

##### ① 常规模型模块匹配（sub\_blocks）

```Python
model_block_name = None
flag = 0  # 标记是否匹配到预定义模型
for name, sub_block in sub_blocks.items():
    sub_str = ''.join(map(str, sub_block))  # 模板算子序列拼接成字符串
    if sub_str in type_tensors_str:  # 判断模板是否为模型序列的子串
        count = type_tensors_str.count(sub_str)  # 统计模块出现次数（如Transformer块数量）
        flag = 1
        model_block_name = name
        self.logger.print_info(f"匹配到{name}，出现次数：{count}")
        break
```

**功能与原理**：遍历`sub_blocks`中的模型模板，若模板的算子序列字符串（`sub_str`）是模型算子序列字符串（`type_tensors_str`）的子串，则认为匹配成功，记录模型名称（`model_block_name`）和模块出现次数。

- 例如：若`sub_blocks`中`vit_block`的算子序列为`['top.Add', 'top.LayerNorm', ...]`，其拼接的`sub_str`若包含在`type_tensors_str`中，则判定该模型为 ViT 结构。

##### ② OpenCLIP 模型匹配（openclip\_blocks）

```Python
else:  # 若未匹配到sub_blocks中的模型，尝试匹配OpenCLIP
    openclip_block_counts = {
        name: type_tensors_str.count(''.join(map(str, sub_block)))
        for name, sub_block in openclip_blocks.items()
    }
    # OpenCLIP需同时包含视觉块、文本块和2个L2归一化块
    if all(count for count in openclip_block_counts.values()) and openclip_block_counts['l2_norm_block'] == 2:
        flag = 1
        model_block_name = 'openclip_block'
        for name, sub_block in openclip_blocks.items():
            count = openclip_block_counts[name]
            self.logger.print_info(f"匹配到{name}，出现次数：{count}")
        # 计算OpenCLIP文本模块中MLP层的索引范围（用于后续筛选浮点层）
        last_matmul_index = num_tensors - type_tensors_str[type_tensors_str.rfind('top.MatMul'):].count('top')
        first_text_block_index = type_tensors_str[:type_tensors_str.index(''.join(openclip_blocks['openclip_text_block']))].count('top')
        first_text_mlp_start_index = first_text_block_index + 27  # 文本MLP起始索引
        first_text_mlp_end_index = first_text_block_index + 31    # 文本MLP结束索引
```

**功能与原理**：OpenCLIP 模型结构特殊（包含视觉块、文本块和 L2 归一化块），需单独匹配：

- 先统计`openclip_blocks`中每个子模块在模型中的出现次数，若所有子模块均存在且`l2_norm_block`出现 2 次（分别用于图像和文本特征归一化），则判定为 OpenCLIP 模型。

- 额外计算文本模块中 MLP 层的索引范围（`first_text_mlp_start_index`到`first_text_mlp_end_index`），因为该区域对精度敏感，需保留浮点。

#### （3）分模型筛选浮点层

根据匹配到的模型类型（`model_block_name`），依据该模型的精度敏感特性筛选浮点层。

##### ① YOLO 模型后处理层筛选

```Python
if model_block_name in ['yolo_block', 'yolo_block_12']:
    from collections import Counter
    def find_sublist(main_list, sub_list):  # 基于算子频次匹配子序列（YOLO后处理可能顺序不固定）
        sub_len = len(sub_list)
        sub_counter = Counter(sub_list)
        matches = []
        for i in range(len(main_list) - sub_len + 1):
            window = main_list[i:i+sub_len]
            if Counter(window) == sub_counter:  # 频次一致即视为匹配
                matches.append((i, i+sub_len))
        return matches

    for name, yolo_post_block in yolo_post_blocks.items():
        matches = find_sublist(type_tensors, yolo_post_block)  # 匹配后处理模块
        if matches:
            self.logger.print_info(f"匹配到{name}后处理，数量：{len(matches)}")
            for start, end in matches:
                fp_layer_list.extend(all_tensors[start:end])  # 后处理模块内算子加入浮点列表
                # 递归收集后处理模块的后续所有依赖算子（确保完整流程保留浮点）
                for next_op in self.parser.get_next_op_by_op_name(all_tensors[end-1]):
                    if next_op:
                        visited = set()
                        def recursive_add(op):
                            if op in visited:
                                return
                            if op not in fp_layer_list:
                                fp_layer_list.append(op)
                            visited.add(op)
                            for next_op in self.parser.get_next_op_by_op_name(op):
                                recursive_add(next_op)
                        recursive_add(next_op)
            break

    # 拆分融合算子（如fused["op1, op2"]需拆分为原始算子）
    split_fuse_fp_layer_list = []
    for item in fp_layer_list:
        if item.startswith('fused['):
            match = re.search(r'fused\["(.*?)"\]', item)
            if match:
                fused_paths = [p.strip('"') for p in match.group(1).split(', ')]
                split_fuse_fp_layer_list.extend(fused_paths)
            else:
                split_fuse_fp_layer_list.append(item)
        else:
            split_fuse_fp_layer_list.append(item)
    self.gen_qtable(split_fuse_fp_layer_list, flag)
    return
```

**功能与原理**：YOLO 模型的后处理（如坐标解码、NMS 前处理）对量化极其敏感，需单独筛选：

- **后处理模块匹配**：通过`find_sublist`函数基于算子频次匹配（而非严格顺序），因为 YOLO 后处理的部分算子顺序可能因优化而变化，但频次固定。

- **递归收集依赖算子**：后处理模块的输出可能依赖多个后续算子（如 Concat→Reshape→TopK），通过`recursive_add`递归收集所有后续算子，确保整个后处理流程保留浮点。

- **融合算子拆分**：MLIR 优化会将连续算子融合为`fused["op1, op2"]`，需通过正则表达式拆分原始算子路径，避免配置失效。

##### ② 常规 Transformer 类模型（ViT/BERT/Swin 等）

```Python
else:  # 处理sub_blocks中匹配到的模型（如ViT、BERT、Swin等）
    for i in range(num_tensors):
        op_type = self.parser.get_op_type_by_op_name(all_tensors[i])
        # 通用规则：LayerNorm前的Add层保留浮点（LayerNorm对输入精度敏感）
        if op_type == 'top.LayerNorm':
            pre_op = self.parser.get_pre_op_by_op_name(all_tensors[i])  # 获取前序算子
            if len(pre_op) == 1 and self.parser.get_op_type_by_op_name(pre_op[0]) == 'top.Add':
                fp_layer_list.append(pre_op[0])
        # 通用规则：SiLU/GELU激活层保留浮点（非线性激活量化易失真）
        if op_type == 'top.SiLU' or op_type == 'top.GELU':
            fp_layer_list.append(all_tensors[i])
        
        # ViT模型专属规则
        if model_block_name == 'vit_block':
            if count < 20:  # 小模型：Softmax后的MatMul保留浮点
                if op_type == 'top.Softmax':
                    next_op = self.parser.get_next_op_by_op_name(all_tensors[i])
                    if next_op and self.parser.get_op_type_by_op_name(next_op[0]) == 'top.MatMul':
                        fp_layer_list.append(next_op[0])
            else:  # 大模型：GELU后的MatMul保留浮点
                if op_type == 'top.GELU':
                    next_op = self.parser.get_next_op_by_op_name(all_tensors[i])
                    if next_op and self.parser.get_op_type_by_op_name(next_op[0]) == 'top.MatMul':
                        fp_layer_list.append(next_op[0])
        
        # 其他模型专属规则（BERT/Swin/EVA等，逻辑类似，根据模型特性筛选）
        # ...（省略BERT、Swin等模型的筛选代码）

    self.gen_qtable(fp_layer_list, flag)
```

**功能与原理**：针对不同 Transformer 类模型的精度敏感特性，制定专属筛选规则：

- **通用规则**：`LayerNorm`前的`Add`层（输入影响 LayerNorm 精度）、`SiLU/GELU`激活层（非线性量化易失真）均保留浮点。

- **模型专属规则**：例如 ViT 模型根据模块数量（`count`）区分小 / 大模型，小模型保留`Softmax`后的`MatMul`，大模型保留`GELU`后的`MatMul`，这是基于实际量化经验的精度优化策略。

##### ③ OpenCLIP 模型专属筛选

```Python
if model_block_name == 'openclip_block':
    for i in range(num_tensors):
        op_type = self.parser.get_op_type_by_op_name(all_tensors[i])
        # 保留最后一个MatMul之后的所有层、文本MLP层、L2归一化相关层
        if i >= last_matmul_index or (first_text_mlp_start_index <= i <= first_text_mlp_end_index):
            fp_layer_list.append(all_tensors[i])
        elif op_type in ['top.Abs', 'top.Reduce', 'top.Sqrt', 'top.Softmax', ...]:
            fp_layer_list.append(all_tensors[i])
        elif op_type == 'top.Div':
            fp_layer_list.append(f'{all_tensors[i]}_inv')  # 适配部署时的算子名修改
        # ...（其他OpenCLIP专属规则）
    self.gen_qtable(fp_layer_list, flag)
```

**功能与原理**：OpenCLIP 的文本 / 视觉特征匹配对精度敏感，筛选规则包括：

- 保留最后一个`MatMul`之后的所有层（特征输出相关）、文本模块 MLP 层（索引范围预计算）。

- 特殊处理`top.Div`算子：部署时该算子名会被自动添加`_inv`后缀，提前适配确保配置生效。

#### （4）部分量化逻辑（未匹配到预定义模型时）

```Python
if flag == 0 and self.args.part_quantize:  # 未匹配到模型但开启部分量化
    for j in range(num_tensors):
        op_type = self.parser.get_op_type_by_op_name(all_tensors[j])
        if op_type not in self.quantize_ops:  # 不在量化列表中的算子保留浮点
            fp_layer_list_part_quantize.append(all_tensors[j])
    self.gen_qtable(fp_layer_list_part_quantize, flag)
```

**功能与原理**：当模型未匹配到任何预定义模板，但开启了部分量化（`part_quantize`），则按预定义的`quantize_ops`列表筛选：不在列表中的算子保留浮点，其余量化。这是对小众模型的兜底支持。



### staging/ 实验性方法（7个高级量化算法）

## utils\.py

### 一、代码整体原理逻辑阐述

该代码是**TPU\-MLIR 量化工具链中的量化优化与评估模块**，核心目标是：通过处理输入数据、管理模型张量、计算量化误差、优化量化参数（如校准表阈值），提升量化模型（如 INT8）的精度。整体逻辑围绕 “量化 \- 评估 \- 优化” 闭环展开，包含以下核心功能：

1. **量化基础工具**：提供量化 / 反量化计算、误差评估（MSE、余弦相似度）、层依赖分组等基础功能。

2. **数据处理**：加载并预处理不同类型的数据集（ImageNet 图像、NPY/NPZ 文件），为量化评估提供输入数据。

3. **模型量化与评估**：将 MLIR 模型转换为量化模型（如 INT8），对比量化前后的输出差异，评估量化精度。

4. **参考张量管理**：高效缓存模型各层的浮点输出（参考值），用于与量化后输出对比，减少重复计算。

5. **优化组件**：通过学习率调度器调整优化步长，通过校准表管理量化参数（阈值、min、max），实现量化参数的迭代优化。

### 二、代码逐部分详细分析

1. 量化基础工具函数

#### （1）量化与反量化函数（quant\_requant\_active）

```Python
def quant_requant_active(data, scale, unsigned=False, bits=8):
    if unsigned:  # 无符号量化（0~2^bits-1）
        d = data / scale * (2**bits - 1)  # 量化：数据/缩放因子 → 映射到量化范围
        dout = np.round(d)  # 四舍五入取整
        return np.clip(dout, 0, 2**bits - 1) / (2**bits - 1) * scale  # 反量化：裁剪后映射回原范围
    else:  # 有符号量化（-2^(bits-1)~2^(bits-1)-1）
        d = data / scale * (2**(bits - 1) - 1)
        dout = np.round(d)
        return np.clip(dout, -(2**(bits - 1)), 2**(bits - 1) - 1) / (2**(bits - 1) - 1) * scale
```

**功能与原理**：实现量化（浮点→整数）和反量化（整数→浮点）的核心计算，用于模拟量化过程并评估精度损失。

- 量化本质：将浮点数据通过缩放因子（`scale`）映射到整数范围（如 INT8 的 \- 128\~127），反量化则通过相同缩放因子映射回浮点。

- 关键处理：`np.clip`确保量化值不超出整数范围，避免溢出导致的精度异常。

#### （2）误差评估函数（cal\_loss、cosine\_sim）

```Python
def cal_loss(target, ref):
    mse_diff = ((target - ref)** 2).mean()  # 均方误差（MSE）
    return mse_diff

def cosine_sim(x, y):
    x[np.isnan(x)] = 0.0  # 处理NaN值
    y[np.isnan(y)] = 0.0
    # 余弦相似度=1-余弦距离，衡量向量方向一致性
    cosine_similarity = 1 - spatial.distance.cosine(x.flatten().astype(np.float32),
                                                    y.flatten().astype(np.float32))
    return cosine_similarity
```

**功能与原理**：评估量化后输出（`target`）与浮点参考输出（`ref`）的差异，是量化精度的核心指标。

- `cal_loss`：通过 MSE 衡量数值差异，值越小说明量化精度越高。

- `cosine_sim`：通过余弦相似度衡量向量方向一致性（对特征匹配类任务更重要），值越接近 1 说明方向越一致。

#### （3）层依赖分组函数（into\_groups）

```Python
def into_groups(parser, layers):
    groups = {}
    group_idx = 0
    for l in layers:
        allocated = False
        if group_idx == 0 and group_idx not in groups:  # 初始化第一个组
            groups[0] = [l]
            group_idx += 1
            continue
        # 检查当前层是否可加入已有组（无依赖关系）
        for i in np.arange(group_idx):
            notin = True  # 标记是否与组内层无依赖
            # 检查当前层的输出算子是否与组内层有依赖
            for out_op in parser.get_next_op_by_op_name(l):
                for layer in groups[i]:
                    if out_op in parser.get_pre_op_by_op_name(layer) or out_op in parser.get_next_op_by_op_name(layer):
                        notin = False  # 有依赖，不能加入该组
                        break
            if notin:
                # 检查当前层的输入算子是否与组内层有依赖
                for in_op in parser.get_pre_op_by_op_name(l):
                    for layer in groups[i]:
                        if in_op in parser.get_next_op_by_op_name(layer) or in_op in parser.get_pre_op_by_op_name(layer):
                            notin = False
                            break
            if notin:  # 无依赖，加入该组
                groups[i].append(l)
                allocated = True
                break
        if not allocated:  # 无法加入任何组，创建新组
            groups[group_idx] = [l]
            group_idx += 1
    return groups
```

**功能与原理**：根据层之间的依赖关系（输入 / 输出算子关联）将层分组，无依赖的层归为一组。

- 依赖判断：通过`parser`获取层的前序（`get_pre_op_by_op_name`）和后序（`get_next_op_by_op_name`）算子，若两层共享任何算子，则视为有依赖。

- 作用：分组可用于并行量化优化（组内层无依赖，可独立调整量化参数），提升优化效率。

#### （4）模型量化与评估工具（get\_fixed\_float\_layers、learning\_lower、lower\_and\_eval）

```Python
def get_fixed_float_layers(mlir, mode, chip, cali_table, q_table, ref_tensor):
    quanted_mlir_file = '{}_int8.quant_test.mlir'.format(mlir)
    # 将MLIR模型量化为INT8（调用mlir_lowering工具）
    mlir_lowering(mlir, quanted_mlir_file, mode, chip, 1, 1, cali_table, False, q_table)
    module = pymlir.module()
    module.load(quanted_mlir_file)  # 加载量化后的模型
    # 设置输入张量（使用参考输入）
    for input_name in module.input_names:
        data = ref_tensor.get(input_name, 0)
        module.set_tensor(input_name, data)
    module.invoke()  # 执行模型推理
    # 收集非INT8/UINT8的张量（即固定为浮点的层）
    float_tensors = []
    for op in module.all_tensor_names:
        qinfo = module.get_tensor_qinfo(op)
        if qinfo.dtype != "I8" and qinfo.dtype != "U8":
            float_tensors.append(op)
    return float_tensors

def learning_lower(mlir, mode, chip, cali_table, q_table):
    quanted_mlir_file = '{}_int8.quant_eval.mlir'.format(mlir)
    # 生成量化模型（用于评估）
    mlir_lowering(mlir, quanted_mlir_file, mode, chip, 1, 1, cali_table, False, q_table)
    module = pymlir.module()
    module.load(quanted_mlir_file)
    return module

def lower_and_eval(mlir, mode, chip, cali_table, q_table, ref_tensor, sample_idxs):
    module = learning_lower(mlir, mode, chip, cali_table, q_table)  # 获取量化模型
    outputs = []
    for s in sample_idxs:  # 对多个样本进行推理
        for input_name in module.input_names:
            data = ref_tensor.get(input_name, s)  # 获取第s个样本的输入
            module.set_tensor(input_name, data)
        module.invoke()  # 执行推理
        output = module.get_tensor(module.output_names[0]).copy()  # 保存输出
        outputs.append(output)
    del module  # 释放资源
    return outputs
```

**功能与原理**：这三个函数协同完成 “模型量化→推理→结果收集” 的流程，是量化评估的核心工具。

- `get_fixed_float_layers`：量化模型后，识别未被量化（仍为浮点）的层，用于确认量化配置是否生效。

- `learning_lower`：将原始 MLIR 模型转换为量化模型（如 INT8），返回可执行的模块。

- `lower_and_eval`：对多个样本执行量化模型推理，收集输出结果，用于与浮点模型输出对比（计算误差）。

2. 日志工具类（logging）

```Python
class logging:
    def __init__(self, filename="logging"):
        self.file_name = filename
        self.log_file = open(self.file_name, 'w')  # 打开日志文件

    def logging(self, info):
        print(info, file=self.log_file)  # 写入日志

    def end(self):
        if self.log_file is not None:
            self.log_file.close()  # 关闭文件
```

**功能与原理**：简单封装日志记录功能，将量化过程中的关键信息（如张量加载、释放）写入文件，便于调试和追溯。

3. 数据集处理类（imagenet\_dataset）

```Python
class imagenet_dataset:
    def __init__(self, path, input_size, mean, scale):
        self.path = path  # 数据集路径
        self.input_size = input_size  # 输入图像尺寸
        self.mean = mean  # 均值（用于归一化）
        self.scale = scale  # 缩放因子（用于归一化）
        self.format = 'bgr'
        self.shuffle = True
        self.val_batchsize = 1  # 验证集批次大小
        self.num_workers = 4  # 数据加载线程数

    def build_dataset(self):
        # 预处理参数转换（均值和缩放因子除以255，适应[0,1]范围的图像）
        mean = tuple([m/255.0 for m in self.mean])
        std = tuple([(1.0/x)/255.0 for x in self.scale])
        crop_pct = 0.875  # 裁剪比例
        # 构建训练/验证集的预处理流程
        val_transform = self.build_transform(input_size=self.input_size, mean=mean, std=std, crop_pct=crop_pct)
        # 加载ImageNet验证集
        valdir = os.path.join(self.path, 'val')
        val_dataset = datasets.ImageFolder(valdir, val_transform)
        # 构建数据加载器
        val_loader = torch.utils.data.DataLoader(
            val_dataset,
            batch_size=self.val_batchsize,
            shuffle=True,
            num_workers=self.num_workers,
            pin_memory=True,
        )
        return val_loader

    def build_transform(self, input_size=224, interpolation="bicubic",
                        mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225),
                        crop_pct=0.875):
        def _pil_interp(method):  # 图像插值方法映射
            if method == "bicubic":
                return Image.BICUBIC
            elif method == "lanczos":
                return Image.LANCZOS
            elif method == "hamming":
                return Image.HAMMING
            else:
                return Image.BILINEAR
        resize_im = input_size > 32  # 小尺寸图像不resize
        t = []
        if resize_im:
            # 先resize到较大尺寸，再中心裁剪到目标尺寸（保持比例）
            size = int(math.floor(input_size / crop_pct))
            ip = _pil_interp(interpolation)
            t.append(transforms.Resize(size, interpolation=ip))
            t.append(transforms.CenterCrop(input_size))
        # 转换为Tensor并归一化
        t.append(transforms.ToTensor())
        t.append(transforms.Normalize(mean, std))
        return transforms.Compose(t)  # 组合预处理步骤
```

**功能与原理**：专门处理 ImageNet 数据集的加载和预处理，为模型提供符合输入要求的数据。

- **预处理流程**：根据模型输入尺寸，先 resize 到合适大小，中心裁剪，再转换为 Tensor 并归一化（减去均值、除以标准差），确保输入数据分布与训练时一致。

- **数据加载**：使用`torch.utils.data.DataLoader`高效加载数据，支持多线程，提升数据读取速度。

4. 输入数据准备类（learning\_inputs）

```Python
class learning_inputs:
    def __init__(self, parser, args):
        self.dataset = args.dataset  # 数据集路径
        self.data_list = args.data_list  # 数据列表文件
        self.imagenet = args.imagenet  # 是否为ImageNet数据集
        self.batch_size = parser.get_batch_size()  # 批次大小
        self.input_num = parser.get_input_num()  # 输入数量（模型可能有多个输入）
        self.num_sample = 0  # 样本数量
        self.parser = parser  # MLIR解析器
        self.ref_activations = {}  # 存储参考输入数据（键：样本索引，值：{输入名: (数据, 计数)}）
        self.mean = []  # 均值（预处理用）
        self.scale = []  # 缩放因子（预处理用）
        self.resize_dims = []  # 输入尺寸
        self.pixel_format = 'rgb'  # 像素格式

    def prepare(self, input_num):
        tune_idx = 0  # 样本索引
        self.ref_activations[tune_idx] = {}  # 初始化第一个样本的输入字典
        input_names = [op.name for op in self.parser.inputs]  # 获取模型输入名

        if self.imagenet:  # 处理ImageNet数据集
            self.num_sample = input_num  # 样本数量
            if self.input_num > 1 or self.batch_size > 1:
                print('仅支持单输入图像网络！')
                sys.exit(1)
            # 获取预处理参数（从MLIR解析器中读取输入配置）
            for i in range(self.input_num):
                ppa = preprocess()
                ppa.load_config(self.parser.get_input_op_by_idx(i))
                self.mean = [x for x in ppa.mean.reshape(-1)]
                self.scale = [x for x in ppa.scale.reshape(-1)]
                self.resize_dims = ppa.net_input_dims
                self.pixel_format = ppa.pixel_format
            # 构建ImageNet数据集加载器
            imgnet_ds = imagenet_dataset(self.dataset, self.resize_dims[0], self.mean, self.scale)
            val_loader = imgnet_ds.build_dataset()
            cnt = 0  # 计数已加载样本
            batched_idx = 0  # 批次内索引
            for data, target in val_loader:  # 遍历数据加载器
                inputs = [s.strip() for s in input_names]
                assert (len(inputs) == 1)  # 确保单输入
                batched_idx += 1
                batched_inputs = self.input_num * ['']
                for i, input in enumerate(input_names):
                    batched_inputs[i] += '{},'.format(inputs[i])
                    if batched_idx == self.batch_size:  # 批次满，保存数据
                        x = data  # 图像数据（Tensor）
                        count = self.parser.get_user_count_by_op_name(input)  # 输入计数
                        self.ref_activations[tune_idx][input] = [x, count]
                if batched_idx == self.batch_size:  # 准备下一个样本
                    tune_idx += 1
                    batched_idx = 0
                    batched_inputs = self.input_num * ['']
                    self.ref_activations[tune_idx] = {}
                cnt += 1
                if cnt >= self.num_sample:  # 达到所需样本数量，停止
                    break
        else:  # 处理非ImageNet数据集（npy/npz文件）
            ds = DataSelector(self.dataset, input_num, self.data_list)  # 数据选择器
            ppa_list = []
            if ds.all_image:  # 图像文件
                for i in range(self.input_num):
                    ppa = preprocess()
                    ppa.load_config(self.parser.get_input_op_by_idx(i))
                    ppa_list.append(ppa)  # 每个输入对应一个预处理实例
                # 补齐批次（确保样本数为批次大小的倍数）
                n = len(ds.data_list) % self.batch_size
                if n != 0:
                    for i in range(self.batch_size - n):
                        ds.data_list.append(ds.data_list[-1])
                self.num_sample = len(ds.data_list) // self.batch_size
                batched_idx = 0
                batched_inputs = self.input_num * ['']
                for data in ds.data_list:  # 遍历图像路径
                    inputs = data.split(',')
                    inputs = [s.strip() for s in inputs]
                    assert (len(inputs) == self.input_num)
                    batched_idx += 1
                    for i, input in enumerate(input_names):
                        batched_inputs[i] += '{},'.format(inputs[i])
                        if batched_idx == self.batch_size:  # 批次满，预处理并保存
                            x = ppa_list[i].run(batched_inputs[i][:-1])  # 预处理图像
                            count = self.parser.get_user_count_by_op_name(input)
                            self.ref_activations[tune_idx][input] = [x, count]
                    if batched_idx == self.batch_size:  # 准备下一个样本
                        tune_idx += 1
                        batched_idx = 0
                        batched_inputs = self.input_num * ['']
                        self.ref_activations[tune_idx] = {}
            elif ds.all_npy:  # NPY文件（单张量）
                self.num_sample = len(ds.data_list)
                for data in ds.data_list:  # 遍历NPY路径
                    inputs = data.split(',')
                    inputs = [s.strip() for s in inputs]
                    assert (len(inputs) == self.input_num)
                    for name, npy in zip(input_names, inputs):
                        x = np.load(npy)  # 加载NPY数据
                        count = self.parser.get_user_count_by_op_name(name)
                        self.ref_activations[tune_idx][name] = [x, count]
                    tune_idx += 1
                    self.ref_activations[tune_idx] = {}
            elif ds.all_npz:  # NPZ文件（多张量）
                self.num_sample = len(ds.data_list)
                input_names = [op.name for op in self.parser.inputs]
                for data in ds.data_list:  # 遍历NPZ路径
                    npz = np.load(data)  # 加载NPZ文件
                    for name in input_names:
                        count = self.parser.get_user_count_by_op_name(name)
                        self.ref_activations[tune_idx][name] = [npz[name], count]  # 按名称取张量
                    tune_idx += 1
                    self.ref_activations[tune_idx] = {}
            else:
                raise RuntimeError("数据集格式错误")
        return self.num_sample  # 返回样本数量
```

**功能与原理**：统一处理不同类型的输入数据（ImageNet 图像、NPY、NPZ），预处理后存储为参考输入，供后续模型推理和量化评估使用。

- **多格式支持**：通过`DataSelector`区分图像、NPY、NPZ 文件，分别处理加载和预处理逻辑。

- **批次管理**：确保输入数据按模型批次大小组织，与模型输入要求匹配。

- **预处理集成**：通过`preprocess`类加载 MLIR 中定义的输入预处理配置（均值、缩放、尺寸等），确保输入数据符合模型预期。

5. 参考张量管理类（ref\_tensors）

```Python
class ref_tensors:
    def __init__(self, learner, inputs, loger=None):
        self.parser = learner.parser  # MLIR解析器
        self.module = learner.module  # 浮点模型模块
        self.net_inputs = inputs  # 输入数据（learning_inputs实例）
        self.epoch_samples = inputs.num_sample  # 样本数量
        self.ops = {}  # 算子：计数（该算子被多少下游算子依赖）
        self.ops_cnt = {}  # 算子：当前剩余依赖计数（用于缓存释放）
        self.ops_buffer = {}  # 算子：缓存的输出张量（键：算子名，值：[样本0输出, 样本1输出, ...]）
        self.loger = loger  # 日志工具
        self.init()  # 初始化算子计数

    def add_name(self, ops):  # 初始化算子字典（计数为0）
        for op in ops:
            if op in self.ops:
                continue
            else:
                self.ops[op] = 0
                self.ops_cnt[op] = 0

    def init(self):  # 计算每个算子的依赖计数
        # 收集所有算子（模型张量和解析器中的算子）
        self.add_name(self.module.all_tensor_names)
        self.add_name(self.parser.get_op_name_list())
        # 计算每个算子被依赖的次数（下游算子数量）
        for op in self.ops:
            cnt = 0
            for op_ in self.parser.ops:
                for op__ in op_.opds:  # 遍历算子的操作数（输入）
                    if op__ in self.parser.get_op_name_list() and op__ == op:
                        cnt += 1  # 被一个下游算子依赖，计数+1
            self.ops[op] = cnt
            if op in self.module.output_names:  # 输出算子额外+1（作为最终输出被依赖）
                self.ops[op] += 1

    def get(self, op, idx, quant=False, symetric=True):
        if idx > self.epoch_samples:  # 样本索引越界检查
            print(f"请求的索引超出范围 {idx} vs {self.epoch_samples}")
        # 若已缓存，直接返回
        if op in self.ops_buffer:
            self.loger.logging(f'从缓存返回 {op}')
            return self.ops_buffer[op][idx]
        # 若为输入算子，从参考输入中获取
        elif op in self.net_inputs.ref_activations[0]:
            self.loger.logging(f'从参考输入返回 {op}')
            return self.net_inputs.ref_activations[idx][op][0]

        # 若未缓存，递归获取其输入算子的输出，再计算当前算子的输出
        inputs = self.parser.get_pre_op_by_op_name(op)  # 当前算子的输入算子
        for in_ in inputs:
            if in_ not in self.ops_buffer and in_ not in self.net_inputs.ref_activations[0]:
                self.loger.logging(f'递归获取 {in_}')
                self.get(in_, idx, quant, symetric)  # 递归获取输入算子的输出
                self.ops_cnt[in_] = self.ops[in_]  # 更新输入算子的依赖计数

        # 计算所有样本的当前算子输出并缓存
        for l in range(self.epoch_samples):
            # 设置输入算子的张量（从缓存或参考输入）
            for in_ in inputs:
                if in_ in self.ops_buffer:
                    self.module.set_tensor(in_, self.ops_buffer[in_][l])
                elif in_ in self.net_inputs.ref_activations[0]:
                    self.module.set_tensor(in_, self.net_inputs.ref_activations[l][in_][0])
                else:
                    print("错误：输入算子不在缓存或参考输入中")
                    os.exit(1)
            # 执行到当前算子，获取输出
            outputs = self.module.invoke_at(op)
            for out_ in self.parser.get_outputs_by_op_name(op):  # 当前算子的输出算子
                if out_ in self.ops and self.ops[out_] > 0:  # 若输出算子被依赖
                    if out_ in self.ops_buffer:
                        self.ops_buffer[out_].append(self.module.get_tensor(out_).copy())
                    else:
                        self.ops_buffer[out_] = []
                        self.ops_buffer[out_].append(self.module.get_tensor(out_).copy())
                if l == 0:
                    self.loger.logging(f'添加 {out_} 计数 {self.ops[out_]}')
        # 更新输出算子的依赖计数
        for out_ in self.parser.get_outputs_by_op_name(op):
            if out_ in self.ops and self.ops[out_] > 0:
                self.loger.logging(f'设置 {out_} {self.ops[out_]}')
                self.ops_cnt[out_] = self.ops[out_]
        # 释放不再需要的输入算子缓存（依赖计数为0时）
        for in_ in inputs:
            if in_ in self.ops_buffer:
                self.ops_cnt[in_] -= 1
                if self.ops_cnt[in_] == 0:
                    del self.ops_buffer[in_]
                    self.loger.logging(f'删除输入缓存 {in_}')
        # 检查当前算子是否已缓存
        if op not in self.ops_buffer:
            print(f'{op} 不在参考张量中！')
            sys.exit(1)
        return self.ops_buffer[op][idx]

    def consumed_tensor(self, op):  # 标记算子已被使用，减少依赖计数，必要时释放缓存
        if op in self.net_inputs.ref_activations[0]:  # 输入算子不释放
            return
        if op not in self.ops_buffer:
            print(f"{op} 不在缓存中却标记为已使用！")
        else:
            self.loger.logging(f'已使用，减少计数 {op}')
            self.ops_cnt[op] -= 1
            if self.ops_cnt[op] == 0:  # 无依赖，释放缓存
                self.loger.logging(f'完全使用，删除缓存 {op}')
                del self.ops_buffer[op]
```

**功能与原理**：高效管理模型各层的浮点输出张量（参考值），用于与量化后输出对比。核心解决 “重复计算” 和 “内存占用过大” 问题。

- **缓存机制**：通过`ops_buffer`缓存算子输出，避免同一算子被多次计算（如多层共享同一输入时）。

- **依赖计数**：`ops`记录每个算子被下游依赖的次数，`ops_cnt`跟踪当前剩余依赖数，当计数为 0 时释放缓存，减少内存占用。

- **递归计算**：当需要某算子的输出时，自动递归计算其所有输入算子的输出，确保依赖链完整。

6. 学习率调度器类（LrScheduler）

```Python
class LrScheduler:
    def __init__(self, lr, max_iter, mode):
        self.lr = lr  # 初始学习率
        self.min_lr = lr / 10  # 最小学习率
        self.mode = mode  # 调度模式（Fixed/Cosine/MultiStep）
        self.max_iter = max_iter  # 最大迭代次数
        self.scale = 0.1  # 多步衰减的缩放因子
        self.warm_up = 0.2  # 余弦退火的预热比例（前20%迭代保持初始学习率）

    def cal_lr(self, iter):  # 计算当前迭代的学习率
        if self.mode == 'Fixed':
            return self.lr  # 固定学习率
        elif self.mode == 'Cosine':  # 余弦退火（预热+余弦衰减）
            if iter <= self.max_iter * self.warm_up:
                return self.lr  # 预热阶段：保持初始学习率
            else:
                # 退火阶段：从lr平滑衰减到min_lr
                return self.min_lr + 0.5 * (self.lr - self.min_lr) * (
                    1.0 + np.cos((iter - self.max_iter * self.warm_up) / 
                                (self.max_iter - self.max_iter * self.warm_up) * np.pi)
                )
        elif self.mode == 'MultiStep':  # 多步衰减
            if iter <= self.max_iter * 0.5:
                return self.lr  # 前50%迭代：初始学习率
            elif iter <= self.max_iter * 2/3:
                return self.lr * self.scale  # 50%~66.7%：衰减10倍
            else:
                return self.lr * self.scale * self.scale  # 66.7%后：衰减100倍
```

**功能与原理**：在量化参数优化过程中动态调整学习率，提升优化效果（如避免陷入局部最优、加速收敛）。

- **Fixed**：固定学习率，适用于简单优化场景。

- **Cosine**：余弦退火策略，先预热（保持高学习率），再按余弦曲线平滑衰减到最小学习率，适合需要精细调整的场景。

- **MultiStep**：多步衰减策略，在特定迭代阶段骤降学习率，适合快速收敛后精细优化。

7. 校准表管理类（CaliTable）

```Python
class CaliTable:
    def __init__(self, in_table, out_table):
        self.in_table = in_table  # 输入校准表路径（原始量化参数）
        self.out_table = out_table  # 输出校准表路径（优化后参数）
        self.table = {}  # 校准表数据（键：算子名，值：[阈值, min, max]）
        self.table4 = {}  # INT4校准表数据
        self.read()  # 读取输入校准表

    def read(self):
        int4 = False  # 是否为INT4校准表
        textfile = open(self.in_table, 'r')
        for line in textfile:
            if len(line) > 0 and (not line.startswith("#")):  # 跳过注释
                s = line.split(" ")
                s = [x for x in s if x != '']  # 分割并过滤空字符串
                if len(s) != 4:  # 校准表每行格式：op_name threshold min max
                    continue
                if int4:  # 读取INT4校准表
                    self.table4[s[0]] = [float(s[1]), float(s[2]), float(s[3])]
                else:  # 读取INT8校准表
                    self.table[s[0]] = [float(s[1]), float(s[2]), float(s[3])]
            if line.startswith("#int4_th"):  # 标记INT4校准表开始
                int4 = True

    def update(self, new_table, int4=False):  # 更新校准表参数（主要更新阈值）
        for op in new_table:
            if int4:  # 更新INT4校准表
                for op_ in self.table4:
                    if op_ == op:
                        self.table4[op][0] = new_table[op][0]  # 仅更新阈值（第一个元素）
            else:  # 更新INT8校准表
                for op_ in self.table:
                    if op_ == op:
                        self.table[op][0] = new_table[op][0]

    def write(self):  # 将更新后的校准表写入文件
        f = open(self.out_table, 'w')
        f.write("# 学习优化生成时间: {}\n".format(datetime.datetime.now()))
        f.write("# op_name    threshold    min    max\n")
        # 写入INT8校准表
        for op in self.table:
            f.write(f'{op}  {self.table[op][0]}  {self.table[op][1]}  {self.table[op][2]}\n')
        # 若有INT4校准表，写入
        if len(self.table4) > 0:
            f.write('\n')
            f.write('#int4_th\n')
            for op in self.table4:
                f.write(f'{op}  {self.table4[op][0]}  {self.table4[op][1]}  {self.table4[op][2]}\n')
        f.close()
```

**功能与原理**：管理量化校准表（存储量化所需的阈值、min、max 等参数），支持读取原始校准表、更新优化后的参数、写入新校准表。

- **校准表作用**：量化过程中，阈值决定浮点数据映射到整数的范围（如`scale = threshold / (2^bits-1)`），直接影响量化精度。

- **更新逻辑**：通过`update`方法调整阈值（其他参数 min/max 通常固定），实现量化参数的优化，提升量化精度。



## adaround\.py

### 一、代码整体原理逻辑阐述

该代码是**TPU\-MLIR 量化工具链中的权重量化优化模块**，核心采用「AdaRound 风格的量化感知学习策略」，针对模型中关键权重算子（如 MatMul）进行精细化优化。核心目标是通过学习权重量化时的 “取舍参数”（alpha），让量化后的权重输出尽可能接近浮点模型，从而减少量化精度损失。

整体逻辑围绕 “权重量化优化闭环” 展开，核心流程如下：

1. **初始化配置**：加载模型、解析 MLIR 文件、筛选待优化层（MatMul 为主）、备份原始权重和计算权重缩放因子。

2. **参数初始化**：初始化优化器（SGD）、学习率调度器、量化取舍参数（alpha），以及正则化相关参数。

3. **迭代学习**：按 epoch 迭代，对每个待优化层执行：

    - 计算原始量化（四舍五入）的基准损失；

    - 基于 alpha 的 “软量化” 生成权重，计算量化输出与浮点参考输出的 MSE 损失；

    - 结合正则化损失（鼓励 alpha 趋近于 0/1，实现硬量化），计算总损失；

    - 反向传播求解 alpha 的梯度，通过 SGD 更新 alpha；

4. **权重更新**：迭代结束后，根据学习到的 alpha 生成最终量化权重，替换原始权重并保存。

核心设计思想：通过学习 “软量化” 参数（alpha），避免传统四舍五入量化的刚性误差，在量化约束下逼近浮点性能，同时支持动量、权重衰减等优化策略提升收敛效果。

### 二、代码逐部分详细分析

1. 全局配置与工具函数

#### （1）全局常量与日志配置

```Python
LEARNING_WEIGHT_OPERATION = [
    'top.MatMul'  # 待优化的权重算子（仅MatMul，注释中预留Conv扩展）
]

log_dir = "logs/histogram"
writer = tf.summary.create_file_writer(log_dir)  # TensorBoard日志写入器
```

**功能与原理**：

- 明确待优化的核心算子（MatMul 是 Transformer、CNN 等模型的核心权重算子，量化敏感性高）；

- 初始化 TensorBoard 日志，用于记录优化过程中关键参数（如 alpha）的分布，方便调试和分析。

#### （2）日志记录函数（log\_alpha）

```Python
def log_alpha(name, alpha, step=0):
    with writer.as_default():
        tensor = tf.convert_to_tensor(alpha, dtype=tf.float32)
        tf.summary.histogram(name, tensor, step=step)  # 记录alpha的分布直方图
```

**功能与原理**：

- 将每个待优化层的 alpha 参数分布写入 TensorBoard，可实时观察 alpha 的收敛趋势（如是否逐渐趋近于 0 或 1），辅助优化策略调整。

2. SGD 优化器类（SgdWeightOpt）

封装适用于 alpha 参数更新的 SGD 优化器，支持动量、权重衰减、Nesterov 加速，适配量化参数学习的需求。

```Python
class SgdWeightOpt:
    def __init__(self, lr, momentum=0.0, nesterov=False, weight_decay=0.0):
        self.lr = lr  # 学习率调度器（LrScheduler实例）
        self.momentum = momentum  # 动量系数
        self.nesterov = nesterov  # 是否启用Nesterov加速
        self.weight_decay = weight_decay  # 权重衰减系数
        self.dampening = 0.0
        self.v = {}  # 动量缓存（键：算子名，值：动量值）
        self.grd = {}  # 梯度缓存（键：算子名，值：累积梯度）
        self.loss = {}  # 损失缓存（键：算子名，值：累积损失）

    def cal_alpha(self, iter, alpha, loss, grd, unsigned=False):
        # 核心更新公式：alpha = alpha - 学习率 * 梯度
        alpha = alpha - self.lr.cal_lr(iter) * grd
        return alpha

    def update_alpha(self, iter, op, alpha, mini_batch, unsigned=False):
        # 梯度和损失归一化（按mini-batch大小平均）
        self.grd[op] = self.grd[op]/mini_batch
        self.loss[op] = self.loss[op]/mini_batch
        
        # 权重衰减（L2正则化，防止alpha过拟合）
        if self.weight_decay != 0.0:
            self.grd[op] = self.grd[op] + alpha*self.weight_decay
        
        # 动量计算
        if self.momentum != 0.0:
            if op in self.v:
                self.v[op] = self.v[op]*self.momentum + self.grd[op]*(1.0-self.dampening)
            else:
                self.v[op] = self.grd[op]
            # Nesterov加速（提前更新梯度，提升收敛速度）
            if self.nesterov:
                self.grd[op] = self.v[op]*self.momentum + self.grd[op]
            else:
                self.grd[op] = self.v[op]
        
        # 更新alpha并裁剪到合理范围（确保rec_sig输出在[0,1]）
        alpha_new = np.clip(
            self.cal_alpha(iter, alpha, self.loss[op], self.grd[op], unsigned),
            -np.log(11),  # 对应rec_sig(alpha)=0的边界
            -np.log(1.2/1.1-1)  # 对应rec_sig(alpha)=1的边界
        )
        self.reset_grd_loss(op)  # 重置当前算子的梯度和损失缓存
        return alpha_new

    def update_loss(self, op, loss):
        # 累积mini-batch内的损失
        if op in self.loss:
            self.loss[op] = self.loss[op] + loss
        else:
            self.loss[op] = loss

    def update_grd(self, op, grd):
        # 累积mini-batch内的梯度
        if op in self.grd:
            self.grd[op] = self.grd[op] + grd
        else:
            self.grd[op] = grd

    def reset_grd_loss(self, op):
        # 重置梯度和损失（mini-batch结束后）
        del self.grd[op]
        self.loss[op] = 0.0
```

**功能与原理**：

- 适配 alpha 参数的更新需求，支持动量、权重衰减等优化策略，提升收敛稳定性；

- alpha 裁剪是关键：通过`np.clip`将 alpha 限制在特定区间，确保后续`rec_sig`函数输出在 \[0,1\]，避免量化权重超出范围；

- 支持 mini\-batch 累积梯度，提升大数据量下的优化效率。

3. 核心权重学习类（LearningAdaWeight）

封装整个权重量化优化流程，是代码的核心逻辑载体，从初始化到迭代学习再到权重保存全流程管理。

#### （1）初始化方法（**init**）

```Python
class LearningAdaWeight:
    def __init__(self, args):
        self.chip = args.chip  # 目标芯片型号（如bm1688）
        self.scales = None  # 激活量化的缩放因子（[scale, min, max]）
        self.scales4 = None  # INT4量化的缩放因子
        self.finetune_layers = []  # 待微调的算子列表
        self.finetune_layer_weights = {}  # 算子→权重名映射
        self.mlir_file = args.mlir_file  # MLIR模型文件路径
        self.module = pymlir.module()
        self.module.load(self.mlir_file)  # 加载MLIR模型
        self.parser = MlirParser(args.mlir_file)  # MLIR解析器
        self.batch_size = self.parser.get_batch_size()  # 模型批次大小
        self.input_num = self.parser.get_input_num()  # 模型输入数量
        self.mini_batch = args.mini_batch  # mini-batch大小（用于梯度累积）
        self.num_sample = 0  # 样本数量
        self.epoch = args.epoch  # 迭代epoch数
        self.ref_tensors = None  # 参考张量（浮点模型输出，来自ref_tensors类）
        self.pre_loss = {}  # 优化前的量化损失（基准）
        self.post_loss = {}  # 优化后的量化损失（结果）
        self.orig_bias = {}  # 原始偏置值备份
        self.opt = None  # 优化器（SgdWeightOpt实例）
        # AdaRound核心参数
        self.zeta = 1.1  # 量化取舍的上边界（rec_sig(alpha)最大1.1→裁剪到1）
        self.gamma = -0.1  # 量化取舍的下边界（rec_sig(alpha)最小-0.1→裁剪到0）
        self.lam = 1.0  # 损失权重系数
        self.beta_warmup = 0.2  # beta参数的预热比例（前20%迭代不启用正则化）
        self.beta_start = 20  # beta初始值（正则化强度相关）
        self.beta_end = 2  # beta最终值
        self.reg_param = 0.01  # 正则化参数
        self.orig_weights = {}  # 原始权重备份
        self.weight_file = self.parser.module_weight_file  # 权重文件路径（.npz）
        self.param_back = {}  # 优化后权重的备份
        self.weights_scales = {}  # 权重量化的缩放因子
        self.alpha = {}  # 量化取舍参数（与权重同形状）
        # 优化器参数
        self.momentum = args.momentum
        self.dampening = 0.0
        self.nesterov = args.nesterov
        self.weight_decay = args.weight_decay
        self.include_layers = self.init_include(args.quant_layers)  # 需量化的层列表（可选）
        self.compare_quanted = False  # 是否对比量化后的输出
        self.loger = None  # 日志工具
        self.support_unsigned = False  # 是否支持无符号量化

        # 筛选待优化算子、备份权重、更新权重缩放因子
        self.get_finetune_ops(args.excepts)
        self.backup_weights()
        self.update_weight_scale(args.weight_cali_table)

        # 调整mini-batch大小（适配模型批次大小）
        if self.mini_batch <= self.batch_size:
            self.mini_batch = 1
        else:
            self.mini_batch = self.mini_batch // self.batch_size

        # 加载原始权重文件到param_back
        w = np.load(self.weight_file, allow_pickle=True)
        for k in w:
            self.param_back[k] = w[k]
```

**功能与原理**：

- 初始化核心参数：包括模型加载、优化器配置、AdaRound 算法参数、权重备份路径等；

- AdaRound 关键参数：`zeta`和`gamma`定义量化取舍的软边界，`beta`系列参数控制正则化强度的退火过程，确保优化初期专注于拟合浮点输出，后期鼓励硬量化（alpha 趋近 0/1）；

- 预处理步骤：筛选待优化层、备份原始权重、初始化缩放因子，为后续学习做准备。

#### （2）待优化算子筛选（get\_finetune\_ops）

```Python
def get_finetune_ops(self, excepts):
    top_ops = {op.name: op for op in self.parser.ops}  # 所有算子的名称→算子对象映射
    exclude = excepts.split(',')  # 排除的算子列表（命令行参数）
    for op in top_ops:
        # 筛选条件：1. 属于待优化算子类型；2. 不在排除列表；3. 在需量化层列表（若指定）
        if (top_ops[op].type in LEARNING_WEIGHT_OPERATION and 
            top_ops[op].name not in exclude and 
            self.included(top_ops[op].name)):
            # 关联算子与权重名（MatMul/Conv的权重通常在opds[1]）
            if len(top_ops[op].opds) > 1 and top_ops[op].opds[1] in self.module.all_weight_names:
                self.finetune_layers.append(op)
                self.finetune_layer_weights[op] = top_ops[op].opds[1]
            # 关联算子与偏置名（若有，通常在opds[2]）
            if len(top_ops[op].opds) > 2 and top_ops[op].opds[2] in self.module.all_weight_names:
                self.finetune_layer_bias[op] = top_ops[op].opds[2]
```

**功能与原理**：

- 精准筛选需要优化的权重算子：仅保留 MatMul（可扩展 Conv），排除指定算子，确保优化聚焦于关键权重；

- 关联算子与权重 / 偏置名称：从 MLIR 解析的算子操作数（opds）中提取权重和偏置的名称，为后续权重读写做准备。

#### （3）原始权重备份与缩放因子计算（backup\_weights）

```Python
def backup_weights(self):
    for op in self.finetune_layers:
        # 备份原始权重（深拷贝，避免修改原始数据）
        self.orig_weights[op] = copy.deepcopy(self.module.get_tensor(self.finetune_layer_weights[op]))
        oc = self.orig_weights[op].shape[0]  # 输出通道数（Conv）或输出维度（MatMul）
        # 备份原始偏置（若有）
        if op in self.finetune_layer_bias:
            self.orig_bias[op] = copy.deepcopy(self.module.get_tensor(self.finetune_layer_bias[op]))
        # 计算权重量化的缩放因子（基于权重最大值）
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            # Conv权重：按输出通道计算最大值（每个通道独立缩放）
            self.weights_scales[op] = np.max(np.abs(self.orig_weights[op].reshape(oc, -1)), axis=1)
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            # MatMul权重：全局最大值（整体缩放）
            self.weights_scales[op] = np.max(np.abs(self.orig_weights[op]))
        else:
            print("不支持的算子类型！")
            sys.exit(1)
        # 避免缩放因子为0（替换为1e-8）
        self.weights_scales[op] = np.where(self.weights_scales[op] > 1e-8, self.weights_scales[op], 1e-8)
```

**功能与原理**：

- 备份原始权重：确保优化过程中可随时恢复，避免原始数据被污染；

- 计算缩放因子：权重量化的核心参数，将权重映射到量化范围（如 INT8 的 \- 128\~127），Conv 按通道独立缩放，MatMul 全局缩放，适配不同算子的特性。

#### （4）权重量化核心方法（quant\_requant\_weight）

实现基于 alpha 的 “软量化”，是 AdaRound 算法的核心。

```Python
def quant_requant_weight(self, op, bitwidth=8, hard=False):
    # 量化范围配置（INT8/INT4的有符号范围）
    if bitwidth == 8:
        qmax = 127.0
        qmin = -128.0
    elif bitwidth == 4:
        qmax = 7.0
        qmin = -7.0
    # 复制原始权重（避免修改备份）
    weight_tmp = self.orig_weights[op].copy()
    # 计算权重的量化缩放因子（权重缩放因子 / 量化最大值）
    scales = self.weights_scales[op]/qmax
    shape = weight_tmp.shape

    # 权重归一化（映射到[-qmax, qmax]范围）
    if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
        weight_tmp = (weight_tmp.reshape(shape[0], -1)/scales[:, None]).reshape(shape)
    elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
        weight_tmp = weight_tmp/scales

    # 初始化alpha参数（基于权重的小数部分）
    if op not in self.alpha:
        # 小数部分 = 权重归一化后的值 - 向下取整值
        alpha = weight_tmp - np.floor(weight_tmp)
        # AdaRound核心转换：将小数部分映射到alpha（通过sigmoid逆函数）
        alpha = -np.log((self.zeta-self.gamma)/(alpha - self.gamma)-1)
        self.alpha[op] = alpha
    else:
        alpha = self.alpha[op]

    # 软量化/硬量化生成权重
    if not hard:
        # 软量化：基于rec_sig(alpha)的连续取舍（优化阶段用）
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            weight = (np.clip(
                np.floor(weight_tmp) + self.rec_sig(alpha),  # 向下取整 + 学习到的取舍比例
                qmin, qmax
            ).reshape(shape[0], -1)*scales[:, None]).reshape(shape)
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            weight = np.clip(
                np.floor(weight_tmp) + self.rec_sig(alpha),
                qmin, qmax
            )*scales
    else:
        # 硬量化：基于alpha正负的二值取舍（优化结束后生成最终权重）
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            weight = (np.clip(
                np.floor(weight_tmp) + (alpha >= 0).astype(np.float32),  # alpha≥0→取1，否则取0
                qmin, qmax
            ).reshape(shape[0], -1)*scales[:, None]).reshape(shape)
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            weight = np.clip(
                np.floor(weight_tmp) + (alpha >= 0).astype(np.float32),
                qmin, qmax
            )*scales

    # 将量化后的权重设置到模型中
    self.module.set_tensor(self.finetune_layer_weights[op], weight)
```

**功能与原理**：

- 核心是 “软量化”：通过`rec_sig(alpha)`（sigmoid 变换）实现连续的取舍比例，而非传统量化的刚性四舍五入，为梯度下降优化提供空间；

- alpha 初始化：基于权重的小数部分，通过逆 sigmoid 变换映射到 alpha 空间，确保初始值贴近合理取舍；

- 硬量化切换：`hard=True`时，alpha 直接二值化（≥0→1，否则→0），生成最终可部署的量化权重。

#### （5）辅助函数：sigmoid 相关变换

```Python
def sigmoid(self, x):
    return expit(-x)  # expit是scipy的sigmoid实现（数值更稳定）

def rec_sig(self, x):
    # 重构sigmoid：将alpha映射到[gamma, zeta]，再裁剪到[0,1]
    return np.clip(self.sigmoid(x)*1.2-0.1, 0, 1)

def dwdalpha(self, a):
    # 计算权重对alpha的导数（用于反向传播）
    l = np.where(a <= -np.log(11), 0, 1)  # alpha下边界掩码（小于边界时导数为0）
    h = np.where(a >= -np.log(1.2/1.1-1), 0, 1)  # alpha上边界掩码（大于边界时导数为0）
    dwda = 1.2*(self.sigmoid(a)*(1-self.sigmoid(a)))  # sigmoid导数 * 系数1.2
    dwda = dwda*l*h + (1-h)  # 结合边界掩码
    return dwda
```

**功能与原理**：

- `rec_sig`：将 alpha 映射到 \[0,1\] 的取舍比例，是软量化的核心变换；

- `dwdalpha`：计算权重对 alpha 的导数，为反向传播求解 alpha 梯度提供关键支撑，同时通过边界掩码确保 alpha 在合理范围内时导数有效。

#### （6）迭代学习核心方法（learning\_one）

对单个算子执行一个 epoch 的优化，是权重学习的核心执行逻辑。

```Python
def learning_one(self, epoch, op, total):
    self.loger.logging(f"开始优化算子 {op}，当前epoch {epoch}")
    # 进度条配置（显示优化进度）
    sub_total = 1
    if epoch == 0:
        sub_total += 1  # epoch=0需计算基准损失，额外增加进度条步数
    if epoch == self.epoch - 1:
        sub_total += 1  # epoch最后需计算优化后损失，额外增加进度条步数
    pbar_detail = tqdm(np.arange(self.num_sample*sub_total))
    pbar_detail.set_description("权重优化，算子 %s" % op)

    # 量化位宽配置（bm1688芯片默认INT4，其他默认INT8）
    if self.chip == 'bm1688':
        bitwidth = 4
        qmax = 7.0
    else:
        bitwidth = 8
        qmax = 127.0

    # Step 1：计算基准损失（epoch=0时，传统四舍五入量化）
    if epoch == 0:
        self.quant_requant_weight_orig(op, bitwidth)  # 传统四舍五入量化
        for loop in np.arange(self.num_sample):
            pbar_detail.set_postfix_str(f"计算基准损失 {epoch}.{loop+1}/{self.epoch}.{self.num_sample}")
            pbar_detail.update()
            self.set_op_inputs(op, loop, bitwidth, quant=True)  # 设置输入（含激活量化）
            outputs = self.module.invoke_at(op)  # 执行到当前算子，获取输出
            # 激活反量化（若需要）
            scale = self.scales[op][0]
            unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
            if self.compare_quanted:
                if self.support_unsigned:
                    outputs = quant_requant_active(outputs, scale, unsigned, bitwidth)
                else:
                    outputs = quant_requant_active(outputs, scale, False, bitwidth)
            # 计算与浮点参考输出的MSE损失
            ref = self.ref_tensors.get(op, loop)
            if op in self.pre_loss:
                self.pre_loss[op] += cal_loss(outputs, ref)
            else:
                self.pre_loss[op] = cal_loss(outputs, ref)

    # Step 2：迭代样本优化alpha
    for loop in np.arange(self.num_sample):
        pbar_detail.set_postfix_str(f"优化中 {epoch}.{loop+1}/{self.epoch}.{self.num_sample}")
        pbar_detail.update()
        
        # 2.1 设置输入（含激活量化）
        self.set_op_inputs(op, loop, bitwidth, quant=True)
        
        # 2.2 基于当前alpha生成软量化权重
        self.quant_requant_weight(op, bitwidth)
        
        # 2.3 执行算子，获取量化输出
        outputs = self.module.invoke_at(op)
        
        # 2.4 激活量化（模拟部署时的激活量化）
        scale = self.scales[op][0]
        unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
        if self.support_unsigned:
            outputq = quant_requant_active(outputs, scale, unsigned, bitwidth)
        else:
            outputq = quant_requant_active(outputs, scale, False, bitwidth)
        
        # 2.5 获取浮点参考输出
        ref = self.ref_tensors.get(op, loop)
        
        # 2.6 计算损失（MSE损失 + 正则化损失）
        beta = self.cal_beta(epoch*self.num_sample+loop)  # 计算当前beta值（正则化强度）
        loss = cal_loss(outputq, ref)  # MSE损失
        self.loger.logging(f"算子 {op} 样本 {loop} 的MSE损失：{loss}")
        loss += self.cal_round_loss(  # 加上正则化损失
            epoch*self.num_sample+loop, self.alpha[op], beta
        )
        self.loger.logging(f"算子 {op} 样本 {loop} 的总损失：{loss}")
        
        # 2.7 累积损失
        self.opt.update_loss(op, loss)
        
        # 2.8 计算输出梯度（量化输出 - 参考输出）
        grd_dst = self.cal_grd(outputs, ref)
        
        # 2.9 计算权重梯度（根据算子类型选择不同方法）
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            # Conv权重梯度：通过pymlir的backward_weight_at计算
            grd_w = self.module.backward_weight_at(op, self.finetune_layer_weights[op], grd_dst)
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            # MatMul权重梯度：通过输入与输出梯度的矩阵乘法计算
            input = self.op_first_input(op, loop, bitwidth)  # 获取MatMul的输入
            shape = input.shape
            batch = outputs.shape[0]
            # 输入形状调整（适配矩阵乘法）
            if len(shape) == 2:
                input = input.reshape(-1, shape[-1]).transpose()
            elif len(shape) == 3:
                input = input.transpose(0, 2, 1)
            if len(grd_dst.shape) == 1:
                grd_dst = grd_dst.reshape(1, grd_dst.shape[0])
            # 梯度计算：input^T * grd_dst
            grd_w = np.matmul(input, grd_dst)
            # 批次平均（3D输入时）
            if len(shape) == 3:
                grd_w = np.average(grd_w, axis=0)
        
        # 2.10 计算alpha的梯度（权重梯度 * 权重对alpha的导数 + 正则化梯度）
        shape = self.alpha[op].shape
        grd_w1 = self.dwdalpha(self.alpha[op])  # 权重对alpha的导数
        # 导数缩放（适配权重的量化缩放因子）
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            grd_w1 = (grd_w1.reshape(shape[0], -1)*((self.weights_scales[op]/qmax)[:, None])).reshape(shape)
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            grd_w1 = grd_w1*(self.weights_scales[op]/qmax)
        # 总梯度 = 权重梯度*导数 + 正则化梯度
        grd_w = grd_w * grd_w1
        grd_r = self.cal_grdr(self.alpha[op], beta, epoch*self.num_sample+loop, grd_w1)  # 正则化梯度
        grd = grd_w + grd_r
        
        # 2.11 累积梯度
        self.opt.update_grd(op, grd)
        
        # 2.12 达到mini-batch大小，更新alpha
        if (epoch*self.num_sample+loop+1) % self.mini_batch == 0:
            self.alpha[op] = self.opt.update_alpha(
                epoch*self.num_sample+loop, op, self.alpha[op], self.mini_batch
            )
            log_alpha(op, self.alpha[op].copy(), loop)  # 记录alpha分布

    # Step 3：计算优化后损失（epoch最后一轮）
    if epoch == self.epoch-1:
        self.quant_requant_weight(op, bitwidth, True)  # 硬量化（基于学习到的alpha）
        for loop in np.arange(self.num_sample):
            pbar_detail.set_postfix_str(f"验证优化效果 {epoch}.{loop+1}/{self.epoch}.{self.num_sample}")
            pbar_detail.update()
            self.set_op_inputs(op, loop, bitwidth, quant=True)
            self.module.invoke_at(op)
            outputs = self.module.get_tensor(op)
            # 激活反量化
            scale = self.scales[op][0]
            unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
            if self.compare_quanted:
                if self.support_unsigned:
                    outputs = quant_requant_active(outputs, scale, unsigned, bitwidth)
                else:
                    outputs = quant_requant_active(outputs, scale, False, bitwidth)
            # 计算优化后损失
            ref = self.ref_tensors.get(op, loop)
            if op in self.post_loss:
                self.post_loss[op] += cal_loss(outputs, ref)
            else:
                self.post_loss[op] = cal_loss(outputs, ref)
        # 恢复原始权重（避免影响其他算子）
        self.restore_weight(op)
        
        # 保存优化后的权重（无论损失是否降低，此处强制保存，可根据需求调整）
        self.loger.logging(f'{op} 优化后损失：{self.post_loss[op]}，基准损失：{self.pre_loss[op]}')
        print(f'{op} 优化后损失：{self.post_loss[op]}，基准损失：{self.pre_loss[op]}')
        self.update_weight(op, bitwidth)  # 更新权重到param_back
```

**功能与原理**：

- 三阶段流程：基准损失计算→alpha 迭代优化→优化后损失验证，形成完整的优化闭环；

- 损失计算：结合 MSE 损失（拟合浮点输出）和正则化损失（鼓励硬量化），平衡精度和部署可行性；

- 梯度计算：通过反向传播求解 alpha 的梯度，同时适配 Conv 和 MatMul 的不同梯度计算逻辑，确保优化方向正确；

- mini\-batch 梯度累积：提升大数据量下的优化效率，避免单样本梯度波动过大。

#### （7）权重更新与保存（update\_weight、save\_weights）

```Python
def adjust_weight(self, op, bitwidth=8):
    # 根据学习到的alpha生成最终量化权重（硬量化）
    if bitwidth == 8:
        qmax = 127.0
        qmin = -128.0
    elif bitwidth == 4:
        qmax = 7.0
        qmin = -7.0
    shape = self.orig_weights[op].shape
    w_reshape = self.orig_weights[op].reshape(shape[0], -1)
    p = self.alpha[op].reshape(shape[0], -1)  # alpha reshape为与权重相同的2D形状（按通道）
    
    # 硬量化：alpha≥0→向上取整（+1），否则向下取整（+0）
    if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
        weight = (np.clip(
            (np.floor(w_reshape/((self.weights_scales[op]/qmax)[:, None])) + 
             np.where(p >= 0, 1.0, 0.0).astype(np.float32)),
            qmin, qmax
        ) * ((self.weights_scales[op]/qmax)[:, None])).reshape(shape)
    elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
        weight = (np.clip(
            (np.floor(w_reshape/(self.weights_scales[op]/qmax)) + 
             np.where(p >= 0, 1.0, 0.0).astype(np.float32)),
            qmin, qmax
        ) * (self.weights_scales[op]/qmax)).reshape(shape)
    else:
        print("不支持的算子类型！")
        sys.exit(1)
    return weight

def update_weight(self, op, bitwidth=8):
    # 将优化后的权重更新到param_back（最终保存用）
    self.param_back[self.finetune_layer_weights[op]] = self.adjust_weight(op, bitwidth)

def save_weights(self):
    # 备份原始权重文件，保存优化后的权重
    os.rename(self.weight_file, self.weight_file.replace(".npz", ".bak.npz"))
    np.savez(self.weight_file, **self.param_back)
```

**功能与原理**：

- `adjust_weight`：基于学习到的 alpha 执行硬量化，生成最终可部署的量化权重；

- `save_weights`：备份原始权重文件（后缀改为\.bak\.npz），将优化后的权重保存到原始路径，覆盖旧权重，确保后续量化流程使用优化后的权重。

#### （8）整体学习流程（learning）

```Python
def learning(self):
    total = len(self.finetune_layers)
    # 按epoch迭代，逐个优化待优化层
    for epoch in np.arange(self.epoch):
        for l in self.finetune_layers:
            self.learning_one(epoch, l, total)
        print(f"\n=================================================")
        print(f"  完成epoch {epoch}，已优化 {total} 个层")
        print(f"=================================================\n")
    # 保存所有优化后的权重
    self.save_weights()
```

**功能与原理**：

- 按 epoch 迭代，逐个处理待优化层，确保每个层都完成指定次数的 alpha 学习；

- 迭代结束后统一保存权重，避免中间过程中保存导致的文件损坏。

### 三、总结

该代码是 TPU\-MLIR 工具链中针对权重量化的高阶优化模块，核心价值是通过「AdaRound 风格的软量化学习」解决传统量化（四舍五入）的精度损失问题。核心亮点如下：

1. **精准优化目标**：聚焦 MatMul（可扩展 Conv）等核心权重算子，针对性提升量化精度；

2. **灵活的优化策略**：支持 SGD \+ 动量、权重衰减、Nesterov 加速，适配不同模型的优化需求；

3. **正则化退火**：通过 beta 参数的余弦退火，平衡 “拟合浮点输出” 和 “硬量化约束”；

4. **工程化适配**：支持 INT4/INT8 量化、Conv/MatMul 算子差异处理、mini\-batch 梯度累积，适配实际部署场景；

5. **可追溯性**：通过 TensorBoard 记录 alpha 分布、日志记录损失变化，便于调试和优化策略调整。

整体流程从初始化、权重备份、alpha 学习到权重保存，形成完整的量化优化闭环，最终输出优化后的权重文件，可直接用于后续的模型量化部署。







## gptq\.py

### 一、GPTQ 核心原理回顾

GPTQ 的核心目标是解决 “低比特量化导致的精度损失”，核心思路是：

1. 量化误差不是独立的，需考虑输入特征对输出的影响（用输入协方差矩阵 H 衡量）；

2. 通过**分块优化**降低高维权重矩阵的计算复杂度；

3. 量化时实时修正后续权重，抵消当前量化误差的传播；

4. 用矩阵分解（Cholesky）加速误差优化，降低计算成本。

代码的所有核心模块均围绕这一原理设计，下面结合代码逐模块拆解。

### 二、核心类与初始化：量化前的准备工作

代码通过两个核心类分工：`GptqQuantizer`负责 “量化参数计算 \+ 量化 / 反量化操作”，`LearningGptqWeight`负责 “整体流程控制 \+ GPTQ 核心逻辑执行”。

1. 初始化核心流程（LearningGptqWeight\.**init**）

- 加载依赖：通过`pymlir`加载 MLIR 模型和权重文件，解析模型输入维度、批量大小等关键信息；

- 筛选待量化层：仅保留`LEARNING_WEIGHT_OPERATION`中指定的层（默认`top.MatMul`，支持扩展`top.Conv`），排除用户指定的例外层（`args.excepts`）；

- 备份原始权重：为后续 “量化前后损失对比” 和 “权重恢复” 做准备，避免原始权重被覆盖；

- 初始化关键参数：量化比特数、blocksize（分块大小）、groupsize（分组量化大小）、迭代轮数（epoch）等，适配不同量化需求。

2. 待量化层筛选逻辑（get\_finetune\_ops）

- 仅选择 “有可训练权重” 的层：要求层的第二个输入（`opds[1]`）是模型权重（在`module.all_weight_names`中）；

- 特殊层过滤：Conv 层排除分组卷积（`group>1`），避免量化逻辑不兼容；

- 支持用户自定义排除：通过`excepts`参数传入需跳过的层名，提升灵活性。

### 三、量化参数计算：GptqQuantizer 类的核心作用

`GptqQuantizer`是量化的 “基础工具类”，负责计算量化所需的**尺度（scale）** 和**零点（zero）**，对应 GPTQ “量化参数自适应” 的需求。

1. 核心方法：find\_params（量化参数计算）

- 维度适配：根据张量类型（权重 / 激活）和维度（2D/3D/4D）调整输入形状，确保量化参数计算维度正确；

    - 权重（weight=True）：按输出通道分组（`reshape(shape[0], -1)`），支持 “按通道量化（perchannel=True）”；

    - 激活（weight=False）：根据维度（Conv 的 4D/MatMul 的 2D）调整转置 / 展平方式，确保统计范围准确。

- 量化范围计算（对称量化）：

    - 取张量极值的最大绝对值（`xmax = torch.maximum(torch.abs(xmin), xmax)`），保证对称量化的 “正负范围均衡”；

    - 处理零值异常：若张量全为 0，强制设置范围为`[-1, 1]`，避免`scale`为 0 导致的计算错误。

- 尺度与零点计算：

    - 尺度（scale）：`(xmax - xmin) / maxq`（`maxq=2^bits-1`，量化后最大整数）；

    - 零点（zero）：对称量化时固定为`(maxq+1)/2`（确保 0 值量化后居中），非对称量化时为`round(-xmin/scale)`。

- 可选 MSE 优化（代码中默认关闭，支持开启）：通过网格搜索调整`xmin/xmax`，找到使量化 MSE 损失最小的`scale`和`zero`，进一步降低误差。

2. 量化 / 反量化操作（quantize 方法）

- 量化逻辑：`q = clip(round(x/scale) + zero, 0, maxq)`，将浮点数映射到整数区间；

- 反量化逻辑：`x = scale * (q - zero)`，确保量化可逆（用于后续误差计算）；

- 核心作用：为 GPTQ 的 “误差优化” 提供基础 —— 先量化得到初始整数权重，再通过微调修正误差。

### 四、GPTQ 核心逻辑：权重量化与误差优化（quant\_requant\_weight）

这是代码最核心的方法，完全对应 GPTQ 的 “分块优化 \+ 误差抵消” 原理，步骤拆解如下：

3. 权重与输入统计量预处理

- 权重适配：

    - MatMul 层：权重转置（`W = W.t()`），适配输入特征维度（输入 × 权重 = 输出）；

    - Conv 层：权重展平为 2D（`reshape(shape[0], -1)`），按输出通道分组量化。

- 输入统计量 H 处理：

    - H 是输入特征的协方差矩阵（通过`update_H`方法计算），衡量 “输入特征对输出的影响程度”；

    - 处理零对角线（`H[dead, dead] = 1`）：避免后续矩阵求逆报错；

    - 可选输入排序（actorder=True）：按 H 的对角线值排序（值越大影响越大），优先量化关键输入对应的权重，提升精度。

4. 矩阵分解加速误差计算

- 添加阻尼项（damp）：`H[diag, diag] += percdamp * mean(diag(H))`，避免 H 矩阵奇异（不可逆），保证数值稳定性；

- Cholesky 分解：通过`torch.linalg.cholesky`将 H 分解为下三角矩阵，再求逆后分解为上三角矩阵（`Hinv`），大幅降低后续误差优化的计算复杂度（GPTQ 的核心优化点）。

- 只有满足两个条件的矩阵才能做 Cholesky 分解：

**对称矩阵**：矩阵的第 i 行第 j 列元素 = 第 j 行第 i 列元素（即 A = Aᵀ）；

**正定矩阵**：对于任意非零向量 x，都满足 xᵀAx \> 0（简单说：矩阵的 “能量” 恒正，对角线元素全为正，且行列式也为正）。

而 GPTQ 中的 H 矩阵（输入协方差矩阵 H = E \[Xᵀ×X\]）恰好满足这两个条件：

对称性：协方差矩阵的本质就是对称的（Xᵢ和 Xⱼ的协方差 = Xⱼ和 Xᵢ的协方差）；

正定性：协方差矩阵是 “能量矩阵”，只要输入特征不是完全线性相关，就是正定的（GPTQ 加阻尼项也是为了确保这一点）。

如果矩阵 A 是 **正定对称矩阵**，则存在唯一的 **下三角矩阵 L**（对角线元素全为正），使得：\\\(A = L \\times L^T\\\)其中：

L：下三角矩阵（对角线以下有元素，对角线以上全为 0，且对角线元素 \> 0）；

Lᵀ：L 的转置矩阵（上三角矩阵，对角线以上有元素，对角线以下全为 0）。

GPTQ 中需要 Hinv = A⁻¹（A=H），流程如下：

对 H 做 Cholesky 分解：H = L × Lᵀ；

求 L 的逆矩阵 L⁻¹（下三角矩阵求逆比普通矩阵简单得多）；

H 的逆矩阵 = \(Lᵀ\)⁻¹ × L⁻¹（因为 \(L×Lᵀ\)⁻¹ = \(Lᵀ\)⁻¹ × L⁻¹，矩阵求逆的性质）。

简单说：Cholesky 分解把 “直接求复杂矩阵的逆”，转化为 “求两个简单三角矩阵的逆”，运算效率大幅提升，这对 GPTQ 处理高维权重矩阵（比如 Transformer 层的 MatMul 权重）至关重要 —— 否则量化速度会慢到无法实用。

5. 分块量化 \+ 误差抵消（GPTQ 核心）

按`blocksize`（默认 128）将权重矩阵列分块，逐块优化，避免高维矩阵直接计算：

- 取当前块权重`W1 = W[:, i1:i2]`，初始化量化权重`Q1`和误差`Err1`；

- 逐列量化权重：

    - 对当前列权重`w`，用`GptqQuantizer.quantize`得到量化值`q`；

    - 计算量化误差`err1 = (w - q) / d`（`d`是`Hinv`的对角线元素，对应输入影响程度）；

    - 修正后续权重：`W1[:, i:] -= err1.unsqueeze(1) @ Hinv1[i, i:]`，抵消当前误差对后续列的影响（GPTQ “误差传播抵消” 的核心）；

- 块间误差修正：`W[:, i2:] -= Err1 @ Hinv[i1:i2, i2:]`，确保块间误差不累积。

1. 权重恢复与更新

- 量化后权重恢复为原始维度（MatMul 层转置回原形状，Conv 层展平回 4D）；

- 更新权重到模型和备份字典（`param_back`），为后续保存做准备。

### 五、输入统计量计算：update\_H 方法（GPTQ 的 “误差影响因子”）

GPTQ 的关键创新是 “考虑输入对量化误差的影响”，而`H`矩阵正是输入特征的协方差矩阵，用于衡量 “每个输入特征对输出的贡献度”。

2. H 矩阵的物理意义

`H = E[X^T × X]`（X 是输入特征），对角线元素越大，对应输入特征对输出的影响越大，量化误差需更严格控制。

3. 代码实现逻辑

- 输入预处理：

    - Conv 层：用`torch.nn.Unfold`展开卷积核对应的局部输入区域（适配卷积计算逻辑）；

    - MatMul 层：将输入展平为 2D 张量（`reshape(-1, shape[-1]).transpose()`），适配矩阵乘法逻辑；

- 增量更新：按批次累加输入的外积（`inp @ inp.transpose()`），并用样本数归一化（`np.sqrt(2/self.samples[op])`），确保 H 反映所有校准样本的统计特性；

- 初始化：首次计算时，按权重维度初始化 H 为零矩阵，后续批次增量更新。

### 六、迭代微调与损失验证：learning 方法（确保量化精度）

GPTQ 不是 “一次性量化”，而是 “量化 \+ 微调” 的迭代过程，代码通过多轮 epoch 实现：

4. 单轮迭代流程（learning\_one 方法）

- epoch=0：计算基准损失（原始量化误差）；

    - 用简单对称量化（`quant_requant_weight_orig`）处理权重，计算量化后模型输出与原始模型输出的损失（`pre_loss`），作为后续对比的基准；

    - 同时累积输入统计量 H，为后续微调做准备。

- 中间 epoch：迭代优化量化误差；

    - 基于当前 H 矩阵，调用`quant_requant_weight`进行分块量化 \+ 误差抵消；

    - 每轮迭代更新 H 矩阵，确保量化参数适配所有样本。

- epoch = 最后一轮：验证优化效果；

    - 用优化后的量化权重计算损失（`post_loss`），与基准损失`pre_loss`对比；

    - 若`post_loss ≤ pre_loss`，保留量化权重；否则恢复原始权重（避免精度下降）。

5. 硬件适配与量化比特控制

- 支持`bm1688`芯片的 4bit 量化（输入 / 权重 4bit，输出 8bit），其他芯片默认 8bit 量化；

- 通过`quant_requant_active`函数处理激活值的量化 / 反量化，与权重量化配合，实现端到端量化。

### 七、最终输出：权重保存与部署适配

- 备份原始权重：将原始权重文件重命名为`.bak.npz`，避免覆盖；

- 保存量化权重：将优化后的量化权重保存为新的`.npz`文件，直接适配 TPU\-MLIR 框架的模型部署；

- 灵活性：支持用户调整`blocksize`（分块大小）、`groupsize`（分组量化大小）、`percdamp`（阻尼系数）等参数，适配不同模型的量化需求。



## lapq\.py

### 一、代码整体原理逻辑阐述

该代码是**TPU\-MLIR 量化工具链中的损失感知量化（Loss\-Aware Post\-Training Quantization, LAPQ）模块**，核心目标是针对 INT4 低精度量化场景，通过优化权重和激活的量化阈值，最小化量化带来的精度损失。整体逻辑围绕 “阈值优化 \- 量化评估 \- 迭代调优” 闭环展开，核心流程如下：

1. **初始化配置**：加载模型、解析 MLIR 文件、筛选待优化层（仅 MatMul）、备份原始权重和量化阈值（初始为传统 absmax 阈值）。

2. **初始阈值优化**：基于 Lp 范数最小化量化失真，为每个权重和激活层计算初始最优阈值（scale），减少量化带来的信号失真。

3. **样本划分**：将数据集分为校准样本（用于阈值优化）和评估样本（用于损失计算）。

4. **数值迭代优化**：通过 Powell 数值优化算法，迭代调整所有待优化层的阈值，以 “1 \- 余弦相似度” 为损失函数，持续降低量化模型与浮点模型的输出差异。

5. **结果生成**：优化结束后，生成调整后的量化校准表和权重文件，用于后续 INT4 量化部署。

核心设计思想：INT4 量化精度损失远大于 INT8，传统 absmax 阈值无法适配复杂模型，因此通过 “Lp 范数初始优化 \+ 数值迭代调优”，同时优化权重和激活的量化阈值，在低精度约束下最大化精度保留。

### 二、代码逐部分详细分析

1. 全局配置与常量定义

```Python
LAPQ_OPERATION = [
    'top.MatMul'  # 待优化的核心算子（仅MatMul，注释预留Conv扩展）
]
```

**功能与原理**：

- 明确 LAPQ 优化的目标算子为 MatMul，这类算子是 Transformer、CNN 等模型的核心权重载体，INT4 量化对其精度影响最大，是优化的关键靶点。

- 排除分组卷积（后续`get_finetune_ops`中处理），因分组卷积的权重结构特殊，暂不支持 LAPQ 优化。

2. 核心类（LossAwareQuant）初始化（**init**）

```Python
class LossAwareQuant:
    def __init__(self, cali_table, args):
        self.cali_table = copy.deepcopy(cali_table)  # 原始校准表（含初始阈值）
        self.scales = cali_table.table.copy()  # INT8激活量化阈值（[scale, min, max]）
        self.scales4 = cali_table.table4.copy()  # INT4激活量化阈值
        self.orig_scales4 = {}  # 原始INT4激活阈值备份
        self.trying_scales4 = {}  # 待尝试的INT4激活阈值（优化过程中使用）
        self.finetune_layers = []  # 待优化的算子列表
        self.point_idx = {}  # 阈值数组索引映射（算子名→数组下标，适配数值优化）
        self.finetune_layer_weights = {}  # 算子→权重名映射
        self.finetune_layer_bias = {}  # 算子→偏置名映射
        self.finetune_layer_weight_orig_ths = {}  # 权重原始阈值（absmax）
        self.finetune_layer_weight_best_ths = {}  # 权重最优阈值（优化后）
        self.mlir_file = args.mlir_file  # MLIR模型路径
        self.module = pymlir.module()
        self.module.load(self.mlir_file)  # 加载MLIR模型
        self.parser = MlirParser(args.mlir_file)  # MLIR解析器
        self.calibration_table = args.calibration_table  # 校准表路径
        self.qtable = args.qtable  # 量化配置表路径
        self.chip = args.chip  # 目标芯片型号
        self.batch_size = self.parser.get_batch_size()  # 模型批次大小
        self.input_num = self.parser.get_input_num()  # 模型输入数量
        self.num_sample = 0  # 样本数量
        self.search_loop = 2  # 数值优化迭代次数
        self.ref_tensors = {None}  # 参考张量（浮点模型输出，来自ref_tensors类）
        self.orig_weights = {}  # 原始权重备份
        self.orig_bias = {}  # 原始偏置备份
        self.weight_file = self.parser.module_weight_file  # 权重文件路径（.npz）
        self.param_back = {}  # 优化后权重备份
        self.weights_scales = {}  # 权重量化缩放因子（初始为absmax）
        self.compare_quanted = True  # 对比量化后的输出（而非浮点输出）
        self.loger = None  # 日志工具
        self.include_layers = self.init_include(args.quant_layers)  # 需量化的层列表（可选）
        self.support_unsigned = False  # 是否支持无符号量化

        # 核心初始化步骤
        self.get_finetune_ops(args.excepts)  # 筛选待优化算子
        self.backup_weights()  # 备份原始权重和初始阈值
        # 加载原始权重文件，初始化权重阈值（absmax：权重绝对值的最大值）
        w = np.load(self.weight_file, allow_pickle=True)
        for k in w:
            self.param_back[k] = w[k]
            for kk in self.finetune_layer_weights:
                if k == self.finetune_layer_weights[kk]:
                    th = np.max(np.abs(w[k]))  # 传统absmax阈值
                    self.finetune_layer_weight_orig_ths[k] = th
                    self.finetune_layer_weight_best_ths[k] = th  # 初始最优阈值=原始阈值
```

**功能与原理**：

- 初始化核心参数：涵盖模型加载、校准表解析、权重备份、阈值初始化等，为后续优化奠定基础。

- 阈值初始化：权重初始阈值采用传统 absmax 方法（权重绝对值的最大值），激活阈值来自输入校准表，后续将通过 Lp 范数和数值优化调整。

- 关键映射准备：`point_idx`用于将分散的算子阈值映射为一维数组，适配数值优化算法（需接收一维参数）。

3. 辅助筛选与备份方法

#### （1）层筛选方法（included、init\_include）

```Python
def included(self, op):
    # 判断算子是否需要纳入优化（基于include_layers列表）
    if len(self.include_layers) == 0:
        return True
    elif op in self.include_layers:
        return True
    else:
        return False

def init_include(self, quant_layers):
    # 从文件加载需量化的层列表（若指定）
    if quant_layers == "":
        return []
    f = open(quant_layers, 'r')
    layers = []
    for line in f:
        if len(line) > 0 and (not line.startswith("#")):
            layers.append(line.strip())
    return layers
```

**功能与原理**：

- 支持自定义筛选需优化的层，通过`quant_layers`参数指定层列表文件，未指定则默认优化所有符合条件的 MatMul 算子。

- 灵活适配不同模型的优化需求，可针对性优化关键层，提升效率。

#### （2）待优化算子筛选（get\_finetune\_ops）

```Python
def get_finetune_ops(self, excepts):
    top_ops = {op.name: op for op in self.parser.ops}  # 算子名→算子对象映射
    exclude = excepts.split(',')  # 排除的算子列表（命令行参数）
    for op in top_ops:
        # 筛选条件：1. 属于LAPQ_OPERATION；2. 不在排除列表；3. 在需量化层列表；4. 非分组卷积（若为Conv）
        if (top_ops[op].type in LAPQ_OPERATION and 
            top_ops[op].name not in exclude and 
            self.included(top_ops[op].name)):
            if top_ops[op].type == 'top.Conv':
                if int(top_ops[op].attrs['group'].split(':')[0]) > 1:
                    continue  # 排除分组卷积
            # 关联算子与权重名（MatMul的权重通常在opds[1]）
            if len(top_ops[op].opds) > 1 and top_ops[op].opds[1] in self.module.all_weight_names:
                self.finetune_layers.append(op)
                self.finetune_layer_weights[op] = top_ops[op].opds[1]
            # 关联算子与偏置名（若有，在opds[2]）
            if len(top_ops[op].opds) > 2 and top_ops[op].opds[2] in self.module.all_weight_names:
                self.finetune_layer_bias[op] = top_ops[op].opds[2]
```

**功能与原理**：

- 精准筛选待优化算子：聚焦 MatMul，排除分组卷积和指定排除的算子，确保优化聚焦于核心权重层。

- 关联权重 / 偏置名：为后续权重量化、阈值优化提供算子与权重的映射关系。

#### （3）权重备份与初始缩放因子计算（backup\_weights）

```Python
def backup_weights(self):
    for op in self.finetune_layers:
        # 备份原始权重和偏置
        self.orig_weights[op] = copy.deepcopy(self.module.get_tensor(self.finetune_layer_weights[op]))
        if op in self.finetune_layer_bias:
            self.orig_bias[op] = copy.deepcopy(self.module.get_tensor(self.finetune_layer_bias[op]))
        oc = self.orig_weights[op].shape[0]  # 输出通道数（Conv）或输出维度（MatMul）
        # 计算初始权重缩放因子（absmax方法）
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            # Conv权重：按输出通道计算absmax（每个通道独立缩放）
            self.weights_scales[op] = np.max(np.abs(self.orig_weights[op].reshape(oc, -1)), axis=1)
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            # MatMul权重：全局absmax（整体缩放）
            self.weights_scales[op] = np.max(np.abs(self.orig_weights[op]))
        else:
            print("不支持的算子类型！")
            sys.exit(1)
        # 避免缩放因子为0（替换为1e-8）
        self.weights_scales[op] = np.where(self.weights_scales[op] > 1e-8, self.weights_scales[op], 1e-8)
```

**功能与原理**：

- 备份原始权重：确保优化过程中可随时恢复，避免原始数据污染。

- 初始缩放因子计算：采用传统 absmax 方法，为后续 Lp 范数优化提供初始值，保证优化的稳定性。

4. 量化核心方法

#### （1）权重与激活量化（quant\_weight、quant\_active、quant\_tensor）

```Python
def quant_weight(self, op, scale, bitwidth=8):
    # 权重量化（基于给定阈值scale）
    tmpweight = self.orig_weights[op].copy()
    step = scale / (2**(bitwidth-1)-1)  # 量化步长（scale映射到量化范围）
    tmpweight = np.round(tmpweight/step)  # 量化：浮点→整数
    # 裁剪范围：INT4时为-6~7（避免权重对称量化溢出），INT8时为-127~127
    tmpweight = np.clip(tmpweight, -2**(bitwidth-1)+1, 2**(bitwidth-1)-1)
    tmpweight = tmpweight*step  # 反量化：整数→浮点（模拟量化后权重）
    self.module.set_tensor(self.finetune_layer_weights[op], tmpweight)

def quant_active(self, active_name, scale, sample_idx, bitwidth=8):
    # 激活量化（基于给定阈值scale）
    tmpactive = self.ref_tensors.get(active_name, sample_idx).copy()
    step = scale / (2**(bitwidth-1)-1)
    tmpactive = np.round(tmpactive/step)
    # 激活裁剪范围：INT4为-7~7，INT8为-128~127（与权重不同，激活支持全范围）
    tmpactive = np.clip(tmpactive, -2**(bitwidth-1), 2**(bitwidth-1)-1)
    tmpactive = tmpactive*step
    self.module.set_tensor(active_name, tmpactive)

def quant_tensor(self, tensor, scale, is_weight, bit_width=8):
    # 通用量化函数（统一处理权重和激活）
    tmp = tensor.copy()
    step = scale / (2**(bit_width-1)-1)
    qmax = 2**(bit_width-1) - 1  # 量化最大值（INT4:7，INT8:127）
    qmin = -qmax  # 量化最小值（INT4:-7，INT8:-127）
    if is_weight:
        qmin = -qmax + 1  # 权重最小值偏移（INT4:-6，INT8:-126），避免对称溢出
    tmp = np.round(tmp/step)  # 量化
    tmp = np.clip(tmp, qmin, qmax)  # 裁剪
    tmp = tmp*step  # 反量化
    return tmp
```

**功能与原理**：

- 核心是 “量化 \- 裁剪 \- 反量化” 流程，模拟低精度量化后的张量状态，用于计算量化失真和模型输出损失。

- 权重与激活的量化范围差异：权重偏移最小值是为了避免对称量化时，因权重分布特性导致的溢出风险，提升量化稳定性。

#### （2）Lp 范数相关方法（量化失真优化核心）

```Python
def lpnorm_loss(self, pred, ref, p):
    # 计算Lp损失（衡量pred与ref的差异，p为范数阶数）
    return np.mean(np.abs(pred.flatten()-ref.flatten())**p)

def lpnorm_quant_loss(self, tensor, scale, p, is_weight, bit_width=8):
    # 计算量化后的Lp损失（量化失真）
    tmp = self.quant_tensor(tensor, scale, is_weight, bit_width)  # 量化后的张量
    return self.lpnorm_loss(tensor, tmp, p)  # 原始张量与量化张量的Lp损失

def lpnorm_quant(self, tensor, p, is_weight, bit_width=8):
    # 基于Lp损失最小化，求解最优量化阈值（scale）
    # 使用scipy的一维数值优化，最小化lpnorm_quant_loss
    opt_scale = opt.minimize_scalar(
        lambda scale: self.lpnorm_quant_loss(tensor, scale, p, is_weight, bit_width),
        bounds=(0, np.max(np.abs(tensor)))  # scale范围：0~张量最大绝对值
    ).x
    return opt_scale
```

**功能与原理**：

- Lp 范数是量化失真的核心度量：p=2 时为 MSE 损失，p=1 时为 MAE 损失，可通过调整 p 适配不同张量的分布特性。

- `lpnorm_quant`是初始阈值优化的关键：通过数值优化找到使量化失真最小的阈值，比传统 absmax 更精准，尤其适用于 INT4 低精度场景。

5. 模型输入设置与参考张量管理

```Python
def set_op_inputs(self, op, loop, bitwidth=8, quant=True):
    # 为算子设置输入（含激活量化）
    pre_ops = self.parser.get_pre_op_by_op_name(op)  # 获取前序输入算子
    for pop in pre_ops:
        shape = self.ref_tensors.get(pop, loop).shape  # 输入张量形状
        # 处理Reshape算子（Reshape的输入为实际数据算子）
        if pop not in self.module.all_tensor_names:
            if self.parser.get_op_type_by_op_name(pop) == 'top.Reshape':
                pre_pre_ops = self.parser.get_pre_op_by_op_name(pop)
                pop = pre_pre_ops[0]
            else:
                print(f"{op}输入{pop}不在张量列表中")
                sys.exit(1)
        # 获取参考输入张量并reshape
        d = self.ref_tensors.get(pop, loop).reshape(shape)
        # 选择对应位宽的激活阈值
        if bitwidth == 4:
            scale = self.scales4[pop][0]
            unsigned = self.scales4[op][1] >= 0 and self.scales4[op][2] >= 0
        else:
            scale = self.scales[pop][0]
            unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
        # 激活量化（若开启）
        if scale != 1.0 and quant:
            if self.support_unsigned:
                d = quant_requant_active(d, scale, unsigned, bits=bitwidth)
            else:
                d = quant_requant_active(d, scale, False, bits=bitwidth)
        # 设置输入张量到模型
        self.module.set_tensor(pop, d)

def get_op_input0(self, op, loop, bitwidth=8, quanted=False):
    # 获取算子的第一个输入（含激活量化，用于梯度计算或单独处理）
    pre_ops = self.parser.get_pre_op_by_op_name(op)
    for pop in pre_ops:
        shape = self.ref_tensors.get(pop, loop).shape
        if pop not in self.module.all_tensor_names:
            if self.parser.get_op_type_by_op_name(pop) == 'top.Reshape':
                pre_pre_ops = self.parser.get_pre_op_by_op_name(pop)
                pop = pre_pre_ops[0]
            else:
                print(f"{op}输入{pop}不在张量列表中")
                sys.exit(1)
        d = self.ref_tensors.get(pop, loop).reshape(shape).copy()
        # 激活量化（若开启）
        if bitwidth == 4:
            scale = self.scales4[pop][0]
            unsigned = self.scales4[op][1] >= 0 and self.scales4[op][2] >= 0
        else:
            scale = self.scales[pop][0]
            unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
        if scale != 1.0 and quanted:
            if self.support_unsigned:
                d = quant_requant_active(d, scale, unsigned, bits=bitwidth)
            else:
                d = quant_requant_active(d, scale, False, bits=bitwidth)
        return d
```

**功能与原理**：

- 确保模型输入符合量化后的状态：激活按当前优化的阈值进行量化，模拟实际部署时的输入，保证损失计算的准确性。

- 处理 Reshape 算子特殊情况：Reshape 仅改变张量形状，实际输入数据来自其前序算子，需递归获取，避免输入错误。

6. 阈值优化核心流程方法

#### （1）单次阈值尝试（try\_one\_q）

```Python
def try_one_q(self, p, cali_samples, eval_samples):
    # 针对给定的p值（Lp范数阶数），计算初始最优阈值并评估损失
    self.backup_weight_file()  # 备份原始权重文件
    self.scales4 = self.cali_table.table4.copy()  # 重置INT4激活阈值
    
    # 为每个待优化层计算初始最优阈值（基于Lp范数）
    for op in self.finetune_layers:
        for opd in [0, 1]:  # 遍历算子的两个操作数（0：激活输入，1：权重）
            opdname = self.parser.get_opds_by_op_name(op)[opd]
            if opdname in self.finetune_layer_weights[op]:  # 操作数为权重
                tmp = self.orig_weights[op].copy()
                # 计算权重的Lp最优阈值
                opt_th = self.lpnorm_quant(tmp, p, True, 4)
                self.finetune_layer_weight_best_ths[opdname] = opt_th
            else:  # 操作数为激活
                # 拼接所有校准样本的激活数据（提升阈值估计精度）
                tmpact = self.ref_tensors.get(opdname, cali_samples[0])
                for s in cali_samples[1:]:
                    tmpact = np.concatenate((tmpact, self.ref_tensors.get(opdname, s)))
                # 计算激活的Lp最优阈值
                opt_th = self.lpnorm_quant(tmpact, p, False, 4)
                self.trying_scales4[opdname] = opt_th
    
    # 生成优化后的校准表和权重
    tmp_cali = self.create_cali_table(False)
    self.create_weights(False)
    # 执行量化模型推理，获取输出
    preds = lower_and_eval(
        self.mlir_file, 'INT4', self.chip, tmp_cali.out_table,
        self.qtable, self.ref_tensors, eval_samples
    )
    outputname = self.module.output_names[0]
    tmploss = 0
    # 计算损失（1-余弦相似度，值越小精度越高）
    for s in eval_samples:
        ref = self.ref_tensors.get(outputname, s)
        pred = preds[s-self.num_sample//2]
        loss = 1 - self.calculate_sim(pred, ref)
        tmploss += loss
        print(f'样本{s}的损失：{loss}，累计损失：{tmploss}')
    tmploss /= len(eval_samples)  # 平均损失
    print(f'当前p值的平均损失：{tmploss}')
    del preds
    self.restore_weight_file()  # 恢复原始权重文件
    return tmploss
```

**功能与原理**：

- 核心是 “Lp 阈值优化 \+ 损失评估”：针对特定 p 值，为权重和激活计算初始最优阈值，然后通过量化模型推理评估精度损失。

- 激活数据拼接：将多个校准样本的激活数据拼接，确保阈值估计基于更全面的数据分布，提升阈值准确性。

- 损失定义：使用 “1 \- 余弦相似度” 而非 MSE，更关注特征向量的方向一致性，适配分类、匹配等任务的精度需求。

#### （2）阈值数组映射（gather\_points、scatter\_points）

```Python
def gather_points(self):
    # 将分散的权重/激活阈值整理为一维数组（适配数值优化算法）
    points = []
    idx = 0
    self.point_idx = {}
    for op in self.finetune_layers:
        for opd in [0, 1]:
            opdname = self.parser.get_opds_by_op_name(op)[opd]
            if opdname in self.finetune_layer_weight_best_ths:  # 权重阈值
                points.append(self.finetune_layer_weight_best_ths[opdname])
                self.point_idx[opdname] = idx
                idx += 1
            elif opdname in self.trying_scales4:  # 激活阈值
                points.append(self.trying_scales4[opdname])
                self.point_idx[opdname] = idx
                idx += 1
    return np.array(points)

def scatter_points(self, points):
    # 将数值优化后的一维数组阈值，映射回对应的权重/激活（反向映射）
    for opdname in self.point_idx:
        if opdname in self.finetune_layer_weight_best_ths:
            self.finetune_layer_weight_best_ths[opdname] = points[self.point_idx[opdname]]
        elif opdname in self.trying_scales4:
            self.trying_scales4[opdname] = points[self.point_idx[opdname]]
```

**功能与原理**：

- 适配数值优化算法：scipy 的`minimize`函数需接收一维数组作为优化参数，因此通过`gather_points`将分散的阈值（权重 \+ 激活）整理为一维数组，优化后再通过`scatter_points`映射回原结构。

- 关键映射：`point_idx`记录每个阈值在一维数组中的下标，确保反向映射时不混淆。

#### （3）数值优化目标函数（opt\_lower\_eval）

```Python
def opt_lower_eval(self, points, cali_samples, eval_samples):
    # 数值优化的目标函数：输入阈值数组，返回平均损失
    print(f'当前优化阈值：{points}')
    self.backup_weight_file()  # 备份原始权重
    self.scatter_points(points)  # 将阈值数组映射回权重/激活
    # 生成优化后的校准表和权重
    tmp_cali = self.create_cali_table(False)
    self.create_weights(False)
    # 量化模型推理
    preds = lower_and_eval(
        self.mlir_file, 'INT4', self.chip, tmp_cali.out_table,
        self.qtable, self.ref_tensors, eval_samples
    )
    outputname = self.module.output_names[0]
    tmploss = 0
    # 计算平均损失
    for s in eval_samples:
        ref = self.ref_tensors.get(outputname, s)
        pred = preds[s-self.num_sample//2]
        loss = 1 - self.calculate_sim(pred, ref)
        tmploss += loss
    tmploss /= len(eval_samples)
    print(f'当前优化轮次的平均损失：{tmploss}')
    del preds
    self.restore_weight_file()  # 恢复原始权重
    return tmploss
```

**功能与原理**：

- 数值优化的核心回调函数：每次迭代时，数值优化算法会生成一组新的阈值数组，该函数将其映射为实际阈值，生成校准表和权重，执行量化推理并计算损失，返回给优化算法用于调整下一步搜索方向。

#### （4）校准表与权重生成（create\_cali\_table、create\_weights）

```Python
def create_cali_table(self, ifprint):
    # 生成优化后的INT4激活校准表
    tmp_cali = copy.deepcopy(self.cali_table)
    for op in self.trying_scales4:
        for op_ in tmp_cali.table4:
            if op_ == op:
                if ifprint:
                    print(f'激活{op}的阈值从{tmp_cali.table4[op][0]}更新为{self.trying_scales4[op]}')
                tmp_cali.table4[op][0] = self.trying_scales4[op]  # 更新激活阈值
    tmp_cali.out_table = tmp_cali.out_table + '.opt'  # 优化后校准表文件名
    tmp_cali.write()  # 写入文件
    return tmp_cali

def create_weights(self, ifprint):
    # 生成优化后的权重文件（INT4量化）
    w = np.load(self.weight_file)
    ww = {}
    for opdname in w.files:
        if opdname in self.finetune_layer_weight_best_ths:
            if ifprint:
                print(f'权重{opdname}的阈值从{self.finetune_layer_weight_orig_ths[opdname]}更新为{self.finetune_layer_weight_best_ths[opdname]}')
            # 按优化后的阈值量化权重
            tmp = self.quant_tensor(w[opdname], self.finetune_layer_weight_best_ths[opdname], True, 4)
            ww[opdname] = tmp
        else:
            ww[opdname] = w[opdname]  # 非优化权重保持不变
    np.savez(self.weight_file, **ww)  # 保存优化后的权重
```

**功能与原理**：

- 将优化后的阈值固化为校准表和权重文件，用于后续量化部署。

- 仅更新优化后的阈值和权重，非优化部分保持原始状态，确保模型其他部分的稳定性。

7. 整体优化流程（learning）

```Python
def learning(self):
    total = len(self.finetune_layers)
    outputname = self.module.output_names[0]
    # 样本划分：前半为校准样本（用于阈值优化），后半为评估样本（用于损失计算）
    cali_samples = range(0, self.num_sample//2)
    eval_samples = range(self.num_sample//2, self.num_sample)

    # （注释部分：遍历不同p值，找到最优Lp范数阶数，此处直接指定p_intr=1.9）
    p_intr = 1.9  # 最优p值（基于经验或前期测试确定）
    print(f'使用的Lp范数阶数：{p_intr:.2f}')

    # 基于最优p值，计算初始阈值并评估损失
    lp_loss = self.try_one_q(p_intr, cali_samples, eval_samples)
    # 将初始阈值整理为一维数组，用于数值优化
    points = self.gather_points()

    # 数值优化参数配置（Powell算法）
    min_options = {}
    min_options['maxiter'] = self.search_loop  # 迭代次数
    min_options['maxfev'] = 100  # 最大函数调用次数
    min_method = "Powell"  # 无梯度优化算法（适配阈值优化，无需计算梯度）

    # 执行数值优化：最小化opt_lower_eval返回的损失
    res = opt.minimize(
        lambda points: self.opt_lower_eval(points, cali_samples, eval_samples),
        points, method=min_method, options=min_options
    )
    print(f'数值优化结果：{res}')

    # 生成最终优化后的校准表和权重
    self.scatter_points(res.x)  # 优化后的阈值数组映射回原结构
    tmp_cali = self.create_cali_table(True)  # 打印阈值更新信息
    self.create_weights(True)
    print('LAPQ优化完成！')
```

**功能与原理**：

- 完整优化闭环：样本划分→初始 Lp 阈值优化→数值迭代调优→最终结果生成。

- 数值优化算法选择：采用 Powell 算法，无需计算梯度，适合阈值这类难以直接求导的优化目标，稳定性强。

- 样本划分：校准样本用于阈值估计，评估样本用于损失计算，避免过拟合，确保优化结果的泛化性。

8. 权重文件备份与恢复

```Python
def backup_weight_file(self):
    # 备份原始权重文件（后缀.orig.npz）
    shutil.copy(self.weight_file, self.weight_file.replace(".npz", ".orig.npz"))

def restore_weight_file(self):
    # 恢复原始权重文件（覆盖当前权重文件）
    shutil.copy(self.weight_file.replace(".npz", ".orig.npz"), self.weight_file)

def save_weights(self):
    # 保存优化后的权重文件（备份原始文件后覆盖）
    os.rename(self.weight_file, self.weight_file.replace(".npz", ".bak.npz"))
    np.savez(self.weight_file, **self.param_back)
```

**功能与原理**：

- 确保优化过程中原始权重的安全性：每次修改权重前备份，优化后可选择恢复或保存，避免误操作导致原始数据丢失。

### 三、总结

该代码是 TPU\-MLIR 工具链中针对 INT4 低精度量化的高阶优化模块，核心价值在于通过 “Lp 范数初始优化 \+ Powell 数值迭代调优”，同时优化权重和激活的量化阈值，解决传统 absmax 阈值在低精度场景下精度损失过大的问题。核心亮点如下：

1. **精准阈值优化**：基于 Lp 范数最小化量化失真，比传统 absmax 更适配 INT4 低精度场景，初始阈值更精准。

2. **双维度优化**：同时优化权重和激活的阈值，全面降低量化带来的精度损失，而非单一维度优化。

3. **灵活的损失定义**：采用 “1 \- 余弦相似度” 作为损失函数，更贴合分类、特征匹配等任务的精度需求。

4. **工程化适配**：支持自定义优化层、样本划分、数值优化参数配置，适配不同模型和硬件场景。

5. **安全的工程实现**：权重文件备份与恢复机制，避免原始数据污染，确保优化过程可回溯。

整体流程从初始化、阈值初优、迭代调优到结果生成，形成完整的量化优化闭环，最终输出可直接用于部署的 INT4 量化校准表和权重文件，大幅提升低精度量化模型的精度。



## lsq\.py

### 一、代码整体原理逻辑阐述

该代码是**TPU\-MLIR 量化工具链中的激活量化缩放因子（scale）优化模块**，核心目标是通过 SGD 或 Adam 优化器，迭代调整激活层的量化缩放因子，让量化后的激活输出尽可能接近浮点模型输出，从而减少 INT8 量化带来的精度损失。整体逻辑围绕 “scale 优化闭环” 展开，核心流程如下：

1. **初始化配置**：加载 MLIR 模型、解析算子信息、筛选待优化层（排除对量化不敏感或无需优化的算子）、初始化优化器（SGD/Adam）和学习率调度器。

2. **基准损失计算**：使用原始缩放因子（通常来自校准表）计算量化后的基准损失，作为优化的参照。

3. **迭代更新 scale**：对每个待优化层，遍历样本计算量化输出与浮点参考输出的损失，通过反向传播求解 scale 的梯度，利用优化器更新 scale。

4. **优化验证**：计算优化后 scale 对应的量化损失，结合 scale 的合理性（如不小于 0、不超过原始 scale 的 1\.5 倍），决定是否保留新 scale。

5. **结果收敛**：按 epoch 重复上述流程，直到所有层完成优化，最终输出优化后的缩放因子，用于后续量化部署。

核心设计思想：量化缩放因子直接决定浮点数据到整数的映射精度，传统校准表的静态 scale 难以适配所有层的分布特性，通过梯度下降动态优化 scale，可针对性降低各层的量化失真，尤其适用于对精度敏感的激活层。

### 二、代码逐部分详细分析

1. 全局配置与常量定义

```SQL
SKIP_OPERATION = [
    'top.Input', 'top.Reshape', 'top.Softmax', 'top.Weight', 'top.MaxPool', 'top.Slice', 'top.Tile',
    'top.Permute', 'top.Upsample'
]
```

**功能与原理**：

- 定义无需优化的算子列表，这些算子要么是输入 / 权重层（如 top\.Input、top\.Weight），要么是对量化不敏感的层（如 top\.Reshape、top\.MaxPool），优化这些层的 scale 对整体精度提升有限，反而会增加计算开销。

- 筛选逻辑聚焦于核心激活层（如 Conv、MatMul 的输出层），确保优化资源集中在关键节点。

2. 量化缩放因子优化器类（SGD/Adam）

分别实现 SGD 和 Adam 两种优化器，适配 scale 的更新逻辑，核心是结合量化特性（有符号 / 无符号）调整更新公式。

#### （1）SGD 优化器（SgdScaleOpt）

```Python
class SgdScaleOpt:
    def __init__(self, lr, momentum=0.0, nesterov=False, weight_decay=0.0, loger=None, support_unsigned=False):
        self.lr = lr  # 学习率调度器
        self.momentum = momentum  # 动量系数
        self.nesterov = nesterov  # Nesterov加速
        self.weight_decay = weight_decay  # 权重衰减
        self.dampening = 0.0
        self.v = {}  # 动量缓存（键：算子名）
        self.grd = {}  # 梯度缓存
        self.loss = {}  # 损失缓存
        self.loger = loger  # 日志工具
        self.support_unsigned = support_unsigned  # 是否支持无符号量化

    def cal_scale(self, iter, scale, loss, grd, unsigned=False):
        # 转换scale为步长（step）：映射到[0,1]范围，适配梯度更新
        if unsigned:
            step = np.abs(scale)/255.0  # 无符号量化：scale对应0~255范围
        else:
            step = np.abs(scale)/127.0  # 有符号量化：scale对应-128~127范围（取127为分母）

        # 梯度更新：步长 = 步长 - 学习率 * 梯度
        step = step - self.lr.cal_lr(iter) * grd
        self.loger.logging(f"更新scale：步长{step} 梯度{grd} 损失{loss}")

        # 转换步长回原始scale范围
        if unsigned:
            scale = step*255.0
        else:
            scale = step*127.0
        return scale

    def update_scale(self, iter, op, scale, mini_batch, unsigned=False):
        # 梯度和损失按mini-batch大小归一化
        self.grd[op] = self.grd[op]/mini_batch
        self.loss[op] = self.loss[op]/mini_batch

        # 权重衰减（L2正则化，防止scale过拟合）
        if self.weight_decay != 0.0:
            if unsigned:
                self.grd[op] += np.abs(scale)/255.0 * self.weight_decay
            else:
                self.grd[op] += np.abs(scale)/127.0 * self.weight_decay

        # 动量计算
        if self.momentum != 0.0:
            if op in self.v:
                self.v[op] = self.v[op]*self.momentum + self.grd[op]*(1.0-self.dampening)
            else:
                self.v[op] = self.grd[op]
            # Nesterov加速：提前更新梯度
            if self.nesterov:
                self.grd[op] = self.v[op]*self.momentum + self.grd[op]
            else:
                self.grd[op] = self.v[op]

        # 计算并返回新scale
        if self.support_unsigned:
            scale = self.cal_scale(iter, scale, self.loss[op], self.grd[op], unsigned)
        else:
            scale = self.cal_scale(iter, scale, self.loss[op], self.grd[op], False)
        self.reset_grd_loss(op)  # 重置梯度和损失缓存
        return scale

    # 辅助方法：累积损失、累积梯度、重置缓存
    def update_loss(self, op, loss):
        self.loss[op] = self.loss[op] + loss if op in self.loss else loss

    def update_grd(self, op, grd):
        self.grd[op] = self.grd[op] + grd if op in self.grd else grd

    def reset_grd_loss(self, op):
        self.grd[op] = 0.0
        self.loss[op] = 0.0
```

**功能与原理**：

- 适配 scale 的更新特性：将 scale 映射到 \[0,1\] 的步长进行优化，避免直接更新 scale 导致的数值不稳定。

- 支持动量和 Nesterov 加速：提升梯度下降的收敛速度，减少震荡；权重衰减防止 scale 过度调整，保证量化稳定性。

- 区分有符号 / 无符号量化：根据量化范围调整步长计算，确保 scale 更新符合硬件量化规则。

#### （2）Adam 优化器（AdamScaleOpt）

```Python
class AdamScaleOpt:
    def __init__(self, lr, beta1=0.9, beta2=0.999, weight_decay=0.0, loger=None, support_unsigned=False):
        self.lr = lr  # 学习率调度器
        self.weight_decay = weight_decay  # 权重衰减
        self.beta1 = beta1  # 一阶动量系数
        self.beta2 = beta2  # 二阶动量系数
        self.amsgrad = False  # 是否启用AMSGrad
        self.steps = {}  # 每个算子的迭代步数
        self.eps = 1e-8  # 防止分母为0的微小值
        self.exp_avgs = {}  # 一阶动量缓存
        self.exp_avgs_sqs = {}  # 二阶动量缓存
        self.max_exp_avgs_sqs = {}  # AMSGrad的最大二阶动量缓存
        self.grd = {}  # 梯度缓存
        self.loss = {}  # 损失缓存
        self.loger = loger
        self.support_unsigned = support_unsigned

    def cal_scale(self, scale, delta, unsigned=False):
        # 转换scale为步长，应用delta更新后还原
        if unsigned:
            step = np.abs(scale)/255.0
        else:
            step = np.abs(scale)/127.0
        step -= delta
        self.loger.logging(f"更新scale步长：{step}")
        return step*255.0 if unsigned else step*127.0

    def update_scale(self, iter, op, scale, mini_batch, unsigned=False):
        # 梯度和损失归一化
        self.grd[op] = self.grd[op]/mini_batch
        self.loss[op] = self.loss[op]/mini_batch

        # 迭代步数计数
        self.steps[op] = self.steps[op] + 1 if op in self.steps else 1

        # 权重衰减
        if self.weight_decay != 0.0:
            if unsigned:
                self.grd[op] = scale/255.0*self.weight_decay + self.grd[op]
            else:
                self.grd[op] = scale/127.0*self.weight_decay + self.grd[op]

        # 一阶和二阶动量计算（带偏差修正）
        bias_correction1 = 1 - self.beta1 ** self.steps[op]
        bias_correction2 = 1 - self.beta2 ** self.steps[op]

        if op in self.exp_avgs:
            self.exp_avgs[op] = self.exp_avgs[op]*self.beta1 + self.grd[op]*(1-self.beta1)
            self.exp_avgs_sqs[op] = self.exp_avgs_sqs[op]*self.beta2 + (self.grd[op]**2)*(1-self.beta2)
        else:
            self.exp_avgs[op] = 0.0
            self.exp_avgs_sqs[op] = 0.0
            self.max_exp_avgs_sqs[op] = 0.0

        # AMSGrad：跟踪最大二阶动量
        if self.amsgrad:
            self.max_exp_avgs_sqs[op] = np.maximum(self.max_exp_avgs_sqs[op], self.exp_avgs_sqs[op])
            denorm = np.sqrt(self.max_exp_avgs_sqs[op])/np.sqrt(bias_correction2) + self.eps
        else:
            denorm = np.sqrt(self.exp_avgs_sqs[op])/np.sqrt(bias_correction2) + self.eps

        # 计算更新量delta
        step_size = self.lr.cal_lr(iter) / bias_correction1
        delta = step_size * self.exp_avgs[op] / denorm

        # 更新并返回新scale
        if unsigned:
            scale = (np.abs(scale)/255.0 - delta)*255.0
            self.loger.logging(f"无符号scale更新：{scale/255.0} 梯度{self.grd[op]} delta{delta}")
        else:
            scale = (np.abs(scale)/127.0 - delta)*127.0
            self.loger.logging(f"有符号scale更新：{scale/127.0} 梯度{self.grd[op]} delta{delta}")
        self.reset_grd_loss(op)
        return scale

    # 辅助方法：与SGD优化器一致，累积损失、梯度和重置缓存
    def update_loss(self, op, loss):
        self.loss[op] = self.loss[op] + loss if op in self.loss else loss

    def update_grd(self, op, grd):
        self.grd[op] = self.grd[op] + grd if op in self.grd else grd

    def reset_grd_loss(self, op):
        self.grd[op] = 0.0
        self.loss[op] = 0.0
```

**功能与原理**：

- 基于自适应学习率：通过一阶和二阶动量动态调整每个 scale 的更新步长，适配不同层 scale 的梯度特性，收敛速度通常快于 SGD。

- 支持 AMSGrad：可选跟踪最大二阶动量，避免 Adam 在后期可能出现的收敛震荡问题。

- 数值稳定性优化：加入 eps 防止分母为 0，偏差修正解决前期动量估计偏差的问题。

3. 核心缩放因子学习类（LearningScale）

封装 scale 优化的全流程，包括初始化、层筛选、迭代优化、损失验证等。

#### （1）初始化方法（**init**）

```Python
class LearningScale:
    def __init__(self, args):
        self.args = args
        self.mlir_file = args.mlir_file  # MLIR模型路径
        self.chip = args.chip  # 目标芯片型号
        self.module = pymlir.module()
        self.module.load(self.mlir_file)  # 加载MLIR模型
        self.parser = MlirParser(args.mlir_file)  # MLIR解析器
        self.batch_size = self.parser.get_batch_size()  # 模型批次大小
        self.input_num = self.parser.get_input_num()  # 模型输入数量
        self.mini_batch = args.mini_batch  # mini-batch大小（梯度累积）
        self.epoch = args.epoch  # 迭代epoch数
        self.ref_tensors = None  # 参考张量（浮点模型输出，来自ref_tensors类）
        self.num_sample = 0  # 样本数量
        self.orig_scales8 = {}  # 原始INT8激活缩放因子（来自校准表）
        self.orig_scales4 = {}  # 原始INT4激活缩放因子（预留）
        self.new_scales = {}  # 优化后的缩放因子
        self.pre_loss = {}  # 优化前的基准损失
        self.post_loss = {}  # 优化后的损失
        self.loger = None  # 日志工具
        self.support_unsigned = False  # 是否支持无符号量化
        self.opt = None  # 优化器（SGD/Adam）
        self.finetune_layers = []  # 待优化的算子列表

        # 核心初始化步骤
        self.get_finetune_ops(args.excepts)  # 筛选待优化层
        # 调整mini-batch大小（适配模型批次大小）
        if self.mini_batch <= self.batch_size:
            self.mini_batch = 1
        else:
            self.mini_batch = self.mini_batch // self.batch_size
```

**功能与原理**：

- 加载模型和解析配置：获取模型结构、批次大小、输入数量等基础信息，为后续优化提供支撑。

- 筛选待优化层：通过`get_finetune_ops`排除无需优化的算子，聚焦核心激活层。

- 初始化关键变量：存储原始 scale、优化后 scale、损失值等，为迭代优化提供数据载体。

#### （2）优化器初始化（init\_sgd、init\_adam）

```Python
def init_sgd(self, scheduler, momentum, nesterov, weight_decay):
    # 初始化SGD优化器
    self.opt = SgdScaleOpt(scheduler, momentum, nesterov, weight_decay, self.loger)

def init_adam(self, scheduler, beta1=0.9, beta2=0.999, weight_decay=0.0):
    # 初始化Adam优化器（参数顺序适配外部调用，beta1对应momentum，beta2对应nesterov参数位）
    self.opt = AdamScaleOpt(scheduler, beta1, beta2, weight_decay, self.loger)
```

**功能与原理**：

- 提供统一的优化器初始化接口，支持根据外部参数选择 SGD 或 Adam，适配不同模型的优化需求。

- 传递学习率调度器、权重衰减等参数，确保优化器配置的灵活性。

#### （3）待优化层筛选（get\_finetune\_ops）

```Python
def get_finetune_ops(self, excepts):
    top_ops = {op.name: op for op in self.parser.ops}  # 算子名→算子对象映射
    exclude = excepts.split(',')  # 外部指定的排除算子列表
    for n in top_ops:
        # 筛选条件：1. 不在SKIP_OPERATION中；2. 不在外部排除列表中
        if top_ops[n].type not in SKIP_OPERATION and top_ops[n].name not in exclude:
            self.finetune_layers.append(n)
```

**功能与原理**：

- 双重筛选确保优化效率：内置排除对量化不敏感的算子，外部支持自定义排除特定层，灵活适配不同模型。

- 聚焦核心激活层：仅保留 Conv、MatMul、Add 等对量化精度影响较大的层，避免无效优化。

#### （4）输入设置方法（set\_op\_inputs）

```Python
def set_op_inputs(self, op, loop, quant=False):
    pre_ops = self.parser.get_pre_op_by_op_name(op)  # 获取当前算子的前序输入算子
    for pop in pre_ops:
        shape = self.ref_tensors.get(pop, loop).shape  # 输入张量形状
        # 处理Reshape/Squeeze/Unsqueeze算子（实际输入为其前序算子）
        if pop not in self.module.all_tensor_names:
            if self.parser.get_op_type_by_op_name(pop) in ['top.Reshape', 'top.Squeeze', 'top.Unsqueeze']:
                pre_pre_ops = self.parser.get_pre_op_by_op_name(pop)
                pop = pre_pre_ops[0]
            else:
                print(f"{op}的输入{pop}不在张量列表中")
                sys.exit(1)
        # 获取参考输入张量并复制
        d = self.ref_tensors.get(pop, loop).copy().reshape(shape)
        # 选择当前使用的scale（优先优化后的新scale，否则用原始scale）
        scale = 1.0
        if pop in self.new_scales:
            scale = self.new_scales[pop][0]
        elif pop in self.orig_scales8:
            scale = self.orig_scales8[pop][0]
        # 判断是否为无符号量化
        unsigned = self.orig_scales8[op][1] >= 0 and self.orig_scales8[op][2] >= 0
        # 对输入进行量化（模拟实际量化流程）
        if scale != 1.0 and not quant:
            if self.support_unsigned:
                d = quant_requant_active(d, scale, unsigned)
            else:
                d = quant_requant_active(d, scale, False)
        # 将处理后的输入设置到模型中
        self.module.set_tensor(pop, d)
```

**功能与原理**：

- 模拟量化输入流程：设置输入时根据当前 scale 进行量化，确保模型推理的输入与实际部署一致，损失计算准确。

- 处理特殊算子：Reshape、Squeeze 等仅改变张量形状的算子，其输入实际来自前序数据算子，需递归获取避免输入错误。

- 动态选择 scale：优化过程中自动切换原始 scale 和新 scale，无需手动切换配置。

#### （5）梯度计算方法（cal\_grd 系列）

```Python
def cal_grdscale_unsigned(self, out):
    # 无符号量化的梯度缩放因子（归一化梯度量级）
    return 1.0 / (out.size * 255.0) ** 0.5

def cal_grdscale_signed(self, out):
    # 有符号量化的梯度缩放因子
    return 1.0 / (out.size * 127.0) ** 0.5

def cal_grd_unsigned(self, out, scale):
    # 无符号量化的梯度计算（考虑量化裁剪影响）
    step = np.abs(scale)/255.0  # 量化步长
    m = np.round(out/step)  # 量化后的整数
    qmin = np.zeros_like(m)  # 无符号量化最小值：0
    qmax = np.ones_like(m)*255  # 无符号量化最大值：255
    # 量化误差：量化后的值 - 原始值，乘以2（MSE损失的导数）
    g = (np.minimum(np.maximum(m, qmin), qmax)*step - out)*2
    grd = 0.0
    # 超出qmin的部分：梯度为0（裁剪后无变化）
    grd += (np.where(m <= 0, 0, 0)*g).sum()
    # 超出qmax的部分：梯度为0
    grd += (np.where(m >= 255, 255, 0)*g).sum()
    # 正常范围内的梯度：考虑量化取舍的影响
    left = np.where(m > 0, 1, 0) & np.where(m < 255, 1, 0)
    m = m - out/step  # 小数部分
    m = m * left * g
    grd += m.sum()
    return grd

def cal_grd_signed(self, out, scale):
    # 有符号量化的梯度计算（逻辑与无符号一致，仅量化范围不同）
    step = np.abs(scale)/127.0
    m = np.round(out/step)
    qmin = np.ones_like(m)*(-128)  # 有符号最小值：-128
    qmax = np.ones_like(m)*127  # 有符号最大值：127
    g = (np.minimum(np.maximum(m, qmin), qmax)*step - out)*2
    grd = 0.0
    grd += (np.where(m <= -128, -128, 0)*g).sum()
    grd += (np.where(m >= 127, 127, 0)*g).sum()
    left = np.where(m > -128, 1, 0) & np.where(m < 127, 1, 0)
    m = m - out/step
    m = m * left * g
    grd += m.sum()
    return grd

def cal_grd(self, out, scale, use_grdscale=True, unsigned=False):
    # 统一梯度计算接口：根据有符号/无符号选择对应方法
    grd_funcs = {'unsigned': self.cal_grd_unsigned, 'signed': self.cal_grd_signed}
    grdscale_funcs = {'unsigned': self.cal_grdscale_unsigned, 'signed': self.cal_grdscale_signed}
    
    grdfunc = grd_funcs['unsigned'] if unsigned else grd_funcs['signed']
    grdscalefunc = grdscale_funcs['unsigned'] if unsigned else grdscale_funcs['signed']

    grd = grdfunc(out, scale)  # 计算原始梯度
    grd_scale = grdscalefunc(out)  # 计算梯度缩放因子

    # 应用梯度缩放（归一化梯度量级，提升优化稳定性）
    return grd * grd_scale if use_grdscale else grd
```

**功能与原理**：

- 梯度计算考虑量化裁剪：对超出量化范围（qmin/qmax）的部分，梯度设为 0，符合实际量化逻辑（裁剪后数值无变化，梯度无贡献）。

- 梯度缩放：通过`cal_grdscale_*`归一化梯度量级，避免因张量大小不同导致的梯度数值差异过大，提升优化稳定性。

- 统一接口：支持有符号 / 无符号量化，无需重复编写梯度计算逻辑。

#### （6）单个层优化核心流程（learning\_one）

```Python
def learning_one(self, op, total):
    self.loger.logging(f"开始优化算子{op}的scale")
    # 进度条配置（3倍样本数：基准损失计算+scale更新+优化验证）
    pbar_detail = tqdm(np.arange(self.num_sample*3))
    pbar_detail.set_description("Scale优化，算子 %s" % op)

    # Step 1：计算基准损失（使用原始scale）
    for loop in np.arange(self.num_sample):
        pbar_detail.set_postfix_str(f"计算基准损失 {loop} [总进度: {total}]")
        pbar_detail.update()
        self.set_op_inputs(op, loop, quant=True)  # 设置输入（不量化，用原始scale）
        outputs = self.module.invoke_at(op).copy()  # 执行到当前算子，获取量化输出
        # 激活量化（模拟部署流程）
        scale = self.orig_scales8[op][0]
        unsigned = self.orig_scales8[op][1] >= 0 and self.orig_scales8[op][2] >= 0
        if self.support_unsigned:
            outputs[0] = quant_requant_active(outputs[0], scale, unsigned)
        else:
            outputs[0] = quant_requant_active(outputs[0], scale, False)
        # 计算与浮点参考输出的MSE损失
        ref = self.ref_tensors.get(op, loop)
        if op in self.pre_loss:
            self.pre_loss[op] += cal_loss(outputs, ref)
        else:
            self.pre_loss[op] = cal_loss(outputs, ref)

    # Step 2：迭代更新scale（核心优化步骤）
    for loop in np.arange(self.num_sample):
        pbar_detail.set_postfix_str(f"优化中 {loop} [总进度: {total}]")
        pbar_detail.update()
        self.set_op_inputs(op, loop, quant=True)  # 设置输入（用当前scale量化）
        outputs = self.module.invoke_at(op).copy()  # 获取量化输出
        # 获取当前使用的scale
        scale = self.new_scales[op][0] if op in self.new_scales else self.orig_scales8[op][0]
        unsigned = self.orig_scales8[op][1] >= 0 and self.orig_scales8[op][2] >= 0
        # 激活量化
        if scale != 1.0:
            if self.support_unsigned:
                outputq = quant_requant_active(outputs[0], scale, unsigned)
            else:
                outputq = quant_requant_active(outputs[0], scale, False)
        else:
            outputq = outputs[0]
        # 计算MSE损失并累积
        ref = self.ref_tensors.get(op, loop)
        loss = cal_loss(outputq, ref)
        self.opt.update_loss(op, loss)
        # 计算scale的梯度并累积
        grd = self.cal_grd(outputs[0], scale, True, unsigned)
        self.opt.update_grd(op, grd)
        # 达到mini-batch大小，更新scale
        if (loop+1) % self.mini_batch == 0:
            scale = self.opt.update_scale(loop, op, scale, self.mini_batch, unsigned)
            # 保存新scale
            if op in self.new_scales:
                self.new_scales[op][0] = scale
            else:
                self.new_scales[op] = [scale, 0, 0]
            self.loger.logging(f"{op} 新scale：{scale:.16f} 迭代{loop+1} 批次{self.mini_batch}")

    # Step 3：验证优化后损失（使用新scale）
    for loop in np.arange(self.num_sample):
        pbar_detail.set_postfix_str(f"验证优化效果 {loop} [总进度: {total}]")
        pbar_detail.update()
        self.set_op_inputs(op, loop, quant=True)  # 设置输入（用新scale量化）
        self.module.invoke_at(op)
        outputs = self.module.get_tensor(op).copy()  # 获取优化后量化输出
        # 激活量化
        scale = self.new_scales[op][0]
        unsigned = self.orig_scales8[op][1] >= 0 and self.orig_scales8[op][2] >= 0
        if self.support_unsigned:
            outputs[0] = quant_requant_active(outputs[0], scale, unsigned)
        else:
            outputs[0] = quant_requant_active(outputs[0], scale, False)
        # 计算优化后损失
        ref = self.ref_tensors.get(op, loop)
        if op in self.post_loss:
            self.post_loss[op] += cal_loss(outputs, ref)
        else:
            self.post_loss[op] = cal_loss(outputs, ref)

    # 释放输入张量缓存（减少内存占用）
    for in_ in self.parser.get_pre_op_by_op_name(op):
        self.ref_tensors.consumed_tensor(in_)

    # Step 4：判断是否保留新scale（损失降低且scale合理）
    scale_valid = self.new_scales[op][0] >= 0 and self.new_scales[op][0]/self.orig_scales8[op][0] <= 1.5
    if self.post_loss[op] >= self.pre_loss[op] or not scale_valid:
        self.loger.logging(
            f"放弃{op}的scale优化：原损失{self.pre_loss[op]} 新损失{self.post_loss[op]} 原scale{self.orig_scales8[op][0]} 新scale{self.new_scales[op][0]}")
        del self.new_scales[op]  # 丢弃无效新scale
    else:
        self.loger.logging(
            f"保留{op}的scale优化：原损失{self.pre_loss[op]} 新损失{self.post_loss[op]} 原scale{self.orig_scales8[op][0]} 新scale{self.new_scales[op][0]}")
```

**功能与原理**：

- 三阶段闭环优化：基准损失计算→scale 迭代更新→优化验证，确保每一步都有明确目标，避免盲目优化。

- scale 合理性检查：除了损失降低，还限制新 scale 不小于 0、不超过原始 scale 的 1\.5 倍，防止 scale 异常导致量化溢出或失真加剧。

- 内存优化：通过`ref_tensors.consumed_tensor`释放不再需要的输入缓存，减少大模型优化时的内存占用。

#### （7）整体优化流程（learning）

```Python
def learning(self):
    total = len(self.finetune_layers)
    # 按epoch迭代，逐个优化待优化层
    for e in range(self.epoch):
        for l in self.finetune_layers:
            self.learning_one(l, total)
```

**功能与原理**：

- 多 epoch 迭代：通过多次 epoch 提升优化收敛效果，确保 scale 逐步逼近最优值。

- 串行优化：逐个处理待优化层，避免层间依赖导致的优化冲突，确保每个层的优化都基于稳定的输入条件。

### 三、总结

该代码是 TPU\-MLIR 量化工具链中针对激活量化缩放因子的高阶优化模块，核心价值在于通过自适应优化器动态调整 scale，解决传统静态校准表中 scale 适配性不足的问题。核心亮点如下：

1. **双优化器支持**：提供 SGD 和 Adam 两种优化器，适配不同模型的收敛特性（SGD 稳定，Adam 收敛快）。

2. **量化类型适配**：严格区分有符号 / 无符号量化的 scale 计算逻辑，符合硬件量化规则。

3. **精准梯度计算**：考虑量化裁剪对梯度的影响，结合梯度缩放，提升优化稳定性和精度。

4. **智能层筛选**：自动排除无需优化的算子，聚焦核心激活层，提升优化效率。

5. **安全优化机制**：通过损失验证和 scale 合理性检查，避免优化后精度下降或量化异常。



## easyquant\.py

### 一、代码整体原理逻辑阐述

该代码是**TPU\-MLIR 量化工具链中的轻量级 INT4 量化优化模块（EasyQuant）**，核心目标是通过**网格搜索**快速找到权重和激活的最优量化阈值（threshold），同时支持偏置校正，最小化 INT4 量化带来的精度损失。整体逻辑围绕 “阈值搜索 \- 交替优化 \- 偏置校正” 闭环展开，（输出 = 激活（输入特征） × 权重（参数） \+ 偏置）核心流程如下：

1. **初始化配置**：加载 MLIR 模型、解析算子信息、筛选待优化层（仅 MatMul）、备份原始权重和初始阈值（absmax 方法），配置搜索参数（搜索范围、步长、迭代轮次）。

2. **阈值搜索准备**：确定权重和激活的初始搜索范围（基于原始 absmax 阈值，按`alpha`\~`beta`比例缩放），设置搜索步长`steps`。

3. **网格搜索最优阈值**：对每个待优化层，交替搜索两个输入（权重 / 激活）的阈值 —— 固定一个输入的阈值，遍历另一个输入的所有候选阈值，计算量化输出与浮点输出的余弦相似度，取相似度最大的阈值作为最优值，迭代`search_loop`轮提升稳定性。

4. **偏置校正**：权重量化后会引入系统误差，通过计算误差对输出的影响，调整偏置值抵消该误差，进一步提升精度。

5. **结果保存**：更新优化后的权重和激活阈值，备份原始权重文件后，保存优化后的权重，用于后续 INT4 量化部署。

核心设计思想：相比复杂的数值优化（如 LAPQ 的 Powell 算法），EasyQuant 采用网格搜索，实现简单、计算开销小、易部署，同时通过交替优化和偏置校正，在精度与效率间取得平衡，适合对部署速度有要求的 INT4 量化场景。

### 二、代码逐部分详细分析

1. 全局配置与常量定义

```Python
EASY_QUANT_OPERATION = [
    'top.MatMul'  # 待优化的核心算子（注释预留Conv扩展）
]
```

**功能与原理**：

- 明确优化目标为 MatMul 算子，这类算子是模型权重的核心载体，INT4 量化对其精度影响最大，是优化的关键靶点。

- 后续筛选逻辑会排除分组卷积（若扩展 Conv 支持），确保优化聚焦于结构简单、量化敏感性高的核心层。

2. 核心类（EasyQuant）初始化（**init**）

```Python
class EasyQuant:
    def __init__(self, args):
        self.scales = None  # INT8激活量化阈值
        self.scales4 = None  # INT4激活量化阈值（核心优化对象）
        self.orig_scales4 = {}  # 原始INT4激活阈值备份（absmax）
        self.best_scales4 = {}  # 搜索到的最优INT4激活阈值
        self.finetune_layers = []  # 待优化的算子列表
        self.finetune_layer_weights = {}  # 算子→权重名映射
        self.finetune_layer_bias = {}  # 算子→偏置名映射
        self.finetune_layer_weight_orig_ths = {}  # 权重原始阈值（absmax）
        self.finetune_layer_weight_best_ths = {}  # 权重最优阈值（搜索后）
        self.mlir_file = args.mlir_file  # MLIR模型路径
        self.module = pymlir.module()
        self.module.load(self.mlir_file)  # 加载MLIR模型
        self.parser = MlirParser(args.mlir_file)  # MLIR解析器
        self.batch_size = self.parser.get_batch_size()  # 模型批次大小
        self.input_num = self.parser.get_input_num()  # 模型输入数量
        self.num_sample = 0  # 样本数量
        self.search_loop = 2  # 阈值搜索迭代轮次（默认2轮，提升稳定性）
        self.ref_tensors = {None}  # 参考张量（浮点模型输出）
        self.chip = args.chip  # 目标芯片型号
        self.orig_weights = {}  # 原始权重备份
        self.orig_bias = {}  # 原始偏置备份
        self.weight_file = self.parser.module_weight_file  # 权重文件路径（.npz）
        self.param_back = {}  # 优化后权重备份
        self.weights_scales = {}  # 权重量化缩放因子（初始absmax）
        self.compare_quanted = True  # 对比量化后的输出
        self.loger = None  # 日志工具
        # 网格搜索参数
        self.alpha = 0.1  # 阈值搜索起始比例（原始阈值的10%）
        self.beta = 2  # 阈值搜索结束比例（原始阈值的200%）
        self.steps = 100  # 搜索步长（100步均匀分布）
        self.active_only = not args.quantweight  # 是否仅优化激活（False则同时优化权重）
        self.include_layers = self.init_include(args.quant_layers)  # 需量化的层列表（可选）

        # 核心初始化步骤
        self.get_finetune_ops(args.excepts)  # 筛选待优化算子
        self.backup_weights()  # 备份原始权重和初始阈值
        # 加载原始权重，初始化权重原始阈值（absmax）
        w = np.load(self.weight_file, allow_pickle=True)
        for k in w:
            self.param_back[k] = w[k]
            for kk in self.finetune_layer_weights:
                if k == self.finetune_layer_weights[kk]:
                    th = np.max(np.abs(w[k]))  # 传统absmax阈值
                    self.finetune_layer_weight_orig_ths[k] = th
                    self.finetune_layer_weight_best_ths[k] = th  # 初始最优阈值=原始阈值
```

**功能与原理**：

- 初始化核心参数：涵盖模型加载、搜索参数配置、权重备份、阈值初始化，为网格搜索奠定基础。

- 搜索参数设计：`alpha`和`beta`定义阈值搜索范围（原始阈值的 0\.1\~2 倍），`steps`控制搜索精度（100 步确保覆盖关键区间），`search_loop`控制迭代轮次（2 轮交替优化，减少局部最优）。

- 灵活优化模式：通过`active_only`参数支持 “仅优化激活” 或 “同时优化权重 \+ 激活”，适配不同精度需求。

3. 辅助筛选与备份方法

#### （1）层筛选方法（included、init\_include）

```Python
def included(self, op):
    # 判断算子是否需要纳入优化（基于include_layers列表）
    if len(self.include_layers) == 0:
        return True
    elif op in self.include_layers:
        return True
    else:
        return False

def init_include(self, quant_layers):
    # 从文件加载需量化的层列表（若指定）
    if quant_layers == "":
        return []
    f = open(quant_layers, 'r')
    layers = []
    for line in f:
        if len(line) > 0 and (not line.startswith("#")):
            layers.append(line.strip())
    return layers
```

**功能与原理**：

- 支持自定义筛选优化层，通过`quant_layers`参数指定层列表文件，未指定则默认优化所有符合条件的 MatMul 算子，灵活适配不同模型。

#### （2）待优化算子筛选（get\_finetune\_ops）

```Python
def get_finetune_ops(self, excepts):
    top_ops = {op.name: op for op in self.parser.ops}  # 算子名→算子对象映射
    exclude = excepts.split(',')  # 外部指定的排除算子列表
    for op in top_ops:
        # 筛选条件：1. 属于EASY_QUANT_OPERATION；2. 不在排除列表；3. 在需量化层列表；4. 非分组卷积（若为Conv）
        if (top_ops[op].type in EASY_QUANT_OPERATION and 
            top_ops[op].name not in exclude and 
            self.included(top_ops[op].name)):
            if top_ops[op].type == 'top.Conv':
                if int(top_ops[op].attrs['group'].split(':')[0]) > 1:
                    continue  # 排除分组卷积
            # 关联算子与权重名（MatMul的权重通常在opds[1]）
            if len(top_ops[op].opds) > 1 and top_ops[op].opds[1] in self.module.all_weight_names:
                self.finetune_layers.append(op)
                self.finetune_layer_weights[op] = top_ops[op].opds[1]
            # 关联算子与偏置名（若有，在opds[2]）
            if len(top_ops[op].opds) > 2 and top_ops[op].opds[2] in self.module.all_weight_names:
                self.finetune_layer_bias[op] = top_ops[op].opds[2]
```

**功能与原理**：

- 精准筛选核心算子：聚焦 MatMul，排除分组卷积和指定排除的算子，确保优化资源集中在关键权重层。

- 关联权重 / 偏置名：为后续权重量化、阈值搜索、偏置校正提供算子与权重 / 偏置的映射关系。

#### （3）权重备份与初始缩放因子计算（backup\_weights）

```Python
def backup_weights(self):
    for op in self.finetune_layers:
        # 备份原始权重和偏置
        self.orig_weights[op] = copy.deepcopy(self.module.get_tensor(self.finetune_layer_weights[op]))
        if op in self.finetune_layer_bias:
            self.orig_bias[op] = copy.deepcopy(self.module.get_tensor(self.finetune_layer_bias[op]))
        oc = self.orig_weights[op].shape[0]  # 输出通道数（Conv）或输出维度（MatMul）
        # 计算初始权重缩放因子（absmax方法）
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            self.weights_scales[op] = np.max(np.abs(self.orig_weights[op].reshape(oc, -1)), axis=1)
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            self.weights_scales[op] = np.max(np.abs(self.orig_weights[op]))
        else:
            print("不支持的算子类型！")
            sys.exit(1)
        # 避免缩放因子为0（替换为1e-8）
        self.weights_scales[op] = np.where(self.weights_scales[op] > 1e-8, self.weights_scales[op], 1e-8)
```

**功能与原理**：

- 备份原始权重 / 偏置：确保搜索过程中可随时恢复，避免原始数据污染，同时为偏置校正提供原始参考。

- 初始缩放因子计算：采用传统 absmax 方法，为网格搜索提供初始阈值范围，保证搜索的稳定性。

4. 量化与恢复核心方法

```Python
def quant_weight(self, op, scale, bitwidth=8):
    # 权重量化（基于给定阈值scale）
    tmpweight = self.orig_weights[op].copy()
    step = scale / (2**(bitwidth-1)-1)  # 量化步长（scale映射到量化范围）
    tmpweight = np.round(tmpweight/step)  # 浮点→整数量化
    # 裁剪范围：INT4为-6~7（避免对称量化溢出），INT8为-127~127
    tmpweight = np.clip(tmpweight, -2**(bitwidth-1)+1, 2**(bitwidth-1)-1)
    tmpweight = tmpweight*step  # 整数→浮点反量化（模拟量化后权重）
    self.module.set_tensor(self.finetune_layer_weights[op], tmpweight)

def quant_active(self, active_name, scale, sample_idx, bitwidth=8):
    # 激活量化（基于给定阈值scale）
    tmpactive = self.ref_tensors.get(active_name, sample_idx).copy()
    step = scale / (2**(bitwidth-1)-1)
    tmpactive = np.round(tmpactive/step)  # 量化
    # 激活裁剪范围：INT4为-7~7，INT8为-128~127（支持全范围）
    tmpactive = np.clip(tmpactive, -2**(bitwidth-1), 2**(bitwidth-1)-1)
    tmpactive = tmpactive*step  # 反量化
    self.module.set_tensor(active_name, tmpactive)

def restore_weight(self, op):
    # 恢复原始权重（搜索单个阈值后重置）
    self.module.set_tensor(self.finetune_layer_weights[op], self.orig_weights[op])

def restore_active(self, active_name, sample_idx):
    # 恢复原始激活（搜索单个阈值后重置）
    tmpactive = self.ref_tensors.get(active_name, sample_idx).copy()
    self.module.set_tensor(active_name, tmpactive)
```

**功能与原理**：

- 模拟量化流程：实现 “量化 \- 裁剪 \- 反量化”，还原 INT4 量化后的权重 / 激活状态，用于计算量化输出与浮点输出的差异。

- 权重与激活裁剪差异：权重偏移最小值是为了避免对称量化溢出，提升量化稳定性；激活支持全范围裁剪，适配激活数据分布特性。

- 快速恢复机制：搜索单个阈值后恢复原始值，确保每次搜索仅改变目标输入的阈值，其他参数保持不变，搜索结果准确。

5. 相似度计算方法（量化精度评估）

```Python
def l2norm(self, pred, ref):
    # L2损失（负值，因需最大化相似度）
    return - np.sum((pred.flatten() - ref.flatten())**2)

def wl2norm(self, pred, ref):
    # 加权L2损失（按参考值绝对值加权）
    return - np.sum(np.abs(ref.flatten()) * (pred.flatten() - ref.flatten())**2)

def wsl2norm(self, pred, ref):
    # 符号加权L2损失（按参考值符号加权）
    return - np.sum(ref.flatten() * (ref.flatten() - pred.flatten())**2)

def calculate_sim(self, pred, ref):
    # 选择相似度评估指标（默认余弦相似度）
    COS = 1  # 余弦相似度（默认启用）
    L2NORM = 0  # L2损失
    WL2NORM = 0  # 加权L2损失
    WSL2NORM = 0  # 符号加权L2损失
    if COS:
        return cosine_sim(pred.flatten(), ref.flatten())
    elif L2NORM:
        return self.l2norm(pred.flatten(), ref.flatten())
    elif WL2NORM:
        return self.wl2norm(pred.flatten(), ref.flatten())
    elif WSL2NORM:
        return self.wsl2norm(pred.flatten(), ref.flatten())
```

**功能与原理**：

- 核心评估指标：默认使用余弦相似度，衡量量化输出与浮点输出的方向一致性，适配分类、特征匹配等任务的精度需求。

- 可选指标支持：提供 L2、加权 L2 等损失指标（负值，因需最大化 “相似度”），可根据模型特性切换，提升适配性。

6. 核心阈值搜索方法（search\_best\_th）

```Python
def search_best_th(self, op, opd):
    # 初始化原始激活阈值（absmax：max(|min|, |max|)）
    if len(self.orig_scales4) == 0:
        for op_ in self.scales4:
            self.orig_scales4[op_] = np.maximum(np.abs(self.scales4[op_][1]), np.abs(self.scales4[op_][2]))
        self.best_scales4 = self.orig_scales4  # 初始最优激活阈值=原始阈值

    opdname = self.parser.get_opds_by_op_name(op)[opd]  # 当前搜索的输入名
    otheropd = self.parser.get_opds_by_op_name(op)[1-opd]  # 另一个固定的输入名

    if opdname in self.finetune_layer_weights[op]:  # 搜索权重的最优阈值
        best_sim = np.zeros(self.steps)  # 存储每个候选阈值的相似度
        for thidx in np.arange(self.steps):
            # 生成候选阈值（alpha~beta范围内均匀分布）
            th = self.finetune_layer_weight_orig_ths[opdname]*self.alpha + \
                self.finetune_layer_weight_orig_ths[opdname]*(self.beta-self.alpha)/self.steps * (thidx + 1)
            for sample_idx in np.arange(self.num_sample):
                # 量化当前权重（使用候选阈值）
                self.quant_weight(op, th, 4)
                # 量化另一个输入（激活，使用当前最优阈值）
                self.quant_active(otheropd, self.best_scales4[otheropd], sample_idx, 4)
                # 执行算子，获取量化输出
                outputs = self.module.invoke_at(op).copy()
                # 恢复原始权重，获取浮点参考输出
                self.restore_weight(op)
                refoutputs = self.module.invoke_at(op).copy()
                # 累积相似度
                best_sim[thidx] += self.calculate_sim(outputs, refoutputs)
        # 选择相似度最大的阈值作为最优权重阈值
        best_th = (np.argmax(best_sim) + 1) * self.finetune_layer_weight_orig_ths[opdname]*(self.beta-self.alpha)/self.steps + \
            self.finetune_layer_weight_orig_ths[opdname]*self.alpha
        self.finetune_layer_weight_best_ths[opdname] = best_th

    else:  # 搜索激活的最优阈值
        best_sim = np.zeros(self.steps)
        for thidx in np.arange(self.steps):
            # 生成候选阈值（alpha~beta范围内均匀分布）
            th = self.orig_scales4[opdname]*(self.beta-self.alpha)/self.steps*(thidx+1) + \
                self.orig_scales4[opdname]*self.alpha
            for sample_idx in np.arange(self.num_sample):
                # 固定另一个输入：若为权重则量化权重，若为激活则量化激活
                if otheropd in self.finetune_layer_weights[op]:
                    otherth = self.finetune_layer_weight_best_ths[otheropd]
                    self.quant_weight(op, otherth, 4)
                else:
                    self.quant_active(otheropd, self.best_scales4[otheropd], sample_idx, 4)
                # 量化当前激活（使用候选阈值）
                self.quant_active(opdname, th, sample_idx, 4)
                # 执行算子，获取量化输出
                outputs = self.module.invoke_at(op).copy()
                # 恢复固定输入的原始值
                if otheropd in self.finetune_layer_weights[op]:
                    self.restore_weight(op)
                else:
                    self.restore_active(otheropd, sample_idx)
                # 恢复当前激活的原始值
                self.restore_active(opdname, sample_idx)
                # 获取浮点参考输出
                refoutputs = self.module.invoke_at(op).copy()
                # 累积相似度
                best_sim[thidx] += self.calculate_sim(outputs, refoutputs)
        # 选择相似度最大的阈值作为最优激活阈值
        best_th = (np.argmax(best_sim)+1) * self.orig_scales4[opdname]/self.steps + \
            self.orig_scales4[opdname]*self.alpha
        print(f'算子{op}激活{opdname}的最优相似度：{best_sim[np.argmax(best_sim)]}')
        self.best_scales4[opdname] = best_th
```

**功能与原理**：

- 核心逻辑：网格搜索 \+ 交替优化，固定一个输入的阈值，遍历另一个输入的候选阈值，通过相似度评估找到最优值，确保搜索针对性强。

- 候选阈值生成：在原始阈值的`alpha`\~`beta`范围内均匀分布`steps`个候选值，兼顾搜索精度和效率（100 步可覆盖关键区间，无遗漏）。

- 参考输出获取：每次搜索后恢复原始值，重新执行算子获取浮点输出，确保参考值不受当前候选阈值影响，评估准确。

7. 偏置校正方法（bias\_correction）

```Python
def bias_correction(self, op):
    # 计算量化权重与原始权重的差异
    tmpweight = self.orig_weights[op].copy()
    weightname = self.finetune_layer_weights[op]
    self.quant_weight(op, self.finetune_layer_weight_best_ths[weightname])  # 用最优阈值量化权重
    qweight = self.module.get_tensor(self.finetune_layer_weights[op])
    nweight = qweight - tmpweight  # 权重差异（量化引入的误差）
    self.module.set_tensor(self.finetune_layer_weights[op], nweight)  # 用差异权重替换原始权重

    # 计算差异权重带来的输出偏移
    tmpout = []
    for sample_idx in np.arange(self.num_sample):
        opdname = self.parser.get_opds_by_op_name(op)[0]  # 获取第一个输入（激活）
        tmpactive = self.ref_tensors.get(opdname, sample_idx)
        self.module.set_tensor(opdname, tmpactive)
        outputs = self.module.invoke_at(op)  # 执行算子，获取输出偏移
        if len(tmpout) == 0:
            tmpout = outputs
        else:
            tmpout += outputs
    tmpout /= self.num_sample  # 平均输出偏移

    # 调整偏置，抵消输出偏移
    shape = tmpout.shape
    origbias = self.orig_bias[op].copy()
    # 计算需要调整的偏置值（按输出通道平均偏移）
    tmpbias = np.sum(tmpout.reshape(-1, tmpout.shape[len(shape)-1]), axis=0).reshape(origbias.shape) / \
        (tmpout.size / tmpout.shape[-1])
    self.update_bias(op, tmpbias)  # 更新偏置
```

**功能与原理**：

- 核心目的：抵消权重量化带来的系统误差 —— 量化后的权重与原始权重存在差异，会导致输出整体偏移，通过调整偏置可抵消该偏移，提升精度。

- 计算逻辑：先通过 “差异权重” 单独计算输出偏移，再按输出通道平均偏移量调整偏置，确保每个通道的偏移都被有效抵消。

8. 整体优化流程（learn\_one、learning）

```Python
def learn_one(self, total):
    # 阈值搜索进度条配置
    pbar_detail = tqdm(np.arange(len(self.finetune_layers)))
    pbar_detail.set_description("搜索权重和激活阈值")
    layer_count = 0
    for op in self.finetune_layers:
        layer_count += 1
        pbar_detail.set_postfix_str(f"搜索中 {layer_count}/{len(self.finetune_layers)}")
        pbar_detail.update()
        # 迭代search_loop轮搜索（默认2轮，交替优化权重和激活）
        for loop in np.arange(self.search_loop):
            for opd in [0, 1]:  # 0：第一个输入，1：第二个输入
                self.search_best_th(op, opd)  # 搜索当前输入的最优阈值

    # 更新激活阈值（将最优阈值写入scales4）
    for op in self.best_scales4:
        if self.best_scales4[op] != self.scales4[op][0] and op in self.finetune_layers:
            print(f'激活{op}的阈值从{self.scales4[op][0]}更新为{self.best_scales4[op]}')
            self.scales4[op][0] = self.best_scales4[op]

    # 若同时优化权重，更新权重并执行偏置校正
    if not self.active_only:
        pbar_detail = tqdm(np.arange(len(self.finetune_layers)))
        pbar_detail.set_description("偏置校正")
        for op in self.finetune_layers:
            if op not in self.finetune_layer_weights:
                continue
            weightname = self.finetune_layer_weights[op]
            # 仅当权重阈值有变化时，更新权重并校正偏置
            if self.finetune_layer_weight_best_ths[weightname] != self.finetune_layer_weight_orig_ths[weightname]:
                print(f'权重{weightname}的阈值从{self.finetune_layer_weight_orig_ths[weightname]}更新为{self.finetune_layer_weight_best_ths[weightname]}')
                pbar_detail.update()
                # 按最优阈值量化权重
                weight = self.orig_weights[op].copy()
                bitwidth = 4
                qmax = 2**(bitwidth-1)-1
                qmin = -2**(bitwidth-1) + 1
                step = self.finetune_layer_weight_best_ths[weightname] / qmax
                weight = np.clip(np.round(weight / step), qmin, qmax) * step
                self.update_weight(op, weight)  # 更新权重
                # 若有偏置，执行偏置校正
                if op in self.finetune_layer_bias:
                    pbar_detail.set_postfix_str(f"校正偏置 {op}")
                    pbar_detail.update()
                    self.bias_correction(op)
    else:
        # 仅优化激活时，将权重最优阈值写入文件（供后续使用）
        f = open('new_w_th.txt', 'w')
        for op in self.finetune_layers:
            if op not in self.finetune_layer_weights:
                continue
            weightname = self.finetune_layer_weights[op]
            f.write(f'{weightname} {self.finetune_layer_weight_best_ths[weightname]}\n')
        f.close()

def learning(self):
    total = len(self.finetune_layers)
    self.learn_one(total)  # 执行阈值搜索和偏置校正
    self.save_weights()  # 保存优化后的权重
```

**功能与原理**：

- 多轮交替搜索：每个层执行`search_loop`轮搜索，每轮交替优化两个输入的阈值，避免单次搜索陷入局部最优，提升阈值稳定性。

- 条件更新逻辑：仅当最优阈值与原始阈值存在差异时，才更新权重 / 激活阈值，减少无效操作。

- 偏置校正触发：仅在 “同时优化权重” 且权重阈值变化时执行，确保校正针对性强，不做无用功。

- 结果保存：优化后备份原始权重文件，保存新权重，同时支持仅输出权重阈值文件，适配不同部署需求。

### 三、总结

该代码是 TPU\-MLIR 工具链中针对 INT4 量化的轻量级优化模块，核心价值在于以 “网格搜索 \+ 交替优化 \+ 偏置校正” 的简单逻辑，在低计算开销下提升量化精度，适配对部署效率有要求的场景。核心亮点如下：

1. **简单高效**：采用网格搜索而非复杂数值优化，实现简单、计算速度快，无需手动调优优化器参数，易集成部署。

2. **双维度优化**：支持同时优化权重和激活的阈值，比单一维度优化更能降低量化失真，精度提升明显。

3. **灵活适配**：支持 “仅优化激活” 或 “权重 \+ 激活 \+ 偏置校正” 模式，可根据模型精度需求和部署成本灵活选择。

4. **稳定可靠**：通过多轮交替搜索、相似度累积评估、条件更新逻辑，确保最优阈值的稳定性和有效性。

5. **工程化友好**：包含权重备份、阈值日志、进度条显示等功能，调试和使用便捷，适配实际工程场景。

整体流程从初始化、阈值搜索、交替优化到偏置校正、结果保存，形成完整的量化优化闭环，最终输出可直接用于 INT4 量化部署的权重和激活阈值，在精度与效率间取得良好平衡。



## comq\.py

### 一、代码整体原理逻辑阐述

该代码是**TPU\-MLIR 量化工具链中基于协同优化的 INT4 权重量化模块（COMQ）**，核心继承自 LossAwareQuant（LAPQ），聚焦 MatMul 算子的权重精细化量化。核心目标是通过 “输入 \- 权重协同优化” 策略，逐列量化权重，最小化矩阵乘法（X@W^T）的量化误差，而非单独优化权重本身，从而提升 INT4 量化模型的精度。

整体逻辑围绕 “协同量化闭环” 展开，核心流程如下：

1. **初始化配置**：复用 LAPQ 的层筛选、权重备份、模型解析等逻辑，筛选待优化的 MatMul 层，初始化 COMQ 量化器（COMQ\_Layer）。

2. **输入数据收集**：拼接目标层的所有样本输入数据，形成全局输入矩阵，为协同量化提供数据支撑。

3. **协同权重量化**：对每个 MatMul 层，基于输入数据与权重的协同关系，采用贪心或非贪心策略逐列量化权重 —— 通过误差累积项（u）跟踪量化对输出的影响，动态调整每列权重的量化值，确保整体矩阵乘法误差最小。

4. **量化结果整理**：恢复权重的原始顺序（贪心策略下），将量化后的权重更新到模型中。

5. **结果保存**：备份原始权重文件，保存优化后的量化权重，用于后续 INT4 量化部署。

核心设计思想：传统权重量化仅关注权重本身的失真（如 Lp 损失），而 COMQ 考虑权重与输入的协同作用 —— 矩阵乘法的输出误差由输入和权重共同决定，逐列量化时引入误差累积项，动态补偿前序量化对后续列的影响，尤其适配 Transformer、CNN 等依赖 MatMul 的模型。

### 二、代码逐部分详细分析

6. 核心量化层类（COMQ\_Layer）

封装单 MatMul 层的协同量化逻辑，是 COMQ 的核心载体，负责权重初始化、量化候选值生成、逐列协同量化及结果整理。

#### （1）初始化方法（**init**）

```Python
class COMQ_Layer:
    def __init__(self, op, weight, scalar, bits=4):
        self.name = op  # 算子名
        self.W = torch.tensor(weight.copy()).T  # 原始权重（转置为[输入维度, 输出维度]，适配矩阵乘法）
        self.Q = torch.tensor(weight.copy()).T  # 量化后权重（初始与原始权重一致）
        self.max_w = torch.ones_like(torch.max(self.Q, dim=1)[0])  # 权重最大值（按输出维度）
        self.min_w = torch.ones_like(torch.min(self.Q, dim=1)[0])  # 权重最小值（按输出维度）
        # 计算权重的全局最大绝对值（初始化量化范围）
        max_w = torch.max(self.Q)
        min_w = torch.min(self.Q)
        max_w = torch.max(max_w.abs(), min_w.abs())
        self.max_w *= max_w
        self.min_w *= -max_w
        self.bits = bits  # 量化位宽（默认4）
        self.bit_code = []  # 量化候选值列表（每个输出维度对应一组候选值）
        self.device = 'cpu'  # 计算设备（默认CPU）
        # 计算量化步长（INT4：-7~7，共15个值，步长=（max-min）/(2^bits-2)）
        delta = (self.max_w - self.min_w)/(2**self.bits - 2)
        starts = (self.min_w[0] / delta).round()  # 量化起始值（按步长对齐）
        ends = starts + (2**self.bits - 1)  # 量化结束值
        # 生成每个输出维度的量化候选值（scalar为缩放系数，默认1.0）
        self.bit_code = scalar * torch.stack([
            torch.linspace(start, end, steps=2**self.bits-1)
            for start, end in zip(starts, ends)
        ]).to(self.device)
        self.bit_code = self.bit_code * delta.unsqueeze(1)  # 映射到原始权重范围
        self.greedy = True  # 是否启用贪心量化策略（默认启用）
        self.u = None  # 误差累积项（跟踪量化对输出的影响）
```

**功能与原理**：

- 权重转置：将原始权重（通常为 \[输出维度，输入维度\]）转置为 \[输入维度，输出维度\]，适配矩阵乘法（X@W^T）的计算逻辑。

- 量化范围与候选值：基于权重全局最大绝对值确定量化范围（\-max\~max），生成 INT4 对应的 15 个候选值（bit\_code），为逐列量化提供选择空间。

- 误差累积项（u）：预留用于跟踪前序列量化对输出的误差，后续逐列量化时动态补偿，实现协同优化。

- 贪心策略开关：默认启用，可切换为非贪心策略，适配不同权重分布。

#### （2）量化候选值匹配（\_quantizer）

```Python
def _quantizer(self, target_val):
    # 扩展目标值形状，与量化候选值对齐（[输出维度] → [输出维度, 15]）
    input_vector_expanded = target_val.unsqueeze(-1).expand_as(self.bit_code)
    # 计算目标值与每个候选值的绝对误差
    differences = torch.abs(self.bit_code - input_vector_expanded)
    # 找到误差最小的候选值索引
    _, min_indices = torch.min(differences, dim=1)
    # 提取最优候选值（高效索引）
    closest_values_efficient = self.bit_code[torch.arange(self.bit_code.size(0)), min_indices]
    return closest_values_efficient
```

**功能与原理**：

- 核心是 “最近邻匹配”：对每个输出维度的目标量化值（target\_val），从 15 个候选值中选择误差最小的作为最终量化值。

- 高效计算：通过张量扩展和索引操作，避免循环遍历，提升量化速度。

#### （3）核心协同量化（\_quant\_layer）

```Python
def _quant_layer(self, X):
    # 输入数据转换为Tensor并移至目标设备
    X = torch.tensor(X).to(self.device)
    print(f'算子{self.name} 输入形状{X.shape} 权重形状{self.Q.shape}')
    # 初始化误差累积项u（形状：[样本数, 输出维度]，跟踪每个样本的输出误差）
    if self.u == None:
        self.u = torch.zeros(X.shape[0], self.W.shape[0]).to(self.device)

    if self.greedy:
        # 贪心策略：按“输入范数×权重绝对值”排序，优先量化影响大的权重列
        need_perm_matrix = torch.norm(X, dim=0).unsqueeze(0) * self.W.abs()  # 权重列重要性评分
        perm = torch.argsort(need_perm_matrix, dim=1, descending=True)  # 降序排序索引
        del need_perm_matrix
        self.invperm = torch.argsort(perm)  # 逆排序索引（用于后续恢复原始顺序）
        # 按排序索引重排权重（原始权重→排序后权重）
        self.W = self.W.gather(1, perm)
        self.Q = self.Q.gather(1, perm)

        # 逐列量化排序后的权重
        for idx, w in enumerate(self.W.T):
            # 按当前列的排序索引重排输入数据
            X_permed = torch.index_select(X.T, 0, perm[:, idx]).T
            # 误差补偿：减去当前列原始权重与初始量化值的误差对输出的影响
            self.u -= X_permed * (w - self.Q[:, idx]).unsqueeze(0)
            # 计算当前列权重与输入的乘积
            w_x = X_permed * w.unsqueeze(0)
            # 计算目标量化值：使（w_x + u）与输入的乘积误差最小
            target_val = torch.sum((w_x + self.u) * X_permed, dim=0) / torch.sum(X_permed*X_permed, dim=0)
            # 匹配最优量化候选值
            q = self._quantizer(target_val)
            # 更新误差累积项：加上当前列量化误差对输出的影响
            self.u.add_(X_permed * (w - q).unsqueeze(0))
            # 保存当前列的量化值
            self.Q[:, idx] = q
            del X_permed  # 释放内存
    else:
        # 非贪心策略：直接逐列量化，不排序
        for idx, w in enumerate(self.W.T):
            # 误差补偿：减去当前列原始权重与初始量化值的误差
            self.u -= torch.outer(X[:, idx], w - self.Q[:, idx])
            # 计算误差累积项与输入的乘积
            u_x = torch.mv(self.u.T, X[:, idx])
            # 计算当前列权重与输入的乘积
            w_x = torch.outer(X[:, idx], w)
            # 计算目标量化值
            target_val = (u_x + torch.mv(w_x.T, X[:, idx])) / (torch.sum(X[:, idx]**2))
            # 匹配最优量化候选值
            q = self._quantizer(target_val)
            # 更新误差累积项
            self.u.add_(torch.outer(X[:, idx], w - q))
            # 保存当前列的量化值
            self.Q[:, idx] = q
```

**功能与原理**：

- 贪心策略核心：先按 “输入范数 × 权重绝对值” 对权重列排序，优先量化对输出影响大的列，提升量化精度和效率。

- 协同优化体现：通过误差累积项 u，逐列跟踪量化误差对输出的影响 —— 前序列量化后，u 记录误差，后续列量化时针对性调整目标值，确保整体误差最小。

- 目标量化值计算：基于 “（当前列乘积 \+ 累积误差）与输入的乘积误差最小” 推导，而非直接量化原始权重，实现输入 \- 权重协同。

- 内存优化：贪心策略中删除中间变量 X\_permed，避免大输入数据导致的内存溢出。

#### （4）量化结果整理（finalize）

```Python
def finalize(self):
    # 贪心策略下，恢复权重的原始列顺序
    if self.greedy:
        self.Q = self.Q.gather(1, self.invperm)
        self.W = self.W.gather(1, self.invperm)
    # 权重转置回原始形状（[输出维度, 输入维度]）
    self.Q = self.Q.T
    self.W = self.W.T
```

**功能与原理**：

- 恢复原始顺序：贪心策略中对权重列排序量化，最终需通过逆排序索引（invperm）恢复原始列顺序，确保模型结构不变。

- 权重转置：将量化后的权重转置回原始形状，适配模型的权重存储和推理逻辑。

7. COMQ 主类（Comq）

继承自 LossAwareQuant（LAPQ），重写 learning 方法，聚焦 MatMul 层的协同权重量化，复用 LAPQ 的工程化逻辑（层筛选、权重备份、更新保存等）。

```Python
class Comq(LossAwareQuant):
    def learning(self):
        total = len(self.finetune_layers)
        quantizer = {}  # 存储每个MatMul层的COMQ量化器
        # 为每个待优化的MatMul层初始化COMQ量化器
        for op in self.finetune_layers:
            if op in self.finetune_layer_weights:  # 仅处理带权重的MatMul层
                quantizer[op] = COMQ_Layer(op, self.orig_weights[op], 1.0, 4)
        
        # 进度条配置
        pbar_detail = tqdm(np.arange(len(self.finetune_layer_weights)))
        pbar_detail.set_description("COMQ量化")
        layer_cnt = 0
        for op in self.finetune_layers:
            if op in self.finetune_layer_weights:
                layer_cnt += 1
                pbar_detail.set_postfix_str(f"量化中 {layer_cnt}/{len(self.finetune_layer_weights)}")
                pbar_detail.update()
                
                # Step 1：收集当前层的所有输入数据（拼接所有样本）
                input_name = self.parser.get_pre_op_by_op_name(op)[0]  # 获取输入算子名
                input_data = self.ref_tensors.get(input_name, 0)  # 第一个样本的输入数据
                for idx in np.arange(1, self.num_sample):
                    # 拼接后续样本的输入数据（按样本维度堆叠）
                    input_data = np.concatenate((input_data, self.ref_tensors.get(input_name, idx)), axis=0)
                # Reshape为[总样本数, 输入维度]，适配矩阵乘法
                input_data = input_data.reshape(-1, input_data.shape[-1])
                
                # Step 2：执行协同量化
                quantizer[op]._quant_layer(input_data)
                del input_data  # 释放内存
                quantizer[op].finalize()  # 整理量化结果（恢复原始顺序、转置）
                
                # Step 3：更新量化后的权重
                self.update_weight(op, quantizer[op].Q.numpy())
        
        # 保存优化后的权重（复用LAPQ的save_weights方法）
        self.save_weights()
```

**功能与原理**：

- 复用 LAPQ 基础能力：继承层筛选、权重备份、update\_weight、save\_weights 等方法，无需重复开发工程化逻辑，聚焦协同量化核心。

- 输入数据收集：拼接当前层所有样本的输入数据，形成全局输入矩阵，确保量化时考虑所有样本的统计特性，提升量化泛化性。

- 逐层量化：对每个 MatMul 层，调用 COMQ\_Layer 的协同量化逻辑，生成最优量化权重并更新。

- 轻量高效：仅优化权重，不涉及激活阈值调整，相比 LAPQ 的双维度优化，计算开销更低，同时通过协同策略保证精度。

### 三、总结

该代码是 TPU\-MLIR 工具链中针对 MatMul 算子的 INT4 权重量化高阶优化模块，核心价值在于 “输入 \- 权重协同优化”，解决传统独立权重量化忽略输入影响的问题。核心亮点如下：

1. **协同优化策略**：量化时引入误差累积项（u），逐列动态补偿前序量化误差，确保矩阵乘法整体输出误差最小，而非单列权重失真最小。

2. **贪心策略适配**：默认启用权重列排序（按输入范数 × 权重绝对值），优先量化关键列，在精度与效率间取得平衡，可切换非贪心策略适配不同场景。

3. **工程化复用**：继承 LAPQ 的层筛选、权重管理、结果保存等逻辑，开发成本低，易集成到现有量化流程。

4. **轻量高效**：仅聚焦权重量化，不涉及激活优化，计算开销低于 LAPQ，同时精度优于传统独立量化（如 absmax、Lp 范数）。

5. **适配性强**：基于 PyTorch 张量计算，支持 CPU 设备，适配不同输入维度和样本量，尤其适合 Transformer 等 MatMul 密集型模型。



### data/vision/ 数据预处理（3个文件）

## datasets\.py

### 一、代码整体原理逻辑阐述

该代码实现了一个**图像文件夹数据集加载类（ImageFolderDataset）**，核心功能是从按类别组织的文件夹结构中加载图像文件，支持图像格式筛选、色彩模式控制（灰度 / 彩色）和自定义数据变换，为图像处理或模型训练提供标准化的图像数据输入接口。

整体逻辑围绕 “目录解析 \- 文件筛选 \- 图像加载” 展开，核心流程如下：

1. **初始化配置**：接收根目录路径、色彩模式（flag）和自定义变换函数（transform），定义支持的图像扩展名（\.jpg, \.jpeg, \.png）。

2. **目录遍历与文件筛选**：递归遍历根目录下的子文件夹（每个子文件夹代表一个类别），筛选出符合扩展名的图像文件，存储文件路径到列表中。

3. **图像加载与处理**：通过索引访问时，使用 OpenCV 加载对应路径的图像，按配置的色彩模式处理，并应用自定义变换（如归一化、尺寸调整等）。

4. **迭代与长度支持**：实现迭代器接口和长度方法，方便按批次或顺序访问数据集。

核心设计思想：采用 “约定优于配置” 的原则，假设图像按 “根目录 / 类别 / 图像文件” 的结构存储，无需额外的标注文件，通过文件夹名隐式区分类别，简化数据集准备流程，适合快速构建原型或处理中小型图像数据集。

### 二、代码逐部分详细分析

1. 初始化方法（**init**）

```Python
class ImageFolderDataset:
    """A dataset for loading image files stored in a folder structure.

    like::

        root/car/0001.jpg
        root/car/xxxa.jpg
        root/car/yyyb.jpg
        root/bus/123.jpg
        root/bus/023.jpg
        root/bus/wwww.jpg

    Parameters
    ----------
    root : str
        Path to root directory.
    flag : {0, 1}, default 1
        If 0, always convert loaded images to greyscale (1 channel).
        If 1, always convert loaded images to colored (3 channels).
    transform : callable, default None
        A function that takes data and label and transforms them::

            transform = lambda data, label: (data.astype(np.float32)/255, label)

    Attributes
    ----------
    synsets : list
        List of class names. `synsets[i]` is the name for the integer label `i`
    items : list of tuples
        List of all images in (filename, label) pairs.
    """
    def __init__(self, root, flag=1, transform=None):
        self._root = os.path.expanduser(root)  # 解析用户路径（支持~符号）
        self._flag = flag  # 色彩模式：1为彩色，0为灰度
        self._transform = transform  # 自定义变换函数
        self._exts = [".jpg", ".jpeg", ".png"]  # 支持的图像扩展名
        self._list_images(self._root)  # 遍历目录并收集图像文件
```

**功能与原理**：

- 路径处理：使用`os.path.expanduser`解析`~`符号，确保在不同操作系统下路径正确。

- 核心参数初始化：明确色彩模式和支持的文件格式，为后续文件筛选和图像加载奠定基础。

- 触发目录遍历：通过`_list_images`方法初始化图像文件列表，确保实例化后即可访问数据。

2. 目录遍历与文件筛选（\_list\_images）

```Python
def _list_images(self, root):
    self.items = []  # 存储图像文件路径的列表

    # 遍历根目录下的所有子项（按名称排序，保证一致性）
    for folder in sorted(os.listdir(root)):
        path = os.path.join(root, folder)  # 子文件夹路径
        if not os.path.isdir(path):  # 跳过非目录项（如文件）
            warnings.warn(
                "Ignoring %s, which is not a directory." % path, stacklevel=3
            )
            continue
        # 遍历子文件夹下的所有文件（按名称排序）
        for filename in sorted(os.listdir(path)):
            filename = os.path.join(path, filename)  # 完整文件路径
            ext = os.path.splitext(filename)[1]  # 获取文件扩展名
            # 筛选支持的图像格式（不区分大小写）
            if ext.lower() not in self._exts:
                warnings.warn(
                    "Ignoring %s of type %s. Only support %s"
                    % (filename, ext, ", ".join(self._exts))
                )
                continue
            self.items.append(filename)  # 符合条件的文件路径加入列表
```

**功能与原理**：

- 层级遍历：先遍历根目录下的子文件夹（假设每个子文件夹对应一个类别），再遍历子文件夹下的文件，符合 “类别 \- 图像” 的目录结构约定。

- 排序保证：对文件夹和文件名进行`sorted`排序，确保不同运行环境下文件列表顺序一致，避免数据加载顺序随机化。

- 格式筛选：仅保留`.jpg`, `.jpeg`, `.png`格式的文件，对其他格式发出警告并忽略，保证加载的文件可被 OpenCV 正确处理。

- 容错处理：通过`warnings.warn`提示非目录项和不支持的文件类型，提升代码健壮性，方便用户排查数据问题。

3. 图像加载（**getitem**）

```Python
def __getitem__(self, idx):
    # 用OpenCV加载图像，flag控制色彩模式（1为彩色，0为灰度）
    img = cv2.imread(self.items[idx], self._flag)
    # 应用自定义变换（如归一化、尺寸调整等）
    if self._transform is not None:
        return self._transform(img)
    return img
```

**功能与原理**：

- 核心加载逻辑：通过`cv2.imread`读取图像，`self._flag`参数控制加载模式（`1`加载彩色图（BGR 格式），`0`加载灰度图），直接适配 OpenCV 的 API 设计。

- 变换支持：允许用户传入自定义变换函数（如将图像转为 float32 类型、归一化到 \[0,1\] 范围、 resize 等），灵活适配不同模型的输入要求。

- 注意点：OpenCV 默认加载彩色图像为 BGR 通道顺序，而很多深度学习框架（如 PyTorch、TensorFlow）默认期望 RGB 格式，需在`transform`中显式转换（如`cv2.cvtColor(img, cv2.COLOR_BGR2RGB)`）。

4. 迭代器与长度支持

```Python
def __iter__(self):
    # 实现迭代器接口，支持for循环遍历数据集
    for i in range(len(self)):
        yield self[i]

def __len__(self):
    # 返回数据集大小（图像数量）
    return len(self.items)
```

**功能与原理**：

- 迭代器支持：通过`iter`方法允许用`for img in dataset`的方式遍历所有图像，简化数据访问流程。

- 长度支持：`len`方法返回`self.items`的长度，即图像总数，方便计算批次大小、迭代次数等。

### 三、总结

该代码实现了一个轻量级的图像文件夹数据集加载器，核心价值在于**简化按类别组织的图像数据加载流程**，无需手动处理标注文件，通过目录结构隐式获取类别信息（尽管当前代码未显式存储标签，需后续扩展）。核心亮点如下：

1. **约定式设计**：基于 “根目录 / 类别 / 图像” 的目录结构约定，自动解析数据，无需额外标注文件，降低数据集准备成本。

2. **灵活配置**：支持灰度 / 彩色模式切换和自定义变换函数，适配不同模型对输入图像的格式要求。

3. **健壮性**：通过格式筛选、非目录项忽略和警告提示，增强对不规范数据的容错能力。

4. **易用性**：实现迭代器和长度接口，支持索引访问和 for 循环遍历，符合 Python 数据集的使用习惯。

5. **轻量高效**：依赖 OpenCV 进行图像加载，速度快，代码量少，易于集成到现有数据处理流程中。

## transforms\.py

### 一、代码整体原理逻辑阐述

该代码是**TPU\-MLIR 工具链中的图像预处理变换模块**，提供了一系列标准化的图像变换接口，核心用于模型训练或推理前的图像数据预处理。通过封装 OpenCV 的底层图像处理能力，实现了 “组合变换、类型转换、归一化、旋转、缩放、裁剪” 等常用功能，所有变换类统一通过`call`方法接收图像输入并返回处理后结果，支持灵活组合与链式调用。

核心设计逻辑如下：

1. **接口统一性**：所有变换类均实现`call`方法，输入为`(H×W×C)`格式的 numpy 数组（图像），输出为处理后的同格式数组，确保调用方式一致。

2. **功能模块化**：每个类对应单一变换功能，职责清晰（如`Normalize`仅做归一化，`Resize`仅做缩放），便于单独使用或组合。

3. **灵活组合性**：通过`Compose`类可将多个变换按顺序串联，形成完整的预处理流程，适配不同模型的输入要求。

4. **工程化适配**：支持边界情况处理（如超出图像范围的裁剪填充、保持比例的缩放填充），兼容标量 / 元组类型的参数输入，提升实用性。

核心目标是为图像数据提供 “标准化、可复用、易扩展” 的预处理能力，让用户无需关注底层图像处理细节，快速构建适配模型的预处理流程。

### 二、代码逐部分详细分析

1. 组合变换类（Compose）

```Python
class Compose(object):
    def __init__(self, transforms):
        super(Compose, self).__init__()
        self.transforms = transforms  # 存储待组合的变换列表

    def __repr__(self):
        # 自定义打印格式，便于调试时查看变换组合顺序
        def _indent(s_, numSpaces):
            s = s_.split("\n")
            if len(s) == 1:
                return s_
            first = s.pop(0)
            s = [first] + [(numSpaces * " ") + line for line in s]
            return "\n".join(s)
        s = "{name}(\n{modstr}\n)"
        modstr = "\n".join(
            [f"  ({key}): {_indent(func.__repr__(), 2)}" 
             for key, func in enumerate(self.transforms)]
        )
        return s.format(name=self.__class__.__name__, modstr=modstr)

    def __getitem__(self, key):
        return self.transforms[key]  # 支持索引访问单个变换

    def __len__(self):
        return len(self.transforms)  # 支持获取变换组合长度

    def __call__(self, x):
        if len(self) == 0:
            return x  # 无变换时直接返回输入
        _x = self[0](x)
        for fun in self.transforms[1:]:
            _x = fun(_x)  # 按顺序执行每个变换，前一个输出作为后一个输入
        return _x
```

**功能与原理**：

- 核心作用：串联多个变换，实现 “一站式” 预处理流程（如`Resize→CenterCrop→Normalize`）。

- 辅助功能：支持索引访问（如`compose[0]`获取第一个变换）、长度计算（`len(compose)`获取变换个数），便于调试和灵活调整。

- 兼容性：无变换时直接返回输入，避免空列表报错，提升鲁棒性。

2. 数据类型转换类（Cast）

```Python
class Cast:
    def __init__(self, dtype=np.float32):
        self._dtype = dtype  # 目标数据类型（默认float32，适配模型输入）

    def __call__(self, x):
        return x.astype(self._dtype)  # 直接调用numpy的astype转换类型
```

**功能与原理**：

- 核心作用：将图像数据转换为模型要求的数据类型（如 uint8→float32）。

- 简洁高效：复用 numpy 的类型转换能力，无额外逻辑，确保转换速度。

- 灵活适配：支持所有 numpy 兼容的 dtype（如 np\.uint32、np\.int8），满足不同场景需求。

3. 归一化类（Normalize）

```Python
class Normalize:
    def __init__(self, mean=0.0, std=1.0):
        self._mean = mean  # 均值（标量或多通道元组）
        self._std = std    # 标准差（标量或多通道元组）

    def __call__(self, x):
        # 转换为与输入x相同的数据类型，避免数值精度问题
        mean = np.array(self._mean, dtype=x.dtype)
        std = np.array(self._std, dtype=x.dtype)
        return (x - mean) / std  # 逐元素归一化：(输入-均值)/标准差
```

**功能与原理**：

- 核心作用：按通道对图像进行归一化，消除数据量纲影响，加速模型收敛。

- 参数灵活性：支持标量（所有通道共用同一均值 / 标准差）或元组（每个通道单独配置），适配 RGB 等多通道图像。

- 数值安全性：均值和标准差转换为输入数据类型，避免 float 与 int 混合计算导致的精度损失。

4. 旋转变换类（Rotate）

```Python
class Rotate:
    def __init__(self, rotation_degrees):
        self._rotation_degrees = rotation_degrees  # 旋转角度（逆时针，单位：度）

    def __call__(self, x):
        # 计算图像中心点（(H-1)/2, (W-1)/2）
        center = tuple((np.array(x.shape[0:2]) - 1) / 2)
        # 生成旋转矩阵：中心点+角度+缩放因子（1.0表示不缩放），opencv的函数，cv2.getRotationMatrix2D(center, angle, scale)
        rot_mat = cv2.getRotationMatrix2D(center, self._rotation_degrees, 1.0)
        # 应用旋转：保持原始图像尺寸x.shape[0:2]保持与原图尺寸一致，双线性插值cv2.INTER_LINEAR，对图像应用仿射变换
        return cv2.warpAffine(x, rot_mat, x.shape[0:2], flags=cv2.INTER_LINEAR)
```

**功能与原理**：

- 核心作用：按指定角度逆时针旋转图像，保持原始尺寸。

- 关键细节：

    - 旋转中心为图像中心点，符合视觉直觉。

    - 使用双线性插值（cv2\.INTER\_LINEAR），平衡旋转速度和图像质量。

    - 依赖 OpenCV 的`getRotationMatrix2D`（生成旋转矩阵）和`warpAffine`（应用仿射变换），确保旋转效率。

5. 缩放变换类（Resize）

```Python
class Resize:
    def __init__(self, size, keep_ratio=False, interpolation=cv2.INTER_LINEAR):
        self._keep = keep_ratio  # 是否保持图像宽高比
        self._size = size if isinstance(size, tuple) else (size, size)  # 目标尺寸(W, H)
        self._interpolation = interpolation  # 插值方法（默认双线性）

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self._keep:
            h, w, c = x.shape
            w_n, h_n = self._size  # 目标宽、高
            scale_h, scale_w = h_n / h, w_n / w  # 高、宽缩放因子

            if scale_h == scale_w:
                return cv2.resize(x, self._size, self._interpolation)  # 比例一致直接缩放

            # 按最小缩放因子缩放，避免图像超出目标尺寸
            scale = min(scale_h, scale_w)
            W, H = int(w * scale), int(h * scale)  # 缩放后实际宽、高
            y = cv2.resize(x, (W, H), self._interpolation)  # 缩放图像
            out = np.zeros((h_n, w_n, c))  # 生成目标尺寸的零填充图像
            # 计算居中填充的起始位置
            h_c = int(h_n / 2 - H / 2)
            w_c = int(w_n / 2 - W / 2)
            out[h_c:h_c+H, w_c:w_c+W, :] = y  # 填充缩放后的图像
            return out

        # 不保持比例：直接缩放到目标尺寸
        return cv2.resize(x, self._size, self._interpolation)
```

**功能与原理**：

- 核心作用：调整图像尺寸，支持 “固定尺寸” 和 “保持比例 \+ 填充” 两种模式。

- 保持比例模式（keep\_ratio=True）：

    - 按最小缩放因子缩放图像，确保图像完全包含在目标尺寸内。

    - 空白区域用零填充，图像居中放置，适合需要固定输入尺寸但不想拉伸图像的场景（如模型推理）。

- 不保持比例模式：直接拉伸 / 压缩图像到目标尺寸，适合对比例不敏感的场景。

- 插值适配：支持 OpenCV 的多种插值方法（如双线性、最近邻），可根据需求调整速度和质量。

6. 裁剪 \- 缩放组合类（CropResize）

```Python
class CropResize:
    def __init__(self, x, y, width, height, size=None, interpolation=1):
        self._x = x  # 裁剪区域左边界
        self._y = y  # 裁剪区域上边界
        self._width = width  # 裁剪区域宽度
        self._height = height  # 裁剪区域高度
        self._size = size  # 裁剪后目标尺寸（可选）
        self._interpolation = interpolation  # 插值方法

    def __call__(self, x):
        def crop(x, _x, _y, _w, _h):
            h, w, c = x.shape
            _start = np.array((_y, _x))  # 裁剪起始位置（y行，x列）
            _shape = np.array((_h, _w))  # 裁剪区域尺寸（高，宽）
            _end = np.min([_start + _shape, np.array((h, w))], axis=0)  # 裁剪结束位置（不超出图像）
            h_crop, w_crop = _end - _start  # 实际裁剪尺寸
            out = np.zeros((_h, _w, c))  # 生成目标裁剪尺寸的零填充图像
            out[:h_crop, :w_crop, :] = x[_y:_y+h_crop, _x:_x+w_crop, :]  # 填充裁剪区域
            return out

        out = crop(x, self._x, self._y, self._width, self._height)  # 先裁剪
        if self._size:
            # 裁剪后缩放（复用Resize类的逻辑）
            out = Resize(self._size, False, self._interpolation)(out)
        return out
```

**功能与原理**：

- 核心作用：先按指定坐标裁剪图像（超出部分填零），再可选缩放到目标尺寸，实现 “裁剪 \+ 缩放” 一站式处理。

- 裁剪逻辑：

    - 支持超出原始图像范围的裁剪，超出部分自动填充零，无需手动处理边界。

    - 裁剪坐标以 “左边界 x、上边界 y” 定义，符合图像处理的常规习惯。

- 灵活扩展：裁剪后可指定新尺寸，适配需要精准截取图像区域并标准化尺寸的场景（如目标检测中的 ROI 处理）。

7. 中心裁剪类（CenterCrop）

```Python
class CenterCrop:
    def __init__(self, size, interpolation=1):
        # 目标尺寸统一转为元组（W, H）
        self._args = (size if isinstance(size, tuple) else (size, size), interpolation)

    def __call__(self, x):
        size, interpolation = self._args
        h_i, w_i, c = x.shape  # 输入图像尺寸（H, W, C）
        w_o, h_o = size  # 目标尺寸（W, H）

        # 输入尺寸小于目标尺寸：先缩放至目标尺寸（保持比例）
        if w_o > w_i and h_o > h_i:
            return Resize((w_o, h_o), True, interpolation)(x)

        # 计算裁剪范围：输入与目标的最小交集尺寸（避免超出）
        shape_io = np.array([[h_i, w_i], [h_o, w_o]])
        shape_union = np.min(shape_io, axis=0)  # 交集尺寸（H, W）
        out = np.zeros((h_o, w_o, c))  # 生成目标尺寸的零填充图像

        # 计算中心裁剪的起始位置（输入和输出的中心对齐）
        shape_range_start = (shape_io - shape_union) / 2  # 起始偏移
        h_i_s, w_i_s, h_o_s, w_o_s = shape_range_start.flatten().astype(int)
        h_i_e, w_i_e, h_o_e, w_o_e = (shape_range_start + shape_union).flatten().astype(int)

        # 填充裁剪后的图像（中心对齐）
        out[h_o_s:h_o_e, w_o_s:w_o_e, :] = x[h_i_s:h_i_e, w_i_s:w_i_e, :]
        return out
```

**功能与原理**：

- 核心作用：从图像中心裁剪指定尺寸，输入尺寸小于目标时自动缩放，确保输出尺寸固定。

- 关键逻辑：

    - 中心对齐：裁剪区域的中心与输入图像中心一致，符合视觉焦点需求（如分类模型的图像预处理）。

    - 尺寸适配：输入小于目标时，通过`Resize`类保持比例缩放至目标尺寸，避免拉伸；输入大于目标时，裁剪中心区域。

- 零填充处理：裁剪后不足目标尺寸的部分用零填充，确保输出尺寸严格符合要求。

### 三、总结

该代码是一套轻量、高效、灵活的图像预处理模块，核心价值在于为 TPU\-MLIR 工具链提供标准化的图像数据预处理能力，适配模型训练和推理的全流程。核心亮点如下：

1. **接口统一简洁**：所有变换类通过`call`方法接收图像输入，调用方式一致，降低使用成本。

2. **功能覆盖全面**：涵盖 “组合、类型转换、归一化、旋转、缩放、裁剪” 等核心预处理操作，满足绝大多数图像任务需求。

3. **工程化适配性强**：

    - 支持标量 / 元组参数，适配单通道 / 多通道图像。

    - 处理边界情况（超出范围裁剪、尺寸不匹配缩放），无需用户额外处理。

    - 依赖 OpenCV 底层实现，兼顾处理速度和图像质量。

4. **可扩展性好**：单个变换类职责单一，新增变换时只需实现`call`方法，即可无缝集成到`Compose`组合中。

该模块是连接原始图像数据与模型输入的关键桥梁，尤其适合需要快速构建预处理流程的场景（如模型推理部署、快速原型开发），同时兼顾灵活性和工程化稳定性。

## test\_transfoms\.py

### 一、代码整体原理逻辑阐述

该代码是**数据预处理变换（transforms）模块的测试用例集合**，主要针对图像预处理中常用的变换操作（如数据类型转换、归一化、旋转、缩放、裁剪等）进行功能验证，确保各变换的输出结果符合预期。同时，测试了变换组合（Compose）、数据集加载与变换联动的完整流程，为预处理模块的正确性提供保障。

整体逻辑围绕 “单元测试 \+ 流程测试” 展开，核心目标如下：

1. 验证单个变换的功能正确性（如 Resize 后的尺寸、Normalize 的数值计算、Cast 的数据类型转换等）。

2. 验证多个变换组合使用时的兼容性（Compose 类的串联执行）。

3. 验证变换与数据集加载结合的端到端流程（如 ImageFolderDataset 加载图像后应用预处理链）。

4. 覆盖边界情况（如裁剪超出图像范围、保持比例缩放的填充等），确保模块鲁棒性。

测试框架采用类和函数结合的形式，通过断言（assert）验证输出结果的形状、数据类型、数值是否符合预期，是保障预处理模块可靠性的关键环节。

### 二、代码逐部分详细分析

1. 变换组合测试（test\_Compose）

```Python
def test_Compose():
    import math

    # 组合两个数学变换：先开平方，再指数运算
    comp = Compose([math.sqrt, math.exp])
    comp(2)
    print(comp(2))  # 预期：exp(sqrt(2)) ≈ exp(1.414) ≈ 4.113
    # 验证单个变换的访问（Compose支持索引）
    assert comp[0](9) == 3  # sqrt(9) = 3
    # 验证变换组合的长度
    assert len(comp) == 2
```

**功能与原理**：

- 测试 Compose 类的核心功能：将多个变换按顺序串联执行，支持索引访问单个变换，支持获取变换数量（len）。

- 用数学函数替代图像变换进行简化测试，验证组合逻辑的正确性 —— 输入先经过第一个变换（sqrt），其输出作为第二个变换（exp）的输入。

- 断言确保索引访问和长度计算的准确性，为后续图像变换的组合提供基础保障。

2. 数据类型转换测试（TestCast）

```Python
class TestCast:
    def test_Cast_i32(self):
        # 定义转换为uint32的变换
        i32 = Cast(np.uint32)
        x = np.arange(10, dtype=np.uint8)  # 原始数据：uint8类型
        y = i32(x)
        # 验证转换后的数据类型和数值
        assert y.dtype == np.uint32
        assert all(y == x)

    def test_Cast_f32(self):
        # 定义转换为float32的变换
        i32 = Cast(np.float32)
        x = np.arange(10, dtype=np.int8)  # 原始数据：int8类型
        y = i32(x)
        # 验证转换后的数据类型和数值
        assert y.dtype == np.float32
        assert (y == x).all()
```

**功能与原理**：

- 测试 Cast 变换的核心功能：将输入数组转换为指定的 numpy 数据类型（如 uint32、float32）。

- 验证转换的两个关键维度：

    - 数据类型：转换后的数据类型必须与指定类型一致（dtype 检查）。

    - 数值一致性：转换后的数据值应与原始值保持一致（因 uint8→uint32、int8→float32 均为安全转换，无精度损失）。

- 覆盖整数→整数、整数→浮点数的转换场景，确保类型转换在预处理中（如模型输入要求 float32）的可靠性。

3. 归一化变换测试（TestNormalize）

```Python
class TestNormalize:
    # 生成随机测试数据：形状为(4,2,3)的数组（模拟4个样本，每个2x3的图像，3个通道）
    # np.random.uniform(low, high, size)，生成服从均匀分布的随机数数组，low：随机数的下界（包含）；high：随机数的上界（不包含）；size：数组的形状（元组形式）。
    x = np.random.uniform(0, 1, (4, 2, 3))

    def test_Normalize(self):
        # 定义归一化变换：mean和std均为长度3的元组（对应3个通道）
        norm = Normalize(mean=(0, 1, 2), std=(3, 2, 1))
        y = norm(self.x)
        # 验证归一化后形状不变（仅数值变换）
        assert y.shape == self.x.shape

    def test_Normalize_scalar(self):
        # 定义归一化变换：std为标量（所有通道使用相同的std）
        norm = Normalize(mean=(0, 1, 2), std=3)
        y = norm(self.x)  # 验证标量输入是否被正确处理

    def test_Normalize_scalar_(self):
        # 定义归一化变换：std为单元素元组（等价于标量）
        norm = Normalize(mean=(0, 1, 2), std=(3))
        y = norm(self.x)  # 验证单元素元组输入是否被正确处理
```

**功能与原理**：

- 测试 Normalize 变换的核心功能：对输入按通道进行归一化（y = \(x \- mean\) /std）。

- 覆盖三种参数场景：

    - mean 和 std 均为多元素元组（通道特异性归一化，最常用场景）。

    - std 为标量（所有通道共享同一标准差）。

    - std 为单元素元组（语法兼容测试，等价于标量）。

- 验证归一化不改变输入形状，确保后续模型输入维度正确；通过执行无断言的变换，验证参数解析逻辑无错误（如标量自动广播到所有通道）。

4. 旋转变换测试（test\_Rotate）

```Python
def test_Rotate():
    # 定义旋转90度的变换
    rot = Rotate(90)
    # 生成6x6x3的随机图像（uint8类型）
    x = np.random.uniform(0, 255, (6, 6, 3)).astype(np.uint8)
    y = rot(x)
    # 验证旋转后的数据是否与预期一致：
    # 旋转90度等价于先沿行翻转，再转置（H, W, C）→（W, H, C）
    assert (x == y[::-1, :, :].transpose((1, 0, 2))).all()
```

**功能与原理**：

- 测试 Rotate 变换对 90 度旋转的处理正确性。

- 旋转 90 度的数学验证：通过 numpy 的翻转（\[::\-1\]）和转置（transpose）操作模拟旋转效果，与 Rotate 变换的输出对比，确保像素位置变换正确。

- 针对图像的空间变换特性，验证旋转后像素的对应关系，确保几何变换的准确性。

5. 缩放变换测试（TestResize）

```Python
class TestResize:
    # 生成20x20x3的随机图像（uint8类型）
    x = np.random.uniform(0, 255, (20, 20, 3)).astype(dtype=np.uint8)

    def test_Resize(self):
        # 定义缩放到(10,20)的变换（注意：通常size为(width, height)，输出形状为(height, width, C)）
        resize = Resize(size=(10, 20))
        y = resize(self.x)
        # 验证输出形状为(20, 10, 3)（height=20, width=10）
        assert y.shape == (20, 10, 3)
        # 验证缩放后非零区域（排除全零填充的情况）
        assert (y[0:5, :, :] != 0).any()

    def test_Resize_keepRatio(self):
        # 定义保持比例缩放到(10,20)，超出部分填充0
        resize = Resize(size=(10, 20), keep_ratio=True)
        y = resize(self.x)
        assert y.shape == (20, 10, 3)
        # 保持比例时，原始20x20图像缩放到width=10后，height=10，上下会有5像素的零填充
        assert (y[0:5, :, :] == 0).all()  # 顶部填充区域
        assert (y[-5:, :, :] == 0).all()  # 底部填充区域
```

**功能与原理**：

- 测试 Resize 变换的两种模式：固定尺寸缩放和保持比例缩放（带填充）。

- 固定尺寸模式：验证输出形状是否与指定 size 一致（注意 size 参数通常为 \(width, height\)，输出数组形状为 \(height, width, C\)），确保缩放逻辑正确。

- 保持比例模式：验证缩放后图像的有效区域（非填充部分）和填充区域（零值）的位置，确保比例保持和填充逻辑正确（原始 20x20 图像按 width=10 缩放后，height=10，上下各填充 5 像素以达到目标 height=20）。

6. 裁剪缩放组合测试（TestCropResize）

```Python
class TestCropResize:
    # 生成20x20x3的随机图像（uint8类型）
    x = np.random.uniform(0, 255, (20, 20, 3)).astype(dtype=np.uint8)

    def test_CropResize_crop(self):
        # 定义裁剪：从(0,0)开始，裁剪width=10, height=6的区域（不缩放）
        cr = CropResize(x=0, y=0, width=10, height=6)
        y = cr(self.x)
        # 验证裁剪后形状为(6,10,3)，且内容与原始区域一致
        assert y.shape == (6, 10, 3)
        assert (y == self.x[:6, :10, :]).all()

    def test_CropResize_resize(self):
        # 定义裁剪后缩放到(10,6)（与裁剪尺寸相同，验证resize无变化）
        cr = CropResize(x=0, y=0, width=10, height=6, size=(10, 6))
        y = cr(self.x)
        assert y.shape == (6, 10, 3)
        assert (y == self.x[:6, :10, :]).all()

    def test_CropResize_offset(self):
        # 定义裁剪：从(15,15)开始（超出原始图像范围），裁剪width=10, height=6
        cr = CropResize(x=15, y=15, width=10, height=6)
        y = cr(self.x)
        assert y.shape == (6, 10, 3)
        # 验证有效区域（原始图像15:20,15:20）与裁剪结果的对应关系
        assert (y[:5, :5, :] == self.x[15:, 15:, :]).all()
        # 验证超出部分为零填充
        z = y.copy()
        z[:5, :5, :] = 0
        assert (z == 0).all()

    def test_CropResize_offset_resize(self):
        # 定义裁剪超出范围的区域，并缩放到(20,20)
        cr = CropResize(x=15, y=15, width=10, height=6, size=(20, 20))
        y = cr(self.x)
        # 验证缩放后形状为(20,20,3)
        assert y.shape == (20, 20, 3)
```

**功能与原理**：

- 测试 CropResize 变换的核心功能：先裁剪指定区域（支持超出图像范围，超出部分填充 0），再缩放到目标尺寸。

- 覆盖四种场景：

    - 正常裁剪（不超出图像范围，不缩放）：验证裁剪区域的准确性。

    - 裁剪后缩放（目标尺寸与裁剪尺寸相同）：验证 resize 在尺寸不变时的一致性。

    - 偏移裁剪（超出图像范围）：验证超出部分的零填充逻辑，以及有效区域的正确性。

    - 偏移裁剪后缩放：验证裁剪 \+ 缩放的组合逻辑，确保输出尺寸正确。

- 边界情况处理是重点，确保图像边缘或超出范围时的裁剪行为可控。

7. 中心裁剪测试（TestCenterCrop）

```Python
class TestCenterCrop:
    # 生成20x20x3的随机图像（uint8类型）
    x = np.random.uniform(0, 255, (20, 20, 3)).astype(dtype=np.uint8)

    def test_CenterCrop(self):
        # 定义中心裁剪到(10,7)（width=10, height=7）
        cc = CenterCrop(size=(10, 7))
        y = cc(self.x)
        # 验证输出形状为(7,10,3)
        assert y.shape == (7, 10, 3)
        # 验证裁剪区域为中心：原始20x20图像，中心裁剪7x10区域的坐标为y=6:13（20-7=13，13//2=6），x=5:15（20-10=10，10//2=5）
        assert (y == self.x[6:13, 5:15:, :]).all()

    def test_CenterCrop_resize(self):
        # 定义中心裁剪到(30,27)（大于原始尺寸，需缩放）
        cc = CenterCrop(size=(30, 27))
        y = cc(self.x)
        # 验证输出形状为(27,30,3)
        assert y.shape == (27, 30, 3)
```

**功能与原理**：

- 测试 CenterCrop 变换的核心功能：从图像中心裁剪指定尺寸的区域，若目标尺寸大于原始尺寸则进行缩放。

- 正常裁剪（目标尺寸小于原始）：验证裁剪区域是否为图像中心，通过坐标计算（原始尺寸 \- 目标尺寸的一半为起始坐标）确保中心位置正确。

- 缩放裁剪（目标尺寸大于原始）：验证是否能正确缩放至目标尺寸，确保变换对大尺寸目标的兼容性。

8. 完整预处理流程测试（TestAll）

```Python
class TestAll:
    def test_resnet_preProcess(self):
        # 模拟ResNet的预处理流程（参考ONNX官方示例）
        from . import transforms

        # 组合变换：Resize→CenterCrop→Cast→Normalize
        transform_fn = transforms.Compose(
            [
                transforms.Resize(224),  # 缩放到224
                transforms.CenterCrop(224),  # 中心裁剪224x224
                transforms.Cast(np.float32),  # 转换为float32
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),  # ImageNet均值方差
            ]
        )
        # 生成320x486x3的随机图像（模拟输入）
        img = np.random.uniform(0, 255, (320, 486, 3)).astype(dtype=np.uint8)
        img = transform_fn(img)  # 应用预处理链
        img = np.expand_dims(img, axis=0)  # 增加批次维度（1,224,224,3）
```

**功能与原理**：

- 测试一个贴近实际应用的预处理流程（模拟 ResNet 的 ImageNet 预处理），验证多个变换组合使用的兼容性。

- 流程包含缩放、裁剪、类型转换、归一化，覆盖模型推理前的典型预处理步骤，确保端到端流程无错误。

- 虽无显式断言，但通过执行完整流程验证变换组合的可行性，为实际应用场景提供参考。

9. 数据集与变换联动测试（TestDataLoader）

```Python
class TestDataLoader:
    def test_dataset(self):
        from .datasets import ImageFolderDataset
        from . import transforms
        import numpy as np

        # 加载图像数据集（使用ImageFolderDataset）
        imgset = ImageFolderDataset("../../../../regression/image")

        # 定义预处理链：Resize→CenterCrop→Cast→Normalize→增加批次维度
        transform_fn = transforms.Compose(
            [
                transforms.Resize(224),
                transforms.CenterCrop(224),
                transforms.Cast(np.float32),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
                lambda img: np.expand_dims(img, axis=0),  # 自定义lambda变换
            ]
        )

        # 对数据集中的第一张图像应用预处理
        img = transform_fn(imgset[0])
        # 验证输出形状为(1,224,224,3)（批次维度+图像维度）
        assert img.shape == (1, 224, 224, 3)
        # 保存预处理结果为npz文件（用于后续回归测试）
        np.savez("resnet50_input.npz", **{"input_1": img})
```

**功能与原理**：

- 测试数据集加载与变换的联动流程：使用 ImageFolderDataset 加载图像，通过 Compose 组合变换进行预处理，验证端到端的数据准备流程。

- 验证关键点：

    - 数据集与变换的兼容性（imgset \[0\] 加载的图像可被 transform\_fn 处理）。

    - 预处理后的形状正确性（增加批次维度后为 \(1,224,224,3\)，符合模型输入要求）。

    - 结果可存储性（保存为 npz 文件，支持后续与模型输出对比的回归测试）。

- 该测试贴近实际使用场景，确保从数据加载到模型输入的全流程可靠。

### 三、总结

该代码是数据预处理模块的全面测试套件，核心价值在于通过单元测试和流程测试，确保各变换功能正确、组合兼容、边界情况可控，为图像预处理的可靠性提供保障。核心亮点如下：

1\. 全面覆盖 **：测试了数据类型转换（Cast）、归一化（Normalize）、旋转（Rotate）、缩放（Resize）、裁剪（CropResize、CenterCrop）等常用变换，覆盖预处理核心操作。**

**2\.** 多场景验证 **：每个变换测试均包含正常情况和边界情况（如裁剪超出图像范围、缩放保持比例），确保模块鲁棒性。**

**3\.** 组合兼容性 **：通过 Compose 类的测试和完整预处理流程测试，验证多个变换串联使用的正确性，贴近实际应用场景。**

**4\.** 端到端验证 **：结合 ImageFolderDataset 测试数据集加载与变换的联动，确保从数据读取到模型输入的全流程无错误。**

**5\.** 可扩展性：测试用例结构清晰（类与函数结合），便于新增变换时补充对应的测试方法。

这些测试用例是预处理模块质量的重要保障，确保在模型训练或推理前，图像数据能按预期完成转换，为后续步骤提供可靠输入。

