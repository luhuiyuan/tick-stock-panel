"""Price-limit evidence exclusively for historical surge coverage (not live trading)."""

import numpy as np
import polars as pl

from app.price_limits import MAIN_BOARD_ST_LIMIT_CHANGE_DATE, polars_limit_price, polars_price_limit_pct
from app.services.strict_daily_context import attach_limit_facts
from app.strategy.strict_signal_data import MARKET_INDEX

COUNTS = {"strict_market_limits", "strict_market_broken", "strict_market_seal"}
PRICES = {"limit_up_price", "limit_down_price"}
ROSTER = "surge_research/market_universe-v1.parquet"
POLICY = (
    "同日维表有效限价优先，否则按规则推算(默认非ST按板块比例; "
    "前后一月窗口内精准触及5%涨跌停价且无破5%证据才判ST按5%; 除权日用权息修正昨收)"
)

# ST 反推参数: 目标日前后各 31 个日历天(约一月), 窗口内 >=2 次精准 5% 触板 => ST。
ST_WINDOW_DAYS = 31
ST_TOUCH_TOL = 0.01       # 触及价容差(元), 覆盖四舍五入误差
ST_MIN_TOUCHES = 2        # 判 ST 所需的 5% 触板次数
ST_VETO_PCT = 0.055       # high 涨幅超过 5.5% => 真 ST 不可能, 一票否决


def _st_from_5pct_touches(symbols, dates, touches, vetoes):
    """按 symbol 统计每根 K 线前后 ST_WINDOW_DAYS 天内的 5% 触板次数, 反推 ST。

    向量化实现(按 symbol 分段 + searchsorted + 前缀和), 与 df 行序一一对应。

    ``touches`` 为逐日"精准触及 5% 涨跌停价"标记(0/1), ``vetoes`` 为逐日
    "high 明显突破 5% 涨停价"标记(0/1, 真 ST 物理上不可能)。判 ST 条件:
    窗口内触板次数 >= ST_MIN_TOUCHES 且窗口内无任何 veto。
    """
    n = len(symbols)
    out = np.zeros(n, dtype=bool)
    if n == 0:
        return out
    d = np.asarray(dates).astype("datetime64[D]")
    t = np.asarray(touches, dtype=np.int64)
    v = np.asarray(vetoes, dtype=np.int64)
    win = np.timedelta64(ST_WINDOW_DAYS, "D")
    # df 已按 (symbol, date) 升序; 按 symbol 连续分段
    _, starts, counts = np.unique(symbols, return_index=True, return_counts=True)
    for start, count in zip(starts, counts):
        sl = slice(start, start + count)
        seg_d = d[sl]
        lo = np.searchsorted(seg_d, seg_d - win, side="left")
        hi = np.searchsorted(seg_d, seg_d + win, side="right")
        tpre = np.concatenate(([0], np.cumsum(t[sl])))
        vpre = np.concatenate(([0], np.cumsum(v[sl])))
        touches_in_window = tpre[hi] - tpre[lo]
        vetoes_in_window = vpre[hi] - vpre[lo]
        out[sl] = (touches_in_window >= ST_MIN_TOUCHES) & (vetoes_in_window == 0)
    return out


def price_facts(df, inst, days, adj_events=None):
    """计算覆盖榜涨跌停价证据。

    涨停价全部由本地规则计算, 不再依赖任何外部接口: 生效价 = 同日维表价 → 规则推断价。

    ``adj_events`` 可选: 权息事件表(见 xdxr_ref.load, 即 adj_factor)。提供时,
    除权除息日改用高精度修正昨收算规则限价(对齐官方口径), 修复原先复权因子反推
    在除权日的精度偏差。不提供时除权日回退旧口径。

    输出 ``_inferred_up/_inferred_down``(纯规则推断价)供落盘使用。
    """
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
    # 权息事件(除权修正): 提供时按 (symbol, date=trade_date) 左连接,
    # xd_ref_expr 在除权日给出高精度昨收; 未提供时占位列全 null, 回退旧口径。
    from app.services.xdxr_ref import join_adj_events, xd_limit_price_expr, xd_ref_expr

    df = join_adj_events(df, adj_events)
    xd_ref, xd_present = xd_ref_expr()
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
    #
    # 连续性不能再用 ix.diff()==1 硬约束: 个股停牌时该日无 K 线, 复牌首日与上一根
    # K 线的 MARKET_INDEX 会跳变(diff>1), 但昨收(停牌前最后收盘价)真实有效, 涨停价
    # 本就该可算 —— 这与 eltdx 官方口径(用完整工作日历定位前收)一致。真正的门槛是
    # 当日为真实交易日(ix 非 null)且昨收可信, 由 ratio_ok + ref 有效性 + 累计 6 根把守。
    # 长假错误日历(混入法定假日工作日)同样会让 diff 跳变, 但其"昨收"仍是上一根真实
    # 收盘价, 价格依旧正确, 故不再以 diff 作为拒绝条件。
    #
    # ST 口径(价格反推, 默认非 ST):
    #   1. 先按板块规则算涨停价(主板 10% / 创业科创 20% / 北交 30%), 全默认非 ST;
    #   2. 用 ST 的 5% 涨跌停物理约束反推: 若目标日前后一月内多次精准触及 5% 涨跌停价
    #      (>= ST_MIN_TOUCHES), 且窗口内没有 high 突破 5.5%(真 ST 不可能), 判为 ST,
    #      涨停价改按 5% 算。这比名字快照可靠 —— 快照只有单日, 历史 ST 状态不可考。
    #   3. 除权日价格跳空, 该日不参与触板统计(改由 xdxr 修正昨收口径处理)。
    # 维表 name 快照明确含 ST 时作为兜底证据(OR), 提高召回。
    eligible = (
        ix.is_not_null()
        & (ix.cum_count().over("symbol") >= 6)
        & ratio_ok
        & ref.is_finite()
        & (ref > 0)
    )

    # 逐日 5% 价(用昨收参考价 ref, 非除权日即上一根 raw_close)。仅在切换日前的主板
    # 有 5% 档; 创业/科创(20%)、北交(30%)及切换后主板-ST 均为板块价, 反推对他们无效,
    # 让 rate() 自然按板块算即可, 无需特殊屏蔽。
    five_up = polars_limit_price(ref, pl.lit(0.05), up=True)
    five_down = polars_limit_price(ref, pl.lit(0.05), up=False)
    # 触板: high 精准触及 5% 涨停价 或 low 精准触及 5% 跌停价(容差内)。
    # 反证: high 涨幅超过 5.5% —— 真 ST 被 5% 封死, 物理上不可能, 一票否决。
    valid_probe = eligible & ~xd_present & rc.is_finite() & (rc > 0)
    touch_5pct = (
        valid_probe
        & (
            (c("raw_high") - five_up).abs().le(ST_TOUCH_TOL)
            | (c("raw_low") - five_down).abs().le(ST_TOUCH_TOL)
        )
    ).fill_null(False)
    veto_st = (valid_probe & (c("raw_high") / ref - 1 > ST_VETO_PCT)).fill_null(False)
    probe = df.select(
        c("symbol"), c("date"), touch_5pct.alias("_touch"), veto_st.alias("_veto")
    )
    inferred_st = _st_from_5pct_touches(
        probe["symbol"].to_numpy(),
        probe["date"].to_numpy(),
        probe["_touch"].to_numpy(),
        probe["_veto"].to_numpy(),
    )
    st_flag = pl.lit(inferred_st) | st.fill_null(False)  # 价格反推 OR 快照兜底
    good = (
        pl.all_horizontal(
            [c(f).is_finite() & (c(f) > 0) for f in ("raw_high", "raw_close", "raw_low")]
        )
        & (c("raw_high") >= rc)
        & (c("raw_low") <= rc)
    )
    no_limit = c("_official_limit_up") >= 10000

    def rule_price(up):
        """规则限价: 除权日走高精度修正昨收(对齐 eltdx), 其余日走原复权反推口径。

        ``xd_present`` 为真仅当 (symbol, 除权日) 有可用权息事件; 该表达式不预截断
        昨收到分, 修复送股等场景下 polars_limit_price 预截断丢一分的问题。
        """
        pct = rate(st_flag)
        normal = polars_limit_price(ref, pct, up=up)
        xd = xd_limit_price_expr(xd_ref, pct, up=up)
        return pl.when(xd_present).then(xd).otherwise(normal)

    def inferred(up):
        """纯规则推断价(不含接口/维表), 供落盘与找缺口。"""
        return (
            pl.when(~good)
            .then(None)
            .when(no_limit.fill_null(False))
            .then(10000.0)
            .when(eligible)
            .then(rule_price(up))
        )

    def price(up):
        """生效价: 同日维表价 → 规则推断价(除权日走 xdxr 修正)。"""
        official = c("_official_limit_" + ("up" if up else "down"))
        return (
            pl.when(~good)
            .then(None)
            .when(no_limit.fill_null(False))
            .then(10000.0)
            .when(official.is_finite() & (official > 0) & (official < 10000))
            .then(official)
            .when(eligible)
            .then(rule_price(up))
        )

    df = df.with_columns(
        price(True).alias("_effective_up"),
        price(False).alias("_effective_down"),
        inferred(True).alias("_inferred_up"),
        inferred(False).alias("_inferred_down"),
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
