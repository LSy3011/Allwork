"""LLM matmul tile 级测试用例生成 + fp16 tile compute 验证（针对目标模型）。

背景（硬件能力边界，CLAUDE.md / ADR-025/026）：
  硬件单 tile = **K=8 fp16 dot**（FP16×FP16→FP32 精确，再 FP32 平衡树累加），8 SMC → N=8/趟、
  M≤4。**大 K / 大 N / M>4 的多-tile 编排未实现**（DEBT-025-MULTITILE）→ 真 LLM 尺寸 GEMM 现在
  跑不通。故本工具做两件「现在可跑」的事：
    1. 枚举目标模型每个 GEMM 的 [M,K,N] + tile 分解（K/N/M 各几 tile）→ coverage 目录。
    2. 用**模型真实 fp16 数值分布**采样 K=8 tile，喂硬件 fp16 dot 语义
       （复用 `ManualFp16MatmulOperator.get_golden` = FP16MUL→FP32ADDTREE8TO1，
        已由 tests/unit/test_fp16_matmul_min.py 证实 == sim units 链），校：
        ① bit-exact vs 平衡树参考；② vs fp64 真值的 fp16 量化误差；③ fp16 无溢出/NaN。

⚠ 诚实声明：数值是按 LLM 典型量级**合成的代表性分布**（权重 ~N(0,σ)、激活 ~N(0,1)+离群），
  **非**从某个真实 checkpoint 提取的权重。要用真权重需另接加载模型（标 TODO）。
"""
from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from verification_v2.ops.matmul_op import ManualFp16MatmulOperator  # noqa: E402

K_TILE = 8     # fp16 单 tile 收缩维（FP16MUL 8 lane）
N_TILE = 8     # 一趟输出列数 = PARAM_SMC_NUM
M_TILE = 4     # 单趟左行数上限（addtree 通道；datapath M>4 = DEBT-036，未实现）


@dataclass
class ModelConfig:
    name: str
    hidden: int          # H
    ffn: int             # I (intermediate_size)
    n_heads: int
    n_kv_heads: int
    head_dim: int
    vocab: int

    @property
    def q_dim(self) -> int:
        return self.n_heads * self.head_dim

    @property
    def kv_dim(self) -> int:
        return self.n_kv_heads * self.head_dim


# 目标模型库（dims 取自公开 config；TinyLlama-1.1B 类为默认小模型快验证目标）
MODELS: Dict[str, ModelConfig] = {
    "tinyllama-1.1b": ModelConfig("TinyLlama-1.1B", hidden=2048, ffn=5632,
                                  n_heads=32, n_kv_heads=4, head_dim=64, vocab=32000),
    "qwen2-7b": ModelConfig("Qwen2-7B", hidden=3584, ffn=18944,
                            n_heads=28, n_kv_heads=4, head_dim=128, vocab=152064),
    "llama3-8b": ModelConfig("Llama-3-8B", hidden=4096, ffn=14336,
                             n_heads=32, n_kv_heads=8, head_dim=128, vocab=128256),
}


@dataclass
class Gemm:
    name: str
    M: int               # 左行数（decode=1 / prefill=seq）
    K: int               # 收缩维
    N: int               # 输出列
    w_role: str          # 权重分布角色
    a_role: str          # 激活分布角色

    def tiles(self) -> Dict[str, int]:
        nk = -(-self.K // K_TILE)        # ceil
        nn = -(-self.N // N_TILE)
        nm = -(-self.M // M_TILE)
        return {"k_tiles": nk, "n_tiles": nn, "m_tiles": nm, "total": nk * nn * nm}


def enumerate_gemms(cfg: ModelConfig, seq_len: int, decode: bool) -> List[Gemm]:
    """目标模型一个 decoder 层的 weight GEMM + LM head + attention GEMM。
    M = 1（decode 单 token）或 seq（prefill）。attention 的 K/N 随上下文 ctx=seq_len 变。"""
    M = 1 if decode else seq_len
    ctx = seq_len
    H, I, vocab = cfg.hidden, cfg.ffn, cfg.vocab
    g: List[Gemm] = [
        Gemm("q_proj",    M, H, cfg.q_dim,  "weight", "hidden_rmsnorm"),
        Gemm("k_proj",    M, H, cfg.kv_dim, "weight", "hidden_rmsnorm"),
        Gemm("v_proj",    M, H, cfg.kv_dim, "weight", "hidden_rmsnorm"),
        Gemm("o_proj",    M, cfg.q_dim, H,  "weight", "attn_out"),
        Gemm("gate_proj", M, H, I,         "weight", "hidden_rmsnorm"),
        Gemm("up_proj",   M, H, I,         "weight", "hidden_rmsnorm"),
        Gemm("down_proj", M, I, H,         "weight", "ffn_inter"),
        Gemm("lm_head",   M, H, vocab,     "weight", "hidden_rmsnorm"),
        # attention（per head；K=head_dim 的 QK^T、K=ctx 的 softmax·V）
        Gemm("attn_qk",   M, cfg.head_dim, ctx, "act_q", "act_k"),
        Gemm("attn_av",   M, ctx, cfg.head_dim, "act_score", "act_v"),
    ]
    return g


# --- 模型真实 fp16 数值分布（代表性，非真 checkpoint）---
# 权重：训练后 transformer 权重 ~ N(0, σ)，σ≈0.02（典型）。激活：post-RMSNorm ~ N(0,1)，
# 但 LLM 已知有「离群通道」（少数维度量级大，可达 ~10-50），fp16 累加要稳住。FFN 中间
# （SiLU(gate)*up）量级更大且偏正。attention score 经 softmax ∈ [0,1]。
_DIST: Dict[str, Tuple[float, float, float]] = {
    # role: (std, outlier_frac, outlier_scale)
    "weight":          (0.020, 0.000, 1.0),
    "hidden_rmsnorm":  (1.000, 0.010, 20.0),   # 1% 离群通道 × 20
    "attn_out":        (0.700, 0.005, 15.0),
    "ffn_inter":       (2.500, 0.010, 8.0),    # SiLU·up，偏大
    "act_q":           (1.000, 0.005, 10.0),
    "act_k":           (1.000, 0.005, 10.0),
    "act_score":       (0.060, 0.000, 1.0),    # softmax 概率，∑=1、单值小
    "act_v":           (0.800, 0.005, 12.0),
}


def sample_vec(role: str, rng: np.random.Generator, n: int = K_TILE) -> np.ndarray:
    std, ofrac, oscale = _DIST[role]
    v = rng.normal(0.0, std, n)
    if ofrac > 0:
        mask = rng.random(n) < ofrac
        v[mask] = rng.normal(0.0, std * oscale, int(mask.sum()))
    return v.astype(np.float16)


def fp64_dot(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a.astype(np.float64), b.astype(np.float64)))


@dataclass
class GemmReport:
    gemm: Gemm
    samples: int
    max_rel_err: float
    max_abs_out: float
    overflow: int
    nan: int
    bit_exact_fail: int


def validate_gemm(g: Gemm, n_samples: int, rng: np.random.Generator,
                  op: ManualFp16MatmulOperator) -> GemmReport:
    max_rel = 0.0
    max_abs = 0.0
    overflow = nan = bitfail = 0
    for _ in range(n_samples):
        a = sample_vec(g.a_role, rng)
        b = sample_vec(g.w_role, rng)
        if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
            overflow += 1                       # fp16 输入已溢出（离群 × 大 → inf）
            continue
        hw = float(op.get_golden({"a": a, "b": b}, vector_lanes=8)["y"][0])  # 硬件 fp16 dot
        if not np.isfinite(hw):
            nan += 1
            continue
        truth = fp64_dot(a, b)                  # fp64 真值
        # bit-exact 自洽：硬件语义 == 平衡树参考（用 fp32 重算同序）
        ref = _balanced_tree_ref(a, b)
        if np.float32(hw) != np.float32(ref):
            bitfail += 1
        denom = abs(truth) if abs(truth) > 1e-6 else 1.0
        max_rel = max(max_rel, abs(hw - truth) / denom)
        max_abs = max(max_abs, abs(hw))
    return GemmReport(g, n_samples, max_rel, max_abs, overflow, nan, bitfail)


def _balanced_tree_ref(a: np.ndarray, b: np.ndarray) -> np.float32:
    """镜像硬件 FP32ADDTREE8TO1 平衡树求和（独立于 op，作 bit-exact 对照）。"""
    f32 = np.float32
    p = (a.astype(np.float32) * b.astype(np.float32))
    l0 = f32(p[0] + p[1]); l1 = f32(p[2] + p[3])
    l2 = f32(p[4] + p[5]); l3 = f32(p[6] + p[7])
    return f32(f32(l0 + l1) + f32(l2 + l3))


def run(model_key: str, seq_len: int, decode: bool, n_samples: int, seed: int) -> List[GemmReport]:
    cfg = MODELS[model_key]
    rng = np.random.default_rng(seed)
    op = ManualFp16MatmulOperator()
    gemms = enumerate_gemms(cfg, seq_len, decode)

    mode = "decode (M=1)" if decode else f"prefill (M={seq_len})"
    print(f"\n=== {cfg.name} · {mode} · ctx={seq_len} · fp16 tile(K={K_TILE},N={N_TILE},M≤{M_TILE}) ===")
    print(f"{'GEMM':<11}{'M':>6}{'K':>7}{'N':>8}{'k_til':>7}{'n_til':>7}{'m_til':>6}{'tiles/层':>12}")
    total_tiles = 0
    for g in gemms:
        t = g.tiles()
        total_tiles += t["total"]
        print(f"{g.name:<11}{g.M:>6}{g.K:>7}{g.N:>8}{t['k_tiles']:>7}{t['n_tiles']:>7}"
              f"{t['m_tiles']:>6}{t['total']:>12,}")
    print(f"{'—'*64}\n单层 weight+attn GEMM 总 tile 数 ≈ {total_tiles:,}（多-tile 编排 DEBT-025 未实现，"
          f"现仅验单 tile compute）")

    print(f"\n--- fp16 tile compute 验证（每 GEMM 采样 {n_samples} 个 K=8 tile）---")
    print(f"{'GEMM':<11}{'samples':>8}{'max_rel_err':>13}{'max|out|':>11}{'overflow':>9}"
          f"{'nan':>5}{'bit_fail':>9}")
    reports = []
    for g in gemms:
        r = validate_gemm(g, n_samples, rng, op)
        reports.append(r)
        print(f"{g.name:<11}{r.samples:>8}{r.max_rel_err:>13.2e}{r.max_abs_out:>11.2f}"
              f"{r.overflow:>9}{r.nan:>5}{r.bit_exact_fail:>9}")
    return reports


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="tinyllama-1.1b", choices=list(MODELS))
    ap.add_argument("--seq", type=int, default=128, help="上下文长度（attention K/N）")
    ap.add_argument("--prefill", action="store_true", help="prefill（M=seq）；默认 decode（M=1）")
    ap.add_argument("--samples", type=int, default=2000, help="每 GEMM 采样 tile 数")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    run(args.model, args.seq, not args.prefill, args.samples, args.seed)


if __name__ == "__main__":
    main()
