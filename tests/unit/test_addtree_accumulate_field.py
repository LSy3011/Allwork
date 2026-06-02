"""ADR-029 可证伪单测：FP32ADDTREE5TO1 微指令 accumulate 位 = 表 517 位序锁定。

直接走 spec→payload_factory→BitPacker，断言 body 位序（VALID bit0 / accumulate bit1 /
smc_id_en bit2-6）+ accumulate = cumulative。e2e 终值 parity 间接覆盖不了「位序」——
一旦有人改 code_fields 顺序，e2e 可能仍 parity 但 VCS 错位；本测直接挡住（hco 建议）。
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))

from verification_v2.infra.pipeline_runner import PipelineRunner
from verification_v2.ops.matmul_op import ManualMatmulOperator, ManualMatmulQ2Operator
from src.driver.payload_factory import UnitPayloadFactory
from src.driver.packer import BitPacker


class TestAddtree5to1AccumulateField(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = PipelineRunner().spec

    def _body_bits_of(self, op_cls):
        op = op_cls()
        at = [o for o in op.get_dfg(spec=None) if o.op_type.upper() == "FP32ADDTREE5TO1"][0]
        payload = UnitPayloadFactory.build(at, {}, {}, self.spec)
        # is_body=True、无 header → 返回值即 body（h_val=0）
        full = BitPacker.pack_capsule(self.spec, "FP32ADDTREE5TO1", payload, is_body=True)
        body_bits = self.spec.modules["FP32ADDTREE5TO1"].code_bits
        return full & ((1 << body_bits) - 1), body_bits, at.metadata.get("cumulative")

    def test_body_is_7_bits_table517(self):
        """表 517：vld+accumulate+smc_id_en = 7 bit。"""
        fields = self.spec.modules["FP32ADDTREE5TO1"].code_fields
        self.assertEqual(self.spec.modules["FP32ADDTREE5TO1"].code_bits, 7)
        names = [f.name.lower() for f in fields]
        self.assertEqual(names, ["valid", "accumulate", "smc_id_en"],
                         f"字段顺序必须 vld→accumulate→smc_id_en（表 517），实际 {names}")
        widths = [f.bits for f in fields]
        self.assertEqual(widths, [1, 1, 5], f"位宽必须 1/1/5，实际 {widths}")

    def test_cumulative_op_accumulate_bit1_set(self):
        """matmul（cumulative=True）→ body bit1(accumulate)=1，VALID bit0=1。"""
        body, bb, cum = self._body_bits_of(ManualMatmulOperator)
        self.assertTrue(cum)
        self.assertEqual(bb, 7)
        self.assertEqual(body & 1, 1, "VALID(bit0) 应=1")
        self.assertEqual((body >> 1) & 1, 1, "cumulative=True → accumulate(bit1) 应=1")

    def test_noncumulative_op_accumulate_bit1_clear(self):
        """matmul_q2（cumulative=False）→ body bit1(accumulate)=0。"""
        body, bb, cum = self._body_bits_of(ManualMatmulQ2Operator)
        self.assertFalse(cum)
        self.assertEqual((body >> 1) & 1, 0, "cumulative=False → accumulate(bit1) 应=0")

    def test_unit_total_bits_41_header_plus_7_body(self):
        """整指令 = 41 header + 7 body = 48 bit（header 在高位，body 低 7 位）。"""
        self.assertEqual(BitPacker.get_unit_bits(self.spec, "FP32ADDTREE5TO1"), 48)


if __name__ == "__main__":
    unittest.main()
