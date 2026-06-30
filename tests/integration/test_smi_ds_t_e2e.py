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


class TestFp16MultiTileK16E2E(unittest.TestCase):
    """DEBT-025-MULTITILE 首步（hco APPROVE 候选①）：fp16 K=16 两-tile 累加 e2e。
    真数据通路验「8to1 片内 + 5to1 accumulate 片间」（§1193）—— 8to1/5to1 首次同 DFG 共存 +
    5to1 累加位真生效（sim 出 t0+t1 而非 naive 的 t1-only）。golden bit-perfect + II≥4。"""

    def _run_e2e(self, op: str):
        proc = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", op, "--mode", "e2e"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=300, env=dict(os.environ))
        return proc.returncode, proc.stdout + proc.stderr

    def test_fp16_matmul_k16_e2e_exit0(self):
        rc, out = self._run_e2e("fp16_matmul_k16")
        self.assertEqual(rc, 0, f"fp16_matmul_k16 e2e 应 exit=0（2-tile 累加 bit-perfect）\n{out[-2000:]}")
        self.assertIn("PASSED y", out, "golden parity 应 PASS（5to1 accumulate 片间累加生效）")


class TestMatmulMultiChannelDenseGolden(unittest.TestCase):
    """ADR-026 Stage-2 / ADR-036 S3：多通道 dense matmul（M 行 → addtree 独立 lane S0..S{M-1}，
    破 matmul_q 的 pipeline-depth M≤4 hack）的**最终 golden parity** 守门。

    `matmul_q_dst_full`（K=8、M=4、cumulative 跨 2 tile、4 通道 dense 输出 [Q0..Q3]）compute
    bit-perfect，但 `--mode e2e` **exit=1**——per-op Truth Audit 对多通道中间值假阳（FP32-only +
    非拓扑序求值，CLAUDE.md「正确性以最终 golden parity 为准」+ fma bug#4 同源）。故本测**不**断言
    exit=0，断言**项目真验收标准 = 最终 8/8 SMC golden bit-perfect**（physical_runner 输出行）。
    专杀：若多通道 compute/通道串扰回归 → golden parity 转 FAILED，本测立挂。
    （注：M>4 真数据通路受「多行激活驻留交付」墙，非本测范围；见会话分析 + DEBT-036-K8COMPUTE。）"""

    def _run_e2e(self, op: str):
        proc = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", op, "--mode", "e2e"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=300, env=dict(os.environ))
        return proc.returncode, proc.stdout + proc.stderr

    def test_matmul_q_dst_full_golden_bit_perfect(self):
        _rc, out = self._run_e2e("matmul_q_dst_full")
        self.assertIn("PASSED y: All 8 SMC outputs bit-perfect", out,
                      f"matmul_q_dst_full 多通道 dense compute 应 8/8 SMC golden bit-perfect\n{out[-2500:]}")
        self.assertNotIn("FAILED y:", out, f"golden parity 不应 FAILED（多通道串扰回归）\n{out[-2500:]}")


class TestDocFlowUnitE2E(unittest.TestCase):
    """文档钦定流程的 8-SMC 功能测例 e2e 守护（VCS 对齐基础，自底向上）。
    - unit_fpadd：Flow A 单一计算指令（FPADD，文档 para 4828）；每 SMC Bank0+Bank1→Bank2。
    - unit_ds_transpose：Flow B 数据整理 datapath（para 4676，UR read—SHIFTDN—SMI_DS_T—SHIFTUP—UR write）。
    锁 exit=0（含 y bit-perfect + 固件完整性门）。"""

    def _run_e2e(self, op: str):
        proc = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", op, "--mode", "e2e"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=300, env=dict(os.environ),
        )
        return proc.returncode, proc.stdout + proc.stderr

    def test_unit_fpadd_e2e_exit0(self):
        rc, out = self._run_e2e("unit_fpadd")
        self.assertEqual(rc, 0, f"unit_fpadd --mode e2e 应 exit=0，得 {rc}\n{out[-2000:]}")
        self.assertIn("ALL PHYSICAL CHECKS PASSED", out)

    def test_unit_ds_transpose_e2e_firmware_and_integrity(self):
        """ADR-041 DEBT-041-STREAM-DLY 突破后（2026-06-30）：unit_ds_transpose dst-datapath 改走 **N 条显式
        EXTRA_DATA/lead-NOP 流**（RTL 忠实 emit）。该流不被 model-A sim 建模（feed 逐拍就绪时序非真 →
        sim 取零 → sim-parity 必 0/8 FAIL），故**不再断言 exit=0/y bit-perfect**——验收改走 **VCS bank5
        逐行 bit-match**（fp16 载体已 8/8 SMC × 16/16 行 bit-perfect，见 ADR-041；fp32 hi-slot 待
        DEBT-038-B2-GATHER）。本测仍守 model-A 能验的**结构正确性**：① Header Audit（stream-aware）SUCCESS、
        ② 固件完整性门 SUCCESS（cross-SMC datapath 无 backdoor）。sim-parity 失败是已知 model-A 局限、非回归。"""
        rc, out = self._run_e2e("unit_ds_transpose")
        self.assertIn("Header Audit SUCCESS", out,
                      f"unit_ds_transpose Header Audit 应 PASS（stream IQ skip-audit）\n{out[-2000:]}")
        self.assertIn("Firmware Integrity Gate: SUCCESS", out,
                      f"unit_ds_transpose 固件完整性门应 PASS（cross-SMC datapath 无 backdoor）\n{out[-2000:]}")


class TestSmiDsTChainDrain(unittest.TestCase):
    """ADR-036 S4（方案 A，hco APPROVE-with-notes）：SHIFTDN gather 逐-SMC chain drain 时序。
    DST-SHIFTDN 事件逐-SMC 错拍（每 hop 1 latency），非同拍 burst；总 drain 跨度守恒于 SMI_DS_T latency。"""

    def _sim_log(self, op):
        proc = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", op, "--mode", "e2e"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=300, env=dict(os.environ))
        self.assertEqual(proc.returncode, 0, f"{op} e2e 应 exit=0，得 {proc.returncode}")
        log = _ROOT / "build" / "verification_v2" / op / f"{op}_sim.log"
        return log.read_text()

    @staticmethod
    def _cycs(text, tag):
        return sorted({int(m) for m in re.findall(rf"Cyc:(\d+)\b[^\n]*Tag:{tag}\b", text)}) or \
               sorted({int(re.search(r"Cyc:(\d+)", l).group(1))
                       for l in text.splitlines() if f"Tag:{tag}" in l and "Cyc:" in l})

    def test_dst_shiftdn_staggered_by_gather_cnt(self):
        """matmul_q_dst（K=4）：DST-SHIFTDN 逐-SMC 错拍 1 拍、跨 gather_cnt=4 拍（非同拍 burst）。"""
        text = self._sim_log("matmul_q_dst")
        cyc = self._cycs(text, "DST-SHIFTDN")
        self.assertEqual(len(cyc), 4, f"K=4 应 4 个 DST-SHIFTDN drain 拍（gather_cnt），得 {cyc}")
        self.assertEqual(cyc, list(range(cyc[0], cyc[0] + 4)),
                         f"DST-SHIFTDN 须逐拍连续错拍（每 hop 1 latency），得 {cyc}")

    def test_drain_conserves_smi_ds_t_latency(self):
        """守恒（hco 约束1）：DST-SHIFTUP 输出 drain 起点 − DST-SHIFTDN gather 起点 == SMI_DS_T RAW 边权
        —— 逐拍 drain 只把已有延迟渲染成可观测事件，不 double-count、不漂 compute barrier。
        ADR-041 D3（hco 二轮 RTL 取证 + VCS 对账）：RAW 边权 = collect(B×WR_BEATS=2×8=16) + drain(3) = 19；
        订正旧 16（那是**输出窗口** SMI_DS_T_WINDOW=OUT_ROWS×2，被误当 RAW latency）。"""
        text = self._sim_log("matmul_q_dst")
        dn = self._cycs(text, "DST-SHIFTDN")
        up = self._cycs(text, "DST-SHIFTUP")
        self.assertTrue(dn and up, "应有 DST-SHIFTDN + DST-SHIFTUP 事件")
        self.assertEqual(up[0] - dn[0], 19,
                         f"输出 drain 起点须 = gather 起点 + SMI_DS_T RAW 边权(19)；得 {up[0]}-{dn[0]}={up[0]-dn[0]}")

    def test_smc_id_en_gates_ds_src_read(self):
        """DEBT-036-SMCEN-SIM-GATE：ds_src_read(smc_id_en=gather_cnt-1=3) 时 sim 只让 SMC 0-3 读 bank4
        （不再 SMC4-7 读零）；compute 算子(smc_id_en=7) 仍全 8 SMC 读写（缺省不门控）。"""
        import re as _re
        text = self._sim_log("matmul_q_dst")
        bank4 = sorted({int(m.group(1)) for l in text.splitlines() if "Tag:READ" in l and "Bank:4 " in l
                        for m in [_re.search(r"SMC_ID:(\d+)", l)] if m})
        self.assertEqual(bank4, [0, 1, 2, 3],
                         f"K=4 时 ds_src_read 只应 SMC 0-3 读 bank4（smc_id_en=3 门控），得 {bank4}")
        text2 = self._sim_log("matmul_parallel_n")
        bank5 = sorted({int(m.group(1)) for l in text2.splitlines() if "Tag:WRITE" in l and "Bank:5 " in l
                        for m in [_re.search(r"SMC_ID:(\d+)", l)] if m})
        self.assertEqual(bank5, list(range(8)),
                         f"compute 算子(smc_id_en=7) 仍全 8 SMC 写，得 {bank5}")


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
        import json
        bdir = _ROOT / "build" / "verification_v2" / "fp16_matmul_full_dst"
        rc_off, out_off = self._run({"AIACC_DST_REAL_GATHER": "0"})    # 显式 OFF（backdoor；S3-c block-1 起默认已 ON）
        self.assertEqual(rc_off, 0, f"OFF e2e 应 exit=0，得 {rc_off}\n{out_off[-1500:]}")
        # OFF 对称守门（hco 可选建议）：OFF 路径 scatter 仍走 bank0 staging（UR-WRITE）——验双路 else 分支未被误删。
        # 须在 ON 跑前读（ON 会覆盖同一 build 目录）。
        off_scatter = [l for l in (bdir / "fp16_matmul_full_dst_sim.log").read_text().splitlines()
                       if "Tag:DST-SHIFTUP" in l]
        self.assertTrue(any("Unit:UR-WRITE" in l for l in off_scatter),
                        "OFF 路径 scatter 应仍是 UR-WRITE bank0 staging（守双路 else 分支）")
        rc_on, out_on = self._run({"AIACC_DST_REAL_GATHER": "1"})       # ON（真固件 gather）
        self.assertEqual(rc_on, 0, f"ON e2e 应 exit=0，得 {rc_on}\n{out_on[-1500:]}")
        self.assertIn("ALL PHYSICAL CHECKS PASSED", out_on)
        # S3-c block-3：固件完整性门在 ON fp16（real_gather + fp16）路径**被调用且 PASS**——
        # 防 hco note「门被 fp16 truth-audit skip 旁路」（门是独立顶层方法、不受 DEBT-FP16-AUDITOR 影响）。
        self.assertIn("Firmware Integrity Gate: SUCCESS", out_on,
                      "ON fp16 路径固件完整性门须被调用且 PASS（不被 fp16 skip 旁路）")
        # ON 须真走 gather 路径——验 build 产物（sim log 写文件、不在 console stdout）：
        #   ① 固件含真 BANK=6 ds_src_read 读指令（非 structural_only 被过滤）；
        #   ② sim_config 带 src_read_port（cluster 据此走端口快照 gather 而非 storage backdoor）；
        #   ③ sim log 有 DST-SHIFTDN 端口读事件。
        instrs = json.loads((bdir / "physical_instructions.json").read_text())
        b6 = [i for i in instrs if i.get("payload", {}).get("BANK") == 6
              and i.get("trace_id") == "ds_src_read"]
        self.assertEqual(len(b6), 1, "ON 固件须含 1 条 BANK=6 ds_src_read 读指令（真固件读，非 backdoor）")
        cfg = json.loads((bdir / "sim_config.json").read_text())
        self.assertIsNotNone(cfg.get("smi_ds_t", {}).get("src_read_port"),
                             "ON sim_config 须带 src_read_port（cluster 走端口 gather）")
        self.assertIsNotNone(cfg.get("dst_src_capture"), "ON 须配 dst_src_capture 捕获寄存器")
        sim_log = (bdir / "fp16_matmul_full_dst_sim.log").read_text()
        self.assertIn("Tag:DST-SHIFTDN", sim_log, "ON sim log 应有 DST-SHIFTDN 端口读事件")
        # S3-b（full fan-out）：scatter 去 backdoor —— 转置行经 SHIFTUP-BCAST 直喂、删 bank0 staging 写。
        self.assertTrue(cfg.get("smi_ds_t", {}).get("scatter_to_feed"),
                        "ON sim_config 须带 scatter_to_feed（scatter 走 SHIFTUP 广播输入缓冲）")
        self.assertIsNotNone(cfg.get("dst_bcast_feed"), "ON 须配 dst_bcast_feed")
        self.assertIn("Unit:SHIFTUP-BCAST", sim_log, "ON scatter 应走 SHIFTUP-BCAST（非 UR-WRITE bank0）")
        scatter_lines = [l for l in sim_log.splitlines() if "Tag:DST-SHIFTUP" in l]
        self.assertTrue(scatter_lines, "应有 DST-SHIFTUP 事件")
        self.assertFalse(any("Unit:UR-WRITE" in l for l in scatter_lines),
                         "S3-b：scatter 不应再是 UR-WRITE bank0（bank0 staging 已删）")
        # BLOCK-Q2：a_read 仍发真 bank0 读指令（trigger + addr 索引，未退化成 phantom）
        a_read = [i for i in instrs if i.get("trace_id") == "a_read"
                  and i.get("payload", {}).get("BANK") == 0]
        self.assertTrue(a_read, "BLOCK-Q2：a_read 须仍发真 BANK=0 读指令（占端口、arbiter 可见）")
        # A/B 逐 bit：两路 SMC 输出串完全一致（real-datapath vs backdoor）
        off_smc, on_smc = self._smc_outputs(out_off), self._smc_outputs(out_on)
        self.assertTrue(off_smc, "未解析到 OFF 的 SMC 输出")
        self.assertEqual(off_smc, on_smc, "real-datapath(ON) 与 backdoor(OFF) 输出应逐 bit 一致")


if __name__ == "__main__":
    unittest.main()
