"""ADR-036 S1 e2e 退出码守门（hco a4f6b70 BLOCK 的权威回归护栏）。

背景：S1 给 SMI_DS_T 算子注入 structural_only 节点（ds_src_read，SMT 排程锚点、不进 emit/sim）。
若 truth auditor 未与 emit 同步过滤该节点，则对没 emit 的节点假阳 → `--mode e2e` 返回 exit=1
（数值仍 bit-perfect，但 Truth Audit 假红）。s1_smt_check.py 绕 sim、pytest 单测查源码不变量，
**唯有真跑 `--mode e2e` 看退出码**能端到端兜住这个缺口——正是本测试。

慢测（每算子走 SMT→hex→sim→audit 全链，~20s+）；CI 必跑，本地可 -k 选跑。
"""
import subprocess
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent


class TestSmiDsTE2EExitCode(unittest.TestCase):
    """三个带 SMI_DS_T 转置前端的算子 `--mode e2e` 必须 exit=0（含 Truth Audit）。"""

    def _run_e2e(self, op: str):
        proc = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", op, "--mode", "e2e"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def test_matmul_q_dst_e2e_exit0(self):
        rc, out = self._run_e2e("matmul_q_dst")
        self.assertEqual(rc, 0, f"matmul_q_dst --mode e2e 应 exit=0，得 {rc}\n{out[-2000:]}")
        self.assertIn("ALL PHYSICAL CHECKS PASSED", out)

    def test_matmul_q_dst_k8_e2e_exit0(self):
        rc, out = self._run_e2e("matmul_q_dst_k8")
        self.assertEqual(rc, 0, f"matmul_q_dst_k8 --mode e2e 应 exit=0，得 {rc}\n{out[-2000:]}")

    def test_fp16_matmul_q_dst_e2e_exit0(self):
        rc, out = self._run_e2e("fp16_matmul_q_dst")
        self.assertEqual(rc, 0, f"fp16_matmul_q_dst --mode e2e 应 exit=0，得 {rc}\n{out[-2000:]}")


if __name__ == "__main__":
    unittest.main()
