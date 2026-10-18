# -*- coding: utf-8 -*-
"""run_multi_stage_staged.sh + hbm_preload.py 新增子命令（backup/stage/
restore-backup）测试（2026-10-15，用户批准 staging 纳入受控运行入口）。

覆盖用户裁定的固定序与响亮失败要求：
  独占共享预载资源 → 备份 → 装载当前 case 输入 → 逐文件校验 →
  仿真与比较 → 无论成功失败都恢复并校验；
  缺输入 / 校验失败 / 恢复失败必须明确报错退出，不继续、不静默成功。
"""
import fcntl
import hashlib
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "verification_v2" / "tools"
sys.path.insert(0, str(TOOLS))
import hbm_preload as hp  # noqa: E402

WRAPPER = TOOLS / "run_multi_stage_staged.sh"
OUT = lambda *_: None  # noqa: E731

NAMES = [f"memory_init{i}.hex" for i in range(1, 9)]


def _fill(d: Path, tag: str):
    d.mkdir(parents=True, exist_ok=True)
    for i, n in enumerate(NAMES, 1):
        (d / n).write_text(f"{tag}{i:02x}deadbeef\n")


def _digest(d: Path) -> str:
    h = hashlib.sha256()
    for n in NAMES:
        h.update((d / n).read_bytes())
    return h.hexdigest()


# ── hbm_preload 新子命令（直接函数级）─────────────────────────────────

def test_backup_happy(tmp_path):
    hbm = tmp_path / "hbm"; _fill(hbm, "aa")
    bak = tmp_path / "bak"
    assert hp.backup(str(hbm), str(bak), out=OUT) == 0
    assert (bak / "SHA256SUMS").is_file()
    assert _digest(bak) == _digest(hbm)


def test_backup_refuses_missing_source(tmp_path):
    hbm = tmp_path / "hbm"; _fill(hbm, "aa")
    os.remove(hbm / "memory_init5.hex")
    bak = tmp_path / "bak"
    assert hp.backup(str(hbm), str(bak), out=OUT) == 1
    assert not bak.exists()  # 不半备份


def test_backup_refuses_nonempty_dir(tmp_path):
    hbm = tmp_path / "hbm"; _fill(hbm, "aa")
    bak = tmp_path / "bak"; bak.mkdir(); (bak / "stray").write_text("x")
    assert hp.backup(str(hbm), str(bak), out=OUT) == 1


def test_stage_happy(tmp_path):
    src = tmp_path / "src"; _fill(src, "bb")
    hbm = tmp_path / "hbm"; _fill(hbm, "aa")
    assert hp.stage(str(src), str(hbm), out=OUT) == 0
    assert _digest(hbm) == _digest(src)


def test_stage_missing_source_no_partial_write(tmp_path):
    """缺任一源文件：拒跑且全局目录一字节未动（不半装载）。"""
    src = tmp_path / "src"; _fill(src, "bb")
    os.remove(src / "memory_init7.hex")
    hbm = tmp_path / "hbm"; _fill(hbm, "aa")
    before = _digest(hbm)
    assert hp.stage(str(src), str(hbm), out=OUT) == 1
    assert _digest(hbm) == before


def test_restore_backup_roundtrip(tmp_path):
    hbm = tmp_path / "hbm"; _fill(hbm, "aa")
    bak = tmp_path / "bak"
    orig = _digest(hbm)
    assert hp.backup(str(hbm), str(bak), out=OUT) == 0
    _fill(hbm, "cc")  # 模拟 staging 覆盖
    assert _digest(hbm) != orig
    assert hp.restore_backup(str(bak), str(hbm), out=OUT) == 0
    assert _digest(hbm) == orig


def test_restore_backup_tampered_refuses_and_untouched(tmp_path):
    hbm = tmp_path / "hbm"; _fill(hbm, "aa")
    bak = tmp_path / "bak"
    assert hp.backup(str(hbm), str(bak), out=OUT) == 0
    (bak / "memory_init3.hex").write_text("garbage\n")
    before = _digest(hbm)
    assert hp.restore_backup(str(bak), str(hbm), out=OUT) == 1
    assert _digest(hbm) == before


def test_wrapper_file_is_executable():
    """包装器须带执行位（2026-10-15 实战首验 exit=126 教训：
    Write 创建的脚本缺 +x，子进程测试用 `bash <path>` 调发现不了）。"""
    assert os.access(WRAPPER, os.X_OK), f"{WRAPPER} 缺执行位"


# ── 包装器（子进程，端到端固定序）─────────────────────────────────────

FAKE_RUNNER = r"""#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIM_ROOT="$(dirname "$HERE")"
sha256sum "$SIM_ROOT"/data_engine/sram/memory_init*.hex > "$SIM_ROOT/runner_seen.sha256"
if [[ "${FAKE_TAMPER_BACKUP:-0}" == "1" ]]; then
    echo "garbage" > "$STAGED_BACKUP_DIR/memory_init3.hex"
fi
exit "${FAKE_RUNNER_RC:-0}"
"""


def _mk_sim_root(tmp_path) -> Path:
    sim = tmp_path / "sim"
    _fill(sim / "data_engine" / "sram", "aa")                       # 现行全局（陈旧）
    case = sim / "test_atomic_ops" / "multi_inst_stage" / "case_x"
    _fill(case / "hbm_data", "bb")                                  # 本 case 输入
    (case / "smc_0").mkdir(parents=True)
    rd = sim / "sim_multi_stage"; rd.mkdir(parents=True)
    runner = rd / "run_sim_aiacc_top_f_multi_stage.sh"
    runner.write_text(FAKE_RUNNER)
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return sim


def _run(sim: Path, extra_env=None, **kw):
    env = dict(os.environ)
    env["SIM_ROOT"] = str(sim)
    env["TMPDIR"] = str(sim / "tmp"); (sim / "tmp").mkdir(exist_ok=True)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(WRAPPER), "multi_inst_stage/case_x/smc_0"],
        env=env, capture_output=True, text=True, **kw)


def test_wrapper_happy_stages_and_restores(tmp_path):
    sim = _mk_sim_root(tmp_path)
    orig = _digest(sim / "data_engine" / "sram")
    r = _run(sim)
    assert r.returncode == 0, r.stderr + r.stdout
    # 运行瞬间运行器看到的是本 case 数据（staging 生效）
    seen = (sim / "runner_seen.sha256").read_text()
    case_sha = hashlib.sha256(
        (sim / "test_atomic_ops/multi_inst_stage/case_x/hbm_data/memory_init1.hex")
        .read_bytes()).hexdigest()
    assert f"memory_init1.hex" in seen and case_sha in seen
    # 结束后全局目录恢复原样
    assert _digest(sim / "data_engine" / "sram") == orig
    # 成功后备份已清理
    assert not list((sim / "tmp").glob("hbm_staged_backup.*"))


def test_wrapper_missing_input_refuses_before_sim(tmp_path):
    sim = _mk_sim_root(tmp_path)
    # 删掉一个输入文件（hbm_data 目录在但不全）
    os.remove(sim / "test_atomic_ops/multi_inst_stage/case_x/hbm_data/memory_init2.hex")
    orig = _digest(sim / "data_engine" / "sram")
    r = _run(sim)
    assert r.returncode == 4          # stage 校验拒跑
    assert not (sim / "runner_seen.sha256").exists()   # 仿真未发生
    assert _digest(sim / "data_engine" / "sram") == orig  # 全局未被污染


def test_wrapper_missing_hbm_data_dir(tmp_path):
    sim = _mk_sim_root(tmp_path)
    import shutil
    shutil.rmtree(sim / "test_atomic_ops/multi_inst_stage/case_x/hbm_data")
    r = _run(sim)
    assert r.returncode == 3
    assert not (sim / "runner_seen.sha256").exists()


def test_wrapper_runner_failure_still_restores(tmp_path):
    sim = _mk_sim_root(tmp_path)
    orig = _digest(sim / "data_engine" / "sram")
    r = _run(sim, {"FAKE_RUNNER_RC": "1"})
    assert r.returncode == 5
    assert _digest(sim / "data_engine" / "sram") == orig


def test_wrapper_restore_failure_is_loud_exit7(tmp_path):
    sim = _mk_sim_root(tmp_path)
    r = _run(sim, {"FAKE_TAMPER_BACKUP": "1"})
    assert r.returncode == 7
    assert "CRITICAL" in r.stderr + r.stdout
    # 备份留存供人工处置
    assert list((sim / "tmp").glob("hbm_staged_backup.*"))


def test_wrapper_serializes_on_shared_lock(tmp_path):
    """同一锁文件被持有时，包装器必须阻塞等待（串行占用共享预载目录）。"""
    sim = _mk_sim_root(tmp_path)
    lock = sim / "data_engine" / "sram" / ".m3lite_gate3.flock"
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    env = dict(os.environ)
    env["SIM_ROOT"] = str(sim)
    env["TMPDIR"] = str(sim / "tmp"); (sim / "tmp").mkdir(exist_ok=True)
    p = subprocess.Popen(
        ["bash", str(WRAPPER), "multi_inst_stage/case_x/smc_0"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(3)
    assert p.poll() is None          # 锁被持有 ⇒ 仍在等待，未抢跑
    fcntl.flock(fd, fcntl.LOCK_UN)
    out, _ = p.communicate(timeout=30)
    os.close(fd)
    assert p.returncode == 0, out
