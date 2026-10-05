"""ADR-057 D3：多段程序 manifest（生成 + 封印）。

manifest 是**生成产物，不是独立真值**（ADR-057 D3 原文）——真值由
``verification_v2/tools/check_stage_manifest.py`` 从原始任务（op 配置 + n +
solver 调度）独立重算后对照。本模块只负责：按 schema 组装、sha256 封印、验封。

封印纪律：
  * ``seal = sha256(canonical_json(body))``；``generated_utc`` 等运行期元数据
    留在封印区外，保证同输入 → 同 body → 同 seal（确定性可复算）。
  * 篡改检测双层：未重封印 → 验封即 RED；重新计算哈希后的语义篡改 →
    检查器独立重算对照 RED（两层缺一不可，D3 明文要求测试覆盖两者）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "adr057_stage_manifest_v1"

# D4 完成契约标识：TB multi_stage 顺序装载各段 → 触发 → 等待中断并应答清零
# → 下一段 BEGIN（段间次序/无残留写由仲裁器在公共 probe cycle 轴上裁决）。
COMPLETION_CONTRACT_ID = "tb_multi_stage_intr_ack_v1"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def canonical_digest(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def hw_config_digest(hw_config_dir: Path) -> Dict[str, Any]:
    """硬件配置哈希：目录内全部 yaml 的 {相对路径: sha256} + 组合摘要。"""
    files = sorted(Path(hw_config_dir).rglob("*.yaml"))
    if not files:
        raise FileNotFoundError(f"hw_config 目录无 yaml: {hw_config_dir}")
    per_file = {str(f.relative_to(hw_config_dir)): sha256_file(f) for f in files}
    return {"dir": str(hw_config_dir), "files": per_file,
            "digest": canonical_digest(per_file)}


def _iq_file_entries(stage_dir: Path) -> Dict[str, Dict[str, Any]]:
    """每段 IQ 文件哈希 + 实际行数（一指令一行，与 ADR-055 D3 口径一致）。"""
    out: Dict[str, Dict[str, Any]] = {}
    iq_dir = Path(stage_dir) / "smc_0"
    for f in sorted(iq_dir.glob("iq_*.hex")):
        rows = sum(1 for ln in f.read_text().splitlines() if ln.strip())
        out[f.name] = {"path": str(f), "sha256": sha256_file(f), "rows": rows}
    if not out:
        raise FileNotFoundError(f"{iq_dir} 下无 iq_*.hex——段产物缺失，拒绝生成 manifest")
    return out


def build_manifest(*, op_name: str, op_cfg: Dict[str, Any],
                   ast_canonical: Dict[str, Any],
                   schedule_json: Path, hw_config_dir: Path,
                   registry: Any, plan: Any,
                   stage_dirs: List[Path],
                   tensor_cfgs: Dict[str, Dict[str, Any]],
                   n: int, lanes: int,
                   generated_utc: Optional[str] = None) -> Dict[str, Any]:
    """组装并封印 manifest。plan = stage_partition.StagePlan。

    地址偏移一律 **word** 单位（UR 128-bit 词；D3 要求单位明确）。
    """
    outputs = [(name, c) for name, c in tensor_cfgs.items() if c.get("role") == "output"]
    if len(outputs) != 1:
        raise ValueError(f"输出张量数 {len(outputs)} ≠ 1（ADR-057 范围：单输出逐元素）")
    out_name, out_cfg = outputs[0]
    out_bank = int(out_cfg["bank"])
    out_off = int(out_cfg.get("offset", 0))

    def tensor_offsets(s: int) -> Dict[str, Dict[str, int]]:
        per_t: Dict[str, Dict[str, int]] = {}
        for tname, tcfg in tensor_cfgs.items():
            stride = int(tcfg.get("stride", 1 if tcfg.get("role") in ("input", "output") else 0))
            per_t[tname] = {
                "bank": int(tcfg["bank"]),
                "base_offset_words": int(tcfg.get("offset", 0)),
                "stride_words_per_iter": stride,
                "stage_base_word": int(tcfg.get("offset", 0)) + s * stride,
            }
        return per_t

    stages_doc: List[Dict[str, Any]] = []
    for st, sdir in zip(plan.stages, stage_dirs):
        stages_doc.append({
            "stage_id": st.stage_id,
            "kind": st.kind,
            "predecessor": st.predecessor,
            "iter_range": [st.start, st.end],          # 半开区间
            "stage_start": st.start,
            "m": st.m,
            "address_offsets": tensor_offsets(st.start),
            "address_unit": "word",
            "active_smc": [0],
            "iq_files": _iq_file_entries(sdir),
            "expected_write_region": {
                "tensor": out_name, "bank": out_bank,
                "addr_range_words": [out_off + st.start, out_off + st.end],
                "unit": "word",
            },
            "completion_contract_id": COMPLETION_CONTRACT_ID,
        })

    body = {
        "schema_version": SCHEMA,
        "op": op_name,
        "n": int(n), "lanes": int(lanes),
        "n_eff": plan.n_eff,
        "total_iterations": plan.n_eff,
        "ii": plan.ii,
        "iq_capacity": plan.capacity,
        "m_max": plan.m_max,
        "queue_spans": {q: int(v) for q, v in sorted(plan.spans.items())},
        "hashes": {
            "op_config": canonical_digest(op_cfg),
            "ordered_ast": canonical_digest(ast_canonical),
            "schedule": sha256_file(schedule_json),
            "hw_config": hw_config_digest(hw_config_dir),
            "protocol_registry": {
                "path": str(registry.path), "version": registry.version,
                "records_sha256": registry.records_digest,
            },
        },
        "stages": stages_doc,
    }
    doc: Dict[str, Any] = {"schema": SCHEMA, "body": body,
                           "seal": canonical_digest(body)}
    if generated_utc is not None:
        doc["generated_utc"] = generated_utc   # 封印区外
    return doc


def verify_seal(doc: Dict[str, Any]) -> bool:
    """验封：seal == sha256(canonical(body))。字段缺失/不符 → False（不抛）。"""
    try:
        return (isinstance(doc.get("body"), dict)
                and doc.get("seal") == canonical_digest(doc["body"]))
    except Exception:
        return False


def write_manifest(doc: Dict[str, Any], path: Path) -> None:
    Path(path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")


def ast_to_canonical(root: Any) -> Dict[str, Any]:
    """有序 AST → 可序列化 canonical dict（供 hashes.ordered_ast）。"""
    return asdict(root)
