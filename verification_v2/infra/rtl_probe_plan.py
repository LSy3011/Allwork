"""ADR-056 步④-2 TB 探针实例覆盖计划（编译器侧唯一来源，镜像 TB 铺针结构）。

端口侧 RTL 事实（出处）：
  * u280_sim_v422/smi/smi_top.v:21          SMI_IQ_CNT = 94（架构 IQ 槽总数）
  * smi_top.v:427-517                        显式 u_iq_0..27（信号见 smi_iq.v:165-170）
  * smi_top.v:521-541                        gen_iq[28..66].u_iq（RtoVR ×39）
  * smi_top.v:547-568                        gen_iq_bridge[68..70].u_iq（vin0/vin1/vout0）
  * smi_top.v:572-586                        gen_iq_stub：67、71..91 纯 assign（无 smi_iq）
  * smi_top.v:588-589                        显式 u_iq_92 / u_iq_93（LSU 族）
  * sim_multi_stage/tb_aiacc_top_v04_f_multi_stage.v 末段探针块（步④-2 additive）

实例覆盖恒等式（测试强制）：30 显式 + 39 gen_iq + 3 gen_iq_bridge = 72 铺针，
22 stub 显式排除；72 + 22 = 94 = SMI_IQ_CNT。注册表 GLOBAL_MONITOR_RULE 的全局
监测承诺 = 本模块 monitored_ids() 的集合。

队列名 → RTL IQ id：端口侧唯一权威来源是莎箱生成器
u280_sim_v422/generate_data_list_coverage.py（:95-104 动态模式、:172-201
IQ_NAME_TO_ID、:235-262 推断函数）；本模块是其编译器侧**只读镜像**，端口侧表
变动须同步鉴证、两侧同改。⚠ 与编译器逻辑 IQ id（hw_config/specs/system/
system_core.yaml iq_registry）不同域，**禁止混用**（前者钓 RTL 层级，后者
只服务于调度/发射）。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

# ---------- 三族铺针 + stub 排除集（与 TB 探针块逐字同构） ----------

EXPLICIT_IDS: List[int] = list(range(0, 28)) + [92, 93]
GEN_RTOVR_IDS: List[int] = list(range(28, 67))          # 28..66（RtoVR）
GEN_BRIDGE_IDS: List[int] = list(range(68, 71))         # 68..70（vin0/vin1/vout0）
STUB_IDS: List[int] = [67] + list(range(71, 92))        # 67、71..91 纯 assign

SMI_IQ_CNT = 94                                          # smi_top.v:21

TB_PROBE_PATH = ("u280_sim_v422/sim_multi_stage/"
                 "tb_aiacc_top_v04_f_multi_stage.v 末段探针块（ADR-056 步④-2）")


class ProbePlanError(ValueError):
    """队列名无法映射 / iq id 越界（映射断裂，拒绝静默通过）。"""


def monitored_ids() -> List[int]:
    """全部铺针 iq id（恒为 72 个，测试强制 72+22=94）。"""
    return sorted(EXPLICIT_IDS + GEN_RTOVR_IDS + GEN_BRIDGE_IDS)


def rtl_instance_path(iq_id: int) -> str:
    """iq id → TB 层级路径（与 TB 末段探针块 / smi_top.v 三族逐字同构）。"""
    if 0 <= iq_id <= 27 or iq_id in (92, 93):
        return f"dut.u_smi_top.u_iq_{iq_id}"
    if 28 <= iq_id <= 66:
        return f"dut.u_smi_top.gen_iq[{iq_id}].u_iq"
    if 68 <= iq_id <= 70:
        return f"dut.u_smi_top.gen_iq_bridge[{iq_id}].u_iq"
    if iq_id in STUB_IDS:
        raise ProbePlanError(f"iq{iq_id} 是 stub 槽（smi_top.v:572-586 纯 assign，无 smi_iq）")
    raise ProbePlanError(f"iq{iq_id} ∉ [0,94)——实例不存在")


# ---------- 队列名 → RTL IQ id（沙箱生成器表的只读镜像） ----------
# 出处：generate_data_list_coverage.py:172-201 IQ_NAME_TO_ID（静态）+
#       :95-104 动态文件名模式（ur-r<i>→16+i, ur-w<i>→20+i, rtovr-<idx>→28+idx）。

STATIC_NAME_TO_ID: Dict[str, int] = {
    "intadd": 0, "int8tofp16": 1, "fp16mul": 2, "fp16mul-s0123": 3,
    "fp16mul-s4567": 4, "fp32addtree5to1": 5, "fp32addtree8to1": 6,
    "fpmul": 7, "fpadd": 8, "fptofp": 9, "inttoint": 10, "fptoint": 11,
    "inttofp": 12, "logic": 13, "shiftup": 14, "shiftdn": 15,
    "stb": 24, "stb-axi": 25, "ldb": 26, "ldb-axi": 27,
    "ds": 92, "smi-ds-t": 93,
    "vin0": 68, "vin1": 69, "vout0": 70,
}

_DYN_RULES = (
    (re.compile(r"ur-r(\d+)$"), 16),        # iq_ur_r<i>  → 16+i
    (re.compile(r"ur-w(\d+)$"), 20),        # iq_ur_w<i>  → 20+i
    (re.compile(r"rtovr-(\d+)$"), 28),      # iq_rtovr_<idx> → 28+idx
)


def rtl_id_for_queue(name: str) -> int:
    """期望事件/调度侧的队列名 → 端口侧 RTL IQ id。不可映射即 ProbePlanError。"""
    n = str(name).strip().lower().replace("_", "-")
    if n in STATIC_NAME_TO_ID:
        return STATIC_NAME_TO_ID[n]
    for pat, base in _DYN_RULES:
        m = pat.fullmatch(n)
        if m:
            return base + int(m.group(1))
    raise ProbePlanError(f"队列名 {name!r} 无 RTL IQ id 映射（映射断裂，拒绝静默推断）")


def coverage_manifest() -> Dict:
    """实例覆盖清单（步④-2 出口件）：铺针 72 + stub 排除 22，逐实例带层级路径。"""
    probes = [{"iq": i, "path": rtl_instance_path(i)}
              for i in monitored_ids()]
    stubs = [{"iq": i, "reason": "smi_top.v:572-586 纯 assign stub（无 smi_iq，无取指源）"}
             for i in STUB_IDS]
    return {
        "schema": "adr056_step4_2_probe_coverage_v1",
        "provenance": {
            "rtl": ["smi_top.v:21 (SMI_IQ_CNT=94)", "smi_top.v:427-517 (u_iq_0..27)",
                    "smi_top.v:521-541 (gen_iq)", "smi_top.v:547-568 (gen_iq_bridge)",
                    "smi_top.v:572-586 (stub)", "smi_top.v:588-589 (u_iq_92/93)"],
            "queue_map": ("generate_data_list_coverage.py:95-104,172-201,235-262"
                          "（编译器侧镜像：verification_v2/infra/rtl_probe_plan.py）"),
            "tb_probes": TB_PROBE_PATH,
        },
        "monitor_rule": ("GLOBAL_MONITOR_RULE（protocol_registry_v1.yaml）："
                         "并集外队列出现取指脉冲即 RED；共同启动锚 iq_probe_cycle，"
                         "禁止逐队列独立平移后宣布一致"),
        "counts": {
            "smi_iq_cnt": SMI_IQ_CNT,
            "instrumented": len(probes),
            "stub_excluded": len(stubs),
            "identity": "30 explicit + 39 gen_iq + 3 gen_iq_bridge = 72; 72 + 22 = 94",
        },
        "instrumented_instances": probes,
        "excluded_stubs": stubs,
        "static_name_to_id": {k: v for k, v in sorted(STATIC_NAME_TO_ID.items())},
        "dynamic_rules": [
            {"pattern": "ur-r<i>", "formula": "16+i"},
            {"pattern": "ur-w<i>", "formula": "20+i"},
            {"pattern": "rtovr-<idx>", "formula": "28+idx"},
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="步④-2 实例覆盖清单发射（只读重放，无随机性）")
    ap.add_argument("--emit", type=Path, help="清单 JSON 落盘路径（缺省打印 stdout）")
    args = ap.parse_args()
    text = json.dumps(coverage_manifest(), ensure_ascii=False, indent=2)
    if args.emit:
        args.emit.parent.mkdir(parents=True, exist_ok=True)
        args.emit.write_text(text + "\n", encoding="utf-8")
        print(f"[EMIT] 实例覆盖清单: {args.emit}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
