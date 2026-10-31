"""尾盘持股次日 9:30–10:00 择机卖。"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import polars as pl

from app.strategy import paper, paper_auto
from app.strategy.overnight_exit import (
    EXIT_POLICY,
    SessionBar,
    decide_session,
    schedule_exits,
)
from app.strategy.paper_overnight import evaluate_account, load_plans
from app.tickflow.repository import DataStore, KlineRepository

DAY = date(2026, 10, 30)
PREV = 10.0
SYMBOL = "000001.SZ"


def _bar(hour: int, minute: int, close: float, *, high=None, low=None, open_=None, volume=1_000.0):
    high = close if high is None else high
    low = close if low is None else low
    return SessionBar(
        at=datetime(DAY.year, DAY.month, DAY.day, hour, minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        amount=close * volume * 100.0,
    )


def _limits(prev: float = PREV):
    return paper.limit_prices(prev, SYMBOL, "stock")


def test_gap_up_sells_on_the_first_bar():
    up, down = _limits()
    decision = decide_session([
        _bar(9, 30, 10.25, open_=10.30),
        _bar(10, 0, 10.40),
    ], prev_close=PREV, limit_up=up, limit_down=down)
    assert decision.action == "sell"
    assert decision.reason == "gap_sell"
    assert decision.price == 10.25


def test_small_gap_sells_the_push():
    up, down = _limits()
    decision = decide_session([
        _bar(9, 30, 10.10, open_=10.10),
        _bar(9, 35, 10.20, high=10.22),
    ], prev_close=PREV, limit_up=up, limit_down=down)
    assert decision.reason == "push_sell"
    assert decision.price == 10.20


def test_gap_down_sells_when_price_breaks_vwap_and_previous_close():
    up, down = _limits()
    bar = _bar(9, 31, 9.70, open_=9.80, high=9.90, low=9.70)
    bar = SessionBar(
        at=bar.at, open=9.80, high=9.85, low=9.70, close=9.70,
        volume=1_000.0, amount=9.85 * 1_000 * 100,
    )
    decision = decide_session([bar], prev_close=PREV, limit_up=up, limit_down=down)
    assert decision.reason == "vwap_stop"


def test_deadline_sells_when_nothing_else_hits():
    up, down = _limits()
    bars = [_bar(9, 30, 10.05, open_=10.05)]
    bars.append(_bar(10, 0, 10.06, high=10.06, low=10.04))
    decision = decide_session(bars, prev_close=PREV, limit_up=up, limit_down=down)
    assert decision.reason == "deadline"
    assert decision.at.minute == 0 and decision.at.hour == 10


def test_sealed_limit_up_waits_for_the_bar_after_it_opens():
    up, down = _limits()
    bars = [
        _bar(9, 30, up, open_=up),
        _bar(10, 0, up),
        _bar(10, 5, 10.95, high=up, low=10.90),
        _bar(10, 6, 10.88, high=10.96, low=10.85),
    ]
    decision = decide_session(bars, prev_close=PREV, limit_up=up, limit_down=down)
    assert decision.reason == "limit_up_open"
    assert decision.price == 10.88


def test_sealed_limit_up_sells_at_the_close_if_it_never_opens():
    up, down = _limits()
    decision = decide_session([
        _bar(9, 30, up, open_=up),
        _bar(10, 0, up),
        _bar(15, 0, up),
    ], prev_close=PREV, limit_up=up, limit_down=down)
    assert decision.reason == "limit_up_close"
    assert decision.price == up


def test_missing_deadline_bar_is_not_priced_from_the_daily_bar():
    up, down = _limits()
    decision = decide_session([
        _bar(9, 30, 10.05, open_=10.05),
    ], prev_close=PREV, limit_up=up, limit_down=down)
    assert decision.action == "uncovered"


def test_limit_down_day_defers_without_a_close_fill():
    up, down = _limits()
    decision = decide_session([
        _bar(9, 30, down, open_=down),
        _bar(10, 0, down),
        _bar(15, 0, down),
    ], prev_close=PREV, limit_up=up, limit_down=down)
    assert decision.action == "defer"


def test_schedule_drops_entries_without_minute_bars_and_scales_covered_ones():
    entry = np.zeros((2, 1), dtype=np.uint8)
    entry[0, 0] = 1
    raw = np.array([[10.0], [10.0]], dtype=np.float32)
    adj = np.array([[20.0], [20.0]], dtype=np.float32)
    calls = {"days": []}

    def bars_for(symbol, day):
        calls["days"].append(day)
        return None

    entry_out, exit_out, prices, _codes, stats = schedule_exits(
        ["2026-10-29", "2026-10-30"],
        [SYMBOL],
        entry,
        raw,
        adj,
        bars_for,
        lambda prev, symbol: paper.limit_prices(prev, symbol, "stock"),
    )
    assert entry_out[0, 0] == 0
    assert exit_out.sum() == 0
    assert stats["uncovered_entries"] == 1
    assert np.isnan(prices).all()

    def bars_for_ok(symbol, day):
        return [_bar(9, 30, 10.30, open_=10.30), _bar(10, 0, 10.20)]

    entry_out, exit_out, prices, codes, stats = schedule_exits(
        ["2026-10-29", "2026-10-30"],
        [SYMBOL],
        entry,
        raw,
        adj,
        bars_for_ok,
        lambda prev, symbol: paper.limit_prices(prev, symbol, "stock"),
    )
    assert entry_out[0, 0] == 1
    assert exit_out[1, 0] == 1
    # 原始卖出价 10.30，复权倍数 2
    assert abs(float(prices[1, 0]) - 20.60) < 1e-3
    assert stats["covered_entries"] == 1
    assert int(codes[1, 0]) == 0  # gap_sell


def _buy(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "cn_today", lambda: date(2026, 10, 29))
    paper.create_account(tmp_path, 1_000_000, slippage_bps=0)
    repo = KlineRepository(DataStore(tmp_path))
    repo.append_daily(pl.DataFrame({
        "symbol": [SYMBOL],
        "date": [date(2026, 10, 29)],
        "open": [10.0],
        "high": [10.0],
        "low": [10.0],
        "close": [10.0],
        "volume": [10_000.0],
        "amount": [100_000.0],
    }))
    order, err = paper.create_order(
        tmp_path, SYMBOL, "buy", qty=100, order_type="close",
        ref_price=10.0, exit_policy=EXIT_POLICY,
    )
    assert err is None
    with paper.PAPER_LOCK:
        fill = paper._fill_order(tmp_path, order, 10.0, "2026-10-29")
    assert fill is not None
    monkeypatch.setattr(paper, "cn_today", lambda: DAY)
    return order


def _quote(**overrides):
    up, down = _limits()
    quote = {
        "open": 10.30,
        "high": 10.30,
        "low": 10.20,
        "last": 10.25,
        "volume": 1_000.0,
        "amount": 10.25 * 1_000 * 100,
        "prev_close": PREV,
        "limit_up": up,
        "limit_down": down,
    }
    quote.update(overrides)
    return {SYMBOL: quote}


def test_paper_gap_sells_the_next_morning(tmp_path, monkeypatch):
    _buy(tmp_path, monkeypatch)
    events = evaluate_account(
        tmp_path, _quote(), account_id=paper.DEFAULT_ACCOUNT_ID,
        now=datetime(2026, 10, 30, 9, 31),
    )
    assert len(events) == 1
    assert events[0]["side"] == "sell"
    plans = load_plans(tmp_path)
    assert plans[0]["status"] == "done"
    assert plans[0]["reason"] == "gap_sell"
    assert paper.load_positions(tmp_path)[SYMBOL]["qty"] == 0


def test_paper_does_not_sell_into_a_limit_down(tmp_path, monkeypatch):
    _buy(tmp_path, monkeypatch)
    up, down = _limits()
    events = evaluate_account(
        tmp_path,
        _quote(open=down, high=down, low=down, last=down),
        account_id=paper.DEFAULT_ACCOUNT_ID,
        now=datetime(2026, 10, 30, 9, 40),
    )
    assert events == []
    assert load_plans(tmp_path)[0]["status"] == "armed"
    assert paper.load_positions(tmp_path)[SYMBOL]["qty"] == 100


def test_paper_holds_a_sealed_limit_up_then_sells_the_next_quote(tmp_path, monkeypatch):
    _buy(tmp_path, monkeypatch)
    up, down = _limits()
    sealed = _quote(open=up, high=up, low=up, last=up)
    assert evaluate_account(
        tmp_path, sealed, account_id=paper.DEFAULT_ACCOUNT_ID,
        now=datetime(2026, 10, 30, 10, 0),
    ) == []
    assert load_plans(tmp_path)[0]["phase"] == "hold_limit"
    opened = _quote(open=up, high=up, low=10.9, last=10.95)
    assert evaluate_account(
        tmp_path, opened, account_id=paper.DEFAULT_ACCOUNT_ID,
        now=datetime(2026, 10, 30, 10, 5),
    ) == []
    assert load_plans(tmp_path)[0]["phase"] == "sell_next"
    events = evaluate_account(
        tmp_path, _quote(open=up, high=up, low=10.8, last=10.85),
        account_id=paper.DEFAULT_ACCOUNT_ID,
        now=datetime(2026, 10, 30, 10, 6),
    )
    assert len(events) == 1
    assert load_plans(tmp_path)[0]["reason"] == "limit_up_open"


def test_auto_rule_buys_at_the_close_and_ignores_exit_signals(tmp_path):
    paper.create_account(tmp_path, 1_000_000, slippage_bps=0)
    paper_auto.create_auto_rule(tmp_path, {
        "name": "尾盘",
        "match_kind": "strategy",
        "match_id": "overnight_tail_hold",
        "side": "auto",
        "size_mode": "fixed_amount",
        "size_value": 1000,
        "order_type": "next_open",
    })
    created = paper_auto.on_rule_events(tmp_path, [
        {"source": "strategy", "strategy_id": "overnight_tail_hold", "type": "pool_entry", "symbol": SYMBOL, "price": 10.0},
        {"source": "strategy", "strategy_id": "overnight_tail_hold", "type": "pool_exit", "symbol": SYMBOL, "price": 10.0},
    ])
    assert len(created) == 1
    assert created[0]["side"] == "buy"
    assert created[0]["order_type"] == "close"
    assert created[0]["exit_policy"] == EXIT_POLICY
