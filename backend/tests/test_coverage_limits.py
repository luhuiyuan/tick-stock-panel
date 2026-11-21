from datetime import date, timedelta

import polars as pl

from app.services.coverage_limits import market_counts, price_facts
from app.strategy.strict_signal_data import attach_calendar


def test_official_overrides_theory_and_unknown_is_not_false():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(8)]
    f = pl.DataFrame(
        {
            "symbol": ["A"] * 8,
            "date": days,
            "close": [10.0] * 8,
            "raw_close": [10.0] * 8,
            "raw_low": [9.0] * 8,
            "raw_high": [11.0] * 8,
        }
    )
    inst = pl.DataFrame({"symbol": ["A"], "as_of": [days[-1]], "limit_up": [10.0]})
    r = price_facts(attach_calendar(f, days), inst, days)
    assert r["strict_close_limit"].to_list() == [None] * 5 + [0.0, 0.0, 1.0]
    assert r["strict_broken_limit"].to_list() == [None] * 5 + [1.0, 1.0, 0.0]
    assert r["strict_limit_streak"][-1] == 1


def test_market_rejects_incomplete_sets_and_unknown_prices():
    day = date(2026, 9, 1)
    df = pl.DataFrame(
        {
            "date": [day, day],
            "symbol": ["x", "y"],
            "strict_close_limit": [1.0, 0.0],
            "strict_broken_limit": [0.0, 1.0],
        }
    )
    roster = pl.DataFrame(
        {
            "date": [day],
            "expected_symbols": [["x", "y"]],
            "source_complete": [True],
            "source_version": ["v1"],
            "available_date": [day],
        }
    )
    assert market_counts(df, roster)["strict_market_seal"].item() == 0.5
    assert market_counts(df.head(1), roster).is_empty()
    assert market_counts(
        df.with_columns(pl.lit(None).alias("strict_broken_limit")), roster
    ).is_empty()
    assert market_counts(df, roster.with_columns(pl.lit(False).alias("source_complete"))).is_empty()


def test_post_holiday_limit_price_survives_calendar_diff_jump():
    """长假后/日历混入假日导致 MARKET_INDEX 跳变，不再误杀限价。

    涨停价依赖上一根真实 K 线的收盘价（ref 按 symbol 内相邻行计算），与日历
    diff 无关。即便日历混入法定假日的工作日、节后首日序号跳变（diff > 1），
    昨收依旧有效，限价必须可算 —— 这正是停牌复牌日误判 unknown 的同一根因。
    """
    true_days = [
        date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23),
        date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 29),
        date(2026, 9, 30), date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12),
    ]
    sym = "301314.SZ"
    bars = pl.DataFrame(
        {
            "symbol": [sym] * len(true_days),
            "date": true_days,
            "close": [10.0] * len(true_days),
            "raw_close": [10.0] * len(true_days),
            "raw_low": [9.0] * len(true_days),
            "raw_high": [11.0] * len(true_days),
        }
    )
    inst = pl.DataFrame(schema={"symbol": pl.String, "as_of": pl.Date, "limit_up": pl.Float64})
    post = date(2026, 10, 8)

    clean = price_facts(attach_calendar(bars, true_days), inst, true_days)
    assert clean.filter(pl.col("date") == post)["limit_down_price"].item() is not None

    # 国庆假期的工作日（10-01~10-07）塞进日历，节后首日序号跳 6；
    # 昨收仍是上一根真实收盘价，限价必须照常算出（不再因 diff 跳变留 null）。
    holiday_weekdays = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),
                        date(2026, 10, 6), date(2026, 10, 7)]
    padded = sorted(true_days + holiday_weekdays)
    broken = price_facts(attach_calendar(bars, padded), inst, padded)
    assert broken.filter(pl.col("date") == post)["limit_down_price"].item() is not None


def test_suspension_resume_day_keeps_valid_limit_price():
    """停牌复牌日：个股缺停牌日 K 线，复牌首日序号跳变，涨停价仍须可算。"""
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(10)]
    sym = "603838.SH"  # 沪市主板 10%
    suspend = days[6]  # 停牌日（日历里有，但个股无 K 线）
    resume = days[7]
    bar_dates = [d for d in days if d != suspend]
    prev_close = 12.95  # 停牌前最后收盘价 = 复牌日昨收
    bars = pl.DataFrame(
        {
            "symbol": [sym] * len(bar_dates),
            "date": bar_dates,
            "close": [prev_close] * len(bar_dates),
            "raw_close": [prev_close] * len(bar_dates),
            "raw_low": [prev_close - 1.0] * len(bar_dates),
            "raw_high": [prev_close + 1.0] * len(bar_dates),
        }
    )
    inst = pl.DataFrame(schema={"symbol": pl.String, "as_of": pl.Date, "limit_up": pl.Float64})
    r = price_facts(attach_calendar(bars, days), inst, days)

    row = r.filter(pl.col("date") == resume)
    assert row.height == 1
    up = row["limit_up_price"].item()
    assert up is not None
    # round(12.95 * 1.1, 2) = 14.25；limit_up_price = 限价 - 0.005
    assert abs(up - (14.25 - 0.005)) < 1e-6
    # 停牌日无 K 线，本就不应产出证据
    assert r.filter(pl.col("date") == suspend).height == 0


def _workdays(start, end):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _main_board_bars(sym, dates, closes, highs, lows):
    return pl.DataFrame(
        {
            "symbol": [sym] * len(dates),
            "date": dates,
            "close": closes,
            "raw_close": closes,
            "raw_low": lows,
            "raw_high": highs,
        }
    )


def test_st_unknown_but_amplitude_proves_non_st_enables_limit_price():
    """2026-07-06 前主板、ST 名未知, 但当日触及 10% 跌停 => 反推非 ST, 按 10% 算。

    复刻沃顿科技 000920 2026-07-03: 昨收 14.87, 当日最低 13.40 (-10%)。维表 name
    只有当日快照(放在目标日之后), 目标日 ST 状态不可考, 旧逻辑因 ST 5%/非ST 10%
    分歧拒算(unknown)。K 线振幅突破 7.5% 物理上证明非 ST, 涨停价应 = 16.36。
    """
    days = _workdays(date(2026, 6, 1), date(2026, 7, 8))
    sym = "000920.SZ"  # 深市主板, 07-06 前 ST 5% / 非ST 10%
    target = date(2026, 7, 3)
    ti = days.index(target)
    assert ti >= 6, "目标日须过 6 根热身窗口"
    closes = [14.87] * len(days)
    highs = [15.50] * len(days)
    lows = [14.20] * len(days)
    # 目标日: 昨收 14.87(前一自然交易日 close), 当日最低 13.40 => -9.9% 振幅
    closes[ti] = 13.40
    lows[ti] = 13.40
    highs[ti] = 14.90
    bars = _main_board_bars(sym, days, closes, highs, lows)
    # name 快照放在目标日之后(07-08), 目标日 st 为 null
    snap = max(d for d in days if d <= date(2026, 7, 8) and d >= target)
    if snap == target:  # 若目标日即最后交易日, 补一天
        extra = date(2026, 7, 9)
        days = days + [extra]
        bars = _main_board_bars(sym, days, closes + [13.40], highs + [14.0], lows + [13.0])
        snap = extra
    inst = pl.DataFrame(
        {"symbol": [sym], "as_of": [snap], "name": ["沃顿科技"], "limit_up": [None]}
    )
    r = price_facts(attach_calendar(bars, days), inst, days)
    up = r.filter(pl.col("date") == target)["limit_up_price"].item()
    assert up is not None, "触及10%跌停应证明非ST, 涨停价必须可算"
    # round(14.87*1.1, 2)=16.36 => limit_up_price=16.355
    assert abs(up - (16.36 - 0.005)) < 1e-6


def test_price_probe_detects_st_without_name_snapshot():
    """价格反推 ST: 前后一月内多次精准触及 5% 涨跌停价且无破 5.5% 证据 => 按 5% 算。

    不提供 name 快照 —— 证明反推不再依赖名字。昨收 10.0, 5% 涨停 10.5,
    5% 跌停 9.5; 窗口内多次精准触板, high 全程 < 10.55(5.5%) => 判 ST。
    """
    days = _workdays(date(2026, 6, 1), date(2026, 7, 3))  # 全在 07-06 前
    sym = "000010.SZ"  # 主板
    highs, lows = [], []
    for i in range(len(days)):
        highs.append(10.5 if i % 3 == 0 else 10.3)  # 定期精准触及 5% 涨停
        lows.append(9.5 if i % 4 == 0 else 9.8)       # 定期精准触及 5% 跌停
    bars = _main_board_bars(sym, days, [10.0] * len(days), highs, lows)
    inst = pl.DataFrame(schema={"symbol": pl.String, "as_of": pl.Date, "limit_up": pl.Float64})
    r = price_facts(attach_calendar(bars, days), inst, days)
    # 全窗口判 ST: round(10*1.05,2)=10.5 => limit_up_price=10.495
    for dt in (days[10], days[-1]):
        row = r.filter(pl.col("date") == dt)
        assert abs(row["limit_up_price"].item() - (10.5 - 0.005)) < 1e-6


def test_price_probe_5pct_veto_defaults_to_non_st():
    """窗口内出现 high 破 5.5%(真 ST 不可能) => 一票否决, 默认非 ST 按 10% 算。

    复刻神火股份 000933: 06-23 单日 -8.68% 破 5.5%, 即使 07-03 当日平静也不判 ST。
    """
    days = _workdays(date(2026, 6, 1), date(2026, 7, 8))
    sym = "000933.SZ"
    j = days.index(date(2026, 6, 23))
    closes = [10.0] * len(days)
    highs = [10.2] * len(days)
    lows = [9.9] * len(days)
    closes[j] = lows[j] = 9.132  # -8.68% 破 5.5% 反证
    highs[j] = 10.0
    bars = _main_board_bars(sym, days, closes, highs, lows)
    inst = pl.DataFrame({"symbol": [sym], "as_of": [days[-1]], "name": ["神火股份"], "limit_up": [None]})
    r = price_facts(attach_calendar(bars, days), inst, days)
    target = r.filter(pl.col("date") == date(2026, 7, 3))
    # round(10*1.1,2)=11.0 => limit_up_price=10.995
    assert abs(target["limit_up_price"].item() - (11.0 - 0.005)) < 1e-6


def test_xd_day_does_not_use_amplitude_inference():
    """除权日价格跳空使振幅失真, 不得走反推; 由 xdxr 修正昨收口径处理。"""
    days = [date(2026, 6, 29) + timedelta(days=i) for i in range(6)]
    sym = "000920.SZ"
    closes = [10.0] * 5 + [6.0]  # 10送接近 4 的跳空, 振幅会失真
    highs = [10.5] * 5 + [6.2]
    lows = [9.5] * 5 + [5.8]
    bars = _main_board_bars(sym, days, closes, highs, lows)
    inst = pl.DataFrame(
        {"symbol": [sym], "as_of": [days[-1]], "name": ["沃顿科技"], "limit_up": [None]}
    )
    # 提供权息事件(除权日), 振幅判据必须被 ~xd_present 屏蔽
    adj = pl.DataFrame(
        {
            "symbol": [sym],
            "trade_date": [days[-1]],
            "dividend": [0.0], "bonus": [0.5], "allot": [0.0],
            "allot_price": [0.0], "prev_close": [10.0],
        }
    )
    r = price_facts(attach_calendar(bars, days), inst, days, adj_events=adj)
    last = r.filter(pl.col("date") == days[-1])
    # 除权日走 xdxr 修正昨收: (10-0)/(1+0.5)=6.6667, 涨停 round(6.6667*1.1,2)=7.33
    up = last["limit_up_price"].item()
    assert up is not None
    assert abs(up - (7.33 - 0.005)) < 1e-6, f"除权日应走 xdxr 口径得 7.33, 实际 {up}"
