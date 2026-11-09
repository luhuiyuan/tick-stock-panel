from datetime import date, timedelta

import polars as pl

from app.indicators.structure_wave import compute_wave_structure


def fixture(prices, confirmation_bars=3):
    dates = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(prices) + 20)]
    frame = pl.DataFrame({
        "date": dates,
        "close": [float(price) for price in prices] + [float(prices[-1])] * (len(dates) - len(prices)),
    })
    swings = [{
        "date": dates[i], "price": float(price), "type": "low" if i % 2 == 0 else "high",
        "confirmed_date": dates[i + confirmation_bars],
    } for i, price in enumerate(prices)]
    return frame, {"swing_points": swings}


def test_l0_exactly_reuses_market_structure():
    frame, structure = fixture([100, 120, 116, 130, 122])
    result = compute_wave_structure(frame, structure)
    assert [(p["date"], p["price"]) for p in result["L0"]["turning_points"]] == [
        (p["date"], p["price"]) for p in structure["swing_points"]]
    assert len(result["L0"]["segments"]) == 4
    assert result["L0"]["confirmation_bars"] == 3


def test_l1_uses_seven_bar_market_structure_points():
    frame, l0 = fixture([100, 120, 116, 130, 122])
    _, l1 = fixture([100, 130, 122], confirmation_bars=7)
    result = compute_wave_structure(frame, l0, l1)
    assert result["L1"]["algorithm"] == "market_structure"
    assert result["L1"]["confirmation_bars"] == 7
    assert [p["price"] for p in result["L1"]["turning_points"]] == [100, 130, 122]
    assert result["L1"]["segments"][0]["confirmed_date"] == l1["swing_points"][1]["confirmed_date"]


def test_l2_uses_fifteen_bar_market_structure_points():
    frame, l0 = fixture([100, 120, 116, 130, 122])
    _, l1 = fixture([100, 130, 122], confirmation_bars=7)
    _, l2 = fixture([100, 140], confirmation_bars=15)
    result = compute_wave_structure(frame, l0, l1, l2)
    assert result["L2"]["algorithm"] == "market_structure"
    assert result["L2"]["confirmation_bars"] == 15
    assert [p["price"] for p in result["L2"]["turning_points"]] == [100, 140]
    assert result["L2"]["segments"][0]["confirmed_date"] == l2["swing_points"][1]["confirmed_date"]


def test_l1_does_not_fall_back_to_l0_when_no_seven_bar_points():
    frame, l0 = fixture([100, 120, 116, 130, 122])
    result = compute_wave_structure(frame, l0, {"swing_points": []})
    assert result["L0"]["segment_count"] == 4
    assert result["L1"]["turning_points"] == []
    assert result["L2"]["turning_points"] == []
    assert result["L1"]["segments"] == []
    assert result["L2"]["turning_points"] == []


def test_l1_tail_is_unconfirmed_latest_close_extension():
    frame, l0 = fixture([100, 120, 116])
    _, l1 = fixture([100, 116], confirmation_bars=7)
    frame = frame.with_columns(pl.when(pl.arange(0, frame.height) == frame.height - 1)
                               .then(120.0).otherwise(pl.col("close")).alias("close"))
    result = compute_wave_structure(frame, l0, l1)
    tail = result["L1"]["current_tail"]
    assert tail is not None
    assert tail["confirmed_date"] is None
    assert tail["end_date"] == frame["date"][-1]


def test_empty_input_is_safe():
    result = compute_wave_structure(pl.DataFrame(), {})
    assert result["L0"]["turning_points"] == []
    assert result["L1"]["turning_points"] == []
    assert result["L2"]["turning_points"] == []
