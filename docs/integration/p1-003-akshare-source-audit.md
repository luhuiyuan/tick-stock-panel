# P1-003-AkShare 源码审计报告

- **任务**：审计并按确认范围实施 AkShare 数据源候选
- **审计时间**：2026-10-16
- **目标分支**：`fork-integration`
- **目标基线**：`6bda5e1d28a3657a2959688da17ba72378bac01d`
- **源仓库**：`https://github.com/CJXkeep/tickflow-stock-panel.git`
- **源分支与快照**：`main@40d4ce0f206345f540edb7d9740636f4dc5b16ed`
- **候选提交**：
  - `f63a364606d3153f7c27ab2b83c31eb49ea258ac`：AkShare 首次接入
  - `b947c28e11b3aa05e7f5d4c0612259cae0f23e9e`：AkShare 回退覆盖修复
- **快照目录**：`/tmp/tick-stock-panel-fork-migration/cjxkeep__tickflow-stock-panel-akshare-20261016/`
- **审计状态**：P1-003-A1、P1-003-A2 均已人工确认

## 1. 结论摘要

AkShare 是真实的数据源新增候选，但不能直接复制源仓库提交：

1. 首次接入提交共修改 132 个文件、约 1.7 万行，混入大量 Trellis 工作流、产品导航和关注范围功能；其中真正与数据源有关的核心文件只有 `backend/app/datasource/`、`backend/app/services/akshare_sync.py`、部分 API/调度、依赖和测试。
2. 目标仓库当前使用 `backend/app/data_providers` + `plugin.yaml` + `ProviderCapabilities` 的插件化契约；源实现使用独立 `backend/app/datasource`、全局 `DATA_PROVIDER=akshare` 和专用同步服务，架构不兼容。
3. 源实现覆盖股票维表、股票日 K、指数维表、指数日 K，以及单股 1 分钟图表回退；不提供目标插件契约下的完整批量 Provider、实时行情、五档、集合竞价、财务或除权因子能力。
4. 日 K 使用 AkShare `adjust="qfq"` 前复权结果，而目标主线将原始价、除权因子和复权计算分层管理，直接接入会改变数据口径。

**建议：将 AkShare 拆为“可选低优先级回退插件/适配器”候选，不作为当前 eltdx 的替代或默认生产源。**

## 2. 源实现核对

### 2.1 数据源能力

| 能力 | 源实现 | 目标兼容性判断 |
|---|---|---|
| 股票维表 | `stock_info_a_code_name()`，另对行业板块逐板块请求成分 | 可映射到 `get_instruments`，但行业映射存在大量请求，需限速/失败隔离 |
| 股票日 K | `stock_zh_a_hist(..., adjust="qfq")`，逐股票请求 | 不能直接进入目标原始日 K 契约；需明确前复权与 `adj_factor` 的口径 |
| 股票分钟 | `stock_zh_a_hist_min_em`，失败后 `stock_zh_a_minute`；仅单股图表回退 | 可作为 `get_minute` 的单股降级，不应宣称全市场分钟同步能力 |
| 指数维表 | 固定七个核心指数 | 可映射，但目标指数维表和数据源路由需独立确认 |
| 指数日 K | `stock_zh_index_daily_em` | 可作为指数日 K 回退，需验证字段、日期和成交量单位 |
| 实时/深度/竞价/财务 | 未提供 | 不能补齐目标 Provider 的完整能力矩阵 |

### 2.2 回退和同步逻辑

- `b947c28e` 在 K 线 API 中增加了 TickFlow 失败后的 AkShare 单股分钟回退。
- 指数同步在 TickFlow 返回空结果时回退到 AkShare，并写入独立指数表。
- 日 K 同步使用线程池按股票并发请求，默认 `akshare_max_workers=8`；这与目标统一能力限流和本机低资源约束不一致，不能原样迁移。
- 本地已有日 K 区间被用于确定增量起点，但源实现没有对目标 `KlineRepository` 的幂等、分区合并和数据新鲜度契约做完整适配。

## 3. 目标代码对照

当前目标仓库已经具备：

- `MarketDataProvider`、`ProviderCapabilities` 和能力路由矩阵；
- 插件清单、插件加载/重载、标准数据集和自定义源映射；
- `daily`、`minute`、`realtime`、`depth5`、`auction`、`financial`、`adj_factor` 等能力的统一路由；
- TickFlow、Fuyao、tdxquant 和已提交的 eltdx 插件。

当前目标仓库已按上述边界接入 AkShare 独立插件；实现位于 `backend/app/plugins/akshare/`，不新增第二套 `backend/app/datasource` 架构，也未全局切换 `DATA_PROVIDER`。

## 4. 主要风险

1. **复权口径风险**：源股票日 K 默认前复权；目标日 K/除权因子分层契约要求先确认原始价、复权价和回测输入口径。
2. **日期口径风险**：源代码多处使用 `date.today()`；目标服务在 UTC 海外主机上统一使用北京自然日工具，必须改为目标日期契约。
3. **接口漂移风险**：AkShare 的 Eastmoney 接口返回列名和可用参数可能变化；必须覆盖缺列、空表、限流、超时和字段类型异常。
4. **并发/资源风险**：源默认八线程逐股票请求，不符合目标统一限流，也可能在 3.5 GiB 内存主机上造成网络并发和转换峰值。
5. **能力误标风险**：单股分钟回退只能支撑图表缺口，不能标记为全市场分钟源；指数七只固定列表也不能替代完整指数维表。
6. **混入无关改造风险**：源首个提交包含大量工作流、产品和前端文件，整提交迁移会扩大范围并覆盖当前目标已有改造。

## 5. 建议拆分的后续实施项

### P1-003-A1：AkShare 股票/指数日 K 最小回退适配器

- 仅实现目标 Provider 契约中的股票/指数日 K 和必要维表；
- 明确 raw/qfq/adj_factor 口径，默认不改变当前生产数据源；
- 复用目标能力矩阵、限流、错误降级、Parquet 幂等写入和北京时间日期工具；
- 增加字段映射、空表、超时、限流、日期边界和前复权口径测试。

### P1-003-A2：AkShare 单股分钟图表回退

- 仅在现有分钟源无数据时为单股图表提供降级；
- 不接入全市场分钟同步，不写入全量分钟数据集；
- 仅支持股票、1 分钟和单标的请求；指数、ETF、多标的和 5/15/30/60 分钟请求返回空表；
- 主接口失败或空结果时，按源实现回退到 `stock_zh_a_minute`；
- 统一输出 `symbol/datetime/open/high/low/close/volume/amount`，`datetime` 为北京时间墙钟无时区值；
- 增加来源标识、失败隔离和数据时间窗口测试。

### 不建议当前迁移

- 源仓库的全局 `DATA_PROVIDER=akshare` 切换；
- 源仓库 Trellis/产品导航/关注范围文件；
- 源默认八线程同步和行业板块 N+1 请求；
- 将 AkShare 宣称为实时、深度、竞价、财务或除权因子完整源。

## 6. P1-003-A1 实施结果

已按确认范围新增目标插件：

- `backend/app/plugins/akshare/provider.py`
- `backend/app/plugins/akshare/plugin.yaml`
- `backend/app/plugins/akshare/requirements.txt`
- `backend/tests/test_akshare_provider.py`

实现边界：

- A1 仅声明并实现 `daily` 数据集；
- 股票和核心指数日 K 使用 `adjust=""`；
- A1 不声明或实现实时、分钟、深度、竞价、财务、除权因子；A2 另行增加单股 `minute`；
- 不启用全局数据源切换；
- 依赖采用延迟导入，未安装时插件在设置页显示不可用，不影响主流程；
- 当前后端虚拟环境已有 AkShare `1.19.1`，本轮未再次安装依赖；
- A1 章节仅记录当时的自动契约验证；A2 章节另有真实分钟网络读取记录。


## 7. P1-003-A2 实施结果

已在现有插件契约内新增单股分钟回退：

- `backend/app/plugins/akshare/provider.py` 增加 `get_minute`；
- `backend/app/plugins/akshare/plugin.yaml` 声明 `minute`，不声明 `full_minute`；
- 股票分钟请求固定调用 `stock_zh_a_hist_min_em(period="1", adjust="")`；
- 主接口失败或空结果时回退 `stock_zh_a_minute(period="1", adjust="")`；
- 仅接受单只股票的 `1m`/`1` 请求，不支持指数、ETF、多标的和全市场分钟同步；
- 返回标准分钟字段并执行北京时间墙钟、时间窗口、数值类型、缺列和空数据校验；
- 不写入 Parquet，不切换默认数据源，不实现 `full_minute`。

## 8. P1-003-A2 验证结果

- `py_compile` 通过；
- Provider 与能力矩阵定向测试：`25 passed`；
- 主接口失败回退测试通过；
- 真实读取 `000001.SZ`、`2026-10-16 09:25` 至 `15:05`：主接口出现 `RemoteDisconnected`，旧接口成功返回 `238` 行；
- 真实结果字段为 `symbol/datetime/open/high/low/close/volume/amount`，时间类型为无时区 `Datetime(us)`，首条 `09:31`，末条 `15:00`；
- 真实请求只读，不写入 Parquet、不修改数据源偏好。

结论：A2 的最小单股分钟回退链路已实现并通过自动验证；上游主接口仍存在连接不稳定，旧接口回退可用。用户已确认前端分钟 K 能力可见，接受本次受控范围。

## 9. 真实网络读取验证

- 股票 `000001.SZ`，窗口 `2026-10-01` 至 `2026-10-16`：成功返回 7 行，字段为 `symbol/date/open/high/low/close/volume/amount`，实际日期 `2026-10-08` 至 `2026-10-16`，无空值异常。
- 指数 `000001.SH`，同一窗口：连续 2 次请求均被远端以 `RemoteDisconnected` 主动断开，Provider 按 fail-closed 返回空表。
- 本次验证未写入 Parquet、未修改数据源配置、未切换默认源。

结论：股票日K真实链路通过；指数日K真实链路受 AkShare 上游接口连接异常阻塞，不能宣称 A1 全部真实验证通过。

## 10. 指数接口诊断与修复结果

- 原实现调用 `stock_zh_index_daily_em(symbol=...)`；在当前 AkShare 1.19.1 环境中，该接口对 `sh000001`、`csi000001` 均出现 `RemoteDisconnected`。
- 对照验证发现 `stock_zh_index_daily(symbol=...)` 可用，且对七个核心指数均成功返回历史数据，最新数据到 `2026-10-16`。
- Provider 已将指数分支切换为 `stock_zh_index_daily`；该接口无日期参数，因此先拉取历史再按目标窗口本地过滤。由于旧接口不返回 `amount`，指数结果显式保留 nullable `amount` 列。
- 修复后真实读取 `000001.SH` 在 `2026-10-01` 至 `2026-10-16` 返回 7 行，日期 `2026-10-08` 至 `2026-10-16`。
- 自动验证：`py_compile` 通过；AkShare Provider 与能力矩阵共 `19 passed`；仅有既有 Polars 日期 cast 弃用警告。

结论：股票和核心指数日K真实读取均已通过；指数之前的失败是 Eastmoney/新版接口路径问题，不是符号映射或 AkShare 全局不可用。

## 11. 本轮未执行事项

- 未安装 AkShare（后端虚拟环境已有 `1.19.1`）；
- 未运行全量构建或全量测试；
- 未部署、重启或切换默认数据源；
- 未写入行情数据、未提交或推送。

A1、A2 均已完成人工确认；不实施 `full_minute`，不切换默认数据源。
