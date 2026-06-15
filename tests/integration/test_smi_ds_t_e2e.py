"""ADR-036 S1 e2e 退出码守门（hco a4f6b70 BLOCK 的权威回归护栏）。

背景：S1 给 SMI_DS_T 算子注入 structural_only 节点（ds_src_read，SMT 排程锚点、不进 emit/sim）。
若 truth auditor 未与 emit 同步过滤该节点，则对没 emit 的节点假阳 → `--mode e2e` 返回 exit=1
（数值仍 bit-perfect，但 Truth Audit 假红）。s1_smt_check.py 绕 sim、pytest 单测查源码不变量，
**唯有真跑 `--mode e2e` 看退出码**能端到端兜住这个缺口——正是本测试。

慢测（每算子走 SMT→hex→sim→audit 全链，~20s+）；CI 必跑，本地可 -k 选跑。
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent


class TestSmiDsTE2EExitCode(unittest.TestCase):
    """三个带 SMI_DS_T 转置前端的算子 `--mode e2e` 必须 exit=0（含 Truth Audit）。"""

    def _run_e2e(self, op: str, env_extra: dict = None):
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", op, "--mode", "e2e"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=300, env=env,
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


class TestSmiDsTS3aRealGatherAB(unittest.TestCase):
    """ADR-036 S3-a：fp16_matmul_full_dst real-gather（去 gather backdoor，走真固件 bank6 UR 读）。
    A/B 护栏（hco APPROVE-with-notes）：AIACC_DST_REAL_GATHER ON vs OFF 输出逐 bit 一致 + 均 exit=0。
    ON 路径 ds_src 与广播 a_read 共享 port0（solver 实测），是 DEBT-032 同款真 clobber 场景——
    8/8 bit-perfect 即 Q3 捕获寄存器鲁棒性的端到端忠实证明。"""

    def _run(self, env_extra=None):
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", "fp16_matmul_full_dst",
             "--mode", "e2e"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=300, env=env,
        )
        return proc.returncode, proc.stdout + proc.stderr

    @staticmethod
    def _smc_outputs(out: str):
        # 提取每行 [SMC_n] ... | SMC=[...] 的 SMC 数值串，作逐 bit A/B 比对锚点
        return re.findall(r"\[SMC_\d\][^\n]*\|\s*SMC=\[[^\]]*\]", out)

    def test_on_exit0_and_bit_identical_to_off(self):
        rc_off, out_off = self._run()                                  # 默认 OFF（backdoor）
        self.assertEqual(rc_off, 0, f"OFF e2e 应 exit=0，得 {rc_off}\n{out_off[-1500:]}")
        rc_on, out_on = self._run({"AIACC_DST_REAL_GATHER": "1"})       # ON（真固件 gather）
        self.assertEqual(rc_on, 0, f"ON e2e 应 exit=0，得 {rc_on}\n{out_on[-1500:]}")
        self.assertIn("ALL PHYSICAL CHECKS PASSED", out_on)
        # ON 须真走 gather 路径——验 build 产物（sim log 写文件、不在 console stdout）：
        #   ① 固件含真 BANK=6 ds_src_read 读指令（非 structural_only 被过滤）；
        #   ② sim_config 带 src_read_port（cluster 据此走端口快照 gather 而非 storage backdoor）；
        #   ③ sim log 有 DST-GATHER 端口读事件。
        import json
        bdir = _ROOT / "build" / "verification_v2" / "fp16_matmul_full_dst"
        instrs = json.loads((bdir / "physical_instructions.json").read_text())
        b6 = [i for i in instrs if i.get("payload", {}).get("BANK") == 6
              and i.get("trace_id") == "ds_src_read"]
        self.assertEqual(len(b6), 1, "ON 固件须含 1 条 BANK=6 ds_src_read 读指令（真固件读，非 backdoor）")
        cfg = json.loads((bdir / "sim_config.json").read_text())
        self.assertIsNotNone(cfg.get("smi_ds_t", {}).get("src_read_port"),
                             "ON sim_config 须带 src_read_port（cluster 走端口 gather）")
        self.assertIsNotNone(cfg.get("dst_src_capture"), "ON 须配 dst_src_capture 捕获寄存器")
        sim_log = (bdir / "fp16_matmul_full_dst_sim.log").read_text()
        self.assertIn("Tag:DST-GATHER", sim_log, "ON sim log 应有 DST-GATHER 端口读事件")
        # A/B 逐 bit：两路 SMC 输出串完全一致（real-gather vs backdoor）
        off_smc, on_smc = self._smc_outputs(out_off), self._smc_outputs(out_on)
        self.assertTrue(off_smc, "未解析到 OFF 的 SMC 输出")
        self.assertEqual(off_smc, on_smc, "real-gather(ON) 与 backdoor(OFF) 输出应逐 bit 一致")


if __name__ == "__main__":
    unittest.main()
