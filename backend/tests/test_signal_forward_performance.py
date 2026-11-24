from datetime import date, timedelta

import polars as pl
import pytest

from app.services.signal_forward_performance import compute_forward_performance, summarize_monthly

DAYS = [date(2026, 1, 28) + timedelta(days=i) for i in range(23)]
SIGNAL = {
    "id": "x",
    "name": "X",
    "kind": "entry",
    "enabled": True,
    "timeframe": "daily",
    "conditions": [{"left": "amount", "op": ">", "right": "1"}],
}


def bars(flags=None):
    flags = flags or {1: 2, 2: 2, 6: 2, 13: 2}
    return pl.DataFrame(
        [
            dict(
                symbol=symbol,
                date=day,
                open=100.0,
                raw_open=10.0,
                raw_close=10.0 + (i if symbol == "A" else 0) * 0.1,
                raw_high=10.1 + (i if symbol == "A" else 0) * 0.1,
                raw_low=9.9 + (i if symbol == "A" else 0) * 0.1,
                official_limit_up=11.0,
                close=100.0 + (i if symbol == "A" else 0),
                high=200.0 + i,
                low=99.0 - i,
                amount=flags.get(i, 0) if symbol == "A" else 0,
            )
            for symbol in ("A", "B")
            for i, day in enumerate(DAYS)
        ]
    )


def run(frame, split=12, catalog=None, ids=("x",)):
    return compute_forward_performance(
        frame,
        catalog or {"x": SIGNAL},
        ids,
        DAYS[1],
        DAYS[-1],
        DAYS[split],
        trading_dates=DAYS,
        min_benchmark=2,
    )


def test_market_windows_adjustment_and_split():
    events, summary = run(bars())
    assert events["signal_date"].to_list() == [DAYS[i] for i in (1, 6, 13)]
    first, second, third = events.to_dicts()
    assert first["entry_date"] == DAYS[2]
    assert first["exit_date_1d"] == DAYS[3]
    assert first["exit_date_5d"] == DAYS[7]
    assert first["exit_date_10d"] == DAYS[12]
    assert first["execution_status"] == "executable"
    assert first["entry_limit_source"] == "dated_instrument"
    assert first["return_1d"] == pytest.approx(0.03)
    assert first["return_5d"] == pytest.approx(0.07)
    # This discovery event's 10-session exit lands on the split boundary and is excluded.
    assert first["return_10d"] is None
    extended, _ = run(bars({1: 2}), split=20)
    assert extended[0]["return_10d"][0] == pytest.approx(0.12)
    assert first["max_favorable_5d"] == pytest.approx(1.07)
    assert first["max_adverse_5d"] == pytest.approx(-0.08)
    assert first["max_favorable_1d"] == pytest.approx(1.03)
    assert first["max_adverse_1d"] == pytest.approx(-0.04)
    assert first["benchmark_5d"] == pytest.approx(0.035)
    assert first["excess_return_5d"] == pytest.approx(0.035)
    assert second["return_10d"] is None
    assert third["return_10d"] is None
    assert summary.height == 6
    assert summarize_monthly(events).filter(pl.col("horizon") == 5)["month"].to_list() == [
        "2026-01",
        "2026-02",
        "2026-02",
    ]


def test_missing_entry_session_does_not_slide_and_missing_middle_blocks_horizon():
    frame = bars().filter(~((pl.col("symbol") == "A") & pl.col("date").is_in([DAYS[2], DAYS[9]])))
    first, second, _ = run(frame)[0].to_dicts()
    assert first["entry_date"] == DAYS[2]
    assert first["execution_reason"] == "missing_next_session_bar"
    assert first["return_1d"] is None
    assert second["return_1d"] == pytest.approx(0.08)
    assert second["return_5d"] is None


def test_discovery_exit_cannot_cross_split_after_t1_shift():
    events, _ = run(bars({10: 2}), split=12)
    event = events.row(0, named=True)
    assert event["signal_date"] == DAYS[10]
    assert event["entry_date"] == DAYS[11]
    assert event["exit_date_1d"] == DAYS[12]
    assert event["return_1d"] is None
    assert event["return_5d"] is None


def test_unknown_not_a_false_and_open_requires_dated_evidence():
    f = bars({1: 2, 2: None, 3: 2, 5: 2, 11: 2}).with_columns(
        pl.when(pl.col("date") == DAYS[2])
        .then(None)
        .when(pl.col("date") == DAYS[6])
        .then(10.0)
        .otherwise(pl.col("official_limit_up"))
        .alias("official_limit_up")
    )
    events, _ = run(f)
    assert events["signal_date"].to_list() == [DAYS[i] for i in (1, 5, 11)]
    assert events["execution_status"].to_list() == ["unknown", "not_executable", "unknown"]
    assert events["execution_reason"][-1] == "entry_outside_split"
    assert events["return_1d"].null_count() == 3


def test_missing_dated_limit_falls_back_to_rule_after_warmup():
    f = bars({7: 2}).with_columns(
        pl.when(pl.col("date") == DAYS[8])
        .then(None)
        .otherwise(pl.col("official_limit_up"))
        .alias("official_limit_up")
    )
    events, _ = run(f)
    assert events.height == 1
    assert events["execution_status"][0] == "executable"
    assert events["entry_limit_source"][0] == "rule_inferred"


def test_binglin_computes_from_history_and_ignores_future_bars():
    from app.strategy.premarket_signal_definitions import SIGNALS

    definition = next(sig for sig in SIGNALS if sig["id"] == "t_binglin")
    catalog = {"t_binglin": definition}
    frame = (
        bars()
        .with_columns(
            pl.col("date").replace_strict(dict(zip(DAYS, range(len(DAYS)), strict=True))).alias("i")
        )
        .with_columns(
            (100 + (pl.col("i") - 18).clip(lower_bound=0)).cast(pl.Float64).alias("close"),
            pl.when(pl.col("i") == 5)
            .then(120.0)
            .otherwise(104 + (pl.col("i") - 18).clip(lower_bound=0))
            .alias("high"),
        )
    )

    def triggers(data):
        return run(data, catalog=catalog, ids=("t_binglin",))[0]["signal_date"].to_list()

    assert triggers(frame) == [DAYS[21], DAYS[21]]
    assert triggers(frame.filter(pl.col("date") <= DAYS[21])) == triggers(frame)
    assert triggers(
        frame.with_columns(
            pl.when(pl.col("date") > DAYS[21]).then(1.0).otherwise(pl.col("close")).alias("close")
        )
    ) == triggers(frame)
    assert triggers(frame.filter(pl.col("date") >= DAYS[2])) == []


def test_composite_compiles_from_atom_and_rejects_unproven_dependency():
    comp = {**SIGNAL, "id": "c", "type": "composite", "conditions": [], "expr": {"ref": "x"}}
    events, _ = run(bars(), catalog={"x": SIGNAL, "c": comp}, ids=("c",))
    assert events["signal_type"].to_list() == ["composite"] * 3
    dependent = {**SIGNAL, "conditions": [{"left": "limit_up_price", "op": ">", "right": "1"}]}
    with pytest.raises(ValueError, match="历史依赖未获时点验证"):
        run(bars(), catalog={"x": dependent, "c": comp}, ids=("c",))


def test_adjusted_absolute_threshold_is_excluded_but_relative_price_is_allowed():
    absolute = {**SIGNAL, "conditions": [{"left": "close", "op": ">", "right": "100"}]}
    with pytest.raises(ValueError, match="前复权绝对价"):
        run(bars(), catalog={"x": absolute})
    relative = {**SIGNAL, "conditions": [{"left": "close", "op": ">", "right": "field:open"}]}
    earlier = run(bars(), catalog={"x": relative})[0]["signal_date"].to_list()
    changed = bars().with_columns(*(pl.col(k) * 0.5 for k in ("close", "open", "low", "high")))
    assert run(changed, catalog={"x": relative})[0]["signal_date"].to_list() == earlier == [DAYS[1]]


def test_no_confirmed_first_trigger_preserves_result_schema():
    frame = bars({i: 2 for i in range(len(DAYS))})
    events, summary = run(frame)
    assert events.is_empty() and "excess_return_10d" in events.columns
    assert summary.is_empty() and "unknown_execution_count" in summary.columns
