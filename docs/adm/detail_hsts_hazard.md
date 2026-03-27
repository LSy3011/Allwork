# HSTS 编排与 UR 冲突消解 (ADM 详细规格 - 3/3)

> 来源：设计全书 §7 + §16。定义相位缝合算法与后端冲突修复。

## 1. APB 时空足迹 (Resource Footprint)
| 维度 | 记录内容 | 冲突判定 |
|:--- |:--- |:--- |
| Bank Mask | (Cycle, Bank, Type) | R-R/W-W |
| Port Mask | (Cycle, Rtovr_Port) | 端口并发互斥 |
| Unit Mask | (Cycle, Module_ID) | 计算单元独占 |

## 2. 缝合算法 (4 步)

### Step 1: 锚点对齐
APB_A 放置在 T=0，足迹写入 AdvancedResTable。

### Step 2: 时空碰撞检测
贪心尝试 $T_B = T_A + \Phi_{min}$，扫描足迹窗口检查：
- Bank 碰撞 (R-R/W-W)
- Unit 碰撞 (同拍同计算实例)

### Step 3: 相位滑动
碰撞 → APB_B 整体 +1 拍，重复 Step 2。

### Step 4: DLY 注入
$DLY_{gap} = T_{final\_B} - T_{scheduled\_A} - 1$
在 LIR 流中补齐气泡。

## 3. 复杂算子三级分流
1. Level 1 (Atomic): SMT 生成小 APB
2. Level 2 (Stitching): HSTS 横向拼接
3. Level 3 (Tiled Loop): 外层 Loop 迭代

## 4. UR Bank 冲突后端消解

### R-R 冲突 (Serialization)
- 触发：同 Bank 同拍两个 UR-R
- 消解：第二个读延迟 +1 拍，下游 DLY 统一 +1
```
冲突前: 周期N: UR-R→A, UR-R→B (非法)
消解后: 周期N: UR-R→A | 周期N+1: UR-R→B
```

### R-W RAW 依赖 (Ordering)
- 触发：写操作不晚于读操作完成
- 消解：$T_{write} \ge T_{read} + latency_{UR\_R}$，不满足则插 NOP

### 冲突消解位置
| 类型 | 检测 | 修复 | 文件 |
|:--- |:--- |:--- |:--- |
| R-R | Stitcher | Stitcher | orchestrator/stitcher.py |
| R-W RAW | SMT/Stitcher | Stitcher | orchestrator/stitcher.py |
| W-W | SMT 公理 B | SMT 强制串行 | synthesis/axioms.py |
