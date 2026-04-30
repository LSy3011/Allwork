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
        """路由下发：在数学操作数进计算器前，必须包一层硬件专属搬运指令 RTOVR"""
        self.rt_cnt += 1
        uid = f"rt_{nid}_{target_slot.name}_{self.rt_cnt}"[:64]
        kwargs = {"dep_port": dep_port} if dep_port else {}
        return self.b.make_op("RTOVR", uid, target_slot, [nid], **kwargs)

    def compile_tensor(self, inputs, formula, shape: List[int], out_name="out_0") -> List:
        """
        张量加速编译：支持多维嵌套循环 (N-D Nesting)。
        基于 ISA V5 协议，目前支持最高 2 级硬件加速 (Level 0 Pulse, Level 1 Loop)。
        """
        # 1. 自动计算硬件迭代次数 (SIMD-4)
        flat_size = 1
        for s in shape: flat_size *= s
        
        if flat_size % 4 != 0:
             raise ValueError(f"Tensor size {flat_size} must be 4-aligned for SIMD-4")
        
        simd_blocks = flat_size // 4
        
        # 判定循环维度：若为多维 (如 [H, W]), W 为内层，H 为外层
        # 对于 [4, 16] -> inner=4 blocks (16/4), outer=4
        if len(shape) >= 2:
            inner_blocks = shape[-1] // 4
            outer_loops = shape[-2]
            # 这里的协议：Level 0 为内层，Level 1 为外层
            self.loop_context = {
                "inner_loops": inner_blocks - 1,
                "outer_loops": outer_loops - 1,
                "inner_inc": 1,
                "outer_inc": inner_blocks # 外层回跳时的基址偏移
            }
        else:
            self.loop_context = {
                "inner_loops": simd_blocks - 1,
                "outer_loops": 0,
                "inner_inc": 1,
                "outer_inc": 0
            }
        
        # 3. 调用单点编译生成 DFG 内核
        dfg = self.compile(inputs, formula, out_name)
        
        # 4. 后处理：注入 EMBED/LOOPS/EMBED_END 物理标记
        # 🛸 V5.1 升级：全节点标记逻辑，由 Compactor 自动处理各 IQ 的 Header/Footer 缝合
        for op in dfg:
            op.metadata["shape"] = shape
            op.metadata["inner_loops"] = self.loop_context["inner_loops"]
            op.metadata["outer_loops"] = self.loop_context.get("outer_loops", 0)
            op.metadata["outer_inc"] = self.loop_context.get("outer_inc", 0)
                
        return dfg

    def compile(self, inputs, formula, out_name="out_0") -> List:
        """
        编译主函数。将复杂的符号数学公式压缩、优化并装配为底层算子图。
        inputs: DFG 的输入口锚点，允许指派测试用的 kwargs (如 manual_bank)
        """
        # 🛸 [ADPC V4.0] 关键修复：重排时清空中间态环境，防止节点在多轮 Fuzz 中累积爆炸。
        from ir.mir.builder import MIROpBuilder
        self.b = MIROpBuilder()
        self.mir_env = {}
        self.cse_vars = set()
        
        # 🛸 V4.0 常量池缓存：避免重复生成相同数值的 UR_READ 节点
        self.const_pool = {} 
        
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
            kwargs = {"role": "intermediate", "meta_name": str(sym)}
            if hasattr(self, "loop_context"):
                kwargs["inc"] = self.loop_context["inner_inc"]
            self.b.make_op("UR_WRITE", f"spill_{sym}", RT.UR_WRITE, [routed_res], **kwargs)
            
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
            kwargs = {"role": "output", "meta_name": out_name}
            if hasattr(self, "loop_context"):
                kwargs["inc"] = self.loop_context["inner_inc"]
            self.b.make_op("UR_WRITE", f"out_obj_{i}", RT.UR_WRITE, [routed_res], **kwargs)
            
        return self.b.get_dfg()

    def _lower_expr(self, sym, expr):
        """AST 硬件特征探测匹配中心。"""
        # 直接符号传承 (需通过 _get_op 兼容输入与 CSE 变量)
        if isinstance(expr, sp.Symbol):
            self.mir_env[sym] = self._get_op(expr)
            return
            
        # 标量常量内联
        if isinstance(expr, (sp.Float, sp.Integer, sp.Rational)):
            val = float(expr)
            op = self._get_const(val)
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
                
        # 匹配【幂次运算 (映射为乘法或倒数泰勒展开)】
        if isinstance(expr, sp.Pow):
            base, exp = expr.args
            if isinstance(exp, sp.Integer):
                 exp_val = int(exp)
                 if exp_val > 0:
                     # 级联 FPMUL
                     current = self._get_op(base)
                     for i in range(1, exp_val):
                         r0 = self._route(current, PS.FPMUL_S0)
                         r1 = self._route(self._get_op(base), PS.FPMUL_S1)
                         current = self.b.make_op("FPMUL", f"{sym}_pw{i}", RT.FPMUL, [r0, r1])
                     self.mir_env[sym] = current
                     return
                 elif exp_val == -1:
                     # 🛸 [ADPC V4.0] 64 阶倍增泰勒展开 (Binary Power Series V3.0)
                     # 数学等等价于 64 阶: (1-dx)(1+dx^2)(1+dx^4)...(1+dx^32)
                     # 拓扑表现为线性链，深度约 24 拍，远优于自回归结构的 NR 方案。
                     x_node = self._get_op(base)
                     one_node = self._get_const(sp.Float(1.0))
                     
                     # dx = x - 1.0 (泰勒展开基础)
                     r_x = self._route(x_node, PS.FPADD_S0)
                     r_o1 = self._route(one_node, PS.FPADD_S1)
                     dx = self.b.make_op("FPADD", f"{sym}_dx", RT.FPADD, [r_x, r_o1], SUB=1)
                     
                     # 初始值 y = 1.0 - dx
                     r_o2 = self._route(one_node, PS.FPADD_S0)
                     r_dx1 = self._route(dx, PS.FPADD_S1)
                     y = self.b.make_op("FPADD", f"{sym}_y1", RT.FPADD, [r_o2, r_dx1], SUB=1)
                     
                     # 二进制倍增提升阶数: 2 -> 4 -> 8 -> 16 -> 32 -> 64 (精度 10^-7 级)
                     curr_dx_pow = dx
                     for i in range(6):
                         # dx_pow_next = dx_pow * dx_pow
                         r_p0 = self._route(curr_dx_pow, PS.FPMUL_S0)
                         r_p1 = self._route(curr_dx_pow, PS.FPMUL_S1)
                         curr_dx_pow = self.b.make_op("FPMUL", f"{sym}_p{i}", RT.FPMUL, [r_p0, r_p1])
                         
                         # term = 1.0 + dx_pow_next
                         r_o = self._route(one_node, PS.FPADD_S0)
                         r_p = self._route(curr_dx_pow, PS.FPADD_S1)
                         term = self.b.make_op("FPADD", f"{sym}_t{i}", RT.FPADD, [r_o, r_p])
                         
                         # y_next = y * term
                         r_y = self._route(y, PS.FPMUL_S0)
                         r_t = self._route(term, PS.FPMUL_S1)
                         y = self.b.make_op("FPMUL", f"{sym}_res{i}", RT.FPMUL, [r_y, r_t])
                     
                     self.mir_env[sym] = y
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

        # 匹配【逐元素 Max 对比选择】 -> FPADD(ST) + LOGIC(SEL)
        if isinstance(expr, sp.Max):
            args = list(expr.args)
            if len(args) == 2:
                # 状态生成：FPADD 比较 (a > b?)
                # a - b > 0 ?
                a = self._get_op(args[0])
                b = self._get_op(args[1])
                r0_cmp = self._route(a, PS.FPADD_S0)
                r1_cmp = self._route(b, PS.FPADD_S1)
                cmp = self.b.make_op("FPADD", f"{sym}_cmp", RT.FPADD, [r0_cmp, r1_cmp], 
                                     UPDATE_ST=1, SUB=1, comment=f"CMP {args[0]} > {args[1]}")
                
                # 逻辑选择：LOGIC SELECT
                st_wire = self._route(cmp, PS.LOGIC_ST, dep_port="ST") # 显式拉取状态端子
                r0_sel = self._route(a, PS.LOGIC_S0)
                r1_sel = self._route(b, PS.LOGIC_S1)
                
                self.mir_env[sym] = self.b.make_op("LOGIC", str(sym), RT.LOGIC, [r0_sel, r1_sel, st_wire], 
                                                   OP_TYPE="SELECT_GT", VALID=1, comment=f"MAX({args[0]}, {args[1]})")
                return

        # 匹配【逐元素 Min 对比选择】 -> FPADD(ST) + LOGIC(SEL)
        if isinstance(expr, sp.Min):
            args = list(expr.args)
            if len(args) == 2:
                a = self._get_op(args[0])
                b = self._get_op(args[1])
                r0_cmp = self._route(a, PS.FPADD_S0)
                r1_cmp = self._route(b, PS.FPADD_S1)
                cmp = self.b.make_op("FPADD", f"{sym}_cmp", RT.FPADD, [r0_cmp, r1_cmp], 
                                     UPDATE_ST=1, SUB=1, comment=f"CMP {args[0]} > {args[1]}")
                
                st_wire = self._route(cmp, PS.LOGIC_ST, dep_port="ST")
                r0_sel = self._route(a, PS.LOGIC_S0)
                r1_sel = self._route(b, PS.LOGIC_S1)
                
                # SELECT_LS (Less Than) for min: if a < b return a else b
                self.mir_env[sym] = self.b.make_op("LOGIC", str(sym), RT.LOGIC, [r0_sel, r1_sel, st_wire], 
                                                   OP_TYPE="SELECT_LS", VALID=1, comment=f"MIN({args[0]}, {args[1]})")
                return

        # 匹配【绝对值】 -> Max(x, -x)
        if isinstance(expr, sp.Abs):
            arg = expr.args[0]
            # Abs(x) = Max(x, -x)
            neg_x = -arg
            formula = sp.Max(arg, neg_x)
            self.mir_env[sym] = self._get_op(formula)
            return

        # 匹配【符号函数】 -> sign(x) = SELECT_GT(1.0, SELECT_LS(-1.0, 0.0))
        if isinstance(expr, sp.sign):
             x = self._get_op(expr.args[0])
             one = self._get_const(1.0)
             neg_one = self._get_const(-1.0)
             zero = self._get_const(0.0)
             
             # 1. cmp1 = x - 0.0 (判断正负)
             r0_cmp1 = self._route(x, PS.FPADD_S0)
             r1_cmp1 = self._route(zero, PS.FPADD_S1)
             cmp1 = self.b.make_op("FPADD", f"{sym}_cmp1", RT.FPADD, [r0_cmp1, r1_cmp1], UPDATE_ST=1, SUB=1)
             st_wire1 = self._route(cmp1, PS.LOGIC_ST, dep_port="ST")
             
             # 2. SELECT_LS(neg_one, zero) -> if x < 0 then -1 else 0
             s0_ls = self._route(neg_one, PS.LOGIC_S0)
             s1_ls = self._route(zero, PS.LOGIC_S1)
             ls_node = self.b.make_op("LOGIC", f"{sym}_ls", RT.LOGIC, [s0_ls, s1_ls, st_wire1], OP_TYPE="SELECT_LS", VALID=1)
             
             # 3. SELECT_GT(one, ls_node) -> if x > 0 then 1 else (if x < 0 then -1 else 0)
             r0_gt = self._route(one, PS.LOGIC_S0)
             r1_gt = self._route(ls_node, PS.LOGIC_S1)
             st_wire2 = self._route(cmp1, PS.LOGIC_ST, dep_port="ST") # 复用 cmp1 状态
             self.mir_env[sym] = self.b.make_op("LOGIC", str(sym), RT.LOGIC, [r0_gt, r1_gt, st_wire2], OP_TYPE="SELECT_GT", VALID=1)
             return

        # 匹配【向上/向下取整】 -> FPTOINT (Trunc) + SELECT
        if isinstance(expr, (sp.floor, sp.ceiling)):
             x = self._get_op(expr.args[0])
             one = self._get_const(1.0)
             
             # 1. Truncate
             r0_f32 = self._route(x, PS.FPTOINT_S)
             i32 = self.b.make_op("FPTOINT", f"{sym}_trunc", RT.FPTOINT, [r0_f32])
             r0_i32 = self._route(i32, PS.INTTOFP_S)
             f_trunc = self.b.make_op("INTTOFP", f"{sym}_recept", RT.INTTOFP, [r0_i32])
             
             # 2. Compare f_trunc with x
             r0_cmp = self._route(f_trunc, PS.FPADD_S0)
             r1_cmp = self._route(x, PS.FPADD_S1)
             cmp = self.b.make_op("FPADD", f"{sym}_cmp", RT.FPADD, [r0_cmp, r1_cmp], UPDATE_ST=1, SUB=1)
             st_wire = self._route(cmp, PS.LOGIC_ST, dep_port="ST")
             
             if isinstance(expr, sp.floor):
                 # floor: if f_trunc > x, then f_trunc - 1.0, else f_trunc
                 r0_sub = self._route(f_trunc, PS.FPADD_S0)
                 r1_sub = self._route(one, PS.FPADD_S1)
                 f_minus_1 = self.b.make_op("FPADD", f"{sym}_minus1", RT.FPADD, [r0_sub, r1_sub], SUB=1)
                 
                 s0_sel = self._route(f_minus_1, PS.LOGIC_S0)
                 s1_sel = self._route(f_trunc, PS.LOGIC_S1)
                 self.mir_env[sym] = self.b.make_op("LOGIC", str(sym), RT.LOGIC, [s0_sel, s1_sel, st_wire], OP_TYPE="SELECT_GT", VALID=1)
             else:
                 # ceiling: if f_trunc < x, then f_trunc + 1.0, else f_trunc
                 r0_add = self._route(f_trunc, PS.FPADD_S0)
                 r1_add = self._route(one, PS.FPADD_S1)
                 f_plus_1 = self.b.make_op("FPADD", f"{sym}_plus1", RT.FPADD, [r0_add, r1_add], SUB=0)
                 
                 s0_sel = self._route(f_plus_1, PS.LOGIC_S0)
                 s1_sel = self._route(f_trunc, PS.LOGIC_S1)
                 self.mir_env[sym] = self.b.make_op("LOGIC", str(sym), RT.LOGIC, [s0_sel, s1_sel, st_wire], OP_TYPE="SELECT_LS", VALID=1)
             return

        # 匹配【四舍五入】 -> floor(x + 0.5)
        if str(expr.func).lower() == "round":
             arg = expr.args[0]
             half = sp.Float(0.5)
             formula = sp.floor(arg + half)
             self.mir_env[sym] = self._get_op(formula)
             return

        # 匹配【逻辑比较】 -> FPADD(ST) + LOGIC(SEL 常数 1.0 / 0.0)
        # 支持: a > b, a >= b, a < b, a <= b, a == b, a != b
        if isinstance(expr, (sp.GreaterThan, sp.LessThan, sp.StrictGreaterThan, sp.StrictLessThan, sp.Equality, sp.Unequality)):
             a = self._get_op(expr.args[0])
             b = self._get_op(expr.args[1])
             r0_cmp = self._route(a, PS.FPADD_S0)
             r1_cmp = self._route(b, PS.FPADD_S1)
             cmp = self.b.make_op("FPADD", f"{sym}_cmp", RT.FPADD, [r0_cmp, r1_cmp], 
                                  UPDATE_ST=1, SUB=1, comment=f"CMP {expr}")
             
             st_wire = self._route(cmp, PS.LOGIC_ST, dep_port="ST")
             one = self._get_const(1.0)
             zero = self._get_const(0.0)
             op_type = "SELECT_GT" 
             r0_sel = self._route(one, PS.LOGIC_S0)
             r1_sel = self._route(zero, PS.LOGIC_S1)
             
             if isinstance(expr, sp.StrictGreaterThan): # a > b
                  op_type = "SELECT_GT"
             elif isinstance(expr, sp.GreaterThan): # a >= b (SymPy default for >=)
                  # a >= b  <=>  !(a < b)  <=> SELECT_LS(zero, one)
                  op_type = "SELECT_LS"
                  r0_sel, r1_sel = self._route(zero, PS.LOGIC_S0), self._route(one, PS.LOGIC_S1)
             elif isinstance(expr, sp.StrictLessThan): # a < b
                  op_type = "SELECT_LS"
             elif isinstance(expr, sp.LessThan): # a <= b
                  # a <= b <=> !(a > b) <=> SELECT_GT(zero, one)
                  op_type = "SELECT_GT"
                  r0_sel, r1_sel = self._route(zero, PS.LOGIC_S0), self._route(one, PS.LOGIC_S1)
             elif isinstance(expr, (sp.Eq, sp.Equality)): # a == b
                  op_type = "SELECT_EQ"
             elif isinstance(expr, (sp.Ne, sp.Unequality)): # a != b
                  # a != b <=> !(a == b) <=> SELECT_EQ(zero, one)
                  op_type = "SELECT_EQ"
                  r0_sel, r1_sel = self._route(zero, PS.LOGIC_S0), self._route(one, PS.LOGIC_S1)
             
             self.mir_env[sym] = self.b.make_op("LOGIC", str(sym), RT.LOGIC, [r0_sel, r1_sel, st_wire], 
                                                OP_TYPE=op_type, VALID=1, comment=str(expr))
             return

        # 匹配【逻辑非】 -> Logical Not(a) -> (a == 0.0) -> SELECT_EQ(1.0, 0.0)
        if isinstance(expr, sp.Not):
             a_node = self._get_op(expr.args[0])
             zero = self._get_const(0.0)
             one = self._get_const(1.0)
             # 1. cmp = a - 0.0
             r0_cmp = self._route(a_node, PS.FPADD_S0)
             r1_cmp = self._route(zero, PS.FPADD_S1)
             cmp = self.b.make_op("FPADD", f"{sym}_cmp", RT.FPADD, [r0_cmp, r1_cmp], 
                                  UPDATE_ST=1, SUB=1, comment="LOGIC_NOT_CMP")
             
             # 2. 从状态位选择 1.0 或 0.0
             st_wire = self._route(cmp, PS.LOGIC_ST, dep_port="ST")
             r0_sel = self._route(one, PS.LOGIC_S0)
             r1_sel = self._route(zero, PS.LOGIC_S1)
             self.mir_env[sym] = self.b.make_op("LOGIC", str(sym), RT.LOGIC, [r0_sel, r1_sel, st_wire], 
                                                OP_TYPE="SELECT_EQ", VALID=1, comment="NOT_SEL")
             return

        # 匹配【位运算】 -> LOGIC
        if isinstance(expr, (sp.And, sp.Or, sp.Xor)):
            args = list(expr.args)
            op_map = {sp.And: "AND", sp.Or: "OR", sp.Xor: "XOR", sp.Not: "NOT"}
            op_name = op_map[type(expr)]
            
            v0 = self._get_op(args[0], is_int=True)
            r0 = self._route(v0, PS.LOGIC_S0)
            if len(args) > 1:
                v1 = self._get_op(args[1], is_int=True)
                r1 = self._route(v1, PS.LOGIC_S1)
                self.mir_env[sym] = self.b.make_op("LOGIC", str(sym), RT.LOGIC, [r0, r1], OP_TYPE=op_name, VALID=1)
            else:
                self.mir_env[sym] = self.b.make_op("LOGIC", str(sym), RT.LOGIC, [r0], OP_TYPE=op_name, VALID=1)
            return

        # 兜底：未知表达式
        raise NotImplementedError(f"Compiler Error: 不支持的 SymPy 表达式类型 {type(expr)}: {expr}. "
                                  f"请在 tests/operators/ 中检查 DFG 定义是否超出了当前硬件能力。")
            
    def _get_op(self, arg, is_int=False):
        """安全的取值助手"""
        # 🛸 兼容性修复：支持将 SymPy Symbol 转换为字符串匹配 inputs_meta
        sym_key = str(arg) if isinstance(arg, sp.Symbol) else arg
        
        # 🛸 兼容性修复：支持 Symbol 和 str 作为输入 key
        if arg in self.inputs_meta:
            return self._get_input(arg, is_int=is_int)
        if isinstance(arg, sp.Symbol) and str(arg) in self.inputs_meta:
            return self._get_input(str(arg), is_int=is_int)
            
        if arg in self.cse_vars:
            # 读取被释放到 SRAM 的长期活状态值
            self.inl_cnt += 1
            new_id = f"{arg}_reload_{self.inl_cnt}"
            # 加入依赖锁 spill_arg 确保读取必在写入之后!
            return self.b.make_op("UR_READ", new_id, RT.UR_READ, [f"spill_{arg}"], meta_name=str(arg), role="intermediate")
            
        if arg in self.mir_env: return self.mir_env[arg]
        
        # 🛸 关键修复：防止符号类型的递归死循环
        if isinstance(arg, sp.Symbol):
            raise NameError(f"Unbound identifier: {arg}. 符号既未在输入 meta 中定义，也未在 CSE 环境中命中。")

        if isinstance(arg, (sp.Float, sp.Integer, sp.Rational)):
            return self._get_const(arg)
        
        # 递归下沉剩余的 inline 表达式 (如 a*b 之类的子图)
        self.inl_cnt += 1
        inline_sym = sp.Symbol(f"inl_{self.inl_cnt}")
        self._lower_expr(inline_sym, arg)
        return self.mir_env[inline_sym]

    def _get_const(self, val: sp.Float):
        # 👽 冗余清理：通过缓存机制确保每个唯一数值在图中仅存在一个逻辑源
        val_f = float(val)
        if val_f in self.const_pool:
            return self.const_pool[val_f]
            
        nid = f"cinl_{len(self.const_pool)}_{val_f}".replace(".", "_").replace("-", "n")
        metadata = self.inputs_meta.get("constants", {}).copy()
        
        # 🛸 关键优化：常量读取通常设置 INC=0，因为所有循环迭代共用同一个常量地址
        if hasattr(self, "loop_context"):
            metadata["inc"] = 0 
            
        # 🛸 关键修复：防止重复传递 meta_name
        m_name = metadata.pop("meta_name", "constants")
        node = self.b.make_op("UR_READ", nid, RT.UR_READ, role="constant", meta_name=m_name, **metadata)
        self.const_pool[val_f] = node
        return node
        
    def _get_input(self, sym, is_int=False):
        """为输入张量产生一个严格贴近使用时间的全新读取操作，绕开物理引脚 Liveness 覆盖"""
        self.inl_cnt += 1
        new_id = f"{sym}_read_{self.inl_cnt}"
        kwargs = self.inputs_meta[sym].copy()
        kwargs["is_int"] = is_int # 注入类型偏好
        
        # 🛰️ 关键协议：在 Tensor 模式下，输入读取自动开启地址增量
        if hasattr(self, "loop_context"):
            kwargs["inc"] = self.loop_context["inner_inc"]
            
        # 🛸 关键修复：防止重复传递 meta_name
        m_name = kwargs.pop("meta_name", str(sym))
        return self.b.make_op("UR_READ", new_id, RT.UR_READ, [], meta_name=m_name, **kwargs)
