"""ADR-053 阶段2-M1：编译器产物 → u280_sim_v422 VCS case 打包器（multi-stage 版）。

⚠ 2026-09-12 修订：单-stage 流程（`run_sim_aiacc_top_f.sh` + `tb_aiacc_top_v04_f.v`）
已弃用且当前编译不过（TB poke `gen_ram_4MB_dw_128` 依赖从未定义的 `AE350_SRAM_4MB`）。
唯一在维护的入口是 **multi-stage 流程**：
`sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh`
（TB `tb_aiacc_top_v04_f_multi_stage.v`，simv 已编译，+define+USE_RTOVR_SPARSE_MASK）。

该流程的 case 契约（`generate_data_list_coverage_multi_stage.py` 强制）：

    <sandbox>/test_atomic_ops/multi_inst_stage/<case>/
        smi_iq_inst/stage_0/iq_*.hex   ← 指令流（stage 必须从 0 起连续编号；
                                         编译器产物为单 stage，全部进 stage_0）
        smc_0/ur_data/bank*.hex        ← 输入 banks（TB $readmemh 预载）
        smc_0/output_bank/*.hex        ← 期望输出（compare_ur_to_golden_multi_stage.py golden 侧）
        smc_N/...                      ← 若编译器多 SMC 发射，同样拷贝

生成器文件名映射（继承主 generator）：
- `iq_vin0/iq_vin1/iq_vout0.hex` → IQ id 68/69/70（inst_mem_vin*），bit_len=48
- `iq_rtovr-<idx>.hex` → id 28+idx；`iq_ur-r<i>` → 16+i；`iq_ur-w<i>` → 20+i
- 行宽 padding 无谓：`_split_inst_to_beats` 按 bit_len 低位掩码取指令，
  编译器 nybble 对齐（FPMUL 52b）与 golden 的 byte 对齐（56b）殊途同归，
  **前提是指令语义在低位右对齐**（D7 已逐位验证；vin body=0x0f 与 golden 一致）。

注意：golden 旧 case（如 `multi_inst_stage/mul_scalar_8smc_v3`，iq 在 smc_0/ 内、
无 smi_iq_inst/）是单-stage 时代的布局，**不满足当前生成器契约**，不可照抄。

用法：
    python3 verification_v2/tools/package_vcs_case.py --op mul --case compiler_mul
    cd ~/u280_sim_v422 && \\
        ./sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh multi_inst_stage/compiler_mul/smc_0
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SANDBOX = Path.home() / "u280_sim_v422"
DEFAULT_SUITE = "multi_inst_stage"


def package(build_dir: Path, suite: str, case_name: str,
            sandbox: Path, clean: bool, stage: int = 0) -> Path:
    if not build_dir.is_dir():
        raise SystemExit(f"build 目录不存在: {build_dir}（先跑 main_verify --op <op> --mode e2e）")
    # 指令源：build 根下第一个含 iq_*.hex 的 smc_* 目录（单 SMI 全局流，只在 smc_0）
    smc_dirs = sorted(p for p in build_dir.glob("smc_*") if p.is_dir())
    if not smc_dirs:
        raise SystemExit(f"{build_dir} 下无 smc_* 目录")
    iq_src_dir = None
    for d in smc_dirs:
        if list(d.glob("iq_*.hex")):
            iq_src_dir = d
            break
    if iq_src_dir is None:
        raise SystemExit(f"{build_dir}/smc_* 下无 iq_*.hex 指令文件")

    case = sandbox / "test_atomic_ops" / suite / case_name
    if case.exists() and clean:
        shutil.rmtree(case)
    case.mkdir(parents=True, exist_ok=True)
    stage_dir = case / "smi_iq_inst" / f"stage_{stage}"
    stage_dir.mkdir(parents=True, exist_ok=True)

    n_iq = 0
    for f in sorted(iq_src_dir.glob("iq_*.hex")):
        shutil.copy2(f, stage_dir / f.name)
        n_iq += 1

    n_smc = 0
    for d in smc_dirs:
        dst = case / d.name
        copied = False
        for sub in ("ur_data", "output_bank"):
            src = d / sub
            if src.is_dir():
                if (dst / sub).exists():
                    shutil.rmtree(dst / sub)
                shutil.copytree(src, dst / sub)
                copied = True
        if copied:
            n_smc += 1

    rel = f"{suite}/{case_name}/smc_0"
    print(f"[package_vcs_case] case = {case}")
    print(f"  指令：{n_iq} 个 iq_*.hex → smi_iq_inst/stage_{stage}/（源 {iq_src_dir.name}）: "
          f"{sorted(f.name for f in stage_dir.glob('iq_*.hex'))}")
    print(f"  数据：{n_smc} 个 smc_* （ur_data + output_bank）")
    print(f"下一步：cd {sandbox} && "
          f"./sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh {rel}")
    return case


def package_multi(build_dir: Path, suite: str, case_name: str,
                  sandbox: Path, clean: bool) -> Path:
    """ADR-057 D5：多段构建（含 stage_manifest.json + stages/stage_k/）的打包。

    契约（任一不满足即 SystemExit，拒绝半截打包）：
      * 段目录 stages/stage_0 .. stage_{K-1} 编号连续、与 manifest body.stages
        段数一致（manifest 在此仅作交叉计数——字段真值由 check_stage_manifest
        独立裁决，打包器不复算）；
      * 每段 stages/stage_k/smc_0/iq_*.hex 非空 → case/smi_iq_inst/stage_k/；
      * 输入 banks 只取 stage_0 的 smc_0/ur_data（全量输入单份装载，UR 跨段
        持久——python sim 侧 poison 一次的镜像）；
      * golden 侧 = build 顶层 smc_0/output_bank（链式 sim **全段终态**，
        由 physical_runner 多段链式驱动产出），缺即拒绝；
      * clean=True 只在入口抹一次 case 目录（多段共享同一 case，逐段 clean
        会抹掉前段指令——验收①侦察教训的形式化）。
    """
    man_path = Path(build_dir) / "stage_manifest.json"
    if not man_path.is_file():
        raise SystemExit(f"{build_dir} 无 stage_manifest.json——非多段构建，"
                         f"单段请用 package()")
    body = (json.loads(man_path.read_text(encoding="utf-8")).get("body") or {})
    stages_root = Path(build_dir) / "stages"
    ids = sorted(int(d.name.split("_", 1)[1])
                 for d in stages_root.glob("stage_*") if d.is_dir())
    if not ids or ids != list(range(len(ids))):
        raise SystemExit(f"段目录编号不连续或缺失: {ids}——拒绝打包（漏段/重号）")
    if len(body.get("stages", [])) != len(ids):
        raise SystemExit(f"manifest 段数 {len(body.get('stages', []))} != "
                         f"磁盘段数 {len(ids)}——manifest 与产物不一致，拒绝打包")

    case = sandbox / "test_atomic_ops" / suite / case_name
    if case.exists() and clean:
        shutil.rmtree(case)            # 全 case 唯一一次清理
    case.mkdir(parents=True, exist_ok=True)

    ur_src = stages_root / "stage_0" / "smc_0" / "ur_data"
    if not ur_src.is_dir():
        raise SystemExit(f"缺 {ur_src}——stage_0 未携带全量输入，拒绝打包")
    dst = case / "smc_0" / "ur_data"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ur_src, dst)

    ob_src = Path(build_dir) / "smc_0" / "output_bank"
    if not ob_src.is_dir():
        raise SystemExit(f"缺 {ob_src}——链式 sim 全段终态（golden 侧）不存在，拒绝打包")
    dst = case / "smc_0" / "output_bank"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ob_src, dst)

    n_total = 0
    for k in ids:
        iqs = sorted((stages_root / f"stage_{k}" / "smc_0").glob("iq_*.hex"))
        if not iqs:
            raise SystemExit(f"stage_{k} 无 iq_*.hex——段产物缺失，拒绝打包")
        dst = case / "smi_iq_inst" / f"stage_{k}"
        dst.mkdir(parents=True, exist_ok=True)
        for f in iqs:
            shutil.copy2(f, dst / f.name)
        n_total += len(iqs)

    rel = f"{suite}/{case_name}/smc_0"
    print(f"[package_vcs_case] MULTI-STAGE case = {case}")
    print(f"  段数：{len(ids)}（smi_iq_inst/stage_0..{len(ids) - 1}，共 {n_total} 个 iq 文件）")
    print(f"  数据：smc_0/ur_data ← stages/stage_0（全量输入单份）；"
          f"smc_0/output_bank ← 顶层（链式终态 golden）")
    print(f"下一步：cd {sandbox} && "
          f"./sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh {rel}")
    return case


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--op", required=True, help="算子名（build 目录名，默认源 build/verification_v2/<op>）")
    ap.add_argument("--case", default=None,
                    help="VCS case 名（默认 = --op；建议 compiler_mul 之类以区分 golden）")
    ap.add_argument("--build-dir", type=Path, default=None,
                    help="默认 build/verification_v2/<op>")
    ap.add_argument("--suite", default=DEFAULT_SUITE,
                    help="VCS suite（默认 multi_inst_stage；runner 只认此 suite）")
    ap.add_argument("--sandbox", type=Path, default=DEFAULT_SANDBOX)
    ap.add_argument("--no-clean", dest="clean", action="store_false", default=True,
                    help="不清目标 case 旧内容（默认清理）")
    a = ap.parse_args()
    build = a.build_dir or (ROOT / "build" / "verification_v2" / a.op)
    # 形态由产物自声明：stage_manifest.json 存在 ⟺ 多段构建（ADR-057 D5）。
    if (build / "stage_manifest.json").is_file():
        package_multi(build.resolve(), a.suite, a.case or a.op, a.sandbox, a.clean)
    else:
        package(build.resolve(), a.suite, a.case or a.op, a.sandbox, a.clean)


if __name__ == "__main__":
    sys.exit(main())
