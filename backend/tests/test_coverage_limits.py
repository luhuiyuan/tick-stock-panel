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
