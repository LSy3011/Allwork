"""ADR-032 D2 可证伪单测：FP16MUL 多微指令 schema（compute/load_s0123/load_s4567）。

验三件事（hco 建议）：
  1. HardwareSpec.load 后 FP16MUL 有 3 个 micro_groups，各 code_bits=8/7/9 + 顶层镜像 compute=8
     （反「master override 丢组」回归）。
  2. payload_factory.build 按 micro_variant 选组：compute 出 src_0_id/src_1_id，load_s4567 出 src7654_low_id。
  3. packer get_unit_bits 按 variant 取 body 宽度（41+8/41+7/41+9，防 emitter 偏移算错）。
  4. 向后兼容：单微指令单元（FPMUL）+ 无 variant → 顶层 code_fields 不变。
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))

from verification_v2.infra.pipeline_runner import PipelineRunner
from src.driver.payload_factory import UnitPayloadFactory
from src.driver.packer import BitPacker
from src.ir.mir.mir_op import MIROp


class TestFP16MULSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = PipelineRunner().spec
        cls.m = cls.spec.modules["FP16MUL"]

    def test_three_micro_groups_loaded(self):
        """反「load 丢组」：3 组各 code_bits 8/7/9，顶层镜像 compute=8。"""
        self.assertIsNotNone(self.m.micro_groups, "FP16MUL 应有 micro_groups（master override 不应丢组）")
        self.assertEqual(set(self.m.micro_groups), {"compute", "load_s0123", "load_s4567"})
        self.assertEqual(self.m.micro_groups["compute"].code_bits, 8)
        self.assertEqual(self.m.micro_groups["load_s0123"].code_bits, 7)
        self.assertEqual(self.m.micro_groups["load_s4567"].code_bits, 9)
        self.assertEqual(self.m.code_bits, 8, "顶层 code_bits 镜像 compute 组")
        for g, grp in self.m.micro_groups.items():
            self.assertEqual(sum(f.bits for f in grp.code_fields), grp.code_bits,
                             f"{g} code_bits 须 == 字段和")

    def test_payload_factory_selects_variant(self):
        """build 按 micro_variant 选组。"""
        op_c = MIROp("FP16MUL", 0, "t_c", metadata={"micro_variant": "compute", "vld": 1,
                                                     "src_0_id": 1, "src_1_id": 0})
        pl_c = UnitPayloadFactory.build(op_c, {}, {}, self.spec)
        self.assertIn("SRC_0_ID", pl_c, "compute 组应有 src_0_id 字段")
        self.assertNotIn("SRC7654_LOW_ID", pl_c, "compute 组不应有 load 字段")

        op_l = MIROp("FP16MUL", 0, "t_l", metadata={"micro_variant": "load_s4567", "vld": 1,
                                                    "src7654_low_id": 2})
        pl_l = UnitPayloadFactory.build(op_l, {}, {}, self.spec)
        self.assertIn("SRC7654_LOW_ID", pl_l, "load_s4567 组应有 src7654_low_id")
        self.assertEqual(pl_l["SRC7654_LOW_ID"], 2, "low_id=2 → 选 S6（表514 2'b10）")
        self.assertNotIn("SRC_0_ID", pl_l, "load 组不应有 compute 字段")

    def test_packer_unit_bits_per_variant(self):
        """get_unit_bits 按 variant 取 body 宽度（41 header + group body）。"""
        self.assertEqual(BitPacker.get_unit_bits(self.spec, "FP16MUL", micro_variant="compute"), 41 + 8)
        self.assertEqual(BitPacker.get_unit_bits(self.spec, "FP16MUL", micro_variant="load_s0123"), 41 + 7)
        self.assertEqual(BitPacker.get_unit_bits(self.spec, "FP16MUL", micro_variant="load_s4567"), 41 + 9)
        # 无 variant → 顶层 compute=8
        self.assertEqual(BitPacker.get_unit_bits(self.spec, "FP16MUL"), 41 + 8)

    def test_backward_compat_single_micro_unit(self):
        """单微指令单元（FPMUL）无 micro_groups，走顶层 code_fields 不变。"""
        fpmul = self.spec.modules["FPMUL"]
        self.assertIsNone(getattr(fpmul, "micro_groups", None), "FPMUL 不应有 micro_groups")
        self.assertEqual(BitPacker.get_unit_bits(self.spec, "FPMUL"), 41 + 8)

    def test_compute_valid_bit_injected(self):
        """ADR-032 S1 可证伪：FP16MUL compute payload 的 valid 位被置 1。
        回归锁：字段名若退回 `vld`（payload_factory 按 'VALID' 匹配 → 漏命中 → VALID 缺失），
        本断言失败。曾导致 FP16MUL 永不触发、D0/D1 恒 0（S1 e2e 才暴露）。"""
        op = MIROp("FP16MUL", 0, "t_valid", metadata={"micro_variant": "compute"})
        pl = UnitPayloadFactory.build(op, {}, {}, self.spec)
        self.assertEqual(pl.get("VALID"), 1, "compute 必须注入 VALID=1，否则单元永不触发")

    def test_compute_input_port_in_dvr_map(self):
        """ADR-032 S1 可证伪：FP16MUL inputs 首端口名须命中 dvr_map（RTOVR 选通槽）。
        回归锁：若退回 'S0123'（load 总线名，非 dvr_map 键）→ ucode_solver positional 查
        target_slot 落 0，rt_a 误路由。断言首端口 == 'S0' 且在 dvr_map 中。"""
        m = self.spec.modules["FP16MUL"]
        inputs = m.inputs if hasattr(m, "inputs") else m.model_dump().get("inputs", [])
        first_port = inputs[0].split("-")[-1]
        dvr = self.spec.get_dvr_map().get("FP16MUL", {})
        self.assertIn(first_port, dvr, f"首端口 {first_port} 须是 dvr_map 选通槽名（非 load 总线名）")
        self.assertEqual(dvr[first_port], 19, "FP16MUL_S0 选通槽 == 19")


if __name__ == "__main__":
    unittest.main()
