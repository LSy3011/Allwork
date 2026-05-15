---
adr_id: ADR-015
title: HardwareSpec pickle 缓存（mtime 校验失效）
decided: 2026-05-15
last_verified: 2026-05-15
still_holds: yes
---

# ADR-015 · HardwareSpec pickle 缓存

## Context

ADR-014 后 horner e2e 11s 实测拆分：27% Python 启动（~3s）/ 65% SMT / 6% sim / 2% 其他。Python 启动里 `HardwareSpec.load("hw_config")` 是大头——读 6 个 yaml 文件 + 解析 + pydantic 校验。

每次 `main_verify.py --op X` 都是新 Python 进程，要重新走一遍。在批量跑算子时（e.g. CI 全 38 个 op），这是 ~38 × 0.5s = 19s 的纯重复开销。

## Decision

**1. HardwareSpec.load 包一层 pickle 缓存**

- 缓存路径：`build/.hw_spec_cache/{sha16(abs_config_dir)}.pkl`
- 缓存内容：`{"mtimes": {file_path: mtime}, "spec": HardwareSpec}`
- mtimes 覆盖：`config_dir/*.yaml` + `config_dir/specs/**/*.yaml`
- 命中条件：所有 mtime 与缓存记录一致
- 失效条件：任一 yaml 文件 mtime 变 / 新增 / 删除 → cache miss → fresh load + 重建 cache
- 异常处理：pickle 反序列化失败、文件读异常等 → fall back 到 fresh load

**2. 环境变量逃生：`AIACC_NO_HW_SPEC_CACHE=1`**

- 设置该环境变量时跳过缓存，强制 fresh load。用于：
  - hw_config 结构性改动调试
  - CI 干净环境验证
  - pickle 格式与 pydantic 版本不兼容时

**3. 原 `load` classmethod 改名为 `_load_fresh`**

外部 API 不变：调用方仍 `HardwareSpec.load(config_dir)`。内部分流到 `_try_load_cached` / `_load_fresh` + `_save_cache`。

## Rejected

- **缓存 ttl 替代 mtime 校验** ⚠
  - why：ttl 不能感知 yaml 编辑；mtime 更精确
- **lru_cache 进程内缓存** ⚠
  - why：解决不了"每次 fresh Python 进程"的实际问题；进程间 share 必须落盘
- **缓存到 system temp（/tmp）** ⚠
  - why：CI 环境 /tmp 可能跨任务清理；项目 `build/` 路径与 .gitignore 一致，便于 user 手动清缓存
- **修 schema 加 version 字段** ⚠
  - why：schema 不动；缓存失效靠 mtime + pickle exception 兜底

## Consequences

- ✅ **冷启动后续 e2e 调用各省 0.3-0.8s**（视 yaml 数量）
- ✅ 缓存命中是确定性的（mtime 精确到秒）
- ✅ 失败模式安全：cache miss/corrupt → fresh load，没破坏面
- ⚠ pickle 格式与 **pydantic 版本绑定**——pydantic 升级时缓存必须刷一遍；exception fallback 保证不崩，但首次跑慢一点
- ⚠ build/.hw_spec_cache/ 占用磁盘（每个 hw_config 约几 KB-几十 KB，可忽略）
- ⚠ 部分文件系统 mtime 精度只到秒：同秒内编辑+缓存会读到 stale；用 `touch <yaml>` 强制 mtime 更新或 `AIACC_NO_HW_SPEC_CACHE=1` 绕过
- 🔒 **binding**：
  - HardwareSpec 字段增减 → 即使 pickle 失败 fallback，**也要清 `build/.hw_spec_cache/` 一次**，避免老缓存的字段集污染新进程
  - 缓存逻辑禁止在 cache miss 时静默 hide error；必须 print warning（user 才能区分"cache miss 是 yaml 改了" vs "cache 永远 miss 因为 bug"）
