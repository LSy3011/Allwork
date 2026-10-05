"""ADR-057 D4 多段仲裁器（arbitrate_multi）红绿契约测试。

合成两段用例镜像验收①侦察形态：TB 标记切窗 [BEGIN_k, BEGIN_{k+1})，每段
复用单段核心（c0 逐段重锚——两段 c0 刻意不同），段间 C1（次序/应答窗）/
C2（静默窗无残留写，空窗不称排干证明）/C3（写集相等 + 不相交 + 链式连续）
逐条对抗：每类破坏必须单独立刻 RED。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "verification_v2" / "tools"))

import arbitrate_probe_logs as arb                              # noqa: E402

IQ_R0, IQ_W0 = 16, 20
# 段窗：stage0 [1000,5000) ack0=4900；stage1 [5000,∞) ack1=8900；complete@9000
B0, ACK0, B1, ACK1, DONE = 1000, 4900, 5000, 8900, 9000
C0_S0, C0_S1 = 1100, 5100          # 两段启动锚刻意不同（逐段重锚语义）


def stage_expected(stage_id: int, start: int, m: int, addrs) -> dict:
    """逐段期望（adr057 schema + 段身份块）。addrs = 本段 ur-w0 期望写地址。"""
    ev_r0 = ([{"cycle": c, "iq": "ur-r0", "kind": "FETCH",
               "payload": {"vld": 1 if c else 0}} for c in (0, 3, 4, 5, 6)]
             + [{"cycle": c, "iq": "ur-r0", "kind": "ISSUE",
                 "payload": {"vld": 0}} for c in range(6)])
    w_fetch = [{"cycle": 0, "iq": "ur-w0", "kind": "FETCH", "payload": {"vld": 0}}]
    for j, a in enumerate(addrs):
        w_fetch.append({"cycle": 5 + j, "iq": "ur-w0", "kind": "FETCH",
                        "payload": {"vld": 1, "BANK": 2, "ADDR": a}})
    w_fetch.append({"cycle": 5 + len(addrs), "iq": "ur-w0", "kind": "FETCH",
                    "payload": {"vld": 0}})
    ev_w0 = w_fetch + [{"cycle": c, "iq": "ur-w0", "kind": "ISSUE",
                        "payload": {"vld": 0}} for c in range(5 + len(addrs))]
    return {"schema": arb.STAGE_SCHEMA,
            "registry": {"records_sha256": "synthetic-test"},
            "stage": {"stage_id": stage_id, "stage_start": start, "m": m,
                      "iter_range": [start, start + m], "kind": "COMPUTE"},
            "union_queues": ["ur-r0", "ur-w0"],
            "queues": {"ur-r0": {"events": ev_r0}, "ur-w0": {"events": ev_w0}}}


def write_green_case(root: Path, n_w1: int = 1) -> None:
    """两段重放三日志 + sim.log 标记。n_w1 = stage1 写地址数（默认 1 个 @12）。"""
    fetch = ["# cycle iq addr_valid addr data_valid data"]
    for c0, n_w in ((C0_S0, 2), (C0_S1, n_w1)):
        for k, rel in enumerate((0, 3, 4, 5, 6)):
            fetch.append(f"{c0 + rel} {IQ_R0} 1 {k} 0 0")
        w_rels = (0,) + tuple(range(5, 5 + n_w)) + (5 + n_w,)
        for k, rel in enumerate(w_rels):
            fetch.append(f"{c0 + rel} {IQ_W0} 1 {k} 0 0")
    root.joinpath("iq_probe_trace.log").write_text("\n".join(fetch) + "\n")

    issue = ["# cycle iq dly_data_valid dly_data"]
    for c0, n_w in ((C0_S0, 2), (C0_S1, n_w1)):
        for rel in range(6):
            issue.append(f"{c0 + rel} {IQ_R0} 1 00aa")
        for rel in range(5 + n_w):
            issue.append(f"{c0 + rel} {IQ_W0} 1 00bb")
    root.joinpath("iq_issue_trace.log").write_text("\n".join(issue) + "\n")

    wdata = "ab" * 16
    write = ["# cycle smc bank we addr wbe wdata",
             f"3000 0 2 1 10 ffff {wdata}",
             f"3001 0 2 1 11 ffff {wdata}"]
    write += [f"{7000 + j} 0 2 1 {12 + j} ffff {wdata}" for j in range(n_w1)]
    root.joinpath("ur_write_trace.log").write_text("\n".join(write) + "\n")

    sim = "\n".join([
        "[TB_MULTI_STAGE] ===== stage_0/1 BEGIN ===== @time 10000 @probe_cycle 1000",
        "[TB_MULTI_STAGE] stage_0 done (intr cleared) @time 49000 @probe_cycle 4900",
        "[TB_MULTI_STAGE] ===== stage_1/1 BEGIN ===== @time 50000 @probe_cycle 5000",
        "[TB_MULTI_STAGE] stage_1 done (intr cleared) @time 89000 @probe_cycle 8900",
        "[TB_MULTI_STAGE] all stages complete, preparing UR/HBM dump "
        "@time 90000 @probe_cycle 9000",
    ])
    root.joinpath("sim.log").write_text(sim + "\n")


class ArbitrateMultiStageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_green_case(self.root)
        self.exp = [stage_expected(0, 0, 2, [10, 11]),
                    stage_expected(1, 2, 1, [12])]
        self.sim_log = self.root / "sim.log"

    def tearDown(self):
        self.tmp.cleanup()

    def _report(self, exp=None):
        return arb.arbitrate_multi(self.root, exp or self.exp, self.sim_log)

    def _red_with(self, tag: str, exp=None) -> dict:
        rep = self._report(exp)
        self.assertEqual(rep["verdict"], "RED",
                         f"对抗注入未检出（期望含 {tag}）: {rep['violations']}")
        self.assertTrue(any(tag in v for v in rep["violations"]),
                        f"违例里未见 {tag}: {rep['violations']}")
        return rep

    # ---------- 绿基线 ----------

    def test_green_two_stage(self):
        rep = self._report()
        self.assertEqual(rep["verdict"], "GREEN", rep["violations"])
        self.assertEqual(rep["limitations"], [])
        self.assertEqual(rep["stage_count"], 2)
        # 逐段 c0 重锚：两段各自锚在自己的首个 fetch
        self.assertEqual(rep["stages"][0]["c0_by_queue"]["ur-r0"]["c0"], C0_S0)
        self.assertEqual(rep["stages"][1]["c0_by_queue"]["ur-r0"]["c0"], C0_S1)
        self.assertEqual(rep["markers"]["begins"], {0: B0, 1: B1})

    # ---------- C1 次序/应答窗 ----------

    def test_c1_ack_after_next_begin_is_red(self):
        sim = self.sim_log.read_text()
        self.sim_log.write_text(sim.replace(
            "stage_1/1 BEGIN ===== @time 50000 @probe_cycle 5000",
            "stage_1/1 BEGIN ===== @time 48000 @probe_cycle 4800"))
        self._red_with("(C1)")

    def test_c1_write_after_ack_is_red(self):
        with self.root.joinpath("ur_write_trace.log").open("a") as f:
            f.write(f"4899 0 2 1 11 ffff {'ab' * 16}\n")
        # 4899 < ack0=4900 但在窗口内、属合法写集——G4 不抓；此处把末笔写挪到
        # 应答前一刻仍合法。真正测「写晚于应答」：把应答提前到 2900。
        sim = self.sim_log.read_text()
        self.sim_log.write_text(sim.replace(
            "stage_0 done (intr cleared) @time 49000 @probe_cycle 4900",
            "stage_0 done (intr cleared) @time 29000 @probe_cycle 2900"))
        rep = self._red_with("(C1)")
        self.assertTrue(any("写回未完成即应答" in v for v in rep["violations"]))

    # ---------- C2 静默窗 ----------

    def test_c2_residual_write_in_gap_is_red(self):
        # gap [4900,5000) 内一笔写（地址属 stage0 写集 → 段内 G4 不抓，唯 C2 抓）
        with self.root.joinpath("ur_write_trace.log").open("a") as f:
            f.write(f"4950 0 2 1 10 ffff {'ab' * 16}\n")
        self._red_with("(C2)")

    def test_c2_write_before_first_begin_is_red(self):
        with self.root.joinpath("ur_write_trace.log").open("a") as f:
            f.write(f"900 0 2 1 10 ffff {'ab' * 16}\n")
        self._red_with("(C2)")

    def test_c2_write_after_last_ack_is_red(self):
        """末段 ack 后的写 → C2 RED（evaluator N1：此前唯一无测试的 C2 分支）。
        故意用 stage1 期望集内的地址（12）——集合语义不可见的那类残留，
        证明 C2 尾向检查地址无关、全捕获。"""
        with self.root.joinpath("ur_write_trace.log").open("a") as f:
            f.write(f"8950 0 2 1 12 ffff {'ab' * 16}\n")
        rep = self._red_with("(C2)")
        self.assertTrue(any("残留写" in v for v in rep["violations"]))

    def test_c2_vacuous_window_is_limitation_not_violation(self):
        """应答≡下段 BEGIN 同拍 → 空窗记 limitations、不单独致 RED
        （ADR-057 复核注记 + errata-2026-10-01：TB 零延迟推进的构造性空窗；
        残留写排除由 C1+C3 正向支撑）。限制声明必须在场——不许静默放过。"""
        sim = self.sim_log.read_text()
        self.sim_log.write_text(sim.replace(
            "stage_0 done (intr cleared) @time 49000 @probe_cycle 4900",
            "stage_0 done (intr cleared) @time 50000 @probe_cycle 5000"))
        rep = self._report()
        self.assertEqual(rep["verdict"], "GREEN", rep["violations"])
        self.assertEqual(rep["violations"], [])
        self.assertEqual(len(rep["limitations"]), 1, rep)
        self.assertIn("空窗不称排干证明", rep["limitations"][0])
        gaps = rep["inter_stage"]["C2_no_residual_write"]["gaps"]
        self.assertTrue(gaps[0].get("vacuous_window"))

    def test_c2_vacuous_window_does_not_mask_real_violation(self):
        """空窗降级为限制声明后，真实违例仍须 RED 且限制声明同场记录。"""
        sim = self.sim_log.read_text()
        self.sim_log.write_text(sim.replace(
            "stage_0 done (intr cleared) @time 49000 @probe_cycle 4900",
            "stage_0 done (intr cleared) @time 50000 @probe_cycle 5000"))
        # stage1 窗口内多一笔属于 stage0 写集的写 → C3 集合不相等
        with self.root.joinpath("ur_write_trace.log").open("a") as f:
            f.write(f"7001 0 2 1 10 ffff {'ab' * 16}\n")
        rep = self._red_with("(C3)")
        self.assertEqual(len(rep["limitations"]), 1, rep)
        self.assertIn("空窗不称排干证明", rep["limitations"][0])

    # ---------- C3 地址接续 ----------

    def test_c3_missing_stage1_write_is_red(self):
        lines = self.root.joinpath("ur_write_trace.log") \
            .read_text().splitlines()
        self.root.joinpath("ur_write_trace.log").write_text(
            "\n".join(ln for ln in lines if not ln.startswith("7000 ")) + "\n")
        self._red_with("(C3)")

    def test_c3_overlapping_write_sets_is_red(self):
        """段间写集相交 → 归属不可判定（即使逐段 G4 各自合法）。"""
        exp = [stage_expected(0, 0, 2, [10, 11]),
               stage_expected(1, 2, 1, [11])]      # 与 stage0 相交于 addr 11
        lines = self.root.joinpath("ur_write_trace.log") \
            .read_text().splitlines()
        lines = [ln.replace(" 12 ", " 11 ") if ln.startswith("7000 ") else ln
                 for ln in lines]
        self.root.joinpath("ur_write_trace.log").write_text("\n".join(lines) + "\n")
        self._red_with("相交", exp=exp)

    def test_c3_continuity_break_is_red(self):
        exp = [stage_expected(0, 0, 2, [10, 11]),
               stage_expected(1, 2, 1, [13])]      # 跳过 12：链断裂
        lines = self.root.joinpath("ur_write_trace.log") \
            .read_text().splitlines()
        lines = [ln.replace(" 12 ", " 13 ") if ln.startswith("7000 ") else ln
                 for ln in lines]
        self.root.joinpath("ur_write_trace.log").write_text("\n".join(lines) + "\n")
        self._red_with("接续断裂", exp=exp)

    # ---------- 标记/schema/覆盖 ----------

    def test_missing_begin_marker_is_red(self):
        sim = self.sim_log.read_text()
        self.sim_log.write_text("\n".join(
            ln for ln in sim.splitlines() if "stage_1/1 BEGIN" not in ln) + "\n")
        self._red_with("TB 段标记不齐")

    def test_wrong_schema_is_red(self):
        exp = [dict(self.exp[0]), dict(self.exp[1])]
        exp[0]["schema"] = "adr056_d4_expected_events_v1"
        self._red_with("schema", exp=exp)

    def test_coverage_gap_is_red(self):
        exp = [stage_expected(0, 0, 2, [10, 11]),
               stage_expected(1, 3, 1, [12])]      # 覆盖链 [0,2)+[3,4) 漏 2
        self._red_with("覆盖断裂", exp=exp)


if __name__ == "__main__":
    unittest.main()
