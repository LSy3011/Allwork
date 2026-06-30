"""LLM matmul tile 级 compute 守门（目标模型 = TinyLlama-1.1B 类，fp16）。

验 `verification_v2/tools/gen_llm_matmul_tiles.py`：对目标模型各 GEMM 用真实 fp16 数值分布
采样 K=8 tile，喂硬件 fp16 dot 语义（FP16MUL→FP32ADDTREE8TO1），守三条不变量：
  ① bit-exact：硬件语义 == 平衡树参考（独立重算同序）→ 专杀 reduce 顺序/精度回归；
  ② fp16 无溢出/NaN：TinyLlama decode 量级（含 1% 离群通道）下 fp16 输入/累加不爆；
  ③ fp16 量化误差有界：vs fp64 真值 rel-err < 1e-2（单 tile K=8 dot）。
纯 Python（复用 op.get_golden，无 sim subprocess），快测。

注：只验**单 tile compute**；大 K/N/M 多-tile 编排 = DEBT-025-MULTITILE 未实现（见工具 docstring）。"""
import unittest
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from verification_v2.tools.gen_llm_matmul_tiles import run, MODELS, enumerate_gemms  # noqa: E402


class TestLLMMatmulTiles(unittest.TestCase):
    MODEL = "tinyllama-1.1b"
    SAMPLES = 800

    def test_tile_decomposition_nonzero(self):
        """tile 分解非平凡：每个 GEMM 至少 1 个 k/n tile，大 GEMM 撑出多 tile（证明多-tile 必需）。"""
        cfg = MODELS[self.MODEL]
        gemms = enumerate_gemms(cfg, seq_len=128, decode=True)
        by_name = {g.name: g for g in gemms}
        self.assertGreaterEqual(by_name["lm_head"].tiles()["n_tiles"], 1000,
                                "lm_head（N=vocab）应撑出 ≥1000 个 N-tile")
        self.assertEqual(by_name["q_proj"].tiles()["k_tiles"], 2048 // 8,
                         "q_proj K=2048 应 256 个 K-tile")
        for g in gemms:
            t = g.tiles()
            self.assertTrue(t["k_tiles"] >= 1 and t["n_tiles"] >= 1 and t["total"] >= 1, g.name)

    def test_fp16_tile_compute_invariants(self):
        """硬件 fp16 dot 对模型真实分布：bit-exact + 无溢出 + 误差有界。"""
        reports = run(self.MODEL, seq_len=128, decode=True, n_samples=self.SAMPLES, seed=0)
        self.assertTrue(reports, "应有 GEMM 报告")
        for r in reports:
            self.assertEqual(r.bit_exact_fail, 0,
                             f"{r.gemm.name}: 硬件 fp16 dot 须 bit-exact == 平衡树参考（reduce 顺序回归）")
            self.assertEqual(r.nan, 0, f"{r.gemm.name}: fp16 dot 出 NaN")
            self.assertEqual(r.overflow, 0,
                             f"{r.gemm.name}: fp16 输入溢出（TinyLlama decode 量级不应爆 fp16）")
            self.assertLess(r.max_rel_err, 1e-2,
                            f"{r.gemm.name}: fp16 量化 rel-err {r.max_rel_err:.2e} 超 1e-2")

    def test_real_checkpoint_weights_fp16_safe(self):
        """真 checkpoint 权重路径：仅当权重已缓存（build/llm_weights_cache/，gitignore、本地 fetch 过）才跑——
        CI 离线不触发下载、自动 skip。守：真权重 tile 也 bit-exact + 无溢出（真 LLM 权重含离群仍 fp16-safe）。
        本地补缓存：python3 verification_v2/tools/llm_checkpoint_weights.py --model tinyllama-1.1b"""
        from verification_v2.tools.llm_checkpoint_weights import CACHE_DIR
        if not (CACHE_DIR / f"{self.MODEL}.npz").exists():
            self.skipTest("真权重未缓存（离线/CI）；本地跑 llm_checkpoint_weights.py 补缓存后启用")
        reports = run(self.MODEL, seq_len=128, decode=True, n_samples=self.SAMPLES,
                      seed=0, real_weights=True)
        real_gemms = [r for r in reports if r.real_w]
        self.assertGreaterEqual(len(real_gemms), 8, "应有 ≥8 个 GEMM 用真权重（q/k/v/o/gate/up/down/lm_head）")
        for r in real_gemms:
            self.assertEqual(r.bit_exact_fail, 0, f"{r.gemm.name}(real): 须 bit-exact")
            self.assertEqual(r.nan + r.overflow, 0,
                             f"{r.gemm.name}(real): 真权重 fp16 dot 不应溢出/NaN")
            self.assertLess(r.max_rel_err, 1e-2, f"{r.gemm.name}(real): rel-err 超界")


if __name__ == "__main__":
    unittest.main()
