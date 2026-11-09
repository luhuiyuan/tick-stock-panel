# Vendored hot-money-radar 规则核心

本目录存放从上游 `financial-analysis` 提取的**主力/游资形态规则核心**，
供 `phase-lab`(主力行为阶段识别与经验库) 复用。遵循"能直接用就直接用、
不重写一套"的原则：规则函数体逐字取自上游，未改任何判定逻辑。

## 来源

- 上游仓库：https://github.com/sorahjy/financial-analysis
- 快照 commit：`b26f01ccb3d19ecfff7e857ba9ef4471adbb21ff` (branch `main`, 浅克隆于 2026-11-09)
- 源文件：`stock_hot_money_radar.py` (7114 行)
- 许可证：**MIT** (见 `LICENSE`，Copyright (c) 2026 sorahjy)
- 本地完整上游副本：`/workspace/reference/financial-analysis/`(仅本地参考，不入库)

## 提取范围与方法

上游是单文件脚本，混有 sqlite 数据层、实时行情抓取、CLI、verify/patterns
回测等与本仓库无关的代码。此处只保留 `match_patterns()` 的**依赖闭包**：

- 用 AST 从 `match_patterns`、`_pattern_phase`、`_build_pattern_context`、
  `_pattern_confidence`、`_phase_*`、`PATTERNS`(26 条规则引用的 `_pat_*` 函数)、
  `_score_chip`、`_chip_metrics` 出发，递归求传递闭包(含函数默认参数/注解引用)；
- 得到 **213 个顶层定义**(80 个函数/类 + 133 个常量)，按原文件行序逐字拼接，
  加最小 import 头(`math` / `numpy` / `typing`)；
- 闭包外部依赖仅 `math`、`numpy`、`typing`，**不引用** `sqlite3` / `requests` /
  `stock_storage` / `stock_etf_pool` / `argparse` / `ProcessPoolExecutor`；
- 明确**不搬**：`_bar()` 等 DB 行解析、腾讯/新浪/东财实时行情、CLI
  (`build_parser` / `__main__`)、verify/patterns 事件研究、题材/新闻/潜伏分等。

产出的 `radar_core.py` = 1984 行。文件头 module docstring 已注明来源。

## 上游规则表与复测结论

`radar_core.py` 保留了上游模块 docstring 中的完整规则总表
(6 类 / 26 个编号 P1-P26，含逐条"全历史复测为负向风险 / 保留形态观测
不作为有效因子 / 正向有效"的结论)，以及 `PATTERNS`、`_pattern_phase`、
`_phase_confidence` 等阶段优先级与置信度逻辑。这些结论是本项目
`phase-lab` 经验库的重要输入，但**需在本仓库信号池上自行复测**，
不直接当作已验证结论使用。

## 一致性验证

见 `backend/tests/test_hot_money_radar_vendor.py`：
用合成行情(上涨/下跌/连板/高波)与真实 enriched 日线，逐条比对
`radar_core.match_patterns` 与本地上游完整副本的 `match_patterns` 输出。
该测试依赖 `/workspace/reference/financial-analysis/` 本地副本；
副本缺失时测试跳过(不影响本仓库自身规则逻辑的 import/冒烟断言)。

## 数据契约

`match_patterns(code, bars)` 要求 `bars` 为日线 dict 列表，字段：
`date / open / high / low / close / volume / amount / chg / turnover`
(换手率，百分数)，以及可选的预计算 `_chip_avg_price`。
`adapter.py` 负责把项目 polars/DataFrame 日线转成该结构。
P1 的大盘门控 `p1_market_gate` 默认 `False`，本项目暂不启用
(依赖上游 510310 指数净值)。
