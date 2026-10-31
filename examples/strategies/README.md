# 示例策略

日线 `matrix_native` 策略模板。

**使用：** 复制到 `data/strategies/custom/` 后，在策略页点「重载」（或调用 `POST /api/strategies/reload`），即出现在策略池；参数可在策略设置中调整。

本目录暂无可安装的示例文件。

> 原 `custom_volume_shrink_pullback.py`（放量缩量回踩：前期放量后缩量回调 5%~15%，KDJ J≤-10，守 MA60，非退潮/冰点）
> 已转为内置种子，随安装自动补到 `data/strategies/custom/`。要看写法或改参数，直接读安装后那份；
> 种子源文件在 `backend/app/strategy/seeds/custom_volume_shrink_pullback.py`。

## 情绪择时

策略文件可声明：

```python
REGIME_PHASES = ["ignite", "rally", "climax", "repair"]
```

阶段 id 取自 `market_phase`：`ice` 冰点 / `ignite` 启动 / `rally` 主升 / `climax` 高潮 / `ebb` 退潮 / `repair` 修复。

- **选股：** 按前一交易日的阶段拦截开仓，结果字段 `regime_blocked` 给出原因；卖出信号不受影响。
- **回测：** 未传 `regime_filter` 时默认使用该声明；传 `{}` 则关闭择时，用于对照。

> 当前仓库尚未迁移 RPS/相对强度因子链（GF-000590-B），因此不提供依赖 RPS 的示例策略；待该能力迁移后再补 `custom_rps_rising.py`。
