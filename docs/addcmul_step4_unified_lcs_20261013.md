# addcmulu 步④统一 LCS 集成报告（2026-10-12 运行 / 2026-10-13 文档）

> 用户令五步④：统一 LCS 集成（ADR-061 单入口 LOAD→COMPUTE→STORE 程序链）。
> 对象 = `addcmulu`。核心问题：horner 步④公式化修复后的 C-H 独立参考对
> 第二个复杂公式是否**零规则改动**通过。
>
> 证据分级：【实测】= 当日真实运行；【源码】；【归档】。
> 运行时间 2026-10-12 09:10Z；文档与提交 2026-10-13（当日工具中断顺延，
> 运行证据全部落盘未动）。

## 0. 结论

**步④通过，零规则改动**：n=8（单段）与 n=160（分段）统一 LCS 链**双双
首轮 GREEN + published**——`x + t1*t2*value` 被公式驱动 C-H 参考直接消费
（program_manifest 记录 formula 原文），未触发任何失败形态，立法序（记录
形态→鉴别测试→修规则→回归）未启动。**horner 步④的 C-H 通用化修复对第二
复杂公式的适用性获得首个正面实证。**

| run_id | n | 程序段 | outcome | published | 代码态 |
|---|---|---|---|---|---|
| acu_step4_ulcs_n8_20261012 | 8 | LOAD+COMPUTE+STORE | GREEN | True | 74cf64b 干净树 |
| acu_step4_ulcs_n160_20261012 | 160 | LOAD+COMPUTE×2+STORE | GREEN | True | 74cf64b 干净树 |

**附随方法学观察（不阻断）**：两个统一链进程发射了与 V1 链不同的合法
放置变体（17 队列、ur-r2 span=4 束缚），分段边界随之取 m_max=38（38+2），
与步③ V1 链的 39+1 不同——分段边界是**求解实例相关量**，两实例各自
自洽且全部检查通过（§3）。**对 n=160 不得宣称唯一的"the"分段形态**；
正确性判据（C1/C2/C3、bit-exact 比对）是实例不变的。

## 1. 程序段时序与比对

n=8（三段）【实测】：

| 段 | begin | ack | 内容 |
|---|---|---|---|
| LOAD（stage_0） | 12 | 494 | 六张量 H2D（x/t1/t2/value/constants + y 窗） |
| COMPUTE（stage_1） | 494 | 3909 | 17/17 队列逐拍 ok；写 16（2 行×8 SMC）零非法 |
| STORE（stage_2） | 3909 | 4107 | D2H 完成；compare GREEN |

n=160（四段）【实测】：

| 段 | begin | ack | 内容 |
|---|---|---|---|
| LOAD（stage_0） | 12 | 1058 | 六张量 H2D（40 行/张量，manifest hbm_layout 全录） |
| COMPUTE seg0（stage_1） | 1058 | 34077 | 17/17 队列 ok；写 304（38 行×8 SMC）零非法 |
| COMPUTE seg1（stage_2） | 34077 | 37492 | 17/17 队列 ok；写 16（2 行×8 SMC）零非法 |
| STORE（stage_3） | 37492 | 37870 | D2H 完成；compare GREEN |

两规模 `unified_lcs_compare.json` 均 verdict=GREEN；段间 C-L/C2 的
「同拍/空窗系 TB 构造性产物」诚实注记照录（ADR-057 errata-2026-10-01 /
ADR-061 C-L 裁定口径），次序由「首读 > 前置 release 最大值」与
「首读 > LOAD 末写」硬断言承载。

## 2. 零规则改动的证据面

1. program_manifest（adr061_program_manifest_v1）记录 op=addcmulu、
   formula=`x + t1 * t2 * value`、n_lines=2/40——公式被通用机制消费，
   无名称特判分支介入【实测】。
2. 两运行首轮即 GREEN：无 RED 留档、无修复提交、无发布冲突/许可证事件
   （对比 horner 步④的 C-H 修复轮——本案例直接受益）。
3. git 工作树全程干净（74cf64b）：运行前后零生产代码/零规则文件改动；
   本步提交 = 本文档 + progress_log。
4. 回归触发条件未达（用户立法：改通用规则才回归 add/scale_sum）——
   本轮无规则变更，horner 步④的回归结论（bd2ed7b 后 add/scale_sum
   全 GREEN）继续有效。

## 3. 放置变体观察（实例 C）与分段边界实例相关性

- **实例 C（本步两进程一致）**：17 队列集（rtovr-37/ur-r1/ur-r2/ur-r3/
  ur-w2；无 rtovr-35/ur-r0/ur-w0）；束缚队列 **ur-r2 span=4** →
  分段器 m_max=38 → n=160 分 38+2。
- 对照实例 A（V1 链，~15 进程）：18 队列集、全 span≤3 → m_max=39 →
  步③ n=160 分 39+1。实例 B（步② 08:11 单进程）：17 队列另一形态。
- **自洽性核验**【实测】：n=160 stage_manifest 记录 queue_spans（ur-r2=4、
  rtovr-14/vin0=3）与发射产物逐行吻合——COMPUTE seg0（m=38）
  ur-r2=**128 恰顶格**、rtovr-14/vin0=125；seg1（m=2）ur-r2=20、
  rtovr-14/vin0=17，全部满足 L_q=3·(m+span_q)+2。
- 判读：放置选择与代码路径相关（统一链两进程同形、V1 链以实例 A 为主，
  与 VARIANCE.md S1≠S2「代码路径影响 tie-breaking」一致）叠加偶发进程
  漂移（实例 B）。**容量校准值 n_max=156 属 V1 链实测**；统一链的分段
  边界随实例在 {38,39} 间取值，两侧均经全链验证——论文表述应写
  「容量墙 128 行/段是硬件常数；单段可容纳迭代数随求解实例在 38–39
  间取值，两值均已 VCS 实测」。
- 注册表 v3 开放问题清单追加：分段决策的实例相关性与公式冻结的关系
  （分段是发射后处理，逐拍公式在两实例各自成立——本步为一例）。

## 4. provenance

两运行 run_report【实测】：git_head=74cf64b1、dirty=false（提交态）；
op_yaml_sha256=a66aa9da…8239 == 提交 c0ba5f3 的 addcmulu.yaml；
TB 3324a33b…f7c5、注册表 v2 973861aa… 与步②③同锚。发布包
releases/addcmulu_acu_step4_ulcs_n{8,160}_20261012。

## 5. 步⑤入口条件

1. 五步台账汇总（①静态②小规模③容量/分段④统一链）；
2. 覆盖矩阵第一档（已测案例）追加 addcmulu 行：II=3 双槽、三流式输入 +
   驻留标量、容量 n_max=156（V1 链 VCS 校准）/统一链分段 m_max=38–39
   实例相关、统一 LCS n8/n160 GREEN；
3. 第二档（已证形态）新增：三因子乘积配置级文本对齐法、放置漂移三实例
   登记与「正确性判据实例不变」口径；
4. 开放项沿用 horner 步⑤框架：注册表 v3 独立论证（含新增分段实例相关
   性问题）、E1–E5、上板包、条件复跑条款。
