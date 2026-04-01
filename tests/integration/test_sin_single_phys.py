import os, sys, json, numpy as np, unittest, subprocess, struct
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))
sys.path.append(str(Path(__file__).parent.parent.parent / "sim"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from orchestrator.stitcher import HSTSStitcher
from library.atomic.transcendentals.sin import get_sin_8order_dfg

class TestSinSingle(unittest.TestCase):
    def test_single_element_phys(self):
        ws = Path(__file__).parent.parent.parent
        out = ws / "build" / "sin_audit_single"
        if out.exists(): import shutil; shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)

        # 1. 🛸 数据生成 (对齐 Bit-Perfect 验证)
        num_elements = 1
        x_data = np.random.uniform(-3.14, 3.14, num_elements).astype(np.float32)
        golden_y = np.sin(x_data).astype(np.float32)
        
        # 存入标准路径
        np.savez(out / "input_data.npz", x=x_data)
        np.savez(out / "golden_data.npz", y=golden_y)

        # 2. 🛸 编译器核心流程: SMT 寻优 & 物理缝合
        spec = HardwareSpec.load("hw_config")
        synthesizer = SMTSynthesizer(spec)
        stitcher = HSTSStitcher()
        
        sin_ops = get_sin_8order_dfg()
        sin_res = synthesizer.solve(sin_ops, strategy="balanced")
        stitcher.add_operator("Sin", sin_ops, sin_res)
        final_stream = stitcher.get_final_stream()

        # 3. 🛸 全算法驱动: 自动反射 DFG 的物理 Bank 分配与张量元数据
        banks = {i: [0]*4 for i in range(8)}
        tensors_meta = []
        
        for op in sin_ops:
            if "bank_id" in op.metadata:
                b = op.metadata["bank_id"]
                node = op.node_id
                if node == "x_rd" or node == "x_f_rd":
                    banks[b] = [x_data[0], 0, 0, 0]
                elif node == "c1_rd": banks[b] = [0.00019841, 0, 0, 0]
                elif node == "c2_rd": banks[b] = [0.00833333, 0, 0, 0]
                elif node == "c3_rd": banks[b] = [0.16666667, 0, 0, 0]
                elif node == "c4_rd": banks[b] = [1.0, 0, 0, 0]
                elif node == "w_y": banks[b] = [x_data[0]**2, 0, 0, 0] # 物理辅助点火
                
                # 构造元数据以对齐 Emitter(bank_id) 和 physical_runner(bank)
                role = "output" if node == "w_fin" else "input"
                name = "y" if node == "w_fin" else node.replace("_rd", "").replace("_f", "")
                tensors_meta.append({
                    "name": name, "bank": b, "bank_id": b, 
                    "addr": 0, "offset": 0, "shape": [1], 
                    "role": role, "initial_data": None
                })

        class ProgramProxy:
            def __init__(self, banks_dict, tensors):
                class Allocator: pass
                self.memory_allocator = Allocator()
                self.memory_allocator.banks = banks_dict
                self.tensors = {t["name"]: type("T", (), t) for t in tensors}
        
        prog = ProgramProxy(banks, tensors_meta)

        # 4. 🛸 发射标准 ACB 目录结构 (Emitter 会自动打包 tensors 到 sim_config)
        from driver.emitter import LIRPacker
        packer = LIRPacker("hw_config")
        print(f"\n[DEBUG] final_stream has {len(final_stream)} ops.")
        packer.emit_from_stream(final_stream, str(out), program=prog)
        
        # 修正 sim_config.json 中的 max_cycles 为 120 (对齐标准时序)
        conf_path = out / "sim_config.json"
        with open(conf_path, "r") as f: sim_conf = json.load(f)
        sim_conf["max_cycles"] = 120
        with open(conf_path, "w") as f: json.dump(sim_conf, f, indent=2)
        
        print(f"\n[DONE] Algorithm-Driven Sin Case emitted to: {out}")

        # 5. 🛸 启动物理仿真器 (它会自动基于 tensors_meta 进行验证)
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{ws}:{ws}/src:{ws}/sim:" + env.get("PYTHONPATH", "")
        runner = ws / "sim" / "smc_simulator" / "tools" / "physical_runner.py"
        res = subprocess.run([sys.executable, str(runner), str(out)], env=env, capture_output=True, text=True)
        
        print(res.stdout)
        if res.returncode != 0:
            print(res.stderr)
            self.fail("Simulation verification failed! See logs above.")
        
        print("✅ Correctness Verified via Dynamic Algorithm Metadata!")

if __name__ == "__main__": unittest.main()

if __name__ == "__main__": unittest.main()
