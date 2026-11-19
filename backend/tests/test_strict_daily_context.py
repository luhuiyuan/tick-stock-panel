"""严格上下文来源校验：不信任legacy补零及未审计市场历史。"""

from datetime import date, timedelta

import polars as pl
import pytest

from app.services.strict_daily_context import attach_limit_facts, attach_market_facts
from app.strategy.strict_signal_data import EVENT_FIELDS, MARKET_FIELDS

DAY = date(2026, 1, 5)


def prices(close=11.0, high=11.0):
    return pl.DataFrame(
        {
            "symbol": ["X"],
            "date": [DAY],
            "raw_close": [close],
            "raw_high": [high],
            "consecutive_limit_ups": [9],
        }
    )


@pytest.mark.parametrize("limit", [10.5, 11.0, 12.0, 13.0])
def test_official_limit_not_assumed_board_rate(limit):
    inst = pl.DataFrame({"symbol": ["X"], "as_of": [DAY], "limit_up": [limit]})
    row = attach_limit_facts(prices(limit, limit), inst).row(0, named=True)
    assert row["strict_close_limit"] == 1
    assert row["strict_broken_limit"] == 0
    assert row["strict_limit_streak"] is None
    row = attach_limit_facts(prices(limit - 0.1, limit), inst).row(0, named=True)
    assert row["strict_close_limit"] == 0
    assert row["strict_broken_limit"] == 1
    assert row["strict_limit_streak"] == 0


@pytest.mark.parametrize("case", ["stale", "duplicate", "missing", "invalid", "no_limit"])
def test_current_snapshot_has_no_historical_proof(case):
    inst = pl.DataFrame({"symbol": ["X"], "as_of": [DAY], "limit_up": [11.0]})
    df = prices()
    if case == "stale":
        inst = inst.with_columns(pl.lit(DAY - timedelta(days=1)).alias("as_of"))
    if case == "duplicate":
        inst = pl.concat([inst, inst])
    if case == "missing":
        inst = inst.drop("as_of")
    if case == "invalid":
        df = prices(11.0, 10.0)
    if case == "no_limit":
        inst = inst.with_columns(pl.lit(10000.0).alias("limit_up"))
    out = attach_limit_facts(df, inst)
    assert out.height == 1
    for field in EVENT_FIELDS:
        assert out[field][0] == (0.0 if case == "no_limit" else None)


def history():
    return pl.DataFrame(
        {
            "date": [DAY],
            "source_complete": [True],
            "source_version": ["audit-v1"],
            "available_date": [DAY],
            "limit_up": [120.0],
            "broken_limit": [30.0],
            "state": ["weak"],
        }
    )


@pytest.mark.parametrize(
    "case", ["valid", "legacy", "duplicate", "future", "incomplete", "zero", "negative"]
)
def test_market_source_contract(case):
    h = history()
    if case == "legacy":
        h = h.drop("source_complete")
    if case == "duplicate":
        h = pl.concat([h, h])
    if case == "future":
        h = h.with_columns(pl.lit(DAY + timedelta(days=1)).alias("available_date"))
    if case == "incomplete":
        h = h.with_columns(pl.lit(False).alias("source_complete"))
    if case == "zero":
        h = h.with_columns(pl.lit(0.0).alias("limit_up"), pl.lit(0.0).alias("broken_limit"))
    if case == "negative":
        h = h.with_columns(pl.lit(-1.0).alias("limit_up"))
    out = attach_market_facts(prices(), h, set(MARKET_FIELDS))
    assert out.height == 1
    assert out["strict_market_cold"][0] is None  # 无梯队历史，不能沿用旧phase标签
    assert out["strict_market_seal"][0] == (0.8 if case == "valid" else None)
    assert out["strict_market_limits"][0] == (
        120.0 if case == "valid" else 0.0 if case == "zero" else None
    )


def test_historical_official_snapshots_streak_requires_anchor_and_calendar():
    days = [DAY + timedelta(days=i) for i in range(5)]
    df = pl.concat(
        [
            prices(10.0 if i == 0 else 11.0).with_columns(pl.lit(d).alias("date"))
            for i, d in enumerate(days)
        ]
    )
    inst = pl.DataFrame({"symbol": ["X"] * 5, "as_of": days, "limit_up": [11.0] * 5})
    out = attach_limit_facts(df, inst, trading_dates=days)
    assert out["strict_limit_streak"].to_list() == [0.0, 1.0, 2.0, 3.0, 4.0]
    missing = inst.filter(pl.col("as_of") != days[2])
    out = attach_limit_facts(df, missing, trading_dates=days)
    assert out["strict_limit_streak"].to_list() == [0.0, 1.0, None, None, None]
    out = attach_limit_facts(df.tail(4), inst, trading_dates=days)
    assert out["strict_limit_streak"].null_count() == 4
    out = attach_limit_facts(df.filter(pl.col("date") != days[2]), inst, trading_dates=days)
    assert out["strict_limit_streak"].to_list() == [0.0, 1.0, None, None]


def test_market_phase_strict_prefix_and_untrusted_gaps(monkeypatch):
    from app.services import trading_day
    from app.services.market_phase import classify_phase_series

    days = [DAY + timedelta(days=i) for i in range(12)]
    monkeypatch.setattr(trading_day, "cached_trading_calendar", lambda: days)
    h = pl.concat(
        [
            history().with_columns(pl.lit(d).alias("date"), pl.lit(d).alias("available_date"))
            for d in days
        ]
    ).with_columns(
        pl.lit(1.0).alias("max_consecutive"),
        pl.lit(5.0).alias("first_board"),
        pl.lit(0.0).alias("ge2_count"),
        pl.lit(0.1).alias("promo_rate"),
        pl.lit(0.8).alias("seal_rate"),
    )
    for size in (1, 5, 10):
        full = classify_phase_series(h, strict=True)
        part = classify_phase_series(h.head(size), strict=True)
        assert full["phase"].head(size).equals(part["phase"])
    frame = pl.DataFrame({"symbol": ["X"] * 12, "date": days})
    out = attach_market_facts(frame, h, {"strict_market_cold"})
    assert out["strict_market_cold"].null_count() == 0
    for invalid in (
        h.filter(pl.col("date") != days[4]),
        h.with_columns(
            pl.when(pl.col("date") == days[4])
            .then(-1.0)
            .otherwise(pl.col("promo_rate"))
            .alias("promo_rate")
        ),
    ):
        out = attach_market_facts(frame, invalid, {"strict_market_cold"})
        assert out["strict_market_cold"].tail(8).null_count() == 8
    leading = h.with_columns(
        pl.when(pl.col("date") < days[2])
        .then(None)
        .otherwise(pl.col("promo_rate"))
        .alias("promo_rate")
    )
    out = classify_phase_series(leading, strict=True)
    assert out["phase"].head(2).null_count() == 2
