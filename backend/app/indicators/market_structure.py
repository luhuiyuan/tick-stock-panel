"""SMC market structure and open-source price-cluster references."""
from __future__ import annotations

from math import isfinite
from typing import Any

import pandas as pd
import polars as pl

from app.indicators.smc_adapter import compute_smc_structure
from app.indicators.vendor.support_resistance.cluster import RawPriceClusterLevels

DEFAULT_SWING_LENGTH = 3
DEFAULT_MIN_SWING_ATR = 0.5
DEFAULT_PRICE_TOLERANCE = 0.001
DEFAULT_BREAK_BUFFER_ATR = 0.05


def _cluster_reference_levels(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """Upstream close-price extrema clusters, classified relative to latest close.

    The upstream 11-bar centered rolling window confirms an extremum five bars
    later; this API reports *current* reference prices, never historical signals.
    """
    if "close" not in frame.columns:
        return []
    closes = [float(value) for value in frame.get_column("close").to_list()
              if value is not None and isfinite(float(value)) and float(value) > 0]
    if len(closes) < 11:
        return []
    current = closes[-1]
    candidates: list[dict[str, Any]] = []
    for maximums in (True, False):
        finder = RawPriceClusterLevels(merge_distance=None, merge_percent=1.5,
                                       bars_for_peak=11, use_maximums=maximums)
        finder.fit(pd.DataFrame({"Close": closes}))
        for level in finder.levels or []:
            price = float(level["price"])
            if not isfinite(price) or price <= 0 or abs(price - current) < 0.005:
                continue
            candidates.append({"price": round(price, 2), "peak_count": int(level["peak_count"]),
                               "side": "resistance" if price > current else "support"})
    # Maxima/minima can yield the same rounded price: show it only once, without
    # claiming the counts across both fits represent independent touches.
    unique: dict[tuple[str, float], dict[str, Any]] = {}
    for item in candidates:
        key = (item["side"], item["price"])
        if key not in unique or item["peak_count"] > unique[key]["peak_count"]:
            unique[key] = item
    below = sorted((v for v in unique.values() if v["side"] == "support"),
                   key=lambda v: current - v["price"])[:3]
    above = sorted((v for v in unique.values() if v["side"] == "resistance"),
                   key=lambda v: v["price"] - current)[:3]
    return below + above


def compute_market_structure(
    df: pl.DataFrame, *, left_bars: int = DEFAULT_SWING_LENGTH,
    right_bars: int = DEFAULT_SWING_LENGTH, min_swing_atr: float = DEFAULT_MIN_SWING_ATR,
    price_tolerance: float = DEFAULT_PRICE_TOLERANCE,
    break_buffer_atr: float = DEFAULT_BREAK_BUFFER_ATR,
) -> dict[str, Any]:
    """Compute confirmed SMC structure and current close-price cluster references."""
    if df.is_empty() or "date" not in df.columns:
        return _empty_structure()
    swing_length = max(1, int(left_bars), int(right_bars))
    smc_result = compute_smc_structure(df, swing_length=swing_length, min_swing_atr=min_swing_atr,
                                       price_tolerance=price_tolerance, break_buffer_atr=break_buffer_atr)
    frame = df.sort("date")
    trend = smc_result["trend"]
    cluster_levels = _cluster_reference_levels(frame)
    return {
        "trend": trend,
        "trend_label": {"bullish": "多头结构", "bearish": "空头结构", "neutral": "震荡结构"}.get(trend, "结构形成中"),
        "last_high": next((p["price"] for p in reversed(smc_result["swing_points"]) if p["type"] == "high"), None),
        "last_low": next((p["price"] for p in reversed(smc_result["swing_points"]) if p["type"] == "low"), None),
        "swing_points": smc_result["swing_points"], "events": smc_result["events"],
        "cluster_levels": cluster_levels,
        "price_zones": [], "support_zones": [], "resistance_zones": [],
        "candidate_zones": [],
        "confirmation_bars": swing_length, "trend_history": smc_result["trend_history"],
        "previous_trend": smc_result["trend_history"][-2]["trend"] if len(smc_result["trend_history"]) > 1 else "unknown",
    }


def _empty_structure() -> dict[str, Any]:
    return {
        "trend": "unknown", "trend_label": "结构形成中", "last_high": None, "last_low": None,
        "swing_points": [], "events": [], "cluster_levels": [], "price_zones": [], "support_zones": [],
        "resistance_zones": [], "candidate_zones": [], "confirmation_bars": DEFAULT_SWING_LENGTH,
        "trend_history": [], "previous_trend": "unknown",
    }
