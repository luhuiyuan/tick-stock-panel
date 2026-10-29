"""连涨研究：中间可以下跌的上涨段，累计涨幅大于 30%。

一段从低点走到之后的最高收盘。中间下跌也留在这段里。
从最高收盘回落超过 5% 时，这段记到最高收盘那天，下一段从回落后的低点再算。
涨幅是最高收盘 / 起点收盘 - 1，大于 30% 才留下。正好 30% 不收。正好回落 5% 也不切。
数据结束时，还没回落超过 5% 的上涨也留下，结束日仍是最高收盘那天。
价格用 enriched 的复权 close，和 K 线一致。
复权涨幅和原始涨幅相差超过 80 个百分点的日子把前后切开，躲开复权因子接错。
"""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from app.services.limit_up_research import (
    BARS_AFTER,
    BARS_BEFORE,
    CALENDAR_AFTER,
    CALENDAR_BEFORE,
    full_window_cutoff,
    shift_years,
    slice_streak_bars,
    slice_year_bars,
)

logger = logging.getLogger(__name__)

MIN_GAIN = 0.30
REVERSAL = 0.05
ADJUST_GAP = 0.8

_RUN_SCHEMA = {
    "id": pl.Utf8,
    "symbol": pl.Utf8,
    "name": pl.Utf8,
    "start_date": pl.Date,
    "end_date": pl.Date,
    "days": pl.UInt32,
    "gain": pl.Float64,
    "base_close": pl.Float64,
    "end_close": pl.Float64,
}

_SCAN_SCHEMA = {
    "symbol": pl.Utf8,
    "start_date": pl.Date,
    "end_date": pl.Date,
    "days": pl.UInt32,
    "gain": pl.Float64,
    "base_close": pl.Float64,
    "end_close": pl.Float64,
}


def empty_rise_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=_SCAN_SCHEMA)


def _is_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _adjustment_break(prev_close: float, close: float, prev_raw: float | None, raw: float | None) -> bool:
    if prev_close <= 0 or close <= 0:
        return False
    if prev_raw is None or raw is None or prev_raw <= 0 or raw <= 0:
        return False
    return abs(close / prev_close - raw / prev_raw) > ADJUST_GAP



def _pulled_back(peak: float, close: float, reversal: float) -> bool:
    """回落超过 reversal 才算切开。正好等于该比例不切。"""
    if peak <= 0:
        return False
    return (peak - close) / peak > reversal + 1e-9


def _wave_record(dates: list[date], closes: list[float], trough: int, peak: int) -> dict:
    base = float(closes[trough])
    end_close = float(closes[peak])
    return {
        "start_date": dates[trough],
        "end_date": dates[peak],
        "days": peak - trough + 1,
        "gain": end_close / base - 1.0,
        "base_close": base,
        "end_close": end_close,
    }


def _zigzag(dates: list[date], closes: list[float], min_gain: float, reversal: float) -> list[dict]:
    """从低点走到高点。回落超过 reversal 才确认高点并切开。"""
    count = len(closes)
    if count == 0:
        return []
    trough = 0
    peak = 0
    direction = "up"
    up_factor = 1.0 + reversal
    gain_factor = 1.0 + min_gain
    found: list[dict] = []
    for index in range(1, count):
        close = closes[index]
        if direction == "up":
            if close > closes[peak]:
                peak = index
                continue
            risen = closes[trough] > 0 and closes[peak] > closes[trough] * gain_factor
            if (not risen) and close < closes[trough]:
                trough = index
                peak = index
                continue
            if _pulled_back(closes[peak], close, reversal):
                if risen:
                    found.append(_wave_record(dates, closes, trough, peak))
                direction = "down"
                trough = index
                peak = index
        elif close < closes[trough]:
            trough = index
            peak = index
        elif closes[trough] > 0 and close > closes[trough] * up_factor:
            direction = "up"
            peak = index
    if (
        direction == "up"
        and closes[trough] > 0
        and closes[peak] > closes[trough] * gain_factor
        and peak > trough
    ):
        found.append(_wave_record(dates, closes, trough, peak))
    return found


def find_rise_waves(
    rows: list[tuple],
    *,
    min_gain: float = MIN_GAIN,
    reversal: float = REVERSAL,
) -> list[dict]:
    """rows 按标的、日期排好。每行是 (symbol, date, close) 或再加 raw_close。"""
    grouped: dict[str, list[tuple[date, float, float | None]]] = {}
    order: list[str] = []
    for row in rows:
        symbol = str(row[0])
        if symbol not in grouped:
            grouped[symbol] = []
            order.append(symbol)
        raw = float(row[3]) if len(row) > 3 and row[3] is not None else None
        grouped[symbol].append((_is_date(row[1]), float(row[2]), raw))
    found: list[dict] = []
    for symbol in order:
        bars = grouped[symbol]
        chunks: list[list[tuple[date, float, float | None]]] = []
        current: list[tuple[date, float, float | None]] = []
        for bar in bars:
            if current and _adjustment_break(current[-1][1], bar[1], current[-1][2], bar[2]):
                chunks.append(current)
                current = [bar]
            else:
                current.append(bar)
        if current:
            chunks.append(current)
        for chunk in chunks:
            dates = [bar[0] for bar in chunk]
            closes = [bar[1] for bar in chunk]
            for wave in _zigzag(dates, closes, min_gain, reversal):
                wave["symbol"] = symbol
                found.append(wave)
    return found


def scan_consecutive_rises(data_dir: Path, *, min_gain: float = MIN_GAIN) -> pl.DataFrame:
    """在独立 DuckDB 连接上扫 enriched 日线。不占用面板主连接。"""
    import duckdb

    root = Path(data_dir) / "kline_daily_enriched"
    if not root.is_dir():
        return empty_rise_frame()
    glob = str(root / "**" / "*.parquet").replace("'", "''")
    relation = f"read_parquet('{glob}', union_by_name=true)"
    sql = f"""
        SELECT symbol, date, close, raw_close FROM (
            SELECT symbol,
                   CAST(date AS DATE) AS date,
                   max(close) AS close,
                   max(raw_close) AS raw_close
            FROM {relation}
            WHERE symbol IS NOT NULL AND close IS NOT NULL AND close > 0
            GROUP BY 1, 2
        )
        ORDER BY symbol, date
    """
    con = duckdb.connect()
    try:
        con.execute("PRAGMA threads=1")
        con.execute("PRAGMA memory_limit='768MB'")
        cursor = con.execute(sql)
        waves: list[dict] = []
        pending_symbol = None
        pending: list[tuple] = []

        def flush() -> None:
            if not pending:
                return
            waves.extend(find_rise_waves(pending, min_gain=min_gain))

        while True:
            batch = cursor.fetchmany(20000)
            if not batch:
                break
            for symbol, day, close, raw in batch:
                if pending_symbol is not None and symbol != pending_symbol:
                    flush()
                    pending = []
                pending_symbol = symbol
                pending.append((symbol, day, close, raw))
        flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("连涨扫描失败: %s", exc)
        raise
    finally:
        con.close()
    if not waves:
        return empty_rise_frame()
    return pl.DataFrame({
        "symbol": [row["symbol"] for row in waves],
        "start_date": [row["start_date"] for row in waves],
        "end_date": [row["end_date"] for row in waves],
        "days": [int(row["days"]) for row in waves],
        "gain": [float(row["gain"]) for row in waves],
        "base_close": [float(row["base_close"]) for row in waves],
        "end_close": [float(row["end_close"]) for row in waves],
    }, schema=_SCAN_SCHEMA)


_CACHE: dict[int, tuple[tuple[str, int], pl.DataFrame]] = {}
_CACHE_LOCK = threading.Lock()


def clear_rise_research_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


class RiseResearchService:
    def __init__(self, repo) -> None:
        self.repo = repo

    def list_runs(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        min_gain: float = MIN_GAIN,
        q: str = "",
        sort: str = "end_date",
        order: str = "desc",
        offset: int = 0,
        limit: int = 24,
        full_window: bool = False,
    ) -> dict:
        table = self._runs_table()
        filtered = table
        cutoff = self._full_window_cutoff() if full_window else None
        if cutoff is not None:
            filtered = filtered.filter(pl.col("end_date") <= cutoff)
        if start is not None:
            filtered = filtered.filter(pl.col("end_date") >= start)
        if end is not None:
            filtered = filtered.filter(pl.col("start_date") <= end)
        if min_gain > MIN_GAIN:
            filtered = filtered.filter(pl.col("end_close") > pl.col("base_close") * (1.0 + min_gain))
        keyword = q.strip()
        if keyword:
            needle = keyword.upper()
            filtered = filtered.filter(
                pl.col("symbol").str.to_uppercase().str.contains(needle, literal=True)
                | pl.col("name").str.to_uppercase().str.contains(needle, literal=True)
            )
        descending = order != "asc"
        sort_key = sort if sort in {"gain", "days", "end_date"} else "end_date"
        filtered = filtered.sort(
            [sort_key, "end_date", "symbol"],
            descending=[descending, True, False],
        )
        total = filtered.height
        page = filtered.slice(offset, limit)
        bars_by_id = self._page_bars(page)
        rows = []
        for record in page.iter_rows(named=True):
            rows.append({
                "id": record["id"],
                "symbol": record["symbol"],
                "name": record["name"] or "",
                "start_date": record["start_date"].isoformat(),
                "end_date": record["end_date"].isoformat(),
                "days": int(record["days"]),
                "gain": round(float(record["gain"]), 6),
                "base_close": round(float(record["base_close"]), 4),
                "end_close": round(float(record["end_close"]), 4),
                "bars": bars_by_id.get(record["id"], []),
            })
        as_of = ""
        if not table.is_empty():
            as_of = table.get_column("end_date").max().isoformat()
        return {
            "as_of": as_of,
            "total": total,
            "offset": offset,
            "limit": limit,
            "min_gain": MIN_GAIN,
            "bars_before": BARS_BEFORE,
            "bars_after": BARS_AFTER,
            "full_window": full_window,
            "window_cutoff": cutoff.isoformat() if cutoff is not None else "",
            "rows": rows,
        }

    def zoom_bars(self, symbol: str, start: date, end: date) -> dict:
        """放大图：起涨前一年到结束后一年，按交易日实有多少算多少。"""
        lo = shift_years(start, -1)
        hi = shift_years(end, 1)
        frame = self.repo.load_enriched_ohlcv([symbol], lo, hi)
        raw: list[dict] = []
        if frame is not None and not frame.is_empty():
            for row in frame.iter_rows(named=True):
                if row["symbol"] != symbol or not _finite_price(row["open"], row["high"], row["low"], row["close"]):
                    continue
                raw.append({
                    "date": row["date"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"] or 0.0,
                })
        window = slice_year_bars(raw, start, end)
        dates = [bar["date"] for bar in window]
        return {
            "symbol": symbol,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "from_date": lo.isoformat(),
            "to_date": hi.isoformat(),
            "bars_before": sum(day < start for day in dates),
            "bars_after": sum(day > end for day in dates),
            "bars": _serialize_bars(window),
        }

    def _runs_table(self) -> pl.DataFrame:
        revision = self._revision()
        key = id(self.repo)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached[0] == revision:
                return cached[1]
        table = self._build_runs()
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached[0] == revision:
                return cached[1]
            _CACHE[key] = (revision, table)
            return table

    def _revision(self) -> tuple[str, int]:
        provider = getattr(self.repo, "limit_up_research_revision", None)
        if callable(provider):
            return provider()
        return "", 0

    def _build_runs(self) -> pl.DataFrame:
        scanned = self.repo.load_consecutive_rises(MIN_GAIN)
        names = self._names()
        if scanned is None or scanned.is_empty():
            return pl.DataFrame(schema=_RUN_SCHEMA)
        named = scanned.join(names, on="symbol", how="left")
        return named.with_columns(
            pl.col("name").fill_null(""),
            pl.col("days").cast(pl.UInt32),
            pl.col("gain").cast(pl.Float64),
            pl.col("base_close").cast(pl.Float64),
            pl.col("end_close").cast(pl.Float64),
            (
                pl.col("symbol")
                + pl.lit(":")
                + pl.col("start_date").dt.strftime("%Y-%m-%d")
            ).alias("id"),
        ).select(list(_RUN_SCHEMA))

    def _full_window_cutoff(self) -> date | None:
        return full_window_cutoff(self._market_dates())

    def _market_dates(self) -> list[date]:
        provider = getattr(self.repo, "research_market_dates", None)
        if not callable(provider):
            provider = getattr(self.repo, "limit_up_research_market_dates", None)
        if callable(provider):
            return list(provider())
        store = getattr(self.repo, "store", None)
        data_dir = getattr(store, "data_dir", None)
        if data_dir is None:
            return []
        root = Path(data_dir) / "kline_daily_enriched"
        if not root.is_dir():
            return []
        found: list[date] = []
        for path in root.iterdir():
            name = path.name
            if not path.is_dir() or not name.startswith("date="):
                continue
            try:
                found.append(date.fromisoformat(name[5:]))
            except ValueError:
                continue
        return found

    def _names(self) -> pl.DataFrame:
        empty = pl.DataFrame(schema={"symbol": pl.Utf8, "name": pl.Utf8})
        try:
            instruments = self.repo.get_instruments()
        except Exception:  # noqa: BLE001
            return empty
        if instruments is None or instruments.is_empty() or "symbol" not in instruments.columns:
            return empty
        if "name" not in instruments.columns:
            return instruments.select(pl.col("symbol").cast(pl.Utf8)).with_columns(pl.lit("").alias("name"))
        return (
            instruments.select(pl.col("symbol").cast(pl.Utf8), pl.col("name").cast(pl.Utf8))
            .unique(subset=["symbol"], keep="first")
        )

    def _page_bars(self, page: pl.DataFrame) -> dict[str, list[dict]]:
        if page.is_empty():
            return {}
        symbols = page.get_column("symbol").unique().to_list()
        start = page.get_column("start_date").min() - timedelta(days=CALENDAR_BEFORE)
        end = page.get_column("end_date").max() + timedelta(days=CALENDAR_AFTER)
        ohlcv = self.repo.load_enriched_ohlcv(symbols, start, end)
        by_symbol: dict[str, list[dict]] = {}
        if ohlcv is not None and not ohlcv.is_empty():
            for row in ohlcv.iter_rows(named=True):
                if not _finite_price(row["open"], row["high"], row["low"], row["close"]):
                    continue
                by_symbol.setdefault(row["symbol"], []).append({
                    "date": row["date"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"] or 0.0,
                })
        result: dict[str, list[dict]] = {}
        for record in page.iter_rows(named=True):
            window = slice_streak_bars(
                by_symbol.get(record["symbol"], []),
                record["start_date"],
                record["end_date"],
            )
            result[record["id"]] = _serialize_bars(window)
        return result


def _serialize_bars(window: list[dict]) -> list[dict]:
    return [
        {
            "date": bar["date"].isoformat(),
            "open": round(float(bar["open"]), 4),
            "high": round(float(bar["high"]), 4),
            "low": round(float(bar["low"]), 4),
            "close": round(float(bar["close"]), 4),
            "volume": float(bar["volume"] or 0.0),
        }
        for bar in window
    ]


def _finite_price(*values: float | None) -> bool:
    for value in values:
        if value is None or value <= 0:
            return False
    return True
