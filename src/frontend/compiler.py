import sympy as sp
from typing import List, Dict, Any
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS

# 🛸 [V6.0] 引入算子库系统
from .kernels.registry import KernelRegistry
import frontend.kernels.standard_kernels # 确保标准算子已注册

class GraphCompiler:
    """
    AIACC 前端编译器 (V6.0 - 算子库解耦版)。
    主编译器现在仅负责图拓扑管理与资源分配，具体的数学转换逻辑已下沉至 kernels 目录。
    """
    def __init__(self):
        self.b = MIROpBuilder()
        self.mir_env = {}
        self.slot_map = {
            "FPMUL": [PS.FPMUL_S0, PS.FPMUL_S1],
            "FPADD": [PS.FPADD_S0, PS.FPADD_S1],
            "INTADD": [PS.INTADD_S0, PS.INTADD_S1, PS.INTADD_S2],
            "LOGIC": [PS.LOGIC_S0, PS.LOGIC_S1],
            "FPTOFP": [PS.FPTOFP_S],
            "INTTOINT": [PS.INTTOINT_S],
            "FPTOINT": [PS.FPTOINT_S],
            "INTTOFP": [PS.INTTOFP_S],
            "INT8TOFP16": [PS.INT8TOFP16_S0, PS.INT8TOFP16_S1],
            "UR_WRITE": [PS.UR_W0_S, PS.UR_W1_S, PS.UR_W2_S, PS.UR_W3_S]
        }
        self.rt_cnt = 0
        self.inl_cnt = 0
        self.cse_vars = set()

    def _route(self, nid: str, target_slot: PS, dep_port: str = None):
        self.rt_cnt += 1
        uid = f"rt_{nid}_{target_slot.name}_{self.rt_cnt}"[:64]
        kwargs = {"dep_port": dep_port} if dep_port else {}
        return self.b.make_op("RTOVR", uid, target_slot, [nid], **kwargs)

    def compile_tensor(self, inputs, formula, shape: List[int], out_name="out_0") -> List:
        flat_size = 1
        for s in shape: flat_size *= s
        if flat_size % 4 != 0: raise ValueError(f"Tensor size {flat_size} must be 4-aligned")
        
        simd_blocks = flat_size // 4
        if len(shape) >= 2:
            inner_blocks = shape[-1] // 4
            self.loop_context = {
                "inner_loops": inner_blocks - 1,
                "outer_loops": shape[-2] - 1,
                "inner_inc": 1,
                "outer_inc": inner_blocks 
            }
        else:
            self.loop_context = {"inner_loops": simd_blocks - 1, "outer_loops": 0, "inner_inc": 1, "outer_inc": 0}
        
        dfg = self.compile(inputs, formula, out_name)
        for op in dfg:
            op.metadata.update({"shape": shape, **self.loop_context})
        return dfg

    def compile(self, inputs, formula, out_name="out_0") -> List:
        self.b = MIROpBuilder()
        self.mir_env = {}
        self.cse_vars = set()
        self.const_pool = {} 
        self.inputs_meta = inputs
        opt_nodes, final_res = sp.cse(formula)
        
        write_slots = self.slot_map["UR_WRITE"]
        num_w_ports = len(write_slots)

        for i, (sym, expr) in enumerate(opt_nodes):
            self.cse_vars.add(sym)
            self._lower_expr(sym, expr)
            write_slot = write_slots[(1 + i) % num_w_ports]
            routed_res = self._route(self.mir_env[sym], write_slot)
            kwargs = {"role": "intermediate", "meta_name": str(sym)}
            if hasattr(self, "loop_context"): kwargs["inc"] = self.loop_context["inner_inc"]
            self.b.make_op("UR_WRITE", f"spill_{sym}", RT.UR_WRITE, [routed_res], **kwargs)
            
        for i, res_expr in enumerate(final_res):
            res_sym = sp.Symbol(f"{out_name}_{i}")
            if res_expr not in self.mir_env: self._lower_expr(res_sym, res_expr)
            else: self.mir_env[res_sym] = self.mir_env[res_expr]
            
            write_slot = write_slots[i % num_w_ports]
            routed_res = self._route(self.mir_env[res_sym], write_slot)
            kwargs = {"role": "output", "meta_name": out_name}
            if hasattr(self, "loop_context"): kwargs["inc"] = self.loop_context["inner_inc"]
            self.b.make_op("UR_WRITE", f"out_obj_{i}", RT.UR_WRITE, [routed_res], **kwargs)
            
        return self.b.get_dfg()

    def _lower_expr(self, sym, expr):
        """
        [ADPC V6.0] 内核化下沉入口。
        """
        if isinstance(expr, sp.Symbol):
            self.mir_env[sym] = self._get_op(expr)
            return
        if isinstance(expr, (sp.Float, sp.Integer, sp.Rational)):
            self.mir_env[sym] = self._get_const(expr)
            return

        lower_func = KernelRegistry.lookup(expr)
        if lower_func:
            lower_func(self, sym, expr)
            return

        raise NotImplementedError(f"Compiler Error: 未在算子库中找到匹配内核 {type(expr)}: {expr}")

    def _get_op(self, arg):
        # 🛸 [V6.1 FIX] 优先检查是否为 CSE 变量，强制执行 Reload 逻辑
        if arg in self.cse_vars:
            self.inl_cnt += 1
            return self.b.make_op("UR_READ", f"{arg}_rld_{self.inl_cnt}", RT.UR_READ, [f"spill_{arg}"], meta_name=str(arg), role="intermediate")
        
        if arg in self.mir_env: return self.mir_env[arg]
        if arg in self.inputs_meta or str(arg) in self.inputs_meta: return self._get_input(arg)
        
        if isinstance(arg, sp.Symbol): raise NameError(f"Unbound: {arg}")
        if isinstance(arg, (sp.Float, sp.Integer, sp.Rational)): return self._get_const(arg)
        
        self.inl_cnt += 1
        inline_sym = sp.Symbol(f"inl_{self.inl_cnt}")
        self._lower_expr(inline_sym, arg)
        return self.mir_env[inline_sym]

    def _get_const(self, val):
        val_f = float(val)
        if val_f in self.const_pool: return self.const_pool[val_f]
        nid = f"cinl_{len(self.const_pool)}_{val_f}".replace(".", "_").replace("-", "n")
        metadata = self.inputs_meta.get("constants", {}).copy()
        if hasattr(self, "loop_context"): metadata["inc"] = 0 
        m_name = metadata.pop("meta_name", "constants")
        node = self.b.make_op("UR_READ", nid, RT.UR_READ, role="constant", meta_name=m_name, **metadata)
        self.const_pool[val_f] = node
        return node
        
    def _get_input(self, sym):
        self.inl_cnt += 1
        new_id = f"{sym}_read_{self.inl_cnt}"
        kwargs = self.inputs_meta.get(sym, self.inputs_meta.get(str(sym), {})).copy()
        if hasattr(self, "loop_context"): kwargs["inc"] = self.loop_context["inner_inc"]
        m_name = kwargs.pop("meta_name", str(sym))
        return self.b.make_op("UR_READ", new_id, RT.UR_READ, [], meta_name=m_name, **kwargs)
