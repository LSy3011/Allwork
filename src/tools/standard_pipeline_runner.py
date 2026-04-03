import os
import json
import numpy as np
from pathlib import Path
from typing import List, Dict, Any

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from driver.emitter import LIRPacker
from orchestrator.colorer import BalancedBankColorer
from orchestrator.memory_allocator import MemoryAllocator

class StandardPipelineRunner:
    """
    AIACC V4.0 标准化编译器流水线 (架构对齐版 V6.0)。
    强制要求每个阶段产出明确的持久化文件以供验证。
    """
    def __init__(self, hw_config: str = "hw_config", build_root: str = "build/standard"):
        self.hw_config = hw_config
        self.build_root = Path(build_root)
        self.spec = HardwareSpec.load(self.hw_config)
        self.build_root.mkdir(parents=True, exist_ok=True)

    def run_full_pipeline(self, op_name: str, get_dfg_fn, input_data: Dict[str, Any], golden_y: np.ndarray):
        print(f"\n🚀 [PIPELINE] Starting standardized flow for: {op_name}")
        op_dir = self.build_root / op_name
        op_dir.mkdir(parents=True, exist_ok=True)

        # --- Stage A: MIR DFG (初图) ---
        dfg = get_dfg_fn()
        self._dump_dfg(dfg, op_dir / "stage_a_initial.json")
        print(f"  [A] MIR DFG initial graph generated.")

        # --- Stage B: Bank & Address Allocation (物理分配) ---
        # 🛸 算法：识别图中所有需要存储映射的节点 (Constants + IO)
        dfg = self._perform_physical_allocation(dfg, input_data, op_dir)
        self._dump_dfg(dfg, op_dir / "stage_b_mapped.json")
        print(f"  [B] Physical bank/addr allocation completed.")

        # --- Stage C: SMT Synthesis (时序合成) ---
        synthesis_res = SMTSynthesizer(self.spec).solve(dfg)
        if not synthesis_res:
            raise RuntimeError(f"❌ Stage C Failed: SMT UNSAT for {op_name}")
            
        from orchestrator.stitcher import HSTSStitcher
        stitcher = HSTSStitcher()
        stitcher.add_operator(op_name, dfg, synthesis_res)
        final_stream = stitcher.get_final_stream()
        
        # 将时序信息填回 DFG 以便 Dump
        for node in dfg:
            if node.trace_id in synthesis_res:
                node.logic_cycle = synthesis_res[node.trace_id]
        
        self._dump_dfg(dfg, op_dir / "stage_c_synthesis.json")
        print(f"  [C] SMT synthesis completed. (Max Cycle: {max(synthesis_res.values())})")

        # --- Stage D: LIR Emission (指令打包) ---
        acb_path = op_dir / "acb_bundle"
        os.makedirs(acb_path, exist_ok=True)
        
        program = self._prepare_simulation_memory(dfg, input_data)
        
        packer = LIRPacker(self.hw_config)
        packer.emit_from_stream(final_stream, str(acb_path), program=program)
        print(f"  [D] LIR binary emission completed.")

        # --- Stage E: Simulation (仿真验证) ---
        # 🛸 对齐：执行 Bit-Perfect 验证
        # 首先将黄金参考值存入 bundle 以供 physical_runner 读取
        golden_path = acb_path / "golden_data.npz"
        np.savez(golden_path, y=golden_y)
        
        success, log = self._run_simulation(acb_path)
        with open(op_dir / "sim.log", "w") as f: f.write(log)
        
        if success:
            print(f"🎉 [SUCCESS] Bit-Perfect Parity Achieved for {op_name}")
        else:
            print(f"❌ [FAIL] Simulation mismatch. Check {op_dir}/sim.log")
        
        return success

    def _perform_physical_allocation(self, dfg, input_data, op_dir):
        """
        Stage B 核心算法：对 DFG 进行内存着色与分配。
        """
        from types import SimpleNamespace
        tensors = []
        node_to_tensor = {}
        
        # 1. 识别需要 Bank 存储的逻辑实体
        for node in dfg:
            # 🛸 核心对位补丁：RTOVR 必须映射到其物理定义的 Slot IQ
            if node.op_type == "RTOVR":
                node.metadata["iq_id"] = node.metadata.get("slot_id")

            # 常量
            if node.metadata.get("is_const"):
                t = SimpleNamespace(name=node.trace_id, size=1, manual_bank=None, start_t=0, end_t=1000)
                tensors.append(t)
                node_to_tensor[node.trace_id] = t
            # 入口数据
            elif node.metadata.get("role") == "operand_a":
                t = SimpleNamespace(name=node.trace_id, size=1, manual_bank=None, start_t=0, end_t=1000)
                tensors.append(t)
                node_to_tensor[node.trace_id] = t
            # 内部持久化张量 (通过 UR_WRITE/UR_READ 关联)
            elif node.op_type == "UR_WRITE":
                t_name = f"tensor_{node.trace_id}"
                t = SimpleNamespace(name=t_name, size=1, manual_bank=None, start_t=0, end_t=1000)
                tensors.append(t)
                node_to_tensor[node.trace_id] = t
                # 寻找其关联的 UR_READ
                for consumer in dfg:
                    if node.trace_id in consumer.metadata.get("depends_on", []):
                        node_to_tensor[consumer.trace_id] = t

        # 2. 建立冲突规则 (简单版：由于是 Flat DFG，我们假设所有激活张量都可能冲突)
        conflicts = []
        for i in range(len(tensors)):
            for j in range(i+1, len(tensors)):
                conflicts.append((tensors[i].name, tensors[j].name))
        
        # 3. 运行着色算法
        coloring, _ = BalancedBankColorer.assign_banks(tensors, conflicts)
        allocator = MemoryAllocator(self.spec.params)
        for t in tensors: t.bank_id = coloring.get(t.name, 0)
        addr_map = allocator.allocate(tensors)

        # 4. 回填镜像
        for node in dfg:
            if node.trace_id in node_to_tensor:
                t = node_to_tensor[node.trace_id]
                node.metadata["bank_id"] = t.bank_id
                node.metadata["addr"] = addr_map.get(t.name, 0)
                # 重要：同步物理值以供 Stage D 使用
                if node.metadata.get("is_const"):
                    node.metadata["value"] = node.metadata.get("value", 0.0)
        
        return dfg

    def _prepare_simulation_memory(self, dfg, input_data):
        """
        准备 Stage D 所需的内存 Proxy。
        """
        # 这里构造一个符合 LIRPacker 要求的 Program 结构
        banks = {i: [[0.0]*4 for _ in range(256)] for i in range(8)}
        t_meta = []
        
        for node in dfg:
            b = node.metadata.get("bank_id")
            a = node.metadata.get("addr")
            if b is not None and a is not None:
                val = node.metadata.get("value")
                if val is None: # 可能是输入
                    role = node.metadata.get("role")
                    if role == "operand_a": val = input_data.get("x")
                
                if val is not None:
                    v4 = [float(val)] * 4 if isinstance(val, (float, int)) else list(val)
                    banks[b][a] = v4
                
                # 建立 Tensor 角色映射
                name = node.metadata.get("meta_name", node.trace_id)
                role = node.metadata.get("role", "internal") # 默认设为 internal
                t_meta.append({"name": name, "bank": b, "addr": a, "offset": a, "shape": [1, 4], "role": role})
        
        class Proxy: pass
        p = Proxy()
        p.memory_allocator = Proxy(); p.memory_allocator.banks = banks
        # 修正：保证每个 Proxy 对象具备完整元数据属性 (role, shape 等)
        p.tensors = {}
        for m in t_meta:
            name = m["name"]
            t_obj = Proxy()
            # 设置缺省值防止 AttributeError
            t_obj.role = "internal"
            t_obj.shape = [1, 4]
            for k, v in m.items(): setattr(t_obj, k, v)
            p.tensors[name] = t_obj
        
        return p

    def _run_simulation(self, acb_path):
        ws = Path.cwd()
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{ws}:{ws}/src:{ws}/sim:" + env.get("PYTHONPATH", "")
        runner = ws / "sim" / "smc_simulator" / "tools" / "physical_runner.py"
        res = subprocess.run(["python3", str(runner), str(acb_path)], env=env, capture_output=True, text=True)
        return "PASSED" in res.stdout or "Verified successfully" in res.stdout, res.stdout

    def _dump_dfg(self, dfg, path):
        data = []
        for node in dfg:
            data.append({
                "trace_id": node.trace_id,
                "op_type": node.op_type,
                "cycle": node.logic_cycle,
                "metadata": {k: str(v) for k, v in node.metadata.items()}
            })
        with open(path, "w") as f: json.dump(data, f, indent=2)

import subprocess # 补上缺失的导入
