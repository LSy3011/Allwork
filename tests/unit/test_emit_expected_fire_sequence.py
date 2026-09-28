"""ADR-055 D4(b-1)/(c)：独立事件检查器 `emit_expected_fire_sequence` 的契约与变异测试。

两件事分开验：
  * **契约**（纯函数）：由 II + t 集合 + 布局独立重建 ur-r3 的梯形与烘焙地址，断言填充/排干
    边界（x_read_1 r_k=2 → ADDR 254,255,0,1）、驻留 op 恒址（alpha_read_3 BANK3/ADDR0）、
    l_init 垫拍（skip·II+1）、行数 = II·R_q+2。
  * **有齿**（变异，D4(c) 精神）：对真 e2e 产物注入 6 类病理（BANK 漂移 / ADDR off-by-one /
    驻留 op 被误步进（M1 类 iq_max_stride 同抄）/ 调度侧 stride 被改（证独立于产物 payload）/
    分支回落 / 行数缺失），断言检查器逐条变红——否则「全绿」是零信号。

**全部比整数，禁止比字符串**（比字符串处仅限 trace_id / 队列名等标识）。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))
sys.path.append(str(_ROOT / "verification_v2" / "tools"))

from api.hw_spec import HardwareSpec                      # noqa: E402
from core.memory_layout import PhysicalMemoryLayout        # noqa: E402

import emit_expected_fire_sequence as fx                   # noqa: E402

BUILD = _ROOT / "build" / "verification_v2" / "add"
_ENSURED = False


def _ensure_add_build() -> Path:
    """确保 build/add 是**当前尺度（n_rows=8 → N_eff=2）且带扩展字段**的 e2e 构建。

    三类陈旧一律重建：① 无构建；② 旧格式（schedule 无 stride——ADR-055 D4(b-1) 扩展前）；
    ③ 尺度不对（同目录被后续 n=60 等 e2e 覆盖——用**本检查器自身**审计裁决，
    即「新鲜 = 独立检查通过」，不引入第二套猜尺度的启发式）。
    """
    global _ENSURED
    if _ENSURED:
        return BUILD
    sch = BUILD / "compiler_schedule.json"
    need = True
    if sch.exists():
        doc = json.loads(sch.read_text())
        first = next(iter(doc.get("ops", {}).values()), {})
        if "stride" in first:
            need = bool(_audit(BUILD))       # ③：独立审计即新鲜度判据
    if need:
        res = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", "add",
             "--mode", "e2e", "--n-rows", "8"],
            cwd=_ROOT, capture_output=True, text=True, timeout=900)
        if res.returncode != 0:
            raise AssertionError(f"add e2e 重建失败（检查器无输入）:\n{res.stdout[-1500:]}")
    _ENSURED = True
    return BUILD


def _copy_build(td: str) -> Path:
    dst = Path(td) / "add"
    shutil.copytree(_ensure_add_build(), dst)
    return dst


def _mutate(path: Path, fn) -> None:
    doc = json.loads(path.read_text())
    fn(doc)
    path.write_text(json.dumps(doc, indent=2))


def _audit(root: Path):
    # 显式绝对路径：本文件可能在任意 cwd 下被收集（相对 "hw_config" 会随 cwd 失效）
    return fx.audit(root, num_iterations=2, hw_config=str(_ROOT / "hw_config"))


class TestDeriveContract(unittest.TestCase):
    """契约纯函数：梯形 + 烘焙地址 + 填充/排干（不触产物，不 import assembler）。"""

    @classmethod
    def setUpClass(cls):
        cls.layout = PhysicalMemoryLayout.from_spec(
            HardwareSpec.load(str(_ROOT / "hw_config")))

    def test_ur_r3_trapezoid_and_baked_addresses(self):
        ops = [
            {"trace_id": "alpha_read_3", "t": 2, "iq": "ur-r3",
             "stride": 0, "logical_addr": 768, "dly": 0},
            {"trace_id": "x_read_1", "t": 7, "iq": "ur-r3",
             "stride": 1, "logical_addr": 0, "dly": 0},
        ]
        rows = fx.derive_queue_rows(2, ops, 2, self.layout, "ur-r3")
        # R_q = (7//2 + 2) − 2//2 = 4；L_q = 2·4+2 = 10
        self.assertEqual(len(rows), 10)
        self.assertEqual([r["stage"] for r in rows], ["INIT"] + ["BODY"] * 8 + ["TERM"])
        # l_init = skip·II+1 = 1·2+1 = 3；首 BODY 落 l_init+1 = 4；TERM 落 4+8 = 12
        self.assertEqual(int(rows[0]["header"]["LOOPS"]), 3)
        self.assertEqual([int(r["abs_t"]) for r in rows],
                         [0, 4, 5, 6, 7, 8, 9, 10, 11, 12])
        # 槽序：t%II → alpha(k=0) 与 x_read_1(k=1) 交替，两槽占满 → 无气泡
        self.assertEqual([str(r["trace_id"]) for r in rows[1:9]],
                         ["alpha_read_3", "x_read_1"] * 4)
        self.assertFalse(any(str(r["trace_id"]).startswith("nop_") for r in rows))
        # 相位编码：有效行 vld=1/level(EMBED_LOOPS)=0/loops=0/inc=0/embed_end=0/inc_embed=0
        for r in rows[1:9]:
            h = r["header"]
            self.assertEqual((int(h["VLD"]), int(h["EMBED_LOOPS"]), int(h["LOOPS"]),
                              int(h["INC"]), int(h["EMBED_END"]), int(h["INC_EMBED"]),
                              int(h["JUMP"])), (1, 0, 0, 0, 0, 0, 0))
        self.assertEqual((int(rows[0]["header"]["VLD"]), int(rows[-1]["header"]["JUMP"])),
                         (0, 1))
        # 驻留型（stride=0, la=768 → bank3/offset0）：地址恒 0，不随 j 步进
        alpha = [r for r in rows if str(r["trace_id"]) == "alpha_read_3"]
        self.assertEqual([(int(r["payload"]["BANK"]), int(r["payload"]["ADDR"]))
                          for r in alpha], [(3, 0)] * 4)
        # 步进型（stride=1, la=0, r_k = 7//2−1 = 2）：j=0,1 填充区预卷 254,255；j=2,3 有效 0,1
        xr = [r for r in rows if str(r["trace_id"]) == "x_read_1"]
        self.assertEqual([int(r["payload"]["ADDR"]) for r in xr], [254, 255, 0, 1])
        self.assertEqual([int(r["payload"]["BANK"]) for r in xr], [0] * 4)

    def test_slot_collision_and_single_slot_rejected(self):
        """同槽两 op / II<2 必须显式失败（拒绝越界推断，不静默）。"""
        collide = [
            {"trace_id": "a", "t": 0, "iq": "ur-r0", "stride": 1, "logical_addr": 0, "dly": 0},
            {"trace_id": "b", "t": 2, "iq": "ur-r0", "stride": 1, "logical_addr": 0, "dly": 0},
        ]
        with self.assertRaisesRegex(ValueError, "槽冲突"):
            fx.derive_queue_rows(2, collide, 2, self.layout, "ur-r0")
        with self.assertRaisesRegex(ValueError, "iq_II=1<2"):
            fx.derive_queue_rows(1, collide[:1], 2, self.layout, "ur-r0")


class TestAuditGreen(unittest.TestCase):
    """真 e2e 产物上独立检查器全绿（并证明确实审到了队列——由变异测试反证）。"""

    def test_add_e2e_independent_check_all_green(self):
        root = _ensure_add_build()
        bad = _audit(root)
        self.assertEqual(bad, [], f"独立检查器在 add e2e 产物上报违例:\n" + "\n".join(bad[:20]))


class TestAuditMutation(unittest.TestCase):
    """D4(c)：6 类病理必须逐条变红。"""

    def test_m1_bank_field_corruption_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root = _copy_build(td)
            def flip(doc):
                for r in doc:
                    if r["iq_name"] == "ur-r3" and r["trace_id"] == "alpha_read_3":
                        r["payload"]["BANK"] = 2      # 真值 3
                        return
                raise AssertionError("未找到 alpha_read_3")
            _mutate(root / "physical_instructions.json", flip)
            bad = _audit(root)
            self.assertTrue(any("payload.BANK" in b and "ur-r3" in b for b in bad),
                            f"BANK 漂移必须被抓: {bad}")

    def test_m2_addr_off_by_one_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root = _copy_build(td)
            def bump(doc):
                for r in doc:
                    if r["iq_name"] == "ur-r3" and r["trace_id"] == "x_read_1":
                        r["payload"]["ADDR"] = int(r["payload"]["ADDR"]) + 1
                        return
                raise AssertionError("未找到 x_read_1")
            _mutate(root / "physical_instructions.json", bump)
            bad = _audit(root)
            self.assertTrue(any("payload.ADDR" in b for b in bad),
                            f"地址 off-by-one 必须被抓: {bad}")

    def test_m3_resident_op_wrongly_stepped_caught(self):
        """M1 类错误（两边同抄 iq_max_stride）：驻留 op（stride=0）被写成 j 步进。"""
        with tempfile.TemporaryDirectory() as td:
            root = _copy_build(td)
            def step(doc):
                n = 0
                for r in doc:
                    if r["iq_name"] == "ur-r3" and r["trace_id"] == "alpha_read_3":
                        r["payload"]["ADDR"] = n          # 0,1,2,3（真值恒 0）
                        n += 1
                if n == 0:
                    raise AssertionError("未找到 alpha_read_3")
            _mutate(root / "physical_instructions.json", step)
            bad = _audit(root)
            self.assertTrue(any("payload.ADDR" in b for b in bad),
                            f"驻留 op 被误步进必须被抓: {bad}")

    def test_m4_schedule_stride_mutation_caught(self):
        """证检查器地址来源独立于产物 payload：只改 **调度侧** stride → 产物即成错址。"""
        with tempfile.TemporaryDirectory() as td:
            root = _copy_build(td)
            def stride2(doc):
                if "x_read_1" not in doc["ops"]:
                    raise AssertionError("调度无 x_read_1")
                doc["ops"]["x_read_1"]["stride"] = 2      # 真值 1
            _mutate(root / "compiler_schedule.json", stride2)
            bad = _audit(root)
            self.assertTrue(any("payload.ADDR" in b for b in bad),
                            f"调度侧 stride 改动必须使产物成错址: {bad}")

    def test_m5_branch_fallback_caught(self):
        """M4 假绿口：应展开却回落 embed（分支表）必须被拒，而非静默跳过。"""
        with tempfile.TemporaryDirectory() as td:
            root = _copy_build(td)
            def fallback(doc):
                doc["branches"]["ur-r0"] = "embed"
            _mutate(root / "branch_report.json", fallback)
            bad = _audit(root)
            self.assertTrue(any("ur-r0" in b and "unroll" in b for b in bad),
                            f"分支回落必须被抓: {bad}")

    def test_m6_row_dropped_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root = _copy_build(td)
            def drop(doc):
                for i, r in enumerate(doc):
                    if r["iq_name"] == "ur-r0":
                        del doc[i]
                        return
                raise AssertionError("未找到 ur-r0")
            _mutate(root / "physical_instructions.json", drop)
            bad = _audit(root)
            self.assertTrue(any("行数" in b and "ur-r0" in b for b in bad),
                            f"缺行必须被抓: {bad}")


if __name__ == "__main__":
    unittest.main()
