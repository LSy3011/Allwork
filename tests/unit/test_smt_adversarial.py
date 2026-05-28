import unittest
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from ir.mir.mir_op import MIROp
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT
from api.hw_spec import HardwareSpec
# ADR-016: 反证测试迁移到 ucode_solver 主路径
from synthesis.ucode_solver import UCodeSynthesizer, SolverStatus

class TestSMTAdversarial(unittest.TestCase):
    def setUp(self):
        self.spec = HardwareSpec.load("hw_config")
        self.synthesizer = UCodeSynthesizer(self.spec)

    def test_fpmul_starvation(self):
        """[用例 3] 算力饥饿：13 个无依赖 FPMUL，硬件只有 4 个，逼出 II >= 4 的鸽巢极限"""
        b = MIROpBuilder()
        for i in range(13):
            b.make_op("FPMUL", f"m_{i}", RT.FPMUL, [], f"M{i}")
        ops = b.get_dfg()

        res = self.synthesizer.solve_with_units(ops, MII=1, MAX_II=32)
        self.assertIsNotNone(res)
        self.assertGreaterEqual(res["II"], 4)

    def test_bank_meatgrinder(self):
        """[用例 4] 内存绞肉机：9 个并发读操作死磕 Bank 2，逼出 II >= 9"""
        b = MIROpBuilder()
        for i in range(9):
            b.make_op("UR_READ", f"r_{i}", RT.UR_READ, [], f"R{i}")
        ops = b.get_dfg()
        for op in ops:
            op.metadata["manual_bank"] = 2

        res = self.synthesizer.solve_with_units(ops, MII=1, MAX_II=32)
        self.assertIsNotNone(res)
        self.assertGreaterEqual(res["II"], 9)

    def test_asymmetric_fork_join(self):
        """[用例 5] 非对称生命周期陷阱 (Zero-FIFO Latch Guardian)
        证明 SMT 的 O(E) 上界能完美拦截跨周期的 Latch 覆盖！
        """
        b = MIROpBuilder()

        # 节点 A 产生数据 (FPADD 的 lat=4)
        a = b.make_op("FPADD", "a", RT.FPADD, [], "A")

        # 深渊延迟链：C1 依赖 A，C2 依赖 C1... C4 依赖 C3
        curr = b.make_op("FPADD", "c_1", RT.FPADD, [a], "C1")
        for i in range(2, 5):
            curr = b.make_op("FPADD", f"c_{i}", RT.FPADD, [curr], f"C{i}")

        # 终点 D，既依赖 A（跨越漫长岁月），又依赖 C4
        d = b.make_op("FPADD", "d", RT.FPADD, [a, curr], "D")

        ops = b.get_dfg()

        # 【数学推导】
        # t[c_1] >= t[a] + 4
        # t[c_2] >= t[c_1] + 4 >= t[a] + 8
        # t[c_3] >= t[c_2] + 4 >= t[a] + 12
        # t[c_4] >= t[c_3] + 4 >= t[a] + 16
        # t[d] >= t[c_4] + 4 >= t[a] + 20
        #
        # 节点 D 此时才来读 A，为了保证 A 的 Latch 不被下一次循环的自己覆盖：
        # t[d] < t[a] + lat_a(4) + II
        # 20 < 4 + II  =>  II > 16  =>  II >= 17

        res = self.synthesizer.solve_with_units(ops, MII=1, MAX_II=32)
        self.assertIsNotNone(res)
        self.assertGreaterEqual(res["II"], 17)

class TestSMTRefutation(unittest.TestCase):
    """
    🔴 反证测试套件 (Refutation / UNSAT Tests)
    
    正向测试验证"SMT 解出的结果是否正确"，
    反证测试验证"当物理上绝对不可能时，SMT 是否正确拒绝而非幻觉出假解"。
    
    一个总是返回 SAT 的求解器同样能通过所有正向测试，但会在硬件上彻底崩溃。
    """
    def setUp(self):
        self.spec = HardwareSpec.load("hw_config")
        self.synthesizer = UCodeSynthesizer(self.spec)

    def _assert_unsat(self, ops, II, msg):
        status, _ = self.synthesizer._solve_for_ii_with_units(ops, II=II)
        self.assertEqual(status, SolverStatus.UNSAT, msg)

    def _assert_sat(self, ops, II, msg):
        status, _ = self.synthesizer._solve_for_ii_with_units(ops, II=II)
        self.assertEqual(status, SolverStatus.SAT, msg)

    def test_impossible_ii_bank_conflict(self):
        """
        [反证 R1] 过约束系统：9 读操作挤 bank=2，同时强制 II=8
        物理下界是 II=9，因此 II=8 在数学上绝对 UNSAT。
        """
        b = MIROpBuilder()
        for i in range(9):
            b.make_op("UR_READ", f"r_{i}", RT.UR_READ, [], f"R{i}")
        ops = b.get_dfg()
        for op in ops:
            op.metadata["manual_bank"] = 2

        self._assert_unsat(ops, II=8,
            msg="II=8 应 UNSAT（9 reads same Bank，物理下界 II=9）；若 SAT 则 Bank 冲突约束漏洞")

    def test_impossible_ii_fpmul_starvation(self):
        """[反证 R2] 13 FPMUL × 4 单元，鸽巢下界 II>=4；II=3 必 UNSAT"""
        b = MIROpBuilder()
        for i in range(13):
            b.make_op("FPMUL", f"m_{i}", RT.FPMUL, [], f"M{i}")
        ops = b.get_dfg()
        self._assert_unsat(ops, II=3, msg="II=3 应 UNSAT（鸽巢下界 II=4）")

    def test_impossible_ii_latch_lifetime(self):
        """[反证 R3] 非对称分叉图数学下界 II=17，II=16 必 UNSAT（ADR-006）"""
        b = MIROpBuilder()
        a = b.make_op("FPADD", "a", RT.FPADD, [], "A")
        curr = b.make_op("FPADD", "c_1", RT.FPADD, [a], "C1")
        for i in range(2, 5):
            curr = b.make_op("FPADD", f"c_{i}", RT.FPADD, [curr], f"C{i}")
        b.make_op("FPADD", "d", RT.FPADD, [a, curr], "D")
        ops = b.get_dfg()
        self._assert_unsat(ops, II=16,
            msg="II=16 应 UNSAT（数学下界 II=17）；若 SAT 则 latch upper bound 漏洞")

    def test_sat_just_above_impossible(self):
        """[反证 R4] 边界：同图 II=17 必 SAT、II=16 必 UNSAT"""
        b = MIROpBuilder()
        a = b.make_op("FPADD", "a", RT.FPADD, [], "A")
        curr = b.make_op("FPADD", "c_1", RT.FPADD, [a], "C1")
        for i in range(2, 5):
            curr = b.make_op("FPADD", f"c_{i}", RT.FPADD, [curr], f"C{i}")
        b.make_op("FPADD", "d", RT.FPADD, [a, curr], "D")
        ops = b.get_dfg()
        import copy
        self._assert_unsat(copy.deepcopy(ops), II=16, msg="II=16 必 UNSAT（边界下方）")
        self._assert_sat(copy.deepcopy(ops), II=17, msg="II=17 必 SAT（精确边界）")

    def test_invariant_exempt_from_latch_upper_bound(self):
        """
        [反证 R5] producer 标 stride=0 时应豁免 latch lifetime 上界（ADR-006）。
        复用 R3/R4 的图结构（数学下界 II=17），但把 a 改成 stride=0 invariant。
        豁免后 a 的 latch 上界消失；II=12 远低于无豁免下界 17，但应 SAT。
        若 UNSAT 说明 is_inv 豁免逻辑被破坏。
        """
        b = MIROpBuilder()
        a_id = b.make_op("UR_READ", "a", RT.UR_READ, [], "A")
        curr = b.make_op("FPADD", "c_1", RT.FPADD, [a_id], "C1")
        for i in range(2, 5):
            curr = b.make_op("FPADD", f"c_{i}", RT.FPADD, [curr], f"C{i}")
        b.make_op("FPADD", "d", RT.FPADD, [a_id, curr], "D")
        ops = b.get_dfg()
        # 在 DFG 上为 a 标记 stride=0（循环不变量）
        for op in ops:
            if op.trace_id == "a":
                op.metadata["stride"] = 0

        self._assert_sat(ops, II=12,
            msg="stride=0 producer 应豁免 latch 上界，II=12 应 SAT；若 UNSAT 则 is_inv 豁免失效")

    def test_ur_write_exempt_from_latch_upper_bound(self):
        """[反证 R6] UR_WRITE producer 豁免 latch lifetime 上界（ADR-006）"""
        b = MIROpBuilder()
        w_id = b.make_op("UR_WRITE", "w", RT.UR_WRITE, [], "W")
        curr = b.make_op("FPADD", "c_1", RT.FPADD, [w_id], "C1")
        for i in range(2, 5):
            curr = b.make_op("FPADD", f"c_{i}", RT.FPADD, [curr], f"C{i}")
        b.make_op("FPADD", "d", RT.FPADD, [w_id, curr], "D")
        ops = b.get_dfg()
        self._assert_sat(ops, II=12,
            msg="UR_WRITE 应豁免 latch 上界，II=12 应 SAT；若 UNSAT 则豁免失效")

    def test_impossible_ii_bank_wr_crosstype(self):
        """[反证 R7] 同 Bank W+R 跨类型冲突（ADR-009）"""
        b = MIROpBuilder()
        b.make_op("UR_WRITE", "w0", RT.UR_WRITE, [], "W0")
        b.make_op("UR_READ", "r0", RT.UR_READ, [], "R0")
        ops = b.get_dfg()
        for op in ops:
            if op.trace_id == "w0":
                op.metadata["manual_bank"] = 2
                op.metadata["logical_addr"] = 0
            elif op.trace_id == "r0":
                op.metadata["manual_bank"] = 2
                op.metadata["logical_addr"] = 1
        self._assert_unsat(ops, II=1,
            msg="II=1 W+R 同 bank 必 UNSAT（ADR-009）；若 SAT 则 W↔R 跨类型互斥未生效")

    def test_sat_wr_crosstype_boundary(self):
        """[反证 R8] II=2 时 W+R 边界 SAT（ADR-009）"""
        b = MIROpBuilder()
        b.make_op("UR_WRITE", "w0", RT.UR_WRITE, [], "W0")
        b.make_op("UR_READ", "r0", RT.UR_READ, [], "R0")
        ops = b.get_dfg()
        for op in ops:
            if op.trace_id == "w0":
                op.metadata["manual_bank"] = 2
                op.metadata["logical_addr"] = 0
            elif op.trace_id == "r0":
                op.metadata["manual_bank"] = 2
                op.metadata["logical_addr"] = 1
        self._assert_sat(ops, II=2,
            msg="II=2 W+R 应 SAT（2 个 mod slot）；若 UNSAT 则约束过严")

    def test_impossible_ii_bank_wr_same_addr(self):
        """[反证 R9] W+R 同 addr 也无豁免（ADR-009，硬件无 forwarding）"""
        b = MIROpBuilder()
        b.make_op("UR_WRITE", "w0", RT.UR_WRITE, [], "W0")
        b.make_op("UR_READ", "r0", RT.UR_READ, [], "R0")
        ops = b.get_dfg()
        for op in ops:
            if op.trace_id == "w0":
                op.metadata["manual_bank"] = 2
                op.metadata["logical_addr"] = 512
            elif op.trace_id == "r0":
                op.metadata["manual_bank"] = 2
                op.metadata["logical_addr"] = 512  # 同 addr
        self._assert_unsat(ops, II=1,
            msg="II=1 W+R 同 bank 同 addr 必 UNSAT（无 forwarding）；若 SAT 则误引同 addr 豁免")

    def test_alu_issue_exclusion_under_resource_constraint(self):
        """
        [反证 R10] 同 ALU 单元同模槽不可同时发射（ADR-013 删除 add_alu_pipeline_axiom 前的安全保障）。

        构造 2 个 FPADD op，预 patch 同一 iq_id（强制分到同一物理 ALU 实例），
        强制 II=1（唯一 mod slot 0）。`add_resource_constraint` 的 modulo 子约束
        应当判 UNSAT，因为两个 op 落到同一 (iq, slot) 不允许。

        若 SAT → 说明 `add_resource_constraint` 没覆盖 ALU 发射排他，
        删 `add_alu_pipeline_axiom` 会留下物理空洞，必须先补强后才能删。
        """
        b = MIROpBuilder()
        b.make_op("FPADD", "a0", RT.FPADD, [], "A0")
        b.make_op("FPADD", "a1", RT.FPADD, [], "A1")
        ops = b.get_dfg()
        # 强制两 op 用同一物理 FPADD 实例
        # FPADD 在 iq_registry 通常是 0/1/2/3 范围中的一个；取 spec 的第一个 valid iq
        valid_iqs = self.spec.get_iq_ids("FPADD")
        if valid_iqs:
            for op in ops:
                op.metadata["iq_id"] = valid_iqs[0]

        # II=1 → 唯一 mod slot 0 → 同 iq 同 slot → 必 UNSAT
        self._assert_unsat(ops, II=1,
            msg="II=1 同 FPADD 实例同 slot 必 UNSAT；若 SAT 则 add_resource_constraint 缺 ALU 排他")


class TestMultiSMCAxioms(unittest.TestCase):
    """
    ADR-018 v7 Stage 1 multi-SMC axiom-triggering tests.

    通过 op.metadata 注入 smc_id / broadcast / smc_id_en 字段 trigger D2/D3.d/D3.e axiom。
    依赖之前的 N=1 baseline 测试集（同 SMC 行为不变）。
    """

    def setUp(self):
        self.spec = HardwareSpec.load("hw_config")
        self.synthesizer = UCodeSynthesizer(self.spec)

    def _assert_unsat(self, ops, II, msg):
        status, _ = self.synthesizer._solve_for_ii_with_units(ops, II=II)
        self.assertEqual(status, SolverStatus.UNSAT, msg)

    def _assert_sat(self, ops, II, msg):
        status, _ = self.synthesizer._solve_for_ii_with_units(ops, II=II)
        self.assertEqual(status, SolverStatus.SAT, msg)

    def test_cross_smc_bank_non_conflict(self):
        """
        ADR-018 v7 D2: 跨 SMC 同 bank_id 不同 SMC → 不同物理 Bank → 应 SAT。
        2 个 UR_READ：(smc_id=0, bank=2) vs (smc_id=1, bank=2) at II=1 → SAT
        """
        b = MIROpBuilder()
        b.make_op("UR_READ", "r_smc0", RT.UR_READ, [], "R0", bank=2, smc_id=0)
        b.make_op("UR_READ", "r_smc1", RT.UR_READ, [], "R1", bank=2, smc_id=1)
        ops = b.get_dfg()
        for op in ops:
            op.metadata["manual_bank"] = 2

        self._assert_sat(ops, II=1,
            msg="跨 SMC 同 bank_id 不冲突（不同物理 Bank）→ II=1 应 SAT；"
                "若 UNSAT 则 D2 bank_conflict key (smc_id, bid) 维度扩展未生效")

    def test_same_smc_bank_conflict_still_unsat(self):
        """ADR-018 v7 D2: 同 SMC 同 bank 仍按 ADR-008/009 互斥 (旧行为保留)"""
        b = MIROpBuilder()
        b.make_op("UR_READ", "r_a", RT.UR_READ, [], "RA", bank=2, smc_id=0)
        b.make_op("UR_READ", "r_b", RT.UR_READ, [], "RB", bank=2, smc_id=0)
        ops = b.get_dfg()
        for op in ops:
            op.metadata["manual_bank"] = 2

        self._assert_unsat(ops, II=1,
            msg="同 SMC 同 bank 同 II=1 必 UNSAT (D2 旧行为不变)")

    def test_broadcast_unique_source_at_ii_1(self):
        """
        ADR-018 v7 D3.d: 2 个 SHIFTUP broadcast=1 同 modulo slot 0 → UNSAT (At-Most-1 per cycle)
        """
        b = MIROpBuilder()
        b.make_op("SHIFTUP", "bc_0", RT.SHIFTUP, [], "BC0",
                  broadcast=1, smc_id_en=31, smc_id=0)
        b.make_op("SHIFTUP", "bc_1", RT.SHIFTUP, [], "BC1",
                  broadcast=1, smc_id_en=31, smc_id=0)
        ops = b.get_dfg()

        self._assert_unsat(ops, II=1,
            msg="2 个 SHIFTUP broadcast=1 at II=1 同 slot → 应 UNSAT (D3.d 唯一源)")

    def test_chain_iq_propagation_at_ii_1(self):
        """
        ADR-018 v7 D3.e: 2 个 SHIFTUP smc_id_en=3 沿途 SMC_0..3 都消耗 chain IQ slot；
        at II=1 同 slot 必冲突 → UNSAT
        """
        b = MIROpBuilder()
        b.make_op("SHIFTUP", "ch_0", RT.SHIFTUP, [], "CH0",
                  broadcast=0, smc_id_en=3, smc_id=0)
        b.make_op("SHIFTUP", "ch_1", RT.SHIFTUP, [], "CH1",
                  broadcast=0, smc_id_en=3, smc_id=0)
        ops = b.get_dfg()

        self._assert_unsat(ops, II=1,
            msg="2 SHIFTUP 沿途 SMC_0..3 都占同 chain IQ slot → UNSAT (D3.e)")


if __name__ == "__main__":
    unittest.main()
