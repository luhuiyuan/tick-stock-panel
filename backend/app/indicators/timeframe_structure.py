"""Independent weekly/monthly market-structure backgrounds."""
from __future__ import annotations

from typing import Any

import polars as pl

from app.indicators.market_structure import compute_market_structure
from app.indicators.pipeline import compute_indicators

TIMEFRAME_CONFIG = {
    "W": {"every": "1w", "label": "周线", "bars": 3},
    "M": {"every": "1mo", "label": "月线", "bars": 3},
}


def _empty(timeframe: str) -> dict[str, Any]:
    config = TIMEFRAME_CONFIG[timeframe]
    result = compute_market_structure(pl.DataFrame())
    result.update({
        "timeframe": timeframe,
        "timeframe_label": config["label"],
        "trend_label": "数据不足：尚无两组已确认高低点",
        "data_through_date": None,
        "completed_periods": 0,
    })
    return result


def _aggregate_completed(frame: pl.DataFrame, every: str) -> pl.DataFrame:
    required = {"date", "open", "high", "low", "close"}
    if frame.is_empty() or not required.issubset(frame.columns):
        return pl.DataFrame()
    daily = frame.sort("date").with_columns(
        pl.col("date").dt.truncate(every).alias("period")
    )
    periods = daily.get_column("period").unique().sort()
    if len(periods) < 3:
        return pl.DataFrame()
    # Boundary buckets may be truncated by the requested range (first) or
    # still in progress (last). Only interior buckets are known to be complete.
    completed = daily.filter(
        (pl.col("period") > periods[0]) & (pl.col("period") < periods[-1])
    )
    if completed.is_empty():
        return pl.DataFrame()
    weekly = completed.group_by("period", maintain_order=True).agg(
        pl.col("date").max().alias("date"),
        pl.col("open").first().alias("open"),
        pl.col("high").max().alias("high"),
        pl.col("low").min().alias("low"),
        pl.col("close").last().alias("close"),
        pl.col("volume").sum().alias("volume") if "volume" in completed.columns else pl.lit(0.0).alias("volume"),
    ).sort("date")
    # Reuse the daily pipeline's Wilder ATR, but compute it on period OHLC.
    return compute_indicators(
        weekly.drop("period").with_columns(pl.lit("period").alias("symbol")),
        needed={"atr_14"},
    ).drop("symbol")


def compute_timeframe_chart(frame: pl.DataFrame, timeframe: str) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Aggregate candles and structure from the same completed-period axis."""
    config = TIMEFRAME_CONFIG[timeframe]
    aggregated = _aggregate_completed(frame, config["every"])
    if aggregated.is_empty():
        return aggregated, _empty(timeframe)
    structure = compute_market_structure(
        aggregated, left_bars=config["bars"], right_bars=config["bars"],
    )
    structure.update({
        "timeframe": timeframe,
        "timeframe_label": config["label"],
        "data_through_date": str(aggregated["date"][-1])[:10],
        "completed_periods": aggregated.height,
    })
    if structure["trend"] == "unknown":
        structure["trend_label"] = "数据不足：尚无两组已确认高低点"
    return aggregated, structure


def compute_timeframe_structures(frame: pl.DataFrame) -> dict[str, dict[str, Any]]:
    """Return independent completed-week and completed-month structures."""
    return {
        timeframe: compute_timeframe_chart(frame, timeframe)[1]
        for timeframe in TIMEFRAME_CONFIG
    }
