"""ADR-057 D3 检查器测试：GREEN 基线 + 篡改对抗（双层）+ 布局对抗。

D3 明文要求的两层篡改模型各至少一测：
  * 未重封印的篡改 → 验封 RED（test_tamper_without_reseal）；
  * 重新计算 seal 的语义篡改 → 独立重算对照 RED（test_tamper_resealed 等）。
布局对抗：漏段 / 重复段 / 偏移一格 / 错误写区 / IQ 行数漂移，各须 RED。

夹具为合成最小多段构建（scale_sum 形态：II=2、spans {rtovr-14:3, ur-r0:2,
vout0:0} → m_max=60，n_eff=61 → 段 (0,60)+(60,1)）；manifest 由真生成器
stage_manifest.build_manifest 产出——检查器的独立性体现在重算对照，而非另写
一份生成器。
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from driver.stage_partition import plan_compute_stages
from verification_v2.infra import stage_manifest as _smod
from verification_v2.infra.protocol_registry import load_default as _load_reg
from verification_v2.reference.ordered_ast import parse_ordered_formula
from verification_v2.tools.check_stage_manifest import check_manifest

_REPO = Path(__file__).resolve().parents[2]

# scale_sum 形态锚点（与 test_stage_partition 同族）：II=2
_OPS = [
    ("ur-r0", 0), ("ur-r0", 4),      # span 2
    ("rtovr-14", 2), ("rtovr-14", 8),  # span 3（束缚队列：m_max = 63-3 = 60）
    ("vout0", 10),                   # span 0
]
_II = 2
_N_EFF = 61          # > m_max=60 → 两段 (0,60)+(60,1)
_LANES = 4
_N = _N_EFF * _LANES  # 244

_OP_YAML = {
    "name": "fake_ck",
    "formula": "alpha * (x + other)",
    "experimental_unroll": True,
    "tensors": {
        "x": {"bank": 0, "role": "input", "offset": 0, "length": 256},
        "other": {"bank": 1, "role": "input", "offset": 0, "length": 256},
        "alpha": {"bank": 3, "role": "param", "offset": 0, "length": 256},
        "y": {"bank": 2, "role": "output", "offset": 0, "length": 256},
    },
}


def _sched_ops():
    return [SimpleNamespace(trace_id=f"op{i}", metadata={"unit_id": q, "global_t": t})
            for i, (q, t) in enumerate(_OPS)]


def _write_iq(dirpath: Path, queue: str, rows: int) -> None:
    (dirpath / f"iq_{queue}.hex").write_text(
        "".join(f"{i:032x}\n" for i in range(rows)), encoding="utf-8")


@pytest.fixture()
def case(tmp_path):
    """合成最小多段构建目录 + 配套 op YAML；返回 (build, ops_dir, manifest_path)。"""
    build = tmp_path / "build" / "fake_ck"
    ops_dir = tmp_path / "ops"
    ops_dir.mkdir(parents=True)
    (ops_dir / "fake_ck.yaml").write_text(yaml.safe_dump(_OP_YAML), encoding="utf-8")
    build.mkdir(parents=True)

    schedule = {"II": _II, "ops": {
        f"op{i}": {"t": t, "iq": q, "stride": 1, "logical_addr": 0,
                   "dly": 0, "op_type": "UR_READ", "structural_only": False}
        for i, (q, t) in enumerate(_OPS)}}
    sched_path = build / "compiler_schedule.json"
    sched_path.write_text(json.dumps(schedule, indent=2), encoding="utf-8")

    plan = plan_compute_stages(_sched_ops(), ii=_II, n_eff=_N_EFF)
    assert [(s.start, s.m) for s in plan.stages] == [(0, 60), (60, 1)]

    stage_dirs = []
    queues = ["rtovr-14", "ur-r0", "vout0"]
    for st in plan.stages:
        sdir = build / "stages" / f"stage_{st.stage_id}" / "smc_0"
        sdir.mkdir(parents=True)
        for q in queues:
            _write_iq(sdir, q, plan.predicted_rows(q, st.m))
        stage_dirs.append(sdir.parent)

    op_cfg = yaml.safe_load((ops_dir / "fake_ck.yaml").read_text(encoding="utf-8"))
    t_names = [k for k, c in op_cfg["tensors"].items()
               if c.get("role") in ("input", "param")]
    doc = _smod.build_manifest(
        op_name="fake_ck", op_cfg=op_cfg,
        ast_canonical=_smod.ast_to_canonical(
            parse_ordered_formula(op_cfg["formula"], t_names)),
        schedule_json=sched_path,
        hw_config_dir=_REPO / "hw_config",
        registry=_load_reg(), plan=plan, stage_dirs=stage_dirs,
        tensor_cfgs=op_cfg["tensors"], n=_N, lanes=_LANES)
    mpath = build / "stage_manifest.json"
    _smod.write_manifest(doc, mpath)
    return build, ops_dir, mpath


def _reseal(doc):
    """篡改者第二层手段：改完重算封印（封印本身合法，语义已被替换）。"""
    doc["seal"] = _smod.canonical_digest(doc["body"])
    return doc


def _rewrite(mpath, fn):
    doc = json.loads(mpath.read_text(encoding="utf-8"))
    fn(doc)
    mpath.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")


def _codes(findings):
    return {c for c, _ in findings}


# ---------------------------------------------------------------- GREEN
def test_green(case):
    build, ops_dir, _ = case
    assert check_manifest(build, ops_dir) == []


# ---------------------------------------------------------------- 篡改双层
def test_tamper_without_reseal(case):
    """第一层：改 body 不重封 → 验封即 RED（且是唯一 RED——验封失败短路）。"""
    build, ops_dir, mpath = case

    def tamper(doc):
        doc["body"]["m_max"] += 1
    _rewrite(mpath, tamper)
    findings = check_manifest(build, ops_dir)
    assert "SEAL" in _codes(findings)
    assert len(findings) == 1


def test_tamper_resealed_mmax(case):
    """第二层：改 m_max 并重算 seal → 封印合法，独立重算对照 RED。"""
    build, ops_dir, mpath = case

    def tamper(doc):
        doc["body"]["m_max"] += 1
        _reseal(doc)
    _rewrite(mpath, tamper)
    findings = check_manifest(build, ops_dir)
    assert "SEAL" not in _codes(findings)   # 封印层被合法绕过
    assert "PLAN" in _codes(findings)


def test_tamper_resealed_span(case):
    build, ops_dir, mpath = case

    def tamper(doc):
        doc["body"]["queue_spans"]["rtovr-14"] -= 1
        _reseal(doc)
    _rewrite(mpath, tamper)
    findings = check_manifest(build, ops_dir)
    assert "PLAN" in _codes(findings)


# ---------------------------------------------------------------- 布局对抗
def test_missing_stage(case):
    """漏段：删掉尾段并重封 → 段数/覆盖 RED。"""
    build, ops_dir, mpath = case

    def tamper(doc):
        doc["body"]["stages"] = doc["body"]["stages"][:1]
        _reseal(doc)
    _rewrite(mpath, tamper)
    assert "COVER" in _codes(check_manifest(build, ops_dir))


def test_duplicate_stage(case):
    """重复段：stage_0 出现两次 → 计划不符 + 平铺破坏 RED。"""
    build, ops_dir, mpath = case

    def tamper(doc):
        doc["body"]["stages"].append(dict(doc["body"]["stages"][0]))
        _reseal(doc)
    _rewrite(mpath, tamper)
    assert "COVER" in _codes(check_manifest(build, ops_dir))


def test_stage_start_off_by_one(case):
    """偏移一格：尾段 stage_start 60→61（iter_range 同步造假 + 重封）→ RED。"""
    build, ops_dir, mpath = case

    def tamper(doc):
        st = doc["body"]["stages"][1]
        st["stage_start"] += 1
        st["iter_range"] = [st["iter_range"][0] + 1, st["iter_range"][1] + 1]
        _reseal(doc)
    _rewrite(mpath, tamper)
    findings = check_manifest(build, ops_dir)
    assert "COVER" in _codes(findings) or "ADDR" in _codes(findings)


def test_wrong_write_region(case):
    """错误写区：输出写区整体平移 1 word + 重封 → ADDR RED。"""
    build, ops_dir, mpath = case

    def tamper(doc):
        wr = doc["body"]["stages"][0]["expected_write_region"]
        wr["addr_range_words"] = [wr["addr_range_words"][0] + 1,
                                  wr["addr_range_words"][1] + 1]
        _reseal(doc)
    _rewrite(mpath, tamper)
    assert "ADDR" in _codes(check_manifest(build, ops_dir))


# ---------------------------------------------------------------- IQ 产物对抗
def test_iq_file_grown_beyond_capacity(case):
    """产物漂移：stage_0 束缚队列文件被加长到 129 行（manifest 不动）
    → 行数/sha/容量/公式多重 RED（封印层无责——产物层漂移）。"""
    build, ops_dir, _ = case
    f = build / "stages" / "stage_0" / "smc_0" / "iq_rtovr-14.hex"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(f"{0xdead:032x}\n")
    codes = _codes(check_manifest(build, ops_dir))
    assert "SEAL" not in codes
    assert codes & {"IQ-ROWS", "IQ-CAP", "IQ-SHA", "IQ-FORMULA"}


def test_iq_rows_claim_tamper_resealed(case):
    """声明层造假：把 stage_1 vout0 行数改小 + 重封 → 实数对照 RED。"""
    build, ops_dir, mpath = case

    def tamper(doc):
        doc["body"]["stages"][1]["iq_files"]["iq_vout0.hex"]["rows"] = 1
        _reseal(doc)
    _rewrite(mpath, tamper)
    assert "IQ-ROWS" in _codes(check_manifest(build, ops_dir))


def test_iq_file_missing_from_manifest(case):
    """漏文件：manifest 抹掉一个队列文件记录 + 重封 → 文件集不符 RED。"""
    build, ops_dir, mpath = case

    def tamper(doc):
        del doc["body"]["stages"][0]["iq_files"]["iq_vout0.hex"]
        _reseal(doc)
    _rewrite(mpath, tamper)
    assert "IQ-SET" in _codes(check_manifest(build, ops_dir))


# ---------------------------------------------------------------- 指纹对抗
def test_schedule_replaced(case):
    """调度产物被替换（manifest 不动）→ schedule 哈希 RED。"""
    build, ops_dir, _ = case
    sched = json.loads((build / "compiler_schedule.json").read_text())
    sched["ops"]["op0"]["t"] = 99
    (build / "compiler_schedule.json").write_text(json.dumps(sched, indent=2))
    assert "HASH-SCHED" in _codes(check_manifest(build, ops_dir))
