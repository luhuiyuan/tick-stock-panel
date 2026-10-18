"""Independent OpenTDX market-data provider.

The vendored client is used only through this provider. It does not replace the
TickFlow client or enable OpenTDX as the default backend.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import polars as pl
from opentdx.const import ADJUST, CATEGORY, MARKET, PERIOD
from opentdx.tdxClient import TdxClient
from opentdx.utils.help import query_market

from app.data_providers.base import AssetType, ProviderCapabilities
from app.data_providers.normalizer import normalize_daily, normalize_instruments

logger = logging.getLogger(__name__)

_MARKETS = (MARKET.SH, MARKET.SZ, MARKET.BJ)


def _market_suffix(market: MARKET) -> str:
    if market == MARKET.SZ:
        return "SZ"
    if market == MARKET.BJ:
        return "BJ"
    return "SH"


def _split_symbol(symbol: str) -> tuple[MARKET, str, str]:
    raw = str(symbol or "").strip().upper()
    code, _, suffix = raw.partition(".")
    if suffix == "SH":
        market = MARKET.SH
    elif suffix == "SZ":
        market = MARKET.SZ
    elif suffix == "BJ":
        market = MARKET.BJ
    else:
        market = query_market(code) or MARKET.SH
    return market, code, f"{code}.{_market_suffix(market)}"


def _is_asset_code(code: str, market: MARKET, asset_type: AssetType) -> bool:
    if asset_type == "index":
        return (market == MARKET.SZ and code.startswith("399")) or (
            market == MARKET.SH and code.startswith("000")
        )
    if asset_type == "etf":
        return code.startswith(("15", "16", "18", "50", "51", "52", "56", "58"))
    if market == MARKET.SZ:
        return code.startswith(("00", "30"))
    if market == MARKET.SH:
        return code.startswith(("60", "68"))
    if market == MARKET.BJ:
        return code.startswith(("43", "83", "87", "88", "92"))
    return False


def _period(freq: str = "1d") -> PERIOD:
    value = str(freq or "1d").lower()
    return {
        "1m": PERIOD.MIN_1,
        "min_1": PERIOD.MIN_1,
        "5m": PERIOD.MIN_5,
        "min_5": PERIOD.MIN_5,
        "15m": PERIOD.MIN_15,
        "min_15": PERIOD.MIN_15,
        "30m": PERIOD.MIN_30,
        "min_30": PERIOD.MIN_30,
        "60m": PERIOD.MIN_60,
        "min_60": PERIOD.MIN_60,
    }.get(value, PERIOD.DAILY)


def _count_for_range(start_time: datetime | None, end_time: datetime | None, fallback: int) -> int:
    if start_time and end_time:
        return min(max((end_time.date() - start_time.date()).days * 2 + 30, fallback), 800)
    return min(max(fallback, 1), 800)


def _normalize_kline_rows(rows: list[dict], symbol: str) -> pl.DataFrame:
    normalized = []
    for row in rows or []:
        normalized.append({
            "symbol": symbol,
            "date": row.get("datetime") or row.get("date"),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "volume": row.get("volume", row.get("vol")),
            "amount": row.get("amount"),
        })
    return normalize_daily(normalized, default_symbol=symbol, source="opentdx")


def _levels(handicap: Any, side: str) -> list[list[float]]:
    if not isinstance(handicap, dict):
        return []
    values = handicap.get(side) or []
    result = []
    for item in values:
        if isinstance(item, dict):
            price, volume = item.get("price"), item.get("vol", item.get("volume"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price, volume = item[0], item[1]
        else:
            continue
        result.append([price, volume])
    return result


class OpenTDXProvider:
    name = "opentdx"
    display = "OpenTDX（通达信协议）"
    capabilities = ProviderCapabilities(
        instruments=True,
        daily=True,
        minute=True,
        realtime=True,
        depth5=True,
        adj_factor=False,
        financial=False,
    )

    def __init__(self, client: TdxClient | None = None) -> None:
        self._client = client or TdxClient()

    def _ensure_connected(self) -> None:
        try:
            client = self._client.q_client()
            if not client.connected:
                client.connect().login()
        except Exception as exc:  # noqa: BLE001
            logger.debug("OpenTDX connection not ready: %s", exc)

    def get_instruments(self, asset_type: AssetType = "stock") -> pl.DataFrame:
        rows: list[dict] = []
        self._ensure_connected()
        for market in _MARKETS:
            try:
                items = self._client.stock_list(market, count=0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OpenTDX instruments %s failed: %s", market, exc)
                continue
            exchange = _market_suffix(market)
            for item in items or []:
                code = str(item.get("code") or "").strip()
                if not code or not _is_asset_code(code, market, asset_type):
                    continue
                rows.append({
                    "symbol": f"{code}.{exchange}",
                    "name": item.get("name") or code,
                    "code": code,
                    "exchange": exchange,
                })
        return normalize_instruments(rows, asset_type=asset_type, source=self.name)

    def get_daily(self, symbols, start_time, end_time, asset_type= "stock", on_chunk_done=None) -> pl.DataFrame:
        if not symbols or asset_type not in {"stock", "index", "etf"}:
            return pl.DataFrame()
        self._ensure_connected()
        frames: list[pl.DataFrame] = []
        count = _count_for_range(start_time, end_time, 250)
        for symbol in symbols:
            market, code, normalized_symbol = _split_symbol(symbol)
            try:
                raw = self._client.stock_kline(market, code, PERIOD.DAILY, count=count, adjust=ADJUST.NONE)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OpenTDX daily %s failed: %s", symbol, exc)
                continue
            df = _normalize_kline_rows(raw, normalized_symbol)
            if df.is_empty():
                continue
            if start_time:
                df = df.filter(pl.col("date") >= start_time.date())
            if end_time:
                df = df.filter(pl.col("date") <= end_time.date())
            if not df.is_empty():
                frames.append(df)
        if on_chunk_done:
            on_chunk_done(1, 1)
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_adj_factors(self, symbols, start_time, end_time, asset_type) -> pl.DataFrame:  # noqa: ARG002
        return pl.DataFrame()

    def get_minute(self, symbols, start_time, end_time, asset_type="stock", freq="1m", on_chunk_done=None) -> pl.DataFrame:
        if not symbols or asset_type not in {"stock", "index", "etf"}:
            return pl.DataFrame()
        self._ensure_connected()
        frames: list[pl.DataFrame] = []
        period = _period(freq)
        count = _count_for_range(start_time, end_time, 240)
        for symbol in symbols:
            market, code, normalized_symbol = _split_symbol(symbol)
            try:
                raw = self._client.stock_kline(market, code, period, count=count, adjust=ADJUST.NONE)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OpenTDX minute %s failed: %s", symbol, exc)
                continue
            rows = [{
                "symbol": normalized_symbol,
                "datetime": row.get("datetime") or row.get("date"),
                "open": row.get("open"), "high": row.get("high"),
                "low": row.get("low"), "close": row.get("close"),
                "volume": row.get("volume", row.get("vol")),
                "amount": row.get("amount"), "freq": freq,
            } for row in raw or []]
            df = pl.DataFrame(rows) if rows else pl.DataFrame()
            if df.is_empty():
                continue
            df = df.with_columns([
                pl.col("datetime").cast(pl.Datetime("us"), strict=False),
                *[pl.col(col).cast(pl.Float64, strict=False) for col in ("open", "high", "low", "close", "volume", "amount")],
            ])
            if start_time:
                df = df.filter(pl.col("datetime") >= start_time)
            if end_time:
                df = df.filter(pl.col("datetime") <= end_time)
            if not df.is_empty():
                frames.append(df)
        if on_chunk_done:
            on_chunk_done(1, 1)
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_realtime(self, universes=None, symbols=None) -> pl.DataFrame:
        if universes and symbols:
            raise ValueError("OpenTDX realtime accepts either universes or symbols, not both")
        self._ensure_connected()
        try:
            if symbols:
                rows = self._client.stock_quotes([_split_symbol(symbol)[:2] for symbol in symbols])
            elif universes and "CN_Equity_A" in universes:
                rows = self._client.stock_quotes_list(category=CATEGORY.A, count=0)
            else:
                return pl.DataFrame()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenTDX realtime failed: %s", exc)
            return pl.DataFrame()
        records = []
        for row in rows or []:
            code = str(row.get("code") or "")
            try:
                market = MARKET(row.get("market"))
            except Exception:  # noqa: BLE001
                market = query_market(code) or MARKET.SH
            last_price = row.get("close") or row.get("price")
            previous = row.get("pre_close")
            change_amount = float(last_price) - float(previous) if last_price is not None and previous not in (None, 0) else None
            records.append({
                "symbol": f"{code}.{_market_suffix(market)}", "open": row.get("open"),
                "high": row.get("high"), "low": row.get("low"), "last_price": last_price,
                "close": last_price, "prev_close": previous, "volume": row.get("volume", row.get("vol")),
                "amount": row.get("amount"), "change_amount": change_amount,
                "change_pct": change_amount / float(previous) if change_amount is not None else None,
                "turnover_rate": row.get("turnover"),
            })
        return pl.DataFrame(records) if records else pl.DataFrame()

    def get_depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        if not symbols:
            return {}
        self._ensure_connected()
        try:
            rows = self._client.stock_quotes_detail([_split_symbol(symbol)[:2] for symbol in symbols])
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenTDX depth failed: %s", exc)
            return {}
        result = {}
        for row in rows or []:
            code = str(row.get("code") or "")
            try:
                market = MARKET(row.get("market"))
            except Exception:  # noqa: BLE001
                market = query_market(code) or MARKET.SH
            symbol = f"{code}.{_market_suffix(market)}"
            bids = _levels(row.get("handicap"), "bid")
            asks = _levels(row.get("handicap"), "ask")
            result[symbol] = {
                "bids": bids, "asks": asks,
                "bid_volumes": [item[1] for item in bids],
                "ask_volumes": [item[1] for item in asks],
            }
        return result
