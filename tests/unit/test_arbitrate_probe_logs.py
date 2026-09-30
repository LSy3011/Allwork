"""ADR-056 步④-2 全局仲裁器 arbitrate_probe_logs 的红绿契约测试。

**对抗自验是本工具的验收本身**：一套合成最小期望（schema 合规、两队列
ur-r0/ur-w0、含写回 BANK/ADDR 载荷）+ 由它重放的合成绿用例必须 GREEN；
对绿用例做 8 类单点破坏（并集外取指 / stub 槽活动 / 漏发 / 错拍 / ISSUE
多拍少拍 / 非法写 / 漏写），每类必须单独立刻 RED 且命中对应裁决编号
——「能检测」若不是双向证明，监测承诺即空转（GLOBAL_MONITOR_RULE 明文
要求对抗用例必须 RED）。

本文件不依赖 e2e 产物与 TB 输出；合成期望的常数形状不等价于注册表重放，
仲裁器本就不推导常数（只读冻结输入）。
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

# 合成期望：ur-r0 → RTL iq16；ur-w0 → RTL iq20（rtl_probe_plan 动态规则 16+i/20+i）。
# ur-r0: FETCH rel {0(INIT), 3,4,5,6(TERM)}；ISSUE rel {0..5}（INIT 回放 2 拍
#        + 3 展开行 + 不齐平到 TERM——形状仅服务仲裁器序列比较）。
# ur-w0: FETCH rel {0(INIT), 5,6(vld 行带 BANK/ADDR), 7(TERM)}；ISSUE rel {0..6}。
IQ_R0, IQ_W0 = 16, 20
C0 = 100  # 公共启动锚（iq_probe_cycle 语义）；两队列共用同一 c0


def minimal_expected() -> dict:
    ev_r0 = ([{"cycle": c, "iq": "ur-r0", "kind": "FETCH",
               "payload": {"vld": 1 if c else 0}} for c in (0, 3, 4, 5, 6)]
             + [{"cycle": c, "iq": "ur-r0", "kind": "ISSUE",
                 "payload": {"vld": 0}} for c in range(6)])
    ev_w0 = ([{"cycle": 0, "iq": "ur-w0", "kind": "FETCH", "payload": {"vld": 0}},
              {"cycle": 5, "iq": "ur-w0", "kind": "FETCH",
               "payload": {"vld": 1, "BANK": 2, "ADDR": 10}},
              {"cycle": 6, "iq": "ur-w0", "kind": "FETCH",
               "payload": {"vld": 1, "BANK": 2, "ADDR": 11}},
              {"cycle": 7, "iq": "ur-w0", "kind": "FETCH", "payload": {"vld": 0}}]
             + [{"cycle": c, "iq": "ur-w0", "kind": "ISSUE",
                 "payload": {"vld": 0}} for c in range(7)])
    return {"schema": "adr056_d4_expected_events_v1",
            "registry": {"records_sha256": "synthetic-test"},
            "union_queues": ["ur-r0", "ur-w0"],
            "queues": {"ur-r0": {"events": ev_r0}, "ur-w0": {"events": ev_w0}}}


def write_green_case(root: Path) -> None:
    """由合成期望重放三日志（common c0；ISSUE INIT 回放数据逐拍全同——
    专门压住「ISSUE 不得按值折叠」这条语义）。"""
    fetch = ["# cycle iq addr_valid addr data_valid data"]
    for k, rel in enumerate((0, 3, 4, 5, 6)):          # ur-r0：地址链 0,1,2,3,4
        fetch.append(f"{C0 + rel} {IQ_R0} 1 {k} 0 0")
    for k, rel in enumerate((0, 5, 6, 7)):             # ur-w0：地址链 0,1,2,3
        fetch.append(f"{C0 + rel} {IQ_W0} 1 {k} 0 0")
    root.joinpath("iq_probe_trace.log").write_text("\n".join(fetch) + "\n")

    issue = ["# cycle iq dly_data_valid dly_data"]
    for rel in range(6):
        issue.append(f"{C0 + rel} {IQ_R0} 1 00aa")     # 同值连拍 = INIT 回放形态
    for rel in range(7):
        issue.append(f"{C0 + rel} {IQ_W0} 1 00bb")
    root.joinpath("iq_issue_trace.log").write_text("\n".join(issue) + "\n")

    wdata = "ab" * 16
    write = ["# cycle smc bank we addr wbe wdata",
             f"{C0 + 40} 0 2 1 10 ffff {wdata}",
             f"{C0 + 41} 0 2 1 11 ffff {wdata}"]
    root.joinpath("ur_write_trace.log").write_text("\n".join(write) + "\n")


class ArbitrateProbeLogsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_green_case(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _report(self) -> dict:
        return arb.arbitrate(self.root, minimal_expected())

    def _red_with(self, tag: str) -> dict:
        rep = self._report()
        self.assertEqual(rep["verdict"], "RED",
                         f"对抗注入未检出（期望含 {tag}）: {rep}")
        self.assertTrue(any(tag in v for v in rep["violations"]),
                        f"违例里未见 {tag}: {rep['violations']}")
        return rep

    # ---------- 绿基线 ----------

    def test_green_replay(self):
        rep = self._report()
        self.assertEqual(rep["verdict"], "GREEN", rep["violations"])
        self.assertEqual(rep["c0_by_queue"]["ur-r0"]["c0"], C0)
        self.assertEqual(rep["c0_by_queue"]["ur-r0"]["iq"], IQ_R0)
        self.assertEqual(rep["writes"]["illegal_count"], 0)
        self.assertEqual(rep["writes"]["missing_on_smc0"], 0)

    def test_tail_fetches_tolerated(self):
        # (G5) TERM 之后残尾取指（并集内队列）：计数入报告，不违例
        log = self.root / "iq_probe_trace.log"
        with log.open("a") as fh:
            fh.write(f"{C0 + 12} {IQ_R0} 1 5 0 0\n{C0 + 15} {IQ_R0} 1 6 0 0\n")
        rep = self._report()
        self.assertEqual(rep["verdict"], "GREEN", rep["violations"])
        self.assertEqual(rep["c0_by_queue"]["ur-r0"]["tail_fetches"], 2)

    def test_fetch_backpressure_stretch_collapses(self):
        # 同一请求地址保持到下一拍 = 反压多采样（INIT rel0 与首个 body rel3
        # 之间有空隙可容拉伸）：折叠后仍须全绿。同拍重复行物理上不可能
        # （TB 每拍每队列至多一行），不构造。
        log = self.root / "iq_probe_trace.log"
        lines = log.read_text().splitlines()
        idx = lines.index(f"{C0} {IQ_R0} 1 0 0 0")
        lines.insert(idx + 1, f"{C0 + 1} {IQ_R0} 1 0 0 0")
        log.write_text("\n".join(lines) + "\n")
        self.assertEqual(self._report()["verdict"], "GREEN")

    # ---------- 对抗注入（每类必须 RED 且命中裁决编号） ----------

    def test_out_of_union_fetch_is_red(self):
        # (G1) iq5 在探针覆盖集内但不在用例并集 → 任何取指脉冲即 RED
        with (self.root / "iq_probe_trace.log").open("a") as fh:
            fh.write(f"{C0 + 50} 5 1 0 0 0\n")
        self._red_with("(G1)")

    def test_stub_slot_fetch_is_red(self):
        # (G1) stub 槽 71（smi_top.v:572-586 纯 assign）出现行 = TB/映射断裂
        with (self.root / "iq_probe_trace.log").open("a") as fh:
            fh.write(f"{C0 + 50} 71 1 0 0 0\n")
        rep = self._red_with("(G1)")
        self.assertTrue(any("stub" in v for v in rep["violations"]))

    def test_out_of_union_issue_is_red(self):
        with (self.root / "iq_issue_trace.log").open("a") as fh:
            fh.write(f"{C0 + 50} 7 1 00ff\n")
        self._red_with("(G1)")

    def test_missing_fetch_is_red(self):
        log = self.root / "iq_probe_trace.log"
        lines = [l for l in log.read_text().splitlines()
                 if l != f"{C0 + 4} {IQ_R0} 1 2 0 0"]
        log.write_text("\n".join(lines) + "\n")
        self._red_with("(G2) 漏发")

    def test_shifted_fetch_is_red(self):
        log = self.root / "iq_probe_trace.log"
        text = log.read_text().replace(f"{C0 + 4} {IQ_R0} 1 2 0 0",
                                       f"{C0 + 14} {IQ_R0} 1 2 0 0")
        log.write_text(text)
        # rel 4 → 期望序列下标 2（INIT=0, rel3=1, rel4=2）
        self._red_with("(G2) FETCH 第2拍错拍")

    def test_missing_issue_beat_is_red(self):
        log = self.root / "iq_issue_trace.log"
        lines = [l for l in log.read_text().splitlines()
                 if l != f"{C0 + 2} {IQ_R0} 1 00aa"]
        log.write_text("\n".join(lines) + "\n")
        self._red_with("(G3)")

    def test_extra_issue_beat_is_red(self):
        # 追加超出期望的 1 拍即多发（ur-r0 合成期望 6 拍；裁决只看期望列表，
        # 与 ISSUE 模型的具体形状无关）
        with (self.root / "iq_issue_trace.log").open("a") as fh:
            fh.write(f"{C0 + 6} {IQ_R0} 1 00aa\n")
        self._red_with("(G3)")

    def test_issue_phase_shift_is_red(self):
        # 拍数对但相位错（ur-w0 整体后移 1 拍）必须 RED——防「只数拍数」的弱裁决
        log = self.root / "iq_issue_trace.log"
        shifted = []
        for l in log.read_text().splitlines():
            parts = l.split()
            if len(parts) == 4 and parts[1] == str(IQ_W0):
                parts[0] = str(int(parts[0]) + 1)
            shifted.append(" ".join(parts))
        log.write_text("\n".join(shifted) + "\n")
        self._red_with("(G3)")

    def test_illegal_write_is_red(self):
        with (self.root / "ur_write_trace.log").open("a") as fh:
            fh.write(f"{C0 + 60} 0 3 1 99 ffff {'00' * 16}\n")
        self._red_with("(G4) 非法写")

    def test_missing_write_on_smc0_is_red(self):
        log = self.root / "ur_write_trace.log"
        lines = [l for l in log.read_text().splitlines() if " 11 " not in l]
        log.write_text("\n".join(lines) + "\n")
        self._red_with("(G4) 漏写")

    # ---------- 输入断裂 ----------

    def test_missing_logs_are_red_not_silent(self):
        for name in ("iq_probe_trace.log", "iq_issue_trace.log", "ur_write_trace.log"):
            (self.root / name).unlink()
        rep = self._report()
        self.assertEqual(rep["verdict"], "RED")
        self.assertEqual(len(rep["violations"]), 3)

    def test_schema_gate(self):
        exp = minimal_expected()
        exp["schema"] = "adr055_whatever"
        rep = arb.arbitrate(self.root, exp)
        self.assertEqual(rep["verdict"], "RED")
        self.assertTrue(any("schema" in v for v in rep["violations"]))


if __name__ == "__main__":
    unittest.main()
