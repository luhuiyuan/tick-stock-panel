# GF-000610 源码审计：TimChey 六/七个选股策略与行情查询

- 审计日期：2026-10-20
- 源仓库：`TimChey/tickflow-stock-panel`
- 固定分支：`main`
- 固定源码 SHA：`b2236188ba7e6687e5c58ae6e2da336f5bbd9115`
- 源码目录：`/tmp/tick-stock-panel-fork-migration/TimChey__tickflow-stock-panel`
- 目标仓库：`/home/tick-stock-panel`
- 审计方式：仓库外固定源码、提交正文/文件清单、策略实现、执行器、行情脚本与目标调用链只读对照

## 1. 提交范围与命名矛盾

固定提交标题为：

```text
feat: 添加单阳不破、大盘底部反弹等6个选股策略及行情查询功能
```

但提交正文实际列出 **7 个新增策略**：

1. 单阳不破
2. 大盘底部反弹
3. 突破后中期整理
4. 强势整理加速回调
5. 强势急涨回调
6. 大盘暴跌错杀低吸
7. 放量双峰缩量企稳低吸

因此不能按“6 个”标题直接裁剪。实现阶段应以固定提交中的源码和正文为准，并在实施前确认是否按 7 个策略整体迁移。

该提交还同时包含：

- `panic_bottom_reversal` 成交额阈值调整；
- `stock_care` 股票池更新；
- `strategy_lab/runner.py` 返回结果改为 dict；
- `strategy_lab/run.py` 增加腾讯公开行情附加查询；
- 删除 `ma_convergence`、`breakout_mid_consolidation`；
- 新增三星电气趋势分析脚本。

这些内容不能自动视为 GF-000610 的策略迁移范围。

## 2. 策略源码清单

### 2.1 按提交正文纳入候选的 7 个策略

| 源文件 | ID | 名称 | 执行后端 | 回看窗口 | 关键字段/逻辑 |
|---|---|---|---|---:|---|
| `single_yang_holds.py` | `lab_single_yang_holds` | 单阳不破 | `python_history_legacy` | 20 | 涨幅达到阈值的大阳线，距今超过最小交易日数，期间收盘不跌破阳线开盘价；量比/动量评分 |
| `dapandibufantan.py` | `lab_dapandibufantan` | 大盘底部反弹 | `python_history_legacy` | 20 | 近 10 日成交额、恐慌跌幅、20 日底部偏离度、流通市值过滤 |
| `strong_breakout_consolidation.py` | `lab_strong_breakout_consolidation` | 强势突破后整理 | `python_history_legacy` | 15 | 近 15 日强势上涨，近 10 日放量且窄幅整理，流通市值过滤 |
| `surge_accel_pullback.py` | `lab_surge_accel_pullback` | 强势整理加速回调 | `python_history_legacy` | 15 | 单日大涨、近 10 日整理、近 5 日累计下跌且末日最深、成交额过滤 |
| `surge_bias_pullback.py` | `lab_surge_bias_pullback` | 强势急涨回调 | `python_history_legacy` | 13 | 单日急涨、近 10 日成交额、BIAS1 超跌、接近布林下轨、流通市值过滤 |
| `crash_day_dip.py` | `lab_crash_day_dip` | 大盘暴跌错杀低吸 | `python_history_legacy` | 21 | 以 as_of 日全市场涨幅中位数识别大盘暴跌，筛选相对大盘更深的前期强势股 |
| `volume_peak_pullback.py` | `lab_volume_peak_pullback` | 放量双峰缩量企稳低吸 | `python_history_legacy` | 15 | 量峰/价峰同在近 10 日、放量、回撤、缩量、跌破 MA20 后末日止跌 |

### 2.2 不自动纳入本批的源文件

源快照中还存在以下策略文件，但固定提交正文没有把它们列为本次 7 个新增策略：

- `consecutive_drop.py`
- `strong_consolidation.py`
- `panic_bottom_reversal.py`
- `volume_golden_cross.py`
- `volume_ma_breakout.py`

其中 `panic_bottom_reversal.py` 是正文明确提到“阈值调整”的旧策略，不应与新增策略重复迁移；其余文件需要先通过提交历史或上游原始 diff 证明属于本提交新增，不能仅因出现在快照中就纳入。

## 3. 源执行器与行情查询边界

Fork 使用 `strategy_lab/runner.py`：

```text
DataStore -> KlineRepository -> ScreenerService -> StrategyEngine
```

执行器加载：

- 目标后端内置策略目录；
- `data/strategies/custom`、`ai`、`composite`；
- Fork 的 `strategy_lab/strategies` 目录。

策略通过 `META`、`filter_history(df, params)`、`LOOKBACK_DAYS`、`REQUIRED_FEATURES` 和 `BASIC_FILTER` 接入 `python_history_legacy` 执行后端。其结构与目标 `backend/app/strategy/engine.py` 已有的历史窗口策略契约相匹配。

Fork 的 `run.py` 不是后端 API 或前端页面：

- 运行策略文件并打印结果；
- 将 `600519.SH` 转成 `sh600519`；
- 调用腾讯公开接口 `https://qt.gtimg.cn/q=`；
- 只在终端附加展示实时行情，不落盘、不进入 Provider 标准化链路；
- 行情失败不影响选股结果。

因此“行情查询功能”是 CLI 附加展示，不是新的标准数据源迁移，也没有对应前端页面。

## 4. 目标仓库对照

目标已有：

- `python_history_legacy` 执行后端；
- `filter_history()` 加载和历史窗口上下文；
- `LOOKBACK_DAYS`、`REQUIRED_FEATURES` 和 `BASIC_FILTER` 读取；
- 策略注册、运行、批量运行、来源标识和 `/api/strategy`、`/api/screener` API；
- 日线 enriched 数据及 `ma20`、`boll_lower`、`change_pct`、成交额等字段；
- `quote_service`、`/api/intraday` 和既有实时行情能力；
- `backend/scripts/stock_care_monitor.py` 的腾讯行情脚本，但其用途是独立股票池监控，不应直接扩大为策略 API 的隐式数据源。

目标没有发现与上述 7 个 `lab_*` ID 完全等价的内置策略。目标已有策略数量较多，但名称相近不代表公式等价，不能用“已有类似策略”替代源码迁移。

## 5. 目标差异和迁移风险

1. **历史窗口语义**：源策略直接按每只股票排序后 `group_by().agg(tail())`，必须确认目标历史上下文的日期裁剪、缺日处理和 as_of 语义一致。
2. **字段与单位**：源策略使用 `change_pct` 小数、成交额元、流通股本/市值过滤和 `boll_lower`；实施前必须按目标字段契约逐项核对，不能凭名称映射。
3. **BASIC_FILTER**：源策略含价格、ST、新股、板块和流通市值条件；目标引擎已有基础过滤，但默认值、北交所覆盖和新股日期口径需逐项对照。
4. **策略参数与评分**：源策略将内部评分字段写入结果并按评分排序。目标引擎有统一评分逻辑，实施时必须先验证同名评分字段、缺失字段和排序方向，否则不能直接复制。
5. **股票池更新/旧策略删除**：属于源仓库其他改动，不应随策略迁移自动执行。
6. **腾讯实时行情**：属于源 CLI 的非标准附加展示；目标已有 Provider/quote_service，不能未经确认引入新的外部行情旁路。
7. **测试覆盖**：固定提交中未发现针对这 7 个 `lab_*` 策略的专用测试文件或按策略 ID 的回归测试；迁移时必须补充目标侧契约测试，但测试数据和断言应以源策略公式为依据，不自行改公式。

## 6. 审计结论

- GF-000610 的核心是 **7 个历史窗口选股策略**，不是一个新的 API/前端页面，也不是腾讯数据源接入。
- 目标已有执行框架和主要指标字段，适合做“源策略文件到目标策略目录”的最小适配；不能整仓复制 Fork 的 `strategy_lab`。
- “6 个”与正文“7 个”存在冲突，实施前需确认按 7 个策略迁移，或明确排除一个。
- 本审计不授权代码修改、数据修复、部署或重启。

## 7. 实施方案（已确认并完成自动验收）

建议拆成两个阶段：

### GF-000610-A：策略源码迁移

- 按固定提交源码迁移确认后的策略文件到目标策略目录；
- 保留源 `META`、参数、历史窗口逻辑、评分字段和基础过滤语义；
- 只做目标引擎契约所需的最小适配；
- 不迁移 `stock_care`、腾讯实时行情旁路、三星电气脚本、旧策略删除和无证据阈值外改；
- 添加逐策略公式/字段/排序/窗口测试。

### GF-000610-B：运行入口与验收

- 接入目标策略注册/选股 API；
- 核对前端策略列表是否可见（源 Fork 没有专用前端页面）；
- 用合成历史数据验证每个策略的正例/反例、as_of 截止和参数覆盖；
- 运行目标侧相关回归测试；
- 由代理完成技术验收，只有确实需要用户体验确认时才要求手动操作。

用户已确认按 7 个策略实施 GF-000610-A。七个策略文件已按固定 SHA 原样迁入 `data/strategies/custom/`，逐文件 SHA-256 与源快照一致。新增目标侧契约测试，并通过 `tests/test_gf_000610_strategies.py`、`tests/test_strategy_registry.py`、`tests/test_strategy_safety_validation.py`、`tests/test_strategy_scoring.py` 合计 58 项测试；`git diff --check` 通过。

当前仅完成自动化接口/注册/历史帧结构验收；未证明真实行情下的策略命中、投资收益、策略各项正反例及全部参数边界。策略没有新增前端页面，走目标既有策略库与 API。无需用户手动操作；用户已确认前端可看到 7 个策略，并确认 GF-000610-A 验收通过；状态已登记于迁移任务台账。此确认仅覆盖 GF-000610-A，不代表真实收益/实盘效果或未实施内容已验证；下一候选迁移未自动启动。
