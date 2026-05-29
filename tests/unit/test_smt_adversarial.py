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


class TestEmitterPerSMCStagger(unittest.TestCase):
    """
    ADR-020 v6 per-SMC stagger 算法测试骨架（TDD red 阶段）.

    本套件锁住 v6-4 `_compute_per_smc_stagger` + v6-5 `_verify_post_stagger_resources`
    的 API contract。v6-4/5 实现未落锤前全部 skip（不破 pytest baseline）;
    落锤后 `@unittest.skipUnless` 自动启用。

    算法契约（ADR-020 v6 §Decision + v5.1 §实施算法 L760-772）:
      Pass 1 forward topo:
        delta(consumer, k) = max(delta(prod, k) for prod in deps(consumer))
        if consumer 跨 SHIFTUP chain:
          delta(consumer, k) = max(delta(consumer, k), (smc_id_en - k) * chain_stage_latency)
      Pass 2 backward BFS:
        partitioned UR_READ (无 producer) 若是 staggered consumer 依赖，反传 stagger

    P2 verifier (v6-5) 契约: post-stagger 后复跑 ResourceAxioms 做 per-SMC sanity check;
    hex 层资源冲突应 raise / return False (HCO 风险 #2 反例).
    """

    SKIP_V6_4 = "v6-4 _compute_per_smc_stagger 未落锤; 实现后自动启用"
    SKIP_V6_5 = "v6-5 _verify_post_stagger_resources 未落锤; 实现后自动启用"

    @staticmethod
    def _has_stagger():
        from driver.emitter import LIRPacker
        return hasattr(LIRPacker, '_compute_per_smc_stagger')

    @staticmethod
    def _has_verifier():
        from driver.emitter import LIRPacker
        return hasattr(LIRPacker, '_verify_post_stagger_resources')

    @unittest.skipUnless(_has_stagger.__func__(), SKIP_V6_4)
    def test_stagger_zero_for_non_shiftup_ops(self):
        """
        非 SHIFTUP/SHIFTDN 链算子: delta_k = 0 for all (trace_id, SMC_k).
        v6 P1 invariant: 非 chain 算子 stagger 算法应输出与 v5.1 baseline 字节级一致.
        horner / activation / matmul 等回归 free pass 依赖此性质 (HCO 理由 #5).
        """
        # Contract: 构造无 SHIFTUP 的 DFG (2 FPADD + dep), 任意 smc_num
        # Expected: delta_map[(trace_id, smc_k)] == 0 for all keys
        self.fail("v6-4 实施时填充: 构造 mock instructions + mir_ops, "
                  "调 _compute_per_smc_stagger(instructions, mir_ops, smc_num=4), "
                  "断言 all(delta == 0)")

    @unittest.skipUnless(_has_stagger.__func__(), SKIP_V6_4)
    def test_stagger_two_smc_broadcast_basic(self):
        """
        2-SMC SHIFTUP broadcast smc_id_en=1: v5.1 公式 delta_k = (smc_id_en - k) × chain_stage_latency
          → delta(rtovr_consumer, SMC_0) = 1
          → delta(rtovr_consumer, SMC_1) = 0
        最小 stagger 用例; ADR-020 v6 fma_parallel_n 主验证路径的 2-SMC 缩影.
        """
        # Contract: DFG = UR_READ → SHIFTUP (smc_id_en=1) → RTOVR (rtovr_dep on SHIFTUP)
        # Expected:
        #   delta[("rtovr_consumer", 0)] == 1
        #   delta[("rtovr_consumer", 1)] == 0
        self.fail("v6-4 实施时填充: 构造 2-SMC SHIFTUP broadcast DFG, "
                  "断言 delta_map 与 v5.1 公式严格匹配")

    @unittest.skipUnless(_has_stagger.__func__(), SKIP_V6_4)
    def test_stagger_chain_propagation_a_path(self):
        """
        SHIFTUP chain stagger 沿 a 通路 forward 传播 (v5.1 §链式 stagger 传播范围):
        UR_READ → SHIFTUP → RTOVR → FPMUL → FPADD → RTOVR → UR_WRITE
        全部 stagger 应继承 SHIFTUP→RTOVR 的 delta_k. fma_parallel_n a 通路缩影.
        """
        # Contract: 6-op a 通路 DFG, smc_id_en=1
        # Expected: 沿 a 通路所有 op 在 SMC_0 都有 delta=1, SMC_1 都 delta=0
        self.fail("v6-4 实施时填充: 构造 6-op a 通路 DFG, "
                  "断言 delta 沿 topo forward propagate 一致")

    @unittest.skipUnless(_has_stagger.__func__(), SKIP_V6_4)
    def test_stagger_backward_to_partitioned_bc(self):
        """
        Pass 2 backward BFS: b/c 通路 partitioned UR_READ 无 producer, 但若是
        a 通路 stagger consumer 的依赖, 必须**反向传播 stagger** (HCO 风险 #4).
        v5.1 §链式传播范围 L750: b_read SMC_0 必须配套 abs_t 提前 31 cycle,
        否则 FPMUL 在 stagger 后 fire 时 b 操作数缺失.
        """
        # Contract: DFG = (UR_READ_a → SHIFTUP smc_id_en=1 → RTOVR) + (UR_READ_b partitioned)
        #          → FPMUL (deps: RTOVR + UR_READ_b)
        # Expected: delta[("UR_READ_b", 0)] == 1 (反向传播)
        #           delta[("UR_READ_b", 1)] == 0
        self.fail("v6-4 实施时填充: 构造 a + b partitioned DFG, "
                  "断言 UR_READ_b 也被 backward BFS 反传 stagger")

    @unittest.skipUnless(_has_stagger.__func__(), SKIP_V6_4)
    def test_stagger_emitter_output_byte_equivalence_at_delta_zero(self):
        """
        HCO 理由 #5 free pass 属性: stagger 算法在 delta_k = 0 时
        emitter 输出与 v5.1 baseline 字节级一致.
        非 SHIFTUP-broadcast 算子 (horner/activation/matmul) 回归 free pass 依赖此性质.
        """
        # Contract: 跑 horner --mode hw-parity 一次, 比较新 emitter (含 stagger) 与
        # baseline emitter 输出 hex 字节级 diff
        # Expected: 全 SMC 全 hex 字节级一致
        self.fail("v6-4 实施时填充: horner hw-parity 端到端比对; "
                  "diff == 0 in all SMC hex files")

    @unittest.skipUnless(_has_verifier.__func__(), SKIP_V6_5)
    def test_p2_verifier_detects_post_stagger_bank_conflict(self):
        """
        ADR-020 v6 §HCO 风险 #2 反例: 构造 2 SHIFTUP-broadcast 后下游各跟 1 UR_WRITE,
        落同 Bank, II=8. SMT 按 fold 算 modulo 通过 (SMC_31 视角 r=0/r=4 不冲突),
        但 emitter post-stagger 后跨 SMC 同 Bank r 不一致 → hex 层 bank 冲突.
        P2 verifier 必须**检测出此冲突并 raise / return False**.
        """
        # Contract: 构造 II=8 + 2 SHIFTUP-broadcast + 2 UR_WRITE 同 Bank DFG
        # 跑 emit_from_instructions(smc_num=32) → 内部应调 _verify_post_stagger_resources
        # Expected: verifier raises (RuntimeError / AssertionError) 或返回 False
        self.fail("v6-5 实施时填充: 构造 v6 风险 #2 反例 DFG, "
                  "断言 emitter 在 post-stagger verify 阶段抛错")


if __name__ == "__main__":
    unittest.main()
