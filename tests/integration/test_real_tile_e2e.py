"""真权重 tile → 实际算子 e2e 守门（单-SMC K=8 dot）。

把真 TinyLlama 权重 tile 喂进 `fp16_matmul_min --mode e2e --input-file`，跑**完整固件+sim 数据通路**
（非 get_golden 算术 oracle），守 golden parity bit-perfect。仅当权重已缓存才跑（CI 离线自动 skip、
不触发下载）。本地补缓存：python3 verification_v2/tools/llm_checkpoint_weights.py --model tinyllama-1.1b

慢测（subprocess e2e）；取 2 个 GEMM ×1 tile 控时长。规模化 tile 算术由 test_llm_matmul_tiles 覆盖。"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TestRealTileE2E(unittest.TestCase):
    MODEL = "tinyllama-1.1b"

    def test_real_weight_tiles_pass_fp16_matmul_min_e2e(self):
        from verification_v2.tools.llm_checkpoint_weights import CACHE_DIR
        if not (CACHE_DIR / f"{self.MODEL}.npz").exists():
            self.skipTest("真权重未缓存（离线/CI）；本地跑 llm_checkpoint_weights.py 补缓存后启用")
        from verification_v2.tools.run_real_tile_e2e import run
        # 2 个代表 GEMM × 1 tile：gate_proj（FFN 小权重）+ lm_head（输出投影）
        results = run(self.MODEL, per_gemm=1, seed=0, gemms=["gate_proj", "lm_head"])
        self.assertEqual(set(results), {"gate_proj", "lm_head"}, "应跑指定 2 个 GEMM")
        for gemm, (passed, failed) in results.items():
            self.assertEqual(failed, 0, f"{gemm}: 真权重 tile e2e 应全 PASS（固件+sim 数据通路），得 {failed} FAIL")
            self.assertGreaterEqual(passed, 1, f"{gemm}: 应至少 1 个 tile 通过")

    def test_real_weight_cols_pass_fp16_matmul_n_e2e(self):
        """multi-SMC N=8：32 个真权重列（8 SMC×4 列）→ fp16_matmul_n e2e，跑真 N=8 跨-SMC 数据通路
        （SHIFTUP 广播 fan-out + 每 SMC 本地权重 + addtree），守 8 SMC golden bit-perfect。
        依赖 pipeline_runner external_input（--input-file honor multi-SMC 注入）。"""
        from verification_v2.tools.llm_checkpoint_weights import CACHE_DIR
        if not (CACHE_DIR / f"{self.MODEL}.npz").exists():
            self.skipTest("真权重未缓存（离线/CI）；本地跑 llm_checkpoint_weights.py 补缓存后启用")
        from verification_v2.tools.run_real_tile_e2e import run
        results = run(self.MODEL, per_gemm=1, seed=0, gemms=["gate_proj"], variant="n8")
        passed, failed = results["gate_proj"]
        self.assertEqual(failed, 0, f"真权重列 N=8 e2e 应全 PASS（8 SMC bit-perfect），得 {failed} FAIL")
        self.assertGreaterEqual(passed, 1, "应至少 1 个 N=8 tile 通过")

    def test_real_weight_cols_pass_fp16_matmul_q_dst_e2e(self):
        """dst 变体：同 npz（a + b0..b3）但走 **SMI_DS_T 转置前端**（激活列对齐 gather→转置 1×8→
        SHIFTUP 广播→每 SMC dot），真权重过最长真数据通路。
        🔴 ADR-047/048（2026-07-02）：本 dst compute datapath（SMI_DS_T 广播 + fp16mul load 微指令 + s_bank）
        **不被 model-A sim 建模**（sim 直读 dvr、对 load 微指令/DST 流全盲 → sim golden-parity 必 FAIL）。
        真验收走 **VCS bank5 逐词 bit-match**（已 8/8 SMC × 4 lane bit-match golden，见 ADR-048）。
        故本 sim-parity 用例 **skip**（sim 无有效 oracle，非假绿；DEBT-047-SIM-BLIND，增强 sim 建模后再启用）。"""
        self.skipTest("q_dst dst datapath sim model-A 全盲（load 微指令/SMI_DS_T 流未建模）；"
                      "验收走 VCS bit-match（ADR-048 已 8/8），DEBT-047-SIM-BLIND")

    def test_real_weight_cols_pass_fp16_matmul_full_dst_e2e(self):
        """full_dst 变体：M=8 多行流式（逐行广播 M-loop）+ SMI_DS_T 多行转置前端，真权重 → M×N×COLS=256
        输出。npz 仅 a 用 M*K=64（8 激活行），权重 b0..b3 同。守 8 SMC golden bit-perfect（终值 parity；
        跨-SMC 逐拍时序忠实度 = ADR-036 proposed，待 VCS）。"""
        from verification_v2.tools.llm_checkpoint_weights import CACHE_DIR
        if not (CACHE_DIR / f"{self.MODEL}.npz").exists():
            self.skipTest("真权重未缓存（离线/CI）；本地跑 llm_checkpoint_weights.py 补缓存后启用")
        from verification_v2.tools.run_real_tile_e2e import run
        results = run(self.MODEL, per_gemm=1, seed=0, gemms=["gate_proj"], variant="full_dst")
        passed, failed = results["gate_proj"]
        self.assertEqual(failed, 0, f"真权重 full_dst（M=8 多行+转置）e2e 应全 PASS，得 {failed} FAIL")
        self.assertGreaterEqual(passed, 1, "应至少 1 个 full_dst tile 通过")


if __name__ == "__main__":
    unittest.main()
