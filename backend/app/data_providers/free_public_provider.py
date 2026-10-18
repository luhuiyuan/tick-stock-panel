"""独立免费公开行情 Provider（新浪/东方财富/腾讯）。

这是按能力路由的可选源，不替换 TickFlow 全局客户端，不提供复权因子或财务数据。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import polars as pl

from app.data_providers.base import AssetType, ProviderCapabilities
from app.data_providers.free_public_adapter import FreeSourceClient
from app.data_providers.normalizer import DAILY_COLS, normalize_daily, normalize_instruments


class FreePublicProvider:
    name = "free_public"
    display = "免费公开源（新浪/东方财富/腾讯）"
    capabilities = ProviderCapabilities(
        instruments=True, daily=True, minute=True, realtime=True, depth5=True,
    )

    def __init__(self) -> None:
        self._client = FreeSourceClient()

    def get_instruments(self, asset_type: AssetType = "stock") -> pl.DataFrame:
        if asset_type not in {"stock", "index", "etf"}:
            return pl.DataFrame()
        rows: list[dict] = []
        for exchange in ("SH", "SZ", "BJ"):
            rows.extend(self._client.exchanges.get_instruments(exchange, instrument_type=asset_type))
        return normalize_instruments(rows, asset_type=asset_type, source=self.name)

    def get_daily(
        self, symbols: list[str], start_time: datetime | None, end_time: datetime | None,
        asset_type: AssetType = "stock", on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        if asset_type not in {"stock", "index", "etf"} or not symbols:
            return pl.DataFrame()
        count = 10000 if start_time and end_time else 250
        raw = self._client.klines.batch(symbols, period="1d", count=count, adjust="none", as_dataframe=False)
        df = normalize_daily(raw, source=self.name)
        if df.is_empty():
            return df
        if start_time is not None:
            df = df.filter(pl.col("date") >= start_time.date())
        if end_time is not None:
            df = df.filter(pl.col("date") <= end_time.date())
        if on_chunk_done:
            on_chunk_done(1, 1)
        return df.select([c for c in DAILY_COLS if c in df.columns]).sort(["symbol", "date"])

    def get_adj_factors(self, symbols, start_time, end_time, asset_type= "stock") -> pl.DataFrame:
        # 该源只返回原始/指定复权模式 K 线，不伪造官方除权因子。
        return pl.DataFrame()

    def get_minute(
        self, symbols: list[str], start_time: datetime | None, end_time: datetime | None,
        asset_type: AssetType = "stock", freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        if asset_type not in {"stock", "index", "etf"} or not symbols:
            return pl.DataFrame()
        raw = self._client.klines.batch(symbols, period=freq, count=10000, adjust="none", as_dataframe=False)
        rows = []
        for symbol, values in (raw or {}).items():
            for item in values or []:
                row = dict(item)
                row["datetime"] = row.pop("date", None)
                rows.append(row)
        if not rows:
            return pl.DataFrame()
        df = pl.DataFrame(rows)
        if "datetime" not in df.columns:
            return pl.DataFrame()
        df = df.with_columns(
            pl.col("datetime").cast(pl.Utf8).str.strptime(pl.Datetime("us"), strict=False).alias("datetime")
        )
        for col in ("open", "high", "low", "close", "volume", "amount"):
            if col in df.columns:
                df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
        if start_time is not None:
            df = df.filter(pl.col("datetime") >= start_time)
        if end_time is not None:
            df = df.filter(pl.col("datetime") <= end_time)
        if on_chunk_done:
            on_chunk_done(1, 1)
        return df.select([c for c in ("symbol", "datetime", "open", "high", "low", "close", "volume", "amount") if c in df.columns]).sort(["symbol", "datetime"])

    def get_realtime(self, universes=None, symbols=None) -> pl.DataFrame:
        if universes:
            raw = self._client.quotes.get_by_universes(universes, as_dataframe=False)
        elif symbols:
            raw = self._client.quotes.get(symbols, as_dataframe=False)
        else:
            return pl.DataFrame()
        return pl.DataFrame(raw or [])

    def get_depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        return self._client.depth.batch(symbols)

    def get_auction_batch(self, symbols: list[str], trade_date: date) -> pl.DataFrame:
        return pl.DataFrame()
