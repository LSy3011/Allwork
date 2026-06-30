"""真权重 tile 喂进**实际算子 e2e**（单-SMC K=8 dot），验真固件+sim 数据通路跑真 LLM 权重值。

与 `gen_llm_matmul_tiles.py` 的区别：那个用 `get_golden` 算术 oracle 大规模验 tile 算术；本 driver
把真权重 tile 写 npz、经 `main_verify.py --op fp16_matmul_min --mode e2e --input-file` 跑**完整数据
通路**（SMT 调度→固件 hex→sim units: RTOVR 路由/FP16MUL/FP32ADDTREE8TO1/bank R·W→golden parity）。
每 tile 一次 e2e≈几秒 → 采样**每 GEMM 少量代表 tile**（非几千个；规模由 gen 工具的 oracle 覆盖）。

范围（CLAUDE.md scope）：单算子拆分验证 + 真场景。**单-SMC** 单 K=8 dot——多-SMC N=8 真权重列需改
pipeline（run_full_stack line 39 对 parallel_dim 重生成 input，忽略 --input-file），留后续。
权重真、激活合成（真激活=后续，见 [[matmul-verif-scope-single-op-tiles]]）。
"""
from __future__ import annotations
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from verification_v2.tools.llm_checkpoint_weights import load_sampler, TENSOR_KEYS  # noqa: E402
from verification_v2.tools.gen_llm_matmul_tiles import enumerate_gemms, sample_vec, MODELS  # noqa: E402

OP = "fp16_matmul_min"


def _run_one(npz_path: Path, timeout: int = 180) -> Tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "verification_v2/main_verify.py", "--op", OP,
         "--mode", "e2e", "--input-file", str(npz_path), "--quiet"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=timeout)
    out = proc.stdout + proc.stderr
    ok = proc.returncode == 0 and "ALL PHYSICAL CHECKS PASSED" in out
    return ok, out


def run(model_key: str, per_gemm: int, seed: int,
        gemms: List[str] = None) -> Dict[str, Tuple[int, int]]:
    """每个 weight GEMM 采样 per_gemm 个真权重 tile，经 fp16_matmul_min e2e 跑，返 {gemm:(passed,failed)}。"""
    sampler = load_sampler(model_key)
    if sampler is None:
        raise RuntimeError("真权重不可用（网络/缓存缺）")
    a_role = {g.name: g.a_role for g in enumerate_gemms(MODELS[model_key], seq_len=128, decode=True)}
    target = gemms or [g for g in TENSOR_KEYS if sampler.has(g)]
    rng = np.random.default_rng(seed)
    results: Dict[str, Tuple[int, int]] = {}
    with tempfile.TemporaryDirectory() as td:
        for gemm in target:
            passed = failed = 0
            for i in range(per_gemm):
                b = sampler.sample(gemm, rng)               # 真权重 K=8 切片
                a = sample_vec(a_role.get(gemm, "hidden_rmsnorm"), rng)  # 合成激活
                npz = Path(td) / f"{gemm}_{i}.npz"
                np.savez(npz, a=a, b=b)
                ok, out = _run_one(npz)
                if ok:
                    passed += 1
                else:
                    failed += 1
                    tail = "\n".join(out.splitlines()[-6:])
                    print(f"  [FAIL] {gemm} tile {i}:\n{tail}")
            results[gemm] = (passed, failed)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="tinyllama-1.1b", choices=list(MODELS))
    ap.add_argument("--per-gemm", type=int, default=2, help="每 GEMM e2e tile 数（慢，每 tile 一次 e2e）")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    print(f"=== {MODELS[args.model].name} · 真权重 tile → {OP} e2e（每 GEMM {args.per_gemm} tile）===")
    results = run(args.model, args.per_gemm, args.seed)
    print(f"\n{'GEMM':<11}{'passed':>8}{'failed':>8}")
    tot_p = tot_f = 0
    for gemm, (p, f) in results.items():
        tot_p += p; tot_f += f
        print(f"{gemm:<11}{p:>8}{f:>8}")
    print(f"{'—'*27}\n{'TOTAL':<11}{tot_p:>8}{tot_f:>8}  "
          f"{'✅ 全通' if tot_f == 0 else '❌ 有 FAIL'}")
    sys.exit(0 if tot_f == 0 else 1)


if __name__ == "__main__":
    main()
