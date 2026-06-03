"""ADR-031 回归守门：每个 module 的 code_bits 必须 == 其 code_fields 位宽和。

根因（ADR-029 假 JUMP / ADR-031 SHIFTDN/INTTOFP 错位的共同病灶）：
`HardwareSpec.load`（hw_spec.py:153-177）用 master functional_units 覆盖 code_fields，
但 **code_bits 留 modules.yaml、从不重算**；packer（packer.py:51）用 code_bits 做 header
左移量。若二者不一致 → body 实际占位与 header 左移量错位 → VCS 解码整条指令错位（假 JUMP）。

本测把「已知 latent 不一致」变 fail-fast：任何人改 code_fields 忘了同步 code_bits 即红。
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))

from verification_v2.infra.pipeline_runner import PipelineRunner


class TestCodeBitsConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = PipelineRunner().spec

    def test_all_modules_codebits_equals_field_sum(self):
        bad = []
        for name, mod in self.spec.modules.items():
            fields = getattr(mod, "code_fields", None) or []
            code_bits = getattr(mod, "code_bits", None)
            if code_bits is None:
                continue
            try:
                fsum = sum(int(f.bits) for f in fields)
            except (TypeError, ValueError):
                continue  # 符号位宽（PARAM_*）跳过
            if fsum != code_bits:
                bad.append(f"{name}: code_bits={code_bits} ≠ 字段和={fsum} "
                           f"({[(f.name, f.bits) for f in fields]})")
        self.assertEqual(bad, [],
                         "以下 module 的 code_bits 与字段和不符（packer header 会错位，VCS 假 JUMP）：\n  "
                         + "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main()
