"""路径 A 逐目标时序裁决器 pathA_target_ordering 的红绿契约测试（2026-10-14）。

判据口径（设计稿 docs/8smc_pathA_full_target_monitoring_20261014.md §2，用户
2026-10-14 验收点③⑤）：覆盖**全部活动目标**——每个 SMC 的 COMPUTE 首读晚于
自己的 LOAD 末写、STORE 首读晚于自己的计算末写、dump 晚于全部目标写入与所需
响应完成；期望目标缺记录必须 RED（不得静默通过）。

本文件的合成日志逐字沿用 TB 探针的实写格式（列数/进制），因此也能当格式回归：
若 TB 侧格式漂移而裁决器未同步，这里会先红。
"""
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "verification_v2" / "tools"))

import pathA_target_ordering as pao  # noqa: E402

N = 16
SMCS = 8

SIM_LOG = """\
[TB_MULTI_STAGE] ===== stage_0/2 BEGIN ===== @time 100 @probe_cycle 50
[TB_MULTI_STAGE] stage_0 done (intr cleared) @time 200 @probe_cycle 150
[TB_MULTI_STAGE] ===== stage_1/2 BEGIN ===== @time 210 @probe_cycle 160
[TB_MULTI_STAGE] stage_1 done (intr cleared) @time 300 @probe_cycle 250
[TB_MULTI_STAGE] ===== stage_2/2 BEGIN ===== @time 310 @probe_cycle 260
[TB_MULTI_STAGE] stage_2 done (intr cleared) @time 340 @probe_cycle 290
[TB_MULTI_STAGE] all stages complete, preparing UR/HBM dump @time 400 @probe_cycle 400
[GATE4][DUMP] begin @probe_cycle 500 (gated by S4BARRIER)
"""


def _write_ur_wr(path: Path, load_off=60, comp_off=170, smc_skip=()):
    rows = ["# cycle smc cls bank addr wbe wdata"]
    for k in range(SMCS):
        if k in smc_skip:
            continue
        rows.append(f"{load_off + 2 * k} {k} ura 2 10 000f "
                    f"{'0' * 31}1")
        rows.append(f"{comp_off + 2 * k} {k} cru 3 21 0001 "
                    f"{'0' * 31}2")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_ur_rd(path: Path, off=165, smc_skip=(), per_smc=None):
    rows = ["# cycle smc port bank addr"]
    for k in range(SMCS):
        if k in smc_skip:
            continue
        cyc = off + 2 * k if per_smc is None else per_smc[k]
        rows.append(f"{cyc} {k} 0 2 10")
        rows.append(f"{cyc + 1} {k} 1 2 11")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_stb_rd(path: Path, off=270, smc_skip=(), per_smc=None):
    rows = ["# cycle smc addr"]
    for k in range(SMCS):
        if k in smc_skip:
            continue
        cyc = off + 2 * k if per_smc is None else per_smc[k]
        rows.append(f"{cyc} {k} 01a4")
        rows.append(f"{cyc + 1} {k} 01a5")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_hbm(path: Path, off=300, smc_skip=(), per_smc=None):
    rows = ["# cycle smc addra wea dina"]
    for k in range(SMCS):
        if k in smc_skip:
            continue
        cyc = off + 3 * k if per_smc is None else per_smc[k]
        rows.append(f"{cyc} {k} 0{(0x5000 >> 4) + k:04x} 0001 "
                    f"{'0' * 31}3")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_bresp(path: Path, off=320, smc_skip=()):
    rows = ["# cycle ch"]
    for k in range(SMCS):
        if k in smc_skip:
            continue
        rows.append(f"{off + 3 * k} {k}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_crd(path: Path, base=343, smc_skip=()):
    rows = ["# cycle sig pos vld done inst err"]
    for k in range(SMCS):
        if k in smc_skip:
            continue
        for pos in range(0, k + 1):
            rows.append(f"{base + k + pos} ldb {pos} 1 1 {k} 0")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


WRITERS = {
    "pa_ur_wr_trace": _write_ur_wr,
    "pa_ur_rd_trace": _write_ur_rd,
    "pa_stb_rd_trace": _write_stb_rd,
    "hbm_write_trace": _write_hbm,
    "pa_bresp_trace": _write_bresp,
    "pa_crd_trace": _write_crd,
}


class PathATargetOrderingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ev = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, sim_log=SIM_LOG, **kwargs):
        """kwargs 键 = 探针日志基名（如 pa_ur_rd_trace），值 = 该写手的构造参数。"""
        (self.ev / f"n{N}_sim_log.log").write_text(sim_log, encoding="utf-8")
        for key, fn in WRITERS.items():
            fn(self.ev / f"n{N}_{key}.log", **kwargs.get(key, {}))

    def analyse(self):
        return pao.analyse(self.ev, N, SMCS, 0, 1, 2)

    # ---------------------------------------------------------------- GREEN
    def test_green_all_three_conditions(self):
        self.build()
        rep = self.analyse()
        self.assertEqual(rep["violations"], [])
        self.assertEqual(rep["verdict"], "GREEN")
        self.assertTrue(rep["dump_after_all_targets"])
        self.assertEqual(rep["observed_smcs"], list(range(SMCS)))
        t = rep["per_target"]["smc_0"]
        self.assertEqual(t["last_load_write"], 60)
        self.assertEqual(t["first_compute_read"], 165)
        self.assertEqual(t["last_compute_write"], 170)
        self.assertEqual(t["first_store_read"], 270)
        self.assertEqual(t["hbm_writes"], 1)
        self.assertEqual(t["bresp"], 1)
        self.assertEqual(t["compute_reads"], 2)

    def test_green_records_credit_positions(self):
        """差分延迟签名原料：逐位置脉冲计数/首末拍必须落在报告里。"""
        self.build()
        rep = self.analyse()
        ldb = rep["credit_positions"]["ldb"]
        self.assertEqual(ldb["0"]["count"], SMCS)     # 聚合上报点
        self.assertEqual(ldb["7"]["count"], 1)        # 只有 SMC7 使 pos7 脉冲
        self.assertEqual(ldb["0"]["first"], 343)
        for pos, last in (("0", 350), ("7", 357)):
            self.assertEqual(ldb[pos]["last"], last)

    # ------------------------------------------------------------------ RED
    def test_red_when_compute_read_before_own_load_write(self):
        """C1：某目标 COMPUTE 首读早于自己的 LOAD 末写 ⇒ 只该目标 RED。"""
        self.build(pa_ur_rd_trace={"per_smc": {**{k: 165 + 2 * k
                                                  for k in range(SMCS)},
                                               3: 58}})
        rep = self.analyse()
        self.assertEqual(rep["verdict"], "RED")
        hits = [v for v in rep["violations"] if v.startswith("(C1/smc_3)")]
        self.assertEqual(len(hits), 1, rep["violations"])
        self.assertFalse([v for v in rep["violations"]
                          if v.startswith(("(C1/smc_0)", "(C1/smc_1)",
                                           "(C1/smc_2)", "(C1/smc_4)"))])

    def test_red_when_store_read_before_own_compute_write(self):
        """C2：STORE 首读早于自己的计算末写 ⇒ RED。"""
        self.build(pa_stb_rd_trace={"per_smc": {**{k: 270 + 2 * k
                                                   for k in range(SMCS)},
                                                5: 168}})
        rep = self.analyse()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue([v for v in rep["violations"]
                         if v.startswith("(C2/smc_5)")], rep["violations"])

    def test_red_when_target_has_zero_events(self):
        """用户验收点⑤：期望目标零记录必须 RED（缺记录不得静默通过）。"""
        self.build(pa_ur_wr_trace={"smc_skip": (7,)},
                   pa_ur_rd_trace={"smc_skip": (7,)},
                   pa_stb_rd_trace={"smc_skip": (7,)},
                   hbm_write_trace={"smc_skip": (7,)},
                   pa_bresp_trace={"smc_skip": (7,)},
                   pa_crd_trace={"smc_skip": (7,)})
        rep = self.analyse()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue([v for v in rep["violations"]
                         if v.startswith("(TARGET-MISSING/smc_7)")],
                        rep["violations"])
        self.assertEqual(rep["observed_smcs"], list(range(7)))

    def test_red_when_bresp_missing_for_one_channel(self):
        """C3：逐通道写响应未达（外部写完成未见）⇒ RED。"""
        self.build(pa_bresp_trace={"smc_skip": (2,)})
        rep = self.analyse()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue([v for v in rep["violations"]
                         if v.startswith("(C3/smc_2)")
                         and "写响应" in v], rep["violations"])

    def test_red_when_dump_early(self):
        """C3：dump 早于末次目标写入/响应 ⇒ RED（最终 dump 必须晚于全部）。"""
        self.build(sim_log=SIM_LOG.replace("@probe_cycle 500",
                                           "@probe_cycle 310"))
        rep = self.analyse()
        self.assertEqual(rep["verdict"], "RED")
        self.assertFalse(rep["dump_after_all_targets"])
        self.assertTrue([v for v in rep["violations"]
                         if v.startswith("(C3/dump)")], rep["violations"])

    def test_red_when_hbm_write_missing(self):
        self.build(hbm_write_trace={"smc_skip": (4,)})
        rep = self.analyse()
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue([v for v in rep["violations"]
                         if v.startswith("(C3/smc_4)")], rep["violations"])

    # ------------------------------------------------------------- 输入缺失
    def test_missing_sim_log_is_exit3(self):
        rc = pao.main(["--evidence", str(self.ev), "--n", str(N)])
        self.assertEqual(rc, 3)

    def test_missing_probe_log_is_not_silent_green(self):
        """探针日志整份缺席 ⇒ 全部目标缺记录 ⇒ RED（绝不静默通过）。"""
        (self.ev / f"n{N}_sim_log.log").write_text(SIM_LOG, encoding="utf-8")
        rep = self.analyse()
        self.assertEqual(rep["verdict"], "RED")
        self.assertEqual(len([v for v in rep["violations"]
                              if v.startswith("(TARGET-MISSING/")]), SMCS)

    # ------------------------------------------------------------- CLI 契约
    def test_main_exit_codes(self):
        self.build()
        out = self.ev / "order.json"
        rc = pao.main(["--evidence", str(self.ev), "--n", str(N),
                       "--json", str(out)])
        self.assertEqual(rc, 0)
        self.assertIn('"verdict": "GREEN"',
                      out.read_text(encoding="utf-8"))
        self.build(pa_bresp_trace={"smc_skip": (1,)})
        self.assertEqual(pao.main(["--evidence", str(self.ev), "--n", str(N)]),
                         2)


if __name__ == "__main__":
    unittest.main()
