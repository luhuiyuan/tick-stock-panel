from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import MatcherConfig, SimulationOptions
from app.backtest.matrix import build_market_data_matrix, build_market_matrix
from app.backtest.scaled_execution import simulate_scaled
from app.backtest.swing_rules import (
    grid_decide,
    mean_reversion_masks,
    n_rebound_masks,
    sellable_shares,
    turtle_add_quantity,
    turtle_buy_ok,
    turtle_sell_signal,
)
from app.strategy.ai_generator import AIStrategyGenerator
from app.strategy.engine import StrategyEngine


def _panel(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def _bar(day: int, price: float, **overrides) -> dict:
    return {
        "symbol": overrides.get("symbol", "000001.SZ"),
        "name": "测试",
        "date": date(2024, 1, 1) + timedelta(days=day),
        "open": overrides.get("open", price),
        "high": overrides.get("high", price),
        "low": overrides.get("low", price),
        "close": overrides.get("close", price),
        "volume": overrides.get("volume", 100_000.0),
        "signal_limit_up": overrides.get("signal_limit_up", False),
        "signal_limit_down": overrides.get("signal_limit_down", False),
    }


def _matrix(rows: list[dict]):
    panel = _panel(rows)
    return build_market_matrix(panel, panel["close"] > 0, None)


def _quiet():
    return SimulationOptions(
        include_monte_carlo=False,
        include_curves=False,
        include_per_symbol_stats=False,
    )


def test_grid_decide_matches_akquant_one_lot_steps():
    side, qty = grid_decide(10, None, 0, grid_pct=0.03, trade_lot=100, initial_lots=10)
    assert (side, qty) == ("buy", 1000)
    side, qty = grid_decide(9.7, 10, 1000, grid_pct=0.03, trade_lot=100, initial_lots=10)
    assert (side, qty) == ("buy", 100)
    side, qty = grid_decide(9.7 * 1.03, 9.7, 1100, grid_pct=0.03, trade_lot=100, initial_lots=10)
    assert (side, qty) == ("sell", 100)
    side, qty = grid_decide(12, 10, 50, grid_pct=0.03, trade_lot=100, initial_lots=10)
    assert side == "hold"


def test_grid_book_adds_one_lot_and_retries_when_cash_is_short():
    rows = [
        _bar(0, 10),
        _bar(1, 10),
        _bar(2, 9.7),
        _bar(3, 9.991),
    ]
    matrix = _matrix(rows)
    result = simulate_scaled(
        matrix,
        MatcherConfig(
            matching="close_t", entry_fill="close_t", exit_fill="close_t",
            fees_pct=0, slippage_bps=0, stamp_tax_pct=0,
            initial_capital=1_000_000, max_positions=5,
        ),
        "grid",
        {"grid_pct": 0.03, "trade_lot": 100, "initial_lots": 10},
        options=_quiet(),
    )
    buys = [item for item in result.stats["fills"] if item["side"] == "buy"]
    sells = [item for item in result.stats["fills"] if item["side"] == "sell"]
    assert [item["qty"] for item in buys] == [1000, 100]
    assert [item["qty"] for item in sells] == [100]
    assert sells[0]["shares_after"] == 1000

    short = simulate_scaled(
        _matrix([_bar(0, 10), _bar(1, 4)]),
        MatcherConfig(
            matching="close_t", entry_fill="close_t", exit_fill="close_t",
            fees_pct=0, slippage_bps=0, stamp_tax_pct=0,
            initial_capital=5_000, max_positions=5,
        ),
        "grid",
        {"grid_pct": 0.03, "trade_lot": 100, "initial_lots": 10},
        options=_quiet(),
    )
    assert short.stats["fills"][0]["date"].endswith("01-02")
    assert short.stats["fills"][0]["qty"] == 1000
    assert short.stats["fills"][0]["price"] == 4


def test_grid_does_not_buy_a_locked_limit_up_bar():
    rows = [
        _bar(0, 10),
        _bar(1, 9.7, open=9.7, high=9.7, low=9.7, close=9.7, signal_limit_up=True),
        _bar(2, 9.7),
    ]
    result = simulate_scaled(
        _matrix(rows),
        MatcherConfig(
            matching="close_t", entry_fill="close_t", exit_fill="close_t",
            fees_pct=0, slippage_bps=0, stamp_tax_pct=0, initial_capital=1_000_000,
        ),
        "grid",
        {"grid_pct": 0.03, "trade_lot": 100, "initial_lots": 10},
        options=_quiet(),
    )
    add_days = [item["date"] for item in result.stats["fills"] if item["qty"] == 100]
    assert add_days == [(date(2024, 1, 1) + timedelta(days=2)).isoformat()]


def test_sellable_shares_block_same_bar_round_trip():
    assert sellable_shares([{"time_id": 5, "qty": 100}], 5) == 0
    assert sellable_shares([{"time_id": 5, "qty": 300}, {"time_id": 6, "qty": 100}], 6) == 300


def test_turtle_add_quantity_and_signal_rules_match_khunter():
    assert turtle_add_quantity(1000, 0) == 500
    assert turtle_add_quantity(1500, 1) == 500
    assert turtle_add_quantity(5000, 3) == 1000
    assert turtle_buy_ok(
        open_=10, high=11, close=10.8, prev_close=10.2, up=10.5, ma20=10,
    )
    assert not turtle_buy_ok(
        open_=10, high=12, close=10.2, prev_close=10.1, up=10.5, ma20=10,
    )
    sell, reason = turtle_sell_signal(low=8, down=9, atr=1, entry_price=10, exit_atr=2, n_exit=5)
    assert sell and "低点" in reason
    sell, reason = turtle_sell_signal(low=7.9, down=7, atr=1, entry_price=10, exit_atr=2, n_exit=5)
    assert sell and reason == "ATR止损"


def test_turtle_book_adds_four_times_then_stops_and_exits():
    rows = []
    for day in range(40):
        close = 10 + day * 0.08
        rows.append(_bar(
            day, close,
            open=close - 0.02,
            high=close + 0.03,
            low=close - 0.03,
        ))
    # 跌破 5 日低点，触发通道卖出。
    last = 10 + 39 * 0.08
    rows.append(_bar(40, last - 2, open=last - 1.5, high=last - 1, low=last - 3))
    rows.append(_bar(41, last - 2.2, open=last - 2, high=last - 1.8, low=last - 2.5))
    result = simulate_scaled(
        _matrix(rows),
        MatcherConfig(
            matching="open_t+1", fees_pct=0, slippage_bps=0, stamp_tax_pct=0,
            initial_capital=1_000_000, max_positions=3,
        ),
        "turtle",
        {},
        options=_quiet(),
    )
    buys = [item["qty"] for item in result.stats["fills"] if item["side"] == "buy"]
    assert buys[0] >= 100
    assert len(buys) == 5
    assert buys[1:] == [turtle_add_quantity(sum(buys[:i]), i - 1) for i in range(1, 5)]
    sells = [item for item in result.stats["fills"] if item["side"] == "sell"]
    assert sells and ("低点" in sells[0]["reason"] or "ATR" in sells[0]["reason"])
    assert sells[0]["qty"] == sum(buys)


def test_mean_reversion_uses_population_bands_and_wilder_rsi():
    close = np.concatenate([
        np.full(30, 10.0),
        np.linspace(10, 7, 8),
        np.full(15, 7.0),
    ])
    entry, exit_ = mean_reversion_masks(close, {})
    assert entry.ndim == 2 and entry.shape[1] == 1
    assert entry.any()
    assert exit_.any()
    pandas = pytest.importorskip("pandas")
    series = pandas.Series(close)
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, np.nan))
    from app.backtest.swing_rules import bollinger_lower_mid, wilder_rsi
    got = wilder_rsi(close, 14)[:, 0]
    finite = np.isfinite(rsi.to_numpy()) & np.isfinite(got)
    np.testing.assert_allclose(got[finite], rsi.to_numpy()[finite], rtol=1e-6, atol=1e-6)
    lower, mid = bollinger_lower_mid(close, 20, 2)
    pandas_mid = series.rolling(20, min_periods=20).mean()
    pandas_std = series.rolling(20, min_periods=20).std(ddof=0)
    np.testing.assert_allclose(mid[19:, 0], pandas_mid.to_numpy()[19:], rtol=1e-6, atol=1e-6)
    np.testing.assert_allclose(
        lower[19:, 0],
        (pandas_mid - 2 * pandas_std).to_numpy()[19:],
        rtol=1e-6,
        atol=1e-6,
    )


def test_n_rebound_accepts_the_screener_example_shape():
    rows_n = 70
    close = np.full(rows_n, 10.0)
    open_ = np.full(rows_n, 10.0)
    high = np.full(rows_n, 10.2)
    low = np.full(rows_n, 9.8)
    volume = np.full(rows_n, 10_000.0)
    # 60 日区间压在低位，第 62 天一根 10% 阳线，之后缩量不破开盘。
    low[:60] = 9.0
    high[:60] = 13.0
    zt = 62
    open_[zt] = 10.0
    close[zt] = 11.0
    high[zt] = 11.1
    low[zt] = 9.9
    volume[zt] = 50_000
    for day in range(zt + 1, rows_n):
        open_[day] = 10.8
        close[day] = 10.9
        high[day] = 11.0
        low[day] = 10.7
        volume[day] = 20_000
    entry, _exit = n_rebound_masks(open_, high, low, close, volume, {})
    assert entry[zt + 1, 0]
    assert not entry[zt, 0]
    close[-1] = 9.5
    entry2, exit2 = n_rebound_masks(open_, high, low, close, volume, {})
    assert not entry2[-1, 0]
    assert exit2[-1, 0]


def test_new_seed_files_pass_safety_and_load():
    seeds = Path(__file__).resolve().parents[1] / "app" / "strategy" / "seeds"
    names = [
        "grid_trading.py",
        "turtle_channel.py",
        "boll_rsi_revert.py",
        "n_limit_pullback.py",
        "first_limit_up.py",
    ]
    for name in names:
        AIStrategyGenerator._validate_safety((seeds / name).read_text(encoding="utf-8"))
    engine = StrategyEngine(strategy_dirs=[seeds])
    assert not engine.load_errors(), engine.load_errors()
    for strategy_id in ("grid_trading", "turtle_channel", "boll_rsi_revert", "n_limit_pullback", "first_limit_up"):
        assert engine.get(strategy_id).execution_backend == "matrix_native"


def test_first_limit_signal_is_exactly_one_board(tmp_path):
    from app.strategy.seeds.first_limit_up import FirstLimitUpMatrixStrategy

    rows = []
    for day in range(65):
        rows.append({
            "symbol": "000001.SZ",
            "name": "测试",
            "date": date(2024, 1, 1) + timedelta(days=day),
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1000.0,
            "signal_limit_up": day == 64,
            "signal_limit_down": False,
            "consecutive_limit_ups": 1.0 if day == 64 else 0.0,
        })
    market = build_market_data_matrix(pl.DataFrame(rows), field_columns={"consecutive_limit_ups"})
    signals = FirstLimitUpMatrixStrategy().compute_signals(market, {})
    assert signals.entry[-1, 0] == 1
    assert signals.entry[-2, 0] == 0


def test_paper_auto_grid_and_turtle_use_delta_quantities(tmp_path, monkeypatch):
    from app.strategy import paper, paper_auto

    today = date(2026, 10, 23)
    monkeypatch.setattr(paper, "cn_today", lambda: today)
    paper.create_account(tmp_path, 1_000_000)
    paper_auto.create_auto_rule(tmp_path, {
        "name": "跟网格",
        "match_kind": "strategy",
        "match_id": "grid_trading",
        "side": "auto",
        "size_mode": "fixed_amount",
        "size_value": 50000,
        "order_type": "next_open",
        "cooldown_days": 0,
    })
    flat = {
        "source": "strategy", "strategy_id": "grid_trading", "type": "buy_signal",
        "symbol": "000001.SZ", "price": 10.0,
    }
    opened = paper_auto.on_rule_events(tmp_path, [flat])
    assert opened[0]["qty"] == 1000

    positions = {
        "positions": {
            "000001.SZ": {
                "asset_type": "stock",
                "qty": 1000,
                "avg_cost": 10.0,
                "available_qty": 1000,
                "lots": [
                    {"date": "2026-10-22", "qty": 1000},
                    {"date": "2026-10-22", "qty": 100},
                ],
            }
        }
    }
    import json
    path = tmp_path / "paper" / "default" / "positions.json"
    # 账户目录由 paper 决定，按实际根目录写。
    from app.strategy.paper import _root
    path = _root(tmp_path) / "positions.json"
    path.write_text(json.dumps(positions), encoding="utf-8")
    added = paper_auto.on_rule_events(tmp_path, [flat])
    assert added[0]["qty"] == 100
    sold = paper_auto.on_rule_events(tmp_path, [{**flat, "type": "sell_signal"}])
    assert sold[0]["qty"] == 100

    paper_auto.create_auto_rule(tmp_path, {
        "name": "跟海龟",
        "match_kind": "strategy",
        "match_id": "turtle_channel",
        "side": "auto",
        "size_mode": "fixed_amount",
        "size_value": 50000,
        "order_type": "next_open",
        "cooldown_days": 0,
    })
    turtle = paper_auto.on_rule_events(tmp_path, [{
        "source": "strategy", "strategy_id": "turtle_channel", "type": "buy_signal",
        "symbol": "000001.SZ", "price": 10.0,
    }])
    assert turtle[0]["qty"] == turtle_add_quantity(1100, 1)
