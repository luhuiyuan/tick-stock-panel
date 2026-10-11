"""Small, leakage-safe L2 expression experiment helpers.

This module deliberately stays outside the production stock-analysis API. It
replays the current market-structure implementation on every historical prefix
and then attaches future returns. The expression is a research key, not a
trading signal.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal

import polars as pl

from app.indicators.smc_adapter import compute_smc_structure
from app.indicators.structure_wave import compute_wave_structure

ExpressionVariant = Literal["state", "state_amp", "state_time_amp"]
SampleUnit = Literal["daily", "event"]

# Finer bins than the old design's 20%+ bucket. Labels are representative
# upper bounds; the underlying continuous amplitude is retained in each row.
FINE_AMPLITUDE_BINS: tuple[tuple[float, int], ...] = (
    (0.03, 3), (0.05, 5), (0.08, 8), (0.12, 12),
    (0.18, 18), (0.25, 25), (0.35, 35), (0.50, 50),
    (float("inf"), 60),
)
COARSE_AMPLITUDE_BINS: tuple[tuple[float, int], ...] = (
    (0.05, 5), (0.10, 10), (0.20, 20), (float("inf"), 30),
)
TIME_BINS: tuple[tuple[int, int], ...] = ((7, 5), (12, 10), (17, 15), (10**9, 20))


@dataclass(frozen=True)
class L2ReplayConfig:
    """Parameters for one deterministic prefix-replay run."""

    min_prefix_bars: int = 80
    context_ks: tuple[int, ...] = (1, 2, 3)
    horizons: tuple[int, ...] = (5, 10, 20)
    variants: tuple[ExpressionVariant, ...] = ("state", "state_amp", "state_time_amp")
    amplitude_scheme: Literal["coarse", "fine"] = "fine"


def _date(value: Any) -> str:
    return str(value)[:10]


def _bin_amplitude(value: float, scheme: Literal["coarse", "fine"]) -> int:
    bins = FINE_AMPLITUDE_BINS if scheme == "fine" else COARSE_AMPLITUDE_BINS
    magnitude = abs(float(value))
    for upper, label in bins:
        if magnitude < upper:
            return label
    return bins[-1][1]


def _bin_time(duration: int) -> int:
    for upper, label in TIME_BINS:
        if duration <= upper:
            return label
    return TIME_BINS[-1][1]


def encode_segment(
    segment: dict[str, Any],
    *,
    variant: ExpressionVariant,
    amplitude_scheme: Literal["coarse", "fine"],
) -> str:
    """Encode one completed or current segment without losing raw values."""
    state = str(segment["state"])[0].upper()
    amplitude = _bin_amplitude(float(segment.get("net_return", 0.0)), amplitude_scheme)
    if variant == "state":
        return state
    if variant == "state_amp":
        return f"{state}_{amplitude}"
    return f"{state}_{_bin_time(int(segment.get('duration', 0)))}_{amplitude}"


def build_expression(
    completed_segments: list[dict[str, Any]],
    current_segment: dict[str, Any],
    *,
    context_k: int,
    variant: ExpressionVariant,
    amplitude_scheme: Literal["coarse", "fine"],
) -> str | None:
    """Build ``history#current`` for exactly ``context_k`` prior segments."""
    if context_k < 1 or len(completed_segments) < context_k:
        return None
    history = completed_segments[-context_k:]
    left = ",".join(
        encode_segment(item, variant=variant, amplitude_scheme=amplitude_scheme)
        for item in history
    )
    right = encode_segment(current_segment, variant=variant, amplitude_scheme=amplitude_scheme)
    return f"{left}#{right}"


def replay_l2_snapshot(frame: pl.DataFrame) -> dict[str, Any] | None:
    """Return the L2 state visible at the end of ``frame`` only."""
    if frame.is_empty() or "date" not in frame.columns:
        return None
    frame = frame.sort("date")
    l0 = compute_smc_structure(frame, swing_length=3, min_swing_atr=0.5, price_tolerance=0.001, break_buffer_atr=0.05)
    l1 = compute_smc_structure(frame, swing_length=7, min_swing_atr=0.5, price_tolerance=0.001, break_buffer_atr=0.05)
    l2 = compute_smc_structure(frame, swing_length=15, min_swing_atr=0.5, price_tolerance=0.001, break_buffer_atr=0.05)
    wave = compute_wave_structure(frame, l0, l1, l2)["L2"]
    current = wave.get("current_tail")
    if not current:
        # No current unconfirmed segment means there is no honest current
        # expression; using the last closed segment would leak a future boundary.
        return None
    completed = list(wave.get("segments", []))
    return {
        "date": _date(frame["date"][-1]),
        "current_start_date": current["start_date"],
        "completed_segments": completed,
        "current_segment": current,
        "l2_segment_count": len(completed),
    }


def _future_return(frame: pl.DataFrame, index: int, horizon: int) -> float | None:
    target = index + horizon
    if target >= frame.height:
        return None
    start = frame["close"][index]
    end = frame["close"][target]
    if start is None or end is None or float(start) <= 0:
        return None
    value = float(end) / float(start) - 1.0
    return value if isfinite(value) else None


def replay_samples(
    frame: pl.DataFrame,
    *,
    symbol: str,
    config: L2ReplayConfig | None = None,
) -> list[dict[str, Any]]:
    """Replay every eligible prefix and attach future labels.

    Prefix computation never sees rows after the observation date. Rows without
    a current tail or without a complete future horizon are retained only when
    at least one requested horizon is available.
    """
    frame = frame.sort("date")
    config = config or L2ReplayConfig()
    samples: list[dict[str, Any]] = []
    max_horizon = max(config.horizons, default=0)
    for index in range(max(config.min_prefix_bars, 1), frame.height - 1):
        prefix = frame.head(index + 1)
        snapshot = replay_l2_snapshot(prefix)
        if snapshot is None:
            continue
        returns = {h: _future_return(frame, index, h) for h in config.horizons}
        if all(value is None for value in returns.values()):
            continue
        for context_k in config.context_ks:
            for variant in config.variants:
                expression = build_expression(
                    snapshot["completed_segments"], snapshot["current_segment"],
                    context_k=context_k, variant=variant,
                    amplitude_scheme=config.amplitude_scheme,
                )
                if expression is None:
                    continue
                row: dict[str, Any] = {
                    "symbol": symbol,
                    "date": snapshot["date"],
                    "current_start_date": snapshot["current_start_date"],
                    "context_k": context_k,
                    "variant": variant,
                    "amplitude_scheme": config.amplitude_scheme,
                    "expression": expression,
                    "current_state": snapshot["current_segment"]["state"],
                    "current_duration": snapshot["current_segment"]["duration"],
                    "current_amplitude": abs(float(snapshot["current_segment"]["net_return"])),
                    "future_available_through": _date(frame["date"][-1]),
                    "max_requested_horizon": max_horizon,
                }
                row.update({f"future_return_{h}d": value for h, value in returns.items()})
                samples.append(row)
    return samples


def event_deduplicate(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one representative observation per symbol/expression/L2 segment.

    The representative is the earliest observation in the same current L2
    segment, preventing a long tail from being counted as many independent
    occurrences. This is intentionally separate from daily samples.
    """
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (item["symbol"], item["date"])):
        key = (
            row["symbol"], row["variant"], row["amplitude_scheme"],
            row["context_k"], row["current_start_date"],
        )
        grouped.setdefault(key, row)
    return list(grouped.values())


def summarize_rows(rows: Iterable[dict[str, Any]], *, horizon: int) -> list[dict[str, Any]]:
    """Summarize exact expression recurrence and future-return distribution."""
    groups: dict[tuple[str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(f"future_return_{horizon}d")
        if value is not None:
            groups[(row["variant"], row["amplitude_scheme"], row["context_k"], row["expression"])].append(row)
    result: list[dict[str, Any]] = []
    for (variant, scheme, context_k, expression), items in groups.items():
        returns = sorted(float(item[f"future_return_{horizon}d"]) for item in items)
        positives = [value for value in returns if value > 0]
        negatives = [value for value in returns if value < 0]
        result.append({
            "variant": variant, "amplitude_scheme": scheme, "context_k": context_k,
            "expression": expression, "sample_count": len(returns),
            "symbol_count": len({item["symbol"] for item in items}),
            "positive_count": len(positives), "negative_count": len(negatives),
            "positive_probability": len(positives) / len(returns),
            "negative_probability": len(negatives) / len(returns),
            "average_return": sum(returns) / len(returns),
            "median_return": returns[len(returns) // 2],
            "positive_mean": sum(positives) / len(positives) if positives else None,
            "negative_mean": sum(negatives) / len(negatives) if negatives else None,
            "p25": returns[max(0, (len(returns) - 1) // 4)],
            "p75": returns[min(len(returns) - 1, (3 * len(returns)) // 4)],
        })
    return sorted(result, key=lambda item: (-item["sample_count"], item["expression"]))
