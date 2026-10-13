"""ADR-056 §D.3 协议模型注册表 + §D.4 期望事件生成的契约测试。

四件事分开验：
  * **完整性**：注册表加载自校验（sha256 封印）；任何记录改动不 --seal 即 RED；
    --seal 重算后恢复——冻结清单的防漂移机制本身须有齿。
  * **公式求值**：受限 eval 白名单（仅 + - * // 与负号）；bool/float/未知名/白名单外
    节点一律 RED；INIT_PAD/TERM_PHASE 已知 A1 点（l_init=3, TERM=128）锁定。
  * **唯一来源**：emit 工具的 l_init/TERM 相位必须经注册表求得——注入拒绝服务的
    注册表桩后 derive_queue_rows 必须失败（若工具内暗抄公式，本用例假绿即破）。
  * **期望先行**：--emit-events 生成 §D.4 FETCH/ISSUE 事件 JSON（合成最小构建，不依赖
    e2e 产物——derive 正确性由 test_emit_expected_fire_sequence 在真产物上另锁）；
    重建侧违例（分支回落）时**拒绝落盘**（假期望禁入库）。

**全部比整数/枚举，禁止比浮点；事件计数恒等式逐队列断言。**
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))
sys.path.append(str(_ROOT / "verification_v2" / "tools"))

import emit_expected_fire_sequence as fx                       # noqa: E402
from verification_v2.infra import protocol_registry as pr      # noqa: E402

REG_PATH = _ROOT / "verification_v2" / "tools" / "protocol_registry_v2.yaml"
REG_PATH_V1 = _ROOT / "verification_v2" / "tools" / "protocol_registry_v1.yaml"


class TestRegistryIntegrity(unittest.TestCase):
    """加载即自校验：封印、必填字段、frozen 状态、出处强制。"""

    def test_default_registry_loads_and_seal_consistent(self):
        reg = pr.load_default()
        self.assertEqual(reg.version, 3)
        self.assertEqual(len(reg.records_digest), 64)   # sha256 hex
        # 冻结对象枚举齐备（§D.3：INIT 公式/TERM 公式/残尾语义/断言形式/投影×2/全局监测
        #   + v2 勘误新增 ISSUE_PHASE_OFFSET/ISSUE_TOTAL_FORMULA
        #   + v3 追加冻结：公式语义×4/漂移政策/分段规则/容量口径/已证范围）
        for name in ("INIT_PAD_FORMULA", "TERM_PHASE_FORMULA", "PROBES_TAIL_SEMANTICS",
                     "ASSERT_FORMS_I_IV", "PROJ_FETCH_UNROLL", "PROJ_ISSUE_UNROLL",
                     "GLOBAL_MONITOR_RULE", "ISSUE_PHASE_OFFSET", "ISSUE_TOTAL_FORMULA",
                     "FORMULA_SEMANTICS_ADD", "FORMULA_SEMANTICS_SCALE_SUM",
                     "FORMULA_SEMANTICS_HORNER_V3U", "FORMULA_SEMANTICS_ADDCMULU",
                     "PLACEMENT_DRIFT_POLICY", "SEGMENTATION_DECISION_RULE",
                     "CAPACITY_MEASURED_SCOPE", "PROVEN_SCOPE_V3"):
            rec = reg.get(name)
            self.assertEqual(rec["status"], "frozen")
            self.assertTrue(rec["provenance"], f"{name} 出处不得为空")

    def test_v1_archive_preserved_untouched(self):
        """用户限定④：旧封印与历史记录保留——v1 文件仍可加载且封印完好。"""
        reg = pr.ProtocolRegistry(REG_PATH_V1)
        self.assertEqual(reg.version, 1)
        self.assertEqual(len(reg.records_digest), 64)
        # v1 的 PROJ_ISSUE_UNROLL 保留旧文本（勘误不追溯改史）
        self.assertIn("TERM 无 ISSUE", reg.get("PROJ_ISSUE_UNROLL")["value"])

    def test_v2_archive_preserved_untouched(self):
        """v3 追加冻结同规：v2 文件仍可加载、封印完好、边界文本未被追溯改写。"""
        reg = pr.ProtocolRegistry(REG_PATH)
        self.assertEqual(reg.version, 2)
        self.assertEqual(len(reg.records_digest), 64)
        # v2 的 INIT_PAD 边界仍为「A1 已证形态：add，II=2」（v3 扩展不追溯改史）
        self.assertIn("A1 已证形态：add，II=2",
                      reg.get("INIT_PAD_FORMULA")["proven_boundary"])
        # v2 无 v3 新增记录（追加冻结只进新文件）
        self.assertNotIn("PROVEN_SCOPE_V3", reg._records)

    def test_tampered_record_breaks_seal_and_reseal_recovers(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "reg.yaml"
            shutil.copy(REG_PATH, p)
            text = p.read_text(encoding="utf-8")
            tampered = text.replace("skip*II + 1", "skip*II + 2", 1)   # 改常数不 seal
            self.assertNotEqual(text, tampered)
            p.write_text(tampered, encoding="utf-8")
            with self.assertRaisesRegex(pr.RegistryError, "records_sha256 不匹配"):
                pr.ProtocolRegistry(p)
            pr._seal(p)                                                # 重封后恢复
            reg2 = pr.ProtocolRegistry(p)
            self.assertEqual(reg2.evaluate("INIT_PAD_FORMULA", skip=1, II=2), 4)

    def test_malformed_registry_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.yaml"
            p.write_text('registry: other\nversion: 1\nrecords: []\n', encoding="utf-8")
            with self.assertRaisesRegex(pr.RegistryError, "registry"):
                pr.ProtocolRegistry(p)
            p.write_text('registry: protocol_model\nversion: 0\nrecords: []\n',
                         encoding="utf-8")
            with self.assertRaisesRegex(pr.RegistryError, "version"):
                pr.ProtocolRegistry(p)


class TestFormulaEvaluation(unittest.TestCase):
    """受限 eval：白名单算子、int 绑定、已知 A1 点。"""

    @classmethod
    def setUpClass(cls):
        cls.reg = pr.load_default()

    def test_known_a1_points(self):
        li = self.reg.evaluate("INIT_PAD_FORMULA", skip=1, II=2)
        self.assertEqual(li, 3)                       # ADR-055 M7：+1 拍
        tp = self.reg.evaluate("TERM_PHASE_FORMULA", l_init=li, II=2, R_q=62)
        self.assertEqual(tp, 128)                     # 3+1+2·62（与 emit/check 两工具一致）
        # v2 勘误新增（用户限定③锁式）
        self.assertEqual(self.reg.evaluate("ISSUE_PHASE_OFFSET"), 1)
        self.assertEqual(self.reg.evaluate("ISSUE_TOTAL_FORMULA",
                                           l_init=li, II=2, R_q=62), 129)

    def test_rejects_non_int_binds_and_unknown_names(self):
        with self.assertRaises(pr.RegistryError):
            self.reg.evaluate("INIT_PAD_FORMULA", skip=True, II=2)      # bool 拒
        with self.assertRaises(pr.RegistryError):
            self.reg.evaluate("INIT_PAD_FORMULA", skip=1.0, II=2)       # float 拒
        with self.assertRaises(pr.RegistryError):
            self.reg.evaluate("INIT_PAD_FORMULA", skip=1)               # 缺 II

    def test_whitelist_enforced_and_non_formula_rejected(self):
        with self.assertRaisesRegex(pr.RegistryError, "白名单外节点"):
            pr.ProtocolRegistry._eval_node(
                __import__("ast").parse("skip**2", mode="eval"), {"skip": 1})
        with self.assertRaisesRegex(pr.RegistryError, "非 formula"):
            self.reg.evaluate("PROBES_TAIL_SEMANTICS")
        with self.assertRaisesRegex(pr.RegistryError, "无记录"):
            self.reg.evaluate("NO_SUCH_RECORD")


class TestRegistryIsSoleSource(unittest.TestCase):
    """§D.3：emit 工具不得内联常数公式——断供注册表则重建必须失败。"""

    def test_derive_fails_when_registry_refuses(self):
        layout = None   # ur-r0 路径会在 payload 构建前触到 layout——用非 UR 队列规避
        ops = [{"trace_id": "a", "t": 0, "iq": "fpadd", "stride": 0,
                "logical_addr": 0, "dly": 0}]
        refusing = mock.Mock(spec=pr.ProtocolRegistry)
        refusing.evaluate.side_effect = pr.RegistryError("断供")
        with self.assertRaisesRegex(pr.RegistryError, "断供"):
            fx.derive_queue_rows(2, ops, 1, layout, "fpadd", reg=refusing)
        self.assertGreaterEqual(refusing.evaluate.call_count, 1)

    def test_issue_projection_fails_when_registry_refuses(self):
        """v2：ISSUE 相位偏移与总拍数公式同样只走注册表——断供即重建失败。"""
        ops = [{"trace_id": "a", "t": 0, "iq": "fpadd", "stride": 0,
                "logical_addr": 0, "dly": 0}]
        rows = fx.derive_queue_rows(2, ops, 1, None, "fpadd")  # 真注册表，行侧成功
        refusing = mock.Mock(spec=pr.ProtocolRegistry)
        refusing.evaluate.side_effect = pr.RegistryError("断供")
        with self.assertRaisesRegex(pr.RegistryError, "断供"):
            fx._rows_to_events("fpadd", rows, 2, {"skip": 0, "l_init": 1, "r_q": 1},
                               reg=refusing)


def _synth_build(root: Path) -> None:
    """最小合规 unroll 构建：单队列 fpadd、II=2、两 op 两槽、N_eff=1。

    skip=0, l_init=1, R_q=(1//2+1)-0=1, L_q=4；TERM abs_t=1+1+2·1=4；
    v2 ISSUE：INIT 回放 2 拍(cycle 1..2) + BODY 2 拍(cycle 3,4=abs_t+1) +
    TERM 1 拍(cycle 5) = l_init+1+II·R_q+1 = 5。
    """
    (root / "compiler_schedule.json").write_text(json.dumps({
        "II": 2,
        "ops": {"a": {"t": 0, "iq": "fpadd", "stride": 0, "logical_addr": 0, "dly": 0},
                "b": {"t": 1, "iq": "fpadd", "stride": 0, "logical_addr": 0, "dly": 0}},
    }), encoding="utf-8")
    (root / "branch_report.json").write_text(
        json.dumps({"branches": {"fpadd": "unroll"}}), encoding="utf-8")
    (root / "physical_instructions.json").write_text("[]", encoding="utf-8")


class TestEmitEvents(unittest.TestCase):
    """--emit-events（§D.3 期望先行）契约与门控。"""

    def test_emit_payload_shape_and_count_identities(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "build"
            root.mkdir()
            _synth_build(root)
            out = Path(td) / "expected_events.json"
            bad = fx.audit(root, 1, hw_config=str(_ROOT / "hw_config"),
                           emit_events_path=out)
            # 产物为空 → compare 侧报「产物无该队列行」；重建侧无违例 → 落盘必须成功
            self.assertTrue(any("产物无该队列行" in b for b in bad), bad)
            self.assertFalse(any("emit-events" in b for b in bad), bad)
            self.assertTrue(out.exists(), "重建侧无违例时期望事件必须落盘")
            d = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(d["schema"], "adr056_d4_expected_events_v1")
            self.assertEqual(d["registry"]["version"], 3)
            self.assertEqual(len(d["registry"]["records_sha256"]), 64)
            self.assertEqual(d["union_queues"], ["fpadd"])
            q = d["queues"]["fpadd"]
            self.assertEqual((q["skip"], q["l_init"], q["R_q"], q["L_q"]), (0, 1, 1, 4))
            self.assertEqual(q["issue_beats"], 5)          # l_init+1+II·R_q+1（v2）
            evs = q["events"]
            fech = [e for e in evs if e["kind"] == "FETCH"]
            iss = [e for e in evs if e["kind"] == "ISSUE"]
            self.assertEqual(len(fech), q["L_q"])          # 行数恒等式
            self.assertEqual(len(iss), q["issue_beats"])   # ISSUE 拍数恒等式
            # FETCH 相位：INIT=0、BODY 严格连续 l_init+1..、TERM 收尾
            self.assertEqual([e["cycle"] for e in fech], [0, 2, 3, 4])
            self.assertEqual([e["payload"]["stage"] for e in fech],
                             ["INIT", "BODY", "BODY", "TERM"])
            # ISSUE（v2 勘误模型）：INIT 回放 l_init+1=2 拍(cycle 1,2)；每展开行
            # 1 拍(cycle=abs_t+1)；TERM 恰 1 拍(cycle=TERM fetch+1)
            init_iss = [e for e in iss if e["payload"]["stage"] == "INIT"]
            self.assertEqual([e["cycle"] for e in init_iss], [1, 2])
            self.assertEqual([e["payload"]["issue_rep"] for e in init_iss], [0, 1])
            self.assertEqual([e["cycle"] for e in iss], [1, 2, 3, 4, 5])
            # 共同启动锚语义入档（消费侧不得逐队列平移）
            self.assertIn("不允许逐队列独立平移", d["semantics"]["cycle_base"])

    def test_issue_projection_v2_locks(self):
        """用户限定③的针对性锁：相位 +1、TERM 恰一拍、总拍数公式、内部事件标记。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "build"
            root.mkdir()
            _synth_build(root)
            out = Path(td) / "expected_events.json"
            fx.audit(root, 1, hw_config=str(_ROOT / "hw_config"), emit_events_path=out)
            q = json.loads(out.read_text(encoding="utf-8"))["queues"]["fpadd"]
            fech = {e["payload"]["trace_id"]: e["cycle"] for e in q["events"]
                    if e["kind"] == "FETCH"}
            iss = [e for e in q["events"] if e["kind"] == "ISSUE"]
            # ① INIT/BODY 相位 +1：每 BODY ISSUE cycle == 对应 BODY FETCH cycle + 1
            body_f = {t: c for t, c in fech.items() if not t.startswith("init_")
                      and not t.startswith("term_")}
            body_i = [e for e in iss if e["payload"]["stage"] == "BODY"]
            self.assertEqual(sorted(e["cycle"] for e in body_i),
                             sorted(c + 1 for c in body_f.values()))
            self.assertEqual(len(body_i), len(body_f))        # 1 拍/行
            # INIT 回放拍相位：FETCH(cycle 0) + 1 起连续 l_init+1 拍
            init_i = sorted(e["cycle"] for e in iss if e["payload"]["stage"] == "INIT")
            self.assertEqual(init_i, list(range(fech["init_fpadd"] + 1,
                                                fech["init_fpadd"] + q["l_init"] + 2)))
            # ② TERM 恰一拍：(fetch, issue) 各 1 条，issue == fetch+1
            term_f = [c for t, c in fech.items() if t.startswith("term_")]
            term_i = [e for e in iss if e["payload"]["stage"] == "TERM"]
            self.assertEqual(len(term_f), 1)
            self.assertEqual(len(term_i), 1)
            self.assertEqual(term_i[0]["cycle"], term_f[0] + 1)
            # 内部发射事件标记（用户限定②：≠有效算术操作）
            self.assertTrue(term_i[0]["payload"]["internal"])
            self.assertEqual(term_i[0]["payload"]["vld"], 1)  # 指令字 VLD 位
            # ③ 总拍数公式：issue_beats == l_init+1+II·R_q+1
            self.assertEqual(q["issue_beats"],
                             q["l_init"] + 1 + 2 * q["R_q"] + 1)
            self.assertEqual(len(iss), q["issue_beats"])

    def test_emit_refused_when_rebuild_side_violates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "build"
            root.mkdir()
            _synth_build(root)
            br = json.loads((root / "branch_report.json").read_text())
            br["branches"]["fpadd"] = "embed"          # 分支回落（M4 假绿口）
            (root / "branch_report.json").write_text(json.dumps(br))
            out = Path(td) / "expected_events.json"
            bad = fx.audit(root, 1, hw_config=str(_ROOT / "hw_config"),
                           emit_events_path=out)
            self.assertTrue(any("unroll" in b for b in bad), bad)
            self.assertTrue(any("emit-events" in b for b in bad), bad)
            self.assertFalse(out.exists(), "重建侧有违例时假期望不得落盘")


if __name__ == "__main__":
    unittest.main()
