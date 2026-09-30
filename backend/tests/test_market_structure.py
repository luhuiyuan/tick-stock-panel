from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from app.indicators.market_structure import compute_market_structure


def _frame(closes: list[float], atr: float = 0.5) -> pl.DataFrame:
    start = date(2026, 1, 1)
    return pl.DataFrame(
        {
            "symbol": ["600000.SH"] * len(closes),
            "date": [start + timedelta(days=i) for i in range(len(closes))],
            "open": closes,
            "high": [value + 0.2 for value in closes],
            "low": [value - 0.2 for value in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
            "atr_14": [atr] * len(closes),
        }
    )


def test_smc_swing_is_only_available_after_symmetric_confirmation_window() -> None:
    closes = [10, 11, 12, 11, 10, 11, 10, 9, 10, 9]
    result = compute_market_structure(
        _frame(closes), left_bars=2, right_bars=2, min_swing_atr=0.1
    )

    assert result["swing_points"]
    assert all(point["confirmed_date"] > point["date"] for point in result["swing_points"])
    assert all(point["confirmed_date"] <= "2026-01-10" for point in result["swing_points"])


def test_confirmed_history_is_prefix_stable_when_future_bars_are_appended() -> None:
    closes = [10, 11, 12, 11, 10, 11, 13, 12, 11, 12, 14, 13, 12, 13, 15, 14, 13]
    prefix = compute_market_structure(
        _frame(closes[:14]), left_bars=2, right_bars=2, min_swing_atr=0.1
    )
    extended = compute_market_structure(
        _frame(closes), left_bars=2, right_bars=2, min_swing_atr=0.1
    )

    prefix_points = [
        (item["type"], item["date"], item["confirmed_date"], item["price"])
        for item in prefix["swing_points"]
        if item["confirmed_date"] < "2026-01-12"
    ]
    extended_points = [
        (item["type"], item["date"], item["confirmed_date"], item["price"])
        for item in extended["swing_points"]
        if item["confirmed_date"] < "2026-01-12"
    ]
    assert extended_points == prefix_points


def test_smc_bos_is_mapped_to_the_actual_breaking_candle() -> None:
    closes = [10, 11, 12, 11, 10, 11, 13, 12, 11, 12, 14, 13, 12, 13, 15, 14, 13, 12, 11]
    result = compute_market_structure(
        _frame(closes), left_bars=2, right_bars=2, min_swing_atr=0.1
    )

    assert any(
        event["type"] == "bos_up"
        and event["date"] == "2026-01-11"
        and event["reference_price"] == 13.2
        for event in result["events"]
    )
    assert all(event["date"] != "2026-01-03" for event in result["events"])


def test_cluster_levels_are_references_without_fictional_lifetime() -> None:
    closes = [10, 11, 13, 11, 10, 9, 10, 12, 14, 12, 10, 8,
              10, 12, 13, 11, 10, 9, 10, 12, 14, 12, 10, 9,
              10, 11, 12, 11, 10, 10]
    result = compute_market_structure(_frame(closes), min_swing_atr=0.1)
    assert result["cluster_levels"]
    assert result["support_zones"] == result["resistance_zones"] == []
    assert all(set(level) == {"price", "peak_count", "side"} for level in result["cluster_levels"])
    assert all(level["peak_count"] >= 1 for level in result["cluster_levels"])
    assert all((level["price"] > closes[-1]) == (level["side"] == "resistance")
               for level in result["cluster_levels"])
    assert len([level for level in result["cluster_levels"] if level["side"] == "support"]) <= 3
    assert len([level for level in result["cluster_levels"] if level["side"] == "resistance"]) <= 3


def test_cluster_extrema_confirmation_requires_right_hand_bars() -> None:
    from app.indicators.market_structure import _cluster_reference_levels

    closes = [9, 10, 11, 12, 13, 15, 13, 12, 11, 10, 9]
    # The high at index 5 requires five subsequent candles with a window of 11.
    before = _cluster_reference_levels(_frame(closes[:10]))
    after = _cluster_reference_levels(_frame(closes))
    assert before == []
    # One extremum cannot form a cluster in sklearn; upstream returns None.
    assert after == []


def test_empty_input_returns_safe_structure() -> None:
    result = compute_market_structure(pl.DataFrame())
    assert result["trend"] == "unknown"
    assert result["swing_points"] == []
    assert result["support_zones"] == []
    assert result["cluster_levels"] == []


def test_flat_and_missing_close_are_safe() -> None:
    from app.indicators.market_structure import _cluster_reference_levels

    assert _cluster_reference_levels(_frame([10] * 25)) == []
    assert _cluster_reference_levels(_frame([10] * 12).drop("close")) == []
