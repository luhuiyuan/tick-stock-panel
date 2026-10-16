# P2B-003 源码审计报告

- **任务**：P2B-003 overlay、自定义信号和组合策略增强
- **审计时间**：2026-10-16
- **审计范围**：叠加条件缺列、监控自定义信号选择、组合策略评分一致性
- **目标基线**：`0a68bf46eed4a721c102864979344a1eb658d03f`
- **审计状态**：只读审计完成，未批准代码迁移
- **证据目录**：`/tmp/tick-stock-panel-fork-migration/p2b003-audit-20261016/`

## 1. 结论摘要

P2B-003 不是一个可整批复制的功能组，必须拆成独立增量：

| 子项 | 结论 | 建议状态 |
|---|---|---|
| P2B-003-A：叠加条件缺列与 enriched 历史存储列保留 | 目标基线仍缺少源补丁中的完整列传递/依赖解析闭环；当前已有部分 `turnover_rate` 透传，不能直接套用整份补丁 | 待迁移前确认 |
| P2B-003-B：评分 null 兜底与排序防御 | 目标基线尚未包含源补丁的 `fill_null(0)` 和 `nulls_last` 防御；属于可独立实施的评分一致性修复 | 待迁移前确认 |
| 监控规则显示全部自定义信号 | 多个 Fork 是同一稳定补丁，目标基线已等价实现 | 无需迁移 |
| 组合策略单候选使用中性分 | 多个 Fork 是同一稳定补丁，目标基线已等价实现 | 无需迁移 |
| 指数/ETF 默认截止日使用北京日期 | 与组合候选关联但属于日期/数据契约修复，目标基线已等价实现 | 无需迁移 |

以上“目标基线”判断基于 `git show HEAD`，不把当前工作区未提交修改当作本次迁移成果。

## 2. 固定源码与重复项

本轮复用了仓库外已有的 Fork 快照，并从官方 GitHub 源补取缺失的指定提交对象；没有把源代码下载到目标仓库。提交、父提交、稳定 patch-id、原始 diff、SHA-256 和快照 HEAD 记录在 `manifest.json` 与 `sha256sums.txt`。

### 2.1 叠加条件缺列

- 仓库：`flyme8899/tick-stock-panel`
- 提交：`f4d8e7a48db626b82a58f02334b5537ce3173625`
- 快照 HEAD：`6fb3915a22225ae1e317f6d3e319c8b5a81a184f`
- 稳定 patch-id：`a51fad3ce67c721a3a2f6c1517b9769ab9f543da`
- 改动面：信号 API、回测依赖解析、指标流水线、筛选器、交易标记、涨停基因、策略引擎、K 线仓库、前端条件编辑器和配套测试。

该提交并非单一 UI 修复，核心是让 `turnover_rate`、`consecutive_limit_ups` 等不能由 OHLCV 回推的 enriched 存储列，在重算/历史缓存/矩阵依赖解析路径中继续存在，并使前端数值字段口径可见。目标当前仅在部分路径保留 `turnover_rate`，没有源补丁中的 `enriched_recompute_columns()` 统一入口，也没有完整的连板数保留闭环。

### 2.2 监控自定义信号选择

以下提交经稳定 patch-id 去重为同一补丁 `7abd1c1dcc2d1b28fe62cef1d5b3539c5665d3c2`：

- `OpZ6/tickflow-quantall`：`f5f1e7e413a597a020829175e4a5f84cd3c466d2`
- `jie429/tick-stock-panel`：`f5f1e7e413a597a020829175e4a5f84cd3c466d2`、`e864ecde4b02f38635a3a2b0b39d2dfbaaaa1053`
- `kuku-26/tick-stock-panel`：同上
- `liuwei1989/tick-stock-panel`：`f5f1e7e413a597a020829175e4a5f84cd3c466d2`
- `m01i0ng/tick-stock-panel`：`e864ecde4b02f38635a3a2b0b39d2dfbaaaa1053`
- `xiaohuang-ye/tick-stock-panel`：`e864ecde4b02f38635a3a2b0b39d2dfbaaaa1053`

目标基线的 `SignalPicker` 已有 `options.filterCustomByKind`，默认按 kind 过滤；监控规则页两处传 `false`，并保留自定义信号的编辑标识，语义与源补丁一致。无需迁移。

### 2.3 组合策略评分与日期

单候选中性分提交在多个 Fork 中重复，稳定 patch-id 为 `909fbfcd1cfec4d33224b84f6f9968facdf1906e`：

- `jie429`：`c89b02455576bc7fcf30bc0afbf1d0c526f24405`、`be54e11912c3253a75eb3c2ffbfb00f0a24be966`
- `kuku-26`：同一提交链
- `xiaohuang-ye`：`c89b02455576bc7fcf30bc0afbf1d0c526f24405`
- `m01i0ng`：`be54e11912c3253a75eb3c2ffbfb00f0a24be966`

目标 `backend/app/strategy/composite.py` 已有 `_NEUTRAL_NORM = 0.5`，单候选和无 score 分支均使用中性分，且已有对应回归测试。关联的北京日期提交 `3ccfe1057b93e34198ce6d4f2cf2e4ad581cb9f8` 也已在目标 `market_data.py` 使用 `cn_today()`，无需迁移。

另有未重复的后续修复：

- 仓库：`m01i0ng/tick-stock-panel`
- 提交：`2b1ee41d03ce6eaebf3bef14a4fc7ce7813cd1d9`
- 稳定 patch-id：`6e2f38f1dc69a0d292991367037a27827b019a1f`
- 内容：评分表达式 `fill_null(0)`，并以 `nulls_last=True` 防止缺数据行占据 limit 头部；新增评分回归测试。

目标基线的 `StrategyEngine` 仍直接按 score 排序，未包含该兜底与排序防御，因此建议作为 P2B-003-B 独立最小变更。

## 3. 目标调用链与风险

### P2B-003-A

目标调用链为：

```text
Parquet enriched 存储
 -> KlineRepository 历史窗口/缓存
 -> indicators pipeline 重算
 -> StrategyDependencyResolver / StrategyEngine
 -> overlay_filter / matrix strategy / Screener
 -> API 与前端条件编辑器
```

风险：

- 直接复制整份源提交会混入多个模块和已有本地改动，容易覆盖当前工作区；
- `consecutive_limit_ups` 属于递推列，不能从 OHLCV 安全回推；
- 目标的 ETF、分钟和数据源降级状态仍需保持，不应借此重新启用延后的 P1 数据源；
- 需确认字符串字段、数值比例字段和缺列时 fail-closed 的现有契约不被改变。

### P2B-003-B

目标调用链为：

```text
StrategyEngine._apply_scoring / 结果排序
 -> StrategyResult / 选股 limit
```

风险：

- `null` 的业务语义必须固定为“数据不足，最低分”，不能改变显式 0 分或反向评分；
- 选股排序和回测评分必须保持一致；
- 目标 `backend/app/strategy/engine.py` 有当前工作区未提交修改，实施前必须重新确认基线和采用最小补丁，不能覆盖已有改动。

## 4. 未执行事项

本轮未修改业务代码、未安装依赖、未运行测试或构建、未部署、未重启服务、未提交或推送。只创建审计证据和文档记录。

## 5. 建议的下一步

分别确认以下最小范围之一后再进入代码迁移：

1. **P2B-003-A**：只迁移 enriched 存储列保留、共享依赖解析和缺列回归测试；不迁移无关前端重构、数据源和部署改动。
2. **P2B-003-B**：只迁移评分 null 兜底、`nulls_last` 排序和选股/回测口径回归测试。

每个子项都应独立执行自动验证并进入人工确认，不能因为 P2B-003 的其他簇已存在就标记整组完成。
