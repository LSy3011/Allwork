"""ADR-033 Stage-A 可证伪单测：SMI_DS_T 跨-SMC 转置核（numeric + ordering + scalable）。

覆盖 ADR-033 D6 门中不依赖 e2e 的部分：
  - test_transpose_numeric         （门 1）：非对称 Tile 真转置，no-op/view 实现必 fail
  - test_no_op_view_would_fail     （门 1 对偶）：identity 实现在非对称 Tile 上确实算错
  - test_param_smc_num_scalable    （门 4）：转置维度随 smc_num/lanes 变，无硬编码
  - test_transpose_before_gather_raises（门 5 unit 级）：gather 未到齐 transpose 抛错
  - test_is_cross_smc_all_smc_gathered （门 2 unit 级）：输入来自所有 SMC，非单 SMC

跨-SMC e2e（门 3 test_matmul_via_smi_ds_t_datapath）见 Increment 3。
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "sim"))

from smc_simulator.units.smi_ds_t_unit import SmiDsTUnit


class TestSmiDsTTranspose(unittest.TestCase):
    def test_transpose_numeric(self):
        """门 1：非对称 8×4 Tile 转置为 4×8，out[l][s] == tile[s][l]（[seq1037-1039]）。"""
        smc_num, lanes = 8, 4
        # 非对称、各元素唯一可区分（s*10 + l），杜绝对称巧合
        tile = [[s * 10 + l for l in range(lanes)] for s in range(smc_num)]
        out = SmiDsTUnit.transpose_tile(tile)
        self.assertEqual(len(out), lanes)          # 输出 lanes 行
        self.assertEqual(len(out[0]), smc_num)     # 每行 smc_num 列
        for l in range(lanes):
            for s in range(smc_num):
                self.assertEqual(out[l][s], tile[s][l], f"transpose mismatch at [{l}][{s}]")
        # 转置后某一行 = 从所有 SMC 取同一 lane（行对齐数据）
        self.assertEqual(out[0], [s * 10 + 0 for s in range(smc_num)])  # lane0 across all SMC
        self.assertEqual(out[3], [s * 10 + 3 for s in range(smc_num)])

    def test_no_op_view_would_fail(self):
        """门 1 对偶：若实现成 no-op/view（直接返回 tile 不搬），非对称 Tile 必算错。
        专杀 ADR-026 B4 教训——激活 transpose 当 no-op 会拿列对齐错数据 → 静默算错。"""
        smc_num, lanes = 8, 4
        tile = [[s * 10 + l for l in range(lanes)] for s in range(smc_num)]
        real = SmiDsTUnit.transpose_tile(tile)
        identity = [list(r) for r in tile]          # 假 no-op 实现
        # 形状就不同（8×4 vs 4×8），且内容不同 → 真转置 != identity
        self.assertNotEqual(len(real), len(identity))
        self.assertNotEqual(real, identity)

    def test_param_smc_num_scalable(self):
        """门 4：转置维度随 smc_num / lanes 参数变，无硬编码 8。"""
        for smc_num, lanes in [(4, 4), (8, 4), (8, 2), (16, 4), (2, 8)]:
            tile = [[s * 100 + l for l in range(lanes)] for s in range(smc_num)]
            out = SmiDsTUnit.transpose_tile(tile)
            self.assertEqual(len(out), lanes, f"smc_num={smc_num} lanes={lanes}")
            self.assertEqual(len(out[0]), smc_num)
            for l in range(lanes):
                for s in range(smc_num):
                    self.assertEqual(out[l][s], tile[s][l])

    def test_ragged_tile_rejected(self):
        """防御：行长不一致的 Tile 应拒绝（非静默截断）。"""
        with self.assertRaises(ValueError):
            SmiDsTUnit.transpose_tile([[1, 2, 3], [4, 5]])


class TestSmiDsTClusterApply(unittest.TestCase):
    """ADR-036 S3-c：cluster.apply_smi_ds_t（model-A run-前原子 backdoor）+ SmiDsTUnit 的
    gather/is_ready/missing/transpose gate 方法已删。跨-SMC 转置数值正确性现由 TestSmiDsTTranspose
    （transpose_tile 静态纯核）+ TestSmiDsTS2Streaming（流式 evaluate/tick）+ e2e 覆盖。本类仅留
    codec 往返单测（与 emitter/_read_sim_output 打包约定一致）。"""

    def test_word_pack_roundtrip(self):
        """pack/unpack fp32 lane 往返一致（与 emitter/_read_sim_output 约定一致）。"""
        from smc_simulator.units.smi_ds_t_unit import word_from_fp32_lanes, fp32_lanes_from_word
        vals = [1.0, -2.5, 3.14159, 100.0]
        w = word_from_fp32_lanes(vals)
        back = fp32_lanes_from_word(w, 4)
        for a, b in zip(vals, back):
            self.assertAlmostEqual(a, b, places=4)


class TestSmiDsTModuleSpec(unittest.TestCase):
    """ADR-034 D7：SMI_DS_T 升指令驱动单元后的配置链守门（hco BLOCK：防 system_core.yaml 漏改）。"""

    def test_smi_ds_t_iq_binding(self):
        """hco 专杀 D2 配置链漏改：get_iq_ids 非空、base id 不撞、res_to_iqs != [0]（防回退 FPADD slot）。"""
        sys.path.append(str(_ROOT / "src"))
        from api.hw_spec import HardwareSpec
        spec = HardwareSpec.load("hw_config")
        self.assertIn("SMI_DS_T", spec.modules)
        m = spec.modules["SMI_DS_T"]
        # ADR-034 D1：去 simulation_only（指令驱动真单元）
        self.assertFalse(getattr(m, "simulation_only", False),
                         "SMI_DS_T 升指令驱动单元后不应 simulation_only")
        self.assertEqual(m.code_bits, 5)
        # D2 配置链：get_iq_ids 必返非空（漏 system_core.yaml unit_instances/iq_registry → 返 []）
        iq_ids = spec.get_iq_ids("SMI_DS_T")
        self.assertEqual(iq_ids, [40], f"SMI_DS_T iq id 应 = [40]，实得 {iq_ids}")
        # base id 不与密集区冲突
        for taken in (29, 30, 34, 35):   # SHIFTUP/UR_READ/SHIFTDN/UR_WRITE
            self.assertNotIn(taken, iq_ids, f"SMI_DS_T base id 撞 {taken}")
        # 有专属 IQ 引用（IQ_SMI_DS_T）
        iq_modules = [getattr(iq, "module", None) for iq in spec.iqs.values()]
        self.assertIn("SMI_DS_T", iq_modules, "缺 IQ_SMI_DS_T（iq_config.yaml）")
        # 现存单元仍 simulation_only=False（schema 向后兼容）
        self.assertFalse(spec.modules["SHIFTUP"].simulation_only)
        self.assertFalse(spec.modules["FP32ADDTREE5TO1"].simulation_only)

    def test_smi_ds_t_microinst_matches_doc_table598(self):
        """hco 建议（doc-契约回归）：固化 v0.4.19 表598 = SMI_DS_T 上行微指令恰 5 字段
        (vld/intlv_en/intlv_grp/intlv_in/intlv_out)、各 1bit、code_bits=5。**专杀 D-01 混淆回归**：
        SMI_DS_T(SMI 中心，表596-598) 与 DS(per-SMC，表585-588) 是两模块，DS 表587 有 inst_id/smc_id_en/
        op_type/t_dim*_cnt——这些**绝不应**串进 SMI_DS_T。"""
        sys.path.append(str(_ROOT / "src"))
        from api.hw_spec import HardwareSpec
        spec = HardwareSpec.load("hw_config")
        m = spec.modules["SMI_DS_T"]
        self.assertEqual(m.code_bits, 5)
        names = [f.name.lower() for f in m.code_fields]
        self.assertEqual(names, ["valid", "intlv_en", "intlv_grp", "intlv_in", "intlv_out"],
                         "SMI_DS_T 微指令须恰为表598 的 5 字段（vld 在 modules.yaml 名 VALID）")
        for f in m.code_fields:
            self.assertEqual(f.bits, 1, f"表598 字段 {f.name} 应 1bit")
        # D-01 防混淆：DS(表587) 专属字段不得出现在 SMI_DS_T
        forbidden = {"inst_id", "smc_id_en", "op_type", "data_type",
                     "t_dim0_cnt", "t_dim1_cnt", "r_in_cnt", "r_out_cnt"}
        self.assertEqual(set(names) & forbidden, set(),
                         "SMI_DS_T 串入了 DS(表587) 字段 → D-01 模块混淆回归")


class TestSmiDsTInstructionDriven(unittest.TestCase):
    """ADR-034 Stage-I 2a：SMI_DS_T 升 DFG 指令节点的「结构」验证
    （DFG 拓扑 + SMT 依赖边 + emit 5bit 窄微指令比特层）。数值由流式 SmiDsTUnit.evaluate/tick 提供
    （ADR-036 S3-c：apply_smi_ds_t backdoor 已删）。"""

    @staticmethod
    def _paths():
        for p in (_ROOT, _ROOT / "src", _ROOT / "sim", _ROOT / "verification_v2"):
            sp = str(p)
            if sp not in sys.path:
                sys.path.append(sp)

    def _spec(self):
        self._paths()
        from api.hw_spec import HardwareSpec
        return HardwareSpec.load("hw_config")

    def _dfg(self):
        self._paths()
        from verification_v2.ops.matmul_op import ManualMatmulQDsTOperator
        spec = self._spec()
        return ManualMatmulQDsTOperator().get_dfg(spec=spec), spec

    def test_smi_ds_t_in_dfg_between_shiftdn_shiftup(self):
        """拓扑：SMI_DS_T 输入边来自 SHIFTDN(gather)，下游经 a_read 到达 broadcast SHIFTUP(scatter)。"""
        ops, _ = self._dfg()
        by_id = {o.trace_id: o for o in ops}
        ds_t = next((o for o in ops if o.op_type.upper() == "SMI_DS_T"), None)
        self.assertIsNotNone(ds_t, "DFG 缺 SMI_DS_T 节点")
        # 输入边来自 SHIFTDN
        deps = ds_t.metadata.get("depends_on", [])
        self.assertTrue(any(by_id[d].op_type.upper() == "SHIFTDN" for d in deps),
                        f"SMI_DS_T 输入边非 SHIFTDN: {deps}")
        # 下游：a_read 依赖 ds_transpose；broadcast SHIFTUP(scatter) 依赖 a_read
        a_read = next(o for o in ops if o.op_type.upper() == "UR_READ"
                      and o.metadata.get("meta_name") == "a")
        self.assertIn(ds_t.trace_id, a_read.metadata.get("depends_on", []),
                      "a_read 应依赖 ds_transpose（scatter-after-transpose）")
        shiftup = next((o for o in ops if o.op_type.upper() == "SHIFTUP"), None)
        self.assertIsNotNone(shiftup, "缺 broadcast SHIFTUP（作 scatter）")
        self.assertIn(a_read.trace_id, shiftup.metadata.get("depends_on", []))

    def test_smt_sees_gather_transpose_scatter_dep(self):
        """SMT 依赖边（专杀 model-A 静默乱序）：t[scatter] > t[transpose] > t[gather]。"""
        self._paths()
        from synthesis.ucode_solver import UCodeSynthesizer
        ops, spec = self._dfg()
        result = UCodeSynthesizer(spec).solve_with_units(ops, MII=1, MAX_II=64)
        self.assertTrue(result, "Synthesis failed（新依赖边破坏 SAT？）")
        sched = result["schedule"]
        gather = next(o for o in ops if o.op_type.upper() == "SHIFTDN")
        transpose = next(o for o in ops if o.op_type.upper() == "SMI_DS_T")
        scatter = next(o for o in ops if o.op_type.upper() == "SHIFTUP")
        self.assertLess(sched[gather.trace_id], sched[transpose.trace_id],
                        "gather(SHIFTDN) 应早于 transpose(SMI_DS_T)")
        self.assertLess(sched[transpose.trace_id], sched[scatter.trace_id],
                        "transpose(SMI_DS_T) 应早于 scatter(SHIFTUP)")

    def test_smi_ds_t_emitted_microinst_bit_layer(self):
        """emit 比特层（D7：5bit 窄微指令端序/对齐，端序路径未必测过）：
        VALID@bit0、VLD header@bit45、总宽 46bit。反证：intlv_en=1 → body bit1 置位。"""
        self._paths()
        from driver.packer import BitPacker
        spec = self._spec()
        payload0 = {"VALID": 1, "intlv_en": 0, "intlv_grp": 0, "intlv_in": 0, "intlv_out": 0}
        cap = BitPacker.pack_capsule(spec, "SMI_DS_T", payload0, is_body=True, VLD=1)
        self.assertEqual(cap & 0b11111, 0b00001, "body 5bit：VALID@bit0 应为 1，其余 0")
        self.assertEqual((cap >> 45) & 1, 1, "header VLD(bit40) 经 body_bits=5 左移到 bit45")
        self.assertEqual(BitPacker.get_unit_bits(spec, "SMI_DS_T"), 46, "总宽 = 41 header + 5 body")
        # 反证：intlv_en 置位 → body bit1
        payload1 = dict(payload0, intlv_en=1)
        cap2 = BitPacker.pack_capsule(spec, "SMI_DS_T", payload1, is_body=True, VLD=1)
        self.assertEqual(cap2 & 0b11111, 0b00011, "intlv_en@bit1 应改变 body")
        self.assertNotEqual(cap, cap2)


class TestSmiDsTInstructionTrigger(unittest.TestCase):
    """ADR-034 Stage-I 2b：转置由 SMI_DS_T 指令的 VLD 触发（cluster 级单实例），
    替 run-前无条件 apply。反证：vld=0 → 不触发 → bank0 未转置（指令驱动可证伪）。"""

    @staticmethod
    def _paths():
        for p in (_ROOT, _ROOT / "src", _ROOT / "sim", _ROOT / "verification_v2"):
            sp = str(p)
            if sp not in sys.path:
                sys.path.append(sp)

    def _fake_cluster(self, spec, num_kernels=8, num_banks=8, bank_size=16):
        import types
        import logging
        from smc_simulator.system.cluster import SystemCluster
        ker = {}
        for i in range(num_kernels):
            ub = types.SimpleNamespace(storage=[[0] * bank_size for _ in range(num_banks)])
            ker[i] = types.SimpleNamespace(ur_bank=ub, hw_spec=spec)
        fc = types.SimpleNamespace(kernels=ker, logger=logging.getLogger("test_2b"),
                                   smi_ds_t_iq=None, smi_ds_t_cfg=None,
                                   smi_ds_t_fired=False, cycle_count=0)
        fc._audit_smi_ds_t = lambda *a, **k: None   # S2-D1b：测试不关心 datapath 日志
        return fc

    def _write_hex(self, spec, vld):
        """单帧 SMI_DS_T 微指令（VALID payload=1，header VLD 由参数控）。"""
        import tempfile
        from driver.packer import BitPacker
        cap = BitPacker.pack_capsule(
            spec, "SMI_DS_T",
            {"VALID": 1, "intlv_en": 0, "intlv_grp": 0, "intlv_in": 0, "intlv_out": 0},
            is_body=True, VLD=vld)
        fh = tempfile.NamedTemporaryFile("w", suffix=".hex", delete=False)
        fh.write(f"{cap:012x}\n")
        fh.close()
        return fh.name

    def test_instruction_driven_vld_gates_transpose(self):
        """VLD=1 → 触发 + bank0 得行对齐转置；VLD=0 → 不触发 + bank0 不变（apply 未被调）。"""
        self._paths()
        from smc_simulator.system.cluster import SystemCluster
        from smc_simulator.units.smi_ds_t_unit import (
            word_from_fp32_lanes, fp32_lanes_from_word,
        )
        spec = self._spec()
        K, M = 4, 4
        X = [[float(m * 10 + k + 1) for k in range(K)] for m in range(M)]
        cfg = {"src_bank": 4, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
               "gather_smc_cnt": K, "lanes": M}

        SENTINEL = 0xDEADBEEFCAFEBABE   # 非零哨兵：apply 若被调会覆写之 → VLD=0 断言非平凡
        for vld, expect_fired in [(1, True), (0, False)]:
            fc = self._fake_cluster(spec)
            for s in range(K):
                col = [X[m][s] for m in range(M)]
                fc.kernels[s].ur_bank.storage[4][0] = word_from_fp32_lanes(col)
            # dst bank0 预置哨兵（非 0），使 VLD=0「未触发」可被「哨兵未被覆写」非平凡证伪
            for m in range(M):
                fc.kernels[0].ur_bank.storage[0][m] = SENTINEL
            hexpath = self._write_hex(spec, vld)
            SystemCluster.setup_smi_ds_t_trigger(fc, hexpath, cfg)
            for c in range(40):   # ADR-036 S2：latency=16 pipeline → scatter 推后；跑到 INST_DONE
                fc.cycle_count = c
                SystemCluster._tick_smi_ds_t_trigger(fc)
                if fc.smi_ds_t_done:
                    break
            self.assertEqual(fc.smi_ds_t_fired, expect_fired,
                             f"VLD={vld} 触发状态应={expect_fired}")
            if expect_fired:
                # apply 被调 → 哨兵被覆写为转置后的行对齐数据
                row0 = fp32_lanes_from_word(fc.kernels[0].ur_bank.storage[0][0], K)
                for k in range(K):
                    self.assertAlmostEqual(row0[k], X[0][k], places=4,
                                           msg=f"VLD=1 转置后行对齐错 @lane{k}")
            else:
                # vld=0：apply 未被调用 → 哨兵原样保留（非平凡：若误调 apply 会被覆写）
                self.assertEqual(fc.kernels[0].ur_bank.storage[0][0], SENTINEL,
                                 "VLD=0 不应触发转置（dst bank0 哨兵应原样保留）")

    def test_smi_ds_t_audit_log_shape(self):
        """ADR-034 S2-D1b（hco 守门）：SMI_DS_T datapath UR 端点事件归属正确——
        gather=UR-READ bank4 / scatter=UR-WRITE bank0，带 Port+SMC_ID，**不冒名 Unit:SMI_DS_T**
        （表596 SMI_DS_T 无 UR 端口，VCS diff 须按真访存单元对齐）。"""
        self._paths()
        import io
        import contextlib
        from smc_simulator.system.cluster import SystemCluster
        from smc_simulator.units.smi_ds_t_unit import word_from_fp32_lanes
        spec = self._spec()
        K, M = 4, 4
        X = [[float(m * 10 + k + 1) for k in range(K)] for m in range(M)]
        cfg = {"src_bank": 4, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
               "gather_smc_cnt": K, "lanes": M}
        fc = self._fake_cluster(spec)
        fc._audit_smi_ds_t = lambda *a: SystemCluster._audit_smi_ds_t(fc, *a)   # 用真 audit
        for s in range(K):
            fc.kernels[s].ur_bank.storage[4][0] = word_from_fp32_lanes([X[m][s] for m in range(M)])
        hexpath = self._write_hex(spec, 1)
        SystemCluster.setup_smi_ds_t_trigger(fc, hexpath, cfg)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for c in range(40):   # ADR-036 S2：latency=16 pipeline → scatter 推后；跑到 INST_DONE
                fc.cycle_count = c
                SystemCluster._tick_smi_ds_t_trigger(fc)
                if fc.smi_ds_t_done:
                    break
        lines = [l for l in buf.getvalue().splitlines() if "[AUDIT-DATA]" in l]
        gather = [l for l in lines if "Tag:DST-SHIFTDN" in l]
        scatter = [l for l in lines if "Tag:DST-SHIFTUP" in l]
        self.assertEqual(len(gather), K, "每 gather SMC 一条 UR-READ")
        self.assertEqual(len(scatter), M, "每转置行一条 UR-WRITE")
        for l in gather:
            self.assertIn("Unit:UR-READ", l)
            self.assertIn("Bank:4", l)
            self.assertIn("Port:", l)
            self.assertIn("SMC_ID:", l)
        for l in scatter:
            self.assertIn("Unit:UR-WRITE", l)
            self.assertIn("Bank:0", l)
            self.assertIn("Port:", l)
        # hco BLOCK：UR 端点绝不冒名 Unit:SMI_DS_T（表596 无 UR 端口）
        self.assertFalse(any("Unit:SMI_DS_T" in l for l in lines),
                         "UR 端点不应冒名 Unit:SMI_DS_T（VCS diff 按真访存单元对齐）")

    def test_latency_single_sourced_from_yaml(self):
        """ADR-036 S2（hco ac50587 守门）：cluster 给 SmiDsTUnit 的 latency **必须取自**
        hw_spec.delays["SMI_DS_T"]（= pipeline_delays.yaml），与 SMT 排程双侧单源耦合——
        不得硬编码。否则 cluster/SMT latency 解耦 → scatter 拍 ≠ a_read 拍 → e2e 静默漂
        （hco 实证解耦 → e2e exit=1）。锁此不变量防未来硬编码。"""
        self._paths()
        from smc_simulator.system.cluster import SystemCluster
        spec = self._spec()
        cfg = {"src_bank": 4, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
               "gather_smc_cnt": 4, "lanes": 4}
        fc = self._fake_cluster(spec)
        SystemCluster.setup_smi_ds_t_trigger(fc, self._write_hex(spec, 1), cfg)
        self.assertEqual(fc.smi_ds_t_unit.latency, int(spec.delays["SMI_DS_T"]),
                         "cluster 的 SMI_DS_T latency 必须 == pipeline_delays.yaml 值（单源，非硬编码）")

    def _spec(self):
        self._paths()
        from api.hw_spec import HardwareSpec
        return HardwareSpec.load("hw_config")


# ADR-036 S3-c：TestSmiDsTStepDriven / TestSmiDsTGatherOrdering / TestSmiDsTClusterApply 的
# gate+apply 测试已删——它们测的 ADR-034 step-driven 接口 + apply_smi_ds_t model-A backdoor + 其
# gather/is_ready/missing/transpose gate 方法均已随 backdoor 删除（无 production 路径）。覆盖迁移：
# 转置数值正确性 / 多词行拆分由 TestSmiDsTTranspose (transpose_tile) + TestSmiDsTS2Streaming
# (evaluate/tick 多词 emit) 覆盖；端到端跨-SMC 数值由 e2e (matmul_q_dst / _k8 / fp16) 覆盖。
# gather-before-scatter ordering gate 随 backdoor 一并退役（流式路径 by-design 无 gate）。


class TestSmiDsTFp16(unittest.TestCase):
    """ADR-035：SMI_DS_T 数据路径 FP16 泛化（8 lane × 16-bit codec + element_bits）。
    含 hco BLOCK-1（byte≡element 不变式）/ BLOCK-2（codec 参数一致）门。"""

    @staticmethod
    def _paths():
        for p in (_ROOT, _ROOT / "sim"):
            sp = str(p)
            if sp not in sys.path:
                sys.path.append(sp)

    def test_fp16_word_codec_roundtrip(self):
        """8 lane × 16-bit 往返一致（含负数 + 跨 byte 错位非对称值）；反证：fp32 codec 解 fp16 词错。"""
        self._paths()
        from smc_simulator.units.smi_ds_t_unit import (
            word_from_fp16_lanes, fp16_lanes_from_word, fp32_lanes_from_word)
        import numpy as np
        # 跨 byte 错位非对称值（half 精度可表示）：相邻 lane 位模式不同，验不串字节
        vals = [np.float16(v) for v in (1.0, -2.5, 3.140625, 100.0, -0.5, 2.0, 7.5, -16.0)]
        w = word_from_fp16_lanes(vals)
        back = fp16_lanes_from_word(w, 8)
        for a, b in zip(vals, back):
            self.assertAlmostEqual(float(a), float(b), places=3)
        # 反证：用 fp32 codec（4 lane × 32-bit）解 fp16 词 → lane 数/值都错
        wrong = fp32_lanes_from_word(w, 4)
        self.assertEqual(len(wrong), 4)                       # fp32 只解 4 lane（≠ 8）
        self.assertNotAlmostEqual(float(wrong[0]), float(vals[0]), places=2)

    def test_smi_ds_t_fp16_no_split_invariant(self):
        """hco BLOCK-1（**sim element-packing 不变式**——非 RTL byte-shuffle，后者 VCS-gated）：
        每 fp16 元素占独立 2-byte 对齐 lane、相邻元素不串字节。**专杀 codec 把元素拆/叠的回归**
        （evaluator caveat：sim 纯 element 粒度，不验 RTL byte 转置；本门只锁 sim 打包正确）。"""
        self._paths()
        from smc_simulator.units.smi_ds_t_unit import word_from_fp16_lanes, fp16_lanes_from_word
        import numpy as np
        vals = [np.float16(s + 1) for s in range(8)]
        w = word_from_fp16_lanes(vals)
        # 8 元素 = 16 byte = 1 个 128b 词（每元素 2-byte 对齐，结构常量记录不变式）
        self.assertEqual(8 * 16, 128)
        # 非平凡：抠掉任一 lane 的 16-bit → 仅该元素清零、其余 7 lane 解码不变（证无重叠/串字节）
        for l in range(8):
            lane = (w >> (l * 16)) & 0xFFFF
            self.assertEqual(struct_half(lane), float(vals[l]))
            w_masked = w & ~(0xFFFF << (l * 16))
            rest = fp16_lanes_from_word(w_masked, 8)
            self.assertEqual(float(rest[l]), 0.0, f"抠 lane{l} 应清零该元素")
            for j in range(8):
                if j != l:
                    self.assertAlmostEqual(float(rest[j]), float(vals[j]), places=3,
                                           msg=f"抠 lane{l} 不应影响 lane{j}（无串字节）")

    def test_smi_ds_t_codec_param_consistency(self):
        """hco BLOCK-2：element_bits 与 word_lanes 矛盾 → 构造器 assert 抛错。"""
        self._paths()
        from smc_simulator.units.smi_ds_t_unit import SmiDsTUnit
        with self.assertRaises(ValueError):
            SmiDsTUnit(smc_num=8, lanes=1, word_lanes=4, element_bits=16)   # 16→须 word_lanes=8
        with self.assertRaises(ValueError):
            SmiDsTUnit(smc_num=4, lanes=4, word_lanes=8, element_bits=32)   # 32→须 word_lanes=4
        # 一致值 OK
        SmiDsTUnit(smc_num=8, lanes=1, word_lanes=8, element_bits=16)
        SmiDsTUnit(smc_num=4, lanes=4, word_lanes=4, element_bits=32)

    def test_smi_ds_t_fp16_transpose(self):
        """element_bits=16：8 SMC × 1 fp16 → 转置 1×8 → DATA_OUT 1 词 8 lane = 行对齐激活。
        ADR-036 S3-c：改走 S2 流式接口 evaluate/tick（load_tile/execute/emit_step 已删）。"""
        self._paths()
        from smc_simulator.units.smi_ds_t_unit import SmiDsTUnit, fp16_lanes_from_word
        import numpy as np
        a = [np.float16((s + 1) * 1.5) for s in range(8)]      # 非对称值
        u = SmiDsTUnit(smc_num=8, lanes=1, word_lanes=8, element_bits=16, latency=0)
        cols = [[a[s]] for s in range(8)]                      # 每 SMC 持 1 fp16 元素
        u.evaluate({"valid": 1, "tile": cols}, cycle=0)
        out = []
        for _ in range(4):
            r = u.tick()
            if r is not None:
                out.append(r)
            if u.inst_done and not u._out_words:
                break
        self.assertEqual(len(out), 1, "FP16 8 元素恰 1 词（vs FP32 2 词）")
        off, word = out[0]
        row = fp16_lanes_from_word(word, 8)
        for s in range(8):
            self.assertAlmostEqual(float(row[s]), float(a[s]), places=3,
                                   msg=f"行对齐 lane{s} 应=a[{s}]")
        self.assertEqual(u.inst_done, 1)


def struct_half(bits16: int) -> float:
    import struct
    return struct.unpack("<e", struct.pack("<H", bits16 & 0xFFFF))[0]


class TestSmiDsTS2Streaming(unittest.TestCase):
    """ADR-036 S2：流式 stepped 接口（evaluate/tick/get_outputs + 固定 latency pipeline，去 gather-gate）。"""

    def _tile(self, K=4, M=4):
        # X[m][s]；列对齐 tile[s] = 列 s = [X[0,s]..X[M-1,s]]
        X = [[float(10 * m + s + 1) for s in range(K)] for m in range(M)]
        tile = [[X[m][s] for m in range(M)] for s in range(K)]
        return X, tile

    def test_streaming_transpose_no_gate(self):
        # evaluate(整 tile) → tick 逐拍出行对齐词；转置数值正确（无 is_ready/raise gate）
        X, tile = self._tile()
        u = SmiDsTUnit(smc_num=4, lanes=4, word_lanes=4, element_bits=32, latency=0)
        u.evaluate({"valid": 1, "tile": tile}, cycle=0)
        out = []
        for _ in range(8):
            r = u.tick()
            if r is not None:
                off, word = r
                out.append((off, [round(x, 1) for x in u._lanes_from_word(word, 4)]))
            if u.inst_done and not u._out_words:
                break
        self.assertEqual(len(out), 4, "4 行各一词")
        for m in range(4):
            self.assertEqual(out[m][0], m)
            self.assertEqual(out[m][1], [round(X[m][s], 1) for s in range(4)],
                             f"行 {m} 转置错")
        self.assertEqual(u.get_outputs()["INST_DONE"], 1)

    def test_fixed_latency_bubble_then_linerate(self):
        # latency=N：触发后 N 拍气泡(DATA_OUT_VLD=0)，再每拍 1 词线速
        _, tile = self._tile()
        N = 16
        u = SmiDsTUnit(smc_num=4, lanes=4, word_lanes=4, element_bits=32, latency=N)
        u.evaluate({"valid": 1, "tile": tile}, cycle=0)
        first_word_tick = None
        for c in range(N + 8):
            r = u.tick()
            if r is not None and first_word_tick is None:
                first_word_tick = c            # 第几次 tick 出第一个词（0-based）
                break
            else:
                self.assertEqual(u.get_outputs()["DATA_OUT_VLD"], 0, f"tick {c} 应是气泡")
        self.assertEqual(first_word_tick, N, f"第一个 DATA_OUT 应在第 {N} 次 tick（0-based）出")

    def test_evaluate_idempotent_until_done(self):
        # 已触发后重复 valid 不重置（幂等）；transpose_tile 纯核可独立验证（既有 test_transpose_numeric）
        _, tile = self._tile()
        u = SmiDsTUnit(smc_num=4, lanes=4, word_lanes=4, element_bits=32, latency=0)
        u.evaluate({"valid": 1, "tile": tile}, cycle=0)
        n0 = len(u._out_words)
        u.evaluate({"valid": 1, "tile": tile}, cycle=1)   # 重复触发应被忽略
        self.assertEqual(len(u._out_words), n0, "已触发后重复 valid 不应重装 FIFO")


class TestSmiDsTS1FiveStage(unittest.TestCase):
    """ADR-036 S1：5 段 DFG 首段 ds_src_read(structural_only) + 「emit 过滤 = audit 过滤」不变量。
    锁死 hco a4f6b70 BLOCK 的回归点：structural_only 节点必须既被 emit 过滤、也被 truth auditor
    过滤，否则 e2e Truth Audit 对没 emit 的节点假阳 → exit=1（假红）。
    （本类测 OFF/structural 路径——S3-c block-1 起默认 ON，故显式置 =0。）"""

    def setUp(self):
        import os
        self._saved = os.environ.get("AIACC_DST_REAL_GATHER")
        os.environ["AIACC_DST_REAL_GATHER"] = "0"   # 本类专测 OFF structural_only 路径

    def tearDown(self):
        import os
        if self._saved is None:
            os.environ.pop("AIACC_DST_REAL_GATHER", None)
        else:
            os.environ["AIACC_DST_REAL_GATHER"] = self._saved

    def _build_dst_dfg(self):
        sys.path.append(str(_ROOT / "src"))
        from api.hw_spec import HardwareSpec
        from verification_v2.ops.matmul_op import ManualMatmulQDsTOperator
        spec = HardwareSpec.load("hw_config")
        return ManualMatmulQDsTOperator().get_dfg(spec=spec)

    def test_five_stage_ds_src_read_structural(self):
        ops = self._build_dst_dfg()
        by_id = {o.trace_id: o for o in ops}
        for nid in ("ds_src_read", "ds_gather", "ds_transpose"):
            self.assertIn(nid, by_id, f"5 段缺节点 {nid}")
        src = by_id["ds_src_read"]
        self.assertEqual(src.op_type.upper(), "UR_READ")
        self.assertTrue(src.metadata.get("structural_only"),
                        "ds_src_read 必须标 structural_only（SMT 锚点、不进 emit/sim）")
        self.assertIn("ds_src_read", by_id["ds_gather"].metadata.get("depends_on", []))
        self.assertIn("ds_gather", by_id["ds_transpose"].metadata.get("depends_on", []))

    def test_emit_filter_excludes_structural_only(self):
        # 镜像 pipeline_runner.py emit 前过滤：structural_only 不进 emit/assemble
        ops = self._build_dst_dfg()
        emit_ids = {o.trace_id for o in ops if not o.metadata.get("structural_only")}
        self.assertNotIn("ds_src_read", emit_ids, "structural_only 节点不该进 emit")
        self.assertIn("ds_gather", emit_ids)
        self.assertIn("ds_transpose", emit_ids)

    def test_auditor_skips_structural_only(self):
        # 不变量：auditor 必须与 emit 同步过滤 structural_only（否则假阳 → e2e exit=1）。
        import inspect
        sys.path.append(str(_ROOT / "src"))
        from verification_v2.infra.auditor import Auditor
        src = inspect.getsource(Auditor.audit_truth_alignment)
        self.assertIn("structural_only", src,
                      "auditor 必须过滤 structural_only（emit 过滤=audit 过滤不变量，hco a4f6b70）")


class TestSmiDsTS3aRealGather(unittest.TestCase):
    """ADR-036 S3-a：env flag AIACC_DST_REAL_GATHER 控制 gather 是否走真固件 UR 读端口
    （替 cluster storage backdoor）。锁死 hco 修正后的不变量：
      - OFF（显式 =0；S3-c block-1 起默认 ON）→ ds_src_read structural_only=True + 硬 pin iq_id（维持 backdoor，S1 行为）
      - ON → ds_src_read 进 emit（去 structural_only）+ **不硬 pin iq_id**（让 solver 分配端口，
        hco BLOCK 提案 A：4 读端口已被 compute 占满，无空闲 ur-r 可静态 pin）+ 标 ds_src_read=True
        （供 pipeline_runner post-solve 回读端口）。"""

    def _build_full_dst_dfg(self):
        sys.path.append(str(_ROOT / "src"))
        sys.path.append(str(_ROOT / "verification_v2"))
        from api.hw_spec import HardwareSpec
        from verification_v2.ops.matmul_op import ManualFp16MatmulFullDsTOperator
        spec = HardwareSpec.load("hw_config")
        return ManualFp16MatmulFullDsTOperator().get_dfg(spec=spec)

    def setUp(self):
        import os
        self._saved = os.environ.get("AIACC_DST_REAL_GATHER")

    def tearDown(self):
        import os
        if self._saved is None:
            os.environ.pop("AIACC_DST_REAL_GATHER", None)
        else:
            os.environ["AIACC_DST_REAL_GATHER"] = self._saved

    def test_off_keeps_structural_only_backdoor(self):
        """OFF（显式 =0；S3-c block-1 起默认已 ON）：ds_src_read structural_only + pin iq_id → 维持 model-A backdoor。"""
        import os
        os.environ["AIACC_DST_REAL_GATHER"] = "0"
        by_id = {o.trace_id: o for o in self._build_full_dst_dfg()}
        src = by_id["ds_src_read"]
        self.assertTrue(src.metadata.get("structural_only"),
                        "OFF 时 ds_src_read 必须 structural_only（不进 emit，走 storage backdoor）")
        self.assertIsNotNone(src.metadata.get("iq_id"), "OFF 时维持硬 pin iq_id")
        self.assertFalse(src.metadata.get("ds_src_read"), "OFF 不打 real-gather 标记")

    def test_on_emits_real_read_no_pin(self):
        """ON：ds_src_read 去 structural_only（进 emit 真 bank 读）+ 不硬 pin iq_id（solver 分配）
        + 标 ds_src_read=True。专杀 hco BLOCK 提案 A 的硬 pin 回归。"""
        import os
        os.environ["AIACC_DST_REAL_GATHER"] = "1"
        by_id = {o.trace_id: o for o in self._build_full_dst_dfg()}
        src = by_id["ds_src_read"]
        self.assertFalse(src.metadata.get("structural_only"),
                         "ON 时 ds_src_read 必须进 emit（去 structural_only → 真固件 bank 读指令）")
        self.assertIsNone(src.metadata.get("iq_id"),
                          "ON 时**不得硬 pin iq_id**（hco BLOCK：4 读端口已满，须让 solver 分配空闲端口）")
        self.assertTrue(src.metadata.get("ds_src_read"),
                        "ON 时须标 ds_src_read=True（供 pipeline_runner post-solve 回读端口）")
        # logical_addr 仍指向 SRC_BANK=6（真 bank6 列对齐读）
        self.assertEqual(src.metadata.get("logical_addr"), 6 * 256,
                         "ds_src_read 应读 SRC_BANK=6（fp16 full_dst 列对齐激活）")


class TestSmiDsTS3aCaptureRegister(unittest.TestCase):
    """ADR-036 S3-a：ds_src 独立捕获寄存器（hco Q3）——同端口异 bank 读不 clobber 列对齐源读快照。
    专杀 DEBT-032 同类失效（广播源被同端口权重读 clobber）：即使 ds_src(bank6) 与广播 a_read(bank0)
    共享 port0（solver 实测分到 port0），按 bank 区分的捕获寄存器各自独立快照、互不覆盖。"""

    @staticmethod
    def _paths():
        for p in (_ROOT, _ROOT / "src", _ROOT / "sim"):
            sp = str(p)
            if sp not in sys.path:
                sys.path.append(sp)

    def _kernel(self):
        self._paths()
        from smc_simulator.core.kernel import ParallelKernel
        return ParallelKernel(smc_id=0)

    def _ur_read_instr(self, spec, bank, addr):
        """真编码一条 UR 读 body 微指令（VALID/BANK/ADDR），驱动 kernel._evaluate_ur 走真解码路径。"""
        from driver.packer import BitPacker
        return BitPacker.pack_capsule(
            spec, "UR_READ", {"VALID": 1, "BANK": bank, "ADDR": addr, "smc_id_en": 7},
            is_body=True, VLD=1)

    def _spec(self):
        self._paths()
        from api.hw_spec import HardwareSpec
        return HardwareSpec.load("hw_config")

    def test_capture_holds_through_same_port_other_bank_read(self):
        """忠实驱动 kernel._evaluate_ur（真解码 UR 读指令）：port0 读 bank6（ds_src）→ 快照；
        随后 port0 读 bank0（广播）→ ds_src 快照**不被 clobber**（按 bank 区分的独立捕获寄存器）。"""
        spec = self._spec()
        k = self._kernel()
        k.set_dst_src_capture(port_idx=0, bank=6)
        k.set_broadcast_capture(port_idx=0, bank=0)   # 共享 port0，验互不干扰
        DST_VAL = 0xABCDEF0123456789
        BCAST_VAL = 0x1111222233334444
        # 拍1：port0 读 bank6（ds_src 列对齐激活）→ _evaluate_ur 内快照进 _dst_src_capture_data
        k.ur_bank.storage[6][0] = DST_VAL
        k.cycle_count = 1
        k._evaluate_ur({"ur-r0": self._ur_read_instr(spec, 6, 0)})
        k.ur_bank.tick(cycle=1)
        self.assertEqual(k._dst_src_capture_data, DST_VAL, "拍1 后 ds_src 快照应=bank6 值")
        # 拍2：port0 读 bank0（广播激活）→ 覆盖 port0 活锁存器，但 ds_src 快照（bank6）应 hold 不变
        k.ur_bank.storage[0][0] = BCAST_VAL
        k.cycle_count = 2
        k._evaluate_ur({"ur-r0": self._ur_read_instr(spec, 0, 0)})
        k.ur_bank.tick(cycle=2)
        self.assertEqual(k._dst_src_capture_data, DST_VAL,
                         "同端口异 bank（bank0 广播）读后，ds_src 快照(bank6)不应被 clobber")
        self.assertEqual(k._bcast_capture_data, BCAST_VAL, "广播快照(bank0)各自独立更新")
        self.assertEqual(k.ur_bank.get_read_data(0), BCAST_VAL,
                         "port0 活锁存器已被 bank0 覆盖（证明若直读端口会 clobber，故需独立捕获寄存器）")


class TestSmiDsTS3bBcastFeed(unittest.TestCase):
    """ADR-036 S3-b（full fan-out，hco a6cc1a2）：SMI_DS_T 转置结果经 _dst_bcast_feed 直喂 SHIFTUP 广播、
    删 bank0 staging。锁死 hco 三条硬约束的可证伪门：NOTE-Q3-A（行索引取自 a_read decode addr）+
    NOTE-Q3-B（dispatch 优先级 _dst_bcast_feed > _bcast_capture_data > live）。"""

    @staticmethod
    def _paths():
        for p in (_ROOT, _ROOT / "src", _ROOT / "sim"):
            sp = str(p)
            if sp not in sys.path:
                sys.path.append(sp)

    def _kernel(self):
        self._paths()
        from smc_simulator.core.kernel import ParallelKernel
        return ParallelKernel(smc_id=0)

    def _spec(self):
        self._paths()
        from api.hw_spec import HardwareSpec
        return HardwareSpec.load("hw_config")

    def _ur_read_instr(self, spec, bank, addr):
        from driver.packer import BitPacker
        return BitPacker.pack_capsule(
            spec, "UR_READ", {"VALID": 1, "BANK": bank, "ADDR": addr, "smc_id_en": 7},
            is_body=True, VLD=1)

    def test_row_index_follows_aread_addr(self):
        """NOTE-Q3-A：_dst_bcast_feed_cur 取自本拍 a_read decode 的 addr（非内部自增计数器）。
        喂非顺序 addr 序列 → 取到的行必须跟随 decode addr，否则乱序/气泡下取错行。"""
        spec = self._spec()
        k = self._kernel()
        k.set_dst_bcast_feed(bank=0)
        # 填 3 行可区分值（行 addr → word）
        k._dst_bcast_feed = {0: 0xAA00, 1: 0xBB11, 2: 0xCC22}
        # 非顺序读 addr=2,0,1 → cur 必须依次 = 行2,行0,行1（跟随 decode addr）
        for addr in (2, 0, 1):
            k.cycle_count = addr
            k._evaluate_ur({"ur-r0": self._ur_read_instr(spec, 0, addr)})
            self.assertEqual(k._dst_bcast_feed_cur, k._dst_bcast_feed[addr],
                             f"读 addr={addr} 应取第 {addr} 行（跟随 decode addr，非计数器）")
        # 反证：同端口异 bank（权重 bank1）读不更新 feed cur（保留 ADR-037 防 clobber 性质）
        k._dst_bcast_feed_cur = 0xDEAD
        k.cycle_count = 9
        k._evaluate_ur({"ur-r0": self._ur_read_instr(spec, 1, 0)})   # bank1 ≠ feed_bank0
        self.assertEqual(k._dst_bcast_feed_cur, 0xDEAD,
                         "同端口异 bank 读不应 clobber feed cur（防 DEBT-032 同款）")

    def test_feed_priority_over_bcast_capture(self):
        """NOTE-Q3-B（最高回归风险）：head 同时配 _dst_bcast_feed 与 _bcast_capture 时，
        SHIFTUP 广播 data 必须取转置行（_dst_bcast_feed_cur），非 bank0 捕获值。
        破坏优先级 → 取 bank0 捕获（已空=0）→ 广播 0 → 退回 DEBT-032 全 0 假象。"""
        from driver.packer import BitPacker
        spec = self._spec()
        k = self._kernel()
        FEED_VAL = 0x1234ABCD
        CAP_VAL = 0x9999          # 若优先级错会取这个
        k.set_dst_bcast_feed(bank=0)
        k._dst_bcast_feed_cur = FEED_VAL
        k.set_broadcast_capture(0, 0)
        k._bcast_capture_data = CAP_VAL
        # 驱动真 SHIFTUP 广播指令（VALID + broadcast），smc_id=0(head) → cru_o = 传入的 data
        cap = BitPacker.pack_capsule(
            spec, "SHIFTUP", {"VALID": 1, "broadcast": 1, "smc_id_en": 7}, is_body=True, VLD=1)
        k._evaluate_units({"shiftup": cap})
        k.units["shiftup"].tick()
        self.assertEqual(k.units["shiftup"].cru_o, FEED_VAL,
                         "SHIFTUP 广播 data 应取 _dst_bcast_feed_cur（转置行），非 bank0 捕获值")
        self.assertNotEqual(k.units["shiftup"].cru_o, CAP_VAL,
                            "优先级错→取 bank0 捕获→DEBT-032 全 0 回归")


if __name__ == "__main__":
    unittest.main()
