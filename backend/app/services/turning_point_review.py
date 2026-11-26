"""Bounded retrospective L2 label review, not a replay of live L2 states."""
from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from app.indicators.pipeline import compute_indicators
from app.indicators.smc_adapter import compute_smc_structure
from app.indicators.structure_wave import L2_SWING_LENGTH
from app.services.coverage_limits import price_facts
from app.services.signal_forward_performance import ADJUSTED_LEVELS, BASE, INDICATORS
from app.strategy import custom_signals
from app.strategy.strict_signal_data import MARKET_INDEX, attach_calendar

RULE = "turning-point-review-v2"
SWING_LENGTH = L2_SWING_LENGTH
WINDOWS = (("1_5", 1, 5), ("6_15", 6, 15), ("16_30", 16, 30))
MAX_LEAD = WINDOWS[-1][2]
EXTREMES = {"high_60d", "low_60d"}
PRICE_INDICATORS = {"boll_upper", "boll_lower"}
LIMIT_FIELDS = {"limit_up_price", "limit_down_price"}
LIMIT_SIGNALS = {"broken_limit_up", "limit_down", "limit_down_recovery", "limit_up"}
SUPPORTED_INDICATORS = INDICATORS | EXTREMES | PRICE_INDICATORS
FIELDS = tuple(sorted(BASE))
CASE_TYPES = ("matched", "missed", "duplicate", "unmatched", "indeterminate_pivot", "indeterminate_alert")


@dataclass(frozen=True)
class ReviewScope:
    start: date
    end: date
    signal_ids: tuple[str, ...]
    directions: tuple[str, ...] = ("high", "low")


def digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def signal_catalog(data_dir) -> dict[str, dict]:
    return {sig["id"]: sig for sig in custom_signals.load_all(data_dir) if sig.get("id")}


def describe_signal(definition: dict) -> dict:
    return {
        "signal_id": definition["id"],
        "name": definition.get("name") or definition["id"],
        "type": custom_signals.signal_type(definition),
        "timeframe": definition.get("timeframe", "daily"),
        "description": definition.get("description", ""),
        "enabled": definition.get("enabled") is not False,
    }


def _compile(sid: str, catalog: dict[str, dict]) -> tuple[pl.Expr, set[str], str]:
    signal = catalog[sid]
    refs = custom_signals.expr_refs(signal.get("expr") or {})
    definitions = {sid: signal}
    for ref in refs:
        atom = catalog.get(ref)
        if not atom or custom_signals.signal_type(atom) != custom_signals.TYPE_ATOM:
            raise ValueError(f"缺少可用的原子信号：{ref}")
        definitions[ref] = atom
    levels = ADJUSTED_LEVELS | EXTREMES | PRICE_INDICATORS
    for item in definitions.values():
        if item.get("enabled") is False or item.get("timeframe", "daily") != "daily":
            raise ValueError(f"仅支持已启用的日线信号：{item['id']}")
        custom_signals.validate(item)
        for condition in item.get("conditions", []):
            if (condition["left"] in levels) != (str(condition["right"]).removeprefix("field:") in levels):
                raise ValueError("不支持前复权绝对价或混合价格比较")
    column = custom_signals.resolve_column(signal)
    expr = custom_signals.build_expressions([signal], catalog=definitions).get(column)
    if expr is None:
        raise ValueError("无法编译信号")
    roots = set(expr.meta.root_names())
    # Review only these same-day built-ins under the retrospective limit policy.
    # Shifted/composite limit operands need separate time-aware provenance.
    retrospective = LIMIT_FIELDS if sid in LIMIT_SIGNALS and not refs and not any(
        c.get("leftDays") or c.get("rightDays") for c in signal.get("conditions", [])
    ) else set()
    unsupported = roots - BASE - SUPPORTED_INDICATORS - retrospective - {MARKET_INDEX, "symbol"}
    if unsupported:
        raise ValueError(f"依赖尚未验证历史可知性：{', '.join(sorted(unsupported))}")
    return expr, roots, digest(definitions)


def _flags(frame: pl.DataFrame, expr: pl.Expr, roots: set[str], calendar: list[date]) -> list[bool | None]:
    numeric = roots - {MARKET_INDEX, "symbol"}
    inputs = (numeric & BASE) | ({"open", "high", "low", "close", "volume"} if numeric & SUPPORTED_INDICATORS else set())
    missing = inputs - set(frame.columns)
    if missing:
        raise ValueError(f"日线缺少字段：{', '.join(sorted(missing))}")
    frame = frame.with_columns(
        pl.when(pl.col(k).is_finite() & (pl.col(k) >= 0 if k in {"volume", "amount"} else pl.col(k) > 0))
        .then(pl.col(k)).alias(k) for k in sorted(inputs)
    )
    needed = numeric & SUPPORTED_INDICATORS
    if needed:
        frame = compute_indicators(frame, needed=needed, assume_sorted=True)
        frame = frame.with_columns(pl.when(pl.col(k).is_finite()).then(pl.col(k)).alias(k) for k in sorted(needed))
    if MARKET_INDEX in roots:
        frame = attach_calendar(frame, calendar)
    valid = pl.lit(True)
    if roots & LIMIT_FIELDS:
        # Same-day limit built-ins: false AND unknown must not become false
        # when the historical limit or an input quote is missing.
        for root in sorted(numeric):
            valid &= pl.col(root).is_finite()
    return frame.select(
        pl.when(valid).then(expr.cast(pl.Boolean)).otherwise(pl.lit(None, dtype=pl.Boolean)).alias("flag")
    )["flag"].to_list()


def _with_limit_prices(frame, calendar, limit_inputs, gaps):
    instruments, adj_events = limit_inputs
    frame = price_facts(attach_calendar(frame, calendar), instruments, calendar, adj_events=adj_events)
    expressions = []
    contiguous = ~pl.col("date").is_in([frame["date"][i] for i in sorted(gaps)])
    for side in ("up", "down"):
        field = f"limit_{side}_price"
        price = pl.col(field)
        official = pl.col(f"_official_limit_{side}").is_between(0, 10000, closed="none")
        valid = price.is_finite() & (price > 0) & ~pl.col("_unlimited").fill_null(False)
        valid &= official.fill_null(False) | contiguous
        expressions.extend([
            pl.when(valid).then(price).otherwise(None).alias(field),
            pl.when(valid).then(pl.when(official).then(pl.lit("recorded")).otherwise(pl.lit("inferred")))
            .alias(f"_{field}_source"),
        ])
    return frame.with_columns(expressions)


def _pivots(frame: pl.DataFrame) -> list[dict]:
    days = [day.isoformat() for day in frame["date"]]
    index = {day: i for i, day in enumerate(days)}
    structure = compute_smc_structure(
        frame, swing_length=SWING_LENGTH, min_swing_atr=0.5, price_tolerance=0.001, break_buffer_atr=0.05,
    )
    return [
        {**point, "index": index[point["date"]],
         "id": f"{point['type']}:{point['date']}:{point['price']}"}
        for point in structure["swing_points"]
    ]


def _alerts(flags: list[bool | None], dates: list[str], gaps: frozenset[int] = frozenset()) -> list[dict]:
    result: list[dict] = []
    for index, flag in enumerate(flags):
        if flag is not True:
            continue
        if index in gaps:
            continue
        if result and result[-1]["end_index"] == index - 1:
            result[-1].update(end_index=index, end_date=dates[index])
            result[-1]["trigger_dates"].append(dates[index])
        elif index and flags[index - 1] is False:
            result.append({"id": dates[index], "start_index": index, "end_index": index,
                           "start_date": dates[index], "end_date": dates[index], "trigger_dates": [dates[index]]})
        # A true run preceded by unknown data is not a known new alert.
    return result


def match_events(pivots, alerts, direction, *, symbol="", signal_id="",
                 pivot_complete=None, alert_complete=None, pair_complete=None):
    targets = sorted((p for p in pivots if p["type"] == direction), key=lambda p: p["index"])
    alerts = sorted(alerts, key=lambda a: (a["start_index"], a["id"]))
    cases = []
    used = set()
    windows = dict.fromkeys((key for key, _, _ in WINDOWS), 0)

    def add(kind, pivot=None, alert=None):
        distance = pivot["index"] - alert["start_index"] if pivot and alert else None
        window = next((key for key, low, high in WINDOWS if distance is not None and low <= distance <= high), None)
        cases.append({
            "case_id": f"{symbol}/{signal_id}/{direction}/{kind}/{pivot['id'] if pivot else '-'}/{alert['id'] if alert else '-'}",
            "case_type": kind, "symbol": symbol, "signal_id": signal_id, "direction": direction,
            "pivot": pivot, "alert": alert, "lead_bars": distance, "window": window,
        })
        if kind == "matched":
            windows[window] += 1

    def eligible(pivot, alert):
        return (1 <= pivot["index"] - alert["start_index"] <= MAX_LEAD
                and (pair_complete is None or pair_complete(alert["start_index"], pivot["index"])))

    # Assign the oldest eligible alert to the earliest pivot, to avoid consuming
    # newer alerts that can still cover a later pivot in an overlapping window.
    for pivot in targets:
        alert = next((a for a in alerts if a["id"] not in used and eligible(pivot, a)), None)
        if alert:
            used.add(alert["id"])
            add("matched", pivot, alert)
        else:
            add("missed" if pivot_complete is None or pivot_complete(pivot) else "indeterminate_pivot", pivot)
    for alert in alerts:
        if alert["id"] in used:
            continue
        pivot = next((p for p in targets if eligible(p, alert)), None)
        if pivot:
            add("duplicate", pivot, alert)
        else:
            add("unmatched" if alert_complete is None or alert_complete(alert) else "indeterminate_alert", alert=alert)
    counts = dict.fromkeys(CASE_TYPES, 0)
    for case in cases:
        counts[case["case_type"]] += 1
    return {"direction": direction, "pivot_count": len(targets), "alert_count": len(alerts),
            "covered_count": counts["matched"], "matched_alert_count": counts["matched"],
            "missed_count": counts["missed"], "duplicate_alert_count": counts["duplicate"],
            "unmatched_alert_count": counts["unmatched"], "indeterminate_pivot_count": counts["indeterminate_pivot"],
            "indeterminate_alert_count": counts["indeterminate_alert"], "lead_windows": windows, "cases": cases}


def build_review(frame: pl.DataFrame, catalog: dict[str, dict], scope: ReviewScope, calendar: list[date],
                 missing_market_days: tuple[date, ...] = (), *,
                 limit_inputs: tuple[pl.DataFrame, pl.DataFrame] | None = None) -> dict:
    frame = frame.sort("date")
    if frame.is_empty():
        raise ValueError("没有日线数据")
    symbol = frame["symbol"][0]
    if frame["date"].is_duplicated().any() or frame["date"].null_count():
        raise ValueError("日线存在重复或空日期")
    if frame.select(pl.any_horizontal(
        (~pl.col(k).is_finite() | (pl.col(k) <= 0)).fill_null(True) for k in ("open", "high", "low", "close")
    ).any()).item():
        raise ValueError("日线存在无效开高低收，无法判定 L2 标签")
    dates = frame["date"].to_list()
    indices = [i for i, day in enumerate(dates) if scope.start <= day <= scope.end]
    if not indices:
        raise ValueError("研究区间没有该标的的日线")
    first, last = indices[0], indices[-1]
    # The last 15 rows are never eligible for a locally confirmed L2 label.
    label_last = frame.height - 1 - SWING_LENGTH
    summaries, cases = [], []
    chart = frame.select("date", "open", "high", "low", "close",
                         pl.when(pl.col("volume").is_finite()).then(pl.col("volume")).alias("volume")).to_dicts()

    date_text = [d.isoformat() for d in dates]

    def complete_between(a, b):
        return bisect_right(missing_market_days, dates[a]) == bisect_right(missing_market_days, dates[b])
    labelled = [p for p in _pivots(frame) if first <= p["index"] <= last]
    pivots = [p for p in labelled if complete_between(p["index"] - SWING_LENGTH, p["index"] + SWING_LENGTH)]
    gaps = frozenset(i for i in range(1, len(dates)) if not complete_between(i - 1, i))
    limit_frame = None
    for sid in scope.signal_ids:
        summary = describe_signal(catalog[sid])
        try:
            expr, roots, version = _compile(sid, catalog)
            limit_roots = roots & LIMIT_FIELDS
            signal_frame = frame
            if limit_roots:
                if limit_inputs is None:
                    raise ValueError("历史涨跌停价数据源未接入")
                if limit_frame is None:
                    limit_frame = _with_limit_prices(frame, calendar, limit_inputs, gaps)
                signal_frame = limit_frame
            flags = _flags(signal_frame, expr, roots, calendar)
        except (ValueError, TypeError, KeyError, pl.exceptions.PolarsError) as exc:
            summaries.append({**summary, "status": "unavailable", "reason": str(exc), "directions": {}})
            continue
        known = sum(flags[i] is not None for i in indices)
        summary.update(status="ok" if known == len(indices) else "partial" if known else "unavailable",
                       known_bars=known, unknown_bars=len(indices) - known, definition_sha256=version, directions={})
        if limit_roots:
            summary["limit_price_sources"] = {}
            for field in sorted(limit_roots):
                sources = signal_frame[f"_{field}_source"].to_list()
                summary["limit_price_sources"][field] = {
                    "recorded": sum(flags[i] is not None and sources[i] == "recorded" for i in indices),
                    "inferred": sum(flags[i] is not None and sources[i] == "inferred" for i in indices),
                    "unknown": len(indices) - known,
                }
        if not known:
            summary["reason"] = "研究区间无可计算的信号数据"
        alerts = [a for a in _alerts(flags, date_text, gaps) if first <= a["start_index"] <= last]

        def pivot_complete(p, flags=flags):
            i = p["index"]
            # The predecessor bar establishes whether a true run began in the window.
            return (i - MAX_LEAD >= first and i > MAX_LEAD
                    and complete_between(i - MAX_LEAD - 1, i)
                    and all(v is not None for v in flags[i - MAX_LEAD - 1:i]))

        def alert_complete(a):
            i = a["start_index"]
            return (i + MAX_LEAD <= min(last, label_last) and frame.height >= 2 * SWING_LENGTH + 3
                    and complete_between(i, i + MAX_LEAD + SWING_LENGTH))

        if known:
            for direction in scope.directions:
                result = match_events(pivots, alerts, direction, symbol=symbol, signal_id=sid,
                                      pivot_complete=pivot_complete, alert_complete=alert_complete,
                                      pair_complete=complete_between)
                for case in result.pop("cases"):
                    alert = case["alert"]
                    case["adverse_excursion_5_bars"] = None
                    if alert:
                        i = alert["start_index"]
                        if i + 5 <= last and complete_between(i, i + 5):
                            close = chart[i]["close"]
                            next_bars = chart[i + 1:i + 6]
                            adverse = (max(b["high"] for b in next_bars) / close - 1 if direction == "high"
                                       else 1 - min(b["low"] for b in next_bars) / close)
                            case["adverse_excursion_5_bars"] = max(0.0, adverse)
                    cases.append(case)
                summary["directions"][direction] = result
        summaries.append(summary)
    return {
        "data": {"symbol": symbol, "from_date": dates[0], "to_date": dates[-1], "study_bars": len(indices),
                 "label_through": dates[label_last] if label_last >= 0 and frame.height >= 2 * SWING_LENGTH + 3 else None},
        "signals": summaries, "cases": cases, "chart": chart,
        "directions": {
            d: {"pivot_count": sum(p["type"] == d for p in pivots),
                "label_excluded_for_market_gap": sum(p["type"] == d for p in labelled) - sum(p["type"] == d for p in pivots)}
            for d in scope.directions
        },
    }
