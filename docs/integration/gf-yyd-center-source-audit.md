# YYD-CENTER Chan 中枢研究策略源码审计

- 审计日期：2026-10-21
- 任务：`YYD-CENTER`（从 `YYD-FOLLOWUP` 拆出；不属于已验收的 `GF-000493-A/B`）
- 源仓库：`yydcnjjw/tick-stock-panel`
- 固定分支：`main`
- 固定源码 SHA：`c97af561de0be4c412c29c641c3f6ade824bbff4`
- 源码目录：`/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`
- 功能提交：`c97af561de0be4c412c29c641c3f6ade824bbff4`
- 提交说明：`feat(backtest): add stateful Chan center range research strategies`
- 作者与日期：`yydcnjjw`，2026-10-09
- 规模：28 个文件，+2587 / −24
- 本次动作：只读审计。未改业务代码，未安装依赖，未运行测试，未部署或重启，未提交或推送。

## 1. 迁移意义与用户可获得的能力

固定源在 GF-000493 已迁移的笔、线段、中枢图表和 chan.py 确认信号之外，另有一套有持仓状态的日线研究策略 `chan_center_range`（名称「中枢震荡: 分型 + 123买卖点」）。

用户若确认实施，获得的是回测页上的研究策略，而不是新的选股或监控策略：

- 八套互不混用的规则：原版、仅成交门槛、仅扩大退出、仅风险定仓、完整新版、中枢结构实验版、中枢结构实验保守版、第24课止损实验版。
- 默认不能跑正式收益回测。只有显式 `params.exploratory=true` 才使用现有行情样本做探索性回测，并在结果里保留口径警告。
- 成交由组合撮合器按实际次日开盘推进；结构观察本身不模拟成交。
- 普通选股、监控、参数优化保持阻断。

这不是收益承诺，也不是严格递归级别的缠论买卖点。源文档写明历史资格未接入，结构实验和课24方案未完成真实历史收益对照。

## 2. 固定源行为契约

策略入口：`backend/app/strategy/builtin/chan_center_range.py`。

- ID：`chan_center_range`
- `EXECUTION_BACKEND = "matrix_native"`
- `backtest_only = True`
- 资产与周期：股票、日线
- 参数：`exploratory` 默认 `False`；`rule_set` 页面默认「完整新版」
- 未携带 `rule_set` 的旧请求在 `CenterPolicy.for_rule_set` 中回落到「原版」。`test_exploratory_run_uses_native_signals_and_persists_scope` 要求保存后的配置和 `stats.center_rules` 都是「原版」
- 预热：`required_warmup_bars` 为 1000
- 入场、出场信号列表为空。信号由 `CenterRangeStrategy.compute_signals` 生成，不走已迁移的 `signal_chan_*` 触发器

算法与状态：

- `backend/app/backtest/chan_center.py`：观察矩阵、八套规则、`CenterBook` 持仓状态、`validate_center_execution`
- `backend/app/backtest/chan_center_context.py`：前缀稳定的上下文；缺失日不跨接
- `backend/app/backtest/chan_center_lesson24.py`：仅「第24课止损实验版」启用的稀疏证据
- 观察复用 `ChanReplay`。本提交给它增加 `fractal_events` / `_new_fractals`。目标 `backend/app/indicators/chan_runtime.py` 已有这两项，因为 GF-000493 复制的就是该 SHA，不能再按本提交重写一遍

硬边界（必须原样保留，不能改成可运行的正式策略）：

- `FORMAL_BLOCK_REASON`：没有逐日上市、退市、ST 资格，正式收益回测在读行情前拒绝。不得用当前股票名单或观察分组代替。
- `exploratory` 只有 Python `True` 才放行。`False`、缺省、`"false"`、`1` 都仍然阻断。
- 探索性结果写入 `stats.research_scope`：模式、警告、现有沪深主板样本、`historical_eligibility_verified=false`。若另外打开当前名称 ST 过滤，警告必须追加「不能解释为历史 ST 资格筛选」。数据错误也要保留该口径。
- `LIVE_BLOCK_REASON`：依赖成交后的持仓状态，不支持普通选股或监控。`StrategyEngine.run`、显式 `run_all`、监控检查都要失败。
- 批量选股默认列表排除 `backtest_only`。显式点名该策略时同样拒绝。
- 参数优化即使带上 `exploratory=true` 也拒绝，测试匹配「历史股票池」。
- `validate_center_execution`：必须双侧 `open_t+1`；禁止分钟成交、最长持有天数，以及额外止盈、止损、移动止损。
- 股票池只用代码前缀：`60*.SH` 与 `00*.SZ`。这只划定沪深主板代码，不证明历史 ST 或退市资格。最新中枢不合格时不回退到更早的宽中枢。
- 期末未平仓继续估值，写 `open_positions` 和 `open_position_details`，不制造策略卖点。
- 叠加信号若带 `center_features`，直接失败：「中枢震荡持仓状态不能合并为叠加信号」。
- `BacktestResultPolicy.select_stats` 在裁剪统计时保留 `research_scope`、`center_rules`、`open_position_details`。GF-000493-B 已明确不保留这三项；本策略才需要加回。

规则差异只来自 `CenterPolicy.for_rule_set`，不另写观察器：

| 方案 | 源码行为 |
|---|---|
| 原版 | 下部 20% 底分型入场，上部 20% 以内顶分型退出；不定仓门槛 |
| 仅成交门槛 | 只加开盘位置不超过 40%、距下沿不超过 5% |
| 仅扩大退出 | 只把退出扩到上部 20% 及上方顶分型 |
| 仅风险定仓 | 只加每笔计划风险 1%、单只最多 50% |
| 完整新版 | 同时启用门槛、扩大退出和 1% 风险定仓 |
| 中枢结构实验版 | 上下沿均不低于前一中枢；始终区间底分型；顶分型或确认卖点退出；按剩余空间/下沿距离排序；计划风险 1%；不套 40%/5% 门槛 |
| 中枢结构实验保守版 | 同上，计划风险 0.5% |
| 第24课止损实验版 | 独立的 A/C 绿柱面积证据和 C 低点冻结；计划风险 1%；不称严格三卖 |

成交约束在源文档 `docs/chan-center-backtest.md` 与撮合测试中一致：100 股整手、买单只在下一市场交易日开盘有效、卖出受阻保留原请求、同一收盘卖出优先、计划风险不保证实际亏损上限。

## 3. 配套测试

本提交新增的测试是行为契约，后续只能适配加载路径，不能改断言去迎合新逻辑。

- `backend/tests/test_chan_center_strategy.py`：注册、详情 API、正式回测和优化阻断、选股/监控阻断、探索性口径、显式 `True`、规则方案传到撮合器。
- `backend/tests/backtest/test_chan_center.py`：成交后才冻结边界、阶段切换、再入场、缺日买单作废、跳空与受阻卖出、禁止额外执行参数、期末不强平。
- `backend/tests/backtest/test_chan_center_controls.py`：成交价门槛、风险定仓、整手、50% 上限、各方案独立。
- `backend/tests/backtest/test_chan_center_research.py`：上下文前缀、缺失日、实验参数失败即关闭。
- `backend/tests/backtest/test_chan_center_structural.py`：中枢过滤只用过去、边界修订不是新中枢。
- `backend/tests/test_chan_center_features.py`：包含关系、右侧 K 线、非主板不入池、不回退旧宽中枢。
- `backend/tests/test_chan_center_lesson24_features.py` 与 `backend/tests/backtest/test_chan_center_lesson24.py`：课24面积、首次反弹、证据撤销、风险线不下移。

同一提交还把内置策略计数从 26 改到 27、矩阵策略计数从 25 改到 26，并让带 `backtest_block_reason` 的策略不进入共享缓存画像。目标策略数量和加载目录都不同，这些计数断言不能原样搬过来。

源文档自述验证包括定向测试和一次前端构建，并写明没有真实历史收益对照、没有向运行服务提交回测。本审计没有重跑这些测试。

## 4. 接入点

不能整提交覆盖目标文件。源 diff 里需要保留的接线是：

- `backend/app/backtest/matrix.py`：`SignalMatrix.center_features` 随创建、校验、切片、内存统计传递。
- `backend/app/backtest/engine.py`：有中枢特征时走 `CenterBook`，成交和退出回调，写入 `center_rules`、`open_positions`、`open_position_details`；执行前调用 `validate_center_execution`。
- `backend/app/backtest/strategy.py`：读行情前阻断；探索性口径；仅 `chan_center_range` 构造 `CenterPolicy`；统计裁剪保留三项研究字段。
- `backend/app/api/strategy.py`：详情返回 `backtest_only`、阻断原因、探索性参数和警告；`run-all` 排除 `backtest_only`。
- `backend/app/api/screener.py`：全量选股排除 `backtest_only`。
- `backend/app/strategy/engine.py`：单次和批量实盘执行拒绝 `backtest_only`。
- `backend/app/strategy/monitor.py`：监控检查返回同一阻断原因。
- `backend/app/strategy/composite.py`：拒绝合并带中枢状态的信号。
- `backend/app/strategy/ai_generator.py`：只把 `app.backtest.chan_center` 加入策略代码导入白名单。
- 前端 `StrategyBacktest.tsx`：探索性勾选、未勾选时显示阻断、规则方案说明、结果警告和 CSV 口径。
- 前端 `api.ts`：参数 `option_details`、策略详情四个字段、成交 `center_reference`。
- 前端 `signals.ts`：四个中枢出场/入场编号的中文名。
- `docs/chan-center-backtest.md`：产品口径说明。

## 5. 目标差异

已有、不得重做：

- `ChanReplay.fractal_events` 与 `_new_fractals` 已在目标运行时。
- 策略 API 的 `run-all` 和监控检查已经会读取 `backtest_only` / `live_block_reason`。目前没有策略带上这些字段，所以还没有可见效果。
- 前端 `StrategyDetail` 已有可选的 `backtest_block_reason`，但没有探索性字段，回测页也没有对应控件。
- GF-000493 的图表中枢是结构展示。`chan_center_range` 是另一套研究成交策略，不在已验收范围内。

尚不存在：

- 没有 `chan_center_range`、`chan_center.py`、`chan_center_context.py`、`chan_center_lesson24.py`、`center_features` 或 `CenterBook`。
- `backend/app/backtest/strategy.py` 没有正式回测阻断、探索性口径和 `CenterPolicy`。`select_stats` 会丢掉 `research_scope`、`center_rules`、`open_position_details`。
- `StrategyEngine._run_unfiltered` 和 `run_all` 还没有源提交里的 `backtest_only` 拒绝。目标已把单次执行拆到 `_run_unfiltered`，接线点在这里，不能按源文件行号覆盖 `run`。
- 选股 `run_all` 只排除 `research_only`，还没有排除 `backtest_only`。
- 策略详情没有返回 `backtest_only` 和探索性字段。
- 前端没有 `option_details`、探索性勾选、研究口径展示和 `center_reference` 类型。
- `cnSignal` 没有四个中枢信号中文名。目标函数已经改成直接匹配 chan 笔级信号，只能把这四个名字补进当前函数，不能回放旧 diff。
- 叠加合并没有拒绝 `center_features`。
- 导入白名单有 `numpy` 和 `app.backtest.matrix`，没有 `app.backtest.chan_center`。

架构差异：

- 源测试从 `backend/app/strategy/builtin/` 加载策略。
- 目标 `builtin` 包只保留 `factor_rank_research` 转发，`main.py` 不扫描该目录。公共策略通过 `backend/app/strategy/seeds/` 幂等补入 `data/strategies/custom/`，已有同名文件不覆盖。
- 因此策略文件不能指望放进 `builtin/` 就会被运行中的引擎加载。后续若实施，算法模块仍放 `backend/app/backtest/`；策略入口应按现有种子方式进入 custom，并让定向测试指向该种子或临时副本。这是加载位置适配，不改变规则。
- 种子复制会经过 `AIStrategyGenerator._validate_safety`。所以白名单必须包含源提交新增的那一个模块，不能扩大到任意回测内部模块。

`engine.py`、`matrix.py`、`strategy.py` 与固定源已明显分叉。实施时只移植中枢状态传递和门控，不把源文件整段盖回目标。

## 6. 明确不迁移

- `.agents/skills` 的两份 Chan 选股流程（`6244566a`、`9df7358e`）。它们依赖本策略，产品价值低于策略本身。
- `3909841e` / GF-000494 的历史交易日三态判断。目标已有日历过滤；这是修复收紧，按当前顺序靠后。
- `b4f9c2ab` 的 Docker 镜像重试和 host 网络。与官方源要求冲突。
- 其余 12 个非 `yydcnjjw` 提交。目标历史已有对应功能，不从该 Fork 再迁。
- 实盘、普通选股、监控、参数优化、分钟成交、额外止盈止损、持有期限。
- 逐日上市、退市、ST 资格数据源。没有这项，就不能把结果写成完整历史股票池收益。
- 真实全市场回测、收益对照、部署和重启。

## 7. 风险

1. 正式门控一旦漏接，策略会在没有历史资格数据时算出看起来像完整收益的结果。
2. 全历史逐股 `ChanReplay` 预热 1000 根，内存高于普通矩阵策略。本机 2 vCPU、约 3.5 GiB 内存。验证只能用合成样本、单进程，不能默认跑全市场探索性回测。
3. 根分区使用率约 91%。不做 clean build，不扩大构建范围。
4. 目标撮合器已有自己的组合、取消和覆盖率路径。接 `CenterBook` 时可能碰这些路径，必须用源测试锁定，不能顺手改 GF-000493 的覆盖率语义。
5. 策略种子写入用户数据目录。实施时只能补缺失文件，不能覆盖用户已有同名策略。
6. 安全白名单不是沙箱。只加源提交那一个模块。
7. 3018 可能仍在运行。本任务不重启、不部署；运行中的进程不会自动加载未发布代码。

## 8. 若确认实施时的范围与验证

建议一次按源契约迁完策略、门控、撮合接线和回测页展示。拆成先后端、后前端会让源 API 测试无法同时成立。仍不包含第 6 节排除项。

实施前仍须单独确认。确认后的文件范围预期为：

- 新增三个回测模块、策略种子、源产品说明和上述八个后端测试的适配副本。
- 最小修改 matrix、engine、strategy、strategy engine、screener、strategy API、monitor、composite、导入白名单。
- 最小修改回测页、`api.ts`、`signals.ts`。
- 更新本台账。不改 GF-000493 已验收语义。

验证计划：

1. 先看 `free -h`。可用内存或空闲 swap 不足则停止。
2. 只跑中枢相关后端测试，单进程；不跑全量套件，不跑真实历史回测。
3. 对改动的 Python 文件做 `compileall`。
4. 前端只在类型或回测页改动后做一次低内存 `tsc` 或相关测试；不并行构建。
5. `git diff --check`。
6. 自动验证通过后停在待人工确认。人工项只看回测页是否出现该策略、未勾选探索性时是否只显示阻断且不发请求、勾选后是否显示警告。不要求用户判断收益。

当前状态：源码审计完成，`待迁移前确认`。未获实施确认前不修改业务代码。
