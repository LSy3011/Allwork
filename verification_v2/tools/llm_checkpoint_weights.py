"""从真实 LLM checkpoint（HF safetensors）**按需 range-read** 权重张量，喂 LLM matmul tile 验证。

为什么 range-read：safetensors 头部是 JSON（含每张量 byte offset），不必下整模型——只 HTTP Range
拉需要的几个权重张量（per-layer q/k/v/o/gate/up/down + lm_head 抽样行），BF16→fp16，缓存到 build/。
TinyLlama-1.1B 全量 2.2GB → 本工具只拉 ~58MB（封顶每张量 MAX_ROWS 行）。

诚实边界：**只接真权重**；激活仍是合成的代表性分布（真激活需跑 forward = torch，未做、标 TODO）。
权重原生 BF16，硬件是 fp16 → 转换本身是精度变化（bf16 10→fp16 11 effective bits、指数范围缩），
但 transformer 权重量级小（~0.02）不溢出，正是要验的。
"""
from __future__ import annotations
import json
import struct
import urllib.request
from pathlib import Path
from typing import Dict, Optional

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = _ROOT / "build" / "llm_weights_cache"
MAX_ROWS = 2048   # 每张量封顶抽样行数（限下载量；2048 个真神经元足够代表分布）

REPOS: Dict[str, str] = {
    "tinyllama-1.1b": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
    "qwen2-7b": "Qwen/Qwen2-7B",
    "llama3-8b": "meta-llama/Meta-Llama-3-8B",
}

# 生成器 GEMM 名 → safetensors 张量 key（layer 0；attn_qk/av 无权重，不在此）
TENSOR_KEYS: Dict[str, str] = {
    "q_proj":    "model.layers.0.self_attn.q_proj.weight",
    "k_proj":    "model.layers.0.self_attn.k_proj.weight",
    "v_proj":    "model.layers.0.self_attn.v_proj.weight",
    "o_proj":    "model.layers.0.self_attn.o_proj.weight",
    "gate_proj": "model.layers.0.mlp.gate_proj.weight",
    "up_proj":   "model.layers.0.mlp.up_proj.weight",
    "down_proj": "model.layers.0.mlp.down_proj.weight",
    "lm_head":   "lm_head.weight",
}

_DTYPE_NP = {"F16": np.float16, "F32": np.float32, "BF16": "bf16"}


def _url(repo: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/model.safetensors"


def _range(url: str, a: int, b: int, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"Range": f"bytes={a}-{b}"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _bytes_to_fp16(raw: bytes, dtype: str) -> np.ndarray:
    if dtype == "BF16":
        u16 = np.frombuffer(raw, dtype=np.uint16)
        f32 = (u16.astype(np.uint32) << 16).view(np.float32)   # bf16 = fp32 高 16 位
        return f32.astype(np.float16)
    return np.frombuffer(raw, dtype=_DTYPE_NP[dtype]).astype(np.float16)


def fetch_weights(model_key: str, refresh: bool = False) -> Optional[Path]:
    """Range-read 各权重张量（封顶 MAX_ROWS 行）→ BF16/F16→fp16 → 缓存 npz。返回缓存路径；网络失败返 None。"""
    cache = CACHE_DIR / f"{model_key}.npz"
    if cache.exists() and not refresh:
        return cache
    repo = REPOS[model_key]
    url = _url(repo)
    try:
        n = struct.unpack("<Q", _range(url, 0, 7))[0]
        hdr = json.loads(_range(url, 8, 8 + n - 1).decode())
        base = 8 + n
        out: Dict[str, np.ndarray] = {}
        for gemm, key in TENSOR_KEYS.items():
            meta = hdr[key]
            n_out, k_in = meta["shape"]               # HF Linear: [out_features, in_features]
            rows = min(n_out, MAX_ROWS)
            itemsize = 2 if meta["dtype"] in ("F16", "BF16") else 4
            s0 = base + meta["data_offsets"][0]
            raw = _range(url, s0, s0 + rows * k_in * itemsize - 1)
            out[gemm] = _bytes_to_fp16(raw, meta["dtype"]).reshape(rows, k_in)
    except Exception as e:  # noqa: BLE001
        print(f"[llm_weights] fetch 失败（网络/权限？）: {e}")
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(cache, **out)
    return cache


class RealWeightSampler:
    """持真权重矩阵（每 GEMM [rows, K_in] fp16），sample(gemm, k_tile) → 随机神经元的真 K=8 权重切片。"""

    def __init__(self, cache: Path):
        z = np.load(cache)
        self.W: Dict[str, np.ndarray] = {k: z[k] for k in z.files}

    def has(self, gemm: str) -> bool:
        return gemm in self.W

    def sample(self, gemm: str, rng: np.random.Generator, k_tile: int = 8) -> Optional[np.ndarray]:
        if gemm not in self.W:
            return None
        w = self.W[gemm]                              # [rows, K_in]
        n = int(rng.integers(w.shape[0]))
        k0 = int(rng.integers(max(1, w.shape[1] - k_tile + 1)))
        return w[n, k0:k0 + k_tile].astype(np.float16)

    def stats(self) -> Dict[str, Dict[str, float]]:
        out = {}
        for k, w in self.W.items():
            wf = w.astype(np.float32)
            out[k] = {"rows": w.shape[0], "k_in": w.shape[1],
                      "std": float(wf.std()), "absmax": float(np.abs(wf).max())}
        return out


def load_sampler(model_key: str, refresh: bool = False) -> Optional["RealWeightSampler"]:
    cache = fetch_weights(model_key, refresh=refresh)
    return RealWeightSampler(cache) if cache else None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="tinyllama-1.1b", choices=list(REPOS))
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    s = load_sampler(args.model, refresh=args.refresh)
    if s is None:
        raise SystemExit("无法获取权重（网络/权限）")
    print(f"=== {args.model} 真权重统计（缓存 {CACHE_DIR}）===")
    print(f"{'tensor':<11}{'rows':>7}{'k_in':>7}{'std':>10}{'absmax':>10}")
    for k, st in s.stats().items():
        print(f"{k:<11}{st['rows']:>7}{st['k_in']:>7}{st['std']:>10.4f}{st['absmax']:>10.3f}")
