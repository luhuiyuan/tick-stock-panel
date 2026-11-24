"""Bounded daily signal research; returns are adjusted marks, not simulated fills."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import date

import polars as pl

from app.factors.premarket import FACTORS
from app.factors.registry import get_factor, isolated_registry
from app.factors.store import register_definition
from app.indicators.pipeline import compute_indicators
from app.services.coverage_limits import price_facts
from app.services.xdxr_ref import xd_ref_expr
from app.strategy import custom_signals as signals
from app.strategy.strict_signal_data import MARKET_INDEX, attach_calendar

RULE = "signal-forward-performance-v5"
HORIZONS = (1, 5, 10)
BASE = {"open", "high", "low", "close", "volume", "amount", "raw_close", "raw_high", "raw_low"}
# Only reviewed backward-looking columns. Do not consume persisted signal columns,
# arbitrary factor plugins or the coverage service's inferred historical limits.
INDICATORS = {
    "ma5",
    "ma10",
    "ma20",
    "ma30",
    "ma60",
    "ma120",
    "vol_ratio_5d",
    "change_pct",
    "prev_close",
    "amplitude",
    "momentum_5d",
    "momentum_10d",
    "momentum_20d",
    "momentum_30d",
    "momentum_60d",
}
# Pin the reviewed formulas, not just their user-editable IDs.
REVIEWED_IDS = {
    "uf_abs_return_1d",
    "uf_min_return_3d",
    "uf_prev_high_prev20_high_bias",
    "uf_close_prev20_high_bias",
}
REVIEWED_FACTORS = {factor["id"]: factor for factor in FACTORS if factor["id"] in REVIEWED_IDS}
ADJUSTED_LEVELS = {
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "ma5",
    "ma10",
    "ma20",
    "ma30",
    "ma60",
    "ma120",
}
KEYS = ["symbol", "date"]


def _valid_price(expr: pl.Expr) -> pl.Expr:
    return (expr.is_finite() & (expr > 0)).fill_null(False)


def _validate_keys(frame: pl.DataFrame) -> None:
    if any(frame[k].null_count() for k in KEYS) or frame.select(KEYS).is_duplicated().any():
        raise ValueError("日线数据存在空键或重复证券日期键")


@contextmanager
def _reviewed_registry(ids):
    if not ids:
        yield
        return
    # Initialise the shared built-ins before copying the registry.
    get_factor("close")
    with isolated_registry() as registry:
        for fid in sorted(ids):
            registry.pop(fid, None)
            register_definition(REVIEWED_FACTORS[fid])
        yield


def compile_signals(catalog: dict[str, dict], ids: Sequence[str]) -> tuple[dict, dict]:
    with _reviewed_registry(REVIEWED_IDS):
        return _compile_signals(catalog, ids)


def _compile_signals(catalog: dict[str, dict], ids: Sequence[str]) -> tuple[dict, dict]:
    expressions, dependencies = {}, {}
    for sid in ids:
        if sid not in catalog:
            raise ValueError(f"未找到信号定义：{sid}")
        definition = catalog[sid]
        if definition.get("enabled") is False or definition.get("timeframe") != "daily":
            raise ValueError(f"仅支持已启用的日线信号：{sid}")
        definitions = {sid: definition}
        for ref in signals.expr_refs(definition.get("expr") or {}):
            atom = catalog.get(ref)
            if (
                not atom
                or atom.get("timeframe") != "daily"
                or signals.signal_type(atom) != signals.TYPE_ATOM
            ):
                raise ValueError(f"缺少可用日线原子：{ref}")
            definitions[ref] = atom
        for item in definitions.values():
            signals.validate(item)
            for condition in item.get("conditions", ()):
                left = condition["left"]
                right = str(condition["right"]).removeprefix("field:")
                # Historical qfq levels can be uniformly rescaled by later corporate
                # actions. Keep scale-invariant comparisons, not absolute qfq levels.
                if (left in ADJUSTED_LEVELS) != (right in ADJUSTED_LEVELS):
                    raise ValueError(f"历史依赖未获时点验证：{sid}：前复权绝对价或混合价格比较")
        expr = signals.build_expressions([definition], catalog=definitions).get(
            signals.resolve_column(definition)
        )
        if expr is None:
            raise ValueError(f"无法编译信号：{sid}")
        roots = set(expr.meta.root_names())
        unsupported = roots - BASE - INDICATORS - REVIEWED_IDS - {MARKET_INDEX, "symbol"}
        if unsupported:
            raise ValueError(f"历史依赖未获时点验证：{sid}：{','.join(sorted(unsupported))}")
        expressions[sid] = expr
        dependencies[sid] = {"definitions": definitions, "fields": sorted(roots)}
        if roots & REVIEWED_IDS:
            dependencies[sid]["reviewed_factors"] = {
                fid: REVIEWED_FACTORS[fid] for fid in sorted(roots & REVIEWED_IDS)
            }
    return expressions, dependencies


def _execution_limits(
    frame: pl.DataFrame,
    calendar: list[date],
    adj_events: pl.DataFrame | None,
) -> pl.DataFrame:
    """Use retrospective rules only for entry outcomes, never for signal flags."""
    col = pl.col
    official = _valid_price(col("official_limit_up"))
    inst = frame.filter(official | col("dated_instrument_name").is_not_null()).select(
        "symbol",
        col("date").alias("as_of"),
        col("official_limit_up").alias("limit_up"),
        col("dated_instrument_name").alias("name"),
    )
    facts = price_facts(frame, inst, calendar, adj_events=adj_events)
    ratio = col("close") / col("raw_close")
    ratio_shift = (ratio - ratio.shift().over("symbol")).abs() > 1e-6
    _, xd_valid = xd_ref_expr()
    # Coverage itself retains its approximate adjustment fallback. For entry assessment,
    # a detected adjustment without usable event terms is not reliable enough.
    needs_xd = ratio_shift.fill_null(False)
    if adj_events is not None and not adj_events.is_empty():
        keys = adj_events.select("symbol", pl.col("trade_date").alias("date")).unique()
        facts = facts.join(
            keys.with_columns(pl.lit(True).alias("_has_xd_event")),
            on=KEYS,
            how="left",
            maintain_order="left",
        )
        needs_xd |= col("_has_xd_event").fill_null(False)
    inferred = _valid_price(col("_inferred_up")) & (~needs_xd | xd_valid.fill_null(False))
    return facts.with_columns(
        pl.when(official)
        .then(col("official_limit_up"))
        .when(inferred)
        .then(col("_inferred_up"))
        .alias("_execution_limit_up"),
        pl.when(official)
        .then(pl.lit("dated_instrument"))
        .when(inferred)
        .then(pl.lit("rule_inferred"))
        .alias("_execution_limit_source"),
    )


def _outcomes(
    frame: pl.DataFrame, calendar: list[date], split: date, end: date, horizons: tuple[int, ...]
) -> pl.DataFrame:
    col = pl.col
    idx = col(MARKET_INDEX)

    def lead(field, n):
        return col(field).shift(-n).over("symbol")

    next_day = pl.Series("entry_date", [*calendar[1:], None], dtype=pl.Date)
    frame = frame.with_columns(pl.lit(next_day).gather(idx).alias("entry_date"))
    discovery = col("date") < split
    in_entry_split = (
        (col("entry_date") <= end) & (~discovery | (col("entry_date") < split))
    ).fill_null(False)
    entry_bar = (lead(MARKET_INDEX, 1) == idx + 1).fill_null(False) & in_entry_split
    raw = lead("raw_open", 1)
    up = lead("_execution_limit_up", 1)
    source = lead("_execution_limit_source", 1)
    # The dated official table uses 10000+ for no limit. At the upper limit
    # the conservative model excludes the open; queue priority isn't observable.
    evidence = entry_bar & _valid_price(raw) & _valid_price(up) & _valid_price(lead("open", 1))
    blocked = (up < 10000) & (raw >= up - 0.005)
    frame = frame.with_columns(
        pl.when(evidence & blocked)
        .then(pl.lit("not_executable"))
        .when(evidence)
        .then(pl.lit("executable"))
        .otherwise(pl.lit("unknown"))
        .alias("execution_status"),
        pl.when(evidence & blocked)
        .then(pl.lit("open_at_upper_limit"))
        .when(evidence)
        .then(pl.lit("below_upper_limit_or_unlimited"))
        .when(~in_entry_split)
        .then(pl.lit("entry_outside_split"))
        .when(~entry_bar)
        .then(pl.lit("missing_next_session_bar"))
        .otherwise(pl.lit("missing_open_or_limit_evidence"))
        .alias("execution_reason"),
        pl.when(entry_bar).then(lead("open", 1)).alias("entry_price_adjusted"),
        pl.when(entry_bar).then(raw).alias("entry_price_raw"),
        pl.when(entry_bar).then(up).alias("entry_limit_up"),
        pl.when(entry_bar).then(source).alias("entry_limit_source"),
    )
    frame = frame.with_columns(
        pl.when(discovery).then(pl.lit("discovery")).otherwise(pl.lit("validation")).alias("split")
    )
    exprs = []
    for h in horizons:
        # Entry is at the next session's open; A-share T+1 means the first
        # eligible exit close is one additional session later.
        offset = h + 1
        target = pl.lit(
            pl.Series(
                "target", calendar[offset:] + [None] * min(offset, len(calendar)), dtype=pl.Date
            )
        ).gather(idx)
        end_close = lead("close", offset)
        complete = (
            (lead(MARKET_INDEX, offset) == idx + offset) & _valid_price(end_close)
        ).fill_null(False)
        in_split = (target <= end) & (~discovery | (target < split))
        valid = (col("execution_status") == "executable") & complete & in_split
        entry = col("entry_price_adjusted")
        low = pl.when(_valid_price(col("low"))).then(col("low"))
        high = pl.when(_valid_price(col("high"))).then(col("high"))
        exprs.extend(
            [
                target.alias(f"exit_date_{h}d"),
                pl.when(valid).then(end_close / entry - 1).alias(f"return_{h}d"),
                pl.when(valid)
                .then(
                    (low.rolling_min(offset).shift(-offset).over("symbol") / entry - 1).clip(
                        upper_bound=0
                    )
                )
                .alias(f"max_adverse_{h}d"),
                pl.when(valid)
                .then(
                    (high.rolling_max(offset).shift(-offset).over("symbol") / entry - 1).clip(
                        lower_bound=0
                    )
                )
                .alias(f"max_favorable_{h}d"),
            ]
        )
    return frame.with_columns(exprs)


def summarize(
    events: pl.DataFrame, horizons: tuple[int, ...] = HORIZONS, *, monthly=False
) -> pl.DataFrame:
    keys = ["signal_id", "signal_name", "signal_type", "split"]
    if monthly:
        keys.append("month")
        events = events.with_columns(pl.col("signal_date").dt.strftime("%Y-%m").alias("month"))
    parts = []
    for h in horizons:
        ret, excess, adverse = (
            pl.col(f"{field}_{h}d") for field in ("return", "excess_return", "max_adverse")
        )
        status = pl.col("execution_status")
        parts.append(
            events.group_by(keys)
            .agg(
                pl.len().alias("first_trigger_count"),
                pl.col("symbol").n_unique().alias("unique_symbol_count"),
                (status == "executable").sum().alias("executable_count"),
                (status == "executable").mean().alias("executable_rate"),
                (status == "not_executable").sum().alias("not_executable_count"),
                (status == "unknown").sum().alias("unknown_execution_count"),
                ret.count().alias("evaluable_count"),
                ret.mean().alias("mean_return"),
                ret.median().alias("median_return"),
                (ret > 0).mean().alias("positive_return_rate"),
                *[
                    ret.quantile(q / 100, interpolation="linear").alias(f"p{q}_return")
                    for q in (10, 25, 75, 90)
                ],
                excess.count().alias("benchmark_evaluable_count"),
                excess.mean().alias("mean_excess_return"),
                excess.median().alias("median_excess_return"),
                (excess > 0).mean().alias("positive_excess_rate"),
                adverse.mean().alias("mean_max_adverse"),
                adverse.median().alias("median_max_adverse"),
                (-adverse).quantile(0.9, interpolation="linear").alias("p90_adverse_loss"),
            )
            .with_columns(pl.lit(h).alias("horizon"))
        )
    return pl.concat(parts).sort([*keys, "horizon"])


def summarize_monthly(events: pl.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pl.DataFrame:
    return summarize(events, horizons, monthly=True)


def compute_forward_performance(
    frame: pl.DataFrame,
    catalog: dict[str, dict],
    signal_ids: Sequence[str],
    date_from: date,
    date_to: date,
    split_date: date,
    *,
    trading_dates: Sequence[date],
    horizons: tuple[int, ...] = HORIZONS,
    min_benchmark: int = 5,
    adj_events: pl.DataFrame | None = None,
    checkpoint: Callable[[], None] = lambda: None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Measure returns from next-open entry to T+1-eligible later-session closes."""
    calendar = list(trading_dates)
    if (
        calendar != sorted(set(calendar))
        or date_from not in calendar
        or date_to not in calendar
        or not date_from < split_date <= date_to
        or not horizons
        or len(set(horizons)) != len(horizons)
        or not set(horizons) <= set(HORIZONS)
        or min_benchmark < 1
        or not signal_ids
        or len(set(signal_ids)) != len(signal_ids)
    ):
        raise ValueError("无效研究日期、信号、窗口或基准样本门槛")
    expressions, deps = compile_signals(catalog, signal_ids)
    required = set(KEYS) | {"open", "high", "low", "close"}
    if not required <= set(frame.columns) or frame.is_empty():
        raise ValueError("没有足够的日线输入字段或记录")
    numeric = BASE | {"raw_open", "official_limit_up"}
    frame = frame.select(
        *KEYS,
        (
            pl.col("dated_instrument_name")
            if "dated_instrument_name" in frame.columns
            else pl.lit(None, pl.String)
        ).alias("dated_instrument_name"),
        *(
            pl.when(pl.col(k).cast(pl.Float64, strict=False).is_finite())
            .then(pl.col(k).cast(pl.Float64, strict=False))
            .alias(k)
            if k in frame.columns
            else pl.lit(None, pl.Float64).alias(k)
            for k in sorted(numeric)
        ),
    ).with_columns(pl.col("date").cast(pl.Date))
    _validate_keys(frame)
    if not set(frame["date"].unique()) <= set(calendar):
        raise ValueError("行情日期不在权威交易日历内")
    frame = attach_calendar(frame.sort(KEYS), calendar)
    checkpoint()
    fields = set().union(*(set(d["fields"]) for d in deps.values()))
    needed = fields & INDICATORS
    if needed:
        frame = compute_indicators(frame, needed=needed, assume_sorted=True)
    factor_ids = fields & REVIEWED_IDS
    if factor_ids:
        with _reviewed_registry(factor_ids):
            frame = signals.materialize_factor_columns(frame, expressions, trading_dates=calendar)
        missing = factor_ids - set(frame.columns)
        if missing:
            raise ValueError(f"未能计算已审核因子：{','.join(sorted(missing))}")
    # Freeze flags before adding outcome columns, without modifying structural keys.
    names = {sid: f"_flag_{i}" for i, sid in enumerate(signal_ids)}
    if needed or factor_ids:
        frame = frame.with_columns(
            pl.when(pl.col(k).is_finite()).then(pl.col(k)).alias(k)
            for k in sorted(needed | factor_ids)
        )
    frame = frame.with_columns(expr.alias(names[sid]) for sid, expr in expressions.items())
    checkpoint()
    frame = _execution_limits(frame, calendar, adj_events)
    checkpoint()
    outcomes = _outcomes(frame, calendar, split_date, date_to, horizons)
    benchmark = (
        outcomes.filter(pl.col("date").is_between(date_from, date_to))
        .group_by("date")
        .agg(
            *[
                expr
                for h in horizons
                for expr in (
                    pl.col(f"return_{h}d").count().alias(f"benchmark_count_{h}d"),
                    pl.when(pl.col(f"return_{h}d").count() >= min_benchmark)
                    .then(pl.col(f"return_{h}d").median())
                    .alias(f"benchmark_{h}d"),
                )
            ]
        )
    )
    output = []
    for sid, flag in names.items():
        checkpoint()
        previous = pl.col(flag).forward_fill().shift(1).over("symbol")
        # Only a confirmed false rearms the signal; unknown never creates a new episode.
        first = pl.col(flag) & previous.eq(False)
        definition = catalog[sid]
        output.append(
            outcomes.filter(first & pl.col("date").is_between(date_from, date_to)).select(
                *KEYS,
                "entry_date",
                "entry_price_raw",
                "entry_price_adjusted",
                "entry_limit_up",
                "entry_limit_source",
                "execution_status",
                "execution_reason",
                "split",
                "^(exit_date|return|max_adverse|max_favorable)_.*$",
                pl.lit(sid).alias("signal_id"),
                pl.lit(definition["name"]).alias("signal_name"),
                pl.lit(signals.signal_type(definition)).alias("signal_type"),
            )
        )
    events = (
        pl.concat(output).join(benchmark, on="date", how="left").rename({"date": "signal_date"})
    )
    events = events.with_columns(
        (pl.col(f"return_{h}d") - pl.col(f"benchmark_{h}d")).alias(f"excess_return_{h}d")
        for h in horizons
    ).sort(["signal_id", "symbol", "signal_date"])
    return events, summarize(events, horizons)
