#!/usr/bin/env python3
"""ADR-055 D4(b-2) 离线裁决器：把 TB 探针的逐拍取指事件裁决成 (i)–(iv) 相位断言 + 填充/排干拍点名。

输入：
  * <case_root>/iq_probe_trace.log  —— TB 探针（tb_aiacc_top_v04_f_multi_stage.v 末段，
    additive-only）对每个 w_rdi_addr_valid/w_rdi_data_valid 周期落的一行：
    `cycle iq addr_valid addr data_valid data`。
  * 归档构建目录（physical_instructions.json + compiler_schedule.json）——静态契约的既有引用。
  * 该规模重打包前后的 `stage_0/iq_<q>.hex` —— 与 RTL 实际读到的字**逐字比整数**。

断言逐字对应 ADR-055 D4(b-2)：
  (i)  槽 0 首行（INIT 后第 1 行 BODY）落在 l_init+1 拍：c1 − c0 == l_init+1
  (ii) BODY 段 II·R_q 行严格连续无空隙：∀k≥1, c_{k+1} − c_k == 1
  (iii) TERM 落在 l_init+1+II·R_q 拍：c_last − c0 == l_init+1 + II·R_q
  (iv) 单槽队列（ur-r0）与多 op 队列（ur-r3）同法成立——垫拍与槽数无关
  (e0) 行数：每队列 addr_valid 上升沿个数 == L_q（来自归档产物，不引用 schedule 中的 II
       自证 —— R_q/l_init 反算以 schedule 的 t 集合为准，与 parse_e2e_scale 同口径）。
  (e1) 内容：第 k 条取指事件读到的字 == hex 文件第 k 行（int 比较；>60bit 前导零无伤）。
  (e2) 地址链：addr_k == base + k（base = 首地址，不假定 0）。

填充/排干拍点名（hco D5）：由**观测到的字**中 decode 出 (CODE/BANK/ADDR)（字段位置读
hw_config/modules.yaml 的 UR_READ code_fields——禁止手抄常量），按归档产物的
trace_id 建立 row→(op, trig j) 映射，点名报告哪些取指拍对哪个 tensor 属填充/有效/排干。

数值一律比整数（验收铁律）。任何断言失败 → RED 且 exit 1。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(_REPO / "src"))

from api.hw_spec import HardwareSpec          # noqa: E402


def _field_positions(spec) -> dict:
    """从 hw_config 源读取 UR_READ payload 各字段位偏移（低→高）+ header 表。"""
    from driver.packer import ISA_V5_HEADER, ISA_V5_HEADER_WIDTH  # 单源
    pos = {name.upper(): (off, w) for name, (off, w) in ISA_V5_HEADER.items()}
    mod = spec.modules["UR_READ"]
    p = 0
    body = {}
    for f in mod.code_fields:
        body[f.name.upper()] = (p, f.bits)
        p += f.bits
    return {"body_bits": p, "header": pos, "body": body,
            "header_width": ISA_V5_HEADER_WIDTH}


def decode_word(word: int, pos: dict) -> dict:
    body_bits = pos["body_bits"]
    out = {}
    for name, (off, w) in pos["header"].items():
        out[name] = (word >> (body_bits + off)) & ((1 << w) - 1)
    for name, (off, w) in pos["body"].items():
        out[name] = (word >> off) & ((1 << w) - 1)
    return out


def _int_or_x(tok: str, base: int):
    """x/z 态记为 None（valid=1 而值为 None 在下游按 RED 计数，不在此崩）。"""
    lo = tok.lower()
    if "x" in lo or "z" in lo:
        return None
    return int(tok, base)


def load_trace(path: Path) -> dict:
    """iq -> [(cycle, addr_valid, addr, data_valid, data)]；addr/data 为 None 表示 x/z 态。"""
    evs: dict[int, list] = {}
    for ln, raw in enumerate(path.read_text().splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 6:
            raise SystemExit(f"trace 行格式坏 @{ln}: {raw!r}")
        cyc, iq, av, dv = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[4])
        addr, data = _int_or_x(parts[3], 10), _int_or_x(parts[5], 16)   # probe: addr=%0d, data=%0h
        evs.setdefault(iq, []).append((cyc, av, addr, dv, data))
    return evs


def hex_lines(path: Path) -> list[int]:
    return [int((l.strip()[2:] if l.strip().lower().startswith("0x") else l.strip()), 16)
            for l in path.read_text().splitlines() if l.strip()]


def collapse_runs(items):
    """把连续周期上信号持续为真的采样折叠成一次事件（取首拍）。

    w_rdi_addr_valid/w_rdi_data_valid 可能因 queen 反压在多个相邻周期保持 1；
    那些是**同一次请求**的多个采样而非多次取指。判据：相邻周期且关键值（addr/data）
    不变 → 同一 run；出现空隙或值变化 → 新 run。
    """
    out: list[tuple[int, object]] = []
    last_cyc = None
    last_val = None
    for cyc, val in items:
        if last_cyc is not None and cyc == last_cyc + 1 and val == last_val:
            last_cyc = cyc
            continue
        out.append((cyc, val))
        last_cyc, last_val = cyc, val
    return out


def check_queue(iq_name: str, iq_id: int, events, hex_f: Path, per_q_rows: list,
                sch: dict, pos: dict, report: dict) -> list[str]:
    """对单队列做 e0/e1/e2 + (i)–(iv) 全套，并点名填充/排干拍。返回违例列表。"""
    bad: list[str] = []
    fetch = collapse_runs([(c, addr) for (c, av, addr, dv, data) in events if av == 1])
    data_ev = collapse_runs([(c, data) for (c, av, addr, dv, data) in events if dv == 1])
    name = f"{iq_name}(IQ{iq_id})"
    if not fetch:
        return [f"{name}: 无取指事件（探针未覆盖?）"]

    rows = per_q_rows
    L_q = len(rows)
    n_x = sum(1 for (_c, a_) in fetch if a_ is None)
    if n_x:
        bad.append(f"{name}: 取指事件含 X 地址 ×{n_x}（valid=1 而地址未知）")
    fetch = [(c, a_) for (c, a_) in fetch if a_ is not None]
    data_ev = [(c, d_) for (c, d_) in data_ev if d_ is not None]

    # 配对：addr 事件配首个 cycle >= 其拍的数据事件（同拍即可，实测 queen 同拍返回）。
    # 无配对的 addr 事件 = TERM 之后的残尾取指尝试（读不到行、data=X、dv=0）——
    # 计数并报告，但不计入行序列（e0/e1/e2/相位只用配上的）。
    paired = []
    unpaired = []
    di = 0
    for (c, addr) in fetch:
        while di < len(data_ev) and data_ev[di][0] < c:
            di += 1
        if di < len(data_ev):
            paired.append((c, addr, data_ev[di][1]))
        else:
            unpaired.append((c, addr))
    if unpaired:
        report["phases"][iq_name + "__probes"] = [
            {"cycle": c, "addr": a_} for (c, a_) in unpaired]
    fetch = [(c, a_) for (c, a_, _w) in paired]
    words = [w for (_c, _a, w) in paired]

    if len(fetch) != L_q:
        bad.append(f"{name}: e0 行数违例: 有效取指 {len(fetch)} 次 != L_q={L_q}"
                   f"（另有残尾尝试 {len(unpaired)} 次）")

    hexw = hex_lines(hex_f)
    base = fetch[0][1]
    for k, ((c, addr), w) in enumerate(zip(fetch, words)):
        if addr != base + k:
            bad.append(f"{name}: e2 地址链断裂: 第{k}行 addr={addr} 期望 {base + k}")
            break
        if k < len(hexw):
            if w is None:
                bad.append(f"{name}: e1 第{k}行无配对数据")
                break
            if w != (hexw[k] & ((1 << 60) - 1)):
                bad.append(f"{name}: e1 内容不等: 第{k}行 rtl={w:#x} hex={hexw[k]:#x}")
                break

    # 相位：l_init 与 R_q 由 schedule 的 t 集合反算（与 parse_e2e_scale 同口径）
    ii = int(sch["II"])
    ts = [int(v["t"]) for k_, v in sch["ops"].items()
          if str(v["iq"]).lower().replace("_", "-") == iq_name]
    if not ts:
        return bad + [f"{name}: schedule 中无该队列 ops"]
    t_min, t_max = min(ts), max(ts)
    n_eff = report["n_eff"]
    skip = t_min // ii
    l_init = skip * ii + 1
    r_q = (t_max // ii + n_eff) - skip
    c = [fc for (fc, _a) in fetch]
    phase = {"queue": iq_name, "l_init": l_init, "R_q": r_q, "c0": c[0],
             "fetch_cycles": c}
    if len(c) >= 2:
        d1 = c[1] - c[0]
        phase["i_delta_c1_c0"] = d1
        if d1 != l_init + 1:
            bad.append(f"{name}: (i) 首 BODY 相位违例: c1-c0={d1} != l_init+1={l_init + 1}")
        gaps = [c[k + 1] - c[k] for k in range(1, len(c) - 1)]
        phase["ii_body_gaps"] = sorted(set(gaps))
        if any(g != 1 for g in gaps):
            bad.append(f"{name}: (ii) BODY 非连续: gaps={gaps}")
        d_term = c[-1] - c[0]
        phase["iii_delta_term_c0"] = d_term
        if d_term != l_init + 1 + ii * r_q:
            bad.append(f"{name}: (iii) TERM 相位违例: c_last-c0={d_term} "
                       f"!= l_init+1+II*R_q={l_init + 1 + ii * r_q}")
    report["phases"][iq_name] = phase

    # 填充/排干拍点名：由观测字 decode + 产物 trace_id 映射
    naming = []
    for k, ((fc, _a), w) in enumerate(zip(fetch, words)):
        if w is None or k >= len(rows):
            continue
        row = rows[k]
        if row["stage"] != "BODY":
            continue
        dec = decode_word(w, pos)
        naming.append({
            "beat_rel": fc - c[0], "row": k, "trace_id": row["trace_id"],
            "obs_vld": dec["VLD"], "obs_bank": dec.get("BANK"), "obs_addr": dec.get("ADDR"),
        })
    report["beats"][iq_name] = naming

    # 填充/排干归属：t//II − skip = r_k；j = (k_body_index_in_slot)；有效窗 [r_k, r_k+N_eff)
    by_slot: dict[str, int] = {}
    for item in naming:
        tid = item["trace_id"]
        if tid.startswith("nop_"):
            continue
        j = by_slot.get(tid, 0)
        by_slot[tid] = j + 1
        op = sch["ops"].get(tid)
        t = int(op["t"]) if op else None
        r_k = (t // ii) - skip if t is not None else None
        if r_k is None:
            item["phase_kind"] = "unknown"
        elif j < r_k:
            item["phase_kind"] = "FILL"
        elif j >= r_k + n_eff:
            item["phase_kind"] = "DRAIN"
        else:
            item["phase_kind"] = "LIVE"
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", type=Path, required=True, help="case_root/iq_probe_trace.log")
    ap.add_argument("--build", type=Path, required=True, help="归档构建目录")
    ap.add_argument("--hex-dir", type=Path, required=True, help="stage_0/iq_*.hex 所在")
    ap.add_argument("--num-iterations", type=int, required=True)
    ap.add_argument("--queues", default="ur-r3:19,ur-r0:16",
                    help="逗号分隔 name:IQ_ID（多 op 队列 + 单槽队列）")
    ap.add_argument("--hw-config", default=str(_REPO / "hw_config"))
    a = ap.parse_args()

    spec = HardwareSpec.load(a.hw_config)
    pos = _field_positions(spec)
    sch = json.loads((a.build / "compiler_schedule.json").read_text())
    phys = json.loads((a.build / "physical_instructions.json").read_text())
    per_q: dict[str, list] = {}
    for r in phys:
        per_q.setdefault(r["iq_name"], []).append(r)

    evs = load_trace(a.trace)
    report = {"n_eff": a.num_iterations, "phases": {}, "beats": {}, "violations": []}
    all_bad: list[str] = []
    for item in a.queues.split(","):
        iq_name, iq_id = item.split(":")
        iq_id = int(iq_id)
        hex_f = a.hex_dir / f"iq_{iq_name}.hex"
        if not hex_f.is_file():
            all_bad.append(f"{iq_name}: 缺 {hex_f}")
            continue
        all_bad.extend(check_queue(iq_name, iq_id, evs.get(iq_id, []), hex_f,
                                   per_q.get(iq_name, []), sch, pos, report))
    report["violations"] = all_bad
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if all_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
