import unittest
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT
from api.hw_spec import HardwareSpec
# ADR-017: TimingVerifier 合并入 ucode_solver.validate_physical_schedule
from synthesis.ucode_solver import UCodeSynthesizer


class TestVerifierRobustness(unittest.TestCase):
    """
    ADR-017 后验防御反证测试。所有用例都用 validate_physical_schedule
    （return False 模式而非 raise RuntimeError）。
    """

    def setUp(self):
        self.spec = HardwareSpec.load("hw_config")
        self.synth = UCodeSynthesizer(self.spec)

    # ---- 1. Latch Overwrite（原 TimingVerifier 用例 1） ----

    def test_latch_expire_trap(self):
        """[反证] 数据被冲刷后 consumer 还在读 → Latch Overwrite 必 False"""
        b = MIROpBuilder()
        op_a = b.make_op("FPADD", "a", RT.FPADD, [], "A")
        op_b = b.make_op("FPADD", "b", RT.FPADD, [op_a], "B")
        dfg = b.get_dfg()
        # FPADD lat=4，II=5；A 存活窗口 [t_a+4, t_a+8]。t_b=9 落到第二迭代 = 覆盖
        schedule = {op_a: 0, op_b: 9}
        unit_map = {op_a: "fpadd_0", op_b: "fpadd_1"}  # 不同 iq 避开 modulo
        ok = self.synth.validate_physical_schedule(dfg, schedule, unit_map, II=5)
        self.assertFalse(ok, "Latch Overwrite 场景应 return False")

    # ---- 2. Bank Modulo Collision（原 TimingVerifier 用例 2，改三段精确） ----

    def test_bank_R_R_diff_addr_collision(self):
        """[反证] 同 bank 同 modulo slot 两 R 异地址 → R-R 异地址互斥 False"""
        b = MIROpBuilder()
        r1 = b.make_op("UR_READ", "r1", RT.UR_READ, [], "R1")
        r2 = b.make_op("UR_READ", "r2", RT.UR_READ, [], "R2")
        dfg = b.get_dfg()
        for op in dfg:
            if op.trace_id == r1:
                op.metadata["manual_bank"] = 3
                op.metadata["logical_addr"] = 0
            elif op.trace_id == r2:
                op.metadata["manual_bank"] = 3
                op.metadata["logical_addr"] = 1  # 异地址
        schedule = {r1: 2, r2: 18}  # 18 % 16 == 2
        unit_map = {r1: "ur_r0", r2: "ur_r1"}
        ok = self.synth.validate_physical_schedule(dfg, schedule, unit_map, II=16)
        self.assertFalse(ok, "R-R 异地址同 bank 同 slot 应 False")

    def test_bank_R_R_same_addr_pass(self):
        """[反证 boundary] 同 bank 同 slot 两 R 同地址（广播读）→ 应 PASS（豁免）"""
        b = MIROpBuilder()
        r1 = b.make_op("UR_READ", "r1", RT.UR_READ, [], "R1")
        r2 = b.make_op("UR_READ", "r2", RT.UR_READ, [], "R2")
        dfg = b.get_dfg()
        for op in dfg:
            if op.trace_id in [r1, r2]:
                op.metadata["manual_bank"] = 3
                op.metadata["logical_addr"] = 768  # 同地址
        schedule = {r1: 2, r2: 18}
        unit_map = {r1: "ur_r0", r2: "ur_r1"}
        ok = self.synth.validate_physical_schedule(dfg, schedule, unit_map, II=16)
        self.assertTrue(ok, "R-R 同地址应 PASS（ADR-008 编译期广播读豁免）")

    def test_bank_W_R_cross_type(self):
        """[反证] 同 bank 同 slot W+R 跨类型 → 无豁免必 False（ADR-009）"""
        b = MIROpBuilder()
        w = b.make_op("UR_WRITE", "w0", RT.UR_WRITE, [], "W0")
        r = b.make_op("UR_READ", "r0", RT.UR_READ, [], "R0")
        dfg = b.get_dfg()
        for op in dfg:
            if op.trace_id == w:
                op.metadata["manual_bank"] = 2
                op.metadata["logical_addr"] = 512
            elif op.trace_id == r:
                op.metadata["manual_bank"] = 2
                op.metadata["logical_addr"] = 512  # 同地址也不豁免
        schedule = {w: 1, r: 17}  # 17 % 16 == 1
        unit_map = {w: "ur_w0", r: "ur_r0"}
        ok = self.synth.validate_physical_schedule(dfg, schedule, unit_map, II=16)
        self.assertFalse(ok, "W↔R 跨类型应 False（ADR-009 硬件无 forwarding）")

    def test_bank_W_W_collision(self):
        """[反证] 同 bank 同 slot W-W 必 False"""
        b = MIROpBuilder()
        w1 = b.make_op("UR_WRITE", "w1", RT.UR_WRITE, [], "W1")
        w2 = b.make_op("UR_WRITE", "w2", RT.UR_WRITE, [], "W2")
        dfg = b.get_dfg()
        for op in dfg:
            if op.trace_id in [w1, w2]:
                op.metadata["manual_bank"] = 4
        schedule = {w1: 3, w2: 19}  # 19 % 16 == 3
        unit_map = {w1: "ur_w0", w2: "ur_w1"}
        ok = self.synth.validate_physical_schedule(dfg, schedule, unit_map, II=16)
        self.assertFalse(ok, "W-W 同 bank 同 slot 应 False")

    # ---- 3. Resource limit / port bandwidth / IQ uniqueness ----

    def test_iq_id_double_assignment(self):
        """[反证] 同 modulo slot 两个 op 用同 iq_id → 物理 ID 冲突 False"""
        b = MIROpBuilder()
        m1 = b.make_op("FPMUL", "m1", RT.FPMUL, [], "M1")
        m2 = b.make_op("FPMUL", "m2", RT.FPMUL, [], "M2")
        dfg = b.get_dfg()
        for op in dfg:
            if op.trace_id in [m1, m2]:
                op.metadata["iq_id"] = 7  # 同物理 IQ
        schedule = {m1: 0, m2: 4}  # II=4, 同 slot 0
        unit_map = {m1: "fpmul_0", m2: "fpmul_1"}  # 不同 name 避开 modulo
        ok = self.synth.validate_physical_schedule(dfg, schedule, unit_map, II=4)
        self.assertFalse(ok, "同 iq_id 同 slot 应 False")

    def test_port_bandwidth_overflow(self):
        """[反证] 同 slot 5 个 UR_READ（max=4） → 端口带宽超限 False"""
        b = MIROpBuilder()
        ids = [b.make_op("UR_READ", f"r{i}", RT.UR_READ, [], f"R{i}") for i in range(5)]
        dfg = b.get_dfg()
        # 不同 bank 不同 iq_name，仅触发 port bandwidth
        schedule = {tid: 0 for tid in ids}  # 都在 slot 0
        unit_map = {tid: f"ur_r{i}" for i, tid in enumerate(ids)}
        for i, op in enumerate(dfg):
            op.metadata["manual_bank"] = i  # 全部不同 bank
        ok = self.synth.validate_physical_schedule(dfg, schedule, unit_map, II=4)
        self.assertFalse(ok, "5 个 UR_READ 同 slot 超 4 端口应 False")


if __name__ == "__main__":
    unittest.main()
