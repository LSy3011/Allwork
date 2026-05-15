import unittest
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from ir.mir.mir_op import MIROp
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT
from api.hw_spec import HardwareSpec
from synthesis.backend import SMTSynthesizer

class TestSMTAdversarial(unittest.TestCase):
    def setUp(self):
        self.spec = HardwareSpec.load("hw_config")
        self.synthesizer = SMTSynthesizer(self.spec)

    def test_fpmul_starvation(self):
        """[用例 3] 算力饥饿：13 个无依赖 FPMUL，硬件只有 4 个，逼出 II >= 4 的鸽巢极限"""
        b = MIROpBuilder()
        for i in range(13):
            b.make_op("FPMUL", f"m_{i}", RT.FPMUL, [], f"M{i}")
        ops = b.get_dfg()
        
        res = self.synthesizer.solve(ops, strategy="extreme")
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
        
        res = self.synthesizer.solve(ops, strategy="extreme")
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
        
        res = self.synthesizer.solve(ops, strategy="extreme")
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
        self.synthesizer = SMTSynthesizer(self.spec)

    def test_impossible_ii_bank_conflict(self):
        """
        [反证 R1] 过约束系统：9 读操作挤 bank=2，同时强制 II=8
        物理下界是 II=9，因此 II=8 在数学上绝对 UNSAT。
        断言：SMT 必须返回 None（表示无解），而不是幻觉出一个假 SAT。
        """
        b = MIROpBuilder()
        for i in range(9):
            b.make_op("UR_READ", f"r_{i}", RT.UR_READ, [], f"R{i}")
        ops = b.get_dfg()
        for op in ops:
            op.metadata["manual_bank"] = 2

        # 直接调用内部的 _solve_for_ii，强制指定一个不可能的 II
        result = self.synthesizer._solve_for_ii(ops, II=8)
        self.assertIsNone(result,
            "SMT 对 II=8 不应产生解（9 个操作挤在同一 Bank，物理下界 II=9）。"
            "如果返回非 None，说明 Bank 冲突约束存在致命漏洞！")

    def test_impossible_ii_fpmul_starvation(self):
        """
        [反证 R2] 算力过约束：13 个 FPMUL，4 个硬件单元，强制 II=3
        鸽巢原理下界 II=ceil(13/4)=4，因此 II=3 绝对 UNSAT。
        """
        b = MIROpBuilder()
        for i in range(13):
            b.make_op("FPMUL", f"m_{i}", RT.FPMUL, [], f"M{i}")
        ops = b.get_dfg()

        result = self.synthesizer._solve_for_ii(ops, II=3)
        self.assertIsNone(result,
            "SMT 对 II=3 不应产生解（13 个 FPMUL，4 个单元，鸽巢下界 II=4）。"
            "如果返回非 None，说明资源互斥约束存在致命漏洞！")

    def test_impossible_ii_latch_lifetime(self):
        """
        [反证 R3] 生命周期过约束：非对称分叉图（数学下界 II=17），强制 II=16
        Zero-FIFO 生命周期上界要求 II >= 17，因此 II=16 绝对 UNSAT。
        """
        b = MIROpBuilder()
        a = b.make_op("FPADD", "a", RT.FPADD, [], "A")
        curr = b.make_op("FPADD", "c_1", RT.FPADD, [a], "C1")
        for i in range(2, 5):
            curr = b.make_op("FPADD", f"c_{i}", RT.FPADD, [curr], f"C{i}")
        b.make_op("FPADD", "d", RT.FPADD, [a, curr], "D")
        ops = b.get_dfg()

        result = self.synthesizer._solve_for_ii(ops, II=16)
        self.assertIsNone(result,
            "SMT 对 II=16 不应产生解（数学推导下界 II=17）。"
            "如果返回非 None，说明 O(E) Latch 生命周期上界约束存在致命漏洞！")

    def test_sat_just_above_impossible(self):
        """
        [反证 R4] 边界吻合验证：同一个图，II=17 必须是 SAT，II=16 必须是 UNSAT。
        验证求解器的 SAT/UNSAT 边界没有偏移（不能有 off-by-one 错误）。
        """
        b = MIROpBuilder()
        a = b.make_op("FPADD", "a", RT.FPADD, [], "A")
        curr = b.make_op("FPADD", "c_1", RT.FPADD, [a], "C1")
        for i in range(2, 5):
            curr = b.make_op("FPADD", f"c_{i}", RT.FPADD, [curr], f"C{i}")
        b.make_op("FPADD", "d", RT.FPADD, [a, curr], "D")
        ops = b.get_dfg()

        import copy
        result_16 = self.synthesizer._solve_for_ii(copy.deepcopy(ops), II=16)
        result_17 = self.synthesizer._solve_for_ii(copy.deepcopy(ops), II=17)

        self.assertIsNone(result_16, "II=16 必须 UNSAT（边界下方）")
        self.assertIsNotNone(result_17, "II=17 必须 SAT（精确边界）。若 UNSAT 则约束过严！")

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

        result = self.synthesizer._solve_for_ii(ops, II=12)
        self.assertIsNotNone(result,
            "stride=0 producer 应豁免 latch 上界，II=12 应 SAT（无豁免数学下界 17）。"
            "若 UNSAT 说明 is_inv 豁免逻辑失效。")

    def test_ur_write_exempt_from_latch_upper_bound(self):
        """
        [反证 R6] producer 是 UR_WRITE 时应豁免 latch lifetime 上界（ADR-006）。
        UR_WRITE 写回 UR Bank 是持久化存储，不存在 latch 被下一迭代覆盖的问题。
        同样的深链结构，II=12 应当 SAT（无豁免数学下界 ≥17）。
        """
        b = MIROpBuilder()
        w_id = b.make_op("UR_WRITE", "w", RT.UR_WRITE, [], "W")
        curr = b.make_op("FPADD", "c_1", RT.FPADD, [w_id], "C1")
        for i in range(2, 5):
            curr = b.make_op("FPADD", f"c_{i}", RT.FPADD, [curr], f"C{i}")
        b.make_op("FPADD", "d", RT.FPADD, [w_id, curr], "D")
        ops = b.get_dfg()

        result = self.synthesizer._solve_for_ii(ops, II=12)
        self.assertIsNotNone(result,
            "UR_WRITE producer 应豁免 latch 上界，II=12 应 SAT。"
            "若 UNSAT 说明 is_inv 豁免逻辑失效。")

    def test_impossible_ii_bank_wr_crosstype(self):
        """
        [反证 R7] 同 Bank W+R 跨类型冲突（ADR-009）。
        单 W 单 R 同 bank=2 不同 addr，强制 II=1 → 必 UNSAT。
        若 SAT 说明 add_bank_conflict_axiom 的 W↔R 段未生效。
        """
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

        result = self.synthesizer._solve_for_ii(ops, II=1)
        self.assertIsNone(result,
            "II=1 W+R 同 bank 必须 UNSAT。"
            "若 SAT 说明 W↔R 跨类型互斥未生效（ADR-009）。")

    def test_sat_wr_crosstype_boundary(self):
        """
        [反证 R8] W+R 跨类型边界吻合：II=2 时应 SAT（ADR-009 边界）。
        与 R7 同图，II=2 提供两个 mod slot，W 和 R 分别占一个 → SAT。
        """
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

        result = self.synthesizer._solve_for_ii(ops, II=2)
        self.assertIsNotNone(result,
            "II=2 W+R 同 bank 应 SAT（2 个 mod slot 充裕）。"
            "若 UNSAT 说明约束过严。")

    def test_impossible_ii_bank_wr_same_addr(self):
        """
        [反证 R9] W+R 跨类型同地址也无豁免（ADR-009 + user 2026-05-15 确认硬件无 forwarding）。
        与 R7 同图但 W 和 R 同 logical_addr，II=1 → 仍必 UNSAT。
        若 SAT 说明 W↔R 段错误地引入了 R-R 同地址豁免逻辑。
        """
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

        result = self.synthesizer._solve_for_ii(ops, II=1)
        self.assertIsNone(result,
            "II=1 W+R 同 bank 同 addr 也应 UNSAT（硬件无 forwarding，ADR-009）。"
            "若 SAT 说明 W↔R 段错误引入了同地址豁免。")

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
        result = self.synthesizer._solve_for_ii(ops, II=1)
        self.assertIsNone(result,
            "II=1 同 FPADD 实例同模槽应 UNSAT（add_resource_constraint modulo 子约束保护）。"
            "若 SAT 说明删除 add_alu_pipeline_axiom 会留下 ALU 发射排他物理空洞。")


if __name__ == "__main__":
    unittest.main()
