from datetime import date, timedelta

import polars as pl

from app.research.l2_analog_features import (
    build_feature_snapshot,
    replay_feature_snapshots,
)


def _frame(count: int = 90) -> pl.DataFrame:
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(count)]
    closes = [10.0 + ((index % 9) - 4) * 0.08 + index * 0.015 for index in range(count)]
    return pl.DataFrame({
        "symbol": ["TEST"] * count,
        "date": dates,
        "open": [value - 0.03 for value in closes],
        "high": [value + 0.12 for value in closes],
        "low": [value - 0.12 for value in closes],
        "close": closes,
        "volume": [1000.0 + index for index in range(count)],
    })


def test_snapshot_contains_structured_levels_alignment_and_normalized_candles() -> None:
    snapshot = build_feature_snapshot(_frame(), symbol="TEST")
    assert snapshot is not None
    assert set(snapshot["levels"]) == {"L0", "L1", "L2"}
    assert "state_signature" in snapshot["alignment"]
    assert len(snapshot["recent_candles"]["range_atr"]) == 20
    assert snapshot["feature_version"] == "l2-analog-features-v1"


def test_replay_snapshot_is_not_affected_by_future_rows() -> None:
    original = _frame(100)
    altered = original.with_columns(
        pl.when(pl.col("date") > date(2024, 3, 1))
        .then(pl.col("close") * 10)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    first_original = replay_feature_snapshots(original, min_prefix_bars=60)[0]
    first_altered = replay_feature_snapshots(altered, min_prefix_bars=60)[0]
    assert first_original == first_altered


def test_l0_research_current_segment_is_explicitly_marked() -> None:
    snapshot = build_feature_snapshot(_frame(), symbol="TEST")
    assert snapshot is not None
    l0 = snapshot["levels"]["L0"]
    if l0["state"] is not None:
        assert l0["current_research_only"] is True
