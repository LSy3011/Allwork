"""ADR-056 §D.3 协议模型注册表加载器（版本化冻结清单的唯一读取通道）。

铁律（§D.3 用户修约③）：
  * 期望**先**由「调度 + 本注册表延迟模型」生成，再与 trace 比对；
    禁止「从 trace 读出常数当期望」，trace 不符 → PER_BEAT_PROTOCOL_MISMATCH，
    **不得回改常数拟合**（亦不得改 schedule 侧输入拟合——对称规则）。
  * 调用方（emit/check 工具）的 INIT 垫拍、TERM 相位等常数一律经
    `evaluate("INIT_PAD_FORMULA", skip=…, II=…)` 取得——**禁止在工具里重写公式**。

完整性：注册表 yaml 的 `records_sha256` 须等于 records 的 canonical JSON 之 sha256；
改动记录后必须以 `--seal` 重算封存，否则 load 即 RED（防手改漂移）。

CLI：
  python -m verification_v2.infra.protocol_registry --check   # 校验（0=OK）
  python -m verification_v2.infra.protocol_registry --seal    # 重算 sha256 写回
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

_REPO = Path(__file__).resolve().parents[2]

_REQUIRED_FIELDS = {"name", "kind", "value", "provenance", "proven_boundary", "status"}
_VALID_KINDS = {"formula", "assertion", "semantics", "projection"}


def _canonical(records: Any) -> bytes:
    return json.dumps(records, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def records_sha256(records: Any) -> str:
    return hashlib.sha256(_canonical(records)).hexdigest()


class RegistryError(ValueError):
    """注册表自身非法（加载即 RED——测试设施问题不得静默降级为「跳过」）。"""


class ProtocolRegistry:
    """版本化协议模型注册表（只读；改动走 --seal 流程并伴随 ADR 级记录）。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        doc = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise RegistryError(f"{self.path}: 顶层须为 mapping")
        if doc.get("registry") != "protocol_model":
            raise RegistryError(f"{self.path}: registry 字段 != 'protocol_model'")
        self.version = doc.get("version")
        if not isinstance(self.version, int) or self.version < 1:
            raise RegistryError(f"{self.path}: version 须为正 int，实得 {self.version!r}")
        records = doc.get("records")
        if not isinstance(records, list) or not records:
            raise RegistryError(f"{self.path}: records 缺失或非空列表")
        seen = set()
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                raise RegistryError(f"records[{i}] 非 mapping")
            missing = _REQUIRED_FIELDS - set(rec)
            if missing:
                raise RegistryError(f"records[{i}] 缺字段 {sorted(missing)}")
            if rec["kind"] not in _VALID_KINDS:
                raise RegistryError(f"records[{i}] kind={rec['kind']!r} ∉ {sorted(_VALID_KINDS)}")
            if rec["status"] != "frozen":
                raise RegistryError(f"records[{i}] status={rec['status']!r} != frozen"
                                    f"（§D.3：入库即冻结；探索期常数不得入库）")
            if not isinstance(rec["provenance"], list) or not rec["provenance"]:
                raise RegistryError(f"records[{i}] provenance 须为非空 list（出处强制）")
            if rec["name"] in seen:
                raise RegistryError(f"records[{i}] 重名 {rec['name']!r}")
            seen.add(rec["name"])
        sealed = doc.get("records_sha256")
        actual = records_sha256(records)
        if sealed != actual:
            raise RegistryError(
                f"records_sha256 不匹配：封值 {str(sealed)[:12]}… != 重算 {actual[:12]}…"
                f"——记录被改动而未 --seal（§D.3 冻结完整性）")
        self.records_digest: str = actual   # 供期望文件等下游落盘引用（证明期望来源版本）
        self._records: Dict[str, Dict[str, Any]] = {r["name"]: r for r in records}

    def get(self, name: str) -> Dict[str, Any]:
        if name not in self._records:
            raise RegistryError(f"注册表无记录 {name!r}（现有 {sorted(self._records)}）")
        return self._records[name]

    # ---- 受限公式求值（值来自注册表 value 字符串；白名单 AST 节点） ----

    @staticmethod
    def _eval_node(node: ast.AST, binds: Dict[str, int]) -> int:
        if isinstance(node, ast.Expression):
            return ProtocolRegistry._eval_node(node.body, binds)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int):
                raise RegistryError(f"公式常量 {node.value!r} 非 int")
            return int(node.value)
        if isinstance(node, ast.Name):
            if node.id not in binds:
                raise RegistryError(f"公式引用未绑定名 {node.id!r}（绑定={sorted(binds)}）")
            return binds[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ProtocolRegistry._eval_node(node.operand, binds)
        if isinstance(node, ast.BinOp):
            lhs = ProtocolRegistry._eval_node(node.left, binds)
            rhs = ProtocolRegistry._eval_node(node.right, binds)
            if isinstance(node.op, ast.Add):
                return lhs + rhs
            if isinstance(node.op, ast.Sub):
                return lhs - rhs
            if isinstance(node.op, ast.Mult):
                return lhs * rhs
            if isinstance(node.op, ast.FloorDiv):
                if rhs == 0:
                    raise RegistryError("公式整解除零")
                return lhs // rhs
        raise RegistryError(f"公式含白名单外节点 {type(node).__name__}"
                            f"（仅 + - * //、负号、int 常量、绑定名）")

    def evaluate(self, name: str, **binds: Any) -> int:
        """按注册表 value 公式求值（kind=formula 专属；binds 须为 int 非 bool）。"""
        rec = self.get(name)
        if rec["kind"] != "formula":
            raise RegistryError(f"{name} kind={rec['kind']} 非 formula，不可求值")
        int_binds: Dict[str, int] = {}
        for k, v in binds.items():
            if isinstance(v, bool) or not isinstance(v, int):
                raise RegistryError(f"绑定 {k}={v!r} 非 int")
            int_binds[k] = v
        expr = str(rec["value"])
        return self._eval_node(ast.parse(expr, mode="eval"), int_binds)


# v3 = 2026-10-13 追加冻结（用户裁定五行表；独立论证 docs/registry_v3_argument_20261013.md；
# 常数零改动，仅扩展已证边界 II∈{2,3,6} + 新增 8 条语义记录）；v2 = 2026-09-29
# PROJ_ISSUE_UNROLL 勘误（用户批准生效版）；v1/v2 文件保留未动，历史产物（封存期望/
# RED 报告/内嵌旧 digest 的 manifest）仍可经 ProtocolRegistry(旧版path) 显式加载复验。
_DEFAULT_PATH = _REPO / "verification_v2" / "tools" / "protocol_registry_v3.yaml"


def load_default() -> ProtocolRegistry:
    return ProtocolRegistry(_DEFAULT_PATH)


def _seal(path: Path) -> None:
    """重算 records_sha256 并以最小文本替换写回（保留注释/排版；仅换封值行）。"""
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    digest = records_sha256(doc["records"])
    new, n = re.subn(r'^records_sha256:\s*".*?"\s*$',
                     f'records_sha256: "{digest}"', text, count=1, flags=re.M)
    if n != 1:
        raise RegistryError(f"{path}: records_sha256 行定位失败（期望恰 1 处）")
    path.write_text(new, encoding="utf-8")
    print(f"[SEAL] {path.name}: records_sha256 = {digest[:16]}…（{len(doc['records'])} 条记录）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ADR-056 §D.3 协议模型注册表校验/封存")
    ap.add_argument("--path", default=str(_DEFAULT_PATH))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="仅校验（0=完整）")
    g.add_argument("--seal", action="store_true", help="重算 records_sha256 写回")
    args = ap.parse_args(argv)
    path = Path(args.path)
    if args.seal:
        _seal(path)
    reg = ProtocolRegistry(path)   # seal 后复核（check 路径也在此验证完整性）
    if args.check:
        # 冒烟：公式求值通道可用（A1 已知点 II=2/skip=1 → l_init=3；TERM=l_init+1+II*R_q=128；
        # v2 新增 ISSUE 模型：相位偏移 1、总拍数 l_init+1+II*R_q+1=129）
        li = reg.evaluate("INIT_PAD_FORMULA", skip=1, II=2)
        tp = reg.evaluate("TERM_PHASE_FORMULA", l_init=li, II=2, R_q=62)
        assert (li, tp) == (3, 128), (li, tp)
        if "ISSUE_PHASE_OFFSET" in reg._records and "ISSUE_TOTAL_FORMULA" in reg._records:
            off = reg.evaluate("ISSUE_PHASE_OFFSET")
            it = reg.evaluate("ISSUE_TOTAL_FORMULA", l_init=li, II=2, R_q=62)
            assert (off, it) == (1, 129), (off, it)
        print(f"[OK] 注册表 v{reg.version} 完整：{len(reg._records)} 条 frozen 记录，"
              f"sha256 自洽，公式求值冒烟通过（A1 点 l_init={li}, term={tp}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
