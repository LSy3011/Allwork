#!/usr/bin/env python3
"""ADR-057 D3 独立检查器：多段程序 manifest 的对照真值。

manifest（``stage_manifest.json``）是**生成产物，不是独立真值**。本检查器从
**原始任务**（op YAML + n/lanes）与 **solver 调度**（compiler_schedule.json，
其 sha256 被 manifest 哈希区钉死）独立重算以下三类声明，再与 manifest 逐字段对照：

  1. 分段计划——span_q / m_max / 段序列（start, m）由 ``plan_compute_stages``
     在调度副本上重算；段区间须**精确平铺** [0, n_eff)（漏段/重复段/偏移一格
     在此层即 RED，无需等到 VCS）。
  2. 地址区——每段每张量 stage_base_word = offset + stage_start·stride，
     stride 缺省规则 input/output=1、其余=0（读自 op YAML，不回读 manifest）；
     输出写区 [out_off+start, out_off+end)（word 单位，半开）。
  3. IQ 文件——逐段重数行数、重算 sha256；行数 ≤ 容量墙（SMI_IQ_INSTQ_DEPTH）
     且 == L_q(m)=II·(m+span_q)+2（公式↔实算互校，ADR-055 D3 同族）。

篡改模型（D3 明文要求两层都测）：
  * 未重封印 → 验封即 RED（第一层）；
  * 重新计算 seal 的语义篡改 → 本检查器独立重算对照 RED（第二层）。
两层缺一不可：仅有封印防不住「改完重封」，仅有对照防不住「产物与声明一起换」。

CLI：
    python verification_v2/tools/check_stage_manifest.py <build_dir> [--ops-dir DIR]
退出码 0 = GREEN（全部对照一致）；1 = RED（逐条打印违例）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

from driver.assembler import SMI_IQ_INSTQ_DEPTH  # noqa: E402  容量墙唯一来源
from driver.stage_partition import (  # noqa: E402
    PartitionRefused, plan_compute_stages)
from verification_v2.infra import stage_manifest as _smod  # noqa: E402
from verification_v2.infra.protocol_registry import (  # noqa: E402
    load_default as _load_registry)
from verification_v2.reference.ordered_ast import (  # noqa: E402
    parse_ordered_formula)

Finding = Tuple[str, str]


def _red(findings: List[Finding], code: str, msg: str) -> None:
    findings.append((code, msg))
    print(f"[D3-{code}] RED: {msg}")


def _ok(code: str, msg: str) -> None:
    print(f"[D3-{code}] ok: {msg}")


def _load_manifest(build: Path) -> Dict[str, Any]:
    p = build / "stage_manifest.json"
    if not p.exists():
        raise FileNotFoundError(f"缺 manifest: {p}（单段构建不属本检查器范围）")
    return json.loads(p.read_text(encoding="utf-8"))


def _sched_ops(schedule: Dict[str, Any]) -> List[Any]:
    """compiler_schedule.json → 分段器输入（与构建期 emit_ops 同口径：
    剔除 structural_only；仅取 unit_id/global_t 两字段，其余不回读）。"""
    ops = []
    for tid, rec in schedule["ops"].items():
        if rec.get("structural_only"):
            continue
        ops.append(SimpleNamespace(
            trace_id=tid,
            metadata={"unit_id": rec["iq"], "global_t": rec["t"]},
        ))
    return ops


def _stride_of(tcfg: Dict[str, Any]) -> int:
    return int(tcfg.get("stride",
                        1 if tcfg.get("role") in ("input", "output") else 0))


def check_manifest(build_dir: Any, ops_dir: Optional[Any] = None) -> List[Finding]:
    """对照入口。返回违例列表（空 = GREEN）；逐条打印。"""
    build = Path(build_dir)
    findings: List[Finding] = []
    doc = _load_manifest(build)

    # ---- 第一层：验封（篡改未重封印即此层 RED） --------------------------------
    if doc.get("schema") != _smod.SCHEMA:
        _red(findings, "SCHEMA",
             f"schema={doc.get('schema')!r} != {_smod.SCHEMA!r}")
        return findings
    if not _smod.verify_seal(doc):
        _red(findings, "SEAL",
             "seal != sha256(canonical(body))——封印破坏（未重封印的篡改）")
        return findings
    _ok("SEAL", "封印完整（第一层）")

    body = doc["body"]
    if body.get("schema_version") != _smod.SCHEMA:
        _red(findings, "SCHEMA",
             f"body.schema_version={body.get('schema_version')!r} 不符")

    # ---- 原始任务侧输入：op YAML ---------------------------------------------
    ops_root = Path(ops_dir) if ops_dir else _REPO / "verification_v2" / "ops"
    op_yaml = ops_root / f"{body.get('op')}.yaml"
    if not op_yaml.exists():
        _red(findings, "TASK", f"op YAML 不存在: {op_yaml}（op={body.get('op')!r}）")
        return findings
    op_cfg = yaml.safe_load(op_yaml.read_text(encoding="utf-8"))
    tensor_cfgs: Dict[str, Dict[str, Any]] = op_cfg["tensors"]

    # ---- 哈希区对照（产物指纹 ↔ 现场重算） ------------------------------------
    hashes = body.get("hashes", {})
    if _smod.canonical_digest(op_cfg) != hashes.get("op_config"):
        _red(findings, "HASH-OPCFG",
             "op_config 哈希不符——op YAML 与构建时不一致")
    t_names = [k for k, c in tensor_cfgs.items()
               if c.get("role") in ("input", "param")]
    ast_doc = _smod.ast_to_canonical(
        parse_ordered_formula(op_cfg["formula"], t_names))
    if _smod.canonical_digest(ast_doc) != hashes.get("ordered_ast"):
        _red(findings, "HASH-AST", "ordered_ast 哈希不符——公式/张量名与构建时不一致")

    sched_path = build / "compiler_schedule.json"
    if not sched_path.exists():
        _red(findings, "HASH-SCHED", f"缺 {sched_path}")
        return findings
    if _smod.sha256_file(sched_path) != hashes.get("schedule"):
        _red(findings, "HASH-SCHED",
             "compiler_schedule.json 哈希不符——调度产物被改或非本次构建")
        return findings
    schedule = json.loads(sched_path.read_text(encoding="utf-8"))

    hw_claim = hashes.get("hw_config", {})
    try:
        hw_now = _smod.hw_config_digest(Path(hw_claim.get("dir", "")))
        if hw_now["digest"] != hw_claim.get("digest"):
            _red(findings, "HASH-HW", "hw_config 组合摘要不符——硬件配置漂移")
    except Exception as e:
        _red(findings, "HASH-HW", f"hw_config 重算失败: {e}")

    reg = _load_registry()
    reg_claim = hashes.get("protocol_registry", {})
    if not (str(reg.path) == reg_claim.get("path")
            and reg.version == reg_claim.get("version")
            and reg.records_digest == reg_claim.get("records_sha256")):
        _red(findings, "HASH-REG",
             f"协议注册表不符：现场 v{reg.version}/{reg.records_digest[:12]}… vs "
             f"声明 {reg_claim.get('version')}/{str(reg_claim.get('records_sha256'))[:12]}…")
    if not any(c.startswith("HASH-") for c, _ in findings):
        _ok("HASH", "op_config/ordered_ast/schedule/hw_config/registry 五指纹一致")

    # ---- 第二层：分段计划独立重算 ---------------------------------------------
    n, lanes, n_eff = int(body["n"]), int(body["lanes"]), int(body["n_eff"])
    if n != n_eff * lanes:
        _red(findings, "TASK", f"n={n} != n_eff·lanes={n_eff}·{lanes}")
    if int(body["ii"]) != int(schedule["II"]):
        _red(findings, "PLAN",
             f"body.ii={body['ii']} != 调度 II={schedule['II']}")
    if int(body["iq_capacity"]) != SMI_IQ_INSTQ_DEPTH:
        _red(findings, "PLAN",
             f"iq_capacity={body['iq_capacity']} != 容量墙唯一来源 "
             f"SMI_IQ_INSTQ_DEPTH={SMI_IQ_INSTQ_DEPTH}（smi_top.v:22）")

    plan = None
    try:
        plan = plan_compute_stages(_sched_ops(schedule), ii=int(schedule["II"]),
                                   n_eff=n_eff)
    except PartitionRefused as pr:
        _red(findings, "PLAN", f"独立重算被拒（PartitionRefused）: {pr}")
    if plan is not None:
        if int(body["m_max"]) != plan.m_max:
            _red(findings, "PLAN", f"m_max: manifest={body['m_max']} vs 重算={plan.m_max}")
        if {k: int(v) for k, v in body["queue_spans"].items()} != plan.spans:
            _red(findings, "PLAN",
                 f"queue_spans 不符: manifest={body['queue_spans']} vs 重算={plan.spans}")
        mstages = body.get("stages", [])
        if len(mstages) != len(plan.stages):
            _red(findings, "COVER",
                 f"段数 manifest={len(mstages)} != 重算={len(plan.stages)}（漏段/增段）")
        for k, (ms, ps) in enumerate(zip(mstages, plan.stages)):
            if (ms.get("stage_id"), ms.get("stage_start"), ms.get("m"),
                    ms.get("predecessor"), ms.get("kind")) != (
                    ps.stage_id, ps.start, ps.m, ps.predecessor, ps.kind):
                _red(findings, "COVER",
                     f"stage_{k} 计划不符: manifest="
                     f"({ms.get('stage_id')},{ms.get('stage_start')},{ms.get('m')},"
                     f"{ms.get('predecessor')},{ms.get('kind')}) vs 重算="
                     f"({ps.stage_id},{ps.start},{ps.m},{ps.predecessor},{ps.kind})"
                     f"——偏移/重排/漏段")
            if list(ms.get("iter_range", [])) != [ps.start, ps.end]:
                _red(findings, "COVER",
                     f"stage_{k} iter_range={ms.get('iter_range')} != "
                     f"[{ps.start},{ps.end})")
        # 覆盖断言（独立于逐段相等再显式断言一次：半开区间精确平铺 [0, n_eff)）
        covered: List[int] = []
        for ms in mstages:
            lo, hi = ms.get("iter_range", [None, None])
            if isinstance(lo, int) and isinstance(hi, int):
                covered.extend(range(lo, hi))
        if covered != list(range(n_eff)):
            _red(findings, "COVER",
                 f"段区间未精确平铺 [0,{n_eff})：覆盖 {len(covered)} 格"
                 f"（漏段/重复段/空洞）")
        elif not any(c == "COVER" for c, _ in findings):
            _ok("COVER", f"{len(plan.stages)} 段精确平铺 [0,{n_eff})，"
                         f"m_max={plan.m_max}")

    # ---- 地址区独立重算（op YAML 直读，不回读 manifest） -----------------------
    outputs = [(nm, c) for nm, c in tensor_cfgs.items() if c.get("role") == "output"]
    if len(outputs) != 1:
        _red(findings, "ADDR", f"op YAML 输出张量数 {len(outputs)} != 1（超 ADR-057 范围）")
        outputs = outputs[:1] or [(None, {})]
    out_name, out_cfg = outputs[0]
    out_stride = _stride_of(out_cfg)
    if out_stride != 1:
        _red(findings, "ADDR", f"输出 {out_name} stride={out_stride} != 1——"
                               f"写区公式仅支持逐元素连续布局（超 ADR-057 范围）")
    for k, ms in enumerate(body.get("stages", [])):
        s = int(ms.get("stage_start", 0))
        m = int(ms.get("m", 0))
        claimed = ms.get("address_offsets", {})
        for tname, tcfg in tensor_cfgs.items():
            want = {
                "bank": int(tcfg["bank"]),
                "base_offset_words": int(tcfg.get("offset", 0)),
                "stride_words_per_iter": _stride_of(tcfg),
                "stage_base_word": int(tcfg.get("offset", 0)) + s * _stride_of(tcfg),
            }
            if claimed.get(tname) != want:
                _red(findings, "ADDR",
                     f"stage_{k} 张量 {tname} 地址不符: manifest={claimed.get(tname)}"
                     f" vs 重算={want}")
        if ms.get("address_unit") != "word":
            _red(findings, "ADDR", f"stage_{k} address_unit={ms.get('address_unit')!r} != 'word'")
        wr = ms.get("expected_write_region", {})
        want_wr = {"tensor": out_name, "bank": int(out_cfg.get("bank", -1)),
                   "addr_range_words": [int(out_cfg.get("offset", 0)) + s,
                                        int(out_cfg.get("offset", 0)) + s + m],
                   "unit": "word"}
        if wr != want_wr:
            _red(findings, "ADDR",
                 f"stage_{k} 写区不符: manifest={wr} vs 重算={want_wr}（错误写区）")
        if ms.get("completion_contract_id") != _smod.COMPLETION_CONTRACT_ID:
            _red(findings, "ADDR",
                 f"stage_{k} completion_contract_id={ms.get('completion_contract_id')!r}"
                 f" != {_smod.COMPLETION_CONTRACT_ID!r}")
    if not any(c == "ADDR" for c, _ in findings):
        _ok("ADDR", "逐段地址偏移/写区/完成契约一致（word 单位）")

    # ---- IQ 文件对照（行数 + sha256 + 容量墙 + 公式互校） ----------------------
    for k, ms in enumerate(body.get("stages", [])):
        sdir = build / "stages" / f"stage_{ms.get('stage_id', k)}" / "smc_0"
        on_disk = {f.name: f for f in sorted(sdir.glob("iq_*.hex"))} if sdir.is_dir() else {}
        claimed_files = ms.get("iq_files", {})
        if set(on_disk) != set(claimed_files):
            _red(findings, "IQ-SET",
                 f"stage_{k} IQ 文件集不符: 磁盘={sorted(on_disk)} vs "
                 f"manifest={sorted(claimed_files)}（漏文件/加文件）")
        m = int(ms.get("m", 0))
        for fname, f in on_disk.items():
            ent = claimed_files.get(fname)
            if ent is None:
                continue
            rows = sum(1 for ln in f.read_text().splitlines() if ln.strip())
            if rows != int(ent.get("rows", -1)):
                _red(findings, "IQ-ROWS",
                     f"stage_{k} {fname} 行数 manifest={ent.get('rows')} vs 实数={rows}")
            if rows > SMI_IQ_INSTQ_DEPTH:
                _red(findings, "IQ-CAP",
                     f"stage_{k} {fname} 行数 {rows} > 容量墙 {SMI_IQ_INSTQ_DEPTH}")
            if _smod.sha256_file(f) != ent.get("sha256"):
                _red(findings, "IQ-SHA",
                     f"stage_{k} {fname} sha256 不符——文件被改或张冠李戴")
            rec_path = str(ent.get("path", ""))
            if not rec_path.endswith(f"stages/stage_{ms.get('stage_id', k)}/smc_0/{fname}"):
                _red(findings, "IQ-PATH",
                     f"stage_{k} {fname} 记录路径 {rec_path!r} 不指向本段目录")
            if plan is not None:
                q = fname[len("iq_"):-len(".hex")]
                if q not in plan.spans:
                    _red(findings, "IQ-FORMULA",
                         f"stage_{k} {fname} 队列 {q!r} 不在调度 span 集合 {sorted(plan.spans)}")
                else:
                    want_rows = plan.predicted_rows(q, m)
                    if rows != want_rows:
                        _red(findings, "IQ-FORMULA",
                             f"stage_{k} {fname} 实数 {rows} != L_q(m)="
                             f"{plan.ii}·({m}+{plan.spans[q]})+2={want_rows}"
                             f"（公式↔实算漂移）")
    if not any(c.startswith("IQ-") for c, _ in findings):
        _ok("IQ", "逐段 IQ 文件集/行数/sha256/容量/公式互校一致")

    return findings


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ADR-057 D3 多段 manifest 独立检查器")
    ap.add_argument("build_dir", help="构建产物目录（含 stage_manifest.json）")
    ap.add_argument("--ops-dir", default=None,
                    help="op YAML 目录（缺省 verification_v2/ops/）")
    args = ap.parse_args(argv)

    findings = check_manifest(args.build_dir, args.ops_dir)
    if findings:
        print(f"\n[VERDICT] RED —— {len(findings)} 项违例（manifest 不予采信）")
        return 1
    print("\n[VERDICT] GREEN —— manifest 与独立重算全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
