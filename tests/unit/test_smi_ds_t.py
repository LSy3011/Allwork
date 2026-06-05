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

    def test_word_pack_roundtrip(self):
        """pack/unpack fp32 lane 往返一致（与 emitter/_read_sim_output 约定一致）。"""
        from smc_simulator.units.smi_ds_t_unit import word_from_fp32_lanes, fp32_lanes_from_word
        vals = [1.0, -2.5, 3.14159, 100.0]
        w = word_from_fp32_lanes(vals)
        back = fp32_lanes_from_word(w, 4)
        for a, b in zip(vals, back):
            self.assertAlmostEqual(a, b, places=4)


class TestSmiDsTModuleSpec(unittest.TestCase):
    """hco note 3：守门——SMI_DS_T 是 simulation_only 且无任何 IQ 引用（防后人误配 IQ 拖进调度）。"""

    def test_smi_ds_t_is_simulation_only_no_iq(self):
        sys.path.append(str(_ROOT / "src"))
        from api.hw_spec import HardwareSpec
        spec = HardwareSpec.load("hw_config")
        self.assertIn("SMI_DS_T", spec.modules)
        m = spec.modules["SMI_DS_T"]
        self.assertTrue(m.simulation_only, "SMI_DS_T 必须 simulation_only=True（不进 SMT 调度）")
        self.assertEqual(m.code_bits, 5)
        # 无任何 IQ 引用 SMI_DS_T（否则会被拖进可调度单元 → 死 module 反模式）
        for iq_name, iq in spec.iqs.items():
            self.assertNotEqual(getattr(iq, "module", None), "SMI_DS_T",
                                f"IQ {iq_name} 不得引用 simulation_only 的 SMI_DS_T")
        # 现存 18 个 compute/datapath module 仍 simulation_only=False（schema 向后兼容）
        self.assertFalse(spec.modules["SHIFTUP"].simulation_only)
        self.assertFalse(spec.modules["FP32ADDTREE5TO1"].simulation_only)


if __name__ == "__main__":
    unittest.main()
