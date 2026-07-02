import unittest
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from ir.mir.mir_op import MIROp
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS
from api.hw_spec import HardwareSpec
# ADR-016: 反证测试迁移到 ucode_solver 主路径
from synthesis.ucode_solver import UCodeSynthesizer, SolverStatus
from driver.emitter import LIRPacker

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
        # ADR-048：latency 对齐（FP16MUL 4→5 / ADDTREE8TO1 3→4）→ 调度 horizon 变大 → z3 证 UNSAT 均时
        #   ~20→24s、贴近默认 30s、z3 变异偶尔超时返 UNKNOWN（非 SAT、约束系统仍正确；90s 直跑实测 20.9s 出 UNSAT）。
        #   给 prover 更大超时（断言仍 UNSAT、不削弱），消除边界 flake。
        status, _ = self.synthesizer._solve_for_ii_with_units(ops, II=II, timeout_ms=90_000)
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

    def _fp16mul_fanout_graph(self):
        """镜像 ADR-032 S2 满 32 乘的输出侧 crux：1 FP16MUL → 8 个 RTOVR（D0-D7），
        4 个写 ADDTREE8TO1_S0（槽 32）、4 个写 S1（槽 33），每 2 个（D{2c}/D{2c+1}）喂 1 条
        ADDTREE8TO1（iq28，4 条串行）。每条 tree 要求其 S0/S1 两 RTOVR **同拍**锁存
        (consumer==latch+1)，4 条 tree 串行 → 4 组锁存拍互异 → 8 个 RTOVR 必须在 FP16MUL
        常驻窗口内**错拍**采样。strict `==` 钉死全部同拍 → 4 条 tree 被逼同拍同单元 → UNSAT。"""
        b = MIROpBuilder()
        u0 = b.make_op("UR_READ", "u0", RT.UR_READ, [], "U0", iq_id=30)
        u1 = b.make_op("UR_READ", "u1", RT.UR_READ, [], "U1", iq_id=31)
        ri0 = b.make_op("RTOVR", "ri0", PS.FP16MUL_S0, [u0])
        ri1 = b.make_op("RTOVR", "ri1", PS.FP16MUL_S4567, [u1])
        mul = b.make_op("FP16MUL", "mul", RT.FP16MUL, [ri0, ri1], iq_id=18)
        for c in range(4):
            rlo = b.make_op("RTOVR", f"rd{2*c}", PS.FP32ADDTREE8TO1_S0, [mul], dep_port=f"D{2*c}")
            rhi = b.make_op("RTOVR", f"rd{2*c+1}", PS.FP32ADDTREE8TO1_S1, [mul], dep_port=f"D{2*c+1}")
            b.make_op("FP32ADDTREE8TO1", f"t{c}", RT.FP32ADDTREE8TO1, [rlo, rhi], iq_id=28)
        return b.get_dfg()

    def test_fp16mul_resident_window_enables_sat(self):
        """
        [反证 R11 · ADR-032 S2] FP16MUL 输出 producer-resident 放宽守门（用真算子 DFG）。

        满 32 乘真算子 DFG：1 FP16MUL → 8 RTOVR（4 抢 ADDTREE8TO1_S0、4 抢 S1）+ 输入侧
        4 权重 + SHIFTUP 激活。8 个输出 RTOVR 必须在 FP16MUL 常驻窗口内**错拍**采样不同 D。
          - resident 放宽生效（dependency_axioms `prod_is_resident` 含 FP16MUL）→ II=10 SAT。
          - 若 FP16MUL 分支被误删（还原 strict `t_latch==prod.t+lat`）→ 8 RTOVR 钉死同拍、
            4 条 tree 被逼同单元同拍 → **MAX_II 内全 UNSAT**（实测 patch 掉分支后 II≤20 全
            UNSAT）→ 本断言 res 为 None → 失败（回归即暴露）。
        与 `test_fp16_matmul_q.py`（验数值）互补：本测试钉住 SMT 放宽语义本身。
        """
        from verification_v2.ops.matmul_op import ManualFp16MatmulQOperator
        ops = ManualFp16MatmulQOperator().get_dfg(spec=self.spec)
        res = self.synthesizer.solve_with_units(ops, MII=1, MAX_II=16)
        self.assertIsNotNone(res,
            msg="满 32 乘真 DFG 应 SAT；若 UNSAT 则 prod_is_resident 的 FP16MUL 分支失效")
        self.assertGreaterEqual(res["II"], 4,
            msg="4 条 ADDTREE8TO1 串行于 iq28 → II>=4 下界")

    def test_fp16mul_fanout_below_addtree_bound_unsat(self):
        """[反证 R11b] 最小图 II=3 必 UNSAT：4 条 ADDTREE8TO1 串行于单元 iq28 鸽巢下界 II>=4。
        守 addtree 单元发射排他（与 resident 放宽正交，放宽不会过度放水越过此下界）。"""
        import copy
        ops = self._fp16mul_fanout_graph()
        self._assert_unsat(copy.deepcopy(ops), II=3,
            msg="II=3 应 UNSAT（4 ADDTREE8TO1 串行下界 II=4）；若 SAT 则 addtree 单元排他漏洞")


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
        # ADR-048：latency 对齐（FP16MUL 4→5 / ADDTREE8TO1 3→4）→ 调度 horizon 变大 → z3 证 UNSAT 均时
        #   ~20→24s、贴近默认 30s、z3 变异偶尔超时返 UNKNOWN（非 SAT、约束系统仍正确；90s 直跑实测 20.9s 出 UNSAT）。
        #   给 prover 更大超时（断言仍 UNSAT、不削弱），消除边界 flake。
        status, _ = self.synthesizer._solve_for_ii_with_units(ops, II=II, timeout_ms=90_000)
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
        b = MIROpBuilder()
        a = b.make_op("FPADD", "a", RT.FPADD, [], "A")
        b.make_op("FPADD", "b_op", RT.FPADD, [a], "B")
        ops = b.get_dfg()

        delta_per_op, delta_per_smc = LIRPacker._compute_per_smc_stagger(ops, smc_num=4)

        for (tid, k), d in delta_per_op.items():
            self.assertEqual(d, 0, f"non-SHIFTUP delta@({tid},SMC_{k}) 应=0, 实测={d}")
        for k, d in delta_per_smc.items():
            self.assertEqual(d, 0, f"non-SHIFTUP delta_per_smc[SMC_{k}] 应=0, 实测={d}")

    @unittest.skipUnless(_has_stagger.__func__(), SKIP_V6_4)
    def test_stagger_two_smc_broadcast_basic(self):
        """
        2-SMC SHIFTUP broadcast smc_id_en=1: v5.1 公式 delta_k = (smc_id_en - k) × chain_stage_latency
          → delta(rtovr_consumer, SMC_0) = 1
          → delta(rtovr_consumer, SMC_1) = 0
        最小 stagger 用例; ADR-020 v6 fma_parallel_n 主验证路径的 2-SMC 缩影.
        """
        b = MIROpBuilder()
        ur = b.make_op("UR_READ", "ur_a", RT.UR_READ, [], "URA")
        b.make_op("SHIFTUP", "shiftup_a", RT.SHIFTUP, [ur], "SUA",
                  broadcast=1, smc_id_en=1)
        b.make_op("RTOVR", "rt_a", PS.SHIFTUP_D, [], "RTA")
        ops = b.get_dfg()
        next(o for o in ops if o.trace_id == "rt_a").metadata["rtovr_deps"] = {"shiftup_a": {}}

        delta_per_op, delta_per_smc = LIRPacker._compute_per_smc_stagger(
            ops, smc_num=2, chain_stage_latency=1
        )

        # SMC_0: delta = (smc_id_en - 0) × 1 = 1; SMC_1: delta = 0
        self.assertEqual(delta_per_op[("rt_a", 0)], 1, "rtovr SMC_0 delta 应=1")
        self.assertEqual(delta_per_op[("rt_a", 1)], 0, "rtovr SMC_1 delta 应=0")
        self.assertEqual(delta_per_smc[0], 1, "delta_per_smc[0] = 1")
        self.assertEqual(delta_per_smc[1], 0, "delta_per_smc[1] = 0")

    @unittest.skipUnless(_has_stagger.__func__(), SKIP_V6_4)
    def test_stagger_chain_propagation_a_path(self):
        """
        SHIFTUP chain stagger 沿 a 通路 forward 传播 (v5.1 §链式 stagger 传播范围):
        UR_READ → SHIFTUP → RTOVR → FPMUL → FPADD → RTOVR → UR_WRITE
        全部 stagger 应继承 SHIFTUP→RTOVR 的 delta_k. fma_parallel_n a 通路缩影.
        """
        b = MIROpBuilder()
        ur_a = b.make_op("UR_READ", "ur_a", RT.UR_READ, [], "URA")
        b.make_op("SHIFTUP", "shiftup_a", RT.SHIFTUP, [ur_a], "SUA",
                  broadcast=1, smc_id_en=1)
        b.make_op("RTOVR", "rt_a", PS.SHIFTUP_D, [], "RT")
        b.make_op("FPMUL", "mul", RT.FPMUL, ["rt_a"], "M")
        b.make_op("FPADD", "add", RT.FPADD, ["mul"], "A")
        b.make_op("RTOVR", "rt_add", PS.UR_W0_S, [], "RT2")
        b.make_op("UR_WRITE", "ur_w", RT.UR_WRITE, ["rt_add"], "URW")
        ops = b.get_dfg()
        next(o for o in ops if o.trace_id == "rt_a").metadata["rtovr_deps"] = {"shiftup_a": {}}
        next(o for o in ops if o.trace_id == "rt_add").metadata["rtovr_deps"] = {"add": {}}

        delta_per_op, _ = LIRPacker._compute_per_smc_stagger(
            ops, smc_num=2, chain_stage_latency=1
        )

        a_path = ["rt_a", "mul", "add", "rt_add", "ur_w"]
        for tid in a_path:
            self.assertEqual(delta_per_op[(tid, 0)], 1,
                f"a-path {tid} SMC_0 delta 应=1 (forward propagation)")
            self.assertEqual(delta_per_op[(tid, 1)], 0,
                f"a-path {tid} SMC_1 delta 应=0")

    @unittest.skipUnless(_has_stagger.__func__(), SKIP_V6_4)
    def test_stagger_backward_to_partitioned_bc(self):
        """
        Pass 2 backward BFS: b/c 通路 partitioned UR_READ 无 producer, 但若是
        a 通路 stagger consumer 的依赖, 必须**反向传播 stagger** (HCO 风险 #4).
        v5.1 §链式传播范围 L750: b_read SMC_0 必须配套 abs_t 提前 31 cycle,
        否则 FPMUL 在 stagger 后 fire 时 b 操作数缺失.
        """
        b = MIROpBuilder()
        ur_a = b.make_op("UR_READ", "ur_a", RT.UR_READ, [], "URA")
        b.make_op("SHIFTUP", "shiftup_a", RT.SHIFTUP, [ur_a], "SUA",
                  broadcast=1, smc_id_en=1)
        b.make_op("RTOVR", "rt_a", PS.SHIFTUP_D, [], "RTA")
        b.make_op("UR_READ", "ur_b", RT.UR_READ, [], "URB")
        b.make_op("FPMUL", "mul", RT.FPMUL, ["rt_a", "ur_b"], "M")
        ops = b.get_dfg()
        next(o for o in ops if o.trace_id == "rt_a").metadata["rtovr_deps"] = {"shiftup_a": {}}

        delta_per_op, _ = LIRPacker._compute_per_smc_stagger(
            ops, smc_num=2, chain_stage_latency=1
        )

        # FPMUL 因 a 通路 stagger → delta_per_op[(mul, 0)] = 1
        self.assertEqual(delta_per_op[("mul", 0)], 1,
            "FPMUL SMC_0 delta 应=1 (a 通路 forward propagation)")
        # ur_b 必须被 backward BFS 反传 stagger (否则 FPMUL fire 时 ur_b 数据缺失)
        self.assertEqual(delta_per_op[("ur_b", 0)], 1,
            "ur_b SMC_0 delta 应=1 (backward BFS 反传, HCO 风险 #4)")
        self.assertEqual(delta_per_op[("ur_b", 1)], 0,
            "ur_b SMC_1 delta 应=0")

    @unittest.skipUnless(_has_stagger.__func__(), SKIP_V6_4)
    def test_stagger_emitter_output_byte_equivalence_at_delta_zero(self):
        """
        HCO 理由 #5 free pass 属性: stagger 算法在 delta_k = 0 时
        emitter 输出与 v5.1 baseline 字节级一致.
        非 SHIFTUP-broadcast 算子 (horner/activation/matmul) 回归 free pass 依赖此性质.
        """
        b = MIROpBuilder()
        a = b.make_op("FPADD", "a", RT.FPADD, [], "A")
        c1 = b.make_op("FPADD", "c1", RT.FPADD, [a], "C1")
        b.make_op("FPADD", "c2", RT.FPADD, [c1], "C2")
        ops = b.get_dfg()

        _, delta_per_smc = LIRPacker._compute_per_smc_stagger(ops, smc_num=4)

        # delta_per_smc 全 0 → INIT loops 不动 → emitter 输出 baseline 字节级一致 (proxy 自检);
        # 端到端字节级 diff 由 v6-9 全量回归 horner/activation/matmul cover.
        for k, d in delta_per_smc.items():
            self.assertEqual(d, 0,
                f"非 chain DFG delta_per_smc[SMC_{k}] 应=0, 实测={d}; "
                f"否则非 chain 算子 INIT loops 会被错改导致 baseline 字节漂动")

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


class TestFp16MulLoadLatchLatency(unittest.TestCase):
    """ADR-049: fp16mul load 微指令 = latch latency(1) 非乘法 latency(5)。"""

    def setUp(self):
        self.spec = HardwareSpec.load("hw_config")
        self.synthesizer = UCodeSynthesizer(self.spec)

    def test_micro_delay_contract(self):
        """ADR-049 D1：micro_delay 对 load 组返 1、compute/未命中 fallback 单元 delays（=5）。
        守 modules.yaml load_s0123/s4567 的 latency:1 + MicroGroup.latency 字段 + helper 契约。"""
        self.assertEqual(self.spec.micro_delay("FP16MUL", "load_s0123"), 1)
        self.assertEqual(self.spec.micro_delay("FP16MUL", "load_s4567"), 1)
        # compute（无 per-group latency）+ 未命中 variant → fallback 单元 latency
        self.assertEqual(self.spec.micro_delay("FP16MUL", "compute"), self.spec.delays["FP16MUL"])
        self.assertEqual(self.spec.micro_delay("FP16MUL", None), self.spec.delays["FP16MUL"])
        self.assertNotEqual(self.spec.delays["FP16MUL"], 1)  # 前提：compute 口径确实 != latch

    def test_load_operand_survives_loop(self):
        """ADR-049 D2/D3：K16 循环核（load→load→compute + loop-carried 累加）中，compute 采样操作数
        寄存器须落在其活窗内——t(compute) - t(load_s0123) < II，否则 iter1 的 load 抢先覆盖（WAR，
        VCS 实证 157.365≠155.14）。旧模型（load 边=5）会把 compute 推到 load 后 10 拍 >> II → 本断言失败。"""
        import sys
        from pathlib import Path
        sys.path.append(str(Path(__file__).parent.parent.parent))
        from verification_v2.ops.matmul_op import ManualFp16MatmulK16Operator
        op = ManualFp16MatmulK16Operator()
        ops = op.get_dfg(self.spec)
        res = self.synthesizer.solve_with_units(ops, MII=1, MAX_II=32)
        self.assertIsNotNone(res)
        sched = res["schedule"]   # {trace_id: 绝对调度拍 t}
        II = res["II"]
        t_load = sched["load_s0123"]
        t_compute = sched["inl_fp16mul"]
        self.assertLess(t_compute - t_load, II,
                        f"操作数活窗违规: compute@{t_compute} - load@{t_load} = {t_compute-t_load} >= II={II} "
                        f"→ iter1 load 会覆盖 s0（ADR-049 WAR）")


if __name__ == "__main__":
    unittest.main()
