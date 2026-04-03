import os
import struct
import logging
from typing import Dict, List, Any
from smc_simulator.core.base import Component

class IQUnit(Component):
    """
    重构后的 Instruction Queue (IQ) 单元。
    支持物理 Header 解析、地址合成 (Address Synthesis) 和多层嵌套循环 (Nested Loops)。
    """
    def __init__(self, name: str, decoder=None):
        super().__init__(name)
        self.unit = name
        self.logger = logging.getLogger(name)
        self.decoder = decoder
        p_width = self.decoder.payload_width if self.decoder else 0
        self.logger.info(f"[IQ-INIT] Unit: {name}, Payload Width: {p_width}")
        self.memory = []
        self.pc = 0
        self._next_pc = 0
        
        # 内部状态：三级循环计数器 (L0, L1, L2)
        self.loop_states = [{"max": 0, "cnt": 0, "inc": 0} for _ in range(8)]
        self.loop_start_pcs = [0] * 8
        self.dly_counter = 0
        self.delay_active = False
        self._dly_finished_marker = False
        self._current_instruction = 0
        self._instruction_valid = False
        self.exec_active = False
        self.terminated = False

    def reset(self):
        self.pc = 0
        self._next_pc = 0
        self.loop_states = [{"max": 0, "cnt": 0, "inc": 0} for _ in range(8)]
        self.loop_start_pcs = [0] * 8
        self.dly_counter = 0
        self.delay_active = False
        self._dly_finished_marker = False
        self.terminated = False
        self._current_instruction = 0
        self._instruction_valid = False
        self.exec_active = False

    def load_hex(self, file_path: str):
        if not os.path.exists(file_path):
            self.logger.error(f"[IQ:{self.unit}] FAILED to load: file not found: {file_path}")
            return
        with open(file_path, 'r') as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
            self.memory = [int(l, 16) for l in lines]
        if len(self.memory) > 0 and "rtovr-13" in self.unit:
             print(f"[IQ-LOAD-AUDIT] {self.unit} Loaded PC=0 Raw:{hex(self.memory[0])}")
        self.logger.info(f"[IQ:{self.unit}] Successfully loaded {len(self.memory)} instructions from {file_path}")

    def evaluate(self, inputs: Dict[str, Any] = {}, cycle: int = 0):
        if self.pc >= len(self.memory):
            self.terminated = True
            self._instruction_valid = False
            return

        raw_base = self.memory[self.pc]
        stall = inputs.get("STALL", False)

        
        if self.decoder and hasattr(self.decoder, "_layout"):
            off_vld = self.decoder._layout['VLD']['offset']
            off_loops = self.decoder._layout['LOOPS']['offset']
            off_jump = self.decoder._layout['JUMP']['offset']
            off_inc = self.decoder._layout['INC']['offset']
            off_dly = self.decoder._layout['DLY']['offset']
            off_hembed = self.decoder._layout['EMBED_LOOPS']['offset']
            p_width = self.decoder.payload_width
            
            hvld = (raw_base >> off_vld) & 0x1
            loops_val = (raw_base >> off_loops) & 0x3FF
            dly = (raw_base >> off_dly) & 0x7 
            
            is_in_dly = (dly > 0 and not self._dly_finished_marker) or self.delay_active
            
            if hvld == 1 and not is_in_dly:
                hjump = (raw_base >> off_jump) & 0x1
                hembed = (raw_base >> off_hembed) & 0x7
                raw_inc = (raw_base >> off_inc) & 0xF
                current_inc = raw_inc if raw_inc < 8 else raw_inc - 16
                
                if hembed > 0:
                    self.loop_states[hembed]["max"] = loops_val
                    self.loop_states[hembed]["inc"] = current_inc
                    self.loop_states[hembed]["cnt"] = 0
                    self.loop_start_pcs[hembed] = self.pc + 1
                    self._next_pc = self.pc + 1

                    self._instruction_valid = False
                    return

                decoded = self.decoder.decode(raw_base)
                self._current_instruction = self._apply_nested_inc(raw_base, decoded, p_width, current_inc, cycle=cycle)
                
                is_control_frame = (hjump == 1)
                self._instruction_valid = not is_control_frame
                self.exec_active = not hjump
                self.loop_states[0]["inc"] = current_inc
                self.loop_states[0]["max"] = loops_val
            else:
                self._instruction_valid = False
                self.exec_active = False
                hjump = 0; hembed = 0; current_inc = 0
                # 🚀 物理增强：支持 NOP 指令的 LOOPS 扩展，维持气泡占位
                self.loop_states[0]["max"] = loops_val
                self.loop_states[0]["inc"] = 0 # NOP 无增量
        else:
            hvld = 0; dly = 0; loops_val = 0; hjump = 0; hembed = 0
            self._instruction_valid = False

        self._next_pc = self.pc
        if dly > 0 and not self._dly_finished_marker:
            self.delay_active = True
            if self.dly_counter <= 0:
                self.dly_counter = dly - 1
            self.exec_active = False
            return

        if not stall:
            self._step_counters(raw_base, p_width, loops_val)

    def _step_counters(self, raw, p_width, loops_val):
        # 物理修正：当 cnt 达到 loops_val 时，说明本拍是最后一拍。
        # 计数器应在本拍结束后才决定是否回跳或溢出。
        if self.loop_states[0]["cnt"] >= loops_val:
            # L0 溢出
            if self.unit == "ur-w0":
                raw_bin = f"{raw:0128b}"
                b58 = raw_bin[-59]
                print(f"[IQ-BIT-AUDIT] ur-w0 PC={self.pc} b58={b58} Raw:{hex(raw)}")
            
            off_end = self.decoder._layout['EMBED_END']['offset']
            end_mask = (raw >> off_end) & 0x3F
            
            # 🚀 物理回跳审计：揭开 Row 1 失踪之谜

            for i in range(1, 7):
                if (end_mask >> (i-1)) & 0x1:
                    self.loop_states[i]["cnt"] += 1
                    if self.loop_states[i]["cnt"] <= self.loop_states[i]["max"]:

                        # 成功回跳
                        self._next_pc = self.loop_start_pcs[i]
                        print(f"[IQ-LOOP-HIT] {self.unit} PC:{self.pc} -> Next:{self._next_pc} L{i}Cnt:{self.loop_states[i]['cnt']}/{self.loop_states[i]['max']}")
                        for j in range(0, i):
                            self.loop_states[j]["cnt"] = 0
                        self.delay_active = False
                        self._dly_finished_marker = False
                        return 
                    else:
                        # 本级也溢出了
                        self.loop_states[i]["cnt"] = 0
            
            # 无任何级可回跳
            self._next_pc = self.pc + 1
            # print(f"[IQ-LOOP-MISS] {self.unit} PC:{self.pc} -> Next:{self._next_pc} E_Mask:{end_mask}")
            self.loop_states[0]["cnt"] = 0

            self.delay_active = False
            self._dly_finished_marker = False
        else:
            # 脉冲继续
            self.loop_states[0]["cnt"] += 1
            self._next_pc = self.pc

    def _apply_nested_inc(self, raw, decoded, p_width, inst_inc, cycle=0):
        if not self.decoder or not hasattr(self.decoder, "_layout"):
            return raw
        pulse_step = self.loop_states[0]["cnt"]
        l1_step = self.loop_states[1]["cnt"]
        off_inc_embed = self.decoder._layout.get('INC_EMBED', {}).get('offset', p_width + 0)
        raw_inc_embed = (raw >> off_inc_embed) & 0x3F
        if self.unit == "ur-w0":
            print(f"[IQ-DEBUG] {self.unit} Raw: {hex(raw)}, Offset: {off_inc_embed}, RawIncEm: {raw_inc_embed}")

        l1_inc = raw_inc_embed if raw_inc_embed < 32 else raw_inc_embed - 64
        if inst_inc == 0 and l1_inc == 0:
            return raw
        new_payload = raw & ((1 << p_width) - 1)
        for name, info in self.decoder._layout.items():
            if "ADDR" in name.upper():
                offset = info["offset"]
                width = info["spec"].width if info["spec"] else 8
                field_mask = (1 << width) - 1
                
                # 🚀 物理升维：支持全维度嵌套增量 (L0 + L1 + L2 + ...)
                total_phys_inc = pulse_step * inst_inc
                for i in range(1, 8):
                    total_phys_inc += self.loop_states[i]["cnt"] * self.loop_states[i]["inc"]
                
                orig_val = (new_payload >> offset) & field_mask
                new_field_val = (orig_val + total_phys_inc) & field_mask
                
                # 物理探针：监控所有核心数据搬运单元的地址切换

                if self.unit.startswith("ur-") and "ADDR" in name.upper():
                    print(f"[IQ-DIAG] {self.unit} Cyc:{cycle} L0Cnt:{pulse_step} L1Cnt:{l1_step} "
                          f"Base:{orig_val} Inc:{inst_inc} L1Inc:{l1_inc} -> Final:{new_field_val}")
                new_payload = (new_payload & ~(field_mask << offset)) | (new_field_val << offset)
        header_mask = ~((1 << p_width) - 1)
        return (raw & header_mask) | new_payload

    def tick(self):
        if not self.terminated:
            self.pc = self._next_pc
        if self.delay_active:
            if self.dly_counter > 0:
                self.dly_counter -= 1
            else:
                self.delay_active = False
                self._dly_finished_marker = True

    def get_current_instruction(self) -> int: 
        return self._current_instruction if self._instruction_valid else 0
    def is_valid(self) -> bool: 
        return self._instruction_valid
