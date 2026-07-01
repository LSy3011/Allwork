# 关于 TPU-MLIR 源码阅读（一）

@Juno

本次关于 TPU-MLIR 的源码阅读是基于在运行代码的过程中所涉及的文件的阅读，这次我们先对如下代码中的所涉及的文件进行解析。如下代码是在 TPU-MLIR 中完成模型转换的示例代码，实现的功能是将 onnx 模型变成 mlir 的模型。

```
$ model_transform \
    --model_name yolov5s \
    --model_def ../yolov5s.onnx \
    --input_shapes [[1,3,640,640]] \
    --mean 0.0,0.0,0.0 \
    --scale 0.0039216,0.0039216,0.0039216 \
    --keep_aspect_ratio \
    --pixel_format rgb \
    --output_names 350,498,646 \
    --test_input ../image/dog.jpg \
    --test_result yolov5s_top_outputs.npz \
    --mlir yolov5s.mlir
```

运行完这个代码，我从日志中发现这段代码明显使用了四个文件，分别为：

- model_transform.py：主要入口文件，包括命令行参数解析（--model_name, --input_shapes 等），ONNX 模型加载与预处理，MLIR 生成和优化流程，验证测试的执行逻辑；
- tpuc-opt.cpp：MLIR 的优化部分。
- model_runner.py：模型执行的逻辑，包括 ONNX/MLIR 模型的加载和执行，输入/输出处理，NPZ 文件生成逻辑。
- npz_tool.py：包含结果比较算法。

## 1.model_transform.py

### 一、main 函数

#### 功能概述

main 函数是模型转换和验证的总入口，负责：

- 解析命令行参数
- 判断是否为 QAT（量化感知训练）模型并做特殊处理
- 根据模型类型选择合适的 Transformer 工具
- 执行模型转换（如 ONNX→MLIR）
- 可选地进行推理验证（如精度对比）
- 处理量化表的对齐与导出
- 清理中间文件

#### 主要流程

- 日志初始化：输出 TPU-MLIR 版本信息。
- 参数解析：使用 argparse 解析大量命令行参数，支持多种模型类型和转换选项。
- QAT 量化模型处理：

  - 创建 FakeQuantNodelProcessor，判断模型是否为 QAT。
  - 如果是 QAT，先做 QAT 相关的模型处理（如去除 FakeQuant 节点），并替换模型路径。
- 选择模型转换工具：调用 get_model_transform(args)，根据模型类型（onnx/caffe/tflite/torch/mlir/maskrcnn）实例化对应的 Transformer。
- 模型转换：调用 tool.model_transform(...)，执行模型转换（如 onnx→mlir）。
- 推理验证（可选）：如果设置了输入和输出，调用 tool.model_validate(...)，对转换前后模型做推理并比对精度。
- QAT 量化表对齐与导出：如果是 QAT，调用 align_final_opt 和 align_canonicalize，对齐量化表与实际模型节点名。
- 记录和清理

  - 保存转换过程中的文件记录。
  - 非 debug 模式下清理中间文件。

### 二、main 函数的调用函数

#### 1. FakeQuantNodelProcessor 相关

- 功能：判断并处理 QAT 量化模型，去除 FakeQuant 节点，生成量化表。
- 主要方法：

  - process_model()：对 QAT onnx 模型做后处理，生成 _qat.onnx。_
  - align_final_opt()、align_canonicalize()：对齐量化表与实际模型节点名。

#### 2. get_model_transform(args)

- 功能：根据模型类型（onnx/caffe/tflite/torch/mlir/maskrcnn）实例化对应的 Transformer 工具。
- 逻辑：

  - 判断模型后缀，选择合适的 Transformer 子类（如 OnnxTransformer、CaffeTransformer 等）。
  - 每个 Transformer 封装了模型转换、推理、验证等功能。

#### 3. tool.model_transform(...)

- 功能：执行模型转换（如 onnx→mlir）。
- 逻辑：

  - 生成 origin mlir（如 onnx→mlir）。
  - 调用 mlir 优化工具（如 mlir_opt_for_top）做结构优化和后处理。
  - 记录转换过程和文件。

#### 4. tool.model_validate(...)

- 功能：对转换前后模型做推理并比对精度。
- 逻辑：

  - 加载输入数据（npz/jpg/npy）。
  - 调用原始模型和 mlir 模型分别推理，保存输出。
  - 调用 f32_blobs_compare 对比输出精度。
  - 记录推理过程和文件。

#### 5. tool.file_recorder.dump()、tool.cleanup()

- 功能：记录转换过程中的文件，清理中间文件。

### 三、基础功能函数

#### 1. ModelTransformer 及其子类

##### ModelTransformer

- 功能：模型转换的基类，定义了转换、推理、验证等通用接口。
- 主要方法：

  - model_transform：模型转换主流程。
  - model_validate：推理验证主流程。
  - origin_inference：原始模型推理（抽象方法，由子类实现）。

##### 子类（OnnxTransformer、CaffeTransformer、TFLiteTransformer、TorchTransformer、MlirTransformer、MaskRCNNTransformer）

- 功能：针对不同模型格式实现具体的转换和推理逻辑。
- 主要方法：

  - origin_inference：调用对应的推理后端（如 onnx_inference、caffe_inference 等）。

#### 2. ensure_batch_size

- 功能：保证输入张量的 batch size 满足要求（如自动扩展）。
- 逻辑：如果 batch size 不足，则重复填充。

#### 3. model_mlir

- 功能：生成原始 mlir 文件。
- 逻辑：调用 converter 的 generate_mlir 方法。

#### 4. model_transform

- 功能：模型转换主流程。
- 逻辑：

  - 生成 origin mlir。
  - 调用 mlir 优化工具做结构优化。
  - 记录转换过程和文件。

#### 5. model_validate

- 功能：推理验证主流程。
- 逻辑：

  - 加载输入数据。
  - 分别调用原始模型和 mlir 模型推理。
  - 对比输出精度。
  - 记录推理过程和文件。

#### 6.ModelTransformer 类的代码

```python
class ModelTransformer(object):
    """
    模型转换器类，用于处理模型转换流程，包括生成MLIR文件、模型验证等功能
    是一个抽象类，需要子类实现origin_inference方法
    """

    def __init__(self, model_name, model_def):
        """
        初始化模型转换器
        
        参数:
            model_name: 模型名称
            model_def: 模型定义（可以是模型文件路径或其他模型定义信息）
        """
        self.model_name = model_name  # 模型名称
        self.model_def = model_def    # 模型定义
        self.do_mlir_infer = True     # 是否进行MLIR推理
        self.converter = BaseConverter()  # 基础转换器实例
        # 输入和参考输出文件名称
        self.in_f32_npz = self.model_name + '_in_f32.npz'
        self.ref_npz = self.model_name + '_ref_outputs.npz'
        self.file_recorder = None     # 命令记录器，用于记录处理过程中的文件和命令

    def set_mlir_file(self, mlir_file: str):
        """
        设置MLIR文件路径并初始化文件记录器
        
        参数:
            mlir_file: MLIR文件路径
        """
        self.mlir_file = mlir_file
        # 创建命令记录器，保存相关文件信息到JSON
        self.file_recorder = CommandRecorder(f"{self.model_name}.ref_files.json")

    def cleanup(self):
        """清理文件（具体清理逻辑由file_clean实现）"""
        file_clean()

    @staticmethod
    def ensure_batch_size(arr: np.ndarray, batch_size):
        """
        确保输入数组的batch size符合要求
        
        参数:
            arr: 输入数组，形状为[old_batch_size, ...]
            batch_size: 目标batch size
            
        返回:
            调整后的数组，batch size为指定值
        """
        old_batch_size = arr.shape[0]
        # 如果原batch size大于1，直接返回
        if old_batch_size > 1:
            return arr
        # 计算需要重复的次数
        repeat_factor = int(np.ceil(batch_size / old_batch_size))
        # 沿第0轴重复数组
        repeated_arr = np.repeat(arr, repeat_factor, axis=0)
        # 截断到目标batch size
        trimmed_arr = repeated_arr[:batch_size]
        return trimmed_arr

    def model_mlir(self):
        """
        生成原始MLIR文件
        
        返回:
            生成的原始MLIR文件路径
        """
        mlir_origin = self.model_name + "_origion.mlir"
        # 通过转换器生成MLIR
        self.converter.generate_mlir(mlir_origin)
        # 标记文件（可能用于跟踪或清理）
        file_mark(mlir_origin)
        return mlir_origin

    def model_transform(self, mlir_file: str, add_postprocess: str="", patterns_count: dict={}):
        """
        模型转换主方法，处理MLIR优化和转换流程
        
        参数:
            mlir_file: 输出的MLIR文件路径
            add_postprocess: 额外的后处理步骤
            patterns_count: 预期的优化模式应用次数，用于验证
        """
        # 设置MLIR文件并初始化记录器
        self.set_mlir_file(mlir_file)
        # 生成原始MLIR文件路径
        mlir_origin = self.mlir_file.replace('.mlir', '_origin.mlir', 1)
        
        # 如果有转换器，使用转换器生成原始MLIR
        if self.converter:
            file_mark(mlir_origin)
            self.converter.generate_mlir(mlir_origin)
        else:
            # 如果没有转换器，直接使用模型定义作为原始MLIR
            mlir_origin = self.model_def

        # 对原始MLIR进行优化，生成目标MLIR
        patterns = mlir_opt_for_top(
            mlir_origin, 
            self.mlir_file, 
            add_postprocess, 
            True if patterns_count else False
        )
        
        # 验证优化模式应用次数是否符合预期
        if patterns_count:
            for k, v in patterns_count.items():
                assert k in patterns and v == patterns[k], \
                f"优化模式 {k} 的应用次数不符合要求。预期 {v}，实际 {patterns.get(k)}"

        logger.info(f"生成MLIR文件: {self.mlir_file}")

        # 解析MLIR模块获取输入数量
        self.module_parsered = MlirParser(self.mlir_file)
        self.input_num = self.module_parsered.get_input_num()

        # 记录相关文件和命令
        if self.file_recorder:
            self.file_recorder.add_file(
                origin_model=self.model_def,
                origin_mlir=mlir_origin,
                top_mlir=self.mlir_file,
            )
            self.file_recorder.add_command(transform_cmd=' '.join(sys.argv))

    def model_validate(self, file_list: str, tolerance, excepts, test_result):
        """
        模型验证方法，比较原始模型和MLIR模型的输出结果
        
        参数:
            file_list: 输入文件列表（可以是npz/npy文件或图片）
            tolerance: 精度容忍度，用于比较输出差异
            excepts: 例外情况（不进行比较的层）
            test_result: 测试结果保存路径
        """
        from tools.model_runner import mlir_inference, free_mlir_module, show_fake_cmd
        
        inputs = dict()  # 存储输入数据
        
        # 处理NPZ格式输入文件
        if len(file_list) == 1 and file_list[0].endswith('.npz'):
            npz_in = np.load(file_list[0])
            only_one = len(npz_in.files) == 1
            
            if only_one:
                # 只有一个输入时，使用转换器定义的输入名称
                assert (len(self.converter.input_names) == 1)
                name = self.converter.input_names[0]
                # 确保batch size符合要求
                batch_size = self.module_parsered.get_batch_size()
                inputs[name] = self.ensure_batch_size(npz_in[npz_in.files[0]], batch_size)
            else:
                # 多个输入时，按名称匹配
                for name in self.converter.input_names:
                    assert (name in npz_in.files)
                    batch_size = self.converter.getShape(name)[0]
                    inputs[name] = self.ensure_batch_size(npz_in[name], batch_size)
                    
        # 处理图片格式输入
        elif file_list[0].endswith(('.jpg', '.jpeg', '.png', '.JPEG')):
            ppa = preprocess()  # 初始化预处理工具
            for i in range(self.input_num):
                # 获取图片路径，不足时复用最后一张
                pic_path = file_list[i] if i < len(file_list) else file_list[-1]
                file = os.path.expanduser(pic_path)
                # 加载输入配置并预处理
                ppa.load_config(self.module_parsered.get_input_op_by_idx(i))
                inputs[ppa.input_name] = ppa.run(file)
                
        # 处理NPY格式输入文件
        else:
            assert (len(file_list) == len(self.converter.input_names))
            for name, file in zip(self.converter.input_names, file_list):
                assert (file.endswith('.npy'))
                inputs[name] = np.load(file)
        
        # 保存输入数据
        np.savez(self.in_f32_npz, **inputs)
        
        # 原始模型推理获取参考输出
        show_fake_cmd(self.in_f32_npz, self.model_def, self.ref_npz)
        ref_outputs = self.origin_inference(inputs)
        
        # 如果不需要MLIR推理，直接保存参考输出
        if not self.do_mlir_infer:
            logger.info(f"保存测试结果到 {test_result}")
            np.savez(test_result,** ref_outputs)
            return
        
        # 保存参考输出
        logger.info(f"保存参考输出到 {self.ref_npz}")
        np.savez(self.ref_npz, **ref_outputs)
        
        # 清理内存
        del self.converter
        del ref_outputs
        gc.collect()

        # MLIR模型推理
        show_fake_cmd(self.in_f32_npz, self.mlir_file, test_result)
        f32_outputs = mlir_inference(inputs, self.mlir_file)
        
        # 保存MLIR推理结果
        logger.info(f"保存测试结果到 {test_result}")
        np.savez(test_result,** f32_outputs)
        
        # 清理内存
        del f32_outputs
        free_mlir_module()
        gc.collect()
        
        # 比较MLIR输出和参考输出
        f32_blobs_compare(test_result, self.ref_npz, tolerance, excepts=excepts)
        file_mark(self.ref_npz)
        
        # 记录验证相关文件
        if self.file_recorder:
            self.file_recorder.add_file(
                mlir_input=self.in_f32_npz,
                top_output=self.ref_npz,
            )

    @abc.abstractmethod
    def origin_inference(self, inputs: dict) -> dict:
        """
        抽象方法：原始模型推理
        
        参数:
            inputs: 输入数据字典
            
        返回:
            推理输出结果字典
        """
        pass
```

#### 7.OnnxTransformer 类的代码

```python
class OnnxTransformer(ModelTransformer):
    """
    ONNX模型转换器，继承自ModelTransformer抽象类，专门用于处理ONNX模型的转换与验证流程
    实现了ONNX格式特有的模型转换逻辑和原始模型推理方法
    """

    def __init__(self,
                 model_name,
                 model_def,
                 input_shapes: list = [],
                 output_names: list = [],
                 test_input='',
                 preprocessor: dict = {},
                 static_shape=True,
                 onnx_sim='',
                 dynamic_shape_input_names: list = [],
                 dynamic=False,
                 shape_influencing_input_names: list = [],
                 dump_final_opt=True,
                 op_custom_shape: dict = {},
                 replace_topk_indices=False,
                 do_onnx_sim=True):
        """
        初始化ONNX模型转换器
        
        参数:
            model_name: 模型名称
            model_def: ONNX模型定义（通常是模型文件路径）
            input_shapes: 输入张量的形状列表，用于指定输入维度
            output_names: 需要保留的输出张量名称列表
            test_input: 测试输入数据路径或描述
            preprocessor: 预处理配置字典，指定输入数据的预处理方式
            static_shape: 是否将模型输入形状固定为静态形状（非动态）
            onnx_sim: ONNX模型简化参数配置
            dynamic_shape_input_names: 动态形状输入的名称列表（维度不固定的输入）
            dynamic: 是否启用动态形状模式
            shape_influencing_input_names: 影响模型输出形状的输入名称列表
            dump_final_opt: 是否导出最终优化后的模型
            op_custom_shape: 自定义算子的形状配置字典 {算子名: 形状信息}
            replace_topk_indices: 是否替换TopK算子的索引输出
            do_onnx_sim: 是否对ONNX模型进行简化处理（去除冗余节点等）
        """
        # 调用父类构造方法，初始化模型名称和模型定义
        super().__init__(model_name, model_def)
        
        # 导入ONNX专用转换器并实例化
        from transform.OnnxConverter import OnnxConverter
        self.converter = OnnxConverter(
            self.model_name,               # 模型名称
            self.model_def,                # 模型定义（ONNX文件路径）
            input_shapes,                  # 输入形状列表
            output_names,                  # 输出名称列表
            test_input,                    # 测试输入
            preprocessor,                  # 预处理配置
            static_shape,                  # 是否使用静态形状
            onnx_sim_param=onnx_sim,       # ONNX简化参数
            dynamic_shape_input_names=dynamic_shape_input_names,  # 动态形状输入名称
            dynamic=dynamic,               # 是否启用动态模式
            shape_influencing_input_names=shape_influencing_input_names,  # 影响形状的输入
            dump_final_opt=dump_final_opt, # 是否导出最终优化后的模型
            op_custom_shape=op_custom_shape,  # 自定义算子形状
            replace_topk_indices=replace_topk_indices,  # 是否替换TopK索引
            do_onnx_sim=do_onnx_sim        # 是否执行ONNX模型简化
        )

    def origin_inference(self, inputs: dict):
        """
        实现父类的抽象方法，执行ONNX原始模型的推理，生成参考输出
        
        参数:
            inputs: 输入数据字典，键为输入名称，值为numpy数组
            
        返回:
            推理输出字典，键为输出名称，值为numpy数组（原始模型的推理结果）
        """
        # 导入ONNX推理工具函数
        from tools.model_runner import onnx_inference
        
        # 调用ONNX推理函数，传入输入数据和ONNX模型文件（由转换器维护）
        # 返回推理结果用于后续与MLIR模型输出对比验证
        return onnx_inference(inputs, self.converter.onnx_file)
```

## 2.BaseConverter.py

### 一、整体定位

该文件定义了一个名为 BaseConverter 的基类，主要用于张量（tensor）、操作数（operand）、形状（shape）等在模型转换过程中的管理。它为后续的具体转换器（如 ONNX、Caffe、TensorFlow 转 MLIR 等）提供了基础的数据结构和通用方法。

### 二、主要成员变量

```python
def __init__(_self_, _no_save_: bool = False):
        _self_.operands = dict()
        _self_.tensors = dict()
        _self_.shapes = dict()
        _self_.input_names = list()
        _self_.output_names = list()
        _self_.no_save = _no_save_ _# do not save intermediate files in disk_
```

- self.operands：字典，保存操作数（operand）的名称与对象的映射。
- self.tensors：字典，保存权重（weight）或常量张量的名称与 numpy 数组的映射。
- self.shapes：字典，保存张量名称与其 shape（list）的映射。
- self.input_names / self.output_names：输入、输出张量的名称列表。
- self.no_save：布尔值，是否不保存中间文件。

### 三、主要方法功能梳理

#### 1. 形状相关

```python
def addShape(self, name, shape):
        if len(shape) == 0:
            shape = [1]
        if isinstance(shape, tuple):
            shape = list(shape)
        elif not isinstance(shape, list):
            raise KeyError("{}:{} unknown shape".format(name, shape))
        if name in self.shapes:
            if self.shapes[name] != shape:
                raise KeyError("shape {} conflict {} vs {}".format(name, self.shapes[name], shape))
        self.shapes[name] = shape

    def getShape(self, name):
        if name not in self.shapes:
            raise KeyError("shape {} not found".format(name))
        return self.shapes[name]

    def setShape(self, name, shape):
```

- addShape(name, shape):添加 shape 信息，支持 tuple、list，若已存在则检查一致性。
- getShape(name):获取指定名称的 shape，不存在则报错。
- setShape(name, shape):修改已存在的 shape，不存在则报错。

#### 2.操作数相关

```python
def addOperand(self, name, op):
        if name in self.operands:
            if self.operands[name] != op:
                raise KeyError("operand {} conflict".format(name))
            return
        self.operands[name] = op

    def getOperand(self, name):
        if name not in self.operands:
            raise KeyError("operand {} not found".format(name))
        return self.operands[name]

    def getOp(self, name):
        if self.isWeight(name):
            return self.getWeightOp(name)
        return self.getOperand(name)
```

- addOperand(name, op):添加操作数，若已存在则检查一致性。
- getOperand(name):获取操作数对象，不存在则报错。
- getOp(name):如果是权重，返回权重操作数，否则返回普通操作数。

#### 3. 权重/张量相关

```python
def addWeight(_self_, _name_, _data_: np.ndarray):
        _if_ not isinstance(_data_, np.ndarray):
            _raise_ KeyError("tensor data must be a numpy array")
        _if_ _data_.dtype != np.float32:
            _data_ = _data_.astype(np.float32)
        _if_ _name_ in _self_.tensors:
            _if_ np.all(_self_.tensors[_name_] == _data_):
                _return_
            _raise_ KeyError("tensor {} conflict".format(_name_))
        _if_ len(_data_.shape) == 0:
            _data_ = _data_.reshape([1])
        _# all weight convert to f32._
        _self_.tensors[_name_] = _data_
        _self_.addShape(_name_, _data_.shape)

    def isWeight(_self_, _name_):
        _if_ _name_ in _self_.tensors:
            _return_ True
        _return_ False

    def getWeight(_self_, _name_):
        _if_ _name_ not in _self_.tensors:
            _raise_ KeyError("No {} tensor in model".format(_name_))
        _return_ _self_.tensors[_name_]
        
    def getWeightOp(_self_, _name_, _shape_: list = []):
        _if_ _name_ not in _self_.tensors:
            _raise_ KeyError("Should addWeight first:{}!!!".format(_name_))
        old_shape = _self_.getShape(_name_)
        _if_ _shape_ and old_shape != _shape_:
            _assert_ (np.prod(old_shape) == np.prod(_shape_))
            old_shape = _shape_
        ori_type = str(_self_.tensors[_name_].dtype)
        type_dict = {
            'int8': "INT8",
            'uint8': "UINT8",
            'float32': "F32",
            'int32': "INT32",
            'int16': "INT16",
            'uint16': "UINT16",
        }
        _if_ ori_type not in type_dict:
            _raise_ KeyError("type {} not implemented".format(ori_type))
        op = _self_.mlir.create_weight_op(_name_, old_shape, type_dict[ori_type])
        _self_.addOperand(_name_, op)
        _return_ op
```

- addWeight(name, data):添加权重张量，要求为 numpy 数组，自动转为 float32，shape 也会同步添加。
- isWeight(name):判断是否为权重（即是否在 tensors 字典中）。
- getWeight(name):获取权重张量，不存在则报错。
- getWeightOp(name, shape=[]):获取权重的操作数对象（通常用于 MLIR），必要时会 reshape，类型映射到 MLIR 支持的类型。

```python
def isScalar(_self_, _name_):
        _if_ not _self_.isWeight(_name_): _return_ False
        _if_ np.prod(_self_.getShape(_name_)) == 1: _return_ True
        w = _self_.getWeight(_name_)
        _return_ np.all(w == w.flatten()[0])

    def isScalar_(_self_, _name_, _x_):
        _assert_ (isinstance(_x_, (int, float)))
        _if_ not _self_.isWeight(_name_): _return_ False
        _if_ np.prod(_self_.getShape(_name_)) == 1: _return_ True
        w = _self_.getWeight(_name_)
        _return_ np.all(w == _x_)

    def getScalar(_self_, _name_):
        _if_ not _self_.isScalar(_name_):
            _raise_ RuntimeError("Not Scalar")
        _return_ _self_.getWeight(_name_).flatten()[0]
```

- isScalar(name):判断是否为标量（shape 乘积为 1，且所有值相等）。
- isScalar_(name, x):判断是否为值为 x 的标量。
- getScalar(name):获取标量的值。

#### 4. 其他

```python
def generate_mlir(_self_, _mlir_file_: str):
        _raise_ NotImplementedError('generate_mlir')
        
    def WeightToNpz(_self_, _weight_file_):
        tensor_npz = {}
        _for_ name _in_ _self_.tensors:
            _if_ name in _self_.operands:
                tensor_npz[name] = _self_.tensors[name]
        np.savez(_weight_file_, **tensor_npz)
```

- generate_mlir(mlir_file):生成 MLIR 文件的接口，基类未实现，需子类实现。
- WeightToNpz(weight_file):将所有权重（且已添加为操作数的）保存为 npz 文件。

### 四、典型的使用流程（举例说明）

假设现在要把一个深度学习模型（比如 ONNX、Caffe、TensorFlow）转换成 MLIR 格式，大致会经历以下几个步骤：

#### 步骤一：收集和注册模型信息

- 读取模型文件，遍历每一层（layer）或每个节点（node）。
- 对于每个输入、输出、权重、常量等，调用如下方法注册到 converter 里：
- addShape(name, shape)：记录每个张量的形状（比如 [1, 3, 224, 224]）。
- addWeight(name, data)：记录每个权重的具体数值（numpy 数组），比如卷积的权重。
- addOperand(name, op)：记录每个操作数（比如某一层的输出）。

#### 步骤二：在转换过程中随时查询

- 需要用到某个张量/权重/操作数时，调用 getShape(name)/getWeight(name)/getOperand(name)这些方法保证你能随时拿到你需要的数据。

#### 步骤三：判断和处理特殊情况

- 比如有些权重其实是“标量”（只有一个数），可以用 isScalar(name)/getScalar(name)判断和获取。

#### 步骤四：生成 MLIR 文件

- 当所有信息都收集好后，调用 generate_mlir(mlir_file)（子类实现）来生成最终的 MLIR 文件。

#### 步骤五：保存权重

- 如果需要把权重单独保存成 npz 文件，调用 WeightToNpz(weight_file)。

## 3.deploy_qat.py

### 一、整体定位

本文件主要用于处理 QAT（Quantization Aware Training，量化感知训练）后的 ONNX 模型，对模型中的 FakeQuant 节点进行处理、裁剪、参数提取、表格导出等操作，为后续的部署和推理做准备。核心类为 FakeQuantNodelProcessor，它负责识别、处理、移除 QAT 相关节点，并生成量化表（calitable、qtable）等。下面是阅读这个文件的代码的简化版的结构。

```
FakeQuantNodelProcessor
    |
    |-- process_model()
            |-- prepare_params()
            |-- remove_fake_pad_op()
            |-- 遍历节点
                    |-- deal_with_weight_fakequant()
                    |-- clip_weight()
                    |-- parse_qparams()
                    |-- deal_with_activation_fakequant()
            |-- 移除节点
            |-- onnx.shape_inference.infer_shapes
            |-- 移除未用initializer
            |-- post_process_clip_ranges()
    |
    |-- export_tables()
    |-- align_final_opt() / align_canonicalize()  # 名字对齐
```

### 二、主要函数与成员变量

#### 1. 量化节点类型定义

```sql
PERCHANNEL_FAKEQUANTIZER = ['FakeQuantizeLearnablePerchannelAffine',
                                'FixedPerChannelAffine',
                                'FakeQuantizeDSQPerchannel',
                                'FPEmuOp_per_channel']
    PERTENSOR_FAKEQUANTIZER = ['LearnablePerTensorAffine',
                               'FixedPerTensorAffine',
                               'FakeQuantizeDSQPertensor',
                               'FakeQuantizeTqtAffine',
                               'FPEmuOp_per_tensor']
    ALL_FAKEQUANTIZER = PERCHANNEL_FAKEQUANTIZER + PERTENSOR_FAKEQUANTIZER
```

- PERCHANNEL_FAKEQUANTIZER：按通道量化的 FakeQuant 节点类型列表。
- PERTENSOR_FAKEQUANTIZER：按张量量化的 FakeQuant 节点类型列表。
- ALL_FAKEQUANTIZER：所有支持的 FakeQuant 节点类型。

#### 2. 初始化与成员变量

```python
def __init__(_self_, _input_model_path_, _input_model_name_):
        _self_.input_model_name = _input_model_name_
        _self_.input_model_path = _input_model_path_
        _self_.output_model_path = _input_model_path_.replace('.onnx', '_qat.onnx')
        _self_.fakequant_model = _self_.check_onnx_for_fakequant_nodes(_input_model_path_)
        _self_.calitable_name = _input_model_name_ + "_calitable_qat"
        _self_.qtable_name = _input_model_name_ + "_qtable_qat"
        _self_.nodes_to_be_removed = []
        _self_.cali_table = {}
        _self_.weight_table = {}
        _self_.q_table = {}
```

- input_model_path/input_model_name：输入模型路径和名称。
- output_model_path：输出模型路径（自动加后缀）。
- fakequant_model：模型中是否包含 FakeQuant 节点。
- calitable_name/qtable_name：量化表文件名。
- nodes_to_be_removed：待移除节点列表。
- cali_table/weight_table/q_table：量化参数表、权重量化表、量化类型表。

#### 3.process_model()

process_model() 是 FakeQuantNodelProcessor 类的核心方法，主要用于对 QAT（量化感知训练）得到的 ONNX 模型进行后处理。其目标是：

- 移除 FakeQuant 节点（量化伪节点）
- 修正权重数据
- 生成量化和校准表
- 简化模型结构，便于后续部署

```python
def process_model(_self_):
        _self_.prepare_params() #初始化输入输出映射、参数、initializer。
        _self_.remove_fake_pad_op() #移除无效 Pad 节点，修正后续节点输入。
        _self_.update_inp2node_out2node(_self_.graph) #建立输入到节点、输出到节点的映射。
        _for_ node _in_ _self_.graph.node:
            print(f'process_node :{node.name}, type:{node.op_type}')
            # 处理伪量化节点
            _if_ node.op_type in _self_.ALL_FAKEQUANTIZER:
                _self_.nodes_to_be_removed.append(node)
                _self_.nodes_to_be_removed.extend(_self_.get_constant_inputs(node))
            # 处理输出节点映射
            _if_ node.output[0] not in _self_.inp2node:
                _assert_ node.output[0] in [l.name _for_ l _in_ _self_.graph.output]#[l.name for l in self.graph.output]：提取模型所有最终输出的名称，用于验证 “未被映射的输出” 的合法性。
                _self_.inp2node[node.output[0]] = []
            next_nodes = _self_.inp2node[node.output[0]]
            #权重量化节点处理（PERCHANNEL_FAKEQUANTIZER）：调用 deal_with_weight_fakequant，将权重 FakeQuant 节点替换为原始权重，收集冗余节点。
            #调用 clip_weight，对权重做裁剪。
            #调用 parse_qparams，解析量化参数，生成权重量化表和量化类型表。
            _if_ node.op_type in _self_.PERCHANNEL_FAKEQUANTIZER:
                redundant_nodes = _self_.deal_with_weight_fakequant(node)
                _self_.nodes_to_be_removed.extend(redundant_nodes)
                _self_.clip_weight(node)
                # 生成权重量化参数表
                tensor_name, scale, zero_point, qmin, qmax, dtype, quant_type = _self_.parse_qparams(node)
                _if_ len(next_nodes) == 1 and next_nodes[0][0].op_type in ['Gemm', 'Conv']:
                    next_node_output = next_nodes[0][0].output[0]
                    _assert_ next_nodes[0][0].op_type in ['Gemm', 'Conv']
                    _if_ _self_.inp2node[next_node_output][0][0].op_type == 'Relu':
                        tensor_name = '{}_{}_weight'.format(_self_.inp2node[next_node_output][0][0].output[0], 'Relu')
                    _else_:
                        tensor_name = '{}_{}_weight'.format(next_node_output, next_nodes[0][0].op_type)
                    _self_.weight_table[tensor_name] = [
                        tensor_name,
                        len(scale),
                        *[float(f"{float(x):.7f}") _for_ x _in_ scale],
                        len(zero_point),
                        *[int(x) _for_ x _in_ zero_point]
                        ]
                    _self_.q_table[tensor_name] = [tensor_name,quant_type]
             # 处理张量级伪量化（激活或权重）
            _elif_ node.op_type in _self_.PERTENSOR_FAKEQUANTIZER:
                _if_ len(next_nodes) == 1 and next_nodes[0][1] == 1 and next_nodes[0][0].op_type in ['Gemm', 'Conv']:
                    _# 处理权重伪量化_
                    redundant_nodes = _self_.deal_with_weight_fakequant(node)
                    _self_.nodes_to_be_removed.extend(redundant_nodes)
                    _self_.clip_weight(node)
                    tensor_name, scale, zero_point, qmin, qmax, dtype, quant_type = _self_.parse_qparams(node)
                    _assert_ next_nodes[0][0].op_type in ['Gemm', 'Conv']
                    tensor_name_new = '{}_{}_weight'.format(next_nodes[0][0].output[0], next_nodes[0][0].op_type)
                    _self_.weight_table[tensor_name_new] = [
                        tensor_name_new,
                        len(scale),
                        *[float(f"{float(x):.7f}") _for_ x _in_ scale],
                        len(zero_point),
                        *[int(x) _for_ x _in_ zero_point]
                        ]
                    _self_.q_table[tensor_name_new] = [tensor_name_new,quant_type]
                _else_:
                    # 处理激活伪量化
                    tensor_name, scale, zero_point, qmin, qmax, dtype, quant_type = _self_.parse_qparams(node)
                    tensor_name = node.input[0]
                    _if_ node.input[0] in _self_.out2node:
                        pre_node = _self_.out2node[tensor_name]
                        pre_type = pre_node.op_type
                        tensor_name = '{}_{}'.format(tensor_name, pre_type)
                    _self_.cali_table[tensor_name] = [
                        tensor_name,
                        float(f"{float(scale * max(-qmin, qmax)):.7f}"),
                        float(f"{float(scale * (qmin - zero_point)):.7f}"),
                        float(f"{float(scale * (qmax - zero_point)):.7f}")
                    ]
                    _self_.q_table[tensor_name] = [tensor_name,quant_type]
                    _self_.deal_with_activation_fakequant(node)
                    output_name = node.output[0]
                    _for_ out _in_ _self_.graph.output:
                        _if_ out.name == output_name:
                            out.name = node.input[0]
        #移除冗余节点
        _for_ node _in_ _self_.nodes_to_be_removed:
            _self_.graph.node.remove(node)
        #形状推理与保存模型
        model_onnx = onnx.shape_inference.infer_shapes(_self_.model)
        onnx.save(model_onnx, _self_.output_model_path)

        _# 清理初始化器_
        _self_.out2node, _self_.inp2node = _self_.update_inp2node_out2node(_self_.graph)
        _self_.named_initializer = _self_.prepare_initializer(_self_.graph)
        _for_ name, initial_data _in_ _self_.named_initializer.items():
            _if_ name in (_self_.out2node.keys() | _self_.inp2node.keys()):
                _continue_
            _self_.graph.initializer.remove(initial_data)
        #后处理裁剪范围
        _self_.post_process_clip_ranges()
```

- prepare_params():初始化输入输出映射、参数、initializer。
- remove_fake_pad_op():移除无效 Pad 节点，修正后续节点输入。
- update_inp2node_out2node(graph)：建立输入到节点、输出到节点的映射。
- deal_with_weight_fakequant(node)：处理权重量化的 FakeQuant 节点，将其替换为原始权重，返回冗余节点。
- clip_weight(node)：对权重做裁剪（clip）到量化区间。
- parse_qparams(node)：解析量化参数（scale、zero_point、qmin、qmax、dtype、quant_type）。
- deal_with_activation_fakequant(node)：处理激活量化的 FakeQuant 节点，将其前后节点直接连接。
- post_process_clip_ranges()：对特殊节点（如 Flatten、Reshape 等）输入输出的 clip range 做对齐，补全校准表。
- numpy_helper.to_array ()： 是 ONNX（Open Neural Network Exchange）工具包中的一个函数，其主要功能是将 ONNX 格式的张量（TensorProto）转换为 NumPy 数组。

#### 4.clip_weight(_self_, _node_)

该函数的核心作用是将权重裁剪到量化对应的浮点范围内，确保量化时不会因数值溢出导致精度损失。

```python
def clip_weight(self, node):
    # 1. 从伪量化节点中解析量化参数
    # tensor_name：权重名称；scale/zero_point：量化缩放和零点；qmin/qmax：量化范围边界
    # dtype：数据类型；quant_type：量化类型（如PER_CHANNEL/PER_TENSOR）
    tensor_name, scale, zero_point, qmin, qmax, dtype, quant_type = self.parse_qparams(node)
    
    # 2. 根据权重名称获取原始权重数据（NumPy数组格式）
    data = self.name2data[tensor_name]
    
    # 3. 计算量化对应的浮点裁剪范围
    # 公式：浮点最小值 = (量化最小值 - 零点) * 缩放因子
    # 作用：将整数量化范围[qmin, qmax]转换为对应的浮点数范围
    clip_range_min = ((qmin - zero_point) * scale).astype(data.dtype)
    clip_range_max = ((qmax - zero_point) * scale).astype(data.dtype)
    
    # 4. 判断是否为通道级量化（每个通道有独立的scale）
    # scale.shape[0] > 1 表示每个通道有独立的缩放因子
    if len(scale.shape) > 0 and scale.shape[0] > 1:
        new_data = []  # 存储裁剪后的权重
        transposed = False  # 标记是否需要转置（针对反卷积）
        
        # 5. 获取当前节点的下游节点（使用该权重的节点）
        next_node = self.inp2node[node.output[0]]
        
        # 6. 特殊处理反卷积层（ConvTranspose）的权重维度
        # 反卷积权重维度通常为[in_channels, out_channels, h, w]，需转置为[out_channels, ...]
        if len(next_node) == 1 and next_node[0][0].op_type == 'ConvTranspose':
            transposed = True
            data = data.transpose(1, 0, 2, 3)  # 调整维度顺序以匹配通道
        
        # 7. 按通道裁剪权重（每个通道使用独立的裁剪范围）
        for c in range(data.shape[0]):
            # 对第c个通道的权重裁剪到该通道对应的范围
            new_data.append(np.clip(data[c], clip_range_min[c], clip_range_max[c]))
        
        # 8. 将列表转换为NumPy数组
        new_data = np.array(new_data)
        
        # 9. 若之前转置过，恢复反卷积权重的原始维度
        if transposed:
            new_data = new_data.transpose(1, 0, 2, 3)
    
    # 10. 若为张量级量化（所有通道共享一个scale），直接裁剪整个权重
    else:
        new_data = np.clip(data, clip_range_min, clip_range_max)
    
    # 11. 将裁剪后的NumPy数组转换为ONNX张量格式
    new_data = numpy_helper.from_array(new_data)
    
    # 12. 更新初始化器中的权重数据（替换为裁剪后的数据）
    self.named_initializer[tensor_name].raw_data = new_data.raw_data
```

#### 5.parse_qparams(self, node)

`parse_qparams` 是 QAT（量化感知训练）模型后处理中的量化参数解析核心函数。它的核心作用是从伪量化（`FakeQuant`）节点中提取关键量化参数（如缩放因子、零点、量化范围等），并自动判断量化类型（如 INT8、UINT8、FP8），为后续模型量化部署（如权重裁剪、算子融合、硬件适配）提供必要的参数支持。

##### 提取关键量化参数

在计算图（如 ONNX）中，“输入（Input）” 和 “属性（Attribute）” 的设计有明确分工：

- 输入（Input）：用于传递 “动态数据”（如张量、中间计算结果），这些数据在图执行过程中可能变化（如不同批次的输入、动态生成的量化参数）。
- 属性（Attribute）：用于传递 “静态配置”（如固定的数值、类型），这些参数在图定义时就已确定，执行过程中不会变化（如 INT8 的固定范围 [-128,127]）。

FakeQuant 节点的核心参数（qmin/qmax、scale、zero_point）既可能是 “动态生成的”（需通过输入传递），也可能是 “固定配置的”（需通过属性传递），因此代码需要兼容两种情况。具体原因如下：

1. 输入传入（qmin/qmax 在 node.input 中）的场景与设计逻辑

适用场景：当 qmin/qmax 等参数是 “动态计算的结果” 时（如通过校准算法生成的量化范围）。

例如：在模型训练或校准阶段，qmin/qmax 可能根据输入数据的分布动态计算（如通过 “最小最大校准法” 统计输入的最小值和最大值作为 qmin/qmax），这些动态生成的参数会作为 “张量数据” 通过节点的 “输入” 传递给 FakeQuant 节点。

```python
if len(node.input) > 3:# 情况1：qmin/qmax通过节点输入传入（部分FakeQuant节点设计）
        qmin, qmax = node.input[-2:]
        qmin, qmax = self.name2data[qmin], self.name2data[qmax]  # 转换为数值# 从属性中提取数据类型（如“int8”）if len(node.attribute) > 0:
            qparams = self.parse_attrs(node.attribute)  # 解析属性为字典
            dtype = qparams['dtype']else:
            dtype = 'None'
```

这里的 `node.input` 是一个列表，存储了节点的所有输入张量名称。当输入长度超过 3 时（前 3 个通常是 tensor_name、scale、zero_point），最后 2 个输入就是动态生成的 qmin 和 qmax（以张量名称的形式存储，需通过 `self.name2data` 映射到实际数值）。

1. 属性传入（qmin/qmax 在 node.attribute 中）的场景与设计逻辑

适用场景：当 qmin/qmax 等参数是 “固定配置” 时（如预定义的量化类型对应的固定范围）。
例如：INT8 的量化范围是固定的 `qmin=-128，qmax=127`，UINT8 是 `qmin=0，qmax=255`，这些无需动态计算，可直接作为 “静态配置” 写入节点属性。

```python
elif len(node.attribute) > 0:# 情况2：qmin/qmax和dtype通过节点属性传入（另一类FakeQuant节点设计）
        qparams = self.parse_attrs(node.attribute)  # 解析属性（如quant_min、quant_max）
        qmin = qparams['quant_min']  # 量化最小值（如INT8的-128）
        qmax = qparams['quant_max']  # 量化最大值（如INT8的127）
        dtype = qparams['dtype']  # 数据类型（如“int”“uint”）
    else:# 异常情况：未找到qmin/qmax（可能导致后续量化错误）
        print.info(f'qmin and qmax are not found for <{node.name}>!')
```

这里的 `node.attribute` 是节点的静态配置列表，存储了预定义的量化范围（如 `quant_min` 对应 qmin，`quant_max` 对应 qmax），这些参数在图定义时就已确定，无需动态计算。

##### 自动判断量化类型

```python
_if_ qmax == float(448.0) or qmax == float(57344.0):
            quant_type = 'FP8'
```

- FP8（浮点 8 位）是一种特殊的量化格式，它的量化范围由硬件标准定义，而非整数范围。目前主流的 FP8 标准（如 IEEE 754-2019）中：

  - `qmax=448.0` 对应 FP8 的 “E4M3” 格式（指数位 4 位，尾数位 3 位）的最大正值范围；
  - `qmax=57344.0` 对应 FP8 的 “E5M2” 格式（指数位 5 位，尾数位 2 位）的最大正值范围。
- 逻辑：通过 `qmax` 的固定值直接匹配 FP8 类型（无需计算位数，因 FP8 不是整数离散范围）

```python
bit = int(np.log2(qmax-qmin+1))
        _if_ (bit == 8 and qmin < 0):
            dtype = 'int'
            quant_type = 'INT8'
        _elif_ (bit == 8 and qmin ==0):
            dtype = 'uint'
            quant_type = 'UINT8'
        _elif_ (bit == 4 and qmin < 0):
            dtype = 'int'
            quant_type = 'INT4'
```

整数量化类型（如 INT8、INT4）的量化范围是 “连续整数”，范围大小（`qmax - qmin + 1`）正好等于 `2^bit`（bit 为量化位数）。

位数（bit）：决定是 8 位还是 4 位量化（通过 `bit` 值判断）；

符号（qmin）：决定是带符号（`qmin < 0`，如 INT8、INT4）还是无符号（`qmin = 0`，如 UINT8）。

### 三、其他函数

- __init__(self, input_model_path, input_model_name)：初始化类成员变量，加载模型，设置输出路径、表名等。
- check_onnx_for_fakequant_nodes(self, onnx_model_path)：检查 ONNX 模型中是否包含 FakeQuant 节点。
- prepare_params(self)：准备模型的输入输出映射、参数、initializer。
- parse_attrs(self, node_attrs)：解析节点属性，转为 Python 字典。
- update_inp2node_out2node(self, graph)：建立输出到节点、输入到节点的映射。
- prepare_data(self, graph)：收集所有 initializer 和 Constant 节点的数据。
- prepare_initializer(self, graph)：收集所有 initializer 节点。
- get_constant_inputs(self, node)：获取节点的常量输入节点。
- remove_fake_pad_op(self)：移除所有 pads 全为 0 的 Pad 节点，并修正后续节点输入。
- deal_with_weight_fakequant(self, node)：处理权重量化的 FakeQuant 节点，将其替换为原始权重，返回冗余节点。
- weight_preprocess(self, target_tensor)：对权重做归一化处理（tanh+ 归一化），并找出冗余节点。
- get_constant_inputs(self, node)：获取节点的常量输入节点。
- post_process_clip_ranges(self)：对 Flatten、Resize、Reshape、Transpose 等节点的输入输出做 clip range 对齐，补全校准表。
- deal_with_activation_fakequant(self, node)：处理激活量化的 FakeQuant 节点，将其前后节点直接连接。
- export_tables(self)：导出校准表（calitable）和量化表（qtable）。
- get_activation_mappings(self, model)：获取激活节点的输入输出映射。
- align_final_opt(self, node_name_mapping, onnx_sim="")：对齐优化后的 ONNX 模型和原始模型的激活节点名。
- creat_new_qat_item(self, oir_name, new_name)：在表格中创建新的 QAT 项（用于节点名对齐）。
- align_canonicalize(self, mlir_file, test_result)：对齐 MLIR 文件中的激活名和 QAT 表。
- extract_activation_names_mlir(self, mlir_path)：从 MLIR 文件中提取激活名。
- compare_npz_name_mapping(self, test_result)：通过 npz 文件比对激活名，辅助对齐。

## 4.model_runner.py

### 一、整体定位

这个文件是一个多框架模型推理工具，支持 ONNX、MLIR、TFLite、Caffe、Torch、BModel 等多种格式的模型推理，并能将推理结果保存为 npz 文件。它既可以作为命令行工具使用，也可以作为 Python 模块被调用。主要功能包括：

- 加载输入数据（npz）
- 根据模型类型选择合适的推理后端
- 支持 dump 所有中间张量
- 支持多种硬件/仿真环境
- 支持多线程/多进程下的硬件资源锁管理

### 二、main 函数的整体流程

1. 参数解析：使用 argparse 统一解析命令行参数，获取输入文件、模型文件、输出文件、模型类型等信息。
2. 加载输入数据：通过 np.load(args.input) 加载输入的 npz 文件，得到输入张量字典。
3. 根据模型类型分发推理函数：通过判断模型文件的后缀，选择合适的推理函数进行模型推理。

   - .final.mlir → final_mlir_inference
   - .mlir → mlir_inference
   - .onnx → onnx_inference
   - .tflite → tflite_inference
   - .prototxt + .caffemodel → caffe_inference
   - .pt/.pth → torch_inference
   - .bmodel/.cvimodel → model_inference
   - 其他 → 报错
4. 保存推理结果

```python
_if_ __name__ == '__main__':
    _# yapf: disable_
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", _required_=True, _help_="input npz file")
    parser.add_argument("--model", _type_=str, _required_=True,
                        _help_="mlir/pytorch/onnx/tflie/bmodel/prototxt file.")
    parser.add_argument("--weight", _type_=str, _default_="",
                        _help_="caffemodel for caffe")
    parser.add_argument("--output", _default_='_output.npz',
                        _help_="output npz file")
    parser.add_argument("--dump_all_tensors", _action_='store_true',
                        _help_="dump all tensors to output file")
    parser.add_argument("--debug", _type_=str, _nargs_="?", _const_="",
                        _help_="configure the debugging information.")
    parser.add_argument("--out_fixed", _action_="store_true",
                        _help_="no float number transforming, only for int8/uint8.")
    parser.add_argument("--cuda", _action_="store_true",
                        _help_="use cuda to do inference")
    parser.add_argument("--decrypt_lib", _type_=str, _default_="",
                        _help_="use decrypt_lib to load encrypted bmodel")
    _# yapf: enable_
    args = parser.parse_args()
    data = np.load(args.input)
    output = dict()
    _if_ args.model.endswith("final.mlir"):
        output = final_mlir_inference(data, args.model, args.dump_all_tensors)
    _elif_ args.model.endswith(".mlir"):
        output = mlir_inference(data, args.model, args.dump_all_tensors, args.debug, args.out_fixed,
                                args.cuda)
    _elif_ args.model.endswith('.onnx'):
        output = onnx_inference(data, args.model, args.dump_all_tensors)
    _elif_ args.model.endswith(".tflite"):
        output = tflite_inference(data, args.model, args.dump_all_tensors)
    _elif_ args.model.endswith(".prototxt") and args.weight.endswith(".caffemodel"):
        output = caffe_inference(data, args.model, args.weight, args.dump_all_tensors)
    _elif_ args.model.endswith(".pt") or args.model.endswith(".pth"):
        output = torch_inference(data, args.model, args.dump_all_tensors)
    _elif_ args.model.endswith(".bmodel") or args.model.endswith(".cvimodel"):
        output = model_inference(data,
                                 args.model,
                                 _out_fixed_=args.out_fixed,
                                 _decrypt_lib_=args.decrypt_lib)
    _else_:
        _raise_ RuntimeError("not support modle file:{}".format(args.model))
    print("\nSaving ...")
    _if_ output:
        np.savez(args.output, **output)
        print("\nResult saved to:{}".format(args.output))
```

- final_mlir_inference：针对 .final.mlir 文件的推理，调用 pyfinalmlir 库，支持 dump 所有张量。
- mlir_inference：针对 .mlir 文件的推理，支持 CPU/CUDA，支持 dump 所有张量。
- onnx_inference：针对 .onnx 文件的推理，支持 dump 所有中间张量（通过修改 ONNX graph 的 output）。
- tflite_inference：针对 .tflite 文件的推理，支持 dump 所有中间张量，支持 NCHW/NHWC 格式转换。
- caffe_inference：针对 Caffe 的 prototxt/caffemodel 文件推理，支持 dump 所有中间张量。
- torch_inference：针对 PyTorch 的 pt/pth 文件推理，支持 dump 所有中间张量。
- model_inference：针对 bmodel/cvimodel 文件推理，支持硬件仿真、文件锁、dump 所有张量等。

### 三、其他函数功能汇总

##### show_fake_cmd

功能：打印可复现的命令行调用示例，方便调试和复现推理过程。

原理：通过字符串格式化将参数拼接成完整命令行字符串，直接输出到控制台。

##### get_chip_from_model

功能：获取模型对应的芯片类型。

原理：调用外部命令 `model_tool --chip` 解析模型文件，通过 `os.popen` 执行命令并读取输出结果，适用于 bmodel、cvimodel 等需要绑定芯片类型的模型格式。

##### pack_bmodel_context_generator

功能：保存 bmodel 的输入和输出数据到文件。

原理：利用 Python 生成器（`yield`）机制，先保存输入数据后暂停执行，待推理完成后恢复执行并保存输出数据，实现推理前后的数据记录。

##### ChipLock

功能：硬件资源锁类，用于多进程 / 多线程环境下管理硬件资源（如仿真器），防止资源冲突。

原理：

##### model_inference

功能：BModel/CVIMODEL 的统一推理入口。

原理：

##### get_cmodel_so / link_custom_so / link_cmodel_so

功能：选择并链接正确的仿真库（.so 文件），确保仿真环境匹配芯片类型。

原理：

##### _model_inference

功能：BModel/CVIMODEL 的底层推理实现。

原理：

##### final_mlir_inference

功能：针对 `.final.mlir` 文件的推理。

原理：

##### mlir_inference

功能：针对 `.mlir` 文件的推理，支持 CPU 和 CUDA 环境。

原理：

```python
def mlir_inference(inputs: dict,
                   mlir_file: str,
                   dump_all: bool = True,
                   mute: bool = False,
                   out_fixed: bool = False,
                   use_cuda: bool = False,
                   log_level: str = 'normal') -> dict:
    """
    基于MLIR文件执行模型推理（CPU或CUDA），返回推理结果
    
    参数说明：
    - inputs: 输入数据字典（键为输入名称，值为输入张量/数据）
    - mlir_file: 待执行的MLIR文件路径（通常是TPU方言或适配硬件的MLIR）
    - dump_all: 是否输出所有中间结果（用于调试，默认True）
    - mute: 是否静音（强制不输出日志，优先级高于log_level）
    - out_fixed: 输出是否为固定点格式（如量化模型的整数输出，默认False）
    - use_cuda: 是否使用CUDA加速（默认False，使用CPU推理）
    - log_level: 日志级别（控制输出详细程度，默认'normal'）
    返回值：
    - 推理结果字典（键为输出名称，值为输出张量/数据）
    """
    
    # 1. 处理静音/安静模式：重定向标准输出和错误输出（避免日志干扰）
    # 若开启静音（mute）或日志级别为quiet，将所有输出重定向到"黑洞"设备
    if mute or log_level == "quiet":
        # 打开Linux下的"黑洞"设备（/dev/null），所有写入的数据会被丢弃
        with open(os.devnull, "w") as devnull:
            # 复制标准输出（stdout）的文件描述符到黑洞设备（不再打印到控制台）
            os.dup2(devnull.fileno(), sys.stdout.fileno())
            # 复制标准错误（stderr）的文件描述符到黑洞设备（错误信息也不打印）
            os.dup2(devnull.fileno(), sys.stderr.fileno())
    
    try:
        # 2. 根据硬件选择推理方式（CPU或CUDA）
        if not use_cuda:  # 默认使用CPU推理
            # 调用CPU推理实现（内部执行MLIR文件，处理输入并返回结果）
            # 传入dump_all（是否输出中间结果）、out_fixed（输出格式）等参数
            return _mlir_inference_by_cpu(inputs, mlir_file, dump_all, out_fixed)
        else:  # 若指定use_cuda=True，使用CUDA加速推理
            # 调用CUDA推理实现（依赖GPU环境，速度更快）
            return _mlir_inference_by_cuda(inputs, mlir_file, dump_all)
    
    finally:
        # 3. 恢复标准输出和错误输出（无论推理是否成功，确保后续输出正常）
        # 仅在静音/quiet模式下需要恢复（否则无需操作）
        if mute or log_level == "quiet":
            # 将标准输出恢复为原始stdout（sys.__stdout__是Python保存的原始stdout）
            os.dup2(sys.__stdout__.fileno(), sys.stdout.fileno())
            # 将标准错误恢复为原始stderr
            os.dup2(sys.__stderr__.fileno(), sys.stderr.fileno())
```

##### _mlir_inference_by_cpu

功能：MLIR 模型在 CPU 环境下的底层推理实现。

原理：

##### _mlir_inference_by_cuda

功能：MLIR 模型在 CUDA 环境下的底层推理实现。

原理：

##### free_mlir_module

功能：释放全局 MLIR 模块资源。

原理：将全局 MLIR 模块变量设为 `None`，触发 Python 垃圾回收机制，避免长期运行时的内存泄漏。

##### onnx_inference

功能：ONNX 模型的推理实现。

原理：

##### caffe_inference

功能：Caffe 模型的推理实现。

原理：

##### tflite_inference

功能：TFLite 模型的推理实现。

原理：

##### torch_inference

功能：PyTorch 模型（`torch.jit` 导出的模型）的推理实现。

原理：

## 5.npz_tool.py

### 一、整体功能

这个脚本是一个多功能 npz 文件处理工具，用于对 .npz 文件（numpy 的多数组存储格式）进行各种操作，如对比、可视化、提取、合并、重命名、格式转换、统计等。它通过命令行参数选择不同的功能，适合模型开发、调试、量化、部署等场景下对中间/输出数据的批量处理。

### 二、主要结构与逻辑

1. 功能映射字典

```sql
npz_tool_func = {
    "compare": npz_compare,
    "print_diff": npz_print_diff,
    "visualize_diff": npz_visualize_diff,
    'dump':npz_dump,
    ...
}
```

- 用于根据命令行参数动态调用对应的处理函数。
- 每个 key 是命令行功能名，value 是实际执行的函数。

1. main 函数

```python
def main():
    args_list = sys.argv
    if len(args_list) < 2:
        funcs = npz_tool_func.keys()
        funcs_str = "["
        for idx, key in enumerate(npz_tool_func.keys()):
            funcs_str += key + ("|" if idx != len(funcs)-1 else '')
        funcs_str += "]"
        print(f"Usage: {args_list[0]} " + funcs_str + " ...")
        exit(-1)

    def NoneAndRaise(func):
        raise RuntimeError("No support {} Method".format(func))

    npz_tool_func.get(args_list[1], lambda x: NoneAndRaise(args_list[1]))(args_list[2:])
```

整体逻辑分为 3 步：

1. 参数数量检查：判断用户是否传入了至少 1 个功能名称（`len(args_list) < 2`）。若未传入，进入下一步提示用法；若已传入，直接执行对应功能。
2. 用法提示生成：当参数不足时，自动收集 `npz_tool_func` 字典中所有支持的功能名称，拼接成 `[功能1|功能2|...]` 格式的提示文本，告知用户工具的正确用法（如 `python script.py [功能名] ...`），然后退出。
3. 功能调用与错误处理：根据用户传入的功能名（`args_list[1]`），从 `npz_tool_func` 中查找对应的函数并调用（传入剩余参数 `args_list[2:]`）。若功能名不存在，通过 `NoneAndRaise` 函数抛出异常，提示 “不支持该功能”。

## 6.tpuc-opt.cpp

### 一、整体功能

这个文件实现了一个 TPU-MLIR 优化器命令行工具（类似于官方 MLIR 的 mlir-opt），用于对 MLIR（多层次中间表示）模块进行优化和转换。它是 TPU-MLIR 工具链中的核心组件之一，负责加载、注册所有自定义 Dialect 和 Pass，并根据命令行参数执行优化流程。

### 二、main 函数

#### 1）注册 Pass 和 Dialect

```python
tpu_mlir::registerAllPasses();
DialectRegistry registry;
tpu_mlir::registerAllDialects(registry);
```

- 注册所有自定义 Pass 和 Dialect，保证后续优化流程可用。

#### 2）处理 --deinit 选项

```go
bool hasDeinit = false;
for (int i = 1; i < argc; ++i) {
  if (std::string(argv[i]).find("--deinit") != std::string(argv[i]).npos) {
    hasDeinit = true;
    break;
  }
}
if (hasDeinit) {
  PluginPostPass = {"--mlir-print-debuginfo"};
} else {
  PluginPostPass = {"--deinit", "--mlir-print-debuginfo"};
}
```

- 检查命令行参数是否包含 --deinit。
- 如果有，则只加 --mlir-print-debuginfo，否则加 --deinit 和 --mlir-print-debuginfo。
- 这样可以灵活控制 pass 流程，避免重复 deinit。

#### 3）参数数量判断

```python
if (argc <= 2) {
  return asMainReturnCode(MlirOptMain(
      argc, argv, "TPU MLIR module optimizer driver\n", registry));
}
```

- 如果参数很少，直接调用 MLIR 的主入口，不做特殊处理。

#### 4）处理 --debug_cmd

```python
std::string debug_cmd = argv[argc - 1];
std::string substring = "--debug_cmd=";
if (debug_cmd.find(substring) != std::string::npos) {
  std::ofstream ofs;
  ofs.open("/tmp/debug_cmd", std::ios::out | std::ios::trunc);
  ofs << debug_cmd.substr(substring.size()) << std::endl;
  argc -= 1;
}
```

- 如果最后一个参数是 --debug_cmd=xxx，则将 xxx 写入 /tmp/debug_cmd 文件，并从参数列表中移除该参数。
- 便于调试和外部工具集成。

#### 5）构造新的参数列表

```python
int num_pre = sizeof(PluginPrePass) / sizeof(PluginPrePass[0]);
int num_post = PluginPostPass.size();
int new_argc = num_pre + argc + num_post;
char *new_argv[new_argc];
...
```

- 计算新参数数量（原始参数 + pre pass + post pass）。
- 构造新的参数数组 new_argv，将 pre pass、原始参数、post pass 按顺序插入。

##### 参数插入逻辑：

- 先插入原始参数（直到遇到第一个以 -- 开头的参数）。
- 插入 pre pass（如果原始参数中没有同名 pass）。
- 插入剩余原始参数（直到遇到 -o）。
- 插入 post pass。
- 插入剩余参数。

#### 6）调用 MLIR 主入口

```python
return asMainReturnCode(MlirOptMain(
    new_argc, new_argv, "TPU MLIR module optimizer driver\n", registry));
```

- 调用 MLIR 的主入口 MlirOptMain，传入新参数列表和注册表，执行优化流程。

## 7.mlir_shell.py

### 一、整体功能

这个文件是 TPU-MLIR 项目中的一个工具脚本，主要用于自动化 MLIR（Multi-Level Intermediate Representation）模型的转换、优化、量化、推理、编译等流程。它通过调用一系列命令行工具（如 tpuc-opt、mlir-opt、clang 等），将高层的 MLIR 文件一步步转换为适合 TPU 芯片（如 Sophgo BM1684/1684X 等）运行的二进制模型（bmodel），并支持调试、日志、性能分析等功能。

```python
输入：高层MLIR模型（如TOP方言）、配置参数（芯片型号/量化模式等）
  ↓
1. 高层优化：对MLIR进行形状推理、算子规范化（适配TPU方言）
  ↓
2. 硬件适配：将MLIR转换为目标芯片（如BM1684X）的TPU算子，融入量化配置
  ↓
3. 硬件级优化：层分组、核心并行、地址分配（最大化硬件利用率）
  ↓
4. 生成目标文件：转换为芯片可直接运行的bmodel格式
  ↓
5. 精度验证：对比转换前后的模型输出，确保精度达标
输出：可部署的bmodel、日志及中间文件
```

### 二、MLIR 优化与转换函数

#### top_opt_options

功能：用于生成 TPU-MLIR 工具链中对 TOP 方言进行高层优化的命令行选项，这些选项会被传递给 `tpuc-opt` 工具（TPU-MLIR 的核心优化工具），控制对 TOP 方言（TPU 专用的高层算子集合）的优化流程。

```python
def top_opt_options(add_postprocess: str = ""):
    # 1. 初始化优化选项列表，首先添加"--shape-infer"（形状推理）
    options = ["--shape-infer"]
    # 2. 若指定了后处理类型（如检测模型的NMS），添加对应的后处理选项
    if len(add_postprocess) > 0:
        options.extend([f"--add-postprocess=\"type={add_postprocess}\""])
    # 3. 添加通用优化选项：算子规范化和额外优化
    options.extend(["--canonicalize", "--extra-optimize"])
    # 4. 返回组装好的优化选项列表
    return options
```

#### mlir_opt_for_top

功能：`mlir_opt_for_top` 是 TPU-MLIR 工具链中对 TOP 方言 MLIR 文件进行优化的核心函数，它通过调用 `tpuc-opt` 工具（TPU-MLIR 的专用优化器），对输入的 TOP 方言 MLIR 文件执行一系列优化，并生成优化后的 MLIR 文件。同时支持日志级别控制和优化模式统计，方便调试和性能分析。

```python
def mlir_opt_for_top(mlirfile: str,
                     opt_mlirfile: str,
                     add_postprocess: str = "",
                     count_patterns: bool = False, 
                     log_level:str="normal"):
    # 1. 初始化命令列表：调用tpuc-opt工具，指定输入的MLIR文件
    cmd = ["tpuc-opt", mlirfile]
    
    # 2. 获取TOP方言的优化选项（复用之前的top_opt_options函数）
    options = top_opt_options(add_postprocess)
    cmd.extend(options)  # 将优化选项添加到命令中
    
    # 3. 指定优化后的输出MLIR文件路径
    cmd.extend(["-o", opt_mlirfile])
    
    # 4. 处理模式统计（记录哪些优化模式被应用）
    log_file = ""
    if count_patterns:
        log_file = "top_patterns.log"  # 日志文件保存路径
        # 添加调试选项：记录模式应用、方言转换、贪婪重写器的详细日志
        cmd.extend([
            "-debug-only=pattern-application,dialect-conversion,greedy-rewriter",
            "> {} 2>&1".format(log_file)  # 将日志重定向到文件
        ])
    
    # 5. 处理日志级别（控制输出详细程度）
    if log_level == "quiet":
        cmd.extend(["> /dev/null"])  # 安静模式：不输出任何日志
    elif log_level == "simple":
        cmd.insert(2, '--init="level=1"')  # 简单模式：只输出关键信息
    
    # 6. 执行命令（调用操作系统接口运行tpuc-opt）
    _os_system(cmd, log_level=log_level)
    
    # 7. 返回匹配的优化模式统计结果（从日志文件中解析）
    return get_matched_patterns(log_file)
```

#### top_to_tosa

功能：将高层的 TOP 方言转换为标准化的 TOSA 方言，是模型编译流程中的重要一步，目的是让模型能够适配更广泛的硬件平台，或为后续的硬件特定优化（如量化、算子融合）提供统一的基础。

```python
def top_to_tosa(top_mlir: str, tosa_mlir: str, includeWeight: bool = False):
    # 初始化命令列表，基础工具为 "tpuc-opt"，输入文件为 top_mlir
    cmd = ["tpuc-opt", top_mlir]

    # 构建转换参数：--convert-top-to-tosa，包含 includeWeight 的配置
    lower_param = "--convert-top-to-tosa=\"includeWeight="
    if includeWeight:
        lower_param += "True\""  # 如果需要包含权重，参数值设为 True
    else:
        lower_param += "False\""  # 否则设为 False

    # 扩展命令列表，添加转换参数、规范化处理和输出文件
    cmd.extend([lower_param, "--canonicalize", "-o", tosa_mlir])
    _os_system(cmd)  # 执行构建好的命令列表
```

#### tosa_to_llvm

功能：该函数用于将 TOSA 方言的 MLIR 文件 转换为 LLVM 目标文件（.obj），适配 CPU 执行（LLVM IR 是 CPU 可执行的底层中间表示）。

详细流程可见下：

```python
TOSA方言MLIR（高层抽象）
  ↓（mlir-opt：10步方言转换+优化）
LLVM方言MLIR（MLIR格式的LLVM兼容表示）
  ↓（mlir-translate：格式转换）
LLVM IR（LLVM原生中间表示）
  ↓（llc：编译为机器码）
LLVM目标文件（.obj，x86_64机器码，CPU可执行）
```

##### 阶段 1：MLIR 内部方言转换（`mlir-opt` 工具，输入 TOSA MLIR）

这一阶段通过 `mlir-opt` 的 `--pass-pipeline` 定义的转换流水线，将 TOSA 方言逐步转为 LLVM 兼容的 MLIR 方言（降低抽象级别，接近硬件执行逻辑）。共包含 10 个细分转换步骤：

##### 阶段 2：MLIR 转 LLVM IR（`mlir-translate` 工具）

- 工具：`mlir-translate --mlir-to-llvmir`
- 输入：阶段 1 输出的 “LLVM 方言 MLIR”（MLIR 格式，语法接近 LLVM IR）
- 输出：LLVM IR（文本格式，如 `%0 = add i32 %1, %2`，LLVM 的原生中间表示）
- 作用：将 ML 格式的 LLVM 方言转为 LLVM 原生的 IR 格式（LLVM 生态的 “通用中间表示”，后续编译依赖 LLVM 工具链）。

##### 阶段 3：LLVM IR 转目标文件（`llc` 工具）

- 工具：`llc -mtriple=x86_64-unknown-linux-gnu --filetype=obj`
- 输入：阶段 2 输出的 LLVM IR（文本）
- 输出：`objfile`（.obj 目标文件，二进制格式，包含 x86_64 架构的机器码）
- 关键参数：`-mtriple=x86_64-unknown-linux-gnu` 指定目标架构（x86_64 Linux），确保生成的机器码适配 CPU；`--filetype=obj` 指定输出为目标文件（而非汇编文本）。
- 作用：将 LLVM IR 编译为 CPU 可执行的机器码（二进制指令），最终生成可被链接器（如 `ld`）处理的目标文件。

```python
# TOSATOObj：函数功能标识（TOSA方言转LLVM目标文件）
def tosa_to_llvm(tosa_mlir: str, objfile: str):
    """
    将TOSA方言的MLIR文件转换为LLVM目标文件（.obj），适配CPU执行
    
    参数：
    - tosa_mlir：输入TOSA方言MLIR文件路径（高层通用方言，与硬件无关）
    - objfile：输出LLVM目标文件路径（.obj格式，可被CPU直接执行或链接为可执行程序）
    """
    # 1. 初始化命令列表：调用mlir-opt工具（MLIR优化器），指定输入TOSA MLIR文件
    # mlir-opt是MLIR官方优化工具，用于执行方言转换、优化等流水线操作
    cmd = ["mlir-opt", tosa_mlir]
    
    # 2. 定义TOSA到LLVM的转换参数（核心转换流水线）
    # lower_param是一串MLIR转换和优化的参数，通过"--pass-pipeline"指定完整转换流程
    lower_param = (
        # 开始定义模块级转换流水线（builtin.module表示对整个模块执行）
        "--pass-pipeline=\"builtin.module("
        # 阶段1：TOSA方言转底层方言（将TOSA算子转为Linalg、Arith等中间方言）
        "func.func(tosa-to-linalg-named, tosa-to-linalg, tosa-to-arith, tosa-to-tensor, tosa-to-scf), "
        # 阶段2：Tensor方言转Linalg（进一步降低抽象级别）
        "convert-tensor-to-linalg, "
        # 阶段3：Linalg转Affine（将线性代数算子转为仿射循环，接近硬件执行逻辑）
        "func.func(canonicalize, linalg-bufferize, convert-linalg-to-affine-loops, affine-loop-fusion, affine-simplify-structures, lower-affine), "
        # 阶段4：函数缓冲化（将SSA值转为内存缓冲，适配硬件内存模型）
        "func-bufferize, "
        # 阶段5：张量缓冲化（将张量操作转为内存操作）
        "func.func(tensor-bufferize, llvm-request-c-wrappers), "
        # 阶段6：算术和控制流方言转LLVM（Arith/Scf转LLVM兼容格式）
        "arith-expand, arith-bufferize, normalize-memrefs, convert-scf-to-cf, "
        # 阶段7：数学/算术/函数/控制流方言最终转LLVM（核心转换步骤）
        "convert-math-to-llvm, convert-arith-to-llvm, convert-func-to-llvm, convert-cf-to-llvm, "
        # 阶段8：内存引用转LLVM（MemRef方言转LLVM内存模型）
        "convert-bufferization-to-memref, memref-expand, expand-strided-metadata, finalize-memref-to-llvm, "
        # 阶段9：优化和合法化（确保LLVM IR可被导出为目标文件）
        "canonicalize, llvm-legalize-for-export, reconcile-unrealized-casts)\""
        # 阶段10：MLIR转LLVM IR（mlir-translate工具将MLIR转为LLVM IR）
        "| mlir-translate --mlir-to-llvmir "
        # 阶段11：LLVM IR转目标文件（llc工具编译为x86_64架构的.obj文件）
        "| llc -mtriple=x86_64-unknown-linux-gnu --filetype=obj"
    )
    
    # 3. 将转换参数和输出路径添加到命令中
    cmd.extend([lower_param, "-o", objfile])  # -o指定输出目标文件路径
    
    # 4. 执行命令（调用操作系统接口运行转换流水线）
    # _os_system是封装的系统命令执行函数（如调用subprocess执行cmd）
    _os_system(cmd)
```

#### mlir_lowering

功能：将输入的 `top_mlir`（高层 TOP 格式 MLIR）通过一系列编译优化（如量化、算子适配、硬件并行配置等），转换为 `tpu_mlir`（TPU 专用低层 MLIR），使其能在目标 TPU 上高效执行。

```python
def mlir_lowering(top_mlir: str,
                  tpu_mlir: str,
                  mode: str,
                  chip: str,
                  num_device: int = 1,
                  num_core: int = 1,
                  cali_table: str = None,
                  asymmetric: bool = False,
                  quantize_table: str = None,
                  customization_format: str = None,
                  fuse_preprocess: bool = False,
                  aligned_input: bool = False,
                  high_precision: bool = False,
                  do_winograd: bool = False,
                  q_group_size: int = 0,
                  q_symmetric: bool = False,
                  count_patterns: bool = False,
                  addr_mode: str = "auto",
                  mute: bool = False,
                  log_level: str = "normal",
                  matmul_perchannel: bool = False,
                  gelu_mode: str = "normal"):
    # 1. 统一模式参数格式（避免大小写不一致导致的错误，如"inference"转为"INFERENCE"）
    mode = mode.upper()
    
    # 2. 初始化命令列表：调用tpuc-opt工具，指定输入的TOP方言MLIR文件
    # top_mlir是待lowering的TOP方言MLIR文件（高层抽象，与硬件无关）
    cmd = ["tpuc-opt", top_mlir]
    
    # 3. 处理量化权重文件（仅当需要量化时生成）
    weight_name = ""  # 初始化权重文件名（默认空，不生成）
    if quantize_table:  # 如果指定了量化表（用于模型量化，减少精度损失）
        # 断言输出文件是.mlir格式（避免路径错误，确保权重文件路径正确生成）
        assert (tpu_mlir.endswith(".mlir"))
        # 生成权重文件路径：与输出TPU MLIR同目录，名称为"输出文件名_qtable_weights.npz"
        # 例如tpu_mlir为"model.tpu.mlir"，则权重文件为"model.tpu_qtable_weights.npz"
        weight_name = tpu_mlir[:-len(".mlir")] + "_qtable_weights.npz"
    
    # 4. 获取TPU lowering选项（根据硬件和配置生成lowering规则）
    # lowering_options函数会根据参数生成适配目标芯片的lowering规则
    # 例如：芯片型号（chip）决定算子实现方式，量化参数（asymmetric）决定量化逻辑
    options = lowering_options(mode,
                                chip,
                                num_device,
                                num_core,
                                cali_table,
                                asymmetric,
                                quantize_table,
                                weight_name,
                                customization_format,
                                fuse_preprocess,
                                aligned_input,
                                high_precision,
                                do_winograd,
                                q_group_size,
                                q_symmetric,
                                addr_mode,
                                matmul_perchannel,
                                gelu_mode)
    cmd.extend(options)  # 将lowering选项添加到命令中，指导tpuc-opt执行lowering
    
    # 5. 指定lowering后的输出TPU MLIR文件路径
    # tpu_mlir是lowering后的TPU方言MLIR文件（底层抽象，与硬件相关，可直接在TPU上执行）
    cmd.extend(["-o", tpu_mlir])
    
    # 6. 处理模式统计（记录lowering过程中应用的优化模式）
    log_file = ""  # 初始化日志文件路径（默认空，不记录）
    if count_patterns:  # 如果需要统计lowering中的优化模式（如调试lowering是否符合预期）
        log_file = "tpu_patterns.log"  # 日志文件固定保存路径
        # 添加调试选项：记录模式应用、方言转换、贪婪重写器的日志（与mlir_opt_for_top逻辑一致）
        cmd.extend(["--debug-only=pattern-application,dialect-conversion,greedy-rewriter", 
                   "> {} 2>&1".format(log_file)])
    
    # 7. 处理日志级别（控制输出详细程度，适配不同场景）
    if log_level == "quiet":  # 安静模式：不输出任何日志
        cmd.extend(["> /dev/null"])
    elif log_level == "only-layer-group":  # 仅输出层分组日志（用于调试硬件资源分配）
        # --debug-only指定只输出层分组相关日志（层分组是TPU并行计算的基础）
        cmd.extend(["--debug-only=layer-group,LayerGroupUtil"])
        # --init="level=2"设置日志级别2（输出较详细的层分组信息）
        cmd.insert(2, '--init="level=2"')
    elif log_level == "simple":  # 简单模式：只输出关键信息（如lowering开始/结束）
        cmd.insert(2, '--init="level=1"')  # 日志级别1（仅关键信息）
    
    # 8. 执行命令（调用操作系统接口运行tpuc-opt，执行lowering）
    # mute控制是否静音（覆盖log_level，强制不输出），log_level控制输出详细程度
    _os_system(cmd, mute=mute, log_level=log_level)
    
    # 9. 返回lowering过程中的优化模式统计结果（从日志文件中解析）
    # 例如：哪些算子被成功lowering到TPU，哪些优化规则被应用
    return get_matched_patterns(log_file)
```

6.mlir_to_model()

功能：该函数是将 TPU 方言 MLIR 转换为最终可执行模型（如 BModel）的核心入口，负责构建转换命令并配置关键参数。

```python
def mlir_to_model(
    *,
    tpu_mlir: str,
    bmodel_path: str,
    final_mlir: str,
    dynamic: bool = False,
    quant_input: bool = False,
    quant_output: bool = False,
    quant_input_list: str = "",
    quant_output_list: str = "",
    disable_layer_group: bool = False,
    opt: int = 2,
    merge_weight: bool = False,
    op_divide: bool = False,
    embed_debug_info: bool = False,
    group_by_cores: str = "auto",
    model_version: str = "",
    count_patterns: bool = False,
    compress_mode: str = "none",
    future_update_rank: int = 0,
    future_update_list: str = "",
    debug_info: str = "",
    log_level: str = "normal",
    trunc_final: list = None,
    command_mem: dict = None,
    quant_output_bf16: bool = False,
    opt_post_processor: bool = False,
    gdma_check: bool = True,
    lg_debugger: bool = False,
):
    """
    将TPU方言MLIR转换为目标硬件可执行模型（如BModel）
    
    核心逻辑：
    - 构建tpuc-opt工具的转换命令，集成量化、优化、层分组等配置
    - 生成最终模型文件（bmodel_path）和中间MLIR文件（final_mlir）
    - 记录转换过程中的命令（用于调试和追溯）
    """
    # 1. 初始化命令记录字典（若未传入则创建空字典）
    if command_mem is None:
        command_mem = {}  # 用于存储转换过程中执行的命令（后续可被外部记录）
    
    # 2. 初始化转换命令：调用tpuc-opt工具，指定输入的TPU方言MLIR文件
    # tpuc-opt是TPU专用的MLIR优化工具，tpu_mlir是已lowering的TPU方言MLIR（中间产物）
    cmd = ["tpuc-opt", tpu_mlir]
    
    # 3. 配置调试命令参数（传递调试信息，如特定模块的日志开关）
    debug_cmd = f"--debug_cmd={debug_info}"  # 将调试信息封装为命令行参数
    
    # 4. 生成TPU优化选项（量化相关配置）
    # tpu_opt_options函数根据参数生成量化输入/输出的配置选项
    options = tpu_opt_options(
        quant_input,  # 是否量化输入
        quant_output,  # 是否量化输出
        quant_input_list,  # 指定需要量化的输入列表
        quant_output_list,  # 指定需要量化的输出列表
        quant_output_bf16=quant_output_bf16  # 是否用BF16格式量化输出
    )
    cmd.extend(options)  # 将量化选项添加到命令中，指导tpuc-opt执行量化逻辑
```

## 8.model_deploy.py

### 一、整体功能

本文件是 TPU-MLIR 工具链中模型部署的主入口脚本，主要用于将 MLIR 文件转换为可在 TPU 芯片上运行的 bmodel 文件，并支持推理、验证、量化、调试等功能。

##### 文件整体结构与主流程

- 依赖导入：导入了大量 utils 下的工具函数、类，以及 numpy、argparse、os 等常用库。
- 核心类 DeployTool：封装了模型转换、推理、验证、输入准备等主要流程。
- 主函数入口：解析命令行参数，实例化 DeployTool，依次执行 lowering、build_model、cleanup 等步骤。

### 二、 主函数流程

1. 参数解析：使用 argparse 解析命令行参数，支持模型路径、芯片类型、量化方式、输入输出、调试选项等几十个参数。
2. 参数合法性检查：对废弃参数、冲突参数进行检查。
3. 实例化 DeployTool：传入参数，初始化部署工具。
4. lowering：调用 lowering，将 mlir 转为 tpu/tosa mlir。
5. build_model：调用 build_model，将 tpu/tosa mlir 编译为 bmodel。
6. cleanup：非 debug 模式下清理临时文件。
7. pattern 检查：如果指定了 patterns_count，检查优化 pattern 是否达到预期次数。
8. 主要函数/类详细解释

```python
_if_ __name__ == '__main__':
    logger.info("TPU-MLIR {}".format(pymlir.__version__))
    parser = argparse.ArgumentParser()
    _# yapf: disable_
    _# ========== Basic Options ===========_
    parser.add_argument("--mlir", _required_=True, _help_="top mlir from model_transform.py")
    ... #参数解析，同上
# yapf: enable  # 启用yapf代码格式化（确保代码风格一致）
    # 1. 解析命令行参数（从用户输入中提取配置，如模型路径、量化模式等）
    args = parser.parse_args()  # parser是预定义的参数解析器，args存储所有参数值
    
    # 2. 处理废弃参数（提示用户使用新参数，保证兼容性）
    # 若用户使用了--io_alone（旧参数），提示用--addr_mode io_alone（新参数）
    deprecated_option(args.io_alone, "DEPRECATED, please use --addr_mode io_alone")
    # 若用户使用了--ignore_f16_overflow（旧参数），提示用--high_precision（新参数）
    deprecated_option(args.ignore_f16_overflow, "DEPRECATED, please use --high_precision")
    
    # 3. 量化参数合法性校验（防止矛盾配置，保证量化逻辑正确）
    # 场景1：若启用BF16量化输出（quant_output_bf16=True）
    if args.quant_output_bf16:
        # 禁止同时设置全局量化模式为BF16（需用--quant_output替代）
        if args.quantize == "BF16":
            RuntimeError("quantize is BF16, please use --quant_output instead")
        # 禁止同时启用普通量化输出（quant_output与quant_output_bf16互斥）
        if args.quant_output:
            RuntimeError("quant_output and quant_output_bf16 can't both be true")
    
    # 4. 输入格式与预处理参数校验（确保输入处理逻辑一致）
    # 场景1：若自定义格式为YUV（图像格式），强制启用输入对齐（提升硬件访问效率）
    if args.customization_format.startswith("YUV"):
        args.aligned_input = True  # 自动设置aligned_input=True（无需用户手动配置）
    # 场景2：若未融合预处理（fuse_preprocess=False），禁止设置自定义格式
    # 原因：自定义格式（如YUV转RGB）需依赖预处理逻辑，未融合时无法生效
    if not args.fuse_preprocess and args.customization_format:
        # 断言失败并提示错误（0 and "..." 等价于False，触发assert错误）
        assert (0 and "Error! If not fuse_preprocess, customization_format shouldn't be set.")
    
    # 5. 初始化部署工具（封装所有部署流程，如模型lowering、构建、验证）
    tool = DeployTool(args)  # 将解析后的参数传入工具类，初始化部署环境
    
    # 6. 执行模型lowering（核心步骤1：将高层MLIR转为硬件相关MLIR）
    # 若启用"不生成BModel"模式（not_gen_bmodel=True，仅调试lowering过程）
    if args.not_gen_bmodel:
        tool.do_validate = False  # 关闭验证（无需验证未完成的模型）
    # 调用tool.lowering()执行lowering（如TOP方言→TPU方言），返回lowering过程的优化模式统计
    lowering_patterns = tool.lowering()
    
    # 7. 生成目标模型（核心步骤2：将硬件相关MLIR转为可执行模型，如BModel）
    # 若仅lowering而不生成BModel，直接退出（调试场景）
    if args.not_gen_bmodel:
        exit(0)
    # 调用tool.build_model()生成BModel，返回模型构建过程的优化模式统计
    tpu_patterns = tool.build_model()
    
    # 8. 清理临时文件（默认启用，调试模式保留）
    if not args.debug:  # 非调试模式（生产环境）
        tool.cleanup()  # 删除中间产物（如临时MLIR、日志文件），节省磁盘空间
    
    # 9. 统计并验证优化模式应用次数（确保优化按预期生效）
    # 合并lowering和构建过程的所有优化模式统计（键为模式名称，值为应用次数）
    total_patterns = {** lowering_patterns, **tpu_patterns}
    # 若指定了模式次数校验（patterns_count非空，如{"conv_fuse": 5}）
    if args.patterns_count:
        # 遍历所有需要校验的模式
        for k, v in args.patterns_count.items():
            # 断言模式存在且应用次数符合预期（否则报错并提示具体差异）
            assert k in total_patterns and v == total_patterns[k], \
            "The number of times {} was applied does not meet the requirements. Expected {}, got {}" \
            .format(k, v, total_patterns.get(k))
```

### 三、 辅助函数/核心类

#### 辅助函数：

##### str2list(v)

- 功能：将逗号分隔的字符串转为字符串列表，并去除空字符串。
- 原理：字符串分割 +strip+ 去空。
- 用途：用于 argparse 解析多输入文件参数。

##### getCustomFormat(pixel_format, channel_format)

- 功能：根据像素格式和通道格式，返回自定义格式字符串（如 RGB_PLANAR、BGR_PACKED 等）。
- 原理：条件判断映射。
- 用途：用于 fuse_preprocess 时，确定输入图片的格式。

#### 核心类 DeployTool：

##### _init__(self, args)

- 功能：初始化部署工具，解析参数，设置各种路径、前缀、状态，准备输入数据，初始化文件记录器。
- 原理：参数赋值、路径拼接、状态判断、目录创建、调用 _prepare_input_npz。_
- 用途：为后续模型转换、推理、验证等流程做准备。

##### cleanup(self)

- 功能：清理临时文件。
- 原理：调用 file_clean 工具函数。
- 用途：部署结束后清理环境。

##### pack_profile(self)

- 功能：打包 profile 相关文件（mlir、onnx、tpu.mlir 等）到一个目录，便于调试和归档。
- 原理：shutil 复制文件，调用 mlir2onnx 转换。
- 用途：便于后续分析和复现。

##### lowering(self)

- 功能：将 top-level mlir 降级为 tpu/tosa mlir，支持 cpu/tpu 两种芯片。
- 原理：

  - cpu：调用 top_to_tosa，再替换 func 名称，生成 tosa mlir。
  - tpu：调用 mlir_lowering（来自 mlir_shell.py），生成 tpu.mlir，并可选进行验证。
- 用途：模型转换流程的第一步。

##### _prepare_input_npz(self)

- 功能：准备推理输入数据（npz/npy/image），并生成参考输出（ref_npz）。
- 原理：

  - 判断输入类型，支持 npz、npy、图片等。
  - 支持 fuse_preprocess，自动推断格式。
  - 支持自动生成参考输出（top_outputs）。
  - 支持动态层输出数据 dump。
- 用途：为模型推理和验证准备输入和参考输出。

##### validate_tpu_mlir(self)

- 功能：对 tpu.mlir 进行推理，并与参考输出对比，支持 cuda 验证。
- 原理：

  - 调用 mlir_inference 进行推理。
  - 调用 f32_blobs_compare 进行输出对比。
  - 支持 cuda 推理和对比。
- 用途：验证 tpu.mlir 的正确性。

##### build_model(self)

- 功能：将 tpu.mlir/tosa.mlir 编译为 bmodel 文件，并可选进行推理验证。
- 原理：

  - cpu：调用 tosa_to_llvm。
  - tpu：调用 mlir_to_model（来自 mlir_shell.py），生成 bmodel，并可选进行推理验证。
  - 记录生成的文件和命令。
- 用途：模型转换流程的第二步，生成最终可部署的模型文件。

##### revise_MaskRCNN_tpu_ref(self)

- 功能：修正 MaskRCNN 的 tpu 参考输出，保证 key 顺序一致。
- 原理：遍历 key，重新映射。
- 用途：特殊模型的兼容性处理。

##### validate_model(self)

- 功能：对 bmodel 进行推理，并与参考输出或 tpu 输出对比。
- 原理：

  - 调用 model_inference 进行推理。
  - MaskRCNN 特殊处理。
  - 根据模型状态选择对比对象（ref_npz 或 tpu_npz）。
  - 调用 f32_blobs_compare 进行输出对比。
- 用途：验证最终 bmodel 的正确性。

### 四、 重要代码解析

#### lowering(self)

- 原理：cpu 走 tosa_to_llvm，tpu 走 mlir_to_model，生成 bmodel 并可选验证。

##### CPU 分支：

- 核心操作：调用 `top_to_tosa` 工具（可跳转到 7.2.3）将输入的 MLIR 转换为 TOSA dialect 格式。
  TOSA（Tensor Operator Set Architecture）是一种标准化的机器学习算子集中间表示，适合在 CPU 等通用硬件上进行优化和执行。
- 辅助处理：替换函数名（`main`→`model`）是为了统一模型入口函数的命名规范，便于后续流程识别。

```python
if self.chip == 'cpu':
    # 将输入的 MLIR 文件转换为 TOSA  dialect 格式，保存为临时文件
    top_to_tosa(self.mlir_file, "tmp_tosa.mlir", self.includeWeight)
    # 定义输出的 TOSA MLIR 文件名（用前缀+固定后缀命名）
    self.tosa_mlir = "{}_tosa.mlir".format(self.prefix)
    # 读取临时文件内容，将函数名从 "main" 替换为 "model"（统一命名规范）
    with open("tmp_tosa.mlir", "r", encoding="utf-8") as file:
        content = file.read()
    content = content.replace("main", "model")
    # 写入最终的 TOSA MLIR 文件，并删除临时文件
    with open(self.tosa_mlir, "w", encoding="utf-8") as file:
        file.write(content)
    delete_file("tmp_tosa.mlir")
    return {}
```

##### 非 CPU 分支：

- 核心操作：调用 `mlir_lowering` 工具（可跳转到 7.2.5）将 MLIR lowering 为 TPU 专用格式。
  “lowering” 是编译领域的术语，指将高层中间表示（接近算法逻辑）转换为低层中间表示（接近硬件执行逻辑），以便进行硬件特定的优化（如算子适配、内存布局调整等）。
- 关键参数解析：

  - 量化相关参数（`quantize`、`cali_table` 等）：TPU 通常擅长整数计算，量化能减少计算量和内存占用；
  - 硬件配置（`num_device`、`num_core`）：适配多设备 / 多核心的并行执行；
  - 优化选项（`fuse_preprocess`、`do_winograd`）：融合算子减少数据搬运、用 Winograd 算法加速卷积，提升执行效率。
- 验证步骤：`validate_tpu_mlir` 用于检查 lowering 后的 MLIR 是否符合 TPU 执行要求（如算子支持、格式正确等），避免部署时出错。

#### build_model

- ```
  功能：构建目标芯片可执行的模型（如BModel），包含模型转换、验证和文件记录。
  ```
- ```
  核心逻辑：
  ```

  - 针对 CPU：将 TOSA 方言 MLIR 转为 LLVM 方言（适配 CPU 执行），调用 tosa_to_llvm()函数（可看 7.2.4）。
  - 针对非 CPU：调用 mlir_to_model 核心函数：将 TPU 方言 MLIR 转换为最终模型。
  - 自动完成模型验证（可选）和文件记录（保存中间产物和命令）。

```python
def build_model(self):

    try:
        # 1. 根据目标芯片类型选择构建逻辑
        if self.chip == "cpu":
            # 1.1 CPU场景：将TOSA方言MLIR转换为LLVM方言MLIR（CPU可执行）
            # tosa_to_llvm：专用转换函数，TOSA是高层通用方言，LLVM是CPU底层方言
            tosa_to_llvm(self.tosa_mlir, self.bmodel_path)
            return {}  # CPU场景无优化模式统计，返回空字典
        
        else:
            # 1.2 非CPU场景（如TPU）：生成目标芯片可执行的模型（如BModel）
            command_mem = {}  # 用于记录构建过程中执行的命令（后续保存）
            
            # 调用mlir_to_model核心函数：将TPU方言MLIR转换为最终模型（如BModel）
            # 传入大量配置参数，控制模型转换、优化、量化等逻辑
            patterns = mlir_to_model(
                tpu_mlir=self.tpu_mlir,  # 输入：TPU方言MLIR文件（已lowering的中间产物）
                bmodel_path=self.bmodel_path,  # 输出：最终可执行模型路径（如BModel）
                final_mlir=self.final_mlir,  # 输出：最终优化后的MLIR文件（用于调试）
                dynamic=self.dynamic,  # 是否支持动态形状输入
                quant_input=self.quant_input,  # 是否量化输入
                quant_output=self.quant_output,  # 是否量化输出
                quant_input_list=self.quant_input_list,  # 指定需要量化的输入列表
                quant_output_list=self.quant_output_list,  # 指定需要量化的输出列表
                disable_layer_group=self.disable_layer_group,  # 是否禁用层分组（TPU并行计算的基础）
                opt=self.opt,  # 是否启用优化（如算子融合、常量折叠）
                merge_weight=self.merge_weight,  # 是否合并权重（减少内存占用）
                op_divide=self.op_divide,  # 是否拆分大算子（适配硬件计算能力）
                embed_debug_info=self.embed_debug_info,  # 是否嵌入调试信息（便于定位问题）
                group_by_cores=self.group_by_cores,  # 是否按核心分组（优化多核心并行）
                model_version=self.model_version,  # 模型版本（用于兼容性控制）
                # 是否统计优化模式应用情况（根据self.patterns_count开关决定）
                count_patterns=True if self.patterns_count else False,
                compress_mode=self.compress_mode,  # 模型压缩模式（如权重压缩）
                future_update_rank=self.future_update_rank,  # 后续更新的秩（预留功能）
                future_update_list=self.future_update_list,  # 后续更新的算子列表（预留功能）
                debug_info=self.debug_cmd,  # 调试命令（传递给底层工具）
                trunc_final=self.trunc_final,  # 是否截断最终MLIR（简化输出）
                command_mem=command_mem,  # 传入命令记录字典（底层会填充执行过的命令）
                quant_output_bf16=self.quant_output_bf16,  # 是否用BF16量化输出（平衡精度和性能）
                opt_post_processor=self.opt_post_processor,  # 后处理优化器（自定义优化逻辑）
                gdma_check=self.gdma_check,  # 是否启用GDMA（全局DMA）检查（内存访问安全）
                lg_debugger=self.lg_debugger  # 层分组调试器（调试多核心分组逻辑）
            )
            
            # 2. 模型验证（可选，确保构建的模型可用）
            # 若未跳过验证且启用验证开关，则执行验证
            if not self.skip_validation and self.do_validate:
                self.validate_model()  # 调用验证方法（如检查输出形状、精度是否符合预期）
            
            return patterns  # 返回优化模式统计结果（供调试分析）
    
    # 2. 最终处理：无论构建是否成功，记录关键文件和命令（便于追溯和调试）
    finally:
        # 仅非CPU场景需要记录（CPU场景产物简单，无需详细记录）
        if self.chip != "cpu":
            # 2.1 记录构建过程中的关键文件（中间产物、输出模型等）
            self.file_recorder.add_file(
                bmodel=self.bmodel_path,  # 最终生成的BModel路径
                tensor_location=f"{self.bmodel_path}.json",  # 张量位置信息文件
                final_mlir=self.final_mlir,  # 最终优化后的MLIR文件
                tpu_mlir=self.tpu_mlir,  # TPU方言MLIR（中间产物）
                tpu_opt_mlir=self.tpu_opt_mlir,  # 优化后的TPU方言MLIR
                tpu_output=self.tpu_npz,  # TPU权重文件（NPZ格式）
                bmodel_output=self.model_npz,  # BModel输出结果（NPZ格式，用于验证）
                context_dir=self.context_dir,  # 上下文目录（存放临时文件）
                layer_group_cache=f"{self.prefix}.layer_group_cache.json",  # 层分组缓存（加速下次构建）
            )
            
            # 2.2 记录构建过程中执行的命令（如mlir_to_model调用的底层工具命令）
            self.file_recorder.add_command(** command_mem)  # command_mem由mlir_to_model填充
            
            # 2.3 将记录的文件和命令写入磁盘（持久化保存）
            self.file_recorder.dump()
```

#### _prepare_input_npz

- 功能：该函数是模型验证前的输入数据准备核心函数，负责处理测试输入（图片、NPZ、NPY 等）、生成模型可接收的输入格式（NPZ），并根据需要生成参考输出（用于后续模型验证）。
- 核心逻辑：

  - 处理多种输入类型（图片、NPZ、NPY），适配模型输入要求。
  - 支持预处理融合（fuse_preprocess），自动处理图片预处理（如 resize、格式转换）。
  - 生成输入 NPZ 文件（供模型推理使用）和参考输出（供验证模型正确性）。

```python
def _prepare_input_npz(self):
    # 1. 初始化验证开关：根据测试输入数量决定是否执行验证（有输入才验证）
    num_inputs = len(self.test_input)  # self.test_input：测试输入文件列表（如图片、NPZ路径）
    self.do_validate = (0 < num_inputs)  # 若输入数量>0，则开启验证（do_validate设为True）
    
    # 2. 无输入时的处理（不执行验证，仅初始化预处理格式）
    if not self.do_validate:  # 若无需验证（无输入）
        if self.customization_format == '':  # 若未指定自定义格式
            ppa = preprocess()  # 初始化预处理工具
            input_op = self.module.inputs[0].op  # 获取模型第一个输入算子（用于加载预处理配置）
            ppa.load_config(input_op)  # 从输入算子加载预处理配置（如像素格式、通道格式）
            if ppa.has_pre:  # 若存在预处理逻辑
                # 生成自定义格式（基于预处理的像素格式和通道格式）
                self.customization_format = getCustomFormat(ppa.pixel_format,
                                                            ppa.channel_format)
        return  # 无输入时直接返回，不继续处理
    
    # 3. 初始化输入存储变量（用于保存模型可接收的输入数据）
    self.tpu_inputs = {}  # 存储模型实际输入数据（键：输入名称，值：numpy数组）
    gen_input_f32 = {}  # 存储用于生成参考输出的输入数据（需保持float32格式）
    # 决定是否生成参考输出：若未指定参考输出文件（ref_npz为空），则需要生成
    gen_ref = True if len(self.ref_npz) == 0 else False
    
    # 4. 处理融合预处理场景（fuse_preprocess=True时，输入为原始图片）
    if self.fuse_preprocess:
        # 断言输入为图片（融合预处理仅支持原始图片，需自动处理预处理）
        assert (self.test_input[0].endswith(('.jpg', '.jpeg', '.png')))
    
    # 5. 按输入类型处理数据（分NPZ、图片、NPY三类输入）
    # 场景1：单输入且为NPZ文件（直接加载其中的所有输入数据）
    if num_inputs == 1 and self.test_input[0].endswith(".npz"):
        x = np.load(self.test_input[0])  # 加载NPZ文件（包含多个输入张量）
        for name in x.files:  # 遍历NPZ中的所有张量
            self.tpu_inputs[name] = x[name]  # 存入tpu_inputs（模型输入）
        if gen_ref:  # 若需要生成参考输出
            gen_input_f32 = self.tpu_inputs  # 直接使用NPZ中的数据作为参考输入
    
    # 场景2：多输入或非NPZ输入（图片、NPY）
    else:
        # 断言输入数量与模型输入数量一致（避免输入不匹配）
        assert (len(self.test_input) == len(self.module.inputs))
        # 遍历输入文件和模型输入算子（一一对应处理）
        for infile, op in zip(self.test_input, self.module.inputs):
            # 子场景2.1：输入为图片（.jpg/.jpeg/.png）
            if infile.endswith(('.jpg', '.jpeg', '.png')):
                ppa = preprocess()  # 初始化预处理工具（处理图片到模型输入的转换）
                input_op = op.op  # 获取当前输入对应的模型算子（用于获取输入形状、预处理配置）
                input_shape = [Operation.shape(input_op)]  # 获取模型输入形状（如[1, 3, 224, 224]）
                ppa.load_config(input_op)  # 从算子加载预处理配置（如resize尺寸、像素格式）
                
                # 若启用预处理融合（fuse_preprocess=True）：将图片预处理逻辑融入模型
                if self.fuse_preprocess:
                    # 若未指定自定义格式，根据预处理配置生成（如像素格式RGB、通道格式NCHW）
                    if self.customization_format == '':
                        self.customization_format = getCustomFormat(
                            ppa.pixel_format, ppa.channel_format)
                    # 构建预处理配置（传递给模型，用于融合预处理逻辑）
                    config = {
                        'input_shapes': input_shape,  # 模型输入形状
                        'resize_dims': ppa.resize_dims,  # 缩放尺寸（如[224, 224]）
                        'fuse_pre': True,  # 标记启用预处理融合
                        'keep_aspect_ratio': ppa.keep_aspect_ratio,  # 是否保持宽高比（避免变形）
                        'keep_ratio_mode': ppa.keep_ratio_mode,  # 保持比例的模式（如pad填充）
                        "pixel_format": ppa.pixel_format,  # 像素格式（如RGB）
                        'customization_format': self.customization_format,  # 自定义格式
                        'aligned': self.aligned_input,  # 输入是否对齐（内存对齐，提升效率）
                        'pad_type': ppa.pad_type,  # 填充类型（如常数填充）
                        'pad_value': ppa.pad_value,  # 填充值（如0）
                        'chip': self.chip,  # 目标芯片（预处理逻辑需适配硬件）
                    }
                    logger.info("Add preprocess, set the following params:")  # 日志输出预处理配置
                    ppb = preprocess()  # 初始化预处理执行器
                    ppb.config(** config)  # 应用配置
                    # 执行预处理并保存结果：键为"输入名称_raw"（标记为原始图片处理后的数据）
                    self.tpu_inputs[op.name + "_raw"] = ppb.run(infile)
                    # 定义输入NPZ文件路径（模型推理时加载）
                    self.in_f32_npz = self.module_name + "_in_ori.npz"
                
                # 若不启用预处理融合：单独执行预处理，结果作为模型输入
                else:
                    # 执行预处理（如resize、归一化），结果存入tpu_inputs（键为预处理工具的输入名称）
                    self.tpu_inputs[ppa.input_name] = ppa.run(infile)
                
                # 若需要生成参考输出：将预处理后的结果作为参考输入
                if gen_ref:
                    gen_input_f32[ppa.input_name] = ppa.run(infile)
            
            # 子场景2.2：输入为NPY文件（单张量文件）
            elif infile.endswith(".npy"):
                data = np.load(infile)  # 加载NPY文件（单输入张量）
                self.tpu_inputs[op.name] = data  # 存入tpu_inputs（键为模型输入算子名称）
                if gen_ref:  # 若需要生成参考输出
                    gen_input_f32[op.name] = self.tpu_inputs  # 存入参考输入
            
            # 子场景2.3：不支持的输入类型（报错提示）
            else:
                raise TypeError("Unsupport input type *{}".format(os.path.splitext(infile)))
    
    # 6. 输入对齐检查（仅支持预处理融合时启用输入对齐）
    if self.aligned_input and not self.fuse_preprocess:
        # 若启用了输入对齐（aligned_input=True）但未融合预处理，抛出错误（当前不支持该组合）
        raise RuntimeError(
            "Not support now, aligned_input requires fuse_preprocess to be set to True.")
    
    # 7. 保存模型输入为NPZ文件（供后续推理使用）
    np.savez(self.in_f32_npz, **self.tpu_inputs)
    
    # 8. 生成参考输出（供模型验证时对比）
    if gen_ref:
        # 定义参考输入NPZ路径
        gen_in_f32_npz = self.module_name + '_in_f32.npz'
        file_mark(gen_in_f32_npz)  # 标记文件（可能用于缓存或版本管理）
        np.savez(gen_in_f32_npz,** gen_input_f32)  # 保存参考输入
        # 定义参考输出NPZ路径（后续验证时用模型输出与其对比）
        self.ref_npz = self.module_name + "_top_outputs.npz"
        show_fake_cmd(gen_in_f32_npz, self.mlir_file, self.ref_npz)  # 日志输出参考生成命令（伪命令，用于调试）
        # 调用mlir_inference执行推理，生成参考输出（基于原始MLIR文件，确保参考的正确性）
        top_outputs = mlir_inference(gen_input_f32, self.mlir_file)
        np.savez(self.ref_npz, **top_outputs)  # 保存参考输出
```

#### validate_model()

- 功能：该函数是模型验证流程的核心，负责对比目标模型（如 BModel）的输出与参考输出（如原始 MLIR 推理结果），验证模型正确性。
- 核心逻辑：

  - 执行目标模型推理，获取实际输出。
  - 针对特殊模型（如 MaskRCNN）修正参考输出格式。
  - 根据模型状态（如量化/非量化）调用对应对比函数，检查输出精度。

```python
def validate_model(self):

    # 1. 输出验证命令日志（伪命令，用于调试和流程追溯）
    # 显示输入文件、目标模型、输出文件、对比范围等关键信息（非实际执行命令）
    show_fake_cmd(
        self.in_f32_npz,  # 输入数据NPZ文件（模型推理的输入）
        self.bmodel_path,  # 目标模型路径（如BModel，待验证的模型）
        self.model_npz,  # 目标模型输出的NPZ文件（存储实际推理结果）
        self.compare_all  # 是否对比所有中间输出（True：全量对比；False：仅对比最终输出）
    )
    
    # 2. 执行目标模型推理，获取实际输出
    # 调用model_inference加载目标模型，用准备好的输入（self.tpu_inputs）执行推理
    model_outputs = model_inference(
        self.tpu_inputs,  # 输入数据字典（键：输入名称，值：numpy数组）
        self.bmodel_path,  # 目标模型路径（如BModel）
        self.compare_all  # 是否返回所有中间输出（用于全量对比）
    )
    
    # 3. 保存目标模型的输出结果（便于后续调试和二次对比）
    np.savez(self.model_npz, **model_outputs)  # 将推理输出存入NPZ文件
    
    # 4. 特殊模型适配：修正MaskRCNN的参考输出格式（若启用）
    # MaskRCNN等检测模型输出格式特殊（如包含框、掩码等），需统一参考输出与实际输出的格式
    if self.enable_maskrcnn:
        self.revise_MaskRCNN_tpu_ref()  # 调用修正方法（如调整张量维度、数据类型）
    
    # 5. 根据模型状态选择对比逻辑（量化模型与非量化模型的对比策略不同）
    # 场景1：模型状态为"TOP_QUANTIZED"（量化后的TOP方言模型）
    if self.state == "TOP_QUANTIZED":
        # 调用f32_blobs_compare对比目标输出与量化参考输出
        # 参数说明：
        # - self.model_npz：目标模型输出（实际推理结果）
        # - self.ref_npz：量化模型的参考输出（如原始MLIR量化推理结果）
        # - self.correctness：精度阈值（如允许的最大误差）
        # - self.excepts：例外算子列表（不参与对比的算子输出）
        # - True：启用详细日志（输出对比结果）
        # - self.fazzy_match：是否启用模糊匹配（允许微小误差，适配量化模型）
        f32_blobs_compare(
            self.model_npz,
            self.ref_npz,
            self.correctness,
            self.excepts,
            True,
            self.fazzy_match
        )
    
    # 场景2：非量化模型（如原始TPU方言模型）
    else:
        # 对比目标输出与TPU参考输出（self.tpu_npz为TPU方言MLIR的推理结果）
        # 无需模糊匹配（非量化模型精度要求更高）
        f32_blobs_compare(
            self.model_npz,
            self.tpu_npz,
            self.correctness,
            self.excepts,
            True
        )


### 核心逻辑总结
1. **推理与输出保存**：通过 `model_inference` 执行目标模型推理，将输出存入 NPZ 文件（便于追溯）。
2. **特殊模型适配**：针对 MaskRCNN 等特殊模型修正参考输出格式，避免因格式差异导致的误判。
3. **差异化对比**：
   - 量化模型（`TOP_QUANTIZED`）：启用 `fazzy_match` 模糊匹配（允许量化引入的微小误差），对比目标输出与量化参考输出。
   - 非量化模型：严格对比目标输出与 TPU 方言推理结果（无模糊匹配，要求高精度一致）。

该函数是模型部署的“质量把关”环节，通过标准化对比流程确保目标模型的输出符合预期，避免因优化、下转等步骤引入精度损失或功能错误。
```

## 9.run_calibration.py

该文件是 TPU-MLIR 工具链中模型量化校准的核心脚本，用于将浮点模型（MLIR 格式）通过量化校准转换为适配 TPU 硬件的定点模型。核心功能是：根据输入的模型、数据集和配置参数，自动完成数据选择、激活值分析、量化参数计算（阈值 / 量化表），并支持 SmoothQuant、权重均衡等量化优化技术，最终生成可用于 TPU 部署的量化表。

### 一、代码逻辑与分步功能

代码按 “参数解析 → 数据准备 → 预处理 → 核心量化流程 → 优化与适配” 的顺序执行，每一步的具体功能如下：

#### 初始化与参数解析

- 功能：定义并解析命令行参数，为后续流程提供配置依据。
- 具体操作：

  - 通过 `argparse.ArgumentParser` 定义参数（如模型路径 `mlir_file`、数据集 `--dataset`、校准方法 `--cali_method` 等）。
  - 参数涵盖 “输入输出”（如 `--dataset` 指定校准数据集、`-o` 指定量化表输出路径）、“量化策略”（如 `-``-search` 选择量化方案）、“优化选项”（如 `--sq` 启用 SmoothQuant）等。
  - 通过 `args = parser.parse_args()` 解析实际运行时传入的命令行参数（例如用户执行 `run_calibration yolov5s.mlir --dataset ../COCO2017` 时的参数）。

#### 数据选择与准备

- 功能：从数据集筛选用于校准和调优的样本，确保量化参数的可靠性。
- 具体操作：

  - 校准数据选择：通过 `DataSelector(args.dataset, args.input_num, args.data_list)` 从 `--dataset` 指定的数据集（或 `--data_list` 指定的文件）中选择 `--input_num` 个样本（如 100 张图片），存储在 `selector` 中。
  - 调优数据选择：若指定 `--tune_list`（调优样本列表文件），则通过 `DataSelector` 从该列表中选择 `--tune_num` 个样本（如 5 张），存储在 `tune_ds` 中（用于后续量化参数调优）。
  - 调试支持：若 `debug_cmd` 包含 `dump_list`，则将选中的样本路径导出到 `selected_image_list.txt` 和 `selected_tune_image_list.txt`（方便查看选了哪些样本）。

#### 输入合法性校验

- 功能：确保用户配置的合理性，避免后续流程出错。
- 具体操作：若用户指定 `--part_quantize custom_mode`（自定义算子量化模式），但未通过 `--custom_operator` 指定算子类型，则通过 `parser.error` 报错并终止，提示用户补充参数。

#### 日志配置与形状预处理

- 功能：配置日志输出级别，并处理模型中的形状相关算子（确保量化时张量形状正确）。
- 具体操作：

  - 日志级别：根据 `--debug_log` 是否启用，设置日志为 `DEBUG`（调试时输出详细信息）或 `INFO`（默认，输出关键步骤）。
  - 形状处理：通过 `ShapeOps(args).run()` 解析模型中的形状算子（如 Reshape、Transpose），确保量化过程中张量形状的一致性（避免因形状不匹配导致量化失败）。

#### 核心量化流程

根据 `--search` 参数（量化方案）的不同，执行对应的量化逻辑，这是整个脚本的核心：

##### 5.1 若 `--search search_qtable`（混合精度量化表搜索）

- 功能：为模型各层分配不同量化位宽（如 8 位 / 16 位），生成最优量化表（平衡精度与效率）。
- 具体操作：

  - 初始化 `SearchQtable`（量化表搜索器），传入参数、校准数据 `selector` 和调优数据 `tune_ds`。
  - 根据 `--mix_mode` 选择位宽模式：若为 `4_8`，执行 `run_4_8()`（4 位与 8 位混合）；默认 `8_16`（8 位与 16 位混合）。
  - 若指定 `--fast`，执行 `run_fast()`（快速搜索，牺牲部分精度换速度）；否则执行 `run()`（完整搜索，精度更高）。
  - 最终生成 `--quantize_table` 指定的量化表文件（用于后续模型量化部署）。

##### 5.2 若 `--search` 为 `search_threshold` 或 `False`（基础量化流程）

- 功能：计算各层量化阈值（将浮点值映射到定点范围的临界值），生成基础量化表。

###### 5.2.1 可选优化：SmoothQuant

- 若启用 `--sq`（SmoothQuant 优化），初始化 `SmoothQuant` 并执行 `run()`：通过调整权重和激活值的分布，减少极端值对量化的影响（尤其适用于 Transformer 等模型）。

###### 5.2.2 可选优化：权重均衡

- 若启用 `--we`（权重均衡），通过 `MixPrecSearcher` 的 `weight_equalization()` 平衡层间权重分布，避免部分层权重范围过大导致的量化误差。

###### 5.2.3 校准与阈值计算

- 若 `--search search_threshold`：通过 `SearchThreshold` 的 `run_search_calitable()` 搜索最优阈值（在保证精度的前提下，找到使量化误差最小的阈值）。
- 若 `--search False`：通过 `ActivationCalibrator` 的 `run()` 直接计算阈值（基于 `--cali_method` 指定的方法，如 KL 散度 `use_kl`、MSE 等）。

###### 5.2.4 可选优化：偏差校正

- 若启用 `--bc`（偏差校正），通过 `MixPrecSearcher` 的 `run_bias_correction()` 补偿量化导致的输出偏差：
  - 临时使用 `--bc_inference_num` 个样本重新校准。
  - 校正后重新执行校准（确保阈值更新）。

#### 模型结构适配

- 功能：针对特殊模型结构（如 Transformer 注意力模块）应用定制化量化策略。
- 具体操作：通过 `MatchPattern(args).run()` 识别模型中的特殊结构（如 `--transformer True` 时识别注意力层），并为其设置适配的量化参数（避免通用量化策略导致的精度损失）。

### 二、重点函数功能解析

这部分代码通过分支逻辑实现了多样化的量化校准策略，核心目标是：

1. 根据用户配置选择混合精度搜索、阈值搜索或基础校准。
2. 结合 SmoothQuant、权重均衡、偏置校正等优化技术，最小化量化精度损失。
3. 通过模式匹配确保量化模型适配硬件特性，最终生成可在 TPU 上高效运行的量化参数（校准表或量化表）。

```python
# 混合精度相关流程
    if args.search == 'search_qtable':
        # 设置日志器
        args._logger = logger('Search_Qtable', log_level=log_level)
        # 初始化量化表搜索器
        searcherQ = SearchQtable(args, selector, tune_ds)
        # 如果是4_8混合精度模式，执行对应的搜索
        if args.mix_mode == '4_8':
            searcherQ.run_4_8()
        else:
            # 根据是否快速搜索，选择不同的运行方法
            if args.fast:
                searcherQ.run_fast()
            else:
                searcherQ.run()
    else:
        # SmoothQuant流程
        if args.sq:
            args._logger = logger('SmoothQuant', log_level=log_level)
            smoothquant = SmoothQuant(args, selector)
            smoothquant.run()
        # 权重均衡流程
        if args.we:
            args._logger = logger('Weight_Equalization', log_level=log_level)
            searcher = MixPrecSearcher(args)
            searcher.weight_equalization()
        # 阈值搜索流程
        if args.search == 'search_threshold':
            args._logger = logger('Search_Threshold', log_level=log_level)
            searcherT = SearchThreshold(args, selector, tune_ds)
            searcherT.run_search_calitable()
        # 普通校准流程
        elif args.search == 'False':
            calibrator = ActivationCalibrator(args, selector, tune_ds)
            calibrator.run()
        # 偏置校正流程
        if args.bc:
            # 保存原始输入数量，后续恢复
            input_num = args.input_num
            args.input_num = args.bc_inference_num
            args._logger = logger('Bias_Correction', log_level=log_level)
            searcher = MixPrecSearcher(args)
            searcher.run_bias_correction()
            args.input_num = input_num
            # 偏置校正后，根据情况再次进行阈值搜索或普通校准
            if args.search == 'search_threshold':
                args._logger = logger('Search_Threshold', log_level=log_level)
                searcher = SearchThreshold(args, selector, tune_ds)
                searcher.run_search_calitable()
            elif args.search == 'False':
                calibrator = ActivationCalibrator(args, selector, tune_ds)
                calibrator.run()
        # 模式匹配流程
        match_pattern = MatchPattern(args)
        match_pattern.run()
```

## 10.data_selector.py

### 一、整体定位

- 功能：该 python 文件的功能是从指定数据集（或数据列表文件）中筛选符合要求的样本（如图片、NPY/NPZ 数据文件），并校验数据格式的合法性，为后续模型量化校准提供标准化的输入数据。
- 具体流程如下：

  1. 根据输入的 `dataset`（数据集路径）或 `data_list_file`（数据列表文件）加载原始数据列表。
  2. 对数据进行筛选（随机选择指定数量的样本，或直接读取列表中的样本）。
  3. 校验所有样本的格式是否统一（只能是图片、NPY、NPZ 中的一种），确保后续处理兼容性。
  4. 支持将筛选结果导出为文件（用于调试或记录）。

### 二、函数解释

1. 初始化方法 `init`（核心入口）

- 功能：根据输入参数加载并筛选数据，初始化数据列表。
- 生成逻辑：

```python
def __init__(self, dataset: str, num: int = 0, data_list_file: str = None):
    self.data_list = []  # 存储最终筛选后的样本路径
    # 情况1：若指定data_list_file（数据列表文件），直接从文件读取
    if data_list_file:
        with open(data_list_file, 'r') as f:
            # 读取文件中每一行（去除首尾空格），作为样本路径
            self.data_list = [line.strip() for line in f.readlines()]
        # 若指定了num（样本数量），且num小于列表长度，则截取前num个
        if num > 0 and num < len(self.data_list):
            self.data_list = self.data_list[:num]
    # 情况2：若指定dataset（数据集路径），则随机筛选
    elif dataset:
        self.data_list = self._random_select(dataset, num)
    # 异常处理：未指定数据来源
    else:
        raise RuntimeError("Please specific dataset path by --dataset")
    # 异常处理：筛选后无有效样本
    if len(self.data_list) == 0:
        raise RuntimeError("There is no inputs")
    # 校验数据格式合法性
    self._check_data_list()
```

1. 数据筛选方法 `_random_select`

- 功能：从 `dataset` 目录中随机选择指定数量的样本（支持子目录递归查找）。
- 生成逻辑：

```python
def _random_select(self, dataset_path, num):
    full_list = []
    # 递归遍历dataset_path下所有文件（包括子目录）
    for file in pathlib.Path(dataset_path).glob('**/*'):
        name = str(file)
        # 只保留图片、NPY、NPZ格式的文件
        if self.is_npz(name) or self.is_npy(name) or self.is_image(name):
            full_list.append(name)
    # 排序后随机打乱（固定种子1684，确保结果可复现）
    full_list = sorted(full_list)
    random.seed(1684)  # 固定种子，多次运行选择相同样本
    random.shuffle(full_list)
    # 确定最终选择数量（不超过总样本数，若num=0则全选）
    num = num if len(full_list) > num else len(full_list)
    if num == 0:
        num = len(full_list)
    return full_list[:num]  # 返回前num个样本
```

1. 格式校验方法 `_check_data_list`

- 功能：确保所有样本格式统一（只能是图片、NPY、NPZ 中的一种），避免后续处理出错。
- 生成逻辑：

```python
def _check_data_list(self):
    # 标记是否存在NPZ、NPY、图片格式
    self.all_npz, self.all_npy, self.all_image = False, False, False
    for file in self.data_list:
        if self.is_npz(file):  # 若文件是NPZ
            self.all_npz = True
        else:
            # 处理多输入情况（如"a.npy,b.npy"，按逗号分割）
            inputs = [s.strip() for s in file.split(',')]
            for i in inputs:
                if self.is_npy(i):  # 若文件是NPY
                    self.all_npy = True
                elif self.is_image(i):  # 若文件是图片
                    self.all_image = True
                else:  # 不支持的格式
                    raise RuntimeError("File illegal:{}".format(file))
        # 校验格式唯一性：只能存在一种格式（NPZ/NPY/图片）
        # （布尔值相加：True=1，False=0，若num_type!=1则存在多种格式）
        num_type = self.all_npz + self.all_image + self.all_npy
        if num_type != 1:
            raise RuntimeError("Only support one input type: npy/npz/image")
```

1. 格式判断工具方法

- 功能：判断文件是否为 NPZ、NPY 或图片格式（基于文件后缀）。
- 实现逻辑：

```python
def is_npz(self, filename: str):
    # NPZ文件：后缀为.npz（忽略大小写，如.NPZ也支持）
    return filename.lower().split('.')[-1] == 'npz'

def is_npy(self, filename: str):
    # NPY文件：后缀为.npy（忽略大小写）
    return filename.lower().split('.')[-1] == 'npy'

def is_image(self, filename: str):
    # 图片文件：后缀为jpg/bmp/png/jpeg/jfif（忽略大小写）
    return filename.lower().split('.')[-1] in ['jpg', 'bmp', 'png', 'jpeg', 'jfif']
```

1. 结果导出方法 `dump`

- 功能：将筛选后的样本路径写入文件（用于调试或记录选择的样本）。
- 实现逻辑：

```python
def dump(self, file):
    with open(file, 'w') as f:
        # 每行写入一个样本路径
        for input in self.data_list:
            f.write(input + '\n')
```

### 三、辅助知识

#### data_list_file（数据列表文件）和 dataset（数据集路径）的区别：

##### 数据列表文件（`data_list_file`）

- 定义：是一个文本文件（如 `.txt`），文件内容是手动指定的输入文件路径列表（每行一个路径）。
- 内容格式：每行对应一个输入（支持单文件或多文件组合），例如：

```
# 单行可以是单个npz/npy/图片文件
a.npz
b.jpg
# 单行也可以是多个npy/图片文件（用逗号分隔，表示多输入）
c.npy,d.npy
e.jpg,f.png
```

- 作用：用于精确指定需要使用的输入文件，适合已知具体哪些文件需要处理的场景（例如筛选过的测试集）。
- 处理逻辑：

  - 直接读取文本文件中的每行内容，作为 `data_list` 的元素。
  - 如果指定了 `num` 参数（如 `num=5`），则只取前 `num` 个文件（避免读取过多）。

##### 数据集文件（`dataset`）

- 定义：是一个文件夹路径，该文件夹下包含若干输入文件（支持子文件夹递归查找）。
- 内容格式：文件夹中存放的是实际的输入文件（如 `.npz`、`.npy`、`.jpg` 等），无需手动整理列表。
- 作用：用于从大量文件中自动筛选并随机选择输入，适合需要从原始数据集中抽样的场景（例如从 1000 张图片中随机选 100 张测试）。
- 处理逻辑：

  - 递归遍历文件夹下所有文件，筛选出后缀为 `.npz`、`.npy` 或图片格式（`.jpg`、`.png` 等）的文件。
  - 对筛选后的文件列表随机打乱（固定种子 `1684`，保证结果可复现），然后按 `num` 参数选取前 `num` 个文件（若 `num=0` 则取全部）。

## 11.search_qtable.py

### 一、整体定位

SearchQtable 是 TPU-MLIR 量化校准流程中自动搜索最优量化表（quantize table）的核心类。它的主要目标是：

- 针对每一层（op），自动选择最优的量化阈值和量化方法（如 KL、MSE、MAX、Percentile9999），
- 通过混合精度（MixPrecision）策略，自动将部分对精度敏感的层保留为浮点（FP32/FP16），其余层量化为 INT8/INT4，
- 支持聚类、敏感性分析、快速搜索等多种自动化策略，
- 生成最终的量化表（quantize table），以便后续模型部署。

### 二、主要函数

#### gen_multiple_thresholds

功能：针对每种量化方法（如 KL、MSE、MAX、Percentile9999），为所有 op 生成一份阈值表，并可选用调优数据集微调阈值。逻辑：

- 遍历所有量化方法，分别用 ActivationCalibrator 计算阈值。
- 支持调优（tune_ds），用 SimpleTuner 微调阈值。
- 结果写入不同的 calibration_table 文件。
- 返回每种方法下每一层的阈值字典。

原理：

1. 前置判断：`if out not in thresholds_map: continue`

作用：过滤未参与校准的算子输出。其中，`thresholds_map` 是校准器（`ActivationCalibrator`）预先计算的 “算子输出 → 阈值” 映射表。如果某个算子输出（`out`）不在该表中，说明它无需量化（或未被校准），直接跳过。

1. 特殊情况：使用 PyTorch 观测器校准（`use_torch_observer_for_cali`）

当启用 PyTorch 的量化观测器时，阈值计算依赖 PyTorch 的量化参数（`scale` 和 `zp`），而非直接统计激活值。

1. 普通情况：使用校准器统计校准

当不使用 PyTorch 观测器时，阈值直接基于输入数据的激活值分布统计得到。

```python
def gen_multiple_thresholds(self, all_op_names, quantize_method_list):
    """
    生成多种校准方法（如MAX、MSE）对应的量化阈值，并保存到文件中
    :param all_op_names: 模型中所有算子的名称列表
    :param quantize_method_list: 校准方法列表（如['MAX', 'MSE']）
    :return: 层阈值字典（键：校准方法，值：{算子名: [绝对最大值, 调优后阈值]}）
    """
    # 初始化层阈值字典，用于存储不同校准方法对应的阈值结果
    layer_th_dicts = {}
    # 遍历每种校准方法（如MAX、MSE、KL等）
    for i, method_name in enumerate(quantize_method_list):
        # 临时存储当前校准方法下的阈值（键：算子名，值：[绝对最大值, 阈值]）
        tmp_th_dict = {}
        # 初始化激活值校准器（核心工具：收集激活值并计算初始阈值）
        calibrator = ActivationCalibrator(self.args, self.selector, self.tune_ds)
        # 根据校准方法设置调试命令（指定阈值计算方式）
        if method_name == "MAX":
            calibrator.debug_cmd = 'use_max'  # 使用最大值法计算阈值
        elif method_name == "Percentile9999":
            calibrator.debug_cmd = 'use_percentile9999'  # 使用99.99分位法
        elif method_name == "MSE":
            calibrator.debug_cmd = 'use_mse'  # 使用均方误差法
        
        # 收集激活值并计算初始阈值（核心步骤）
        # 返回值：阈值映射、绝对最大值映射、缩放因子映射、零点映射等
        thresholds_map, thresholds_map_absmax, thresholds_map_scale, thresholds_map_zp, _, _, _, _ = calibrator.activation_collect_and_calc_th_new()
        # 清理校准器占用的资源（如缓存的激活值数据）
        calibrator._clean_resource()

        # 存储当前方法下的阈值列表（用于后续校验）
        thresholds_map_list = []
        # 获取模型中所有算子的名称列表（从MLIR解析）
        op_layers = self.parser.get_op_name_list()
        # 定义校准表文件路径（含当前校准方法，如"calib_table_MAX"）
        cali_table = self.args.calibration_table + "_" + method_name
        # 若指定了调优样本数，在文件名后加".1"（临时标记）
        if self.args.tune_num > 0:
            cali_table += ".1"
        
        # 将初始阈值写入校准表文件（用于记录原始结果）
        with open(cali_table, 'w') as f:
            # 写入文件头（版本、时间、参数等元信息）
            f.write("# mlir version: {}\n".format(pymlir.__version__))
            f.write("# mlir: {}\n".format(self.args.mlir_file))
            f.write("# genertated time: {}\n".format(datetime.datetime.now()))
            f.write("# histogram number: {}\n".format(calibrator.histogram_bin_num))  # 直方图分箱数
            f.write("# sample number: {}\n###\n".format(calibrator.num_samples))  # 校准样本数
            f.write("# op_name    threshold    min    max\n")  # 表头：算子名、阈值、最小值、最大值
            
            # 遍历每个算子，写入对应的阈值信息
            for i, op_name in enumerate(op_layers):
                # 获取当前算子的输出张量名称（一个算子可能有多个输出）
                outputs = self.parser.get_outputs_by_op_name(op_name)
                # 遍历每个输出张量
                for out in outputs:
                    # 若当前输出张量无阈值（未被校准），跳过
                    if out not in thresholds_map:
                        continue
                    else:
                        # 特殊情况：使用PyTorch的观测器计算校准时的阈值
                        if 'use_torch_observer_for_cali' in calibrator.debug_cmd:
                            # 量化范围（INT8：-128~127）
                            qmin, qmax = -128, 127
                            # 获取缩放因子和零点（量化参数）
                            scale = thresholds_map_scale[op_name]
                            zp = thresholds_map_zp[op_name]
                            # 根据量化参数计算阈值（最大可表示值）
                            threshold = float(scale * max(-(qmin-zp), qmax-zp))
                            # 计算量化后的最小/最大值
                            min_value = float(scale * (qmin - zp))
                            max_value = float(scale * (qmax - zp))
                        # 普通情况：使用校准器计算的阈值
                        else:
                            # 获取当前输出的阈值（若不存在则默认1.0）
                            if out in thresholds_map:
                                threshold = thresholds_map[out]
                            else:
                                threshold = 1.0
                            # 获取激活值的最小/最大值（从校准器统计结果中读取）
                            if out in calibrator.activations_statistics:
                                min_value, max_value, _ = calibrator.activations_statistics[out]
                            else:
                                min_value, max_value = -1, 1  # 默认值
                        # 记录阈值到列表（用于后续校验）
                        thresholds_map_list.append(threshold)
                        # 将阈值、最小/最大值写入校准表文件（保留7位小数）
                        f.write("{} {:.7f} {:.7f} {:.7f}\n".format(out, threshold, min_value, max_value))
        
        # 若未指定调优样本数（无需调优），直接返回（不再执行后续调优步骤）
        if calibrator.args.tune_num <= 0:
            return

        # 若指定了调优样本数，处理校准表文件名（移除临时标记".1"）
        if self.args.tune_num > 0:
            cali_table = cali_table.rsplit(".1", 1)[0]
        # 将校准表路径设置到校准器参数中（用于调优）
        calibrator.args.calibration_table = cali_table
        # 初始化简单调优器（基于调优样本微调阈值，提升精度）
        tunner = SimpleTuner(calibrator.args, calibrator.tune_ds, calibrator.ppa_list, thresholds_map_absmax)
        # 执行调优并获取调优后的阈值
        thresholds_map = tunner.run()

        # 存储调优后的阈值和层名（用于后续记录）
        tuned_threshold_list = []
        layer_name_list = []
        # 定义调优后的校准表文件名（加"_tune"标记）
        cali_table += "_tune"
        # 过滤掉融合算子（只保留需要量化的原始算子）
        op_layers = calibrator.get_no_fused_tensors(op_layers)
        # 将调优后的阈值写入新的校准表文件
        with open(cali_table, 'w') as f:
            # 写入文件头（元信息）
            f.write("# mlir version: {}\n".format(pymlir.__version__))
            f.write("# mlir: {}\n".format(self.args.mlir_file))
            f.write("# genertated time: {}\n".format(datetime.datetime.now()))
            f.write("# histogram number: {}\n".format(calibrator.histogram_bin_num))
            f.write("# sample number: {}\n".format(calibrator.num_samples))
            f.write("# tune number: {}\n###\n".format(self.args.tune_num))  # 调优样本数
            f.write("# op_name   threshold    min    max\n")  # 表头
            
            # 遍历每个算子，写入调优后的阈值
            for i, op_name in enumerate(op_layers):
                # 获取调优后的阈值
                threshold = thresholds_map[op_name]
                # 处理异常值（阈值过小或NaN时，强制设为1e-5避免后续错误）
                if threshold <= 1e-5 or np.isnan(threshold):
                    threshold = 1e-5
                    self.mix_prec.logger.print_info("WARNING: layer {} threshold is zero. Please check the "
                        "input data correctness.".format(op_name))  # 日志警告
                # 记录层名和阈值（用于后续统计）
                layer_name_list.append('{}_{}'.format(i, op_name))
                tuned_threshold_list.append(threshold)
                # 获取激活值的最小/最大值（从校准器统计结果中读取）
                min_value, max_value, _ = calibrator.activations_statistics[op_name]
                # 写入调优后的阈值、最小/最大值（保留7位小数）
                f.write("{} {:.7f} {:.7f} {:.7f}\n".format(op_name, threshold, min_value, max_value))
        
        # 将当前校准方法的阈值结果存入临时字典
        for op_name in all_op_names:
            # 跳过无阈值的算子
            if op_name not in thresholds_map:
                pass
            else:
                # 再次处理异常值（确保阈值有效）
                if thresholds_map[op_name] <= 1e-5 or np.isnan(thresholds_map[op_name]):
                    thresholds_map[op_name] = 1e-5
                # 存储：[绝对最大值（参考）, 调优后阈值（实际使用）]
                tmp_th_dict[op_name] = [thresholds_map_absmax[op_name], thresholds_map[op_name]]
        # 将当前校准方法的结果存入总字典
        layer_th_dicts[method_name] = tmp_th_dict
    # 返回所有校准方法的阈值结果
    return layer_th_dicts
```

#### 2. search_layer_type_no_need_quant

功能：自动分析哪些 op 类型对精度影响较大（敏感），建议保留为浮点。逻辑：

- 遍历所有 op 类型，分别将该类型全部保留为浮点，其余量化，评估整体精度（cosine similarity）。
- 如果精度下降明显，则该类型为敏感类型。
- 返回所有敏感类型。

原理：

- 通过“整体置浮点-量化对比”法，自动发现对精度影响最大的算子类型，便于后续混合精度策略。

```python
def search_layer_type_no_need_quant(self, layer_names, float_outputs_cos, global_compare_layers, layers_rate, predictions_gt):
    """
    搜索对量化不敏感的算子类型（即量化后精度损失小的类型），仅关注敏感类型以减少后续计算量
    :param layer_names: 需要分析的层名称列表
    :param float_outputs_cos: 浮点模型的输出余弦相似度（精度基准）
    :param global_compare_layers: 用于精度对比的关键层列表
    :param layers_rate: 各对比层的权重（影响精度评估结果）
    :param predictions_gt: 浮点模型的输出结果（作为精度对比的基准）
    :return: 对量化敏感的算子类型列表（需要重点关注的类型）
    """
    # 收集所有层对应的算子类型（去重，避免重复分析）
    op_types = set()
    for layer_name in layer_names:
        # 通过层名获取对应的算子类型（如"top.Conv"、"top.MatMul"）
        op_type = self.parser.get_op_type_by_op_name(layer_name)
        op_types.add(op_type)  # 用集合存储，自动去重

    # 存储对量化敏感的算子类型（量化后精度损失大的类型）
    sensitive_op_type = []
    # 构建"层名→算子类型"的映射（方便后续快速查询）
    layer_op_map = {layer_name: self.parser.get_op_type_by_op_name(layer_name) for layer_name in layer_names}
    # 设定余弦相似度阈值：取0.999和预期精度的最大值（确保精度要求不低于基础阈值）
    cos_threshold = max(0.999, self.args.expected_cos)

    # 遍历每种算子类型，评估该类型量化后的精度损失
    for op_type in op_types:
        # 构建"浮点层列表"：除当前算子类型外，其他层均保持浮点（仅量化当前类型的层）
        fp_list = []
        for layer_name in layer_names:
            # 若层的算子类型不是当前类型，则加入浮点列表（不量化）
            if layer_op_map[layer_name] == op_type:
                pass  # 当前类型的层会被量化，不加入浮点列表
            else:
                fp_list.append(layer_name)  # 非当前类型的层保持浮点

        # 生成混合精度量化表（指定哪些层保持浮点）
        mix_table = self.mix_prec._gen_mix_table(fp_list)
        # 创建混合精度模型（仅当前算子类型的层被量化，其他层浮点）
        mix_model = MixQuantModel(self.fp32_mlir, self.chip, self.cali_table_name, mix_table)

        # 评估混合模型的精度：计算量化后与浮点模型的相似度（值越小，精度损失越大）
        # 1 - 余弦相似度 = 精度损失（值越小越好）
        similarity = 1 - self.mix_prec.run_model_new(
            mix_model,  # 混合精度模型
            False,  # 非浮点模型（需要与浮点基准对比）
            global_compare_layers,  # 对比层
            layers_rate,  # 对比层权重
            predictions_gt,  # 浮点模型输出（基准）
            -1,  # 使用所有样本评估
            ['cos']  # 用余弦相似度评估精度
        )
        # 打印当前算子类型的精度损失结果（方便调试和分析）
        self.mix_prec.logger.print_info(f"op_type : {op_type}, similarity : {similarity}")

        # 判断当前算子类型是否对量化敏感：
        # 若精度损失（similarity）小于 "浮点基准 * 阈值"，说明量化后精度足够，非敏感；
        # 反之则敏感（量化后精度损失大，需要重点关注）
        if similarity < float_outputs_cos * cos_threshold:
            sensitive_op_type.append(op_type)

    # 打印敏感算子类型（后续量化需优先保证这些类型的层精度）
    self.mix_prec.logger.print_info(f"sensitive_op_type : {sensitive_op_type}, please pay attention to these types of operations")
    return sensitive_op_type
```

#### 3.search_sensitve_layer

功能：对每一层，自动尝试不同量化方法，选择最优阈值和方法，记录敏感层。

逻辑：

- 依次将每一层从量化变为浮点，评估精度损失。
- 对每一层，遍历所有量化方法，选择最优阈值和方法。
- 记录每一层的最小损失、最优方法、最优阈值。
- 返回敏感层分析结果、最终 calibration_table 文件名、损失字典。

```python
def search_sensitve_layer(self, layer_names, quantize_method_list, float_model, int8_model, layer_th_dicts, global_compare_layers, layers_rate, predictions_gt):
    # 检查输入的层名称列表是否为空，若为空则日志提示并退出程序（无层可分析）
    if not layer_names:
        self.mix_prec.logger.print_info("Layer names are empty. All operators skipped in search phase.")
        sys.exit(1)
    
    # 获取量化方法的总数（用于控制每种方法的尝试次数）
    num_quantize_method = len(quantize_method_list)
    
    # 初始化损失字典：key为层名，value为该层的[余弦相似度, 信噪比]列表（记录量化损失）
    loss_dict = collections.defaultdict(list)
    
    # 初始化浮点层列表：初始包含所有层（后续会临时移除当前处理的层，模拟对其进行量化）
    fp_layer_list = []
    for op_name in layer_names:
        fp_layer_list.append(op_name)
    
    # 记录每层的量化方法尝试情况：key为层名，value为[尝试次数, 最佳余弦损失, 最佳阈值, 最佳方法]
    modified_layers = {}
    
    # 记录最近尝试的量化方法（初始化为第一种方法）
    last_tried_method = quantize_method_list[0]
    
    # 敏感层分析结果字典：key为层名，value为[最佳余弦损失, 层类型]（用于评估敏感程度）
    sensitive_layer_analysis_dict = {}
    
    # 遍历每个层，逐个分析其量化敏感性
    for layer_name in layer_names:
        # 获取当前层的操作类型（如卷积、全连接等）
        layer_type = self.parser.get_op_type_by_op_name(layer_name)
        # 日志输出：开始处理当前层及其类型
        self.mix_prec.logger.print_info("start to handle layer: {}, type: {}".format(layer_name, layer_type))
        
        # 从浮点层列表中移除当前层（意味着对该层进行量化测试，其他层保持浮点精度）
        fp_layer_list.remove(layer_name)
        
        # 生成混合精度量化表：基于当前浮点层列表（指定哪些层用浮点，哪些用量化）
        mix_table = self.mix_prec._gen_mix_table(fp_layer_list)
        
        # 标记当前层是否处理完成（ret为True时退出循环）
        ret = False
        
        # 循环尝试不同的量化方法，直到当前层处理完成
        while not ret:
            # 情况1：当前层未尝试过任何量化方法（首次处理）
            if layer_name not in modified_layers:
                # 初始尝试第一种量化方法
                last_tried_method = quantize_method_list[0]
                # 在modified_layers中记录该层的初始状态：
                # [尝试次数=1, 最佳余弦损失=无穷大, 初始阈值, 初始方法]
                modified_layers[layer_name] = [1, float('inf'), layer_th_dicts[last_tried_method][layer_name][1], last_tried_method]
                method = last_tried_method
                # 获取该方法对应的量化阈值（layer_th_dicts结构：{量化方法: {层名: {fmax, 阈值}}}）
                new_th = layer_th_dicts[last_tried_method][layer_name][1]
                # 更新当前层的阈值到INT8模型的校准表中，返回新的校准表名称
                new_cali_table_name = self.set_layer_new_th(int8_model, layer_name, new_th)
                # 记录最近尝试的方法
                last_tried_method = method
                # 日志输出：当前层使用的量化方法和阈值
                self.mix_prec.logger.print_info("adjust layer {} th, with method {}, and threshlod {}".format(layer_name, method, new_th))
                
                # 创建混合精度量化模型（基于FP32模型、芯片类型、新校准表、混合精度表）
                mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table_name, mix_table)
                
                # 非集群模式：运行模型计算余弦损失（1 - 余弦相似度，值越小表示差异越大）
                if not self.args.cluster:
                    outputs_cos = 1 - self.mix_prec.run_model(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)
                # 集群模式：运行模型同时计算余弦相似度和信噪比，并存入损失字典
                else:
                    outputs_cos, outputs_snr = self.mix_prec.run_model_new(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)
                    loss_dict[layer_name].extend([outputs_cos, outputs_snr])
                
                # 日志输出当前的余弦损失
                self.mix_prec.logger.print_info("outputs_cos_los = {}".format(outputs_cos))
            
            # 情况2：当前层已尝试过部分量化方法（尝试次数小于总方法数）
            elif modified_layers[layer_name][0] < num_quantize_method:
                # 获取下一个要尝试的方法索引（基于已尝试次数）
                method_idx = modified_layers[layer_name][0]
                method = quantize_method_list[method_idx]
                
                # 若当前余弦损失优于记录的最佳值（更小，即差异更大），更新最佳记录
                if outputs_cos < modified_layers[layer_name][1]:
                    modified_layers[layer_name][1] = outputs_cos
                    modified_layers[layer_name][2] = layer_th_dicts[last_tried_method][layer_name][1]
                    modified_layers[layer_name][3] = last_tried_method
                
                # 获取新方法对应的量化阈值
                new_th = layer_th_dicts[method][layer_name][1]
                # 更新阈值到校准表
                new_cali_table_name = self.set_layer_new_th(int8_model, layer_name, new_th)
                # 记录最近尝试的方法
                last_tried_method = method
                # 日志输出当前方法和阈值
                self.mix_prec.logger.print_info("adjust layer {} th, with method {}, and threshlod {}".format(layer_name, method, new_th))
                # 增加该层的尝试次数
                modified_layers[layer_name][0] += 1
                
                # 创建新的混合精度模型
                mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table_name, mix_table)
                
                # 非集群模式：计算余弦损失
                if not self.args.cluster:
                    outputs_cos = 1 - self.mix_prec.run_model(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)
                # 集群模式：计算余弦损失和信噪比，并更新损失字典（保留更差的指标）
                else:
                    outputs_cos, outputs_snr = self.mix_prec.run_model_new(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)
                    self.compare_loss(layer_name, loss_dict, outputs_cos, outputs_snr)
                
                # 日志输出当前余弦损失
                self.mix_prec.logger.print_info("outputs_cos_los = {}".format(outputs_cos))
            
            # 情况3：当前层已尝试完所有量化方法（尝试次数等于总方法数）
            elif modified_layers[layer_name][0] == num_quantize_method:
                # 最后一次检查是否更新最佳记录
                if outputs_cos < modified_layers[layer_name][1]:
                    modified_layers[layer_name][1] = outputs_cos
                    modified_layers[layer_name][2] = layer_th_dicts[last_tried_method][layer_name][1]
                    modified_layers[layer_name][3] = last_tried_method
                
                # 获取该层的最佳阈值
                best_th = modified_layers[layer_name][2]
                # 尝试次数加1（标记为已完成所有方法）
                modified_layers[layer_name][0] += 1
                # 用最佳阈值更新校准表
                new_cali_table_name = self.set_layer_new_th(int8_model, layer_name, best_th)
                # 日志输出该层的最佳量化策略（最佳阈值、方法、余弦损失）
                self.mix_prec.logger.print_info("layer {}, layer type is {}, best_th = {}, best_method = {}, best_cos_loss = {}"
                                       .format(layer_name, layer_type, best_th, modified_layers[layer_name][3], modified_layers[layer_name][1]))
                
                # 将该层的最佳余弦损失和类型存入敏感层分析字典
                sensitive_layer_analysis_dict[layer_name] = [modified_layers[layer_name][1], layer_type]
                # 标记当前层处理完成
                ret = True
        
        # 将当前层重新加入浮点层列表（准备处理下一个层）
        fp_layer_list.append(layer_name)
    
    # 返回敏感层分析结果、最终校准表名称、损失字典
    return sensitive_layer_analysis_dict, new_cali_table_name, loss_dict
```

##### 集群和非集群的区别

- 非集群模式：
  通常是在单台设备（如单个服务器或本地机器） 上运行模型。代码中通过 `self.mix_prec.run_model()` 执行模型，流程更简单，专注于基本的量化效果评估。仅计算并返回 余弦损失（outputs_cos），即 `1 - 余弦相似度`（用于衡量量化后输出与原始浮点输出的差异）。关注点单一，仅评估输出相似度。适合小规模模型、本地调试或快速迭代验证量化策略。
- 集群模式：
  可能是在多台设备组成的集群上分布式运行模型。通过 `self.mix_prec.run_model_new()` 执行，能够利用集群的计算资源（如多 GPU / 多节点），适合处理更大规模的模型或更复杂的评估任务。同时计算并返回 余弦相似度（outputs_cos） 和 信噪比（outputs_snr）。适合大规模模型（如深度学习中的大型 CNN/RNN）、需要高精度评估（多指标）或高计算量的场景，通过分布式计算提高效率。

##### search_sensitve_layer/search_sensitve_layer_int4/search_sensitve_layer_fast 的区别

#### 4.analysis_sensitive_layers

功能：`analysis_sensitive_layers` 函数的核心功能是分析量化敏感层（即量化后损失较大的层），并根据配置筛选出需要保持高精度（如 FP32）的层，同时自动关联处理其依赖的相关层（如卷积层后的缩放层），最终生成高精度层列表。

原理：

1. 敏感层排序：首先将敏感层按量化损失（如 cos/snr 损失）从大到小排序，确保损失最大的层优先被处理（这类层最需要高精度保留）。
2. 高精度层筛选：根据配置的最大高精度层数量（`self.args.max_float_layers`），从排序后的敏感层中选取前 N 个作为初始高精度层。
3. 依赖层关联：对于筛选出的高精度层，自动关联其紧密依赖的层（如卷积层 `top.Conv` 后的缩放层 `top.Scale`，或缩放层前的卷积层），将这些关联层也设为高精度 —— 原因是这类层通常存在强依赖关系（如卷积输出直接作为缩放层输入），单独量化其中一个会导致误差累积，需同时用高精度保证整体精度。

```python
def analysis_sensitive_layers(self, sensitive_layer_analysis_dict, pr):
    # 初始化变量：
    num = 0  # 敏感层计数（用于打印排序后的序号）
    num_fp32 = 0  # 已选择的高精度层数量
    set_fp_layer_list = []  # 最终的高精度层列表

    # 步骤1：将敏感层按量化损失从大到小排序（损失越大，越需要高精度保留）
    # sensitive_layer_analysis_dict格式：{层名: [损失值, 层类型]}
    sensitive_layer_analysis_dict = sorted(
        sensitive_layer_analysis_dict.items(), 
        key=lambda x: x[1][0],  # 按损失值（x[1][0]）排序
        reverse=True  # 降序：损失最大的层排在最前
    )

    # 步骤2：遍历排序后的敏感层，筛选并关联高精度层
    for sensitive_layer_tuple in sensitive_layer_analysis_dict:
        # 敏感层元组格式：(层名, [损失值, 层类型])
        layer_name = sensitive_layer_tuple[0]
        loss = sensitive_layer_tuple[1][0]
        layer_type = sensitive_layer_tuple[1][1]

        # 若需要打印（pr=True），输出当前层的敏感信息
        if pr == True:
            self.mix_prec.logger.print_info(
                f"the layer {layer_name} is {num} sensitive layer, loss is {loss}, type is {layer_type}"
            )

        # 步骤3：根据最大高精度层数量限制，筛选高精度层
        # 仅当未超过最大高精度层数量时，继续添加
        if self.args.max_float_layers > 0 and self.args.max_float_layers > num_fp32:
            # 避免重复添加同一层
            if layer_name in set_fp_layer_list:
                continue

            # 将当前敏感层加入高精度列表，更新计数
            set_fp_layer_list.append(layer_name)
            num_fp32 += 1

            # 步骤4：处理关联层（卷积层与缩放层强关联，需同时设为高精度）
            # 获取当前层的操作对象
            op = self.parser.get_op_by_op_name(layer_name)

            # 情况1：若当前层是卷积层（top.Conv），则将其后的缩放层（top.Scale）也设为高精度
            if op.type == "top.Conv":
                # 获取当前卷积层的直接后继层
                next_op_name_list = self.parser.get_next_op_by_op_name(layer_name)
                for next_op_name in next_op_name_list:
                    next_op = self.parser.get_op_by_op_name(next_op_name)
                    # 若后继层是缩放层，加入高精度列表
                    if next_op.type == "top.Scale":
                        self.mix_prec.logger.print_info(f"next scale op: {next_op_name}")
                        set_fp_layer_list.append(next_op_name)
                        num_fp32 += 1  # 更新高精度层计数

            # 情况2：若当前层是缩放层（top.Scale），则将其前面的卷积层（top.Conv）也设为高精度
            elif op.type == "top.Scale":
                # 获取当前缩放层的直接前置层
                pre_op_name_list = self.parser.get_pre_op_by_op_name(layer_name)
                for pre_op_name in pre_op_name_list:
                    pre_op = self.parser.get_op_by_op_name(pre_op_name)
                    # 若前置层是卷积层，加入高精度列表
                    if pre_op.type == "top.Conv":
                        self.mix_prec.logger.print_info(f"pre conv op: {pre_op_name}")
                        set_fp_layer_list.append(pre_op_name)
                        num_fp32 += 1  # 更新高精度层计数

        # 更新敏感层序号
        num += 1

    # 打印最终的高精度层列表，返回结果
    self.mix_prec.logger.print_info(f"set_fp_layer_list = {set_fp_layer_list}")
    return set_fp_layer_list
```

#### 5.cluster /auto_select_clusters

1. `cluster` 方法：使用指定数量的聚类（K 值）对网络层的损失数据进行聚类，并选择特定聚类中的层。
2. `auto_select_clusters` 方法：自动选择最佳的聚类数量（K 值），通过评估不同 K 值下的聚类质量，确定最优聚类数。
3. 两段代码均基于 K-Means 聚类算法和轮廓系数（Silhouette Score） 实现：

- K-Means 聚类：一种无监督学习算法，将数据分成 K 个簇，使得簇内数据相似度高、簇间数据相似度低。
- 轮廓系数：用于评估聚类质量的指标，取值范围为 [-1, 1]。值越接近 1，说明聚类效果越好（簇内紧凑、簇间分离）；值接近 - 1 则表示聚类效果差。

```python
def cluster(self, loss_dict, num_cluster):
    # 1. 提取损失字典中的层名称和对应的损失数据
    layer_names = list(loss_dict.keys())  # 获取所有层的名称（如网络层"conv1"、"fc2"等）
    X = np.array([losses for losses in loss_dict.values()])  # 将损失数据转换为numpy数组，形状为(层数量, 损失维度)
    # 例如：若有3层，每层损失是2维数据，则X形状为(3, 2)

    # 2. 使用K-Means进行聚类
    kmeans = KMeans(n_clusters=num_cluster, random_state=42)  # 初始化K-Means模型，指定聚类数和随机种子（保证结果可复现）
    labels = kmeans.fit_predict(X)  # 拟合数据并得到每个样本（层）的聚类标签（0, 1, ..., num_cluster-1）

    # 3. 构建"层名称-聚类标签"的映射关系
    result = {name: label for name, label in zip(layer_names, labels)}  # 键为层名称，值为该层所属的聚类标签

    # 4. 确定目标聚类并筛选层
    centroids = kmeans.cluster_centers_  # 获取每个聚类的中心（形状为(num_cluster, 损失维度)）
    target_cluster = np.argmax(centroids[:, 0])  # 选择"第一个损失维度"最大的聚类作为目标聚类
    # 注：centroids[:, 0]表示所有聚类中心的第一个损失维度值，np.argmax取最大值对应的索引（即目标聚类的标签）
    
    selected_layers = [name for name, label in result.items() if label == target_cluster]  # 筛选出属于目标聚类的层

    # 5. 打印并返回筛选结果
    self.mix_prec.logger.print_info("selected_layers = {}".format(selected_layers))  # 日志输出选中的层
    return selected_layers  # 返回选中的层名称列表
```

```python
def auto_select_clusters(self, X, max_clusters=10):
    best_score = -1  # 初始化最佳轮廓系数（最小值为-1）
    best_n_clusters = 3  # 初始最佳聚类数设为3（注释说明"聚类数至少从3开始"）

    # 遍历可能的聚类数（从2到max_clusters）
    for n in range(2, max_clusters + 1):
        # 对当前n值进行K-Means聚类
        kmeans = KMeans(n_clusters=n, random_state=42)  # 初始化模型
        labels = kmeans.fit_predict(X)  # 得到聚类标签

        # 计算当前聚类结果的轮廓系数
        score = silhouette_score(X, labels)  # 传入特征数据和标签，计算轮廓系数
        self.mix_prec.logger.print_info(f"n_clusters={n}, silhouette_score={score:.4f}")  # 日志输出当前K值和分数

        # 更新最佳聚类数（保留轮廓系数最高的K值）
        if score > best_score:
            best_score = score  # 更新最佳分数
            best_n_clusters = n  # 更新最佳聚类数

    return best_n_clusters  # 返回最优聚类数
```

#### 6.run/run_4_8/run_fast

```python
def run(self):
    t0 = time.time()  # 记录开始时间，用于计算总耗时

    # step1: 生成多种量化方法的阈值字典（为不同层计算量化阈值）
    all_op_names = self.parser.get_op_name_list()  # 获取模型中所有算子（层）的名称
    quantize_method_list = self.quantize_method_list  # 量化方法列表（如KL散度、Max、分位数、MSE等）
    # 为每个层生成不同量化方法对应的阈值（决定量化动态范围）
    layer_th_dicts = self.gen_multiple_thresholds(all_op_names, quantize_method_list)
    self.mix_prec.logger.print_info("quantize_method_list={}".format(quantize_method_list))  # 日志输出使用的量化方法

    # 加载校准表（存储量化核心参数，如scale/zero_point）
    try:
        # 优先尝试加载MSE优化的校准表（MSE方法通常精度更优）
        mse_cali_table = self.args.calibration_table + "_MSE_tune"
        with open(mse_cali_table, 'r') as file:
            data = file.read()
    except Exception as e:
        # 若MSE校准表不存在，加载默认量化方法的校准表
        _cali_table = self.args.calibration_table + "_" + quantize_method_list[0] +"_tune"
        with open(_cali_table, 'r') as file:
            data = file.read()
    # 将加载的校准表数据写入目标文件（供后续模型使用）
    with open(self.cali_table_name, 'w') as file:
        file.write(data)

    # step2: 初始化float32和int8模型并验证全int8精度
    float_model = MixQuantModel(self.fp32_mlir, self.chip)  # 加载float32模型（精度基准）
    int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.cali_table_name)  # 加载全int8量化模型
    float_outputs_cos = 1.0  # float32模型自身的余弦相似度（完美匹配）
    layer_cos_list, predictions_gt = [], []  # 存储每层余弦相似度、基准预测结果
    # 提取用于计算精度的关键层（全局对比层）和权重
    global_compare_layers, layers_rate, _ = self.mix_prec.extract_global_layers()
    # 运行float32模型，获取基准输出（用于后续与量化模型对比）
    _ = self.mix_prec.run_model(float_model, True, global_compare_layers, layers_rate, predictions_gt)
    # 运行int8模型，计算其输出与float32输出的余弦相似度（评估全int8精度）
    outputs_cos = self.mix_prec.run_model(int8_model, False, global_compare_layers, layers_rate, predictions_gt)
    # 若全int8精度已满足预期，直接退出（无需混合精度）
    if outputs_cos > self.args.expected_cos:
        float_model.clean()  # 释放模型资源
        int8_model.clean()
        self.mix_prec.enable_print()
        self.mix_prec.logger.print_info(
            f'job success, current int8 cos:{outputs_cos} is higher than expected_cos:{self.args.expected_cos},no need for mix precsion')
        exit(0)
    all_int8_cos = outputs_cos  # 记录全int8模型的精度（作为后续对比基准）
    self.mix_prec.logger.print_info("all_int8_cos={}".format(all_int8_cos))
    self.cali_table = CalibrationTable(self.cali_table_name)  # 初始化校准表对象（用于后续修改）

    # step3: 筛选待分析的敏感层（缩小搜索范围）
    # 获取固定为float32的层（如本身不适合量化的层，如归一化层）
    float_ops = self.mix_prec.get_fixed_float_layers(int8_model,global_compare_layers, layers_rate, predictions_gt)
    # 检查层名称有效性（排除无效/不支持量化的层）
    layer_names = self.check_layer_names(all_op_names, int8_model, layer_th_dicts, quantize_method_list)
    self.mix_prec.logger.print_info("all layer number: {}".format(len(layer_names)))  # 输出总层数
    # 排除固定为float32的层，保留可量化的层
    layer_names_quant = [layer for layer in layer_names if layer not in float_ops]
    self.mix_prec.logger.print_info("all layer number no float: {}".format(len(layer_names_quant)))  # 输出可量化的层数
    # 搜索对量化敏感的操作类型（如Transformer的QKV层、卷积层等，量化后精度损失大）
    sensitive_op_type = self.search_layer_type_no_need_quant(layer_names_quant, float_outputs_cos, global_compare_layers, layers_rate, predictions_gt)
    # 最终待分析的层：属于敏感操作类型的可量化层（只关注对量化敏感的层）
    layer_names =  [layer for layer in layer_names_quant if self.parser.get_op_type_by_op_name(layer) in sensitive_op_type]
    self.mix_prec.logger.print_info("transformer model: {}, all search layer number: {}".format(self.args.transformer, len(layer_names)))
    self.mix_prec.logger.print_info("Global metrics layer is : {}".format(global_compare_layers))  # 输出用于计算精度的关键层

    # step4: 搜索敏感层并生成损失字典（量化损失指标）
    # 返回：敏感层分析详情、新校准表名称、层-损失字典（键：层名，值：量化损失）
    sensitive_layer_analysis_dict, new_cali_table_name, loss_dict = self.search_sensitve_layer(layer_names, quantize_method_list, float_model, int8_model, layer_th_dicts, global_compare_layers, layers_rate, predictions_gt)

    # step5: 分析敏感层，生成混合精度配置表
    self.mix_prec.enable_print()  # 启用日志打印
    if self.args.cluster:  # 若启用聚类模式
        # 用K-Means将层聚为2类，选择损失最大的类作为需保留为float32的层
        selected_fp_layers = self.cluster(loss_dict, 2)
        mix_table = self.mix_prec._gen_mix_table(selected_fp_layers)  # 生成混合表（记录float32/int8分配）
    else:  # 非聚类模式：直接分析损失字典
        # 筛选损失最大的层作为需保留为float32的层
        set_fp_layer_list = self.analysis_sensitive_layers(sensitive_layer_analysis_dict, True)
        mix_table = self.mix_prec._gen_mix_table(set_fp_layer_list)  # 生成混合表

    # step6: 验证最终混合模型并输出结果
    self.mix_prec.dot_log.gen_dot_graph()  # 生成混合精度层关系图（可视化）
    # 加载混合精度表，生成最终混合模型
    mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table_name, mix_table)
    # 验证混合模型精度
    outputs_cos = self.mix_prec.run_model(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)
    self.mix_prec.logger.print_info("float layer number: {}, mix model outputs_cos: {}".format(self.args.max_float_layers,outputs_cos))  # 输出float层数量和最终精度

    # 根据是否聚类，执行不同的后续操作（微调校准表/输出日志）
    if not self.args.cluster:
        # 微调量化表（进一步优化精度）
        self.adjust_qtable(outputs_cos, layer_names_quant, sensitive_layer_analysis_dict, new_cali_table_name, global_compare_layers, layers_rate, predictions_gt)
        self.print_log_info(layer_cos_list, set_fp_layer_list, all_int8_cos, outputs_cos, t0)  # 输出详细日志
    else:
        self.print_log_info(layer_cos_list, selected_fp_layers, all_int8_cos, outputs_cos, t0)
    print("success search qtable")  # 提示校准表搜索完成
    return 'success'


### `run_4_8` 方法：int4与int8混合精度量化流程（追求极致性能）
def run_4_8(self):

    t0 = time.time()  # 记录开始时间
    layer_cos_list, predictions_gt = [], []  # 存储每层余弦相似度、基准预测结果
    # 初始化float32模型（基准）和int8模型（基础量化模型）
    float_model = MixQuantModel(self.fp32_mlir, self.chip)
    int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.cali_table_name)
    # 提取全局对比层和权重
    global_compare_layers, layers_rate, _ = self.mix_prec.extract_global_layers()
    # 运行float32模型获取基准输出
    _ = self.mix_prec.run_model(float_model, True, global_compare_layers, layers_rate, predictions_gt)
    # 计算全int8模型的精度（作为后续int4量化的基准）
    int8_outputs_cos = self.mix_prec.run_model(int8_model, False, global_compare_layers, layers_rate, predictions_gt)
    self.mix_prec.logger.print_info(f'current int8 cos:{int8_outputs_cos}')  # 输出int8基准精度

    # 筛选适合int4量化的层（计算密集型层，如卷积、矩阵乘法）
    all_op_names = self.parser.get_op_name_list()  # 所有层名称
    search_op_type = ['top.Conv', 'top.MatMul']  # 目标层类型（卷积、矩阵乘法，量化收益高）
    layer_names =  [layer for layer in all_op_names if self.parser.get_op_type_by_op_name(layer) in search_op_type]

    # 计算各层从int8转为int4后的损失（精度损失指标）
    loss_dict = self.search_sensitve_layer_int4(layer_names, all_op_names, global_compare_layers, layers_rate, predictions_gt)

    # 按损失从小到大排序并输出每层的精度指标（余弦相似度、信噪比）
    sorted_loss_items = sorted(loss_dict.items(), key=lambda item: item[1][0])
    for layer_name, values in sorted_loss_items:
        outputs_cos, outputs_snr = values  # outputs_cos：int4与float32的相似度；outputs_snr：信噪比（辅助指标）
        self.mix_prec.logger.print_info("Layer: {}, outputs_cos: {}, outputs_snr: {}".format(layer_name, outputs_cos, outputs_snr))

    # 对损失字典聚类，按损失从小到大排序（损失小的层优先转为int4）
    sorted_clusters = self.cluster_4_8(loss_dict)
    fp_layer_list = copy.deepcopy(all_op_names)  # 初始为全int8层列表（需转为int4的层将从列表中移除）

    # 迭代将聚类的层转为int4，验证精度并控制损失
    for cluster in sorted_clusters:
        # 将当前聚类的层标记为int4（写入校准表，从int8列表移除）
        for op_name in cluster:
            with open(self.cali_table_name, 'a') as file:
                file.write(f"{op_name}\n")  # 记录需用int4的层
            fp_layer_list.remove(op_name)  # 从int8列表中移除
        # 生成混合精度表（fp_layer_list为int8，其余为int4）
        mix_table = self.mix_prec._gen_mix_table_4_8(fp_layer_list, 'INT8')
        # 加载int4/int8混合模型
        int4_mix_model = MixQuantModel(self.fp32_mlir, self.chip, self.cali_table_name, mix_table, "int4")
        # 验证混合模型精度（余弦相似度、信噪比）
        outputs_cos, outputs_snr = self.mix_prec.run_model_new(int4_mix_model, False, global_compare_layers, layers_rate, predictions_gt)
        # 若精度损失超过int8基准的5%（1 - 相似度 衡量损失），回退当前聚类的层（重新转为int8）
        if 1 - outputs_cos < int8_outputs_cos * 0.95:
            self.remove_lines_from_file(self.cali_table_name, cluster)  # 从校准表移除当前聚类的层
            for op_name in cluster:
                fp_layer_list.append(op_name)  # 重新加入int8列表
            break  # 停止迭代，保留当前最优配置
    # 输出最终日志（int8层列表、总耗时等）
    self.print_log_info_4_8(fp_layer_list, int8_outputs_cos, t0)


### `run_fast` 方法：快速float32与int8混合精度量化（效率优先）
def run_fast(self):
    t0 = time.time()  # 记录开始时间
    layer_cos_list, predictions_gt = [], []  # 存储每层余弦相似度、基准预测结果
    # 初始化float32和int8模型
    float_model = MixQuantModel(self.fp32_mlir, self.chip)
    int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.cali_table_name)
    # 提取全局对比层和权重
    global_compare_layers, layers_rate, _ = self.mix_prec.extract_global_layers()
    # 运行float32模型获取基准输出
    _ = self.mix_prec.run_model(float_model, True, global_compare_layers, layers_rate, predictions_gt)
    # 计算全int8模型精度
    int8_outputs_cos = self.mix_prec.run_model(int8_model, False, global_compare_layers, layers_rate, predictions_gt)
    # 若全int8精度达标，直接退出
    if int8_outputs_cos > self.args.expected_cos:
        float_model.clean()
        int8_model.clean()
        self.mix_prec.enable_print()
        self.mix_prec.logger.print_info(
            f'job success, current int8 cos:{int8_outputs_cos} is higher than expected_cos:{self.args.expected_cos},no need for mix precsion')
        exit(0)

    # 筛选待分析的敏感层
    float_outputs_cos = 1.0  # float32模型自身的相似度
    all_op_names = self.parser.get_op_name_list()  # 所有层名称
    # 搜索对量化敏感的操作类型
    sensitive_op_type = self.search_layer_type_no_need_quant(all_op_names, float_outputs_cos, global_compare_layers, layers_rate, predictions_gt)
    # 待分析的层：属于敏感操作类型的层
    layer_names =  [layer for layer in all_op_names if self.parser.get_op_type_by_op_name(layer) in sensitive_op_type]
    self.mix_prec.logger.print_info("transformer model: {}, all search layer number: {}".format(self.args.transformer, len(layer_names)))

    # 迭代筛选敏感层（每次选top5损失最大的层保留为float32）
    sensitive_layer = []  # 记录需保留为float32的层
    cos_sim = int8_outputs_cos  # 当前精度（初始为全int8精度）
    count = 0  # 迭代次数
    eps = 0  # 精度提升阈值（用于判断是否需继续迭代）
    # 若当前精度未达标，继续迭代
    while int8_outputs_cos < self.args.expected_cos:
        # 快速计算当前待分析层的损失字典
        loss_dict = self.search_sensitve_layer_fast(layer_names, all_op_names, sensitive_layer, global_compare_layers, layers_rate, predictions_gt, count)

        # 双指标排序（综合2个损失值），筛选损失最大的top5层
        keys = list(loss_dict.keys())
        sorted_by_first = sorted(keys, key=lambda k: loss_dict[k][0])  # 按第一个损失值排序
        rank_first = {key: idx for idx, key in enumerate(sorted_by_first)}  # 第一个损失值的排名
        sorted_by_second = sorted(keys, key=lambda k: loss_dict[k][1])  # 按第二个损失值排序
        rank_second = {key: idx for idx, key in enumerate(sorted_by_second)}  # 第二个损失值的排名
        total_rank = {key: rank_first[key] + rank_second[key] for key in keys}  # 总排名（越小损失越小）
        # 按总排名和第一个损失值排序，取最后5个（损失最大）
        sorted_keys = sorted(keys, key=lambda k: (total_rank[k], loss_dict[k][0]))
        top_5_layers = sorted_keys[-5:]

        # 更新待分析层（排除已选的top5层）
        layer_names = [layer for layer in layer_names if layer not in top_5_layers]
        # 将top5层加入敏感层列表（去重）
        sensitive_layer += [layer for layer in top_5_layers if layer not in sensitive_layer]

        # 生成混合精度表并验证精度
        mix_table = self.mix_prec._gen_mix_table(sensitive_layer)  # 记录float32/int8分配
        int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.cali_table_name, mix_table)  # 加载混合模型
        outputs_cos = self.mix_prec.run_model(int8_model, False, global_compare_layers, layers_rate, predictions_gt)  # 验证精度

        # 终止条件：精度提升不明显、敏感层超总层20%、迭代次数上限
        if (outputs_cos - cos_sim < eps and outputs_cos - cos_sim < 0.001) or len(sensitive_layer) > 0.2 * len(all_op_names):
            break
        else:
            # 更新精度提升阈值（首次迭代为当前提升的一半）
            if count == 0:
                eps = abs(outputs_cos - cos_sim) / 2
            cos_sim = outputs_cos  # 更新当前精度
            count += 1  # 迭代次数+1
            if count > self.args.inference_num - 1:  # 达到最大迭代次数
                break
    # 输出最终日志
    self.print_log_info(layer_cos_list, sensitive_layer, int8_outputs_cos, outputs_cos, t0)
```

#### 7.adjust_qtable

功能：`adjust_qtable` 是混合精度量化中的精度微调模块，核心目标是在保证模型精度达标的前提下，尽可能减少浮点层数量（以提升性能）。

原理：

1. 初始调整阶段：
   当混合模型精度未达标，且有足够的可量化层可转为浮点时，临时增加浮点层数量（设为可量化层的 1/5），验证是否能通过增加浮点层使精度达标。
2. 二分查找优化阶段：
   若初始调整后精度达标，通过二分法在原始最大浮点层数量和临时最大浮点层数量之间寻找最小值 —— 既满足精度要求，又能使浮点层数量最少。这样既保证了模型精度，又最大限度减少了浮点层（降低计算成本）。

```python
def adjust_qtable(self, outputs_cos, layer_names_quant, sensitive_layer_analysis_dict, new_cali_table_name, global_compare_layers, layers_rate, predictions_gt):
    """
    调整量化表（qtable）和浮点层数量，在保证精度达标的前提下，尽量减少浮点层数量（平衡精度与性能）。
    核心逻辑：当混合模型精度不达标时，通过增加浮点层提升精度；若达标则用二分法找到满足精度的最小浮点层数量。
    
    参数说明：
    - outputs_cos: 当前混合模型的余弦相似度（精度指标，越接近1越好）
    - layer_names_quant: 可量化的层列表（可能被转为浮点的候选层）
    - sensitive_layer_analysis_dict: 敏感层分析字典（记录各层量化损失）
    - new_cali_table_name: 新校准表名称（量化参数文件）
    - global_compare_layers: 用于计算精度的关键层
    - layers_rate: 各关键层的权重（计算整体精度时使用）
    - predictions_gt: 基准模型（float32）的预测结果（用于精度对比）
    """
    
    # 条件判断：是否需要调整浮点层数量
    # 1. 当前混合模型精度未达标（outputs_cos < 预期值）
    # 2. 可量化层的1/5数量 > 当前最大允许的浮点层数量（说明有足够的层可转为浮点来提升精度）
    if outputs_cos < self.args.expected_cos and (len(layer_names_quant) // 5) > self.args.max_float_layers:
        
        # 保存当前最大浮点层数量作为基准（后续二分查找的下界）
        base_float_layers = self.args.max_float_layers
        # 临时将最大浮点层数量调整为可量化层的1/5（尝试增加浮点层提升精度）
        self.args.max_float_layers = len(layer_names_quant) // 5
        
        # 根据新的最大浮点层数量，筛选敏感层中损失最大的层作为浮点层
        set_fp_layer_list = self.analysis_sensitive_layers(sensitive_layer_analysis_dict, False)
        # 生成新的混合精度表（记录哪些层用浮点，哪些用int8）
        mix_table = self.mix_prec._gen_mix_table(set_fp_layer_list)
        # 加载新的混合模型
        mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table_name, mix_table)
        # 验证新混合模型的精度
        outputs_cos = self.mix_prec.run_model(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)
        self.mix_prec.logger.print_info(f"float layer number: {self.args.max_float_layers}, mix model outputs_cos: {outputs_cos}")
        
        # 若调整后精度达标，进入二分查找阶段（寻找满足精度的最小浮点层数量）
        if outputs_cos > self.args.expected_cos:
            # 二分查找的下界：原始最大浮点层数量（base_float_layers）
            lower_bound = base_float_layers
            # 二分查找的上界：当前临时设置的最大浮点层数量（可量化层的1/5）
            upper_bound = len(layer_names_quant) // 5
            
            # 二分查找循环：不断缩小范围，找到最小的浮点层数量
            while lower_bound <= upper_bound:
                # 计算当前中间值作为候选最大浮点层数量
                self.args.max_float_layers = (lower_bound + upper_bound) // 2
                # 根据当前候选值，筛选对应的浮点层列表
                set_fp_layer_list = self.analysis_sensitive_layers(sensitive_layer_analysis_dict, False)
                # 生成混合精度表并加载模型
                mix_table = self.mix_prec._gen_mix_table(set_fp_layer_list)
                mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table_name, mix_table)
                # 验证当前模型的精度
                outputs_cos = self.mix_prec.run_model(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)
                self.mix_prec.logger.print_info(f"float layer number: {self.args.max_float_layers}, mix model outputs_cos: {outputs_cos}")
                
                # 二分逻辑：
                # 若当前精度达标，尝试减小浮点层数量（缩小上界）
                if outputs_cos > self.args.expected_cos:
                    upper_bound = self.args.max_float_layers - 1
                # 若当前精度不达标，需要增加浮点层数量（扩大下界）
                else:
                    lower_bound = self.args.max_float_layers + 1
```

## 12.mix_precision.py

### 一、整体定位

本文件是 TPU-MLIR 量化校准工具链中混合精度量化（Mix Precision Quantization）的核心实现。主要功能包括：

- 支持自动/手动混合精度（如 INT8+FP16/FP32）量化策略的搜索与应用；
- 支持量化模型和浮点模型的推理、精度评估（如 cosine similarity、SNR、SQNR）；
- 支持敏感层自动分析、量化表生成、可视化、权重量化均衡、偏置修正等高级功能；
- 支持多种输入数据格式（图片、npy、npz）和多种芯片类型。

这个文件包括两个类：MixQuantModel 和 MixPrecSearcher。其中，`MixQuantModel` 是混合精度量化模型的核心封装类，负责模型的具体构建与推理，能够基于原始 FP32 MLIR 文件、芯片类型以及校准表、混合精度表等配置，生成不同精度（如 INT8、INT4、FP16 等）的模型，并提供推理（包括完整推理和从指定操作开始的部分推理）以及临时文件和内存清理的功能；`MixPrecSearcher` 的核心功能是作为混合精度量化的主控制器，统筹整个量化流程，涵盖输入数据（如图片、npy、npz 等）的初始化准备、模型敏感层的分析、混合精度量化表的生成、量化后模型精度的评估，以及权重均衡、偏置修正等优化操作，同时能输出量化表和精度相关的日志信息。

### 二、MixPrecSearcher 类

#### 1. init(self, args)

功能：初始化混合精度搜索器，解析输入参数、加载 MLIR 模型、初始化数据、配置日志等。

原理：

- 接收外部参数（`args`），包括 MLIR 文件路径、校准表、损失表等，初始化核心参数（如芯片类型、混合精度模式）。
- 通过 `MlirParser` 解析 MLIR 模型，获取输入信息（批次大小、输入数量等）。
- 初始化日志（`logger`），根据调试命令（`debug_cmd`）设置日志级别。
- 调用 `_init_inputs` 加载并预处理输入数据，存储原始精度（`ref_activations`）和 INT8 精度（`int8_activations`）的激活值缓存。

#### 2. disable_print(self)与 enable_print(self)

功能：临时关闭 / 恢复标准输出，避免推理过程中冗余打印干扰日志。

原理：

- `disable_print` 将 `sys.stdout` 重定向到 `os.devnull`（黑洞设备），屏蔽输出。
- `enable_print` 恢复 `sys.stdout` 到原始状态。

#### 3. _init_inputs(self, args)

功能：加载并预处理输入数据，初始化 `ref_activations`（原始精度激活值缓存）。
原理：

- 根据数据集类型（图片、npy 文件、npz 文件），通过 `DataSelector` 选择数据。
- 对图片数据：使用 `preprocess` 预处理，按批次（`batch_size`）组织输入，存储到 `ref_activations`。
- 对 npy/npz 文件：直接加载数据，按样本存储到 `ref_activations`。
- 记录每个输入操作的 “用户计数”（`user count`，即该操作被后续层使用的次数），用于后续缓存管理。

```python
# 处理图片数据（按批次加载）
if ds.all_image:# 初始化每个输入的预处理器
    for i in range(self.input_num):
        ppa = preprocess()
        ppa.load_config(self.parser.get_input_op_by_idx(i))
        ppa_list.append(ppa)# 补齐批次（确保数据量为batch_size的整数倍）
    n = len(ds.data_list) % self.batch_size
    if n != 0:for i in range(self.batch_size - n):
            ds.data_list.append(ds.data_list[-1])
    self.num_sample = len(ds.data_list)   # 总样本数
    batched_idx = 0
    batched_inputs = self.input_num * ['']
    for data in ds.data_list:
        inputs = data.split(',')  # 每个样本可能包含多个输入（如多输入模型）
        batched_idx += 1
        for i, input in enumerate(input_names):# 拼接批次内的输入路径（如"img1.jpg,img2.jpg,..."）
            batched_inputs[i] += '{},'.format(inputs[i])# 当批次满时，预处理并存储
            if batched_idx == self.batch_size:
                x = ppa_list[i].run(batched_inputs[i][:-1])  # 预处理批次数据
                count = self.parser.get_user_count_by_op_name(input)  # 获取该输入被使用的次数
                self.ref_activations[tune_idx][input] = [x, count]  # 缓存：[数据, 使用次数]# 重置批次计数器，准备下一批
        if batched_idx == self.batch_size:
            tune_idx += 1
            batched_idx = 0
            batched_inputs = self.input_num * ['']
            self.ref_activations[tune_idx] = {}
```

这段代码的核心是按批次处理图片数据，通过预处理器将原始图片转换为模型输入格式，并缓存输入数据及使用次数，为后续层的激活值计算提供基础。

#### 4. _gen_mix_table(self, mix_ops)与_gen_mix_table_4_8(self, mix_ops, mix_mode)

功能：生成混合精度配置表（临时文件），指定哪些操作使用目标精度（如 FP32）。

原理：

- 写入 “操作名 精度模式” 格式的内容到临时文件（如 `tmp_mix_table.txt`）。
- 量化工具可通过该表识别需要保持高精度的层，其余层默认使用低精度（如 INT8）。

#### 5.损失计算方法（`_cal_sqnr`、`_sqnr_loss`、`_cos_loss`、`_snr_loss`、`_loss`）

功能：评估量化后（低精度）与原始（高精度）输出的差异，作为混合精度搜索的依据。

原理：

- SQNR（信号与量化噪声比）：`_cal_sqnr` 计算原始信号与量化信号的噪声比，值越高表示量化误差越小。
- 余弦相似度（Cosine Similarity）：`_cos_loss` 计算输出向量的余弦相似度，值越接近 1 表示差异越小。
- SNR（信噪比）：`_snr_loss` 计算信号功率与噪声功率的比值，值越低表示误差越小。
- `_loss` 是统一接口，可选择上述任一指标作为损失函数。
- `_sqnr_loss` 是基于 `_cal_sqnr`（信号与量化噪声比）的损失计算方法，用于评估多个层的整体量化误差，而非单个层的误差。

```python
def _cal_sqnr(self, signal_raw, signal_dequant, remove_zero=False):
    raw = signal_raw.flatten()  # 原始高精度信号（如FP32）
    dequant = signal_dequant.flatten()  # 量化后信号（如INT8反量化到FP32）
    if remove_zero:  # 可选：过滤零值（避免零值对噪声的影响）
        idx = dequant != 0
        raw = raw[idx]
        dequant = dequant[idx]

    noise = raw - dequant  # 噪声 = 原始信号 - 量化信号# 计算零均值信号和噪声（去除直流分量影响）
    avg_raw = np.sum(raw) / raw.size
    avg_noise = np.sum(noise) / noise.size
    raw_zero_mean = raw - avg_raw
    noise_zero_mean = noise - avg_noise

    # 计算信号和噪声的方差
    var_raw_zero_mean = np.sum(np.square(raw_zero_mean))  # 信号方差
    var_noise_zero_mean = np.sum(np.square(noise_zero_mean))  # 噪声方差
    if var_noise_zero_mean == 0.0:  # 无噪声时返回无穷大
        return math.inf

    # SQNR = 10 * log10(信号方差 / 噪声方差)
    sqnr = 10 * np.log10(var_raw_zero_mean / var_noise_zero_mean)
    return sqnr
```

该方法通过统计信号与噪声的方差比，量化评估量化误差，是判断层是否适合低精度的核心指标。

#### 6.模型运行方法（`run_model`、`run_model_new`、`run_model_fast`）

功能：在不同精度模式（FP32/INT8）下运行模型，获取输出并计算损失。

原理：

- 对 FP32 模式：运行模型并缓存输出（`predictions_gt`）作为 “ground truth”。
- 对 INT8 模式：运行模型并与 `predictions_gt` 比较，计算损失（如余弦相似度）。
- 支持后处理（`post_process`），对输出进行特定处理（如目标检测的 NMS）。

#### 7. `search_mix_layer(self, ...)`

功能：核心方法，自动搜索需要保持高精度的层，以最小化精度损失。

原理：

- 遍历网络层，计算各层 INT8 量化后的余弦相似度（`avg_cos`）。
- 若某层的 `avg_cos` 低于阈值（`args.min_layer_cos`），将该层及后续依赖层设置为高精度（如 FP32）。
- 监控整体损失，若达标（高于 `args.expected_cos`）则停止搜索。

```python
# 遍历所有操作，判断是否需要设置为高精度
for op in pbar:# 生成INT8和FP32的激活值缓存
    have_int8_next = self.gen_int8_tensor(op, int8_op_names, int8_model, fp_layer_list)
    self.gen_fp_tensor(op, fp_op_names, float_model)# 跳过无需处理的层（如已在高精度列表、无后续INT8层等）
    if self.skip_op(op, top_ops, have_int8_next, fp_layer_list, all_pre_layers):
    continue# 计算当前层INT8量化后的平均余弦相似度
    avg_cos = self.cal_avg_layer_cos(op)
    layer_cos_list.append((op.name, avg_cos))# 若相似度低于阈值，将该层及后续层设为高精度
    if avg_cos < self.args.min_layer_cos:
        next_top_ops = self.parser.get_next_op_by_op_name(op.name)  # 后续依赖层# 生成混合精度表，运行模型并计算损失
        outputs_cos, mix_model = self.set_curr_layer_and_the_next_layer_mix(...)# 若损失达标，停止搜索
        if outputs_cos > self.args.expected_cos:
            break# 若高精度层数量超限，停止搜索
        if len(fp_layer_list) > max_fp32_layer_num:
            break
```

这段代码通过评估各层量化后的相似度，动态调整高精度层集合，在精度损失与效率之间找到平衡。

#### 8. run(self)

功能：混合精度搜索的主流程入口。

原理：

- 初始化 FP32 模型，获取原始输出作为基准（`predictions_gt`）。
- 运行 INT8 模型，计算初始损失；若损失达标，无需混合精度。
- 调用 `search_mix_layer` 搜索最优混合精度配置，输出结果（损失表、量化表）。

#### 9.优化方法（`run_bias_correction`、`weight_equalization`）

功能：通过调整模型参数（偏置、权重）减少量化误差，提升混合精度模型性能。

- 偏差校正（`run_bias_correction`）：计算原始与量化输出的均值差，调整卷积层偏置以补偿误差。

在模型从高精度（FP32）量化到低精度（如 INT8）的过程中，由于数值范围截断、舍入误差等原因，卷积层的输出激活值会出现均值偏移（即 INT8 量化后的输出均值与 FP32 原始输出均值存在差异）。这种偏移会在网络中累积，导致最终输出精度下降。

而卷积层的输出公式为：`output = activation(weight * input + bias)`。
其中，`bias`（偏置）是一个可调整的参数，对输出均值的影响直接且线性。因此，通过微调 `bias` 可以有效补偿量化导致的均值偏移，让 INT8 输出的均值更接近 FP32，从而减少精度损失。

```python
def run_bias_correction(self):
    """
    偏差校正（Bias Correction）优化：
    通过调整卷积层的偏置参数，补偿量化过程中产生的均值偏移，提升低精度（如INT8）模型的输出精度。
    核心逻辑：计算原始高精度输出与量化后输出的均值差，调整偏置以抵消该差异，减少量化误差。
    """
    self.logger.print_info("偏差校正优化开始")
    t0 = time.time()  # 记录开始时间，用于计算总耗时
    layer_cos_list = list()  # 存储各层校正前后的余弦相似度
    predictions_gt = list()  # 存储高精度模型的输出，作为"基准值"
    # 从MLIR模型中解析权重文件路径（用于后续调整偏置）
    weight_file_name = MlirParser(self.fp32_mlir).module_weight_file
    # 清理并创建张量差异可视化目录（用于调试时查看量化前后差异）
    os.system('rm -rf tensor_diff_fp32_vs_int8;mkdir -p tensor_diff_fp32_vs_int8/')

    # --------------------------
    # 步骤1：运行高精度（FP32）模型，获取基准输出
    # --------------------------
    self.logger.print_info(f"运行高精度模式：{self.fp32_mlir}")
    self.disable_print()  # 临时关闭打印，避免冗余输出
    # 初始化高精度模型（所有层均为FP32）
    float_model = MixQuantModel(self.fp32_mlir, self.chip)
    # 解析全局比较层配置（用于指定需要重点关注的输出层）
    global_compare_layers = None
    layers_rate = None  # 各比较层的权重（用于计算整体损失）
    all_pre_layers = None  # 所有比较层的前置依赖层
    if self.args.global_compare_layers != '':
        # 解析格式如"layer1:0.3,layer2:0.7"的配置，提取层名和权重
        global_compare_layers = [i.strip() for i in self.args.global_compare_layers.split(',')]
        layers_rate = [float(i.split(':')[-1].strip()) for i in global_compare_layers if ':' in i]
        global_compare_layers = [i.split(':')[0].strip() for i in global_compare_layers]
        # 收集所有比较层的前置依赖层（用于后续过滤无关层）
        all_pre_layers = []
        for layer in global_compare_layers:
            pre_layers = []
            find_all_pre_layers(pre_layers, layer, self.parser)  # 递归查找前置层
            all_pre_layers.extend(pre_layers)
        all_pre_layers = set(all_pre_layers)
        # 校验权重格式（数量与层匹配，且总和为1）
        if len(layers_rate) > 0:
            if len(global_compare_layers) != len(layers_rate):
                print('global_compare_layers格式错误（层数量与权重不匹配）')
                exit(1)
            if 1 != sum(layers_rate):
                print('global_compare_layers权重错误（总和不为1）')
                exit(1)
        else:
            # 若未指定权重，默认各层权重相等
            layers_rate = len(global_compare_layers) * [1]
    # 运行高精度模型，保存所有样本的输出作为基准
    for idx in range(self.num_sample):
        data = []
        # 加载当前样本的输入数据（从高精度激活值缓存中获取）
        for name in list(self.ref_activations[idx].keys()):
            data.append(self.ref_activations[idx][name][0])
        # 推理并保存输出（作为后续量化模型的对比基准）
        outputs = float_model.infer(data, global_compare_layers)
        predictions_gt.append(outputs)

    # --------------------------
    # 步骤2：运行全INT8模型，评估初始量化误差
    # --------------------------
    self.logger.print_info(f"运行全INT8模式：{self.fp32_mlir}")
    outputs_cos = 0  # 累计全INT8模型的余弦相似度（整体损失）
    # 初始化全INT8模型（使用校准表进行量化）
    int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table)
    for idx in range(self.num_sample):
        data = []
        # 加载当前样本的输入数据（与高精度模型一致）
        for name in list(self.ref_activations[idx].keys()):
            data.append(self.ref_activations[idx][name][0])
        # 推理INT8模型
        outputs = int8_model.infer(data, global_compare_layers)
        # 计算当前样本的输出与基准的余弦相似度（累加求平均）
        outputs_cos += self._loss(outputs, predictions_gt[idx], layers_rate)
    outputs_cos = outputs_cos / self.num_sample  # 平均余弦相似度
    all_int8_cos = outputs_cos  # 保存全INT8模型的初始性能

    # 若全INT8模型性能已达标（超过预期余弦值），无需校正，直接退出
    if outputs_cos > self.args.expected_cos:
        float_model.clean()
        int8_model.clean()
        self.enable_print()  # 恢复打印
        self.logger.print_info(
            f'优化成功，全INT8模型余弦值:{outputs_cos:.6f} 已超过预期值:{self.args.expected_cos:.6f}，无需偏差校正')
        exit(0)

    # --------------------------
    # 步骤3：初始化校正相关变量，准备遍历各层
    # --------------------------
    max_outputs_cos = all_int8_cos  # 跟踪校正后的最大整体余弦值
    max_layer_cos, ret = None, None  # 跟踪校正后的最大层余弦值
    fp_layer_list = []  # 高精度层列表（此处暂未使用，预留用于混合精度）
    layer_cos_list.clear()  # 清空层余弦列表
    # 初始化日志可视化工具（用于生成校正过程的图表）
    self.dot_log = net_dot_log('bias_correction', int8_model.parser, self.logger)
    self.dot_log.add_new_log_region('计算余弦相似度并执行偏差校正')

    # 构建包含所有操作的列表（融合高精度和INT8模型的操作）
    fp_op_names = float_model.parser.get_op_name_list()  # 高精度模型的操作名
    top_ops = {op.name: op for op in self.parser.ops}  # 目标操作字典（键为操作名）
    int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table)  # 重新初始化INT8模型
    int8_op_names = int8_model.parser.get_op_name_list()  # INT8模型的操作名
    full_op_list, cur_fp_op_idx = {}, 0  # 融合后的操作列表及当前索引
    for int8_op in int8_op_names:
        # 查找INT8操作在高精度模型中的位置
        pos = fp_op_names.index(int8_op) if int8_op in fp_op_names else -1
        if -1 == pos:
            # 若INT8操作不在高精度模型中，直接添加到融合列表
            full_op_list[int8_op] = int8_model.parser.get_op_by_op_name(int8_op)
        else:
            # 若存在，将高精度模型中从当前索引到pos的操作添加到融合列表
            for i in range(cur_fp_op_idx, pos + 1):
                full_op_list[fp_op_names[i]] = float_model.parser.get_op_by_op_name(fp_op_names[i])
            cur_fp_op_idx = pos + 1  # 更新当前索引
    print('融合后的操作列表:', list(full_op_list.keys()))

    # --------------------------
    # 步骤4：遍历各层，对符合条件的卷积层执行偏差校正
    # --------------------------
    # 使用进度条遍历所有操作
    pbar = tqdm(list(full_op_list.values()))
    for op in pbar:
        pbar.set_description(f"处理操作: {op.name}")
        have_int8_next = False  # 标记当前层是否有INT8后继层（需生成INT8激活值）

        # 生成当前层的INT8激活值（若有INT8后继层）
        if op.name in int8_op_names:
            # 检查当前层的后继层是否有非高精度层（需保留INT8激活值）
            for next_int8_op in int8_model.parser.get_next_op_by_op_name(op.name):
                if next_int8_op not in fp_layer_list:
                    have_int8_next = True
                    break
            if have_int8_next:
                # 为所有样本生成当前层的INT8激活值并缓存
                for idx in range(self.num_sample):
                    ret = self.gen_ref_tensor(idx, op.name, self.int8_activations, int8_model, True)
                if ret:
                    self.dot_log.add_node_label(op.name, '已生成INT8激活值')
            else:
                self.dot_log.add_node_label(op.name, f'后继层为{self.mix_mode}，无需生成INT8激活值')

        # 生成当前层的高精度激活值（若为高精度层）
        if op.name in fp_op_names:
            for idx in range(self.num_sample):
                ret = self.gen_ref_tensor(idx, op.name, self.ref_activations, float_model)
            if ret:
                self.dot_log.add_node_label(op.name, f'已生成{self.mix_mode}激活值')

        # 过滤无需处理的层
        if op.name not in top_ops:
            self.dot_log.add_node_label(op.name, '操作不在目标方言中，跳过')
            continue
        if op.type in SKIP_OPERATION:
            # 若为量化跳过类型（如无需量化的操作），处理其前置层
            pre_layers = self.parser.get_pre_op_by_op_name(op.name)
            for pre_layer in pre_layers:
                if pre_layer in fp_layer_list:
                    self.dot_log.add_node_label(op.name, f'将{op.name}添加到高精度层列表')
                    fp_layer_list.append(op.name)
                    break
            self.dot_log.add_node_label(op.name, f'遇到量化跳过类型: {op.type}，继续')
            continue
        if op.name in fp_layer_list:
            self.dot_log.add_node_label(op.name, f'操作已在{self.mix_mode}层列表中，跳过')
            continue
        if not have_int8_next:
            continue  # 无INT8后继层，无需处理
        if op.name not in top_ops:
            self.dot_log.add_node_label(op.name, '操作仅存在于INT8模型中，跳过')
            continue
        if all_pre_layers is not None and op.name not in all_pre_layers:
            self.dot_log.add_node_label(op.name, '不在比较层的前置依赖中，跳过')
            continue

        # 对符合条件的卷积层执行偏差校正
        top_op = top_ops[op.name]  # 获取目标操作
        layer_cos, int8_mean, fp32_mean = 0, 0, 0  # 层余弦相似度、INT8均值、FP32均值
        # 计算当前层INT8与FP32输出的均值和余弦相似度
        for idx in range(self.num_sample):
            # 获取当前样本的INT8输出（反量化到FP32）和FP32输出
            int8_out = self.get_input_int8_tensor(idx, top_op.name, True)
            fp32_out = self.get_input_fp32_tensor(idx, top_op.name)
            # 计算通道维度的均值（假设格式为[N, C, H, W]，沿N/H/W维度求平均）
            int8_mean += np.mean(int8_out, axis=(0, 2, 3))
            fp32_mean += np.mean(fp32_out, axis=(0, 2, 3))
            # 累计余弦相似度
            layer_cos += cos_sim(int8_out, fp32_out)
        # 计算平均余弦相似度（校正前）
        old_layer_cos = layer_cos / self.num_sample
        print(f'校正前的层余弦相似度: {old_layer_cos:.6f}')

        # 仅对余弦相似度低的卷积层执行校正（且存在偏置参数）
        if old_layer_cos < self.args.min_layer_cos and top_op.type == 'top.Conv' and len(top_op.opds) > 2:
            # 加载权重文件并备份（用于校正失败时恢复）
            weight_file_dict = {}
            weight_file = np.load(weight_file_name)
            os.system(f'cp -f {weight_file_name} {weight_file_name}_tmpbk')  # 备份原始权重
            for k in weight_file:
                weight_file_dict[k] = weight_file[k]  # 权重字典化

            # 计算均值差（FP32均值 - INT8均值），用于调整偏置
            mean_diff = (fp32_mean - int8_mean) / self.num_sample  # 平均到每个样本
            # 获取当前卷积层的偏置（op.opds[2]通常为偏置的名称）
            bias_float = weight_file_dict[top_op.opds[2]]
            bias_float += mean_diff  # 调整偏置以补偿均值差
            weight_file_dict[top_op.opds[2]] = bias_float  # 更新偏置

            # 保存调整后的权重
            os.system(f'rm -f {weight_file_name}')
            np.savez(weight_file_name, **weight_file_dict)

            # 验证校正效果：运行调整后的模型
            mix_model = MixQuantModel(self.fp32_mlir, self.chip, self.calib_table)
            extra_input = self.get_extra_input_tensor(top_op.name, int8_model.parser)  # 额外输入层
            layer_cos_new, outputs_cos_new = 0, 0  # 校正后的层余弦和整体余弦
            for idx in range(self.num_sample):
                # 收集输入数据（包含额外依赖层）
                input_data_dict, extra_input_data_dict = self.collect_op_input_tensor(
                    idx, top_op.name, extra_input, fp_layer_list)
                # 从当前层开始推理，获取输出
                outputs = mix_model.infer_from(top_op.name, input_data_dict, extra_input_data_dict,
                                               global_compare_layers)
                # 计算校正后的层余弦和整体余弦
                mix_layer_out = mix_model.module.get_fp32_tensor(top_op.name)
                fp32_out = self.get_input_fp32_tensor(idx, top_op.name)
                layer_cos_new += cos_sim(mix_layer_out, fp32_out)
                outputs_cos_new += self._loss(outputs, predictions_gt[idx], layers_rate)
            # 计算平均值
            layer_cos_new = layer_cos_new / self.num_sample
            outputs_cos_new = outputs_cos_new / self.num_sample

            # 判断校正效果：若更差则恢复原始权重，否则保留调整
            if outputs_cos_new < max_outputs_cos or (max_layer_cos is not None and layer_cos_new < max_layer_cos):
                os.system(f'cp -f {weight_file_name}_tmpbk {weight_file_name}')  # 恢复备份
                print(
                    f'操作{top_op.name}：偏差校正撤销，校正后层余弦:{layer_cos_new:.6f}，整体余弦:{outputs_cos_new:.6f}')
            else:
                print(
                    f'操作{top_op.name}：偏差校正应用，校正后层余弦:{layer_cos_new:.6f}，整体余弦:{outputs_cos_new:.6f}')
            # 记录校正前后的指标
            layer_cos_list.append((top_op.name, old_layer_cos, layer_cos_new, outputs_cos_new))
            # 更新最优指标
            max_outputs_cos = outputs_cos_new if outputs_cos_new > max_outputs_cos else max_outputs_cos
            max_layer_cos = layer_cos_new if (max_layer_cos is None or layer_cos_new > max_layer_cos) else max_layer_cos

            # 若校正后性能已达标，提前退出
            if max_outputs_cos > self.args.expected_cos:
                print(f'优化成功，当前余弦值已超过预期值:{self.args.expected_cos:.6f}')
                break

        # 清理当前层的激活值缓存（释放内存）
        self.clear_ref_tensor(top_op.name, self.parser, self.ref_activations)
        self.clear_ref_tensor(top_op.name, int8_model.parser, self.int8_activations)

    # --------------------------
    # 步骤5：输出校正结果并清理资源
    # --------------------------
    self.dot_log.gen_dot_graph()  # 生成校正过程的可视化图表
    int8_model.clean()  # 清理INT8模型资源
    float_model.clean()  # 清理高精度模型资源
    self.enable_print()  # 恢复打印

    # 打印最终结果
    self.logger.print_info('>>>校正结果:')
    self.logger.print_info(f'全INT8模型初始余弦值: {all_int8_cos:.6f}')
    self.logger.print_info('偏差校正统计:')
    for item in layer_cos_list:
        self.logger.print_info(
            f'  操作:{item[0]}, 校正前层余弦:{item[1]:.6f}, 校正后层余弦:{item[2]:.6f}, 校正后整体余弦:{item[3]:.6f}')
    self.logger.print_info(f'最优混合模型整体余弦值: {max_outputs_cos:.6f}')
    self.logger.print_info(f'总耗时: {time.time() - t0}秒')
```

- 权重均衡（`weight_equalization`）：跨层调整权重缩放因子，使相邻层（如 Conv->Relu->Conv）的量化范围更匹配，减少误差累积。而权重均衡的目标是让相邻卷积层的权重动态范围尽可能接近，同时保证两层的整体输出不变（通过缩放补偿），从而降低量化误差。

```python
def weight_equalization(self):
    """
    跨层权重均衡（Cross Layer Weight Equalization）优化：
    通过调整相邻卷积层的权重缩放因子，使两层的权重动态范围更匹配，减少量化误差。
    适用于"Conv -> (激活函数) -> Conv"模式的相邻卷积层，提升低精度量化性能。
    """
    # 步骤1：初始化准备工作
    # 获取模型权重文件路径（从MLIR模型中解析）
    weight_file_name = MlirParser(self.fp32_mlir).module_weight_file
    # 加载MLIR模块，用于获取张量形状信息
    module = pymlir.module()
    module.load(self.fp32_mlir)
    # 清理并创建张量差异可视化目录（用于调试）
    os.system('rm -rf tensor_diff_fp32_vs_int8;mkdir -p tensor_diff_fp32_vs_int8/')
    
    # 步骤2：遍历模型操作，筛选需要处理的相邻卷积层对
    # 遍历模型中所有操作
    all_ops = self.parser.ops
    for op in all_ops:
        # 筛选出卷积层（仅处理"top.Conv"类型的操作）
        if self.parser.get_op_type_by_op_name(op.name) == 'top.Conv':
            # 查找当前卷积层的有效后继层（需符合"Conv -> Relu -> Conv"或"Conv -> Conv"模式）
            succ = self.find_successor(op.name)
            # 仅处理有且仅有一个有效后继层的情况
            if len(succ) != 1:
                continue
            # 步骤3：初始化迭代参数，准备权重调整
            # 初始化迭代计数器
            iter = 1
            weight_file_dict = {}
            # 迭代调整权重直到收敛
            while True:
                # 步骤4：加载当前权重文件并解析
                weight_file = np.load(weight_file_name)
                for k in weight_file:
                    weight_file_dict[k] = weight_file[k]
                # 步骤5：处理当前卷积层的权重
                # 处理当前卷积层的权重
                # 获取权重张量（op.opds[1]通常是卷积核权重的名称）
                weight_first = weight_file[op.opds[1]]
                # 获取MLIR中记录的权重形状（确保与实际加载的权重形状一致）
                weight_first_shape = module.get_tensor(op.opds[1]).shape
                if weight_first.shape != weight_first_shape:
                    weight_first = weight_first.reshape(weight_first_shape)
                # 深拷贝当前权重用于修改（避免直接修改原始数据）
                new_weight_first = copy.deepcopy(weight_first)
                
                # 步骤6：处理当前卷积层的偏置（如果存在）
                if len(op.opds) > 2:  # op.opds[2]通常是偏置的名称
                    bias_first = weight_file[op.opds[2]]
                    new_bias_first = copy.deepcopy(bias_first)
                
                # 步骤7：处理后继卷积层的权重
                next_op_name = succ[0]
                next_op = self.parser.get_op_by_op_name(next_op_name)
                # 获取后继层权重张量（next_op.opds[1]为后继卷积核权重名称）
                weight_second = weight_file[next_op.opds[1]]
                # 确保权重形状与MLIR中记录一致
                weight_second_shape = module.get_tensor(next_op.opds[1]).shape
                if weight_second.shape != weight_second_shape:
                    weight_second = weight_second.reshape(weight_second_shape)
                new_weight_second = copy.deepcopy(weight_second)
                
                #  步骤8：计算分组数（适用于分组卷积，按输出通道分组）
                # 分组数 = 当前层输出通道数 // 后继层输入通道数
                num_group = weight_first.shape[0] // weight_second.shape[1]
                self.logger.print_info(
                    f'Cross Layer WE: {op.name} --- {next_op.name} Groups: {num_group} Iter: {iter}'
                )
                
                # 计算每组的输入/输出通道数
                group_channels_i = weight_first.shape[0] // num_group  # 每组输入通道数
                group_channels_o = weight_second.shape[0] // num_group  # 每组输出通道数
                
                # 步骤9：按组调整权重
                for g in range(num_group):
                    # 当前卷积层：第g组的输入通道范围
                    c_start_i = g * group_channels_i
                    c_end_i = (g + 1) * group_channels_i
                    weight_first_group = weight_first[c_start_i:c_end_i]  # 第g组的权重切片
                    
                    # 后继卷积层：第g组的输出通道范围
                    c_start_o = g * group_channels_o
                    c_end_o = (g + 1) * group_channels_o
                    weight_second_group = weight_second[c_start_o:c_end_o]  # 第g组的权重切片
                    
                    # 遍历当前组内的每个输入通道（与后继层的输入通道对应）
                    for ii in range(weight_second_group.shape[1]):
                        # 步骤10：计算当前层权重的动态范围（绝对值最大值）
                        range_1 = np.abs(weight_first_group)[ii].max()
                        # 计算后继层权重的动态范围（绝对值最大值）
                        range_2 = np.abs(weight_second_group)[:, ii].max()
                        
                        # 处理接近零的极端情况（避免后续除零）
                        if range_1 < 1e-6:
                            range_1 = 0.
                        if range_2 < 1e-6:
                            range_2 = 0.
                        
                        # 步骤11：计算缩放因子s：使两层的动态范围乘积保持不变，同时平衡各自范围
                        # s = range_1 / sqrt(range_1 * range_2) = sqrt(range_1 / range_2)
                        s = range_1 / np.sqrt(range_1 * range_2)
                        # 处理无穷大或NaN（如range_1和range_2均为0时）
                        if np.isinf(s) or np.isnan(s):
                            s = 1.0
                        # 步骤12：调整当前层和后继层的权重（缩放补偿）
                        # 调整当前层权重：除以s（缩小/放大范围）
                        new_weight_first[c_start_i + ii] /= s
                        # 调整后继层权重：乘以s（补偿当前层的缩放，保持整体输出不变）
                        new_weight_second[c_start_o:c_end_o, ii] *= s
                        # 调整当前层偏置（若存在）：同步除以s，保持偏置与权重的一致性
                        if len(op.opds) > 2:
                            new_bias_first[c_start_i + ii] /= s
                # 步骤13：检查权重调整是否收敛（权重变化量小于阈值）
                # 检查权重调整是否收敛（权重变化量小于阈值）
                if self.converged([weight_first, weight_second], [new_weight_first, new_weight_second]):
                    break  # 收敛则停止迭代
                # 步骤14：未收敛，更新权重并继续迭代
                # 未收敛，更新迭代次数并保存调整后的权重
                iter += 1
                weight_file_dict[op.opds[1]] = new_weight_first  # 更新当前层权重
                weight_file_dict[next_op.opds[1]] = new_weight_second  # 更新后继层权重
                if len(op.opds) > 2:
                    weight_file_dict[op.opds[2]] = new_bias_first  # 更新当前层偏置
                # 删除旧权重文件并保存新权重
                os.system(f'rm -f {weight_file_name}')
                np.savez(weight_file_name, **weight_file_dict)
                weight_file_dict.clear()  # 清空字典准备下一轮迭代
```

## 13.mlir_parser.py

### 一、整体定位

本文件是 TPU-MLIR 工具链中 MLIR 文件解析的核心模块，主要用于解析 MLIR（Multi-Level Intermediate Representation）模型文件，并将其结构化为 Python 对象，便于后续的模型分析、量化、校准、推理等自动化处理。核心思想：

- 通过 mlir.ir 和 mlir.dialects 提供的 Python API，读取和解析 MLIR 文件，提取出模型的算子（Operation）、输入输出、权重、连接关系、属性等信息。
- 封装为 Operation 和 MlirParser 两个主要类，提供丰富的接口，便于其他模块调用和分析。其中 Operation 类的作用是封装单个 MLIR 算子的所有关键信息，包括名称、类型、位置、形状、输入输出、属性等；MlirParser 类封装整个 MLIR 模型的解析过程，负责读取文件、构建算子列表、提取输入输出、权重、连接关系等，提供丰富的接口，便于按名称、类型、连接关系等方式查询算子。

### 二、 Operation 类

#### init(self, op, body, idx)

功能：初始化 `Operation` 对象，从 MLIR 原始操作中提取核心属性并存储。
原理：

- 接收三个参数：`op`（MLIR 原始操作对象）、`body`（操作所在的代码块）、`idx`（当前操作在代码块中的索引）。
- 通过调用类的静态方法（如 `name()`、`type()` 等），提取操作的名称、类型、位置、形状、操作数、属性等信息，分别赋值给实例属性（`self.name`、`self.type` 等）。
- 最终将原始操作对象 `op` 也存储在实例中（`self.op`），方便后续直接访问原始信息。

#### str(self)

功能：定义 `Operation` 对象的字符串表示形式，方便打印和查看核心信息。
原理：将实例的 `name`（名称）、`type`（类型）、`loc`（位置）、`shape`（形状）、`opds`（操作数）拼接为逗号分隔的字符串返回。

#### 提取操作名称与输出信息

##### `name(op)`

功能：从操作的位置信息中提取操作的名称（通常是用户定义的变量名或融合操作名）。

原理：

- 操作的位置信息存储在 `op.location` 中，先判断位置是否为 `loc(unknown)`（未知位置），若是则返回 `None`。
- 若操作有多个结果（`num_results > 1`），优先通过正则表达式 `r'(fused\[.+?\])'` 从位置信息中提取 “融合操作名”（如 `fused[conv2d+relu]`）。
- 若只有单个结果，通过正则表达式 `r'\"(.+?)\"'` 提取位置信息中引号包裹的字符串（通常是变量名，如 `"conv_output"`）。

##### `outputs(op)`

功能：提取操作的输出名称列表（操作结果对应的标识）。
原理：与 `name(op)` 类似，从 `op.location` 中通过正则表达式 `r"\"(.+?)\""` 提取所有引号包裹的字符串，作为输出名称列表（一个操作可能有多个输出）。

#### 提取操作类型

##### `type(op)`

功能：获取操作的运算类型（如卷积、池化、激活等具体操作类型）。
原理：直接返回 `op.operation.name`，这是 MLIR 中操作的内置名称（如 `"tpu.conv2d"`、`"top.relu"` 等）。

#### MLIR 属性与 Python 类型的转换

这些方法用于将 MLIR 中特定类型的属性（如字符串、布尔值、数组等）转换为 Python 原生类型，方便后续处理。

##### `str(value)`

功能：将 MLIR 的 `StringAttr` 属性转换为 Python 字符串。
原理：通过 `mlir.ir.StringAttr(value).value` 提取字符串值。

##### `bool(value)`

功能：将 MLIR 的 `BoolAttr` 属性转换为 Python 布尔值。
原理：通过 `mlir.ir.BoolAttr(value).value` 提取布尔值。

##### `int(value)`

功能：将 MLIR 的 `IntegerAttr` 属性转换为 Python 整数。
原理：通过 `mlir.ir.IntegerAttr(value).value` 提取整数值。

##### `int_array(value)`

功能：将 MLIR 的 `ArrayAttr`（元素为整数）转换为 Python 整数列表。
原理：遍历 `mlir.ir.ArrayAttr(value)` 中的每个元素，通过 `mlir.ir.IntegerAttr(x).value` 逐个转换为整数，组成列表返回。

##### `fp_array(value)`

功能：将 MLIR 的 `ArrayAttr`（元素为浮点数）转换为 Python 浮点数列表。
原理：遍历 `mlir.ir.ArrayAttr(value)` 中的每个元素，通过 `mlir.ir.FloatAttr(x).value` 逐个转换为浮点数，组成列表返回。

#### 提取与扩展操作的属性

##### `attrs(op)`

功能：收集操作的所有属性，以 “属性名 - 属性值” 字典形式返回。
原理：遍历 `op.attributes`（操作的所有属性），将每个属性的名称（`attr.name`）和值（`attr.attr`）转换为字符串后存入字典。

##### `append_attr(op, attrs)`

功能：为属性字典补充量化相关的额外属性（针对量化模型）。

原理：

- 仅处理只有一个结果的操作（`len(op.results) == 1`），因为量化属性通常与单个张量结果关联。
- 解析结果的元素类型（`shape_type.element_type`），若为 `UniformQuantizedType`（均匀量化类型），则提取 `scale`（缩放因子）和 `zero_point`（零点），添加到 `attrs` 中。
- 若为 `CalibratedQuantizedType`（校准量化类型），则提取 `min`（最小值）和 `max`（最大值），添加到 `attrs` 中。

##### `dictattr(op, field_name)`

功能：获取操作中指定字段名的字典类型属性（MLIR 的 `DictAttr`）。
原理：直接返回 `mlir.ir.DictAttr(op.attributes[field_name])`，用于处理结构化的属性（如包含多个键值对的属性）。

#### 提取操作的位置与形状

##### `loc(op)`

功能：提取操作在 MLIR 中的位置标识符（通常是 `%` 开头的编号，如 `%12`）。
原理：通过 `op.get_asm()` 获取操作的汇编字符串表示，按 `=` 分割后取前半部分，去除 `%` 和空格，得到位置标识符。

##### `shape(op)`

功能：获取操作结果的张量形状（维度大小列表，如 `[2, 3, 224, 224]`）。
原理：

- 遍历操作的所有结果（`op.results`），忽略类型为 `"none"` 的无效结果。
- 对有效结果，通过 `mlir.ir.ShapedType` 解析其形状类型，若有明确的秩（`has_rank`），则提取每个维度的大小（`get_dim_size(i)`），组成形状列表；否则返回空列表。

#### 提取操作的操作数（输入依赖）

操作数（operands）是当前操作依赖的前置操作的结果，这两个方法用于获取这些前置操作的名称。

##### `operands(op, body, idx)`

功能：通过反向查找获取当前操作依赖的前置操作名称列表（未使用缓存的基础版本）。

原理：

- 遍历当前操作的所有操作数（`op.operands`）。
- 对每个操作数，反向遍历代码块（`body`）中索引小于当前操作索引（`idx`）的前置操作（`prev_op`），检查前置操作的第一个结果是否与当前操作数匹配。
- 若匹配且前置操作类型不在过滤列表（`tpu.None`、`top.None` 等无效操作）中，则将其名称加入操作数列表。

##### `operands_v2(op, body, idx)`

功能：利用 `cache_map` 缓存快速获取当前操作依赖的前置操作名称列表（优化版本）。
原理：

- 遍历当前操作的所有操作数（`op.operands`），检查该操作数是否在 `cache_map` 中（即是否有前置操作产生该操作数）。
- 若存在缓存，遍历缓存中 `[前置操作索引, 前置操作名称]` 的列表，仅保留索引小于当前操作索引（`i < idx`）的前置操作名称，加入操作数列表。
- 相比 `operands` 方法，通过缓存避免了重复的反向查找，效率更高。

### 三、MlirParser 类

#### 1. init(self, mlir_file)

功能：初始化解析器，加载并解析 MLIR 文件，提取模块信息、操作列表、输入输出等核心数据。

原理：

1. 加载与解析 MLIR 文件：

   - 读取 `mlir_file` 内容，通过 `mlir.ir.Module.parse` 解析为 MLIR 模块对象（`self.module`），并配置上下文（`self.ctx`）允许未注册的方言（dialects）。
2. 提取模块结构：

   - 从模块中提取核心代码块（`self.body`），通常是模块中第一个操作的第一个区域的第一个块（MLIR 的层级结构：`module -> operation -> region -> block`）。
3. 提取模块属性：

   - 通过 `Operation.attrs` 获取模块级属性（如 `sym_name`、`module.state` 等），并通过 `eval` 转换为 Python 对象，存储为 `self.module_name`、`self.module_state` 等。
4. 构建操作数缓存（`cache_map`）：

   - 遍历代码块中的所有操作，对非过滤类型（如 `tpu.None` 等）且有结果的操作，记录其结果（`prev_op.results`）与操作输出名称的映射关系，并按结果对象缓存（`cache_map[r] = [索引, 输出名称]`）。
   - 缓存结果赋值给 `Operation.cache_map`，供 `Operation.operands_v2` 方法快速查找操作数依赖。
5. 收集操作列表（`self.ops`）：

   - 遍历代码块中的操作，过滤掉不需要的类型（`top.None`、`func.return` 等），将其余操作转换为 `Operation` 对象并存入 `self.ops`。
   - 单独收集输入操作（`type == "top.Input"`）到 `self.inputs`，记录返回操作（`func.return`）到 `self.return_op`。

#### 2.基础信息查询方法

##### `get_op_name_list(self)`

功能：获取所有操作的名称列表。
原理：遍历 `self.ops`，提取每个 `Operation` 对象的 `name` 属性，返回列表。

##### `get_op_output_name_list(self)`

功能：获取所有操作的输出名称列表（扁平化）。
原理：通过 `itertools.chain` 将每个操作的 `outputs` 列表（可能包含多个输出）拼接为一个扁平列表。

##### `get_input_num(self)`

功能：获取输入操作的数量。
原理：返回 `self.inputs` 列表的长度（`self.inputs` 中仅包含 `type == "top.Input"` 的操作）。

##### `get_input_shapes(self)`

功能：获取所有输入操作的张量形状。
原理：对每个输入操作，通过 `mlir.ir.ShapedType` 解析其输入类型的形状（`shape_type.shape`），返回形状列表。

##### `get_input_types(self)`

功能：获取所有输入操作的元素类型（如 `f32`、`i32` 等）。
原理：对每个输入操作，通过 `mlir.ir.ShapedType` 提取元素类型（`shape_type.element_type`），转换为字符串后返回列表。

##### `get_input_op_by_idx(self, idx)`

功能：根据索引获取对应的输入操作（原始 MLIR 操作对象）。
原理：返回 `self.inputs[idx].op`（`op` 是 `Operation` 对象中存储的原始 MLIR 操作）。

##### `get_batch_size(self)`

功能：获取输入的批次大小（batch size）。
原理：默认取第一个输入操作的形状的第一个维度（假设输入形状的第一个维度为批次大小），通过 `Operation.shape` 提取。

#### 3.操作依赖关系查询方法

##### `get_pre_op_by_op_name(self, op_name)`

功能：获取指定操作的直接前置操作（即该操作依赖的输入操作）的名称列表。

原理：

- 找到名称为 `op_name` 的操作，提取其 `opds`（操作数列表，即依赖的前置操作名称）；
- 过滤出在所有操作输出名称列表中的前置操作（确保是有效操作），返回结果。

##### `get_next_op_by_op_name(self, op_name)`

功能：获取直接依赖指定操作的后续操作的名称列表。
原理：遍历所有操作，检查哪些操作的 `opds`（操作数）包含 `op_name`，收集这些操作的名称并返回。

##### `get_all_pre_ops_by_op_name(self, op_name)`

功能：递归获取指定操作的所有前置操作（包括间接依赖）的名称列表（闭包）。

原理：

- 初始包含 `op_name` 及其直接前置操作；
- 递归遍历每个前置操作的前置操作，直到没有新的前置操作加入，最终返回所有相关前置操作的集合。

##### `get_all_next_ops_by_op_name(self, op_name)`

功能：递归获取依赖指定操作的所有后续操作（包括间接依赖）的名称列表（闭包）。
原理：类似 `get_all_pre_ops_by_op_name`，但方向相反，递归收集所有后续操作直到无新操作加入。

##### `get_block_ops_by_op_name(self, name_list1, name_list2)`

功能：获取由两个操作列表共同关联的所有操作（形成一个 “块”）。

原理：

- 对 `name_list2` 中的所有操作，获取其所有前置操作；
- 对 `name_list1` 中的所有操作，获取其所有后续操作；
- 取两者的并集，即为关联的所有操作（块）。

#### 4.操作使用与属性查询方法

##### `get_user_count_by_op_name(self, op_name)`

功能：统计有多少个不同的操作依赖指定操作（去重计数）。
原理：遍历所有操作，统计 `opds` 中包含 `op_name` 的操作的数量（每个操作只计一次）。

##### `get_use_count_by_op_name(self, op_name)`

功能：统计指定操作被依赖的总次数（包括同一操作的多次依赖）。
原理：遍历所有操作，累计 `opds` 列表中 `op_name` 出现的总次数。

##### `get_outputs_by_op_name(self, op_name)`

功能：获取指定操作的输出名称列表。
原理：查找名称为 `op_name` 的 `Operation` 对象，返回其 `outputs` 属性。

##### `get_op_by_op_name(self, op_name)`

功能：获取指定名称对应的 `Operation` 对象。
原理：遍历 `self.ops`，返回 `name` 匹配的 `Operation` 对象（未找到则返回 `None`）。

##### `get_opds_by_op_name(self, op_name)`

功能：获取指定操作的操作数（依赖的前置操作名称）列表。
原理：查找名称为 `op_name` 的 `Operation` 对象，返回其 `opds` 属性。

##### `get_op_type_by_op_name(self, op_name)`

功能：获取指定操作的类型（如卷积、池化等）。
原理：查找名称为 `op_name` 的 `Operation` 对象，返回其 `type` 属性。

#### 5.输出与中间操作信息查询方法

##### `get_output_op_names_n_shapes(self)`

功能：获取模块输出操作的名称及其对应的张量形状（字典形式）。

原理：

- 以 `self.return_op`（返回操作）为起点，遍历其操作数（`return_op.operands`）；
- 对每个操作数，找到产生该操作数的原始操作，提取其输出名称和形状（通过 `mlir.ir.ShapedType` 解析），存入字典返回。

##### `get_middle_op_names_n_shape_type(self)`

功能：获取中间操作（非输入、非返回、非 None 类型）的名称及其形状类型。

原理：

- 遍历所有操作，过滤掉 `top.None`、`top.Input`、`func.return` 等类型；
- 对剩余操作，解析其结果的形状类型（`mlir.ir.ShapedType`），以 “名称 - 形状类型” 字典形式返回。

##### `get_initializer_op_names_n_shape_type(self)`

功能：获取初始化操作（通常是权重，`type == "top.Weight"`）的名称及其形状类型。
原理：遍历所有操作，筛选出 `type == "top.Weight"` 的操作，解析其结果的形状类型，以字典形式返回。

##### `collect_op_name_dict(self)`

功能：构建 “操作名称 - Operation 对象” 的映射字典，方便快速查询。
原理：遍历 `self.ops`，以操作名称为键、`Operation` 对象为值，构建并返回字典。

## 14.kld_calibrator.py

### 一、整体定位

#### 1. 整体功能

本文件是 TPU-MLIR 量化校准流程中的关键模块，主要负责：

- 统计网络各层激活值的分布（如直方图、最大值、均值等）
- 计算各层的量化阈值（threshold），支持多种方法（KL 散度、最大值、均方误差、分位数等）
- 支持自动调优（tune）阈值以提升量化精度
- 生成和管理量化校准表（calibration table）
- 支持多种量化模式（int8、int4、asymmetric、fp8 等）

#### 2. 原理

- 通过前向推理收集每一层的激活数据，统计其分布特征
- 根据不同的量化策略，计算最优的量化阈值
- 可选地对阈值进行微调（tune），以最小化量化误差
- 最终将阈值写入校准表，供后续量化模型使用

### 二、重点函数解析

#### 1.import_quant_bias

```python
def import_quant_bias(value, threshold):"""
    对输入数值进行8位量化-反量化处理，将数值约束在8位有符号整数精度范围内，
    同时尽可能保留原始数值特征（常用于模型量化场景，平衡精度与存储/计算成本）
    参数:
        value: 输入的原始数值（可为单个数值或numpy数组）
        threshold: 原始数值的阈值（通常为原始数据的最大绝对值，用于确定量化范围）
    返回:
        经过量化-反量化处理后的数值（精度被约束在8位有符号整数范围内）
    """# 1. 计算缩放因子：将原始数据范围映射到8位有符号整数的最大值范围（0~127）# 8位有符号整数的范围是-128~127，这里用127作为正向最大值的基准
    scale = 127 / threshold
    
    # 2. 量化操作：将原始数值映射到8位整数范围# 先通过缩放因子放大原始数值，再四舍五入为整数（模拟量化中的整数化）
    value = np.round(value * scale)# 3. 截断操作：确保量化后的数值严格落在8位有符号整数范围内# 超过最大值127的数值强制设为127（上边界截断）
    value[value > 127] = 127# 低于最小值-128的数值强制设为-128（下边界截断）
    value[value < -128] = -128# 4. 反量化操作：将量化后的整数映射回原始数值范围# 通过除以缩放因子，恢复到与原始数据同量级的范围（便于后续计算使用）
    value /= scale
    
    # 返回处理后的结果return value
```

#### 2.find_better_threshold()

功能：为特定操作（`tuned_op`）寻找更优的阈值（threshold），通过迭代测试不同阈值下的误差（距离）和相似度，最终确定使误差最小的阈值，并更新阈值记录表。整体逻辑围绕 “范围搜索 → 误差计算 → 最优选择 → 结果记录” 展开，常用于需要通过阈值控制精度的场景（如模型量化、特征筛选等）。

```python
def find_better_threshold(self, evaled_op, tuned_op, node_label):
    """
    寻找更优的阈值（threshold），通过迭代测试不同阈值，计算距离和余弦相似度，
    找到使距离最小（或相似度最高）的阈值，并更新阈值记录表
    
    参数:
        evaled_op: 待评估的操作名称
        tuned_op: 待调优阈值的操作名称
        node_label: 用于记录调优结果的标签列表
    返回:
        bool: 调优过程是否成功完成
    """
    # 初始化之前的距离为-1（作为初始状态标记）
    prev_distance = -1
    # 从初始阈值表中获取tuned_op的当前阈值
    threshold = self.initial_threshold[tuned_op][0]
    # 获取tuned_op的最大绝对值（用于限制阈值范围）
    abs_max = self.abs_max_dict[tuned_op]
    
    # 获取tuned_op在操作列表中的索引（用于调试）
    if tuned_op in self.parser.get_op_name_list():
        op_no = self.parser.get_op_name_list().index(tuned_op)
    else:
        op_no = 0  # 不在列表中时默认索引为0
    
    # 打印调试信息：调优操作的索引、名称、当前阈值、最大绝对值等
    self.print_dbg('>>>tuned_op_idx:', op_no, ', tuned_op:', tuned_op, ', threshold:',
                   threshold, 'abs_max:', abs_max, ', evaled_op:', evaled_op)
    
    # 如果当前阈值大于最大绝对值，提示无需调优（阈值已超出合理范围）
    if threshold > abs_max:
        self.print_dbg('waring, threshold > abs_max, do not tune the threshold')
    
    # 初始化阈值最小值为当前阈值
    th_min = threshold
    # 若调试命令中包含"find_lower_th"，则将阈值最小值设为当前阈值的1/3（扩大搜索范围）
    if 'find_lower_th' in self.debug_cmd:
        th_min = threshold / 3
    
    # 初始化最佳阈值为当前阈值，最佳余弦相似度为0
    best_threshold = threshold
    best_cos_sim = 0
    # 初始化初始余弦相似度为0
    init_cos_sim = 0
    
    # 获取待评估操作的前置操作列表（可能用于生成输入张量）
    pre_ops = self.parser.get_pre_op_by_op_name(evaled_op)
    
    # 根据调优次数生成输入张量（若调试命令指定不使用FP32张量作为参考）
    for idx in range(self.args.tune_num):
        if 'not_use_fp32_tensor_as_ref' in self.debug_cmd:
            # 为每个前置操作生成输入张量，失败则返回False
            for pre_op in pre_ops:
                if not self.gen_input_tensor(idx, pre_op, node_label):
                    return False
    
    # 调优的最小差值（用于判断是否需要调整步长）
    min_tuned_diff = 0.01
    # 若调试命令中指定了min_tuned_diff，则使用该值
    if 'min_tuned_diff' in self.debug_cmd:
        min_tuned_diff = self.debug_cmd['min_tuned_diff']
    
    # 计算阈值范围的总差值（最大绝对值与最小阈值的差）
    diff = abs(abs_max - th_min)
    # 计算步长：总差值除以调优步数（平均分配搜索区间）
    step = (abs_max - th_min) / self.tune_steps
    # 初始化迭代范围（从0到调优步数，包含两端）
    ranges = range(self.tune_steps + 1)
    
    # 若步长为正且总差值大于最小调优差值（说明有调整空间）
    if step > 0 and diff > min_tuned_diff:
        # 迭代轮数，默认1轮
        r = 1
        # 若开启寻找更低阈值，迭代轮数设为2（扩大搜索范围）
        if 'find_lower_th' in self.debug_cmd :
            r = 2
        
        # 多轮迭代搜索最佳阈值
        for n in range(r):
            # 第二轮迭代（针对寻找更低阈值的场景）
            if n == 1 and 'find_lower_th' in self.debug_cmd:
                # 以当前最佳阈值为中心，设置新的搜索范围（±step）
                th_min = best_threshold - step
                th_max = best_threshold + step
                # 重新计算差值和步长
                diff = abs(th_max - th_min)
                times = self.tune_steps // 2  # 减少步数为原来的一半
                step = (th_max - th_min) / times
                # 迭代范围排除两端（只搜索中间区间）
                ranges = range(times + 1)[1:-1]
            
            # 遍历当前范围内的每个阈值点
            for i in ranges:
                # 计算当前阈值（起始阈值 + 步长×索引）
                cur_threshold = th_min + step * i
                # 计算当前阈值下的距离和余弦相似度
                cur_distance, cur_cos_sim = self.calc_distance(evaled_op, cur_threshold)
                
                # 若计算失败，返回False
                if cur_distance is None and cur_cos_sim is None:
                    return False
                
                # 处理初始状态（第一次计算）
                if prev_distance == -1:
                    # 打印初始调试信息
                    self.print_dbg("### tuning i:{}, tuned_op_idx:{}, tuned_op:{}, threshold:"
                                   "{:5f}, distance: {}".format(i, op_no, tuned_op,
                                                                cur_threshold, cur_distance))
                    # 更新之前的距离和初始余弦相似度
                    prev_distance = cur_distance
                    init_cos_sim = cur_cos_sim
                    continue
                # 若当前距离小于之前的距离（说明当前阈值更优）
                elif cur_distance < prev_distance:
                    # 更新最佳状态：距离、阈值、余弦相似度
                    prev_distance = cur_distance
                    best_threshold = cur_threshold
                    best_cos_sim = cur_cos_sim
    
    # 清除生成的输入张量（若调试命令指定）
    for idx in range(self.args.tune_num):
        if 'not_use_fp32_tensor_as_ref' in self.debug_cmd:
            self.clear_input_tensor(idx, tuned_op, node_label)
    
    # 根据调优结果更新节点标签（记录阈值变化）
    if step > 0:
        # 若找到更优阈值
        if threshold != best_threshold:
            node_label[0] += '\ntune, find:{} th:{:.4f}->{:.4f}, abs_max:{:.4f}, cos_sim:{:.3f}->{:.3f}'.format(
                tuned_op, threshold, best_threshold, abs_max, init_cos_sim, best_cos_sim)
        # 未找到更优阈值
        else:
            node_label[0] += '\ntune:{} no better th, init th:{}, cos_sim:{:.3f}->{:.3f}'.format(
                tuned_op, threshold, init_cos_sim, best_cos_sim)
    # 无调整空间（步长为0）
    else:
        node_label[0] += '\nnot tune {}, init th:{}'.format(tuned_op, threshold)
    
    # 若当前最佳阈值大于历史最佳阈值，则更新阈值表
    if best_threshold > self.threshold_table.thresholds_map[tuned_op][0]:
        node_label[0] += '\nupdate {} th: {}>{}'.format(
            tuned_op, self.threshold_table.thresholds_map[tuned_op][0], best_threshold)
        self.threshold_table.thresholds_map[tuned_op][0] = best_threshold
    
    # 调优成功完成
    return True
```

#### 3.run

功能：遍历模型中的所有操作（op），按依赖关系逐步优化每个操作的阈值（threshold），最终生成调优后的阈值字典。

原理：

1. 按操作的依赖关系（前置操作 → 当前操作）顺序处理所有操作；
2. 为每个操作生成参考数据（原始精度的张量）；
3. 调用阈值调优逻辑（`find_better_threshold`）为每个操作（或其前置操作）寻找最优阈值；
4. 记录调优过程和结果，支持可视化（生成操作依赖图和余弦相似度图表）；
5. 最终返回所有操作调优后的阈值字典，用于后续模型部署或量化。

```python
def run(self):
    """
    自动调优所有操作（op）的阈值主函数，通过遍历所有操作、处理前置依赖、生成参考数据、
    调用阈值调优函数，最终生成并返回调优后的阈值字典，同时支持可视化调优结果（dot图）。
    """
    # 初始化字典，用于存储各层的余弦相似度（key:层名，value:累计相似度）
    self.layer_cos_sim = {}
    # 获取所有操作（op）的名称列表（待调优的操作集合）
    all_tensors = self.parser.get_op_name_list()
    # 深拷贝初始阈值表，作为调优的基准（避免直接修改原始阈值表）
    self.initial_threshold = copy.deepcopy(self.threshold_table.thresholds_map)
    # 创建进度条，显示调优进度（遍历所有操作）
    pbar = tqdm(all_tensors, total=len(all_tensors), position=0, leave=True)
    
    # 遍历所有操作，逐个进行调优处理
    for i, evaled_op in enumerate(all_tensors):
        # 更新进度条描述，显示当前调优的操作名称
        pbar.set_description("tune op: {}".format(evaled_op))
        pbar.update(1)  # 进度条前进1步
        
        # 获取当前操作（evaled_op）的类型（如卷积、激活等）
        type = self.parser.get_op_type_by_op_name(evaled_op)
        if type is None:  # 若无法获取类型，跳过该操作
            continue
        
        # 初始化节点标签，记录操作的索引、名称和类型（用于可视化和日志）
        node_label = ['idx:{}  name:{}  type:{}\n'.format(i, evaled_op, type.split('.')[-1])]
        
        # 若当前操作是输入层（top.Input），无需调优，仅在dot图中创建节点后继续
        if type == 'top.Input':
            if self.dot is not None:  # 若启用可视化（dot图）
                self.dot.node(evaled_op, node_label[0], shape='box')  # 创建输入节点
            continue
        
        # 获取当前操作（evaled_op）的所有前置操作（输入依赖）
        pre_ops = self.parser.get_pre_op_by_op_name(evaled_op)
        # 若启用可视化，为当前操作与前置操作之间添加边（表示依赖关系）
        if self.dot is not None:
            for pre_op in pre_ops:
                evaled_op = split_fuseop(evaled_op)  # 拆分融合操作（若有）
                self.dot.edge(pre_op, evaled_op, label=pre_op)  # 添加边，标签为前置操作名
        
        # 为当前操作生成参考张量（用于后续计算误差和相似度，调优次数由tune_num指定）
        for idx in range(self.args.tune_num):
            evaled_op = split_fuseop(evaled_op)  # 拆分融合操作
            self.gen_ref_tensor(idx, evaled_op, node_label)  # 生成第idx次调优的参考张量
        
        # 检查当前操作的所有输入（前置操作）是否已调优
        if self.isAllInputTuned(evaled_op):
            # 若所有输入已调优，任选第一个前置操作进行调优（简化逻辑）
            ret = self.find_better_threshold(evaled_op, pre_ops[0], node_label)
            if not ret:  # 调优失败，中断循环
                break
            # 将调优后的前置操作加入已调优列表
            self.tuned_op_list.append(pre_ops[0])
            # 若启用可视化，更新当前操作的节点信息
            if self.dot is not None:
                self.dot.node(evaled_op, node_label[0], shape='box')
            # 清理当前操作的参考张量（释放资源）
            for idx in range(self.args.tune_num):
                self.clear_ref_tensor(idx, evaled_op, node_label)
            continue  # 继续处理下一个操作
        
        # 若输入未全部调优，处理未调优的前置操作
        faild = False  # 标记调优是否失败
        for tuned_op in pre_ops:
            # 仅调优未处理过的前置操作
            if tuned_op not in self.tuned_op_list:
                # 为当前前置操作寻找更优阈值
                ret = self.find_better_threshold(evaled_op, tuned_op, node_label)
                if not ret:  # 调优失败，标记并中断循环
                    faild = True
                    break
                # 将调优后的操作加入已调优列表
                self.tuned_op_list.append(tuned_op)
        if faild:  # 若调优失败，整体中断
            break
        
        # 清理当前操作的参考张量（释放资源）
        for idx in range(self.args.tune_num):
            self.clear_ref_tensor(idx, evaled_op, node_label)
        
        # 打印调试信息：缓存的张量信息（用于检查资源占用和引用计数）
        self.print_dbg('>>>>buffered_tensors info:')
        self.print_dbg('dq_activations keys:', list(self.dq_activations[0].keys()))
        for op_name in self.dq_activations[0]:
            self.print_dbg('op:', op_name, ' refcount:', self.dq_activations[0][op_name][1])
        self.print_dbg('ref_activations keys:', list(self.ref_activations[0].keys()))
        for op_name in self.ref_activations[0]:
            self.print_dbg('op:', op_name, ' refcount:', self.ref_activations[0][op_name][1])
        
        # 若启用可视化，更新当前操作的节点信息
        if self.dot is not None:
            self.dot.node(evaled_op, node_label[0], shape='box')
    
    # 关闭进度条
    pbar.close()
    # 打印调优结束信息和总运行时间
    print('auto tune end, run time:{}'.format(time.time() - self.start_time))
    
    # 若启用调试信息打印，保存各层的余弦相似度可视化结果（子图）
    if 'print_debug_info' in self.debug_cmd:
        index_list = []  # 层索引列表（用于绘图x轴）
        cos_tensor = []  # 余弦相似度列表（用于绘图y轴）
        for i, layer in enumerate(self.layer_cos_sim):
            index_list.append('{}_{}'.format(i, layer))  # 层标识（索引+层名）
            cos_tensor.append(self.layer_cos_sim[layer] / self.args.tune_num)  # 平均相似度
        index_list = np.array(index_list)
        cos_tensor = np.array(cos_tensor)
        # 保存余弦相似度的子图
        save_tensor_subplot(cos_tensor, index_list, 'layer_cos_sim', 'layer_cos_sim')
    
    # 若启用可视化，生成并保存dot图结果（svg格式）
    if self.dot is not None:
        self.dot.render(filename='auto_tune_result', directory='./', view=False)  # 渲染dot图
        os.system('dot -Tsvg ./auto_tune_result -o ./auto_tune_result.svg')  # 转换为svg格式
    
    # 构建调优后的阈值字典（key:操作名，value:调优后的阈值）
    tuned_th_dict = {
        i: self.threshold_table.thresholds_map[i][0]
        for i in self.threshold_table.thresholds_map
    }
    return tuned_th_dict  # 返回调优后的阈值字典
```

## 15.cache_tool.py

### 一、整体定位

功能：这个文件主要实现了两种缓存机制，用于跟踪文件状态、记录操作信息并基于文件变化判断是否需要重复执行验证或处理步骤，核心目标是通过缓存减少不必要的重复工作，提高效率。

整体逻辑流程：

1. 缓存建立：通过 `CacheTool` 和 `CommandRecorder` 分别记录验证状态、文件哈希、修改时间、执行命令等信息，并持久化到 JSON 文件。
2. 变化检测：在执行验证或处理步骤前，通过哈希值（`CacheTool`）或最后修改时间（`CommandRecorder`）判断文件是否变化。
3. 缓存复用：若文件未变化且历史状态有效（如验证成功），则跳过重复操作；否则更新缓存并执行新操作。

### 二、CacheTool

`CacheTool` 是一个用于管理模型验证流程的缓存工具，核心目标是 避免重复执行未发生变化的验证步骤，从而提高模型转换、验证流程的效率。具体功能包括：

1. 记录 `top/mlir`、`tpu/mlir`、`BModel` 三种文件的验证状态（是否成功）。
2. 基于文件内容哈希值判断文件是否修改（确保只有文件变化时才重新验证）。
3. 结合验证标准（相似度阈值）、调试模式等条件，判断是否可跳过验证。
4. 通过 JSON 文件（`build_flag.json`）持久化缓存状态，支持跨进程 / 跨会话复用。

```python
def BModel2Hash(bmodel_file):
    """
    生成BModel模型文件的唯一哈希值，用于标识模型关键信息是否变化
    
    哈希计算基于模型的核心属性（芯片类型、版本、二进制数据等）和MLIR转换结果，
    确保模型关键信息不变时哈希值一致。
    """
    try:
        # 解析BModel文件获取核心信息
        bmodel = dis.BModel(bmodel_file)
        # 收集模型关键属性（用于哈希计算的基础数据）
        obj = [
            bmodel.chip,          # 芯片类型
            bmodel.version,       # 模型版本
            bmodel.type,          # 模型类型
            bmodel.neuron_size,   # 神经元大小
            bmodel.device_num,    # 设备数量
            bmodel.core_num,      # 核心数量
        ]
        # 加入模型二进制数据和MLIR转换结果（确保内容级别的唯一性）
        obj.append(bytes(bmodel.binary))
        obj.append(str(BModel2MLIR(bmodel_file)))  # 转换为MLIR字符串
        
        # 序列化后计算MD5哈希
        md = hashlib.md5()
        md.update(pickle.dumps(obj))
        return md.hexdigest()
    except Exception:
        # 解析失败时，用当前时间生成哈希（降级方案）
        return hashlib.md5(str(datetime.datetime.now()).encode()).hexdigest()


class CacheTool:
    """
    缓存工具类，用于管理模型验证过程的缓存状态，判断是否可跳过重复验证
    
    核心功能：通过记录文件哈希、验证状态、相似度等信息，在满足条件时跳过重复的
    top/mlir、tpu/mlir、BModel模型验证步骤，提高流程效率。
    """
    def __init__(self, cache_skip: bool) -> None:
        """
        初始化缓存工具
        
        参数：
            cache_skip: 是否启用缓存跳过模式（外部传入的开关）
        """
        # 缓存文件路径（当前工作目录下的build_flag.json）
        self.fn = os.path.join(os.getcwd(), "build_flag.json")
        # 读取现有缓存（JSON格式），若文件不存在或解析失败则返回空字典
        self.cache = self.read_json(self.fn)
        # 缓存跳过总开关：外部传入的cache_skip为True，或环境变量CACHE_SKIP为True时启用
        self.cache_skip = cache_skip or os.environ.get("CACHE_SKIP", "False") == "True"
        if cache_skip:
            logger.info("启用缓存跳过模式：当哈希比对成功时，将跳过验证步骤")

    def mark_top_success(self):
        """标记top/mlir验证步骤已成功完成，更新缓存"""
        self["top_validate"] = True

    def mark_tpu_success(self):
        """标记tpu/mlir验证步骤已成功完成，更新缓存"""
        self["tpu_validate"] = True

    def mark_model_success(self):
        """标记BModel模型验证步骤已成功完成，更新缓存"""
        self["model_validate"] = True

    def sim_more_strict(self, old_sim, new_sim):
        """
        判断新的相似度标准是否比旧的更严格（用于验证标准是否提高）
        
        参数：
            old_sim: 旧的相似度阈值（二元组，如(0.9, 0.95)）
            new_sim: 新的相似度阈值（同上）
        返回：
            True: 新标准更严格（new_sim的两个值均大于old_sim）
            False: 新标准不更严格
        """
        if old_sim is None:
            # 若旧标准不存在，默认新标准更严格
            return True
        # 新标准的两个维度均大于旧标准时，视为更严格
        if new_sim[0] > old_sim[0] and new_sim[1] > old_sim[1]:
            return True
        return False

    def do_top_validate(self, top_mlir, top_output, sim, debug=False):
        """
        判断是否需要执行top/mlir文件的验证
        
        参数：
            top_mlir: top/mlir文件路径
            top_output: 验证输出文件路径
            sim: 当前验证的相似度阈值（二元组）
            debug: 是否启用调试模式
        返回：
            True: 需要执行验证
            False: 可跳过验证（缓存条件满足）
        """
        # 计算当前top/mlir文件的哈希值（基于文件内容）
        new_hash = self.hash_file(top_mlir)

        # 缓存条件判断：若所有条件满足，则跳过验证
        if (
            self["top_mlir_debug"] == debug  # 调试模式与历史一致
            and self["top_mlir"] == new_hash  # 文件内容未变（哈希一致）
            and not self.sim_more_strict(self["top_sim"], sim)  # 新相似度标准不更严格
            and self["top_validate"]  # 历史验证已成功
            and os.path.exists(top_output)  # 输出文件存在
            and self.cache_skip  # 启用缓存跳过模式
        ):
            # 更新缓存中的相似度（记录最新使用的阈值）
            self["top_sim"] = sim
            logger.info("缓存命中，跳过top验证")
            return False
        
        # 缓存条件不满足：更新缓存并返回需要验证
        self["top_sim"] = sim
        self["top_mlir_debug"] = debug
        self["top_mlir"] = new_hash
        return True

    def do_tpu_validate(self, tpu_mlir, tpu_output, sim, debug=False):
        """
        判断是否需要执行tpu/mlir文件的验证（逻辑与top验证类似）
        
        参数：
            tpu_mlir: tpu/mlir文件路径
            tpu_output: 验证输出文件路径
            sim: 当前验证的相似度阈值（二元组）
            debug: 是否启用调试模式
        返回：
            True: 需要执行验证
            False: 可跳过验证（缓存条件满足）
        """
        # 计算当前tpu/mlir文件的哈希值
        new_hash = self.hash_file(tpu_mlir)
        
        # 缓存条件判断（与top验证类似，注释略）
        if (
            self["tpu_mlir_debug"] == debug
            and self["tpu_mlir"] == new_hash
            and not self.sim_more_strict(self["tpu_sim"], sim)
            and self["tpu_validate"]
            # and os.path.exists(tpu_output)  # 注释：可能因业务需求暂时不校验输出文件存在性
            and self.cache_skip
        ):
            self["tpu_sim"] = sim
            logger.info("缓存命中，跳过tpu验证")
            return False
        
        # 更新缓存并返回需要验证
        self["tpu_sim"] = sim
        self["tpu_mlir_debug"] = debug
        self["tpu_mlir"] = new_hash
        return True

    def do_model_validate(self, bmodel, model_output):
        """
        判断是否需要执行BModel模型的验证
        
        参数：
            bmodel: BModel模型文件路径
            model_output: 验证输出文件路径
        返回：
            True: 需要执行验证
            False: 可跳过验证（缓存条件满足）
        """
        # 导入BModel解析工具（延迟导入，避免循环依赖）
        from tools.bmodel_dis import BModel2Reg
        # 计算当前BModel的哈希值（基于模型核心信息）
        new_hash = BModel2Hash(bmodel)

        # 缓存条件判断
        if (
            self["model_mlir"] == new_hash  # 模型核心信息未变（哈希一致）
            and self["model_validate"]  # 历史验证已成功
            # and os.path.exists(model_output)  # 注释：可能因业务需求暂时不校验输出文件存在性
            and self.cache_skip  # 启用缓存跳过模式
        ):
            logger.info("缓存命中，跳过模型验证")
            return False
        
        # 更新缓存并返回需要验证
        self["model_mlir"] = new_hash
        return True

    def hash_file(self, file: str) -> Optional[str]:
        """
        计算文件内容的MD5哈希值（用于标识文件是否修改）
        
        参数：
            file: 文件路径
        返回：
            哈希字符串（文件存在且为普通文件时）；None（文件不存在或不是普通文件时）
        """
        if os.path.exists(file) and os.path.isfile(file):
            with open(file, "rb") as r:
                # 读取文件内容并计算MD5
                st = r.read()
                return hashlib.md5(st).hexdigest()
        return None

    def read_json(self, fn):
        """
        读取JSON格式的缓存文件
        
        参数：
            fn: 文件路径
        返回：
            解析后的字典（解析失败时返回空字典）
        """
        try:
            with open(fn) as r:
                return json.load(r)
        except Exception:
            # 捕获所有异常（文件不存在、格式错误等），返回空字典
            return {}

    def replace_key_json(self, fn, **kwargs):
        """
        更新JSON缓存文件中的键值对
        
        参数：
            fn: 文件路径
            kwargs: 需要更新的键值对
        """
        # 先读取现有内容
        dic = self.read_json(fn)
        # 更新键值对
        dic.update(kwargs)
        # 写回文件
        with open(fn, "w") as w:
            json.dump(dic, w)

    def __getitem__(self, k):
        """
        魔术方法：支持像字典一样访问缓存值（self[k]）
        
        参数：
            k: 缓存键名
        返回：
            缓存值（不存在时返回None）
        """
        return self.cache.get(k, None)

    def __setitem__(self, k, v):
        """
        魔术方法：支持像字典一样设置缓存值（self[k] = v），并同步更新到JSON文件
        
        参数：
            k: 缓存键名
            v: 缓存值
        """
        self.cache[k] = v
        # 同步更新到JSON文件
        self.replace_key_json(self.fn,**{k: v})
```

### 三、CommandRecorder

`CommandRecorder` 类是一个用于文件状态跟踪、操作记录与缓存管理的工具类，核心功能是通过持久化缓存文件记录关键信息，实现对文件变化的检测和操作过程的追溯，最终支持 “增量执行”（即文件未变化时复用历史结果，减少重复计算）。其具体功能可分为以下几个方面：

#### 1. 文件状态跟踪

- 记录文件 / 目录的修改时间：通过 `add_file` 方法，可将指定文件或目录添加到缓存中，记录其相对路径和最后修改时间（目录通过生成 `.modify` 文件特殊处理，避免因内部文件变化而无法检测）。
- 检测文件是否变化：通过 `all_file_no_change` 方法对比当前文件的修改时间与缓存记录，快速判断文件是否被修改；`check` 方法则批量检查所有缓存文件的状态。

#### 2. 操作与属性记录

- 记录执行命令：通过 `add_command` 方法按阶段（如 “编译”“量化”）记录执行的命令，便于追溯流程中各步骤的具体操作。
- 记录自定义属性：通过 `add_property` 方法存储额外信息（如作者、构建时间、版本号等），丰富缓存的元数据。

#### 3. 缓存文件管理

- 缓存持久化：通过 `dump` 方法将所有记录的信息（文件状态、命令、属性等）以 JSON 格式写入缓存文件，实现数据持久化。
- 缓存加载与合并：初始化时自动加载已有缓存文件；通过 `update_file` 方法可合并其他缓存文件的数据，支持多阶段流程的缓存整合。
- 缓存刷新与清理：`refresh` 方法从缓存文件更新内存数据，确保一致性；`clear` 方法可清空缓存，仅保留版本信息。

#### 4. 支持增量执行

通过检测文件是否变化（结合 `check` 或 `all_file_no_change` 方法），判断是否需要重新执行流程（如编译、模型量化等）。若文件未修改且缓存有效，可直接复用历史结果，显著提升效率。

```python
class CommandRecorder:
    """
    命令记录与文件缓存工具类
    
    功能：跟踪文件状态（最后修改时间）、记录执行命令和属性信息，
    通过缓存文件持久化这些数据，用于判断文件是否变化以决定是否需要重新执行操作。
    """
    def __init__(self, cache_fn, read=False):
        """初始化CommandRecorder实例，读取或创建缓存文件"""
        if read and not os.path.exists(cache_fn):
            raise FileNotFoundError(f"缓存文件 {cache_fn} 不存在")
        
        self._dic = {"version": version}  # 核心数据字典，初始包含版本信息
        self.cache_fn = cache_fn
        self.base_dir = os.path.abspath(os.path.dirname(cache_fn))
        
        # 加载已有缓存文件内容
        if os.path.exists(cache_fn):
            self._dic.update(json.loads(Path(cache_fn).read_text()))
        
        # 构建文件路径→最后修改时间的映射（用于快速检查变化）
        self.cache_rev = {}
        try:
            for type_, val in self._dic.get("files", {}).items():
                self.cache_rev[val["path"]] = val["last_modify"]
        except KeyError:
            logger.error(f"缓存文件 {cache_fn} 格式无效")
            os.remove(cache_fn)

    def clear(self):
        """清空缓存数据，仅保留版本信息"""
        self._dic = {"version": version}

    @property
    def dic(self):
        """处理后的缓存字典，仅保留存在的文件并转换为绝对路径"""
        dic = self._dic.copy()
        files = {}
        for file_name, file_info in dic.get("files", {}).items():
            file = os.path.realpath(os.path.join(self.base_dir, file_info["path"]))
            if os.path.exists(file):
                files[file_name] = {
                    "path": file,
                    "last_modify": file_info["last_modify"],
                }
        dic["files"] = files
        return dic

    def add_property(self, **kwargs):
        """添加自定义属性到缓存（如作者、构建时间等）"""
        for key, value in kwargs.items():
            ret = self._dic.setdefault("properties", {})
            ret[key] = value

    def add_file(self,** kwargs):
        """添加文件/目录到缓存，记录相对路径和最后修改时间"""
        for name, file_path in kwargs.items():
            if not isinstance(file_path, str) or not os.path.exists(file_path):
                continue
            ret = self._dic.setdefault("files", {})
            rel_path = os.path.relpath(file_path, self.base_dir)
            
            # 处理目录：通过.modify文件跟踪修改时间
            if os.path.isdir(file_path):
                modify_file = Path(file_path).joinpath(".modify")
                if not modify_file.exists():
                    modify_file.write_text("1")
                ret[name] = {
                    "path": rel_path,
                    "last_modify": os.path.getmtime(modify_file),
                }
            # 处理文件：直接记录修改时间
            else:
                ret[name] = {
                    "path": rel_path,
                    "last_modify": os.path.getmtime(file_path),
                }

    def refresh(self):
        """从自身缓存文件更新数据"""
        self.update_file(self.cache_fn)

    def add_command(self, **kwargs):
        """记录各阶段执行的命令（如编译、链接命令）"""
        for stage, command in kwargs.items():
            self._dic.setdefault("commands", {})[stage] = command

    def update_file(self, file_path):
        """从其他缓存文件加载并合并数据（整合多阶段缓存）"""
        try:
            load_from = CommandRecorder(file_path)
            ref_dir = os.path.dirname(file_path)
            
            # 合并文件信息
            for file_name, file_info in load_from._dic.get("files", {}).items():
                self.add_file(**{file_name: os.path.join(ref_dir, file_info["path"])})
            
            # 合并其他字段（版本、属性、命令等）
            for k, v in load_from._dic.items():
                if k == "files":
                    continue
                if isinstance(v, dict) and k in self._dic:
                    self._dic[k].update(v)
                elif isinstance(v, dict):
                    self._dic[k] = v.copy()
                else:
                    self._dic[k] = v
        except Exception:
            logger.error(f"缓存文件 {file_path} 无效，合并失败")

    def all_file_no_change(self, *file_names):
        """检查指定文件是否未变化（修改时间与缓存一致）"""
        for file_name in file_names:
            if file_name not in self.cache_rev or self.cache_rev[file_name] != os.path.getmtime(file_name):
                return False
        return True

    def dump(self, fn=None):
        """将缓存数据写入文件（持久化）"""
        if fn is None:
            fn = self.cache_fn
        with open(fn, "w") as w:
            json.dump(self._dic, w, indent=2, ensure_ascii=False)

    def check(self):
        """检查所有缓存文件是否均未变化"""
        return self.all_file_no_change(*self.cache_rev.keys())
```

## 16.data_selector

### 一、整体定位

`DataSelector` 的核心作用是标准化数据样本的选择与校验，具体包括：

1. 从指定路径（`dataset`）或数据列表文件（`data_list_file`）中获取数据样本路径；
2. 支持随机选择指定数量（`num`）的样本；
3. 严格校验所有样本的类型，确保只能是以下三种类型中的一种：

   - npz 文件（单文件可能包含多个输入）；
   - npy 文件（单个或多个用逗号分隔的文件）；
   - 图像文件（单个或多个用逗号分隔的文件，支持 jpg、bmp、png 等格式）；
4. 可将筛选后的样本列表保存到文件中。

### 二、重点代码

1. 数据来源处理
   - 支持两种数据输入方式：
     - 从 `data_list_file`（数据列表文件）读取数据路径，按文件内容加载数据，若指定 `num`（样本数量）则截取前 `num` 个数据；
     - 若未提供列表文件，则从 `dataset`（数据集目录）中随机选择 `num` 个数据（`num=0` 时选择所有数据）。
     - 若未指定数据集路径或数据列表文件，抛出错误；
     - 若筛选后的数据列表为空（未找到任何数据），抛出错误，确保流程能获取有效输入。

   ```python
   ```

# 支持的输入数据格式情况：

# case 0: 单个输入，jpg 格式

# case 1: 单个输入，npy 格式

# case 2: 一个或多个输入，npz 格式

# case 3: 多个输入，npy 格式（用逗号分隔）

# case 4: 多个输入，jpg 格式（用逗号分隔）

class DataSelector:
"""
数据选择器类，用于从指定数据集或数据列表文件中选择数据样本
支持 npz、npy 和图像文件格式，确保所有选中的数据格式统一
"""

```
def __init__(self, dataset: str, num: int = 0, data_list_file: str = None):
    """
    初始化数据选择器
    
    参数:
        dataset: 数据集目录路径
        num: 要选择的数据样本数量，0表示选择所有
        data_list_file: 数据列表文件路径，若提供则从文件读取数据列表
    """
    self.data_list = []  # 存储选中的数据文件路径列表
    
    # 优先从数据列表文件读取数据
    if data_list_file:
        with open(data_list_file, 'r') as f:
            # 读取文件中的每一行，去除首尾空白后添加到数据列表
            for line in f.readlines():
                self.data_list.append(line.strip())
        # 如果指定了数量且小于现有数据量，则截取前num个数据
        if num > 0 and num < len(self.data_list):
            self.data_list = self.data_list[:num]
    # 若未提供数据列表文件，则从数据集目录随机选择
    elif dataset:
        self.data_list = self._random_select(dataset, num)
    else:
        raise RuntimeError("请通过--dataset指定数据集路径")
        
    # 检查数据列表是否为空
    if len(self.data_list) == 0:
        raise RuntimeError("没有找到输入数据")
        
    # 初始化数据类型标记
    self.all_npz, self.all_npy, self.all_image = False, False, False
    # 检查数据列表中的文件格式是否合法且统一
    self._check_data_list()
```

```

2. 数据格式校验与统一
	- 支持`npz`、`npy`、图像文件（如 jpg）等格式；
	- 通过`_check_data_list`方法检查所有选中数据的格式是否合法，且确保格式统一（即所有数据均为同一类型，如全为`npz`或全为图像文件），避免格式混杂导致后续处理出错。

```python
def _check_data_list(self):
        """
        检查数据列表中的所有文件是否为支持的格式，且所有文件格式统一
        支持的格式：npz、npy、图像文件（jpg/bmp/png等）
        """
        for file in self.data_list:
            # 检查是否为npz格式
            if self.is_npz(file):
                self.all_npz = True
            else:
                # 处理多输入情况（用逗号分隔的多个文件）
                inputs = file.split(',')
                inputs = [s.strip() for s in inputs]  # 去除每个文件名的空白
                
                # 检查每个输入文件的格式
                for i in inputs:
                    if self.is_npy(i):
                        self.all_npy = True
                    elif self.is_image(i):
                        self.all_image = True
                    else:
                        raise RuntimeError(f"不支持的文件格式: {file}")
            
            # 确保所有文件只能是一种格式（npz、npy或图像）
            # 计算已标记的格式数量（True=1, False=0）
            num_type = self.all_npz + self.all_image + self.all_npy
            if num_type != 1:
                raise RuntimeError("仅支持单一输入类型：npy/npz/图像文件")
```

1. 随机选择的实现原理

使用固定随机种子（`random.seed(1684)`）打乱列表，既实现 “随机选择”，又保证多次运行时选择结果一致（可复现）。

```python
def _random_select(self, dataset_path, num):
        """
        从数据集目录中随机选择指定数量的文件
        
        参数:
            dataset_path: 数据集目录路径
            num: 要选择的文件数量，0表示选择所有
            
        返回:
            选中的文件路径列表
        """
        full_list = []
        # 递归遍历目录下的所有文件
        for file in pathlib.Path(dataset_path).glob('**/*'):
            name = str(file)
            # 检查文件是否为支持的格式
            if self.is_npz(name) or self.is_npy(name) or self.is_image(name):
                full_list.append(name)
        
        # 排序并随机打乱顺序
        full_list = sorted(full_list)
        random.seed(1684)  # 设置随机种子，确保结果可复现
        random.shuffle(full_list)
        
        # 确定最终选择的数量
        if num > 0:
            num = num if len(full_list) > num else len(full_list)
        else:
            num = len(full_list)  # 若num为0，则选择所有文件
            
        return full_list[:num]
```

## 17.sensitive_layer.py

### 一、整体定位

`SensitiveLayer` 类是 TPU-MLIR 工具中用于模型量化敏感层分析与混合精度优化的核心类，其核心功能是：通过识别模型中对量化（如 INT8）敏感的层（即量化后精度损失较大的层），调整这些层的量化策略（如保留浮点精度），最终在保证模型精度的前提下，平衡量化效率与精度损失。具体功能包括：

1. 量化阈值生成
   针对不同量化方法（MAX、Percentile9999、KL），为模型各层生成量化阈值（激活值的截断范围），并记录各层的最大激活值和最优阈值，为后续敏感层分析提供基础数据。
2. 敏感层识别
   通过对比不同量化方法下各层的精度损失（如余弦相似度损失），识别对量化敏感的层 —— 这些层在量化后精度下降明显，需要特殊处理（如保留浮点精度）。
3. 混合精度优化
   基于敏感层分析结果，将最敏感的前 N 层（由 `--max_float_layers` 指定）设置为浮点精度（如 FP32/FP16），其余层保持量化精度（如 INT8），生成混合精度模型，在精度损失最小的前提下最大化量化效率。
4. 模型精度验证与日志输出
   对优化后的混合精度模型进行推理验证，输出各层的量化模式（浮点 / 量化）、精度指标（如余弦相似度）和优化日志，确保优化效果可追溯。

### 二、代码解析

代码以 `run()` 方法为入口，按 “精度评估 → 阈值生成 → 敏感层识别 → 混合精度优化 → 结果验证” 的流程递进，逻辑清晰，步骤明确：

1. 初始化与基础精度评估（`run()` 第一步）

- 加载浮点模型（`float_model`）和 INT8 量化模型（`int8_model`），分别执行推理。
- 计算 INT8 模型的精度（如余弦相似度），若精度已满足预期（`outputs_cos > expected_cos`），则无需优化，直接退出。

```python
def run(self):
    # 记录程序开始时间，用于后续计算总耗时
    t0 = time.time()

    # 第一步：浮点模型和INT8量化模型推理，评估基础精度
    # 初始化浮点模型（未量化的原始模型）
    float_model = MixQuantModel(self.fp32_mlir, self.chip)
    # 初始化INT8量化模型（使用预先生成的校准表）
    int8_model = MixQuantModel(self.fp32_mlir, self.chip, self.cali_table_name)
    
    # 初始化存储层余弦相似度的列表和预测结果的列表
    layer_cos_list, predictions_gt = [], []
    # 提取全局比较层（用于评估模型整体精度的关键层）、各层权重占比等信息
    global_compare_layers, layers_rate, _ = self.mix_prec.extract_global_layers()
    
    # 运行浮点模型推理，获取基准输出（True表示这是基准模型，用于记录参考结果）
    # 结果用于后续与量化模型对比
    _ = self.mix_prec.run_model(float_model, True, global_compare_layers, layers_rate, predictions_gt)
    
    # 运行INT8量化模型推理，计算与浮点模型的余弦相似度（评估量化精度损失）
    # False表示这是待评估模型，需要与基准模型对比
    outputs_cos = self.mix_prec.run_model(int8_model, False, global_compare_layers, layers_rate, predictions_gt)
    
    # 如果INT8模型精度已满足预期（余弦相似度高于设定阈值），则无需优化
    if outputs_cos > self.args.expected_cos:
        # 清理模型资源
        float_model.clean()
        int8_model.clean()
        # 启用日志打印
        self.mix_prec.enable_print()
        # 输出成功信息并退出
        self.mix_prec.logger.print_info(
            f'任务成功，当前INT8模型余弦相似度:{outputs_cos} 高于预期阈值:{self.args.expected_cos}，无需混合精度优化')
        exit(0)
    
    # 记录INT8模型的精度（用于后续与混合精度模型对比）
    all_int8_cos = outputs_cos
    self.mix_prec.logger.print_info(f"INT8模型整体余弦相似度: {all_int8_cos}")
```

1. 多方法量化阈值生成（`gen_multiple_thresholds`）

- 针对三种量化方法（MAX、Percentile9999、KL），为模型各层计算量化阈值：
  - 通过 `ActivationCalibrator` 收集激活值统计信息（如最大 / 最小值、直方图）；
  - 结合调优数据集（`tune_ds`）优化阈值，生成各方法对应的阈值表；
  - 最终得到 `layer_th_dicts`，记录各层在不同方法下的最大激活值和最优阈值。

```python
def gen_multiple_thresholds(self, all_op_names, quantize_method_list):
    """
    为每种量化方法生成各层的量化阈值，并记录最大激活值和最优阈值
    
    参数:
        all_op_names: 所有操作层的名称列表
        quantize_method_list: 量化方法列表（如["MAX", "Percentile9999", "KL"]）
    
    返回:
        layer_th_dicts: 字典，结构为 {量化方法: {层名: [最大激活值, 最优阈值]}}
    """
    # 初始化存储阈值的字典：键为量化方法，值为该方法下各层的阈值信息
    layer_th_dicts = {}  # method_name: {op_name: [op_fmax, op_th]}
    
    # 遍历每种量化方法，为各层计算阈值
    for i, method_name in enumerate(quantize_method_list):
        # 临时存储当前量化方法下各层的阈值信息
        tmp_th_dict = {}
        
        # 初始化激活校准器，用于收集激活值并计算阈值
        calibrator = ActivationCalibrator(self.args, self.selector, self.tune_ds)
        
        # 根据量化方法设置校准器的调试命令（指定阈值计算方式）
        if method_name == "MAX":
            calibrator.debug_cmd = 'use_max'  # 使用最大值法计算阈值
        elif method_name == "Percentile9999":
            calibrator.debug_cmd = 'use_percentile9999'  # 使用99.99分位数法计算阈值
        
        # 收集激活值统计信息并计算阈值
        # 返回值包括：阈值映射、最大激活值映射、缩放因子映射、零点映射等
        thresholds_map, thresholds_map_absmax, thresholds_map_scale, thresholds_map_zp, _, _, _, _ = calibrator.activation_collect_and_calc_th()
        
        # 清理校准器占用的资源
        calibrator._clean_resource()
        
        # 步骤3：导出默认直方图分箱数对应的阈值表
        thresholds_map_list = []  # 存储所有层的阈值列表
        op_layers = self.parser.get_op_name_list()  # 获取所有操作层名称
        
        # 生成当前量化方法对应的校准表文件名
        cali_table = self.args.calibration_table + "_" + method_name
        # 若启用调优，添加后缀区分
        if self.args.tune_num > 0:
            cali_table += ".1"
        
        # 将阈值信息写入校准表文件
        with open(cali_table, 'w') as f:
            f.write("# 生成时间: {}\n".format(datetime.datetime.now()))
            f.write("# 直方图分箱数: {}\n".format(calibrator.histogram_bin_num))
            f.write("# 样本数量: {}\n###\n".format(calibrator.num_samples))
            f.write("# 层名称    阈值    最小值    最大值\n")
            
            # 遍历所有操作层，记录阈值信息
            for i, op_name in enumerate(op_layers):
                outputs = self.parser.get_outputs_by_op_name(op_name)  # 获取该层的输出张量
                for out in outputs:
                    if out not in thresholds_map:
                        continue  # 跳过无阈值信息的层
                    
                    # 计算阈值、最小值、最大值（区分是否使用PyTorch的观测器）
                    if 'use_torch_observer_for_cali' in calibrator.debug_cmd:
                        # 使用PyTorch观测器时，根据缩放因子和零点计算阈值
                        qmin, qmax = -128, 127  # 假设INT8量化的范围
                        scale = thresholds_map_scale[op_name]  # 缩放因子
                        zp = thresholds_map_zp[op_name]  # 零点
                        # 计算阈值（基于量化范围和缩放因子）
                        threshold = float(scale * max(-(qmin - zp), qmax - zp))
                        min_value = float(scale * (qmin - zp))  # 量化后的最小值
                        max_value = float(scale * (qmax - zp))  # 量化后的最大值
                    else:
                        # 直接使用校准器计算的阈值
                        threshold = thresholds_map[out] if out in thresholds_map else 1.0
                        # 从激活值统计中获取最小值和最大值
                        if out in calibrator.activations_statistics:
                            min_value, max_value, _ = calibrator.activations_statistics[out]
                        else:
                            min_value, max_value = -1, 1  # 默认值
                    
                    thresholds_map_list.append(threshold)
                    # 写入文件（保留7位小数）
                    f.write("{} {:.7f} {:.7f} {:.7f}\n".format(out, threshold, min_value, max_value))
        
        # 若未启用调优（tune_num <= 0），直接返回（无需后续优化）
        if calibrator.args.tune_num <= 0:
            return
        
        # 若启用调优，处理调优后的阈值
        if self.args.tune_num > 0:
            # 去除临时后缀，准备生成调优后的校准表
            cali_table = cali_table.rsplit(".1", 1)[0]
        
        # 更新校准器的校准表路径，用于调优
        calibrator.args.calibration_table = cali_table
        # 初始化简单调优器，基于调优数据集优化阈值
        tunner = SimpleTuner(calibrator.args, calibrator.tune_ds, calibrator.ppa_list, thresholds_map_absmax)
        # 执行调优，获取优化后的阈值映射
        thresholds_map = tunner.run()
        
        # 存储调优后的阈值列表和层名称列表
        tuned_threshold_list = []
        layer_name_list = []
        # 生成调优后的校准表文件名
        cali_table += "_tune"
        # 获取未融合的张量列表（排除融合层，确保每层独立）
        op_layers = calibrator.get_no_fused_tensors(op_layers)
        
        # 将调优后的阈值信息写入新的校准表文件
        with open(cali_table, 'w') as f:
            f.write("# 生成时间: {}\n".format(datetime.datetime.now()))
            f.write("# 直方图分箱数: {}\n".format(calibrator.histogram_bin_num))
            f.write("# 样本数量: {}\n".format(calibrator.num_samples))
            f.write("# 调优样本数量: {}\n###\n".format(self.args.tune_num))
            f.write("# 层名称    阈值    最小值    最大值\n")
            
            for i, op_name in enumerate(op_layers):
                op_name = split_fuseop(op_name)  # 拆分融合层名称，获取原始层名
                threshold = thresholds_map[op_name]  # 获取调优后的阈值
                
                # 处理异常阈值（接近0或NaN），避免量化错误
                if threshold <= 1e-5 or np.isnan(threshold):
                    threshold = 1e-5
                    print(f"警告: 层 {op_name} 的阈值为0，请检查输入数据的正确性。")
                
                # 记录层名称和阈值
                layer_name_list.append(f'{i}_{op_name}')
                tuned_threshold_list.append(threshold)
                
                # 获取该层的激活值统计信息（最小值、最大值）
                min_value, max_value, _ = calibrator.activations_statistics[op_name]
                # 写入文件
                f.write("{} {:.7f} {:.7f} {:.7f}\n".format(op_name, threshold, min_value, max_value))
        
        # 整理当前量化方法下各层的最大激活值和调优后的阈值
        for op_name in all_op_names:
            if op_name in thresholds_map:  # 仅处理有阈值信息的层
                # 存储 [最大激活值, 调优后阈值]
                tmp_th_dict[op_name] = [thresholds_map_absmax[op_name], thresholds_map[op_name]]
        
        # 将当前量化方法的阈值信息存入总字典
        layer_th_dicts[method_name] = tmp_th_dict
    
    # 返回所有量化方法的阈值信息
    return layer_th_dicts
```

1. 层名一致性检查（`check_layer_names`）

- 验证模型定义中的层名与阈值表中的层名是否一致，排除无效层（如类型为 “Coeff”“Accuracy” 的层），确保后续分析的层是有效计算层。

```python
def check_layer_names(self, all_op_names, int8_model, layer_th_dicts, quantize_method_list):
    """
    检查模型层名的一致性，过滤无效层，确保后续分析的层在阈值表中存在
    
    参数:
        all_op_names: 所有操作层的名称列表
        int8_model: INT8量化模型实例（用于获取层的元信息）
        layer_th_dicts: 存储各量化方法下阈值信息的字典
        quantize_method_list: 量化方法列表
    
    返回:
        layer_names: 过滤后的有效层名称列表
    """
    # 存储过滤后的有效层名称
    layer_names = []
    # 存储层名到层类型的映射
    layer_name2layer_type_dict = {}
    # 需要忽略的层类型（非计算层，无需量化分析）
    ignored_layers = ["Coeff", "Accuracy"]
    
    # 遍历所有层名，过滤无效层
    for layer_name in all_op_names:
        ignore = False  # 标记是否忽略当前层
        # 获取当前层的原型信息（包含层类型等元数据）
        layer_proto = int8_model.parser.get_op_by_op_name(layer_name)
        
        # 若层不存在（原型为None）或属于需忽略的类型，则标记为忽略
        if layer_proto is None or layer_proto.type in ignored_layers:
            ignore = True
        
        # 若不忽略，则加入有效层列表，并记录层类型
        if not ignore:
            layer_names.append(layer_name)
            layer_name2layer_type_dict[layer_name] = layer_proto.type
    
    # 检查有效层是否都在阈值表中存在（确保名称一致性）
    for layer_name_check in layer_names:
        # 以第一种量化方法的阈值表为基准检查
        if layer_name_check not in layer_th_dicts[quantize_method_list[0]].keys():
            # 若层名不匹配，输出调试信息并退出（避免后续分析出错）
            self.mix_prec.logger.print_dbg(
                f"模型定义中的层名 {layer_name_check} 与阈值表中的层名不匹配，请检查层名中是否包含 '!' 或 '/' 等特殊字符"
            )
            exit(1)  # 退出程序，返回错误码1
    
    # 所有层名检查通过
    self.mix_prec.logger.print_info("层名一致性检查通过！")
    return layer_names
```

1. 敏感层搜索（`search_sensitve_layer`）

- 遍历所有有效层，依次测试将每层设置为不同量化方法的阈值时，模型整体精度的变化：
  - 记录每层在不同方法下的精度损失（`sensitive_layer_analysis_dict`）；
  - 精度损失越大，说明层对量化越敏感。

```python
def search_sensitve_layer(self, layer_names, quantize_method_list, float_model, int8_model, layer_th_dicts, global_compare_layers, layers_rate, predictions_gt):
    """
    搜索对量化敏感的层：通过测试不同量化方法下各层的精度损失，识别量化后精度下降明显的层
    
    参数:
        layer_names: 有效层名称列表（经过check_layer_names过滤）
        quantize_method_list: 量化方法列表（如["MAX", "Percentile9999", "KL"]）
        float_model: 浮点模型（基准模型）
        int8_model: INT8量化模型
        layer_th_dicts: 各量化方法下的阈值信息字典
        global_compare_layers: 用于评估精度的全局比较层
        layers_rate: 各层权重占比
        predictions_gt: 基准模型的预测结果（用于对比）
    
    返回:
        sensitive_layer_analysis_dict: 敏感层分析字典，结构为 {层名: [精度损失, 层类型]}
        new_cali_table_name: 最后生成的校准表文件名
    """
    # 量化方法的数量（用于控制测试轮次）
    num_quantize_method = len(quantize_method_list)
    # 初始化浮点层列表（记录当前被视为浮点的层，初始包含所有层）
    fp_layer_list = []
    for op_name in layer_names:
        fp_layer_list.append(op_name)
    
    # 记录每层的测试状态：{层名: [尝试的方法索引, 最小精度损失, 最优阈值, 最优方法]}
    modified_layers = {}
    # 记录上一次尝试的量化方法
    last_tried_method = quantize_method_list[0]
    # 存储敏感层分析结果：键为层名，值为[精度损失, 层类型]
    sensitive_layer_analysis_dict = {}
    
    # 遍历每个有效层，测试其量化敏感性
    for layer_name in layer_names:
        # 获取当前层的类型（如卷积层、缩放层等）
        layer_type = self.parser.get_op_type_by_op_name(layer_name)
        self.mix_prec.logger.print_info(f"开始处理层: {layer_name}, 类型: {layer_type}")
        
        # 临时将当前层从浮点层列表中移除（即对该层进行量化测试，其他层保持浮点）
        fp_layer_list.remove(layer_name)
        # 生成混合精度配置表（指定哪些层用浮点，哪些用量化）
        mix_table = self.mix_prec._gen_mix_table(fp_layer_list)
        
        ret = False  # 标记当前层是否完成测试
        while not ret:
            # 第一次处理当前层：初始化测试状态
            if layer_name not in modified_layers:
                # 从第一种量化方法开始测试
                last_tried_method = quantize_method_list[0]
                # 初始化状态：[尝试的方法索引=1, 最小损失=无穷大, 初始阈值, 初始方法]
                modified_layers[layer_name] = [1, float('inf'), layer_th_dicts[last_tried_method][layer_name][1], last_tried_method]
                method = last_tried_method
                # 获取该方法下当前层的阈值（layer_th_dicts结构：{方法: {层名: [最大激活值, 阈值]}}）
                new_th = layer_th_dicts[last_tried_method][layer_name][1]
                # 更新当前层的阈值，生成新的校准表
                new_cali_table_name = self.set_layer_new_th(int8_model, layer_name, new_th)
                last_tried_method = method
                self.mix_prec.logger.print_info(f"调整层 {layer_name} 的阈值，使用方法 {method}，阈值为 {new_th}")
                
                # 创建使用新校准表和混合精度配置的模型
                mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table_name, mix_table)
                # 计算模型精度损失（1 - 余弦相似度，值越大说明损失越大）
                outputs_cos = 1 - self.mix_prec.run_model(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)
                self.mix_prec.logger.print_info(f"精度损失（cosine loss）= {outputs_cos}")
            
            # 未测试完所有量化方法：继续尝试下一种方法
            elif modified_layers[layer_name][0] < num_quantize_method:
                # 获取下一种量化方法的索引和名称
                method_idx = modified_layers[layer_name][0]
                method = quantize_method_list[method_idx]
                
                # 若当前精度损失小于记录的最小损失，更新最优状态
                if outputs_cos < modified_layers[layer_name][1]:
                    modified_layers[layer_name][1] = outputs_cos  # 更新最小损失
                    modified_layers[layer_name][2] = layer_th_dicts[last_tried_method][layer_name][1]  # 更新最优阈值
                    modified_layers[layer_name][3] = last_tried_method  # 更新最优方法
                
                # 获取当前方法下的阈值，更新校准表
                new_th = layer_th_dicts[method][layer_name][1]
                new_cali_table_name = self.set_layer_new_th(int8_model, layer_name, new_th)
                last_tried_method = method
                self.mix_prec.logger.print_info(f"调整层 {layer_name} 的阈值，使用方法 {method}，阈值为 {new_th}")
                
                # 递增方法索引，标记已尝试该方法
                modified_layers[layer_name][0] += 1
                # 创建新模型并计算精度损失
                mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table_name, mix_table)
                outputs_cos = 1 - self.mix_prec.run_model(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)
                self.mix_prec.logger.print_info(f"精度损失（cosine loss）= {outputs_cos}")
            
            # 已测试完所有量化方法：确定最优阈值和方法
            elif modified_layers[layer_name][0] == num_quantize_method:
                # 最后一次比较，更新最优状态
                if outputs_cos < modified_layers[layer_name][1]:
                    modified_layers[layer_name][1] = outputs_cos
                    modified_layers[layer_name][2] = layer_th_dicts[last_tried_method][layer_name][1]
                    modified_layers[layer_name][3] = last_tried_method
                
                # 获取最优阈值
                best_th = modified_layers[layer_name][2]
                # 标记测试完成（索引+1，避免重复处理）
                modified_layers[layer_name][0] += 1
                # 应用最优阈值，生成最终校准表
                new_cali_table_name = self.set_layer_new_th(int8_model, layer_name, best_th)
                
                # 记录当前层的最优结果
                self.mix_prec.logger.print_info(
                    f"层 {layer_name}，类型 {layer_type}，最优阈值 = {best_th}，最优方法 = {modified_layers[layer_name][3]}，最优精度损失 = {modified_layers[layer_name][1]}"
                )
                # 将当前层的精度损失和类型存入分析字典
                sensitive_layer_analysis_dict[layer_name] = [modified_layers[layer_name][1], layer_type]
                ret = True  # 标记当前层测试完成
        
        # 将当前层加回浮点层列表（准备处理下一层）
        fp_layer_list.append(layer_name)
    
    # 返回敏感层分析结果和最后生成的校准表名
    return sensitive_layer_analysis_dict, new_cali_table_name
```

1. 敏感层分析与混合精度配置（`analysis_sensitive_layers`）

- 对敏感层按精度损失从大到小排序，选取前 N 层（`--max_float_layers`）标记为浮点层。
- 特殊处理：若敏感层为卷积层（`top.Conv`），则其后续的缩放层（`top.Scale`）也需设为浮点；若敏感层为缩放层，则其前置的卷积层也需设为浮点（确保计算链精度一致）。

```python
def analysis_sensitive_layers(self, sensitive_layer_analysis_dict):
    """
    分析敏感层，根据精度损失排序并筛选需要保留浮点精度的层，同时处理关联层
    
    参数:
        sensitive_layer_analysis_dict: 敏感层分析字典，结构为 {层名: [精度损失, 层类型]}
    
    返回:
        set_fp_layer_list: 需要保留浮点精度的层名称列表
    """
    # 敏感层计数器（用于标记敏感程度排名）
    num = 0
    # 已设置为浮点的层计数器（不超过max_float_layers）
    num_fp32 = 0
    # 存储需要保留浮点精度的层列表
    set_fp_layer_list = []
    
    # 按精度损失从大到小排序敏感层（损失越大，对量化越敏感）
    # 排序键为元组中的第二个元素（即[1][0]，精度损失值），reverse=True表示降序
    sensitive_layer_analysis_dict = sorted(sensitive_layer_analysis_dict.items(), key=lambda x: x[1][0], reverse=True)
    
    # 遍历排序后的敏感层，筛选需要保留浮点的层
    for sensitive_layer_tuple in sensitive_layer_analysis_dict:
        # 打印当前层的敏感信息：层名、敏感排名、精度损失、层类型
        self.mix_prec.logger.print_info(
            f"层 {sensitive_layer_tuple[0]} 是第 {num} 敏感层，精度损失为 {sensitive_layer_tuple[1][0]}，类型为 {sensitive_layer_tuple[1][1]}"
        )
        
        # 若未超过最大浮点层限制（max_float_layers），则将当前层设为浮点
        if self.args.max_float_layers > 0 and self.args.max_float_layers > num_fp32:
            # 跳过已添加的层（避免重复）
            if sensitive_layer_tuple[0] in set_fp_layer_list:
                continue
            
            # 将当前敏感层加入浮点列表，并递增计数器
            set_fp_layer_list.append(sensitive_layer_tuple[0])
            num_fp32 += 1
            
            # 获取当前层的操作信息（用于判断层类型）
            op = self.parser.get_op_by_op_name(sensitive_layer_tuple[0])
            
            # 特殊处理1：若当前层是卷积层（top.Conv），则其后续的缩放层（top.Scale）也需设为浮点
            if op.type == "top.Conv":
                # 获取当前卷积层的后续层
                next_op_name_list = self.parser.get_next_op_by_op_name(sensitive_layer_tuple[0])
                for next_op_name in next_op_name_list:
                    next_op = self.parser.get_op_by_op_name(next_op_name)
                    # 若后续层是缩放层，加入浮点列表并更新计数器
                    if next_op.type == "top.Scale":
                        self.mix_prec.logger.print_info(f"后续缩放层: {next_op_name}（需设为浮点）")
                        set_fp_layer_list.append(next_op_name)
                        num_fp32 += 1
            
            # 特殊处理2：若当前层是缩放层（top.Scale），则其前置的卷积层（top.Conv）也需设为浮点
            elif op.type == "top.Scale":
                # 获取当前缩放层的前置层
                pre_op_name_list = self.parser.get_pre_op_by_op_name(sensitive_layer_tuple[0])
                for pre_op_name in pre_op_name_list:
                    pre_op = self.parser.get_op_by_op_name(pre_op_name)
                    # 若前置层是卷积层，加入浮点列表并更新计数器
                    if pre_op.type == "top.Conv":
                        self.mix_prec.logger.print_info(f"前置卷积层: {pre_op_name}（需设为浮点）")
                        set_fp_layer_list.append(pre_op_name)
                        num_fp32 += 1
        
        # 递增敏感层计数器
        num += 1
    
    # 打印最终确定的浮点层列表
    self.mix_prec.logger.print_info(f"需要保留浮点精度的层列表: {set_fp_layer_list}")
    return set_fp_layer_list
```

1. 混合精度模型生成与验证（`run()` 最后一步）

- 根据敏感层分析结果，生成混合精度配置表（`mix_table`），明确各层的量化模式（浮点 / 量化）。
- 加载混合精度模型，执行推理验证，计算精度指标，并输出量化表、精度日志和优化结果，完成整个流程。

```python
# 接run()方法后续部分
# 第六步：生成最终的混合精度模型并打印结果信息

# 生成网络结构可视化图（以dot格式记录敏感层搜索结果）
self.mix_prec.dot_log.gen_dot_graph()

# 根据需要保留浮点精度的层列表，生成混合精度配置表（标记各层的量化模式）
mix_table = self.mix_prec._gen_mix_table(set_fp_layer_list)

# 创建混合精度模型：基于FP32模型、目标芯片、优化后的校准表和混合精度配置
mixmodel = MixQuantModel(self.fp32_mlir, self.chip, new_cali_table_name, mix_table)

# 运行混合精度模型，计算与浮点基准模型的余弦相似度（评估优化效果）
outputs_cos = self.mix_prec.run_model(mixmodel, False, global_compare_layers, layers_rate, predictions_gt)

# 打印日志信息（包括精度对比、量化表路径、总耗时等）
self.print_log_info(layer_cos_list, set_fp_layer_list, all_int8_cos, outputs_cos, t0)

# 输出成功信息，返回"success"表示整个流程完成
print("success sensitive layer search")
return 'success'


# 辅助方法：打印并记录混合精度优化的结果信息
def print_log_info(self, layer_cos_list, fp_layer_list, all_int8_cos, outputs_cos, t0):
    # 打印结果标题
    self.mix_prec.logger.print_info('>>>运行结果:')
    
    # 按余弦相似度升序排序层列表（便于查看精度损失较大的层）
    layer_cos_list = sorted(layer_cos_list, key=lambda x: x[1], reverse=False)
    
    # 将混合精度量化配置写入量化表文件（用于后续模型部署）
    with open(self.mix_prec.quantize_table, "w") as f:
        f.write("# 生成时间: {}\n".format(datetime.datetime.now()))  # 记录生成时间
        f.write("# 样本数量: {}\n".format(self.mix_prec.num_sample))  # 记录校准用的样本数
        f.write("# 芯片平台: {}  混合精度模式: {}\n".format(self.mix_prec.chip, self.mix_prec.mix_mode))  # 硬件和模式信息
        f.write("# {}精度层数量: {}\n".format(self.mix_prec.mix_mode, len(fp_layer_list)))  # 浮点层数量
        f.write("###\n")
        f.write("# 层名称   量化模式\n")  # 表头
        # 遍历浮点层列表，记录各层的量化模式（如FP32）
        for layer in fp_layer_list:
            f.write("{} {}\n".format(layer, self.mix_prec.mix_mode))
    
    # 打印精度对比信息
    self.mix_prec.logger.print_info(f'原始INT8模型余弦相似度: {all_int8_cos:.6f}')  # 优化前的精度
    self.mix_prec.logger.print_info(f'混合精度模型余弦相似度: {outputs_cos:.6f}')  # 优化后的精度
    
    # 打印量化表输出路径
    self.mix_prec.logger.print_info(f"混合精度量化表已输出至: {self.mix_prec.quantize_table}")
    
    # 打印总耗时（从程序开始到结束的时间）
    self.mix_prec.logger.print_info(f"总耗时: {time.time() - t0}秒")
```

## 附录一：关于 MaskRCNN

可以参考一下链接的介绍：

[https://blog.csdn.net/IanYue/article/details/126657217](https://blog.csdn.net/IanYue/article/details/126657217)

[https://zhuanlan.zhihu.com/p/407831250](https://zhuanlan.zhihu.com/p/407831250)
