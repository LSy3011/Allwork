#!/usr/bin/env python3
# sum_lane0_product_audit.py —— SUM 首证产物审计（契约 §3.7-1 连接/字段/容量/地址 + §3.7-4 逐拍时序）
#
# 对一个 sum_lane0* build 目录做静态产物审计：
#   A. 容量：每队列发射行数 ≤ 128（IQ 墙，smi_top.v:22），行列数与 hex 文件一致；
#   B. 字段：全部 BODY 行 loops=0/inc=0/embed_end=0/inc_embed=0（ADR-056 D2 行规格铁律）、
#      dly∈[0,7]、JUMP 仅 TERM；
#   C. 连接：零路由唯一（rtz0_0: SEL=VIN1_D=12）、进位 RTOVR 全员 SEL=4（FPADD_D）、
#      末次写回链 SEL=4→VOUT0_S / SEL=13→UR_W0_S、UR 地址落在声明区；
#   D. 时序（§3.7-4，对 SMT 全局槽位）：逐 add 核对「FPADD_D 可用时刻 ≤ 进位采样时刻
#      < 下一次 D 覆盖时刻」；行首零路由链逐级延迟；写回链在生产者落地之后。
#   E. 重发射如实登记：ADR-056 展开分支 = 模调度帧重复（assembler.py:546-553，
#      每 op 每帧发射一次），R_q 逐队列列出；单行链的逐帧幂等论据见
#      docs/softmax_v1_spec_design_20261019.md v4 附录——本审计不把「火一次」当前提。
#
# 用法：sum_lane0_product_audit.py <build_dir> [--L 16] [--rows 1]
# 退出码 0=全过 / 1=任一违规 / 2=用法。

import argparse
import json
import os
import sys
from collections import defaultdict

IQ_CAP = 128
FPADD_LAT = 4
RTOVR_LAT = 1
UREAD_LAT = 1
VIN_LAT = 1

BANK_X, BANK_Y, BANK_Z = 1, 2, 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("build")
    ap.add_argument("--L", type=int, default=16)
    ap.add_argument("--rows", type=int, default=1)
    args = ap.parse_args()

    sched = json.load(open(os.path.join(args.build, "compiler_schedule.json")))
    phys = json.load(open(os.path.join(args.build, "physical_instructions.json")))
    II = sched["II"]
    st = {k: v["t"] for k, v in sched["ops"].items()}
    fails = []

    # ── A. 容量 ────────────────────────────────────────────────────────
    per_q_rows = defaultdict(int)
    for r in phys:
        per_q_rows[r["iq_name"]] += 1
    print(f"[A] 容量（IQ 墙 {IQ_CAP} 行/队列）：")
    for q, n in sorted(per_q_rows.items()):
        hexp = os.path.join(args.build, "smc_0", f"iq_{q}.hex")
        hexn = sum(1 for _ in open(hexp)) if os.path.exists(hexp) else -1
        ok = n <= IQ_CAP and n == hexn
        print(f"    {q:<10} rows={n:<4} hex_lines={hexn:<4} {'OK' if ok else 'VIOLATION'}")
        if not ok:
            fails.append(f"容量/行列不一致: {q} rows={n} hex={hexn}")

    # ── B. 字段铁律 ────────────────────────────────────────────────────
    n_bad = 0
    for r in phys:
        h = r["header"]
        if r["stage"] == "BODY":
            if (h["LOOPS"], h["INC"], h["EMBED_END"], h["INC_EMBED"]) != (0, 0, 0, 0):
                fails.append(f"字段违规 {r['trace_id']}: {h}")
                n_bad += 1
            if not (0 <= h["DLY"] <= 7):
                fails.append(f"dly 越域 {r['trace_id']}: DLY={h['DLY']}")
                n_bad += 1
        if h["JUMP"] and r["stage"] != "TERM":
            fails.append(f"JUMP 非 TERM: {r['trace_id']}")
            n_bad += 1
    print(f"[B] 字段铁律：BODY 行规格违规 {n_bad} 处"
          + ("（OK）" if n_bad == 0 else "（VIOLATION）"))

    # ── C. 连接与地址 ──────────────────────────────────────────────────
    # 按 trace_id 去重（帧重复行同 trace_id 同 payload，连接审计对 slot-op）。
    # 命名（rtovr_bypass 改写后）：
    #   零路由 = zr{r}_{i}(UR_READ) → rtz..__vinin(RTOVR→VIN1_S, sel∈30..33)
    #            → rtz..__vin1(VIN1) → rtz..__vinout(RTOVR→FPADD_S1, sel=12)
    #   x 路由 = xr{r}_{i} → rtx..__vinin/__vin0/__vinout(sel=11) → FPADD_S0
    #   进位   = rtc{r}_{i}（合法 compute→compute 边保留原名，sel=4=FPADD_D）
    #   写回   = rtwb..__voutin(sel=4)/__vout0/__voutout(sel=13) → yw{r}
    seen = {}
    for r in phys:
        seen.setdefault(r["trace_id"], r)
    zero_in = [t for t in seen if t.startswith("rtz") and t.endswith("__vinin")]
    zero_out = [t for t in seen if t.startswith("rtz") and t.endswith("__vinout")]
    x_out = [t for t in seen if t.startswith("rtx") and t.endswith("__vinout")]
    carry_rts = [t for t in seen if t.startswith("rtc")]
    wb_ins = [t for t in seen if t.startswith("rtwb") and t.endswith("__voutin")]
    wb_outs = [t for t in seen if t.startswith("rtwb") and t.endswith("__voutout")]
    print(f"[C] 连接：零路由 {len(zero_in)}/{len(zero_out)} 条（应==行数 {args.rows}）、"
          f"x 路由 {len(x_out)} 条（应=={args.rows * args.L}）、"
          f"进位 RTOVR {len(carry_rts)} 条、写回链 {len(wb_ins)}/{len(wb_outs)}")
    if not (len(zero_in) == len(zero_out) == args.rows):
        fails.append(f"零路由数 {len(zero_in)}/{len(zero_out)} != 行数 {args.rows}")
    if len(x_out) != args.rows * args.L:
        fails.append(f"x 路由数 {len(x_out)} != {args.rows * args.L}")
    for t in zero_in:
        if seen[t]["payload"].get("SEL") not in (30, 31, 32, 33):
            fails.append(f"零路由入 {t} SEL={seen[t]['payload'].get('SEL')} ∉ 30..33")
    for t in zero_out:
        if seen[t]["payload"].get("SEL") != 12:
            fails.append(f"零路由出 {t} SEL={seen[t]['payload'].get('SEL')} != 12(VIN1_D)")
    for t in x_out:
        if seen[t]["payload"].get("SEL") != 11:
            fails.append(f"x 路由出 {t} SEL={seen[t]['payload'].get('SEL')} != 11(VIN0_D)")
    for t in carry_rts:
        if seen[t]["payload"].get("SEL") != 4:
            fails.append(f"进位 {t} SEL={seen[t]['payload'].get('SEL')} != 4(FPADD_D)")
    for t in wb_ins:
        if seen[t]["payload"].get("SEL") != 4:
            fails.append(f"写回入 {t} SEL={seen[t]['payload'].get('SEL')} != 4(FPADD_D)")
    for t in wb_outs:
        if seen[t]["payload"].get("SEL") != 13:
            fails.append(f"写回出 {t} SEL={seen[t]['payload'].get('SEL')} != 13(VOUT0_D)")
    # 地址域：UR 行 payload BANK/ADDR
    L, R = args.L, args.rows
    for t, r in seen.items():
        p = r["payload"]
        if r["iq_name"].startswith("ur-r") and t.startswith("xr"):
            rr, ii = int(t[2]), int(t.split("_")[1])
            if (p.get("BANK"), p.get("ADDR")) != (BANK_X, rr * L + ii):
                fails.append(f"x 读地址错 {t}: BANK={p.get('BANK')} ADDR={p.get('ADDR')}")
        elif r["iq_name"].startswith("ur-r") and t.startswith("zr"):
            rr = int(t[2])
            if (p.get("BANK"), p.get("ADDR")) != (BANK_Z, rr):
                fails.append(f"零读地址错 {t}: BANK={p.get('BANK')} ADDR={p.get('ADDR')}")
        elif r["iq_name"].startswith("ur-w") and t.startswith("yw"):
            rr = int(t[2])
            if (p.get("BANK"), p.get("ADDR")) != (BANK_Y, rr):
                fails.append(f"y 写地址错 {t}: BANK={p.get('BANK')} ADDR={p.get('ADDR')}")
    print(f"    地址域：x∈bank{BANK_X}[0,{R * L}) zero∈bank{BANK_Z}[0,{R}) "
          f"y∈bank{BANK_Y}[0,{R}) 逐行核对完毕")

    # ── D. 逐拍时序（§3.7-4：结果何时可用 / 何时被下一次加法采样）──────
    print(f"[D] 逐拍时序（SMT 槽位，II={II}；FPADD 落地=发射+{FPADD_LAT}，"
          f"RTOVR 采样→交付=+{RTOVR_LAT}）：")
    for rr in range(R):
        n_adds = len([k for k in st if k.startswith(f"add{rr}_")])
        for i in range(n_adds):
            t_a = st[f"add{rr}_{i}"]
            if i == 0 or f"rtz{rr}_{i}__vinout" in st:
                # 零路由四级链：zr → __vinin → __vin1 → __vinout → add 采样；
                # 本 add 的 S1 来自零常量字，全程不采样 FPADD_D（行首切换点）。
                t_zr = st[f"zr{rr}_{i}"]
                t_vi = st[f"rtz{rr}_{i}__vinin"]
                t_v1 = st[f"rtz{rr}_{i}__vin1"]
                t_vo = st[f"rtz{rr}_{i}__vinout"]
                chain_ok = (t_vi >= t_zr + UREAD_LAT
                            and t_v1 >= t_vi + RTOVR_LAT
                            and t_vo >= t_v1 + VIN_LAT
                            and t_a >= t_vo + RTOVR_LAT)
                tag = "零路由"
                detail = (f"zr@{t_zr} →vin@{t_vi}/{t_v1} →vinout@{t_vo} "
                          f"→add@{t_a}（不采样 FPADD_D）")
            else:
                t_rt = st[f"rtc{rr}_{i}"]
                t_prod = st[f"add{rr}_{i-1}"]
                avail = t_prod + FPADD_LAT
                clobber = t_a + FPADD_LAT  # 本 add 自身落地即下一次覆盖
                chain_ok = (t_rt >= avail and t_a >= t_rt + RTOVR_LAT
                            and t_rt < clobber)
                tag = "进位"
                detail = (f"add{i-1}@{t_prod} 落地@{avail} →rtc 采样@{t_rt} "
                          f"→add{i}@{t_a}（窗口 {avail}..{clobber - 1}）")
            status = "OK" if chain_ok else "VIOLATION"
            if not chain_ok:
                fails.append(f"时序违规 add{rr}_{i}: {detail}")
            print(f"    add{rr}_{i:<2} [{tag}] {detail}  {status}")
        # 写回链：rtwb__voutin 采样 ≥ 末次 add 落地
        last = n_adds - 1
        t_wb, t_last = st[f"rtwb{rr}__voutin"], st[f"add{rr}_{last}"]
        ok = t_wb >= t_last + FPADD_LAT
        print(f"    写回行{rr}: 末加@{t_last} 落地@{t_last + FPADD_LAT} "
              f"→rtwb 采样@{t_wb}  {'OK' if ok else 'VIOLATION'}")
        if not ok:
            fails.append(f"写回时序违规 行{rr}: rtwb@{t_wb} < 落地@{t_last + FPADD_LAT}")

    # ── E. 重发射登记（不当前提，只陈述）────────────────────────────────
    print(f"[E] 模调度帧重复登记（assembler.py:546-553 语义）：II={II}")
    for q in sorted(per_q_rows):
        rq = (per_q_rows[q] - 2) // II if per_q_rows[q] > 2 else 0
        print(f"    {q:<10} rows={per_q_rows[q]:<4} → R_q≈{rq} 帧（每 slot-op 每帧发射一次）")

    if fails:
        print("\n" + "\n".join("[AUDIT-FAIL] " + f for f in fails))
        return 1
    print("\n[AUDIT] 连接/字段/容量/地址/时序 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
