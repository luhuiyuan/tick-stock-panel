# GF-000074 自定义深度数据源源码审计

- 审计日期：2026-10-19
- 源仓库：`kuku-26/tick-stock-panel`
- 固定分支：`main`
- 固定源码 SHA：`72b4d30fbffd9043c160b09368fe70e0eb8dda56`
- 源码目录：`/tmp/tick-stock-panel-fork-migration/kuku-26__tick-stock-panel/72b4d30fbffd9043c160b09368fe70e0eb8dda56`
- 核心提交：`e650137e204526b63bea82765e2e8e8a625b7ca6`、`b5a005a8ac2af998ea6afabe9a9d0e299a64ef59`

## 1. 源功能范围

核心提交为同一内容的重复提交，新增/调整内容包括：

- `ProviderCapabilities.depth5` 与 `MarketDataProvider.get_depth_batch()` 契约；
- 自定义 Provider 声明 `depth5` 数据集并进入能力矩阵；
- `DepthService` 按当前五档 Provider 路由、按能力限制分批、逐批失败隔离，不跨源回退；
- TickFlow Provider 实现 `get_depth_batch()`；
- 自定义源注册发生在能力探测之前；
- 插件文档、能力矩阵和自定义深度 Provider 测试。

关联表中的其他提交不纳入本功能核心迁移：

- `71d4079d...`、`7dd796c4...`：AI `ToolCatalog`，不是深度数据源能力；
- `26022225...`、`49472870...`：全量分钟北京时间窗口修复，属于相邻但独立的数据源修复。

## 2. 目标侧对照

目标仓库已具备等价或更强实现：

- `ProviderCapabilities.depth5`、`MarketDataProvider.get_depth_batch()`；
- 自定义数据集白名单已包含 `depth5`；
- 能力矩阵 `depth5` 可独立配置和展示；
- `tickflow.policy` 将当前 Provider 的 `depth5` 声明映射为 `DEPTH5_BATCH`；
- `DepthService._call_depth_batch()` 已按当前路由选择 TickFlow、内置 Provider 或自定义 Provider；
- 共享批量限制与节流；批次异常隔离；非 dict 返回拒绝；无效自定义契约 fail-closed；不跨源回退；
- `backend/tests/test_custom_depth_provider.py` 已覆盖批量/限速、失败隔离、TickFlow 契约、配置映射和无效配置。

目标当前实现还兼容了项目已有的内置 Provider 注册、竞价与五档能力矩阵扩展，不应被源提交覆盖。

## 3. 审计结论

**GF-000074 无需重复迁移。** 目标侧已存在核心提交要求的深度数据源契约、路由、能力授予、失败降级和测试覆盖。直接复制 Fork 文件会覆盖目标已扩展的 auction/Provider 逻辑，增加冲突和回归风险。

本结论不是对真实第三方深度数据源可用性的确认：当前运行态是否配置了具体 Provider、是否有有效权限和真实五档返回，属于运行配置/数据源验收，不是 GF-000074 源码迁移缺口。

## 4. 台账处理建议

将 GF-000074 标记为 `无需迁移（目标已有等价能力）`，保留源 SHA、核心提交和对照证据；不进入代码实施、依赖安装或人工验收门禁。按登记顺序继续审计 GF-000493。
