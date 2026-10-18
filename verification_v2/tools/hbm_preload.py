#!/usr/bin/env python3
"""hbm_preload.py —— M3-lite 门③ 任务 1（用户 2026-10-07 授权）：
全局 HBM 预载（sandbox data_engine/sram/memory_init1..8.hex）的自动恢复
与校验，消除人工恢复步骤。

动机（ADR-060 errata ⑥/⑦）：drive-3 单独重跑绕过 run.sh，未恢复 case
冻结快照 → 以 a5a5 旧预载跑出数据面假红（污染运行）。用户裁定：
「run.sh 每次自动恢复对应 snapshot、校验哈希、串行占用共享预载目录」；
「依赖正确初态的结论，只能由恢复预载后的干净运行支撑」。

子命令（均可独立调用，返回码 0=OK / 1=不一致或拒跑 / 2=用法或缺前提）：

  check    只读比对 sandbox 现行 memory_init1..8.hex 与 case 冻结快照
           （memory_init_snapshot/），并校快照与 g3_expect.json 内
           hbm_preload.files 冻结哈希（若存在）的一致性；任一不符 exit 1。
  restore  先校快照 vs expect 冻结哈希（防快照随初态一并被篡改），
           再逐文件复制快照 → 全局目录，复制后复读复核 sha256；
           任一失败即拒（exit 1），调用方不得继续跑 VCS。

2026-10-15 增补（用户批准「staging 纳入受控运行入口」，供
run_multi_stage_staged.sh 及后续运行器使用；既有子命令语义不变）：

  backup          现行全局 8 文件 → --backup-dir + SHA256SUMS，复制后逐文件
                  复核；源缺失或复核失败 exit 1（不半备份）。
  stage           --src-dir 内 memory_init1..8.hex → 全局目录；先查 8 源齐备
                  （缺任一即 exit 1，不写任何文件=不半装载），再逐文件复制+
                  复读复核。
  restore-backup  --backup-dir → 全局目录：先按 SHA256SUMS 校备份自身完整
                  （防备份被一并污染），再逐文件复制+复核。任一失败 exit 1
                  ——此档最重：全局目录可能仍持 case 数据，须人工处置。

纪律：本工具是 run.sh 之外唯一受支持的预载写入路径；单独重跑必须经
m3lite_gate3_rerun_leg.sh（内部调本工具 restore）。快照比对不上 expect
冻结哈希时拒绝恢复——那意味着冻结证据本身已不可信，须人工核查。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

EXPECTED_FILES = [f"memory_init{i}.hex" for i in range(1, 9)]
DEFAULT_HBM_DIR = os.path.expanduser("~/u280_sim_v422/data_engine/sram")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_frozen_hashes(case_dir: str):
    """g3_expect.json 的 hbm_preload.files 冻结哈希；无该键（旧 case）返回 None。"""
    exp_path = os.path.join(case_dir, "g3_expect.json")
    if not os.path.isfile(exp_path):
        return None
    with open(exp_path, encoding="utf-8") as f:
        exp = json.load(f)
    hp = exp.get("hbm_preload")
    if not isinstance(hp, dict) or not isinstance(hp.get("files"), dict):
        return None
    return hp["files"]


def _snapshot_dir(case_dir: str) -> str:
    return os.path.join(case_dir, "memory_init_snapshot")


def check(case_dir: str, hbm_dir: str, out=print) -> int:
    """只读三方比对：现行全局文件 vs 冻结快照 vs expect 冻结哈希（若有）。"""
    snap = _snapshot_dir(case_dir)
    frozen = load_frozen_hashes(case_dir)
    bad = 0
    if not os.path.isdir(snap):
        out(f"[HBM-CHK] RED 缺快照目录：{snap}")
        return 1
    for name in EXPECTED_FILES:
        sp = os.path.join(snap, name)
        cp = os.path.join(hbm_dir, name)
        if not os.path.isfile(sp):
            out(f"[HBM-CHK] {name} MISSING-SNAPSHOT（{sp}）")
            bad += 1
            continue
        s_sha = sha256_file(sp)
        if frozen is not None and frozen.get(name) not in (None, s_sha):
            out(f"[HBM-CHK] {name} SNAPSHOT-TAMPER：快照 {s_sha[:16]}… ≠ "
                f"expect 冻结 {frozen[name][:16]}…（冻结证据不可信，人工核查）")
            bad += 1
            continue
        if not os.path.isfile(cp):
            out(f"[HBM-CHK] {name} MISSING-CURRENT（{cp}）")
            bad += 1
            continue
        c_sha = sha256_file(cp)
        if c_sha == s_sha:
            out(f"[HBM-CHK] {name} OK（{s_sha[:16]}…）")
        else:
            out(f"[HBM-CHK] {name} STALE：现行 {c_sha[:16]}… ≠ 快照 {s_sha[:16]}…")
            bad += 1
    out(f"[HBM-CHK] 总判：{'CLEAN（全局预载 == 冻结快照）' if bad == 0 else f'STALE/TAMPER（{bad} 项不符）'}")
    return 0 if bad == 0 else 1


def restore(case_dir: str, hbm_dir: str, out=print) -> int:
    """快照 → 全局目录。先验快照可信（对 expect 冻结哈希），复制后逐文件复核。"""
    snap = _snapshot_dir(case_dir)
    if not os.path.isdir(snap):
        out(f"[HBM-RESTORE] RED 缺快照目录：{snap}——拒跑")
        return 1
    frozen = load_frozen_hashes(case_dir)
    for name in EXPECTED_FILES:
        sp = os.path.join(snap, name)
        if not os.path.isfile(sp):
            out(f"[HBM-RESTORE] RED 快照不全：缺 {name}——拒跑（不半恢复）")
            return 1
        if frozen is not None:
            s_sha = sha256_file(sp)
            if frozen.get(name) != s_sha:
                out(f"[HBM-RESTORE] RED 快照与 expect 冻结哈希不符（{name}："
                    f"{s_sha[:16]}… ≠ {str(frozen.get(name))[:16]}…）"
                    f"——冻结证据不可信，拒跑，人工核查")
                return 1
    os.makedirs(hbm_dir, exist_ok=True)
    for name in EXPECTED_FILES:
        sp = os.path.join(snap, name)
        dp = os.path.join(hbm_dir, name)
        shutil.copyfile(sp, dp)
        s_sha, d_sha = sha256_file(sp), sha256_file(dp)
        if d_sha != s_sha:
            out(f"[HBM-RESTORE] RED 复制后复核失败（{name}）——拒跑")
            return 1
        out(f"[HBM-RESTORE] {name} OK（{s_sha[:16]}…）")
    out(f"[HBM-RESTORE] 总判：RESTORED+VERIFIED（8/8 == 冻结快照，case="
        f"{os.path.basename(os.path.abspath(case_dir))}）")
    return 0


def _copy_verify(sp: str, dp: str, tag: str, out=print) -> bool:
    shutil.copyfile(sp, dp)
    s_sha, d_sha = sha256_file(sp), sha256_file(dp)
    if d_sha != s_sha:
        out(f"[{tag}] RED 复制后复核失败（{os.path.basename(dp)}）："
            f"{d_sha[:16]}… ≠ {s_sha[:16]}…")
        return False
    return True


def backup(hbm_dir: str, backup_dir: str, out=print) -> int:
    """现行全局 8 文件 → backup_dir（须不存在或为空）+ SHA256SUMS。"""
    for name in EXPECTED_FILES:
        if not os.path.isfile(os.path.join(hbm_dir, name)):
            out(f"[HBM-BACKUP] RED 全局目录缺 {name}（{hbm_dir}）——拒跑")
            return 1
    if os.path.isdir(backup_dir) and os.listdir(backup_dir):
        out(f"[HBM-BACKUP] RED 备份目录非空（{backup_dir}）——拒覆盖，人工核查")
        return 1
    os.makedirs(backup_dir, exist_ok=True)
    sums = []
    for name in EXPECTED_FILES:
        sp = os.path.join(hbm_dir, name)
        dp = os.path.join(backup_dir, name)
        if not _copy_verify(sp, dp, "HBM-BACKUP", out):
            return 1
        sums.append(f"{sha256_file(dp)}  {name}")
    with open(os.path.join(backup_dir, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write("\n".join(sums) + "\n")
    out(f"[HBM-BACKUP] 总判：BACKED-UP 8/8 → {backup_dir}")
    return 0


def stage(src_dir: str, hbm_dir: str, out=print) -> int:
    """case 输入 → 全局目录。先查 8 源齐备（不半装载），再逐文件复制复核。"""
    for name in EXPECTED_FILES:
        if not os.path.isfile(os.path.join(src_dir, name)):
            out(f"[HBM-STAGE] RED 缺输入 {name}（{src_dir}）——拒跑，未写任何文件")
            return 1
    os.makedirs(hbm_dir, exist_ok=True)
    for name in EXPECTED_FILES:
        if not _copy_verify(os.path.join(src_dir, name),
                            os.path.join(hbm_dir, name), "HBM-STAGE", out):
            return 1
    out(f"[HBM-STAGE] 总判：STAGED+VERIFIED 8/8（{src_dir} → {hbm_dir}）")
    return 0


def restore_backup(backup_dir: str, hbm_dir: str, out=print) -> int:
    """备份 → 全局目录。先按 SHA256SUMS 校备份自身完整，再复制复核。"""
    sums_path = os.path.join(backup_dir, "SHA256SUMS")
    if not os.path.isfile(sums_path):
        out(f"[HBM-RESTORE-BACKUP] RED 缺 {sums_path}——备份不可信，拒恢复")
        return 1
    with open(sums_path, encoding="utf-8") as f:
        entries = [ln.split() for ln in f.read().splitlines() if ln.strip()]
    names = [e[1] for e in entries if len(e) == 2]
    if sorted(names) != sorted(EXPECTED_FILES):
        out(f"[HBM-RESTORE-BACKUP] RED SHA256SUMS 文件清单不符（{names}）——拒恢复")
        return 1
    for sha, name in entries:
        bp = os.path.join(backup_dir, name)
        if not os.path.isfile(bp) or sha256_file(bp) != sha:
            out(f"[HBM-RESTORE-BACKUP] RED 备份被污染/缺失（{name}）——拒恢复，"
                f"全局目录状态未知，须人工核查")
            return 1
    for name in EXPECTED_FILES:
        if not _copy_verify(os.path.join(backup_dir, name),
                            os.path.join(hbm_dir, name),
                            "HBM-RESTORE-BACKUP", out):
            out("[HBM-RESTORE-BACKUP] RED 恢复复核失败——全局目录可能处于"
                "半恢复态，须人工核查")
            return 1
    out(f"[HBM-RESTORE-BACKUP] 总判：RESTORED+VERIFIED 8/8（← {backup_dir}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--case-dir", required=True)
        p.add_argument("--hbm-dir", default=DEFAULT_HBM_DIR)
    p = sub.add_parser("backup")
    p.add_argument("--backup-dir", required=True)
    p.add_argument("--hbm-dir", default=DEFAULT_HBM_DIR)
    p = sub.add_parser("stage")
    p.add_argument("--src-dir", required=True)
    p.add_argument("--hbm-dir", default=DEFAULT_HBM_DIR)
    p = sub.add_parser("restore-backup")
    p.add_argument("--backup-dir", required=True)
    p.add_argument("--hbm-dir", default=DEFAULT_HBM_DIR)
    args = ap.parse_args()
    if args.cmd in ("check", "restore"):
        case_dir = os.path.abspath(args.case_dir)
        if not os.path.isdir(case_dir):
            print(f"[HBM] 用法错误：case 目录不存在 {case_dir}")
            return 2
        fn = check if args.cmd == "check" else restore
        return fn(case_dir, args.hbm_dir)
    if args.cmd == "backup":
        return backup(args.hbm_dir, os.path.abspath(args.backup_dir))
    if args.cmd == "stage":
        return stage(os.path.abspath(args.src_dir), args.hbm_dir)
    return restore_backup(os.path.abspath(args.backup_dir), args.hbm_dir)


if __name__ == "__main__":
    sys.exit(main())
