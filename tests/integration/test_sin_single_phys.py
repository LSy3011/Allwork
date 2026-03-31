import os, sys, json, numpy as np, unittest, subprocess
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer
from orchestrator.stitcher import HSTSStitcher
from library.atomic.transcendentals.sin import get_sin_8order_dfg

class TestSinSingle(unittest.TestCase):
    def test_single_element_phys(self):
        ws = Path(__file__).parent.parent.parent
        out = ws / "build" / "sin_audit_single"
        if out.exists(): import shutil; shutil.rmtree(out)
        os.makedirs(out, exist_ok=True)

        spec = HardwareSpec.load("hw_config")
        synthesizer = SMTSynthesizer(spec)
        stitcher = HSTSStitcher()
        
        sin_ops = get_sin_8order_dfg()
        sin_res = synthesizer.solve(sin_ops, strategy="balanced")
        stitcher.add_operator("Sin", sin_ops, sin_res)
        final_stream = stitcher.get_final_stream()

        from driver.emitter import LIRPacker
        packer = LIRPacker("hw_config")
        # 🛸 关键：现在 emit_from_stream 会全面管理 out 目录
        packer.emit_from_stream(final_stream, str(out))

        env = os.environ.copy()
        env["PYTHONPATH"] = f"{ws}:{ws}/sim:" + env.get("PYTHONPATH", "")
        runner = ws / "sim" / "smc_simulator" / "tools" / "physical_runner.py"
        subprocess.run([sys.executable, str(runner), str(out)], env=env)

if __name__ == "__main__": unittest.main()
