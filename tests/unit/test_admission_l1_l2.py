"""ADR-056 步③：L1 配置面 + UnrollAdmission 凭据 + L2 八闸门 + runner 接线的契约测试。

八闸门覆盖矩阵（m 系列 = tests/unit/test_rtovr_sparse_guards.py::TestMutation，既有）：

| 闸门     | 语义                                            | 覆盖点 |
|---------|-------------------------------------------------|--------|
| G-op    | 计算单元 ⊆ {FPMUL,FPADD}（§A.2-2）               | 本文件 test_gate_gop_out_of_scope |
| G-II    | II=1 单槽归 ADR-054 平坦分支；G-II 本体为防御闸  | 本文件 test_gate_gii_*（两逼近路径锁死，见注） |
| G-slot  | 同 IQ 同槽响拒（kernel_map 构造期，先于各闸门）   | m16（既有） |
| G-stride| 步进型 memory op 步长一致（stride=0 驻留豁免）    | m19（既有） |
| G-wrap  | stride·R_q ≥ bank_depth 拒（烘焙环之前）          | m20（既有） |
| G-addr  | 基址命中声明区 + LIVE 未取模地址 ⊆ 声明区（§F.5） | 本文件 test_gate_gaddr_*（两例） |
| G-cap   | 展开行数 ≤ 128（烘焙环之后）                      | m18（既有） |
| G-stage | 单 stage（src 现无 stage_id 生产者，虚设防御闸）  | 本文件 test_gate_gstage_rejected |
| G-dp    | 拒 SMI_DS_T datapath IQ（eff_num_iter=2 专用路径）| 本文件 test_gate_gdp_rejected |

注（G-II 可达性论证，2026-09-28 逐行核实 assembler.py）：admission 分支内
`iq_II < 2` 出口经静态分析**不可达**——II=1 单槽已在平坦分支（:413）先行分流
（合法，ADR-054 已证路径），II=1 多槽被 D1 槽冲突（:384）先拦，smi-ds-t 强制
iq_II=DST_REGION_II=2。故以两条逼近路径的行为断言锁死其边界，不构造虚假 raise。

铁律对照：任一 L2 闸门失败 → STATIC_ADMISSION_REJECTED，禁止「失败即回落
embed」（ADR-055 D1）；admission=None（未请求）全程零行为变化（§B.3）。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import yaml

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))

from api.hw_spec import HardwareSpec                                   # noqa: E402
from driver.assembler import PhysicalAssembler, UnrollAdmission        # noqa: E402
from verification_v2.infra.pipeline_runner import PipelineRunner       # noqa: E402
from verification_v2.ops.configurable import ConfigurableOperator      # noqa: E402

OPS_DIR = _ROOT / "verification_v2" / "ops"

# 合法 add 形态基准（各用例在此基础上做**单项**病理变异，保证唯一变红点）。
_BASE_YAML = {
    "name": "t",
    "formula": "x + alpha * other",
    "tensors": {
        "x":         {"bank": 0, "role": "input",    "offset": 0, "length": 256},
        "other":     {"bank": 1, "role": "input",    "offset": 0, "length": 256},
        "alpha":     {"bank": 3, "role": "param",    "offset": 0, "length": 256},
        "constants": {"bank": 7, "role": "constant", "offset": 0, "length": 256},
        "y":         {"bank": 2, "role": "output",   "offset": 0, "length": 256},
    },
    "test_data": {"x": {"range": [-10.0, 10.0]}, "alpha": [2.5]},
    "experimental_unroll": True,
}


def _mk_op(cfg: dict) -> ConfigurableOperator:
    with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
        path = f.name
    return ConfigurableOperator(path)


def _base(**overrides) -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in _BASE_YAML.items()}
    cfg["tensors"] = {k: dict(v) for k, v in _BASE_YAML["tensors"].items()}
    cfg.update(overrides)
    return cfg


class _FakeLIROp:
    """最小 LIROp 替身（照 m15-m20 形态；UR_READ/ur-r0 走 memory-IQ 布局分支）。"""

    def __init__(self, tid="tA", t=0, stride=0, addr=0, op_type="UR_READ", **extra):
        self.op_type = op_type
        self.trace_id = tid
        self.metadata = {"unit_id": "ur-r0", "global_t": t,
                         "stride": stride, "logical_addr": addr}
        self.metadata.update(extra)


# ---------------------------------------------------------------- L1 配置面

class TestL1ConfigGate(unittest.TestCase):
    """ConfigurableOperator 层：键 allowlist / 请求标记 / 文本闸 / 准入承诺。"""

    def test_l1_flag_absent_means_not_requested(self):
        """缺省缺席 = 未请求：不触发准入承诺闸，experimental_unroll is False。
        exp(x) 越出 §A.2-1 子集但未请求 → 构造成功（legacy 兼容面，§B.3）。"""
        cfg = _base(formula="exp(x)")
        del cfg["experimental_unroll"]
        op = _mk_op(cfg)
        self.assertIs(op.experimental_unroll, False)

    def test_l1_flag_non_bool_rejected(self):
        """§B.1：请求标记严格 bool——1/0/"true"（YAML 常见误写）全部响拒。"""
        for bad in (1, 0, "true"):
            with self.assertRaisesRegex(ValueError, "非 bool"):
                _mk_op(_base(experimental_unroll=bad))

    def test_l1_unknown_top_key_rejected(self):
        """拼错字段（experimental_unrol）在键 allowlist 处响亮失败，绝不静默忽略
        （hco notes-4：静默忽略 = 用户以为已请求准入而实际未生效）。"""
        cfg = _base()
        del cfg["experimental_unroll"]
        cfg["experimental_unrol"] = True
        with self.assertRaisesRegex(ValueError, "experimental_unrol"):
            _mk_op(cfg)

    def test_l1_unknown_tensor_key_rejected(self):
        cfg = _base()
        cfg["tensors"]["x"]["lenght"] = 256
        with self.assertRaisesRegex(ValueError, "lenght"):
            _mk_op(cfg)

    def test_l1_unknown_testdata_key_rejected(self):
        cfg = _base()
        cfg["test_data"] = {"x": {"rang": [0, 1]}}
        with self.assertRaisesRegex(ValueError, "rang"):
            _mk_op(cfg)

    def test_l1_negation_gate_text_level(self):
        """§A.2-1 文本级取负闸（sympify 之前，全量无条件）：
        -x / -(x+y) / -alpha 拒；负数值字面量与一元正号放行。"""
        for bad in ("-x + other", "-(x + other)", "-alpha + x"):
            with self.assertRaisesRegex(ValueError, r"\[SYNTAX_UNSUPPORTED\]"):
                cfg = _base(formula=bad)
                del cfg["experimental_unroll"]   # 无 flag 也生效（全量闸）
                _mk_op(cfg)
        for good in ("x + (-2.5)", "-3.0 * x + other", "+x + other"):
            cfg = _base(formula=good)
            del cfg["experimental_unroll"]
            _mk_op(cfg)   # 不抛即过

    def test_l1_subset_gate_requested_only(self):
        """§A.2-1/2 严格子集闸仅对声明 flag 的准入算子生效（语言 ≡ 参考发生器）。"""
        # requested：函数调用 / 减法 / 未声明名 / 保留名 全部 [SYNTAX_UNSUPPORTED]
        for bad in ("exp(x)", "x - other", "x + z", "y + x"):
            with self.assertRaisesRegex(ValueError, r"\[SYNTAX_UNSUPPORTED\]"):
                _mk_op(_base(formula=bad))
        # 同一 exp(x) 未请求 → 过（与 test_l1_flag_absent 互为对照，锁定门控位置）
        cfg = _base(formula="exp(x)")
        del cfg["experimental_unroll"]
        _mk_op(cfg)
        # 子集内合法公式 + flag → 过
        _mk_op(_base())

    def test_l1_fp32_only(self):
        """§A.2-3：准入要求 fp32（element_bits 缺省 32；声明 fp16 即拒）。"""
        cfg = _base()
        cfg["tensors"]["x"]["precision"] = "fp16"
        with self.assertRaisesRegex(ValueError, "fp32"):
            _mk_op(cfg)

    def test_l1_exactly_one_output(self):
        cfg = _base()
        cfg["tensors"]["z"] = {"bank": 4, "role": "output", "offset": 0, "length": 256}
        with self.assertRaisesRegex(ValueError, "恰一 output"):
            _mk_op(cfg)
        cfg = _base()
        del cfg["tensors"]["y"]
        with self.assertRaisesRegex(ValueError, "恰一 output"):
            _mk_op(cfg)

    def test_l1_role_stride_consistency(self):
        """§A.2-4：input/output stride=1、param/constant stride=0 驻留。
        闸门次序：AST 子集闸 → 恰一 output → role→stride（同一循环内 role 非法先拒），
        故 role 病理用「新增场外张量 z role=weight」（不动公式名字集与 output 数）。"""
        cfg = _base()
        cfg["tensors"]["x"]["stride"] = 2
        with self.assertRaisesRegex(ValueError, "stride"):
            _mk_op(cfg)
        cfg = _base()
        cfg["tensors"]["alpha"]["stride"] = 1
        with self.assertRaisesRegex(ValueError, "stride"):
            _mk_op(cfg)
        cfg = _base()
        cfg["tensors"]["z"] = {"bank": 4, "role": "weight", "offset": 0, "length": 256}
        with self.assertRaisesRegex(ValueError, "role"):
            _mk_op(cfg)

    def test_l1_length_required_int_positive(self):
        """§F.5（hco M5）：准入算子 length 必填且为 >0 的 int。"""
        for mutate in (
                lambda c: c["tensors"]["x"].pop("length"),       # 缺失
                lambda c: c["tensors"]["x"].update(length="256"),  # 非 int
                lambda c: c["tensors"]["x"].update(length=True),   # bool 亦拒
                lambda c: c["tensors"]["x"].update(length=0),      # 非正
                lambda c: c["tensors"]["x"].update(length=-4)):
            cfg = _base()
            mutate(cfg)
            with self.assertRaisesRegex(ValueError, "length"):
                _mk_op(cfg)


# ---------------------------------------------------------------- UnrollAdmission 凭据

class TestUnrollAdmissionDataclass(unittest.TestCase):
    """构造期全量校验（frozen 不可变凭据）：形态/int 非 bool/正值/同 bank 两两不交。"""

    def test_empty_regions_rejected(self):
        with self.assertRaisesRegex(ValueError, "非空"):
            UnrollAdmission(op_name="add", tensor_regions={})

    def test_field_type_validation(self):
        with self.assertRaisesRegex(ValueError, "非 int"):
            UnrollAdmission(op_name="add", tensor_regions={"x": (True, 0, 10)})
        with self.assertRaisesRegex(ValueError, "非法"):
            UnrollAdmission(op_name="add", tensor_regions={"x": (0, -1, 10)})
        with self.assertRaisesRegex(ValueError, "非法"):
            UnrollAdmission(op_name="add", tensor_regions={"x": (0, 0, 0)})
        with self.assertRaisesRegex(ValueError, "形态非法"):
            UnrollAdmission(op_name="add", tensor_regions={"x": [0, 0, 10]})

    def test_same_bank_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, "重叠"):
            UnrollAdmission(op_name="add",
                            tensor_regions={"a": (0, 0, 100), "b": (0, 50, 60)})

    def test_adjacent_regions_ok_and_find_region(self):
        adm = UnrollAdmission(op_name="add",
                              tensor_regions={"a": (0, 0, 100), "b": (0, 100, 50)})
        self.assertEqual(adm.find_region(0, 99), ("a", 0, 0, 100))
        self.assertEqual(adm.find_region(0, 100), ("b", 0, 100, 50))
        self.assertIsNone(adm.find_region(0, 150))   # 右端点开区间
        self.assertIsNone(adm.find_region(1, 0))     # bank 不同


# ---------------------------------------------------------------- L2 八闸门（assembler 层）

class TestL2AssemblerGates(unittest.TestCase):
    """assemble(admission=...) 各闸门逐项变红；措辞 regex 与实现消息同源锁定。"""

    @classmethod
    def setUpClass(cls):
        cls.spec = HardwareSpec.load("hw_config")

    def _adm(self, regions=None):
        return UnrollAdmission(op_name="add",
                               tensor_regions=regions or {"x": (0, 0, 256)})

    def test_gate_gop_out_of_scope(self):
        """G-op（§B.2-1/§A.2-2）：LOGIC ∈ 计算族但 ∉ {FPMUL,FPADD} → 双标签拒绝。
        流级闸门（assemble 开头一次判定），metadata 无需任何调度字段。"""
        with self.assertRaisesRegex(ValueError, "BASE_OP_OUT_OF_SCOPE"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeLIROp(op_type="LOGIC", unit_id=None)],
                II=2, num_iterations=1, admission=self._adm())

    def test_gate_gstage_rejected(self):
        """G-stage（§B.2-7）：虚设防御闸——注入 stage_id=1 病理必须变红
        （src 现无 stage_id 生产者；一旦出现即在流级响拒，指向 A2a 非目标）。"""
        with self.assertRaisesRegex(ValueError, "G-stage"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeLIROp(op_type="FPMUL", unit_id=None, stage_id=1)],
                II=2, num_iterations=1, admission=self._adm())

    def test_gate_gdp_rejected(self):
        """G-dp（§B.2-8）：SMI_DS_T datapath IQ（smi_ds_t_gather 标记）越出 V1 包络。"""
        with self.assertRaisesRegex(ValueError, "G-dp"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeLIROp(smi_ds_t_gather=True)],
                II=2, num_iterations=2, admission=self._adm())

    def test_gate_gii_approach_multislot_collision(self):
        """G-II 逼近路径①：II=1 多 op 必撞 D1 槽冲突（先于 G-II）——global_t%1 恒 0。"""
        with self.assertRaisesRegex(ValueError, "槽冲突"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeLIROp("tA", 0, 1), _FakeLIROp("tB", 1, 1)],
                II=1, num_iterations=2, admission=self._adm())

    def test_gate_gii_approach_single_slot_flat(self):
        """G-II 逼近路径②：II=1 单槽 + admission → ADR-054 平坦分支（合法分流，
        不走展开也不触 G-II；mul 已证路径）。branch_report 自证选中分支。"""
        a = PhysicalAssembler(self.spec)
        a.assemble([_FakeLIROp("tA", 0, 1)], II=1, num_iterations=2,
                   admission=self._adm())
        self.assertEqual(a.branch_report.get("ur-r0"), "flat")

    def test_gate_gaddr_base_miss(self):
        """G-addr（§F.5）例一：基址未命中任何声明区（bank7 无声明）→ 响拒。"""
        with self.assertRaisesRegex(ValueError, "G-addr"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeLIROp("tA", 0, 0, addr=1792)],   # (bank=7, offset=0)
                II=2, num_iterations=2, admission=self._adm())

    def test_gate_gaddr_live_overrun(self):
        """G-addr（§F.5）例二：LIVE 未取模地址越出声明区——length=4、stride=1、
        N_eff=10 → j_logical=4 时 unwrapped=4 ∉ [0,4)。mod 不作越界豁免。"""
        with self.assertRaisesRegex(ValueError, "越出声明区"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeLIROp("tA", 0, 1, addr=0)],
                II=2, num_iterations=10,
                admission=self._adm(regions={"x": (0, 0, 4)}))

    def test_no_request_zero_behavior_change(self):
        """§B.3 兼容语义：admission=None（未请求）不启用展开分支与任何 L2 闸——
        m19 形态（步进 1 vs 2）无 admission 时走 embed 主线、无「步长不一致」闸。"""
        a = PhysicalAssembler(self.spec)
        a.assemble([_FakeLIROp("tA", 0, 1), _FakeLIROp("tB", 1, 2)],
                   II=2, num_iterations=2)
        self.assertEqual(a.branch_report.get("ur-r0"), "embed")


# ---------------------------------------------------------------- runner 接线 + _build_admission

class TestRunnerAdmission(unittest.TestCase):
    """pipeline_runner 层：请求物化（_build_admission）与 M3 形态接线闸。"""

    @classmethod
    def setUpClass(cls):
        cls.runner = PipelineRunner(show_review=False)

    class _FakeOperator:
        """最小算子替身：name/experimental_unroll/element_bits/dtype/tensor_configs
        + 空 DFG；parallel_dim=None → 单 SMC 路径。"""

        def __init__(self, name="real_add", flag=True, tensor_configs=None):
            self.name = name
            self.experimental_unroll = flag
            self.element_bits = 32
            self.parallel_dim = None
            self.dtype = np.float32
            self.tensor_configs = tensor_configs or {
                "x": {"role": "input", "bank": 0, "offset": 0, "length": 256}}

        def get_dfg(self, spec=None):
            return []

    _SOLVE_RET = {"schedule": {}, "II": 2, "unit_assignments": {}}

    def _run_to_gate(self, operator, op_name):
        """mock solver/bypass 直抵接线闸（§B.1 身份一致性检查在 assemble 之前）。"""
        with mock.patch(
                "verification_v2.infra.pipeline_runner.UCodeSynthesizer") as ms, \
             mock.patch("frontend.rtovr_bypass.apply_rtovr_sparse_bypass",
                        side_effect=lambda ops, spec: ops):
            ms.return_value.solve_with_units.return_value = self._SOLVE_RET
            return self.runner.run_full_stack(
                operator, {"x": np.zeros(4, dtype=np.float32)}, op_name=op_name)

    def test_label_mismatch_rejected(self):
        """接线闸（ADR-055 M3 的 ADR-056 形态）：本体声明 flag 但调用标签规范化后
        与本体身份不符 → 响亮失败，不得静默回落 embed。"""
        op = self._FakeOperator(name="real_add")
        with self.assertRaisesRegex(ValueError, "与本体身份不符"):
            self._run_to_gate(op, op_name="wrong_label")

    def test_label_match_passes_gate(self):
        """对照组：标签与本体一致 → 过接线闸（后续在 fake 算子未实现的
        inject_data 处自然失败——证明变红点不在接线闸）。"""
        op = self._FakeOperator(name="real_add")
        try:
            self._run_to_gate(op, op_name="real_add")
        except Exception as e:  # noqa: BLE001 - 断言的是「非接线闸措辞」
            self.assertNotIn("与本体身份不符", str(e))
        # hw_parity 前缀剥除后 identity 一致 → 同样过闸
        try:
            self._run_to_gate(op, op_name="hw_parity_real_add")
        except Exception as e:  # noqa: BLE001
            self.assertNotIn("与本体身份不符", str(e))

    # ---- _build_admission（§B.4 触点 3：L1 请求 → L2 凭据物化） ----

    def test_build_admission_indivisible_len_rejected(self):
        """§A.2-5：per_smc_len 不整除 vector_lanes（尾批布局 V1 不支持）。"""
        with self.assertRaisesRegex(ValueError, "不整除"):
            self.runner._build_admission(
                self._FakeOperator(), "add",
                num_loop_iters=2, per_smc_len=10, vector_lanes=4)

    def test_build_admission_bank_list_rejected(self):
        """多 bank（list）布局属 A2a 非目标——V1 要求标量 int bank。"""
        op = self._FakeOperator(tensor_configs={
            "x": {"role": "input", "bank": [0, 1], "offset": 0, "length": 256}})
        with self.assertRaisesRegex(ValueError, "标量 int bank"):
            self.runner._build_admission(op, "add",
                                         num_loop_iters=2, per_smc_len=8,
                                         vector_lanes=4)

    def test_build_admission_length_shortfall_rejected(self):
        """§F.5 静态容量：input stride=1，N_eff=10 消费 10 words > length=5 → 拒。"""
        op = self._FakeOperator(tensor_configs={
            "x": {"role": "input", "bank": 0, "offset": 0, "length": 5}})
        with self.assertRaisesRegex(ValueError, "静态消耗"):
            self.runner._build_admission(op, "add",
                                         num_loop_iters=10, per_smc_len=40,
                                         vector_lanes=4)

    def test_build_admission_happy_path(self):
        """add 形态五张量：stride=0 驻留（alpha/constants）每标量耗 1 word、
        stride=1（x/other/y）N_eff 消费——均在 length=256 内 → 物化成功。"""
        op = self._FakeOperator(tensor_configs={
            "x":         {"role": "input",    "bank": 0, "offset": 0, "length": 256},
            "other":     {"role": "input",    "bank": 1, "offset": 0, "length": 256},
            "alpha":     {"role": "param",    "bank": 3, "offset": 0, "length": 256},
            "constants": {"role": "constant", "bank": 7, "offset": 0, "length": 256},
            "y":         {"role": "output",   "bank": 2, "offset": 0, "length": 256},
        })
        adm = self.runner._build_admission(op, "add", num_loop_iters=15,
                                           per_smc_len=60, vector_lanes=4)
        self.assertIsInstance(adm, UnrollAdmission)
        self.assertEqual(adm.op_name, "add")
        self.assertEqual(adm.tensor_regions["alpha"], (3, 0, 256))
        self.assertEqual(adm.tensor_regions["y"], (2, 0, 256))


# ---------------------------------------------------------------- 存量 YAML 语料契约

class TestYamlCorpusCompat(unittest.TestCase):
    """ADR-056 §B.3 兼容面直接证明：键 allowlist / 文本闸对存量 YAML 零冲突；
    且全语料声明 experimental_unroll=true 的算子被**显式名单**锁定。

    名单沿革：步③时恰 add 一份（触点 5 唯一迁移对象）；步④-3 增 add_v1
    （V1 准入证据用例：同公式同布局、test_data 声明域收紧避开 SUBNORMAL_BAND，
    供 v1_admission 双道全过——与 add 的 frozen_regression 回归通道严格分列，
    见 run_v1_verify.py 发布规则）；步⑤ 增 scale_sum（V1 验收对象 y=alpha*(x+other)，
    配置-only 新增）；2026-10-12 复杂结构首证步② 增 horner_v3u（Horner 6 级链
    II=6 等价目的新配置，horner_v3 原样守护 census 快照；docs/
    horner_v3_static_confirmation_20261012.md §5）。**本测试是语料快照守卫而非名称许可**：名单随新配置算子更新，
    准入与否由 L2 结构闸门判定（见 test_run_v1_verify 更名用例证明任意合法名
    不影响准入）。任何未登记的 flag 声明仍会令本测试失败。"""

    def test_yaml_corpus_zero_conflict_and_single_flag(self):
        paths = sorted(OPS_DIR.glob("*.yaml"))
        # scale_sum.yaml 自 2026-09-26 预检期已在语料（当时无 flag），步⑤ 仅补 flag+length；
        # 2026-10-12 步② 新增 horner_v3u.yaml（语料 38→39）。
        self.assertEqual(len(paths), 39,
                         f"ops/*.yaml 语料规模漂移（键并集取证的基数前提）: {len(paths)}")
        flagged = []
        for p in paths:
            op = ConfigurableOperator(str(p))   # 任何一份构造失败即 RED
            if op.experimental_unroll:
                flagged.append(op.name)
        self.assertEqual(flagged, ["add", "add_v1", "horner_v3u", "scale_sum"],
                         f"声明 experimental_unroll 的算子应恰为 "
                         f"[add, add_v1, horner_v3u, scale_sum]: {flagged}")


if __name__ == "__main__":
    unittest.main()
