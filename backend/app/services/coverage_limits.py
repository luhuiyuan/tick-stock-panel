"""Price-limit evidence exclusively for historical surge coverage (not live trading)."""

import polars as pl

from app.price_limits import polars_limit_price, polars_price_limit_pct
from app.services.strict_daily_context import attach_limit_facts
from app.strategy.strict_signal_data import MARKET_INDEX

COUNTS = {"strict_market_limits", "strict_market_broken", "strict_market_seal"}
PRICES = {"limit_up_price", "limit_down_price"}
ROSTER = "surge_research/market_universe-v1.parquet"
POLICY = "同日有效限价优先，否则按规则推算"


def price_facts(df, inst, days):
    c = pl.col
    hist = (
        inst
        if {"symbol", "as_of"} <= set(inst.columns)
        else pl.DataFrame(schema={"symbol": pl.String, "as_of": pl.Date})
    )
    fields = {"limit_up": pl.Float64, "limit_down": pl.Float64, "name": pl.String}
    hist = hist.select(
        "symbol",
        c("as_of").cast(pl.Date).alias("date"),
        *[
            (c(k).cast(t, strict=False) if k in hist.columns else pl.lit(None, t)).alias(
                "_official_" + k
            )
            for k, t in fields.items()
        ],
    )
    hist = hist.filter(~pl.struct("symbol", "date").is_duplicated())
    df = df.sort("symbol", "date").join(
        hist, on=["symbol", "date"], how="left", maintain_order="left"
    )
    ix = c(MARKET_INDEX)
    rc = c("raw_close")
    adj = c("close") / rc
    def prev(e):
        return e.shift().over("symbol")
    ratio_ok = adj.is_finite() & (adj > 0) & prev(adj).is_finite() & (prev(adj) > 0)
    ref = pl.when((adj - prev(adj)).abs() > 1e-6).then(prev(c("close")) / adj).otherwise(prev(rc))
    st = c("_official_name").str.to_uppercase().str.contains("ST", literal=True)
    def rate(v):
        return polars_price_limit_pct(c("symbol"), c("date"), v)
    # Six observations prove that the initial no-limit window has elapsed even
    # without trusting today's instrument name or inferring old ST status.
    eligible = (
        (ix.diff().over("symbol") == 1)
        & (ix.cum_count().over("symbol") >= 6)
        & ratio_ok
        & ref.is_finite()
        & (ref > 0)
        & (st.is_not_null() | (rate(pl.lit(True)) == rate(pl.lit(False))))
    )
    good = (
        pl.all_horizontal(
            [c(f).is_finite() & (c(f) > 0) for f in ("raw_high", "raw_close", "raw_low")]
        )
        & (c("raw_high") >= rc)
        & (c("raw_low") <= rc)
    )
    no_limit = c("_official_limit_up") >= 10000

    def price(up):
        official = c("_official_limit_" + ("up" if up else "down"))
        return (
            pl.when(~good)
            .then(None)
            .when(no_limit.fill_null(False))
            .then(10000.0)
            .when(official.is_finite() & (official > 0) & (official < 10000))
            .then(official)
            .when(eligible)
            .then(polars_limit_price(ref, rate(st.fill_null(False)), up=up))
        )

    df = df.with_columns(
        price(True).alias("_effective_up"),
        price(False).alias("_effective_down"),
        (good & no_limit.fill_null(False)).alias("_unlimited"),
    )
    # Share strict event/streak logic; these are derived limits, not official source records.
    effective = df.select("symbol", c("date").alias("as_of"), c("_effective_up").alias("limit_up"))
    return attach_limit_facts(df, effective, trading_dates=days).with_columns(
        (c("_effective_up") - 0.005).alias("limit_up_price"),
        (c("_effective_down") + 0.005).alias("limit_down_price"),
    )


def market_counts(facts, roster):
    """Exact date and symbol-set equality; no coverage-ratio or row-count proxy."""
    required = {"date", "expected_symbols", "source_version", "source_complete", "available_date"}
    empty = pl.DataFrame(schema={"date": pl.Date, **{f: pl.Float64 for f in COUNTS}})
    if not required <= set(roster.columns) or roster.schema["source_complete"] != pl.Boolean:
        return empty
    r = roster.filter(
        ~pl.col("date").is_duplicated()
        & pl.col("source_complete")
        & (pl.col("available_date") <= pl.col("date"))
        & (pl.col("source_version").str.strip_chars().str.len_chars() > 0)
    )
    rows = []
    for day, expected in r.select("date", "expected_symbols").iter_rows():
        if not expected or None in expected or len(expected) != len(set(expected)):
            continue
        d = facts.filter(pl.col("date") == day)
        if (
            d.height != len(expected)
            or set(d["symbol"]) != set(expected)
            or any(d[f].null_count() for f in ("strict_close_limit", "strict_broken_limit"))
        ):
            continue
        up = d["strict_close_limit"].sum()
        br = d["strict_broken_limit"].sum()
        rows.append(
            {
                "date": day,
                "strict_market_limits": up,
                "strict_market_broken": br,
                "strict_market_seal": up / (up + br) if up + br else None,
            }
        )
    return pl.from_dicts(rows, schema=empty.schema) if rows else empty
