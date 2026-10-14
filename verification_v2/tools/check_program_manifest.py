#!/usr/bin/env python3
"""check_program_manifest —— ADR-061 D5：adr061_program_manifest_v1 独立检查器。

只读 case 目录（program_manifest.json + g3_expect.json + 指令/数据/HBM 镜像），
不读 build_copy——全部声明从 manifest 自含字段重算复核：

  ① schema/段链：段数 = K+2；LOAD=stage_0、COMPUTE=1..K、STORE=K+1；
     predecessor 线性链；COMPUTE 迭代区间从 0 链式覆盖到 n_lines。
  ② 布局重算：hbm_layout 基址 = 0x1000*(i+1)（h2d 声明序）、输出随后；
     4KB 区两两不相交；bank 两两相异；bytes == n_lines*16。
  ③ 指令再发射（重算哈希后语义篡改的克星）：从 manifest 声明的
     (bank, byte_base, n_lines) 重算描述符并经真 LIRPacker 再发射，
     与 case 内 iq_ldb/iq_ldb_axi/iq_stb/iq_stb_axi.hex **逐字节相等**；
     全部段全部 IQ 文件 sha256 == manifest 记录。
  ④ 预载形态：LOAD bank = g2 行索引哨兵；输出 bank = 全零；smc_1..7 = 辅助哨兵。
  ⑤ 终态形态：LOAD bank 终态 = 哨兵底 + 窗口 == memory_init1 对应 HBM 区
     （LOAD 送达与期望终态的同源互校）；输出终态窗口全非零 + 窗外全零。
  ⑥ HBM 镜像：区域 sha256 == manifest 锚；窗口外 == canary 图案；
     输出窗全零；init2..8 == 标记图案；g3_expect.hbm_preload.files ==
     快照实际 sha256（双锚互校）。
  ⑦ 屏障参数：g3_expect 计数 == 实际文件行数；wbarrier/s4 与 manifest 声明一致。

任一不符 → RED（exit 1，逐项列出）；全绿 exit 0。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, "src"))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import ldb_review_case_gen as g2  # noqa: E402
import stb_solo_case_gen as s1    # noqa: E402
from unified_lcs_case_gen import (  # noqa: E402
    PROGRAM_MANIFEST_SCHEMA, PROGRAM_MANIFEST_SCHEMA_V2, EXPECT_SCHEMA,
    LOAD_ROLES, OUTPUT_ROLE, SMC_COUNT,
    HBM_REGION_BYTES, HBM_FIRST_BASE, UR_BANKS, UR_BANK_DEPTH, LINE_BYTES,
    chunk_descs, _sha256_file, _read_hex_lines)

SCHEMA = PROGRAM_MANIFEST_SCHEMA


class Checker:
    def __init__(self, case_dir: Path):
        self.case = Path(case_dir)
        self.bad: list[str] = []

    def err(self, msg: str) -> None:
        self.bad.append(msg)

    def run(self) -> int:
        man_p = self.case / "program_manifest.json"
        exp_p = self.case / "g3_expect.json"
        if not man_p.is_file():
            self.err(f"缺 program_manifest.json: {man_p}")
            return self._finish()
        if not exp_p.is_file():
            self.err(f"缺 g3_expect.json: {exp_p}")
            return self._finish()
        man = json.loads(man_p.read_text(encoding="utf-8"))
        exp = json.loads(exp_p.read_text(encoding="utf-8"))
        schema = man.get("schema_version")
        if schema == SCHEMA:
            is_v2 = False
        elif schema == PROGRAM_MANIFEST_SCHEMA_V2:
            is_v2 = True
        else:
            self.err(f"schema_version={schema!r} 未识别（v1/v2 均不符）")
            return self._finish()
        if exp.get("schema") != EXPECT_SCHEMA:
            self.err(f"g3_expect schema={exp.get('schema')!r} != {EXPECT_SCHEMA!r}")
        if is_v2:
            return self._run_v2_body(man, exp)

        n = man.get("n")
        if not isinstance(n, int) or n % 4 != 0:
            self.err(f"n={n!r} 非 4 的倍数")
            return self._finish()
        n_lines = n // 4
        if man.get("n_lines") != n_lines:
            self.err(f"manifest n_lines={man.get('n_lines')} != n//4={n_lines}")

        self._check_layout(man, n_lines)
        stages = man.get("stages") or []
        K = self._check_stage_chain(man, stages, n_lines)
        if K is None:
            return self._finish()
        self._check_iq_files(stages)
        self._check_reemit(man, stages, n_lines)
        self._check_preloads(man, stages, n_lines)
        self._check_hbm(man, exp, n_lines)
        self._check_barrier_params(man, exp, stages, n_lines)
        return self._finish()

    # ② 布局重算
    def _check_layout(self, man: dict, n_lines: int) -> None:
        lay = man.get("hbm_layout") or []
        h2d = [e for e in lay if e.get("direction") == "h2d"]
        d2h = [e for e in lay if e.get("direction") == "d2h"]
        if len(d2h) != 1:
            self.err(f"hbm_layout d2h 区数 {len(d2h)} != 1")
        if not h2d:
            self.err("hbm_layout 无 h2d 区")
        banks: set[int] = set()
        spans = []
        for i, e in enumerate(h2d):
            want_base = HBM_FIRST_BASE + i * HBM_REGION_BYTES
            if e.get("byte_base") != want_base:
                self.err(f"h2d[{i}] {e.get('name')} byte_base="
                         f"{e.get('byte_base')} != 重算 {want_base}")
            if e.get("role") not in LOAD_ROLES:
                self.err(f"h2d[{i}] role={e.get('role')!r} ∉ {LOAD_ROLES}")
            if e.get("bytes") != n_lines * LINE_BYTES or e.get("lines") != n_lines:
                self.err(f"h2d[{i}] bytes/lines 与 n_lines={n_lines} 不符: {e}")
            banks.add(e.get("bank"))
            spans.append((e.get("byte_base", 0), e.get("name")))
        if d2h:
            e = d2h[0]
            want_base = HBM_FIRST_BASE + len(h2d) * HBM_REGION_BYTES
            if e.get("byte_base") != want_base:
                self.err(f"d2h byte_base={e.get('byte_base')} != 重算 {want_base}")
            if e.get("role") != OUTPUT_ROLE:
                self.err(f"d2h role={e.get('role')!r} != {OUTPUT_ROLE!r}")
            banks.add(e.get("bank"))
            spans.append((e.get("byte_base", 0), e.get("name")))
        if len(banks) != len(lay):
            self.err("hbm_layout bank 不两两相异（窗交叠）")
        spans.sort()
        for (a, na), (b, nb) in zip(spans, spans[1:]):
            if b < a + HBM_REGION_BYTES:
                self.err(f"HBM 4KB 区交叠: {na}@{a:#x} vs {nb}@{b:#x}")

    # ① 段链
    def _check_stage_chain(self, man: dict, stages: list, n_lines: int):
        if len(stages) < 3:
            self.err(f"段数 {len(stages)} < 3（至少 LOAD+COMPUTE+STORE）")
            return None
        K = len(stages) - 2
        for i, st in enumerate(stages):
            if st.get("stage_id") != i:
                self.err(f"段号乱序: stages[{i}].stage_id={st.get('stage_id')}")
            want_kind = "LOAD" if i == 0 else ("STORE" if i == len(stages) - 1
                                               else "COMPUTE")
            if st.get("kind") != want_kind:
                self.err(f"stage_{i} kind={st.get('kind')} != {want_kind}")
            if st.get("predecessor") != (i - 1 if i > 0 else None):
                self.err(f"stage_{i} predecessor={st.get('predecessor')} 非线性链")
        load, store = stages[0], stages[-1]
        lay = man.get("hbm_layout") or []
        h2d_names = sorted(e["name"] for e in lay if e.get("direction") == "h2d")
        cov_names = sorted(c["tensor"] for c in load.get("covers", []))
        if cov_names != h2d_names:
            self.err(f"LOAD covers {cov_names} != h2d 张量 {h2d_names}")
        for c in load.get("covers", []):
            if c.get("line0") != 0 or c.get("lines") != n_lines:
                self.err(f"LOAD cover {c.get('tensor')} 行窗 != [0,{n_lines})")
        d2h = [e for e in lay if e.get("direction") == "d2h"]
        scov = store.get("covers", [])
        if d2h and (len(scov) != 1 or scov[0].get("tensor") != d2h[0]["name"]
                    or scov[0].get("hbm_byte_base") != d2h[0]["byte_base"]):
            self.err("STORE covers 与 d2h 区不符")
        win = store.get("expected_hbm_write_window") or {}
        if d2h and (win.get("byte_base") != d2h[0]["byte_base"]
                    or win.get("line0") != d2h[0]["byte_base"] // LINE_BYTES
                    or win.get("lines") != n_lines):
            self.err(f"STORE expected_hbm_write_window 与布局不符: {win}")
        if store.get("expected_ur_write_windows") != []:
            self.err("STORE 声明了 UR 写窗（应为空——STORE 不写 UR）")
        # COMPUTE 迭代链 + 写窗
        out_bank = d2h[0]["bank"] if d2h else None
        prev_end = 0
        for i in range(1, len(stages) - 1):
            st = stages[i]
            rng = st.get("iteration_range")
            if not (isinstance(rng, list) and len(rng) == 2
                    and rng[0] == prev_end):
                self.err(f"stage_{i} iteration_range={rng} 不接续 prev_end={prev_end}")
                continue
            prev_end = rng[1]
            wins = st.get("expected_ur_write_windows") or []
            if wins != [{"bank": out_bank, "line0": rng[0],
                         "lines": rng[1] - rng[0]}]:
                self.err(f"stage_{i} 期望写窗 {wins} != "
                         f"[bank{out_bank} {rng[0]}+{rng[1] - rng[0]}]")
        if prev_end != n_lines:
            self.err(f"COMPUTE 迭代链终点 {prev_end} != n_lines={n_lines}")
        # LOAD 期望写窗 == h2d bank 集
        want_wins = sorted([{"bank": e["bank"], "line0": 0, "lines": n_lines}
                            for e in lay if e.get("direction") == "h2d"],
                           key=lambda w: w["bank"])
        got_wins = sorted(load.get("expected_ur_write_windows") or [],
                          key=lambda w: w["bank"])
        if got_wins != want_wins:
            self.err(f"LOAD 期望 UR 写窗 {got_wins} != 布局重算 {want_wins}")
        return K

    # ③a 文件哈希
    def _check_iq_files(self, stages: list) -> None:
        for st in stages:
            for fm in st.get("iq_files", []):
                p = self.case / "smi_iq_inst" / f"stage_{st['stage_id']}" / fm["file"]
                if not p.is_file():
                    self.err(f"缺 IQ 文件: {p}")
                    continue
                if _sha256_file(p) != fm.get("sha256"):
                    self.err(f"IQ 文件哈希不符: {p.name} @stage_{st['stage_id']}"
                             f"（篡改或未重封印）")
                if len(_read_hex_lines(p)) != fm.get("lines"):
                    self.err(f"IQ 文件行数不符: {p.name}")

    # ③b 指令再发射逐字节比对（v2：en=7 广播；v1：en=0 单 SMC）
    def _check_reemit(self, man: dict, stages: list, n_lines: int,
                      en: int = 0) -> None:
        descs = chunk_descs(n_lines)
        lay = man.get("hbm_layout") or []
        h2d = [e for e in lay if e.get("direction") == "h2d"]
        d2h = [e for e in lay if e.get("direction") == "d2h"]
        if not d2h:
            return
        cwd = os.getcwd()
        os.chdir(ROOT)  # LIRPacker(config_dir="hw_config") 相对路径
        try:
            ldb_all: list[str] = []
            axi_all: list[str] = []
            for e in h2d:
                ls, xs = g2.build_and_emit(descs, e["bank"], 0, e["byte_base"],
                                           en)
                ldb_all += ls
                axi_all += xs
            stb, stbaxi = s1.build_and_emit(descs, d2h[0]["bank"], 0,
                                            d2h[0]["byte_base"], en)
        finally:
            os.chdir(cwd)
        s0 = self.case / "smi_iq_inst" / "stage_0"
        sN = self.case / "smi_iq_inst" / f"stage_{len(stages) - 1}"
        for p, want in ((s0 / "iq_ldb.hex", ldb_all),
                        (s0 / "iq_ldb_axi.hex", axi_all),
                        (sN / "iq_stb.hex", stb),
                        (sN / "iq_stb_axi.hex", stbaxi)):
            if not p.is_file():
                self.err(f"缺 {p}")
                continue
            got = _read_hex_lines(p)
            if [g.lower() for g in got] != [w.lower() for w in want]:
                self.err(f"{p.name} 与 manifest 声明参数再发射不逐字节相等"
                         f"（指令语义篡改或声明失实）")

    # ④ 预载形态
    def _check_preloads(self, man: dict, stages: list, n_lines: int) -> None:
        lay = man.get("hbm_layout") or []
        load_banks = {e["bank"] for e in lay if e.get("direction") == "h2d"}
        out = [e for e in lay if e.get("direction") == "d2h"]
        out_bank = out[0]["bank"] if out else None
        for b in range(UR_BANKS):
            p = self.case / "smc_0" / "ur_data" / f"bank{b}.hex"
            if not p.is_file():
                self.err(f"缺 {p}")
                continue
            ls = _read_hex_lines(p)
            if b in load_banks:
                want = [g2.sentinel_line(b, k) for k in range(UR_BANK_DEPTH)]
                if ls != want:
                    self.err(f"bank{b} 预载 != 行索引哨兵（LOAD 承重纪律破坏）")
            elif b == out_bank:
                if any(int(x, 16) != 0 for x in ls):
                    self.err(f"输出 bank{b} 预载非全零")
        for i in range(1, 8):
            p = self.case / f"smc_{i}" / "ur_data" / "bank0.hex"
            if not p.is_file():
                self.err(f"缺辅助 SMC 预载: {p}")
                continue
            if _read_hex_lines(p) != [g2.sentinel_aux_line(i, 0, k)
                                      for k in range(UR_BANK_DEPTH)]:
                self.err(f"smc_{i} bank0 预载 != 辅助哨兵")

    # ⑤⑥ HBM 镜像与终态互校
    def _check_hbm(self, man: dict, exp: dict, n_lines: int) -> None:
        snap = self.case / "memory_init_snapshot"
        init1_p = snap / "memory_init1.hex"
        if not init1_p.is_file():
            self.err(f"缺 {init1_p}")
            return
        init1 = _read_hex_lines(init1_p)
        lay = man.get("hbm_layout") or []
        rh = man.get("hbm_region_sha256") or {}
        import hashlib as _hl
        for e in lay:
            l0 = e["byte_base"] // LINE_BYTES
            if l0 + e["lines"] > len(init1):
                self.err(f"HBM 区 {e['name']} 越出 memory_init1 行数 {len(init1)}")
                continue
            region = init1[l0:l0 + e["lines"]]
            sha = _hl.sha256(("\n".join(region) + "\n").encode()).hexdigest()
            if rh.get(e["name"]) != sha:
                self.err(f"HBM 区 {e['name']} sha256 与 manifest 锚不符")
            if e["direction"] == "d2h":
                if any(int(x, 16) != 0 for x in region):
                    self.err(f"输出区 {e['name']} 预填非全零")
        # 窗口外 canary
        spans = [(e["byte_base"] // LINE_BYTES,
                  e["byte_base"] // LINE_BYTES + e["lines"]) for e in lay]
        for k, ln in enumerate(init1):
            if any(a <= k < b for a, b in spans):
                continue
            if ln != g2.preload_pattern_line(k):
                self.err(f"memory_init1 行 {k} 非 canary 图案（泄漏检测面破坏）")
                break
        # init2..8 标记 + 双锚互校
        hp = (exp.get("hbm_preload") or {}).get("files") or {}
        for i in range(2, 9):
            p = snap / f"memory_init{i}.hex"
            if not p.is_file():
                self.err(f"缺 {p}")
                continue
            ls = _read_hex_lines(p)
            if ls != [g2.mark_pattern_line(k) for k in range(len(ls))]:
                self.err(f"memory_init{i}.hex 非标记图案")
            if hp.get(f"memory_init{i}.hex") != _sha256_file(p):
                self.err(f"memory_init{i}.hex 与 g3_expect 冻结哈希不符")
        if hp.get("memory_init1.hex") != _sha256_file(init1_p):
            self.err("memory_init1.hex 与 g3_expect 冻结哈希不符")
        # ⑤ LOAD bank 终态窗 == HBM 源区；输出终态形态
        for e in lay:
            b = e["bank"]
            fin_p = self.case / "smc_0" / "output_bank" / f"bank{b}_final.hex"
            if not fin_p.is_file():
                self.err(f"缺 {fin_p}")
                continue
            fin = _read_hex_lines(fin_p)
            l0 = e["byte_base"] // LINE_BYTES
            if e["direction"] == "h2d":
                want = [g2.sentinel_line(b, k) for k in range(UR_BANK_DEPTH)]
                want[:e["lines"]] = init1[l0:l0 + e["lines"]]
                if fin != want:
                    self.err(f"bank{b} 终态 != 哨兵底+HBM 源窗（LOAD 送达互校失败）")
            else:
                if any(int(x, 16) == 0 for x in fin[:e["lines"]]):
                    self.err(f"输出 bank{b} 终态窗口存在全零行（与零预填不可区分）")
                if any(int(x, 16) != 0 for x in fin[e["lines"]:]):
                    self.err(f"输出 bank{b} 终态窗外 [行{e['lines']}..) 非零")

    # ⑦ 屏障参数
    def _check_barrier_params(self, man: dict, exp: dict, stages: list,
                              n_lines: int) -> None:
        s0 = self.case / "smi_iq_inst" / "stage_0"
        sN = self.case / "smi_iq_inst" / f"stage_{len(stages) - 1}"
        bar = exp.get("barrier") or {}
        if bar.get("exp_ldb_done") != len(_read_hex_lines(s0 / "iq_ldb.hex")):
            self.err("barrier.exp_ldb_done != iq_ldb.hex 实际行数")
        if bar.get("exp_ldbaxi_done") != len(_read_hex_lines(s0 / "iq_ldb_axi.hex")):
            self.err("barrier.exp_ldbaxi_done != iq_ldb_axi.hex 实际行数")
        wb = exp.get("wbarrier") or {}
        lay = man.get("hbm_layout") or []
        d2h = [e for e in lay if e.get("direction") == "d2h"]
        if d2h and (wb.get("bank") != d2h[0]["bank"]
                    or wb.get("writes") != n_lines
                    or wb.get("stage") != len(stages) - 2):
            self.err(f"wbarrier 参数与 manifest 不符: {wb}")
        s4 = exp.get("s4barrier") or {}
        if d2h and (s4.get("addr0") != d2h[0]["byte_base"] // LINE_BYTES
                    or s4.get("writes") != n_lines
                    or s4.get("bresp") != len(_read_hex_lines(sN / "iq_stb_axi.hex"))):
            self.err(f"s4barrier 参数与 manifest/实际文件不符: {s4}")
        if exp.get("stage_count") != len(stages):
            self.err(f"g3_expect stage_count={exp.get('stage_count')} "
                     f"!= manifest 段数 {len(stages)}")

    # ==================== v2（equal_8smc_v1）主体：M1-M5 ====================
    def _run_v2_body(self, man: dict, exp: dict) -> int:
        # M5 规模与分片块
        part = man.get("partition")
        if not isinstance(part, dict) or part.get("scheme") != "equal_8smc_v1":
            self.err("(M5) partition 块缺失或 scheme 非 equal_8smc_v1")
            return self._finish()
        n_total, n_per = part.get("n_total"), part.get("n_per")
        if man.get("n") != n_total:
            self.err(f"(M5) manifest n={man.get('n')} != partition.n_total="
                     f"{n_total}")
        if not isinstance(n_total, int) or n_total % (4 * SMC_COUNT) != 0:
            self.err(f"(M5) n_total={n_total!r} 非 {4 * SMC_COUNT} 的倍数")
            return self._finish()
        if n_per != n_total // SMC_COUNT or part.get("smc_count") != SMC_COUNT:
            self.err(f"(M5) partition n_per={n_per}/smc_count="
                     f"{part.get('smc_count')} 与 n_total={n_total} 不一致")
            return self._finish()
        n_lines = n_per // 4
        if man.get("n_lines") != n_lines:
            self.err(f"(M5) manifest n_lines={man.get('n_lines')} != n_per//4="
                     f"{n_lines}")
        # M1 结构面：全段 active_smcs == [0..7]，无旧单值键
        stages = man.get("stages") or []
        for st in stages:
            if st.get("active_smcs") != list(range(SMC_COUNT)):
                self.err(f"(M1) stage_{st.get('stage_id')} active_smcs="
                         f"{st.get('active_smcs')} != [0..{SMC_COUNT - 1}]")
            if "active_smc" in st:
                self.err(f"(M1) stage_{st.get('stage_id')} 残留旧键 active_smc")
        self._check_layout(man, n_lines)
        K = self._check_stage_chain(man, stages, n_lines)
        if K is None:
            return self._finish()
        self._check_iq_files(stages)
        self._check_reemit(man, stages, n_lines, SMC_COUNT - 1)
        self._check_m1_enablement(man, stages)
        self._check_v2_preloads(man, n_lines)
        self._check_v2_hbm_golden(man, exp, n_lines)
        self._check_barrier_params(man, exp, stages, n_lines)
        return self._finish()

    # M1 manifest.active_smcs ↔ 解码指令使能
    def _check_m1_enablement(self, man: dict, stages: list) -> None:
        """LOAD/STORE 段：每个队列 body VALID=1 行 smc_id_en 全集 == {7}（8 目
        标广播）且非空（缺席须正面取证，不许无证据放行）；COMPUTE 段：产物=build
        字节拷贝已由③哈希锚定，此处只否决反证（可解码 body 行出现 en≠7 即 RED）。"""
        import decode_case_smc_enablement as dec
        try:
            rep = dec.decode_case(str(self.case))
        except Exception as exc:  # noqa: BLE001——解码失败须响亮入档
            self.err(f"(M1) case 使能解码异常: {exc}")
            return
        n_st = len(rep.get("stages", []))
        if n_st != len(stages):
            self.err(f"(M1) 解码段数 {n_st} != manifest 段数 {len(stages)}")
            return
        want = SMC_COUNT - 1
        for i, st in enumerate(rep["stages"]):
            is_ls = i in (0, n_st - 1)
            for q in st["queues"]:
                vals = q.get("smc_id_en_vld1") or []
                if is_ls:
                    if not vals:
                        self.err(f"(M1) {st['stage']}/{q['file']} 无 body VALID "
                                 f"行——使能无法正面取证")
                    elif set(vals) != {want}:
                        self.err(f"(M1) {st['stage']}/{q['file']} body "
                                 f"smc_id_en={sorted(set(vals))} != {{{want}}}")
                else:
                    bad = [v for v in vals if v != want]
                    if bad:
                        self.err(f"(M1) COMPUTE {st['stage']}/{q['file']} body"
                                 f" smc_id_en 出现 {sorted(set(bad))} != "
                                 f"{want}")

    # ④v2 预载形态（8 通道对称：LOAD bank 哨兵 / 输出 bank 全零）
    def _check_v2_preloads(self, man: dict, n_lines: int) -> None:
        lay = man.get("hbm_layout") or []
        load_banks = {e["bank"] for e in lay if e.get("direction") == "h2d"}
        out = [e for e in lay if e.get("direction") == "d2h"]
        out_bank = out[0]["bank"] if out else None
        for k in range(SMC_COUNT):
            for b in range(UR_BANKS):
                p = self.case / f"smc_{k}" / "ur_data" / f"bank{b}.hex"
                if not p.is_file():
                    self.err(f"(M2) 缺预载文件: smc_{k}/ur_data/bank{b}.hex")
                    continue
                ls = _read_hex_lines(p)
                if b in load_banks:
                    want = [g2.sentinel_line(b, i) for i in range(UR_BANK_DEPTH)]
                    if ls != want:
                        self.err(f"(M2) smc_{k} bank{b} 预载 != 行索引哨兵"
                                 f"（LOAD 承重纪律破坏）")
                elif b == out_bank:
                    if any(int(x, 16) != 0 for x in ls):
                        self.err(f"(M2) smc_{k} 输出 bank{b} 预载非全零")

    # M2 输入文件+哈希 / M3 golden 枚举+哈希 / M4 可区分 / ⑤⑥ 逐通道互校
    def _check_v2_hbm_golden(self, man: dict, exp: dict, n_lines: int) -> None:
        import hashlib as _hl
        snap = self.case / "memory_init_snapshot"
        lay = man.get("hbm_layout") or []
        d2h = [e for e in lay if e.get("direction") == "d2h"]
        out_bank = d2h[0]["bank"] if d2h else None
        rh = man.get("hbm_region_sha256") or {}
        imgs: dict[int, list[str]] = {}
        for k in range(SMC_COUNT):
            p = snap / f"memory_init{k + 1}.hex"
            if not p.is_file():
                self.err(f"(M2) 缺镜像文件: memory_init{k + 1}.hex")
                continue
            imgs[k] = _read_hex_lines(p)
        spans = [(e["byte_base"] // LINE_BYTES,
                  e["byte_base"] // LINE_BYTES + e["lines"]) for e in lay]
        # M2：逐通道区域哈希 ↔ manifest 锚 + 输出预填 + 窗外 canary + 双锚
        hp = (exp.get("hbm_preload") or {}).get("files") or {}
        for k, img in imgs.items():
            for e in lay:
                l0 = e["byte_base"] // LINE_BYTES
                if l0 + e["lines"] > len(img):
                    self.err(f"(M2) smc_{k} HBM 区 {e['name']} 越出镜像行数")
                    continue
                region = img[l0:l0 + e["lines"]]
                sha = _hl.sha256(("\n".join(region) + "\n").encode()).hexdigest()
                per = rh.get(e["name"])
                if not isinstance(per, dict) or per.get(f"smc_{k}") != sha:
                    self.err(f"(M2) smc_{k} HBM 区 {e['name']} sha256 与 "
                             f"manifest per-SMC 锚不符")
                if e["direction"] == "d2h" \
                        and any(int(x, 16) != 0 for x in region):
                    self.err(f"(M2) smc_{k} 输出区 {e['name']} 预填非全零")
            for i, ln in enumerate(img):
                if any(a <= i < b for a, b in spans):
                    continue
                if ln != g2.preload_pattern_line(i):
                    self.err(f"(M2) memory_init{k + 1} 行 {i} 非 canary 图案"
                             f"（泄漏检测面破坏）")
                    break
            p = snap / f"memory_init{k + 1}.hex"
            if hp.get(p.name) != _sha256_file(p):
                self.err(f"(M2) {p.name} 与 g3_expect 冻结哈希不符")
        # ⑤ 逐通道：LOAD 终态 == 哨兵底 + 本通道 HBM 源窗；输出终态形态
        for k in range(SMC_COUNT):
            if k not in imgs:
                continue
            for e in lay:
                b = e["bank"]
                fin_p = self.case / f"smc_{k}" / "output_bank" \
                    / f"bank{b}_final.hex"
                if not fin_p.is_file():
                    continue  # 缺文件由 M3 记
                fin = _read_hex_lines(fin_p)
                l0 = e["byte_base"] // LINE_BYTES
                if e["direction"] == "h2d":
                    want = [g2.sentinel_line(b, i) for i in range(UR_BANK_DEPTH)]
                    want[:e["lines"]] = imgs[k][l0:l0 + e["lines"]]
                    if fin != want:
                        self.err(f"(M2) smc_{k} bank{b} 终态 != 哨兵底+本通道"
                                 f"HBM 源窗（LOAD 送达互校失败）")
                else:
                    if any(int(x, 16) == 0 for x in fin[:e["lines"]]):
                        self.err(f"(M4-sup) smc_{k} 输出 bank{b} 终态窗口存在"
                                 f"全零行（与零预填不可区分）")
                    if any(int(x, 16) != 0 for x in fin[e["lines"]:]):
                        self.err(f"(M4-sup) smc_{k} 输出 bank{b} 终态窗外"
                                 f"[行{e['lines']}..) 非零")
        # M3：golden_files 显式枚举 8×8 bank + 逐文件 sha256
        gf = man.get("golden_files")
        if not isinstance(gf, list):
            self.err(f"(M3) golden_files 缺失或非列表: {type(gf).__name__}")
        else:
            if len(gf) != SMC_COUNT * UR_BANKS:
                self.err(f"(M3) golden_files 枚举 {len(gf)} != "
                         f"{SMC_COUNT}×{UR_BANKS}")
            for ent in gf:
                rel = ent.get("file") if isinstance(ent, dict) else None
                p = self.case / rel if rel else None
                if p is None or not p.is_file():
                    self.err(f"(M3) 缺 golden 文件: {rel}——缺/不符即拒收")
                    continue
                if _sha256_file(p) != ent.get("sha256"):
                    self.err(f"(M3) golden 文件哈希不符: {rel}")
        # M4：8 份 golden 输出窗两两不全等（可区分性硬要求）
        if out_bank is not None:
            wins: list[tuple[int, list[str]]] = []
            for k in range(SMC_COUNT):
                p = self.case / f"smc_{k}" / "output_bank" \
                    / f"bank{out_bank}_final.hex"
                if p.is_file():
                    wins.append((k, _read_hex_lines(p)[:n_lines]))
            if len(wins) == SMC_COUNT:
                for x in range(len(wins)):
                    for y in range(x + 1, len(wins)):
                        if wins[x][1] == wins[y][1]:
                            self.err(f"(M4) smc_{wins[x][0]} 与 smc_{wins[y][0]}"
                                     f" golden 输出窗全等——可区分性破坏，"
                                     f"配置无效（拒收）")

    def _finish(self) -> int:
        if self.bad:
            print(f"[PM-CHK] RED（{len(self.bad)} 项）:")
            for m in self.bad:
                print(f"  - {m}")
            return 1
        print(f"[PM-CHK] GREEN: {self.case}（schema/段链/布局/再发射/预载/"
              f"终态/HBM 双锚/屏障参数 全绿）")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("case_dir", type=Path)
    a = ap.parse_args()
    return Checker(a.case_dir).run()


if __name__ == "__main__":
    sys.exit(main())
