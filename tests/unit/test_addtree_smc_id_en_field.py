"""ADR-030 / DEBT-029-SMCEN 可证伪单测：addtree smc_id_en 缺省 emit = PARAM_SMC_NUM-1。

user 2026-06-03 RTL 裁定：smc_id_en 是阈值门控（smc_id_en >= SMC_ID 才使能），
所有硬件单元含义一致。多-SMC 默认全员参与本地计算 → addtree 缺省必须 emit N-1，
否则 emit=0 仅 SMC_0 跑 addtree（VCS 上多-SMC bug；sim 全盲不暴露，8/8 假绿）。

本测直接走 spec→payload_factory，断言三件事（hco 建议的回归保险）：
  a. addtree（无 smc_id_en metadata）→ payload SMC_ID_EN == PARAM_SMC_NUM-1。
  b. 防误伤反证：FPMUL（字段名 SMC_ID ≠ smc_id_en）→ payload SMC_ID 维持 default 0，
     证明特例精确匹配 SMC_ID_EN、没把 compute 的 SMC_ID 也改成 N-1
     （hco REJECT 扩 scope，DEBT-023-COMPUTE-ADDR）。
  c. metadata 优先级：显式设 smc_id_en=3 的 op → payload 取 3 而非 N-1。
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))

from verification_v2.infra.pipeline_runner import PipelineRunner
from verification_v2.ops.matmul_op import ManualMatmulOperator
from src.driver.payload_factory import UnitPayloadFactory
from src.ir.mir.mir_op import MIROp


class TestAddtreeSmcIdEnField(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = PipelineRunner().spec
        cls.N = cls.spec.params.PARAM_SMC_NUM

    def test_addtree_smc_id_en_defaults_to_n_minus_1(self):
        """addtree（无 smc_id_en metadata）→ SMC_ID_EN == PARAM_SMC_NUM-1（使能全 SMC）。"""
        op = ManualMatmulOperator()
        at = [o for o in op.get_dfg(spec=None) if o.op_type.upper() == "FP32ADDTREE5TO1"][0]
        self.assertNotIn("smc_id_en", at.metadata,
                         "前提：addtree DFG 未显式设 smc_id_en（缺省由 payload_factory 补 N-1）")
        payload = UnitPayloadFactory.build(at, {}, {}, self.spec)
        self.assertEqual(payload["SMC_ID_EN"], self.N - 1,
                         f"addtree smc_id_en 缺省应 emit N-1={self.N - 1}（使能 SMC_0..N-1），"
                         f"实际 {payload.get('SMC_ID_EN')}（=0 则仅 SMC_0 跑 → 多-SMC bug）")

    def test_compute_smc_id_en_emits_n_minus_1(self):
        """ADR-031（推翻 ADR-030 Q2）：compute 的 SMC_ID 是 smc_id_en 异名，统一后 FPMUL
        缺省也 emit N-1（多-SMC 全员使能）。原 ADR-030 测「SMC_ID 维持 0」已作废——
        user RTL 定论「除 RTOVR 外所有单元 smc_id_en，SMC_ID 即其异名」。"""
        op = ManualMatmulOperator()
        mul = [o for o in op.get_dfg(spec=None) if o.op_type.upper() == "FPMUL"][0]
        self.assertNotIn("smc_id_en", mul.metadata)
        payload = UnitPayloadFactory.build(mul, {}, {}, self.spec)
        self.assertIn("SMC_ID_EN", payload, "FPMUL 现用 smc_id_en 字段（master 已统一改名）")
        self.assertNotIn("SMC_ID", payload, "SMC_ID 已统一改名 smc_id_en，不应再有 SMC_ID 字段")
        self.assertEqual(payload["SMC_ID_EN"], self.N - 1,
                         f"compute smc_id_en 缺省应 emit N-1={self.N - 1}（多-SMC 全 SMC 跑 compute）；"
                         "=0 则真硬件仅 SMC_0 跑 → 多-SMC bug（ADR-031 修）")

    def test_explicit_metadata_overrides_default(self):
        """metadata 显式 smc_id_en=3 → payload 取 3 而非 N-1（metadata 优先级）。"""
        sup = MIROp(op_type="SHIFTUP", logic_cycle=0, trace_id="sup_test",
                    metadata={"smc_id_en": 3, "broadcast": 1, "smc_id": 0, "data": 0})
        payload = UnitPayloadFactory.build(sup, {}, {}, self.spec)
        self.assertEqual(payload["SMC_ID_EN"], 3,
                         "显式 metadata smc_id_en=3 必须优先于 N-1 缺省")


if __name__ == "__main__":
    unittest.main()
