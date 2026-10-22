"""QATdx provider：pytdx 行情适配到面板契约。

日 K 不复权。除权因子只用除权除息（category==1），字段按每 10 股换成每股后，
用与 TdxQuant 相同的交易所参考价算单事件 ex_factor。成交量保持「手」。
财务只填股本快照；利润表、资产负债表、现金流量表和指标返回空帧。
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterator
from datetime import date, datetime, time, timedelta
from typing import Any

import polars as pl

from app.data_providers.normalizer import normalize_daily
from app.plugins.qatdx import client

logger = logging.getLogger(__name__)

_DATASETS = (
    "daily", "minute", "full_minute", "realtime", "depth5", "instruments",
    "adj_factor", "financial", "transaction", "block", "industry", "margin", "bond",
)
_ADJ_SCHEMA = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "ex_factor": pl.Float64,
    "dividend": pl.Float64,
    "bonus": pl.Float64,
    "allot": pl.Float64,
    "allot_price": pl.Float64,
    "prev_close": pl.Float64,
}
_SHARES_SCHEMA = {
    "symbol": pl.String,
    "period_end": pl.Date,
    "announce_date": pl.Date,
    "total_shares": pl.Float64,
    "float_shares": pl.Float64,
}
_TX_SCHEMA = {
    "symbol": pl.String,
    "datetime": pl.Datetime("us"),
    "price": pl.Float64,
    "volume": pl.Float64,
    "direction": pl.String,
}
_BLOCK_SCHEMA = {
    "symbol": pl.String,
    "block_name": pl.String,
    "block_type": pl.String,
}
_INDUSTRY_SCHEMA = {
    "symbol": pl.String,
    "industry_name": pl.String,
}
_MARGIN_SCHEMA = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "leveraged_balance": pl.Float64,
    "leveraged_buyout": pl.Float64,
    "margin_sell": pl.Float64,
    "margin_left": pl.Float64,
    "exchange": pl.String,
}
_BARS_PER_DAY = {"1m": 240, "5m": 48, "15m": 16, "30m": 8, "60m": 4}


def availability() -> tuple[bool, str]:
    """源码随插件放在 vendor/pytdx，不要求把 pytdx 装进虚拟环境。"""
    hq = client._vendor_root() / "pytdx" / "hq.py"
    if not hq.is_file():
        return False, "缺少插件内 pytdx 源码 (vendor/pytdx)"
    try:
        client._import_api()
    except Exception as exc:  # noqa: BLE001
        return False, f"pytdx 源码无法加载 ({exc})"
    return True, "ok (插件内 pytdx 源码, 无 API Key)"


def _price_limit(symbol: str) -> float:
    code = symbol.split(".")[0]
    if code.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def _ref_price(prev_close: float, dividend: float, bonus: float, allot: float, allot_price: float) -> float | None:
    denom = 1.0 + bonus + allot
    if denom <= 0 or prev_close <= 0:
        return None
    value = (prev_close - dividend + allot * allot_price) / denom
    if value <= 0:
        return None
    return math.floor(value * 100 + 0.5) / 100


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date()
        except ValueError:
            return None
    return None


def _bar_datetime(row: dict) -> datetime | None:
    raw = row.get("datetime")
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None)
    text = str(raw or "").strip()
    if text:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
    year, month, day = row.get("year"), row.get("month"), row.get("day")
    if year and month and day:
        return datetime(int(year), int(month), int(day), int(row.get("hour") or 0), int(row.get("minute") or 0))
    return None


def _asset_kind(code: str, asset_type: str) -> bool:
    if asset_type == "index":
        return code.startswith("399") or code.startswith("000")
    if asset_type == "etf":
        return code.startswith(("15", "16", "18", "50", "51", "52", "56", "58"))
    if asset_type == "bond":
        return code.startswith((
            "101", "104", "105", "106", "107", "108", "109", "110", "111", "112",
            "113", "114", "115", "116", "117", "118", "119", "120", "122", "123",
            "127", "128", "130", "131", "132",
        ))
    if asset_type == "stock":
        return code.startswith(("00", "30", "60", "68", "43", "83", "87", "88", "92"))
    return False


def _symbol_of_block_code(code: str) -> str | None:
    text = "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(6)[-6:]
    if len(text) != 6:
        return None
    if text.startswith(("43", "83", "87", "88", "92", "4", "8")):
        return f"{text}.BJ"
    if text.startswith(("5", "6", "9")):
        return f"{text}.SH"
    if text.startswith(("0", "1", "3")):
        return f"{text}.SZ"
    return None


class QatdxProvider:
    name = "qatdx"
    minute_history_days = 60

    def __init__(self) -> None:
        self.builtin = True
        self.datasets = dict.fromkeys(_DATASETS)
        self.config = type("ProviderConfig", (), {"datasets": self.datasets})()

    def close(self) -> None:
        return None

    def get_instruments(self, asset_type: str = "stock") -> list[dict]:
        rows: list[dict] = []
        markets = (0, 1, 2) if asset_type in {"stock", "bond"} else (0, 1)
        for market in markets:
            try:
                listed = client.security_list(market)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 列表失败 market=%s: %s", market, exc)
                continue
            for item in listed:
                code = str(item.get("code") or "").zfill(6)
                if not _asset_kind(code, asset_type):
                    continue
                symbol = client.to_symbol(market, code)
                if symbol is None:
                    continue
                rows.append({
                    "symbol": symbol,
                    "name": item.get("name") or code,
                    "code": code,
                    "exchange": symbol.split(".")[1],
                    "asset_type": asset_type,
                    "source": self.name,
                })
        return rows

    def iter_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> Iterator[pl.DataFrame]:
        total = len(symbols or [])
        if total == 0:
            if on_chunk_done:
                on_chunk_done(0, 0)
            return
        for index, symbol in enumerate(symbols, start=1):
            try:
                frame = self._daily_one(symbol, start_time, end_time, asset_type)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 日K %s 失败: %s", symbol, exc)
                frame = pl.DataFrame()
            yield frame
            if on_chunk_done:
                on_chunk_done(index, total)

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        frames = [
            frame for frame in self.iter_daily(
                symbols, start_time, end_time, asset_type, on_chunk_done,
            ) if not frame.is_empty()
        ]
        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def _daily_one(self, symbol: str, start_time: datetime | None, end_time: datetime | None, asset_type: str) -> pl.DataFrame:
        parsed = client.split_symbol(symbol, index=asset_type == "index")
        if parsed is None:
            return pl.DataFrame()
        market, code = parsed
        end = end_time.date() if start_time or end_time else date.today()
        if end_time:
            end = end_time.date()
        start = start_time.date() if start_time else end - timedelta(days=365)
        count = min(8000, max((end - start).days + 10, 5))
        raw = client.stock_bars(market, code, 9, count)
        rows = []
        for item in raw:
            stamp = _bar_datetime(item)
            if stamp is None:
                continue
            if stamp.date() < start or stamp.date() > end:
                continue
            if float(item.get("open") or 0) == 0:
                continue
            rows.append({
                "symbol": f"{code}.{symbol.split('.')[1]}",
                "date": stamp.date(),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": item.get("vol", item.get("volume")),
                "amount": item.get("amount"),
            })
        return normalize_daily(rows, default_symbol=symbol, source=self.name)

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        category = client.bar_category(freq)
        if category is None or category == 9:
            return pl.DataFrame()
        freq_key = "1m" if category == 8 else freq
        per_day = _BARS_PER_DAY.get(freq_key, 240)
        end = end_time or datetime.now()
        start = start_time or (end - timedelta(days=5))
        count = min(client._MAX_BARS, max(per_day, int((end - start).total_seconds() / 86400 + 1) * per_day))
        frames: list[pl.DataFrame] = []
        total = len(symbols or [])
        for index, symbol in enumerate(symbols or [], start=1):
            parsed = client.split_symbol(symbol, index=asset_type == "index")
            if parsed is not None:
                market, code = parsed
                try:
                    raw = client.stock_bars(market, code, category, count)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("qatdx 分钟 %s 失败: %s", symbol, exc)
                    raw = []
                rows = []
                for item in raw:
                    stamp = _bar_datetime(item)
                    if stamp is None or stamp < start or stamp > end:
                        continue
                    rows.append({
                        "symbol": symbol,
                        "datetime": stamp,
                        "open": item.get("open"),
                        "high": item.get("high"),
                        "low": item.get("low"),
                        "close": item.get("close"),
                        "volume": item.get("vol", item.get("volume")),
                        "amount": item.get("amount"),
                    })
                if rows:
                    frames.append(pl.DataFrame(rows).with_columns(
                        pl.col("datetime").cast(pl.Datetime("us")),
                        pl.col("open").cast(pl.Float64),
                        pl.col("high").cast(pl.Float64),
                        pl.col("low").cast(pl.Float64),
                        pl.col("close").cast(pl.Float64),
                        pl.col("volume").cast(pl.Float64),
                        pl.col("amount").cast(pl.Float64),
                    ))
            if on_chunk_done:
                on_chunk_done(index, total)
        if total == 0 and on_chunk_done:
            on_chunk_done(0, 0)
        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def get_intraday_batch(self, symbols: list[str], count: int = 240, asset_type: str = "stock") -> pl.DataFrame:
        today = date.today()
        start = datetime.combine(today, time(9, 0))
        end = datetime.combine(today, time(15, 10))
        return self.get_minute(symbols, start, end, asset_type=asset_type, freq="1m")

    def get_realtime(self, universes: list[str] | None = None, symbols: list[str] | None = None) -> list[dict]:
        del universes
        try:
            targets = list(symbols or [])
            if not targets:
                targets = [row["symbol"] for row in self.get_instruments("stock")]
            return self._quotes(targets, index=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 实时快照失败: %s", exc)
            return []

    def get_realtime_indices(self, symbols: list[str]) -> list[dict] | None:
        try:
            return self._quotes(list(symbols or []), index=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 指数快照失败: %s", exc)
            return None

    def get_depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        books: dict[str, dict] = {}
        parsed_pairs = []
        symbol_by_code = {}
        for symbol in symbols or []:
            parsed = client.split_symbol(symbol)
            if parsed is None:
                continue
            market, code = parsed
            parsed_pairs.append((market, code))
            symbol_by_code[code] = symbol
        try:
            rows = client.quotes(parsed_pairs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 五档失败: %s", exc)
            return {}
        now_ms = int(datetime.now().timestamp() * 1000)
        for item in rows:
            code = str(item.get("code") or "").zfill(6)
            symbol = symbol_by_code.get(code)
            if symbol is None:
                continue
            books[symbol] = {
                "bid_prices": [item.get(f"bid{i}") for i in range(1, 6)],
                "bid_volumes": [item.get(f"bid_vol{i}") for i in range(1, 6)],
                "ask_prices": [item.get(f"ask{i}") for i in range(1, 6)],
                "ask_volumes": [item.get(f"ask_vol{i}") for i in range(1, 6)],
                "timestamp": now_ms,
            }
        return books

    def _quotes(self, symbols: list[str], *, index: bool) -> list[dict]:
        pairs = []
        wanted = {}
        for symbol in symbols:
            parsed = client.split_symbol(symbol, index=index)
            if parsed is None:
                continue
            market, code = parsed
            pairs.append((market, code))
            wanted[code] = symbol
        records = []
        for item in client.quotes(pairs):
            code = str(item.get("code") or "").zfill(6)
            symbol = wanted.get(code)
            if symbol is None:
                continue
            price = _float(item.get("price"))
            prev = _float(item.get("last_close"))
            change = None if price is None or prev is None else price - prev
            change_pct = None if change is None or not prev else change / prev
            records.append({
                "symbol": symbol,
                "last_price": price,
                "prev_close": prev,
                "open": _float(item.get("open")),
                "high": _float(item.get("high")),
                "low": _float(item.get("low")),
                "volume": _float(item.get("vol")),
                "amount": _float(item.get("amount")),
                "change_pct": change_pct,
                "change_amount": change,
                "timestamp": int(datetime.now().timestamp() * 1000),
                "name": None,
                "amplitude": None,
                "turnover_rate": None,
                "session": None,
            })
        return records

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        if asset_type != "stock" or not symbols:
            if on_chunk_done:
                on_chunk_done(0, len(symbols or []))
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        start = start_time.date() if start_time else date(1990, 1, 1)
        end = end_time.date() if end_time else date.today()
        out: list[dict] = []
        for index, symbol in enumerate(symbols, start=1):
            try:
                out.extend(self._factor_rows(symbol, start, end))
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 除权因子 %s 失败: %s", symbol, exc)
            if on_chunk_done:
                on_chunk_done(index, len(symbols))
        if not out:
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        return pl.DataFrame(out, schema=_ADJ_SCHEMA).unique(
            subset=["symbol", "trade_date"], keep="last",
        ).sort(["symbol", "trade_date"])

    def _factor_rows(self, symbol: str, start: date, end: date) -> list[dict]:
        parsed = client.split_symbol(symbol)
        if parsed is None:
            return []
        market, code = parsed
        events = []
        for item in client.xdxr(market, code):
            if int(item.get("category") or 0) != 1:
                continue
            ex_date = date(int(item["year"]), int(item["month"]), int(item["day"]))
            if ex_date < start or ex_date > end:
                continue
            events.append((ex_date, item))
        if not events:
            return []
        first = min(item[0] for item in events)
        bars = client.stock_bars(market, code, 9, min(8000, max((end - first).days + 30, 30)))
        closes: dict[date, float] = {}
        for item in bars:
            stamp = _bar_datetime(item)
            close = _float(item.get("close"))
            if stamp is not None and close is not None and close > 0:
                closes[stamp.date()] = close
        days = sorted(closes)
        merged: dict[date, dict] = {}
        for ex_date, item in events:
            slot = merged.setdefault(ex_date, {"fenhong": 0.0, "songzhuangu": 0.0, "peigu": 0.0, "peigujia": 0.0})
            slot["fenhong"] += _float(item.get("fenhong")) or 0.0
            slot["songzhuangu"] += _float(item.get("songzhuangu")) or 0.0
            slot["peigu"] += _float(item.get("peigu")) or 0.0
            price = _float(item.get("peigujia"))
            if price:
                slot["peigujia"] = price
        rows = []
        for ex_date, slot in sorted(merged.items()):
            prev_days = [day for day in days if day < ex_date]
            if not prev_days:
                continue
            prev_close = closes[prev_days[-1]]
            dividend = slot["fenhong"] / 10.0
            bonus = slot["songzhuangu"] / 10.0
            allot = slot["peigu"] / 10.0
            allot_price = slot["peigujia"]
            ref = _ref_price(prev_close, dividend, bonus, allot, allot_price)
            if ref is None:
                continue
            factor = prev_close / ref
            ex_days = [day for day in days if day >= ex_date]
            if ex_days:
                ret = closes[ex_days[0]] / (prev_close / factor) - 1.0
                if abs(ret) > _price_limit(symbol) + 0.02:
                    logger.warning("qatdx 除权因子自检剔除 %s %s: %.1f%%", symbol, ex_date, ret * 100)
                    continue
            rows.append({
                "symbol": symbol,
                "trade_date": ex_date,
                "ex_factor": factor,
                "dividend": dividend,
                "bonus": bonus,
                "allot": allot,
                "allot_price": allot_price,
                "prev_close": prev_close,
            })
        return rows

    def get_financials(self, table: str, symbols: list[str], latest_only: bool = False) -> pl.DataFrame:
        del latest_only
        if table != "shares":
            return pl.DataFrame()
        rows = []
        for symbol in symbols or []:
            parsed = client.split_symbol(symbol)
            if parsed is None:
                continue
            try:
                info = client.finance(*parsed)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 财务快照 %s 失败: %s", symbol, exc)
                continue
            float_raw = info.get("liutongguben")
            total_raw = info.get("zongguben")
            already_shares = _shares_already_in_shares(float_raw, total_raw)
            float_shares = _shares(float_raw, already_shares=already_shares)
            total_shares = _shares(total_raw, already_shares=already_shares)
            if float_shares is None:
                continue
            as_of = _as_date(info.get("updated_date")) or date.today()
            rows.append({
                "symbol": symbol,
                "period_end": as_of,
                "announce_date": as_of,
                "total_shares": total_shares if total_shares is not None else float_shares,
                "float_shares": float_shares,
            })
        if not rows:
            return pl.DataFrame(schema=_SHARES_SCHEMA)
        return pl.DataFrame(rows, schema=_SHARES_SCHEMA)

    def get_transactions(self, symbols: list[str], trade_day: date) -> pl.DataFrame:
        rows = []
        for symbol in symbols or []:
            code = symbol.split(".")[0]
            # 000001.SZ 是股票；000001.SH / 399xxx 才是指数。
            index = code.startswith("399") or (symbol.endswith(".SH") and code.startswith("000"))
            parsed = client.split_symbol(symbol, index=index)
            if parsed is None:
                continue
            market, code = parsed
            try:
                raw = client.transactions(market, code, trade_day)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 分笔 %s 失败: %s", symbol, exc)
                continue
            for item in raw:
                stamp = _tick_time(trade_day, item.get("time"))
                if stamp is None:
                    continue
                rows.append({
                    "symbol": symbol,
                    "datetime": stamp,
                    "price": _float(item.get("price")),
                    "volume": _float(item.get("vol")),
                    "direction": _direction(item.get("buyorsell")),
                })
        if not rows:
            return pl.DataFrame(schema=_TX_SCHEMA)
        return pl.DataFrame(rows, schema=_TX_SCHEMA)

    def get_blocks(self) -> pl.DataFrame:
        try:
            raw = client.blocks()
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 板块失败: %s", exc)
            return pl.DataFrame(schema=_BLOCK_SCHEMA)
        rows = []
        for item in raw:
            symbol = _symbol_of_block_code(item.get("code"))
            name = item.get("blockname") or item.get("block_name")
            if symbol is None or not name:
                continue
            rows.append({
                "symbol": symbol,
                "block_name": str(name),
                "block_type": str(item.get("block_type") or ""),
            })
        if not rows:
            return pl.DataFrame(schema=_BLOCK_SCHEMA)
        return pl.DataFrame(rows, schema=_BLOCK_SCHEMA).unique(subset=["symbol", "block_name", "block_type"])

    def get_industries(self) -> pl.DataFrame:
        try:
            raw = client.industries()
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 行业失败: %s", exc)
            return pl.DataFrame(schema=_INDUSTRY_SCHEMA)
        rows = []
        for item in raw:
            parts = str(item.get("line") or "").split("|")
            if len(parts) < 3:
                continue
            symbol = _symbol_of_block_code(parts[1])
            name = parts[-1].strip()
            # tdxhy 最后一列有时只是行业代码。没有中文名就丢掉，避免把代码当成行业。
            if symbol is None or not name or not any("\u4e00" <= ch <= "\u9fff" for ch in name):
                continue
            if parts[1].startswith(("9", "2")):
                continue
            rows.append({"symbol": symbol, "industry_name": name})
        if not rows:
            return pl.DataFrame(schema=_INDUSTRY_SCHEMA)
        return pl.DataFrame(rows, schema=_INDUSTRY_SCHEMA).unique(subset=["symbol", "industry_name"])

    def get_margin(self, trade_day: date | None = None) -> pl.DataFrame:
        day = trade_day or date.today()
        try:
            raw = client.margin(day)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 融资融券失败: %s", exc)
            return pl.DataFrame(schema=_MARGIN_SCHEMA)
        if not raw:
            return pl.DataFrame(schema=_MARGIN_SCHEMA)
        frame = pl.DataFrame(raw)
        if "trade_date" in frame.columns and frame.schema["trade_date"] == pl.String:
            frame = frame.with_columns(pl.col("trade_date").str.to_date(strict=False))
        elif "trade_date" in frame.columns and frame.schema["trade_date"] != pl.Date:
            frame = frame.with_columns(pl.col("trade_date").cast(pl.Date, strict=False))
        return frame

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        sample = symbols or ["000001.SZ"]
        try:
            if dataset == "daily":
                frame = self.get_daily(sample[:1], datetime.now() - timedelta(days=10), datetime.now(), "stock")
            elif dataset == "minute":
                frame = self.get_minute(sample[:1], datetime.now() - timedelta(days=2), datetime.now(), freq="1m")
            elif dataset == "adj_factor":
                frame = self.get_adj_factors(sample[:1], datetime(2018, 1, 1), datetime.now(), "stock")
            elif dataset == "financial":
                frame = self.get_financials("shares", sample[:1])
            elif dataset == "transaction":
                frame = self.get_transactions(sample[:1], date.today())
            elif dataset == "block":
                frame = self.get_blocks()
            elif dataset == "industry":
                frame = self.get_industries()
            elif dataset == "margin":
                frame = self.get_margin(date.today())
            elif dataset == "bond":
                rows = self.get_instruments("bond")
                frame = pl.DataFrame(rows[:5]) if rows else pl.DataFrame()
            elif dataset == "realtime":
                records = self.get_realtime(symbols=sample[:1])
                frame = pl.DataFrame(records) if records else pl.DataFrame()
            else:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "columns": [], "error": "未支持的数据集"}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "dataset": dataset, "rows": 0, "columns": [], "error": str(exc)}
        preview = frame.head(3).to_dicts() if not frame.is_empty() else []
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": frame.height,
            "columns": frame.columns,
            "preview": preview,
        }


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# 1e8 万股 = 1 万亿股，A 股不存在。达到这个数量级就是已经换成股。
_SHARES_ALREADY = 100_000_000


def _shares_already_in_shares(*values: Any) -> bool:
    """同一快照里只要有一个字段已经是股，其余字段按同一单位处理。"""
    for value in values:
        number = _float(value)
        if number is not None and number >= _SHARES_ALREADY:
            return True
    return False


def _shares(value: Any, *, already_shares: bool = False) -> float | None:
    """股本统一成股。

    这份 pytdx 解析财务快照时已经把万股乘过 10000，平安银行总股本大约是
    19,405,918,750 股。未换算的旧值仍是万股，例如 20000 表示 2 亿股。
    小于 1 亿的数按万股乘 10000；同一快照里已有股单位时，小的流通股本也不再乘。
    """
    number = _float(value)
    if number is None or number <= 0:
        return None
    if already_shares or number >= _SHARES_ALREADY:
        return number
    return number * 10000.0


def _direction(value: Any) -> str:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return "unknown"
    return {0: "buy", 1: "sell", 2: "auction"}.get(code, "unknown")


def _tick_time(trade_day: date, value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            clock = datetime.strptime(text, fmt).time()
            return datetime.combine(trade_day, clock)
        except ValueError:
            continue
    return None
