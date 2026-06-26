#!/usr/bin/env python3
"""VCD parser（方案 C 时序 diff PoC）—— 与具体信号无关的通用解析层。

职责单一：把一个 VCS-dump 的 VCD（经 vpd2vcd 转纯文本）解析成
  ① 信号表：全层次名 -> (identifier_code, width)
  ② 选定信号的值变化事件流：[(time_ps, value_str), ...]

不做任何"有效拍"语义判断（那是上层 timing_diff 的事）。VCD 体量大
（unit_fpadd 全 +all dump = 96 万行），所以提供"先按 identifier code 过滤、
只对关心的信号收集值变化"的流式接口，避免把整文件读进内存。

VCD 值格式（IEEE 1364）：
  标量：`0!`  `1"`  `x#`  `z$`         —— 值字符紧贴 identifier code（无空格）
  向量：`b1010 !`  `bx "`              —— b/B 前缀 + 二进制串 + 空格 + code
  实数：`r3.14 !`                       —— 本设计无浮点信号走 VCD，忽略
时间戳行：`#1234`
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class VcdSignal:
    code: str            # VCD identifier code（值变化区用它指代信号）
    name: str            # 叶子信号名（含位宽后缀如 [127:0] 已剥离）
    width: int
    scope: str           # 点分全层次路径，不含叶子名
    msb: int | None = None
    lsb: int | None = None

    @property
    def full(self) -> str:
        return f"{self.scope}.{self.name}" if self.scope else self.name


@dataclass
class VcdHeader:
    timescale: str = ""
    # identifier code -> VcdSignal（同一 code 可能被多个层次别名共享，这里留首个）
    by_code: dict[str, VcdSignal] = field(default_factory=dict)
    # 全层次名 -> code（多个名可映到同一 code）
    by_full: dict[str, str] = field(default_factory=dict)
    all_signals: list[VcdSignal] = field(default_factory=list)


_VAR_RE = re.compile(
    r"^\$var\s+\w+\s+(\d+)\s+(\S+)\s+(\S+)\s*(\[[\d:]+\])?\s*\$end"
)


def parse_header(vcd_path: str) -> VcdHeader:
    """只读到 $enddefinitions 为止，构建信号表。"""
    hdr = VcdHeader()
    scope_stack: list[str] = []
    with open(vcd_path, "r", errors="replace") as f:
        in_timescale = False
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("$enddefinitions"):
                break
            if s.startswith("$timescale"):
                in_timescale = True
                continue
            if in_timescale:
                if s.startswith("$end"):
                    in_timescale = False
                else:
                    hdr.timescale = s
                continue
            if s.startswith("$scope"):
                parts = s.split()
                # $scope module <name> $end
                if len(parts) >= 3:
                    scope_stack.append(parts[2])
                continue
            if s.startswith("$upscope"):
                if scope_stack:
                    scope_stack.pop()
                continue
            if s.startswith("$var"):
                m = _VAR_RE.match(s)
                if not m:
                    continue
                width = int(m.group(1))
                code = m.group(2)
                name = m.group(3)
                rng = m.group(4)
                msb = lsb = None
                if rng:
                    nums = rng.strip("[]").split(":")
                    msb = int(nums[0])
                    lsb = int(nums[-1])
                sig = VcdSignal(
                    code=code, name=name, width=width,
                    scope=".".join(scope_stack), msb=msb, lsb=lsb,
                )
                hdr.all_signals.append(sig)
                hdr.by_code.setdefault(code, sig)
                hdr.by_full[sig.full] = code
    return hdr


def find_signals(hdr: VcdHeader, pattern: str) -> list[VcdSignal]:
    """按正则在全层次名上匹配信号。"""
    rx = re.compile(pattern)
    return [s for s in hdr.all_signals if rx.search(s.full)]


def stream_changes(vcd_path: str, codes: set[str]):
    """流式产出 (time_ps, code, value_str)，仅限 codes 集合内的信号。

    value_str：标量为 '0'/'1'/'x'/'z'；向量为去掉 b 前缀的二进制串。
    """
    cur_t = 0
    seen_defs = False
    with open(vcd_path, "r", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if not seen_defs:
                if s.startswith("$enddefinitions"):
                    seen_defs = True
                continue
            c0 = s[0]
            if c0 == "#":
                cur_t = int(s[1:])
            elif c0 in "01xzXZ":
                code = s[1:]
                if code in codes:
                    yield cur_t, code, s[0].lower()
            elif c0 in "bB":
                # b<bits> <code>
                sp = s.split()
                if len(sp) == 2 and sp[1] in codes:
                    yield cur_t, sp[1], sp[0][1:]
            elif c0 in "rR":
                sp = s.split()
                if len(sp) == 2 and sp[1] in codes:
                    yield cur_t, sp[1], sp[0][1:]
            # $dumpvars/$end 等指令行忽略


if __name__ == "__main__":
    import sys
    vcd = sys.argv[1]
    hdr = parse_header(vcd)
    print(f"timescale={hdr.timescale}  signals={len(hdr.all_signals)}  unique_codes={len(hdr.by_code)}")
    if len(sys.argv) > 2:
        pat = sys.argv[2]
        hits = find_signals(hdr, pat)
        print(f"--- 匹配 /{pat}/ 的信号 {len(hits)} 个（前 40）---")
        for s in hits[:40]:
            print(f"  [{s.width:>3}b] {s.full}   (code={s.code})")
