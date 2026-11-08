"""策略复盘：前向收益、去重口径、胜负判定与统计。

口径见 app/services/strategy_review.py 模块说明。这些用例覆盖纯函数，
不依赖行情数据；区间扫描的端到端验证由页面运行后的接口核对完成。
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.services.strategy_review import (
    DEFAULT_COST_PCT,
    HORIZONS,
    _SCHEMA,
    _forward_returns,
    _serialize_bars,
    _stats,
    empty_frame,
)


def _bars(prices: list[float], start: date = date(2026, 1, 5)) -> list[dict]:
    """按连续交易日造 OHLCV，收盘价按给定序列。"""
    out = []
    for index, price in enumerate(prices):
        day = start + timedelta(days=index)
        out.append({
            "date": day, "open": price, "high": price * 1.01,
            "low": price * 0.99, "close": price, "volume": 1000.0,
        })
    return out


def test_three_horizons_use_close_of_nth_bar():
    bars = _bars([10.0, 10.5, 10.2, 11.0, 11.5, 12.0, 11.8])
    result = _forward_returns(bars, 0, DEFAULT_COST_PCT)
    assert result["entry_close"] == 10.0
    assert result["tradable"] == "ok"
    # 1 日: 10.5/10 - 1 - 0.0015
    assert result["ret_1"] == pytest.approx(0.05 - DEFAULT_COST_PCT)
    assert result["status_1"] == "win"
    assert result["exit_date_1"] == bars[1]["date"]
    # 3 日: 11.0/10 - 1
    assert result["ret_3"] == pytest.approx(0.10 - DEFAULT_COST_PCT)
    assert result["exit_date_3"] == bars[3]["date"]
    # 5 日: 12.0/10 - 1
    assert result["ret_5"] == pytest.approx(0.20 - DEFAULT_COST_PCT)
    assert result["exit_date_5"] == bars[5]["date"]


def test_net_return_of_exactly_zero_is_a_loss():
    # 毛收益正好等于成本 -> 净收益 0，按「扣成本后 <= 0」判失败
    cost = 0.01
    bars = _bars([10.0, 10.1, 10.1, 10.1])
    result = _forward_returns(bars, 0, cost)
    assert result["ret_1"] == pytest.approx(0.01 - cost)
    assert result["ret_1"] == pytest.approx(0.0)
    assert result["status_1"] == "lose"


def test_missing_tail_bars_are_pending():
    bars = _bars([10.0, 11.0])
    result = _forward_returns(bars, 0, DEFAULT_COST_PCT)
    assert result["status_1"] == "win"
    assert result["ret_3"] is None and result["status_3"] == "pending"
    assert result["ret_5"] is None and result["status_5"] == "pending"


def test_signal_day_without_bar_is_all_pending():
    result = _forward_returns([], None, DEFAULT_COST_PCT)
    assert result["entry_close"] is None
    assert result["tradable"] == "unknown"
    for h in HORIZONS:
        assert result[f"ret_{h}"] is None
        assert result[f"status_{h}"] == "pending"


def test_one_price_bar_is_marked_locked_not_dropped():
    bars = _bars([10.0, 10.5, 10.6, 10.7, 10.8, 10.9])
    bars[0]["high"] = bars[0]["low"] = 10.0
    result = _forward_returns(bars, 0, DEFAULT_COST_PCT)
    assert result["tradable"] == "locked"
    assert result["ret_1"] is not None  # 标注但不剔除


def test_stats_exclude_pending_from_win_rate():
    table = pl.DataFrame([
        {**{c: None for c in _SCHEMA},
         "id": "a", "signal_date": date(2026, 1, 5), "symbol": "000001.SZ",
         "name": "甲", "strategy_ids": "s1,s2", "strategy_count": 2,
         "entry_close": 10.0, "tradable": "ok",
         "ret_1": 0.02, "status_1": "win",
         "ret_3": -0.01, "status_3": "lose",
         "ret_5": None, "status_5": "pending"},
        {**{c: None for c in _SCHEMA},
         "id": "b", "signal_date": date(2026, 1, 6), "symbol": "000002.SZ",
         "name": "乙", "strategy_ids": "s1", "strategy_count": 1,
         "entry_close": 20.0, "tradable": "ok",
         "ret_1": -0.03, "status_1": "lose",
         "ret_3": 0.05, "status_3": "win",
         "ret_5": None, "status_5": "pending"},
    ], schema=_SCHEMA, strict=False)
    stats = _stats(table)
    assert stats["1"]["total"] == 2 and stats["1"]["win"] == 1
    assert stats["1"]["win_rate"] == pytest.approx(0.5)
    assert stats["3"]["win"] == 1 and stats["3"]["lose"] == 1
    assert stats["5"]["total"] == 0 and stats["5"]["pending"] == 2
    assert stats["5"]["win_rate"] is None


def test_empty_frame_has_full_schema():
    frame = empty_frame()
    assert frame.height == 0
    assert list(frame.columns) == list(_SCHEMA)
    assert _stats(frame)["1"]["win_rate"] is None


def test_serialize_bars_skips_invalid_prices():
    bars = _bars([10.0, 0.0, 11.0])
    serialized = _serialize_bars(bars)
    assert len(serialized) == 2
    assert serialized[0]["date"] == "2026-01-05"
    assert serialized[-1]["close"] == 11.0
