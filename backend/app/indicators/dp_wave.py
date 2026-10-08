"""ATR-normalized Douglas-Peucker wave extraction.

The extractor intentionally stays independent from the existing SMC market
structure implementation. It operates on adjusted close and ATR14, returns
JSON-friendly dictionaries, and supports the L0 -> L3 hierarchy used by the
trend-context experiment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from math import isfinite, log
from typing import Any

import polars as pl

LEVEL_EPSILONS: dict[str, float] = {
    "L0": 0.20,
    "L1": 0.80,
    "L2": 2.00,
    "L3": 3.00,
}


@dataclass(frozen=True)
class _Point:
    index: int
    date: str
    close: float
    atr: float | None


def _date_text(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number > 0 else None


def _points_from_frame(frame: pl.DataFrame) -> list[_Point]:
    required = {"date", "close"}
    if frame.is_empty() or not required.issubset(frame.columns):
        return []

    rows = frame.select(
        [pl.col("date"), pl.col("close"), pl.col("atr_14") if "atr_14" in frame.columns else pl.lit(None).alias("atr_14")]
    ).to_dicts()
    points: list[_Point] = []
    for index, row in enumerate(rows):
        close = _finite_positive(row.get("close"))
        if close is None:
            continue
        atr = _finite_positive(row.get("atr_14"))
        points.append(_Point(index=index, date=_date_text(row.get("date")), close=close, atr=atr))
    return points


def _trend_log_value(left: _Point, right: _Point, point: _Point) -> float:
    span = right.index - left.index
    if span <= 0:
        return log(left.close)
    ratio = (point.index - left.index) / span
    return log(left.close) + ratio * (log(right.close) - log(left.close))


def _max_deviation(points: list[_Point], left_index: int, right_index: int) -> tuple[float, int | None]:
    left = points[left_index]
    right = points[right_index]
    best = 0.0
    best_index: int | None = None
    for position in range(left_index + 1, right_index):
        point = points[position]
        if point.atr is None:
            continue
        atr_ratio = point.atr / point.close
        if not isfinite(atr_ratio) or atr_ratio <= 0:
            continue
        deviation = abs(log(point.close) - _trend_log_value(left, right, point)) / atr_ratio
        if deviation > best:
            best = deviation
            best_index = position
        # Equal maxima deliberately keep the earlier point for deterministic output.
    return best, best_index


def _retain_points(points: list[_Point], epsilon: float) -> list[int]:
    if len(points) < 2:
        return list(range(len(points)))

    retained: set[int] = {0, len(points) - 1}

    def split(left_index: int, right_index: int) -> None:
        deviation, split_index = _max_deviation(points, left_index, right_index)
        if split_index is None or deviation <= epsilon:
            return
        retained.add(split_index)
        split(left_index, split_index)
        split(split_index, right_index)

    split(0, len(points) - 1)
    return sorted(retained)


def _point_payload(point: _Point) -> dict[str, Any]:
    return {"date": point.date, "price": round(point.close, 6), "index": point.index}


def _segment_payload(points: list[_Point], left: _Point, right: _Point, epsilon: float) -> dict[str, Any]:
    deviation, _ = _max_deviation(points, points.index(left), points.index(right))
    state = "UP" if right.close > left.close else "DOWN"
    return {
        "state": state,
        "start_date": left.date,
        "end_date": right.date,
        "start_price": round(left.close, 6),
        "end_price": round(right.close, 6),
        "duration": max(1, right.index - left.index + 1),
        "net_return": round(right.close / left.close - 1.0, 8),
        "amplitude": round(abs(right.close / left.close - 1.0), 8),
        "max_deviation_atr": round(deviation, 6),
        "epsilon": epsilon,
    }


def _merge_same_direction(points: list[_Point], retained_indices: list[int]) -> list[int]:
    """Collapse adjacent DP segments that have the same direction.

    Douglas-Peucker preserves geometric detail, but a retained point can still
    sit inside a move whose net direction has not changed. Such points are not
    wave reversals, so remove them until the remaining directions alternate.
    """
    if len(retained_indices) < 3:
        return retained_indices

    merged: list[int] = [retained_indices[0], retained_indices[1]]
    for candidate in retained_indices[2:]:
        while len(merged) >= 2:
            left = points[merged[-2]]
            middle = points[merged[-1]]
            right = points[candidate]
            first_state = "UP" if middle.close > left.close else "DOWN"
            second_state = "UP" if right.close > middle.close else "DOWN"
            if first_state != second_state:
                break
            # The middle point is a geometric fit point, not a direction change.
            merged.pop()
        merged.append(candidate)
    return merged


def _compute_level(points: list[_Point], level: str, epsilon: float) -> dict[str, Any]:
    if len(points) < 2:
        return {
            "level": level,
            "epsilon": epsilon,
            "turning_points": [_point_payload(point) for point in points],
            "segments": [],
            "current_tail": None,
        }

    retained_indices = _retain_points(points, epsilon)
    retained_indices = _merge_same_direction(points, retained_indices)
    retained_points = [points[index] for index in retained_indices]
    segments = [
        _segment_payload(points, left, right, epsilon)
        for left, right in pairwise(retained_points)
    ]
    current_tail = segments[-1] if segments else None
    confirmed = segments[:-1] if segments else []
    return {
        "level": level,
        "epsilon": epsilon,
        "turning_points": [_point_payload(point) for point in retained_points],
        "segments": confirmed,
        "current_tail": current_tail,
        "segment_count": len(segments),
    }


def _as_level_points(result: dict[str, Any], source: list[_Point]) -> list[_Point]:
    by_index = {point.index: point for point in source}
    points: list[_Point] = []
    for item in result.get("turning_points", []):
        point = by_index.get(int(item["index"]))
        if point is not None:
            points.append(point)
    return points


def compute_dp_structure(frame: pl.DataFrame, epsilons: dict[str, float] | None = None) -> dict[str, dict[str, Any]]:
    """Return L0-L3 DP wave structures for a daily adjusted-close frame."""
    config = {**LEVEL_EPSILONS, **(epsilons or {})}
    raw_points = _points_from_frame(frame)
    if len(raw_points) < 2:
        return {
            level: _compute_level([], level, float(config[level]))
            for level in ("L0", "L1", "L2", "L3")
        }

    output: dict[str, dict[str, Any]] = {}
    points = raw_points
    for level in ("L0", "L1", "L2", "L3"):
        result = _compute_level(points, level, float(config[level]))
        output[level] = result
        # Higher levels consume the lower level's retained trajectory points.
        retained = _as_level_points(result, points)
        if len(retained) >= 2:
            points = retained
    return output
