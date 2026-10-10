from datetime import date, timedelta

import polars as pl

from app.indicators.timeframe_structure import compute_timeframe_structures


def frame(days: int = 160) -> pl.DataFrame:
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(days)]
    close = [100.0 + (i % 20) for i in range(days)]
    return pl.DataFrame({
        "date": dates,
        "open": close,
        "high": [value + 2 for value in close],
        "low": [value - 2 for value in close],
        "close": close,
        "volume": [100.0] * days,
    })


def test_week_and_month_exclude_current_incomplete_period():
    result = compute_timeframe_structures(frame())
    assert set(result) == {"W", "M"}
    assert result["W"]["timeframe_label"] == "周线"
    assert result["M"]["timeframe_label"] == "月线"
    assert result["W"]["completed_periods"] > 0
    assert result["M"]["completed_periods"] > 0
    assert result["W"]["confirmation_bars"] == 3
    assert result["M"]["confirmation_bars"] == 3
    assert result["W"]["data_through_date"] < "2025-06-09"
    assert result["M"]["data_through_date"] < "2025-06-01"


def test_empty_and_insufficient_input_are_safe():
    result = compute_timeframe_structures(pl.DataFrame())
    assert result["W"]["trend"] == "unknown"
    assert result["M"]["trend"] == "unknown"
    result = compute_timeframe_structures(frame(3))
    assert result["W"]["completed_periods"] == 0
    assert result["M"]["completed_periods"] == 0


def test_period_ohlcv_is_aggregated_and_daily_atr_is_not_reused():
    from app.indicators.timeframe_structure import compute_timeframe_chart

    daily = pl.DataFrame({
        "date": [date(2025, 1, 2), date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 13)],
        "open": [1.0, 10.0, 13.0, 20.0],
        "high": [2.0, 15.0, 17.0, 22.0],
        "low": [0.5, 9.0, 12.0, 19.0],
        "close": [1.5, 13.0, 16.0, 21.0],
        "volume": [1.0, 100.0, 200.0, 1.0],
        "atr_14": [999.0] * 4,
    })
    candles, structure = compute_timeframe_chart(daily, "W")
    assert candles.height == 1
    row = candles.row(0, named=True)
    assert row["date"] == date(2025, 1, 7)
    assert (row["open"], row["high"], row["low"], row["close"], row["volume"]) == (10, 17, 9, 16, 300)
    assert row["atr_14"] == 8
    assert structure["completed_periods"] == 1
    assert structure["trend"] == "unknown"
    assert "数据不足" in structure["trend_label"]


def test_month_aggregation_and_structure_dates_share_same_axis():
    from app.indicators.timeframe_structure import compute_timeframe_chart

    candles, structure = compute_timeframe_chart(frame(1100), "M")
    dates = {str(value) for value in candles["date"].to_list()}
    assert all(point["date"] in dates and point["confirmed_date"] in dates
               for point in structure["swing_points"])
    assert candles["date"][0].month == 2
    assert candles["volume"][0] == 2800
    assert candles["close"][0] == frame(1100).filter(pl.col("date") == date(2025, 2, 28))["close"][0]
    assert "last_high" in compute_timeframe_structures(pl.DataFrame())["M"]
