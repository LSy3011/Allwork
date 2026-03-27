# 验证审计与守卫系统 (TFD 详细规格)

> 来源：设计全书 §9 + §10 + §12。

## 1. STSA 同余拦截 (§9.1)
$gcd(Sr - Sw, N) \nmid (Ar - Aw - \Phi_w \cdot Sw)$
- 编译期 100% 拦截动态地址冲突导致的读取死锁

## 2. CycleValidator (§9.2)
仿真期间抓取所有 Valid 脉冲，校验 $T_{actual} - T_{scheduled} \equiv 0$

## 3. Mock 审计 JSON (§10.1)
```json
{
  "audit_version": "5.0",
  "kernel": "atan2_composite",
  "intensity": 0.94,
  "hardware_checksum": "0xABC",
  "results": {
    "total_cycles": 142,
    "bank_conflicts_resolved": 8
  }
}
```

## 4. 硬件感知重合成 (§10.2)
1. 监控 hw_config/*.yaml 的 MD5
2. Hash 变动 → 清空 synthesis/cache/
3. 自动重跑 70+ 算子 SMT 合成

## 5. Pass-Level 审计快照 (§12.1)
- `01_graph_fusion.json`
- `04_smt_synthesis.mir`
- `08_addr_coloring.map`

## 6. 相位不变式断言 (§12.2)
- Post-Synthesis: APB 内部延时符合 HardwareSpec
- Post-Stitcher: 同拍并行 IQ 的 Bank ID 无交集
- Post-Compressor: LOOPS 未溢出位宽

## 7. 属性溯源 (§12.3)
所有 IR 节点携带 `trace_id`，LIR 可回溯到 HIR。
