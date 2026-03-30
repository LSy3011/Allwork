# 物理假设登记簿

> 大模型每做一个物理假设，必须登记到此文件。
> 你（硬件架构师）定期审阅，确认或修正每个 ❓ 项。

| # | 假设描述 | 代码位置 | 影响范围 | 状态 |
|:--- |:--- |:--- |:--- |:--- |
| A01 | ur_read_ports=4 是全局共享（非每Bank独立） | axioms.py | bus_width 公理 | ✅已确认 |
| A02 | unit_latency 是操作数就绪→结果可用的拍数 | axioms.py | RAW 约束 | ✅已确认 |
| A03 | 同Bank同拍 R+W 合法 | stitcher.py | 冲突检测 | ❌已修正: 必须读先于写 |
| A04 | mutex_groups 表示不能同拍使用的端口组 | axioms.py | port_mutex 公理 | ✅已确认 |
| A05 | 41-bit 所有 IQ 布局统一（VLD在最高位） | emitter.py | 二进制编码 | ✅已确认 |
| A06 | R-R 冲突处理：同一 Bank 的多个读操作必须串行化 | orchestrator | 延迟补偿 (DLY) | ✅已确认 |
| A07 | R-W 冲突处理：写操作必须在同 Bank 读操作完成后执行 | orchestrator | RAW 顺序守卫 | ✅已确认 |
| A08 | INC 字段仅针对 UR_READ/UR_WRITE 的地址偏移有效 | compactor.py | 地址折叠逻辑 | ✅已确认 |
| A09 | EMBED 字段全 IQ 通用，通过 EMBED_END 位掩码配合嵌套 | packer.py    | 嵌套循环合成 | ✅已确认 |

## 状态说明
- ❓待确认：大模型的假设，需要你确认是否正确
- ✅ 已确认：你确认无误
- ❌ 已修正：假设错误，已按你的修正更新代码
