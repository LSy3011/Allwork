# 公平对照第二规模预检：255 词/SMC（1020 元素）双方支持性裁定（2026-10-18）

依据：用户 2026-10-18 四步序之 **③ 预检 255 词配置，再决定正式配对运行
——检查每-SMC 1020 元素、地址边界、循环字段、容量及搬运合法性**；
**不为跑 255 词放宽准入条件，任何一侧不支持则记录实际限制并改选共同
支持的第二规模**。

## 裁定：双方均支持 255 词/SMC，无需放宽任何准入条件 → 进入步④

| 预检项 | 手写侧 | 编译器侧 | 证据 |
|---|---|---|---|
| 每-SMC 1020 元素 | ✅ 255 词 × 4 lane = 1020；生成器默认 `input_words=255`（`mul_scalar_lsu_op.h:28`） | ✅ `--n-rows 1020` e2e 全绿（ALL PHYSICAL CHECKS PASSED + Truth Audit SUCCESS，golden y shape (1020,)） | 本日运行；yaml **零改动**（见下注） |
| 地址边界 | ✅ r2 先例 meta 实测：x@byte0（255w=4080B<4096）、α@byte4096（4080B，8176<8192）、y@byte8192（4080B）——无交叠，通道 1M 行内 | ✅ 同一框架（组合程序共用 LOAD/STORE，布局与编译器无关） | r2 case `mul_scalar_8smc_v3_fpga_v422_255w_20260828_111130/meta_v3.json` |
| 循环字段 | ✅ 255w 先例 VCS 实证：fpmul BODY′ 迭代字段 `…010010D00C00`（0x10D=269 vs 64w 0x4E=78，差 191=迭代差）——同字段随规模缩放 | ✅ BODY LOOPS=254 实发（physical_instructions.json `(VLD=1,JUMP=0,LOOPS=254)`）；LOOPS 字段 10bit（`hw_config/iq_config.yaml` 全单元 BITS_LOOPS=10），254≪1023 | 本日 e2e 产物 + r2 txt |
| 容量 | ✅ UR bank 深度 256 词 ≥ 255（余 1 词）；IQ 128 行墙不适用（v3 bulk 恒 2–3 行/队列，r2 实证） | ✅ UR 同左；flat 恒 3 行/队列（n 无关，64w 已实证），IQ 墙不适用 | `hw_params.yaml: PARAM_UR_BANK_DEPTH=256`；64w 拆解文档 §3 |
| 搬运合法性 | ✅ 255w LOAD/STORE 由生成器发，r2 VCS 实证（llama r2 8SMC 40/40 DDR bit-exact） | ✅ 编译器不发 LOAD/STORE（框架提供），无新增合法性面 | r2 先例 |
| α 装载 | ✅ 整载 255 词 | ✅ 整载 255 词（沿用 64w 裁定：**双侧共同整载**——证据库无 1 词 LOAD 描述符先例），保守共同搬运框架，不声称优化 | 64w 设计文档 §4 |

**注（yaml 零改动的实证路径）**：曾试将 `scalar_mul.yaml` length 256→1020
跑 e2e——产物与 64w 逐字节同（LOOPS 仍 63），查明 `length` 语义为
**声明的 bank 内 UR word 静态分配上界**（`pipeline_runner.py:499-527`：
`consumed = stride·(N_eff−1)+1 words`，超出则 TYPE_LAYOUT_UNSUPPORTED
响亮拒绝），**元素数由 `--n-rows` 驱动**（默认 256）。1020 元素消耗
255 words ≤ 声明 256 ✓，故 yaml 保持已提交原样（length=256），
e2e 以 `--n-rows 1020` 运行。length 1020 > bank 物理深度 256 的
过度声明写法已还原，未入库。

## 观测记录（非阻断，供后续拆解）

- 编译器 INIT 行 LOOPS 值集随 n 变化：64w（n_rows=256）时
  {1,2,3,4,5,12}，1020 时 {1,2,3,4,5,9,10,11,12}——INIT 配置与规模
  存在耦合，机制未解码（本预检不断言）；行数恒 3 不变。
- 编译器侧 255w 的 VCS 实证尚不存在（e2e 绿 = sim-vs-golden 自洽，
  ADR-023 口径）；此前 flat 形态 VCS 实证最大规模为 LOOPS=63
  （mul ADR-054 与 scalar_mul b1 均 64w）——**255w 的 VCS 首验即步④
  本身**。
- 步④ 工具准备项：`fair_smul64w_ref_check.py` WORDS=64 硬编码需
  参数化（--words，默认 64 保 64w 归档可复验）。

## 结论

255 词/SMC 为双方共同支持的第二规模：手写侧有 r2 VCS 实证先例，
编译器侧生成链 1020 元素全绿且所有字段静态合法。准入条件零放宽。
按四步序进入 **④ 第二规模配对运行（同一公平条件，先正确性再取数）**。
