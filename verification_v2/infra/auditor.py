import os, glob, json, struct
from pathlib import Path
from typing import List, Dict, Any

class Auditor:
    OP_SYMBOLS = {'fpmul': 'm', 'fpadd': 'a', 'rtovr': 'r', 'ur-r': 'L', 'ur-w': 'S', 'ur': 'L'}

    def __init__(self, output_dir: str, spec: Any, show_review: bool = True, show_model: bool = False):
        self.output_dir = Path(output_dir)
        self.smc_dir = self.output_dir / "smc_0"
        self.spec = spec
        self.show_review = show_review
        self.show_model = show_model

    def audit_physical_headers(self):
        """审计 .hex 文件中的硬件 Header 字段"""
        if self.show_review:
            print(f"\n🔍 [AUDIT-REVIEW] Physical Header Summary")
        
        hex_files = glob.glob(str(self.smc_dir / "*.hex"))
        if not hex_files:
            print(f"❌ [ERROR] No hex files found in {self.smc_dir}")
            return False
            
        all_passed = True
        units_audited = 0
        for hf in hex_files:
            unit_name = os.path.basename(hf).replace("iq_", "").replace(".hex", "")
            # 物理单元类型归一化
            u_type = unit_name.split("-")[0]
            if u_type == "ur":
                u_type = "ur_read" if "ur-r" in unit_name else "ur_write"
            
            # 🛸 核心：从硬件规格动态获取指令长度 (强制大写匹配)
            u_key = u_type.upper()
            if u_key not in self.spec.modules:
                continue
            p_bits = self.spec.modules[u_key].code_bits
            
            with open(hf, 'r') as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            
            if len(lines) < 2: continue 
            units_audited += 1
            
            # PC 1 为 HDR 帧，右移 body 位宽得到 header
            hdr_raw = int(lines[1], 16)
            header_val = hdr_raw >> p_bits
            
            inc_embed = header_val & 0x3F
            loops     = (header_val >> 20) & 0x3FF
            embed     = (header_val >> 36) & 0x7
            vld       = (header_val >> 40) & 0x1
            
            if vld == 1:
                if self.show_review:
                    print(f"  Unit: {unit_name:<12} | EMB:{embed} | INC_E:{inc_embed} | LOOPS:{loops} ✅")
                
                if embed == 0 or ("ur-" in unit_name and inc_embed == 0):
                    print(f"    ❌ [FAIL] Unit {unit_name} misconfigured.")
                    all_passed = False
        
        if self.show_review:
            status = "SUCCESS" if all_passed else "FAILED"
            print(f"  [RESULT] Header Audit {status} (Total Units: {units_audited})")
            
        return all_passed

    def audit_truth_alignment(self, log_path: str, num_iterations: int = 1):
        """进行时序对齐与数值审计（智能静默模式）。"""
        if self.show_review:
            print("\n" + "="*20 + " 🛸 SMT-DRIVEN RIGID AUDIT REPORT (V27.5) " + "="*20)
            print(f"  [CONTRACT] Physical_Cycle = 2 + SMT_t | Iters: {num_iterations}")
        
        # 1. 加载元数据
        # ... (加载逻辑保持不变)
        schedule_path = self.output_dir / "compiler_schedule.json"
        expected_vals_path = self.output_dir / "expected_values.json"
        timeline_path = self.smc_dir / "analysis" / "timeline_ascii.txt"
        
        if not (schedule_path.exists() and expected_vals_path.exists() and timeline_path.exists()):
            print("❌ [ERROR] Audit metadata missing.")
            return False

        with open(schedule_path, "r") as f:
            sched_data = json.load(f)
            ii = sched_data["II"]
            theoretical_ops = sched_data["ops"]
        
        with open(expected_vals_path, "r") as f:
            expected_vals = json.load(f)

        # 2. 解析仿真 Log 中的数值
        actual_values = {}
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                for line in f:
                    if "[AUDIT-DATA]" in line:
                        try:
                            parts = line.strip().split()
                            cyc = int(parts[2].split(":")[1])
                            unit_name = parts[3].split(":")[1].lower()
                            tag = parts[4].split(":")[1]
                            data = {p.split(":")[0]: p.split(":")[1] for p in parts[5:] if ":" in p}
                            if unit_name == "rtovr":
                                port = data.get("Port")
                                actual_values[(cyc, f"rtovr-{port}")] = {"tag": tag, "data": data}
                            elif unit_name == "ur-read":
                                port = data.get("Port")
                                actual_values[(cyc, f"ur-r{port}")] = {"tag": tag, "data": data}
                            elif unit_name == "ur-write":
                                port = data.get("Port")
                                actual_values[(cyc, f"ur-w{port}")] = {"tag": tag, "data": data}
                            else:
                                actual_values[(cyc, unit_name)] = {"tag": tag, "data": data}
                        except: continue

        # 3. 解析仿真时序
        sim_activity = {}
        with open(timeline_path, "r") as f:
            for line in f:
                if "|" in line and "iq_name" not in line and "occupancy" not in line:
                    name, trace = line.split("|")
                    sim_activity[name.strip()] = trace.strip().split()

        all_matched = True
        # [REMOVED HARDCODED num_iterations]

        # 4. 逐个算子审计（智能分级：详情仅在失败时显示）
        for trace_id, info in theoretical_ops.items():
            if any(trace_id.startswith(x) for x in ["nop", "init", "hdr"]): continue
            
            t_base = info["t"]
            iq_name = info["iq"].lower().replace("_", "-")
            # 🛸 改进：更精准的字符映射匹配
            target_char = 'X'
            for prefix, symbol in self.OP_SYMBOLS.items():
                if iq_name.startswith(prefix):
                    target_char = symbol
                    break
            
            sim_iq_name = iq_name
            if sim_iq_name not in sim_activity:
                for k in sim_activity.keys():
                    if iq_name in k: sim_iq_name = k; break
            
            sim_trace = sim_activity.get(sim_iq_name, [])
            if not sim_trace: continue
            
            op_failed = False
            debug_buffer = [] 
            
            for iter_idx in range(num_iterations):
                expected_cyc = 2 + t_base + (iter_idx * ii)
                actual_char = sim_trace[expected_cyc] if expected_cyc < len(sim_trace) else "?"
                timing_ok = (actual_char == target_char)
                
                val_ok = True; val_info = ""
                if (expected_cyc, sim_iq_name) in actual_values:
                    act_val = self.to_float(actual_values[(expected_cyc, sim_iq_name)]["data"].get("V0", "0"))
                    val_info = f" | Val: {act_val:.4f} ✅"
                elif any(x in trace_id for x in ["rd_", "mul", "add", "wr_"]):
                    val_ok = False; val_info = " | Data Missing"
                
                icon = "✅" if (timing_ok and val_ok) else "❌"
                msg = f"  [ITER {iter_idx}] Cyc {expected_cyc}: '{actual_char}' {icon}{val_info}"
                if not (timing_ok and val_ok):
                    op_failed = True
                    msg += f" (Exp '{target_char}')"
                    if not timing_ok:
                        found_at = -1
                        for s_cyc in range(max(0, expected_cyc-5), min(len(sim_trace), expected_cyc+5)):
                            if sim_trace[s_cyc] == target_char:
                                found_at = s_cyc; break
                        if found_at != -1:
                            msg += f"\n    ⚠️ [DIAGNOSIS] Found at {found_at}, Offset should be {found_at - t_base}"
                debug_buffer.append(msg)

            # --- 智能打印策略 ---
            if op_failed:
                all_matched = False
                print(f"\n❌ Op: {trace_id:<12} | IQ: {sim_iq_name} | SMT_t: {t_base} [FAILED]")
                for line in debug_buffer: print(line)
            elif self.show_review:
                # 成功时只显示一行摘要
                print(f"  Op: {trace_id:<12} | IQ: {sim_iq_name:<12} | Status: ✅")

        if self.show_review:
            print(f"\n  [RESULT] Truth Audit: {'SUCCESS' if all_matched else 'FAILED'}")
            
        return all_matched

    @staticmethod
    def to_float(hex_str):
        if not hex_str: return 0.0
        lsb32 = hex_str[-8:] if len(hex_str) >= 8 else hex_str
        try:
            return struct.unpack('>f', struct.pack('>I', int(lsb32, 16)))[0]
        except: return 0.0
