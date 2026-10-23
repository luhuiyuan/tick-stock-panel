"""底仓做T：对照掘金 MACD 回转和 stockquant 等量对冲，外加 A 股 T+1。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import polars as pl

from app.backtest.base_t_execution import simulate_base_t
from app.backtest.swing_rules import (
    band_order_qty,
    band_prices,
    flatten_to_base,
    macd_clip,
    replay_intraday_day,
)
from app.strategy.ai_generator import AIStrategyGenerator
from app.strategy.engine import StrategyEngine


def _clock(start: int, count: int) -> np.ndarray:
    """从 HHMM 起连续取交易分钟，跳过午休。"""
    hour, minute = divmod(start, 100)
    out = []
    while len(out) < count:
        if (hour == 11 and minute > 30) or hour == 12 or (hour == 13 and minute == 0):
            hour, minute = 13, 1
        if hour > 15 or (hour == 15 and minute > 0):
            break
        out.append(hour * 100 + minute)
        minute += 1
        if minute >= 60:
            hour += 1
            minute = 0
    if len(out) < count:
        raise RuntimeError(f"clock short {len(out)} < {count}")
    return np.asarray(out, dtype=np.int64)


def test_macd_clip_uses_the_strict_source_cap():
    # 9900 + 100 < 10000 为假，所以最多买到 9900。
    assert macd_clip(1.0, 9800, 0, 10000, 100) == ("buy", 100)
    assert macd_clip(1.0, 9900, 0, 10000, 100) == ("hold", 0)
    assert macd_clip(-1.0, 0, 9900, 10000, 100) == ("hold", 0)
    assert macd_clip(0.0, 0, 0, 10000, 100) == ("hold", 0)


def test_flatten_cannot_sell_shares_bought_today():
    assert flatten_to_base(10100, 10000, 0) == ("hold", 0)
    assert flatten_to_base(10100, 10000, 100) == ("sell", 100)
    assert flatten_to_base(9900, 10000, 10000) == ("buy", 100)


def test_band_rejects_a_spread_under_the_fee_floor_and_sizes_equal_lots():
    assert band_prices(10.0, 0.003, 0.003, 0.008) is None
    sell, buy = band_prices(10.0, 0.015, 0.015, 0.008)
    assert abs((sell - buy) / 10.0 - 0.03) < 1e-12
    # target 10000 / 10 = 1000 股；底仓 10000 的一半是 5000；取 1000。
    assert band_order_qty(10.0, 10000, 10000, 0.5) == 1000
    assert band_order_qty(10.0, 1000, 10000, 0.5) == 500


def test_macd_day_buys_only_until_the_cap_and_restores_with_yesterdays_shares():
    n = 40
    close = np.linspace(10, 12, n)
    orders = replay_intraday_day(
        close, close + 0.01, close - 0.01, close, _clock(931, n),
        kind="macd", base=1000, sellable=1000, params={"trade_n": 100},
    )
    buys = [item for item in orders if item["reason"] == "macd" and item["side"] == "buy"]
    assert buys
    assert all(item["qty"] == 100 for item in buys)
    assert sum(item["qty"] for item in buys) < 1000
    # 这组时钟未必含 14:55。补一根尾盘，确认调回。
    close2 = np.concatenate([close, [12.0]])
    clock2 = np.concatenate([_clock(931, n), [1455]])
    orders = replay_intraday_day(
        close2, close2, close2, close2, clock2,
        kind="macd", base=1000, sellable=1000, params={"trade_n": 100},
    )
    assert orders[-1]["reason"] == "flat"
    assert orders[-1]["side"] == "sell"
    assert orders[-1]["shares_after"] == 1000


def test_macd_cannot_sell_when_yesterday_is_empty():
    n = 40
    close = np.linspace(12, 10, n)
    orders = replay_intraday_day(
        close, close, close, close, _clock(931, n),
        kind="macd", base=1000, sellable=0, params={"trade_n": 100},
    )
    assert all(item["side"] != "sell" for item in orders)


def test_band_fills_both_sides_once_with_equal_qty():
    # 开盘 10，随后一根冲到 10.2 再一根落到 9.8。
    open_ = np.array([10.0, 10.1, 9.9])
    high = np.array([10.05, 10.2, 10.0])
    low = np.array([9.95, 10.05, 9.8])
    close = np.array([10.0, 10.15, 9.85])
    clock = np.array([931, 940, 950])
    orders = replay_intraday_day(
        open_, high, low, close, clock,
        kind="band", base=10000, sellable=10000,
        params={"sell_pct": 0.015, "buy_pct": 0.015, "fee_floor": 0.008, "target_yuan": 10000, "max_sell_frac": 0.5},
    )
    assert [item["side"] for item in orders] == ["sell", "buy"]
    assert orders[0]["qty"] == orders[1]["qty"] == 1000
    assert orders[0]["price"] == 10.0 * 1.015
    assert orders[1]["price"] == 10.0 * 0.985
    assert orders[-1]["shares_after"] == 10000


def test_portfolio_establishes_base_on_day_one_and_sells_only_the_next_day():
    day0 = date(2026, 10, 21)
    day1 = date(2026, 10, 22)
    n = 36
    clock = _clock(931, n)

    def _frame(day: date, closes: np.ndarray) -> pl.DataFrame:
        return pl.DataFrame({
            "symbol": ["000001.SZ"] * len(closes),
            "datetime": [datetime(day.year, day.month, day.day, int(h) // 100, int(h) % 100) for h in clock],
            "open": closes,
            "high": closes + 0.2,
            "low": closes - 0.2,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
            "amount": np.full(len(closes), 10000.0),
        })

    frames = {
        day0: _frame(day0, np.full(n, 10.0)),
        day1: _frame(day1, np.linspace(12, 10, n)),
    }

    class Repo:
        def list_minute_dates(self, start, end, asset_type="stock"):
            return [day for day in (day0, day1) if start <= day <= end]

        def get_minute_by_dates(self, symbols, dates, asset_type="stock"):
            return frames[dates[0]]

    result = simulate_base_t(
        Repo(),
        kind="base_t_macd",
        symbols=["000001.SZ"],
        start=day0,
        end=day1,
        params={"base_shares": 1000, "trade_n": 100},
        initial_capital=100_000,
        buy_cost=0.0,
        sell_cost=0.0,
        max_positions=3,
        max_exposure=1.0,
        include_curves=False,
        include_trades=True,
        include_per_symbol=False,
        include_monte_carlo=False,
    )
    fills = result.stats["fills"]
    assert fills[0]["date"] == day0.isoformat()
    assert fills[0]["reason"] == "base"
    assert fills[0]["qty"] == 1000
    assert all(item["date"] != day0.isoformat() or item["reason"] == "base" for item in fills)
    sells = [item for item in fills if item["side"] == "sell" and item["reason"] == "macd"]
    assert sells and sells[0]["date"] == day1.isoformat()


def test_seed_files_load_as_minute_strategies():
    seeds = __import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "strategy" / "seeds"
    for name in ("intraday_base_t_macd.py", "intraday_base_t_band.py"):
        AIStrategyGenerator._validate_safety((seeds / name).read_text(encoding="utf-8"))
    engine = StrategyEngine(strategy_dirs=[seeds])
    assert not engine.load_errors(), engine.load_errors()
    for strategy_id, kind in (
        ("intraday_base_t_macd", "base_t_macd"),
        ("intraday_base_t_band", "base_t_band"),
    ):
        strategy = engine.get(strategy_id)
        assert strategy.execution_backend == "minute_filter"
        assert strategy.meta["timeframes"] == ["1m"]
        assert strategy.meta["scaled_execution"] == kind


def test_scan_reports_the_last_action_of_the_day():
    from app.backtest.swing_rules import scan_intraday_base_t

    day = date(2026, 10, 22)
    n = 36
    clock = _clock(931, n)
    # 前 35 根走平，最后一根抬高，MACD 线应为正。
    close = np.concatenate([np.full(35, 10.0), [10.8]])
    df = pl.DataFrame({
        "symbol": ["000001.SZ"] * n,
        "datetime": [datetime(day.year, day.month, day.day, int(h) // 100, int(h) % 100) for h in clock],
        "open": close,
        "high": close,
        "low": close,
        "close": close,
    })
    out = scan_intraday_base_t(df, {"base_shares": 1000, "trade_n": 100}, kind="macd")
    assert out.height == 1
    assert out["t_side"][0] == "buy"
    assert out["signal_base_t_buy"][0] is True
