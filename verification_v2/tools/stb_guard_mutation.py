#!/usr/bin/env python3
"""stb_guard_mutation —— ADR-052 守卫的**变异测试**：逐个注入真实失效模式，
   断言 `tests/unit/test_stb_schema_regression.py` 的对应测试确实变红。

为什么需要它：**通过的测试 ≠ 能失败的测试**。一条永远绿的断言没有信号价值，
却会在评审里伪装成"已覆盖"。本脚本把每条守卫对着它本该抓住的病理跑一遍 ——
若注入病理后守卫仍绿，则该守卫失效。

不改任何仓库文件：
    · spec 变异 → `mock.patch.object` 换掉类属性（深拷贝后改）
    · yaml 变异 → 临时目录里的 minimal yaml
    · hw_spec 变异 → **读真实源码**做字符串替换 + `exec`（验的是生产代码的判断逻辑本身）

退出码 0 = 全部守卫均被证明可红。

用法：
    python verification_v2/tools/stb_guard_mutation.py
"""
from __future__ import annotations

import contextlib
import copy
import importlib
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
for p in (ROOT, ROOT / "src", ROOT / "tests"):
    sys.path.insert(0, str(p))
os.environ["AIACC_NO_HW_SPEC_CACHE"] = "1"

TM = importlib.import_module("unit.test_stb_schema_regression")
from api.hw_spec import HardwareSpec  # noqa: E402

# ⚠ 手动调测试方法**不会**触发 setUpClass —— 不显式调则 cls.spec 未设，
#   所有测试都因 AttributeError 假红（首轮即踩）。
TM.TestStbSchemaRegression.setUpClass()

RESULTS: list[bool] = []


def run_one(method: str, _mod_patches: dict | None = None, **class_patches) -> bool:
    """在 patch 生效下跑单条测试方法，返回是否 FAILED（无 patch 时即基线）。

    ⚠ 用 patch.object 而非 patch.multiple：后者的 `spec` / `create` / `autospec` 是 **mock 自身
      的保留关键字**，传 `spec=<模型>` 会被它吞掉 → `ValueError: Must supply at least one
      keyword argument`。本脚本正要把类属性 `spec` 换掉，撞名必踩。
    """
    t = TM.TestStbSchemaRegression(method)
    with contextlib.ExitStack() as stack:
        for name, val in class_patches.items():
            stack.enter_context(mock.patch.object(TM.TestStbSchemaRegression, name, val))
        for name, val in (_mod_patches or {}).items():
            stack.enter_context(mock.patch.object(TM, name, val))
        try:
            getattr(t, method)()
            return False
        except Exception as e:
            print(f"         （失败原因：{type(e).__name__}: {str(e)[:110]}）")
            return True


def mutant_spec(mutate) -> object:
    """深拷贝真实 spec 后施加变异（不改磁盘、不改进程内原对象）。"""
    s = copy.deepcopy(TM.TestStbSchemaRegression.spec)
    mutate(s)
    return s


def check(label: str, method: str, expect_red: bool, **patches) -> bool:
    print(f"  · {label}")
    red = run_one(method, **patches)
    ok = (red == expect_red)
    RESULTS.append(ok)
    print(f"    {'✅' if ok else '❌'} {method} → {'RED（守卫有效）' if red else 'GREEN'}"
          f"  期望 {'RED' if expect_red else 'GREEN'}")
    return ok


def main() -> int:
    print("=" * 74)
    print("基线：未变异时应全绿")
    print("=" * 74)
    for m in ("test_iq_registry_no_duplicate_id_in_either_spec",
              "test_two_specs_agree_on_store_and_load_family",
              "test_stb_axi_not_swallowed_by_stb_prefix",
              "test_resolve_iq_type_store_and_load_family",
              "test_get_iq_name_both_paths_and_no_idx_suffix",
              "test_mirror_relation_stb_from_ldb",
              "test_mirror_relation_stbaxi_from_ldbaxi"):
        check(f"基线 {m}", m, expect_red=False)

    print("\n" + "=" * 74)
    print("变异 ①：iq_registry 撞号（复现改前的 STB:7 / INTTOINT:7）")
    print("=" * 74)
    tmpd = Path(tempfile.mkdtemp(prefix="stb_mut_"))
    dup = "iq_registry:\n" + "".join(f"  {u}: {i}\n" for u, i in
                                     [("FPMUL", 0), ("INTTOINT", 7), ("STB", 7)])
    (dup_f := tmpd / "dup_master.yaml").write_text(dup, encoding="utf-8")
    (dup_s := tmpd / "dup_sys.yaml").write_text(dup, encoding="utf-8")
    check("注入 STB:7 与 INTTOINT:7 同 id", "test_iq_registry_no_duplicate_id_in_either_spec",
          expect_red=True, _mod_patches={"_SPEC_FILES": {"master": dup_f, "system_core": dup_s}})

    print("\n" + "=" * 74)
    print("变异 ①b：两份 specs 对 STB 的 id 不一致")
    print("=" * 74)
    (ag_m := tmpd / "ag_m.yaml").write_text("iq_registry:\n  STB: 52\n  STB_AXI: 53\n"
                                            "  LDB: 50\n  LDB_AXI: 51\n", encoding="utf-8")
    (ag_s := tmpd / "ag_s.yaml").write_text("iq_registry:\n  STB: 99\n  STB_AXI: 53\n"
                                            "  LDB: 50\n  LDB_AXI: 51\n", encoding="utf-8")
    check("master STB:52 vs system_core STB:99", "test_two_specs_agree_on_store_and_load_family",
          expect_red=True, _mod_patches={"_SPEC_FILES": {"master": ag_m, "system_core": ag_s}})

    print("\n" + "=" * 74)
    print("变异 ②：STB_AXI 条目缺失（复现 ADR-052 D4 的前缀吞噬）")
    print("=" * 74)
    no_axi = mutant_spec(lambda s: s.modules.pop("STB_AXI"))
    check("删掉 modules['STB_AXI']", "test_stb_axi_not_swallowed_by_stb_prefix",
          expect_red=True, spec=no_axi)
    check("删掉 modules['STB_AXI']", "test_resolve_iq_type_store_and_load_family",
          expect_red=True, spec=no_axi)

    stub7 = mutant_spec(lambda s: s.modules.__setitem__(
        "STB", types.SimpleNamespace(code_bits=7, code_fields=[
            types.SimpleNamespace(name="VLD", bits=1),
            types.SimpleNamespace(name="SLOT_ID", bits=6)])))
    check("STB 退回 7-bit stub", "test_stb_axi_not_swallowed_by_stb_prefix",
          expect_red=True, spec=stub7)

    print("\n" + "=" * 74)
    print("变异 ⑤：镜像关系被破坏（下移 6 位 / STB_AXI 留 tid）")
    print("=" * 74)

    def shift6(s):
        """把 inst_id/smc_id_en 只下移 6 位（ADR-052 §2 明确警告的错误实现）。"""
        b = s.modules["STB"]
        b.code_fields = [f for f in b.code_fields if f.name not in ("inst_id", "smc_id_en")]
        b.code_fields += [types.SimpleNamespace(name="pad", bits=2),
                          types.SimpleNamespace(name="inst_id", bits=4),
                          types.SimpleNamespace(name="smc_id_en", bits=5)]
        b.code_bits = sum(f.bits for f in b.code_fields)

    check("inst_id/smc_id_en 只下移 6 位", "test_mirror_relation_stb_from_ldb",
          expect_red=True, spec=mutant_spec(shift6))

    def keep_tid(s):
        t = s.modules["STB_AXI"]
        t.code_fields = list(t.code_fields) + [types.SimpleNamespace(name="tid", bits=2)]
        t.code_bits = 104

    check("STB_AXI 保留 tid（= LDBAXI）", "test_mirror_relation_stbaxi_from_ldbaxi",
          expect_red=True, spec=mutant_spec(keep_tid))
    check("STB_AXI 保留 tid → 位宽也查出", "test_stb_axi_not_swallowed_by_stb_prefix",
          expect_red=True, spec=mutant_spec(keep_tid))

    print("\n" + "=" * 74)
    print("变异 KAT：body 参数被扰动（防假绿）")
    print("=" * 74)
    orig = TM._KAT_CASES
    try:
        TM._KAT_CASES = (("STB", "iq_stb.hex", {"INC": 16, "VLD": 1},
                          dict(orig[0][3], ur_addr=12288 + (1 << 12))),) + orig[1:]
        check("STB ur_addr 12288→16384（bank 漂移）", "test_kat_reproduces_golden_row5",
              expect_red=True)
        TM._KAT_CASES = (("STB_AXI", "iq_stb_axi.hex", {"INC": 256, "LOOPS": 1, "VLD": 1},
                          dict(orig[1][3], gr_base_addr=12288 + 16)),)
        check("STB_AXI gr_base_addr 12288→12304", "test_kat_reproduces_golden_row5",
              expect_red=True)
    finally:
        TM._KAT_CASES = orig

    print("\n" + "=" * 74)
    print("变异 ③：get_iq_name 只改一份清单（真实源码变异 + exec）")
    print("=" * 74)
    src = (ROOT / "src" / "api" / "hw_spec.py").read_text(encoding="utf-8")
    OLD = '"LDB", "LDB_AXI", "STB", "STB_AXI"]:'
    NEW = '"LDB", "LDB_AXI"]:'
    if src.count(OLD) != 2:
        print(f"  ❌ hw_spec.py 里该清单应有 2 份，实得 {src.count(OLD)} —— "
              f"清单结构变了，本变异需同步更新")
        return 1
    head, _, tail = src.rpartition(OLD)      # 只变异**最后**一份（全局扫描兜底）
    mutated = head + NEW + tail
    ns: dict = {}
    exec(compile(mutated, "hw_spec_mutated.py", "exec"), ns)
    print(f"       两份清单存在性核对：变异前 {src.count(OLD)} 份 → 变异后 {mutated.count(OLD)} 份 ✅")

    class Stub:
        """轻量 self：get_iq_name 只用 .config / .get_iq_ids。"""
        config = TM.TestStbSchemaRegression.spec.config
        get_iq_ids = HardwareSpec.get_iq_ids

    mut_fn = ns["HardwareSpec"].__dict__["get_iq_name"]
    real_fn = HardwareSpec.__dict__["get_iq_name"]
    # (iq_id, 单元, 期望名, 是否应因本次变异分叉)
    #   ⚠ 51/LDB_AXI 是**阳性对照**：本次变异只删了 "STB","STB_AXI"，LDB_AXI 仍在兜底清单里
    #     → 本就不该分叉。期望它分叉是写脚本时的想当然（首轮即踩）。
    #   ⚠ 两条路径都必须调：带 unit_type 走快路径（第 1 份清单，未变异）会提前 return，
    #     压根到不了被变异的兜底清单 —— 只调快路径会误判「变异未复现」（首轮亦踩）。
    for iq_id, u_t, want, should_diverge in ((53, "STB_AXI", "stb_axi", True),
                                             (52, "STB", "stb", True),
                                             (51, "LDB_AXI", "ldb_axi", False)):
        fast = mut_fn(Stub(), iq_id, u_t)     # unit_type 快路径
        scan = mut_fn(Stub(), iq_id)          # 全局扫描兜底 ← 被变异的那份
        diverged = (fast != scan)
        ok = (diverged == should_diverge)
        RESULTS.append(ok)
        if should_diverge:
            print(f"  {'✅' if ok else '❌'} 变异后 get_iq_name({iq_id}) [{u_t}]: 快路径={fast!r} / "
                  f"兜底={scan!r} → "
                  f"{'两路径分叉，测试的 assertEqual(fast, scan) 必红 ✅' if diverged else '未复现病理'}")
        else:
            print(f"  {'✅' if ok else '❌'} 阳性对照 get_iq_name({iq_id}) [{u_t}]: 快路径={fast!r} / "
                  f"兜底={scan!r} → "
                  f"{'未受波及（变异是外科式的）✅' if not diverged else '误伤邻项 ❌'}")
        assert real_fn(Stub(), iq_id, u_t) == want and real_fn(Stub(), iq_id) == want, \
            "未变异的原函数两条路径都应正常"
    print("   （对照：未变异时两条路径逐值相同 → 见上方基线 test_get_iq_name_both_paths 全绿）")

    print("\n" + "=" * 74)
    print(f"变异测试汇总：{sum(RESULTS)}/{len(RESULTS)} 条符合预期"
          f"{'  ✅ 全部守卫均被证明可红' if all(RESULTS) else '  ❌ 存在无信号价值的守卫'}")
    print("=" * 74)
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
