import os, sys, json, numpy as np, struct, unittest, subprocess
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from orchestrator.stitcher import HSTSStitcher
from orchestrator.memory_allocator import MemoryAllocator
from library.atomic.transcendentals.sin import get_sin_8order_dfg

class TestSinAudit(unittest.TestCase):
    def test_sin_only_physics(self):
        workspace_dir = Path(__file__).parent.parent.parent
        output_dir = workspace_dir / "build" / "sin_audit_clean"
        smc_output = output_dir / "smc_0"
        for d in [output_dir, smc_output, smc_output / "ur_data"]: os.makedirs(d, exist_ok=True)

        spec = HardwareSpec.load(str(workspace_dir / "hw_config"))
        synthesizer = SMTSynthesizer(spec)
        stitcher = HSTSStitcher()
        
        # 🛸 关键：只加载 Sin
        num_elements = 1
        sin_ops = get_sin_8order_dfg()
        # 强制使用 Balanced 策略以对齐理论图
        sin_res = synthesizer.solve(sin_ops, strategy="balanced")
        
        stitcher.add_operator("Sin", sin_ops, sin_res)
        final_stream = stitcher.get_final_stream()

        # 导出指令
        from driver.emitter import LIRPacker
        packer = LIRPacker(str(workspace_dir / "hw_config"))
        packer.emit_from_stream(final_stream, str(smc_output))

        # 生成 Sim Config
        iq_init = {hf.stem.upper(): f"smc_0/{hf.name}" for hf in smc_output.glob("*.hex")}
        sim_config = {"smc_id": 0, "max_cycles": 100, "iq_init": iq_init, "ur_init": {}, "tensors": []}
        with open(output_dir / "sim_config.json", "w") as f: json.dump(sim_config, f, indent=4)

        # 运行仿真
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{workspace_dir}:{workspace_dir}/sim:" + env.get("PYTHONPATH", "")
        runner_path = workspace_dir / "sim" / "smc_simulator" / "tools" / "physical_runner.py"
        subprocess.run([sys.executable, str(runner_path), str(output_dir)], env=env)

if __name__ == "__main__": unittest.main()
