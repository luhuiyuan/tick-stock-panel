"""Adapter around the vendored smart-money-concepts structure algorithms.

The upstream implementation is intentionally kept isolated from API code. This
module owns Polars/Pandas conversion, confirmed-date semantics, and the stable
output consumed by the existing chart.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
import polars as pl

from app.indicators.vendor.smartmoneyconcepts.smc import smc


def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


def _as_pandas(df: pl.DataFrame) -> pd.DataFrame:
    frame = df.sort("date")
    required = ["date", "open", "high", "low", "close"]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError(f"missing OHLC columns: {', '.join(missing)}")
    values = {
        name: frame.get_column(name).to_list()
        for name in ("open", "high", "low", "close")
    }
    values["volume"] = (
        frame.get_column("volume").to_list()
        if "volume" in frame.columns
        else [0.0] * frame.height
    )
    return pd.DataFrame(values)


def compute_smc_structure(
    df: pl.DataFrame,
    *,
    swing_length: int = 3,
    min_swing_atr: float = 0.0,
    price_tolerance: float = 0.0,
    break_buffer_atr: float = 0.0,
) -> dict[str, Any]:
    """Run upstream Swing/BOS/CHoCH and map it to project semantics.

    The upstream implementation uses a symmetric look-ahead window. A swing is
    only exposed when its effective confirmation bar exists; the final
    incomplete window and the upstream endpoint correction are ignored.
    """
    if df.is_empty() or "date" not in df.columns:
        return {"swing_points": [], "events": [], "trend": "unknown", "trend_history": []}

    window = max(1, int(swing_length))
    frame = df.sort("date")
    dates = [_date_text(value) for value in frame.get_column("date").to_list()]
    atrs = (
        [_finite(value) for value in frame.get_column("atr_14").to_list()]
        if "atr_14" in frame.columns else [None] * len(dates)
    )
    if len(dates) < window * 2 + 3:
        return {"swing_points": [], "events": [], "trend": "unknown", "trend_history": []}

    ohlc = _as_pandas(frame)
    raw_swings = smc.swing_highs_lows(ohlc, swing_length=window)

    swings: list[dict[str, Any]] = []
    swing_frame = pd.DataFrame({"HighLow": float("nan"), "Level": float("nan")}, index=ohlc.index)
    for index, value in raw_swings["HighLow"].items():
        kind_value = _finite(value)
        level = _finite(raw_swings["Level"].iloc[index])
        # The upstream function adjusts the first/last detected point to force
        # alternation. Those points are not confirmed by a complete window.
        if index < window or index + window >= len(dates) or kind_value is None or level is None:
            continue
        kind = "high" if kind_value == 1 else "low"
        previous_opposite = next(
            (point for point in reversed(swings) if point["type"] != kind), None
        )
        origin_atr = atrs[index] or 0.0
        if (
            previous_opposite is not None
            and origin_atr > 0
            and abs(level - float(previous_opposite["price"]))
            < origin_atr * max(0.0, min_swing_atr)
        ):
            continue
        swing_frame.loc[index, "HighLow"] = kind_value
        swing_frame.loc[index, "Level"] = level
        swings.append({
            "type": kind,
            "price": round(level, 2),
            "date": dates[index],
            "confirmed_date": dates[index + window],
        })

    events: list[dict[str, Any]] = []
    if swings:
        raw_events = smc.bos_choch(ohlc, swing_frame, close_break=True)
        for index in raw_events.index:
            bos = _finite(raw_events.at[index, "BOS"])
            choch = _finite(raw_events.at[index, "CHOCH"])
            level = _finite(raw_events.at[index, "Level"])
            broken = _finite(raw_events.at[index, "BrokenIndex"])
            if level is None or broken is None or (bos is None and choch is None):
                continue
            broken_index = int(broken)
            if broken_index < 0 or broken_index >= len(dates):
                continue
            direction = 1 if (bos or choch) > 0 else -1
            close = _finite(ohlc.iloc[broken_index]["close"])
            break_atr = (atrs[broken_index] or 0.0) * max(0.0, break_buffer_atr)
            if close is None or (direction > 0 and close <= level + break_atr) or (
                direction < 0 and close >= level - break_atr
            ):
                continue
            is_bos = bos is not None and bos != 0
            event_type = (
                "bos_up" if direction > 0 else "bos_down"
            ) if is_bos else (
                "choch_up" if direction > 0 else "choch_down"
            )
            events.append({
                "type": event_type,
                "date": dates[broken_index],
                "price": round(close, 2),
                "reference_price": round(level, 2),
                "label": {
                    "bos_up": "突破前高", "bos_down": "跌破前低",
                    "choch_up": "空头结构被改变", "choch_down": "多头结构被改变",
                }[event_type],
            })

    deduped: dict[tuple[str, str, float], dict[str, Any]] = {}
    for event in events:
        key = (event["type"], event["date"], event["reference_price"])
        deduped[key] = event
    events = sorted(deduped.values(), key=lambda item: item["date"])

    trend = _trend_from_swings(swings, price_tolerance)
    trend_history = [
        {"date": dates[index], "trend": _trend_from_swings(
            [point for point in swings if point["confirmed_date"] <= dates[index]],
            price_tolerance,
        )}
        for index in range(len(dates))
    ]
    return {
        "swing_points": swings,
        "events": events,
        "trend": trend,
        "trend_history": trend_history,
    }


def _trend_from_swings(swings: list[dict[str, Any]], price_tolerance: float) -> str:
    highs = [item["price"] for item in swings if item["type"] == "high"]
    lows = [item["price"] for item in swings if item["type"] == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "unknown"
    reference = max(float(highs[-1]), float(lows[-1]), 1.0)
    tolerance = reference * max(0.0, price_tolerance)
    high_delta = highs[-1] - highs[-2]
    low_delta = lows[-1] - lows[-2]
    if high_delta > tolerance and low_delta > tolerance:
        return "bullish"
    if high_delta < -tolerance and low_delta < -tolerance:
        return "bearish"
    return "neutral"
