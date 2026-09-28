#!/usr/bin/env python3
"""ADR-053 D7 验收：编译器产物 vs 手写 golden 逐位对齐 + 物理合法性断言 + 负向断言。

四条判据（全部比整数，禁止比字符串）：
  ① 逐位对齐（模 UR 端口重命名）：编译器 mul 的 (slot, sel) 集合与 golden mul_scalar
     （已上板 v422 包）逐位一致——UR 源口 (30..33) 的选取是调度自由（ADR §4 blueprint
     自身 out14 就出现过 UR0/UR2 两种），故允许对 30..33 施加**一个全局一致的置换 σ**；
     σ 之外任何一位不同即 FAIL。
  ② 物理合法性（屏蔽数 472→0）：编译器 emit 的全部 rtovr (slot, sel) 满足
     hw_spec.get_rtovr_connectivity() 邻接表（D1，RTL 生成；D8① 锁表↔RTL 逐位）。
  ③ VIN/VOUT 指令逐位：iq_vin0/vin1/vout0.hex 的 vld body（低 6 位
     {smc_id_en[5:1], vld[0]}）与 golden 的 iq_68/69/70.hex 逐位相等（=0x0f，en=7）。
  ④ 负向断言：把一条 select 改成非法后重跑 ②，审计必须计数 >0（防校验器假绿）。

用法：
  .venv/bin/python verification_v2/tools/validate_rtovr_golden_parity.py \
      [--op mul] [--golden-dir <stage_1>] [--build-dir build/verification_v2/mul]

exit 0 = 全过；exit 1 = 任一判据失败。
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

COMPILER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(COMPILER / "src"))

from api.hw_spec import HardwareSpec  # noqa: E402

DEFAULT_GOLDEN = (Path.home() / "u280_sim_v422" / "fpga_pkg"
                  / "mul_scalar_8smc_v3_fpga_writeback_fix" / "case"
                  / "smi_iq_inst" / "stage_1")

# golden 的 vin 文件名 → 编译器的 vin 文件名
GOLDEN_VIN_MAP = {"iq_68_vin0.hex": "iq_vin0.hex",
                  "iq_69_vin1.hex": "iq_vin1.hex",
                  "iq_70_vout0.hex": "iq_vout0.hex"}
UR_PORT_IDS = (30, 31, 32, 33)


def decode_rtovr_selects(d: Path):
    """目录 d 下全部 iq_rtovr-<slot>.hex → [(slot, sel)]（只取 vld=1 的行，比整数）。

    编译器与 golden 共用同一 body 布局：低 7 位 {sel[6:1], vld[0]}（modules.yaml
    RTOVR.code_bits=7）。INIT/HDR/终结行 vld=0 自然被滤掉。
    """
    pairs = []
    for f in sorted(d.glob("iq_rtovr-*.hex")):
        slot = int(f.stem.split("-")[1])
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            body = int(line, 16) & 0x7F
            if body & 1:
                pairs.append((slot, (body >> 1) & 0x3F))
    return pairs


def decode_vin_bodies(d: Path, names):
    """{文件名: [vld body 整数]}；VIN body = 低 6 位 {smc_id_en[5:1], vld[0]}。"""
    out = {}
    for fn in names:
        f = d / fn
        if not f.exists():
            out[fn] = None
            continue
        bodies = []
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            body = int(line, 16) & 0x3F
            if body & 1:
                bodies.append(body)
        out[fn] = bodies
    return out


def audit_legality(build_dir: Path, allowed) -> list:
    """D7②：返回被屏蔽的 (slot, sel) 列表（空 = 0 屏蔽）。"""
    masked = []
    for slot, sel in decode_rtovr_selects(build_dir):
        if sel not in allowed.get(slot, frozenset()):
            masked.append((slot, sel))
    return masked


def port_permutation_match(comp_pairs, gold_pairs):
    """判据①：存在 30..33 的置换 σ，使 σ(编译器集合) == golden 集合（多重集相等）。

    穷举 4! = 24 个置换；只重命名 UR 源口，其余 sel 原样。比整数。
    """
    from itertools import permutations
    if len(comp_pairs) != len(gold_pairs):
        return None
    gold_sorted = sorted(gold_pairs)
    for perm in permutations(UR_PORT_IDS):
        sigma = dict(zip(UR_PORT_IDS, perm))
        mapped = sorted((s, sigma.get(sel, sel)) for s, sel in comp_pairs)
        if mapped == gold_sorted:
            return sigma
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", default="mul")
    ap.add_argument("--build-dir", default=None)
    ap.add_argument("--golden-dir", default=str(DEFAULT_GOLDEN))
    ap.add_argument("--skip-negative", action="store_true")
    args = ap.parse_args()

    build_dir = Path(args.build_dir) if args.build_dir \
        else COMPILER / "build" / "verification_v2" / args.op / "smc_0"
    golden_dir = Path(args.golden_dir)
    assert build_dir.is_dir(), f"build 目录不存在: {build_dir}（先跑 main_verify --op {args.op} --mode e2e）"
    assert golden_dir.is_dir(), f"golden 目录不存在: {golden_dir}"

    spec = HardwareSpec.load("hw_config")
    allowed = spec.get_rtovr_connectivity()
    assert allowed, "邻接表为空——D1 未落盘或配置事故（fail-closed）"

    failures = []

    # ---------- 判据②：物理合法性（屏蔽数 472→0） ----------
    masked = audit_legality(build_dir, allowed)
    n_total = len(decode_rtovr_selects(build_dir))
    if masked:
        failures.append(f"②物理合法性 FAIL：{len(masked)}/{n_total} 条被屏蔽: {masked}")
    else:
        print(f"[D7②] 物理合法性 PASS：{n_total}/{n_total} 条 select 全合法（屏蔽数 0）")

    # ---------- 判据①：golden 逐位对齐（模 UR 端口置换） ----------
    comp_pairs = decode_rtovr_selects(build_dir)
    gold_pairs = decode_rtovr_selects(golden_dir)
    comp_slots = sorted({s for s, _ in comp_pairs})
    gold_slots = sorted({s for s, _ in gold_pairs})
    if comp_slots != gold_slots:
        failures.append(f"①slot 集合不一致：编译器 {comp_slots} vs golden {gold_slots}")
    else:
        sigma = port_permutation_match(comp_pairs, gold_pairs)
        if sigma is None:
            failures.append(
                f"①逐位对齐 FAIL：编译器 {sorted(comp_pairs)} 经任何 UR 端口置换"
                f"都无法等于 golden {sorted(gold_pairs)}")
        else:
            identity = all(k == v for k, v in sigma.items())
            note = "（σ=恒等，端口逐位一致）" if identity else \
                   f"（σ={sigma}：UR 端口重命名后逐位一致，端口选取为调度自由）"
            print(f"[D7①] golden 逐位对齐 PASS：{len(comp_pairs)} 条 select {note}")
            print(f"       pairs = {sorted(comp_pairs)}")

    # ---------- 判据③：VIN/VOUT body 逐位 ----------
    gold_vin = decode_vin_bodies(golden_dir, GOLDEN_VIN_MAP.keys())
    comp_vin = decode_vin_bodies(build_dir, GOLDEN_VIN_MAP.values())
    for g_fn, c_fn in GOLDEN_VIN_MAP.items():
        g, c = gold_vin[g_fn], comp_vin[c_fn]
        if c is None:
            failures.append(f"③缺编译器产物 {c_fn}")
        elif g != c:
            failures.append(f"③{c_fn} body 不一致：编译器 {c} vs golden {g}")
        else:
            print(f"[D7③] {c_fn} body 逐位一致：{[hex(b) for b in c]}")

    # ---------- 判据④：负向断言 ----------
    if not args.skip_negative:
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            shutil.copytree(build_dir, tdir, dirs_exist_ok=True)
            victim = tdir / "iq_rtovr-3.hex"
            lines = victim.read_text().splitlines()
            mutated = False
            for idx, line in enumerate(lines):
                body = int(line.strip(), 16) & 0x7F
                if body & 1:
                    # sel 11(VIN0_D，合法) → 30(UR_RDATA_0，对 slot3 非法)
                    new_body = (30 << 1) | 1
                    lines[idx] = format((int(line, 16) & ~0x7F) | new_body, "012x")
                    mutated = True
                    break
            assert mutated, "负向断言找不到可变异行"
            victim.write_text("\n".join(lines) + "\n")
            masked_neg = audit_legality(tdir, allowed)
            if masked_neg:
                print(f"[D7④] 负向断言 PASS：变异后审计检出 {masked_neg}（校验器不假绿）")
            else:
                failures.append("④负向断言 FAIL：非法 select 未被审计检出——校验器假绿")

    if failures:
        print("\n*** D7 ACCEPTANCE FAILED ***")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n*** D7 ACCEPTANCE: ALL CHECKS PASSED ***")
    return 0


if __name__ == "__main__":
    sys.exit(main())
