#!/usr/bin/env python3
"""f6b_check.py — F6-B 子系统 TB 裁决器（与 TB 分离）。

解析 tb_f6b_smi_top.v 的 [F6B_FACT] 事实行，按 C-F6 v3.1（E1-E4、§9.0
T-FAILLOCK/T-INTFULL/T-RSTLATE + 阶段二 §12）求值，对照模式期望矩阵判定。

执行边界 2：期望在运行前冻结（本文件的 EXPECT 即冻结载体）；
AS-EXPECTED = 观测符合预先定义，**不等于新契约通过**。

======================== 判据 v2（2026-10-10，首跑前冻结） ========================
演进依据 = F6 文档 §12.5/§12.6（阶段二「错误及时检出」+ E3/E4 观测项）。
演进只升不降：阶段一判据全部保留；「静默吸收」推导值被「检出上报」推导值
替换；B3/B4/B5 的 intid_pX 0→1 与 intr_cyc −1→>late_cyc 属**判据演进
（标准提高）非 DUT 行为变化**——2026-10-09/10 归档（判据 v1）封存不动。

契约断言（PASS 条件，各模式同判据）：
  B1 T-FAILLOCK:
     iss0==3              批 A 三条全部发射（激励健全性）
     intid_err>=1         错误事件已通知（Q6：intid 续增）
     interr_err==1        int_err 置位（E3；legacy R6 不置位 → 违约）
     failed_err==1        FAILED 锁存置位（E4 条件 i，0x004[2]）
     iss_blk==0           FAILED/楔死期间排队 START B 零发射
     doneB==0             B 未登记完成
     iss_postcfgrst==0    cfg_rst 后新写 START C 仍不得执行（E4 核心；
                          legacy R4 清锁会执行 → 违约复现）
     doneC==0 ∧ doneD==1 ∧ eidD==3   C 不完成；rst_n 后 D 恢复控制
     failed_postrst==1    锁存挺过 cfg_rst（「cfg_rst 不得单独解锁」实证）
  B2 T-INTFULL（兼容用例，各版皆应 PASS，R11 既有机制）：
     intid_mid50==50 / intid_final==255 / interr==1 / intr==1
  B3 T-RSTLATE（字面序：在飞→cfg_rst→旧信用→新 START）：
     iss_p2==0 ∧ doneC==0          无发射/无完成
     intid_p3==1 ∧ interr_p3==1 ∧ failed_p3==1
                          **v2**：迟到信用被 off-by-one 修复检出（crd==cru==0
                          越界）→ 错误通知一次 + 锁存；legacy/fixed_p1 静默
                          吸收 ⇒ 本组违约（B3 翻转定性见 §12.6）
     doneD==1 ∧ eidD==1   rst_n 后新批恢复正常
     诚实注记（v1 保留）：legacy 失效形态为 executing 下溢静默楔死
     （dly_queen :220 无条件递减 0→7）；v1 下 host 面与锁定不可区分记
     PASS，v2 下「未检出迟到信用」本身即违约载体。
  B4 T-RSTLATE-ATTRIB（归因探针：在飞→cfg_rst→新 START C→旧信用）：
     iss_p2==0            fixed_p2：FAILED 锁定 ⇒ C 不得发射
     doneC==0             零真信用下 C 不得完成
     intid_p4==1 ∧ interr_p4==1 ∧ failed_p4==1
                          **v2**：旧信用检出 ⇒ 错误通知恰一次
     intr_cyc>late_cyc    通知在旧信用注入之后（因果序；legacy 竞态形态
                          intr 早于 late_cyc 13 拍 → 违约；fixed_p1 假成功
                          通知在 late_cyc 后 → 本项过，余项违约）
     doneD==1 ∧ eidD==1   rst_n 后恢复控制
  B5 T-RSTLATE-ATTRIB2（归因加固：C=2 指令 + 两笔旧信用 id=0/1 足额）：
     iss_p2==0 ∧ doneC==0          C 零发射零完成
     intid_p5==1 ∧ interr_p5==1 ∧ failed_p5==1
                          **v2**：首笔旧信用检出即通知一次；第二笔粘滞期内
                          不重复自增（「同一失败只通知一次」实证）
     intr_cyc>late1_cyc   因果序
     doneD==1 ∧ eidD==1   rst_n 后恢复控制
  B6 T-UNITERR（v2 新增，E2 定向）：
     iss6==1              批 A 一指令发射（健全性）
     intid6==1            通知恰一次（legacy/fixed_p1 为假成功通知、
                          fixed_p2 为错误通知——由下行区分）
     interr6==1 ∧ failed6==1 ∧ doneA6==0
                          fixed_p2：错误通知 + 锁存 + 拒完成（E1「计数恰好
                          相等也不能形成成功」直接实证：crd−base==1==N 但
                          err 排除优先）；legacy/fixed_p1 丢 bit6 ⇒ 假完成
     iss_p6==0 ∧ doneC6==0 ∧ failed6b==1   锁存期间新 START 不执行
     doneD6==1 ∧ eidD6==1          rst_n 后恢复控制

======================== 判据 v3（2026-10-10，用户复核裁定补三缺口，首跑前冻结） ====
裁定原文：「错误与 batch_done 真正同拍时仍可能同时置错误位和成功完成位」
「若 IQ92 已经把全局 ir_err 持高，随后 IQ24–27 出现新的 lsu_err_evt，外层
条件可能不成立，导致 FAILED 锁住了但本次 LSU 错误没有通知或没有置 int_err」
「arm_err 仍无输出。重复载荷、基线不一致如何触发系统 FAILED？」
——v2 判据全部保留（B1-B6 期望不变），新增三定向用例：
  B7 T-ERRPRIO（错误同拍优先，控制层）：
     iss7==1               批 A 一指令发射（健全性）
     doneA7==0             错误与 batch_done 同拍 ⇒ 0x024 不登记成功（核心）
     intid7==1 ∧ interr7==1 ∧ failed7==1
                           通知恰一次记错误 + FAILED 锁存
     iss_p7==0 ∧ doneC7==0 锁存期间新 START 不执行
     failed7b==1           cfg_rst 不解锁
     doneD7==1 ∧ eidD7==1  rst_n 后恢复控制
  B8 T-ERRMASK（通知遮蔽）：
     intid_a==1 ∧ interr_a==1 ∧ failed_a==1
                           源1 首笔检出 ⇒ 通知一次记错误+锁存
     intid_b==1            同源第二笔无新边沿 ⇒ 不重复计数（notify-once）
     intid_c==2            源1 持高期间源2 新错误：逐源边沿 ⇒ 仍通知一次
                           （1-bit OR 边沿会被遮蔽——复核裁定 #3 定向）
     interr_c==1 ∧ failed_c==1 ∧ doneD==1
  B9 T-PROTOERR（协议异常上报；force 诊断性强制，异常产生机制证据在
     TB-A C7 proto 字段；lsu_err_evt 独立进入通知，ir_err 全程未参与）：
     intid9==1 ∧ interr9==1 ∧ failed9==1
     iss_p9==0 ∧ doneC9==0 锁存期间新 START 不执行
     failed9b==1           release 源撤销后锁存保持（rst_n 唯一解锁）
     doneD9==1 ∧ eidD9==1  rst_n 后恢复控制
     legacy/fixed_p1：无 F6B_HAS_F6PROTO ⇒ TB 打印 skipped=1 ⇒ 矩阵记 S

模式期望矩阵（v3，2026-10-10 首跑前冻结）：
  legacy  : B1=F B2=T B3=F B4=F B5=F B6=F B7=F B8=F B9=S
  fixed_p1: B1=F B2=T B3=F B4=F B5=F B6=F B7=F B8=F B9=S
  fixed_p2: 全部 PASS（B9 含；即阶段二缺口修复目标矩阵）

用法: f6b_check.py --mode {legacy|fixed_p1|fixed_p2} FACTS.log
退出码: 0=观测与模式矩阵一致; 1=不一致; 2=事实缺失/解析失败
"""
import argparse
import re
import sys

EXPECT = {
    "legacy":   {"B1": False, "B2": True, "B3": False, "B4": False, "B5": False, "B6": False, "B7": False, "B8": False, "B9": "SKIP"},
    "fixed_p1": {"B1": False, "B2": True, "B3": False, "B4": False, "B5": False, "B6": False, "B7": False, "B8": False, "B9": "SKIP"},
    "fixed_p2": {"B1": True,  "B2": True, "B3": True,  "B4": True,  "B5": True,  "B6": True,  "B7": True,  "B8": True,  "B9": True},
}


def parse_facts(text):
    facts = {"__suite_done__": "suite=done" in text}
    for m in re.finditer(r"\[F6B_FACT\]\s+(\S.*?)\s+end\b", text):
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


def judge(case, f):
    try:
        if case == "B1":
            checks = [
                ("iss0==3",            f["iss0"] == 3),
                ("intid_err>=1",       f["intid_err"] >= 1),
                ("interr_err==1",      f["interr_err"] == 1),
                ("failed_err==1",      f["failed_err"] == 1),
                ("iss_blk==0",         f["iss_blk"] == 0),
                ("doneB==0",           f["doneB"] == 0),
                ("iss_postcfgrst==0",  f["iss_postcfgrst"] == 0),
                ("doneC==0",           f["doneC"] == 0),
                ("failed_postrst==1",  f["failed_postrst"] == 1),
                ("doneD==1",           f["doneD"] == 1),
                ("eidD==3",            f["eidD"] == 3),
            ]
        elif case == "B2":
            checks = [
                ("intid_mid50==50",    f["intid_mid50"] == 50),
                ("intid_final==255",   f["intid_final"] == 255),
                ("interr==1",          f["interr"] == 1),
                ("intr==1",            f["intr"] == 1),
            ]
        elif case == "B3":
            checks = [
                ("iss_p2==0",          f["iss_p2"] == 0),
                ("doneC==0",           f["doneC"] == 0),
                ("intid_p3==1",        f["intid_p3"] == 1),
                ("interr_p3==1",       f["interr_p3"] == 1),
                ("failed_p3==1",       f["failed_p3"] == 1),
                ("doneD==1",           f["doneD"] == 1),
                ("eidD==1",            f["eidD"] == 1),
            ]
        elif case == "B4":
            checks = [
                ("iss_p2==0",          f["iss_p2"] == 0),
                ("doneC==0",           f["doneC"] == 0),
                ("intid_p4==1",        f["intid_p4"] == 1),
                ("interr_p4==1",       f["interr_p4"] == 1),
                ("failed_p4==1",       f["failed_p4"] == 1),
                ("intr_cyc>late_cyc",  f["intr_cyc"] > f["late_cyc"]),
                ("doneD==1",           f["doneD"] == 1),
                ("eidD==1",            f["eidD"] == 1),
            ]
        elif case == "B5":
            checks = [
                ("iss_p2==0",          f["iss_p2"] == 0),
                ("doneC==0",           f["doneC"] == 0),
                ("intid_p5==1",        f["intid_p5"] == 1),
                ("interr_p5==1",       f["interr_p5"] == 1),
                ("failed_p5==1",       f["failed_p5"] == 1),
                ("intr_cyc>late1_cyc", f["intr_cyc"] > f["late1_cyc"]),
                ("doneD==1",           f["doneD"] == 1),
                ("eidD==1",            f["eidD"] == 1),
            ]
        elif case == "B6":
            checks = [
                ("iss6==1",            f["iss6"] == 1),
                ("intid6==1",          f["intid6"] == 1),
                ("interr6==1",         f["interr6"] == 1),
                ("failed6==1",         f["failed6"] == 1),
                ("doneA6==0",          f["doneA6"] == 0),
                ("iss_p6==0",          f["iss_p6"] == 0),
                ("doneC6==0",          f["doneC6"] == 0),
                ("failed6b==1",        f["failed6b"] == 1),
                ("doneD6==1",          f["doneD6"] == 1),
                ("eidD6==1",           f["eidD6"] == 1),
            ]
        elif case == "B7":
            checks = [
                ("iss7==1",            f["iss7"] == 1),
                ("doneA7==0",          f["doneA7"] == 0),
                ("intid7==1",          f["intid7"] == 1),
                ("interr7==1",         f["interr7"] == 1),
                ("failed7==1",         f["failed7"] == 1),
                ("iss_p7==0",          f["iss_p7"] == 0),
                ("doneC7==0",          f["doneC7"] == 0),
                ("failed7b==1",        f["failed7b"] == 1),
                ("doneD7==1",          f["doneD7"] == 1),
                ("eidD7==1",           f["eidD7"] == 1),
            ]
        elif case == "B8":
            checks = [
                ("intid_a==1",         f["intid_a"] == 1),
                ("interr_a==1",        f["interr_a"] == 1),
                ("failed_a==1",        f["failed_a"] == 1),
                ("intid_b==1",         f["intid_b"] == 1),
                ("intid_c==2",         f["intid_c"] == 2),
                ("interr_c==1",        f["interr_c"] == 1),
                ("failed_c==1",        f["failed_c"] == 1),
                ("doneD==1",           f["doneD"] == 1),
            ]
        elif case == "B9":
            if f.get("skipped", 0) == 1:
                return "SKIP", "skipped（未编译 F6B_HAS_F6PROTO）"
            checks = [
                ("intid9==1",          f["intid9"] == 1),
                ("interr9==1",         f["interr9"] == 1),
                ("failed9==1",         f["failed9"] == 1),
                ("iss_p9==0",          f["iss_p9"] == 0),
                ("doneC9==0",          f["doneC9"] == 0),
                ("failed9b==1",        f["failed9b"] == 1),
                ("doneD9==1",          f["doneD9"] == 1),
                ("eidD9==1",           f["eidD9"] == 1),
            ]
        else:
            return None, "unknown case"
    except KeyError as e:
        return None, f"missing key {e}"
    bad = [name for name, ok in checks if not ok]
    return (not bad), ("ok" if not bad else "fail: " + ",".join(bad))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["legacy", "fixed_p1", "fixed_p2"],
                    required=True)
    ap.add_argument("log")
    a = ap.parse_args()
    text = open(a.log, encoding="utf-8", errors="replace").read()
    facts = parse_facts(text)
    if not facts["__suite_done__"]:
        print("[F6B_CHECK][FAIL] suite 未完成（缺 suite=done）")
        return 2
    matrix = EXPECT[a.mode]
    all_match = True
    print(f"[F6B_CHECK] mode={a.mode} criteria=v3")
    for case in ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9"]:
        if case not in facts:
            print(f"[F6B_CHECK][FAIL] {case}: 事实缺失")
            all_match = False
            continue
        cp, detail = judge(case, facts[case])
        if cp is None:
            print(f"[F6B_CHECK][FAIL] {case}: 解析失败 {detail}")
            all_match = False
            continue
        want = matrix[case]
        if cp == "SKIP" or want == "SKIP":
            ok_match = (cp == "SKIP" and want == "SKIP")
            if not ok_match:
                all_match = False
            print(f"[F6B_CHECK] {case}: contract=SKIP "
                  f"expect={want} -> {'AS-EXPECTED' if ok_match else '**SURPRISE**'} | {detail}")
            continue
        verdict = "PASS" if cp else "VIOLATION"
        match = "AS-EXPECTED" if cp == want else "**SURPRISE**"
        if cp != want:
            all_match = False
        print(f"[F6B_CHECK] {case}: contract={verdict} "
              f"expect={'PASS' if want else 'VIOLATION'} -> {match} | {detail}")
    print(f"[F6B_CHECK] overall={'AS-EXPECTED' if all_match else 'MISMATCH'}")
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
