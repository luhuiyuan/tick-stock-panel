# 变更提案：新增无重绘的市场结构与 BOS/CHoCH 信号

- **变更 ID**：`market-structure-swing-bos`
- **日期**：2026-09-29
- **状态**：提案中（Proposed）
- **范围**：后端指标、策略特征规划、矩阵回测接入和回归测试

## 1. 摘要

为日线 OHLCV 数据新增一套无重绘的市场结构特征。该特征集需要区分摆动点的发生日期和确认日期，识别已确认的多头/空头/中性价格结构，并输出已确认的结构突破（Break of Structure，BOS）和性格变化（Change of Character，CHoCH）事件。

本变更旨在为后续的结构回调策略提供基础：

```text
多头结构
+ 回调进入已确认的支撑区
+ 反弹确认
+ 基于结构的止损
```

本提案不实现支撑/阻力区聚类策略本身，而是建立后续策略可以安全消费的、带明确时间语义的结构基础特征。

## 2. 背景与动机

当前项目已经支持：

- 动态加载内置策略和自定义策略；
- 基于 Polars 的指标和 enriched 数据；
- `matrix_native` 策略执行；
- 支持成交口径、手续费、滑点、止损和持仓期限配置的日线回测；
- 策略参数配置和回测界面。

现有的 `pullback_to_support` 和 `trend_breakout` 等策略，主要通过均线、动量、新高和成交量近似表达趋势跟随行为，但还不能明确表示：

- 已确认的更高高点 / 更高低点；
- 已确认的更低高点 / 更低低点；
- 明确的市场结构状态；
- 结构失效；
- 带有无重绘时间语义的 BOS 和 CHoCH 事件。

在实现“趋势结构 + 关键位 + 回调”交易体系之前，需要先建立这一层结构特征。

## 3. 目标

### 3.1 功能目标

1. 使用可配置的左右确认窗口识别原始 Swing High 和 Swing Low 候选点。
2. 只有在 `pivot_right_bars` 根未来 K 线完成后，才向策略暴露已确认的摆动点。
3. 同时保留摆动点发生日期，以及该摆动点对策略变得可用的确认日期。
4. 将连续的同类型摆动点去重，形成高点/低点交替的结构序列。
5. 使用 ATR 和可配置容差过滤过小的摆动。
6. 将已确认结构分类为：
   - `unknown`（未知）；
   - `bullish`（多头）；
   - `bearish`（空头）；
   - `neutral`（中性）；
   - `bullish_candidate`（多头候选）；
   - `bearish_candidate`（空头候选）。
7. 检测收盘确认的多头 BOS 和空头 BOS。
8. 将 CHoCH 定义为结构失效/方向候选转换，而不是一次突破后立即切换长期趋势。
9. 让所有事件都可以被 `matrix_native` 策略和日线回测使用，且不产生未来数据泄漏。
10. 提供确定性的原因和引用字段，用于调试、图表标注以及未来的策略解释。

### 3.2 工程目标

1. 将核心实现保持为纯函数，并保证可单独测试。
2. 复用现有 Polars/enriched 数据和矩阵回测约定。
3. 不引入第二套基于 Pandas 的指标或回测框架。
4. 未使用该特征时，现有策略的默认行为保持不变。
5. 增加确认延迟、前缀不变性、等高/等低、BOS 时间和 CHoCH 状态转换等回归测试。

## 4. 非目标

本变更不实现：

- 支撑/阻力区聚类；
- 测试次数和独立测试评分；
- 可交易的支撑/阻力互换特征；
- Order Block（订单块）；
- Fair Value Gap（公允价值缺口，FVG）；
- 流动性扫盘规则；
- 基于 Fibonacci 的入场规则；
- 完整的做空交易执行模型；
- 实盘下单；
- 自动修改现有内置策略行为；
- 新的前端页面。

以上能力可以在后续变更中消费本变更新增的结构特征。

## 5. 特征契约

### 5.1 输入

结构计算器接收单个标的、按时间升序排列的日线数据：

```text
symbol
date
open
high
low
close
volume
atr_14
```

实现必须按照现有指标流水线约定校验或优雅处理缺失/无效数据行。

### 5.2 配置参数

第一版参数：

```python
pivot_left_bars: int = 3
pivot_right_bars: int = 3
min_swing_atr: float = 0.5
price_tolerance: float = 0.001
break_buffer_atr: float = 0.05
break_mode: Literal["close"] = "close"
```

第一版只支持收盘确认的结构突破。影线突破需要在收盘口径验证完成后再考虑加入。

### 5.3 Swing 字段

特征输出必须区分候选点和已确认事件：

```text
structure_swing_high_candidate
structure_swing_low_candidate
structure_swing_high_confirmed
structure_swing_low_confirmed

structure_swing_high_origin_date
structure_swing_low_origin_date
structure_swing_high_confirmed_date
structure_swing_low_confirmed_date
```

如果候选摆动点发生在索引 `t`，且 `pivot_right_bars = R`，策略最早只能在索引 `t + R` 使用该摆动点。

### 5.4 结构字段

```text
structure_last_high
structure_last_low
structure_previous_high
structure_previous_low

structure_high_class
    higher_high / lower_high / equal_high

structure_low_class
    higher_low / lower_low / equal_low

structure_trend_state
    unknown / bullish / bearish / neutral
    bullish_candidate / bearish_candidate
```

### 5.5 BOS 和 CHoCH 字段

```text
structure_bos_up
structure_bos_down
structure_choch_up
structure_choch_down

structure_break_level
structure_break_event_date
structure_break_reference_origin_date
structure_invalidated
```

事件必须标记在其确认日期，而不能回写到被突破摆动点的发生日期。

## 6. 算法语义

### 6.1 Swing 候选点

对于 `t` 日的高点候选：

```text
high[t] >= 左侧窗口最高价
high[t] >  右侧窗口最高价
```

对于 `t` 日的低点候选：

```text
low[t] <= 左侧窗口最低价
low[t] <  右侧窗口最低价
```

精确的等值处理规则必须确定，并由测试覆盖。推荐规则是：左侧允许相等，右侧要求严格分离，以避免平台行情中连续产生重复摆动点。

### 6.2 确认时间

如果候选点发生在 `t`，则该事件在以下位置才变得可用：

```text
confirmed_index = t + pivot_right_bars
```

确认行必须保留原始索引/日期，以便下游消费者解释确认延迟。

### 6.3 同类型去重

确认后的结构序列应在高点和低点之间交替。

- 连续高点：保留较高的高点；如果前一个高点尚未与低点配对，则替换它。
- 连续低点：保留较低的低点；如果前一个低点尚未与高点配对，则替换它。
- 如果中间没有确认相反类型的摆动点，则较低的连续高点或较高的连续低点不产生新的结构点。

### 6.4 趋势分类

当已经有两个确认高点和两个确认低点时：

```text
bullish if:
    H2 > H1 + tolerance
    and L2 > L1 + tolerance

bearish if:
    H2 < H1 - tolerance
    and L2 < L1 - tolerance

otherwise:
    neutral
```

如果已确认点不足，状态保持为 `unknown`。

容差应由最小价位变动、价格百分比以及可选的 ATR 组合确定。实现不得使用未来日期的 ATR。

### 6.5 BOS

采用收盘确认口径时：

```text
多头 BOS：
    当前趋势为 bullish
    且 close[t] > last_confirmed_swing_high + break_buffer

空头 BOS：
    当前趋势为 bearish
    且 close[t] < last_confirmed_swing_low - break_buffer
```

突破事件在 `t` 日产生，引用字段指向此前已确认的结构价位。BOS 不得回写到原始摆动点日期。

### 6.6 CHoCH 与候选状态

CHoCH 首先使当前趋势失效，但不能立即确认相反趋势：

```text
bullish → 收盘跌破有效的 higher low
        → bearish CHoCH
        → bearish_candidate

bearish → 收盘突破有效的 lower high
        → bullish CHoCH
        → bullish_candidate
```

只有在后续形成新的有效结构序列后，才确认相反趋势：

```text
bearish_candidate → 确认 LH + LL → bearish
bullish_candidate → 确认 HH + HL → bullish
```

这样可以避免震荡行情中的单根假突破导致趋势反复切换。

## 7. 接入设计

### 7.1 建议的模块边界

建议新增模块：

```text
backend/app/indicators/market_structure.py
```

建议的纯函数：

```python
detect_raw_pivots(df, params) -> pl.DataFrame
apply_pivot_confirmation(candidates, params) -> pl.DataFrame
classify_confirmed_structure(df, params) -> pl.DataFrame
detect_bos_choch(df, params) -> pl.DataFrame
compute_market_structure(df, params) -> pl.DataFrame
```

具体函数拆分可以在实现阶段微调，但候选点与确认点之间的边界必须保持明确。

### 7.2 策略与矩阵接入

结构特征应通过现有矩阵策略使用的依赖解析路径提供。

未来策略应能够声明所需字段，例如：

```text
structure_trend_state
structure_bos_up
structure_bos_down
structure_choch_up
structure_choch_down
structure_invalidated
```

实现必须更新：

- 所需特征解析；
- warmup bar 计算；
- 矩阵列准备；
- 策略缓存/配置文件签名；
- 矩阵策略字段校验。

第一版不应自动修改现有内置策略。

### 7.3 回测成交语义

推荐默认口径：

```text
close[t] 确认事件
open[t+1] 作为最早成交价
```

实现必须记录结构字段在 `t` 日收盘时可用，还是只能在下一根 K 线可用。不能让策略隐式使用信号之前的价格成交。

### 7.4 存储与缓存

第一版优先使用矩阵计算特征，或使用明确版本化的 enriched 特征路径。如果持久化，缓存键/版本必须包含：

```text
pivot_left_bars
pivot_right_bars
min_swing_atr
price_tolerance
break_buffer_atr
break_mode
structure_lookback
```

这些参数任意一个发生变化时，都不得复用不兼容的结构结果。

## 8. 测试与验收标准

### 8.1 摆动点确认延迟

给定索引 `t` 的已知摆动点和 `right_bars = R`：

- `t + R` 之前不能提供已确认摆动点；
- 在 `t + R` 可以提供该已确认摆动点；
- 输出必须同时保留发生日期和确认日期。

### 8.2 前缀不变性

先计算截止到 `T` 的数据，再追加未来 K 线并重新计算。

所有在最后一个尚未完成的右侧窗口之前已经确认的事件，都必须保持完全一致。只有之前尚未确认的候选点可以在追加数据后变为已确认，这是允许的行为。

### 8.3 等高/等低

在配置容差以内的价格必须分类为等高/等低，而不能分类为更高/更低结构。

### 8.4 BOS 时间

收盘突破已确认结构价位时，BOS 必须在突破日产生，而不是在原始摆动点日期产生；在引用摆动点确认之前不能产生 BOS。

### 8.5 CHoCH 状态转换

一次逆趋势突破只能产生 CHoCH 并进入候选状态。在形成所需的新结构序列之前，趋势不能被确认切换到相反方向。

### 8.6 不读取未来字段

构造一个测试：大幅修改某个日期之后的未来 K 线。断言在该日期之前已经可用的输出不发生变化。

### 8.7 缺失数据和数据不足

实现必须：

- 在 warmup 不足时返回 `unknown` 或空结构字段；
- 空输入不抛异常；
- 按现有指标回退约定处理缺失/无效 ATR；
- 保持标的和日期顺序。

### 8.8 回测一致性

使用小型合成面板验证：

- 信号日期与已确认特征日期一致；
- 配置 `open_t+1` 时使用次日开盘成交；
- 回测面板开头的未确认摆动点不能产生首个信号；
- 追加未来行后结果稳定。

## 9. 风险与缓解措施

| 风险 | 缓解措施 |
| --- | --- |
| 右侧摆动点确认导致未来数据泄漏 | 分离发生字段和确认字段；增加前缀不变性测试 |
| 小波动产生过多噪声 | 增加 ATR 显著性和价格容差过滤 |
| 震荡行情导致趋势反复切换 | 使用 `bullish_candidate` / `bearish_candidate` 中间状态 |
| 收盘突破和影线突破口径不一致 | 第一版只支持收盘确认突破 |
| 参数变化后矩阵/缓存不一致 | 将所有结构参数加入依赖和缓存签名 |
| 全市场计算性能不足 | 向量化候选点检测；按标的执行轻量状态机；在需要时再基准测试 Numba |
| 策略语义不清晰 | 返回引用日期、价位、事件类型和原因码 |
| 破坏现有策略 | 仅新增字段；不改变默认行为 |

## 10. 发布/实施计划

### 步骤一：纯算法和合成测试

独立实现并测试结构计算器，不接入 API 和 UI。

### 步骤二：指标/矩阵特征接入

通过现有依赖解析器和矩阵准备路径暴露已确认字段，并补充缓存/版本测试。

### 步骤三：诊断输出

在开发或分析响应中暴露结构字段，以便逐个检查标的结果，但暂时不将其作为交易信号启用。

### 步骤四：消费结构特征的策略

实现一个独立的日线只做多结构回调策略，消费已确认字段。该策略应作为后续变更，不与本结构基础变更捆绑。

### 步骤五：区间和做空模型后续变更

新增支撑/阻力聚类、独立测试计数、支撑/阻力互换，并在此之后再评估完整做空执行模型。

## 11. 已考虑的替代方案

### 直接复制 `smart-money-concepts`

不采用。该项目的输出面向事后分析，其中部分字段需要未来确认；同时它不符合本项目的 Polars/矩阵执行契约。

### 只实现自定义信号表达式

不采用。当前自定义信号 DSL 支持字段比较和 AND 组合，但无法表达有状态的摆动点、确认延迟、连续结构和动态区间。

### 引入完整第三方回测框架

不采用。本项目已经拥有数据加载、矩阵计算、成交语义、成本、缓存和结果展示能力，本变更不应再引入第二套回测执行模型。

## 12. 完成定义

满足以下条件后，本变更才算完成：

- 算法和字段已实现，并明确确认时间；
- 合成测试和回归测试覆盖上述验收标准；
- 矩阵依赖和 warmup 路径已更新；
- 缓存签名包含结构参数；
- 现有策略及其测试行为未发生变化；
- 小型诊断运行证明追加未来数据不会重绘已确认结果；
- 文档明确记录事件时间和成交语义。

本提案本身不会启用任何生产交易策略。
