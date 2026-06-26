#!/usr/bin/env python3
"""从 VCD 抽 UR bank 写事件流（方案 C 时序 diff 的 RTL 侧事件源）。

写事件模型（与 RTL 同步 SRAM 一致）：
  在每个 clk posedge，若 (ena==1 AND wea!=0)，则发生一次写：
  把"进入该时戳前保持的" dina 写到 addra。
  —— VCD 只记录值变化，wea 常驻 0xFFFF 时只有一条变化事件，
     因此必须按 clk posedge 采样，而非按 wea 边沿。

采样时序：同步 SRAM 在边沿采集的是边沿前已稳定的组合值，故在 posedge
时戳上、用"应用本时戳更新之前"的保持值评估写。本时戳内 dina 的新变化
归属下一拍。

输出：events[(smc, bank)] = [WriteEvent(cycle, time_ps, addr, data_hex), ...]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass

from vcd_parser import parse_header


@dataclass
class WriteEvent:
    cycle: int
    time_ps: int
    addr: int
    data_hex: str   # 32 个 hex 字符（128bit），大端


def _smc_scope(k: int) -> str:
    if k == 0:
        return "tb_aiacc_top_f.dut.u_smc.u_user_register"
    return (f"tb_aiacc_top_f.dut.gen_smc_extra.smc_more[{k}]"
            f".u_smc_more.u_user_register")


def _bits_to_int(b: str):
    try:
        return int(b, 2)
    except ValueError:
        return None  # 含 x/z


def _bits_to_hex(b: str, width: int = 128) -> str:
    v = _bits_to_int(b)
    if v is None:
        return "x" * (width // 4)
    return format(v, f"0{width // 4}x")


def extract(vcd_path: str, banks=range(8), smcs=range(8)):
    hdr = parse_header(vcd_path)
    clk_code = hdr.by_full.get("tb_aiacc_top_f.clk")
    if clk_code is None:
        raise SystemExit("找不到 tb_aiacc_top_f.clk")

    # 建 code->(smc,bank,kind) 映射；kind in {wea,ena,addra,dina}
    info = {}
    codes = {clk_code}
    for k in smcs:
        for b in banks:
            base = f"{_smc_scope(k)}.bram_bank_inst[{b}].u_bank_sram"
            for kind in ("wea", "ena", "addra", "dina"):
                full = f"{base}.{kind}"
                c = hdr.by_full.get(full)
                if c is None:
                    continue
                info[c] = (k, b, kind)
                codes.add(c)

    # 保持状态
    held = defaultdict(lambda: {"wea": "0", "ena": "0", "addra": "0", "dina": "0"})
    events = defaultdict(list)
    cycle = -1
    prev_clk = "0"

    # 手动按时戳分批解析（直接读文件，避免逐事件回调开销）
    import re
    _scal = set("01xzXZ")
    cur_t = 0
    seen = False
    batch = []  # 本时戳内的 (code,val)
    clk_rose = [False]

    def flush(t):
        # 1) 先看本批是否含 clk 上升沿（用本批 clk 值判断）
        nonlocal cycle, prev_clk
        new_clk = None
        for code, val in batch:
            if code == clk_code:
                new_clk = val
        rose = (prev_clk == "0" and new_clk == "1")
        # 2) posedge：用"应用本批前"的保持值评估写
        if rose:
            cycle += 1
            for (k, b), st in [((kk, bb), held[(kk, bb)]) for kk in smcs for bb in banks]:
                wez = _bits_to_int(st["wea"]) or 0
                enz = _bits_to_int(st["ena"])
                if wez != 0 and (enz is None or enz != 0):
                    addr = _bits_to_int(st["addra"])
                    events[(k, b)].append(WriteEvent(
                        cycle=cycle, time_ps=t,
                        addr=addr if addr is not None else -1,
                        data_hex=_bits_to_hex(st["dina"]),
                    ))
        # 3) 应用本批更新
        for code, val in batch:
            if code == clk_code:
                continue
            k, b, kind = info[code]
            held[(k, b)][kind] = val
        if new_clk is not None:
            prev_clk = new_clk

    with open(vcd_path, "r", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if not seen:
                if s.startswith("$enddefinitions"):
                    seen = True
                continue
            c0 = s[0]
            if c0 == "#":
                if batch:
                    flush(cur_t)
                    batch = []
                cur_t = int(s[1:])
            elif c0 in _scal:
                code = s[1:]
                if code in codes:
                    batch.append((code, s[0].lower()))
            elif c0 in "bB":
                sp = s.split()
                if len(sp) == 2 and sp[1] in codes:
                    batch.append((sp[1], sp[0][1:]))
        if batch:
            flush(cur_t)
    return events


if __name__ == "__main__":
    vcd = sys.argv[1]
    bank = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    ev = extract(vcd, banks=[bank])
    print(f"=== bank{bank} UR 写事件 ===")
    for k in range(8):
        e = ev[(k, bank)]
        print(f"SMC_{k}: 写次数={len(e)}  首写cyc={e[0].cycle if e else '-'}  "
              f"addr序列={[w.addr for w in e][:20]}")
        for w in e[:3]:
            print(f"    cyc={w.cycle} addr={w.addr} data={w.data_hex}")
    firsts = [ev[(k, bank)][0].cycle if ev[(k, bank)] else None for k in range(8)]
    if all(f is not None for f in firsts):
        print(f"\nSMC 偏移: {[f - firsts[0] for f in firsts]}  (模型预测 [0..7])")
