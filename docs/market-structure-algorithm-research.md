# 价格结构与支撑阻力算法调研

- **调研日期**：2026-09-29
- **调研目标**：评估 GitHub 上已有的技术分析、市场结构和支撑阻力实现，判断哪些算法可以为本项目的“趋势结构 + 关键位 + 顺势回调”交易体系提供参考。
- **当前结论**：没有一个现成项目可以直接完整覆盖目标体系。推荐保留当前项目的 Polars、`matrix_native` 和回测执行链路，参考 `smart-money-concepts` 的 Swing/BOS/CHoCH 思路，自行实现无未来函数的市场结构和价格区间特征。

## 1. 目标交易体系的算法拆解

目标体系不是单一指标，而是由四层逻辑组成：

1. **趋势结构**
   - 多头：高点逐步抬高，低点逐步抬高（HH/HL）。
   - 空头：高点逐步降低，低点逐步降低（LH/LL）。
   - 结构被破坏后，原趋势假设失效或进入重新判断状态。

2. **关键位区间**
   - 关键位不是一个精确价格，而是有宽度的支撑/阻力区间。
   - 区间需要考虑摆动高低点、成交密集度、测试次数、最近测试时间和区间宽度。

3. **支撑阻力互换**
   - 原阻力被有效突破后，后续回踩成为支撑。
   - 原支撑被有效跌破后，后续反弹成为阻力。

4. **交易与风险管理**
   - 多头趋势中只关注回调到支撑区后的做多信号。
   - 空头趋势中只关注反弹到阻力区后的做空信号。
   - 止损应优先参考结构失效位或关键区间外侧，而不是只使用固定百分比。

## 2. GitHub 项目调研

### 2.1 TA-Lib Python

项目：<https://github.com/TA-Lib/ta-lib-python>

TA-Lib 是成熟的技术分析指标封装，覆盖大量趋势、动量、波动率、成交量和 K 线形态指标。当前 Python 封装支持 NumPy、Pandas 和 Polars 输入，并提供多平台预编译 wheel。

适合参考或使用的能力：

- ATR、ADX、SAR；
- 移动平均线、MACD、RSI；
- 布林带、随机指标；
- K 线形态识别；
- 基础指标结果的交叉验证。

不直接提供的核心能力：

- HH/HL、LH/LL 价格结构；
- 趋势状态机；
- 支撑/阻力价格区间；
- 独立关键位测试次数；
- 支撑阻力互换；
- BOS/CHoCH 等结构事件。

**适配结论**：TA-Lib 适合作为可选指标补充或测试基准，不适合作为本交易体系的核心实现。当前项目已经在 Polars 指标流水线中计算 MA、EMA、ATR、MACD、RSI、布林带等指标。为了避免同时维护两套指标口径，不建议仅为了本策略直接引入 TA-Lib 生产依赖。

### 2.2 `smart-money-concepts`

项目：<https://github.com/joshyattridge/smart-money-concepts>

这是与目标体系最接近的项目之一，包含：

- Swing High / Swing Low；
- Break of Structure（BOS）；
- Change of Character（CHoCH）；
- Order Block；
- Fair Value Gap；
- Liquidity；
- Retracement。

可参考的对应关系：

| 目标概念 | 可参考能力 |
| --- | --- |
| 更高高点/更高低点 | Swing High / Swing Low |
| 趋势延续 | BOS |
| 结构反转 | CHoCH |
| 关键区域 | Order Block / FVG |
| 回调位置 | Retracement |
| 流动性扫盘 | Liquidity 相关逻辑 |

重要风险：Swing 高低点通常需要左右两侧的 K 线确认。若直接把完整历史计算结果回填到过去日期，会把未来数据带入回测。生产实现必须明确：

- 摆动点确认需要多少右侧 K 线；
- 信号从哪个日期开始生效；
- 结构突破是否需要收盘确认；
- 回测中是否按确认后的下一根 K 线成交。

**适配结论**：推荐作为市场结构算法的主要参考，但不建议直接复制为生产依赖。应将其核心思想改写为当前项目的 Polars 和 `matrix_native` 特征，并补充严格的确认延迟测试。

### 2.3 `pytrendline`

项目：<https://github.com/ednunezg/pytrendline>

`pytrendline` 面向 OHLC 数据寻找支撑线和阻力线，包括：

- 局部极值识别；
- 候选趋势线连接；
- 误差与有效点数量检查；
- 趋势线突破判断。

优点：

- 直接面向支撑/阻力分析；
- 适合单只股票或小窗口的研究；
- 可以作为离线结果或人工分析的对照实现。

局限：

- 趋势线候选搜索成本较高；
- 不适合对全市场标的进行高频批量扫描；
- 输出偏向趋势线，不等同于“有厚度的价格区间”；
- 需要额外改造成无未来函数的滚动计算。

**适配结论**：适合用于研究页面、算法对照和测试样本生成，不建议直接接入全市场 `matrix_native` 回测热路径。

### 2.4 Jesse

项目：<https://github.com/jesse-ai/jesse>

Jesse 是完整的交易研究和回测框架，支持多时间周期、多标的、订单、止损、止盈、多空和风险管理。

可参考的能力：

- 多空统一持仓模型；
- 限价单、止损单和止盈单；
- 多周期数据组织；
- 订单级回测；
- 风险管理和策略统计。

**适配结论**：可以参考其多空和订单语义，但不建议整体引入当前项目。当前项目已经有自己的数据仓库、策略引擎、矩阵回测、费用模型、缓存和前端回测页面。整体引入 Jesse 会形成第二套数据与执行模型。

### 2.5 vectorbt

项目：<https://github.com/polakowo/vectorbt>

vectorbt 提供基于信号的组合回测能力，支持多空信号、止损、止盈、移动止损和自定义订单逻辑。

可参考的能力：

- `long_entries` / `long_exits`；
- `short_entries` / `short_exits`；
- 止损、止盈和移动止损；
- 复杂订单逻辑的自定义函数；
- 向量化组合回测。

**适配结论**：可用于对照多空和风险模型，但不建议替换当前回测引擎。引入后会带来 Pandas/NumPy 转换、内存开销、依赖膨胀以及成交和费用语义不一致的问题。

### 2.6 `bukosabino/ta`

项目：<https://github.com/bukosabino/ta>

该项目提供基于 Pandas 的常用技术分析指标封装，包括趋势、动量、波动率、成交量等指标。

**适配结论**：与 TA-Lib 的定位相似，适合基础指标参考，不直接解决价格结构和关键区间识别问题。当前项目优先使用 Polars，不建议为本策略再引入一套 Pandas 指标层。

## 3. 与当前项目的适配情况

### 3.1 当前已经可以复用的能力

当前项目已经具备：

- 策略动态加载和注册；
- `META` 参数定义；
- Polars 表达式策略；
- `matrix_native` 向量化策略；
- 日线回测；
- 入场/出场信号；
- 固定止损、止盈、移动止损和最大持仓天数；
- `close_t` / `open_t+1` 成交口径；
- 手续费、印花税和滑点；
- 回测矩阵缓存和 Walk-forward；
- 前端策略参数与回测页面。

主要相关目录：

```text
backend/app/strategy/engine.py
backend/app/strategy/builtin/
backend/app/backtest/engine.py
backend/app/backtest/matrix.py
backend/app/backtest/strategy.py
backend/app/indicators/pipeline.py
backend/app/indicators/levels.py
frontend/src/components/screener/
frontend/src/pages/backtest/
```

### 3.2 已有但不能直接复用为目标算法的能力

`backend/app/strategy/builtin/pullback_to_support.py` 已经实现：

- 接近 MA20；
- 缩量；
- 位于 MA60 上方；
- 20 日动量为正。

它更接近“均线附近回踩”，不是完整的“结构趋势 + 关键位区间”策略。

`backend/app/indicators/levels.py` 已经提供：

- 成交密集区；
- Pivot；
- 前高前低；
- Swing 高低点；
- 布林带、Keltner、ATR 通道；
- 缺口、Fibonacci 和整数关口。

但该模块主要服务于图表 markLine 和 AI 个股分析，返回的是当前窗口的展示价位，不是可安全回测的逐日滚动特征。不能直接把完整窗口计算结果回填到历史日期，否则可能引入未来数据。

### 3.3 当前回测对做空的限制

现有 `TradeRecord` 和 `BacktestEngine` 主要按照先买入、后卖出的多头模型设计：

- PnL 默认按 `(exit_price - entry_price) / entry_price` 计算；
- 成本模型区分买入和卖出；
- 持仓状态没有统一的 `long/short` 方向字段；
- 停止损逻辑没有完整的空头语义；
- A 股现货的融券、保证金、借券费和不可卖空约束尚未建模。

因此“空头趋势反弹做空”不能仅通过新增一个策略文件完成。如果目标是 A 股股票，第一阶段更适合实现为做空候选信号或风险预警；如果需要真正回测空头，则需要扩展统一的多空持仓和成交模型。

## 4. 推荐的本地实现架构

### 4.1 市场结构模块

建议新增：

```text
backend/app/indicators/market_structure.py
```

职责：

```text
detect_confirmed_swings()
classify_trend_structure()
detect_structure_break()
detect_bos_choch()
```

建议输出字段：

```text
swing_high_confirmed
swing_low_confirmed
last_confirmed_swing_high
last_confirmed_swing_low
trend_state: bullish / bearish / neutral
structure_break_up
structure_break_down
structure_age_bars
```

实现要求：

- 使用左右窗口确认摆动点；
- 记录确认日期，不把确认前的信息提前使用；
- 可以设置最小波动幅度，例如 ATR 倍数；
- 结构状态必须有 `neutral`，不能强行二选一；
- 必须为每个结构事件提供可解释的原因字段。

### 4.2 价格区间模块

建议新增：

```text
backend/app/indicators/price_zones.py
```

建议输出一个或多个有厚度的区间：

```python
{
    "lower": 10.20,
    "upper": 10.45,
    "side": "support",
    "touch_count": 5,
    "flip_count": 2,
    "last_test_date": "2026-09-29",
    "age_bars": 12,
    "strength_score": 0.82,
    "source": "swing_volume_profile",
}
```

需要避免把连续横盘中的每根 K 线都计算成一次独立测试。建议引入：

- 区间宽度阈值，例如 ATR 的倍数；
- 两次测试之间的最小 K 线间隔；
- 同一波动簇只算一次；
- 最近测试衰减；
- 测试时的成交量或换手率权重；
- 突破后的确认周期。

### 4.3 第一阶段策略

建议新增：

```text
backend/app/strategy/builtin/structure_pullback_long.py
```

第一版只实现日线多头：

```text
trend_state == bullish
AND 当前价格进入支撑区
AND 支撑区测试次数 >= N
AND 支撑区强度 >= 阈值
AND 没有发生结构失效
AND 出现反弹确认
```

可配置参数：

```text
pivot_left_bars
pivot_right_bars
min_swing_atr
min_touch_count
min_flip_count
zone_width_atr
entry_confirmation
stop_buffer_atr
max_hold_days
```

推荐入场确认方式：

```text
收盘进入支撑区后，下一根 K 线突破确认 K 线高点
```

相对于“触碰区间立即入场”，这种方式更能避免价格继续下跌时误入场。

### 4.4 风险模型

推荐优先使用结构止损：

```text
多头止损 = 支撑区下沿 - ATR × buffer
```

或：

```text
多头止损 = 最近确认的更高低点 - ATR × buffer
```

固定百分比止损可以作为数据不足时的 fallback，但不应作为唯一止损规则。

## 5. 关键回测约束

### 5.1 严格避免未来函数

每个交易日的结构和关键位只能使用当日及以前的数据：

```text
正确：rolling window ending at t
错误：用完整历史数据计算后回填到 t
```

摆动点的右侧确认会产生延迟，必须在信号日期和成交日期中体现。

### 5.2 明确成交口径

至少需要区分：

```text
收盘进入区间，当日收盘成交
收盘确认信号，次日开盘成交
分钟数据触及关键位，分钟成交
```

第一版推荐：

```text
日线收盘确认，次日开盘成交
```

### 5.3 结构止损需要统一成交假设

当前回测模型支持固定止损和移动止损，但结构止损需要明确：

- 盘中触及止损价是否成交；
- 若同一根 K 线同时触发止损和止盈，先后顺序如何判断；
- 跳空穿过止损价时使用开盘价还是止损价；
- 涨跌停和不可成交如何处理。

### 5.4 关键位测试不能只按触碰次数统计

连续几根 K 线在同一区间横盘，不应被计为多次独立测试。建议测试定义至少包含：

```text
进入区间
离开区间至少 N 根 K 线
再次进入区间
```

并可根据成交量、波动幅度和反应强度调整测试权重。

## 6. 推荐实施阶段

### 阶段一：日线多头回调 MVP

范围：

- 股票和 ETF 日线；
- 价格结构识别；
- 支撑区生成；
- 多次测试计数；
- 可选支撑阻力互换；
- 反弹确认；
- 结构止损；
- 接入现有 `matrix_native` 回测和选股。

不包含：

- 真正的做空回测；
- 分钟级关键位触发；
- 复杂趋势线穷举；
- 机器学习评分；
- 自动实盘交易。

### 阶段二：结构解释和图表展示

在选股结果和个股详情中展示：

```text
趋势：多头
当前支撑区：10.20 ~ 10.45
测试次数：5
支撑阻力互换：2 次
结构失效位：9.95
触发原因：回调进入支撑区并重新站回区间上沿
```

后端应返回结构化的 `reason_codes`，不要只返回自然语言描述。

### 阶段三：空头候选信号

先实现：

```text
空头趋势识别
阻力区识别
反弹到阻力区提醒
结构突破失效提醒
```

此阶段可以服务于风险预警和候选列表，但不把它当成 A 股现货可直接执行的做空交易。

### 阶段四：统一多空回测模型

如果确认需要真正支持做空，再扩展：

- `position_side`；
- 多空 PnL；
- 空头成交费用；
- 保证金和借券费；
- 可卖空标的约束；
- 空头涨跌停与不可成交规则；
- 多空同时持仓和风险敞口。

## 7. 项目选择结论

| 项目 | 建议 | 用途 |
| --- | --- | --- |
| TA-Lib | 有条件使用 | 基础指标和交叉验证 |
| `smart-money-concepts` | 推荐参考 | Swing、BOS、CHoCH、结构事件 |
| `pytrendline` | 仅研究使用 | 单股离线趋势线和支撑阻力对照 |
| Jesse | 不直接引入 | 参考多空和订单模型 |
| vectorbt | 不替换当前引擎 | 参考多空信号和止损接口 |
| `bukosabino/ta` | 不建议新增依赖 | Pandas 指标参考 |
| 当前项目 Polars / `matrix_native` | 作为生产实现 | 性能、缓存和回测口径统一 |

## 8. 最终建议

不要把目标体系简单实现成：

```text
close 接近 MA20 + momentum > 0
```

这只会得到现有“均线回踩策略”的变体，无法表达：

```text
趋势结构
关键位厚度
多次独立测试
支撑阻力互换
结构失效止损
顺势而非预测
```

推荐路线是：

1. 参考 `smart-money-concepts` 定义 Swing、BOS 和 CHoCH；
2. 使用当前项目 Polars 流水线实现无未来函数的结构特征；
3. 将展示用 `levels.py` 与回测用 `price_zones.py` 分开；
4. 先实现日线多头回调 MVP；
5. 先验证信号质量、未来函数和成交口径，再扩展空头模型；
6. 不直接引入完整第三方回测框架。

## 9. 来源

- TA-Lib Python：<https://github.com/TA-Lib/ta-lib-python>
- smart-money-concepts：<https://github.com/joshyattridge/smart-money-concepts>
- pytrendline：<https://github.com/ednunezg/pytrendline>
- Jesse：<https://github.com/jesse-ai/jesse>
- vectorbt：<https://github.com/polakowo/vectorbt>
- bukosabino/ta：<https://github.com/bukosabino/ta>

以上项目仅作为算法和工程实现参考。正式接入前应再次核对上游仓库的当前版本、许可证、API 变化和生产依赖风险。
