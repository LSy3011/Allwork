# Phase 1: 硬件契约层 (M01-M02)

> 预计 1-2 天。目标：HardwareSpec 成为全项目唯一真相源。

## M01: 硬件配置文件解析
**要实现的函数**：
- `HardwareSpec.load(hw_config_dir: str)` — 五源 YAML 解析
- `_load_iq_configs()` — 解析 iq_config.yaml
- `_load_modules()` — 解析 modules.yaml
- `_load_connectivity()` — 加载 connectivity.xlsx
- `parse_connectivity(path) -> ConnectivityGraph` [ADR-001]
**验收**：
```bash
pytest tests/unit/test_hw_spec.py::test_load_all_sources -v
```
**通过**：`spec.unit_latency["FPADD"] == 3`

## M02: HardwareSpec 校验
**要实现的函数**：
- `@validator("unit_latency")` — 延时不超 bits_dly 上限
- `validate_connectivity_matrix()` — 连接矩阵维度合法
- `validate_rtovr_mutex(spec) -> None` [ADR-001]
**验收**：
```bash
pytest tests/unit/test_hw_spec.py::test_invalid_dly_exceeds_bits -v
pytest tests/unit/test_hw_spec.py::test_missing_module_raises -v
```
**通过**：非法 YAML 抛出 `ValidationError`

---
*门禁：M02 全绿才进 Phase 2*
