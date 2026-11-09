"""Hierarchical market-structure waves used by the stock-analysis chart.

L0 and L1 use the same confirmed swing algorithm on raw daily OHLC data. The
levels differ only in their local-extremum window: three bars for L0 and seven
bars for L1. This keeps the visual meaning simple and makes the confirmation
latency explicit.
"""
from __future__ import annotations

from itertools import pairwise
from typing import Any

import polars as pl

L0_SWING_LENGTH = 3
L1_SWING_LENGTH = 7
L2_SWING_LENGTH = 15


def _segment(left: dict[str, Any], right: dict[str, Any], *, confirmed_date: str | None) -> dict[str, Any]:
    change = right["price"] / left["price"] - 1.0
    return {
        "state": "UP" if change > 0 else "DOWN",
        "start_date": left["date"],
        "end_date": right["date"],
        "start_price": left["price"],
        "end_price": right["price"],
        "duration": max(1, right["index"] - left["index"] + 1),
        "net_return": change,
        "amplitude": abs(change),
        "confirmed_date": confirmed_date,
        "reversal_threshold": None,
    }


def _level(
    frame: pl.DataFrame,
    swings: list[dict[str, Any]],
    *,
    level: str,
    algorithm: str,
    confirmation_bars: int,
    include_tail: bool,
) -> dict[str, Any]:
    dates = [str(value)[:10] for value in frame["date"].to_list()]
    index_by_date = {value: index for index, value in enumerate(dates)}
    points = [
        {**point, "index": index_by_date[point["date"]], "confirmed": True}
        for point in swings
        if point.get("date") in index_by_date
        and point.get("confirmed_date", dates[-1]) <= dates[-1]
    ]
    segments = [
        _segment(left, right, confirmed_date=max(left["confirmed_date"], right["confirmed_date"]))
        for left, right in pairwise(points)
    ]
    tail = None
    if include_tail and points and points[-1]["index"] < len(dates) - 1:
        latest = {
            "date": dates[-1],
            "price": float(frame["close"][-1]),
            "index": len(dates) - 1,
        }
        if latest["price"] != points[-1]["price"]:
            tail = _segment(points[-1], latest, confirmed_date=None)
            tail["state"] = "UP" if latest["price"] > points[-1]["price"] else "DOWN"
    return {
        "level": level,
        "algorithm": algorithm,
        "confirmation_bars": confirmation_bars,
        "turning_points": points,
        "segments": segments,
        "current_tail": tail,
        "segment_count": len(segments) + int(tail is not None),
        "data_through_date": dates[-1] if dates else None,
    }


def compute_wave_structure(
    frame: pl.DataFrame,
    structure: dict[str, Any],
    l1_structure: dict[str, Any] | None = None,
    l2_structure: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build L0/L1/L2 from independent raw-daily market-structure windows."""
    empty = {
        "turning_points": [], "segments": [], "current_tail": None,
        "segment_count": 0, "confirmation_bars": 0,
    }
    if frame.is_empty() or "date" not in frame.columns:
        return {
            "L0": {"level": "L0", "algorithm": "market_structure", **empty},
            "L1": {"level": "L1", "algorithm": "market_structure", **empty},
            "L2": {"level": "L2", "algorithm": "market_structure", **empty},
        }
    return {
        "L0": _level(
            frame, structure.get("swing_points", []), level="L0",
            algorithm="market_structure", confirmation_bars=L0_SWING_LENGTH,
            include_tail=False,
        ),
        "L1": _level(
            frame, (l1_structure or {}).get("swing_points", []), level="L1",
            algorithm="market_structure", confirmation_bars=L1_SWING_LENGTH,
            include_tail=True,
        ),
        "L2": _level(
            frame, (l2_structure or {}).get("swing_points", []), level="L2",
            algorithm="market_structure", confirmation_bars=L2_SWING_LENGTH,
            include_tail=True,
        ),
    }
