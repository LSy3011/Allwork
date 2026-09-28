"""ADR-053 D8：RtoVR 稀疏化 + VIN/VOUT 绕行的 14 条回归守卫。

每条守卫对应一个已实证的失效模式（ADR-053 §D8 表）；守卫⑥（变异测试）以
TestMutation 实现——对每条守卫注入病理，断言确实变红，防「永绿断言零信号」
（ADR-052 D7 同款纪律）。**全部比整数，禁止比字符串。**

| # | 守卫 | 对应失效 |
|---|---|---|
| ① | get_rtovr_connectivity() 40 项与 RtoVR.v 掩码逐位一致 | 邻接表转录漂移 |
| ② | master↔units/*.yaml↔iq_config↔system_core 四向同步 | 双处不同步静默失效 |
| ③ | 编译器 emit 的全部 (out,sel) 满足邻接表（add/mul e2e） | 472 条屏蔽回归 |
| ④ | 模拟器 rto_vr 屏蔽行为与 RTL 一致（数据 0、vld 仍传播） | 模拟器假绿回归 |
| ⑤ | VIN/VOUT code_bits/BITS_CODE=7（帧 41+7，body[6] 保留）且 pipeline_delays=1 | 位宽/延迟回退 |
| ⑥ | TestMutation：对每条守卫注入病理断言变红 | 永绿断言零信号 |
| ⑦ | VOUT0 容量负向：同拍两条 compute_D→out16 必被拆开 | VOUT0 单口冲突静默漏检 |
| ⑧ | payload_factory：显式 smc_id_en 优先于缺省分支（B3 排序） | smc_id_en 静默发错 |
| ⑨ | 双世界：golden select 在 sparse∩dense 均合法（锁不变量） | 拿 dense 放松约束 |
| ⑩ | rtovr_conn_table.tsv 行宽一致性（哨兵：当前漂移 28 行） | 文档表转录漂移 |
| ⑪ | pass 后 UR_READ 直连边不残留于计算槽（仅 14/15/34） | 陈旧显式 sel 假阳跳过绕行 |
| ⑫ | VIN/VOUT 帧级 vld 落位：body vld=1 的行 header VLD 必须在 bit47 | code_bits 6/7 混淆致全行 header vld=0 |
| ⑬ | 模拟器侧 decoder payload=7 且 header VLD offset=47（发射/仿真两侧帧宽同源） | sim spec 字段和滞后致 sim 全链路死（假红） |
| ⑭ | mul 产物全流 level-0 平坦协议：全流 embed==0（较硬件死区 embed∈{1..6} 有意加严；embed=7 是 golden wait 哨兵不入我方产物）+ 末行 jump 终止 + op 行 code≠0（ADR-054） | embed∈{1..6} 在 RTL array-replay 卡死（bank2 仅 3 行）；全零 code 假绿 |
"""
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(_ROOT))
sys.path.append(str(_ROOT / "src"))
sys.path.append(str(_ROOT / "sim"))
sys.path.append(str(_ROOT / "verification_v2" / "tools"))

from api.hw_spec import HardwareSpec                       # noqa: E402
from driver.hw_resource import ResourceType as RT          # noqa: E402
from ir.mir.mir_op import MIROp                            # noqa: E402
from sim.tests.helpers import make_op                      # noqa: E402
from src.driver.payload_factory import UnitPayloadFactory  # noqa: E402
from synthesis.ucode_solver import UCodeSynthesizer        # noqa: E402

import gen_rtovr_allowed as gen_allowed                    # noqa: E402
import validate_rtovr_golden_parity as vtool               # noqa: E402

TSV_PATH = Path.home() / "rtovr_mask_probe" / "rtovr_conn_table.tsv"
GOLDEN_24_DIR = (Path.home() / "u280_sim_v422" / "fpga_pkg"
                 / "sub_8smc_v3_fpga_verify" / "case" / "smi_iq_inst" / "stage_1")
VIN_UNITS = ("VIN0", "VIN1", "VOUT0")


# ---------------------------------------------------------------- guard 函数（返回违例列表，空=绿）

def g1_table_vs_rtl(spec) -> list:
    """① yaml 邻接表 vs RTL 掩码（AST/正则直读 RtoVR.v），40 项逐位集合相等。"""
    rtl = {k: frozenset(v) for k, v in gen_allowed.derive_allowed_from_rtl().items()}
    tbl = spec.get_rtovr_connectivity()
    bad = []
    if set(rtl) != set(tbl):
        bad.append(f"键集不一致: rtl={sorted(rtl)} vs yaml={sorted(tbl)}")
    for out in sorted(set(rtl) & set(tbl)):
        if rtl[out] != tbl[out]:
            bad.append(f"out{out}: rtl={sorted(rtl[out])} vs yaml={sorted(tbl[out])}")
    return bad


def g2_four_way_sync(root: Path) -> list:
    """② master functional_units ↔ units/{vin0,vin1,vout0}.yaml ↔ iq_config ↔ system_core。"""
    bad = []
    master = yaml.safe_load((root / "hw_config/specs/aiacc_v2.4_master.yaml").read_text())
    m_units = master["functional_units"]
    for u in VIN_UNITS:
        # master ↔ units/*.yaml（解析值深等：latency/ports/micro_instructions）
        mu = {k: v for k, v in m_units[u].items() if k in ("unit_name", "latency", "ports", "micro_instructions")}
        uu_raw = yaml.safe_load((root / f"hw_config/specs/units/{u.lower()}.yaml").read_text())
        uu = {k: v for k, v in uu_raw.items() if k in ("unit_name", "latency", "ports", "micro_instructions")}
        if mu != uu:
            bad.append(f"{u}: master↔units/{u.lower()}.yaml 不同步")
    # iq_config：BITS_CODE=7（顶层键 iq_config；2026-09-12 errata：6→7，CODE[6] 保留位）
    iqcfg = yaml.safe_load((root / "hw_config/iq_config.yaml").read_text())
    iqcfg = iqcfg.get("iq_config", iqcfg)
    for u in VIN_UNITS:
        ent = iqcfg.get(f"IQ_{u}", {})
        if ent.get("module") != u or int(ent.get("BITS_CODE", -1)) != 7:
            bad.append(f"IQ_{u}: iq_config 条目 module/BITS_CODE≠({u},7): {ent}")
    # system_core：unit_instances + per_smc 各 1；iq_registry 三键互异
    sc = yaml.safe_load((root / "hw_config/specs/system/system_core.yaml").read_text())
    rc = sc.get("resource_constraints", sc)
    inst = rc.get("unit_instances", {})
    inst_ps = rc.get("unit_instances_per_smc", {})
    for u in VIN_UNITS:
        if int(inst.get(u, -1)) != 1:
            bad.append(f"system_core unit_instances.{u}≠1: {inst.get(u)}")
        if int(inst_ps.get(u, -1)) != 1:
            bad.append(f"system_core unit_instances_per_smc.{u}≠1: {inst_ps.get(u)}")
    reg = sc.get("iq_registry", {})
    ids = [reg.get(u) for u in VIN_UNITS]
    if any(i is None for i in ids) or len(set(ids)) != 3:
        bad.append(f"system_core iq_registry 缺 VIN/VOUT 键或 id 撞车: {ids}")
    return bad


def g5_bits_and_delays(root: Path) -> list:
    """⑤ iq_config BITS_CODE=7（帧 41+7，CODE[6] 保留位）且 pipeline_delays=1。"""
    bad = []
    iqcfg = yaml.safe_load((root / "hw_config/iq_config.yaml").read_text())
    iqcfg = iqcfg.get("iq_config", iqcfg)
    delays = yaml.safe_load((root / "hw_config/pipeline_delays.yaml").read_text())
    dmap = delays.get("pipeline_delays", delays)
    for u in VIN_UNITS:
        ent = iqcfg.get(f"IQ_{u}", {})
        if int(ent.get("BITS_CODE", -1)) != 7:
            bad.append(f"IQ_{u}.BITS_CODE={ent.get('BITS_CODE')}≠7")
        if int(dmap.get(u, -1)) != 1:
            bad.append(f"pipeline_delays.{u}={dmap.get(u)}≠1")
    return bad


def g12_vin_frame_vld(build_dir: Path) -> list:
    """⑫ 帧级落位守卫：body vld=1（CRU[0]）的 VIN 行，header VLD 必须在 bit47。

    失效标本（2026-09-12 VCS 取证）：VIN code_bits 曾按 CRU_VIN_W=6 建模，
    header 落在 [46:6] → RTL 读 bit47 恒 0 → 流全行 header vld=0，bank2 全零
    （A/B 实证：h2 换 golden vin 则 bank2 写出前 3 行、h3 换我方 vin 恒 0 → 主犯）。
    D7 只验 body 内容（低 6 位）对此盲；本条守帧位移。返回 [(file, line_idx, 原因)]。"""
    bad = []
    for fn in ("iq_vin0.hex", "iq_vin1.hex", "iq_vout0.hex"):
        p = build_dir / fn
        if not p.is_file():
            bad.append((fn, -1, "missing"))
            continue
        for i, ln in enumerate(p.read_text().splitlines()):
            ln = ln.strip()
            if not ln:
                continue
            v = int(ln, 16)
            if (v & 1) and ((v >> 47) & 1) != 1:   # body vld=1 → 帧 bit47 必须=1
                bad.append((fn, i, "bit47=0"))
    return bad


def _decoder_layout_bad(spec_like, u: str) -> list:
    """⑬ 判据核：对任一 UnitSpec 建 InstructionDecoder，payload 必=7 且 header VLD 必=bit47。"""
    from smc_simulator.core.decoder import InstructionDecoder
    dec = InstructionDecoder(spec_like)
    bad = []
    if dec.payload_width != 7:
        bad.append(f"{u}: decoder payload={dec.payload_width}≠7")
    vld_off = dec._layout["VLD"]["offset"]
    if vld_off != 47:
        bad.append(f"{u}: header VLD offset={vld_off}≠47")
    return bad


def g13_sim_decoder_frame_width(_root: Path) -> list:
    """⑬ 模拟器侧帧宽与发射侧同源（发射 modules.yaml code_bits / 仿真 specs 字段和）。

    失效标本（2026-09-12 实证）：发射侧 code_bits 修 6→7 后，sim 侧
    specs/units/vin*.yaml 微指令和仍为 6 → 解码器 header VLD 读 bit46 恒 0 →
    IQUnit.exec_active=0 → kernel controls=0 → RegPipeUnit vld_eff=0 →
    mul e2e 物理仿真全链路死（日志 `Unit:VIN0` 条数为 0）。⑫只验发射产物，
    ⑬补验仿真解码侧——两侧帧宽必须同源（vh:16 CODE[6] reserved → payload 7bit）。"""
    from aiacc_hardware.src.spec_loader import SpecLoader
    bad = []
    for u in VIN_UNITS:
        bad += _decoder_layout_bad(SpecLoader().get_unit(u), u)
    return bad


def g14_flat_stream_protocol(build_dir: Path, spec) -> list:
    """⑭ ADR-054 平坦流协议：对目录内全部 iq_*.hex 逐行解码（code_bits 一律从
    spec.modules 读，禁手抄），要求
      (a) 所有行 EMBED==0。硬件死区实为 **embed∈{1..6}**（smi_iq_iic.v:441
          `EN_EMBED && ~(&w_embed)` 才进 :468 LOOP_PARA_SET 多元素 kernel
          array-replay 分支，2026-09-12 VCS 实证 bank2 仅写 3 行）；**embed==7
          是安全哨兵**——golden append_nop_wait（mul_scalar_op.cpp:803-819）原生发
          embed=7 wait 行（hco 2026-09-12 对 golden iq_68_vin0.hex 逐字节复核）。
          本条对我方产物**有意加严为全流 embed==0**（编译器永不发 wait 行，
          垫拍由 vld=0 NOP 承担）——判据比硬件死区严是协议而非翻译。
      (b) 末行 = jump 终止行（JUMP=1 且 code==0），且仅末行 jump。
      (c) op 行（vld=1 且 jump=0）code≠0——防未来全零 code 假绿（hco 建议②）。
    返回违例列表 [(file, line_idx, 原因)]。"""
    bad = []
    for p in sorted(build_dir.glob("iq_*.hex")):
        stem = p.stem[3:]
        if stem.startswith("ur-r"):
            mod = "UR_READ"
        elif stem.startswith("ur-w"):
            mod = "UR_WRITE"
        elif stem.startswith("rtovr"):
            mod = "RTOVR"
        else:
            mod = stem.upper()
        cb = int(spec.modules[mod].code_bits)
        lines = [l.strip() for l in p.read_text().splitlines() if l.strip()]
        if not lines:
            bad.append((p.name, -1, "empty")); continue
        for i, ln in enumerate(lines):
            v = int(ln, 16)
            h = v >> cb
            embed = (h >> 36) & 0x7
            jump = (h >> 39) & 0x1
            vld = (h >> 40) & 0x1
            code = v & ((1 << cb) - 1)
            if embed != 0:
                bad.append((p.name, i, f"embed={embed}≠0"))
            if vld == 1 and jump == 0 and code == 0:
                bad.append((p.name, i, "op 行(vld=1,jump=0) code=0 假绿哨兵"))
            if i < len(lines) - 1 and jump:
                bad.append((p.name, i, "非末行 jump"))
            if i == len(lines) - 1 and (jump != 1 or code != 0):
                bad.append((p.name, i, f"末行非终止行(jump={jump},code=0x{code:x})"))
    return bad


def g14b_unroll_stream_audit(build_root: Path, spec, expect_top_op: str,
                             num_iterations: int) -> list:
    """⑭-b（ADR-055 M4 / D4(a) 强制跑点）：展开形（unroll）产物审计。g14 只验 hex 流
    形（embed/TERM/哨兵），本函数从**三个独立产物**交叉裁决白名单算子的发射契约：

      ① `branch_report.json`：top_op 匹配且**每个队列分支 == "unroll"**——
         「应展开却静默回落 embed」的唯一证物（sim 同构建模下 e2e 全绿证明不了分支，
         hco M4 假绿口）；
      ② `physical_instructions.json`：无 HDR、恰 1 条末尾 TERM(JUMP=1)、INIT 居首(vld=0)、
         全行 level==0 / EMBED_END==0 / INC_EMBED==0 / INC==0、域合法
         （LOOPS∈[0,1023]，DLY∈[0,7]；⚠ 读 `EMBED_LOOPS` 键名——`packer.py` 无 `EMBED`
         位，读它会恒真假绿，E7）；`nop_*` 气泡行必须 vld==0、非气泡 BODY 必须 vld==1；
      ③ `compiler_schedule.json`（solver 侧独立来源）：逐队列重建
         R_q = (⌊t_max/II⌋ + num_iterations) − ⌊t_min/II⌋，L_q = II·R_q + 2，
         与 **hex 实际行数** 逐队列三方比对（formula 为契约同源，但数据流独立，
         抓发射层 off-by-one / 行数漂移）。

    build_root = `build/verification_v2/<op>/`（hex 在其下 `smc_0/`）。
    返回违例字符串列表（空 = 全绿）。"""
    import json as _json
    bad: list = []
    # ---- ① branch_report ----
    br_path = build_root / "branch_report.json"
    if not br_path.exists():
        return [f"缺 branch_report.json（M4 证物缺失）: {br_path}"]
    br = _json.loads(br_path.read_text())
    if br.get("top_op") != expect_top_op:
        bad.append(f"branch_report top_op={br.get('top_op')!r} != 期望 {expect_top_op!r}")
    branches = br.get("branches", {})
    for q, b in sorted(branches.items()):
        if b != "unroll":
            bad.append(f"队列 {q} 分支={b} != unroll（静默回落或分支流窜）")
    # ---- ② physical_instructions.json ----
    pi_path = build_root / "physical_instructions.json"
    if not pi_path.exists():
        return bad + [f"缺 physical_instructions.json: {pi_path}"]
    rows = _json.loads(pi_path.read_text())
    per_q: dict = {}
    for r in rows:
        per_q.setdefault(r["iq_name"], []).append(r)
    for q, rs in sorted(per_q.items()):
        rs = sorted(rs, key=lambda r: r["abs_t"])
        if branches.get(q) != "unroll":
            continue   # 非展开队列不受本契约约束（A1 范围内应为零，但已被 ① 罚）
        if rs[0]["stage"] != "INIT" or rs[0]["header"]["VLD"] != 0:
            bad.append(f"{q}: 首行非 INIT/vld=0（{rs[0]['stage']}, vld={rs[0]['header']['VLD']}）")
        terms = [r for r in rs if r["stage"] == "TERM"]
        if len(terms) != 1 or rs[-1]["stage"] != "TERM" or rs[-1]["header"].get("JUMP") != 1:
            bad.append(f"{q}: TERM 数量={len(terms)} 或未居末带 jump=1")
        if any(r["stage"] == "HDR" for r in rs):
            bad.append(f"{q}: 仍含 HDR 行（embed 主线残留）")
        for r in rs:
            h = r["header"]
            if h.get("EMBED_LOOPS", 0) != 0:
                bad.append(f"{q}: 行 EMBED_LOOPS={h.get('EMBED_LOOPS')}≠0（{r['trace_id']}）")
            if h.get("EMBED_END", 0) != 0:
                bad.append(f"{q}: 行 EMBED_END={h.get('EMBED_END')}≠0（{r['trace_id']}——"
                           f"照抄主线 :366 即此错）")
            if h.get("INC_EMBED", 0) != 0:
                bad.append(f"{q}: 行 INC_EMBED={h.get('INC_EMBED')}≠0（{r['trace_id']}——"
                           f"照抄主线 op_mask 逻辑即此错）")
            if r["stage"] in ("BODY", "TERM") and h.get("INC", 0) != 0:
                bad.append(f"{q}: 行 INC={h.get('INC')}≠0（展开形地址须烘焙，{r['trace_id']}）")
            if not (0 <= h.get("LOOPS", 0) <= 1023):
                bad.append(f"{q}: LOOPS={h.get('LOOPS')} 越 [0,1023]（{r['trace_id']}）")
            if not (0 <= h.get("DLY", 0) <= 7):
                bad.append(f"{q}: DLY={h.get('DLY')} 越 [0,7]（{r['trace_id']}）")
            if r["stage"] == "BODY":
                if r["trace_id"].startswith("nop_") and h["VLD"] != 0:
                    bad.append(f"{q}: 气泡行 {r['trace_id']} 被置 vld=1")
                if not r["trace_id"].startswith("nop_") and h["VLD"] != 1:
                    bad.append(f"{q}: 有效行 {r['trace_id']} 被置 vld=0")
    # ---- ③ 三方行数互校 ----
    sch_path = build_root / "compiler_schedule.json"
    if not sch_path.exists():
        return bad + [f"缺 compiler_schedule.json: {sch_path}"]
    sch = _json.loads(sch_path.read_text())
    II = int(sch["II"])
    t_by_q: dict = {}
    for tid, info in sch["ops"].items():
        t_by_q.setdefault(str(info["iq"]).lower(), []).append(int(info["t"]))
    hex_dir = build_root / "smc_0"
    q_hex = {p.stem[3:]: len([l for l in p.read_text().splitlines() if l.strip()])
             for p in sorted(hex_dir.glob("iq_*.hex"))}
    for q in sorted(set(branches) | set(per_q) | set(t_by_q) | set(q_hex)):
        ts = t_by_q.get(q)
        if not ts:
            bad.append(f"{q}: schedule 无对应桶（名称映射断裂）"); continue
        rq = (max(ts) // II + num_iterations) - (min(ts) // II)
        l_q = II * rq + 2
        n_json = len(per_q.get(q, []))
        n_hex = q_hex.get(q, -1)
        if n_json != l_q or n_hex != l_q:
            bad.append(f"{q}: L_q 三方不等（预期 {l_q} = {II}·{rq}+2 / "
                       f"json {n_json} / hex {n_hex}）——发射层行数漂移")
    return bad


def g10_tsv_widths(path: Path):
    """⑩ 每个数据行的字段数必须 == 表头字段数。返回 (行号, 行名, NF) 列表。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    header_nf = len(lines[0].split("\t"))
    bad = []
    for idx, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        if len(cells) != header_nf:
            bad.append((idx, cells[1] if len(cells) > 1 else "?", len(cells)))
    return bad


# ---------------------------------------------------------------- 绿态守卫

class TestGuardsGreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = HardwareSpec.load("hw_config")
        cls.allowed = cls.spec.get_rtovr_connectivity()

    def test_g1_allowed_table_matches_rtl(self):
        self.assertEqual(g1_table_vs_rtl(self.spec), [],
                         "①邻接表与 RtoVR.v 掩码漂移")

    def test_g2_four_way_sync(self):
        self.assertEqual(g2_four_way_sync(_ROOT), [], "②四向不同步")

    def test_g5_bits_and_delays(self):
        self.assertEqual(g5_bits_and_delays(_ROOT), [], "⑤位宽/延迟回退")

    def test_g13_sim_decoder_frame_width(self):
        self.assertEqual(g13_sim_decoder_frame_width(_ROOT), [],
                         "⑬模拟器 decoder 帧宽滞后发射侧（specs 字段和≠7 → 当日 mul e2e 全零病）")

    def test_g4_sim_mask_semantics(self):
        """④ 模拟器屏蔽 = RTL 语义：非法 (port,src) 数据置 0 且 vld 仍传播。"""
        from smc_simulator.datapath.rto_vr import RtoVRUnit
        r = RtoVRUnit("G4", num_outputs=64, allowed=self.allowed)
        # 非法：slot3(FPMUL_S0) ← src30(UR_RDATA_0)——旧 dense 世界直连
        r.evaluate({"sources": {30: 0xDEADBEEF},
                    "iq_updates": [(3, (30 << 1) | 1)], "cycle": 0})
        r.tick()
        self.assertEqual(r.get_output(3), 0, "非法 select 必须出 0（RTL mux_result=0）")
        self.assertTrue(r.is_valid(3), "vld 必须仍传播（RTL 输出寄存器锁存 0）")
        # 合法：slot3 ← src11(VIN0_D) 不受影响
        r.evaluate({"sources": {11: 0xCAFEBABE},
                    "iq_updates": [(3, (11 << 1) | 1)], "cycle": 1})
        r.tick()
        self.assertEqual(r.get_output(3), 0xCAFEBABE)
        # fail-closed：无表全屏蔽
        r2 = RtoVRUnit("G4B", num_outputs=64)
        r2.evaluate({"sources": {11: 0x1234},
                     "iq_updates": [(3, (11 << 1) | 1)], "cycle": 2})
        r2.tick()
        self.assertEqual(r2.get_output(3), 0)
        self.assertTrue(r2.is_valid(3))

    def test_g7_vout0_capacity_negative(self):
        """⑦ 两条 compute_D→out16 同拍必须被容量公理拆开：II 不得为 1。"""
        def build(n_vout):
            ops = [make_op("FPMUL", "f1", iq_type=RT.FPMUL),
                   make_op("FPADD", "f2", iq_type=RT.FPADD),
                   make_op("VOUT0", "vA", deps=["f1"], iq_type=RT.VOUT0)]
            if n_vout == 2:
                ops.append(make_op("VOUT0", "vB", deps=["f2"], iq_type=RT.VOUT0))
            return ops
        r1 = UCodeSynthesizer(self.spec).solve_with_units(build(1), MII=1, MAX_II=8)
        self.assertTrue(r1 and r1["II"] == 1, "控制组（单 VOUT0）应 II=1 SAT")
        r2 = UCodeSynthesizer(self.spec).solve_with_units(build(2), MII=1, MAX_II=8)
        self.assertTrue(r2, "双 VOUT0 应在 II>=2 SAT（被拆开而非拒绝编译）")
        self.assertGreaterEqual(r2["II"], 2,
                                "VOUT0 单实例容量：同拍两条 out16 必须被拆开（II≥2）")

    def test_g8_payload_smc_id_en(self):
        """⑧ B3 排序锁定：显式 metadata 先于缺省分支；errata 后 pass 发 N-1。"""
        N = self.spec.params.PARAM_SMC_NUM

        def probe(meta):
            op = MIROp(op_type="VIN0", logic_cycle=0, trace_id="g8", metadata=meta)
            return UnitPayloadFactory.build(op, {}, {}, self.spec)

        self.assertEqual(probe({"iq_type": RT.VIN0, "smc_id_en": N - 1})["SMC_ID_EN"], N - 1,
                         "D4 pass 显式写 N-1 → 必须发 N-1（errata 后 golden 同值）")
        self.assertEqual(probe({"iq_type": RT.VIN0, "smc_id_en": 31})["SMC_ID_EN"], 31,
                         "显式 31 也必须透传——证明 meta_upper 先于缺省分支（B3 排序）")
        self.assertEqual(probe({"iq_type": RT.VIN0})["SMC_ID_EN"], N - 1,
                         "无 metadata 时缺省分支发 N-1（文档化：pass 保证不走到这）")

    def test_g9_dual_world_invariant(self):
        """⑨ golden select ⊆ sparse（必⊆dense 全 1）——锁「只发两世界交集合法码」。"""
        pairs = vtool.decode_rtovr_selects(GOLDEN_24_DIR)
        self.assertGreater(len(pairs), 0, f"golden 目录无 select: {GOLDEN_24_DIR}")
        bad = [(s, sel) for s, sel in pairs if sel not in self.allowed.get(s, frozenset())]
        self.assertEqual(bad, [],
                         f"golden {len(pairs)} 条 select 中 {len(bad)} 条在 sparse 下非法: {bad}")
        # dense 侧恒真（全 1 掩码），不变量即 sparse⊆dense 且 golden⊆sparse。

    def test_g10_tsv_row_width_sentinel(self):
        """⑩ tsv 行宽哨兵：当前已知漂移 28 行（DEBT-053-TRANSCRIPTION，hco B1）。
        修复 tsv 后本测转红——届时把哨兵值改为 0；漂移行数变化（好转/恶化）同样转红。"""
        bad = g10_tsv_widths(TSV_PATH)
        self.assertEqual(len(bad), 28,
                         f"tsv 漂移行数从已知 28 变为 {len(bad)}（{bad[:3]}...）——"
                         "若已修复请把哨兵改 0 并闭合 DEBT-053-TRANSCRIPTION")

    def test_g11_no_direct_ur_edge_survives_pass(self):
        """⑪ pass 后 UR_READ 直连边不得残留于计算槽：所有 UR_READ→RTOVR→consumer 边
        的 consumer 槽必须 ∈ {14,15,34}（掩码中唯一接纳 30-33 的三个槽）。
        失效标本（2026-09-11）：hardsigmoid 常量注入的 RTOVR（cinl_*/inl_*）带陈旧
        显式 sel=4/10（沿用原计算边 select），∃ 合法假阳跳过绕行，发射期 solver 改派
        真实读口 → (6,31)/(6,33)/(12,30)/(12,33) 四条被掩码置 0，hw-parity 全 0。"""
        from verification_v2.ops.configurable import ConfigurableOperator
        from frontend.rtovr_bypass import apply_rtovr_sparse_bypass
        op = ConfigurableOperator(str(_ROOT / "verification_v2/ops/hardsigmoid.yaml"))
        out = apply_rtovr_sparse_bypass(op.get_dfg(spec=self.spec), self.spec)
        by_id = {o.trace_id: o for o in out}
        bad = []
        for o in out:
            if o.op_type.upper() != "RTOVR":
                continue
            prods = o.metadata.get("depends_on", [])
            if not prods or prods[0] not in by_id:
                continue
            if "UR_READ" in by_id[prods[0]].op_type.upper():
                slot = o.metadata.get("slot_id")
                slot = int(getattr(slot, "value", slot))
                if slot not in (14, 15, 34):
                    bad.append((o.trace_id, slot))
        self.assertEqual(bad, [], f"UR 直连边残留于计算槽（应全部绕行 VIN）: {bad}")


# ---------------------------------------------------------------- ③ e2e 冒烟 + 合法性（重，独立类）

class TestG3EmittedSelectsLegal(unittest.TestCase):
    """③ 编译器 emit 的全部 (out,sel) 满足邻接表（add/mul；472→0 回归）。"""

    def _compile_and_audit(self, op: str, n_rows: int = 256):
        res = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", op, "--mode", "e2e",
             "--n-rows", str(n_rows)],
            cwd=_ROOT, capture_output=True, text=True, timeout=900)
        self.assertEqual(res.returncode, 0,
                         f"{op} e2e 必须全绿（绕行后合法路径应恢复 sim PASS）:\n"
                         f"{res.stdout[-2000:]}\n{res.stderr[-1000:]}")
        build_dir = _ROOT / "build" / "verification_v2" / op / "smc_0"
        spec = HardwareSpec.load("hw_config")
        allowed = spec.get_rtovr_connectivity()
        pairs = vtool.decode_rtovr_selects(build_dir)
        masked = vtool.audit_legality(build_dir, allowed)
        self.assertGreater(len(pairs), 0, f"{op} 无 emit select")
        self.assertEqual(masked, [],
                         f"③{op}: {len(masked)}/{len(pairs)} 条 select 被屏蔽（472 回归）: {masked}")

    def test_g3_mul(self):
        self._compile_and_audit("mul")

    def test_g3_add(self):
        # ADR-055 D3：默认 256 行时 add 展开形实算行数超 IQ 容量墙（ur-r3 L_q=134>128）
        # → 显式拒绝是**预期契约**（不再走 embed 兜底）。③的合法性信号是尺度无关的，
        # 取 A1 验收尺度内最大者（n_rows=60；实测 rtovr-14 L_q=38 ≤ 128）。
        self._compile_and_audit("add", n_rows=60)

    def test_g3_add_overcapacity_refuses_loudly(self):
        """ADR-055 D3 e2e 级契约：超容量 add 必须**响亮失败**（非零退出码 + DEBT-055-CAPACITY），
        绝不静默回落 embed（embed 产物 sim 仍可绿，回落即是假绿）。"""
        res = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", "add",
             "--mode", "e2e", "--n-rows", "256"],
            cwd=_ROOT, capture_output=True, text=True, timeout=900)
        self.assertNotEqual(res.returncode, 0,
                            f"256 行 add 必须显式拒绝，不得静默回落 embed:\n{res.stdout[-800:]}")
        self.assertIn("DEBT-055-CAPACITY", res.stdout + res.stderr,
                      f"失败原因必须是容量拒绝（而非其他回归）:\n{res.stdout[-800:]}")

    def test_g12_vin_frame_vld(self):
        """⑫ e2e 后 mul 产物的 VIN 帧：body vld=1 行 header VLD 须落 bit47。"""
        res = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", "mul", "--mode", "e2e"],
            cwd=_ROOT, capture_output=True, text=True, timeout=900)
        self.assertEqual(res.returncode, 0, f"mul e2e 必须全绿:\n{res.stdout[-1500:]}")
        build_dir = _ROOT / "build" / "verification_v2" / "mul" / "smc_0"
        self.assertEqual(g12_vin_frame_vld(build_dir), [],
                         "⑫ VIN 帧 header vld 未落 bit47（code_bits 6/7 混淆回归）")

    def test_g14_flat_stream_protocol(self):
        """⑭ e2e 后 mul 全队列流：level-0 平坦协议（无 embed 行，末行 jump 终止）。"""
        res = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", "mul", "--mode", "e2e"],
            cwd=_ROOT, capture_output=True, text=True, timeout=900)
        self.assertEqual(res.returncode, 0, f"mul e2e 必须全绿:\n{res.stdout[-1500:]}")
        build_dir = _ROOT / "build" / "verification_v2" / "mul" / "smc_0"
        spec = HardwareSpec.load("hw_config")
        self.assertEqual(g14_flat_stream_protocol(build_dir, spec),
                         [], "⑭ mul 流仍含 embed-loop 或缺 jump 终止行（ADR-054 回归）")

    def test_g14_add_unroll_stream_protocol(self):
        """⑭-on-add（ADR-055 M4 强制跑点）：e2e 后 add 全队列走展开形且满足发射契约。

        不替换 mul 的 test_g14；add 是本轮唯一新增待验证算子。三项裁决：
        hex 流形（复用 g14）、分支分类自证（branch_report 全 unroll）、
        三方行数互校（solver schedule 独立重建 L_q vs json vs hex）。
        num_iterations 固定 = n_rows 8 / fp32 vec4 = 2（--n-rows 8 明示）。"""
        res = subprocess.run(
            [sys.executable, "verification_v2/main_verify.py", "--op", "add",
             "--mode", "e2e", "--n-rows", "8"],
            cwd=_ROOT, capture_output=True, text=True, timeout=900)
        self.assertEqual(res.returncode, 0, f"add e2e 必须全绿:\n{res.stdout[-1500:]}")
        add_root = _ROOT / "build" / "verification_v2" / "add"
        spec = HardwareSpec.load("hw_config")
        self.assertEqual(g14_flat_stream_protocol(add_root / "smc_0", spec), [],
                         "⑭ add 展开流仍含 embed 行 / 非末行 jump / 缺终止行")
        self.assertEqual(
            g14b_unroll_stream_audit(add_root, spec, expect_top_op="add",
                                     num_iterations=2),
            [], "⑭-b add 展开契约（分支分类/字段域/气泡 vld/三方行数）违例——"
                "疑似 M4 静默回落 embed 或发射层漂移")


# ---------------------------------------------------------------- ⑥ 变异测试

class TestMutation(unittest.TestCase):
    """⑥ 对每条守卫注入病理，断言确实变红（永绿断言零信号防护）。"""

    @classmethod
    def setUpClass(cls):
        cls.spec = HardwareSpec.load("hw_config")
        cls.allowed = cls.spec.get_rtovr_connectivity()

    def test_m1_table_corruption_detected(self):
        rtl = {k: frozenset(v) for k, v in gen_allowed.derive_allowed_from_rtl().items()}
        class FakeSpec:
            def get_rtovr_connectivity(self_):
                bad = dict(rtl)
                bad[3] = frozenset(bad[3]) | {30}   # 放回一个 UR 源
                return bad
        self.assertTrue(g1_table_vs_rtl(FakeSpec()),
                        "①对放回 sel=30 的病理表必须变红")

    def test_m2_sync_corruption_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            import shutil
            shutil.copytree(_ROOT / "hw_config", root / "hw_config")
            p = root / "hw_config/specs/units/vin0.yaml"
            doc = yaml.safe_load(p.read_text())
            doc["latency"] = 2
            p.write_text(yaml.safe_dump(doc, allow_unicode=True))
            self.assertTrue(any("VIN0" in b for b in g2_four_way_sync(root)),
                            "②对 units/vin0.yaml latency 病理必须变红")

    def test_m4_dense_would_pass_illegal(self):
        """④的变异证明：dense（全 1）下同一条非法路由会放行——屏蔽行为是过滤器造成的。"""
        from smc_simulator.datapath.rto_vr import RtoVRUnit
        dense = {i: frozenset(range(64)) for i in range(40)}
        r = RtoVRUnit("M4", num_outputs=64, allowed=dense)
        r.evaluate({"sources": {30: 0xABCD},
                    "iq_updates": [(3, (30 << 1) | 1)], "cycle": 0})
        r.tick()
        self.assertEqual(r.get_output(3), 0xABCD,
                         "dense 下 (3,30) 放行——与 sparse 屏蔽形成差分，证明过滤器生效")
        r2 = RtoVRUnit("M4B", num_outputs=64, allowed=self.allowed)
        r2.evaluate({"sources": {30: 0xABCD},
                     "iq_updates": [(3, (30 << 1) | 1)], "cycle": 0})
        r2.tick()
        self.assertEqual(r2.get_output(3), 0)

    def test_m12_frame_shift_detected(self):
        """⑫ 变异：按 code_bits=6 左偏 1 位的帧必须被判违规（2026-09-12 历史缺陷再现）。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            good = (1 << 47) | 0xF          # 正位帧：header vld bit47=1, body=0xF
            shifted = good >> 1             # code_bits=6 病理：全体右移 1 位
            (d / "iq_vin0.hex").write_text(f"{good:x}\n{shifted:x}\n")        # 第 2 行注入病理
            (d / "iq_vin1.hex").write_text(f"{good:x}\n")                     # 对照：全部正位
            (d / "iq_vout0.hex").write_text(f"{good:x}\n{good:x}\n")
            bad = g12_vin_frame_vld(d)
            self.assertEqual(len(bad), 1, f"左偏帧仅 1 行应违规: {bad}")
            self.assertEqual((bad[0][0], bad[0][1], bad[0][2]), ("iq_vin0.hex", 1, "bit47=0"))

    def test_m13_sim_decoder_width_lag_detected(self):
        """⑬ 变异：sim spec 字段和=6（缺 RESERVED，2026-09-12 实证病理）必须双判据判违。"""
        from aiacc_hardware.src.spec_loader import MicroInstructionField, UnitSpec
        bad_spec = UnitSpec(
            unit_name="VIN0", latency=1, throughput=1, desc="",
            ports={"inputs": [], "outputs": []},
            micro_instructions=[
                MicroInstructionField(name="VALID", width=1),
                MicroInstructionField(name="smc_id_en", width=5),
            ])
        bad = _decoder_layout_bad(bad_spec, "VIN0")
        self.assertEqual(len(bad), 2,
                         f"6-bit 病理应同时被 payload 与 VLD offset 两判据抓住: {bad}")
        # 对照组：真实 spec 文件必须干净（防止变异本身传染绿态判定）
        self.assertEqual(g13_sim_decoder_frame_width(_ROOT), [])

    def test_m14_embed_row_detected(self):
        """⑭ 变异：含 embed=1 行 / 缺失 jump 终止行的病理流必须被判违例。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            cb_vin = int(self.spec.modules["VIN0"].code_bits)   # 7
            good_op = ((1 << 40) << cb_vin) | 0xF               # header vld=1, body=0xF
            jump_term = (1 << (40 + cb_vin)) | (1 << (39 + cb_vin))  # vld=1 且 jump=1 的终止行
            # 病理 1：行间夹一条 embed=1（level-1 loop 复发）
            embed_row = good_op | (1 << (36 + cb_vin))
            (d / "iq_vin0.hex").write_text(f"{good_op:x}\n{embed_row:x}\n{jump_term:x}\n")
            bad = g14_flat_stream_protocol(d, self.spec)
            self.assertTrue(any(r[2].startswith("embed=") for r in bad),
                            f"embed=1 行必须被抓: {bad}")
            self.assertTrue(any(r[2].startswith("非末行") for r in bad) is False)
            # 病理 2：缺 jump 终止行
            (d / "iq_vin0.hex").write_text(f"{good_op:x}\n")
            bad2 = g14_flat_stream_protocol(d, self.spec)
            self.assertTrue(any("末行非终止行" in r[2] for r in bad2),
                            f"缺终止行必须被抓: {bad2}")
            # 病理 3（hco 建议②）：op 行 vld=1/jump=0 但 code=0 必被判「假绿哨兵」
            zero_op = (1 << 40) << cb_vin                       # header vld=1，body=0
            (d / "iq_vin0.hex").write_text(f"{zero_op:x}\n{jump_term:x}\n")
            bad3 = g14_flat_stream_protocol(d, self.spec)
            self.assertTrue(any("假绿哨兵" in r[2] for r in bad3),
                            f"全零 code op 行必须被抓: {bad3}")

    def test_m15_flat_inc_range_guard(self):
        """hco 2026-09-12 建议①：RTL w_inc_comp（smi_iq_iic.v:245-246）为 4-bit
        二进制补码（合法 -8..+7）；平坦 op 行 inc=iq_max_stride 超 7 时必须显式
        error 拒绝发射，绝不可静默发 RTL 无法表达的 INC（新分支放大既有盲区）。
        注：iq_max_stride 由 max 聚合恒 ≥0（assembler.py），负数方向目前不可达。"""
        from driver.assembler import PhysicalAssembler

        class _FakeOp:
            op_type = "UR_READ"
            trace_id = "t_read0"

            def __init__(self, stride):
                self.metadata = {"unit_id": "ur-r0", "global_t": 0,
                                 "stride": stride, "logical_addr": 0}

        with self.assertRaises(ValueError):
            PhysicalAssembler(self.spec).assemble(
                [_FakeOp(8)], II=1, num_iterations=2)

    def test_m16_slot_collision_rejected(self):
        """⑭-b/D1 变异：同 IQ 同槽（global_t % II 相同）两 op 必须显式拒绝——
        旧字典推导 kernel_map 会静默覆盖丢一个（ADR-055 D1）。
        ADR-056 迁移：准入凭据由 admission 物化对象承载（取代 top_op 名称白名单）；
        槽冲突在 kernel_map 构造期触发，先于任何 G-* 队列闸门，与区域内容无关。"""
        from driver.assembler import PhysicalAssembler, UnrollAdmission

        class _FakeOp:
            op_type = "UR_READ"
            trace_id = "tA"

            def __init__(self, tid, t):
                self.trace_id = tid
                self.metadata = {"unit_id": "ur-r0", "global_t": t,
                                 "stride": 1, "logical_addr": 0}

        admission = UnrollAdmission(op_name="add", tensor_regions={"x": (0, 0, 256)})
        # II=2 下 t=0 与 t=2 同落槽 0
        with self.assertRaisesRegex(ValueError, "槽冲突"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeOp("tA", 0), _FakeOp("tB", 2)], II=2, num_iterations=1,
                admission=admission)

    def test_m17_bubble_vld_misplaced_detected(self):
        """D4(c) 变异：气泡行被误置 vld=1 时 g14b 必须变红（json 层气泡/有效行互斥断言）。"""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "smc_0").mkdir(parents=True)
            h0 = {"VLD": 0, "INC": 0, "DLY": 0, "COND": 0, "LOOPS": 1,
                  "EMBED_END": 0, "EMBED_LOOPS": 0, "JUMP": 0, "INC_EMBED": 0}
            h_op = {**h0, "VLD": 1, "LOOPS": 0}
            h_bub_bad = {**h_op}
            h_term = {**h_op, "JUMP": 1}
            rows = [
                {"iq_name": "ur-r0", "abs_t": 0, "stage": "INIT", "header": h0,
                 "payload": {}, "trace_id": "init_ur-r0"},
                {"iq_name": "ur-r0", "abs_t": 1, "stage": "BODY", "header": h_bub_bad,
                 "payload": {}, "trace_id": "nop_ur-r0_0_0"},   # 病理：气泡 vld=1
                {"iq_name": "ur-r0", "abs_t": 2, "stage": "BODY", "header": h_op,
                 "payload": {}, "trace_id": "x_read_1"},
                {"iq_name": "ur-r0", "abs_t": 3, "stage": "TERM", "header": h_term,
                 "payload": {}, "trace_id": "term_ur-r0"},
            ]
            (root / "physical_instructions.json").write_text(_json.dumps(rows))
            (root / "branch_report.json").write_text(_json.dumps(
                {"top_op": "add", "branches": {"ur-r0": "unroll"}}))
            (root / "compiler_schedule.json").write_text(_json.dumps(
                {"II": 2, "ops": {"x_read_1": {"t": 0, "iq": "ur-r0"}}}))
            (root / "smc_0" / "iq_ur-r0.hex").write_text("0\n0\n0\n0\n")
            bad = g14b_unroll_stream_audit(root, self.spec, expect_top_op="add",
                                           num_iterations=1)
            self.assertTrue(any("气泡行" in b for b in bad),
                            f"气泡 vld=1 病理必须被抓: {bad}")

    def test_m18_capacity_rejected(self):
        """D3 变异：展开行数 > 128 必须显式拒绝（DEBT-055-CAPACITY），禁回落 embed。
        ADR-056 迁移：admission 物化对象取代 top_op；stride=0 → 逐行 G-addr
        未取模地址恒 0 ∈ 声明区 [0,256)，行级闸门全过后容量硬拒（:608 于烘焙环之后）。"""
        from driver.assembler import PhysicalAssembler, UnrollAdmission

        class _FakeOp:
            op_type = "UR_READ"

            def __init__(self):
                self.trace_id = "t_res"
                self.metadata = {"unit_id": "ur-r0", "global_t": 0,
                                 "stride": 0, "logical_addr": 0}

        admission = UnrollAdmission(op_name="add", tensor_regions={"x": (0, 0, 256)})
        with self.assertRaisesRegex(ValueError, "DEBT-055-CAPACITY"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeOp()], II=2, num_iterations=200, admission=admission)

    def test_m19_stride_guard_rejected(self):
        """D2 前置守卫变异：同 IQ 步进型 stride 不一致（1 vs 2）必须拒绝。
        ADR-056 迁移：admission 物化对象取代 top_op；步长闸在烘焙环之前触发。"""
        from driver.assembler import PhysicalAssembler, UnrollAdmission

        class _FakeOp:
            op_type = "UR_READ"

            def __init__(self, tid, t, stride):
                self.trace_id = tid
                self.metadata = {"unit_id": "ur-r0", "global_t": t,
                                 "stride": stride, "logical_addr": 0}

        admission = UnrollAdmission(op_name="add", tensor_regions={"x": (0, 0, 256)})
        with self.assertRaisesRegex(ValueError, "步长不一致"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeOp("tA", 0, 1), _FakeOp("tB", 1, 2)],
                II=2, num_iterations=2, admission=admission)

    def test_m20_wrap_guard_rejected(self):
        """D2 环绕保守规则变异：stride·R_q ≥ bank_depth 必须拒绝
        （含 ==：1·300 ≥ 256；UR_READ 歧义/UR_WRITE 硬错同拒）。
        ADR-056 迁移：admission 物化对象取代 top_op；环绕闸在烘焙环之前触发，
        先于任何行级 G-addr——本用例不因 length=256 声明区提前变红。"""
        from driver.assembler import PhysicalAssembler, UnrollAdmission

        class _FakeOp:
            op_type = "UR_READ"

            def __init__(self):
                self.trace_id = "t_stream"
                self.metadata = {"unit_id": "ur-r0", "global_t": 0,
                                 "stride": 1, "logical_addr": 0}

        admission = UnrollAdmission(op_name="add", tensor_regions={"x": (0, 0, 256)})
        with self.assertRaisesRegex(ValueError, "环绕"):
            PhysicalAssembler(self.spec).assemble(
                [_FakeOp()], II=2, num_iterations=300, admission=admission)

    def test_m5_bits_delays_corruption_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            import shutil
            shutil.copytree(_ROOT / "hw_config", root / "hw_config")
            p = root / "hw_config/pipeline_delays.yaml"
            doc = yaml.safe_load(p.read_text())
            dmap = doc.get("pipeline_delays", doc)
            dmap["VIN0"] = 0
            p.write_text(yaml.safe_dump(doc, allow_unicode=True))
            self.assertTrue(g5_bits_and_delays(root),
                            "⑤对 pipeline_delays.VIN0=0 回退必须变红")

    def test_m7_capacity_removed_mechanism_check(self):
        """⑦的机制归因：II-Analysis 的 res 下界确实由 VOUT0 inst=1 顶起。
        （若容量公理被删，2×VOUT0 会在 II=1 SAT——本断言锁定 II≥2 这一可观测后果。）"""
        ops = [make_op("FPMUL", "f1", iq_type=RT.FPMUL),
               make_op("FPADD", "f2", iq_type=RT.FPADD),
               make_op("VOUT0", "vA", deps=["f1"], iq_type=RT.VOUT0),
               make_op("VOUT0", "vB", deps=["f2"], iq_type=RT.VOUT0)]
        r = UCodeSynthesizer(self.spec).solve_with_units(ops, MII=1, MAX_II=8)
        self.assertIsNot(r["II"], 1,
                         "容量机制若在，双 VOUT0 永不得 II=1；若为 1 则公理已被删（变绿事故）")

    def test_m8_default_branch_order_matters(self):
        """⑧的病理形态：若有人把缺省分支改回 default=0 或乱序，无 metadata 即破。
        另：显式 0 也必须透传（证明不是「凡 smc_id_en 都改 N-1」）。"""
        op = MIROp(op_type="VIN0", logic_cycle=0, trace_id="m8",
                   metadata={"iq_type": RT.VIN0, "smc_id_en": 0})
        payload = UnitPayloadFactory.build(op, {}, {}, self.spec)
        self.assertEqual(payload["SMC_ID_EN"], 0,
                         "显式 0 不透传 = 工厂把显式路径吞了（B3 排序被破坏）")

    def test_m9_dense_membership_is_meaningless(self):
        """⑨的不变量锚：非法对 (3,30) 在 dense 下合法、在 sparse 下非法——
        锁住「dense 合法不得作为发码依据」。"""
        self.assertIn(30, frozenset(range(64)))                     # dense 恒含
        self.assertNotIn(30, self.allowed.get(3, frozenset()),      # sparse 必拒
                         "(3,30) 若变合法说明邻接表被 dense 化污染")

    def test_m10_tsv_checker_detects_drift(self):
        """⑩的变异：合成漂移文件必须被行宽检查检出；合成整齐文件必须通过。"""
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good.tsv"
            good.write_text("a\tb\tc\n1\tx\t1\n2\ty\t0\n")
            self.assertEqual(g10_tsv_widths(good), [])
            bad = Path(td) / "bad.tsv"
            bad.write_text("a\tb\tc\n1\tx\t1\n2\ty\n")  # 第二数据行少 1 列
            drift = g10_tsv_widths(bad)
            self.assertEqual(len(drift), 1)
            self.assertEqual(drift[0][0], 3, "应定位到第 3 行")


if __name__ == "__main__":
    unittest.main()
