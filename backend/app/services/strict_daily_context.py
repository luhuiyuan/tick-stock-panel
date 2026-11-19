"""普通日线严格事实适配；不下载、不补数、不写缓存，不信任旧填零结果。

市场快照必须有显式完整性/可用日期证据；旧缓存缺这些字段时返回未知。
历史封板只接受同日权威限价，不把当前证券身份推断当作历史事实。
"""

from __future__ import annotations

import polars as pl

from app.strategy.strict_signal_data import EVENT_FIELDS, MARKET_FIELDS


def attach_limit_facts(
    frame: pl.DataFrame, instruments: pl.DataFrame, *, trading_dates=None
) -> pl.DataFrame:
    """逐日权威限价事实。缺历史来源不能靠legacy CLU填零或补连板数。"""
    blank = [pl.lit(None, dtype=pl.Float64).alias(c) for c in sorted(EVENT_FIELDS)]
    if not {"symbol", "date", "raw_close", "raw_high"} <= set(frame.columns):
        return frame.with_columns(blank)
    if not {"symbol", "as_of", "limit_up"} <= set(instruments.columns):
        return frame.with_columns(blank)
    # 可接收逐日权威快照；重复证券/日期一律拒绝，而不是任取一份。
    inst = instruments.select(
        "symbol",
        pl.col("as_of").cast(pl.Date, strict=False).alias("date"),
        pl.col("limit_up").cast(pl.Float64, strict=False).alias("__official_limit"),
    )
    inst = inst.filter(~pl.struct("symbol", "date").is_duplicated())
    out = frame.drop(list(EVENT_FIELDS), strict=False).join(
        inst, on=["symbol", "date"], how="left", maintain_order="left"
    )
    price = pl.col("__official_limit")
    rc, rh = pl.col("raw_close"), pl.col("raw_high")
    known = (
        price.is_finite() & (price > 0) & rc.is_finite() & rh.is_finite() & (rc > 0) & (rh >= rc)
    )
    sealed = pl.when(price >= 10000).then(False).otherwise(rc >= price - 0.005)
    broken = pl.when(price >= 10000).then(False).otherwise((rh >= price - 0.005) & ~sealed)
    out = out.with_columns(
        pl.when(known).then(sealed.cast(pl.Float64)).otherwise(None).alias("strict_close_limit"),
        pl.when(known).then(broken.cast(pl.Float64)).otherwise(None).alias("strict_broken_limit"),
    )
    from app.strategy.strict_signal_data import MARKET_INDEX, attach_calendar

    if trading_dates is None:
        from app.services.trading_day import cached_trading_calendar

        trading_dates = cached_trading_calendar()
    had_index = MARKET_INDEX in out.columns
    out = attach_calendar(out, trading_dates)
    idx, sealed = pl.col(MARKET_INDEX), pl.col("strict_close_limit")
    # 缺事实、日期跳跃与重复记录均切断证据链；只有已知非板日才能锚定连续板数。
    broken_chain = (sealed.is_null() | idx.is_null() | (idx.diff().over("symbol") != 1)).fill_null(
        True
    )
    out = out.with_columns(broken_chain.cast(pl.Int64).alias("__event_epoch"))
    out = out.with_columns(pl.col("__event_epoch").cum_sum().over("symbol"))
    out = out.with_columns(
        pl.when(sealed == 0)
        .then(idx)
        .otherwise(None)
        .forward_fill()
        .over("symbol", "__event_epoch")
        .alias("__event_anchor")
    )
    out = out.with_columns(
        pl.when(sealed == 0)
        .then(0.0)
        .when((sealed == 1) & idx.is_not_null())
        .then(idx - pl.col("__event_anchor"))
        .otherwise(None)
        .cast(pl.Float64)
        .alias("strict_limit_streak")
    )
    out = out.drop("__official_limit", "__event_epoch", "__event_anchor")
    return out if had_index else out.drop(MARKET_INDEX)


def attach_market_facts(
    frame: pl.DataFrame, history: pl.DataFrame, fields: set[str]
) -> pl.DataFrame:
    """按日期精确匹配完整快照；不前向填充、不从筛选后的股票子集聚合。"""
    fields = fields & MARKET_FIELDS
    if not fields:
        return frame
    blank = [pl.lit(None, dtype=pl.Float64).alias(c) for c in sorted(fields)]
    required = {
        "date",
        "source_complete",
        "available_date",
        "source_version",
        "limit_up",
        "broken_limit",
        "state",
    }
    if not required <= set(history.columns) or history.is_empty():
        return frame.with_columns(blank)
    if history.schema["source_complete"] != pl.Boolean:
        return frame.with_columns(blank)
    h = history.with_columns(
        pl.col("date").cast(pl.Date, strict=False),
        pl.col("available_date").cast(pl.Date, strict=False),
        pl.col("limit_up").cast(pl.Float64, strict=False),
        pl.col("broken_limit").cast(pl.Float64, strict=False),
        pl.col("source_version").cast(pl.String, strict=False),
    ).sort("date")
    lu, br = pl.col("limit_up"), pl.col("broken_limit")
    states = ["strong", "lean_strong", "range", "lean_weak", "weak"]
    good = (
        pl.col("source_complete")
        & (pl.col("source_version").str.strip_chars().str.len_chars() > 0)
        & (pl.col("available_date") <= pl.col("date"))
        & ~pl.col("date").is_duplicated()
        & lu.is_finite()
        & br.is_finite()
        & (lu >= 0)
        & (br >= 0)
        & (lu == lu.floor())
        & (br == br.floor())
        & pl.col("state").is_in(states)
    )
    h = h.with_columns(good.fill_null(False).alias("__complete"))
    denom = lu + br
    h = h.with_columns(
        pl.when(pl.col("__complete"))
        .then(lu)
        .otherwise(None)
        .cast(pl.Float64)
        .alias("strict_market_limits"),
        pl.when(pl.col("__complete"))
        .then(br)
        .otherwise(None)
        .cast(pl.Float64)
        .alias("strict_market_broken"),
        pl.when(pl.col("__complete") & (denom > 0))
        .then(lu / denom)
        .otherwise(None)
        .alias("strict_market_seal"),
        pl.when(pl.col("__complete"))
        .then(pl.col("state").is_in(["weak", "lean_weak"]).cast(pl.Float64))
        .otherwise(None)
        .alias("strict_market_weak"),
    )
    if "strict_market_cold" in fields:
        from app.services.market_phase import classify_phase_series

        needs = {"max_consecutive", "first_board", "ge2_count", "promo_rate", "seal_rate"}
        if needs <= set(h.columns):
            # 旧缓存seal_rate可能使用分母0默认0.5；严格路径使用显式计算的比率。
            h = h.with_columns(pl.col("strict_market_seal").alias("seal_rate"))
            # 递归相位不能跨过未证实的交易日缺口。
            from app.services.trading_day import cached_trading_calendar
            from app.strategy.strict_signal_data import MARKET_INDEX, attach_calendar

            h = attach_calendar(
                h.with_columns(pl.lit("__market__").alias("symbol")), cached_trading_calendar()
            )
            idx = pl.col(MARKET_INDEX)
            continuous = idx.is_not_null() & ((idx.diff() == 1) | (pl.int_range(pl.len()) == 0))
            h = h.with_columns(
                (pl.col("__complete") & continuous.fill_null(False)).alias("__complete")
            )
            h = h.with_columns(
                [
                    pl.when(pl.col("__complete")).then(pl.col(c)).otherwise(None).alias(c)
                    for c in needs
                ]
            )
            h = classify_phase_series(h, strict=True)
            h = h.with_columns(
                pl.when(pl.col("__complete") & pl.col("phase").is_not_null())
                .then(pl.col("phase").is_in(["ice", "ebb"]).cast(pl.Float64))
                .otherwise(None)
                .alias("strict_market_cold")
            )
        else:
            h = h.with_columns(pl.lit(None, dtype=pl.Float64).alias("strict_market_cold"))
    # 重复日期都已经无效；去重仅为保证join不扩张样本行数。
    h = h.select("date", *sorted(fields)).unique("date", keep="first")
    return frame.drop(sorted(fields), strict=False).join(
        h, on="date", how="left", maintain_order="left"
    )


def prepare_context(frame: pl.DataFrame, requested: set[str]) -> pl.DataFrame:
    """评分/普通信号共用按需入口；现有服务读取签名缓存，无额外持久化。"""
    fields = (requested & MARKET_FIELDS) - set(frame.columns)
    if fields:
        from app.config import settings
        from app.services.regime_builder import load_regime_history

        history = load_regime_history(settings.data_dir)
        frame = attach_market_facts(frame, history, fields)
    return frame
