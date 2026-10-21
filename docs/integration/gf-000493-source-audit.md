# GF-000493 CZSC 日线信号与图表源码审计

- 审计日期：2026-10-19
- 源仓库：`yydcnjjw/tick-stock-panel`
- 固定分支：`main`
- 固定源码 SHA：`c97af561de0be4c412c29c641c3f6ade824bbff4`
- 源码目录：`/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`
- 功能提交：`79bccd7943fe9e869d5f6e0706ca4445c4b05125`（CZSC 初始功能）

## 1. 固定源与版本演进

GF-000493 的候选提交最初引入：

- 可选依赖 `czsc==1.0.1`；
- 六类日线辅助买卖信号；
- 信号可选接入策略、回测和监控；
- CZSC 日线结构图、快照 API、个股入口与前端页面；
- 日线收盘边界、缺失数据、首次触发、覆盖率和监控保护测试。

固定 `main` 在该提交之后又包含关键演进：

- `77e9035`：用固定版本的 vendored `chan.py` 替换 CZSC 计算引擎；
- `d90cd55`、`6ad6dee` 等：多周期图表、分钟数据恢复和回测并行/取消能力；
- 当前 `pyproject.toml` 的 `czsc` extra 已为空兼容项，目标算法放在 `backend/app/vendor/chanpy`，不应再直接安装或复制 CZSC 运行时。

因此，审计和后续迁移必须以固定 SHA 的**现行 Chan/CZSC 产品契约**为准，不能只复制 `79bccd7` 的历史 diff。

## 2. 现行功能边界

固定 `main` 的现行文档与测试显示：

- 固定 `chan.py` 上游提交、MIT 许可证和内部命名空间；
- 有确认事件语义：`endpoint_at`、`first_seen_at`、`confirmed_at`，以已确认结构边界防止未来函数；
- 信号分为 1、1p、2、2s、3a、3b 等类型，支持策略/回测信号选择与覆盖率诊断；
- 图表复用逐根回放，展示 K 线、分型、笔、中枢、未完成结构、成交量、MACD 和信号；
- 支持日线及后续多周期/分钟数据边界，未收盘 K 线、缺失/损坏数据和源快照变化有明确降级；
- 监控拒绝不支持的 CZSC/Chan 运行形态，并要求当前源快照，避免用陈旧或非当前结构触发；
- 前端提供独立结构图页面/扩展入口及个股详情入口。

## 3. 目标侧对照与缺口

目标仓库当前未发现 `czsc_*`、`chan_*`、CZSC 可选依赖、Chan 结构运行时或对应前端扩展。现有目标虽然有：

- 通用指标流水线；
- 策略信号、回测、监控和个股分析扩展入口；
- K 线与 ECharts 基础组件；
- `data/strategies/custom` 策略目录；

但这些通用入口不等于已经具备 CZSC/Chan 的结构状态、确认事件、覆盖率、快照缓存和监控保护。

主要迁移风险：

1. **算法版本风险**：不能按早期 CZSC 1.0.1 直接安装；固定源当前以 vendored Chan 引擎为准。
2. **数据时点风险**：日线/分钟收盘边界、源快照和缺失 K 线必须保持 fail-closed，不能用普通指标流水线代替结构回放。
3. **跨层接入风险**：功能同时影响指标、API、策略、回测、监控、前端和测试，不能整提交覆盖目标文件。
4. **资源风险**：结构回放、图表和批量回测可能显著占用内存；本机仅 2 vCPU、3.5 GiB RAM，必须单进程、单并发、先做窄测试。
5. **许可证/依赖风险**：应核对固定 Chan 上游 MIT 许可及 vendored 文件清单；不得引入未经确认的外部 CZSC 依赖。
6. **产品边界风险**：源功能是辅助信号和结构可视化，不应宣称为严格缠论买卖点或保证收益。

## 4. 建议实施拆分（待用户确认）

### GF-000493-A-Foundation：Chan/CZSC 结构与信号底座

- 固定并审查源 `chan.py` vendored 文件、版本、许可证和算法配置；
- 迁入现行结构回放、确认事件、六类/现行信号选择、不可计算原因与覆盖率契约；
- 复用目标已有指标/策略/回测/监控边界，避免覆盖目标核心文件；
- 添加小规模日线合成数据测试：收盘边界、未来数据截止、缺失/损坏数据、首次触发、同标的隔离、依赖缺失降级；
- 暂不接入前端页面、分钟数据或真实行情。

### GF-000493-B：图表、API、策略/回测/监控与前端集成

- 在 Foundation 验证通过后，按固定源当前 API/缓存/快照契约接入；
- 增加独立结构图 API、前端扩展页面、个股入口和结构覆盖率提示；
- 接入策略设置、回测信号、监控保护和必要的前端信号选项；
- 单独进行前端类型/组件测试和低内存单进程构建验证。

明确排除：源仓库与 CZSC 无关的研究文档、纸面交易记录、ToolCatalog、全量分钟修复提交，以及未经单独确认的 Chan 中枢研究策略。

## 5. 审计结论

GF-000493 是真实新增的跨层指标/信号/图表能力，不是单纯 bug 修复；目标侧未发现等价实现，值得进入迁移前确认。由于固定源已经从 CZSC 1.0.1 演进到 vendored Chan，建议按 `Foundation → Execution` 两阶段实施，先完成算法/时间边界/数据契约，再接入 API、前端、回测和监控。

## 6. GF-000493-A-Foundation 实施记录（2026-10-20）

- 用户确认 Foundation 范围后，从上文固定 SHA 快照迁入 `backend/app/vendor/chanpy`、MIT LICENSE、`UPSTREAM.json`、`chan_runtime.py`、`chan_signals.py`、`czsc_bars.py` 和 Foundation 测试/fixture。
- 固定源 vendored 52 个文件与目标逐文件 SHA-256 一致；没有安装外部 CZSC 依赖。未接入目标指标流水线、API、图表、策略、回测、监控或前端；本阶段只以日线合成数据验证，不声明分钟数据或真实行情验收。
- 自动验证：`backend/.venv/bin/python -m pytest -q tests/test_chan_runtime.py tests/test_chan_signals_foundation.py` → **22 passed**；新增非 vendor 文件 Ruff 检查通过；`git diff --check` 通过。
- 未部署、未重启服务、未提交或推送。
- 当前状态：**GF-000493-A-Foundation 用户验收通过**。GF-000493-B 尚未开始；须先向用户提交独立方案并获得明确确认。


### 用户验收确认（2026-10-20）

用户确认 GF-000493-A-Foundation 验收通过。确认范围限于已记录的隔离底座迁移和自动验证，不扩展至产品集成、分钟/真实行情或前端体验。GF-000493-B 未启动。


## 7. GF-000493-B-Execution 实施断点（2026-10-20）

用户已确认 B 范围，当前为**实施中、未完成**。已落地图表结构计算/本地仓库适配/API、日线扩展入口、Chan 按需信号与策略/回测入口保护、旧 CZSC 隔离和监控前置拦截。定向验证与普通策略结果契约回归、前端类型检查、绘图单测和生产构建通过，具体命令和结果见任务台账最新记录。

现有前端日线页只属于初批切片，不能取代固定快照现行的多周期、线段/线段中枢、候选端点与确认事件时点展示。回测覆盖率/取消进度、前端场景可用性保护、监控旧规则状态处理和真实产品调用链回归也尚未补齐。分钟采集/新 Provider/Chan 中枢研究策略仍明确排除；不采集分钟数据不等于获批删除已有本地分钟读取/图表产品契约。

本阶段未通过整体自动验收，尚不应要求用户人工验收通过或开始下一迁移项。未提交/推送、未重启服务；已构建前端产物。


### B-Execution 后续实施记录：图表快照一致性（2026-10-20）

按固定快照 `c97af561de0be4c412c29c641c3f6ade824bbff4` 的 `get_daily_published`、`get_minute_published`、generation/fingerprint 读取契约补齐目标本地图表读取：股票/指数日线按已发布 parquet 有界读取，分钟按 symbol/结束时点有界读取；指数与分钟用只读分区指纹验证读前读后变化。股票 enriched generation 增加可选只读门控，图表调用不会初始化缺失 generation，也不会恢复孤儿发布标记；其它调用方默认行为不变。损坏 parquet 读取错误向 API 映射为服务不可用，不伪装为正常空行情。新增回归由 `test_chan_chart.py` 和 `test_enriched_generation.py` 覆盖。

本切片自动测试：`uv run --frozen pytest -q tests/test_chan_chart.py tests/test_chan_retirement.py tests/test_enriched_generation.py` → 39 passed；`git diff --check` 通过。未新增分钟采集/provider/写入能力。GF-000493-B 仍在实施中，尚未完成整体验收。


### B-Execution 后续实施记录：Chan 回测进度/取消与覆盖率（2026-10-20）

固定源 `c97af561de0be4c412c29c641c3f6ade824bbff4` 的策略回测路径将 progress/cancel 传至 `load_panel_for_backtest`，在 Chan 信号准备后计算覆盖率，并在 Chan 重放取消时返回取消状态而不提交部分结果。目标此前 `BacktestEngine.load_panel_for_backtest` 和回测服务均未透传 progress/cancel。现依固定源调用链补入参数透传、取消捕获和正式区间覆盖率。按本机资源约束 `czsc_max_workers=1`，不复制固定源的 4 worker 配置。错误结果构造器保留已生成的覆盖率诊断；旧签名测试替身通过方法签名过滤兼容。

新增 `backend/tests/test_chan_backtest.py` 覆盖回调透传、取消事件与 worker 配置、取消无半成品结果。验证：回测兼容组合 **35 passed**；图表/generation/Chan 回测组合 **27 passed**；compileall 和 `git diff --check` 通过。成功回测 API/SSE 与前端覆盖率呈现仍未端到端验收，GF-000493-B 继续保持实施中。


### B-Execution 后续实施记录：回测覆盖率 API/SSE 与前端（2026-10-20）

固定源成功日线回测把 `czsc_diagnostics` 写入 `result.stats["czsc_coverage"]`，并在 `select_stats` 的诊断白名单中保留它。目标已按该两处补齐；白名单未加入源码中同组但属于未迁移研究范围的其他键。失败 SSE 与源测试 `test_sse_error_preserves_czsc_coverage` 一致，只附加 `stats.czsc_coverage`。取消事件不带覆盖率。前端 `errorStats` 与页面读取顺序按固定源 `backtestTask.ts`、`StrategyBacktest.tsx` 对齐。

验证：回测兼容组合 **39 passed**；compileall 与 `git diff --check` 通过；`ChanCoverageSummary` 组件测试 1 passed。GF-000493-B 仍在实施中，策略/选股/监控整链和整体自动验收尚未完成。未提交、未推送、未重启服务。

### B-Execution 后续实施记录：策略、选股、监控调用链（2026-10-21）

固定源策略运行在接入当日信号后，把原因空值补为 `missing_data`，并且只对 `date == as_of` 计算覆盖率；没有命中行时结果仍携带 `czsc_coverage`。目标 `StrategyEngine.run` 已按该口径补齐。

固定源在保存、局部更新和重置策略配置时，若进出场或声明信号涉及 Chan/旧 CZSC，只删除该策略的选股缓存和监控状态。目标补上 `invalidate_strategy`，并保留原有叠加条件的全量清缓存。因目标缓存有内存镜像，失效时拷贝后再改，写盘后丢弃旧镜像。

选股策略清单按已保存 override 重算 `execution_available`。选股页按固定源提示不可执行原因、跳过新的运行，并展示单策略覆盖率。

验证：`tests/test_chan_retirement.py` 与 `tests/test_chan_backtest.py` **27 passed**；compileall 与 `git diff --check` 通过。GF-000493-B 仍在实施中，整体自动验收未做。未提交、未推送、未重启服务。


### B-Execution 整体自动验收（2026-10-21）

用户确认开始 GF-000493-B 已迁移范围的整体自动验收。未新增业务代码。结果：

- 后端：`uv run --frozen pytest -q -p no:xdist` 覆盖 `test_chan_runtime.py`、`test_chan_signals_foundation.py`、`test_chan_chart.py`、`test_chan_retirement.py`、`test_chan_backtest.py`、`test_enriched_generation.py`、`backtest/test_strategy_backtest_correctness.py`、`backtest/test_minute_backtest.py` → **93 passed in 17.08s**。
- 前端：图表结构、Chan 覆盖率组件、Czsc 页面测试 → **11 passed**。`tsc -b` 通过。未重跑 Vite 生产构建。
- `python -m compileall` 覆盖本阶段相关 Python 文件，通过；`git diff --check` 通过。

当前状态改为 **待人工确认**。这不是人工验收通过，也不授权下一功能。未覆盖浏览器端到端、真实行情、全量测试、服务重启后的页面验收、分钟采集、新 Provider 和 Chan 中枢研究策略。未提交、未推送、未重启服务。


### 用户验收确认（2026-10-21）

用户确认 GF-000493-B-Execution 页面符合预期，人工验收通过。确认依据是 3018 上的页面查看，以及此前已记录的定向自动验收。结构图维持固定源入口，不加入侧栏；选股页不预置 Chan 策略，chan.py 信号位于策略触发器。

本次确认不包含分钟采集、新 Provider、Chan 中枢研究策略、真实行情收益或全量测试。未提交、未推送，未开始下一功能。
