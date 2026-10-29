# 仓位管理参考仓库

记录日期：2026-10-27。

本文只记住公开仓库里和仓位管理有关的能力，方便以后对照。没有克隆这些仓库，没有把它们接进本系统，也没有改策略或回测代码。许可、接口和默认参数以各仓库当时的 LICENSE 与文档为准；下面只写这次核对过的内容。星标数量会变，这里不记。

## 和本仓库的差距

本仓库多数日线策略仍是整仓进出。策略文件上的 `STOP_LOSS`、移动止损、`MAX_HOLD_DAYS` 管的是退出，不是下单股数。网格按固定手数加减，海龟按 ATR 加仓。还没有按账户风险百分比算股数、限制同时持有只数，或给一组信号分配组合权重。详见 [swing-strategy-integration-plan.md](./swing-strategy-integration-plan.md)。

涨停策略名单和口径见 [limit-up-strategy-survey.md](./limit-up-strategy-survey.md)。那些仓库解决的是信号，不是仓位。

## 怎么用这张表

| 如果要解决 | 先看 | 不要直接拿来用的原因 |
|---|---|---|
| 单笔该买多少股 | Hikyuu 的 `MM_*`，或 riskkit 的 `PositionSizer` | 涨停单经常买不进；股数还要落到 100 股一手，科创板/北交所规则不同 |
| 同时持有几只、每天换几只 | Qlib `TopkDropoutStrategy` | 它按预测分数等权换仓，不是按止损距离控风险 |
| 一组标的的权重、波动率上限 | Riskfolio-Lib；ETF 轮动项目的逆波动率 | 优化器要收益和协方差，打板信号给不出稳定的输入 |
| 亏损后缩小仓位、回撤暂停 | riskkit 的减仓阶梯和回撤档 | 参数是作者默认值，不是本仓库的风控结论 |
| 只跟踪已有持仓和盈亏 | VeighNa `portfolio_manager`；yzz521 的持仓助手 | 它们不决定新开仓买多少 |

Kelly 类公式都要样本外的胜率和盈亏比。本仓库的涨停信号还没有这组统计，不能把公式套上去。

## 框架里的仓位部件

### Hikyuu

- 仓库：<https://github.com/fasiondog/hikyuu>
- 文档：<https://hikyuu.readthedocs.io/zh-cn/latest/trade_sys/money_manager.html>
- 许可：Apache-2.0（已看仓库根目录 `LICENSE`）
- 部件库：<https://github.com/fasiondog/hikyuu_hub>，官网也指向 Gitee 上的同名仓库。本次没有逐个核对 hub 里的资金管理部件。

单策略把仓位做成独立部件 `MM`，和信号 `SG`、止损 `ST`、止盈 `TP`、盈利目标 `PG` 分开。组合再回答三件事：选谁（`SE`）、分多少钱（`AF`）、按什么分（`MF`）。`AF` 文档把分配分成权重、换成股数、风控三层。系统实例 `SYS_Simple` 每次买入后卖出时全部卖出，不在一笔交易里多次加减仓。

公共参数包括：现金不够时是否自动补现金（`auto-checkin`，默认否）、最多持有多少只证券（`max-stock`，默认 20000）、市场环境或系统条件失效时是否强制清仓。

这次在资金管理文档里看到的内建算法：

| 部件 | 文档中的算法 |
|---|---|
| `MM_Nothing` | 不做管理，有多少钱买多少 |
| `MM_FixedCount` | 每次买固定数量，默认 100；文档写明程序不检查是否为最小交易单位的整数倍 |
| `MM_FixedCountTps` | 连续买、连续卖各自用一张数量列表 |
| `MM_FixedRisk` | 数量 = 固定风险金额 / 这笔交易的风险，默认风险 1000 |
| `MM_FixedPercent` | 头寸 = 账户余额 × 百分比 / 每股交易风险，默认百分比 0.03 |
| `MM_FixedCapital` | 用当前现金控制每次投入，默认资本单位 10000 |
| `MM_FixedCapitalFunds` | 用当前总资产控制每次投入，默认资本单位 10000 |
| `MM_FixedUnits` | 当前现金分成 n 份，再除以当前风险，默认 33 份 |
| `MM_WilliamsFixedRisk` | 数量 =（账户余额 × 风险百分比）/ 最大损失，默认百分比 0.1、最大损失 1000 |

文档目录里还有「固定波幅」一节，这次打开的页面没有给出公式，所以不记。自定义时必须实现买入数量；卖出数量不重载则默认卖光。

索引里能看到的分配器包括 `AF_EqualWeight`、`AF_FixedWeight`、`AF_FixedWeightList`、`AF_FixedAmount`、`AF_MultiFactor`，以及不做资金分配的 `PF_WithoutAF`。本次没有逐个打开它们的参数页。

### Microsoft Qlib

- 仓库：<https://github.com/microsoft/qlib>
- 策略说明：<https://github.com/microsoft/qlib/blob/main/docs/component/strategy.rst>
- 许可：本次未打开 `LICENSE`。仓库长期以 MIT 发布，引用前再核对。

`TopkDropoutStrategy` 决定持有只数和换手，不按止损距离算股数：

- `topk`：目标持有只数。文档示例是 50。
- `n_drop`：每个交易日换成更差的几只。文档示例是 5。
- 已持有、且按预测分数排名落到 K 名之外的，卖掉其中分数最差的；再买进未持有里分数最好的，数量与卖掉的相同。候选池够大时，每天大约卖 `n_drop`、买 `n_drop`，换手率约为 `2 * n_drop / topk`。
- `hold_thresh`：未持有够天数则先不卖。
- `forbid_all_trade_at_limit`：涨跌停时可以禁止交易。

`risk_degree` 默认 0.95。策略文档把 `get_risk_degree` 定义成「总资产里拿去投资的比例」。实现里常见写法是用可买名单把 `现金 * risk_degree` 平分。维护者在 issue #612 里说过，0.95 原本是防止钱不够成交的缓冲，拿 `risk_degree` 来做这件事并不准确。所以不能把 0.95 理解成一套风险模型。

同文件里还有按目标权重下单的 `WeightStrategyBase`，以及带风险模型的 `EnhancedIndexingStrategy`。本次没有核对后者的公式。

### Backtrader

- 仓库：<https://github.com/mementum/backtrader>
- 文档：<https://www.backtrader.com/docu/sizers-reference/>
- 许可：GPL-3.0。分发衍生程序前要单独看 copyleft，不能当成可随意摘进本仓库的代码。

Sizer 只回答下单数量。和仓位直接相关的有：

| 类 | 行为 |
|---|---|
| `FixedSize` | 固定数量，默认 1；`tranches` 可把一次建仓拆成几笔 |
| `FixedReverser` | 开仓用固定数量；反手时用两倍，先平再反向开 |
| `PercentSizer` | 空仓时用可用现金的一定百分比，默认 20%；已有仓位则维持现有数量 |
| `PercentSizerInt` | 同上，数量截成整数 |
| `AllInSizer` / `AllInSizerInt` | 百分比默认 100%，也就是用全部可用现金 |

百分比是相对现金，不是相对「亏到止损时最多亏账户的百分之几」。

### VeighNa / vnpy

- 主仓库：<https://github.com/vnpy/vnpy>，MIT
- 组合跟踪：<https://github.com/vnpy/vnpy_portfoliomanager>，MIT
- 说明：<https://www.vnpy.com/docs/cn/community/app/portfolio_manager.html>

`portfolio_manager` 按委托来源把成交拆成独立组合，跟踪开盘仓位、当前仓位、交易盈亏和持仓盈亏。它不计算该买多少。主仓库另有 `portfolio_strategy`，面向同时交易多个合约的策略，提供回测和实盘，同样不是 Kelly 或风险百分比模型。

VeighNa Elite 文档里的事前风控有持仓上限（多头、空头、净仓、总仓、持仓集中度）。那是另一套模块，不在上面这个开源组合管理仓库里。

## 只做仓位计算的库

### riskkit

- 仓库：<https://github.com/HasibVortex369/riskkit>
- 说明：<https://hasibvortex369.github.io/riskkit/>
- 许可：MIT
- 安装名是 `riskkit-quant`，导入名仍是 `riskkit`。核心无第三方依赖。

`PositionSizer` 按止损距离做固定分数风险，再用波动率调整。可选半 Kelly 上限、连亏和回撤减仓阶梯、名义本金上限。README 示例是每笔先按权益的 1% 冒险，单笔风险不超过 1.5%，单笔名义本金不超过权益的 4%。返回数量时带调整原因，方便核对。

可单独调用的函数：

- `kelly_fraction`：由胜率、平均盈利、平均亏损算风险分数；`fraction=0.5` 即半 Kelly。
- `volatility_target_size`：按收益率波动把仓位调到目标波动，不看止损距离。
- `inverse_vol_weights`：权重与波动率成反比，文档称为朴素风险平价。

组合层还有总名义本金、总 heat（止损处的风险合计）、分行业或资产类别上限。`DrawdownManager` 默认按回撤分档缩小仓位，超过暂停线就不再开新仓，恢复时逐档加回，而不是权益一回升就满仓。作者约束是亏损后不得加大风险。这些默认百分比是库自己的例子。

### Aeon Position Sizing

- 仓库：<https://github.com/lijiegsluis/aeon-position-sizing>
- 许可：本次未核对
- 形态：单文件计算器，Python 3.8+，无第三方依赖。不是交易框架。

三种算法放在一起比较：

- Kelly：`f* = (p*b - (1-p)) / b`。全 Kelly 另有 10% 上限；同时给出半 Kelly、四分之一 Kelly。
- 固定风险 1R：风险金额 = 资金 × 风险比例；股数 = 风险金额 / |买入价 - 止损价|，再向下取整。用目标价和止损算出 R 倍数，README 把 R 小于 2 视为不满足其最低要求。
- 信心分档：8–10 分用半 Kelly、单票不超过 8%；5–7 分用四分之一 Kelly、不超过 5%；1–4 分固定 2%。硬上限优先于 Kelly 算出的更大仓位。

示例里的胜率、盈亏比和 1.5% 风险都是演示输入。

### Bayesian Kelly

- 仓库：<https://github.com/sergeisukhovmkt/Bayesian-Kelly-Criterion-with-Parameter-Uncertainty>
- 许可：本次未核对
- 作者把插件 Kelly 乘上样本有效性折扣：`n_eff / (n_eff + kappa)`。README 对波段交易建议 `kappa=30`，并用 `f_max=0.25` 做上限。输入是历史胜负和盈亏比 `b`，不是行情信号。

同一作者另有 <https://github.com/sergeisukhovmkt/kelly-drawdown-code>，比较全 Kelly、半 Kelly 和回撤约束下的降风险规则。那是论文复现，不是可嵌入的下单模块。

### Riskfolio-Lib

- 仓库：<https://github.com/dcajasn/Riskfolio-Lib>
- 站点：<https://www.portfoliooptimization.org>
- 许可：源文件头为 BSD-3-Clause。本次未再打开根目录 `LICENSE.txt`。

它求的是一组资产的权重，不是「离止损多远就买多少股」。和仓位有关的能力包括：均值—风险优化、对数均值风险（Kelly）目标、风险平价、层次风险平价（HRP）、层次等风险贡献（HERC）、嵌套聚类优化（NCO）。风险指标里有波动、CVaR 和多种回撤。约束可以限制资产个数、做空、杠杆、换手和风险贡献。需要收益率序列和协方差，不能直接吃涨停布尔信号。

## 带仓位规则的完整系统

这些系统的规则绑在它们自己的交易品种和执行接口上，只作算法备忘。

### ETF 自适应轮动（QMT）

- 仓库：<https://github.com/guoyaohua/etf-adaptive-rotation-qmt>
- 许可：本次未核对

README 中的组合规则：

- 入选标的按逆波动率加权。
- 预估年化波动率超过 10% 时只把组合缩小，波动率低也不用杠杆补到 10%。
- 单资产权重不超过 40%，总敞口不超过 90%。
- 资金分成 A/B/C/D 四个周度子组合，每周只换其中一份，避免同一天换完全部。
- 其余资金可放到货币 ETF。风险内核还提到 ATR，本次没有继续核对止损公式。

### quant_trading_system

- 仓库：<https://github.com/yzz521/quant_trading_system>
- 许可：MIT（README 写明）
- 核对的是 `main` 分支 README。仓库介绍页上还有另一套「今日计划」文案，以 README 为准。

两条线：

- 量化框架的 `risk/`：仓位、回撤、T+1、投影权重、下单频率。订单先过 `RiskManager` 再送给模拟或 Paper 经纪。回测可打开涨跌停、100 股一手和卖出印花税。
- A 股助手：SQLite 持仓、卖出区间、深套分批路径、加仓参考和诊断。README 写明不自动下真实订单。实盘经纪适配器仍是骨架。

本次没有打开 `risk/` 源码，所以不记具体类名和默认百分比。

### guyu-adam/quant

- 仓库：<https://github.com/guyu-adam/quant>
- 许可：本次未核对
- 品种是美股和 IBKR，不是 A 股。

README 的 `config/settings.py` 示例：单仓不超过权益 10%，总敞口不超过 80%，当日亏损达到 2% 停止交易，每笔风险 1%，止损为 2 倍 ATR。长期组合目标权重偏离超过 5% 才再平衡；价格低于 52 周高点 15% 时额外加半份；从峰值回撤超过 25% 清掉该标的。

## 明确不记入本文的仓库

`guoyaohua/limit-up-sniper` 有首板仓位和分批止盈，但那是打板执行系统。评估结论已经写在 [limit-up-strategy-survey.md](./limit-up-strategy-survey.md)：不接入、不复制。它的仓位规则不在这次「仓位管理参考」范围内展开。

## 本次没有做的事

- 没有克隆、安装或运行上述仓库。
- 没有把任何算法改写成本仓库策略或回测代码。
- 没有核对未打开的许可、hub 部件和 Elite 商业模块的源码。
