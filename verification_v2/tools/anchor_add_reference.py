# -*- coding: utf-8 -*-
"""ADR-056 实现步②出口证据 4：add 三规模独立参考 ↔ 冻结 golden 逐位锚定 + 冻结哈希复核。

锚定链（§C.3；冻结清单无数值字节的事实决定本链形状，见 FROZEN_BASELINE.md）
---------------------------------------------------------------------------------
1. **冻结哈希复核**：``sha256sum -c FROZEN_BASELINE_SHA256SUMS.txt``，60/60 OK 才继续。
2. **输入确定性再生成**：按 ConfigurableOperator.prepare_input 配方手工重放
   （linspace/常量数组），并**逐位断言**与现行仓库 prepare_input 输出一致
   （x/other/alpha 三键；constants bank 7 是 np.random.randn 非确定项、公式不引用，单列记录）。
3. **旧 golden 复现**：用**未改动的**仓库 ConfigurableOperator.get_golden
   （sympy lambdify，numpy fp32）在三规模上重新产出 golden。
4. **新参考求值**：§C.2 有序语法树逐步 fp32（verification_v2.reference）求同一输入。
5. **逐位比对**：n ∈ {4, 8, 60} 全量 uint32 位模式相等才算锚定；任一失配 → 非零退出
   （步②熔断：阻止进入步③，按「参考发生器 / 常量转换 / 运算顺序 / 旧 golden」分类定位，
   冻结基线一律不动）。
6. **n60 幸存字节双载体对照**：build/verification_v2/add/golden_data.npz（key 'y'）
   与 ur_bank_2_out_y.hex（128-bit/行×4 lane，**最右 8 hex = lane 0**，解码时顺带断言
   y[0] = 0xC1B40000 = -22.5 = -10 + 2.5×(-5) 交叉验证 lane 序）逐位比对，并复制入档。
   此两件是**同一次** A1 运行（PASSED 日志链）留下的原始值字节——两个归档载体、
   同一数值来源，非两个独立来源。
7. **§C.4 判定留档**：add 遗留域静态保守拒绝（设计内）+ 同数据动态判过道（设计内并存）、
   scale_sum 现行 yaml 安全域静态/动态双过。

产物：verification_v2/artifacts/reference_anchor_20260926/
  ANCHOR_REPORT.md / anchor_report.json / golden_data.npz / ur_bank_2_out_y.hex
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from verification_v2.ops.configurable import ConfigurableOperator            # noqa: E402
from verification_v2.reference.ordered_ast import parse_ordered_formula      # noqa: E402
from verification_v2.reference import reference_generator as rg              # noqa: E402

F32 = np.float32
SCALES = (4, 8, 60)
ADD_FORMULA = "x + alpha * other"
ADD_DOMAINS = {"x": (-10.0, 10.0), "other": (-5.0, 15.0), "alpha": (2.5, 2.5)}
SCALE_SUM_DOMAINS = {"x": (1.0, 2.0), "other": (1.0, 2.0), "alpha": (2.5, 2.5)}

FROZEN_DIR = ROOT / "verification_v2/artifacts/e2e_add_20260926"
BUILD_ADD = ROOT / "build/verification_v2/add"
OUT_DIR = ROOT / "verification_v2/artifacts/reference_anchor_20260926"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bits_equal(a: np.ndarray, b: np.ndarray) -> bool:
    a = np.asarray(a, dtype=F32).reshape(-1)
    b = np.asarray(b, dtype=F32).reshape(-1)
    return a.shape == b.shape and bool(np.array_equal(a.view(np.uint32), b.view(np.uint32)))


def recipe_inputs(n: int) -> dict:
    """prepare_input 配方的手工重放（add.yaml test_data）。"""
    return {
        "x": np.linspace(-10.0, 10.0, n).astype(F32),
        "other": np.linspace(-5.0, 15.0, n).astype(F32),
        "alpha": np.array([2.5]).astype(F32),
    }


def fail(msg: str) -> "NoReturn":  # noqa: F821
    print(f"[ANCHOR FAIL] {msg}")
    sys.exit(1)


def check_frozen_manifest() -> dict:
    sums = FROZEN_DIR / "FROZEN_BASELINE_SHA256SUMS.txt"
    if not sums.exists():
        fail(f"冻结清单缺失：{sums}")
    proc = subprocess.run(["sha256sum", "-c", sums.name], cwd=FROZEN_DIR,
                          capture_output=True, text=True)
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    ok = sum(1 for l in lines if l.rstrip().endswith(": OK"))
    bad = [l for l in lines if not l.rstrip().endswith(": OK")]
    if proc.returncode != 0 or bad or ok != 60:
        fail(f"冻结哈希复核失败：ok={ok} bad={bad} rc={proc.returncode}\n{proc.stdout}")
    return {"checked": ok, "failed": bad, "manifest_sha256": sha256_of(sums)}


def decode_out_y_hex(path: Path, expect_n: int) -> np.ndarray:
    """128-bit/行，最右 8 hex chars = lane 0（A1 取证已核：n60 行1 尾 c1b40000 = y[0]）。"""
    vals = []
    for ln, raw in enumerate(path.read_text().splitlines()):
        h = raw.strip()
        if not h:
            continue
        assert len(h) == 32, f"第 {ln} 行非 128-bit：{h!r}"
        for lane in range(4):  # lane0 最右
            word = h[24 - 8 * lane: 32 - 8 * lane]
            vals.append(np.uint32(int(word, 16)).view(F32))
    arr = np.array(vals, dtype=F32)
    assert arr.size == expect_n, f"{path.name} 解出 {arr.size} 值 ≠ {expect_n}"
    return arr


def main() -> int:
    report = {"scales": {}, "frozen": None, "surviving_n60": {}, "c4_records": {},
              "provenance_files": {}}
    for rel in ["verification_v2/ops/add.yaml", "verification_v2/ops/scale_sum.yaml",
                "verification_v2/reference/ordered_ast.py",
                "verification_v2/reference/reference_generator.py"]:
        report["provenance_files"][rel] = sha256_of(ROOT / rel)

    # 1. 冻结哈希复核
    report["frozen"] = check_frozen_manifest()
    print(f"[1/5] 冻结基线 sha256 复核：{report['frozen']['checked']}/60 OK")

    op = ConfigurableOperator(str(ROOT / "verification_v2/ops/add.yaml"))
    root_add = parse_ordered_formula(ADD_FORMULA, ["x", "other", "alpha"])
    leaf_map = rg.leaf_paths(root_add)

    # 2-5. 三规模：输入再生成逐位断言 → 旧 golden → 新参考 → 逐位比对 → §C.4 判定
    for n in SCALES:
        repo_inputs = op.prepare_input(n)
        mine = recipe_inputs(n)
        for k in ("x", "other", "alpha"):
            assert bits_equal(repo_inputs[k], mine[k]), f"n={n} 输入 {k} 再生成不一致"
        const_note = ("constants 键由 prepare_input 走 np.random.randn 非确定生成分支，"
                      "公式自由符号为 {x, other, alpha} 不引用之；逐位断言仅覆盖公式三键。")

        golden_old = np.asarray(op.get_golden(repo_inputs, 4)["y"], dtype=F32).reshape(-1)
        ev = rg.evaluate(root_add, {k: mine[k] for k in ("x", "other", "alpha")})
        ref_y = ev.y.reshape(-1)
        n_bits = golden_old.size
        match = bits_equal(ref_y, golden_old)

        st = rg.static_domain_check(root_add, ADD_DOMAINS)
        dyn = rg.dynamic_data_check(ev, ADD_DOMAINS, leaf_map)

        report["scales"][str(n)] = {
            "n_bits": int(n_bits),
            "input_regen_bits_equal": True,
            "constants_note": const_note,
            "ref_vs_frozen_golden_bits_equal": bool(match),
            "static_ok": st.ok,
            "static_kinds": sorted({d.kind for d in st.diagnostics}),
            "static_first": str(st.diagnostics[0]) if st.diagnostics else None,
            "dynamic_diags": len(dyn),
            "node_values_min_abs": float(min(
                np.abs(v[np.nonzero(v)]).min() if np.nonzero(v)[0].size else 0.0
                for _, v in ev.node_values)),
        }
        status = "BIT-EXACT" if match else "MISMATCH"
        print(f"[2/5] n={n:>2}: golden {n_bits} 值 {status}；静态={'PASS' if st.ok else 'REJECT'}"
              f"（{report['scales'][str(n)]['static_kinds']}），动态诊断={len(dyn)}")
        if not match:
            idx = int(np.nonzero(ref_y.view(np.uint32) != golden_old.view(np.uint32))[0][0])
            fail(f"n={n} 第 {idx} 点失配：ref=0x{ref_y.view(np.uint32)[idx]:08X} "
                 f"golden=0x{golden_old.view(np.uint32)[idx]:08X}"
                 f"（ref={float(ref_y[idx])!r} golden={float(golden_old[idx])!r}）"
                 "——按熔断分类定位：参考发生器/常量转换/运算顺序/旧 golden，冻结基线不动。")

    # 6. n60 幸存字节双重独立对照
    npz_path, hex_path = BUILD_ADD / "golden_data.npz", BUILD_ADD / "ur_bank_2_out_y.hex"
    ref60 = rg.evaluate(root_add, recipe_inputs(60)).y.reshape(-1)
    npz_y = np.asarray(np.load(npz_path)["y"], dtype=F32).reshape(-1)
    hex_y = decode_out_y_hex(hex_path, 60)
    lane_order_proof = (f"y[0] 位模式 0x{hex_y.view(np.uint32)[0]:08X} == 0xC1B40000"
                        f" == -10 + 2.5×(-5) = -22.5（lane 序交叉验证）")
    assert hex_y.view(np.uint32)[0] == 0xC1B40000, lane_order_proof
    npz_ok, hex_ok = bits_equal(ref60, npz_y), bits_equal(ref60, hex_y)
    report["surviving_n60"] = {
        "npz_sha256": sha256_of(npz_path), "npz_bits_equal": bool(npz_ok),
        "hex_sha256": sha256_of(hex_path), "hex_bits_equal": bool(hex_ok),
        "lane_order_proof": lane_order_proof,
    }
    print(f"[3/5] n60 幸存字节：npz {'BIT-EXACT' if npz_ok else 'MISMATCH'}；"
          f"hex {'BIT-EXACT' if hex_ok else 'MISMATCH'}（{lane_order_proof}）")
    if not (npz_ok and hex_ok):
        fail("n60 幸存字节与新参考失配——熔断，先定位再前进，冻结基线不动。")

    # 7. §C.4 判定留档（scale_sum 现行安全域 + 遗留域）
    root_ss = parse_ordered_formula("alpha * (x + other)", ["x", "other", "alpha"])
    st_ss = rg.static_domain_check(root_ss, SCALE_SUM_DOMAINS)
    ev_ss = rg.evaluate(root_ss, {
        "x": np.linspace(1.0, 2.0, 4).astype(F32),
        "other": np.linspace(1.0, 2.0, 4).astype(F32),
        "alpha": np.array([2.5]).astype(F32)})
    dyn_ss = rg.dynamic_data_check(ev_ss, SCALE_SUM_DOMAINS, rg.leaf_paths(root_ss))
    st_ss_legacy = rg.static_domain_check(root_ss, ADD_DOMAINS)
    report["c4_records"]["scale_sum"] = {
        "current_yaml_safe_domain": {"static_ok": st_ss.ok, "dynamic_diags": len(dyn_ss)},
        "legacy_domain": {"static_ok": st_ss_legacy.ok,
                          "static_kinds": sorted({d.kind for d in st_ss_legacy.diagnostics}),
                          "first": str(st_ss_legacy.diagnostics[0])},
    }
    print(f"[4/5] scale_sum：安全域 static={'PASS' if st_ss.ok else 'REJECT'} "
          f"dyn={len(dyn_ss)}；遗留域 static=REJECT（{report['c4_records']['scale_sum']['legacy_domain']['static_kinds']}）")
    assert st_ss.ok and not dyn_ss and not st_ss_legacy.ok

    # 归档
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(npz_path, OUT_DIR / "golden_data.npz")
    shutil.copy2(hex_path, OUT_DIR / "ur_bank_2_out_y.hex")
    (OUT_DIR / "anchor_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"[5/5] 归档 → {OUT_DIR.relative_to(ROOT)}/（report.json + 幸存字节两件）")
    print("ANCHOR OK：add 三规模参考 ↔ golden 逐位一致；冻结哈希 60/60；n60 幸存字节双载体对照一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
