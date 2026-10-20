"""
softmax 主线 SUM 首证算子（受限的跨迭代 fp32 SUM，lane0-only 全展开串行反馈链）。

依据：docs/softmax_v1_spec_design_20261019.md §3.7「SUM 首证实验契约」（2026-10-19
用户锁定五项边界）+ §3.2 五取证结论（addtree 冻结 TB fp32 不可达 → 串行 FPADD 反馈路径
为「当前已核实连接合法、选作首证的载体」）。

────────────────────────────────────────────────────────────────────────
形态裁定（2026-10-20，发射机制实证后落地，偏差如实登记）：

契约行文假设的是「模调度循环内核 + RtoVR 进位 + 行首路由切换」形态（II=5 候选）。
对现有发射器三个分支逐一核查（assembler.py）后，该形态不可达：

1. ADR-054 flat 分支（VCS 已证绿）：仅限 II=1 单槽 kernel（assembler.py:431）。
   SUM 有反馈 ⇒ II≥2 ⇒ 不可达。
2. embed 主线（HDR+kernel）：多元素 kernel 命中 embed∈{1..6} RTL 死区，
   VCS 实证断流（compiler_mul bank2 先例，assembler.py:413-430 注释链）——
   故意发射已知劣形态 = 浪费 VCS 窗口，排除。
3. ADR-056 unroll 分支（显式 level=0 槽序列）：II≥2 唯一合法形态，但
   (a) kernel 内所有槽均匀重复 R_q 次——「行首不同路由」的 prologue 机制不存在；
   (b) 若改走 UR 往返进位（均匀循环可表达），RecMII≈8-11 × R_q=16 迭代
       ⇒ II·R_q+2 = 130~178 行 > IQ 容量墙 128（smi_top.v:22，G-cap 响亮拒绝）；
       分段器仅在 run_v1_verify 统一入口开放（main_verify 不开放，
       pipeline_runner.py:191-194），本契约第 1 条又明确「暂不进入 V1 正式发布通道」。

故本首证采用**全展开串行链**：L 个 FPADD 各自独立成 op（num_iterations=1），
进位边 = FPADD_D →(RTOVR, sel=4)→ 下一 FPADD_S1，与契约载体（串行 FPADD 反馈
路径）物理同构；「首次路由切换」在本形态下字面成立——行首 add 的 S1 经 VIN1←
零常量字，行内其余 add 的 S1←FPADD_D，是两条不同的 RTOVR 指令，检查器可分别
核验。

⚠ **执行语义订正（2026-10-20 v4，实证）**：ADR-056 展开分支并非「每 op 发射
一次」，而是**模调度帧重复**（assembler.py:546-553：slot-op 在每帧各发射一次，
R_q=逐队列窗口，L=16 时 fpadd 6 帧、写回路径 1 帧）。本形态在帧重复下正确的
论据（单行链）：全部输入经 UR stride=0 重读 + VIN 中继逐帧重放，行首 add 的
S1 来自零常量路由、不依赖 FPADD_D 初值，故逐帧重算值序列相同（幂等）；写回
路径队列 R_q=1 单次发射，采样点在链收敛后（sim：VOUT0 仅 1 次更新，值
bit-exact）。**多行单 schedule 不可行**：两链在帧内交错过写 FPADD_D（单寄存
器），2row sim 实测行0 写回被行1 污染（-65514.0，终值比对检出）——契约
§3.7-5 的两行检查移至 VCS two-stage（段间无复位、寄存器残留真实，行1 零路由
在残留上工作即目标性质；ADR-061 跨段保持先例）。`sum_lane0_2row` 保留为
expected-fail 的污染检出证据。

✔ 证明：编译器后端能生成并正确执行受限的串行 fp32 SUM（冻结语义 s₀=+0，
  s_{i+1}=fp32(s_i+x_i)，RNE）；反馈路由 FPADD_D→FPADD_S1 的稀疏掩码合法路径
  在 VCS 下逐拍闭环。
✘ 不证明（登记为后续实验议程）：模调度循环内核的 loop-carried 重放（II=5
  looped 形态）——其解锁前提 = embed 死区修复 或 unroll 容量/prologue 机制扩展。
────────────────────────────────────────────────────────────────────────

契约条目映射：
- §3.7-1 隔离实验入口：Manual-DFG + experimental_unroll 准入（既有机制，经
  SMT 调度 / rtovr_bypass lowering / ADR-056 unroll 发射 / G-addr·G-cap 产物门），
  不改生产接口、不进 V1 发布通道。
- §3.7-2 L=16 / W=16 / lane0-only：每词 1 有效元素（lane0），lane1-3 恒 +0
  （0+0=+0 RNE 精确保持），输出词全词 bit-exact 比对即覆盖其余 lane 零值承诺。
- §3.7-3 首项初始化（零常量字，host 预置 +0.0）/ 反馈（FPADD_D 链）/ 末次写回
  （每行唯一 UR_WRITE）分开表达；中间 s_i 由 auditor 对 sim 时间线逐 op 定值核验。
- §3.7-4 时序不作假设：cadence 由 SMT 依赖链推出（FPADD 4 + RtoVR 1 ⇒ ≥5），
  验收时逐拍核对「结果何时可用 / 何时被下一次加法采样」。
- §3.7-5 验收矩阵：L=16 正向（sum_lane0，unroll 分支）、L=1（sum_lane0_l1，
  无反馈 ⇒ II=1 ⇒ ADR-054 flat 分支）、三个负控（sum_lane0_neg_seed 错误种子 /
  sum_lane0_neg_wire 反馈误接 / sum_lane0_neg_drop 漏末项）——负控的通过判据
  = 检查器响亮 FAIL；两行连续无全局复位：v4 起移至 VCS two-stage 组合（单
  schedule 形态在模调度帧重复下不可行，见上 ⚠）；sim 侧 sum_lane0_2row
  （L=8×2）保留为跨行污染检出的 expected-fail 证据。
  ⚠ FPADD_D 单寄存器 ⇒ 多行链同 schedule 必互相污染（帧重复下无时段隔离）；
  单行链安全（唯一写者 + 幂等重放）。
  输入固定为符号/抵消/量级混合向量（prepare_input 内常量，确定性可复现）。
"""

import numpy as np
from typing import List, Dict, Any, Optional

from .base import BaseOperator
from ir.mir.builder import MIROpBuilder
from driver.hw_resource import ResourceType as RT, PortSlot as PS


# 契约 §3.7-5：输入须正负混合、抵消、大小量级混合，不得只测简单正整数。
# 两池各 16 个、逐池固定（确定性）；相邻精确对消对（a,-a ⇒ RNE 下精确 +0）、
# 近对消（100.5,-100.25）、2^20 与 2^-10 量级混合、非整数值。
_POOL_ROW0 = [
    1.5, -1.5, 1048576.0, -1048576.0,
    3.25, 0.0009765625, -0.0009765625, -0.25,
    100.5, -100.25, 0.125, -7.75,
    7.5, 1e-6, -1e-6, 42.0,
]
_POOL_ROW1 = [
    -2.75, 5.5, -5.5, 65536.0,
    -65536.0, 0.0078125, -0.0078125, 11.0,
    -3.5, 0.5, -0.5, 1234.5,
    -1234.25, -0.125, 8.0, -1.0,
]
_POOLS = [_POOL_ROW0, _POOL_ROW1]

# UR 布局（§3.6 P0 提案的 SUM 子集）：bank1=x 流、bank2=y 输出（进位词）、
# bank3=常量（零种子）。每行 L 词 x + 1 词零 + 1 词 y。
_BANK_X, _BANK_Y, _BANK_Z = 1, 2, 3


class SumLane0UnrollOperator(BaseOperator):
    """受限跨迭代 fp32 SUM 首证：lane0-only、全展开串行 FPADD 反馈链。

    参数：
        L:          行长（元素数 = UR 词数，lane0-only），1 ≤ L ≤ 16。
        n_lines:    连续行数（同一 schedule 内顺序发射、无全局复位）；
                    ≥2 时第二行首 add 出现第二次零路由切换（行间隔膜检查）。
        neg_ctrl:   负控形态 None | "bad_seed" | "miswire" | "drop_last"。
                    - bad_seed: 零种子字注入 1.0（DFG 不动）→ 检查器必须检出；
                    - miswire:  第 0 行第 miswire_at 个 add 的 S1 误接零常量
                      （丢失部分和）→ 检查器必须检出；
                    - drop_last: 少发最后一个 add（末次写回 = s_{L-1}）→ 检出。
        miswire_at: miswire 的注入点（默认 L//2）。
        name:       算子身份（ADR-056 §B.1 接线闸要求与注册键规范化一致）。
    """

    def __init__(self, L: int = 16, n_lines: int = 1,
                 neg_ctrl: Optional[str] = None, miswire_at: Optional[int] = None,
                 name: str = "sum_lane0", **kwargs):
        if not (1 <= L <= 16):
            raise ValueError(f"L={L} 越出首证包络 [1,16]（输入池按 16 冻结）")
        if not (1 <= n_lines <= 2):
            raise ValueError(f"n_lines={n_lines} 越出首证包络 [1,2]（行池冻结 2 行）")
        if neg_ctrl not in (None, "bad_seed", "miswire", "drop_last"):
            raise ValueError(f"未知负控形态: {neg_ctrl!r}")
        self.L = int(L)
        self.n_lines = int(n_lines)
        self.neg_ctrl = neg_ctrl
        self.miswire_at = int(miswire_at) if miswire_at is not None else self.L // 2
        self.name = name
        # ADR-056 §B.1 准入请求信号：既有机制（算子本体声明），runner 据
        # tensor_configs 物化 UnrollAdmission → assembler 走 unroll 分支
        # （II≥2 唯一 VCS 合法发射形态）。不改准入语义、不动生产接口。
        self.experimental_unroll = True

    # ── BaseOperator 契约 ────────────────────────────────────────────

    @property
    def tensor_configs(self) -> Dict[str, Dict[str, Any]]:
        cfgs: Dict[str, Dict[str, Any]] = {}
        for r in range(self.n_lines):
            for i in range(self.L):
                # 每元素独立张量声明（1 词/元素）：固定地址全展开形态下
                # num_loop_iters=len(arr)//lanes=1 的自然表达，且准入 G-addr
                # 对每个读地址有独立声明区可核。
                cfgs[f"x{r}_{i}"] = {"bank": _BANK_X, "offset": r * self.L + i,
                                     "role": "input", "precision": "fp32",
                                     "length": 1}
        for r in range(self.n_lines):
            cfgs[f"zero_{r}"] = {"bank": _BANK_Z, "offset": r,
                                 "role": "constant", "precision": "fp32",
                                 "length": 1}
        # 输出张量必须命名 "y" 且覆盖全部行：physical_runner.py:194-206 的
        # 终值比对按「张量名 ∈ golden_data.npz 键」过滤，runner 只写
        # golden_res["y"]——命名不符将被静默 skip（2026-10-20 实证：
        # y_0 命名下 neg_wire/neg_drop 终值比对从未执行、假绿）。
        cfgs["y"] = {"bank": _BANK_Y, "offset": 0,
                     "role": "output", "precision": "fp32",
                     "length": self.n_lines,
                     "shape": (4 * self.n_lines,)}
        return cfgs

    def prepare_input(self, n_rows: int) -> Dict[str, np.ndarray]:
        # n_rows 有意忽略：首证输入为冻结的判别性向量（契约 §3.7-5）。
        data: Dict[str, np.ndarray] = {}
        for r in range(self.n_lines):
            pool = _POOLS[r]
            for i in range(self.L):
                data[f"x{r}_{i}"] = np.array(
                    [np.float32(pool[i]), 0.0, 0.0, 0.0], dtype=np.float32)
        return data

    def get_golden(self, input_data: Dict[str, np.ndarray],
                   vector_lanes: int) -> Dict[str, Any]:
        # 冻结语义：s₀=+0，s_{i+1}=fp32(s_i+x_i)，逐元素 RNE（numpy fp32 加即 RNE）。
        # 负控下 golden 恒为正确值——负控的通过判据是检查器检出偏差。
        words = []
        for r in range(self.n_lines):
            s = np.float32(0.0)
            for i in range(self.L):
                s = np.float32(s + np.float32(input_data[f"x{r}_{i}"][0]))
            words.append(np.array([s, 0.0, 0.0, 0.0], dtype=np.float32))
        return {"y": np.concatenate(words), "intermediates": []}

    def get_dfg(self, spec: Any = None) -> List[Any]:
        b = MIROpBuilder()
        prev_row_last_add: Optional[str] = None
        for r in range(self.n_lines):
            prev_add: Optional[str] = None
            n_adds = self.L - 1 if self.neg_ctrl == "drop_last" else self.L
            for i in range(n_adds):
                xr = b.make_op(
                    "UR_READ", f"xr{r}_{i}", RT.UR_READ, [],
                    meta_name=f"x{r}_{i}",
                    logical_addr=_BANK_X * 256 + r * self.L + i,
                    stride=0, role="input", iq_id=30 + (i % 4),
                    precision="fp32")
                rt_x = b.make_op(
                    "RTOVR", f"rtx{r}_{i}", PS.FPADD_S0, [xr], precision="fp32")

                use_seed_route = (
                    prev_add is None
                    or (self.neg_ctrl == "miswire" and r == 0
                        and i == self.miswire_at))
                if use_seed_route:
                    # 行首（或负控误接点）：S1 ← 零常量字（VIN1 绕行由
                    # rtovr_bypass 按 dep 序号 1 自动插入）。
                    # n_lines≥2 时 r>0 行的零读携带对上一行末次加法的依赖边——
                    # 物理事实「FPADD_D 为单寄存器、同时只能承载一条串行链」的
                    # DFG 级表达（anti-overwrite 公理现非活跃，ucode_solver.py:458；
                    # 无此边则求解器可合法交错两链 → 行间 D 覆盖）。
                    seed_deps = ([prev_row_last_add] if prev_row_last_add is not None
                                 else [])
                    zr = b.make_op(
                        "UR_READ", f"zr{r}_{i}", RT.UR_READ, seed_deps,
                        meta_name=f"zero_{r}",
                        logical_addr=_BANK_Z * 256 + r,
                        stride=0, role="constant", iq_id=33,
                        precision="fp32")
                    rt_s1 = b.make_op(
                        "RTOVR", f"rtz{r}_{i}", PS.FPADD_S1, [zr],
                        precision="fp32")
                else:
                    # 反馈边：S1 ← 前一加法的 FPADD_D（sel=4，稀疏掩码
                    # 0x1FDB 内含位，RtoVR.v:243-244 实证）。
                    rt_s1 = b.make_op(
                        "RTOVR", f"rtc{r}_{i}", PS.FPADD_S1, [prev_add],
                        sel=4, precision="fp32")

                prev_add = b.make_op(
                    "FPADD", f"add{r}_{i}", RT.FPADD, [rt_x, rt_s1],
                    iq_id=4, precision="fp32")

            # 末次写回（每行一次）：仅末次加法的结果出链。
            # 中间 s_i 的逐拍审计由 auditor 对时间线的逐 op 定值核验承担
            # （sim 时间线记录每次 fire 的操作数值），不靠 UR 中间写——
            # 逐次写会多 64 节点（16×[rt1+VOUT0+rt2+UW]），z3 在默认预算内
            # 全 UNKNOWN（matmul_q_full 先例：大展开 DFG 需拉长 per-II 超时）。
            rt_wb = b.make_op(
                "RTOVR", f"rtwb{r}", PS.UR_W0_S, [prev_add],
                sel=4, precision="fp32")
            b.make_op(
                "UR_WRITE", f"yw{r}", RT.UR_WRITE, [rt_wb],
                logical_addr=_BANK_Y * 256 + r, stride=0,
                role="output", is_output=True,
                iq_id=35, precision="fp32")
            prev_row_last_add = prev_add

        ops = b.get_dfg()
        # rtovr_deps 自动补全（selu/matmul 同款惯例；bypass pass 会再全量重建）。
        for op in ops:
            if op.op_type.upper() == "RTOVR":
                prods = op.metadata.get("depends_on", [])
                if prods:
                    pid = prods[0]
                    for consumer in ops:
                        if op.trace_id in consumer.metadata.get("depends_on", []):
                            consumer.metadata.setdefault("rtovr_deps", {})[pid] = {
                                "dvr": op.metadata.get("dvr", -1),
                                "sel": op.metadata.get("sel", -1),
                            }
        return ops

    def inject_data(self, program: Any, input_data: Dict[str, np.ndarray],
                    vector_lanes: int, spec: Any = None):
        super().inject_data(program, input_data, vector_lanes, spec)
        # 零种子字（role=constant 不在基类注入面内，selu 同款手注）。
        # bad_seed 负控：注入 1.0 破坏 s₀=+0 冻结语义，检查器必须检出。
        seed_val = 1.0 if self.neg_ctrl == "bad_seed" else 0.0
        for r in range(self.n_lines):
            program.inject_broadcast(_BANK_Z * 256 + r, seed_val)
