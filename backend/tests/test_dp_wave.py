import polars as pl

from app.indicators.dp_wave import compute_dp_structure


def _frame(closes: list[float], atr: float = 1.0) -> pl.DataFrame:
    return pl.DataFrame({
        "date": [f"2026-01-{index + 1:02d}" for index in range(len(closes))],
        "close": closes,
        "atr_14": [atr] * len(closes),
    })


def test_straight_line_is_not_split():
    result = compute_dp_structure(_frame([100, 102, 104, 106, 108], atr=2.0))
    assert len(result["L0"]["turning_points"]) == 2
    assert result["L0"]["segments"] == []
    assert result["L0"]["current_tail"]["state"] == "UP"


def test_large_deviation_keeps_turning_point():
    result = compute_dp_structure(_frame([100, 110, 90, 120], atr=1.0))
    points = result["L0"]["turning_points"]
    assert [point["price"] for point in points] == [100.0, 110.0, 90.0, 120.0]
    assert [segment["state"] for segment in result["L0"]["segments"]] == ["UP", "DOWN"]
    assert result["L0"]["current_tail"]["state"] == "UP"


def test_same_direction_dp_segments_are_merged():
    result = compute_dp_structure(
        _frame([100, 110, 115, 100], atr=1.0),
        {"L0": 0.2, "L1": 2.0, "L2": 4.0, "L3": 6.0},
    )
    states = [segment["state"] for segment in result["L0"]["segments"]]
    if result["L0"]["current_tail"] is not None:
        states.append(result["L0"]["current_tail"]["state"])
    assert states == ["UP", "DOWN"]
    assert [point["price"] for point in result["L0"]["turning_points"]] == [100.0, 115.0, 100.0]


def test_higher_epsilon_does_not_create_more_turning_points():
    result = compute_dp_structure(
        _frame([100, 110, 90, 120, 115, 130], atr=1.0),
        {"L0": 0.2, "L1": 2.0, "L2": 4.0, "L3": 6.0},
    )
    assert len(result["L1"]["turning_points"]) <= len(result["L0"]["turning_points"])
    assert len(result["L2"]["turning_points"]) <= len(result["L1"]["turning_points"])
    assert len(result["L3"]["turning_points"]) <= len(result["L2"]["turning_points"])


def test_missing_atr_is_skipped_for_deviation_but_points_are_kept():
    frame = _frame([100, 110, 90, 120], atr=1.0).with_columns(
        pl.when(pl.col("date") == "2026-01-02").then(None).otherwise(pl.col("atr_14")).alias("atr_14")
    )
    result = compute_dp_structure(frame)
    assert result["L0"]["turning_points"][0]["price"] == 100.0
    assert result["L0"]["turning_points"][-1]["price"] == 120.0


def test_equal_maximum_chooses_earlier_point():
    result = compute_dp_structure(_frame([100, 110, 90, 100], atr=1.0))
    points = result["L0"]["turning_points"]
    assert points[1]["date"] == "2026-01-02"
