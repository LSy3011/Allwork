# ADR-001: Rtovr 物理路由特性

## 1. 硬件变更
新增 Rtovr 物理路由层。Bank 到 IQ 通路非全连接，Port 之间存在互斥组。

## 2. hw_params.yaml 影响
新增 `rtovr` 段: `port_num: 4`, `mutex_groups: [[0,2],[1,3]]`

## 3. 算法影响
- SMT: Z3 决策空间 (T,B) → (T,B,P)，新增公理 5(路由) + 6(互斥)
- HSTS: FootprintMask 新增 Port Mask 维度
- HSTS: Stitcher 新增 `resolve_port_conflict()`

## 4. Roadmap 影响
- M08: 五大公理 → 六大公理 (+2 函数)
- M12: FootprintMask 增加 port 方法
- M14: Stitcher 增加端口冲突消解

## 5. TDD 影响
新增 6 个测试函数:
- M01-F05: `parse_connectivity`
- M01-F06: `validate_rtovr_mutex`
- M08-F05: `add_rtovr_connectivity_axiom`
- M08-F06: `add_rtovr_port_mutex_axiom`
- M12-F02: `FootprintMask.record_port`
- M14-F06: `Stitcher.resolve_port_conflict`
