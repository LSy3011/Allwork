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


class TestSmiDsTGatherOrdering(unittest.TestCase):
    def test_transpose_before_gather_raises(self):
        """门 5（unit 级）：gather 未到齐就 transpose → 抛 RuntimeError（gather-before-scatter）。"""
        u = SmiDsTUnit(smc_num=8, lanes=4)
        u.gather(0, [1, 2, 3, 4])
        u.gather(1, [5, 6, 7, 8])          # 只 gather 了 2/8
        self.assertFalse(u.is_ready())
        self.assertEqual(set(u.missing()), set(range(2, 8)))
        with self.assertRaises(RuntimeError):
            u.transpose()

    def test_is_cross_smc_all_smc_gathered(self):
        """门 2（unit 级）：输入来自所有 SMC（非单 SMC），全到齐才能转置出正确行对齐数据。"""
        smc_num, lanes = 8, 4
        u = SmiDsTUnit(smc_num=smc_num, lanes=lanes)
        # 每个 SMC 贡献 lanes 个元素：SMC_s 持 [X[0,s], X[1,s], ..., X[lanes-1,s]]（列对齐存）
        X = [[m * 1000 + s for s in range(smc_num)] for m in range(lanes)]  # 逻辑激活 [lanes 行 × smc_num 列]
        for s in range(smc_num):
            u.gather(s, [X[m][s] for m in range(lanes)])   # SMC_s 的 UR 行 = 列 s
        self.assertTrue(u.is_ready())
        out = u.transpose()
        # 转置后第 m 行 = 完整激活行 X[m,:]（跨所有 SMC 汇聚）
        for m in range(lanes):
            self.assertEqual(out[m], X[m], f"row {m} not recovered from cross-SMC gather")

    def test_gather_validates_row_width(self):
        """gather 行宽必须 = lanes，防错配。"""
        u = SmiDsTUnit(smc_num=8, lanes=4)
        with self.assertRaises(ValueError):
            u.gather(0, [1, 2, 3])         # 宽度 3 != lanes 4
        with self.assertRaises(ValueError):
            u.gather(99, [1, 2, 3, 4])     # smc_id 越界


class TestSmiDsTClusterApply(unittest.TestCase):
    """门 2/3/5（cluster 级）：cluster.apply_smi_ds_t 从多 SMC bank gather 列对齐激活 →
    SMI_DS_T 转置 → 行对齐写回 SMC_0 bank0。验证跨-SMC datapath 的数值正确性。"""

    @staticmethod
    def _fake_cluster(num_kernels, num_banks=8, bank_size=16):
        import types
        import logging
        ker = {}
        for i in range(num_kernels):
            ub = types.SimpleNamespace(storage=[[0] * bank_size for _ in range(num_banks)])
            ker[i] = types.SimpleNamespace(ur_bank=ub)
        return types.SimpleNamespace(kernels=ker, logger=logging.getLogger("test_smi_ds_t"))

    def test_gather_transpose_scatter(self):
        """门 2+3：列对齐激活存 K 个 SMC → apply_smi_ds_t → SMC_0 bank0 得行对齐 X[m,:]。"""
        from smc_simulator.system.cluster import SystemCluster
        from smc_simulator.units.smi_ds_t_unit import word_from_fp32_lanes, fp32_lanes_from_word

        K, M = 4, 4
        # 逻辑激活 X[M,K]，元素唯一可区分
        X = [[float(m * 10 + k + 1) for k in range(K)] for m in range(M)]
        fake = self._fake_cluster(num_kernels=8)   # 8 SMC，仅前 K 个持激活列
        # 列对齐存（非转置间插）：SMC_s bank4 持列 s = [X[0,s]..X[M-1,s]]
        for s in range(K):
            col = [X[m][s] for m in range(M)]
            fake.kernels[s].ur_bank.storage[4][0] = word_from_fp32_lanes(col)
        cfg = {"src_bank": 4, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
               "gather_smc_cnt": K, "lanes": M}
        SystemCluster.apply_smi_ds_t(fake, cfg)
        # SMC_0 bank0 addr m 应 = 行对齐 X[m,:]（跨所有 K 个 SMC 汇聚转置恢复）
        for m in range(M):
            row = fp32_lanes_from_word(fake.kernels[0].ur_bank.storage[0][m], K)
            for k in range(K):
                self.assertAlmostEqual(row[k], X[m][k], places=4,
                                       msg=f"row {m} lane {k} not recovered")
        # 反证：源 SMC_3（持列 3）若全 0，则恢复的每行最后一元素错 → 证明真用了跨-SMC 源
        fake2 = self._fake_cluster(num_kernels=8)
        for s in range(K - 1):   # 故意漏掉 SMC_3
            col = [X[m][s] for m in range(M)]
            fake2.kernels[s].ur_bank.storage[4][0] = word_from_fp32_lanes(col)
        SystemCluster.apply_smi_ds_t(fake2, cfg)
        row0 = fp32_lanes_from_word(fake2.kernels[0].ur_bank.storage[0][0], K)
        self.assertNotAlmostEqual(row0[K - 1], X[0][K - 1], places=4)  # 列 3 缺 → 错
        self.assertAlmostEqual(row0[K - 1], 0.0, places=4)             # 缺的列 = 0

    def test_k8_multiword_gather_transpose_scatter(self):
        """Stage-A'：K=8 全 gather（8 SMC × lanes=1）→ 转置 1×8 → 写回 SMC_0 bank0 2 词。
        验证 apply_smi_ds_t 多词行（K=8 > word_lanes=4）正确拆词。"""
        from smc_simulator.system.cluster import SystemCluster
        from smc_simulator.units.smi_ds_t_unit import word_from_fp32_lanes, fp32_lanes_from_word

        K = 8
        a = [float(s + 1) * 1.5 for s in range(K)]   # 8 元素激活向量
        fake = self._fake_cluster(num_kernels=8)
        # 列对齐：SMC_s bank4 持 a[s]（lanes=1，词 = [a[s],0,0,0]）
        for s in range(K):
            fake.kernels[s].ur_bank.storage[4][0] = word_from_fp32_lanes([a[s]])
        cfg = {"src_bank": 4, "src_addr": 0, "dst_bank": 0, "dst_addr": 0,
               "gather_smc_cnt": K, "lanes": 1, "word_lanes": 4}
        SystemCluster.apply_smi_ds_t(fake, cfg)
        # SMC_0 bank0 addr0 = a[0:4]，addr1 = a[4:8]（行对齐 8 元素拆 2 词）
        w0 = fp32_lanes_from_word(fake.kernels[0].ur_bank.storage[0][0], 4)
        w1 = fp32_lanes_from_word(fake.kernels[0].ur_bank.storage[0][1], 4)
        for k in range(4):
            self.assertAlmostEqual(w0[k], a[k], places=4)
            self.assertAlmostEqual(w1[k], a[4 + k], places=4)

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


class TestSmiDsTInstructionDriven(unittest.TestCase):
    """ADR-034 Stage-I 2a：SMI_DS_T 升 DFG 指令节点的「结构」验证
    （DFG 拓扑 + SMT 依赖边 + emit 5bit 窄微指令比特层）。数值仍由 apply_smi_ds_t（model-A）提供。"""

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
        fc.apply_smi_ds_t = lambda cfg: SystemCluster.apply_smi_ds_t(fc, cfg)
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
            for c in range(6):
                fc.cycle_count = c
                SystemCluster._tick_smi_ds_t_trigger(fc)
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

    def _spec(self):
        self._paths()
        from api.hw_spec import HardwareSpec
        return HardwareSpec.load("hw_config")


if __name__ == "__main__":
    unittest.main()
