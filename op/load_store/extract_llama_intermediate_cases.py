#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract LLAMA fused-op intermediates into single-op validation cases.

This script intentionally does not try to run the whole fused op in RTL.
It materializes the intermediate tensors that map to standalone operators:

  - varlen_fwd: QK score -> mul_scalar(softmax_scale)
  - reshape_and_cache_flash: slot_mapping/index_put cache state checks

When --run-rtl is set, the QK-score scale tensor is sent through the existing
load_store_v3 mul_scalar RTL flow.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import re
import shutil
import struct
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
CURRENT_LSV3 = SCRIPT_DIR
SNAP_LSV3 = Path("/home/juno1023/Jianyuan_before_upload_20260707_110002/snapshot/load_store_v3")
BASE = SNAP_LSV3 / "run_llama_full_rtl_v3.py"
RUN421 = SNAP_LSV3 / "run_llama_full_rtl_v3_421_multistage.py"

RTL_DEFAULT = Path("/home/juno1023/Jianyuan-aiacc_4.21")
OUT_DEFAULT = CURRENT_LSV3 / "out" / ("llama_intermediate_extract_" + time.strftime("%Y%m%d_%H%M%S"))

SCALE = 0.08838834764831845

VARLEN = {
    "r1": {
        "label": "varlen_r1_qk_score_softmax_scale",
        "dir": Path("/home/bupt/0000-round-1/0047-forward/0233-2/0244-self_attn/0285-attn/0300-_vllm_fa2_C::varlen_fwd"),
        "q": "00000-1147x32x128xfloat16-q.bin",
        "k": "00001-128x16x32x128xfloat16-k.bin",
        "v": "00002-128x16x32x128xfloat16-v.bin",
        "out": "00010-1147x32x128xfloat16-return.0.bin",
        "lse": "00011-32x1147xfloat32-return.1.bin",
        "cu_q": "00004-2xint32-cu_seqlens_q.bin",
        "seqused_k": "00006-1xint32-seqused_k.bin",
        "block_table": "00007-1x128xint32-block_table.bin",
        "q_shape": (1147, 32, 128),
        "cache_shape": (128, 16, 32, 128),
        "causal": True,
    },
    "r2": {
        "label": "varlen_r2_qk_score_softmax_scale",
        "dir": Path("/home/bupt/0000-round-2/0037-forward/2548-27/2559-self_attn/2600-attn/2615-_vllm_fa2_C::varlen_fwd"),
        "q": "00000-1x32x128xfloat16-q.bin",
        "k": "00001-128x16x32x128xfloat16-k.bin",
        "v": "00002-128x16x32x128xfloat16-v.bin",
        "out": "00010-1x32x128xfloat16-return.0.bin",
        "lse": "00011-32x1xfloat32-return.1.bin",
        "cu_q": "00004-2xint32-cu_seqlens_q.bin",
        "seqused_k": "00006-1xint32-seqused_k.bin",
        "block_table": "00007-1x128xint32-block_table.bin",
        "q_shape": (1, 32, 128),
        "cache_shape": (128, 16, 32, 128),
        "causal": True,
    },
}

CACHE = {
    "r1": {
        "label": "cache_r1_slot_mapping_index_put",
        "dir": Path("/home/bupt/0000-round-1/0047-forward/0233-2/0244-self_attn/0285-attn/0291-_C_cache_ops::reshape_and_cache_flash"),
        "key": "00000-1147x32x128xfloat16-key.bin",
        "value": "00001-1147x32x128xfloat16-value.bin",
        "key_cache_before": "00002-128x16x32x128xfloat16-key_cache.bin",
        "value_cache_before": "00003-128x16x32x128xfloat16-value_cache.bin",
        "slot_mapping": "00004-1147xint64-slot_mapping.bin",
        "k_scale": "00005-1xfloat32-k_scale.bin",
        "v_scale": "00006-1xfloat32-v_scale.bin",
        "key_cache_after": "00007-128x16x32x128xfloat16-key_cache-output.bin",
        "value_cache_after": "00008-128x16x32x128xfloat16-value_cache-output.bin",
        "tok_shape": (1147, 32, 128),
        "cache_shape": (128, 16, 32, 128),
    },
    "r2": {
        "label": "cache_r2_slot_mapping_index_put",
        "dir": Path("/home/bupt/0000-round-2/0037-forward/2548-27/2559-self_attn/2600-attn/2606-_C_cache_ops::reshape_and_cache_flash"),
        "key": "00000-1x32x128xfloat16-key.bin",
        "value": "00001-1x32x128xfloat16-value.bin",
        "key_cache_before": "00002-128x16x32x128xfloat16-key_cache.bin",
        "value_cache_before": "00003-128x16x32x128xfloat16-value_cache.bin",
        "slot_mapping": "00004-1xint64-slot_mapping.bin",
        "k_scale": "00005-1xfloat32-k_scale.bin",
        "v_scale": "00006-1xfloat32-v_scale.bin",
        "key_cache_after": "00007-128x16x32x128xfloat16-key_cache-output.bin",
        "value_cache_after": "00008-128x16x32x128xfloat16-value_cache-output.bin",
        "tok_shape": (1, 32, 128),
        "cache_shape": (128, 16, 32, 128),
    },
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load {}".format(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


llama_v3 = load_module("llama_v3_extract", BASE)
llama421 = load_module("llama421_extract", RUN421)
llama_v3.SCRIPT_DIR = CURRENT_LSV3
llama_v3.SMI_IQ_DIR = CURRENT_LSV3.parent
llama421.SCRIPT_DIR = CURRENT_LSV3
llama421.BASE_SCRIPT = CURRENT_LSV3 / "run_llama_full_rtl_v3.py"


def read_f32(path: Path) -> float:
    return struct.unpack("<f", path.read_bytes()[:4])[0]


def stable_softmax(x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    m = np.max(x, axis=-1, keepdims=True)
    e = np.exp(x - m)
    s = np.sum(e, axis=-1, keepdims=True)
    p = e / s
    lse = np.squeeze(np.log(s) + m, axis=-1)
    return p.astype(np.float32), lse.astype(np.float32)


def gather_cache_sequence(cache: np.ndarray, block_table: np.ndarray, seq_len: int) -> np.ndarray:
    out = np.empty((seq_len, cache.shape[2], cache.shape[3]), dtype=cache.dtype)
    for i in range(seq_len):
        block = int(block_table[i // cache.shape[1]])
        offset = i % cache.shape[1]
        out[i] = cache[block, offset]
    return out


def masked_scores(score: np.ndarray, q_abs_start: int, causal: bool) -> np.ndarray:
    if not causal:
        return score
    masked = score.copy()
    q_len, _, k_len = masked.shape
    k_idx = np.arange(k_len)
    for q in range(q_len):
        abs_q = q_abs_start + q
        masked[q, :, k_idx > abs_q] = -np.inf
    return masked


def max_abs(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0:
        return 0.0
    return float(np.max(np.abs(a.astype(np.float32) - b.astype(np.float32))))


def extract_varlen(spec: Dict[str, object], out_dir: Path,
                   q_start: int = 0, q_count: Optional[int] = None) -> Dict[str, object]:
    d = spec["dir"]
    q_shape = spec["q_shape"]
    cache_shape = spec["cache_shape"]
    q_all = np.fromfile(d / spec["q"], dtype=np.float16).reshape(q_shape)
    k_cache = np.fromfile(d / spec["k"], dtype=np.float16).reshape(cache_shape)
    v_cache = np.fromfile(d / spec["v"], dtype=np.float16).reshape(cache_shape)
    block_table = np.fromfile(d / spec["block_table"], dtype=np.int32).reshape(-1)
    seq_len = int(np.fromfile(d / spec["seqused_k"], dtype=np.int32)[0])
    cu_q = np.fromfile(d / spec["cu_q"], dtype=np.int32)
    q_total = q_all.shape[0]
    if q_count is None:
        q_count = q_total - q_start
    q_end = min(q_total, q_start + q_count)
    q = q_all[q_start:q_end].astype(np.float32)
    k_seq = gather_cache_sequence(k_cache, block_table, seq_len).astype(np.float32)
    v_seq = gather_cache_sequence(v_cache, block_table, seq_len).astype(np.float32)

    q_abs_start = int(cu_q[0]) + q_start
    if q_total == 1 and seq_len > q_total:
        q_abs_start = seq_len - q.shape[0]

    qk = np.einsum("qhd,khd->qhk", q, k_seq, optimize=True).astype(np.float32)
    scaled = (qk * np.float32(SCALE)).astype(np.float32)
    masked = masked_scores(scaled, q_abs_start, bool(spec["causal"]))
    prob, lse = stable_softmax(masked)
    out = np.einsum("qhk,khd->qhd", prob, v_seq, optimize=True).astype(np.float32)
    out_fp16 = out.astype(np.float16)

    case_dir = out_dir / "intermediate" / spec["label"]
    case_dir.mkdir(parents=True, exist_ok=True)
    qk_path = case_dir / "qk_score_fp32.bin"
    scaled_path = case_dir / "scaled_score_fp32.bin"
    masked_path = case_dir / "masked_score_fp32.bin"
    prob_path = case_dir / "softmax_prob_fp32.bin"
    out_path = case_dir / "context_out_fp16.bin"
    scalar_path = case_dir / "softmax_scale_fp32.bin"
    qk.tofile(qk_path)
    scaled.tofile(scaled_path)
    masked.tofile(masked_path)
    prob.tofile(prob_path)
    out_fp16.tofile(out_path)
    scalar_path.write_bytes(struct.pack("<f", SCALE))

    captured_out = np.fromfile(d / spec["out"], dtype=np.float16).reshape(q_shape)[q_start:q_end]
    captured_lse = np.fromfile(d / spec["lse"], dtype=np.float32)
    if q_shape[0] == 1:
        captured_lse = captured_lse.reshape((q_shape[1], q_shape[0])).T
    else:
        captured_lse = captured_lse.reshape((q_shape[1], q_shape[0])).T
    captured_lse = captured_lse[q_start:q_end]

    row_sum_err = float(np.max(np.abs(np.sum(prob, axis=-1) - 1.0))) if prob.size else 0.0
    invalid_max = 0.0
    if bool(spec["causal"]):
        invalid_vals: List[float] = []
        for q_i in range(prob.shape[0]):
            abs_q = q_abs_start + q_i
            if abs_q + 1 < prob.shape[-1]:
                invalid_vals.append(float(np.max(prob[q_i, :, abs_q + 1:])))
        invalid_max = max(invalid_vals) if invalid_vals else 0.0

    meta = {
        "label": spec["label"],
        "q_start": q_start,
        "q_count": q_end - q_start,
        "q_total": q_total,
        "q_abs_start": q_abs_start,
        "seq_len_k": seq_len,
        "softmax_scale": SCALE,
        "qk_shape": list(qk.shape),
        "dtype": "fp32",
        "qk_score": str(qk_path),
        "scaled_score": str(scaled_path),
        "masked_score": str(masked_path),
        "softmax_prob": str(prob_path),
        "context_out": str(out_path),
        "softmax_scale_bin": str(scalar_path),
        "context_max_abs_err_vs_capture": max_abs(out_fp16, captured_out),
        "lse_max_abs_err_vs_capture": max_abs(lse, captured_lse),
        "softmax_row_sum_max_err": row_sum_err,
        "masked_invalid_prob_max": invalid_max,
        "nan_count": int(np.isnan(prob).sum() + np.isnan(qk).sum()),
        "inf_count": int(np.isinf(qk).sum()),
    }
    (case_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return meta


def fp16_mul_bits_like_rtl(raw_bits: np.ndarray, scalar_bits: int) -> np.ndarray:
    a = raw_bits.view(np.float16)
    s = np.array([np.uint16(scalar_bits)], dtype=np.uint16).view(np.float16)[0]
    r_bits = (a * s).astype(np.float16).view(np.uint16).copy()
    a_exp = (raw_bits >> np.uint16(10)) & np.uint16(0x1F)
    a_mant = raw_bits & np.uint16(0x03FF)
    s_exp = (np.uint16(scalar_bits) >> np.uint16(10)) & np.uint16(0x1F)
    s_mant = np.uint16(scalar_bits) & np.uint16(0x03FF)
    a_small = (a_exp == 0) | ((a_exp == 1) & (a_mant == 0))
    s_small = (s_exp == 0) | ((s_exp == 1) & (s_mant == 0))
    small = np.ones_like(a_small, dtype=bool) if bool(s_small) else a_small
    sign = (raw_bits ^ np.uint16(scalar_bits)) & np.uint16(0x8000)
    r_bits[small] = sign[small]
    return r_bits


def scaled_fp16_like_rtl(arr: np.ndarray, scale: float) -> np.ndarray:
    scalar_bits = int(np.array([scale], dtype=np.float16).view(np.uint16)[0])
    return fp16_mul_bits_like_rtl(arr.view(np.uint16), scalar_bits).view(np.float16)


def scaled_fp16_reference(arr: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return arr.copy()
    return (arr.astype(np.float32) * np.float32(scale)).astype(np.float16)


def extract_cache(spec: Dict[str, object], out_dir: Path) -> Dict[str, object]:
    d = spec["dir"]
    tok_shape = spec["tok_shape"]
    cache_shape = spec["cache_shape"]
    key = np.fromfile(d / spec["key"], dtype=np.float16).reshape(tok_shape)
    value = np.fromfile(d / spec["value"], dtype=np.float16).reshape(tok_shape)
    key_before = np.fromfile(d / spec["key_cache_before"], dtype=np.float16).reshape(cache_shape)
    value_before = np.fromfile(d / spec["value_cache_before"], dtype=np.float16).reshape(cache_shape)
    key_after = np.fromfile(d / spec["key_cache_after"], dtype=np.float16).reshape(cache_shape)
    value_after = np.fromfile(d / spec["value_cache_after"], dtype=np.float16).reshape(cache_shape)
    slots = np.fromfile(d / spec["slot_mapping"], dtype=np.int64)
    k_scale = read_f32(d / spec["k_scale"])
    v_scale = read_f32(d / spec["v_scale"])

    key_scaled = scaled_fp16_reference(key, k_scale)
    value_scaled = scaled_fp16_reference(value, v_scale)
    written = np.zeros(cache_shape[:2], dtype=bool)
    key_bad = 0
    value_bad = 0
    rows = []
    for i, slot in enumerate(slots):
        slot_i = int(slot)
        block = slot_i // cache_shape[1]
        offset = slot_i % cache_shape[1]
        written[block, offset] = True
        k_ok = bool(np.array_equal(key_scaled[i].view(np.uint16), key_after[block, offset].view(np.uint16)))
        v_ok = bool(np.array_equal(value_scaled[i].view(np.uint16), value_after[block, offset].view(np.uint16)))
        key_bad += 0 if k_ok else 1
        value_bad += 0 if v_ok else 1
        rows.append({
            "token_index": i,
            "slot": slot_i,
            "block": block,
            "offset": offset,
            "key_status": "PASS" if k_ok else "FAIL",
            "value_status": "PASS" if v_ok else "FAIL",
        })

    untouched_key_bad = int(np.count_nonzero(key_before[~written].view(np.uint16) != key_after[~written].view(np.uint16)))
    untouched_value_bad = int(np.count_nonzero(value_before[~written].view(np.uint16) != value_after[~written].view(np.uint16)))
    slot_oob = int(np.count_nonzero((slots < 0) | (slots >= cache_shape[0] * cache_shape[1])))
    duplicate_slots = int(len(slots) - len(set(int(x) for x in slots)))

    case_dir = out_dir / "intermediate" / spec["label"]
    case_dir.mkdir(parents=True, exist_ok=True)
    with (case_dir / "slot_mapping_index_put.tsv").open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["token_index", "slot", "block", "offset", "key_status", "value_status"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    key_scaled.tofile(case_dir / "key_scaled_fp16.bin")
    value_scaled.tofile(case_dir / "value_scaled_fp16.bin")

    meta = {
        "label": spec["label"],
        "token_count": int(len(slots)),
        "k_scale": k_scale,
        "v_scale": v_scale,
        "slot_min": int(slots.min()) if slots.size else None,
        "slot_max": int(slots.max()) if slots.size else None,
        "slot_oob": slot_oob,
        "duplicate_slots": duplicate_slots,
        "key_written_fail": key_bad,
        "value_written_fail": value_bad,
        "key_untouched_fail_elements": untouched_key_bad,
        "value_untouched_fail_elements": untouched_value_bad,
        "status": "PASS" if slot_oob == 0 and key_bad == 0 and value_bad == 0 and untouched_key_bad == 0 and untouched_value_bad == 0 else "FAIL",
        "slot_mapping_tsv": str(case_dir / "slot_mapping_index_put.tsv"),
        "key_scaled": str(case_dir / "key_scaled_fp16.bin"),
        "value_scaled": str(case_dir / "value_scaled_fp16.bin"),
    }
    (case_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return meta


def append_summary(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    rows = list(rows)
    if not rows:
        return
    fields = sorted({k for row in rows for k in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def update_mul_scalar_case_data(case_abs: Path, qk_path: Path, scalar_path: Path,
                                scaled_path: Path, chunk: int, lines_per_smc: int,
                                dtype: str, max_valid: int = 255) -> List[int]:
    raw_len = qk_path.stat().st_size
    if raw_len != scaled_path.stat().st_size:
        raise ValueError("qk/scaled size mismatch: {} {}".format(qk_path, scaled_path))
    total_lines = (raw_len + 15) // 16
    valid_lines = llama_v3.chunk_valid_lines(total_lines, chunk, lines_per_smc, 8)

    meta = json.loads((case_abs / "meta_v3.json").read_text())
    word_bytes = int(meta["word_bytes"])
    input_line = 0
    scalar_line = 0
    store_line = int(meta["store_byte_base"]) // word_bytes
    for ip in meta.get("inputs", []):
        if ip["name"] == "input":
            input_line = int(ip["byte_base"]) // word_bytes
        elif ip["name"] == "scalar":
            scalar_line = int(ip["byte_base"]) // word_bytes

    scalar_lines = llama_v3.scalar_lines_from_bytes(scalar_path.read_bytes(), dtype, max_valid)
    hbm_dir = case_abs / "hbm_data"
    with qk_path.open("rb") as qfp, scaled_path.open("rb") as gfp:
        base_line = chunk * lines_per_smc * 8
        for smc in range(8):
            start_line = base_line + smc * lines_per_smc
            valid = valid_lines[smc]
            start_byte = start_line * 16
            qfp.seek(start_byte)
            gfp.seek(start_byte)
            input_seg = qfp.read(valid * 16)
            golden_seg = gfp.read(valid * 16)

            input_lines = [llama_v3.hex_line_from_mem(input_seg[i:i + 16])
                           for i in range(0, len(input_seg), 16)]
            golden_lines = [llama_v3.hex_line_from_mem(golden_seg[i:i + 16])
                            for i in range(0, len(golden_seg), 16)]
            while len(input_lines) < max_valid:
                input_lines.append("0" * 32)
            while len(golden_lines) < max_valid:
                golden_lines.append("0" * 32)

            mem_lines = ["0" * 32] * (store_line + max_valid)
            for i, line in enumerate(input_lines):
                mem_lines[input_line + i] = line
            for i, line in enumerate(scalar_lines):
                mem_lines[scalar_line + i] = line
            llama_v3.write_lines(hbm_dir / "memory_init{}.hex".format(smc + 1), mem_lines)
            llama_v3.write_lines(
                case_abs / "smc_{}".format(smc) / "output_bank/bank2_final.hex",
                golden_lines + ["0" * 32] * max(0, 256 - len(golden_lines)),
            )
    return valid_lines


def verify_ddr_unique(case_abs: Path, out_dir: Path, case_name: str,
                      chunk: int) -> Tuple[int, Optional[int], Optional[int], Path]:
    verify_log = out_dir / "logs" / "{}_chunk{:04d}.verify.log".format(case_name, chunk)
    proc = llama421.run_cmd(
        ["python3", str(CURRENT_LSV3 / "verify_ddr_v3.py"), str(case_abs)],
        cwd=CURRENT_LSV3.parent,
    )
    verify_log.parent.mkdir(parents=True, exist_ok=True)
    verify_log.write_text(proc.stdout or "", encoding="utf-8")
    ddr_pass, ddr_fail = llama421.parse_v3_summary(proc.stdout or "")
    return proc.returncode, ddr_pass, ddr_fail, verify_log


def run_reuse_multistage_case(rtl_dir: Path, multi_abs: Path, multi_rel: str,
                              out_dir: Path, label: str, chunk: int,
                              chunk_count: int, detail: str,
                              generate_data_list: bool) -> Dict[str, object]:
    llama421.copy_case_hbm_to_sram(rtl_dir, multi_abs)
    log_path = out_dir / "logs" / "{}_chunk{:04d}.log".format(multi_abs.name, chunk)
    proc = llama421.run_cmd(
        ["bash", "sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh", "{}/smc_0".format(multi_rel)],
        cwd=rtl_dir,
        env={
            "SKIP_GENERATE_DATA_LIST": "0" if generate_data_list else "1",
            "SKIP_COMPARE": "0",
        },
        log_path=log_path,
    )
    log_text = llama421.read_text(log_path)
    ur_pass, ur_fail = llama421.parse_ur_summary(log_text)
    ddr_rc, ddr_pass, ddr_fail, verify_log = verify_ddr_unique(multi_abs, out_dir, multi_abs.name, chunk)
    status = "PASS" if proc.returncode == 0 and ddr_rc == 0 else "FAIL"
    if status != "PASS":
        detail = "{}; sim_rc={} ddr_rc={}".format(detail, proc.returncode, ddr_rc)
    return {
        "status": status,
        "op": "mul_scalar",
        "llama_case": label,
        "chunk_index": chunk,
        "chunk_count": chunk_count,
        "ur_pass": ur_pass,
        "ur_fail": ur_fail,
        "ddr_pass": ddr_pass,
        "ddr_fail": ddr_fail,
        "case_rel": str(multi_rel).replace("multi_inst_stage/", "verification_v2/", 1),
        "multi_case_rel": multi_rel,
        "log": str(log_path),
        "verify_log": str(verify_log),
        "detail": detail,
    }


AXI_CODE_BYTES = 13
PACKED_CHUNK_STRIDE_BYTES = 0x3000


def patch_axi_gr_base(line: str, base_offset: int) -> str:
    data = bytearray.fromhex(line.strip())
    if len(data) < AXI_CODE_BYTES:
        raise ValueError("AXI IQ line too short: {}".format(line))
    code = bytes(data[-AXI_CODE_BYTES:])
    cru = int.from_bytes(code[::-1], "little")
    mask = (1 << 40) - 1
    base = (cru >> 13) & mask
    new_base = (base + base_offset) & mask
    cru = (cru & ~(mask << 13)) | (new_base << 13)
    data[-AXI_CODE_BYTES:] = cru.to_bytes(AXI_CODE_BYTES, "little")[::-1]
    return data.hex()


def copy_stage_with_hbm_offset(src_stage: Path, dst_stage: Path, base_offset: int) -> None:
    dst_stage.mkdir(parents=True, exist_ok=True)
    for src in sorted(src_stage.glob("*.hex")):
        dst = dst_stage / src.name
        if src.name in ("iq_ldb_axi.hex", "iq_stb_axi.hex"):
            lines = [
                patch_axi_gr_base(line, base_offset)
                for line in src.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            ]
            llama_v3.write_lines(dst, lines)
        else:
            shutil.copy2(src, dst)


def golden_lines_for_chunk(raw_return: bytes, chunk: int, smc: int,
                           lines_per_smc: int, words: int) -> List[str]:
    base_line = chunk * lines_per_smc * 8 + smc * lines_per_smc
    start = base_line * 16
    seg = raw_return[start:start + words * 16]
    lines = [llama_v3.hex_line_from_mem(seg[i:i + 16]) for i in range(0, len(seg), 16)]
    while len(lines) < words:
        lines.append("0" * 32)
    return lines[:words]


def build_packed_mul_scalar_case(rtl_dir: Path, gen: Path, label: str,
                                 qk_path: Path, scalar_path: Path, scaled_path: Path,
                                 chunks: List[int], chunk_count: int,
                                 lines_per_smc: int) -> Tuple[Path, str, Dict[str, object]]:
    if not chunks:
        raise ValueError("empty packed chunk list")
    words = lines_per_smc
    if words != 255:
        raise ValueError("packed QK scale currently expects lines_per_smc=255, got {}".format(words))

    safe = llama421.safe_label(label)
    template_name = "mul_scalar_llama_{}_pack_template".format(safe)
    template_abs, _case_rel, _max_valid, _valid_lines = llama_v3.prepare_mul_scalar_case_from_node(
        rtl_dir, gen, template_name, qk_path, scalar_path, scaled_path, 0, lines_per_smc, "fp32")
    template_multi_abs, _template_multi_rel = llama421.convert_to_multistage(rtl_dir, template_abs, "mul_scalar")

    pack_name = "mul_scalar_llama_{}_pack{:04d}_{:04d}".format(safe, chunks[0], chunks[-1])
    pack_rel = "multi_inst_stage/{}".format(pack_name)
    pack_abs = rtl_dir / "test_atomic_ops" / pack_rel
    llama421.reset_dir(pack_abs)
    llama421.copy_smc_dirs(template_multi_abs, pack_abs)

    smi_iq_root = pack_abs / "smi_iq_inst"
    if smi_iq_root.exists():
        shutil.rmtree(smi_iq_root)
    smi_iq_root.mkdir(parents=True, exist_ok=True)
    for local_i, _chunk in enumerate(chunks):
        base_offset = local_i * PACKED_CHUNK_STRIDE_BYTES
        for stage in range(3):
            copy_stage_with_hbm_offset(
                template_multi_abs / "smi_iq_inst" / "stage_{}".format(stage),
                smi_iq_root / "stage_{}".format(local_i * 3 + stage),
                base_offset,
            )

    raw_qk = qk_path.read_bytes()
    raw_scaled = scaled_path.read_bytes()
    raw_scalar = scalar_path.read_bytes()
    if len(raw_qk) != len(raw_scaled):
        raise ValueError("qk/scaled size mismatch: {} {}".format(qk_path, scaled_path))
    total_lines = (len(raw_qk) + 15) // 16
    scalar_lines = llama_v3.scalar_lines_from_bytes(raw_scalar, "fp32", words)

    input_line = 0
    scalar_line = 0x1000 // 16
    store_line = 0x2000 // 16
    hbm_lines_per_pack = (len(chunks) * PACKED_CHUNK_STRIDE_BYTES) // 16
    hbm_dir = pack_abs / "hbm_data"
    hbm_dir.mkdir(parents=True, exist_ok=True)

    packed_chunks = []
    for local_i, chunk in enumerate(chunks):
        valid_lines = llama_v3.chunk_valid_lines(total_lines, chunk, lines_per_smc, 8)
        packed_chunks.append({
            "chunk_index": chunk,
            "local_index": local_i,
            "store_byte_base": local_i * PACKED_CHUNK_STRIDE_BYTES + 0x2000,
            "valid_lines_per_smc": valid_lines,
        })

    final_chunk = chunks[-1]
    for smc in range(8):
        mem_lines = ["0" * 32] * hbm_lines_per_pack
        for local_i, chunk in enumerate(chunks):
            base_line = (local_i * PACKED_CHUNK_STRIDE_BYTES) // 16
            valid_lines = packed_chunks[local_i]["valid_lines_per_smc"]
            valid = int(valid_lines[smc])
            src_line = chunk * lines_per_smc * 8 + smc * lines_per_smc
            start = src_line * 16
            input_seg = raw_qk[start:start + valid * 16]
            input_lines = [llama_v3.hex_line_from_mem(input_seg[i:i + 16])
                           for i in range(0, len(input_seg), 16)]
            for i, line in enumerate(input_lines):
                mem_lines[base_line + input_line + i] = line
            for i, line in enumerate(scalar_lines):
                mem_lines[base_line + scalar_line + i] = line
        llama_v3.write_lines(hbm_dir / "memory_init{}.hex".format(smc + 1), mem_lines)
        final_golden = golden_lines_for_chunk(raw_scaled, final_chunk, smc, lines_per_smc, words)
        llama_v3.write_lines(
            pack_abs / "smc_{}".format(smc) / "output_bank" / "bank2_final.hex",
            final_golden + ["0" * 32] * max(0, 256 - len(final_golden)),
        )

    meta = {
        "op": "mul_scalar",
        "packed": True,
        "num_smc": 8,
        "out_bank": 2,
        "out_words": words,
        "word_bytes": 16,
        "dtype": "fp32",
        "chunk_count": chunk_count,
        "lines_per_smc": lines_per_smc,
        "chunk_stride_bytes": PACKED_CHUNK_STRIDE_BYTES,
        "input_byte_base": 0,
        "scalar_byte_base": 0x1000,
        "store_byte_base": 0x2000,
        "llama_case": label,
        "llama_self": str(qk_path),
        "llama_other": str(scalar_path),
        "llama_return": str(scaled_path),
        "chunks": packed_chunks,
    }
    (pack_abs / "meta_packed_v3.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (pack_abs / "meta_v3.json").write_text(
        json.dumps({
            "op": "mul_scalar",
            "num_smc": 8,
            "out_bank": 2,
            "out_words": words,
            "store_byte_base": (len(chunks) - 1) * PACKED_CHUNK_STRIDE_BYTES + 0x2000,
            "word_bytes": 16,
            "dtype": "fp32",
            "valid_output_bytes": words * 16,
            "packed_meta": str(pack_abs / "meta_packed_v3.json"),
            "inputs": [],
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return pack_abs, pack_rel, meta


def verify_packed_ddr(case_abs: Path, out_dir: Path, case_name: str) -> Tuple[int, int, int, Path]:
    meta = json.loads((case_abs / "meta_packed_v3.json").read_text(encoding="utf-8"))
    raw_return = Path(meta["llama_return"]).read_bytes()
    words = int(meta["out_words"])
    word_bytes = int(meta["word_bytes"])
    lines_per_smc = int(meta["lines_per_smc"])
    verify_log = out_dir / "logs" / "{}.packed.verify.log".format(case_name)
    verify_log.parent.mkdir(parents=True, exist_ok=True)
    npass = 0
    nfail = 0
    rows = []
    for item in meta["chunks"]:
        chunk = int(item["chunk_index"])
        store_line = int(item["store_byte_base"]) // word_bytes
        for smc in range(int(meta["num_smc"])):
            hbm_path = case_abs / "smc_{}".format(smc) / "hbm_result_smc_{}.hex".format(smc)
            hbm = [ln.strip().lower() for ln in hbm_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            expected = golden_lines_for_chunk(raw_return, chunk, smc, lines_per_smc, words)
            got = hbm[store_line:store_line + words]
            bad = -1
            if len(got) < words:
                bad = len(got)
            else:
                for i, (g, e) in enumerate(zip(got, expected)):
                    if g != e:
                        bad = i
                        break
            if bad < 0:
                npass += 1
                rows.append("chunk {} smc_{} PASS DDR==golden ({} words @line {})".format(
                    chunk, smc, words, store_line))
            else:
                nfail += 1
                got_s = got[bad] if bad < len(got) else "<missing>"
                exp_s = expected[bad] if bad < len(expected) else "<missing>"
                rows.append("chunk {} smc_{} FAIL @word {} golden={} ddr={}".format(
                    chunk, smc, bad, exp_s, got_s))
    rows.append("PACKED-DDR-VERIFY Summary: pass={} fail={} chunks={}".format(
        npass, nfail, len(meta["chunks"])))
    verify_log.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return (0 if nfail == 0 else 1), npass, nfail, verify_log


def run_packed_multistage_case(rtl_dir: Path, multi_abs: Path, multi_rel: str,
                               out_dir: Path, label: str, chunks: List[int],
                               chunk_count: int, detail: str) -> List[Dict[str, object]]:
    llama421.copy_case_hbm_to_sram(rtl_dir, multi_abs)
    log_path = out_dir / "logs" / "{}.log".format(multi_abs.name)
    proc = llama421.run_cmd(
        ["bash", "sim_multi_stage/run_sim_aiacc_top_f_multi_stage.sh", "{}/smc_0".format(multi_rel)],
        cwd=rtl_dir,
        env={"SKIP_GENERATE_DATA_LIST": "0", "SKIP_COMPARE": "0"},
        log_path=log_path,
    )
    log_text = llama421.read_text(log_path)
    ur_pass, ur_fail = llama421.parse_ur_summary(log_text)
    ddr_rc, ddr_pass, ddr_fail, verify_log = verify_packed_ddr(multi_abs, out_dir, multi_abs.name)
    status = "PASS" if proc.returncode == 0 and ddr_rc == 0 else "FAIL"
    row_detail = "{}; packed_chunks={}".format(detail, ",".join(str(x) for x in chunks))
    if status != "PASS":
        row_detail = "{}; sim_rc={} ddr_rc={}".format(row_detail, proc.returncode, ddr_rc)
    rows = []
    for chunk in chunks:
        rows.append({
            "status": status,
            "op": "mul_scalar",
            "llama_case": label,
            "chunk_index": chunk,
            "chunk_count": chunk_count,
            "ur_pass": ur_pass,
            "ur_fail": ur_fail,
            "ddr_pass": ddr_pass,
            "ddr_fail": ddr_fail,
            "case_rel": str(multi_rel).replace("multi_inst_stage/", "verification_v2/", 1),
            "multi_case_rel": multi_rel,
            "log": str(log_path),
            "verify_log": str(verify_log),
            "detail": row_detail,
        })
    return rows


def run_qk_scale_rtl(rtl_dir: Path, out_dir: Path, metas: List[Dict[str, object]],
                     lines_per_smc: int, max_chunks: Optional[int],
                     chunk_start: int, chunk_end: Optional[int],
                     resume: bool, pack_chunks: int) -> Path:
    summary_path = out_dir / "qk_scale_rtl_summary.tsv"
    gen = llama_v3.ensure_mul_scalar_gen()
    passed = set()
    if resume and summary_path.exists():
        with summary_path.open("r", encoding="utf-8", errors="replace") as fp:
            reader = csv.DictReader(fp, delimiter="\t")
            for row in reader:
                if row.get("status") == "PASS":
                    try:
                        passed.add((row.get("llama_case", ""), int(row.get("chunk_index", "0"))))
                    except ValueError:
                        pass
    fail = 0
    lock_path = rtl_dir / "data_engine" / "sram" / ".extract_llama_intermediate_cases.lock"
    with llama421.FileLock(lock_path):
        backup = llama421.backup_sram(rtl_dir, out_dir)
        try:
            for meta in metas:
                label = str(meta["label"])
                qk_path = Path(str(meta["qk_score"]))
                scaled_path = Path(str(meta["scaled_score"]))
                scalar_path = Path(str(meta["softmax_scale_bin"]))
                chunk_count = llama_v3.chunk_count_from_bytes(qk_path.stat().st_size, lines_per_smc, 8)
                stop = chunk_count if chunk_end is None else min(chunk_end, chunk_count)
                chunks = list(range(max(0, chunk_start), stop))
                if max_chunks is not None:
                    chunks = chunks[:max_chunks]
                if pack_chunks > 1:
                    todo = [chunk for chunk in chunks if (label, chunk) not in passed]
                    for pack_i in range(0, len(todo), pack_chunks):
                        group = todo[pack_i:pack_i + pack_chunks]
                        multi_abs, multi_rel, _packed_meta = build_packed_mul_scalar_case(
                            rtl_dir, gen, label, qk_path, scalar_path, scaled_path,
                            group, chunk_count, lines_per_smc)
                        detail = "kind=varlen_real_qk_score_scale; dtype=fp32 packed={} qk_shape={}".format(
                            len(group), meta["qk_shape"])
                        rows = run_packed_multistage_case(
                            rtl_dir, multi_abs, multi_rel, out_dir, label, group, chunk_count, detail)
                        for row in rows:
                            llama421.append_summary(summary_path, row)
                        status = rows[0]["status"] if rows else "FAIL"
                        print("[{}] {} packed chunks {}/{}..{}/{}".format(
                            status, label, group[0] + 1, chunk_count, group[-1] + 1, chunk_count), flush=True)
                        if status != "PASS":
                            fail += len(group)
                    continue
                for chunk in chunks:
                    if (label, chunk) in passed:
                        print("[SKIP] {} chunk {}/{}".format(label, chunk + 1, chunk_count), flush=True)
                        continue
                    case_name = "mul_scalar_llama_{}_chunk{:04d}".format(llama421.safe_label(label), chunk)
                    case_abs, _case_rel, max_valid, valid_lines = llama_v3.prepare_mul_scalar_case_from_node(
                        rtl_dir, gen, case_name, qk_path, scalar_path, scaled_path, chunk, lines_per_smc, "fp32")
                    multi_abs, multi_rel = llama421.convert_to_multistage(rtl_dir, case_abs, "mul_scalar")
                    detail = "kind=varlen_real_qk_score_scale; dtype=fp32 max_valid={} valid_lines={}; qk_shape={}".format(
                        max_valid, ",".join(str(v) for v in valid_lines), meta["qk_shape"])
                    row = llama421.run_multistage_case(
                        rtl_dir, multi_abs, multi_rel, out_dir, "mul_scalar", label,
                        chunk, chunk_count, detail)
                    llama421.append_summary(summary_path, row)
                    print("[{}] {} chunk {}/{}".format(row["status"], label, chunk + 1, chunk_count), flush=True)
                    if row["status"] != "PASS":
                        fail += 1
        finally:
            llama421.restore_sram(rtl_dir, backup)
    if fail:
        print("qk scale RTL failures={}".format(fail), flush=True)
    return summary_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", choices=["r1", "r2", "both"], default="r2")
    ap.add_argument("--out-dir", default=str(OUT_DEFAULT))
    ap.add_argument("--rtl-dir", default=str(RTL_DEFAULT))
    ap.add_argument("--run-rtl", action="store_true")
    ap.add_argument("--lines-per-smc", type=int, default=255)
    ap.add_argument("--max-rtl-chunks", type=int, default=None)
    ap.add_argument("--chunk-start", type=int, default=0)
    ap.add_argument("--chunk-end", type=int, default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--pack-chunks", type=int, default=1,
                    help="run multiple QK scale chunks in one multi-stage simv case")
    ap.add_argument("--q-start", type=int, default=0)
    ap.add_argument("--q-count", type=int, default=None)
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rounds = ["r1", "r2"] if args.round == "both" else [args.round]

    varlen_rows = []
    cache_rows = []
    for r in rounds:
        q_start = args.q_start if r == "r1" else 0
        q_count = args.q_count if r == "r1" else None
        varlen_rows.append(extract_varlen(VARLEN[r], out_dir, q_start=q_start, q_count=q_count))
        cache_rows.append(extract_cache(CACHE[r], out_dir))

    append_summary(out_dir / "varlen_intermediate_summary.tsv", varlen_rows)
    append_summary(out_dir / "cache_intermediate_summary.tsv", cache_rows)
    print("out_dir={}".format(out_dir), flush=True)
    print("varlen_summary={}".format(out_dir / "varlen_intermediate_summary.tsv"), flush=True)
    print("cache_summary={}".format(out_dir / "cache_intermediate_summary.tsv"), flush=True)

    if args.run_rtl:
        summary = run_qk_scale_rtl(Path(args.rtl_dir).resolve(), out_dir, varlen_rows,
                                   args.lines_per_smc, args.max_rtl_chunks,
                                   args.chunk_start, args.chunk_end, args.resume,
                                   max(1, args.pack_chunks))
        print("qk_scale_rtl_summary={}".format(summary), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
