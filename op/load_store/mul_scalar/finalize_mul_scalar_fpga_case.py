#!/usr/bin/env python3
"""Finalize a generated mul_scalar RTL case for the FPGA v4.22 pipeline."""

import argparse
import re
from pathlib import Path


FPGA_QUEUES = (
    (40, 68, "vin0"),
    (41, 69, "vin1"),
    (42, 70, "vout0"),
)

RTL_UR_WAIT = "02000060380000000000"
FPGA_UR_WAIT = "02000020380000000000"


def read_iq_id(path: Path) -> int:
    first = path.read_text(encoding="utf-8").splitlines()[0]
    match = re.search(r"\bIQ_ID=(\d+)\b", first)
    if not match:
        raise RuntimeError(f"missing IQ_ID in {path}")
    return int(match.group(1))


def finalize_stage(stage_dir: Path, subq: int) -> None:
    data_list = stage_dir / f"data_list_inst_subq{subq}.txt"
    if not data_list.is_file():
        raise RuntimeError(f"missing data list: {data_list}")

    control_rows = []
    renamed = {}
    for reserved_port, iq_id, unit in FPGA_QUEUES:
        old_hex = stage_dir / f"iq_rtovr-{reserved_port}.hex"
        new_hex = stage_dir / f"iq_{iq_id}_{unit}.hex"
        old_inst = stage_dir / f"iq_inst_rtovr_{reserved_port}_subq{subq}.txt"
        new_inst = stage_dir / f"iq_inst_{iq_id}_{unit}_subq{subq}.txt"

        if old_hex.exists():
            old_hex.replace(new_hex)
        if old_inst.exists():
            old_inst.replace(new_inst)
        if not new_hex.is_file() or not new_inst.is_file():
            raise RuntimeError(f"missing FPGA IQ{iq_id} files under {stage_dir}")
        if read_iq_id(new_inst) != iq_id:
            raise RuntimeError(f"wrong IQ_ID in {new_inst}; expected {iq_id}")

        renamed[old_inst.name] = new_inst.name
        control_rows.append(f"inst_mem_{unit} {new_inst.resolve()}")

    normal_rows = []
    for raw in data_list.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        basename = Path(line.split()[-1]).name
        if basename in renamed or basename in renamed.values():
            continue
        normal_rows.append(line)

    data_list.write_text(
        "\n".join(control_rows + normal_rows) + "\n", encoding="utf-8"
    )

    discovered = {p.name for p in stage_dir.glob(f"iq_inst_*_subq{subq}.txt")}
    listed = {
        Path(line.split()[-1]).name
        for line in data_list.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    if discovered != listed:
        raise RuntimeError(
            f"data list mismatch: missing={sorted(discovered - listed)} "
            f"extra={sorted(listed - discovered)}"
        )


def advance_fpga_ur_writeback(stage_dir: Path) -> None:
    """Advance UR writeback by two loop issues for the physical pipeline."""
    path = stage_dir / "iq_ur-w0.hex"
    if not path.is_file():
        raise RuntimeError(f"missing UR writeback IQ: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise RuntimeError(f"empty UR writeback IQ: {path}")

    first = lines[0].strip().lower()
    if first == RTL_UR_WAIT:
        lines[0] = FPGA_UR_WAIT
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif first != FPGA_UR_WAIT:
        raise RuntimeError(
            f"unexpected UR wait instruction {first} in {path}; "
            f"expected {RTL_UR_WAIT}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--stage", type=int, default=1)
    parser.add_argument("--subq", type=int, default=0)
    parser.add_argument(
        "--keep-rtl-writeback",
        action="store_true",
        help="rename IQ68/69/70 only; do not apply the FPGA writeback advance",
    )
    args = parser.parse_args()

    stage_dir = args.case_dir.resolve() / "smi_iq_inst" / f"stage_{args.stage}"
    if not stage_dir.is_dir():
        raise SystemExit(f"missing compute stage: {stage_dir}")
    finalize_stage(stage_dir, args.subq)
    if not args.keep_rtl_writeback:
        advance_fpga_ur_writeback(stage_dir)
    print(f"FPGA v4.22 mul_scalar case finalized: {stage_dir}")


if __name__ == "__main__":
    main()
