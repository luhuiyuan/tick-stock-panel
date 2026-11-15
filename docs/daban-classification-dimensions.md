# 打板接力 · 涨停记录分类维度设计稿

> **状态：设计稿 / 方案，未实现。** 本文只做维度整理与口径约定，未改动任何代码、数据、缓存，未提交未推送。
> **定位：事后观察标签，不是选股策略。** 分类只用于对已有 146,136 笔复盘记录做切片观察，不改变列表笔数、收益口径与现有的 6 档板位。
> 版本 v0.1 · 2026-11-15

---

## 0. 为什么要这样组织

「横盘后涨停」「大跌后的涨停」这类名字**不可能一次定全**：阈值模糊、彼此重叠、不同市场年份下有效性会漂移，后续必然要一轮轮拆分观察，才能确定哪些维度值得保留、哪些该废弃。因此本文不直接给一张固定的分类表，而是给一套可持续迭代的结构：

```
原子特征（一次算好、连续值、无主观命名）
        ↓  阈值组合（配置化、可拖、可调、不触发重算）
规则分类（多标签、带版本、带状态）
        ↓  分档观察 + 交叉拆分
观察面板（n / 胜率 / 均值 / 按年稳定性 / 与基线差）
        ↓  稳定且可复现才固化
已固化分类 / 信号候选
```

**设计原则**

1. 特征与命名分离：先把能算的数值算全，命名与阈值后加，避免为了凑名字改计算。
2. 多标签、非互斥：一笔记录可同时属于多个分类，必须输出重叠矩阵。
3. 只用 T 日及以前的信息，任何事后字段不得进入分类定义。
4. 阈值是参数不是代码：调整阈值只做实时切分，不重算全量。
5. 每条分类都要带样本量与按年稳定性，小样本差异不固化。

---

## 1. 现有基础（只读核查结论，2026-11-15）

| 项 | 值 |
|---|---|
| 全量记录 | 146,136 笔，起点 2016-11-10，交易日历 2,431 天 |
| 板位 | 6 档（首板 / 2 / 3 / 4 / 5 / >5），默认全部、零过滤 |
| `stats` 列 | 23 列：`ph st yr mktcap promo seal lu mc brk gap yizi_t yizi_b bp` + 7 收益 + `di sym boards` |
| 收益 | `r_t1 / r_t2 / r_t3 / r_t5 / r_sl / r_3red / r_trail`（T+1 开盘买入，前复权，未扣费） |
| 窗口张量 `W` | 17 个交易日（T-6 ~ T+10）× 8 字段：`open high low close raw_close amount turnover_rate consecutive_limit_ups` |
| `ohlc.npy` | `(146136, 68)` int32，68 = 17×4，**只存 OHLC 四个价格**，供前端画图 |
| 现有 T 日特征 | 情绪相位、大盘状态、涨停家数、封板率、晋级率、最高板、炸板数、市值、换手、隔夜高开、一字标记 |
| 现有形态特征 | **无**。`W` 里的前期 OHLC / 成交额 / 换手率已加载但未被使用 |

**关键结论**：第一批形态特征的原料已在内存张量里，新增特征几乎没有额外数据成本，只需要在 `build_trades` 里多算若干列。`amount` 与 `turnover_rate` 已在 `FLOAT_COLS`，`clu` 已用于板位切分。

---

## 2. 数据来源与可得性

| 维度 | 数据源 | 覆盖范围 | 结论 |
|---|---|---|---|
| 前期涨跌、波动收敛、量能、缺口、T-1 日 K 线 | `kline_daily_enriched`（已在 `W`） | 4,861 个交易日，全历史 | ✅ 第一批即做 |
| 情绪相位 / 涨停家数 / 封板率 / 晋级率 / 最高板 | `regime_history` | 已在用 | ✅ 直接复用 |
| 板位、距上次涨停、距上次大跌 | `consecutive_limit_ups` / 收盘价 | 全历史 | ✅ 可算 |
| 板块题材联动、龙头 vs 跟风补涨 | `instruments`、`pools`（当前为空） | 待核 | ⚠️ 后置 |
| 相对大盘超额收益 | `kline_index_daily` | 2025-11-07 ~ 2026-11-13 | ⚠️ 仅近一年子样本 |
| 封板时间、炸板次数、尾盘板、秒板 | `kline_minute` | 2026-08-31 ~ 2026-11-13 | ⚠️ 仅近 2.5 个月 |
| 封单、盘口委托 | `depth5` | 约 5 个交易日 | ❌ 暂不可用 |
| 集合竞价明细 | `auction`（当前为空） | — | ❌ 暂不可用 |
| 消息、公告、龙虎榜、舆情 | 无 | — | ❌ 需外接数据源 |

> 任何依赖 ⚠️ / ❌ 数据源的分类，必须在面板上标注其可用时间区间，且不得与全历史分类直接比较。

---

## 3. 网上已有分类维度调研

### 3.1 中文游资 / 民间体系

名字多、主观、互相重叠，但覆盖 A 股特有语境。

| 维度 | 典型类别 | 来源 |
|---|---|---|
| 位置 / 趋势 | 底部反转板、平台·前高·箱体突破板、主升加速板、高位出货板 | [涨停板类型小结](http://www.sanhuba.cn/post/63-e3-22231.html) |
| K 线组合 | 反包板、双响炮 / N 字 / 回马枪（再分 V 型、W 型、平台型）、一阳穿多线、黄金坑 | [回马枪战法](https://www.tgb.cn/a/2vWZgGwzOKD-1) |
| 涨停前兆形态 | 平台整理突破、旗形整理、V 形反转、W 底、超跌后碎步小阳、倒锤头、长上影试盘线 | [东方财富](https://caifuhao.eastmoney.com/news/20241105150704137305030)、[K线形态与涨停](https://www.caiair.com/post/k-xian-yunxing-xingtai-zhangtingban-zoushi-261789-120166.html) |
| 量能 / 封板质量 | 缩量一字·秒板、放量换手板、烂板回封（弱转强）、尾盘偷袭板；按封板时间分早 / 中 / 尾盘 | [六种常见类型](https://www.gupiaobb.com/school/p/1345.html) |
| 主力意图 | 吸筹建仓型、洗盘整理型、出货派发型 | [涨停板战法大全](https://www.178448.com/thread-2157980-1-1.html) |
| 情绪 / 题材 | 龙头首板、跟风补涨、地天板、利好刺激、利空出尽 | [A股涨停板 5 大策略](https://www.xtradingtime.com/articles/limit-up-trading-strategy) |
| 量学「涨停基因」 | 价升量缩、极阴次阳、长腿踩线、兵临城下、倍量不穿等 25 种 | [25 种涨停基因清单](https://www.178448.com/thread-2164315-1-1.html) |
| 已给出数字口径 | 「横行霸道」：暴跌后横盘缩量 10–30 日、乖离 24 日线 −15%~−25%、放量 2 倍突破 5 日线 | [约投顾](https://ag.yueniuzq.com/qa/shen-me-shi-heng-xing-ba-dao-zhang-ting-f-s2-98265) |

### 3.2 英文 / 量化体系

结构更规整、天然可计算，适合作为原子特征的理论来源。

| 大类 | 形态 | 可映射特征 |
|---|---|---|
| 突破与延续 | Flat Base、Bull Flag / Pennant、Ascending / Symmetrical Triangle、Cup & Handle、VCP、Darvas Box、High Tight Flag | 波动压缩比、区间宽度、量能收缩、突破放量 |
| 反转 | Double Bottom、Inverse H&S、Falling Wedge、Rounding Bottom、**Post-Collapse Recovery** | 前期跌幅、距高点回撤、距低点距离、W 底结构 |
| 动量 | **Volatility Compression**、Breakaway Gap、Higher Lows + Volume Spike、Catalyst Gap / Episodic Pivot | 振幅收敛、跳空、连续更高低点、量价配合 |
| K 线 | Bullish Engulfing、Three White Soldiers、Morning Star、Hammer / Inverted Hammer | T-1 实体与影线比例、连阳 |

来源：[16 种形态检测与三维打分](https://www.stockdataanalytics.com/blog/stock-pattern-detection-16-bullish-breakout-patterns-scoring-methodology)（按形态质量 / 量能确认 / 相对强度打分，并指出**形态频率随市场状态漂移**）、[momentum breakout playbook](https://sovascan.com/guide)、[consolidation 三要素：明确支撑阻力 + 窄幅区间 + 缩量无突刺](https://www.investopedia.com/ask/answers/120414/how-do-i-identify-stock-under-consolidation.asp)、[breakout 需放量确认](https://www.investopedia.com/terms/b/breakout.asp)。

### 3.3 直接相关的实证

有文章已用 T-5 累计涨幅、T-1 涨幅、成交额放大倍数给涨停板画特征画像，分出「**连涨加速型**」与「**横盘突发型**」，并发现约 46% 的 20cm 涨停前 5 日是下跌的。结论有两点参考价值：单一动量维度刻画不了涨停前的形态；应该先看分布再定义类别，而不是先定名字。[涨停板特征画像（DuckDB）](https://agents-quant.com/blog/a-share-limit-up-37-stocks-duckdb-feature-portrait)

### 3.4 为什么不能直接照搬

1. **主观且重叠**：一个板可以同时是「平台突破」+「反包」+「龙头首板」，互斥分类树会丢信息。
2. **阈值模糊**：「横盘久」「大跌」没有数字，必须参数化才能复现。
3. **数据源不匹配**：封板时间、封单、题材、龙虎榜在本地只覆盖近 2 个月或根本没有。
4. **叙事 ≠ 统计分类**：网上这些是交易叙事与口诀，本文要的是可复现、可切分、可证伪的特征与规则。

---

## 4. 第 1 层 · 原子特征清单（建议，未实现）

命名仅为建议，用于后续落代码时对齐。全部**只用 T 日及以前信息**。价格用前复权；`C_k` 表示 T-k 日收盘。

### 4.1 位置 / 趋势组

| 字段 | 定义 | 用途 |
|---|---|---|
| `pre_ret_5 / 10 / 20` | `C_1 / C_6 − 1`、`C_1 / C_11 − 1`、`C_1 / C_21 − 1` | 前期动量、加速 / 超跌 |
| `dd_high20 / dd_high60` | `C_1 / max(C_1..C_20) − 1`、`C_1 / max(C_1..C_60) − 1` | 距高点回撤、是否高位 |
| `bias_ma5 / bias_ma20` | `C_1 / MA5 − 1`、`C_1 / MA20 − 1` | 均线乖离（对应「乖离 24 日线」口径） |
| `dist_low60` | `C_1 / min(C_1..C_60) − 1` | 是否处于底部区域 |

> `win_before=6`，T-6 以内可直接取；T-20 / T-60 需要把窗口向前扩或另取一段收盘序列（实现时扩 `win_before` 到 60，仅用于特征、不用于画图）。这是第一批唯一需要动的加载逻辑。

### 4.2 波动收敛组（→「横盘」）

| 字段 | 定义 | 用途 |
|---|---|---|
| `amp_5` | T-5~T-1 每日 `(H−L)/C` 的均值 | 日内振幅水平 |
| `range_5` | `(max(H_{1..5}) − min(L_{1..5})) / C_1` | 区间宽度，横盘核心判据 |
| `std_close_5` | T-5~T-1 收盘收益率标准差 | 收盘离散度 |
| `vol_compress` | `amp_5 / amp_20`（前 20 日振幅均值） | 波动压缩，对应 Volatility Compression / VCP |
| `atr_ratio` | 近 5 日 ATR / 前 20 日 ATR | 同上，稳健版 |

### 4.3 量能组

| 字段 | 定义 | 用途 |
|---|---|---|
| `amt_shrink` | T-5~T-1 平均 `amount` / T-25~T-6 平均 `amount` | 缩量比，对应「缩量蓄势」 |
| `amt_ratio_t` | T 日 `amount` / T-5~T-1 平均 `amount` | 突破放量倍数 |
| `turn_5 / turn_t` | T-5~T-1 平均换手率 / T 日换手率 | 换手水平 |
| `turn_chg` | `turn_t / turn_5` | 换手突变 |

### 4.4 T-1 日 K 线组

| 字段 | 定义 | 用途 |
|---|---|---|
| `prev_ret` | T-1 涨跌幅 | 大跌 / 加速 / 反包 |
| `body_ratio` | `(C_1−O_1) / (H_1−L_1)` | 实体占比 |
| `upper_sh / lower_sh` | 上影 / 下影占全幅比例 | 长上影试盘、长下影探底 |
| `is_yin / is_doji` | 布尔 | K 线组合判定 |
| `gap_t1` | `(O_1 − C_2) / C_2` | 跳空缺口 |

### 4.5 事件上下文组

| 字段 | 定义 | 用途 |
|---|---|---|
| `days_since_lu` | 距上一次 `clu ≥ 1` 的交易日数 | 首板 / 回马枪 / N 字板 |
| `days_since_drop` | 距上一次单日跌幅 ≤ −7% 的交易日数 | 超跌反弹 |
| `first_yang_after_drop` | 连跌后首根阳线（布尔） | 止跌企稳 |
| `days_since_streak_end` | 距上个连板段结束天数 | 双响炮 / 回马枪 |
| `prev_broken`（二期） | T-1 盘中触及涨停但收盘未涨停 | 反包 / 炸板。需复用 `is_limit` 口径由 `high` 推算，**当前张量无直接标记**，暂列二期 |

### 4.6 市场环境组（复用已有，不新增计算）

`phase`、`state`、`limit_up`、`seal_rate`、`promo_rate`、`max_consecutive`、`broken_limit`、`mktcap`、`gap`、`yizi_t`、`yizi_b`、`bp`、`year`。

### 4.7 受限 / 后置组

`rel_market`（相对大盘超额，仅近一年）、`seal_time` / `open_count` / `is_tail_board`（分钟线，仅近 2.5 个月）、`sector_rank` / `is_leader`（需板块数据）、`catalyst`（需外接消息源）。

---

## 5. 第 2 层 · 规则分类清单（候选，阈值待观察校准）

> 所有阈值为**初始建议值**，不是结论；每条都要经过观察面板验证后才可改状态为「已固化」。状态：`候选` → `观察中` → `已固化` / `已废弃`。

| 编号 | 名称 | 初始定义 | 对应网上术语 | 数据 | 状态 |
|---|---|---|---|---|---|
| C01 | 横盘后涨停 | `range_5 ≤ 6%` 且 `abs(pre_ret_5) ≤ 5%` 且 `amt_shrink ≤ 1.0` | Flat Base / 平台整理 / Volatility Compression / 横行霸道 | 全历史 | 候选 |
| C02 | 大跌后涨停 | `pre_ret_20 ≤ −15%` 或 `dd_high20 ≤ −20%` | Post-Collapse Recovery / 超跌反弹 / V 型反转 | 全历史 | 候选 |
| C03 | 连涨加速型 | `pre_ret_5 ≥ 8%` 且 `prev_ret ≥ 3%` | 连涨加速型 / 主升加速板 | 全历史 | 候选 |
| C04 | 反包板 | `prev_ret ≤ −3%` 且 `is_yin` 且 T 日涨停收复 T-1 实体 | 反包涨停 / 极阴次阳 | 全历史 | 候选 |
| C05 | 缩量突破 vs 放量突破 | `vol_compress ≤ 0.7` 交叉 `amt_ratio_t` 分档（<1.5 / 1.5~3 / >3） | 缩量突破多诱多 / 放量突破走主升 | 全历史 | 候选 |
| C06 | 一阳穿多线 | `C_1` 同时上穿 MA5 / MA20 且 `bias_ma20` 由负转正 | 一阳穿多线 / 均线金叉 | 全历史 | 候选 |
| C07 | 回马枪 / N 字板 | `days_since_lu ∈ [2, 10]` 且期间未创新高 | 双响炮 / 单板回马枪 | 全历史 | 候选 |
| C08 | 一字 / 秒板 / 尾盘板 | 由封板时间与开板次数判定 | 按封板时间分档 | ⚠️ 近 2.5 个月 | 后置 |
| C09 | 龙头 / 跟风补涨 | 板块内涨停排序与板块涨停家数 | 龙头首板 / 跟风补涨 | ⚠️ 待核 | 后置 |
| C10 | 高位出货型 | 高位 + 多次开板 + 放量滞涨 | 高位出货板 | ❌ 需封单 | 暂不可 |

### 5.1 交叉分层维度（不是分类，但每次观察都要带着拆）

`bp`（板位）、`year`、`state`（大盘强弱）、`phase`（情绪相位）、`mktcap` 分位、`amt_ratio_t` 分位。任何一档结论都必须在这几层上分别复现，否则不固化。

### 5.2 重叠矩阵

分类非互斥，需输出两两共现笔数与占比；避免出现「两个分类其实在描述同一批记录」却当成两条独立发现。

---

## 6. 第 3 层 · 观察面板与迭代流程

**面板能力**

1. 选任一原子特征 → 按分位数自动分 5–10 档 → 每档显示 `n / 胜率 / 均值 / 中位数 / 与全量基线差 / 按年拆分`。
2. 选中某档后**再按第二个特征拆一层**，支持逐层下钻，这是「不断拆分确定后续维度」的主要操作。
3. 阈值滑杆：拖动直接看样本量与收益怎么变，稳定后「保存为新分类」并写入规则表。
4. 选中已保存分类 → 同样指标 + 重叠矩阵 + 按年/板位/市场状态的一致性检查。
5. 每档强制显示样本量与**最小样本门槛（建议 ≥ 300 笔）**，低于门槛标灰，不允许据此固化结论。

**迭代流程**

| 阶段 | 动作 | 产出 | 是否触发重算 |
|---|---|---|---|
| ① 定口径 | 确认第一批特征与初始阈值 | 本文档 v0.1 → v0.2 | 否 |
| ② 算特征 | 落 `features.parquet`，行号与 `stats` 严格同序 | 特征表 + 字段字典 | **是，一次约 20s** |
| ③ 观察 | 规则表 + 观察面板，只观察、不命名、不宣称有效 | 观察记录 | 否 |
| ④ 固化 | 稳定且可复现的分类改状态为「已固化」，写 caveats | 规则表新版本 | 否 |
| ⑤ 新维度 | 重复 ②③，废弃的改「已废弃」保留痕迹 | 变更记录 | 视是否新增特征而定 |

**注意**：分类是**事后标签**，不是买入条件；面板里的收益只是对现有 7 种出场收益的分组统计，不新增交易、不改变口径。若后续某维度稳定到可称为「信号」，需单独走样本外验证并明确标注其为统计观察结果。

---

## 7. 防自欺硬约束

1. **无未来函数**：`boards`（事后连板数）、`days`（事后持续天数）、7 个 `r_*`、T+1 之后的任何价格只能作因变量，不得进入特征或分类定义。
2. **多重比较**：试足够多的切分必然出现「显著」差异 → 要求按年份、市场状态、板位样本外一致，且设定最小样本门槛。
3. **样本不独立**：同一连涨段拆成多笔、同股同年多笔高度相关 → 按 `(股票, 连涨段)` 聚类做稳健性检查。
4. **标注可用区间**：2016-11-10 起点、分钟数据仅近 2 个月、早年情绪字段缺失，每档必须显示覆盖范围，⚠️ 类不得与全历史直接比较。
5. **分类不互斥**：输出重叠矩阵，禁止把标签当正交因子叠加解读。
6. **收益留空记录**：末尾 67 笔持有期未走完，收益为空，统计时跳过；笔数仍计入，面板需同时展示「记录数」与「有效收益数」。

---

## 8. 工程落点（尚未实施，仅备查）

| 位置 | 若确认后大致改动 |
|---|---|
| `backend/app/services/daban_lab.py` | `build_trades` 内扩 `win_before` 到 60（仅特征用，画图窗口不变），新增约 20 个特征列；`prev_broken` 等二期项先不算 |
| `backend/app/services/daban_compute.py` | 特征单独落 `features.parquet`（与 `stats` 行号同序），不塞进 `stats`，避免 dataset 从 12.8MB 继续膨胀；`meta` 增字段字典与版本号 |
| 后端接口 | 新增 `GET /api/daban-research/features` 或随 dataset 增量返回；规则表存配置（带版本与状态） |
| `frontend/.../recipes/daban.tsx` | 新增「形态分类 / 特征观察」面板，复用现有 `summarize` / `dailyPoints`；阈值调整纯前端实时切分，不发请求 |
| 缓存 | 特征一次落盘后，调阈值、加规则、改名称都**不触发重算** |

---

## 9. 待确认 / 待验证池

| # | 事项 | 状态 |
|---|---|---|
| 1 | 第一批 20 个特征是否全要，有无增删 | 待确认 |
| 2 | `win_before` 扩到 60 是否可接受（内存与 20s 重算耗时的影响） | 待验证 |
| 3 | 各分类初始阈值（6% / 5% / −15% / −20% 等） | 待观察校准 |
| 4 | 是否先只做「横盘」「大跌」两类试跑 | 待确认 |
| 5 | 输出格式与落位（Markdown / 进仓库 / Word / PDF） | 待确认 |
| 6 | 板块与题材数据源是否存在可用历史 | 待核 |
| 7 | 受限维度是否按「近年子样本」单独出一份观察结果 | 待确认 |

## 10. 变更记录

| 版本 | 日期 | 内容 |
|---|---|---|
| v0.1 | 2026-11-15 | 初稿：调研归纳、数据可得性、三层结构、第一批特征与候选分类、防自欺约束、迭代流程 |

---

## 参考来源

- [涨停板类型小结（位置/量能/K线/情绪四大类）](http://www.sanhuba.cn/post/63-e3-22231.html)
- [涨停板战法大全（主力意图与封板质量）](https://www.178448.com/thread-2157980-1-1.html)
- [量学 25 种涨停基因清单](https://www.178448.com/thread-2164315-1-1.html)
- [回马枪战法：按形态分 V 型 / W 型 / 平台型](https://www.tgb.cn/a/2vWZgGwzOKD-1)
- [即将被打板前规律（底部、中继、特殊 K 线）](https://caifuhao.eastmoney.com/news/20241105150704137305030)
- [如何通过 K 线运行形态抓住涨停板](https://www.caiair.com/post/k-xian-yunxing-xingtai-zhangtingban-zoushi-261789-120166.html)
- [涨停板的六种常见类型与市场含义](https://www.gupiaobb.com/school/p/1345.html)
- [A 股专属的 5 大涨停板策略](https://www.xtradingtime.com/articles/limit-up-trading-strategy)
- [横行霸道涨停法（含可量化阈值）](https://ag.yueniuzq.com/qa/shen-me-shi-heng-xing-ba-dao-zhang-ting-f-s2-98265)
- [16 种看涨形态检测与三维打分](https://www.stockdataanalytics.com/blog/stock-pattern-detection-16-bullish-breakout-patterns-scoring-methodology)
- [The momentum breakout playbook（base breakout / VCP / catalyst gap）](https://sovascan.com/guide)
- [Investopedia：识别 Consolidation](https://www.investopedia.com/ask/answers/120414/how-do-i-identify-stock-under-consolidation.asp)
- [Investopedia：Breakout 定义与放量确认](https://www.investopedia.com/terms/b/breakout.asp)
- [Investors Underground：Consolidation / Reversal / Momentum breakout](https://www.investorsunderground.com/stock-breakouts/)
- [涨停板特征画像（DuckDB 实证，连涨加速型 vs 横盘突发型）](https://agents-quant.com/blog/a-share-limit-up-37-stocks-duckdb-feature-portrait)
- [用多因子构建涨停板前介入模型（技术面/资金面/情绪面因子清单）](https://www.logic88.cn/146404.html)
