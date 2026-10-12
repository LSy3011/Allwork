#!/usr/bin/env python3
"""f6a_unit_check.py — F6-A 单元 TB 裁决器（与 TB 分离，gate3 纪律同构）。

解析 tb_f6a_lsu_complete.v 的 [F6A_FACT] 事实行，按 C-F6 v3.1 契约断言
求值，并对照模式期望矩阵（legacy=旧行为基线 / fixed=修复版）判定。

拍序约定：所有拍号均为 TB 监测记录拍（同一约定），断言一律用**相对拍差**
（done − last_credit_visible 等），TB 记簿常数在差值中抵消。

契约断言：
  C1 T-RACE    : 竞态形态（dispatch 时仍有未发射/未回信用指令）。
                 fixed: done−lcv == 2；legacy: >=1 才算不早于真实完成
                 （旧模块预期违反 = 竞态复现）。
  C2 T-PREARM  : 武装前信用已返回仍能完成（done!=-1），拍差同 C1。
  C3 T-EMPTY   : 空批。fixed: done−dispatch_valid == 3（载荷捕获后首个
                 求值拍完成，钉死）；legacy: >=2（兼容）。
  C4 T-LASTCRD : 无竞态形态。fixed: done−lcv == 2；legacy: >=1。
  C5 T-ERRSYN  : 末笔信用与错误同拍 ⇒ 窗内 done 永不出现 且 err 已观测。
                 （合法完成拍紧，窗 20 拍 >> 合法拍，缺席即阻断成立。）
  C6 连续批    : 两批各满足拍差断言 且 done2 > done1 且无误报 err。
  C7 T-DUPPAYLOAD（2026-10-10 补丁定向测试，判据随补丁首跑**前**冻结）：
               重复载荷窗覆盖「完成判定首次通过 posedge」两种口径——
               fixed: done1==-1（协议异常当拍优先，完成被阻断）∧
                      done2!=-1 ∧ done2−lcv2==2（恢复批正常完成不误锁死）
                      ∧ err==-1；
               legacy 忽略载荷 ⇒ done1 必然出现 ⇒ 预期 VIOLATION。
               变异核验：对打补丁前 DUT 本用例必然 VIOLATION（有牙齿）。
               **proto 扩展（2026-10-10 缺口修复轮，用户复核裁定 #4a，
               首跑前冻结）**：fixed 追加 proto>=0——重复载荷须触发
               arm_err ⇒ proto_err_o 拉高（协议异常上报端口定向证据；
               系统级 FAILED 触发路径由 TB-B B9 验收）。legacy 包装
               无此端口（TB 常 0），不经本项判定。

★ 钉拍勘误（2026-10-09，F6-A 阶段一 fixed 首跑后）：冻结稿原钉
  C1/C2/C4==1、C3==2、C6==1/1，系从契约翻译到 TB 监测口径时整体偏差
  一拍。三重独立证明（均不依赖修复实现本身）：
  ①契约 v3.1 §4.3：「t 拍脉冲 → t+1 起 r_crd 更新可见 → done 最早在
    结束 t+1 的 posedge 注册」。归档 C1 trace（f6a_c1_trace_20261009，
    字节核验）钉死：末信用脉冲可见=监测拍30=FACT 的 lcv，计数器更新
    可见=31，done 注册于结束 31 的 posedge → 监测拍 32 可见
    ⇒ done−lcv 恒为 2（非 1）。
  ②NBA 不可能性：拍差==1 要求 DUT 在末信用注册的**同一** posedge 采到
    更新后计数器（违反 NBA 语义）；冻结端口只有计数器、无信用脉冲输入，
    物理上不可实现。==2 是**在当前注册级数、接口与采样约定下的最早合法
    拍**；任何额外流水级都会失败。
  ③已归档 legacy 基线（f6a_unit_legacy_baseline_20261009，AS-EXPECTED）
    通过用例的实测拍差恰为 C2/C3/C4/C6 = 2/3/2/2/2——修复版若被钉得比
    已验收的 legacy 还严一拍，判据本身即不自洽。
  C3 的 ==3 同理由「载荷捕获后首个求值拍」（捕获 posedge 的下一拍求值、
  再下一拍监测可见）+ t_dispatch 记录约定得出。
  本勘误只修正钉拍常数；PASS/VIOLATION 期望矩阵与 legacy >= 判据不变。
  修复版的性质断言（C1 竞态消除、C5 窗内阻断、兼容用例不慢于 legacy）
  不受勘误影响，反而由 ==2 继续严格保证。

模式期望矩阵（裁决「竞态用例旧 RED/新 GREEN，兼容用例允许两版皆 GREEN」）：
  legacy: C1=VIOLATION C2=PASS C3=PASS C4=PASS C5=VIOLATION C6=PASS
          C7=VIOLATION（2026-10-10 首跑前冻结：legacy 忽略载荷必然放行）
  fixed : 全部 PASS

用法: f6a_unit_check.py --mode {legacy|fixed} FACTS.log
退出码: 0=观测与模式矩阵一致; 1=不一致; 2=事实缺失/解析失败
"""
import argparse
import re
import sys

EXPECT = {
    "legacy": {"C1": False, "C2": True, "C3": True, "C4": True,
               "C5": False, "C6": True, "C7": False},
    "fixed":  {"C1": True, "C2": True, "C3": True, "C4": True,
               "C5": True, "C6": True, "C7": True},
}


def parse_facts(text):
    facts = {"__suite_done__": "suite=done" in text}
    for m in re.finditer(r"\[F6A_FACT\]\s+(\S.*?)\s+end\b", text):
        kv, case = {}, None
        for t in m.group(1).split():
            k, _, v = t.partition("=")
            if k == "case":
                case = v
            else:
                try:
                    kv[k] = int(v)
                except ValueError:
                    kv[k] = v
        if case:
            facts[case] = kv
    return facts


def judge(case, f, mode):
    """返回 (contract_pass: bool, detail: str)。exact=True 仅 fixed。"""
    exact = (mode == "fixed")
    try:
        if case in ("C1", "C2", "C4"):
            delta = f["done"] - f["last_credit_visible"]
            rel = (delta == 2) if exact else (delta >= 1)
            ok = rel and f["done"] != -1 and f["err"] == -1
            return ok, (f"done-lcv={delta} want={'==2' if exact else '>=1'}")
        if case == "C3":
            delta = f["done"] - f["dispatch"]
            rel = (delta == 3) if exact else (delta >= 2)
            ok = rel and f["err"] == -1
            return ok, (f"done-dv={delta} want={'==3' if exact else '>=2'}")
        if case == "C5":
            ok = f["done"] == -1 and f["err"] >= 0
            return ok, f"done={f['done']} err={f['err']}"
        if case == "C6":
            d1 = f["done1"] - f["last_credit1_visible"]
            d2 = f["done2"] - f["last_credit2_visible"]
            if exact:
                rel = (d1 == 2 and d2 == 2)
            else:
                rel = (d1 >= 1 and d2 >= 1)
            ok = (rel and f["done2"] > f["done1"] and f["done1"] != -1
                  and f["err"] == -1)
            return ok, f"d1={d1} d2={d2} done1={f['done1']} done2={f['done2']}"
        if case == "C7":
            d2 = f["done2"] - f["last_credit2_visible"]
            rel = (d2 == 2) if exact else (d2 >= 1)
            # proto（2026-10-10 缺口修复轮，用户复核裁定 #4a）：fixed 模式
            # 要求重复载荷触发 arm_err ⇒ proto_err_o 拉高（协议异常上报
            # 端口定向）；legacy 包装无此端口（TB 常 0），不经本项判定。
            proto_ok = (f["proto"] >= 0) if exact else True
            ok = (f["done1"] == -1 and f["done2"] != -1 and rel
                  and f["err"] == -1 and proto_ok)
            want = "done1==-1,d2==2,proto>=0" if exact else "done1==-1,d2>=1"
            return ok, (f"done1={f['done1']} done2={f['done2']} d2={d2} "
                        f"proto={f['proto']} want={want}")
    except KeyError as e:
        return None, f"missing key {e}"
    return None, "unknown case"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["legacy", "fixed"], required=True)
    ap.add_argument("log")
    a = ap.parse_args()
    text = open(a.log, encoding="utf-8", errors="replace").read()
    facts = parse_facts(text)
    if not facts["__suite_done__"]:
        print("[F6A_CHECK][FAIL] suite 未完成（缺 suite=done）")
        return 2
    matrix = EXPECT[a.mode]
    all_match = True
    print(f"[F6A_CHECK] mode={a.mode}")
    for case in ["C1", "C2", "C3", "C4", "C5", "C6", "C7"]:
        if case not in facts:
            print(f"[F6A_CHECK][FAIL] {case}: 事实缺失")
            all_match = False
            continue
        cp, detail = judge(case, facts[case], a.mode)
        if cp is None:
            print(f"[F6A_CHECK][FAIL] {case}: 解析失败 {detail}")
            all_match = False
            continue
        want = matrix[case]
        verdict = "PASS" if cp else "VIOLATION"
        match = "AS-EXPECTED" if cp == want else "**SURPRISE**"
        if cp != want:
            all_match = False
        print(f"[F6A_CHECK] {case}: contract={verdict} "
              f"expect={'PASS' if want else 'VIOLATION'} -> {match} | {detail}")
    print(f"[F6A_CHECK] overall={'AS-EXPECTED' if all_match else 'MISMATCH'}")
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
