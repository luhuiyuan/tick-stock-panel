"""除权除息日昨收修正 —— 复用 adj_factor 权息四元组，对齐 eltdx 口径。

动机
--------------------------------------------------------------------------
涨停价规则推断在普通日与 eltdx 高度一致，但在除权除息日有偏差：现有
``coverage_limits.price_facts`` 用复权因子反推昨收 ``prev(close)/adj``，精度不足；
eltdx 则用权息事件算出**高精度修正昨收**，直接乘涨跌幅再四舍五入到分。

``data/adj_factor/all.parquet`` 已存全市场权息四元组，与 eltdx 的 c1~c4 数学等价::

    eltdx: D=c1(每10股分红) P=c2(配股价) S=c3(每10股送转) R=c4(每10股配股)
           m = (10+S+R)/10 ; c = (D-R*P)/10 ; 修正昨收 = (前收-c)/m
    adj_factor(除以10后的量): dividend=D/10 bonus=S/10 allot=R/10 allot_price=P
           m = 1 + bonus + allot
           c = dividend - allot*allot_price
           修正昨收 = (prev_close - dividend + allot*allot_price) / (1 + bonus + allot)

实测 38 个除权日样本：用本模块口径算涨停价 100% 命中 eltdx 接口价；
"不修正"对照 38/38 全部失败。故除权日应改用本模块的高精度昨收与限价表达式。

舍入次序很关键：eltdx 用 ``round(高精度昨收 * (1±pct) + 1e-9, 2)``，**不预先把
昨收截到分**；而普通日用的 ``polars_limit_price`` 会先把 previous 截到分。对送股等
产生多位小数的修正昨收，预截断会丢一分（实测 605100.SH 2026-05-25）。因此除权日
必须走本模块的 ``xd_limit_price_expr``。
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# 复用现有 adj_factor 表（QATdx 同步，全市场 2006~2026，主键 symbol+trade_date）。
REL_PATH = "adj_factor/all.parquet"
ADJ_COLUMNS = ["dividend", "bonus", "allot", "allot_price", "prev_close"]
NEED = ["symbol", "trade_date", *ADJ_COLUMNS]

_SCHEMA = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "dividend": pl.Float64,
    "bonus": pl.Float64,
    "allot": pl.Float64,
    "allot_price": pl.Float64,
    "prev_close": pl.Float64,
}


def path_for(data_dir: Path) -> Path:
    return Path(data_dir) / REL_PATH


def load(data_dir: Path) -> pl.DataFrame:
    """读取权息事件表; 缺失/损坏/缺列时降级为空帧(price_facts 自动回退旧口径)。"""
    p = path_for(data_dir)
    if not p.exists():
        return pl.DataFrame(schema=_SCHEMA)
    try:
        df = pl.read_parquet(p)
    except Exception as e:  # 损坏文件不阻断覆盖榜
        logger.warning("adj_factor 读取失败, 除权修正降级关闭: %s", e)
        return pl.DataFrame(schema=_SCHEMA)
    missing = [c for c in NEED if c not in df.columns]
    if missing:
        logger.warning("adj_factor 缺列 %s, 除权修正降级关闭", missing)
        return pl.DataFrame(schema=_SCHEMA)
    try:
        out = df.select([pl.col(c).cast(t, strict=False) for c, t in _SCHEMA.items()])
        return out.filter(pl.col("symbol").is_not_null() & pl.col("trade_date").is_not_null())
    except Exception as e:  # pragma: no cover - 纵深防御
        logger.warning("adj_factor 规范化失败, 除权修正降级关闭: %s", e)
        return pl.DataFrame(schema=_SCHEMA)


def join_adj_events(df: pl.DataFrame, adj_events: pl.DataFrame | None) -> pl.DataFrame:
    """把权息四元组按 (symbol, date=trade_date) 左连接进来, 列前缀 ``xd_``。

    无论是否提供事件表都补齐占位列, 让后续表达式统一可求值(全 null → 回退旧口径)。
    """
    cols = [pl.lit(None, _SCHEMA[c]).alias(f"xd_{c}") for c in ADJ_COLUMNS]
    if (
        adj_events is None
        or adj_events.is_empty()
        or not set(NEED) <= set(adj_events.columns)
    ):
        return df.with_columns(cols)
    adj = adj_events.select(
        pl.col("symbol"),
        pl.col("trade_date").alias("date"),
        *[pl.col(c).cast(_SCHEMA[c], strict=False).alias(f"xd_{c}") for c in ADJ_COLUMNS],
    ).filter(~pl.struct("symbol", "date").is_duplicated())
    out = df.join(adj, on=["symbol", "date"], how="left", maintain_order="left")
    # join 后缺失的占位列(理论上不会, 但兼容极端输入)
    for c in cols:
        name = c.meta.output_name()
        if name not in out.columns:
            out = out.with_columns(c)
    return out


def xd_ref_expr() -> tuple[pl.Expr, pl.Expr]:
    """返回 (高精度修正昨收表达式, 有效掩码表达式)。要求 ``xd_*`` 列已 join。

    修正昨收不截断到分; ``present`` 为真表示该 (symbol, 除权日) 有可用权息事件。
    """
    div = pl.col("xd_dividend")
    bonus = pl.col("xd_bonus")
    allot = pl.col("xd_allot")
    allot_price = pl.col("xd_allot_price")
    prev = pl.col("xd_prev_close")
    denom = 1 + bonus + allot
    ref = (prev - div + allot * allot_price) / denom
    present = (
        div.is_not_null()
        & prev.is_not_null()
        & denom.is_finite()
        & (denom > 0)
        & ref.is_finite()
        & (ref > 0)
    )
    return ref, present


def xd_limit_price_expr(ref: pl.Expr, pct: pl.Expr, *, up: bool) -> pl.Expr:
    """除权日专用限价: round(高精度昨收 × (1±pct) + 1e-9, 2), 不预截断昨收。

    对齐 eltdx ``helpers.daily_price_limits`` 的舍入口径。
    """
    sign = 1 if up else -1
    return ((ref * (1 + sign * pct) + 1e-9) * 100).round(0) / 100
