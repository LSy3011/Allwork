#!/usr/bin/env python3
"""阶段三性能取数：sim.log 标记 → perf_metrics_definition_20261014 §2 区间（2026-10-15）。

口径纪律（来自该文 §4，本工具在输出中强制体现）：
- 只报类型②（TB 执行行为，probe_cycle 拍）；不换算墙钟、不算百分比。
- T_exec_obs 仅当该段 WBARRIER 开启时可取（TRIGGER→WBARRIER release 窗，
  是上界近似，不称「硬件纯计算时间」）；未开启时给 T_trig_to_done 代理区间
  并显式标注「含 T_poll+T_ack，不可当执行时间」。
- TRIGGER 行本身无时间戳：其完成拍双侧夹逼——lb=前面最近带时标行
  （本 TB 中即触发写自身的 [AXI_DEBUG] B-Channel 握手行，≈真值），
  ub=后面第一条 @probe_cycle 行。区间端点一律用 lb（trig→* 为上界，
  误差 ≤ bound_width，随 JSON 输出）。2026-10-15 修订：初版取 ub 作
  approx 在 probe 行稀疏段可偏数百拍（stage_1 实测差 276 拍），已改。
- [TB_AXI_DBG] 行只有 $time(ps)：经本运行自校准偏移换算
  （用同时含 @time 与 @probe_cycle 的 stage BEGIN/done 行拟合 offset，
  残差必须 <1 拍否则报错——防跨 run 移植口径）。

输入：sim.log（mainline TB 3324a33b 及以后格式；旧格式无 probe_cycle 会报错退出 3）。
输出：JSON（--json）+ stdout 表。退出码 0=成功；3=输入缺标记/无法校准。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RE_BEGIN = re.compile(r"===== stage_(\d+)/(\d+) BEGIN ===== @time (\d+) @probe_cycle (\d+)")
RE_SDONE = re.compile(r"stage_(\d+) done \(intr cleared\) @time (\d+) @probe_cycle (\d+)")
RE_DLDONE = re.compile(r"\[TB_AXI_DBG\]\[(\d+)\] data_list done: lines=(\d+)")
RE_TRIG = re.compile(r"\[TB\] TRIGGER 0x020 written")
RE_PROBE = re.compile(r"@probe_cycle (\d+)")
RE_WB_ENTER = re.compile(r"\[GATE4\]\[WBARRIER\] enter @probe_cycle (\d+): expect bank=(\d+) writes=(\d+)")
RE_WB_REL = re.compile(r"\[GATE4\]\[WBARRIER\] release @probe_cycle (\d+): landed (\d+) \(waited (\d+) cycles\)")
RE_INTR = re.compile(r"\[TB_MULTI_STAGE\] INTR_ASSERT @probe_cycle (\d+)")
RE_BATCH_INTR = re.compile(r"batch interrupt @ cycle \d+ @time (\d+) @probe_cycle (\d+)")
RE_IQBEATS = re.compile(r"\[IQ_INST\] mem=(\S+) done, axi_beats=(\d+)")
RE_FINISH = re.compile(r"\$finish at simulation time\s+(\d+)")
RE_DBG_PS = re.compile(r"\[TB_AXI_DBG\]\[(\d+)\]")
RE_AXIDBG_PS = re.compile(r"\[AXI_DEBUG\] @(\d+)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sim_log")
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args()
    text = Path(a.sim_log).read_text(errors="replace").splitlines()

    begins, dones = {}, {}
    calib = []  # (time_ns, probe_cycle)
    trig_idx = []           # line numbers of TRIGGER writes
    stamped_ps = []         # (line_no, ps)   —— [TB_AXI_DBG]/[AXI_DEBUG] 时标行
    stamped_probe = []      # (line_no, probe_cycle)
    dl_done = []            # (line_no, ps, cum_lines)
    iq_beats = []           # (line_no, mem, beats)
    wb_enter = wb_rel = None
    intrs = []              # (line_no, probe_cycle)
    finish_ps = None

    for i, ln in enumerate(text):
        m = RE_DBG_PS.search(ln) or RE_AXIDBG_PS.search(ln)
        if m:
            stamped_ps.append((i + 1, int(m.group(1))))
        m = RE_PROBE.search(ln)
        if m:
            stamped_probe.append((i + 1, int(m.group(1))))
        m = RE_BEGIN.search(ln)
        if m:
            s = int(m.group(1))
            begins[s] = {"line": i + 1, "probe": int(m.group(4)), "time_ns": int(m.group(3))}
            calib.append((int(m.group(3)), int(m.group(4))))
            continue
        m = RE_SDONE.search(ln)
        if m:
            dones[int(m.group(1))] = {"line": i + 1, "probe": int(m.group(3)), "time_ns": int(m.group(2))}
            calib.append((int(m.group(2)), int(m.group(3))))
            continue
        m = RE_DLDONE.search(ln)
        if m:
            dl_done.append((i + 1, int(m.group(1)), int(m.group(2))))
            continue
        if RE_TRIG.search(ln):
            trig_idx.append(i + 1)
            continue
        m = RE_WB_ENTER.search(ln)
        if m:
            wb_enter = {"line": i + 1, "probe": int(m.group(1)),
                        "bank": int(m.group(2)), "writes": int(m.group(3))}
            continue
        m = RE_WB_REL.search(ln)
        if m:
            wb_rel = {"line": i + 1, "probe": int(m.group(1)),
                      "landed": int(m.group(2)), "waited": int(m.group(3))}
            continue
        m = RE_INTR.search(ln) or RE_BATCH_INTR.search(ln)
        if m:
            intrs.append((i + 1, int(m.group(m.lastindex))))
            continue
        m = RE_IQBEATS.search(ln)
        if m:
            iq_beats.append((i + 1, m.group(1), int(m.group(2))))
            continue
        m = RE_FINISH.search(ln)
        if m:
            finish_ps = int(m.group(1))

    if not begins or not dones:
        print("FATAL: 无 stage BEGIN/done @probe_cycle 标记（旧格式 TB？）", file=sys.stderr)
        return 3
    if len(calib) < 2:
        print("FATAL: 校准点不足", file=sys.stderr)
        return 3

    # ps→probe_cycle 偏移校准：probe = time_ns/10 - offset；offset 应恒定
    offsets = [t / 10.0 - p for t, p in calib]
    off = offsets[0]
    max_dev = max(abs(o - off) for o in offsets)
    if max_dev >= 1.0:
        print(f"FATAL: ps↔probe_cycle 校准残差 {max_dev}≥1 拍: {offsets}", file=sys.stderr)
        return 3

    def ps2probe(ps: int) -> int:
        return int(round(ps / 10000.0 - off))  # 周期 10000ps ⇒ ps/10000=拍，−校准偏移

    stamped = sorted(
        [(ln, p, "probe") for ln, p in stamped_probe]
        + [(ln, ps2probe(ps), "ps") for ln, ps in stamped_ps]
    )
    stages = sorted(begins)
    out_stages = []
    prev_done = None
    for s in stages:
        b, d = begins[s], dones.get(s)
        if d is None:
            print(f"FATAL: stage_{s} 有 BEGIN 无 done", file=sys.stderr)
            return 3
        # 该段内事件（按行号夹逼）
        seg_dl = [x for x in dl_done if b["line"] < x[0] < d["line"]]
        seg_tr = [x for x in trig_idx if b["line"] < x < d["line"]]
        seg_iq = [x for x in iq_beats if b["line"] < x[0] < d["line"]]
        seg_intr = [x for x in intrs if b["line"] < x[0] < d["line"]]
        dl_last = seg_dl[-1] if seg_dl else None
        trig_ln = seg_tr[0] if seg_tr else None
        # TRIGGER 完成拍双侧夹逼：lb=前面最近时标行（本 TB 中即触发写自身
        # B 握手行），ub=后面第一条 @probe_cycle 行；区间端点一律用 lb。
        trig_probe = None
        if trig_ln is not None:
            lb = None
            for ln2, p2, kind in stamped:
                if ln2 >= trig_ln:
                    break
                lb = {"line": ln2, "probe": p2, "kind": kind}
            ub = None
            for j in range(trig_ln, len(text)):
                m = RE_PROBE.search(text[j])
                if m:
                    ub = {"line": j + 1, "probe": int(m.group(1))}
                    break
            if lb is None or ub is None:
                print(f"FATAL: stage_{s} TRIGGER(行{trig_ln}) 无法双侧夹逼", file=sys.stderr)
                return 3
            trig_probe = {"lb": lb, "ub": ub, "bound_width": ub["probe"] - lb["probe"]}
        beats = sum(x[2] for x in seg_iq)
        rec = {
            "stage": s,
            "begin": {"line": b["line"], "probe": b["probe"]},
            "done": {"line": d["line"], "probe": d["probe"]},
            "data_list_done": ({"line": dl_last[0], "probe": ps2probe(dl_last[1]),
                                "cum_lines": dl_last[2]} if dl_last else None),
            "trigger": trig_probe,
            "intr": ({"line": seg_intr[0][0], "probe": seg_intr[0][1]} if seg_intr else None),
            "iq_beats_total": beats,
            "iq_beats_by_mem": {m_: sum(x[2] for x in seg_iq if x[1] == m_)
                                for m_ in sorted({x[1] for x in seg_iq})},
        }
        # 区间（perf_metrics_definition §2）；trig 锚一律取 lb（trig→* 为上界）
        iv = {}
        tl = trig_probe["lb"]["probe"] if trig_probe else None
        if dl_last:
            iv["T_load_inst"] = ps2probe(dl_last[1]) - b["probe"]
        if dl_last and tl is not None:
            iv["T_cfg_trigger"] = tl - ps2probe(dl_last[1])
        if tl is not None:
            iv["T_trig_to_done_proxy"] = d["probe"] - tl
        if seg_intr and tl is not None:
            iv["T_trig_to_intr_proxy"] = seg_intr[0][1] - tl
        if seg_intr:
            iv["T_ack"] = d["probe"] - seg_intr[0][1]
        iv["T_stage_span"] = d["probe"] - b["probe"]
        if prev_done is not None:
            iv["T_interstage_gap"] = b["probe"] - prev_done
        # 单调性护栏：倒挂即标记解析失真，宁死不产出错数
        for k in ("T_load_inst", "T_cfg_trigger", "T_trig_to_done_proxy",
                  "T_trig_to_intr_proxy", "T_ack", "T_stage_span"):
            if iv.get(k) is not None and iv[k] < 0:
                print(f"FATAL: stage_{s} 区间 {k}={iv[k]}<0（标记解析失真）", file=sys.stderr)
                return 3
        if "T_load_inst" in iv and iv["T_load_inst"] > iv["T_stage_span"]:
            print(f"FATAL: stage_{s} T_load_inst={iv['T_load_inst']}>span"
                  f"={iv['T_stage_span']}（标记解析失真）", file=sys.stderr)
            return 3
        rec["intervals"] = iv
        rec["beats_per_cycle_note"] = (
            f"T_load_inst/{beats} 行 = {iv.get('T_load_inst', 0)/beats:.1f} 拍/行"
            if beats and "T_load_inst" in iv else "本段无 IQ 装载")
        out_stages.append(rec)
        prev_done = d["probe"]

    wb = None
    if wb_enter and wb_rel:
        wb = {"enter": wb_enter, "release": wb_rel,
              "window": wb_rel["probe"] - wb_enter["probe"]}
        # T_exec_obs：该 WBARRIER 所属段的 TRIGGER→release
        for rec in out_stages:
            if rec["trigger"] and wb_enter["line"] > rec["begin"]["line"] and \
                    wb_rel["line"] < rec["done"]["line"]:
                rec["intervals"]["T_exec_obs"] = wb_rel["probe"] - rec["trigger"]["lb"]["probe"]
                rec["intervals"]["T_poll"] = (rec["intr"]["probe"] - wb_rel["probe"]
                                              if rec["intr"] else None)

    result = {
        "sim_log": str(a.sim_log),
        "calib_offset_cycles": off, "calib_max_dev": max_dev,
        "stages": out_stages,
        "wbarrier": wb,
        "T_total_sim": (dones[stages[-1]]["probe"] - begins[stages[0]]["probe"]),
        "finish_ps": finish_ps,
        "discipline": "类型② TB 拍；trigger 拍=lb/ub 夹逼（lb=触发写自身 B 握手行）；"
                      "trig→* 区间为上界（误差≤bound_width）；"
                      "T_exec_obs=TRIGGER→WBARRIER release 上界近似；"
                      "proxy 区间含 poll/ack 不可称执行时间；段拍以装载流量为主",
    }
    js = json.dumps(result, ensure_ascii=False, indent=1)
    if a.json_out:
        Path(a.json_out).write_text(js + "\n")
    for rec in out_stages:
        iv = rec["intervals"]
        tw = rec["trigger"]["bound_width"] if rec["trigger"] else "—"
        print(f"stage_{rec['stage']}: span={iv['T_stage_span']}"
              f" load={iv.get('T_load_inst')} cfg={iv.get('T_cfg_trigger')}"
              f" exec_obs={iv.get('T_exec_obs', '—')} trig→done={iv.get('T_trig_to_done_proxy')}"
              f" ack={iv.get('T_ack')} gap={iv.get('T_interstage_gap', '—')}"
              f" trig±{tw} | beats={rec['iq_beats_total']} ({rec['beats_per_cycle_note']})")
    print(f"T_total_sim={result['T_total_sim']} 拍"
          f"（stage_{stages[0]} BEGIN → stage_{stages[-1]} done；不含 S4/dump）"
          f" | WBARRIER={'有' if wb else '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
