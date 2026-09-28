import yaml
import os
import numpy as np
import sympy as sp
from typing import List, Dict, Any
from .base import BaseOperator

# ============ ADR-056 §B.4 触点 4/7：L1 配置面（键 allowlist + 请求标记 + 文本闸） ============
# 键 allowlist = 存量 37 份 YAML 实测键并集冻结（2026-09-28 取证；chain_reduce_depth/shape
# 无一使用但是认可的解析键，予以保留）。未知键 = 伪造/拼错字段（如 experimental_unrol）
# → 响亮失败，不得静默忽略（hco notes-4）。全量强制（非仅准入算子），存量并集零冲突已证。
_TOP_KEYS = frozenset({
    "name", "formula", "tensors", "test_data", "parallel_dim", "chain_reduce_depth",
    "experimental_unroll",
})
_TENSOR_KEYS = frozenset({
    "bank", "role", "offset", "precision", "element_bits", "smc_layout", "stride",
    "length", "shape",
})
_TESTDATA_DICT_KEYS = frozenset({"range"})

# §A.2-4：role → 要求 stride（输入 stride=1、param/constant stride=0 驻留、输出单写口=1）。
# 键缺席 = 取 role 默认（add.yaml 即无 stride 键，与 prepare_input/inject_data 现有缺省一致）。
_ROLE_STRIDE = {"input": 1, "param": 0, "constant": 0, "output": 1}


class ConfigurableOperator(BaseOperator):
    """
    高级抽象算子。
    允许通过 YAML 配置文件定义公式、布局和测试逻辑，实现 70+ 算子的配置化扩展。
    """
    def __init__(self, config_path: str):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.cfg = yaml.safe_load(f)

        self._validate_yaml_keys()

        self.name = self.cfg.get("name", "anon_op")
        self.formula_str = self.cfg["formula"]
        self.tensors_cfg = self.cfg["tensors"]
        self.test_cfg = self.cfg.get("test_data", {})

        # ADR-018 v7 D4.5: 多 SMC partition 字段（向后兼容，不存在则单 SMC 模式）
        # parallel_dim: None | "N" | "K"（Stage 1 X = "N" 用 SHIFTUP broadcast；Stage 2 Y = "K" 用 SHIFTDN chain reduce）
        # chain_reduce_depth: 仅 parallel_dim="K" 时有效；默认 PARAM_SMC_NUM-1=31
        self.parallel_dim = self.cfg.get("parallel_dim")
        self.chain_reduce_depth = self.cfg.get("chain_reduce_depth")
        if self.parallel_dim not in (None, "N", "K"):
            raise ValueError(
                f"[ADR-018 D4.5] op '{self.name}' parallel_dim={self.parallel_dim!r} 非法；"
                f"合法值: None / 'N' / 'K'"
            )

        # ADR-056 §B.1 L1：experimental_unroll = 用户对展开路径的**请求**（仅请求，
        # 不构成能力证明）。缺省缺席 = 未请求；非 bool（含 1/0、"true" 字符串）响亮失败。
        _flag = self.cfg.get("experimental_unroll", False)
        if type(_flag) is not bool:
            raise ValueError(
                f"[TYPE_LAYOUT_UNSUPPORTED] op '{self.name}' 字段 experimental_unroll="
                f"{_flag!r} 非 bool——L1 请求标记只接受 true/false（ADR-056 §B.1）")
        self.experimental_unroll = _flag

        # 🛸 [V5.0 FIX] 构建 locals，强制把 tensors_cfg 里的名字当作 Symbol，防止 gamma/beta 等与 SymPy 内置冲突
        # ADR-056 §A.2-1 文本级取负闸在 sympify **之前**（SymPy 会把 -x 规整为
        # Mul(-1,x)，改写后字面量与取负不可区分；hco M2）。
        self._gate_text_formula()
        locals_dict = {name: sp.Symbol(name) for name in self.tensors_cfg}
        self.formula = sp.sympify(self.formula_str, locals=locals_dict)

        if self.experimental_unroll:
            self._gate_requested_admission()

    def _validate_yaml_keys(self) -> None:
        """ADR-056 §B.4 触点 4：三层键集合 allowlist（顶层 / tensors / test_data 字典项）。
        未知键响亮失败并带允许集提示；校验先于任何字段消费。"""
        label = self.cfg.get("name", "<unnamed>")
        unknown_top = set(self.cfg) - _TOP_KEYS
        if unknown_top:
            raise ValueError(
                f"[TYPE_LAYOUT_UNSUPPORTED] op '{label}' YAML 顶层含未知键 "
                f"{sorted(unknown_top)}——允许集 {sorted(_TOP_KEYS)}；拼错字段"
                f"（如 experimental_unrol）在此响亮失败（ADR-056 §B.4）")
        tensors = self.cfg.get("tensors", {})
        if not isinstance(tensors, dict):
            raise ValueError(
                f"[TYPE_LAYOUT_UNSUPPORTED] op '{label}' 字段 tensors 非映射："
                f"{type(tensors).__name__}")
        for tname, tcfg in tensors.items():
            if not isinstance(tcfg, dict):
                raise ValueError(
                    f"[TYPE_LAYOUT_UNSUPPORTED] op '{label}' 张量 '{tname}' 配置非映射："
                    f"{tcfg!r}")
            unknown_t = set(tcfg) - _TENSOR_KEYS
            if unknown_t:
                raise ValueError(
                    f"[TYPE_LAYOUT_UNSUPPORTED] op '{label}' 张量 '{tname}' 含未知键 "
                    f"{sorted(unknown_t)}——允许集 {sorted(_TENSOR_KEYS)}（ADR-056 §B.4）")
        test_data = self.cfg.get("test_data", {}) or {}
        if not isinstance(test_data, dict):
            raise ValueError(
                f"[TYPE_LAYOUT_UNSUPPORTED] op '{label}' 字段 test_data 非映射："
                f"{type(test_data).__name__}")
        for dname, dcfg in test_data.items():
            if isinstance(dcfg, dict):
                unknown_d = set(dcfg) - _TESTDATA_DICT_KEYS
                if unknown_d:
                    raise ValueError(
                        f"[TYPE_LAYOUT_UNSUPPORTED] op '{label}' test_data '{dname}' "
                        f"字典形态含未知键 {sorted(unknown_d)}——允许集 "
                        f"{sorted(_TESTDATA_DICT_KEYS)}（ADR-056 §B.4）")

    def _gate_text_formula(self) -> None:
        """ADR-056 §A.2-1 文本级一元取负闸（hco M2：须在 sp.sympify 之前的原始语法树阶段）。

        全量无条件执行（非仅准入算子）——存量 21 处 USub 跨 14 文件逐一取证全为数值
        字面量取负，兼容已证。规则（与 verification_v2.reference.ordered_ast._build 逐条
        对齐）：负数值字面量允许（`x + (-2.5)`、`-3.0 * x`）；表达式取负（`-x`、`-(x+y)`、
        `-alpha`）拒绝 [SYNTAX_UNSUPPORTED]；一元正号 +x 允许（对 fp32 位模式恒等）。
        """
        import ast
        try:
            tree = ast.parse(self.formula_str, mode="eval")
        except SyntaxError as e:
            raise ValueError(
                f"[SYNTAX_UNSUPPORTED] op '{self.name}' 公式文本无法解析：{e}"
                f"（ADR-056 §A.2-1 语法子集判定入口）") from e
        for node in ast.walk(tree):
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                operand = node.operand
                if not (isinstance(operand, ast.Constant)
                        and isinstance(operand.value, (int, float))
                        and not isinstance(operand.value, bool)):
                    raise ValueError(
                        f"[SYNTAX_UNSUPPORTED] op '{self.name}' 公式含表达式取负："
                        f"USub 操作数非数值字面量——负 fp32 字面量允许，"
                        f"-x / -(x+y) / -alpha 等表达式取负范围外（ADR-056 §A.2-1）")

    def _gate_requested_admission(self) -> None:
        """ADR-056 准入算子（experimental_unroll=true）的配置面承诺，不满足即响亮失败。

        (0) 严格子集闸（§A.2-1/2，hco M2）：公式语言 ≡ 参考发生器语言——复用
            verification_v2.reference.ordered_ast 的同一解析器复核，§C.2 恒等式由构造
            保证；FormulaRejection 转译 [SYNTAX_UNSUPPORTED]。
        (a) fp32 仅（§A.2-3）；(b) 恰一 output（单输出式）；
        (c) role→stride 一致（§A.2-4）；(d) length 必填 int>0（§F.5 hco M5，UR word 上界）。
        """
        # (0) 严格子集闸：deferred import（ops→reference 方向合法；reference 自身保持
        #   纯 numpy+stdlib。tensor_names = role input/param 声明名，reserved = 输出/常驻名）
        from verification_v2.reference.ordered_ast import (
            FormulaRejection, parse_ordered_formula)
        formula_names = [n for n, c in self.tensors_cfg.items()
                         if c.get("role") in ("input", "param")]
        try:
            parse_ordered_formula(self.formula_str, formula_names,
                                  reserved_names=("y", "constants"))
        except FormulaRejection as e:
            raise ValueError(
                f"[SYNTAX_UNSUPPORTED] op '{self.name}' 准入复核对公式拒绝：{e}"
                f"（ADR-056 §A.2-1，准入门语言 ≡ 参考发生器语言）") from e
        # (a) fp32 仅
        if self.element_bits != 32:
            raise ValueError(
                f"[TYPE_LAYOUT_UNSUPPORTED] op '{self.name}' 准入要求 dtype=fp32，"
                f"实得 element_bits={self.element_bits}（ADR-056 §A.2-3）")
        # (b) 恰一 output
        outputs = [n for n, c in self.tensors_cfg.items() if c.get("role") == "output"]
        if len(outputs) != 1:
            raise ValueError(
                f"[TYPE_LAYOUT_UNSUPPORTED] op '{self.name}' 准入要求恰一 output 张量，"
                f"实得 {outputs}（ADR-056 §A.2-4 单输出式）")
        # (c)+(d) 逐张量 role→stride 一致 + length 必填
        for tname, tcfg in self.tensors_cfg.items():
            role = tcfg.get("role")
            if role not in _ROLE_STRIDE:
                raise ValueError(
                    f"[TYPE_LAYOUT_UNSUPPORTED] op '{self.name}' 张量 '{tname}' "
                    f"role={role!r} 越出 {sorted(_ROLE_STRIDE)}（ADR-056 §A.2-4）")
            expect_stride = _ROLE_STRIDE[role]
            got_stride = tcfg.get("stride", expect_stride)
            if got_stride != expect_stride:
                raise ValueError(
                    f"[TYPE_LAYOUT_UNSUPPORTED] op '{self.name}' 张量 '{tname}' "
                    f"role={role} 要求 stride={expect_stride}，实得 {got_stride}"
                    f"（ADR-056 §A.2-4：输入 stride=1、param/constant stride=0）")
            length = tcfg.get("length")
            if (length is None or not isinstance(length, int)
                    or isinstance(length, bool) or length <= 0):
                raise ValueError(
                    f"[TYPE_LAYOUT_UNSUPPORTED] op '{self.name}' 张量 '{tname}' "
                    f"length={length!r}——准入算子 length 必填且为 >0 的 int"
                    f"（bank 内 UR word 静态分配上界，ADR-056 §F.5）")


    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        return self.tensors_cfg

    @property
    def element_bits(self) -> int:
        # 🛸 [V6.5 FIX] 动态解析 YAML 精度配置，解决 FP16/INT8 等多精度混合场景下的硬编码问题
        for name, config in self.tensors_cfg.items():
            prec = config.get("precision") or config.get("element_bits")
            if prec:
                if isinstance(prec, int): return prec
                prec = str(prec).lower()
                if "16" in prec: return 16
                if "8" in prec: return 8
                if "4" in prec: return 4
        return 32

    def get_dfg(self, spec: Any = None) -> List[Any]:
        # 1. 解析公式
        expr = self.formula
        symbols = {str(s): s for s in expr.free_symbols}
        
        # 2. 映射符号到物理元数据
        inputs_map = {}
        for sym_name, sym_obj in symbols.items():
            if sym_name in self.tensors_cfg:
                inputs_map[sym_name] = sym_obj
            else:
                # 处理未显式定义的符号
                inputs_map[sym_name] = sym_obj
        
        inputs_meta = self.build_inputs_meta(inputs_map, spec)
        
        # 🛸 [V5.0 FIX] 自动或显式为常量分配 Bank (处理字面量)
        if "constants" not in inputs_meta:
            const_cfg = self.tensors_cfg.get("constants")
            if const_cfg:
                const_bank = const_cfg["bank"]
                offset = const_cfg.get("offset", 0)
            else:
                const_bank = self._find_free_bank(spec)
                offset = 0
                
            bank_depth = self._get_hw_param(spec, "PARAM_UR_BANK_DEPTH", 256)
            if isinstance(const_bank, list):
                logical_addr = const_bank[0] * bank_depth + offset
                logical_addr_list = [b * bank_depth + offset for b in const_bank]
            else:
                logical_addr = const_bank * bank_depth + offset
                logical_addr_list = None
                
            inputs_meta["constants"] = {
                "logical_addr": logical_addr,
                "stride": 0,
                "role": "constant"
            }
            if logical_addr_list is not None:
                inputs_meta["constants"]["logical_addr_list"] = logical_addr_list
        
        # 3. 编译
        out_name = "y"
        ops = self.compile_with_adpc(
            expr,
            inputs_meta,
            out_name=out_name,
            out_addr=self.get_bank_addr(out_name, spec),
            spec=spec
        )

        # ADR-018 v7 Stage 1 X · parallel_dim=N：把 broadcast 输入的 UR_READ 后插 SHIFTUP MIROp。
        # ADR-023 D2(B)：SMC_0 bank0 单源（ur-r0 读出 a[i]），smc_id_en=PARAM_SMC_NUM-1 广播；
        # sim 由 cluster 同拍 fan-out 到全部使能 SMC（旧 OQ-1「固定 SMC_0 + 逐级 chain」语义已取代）。
        if self.parallel_dim == "N":
            ops = self._inject_shiftup_broadcast(ops, spec)

        return ops

    def _inject_shiftup_broadcast(self, ops: List[Any], spec: Any) -> List[Any]:
        """
        ADR-018 v7 D4 Stage 1 X 模式 (Output-stationary by N) DFG 后处理：
        对 smc_layout=broadcast 的 input UR_READ 插入 SHIFTUP MIROp，
        consumer RTOVR 的 sel 改为 SHIFTUP_D=29（rtovr_config.yaml consumers_dr.29）。

        前置：tensors_cfg[name].smc_layout == "broadcast"。
        D5.c emitter 后续在 SMC_0..smc_id_en 各份 hex 同 cycle emit 此 SHIFTUP（沿途
        SMC_k>0 data 字段填 0，硬件 cru_shiftup_i 接上游 chain，本地 data 忽略）。
        """
        from ir.mir.mir_op import MIROp
        from driver.hw_resource import ResourceType as RT

        smc_num = self._get_hw_param(spec, "PARAM_SMC_NUM", 32)
        smc_id_en = smc_num - 1
        broadcast_names = {
            name for name, cfg in self.tensors_cfg.items()
            if cfg.get("smc_layout") == "broadcast"
        }
        if not broadcast_names:
            return ops

        SHIFTUP_D_SEL = 29  # rtovr_config.yaml consumers_dr.29 = SHIFTUP_D

        ur_to_shiftup: Dict[str, str] = {}
        new_ops = list(ops)
        # sim/smc_simulator/core/kernel.py:508 硬连 SHIFTUP 的 local data = ur_bank.get_read_data(0)
        # = ur-r0 端口。第 1 个 broadcast 输入 pin 到 ur-r0 (iq_id=30) 保证 SHIFTUP 拿到真实 data；
        # 多 broadcast (如 matmul a0..a3) sim 端无法正确路由，需 ADR-020 sim 重构。
        # 本函数不阻塞 DFG 注入 (axiom-trigger 测试仍可用)；e2e 多 broadcast 已知 sub-7 退化。
        UR_R0_IQ_ID = spec.config.get("iq_registry", {}).get("UR_READ", 30) + 0
        for op in ops:
            if (op.op_type.upper() == "UR_READ"
                    and op.metadata.get("meta_name") in broadcast_names
                    and op.metadata.get("role") == "input"):
                if len(ur_to_shiftup) == 0:
                    op.metadata["iq_id"] = UR_R0_IQ_ID  # pin only first broadcast UR_READ
                sup_id = f"shiftup_{op.trace_id}"
                sup_op = MIROp(
                    op_type="SHIFTUP", logic_cycle=0, trace_id=sup_id,
                    metadata={
                        "depends_on": [op.trace_id],
                        "iq_type": RT.SHIFTUP,
                        "broadcast": 1,
                        "smc_id_en": smc_id_en,
                        "smc_id": 0,  # OQ-1: 数据源固定 SMC_0
                        "math": f"shiftup_broadcast({op.metadata.get('meta_name')})",
                        "dep_port": "D",
                    },
                )
                new_ops.append(sup_op)
                ur_to_shiftup[op.trace_id] = sup_id

        if not ur_to_shiftup:
            return ops

        shiftup_ids = set(ur_to_shiftup.values())

        # Rewrite consumer deps（跳过 SHIFTUP 自身——它合法地 dep on UR_READ）
        for op in new_ops:
            if op.trace_id in shiftup_ids:
                continue
            deps = op.metadata.get("depends_on", [])
            if any(d in ur_to_shiftup for d in deps):
                op.metadata["depends_on"] = [
                    ur_to_shiftup[d] if d in ur_to_shiftup else d
                    for d in deps
                ]
                if op.op_type.upper() == "RTOVR":
                    op.metadata["sel"] = SHIFTUP_D_SEL

        # Rebuild rtovr_deps from scratch（清旧 stale UR_READ 条目，避免与新 SHIFTUP 条目并存
        # 双重约束 t_fpmul == t_ur_read+lat+1 vs t_shiftup+lat+1）
        for op in new_ops:
            if "rtovr_deps" in op.metadata:
                op.metadata["rtovr_deps"] = {}
        for op in new_ops:
            if op.op_type.upper() != "RTOVR":
                continue
            prod_ids = op.metadata.get("depends_on", [])
            if not prod_ids:
                continue
            prod_id = prod_ids[0]
            for consumer in new_ops:
                if op.trace_id in consumer.metadata.get("depends_on", []):
                    consumer.metadata.setdefault("rtovr_deps", {})[prod_id] = {
                        "dvr": op.metadata.get("dvr", -1),
                        "sel": op.metadata.get("sel", -1),
                    }

        return new_ops

    def get_golden(self, input_data: Dict[str, np.ndarray], vector_lanes: int) -> Dict[str, Any]:
        # ADR-018 v7 Stage 1 X (task #18 sub-7): parallel_dim=N 多 SMC golden
        # partitioned 输入 2D 时，per-SMC 各算一份 y[k] = f(a, b[k], c[k], ...)。
        if self.parallel_dim == "N":
            partitioned_2d = any(
                isinstance(v, np.ndarray) and v.ndim == 2
                for v in input_data.values()
            )
            if partitioned_2d:
                return self._get_golden_parallel_n(input_data)

        if self.name == "matmul":
            # [V8.0] 对齐真实硬件行为：
            # 每次外层迭代（vector_lanes 行）执行 4 次独立计算（step k, k=0..3）：
            #   FPMUL: [ak*bk] 4-lane 向量乘法
            #   ADDTREE 通道0: S0[0]+S0[1]+S0[2]+S0[3]+old_0
            # pipeline latency (4 cycles) 自然将相邻 step 的 old_0 隔离为 0，
            # 因此 4 个输出位置各自独立：
            #   y[start+k] = sum(ak[start:end] * bk[start:end])  (k=0..3)
            a0 = input_data["a0"]
            a1 = input_data["a1"]
            a2 = input_data["a2"]
            a3 = input_data["a3"]
            b0 = input_data["b0"]
            b1 = input_data["b1"]
            b2 = input_data["b2"]
            b3 = input_data["b3"]

            n_rows = len(a0)
            golden_y = np.zeros(n_rows, dtype=np.float32)
            for iter_idx in range(n_rows // vector_lanes):
                start = iter_idx * vector_lanes
                end   = start + vector_lanes
                golden_y[start + 0] = np.sum(a0[start:end] * b0[start:end])
                golden_y[start + 1] = np.sum(a1[start:end] * b1[start:end])
                golden_y[start + 2] = np.sum(a2[start:end] * b2[start:end])
                golden_y[start + 3] = np.sum(a3[start:end] * b3[start:end])
            return {"y": golden_y, "intermediates": []}

        expr = self.formula
        # 获取排序后的符号列表
        sorted_symbols = sorted([str(s) for s in expr.free_symbols])
        func = sp.lambdify([sp.Symbol(s) for s in sorted_symbols], expr, "numpy")
        
        # 提取参数，支持标量广播
        args = {}
        for s in sorted_symbols:
            val = input_data.get(s)
            if val is not None:
                args[s] = val
        
        golden_y = func(**args)
        
        # 🛸 [ALGORITHM OPTIMIZATION] 支持多输出/矩阵算子：转换形如 (num_outputs, 1, n_rows) 的矩阵结果为 (n_rows, num_outputs) 布局
        if isinstance(golden_y, list) or (hasattr(golden_y, "ndim") and golden_y.ndim > 1):
            arr = np.array(golden_y)
            if arr.ndim == 3 and arr.shape[1] == 1:
                golden_y = np.squeeze(arr, axis=1).T
        
        return {
            "y": golden_y, 
            "intermediates": [] 
        }

    def _get_golden_parallel_n(self, input_data: Dict[str, np.ndarray]) -> Dict[str, Any]:
        """ADR-018 v7 Stage 1 X: parallel_dim=N per-SMC golden = f(a, b[k], c[k], ...)。
        broadcast 输入 (1D) 复用所有 k；partitioned 输入 (2D shape=(smc_num,n_rows)) 取第 k 切片。
        Returns {"y": np.ndarray shape (smc_num, n_rows), "intermediates": []}.
        """
        expr = self.formula
        sorted_syms = sorted(str(s) for s in expr.free_symbols)
        smc_num = None
        for s in sorted_syms:
            v = input_data.get(s)
            if isinstance(v, np.ndarray) and v.ndim == 2:
                smc_num = v.shape[0]
                break
        assert smc_num is not None, "_get_golden_parallel_n 需至少 1 个 partitioned 2D 输入"

        func = sp.lambdify([sp.Symbol(s) for s in sorted_syms], expr, "numpy")
        goldens = []
        for k in range(smc_num):
            args = {}
            for s in sorted_syms:
                v = input_data.get(s)
                if v is None:
                    continue
                args[s] = v[k] if (isinstance(v, np.ndarray) and v.ndim == 2) else v
            goldens.append(func(**args))
        return {"y": np.array(goldens, dtype=self.dtype), "intermediates": []}

    def prepare_input(self, n_rows: int, smc_num: int = 1) -> Dict[str, np.ndarray]:
        if self.parallel_dim == "N" and smc_num > 1:
            return self._prepare_input_parallel_n(n_rows, smc_num)
        inputs = {}
        addr_to_data = {}
        for name, cfg in self.tensors_cfg.items():
            if cfg["role"] == "output": continue
            
            bank_key = tuple(cfg["bank"]) if isinstance(cfg["bank"], list) else cfg["bank"]
            addr_key = (bank_key, cfg.get("offset", 0))
            if addr_key in addr_to_data:
                inputs[name] = addr_to_data[addr_key]
                continue
            
            t_cfg = self.test_cfg.get(name, {})
            if isinstance(t_cfg, list):
                # 固定参数
                data = np.array(t_cfg).astype(self.dtype)
            elif isinstance(t_cfg, dict) and "range" in t_cfg:
                # 范围生成
                r = t_cfg["range"]
                data = np.linspace(r[0], r[1], n_rows).astype(self.dtype)
            else:
                # 默认随机
                data = np.random.randn(n_rows).astype(self.dtype)
                
            inputs[name] = data
            addr_to_data[addr_key] = data
        return inputs

    def _prepare_input_parallel_n(self, n_rows: int, smc_num: int) -> Dict[str, np.ndarray]:
        """ADR-018 v7 Stage 1 X: parallel_dim=N 数据形态。
        - broadcast tensor: 1D shape (n_rows,) 全 SMC 共享
        - partitioned tensor: 2D shape (smc_num, n_rows) 每 SMC 独立
        固定 seed 确保 hw-parity bit-perfect 对比稳定（matmul_op 同模式）。
        """
        rng = np.random.default_rng(0)
        inputs: Dict[str, np.ndarray] = {}
        for name, cfg in self.tensors_cfg.items():
            if cfg.get("role") == "output":
                continue
            smc_layout = cfg.get("smc_layout")
            if smc_layout == "broadcast":
                inputs[name] = rng.uniform(1.0, 5.0, n_rows).astype(self.dtype)
            elif smc_layout == "partitioned":
                inputs[name] = rng.uniform(1.0, 5.0, (smc_num, n_rows)).astype(self.dtype)
            else:
                # smc_layout 缺省 (legacy 字段)：作 broadcast 处理（保守不破单 SMC 默认）
                inputs[name] = rng.uniform(1.0, 5.0, n_rows).astype(self.dtype)
        return inputs

    def inject_data_per_smc(
        self, programs: List[Any], input_data: Dict[str, np.ndarray],
        vector_lanes: int, spec: Any = None,
    ):
        """ADR-018 v7 D5.d task #18 sub-7: 多 SMC 数据注入。
        - broadcast 输入：只注入 programs[0]（OQ-1 数据源 SMC_0）；SMC_k>0 SHIFTUP 由 chain 供值
        - partitioned 输入：每 SMC k 注入 input_data[name][k]
        - output role：跳过（仿真器写入）
        """
        for name, cfg in self.tensors_cfg.items():
            if cfg.get("role") == "output":
                continue
            data = input_data.get(name)
            if data is None:
                continue
            addr_base = self.get_bank_addr(name, spec)
            stride = cfg.get("stride", 1 if cfg.get("role") == "input" else 0)
            smc_layout = cfg.get("smc_layout")

            if smc_layout == "broadcast":
                assert data.ndim == 1, (
                    f"broadcast tensor {name} 应 1D, got shape {data.shape}"
                )
                programs[0].inject_vector(addr_base, data, stride=stride)
            elif smc_layout == "partitioned":
                assert data.ndim == 2 and data.shape[0] == len(programs), (
                    f"partitioned tensor {name} 应 2D shape=({len(programs)}, n_rows), "
                    f"got {data.shape}"
                )
                for k, prog in enumerate(programs):
                    prog.inject_vector(addr_base, data[k], stride=stride)
            else:
                # 未声明 smc_layout：复制到所有 SMC（保守兼容）
                for prog in programs:
                    if data.ndim == 1:
                        prog.inject_vector(addr_base, data, stride=stride)

    def inject_data(self, program: Any, input_data: Dict[str, np.ndarray], vector_lanes: int, spec: Any = None):
        # 1. 注入常规张量
        super().inject_data(program, input_data, vector_lanes, spec)
        
        # 2. 🛸 [V6.3 FIX] 统一且鲁棒的编译期常量注入
        # 弃用老旧的手动 SymPy 公式字面量提取，直接查询编译器实际使用的 const_pool，
        # 并通过 self.ops 检索其实际分配的 logical_addr 地址进行内存注入，
        # 完美解决内核底层动态生成常量（如 Abs 展开引入的 -1.0）导致的漏注/内存污染问题
        if hasattr(self, "ops") and hasattr(self, "compiler") and hasattr(self.compiler, "const_pool"):
            op_map = {op.trace_id: op for op in self.ops}
            for val_f, node_id in self.compiler.const_pool.items():
                if node_id in op_map:
                    node = op_map[node_id]
                    if "logical_addr" in node.metadata:
                        addr = node.metadata["logical_addr"]
                        program.inject_broadcast(addr, val_f)
