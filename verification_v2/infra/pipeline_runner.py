import os, sys, json, subprocess
import numpy as np
from typing import List, Dict, Any
from api.hw_spec import HardwareSpec
from synthesis.ucode_solver import UCodeSynthesizer
from driver.assembler import PhysicalAssembler, UnrollAdmission
from driver.emitter import LIRPacker
from collections import namedtuple

TensorObj = namedtuple("TensorObj", ["bank", "offset", "shape", "role"])

# ADR-055 M3：算子身份**规范化契约**。调用方标签可带渠道前缀（hw-parity 物理验收渠道在
# tests/integration/test_smt_hw_parity.py:67 传 f"hw_parity_{op_name}"）；剥离**已知闭集前缀**、
# 小写化、形态校验。无法规范化 → ValueError（响亮失败），绝不静默降级到 embed 主线。
_OP_LABEL_PREFIXES = ("hw_parity_",)

def normalize_top_op(raw):
    """规范化顶层算子身份：小写、剥离已知渠道前缀、形态校验。None → None。"""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    for _p in _OP_LABEL_PREFIXES:
        if s.startswith(_p):
            s = s[len(_p):]
    if not s or s[0].isdigit() or any(not (c.isalnum() or c == "_") for c in s):
        raise ValueError(f"ADR-055 M3：算子身份标签无法规范化: {raw!r}")
    return s

class PipelineRunner:
    def __init__(self, hw_config_name: str = "hw_config", show_review: bool = True, show_model: bool = False):
        self.hw_config_name = hw_config_name
        self.spec = HardwareSpec.load(hw_config_name)
        self.show_review = show_review
        self.show_model = show_model
        self.output_root = "build/verification_v2"
        os.makedirs(self.output_root, exist_ok=True)

    def run_full_stack(self, operator: Any, input_data: Dict[str, np.ndarray], op_name: str = None,
                       external_input: bool = False):
        if self.show_review:
            print(f"\n>>> Starting Pipeline Runner...")
        
        # 🛸 [V5.4 FIX] 注入硬件 Spec 和数据类型到模拟内存层
        from verification_v2.infra.mock_memory import MockProgram

        vector_width_bits = self.spec.params.PARAM_SMC_VECTOR_WIDTH
        element_bits = operator.element_bits
        vector_lanes = vector_width_bits // element_bits

        # ADR-018 v7 D5.d task #18 sub-7: parallel_dim 算子需 per-SMC programs
        # + 多 SMC 数据 reshape（broadcast 1D / partitioned 2D shape=(smc_num,n_rows)）。
        smc_num_eff = self._compute_smc_num(operator)
        is_multi_smc = smc_num_eff is not None and smc_num_eff >= 2
        if is_multi_smc:
            first_val = next(iter(input_data.values()))
            n_rows_actual = first_val.shape[-1]
            # ADR-041 followup（2026-06-30）：补全 --input-file 语义。multi-SMC 路径默认 regen 出 per-SMC
            #   partitioned input（rng.uniform）；当 external_input（--input-file 注入，已是 multi-SMC 形状：
            #   broadcast 1D + partitioned (smc_num,K)）时**跳过 regen、honor 注入值**（真权重列 tile）。
            #   默认 external_input=False → 行为完全不变（现有 multi-SMC 回归零影响）。
            if not external_input:
                input_data = operator.prepare_input(n_rows_actual, smc_num=smc_num_eff)
            programs = [MockProgram(spec=self.spec, dtype=operator.dtype)
                        for _ in range(smc_num_eff)]
            program = programs[0]  # for tensor metadata / 兼容
        else:
            program = MockProgram(spec=self.spec, dtype=operator.dtype)
            programs = None

        ops = operator.get_dfg(spec=self.spec)
        # ADR-053 D4：RtoVR 稀疏掩码确定性绕行 lowering（UR↔compute 全路径经
        # VIN0/VIN1/VOUT0 中继；非法且不可绕行的 UR↔compute 边 → 编译期硬错误）。
        # 必须在 get_dfg 之后、solve 之前：改写后的拓扑进 SMT，延迟结构性推出。
        from frontend.rtovr_bypass import apply_rtovr_sparse_bypass
        ops = apply_rtovr_sparse_bypass(ops, self.spec)
        if self.show_model:
            print("\n[DEBUG] DFG Nodes Metadata:")
            for op in ops:
                print(f"  Node: {op.trace_id:<15} | Type: {op.op_type:<10} | Meta: {op.metadata}")
                
        synth = UCodeSynthesizer(self.spec)
        result = synth.solve_with_units(ops, MII=1, MAX_II=64)
        if not result:
            raise RuntimeError("Synthesis failed.")
        
        schedule, II, unit_map = result["schedule"], result["II"], result.get("unit_assignments", {})
        for op in ops:
            op.metadata["global_t"] = schedule[op.trace_id]
            op.metadata["unit_id"] = unit_map.get(op.trace_id)

        # ADR-044（正本，user 2026-07-01「latency 固定不该标定」）：DST 广播→bank5 写的
        #   stream_lead_delay 从 SMT 已排的 shiftup（其 global_t = ds_transpose + SMI_DS_T lat
        #   = data_out 就绪拍，已含固定 gather/collect/2N-drain latency）+ 固定 SHIFTUP 广播→UR写
        #   管线常量推导，取代手设常量。换 PARAM_SMC_NUM / gather drain 时自动跟，无需 VCS 逐拍标定。
        #   （out_obj_0 自身 global_t 被 rtovr resident-window slack 高估、不能直接用；shiftup.global_t 准。）
        #   见 memory derive-timing-not-vcs-calibrate + ADR-044 DEBT-042-EMIT-SMT-LAYER。
        DST_BCAST_WRITE_LEAD = 7   # 固定：shiftup.global_t(=data_out 就绪拍)→bank5 首写的广播管线拍数
                                   #   （SHIFTUP 广播传播 + RTOVR sel→uvr_wdata + 写捕获）。VCS 锚定：lead=6
                                   #   时 bank5[0]=0/整体偏移 1 拍（末 beat 丢），+1 对齐 → =7。固定常量非 per-run 调。
        # 门控：仅 two-separated-gather（vec128，transpose 带 dst_two_gather_sep）走推导——其 data_out 因
        #   gather1 隔 2N 后移、手设 lead 无法跟。unit_ds_transpose（单-tile、已 VCS 8/8）保留其手设 lead 不动。
        _is_two_word = any(op.metadata.get("dst_two_gather_sep") for op in ops)
        _dst_shiftup = next((op for op in ops
                             if op.op_type.upper() == "SHIFTUP"
                             and op.metadata.get("dst_datapath_value")), None)
        if _is_two_word and _dst_shiftup is not None and "global_t" in _dst_shiftup.metadata:
            _sgt = int(_dst_shiftup.metadata["global_t"])
            for op in ops:
                if op.metadata.get("stream_beats") and op.metadata.get("dst_datapath_value"):
                    ot = op.op_type.upper()
                    if ot == "UR_WRITE":
                        op.metadata["stream_lead_delay"] = _sgt + DST_BCAST_WRITE_LEAD
                    elif ot == "RTOVR":
                        # RTOVR 路由早 UR 写 1 拍（sel→uvr_wdata 1 拍延迟，off-by-one 修）
                        op.metadata["stream_lead_delay"] = _sgt + DST_BCAST_WRITE_LEAD - 1

        v_level = 3 if self.show_model else (2 if self.show_review else 1)
        assembler = PhysicalAssembler(self.spec, verbosity=v_level)
        
        input_tensor_names = [n for n, c in operator.tensor_configs.items() if c.get("role") == "input"]
        if not input_tensor_names:
             input_key = next(iter(input_data))
        else:
             input_key = input_tensor_names[0]
             
        # 循环迭代数 = 每-SMC 向量长度 / lanes。多-SMC partitioned 输入是 2D (smc_num, N)，须取末维 N
        # （非 len() 的首维 smc_num）；broadcast/单-SMC 输入 1D 时 shape[-1]==len()，行为不变。
        # （修：全-partitioned 多-SMC op 如 unit_fpadd 旧版误用 smc_num 当迭代数 → 只跑 2 迭代、后段 stale。）
        _in_arr = input_data[input_key]
        _per_smc_len = _in_arr.shape[-1] if hasattr(_in_arr, "ndim") and _in_arr.ndim >= 2 else len(_in_arr)
        num_loop_iters = _per_smc_len // vector_lanes
        # ADR-036 S1：structural_only 节点（如 ds_src_read 列对齐读）只作 SMT 排程锚点，
        # 不进 emit/sim —— 真实数值由指令驱动的流式 SMI_DS_T 单元提供（ds_transpose hex →
        # _tick_smi_ds_t_trigger；ADR-033 的 apply_smi_ds_t run-前 backdoor 已删 S3-c）。
        # solve 已对全部 ops（含 structural）排程并设 global_t；此处仅从 emit/assemble 路径剔除。
        # ADR-056 §B.1/§B.4 触点 3（取代 ADR-055 M2/M3 名称白名单双向闸）：准入凭据在
        #   assemble **之前**定案。名称级白名单 A1_UNROLL_ALLOWLIST 已删——名称不再构成
        #   准入依据；请求信号 = 算子 YAML `experimental_unroll: true`（ConfigurableOperator
        #   构造期严格 bool 解析，缺省缺席=未请求）。标签规范化保留：仍作
        #   branch_report/返回值的算子身份载体与下述接线闸输入。
        norm_label = normalize_top_op(op_name)
        op_identity = getattr(operator, "name", None)
        norm_identity = (normalize_top_op(op_identity)
                         if isinstance(op_identity, str) and op_identity else None)
        if norm_label is None:
            norm_label = norm_identity or normalize_top_op(operator.__class__.__name__)
        # 接线闸（ADR-055 M3 的 ADR-056 形态）：算子本体声明了准入请求，但调用标签规范
        #   化后与本体身份不一致 → 接线错（hw-parity 渠道前缀已剥；前缀拼错/张冠李戴即
        #   此类）——响亮失败，不得静默回落 embed（该形态在 RTL 存疑、不计覆盖）。
        request_unroll = bool(getattr(operator, "experimental_unroll", False))
        if (request_unroll and norm_identity is not None
                and norm_label != norm_identity):
            raise ValueError(
                f"ADR-056 §B.1：算子本体 '{op_identity}' 声明 experimental_unroll，但调用"
                f"标签 '{op_name!r}' 规范化后为 '{norm_label}'，与本体身份不符——响亮失败"
                f"（不得静默回落 embed）")

        admission = None
        if request_unroll:
            admission = self._build_admission(operator, norm_label,
                                              num_loop_iters, _per_smc_len, vector_lanes)

        emit_ops = [op for op in ops if not op.metadata.get("structural_only")]
        physical_instrs = assembler.assemble(emit_ops, II=II, num_iterations=num_loop_iters,
                                             admission=admission)
 
        if is_multi_smc and hasattr(operator, "inject_data_per_smc"):
            operator.inject_data_per_smc(
                programs, input_data, vector_lanes=vector_lanes, spec=self.spec
            )
        else:
            operator.inject_data(program, input_data, vector_lanes=vector_lanes, spec=self.spec)
        golden_res = operator.get_golden(input_data, vector_lanes=vector_lanes)
        
        for name, config in operator.tensor_configs.items():
            raw_shape = config.get("shape", (num_loop_iters, vector_lanes))
            t_shape = tuple((vector_lanes if s == -1 else s) for s in raw_shape)
            program.tensors[name] = TensorObj(
                bank=config["bank"], 
                offset=config.get("offset", 0), 
                shape=t_shape,
                role=config["role"]
            )
 
        if op_name is None:
            op_name = operator.__class__.__name__.lower()
        output_dir = os.path.join(self.output_root, op_name)
        os.makedirs(output_dir, exist_ok=True)

        # ADR-055 M4：落地逐队列实际选中分支（branch_report），守卫⑭泛化后据此
        #   断言白名单算子全队列走 unroll——sim 同构建模下这是唯一防静默回落的证物。
        if assembler.branch_report:
            with open(os.path.join(output_dir, "branch_report.json"), "w") as f:
                json.dump({"top_op": norm_label, "branches": assembler.branch_report},
                          f, indent=2, sort_keys=True)
        
        actual_max_t = max((instr.abs_t for instr in physical_instrs), default=0)
        safe_max_cycles = actual_max_t + (II * num_loop_iters) + 50
        if self.show_review:
            print(f"[RUNNER] Dynamic Max Cycles set to: {safe_max_cycles}")

        v_level = 3 if self.show_model else (2 if self.show_review else 1)
        packer = LIRPacker(self.hw_config_name, verbosity=v_level)
        if self.show_review and is_multi_smc:
            print(f"[RUNNER] parallel_dim={getattr(operator, 'parallel_dim', None)} "
                  f"→ multi-SMC emit smc_num={smc_num_eff}")
        # 多 SMC 模式：传 programs list；emitter._emit_multi_smc 据此 per-SMC inject_data
        emit_program = programs if is_multi_smc else program
        # ADR-020 v6: 多 SMC 路径必传 lir_stream (DFG 拓扑) 用于 per-SMC stagger
        packer.emit_from_instructions(
            physical_instrs, output_dir, program=emit_program, smc_num=smc_num_eff,
            lir_stream=emit_ops if is_multi_smc else None,
        )
        self._patch_max_cycles(output_dir, safe_max_cycles)

        # ADR-033 Stage-A：算子提供 SMI_DS_T 跨-SMC 转置 config → patch 进 sim_config.json
        # （照搬 _patch_max_cycles 的 post-emit patch 模式，零 emitter 签名改动）。
        if is_multi_smc and hasattr(operator, "smi_ds_t_config"):
            cfg = operator.smi_ds_t_config(spec=self.spec)
            if cfg:
                # ADR-036 S3-a：real-gather 时 ds_src_read 进 emit、solver 分配端口。post-solve 回读其
                # 实际落的端口 → cfg["src_read_port"]（cluster 据此走读端口 gather 而非 storage backdoor）
                # + dst_src_capture（独立捕获寄存器，避同端口异 bank 读 clobber，mirror broadcast_capture）。
                dst_cap = self._dst_src_capture_config(ops)
                # ADR-036 S3-a（hco a1d94523 note）：real-gather 时 ds_src_read 已 emit 真 bank 读指令，
                # 若端口回读失败（dst_cap None，仅当 solver 漏赋 iq_id）则 cluster 会静默回 storage
                # backdoor → 固件走真读 / sim 走 backdoor 的半态。loud-fail 钉死此不一致（现 emit 路径
                # 每个非 structural op 必赋 iq_id、触发不到；前移守 S3-b 固件完整性门）。
                if any(o.metadata.get("ds_src_read") for o in ops) and dst_cap is None:
                    raise RuntimeError(
                        "ADR-036 S3-a: ds_src_read 标记 real-gather 但端口回读失败（iq_id 未分配）——"
                        "拒绝静默回 storage backdoor（固件真读/sim backdoor 半态）")
                if dst_cap:
                    cfg["src_read_port"] = dst_cap["port"]
                    self._patch_sim_config(output_dir, {"dst_src_capture": dst_cap})
                    # ADR-036 S3-b（full fan-out）：real-gather 同时去 scatter backdoor——转置行经 SHIFTUP
                    # 广播输入缓冲直喂 compute（删 bank0 staging）。scatter_to_feed 驱动 cluster 投缓冲；
                    # dst_bcast_feed 配 head(SMC_0) 启用、bank = a_read 触发读的 dst_bank。
                    cfg["scatter_to_feed"] = True
                    self._patch_sim_config(output_dir, {
                        "dst_bcast_feed": {"bank": int(cfg["dst_bank"]), "smc": 0}})
                self._patch_sim_config(output_dir, {"smi_ds_t": cfg})

        # DEBT-032 H2：SHIFTUP 广播源独立捕获寄存器 config（消 flaky，hco H2 判定）。
        # 从 DFG 找 broadcast_source 标记的 UR_READ，解析其读端口(iq_id-base)与 bank(layout)，
        # 令 sim 在该端口读该 bank 时快照广播激活、不被同端口异 bank（权重）读 clobber。
        bcast_cfg = self._broadcast_capture_config(ops)
        if bcast_cfg:
            self._patch_sim_config(output_dir, {"broadcast_capture": bcast_cfg})

        # 🛸 [V5.4 FIX] 保存物理指令供审计员使用
        with open(os.path.join(output_dir, "physical_instructions.json"), "w") as f:
            json.dump([instr.to_dict() for instr in physical_instrs], f, indent=2)
        
        np.savez(os.path.join(output_dir, "golden_data.npz"), y=golden_res["y"])
        with open(os.path.join(output_dir, "expected_values.json"), "w") as f:
            json.dump(golden_res["intermediates"], f, indent=2)
        
        # ADR-055 D4(b-1)：独立事件检查器（tools/emit_expected_fire_sequence.py）须从 **solver 侧**
        #   独立重建展开行的 (BANK, ADDR)——地址契约 `orig_offset_k + stride_k·(j − r_k)` 的两个输入
        #   （stride / logical_addr）必须与 t 一同落盘，否则检查器只能回读产物 payload（同源、零信号）。
        #   纯附加字段：既有读者（auditor / g14b / timing_diff）只取 `t` / `iq`。
        schedule_dump = {
            "II": II,
            "ops": {op.trace_id: {
                "t": int(op.metadata["global_t"]),
                "iq": op.metadata["unit_id"],
                "stride": int(op.metadata.get("stride", 0) or 0),
                "logical_addr": int(op.metadata.get("logical_addr", 0) or 0),
                "dly": int(op.metadata.get("dly", 0) or 0),
                "op_type": str(op.op_type),
            } for op in ops}
        }
        with open(os.path.join(output_dir, "compiler_schedule.json"), "w") as f:
            json.dump(schedule_dump, f, indent=2)

        if self.show_review:
            print(f"[STEP] Running Physical Simulation...")
        
        log_path = os.path.join(output_dir, f"{op_name}_sim.log")
        with open(log_path, "w") as log_f:
            sim_cmd = [sys.executable, "sim/smc_simulator/tools/physical_runner.py", output_dir]
            res = subprocess.run(sim_cmd, stdout=log_f, stderr=log_f)
            if res.returncode != 0:
                print(f"[ERROR] Physical simulation failed. Check {log_path}")

        # 🛸 [V6.4] 解析并控制台实时回显 SMC 仿真比对结果
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                lines = f.readlines()
            start_idx = -1
            for idx, line in enumerate(lines):
                if "Simulation Finished" in line and "Verifying Data..." in line:
                    start_idx = idx
                    break
            if start_idx != -1:
                print("\n>>> SMC Physical Simulation Numerical Verification:")
                for line in lines[start_idx:]:
                    print("  " + line.strip("\n"))
            else:
                # Fallback: Just print last 10 lines if "Simulation Finished" is not matched
                print("\n>>> SMC Physical Simulation Numerical Verification:")
                for line in lines[-10:]:
                    print("  " + line.strip("\n"))

        return {
            "output_dir": output_dir,
            "log_path": log_path,
            "schedule": schedule,
            "II": II,
            "physical_instrs": physical_instrs,
            "num_iterations": num_loop_iters,
            "sim_success": (res.returncode == 0),
            "ops": ops,
            # ADR-055：规范化后的顶层算子身份 + 逐队列分支分类（⑭-on-add 用）
            "top_op": norm_label,
            "branch_report": assembler.branch_report,
            # ADR-020 v3：multi-SMC 路径在本函数内 line 39 重生成 input_data（_prepare_input_parallel_n,
            # rng.uniform(1,5) + default_rng(0)），与 main_verify.run_test 入口前 prepare_input(n_rows)
            # 的 randn 输出不一致。Auditor 须用 sim 实际吃到的这份算 expected，否则系统性 mismatch。
            "input_data": input_data,
        }

    def _build_admission(self, operator: Any, norm_label: str,
                         num_loop_iters: int, per_smc_len: int,
                         vector_lanes: int) -> UnrollAdmission:
        """ADR-056 §B.4 触点 3：由算子张量声明物化 UnrollAdmission（L1 请求 → L2 凭据）。

        - bank 须为标量 int（list 形态属多 bank 布局，越出 V1 包络 → TYPE_LAYOUT_UNSUPPORTED）。
        - 规模约束：per_smc_len 须整除 vector_lanes（尾批不满一拍向量的布局 V1 不支持）。
        - §F.5 静态容量：stride=1 张量（input/output）N_eff 迭代消费
          stride·(N_eff−1)+1 words；stride=0 驻留参数/常量每标量 1 word（不得按 n_rows/4）。
          消耗 > length（声明的 bank 内 UR word 静态分配上界）→ TYPE_LAYOUT_UNSUPPORTED。
        字段形态校验（int/bool 拒/正值/同 bank 两两不交）由 UnrollAdmission.__post_init__ 兜底。
        """
        if per_smc_len % vector_lanes != 0:
            raise ValueError(
                f"[TYPE_LAYOUT_UNSUPPORTED] 准入算子 '{norm_label}' 输入长度 "
                f"{per_smc_len} 不整除 vector_lanes={vector_lanes}——尾批布局越出 V1 "
                f"规模约束（ADR-056 §A.2-5），显式拒绝")
        regions: Dict[str, tuple] = {}
        _DEFAULT_STRIDE = {"input": 1, "output": 1, "param": 0, "constant": 0}
        for tname, tcfg in operator.tensor_configs.items():
            bank = tcfg.get("bank")
            if isinstance(bank, list) or not isinstance(bank, int) or isinstance(bank, bool):
                raise ValueError(
                    f"[TYPE_LAYOUT_UNSUPPORTED] 准入算子 '{norm_label}' 张量 '{tname}' "
                    f"bank={bank!r}——V1 要求标量 int bank（多 bank 布局属 A2a 非目标，"
                    f"ADR-056 §A.3），显式拒绝")
            offset = tcfg.get("offset", 0)
            length = tcfg.get("length")
            stride = tcfg.get("stride", _DEFAULT_STRIDE.get(tcfg.get("role"), 0))
            consumed = 1 if stride == 0 else stride * (num_loop_iters - 1) + 1
            if length is not None and consumed > length:
                raise ValueError(
                    f"[TYPE_LAYOUT_UNSUPPORTED] 准入算子 '{norm_label}' 张量 '{tname}' "
                    f"声明 length={length} 不足以承载 N_eff={num_loop_iters} 迭代的静态消耗 "
                    f"{consumed} words（stride={stride}；stride=0 驻留=每标量 1 word）"
                    f"——ADR-056 §F.5，显式拒绝")
            regions[tname] = (bank, offset, length)
        return UnrollAdmission(op_name=norm_label, tensor_regions=regions)

    def _compute_smc_num(self, operator: Any):
        """
        ADR-018 v7 D5.d (task #18 sub-5): parallel_dim 算子触发多 SMC emit 路径。

        - parallel_dim ∈ {"N", "K"} → smc_num = PARAM_SMC_NUM (=32)
        - parallel_dim = None / 算子无此属性 → smc_num = None (单 SMC 向后兼容)

        emitter.emit_from_instructions 看到 smc_num=None 走 _emit_single_smc
        (旧 smc_0/ layout)；smc_num>=2 走 _emit_multi_smc (smc_{k}/ layout)。
        """
        parallel_dim = getattr(operator, "parallel_dim", None)
        if parallel_dim is None:
            return None
        return self.spec.params.PARAM_SMC_NUM

    def _patch_max_cycles(self, output_dir: str, max_cycles: int):
        conf_path = os.path.join(output_dir, "..", "sim_config.json")
        if not os.path.exists(conf_path):
            conf_path = os.path.join(output_dir, "sim_config.json")
            if not os.path.exists(conf_path): return
        
        with open(conf_path, "r") as f:
            data = json.load(f)
        data["max_cycles"] = max_cycles
        with open(conf_path, "w") as f:
            json.dump(data, f, indent=2)

    def _patch_sim_config(self, output_dir: str, extra: dict):
        """把额外键值 patch 进 sim_config.json（同 _patch_max_cycles 的 post-emit 模式）。"""
        conf_path = os.path.join(output_dir, "..", "sim_config.json")
        if not os.path.exists(conf_path):
            conf_path = os.path.join(output_dir, "sim_config.json")
            if not os.path.exists(conf_path):
                return
        with open(conf_path, "r") as f:
            data = json.load(f)
        data.update(extra)
        with open(conf_path, "w") as f:
            json.dump(data, f, indent=2)

    def _broadcast_capture_config(self, ops) -> dict:
        """DEBT-032 H2：从 DFG 派生 SHIFTUP 广播源捕获 config（port + bank）。
        广播源 = `_inject_shiftup_broadcast` 标记 broadcast_source 的 UR_READ（pin iq_id=ur-r0）。
        port = iq_id - UR_READ 基址；bank = logical_addr 经 PhysicalMemoryLayout 解析。
        无广播源（非 broadcast 算子）→ 返回 None，sim 保持旧 live-read 行为。"""
        src = next((o for o in ops if o.metadata.get("broadcast_source")
                    and o.metadata.get("iq_id") is not None), None)
        if src is None:
            return None
        base = self.spec.config.get("iq_registry", {}).get("UR_READ", 30)
        port = int(src.metadata["iq_id"]) - int(base)
        from src.core.memory_layout import PhysicalMemoryLayout
        layout = PhysicalMemoryLayout.from_spec(self.spec)
        bank, _off = layout.resolve(int(src.metadata.get("logical_addr", 0)))
        return {"port": port, "bank": int(bank)}

    def _dst_src_capture_config(self, ops) -> dict:
        """ADR-036 S3-a：从 DFG 派生 SMI_DS_T 列对齐源读（ds_src_read）的端口捕获 config。
        real-gather 时 ds_src_read 标 `ds_src_read=True` 且不硬 pin iq_id → solver 分配空闲读端口
        （4 端口已被 compute 占满，但 ds_src 是单次 prologue，与稳态读时间错开）。
        port = 实际分配 iq_id - UR_READ 基址；bank = logical_addr 经 layout 解析（=SRC_BANK）。
        非 real-gather（structural_only，无此标记）→ 返回 None，sim 维持 storage backdoor。
        Q1 待 RTL：DST 列对齐源读是否实占 compute 的 UR_READ 端口（DEBT-036-DST-SRC-PORT，hco）。"""
        src = next((o for o in ops if o.metadata.get("ds_src_read")
                    and o.metadata.get("iq_id") is not None), None)
        if src is None:
            return None
        base = self.spec.config.get("iq_registry", {}).get("UR_READ", 30)
        port = int(src.metadata["iq_id"]) - int(base)
        from src.core.memory_layout import PhysicalMemoryLayout
        layout = PhysicalMemoryLayout.from_spec(self.spec)
        bank, _off = layout.resolve(int(src.metadata.get("logical_addr", 0)))
        return {"port": port, "bank": int(bank)}

    def parse_simulation_timeline(self, output_dir: str) -> Dict[str, List[str]]:
        timeline_path = os.path.join(output_dir, "smc_0", "analysis", "timeline_ascii.txt")
        actual_activity = {}
        if os.path.exists(timeline_path):
            with open(timeline_path, "r") as f:
                for line in f:
                    if "|" in line and "iq_name" not in line and "occupancy" not in line:
                        parts = line.split("|")
                        if len(parts) >= 2:
                            name = parts[0].strip()
                            trace = parts[1].strip().split()
                            actual_activity[name] = trace
        return actual_activity
