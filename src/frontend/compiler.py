import sympy as sp
from typing import List, Dict, Any
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS

class GraphCompiler:
    """
    AIACC 前端编译器：包含中端 (SymPy CSE 优化) 与硬件下降匹配器 (Hardware Lowering)
    """
    def __init__(self):
        self.b = MIROpBuilder()
        self.mir_env = {}
        
        # 预先定义每个算力单元在硬件中暴露出的目标插槽 (Slot)
        self.slot_map = {
            "FPMUL": [PS.FPMUL_S0, PS.FPMUL_S1],
            "FPADD": [PS.FPADD_S0, PS.FPADD_S1],
            "INTADD": [PS.INTADD_S0, PS.INTADD_S1, PS.INTADD_S2],
            "LOGIC": [PS.LOGIC_S0, PS.LOGIC_S1],
            "INT8TOFP16": [PS.INT8TOFP16_S0, PS.INT8TOFP16_S1],
            "UR_WRITE": [PS.UR_W0_S, PS.UR_W1_S, PS.UR_W2_S, PS.UR_W3_S]
        }
        self.rt_cnt = 0
        self.inl_cnt = 0
        self.cse_vars = set()

    def _route(self, nid: str, target_slot: PS):
        """路由下发：在数学操作数进计算器前，必须包一层硬件专属搬运指令 RTOVR"""
        self.rt_cnt += 1
        uid = f"rt_{nid}_{target_slot.name}_{self.rt_cnt}"[:25]
        return self.b.make_op("RTOVR", uid, target_slot, [nid])

    def compile(self, inputs: Dict[sp.Symbol, dict], formula, out_name="out") -> List:
        """
        编译主函数。将复杂的符号数学公式压缩、优化并装配为底层算子图。
        inputs: DFG 的输入口锚点，允许指派测试用的 kwargs (如 manual_bank)
        """
        self.inputs_meta = inputs
        # --- Stage 1: Sympy 中端图优化 (常数叠算，公共子代清除) ---
        opt_nodes, final_res = sp.cse(formula)
        
        # --- Stage 2.1: 装配环境已经委托给依赖注入 ---
        # 引脚的实例化将被懒汉式调用 (Lazy-load)，以确保每个引用的时间点离计算最近，避免物理寄存器生命周期失效
            
        # --- Stage 2.2: 下沉并实例化图的内部计算节点 ---
        for i, (sym, expr) in enumerate(opt_nodes):
            self.cse_vars.add(sym)
            self._lower_expr(sym, expr)
            # 突破点: 拥有超过1个下游子代的特征向量，必须转储(Spilled)出被覆写的 ALU FIFO
            write_slot = self.slot_map["UR_WRITE"][(1 + i) % 4]
            routed_res = self._route(self.mir_env[sym], write_slot)
            self.b.make_op("UR_WRITE", f"spill_{sym}", RT.UR_WRITE, [routed_res], role="intermediate", meta_name=str(sym))
            
        # --- Stage 2.3: 终极连线并汇出至输出寄存器 ---
        for i, res_expr in enumerate(final_res):
            res_sym = sp.Symbol(f"{out_name}_{i}")
            if res_expr not in self.mir_env: 
                self._lower_expr(res_sym, res_expr)
            else:
                self.mir_env[res_sym] = self.mir_env[res_expr]
                
            # 自动挑选可用的写出端口
            write_slot = self.slot_map["UR_WRITE"][i % 4]
            routed_res = self._route(self.mir_env[res_sym], write_slot)
            self.b.make_op("UR_WRITE", f"out_obj_{i}", RT.UR_WRITE, [routed_res], role="output", meta_name=out_name)
            
        return self.b.get_dfg()

    def _lower_expr(self, sym, expr):
        """AST 硬件特征探测匹配中心。"""
        # 直接符号传承
        if isinstance(expr, sp.Symbol):
            self.mir_env[sym] = self.mir_env[expr]
            return
            
        # 标量常量内联
        if isinstance(expr, (sp.Float, sp.Integer, sp.Rational)):
            val = float(expr)
            op = self.b.make_op("UR_READ", f"c_{sym}", RT.UR_READ, [], is_const=True, value=val, role="constant")
            self.mir_env[sym] = op
            return

        # 【特权通道】捕捉特制底层硬件指令 (Intrinsics)
        if isinstance(expr, sp.Function):
            func_name = str(expr.func)
            if hasattr(RT, func_name):
                rt = getattr(RT, func_name)
                # 分配该硬件单元专用的插槽接口 (无则Fallback用多路加的接口)
                slots = self.slot_map.get(func_name, [PS.FPMUL_S0, PS.FPMUL_S1]) 
                
                # 开始搬运操作数进专用口
                routed_args = []
                for i, arg in enumerate(expr.args):
                    if isinstance(arg, sp.Symbol) and arg in self.mir_env:
                        routed_args.append(self._route(self.mir_env[arg], slots[i]))
                    elif isinstance(arg, (sp.Float, sp.Integer, sp.Rational)):
                         # 将立即数实化为内存结点再搬入
                         c_node = self._get_const(arg)
                         routed_args.append(self._route(c_node, slots[i]))

                # 实例化此独有硬件算子
                op = self.b.make_op(func_name, str(sym), rt, routed_args)
                self.mir_env[sym] = op
                return

        # 匹配【乘法单元】 -> FPMUL
        if isinstance(expr, sp.Mul):
            args = list(expr.args)
            if len(args) == 2:
                r0 = self._route(self._get_op(args[0]), PS.FPMUL_S0)
                r1 = self._route(self._get_op(args[1]), PS.FPMUL_S1)
                self.mir_env[sym] = self.b.make_op("FPMUL", str(sym), RT.FPMUL, [r0, r1])
                return
            else:
                # 级联乘法
                current = self._get_op(args[0])
                for i in range(1, len(args)):
                    r0 = self._route(current, PS.FPMUL_S0)
                    r1 = self._route(self._get_op(args[i]), PS.FPMUL_S1)
                    current = self.b.make_op("FPMUL", f"{sym}_m{i}", RT.FPMUL, [r0, r1])
                self.mir_env[sym] = current
                return
                
        # 匹配【幂次运算 (展开为乘法)】 -> FPMUL 级联
        if isinstance(expr, sp.Pow):
            base, exp = expr.args
            if isinstance(exp, sp.Integer) and exp > 0:
                current = self._get_op(base)
                for i in range(1, int(exp)):
                    r0 = self._route(current, PS.FPMUL_S0)
                    r1 = self._route(self._get_op(base), PS.FPMUL_S1)
                    current = self.b.make_op("FPMUL", f"{sym}_pw{i}", RT.FPMUL, [r0, r1])
                self.mir_env[sym] = current
                return

        # 匹配【标量加法与向量逐元素加法】 -> FPADD 级联
        if isinstance(expr, sp.Add):
            args = list(expr.args)
            op_list = [self._get_op(arg) for arg in args]
            
            # 级联逐元素加法 (FPADD)
            current = op_list[0]
            for i in range(1, len(op_list)):
                r0 = self._route(current, PS.FPADD_S0)
                r1 = self._route(op_list[i], PS.FPADD_S1)
                current = self.b.make_op("FPADD", f"{sym}_a{i}", RT.FPADD, [r0, r1])
            self.mir_env[sym] = current
            return

        # 兜底：未知表达式
        raise NotImplementedError(f"Compiler Error: 不支持的 SymPy 表达式类型 {type(expr)}: {expr}. "
                                  f"请在 tests/operators/ 中检查 DFG 定义是否超出了当前硬件能力。")
            
    def _get_op(self, arg):
        """安全的取值助手"""
        if arg in self.inputs_meta:
            return self._get_input(arg)
            
        if arg in self.cse_vars:
            # 读取被释放到 SRAM 的长期活状态值
            self.inl_cnt += 1
            new_id = f"{arg}_reload_{self.inl_cnt}"
            # 加入依赖锁 spill_arg 确保读取必在写入之后!
            return self.b.make_op("UR_READ", new_id, RT.UR_READ, [f"spill_{arg}"], meta_name=str(arg), role="intermediate")
            
        if arg in self.mir_env: return self.mir_env[arg]
        if isinstance(arg, (sp.Float, sp.Integer, sp.Rational)):
            return self._get_const(arg)
        
        # 递归下沉剩余的 inline 表达式
        self.inl_cnt += 1
        inline_sym = sp.Symbol(f"inl_{self.inl_cnt}")
        self._lower_expr(inline_sym, arg)
        return self.mir_env[inline_sym]

    def _get_const(self, expr):
        """常数匿名补丁"""
        val = float(expr)
        self.inl_cnt += 1
        new_id = f"cinl_{str(val).replace('.', '_')}_{self.inl_cnt}"[:25]
        op = self.b.make_op("UR_READ", new_id, RT.UR_READ, [], is_const=True, value=val, role="constant")
        return op
        
    def _get_input(self, sym):
        """为输入张量产生一个严格贴近使用时间的全新读取操作，绕开物理引脚 Liveness 覆盖"""
        self.inl_cnt += 1
        new_id = f"{sym}_read_{self.inl_cnt}"
        kwargs = self.inputs_meta[sym]
        return self.b.make_op("UR_READ", new_id, RT.UR_READ, [], meta_name=str(sym), **kwargs)
