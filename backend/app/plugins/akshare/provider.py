"""AkShare provider for unadjusted daily and single-stock minute bars.

This plugin exposes ``daily`` and a deliberately narrow ``minute`` fallback:
only one stock at a time, 1-minute bars, no full-market synchronization.
It does not provide adjustment factors, realtime, depth, auction, or financial
data.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import polars as pl

from app.data_providers.base import ProviderCapabilities
from app.data_providers.normalizer import normalize_daily
from app.market_time import cn_today

logger = logging.getLogger(__name__)

_DATASETS = ("daily", "minute")
_CORE_INDEXES = (
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000016.SH", "上证50"),
    ("000852.SH", "中证1000"),
)


def _ak() -> Any:
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("AkShare is not installed") from exc
    return ak


def availability() -> tuple[bool, str]:
    """Return dependency availability without making a network request."""
    try:
        import akshare  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"AkShare 依赖不可用: {exc}"
    return True, "ok (AkShare, no API key required)"


def _to_polars(frame: Any) -> pl.DataFrame:
    if frame is None:
        return pl.DataFrame()
    if isinstance(frame, pl.DataFrame):
        return frame
    if hasattr(frame, "reset_index"):
        frame = frame.reset_index()
    try:
        return pl.from_pandas(frame)
    except Exception:  # noqa: BLE001
        try:
            return pl.DataFrame(frame)
        except Exception:  # noqa: BLE001
            return pl.DataFrame()


def _normalize_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if "." in raw:
        code, exchange = raw.split(".", 1)
        return f"{code.zfill(6)}.{exchange[:2]}"
    code = "".join(ch for ch in raw if ch.isdigit()).zfill(6)
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    if code.startswith(("43", "83", "87", "88", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _ak_code(symbol: str) -> str:
    return _normalize_symbol(symbol).split(".", 1)[0]


def _ak_index_code(symbol: str) -> str:
    code, exchange = _normalize_symbol(symbol).split(".", 1)
    prefix = {"SH": "sh", "BJ": "bj"}.get(exchange, "sz")
    return f"{prefix}{code}"


def _ak_stock_prefixed(symbol: str) -> str:
    code, exchange = _normalize_symbol(symbol).split(".", 1)
    prefix = {"SH": "sh", "BJ": "bj"}.get(exchange, "sz")
    return f"{prefix}{code}"


def _as_beijing_wallclock(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    if value.tzinfo is not None:
        return value.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return value


def _normalize_raw_minute(
    raw: Any,
    symbol: str,
    start_time: datetime | date | None,
    end_time: datetime | date | None,
) -> pl.DataFrame:
    df = _to_polars(raw)
    if df.is_empty():
        return df
    rename = {
        "时间": "datetime",
        "日期": "datetime",
        "day": "datetime",
        "trade_time": "datetime",
        "开盘": "open",
        "open": "open",
        "收盘": "close",
        "close": "close",
        "最高": "high",
        "high": "high",
        "最低": "low",
        "low": "low",
        "成交量": "volume",
        "volume": "volume",
        "成交额": "amount",
        "amount": "amount",
    }
    df = df.rename({old: new for old, new in rename.items() if old in df.columns})
    required = {"datetime", "open", "high", "low", "close", "volume"}
    if required - set(df.columns):
        logger.warning(
            "AkShare minute response missing columns for %s: %s",
            symbol, sorted(required - set(df.columns)),
        )
        return pl.DataFrame()
    if "amount" not in df.columns:
        df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("amount"))

    if df.schema["datetime"] == pl.Utf8:
        parsed = pl.coalesce([
            pl.col("datetime").str.strptime(pl.Datetime("us"), fmt, strict=False)
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
            )
        ])
        df = df.with_columns(parsed.alias("datetime"))
    else:
        df = df.with_columns(pl.col("datetime").cast(pl.Datetime("us"), strict=False))

    df = df.with_columns(
        pl.lit(_normalize_symbol(symbol)).alias("symbol"),
        *[
            pl.col(column).cast(pl.Float64, strict=False).alias(column)
            for column in ("open", "high", "low", "close", "volume", "amount")
        ],
    ).drop_nulls(subset=["symbol", "datetime", "open", "high", "low", "close", "volume"])

    start = _as_beijing_wallclock(start_time)
    end = _as_beijing_wallclock(end_time)
    if start is not None:
        df = df.filter(pl.col("datetime") >= start)
    if end is not None:
        df = df.filter(pl.col("datetime") <= end)

    return (
        df.select(["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"])
        .unique(subset=["symbol", "datetime"], keep="last")
        .sort(["symbol", "datetime"])
    )


def _normalize_raw_daily(
    raw: Any, symbol: str, *, allow_missing_amount: bool = False
) -> pl.DataFrame:
    df = _to_polars(raw)
    if df.is_empty():
        return df
    rename = {
        "日期": "date",
        "date": "date",
        "开盘": "open",
        "open": "open",
        "收盘": "close",
        "close": "close",
        "最高": "high",
        "high": "high",
        "最低": "low",
        "low": "low",
        "成交量": "volume",
        "volume": "volume",
        "成交额": "amount",
        "amount": "amount",
    }
    df = df.rename({old: new for old, new in rename.items() if old in df.columns})
    had_amount = "amount" in df.columns
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing or ("amount" not in df.columns and not allow_missing_amount):
        if "amount" not in df.columns and allow_missing_amount:
            df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("amount"))
        else:
            logger.warning("AkShare daily response missing columns for %s: %s", symbol, sorted(missing | ({"amount"} if "amount" not in df.columns else set())))
            return pl.DataFrame()
    df = df.with_columns(pl.lit(_normalize_symbol(symbol)).alias("symbol"))
    normalized = normalize_daily(df, source="akshare")
    if allow_missing_amount and not had_amount and "amount" not in normalized.columns:
        normalized = normalized.with_columns(pl.lit(None).cast(pl.Float64).alias("amount"))
    return normalized.sort(["symbol", "date"])


class AkShareProvider:
    """Optional AkShare source for unadjusted daily and narrow minute fallbacks."""

    name = "akshare"
    capabilities = ProviderCapabilities(daily=True, minute=True)

    def __init__(self) -> None:
        self.builtin = True
        self.datasets = dict.fromkeys(_DATASETS)
        self.config = type("ProviderConfig", (), {"datasets": self.datasets})()

    def get_instruments(self, asset_type: str = "stock") -> list[dict]:
        ak = _ak()
        if asset_type == "index":
            return [
                {"symbol": symbol, "name": name, "code": symbol.split(".")[0],
                 "exchange": symbol.split(".")[1], "asset_type": "index", "source": self.name}
                for symbol, name in _CORE_INDEXES
            ]
        if asset_type != "stock":
            return []
        raw = _to_polars(ak.stock_info_a_code_name())
        if raw.is_empty():
            return []
        code_col = "代码" if "代码" in raw.columns else "code" if "code" in raw.columns else None
        name_col = "名称" if "名称" in raw.columns else "name" if "name" in raw.columns else None
        if code_col is None:
            return []
        rows = []
        for row in raw.iter_rows(named=True):
            code = str(row.get(code_col) or "").zfill(6)
            if not code.isdigit():
                continue
            symbol = _normalize_symbol(code)
            rows.append({
                "symbol": symbol,
                "name": str(row.get(name_col) or symbol) if name_col else symbol,
                "code": code,
                "exchange": symbol.split(".")[1],
                "asset_type": "stock",
                "source": self.name,
            })
        return rows

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | date | None,
        end_time: datetime | date | None,
        asset_type: str = "stock",
        on_chunk_done=None,
    ) -> pl.DataFrame:
        if not symbols or asset_type not in {"stock", "index"}:
            return pl.DataFrame()
        start = start_time.date() if isinstance(start_time, datetime) else start_time
        end = end_time.date() if isinstance(end_time, datetime) else end_time
        end = end or cn_today()
        start = start or (end - timedelta(days=365))
        ak = _ak()
        frames: list[pl.DataFrame] = []
        total = len(symbols)
        for index, symbol in enumerate(symbols, 1):
            normalized = _normalize_symbol(symbol)
            try:
                if asset_type == "index":
                    # This endpoint is stable in the current AkShare runtime and
                    # returns the full history without a date-range parameter.
                    raw = ak.stock_zh_index_daily(symbol=_ak_index_code(normalized))
                else:
                    raw = ak.stock_zh_a_hist(
                        symbol=_ak_code(normalized),
                        period="daily",
                        start_date=start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                        adjust="",  # raw/unadjusted prices; never qfq/hfq here
                    )
                frame = _normalize_raw_daily(
                    raw, normalized, allow_missing_amount=asset_type == "index"
                )
                if not frame.is_empty():
                    frames.append(frame.filter((pl.col("date") >= start) & (pl.col("date") <= end)))
            except Exception as exc:  # noqa: BLE001
                logger.warning("AkShare daily failed: symbol=%s range=%s..%s error=%s", normalized, start, end, exc)
            if on_chunk_done is not None:
                on_chunk_done(index, total)
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done=None,
    ) -> pl.DataFrame:
        """Return one stock's 1-minute bars as a chart fallback.

        AkShare's endpoint is called sequentially and is intentionally not
        exposed as a full-market or full-history minute source.
        """
        if asset_type != "stock" or len(symbols) != 1 or freq not in {"1m", "1"}:
            return pl.DataFrame()
        symbol = _normalize_symbol(symbols[0])
        start = _as_beijing_wallclock(start_time)
        end = _as_beijing_wallclock(end_time)
        if end is None:
            end = datetime.combine(cn_today(), datetime.min.time()).replace(hour=15, minute=5)
        if start is None:
            start = end.replace(hour=9, minute=25, second=0, microsecond=0)

        ak = _ak()
        code = _ak_code(symbol)
        raw = None
        try:
            raw = ak.stock_zh_a_hist_min_em(
                symbol=code,
                period="1",
                start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
                end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
                adjust="",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AkShare minute failed: symbol=%s range=%s..%s error=%s",
                symbol, start, end, exc,
            )

        if raw is None or _to_polars(raw).is_empty():
            try:
                raw = ak.stock_zh_a_minute(
                    symbol=_ak_stock_prefixed(symbol),
                    period="1",
                    adjust="",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("AkShare legacy minute failed: symbol=%s error=%s", symbol, exc)
                raw = None

        if raw is None or _to_polars(raw).is_empty():
            logger.info("AkShare minute returned no rows: symbol=%s", symbol)
            return pl.DataFrame()
        out = _normalize_raw_minute(raw, symbol, start, end)
        if on_chunk_done is not None:
            on_chunk_done(1, 1)
        return out

    def close(self) -> None:
        """Plugin lifecycle hook; AkShare has no persistent client to close."""
