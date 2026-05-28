"""
ADR-018 v7 Stage 1 D1/D5.a 基础多 SMC 单元测试。

覆盖：
- HardwareSpec multi-SMC helper（get_unit_instances_per_smc / is_smc_shared_unit / get_global_unit_instances）
- hw_resource.get_per_smc_resource_id
- emitter 多 SMC emit 基础（多目录 + SMC_ID 注入差异化 hex + sim_config layout）
- LOGIC SMC_ID 4-bit fail-fast 安全检查

不覆盖（留 D5.b/c + e2e）：
- broadcast unique source axiom 触发
- chain IQ propagation axiom 触发
- cross-SMC bank conflict 实际触发（需要 op.metadata.smc_id 注入路径）
- matmul --parallel_dim=N e2e
"""
import os
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from api.hw_spec import HardwareSpec
from driver.hw_resource import PortSlot, get_per_smc_resource_id
from driver.emitter import LIRPacker
from ir.lir.lir_op import PhysicalInstruction, SMIHeader


class TestHardwareSpecMultiSMCHelpers(unittest.TestCase):
    """ADR-018 v7 D1 HardwareSpec schema 重构 helpers."""

    @classmethod
    def setUpClass(cls):
        cls.spec = HardwareSpec.load("hw_config")

    def test_per_smc_unit_instances_present(self):
        per_smc = self.spec.get_unit_instances_per_smc()
        # 应至少包含 FPMUL / FPADD / RTOVR
        self.assertIn("FPMUL", per_smc)
        self.assertIn("FPADD", per_smc)
        self.assertIn("RTOVR", per_smc)

    def test_smc_shared_units_empty(self):
        # ADR-018 v7：当前为空（预留未来真正跨 SMC 共享的 unit）
        self.assertEqual(self.spec.get_smc_shared_units(), set())

    def test_global_unit_instances_multiply_by_smc_num(self):
        per_smc = self.spec.get_unit_instances_per_smc()
        glob = self.spec.get_global_unit_instances()
        smc_num = self.spec.params.PARAM_SMC_NUM
        # 非 shared unit：global = per_smc × smc_num
        for u, v in per_smc.items():
            self.assertEqual(glob[u], v * smc_num,
                f"global_unit_instances[{u}] = {glob[u]} 应为 {v} × {smc_num}")

    def test_get_iq_ids_smc_id_param_backward_compat(self):
        # smc_id 参数默认 0（向后兼容），smc_id ≠ 0 当前返回相同结果（D5.b 阶段可能扩展）
        ids_default = self.spec.get_iq_ids("FPMUL")
        ids_smc0 = self.spec.get_iq_ids("FPMUL", smc_id=0)
        ids_smc5 = self.spec.get_iq_ids("FPMUL", smc_id=5)
        self.assertEqual(ids_default, ids_smc0)
        self.assertEqual(ids_default, ids_smc5)

    def test_assert_single_smc_mode_fails_when_smc_num_gt_1(self):
        # PARAM_SMC_NUM=32 → assert_single_smc_mode 应 raise
        with self.assertRaisesRegex(RuntimeError, r"ADR-018.*未迁移"):
            self.spec.assert_single_smc_mode(caller="test_legacy_caller")


class TestHwResourcePerSMCId(unittest.TestCase):
    """ADR-018 v7 D1 hw_resource.get_per_smc_resource_id helper."""

    def test_smc_0_base_value_unchanged(self):
        self.assertEqual(get_per_smc_resource_id(PortSlot.SHIFTUP_D, smc_id=0), 29)

    def test_smc_k_offset_distinct(self):
        # 不同 SMC 的同 enum 应产生不同的资源 ID
        ids = [get_per_smc_resource_id(PortSlot.SHIFTUP_D, smc_id=k) for k in range(32)]
        self.assertEqual(len(set(ids)), 32, "32 SMC 应产生 32 个互不相同的资源 ID")

    def test_out_of_range_raises(self):
        with self.assertRaisesRegex(ValueError, "out of range"):
            get_per_smc_resource_id(PortSlot.SHIFTUP_D, smc_id=32)
        with self.assertRaisesRegex(ValueError, "out of range"):
            get_per_smc_resource_id(PortSlot.SHIFTUP_D, smc_id=-1)


class TestEmitterMultiSMC(unittest.TestCase):
    """ADR-018 v7 D5.a emitter 多 SMC emit 基础测试."""

    @classmethod
    def setUpClass(cls):
        cls.packer = LIRPacker("hw_config", verbosity=0)

    def _make_fpmul_instr(self):
        return PhysicalInstruction(
            iq_name='fpmul',
            header=SMIHeader(),
            payload={'SRC_PREC': 1, 'DST_PREC': 1},
            abs_t=0,
            op_type='FPMUL',
            stage='BODY'
        )

    def test_single_smc_default_path_backward_compat(self):
        """N=1（不传 smc_num）走旧 smc_0/ 路径，sim_config 含 smc_id=0 字段。"""
        instr = self._make_fpmul_instr()
        with tempfile.TemporaryDirectory() as tmp:
            self.packer.emit_from_instructions([instr], tmp)
            with open(os.path.join(tmp, "sim_config.json")) as f:
                cfg = json.load(f)
            self.assertEqual(cfg["smc_id"], 0)
            self.assertNotIn("smc_num", cfg)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "smc_0")))

    def test_multi_smc_n4_creates_4_dirs_and_layout(self):
        """N=4 创建 smc_{0..3}/ 目录 + sim_config 含 per_smc_iq_init / smc_num。"""
        instr = self._make_fpmul_instr()
        with tempfile.TemporaryDirectory() as tmp:
            self.packer.emit_from_instructions([instr], tmp, smc_num=4)
            for k in range(4):
                self.assertTrue(os.path.isdir(os.path.join(tmp, f"smc_{k}")),
                    f"smc_{k}/ 应被创建")
            with open(os.path.join(tmp, "sim_config.json")) as f:
                cfg = json.load(f)
            self.assertEqual(cfg["smc_num"], 4)
            self.assertEqual(len(cfg["per_smc_iq_init"]), 4)
            self.assertEqual(cfg["smc_id_list"], [0, 1, 2, 3])

    def test_smc_id_injection_differentiates_hex(self):
        """每 SMC 的 hex 因 SMC_ID 字段不同而不同。"""
        instr = self._make_fpmul_instr()
        with tempfile.TemporaryDirectory() as tmp:
            self.packer.emit_from_instructions([instr], tmp, smc_num=4)
            hexes = []
            for k in range(4):
                with open(os.path.join(tmp, f"smc_{k}", "iq_fpmul.hex")) as f:
                    hexes.append(f.read().strip())
            # 4 个 SMC 的 hex 必须互不相同（SMC_ID 字段值分别 0,1,2,3）
            self.assertEqual(len(set(hexes)), 4,
                f"4 SMC 的 FPMUL hex 应互不相同 (SMC_ID 注入)，实际: {hexes}")

    def test_broadcast_shiftup_distribution(self):
        """
        ADR-018 v7 D5.c: broadcast SHIFTUP smc_id_en=3，N=8 时仅 SMC_0..3 emit；
        SMC_4..7 跳过此 instr。沿途 SMC_k>0 data 字段填 0。
        """
        instr = PhysicalInstruction(
            iq_name='shiftup',
            header=SMIHeader(),
            payload={'data': 0xdeadbeef, 'broadcast': 1, 'smc_id_en': 3, 'MODE': 0, 'AMOUNT': 0, 'SRC_PREC': 1, 'UPDATE_ST': 1, 'VALID': 1},
            abs_t=0,
            op_type='SHIFTUP',
            stage='BODY'
        )
        with tempfile.TemporaryDirectory() as tmp:
            self.packer.emit_from_instructions([instr], tmp, smc_num=8)
            # SMC_0..3 应有 hex；SMC_4..7 hex 为空
            for k in range(4):
                hex_path = os.path.join(tmp, f"smc_{k}", "iq_shiftup.hex")
                self.assertTrue(os.path.exists(hex_path))
                with open(hex_path) as f:
                    content = f.read().strip()
                self.assertNotEqual(content, "", f"SMC_{k} 应 emit SHIFTUP")
            for k in range(4, 8):
                hex_path = os.path.join(tmp, f"smc_{k}", "iq_shiftup.hex")
                with open(hex_path) as f:
                    content = f.read().strip()
                self.assertEqual(content, "", f"SMC_{k} 应跳过 emit (smc_id > smc_id_en)")

    def test_broadcast_smc0_data_distinct_from_followers(self):
        """
        ADR-018 v7 D5.c + ADR-019 errata task #19: SHIFTUP broadcast 沿途 SMC_k>0 data 字段填 0；
        SMC_0 含 真实 data，故 SMC_0 hex ≠ SMC_k>0 hex (data 字段差异)。
        沿途 SMC_k>0 (k=1,2,3) hex 应一致（都是 data=0 + 同 smc_id_en/broadcast）。
        """
        instr = PhysicalInstruction(
            iq_name='shiftup',
            header=SMIHeader(),
            payload={'data': 0xcafe, 'broadcast': 1, 'smc_id_en': 3, 'VALID': 1},
            abs_t=0,
            op_type='SHIFTUP',
            stage='BODY'
        )
        with tempfile.TemporaryDirectory() as tmp:
            self.packer.emit_from_instructions([instr], tmp, smc_num=4)
            hexes = []
            for k in range(4):
                with open(os.path.join(tmp, f"smc_{k}", "iq_shiftup.hex")) as f:
                    hexes.append(f.read().strip())
            # 沿途 SMC_1/2/3 (data=0) 完全一致
            self.assertEqual(hexes[1], hexes[2], "SMC_1/2 沿途 data=0 → hex 一致")
            self.assertEqual(hexes[2], hexes[3], "SMC_2/3 沿途 data=0 → hex 一致")
            # SMC_0 (data=0xcafe) 与 SMC_1 (data=0) 必不同
            self.assertNotEqual(hexes[0], hexes[1],
                "SMC_0 含 data=0xcafe vs SMC_1 data=0 必不同")

    def test_smc_id_5bit_fail_fast_at_overflow(self):
        """
        SMC_ID 5-bit (max 31)；smc_num=33 时 emit 到 SMC_32 应 fail-fast 防 silent truncate。
        注：modules.yaml LOGIC SMC_ID=4 bit 但 master file 优先 = 5 bit；实际所有 SMC_ID
        字段（FPMUL/FPADD/UR_READ/UR_WRITE/LOGIC）加载后均为 5 bit。hw_config 内部不一致
        见 hco 三审 B2 + 本测试 verify 真实 loaded 行为。
        """
        instr = self._make_fpmul_instr()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, r"ADR-018 D5\.a.*5 bit"):
                self.packer.emit_from_instructions([instr], tmp, smc_num=33)


class TestFrontendParallelDimNInjection(unittest.TestCase):
    """ADR-018 v7 D4 Stage 1 X: ConfigurableOperator parallel_dim=N SHIFTUP 注入."""

    @classmethod
    def setUpClass(cls):
        sys.path.append(str(Path(__file__).parent.parent.parent))
        cls.spec = HardwareSpec.load("hw_config")

    def _load_gemm_op(self):
        from verification_v2.ops.configurable import ConfigurableOperator
        return ConfigurableOperator("verification_v2/ops/matmul_parallel_n.yaml")

    def test_shiftup_injected_per_broadcast_input(self):
        """matmul_parallel_n 有 4 个 broadcast 输入 (a0..a3) → 应注入 4 个 SHIFTUP MIROp."""
        op = self._load_gemm_op()
        ops = op.get_dfg(self.spec)
        shiftup_ops = [o for o in ops if o.op_type == "SHIFTUP"]
        self.assertEqual(len(shiftup_ops), 4,
            f"应注入 4 个 SHIFTUP（每 broadcast 输入一个），实际 {len(shiftup_ops)}")

    def test_shiftup_metadata_axiom_triggers(self):
        """每 SHIFTUP MIROp 应 broadcast=1 + smc_id_en=PARAM_SMC_NUM-1 + smc_id=0 (OQ-1)."""
        op = self._load_gemm_op()
        ops = op.get_dfg(self.spec)
        smc_id_en_expected = self.spec.params.PARAM_SMC_NUM - 1
        for sup in [o for o in ops if o.op_type == "SHIFTUP"]:
            self.assertEqual(sup.metadata.get("broadcast"), 1,
                f"{sup.trace_id} broadcast 应为 1（D3.d 唯一源 axiom 触发条件）")
            self.assertEqual(sup.metadata.get("smc_id_en"), smc_id_en_expected,
                f"{sup.trace_id} smc_id_en 应为 PARAM_SMC_NUM-1={smc_id_en_expected}")
            self.assertEqual(sup.metadata.get("smc_id"), 0,
                f"{sup.trace_id} smc_id 应为 0 (OQ-1 数据源固定 SMC_0)")

    def test_consumer_rtovr_sel_rewritten_to_shiftup_d(self):
        """consumer RTOVR 的 sel 应改为 29 (rtovr_config.yaml consumers_dr.29 = SHIFTUP_D)."""
        op = self._load_gemm_op()
        ops = op.get_dfg(self.spec)
        shiftup_ids = {o.trace_id for o in ops if o.op_type == "SHIFTUP"}
        rtovr_after_shiftup = [
            o for o in ops
            if o.op_type == "RTOVR"
            and any(d in shiftup_ids for d in o.metadata.get("depends_on", []))
        ]
        self.assertGreater(len(rtovr_after_shiftup), 0,
            "应有至少 1 个 RTOVR 在 SHIFTUP 后")
        for r in rtovr_after_shiftup:
            self.assertEqual(r.metadata.get("sel"), 29,
                f"{r.trace_id} sel 应为 29 (SHIFTUP_D)，实际 {r.metadata.get('sel')}")

    def test_no_stale_ur_read_rtovr_deps_on_fpmul(self):
        """下游 FPMUL 的 rtovr_deps 不应残留 broadcast 输入 UR_READ 条目
        （否则会同时约束 t_fpmul == t_ur_read+lat+1 和 t_shiftup+lat+1 → 过约束）."""
        op = self._load_gemm_op()
        ops = op.get_dfg(self.spec)
        broadcast_ur_ids = {
            o.trace_id for o in ops
            if o.op_type == "UR_READ"
            and o.metadata.get("meta_name") in {"a0", "a1", "a2", "a3"}
            and o.metadata.get("role") == "input"
        }
        for fpm in [o for o in ops if o.op_type == "FPMUL"]:
            rdeps = fpm.metadata.get("rtovr_deps", {})
            stale = set(rdeps.keys()) & broadcast_ur_ids
            self.assertEqual(stale, set(),
                f"{fpm.trace_id} rtovr_deps 残留 broadcast UR_READ 键 {stale}")

    def test_parallel_dim_none_no_injection_backward_compat(self):
        """parallel_dim 未设的 yaml（如 horner_v3） get_dfg 不应注入任何 SHIFTUP."""
        from verification_v2.ops.configurable import ConfigurableOperator
        op = ConfigurableOperator("verification_v2/ops/horner_v3.yaml")
        ops = op.get_dfg(self.spec)
        shiftup_ops = [o for o in ops if o.op_type == "SHIFTUP"]
        self.assertEqual(len(shiftup_ops), 0,
            "parallel_dim=None 应保持单 SMC 模式，无 SHIFTUP 注入")


class TestShiftupSmtIntegration(unittest.TestCase):
    """
    ADR-018 v7 Stage 1 X: SHIFTUP MIROp → ucode_solver 端到端 SMT 集成。

    test_smt_adversarial 已守 D3.d/e axiom 单独触发；本套守:
    ConfigurableOperator get_dfg → ucode_solver.solve_with_units 完整链路 SAT，
    且 SHIFTUP 获分配 iq_id=29 (SHIFTUP_D / iq_registry IQ_SHIFTUP).
    """

    @classmethod
    def setUpClass(cls):
        sys.path.append(str(Path(__file__).parent.parent.parent))
        cls.spec = HardwareSpec.load("hw_config")

    def test_fma_parallel_n_sat_with_shiftup(self):
        from verification_v2.ops.configurable import ConfigurableOperator
        from synthesis.ucode_solver import UCodeSynthesizer

        op = ConfigurableOperator("verification_v2/ops/fma_parallel_n.yaml")
        ops = op.get_dfg(self.spec)
        shiftup_ops = [o for o in ops if o.op_type == "SHIFTUP"]
        self.assertEqual(len(shiftup_ops), 1,
            "fma_parallel_n 单 broadcast 输入 (a) → 1 个 SHIFTUP MIROp")

        syn = UCodeSynthesizer(self.spec)
        res = syn.solve_with_units(ops, MII=1, MAX_II=8)
        self.assertIsNotNone(res, "fma_parallel_n DFG 应 SAT；若 None 则 SHIFTUP 集成回退")
        self.assertLessEqual(res["II"], 4,
            f"FMA + 单 SHIFTUP 应 SAT II≤4，实际 II={res['II']}")

        # SHIFTUP 获得 iq_id (IQ_SHIFTUP=29 via iq_registry)
        sup = shiftup_ops[0]
        self.assertEqual(sup.metadata.get("iq_id"), 29,
            f"SHIFTUP iq_id 应为 29 (iq_registry.IQ_SHIFTUP)，实际 {sup.metadata.get('iq_id')}")
        # schedule 含 SHIFTUP 时间
        self.assertIn(sup.trace_id, res["schedule"],
            "SHIFTUP MIROp 应在 ucode_solver schedule 中")

    def test_shiftup_axiom_metadata_intact_after_solve(self):
        """solve 后 SHIFTUP 的 broadcast / smc_id_en 不应被 unit_assignments 后处理清掉
        （否则下游 emitter D5.c 无法 dispatch broadcast hex）。"""
        from verification_v2.ops.configurable import ConfigurableOperator
        from synthesis.ucode_solver import UCodeSynthesizer

        op = ConfigurableOperator("verification_v2/ops/fma_parallel_n.yaml")
        ops = op.get_dfg(self.spec)
        syn = UCodeSynthesizer(self.spec)
        syn.solve_with_units(ops, MII=1, MAX_II=8)

        sup = next(o for o in ops if o.op_type == "SHIFTUP")
        self.assertEqual(sup.metadata.get("broadcast"), 1)
        self.assertEqual(sup.metadata.get("smc_id_en"),
                         self.spec.params.PARAM_SMC_NUM - 1)
        self.assertEqual(sup.metadata.get("smc_id"), 0)


class TestShiftupMirToLirLowering(unittest.TestCase):
    """
    ADR-018 v7 Stage 1 X (task #18 sub-4): MIR→LIR lowering 验证。
    SHIFTUP MIROp → PhysicalAssembler → PhysicalInstruction(iq_name='shiftup',
    payload 含 BROADCAST/SMC_ID_EN/VALID)。
    历史 bug：UnitPayloadFactory 大写 field 名查 lowercase MIROp metadata 失败 →
    BROADCAST/SMC_ID_EN 退化为 default 0；本类守 fix 不回退。
    """

    @classmethod
    def setUpClass(cls):
        sys.path.append(str(Path(__file__).parent.parent.parent))
        cls.spec = HardwareSpec.load("hw_config")

    def _build_physical_instrs(self, yaml_path: str):
        from verification_v2.ops.configurable import ConfigurableOperator
        from synthesis.ucode_solver import UCodeSynthesizer
        from driver.assembler import PhysicalAssembler

        op = ConfigurableOperator(yaml_path)
        ops = op.get_dfg(self.spec)
        syn = UCodeSynthesizer(self.spec)
        res = syn.solve_with_units(ops, MII=1, MAX_II=8)
        self.assertIsNotNone(res, "SMT solve 应 SAT，前置 sub-3 已保证")
        for o in ops:
            o.metadata["global_t"] = res["schedule"][o.trace_id]
            o.metadata["unit_id"] = res.get("unit_assignments", {}).get(o.trace_id)
        return PhysicalAssembler(self.spec).assemble(
            ops, II=res["II"], num_iterations=1
        )

    def test_shiftup_physical_instr_emitted(self):
        """SHIFTUP MIROp 应产 iq_name='shiftup' 的 PhysicalInstruction (INIT+HDR+BODY)."""
        instrs = self._build_physical_instrs("verification_v2/ops/fma_parallel_n.yaml")
        sup_instrs = [p for p in instrs if p.iq_name == "shiftup"]
        stages = sorted(p.stage for p in sup_instrs)
        self.assertIn("INIT", stages)
        self.assertIn("HDR", stages)
        self.assertIn("BODY", stages)
        for p in sup_instrs:
            self.assertEqual(p.op_type.upper(), "SHIFTUP")

    def test_shiftup_body_payload_axiom_fields_intact(self):
        """SHIFTUP BODY payload 应有 BROADCAST=1 / SMC_ID_EN=31 / VALID=1（非 default 0）.
        前置 bug：MIROp metadata 用 lowercase 'broadcast'/'smc_id_en'，
        UnitPayloadFactory upper-case 查不到 → 退化 default 0。本测试守
        UnitPayloadFactory case-insensitive metadata lookup 修复。"""
        instrs = self._build_physical_instrs("verification_v2/ops/fma_parallel_n.yaml")
        sup_body = next(
            p for p in instrs if p.iq_name == "shiftup" and p.stage == "BODY"
        )
        self.assertEqual(sup_body.payload.get("VALID"), 1)
        self.assertEqual(sup_body.payload.get("BROADCAST"), 1,
            f"BROADCAST 应为 1，实际 {sup_body.payload.get('BROADCAST')} "
            "（若为 0：UnitPayloadFactory 没找到 lowercase 'broadcast'，触发回归）")
        smc_id_en_expected = self.spec.params.PARAM_SMC_NUM - 1
        self.assertEqual(sup_body.payload.get("SMC_ID_EN"), smc_id_en_expected,
            f"SMC_ID_EN 应为 PARAM_SMC_NUM-1={smc_id_en_expected}")

    def test_payload_factory_case_insensitive_metadata_lookup(self):
        """UnitPayloadFactory 应支持 lowercase metadata key 查 UPPERCASE code_field name."""
        from driver.payload_factory import UnitPayloadFactory
        from ir.mir.mir_op import MIROp
        from driver.hw_resource import ResourceType as RT

        # 构造最小 SHIFTUP MIROp（lowercase metadata）
        sup = MIROp(
            op_type="SHIFTUP", logic_cycle=0, trace_id="test_sup",
            metadata={"iq_type": RT.SHIFTUP, "broadcast": 1, "smc_id_en": 17},
        )
        payload = UnitPayloadFactory.build(sup, {}, {}, self.spec)
        self.assertEqual(payload.get("BROADCAST"), 1,
            "lowercase 'broadcast'=1 应被 UnitPayloadFactory 识别 → BROADCAST=1")
        self.assertEqual(payload.get("SMC_ID_EN"), 17,
            "lowercase 'smc_id_en'=17 应被识别 → SMC_ID_EN=17")
        self.assertEqual(payload.get("VALID"), 1)


class TestPipelineRunnerSmcNumDispatch(unittest.TestCase):
    """
    ADR-018 v7 Stage 1 X (task #18 sub-5): pipeline_runner 多 SMC dispatch。
    PipelineRunner._compute_smc_num 据 operator.parallel_dim 决定 emit smc_num，
    传给 emitter.emit_from_instructions（None / PARAM_SMC_NUM）。
    """

    @classmethod
    def setUpClass(cls):
        sys.path.append(str(Path(__file__).parent.parent.parent))
        from verification_v2.infra.pipeline_runner import PipelineRunner
        cls.runner = PipelineRunner(hw_config_name="hw_config", show_review=False)
        cls.PARAM_SMC_NUM = cls.runner.spec.params.PARAM_SMC_NUM

    def test_no_parallel_dim_returns_none(self):
        """单 SMC 算子（无 parallel_dim 属性 / =None）→ smc_num None（单 SMC emit 路径）."""
        class _SingleSmcOp:
            pass
        self.assertIsNone(self.runner._compute_smc_num(_SingleSmcOp()))

        class _NoneOp:
            parallel_dim = None
        self.assertIsNone(self.runner._compute_smc_num(_NoneOp()))

    def test_parallel_dim_n_returns_param_smc_num(self):
        """parallel_dim='N' → smc_num = PARAM_SMC_NUM (32)."""
        class _ParallelN:
            parallel_dim = "N"
        self.assertEqual(self.runner._compute_smc_num(_ParallelN()), self.PARAM_SMC_NUM)

    def test_parallel_dim_k_returns_param_smc_num(self):
        """parallel_dim='K' (Stage 2) 同样触发多 SMC emit (smc_num=PARAM_SMC_NUM)."""
        class _ParallelK:
            parallel_dim = "K"
        self.assertEqual(self.runner._compute_smc_num(_ParallelK()), self.PARAM_SMC_NUM)

    def test_configurable_op_fma_parallel_n_dispatches_multi_smc(self):
        """加载 fma_parallel_n.yaml → ConfigurableOperator.parallel_dim='N' → smc_num=32."""
        from verification_v2.ops.configurable import ConfigurableOperator
        op = ConfigurableOperator("verification_v2/ops/fma_parallel_n.yaml")
        self.assertEqual(self.runner._compute_smc_num(op), self.PARAM_SMC_NUM)

    def test_configurable_op_no_parallel_dim_dispatches_single_smc(self):
        """加载未带 parallel_dim 的 yaml（如 horner_v3） → smc_num=None."""
        from verification_v2.ops.configurable import ConfigurableOperator
        op = ConfigurableOperator("verification_v2/ops/horner_v3.yaml")
        self.assertIsNone(self.runner._compute_smc_num(op))


class TestPhysicalRunnerMultiSmcDispatch(unittest.TestCase):
    """
    ADR-018 v7 Stage 1 X (task #18 sub-6): physical_runner 据 sim_config.smc_num
    分流到 _run_multi_smc (SystemCluster) 路径。
    """

    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).parent.parent.parent
        sys.path.append(str(project_root))
        sys.path.append(str(project_root / "sim"))

    def test_single_smc_config_routes_to_parallelkernel(self):
        """sim_config 无 smc_num (旧单 SMC layout) → 走 ParallelKernel 路径 (向后兼容)."""
        import json
        from unittest.mock import patch
        from pathlib import Path as P
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = P(tmp)
            (tmp_path / "smc_0").mkdir()
            cfg = {"smc_id": 0, "iq_init": {}, "ur_init": {}, "tensors": [], "max_cycles": 1}
            (tmp_path / "sim_config.json").write_text(json.dumps(cfg))
            (tmp_path / "golden_data.npz").touch()  # 防 golden_file not found 早退

            from smc_simulator.tools import physical_runner as pr
            with patch.object(pr, "_run_multi_smc") as mock_multi:
                # 不执行真 sim；ParallelKernel.run 可能失败但本测试只验路由
                try:
                    pr.run_physical_verification(str(tmp_path))
                except Exception:
                    pass
                self.assertFalse(mock_multi.called,
                    "smc_num 缺省 → 不应进 _run_multi_smc")

    def test_multi_smc_config_routes_to_system_cluster(self):
        """sim_config.smc_num >= 2 → 走 _run_multi_smc (SystemCluster) 路径."""
        import json
        from unittest.mock import patch, MagicMock
        from pathlib import Path as P
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = P(tmp)
            cfg = {
                "smc_num": 4,
                "smc_id_list": [0, 1, 2, 3],
                "per_smc_iq_init": [{}] * 4,
                "per_smc_ur_init": [{}] * 4,
                "tensors": [],
                "max_cycles": 1,
            }
            (tmp_path / "sim_config.json").write_text(json.dumps(cfg))

            from smc_simulator.tools import physical_runner as pr
            with patch.object(pr, "_run_multi_smc", return_value=True) as mock_multi:
                pr.run_physical_verification(str(tmp_path))
                mock_multi.assert_called_once()
                args, _ = mock_multi.call_args
                self.assertEqual(args[2], 4, "_run_multi_smc 应收 smc_num=4")


if __name__ == "__main__":
    unittest.main()
