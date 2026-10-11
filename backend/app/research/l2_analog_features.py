"""Historical-prefix snapshots and continuous analog features for L0/L1/L2."""
from __future__ import annotations

from math import isfinite
from typing import Any

import polars as pl

from app.indicators.smc_adapter import compute_smc_structure
from app.indicators.structure_wave import compute_wave_structure

LEVEL_CONFIG = {"L0": 3, "L1": 7, "L2": 15}


def _date_text(value: Any) -> str:
    return str(value)[:10]


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def ensure_atr14(frame: pl.DataFrame) -> pl.DataFrame:
    """Add the project's Wilder-style ATR14 when the input lacks it."""
    if frame.is_empty() or "atr_14" in frame.columns:
        return frame
    if not {"high", "low", "close"}.issubset(frame.columns):
        return frame
    ordered = frame.sort("date")
    previous_close = pl.col("close").shift(1)
    true_range = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - previous_close).abs(),
        (pl.col("low") - previous_close).abs(),
    )
    return ordered.with_columns(
        true_range.ewm_mean(alpha=1.0 / 14, adjust=False).alias("atr_14"),
    )


def _structure(frame: pl.DataFrame, swing_length: int) -> dict[str, Any]:
    return compute_smc_structure(
        frame,
        swing_length=swing_length,
        min_swing_atr=0.5,
        price_tolerance=0.001,
        break_buffer_atr=0.05,
    )


def _synthetic_current_segment(
    frame: pl.DataFrame,
    level: dict[str, Any],
) -> dict[str, Any] | None:
    """Create a research-only current segment for L0, which has no UI tail."""
    points = level.get("turning_points", [])
    if not points or frame.is_empty():
        return None
    last = points[-1]
    dates = [_date_text(value) for value in frame["date"].to_list()]
    try:
        start_index = dates.index(last["date"])
    except ValueError:
        return None
    end_index = len(dates) - 1
    if end_index <= start_index:
        return None
    start_price = float(last["price"])
    end_price = float(frame["close"][-1])
    if start_price <= 0 or end_price <= 0 or start_price == end_price:
        return None
    change = end_price / start_price - 1.0
    return {
        "state": "UP" if change > 0 else "DOWN",
        "start_date": last["date"],
        "end_date": dates[-1],
        "start_price": start_price,
        "end_price": end_price,
        "duration": end_index - start_index + 1,
        "net_return": change,
        "amplitude": abs(change),
        "confirmed_date": None,
        "reversal_threshold": None,
        "research_only": True,
    }


def _level_snapshot(
    frame: pl.DataFrame,
    level_name: str,
    level: dict[str, Any],
) -> dict[str, Any]:
    current = level.get("current_tail")
    if current is None and level_name == "L0":
        current = _synthetic_current_segment(frame, level)
    completed = list(level.get("segments", []))
    previous = completed[-1] if completed else None
    return {
        "state": current.get("state") if current else None,
        "current_start_date": current.get("start_date") if current else None,
        "current_duration": int(current["duration"]) if current else None,
        "current_net_return": _finite_float(current.get("net_return")) if current else None,
        "current_amplitude": _finite_float(current.get("amplitude")) if current else None,
        "current_confirmed": bool(current and current.get("confirmed_date")),
        "current_research_only": bool(current and current.get("research_only", False)),
        "previous_state": previous.get("state") if previous else None,
        "just_reversed": bool(current and previous and current.get("state") != previous.get("state")),
        "completed_segment_count": len(completed),
        "history": [
            {
                "state": item.get("state"),
                "duration": int(item.get("duration", 0)),
                "net_return": _finite_float(item.get("net_return")),
                "start_date": item.get("start_date"),
                "end_date": item.get("end_date"),
            }
            for item in completed[-3:]
        ],
    }


def _alignment(levels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    states = {name: levels[name]["state"] for name in LEVEL_CONFIG}
    return {
        "l0_l1_same_direction": bool(states["L0"] and states["L0"] == states["L1"]),
        "l1_l2_same_direction": bool(states["L1"] and states["L1"] == states["L2"]),
        "all_same_direction": bool(states["L0"] and states["L0"] == states["L1"] == states["L2"]),
        "state_signature": "/".join(states[name] or "NA" for name in LEVEL_CONFIG),
    }


def _recent_candle_features(frame: pl.DataFrame, lookback: int) -> dict[str, list[float | None]]:
    frame = ensure_atr14(frame).tail(lookback)
    if frame.is_empty():
        return {"returns_atr": [], "range_atr": [], "body_atr": [], "upper_shadow_atr": [], "lower_shadow_atr": []}
    closes = frame["close"].to_list()
    highs = frame["high"].to_list()
    lows = frame["low"].to_list()
    opens = frame["open"].to_list()
    atrs = frame["atr_14"].to_list() if "atr_14" in frame.columns else [None] * frame.height
    output = {name: [] for name in ("returns_atr", "range_atr", "body_atr", "upper_shadow_atr", "lower_shadow_atr")}
    previous_close: float | None = None
    for open_, high, low, close, atr in zip(opens, highs, lows, closes, atrs, strict=True):
        values = [_finite_float(value) for value in (open_, high, low, close, atr)]
        if any(value is None for value in values) or values[-1] is None or values[-1] <= 0:
            for name in output:
                output[name].append(None)
            previous_close = values[3]
            continue
        open_value, high_value, low_value, close_value, atr_value = values
        assert open_value is not None and high_value is not None and low_value is not None
        assert close_value is not None and atr_value is not None
        scale = atr_value if atr_value > 0 else None
        if scale is None:
            for name in output:
                output[name].append(None)
        else:
            reference_return = None if previous_close in (None, 0) else (close_value / previous_close - 1.0)
            upper = high_value - max(open_value, close_value)
            lower = min(open_value, close_value) - low_value
            output["returns_atr"].append(reference_return / scale if reference_return is not None else None)
            output["range_atr"].append((high_value - low_value) / scale)
            output["body_atr"].append((close_value - open_value) / scale)
            output["upper_shadow_atr"].append(upper / scale)
            output["lower_shadow_atr"].append(lower / scale)
        previous_close = close_value
    return output


def build_feature_snapshot(
    frame: pl.DataFrame,
    *,
    symbol: str | None = None,
    candle_lookback: int = 20,
) -> dict[str, Any] | None:
    """Build the state visible at the end of one already-truncated prefix."""
    if frame.is_empty() or not {"date", "open", "high", "low", "close"}.issubset(frame.columns):
        return None
    frame = ensure_atr14(frame)
    structures = {name: _structure(frame, length) for name, length in LEVEL_CONFIG.items()}
    waves = compute_wave_structure(
        frame,
        structures["L0"],
        structures["L1"],
        structures["L2"],
    )
    levels = {name: _level_snapshot(frame, name, waves[name]) for name in LEVEL_CONFIG}
    snapshot: dict[str, Any] = {
        "symbol": symbol,
        "trade_date": _date_text(frame["date"][-1]),
        "data_through_date": _date_text(frame["date"][-1]),
        "bar_count": frame.height,
        "levels": levels,
        "alignment": _alignment(levels),
        "recent_candles": _recent_candle_features(frame, candle_lookback),
        "feature_version": "l2-analog-features-v1",
    }
    return snapshot


def replay_feature_snapshots(
    frame: pl.DataFrame,
    *,
    symbol: str | None = None,
    min_prefix_bars: int = 80,
    candle_lookback: int = 20,
) -> list[dict[str, Any]]:
    """Generate snapshots using only each historical prefix."""
    frame = frame.sort("date")
    return [
        snapshot
        for index in range(max(1, min_prefix_bars), frame.height + 1)
        if (snapshot := build_feature_snapshot(
            frame.head(index), symbol=symbol, candle_lookback=candle_lookback,
        )) is not None
    ]
